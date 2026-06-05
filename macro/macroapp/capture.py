from __future__ import annotations
import threading
import time
from typing import Any, Optional

import cv2
import numpy as np

from macroapp import winapi
from macroapp.logging_util import LogCallback
from macroapp.config import WGC_FIRST_FRAME_TIMEOUT_SECONDS

class WGCCaptureEngine:
    """windows-capture 기반 비동기 WGC 창 캡처 엔진입니다."""

    def __init__(self, hwnd: int, logger: Optional[LogCallback] = None):
        self.hwnd = int(hwnd)
        self.logger = logger or print
        self.latest_frame: Optional[np.ndarray] = None
        self.capture: Optional[Any] = None
        self.capture_control: Optional[Any] = None
        self.frame_lock = threading.Lock()
        self.first_frame_event = threading.Event()
        self.closed_event = threading.Event()
        self.started = False
        self.logged_first_frame = False
        self._frame_seq = 0
        self._last_consumed_seq = -1

    def log(self, message: str) -> None:
        """콘솔 또는 GUI 로그 영역으로 메시지를 보냅니다."""

        self.logger(message)

    def on_frame_arrived(self, frame: winapi.Frame, _capture_control: Any = None) -> None:
        """WGC 캡처 스레드에서 프레임이 도착할 때마다 최신 BGRA 배열을 저장합니다."""

        try:
            image_bgra = np.asarray(frame.frame_buffer)
            if image_bgra.size == 0:
                return

            frame_copy = image_bgra.copy()
            with self.frame_lock:
                self.latest_frame = frame_copy
                self._frame_seq += 1

            self.first_frame_event.set()
            if not self.logged_first_frame:
                self.logged_first_frame = True
                self.log(
                    f"[캡처] WGC {int(frame.width)}x{int(frame.height)}"
                )
        except Exception as exc:
            self.log(f"[캡처 오류] WGC 프레임 처리 중 문제가 발생했습니다: {exc}")

    def on_closed(self) -> None:
        """WGC 캡처 세션이 닫혔을 때 호출됩니다."""

        self.closed_event.set()
        self.log("[캡처 종료] WGC 캡처 세션이 닫혔습니다.")

    def start_capture(self) -> bool:
        """WindowsCapture를 블로킹 없이 전용 캡처 스레드에서 시작합니다."""

        if winapi.WindowsCapture is None:
            self.log("[오류] windows-capture 모듈을 불러올 수 없습니다.")
            self.log("       Windows 환경에서 requirements.txt를 다시 설치하세요.")
            self.log(f"       원본 오류: {winapi.WINDOWS_CAPTURE_IMPORT_ERROR}")
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

        capture_kwargs: dict[str, object] = {
            "cursor_capture": False,
            "monitor_index": None,
            "window_name": None,
            "window_hwnd": self.hwnd,
        }

        def start_with_options(options: dict[str, object]) -> None:
            self.capture = winapi.WindowsCapture(**options)
            self.capture.event(self.on_frame_arrived)
            self.capture.event(self.on_closed)
            self.capture_control = self.capture.start_free_threaded()

        try:
            try:
                start_with_options({**capture_kwargs, "draw_border": False})
            except Exception as exc:
                error_text = str(exc).lower()
                if "capture border" not in error_text and "draw_border" not in error_text:
                    raise

                self.log(
                    "[캡처 안내] 현재 플랫폼이 WGC 캡처 테두리 토글을 지원하지 않아 "
                    "draw_border 옵션 없이 다시 시도합니다."
                )
                self.capture = None
                self.capture_control = None
                start_with_options(capture_kwargs)

            self.started = True
            return True
        except Exception as exc:
            self.log(f"[캡처 오류] WGC 캡처 세션을 시작하지 못했습니다: {exc}")
            self.capture = None
            self.capture_control = None
            self.started = False
            return False

    def get_latest_frame(self, timeout: float = 0.0) -> Optional[np.ndarray]:
        """최신 WGC 프레임을 BGRA NumPy 배열로 반환합니다. 새 프레임이 없으면 None."""

        if timeout > 0 and not self.first_frame_event.wait(timeout):
            return None

        with self.frame_lock:
            if self.latest_frame is None:
                return None
            if self._frame_seq == self._last_consumed_seq:
                return None  # 동일 프레임 재처리 방지
            self._last_consumed_seq = self._frame_seq
            return self.latest_frame

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
