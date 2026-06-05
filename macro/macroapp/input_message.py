from __future__ import annotations
import ctypes
from typing import Any, Optional

from macroapp import winapi
from macroapp.config import (
    CLICK_MESSAGE_DELAY_SECONDS,
    MOUSE_HOVER_BEFORE_CLICK_SECONDS,
    _parse_optional_int,
)
import time

KEY_TO_VK: dict[str, int] = {
    "esc": 0x1B, "escape": 0x1B,
    "enter": 0x0D, "return": 0x0D,
    "space": 0x20,
    "tab": 0x09,
    "s": 0x53, "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44,
    "e": 0x45, "f": 0x46, "g": 0x47, "h": 0x48, "i": 0x49,
    "j": 0x4A, "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E,
    "o": 0x4F, "p": 0x50, "q": 0x51, "r": 0x52, "t": 0x54,
    "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59, "z": 0x5A,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101

def send_key_to_window(hwnd: int, vk_code: int, press_delay: float = 0.05) -> bool:
    """PostMessage로 대상 창에 WM_KEYDOWN/WM_KEYUP을 전송합니다."""
    if winapi.win32gui is None:
        raise RuntimeError("pywin32 win32gui 모듈이 필요합니다.")
    scan_code = ctypes.windll.user32.MapVirtualKeyW(vk_code, 0)
    lparam_down = (scan_code << 16) | 1
    lparam_up = (scan_code << 16) | 1 | (1 << 30) | (1 << 31)
    winapi.win32gui.PostMessage(hwnd, WM_KEYDOWN, vk_code, lparam_down)
    time.sleep(press_delay)
    winapi.win32gui.PostMessage(hwnd, WM_KEYUP, vk_code, lparam_up)
    return True

def _make_mouse_lparam(x: int, y: int) -> int:
    """Windows 마우스 메시지 lParam을 클라이언트 좌표로 만듭니다."""

    x = int(x)
    y = int(y)
    if winapi.win32api is not None and hasattr(winapi.win32api, "MAKELONG"):
        return int(winapi.win32api.MAKELONG(x, y))

    return (y & 0xFFFF) << 16 | (x & 0xFFFF)

def _send_mouse_message(
    hwnd: int,
    message: int,
    wparam: int,
    x: int,
    y: int,
    *,
    use_send_message: bool = False,
) -> bool:
    """PostMessage 또는 SendMessage로 대상 HWND에 마우스 메시지를 보냅니다."""

    if winapi.win32gui is None:
        raise RuntimeError("pywin32 win32gui 모듈이 필요합니다.")
    if not winapi.win32gui.IsWindow(hwnd):
        raise RuntimeError(f"유효하지 않은 HWND입니다: {hwnd}")

    lparam = _make_mouse_lparam(x, y)
    if use_send_message:
        winapi.win32gui.SendMessage(hwnd, message, wparam, lparam)
    else:
        winapi.win32gui.PostMessage(hwnd, message, wparam, lparam)
    return True

def _is_child_or_same(parent_hwnd: int, child_hwnd: int) -> bool:
    """child_hwnd가 parent_hwnd 자신이거나 그 자식 창인지 확인합니다."""

    if int(parent_hwnd) == int(child_hwnd):
        return True
    if winapi.win32gui is None:
        return False
    try:
        return bool(winapi.win32gui.IsChild(int(parent_hwnd), int(child_hwnd)))
    except Exception:
        return False


def _get_thread_focus_hwnd(hwnd: int) -> Optional[int]:
    """대상 창 스레드의 포커스 HWND를 가져옵니다."""

    if winapi.win32gui is None or not hasattr(ctypes, "windll"):
        return None

    try:
        from ctypes import wintypes

        class GUITHREADINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND),
                ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT),
            ]

        _gwtpi = ctypes.windll.user32.GetWindowThreadProcessId
        _gwtpi.restype = wintypes.DWORD
        _gwtpi.argtypes = [wintypes.HWND, wintypes.LPDWORD]
        thread_id = _gwtpi(wintypes.HWND(int(hwnd)), None)
        if not thread_id:
            return None

        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if not ctypes.windll.user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            return None

        focus_hwnd = int(info.hwndFocus or 0)
        if focus_hwnd and winapi.win32gui.IsWindow(focus_hwnd) and _is_child_or_same(hwnd, focus_hwnd):
            return focus_hwnd
    except Exception:
        return None

    return None


