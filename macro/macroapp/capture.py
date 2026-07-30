from __future__ import annotations
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import cv2
import numpy as np

from macroapp import winapi
from macroapp.border_mask import CaptureBorderMask
from macroapp.logging_util import LogCallback


@dataclass(frozen=True)
class CapturedFrame:
    """소비된 WGC 프레임과 신선도 검증용 메타데이터."""

    image: np.ndarray
    sequence_id: int
    captured_at: float


def _supports_native_borderless_wgc() -> bool:
    """WGC IsBorderRequired가 도입된 Windows 빌드인지 확인합니다."""

    if not hasattr(sys, "getwindowsversion"):
        return False
    try:
        return int(sys.getwindowsversion().build) >= 20348
    except Exception:
        return False


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
        self.border_mask: Optional[CaptureBorderMask] = None
        self.frame_lock = threading.Lock()
        self.first_frame_event = threading.Event()
        self.frame_ready_event = threading.Event()
        self.closed_event = threading.Event()
        self.started = False
        self.logged_first_frame = False
        self._frame_seq = 0
        self._last_consumed_seq = -1
        self._latest_frame_timestamp = 0.0
        self._replaced_frame_count = 0
        # 갱신 감시처럼 화면의 극히 일부만 필요한 경우에는 BGRA 전체 프레임을
        # grayscale로 바꾸지 않고 이 영역만 변환합니다. 좌표는 WGC 프레임 기준입니다.
        self.capture_region: Optional[tuple[int, int, int, int]] = None

    def log(self, message: str) -> None:
        """콘솔 또는 GUI 로그 영역으로 메시지를 보냅니다."""

        self.logger(message)

    def on_frame_arrived(self, frame: winapi.Frame, _capture_control: Any = None) -> None:
        """WGC 프레임을 grayscale 단일 슬롯에 넣고 밀린 프레임은 버립니다."""

        try:
            image_bgra = np.asarray(frame.frame_buffer)
            if image_bgra.size == 0:
                return
            if image_bgra.ndim != 3 or image_bgra.shape[2] < 3:
                return

            with self.frame_lock:
                capture_region = self.capture_region

            if capture_region is not None:
                x1, y1, x2, y2 = capture_region
                frame_height, frame_width = image_bgra.shape[:2]
                x1 = max(0, min(int(x1), frame_width - 1))
                y1 = max(0, min(int(y1), frame_height - 1))
                x2 = max(x1 + 1, min(int(x2), frame_width))
                y2 = max(y1 + 1, min(int(y2), frame_height))
                image_bgra = image_bgra[y1:y2, x1:x2]

            # BGRA 전체 복사(픽셀당 4바이트) 대신 독립된 grayscale(1바이트)만 생성합니다.
            # capture_region이 있으면 수십 픽셀 높이만 변환하므로 CPU/RAM 대역폭도 크게 줄어듭니다.
            color_code = cv2.COLOR_BGRA2GRAY if image_bgra.shape[2] >= 4 else cv2.COLOR_BGR2GRAY
            gray = cv2.cvtColor(image_bgra, color_code)
            with self.frame_lock:
                # 큐를 쌓지 않고 아직 소비되지 않은 이전 프레임을 최신 프레임으로
                # 덮어씁니다. 판정이 한 프레임 늦어져도 과거 화면을 처리하지 않습니다.
                if self.latest_frame is not None:
                    self._replaced_frame_count += 1
                self.latest_frame = gray
                self.last_frame_size = (int(frame.width), int(frame.height))
                self._frame_seq += 1
                self._latest_frame_timestamp = time.perf_counter()
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
            self._latest_frame_timestamp = 0.0
            self._replaced_frame_count = 0

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

        def start_compatibility_mask() -> None:
            self.border_mask = CaptureBorderMask(self.hwnd, logger=self.log)
            if not self.border_mask.start():
                self.border_mask = None

        try:
            if _supports_native_borderless_wgc():
                try:
                    start_with_options({**capture_kwargs, "draw_border": False})
                except Exception as exc:
                    error_text = str(exc).lower()
                    if "capture border" not in error_text and "draw_border" not in error_text:
                        raise

                    # API가 노출됐지만 드라이버/플랫폼에서 거부되는 경우에도 WGC
                    # 인식은 유지하고 시각 마스크로 표시만 가립니다.
                    self.capture = None
                    self.capture_control = None
                    start_compatibility_mask()
                    start_with_options(capture_kwargs)
            else:
                # Windows 10 22H2(19045)는 IsBorderRequired가 없으므로 false를
                # 전달하면 E_NOINTERFACE로 실패합니다. WGC를 그대로 쓰되 표시만 가립니다.
                start_compatibility_mask()
                start_with_options(capture_kwargs)

            self.started = True
            return True
        except Exception as exc:
            self.log(f"[캡처 오류] WGC 캡처 세션을 시작하지 못했습니다: {exc}")
            if self.border_mask is not None:
                self.border_mask.stop()
                self.border_mask = None
            self.capture = None
            self.capture_control = None
            self.started = False
            return False

    def get_latest_frame_packet(
        self,
        timeout: float = 0.0,
    ) -> Optional[CapturedFrame]:
        """소비되지 않은 최신 grayscale 프레임과 순번을 반환합니다."""

        if timeout > 0 and not self.frame_ready_event.wait(timeout):
            return None

        with self.frame_lock:
            if self.latest_frame is None:
                self.frame_ready_event.clear()
                return None
            frame = self.latest_frame
            sequence_id = self._frame_seq
            captured_at = self._latest_frame_timestamp
            self.latest_frame = None
            self._last_consumed_seq = sequence_id
            self.frame_ready_event.clear()
            return CapturedFrame(frame, sequence_id, captured_at)

    def get_latest_frame(self, timeout: float = 0.0) -> Optional[np.ndarray]:
        """기존 호출부 호환용: 소비되지 않은 최신 grayscale 이미지만 반환합니다."""

        packet = self.get_latest_frame_packet(timeout=timeout)
        return None if packet is None else packet.image

    def get_frame_size(self) -> Optional[tuple[int, int]]:
        """최신 WGC 프레임의 (width, height)를 반환합니다."""

        with self.frame_lock:
            return self.last_frame_size

    def get_replaced_frame_count(self) -> int:
        """소비 전에 더 최신 프레임으로 교체된 누적 횟수입니다."""

        with self.frame_lock:
            return int(self._replaced_frame_count)

    def set_capture_region(
        self,
        region: Optional[tuple[int, int, int, int]],
    ) -> None:
        """이후 프레임에서 grayscale로 변환할 WGC 영역을 지정합니다.

        None이면 전체 프레임을 반환합니다. 영역을 바꾸는 순간 대기 중인 이전 크기의
        프레임은 버려 소비자가 서로 다른 좌표계의 배열을 섞어 받지 않게 합니다.
        """

        normalized = None
        if region is not None:
            x1, y1, x2, y2 = (int(value) for value in region)
            if x2 <= x1 or y2 <= y1:
                raise ValueError("캡처 영역은 x1<x2, y1<y2여야 합니다.")
            normalized = (x1, y1, x2, y2)

        with self.frame_lock:
            self.capture_region = normalized
            self.latest_frame = None
            self.frame_ready_event.clear()

    def stop_capture(self) -> None:
        """실행 중인 WGC 세션을 안전하게 중지합니다."""

        capture_control = self.capture_control
        border_mask = self.border_mask
        self.capture_control = None
        self.border_mask = None
        self.capture = None
        self.started = False
        self.first_frame_event.clear()
        self.frame_ready_event.clear()

        with self.frame_lock:
            self.latest_frame = None
            self.last_frame_size = None
            self.capture_region = None
            self._latest_frame_timestamp = 0.0

        if capture_control is None:
            if border_mask is not None:
                border_mask.stop()
            return

        try:
            if not capture_control.is_finished():
                capture_control.stop()
        except Exception as exc:
            self.log(f"[주의] WGC 캡처 세션 정리 중 문제가 발생했습니다: {exc}")
        finally:
            if border_mask is not None:
                border_mask.stop()
