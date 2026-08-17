"""UI 공통 부품 — 팔레트 + 커스텀 위젯.

mAuto(macroapp/gui.py)의 AutomationApp 메서드로만 존재하던 위젯 헬퍼들을
클래스 상태에서 떼어내 자유 함수로 만든 것이다. 팔레트는 같은 값을 쓰므로
두 앱의 첫인상이 통일된다.

tkinter 기본 위젯(tk.Radiobutton, tk.Scale)은 옛 윈도우 느낌이 나서 쓰지 않는다.
"""

from __future__ import annotations

import platform
import tkinter as tk
from typing import Callable, Optional

# 장시간 봐도 눈이 편한 뉴트럴 다크 + 단일 강조색(인디고) 팔레트.
COLORS: dict[str, str] = {
    "bg": "#0A0D14",            # 앱 배경(가장 어두운 층)
    "panel": "#11151E",         # 카드 표면
    "row": "#161B26",           # 카드 안 리스트 행
    "row_hover": "#1B2130",
    "border": "#1E2532",        # 1px 실선 테두리
    "input": "#0D1119",         # 입력/트랙 배경
    "text": "#E9EDF5",
    "muted": "#78839A",
    "accent": "#635BFF",
    "accent_active": "#4F46E5",
    "accent_hover": "#7169FF",
    "accent_soft": "#7CD9F5",   # 수치·링크용 보조 강조
    "disabled": "#1C2230",
    "disabled_text": "#4E5769",
    "ok": "#34D399",
    "ok_bg": "#0C2A22",
    "danger": "#FB7185",
    "danger_bg": "#2C1520",
    "warn": "#FBBF24",
    "warn_bg": "#2E2410",
}

FONT_FAMILY = "Malgun Gothic" if platform.system() == "Windows" else "Arial"
MONO_FAMILY = "Consolas" if platform.system() == "Windows" else "Menlo"


def font(size: int, bold: bool = False) -> tuple:
    """플랫폼에 맞는 한글 UI 폰트."""
    return (FONT_FAMILY, size, "bold") if bold else (FONT_FAMILY, size)


def round_rect(
    canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs
):
    """캔버스에 둥근 사각형(tkinter 기본 위젯에는 라운드가 없다)."""
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def panel(
    parent: tk.Misc,
    title: Optional[str] = None,
    *,
    subtitle: Optional[str] = None,
    padx: int = 16,
    pady: int = 14,
    colors: Optional[dict] = None,
) -> tk.Frame:
    """1px 테두리 카드. 제목은 좌측 정렬 + 강조 바."""
    c = colors or COLORS
    frame = tk.Frame(
        parent,
        bg=c["panel"],
        padx=padx,
        pady=pady,
        highlightbackground=c["border"],
        highlightthickness=1,
    )
    if title:
        head = tk.Frame(frame, bg=c["panel"])
        head.pack(fill=tk.X, pady=(0, 12))
        bar = tk.Frame(head, bg=c["accent"], width=3, height=15)
        bar.pack(side=tk.LEFT)
        bar.pack_propagate(False)
        tk.Label(
            head, text=title, bg=c["panel"], fg=c["text"], font=font(11, bold=True)
        ).pack(side=tk.LEFT, padx=(9, 0))
        if subtitle:
            tk.Label(
                head, text=subtitle, bg=c["panel"], fg=c["muted"], font=font(8)
            ).pack(side=tk.RIGHT, pady=(3, 0))
    return frame


def hover_button(button: tk.Button, color: str) -> None:
    """비활성 버튼은 호버 색을 무시한다."""
    if str(button["state"]) == tk.DISABLED:
        return
    button.configure(bg=color)


def make_button(
    parent: tk.Misc,
    text: str,
    command: Callable[[], None],
    *,
    tone: str = "primary",
    small: bool = False,
    colors: Optional[dict] = None,
) -> tk.Button:
    """톤(primary/ghost/danger)에 맞춘 플랫 버튼 + 호버 효과."""
    c = colors or COLORS
    palettes = {
        "primary": (c["accent"], c["accent_hover"], "#FFFFFF"),
        "ghost": (c["input"], c["row_hover"], c["text"]),
        "danger": (c["input"], c["danger_bg"], c["danger"]),
    }
    base, hover, fg = palettes.get(tone, palettes["primary"])
    button = tk.Button(
        parent,
        text=text,
        command=command,
        bg=base,
        fg=fg,
        activebackground=hover,
        activeforeground=fg,
        disabledforeground=c["disabled_text"],
        relief=tk.FLAT,
        bd=0,
        cursor="hand2",
        font=font(9 if small else 11, bold=True),
        padx=10 if small else 18,
        pady=2 if small else 9,
    )
    button._tone_colors = (base, hover)  # 호버·비활성 복원용
    button.bind("<Enter>", lambda _e, b=button, v=hover: hover_button(b, v))
    button.bind("<Leave>", lambda _e, b=button, v=base: hover_button(b, v))
    return button


