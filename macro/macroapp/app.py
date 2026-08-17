"""진입점. stdout/stderr 가드 후 Tk 루트를 만들고 라이센스 다이얼로그를 띄웁니다."""

from __future__ import annotations

import os
import sys
import tkinter as tk
import ctypes
import traceback
from ctypes import wintypes
from pathlib import Path

try:
    import win32gui
except ImportError:  # pragma: no cover - non-Windows development only
    win32gui = None

from macroapp.paths import app_dir
from macroapp.config import migrate_custom_targets
from macroapp.gui import LicenseDialog


_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE = 0x08000000
_GA_ROOT = 2
_WH_CBT = 5
_HCBT_CREATEWND = 3
_HCBT_ACTIVATE = 5
_BACKGROUND_HOOK_KEEPALIVE: list[tuple] = []


def _install_background_noactivate_hook(foreground_before: int = 0):
    """Harden Tk HWNDs before Windows can activate them.

    ``tk.Tk()`` creates its native wrapper before Python can call
    ``withdraw``. A thread-local CBT hook is the only point where the
    ``CREATESTRUCT`` can be amended before that wrapper becomes eligible for
    foreground activation. The hook observes only this process' current UI
    thread and neither captures nor injects keyboard/mouse input.
    """

    if os.name != "nt":
        return None

    class CreateStructW(ctypes.Structure):
        _fields_ = (
            ("lpCreateParams", ctypes.c_void_p),
            ("hInstance", wintypes.HINSTANCE),
            ("hMenu", wintypes.HMENU),
            ("hwndParent", wintypes.HWND),
            ("cy", ctypes.c_int),
            ("cx", ctypes.c_int),
            ("y", ctypes.c_int),
            ("x", ctypes.c_int),
            ("style", wintypes.LONG),
            ("lpszName", ctypes.c_void_p),
            ("lpszClass", ctypes.c_void_p),
            ("dwExStyle", wintypes.DWORD),
        )

    class CbtCreateWndW(ctypes.Structure):
        _fields_ = (
            ("lpcs", ctypes.POINTER(CreateStructW)),
            ("hwndInsertAfter", wintypes.HWND),
        )

    hook_proc_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    call_next = user32.CallNextHookEx
    call_next.argtypes = (
        wintypes.HHOOK,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    call_next.restype = ctypes.c_ssize_t

    @hook_proc_type
    def hook_proc(code, wparam, lparam):
        hwnd = int(wparam or 0)
        if code == _HCBT_ACTIVATE and hwnd:
            # The background-only process must never become the active queue
            # owner, even during Tk's first native wrapper transition.  This
            # is a thread-local activation veto; it neither focuses the game
            # nor generates keyboard/mouse input.
            return 1
        if code == _HCBT_CREATEWND and hwnd and lparam:
            try:
                create = ctypes.cast(
                    lparam,
                    ctypes.POINTER(CbtCreateWndW),
                ).contents
                create.lpcs.contents.dwExStyle |= _WS_EX_NOACTIVATE
            except Exception:
                pass
        return int(call_next(None, code, wparam, lparam))

    set_hook = user32.SetWindowsHookExW
    set_hook.argtypes = (
        ctypes.c_int,
        hook_proc_type,
        wintypes.HINSTANCE,
        wintypes.DWORD,
    )
    set_hook.restype = wintypes.HHOOK
    handle = set_hook(
        _WH_CBT,
        hook_proc,
        None,
        kernel32.GetCurrentThreadId(),
    )
    if not handle:
        return None

    def uninstall() -> None:
        try:
            user32.UnhookWindowsHookEx(handle)
        except Exception:
            pass

    # Keep the callback and structure classes alive until uninstall.
    return uninstall, hook_proc, CreateStructW, CbtCreateWndW


def _attach_background_creation_queue(foreground_before: int):
    """Temporarily share the existing foreground queue during Tk creation.

    A vetoed first activation can otherwise make ``GetForegroundWindow``
    return zero for a sub-millisecond gap. Attaching only the two UI queues
    keeps the existing active HWND authoritative while the hidden wrapper is
    created. No key state is changed and no input message is generated.
    """

    if os.name != "nt" or not foreground_before:
        return None
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        get_thread = user32.GetWindowThreadProcessId
        get_thread.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        get_thread.restype = wintypes.DWORD
        attach = user32.AttachThreadInput
        attach.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
        attach.restype = wintypes.BOOL

        current_thread = int(kernel32.GetCurrentThreadId())
        foreground_thread = int(get_thread(int(foreground_before), None))
        if (
            not foreground_thread
            or foreground_thread == current_thread
            or not attach(current_thread, foreground_thread, True)
        ):
            return None

        def detach() -> None:
            try:
                attach(current_thread, foreground_thread, False)
            except Exception:
                pass

        return detach
    except Exception:
        return None


def create_background_root(foreground_before: int) -> tk.Tk:
    """Create a withdrawn Tk root without a transient foreground transfer."""

    detach = _attach_background_creation_queue(foreground_before)
    hook = _install_background_noactivate_hook(foreground_before)
    try:
        root = tk.Tk()
        prepare_background_root(root, foreground_before)
        if hook is not None:
            # Keep the thread-local hook and callback alive for the lifetime of
            # this hidden UI. Tk can replace its native wrapper while the full
            # AutomationApp is built; those later HWNDs must also be born with
            # NOACTIVATE. Windows removes the hook when the UI thread exits.
            _BACKGROUND_HOOK_KEEPALIVE.append(hook)
        return root
    finally:
        if detach is not None:
            detach()


def consume_autostart_experiment_request() -> bool:
    """Consume an explicit, one-shot request to start the local experiment.

    The marker survives the installer/UAC boundary that can strip a temporary
    environment variable.  It lives in the current user's LocalAppData and is
    deleted before the UI is created, so a later normal launch stays idle.
    """

    requested = os.environ.get("MAUTO_AUTOSTART_EXPERIMENT", "").strip() == "1"
    requested = requested or any(
        arg.lower() == "--autostart-experiment" for arg in sys.argv[1:]
    )

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return requested

    marker = Path(local_app_data) / "Macro" / "autostart_experiment.once"
    try:
        if marker.is_file():
            requested = True
            marker.unlink()
    except OSError:
        # A stale marker must never prevent the normal application from opening.
        pass
    return requested


def background_experiment_requested() -> bool:
    """Return whether this launch must never map or focus the Tk window.

    This is intentionally a separate, explicit CLI switch.  Normal user
    launches retain the existing foreground behavior, while unattended local
    skip experiments can start without invalidating the focus guard.
    """

    return any(arg.lower() == "--background-experiment" for arg in sys.argv[1:])


def prepare_background_root(root: tk.Tk, foreground_before: int) -> None:
    """Create the withdrawn native Tk host without retaining foreground.

    Tk materializes its ``TkTopLevel`` during the first idle update and Windows
    briefly assigns that hidden HWND as foreground.  Mark the real top-level as
    ``WS_EX_NOACTIVATE`` immediately, then restore the window that was active
    before Tk existed.  All later UI construction happens on the no-activate
    host, so the unattended experiment cannot steal foreground again.
    """

    root.withdraw()
    if win32gui is None:
        return
    try:
        # Force creation once, under our control, so we can style the actual
        # TkTopLevel rather than the intermediate TkChild returned beforehand.
        root.update_idletasks()
        top_hwnd = int(win32gui.GetAncestor(int(root.winfo_id()), _GA_ROOT))
        if top_hwnd:
            style = int(win32gui.GetWindowLong(top_hwnd, _GWL_EXSTYLE))
            win32gui.SetWindowLong(
                top_hwnd,
                _GWL_EXSTYLE,
                style | _WS_EX_NOACTIVATE,
            )
        if foreground_before and win32gui.IsWindow(int(foreground_before)):
            win32gui.SetForegroundWindow(int(foreground_before))
    except Exception:
        # Remaining withdrawn is safer than making startup depend on an
        # optional focus-hardening API.  The external focus monitor will reject
        # the run if restoration did not actually hold.
        pass


def main() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    # 지난 실행이 비정상으로 끝나(작업 관리자 강제 종료·크래시·설치 프로그램의 강제
    # 닫기) 정지된 채 남은 게 있으면 **UI 를 만들기 전에** 되살린다. 프로세스 안의
    # 방어(워커 finally / 창 닫기 / atexit)는 그 경로들에서 하나도 돌지 않는다.
    # 권한을 먼저 켜는 순서가 중요하다 — 그래야 되살릴 수 있는 범위가 정지시킬 때와 같다.
    try:
        from macroapp import ledger, winproc

        winproc.enable_debug_privilege()
        ledger.recover()
    except Exception:
        # 복구가 시작을 막으면 안 된다(막히면 사용자는 손쓸 방법이 없다).
        pass

    background_experiment = background_experiment_requested()
    foreground_before = 0
    if background_experiment and win32gui is not None:
        try:
            foreground_before = int(win32gui.GetForegroundWindow())
        except Exception:
            pass

    root = (
        create_background_root(foreground_before)
        if background_experiment
        else tk.Tk()
    )
    base_dir = app_dir()
    # 예전 설치 폴더에 있던 커스텀 캡처를 업데이트에도 안 지워지는 AppData로 1회 이전.
    migrate_custom_targets(base_dir)
    LicenseDialog(
        root,
        base_dir,
        autostart_experiment=consume_autostart_experiment_request(),
        background_experiment=background_experiment,
    )
    root.mainloop()


def _write_startup_crash() -> None:
    """Persist windowed-build startup failures that have no visible console."""

    report = traceback.format_exc()
    candidates = [app_dir() / "logs" / "startup_crash.log"]
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "Macro" / "logs" / "startup_crash.log"
        )
    for path in candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report, encoding="utf-8")
            return
        except OSError:
            continue


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        _write_startup_crash()
        raise
