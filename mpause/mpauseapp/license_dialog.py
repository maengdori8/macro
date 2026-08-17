"""라이센스 인증 화면.

mAuto 의 LicenseDialog 를 가져오되 두 가지를 고쳤다.

1) **검증을 스레드로 뺐다.** mAuto 는 UI 스레드에서 바로 urlopen(timeout=10)을
   불러서, 서버가 느리면 창이 최대 10초 통째로 얼어붙는다(응답 없음 표시).
2) **무효로 판정된 키는 파일에서 지운다.** mAuto 는 입력칸만 비우고 디스크에는
   남겨 둬서, 실행할 때마다 같은 무효 키로 자동 시도했다. 단 '오프라인'일 때는
   지우지 않는다 — 정품 사용자가 인터넷이 잠깐 끊겼다고 키를 잃으면 안 된다.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from typing import Callable, Optional

from mpauseapp import ui_kit
from mpauseapp.config import APP_NAME
from mpauseapp.license_client import (
    delete_saved_license,
    get_hwid,
    load_saved_license,
    save_license_key,
    verify_license_server,
)


class LicenseDialog:
    """라이센스 키 입력/검증 다이얼로그."""

    def __init__(
        self,
        root: tk.Tk,
        base_dir: Path,
        on_success: Callable[[tk.Tk, str, int], None],
    ) -> None:
        self.root = root
        self.base_dir = base_dir
        self.on_success = on_success
        self.result: Optional[str] = None
        self._results: "Queue[tuple[str, dict, bool]]" = Queue()
        self._pending = False
        # 인증 성공 후 _launch 까지 600~700ms 의 지연이 있다. 그 사이에 버튼이나
        # Enter 로 두 번째 검증이 시작되면 PauseApp 이 두 번 만들어진다.
        # 이 플래그로 '성공한 순간부터' 추가 시도를 전부 막는다.
        self._launched = False
        self._poll_id: Optional[str] = None
        # 서명으로 확인된 남은 시간. 본체가 로컬 만료 강제에 쓴다.
        self._remaining_seconds = 0

        c = ui_kit.COLORS
        self._c = c

        self.root.title(f"{APP_NAME} 라이센스 인증")
        self.root.geometry("460x400+200+200")
        self.root.resizable(False, False)
        self.root.configure(bg=c["bg"])

        main = tk.Frame(self.root, bg=c["bg"], padx=30, pady=30)
        main.pack(fill=tk.BOTH, expand=True)

        mark = tk.Canvas(main, width=44, height=44, bg=c["bg"], highlightthickness=0, bd=0)
        mark.pack(anchor=tk.W)
        ui_kit.round_rect(mark, 1, 1, 43, 43, 13, fill=c["accent"], outline="")
        mark.create_text(
            22, 22, text="P", fill="#FFFFFF", font=(ui_kit.FONT_FAMILY, 17, "bold")
        )

        tk.Label(
            main,
            text=f"{APP_NAME} 라이센스 인증",
            bg=c["bg"],
            fg=c["text"],
            font=ui_kit.font(16, bold=True),
        ).pack(anchor=tk.W, pady=(16, 4))
        tk.Label(
            main,
            text="구매하신 라이센스 키를 입력하면 자동으로 이 PC에 등록됩니다.",
            bg=c["bg"],
            fg=c["muted"],
            font=ui_kit.font(9),
        ).pack(anchor=tk.W, pady=(0, 20))

        tk.Label(
            main, text="LICENSE KEY", bg=c["bg"], fg=c["muted"], font=ui_kit.font(7, bold=True)
        ).pack(anchor=tk.W, pady=(0, 6))

        self.key_var = tk.StringVar()
        self.key_entry = ui_kit.make_entry(
            main, self.key_var, width=34, center=True, mono=True, size=12
        )
        self.key_entry.pack(fill=tk.X, ipady=9)
        self.key_entry.bind("<Return>", lambda _e: self._activate())

        self.message_var = tk.StringVar()
        self.message_label = tk.Label(
            main,
            textvariable=self.message_var,
            bg=c["bg"],
            fg=c["muted"],
            font=ui_kit.font(9),
            wraplength=390,
            justify=tk.LEFT,
            anchor=tk.W,
        )
        self.message_label.pack(fill=tk.X, pady=(10, 18))

        buttons = tk.Frame(main, bg=c["bg"])
        buttons.pack(fill=tk.X)

        self.activate_btn = ui_kit.make_button(buttons, "인증하기", self._activate)
        self.activate_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 8))

        self.exit_btn = ui_kit.make_button(
            buttons, "종료", self.root.destroy, tone="ghost"
        )
        self.exit_btn.pack(side=tk.RIGHT)

        # ⚠️ 여기서 get_hwid() 를 직접 부르면 안 된다. mainloop 전이라 창이 아직
        # 그려지지 않았는데, HWID 계산이 wmic → powershell 서브프로세스를 돌린다
        # (각 timeout=8초). 그동안 화면에 아무것도 없는 채로 멈춘다.
        self.hwid_var = tk.StringVar(value="기기번호 확인 중…")
        tk.Label(
            main,
            textvariable=self.hwid_var,
            bg=c["bg"],
            fg=c["muted"],
            font=ui_kit.font(8),
        ).pack(side=tk.BOTTOM, pady=(14, 0))
        self._start_hwid_probe()

        self._poll_id = self.root.after(80, self._poll)

        saved_key = load_saved_license(self.base_dir)
        if saved_key:
            self.key_var.set(saved_key)
            self._verify(saved_key, from_saved=True)
        else:
            self.key_entry.focus_set()

    # ── 검증 ──────────────────────────────────────────────────────────────

    def _start_hwid_probe(self) -> None:
        """HWID 를 워커에서 계산해 라벨만 나중에 채운다(창은 즉시 뜬다)."""
        holder: dict[str, str] = {}

        def work() -> None:
            try:
                holder["hwid"] = get_hwid()
            except Exception:
                holder["hwid"] = ""

        thread = threading.Thread(target=work, name="mpause-hwid", daemon=True)
        thread.start()

        def poll() -> None:
            if not self.root.winfo_exists():
                return
            if thread.is_alive():
                self.root.after(120, poll)
                return
            hwid = holder.get("hwid", "")
            self.hwid_var.set(
                f"이 PC 기기번호: {hwid[:12]}…" if hwid else "기기번호를 확인할 수 없습니다."
            )

        self.root.after(120, poll)

    def _set_message(self, text: str, tone: str = "muted") -> None:
        self.message_var.set(text)
        self.message_label.configure(fg=self._c.get(tone, self._c["muted"]))

    def _verify(self, key: str, *, from_saved: bool) -> None:
        """네트워크 검증을 워커 스레드에서 돌린다(UI 프리즈 방지)."""
        if self._pending or self._launched:
            return
        self._pending = True
        ui_kit.set_button_enabled(self.activate_btn, False)
        self._set_message("서버에 확인하는 중…", "muted")

        def work() -> None:
            try:
                result = verify_license_server(key, get_hwid())
            except Exception as exc:  # noqa: BLE001 - 어떤 실패도 UI 로 흘려보낸다
                result = {"_offline": True, "message": str(exc)}
            self._results.put((key, result, from_saved))

        threading.Thread(target=work, name="mpause-license", daemon=True).start()

    def _poll(self) -> None:
        """워커 결과를 UI 스레드에서 받아 처리한다."""
        self._poll_id = None
        # 본 화면으로 넘어갔으면 이 다이얼로그의 위젯은 이미 destroy 됐다.
        # 계속 재예약하면 늦게 도착한 결과가 사라진 위젯을 건드린다.
        if self._launched or not self.root.winfo_exists():
            return
        try:
            while True:
                key, result, from_saved = self._results.get_nowait()
                self._handle_result(key, result, from_saved)
                if self._launched:
                    return
        except Empty:
            pass
        self._poll_id = self.root.after(80, self._poll)

    def _handle_result(self, key: str, result: dict, from_saved: bool) -> None:
        if self._launched:
            return
        self._pending = False
        ui_kit.set_button_enabled(self.activate_btn, True)

        if result.get("_offline"):
            # 오프라인은 '무효'가 아니다 — 저장된 키를 절대 지우지 않는다.
            self._set_message("서버에 연결할 수 없습니다. 인터넷 연결을 확인하세요.", "danger")
            self.key_entry.focus_set()
            return

        if not result.get("valid", False):
            self._set_message(result.get("message", "서버 인증 실패"), "danger")
            # 서명이 검증된 '무효' 판정일 때만 지운다. 서명 검증 자체가 실패한
            # 경우(가짜 서버·공개키 불일치·서버가 아직 제품 바인딩 미배포)는
            # 정품 키일 수 있으므로 절대 지우지 않는다.
            if from_saved and result.get("_signed_invalid"):
                delete_saved_license(self.base_dir)
                self.key_var.set("")
            self.key_entry.focus_set()
            return

        # 성공이 확정된 순간부터 추가 시도를 전부 막는다(버튼·Enter 모두).
        self._launched = True
        ui_kit.set_button_enabled(self.activate_btn, False)

        self._remaining_seconds = int(result.get("remaining_seconds", 0) or 0)
        msg = result.get("message", "유효한 라이센스입니다.")
        if from_saved:
            self._set_message(f"저장된 라이센스가 유효합니다. {msg}", "ok")
            delay = 700
        else:
            self._set_message(f"인증 성공! {msg}", "ok")
            save_license_key(self.base_dir, key)
            delay = 600
        self.root.after(delay, lambda: self._launch(key))

    def _activate(self) -> None:
        key = self.key_var.get().strip()
        if not key:
            self._set_message("라이센스 키를 입력하세요.", "danger")
            return
        self._verify(key, from_saved=False)

    def _launch(self, key: str) -> None:
        if not self.root.winfo_exists():
            return
        self.result = key
        for widget in self.root.winfo_children():
            widget.destroy()
        self.on_success(self.root, key, self._remaining_seconds)
