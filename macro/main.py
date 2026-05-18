"""
Windows inactive-window image matching and click GUI example.

목적:
- tkinter UI에서 대상 창 제목을 입력하고 자동화를 시작/중지합니다.
- Windows Graphics Capture로 대상 창이 다른 창 뒤에 가려져 있어도 캡처합니다.
- targets.json에 설정한 이미지들을 OpenCV 템플릿 매칭으로 찾습니다.
- 실제 마우스 커서를 움직이지 않고 PostMessage로 클릭 메시지를 보냅니다.
주의:
- 최소화된 창은 지원하지 않습니다.
- 일부 프로그램은 Windows Graphics Capture 또는 PostMessage 클릭을 지원하지 않을 수 있습니다.
- 허가된 사내 프로그램, 개인 학습, RPA 테스트 목적의 예시 코드입니다.
"""

from __future__ import annotations

import ctypes
import json
import platform
import queue
import random
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import scrolledtext
from typing import Any, Callable, Optional

import cv2
import numpy as np

try:
    import pyautogui
except ImportError as exc:
    pyautogui = None
    PYAUTOGUI_IMPORT_ERROR = exc
else:
    PYAUTOGUI_IMPORT_ERROR = None
    pyautogui.FAILSAFE = True

try:
    # pywin32는 Windows 전용 패키지입니다.
    # Mac 개발 환경에서는 설치되지 않는 것이 정상이라 Pylance 경고만 무시합니다.
    import win32api  # type: ignore[reportMissingModuleSource]
    import win32con  # type: ignore[reportMissingModuleSource]
    import win32gui  # type: ignore[reportMissingModuleSource]
except ImportError as exc:
    win32api = None
    win32con = None
    win32gui = None
    WIN32_IMPORT_ERROR = exc
else:
    WIN32_IMPORT_ERROR = None

try:
    # windows-capture는 Windows Graphics Capture API를 감싼 Windows 전용 패키지입니다.
    from windows_capture import Frame, WindowsCapture  # type: ignore[reportMissingModuleSource]
except (ImportError, OSError) as exc:
    Frame = Any
    WindowsCapture = None
    WINDOWS_CAPTURE_IMPORT_ERROR = exc
else:
    WINDOWS_CAPTURE_IMPORT_ERROR = None


# UI 입력칸의 기본값입니다. 사용자는 프로그램 실행 후 UI에서 수정할 수 있습니다.
# 예: "메모장", "Notepad", "계산기", "Chrome"
WINDOW_TITLE = "대상 창 제목 일부를 입력하세요"

# 아무것도 발견되지 않았을 때 CPU 과부하를 막기 위한 기본 대기 시간입니다.
LOOP_SLEEP_SECONDS = 0.5

# 대상 창을 찾지 못했을 때 재검색하는 간격입니다.
WINDOW_RETRY_SECONDS = 2.0

# WGC 세션 시작 뒤 첫 프레임을 기다리는 최대 시간입니다.
WGC_FIRST_FRAME_TIMEOUT_SECONDS = 2.0

# 매칭 영역 중심 주변에서 클릭 좌표를 약간 조정합니다.
# 허가된 UI 테스트에서 고정 좌표 취약성을 줄이기 위한 안정화 값입니다.
CLICK_JITTER_PIXELS = 3

# WM_LBUTTONDOWN과 WM_LBUTTONUP 사이의 짧은 지연입니다.
CLICK_MESSAGE_DELAY_SECONDS = 0.05

# 마우스를 대상 위치에 올린 뒤 클릭하기 전 기다리는 시간입니다.
MOUSE_HOVER_BEFORE_CLICK_SECONDS = 0.5

# PostMessage 가상 마우스 이동 단계와 전체 이동 시간입니다.
CURVED_CLICK_MIN_STEPS = 30
CURVED_CLICK_MAX_STEPS = 50
CURVED_CLICK_MOVE_DURATION_SECONDS = 0.5

# DWM 확장 프레임 bounds 속성입니다.
DWMWA_EXTENDED_FRAME_BOUNDS = 9

# 화면 영역 캡처 모드의 기본 영역입니다.
DEFAULT_REGION_X = 0
DEFAULT_REGION_Y = 0
DEFAULT_REGION_WIDTH = 1280
DEFAULT_REGION_HEIGHT = 720

TARGET_CONFIG_FILENAME = "targets.json"
DEFAULT_TARGET_CONFIGS: list[dict[str, object]] = [
    {"name": "target_A", "filename": "target_A.png", "action": "click"},
    {"name": "target_B", "filename": "target_B.png", "action": "click"},
    {"name": "target_C", "filename": "target_C.png", "action": "click"},
    {"name": "target_D", "filename": "target_D.png", "action": "click"},
    {
        "name": "target_E",
        "filename": "target_E.png",
        "action": "key",
        "key": "s",
        "key_mode": "sendmessage",
    },
    {
        "name": "target_F",
        "filename": "target_F.png",
        "action": "key",
        "key": "esc",
        "key_mode": "sendmessage",
    },
]

LogCallback = Callable[[str], None]


def _make_mouse_lparam(x: int, y: int) -> int:
    """Windows 마우스 메시지 lParam을 클라이언트 좌표로 만듭니다."""

    x = int(x)
    y = int(y)
    if win32api is not None and hasattr(win32api, "MAKELONG"):
        return int(win32api.MAKELONG(x, y))

    return (y & 0xFFFF) << 16 | (x & 0xFFFF)


def _require_win32con() -> Any:
    """Windows 메시지 상수를 쓰기 전에 win32con 로드를 확인합니다."""

    if win32con is None:
        raise RuntimeError("pywin32 win32con 모듈이 필요합니다.")
    return win32con


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

    if win32gui is None:
        raise RuntimeError("pywin32 win32gui 모듈이 필요합니다.")
    if not win32gui.IsWindow(hwnd):
        raise RuntimeError(f"유효하지 않은 HWND입니다: {hwnd}")

    lparam = _make_mouse_lparam(x, y)
    if use_send_message:
        win32gui.SendMessage(hwnd, message, wparam, lparam)
    else:
        win32gui.PostMessage(hwnd, message, wparam, lparam)
    return True


def _make_key_lparam(vk_code: int, *, key_up: bool = False) -> int:
    """Windows 키 메시지 lParam을 만듭니다."""

    scan_code = 0
    if win32api is not None and hasattr(win32api, "MapVirtualKey"):
        scan_code = int(win32api.MapVirtualKey(int(vk_code), 0))

    lparam = 1 | (scan_code << 16)
    if key_up:
        lparam |= (1 << 30) | (1 << 31)
    return int(lparam)


def _virtual_key_from_key(key: str) -> int:
    """키 이름을 Windows virtual-key 코드로 변환합니다."""

    normalized = key.strip().lower()
    special_keys = {
        "esc": 0x1B,
        "escape": 0x1B,
    }
    if normalized in special_keys:
        return special_keys[normalized]

    if len(normalized) != 1:
        raise ValueError(f"지원하지 않는 키입니다: {key!r}")
    return ord(normalized.upper())


def _char_code_from_key(key: str) -> Optional[int]:
    """필요할 때 함께 보낼 WM_CHAR wParam 값을 반환합니다."""

    normalized = key.strip().lower()
    if normalized in ("esc", "escape"):
        return 0x1B
    return None


def _is_child_or_same(parent_hwnd: int, child_hwnd: int) -> bool:
    """child_hwnd가 parent_hwnd 자신이거나 그 자식 창인지 확인합니다."""

    if int(parent_hwnd) == int(child_hwnd):
        return True
    if win32gui is None:
        return False
    try:
        return bool(win32gui.IsChild(int(parent_hwnd), int(child_hwnd)))
    except Exception:
        return False


def _get_thread_focus_hwnd(hwnd: int) -> Optional[int]:
    """대상 창 스레드의 포커스 HWND를 가져옵니다."""

    if win32gui is None or not hasattr(ctypes, "windll"):
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

        thread_id = ctypes.windll.user32.GetWindowThreadProcessId(
            wintypes.HWND(int(hwnd)),
            None,
        )
        if not thread_id:
            return None

        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if not ctypes.windll.user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            return None

        focus_hwnd = int(info.hwndFocus or 0)
        if focus_hwnd and win32gui.IsWindow(focus_hwnd) and _is_child_or_same(hwnd, focus_hwnd):
            return focus_hwnd
    except Exception:
        return None

    return None


def _get_keyboard_target_hwnd(hwnd: int) -> int:
    """키 메시지를 보낼 대상 HWND를 고릅니다."""

    focus_hwnd = _get_thread_focus_hwnd(hwnd)
    if focus_hwnd is not None:
        return focus_hwnd
    return int(hwnd)


def _send_keyboard_message(
    hwnd: int,
    message: int,
    vk_code: int,
    lparam: int,
    *,
    use_send_message: bool = False,
) -> bool:
    """PostMessage 또는 SendMessage로 대상 HWND에 키보드 메시지를 보냅니다."""

    if win32gui is None:
        raise RuntimeError("pywin32 win32gui 모듈이 필요합니다.")
    if not win32gui.IsWindow(hwnd):
        raise RuntimeError(f"유효하지 않은 HWND입니다: {hwnd}")

    if use_send_message:
        win32gui.SendMessage(hwnd, message, int(vk_code), int(lparam))
    else:
        win32gui.PostMessage(hwnd, message, int(vk_code), int(lparam))
    return True


def post_key_down(
    hwnd: int,
    key: str,
    *,
    use_send_message: bool = False,
) -> bool:
    """대상 창에 WM_KEYDOWN을 보냅니다."""

    constants = _require_win32con()
    vk_code = _virtual_key_from_key(key)
    return _send_keyboard_message(
        hwnd,
        constants.WM_KEYDOWN,
        vk_code,
        _make_key_lparam(vk_code, key_up=False),
        use_send_message=use_send_message,
    )


def post_key_up(
    hwnd: int,
    key: str,
    *,
    use_send_message: bool = False,
) -> bool:
    """대상 창에 WM_KEYUP을 보냅니다."""

    constants = _require_win32con()
    vk_code = _virtual_key_from_key(key)
    return _send_keyboard_message(
        hwnd,
        constants.WM_KEYUP,
        vk_code,
        _make_key_lparam(vk_code, key_up=True),
        use_send_message=use_send_message,
    )


