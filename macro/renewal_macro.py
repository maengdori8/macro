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
from tkinter import messagebox
from typing import Optional

import numpy as np
from PIL import Image, ImageTk

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
    RENEWAL_CALIBRATION_VERSION,
    RENEWAL_CALIBRATION_FRAMES_PER_OPENING,
    RENEWAL_CALIBRATION_OPENINGS,
    FastRenewalRunner,
    NormalizedPoint,
    NormalizedRect,
    RenewalModalGuard,
    _FastClicker,
    build_calibration_result,
    build_guard_rect,
    encode_gray_png,
    load_renewal_profile,
    price_box_in_guard,
    save_renewal_profile,
    validate_price_region,
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
        self.speed_var = tk.IntVar(value=self.profile.speed_level)
        self.speed_status_var = tk.StringVar()
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
            text="목록: 창 열기 버튼  |  열린 창: 가격·상/하한가·최종 버튼  |  닫기: ESC",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(9),
        ).pack(anchor=tk.W, pady=(0, 8))
        button_row = tk.Frame(calibration_panel, bg=c["panel"])
        button_row.pack(fill=tk.X)
        for text, item in (
            ("창 열기 버튼", "action"),
            ("가격 숫자영역", "price"),
            ("상한가/하한가", "limit"),
            ("창 안 최종 버튼", "confirm"),
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
        tk.Label(
            speed_row,
            text="안정",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(9),
        ).pack(side=tk.LEFT, padx=(0, 8))
        self.speed_scale = tk.Scale(
            speed_row,
            from_=1,
            to=10,
            resolution=1,
            orient=tk.HORIZONTAL,
            showvalue=False,
            variable=self.speed_var,
            command=self._on_speed_changed,
            bg=c["panel"],
            fg=c["text"],
            troughcolor=c["input"],
            activebackground=c["accent"],
            highlightthickness=0,
            bd=0,
            sliderrelief=tk.FLAT,
        )
        self.speed_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            speed_row,
            text="극한",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(9),
        ).pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(
            speed_panel,
            textvariable=self.speed_status_var,
            bg=c["panel"],
            fg=c["text"],
            font=self._font(9, bold=True),
        ).pack(anchor=tk.W, pady=(7, 0))
        tk.Label(
            speed_panel,
            text="한 슬라이더로 클릭 대기·인식 민감도·확정 프레임을 함께 조절합니다.",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(8),
        ).pack(anchor=tk.W, pady=(8, 0))
        self._on_speed_changed(str(self.speed_var.get()))

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
        safe_price = (
            side.price_rect
            and side.guard_rect
            and side.baseline_png
            and side.guard_png
            and side.calibration_openings >= RENEWAL_CALIBRATION_OPENINGS
            and side.calibration_version >= RENEWAL_CALIBRATION_VERSION
        )
        self.setting_status_var.set(
            "공통: 가격이 그대로면 ESC 후 다시 열기\n"
            f"{side_name}: 열기 {mark(side.action_point)} / "
            f"안전 가격영역 {mark(safe_price)} / "
            f"{limit_name} {mark(side.limit_point)} / "
            f"최종 버튼 {mark(side.confirm_point)}"
        )

    def _save_settings(self) -> bool:
        try:
            self.profile.apply_speed_level(self.speed_var.get())
            self.speed_var.set(self.profile.speed_level)
            self._on_speed_changed(str(self.profile.speed_level))
            save_renewal_profile(self.profile)
            return True
        except Exception as exc:
            self.log(f"[설정 오류] {exc}")
            self.status_var.set("설정값 오류")
            return False

    def _on_speed_changed(self, value: str) -> None:
        level = int(round(float(value)))
        required_frames = 2 if level >= 8 else 3
        if level == 10:
            text = "속도 10/10 · 명확한 변경 2프레임 즉시 · 애매하면 주문 금지"
        else:
            mode = "안정" if level <= 3 else ("균형" if level <= 7 else "초고속")
            text = (
                f"속도 {level}/10 · {mode} · 명확한 변경 "
                f"{required_frames}프레임 · 애매하면 주문 금지"
            )
        self.speed_status_var.set(text)

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

    def _capture_guard_samples(
        self,
        guard_rect: NormalizedRect,
        price_box: tuple[int, int, int, int],
        action_point: NormalizedPoint,
        expected_size: tuple[int, int],
    ) -> list[list[np.ndarray]]:
        """서로 다른 5회 팝업 열기에서 회당 새 WGC 프레임 4장을 모읍니다."""
        manager = InactiveManager(self.window_title_var.get().strip(), logger=self.log)
        sessions: list[list[np.ndarray]] = []
        try:
            if not manager.find_window():
                raise RuntimeError("FC ONLINE 창을 찾지 못했습니다.")
            full_frame = manager.capture_client_area(window_validated=True)
            if full_frame is None:
                raise RuntimeError("팝업 보정용 WGC 프레임을 받지 못했습니다.")
            frame_height, frame_width = full_frame.shape[:2]
            if (frame_width, frame_height) != expected_size:
                raise RuntimeError(
                    "좌표 선택 중 게임 창 크기가 바뀌었습니다. 다시 설정하세요."
                )
            gx1, gy1, gx2, gy2 = guard_rect.to_pixels(frame_width, frame_height)
            reference_guard = full_frame[gy1:gy2, gx1:gx2].copy()
            provisional_guard = RenewalModalGuard(
                reference_guard,
                price_box,
                shift_limit=4,
            )
            engine = manager.capture_engine
            if engine is None:
                raise RuntimeError("WGC 캡처 엔진이 시작되지 않았습니다.")
            if not hasattr(engine, "get_latest_frame_packet"):
                raise RuntimeError("WGC 프레임 순번 API를 사용할 수 없습니다.")
            engine.set_capture_region((gx1, gy1, gx2, gy2))
            clicker = _FastClicker(manager, frame_width, frame_height)
            action = clicker.resolve(action_point)
            last_sequence = -1
            last_timestamp = float("-inf")

            def flush() -> None:
                nonlocal last_sequence, last_timestamp
                packet = engine.get_latest_frame_packet(timeout=0.0)
                if packet is not None:
                    last_sequence = max(last_sequence, packet.sequence_id)
                    last_timestamp = max(
                        last_timestamp,
                        packet.captured_at,
                    )

            def next_packet(deadline: float):
                nonlocal last_sequence, last_timestamp
                while time.monotonic() < deadline:
                    packet = engine.get_latest_frame_packet(timeout=0.10)
                    if packet is None:
                        if engine.closed_event.is_set():
                            raise RuntimeError("WGC 캡처 세션이 종료되었습니다.")
                        continue
                    if (
                        packet.sequence_id <= last_sequence
                        or packet.captured_at <= last_timestamp
                    ):
                        continue
                    last_sequence = packet.sequence_id
                    last_timestamp = packet.captured_at
                    return packet
                return None

            def wait_until_closed() -> None:
                deadline = time.monotonic() + 1.0
                while True:
                    packet = next_packet(deadline)
                    if packet is None:
                        raise RuntimeError("팝업 닫힘을 확인하지 못했습니다.")
                    registration = provisional_guard.register(
                        packet.image,
                        0.0,
                        0.0,
                    )
                    if not registration.valid:
                        return

            flush()
            for opening in range(RENEWAL_CALIBRATION_OPENINGS):
                if opening > 0:
                    clicker.press_escape()
                    wait_until_closed()
                    flush()
                    clicker.click_client(action)
                    flush()

                self.status_var.set(
                    f"v6 안전 보정 {opening + 1}/{RENEWAL_CALIBRATION_OPENINGS} · "
                    "창을 조작하지 마세요"
                )
                self.root.update_idletasks()
                session: list[np.ndarray] = []
                deadline = time.monotonic() + 2.0
                while (
                    len(session) < RENEWAL_CALIBRATION_FRAMES_PER_OPENING
                    and time.monotonic() < deadline
                ):
                    packet = next_packet(deadline)
                    if packet is None:
                        break
                    sample = packet.image
                    if sample.shape != reference_guard.shape:
                        raise RuntimeError("보정 중 게임 창 크기가 바뀌었습니다.")
                    registration = provisional_guard.register(sample, 0.0, 0.0)
                    if not registration.valid:
                        session.clear()
                        continue
                    session.append(sample.copy())
                if len(session) < RENEWAL_CALIBRATION_FRAMES_PER_OPENING:
                    raise RuntimeError(
                        f"{opening + 1}번째 팝업의 안전 프레임이 부족합니다."
                    )
                sessions.append(session)
            return sessions
        finally:
            manager.stop_capture()

    def _confirm_price_preview(self, image: np.ndarray) -> bool:
        """실제 감지될 한 줄을 보여주고 저장 여부를 확인합니다."""
        result = {"accepted": False}
        dialog = tk.Toplevel(self.root)
        dialog.title("실제 가격 감지영역 미리보기")
        dialog.configure(bg=self.COLORS["bg"], padx=18, pady=18)
        dialog.resizable(False, False)
        dialog.transient(self.root)

        source = Image.fromarray(image)
        scale = max(2, min(6, 660 // max(1, source.width)))
        preview = source.resize(
            (source.width * scale, source.height * scale),
            Image.Resampling.NEAREST,
        )
        photo = ImageTk.PhotoImage(preview, master=dialog)
        label = tk.Label(
            dialog,
            image=photo,
            bg="#FFFFFF",
            bd=2,
            relief=tk.SOLID,
        )
        label.image = photo
        label.pack(pady=(0, 12))
        tk.Label(
            dialog,
            text=(
                "이 한 줄만 가격으로 감지합니다.\n"
                "'0회', 다른 가격 줄, 큰 여백이 보이면 다시 선택하세요."
            ),
            justify=tk.LEFT,
            bg=self.COLORS["bg"],
            fg=self.COLORS["text"],
            font=self._font(10, bold=True),
        ).pack(anchor=tk.W, pady=(0, 14))

        buttons = tk.Frame(dialog, bg=self.COLORS["bg"])
        buttons.pack(fill=tk.X)

        def finish(accepted: bool) -> None:
            result["accepted"] = accepted
            dialog.destroy()

        self._button(
            buttons,
            "이 영역 저장",
            lambda: finish(True),
            small=True,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._button(
            buttons,
            "다시 선택",
            lambda: finish(False),
            small=True,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        dialog.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        dialog.update_idletasks()
        dialog.geometry(
            f"+{self.root.winfo_rootx() + 40}+{self.root.winfo_rooty() + 80}"
        )
        dialog.grab_set()
        self.root.wait_window(dialog)
        return bool(result["accepted"])

    def calibrate(self, item: str) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.status_var.set("실행 중에는 좌표 변경 불가")
            return
        frame = self._capture_frame()
        if frame is None:
            return

        side_name = "구매" if self.side_var.get() == "buy" else "판매"
        instruction = {
            "action": f"목록 화면에서 가격 창을 여는 '{side_name}' 버튼 가운데를 클릭하세요.",
            "price": f"{side_name} 창에서 감시할 가격 숫자 부분만 좁게 드래그하세요.",
            "limit": f"{side_name} 창에서 {'상한가' if self.side_var.get() == 'buy' else '하한가'} 항목 가운데를 클릭하세요.",
            "confirm": f"열린 {side_name} 창 하단의 최종 '{side_name}' 버튼 가운데를 클릭하세요.",
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
            if side.action_point is None:
                self.status_var.set("먼저 창 열기 버튼을 설정하세요")
                messagebox.showerror(
                    "창 열기 버튼 필요",
                    "5회 독립 팝업 보정을 위해 먼저 창 열기 구매/판매 버튼을 설정하세요.",
                    parent=self.root,
                )
                return
            frame_height, frame_width = frame.shape[:2]
            x1, y1, x2, y2 = selection.to_pixels(frame_width, frame_height)
            selected_price = frame[y1:y2, x1:x2]
            validation = validate_price_region(selected_price)
            if not validation.valid:
                self.status_var.set("가격영역 거부 · 숫자 한 줄만 다시 선택")
                messagebox.showerror(
                    "가격영역을 다시 선택하세요",
                    validation.message,
                    parent=self.root,
                )
                return

            guard_rect = build_guard_rect(selection, frame_width, frame_height)
            price_box = price_box_in_guard(
                selection,
                guard_rect,
                frame_width,
                frame_height,
            )
            self.status_var.set("팝업 안전 보정 중 · 창을 그대로 두세요")
            self.root.update_idletasks()
            try:
                samples = self._capture_guard_samples(
                    guard_rect,
                    price_box,
                    side.action_point,
                    (frame_width, frame_height),
                )
                calibration = build_calibration_result(samples, price_box)
            except Exception as exc:
                self.status_var.set("안전 보정 실패 · 다시 선택")
                messagebox.showerror(
                    "안전 보정 실패",
                    str(exc),
                    parent=self.root,
                )
                return

            if not self._confirm_price_preview(calibration.baseline):
                self.status_var.set("가격영역 저장 취소 · 다시 선택하세요")
                return

            # 모든 검증이 끝난 뒤 한 번에 교체하여 실패 시 기존 설정을 보존합니다.
            side.price_rect = selection
            side.guard_rect = guard_rect
            side.baseline_png = encode_gray_png(calibration.baseline)
            side.guard_png = encode_gray_png(calibration.guard)
            side.noise_global = calibration.noise_global
            side.noise_slice = calibration.noise_slice
            side.guard_luma_noise = calibration.guard_luma_noise
            side.guard_edge_noise = calibration.guard_edge_noise
            side.unchanged_limit = calibration.unchanged_limit
            side.stability_limit = calibration.stability_limit
            side.registration_shift_limit = calibration.registration_shift_limit
            side.calibration_openings = calibration.calibration_openings
            side.calibration_version = RENEWAL_CALIBRATION_VERSION
        elif isinstance(selection, NormalizedPoint):
            if item == "action":
                side.action_point = selection
            elif item == "limit":
                side.limit_point = selection
            elif item == "confirm":
                side.confirm_point = selection
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
            f"[시작 v6] {'구매/상한가' if side == 'buy' else '판매/하한가'} "
            f"속도={self.profile.speed_level}/10 "
            f"열기={self.profile.open_settle_ms}ms "
            f"명확한 변경={2 if self.profile.speed_level >= 8 else 3}프레임 "
            "애매하면 주문 금지"
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
        self.speed_scale.configure(state=tk.DISABLED if running else tk.NORMAL)

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
