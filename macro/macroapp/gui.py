from __future__ import annotations
import os
import platform
import queue
import random
import sys
import threading
import time
import traceback
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import scrolledtext
from typing import Any, Optional

import cv2
import numpy as np

from macroapp import winapi
from macroapp import input_gamepad
from macroapp.input_gamepad import _get_gamepad, send_gamepad_button, send_gamepad_trigger
from macroapp.paths import APP_VERSION, app_dir
from macroapp.logging_util import LogCallback
from macroapp.config import (
    TargetImage, WINDOW_TITLE, LOOP_SLEEP_SECONDS, WINDOW_RETRY_SECONDS,
    CLICK_JITTER_PIXELS, MOUSE_HOVER_BEFORE_CLICK_SECONDS,
    DEFAULT_REGION_X, DEFAULT_REGION_Y, DEFAULT_REGION_WIDTH, DEFAULT_REGION_HEIGHT,
    load_targets, load_target_definitions,
)
from macroapp.license_client import (
    STATUS_REPORT_INTERVAL_SECONDS, _send_status, get_hwid, verify_license_server,
    format_remaining_time, load_saved_license, save_license_key,
)
from macroapp.matching import find_template_center, downscale_screen
from macroapp.window import InactiveManager

class AutomationApp:
    """tkinter UI와 자동화 스레드를 관리합니다."""

    def __init__(self, root: tk.Tk, license_key: Optional[str] = None):
        self.root = root
        self.license_key = license_key
        self.license_info: Optional[dict] = None

        self.root.title("비활성 창 이미지 자동화 테스트")
        self.root.geometry("980x760+80+80")
        self.root.minsize(900, 640)
        self.ui_preview_only = platform.system() != "Windows"
        if self.ui_preview_only:
            self.root.title("비활성 창 이미지 자동화 테스트 - UI 미리보기")

        self.window_title_var = tk.StringVar(value=WINDOW_TITLE)
        initial_status = "UI 미리보기" if self.ui_preview_only else "대기 중"
        self.status_var = tk.StringVar(value=initial_status)
        self.base_dir = app_dir()
        self.target_definitions = load_target_definitions(self.base_dir)
        self.target_names = tuple(target.name for target in self.target_definitions)
        self.threshold_lock = threading.Lock()
        self.threshold_values = {
            target.name: target.threshold
            for target in self.target_definitions
        }
        self.threshold_vars = {
            name: tk.DoubleVar(value=value)
            for name, value in self.threshold_values.items()
        }
        self.threshold_label_vars = {
            name: tk.StringVar(value=f"{value:.2f}")
            for name, value in self.threshold_values.items()
        }
        self.capture_mode_var = tk.StringVar(value="wgc")
        self.click_mode_var = tk.StringVar(value="postmessage")
        self.region_vars = {
            "x": tk.IntVar(value=DEFAULT_REGION_X),
            "y": tk.IntVar(value=DEFAULT_REGION_Y),
            "width": tk.IntVar(value=DEFAULT_REGION_WIDTH),
            "height": tk.IntVar(value=DEFAULT_REGION_HEIGHT),
        }

        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.ui_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.closing = False
        # 상태 전송 전용 단일 워커 풀 (스레드 누적 방지)
        self._status_executor = ThreadPoolExecutor(max_workers=1)
        self._log_dirty = False
        self._close_deadline = 0.0

        # 로그 파일 초기화
        self._log_file = None
        try:
            log_dir = self.base_dir / "logs"
            log_dir.mkdir(exist_ok=True)
            log_path = log_dir / f"macro_{time.strftime('%Y-%m-%d')}.log"
            self._log_file = open(log_path, "a", encoding="utf-8")
            self._log_file.write(f"\n{'='*50}\n")
            self._log_file.write(f"세션 시작: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            self._log_file.write(f"버전: {APP_VERSION}\n")
            self._log_file.write(f"{'='*50}\n")
        except Exception:
            pass

        self._build_ui()
        self._bind_shortcuts()
        self._set_button_state(running=False)
        self._bring_window_to_front()
        self._poll_ui_queue()
        self._flush_log_periodic()

        # 가상 게임패드를 미리 생성해 게임이 컨트롤러를 일찍 인식하게 합니다.
        if winapi.vg is not None and not self.ui_preview_only:
            try:
                _get_gamepad()
                self.log("[게임패드] 가상 Xbox 컨트롤러를 연결했습니다.")
            except Exception as exc:
                self.log(f"[게임패드] 가상 컨트롤러 생성 실패: {exc}")

        self.log("프로그램을 시작했습니다.")
        if self.license_info:
            remaining = format_remaining_time(self.license_info["remaining_seconds"])
            self.log(f"[라이센스] {self.license_info['days']}일권 인증됨 - {remaining}")
        self.log("F8: 시작, F9 또는 ESC: 중지")
        self.log("대상 창은 백그라운드에 있어도 되지만 최소화되어 있으면 안 됩니다.")
        if self.ui_preview_only:
            self.log("[UI 미리보기] 현재 OS가 Windows가 아니므로 자동화 실행은 비활성화했습니다.")
            self.log("              UI 확인만 가능하며, 실제 캡처/클릭은 Windows에서 실행하세요.")

    def _bring_window_to_front(self) -> None:
        """창이 뒤에 숨어 보이지 않는 일을 줄이기 위해 잠깐 최상위로 올립니다."""

        self.root.update_idletasks()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

        # macOS에서 처음 뜬 tkinter 창이 다른 앱 뒤로 가는 경우가 있어 잠깐만 topmost를 켭니다.
        self.root.attributes("-topmost", True)
        self.root.after(1200, lambda: self.root.attributes("-topmost", False))

    def _build_ui(self) -> None:
        """간단한 테스트 도구 형태의 UI를 만듭니다."""

        if self.ui_preview_only:
            self._build_preview_ui()
            return

        # macOS의 기본 Tcl/Tk는 다크 모드에서 배경색을 강하게 바꾸는 경우가 있어,
        # 미리보기에서도 확실히 읽히도록 어두운 배경 + 밝은 글자로 고정합니다.
        bg = "#1E1E1E"
        panel_bg = "#252526"
        input_bg = "#111827"
        text_color = "#F9FAFB"
        accent = "#7AB7FF"

        self.root.configure(bg=bg)

        main_frame = tk.Frame(self.root, bg=bg, padx=12, pady=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        title_frame = tk.Frame(
            main_frame,
            bg=panel_bg,
            padx=8,
            pady=8,
            relief=tk.SOLID,
            bd=1,
        )
        title_frame.pack(fill=tk.X)

        tk.Label(
            title_frame,
            text="대상 창",
            bg=panel_bg,
            fg=accent,
            font=("Arial", 12, "bold"),
        ).pack(anchor=tk.W)

        tk.Label(
            title_frame,
            text="창 제목 일부",
            bg=panel_bg,
            fg=text_color,
        ).pack(anchor=tk.W, pady=(8, 4))
        self.title_entry = tk.Entry(
            title_frame,
            textvariable=self.window_title_var,
            bg=input_bg,
            fg=text_color,
            insertbackground=text_color,
            relief=tk.SOLID,
            bd=1,
        )
        self.title_entry.pack(fill=tk.X)

        button_frame = tk.Frame(main_frame, bg=bg)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        self.start_button = tk.Button(
            button_frame,
            text="시작 (F8)",
            command=self.start_automation,
            width=14,
        )
        self.start_button.pack(side=tk.LEFT)

        self.stop_button = tk.Button(
            button_frame,
            text="종료 (F9/ESC)",
            command=self.stop_automation,
            width=14,
        )
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))

        status_frame = tk.Frame(main_frame, bg=bg)
        status_frame.pack(fill=tk.X, pady=(10, 0))
        tk.Label(status_frame, text="상태", bg=bg, fg=text_color).pack(side=tk.LEFT)
        self.status_label = tk.Label(
            status_frame,
            textvariable=self.status_var,
            bg=bg,
            fg=accent,
        )
        self.status_label.pack(side=tk.LEFT, padx=(8, 0))

        mode_frame = tk.Frame(
            main_frame,
            bg=panel_bg,
            padx=8,
            pady=8,
            relief=tk.SOLID,
            bd=1,
        )
        mode_frame.pack(fill=tk.X, pady=(10, 0))

        tk.Label(
            mode_frame,
            text="캡처/클릭 모드",
            bg=panel_bg,
            fg=accent,
            font=("Arial", 12, "bold"),
        ).pack(anchor=tk.W, pady=(0, 6))

        capture_row = tk.Frame(mode_frame, bg=panel_bg)
        capture_row.pack(fill=tk.X, pady=2)
        tk.Label(
            capture_row,
            text="캡처",
            bg=panel_bg,
            fg=text_color,
            width=10,
            anchor=tk.W,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            capture_row,
            text="비활성 WGC",
            variable=self.capture_mode_var,
            value="wgc",
            command=self.on_capture_mode_changed,
            bg=panel_bg,
            fg=text_color,
            selectcolor=input_bg,
            activebackground=panel_bg,
            activeforeground=text_color,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            capture_row,
            text="화면 영역 캡처",
            variable=self.capture_mode_var,
            value="region",
            command=self.on_capture_mode_changed,
            bg=panel_bg,
            fg=text_color,
            selectcolor=input_bg,
            activebackground=panel_bg,
            activeforeground=text_color,
        ).pack(side=tk.LEFT, padx=(12, 0))

        click_row = tk.Frame(mode_frame, bg=panel_bg)
        click_row.pack(fill=tk.X, pady=2)
        tk.Label(
            click_row,
            text="클릭",
            bg=panel_bg,
            fg=text_color,
            width=10,
            anchor=tk.W,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            click_row,
            text="PostMessage",
            variable=self.click_mode_var,
            value="postmessage",
            bg=panel_bg,
            fg=text_color,
            selectcolor=input_bg,
            activebackground=panel_bg,
            activeforeground=text_color,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            click_row,
            text="마우스 이동 후 복귀",
            variable=self.click_mode_var,
            value="mouse",
            bg=panel_bg,
            fg=text_color,
            selectcolor=input_bg,
            activebackground=panel_bg,
            activeforeground=text_color,
        ).pack(side=tk.LEFT, padx=(12, 0))

        region_row = tk.Frame(mode_frame, bg=panel_bg)
        region_row.pack(fill=tk.X, pady=(6, 0))
        tk.Label(
            region_row,
            text="영역",
            bg=panel_bg,
            fg=text_color,
            width=10,
            anchor=tk.W,
        ).pack(side=tk.LEFT)
        for key, label in (
            ("x", "X"),
            ("y", "Y"),
            ("width", "W"),
            ("height", "H"),
        ):
            tk.Label(region_row, text=label, bg=panel_bg, fg=text_color).pack(side=tk.LEFT)
            tk.Entry(
                region_row,
                textvariable=self.region_vars[key],
                width=7,
                bg=input_bg,
                fg=text_color,
                insertbackground=text_color,
                relief=tk.SOLID,
                bd=1,
            ).pack(side=tk.LEFT, padx=(4, 10))

        threshold_frame = tk.Frame(
            main_frame,
            bg=panel_bg,
            padx=8,
            pady=8,
            relief=tk.SOLID,
            bd=1,
        )
        threshold_frame.pack(fill=tk.X, pady=(10, 0))

        tk.Label(
            threshold_frame,
            text="타겟별 임계값",
            bg=panel_bg,
            fg=accent,
            font=("Arial", 12, "bold"),
        ).pack(anchor=tk.W, pady=(0, 6))

        for name in self.target_names:
            row = tk.Frame(threshold_frame, bg=panel_bg)
            row.pack(fill=tk.X, pady=2)

            tk.Label(
                row,
                text=name,
                bg=panel_bg,
                fg=text_color,
                width=10,
                anchor=tk.W,
            ).pack(side=tk.LEFT)

            tk.Scale(
                row,
                from_=0.5,
                to=1.0,
                resolution=0.01,
                orient=tk.HORIZONTAL,
                variable=self.threshold_vars[name],
                command=lambda value, target_name=name: self.update_threshold(
                    target_name,
                    value,
                ),
                bg=panel_bg,
                fg=text_color,
                troughcolor=input_bg,
                highlightthickness=0,
                length=360,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

            tk.Label(
                row,
                textvariable=self.threshold_label_vars[name],
                bg=panel_bg,
                fg=accent,
                width=5,
            ).pack(side=tk.LEFT, padx=(8, 0))

        log_frame = tk.Frame(
            main_frame,
            bg=panel_bg,
            padx=8,
            pady=8,
            relief=tk.SOLID,
            bd=1,
        )
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        tk.Label(
            log_frame,
            text="로그",
            bg=panel_bg,
            fg=accent,
            font=("Arial", 12, "bold"),
        ).pack(anchor=tk.W, pady=(0, 6))

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=22,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=input_bg,
            fg=text_color,
            insertbackground=text_color,
            relief=tk.SOLID,
            bd=1,
            font=("Consolas", 10),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def _build_preview_ui(self) -> None:
        """
        Mac 전용 UI 미리보기입니다.

        일부 Mac Tcl/Tk 조합에서는 Label, Entry, Text가 보이지 않는 경우가 있어
        미리보기 모드에서는 화면에 확실히 표시되는 Button 위젯만 사용합니다.
        Windows 실행 시에는 위의 일반 UI가 사용됩니다.
        """

        self.root.configure(bg="#1E1E1E")
        main_frame = tk.Frame(self.root, bg="#1E1E1E", padx=18, pady=18)
        main_frame.pack(fill=tk.BOTH, expand=True)

        self.title_entry = tk.Button(
            main_frame,
            text=f"대상 창 제목: {self.window_title_var.get()}",
            anchor=tk.W,
            justify=tk.LEFT,
            relief=tk.GROOVE,
            height=2,
        )
        self.title_entry.pack(fill=tk.X, pady=(0, 12))

        button_frame = tk.Frame(main_frame, bg="#1E1E1E")
        button_frame.pack(fill=tk.X, pady=(0, 12))

        self.start_button = tk.Button(
            button_frame,
            text="시작 (F8) - 미리보기 로그만 표시",
            command=self.start_automation,
            width=28,
            height=2,
        )
        self.start_button.pack(side=tk.LEFT)

        self.stop_button = tk.Button(
            button_frame,
            text="종료 (F9/ESC)",
            command=self.stop_automation,
            width=18,
            height=2,
        )
        self.stop_button.pack(side=tk.LEFT, padx=(10, 0))

        self.status_label = tk.Button(
            main_frame,
            textvariable=self.status_var,
            anchor=tk.W,
            justify=tk.LEFT,
            relief=tk.GROOVE,
            height=2,
        )
        self.status_label.pack(fill=tk.X, pady=(0, 12))

        mode_text = (
            f"캡처 모드: {self.capture_mode_var.get()}\n"
            f"클릭 모드: {self.click_mode_var.get()}\n"
            f"영역: {DEFAULT_REGION_X}, {DEFAULT_REGION_Y}, "
            f"{DEFAULT_REGION_WIDTH}, {DEFAULT_REGION_HEIGHT}"
        )
        self.preview_mode_button = tk.Button(
            main_frame,
            text=mode_text,
            anchor=tk.W,
            justify=tk.LEFT,
            relief=tk.GROOVE,
            height=3,
        )
        self.preview_mode_button.pack(fill=tk.X, pady=(0, 12))

        threshold_text = "\n".join(
            f"{name} 임계값: {self.get_threshold(name):.2f}"
            for name in self.target_names
        )
        self.preview_threshold_button = tk.Button(
            main_frame,
            text=threshold_text,
            anchor=tk.W,
            justify=tk.LEFT,
            relief=tk.GROOVE,
            height=len(self.target_names),
        )
        self.preview_threshold_button.pack(fill=tk.X, pady=(0, 12))

        self.preview_log_messages: list[str] = []
        self.preview_log_frame = tk.Frame(
            main_frame,
            relief=tk.GROOVE,
            bd=2,
        )
        self.preview_log_frame.pack(fill=tk.BOTH, expand=True)

        self.preview_log_rows: list[tk.Button] = []
        for _index in range(12):
            row = tk.Button(
                self.preview_log_frame,
                text="",
                anchor=tk.W,
                justify=tk.LEFT,
                relief=tk.FLAT,
                height=1,
            )
            row.pack(fill=tk.X, padx=6, pady=1)
            self.preview_log_rows.append(row)

    def _bind_shortcuts(self) -> None:
        """UI가 활성화되어 있을 때 동작하는 단축키를 등록합니다."""

        self.root.bind("<F8>", lambda _event: self.start_automation())
        self.root.bind("<F9>", lambda _event: self.stop_automation())
        self.root.bind("<Escape>", lambda _event: self.stop_automation())

        # 기존 콘솔 예제의 q 종료 습관을 보조로 유지합니다.
        self.root.bind("<KeyPress-q>", lambda _event: self.stop_automation())
        self.root.bind("<KeyPress-Q>", lambda _event: self.stop_automation())

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_capture_mode_changed(self) -> None:
        """화면 영역 캡처 모드에서는 기본 클릭 방식을 마우스 이동/복귀로 맞춥니다."""

        if self.capture_mode_var.get() == "region":
            self.click_mode_var.set("mouse")
            self.log("[설정] 화면 영역 캡처 모드에서는 마우스 이동 후 복귀 클릭을 사용합니다.")

    def get_region_from_ui(self) -> Optional[tuple[int, int, int, int]]:
        """UI의 영역 입력값을 안전하게 읽습니다."""

        try:
            x = int(self.region_vars["x"].get())
            y = int(self.region_vars["y"].get())
            width = int(self.region_vars["width"].get())
            height = int(self.region_vars["height"].get())
        except Exception as exc:
            self.log(f"[오류] 화면 영역 값이 올바른 숫자가 아닙니다: {exc}")
            return None

        if width <= 0 or height <= 0:
            self.log("[오류] 화면 영역의 W/H는 1 이상이어야 합니다.")
            return None

        return x, y, width, height

    def start_automation(self) -> None:
        """시작 버튼 또는 F8 키로 자동화 스레드를 시작합니다.

        라이센스 검증은 UI를 얼리지 않도록 백그라운드 스레드에서 수행하고,
        결과를 root.after로 받아 이어서 시작합니다."""

        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.log("[안내] 이미 실행 중입니다.")
            self.set_status("실행 중")
            return
        if getattr(self, "_starting", False):
            return  # 인증 진행 중 중복 클릭 방지

        self._starting = True
        self._set_button_state(running=True)

        if self.license_key and not self.ui_preview_only:
            self.set_status("인증 확인 중...")
            threading.Thread(target=self._verify_then_start, daemon=True).start()
        else:
            self._do_start()

    def _verify_then_start(self) -> None:
        """백그라운드: 라이센스 검증 후 결과를 UI 스레드로 전달."""
        try:
            hwid = get_hwid()
            sr = verify_license_server(self.license_key, hwid)
        except Exception:
            sr = {"_offline": True}
        self.root.after(0, lambda: self._after_verify(sr))

    def _after_verify(self, sr: dict) -> None:
        """UI 스레드: 검증 결과에 따라 시작하거나 중단."""
        if sr.get("_offline"):
            self.log("[라이센스] 서버에 연결할 수 없습니다. 인터넷 연결을 확인하세요.")
            self.set_status("서버 연결 실패")
            self._set_button_state(running=False)
            self._starting = False
            return
        if not sr.get("valid", False):
            self.log(f"[라이센스] {sr.get('message', '인증 실패')} 프로그램을 재시작하고 새 키를 입력하세요.")
            self.set_status("라이센스 만료")
            self._set_button_state(running=False)
            self._starting = False
            return
        self._do_start()

    def _do_start(self) -> None:
        """실제 자동화 시작 (라이센스 통과 후)."""
        self._starting = False

        if self.ui_preview_only:
            self.log("[UI 미리보기] Mac에서는 자동화 루프를 실행하지 않습니다.")
            self.log("              Windows에서 실행하면 시작 버튼과 F8이 자동화를 시작합니다.")
            self.set_status("UI 미리보기")
            self._set_button_state(running=False)
            return

        capture_mode = self.capture_mode_var.get()
        click_mode = self.click_mode_var.get()
        region = self.get_region_from_ui()
        if region is None:
            self.set_status("오류 발생")
            self._set_button_state(running=False)
            return

        if capture_mode == "region":
            click_mode = "mouse"
            self.click_mode_var.set("mouse")

        window_title = self.window_title_var.get().strip()
        needs_window = (
            capture_mode == "wgc"
            or click_mode == "postmessage"
            or any(target.action == "message" for target in self.target_definitions)
        )
        if needs_window and not window_title:
            self.log("[오류] 대상 창 제목을 입력하세요.")
            self.set_status("오류 발생")
            self._set_button_state(running=False)
            return

        self.stop_event.clear()
        self._set_button_state(running=True)
        self.set_status("실행 중")
        self.log(
            f"[시작] 자동화를 시작합니다. "
            f"캡처={capture_mode}, 클릭={click_mode}, 영역={region}, 창='{window_title}'"
        )

        self.worker_thread = threading.Thread(
            target=self._automation_loop,
            args=(window_title, capture_mode, click_mode, region),
            daemon=True,
        )
        self.worker_thread.start()

    def update_threshold(self, target_name: str, value: str) -> None:
        """슬라이더 값 변경을 thread-safe하게 저장합니다."""

        threshold = max(0.0, min(1.0, float(value)))
        with self.threshold_lock:
            self.threshold_values[target_name] = threshold
        self.threshold_label_vars[target_name].set(f"{threshold:.2f}")

    def get_threshold(self, target_name: str) -> float:
        """작업 스레드에서 사용할 현재 임계값을 가져옵니다."""

        with self.threshold_lock:
            return float(self.threshold_values.get(target_name, 0.8))

    def apply_current_thresholds(self, targets: list[TargetImage]) -> None:
        """UI에서 조절한 임계값을 TargetImage 목록에 반영합니다."""

        for target in targets:
            target.threshold = self.get_threshold(target.name)

    def stop_automation(self) -> None:
        """종료 버튼, F9, ESC, q 키로 자동화를 안전하게 중단 요청합니다."""

        if self.ui_preview_only:
            self.log("[UI 미리보기] 실행 중인 자동화가 없습니다.")
            self.set_status("UI 미리보기")
            self._set_button_state(running=False)
            return

        if self.worker_thread is None or not self.worker_thread.is_alive():
            self.log("[안내] 현재 실행 중인 자동화가 없습니다.")
            self.set_status("대기 중")
            self._set_button_state(running=False)
            return

        self.stop_event.set()
        self.set_status("종료 요청됨")
        self.log("[중지 요청] 자동화 루프를 안전하게 중단합니다.")
        self.stop_button.configure(state=tk.DISABLED)

    def on_close(self) -> None:
        """창 닫기 버튼을 눌렀을 때도 자동화 스레드를 자연스럽게 멈춥니다."""

        self.closing = True
        self._close_log_file()
        # 상태 풀은 여기서 닫지 않습니다: 워커의 finally가 '종료' 상태를 제출한 뒤
        # 인터프리터 종료 시 atexit가 풀을 join하여 마지막 전송이 완료됩니다.
        # (여기서 shutdown하면 워커의 마지막 제출이 거부될 수 있음.)
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.stop_event.set()
            self.set_status("종료 요청됨")
            self._close_deadline = time.monotonic() + 5.0
            self.root.after(100, self._destroy_when_worker_stops)
            return

        self.root.destroy()

    def _close_log_file(self) -> None:
        """로그 파일을 안전하게 닫습니다."""
        if self._log_file is not None:
            try:
                self._log_file.write(f"세션 종료: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    def _destroy_when_worker_stops(self) -> None:
        """작업 스레드가 끝난 뒤 tkinter 창을 닫습니다."""

        if self.worker_thread is not None and self.worker_thread.is_alive():
            # 워커가 5초 안에 안 멈추면(블로킹 호출에 걸림) 강제로 닫습니다.
            # daemon 스레드라 프로세스 종료 시 함께 정리됩니다.
            if time.monotonic() > self._close_deadline:
                self._log_to_file_only("[종료] 워커가 응답하지 않아 강제 종료합니다.")
                self.root.destroy()
                return
            self.root.after(100, self._destroy_when_worker_stops)
            return
        self.root.destroy()

    def _automation_loop(
        self,
        window_title: str,
        capture_mode: str,
        click_mode: str,
        region: tuple[int, int, int, int],
    ) -> None:
        """
        실제 자동화 루프입니다.

        tkinter는 메인 스레드에서만 UI를 만지는 것이 안전하므로,
        이 함수는 직접 라벨/로그 위젯을 수정하지 않고 queue에 메시지만 넣습니다.
        """

        manager: Optional[InactiveManager] = None

        try:
            self.queue_status("대상 이미지 로드 중")
            targets = load_targets(
                self.base_dir,
                self.queue_log,
                definitions=self.target_definitions,
            )
            if targets is None:
                self.queue_status("오류 발생")
                self.queue_log("[종료] 타겟 이미지 준비에 실패하여 실행을 중단합니다.")
                return

            # ViGEm 드라이버 미설치 경고
            has_key_targets = any(t.action == "key" for t in targets)
            if has_key_targets and winapi.vg is None:
                self.queue_log("[경고] vgamepad를 사용할 수 없어 키 입력 타겟이 작동하지 않습니다.")
                self.queue_log(f"       원본 오류: {winapi.VGAMEPAD_IMPORT_ERROR}")
                self.queue_log("       ViGEm Bus Driver를 설치하세요:")
                self.queue_log("       https://github.com/nefarius/ViGEmBus/releases")

            requires_window = (
                capture_mode == "wgc"
                or click_mode == "postmessage"
                or any(target.action == "message" for target in targets)
            )

            if requires_window:
                manager = InactiveManager(window_title, logger=self.queue_log)

            # 상태 전송 타이머
            last_status_report = 0.0

            # 시작 상태 전송 (단일 워커 풀)
            self._report_status(running=True, message="매크로 시작")

            while not self.stop_event.is_set():
                # 주기적 상태 전송
                now_mono = time.monotonic()
                if self.license_key and now_mono - last_status_report >= STATUS_REPORT_INTERVAL_SECONDS:
                    last_status_report = now_mono
                    self._report_status(running=True, message="실행 중")
                self.apply_current_thresholds(targets)

                if requires_window and manager is not None and not manager.is_valid_window():
                    self.queue_status("대상 창 검색 중")
                    manager.find_window()

                    if self.stop_event.is_set():
                        break

                    if not manager.is_valid_window():
                        self.queue_log(f"[대기] {WINDOW_RETRY_SECONDS}초 후 창을 다시 찾습니다.")
                        self.interruptible_sleep(WINDOW_RETRY_SECONDS)
                        continue

                self.queue_status("실행 중")
                if capture_mode == "region":
                    screen_gray = self.capture_screen_region(region)
                elif manager is not None:
                    screen_gray = manager.capture_client_area()
                else:
                    self.queue_log("[오류] 캡처 관리자가 준비되지 않았습니다.")
                    screen_gray = None

                if screen_gray is None:
                    self.interruptible_sleep(LOOP_SLEEP_SECONDS)
                    continue

                found_any = False

                # 프레임당 1회만 축소해 모든 타겟이 공유합니다(중복 축소 제거).
                small_screen = downscale_screen(screen_gray)

                # targets.json에 적힌 순서대로 탐지합니다.
                for target in targets:
                    if self.stop_event.is_set():
                        break

                    center, score = find_template_center(
                        screen_gray, target, self.queue_log, small_screen=small_screen
                    )
                    if center is None:
                        continue

                    found_any = True
                    base_x, base_y = center
                    x, y = self.apply_click_jitter(target, base_x, base_y)
                    self.queue_status("이미지 감지 성공")
                    self.queue_log(
                        f"[감지] {target.name} "
                        f"(점수: {score:.3f}, 위치: {base_x},{base_y})"
                    )

                    if target.action == "key":
                        action_ok = self.dispatch_key_press(manager, target)
                    elif target.action == "message":
                        action_ok = self.dispatch_win32_message(manager, target)
                    else:
                        action_ok = self.dispatch_click(
                            manager,
                            click_mode,
                            capture_mode,
                            region,
                            x,
                            y,
                            target,
                        )

                    if action_ok:
                        status_by_action = {
                            "key": "키 입력 완료",
                            "message": "메시지 완료",
                        }
                        self.queue_status(status_by_action.get(target.action, "클릭 완료"))
                        if target.wait_after_click > 0:
                            self.interruptible_sleep(target.wait_after_click)

                    # 한 루프에서 하나만 클릭합니다.
                    break

                if self.stop_event.is_set():
                    break

                if not found_any:
                    self.interruptible_sleep(LOOP_SLEEP_SECONDS)

        except Exception as exc:
            self.queue_status("오류 발생")
            self.queue_log(f"[치명적 오류] 예상하지 못한 문제가 발생했습니다: {exc}")
            # 디버깅용 전체 트레이스백은 로그 파일에만 남깁니다.
            self._log_to_file_only(traceback.format_exc())

        finally:
            if manager is not None:
                manager.stop_capture()
            self.stop_event.set()
            self.queue_status("종료됨")
            self.queue_log("[종료] 자동화 루프가 종료되었습니다.")
            # 종료 상태 전송 (단일 워커 풀)
            self._report_status(running=False, message="매크로 종료")
            self.ui_queue.put(("finished", ""))

    def _report_status(self, running: bool, message: str = "") -> None:
        """라이센스가 있으면 상태를 단일 워커 풀로 전송합니다(스레드 누적 방지)."""
        if not self.license_key:
            return
        try:
            self._status_executor.submit(
                _send_status, self.license_key, None, running, message
            )
        except Exception:
            pass

    def _log_to_file_only(self, text: str) -> None:
        """UI를 거치지 않고 로그 파일에만 기록합니다(트레이스백 등)."""
        if self._log_file is not None:
            try:
                self._log_file.write(text + "\n")
                self._log_file.flush()
            except Exception:
                pass

    def apply_click_jitter(
        self,
        target: TargetImage,
        center_x: int,
        center_y: int,
    ) -> tuple[int, int]:
        """매칭 영역 안에서 중심 좌표 주변의 작은 클릭 보정값을 적용합니다."""

        if target.image_gray is None:
            return center_x, center_y

        target_height, target_width = target.image_gray.shape[:2]

        # 중심에서 너무 멀어져 템플릿 영역 밖으로 나가지 않도록 작은 이미지에서는 범위를 줄입니다.
        max_x_jitter = min(CLICK_JITTER_PIXELS, max(0, (target_width - 1) // 2))
        max_y_jitter = min(CLICK_JITTER_PIXELS, max(0, (target_height - 1) // 2))

        jitter_x = random.randint(-max_x_jitter, max_x_jitter)
        jitter_y = random.randint(-max_y_jitter, max_y_jitter)

        return center_x + jitter_x, center_y + jitter_y

    def capture_screen_region(self, region: tuple[int, int, int, int]) -> Optional[np.ndarray]:
        """
        화면의 지정 영역만 캡처해서 GrayScale NumPy 배열로 반환합니다.

        이 모드는 대상 창이 실제 화면에 보이는 상태일 때 사용하는 현실적 타협 모드입니다.
        WGC와 달리 전체 화면 캡처에서 영역을 잘라오기 때문에 렌더링 호환성이 높지만,
        창이 다른 창에 가려지면 가려진 화면 그대로 캡처됩니다.
        """

        if winapi.pyautogui is None:
            self.queue_log("[오류] pyautogui를 불러올 수 없어 화면 영역 캡처를 사용할 수 없습니다.")
            self.queue_log(f"       원본 오류: {winapi.PYAUTOGUI_IMPORT_ERROR}")
            return None

        x, y, width, height = region
        try:
            screenshot = winapi.pyautogui.screenshot(region=(x, y, width, height))
            image_rgb = np.array(screenshot)
            if image_rgb.size == 0:
                self.queue_log("[캡처 오류] 화면 영역 캡처 결과가 비어 있습니다.")
                return None

            if image_rgb[0, 0].sum() == 0 and np.all(image_rgb[::16, ::16] == 0):
                self.queue_log("[캡처 오류] 화면 영역 캡처 결과가 완전히 검은색입니다.")
                return None

            return cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
        except Exception as exc:
            self.queue_log(f"[캡처 오류] 화면 영역 캡처 중 문제가 발생했습니다: {exc}")
            return None

    def dispatch_key_press(
        self,
        manager: Optional[InactiveManager],
        target: TargetImage,
    ) -> bool:
        """타겟 감지 시 vgamepad Xbox 컨트롤러 버튼 입력을 전송합니다.
        대상 창이 비활성이면 WM_ACTIVATE로 가짜 포커스를 보낸 뒤 입력합니다."""

        if not target.key:
            self.queue_log(f"[오류] {target.name}에 전송할 키가 설정되지 않았습니다.")
            return False
        if winapi.vg is None:
            self.queue_log("[오류] vgamepad를 불러올 수 없어 게임패드 입력을 보낼 수 없습니다.")
            self.queue_log(f"       원본 오류: {winapi.VGAMEPAD_IMPORT_ERROR}")
            return False

        normalized_key = target.key.strip().lower()
        is_trigger = normalized_key in input_gamepad.TRIGGER_KEYS
        button = None if is_trigger else input_gamepad.KEY_TO_GAMEPAD.get(normalized_key)
        if not is_trigger and button is None:
            self.queue_log(
                f"[키 경고] {target.name}의 key='{target.key}'는 "
                "vgamepad 매핑에 없어 건너뜁니다."
            )
            return False

        try:
            if manager is not None and manager.hwnd and winapi.win32gui is not None:
                WM_ACTIVATE = 0x0006
                WA_ACTIVE = 1
                winapi.win32gui.PostMessage(manager.hwnd, WM_ACTIVATE, WA_ACTIVE, 0)

            if is_trigger:
                send_gamepad_trigger(normalized_key)
            else:
                send_gamepad_button(button)
            self.queue_log(f"[키] {target.key.upper()}")
            return True
        except Exception as exc:
            self.queue_log(f"[오류] vgamepad 버튼 입력 중 문제가 발생했습니다: {exc}")
            return False

    def dispatch_win32_message(
        self,
        manager: Optional[InactiveManager],
        target: TargetImage,
    ) -> bool:
        """타겟 창에 Win32 컨트롤/명령 메시지를 전송합니다."""

        if manager is None:
            self.queue_log("[오류] Win32 메시지 액션에는 대상 창 HWND가 필요합니다.")
            return False

        return manager.send_win32_message_action(target)

    def dispatch_click(
        self,
        manager: Optional[InactiveManager],
        click_mode: str,
        capture_mode: str,
        region: tuple[int, int, int, int],
        x: int,
        y: int,
        target: Optional[TargetImage] = None,
    ) -> bool:
        """선택된 클릭 모드에 따라 클릭을 전송합니다."""

        vibrate = target is not None and target.vibrate_before_click

        if click_mode == "postmessage":
            if manager is None:
                self.queue_log("[오류] PostMessage 클릭에는 대상 창 HWND가 필요합니다.")
                return False
            start_x, start_y = manager.get_virtual_start_position(x, y)
            return manager.post_curved_click(start_x, start_y, x, y)

        if capture_mode == "region":
            screen_x = region[0] + x
            screen_y = region[1] + y
            return self.click_mouse_and_return(screen_x, screen_y, vibrate=vibrate)

        if manager is None:
            self.queue_log("[오류] 마우스 클릭 좌표 변환에 대상 창 정보가 없습니다.")
            return False

        screen_point = manager.client_to_screen(x, y)
        if screen_point is None:
            return False

        screen_x, screen_y = screen_point
        return self.click_mouse_and_return(screen_x, screen_y, vibrate=vibrate)

    def click_mouse_and_return(self, screen_x: int, screen_y: int, vibrate: bool = False) -> bool:
        """실제 마우스를 대상 위치로 이동해 클릭한 뒤 원래 위치로 되돌립니다."""

        if winapi.pyautogui is None:
            self.queue_log("[오류] pyautogui를 불러올 수 없어 마우스 클릭을 사용할 수 없습니다.")
            self.queue_log(f"       원본 오류: {winapi.PYAUTOGUI_IMPORT_ERROR}")
            return False

        original_x = original_y = None
        try:
            original_x, original_y = winapi.pyautogui.position()
            winapi.pyautogui.moveTo(screen_x, screen_y, duration=0.15)

            for dx in [3, -6, 6, -6, 3]:
                winapi.pyautogui.moveRel(dx, 0, duration=0.03)
            winapi.pyautogui.moveTo(screen_x, screen_y, duration=0.02)

            time.sleep(MOUSE_HOVER_BEFORE_CLICK_SECONDS)
            winapi.pyautogui.click()
            time.sleep(0.05)
            winapi.pyautogui.moveTo(original_x, original_y, duration=0.15)
            original_x = original_y = None  # 정상 복귀 완료 → finally 재이동 불필요
            self.queue_log(f"[클릭] ({screen_x},{screen_y})")
            return True
        except winapi.pyautogui.FailSafeException:
            self.queue_log("[긴급 중단] PyAutoGUI FAILSAFE가 감지되었습니다.")
            raise
        except Exception as exc:
            self.queue_log(f"[오류] 마우스 클릭 중 문제가 발생했습니다: {exc}")
            return False
        finally:
            # 예외로 중간에 멈췄으면 실제 커서를 원위치로 되돌립니다.
            if original_x is not None and original_y is not None:
                try:
                    winapi.pyautogui.moveTo(original_x, original_y, duration=0.15)
                except Exception:
                    pass

    def interruptible_sleep(self, seconds: float) -> bool:
        """
        긴 대기 중에도 종료 버튼/단축키가 반응할 수 있도록 0.1초 단위로 나눠 쉽니다.

        반환값:
        - True: 대기 중 stop_event가 설정됨
        - False: 지정된 시간만큼 정상 대기
        """

        end_time = time.monotonic() + seconds
        while time.monotonic() < end_time:
            if self.stop_event.is_set():
                return True
            remaining = end_time - time.monotonic()
            time.sleep(min(0.1, max(0.0, remaining)))
        return self.stop_event.is_set()

    def log(self, message: str) -> None:
        """UI 스레드에서 바로 로그를 남깁니다."""

        timestamp = time.strftime("%H:%M:%S")
        line = f"{timestamp} {message}\n"

        # 로그 파일에 기록 (flush는 _flush_log_periodic이 1초마다 묶어서 처리)
        if self._log_file is not None:
            try:
                self._log_file.write(line)
                self._log_dirty = True
            except Exception:
                pass

        if hasattr(self, "preview_log_rows"):
            self.preview_log_messages.append(line.rstrip())
            self.preview_log_messages = self.preview_log_messages[-12:]
            empty_count = 12 - len(self.preview_log_messages)
            visible_lines = [""] * empty_count + self.preview_log_messages
            for row, text in zip(self.preview_log_rows, visible_lines):
                row.configure(text=text)
            return

        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line)

        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 500:
            self.log_text.delete("1.0", "200.0")

        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)
        self.log_text.update_idletasks()

    def queue_log(self, message: str) -> None:
        """작업 스레드에서 로그 메시지를 UI 큐에 넣습니다."""

        self.ui_queue.put(("log", message))

    def set_status(self, status: str) -> None:
        """UI 스레드에서 상태 라벨을 갱신합니다."""

        self.status_var.set(status)

    def queue_status(self, status: str) -> None:
        """작업 스레드에서 상태 변경 메시지를 UI 큐에 넣습니다."""

        self.ui_queue.put(("status", status))

    def _poll_ui_queue(self) -> None:
        """root.after로 UI 큐를 주기적으로 확인하고 위젯을 갱신합니다."""

        processed = 0
        # 상한을 두되(이벤트 루프 기아 방지) 로그 폭주 시 따라잡도록 200으로.
        while processed < 200:
            try:
                kind, message = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "log":
                self.log(message)
            elif kind == "status":
                self.set_status(message)
            elif kind == "finished":
                self._set_button_state(running=False)
            processed += 1

        if not self.closing:
            self.root.after(50, self._poll_ui_queue)

    def _flush_log_periodic(self) -> None:
        """로그 파일을 1초마다 한 번씩 묶어서 flush합니다(매 줄 flush 제거)."""
        if self._log_file is not None and self._log_dirty:
            try:
                self._log_file.flush()
                self._log_dirty = False
            except Exception:
                pass
        if not self.closing:
            self.root.after(1000, self._flush_log_periodic)

    def _set_button_state(self, running: bool) -> None:
        """실행 상태에 따라 시작/종료 버튼 활성화를 조절합니다."""

        if self.ui_preview_only:
            self.start_button.configure(state=tk.NORMAL)
            self.stop_button.configure(state=tk.DISABLED)
            self.title_entry.configure(state=tk.NORMAL)
            return

        if running:
            self.start_button.configure(state=tk.DISABLED)
            self.stop_button.configure(state=tk.NORMAL)
            self.title_entry.configure(state=tk.DISABLED)
        else:
            self.start_button.configure(state=tk.NORMAL)
            self.stop_button.configure(state=tk.DISABLED)
            self.title_entry.configure(state=tk.NORMAL)


class LicenseDialog:
    """라이센스 키 입력/검증 다이얼로그입니다."""

    def __init__(self, root: tk.Tk, base_dir: Path):
        self.root = root
        self.base_dir = base_dir
        self.result: Optional[str] = None

        self.root.title("라이센스 인증")
        self.root.geometry("480x340+200+200")
        self.root.resizable(False, False)

        bg = "#1E1E1E"
        panel_bg = "#252526"
        input_bg = "#111827"
        text_color = "#F9FAFB"
        accent = "#7AB7FF"
        error_color = "#FF6B6B"
        success_color = "#69DB7C"

        self.root.configure(bg=bg)

        main_frame = tk.Frame(self.root, bg=bg, padx=24, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            main_frame,
            text="라이센스 인증",
            bg=bg,
            fg=accent,
            font=("Arial", 16, "bold"),
        ).pack(pady=(0, 16))

        tk.Label(
            main_frame,
            text="라이센스 키를 입력하세요",
            bg=bg,
            fg=text_color,
            font=("Arial", 10),
        ).pack(anchor=tk.W, pady=(0, 6))

        self.key_var = tk.StringVar()
        self.key_entry = tk.Entry(
            main_frame,
            textvariable=self.key_var,
            bg=input_bg,
            fg=text_color,
            insertbackground=text_color,
            font=("Consolas", 12),
            relief=tk.SOLID,
            bd=1,
            width=40,
        )
        self.key_entry.pack(fill=tk.X, pady=(0, 12))
        self.key_entry.bind("<Return>", lambda _e: self._activate())

        self.message_var = tk.StringVar()
        self.message_label = tk.Label(
            main_frame,
            textvariable=self.message_var,
            bg=bg,
            fg=text_color,
            font=("Arial", 9),
            wraplength=420,
            justify=tk.LEFT,
        )
        self.message_label.pack(fill=tk.X, pady=(0, 16))

        btn_frame = tk.Frame(main_frame, bg=bg)
        btn_frame.pack(fill=tk.X)

        self.activate_btn = tk.Button(
            btn_frame,
            text="인증하기",
            command=self._activate,
            bg=accent,
            fg="#000000",
            font=("Arial", 11, "bold"),
            relief=tk.FLAT,
            padx=20,
            pady=6,
            cursor="hand2",
        )
        self.activate_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 6))

        self.exit_btn = tk.Button(
            btn_frame,
            text="종료",
            command=self.root.destroy,
            bg="#3C3C3C",
            fg=text_color,
            font=("Arial", 11),
            relief=tk.FLAT,
            padx=20,
            pady=6,
            cursor="hand2",
        )
        self.exit_btn.pack(side=tk.RIGHT, padx=(6, 0))

        self.info_label = tk.Label(
            main_frame,
            text="",
            bg=bg,
            fg="#888888",
            font=("Arial", 8),
        )
        self.info_label.pack(side=tk.BOTTOM, pady=(12, 0))

        self._error_color = error_color
        self._success_color = success_color
        self._bg = bg

        saved_key = load_saved_license(self.base_dir)
        if saved_key:
            self.key_var.set(saved_key)
            self._try_auto_activate(saved_key)
        else:
            self.key_entry.focus_set()

    def _try_auto_activate(self, key: str) -> None:
        hwid = get_hwid()
        server_result = verify_license_server(key, hwid)

        if server_result.get("_offline"):
            self.message_var.set("서버에 연결할 수 없습니다. 인터넷 연결을 확인하세요.")
            self.message_label.configure(fg=self._error_color)
            self.key_entry.focus_set()
            return

        if not server_result.get("valid", False):
            self.message_var.set(server_result.get("message", "서버 인증 실패"))
            self.message_label.configure(fg=self._error_color)
            self.key_var.set("")
            self.key_entry.focus_set()
            return

        msg = server_result.get("message", "유효한 라이센스입니다.")
        self.message_var.set(f"저장된 라이센스가 유효합니다. {msg}")
        self.message_label.configure(fg=self._success_color)
        self.root.after(800, lambda: self._launch_app(key))

    def _activate(self) -> None:
        key = self.key_var.get().strip()
        if not key:
            self.message_var.set("라이센스 키를 입력하세요.")
            self.message_label.configure(fg=self._error_color)
            return

        hwid = get_hwid()
        server_result = verify_license_server(key, hwid)

        if server_result.get("_offline"):
            self.message_var.set("서버에 연결할 수 없습니다. 인터넷 연결을 확인하세요.")
            self.message_label.configure(fg=self._error_color)
            return

        if not server_result.get("valid", False):
            self.message_var.set(server_result.get("message", "서버 인증 실패"))
            self.message_label.configure(fg=self._error_color)
            return

        msg = server_result.get("message", "유효한 라이센스입니다.")
        self.message_var.set(f"인증 성공! {msg}")
        self.message_label.configure(fg=self._success_color)
        save_license_key(self.base_dir, key)
        self.root.after(600, lambda: self._launch_app(key))

    def _launch_app(self, key: str) -> None:
        self.result = key
        for widget in self.root.winfo_children():
            widget.destroy()
        AutomationApp(self.root, license_key=key)
