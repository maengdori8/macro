from __future__ import annotations
import threading
from typing import Any, Optional

import cv2
import numpy as np

from macroapp import winapi
from macroapp.logging_util import LogCallback
class WGCCaptureEngine:
    """windows-capture 기반 비동기 WGC 창 캡처 엔진입니다."""

    def __init__(self, hwnd: int, logger: Optional[LogCallback] = None):
        self.hwnd = int(hwnd)
        self.logger = logger or print
        # 아직 소비되지 않은 최신 grayscale 프레임 하나만 유지합니다.
        self.latest_frame: Optional[np.ndarray] = None
        self.last_frame_size: Optional[tuple[int, int]] = None
        self.capture: Optional[Any] = None
        self.capture_control: Optional[Any] = None
        self.frame_lock = threading.Lock()
        self.first_frame_event = threading.Event()
        self.frame_ready_event = threading.Event()
        self.closed_event = threading.Event()
        self.started = False
        self.logged_first_frame = False
        self._frame_seq = 0
        self._last_consumed_seq = -1

    def log(self, message: str) -> None:
        """콘솔 또는 GUI 로그 영역으로 메시지를 보냅니다."""

        self.logger(message)

    def on_frame_arrived(self, frame: winapi.Frame, _capture_control: Any = None) -> None:
        """WGC 프레임을 grayscale 단일 슬롯에 넣고 밀린 프레임은 버립니다."""

        try:
            # 소비자가 아직 이전 프레임을 처리 중이면 새 전체 프레임 복사/변환을 생략합니다.
            # 큐를 쌓지 않고 가장 이른 다음 프레임을 받으므로 RAM과 지연이 함께 제한됩니다.
            with self.frame_lock:
                if self.latest_frame is not None:
                    return

            image_bgra = np.asarray(frame.frame_buffer)
            if image_bgra.size == 0:
                return
            if image_bgra.ndim != 3 or image_bgra.shape[2] < 3:
                return

            # BGRA 전체 복사(픽셀당 4바이트) 대신 독립된 grayscale(1바이트)만 생성합니다.
            color_code = cv2.COLOR_BGRA2GRAY if image_bgra.shape[2] >= 4 else cv2.COLOR_BGR2GRAY
            gray = cv2.cvtColor(image_bgra, color_code)
            with self.frame_lock:
                if self.latest_frame is not None:
                    return
                self.latest_frame = gray
                self.last_frame_size = (int(frame.width), int(frame.height))
                self._frame_seq += 1
                self.frame_ready_event.set()

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
        self.frame_ready_event.set()  # 대기 중인 소비자를 즉시 깨워 종료 상태를 확인시킵니다.
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
        self.frame_ready_event.clear()
        self.closed_event.clear()
        self.logged_first_frame = False

        with self.frame_lock:
            self.latest_frame = None
            self.last_frame_size = None

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
        """소비되지 않은 최신 grayscale 프레임을 반환합니다. 없으면 None."""

        if timeout > 0 and not self.frame_ready_event.wait(timeout):
            return None

        with self.frame_lock:
            if self.latest_frame is None:
                self.frame_ready_event.clear()
                return None
            frame = self.latest_frame
            self.latest_frame = None
            self._last_consumed_seq = self._frame_seq
            self.frame_ready_event.clear()
            return frame

    def get_frame_size(self) -> Optional[tuple[int, int]]:
        """최신 WGC 프레임의 (width, height)를 반환합니다."""

        with self.frame_lock:
            return self.last_frame_size

    def stop_capture(self) -> None:
        """실행 중인 WGC 세션을 안전하게 중지합니다."""

        capture_control = self.capture_control
        self.capture_control = None
        self.capture = None
        self.started = False
        self.first_frame_event.clear()
        self.frame_ready_event.clear()

        with self.frame_lock:
            self.latest_frame = None
            self.last_frame_size = None

        if capture_control is None:
            return

        try:
            if not capture_control.is_finished():
                capture_control.stop()
        except Exception as exc:
            self.log(f"[주의] WGC 캡처 세션 정리 중 문제가 발생했습니다: {exc}")
