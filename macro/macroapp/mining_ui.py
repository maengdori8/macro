"""tkinter 기반 FC 채굴 현황 대시보드."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
import queue
import threading
import webbrowser
from typing import Callable, Optional

import tkinter as tk

from macroapp.mining import (
    KST,
    MiningSettings,
    MiningStore,
    MiningSummary,
    MiningSynchronizer,
)


class MiningDashboard(tk.Frame):
    """API 설정, 동기화, 기간 통계를 한 페이지에 표시한다."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        base_dir: Path,
        colors: dict[str, str],
        font_factory: Callable[[int, bool], tuple],
        automation_running: Callable[[], bool],
        logger: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent, bg=colors["bg"])
        self.colors = colors
        self.font_factory = font_factory
        self.automation_running = automation_running
        self.logger = logger or (lambda _message: None)
        self.store = MiningStore(base_dir)
        self.settings = self.store.load_settings()
        self.period_days = 30
        self._summary: Optional[MiningSummary] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._cancel_event = threading.Event()
        self._events: queue.SimpleQueue[tuple[str, object]] = queue.SimpleQueue()
        self._poll_job = None
        self._chart_job = None
        self._period_buttons: dict[int, tk.Button] = {}

        self.nickname_var = tk.StringVar(value=self.settings.nickname)
        self.key_var = tk.StringVar(value="")
        self.key_hint_var = tk.StringVar(
            value="API 키 저장됨 · 변경 시 새 키 입력"
            if self.settings.api_key else "Nexon Open API 키가 필요합니다"
        )
        self.sync_state_var = tk.StringVar(value="대기 중")
        self.sync_detail_var = tk.StringVar(value=self._last_sync_text())
        self.period_label_var = tk.StringVar(value="최근 30일")
        self.metric_vars = {
            "fc": tk.StringVar(value="0 FC"),
            "period": tk.StringVar(value="-"),
            "average": tk.StringVar(value="0.0 FC"),
            "matches": tk.StringVar(value="0"),
            "record": tk.StringVar(value="0승 0패"),
        }

        self._build_ui()
        self._refresh_summary()
        self._poll_job = self.after(100, self._poll_events)

    def _font(self, size: int, bold: bool = False) -> tuple:
        return self.font_factory(size, bold)

    def _last_sync_text(self) -> str:
        if not self.settings.last_sync:
            return "아직 동기화하지 않았습니다"
        try:
            parsed = dt.datetime.fromisoformat(self.settings.last_sync)
            return f"마지막 동기화 · {parsed.astimezone(KST):%Y-%m-%d %H:%M}"
        except ValueError:
            return f"마지막 동기화 · {self.settings.last_sync}"

    def _card(self, parent: tk.Misc, *, padx: int = 16, pady: int = 14) -> tk.Frame:
        c = self.colors
        return tk.Frame(
            parent,
            bg=c["panel"],
            padx=padx,
            pady=pady,
            highlightbackground=c["border"],
            highlightthickness=1,
        )

    def _card_title(self, parent: tk.Misc, text: str) -> tk.Frame:
        """자동화 페이지와 같은 '강조 바 + 제목' 스타일을 씁니다."""

        c = self.colors
        head = tk.Frame(parent, bg=c["panel"])
        bar = tk.Frame(head, bg=c["accent"], width=3, height=15)
        bar.pack(side=tk.LEFT)
        bar.pack_propagate(False)
        tk.Label(
            head,
            text=text,
            bg=c["panel"],
            fg=c["text"],
            font=self._font(11, True),
        ).pack(side=tk.LEFT, padx=(9, 0))
        return head

    def _flat_button(
        self,
        parent: tk.Misc,
        text: str,
        command,
        *,
        accent: bool = False,
        width: int = 10,
        restore=None,
    ) -> tk.Button:
        c = self.colors
        bg = c["accent"] if accent else c["input"]
        active = c["accent_hover"] if accent else c["row_hover"]
        button = tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            bg=bg,
            fg="#FFFFFF" if accent else c["text"],
            activebackground=active,
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=self._font(10, True),
            padx=12,
            pady=7,
        )

        def enter(_event) -> None:
            if str(button["state"]) == tk.DISABLED or getattr(button, "_selected", False):
                return
            button.configure(bg=active)

        def leave(_event) -> None:
            if str(button["state"]) == tk.DISABLED:
                return
            if restore is not None:
                restore()
            else:
                button.configure(bg=bg)

        button.bind("<Enter>", enter)
        button.bind("<Leave>", leave)
        return button

    def _entry(self, parent: tk.Misc, variable: tk.StringVar, *, show: str = "") -> tk.Entry:
        c = self.colors
        return tk.Entry(
            parent,
            textvariable=variable,
            show=show,
            bg=c["input"],
            fg=c["text"],
            insertbackground=c["text"],
            relief=tk.FLAT,
            bd=0,
            highlightbackground=c["border"],
            highlightcolor=c["accent"],
            highlightthickness=1,
            font=self._font(10),
        )

    def _build_ui(self) -> None:
        c = self.colors
        header = tk.Frame(self, bg=c["bg"])
        header.pack(fill=tk.X, pady=(0, 16))
        title_block = tk.Frame(header, bg=c["bg"])
        title_block.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            title_block,
            text="FC 채굴 현황",
            bg=c["bg"],
            fg=c["text"],
            font=self._font(19, True),
            anchor=tk.W,
        ).pack(anchor=tk.W)
        tk.Label(
            title_block,
            text="감독모드 매치 기록을 동기화해 FC 적립량과 추이를 확인합니다.",
            bg=c["bg"],
            fg=c["muted"],
            font=self._font(9),
            anchor=tk.W,
        ).pack(anchor=tk.W, pady=(4, 0))

        self.state_badge = tk.Label(
            header,
            textvariable=self.sync_state_var,
            bg=c["ok_bg"],
            fg=c["ok"],
            font=self._font(9, True),
            padx=13,
            pady=7,
        )
        self.state_badge.pack(side=tk.RIGHT)

        controls = tk.Frame(self, bg=c["bg"])
        controls.pack(fill=tk.X)
        controls.grid_columnconfigure(0, weight=5)
        controls.grid_columnconfigure(1, weight=4)

        account = self._card(controls)
        account.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        self._card_title(account, "FC Online 계정").pack(fill=tk.X, pady=(0, 12))

        tk.Label(
            account,
            text="닉네임",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(8),
            anchor=tk.W,
        ).pack(fill=tk.X)
        nickname_entry = self._entry(account, self.nickname_var)
        nickname_entry.pack(fill=tk.X, ipady=7, pady=(4, 9))

        key_label_row = tk.Frame(account, bg=c["panel"])
        key_label_row.pack(fill=tk.X)
        tk.Label(
            key_label_row,
            text="Nexon Open API 키",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(8),
        ).pack(side=tk.LEFT)
        issue = tk.Label(
            key_label_row,
            text="발급 페이지 ↗",
            bg=c["panel"],
            fg=c["accent_soft"],
            font=self._font(8, True),
            cursor="hand2",
        )
        issue.pack(side=tk.RIGHT)
        issue.bind(
            "<Button-1>",
            lambda _event: webbrowser.open("https://openapi.nexon.com/"),
        )
        key_entry = self._entry(account, self.key_var, show="●")
        key_entry.pack(fill=tk.X, ipady=7, pady=(4, 4))
        tk.Label(
            account,
            textvariable=self.key_hint_var,
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(8),
            anchor=tk.W,
        ).pack(fill=tk.X)

        actions = tk.Frame(account, bg=c["panel"])
        actions.pack(fill=tk.X, pady=(11, 0))
        self.save_button = self._flat_button(actions, "계정 저장", self._save_account)
        self.save_button.pack(side=tk.LEFT)
        self.sync_button = self._flat_button(
            actions,
            "지금 동기화",
            self._start_sync,
            accent=True,
            width=12,
        )
        self.sync_button.pack(side=tk.LEFT, padx=(8, 0))

        period = self._card(controls)
        period.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        self._card_title(period, "조회 기간").pack(fill=tk.X)
        tk.Label(
            period,
            text="오늘 또는 최근 기간을 한 번에 비교하세요.",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(8),
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(4, 12))
        period_buttons = tk.Frame(period, bg=c["panel"])
        period_buttons.pack(fill=tk.X)
        for days, label in ((1, "오늘"), (7, "7일"), (30, "30일"), (90, "90일")):
            button = self._flat_button(
                period_buttons,
                label,
                lambda value=days: self._set_period(value),
                width=5,
                restore=self._paint_period_buttons,
            )
            button.pack(side=tk.LEFT, padx=(0, 6))
            self._period_buttons[days] = button
        self._paint_period_buttons()
        tk.Label(
            period,
            textvariable=self.sync_detail_var,
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(8),
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(14, 0))

        self.progress_canvas = tk.Canvas(
            period,
            height=5,
            bg=c["input"],
            highlightthickness=0,
            bd=0,
        )
        self.progress_canvas.pack(fill=tk.X, pady=(8, 5))
        self.progress_canvas.bind("<Configure>", lambda _event: self._draw_progress())
        self.progress_ratio = 0.0
        self.progress_message_var = tk.StringVar(value="수동 동기화 · 자동 네트워크 작업 없음")
        tk.Label(
            period,
            textvariable=self.progress_message_var,
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(8),
            anchor=tk.W,
        ).pack(fill=tk.X)

        metrics = tk.Frame(self, bg=c["bg"])
        metrics.pack(fill=tk.X, pady=(14, 0))
        metric_defs = (
            ("기간 FC 채굴량", "fc", c["accent_soft"]),
            ("조회 기간", "period", c["text"]),
            ("일 평균", "average", c["text"]),
            ("감독모드 경기", "matches", c["text"]),
            ("전적", "record", c["text"]),
        )
        for index, (title, key, color) in enumerate(metric_defs):
            card = self._card(metrics, padx=14, pady=12)
            card.pack(
                side=tk.LEFT,
                fill=tk.BOTH,
                expand=True,
                padx=(0 if index == 0 else 5, 0 if index == len(metric_defs) - 1 else 5),
            )
            tk.Label(
                card,
                text=title,
                bg=c["panel"],
                fg=c["muted"],
                font=self._font(8),
                anchor=tk.W,
            ).pack(fill=tk.X)
            tk.Label(
                card,
                textvariable=self.metric_vars[key],
                bg=c["panel"],
                fg=color,
                font=self._font(14, True),
                anchor=tk.W,
            ).pack(fill=tk.X, pady=(7, 0))

        chart_card = self._card(self, padx=14, pady=12)
        chart_card.pack(fill=tk.BOTH, expand=True, pady=(14, 0))
        chart_header = tk.Frame(chart_card, bg=c["panel"])
        chart_header.pack(fill=tk.X)
        self._card_title(chart_header, "날짜별 FC 채굴량").pack(side=tk.LEFT)
        tk.Label(
            chart_header,
            textvariable=self.period_label_var,
            bg=c["input"],
            fg=c["accent_soft"],
            font=self._font(8, True),
            padx=8,
            pady=4,
        ).pack(side=tk.RIGHT)
        self.chart = tk.Canvas(
            chart_card,
            height=245,
            bg=c["panel"],
            highlightthickness=0,
            bd=0,
        )
        self.chart.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.chart.bind("<Configure>", self._schedule_chart)

        tk.Label(
            self,
            text=(
                "챔피언스 승 +15 FC · 슈퍼챔피언스 승 +20 FC · 무승부 제외   |   "
                "Data based on NEXON Open API"
            ),
            bg=c["bg"],
            fg=c["muted"],
            font=self._font(8),
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(8, 0))

    def _save_account(self) -> None:
        nickname = self.nickname_var.get().strip()
        key = self.key_var.get().strip() or self.settings.api_key
        if not nickname:
            self._set_state("닉네임 필요", error=True)
            self.progress_message_var.set("FC Online 닉네임을 입력해 주세요.")
            return
        if not key:
            self._set_state("API 키 필요", error=True)
            self.progress_message_var.set("Nexon Open API 키를 입력해 주세요.")
            return
        self.settings = MiningSettings(
            nickname=nickname,
            ouid=(self.settings.ouid if nickname == self.settings.nickname else ""),
            api_key=key,
            last_sync=self.settings.last_sync,
        )
        try:
            self.store.save_settings(self.settings)
        except OSError as exc:
            self._set_state("저장 실패", error=True)
            self.progress_message_var.set(f"계정 설정을 저장하지 못했습니다: {exc}")
            return
        self.key_var.set("")
        self.key_hint_var.set("API 키 저장됨 · Windows DPAPI 보호")
        self._set_state("저장됨")
        self.progress_message_var.set("계정 설정을 이 PC에 저장했습니다.")
        self._refresh_summary()

    def _start_sync(self) -> None:
        if self._sync_thread is not None and self._sync_thread.is_alive():
            return
        nickname = self.nickname_var.get().strip()
        api_key = self.key_var.get().strip() or self.settings.api_key
        if not nickname or not api_key:
            self._save_account()
            nickname = self.nickname_var.get().strip()
            api_key = self.settings.api_key
            if not nickname or not api_key:
                return
        self._cancel_event.clear()
        self.sync_button.configure(state=tk.DISABLED, bg=self.colors["disabled"])
        self.save_button.configure(state=tk.DISABLED)
        self._set_state("동기화 중")
        self.progress_ratio = 0.01
        self._draw_progress()
        self.progress_message_var.set("계정 확인 중")
        workers = 2 if self.automation_running() else 8

        def progress(phase: str, current: int, total: int, message: str) -> None:
            self._events.put(("progress", (phase, current, total, message)))

        def worker() -> None:
            try:
                synchronizer = MiningSynchronizer(self.store, max_workers=workers)
                result = synchronizer.sync(
                    nickname,
                    api_key,
                    progress=progress,
                    cancel_event=self._cancel_event,
                )
                self._events.put(("success", result))
            except Exception as exc:
                self._events.put(("error", exc))

        self._sync_thread = threading.Thread(
            target=worker,
            name="fc-mining-sync",
            daemon=True,
        )
        self._sync_thread.start()

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "progress":
                    self._apply_progress(*payload)
                elif kind == "success":
                    self.settings = self.store.load_settings()
                    self.nickname_var.set(self.settings.nickname)
                    self.key_var.set("")
                    self.key_hint_var.set("API 키 저장됨 · Windows DPAPI 보호")
                    self.sync_detail_var.set(self._last_sync_text())
                    self.progress_ratio = 1.0
                    self._draw_progress()
                    self._set_state("동기화 완료")
                    self.progress_message_var.set(
                        f"신규 {payload.added:,}경기 · 조회 {payload.scanned:,}경기"
                    )
                    self._refresh_summary()
                    self._finish_sync_controls()
                elif kind == "error":
                    message = str(payload)
                    self.logger(f"[채굴 동기화] {message}")
                    self._set_state("동기화 실패", error=True)
                    self.progress_message_var.set(message)
                    self._finish_sync_controls()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self._poll_job = self.after(100, self._poll_events)

    def _finish_sync_controls(self) -> None:
        self.sync_button.configure(state=tk.NORMAL, bg=self.colors["accent"])
        self.save_button.configure(state=tk.NORMAL)

    def _apply_progress(self, phase: str, current: int, total: int, message: str) -> None:
        if phase == "account":
            ratio = 0.03
        elif phase == "list":
            ratio = 0.05 + 0.15 * min(1.0, current / max(1, total))
        elif phase == "detail":
            ratio = 0.20 + 0.78 * min(1.0, current / max(1, total))
        else:
            ratio = 1.0
        self.progress_ratio = ratio
        self.progress_message_var.set(message)
        self._draw_progress()

    def _set_state(self, text: str, *, error: bool = False) -> None:
        self.sync_state_var.set(text)
        if error:
            self.state_badge.configure(
                bg=self.colors["danger_bg"],
                fg=self.colors["danger"],
            )
        else:
            self.state_badge.configure(
                bg=self.colors["ok_bg"],
                fg=self.colors["ok"],
            )

    def _set_period(self, days: int) -> None:
        self.period_days = max(1, int(days))
        self.period_label_var.set("오늘" if days == 1 else f"최근 {days}일")
        self._paint_period_buttons()
        self._refresh_summary()

    def _paint_period_buttons(self) -> None:
        c = self.colors
        for days, button in self._period_buttons.items():
            selected = days == self.period_days
            button._selected = selected  # 호버가 선택 강조를 덮지 않게 표시
            button.configure(
                bg=c["accent"] if selected else c["input"],
                fg="#FFFFFF" if selected else c["muted"],
            )

    def _refresh_summary(self) -> None:
        ouid = self.settings.ouid
        if not ouid:
            self._summary = self.store.summary("", self.period_days)
        else:
            self._summary = self.store.summary(ouid, self.period_days)
        summary = self._summary
        self.metric_vars["fc"].set(f"{summary.fc:,} FC")
        self.metric_vars["period"].set(
            summary.end_date if summary.period_days == 1
            else f"{summary.start_date[5:]} ~ {summary.end_date[5:]}"
        )
        self.metric_vars["average"].set(f"{summary.daily_average:,.1f} FC")
        self.metric_vars["matches"].set(f"{summary.matches:,}")
        self.metric_vars["record"].set(f"{summary.wins:,}승 {summary.losses:,}패")
        self._schedule_chart()

    def _draw_progress(self) -> None:
        canvas = self.progress_canvas
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill=self.colors["input"], outline="")
        canvas.create_rectangle(
            0,
            0,
            int(width * max(0.0, min(1.0, self.progress_ratio))),
            height,
            fill=self.colors["accent"],
            outline="",
        )

    def _schedule_chart(self, _event=None) -> None:
        if self._chart_job is not None:
            try:
                self.after_cancel(self._chart_job)
            except Exception:
                pass
        self._chart_job = self.after_idle(self._draw_chart)

    def _draw_chart(self) -> None:
        self._chart_job = None
        canvas = self.chart
        canvas.delete("all")
        summary = self._summary
        width = max(320, canvas.winfo_width())
        height = max(180, canvas.winfo_height())
        left, right, top, bottom = 48, 14, 15, 30
        plot_width = max(1, width - left - right)
        plot_height = max(1, height - top - bottom)
        c = self.colors
        if summary is None or not summary.daily:
            return
        max_fc = max((item.fc for item in summary.daily), default=0)
        y_max = max(20, ((max_fc + 19) // 20) * 20)
        for index in range(5):
            y = top + plot_height * index / 4
            value = y_max * (4 - index) / 4
            canvas.create_line(left, y, width - right, y, fill=c["grid"], width=1)
            canvas.create_text(
                left - 8,
                y,
                text=f"{value:,.0f}",
                fill=c["muted"],
                font=self._font(7),
                anchor=tk.E,
            )

        items = summary.daily
        slot = plot_width / max(1, len(items))
        bar_width = max(2, min(18, slot * 0.66))
        label_every = max(1, len(items) // 7)
        for index, item in enumerate(items):
            x = left + slot * (index + 0.5)
            bar_height = plot_height * item.fc / y_max
            y1 = top + plot_height - bar_height
            canvas.create_rectangle(
                x - bar_width / 2,
                y1,
                x + bar_width / 2,
                top + plot_height,
                fill=c["accent"],
                outline="",
            )
            if item.fc > 0 and len(items) <= 30:
                canvas.create_text(
                    x,
                    max(top + 7, y1 - 7),
                    text=str(item.fc),
                    fill=c["text"],
                    font=self._font(7),
                )
            if index % label_every == 0 or index == len(items) - 1:
                canvas.create_text(
                    x,
                    height - 12,
                    text=item.date[5:],
                    fill=c["muted"],
                    font=self._font(7),
                )

        average = summary.daily_average
        if average > 0:
            y = top + plot_height * (1.0 - min(1.0, average / y_max))
            canvas.create_line(
                left,
                y,
                width - right,
                y,
                fill=c["warn"],
                width=1,
                dash=(5, 4),
            )
            canvas.create_text(
                width - right,
                y - 7,
                text=f"평균 {average:.1f} FC",
                fill=c["warn"],
                font=self._font(7, True),
                anchor=tk.E,
            )

    def close(self) -> None:
        self._cancel_event.set()
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        if self._chart_job is not None:
            try:
                self.after_cancel(self._chart_job)
            except Exception:
                pass
            self._chart_job = None