def _get_child_windows(hwnd: int, limit: int = 80) -> list[int]:
    """대상 창의 자식 HWND 목록을 가져옵니다."""

    if winapi.win32gui is None:
        return []

    children: list[int] = []

    def enum_child(child_hwnd: int, _extra: object) -> bool:
        if len(children) >= limit:
            return False
        try:
            if winapi.win32gui.IsWindow(child_hwnd):
                children.append(int(child_hwnd))
        except Exception:
            pass
        return True

    try:
        winapi.win32gui.EnumChildWindows(int(hwnd), enum_child, None)
    except Exception:
        return children

    return children


def _unique_hwnds(hwnds: list[int]) -> list[int]:
    """순서를 유지하면서 HWND 중복을 제거합니다."""

    seen: set[int] = set()
    result: list[int] = []
    for hwnd in hwnds:
        hwnd = int(hwnd)
        if hwnd in seen:
            continue
        seen.add(hwnd)
        result.append(hwnd)
    return result

def _resolve_win32_message(message: object) -> int:
    """WM_COMMAND, BM_CLICK 같은 메시지 이름 또는 숫자를 int 메시지로 변환합니다."""

    if not isinstance(message, str):
        parsed = _parse_optional_int(message, "message")
        if parsed is not None:
            return parsed

    if not isinstance(message, str) or not message.strip():
        raise ValueError("message 값이 비어 있습니다.")

    constants = winapi._require_win32con()
    message_name = message.strip().upper()
    try:
        return int(message_name, 0)
    except ValueError:
        pass

    if hasattr(constants, message_name):
        return int(getattr(constants, message_name))

    aliases = {
        "BM_CLICK": 0x00F5,
        "WM_COMMAND": 0x0111,
        "WM_CLOSE": 0x0010,
        "WM_SETTEXT": 0x000C,
    }
    if message_name in aliases:
        return aliases[message_name]

    raise ValueError(f"지원하지 않는 Win32 메시지 이름입니다: {message!r}")


def _make_command_wparam(command_id: int, notify_code: int = 0) -> int:
    """WM_COMMAND wParam을 만듭니다."""

    if winapi.win32api is not None and hasattr(winapi.win32api, "MAKELONG"):
        return int(winapi.win32api.MAKELONG(int(command_id), int(notify_code)))
    return ((int(notify_code) & 0xFFFF) << 16) | (int(command_id) & 0xFFFF)

def _get_window_text_safe(hwnd: int) -> str:
    """창 텍스트를 안전하게 가져옵니다."""

    if winapi.win32gui is None:
        return ""
    try:
        return winapi.win32gui.GetWindowText(int(hwnd)) or ""
    except Exception:
        return ""


def _get_class_name_safe(hwnd: int) -> str:
    """창 클래스 이름을 안전하게 가져옵니다."""

    if winapi.win32gui is None:
        return ""
    try:
        return winapi.win32gui.GetClassName(int(hwnd)) or ""
    except Exception:
        return ""


def _get_dlg_item(hwnd: int, control_id: int) -> Optional[int]:
    """GetDlgItem으로 자식 컨트롤 HWND를 찾습니다."""

    if winapi.win32gui is None:
        return None
    try:
        child_hwnd = int(winapi.win32gui.GetDlgItem(int(hwnd), int(control_id)) or 0)
        if child_hwnd and winapi.win32gui.IsWindow(child_hwnd):
            return child_hwnd
    except Exception:
        pass

    if hasattr(ctypes, "windll"):
        try:
            child_hwnd = int(ctypes.windll.user32.GetDlgItem(int(hwnd), int(control_id)) or 0)
            if child_hwnd and winapi.win32gui.IsWindow(child_hwnd):
                return child_hwnd
        except Exception:
            pass

    return None


