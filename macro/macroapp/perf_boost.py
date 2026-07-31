"""게임에 물리 램을 몰아주는 안전한 OS 최적화(갱신매크로 내장용).

핵심 안전 원칙 — FC ONLINE은 BlackCipher 안티치트가 돈다:
  * 게임(fczf) · 안티치트 프로세스엔 **절대 핸들을 열지 않는다**(트림·우선순위 모두 제외).
    외부에서 게임 프로세스 핸들을 여는 것 자체가 탐지 트리거가 될 수 있기 때문.
  * 대기 메모리 정리는 시스템 콜(NtSetSystemInformation)이라 어떤 프로세스 핸들도 안 연다.
  * 워킹셋 트림은 '비게임·비시스템 대형 프로세스(크롬 등)'에만 적용 → 그 램을 반납시켜
    게임이 스왑(페이지파일) 대신 물리 램에서 돌게 한다. 트림은 비파괴적(앱은 계속 실행).
  * 아무 프로세스도 종료하지 않는다.

전부 ctypes(win32)만 사용하고 모든 단계가 예외 가드라, 실패해도 매크로 본체엔 영향이 없다.
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Optional

try:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
except Exception:  # noqa: BLE001 — Windows가 아니면 전체 비활성
    _kernel32 = _ntdll = _psapi = _advapi32 = None

# ── 64비트 핸들이 잘리지 않도록 함수 시그니처를 명시(restype=HANDLE 미지정 시 핸들이
#    32비트 int로 잘려 오작동). 실패해도 매크로 본체엔 영향 없도록 전부 가드. ──
_HANDLE = wintypes.HANDLE
_BOOL = wintypes.BOOL
_DWORD = wintypes.DWORD


def _setup_signatures() -> None:
    if _kernel32 is None:
        return
    try:
        _kernel32.GetCurrentProcess.restype = _HANDLE
        _kernel32.GetCurrentProcess.argtypes = []
        _kernel32.SetPriorityClass.restype = _BOOL
        _kernel32.SetPriorityClass.argtypes = [_HANDLE, _DWORD]
        _kernel32.OpenProcess.restype = _HANDLE
        _kernel32.OpenProcess.argtypes = [_DWORD, _BOOL, _DWORD]
        _kernel32.CloseHandle.restype = _BOOL
        _kernel32.CloseHandle.argtypes = [_HANDLE]
        _kernel32.CreateToolhelp32Snapshot.restype = _HANDLE
        _kernel32.CreateToolhelp32Snapshot.argtypes = [_DWORD, _DWORD]
        _kernel32.Process32FirstW.restype = _BOOL
        _kernel32.Process32FirstW.argtypes = [_HANDLE, ctypes.c_void_p]
        _kernel32.Process32NextW.restype = _BOOL
        _kernel32.Process32NextW.argtypes = [_HANDLE, ctypes.c_void_p]
        _psapi.GetProcessMemoryInfo.restype = _BOOL
        _psapi.GetProcessMemoryInfo.argtypes = [_HANDLE, ctypes.c_void_p, _DWORD]
        _psapi.EmptyWorkingSet.restype = _BOOL
        _psapi.EmptyWorkingSet.argtypes = [_HANDLE]
        _ntdll.NtSetSystemInformation.restype = wintypes.LONG
        _ntdll.NtSetSystemInformation.argtypes = [ctypes.c_int, ctypes.c_void_p, wintypes.ULONG]
        _advapi32.OpenProcessToken.restype = _BOOL
        _advapi32.OpenProcessToken.argtypes = [_HANDLE, _DWORD, ctypes.POINTER(_HANDLE)]
        _advapi32.LookupPrivilegeValueW.restype = _BOOL
        _advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
        _advapi32.AdjustTokenPrivileges.restype = _BOOL
        _advapi32.AdjustTokenPrivileges.argtypes = [
            _HANDLE, _BOOL, ctypes.c_void_p, _DWORD, ctypes.c_void_p, ctypes.c_void_p,
        ]
    except Exception:  # noqa: BLE001
        pass


_setup_signatures()


# ── 게임/안티치트/시스템: 핸들조차 열지 않는다(소문자 부분일치) ──
_PROTECTED = (
    "fczf",           # FC ONLINE 게임 본체
    "blackcipher",    # 안티치트
    "wellbia", "xigncode", "nprotect", "gameguard", "npggnt",  # 흔한 안티치트류
    "renewal_macro", "macro", "gamepad_test", "launcher",       # 우리 자신
    "system", "registry", "smss", "csrss", "wininit", "winlogon",
    "services", "lsass", "svchost", "dwm", "fontdrvhost", "memcompression",
)

# 이 크기(MB) 이상만 트림 — 작은 프로세스는 회수 이득이 없어 건너뛴다.
_TRIM_MIN_MB = 150

# NtSetSystemInformation
_SystemMemoryListInformation = 0x50
_MemoryPurgeStandbyList = 4

# 권한
_SE_PRIVILEGE_ENABLED = 0x00000002
_TOKEN_ADJUST_PRIVILEGES = 0x0020
_TOKEN_QUERY = 0x0008

# OpenProcess 접근권 — 트림에 필요한 최소치(정보 조회 + 쿼타 설정)
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# SetPriorityClass
_ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000


def available() -> bool:
    return _kernel32 is not None


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [("PrivilegeCount", wintypes.DWORD),
                ("Privileges", _LUID_AND_ATTRIBUTES * 1)]


def _enable_privilege(name: str) -> bool:
    """현재 프로세스 토큰에 지정 특권을 활성화(대기메모리 정리 등에 필요)."""
    try:
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(),
            _TOKEN_ADJUST_PRIVILEGES | _TOKEN_QUERY,
            ctypes.byref(token),
        ):
            return False
        try:
            luid = _LUID()
            if not _advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
                return False
            tp = _TOKEN_PRIVILEGES()
            tp.PrivilegeCount = 1
            tp.Privileges[0].Luid = luid
            tp.Privileges[0].Attributes = _SE_PRIVILEGE_ENABLED
            if not _advapi32.AdjustTokenPrivileges(
                token, False, ctypes.byref(tp), 0, None, None
            ):
                return False
            # AdjustTokenPrivileges는 부분 실패해도 TRUE를 반환하므로 GetLastError로 확인.
            return ctypes.get_last_error() == 0
        finally:
            _kernel32.CloseHandle(token)
    except Exception:  # noqa: BLE001
        return False


def raise_own_priority() -> bool:
    """매크로 자기 프로세스 우선순위만 ABOVE_NORMAL로 올린다(게임은 절대 안 건드림).

    HIGH는 게임과 CPU를 다투므로 쓰지 않는다. ABOVE_NORMAL이면 인식/클릭 스레드가
    다른 백그라운드 앱보다 먼저 스케줄되되 게임을 굶기지 않는다.
    """
    if _kernel32 is None:
        return False
    try:
        return bool(
            _kernel32.SetPriorityClass(
                _kernel32.GetCurrentProcess(), _ABOVE_NORMAL_PRIORITY_CLASS
            )
        )
    except Exception:  # noqa: BLE001
        return False


def purge_standby_list() -> bool:
    """대기 메모리 목록을 비운다(캐시로 잡힌 물리 램을 즉시 가용으로 전환).

    RAMMap의 'Empty Standby List'와 동일. 특정 프로세스 핸들을 열지 않는 시스템 콜이라
    안티치트와 무관하다. SeProfileSingleProcessPrivilege(관리자) 필요.
    """
    if _ntdll is None:
        return False
    _enable_privilege("SeProfileSingleProcessPrivilege")
    try:
        command = ctypes.c_int(_MemoryPurgeStandbyList)
        status = _ntdll.NtSetSystemInformation(
            _SystemMemoryListInformation,
            ctypes.byref(command),
            ctypes.sizeof(command),
        )
        return status == 0
    except Exception:  # noqa: BLE001
        return False


def _iter_processes():
    """(pid, 소문자 이름) 목록을 toolhelp 스냅샷으로 얻는다."""
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == wintypes.HANDLE(-1).value or not snapshot:
        return
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not _kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return
        while True:
            yield int(entry.th32ProcessID), str(entry.szExeFile or "").lower()
            if not _kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        _kernel32.CloseHandle(snapshot)


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
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
    ]


def trim_other_processes(min_mb: int = _TRIM_MIN_MB) -> tuple[int, float]:
    """게임/안티치트/시스템/우리 자신을 제외한 대형 프로세스의 워킹셋을 비운다.

    EmptyWorkingSet은 해당 프로세스의 물리 램을 대기목록/페이지파일로 밀어내(비파괴적)
    물리 램을 회수한다. 게임 프로세스엔 핸들을 열지 않으므로 안티치트와 무관.
    반환: (트림한 프로세스 수, 회수 추정 MB).
    """
    if _kernel32 is None or _psapi is None:
        return 0, 0.0
    trimmed = 0
    freed_mb = 0.0
    for pid, name in _iter_processes():
        if pid <= 4:
            continue
        if any(tag in name for tag in _PROTECTED):
            continue
        handle = _kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            continue
        try:
            counters = _PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            ws_mb = 0.0
            if _psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            ):
                ws_mb = counters.WorkingSetSize / (1024.0 * 1024.0)
            if ws_mb < min_mb:
                continue
            # EmptyWorkingSet(hProcess): 성공 시 워킹셋을 최소로 줄인다.
            if _psapi.EmptyWorkingSet(handle):
                trimmed += 1
                freed_mb += ws_mb
        finally:
            _kernel32.CloseHandle(handle)
    return trimmed, freed_mb


def free_ram_for_game(min_trim_mb: int = _TRIM_MIN_MB) -> tuple[int, float]:
    """대기목록 정리 + 비게임 대형 프로세스 트림을 한 번 수행하고 결과를 반환."""
    purged = purge_standby_list()
    trimmed, freed = trim_other_processes(min_trim_mb)
    return (trimmed + (1 if purged else 0)), freed


class RamBooster:
    """백그라운드 스레드로 주기적으로 게임용 물리 램을 확보한다."""

    def __init__(self, interval_seconds: float = 45.0, logger=None):
        self.interval = max(10.0, float(interval_seconds))
        self.log = logger or (lambda _m: None)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not available() or (self._thread and self._thread.is_alive()):
            return
        raise_own_priority()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # 시작 즉시 1회 확보(스왑 상태를 바로 완화), 이후 주기 반복.
        while not self._stop.is_set():
            try:
                count, freed = free_ram_for_game()
                if freed >= 50:
                    self.log(f"[램 확보] {count}개 정리 · 약 {freed:.0f}MB 반납")
            except Exception as exc:  # noqa: BLE001
                self.log(f"[램 확보] 건너뜀: {exc}")
            self._stop.wait(self.interval)
