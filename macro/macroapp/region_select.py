"""화면 영역 드래그 선택 오버레이.

타겟 템플릿 캡처에 사용합니다. 전체 화면 스크린샷을 그대로 띄운 뒤
마우스 드래그로 사각 영역을 선택하면 해당 부분을 원본(물리) 해상도로
잘라 PIL 이미지로 반환합니다. DPI 배율 때문에 Tk 논리 해상도와
스크린샷 해상도가 다르면 비율로 환산해 정확한 픽셀을 잘라냅니다.
"""

from __future__ import annotations

import tkinter as tk
from typing import Any, Optional

from macroapp import winapi
from macroapp.logging_util import LogCallback

try:
    from PIL import Image, ImageTk
except Exception as exc:  # noqa: BLE001
    Image = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]
    PIL_IMPORT_ERROR: Optional[BaseException] = exc
else:
    PIL_IMPORT_ERROR = None


class RegionSelector:
    """전체 화면 위에서 드래그로 사각 영역을 선택하는 1회용 오버레이입니다."""

    # 표시(논리) 좌표 기준 최소 드래그 크기. 이보다 작으면 단순 클릭으로 보고 취소합니다.
    MIN_DISPLAY_SIZE = 4

    def __init__(self, root: tk.Misc, logger: Optional[LogCallback] = None):
        self.root = root
        self.log = logger or print
        self.result: Optional[Any] = None
        self._screenshot: Optional[Any] = None
        self._photo: Optional[Any] = None
        self._top: Optional[tk.Toplevel] = None
        self._canvas: Optional[tk.Canvas] = None
        self._rect_id: Optional[int] = None
        self._start: Optional[tuple[int, int]] = None
        self._scale_x = 1.0
        self._scale_y = 1.0

    def select(self) -> Optional[Any]:
        """오버레이를 띄우고 선택이 끝날 때까지 대기한 뒤 잘라낸 PIL 이미지를 반환합니다.

        취소(ESC)하거나 캡처가 불가능하면 None을 반환합니다.
        """

        if Image is None or ImageTk is None:
            self.log("[캡처 오류] Pillow를 불러올 수 없어 템플릿 캡처를 사용할 수 없습니다.")
            self.log(f"       원본 오류: {PIL_IMPORT_ERROR}")
            return None
        if winapi.pyautogui is None:
            self.log("[캡처 오류] pyautogui를 불러올 수 없어 템플릿 캡처를 사용할 수 없습니다.")
            self.log(f"       원본 오류: {winapi.PYAUTOGUI_IMPORT_ERROR}")
            return None

        try:
            self._screenshot = winapi.pyautogui.screenshot()
        except Exception as exc:
            self.log(f"[캡처 오류] 화면 스크린샷에 실패했습니다: {exc}")
            return None

        screen_w = max(1, self.root.winfo_screenwidth())
        screen_h = max(1, self.root.winfo_screenheight())
        self._scale_x = self._screenshot.width / screen_w
        self._scale_y = self._screenshot.height / screen_h

        if (self._screenshot.width, self._screenshot.height) == (screen_w, screen_h):
            display_image = self._screenshot
        else:
            display_image = self._screenshot.resize((screen_w, screen_h))

        top = tk.Toplevel(self.root)
        self._top = top
        try:
            top.overrideredirect(True)
            top.geometry(f"{screen_w}x{screen_h}+0+0")
            top.attributes("-topmost", True)

            self._photo = ImageTk.PhotoImage(display_image, master=top)
            canvas = tk.Canvas(
                top,
                width=screen_w,
                height=screen_h,
                highlightthickness=0,
                bd=0,
                cursor="crosshair",
                bg="#000000",
            )
            self._canvas = canvas
            canvas.pack(fill=tk.BOTH, expand=True)
            canvas.create_image(0, 0, image=self._photo, anchor=tk.NW)
            canvas.create_text(
                screen_w // 2,
                36,
                text="드래그로 템플릿 영역을 선택하세요  (ESC: 취소)",
                fill="#FF5A3C",
                font=("Malgun Gothic", 14, "bold"),
            )

            canvas.bind("<ButtonPress-1>", self._on_press)
            canvas.bind("<B1-Motion>", self._on_drag)
            canvas.bind("<ButtonRelease-1>", self._on_release)
            top.bind("<Escape>", self._on_cancel)
            top.protocol("WM_DELETE_WINDOW", self._on_cancel)

            top.lift()
            top.focus_force()
            try:
                top.grab_set()
            except Exception:
                pass
        except Exception as exc:
            # 오버레이가 반쯤 만들어진 채 예외가 나면 topmost 전체화면 창이
            # 화면을 영구히 덮으므로 반드시 정리하고 취소로 처리합니다.
            self.log(f"[캡처 오류] 캡처 오버레이를 띄우지 못했습니다: {exc}")
            self._finish(None)
            self._screenshot = None
            self._photo = None
            self._canvas = None
            return None

        self.root.wait_window(top)

        result = self.result
        self._screenshot = None
        self._photo = None
        self._canvas = None
        return result

    def _on_press(self, event: tk.Event) -> None:
        self._start = (event.x, event.y)
        if self._canvas is None:
            return
        if self._rect_id is not None:
            self._canvas.delete(self._rect_id)
        self._rect_id = self._canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#FF3B1F", width=2
        )

    def _on_drag(self, event: tk.Event) -> None:
        if self._start is None or self._rect_id is None or self._canvas is None:
            return
        start_x, start_y = self._start
        self._canvas.coords(self._rect_id, start_x, start_y, event.x, event.y)

    def _on_release(self, event: tk.Event) -> None:
        if self._start is None or self._screenshot is None:
            self._finish(None)
            return

        start_x, start_y = self._start
        left, right = sorted((start_x, event.x))
        top_, bottom = sorted((start_y, event.y))

        if right - left < self.MIN_DISPLAY_SIZE or bottom - top_ < self.MIN_DISPLAY_SIZE:
            self._finish(None)
            return

        pixel_left = max(0, int(round(left * self._scale_x)))
        pixel_top = max(0, int(round(top_ * self._scale_y)))
        pixel_right = min(self._screenshot.width, int(round(right * self._scale_x)))
        pixel_bottom = min(self._screenshot.height, int(round(bottom * self._scale_y)))

        if pixel_right - pixel_left <= 0 or pixel_bottom - pixel_top <= 0:
            self._finish(None)
            return

        self._finish(self._screenshot.crop((pixel_left, pixel_top, pixel_right, pixel_bottom)))

    def _on_cancel(self, _event: Optional[tk.Event] = None) -> None:
        self._finish(None)

    def _finish(self, image: Optional[Any]) -> None:
        self.result = image
        top = self._top
        self._top = None
        if top is not None:
            try:
                top.grab_release()
            except Exception:
                pass
            try:
                top.destroy()
            except Exception:
                pass
