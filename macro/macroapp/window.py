from __future__ import annotations
import ctypes
import os
import platform
import time
from typing import Optional

import numpy as np

from macroapp import winapi
from macroapp.logging_util import LogCallback
from macroapp.config import (
    TargetImage,
    DWMWA_EXTENDED_FRAME_BOUNDS,
    FC_ONLINE_PROCESS_NAMES,
    WGC_CAPTURE_MAX_FPS,
    WGC_FRAME_WAIT_SECONDS,
    WGC_FIRST_FRAME_TIMEOUT_SECONDS,
)
from macroapp.capture import WGCCaptureEngine
from macroapp.input_message import (
    _find_child_window, _get_child_windows, _get_class_name_safe,
    _get_thread_focus_hwnd, _get_window_text_safe, _make_command_wparam,
    _make_mouse_lparam, _resolve_win32_message, _send_win32_message,
    _unique_hwnds, post_mouse_click, post_mouse_down, post_mouse_up,
)
from macroapp.input_mouse import post_curved_click

# ctypes 함수 시그니처를 1회만 설정하기 위한 플래그 (창마다 반복 설정 방지).
_KERNEL32_SIG_READY = False
_USER32_GWTPI_READY = False


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

    if winapi.win32gui is None:
        return None

    extended_frame = _get_extended_frame_bounds(hwnd)
    window_rect = winapi.win32gui.GetWindowRect(hwnd)

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

    if winapi.win32gui is None:
        log("[좌표 오류] pywin32 win32gui 모듈이 필요합니다.")
        return None
    if not winapi.win32gui.IsWindow(hwnd):
        log(f"[좌표 오류] 유효하지 않은 HWND입니다: {hwnd}")
        return None

    x = int(x)
    y = int(y)

    try:
        left, top, right, bottom = winapi.win32gui.GetClientRect(hwnd)
        client_width = int(right - left)
        client_height = int(bottom - top)
        if client_width <= 0 or client_height <= 0:
            log("[좌표 오류] 대상 창의 클라이언트 영역 크기가 0입니다.")
            return None

        if frame_size is None or _same_size(frame_size, (client_width, client_height)):
            client_x, client_y = x, y
        else:
            client_screen_x, client_screen_y = winapi.win32gui.ClientToScreen(hwnd, (0, 0))
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

        return client_x, client_y
    except Exception as exc:
        log(f"[좌표 오류] WGC 좌표를 클라이언트 좌표로 변환하지 못했습니다: {exc}")
        return None

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
        self._received_frame = False

        if winapi.WIN32_IMPORT_ERROR is not None:
            self.log("[오류] pywin32 모듈을 불러올 수 없습니다.")
            self.log("       이 프로그램은 Windows + pywin32 환경에서 실행해야 합니다.")
            self.log(f"       원본 오류: {winapi.WIN32_IMPORT_ERROR}")

        if winapi.WINDOWS_CAPTURE_IMPORT_ERROR is not None:
            self.log("[오류] windows-capture 모듈을 불러올 수 없습니다.")
            self.log("       WGC 캡처에는 Windows + windows-capture 환경이 필요합니다.")
            self.log(f"       원본 오류: {winapi.WINDOWS_CAPTURE_IMPORT_ERROR}")

        if platform.system() != "Windows":
            self.log("[주의] 현재 OS는 Windows가 아닙니다.")
            self.log("       코드는 작성/검토할 수 있지만 실제 캡처와 클릭은 Windows에서 실행하세요.")

    def log(self, message: str) -> None:
        """콘솔 또는 GUI 로그 영역으로 메시지를 보냅니다."""

        self.logger(message)

    @staticmethod
    def _get_pid_process_name(pid: int) -> Optional[str]:
        """PID로 프로세스 실행 파일 이름을 반환합니다."""
        if not hasattr(ctypes, "windll"):
            return None
        try:
            from ctypes import wintypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            # ctypes 시그니처는 창마다 반복 설정하지 않고 1회만 지정합니다.
            # (64비트에서 기본 restype(c_int)은 HANDLE을 잘라먹으므로 명시 필요.)
            global _KERNEL32_SIG_READY
            if not _KERNEL32_SIG_READY:
                kernel32.OpenProcess.restype = wintypes.HANDLE
                kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
                kernel32.QueryFullProcessImageNameW.argtypes = [
                    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
                ]
                kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                _KERNEL32_SIG_READY = True
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
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

    def _is_fc_online_process(self, hwnd: int) -> bool:
        """HWND가 FC Online 프로세스의 창인지 확인합니다."""
        if not hasattr(ctypes, "windll"):
            return False
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            global _USER32_GWTPI_READY
            if not _USER32_GWTPI_READY:
                user32.GetWindowThreadProcessId.restype = wintypes.DWORD
                user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
                _USER32_GWTPI_READY = True
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
            if pid.value == 0:
                return False
            proc_name = self._get_pid_process_name(pid.value)
            if proc_name is None:
                return False
            proc_name_lower = proc_name.replace(".exe", "")
            for fc_name in FC_ONLINE_PROCESS_NAMES:
                if fc_name in proc_name_lower:
                    return True
        except Exception:
            pass
        return False

    def find_window(self) -> bool:
        """
        FC Online 프로세스의 창을 찾습니다.

        프로세스 이름 기반으로 검색하여 다른 프로그램과 혼동하지 않습니다.
        프로세스로 못 찾으면 창 제목 기반 검색으로 폴백합니다.
        """

        if not self._win32_ready():
            return False

        matches: list[tuple[int, str]] = []
        minimized_matches: list[tuple[int, str]] = []
        title_matches: list[tuple[int, str]] = []

        keyword = self.window_title.strip().lower()

        def enum_handler(hwnd: int, _extra: object) -> bool:
            # EnumWindows 콜백은 True를 반환해야 열거가 계속됩니다.
            if not winapi.win32gui.IsWindow(hwnd):
                return True
            if not winapi.win32gui.IsWindowVisible(hwnd):
                return True

            title = winapi.win32gui.GetWindowText(hwnd).strip()
            if not title:
                return True

            # 프로세스 이름으로 FC Online 확인
            is_fc = self._is_fc_online_process(hwnd)

            if is_fc:
                if winapi.win32gui.IsIconic(hwnd):
                    minimized_matches.append((hwnd, title))
                else:
                    matches.append((hwnd, title))
            elif keyword and keyword in title.lower():
                # 프로세스 매칭 실패 시 제목 폴백용
                if not winapi.win32gui.IsIconic(hwnd):
                    title_matches.append((hwnd, title))
            return True

        try:
            winapi.win32gui.EnumWindows(enum_handler, None)
        except Exception as exc:
            self.log(f"[오류] 창 검색 중 문제가 발생했습니다: {exc}")
            return False

        self.log("[창 검색] FC Online 프로세스를 검색했습니다.")

        if minimized_matches:
            self.log("[제외] 최소화된 창은 지원하지 않아 제외했습니다.")
            for hwnd, title in minimized_matches:
                self.log(f"       HWND={hwnd}, 제목='{title}'")

        # 프로세스 매칭 우선, 없으면 제목 폴백
        if not matches and title_matches:
            self.log("[안내] 프로세스로 찾지 못해 창 제목으로 검색합니다.")
            matches = title_matches

        if not matches:
            self.log("[안내] FC Online 창을 찾지 못했습니다.")
            self.log("       게임이 실행 중인지 확인하세요.")
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
            if not winapi.win32gui.IsWindow(self.hwnd):
                self.log("[주의] 대상 HWND가 더 이상 유효하지 않습니다.")
                return False
            if not winapi.win32gui.IsWindowVisible(self.hwnd):
                self.log("[주의] 대상 창이 보이지 않는 상태입니다.")
                return False
            if winapi.win32gui.IsIconic(self.hwnd):
                self.log("[주의] 대상 창이 최소화되어 있습니다. 최소화된 창은 지원하지 않습니다.")
                return False

            left, top, right, bottom = winapi.win32gui.GetClientRect(self.hwnd)
            width = right - left
            height = bottom - top
            if width <= 0 or height <= 0:
                self.log("[주의] 대상 창의 클라이언트 영역 크기가 0입니다.")
                return False

            return True
        except Exception as exc:
            self.log(f"[주의] 대상 창 상태 확인 중 오류가 발생했습니다: {exc}")
            return False

    def capture_client_area(self, *, window_validated: bool = False) -> Optional[np.ndarray]:
        """
        WGC 최신 프레임을 OpenCV 템플릿 매칭용 GrayScale 배열로 반환합니다.

        windows-capture 콜백이 소비되지 않은 프레임 하나만 grayscale로 변환하므로,
        여기서는 추가 전체 프레임 복사나 색상 변환 없이 배열을 넘깁니다.
        """

        if not window_validated and not self.is_valid_window():
            self.stop_capture()
            return None

        if self.hwnd is None:
            self.log("[오류] WGC 캡처를 시작할 대상 HWND가 없습니다.")
            return None

        if self.capture_engine is None:
            self.capture_engine = WGCCaptureEngine(
                self.hwnd,
                logger=self.log,
                max_fps=WGC_CAPTURE_MAX_FPS,
            )

        if not self.capture_engine.start_capture():
            return None

        if self.capture_engine.closed_event.is_set():
            self.log("[주의] WGC 캡처 세션이 닫혔습니다. 대상 창을 다시 찾습니다.")
            self.stop_capture()
            return None

        gray = self.capture_engine.get_latest_frame(
            timeout=(
                WGC_FRAME_WAIT_SECONDS
                if self._received_frame
                else WGC_FIRST_FRAME_TIMEOUT_SECONDS
            ),
        )
        if gray is None:
            # 새 프레임이 없으면(화면 정지) None을 반환해 재매칭을 건너뜁니다.
            # 같은 픽셀은 매칭 결과도 같으므로 재매칭은 순수 낭비이며,
            # 새 프레임이 도착하면 그때 다시 매칭합니다. → 정지 화면 CPU ~0.
            if not self._received_frame:
                self._log_capture_wait("[대기] WGC 첫 프레임을 아직 받지 못했습니다.")
            return None

        if gray.ndim != 2:
            self.log(f"[캡처 오류] 지원하지 않는 WGC 프레임 형태입니다: {gray.shape}")
            return None

        sampled = gray[::16, ::16]
        if sampled.max() == 0:
            self._log_capture_wait("[캡처 오류] WGC 캡처 이미지가 완전히 검은색입니다.")
            return None

        self._received_frame = True
        return gray

    def stop_capture(self) -> None:
        """실행 중인 WGC 캡처 엔진을 정리합니다."""

        if self.capture_engine is None:
            return

        throttled = self.capture_engine.get_throttled_frame_count()
        replaced = self.capture_engine.get_replaced_frame_count()
        if throttled or replaced:
            self.log(
                f"[성능] WGC 색 변환 전 폐기 {throttled:,}프레임 · "
                f"최신 프레임 교체 {replaced:,}회"
            )
        self.capture_engine.stop_capture()
        self.capture_engine = None
        self._received_frame = False

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
                left, top, right, bottom = winapi.win32gui.GetClientRect(self.hwnd)
                start_client = (
                    max(0, int(right - left) // 2),
                    max(0, int(bottom - top) // 2),
                )
            except Exception:
                start_client = end_client

        try:
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

    def _resolve_message_control_hwnd(self, target: TargetImage) -> Optional[int]:
        """타겟 설정에 맞는 자식 컨트롤 HWND를 찾습니다."""

        if self.hwnd is None:
            return None

        return _find_child_window(
            self.hwnd,
            control_id=target.control_id,
            control_hwnd=target.control_hwnd,
            control_class=target.control_class,
            control_text=target.control_text,
        )

    def _resolve_message_targets(self, target: TargetImage) -> list[int]:
        """Win32 메시지를 보낼 HWND 목록을 만듭니다."""

        if self.hwnd is None:
            return []

        message_target = target.message_target.strip().lower()
        if message_target == "top":
            return [self.hwnd]
        if message_target == "focus":
            return [_get_thread_focus_hwnd(self.hwnd) or self.hwnd]
        if message_target == "all":
            return _unique_hwnds([self.hwnd] + _get_child_windows(self.hwnd))
        if message_target == "control":
            control_hwnd = self._resolve_message_control_hwnd(target)
            return [control_hwnd] if control_hwnd is not None else []

        raise ValueError(f"지원하지 않는 message_target입니다: {message_target!r}")

    def send_win32_message_action(self, target: TargetImage) -> bool:
        """targets.json의 message 액션을 대상 창에 전송합니다."""

        if not self.is_valid_window() or self.hwnd is None:
            return False
        if not target.message:
            self.log(f"[메시지 오류] {target.name}에 message가 설정되지 않았습니다.")
            return False

        try:
            message_id = _resolve_win32_message(target.message)
            constants = winapi._require_win32con()
            use_send_message = target.message_mode == "sendmessage"
            target_hwnds = self._resolve_message_targets(target)

            if not target_hwnds:
                self.log(
                    f"[메시지 오류] {target.name}의 message_target={target.message_target}에 "
                    "해당하는 HWND를 찾지 못했습니다."
                )
                if target.control_id is not None or target.control_class or target.control_text:
                    self.log(
                        f"       control_id={target.control_id}, "
                        f"control_class={target.control_class}, control_text={target.control_text}"
                    )
                    child_hwnds = _get_child_windows(self.hwnd, limit=20)
                    if child_hwnds:
                        self.log("[메시지 안내] 찾은 자식 HWND 후보:")
                        for child_hwnd in child_hwnds:
                            self.log(
                                f"       HWND={child_hwnd}, "
                                f"class='{_get_class_name_safe(child_hwnd)}', "
                                f"text='{_get_window_text_safe(child_hwnd)}'"
                            )
                return False

            control_hwnd = self._resolve_message_control_hwnd(target)
            wparam = target.message_wparam if target.message_wparam is not None else 0
            lparam = target.message_lparam if target.message_lparam is not None else 0

            if message_id == int(constants.WM_COMMAND):
                if target.command_id is not None:
                    wparam = _make_command_wparam(target.command_id, target.notify_code)
                if target.message_lparam is None and control_hwnd is not None:
                    lparam = control_hwnd

            sent_count = 0
            for target_hwnd in target_hwnds:
                try:
                    _send_win32_message(
                        target_hwnd,
                        message_id,
                        wparam,
                        lparam,
                        use_send_message=use_send_message,
                    )
                    sent_count += 1
                except Exception:
                    pass

            if sent_count <= 0:
                self.log(f"[오류] {target.name} 메시지 전송 실패")
                return False

            self.log(f"[메시지] {target.name} {target.message}")
            return True
        except Exception as exc:
            self.log(f"[메시지 오류] Win32 메시지 액션 중 문제가 발생했습니다: {exc}")
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
            post_mouse_click(
                self.hwnd,
                client_x,
                client_y,
                use_send_message=use_send_message,
            )
            self.log(f"[클릭] ({client_x},{client_y})")
            return True
        except Exception as exc:
            self.log(f"[오류] 클릭 메시지 전송 중 문제가 발생했습니다: {exc}")
            return False

    def client_to_screen(self, x: int, y: int) -> Optional[tuple[int, int]]:
        """클라이언트 영역 기준 좌표를 화면 절대 좌표로 변환합니다."""

        if not self.is_valid_window():
            return None

        try:
            screen_x, screen_y = winapi.win32gui.ClientToScreen(self.hwnd, (int(x), int(y)))
            return int(screen_x), int(screen_y)
        except Exception as exc:
            self.log(f"[오류] 클라이언트 좌표를 화면 좌표로 변환하지 못했습니다: {exc}")
            return None

    def _win32_ready(self) -> bool:
        """pywin32 모듈이 정상 로드되었는지 확인합니다."""

        return winapi.WIN32_IMPORT_ERROR is None

    def _make_lparam(self, x: int, y: int) -> int:
        """
        Windows 마우스 메시지 lParam을 만듭니다.

        하위 16비트는 x 좌표, 상위 16비트는 y 좌표입니다.
        win32api.MAKELONG이 있으면 사용하고, 없으면 동일한 방식으로 직접 구성합니다.
        """

        return _make_mouse_lparam(x, y)
