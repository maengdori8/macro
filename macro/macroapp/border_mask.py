"""Windows 10 WGC 캡처 표시 테두리를 시각적으로 가립니다.

Windows 10 클라이언트 빌드(19045 이하)는
``GraphicsCaptureSession.IsBorderRequired``를 제공하지 않습니다. 따라서 WGC의
비활성 창 캡처를 유지하면서 시스템의 노란 표시를 숨기려면 대상 창 바로 위에
입력을 받지 않는 얇은 마스크를 배치해야 합니다.

마스크는 대상 창의 Z-order 바로 위에만 놓입니다. 브라우저처럼 다른 창이 대상을
덮으면 마스크도 함께 가려지고, 캡처가 끝나면 전용 스레드에서 즉시 제거됩니다.
"""

from __future__ import annotations

import ctypes
import os
import threading
from ctypes import wintypes
from typing import Optional

from macroapp import winapi
from macroapp.logging_util import LogCallback

_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_RGN_DIFF = 4


def _extended_frame_bounds(hwnd: int) -> Optional[tuple[int, int, int, int]]:
    """DWM이 실제로 그리는 창 경계를 반환합니다."""

    if not hasattr(ctypes, "windll"):
        return None

    try:
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


class CaptureBorderMask:
    """대상 창의 WGC 표시 테두리를 가리는 클릭 통과형 Win32 창입니다."""

    def __init__(
        self,
        hwnd: int,
        logger: Optional[LogCallback] = None,
        *,
        thickness: int = 5,
        outer_padding: int = 2,
    ) -> None:
        self.hwnd = int(hwnd)
        self.logger = logger or print
        self.thickness = max(3, int(thickness))
        self.outer_padding = max(0, int(outer_padding))

        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._mask_hwnd: Optional[int] = None
        self._started = False

    def log(self, message: str) -> None:
        self.logger(message)

    def start(self, timeout: float = 1.0) -> bool:
        """마스크 스레드를 시작하고 창 생성 결과를 반환합니다."""

        if self._thread is not None and self._thread.is_alive():
            return self._started
        if winapi.win32gui is None or winapi.win32api is None or winapi.win32con is None:
            return False
        if self.hwnd <= 0 or not winapi.win32gui.IsWindow(self.hwnd):
            return False

        self._stop_event.clear()
        self._ready_event.clear()
        self._started = False
        self._thread = threading.Thread(
            target=self._run,
            name="WGCBorderMask",
            daemon=True,
        )
        self._thread.start()
        self._ready_event.wait(max(0.0, float(timeout)))
        return self._started

    def stop(self, timeout: float = 1.0) -> None:
        """마스크 창을 생성한 스레드에서 안전하게 제거합니다."""

        thread = self._thread
        self._thread = None
        self._stop_event.set()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout)))
        self._started = False

    def _run(self) -> None:
        gui = winapi.win32gui
        api = winapi.win32api
        con = winapi.win32con
        if gui is None or api is None or con is None:
            self._ready_event.set()
            return

        class_name = f"MautoWGCBorderMask_{os.getpid()}_{id(self):x}"
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

            # 같은 무결성 수준이면 게임 창의 owned window로 만들 수 있습니다. 게임이
            # 더 높은 권한으로 실행 중이면 ERROR_ACCESS_DENIED가 나므로 독립 창으로
            # 폴백하고 아래에서 Z-order를 대상 바로 위로 맞춥니다.
            try:
                mask_hwnd = gui.CreateWindowEx(
                    ex_style,
                    class_name,
                    None,
                    con.WS_POPUP,
                    0,
                    0,
                    1,
                    1,
                    self.hwnd,
                    0,
                    instance,
                    None,
                )
            except Exception:
                mask_hwnd = gui.CreateWindowEx(
                    ex_style,
                    class_name,
                    None,
                    con.WS_POPUP,
                    0,
                    0,
                    1,
                    1,
                    0,
                    0,
                    instance,
                    None,
                )

            self._mask_hwnd = int(mask_hwnd)
            self._started = True
            self._ready_event.set()

            last_geometry: Optional[tuple[int, int, int, int]] = None
            visible = False

            while not self._stop_event.wait(0.20):
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
                        bounds = tuple(int(value) for value in gui.GetWindowRect(self.hwnd))

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
                        # hWndInsertAfter는 '마스크보다 앞설 창'입니다. 대상 바로 앞의
                        # 창 뒤에 넣으면 다른 앱보다 위로 튀지 않고 대상 바로 위에 옵니다.
                        flags = con.SWP_NOACTIVATE | con.SWP_SHOWWINDOW
                        if int(preceding or 0) == int(mask_hwnd):
                            # 이미 대상 바로 위라면 자기 자신을 hWndInsertAfter로 넘기지
                            # 않고 위치/크기만 갱신합니다.
                            insert_after = 0
                            flags |= con.SWP_NOZORDER
                        else:
                            insert_after = preceding if preceding else con.HWND_TOP
                        gui.SetWindowPos(mask_hwnd, insert_after, *geometry, flags)
                        gui.UpdateWindow(mask_hwnd)
                        last_geometry = geometry
                        visible = True
                except Exception:
                    # 게임의 전체 화면/창 전환 순간에는 HWND 상태와 DWM bounds가 잠시
                    # 불일치할 수 있습니다. 다음 200ms 주기에서 자연스럽게 복구합니다.
                    continue
        except Exception as exc:
            self.log(f"[캡처 안내] Windows 10 WGC 테두리 마스크를 만들지 못했습니다: {exc}")
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
        """창 중앙은 완전히 비우고 바깥쪽 테두리 픽셀만 남깁니다."""

        gui = winapi.win32gui
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
            # 성공하면 region의 소유권은 Windows로 넘어가므로 outer를 삭제하면 안 됩니다.
            gui.SetWindowRgn(hwnd, outer, True)
            outer = None
        finally:
            gui.DeleteObject(inner)
            if outer is not None:
                gui.DeleteObject(outer)
