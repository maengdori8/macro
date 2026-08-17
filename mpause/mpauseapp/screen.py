"""대상 창을 찾아 캡처하고, 찾는 그림이 화면 어디에 있는지 알아낸다.

mAuto(``macroapp/window.py`` + ``macroapp/matching.py``)에서 필요한 만큼만
이식했다. 이식하면서 지킨 것 세 가지:

  1. **창은 제목이 아니라 실행 파일로 찾는다.** 제목은 업데이트마다 바뀐다.
  2. **해상도를 정규화한 뒤 매칭한다.** 템플릿은 특정 세로 크기에서 잘라낸
     픽셀 조각이라, 다른 해상도로 켜면 단일 배율 matchTemplate 이 전부 실패한다.
  3. **축소는 화면·템플릿 둘 다 같은 보간(INTER_AREA)** 으로 한다. 한쪽만
     스트라이드 슬라이싱하면 얇은 템플릿이 1차 필터에서 통째로 누락된다
     (mAuto 에서 8개 중 6개가 실패하던 실제 버그).
"""

from __future__ import annotations

import ctypes
import os
import time
from typing import Callable, Optional

from mpauseapp import assets, deps
from mpauseapp.config import TARGET_WINDOW_PROCESS_NAMES

Logger = Callable[[str], None]

# ─── 해상도 정규화 (mAuto 와 같은 값) ───────────────────────────────────────
#: 템플릿을 잘라낸 캡처 프레임의 세로 크기.
TEMPLATE_REFERENCE_HEIGHT = 1048
#: 이 오차 안이면 리사이즈를 건너뛴다.
CAPTURE_SCALE_DEADZONE = 0.01
#: 범위를 벗어난 배율은 보정하지 않는다(오검출 상태로 누르는 것보다 안전).
CAPTURE_SCALE_MIN = 0.5
CAPTURE_SCALE_MAX = 2.5

#: 1차 사전필터 축소 배율. 2 가 가장 안전하다(3 이상은 얇은 템플릿 누락 위험).
DOWNSCALE_FACTOR = 2

_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_KERNEL32_READY = False
_USER32_READY = False


def _silent(_message: str) -> None:
    """기본 로거 — 아무 데도 남기지 않는다."""


def capture_normalization_scale(frame_height: int) -> float:
    """캡처 세로를 템플릿 기준 세로로 맞추는 배율. 1.0 이면 보정하지 않는다.

    세로 기준인 이유: 게임 UI 는 세로 해상도에 맞춰 커지므로 2560x1440 은
    1.34배가 되지만, 21:9(2560x1080)처럼 가로만 넓어지면 UI 크기가 그대로여서
    배율이 1.0 이 된다. 가로 기준으로 잡으면 후자에서 멀쩡한 화면을 잘못 줄인다.
    """

    try:
        height = int(frame_height)
    except (TypeError, ValueError):
        return 1.0
    if height <= 0:
        return 1.0
    scale = height / float(TEMPLATE_REFERENCE_HEIGHT)
    if abs(scale - 1.0) <= CAPTURE_SCALE_DEADZONE:
        return 1.0
    if not (CAPTURE_SCALE_MIN <= scale <= CAPTURE_SCALE_MAX):
        return 1.0
    return scale


# ─── 대상 창 찾기 ──────────────────────────────────────────────────────────


def _process_name(pid: int) -> Optional[str]:
    """PID 의 실행 파일 이름(소문자). 실패하면 None."""

    if not hasattr(ctypes, "windll"):
        return None
    try:
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        # 시그니처는 1회만 지정한다. 64비트에서 기본 restype(c_int)은 HANDLE 을
        # 잘라먹어 핸들이 조용히 망가진다.
        global _KERNEL32_READY
        if not _KERNEL32_READY:
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            kernel32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            ]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            _KERNEL32_READY = True

        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return None
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(32768)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return os.path.basename(buf.value).lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return None


def _window_pid(hwnd: int) -> int:
    if not hasattr(ctypes, "windll"):
        return 0
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        global _USER32_READY
        if not _USER32_READY:
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.GetWindowThreadProcessId.argtypes = [
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            ]
            _USER32_READY = True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def window_alive(hwnd: Optional[int]) -> bool:
    """캡처할 수 있는 상태의 창인가(존재·표시·비최소화).

    최소화된 창은 캡처가 빈 프레임만 주므로 살아 있는 것으로 보지 않는다.
    """

    gui = deps.win32gui
    if gui is None or not hwnd:
        return False
    try:
        return bool(
            gui.IsWindow(int(hwnd))
            and gui.IsWindowVisible(int(hwnd))
            and not gui.IsIconic(int(hwnd))
        )
    except Exception:
        return False


