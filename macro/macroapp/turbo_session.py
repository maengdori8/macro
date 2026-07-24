"""16GB PC에서 갱신 작업 전 RAM·VRAM 압박을 줄이는 안전한 세션 도구.

게임, 안티치트, 시스템 프로세스에는 접근하지 않습니다. 사용자가 명시적으로
선택한 데스크톱 앱만 정상 종료하고, 별도 재확인을 받은 경우에만 종료되지 않은
동일 PID를 강제 종료할 수 있습니다. 워킹셋 트림이나 대기목록 삭제는 사용하지
않습니다.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


TARGET_WGC_SIZE = (1928, 1048)
TARGET_AVAILABLE_RAM_GB = 6.0
MIN_AVAILABLE_RAM_GB = 5.0
TARGET_FREE_VRAM_GB = 2.0

_PROCESS_TERMINATE = 0x0001
_PROCESS_VM_READ = 0x0010
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WM_CLOSE = 0x0010
_SW_SHOWMAXIMIZED = 3
_SW_SHOWNOACTIVATE = 4
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020


@dataclass(frozen=True)
class AppSpec:
    key: str
    label: str
    executable_names: tuple[str, ...]
    path_fragments: tuple[str, ...]


APP_SPECS: tuple[AppSpec, ...] = (
    AppSpec(
        "chrome",
        "Google Chrome",
        ("chrome.exe",),
        ("\\google\\chrome\\application\\",),
    ),
    AppSpec(
        "discord",
        "Discord",
        ("discord.exe",),
        ("\\appdata\\local\\discord\\",),
    ),
    AppSpec(
        "openai",
        "ChatGPT / Codex",
        ("chatgpt.exe", "codex.exe"),
        ("\\windowsapps\\openai.",),
    ),
    AppSpec(
        "claude",
        "Claude Desktop",
        ("claude.exe",),
        ("\\windowsapps\\claude_",),
    ),
    AppSpec(
        "github",
        "GitHub Desktop",
        ("githubdesktop.exe",),
        ("\\appdata\\local\\githubdesktop\\",),
    ),
    AppSpec(
        "fdm",
        "Free Download Manager",
        ("fdm.exe",),
        ("\\softdeluxe\\free download manager\\",),
    ),
    AppSpec(
        "riot",
        "Riot Client / Tray",
        (
            "riotclientux.exe",
            "riotclientuxrender.exe",
            "riot client.exe",
            "vgtray.exe",
        ),
        ("\\riot games\\riot client\\", "\\riot vanguard\\vgtray.exe"),
    ),
)

_SPEC_BY_KEY = {spec.key: spec for spec in APP_SPECS}
_OVERLAY_NAMES = {"nvidia overlay.exe", "nvsphelper64.exe"}

# 이 목록은 방어를 한 번 더 겹치기 위한 것입니다. APP_SPECS에 없는 프로세스는
# 원래 종료 후보가 될 수 없으며, 아래 이름은 어떤 경로에서도 종료하지 않습니다.
_PROTECTED_NAMES = {
    "fczf.exe",
    "blackcipher64.aes",
    "blackcipher.aes",
    "vgc.exe",
    "vgk.exe",
    "system",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "explorer.exe",
    "launcher.exe",
    "macro.exe",
    "renewal_macro.exe",
}


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    created_at_100ns: int
    executable_name: str
    executable_path: str
    working_set_bytes: int
    private_bytes: int


@dataclass(frozen=True)
class TurboCandidate:
    key: str
    label: str
    processes: tuple[ProcessIdentity, ...]
    working_set_bytes: int
    private_bytes: int

    @property
    def process_count(self) -> int:
        return len(self.processes)

    @property
    def working_set_mb(self) -> float:
        return self.working_set_bytes / float(1024**2)


@dataclass(frozen=True)
class CloseResult:
    requested: tuple[ProcessIdentity, ...]
    closed: tuple[ProcessIdentity, ...]
    remaining: tuple[ProcessIdentity, ...]


@dataclass(frozen=True)
class SystemPressure:
    total_ram_gb: Optional[float]
    available_ram_gb: Optional[float]
    pagefile_used_gb: Optional[float]
    total_vram_gb: Optional[float]
    free_vram_gb: Optional[float]
    nvidia_overlay_running: bool


@dataclass(frozen=True)
class WindowRect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class WindowResizeSnapshot:
    hwnd: int
    original: WindowRect
    resized: WindowRect
    original_was_maximized: bool = False


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _SYSTEM_PAGEFILE_INFORMATION_HEADER(ctypes.Structure):
    _fields_ = [
        ("NextEntryOffset", wintypes.ULONG),
        ("TotalSize", wintypes.ULONG),
        ("TotalInUse", wintypes.ULONG),
        ("PeakUsage", wintypes.ULONG),
    ]


if os.name == "nt":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
        ctypes.POINTER(_FILETIME),
    ]
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.GlobalMemoryStatusEx.argtypes = [
        ctypes.POINTER(_MEMORYSTATUSEX)
    ]
    _ntdll.NtQuerySystemInformation.restype = wintypes.LONG
    _ntdll.NtQuerySystemInformation.argtypes = [
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    ]
    _psapi.EnumProcesses.argtypes = [
        ctypes.POINTER(wintypes.DWORD),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_PROCESS_MEMORY_COUNTERS_EX),
        wintypes.DWORD,
    ]
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsZoomed.argtypes = [wintypes.HWND]
    _user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
else:
    _kernel32 = _ntdll = _psapi = _user32 = None


def _filetime_value(value: _FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _normalized_path(path: str) -> str:
    return str(path or "").replace("/", "\\").lower()


def _matches_spec(process: ProcessIdentity, spec: AppSpec) -> bool:
    name = process.executable_name.lower()
    path = _normalized_path(process.executable_path)
    if not path or name in _PROTECTED_NAMES:
        return False
    return (
        name in spec.executable_names
        and any(fragment in path for fragment in spec.path_fragments)
    )


def _read_process(pid: int) -> Optional[ProcessIdentity]:
    if _kernel32 is None or _psapi is None or pid <= 4:
        return None
    access = (
        _PROCESS_QUERY_LIMITED_INFORMATION
        | _PROCESS_QUERY_INFORMATION
        | _PROCESS_VM_READ
    )
    handle = _kernel32.OpenProcess(access, False, int(pid))
    if not handle:
        return None
    try:
        path_buffer = ctypes.create_unicode_buffer(32768)
        path_size = wintypes.DWORD(len(path_buffer))
        if not _kernel32.QueryFullProcessImageNameW(
            handle,
            0,
            path_buffer,
            ctypes.byref(path_size),
        ):
            return None
        created = _FILETIME()
        exited = _FILETIME()
        kernel = _FILETIME()
        user = _FILETIME()
        if not _kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        counters = _PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(counters)
        if not _psapi.GetProcessMemoryInfo(
            handle,
            ctypes.byref(counters),
            counters.cb,
        ):
            counters.WorkingSetSize = 0
            counters.PrivateUsage = 0
        path = path_buffer.value
        return ProcessIdentity(
            pid=int(pid),
            created_at_100ns=_filetime_value(created),
            executable_name=Path(path).name.lower(),
            executable_path=path,
            working_set_bytes=int(counters.WorkingSetSize),
            private_bytes=int(counters.PrivateUsage),
        )
    finally:
        _kernel32.CloseHandle(handle)


def scan_processes() -> tuple[ProcessIdentity, ...]:
    """현재 프로세스를 읽기 전용으로 열거합니다."""

    if _psapi is None:
        return ()
    capacity = 4096
    while capacity <= 65536:
        values = (wintypes.DWORD * capacity)()
        needed = wintypes.DWORD()
        if not _psapi.EnumProcesses(
            values,
            ctypes.sizeof(values),
            ctypes.byref(needed),
        ):
            return ()
        count = int(needed.value // ctypes.sizeof(wintypes.DWORD))
        if count < capacity:
            break
        capacity *= 2
    else:
        return ()

    current_pid = os.getpid()
    result: list[ProcessIdentity] = []
    for pid in values[:count]:
        if int(pid) == current_pid:
            continue
        process = _read_process(int(pid))
        if process is not None:
            result.append(process)
    return tuple(result)


def group_candidates(
    processes: Iterable[ProcessIdentity],
) -> tuple[TurboCandidate, ...]:
    """종료 허용 목록에 정확히 일치하는 프로세스만 앱 단위로 묶습니다."""

    all_processes = tuple(processes)
    candidates: list[TurboCandidate] = []
    for spec in APP_SPECS:
        matched = tuple(
            process
            for process in all_processes
            if _matches_spec(process, spec)
        )
        if not matched:
            continue
        candidates.append(
            TurboCandidate(
                key=spec.key,
                label=spec.label,
                processes=matched,
                working_set_bytes=sum(
                    process.working_set_bytes for process in matched
                ),
                private_bytes=sum(
                    process.private_bytes for process in matched
                ),
            )
        )
    return tuple(candidates)


def scan_candidates() -> tuple[TurboCandidate, ...]:
    return group_candidates(scan_processes())


def _same_process(identity: ProcessIdentity) -> Optional[ProcessIdentity]:
    current = _read_process(identity.pid)
    if current is None:
        return None
    if current.created_at_100ns != identity.created_at_100ns:
        return None
    if current.executable_name != identity.executable_name:
        return None
    return current


def _is_allowed_identity(identity: ProcessIdentity) -> bool:
    return any(_matches_spec(identity, spec) for spec in APP_SPECS)


def _post_close_to_windows(pids: set[int]) -> int:
    if _user32 is None or not pids:
        return 0
    posted = 0
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    @callback_type
    def callback(hwnd, _lparam):
        nonlocal posted
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) not in pids:
            return True
        if not _user32.IsWindowVisible(hwnd):
            return True
        if _user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0):
            posted += 1
        return True

    _user32.EnumWindows(callback, 0)
    return posted


def close_selected_gracefully(
    candidates: Iterable[TurboCandidate],
    selected_keys: Iterable[str],
    timeout_seconds: float = 2.0,
) -> CloseResult:
    """선택 앱의 보이는 최상위 창에 WM_CLOSE를 보내고 종료를 기다립니다."""

    selected = {str(key) for key in selected_keys if key in _SPEC_BY_KEY}
    requested = tuple(
        process
        for candidate in candidates
        if candidate.key in selected
        for process in candidate.processes
        if _is_allowed_identity(process)
    )
    _post_close_to_windows({process.pid for process in requested})

    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    remaining = requested
    while remaining and time.monotonic() < deadline:
        time.sleep(0.050)
        remaining = tuple(
            process
            for process in requested
            if _same_process(process) is not None
        )
    remaining = tuple(
        process
        for process in requested
        if _same_process(process) is not None
    )
    remaining_keys = {
        (process.pid, process.created_at_100ns) for process in remaining
    }
    closed = tuple(
        process
        for process in requested
        if (process.pid, process.created_at_100ns) not in remaining_keys
    )
    return CloseResult(requested, closed, remaining)


def force_close_remaining(
    processes: Iterable[ProcessIdentity],
) -> CloseResult:
    """재확인 뒤 전달된 동일 PID·생성시각의 허용 앱만 강제 종료합니다."""

    requested = tuple(
        process for process in processes if _is_allowed_identity(process)
    )
    closed: list[ProcessIdentity] = []
    remaining: list[ProcessIdentity] = []
    for original in requested:
        current = _same_process(original)
        if current is None:
            closed.append(original)
            continue
        handle = _kernel32.OpenProcess(
            _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            int(original.pid),
        )
        if not handle:
            remaining.append(original)
            continue
        try:
            if _kernel32.TerminateProcess(handle, 0):
                closed.append(original)
            else:
                remaining.append(original)
        finally:
            _kernel32.CloseHandle(handle)
    if closed:
        time.sleep(0.150)
    still_running = tuple(
        process
        for process in requested
        if _same_process(process) is not None
    )
    still_keys = {
        (process.pid, process.created_at_100ns)
        for process in still_running
    }
    return CloseResult(
        requested,
        tuple(
            process
            for process in requested
            if (process.pid, process.created_at_100ns) not in still_keys
        ),
        still_running,
    )


def _nvidia_memory() -> tuple[Optional[float], Optional[float]]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return None, None
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            [
                executable,
                "--query-gpu=memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
            creationflags=flags,
        )
        first = result.stdout.strip().splitlines()[0]
        total_mb_text, used_mb_text = first.split(",", 1)
        total_mb = float(total_mb_text.strip())
        used_mb = float(used_mb_text.strip())
        return total_mb / 1024.0, max(0.0, total_mb - used_mb) / 1024.0
    except Exception:
        return None, None


def _pagefile_used_gb() -> Optional[float]:
    """SystemPageFileInformation에서 실제 페이지파일 사용량을 읽습니다."""

    if _ntdll is None or _kernel32 is None:
        return None
    size = 64 * 1024
    for _attempt in range(4):
        buffer = ctypes.create_string_buffer(size)
        returned = wintypes.ULONG()
        status = int(
            _ntdll.NtQuerySystemInformation(
                18,  # SystemPageFileInformation
                buffer,
                size,
                ctypes.byref(returned),
            )
        )
        unsigned_status = ctypes.c_ulong(status).value
        if unsigned_status == 0xC0000004:  # STATUS_INFO_LENGTH_MISMATCH
            size = max(size * 2, int(returned.value) + 4096)
            continue
        if status != 0:
            return None

        # SYSTEM_INFO의 첫 DWORD 다음 필드가 dwPageSize입니다.
        system_info = ctypes.create_string_buffer(64)
        _kernel32.GetSystemInfo(system_info)
        page_size = int.from_bytes(system_info.raw[4:8], "little")
        if page_size <= 0:
            return None

        total_pages = 0
        offset = 0
        while offset + ctypes.sizeof(_SYSTEM_PAGEFILE_INFORMATION_HEADER) <= size:
            header = _SYSTEM_PAGEFILE_INFORMATION_HEADER.from_buffer_copy(
                buffer.raw,
                offset,
            )
            total_pages += int(header.TotalInUse)
            if int(header.NextEntryOffset) <= 0:
                break
            offset += int(header.NextEntryOffset)
        return float(total_pages * page_size) / float(1024**3)
    return None


def measure_pressure(
    processes: Optional[Iterable[ProcessIdentity]] = None,
) -> SystemPressure:
    total_ram_gb: Optional[float] = None
    available_ram_gb: Optional[float] = None
    if _kernel32 is not None:
        status = _MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            divisor = float(1024**3)
            total_ram_gb = float(status.ullTotalPhys) / divisor
            available_ram_gb = float(status.ullAvailPhys) / divisor
    process_list = tuple(processes) if processes is not None else scan_processes()
    overlay_running = any(
        process.executable_name in _OVERLAY_NAMES
        for process in process_list
    )
    total_vram_gb, free_vram_gb = _nvidia_memory()
    return SystemPressure(
        total_ram_gb,
        available_ram_gb,
        _pagefile_used_gb(),
        total_vram_gb,
        free_vram_gb,
        overlay_running,
    )


def _get_window_rect(hwnd: int) -> WindowRect:
    if _user32 is None or not _user32.IsWindow(int(hwnd)):
        raise RuntimeError("FC ONLINE 창이 더 이상 유효하지 않습니다.")
    rect = _RECT()
    if not _user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
        raise RuntimeError("FC ONLINE 창 크기를 읽지 못했습니다.")
    return WindowRect(
        int(rect.left),
        int(rect.top),
        int(rect.right),
        int(rect.bottom),
    )


def get_window_rect(hwnd: int) -> WindowRect:
    """유효한 창의 현재 외곽 위치와 크기를 읽습니다."""

    return _get_window_rect(hwnd)


def target_outer_size_for_wgc(
    current_outer: WindowRect,
    current_wgc_size: tuple[int, int],
    target_wgc_size: tuple[int, int] = TARGET_WGC_SIZE,
) -> tuple[int, int]:
    """Compensate only for the measured outer-window/WGC size difference."""

    current_wgc_width = int(current_wgc_size[0])
    current_wgc_height = int(current_wgc_size[1])
    target_wgc_width = int(target_wgc_size[0])
    target_wgc_height = int(target_wgc_size[1])
    if (
        current_wgc_width < 640
        or current_wgc_height < 480
        or target_wgc_width < 640
        or target_wgc_height < 480
    ):
        raise ValueError("WGC size is too small for safe automatic fitting.")
    width_difference = current_outer.width - current_wgc_width
    height_difference = current_outer.height - current_wgc_height
    if abs(width_difference) > 64 or abs(height_difference) > 64:
        raise ValueError(
            "Outer-window/WGC size difference exceeds the 64 px safety limit."
        )
    return (
        target_wgc_width + width_difference,
        target_wgc_height + height_difference,
    )


def resize_window_no_activate(
    hwnd: int,
    target_size: tuple[int, int] = TARGET_WGC_SIZE,
) -> WindowResizeSnapshot:
    """창을 활성화하거나 Z 순서를 바꾸지 않고 목표 외곽 크기로 조정합니다."""

    original = _get_window_rect(hwnd)
    original_was_maximized = bool(_user32.IsZoomed(int(hwnd)))
    width, height = (int(target_size[0]), int(target_size[1]))
    if width < 640 or height < 480:
        raise ValueError("터보 창 크기가 너무 작습니다.")
    if original_was_maximized:
        # SetWindowPos cannot resize a maximized FC window.  This restores the
        # normal placement without activating the game or changing Z order.
        _user32.ShowWindowAsync(int(hwnd), _SW_SHOWNOACTIVATE)
        deadline = time.monotonic() + 1.0
        while bool(_user32.IsZoomed(int(hwnd))) and time.monotonic() < deadline:
            time.sleep(0.005)
        if bool(_user32.IsZoomed(int(hwnd))):
            raise RuntimeError("FC ONLINE maximized window could not be restored.")
    if not _user32.SetWindowPos(
        int(hwnd),
        0,
        original.left,
        original.top,
        width,
        height,
        _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
    ):
        raise RuntimeError("FC ONLINE 창 크기 변경이 거부되었습니다.")
    resized = _get_window_rect(hwnd)
    if abs(resized.width - width) > 2 or abs(resized.height - height) > 2:
        # 게임이 일부 크기만 적용한 경우에도 세션 시작 전 상태로 되돌립니다.
        _user32.SetWindowPos(
            int(hwnd),
            0,
            original.left,
            original.top,
            original.width,
            original.height,
            _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
        )
        if original_was_maximized:
            _user32.ShowWindowAsync(int(hwnd), _SW_SHOWMAXIMIZED)
        raise RuntimeError(
            f"FC ONLINE이 목표 크기를 적용하지 않았습니다: "
            f"{resized.width}x{resized.height}"
        )
    return WindowResizeSnapshot(
        int(hwnd),
        original,
        resized,
        original_was_maximized,
    )


def restore_window_no_activate(snapshot: WindowResizeSnapshot) -> WindowRect:
    """같은 HWND가 살아 있을 때만 세션 시작 전 창 위치와 크기로 복원합니다."""

    current = _get_window_rect(snapshot.hwnd)
    original = snapshot.original
    if not _user32.SetWindowPos(
        int(snapshot.hwnd),
        0,
        original.left,
        original.top,
        original.width,
        original.height,
        _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
    ):
        raise RuntimeError("FC ONLINE 원래 창 크기 복원이 거부되었습니다.")
    if snapshot.original_was_maximized:
        _user32.ShowWindowAsync(int(snapshot.hwnd), _SW_SHOWMAXIMIZED)
        deadline = time.monotonic() + 1.0
        while (
            not bool(_user32.IsZoomed(int(snapshot.hwnd)))
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        if not bool(_user32.IsZoomed(int(snapshot.hwnd))):
            raise RuntimeError("FC ONLINE maximized state could not be restored.")
    restored = _get_window_rect(snapshot.hwnd)
    if (
        abs(restored.left - original.left) > 2
        or abs(restored.top - original.top) > 2
        or abs(restored.width - original.width) > 2
        or abs(restored.height - original.height) > 2
    ):
        raise RuntimeError(
            f"원래 창 크기 복원이 완료되지 않았습니다: "
            f"{restored.width}x{restored.height}"
        )
    return restored