def post_key_char(
    hwnd: int,
    key: str,
    *,
    use_send_message: bool = False,
) -> bool:
    """대상 창에 필요한 경우 WM_CHAR를 보냅니다."""

    char_code = _char_code_from_key(key)
    if char_code is None:
        return False

    constants = _require_win32con()
    vk_code = _virtual_key_from_key(key)
    return _send_keyboard_message(
        hwnd,
        constants.WM_CHAR,
        char_code,
        _make_key_lparam(vk_code, key_up=False),
        use_send_message=use_send_message,
    )


def post_key_press(
    hwnd: int,
    key: str,
    *,
    press_delay: float = CLICK_MESSAGE_DELAY_SECONDS,
    use_send_message: bool = False,
    logger: Optional[LogCallback] = None,
) -> bool:
    """실제 키보드 입력 없이 대상 HWND에 키 Down/Up 메시지를 보냅니다."""

    log = logger or print
    target_hwnd = _get_keyboard_target_hwnd(hwnd)
    if target_hwnd != int(hwnd):
        log(f"[키 대상] top_hwnd={hwnd}, focus_hwnd={target_hwnd}")

    post_key_down(target_hwnd, key, use_send_message=use_send_message)
    post_key_char(target_hwnd, key, use_send_message=use_send_message)
    if press_delay > 0:
        time.sleep(press_delay)
    post_key_up(target_hwnd, key, use_send_message=use_send_message)
    return True


def post_mouse_move(
    hwnd: int,
    x: int,
    y: int,
    *,
    use_send_message: bool = False,
) -> bool:
    """대상 창의 클라이언트 영역 좌표로 WM_MOUSEMOVE를 보냅니다."""

    constants = _require_win32con()
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

    constants = _require_win32con()
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

    constants = _require_win32con()
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


def get_bezier_point(
    p1: tuple[int, int],
    p2: tuple[int, int],
    p3: tuple[int, int],
    t: float,
) -> tuple[int, int]:
    """2차 베지에 곡선 위의 한 점을 계산합니다."""

    t = max(0.0, min(1.0, float(t)))
    one_minus_t = 1.0 - t
    x = one_minus_t * one_minus_t * p1[0] + 2 * one_minus_t * t * p2[0] + t * t * p3[0]
    y = one_minus_t * one_minus_t * p1[1] + 2 * one_minus_t * t * p2[1] + t * t * p3[1]
    return int(round(x)), int(round(y))


def _build_bezier_control_point(
    start_pos: tuple[int, int],
    end_pos: tuple[int, int],
) -> tuple[int, int]:
    """출발점과 목적지 사이에 랜덤한 휨을 가진 제어점을 만듭니다."""

    start_x, start_y = start_pos
    end_x, end_y = end_pos
    dx = end_x - start_x
    dy = end_y - start_y
    distance = max(1.0, (dx * dx + dy * dy) ** 0.5)

    mid_x = (start_x + end_x) / 2.0
    mid_y = (start_y + end_y) / 2.0
    perpendicular_x = -dy / distance
    perpendicular_y = dx / distance

    bend = random.uniform(-0.35, 0.35) * distance
    if abs(bend) < 12.0 and distance >= 24.0:
        bend = 12.0 if bend >= 0 else -12.0

    control_x = mid_x + perpendicular_x * bend + random.uniform(-8.0, 8.0)
    control_y = mid_y + perpendicular_y * bend + random.uniform(-8.0, 8.0)
    return int(round(control_x)), int(round(control_y))


def _clamp_client_point(
    point: tuple[int, int],
    client_width: int,
    client_height: int,
) -> tuple[int, int]:
    """좌표가 클라이언트 영역 밖으로 나가지 않게 보정합니다."""

    max_x = max(0, int(client_width) - 1)
    max_y = max(0, int(client_height) - 1)
    x = max(0, min(max_x, int(point[0])))
    y = max(0, min(max_y, int(point[1])))
    return x, y


def post_curved_click(
    hwnd: int,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    *,
    steps: Optional[int] = None,
    move_duration: float = CURVED_CLICK_MOVE_DURATION_SECONDS,
    down_up_delay: float = CLICK_MESSAGE_DELAY_SECONDS,
    use_send_message: bool = False,
    logger: Optional[LogCallback] = None,
) -> bool:
    """클라이언트 좌표 기준으로 베지에 곡선 이동 후 왼쪽 클릭 메시지를 보냅니다."""

    log = logger or print

    if win32gui is None:
        raise RuntimeError("pywin32 win32gui 모듈이 필요합니다.")
    if not win32gui.IsWindow(hwnd):
        raise RuntimeError(f"유효하지 않은 HWND입니다: {hwnd}")

    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    client_width = int(right - left)
    client_height = int(bottom - top)
    if client_width <= 0 or client_height <= 0:
        raise RuntimeError("대상 창의 클라이언트 영역 크기가 0입니다.")

    if steps is None:
        steps = random.randint(CURVED_CLICK_MIN_STEPS, CURVED_CLICK_MAX_STEPS)
    steps = max(2, int(steps))
    step_delay = max(0.0, float(move_duration)) / max(1, steps - 1)

    start_pos = _clamp_client_point((start_x, start_y), client_width, client_height)
    end_pos = _clamp_client_point((end_x, end_y), client_width, client_height)
    control_pos = _clamp_client_point(
        _build_bezier_control_point(start_pos, end_pos),
        client_width,
        client_height,
    )

    log(
        f"[곡선 시작] start={start_pos}, control={control_pos}, "
        f"end={end_pos}, steps={steps}, duration={move_duration:.3f}s, "
        f"step_delay={step_delay:.3f}s"
    )

    for index in range(steps):
        t = index / (steps - 1)
        move_x, move_y = _clamp_client_point(
            get_bezier_point(start_pos, control_pos, end_pos, t),
            client_width,
            client_height,
        )
        log(f"[곡선 이동] {index + 1}/{steps} client=({move_x}, {move_y})")
        post_mouse_move(
            hwnd,
            move_x,
            move_y,
            use_send_message=use_send_message,
        )
        if index < steps - 1 and step_delay > 0:
            time.sleep(step_delay)

    final_x, final_y = end_pos
    post_mouse_down(hwnd, final_x, final_y, use_send_message=use_send_message)
    if down_up_delay > 0:
        time.sleep(down_up_delay)
    post_mouse_up(hwnd, final_x, final_y, use_send_message=use_send_message)
    log(f"[곡선 클릭 완료] client=({final_x}, {final_y})")
    return True


def _same_size(
    first: tuple[int, int],
    second: tuple[int, int],
    tolerance: int = 2,
) -> bool:
    """DPI 반올림 차이를 감안해 두 크기가 거의 같은지 확인합니다."""

    return (
        abs(int(first[0]) - int(second[0])) <= tolerance
        and abs(int(first[1]) - int(second[1])) <= tolerance
    )


def _get_extended_frame_bounds(hwnd: int) -> Optional[tuple[int, int, int, int]]:
    """DWM 확장 프레임 bounds를 반환합니다. 실패하면 None을 반환합니다."""

    if not hasattr(ctypes, "windll"):
        return None

    try:
        from ctypes import wintypes

        rect = wintypes.RECT()
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(int(hwnd)),
            ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if result != 0:
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:
        return None


def _get_wgc_capture_origin(
    hwnd: int,
    frame_size: Optional[tuple[int, int]],
) -> Optional[tuple[int, int]]:
    """WGC 프레임의 화면 기준 좌상단 원점을 추정합니다."""

    if win32gui is None:
        return None

    extended_frame = _get_extended_frame_bounds(hwnd)
    window_rect = win32gui.GetWindowRect(hwnd)

    if frame_size is not None:
        if extended_frame is not None:
            ext_left, ext_top, ext_right, ext_bottom = extended_frame
            ext_size = (int(ext_right - ext_left), int(ext_bottom - ext_top))
            if _same_size(frame_size, ext_size):
                return int(ext_left), int(ext_top)

        win_left, win_top, win_right, win_bottom = window_rect
        window_size = (int(win_right - win_left), int(win_bottom - win_top))
        if _same_size(frame_size, window_size):
            return int(win_left), int(win_top)

    if extended_frame is not None:
        return int(extended_frame[0]), int(extended_frame[1])

    return int(window_rect[0]), int(window_rect[1])


def wgc_to_client(
    hwnd: int,
    x: int,
    y: int,
    frame_size: Optional[tuple[int, int]],
    logger: Optional[LogCallback] = None,
) -> Optional[tuple[int, int]]:
    """
    WGC 캡처 프레임 좌표를 대상 창 Client Area 기준 좌표로 변환합니다.

    WGC 프레임이 클라이언트 영역과 같은 크기면 x, y를 그대로 사용합니다.
    전체 창이나 확장 프레임 기준 캡처이면 캡처 원점과 ClientToScreen(0, 0)의
    차이를 반영해 PostMessage가 요구하는 클라이언트 좌표로 보정합니다.
    """

    log = logger or print

    if win32gui is None:
        log("[좌표 오류] pywin32 win32gui 모듈이 필요합니다.")
        return None
    if not win32gui.IsWindow(hwnd):
        log(f"[좌표 오류] 유효하지 않은 HWND입니다: {hwnd}")
        return None

    x = int(x)
    y = int(y)

    try:
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        client_width = int(right - left)
        client_height = int(bottom - top)
        if client_width <= 0 or client_height <= 0:
            log("[좌표 오류] 대상 창의 클라이언트 영역 크기가 0입니다.")
            return None

        if frame_size is None or _same_size(frame_size, (client_width, client_height)):
            client_x, client_y = x, y
        else:
            client_screen_x, client_screen_y = win32gui.ClientToScreen(hwnd, (0, 0))
            capture_origin = _get_wgc_capture_origin(hwnd, frame_size)
            if capture_origin is None:
                log("[좌표 안내] WGC 캡처 원점을 확인하지 못해 좌표를 클라이언트 기준으로 사용합니다.")
                client_x, client_y = x, y
            else:
                origin_x, origin_y = capture_origin
                client_x = int(round(x + origin_x - client_screen_x))
                client_y = int(round(y + origin_y - client_screen_y))

        if not (0 <= client_x < client_width and 0 <= client_y < client_height):
            log(
                f"[좌표 경고] WGC=({x}, {y}) -> client=({client_x}, {client_y})가 "
                f"클라이언트 영역 {client_width}x{client_height} 밖입니다."
            )
            return None

        if (client_x, client_y) != (x, y):
            log(f"[좌표 보정] WGC=({x}, {y}) -> client=({client_x}, {client_y})")
        return client_x, client_y
    except Exception as exc:
        log(f"[좌표 오류] WGC 좌표를 클라이언트 좌표로 변환하지 못했습니다: {exc}")
        return None