def _is_target_window(hwnd: int) -> bool:
    pid = _window_pid(hwnd)
    if not pid:
        return False
    name = _process_name(pid)
    if not name:
        return False
    stem = name.replace(".exe", "")
    return any(candidate in stem for candidate in TARGET_WINDOW_PROCESS_NAMES)


def find_window() -> Optional[int]:
    """대상 창의 HWND. 없으면 None.

    최소화된 창은 제외한다 — 캡처가 빈 프레임만 준다.
    """

    if deps.win32gui is None:
        return None

    found: list[int] = []

    def visit(hwnd: int, _extra: object) -> bool:
        # EnumWindows 콜백은 True 를 돌려줘야 열거가 계속된다.
        try:
            if not deps.win32gui.IsWindow(hwnd):
                return True
            if not deps.win32gui.IsWindowVisible(hwnd):
                return True
            if deps.win32gui.IsIconic(hwnd):
                return True
            # 제목 없는 도구 창(스플래시·오버레이)은 대상이 아니다.
            if not deps.win32gui.GetWindowText(hwnd).strip():
                return True
            if _is_target_window(hwnd):
                found.append(int(hwnd))
        except Exception:
            pass
        return True

    try:
        deps.win32gui.EnumWindows(visit, None)
    except Exception:
        return None
    return found[0] if found else None


# ─── 좌표 변환 ─────────────────────────────────────────────────────────────


def _same_size(first, second, tolerance: int = 2) -> bool:
    """DPI 반올림 차이를 감안해 두 크기가 거의 같은지."""

    return (
        abs(int(first[0]) - int(second[0])) <= tolerance
        and abs(int(first[1]) - int(second[1])) <= tolerance
    )


