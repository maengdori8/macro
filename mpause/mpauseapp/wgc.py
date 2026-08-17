"""Windows Graphics Capture 로 대상 창 한 개만 캡처한다.

mAuto(``macroapp/capture.py`` + ``macroapp/border_mask.py``)에서 이식했다.
이식한 이유는 그쪽이 이미 실측으로 다듬어진 코드이기 때문이다.

  * 비활성(백그라운드) 창도 캡처된다 — 창을 앞으로 끌어올 필요가 없다.
  * 소비되지 않은 최신 프레임 **한 장만** 들고 있는다. 큐를 쌓으면 과거 화면을
    처리하게 되고, 그건 늦은 판단보다 나쁘다.
  * Windows 10(빌드 20348 미만)은 ``IsBorderRequired`` 가 없어서 캡처 중에
    노란 테두리가 그려진다. 화면에 변화를 남기지 않기 위해 그 테두리 위에
    입력을 받지 않는 얇은 마스크 창을 덮는다.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, Callable, Optional

from mpauseapp import deps

Logger = Callable[[str], None]

_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_RGN_DIFF = 4


def _silent(_message: str) -> None:
    """기본 로거 — 아무 데도 남기지 않는다(콘솔 없는 빌드가 기본)."""


def _supports_native_borderless_wgc() -> bool:
    """WGC IsBorderRequired 가 도입된 Windows 빌드인지 확인한다."""

    if not hasattr(sys, "getwindowsversion"):
        return False
    try:
        return int(sys.getwindowsversion().build) >= 20348
    except Exception:
        return False


def _extended_frame_bounds(hwnd: int):
    """DWM 이 실제로 그리는 창 경계. 실패하면 None."""

    import ctypes

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
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:
        return None


class BorderMask:
    """캡처 표시 테두리를 가리는 '클릭 통과' 창.

    대상 창의 Z-order 바로 위에만 놓는다. 다른 창이 대상을 덮으면 마스크도 함께
    가려지고, 캡처가 끝나면 전용 스레드에서 즉시 제거된다.
    """

    def __init__(
        self,
        hwnd: int,
        logger: Optional[Logger] = None,
        *,
        thickness: int = 5,
        outer_padding: int = 2,
    ) -> None:
        self.hwnd = int(hwnd)
        self.logger = logger or _silent
        self.thickness = max(3, int(thickness))
        self.outer_padding = max(0, int(outer_padding))

        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._mask_hwnd: Optional[int] = None
        self._started = False

    def start(self, timeout: float = 1.0) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return self._started
        if deps.win32gui is None or deps.win32api is None or deps.win32con is None:
            return False
        if self.hwnd <= 0 or not deps.win32gui.IsWindow(self.hwnd):
            return False

        self._stop_event.clear()
        self._ready_event.clear()
        self._started = False
        self._thread = threading.Thread(target=self._run, name="overlay", daemon=True)
        self._thread.start()
        self._ready_event.wait(max(0.0, float(timeout)))
        return self._started

    def stop(self, timeout: float = 1.0) -> None:
        thread = self._thread
        self._thread = None
        self._stop_event.set()
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(max(0.0, float(timeout)))
        self._started = False

    def _run(self) -> None:
        gui = deps.win32gui
        api = deps.win32api
        con = deps.win32con
        if gui is None or api is None or con is None:
            self._ready_event.set()
            return

        class_name = f"UiOverlay_{os.getpid()}_{id(self):x}"
        instance = api.GetModuleHandle(None)
        brush = None
        class_registered = False

        try:
            brush = gui.CreateSolidBrush(api.RGB(0, 0, 0))
            wnd_class = gui.WNDCLASS()
            wnd_class.hInstance = instance
            wnd_class.lpszClassName = class_name
            wnd_class.hbrBackground = brush
            wnd_class.lpfnWndProc = {
                con.WM_NCHITTEST: lambda *_args: con.HTTRANSPARENT,
                con.WM_MOUSEACTIVATE: lambda *_args: con.MA_NOACTIVATE,
            }
            gui.RegisterClass(wnd_class)
            class_registered = True

            ex_style = con.WS_EX_TOOLWINDOW | con.WS_EX_NOACTIVATE | con.WS_EX_TRANSPARENT

            # 같은 무결성 수준이면 대상 창의 owned window 로 만들 수 있다. 대상이 더
            # 높은 권한이면 ERROR_ACCESS_DENIED 가 나므로 독립 창으로 폴백하고
            # 아래에서 Z-order 를 대상 바로 위로 맞춘다.
            try:
                mask_hwnd = gui.CreateWindowEx(
                    ex_style, class_name, None, con.WS_POPUP,
                    0, 0, 1, 1, self.hwnd, 0, instance, None,
                )
            except Exception:
                mask_hwnd = gui.CreateWindowEx(
                    ex_style, class_name, None, con.WS_POPUP,
                    0, 0, 1, 1, 0, 0, instance, None,
                )

            self._mask_hwnd = int(mask_hwnd)
            last_geometry: Optional[tuple[int, int, int, int]] = None
            visible = False
            first_update = True

            while not self._stop_event.is_set():
                if not first_update and self._stop_event.wait(0.20):
                    break
                try:
                    gui.PumpWaitingMessages()

                    if (
                        not gui.IsWindow(self.hwnd)
                        or not gui.IsWindowVisible(self.hwnd)
                        or gui.IsIconic(self.hwnd)
                    ):
                        if visible:
                            gui.ShowWindow(mask_hwnd, con.SW_HIDE)
                            visible = False
                        continue

                    bounds = _extended_frame_bounds(self.hwnd)
                    if bounds is None:
                        bounds = tuple(int(v) for v in gui.GetWindowRect(self.hwnd))

                    left, top, right, bottom = bounds
                    padding = self.outer_padding
                    geometry = (
                        int(left - padding),
                        int(top - padding),
                        int(right - left + padding * 2),
                        int(bottom - top + padding * 2),
                    )
                    if geometry[2] <= self.thickness * 2 or geometry[3] <= self.thickness * 2:
                        continue

                    preceding = gui.GetWindow(self.hwnd, con.GW_HWNDPREV)
                    z_order_changed = int(preceding or 0) != int(mask_hwnd)
                    geometry_changed = geometry != last_geometry

                    if geometry_changed:
                        self._set_hollow_region(mask_hwnd, geometry[2], geometry[3])

                    if geometry_changed or z_order_changed or not visible:
                        # hWndInsertAfter 는 '마스크보다 앞설 창'이다. 대상 바로 앞의
                        # 창 뒤에 넣으면 다른 앱보다 위로 튀지 않고 대상 바로 위에 온다.
                        flags = con.SWP_NOACTIVATE | con.SWP_SHOWWINDOW
                        if int(preceding or 0) == int(mask_hwnd):
                            insert_after = 0
                            flags |= con.SWP_NOZORDER
                        else:
                            insert_after = preceding if preceding else con.HWND_TOP
                        gui.SetWindowPos(mask_hwnd, insert_after, *geometry, flags)
                        gui.UpdateWindow(mask_hwnd)
                        last_geometry = geometry
                        visible = True
                except Exception:
                    # 전체 화면/창 전환 순간에는 HWND 상태와 DWM bounds 가 잠시
                    # 어긋난다. 다음 주기에 자연히 복구된다.
                    continue
                finally:
                    if first_update:
                        # 첫 배치가 끝나기 전에는 start() 가 돌아오지 않게 한다.
                        # 테두리가 잠깐이라도 보이는 시간을 최소화하기 위해서다.
                        self._started = True
                        self._ready_event.set()
                        first_update = False
        except Exception as exc:  # noqa: BLE001
            self.logger(f"[캡처] 테두리 마스크를 만들지 못했습니다: {exc}")
            self._ready_event.set()
        finally:
            mask_hwnd = self._mask_hwnd
            self._mask_hwnd = None
            if mask_hwnd is not None:
                try:
                    if gui.IsWindow(mask_hwnd):
                        gui.DestroyWindow(mask_hwnd)
                except Exception:
                    pass
            if class_registered:
                try:
                    gui.UnregisterClass(class_name, instance)
                except Exception:
                    pass
            if brush is not None:
                try:
                    gui.DeleteObject(brush)
                except Exception:
                    pass
            self._started = False
            self._ready_event.set()

    def _set_hollow_region(self, hwnd: int, width: int, height: int) -> None:
        """창 중앙은 완전히 비우고 바깥 테두리 픽셀만 남긴다."""

        gui = deps.win32gui
        if gui is None:
            return

        outer = gui.CreateRectRgnIndirect((0, 0, int(width), int(height)))
        inner = gui.CreateRectRgnIndirect(
            (
                self.thickness,
                self.thickness,
                int(width - self.thickness),
                int(height - self.thickness),
            )
        )
        try:
            gui.CombineRgn(outer, outer, inner, _RGN_DIFF)
            # 성공하면 region 소유권이 Windows 로 넘어가므로 outer 를 삭제하면 안 된다.
            gui.SetWindowRgn(hwnd, outer, True)
            outer = None
        finally:
            gui.DeleteObject(inner)
            if outer is not None:
                gui.DeleteObject(outer)


class CaptureEngine:
    """windows-capture 기반 비동기 창 캡처."""

    def __init__(
        self,
        hwnd: int,
        logger: Optional[Logger] = None,
        *,
        max_fps: float = 30.0,
    ) -> None:
        self.hwnd = int(hwnd)
        self.logger = logger or _silent
        self.latest_frame = None            # 아직 소비되지 않은 최신 grayscale 프레임
        self.last_frame_size: Optional[tuple[int, int]] = None
        self.capture: Optional[Any] = None
        self.capture_control: Optional[Any] = None
        self.border_mask: Optional[BorderMask] = None
        self.frame_lock = threading.Lock()
        self.frame_ready_event = threading.Event()
        self.closed_event = threading.Event()
        self.started = False
        self._last_published_at = 0.0
        self._publish_interval = (
            1.0 / max(1.0, float(max_fps)) if float(max_fps) > 0.0 else 0.0
        )

    # ── 콜백 ─────────────────────────────────────────────────────────────

    def on_frame_arrived(self, frame: Any, _capture_control: Any = None) -> None:
        """프레임을 grayscale 단일 슬롯에 넣고 밀린 프레임은 버린다.

        ⚠️ **이름을 바꾸지 말 것.** windows-capture 의 event() 는 핸들러를
        `handler.__name__` 으로 구분해서, 이름이 'on_frame_arrived'/'on_closed'
        가 아니면 등록 시점에 그대로 예외를 던진다. 이식하면서 앞에 밑줄을
        붙였다가 캡처 세션이 100% 시작 실패했고, 실패가 가드에 삼켜져
        '인식을 한 번도 못 했는데 완료로 보고'되는 상태가 됐다(적대적 리뷰 실측).
        tests/test_wgc_contract.py 가 이 이름을 고정한다.
        """

        cv2 = deps.cv2
        np = deps.np
        if cv2 is None or np is None:
            return
        try:
            captured_at = time.perf_counter()
            # 소비 속도보다 빠른 프레임은 큰 BGRA 변환 전에 버린다(순수 낭비 제거).
            with self.frame_lock:
                if (
                    self._publish_interval > 0.0
                    and self._last_published_at > 0.0
                    and captured_at - self._last_published_at + 1e-9 < self._publish_interval
                ):
                    return
                self._last_published_at = captured_at

            image_bgra = np.asarray(frame.frame_buffer)
            if image_bgra.size == 0:
                return
            if image_bgra.ndim != 3 or image_bgra.shape[2] < 3:
                return

            color_code = (
                cv2.COLOR_BGRA2GRAY if image_bgra.shape[2] >= 4 else cv2.COLOR_BGR2GRAY
            )
            gray = cv2.cvtColor(image_bgra, color_code)
            with self.frame_lock:
                # 큐를 쌓지 않고 덮어쓴다 — 한 프레임 늦더라도 과거 화면은 안 본다.
                self.latest_frame = gray
                self.last_frame_size = (int(frame.width), int(frame.height))
                self.frame_ready_event.set()
        except Exception as exc:  # noqa: BLE001
            self.logger(f"[캡처] 프레임 처리 중 문제: {exc}")

    def on_closed(self) -> None:
        """캡처 세션이 닫혔을 때. (이름 고정 — on_frame_arrived 주석 참고)"""

        self.closed_event.set()
        self.frame_ready_event.set()  # 대기 중인 소비자를 즉시 깨운다

    # ── 수명주기 ─────────────────────────────────────────────────────────

    def start(self) -> bool:
        if deps.WindowsCapture is None:
            return False

        if self.capture_control is not None:
            try:
                if not self.capture_control.is_finished():
                    return True
            except Exception:
                self.stop()

        self.frame_ready_event.clear()
        self.closed_event.clear()
        with self.frame_lock:
            self.latest_frame = None
            self.last_frame_size = None
            self._last_published_at = 0.0

        options: dict[str, object] = {
            "cursor_capture": False,
            "monitor_index": None,
            "window_name": None,
            "window_hwnd": self.hwnd,
        }

        def start_with(extra: dict[str, object]) -> None:
            self.capture = deps.WindowsCapture(**{**options, **extra})
            # 이 두 줄은 메서드 '이름'에 의존한다(위 on_frame_arrived 주석 참고).
            self.capture.event(self.on_frame_arrived)
            self.capture.event(self.on_closed)
            self.capture_control = self.capture.start_free_threaded()

        def start_mask() -> None:
            self.border_mask = BorderMask(self.hwnd, logger=self.logger)
            if not self.border_mask.start():
                self.border_mask = None

        try:
            if _supports_native_borderless_wgc():
                try:
                    start_with({"draw_border": False})
                except Exception as exc:  # noqa: BLE001
                    text = str(exc).lower()
                    if "capture border" not in text and "draw_border" not in text:
                        raise
                    # API 는 있는데 플랫폼이 거부하는 경우: 캡처는 그대로 쓰고
                    # 표시만 마스크로 가린다.
                    self.capture = None
                    self.capture_control = None
                    start_mask()
                    start_with({})
            else:
                # Windows 10 22H2(19045)에는 IsBorderRequired 가 없어서 false 를
                # 넘기면 E_NOINTERFACE 로 실패한다. 마스크로 가리는 쪽을 쓴다.
                start_mask()
                start_with({})

            self.started = True
            return True
        except Exception as exc:  # noqa: BLE001
            self.logger(f"[캡처] 세션을 시작하지 못했습니다: {exc}")
            if self.border_mask is not None:
                self.border_mask.stop()
                self.border_mask = None
            self.capture = None
            self.capture_control = None
            self.started = False
            return False

    def latest(self, timeout: float = 0.0):
        """소비되지 않은 최신 grayscale 프레임(없으면 None)."""

        if timeout > 0 and not self.frame_ready_event.wait(timeout):
            return None
        with self.frame_lock:
            frame = self.latest_frame
            self.latest_frame = None
            self.frame_ready_event.clear()
            return frame

    def frame_size(self) -> Optional[tuple[int, int]]:
        with self.frame_lock:
            return self.last_frame_size

    def stop(self) -> None:
        capture_control = self.capture_control
        border_mask = self.border_mask
        self.capture_control = None
        self.border_mask = None
        self.capture = None
        self.started = False
        self.frame_ready_event.clear()

        with self.frame_lock:
            self.latest_frame = None
            self.last_frame_size = None
            self._last_published_at = 0.0

        if capture_control is None:
            if border_mask is not None:
                border_mask.stop()
            return

        try:
            if not capture_control.is_finished():
                capture_control.stop()
        except Exception:
            pass
        finally:
            if border_mask is not None:
                border_mask.stop()
