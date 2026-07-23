"""WGC 프레임 위에서 좌표 또는 영역을 보정하는 Tk 대화상자."""

from __future__ import annotations

import platform
import tkinter as tk
from typing import Optional, Union

import numpy as np
from PIL import Image, ImageTk

from macroapp.renewal import NormalizedPoint, NormalizedRect


Selection = Union[NormalizedPoint, NormalizedRect]


class FrameSelector:
    def __init__(
        self,
        parent: tk.Misc,
        frame_gray: np.ndarray,
        mode: str,
        title: str,
        instruction: str,
    ):
        if mode not in ("point", "region"):
            raise ValueError(f"지원하지 않는 선택 모드입니다: {mode}")
        self.parent = parent
        self.mode = mode
        self.result: Optional[Selection] = None
        self.start: Optional[tuple[int, int]] = None
        self.shape_id: Optional[int] = None

        frame_height, frame_width = frame_gray.shape[:2]
        screen_width = parent.winfo_screenwidth()
        screen_height = parent.winfo_screenheight()
        max_width = max(640, int(screen_width * 0.90))
        max_height = max(360, int(screen_height * 0.82))
        scale = min(max_width / frame_width, max_height / frame_height, 1.0)
        self.display_width = max(1, int(round(frame_width * scale)))
        self.display_height = max(1, int(round(frame_height * scale)))

        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.configure(bg="#101010")
        self.window.attributes("-topmost", True)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)

        font_family = "Malgun Gothic" if platform.system() == "Windows" else "Arial"
        tk.Label(
            self.window,
            text=instruction,
            bg="#101010",
            fg="#FFFFFF",
            font=(font_family, 11, "bold"),
            pady=8,
        ).pack(fill=tk.X)

        source = Image.fromarray(frame_gray)
        if (self.display_width, self.display_height) != (frame_width, frame_height):
            source = source.resize(
                (self.display_width, self.display_height),
                Image.Resampling.LANCZOS,
            )
        self.photo = ImageTk.PhotoImage(source)
        self.canvas = tk.Canvas(
            self.window,
            width=self.display_width,
            height=self.display_height,
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.pack()
        self.canvas.create_image(0, 0, image=self.photo, anchor=tk.NW)

        if mode == "point":
            self.canvas.bind("<Button-1>", self._select_point)
        else:
            self.canvas.bind("<ButtonPress-1>", self._start_region)
            self.canvas.bind("<B1-Motion>", self._drag_region)
            self.canvas.bind("<ButtonRelease-1>", self._finish_region)
        self.window.bind("<Escape>", lambda _event: self.cancel())

        self.window.update_idletasks()
        x = max(0, (screen_width - self.window.winfo_width()) // 2)
        y = max(0, (screen_height - self.window.winfo_height()) // 2)
        self.window.geometry(f"+{x}+{y}")
        self.window.grab_set()

    def _normalize_point(self, x: int, y: int) -> NormalizedPoint:
        return NormalizedPoint(
            min(max(x / max(1, self.display_width), 0.0), 1.0),
            min(max(y / max(1, self.display_height), 0.0), 1.0),
        )

    def _select_point(self, event) -> None:
        self.result = self._normalize_point(event.x, event.y)
        self.window.destroy()

    def _start_region(self, event) -> None:
        self.start = (event.x, event.y)
        if self.shape_id is not None:
            self.canvas.delete(self.shape_id)
        self.shape_id = self.canvas.create_rectangle(
            event.x,
            event.y,
            event.x,
            event.y,
            outline="#FF3B30",
            width=2,
        )

    def _drag_region(self, event) -> None:
        if self.start is None or self.shape_id is None:
            return
        self.canvas.coords(self.shape_id, self.start[0], self.start[1], event.x, event.y)

    def _finish_region(self, event) -> None:
        if self.start is None:
            return
        x1, x2 = sorted((self.start[0], event.x))
        y1, y2 = sorted((self.start[1], event.y))
        if x2 - x1 < 4 or y2 - y1 < 4:
            return
        first = self._normalize_point(x1, y1)
        second = self._normalize_point(x2, y2)
        self.result = NormalizedRect(first.x, first.y, second.x, second.y)
        self.window.destroy()

    def cancel(self) -> None:
        self.result = None
        self.window.destroy()

    def show(self) -> Optional[Selection]:
        self.parent.wait_window(self.window)
        return self.result


def select_from_frame(
    parent: tk.Misc,
    frame_gray: np.ndarray,
    *,
    mode: str,
    title: str,
    instruction: str,
) -> Optional[Selection]:
    return FrameSelector(parent, frame_gray, mode, title, instruction).show()
