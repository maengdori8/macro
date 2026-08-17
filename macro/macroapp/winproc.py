"""Windows 프로세스 열거·일시정지·재개 (ctypes).

리소스 모니터의 '프로세스 일시 중단 / 다시 시작' 과 같은 커널 호출을 쓴다:
ntdll!NtSuspendProcess / ntdll!NtResumeProcess.

의존성은 전부 가드한다 — Windows 가 아니면 import 는 되고 호출만 실패한다.
그래야 순수 로직(core.py) 테스트가 다른 OS 에서도 돌아간다.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Iterator

IS_WINDOWS = sys.platform == "win32"

# --- Windows 의존성 가드 (macroapp/winapi.py 와 같은 원칙) -------------------
if IS_WINDOWS:  # pragma: no cover - 플랫폼 분기
    import ctypes
    from ctypes import wintypes

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    except OSError:  # DLL 로드 실패 — 있을 수 없지만 죽이지는 않는다
        kernel32 = ntdll = advapi32 = None
else:  # pragma: no cover - 플랫폼 분기
    ctypes = None  # type: ignore[assignment]
    wintypes = None  # type: ignore[assignment]
    kernel32 = ntdll = advapi32 = None


class ProcessControlError(RuntimeError):
    """제어 실패.

    ⚠️ message 는 사용자에게 그대로 보일 수 있으므로 **중립 문구만** 담는다.
    대상 이름·PID·수행한 동작(정지/재개)을 절대 넣지 않는다 — 오류 문구 한 줄이
    앱이 무엇을 어떻게 하는지 통째로 알려 주기 때문이다.
    분기 판단은 문자열이 아니라 reason 코드로 한다(문구를 바꿔도 로직이 안 깨진다).

    reason: platform | enumerate | denied | gone | open | control
    """

    def __init__(self, message: str, reason: str = "control") -> None:
        super().__init__(message)
        self.reason = reason


class ProcessGoneError(ProcessControlError):
    """대상이 이미 종료됐다.

    열거 스냅샷을 찍은 뒤 실제로 열기까지 사이에 프로세스가 끝나는 일은
    흔하다(경합). 이건 오류가 아니라 '이 대상은 건너뛴다'는 뜻이므로,
    나머지 대상까지 통째로 실패시키지 않도록 따로 구분한다.
    """

    def __init__(self, message: str = "대상이 이미 종료되었습니다.") -> None:
        super().__init__(message, reason="gone")


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

TH32CS_SNAPPROCESS = 0x00000002

#: CreateToolhelp32Snapshot 실패값.
#: ⚠️ 정수 -1 과 비교하면 안 된다. restype 이 HANDLE(=c_void_p) 이라
#: ctypes 가 부호 없는 정수(0xFFFFFFFFFFFFFFFF)로 돌려주므로 -1 과 절대 같지 않고,
#: NULL 도 아니어서 `not snapshot` 에도 안 걸린다 → 실패 가드가 통째로 죽는다.
#: (실측: 실패 시 18446744073709551615 반환 → 빈 목록이 조용히 반환됐다.)
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value if IS_WINDOWS else -1

PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001

STILL_ACTIVE = 259

ERROR_ACCESS_DENIED = 5
#: 존재하지 않는 PID 로 OpenProcess 하면 이 오류가 온다(실측 확인).
ERROR_INVALID_PARAMETER = 87

#: 종료 중인 프로세스를 정지/재개하려 할 때의 NTSTATUS. 오류가 아니라
#: '이미 끝났다'는 뜻이므로 실패로 취급하지 않는다.
STATUS_PROCESS_IS_TERMINATING = 0xC000010A

TOKEN_ADJUST_PRIVILEGES = 0x0020
TOKEN_QUERY = 0x0008
SE_PRIVILEGE_ENABLED = 0x00000002
SE_DEBUG_NAME = "SeDebugPrivilege"

MAX_PATH = 260


# ---------------------------------------------------------------------------
# 구조체 / 함수 시그니처
# ---------------------------------------------------------------------------

if IS_WINDOWS and kernel32 is not None:  # pragma: no cover - 플랫폼 분기

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            # ULONG_PTR — x64 에서 8바이트. 여기서 크기를 틀리면 뒤 필드가 전부 밀린다.
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * MAX_PATH),
        ]

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", ctypes.c_long)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", wintypes.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [
            ("PrivilegeCount", wintypes.DWORD),
            ("Privileges", LUID_AND_ATTRIBUTES * 1),
        ]

    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL

    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE

    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL

    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE

    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL

    # NTSTATUS 는 LONG. 음수면 실패.
    ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtSuspendProcess.restype = ctypes.c_long
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL

    advapi32.LookupPrivilegeValueW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.POINTER(LUID),
    ]
    advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL

    advapi32.AdjustTokenPrivileges.argtypes = [
        wintypes.HANDLE,
        wintypes.BOOL,
        ctypes.POINTER(TOKEN_PRIVILEGES),
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL


# ---------------------------------------------------------------------------
# 권한
# ---------------------------------------------------------------------------

_debug_privilege_state: bool | None = None


def enable_debug_privilege() -> bool:
    """SeDebugPrivilege 를 켠다. 관리자 권한이 있어야 성공한다.

    실패해도 예외를 던지지 않는다 — 같은 사용자·같은 무결성 수준의 프로세스는
    이 권한 없이도 제어할 수 있기 때문. 결과는 한 번만 계산해 캐시한다.
    """
    global _debug_privilege_state
    if _debug_privilege_state is not None:
        return _debug_privilege_state
    if not IS_WINDOWS or advapi32 is None:
        _debug_privilege_state = False
        return False

    token = wintypes.HANDLE()
    try:
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
            ctypes.byref(token),
        ):
            _debug_privilege_state = False
            return False
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, SE_DEBUG_NAME, ctypes.byref(luid)):
            _debug_privilege_state = False
            return False
        privileges = TOKEN_PRIVILEGES()
        privileges.PrivilegeCount = 1
        privileges.Privileges[0].Luid = luid
        privileges.Privileges[0].Attributes = SE_PRIVILEGE_ENABLED
        ok = advapi32.AdjustTokenPrivileges(
            token, False, ctypes.byref(privileges), 0, None, None
        )
        # AdjustTokenPrivileges 는 일부만 적용돼도 TRUE 를 준다.
        # ERROR_NOT_ALL_ASSIGNED(1300) 이면 실제로는 못 켠 것.
        err = ctypes.get_last_error()
        _debug_privilege_state = bool(ok) and err == 0
        return _debug_privilege_state
    except OSError:
        _debug_privilege_state = False
        return False
    finally:
        if token:
            kernel32.CloseHandle(token)


def is_admin() -> bool:
    """관리자 권한으로 실행 중인가."""
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 프로세스 열거
# ---------------------------------------------------------------------------

def iter_processes() -> Iterator[tuple[int, str]]:
    """실행 중인 (pid, exe 이름) 을 나열한다."""
    if not IS_WINDOWS or kernel32 is None:
        raise ProcessControlError(
            "이 기능은 Windows 에서만 동작합니다.", reason="platform"
        )

    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE or not snapshot:
        raise ProcessControlError(
            f"준비하지 못했습니다 (코드 {ctypes.get_last_error()}).",
            reason="enumerate",
        )
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return
        while True:
            yield int(entry.th32ProcessID), str(entry.szExeFile)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)


def list_processes() -> list[tuple[int, str]]:
    """iter_processes() 의 리스트 버전 (스냅샷 핸들을 즉시 닫기 위함)."""
    return list(iter_processes())


# ---------------------------------------------------------------------------
# 핸들 — 일시정지와 재개 사이에 계속 붙잡고 있는다
# ---------------------------------------------------------------------------

@dataclass
class ProcessHandle:
    """열린 프로세스 핸들 하나.

    일시정지 → 재개 사이에 핸들을 닫지 않는 것이 핵심이다.
    핸들을 쥐고 있으면 커널이 그 PID 를 재사용하지 못하므로,
    10초 뒤에 '엉뚱한 새 프로세스를 재개'하는 사고가 원천 차단된다.
    """

    pid: int
    name: str
    handle: int

    def close(self) -> None:
        if self.handle and kernel32 is not None:
            kernel32.CloseHandle(self.handle)
        self.handle = 0

    def is_alive(self) -> bool:
        if not self.handle or kernel32 is None:
            return False
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(self.handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE


def open_process(pid: int, name: str = "") -> ProcessHandle:
    """필요한 권한으로 대상을 연다.

    ⚠️ 오류 문구에 name/pid 를 넣지 않는다(중립 문구 규칙). 어느 대상에서
    실패했는지는 호출부가 이미 알고 있고, 사용자에게는 알릴 필요가 없다.
    """
    if not IS_WINDOWS or kernel32 is None:
        raise ProcessControlError(
            "이 기능은 Windows 에서만 동작합니다.", reason="platform"
        )

    access = PROCESS_SUSPEND_RESUME | PROCESS_QUERY_LIMITED_INFORMATION
    handle = kernel32.OpenProcess(access, False, pid)
    if not handle:
        err = ctypes.get_last_error()
        if err == ERROR_ACCESS_DENIED:
            hint = "" if is_admin() else " 관리자 권한으로 다시 실행해 보세요."
            raise ProcessControlError(
                f"접근이 거부되었습니다.{hint}", reason="denied"
            )
        if err == ERROR_INVALID_PARAMETER:
            # 열거한 뒤 여는 사이에 끝난 프로세스. 나머지 대상은 계속 처리해야 한다.
            raise ProcessGoneError()
        raise ProcessControlError(
            f"준비하지 못했습니다 (코드 {err}).", reason="open"
        )
    return ProcessHandle(pid=pid, name=name, handle=handle)


# ---------------------------------------------------------------------------
# 일시정지 / 재개
# ---------------------------------------------------------------------------

def _nt_call(func, target: ProcessHandle) -> None:
    status = func(target.handle)
    if status >= 0:  # NTSTATUS 음수만 실패
        return
    code = status & 0xFFFFFFFF
    if code == STATUS_PROCESS_IS_TERMINATING:
        # 호출하려는 사이에 대상이 끝났다. 실패가 아니다 — 여기서 예외를 던지면
        # 여러 대상 중 하나가 죽었을 뿐인데 실행 전체가 통째로 실패한다
        # (실측으로 확인된 경로).
        raise ProcessGoneError()
    # 어떤 호출이었는지는 문구에 남기지 않는다(중립 문구 규칙).
    # NTSTATUS 숫자는 그 자체로 동작을 알려 주지 않으므로 진단용으로 남긴다.
    raise ProcessControlError(
        f"요청을 처리하지 못했습니다 (코드 0x{code:08X}).", reason="control"
    )


def suspend(target: ProcessHandle) -> None:
    """대상의 모든 스레드를 정지시킨다."""
    if ntdll is None:
        raise ProcessControlError(
            "이 기능은 Windows 에서만 동작합니다.", reason="platform"
        )
    _nt_call(ntdll.NtSuspendProcess, target)


def resume(target: ProcessHandle) -> None:
    """정지된 대상을 다시 돌린다."""
    if ntdll is None:
        raise ProcessControlError(
            "이 기능은 Windows 에서만 동작합니다.", reason="platform"
        )
    _nt_call(ntdll.NtResumeProcess, target)


def current_pid() -> int:
    """자기 자신의 PID — 대상에서 제외하기 위해."""
    return os.getpid()


def identity_token(target: ProcessHandle) -> int:
    """PID 재사용을 구별하는 토큰 — 프로세스 생성 시각. 실패하면 0.

    PID 는 재사용된다. 기록해 둔 PID 를 나중에 다시 열었을 때 그것이 **같은**
    프로세스인지 확인할 근거가 필요하다. 생성 시각은 PID 가 재사용돼도 같을 수
    없으므로(같은 100나노초에 시작할 수 없다) 이 조합이 사실상 유일한 신원이 된다.
    """
    if not IS_WINDOWS or kernel32 is None or not target.handle:
        return 0
    creation = wintypes.FILETIME()
    exited = wintypes.FILETIME()
    kernel_time = wintypes.FILETIME()
    user_time = wintypes.FILETIME()
    try:
        ok = kernel32.GetProcessTimes(
            target.handle,
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        )
    except OSError:
        return 0
    if not ok:
        return 0
    return (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