@dataclass
class TargetImage:
    """탐지할 이미지 정보입니다."""

    name: str
    filename: str
    wait_after_click: float
    threshold: float = 0.8
    action: str = "click"
    key: Optional[str] = None
    key_mode: str = "sendmessage"

    # load_targets()에서 GrayScale 이미지가 채워집니다.
    # repr=False로 두면 로그에 큰 NumPy 배열 내용이 출력되지 않습니다.
    image_gray: Optional[np.ndarray] = field(default=None, repr=False)


class WGCCaptureEngine:
    """windows-capture 기반 비동기 WGC 창 캡처 엔진입니다."""

    def __init__(self, hwnd: int, logger: Optional[LogCallback] = None):
        self.hwnd = int(hwnd)
        self.logger = logger or print
        self.latest_frame: Optional[np.ndarray] = None
        self.capture: Optional[Any] = None
        self.capture_control: Optional[Any] = None
        self.frame_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)
        self.frame_lock = threading.Lock()
        self.first_frame_event = threading.Event()
        self.closed_event = threading.Event()
        self.started = False
        self.logged_first_frame = False

    def log(self, message: str) -> None:
        """콘솔 또는 GUI 로그 영역으로 메시지를 보냅니다."""

        self.logger(message)

    def on_frame_arrived(self, frame: Frame, _capture_control: Any = None) -> None:
        """WGC 캡처 스레드에서 프레임이 도착할 때마다 최신 BGRA 배열을 저장합니다."""

        try:
            # windows-capture 2.x의 Frame.frame_buffer는 BGRA uint8 NumPy 배열입니다.
            image_bgra = np.asarray(frame.frame_buffer).copy()
            if image_bgra.size == 0:
                return

            with self.frame_lock:
                self.latest_frame = image_bgra

            try:
                if self.frame_queue.full():
                    self.frame_queue.get_nowait()
                self.frame_queue.put_nowait(image_bgra)
            except queue.Empty:
                pass
            except queue.Full:
                pass

            self.first_frame_event.set()
            if not self.logged_first_frame:
                self.logged_first_frame = True
                self.log(
                    f"[캡처 수신] WGC 첫 프레임 "
                    f"{int(frame.width)}x{int(frame.height)}"
                )
        except Exception as exc:
            self.log(f"[캡처 오류] WGC 프레임 처리 중 문제가 발생했습니다: {exc}")

    def on_closed(self) -> None:
        """WGC 캡처 세션이 닫혔을 때 호출됩니다."""

        self.closed_event.set()
        self.log("[캡처 종료] WGC 캡처 세션이 닫혔습니다.")

    def start_capture(self) -> bool:
        """WindowsCapture를 블로킹 없이 전용 캡처 스레드에서 시작합니다."""

        if WindowsCapture is None:
            self.log("[오류] windows-capture 모듈을 불러올 수 없습니다.")
            self.log("       Windows 환경에서 requirements.txt를 다시 설치하세요.")
            self.log(f"       원본 오류: {WINDOWS_CAPTURE_IMPORT_ERROR}")
            return False

        if self.capture_control is not None:
            try:
                if not self.capture_control.is_finished():
                    return True
            except Exception:
                self.stop_capture()

        self.first_frame_event.clear()
        self.closed_event.clear()
        self.logged_first_frame = False

        with self.frame_lock:
            self.latest_frame = None

        while True:
            try:
                self.frame_queue.get_nowait()
            except queue.Empty:
                break

        try:
            self.capture = WindowsCapture(
                cursor_capture=False,
                draw_border=False,
                monitor_index=None,
                window_name=None,
                window_hwnd=self.hwnd,
            )
            self.capture.event(self.on_frame_arrived)
            self.capture.event(self.on_closed)
            self.capture_control = self.capture.start_free_threaded()
            self.started = True
            self.log(f"[캡처 시작] WGC 세션을 시작했습니다. HWND={self.hwnd}")
            return True
        except Exception as exc:
            self.log(f"[캡처 오류] WGC 캡처 세션을 시작하지 못했습니다: {exc}")
            self.capture = None
            self.capture_control = None
            self.started = False
            return False

    def get_latest_frame(self, timeout: float = 0.0) -> Optional[np.ndarray]:
        """최신 WGC 프레임을 BGRA NumPy 배열 복사본으로 반환합니다."""

        if timeout > 0 and not self.first_frame_event.wait(timeout):
            return None

        latest_from_queue: Optional[np.ndarray] = None
        while True:
            try:
                latest_from_queue = self.frame_queue.get_nowait()
            except queue.Empty:
                break

        if latest_from_queue is not None:
            with self.frame_lock:
                self.latest_frame = latest_from_queue

        with self.frame_lock:
            if self.latest_frame is None:
                return None
            return self.latest_frame.copy()

    def get_frame_size(self) -> Optional[tuple[int, int]]:
        """최신 WGC 프레임의 (width, height)를 반환합니다."""

        with self.frame_lock:
            if self.latest_frame is None:
                return None
            height, width = self.latest_frame.shape[:2]
            return int(width), int(height)

    def stop_capture(self) -> None:
        """실행 중인 WGC 세션을 안전하게 중지합니다."""

        capture_control = self.capture_control
        self.capture_control = None
        self.capture = None
        self.started = False
        self.first_frame_event.clear()

        with self.frame_lock:
            self.latest_frame = None

        if capture_control is None:
            return

        try:
            if not capture_control.is_finished():
                capture_control.stop()
        except Exception as exc:
            self.log(f"[주의] WGC 캡처 세션 정리 중 문제가 발생했습니다: {exc}")


