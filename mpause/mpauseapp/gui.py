"""mPause 메인 창.

화면에 있는 것은 실행 버튼 하나, 진행바 하나, 상태 문구 한 줄이 전부다.
설정값(대상·유지 시간)은 config.py 에 고정돼 있어 입력칸이 필요 없고,
문구는 진행 상태만 알려 준다.
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Optional

from mpauseapp import core, followup, press, runner as runner_mod, ui_kit, winproc
from mpauseapp.config import (
    APP_NAME,
    APP_TITLE,
    MIN_HEIGHT,
    MIN_WIDTH,
    PRODUCT_ID,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)
from mpauseapp.license_client import format_remaining_time, send_status
from mpauseapp.paths import APP_VERSION, app_dir


class PauseApp:
    """tkinter UI + 1회성 실행 워커."""

    def __init__(
        self,
        root: tk.Tk,
        license_key: Optional[str] = None,
        *,
        preview: bool = False,
        remaining_seconds: int = 0,
    ) -> None:
        self.root = root
        self.license_key = license_key
        self.preview = preview
        self.base_dir: Path = app_dir()
        self.runner = runner_mod.PauseRunner(
            prepare=followup.prepare,
            after_resume=followup.after_resume,
        )

        # 서명된 exp 로 만료 시점을 스냅샷해 두고 실행할 때마다 로컬로 강제한다.
        # exp 는 서명에 묶여 있어 위조가 불가능하므로 서버를 다시 부를 필요가 없고,
        # 오프라인 정품 사용자를 벌주지도 않는다(mAuto 와 같은 방식).
        self._license_deadline: Optional[float] = (
            time.monotonic() + remaining_seconds if remaining_seconds > 0 else None
        )
        self._remaining_at_start = remaining_seconds

        c = ui_kit.COLORS
        self._c = c

        root.title(f"{APP_TITLE}  v{APP_VERSION}")
        root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        root.minsize(MIN_WIDTH, MIN_HEIGHT)
        # 라이센스 창이 걸어 둔 resizable(False, False) 를 되돌린다.
        # 같은 root 를 재사용하므로 안 풀면 본 화면까지 크기 고정으로 남는다.
        root.resizable(True, True)
        root.configure(bg=c["bg"])
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._poll_id: Optional[str] = None
        self._build_ui()
        root.bind("<Destroy>", self._cancel_poll, add="+")
        self._poll_events()

        if not preview:
            self._check_admin_hint()
            if self.license_key:
                # 상태 전송은 네트워크 호출이다. UI 스레드에서 부르면 서버가 느릴 때
                # 창이 뜨자마자 최대 5초 얼어붙는다 → 워커로 던지고 결과는 안 본다.
                threading.Thread(
                    target=send_status,
                    args=(self.license_key,),
                    # 관리 패널에서 어느 제품인지 구분해야 하므로 여기서는
                    # 화면 표시 이름이 아니라 제품 식별자를 쓴다.
                    kwargs={"running": False, "message": f"{PRODUCT_ID} 시작"},
                    name="mpause-status",
                    daemon=True,
                ).start()

    # ── UI 구성 ───────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        c = self._c
        outer = tk.Frame(self.root, bg=c["bg"], padx=18, pady=16)
        outer.pack(fill=tk.BOTH, expand=True)

        # 헤더
        header = tk.Frame(outer, bg=c["bg"])
        header.pack(fill=tk.X, pady=(0, 16))
        mark = tk.Canvas(header, width=30, height=30, bg=c["bg"], highlightthickness=0, bd=0)
        mark.pack(side=tk.LEFT)
        ui_kit.round_rect(mark, 1, 1, 29, 29, 9, fill=c["accent"], outline="")
        mark.create_text(
            15, 15, text=APP_NAME[1:2].upper() or "A",
            fill="#FFFFFF", font=(ui_kit.FONT_FAMILY, 12, "bold"),
        )
        titles = tk.Frame(header, bg=c["bg"])
        titles.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(
            titles, text=APP_NAME, bg=c["bg"], fg=c["text"], font=ui_kit.font(13, bold=True)
        ).pack(anchor=tk.W)
        tk.Label(
            titles,
            text="버튼 한 번이면 나머지는 자동입니다",
            bg=c["bg"],
            fg=c["muted"],
            font=ui_kit.font(8),
        ).pack(anchor=tk.W)

        self.license_label = tk.Label(
            header,
            text=(
                format_remaining_time(self._remaining_at_start)
                if self._remaining_at_start
                else ("미리보기" if self.preview else "")
            ),
            bg=c["bg"],
            fg=c["muted"],
            font=ui_kit.font(8),
        )
        self.license_label.pack(side=tk.RIGHT, pady=(4, 0))

        # 실행 버튼 + 진행바 + 상태
        action = ui_kit.panel(outer)
        action.pack(fill=tk.BOTH, expand=True)

        self.run_btn = ui_kit.make_button(action, "실행", self._on_run)
        self.run_btn.pack(fill=tk.X, pady=(4, 0))

        self.progress = ui_kit.ProgressBar(action, height=10)
        self.progress.pack(fill=tk.X, pady=(22, 14))

        self.status_var = tk.StringVar(value="대기 중")
        self.status_label = tk.Label(
            action,
            textvariable=self.status_var,
            bg=c["panel"],
            fg=c["muted"],
            font=ui_kit.font(10),
            wraplength=WINDOW_WIDTH - 90,
            justify=tk.CENTER,
        )
        self.status_label.pack(fill=tk.X, pady=(0, 4))

        # 하단 안내
        self.hint_var = tk.StringVar(value="")
        tk.Label(
            outer,
            textvariable=self.hint_var,
            bg=c["bg"],
            fg=c["muted"],
            font=ui_kit.font(8),
            wraplength=WINDOW_WIDTH - 50,
            justify=tk.LEFT,
            anchor=tk.W,
        ).pack(fill=tk.X, side=tk.BOTTOM, pady=(10, 0))

    # ── 동작 ──────────────────────────────────────────────────────────────

    def _check_admin_hint(self) -> None:
        """관리자 권한이 아니면 미리 알려 준다(권한 오류를 겪기 전에)."""
        if not winproc.is_admin():
            self.hint_var.set("관리자 권한으로 실행하면 더 안정적으로 동작합니다.")

    def _on_run(self) -> None:
        """버튼 한 번 = 1회성 실행."""
        if self.runner.busy:
            return

        # 라이센스 만료 강제 (서명된 exp 스냅샷 기준, 로컬 검사)
        if self._license_deadline is not None and time.monotonic() >= self._license_deadline:
            self._set_status("라이센스가 만료되었습니다. 프로그램을 다시 실행해 인증하세요.")
            ui_kit.set_button_enabled(self.run_btn, False)
            return

        if not self.runner.start():
            return
        ui_kit.set_button_enabled(self.run_btn, False)
        self.progress.set(0.0)

    def _set_status(self, message: str) -> None:
        self.status_var.set(message)
        fg, _bg = ui_kit.status_tone(message)
        self.status_label.configure(fg=fg)

    # ── 워커 이벤트 폴링 ──────────────────────────────────────────────────

    def _poll_events(self) -> None:
        self._poll_id = None
        if not self.root.winfo_exists():
            return
        self.runner.drain(self._handle_event)
        self._poll_id = self.root.after(50, self._poll_events)

    def _cancel_poll(self, event=None) -> None:
        """예약된 폴링을 취소한다.

        창이 destroy 된 뒤에 after 콜백이 깨어나면 Tk 가
        'invalid command name ..._poll_events' 를 뱉는다. 기능에는 영향이 없지만
        종료 로그를 더럽히고 진짜 오류를 가린다.
        """
        if event is not None and event.widget is not self.root:
            return  # 자식 위젯의 <Destroy> 는 무시한다
        if self._poll_id is not None:
            try:
                self.root.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None

    def _handle_event(self, event: runner_mod.Event) -> None:
        if event.kind == runner_mod.EVT_STATE:
            if event.text:
                self._set_status(event.text)
            # 버튼은 **종료 상태에서만** 다시 켠다. 워커가 종료 상태를 마무리까지
            # 끝낸 뒤에 보내므로, 아직 도는 중에 다음 실행이 시작되는 일이 없다.
            if event.state in (core.STATE_DONE, core.STATE_FAILED):
                ui_kit.set_button_enabled(self.run_btn, True)
                if event.state == core.STATE_FAILED:
                    self.progress.set(0.0)
        elif event.kind == runner_mod.EVT_TICK:
            self.progress.set(event.progress)
        elif event.kind in (runner_mod.EVT_DONE, runner_mod.EVT_ERROR):
            # 문구만 갱신한다. 버튼 복구는 위의 종료 상태가 맡는다.
            self._set_status(event.text)

    # ── 종료 ──────────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        """진행 중에 창을 닫아도 대상을 반드시 정상 상태로 되돌린다."""
        if self.runner.busy:
            self._set_status("정리하는 중… 잠시만 기다리세요.")
            self.root.update_idletasks()
        self._cancel_poll()
        self.runner.shutdown(timeout=5.0)
        # 앱 수명 동안 유지하던 가상 패드는 여기서 뽑는다(mAuto 방식 — 실행 중엔
        # 유지). 실패해도 프로세스가 죽으면 ViGEm 이 장치를 알아서 제거한다.
        try:
            press.release_pad()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