def _find_child_window(
    hwnd: int,
    *,
    control_id: Optional[int] = None,
    control_hwnd: Optional[int] = None,
    control_class: Optional[str] = None,
    control_text: Optional[str] = None,
) -> Optional[int]:
    """설정된 조건으로 자식 컨트롤 HWND를 찾습니다."""

    if winapi.win32gui is None:
        return None

    if control_hwnd is not None:
        try:
            candidate = int(control_hwnd)
            if winapi.win32gui.IsWindow(candidate) and _is_child_or_same(int(hwnd), candidate):
                return candidate
        except Exception:
            return None

    if control_id is not None:
        child_hwnd = _get_dlg_item(hwnd, control_id)
        if child_hwnd is not None:
            return child_hwnd

    class_filter = control_class.strip().lower() if control_class else None
    text_filter = control_text.strip().lower() if control_text else None
    if class_filter is None and text_filter is None:
        return None

    for child_hwnd in _get_child_windows(hwnd):
        class_name = _get_class_name_safe(child_hwnd).lower()
        window_text = _get_window_text_safe(child_hwnd).lower()
        if class_filter is not None and class_filter not in class_name:
            continue
        if text_filter is not None and text_filter not in window_text:
            continue
        return child_hwnd

    return None


def _send_win32_message(
    hwnd: int,
    message: int,
    wparam: int = 0,
    lparam: int = 0,
    *,
    use_send_message: bool = True,
) -> bool:
    """PostMessage 또는 SendMessage로 일반 Win32 메시지를 보냅니다."""

    if winapi.win32gui is None:
        raise RuntimeError("pywin32 win32gui 모듈이 필요합니다.")
    if not winapi.win32gui.IsWindow(int(hwnd)):
        raise RuntimeError(f"유효하지 않은 HWND입니다: {hwnd}")

    if use_send_message:
        winapi.win32gui.SendMessage(int(hwnd), int(message), int(wparam), int(lparam))
    else:
        winapi.win32gui.PostMessage(int(hwnd), int(message), int(wparam), int(lparam))
    return True

def post_mouse_move(
    hwnd: int,
    x: int,
    y: int,
    *,
    use_send_message: bool = False,
) -> bool:
    """대상 창의 클라이언트 영역 좌표로 WM_MOUSEMOVE를 보냅니다."""

    constants = winapi._require_win32con()
    return _send_mouse_message(
        hwnd,
        constants.WM_MOUSEMOVE,
        0,
        x,
        y,
        use_send_message=use_send_message,
    )


def post_mouse_down(
    hwnd: int,
    x: int,
    y: int,
    *,
    use_send_message: bool = False,
) -> bool:
    """대상 창의 클라이언트 영역 좌표로 마우스 왼쪽 버튼 Down을 보냅니다."""

    constants = winapi._require_win32con()
    return _send_mouse_message(
        hwnd,
        constants.WM_LBUTTONDOWN,
        constants.MK_LBUTTON,
        x,
        y,
        use_send_message=use_send_message,
    )


def post_mouse_up(
    hwnd: int,
    x: int,
    y: int,
    *,
    use_send_message: bool = False,
) -> bool:
    """대상 창의 클라이언트 영역 좌표로 마우스 왼쪽 버튼 Up을 보냅니다."""

    constants = winapi._require_win32con()
    return _send_mouse_message(
        hwnd,
        constants.WM_LBUTTONUP,
        0,
        x,
        y,
        use_send_message=use_send_message,
    )


def post_mouse_click(
    hwnd: int,
    x: int,
    y: int,
    *,
    use_send_message: bool = False,
    hover_delay: float = MOUSE_HOVER_BEFORE_CLICK_SECONDS,
    down_up_delay: float = CLICK_MESSAGE_DELAY_SECONDS,
) -> bool:
    """대상 HWND의 클라이언트 좌표를 왼쪽 클릭합니다."""

    post_mouse_move(hwnd, x, y, use_send_message=use_send_message)
    if hover_delay > 0:
        time.sleep(hover_delay)

    post_mouse_down(hwnd, x, y, use_send_message=use_send_message)
    if down_up_delay > 0:
        time.sleep(down_up_delay)
    post_mouse_up(hwnd, x, y, use_send_message=use_send_message)
    return True