class InactiveManager:
    """비활성 Windows 창 WGC 캡처와 메시지 클릭을 담당합니다."""

    def __init__(self, window_title: str, logger: Optional[LogCallback] = None):
        self.window_title = window_title
        self.hwnd: Optional[int] = None
        self.window_text: str = ""
        self.logger = logger or print
        self.capture_engine: Optional[WGCCaptureEngine] = None
        self.last_capture_wait_log_time = 0.0
        self.virtual_mouse_wgc_pos: Optional[tuple[int, int]] = None
        self.virtual_mouse_client_pos: Optional[tuple[int, int]] = None

        if WIN32_IMPORT_ERROR is not None:
            self.log("[오류] pywin32 모듈을 불러올 수 없습니다.")
            self.log("       이 프로그램은 Windows + pywin32 환경에서 실행해야 합니다.")
            self.log(f"       원본 오류: {WIN32_IMPORT_ERROR}")

        if WINDOWS_CAPTURE_IMPORT_ERROR is not None:
            self.log("[오류] windows-capture 모듈을 불러올 수 없습니다.")
            self.log("       WGC 캡처에는 Windows + windows-capture 환경이 필요합니다.")
            self.log(f"       원본 오류: {WINDOWS_CAPTURE_IMPORT_ERROR}")

        if platform.system() != "Windows":
            self.log("[주의] 현재 OS는 Windows가 아닙니다.")
            self.log("       코드는 작성/검토할 수 있지만 실제 캡처와 클릭은 Windows에서 실행하세요.")

    def log(self, message: str) -> None:
        """콘솔 또는 GUI 로그 영역으로 메시지를 보냅니다."""

        self.logger(message)

    def find_window(self) -> bool:
        """
        WINDOW_TITLE 문자열이 제목에 포함된 창을 찾습니다.

        정확히 일치하지 않아도 되도록 부분 문자열로 검색합니다.
        같은 제목의 창이 여러 개 있을 수 있으므로 모든 후보 HWND와 제목을 출력합니다.
        """

        if not self._win32_ready():
            return False

        keyword = self.window_title.strip().lower()
        if not keyword:
            self.log("[오류] 대상 창 제목이 비어 있습니다.")
            self.log("       UI의 대상 창 제목 입력칸에 찾을 창 제목 일부를 입력하세요.")
            return False

        matches: list[tuple[int, str]] = []
        minimized_matches: list[tuple[int, str]] = []

        def enum_handler(hwnd: int, _extra: object) -> None:
            if not win32gui.IsWindow(hwnd):
                return
            if not win32gui.IsWindowVisible(hwnd):
                return

            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return
            if keyword not in title.lower():
                return

            if win32gui.IsIconic(hwnd):
                minimized_matches.append((hwnd, title))
                return

            matches.append((hwnd, title))

        try:
            win32gui.EnumWindows(enum_handler, None)
        except Exception as exc:
            self.log(f"[오류] 창 검색 중 문제가 발생했습니다: {exc}")
            return False

        self.log(f"[창 검색] 제목에 '{self.window_title}' 포함된 창을 검색했습니다.")

        if minimized_matches:
            self.log("[제외] 최소화된 창은 지원하지 않아 제외했습니다.")
            for hwnd, title in minimized_matches:
                self.log(f"       HWND={hwnd}, 제목='{title}'")

        if not matches:
            self.log("[안내] 사용 가능한 대상 창을 찾지 못했습니다.")
            self.log("       창 제목을 확인하거나, 대상 창이 최소화되어 있지 않은지 확인하세요.")
            return False

        self.log("[발견] 사용 가능한 창 후보:")
        for hwnd, title in matches:
            self.log(f"       HWND={hwnd}, 제목='{title}'")

        previous_hwnd = self.hwnd
        self.hwnd, self.window_text = matches[0]
        if previous_hwnd is not None and previous_hwnd != self.hwnd:
            self.stop_capture()
            self.virtual_mouse_wgc_pos = None
            self.virtual_mouse_client_pos = None
        self.log(f"[선택] HWND={self.hwnd}, 제목='{self.window_text}' 창을 사용합니다.")
        return True

    def is_valid_window(self) -> bool:
        """현재 저장된 HWND가 캡처 가능한 상태인지 확인합니다."""

        if not self._win32_ready():
            return False
        if self.hwnd is None:
            return False

        try:
            if not win32gui.IsWindow(self.hwnd):
                self.log("[주의] 대상 HWND가 더 이상 유효하지 않습니다.")
                return False
            if not win32gui.IsWindowVisible(self.hwnd):
                self.log("[주의] 대상 창이 보이지 않는 상태입니다.")
                return False
            if win32gui.IsIconic(self.hwnd):
                self.log("[주의] 대상 창이 최소화되어 있습니다. 최소화된 창은 지원하지 않습니다.")
                return False

            left, top, right, bottom = win32gui.GetClientRect(self.hwnd)
            width = right - left
            height = bottom - top
            if width <= 0 or height <= 0:
                self.log("[주의] 대상 창의 클라이언트 영역 크기가 0입니다.")
                return False

            return True
        except Exception as exc:
            self.log(f"[주의] 대상 창 상태 확인 중 오류가 발생했습니다: {exc}")
            return False

    def capture_client_area(self) -> Optional[np.ndarray]:
        """
        WGC 최신 프레임을 가져와 OpenCV 템플릿 매칭용 GrayScale 배열로 반환합니다.

        windows-capture는 별도 프레임 스레드에서 BGRA 버퍼를 밀어주므로,
        여기서는 WGCCaptureEngine의 최신 프레임만 읽고 cv2.cvtColor로 변환합니다.
        """

        if not self.is_valid_window():
            self.stop_capture()
            return None

        if self.hwnd is None:
            self.log("[오류] WGC 캡처를 시작할 대상 HWND가 없습니다.")
            return None

        if self.capture_engine is None:
            self.capture_engine = WGCCaptureEngine(self.hwnd, logger=self.log)

        if not self.capture_engine.start_capture():
            return None

        if self.capture_engine.closed_event.is_set():
            self.log("[주의] WGC 캡처 세션이 닫혔습니다. 대상 창을 다시 찾습니다.")
            self.stop_capture()
            return None

        frame_bgra = self.capture_engine.get_latest_frame(
            timeout=WGC_FIRST_FRAME_TIMEOUT_SECONDS,
        )
        if frame_bgra is None:
            self._log_capture_wait("[대기] WGC 첫 프레임 또는 최신 프레임을 아직 받지 못했습니다.")
            return None

        if frame_bgra.ndim != 3 or frame_bgra.shape[2] < 3:
            self.log(f"[캡처 오류] 지원하지 않는 WGC 프레임 형태입니다: {frame_bgra.shape}")
            return None

        color_channels = frame_bgra[:, :, :3]
        if np.all(color_channels == 0):
            self._log_capture_wait("[캡처 오류] WGC 캡처 이미지가 완전히 검은색입니다.")
            return None

        mean_value = float(color_channels.mean())
        std_value = float(color_channels.std())
        if mean_value < 1.0 and std_value < 1.0:
            self._log_capture_wait("[캡처 오류] WGC 캡처 결과가 거의 검은 화면입니다.")
            return None

        if std_value < 0.2:
            self._log_capture_wait("[주의] WGC 캡처 결과가 거의 단색입니다.")

        try:
            if frame_bgra.shape[2] >= 4:
                return cv2.cvtColor(frame_bgra, cv2.COLOR_BGRA2GRAY)
            return cv2.cvtColor(frame_bgra, cv2.COLOR_BGR2GRAY)
        except Exception as exc:
            self.log(f"[캡처 오류] WGC 프레임을 GrayScale로 변환하지 못했습니다: {exc}")
            return None

    def stop_capture(self) -> None:
        """실행 중인 WGC 캡처 엔진을 정리합니다."""

        if self.capture_engine is None:
            return

        self.capture_engine.stop_capture()
        self.capture_engine = None

    def _log_capture_wait(self, message: str) -> None:
        """반복 루프에서 같은 캡처 메시지가 과도하게 쌓이지 않게 합니다."""

        now = time.monotonic()
        if now - self.last_capture_wait_log_time < 2.0:
            return
        self.last_capture_wait_log_time = now
        self.log(message)

    def wgc_to_client(self, x: int, y: int) -> Optional[tuple[int, int]]:
        """
        WGC 프레임 기준 좌표를 PostMessage가 요구하는 클라이언트 좌표로 변환합니다.

        WGC가 클라이언트 영역만 캡처하는 환경이면 좌표를 그대로 사용합니다.
        WGC가 제목 표시줄/테두리를 포함한 전체 창을 캡처하면, 캡처 원점과
        ClientToScreen(0, 0)의 차이를 빼서 정확한 클라이언트 좌표를 계산합니다.
        """

        if self.hwnd is None:
            return None

        frame_size = (
            self.capture_engine.get_frame_size()
            if self.capture_engine is not None
            else None
        )
        return wgc_to_client(self.hwnd, x, y, frame_size, logger=self.log)

    def get_virtual_start_position(self, fallback_x: int, fallback_y: int) -> tuple[int, int]:
        """PostMessage 곡선 이동의 WGC 기준 시작점을 반환합니다."""

        if self.virtual_mouse_wgc_pos is not None:
            return self.virtual_mouse_wgc_pos

        frame_size = (
            self.capture_engine.get_frame_size()
            if self.capture_engine is not None
            else None
        )
        if frame_size is not None:
            frame_width, frame_height = frame_size
            return max(0, frame_width // 2), max(0, frame_height // 2)

        return int(fallback_x), int(fallback_y)

    def post_mouse_down(
        self,
        x: int,
        y: int,
        *,
        use_send_message: bool = False,
    ) -> bool:
        """WGC 프레임 좌표를 보정한 뒤 왼쪽 버튼 Down 메시지를 보냅니다."""

        if not self.is_valid_window() or self.hwnd is None:
            return False

        client_point = self.wgc_to_client(x, y)
        if client_point is None:
            return False

        client_x, client_y = client_point
        try:
            self.log(f"[클릭 Down] HWND={self.hwnd}, client=({client_x}, {client_y})")
            return post_mouse_down(
                self.hwnd,
                client_x,
                client_y,
                use_send_message=use_send_message,
            )
        except Exception as exc:
            self.log(f"[오류] 마우스 Down 메시지 전송 중 문제가 발생했습니다: {exc}")
            return False

    def post_mouse_up(
        self,
        x: int,
        y: int,
        *,
        use_send_message: bool = False,
    ) -> bool:
        """WGC 프레임 좌표를 보정한 뒤 왼쪽 버튼 Up 메시지를 보냅니다."""

        if not self.is_valid_window() or self.hwnd is None:
            return False

        client_point = self.wgc_to_client(x, y)
        if client_point is None:
            return False

        client_x, client_y = client_point
        try:
            self.log(f"[클릭 Up] HWND={self.hwnd}, client=({client_x}, {client_y})")
            return post_mouse_up(
                self.hwnd,
                client_x,
                client_y,
                use_send_message=use_send_message,
            )
        except Exception as exc:
            self.log(f"[오류] 마우스 Up 메시지 전송 중 문제가 발생했습니다: {exc}")
            return False

    def post_curved_click(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        *,
        use_send_message: bool = False,
    ) -> bool:
        """
        WGC 좌표를 클라이언트 좌표로 보정한 뒤 가상 곡선 이동과 클릭 메시지를 보냅니다.

        실제 마우스 커서는 움직이지 않으며, 내부적으로 WM_MOUSEMOVE / WM_LBUTTONDOWN /
        WM_LBUTTONUP 메시지만 대상 HWND에 전송합니다.
        """

        if not self.is_valid_window() or self.hwnd is None:
            return False

        end_client = self.wgc_to_client(end_x, end_y)
        if end_client is None:
            return False

        start_client = self.wgc_to_client(start_x, start_y)
        if start_client is None:
            start_client = self.virtual_mouse_client_pos

        if start_client is None:
            try:
                left, top, right, bottom = win32gui.GetClientRect(self.hwnd)
                start_client = (
                    max(0, int(right - left) // 2),
                    max(0, int(bottom - top) // 2),
                )
                self.log(f"[곡선 시작점] 이전 가상 위치가 없어 client 중심 {start_client}에서 시작합니다.")
            except Exception:
                start_client = end_client
                self.log("[곡선 시작점] 시작점 계산 실패로 목적지에서 시작합니다.")

        try:
            self.log(
                f"[곡선 클릭 준비] HWND={self.hwnd}, "
                f"start_wgc=({int(start_x)}, {int(start_y)}), "
                f"end_wgc=({int(end_x)}, {int(end_y)}), "
                f"start_client={start_client}, end_client={end_client}"
            )
            post_curved_click(
                self.hwnd,
                start_client[0],
                start_client[1],
                end_client[0],
                end_client[1],
                use_send_message=use_send_message,
                logger=self.log,
            )
            self.virtual_mouse_wgc_pos = (int(end_x), int(end_y))
            self.virtual_mouse_client_pos = end_client
            return True
        except Exception as exc:
            self.log(f"[오류] 곡선 클릭 메시지 전송 중 문제가 발생했습니다: {exc}")
            return False

    def post_key_press(
        self,
        key: str,
        *,
        use_send_message: bool = False,
    ) -> bool:
        """대상 창 HWND에만 메시지 방식 키 입력을 보냅니다."""

        if not self.is_valid_window() or self.hwnd is None:
            return False

        try:
            mode_name = "SendMessage" if use_send_message else "PostMessage"
            self.log(
                f"[키 전송] HWND={self.hwnd}, 제목='{self.window_text}', "
                f"key='{key.upper()}', mode={mode_name}"
            )
            post_key_press(
                self.hwnd,
                key,
                use_send_message=use_send_message,
                logger=self.log,
            )
            self.log(f"[키 완료] WM_KEYDOWN / WM_KEYUP: {key.upper()}")
            return True
        except Exception as exc:
            self.log(f"[오류] 키 메시지 전송 중 문제가 발생했습니다: {exc}")
            return False

    def post_click(
        self,
        x: int,
        y: int,
        *,
        use_send_message: bool = False,
    ) -> bool:
        """
        실제 마우스 커서를 움직이지 않고 대상 창에 클릭 메시지를 보냅니다.

        좌표는 WGC 캡처 프레임 기준이며, 내부에서 클라이언트 좌표로 보정합니다.
        일부 프로그램은 보안 정책, 자체 입력 처리 방식, DirectX/게임 렌더링 구조 때문에
        PostMessage 기반 클릭을 무시할 수 있습니다.
        """

        if not self.is_valid_window() or self.hwnd is None:
            return False

        client_point = self.wgc_to_client(x, y)
        if client_point is None:
            return False

        client_x, client_y = client_point
        try:
            self.log(
                f"[클릭 전송] HWND={self.hwnd}, "
                f"WGC=({int(x)}, {int(y)}), client=({client_x}, {client_y}), "
                f"hover={MOUSE_HOVER_BEFORE_CLICK_SECONDS:.2f}s"
            )
            post_mouse_click(
                self.hwnd,
                client_x,
                client_y,
                use_send_message=use_send_message,
            )
            self.log("[클릭 완료] WM_LBUTTONDOWN / WM_LBUTTONUP 메시지를 보냈습니다.")
            return True
        except Exception as exc:
            self.log(f"[오류] 클릭 메시지 전송 중 문제가 발생했습니다: {exc}")
            return False

    def client_to_screen(self, x: int, y: int) -> Optional[tuple[int, int]]:
        """클라이언트 영역 기준 좌표를 화면 절대 좌표로 변환합니다."""

        if not self.is_valid_window():
            return None

        try:
            screen_x, screen_y = win32gui.ClientToScreen(self.hwnd, (int(x), int(y)))
            return int(screen_x), int(screen_y)
        except Exception as exc:
            self.log(f"[오류] 클라이언트 좌표를 화면 좌표로 변환하지 못했습니다: {exc}")
            return None

    def _win32_ready(self) -> bool:
        """pywin32 모듈이 정상 로드되었는지 확인합니다."""

        return WIN32_IMPORT_ERROR is None

    def _make_lparam(self, x: int, y: int) -> int:
        """
        Windows 마우스 메시지 lParam을 만듭니다.

        하위 16비트는 x 좌표, 상위 16비트는 y 좌표입니다.
        win32api.MAKELONG이 있으면 사용하고, 없으면 동일한 방식으로 직접 구성합니다.
        """

        return _make_mouse_lparam(x, y)


class AutomationApp:
    """tkinter UI와 자동화 스레드를 관리합니다."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("비활성 창 이미지 자동화 테스트")
        self.root.geometry("980x760+80+80")
        self.root.minsize(900, 640)
        self.ui_preview_only = platform.system() != "Windows"
        if self.ui_preview_only:
            self.root.title("비활성 창 이미지 자동화 테스트 - UI 미리보기")

        self.window_title_var = tk.StringVar(value=WINDOW_TITLE)
        initial_status = "UI 미리보기" if self.ui_preview_only else "대기 중"
        self.status_var = tk.StringVar(value=initial_status)
        self.base_dir = Path(__file__).resolve().parent
        self.target_definitions = load_target_definitions(self.base_dir)
        self.target_names = tuple(target.name for target in self.target_definitions)
        self.threshold_lock = threading.Lock()
        self.threshold_values = {
            target.name: target.threshold
            for target in self.target_definitions
        }
        self.threshold_vars = {
            name: tk.DoubleVar(value=value)
            for name, value in self.threshold_values.items()
        }
        self.threshold_label_vars = {
            name: tk.StringVar(value=f"{value:.2f}")
            for name, value in self.threshold_values.items()
        }
        self.capture_mode_var = tk.StringVar(value="wgc")
        self.click_mode_var = tk.StringVar(value="postmessage")
        self.region_vars = {
            "x": tk.IntVar(value=DEFAULT_REGION_X),
            "y": tk.IntVar(value=DEFAULT_REGION_Y),
            "width": tk.IntVar(value=DEFAULT_REGION_WIDTH),
            "height": tk.IntVar(value=DEFAULT_REGION_HEIGHT),
        }

        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.ui_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.closing = False

        self._build_ui()
        self._bind_shortcuts()
        self._set_button_state(running=False)
        self._bring_window_to_front()
        self._poll_ui_queue()

        self.log("프로그램을 시작했습니다.")
        self.log("F8: 시작, F9 또는 ESC: 중지")
        self.log("대상 창은 백그라운드에 있어도 되지만 최소화되어 있으면 안 됩니다.")
        if self.ui_preview_only:
            self.log("[UI 미리보기] 현재 OS가 Windows가 아니므로 자동화 실행은 비활성화했습니다.")
            self.log("              UI 확인만 가능하며, 실제 캡처/클릭은 Windows에서 실행하세요.")

    def _bring_window_to_front(self) -> None:
        """창이 뒤에 숨어 보이지 않는 일을 줄이기 위해 잠깐 최상위로 올립니다."""

        self.root.update_idletasks()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

        # macOS에서 처음 뜬 tkinter 창이 다른 앱 뒤로 가는 경우가 있어 잠깐만 topmost를 켭니다.
        self.root.attributes("-topmost", True)
        self.root.after(1200, lambda: self.root.attributes("-topmost", False))

    def _build_ui(self) -> None:
        """간단한 테스트 도구 형태의 UI를 만듭니다."""

        if self.ui_preview_only:
            self._build_preview_ui()
            return

        # macOS의 기본 Tcl/Tk는 다크 모드에서 배경색을 강하게 바꾸는 경우가 있어,
        # 미리보기에서도 확실히 읽히도록 어두운 배경 + 밝은 글자로 고정합니다.
        bg = "#1E1E1E"
        panel_bg = "#252526"
        input_bg = "#111827"
        text_color = "#F9FAFB"
        accent = "#7AB7FF"

        self.root.configure(bg=bg)

        main_frame = tk.Frame(self.root, bg=bg, padx=12, pady=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        title_frame = tk.Frame(
            main_frame,
            bg=panel_bg,
            padx=8,
            pady=8,
            relief=tk.SOLID,
            bd=1,
        )
        title_frame.pack(fill=tk.X)

        tk.Label(
            title_frame,
            text="대상 창",
            bg=panel_bg,
            fg=accent,
            font=("Arial", 12, "bold"),
        ).pack(anchor=tk.W)

        tk.Label(
            title_frame,
            text="창 제목 일부",
            bg=panel_bg,
            fg=text_color,
        ).pack(anchor=tk.W, pady=(8, 4))
        self.title_entry = tk.Entry(
            title_frame,
            textvariable=self.window_title_var,
            bg=input_bg,
            fg=text_color,
            insertbackground=text_color,
            relief=tk.SOLID,
            bd=1,
        )
        self.title_entry.pack(fill=tk.X)

        button_frame = tk.Frame(main_frame, bg=bg)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        self.start_button = tk.Button(
            button_frame,
            text="시작 (F8)",
            command=self.start_automation,
            width=14,
        )
        self.start_button.pack(side=tk.LEFT)

        self.stop_button = tk.Button(
            button_frame,
            text="종료 (F9/ESC)",
            command=self.stop_automation,
            width=14,
        )
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))

        status_frame = tk.Frame(main_frame, bg=bg)
        status_frame.pack(fill=tk.X, pady=(10, 0))
        tk.Label(status_frame, text="상태", bg=bg, fg=text_color).pack(side=tk.LEFT)
        self.status_label = tk.Label(
            status_frame,
            textvariable=self.status_var,
            bg=bg,
            fg=accent,
        )
        self.status_label.pack(side=tk.LEFT, padx=(8, 0))

        mode_frame = tk.Frame(
            main_frame,
            bg=panel_bg,
            padx=8,
            pady=8,
            relief=tk.SOLID,
            bd=1,
        )
        mode_frame.pack(fill=tk.X, pady=(10, 0))

        tk.Label(
            mode_frame,
            text="캡처/클릭 모드",
            bg=panel_bg,
            fg=accent,
            font=("Arial", 12, "bold"),
        ).pack(anchor=tk.W, pady=(0, 6))

        capture_row = tk.Frame(mode_frame, bg=panel_bg)
        capture_row.pack(fill=tk.X, pady=2)
        tk.Label(
            capture_row,
            text="캡처",
            bg=panel_bg,
            fg=text_color,
            width=10,
            anchor=tk.W,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            capture_row,
            text="비활성 WGC",
            variable=self.capture_mode_var,
            value="wgc",
            command=self.on_capture_mode_changed,
            bg=panel_bg,
            fg=text_color,
            selectcolor=input_bg,
            activebackground=panel_bg,
            activeforeground=text_color,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            capture_row,
            text="화면 영역 캡처",
            variable=self.capture_mode_var,
            value="region",
            command=self.on_capture_mode_changed,
            bg=panel_bg,
            fg=text_color,
            selectcolor=input_bg,
            activebackground=panel_bg,
            activeforeground=text_color,
        ).pack(side=tk.LEFT, padx=(12, 0))

        click_row = tk.Frame(mode_frame, bg=panel_bg)
        click_row.pack(fill=tk.X, pady=2)
        tk.Label(
            click_row,
            text="클릭",
            bg=panel_bg,
            fg=text_color,
            width=10,
            anchor=tk.W,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            click_row,
            text="PostMessage",
            variable=self.click_mode_var,
            value="postmessage",
            bg=panel_bg,
            fg=text_color,
            selectcolor=input_bg,
            activebackground=panel_bg,
            activeforeground=text_color,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            click_row,
            text="마우스 이동 후 복귀",
            variable=self.click_mode_var,
            value="mouse",
            bg=panel_bg,
            fg=text_color,
            selectcolor=input_bg,
            activebackground=panel_bg,
            activeforeground=text_color,
        ).pack(side=tk.LEFT, padx=(12, 0))

        region_row = tk.Frame(mode_frame, bg=panel_bg)
        region_row.pack(fill=tk.X, pady=(6, 0))
        tk.Label(
            region_row,
            text="영역",
            bg=panel_bg,
            fg=text_color,
            width=10,
            anchor=tk.W,
        ).pack(side=tk.LEFT)
        for key, label in (
            ("x", "X"),
            ("y", "Y"),
            ("width", "W"),
            ("height", "H"),
        ):
            tk.Label(region_row, text=label, bg=panel_bg, fg=text_color).pack(side=tk.LEFT)
            tk.Entry(
                region_row,
                textvariable=self.region_vars[key],
                width=7,
                bg=input_bg,
                fg=text_color,
                insertbackground=text_color,
                relief=tk.SOLID,
                bd=1,
            ).pack(side=tk.LEFT, padx=(4, 10))

        threshold_frame = tk.Frame(
            main_frame,
            bg=panel_bg,
            padx=8,
            pady=8,
            relief=tk.SOLID,
            bd=1,
        )
        threshold_frame.pack(fill=tk.X, pady=(10, 0))

        tk.Label(
            threshold_frame,
            text="타겟별 임계값",
            bg=panel_bg,
            fg=accent,
            font=("Arial", 12, "bold"),
        ).pack(anchor=tk.W, pady=(0, 6))

        for name in self.target_names:
            row = tk.Frame(threshold_frame, bg=panel_bg)
            row.pack(fill=tk.X, pady=2)

            tk.Label(
                row,
                text=name,
                bg=panel_bg,
                fg=text_color,
                width=10,
                anchor=tk.W,
            ).pack(side=tk.LEFT)

            tk.Scale(
                row,
                from_=0.5,
                to=1.0,
                resolution=0.01,
                orient=tk.HORIZONTAL,
                variable=self.threshold_vars[name],
                command=lambda value, target_name=name: self.update_threshold(
                    target_name,
                    value,
                ),
                bg=panel_bg,
                fg=text_color,
                troughcolor=input_bg,
                highlightthickness=0,
                length=360,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

            tk.Label(
                row,
                textvariable=self.threshold_label_vars[name],
                bg=panel_bg,
                fg=accent,
                width=5,
            ).pack(side=tk.LEFT, padx=(8, 0))

        log_frame = tk.Frame(
            main_frame,
            bg=panel_bg,
            padx=8,
            pady=8,
            relief=tk.SOLID,
            bd=1,
        )
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        tk.Label(
            log_frame,
            text="로그",
            bg=panel_bg,
            fg=accent,
            font=("Arial", 12, "bold"),
        ).pack(anchor=tk.W, pady=(0, 6))

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=22,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=input_bg,
            fg=text_color,
            insertbackground=text_color,
            relief=tk.SOLID,
            bd=1,
            font=("Consolas", 10),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _build_preview_ui(self) -> None:
        """
        Mac 전용 UI 미리보기입니다.

        일부 Mac Tcl/Tk 조합에서는 Label, Entry, Text가 보이지 않는 경우가 있어
        미리보기 모드에서는 화면에 확실히 표시되는 Button 위젯만 사용합니다.
        Windows 실행 시에는 위의 일반 UI가 사용됩니다.
        """

        self.root.configure(bg="#1E1E1E")
        main_frame = tk.Frame(self.root, bg="#1E1E1E", padx=18, pady=18)
        main_frame.pack(fill=tk.BOTH, expand=True)

        self.title_entry = tk.Button(
            main_frame,
            text=f"대상 창 제목: {self.window_title_var.get()}",
            anchor=tk.W,
            justify=tk.LEFT,
            relief=tk.GROOVE,
            height=2,
        )
        self.title_entry.pack(fill=tk.X, pady=(0, 12))

        button_frame = tk.Frame(main_frame, bg="#1E1E1E")
        button_frame.pack(fill=tk.X, pady=(0, 12))

        self.start_button = tk.Button(
            button_frame,
            text="시작 (F8) - 미리보기 로그만 표시",
            command=self.start_automation,
            width=28,
            height=2,
        )
        self.start_button.pack(side=tk.LEFT)

        self.stop_button = tk.Button(
            button_frame,
            text="종료 (F9/ESC)",
            command=self.stop_automation,
            width=18,
            height=2,
        )
        self.stop_button.pack(side=tk.LEFT, padx=(10, 0))

        self.status_label = tk.Button(
            main_frame,
            textvariable=self.status_var,
            anchor=tk.W,
            justify=tk.LEFT,
            relief=tk.GROOVE,
            height=2,
        )
        self.status_label.pack(fill=tk.X, pady=(0, 12))

        mode_text = (
            f"캡처 모드: {self.capture_mode_var.get()}\n"
            f"클릭 모드: {self.click_mode_var.get()}\n"
            f"영역: {DEFAULT_REGION_X}, {DEFAULT_REGION_Y}, "
            f"{DEFAULT_REGION_WIDTH}, {DEFAULT_REGION_HEIGHT}"
        )
        self.preview_mode_button = tk.Button(
            main_frame,
            text=mode_text,
            anchor=tk.W,
            justify=tk.LEFT,
            relief=tk.GROOVE,
            height=3,
        )
        self.preview_mode_button.pack(fill=tk.X, pady=(0, 12))

        threshold_text = "\n".join(
            f"{name} 임계값: {self.get_threshold(name):.2f}"
            for name in self.target_names
        )
        self.preview_threshold_button = tk.Button(
            main_frame,
            text=threshold_text,
            anchor=tk.W,
            justify=tk.LEFT,
            relief=tk.GROOVE,
            height=len(self.target_names),
        )
        self.preview_threshold_button.pack(fill=tk.X, pady=(0, 12))

        self.preview_log_messages: list[str] = []
        self.preview_log_frame = tk.Frame(
            main_frame,
            relief=tk.GROOVE,
            bd=2,
        )
        self.preview_log_frame.pack(fill=tk.BOTH, expand=True)

        self.preview_log_rows: list[tk.Button] = []
        for _index in range(12):
            row = tk.Button(
                self.preview_log_frame,
                text="",
                anchor=tk.W,
                justify=tk.LEFT,
                relief=tk.FLAT,
                height=1,
            )
            row.pack(fill=tk.X, padx=6, pady=1)
            self.preview_log_rows.append(row)

    def _bind_shortcuts(self) -> None:
        """UI가 활성화되어 있을 때 동작하는 단축키를 등록합니다."""

        self.root.bind("<F8>", lambda _event: self.start_automation())
        self.root.bind("<F9>", lambda _event: self.stop_automation())
        self.root.bind("<Escape>", lambda _event: self.stop_automation())

        # 기존 콘솔 예제의 q 종료 습관을 보조로 유지합니다.
        self.root.bind("<KeyPress-q>", lambda _event: self.stop_automation())
        self.root.bind("<KeyPress-Q>", lambda _event: self.stop_automation())

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_capture_mode_changed(self) -> None:
        """화면 영역 캡처 모드에서는 기본 클릭 방식을 마우스 이동/복귀로 맞춥니다."""

        if self.capture_mode_var.get() == "region":
            self.click_mode_var.set("mouse")
            self.log("[설정] 화면 영역 캡처 모드에서는 마우스 이동 후 복귀 클릭을 사용합니다.")

    def get_region_from_ui(self) -> Optional[tuple[int, int, int, int]]:
        """UI의 영역 입력값을 안전하게 읽습니다."""

        try:
            x = int(self.region_vars["x"].get())
            y = int(self.region_vars["y"].get())
            width = int(self.region_vars["width"].get())
            height = int(self.region_vars["height"].get())
        except Exception as exc:
            self.log(f"[오류] 화면 영역 값이 올바른 숫자가 아닙니다: {exc}")
            return None

        if width <= 0 or height <= 0:
            self.log("[오류] 화면 영역의 W/H는 1 이상이어야 합니다.")
            return None

        return x, y, width, height

    def start_automation(self) -> None:
        """시작 버튼 또는 F8 키로 자동화 스레드를 시작합니다."""

        if self.ui_preview_only:
            self.log("[UI 미리보기] Mac에서는 자동화 루프를 실행하지 않습니다.")
            self.log("              Windows에서 실행하면 시작 버튼과 F8이 자동화를 시작합니다.")
            self.set_status("UI 미리보기")
            self._set_button_state(running=False)
            return

        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.log("[안내] 이미 실행 중입니다.")
            self.set_status("실행 중")
            return

        capture_mode = self.capture_mode_var.get()
        click_mode = self.click_mode_var.get()
        region = self.get_region_from_ui()
        if region is None:
            self.set_status("오류 발생")
            return

        if capture_mode == "region":
            click_mode = "mouse"
            self.click_mode_var.set("mouse")

        window_title = self.window_title_var.get().strip()
        needs_window = (
            capture_mode == "wgc"
            or click_mode == "postmessage"
            or any(target.action == "key" for target in self.target_definitions)
        )
        if needs_window and not window_title:
            self.log("[오류] 대상 창 제목을 입력하세요.")
            self.set_status("오류 발생")
            return

        self.stop_event.clear()
        self._set_button_state(running=True)
        self.set_status("실행 중")
        self.log(
            f"[시작] 자동화를 시작합니다. "
            f"캡처={capture_mode}, 클릭={click_mode}, 영역={region}, 창='{window_title}'"
        )

        self.worker_thread = threading.Thread(
            target=self._automation_loop,
            args=(window_title, capture_mode, click_mode, region),
            daemon=True,
        )
        self.worker_thread.start()

    def update_threshold(self, target_name: str, value: str) -> None:
        """슬라이더 값 변경을 thread-safe하게 저장합니다."""

        threshold = max(0.0, min(1.0, float(value)))
        with self.threshold_lock:
            self.threshold_values[target_name] = threshold
        self.threshold_label_vars[target_name].set(f"{threshold:.2f}")

    def get_threshold(self, target_name: str) -> float:
        """작업 스레드에서 사용할 현재 임계값을 가져옵니다."""

        with self.threshold_lock:
            return float(self.threshold_values.get(target_name, 0.8))

    def apply_current_thresholds(self, targets: list[TargetImage]) -> None:
        """UI에서 조절한 임계값을 TargetImage 목록에 반영합니다."""

        for target in targets:
            target.threshold = self.get_threshold(target.name)

    def stop_automation(self) -> None:
        """종료 버튼, F9, ESC, q 키로 자동화를 안전하게 중단 요청합니다."""

        if self.ui_preview_only:
            self.log("[UI 미리보기] 실행 중인 자동화가 없습니다.")
            self.set_status("UI 미리보기")
            self._set_button_state(running=False)
            return

        if self.worker_thread is None or not self.worker_thread.is_alive():
            self.log("[안내] 현재 실행 중인 자동화가 없습니다.")
            self.set_status("대기 중")
            self._set_button_state(running=False)
            return

        self.stop_event.set()
        self.set_status("종료 요청됨")
        self.log("[중지 요청] 자동화 루프를 안전하게 중단합니다.")
        self.stop_button.configure(state=tk.DISABLED)

    def on_close(self) -> None:
        """창 닫기 버튼을 눌렀을 때도 자동화 스레드를 자연스럽게 멈춥니다."""

        self.closing = True
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.stop_event.set()
            self.log("[종료 요청] 창 닫기 전에 자동화 스레드를 중단합니다.")
            self.set_status("종료 요청됨")
            self.root.after(100, self._destroy_when_worker_stops)
            return

        self.root.destroy()

    def _destroy_when_worker_stops(self) -> None:
        """작업 스레드가 끝난 뒤 tkinter 창을 닫습니다."""

        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.root.after(100, self._destroy_when_worker_stops)
            return
        self.root.destroy()

    def _automation_loop(
        self,
        window_title: str,
        capture_mode: str,
        click_mode: str,
        region: tuple[int, int, int, int],
    ) -> None:
        """
        실제 자동화 루프입니다.

        tkinter는 메인 스레드에서만 UI를 만지는 것이 안전하므로,
        이 함수는 직접 라벨/로그 위젯을 수정하지 않고 queue에 메시지만 넣습니다.
        """

        manager: Optional[InactiveManager] = None

        try:
            self.queue_status("대상 이미지 로드 중")
            targets = load_targets(
                self.base_dir,
                self.queue_log,
                definitions=self.target_definitions,
            )
            if targets is None:
                self.queue_status("오류 발생")
                self.queue_log("[종료] 타겟 이미지 준비에 실패하여 실행을 중단합니다.")
                return

            requires_window = (
                capture_mode == "wgc"
                or click_mode == "postmessage"
                or any(target.action == "key" for target in targets)
            )

            if requires_window:
                manager = InactiveManager(window_title, logger=self.queue_log)

            while not self.stop_event.is_set():
                self.apply_current_thresholds(targets)

                if requires_window and manager is not None and not manager.is_valid_window():
                    self.queue_status("대상 창 검색 중")
                    manager.find_window()

                    if self.stop_event.is_set():
                        break

                    if not manager.is_valid_window():
                        self.queue_log(f"[대기] {WINDOW_RETRY_SECONDS}초 후 창을 다시 찾습니다.")
                        self.interruptible_sleep(WINDOW_RETRY_SECONDS)
                        continue

                self.queue_status("실행 중")
                if capture_mode == "region":
                    screen_gray = self.capture_screen_region(region)
                elif manager is not None:
                    screen_gray = manager.capture_client_area()
                else:
                    self.queue_log("[오류] 캡처 관리자가 준비되지 않았습니다.")
                    screen_gray = None

                if screen_gray is None:
                    self.queue_status("오류 발생")
                    self.queue_log(f"[대기] 캡처 실패로 {LOOP_SLEEP_SECONDS}초 후 다시 시도합니다.")
                    self.interruptible_sleep(LOOP_SLEEP_SECONDS)
                    continue

                found_any = False

                # targets.json에 적힌 순서대로 탐지합니다.
                for target in targets:
                    if self.stop_event.is_set():
                        break

                    center, score = find_template_center(screen_gray, target, self.queue_log)
                    if center is None:
                        continue

                    found_any = True
                    base_x, base_y = center
                    x, y = self.apply_click_jitter(target, base_x, base_y)
                    self.queue_status("이미지 감지 성공")
                    self.queue_log(
                        f"[감지] {target.name} "
                        f"(점수: {score:.3f}, 위치: {base_x}, {base_y})"
                    )
                    if (x, y) != (base_x, base_y):
                        self.queue_log(
                            f"[클릭 좌표] {target.name} "
                            f"(기준: {base_x}, {base_y}, 보정: {x}, {y})"
                        )

                    if target.action == "key":
                        action_ok = self.dispatch_key_press(manager, target)
                    else:
                        action_ok = self.dispatch_click(
                            manager,
                            click_mode,
                            capture_mode,
                            region,
                            x,
                            y,
                        )

                    if action_ok:
                        self.queue_status("키 입력 완료" if target.action == "key" else "클릭 완료")
                        if target.wait_after_click > 0:
                            self.queue_log(f"[대기] {target.wait_after_click}초 동안 대기합니다.")
                            self.interruptible_sleep(target.wait_after_click)

                    # 한 루프에서 하나만 클릭합니다.
                    break

                if self.stop_event.is_set():
                    break

                if not found_any:
                    self.interruptible_sleep(LOOP_SLEEP_SECONDS)

        except Exception as exc:
            self.queue_status("오류 발생")
            self.queue_log(f"[치명적 오류] 예상하지 못한 문제가 발생했습니다: {exc}")

        finally:
            if manager is not None:
                manager.stop_capture()
            self.stop_event.set()
            self.queue_status("종료됨")
            self.queue_log("[종료] 자동화 루프가 종료되었습니다.")
            self.ui_queue.put(("finished", ""))

    def apply_click_jitter(
        self,
        target: TargetImage,
        center_x: int,
        center_y: int,
    ) -> tuple[int, int]:
        """매칭 영역 안에서 중심 좌표 주변의 작은 클릭 보정값을 적용합니다."""

        if target.image_gray is None:
            return center_x, center_y

        target_height, target_width = target.image_gray.shape[:2]

        # 중심에서 너무 멀어져 템플릿 영역 밖으로 나가지 않도록 작은 이미지에서는 범위를 줄입니다.
        max_x_jitter = min(CLICK_JITTER_PIXELS, max(0, (target_width - 1) // 2))
        max_y_jitter = min(CLICK_JITTER_PIXELS, max(0, (target_height - 1) // 2))

        jitter_x = random.randint(-max_x_jitter, max_x_jitter)
        jitter_y = random.randint(-max_y_jitter, max_y_jitter)

        return center_x + jitter_x, center_y + jitter_y

    def capture_screen_region(self, region: tuple[int, int, int, int]) -> Optional[np.ndarray]:
        """
        화면의 지정 영역만 캡처해서 GrayScale NumPy 배열로 반환합니다.

        이 모드는 대상 창이 실제 화면에 보이는 상태일 때 사용하는 현실적 타협 모드입니다.
        WGC와 달리 전체 화면 캡처에서 영역을 잘라오기 때문에 렌더링 호환성이 높지만,
        창이 다른 창에 가려지면 가려진 화면 그대로 캡처됩니다.
        """

        if pyautogui is None:
            self.queue_log("[오류] pyautogui를 불러올 수 없어 화면 영역 캡처를 사용할 수 없습니다.")
            self.queue_log(f"       원본 오류: {PYAUTOGUI_IMPORT_ERROR}")
            return None

        x, y, width, height = region
        try:
            screenshot = pyautogui.screenshot(region=(x, y, width, height))
            image_rgb = np.array(screenshot)
            if image_rgb.size == 0:
                self.queue_log("[캡처 오류] 화면 영역 캡처 결과가 비어 있습니다.")
                return None

            if np.all(image_rgb == 0):
                self.queue_log("[캡처 오류] 화면 영역 캡처 결과가 완전히 검은색입니다.")
                return None

            self.queue_log(f"[캡처 성공] 화면 영역 x={x}, y={y}, w={width}, h={height}")
            screen_gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
            cv2.imwrite(str(Path(__file__).resolve().parent / "debug_capture.png"), screen_gray)
            return screen_gray
        except Exception as exc:
            self.queue_log(f"[캡처 오류] 화면 영역 캡처 중 문제가 발생했습니다: {exc}")
            return None

    def dispatch_key_press(
        self,
        manager: Optional[InactiveManager],
        target: TargetImage,
    ) -> bool:
        """타겟 창에만 PostMessage 방식 키 입력을 전송합니다."""

        if manager is None:
            self.queue_log("[오류] 키 입력에는 대상 창 HWND가 필요합니다.")
            return False
        if not target.key:
            self.queue_log(f"[오류] {target.name}에 전송할 키가 설정되지 않았습니다.")
            return False

        use_send_message = target.key_mode == "sendmessage"
        mode_name = "SendMessage" if use_send_message else "PostMessage"
        self.queue_log(
            f"[키 요청] {target.name} 감지로 대상 창에 "
            f"'{target.key.upper()}' 키를 {mode_name}로 보냅니다."
        )
        return manager.post_key_press(target.key, use_send_message=use_send_message)

    def dispatch_click(
        self,
        manager: Optional[InactiveManager],
        click_mode: str,
        capture_mode: str,
        region: tuple[int, int, int, int],
        x: int,
        y: int,
    ) -> bool:
        """선택된 클릭 모드에 따라 클릭을 전송합니다."""

        if click_mode == "postmessage":
            if manager is None:
                self.queue_log("[오류] PostMessage 클릭에는 대상 창 HWND가 필요합니다.")
                return False
            start_x, start_y = manager.get_virtual_start_position(x, y)
            return manager.post_curved_click(start_x, start_y, x, y)

        if capture_mode == "region":
            screen_x = region[0] + x
            screen_y = region[1] + y
            return self.click_mouse_and_return(screen_x, screen_y)

        if manager is None:
            self.queue_log("[오류] 마우스 클릭 좌표 변환에 대상 창 정보가 없습니다.")
            return False

        screen_point = manager.client_to_screen(x, y)
        if screen_point is None:
            return False

        screen_x, screen_y = screen_point
        return self.click_mouse_and_return(screen_x, screen_y)

    def click_mouse_and_return(self, screen_x: int, screen_y: int) -> bool:
        """
        실제 마우스를 대상 위치로 부드럽게 이동해 클릭한 뒤 원래 위치로 되돌립니다.

        허가된 UI 테스트 및 업무 자동화 환경에서 입력 장치 충돌을 줄이기 위한
        안정 클릭 루틴입니다. 이 방식은 사용 중인 마우스를 잠깐 점유합니다.
        """

        if pyautogui is None:
            self.queue_log("[오류] pyautogui를 불러올 수 없어 마우스 클릭을 사용할 수 없습니다.")
            self.queue_log(f"       원본 오류: {PYAUTOGUI_IMPORT_ERROR}")
            return False

        try:
            original_x, original_y = pyautogui.position()
            self.queue_log(
                f"[클릭 전송] 화면 좌표=({screen_x}, {screen_y}), "
                f"복귀 좌표=({original_x}, {original_y})"
            )

            # 입력 렉과 포커스 흔들림을 줄이기 위해 순간이동 대신 짧은 고정 시간으로 이동합니다.
            pyautogui.moveTo(screen_x, screen_y, duration=0.15)
            time.sleep(MOUSE_HOVER_BEFORE_CLICK_SECONDS)

            pyautogui.click()
            time.sleep(0.05)

            # 사용자의 원래 작업 위치를 최대한 보존합니다.
            pyautogui.moveTo(original_x, original_y, duration=0.15)
            self.queue_log("[클릭 완료] 마우스 클릭 후 원래 위치로 복귀했습니다.")
            return True
        except pyautogui.FailSafeException:
            self.queue_log("[긴급 중단] PyAutoGUI FAILSAFE가 감지되었습니다.")
            raise
        except Exception as exc:
            self.queue_log(f"[오류] 마우스 클릭 중 문제가 발생했습니다: {exc}")
            return False

    def interruptible_sleep(self, seconds: float) -> bool:
        """
        긴 대기 중에도 종료 버튼/단축키가 반응할 수 있도록 0.1초 단위로 나눠 쉽니다.

        반환값:
        - True: 대기 중 stop_event가 설정됨
        - False: 지정된 시간만큼 정상 대기
        """

        end_time = time.monotonic() + seconds
        while time.monotonic() < end_time:
            if self.stop_event.is_set():
                return True
            remaining = end_time - time.monotonic()
            time.sleep(min(0.1, max(0.0, remaining)))
        return self.stop_event.is_set()

    def log(self, message: str) -> None:
        """UI 스레드에서 바로 로그를 남깁니다."""

        timestamp = time.strftime("%H:%M:%S")
        line = f"{timestamp} {message}\n"

        if hasattr(self, "preview_log_rows"):
            self.preview_log_messages.append(line.rstrip())
            self.preview_log_messages = self.preview_log_messages[-12:]
            empty_count = 12 - len(self.preview_log_messages)
            visible_lines = [""] * empty_count + self.preview_log_messages
            for row, text in zip(self.preview_log_rows, visible_lines):
                row.configure(text=text)
            return

        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line)

        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 1000:
            self.log_text.delete("1.0", "100.0")

        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def queue_log(self, message: str) -> None:
        """작업 스레드에서 로그 메시지를 UI 큐에 넣습니다."""

        self.ui_queue.put(("log", message))

    def set_status(self, status: str) -> None:
        """UI 스레드에서 상태 라벨을 갱신합니다."""

        self.status_var.set(status)

    def queue_status(self, status: str) -> None:
        """작업 스레드에서 상태 변경 메시지를 UI 큐에 넣습니다."""

        self.ui_queue.put(("status", status))

    def _poll_ui_queue(self) -> None:
        """root.after로 UI 큐를 주기적으로 확인하고 위젯을 갱신합니다."""

        while True:
            try:
                kind, message = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self.log(message)
            elif kind == "status":
                self.set_status(message)
            elif kind == "finished":
                self._set_button_state(running=False)

        if not self.closing:
            self.root.after(100, self._poll_ui_queue)

    def _set_button_state(self, running: bool) -> None:
        """실행 상태에 따라 시작/종료 버튼 활성화를 조절합니다."""

        if self.ui_preview_only:
            self.start_button.configure(state=tk.NORMAL)
            self.stop_button.configure(state=tk.DISABLED)
            self.title_entry.configure(state=tk.NORMAL)
            return

        if running:
            self.start_button.configure(state=tk.DISABLED)
            self.stop_button.configure(state=tk.NORMAL)
            self.title_entry.configure(state=tk.DISABLED)
        else:
            self.start_button.configure(state=tk.NORMAL)
            self.stop_button.configure(state=tk.DISABLED)
            self.title_entry.configure(state=tk.NORMAL)


def _target_from_config(config: dict[str, object], index: int) -> Optional[TargetImage]:
    """targets.json 항목 하나를 TargetImage 설정으로 변환합니다."""

    if bool(config.get("enabled", True)) is False:
        return None

    filename_value = config.get("filename")
    if not filename_value:
        raise ValueError(f"{index + 1}번째 타겟에 filename이 없습니다.")

    filename = str(filename_value)
    name = str(config.get("name") or Path(filename).stem)
    action = str(config.get("action", "click")).strip().lower()
    if action not in ("click", "key"):
        raise ValueError(f"{name}의 action은 click 또는 key여야 합니다: {action!r}")

    key_value = config.get("key")
    key = str(key_value).strip() if key_value is not None else None
    if action == "key" and not key:
        raise ValueError(f"{name}은 key action이라 key 값이 필요합니다.")

    key_mode = str(config.get("key_mode", "sendmessage")).strip().lower()
    if key_mode not in ("postmessage", "sendmessage"):
        raise ValueError(f"{name}의 key_mode는 postmessage 또는 sendmessage여야 합니다: {key_mode!r}")

    threshold = float(config.get("threshold", 0.8))
    threshold = max(0.0, min(1.0, threshold))
    wait_after_click = float(
        config.get("wait_after_action", config.get("wait_after_click", 0.0))
    )

    return TargetImage(
        name=name,
        filename=filename,
        wait_after_click=max(0.0, wait_after_click),
        threshold=threshold,
        action=action,
        key=key,
        key_mode=key_mode,
    )


def load_target_definitions(
    base_dir: Path,
    logger: Optional[LogCallback] = None,
) -> list[TargetImage]:
    """targets.json에서 타겟 설정을 읽고, 없으면 기본 설정을 사용합니다."""

    log = logger or print
    config_path = base_dir / TARGET_CONFIG_FILENAME
    raw_targets: object = DEFAULT_TARGET_CONFIGS

    if config_path.exists():
        try:
            with config_path.open("r", encoding="utf-8") as config_file:
                raw_config = json.load(config_file)
            raw_targets = raw_config.get("targets", raw_config) if isinstance(raw_config, dict) else raw_config
        except Exception as exc:
            log(f"[설정 오류] {config_path} 파일을 읽지 못했습니다: {exc}")
            log("[설정 안내] 기본 타겟 설정을 대신 사용합니다.")
            raw_targets = DEFAULT_TARGET_CONFIGS
    else:
        log(f"[설정 안내] {TARGET_CONFIG_FILENAME}이 없어 기본 타겟 설정을 사용합니다.")

    if not isinstance(raw_targets, list):
        log("[설정 오류] 타겟 설정은 리스트이거나 {'targets': [...]} 형태여야 합니다.")
        raw_targets = DEFAULT_TARGET_CONFIGS

    targets: list[TargetImage] = []
    for index, raw_target in enumerate(raw_targets):
        if not isinstance(raw_target, dict):
            log(f"[설정 오류] {index + 1}번째 타겟 설정이 객체가 아니라 건너뜁니다.")
            continue
        try:
            target = _target_from_config(raw_target, index)
        except Exception as exc:
            log(f"[설정 오류] {index + 1}번째 타겟 설정을 건너뜁니다: {exc}")
            continue
        if target is not None:
            targets.append(target)

    if targets:
        return targets

    log("[설정 오류] 사용할 타겟이 없어 기본 타겟 설정을 사용합니다.")
    return [
        target
        for target in (
            _target_from_config(config, index)
            for index, config in enumerate(DEFAULT_TARGET_CONFIGS)
        )
        if target is not None
    ]


def clone_target_definition(target: TargetImage) -> TargetImage:
    """이미지 배열 없이 타겟 설정만 복사합니다."""

    return TargetImage(
        name=target.name,
        filename=target.filename,
        wait_after_click=target.wait_after_click,
        threshold=target.threshold,
        action=target.action,
        key=target.key,
        key_mode=target.key_mode,
    )


def load_targets(
    base_dir: Path,
    logger: Optional[LogCallback] = None,
    definitions: Optional[list[TargetImage]] = None,
) -> Optional[list[TargetImage]]:
    """설정된 타겟 이미지를 GrayScale 이미지로 미리 로드합니다."""

    log = logger or print
    target_definitions = definitions or load_target_definitions(base_dir, logger=log)
    targets = [clone_target_definition(target) for target in target_definitions]

    for target in targets:
        image_path = base_dir / target.filename

        if not image_path.exists():
            log(f"[오류] 이미지 파일을 찾을 수 없습니다: {image_path}")
            log(f"       targets.json의 filename을 확인하거나 파일을 같은 폴더에 넣어주세요.")
            return None

        try:
            # cv2.imread는 한글/특수문자 경로에서 실패하는 경우가 있어,
            # np.fromfile + cv2.imdecode 방식으로 읽습니다.
            file_bytes = np.fromfile(str(image_path), dtype=np.uint8)
            image_bgr = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
        except Exception as exc:
            log(f"[오류] 이미지 파일을 읽는 중 문제가 발생했습니다: {image_path}")
            log(f"       원본 오류: {exc}")
            return None

        if image_bgr is None:
            log(f"[오류] 이미지 로드에 실패했습니다: {image_path}")
            log("       파일이 손상되었거나 OpenCV가 읽을 수 없는 형식일 수 있습니다.")
            return None

        target.image_gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        log(
            f"[이미지 로드] {target.filename}, "
            f"크기={target.image_gray.shape[1]}x{target.image_gray.shape[0]}, "
            f"임계값={target.threshold:.2f}, action={target.action}"
        )

    return targets


def find_template_center(
    screen_gray: np.ndarray,
    target: TargetImage,
    logger: Optional[LogCallback] = None,
) -> tuple[Optional[tuple[int, int]], float]:
    """
    cv2.matchTemplate로 target 이미지를 찾고 중심 좌표와 매칭 점수를 반환합니다.

    반환:
    - center: threshold 이상이면 (x, y), 아니면 None
    - score: 가장 높은 매칭 점수
    """

    log = logger or print

    if target.image_gray is None:
        log(f"[오류] {target.name} 이미지가 로드되지 않았습니다.")
        return None, 0.0

    target_height, target_width = target.image_gray.shape[:2]
    screen_height, screen_width = screen_gray.shape[:2]

    if target_width > screen_width or target_height > screen_height:
        log(
            f"[주의] {target.filename}이 클라이언트 영역보다 큽니다. "
            f"target={target_width}x{target_height}, screen={screen_width}x{screen_height}"
        )
        return None, 0.0

    cv2.imwrite(str(Path(__file__).resolve().parent / "debug_screen.png"), screen_gray)

    result = cv2.matchTemplate(
        screen_gray,
        target.image_gray,
        cv2.TM_CCOEFF_NORMED,
    )
    _min_value, max_value, _min_location, max_location = cv2.minMaxLoc(result)
    score = float(max_value)
    debug_message = f"[디버그] {target.name} 검사 중... 산출된 최고 유사도 점수: {score:.3f}"
    print(debug_message)
    if logger is not None:
        log(debug_message)

    top_left_x, top_left_y = max_location
    center_x = top_left_x + target_width // 2
    center_y = top_left_y + target_height // 2

    log(
        f"[매칭] {target.name} "
        f"(점수: {score:.3f}, 위치: {center_x}, {center_y}, 기준: {target.threshold:.2f})"
    )

    if score < target.threshold:
        return None, score

    return (center_x, center_y), score


def main() -> None:
    """프로그램 진입점입니다."""

    root = tk.Tk()
    AutomationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