def _frame_bounds(hwnd: int):
    if not hasattr(ctypes, "windll"):
        return None
    try:
        from ctypes import wintypes

        rect = wintypes.RECT()
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(int(hwnd)),
            ctypes.c_uint(_DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if result != 0:
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:
        return None


def _capture_origin(hwnd: int, frame_size) -> Optional[tuple[int, int]]:
    """캡처 프레임의 화면 기준 좌상단 원점을 추정한다."""

    if deps.win32gui is None:
        return None

    extended = _frame_bounds(hwnd)
    window_rect = deps.win32gui.GetWindowRect(hwnd)

    if frame_size is not None:
        if extended is not None:
            ext_left, ext_top, ext_right, ext_bottom = extended
            if _same_size(frame_size, (ext_right - ext_left, ext_bottom - ext_top)):
                return int(ext_left), int(ext_top)
        win_left, win_top, win_right, win_bottom = window_rect
        if _same_size(frame_size, (win_right - win_left, win_bottom - win_top)):
            return int(win_left), int(win_top)

    if extended is not None:
        return int(extended[0]), int(extended[1])
    return int(window_rect[0]), int(window_rect[1])


# ─── 템플릿 ────────────────────────────────────────────────────────────────


class Template:
    """찾을 그림 하나. 축소본과 직전 위치를 캐시한다."""

    def __init__(self, gray, threshold: float) -> None:
        self.gray = gray
        self.threshold = float(threshold)
        self.small = None
        self._last_match: Optional[tuple[tuple[int, int], tuple[int, int]]] = None
        self._last_match_misses = 0


def load_template(threshold: float) -> Optional[Template]:
    """바이너리에 임베드된 그림을 grayscale 로 푼다."""

    cv2 = deps.cv2
    np = deps.np
    if cv2 is None or np is None:
        return None
    try:
        buffer = np.frombuffer(assets.target_png(), dtype=np.uint8)
        gray = cv2.imdecode(buffer, cv2.IMREAD_GRAYSCALE)
    except Exception:
        return None
    if gray is None or gray.size == 0:
        return None
    return Template(gray, threshold)


def _inter_area() -> int:
    cv2 = deps.cv2
    return getattr(cv2, "INTER_AREA", getattr(cv2, "INTER_LINEAR", 1))


def locate(frame_gray, template: Template):
    """2단계 매칭: 축소본으로 빠르게 거른 뒤 원본 ROI 에서 정밀 매칭.

    반환: ((중심 x, 중심 y), 점수) — 못 찾으면 (None, 점수).
    좌표는 **정규화 프레임 기준**이다(그대로 to_client 에 넣으면 된다).
    """

    cv2 = deps.cv2
    if cv2 is None or frame_gray is None or template.gray is None:
        return None, 0.0

    target_h, target_w = template.gray.shape[:2]
    screen_h, screen_w = frame_gray.shape[:2]
    if target_w > screen_w or target_h > screen_h:
        return None, 0.0

    def remember(x: int, y: int) -> None:
        template._last_match = ((screen_h, screen_w), (int(x), int(y)))
        template._last_match_misses = 0

    # 직전에 찾은 자리부터 본다. 게임 UI 는 같은 해상도에서 같은 위치에 뜬다.
    cached = template._last_match
    if cached is not None and cached[0] == (screen_h, screen_w):
        cx, cy = cached[1]
        margin = max(12, max(target_w, target_h) // 3)
        x1 = max(0, cx - margin)
        y1 = max(0, cy - margin)
        x2 = min(screen_w, cx + target_w + margin)
        y2 = min(screen_h, cy + target_h + margin)
        roi = frame_gray[y1:y2, x1:x2]
        if roi.shape[1] >= target_w and roi.shape[0] >= target_h:
            try:
                result = cv2.matchTemplate(roi, template.gray, cv2.TM_CCOEFF_NORMED)
                _, best, _, location = cv2.minMaxLoc(result)
                if float(best) >= template.threshold:
                    top_x = location[0] + x1
                    top_y = location[1] + y1
                    remember(top_x, top_y)
                    return (top_x + target_w // 2, top_y + target_h // 2), float(best)
            except cv2.error:
                pass
        template._last_match_misses += 1
        if template._last_match_misses >= 3:
            template._last_match = None
    elif cached is not None:
        template._last_match = None
        template._last_match_misses = 0

    factor = DOWNSCALE_FACTOR
    small_w = target_w // factor
    small_h = target_h // factor

    def full_scan():
        result = cv2.matchTemplate(frame_gray, template.gray, cv2.TM_CCOEFF_NORMED)
        _, best, _, location = cv2.minMaxLoc(result)
        score = float(best)
        if score < template.threshold:
            return None, score
        remember(location[0], location[1])
        return (
            location[0] + target_w // 2,
            location[1] + target_h // 2,
        ), score

    if small_w < 4 or small_h < 4:
        return full_scan()

    if template.small is None:
        template.small = cv2.resize(
            template.gray, (small_w, small_h), interpolation=_inter_area()
        )
    small_screen = cv2.resize(
        frame_gray,
        (screen_w // factor, screen_h // factor),
        interpolation=_inter_area(),
    )
    try:
        rough = cv2.matchTemplate(small_screen, template.small, cv2.TM_CCOEFF_NORMED)
        _, rough_best, _, rough_loc = cv2.minMaxLoc(rough)
    except cv2.error:
        return None, 0.0

    # 1차 게이트는 조금 낮춘다 — 축소하면 상관도가 떨어지므로. 정밀도는 ROI 가 본다.
    if float(rough_best) < template.threshold * 0.85:
        return None, float(rough_best)

    rough_x = rough_loc[0] * factor
    rough_y = rough_loc[1] * factor
    margin = max(target_w, target_h) // 2
    x1 = max(0, rough_x - margin)
    y1 = max(0, rough_y - margin)
    x2 = min(screen_w, rough_x + target_w + margin)
    y2 = min(screen_h, rough_y + target_h + margin)
    if x2 - x1 < target_w or y2 - y1 < target_h:
        return full_scan()

    try:
        result = cv2.matchTemplate(
            frame_gray[y1:y2, x1:x2], template.gray, cv2.TM_CCOEFF_NORMED
        )
        _, best, _, location = cv2.minMaxLoc(result)
    except cv2.error:
        return None, 0.0

    score = float(best)
    if score < template.threshold:
        return None, score
    top_x = location[0] + x1
    top_y = location[1] + y1
    remember(top_x, top_y)
    return (top_x + target_w // 2, top_y + target_h // 2), score


# ─── 창 하나를 붙잡고 보는 객체 ─────────────────────────────────────────────


class Watcher:
    """대상 창 하나를 캡처하고 좌표를 변환한다."""

    def __init__(self, hwnd: int, logger: Optional[Logger] = None) -> None:
        self.hwnd = int(hwnd)
        self.logger = logger or _silent
        self.engine = wgc_engine(self.hwnd, self.logger)
        self.capture_scale = 1.0
        self._first_frame_seen = False

    # 캡처 시작은 지연시킨다 — 객체를 만들자마자 세션이 열리면 필요 없는
    # 순간에도 캡처 표시가 뜬다.
    def start(self) -> bool:
        if self.engine is None:
            return False
        return self.engine.start()

    def stop(self) -> None:
        if self.engine is not None:
            self.engine.stop()

    def alive(self) -> bool:
        return window_alive(self.hwnd)

    def frame(self, timeout: float = 0.1):
        """새 grayscale 프레임(정규화 완료). 새 프레임이 없으면 None.

        None 은 '화면이 그대로'라는 뜻이다 — 같은 픽셀을 다시 매칭하는 것은
        순수 낭비이므로 호출부는 그냥 건너뛰면 된다.
        """

        if self.engine is None:
            return None
        if self.engine.closed_event.is_set():
            return None
        gray = self.engine.latest(
            timeout=timeout if self._first_frame_seen else max(timeout, 2.0)
        )
        if gray is None or gray.ndim != 2:
            return None
        self._first_frame_seen = True
        return self._normalize(gray)

    def _normalize(self, gray):
        cv2 = deps.cv2
        height, width = gray.shape[:2]
        scale = capture_normalization_scale(height)
        self.capture_scale = scale
        if scale == 1.0 or cv2 is None:
            return gray
        try:
            # 템플릿 축소와 같은 INTER_AREA 를 써야 상관도가 맞는다.
            return cv2.resize(
                gray,
                (max(1, int(round(width / scale))), max(1, int(round(height / scale)))),
                interpolation=cv2.INTER_AREA,
            )
        except Exception:
            self.capture_scale = 1.0
            return gray

    def client_size(self) -> Optional[tuple[int, int]]:
        gui = deps.win32gui
        if gui is None:
            return None
        try:
            left, top, right, bottom = gui.GetClientRect(self.hwnd)
        except Exception:
            return None
        width = int(right - left)
        height = int(bottom - top)
        if width <= 0 or height <= 0:
            return None
        return width, height

    def to_client(self, x: int, y: int) -> Optional[tuple[int, int]]:
        """정규화 프레임 좌표 → 창 클라이언트 좌표.

        캡처가 클라이언트 영역과 같은 크기면 그대로 쓰고, 제목 표시줄/테두리를
        포함한 크기면 캡처 원점과 ClientToScreen(0,0) 차이를 빼서 보정한다.
        """

        gui = deps.win32gui
        if gui is None:
            return None
        try:
            if not gui.IsWindow(self.hwnd):
                return None

            # 먼저 실제 캡처 프레임 좌표로 되돌린다.
            scale = self.capture_scale
            if scale != 1.0:
                x = int(round(x * scale))
                y = int(round(y * scale))

            size = self.client_size()
            if size is None:
                return None
            client_w, client_h = size

            frame_size = self.engine.frame_size() if self.engine is not None else None
            if frame_size is None or _same_size(frame_size, (client_w, client_h)):
                client_x, client_y = int(x), int(y)
            else:
                screen_x, screen_y = gui.ClientToScreen(self.hwnd, (0, 0))
                origin = _capture_origin(self.hwnd, frame_size)
                if origin is None:
                    client_x, client_y = int(x), int(y)
                else:
                    client_x = int(round(x + origin[0] - screen_x))
                    client_y = int(round(y + origin[1] - screen_y))

            if not (0 <= client_x < client_w and 0 <= client_y < client_h):
                return None
            return client_x, client_y
        except Exception:
            return None


def wgc_engine(hwnd: int, logger: Logger):
    """캡처 엔진을 만든다. 의존성이 없으면 None(기능만 꺼진다)."""

    if not deps.vision_ready():
        return None
    from mpauseapp.wgc import CaptureEngine

    return CaptureEngine(hwnd, logger=logger)


def wait_for_window(timeout: float, cancel=None, poll: float = 0.25) -> Optional[int]:
    """대상 창이 나타날 때까지 기다린다(취소 가능)."""

    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if cancel is not None and cancel.is_set():
            return None
        hwnd = find_window()
        if hwnd:
            return hwnd
        if time.monotonic() >= deadline:
            return None
        if cancel is not None:
            cancel.wait(poll)
        else:
            time.sleep(poll)