def set_button_enabled(button: tk.Button, enabled: bool, *, colors: Optional[dict] = None) -> None:
    """버튼을 활성/비활성으로 바꾸고 배경색까지 되돌린다.

    tk.Button 은 state 만 바꾸면 배경색이 그대로 남아 '눌리는 것처럼' 보인다.
    """
    c = colors or COLORS
    base = getattr(button, "_tone_colors", (c["accent"], c["accent_hover"]))[0]
    if enabled:
        button.configure(state=tk.NORMAL, bg=base, cursor="hand2")
    else:
        button.configure(state=tk.DISABLED, bg=c["disabled"], cursor="")


def make_entry(
    parent: tk.Misc,
    variable: tk.Variable,
    *,
    width: int = 20,
    center: bool = False,
    mono: bool = False,
    size: int = 10,
    colors: Optional[dict] = None,
) -> tk.Entry:
    """테두리만 있는 납작한 입력칸(포커스 시 강조색 테두리)."""
    c = colors or COLORS
    return tk.Entry(
        parent,
        textvariable=variable,
        width=width,
        bg=c["input"],
        fg=c["text"],
        insertbackground=c["accent_soft"],
        relief=tk.FLAT,
        bd=0,
        justify=tk.CENTER if center else tk.LEFT,
        font=(MONO_FAMILY, size) if mono else font(size),
        highlightbackground=c["border"],
        highlightcolor=c["accent"],
        highlightthickness=1,
    )


def caption(
    parent: tk.Misc, text: str, *, colors: Optional[dict] = None, size: int = 7
) -> tk.Label:
    """입력칸 위에 붙는 작은 대문자 라벨."""
    c = colors or COLORS
    return tk.Label(
        parent, text=text, bg=parent["bg"], fg=c["muted"], font=font(size, bold=True)
    )


class ProgressBar:
    """캔버스로 그린 납작한 진행바.

    ttk.Progressbar 는 OS 테마를 따라가서 이 팔레트와 따로 논다.
    값은 0.0~1.0 이고, 창 크기가 바뀌면 <Configure> 로 다시 그린다.
    """

    def __init__(
        self,
        parent: tk.Misc,
        *,
        height: int = 10,
        width: int = 200,
        colors: Optional[dict] = None,
    ) -> None:
        c = colors or COLORS
        self._c = c
        self._value = 0.0
        self.canvas = tk.Canvas(
            parent,
            height=height,
            width=width,
            bg=c["panel"],
            highlightthickness=0,
            bd=0,
        )
        self.canvas.bind("<Configure>", lambda _e: self._redraw())

    def pack(self, **kwargs) -> None:
        self.canvas.pack(**kwargs)

    def set(self, value: float) -> None:
        """0.0~1.0. 범위 밖 값은 잘라낸다."""
        self._value = max(0.0, min(1.0, float(value)))
        self._redraw()

    def get(self) -> float:
        return self._value

    def _redraw(self) -> None:
        canvas = self.canvas
        try:
            width = int(canvas.winfo_width())
            height = int(canvas.winfo_height())
        except tk.TclError:  # 이미 파괴된 위젯
            return
        if width <= 1 or height <= 1:
            return
        canvas.delete("all")
        radius = max(2, height // 2)
        round_rect(canvas, 0, 0, width, height, radius, fill=self._c["input"], outline="")
        filled = int(width * self._value)
        if filled >= 2:
            round_rect(
                canvas,
                0,
                0,
                max(filled, radius * 2),
                height,
                radius,
                fill=self._c["accent"],
                outline="",
            )


# ─── 상태 문구 색 ──────────────────────────────────────────────────────────

_DANGER_WORDS = ("오류", "실패", "만료", "거부", "찾을 수 없", "차단")
_WARN_WORDS = ("중단", "확인", "권한", "주의")


def status_tone(message: str, *, colors: Optional[dict] = None) -> tuple[str, str]:
    """상태 문구의 의미에 맞는 (전경색, 배경색)을 고른다.

    mAuto 와 같은 방식(한국어 부분문자열 매칭)이라 문구만 바꿔도 색이 따라온다.
    """
    c = colors or COLORS
    if not message:
        return c["muted"], c["input"]
    for word in _DANGER_WORDS:
        if word in message:
            return c["danger"], c["danger_bg"]
    for word in _WARN_WORDS:
        if word in message:
            return c["warn"], c["warn_bg"]
    return c["ok"], c["ok_bg"]
