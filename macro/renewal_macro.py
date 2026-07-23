"""FC ONLINE 이적시장 갱신매크로 전용 실행 파일.

기존 감독모드 앱과 UI/작업 스레드를 완전히 분리합니다. 실행 중에는 OCR과 전체 화면
템플릿 매칭을 시작하지 않고, 지정한 가격 숫자 영역만 WGC로 받아 비교합니다.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from typing import Optional

import numpy as np

from macroapp.config import WINDOW_TITLE
from macroapp.frame_select import select_from_frame
from macroapp.license_client import (
    _send_status,
    get_hwid,
    load_saved_license,
    save_license_key,
    verify_license_server,
)
from macroapp.paths import APP_VERSION, app_dir
from macroapp.renewal import (
    FastRenewalRunner,
    NormalizedPoint,
    NormalizedRect,
    encode_gray_png,
    load_renewal_profile,
    save_renewal_profile,
)
from macroapp.window import InactiveManager


class RenewalLicenseDialog:
    """기존 제품과 같은 라이센스 서버/저장 키를 쓰는 갱신 전용 인증창."""

    def __init__(self, root: tk.Tk, base_dir: Path):
        self.root = root
        self.base_dir = base_dir
        self.bg = "#101010"
        self.text = "#F3F3F3"
        self.accent = "#D93A2B"
        self.error = "#FF6B6B"
        self.ok = "#69DB7C"

        root.title("mAuto 갱신매크로 라이센스")
        root.geometry("480x320+200+160")
        root.resizable(False, False)
        root.configure(bg=self.bg)

        body = tk.Frame(root, bg=self.bg, padx=24, pady=22)
        body.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            body,
            text="갱신매크로 라이센스 인증",
            bg=self.bg,
            fg=self.accent,
            font=("Malgun Gothic", 16, "bold"),
        ).pack(pady=(0, 18))
        tk.Label(
            body,
            text="라이센스 키",
            bg=self.bg,
            fg=self.text,
            font=("Malgun Gothic", 10),
        ).pack(anchor=tk.W, pady=(0, 5))

        self.key_var = tk.StringVar()
        self.key_entry = tk.Entry(
            body,
            textvariable=self.key_var,
            bg="#262626",
            fg=self.text,
            insertbackground=self.text,
            font=("Consolas", 12),
            relief=tk.FLAT,
        )
        self.key_entry.pack(fill=tk.X, ipady=7, pady=(0, 12))
        self.key_entry.bind("<Return>", lambda _event: self.activate())

        self.message_var = tk.StringVar(value="")
        self.message_label = tk.Label(
            body,
            textvariable=self.message_var,
            bg=self.bg,
            fg=self.text,
            wraplength=420,
            justify=tk.LEFT,
            font=("Malgun Gothic", 9),
        )
        self.message_label.pack(fill=tk.X, pady=(0, 14))

        button_row = tk.Frame(body, bg=self.bg)
        button_row.pack(fill=tk.X)
        self.activate_button = tk.Button(
            button_row,
            text="인증하기",
            command=self.activate,
            bg=self.accent,
            fg="#FFFFFF",
            activebackground="#B72D20",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            font=("Malgun Gothic", 11, "bold"),
            pady=7,
        )
        self.activate_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        tk.Button(
            button_row,
            text="종료",
            command=root.destroy,
            bg="#3C3C3C",
            fg=self.text,
            relief=tk.FLAT,
            font=("Malgun Gothic", 11),
            padx=24,
            pady=7,
        ).pack(side=tk.LEFT, padx=(6, 0))

        saved = load_saved_license(base_dir)
        if saved:
            self.key_var.set(saved)
            root.after(100, lambda: self._verify(saved, auto=True))
        else:
            self.key_entry.focus_set()

    def activate(self) -> None:
        key = self.key_var.get().strip()
        if not key:
            self._message("라이센스 키를 입력하세요.", self.error)
            return
        self._verify(key, auto=False)

    def _verify(self, key: str, *, auto: bool) -> None:
        self.activate_button.configure(state=tk.DISABLED)
        self._message("라이센스 확인 중...", self.text)
        self.root.update_idletasks()
        try:
            result = verify_license_server(key, get_hwid())
        except Exception:
            result = {"_offline": True}
        self.activate_button.configure(state=tk.NORMAL)

        if result.get("_offline"):
            self._message("서버에 연결할 수 없습니다. 인터넷 연결을 확인하세요.", self.error)
            return
        if not result.get("valid", False):
            self._message(result.get("message", "라이센스 인증 실패"), self.error)
            if auto:
                self.key_var.set("")
            return

        save_license_key(self.base_dir, key)
        self._message("인증 성공", self.ok)
        self.root.after(350, lambda: self._launch(key))

    def _message(self, text: str, color: str) -> None:
        self.message_var.set(text)
        self.message_label.configure(fg=color)

    def _launch(self, key: str) -> None:
        for widget in self.root.winfo_children():
            widget.destroy()
        RenewalApp(self.root, license_key=key)


class RenewalApp:
    COLORS = {
        "bg": "#101010",
        "panel": "#1A1A1A",
        "border": "#2B2B2B",
        "input": "#262626",
        "text": "#F3F3F3",
        "muted": "#9C9C9C",
        "accent": "#D93A2B",
        "accent_active": "#B72D20",
        "disabled": "#3A3A3A",
        "ok": "#69DB7C",
    }

    def __init__(self, root: tk.Tk, license_key: Optional[str] = None):
        self.root = root
        self.license_key = license_key
        self.profile = load_renewal_profile()
        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.ui_queue: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self.closing = False
        self._starting = False
        self._capture_buttons: list[tk.Button] = []

        self.window_title_var = tk.StringVar(value=WINDOW_TITLE)
        self.side_var = tk.StringVar(value="buy")
        self.threshold_var = tk.DoubleVar(value=self.profile.change_threshold)
        self.open_ms_var = tk.IntVar(value=self.profile.open_settle_ms)
        self.close_ms_var = tk.IntVar(value=self.profile.close_settle_ms)
        self.confirm_frames_var = tk.IntVar(value=self.profile.confirm_frames)
        self.status_var = tk.StringVar(value="좌표 설정 후 F8")
        self.clock_var = tk.StringVar(value="--:--:--")
        self.setting_status_var = tk.StringVar(value="")

        self._log_file = None
        try:
            log_dir = self._data_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            self._log_file = open(
                log_dir / f"renewal_{time.strftime('%Y-%m-%d')}.log",
                "a",
                encoding="utf-8",
            )
        except Exception:
            pass

        self.root.title(f"mAuto 갱신매크로 {APP_VERSION}")
        self.root.geometry("760x650+120+70")
        self.root.minsize(720, 610)
        self.root.resizable(True, True)
        self.root.configure(bg=self.COLORS["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<F8>", lambda _event: self.start())
        self.root.bind("<F9>", lambda _event: self.stop())
        self.root.bind("<Escape>", lambda _event: self.stop())

        self._build_ui()
        self._set_running(False)
        self._poll_queue()
        self._tick_clock()

    @staticmethod
    def _data_dir() -> Path:
        local = os.environ.get("LOCALAPPDATA")
        return (Path(local) if local else Path.home()) / "mAuto"

    def _font(self, size: int, bold: bool = False) -> tuple:
        return ("Malgun Gothic", size, "bold") if bold else ("Malgun Gothic", size)

    def _button(self, parent: tk.Misc, text: str, command, *, small: bool = False) -> tk.Button:
        c = self.COLORS
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=c["accent"],
            fg="#FFFFFF",
            activebackground=c["accent_active"],
            activeforeground="#FFFFFF",
            disabledforeground="#888888",
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=self._font(9 if small else 11, bold=True),
            padx=10 if small else 18,
            pady=3 if small else 9,
        )

    def _panel(self, parent: tk.Misc, title: str) -> tk.Frame:
        c = self.COLORS
        panel = tk.Frame(
            parent,
            bg=c["panel"],
            padx=14,
            pady=12,
            highlightbackground=c["border"],
            highlightthickness=1,
        )
        tk.Label(
            panel,
            text=title,
            bg=c["panel"],
            fg=c["text"],
            font=self._font(11, bold=True),
        ).pack(anchor=tk.W, pady=(0, 9))
        return panel

    def _build_ui(self) -> None:
        c = self.COLORS
        body = tk.Frame(self.root, bg=c["bg"], padx=16, pady=16)
        body.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(body, bg=c["bg"])
        header.pack(fill=tk.X, pady=(0, 12))
        tk.Label(
            header,
            text="FC ONLINE 갱신매크로",
            bg=c["bg"],
            fg=c["accent"],
            font=self._font(18, bold=True),
        ).pack(side=tk.LEFT)
        tk.Label(
            header,
            textvariable=self.clock_var,
            bg=c["bg"],
            fg=c["text"],
            font=("Consolas", 19, "bold"),
        ).pack(side=tk.RIGHT)

        mode_panel = self._panel(body, "1. 갱신 구분")
        mode_panel.pack(fill=tk.X, pady=(0, 10))
        mode_row = tk.Frame(mode_panel, bg=c["panel"])
        mode_row.pack(fill=tk.X)
        for text, value in (("구매 — 상한가", "buy"), ("판매 — 하한가", "sell")):
            tk.Radiobutton(
                mode_row,
                text=text,
                variable=self.side_var,
                value=value,
                command=self._refresh_status,
                bg=c["panel"],
                fg=c["text"],
                selectcolor=c["input"],
                activebackground=c["panel"],
                activeforeground=c["text"],
                font=self._font(10, bold=True),
            ).pack(side=tk.LEFT, padx=(0, 22))

        calibration_panel = self._panel(body, "2. 화면 좌표 설정")
        calibration_panel.pack(fill=tk.X, pady=(0, 10))
        tk.Label(
            calibration_panel,
            text="목록 화면: 재등록  |  재등록 창: 가격 숫자·상/하한가·확정·취소",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(9),
        ).pack(anchor=tk.W, pady=(0, 8))
        button_row = tk.Frame(calibration_panel, bg=c["panel"])
        button_row.pack(fill=tk.X)
        for text, item in (
            ("재등록", "re_register"),
            ("가격 숫자영역", "price"),
            ("상한가/하한가", "limit"),
            ("확정", "confirm"),
            ("취소", "cancel"),
        ):
            button = self._button(
                button_row,
                text,
                lambda selected=item: self.calibrate(selected),
                small=True,
            )
            button.pack(side=tk.LEFT, padx=(0, 7))
            self._capture_buttons.append(button)

        self._refresh_status()
        tk.Label(
            calibration_panel,
            textvariable=self.setting_status_var,
            justify=tk.LEFT,
            anchor=tk.W,
            bg=c["panel"],
            fg=c["ok"],
            font=self._font(9),
        ).pack(fill=tk.X, pady=(9, 0))

        speed_panel = self._panel(body, "3. 속도·인식 설정")
        speed_panel.pack(fill=tk.X, pady=(0, 10))
        speed_row = tk.Frame(speed_panel, bg=c["panel"])
        speed_row.pack(fill=tk.X)
        for label, variable, width in (
            ("민감도", self.threshold_var, 7),
            ("열기 대기 ms", self.open_ms_var, 6),
            ("닫기 대기 ms", self.close_ms_var, 6),
            ("변경 확인 프레임", self.confirm_frames_var, 4),
        ):
            tk.Label(
                speed_row,
                text=label,
                bg=c["panel"],
                fg=c["muted"],
                font=self._font(9),
            ).pack(side=tk.LEFT, padx=(0, 4))
            tk.Entry(
                speed_row,
                textvariable=variable,
                width=width,
                bg=c["input"],
                fg=c["text"],
                insertbackground=c["text"],
                relief=tk.FLAT,
                justify=tk.CENTER,
            ).pack(side=tk.LEFT, padx=(0, 12), ipady=3)
        tk.Label(
            speed_panel,
            text="기본값 45/25ms · 가격이 그대로면 즉시 취소 후 다시 열기 · 변경은 2프레임 확인",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(8),
        ).pack(anchor=tk.W, pady=(8, 0))

        status_panel = self._panel(body, "상태")
        status_panel.pack(fill=tk.X, pady=(0, 10))
        tk.Label(
            status_panel,
            textvariable=self.status_var,
            bg=c["panel"],
            fg=c["accent"],
            font=self._font(12, bold=True),
        ).pack(anchor=tk.W)

        controls = tk.Frame(body, bg=c["bg"])
        controls.pack(side=tk.BOTTOM, fill=tk.X)
        self.start_button = self._button(controls, "시작 (F8)", self.start)
        self.start_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.stop_button = self._button(controls, "정지 (F9/ESC)", self.stop)
        self.stop_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

    def _refresh_status(self) -> None:
        def mark(value: object) -> str:
            return "완료" if value else "미설정"

        side = self.profile.side(self.side_var.get())
        side_name = "구매" if self.side_var.get() == "buy" else "판매"
        limit_name = "상한가" if self.side_var.get() == "buy" else "하한가"
        self.setting_status_var.set(
            f"공통: 재등록 {mark(self.profile.re_register_point)} / "
            f"확정 {mark(self.profile.confirm_point)} / 취소 {mark(self.profile.cancel_point)}\n"
            f"{side_name}: 가격영역 {mark(side.price_rect and side.baseline_png)} / "
            f"{limit_name} {mark(side.limit_point)}"
        )

    def _save_settings(self) -> bool:
        try:
            self.profile.change_threshold = min(
                0.30, max(0.003, float(self.threshold_var.get()))
            )
            self.profile.open_settle_ms = min(
                500, max(20, int(self.open_ms_var.get()))
            )
            self.profile.close_settle_ms = min(
                500, max(10, int(self.close_ms_var.get()))
            )
            self.profile.confirm_frames = min(
                3, max(1, int(self.confirm_frames_var.get()))
            )
            self.threshold_var.set(self.profile.change_threshold)
            self.open_ms_var.set(self.profile.open_settle_ms)
            self.close_ms_var.set(self.profile.close_settle_ms)
            self.confirm_frames_var.set(self.profile.confirm_frames)
            save_renewal_profile(self.profile)
            return True
        except Exception as exc:
            self.log(f"[설정 오류] {exc}")
            self.status_var.set("설정값 오류")
            return False

    def _capture_frame(self) -> Optional[np.ndarray]:
        manager = InactiveManager(self.window_title_var.get().strip(), logger=self.log)
        try:
            if not manager.find_window():
                self.status_var.set("FC ONLINE 창을 찾지 못함")
                return None
            self.status_var.set("WGC 화면 캡처 중")
            self.root.update_idletasks()
            for _attempt in range(2):
                frame = manager.capture_client_area(window_validated=True)
                if frame is not None:
                    return frame.copy()
            self.status_var.set("WGC 화면 캡처 실패")
            return None
        finally:
            manager.stop_capture()

    def calibrate(self, item: str) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.status_var.set("실행 중에는 좌표 변경 불가")
            return
        frame = self._capture_frame()
        if frame is None:
            return

        side_name = "구매 상한가" if self.side_var.get() == "buy" else "판매 하한가"
        instruction = {
            "re_register": "거래 목록에서 '재등록' 버튼 가운데를 클릭하세요.",
            "price": f"{side_name} 가격의 숫자 부분만 좁게 드래그하세요.",
            "limit": f"재등록 창에서 {side_name} 항목 가운데를 클릭하세요.",
            "confirm": "최종 구매/판매 확정 버튼 가운데를 클릭하세요.",
            "cancel": "재등록 창의 취소 버튼 가운데를 클릭하세요.",
        }[item]
        selection = select_from_frame(
            self.root,
            frame,
            mode="region" if item == "price" else "point",
            title="갱신 좌표 설정",
            instruction=instruction,
        )
        if selection is None:
            self.status_var.set("좌표 선택 취소")
            return

        side = self.profile.side(self.side_var.get())
        if item == "price" and isinstance(selection, NormalizedRect):
            x1, y1, x2, y2 = selection.to_pixels(frame.shape[1], frame.shape[0])
            side.price_rect = selection
            side.baseline_png = encode_gray_png(frame[y1:y2, x1:x2])
        elif isinstance(selection, NormalizedPoint):
            if item == "re_register":
                self.profile.re_register_point = selection
            elif item == "limit":
                side.limit_point = selection
            elif item == "confirm":
                self.profile.confirm_point = selection
            elif item == "cancel":
                self.profile.cancel_point = selection
        else:
            self.status_var.set("잘못된 선택")
            return

        if self._save_settings():
            self._refresh_status()
            self.status_var.set("좌표 저장 완료")

    def start(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.status_var.set("이미 실행 중")
            return
        if self._starting or not self._save_settings():
            return
        missing = self.profile.missing(self.side_var.get())
        if missing:
            self.status_var.set("미설정: " + ", ".join(missing))
            return

        if self.license_key:
            self._starting = True
            self._set_running(True)
            self.status_var.set("라이센스 확인 중")
            threading.Thread(target=self._verify_then_start, daemon=True).start()
        else:
            self._start_worker()

    def _verify_then_start(self) -> None:
        try:
            result = verify_license_server(self.license_key, get_hwid())
        except Exception:
            result = {"_offline": True}
        self.root.after(0, lambda: self._after_verify(result))

    def _after_verify(self, result: dict) -> None:
        self._starting = False
        if result.get("_offline"):
            self.status_var.set("라이센스 서버 연결 실패")
            self._set_running(False)
            return
        if not result.get("valid", False):
            self.status_var.set("라이센스 만료")
            self._set_running(False)
            return
        self._start_worker()

    def _start_worker(self) -> None:
        self.stop_event.clear()
        self._set_running(True)
        side = self.side_var.get()
        self.status_var.set("갱신매크로 시작")
        self.log(
            f"[시작] {'구매/상한가' if side == 'buy' else '판매/하한가'} "
            f"열기={self.profile.open_settle_ms}ms 닫기={self.profile.close_settle_ms}ms"
        )
        self._report(True, "갱신매크로 시작")
        self.worker_thread = threading.Thread(
            target=self._worker,
            args=(side,),
            daemon=True,
        )
        self.worker_thread.start()

    def _worker(self, side: str) -> None:
        manager = InactiveManager(self.window_title_var.get().strip(), logger=self.log)
        completed = False
        failed = False
        try:
            completed = FastRenewalRunner(
                manager=manager,
                profile=self.profile,
                side=side,
                stop_event=self.stop_event,
                logger=self.log,
                status=lambda message: self.ui_queue.put(("status", message)),
            ).run()
        except Exception as exc:
            failed = True
            self.log(f"[갱신 오류] {exc}")
            self.log(traceback.format_exc())
        finally:
            self.stop_event.set()
            manager.stop_capture()
            final_status = "갱신 입력 완료" if completed else ("갱신 오류" if failed else "종료됨")
            self._report(False, final_status)
            self.ui_queue.put(("finished", final_status))

    def stop(self) -> None:
        if self.worker_thread is None or not self.worker_thread.is_alive():
            self.status_var.set("대기 중")
            self._set_running(False)
            return
        self.stop_event.set()
        self.status_var.set("정지 요청됨")
        self.stop_button.configure(state=tk.DISABLED)

    def _set_running(self, running: bool) -> None:
        c = self.COLORS
        self.start_button.configure(
            state=tk.DISABLED if running else tk.NORMAL,
            bg=c["disabled"] if running else c["accent"],
        )
        self.stop_button.configure(
            state=tk.NORMAL if running else tk.DISABLED,
            bg=c["accent"] if running else c["disabled"],
        )
        for button in self._capture_buttons:
            button.configure(state=tk.DISABLED if running else tk.NORMAL)

    def _poll_queue(self) -> None:
        while True:
            try:
                kind, message = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status_var.set(message)
            elif kind == "finished":
                self.status_var.set(message)
                self._set_running(False)
                self._refresh_status()
        if not self.closing:
            self.root.after(40, self._poll_queue)

    def _tick_clock(self) -> None:
        self.clock_var.set(time.strftime("%H:%M:%S"))
        if not self.closing:
            self.root.after(1000, self._tick_clock)

    def _report(self, running: bool, message: str) -> None:
        if not self.license_key:
            return
        threading.Thread(
            target=_send_status,
            args=(self.license_key, None, running, message),
            daemon=True,
        ).start()

    def log(self, message: str) -> None:
        if self._log_file is None:
            return
        try:
            self._log_file.write(f"{time.strftime('%H:%M:%S')} {message}\n")
            self._log_file.flush()
        except Exception:
            pass

    def on_close(self) -> None:
        self.closing = True
        self.stop_event.set()
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
        self.root.destroy()


def main() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    root = tk.Tk()
    RenewalLicenseDialog(root, app_dir())
    root.mainloop()


if __name__ == "__main__":
    main()
