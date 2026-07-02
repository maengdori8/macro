from __future__ import annotations
import io
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

try:
    from PIL import Image as PILImage, ImageTk as PILImageTk
except Exception:  # noqa: BLE001 - 썸네일 표시는 선택 기능이라 없으면 건너뜁니다.
    PILImage = None
    PILImageTk = None

from macroapp import winapi
from macroapp import input_gamepad
from macroapp import input_message
from macroapp.input_gamepad import _get_gamepad, send_gamepad_button, send_gamepad_trigger
from macroapp.paths import APP_VERSION, app_dir
from macroapp.logging_util import LogCallback
from macroapp.config import (
    TargetImage, WINDOW_TITLE, LOOP_SLEEP_SECONDS, WINDOW_RETRY_SECONDS,
    CLICK_JITTER_PIXELS, MOUSE_HOVER_BEFORE_CLICK_SECONDS,
    DEFAULT_REGION_X, DEFAULT_REGION_Y, DEFAULT_REGION_WIDTH, DEFAULT_REGION_HEIGHT,
    CUSTOM_TARGETS_DIR_NAME,
    RANK_OCR_ENABLED, RANK_OCR_INTERVAL_SECONDS,
    MATCH_GATE_ENABLED, MATCH_GATE_LEFT_FRACTION, MATCH_GATE_RIGHT_FRACTION,
    MATCH_GATE_TOP_FRACTION, MATCH_GATE_BOTTOM_FRACTION, MATCH_GATE_TOKENS,
    SKIP_ENABLED, SKIP_OCR_INTERVAL_SECONDS, SKIP_PRESS_DELAY_SECONDS, SKIP_A_CLICK,
    SKIP_A_BUTTON, SKIP_A_SWEEP_BUTTONS, SKIP_A_TAP_SECONDS,
    SKIP_A_SENDINPUT_AFTER_SECONDS,
    SKIP_OCR_MAX_WIDTH, SKIP_OCR_LEFT_FRACTION, SKIP_OCR_RIGHT_FRACTION,
    SKIP_OCR_TOP_FRACTION, SKIP_OCR_BOTTOM_FRACTION,
    SKIP_TEXT_CONSENSUS, SKIP_FALLBACK_BOTH_SECONDS, STOP_CONFIRM_COUNT,
    load_targets, load_target_definitions,
    read_target_image_bytes, has_custom_target_image,
    save_custom_target_image, delete_custom_target_image,
)
from macroapp import ocr as rank_ocr
from macroapp.region_select import RegionSelector
from macroapp.license_client import (
    STATUS_REPORT_INTERVAL_SECONDS, _send_status, get_hwid, verify_license_server,
    format_remaining_time, load_saved_license, save_license_key,
)
from macroapp.matching import find_template_center, downscale_screen
from macroapp.window import InactiveManager

class AutomationApp:
    """tkinter UI와 자동화 스레드를 관리합니다."""

    # 다크 + 레드 테마 팔레트 (UI 전반에서 공유)
    COLORS = {
        "bg": "#101010",
        "panel": "#1A1A1A",
        "border": "#2B2B2B",
        "input": "#262626",
        "text": "#F3F3F3",
        "muted": "#9C9C9C",
        "accent": "#D93A2B",
        "accent_active": "#B72D20",
        "accent_soft": "#FF6A55",
        "disabled": "#3A3A3A",
        "ok": "#69DB7C",
    }

    def __init__(self, root: tk.Tk, license_key: Optional[str] = None):
        self.root = root
        self.license_key = license_key
        self.license_info: Optional[dict] = None

        self.root.title("mAuto")
        # LicenseDialog가 고정 크기로 만든 root를 재사용하므로 리사이즈를 다시 허용합니다.
        self.root.resizable(True, True)
        # 1366x768 같은 작은 화면에서도 하단 시작/정지 버튼이 잘리지 않게 높이를 화면에 맞춥니다.
        screen_height = self.root.winfo_screenheight()
        window_height = min(780, max(560, screen_height - 110))
        offset_y = max(0, min(60, screen_height - window_height - 90))
        self.root.geometry(f"1180x{window_height}+80+{offset_y}")
        self.root.minsize(1080, 560)
        self.window_title_var = tk.StringVar(value=WINDOW_TITLE)
        initial_status = "대기 중"
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
        # 목표 도달 시 자동 정지: "off"=안 멈춤 / "rank"=등수 / "score"=점수 (둘 중 하나)
        self.stop_mode_var = tk.StringVar(value="off")
        self.stop_value_var = tk.StringVar(value="")
        # 등수/점수/티어 OCR 박스(프레임 비율 %). 클러스터만 정확히 가둬 노이즈 제거.
        # 기본값 = 실제 매칭 화면 기준으로 등수·점수·티어 기둥만(좌33~우50, 상47~하64).
        # 왼쪽 3D 선수 모델·배경·상대(우측)를 제외 → 인식 정확도↑. 필요하면 UI에서 조절.
        self.ocr_box_vars = {
            "left": tk.StringVar(value="33"),
            "top": tk.StringVar(value="47"),
            "right": tk.StringVar(value="50"),
            "bottom": tk.StringVar(value="64"),
        }
        self.region_vars = {
            "x": tk.IntVar(value=DEFAULT_REGION_X),
            "y": tk.IntVar(value=DEFAULT_REGION_Y),
            "width": tk.IntVar(value=DEFAULT_REGION_WIDTH),
            "height": tk.IntVar(value=DEFAULT_REGION_HEIGHT),
        }

        # 타겟별 템플릿 캡처 UI 상태 (썸네일 PhotoImage는 GC 방지를 위해 보관)
        self.clock_var = tk.StringVar(value="--:--:--")
        self._thumb_refs: dict[str, Any] = {}
        self._thumb_labels: dict[str, tk.Label] = {}
        self._target_source_vars: dict[str, tk.StringVar] = {}
        self._target_source_labels: dict[str, tk.Label] = {}
        self._capture_buttons: dict[str, tk.Button] = {}
        self._reset_buttons: dict[str, tk.Button] = {}
        self._capturing_template = False

        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.ui_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.closing = False
        # 상태 전송 전용 단일 워커 풀 (스레드 누적 방지)
        self._status_executor = ThreadPoolExecutor(max_workers=1)
        self._log_dirty = False
        self._close_deadline = 0.0
        # (등수, 상태메시지)를 한 튜플로 묶어 스레드 간 원자적으로 교체/읽기.
        self._rank_state = (None, "실행 중")
        # 매칭 루프 → OCR 워커로 공개하는 최신 프레임. (시퀀스번호, gray) 튜플을 원자적으로 교체.
        # 시퀀스번호로 '아직 OCR 안 한 새 프레임'만 골라 같은 프레임 중복 OCR을 막는다(신선도 게이팅).
        self._latest_frame = None
        self._frame_seq = 0
        self._rank_consensus = rank_ocr.RankConsensus()  # 다중 프레임 투표(단발 오인식 폐기)
        self._ocr_manager = None        # OCR 워커가 SKIP 입력에 쓰는 매니저
        self._ocr_thread = None         # OCR 전용 스레드
        self._skip_active_until = 0.0   # 이 시각 전까지 매칭 일시정지(SKIP 처리 중)
        self._skip_seen_since = None    # 현재 스킵이 처음 감지된 시각(안전망 타이머용)
        self._skip_text_streak = 0      # 일반 스킵(OCR) 연속 감지 횟수(단발 노이즈 차단)
        self._skip_kind = None          # 현재 스킵 에피소드 종류: None|"a"|"start" (A형은 sticky)
        # (A) SKIP 자동 버튼 탐색·학습 상태 — 게임이 가상 A를 무시해서, 어떤 입력이
        # 실제로 스킵을 넘기는지 후보를 하나씩 눌러보고 성공한 것을 학습한다.
        self._skip_a_learned = None     # 학습된(또는 SKIP_A_BUTTON 지정) 입력 이름
        self._skip_a_sweep_idx = 0      # 다음에 시도할 후보 인덱스
        self._skip_last_press = None    # (입력이름, 시각) — 에피소드 종료 시 학습 판정용
        self._skip_learned_fail = 0     # 학습된 입력이 연속 무효면 학습 취소용
        self._skip_a_dumped = False     # (A) SKIP 첫 감지 시 창 클래스 1회 진단 덤프
        self._gate_fail_log_at = 0.0    # 게이트 미검출 로그 스로틀(과다 로그 방지)
        # 목표 도달 자동 정지 — 시작 시 UI에서 스냅샷한 평문 값(OCR 워커가 스레드 안전하게 읽음)
        self._stop_mode = "off"         # off | rank | score
        self._stop_value = 0            # 목표 등수/점수
        self._stop_triggered = False    # 중복 트리거/로그 방지
        self._stop_confirm_count = 0    # 정지조건 연속 확정 횟수(STOP_CONFIRM_COUNT 도달 시 정지)
        # 마지막 값 유지: 등수 없는 프레임(로비)이 매칭 때 읽은 등수를 덮어쓰지 않게 한다.
        # 챔/슈챔이면 등수가 무조건 있으므로, 한 번 읽은 등수는 다음 매칭 전까지 유지·표시.
        self._last_rank = None
        self._last_tier = None
        self._last_score = None
        # OCR 박스(프레임 비율 l,t,r,b) — 시작 시 UI에서 스냅샷(스레드 안전)
        self._ocr_box = (0.35, 0.50, 0.48, 0.64)

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
        self._tick_clock()

        # 가상 게임패드를 미리 생성해 게임이 컨트롤러를 일찍 인식하게 합니다.
        if winapi.vg is not None:
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

    def _bring_window_to_front(self) -> None:
        """창이 뒤에 숨어 보이지 않는 일을 줄이기 위해 잠깐 최상위로 올립니다."""

        self.root.update_idletasks()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

        # macOS에서 처음 뜬 tkinter 창이 다른 앱 뒤로 가는 경우가 있어 잠깐만 topmost를 켭니다.
        self.root.attributes("-topmost", True)
        self.root.after(1200, lambda: self.root.attributes("-topmost", False))

    def _font(self, size: int, bold: bool = False) -> tuple:
        """플랫폼에 맞는 한글 UI 폰트를 반환합니다."""

        family = "Malgun Gothic" if platform.system() == "Windows" else "Arial"
        return (family, size, "bold") if bold else (family, size)

    def _panel(self, parent: tk.Misc, title: Optional[str] = None) -> tk.Frame:
        """테두리가 있는 어두운 패널 프레임을 만듭니다."""

        c = self.COLORS
        frame = tk.Frame(
            parent,
            bg=c["panel"],
            padx=12,
            pady=10,
            highlightbackground=c["border"],
            highlightthickness=1,
        )
        if title:
            tk.Label(
                frame,
                text=title,
                bg=c["panel"],
                fg=c["text"],
                font=self._font(11, bold=True),
            ).pack(pady=(0, 8))
        return frame

    def _accent_button(
        self,
        parent: tk.Misc,
        text: str,
        command,
        small: bool = False,
    ) -> tk.Button:
        """스크린샷 느낌의 빨간 강조 버튼을 만듭니다."""

        c = self.COLORS
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=c["accent"],
            fg="#FFFFFF",
            activebackground=c["accent_active"],
            activeforeground="#FFFFFF",
            disabledforeground="#8A8A8A",
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=self._font(9 if small else 11, bold=not small),
            padx=10 if small else 18,
            pady=1 if small else 8,
        )

    def _build_ui(self) -> None:
        """다크 + 레드 테마의 2단 레이아웃 UI를 만듭니다."""

        c = self.COLORS
        self.root.configure(bg=c["bg"])

        main_frame = tk.Frame(self.root, bg=c["bg"], padx=14, pady=14)
        main_frame.pack(fill=tk.BOTH, expand=True)

        left_column = tk.Frame(main_frame, bg=c["bg"])
        left_column.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right_column = tk.Frame(main_frame, bg=c["bg"], width=400)
        right_column.pack(side=tk.LEFT, fill=tk.Y, padx=(14, 0))
        right_column.pack_propagate(False)

        # ── 왼쪽 아래: 기본 설정 ──
        # 먼저 pack해 두면 창 높이가 부족할 때 타겟 목록 쪽이 먼저 줄어듭니다.
        settings_panel = self._panel(left_column, "기본 설정")
        settings_panel.pack(side=tk.BOTTOM, fill=tk.X, pady=(14, 0))

        # ── 왼쪽: 타겟 설정 (target_A~H, 템플릿 캡처/임계값) ──
        targets_panel = self._panel(left_column, "타겟 설정")
        targets_panel.pack(fill=tk.BOTH, expand=True)

        for name in self.target_names:
            row = tk.Frame(targets_panel, bg=c["panel"])
            row.pack(fill=tk.X, pady=3)

            tk.Label(
                row,
                text=name,
                bg=c["panel"],
                fg=c["text"],
                width=9,
                anchor=tk.W,
                font=self._font(10, bold=True),
            ).pack(side=tk.LEFT)

            thumb_holder = tk.Frame(
                row,
                bg=c["input"],
                width=66,
                height=28,
                highlightbackground=c["border"],
                highlightthickness=1,
            )
            thumb_holder.pack_propagate(False)
            thumb_holder.pack(side=tk.LEFT, padx=(0, 10))
            thumb_label = tk.Label(thumb_holder, bg=c["input"], fg=c["muted"], font=self._font(8))
            thumb_label.pack(fill=tk.BOTH, expand=True)
            self._thumb_labels[name] = thumb_label

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
                bg=c["panel"],
                fg=c["text"],
                troughcolor=c["input"],
                activebackground=c["accent_soft"],
                highlightthickness=0,
                bd=0,
                showvalue=False,
                length=140,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

            tk.Label(
                row,
                textvariable=self.threshold_label_vars[name],
                bg=c["panel"],
                fg=c["accent_soft"],
                width=5,
                font=self._font(10, bold=True),
            ).pack(side=tk.LEFT, padx=(6, 6))

            source_var = tk.StringVar(value="기본")
            source_label = tk.Label(
                row,
                textvariable=source_var,
                bg=c["panel"],
                fg=c["muted"],
                width=5,
                font=self._font(9),
            )
            source_label.pack(side=tk.LEFT)
            self._target_source_vars[name] = source_var
            self._target_source_labels[name] = source_label

            capture_button = self._accent_button(
                row,
                "캡처",
                lambda target_name=name: self.capture_target_template(target_name),
                small=True,
            )
            capture_button.pack(side=tk.LEFT, padx=(8, 4))
            self._capture_buttons[name] = capture_button

            reset_button = tk.Button(
                row,
                text="기본값",
                command=lambda target_name=name: self.reset_target_template(target_name),
                bg=c["input"],
                fg=c["text"],
                activebackground=c["border"],
                activeforeground=c["text"],
                disabledforeground="#6A6A6A",
                relief=tk.FLAT,
                bd=0,
                cursor="hand2",
                font=self._font(9),
                padx=8,
                pady=1,
            )
            reset_button.pack(side=tk.LEFT)
            self._reset_buttons[name] = reset_button

        tk.Label(
            targets_panel,
            text="캡처: 화면에서 드래그한 영역으로 템플릿 교체 · 기본값: 빌드 내장 이미지로 복원",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(8),
        ).pack(pady=(8, 0))

        capture_row = tk.Frame(settings_panel, bg=c["panel"])
        capture_row.pack(fill=tk.X, pady=2)
        tk.Label(
            capture_row,
            text="캡처",
            bg=c["panel"],
            fg=c["text"],
            width=10,
            anchor=tk.W,
            font=self._font(10),
        ).pack(side=tk.LEFT)
        for text, value in (("비활성 WGC", "wgc"), ("화면 영역 캡처", "region")):
            tk.Radiobutton(
                capture_row,
                text=text,
                variable=self.capture_mode_var,
                value=value,
                command=self.on_capture_mode_changed,
                bg=c["panel"],
                fg=c["text"],
                selectcolor=c["input"],
                activebackground=c["panel"],
                activeforeground=c["text"],
                font=self._font(9),
            ).pack(side=tk.LEFT, padx=(0, 12))

        click_row = tk.Frame(settings_panel, bg=c["panel"])
        click_row.pack(fill=tk.X, pady=2)
        tk.Label(
            click_row,
            text="클릭",
            bg=c["panel"],
            fg=c["text"],
            width=10,
            anchor=tk.W,
            font=self._font(10),
        ).pack(side=tk.LEFT)
        for text, value in (("PostMessage", "postmessage"), ("마우스 이동 후 복귀", "mouse")):
            tk.Radiobutton(
                click_row,
                text=text,
                variable=self.click_mode_var,
                value=value,
                bg=c["panel"],
                fg=c["text"],
                selectcolor=c["input"],
                activebackground=c["panel"],
                activeforeground=c["text"],
                font=self._font(9),
            ).pack(side=tk.LEFT, padx=(0, 12))

        region_row = tk.Frame(settings_panel, bg=c["panel"])
        region_row.pack(fill=tk.X, pady=(6, 0))
        tk.Label(
            region_row,
            text="영역",
            bg=c["panel"],
            fg=c["text"],
            width=10,
            anchor=tk.W,
            font=self._font(10),
        ).pack(side=tk.LEFT)
        for key, label in (
            ("x", "X"),
            ("y", "Y"),
            ("width", "W"),
            ("height", "H"),
        ):
            tk.Label(
                region_row,
                text=label,
                bg=c["panel"],
                fg=c["muted"],
                font=self._font(9),
            ).pack(side=tk.LEFT)
            tk.Entry(
                region_row,
                textvariable=self.region_vars[key],
                width=7,
                bg=c["input"],
                fg=c["text"],
                insertbackground=c["text"],
                relief=tk.FLAT,
                highlightbackground=c["border"],
                highlightcolor=c["accent"],
                highlightthickness=1,
            ).pack(side=tk.LEFT, padx=(4, 10), ipady=2)

        # ── 자동 정지: 목표 등수/점수 도달 시 멈춤(둘 중 하나) 또는 안 멈춤 ──
        stop_row = tk.Frame(settings_panel, bg=c["panel"])
        stop_row.pack(fill=tk.X, pady=(6, 0))
        tk.Label(
            stop_row,
            text="자동 정지",
            bg=c["panel"],
            fg=c["text"],
            width=10,
            anchor=tk.W,
            font=self._font(10),
        ).pack(side=tk.LEFT)
        for text, value in (("안 함", "off"), ("등수 이내", "rank"), ("점수 이상", "score")):
            tk.Radiobutton(
                stop_row,
                text=text,
                variable=self.stop_mode_var,
                value=value,
                bg=c["panel"],
                fg=c["text"],
                selectcolor=c["input"],
                activebackground=c["panel"],
                activeforeground=c["text"],
                font=self._font(9),
            ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(
            stop_row,
            text="목표",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(9),
        ).pack(side=tk.LEFT, padx=(4, 2))
        tk.Entry(
            stop_row,
            textvariable=self.stop_value_var,
            width=8,
            bg=c["input"],
            fg=c["text"],
            insertbackground=c["text"],
            relief=tk.FLAT,
            highlightbackground=c["border"],
            highlightcolor=c["accent"],
            highlightthickness=1,
        ).pack(side=tk.LEFT, padx=(0, 4), ipady=2)

        # ── 등수/점수/티어 OCR 박스(프레임 비율 %) — 그 영역만 정확히 읽어 노이즈 제거 ──
        ocr_row = tk.Frame(settings_panel, bg=c["panel"])
        ocr_row.pack(fill=tk.X, pady=(6, 0))
        tk.Label(
            ocr_row,
            text="등수영역%",
            bg=c["panel"],
            fg=c["text"],
            width=10,
            anchor=tk.W,
            font=self._font(10),
        ).pack(side=tk.LEFT)
        for key, label in (("left", "좌"), ("top", "상"), ("right", "우"), ("bottom", "하")):
            tk.Label(
                ocr_row,
                text=label,
                bg=c["panel"],
                fg=c["muted"],
                font=self._font(9),
            ).pack(side=tk.LEFT)
            tk.Entry(
                ocr_row,
                textvariable=self.ocr_box_vars[key],
                width=5,
                bg=c["input"],
                fg=c["text"],
                insertbackground=c["text"],
                relief=tk.FLAT,
                highlightbackground=c["border"],
                highlightcolor=c["accent"],
                highlightthickness=1,
            ).pack(side=tk.LEFT, padx=(2, 8), ipady=2)

        # ── 오른쪽: 버전 / 시계 / 상태 / 로그 / 시작·정지 ──
        version_panel = self._panel(right_column)
        version_panel.pack(fill=tk.X)
        version_text = f"Version : {APP_VERSION}"
        if self.license_key:
            version_text = f"Version : {APP_VERSION}  👑"
        tk.Label(
            version_panel,
            text=version_text,
            bg=c["panel"],
            fg=c["accent_soft"],
            font=self._font(12, bold=True),
        ).pack()

        clock_panel = self._panel(right_column)
        clock_panel.pack(fill=tk.X, pady=(12, 0))
        tk.Label(
            clock_panel,
            textvariable=self.clock_var,
            bg=c["panel"],
            fg=c["text"],
            font=("Consolas", 26, "bold"),
        ).pack()

        status_panel = self._panel(right_column)
        status_panel.pack(fill=tk.X, pady=(12, 0))
        status_row = tk.Frame(status_panel, bg=c["panel"])
        status_row.pack()
        tk.Label(
            status_row,
            text="상태 : ",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(11),
        ).pack(side=tk.LEFT)
        self.status_label = tk.Label(
            status_row,
            textvariable=self.status_var,
            bg=c["panel"],
            fg=c["accent_soft"],
            font=self._font(11, bold=True),
        )
        self.status_label.pack(side=tk.LEFT)

        # 시작/정지 패널을 로그보다 먼저 pack해, 높이가 부족하면 로그가 먼저 줄어듭니다.
        control_panel = self._panel(right_column, "시작/정지 & ETC")
        control_panel.pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))
        button_row = tk.Frame(control_panel, bg=c["panel"])
        button_row.pack(fill=tk.X)
        self.start_button = self._accent_button(button_row, "시작 (F8)", self.start_automation)
        self.start_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.stop_button = self._accent_button(button_row, "정지 (F9/ESC)", self.stop_automation)
        self.stop_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        log_panel = self._panel(right_column, "로그")
        log_panel.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        self.log_text = scrolledtext.ScrolledText(
            log_panel,
            height=10,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=c["input"],
            fg=c["text"],
            insertbackground=c["text"],
            relief=tk.FLAT,
            bd=0,
            font=("Consolas", 9),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self._refresh_all_target_rows()

    def _tick_clock(self) -> None:
        """오른쪽 패널의 시계를 0.5초마다 갱신합니다."""

        self.clock_var.set(time.strftime("%H:%M:%S"))
        if not self.closing:
            self.root.after(500, self._tick_clock)

    # ── 타겟 템플릿 캡처 ──

    def _find_target_definition(self, name: str) -> Optional[TargetImage]:
        for target in self.target_definitions:
            if target.name == name:
                return target
        return None

    def capture_target_template(self, name: str) -> None:
        """화면 드래그 캡처로 타겟 템플릿을 교체합니다."""

        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.log("[캡처] 자동화 실행 중에는 템플릿을 변경할 수 없습니다. 정지 후 다시 시도하세요.")
            return
        if self._capturing_template:
            return
        target = self._find_target_definition(name)
        if target is None:
            self.log(f"[캡처 오류] {name} 타겟 정보를 찾을 수 없습니다.")
            return

        self._capturing_template = True
        self.log(f"[캡처] {name}: 화면에서 영역을 드래그하세요. (ESC: 취소, 주 모니터만 지원)")
        self.root.withdraw()
        # 창이 화면에서 완전히 사라진 뒤 스크린샷을 찍도록 잠시 기다립니다.
        self.root.after(300, lambda: self._run_template_capture(target))

    def _run_template_capture(self, target: TargetImage) -> None:
        """창을 숨긴 상태에서 영역 선택 오버레이를 띄우고 결과를 저장합니다."""

        image = None
        try:
            selector = RegionSelector(self.root, logger=self.log)
            image = selector.select()
        except Exception as exc:
            # 콘솔 없는 빌드에서는 예외가 조용히 사라지므로 UI 로그로 남깁니다.
            if not self.closing:
                self.log(f"[캡처 오류] 영역 선택 중 문제가 발생했습니다: {exc}")
        finally:
            self._capturing_template = False
            try:
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
            except tk.TclError:
                pass  # 캡처 도중 창이 닫힌 경우 복원할 UI가 없습니다.

        if self.closing:
            return

        if image is None:
            self.log(f"[캡처] {target.name} 캡처를 취소했습니다.")
            return

        if image.width < 12 or image.height < 12:
            self.log(
                f"[캡처 오류] 선택 영역({image.width}x{image.height})이 너무 작습니다. "
                "가로/세로 12px 이상으로 선택하세요."
            )
            return

        try:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            path = save_custom_target_image(self.base_dir, target.filename, buffer.getvalue())
        except Exception as exc:
            self.log(f"[캡처 오류] 템플릿 저장에 실패했습니다: {exc}")
            return

        self.log(
            f"[캡처] {target.name} 템플릿을 교체했습니다 "
            f"({image.width}x{image.height}) → {CUSTOM_TARGETS_DIR_NAME}/{path.name}"
        )
        self.log("       다음 '시작'부터 새 템플릿이 적용됩니다.")

        # 캡처 프레임보다 큰 템플릿은 절대 매칭되지 않으므로 미리 경고합니다.
        if self.capture_mode_var.get() == "region":
            region = self.get_region_from_ui()
            if region is not None and (image.width > region[2] or image.height > region[3]):
                self.log(
                    f"[경고] 선택 영역({image.width}x{image.height})이 "
                    f"현재 캡처 영역({region[2]}x{region[3]})보다 큽니다."
                )
                self.log("       이대로는 매칭되지 않으니 더 작게 캡처하거나 영역을 키우세요.")

        self._refresh_target_row(target.name)

    def reset_target_template(self, name: str) -> None:
        """커스텀 캡처를 삭제하고 빌드에 포함된 기본 템플릿으로 되돌립니다."""

        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.log("[캡처] 자동화 실행 중에는 템플릿을 변경할 수 없습니다. 정지 후 다시 시도하세요.")
            return
        target = self._find_target_definition(name)
        if target is None:
            return

        try:
            removed = delete_custom_target_image(self.base_dir, target.filename)
        except Exception as exc:
            self.log(f"[캡처 오류] 커스텀 템플릿 삭제에 실패했습니다: {exc}")
            return

        if removed:
            self.log(f"[캡처] {name} 템플릿을 기본 이미지로 되돌렸습니다.")
        else:
            self.log(f"[캡처] {name}은(는) 이미 기본 이미지를 사용하고 있습니다.")
        self._refresh_target_row(name)

    def _refresh_all_target_rows(self) -> None:
        for name in self.target_names:
            self._refresh_target_row(name)

    def _refresh_target_row(self, name: str) -> None:
        """타겟 행의 템플릿 출처(기본/커스텀) 표시와 썸네일을 갱신합니다."""

        target = self._find_target_definition(name)
        if target is None:
            return

        c = self.COLORS
        is_custom = has_custom_target_image(self.base_dir, target.filename)
        source_var = self._target_source_vars.get(name)
        source_label = self._target_source_labels.get(name)
        if source_var is not None and source_label is not None:
            source_var.set("커스텀" if is_custom else "기본")
            source_label.configure(fg=c["ok"] if is_custom else c["muted"])
        self._update_thumbnail(name, target.filename)

    def _update_thumbnail(self, name: str, filename: str) -> None:
        """타겟 행의 작은 템플릿 미리보기를 갱신합니다.

        판매본 보호(자산 임베드)의 취지를 지키기 위해 내장 기본 템플릿의
        픽셀은 화면에 노출하지 않고 크기 정보만 표시합니다.
        사용자가 직접 캡처한 커스텀 템플릿만 실제 이미지로 보여줍니다.
        """

        label = self._thumb_labels.get(name)
        if label is None:
            return
        if PILImage is None or PILImageTk is None:
            label.configure(text="-", image="")
            return

        raw = read_target_image_bytes(self.base_dir, filename)
        if not raw:
            self._thumb_refs.pop(name, None)
            label.configure(text="없음", image="")
            return

        try:
            image = PILImage.open(io.BytesIO(raw))
            if not has_custom_target_image(self.base_dir, filename):
                self._thumb_refs.pop(name, None)
                label.configure(text=f"{image.width}x{image.height}", image="")
                return
            image.thumbnail((62, 24))
            photo = PILImageTk.PhotoImage(image, master=self.root)
        except Exception:
            self._thumb_refs.pop(name, None)
            label.configure(text="오류", image="")
            return

        self._thumb_refs[name] = photo
        label.configure(image=photo, text="")

    def _bind_shortcuts(self) -> None:
        """UI가 활성화되어 있을 때 동작하는 단축키를 등록합니다."""

        self.root.bind("<F8>", lambda _event: self.start_automation())
        self.root.bind("<F9>", lambda _event: self.stop_automation())
        self.root.bind("<Escape>", lambda _event: self.stop_automation())

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

        if self.license_key:
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

        # 대상 창은 프로세스명(fczf 등)으로 찾으므로 제목 입력이 없어도 된다(빈 문자열 허용).
        # find_window가 프로세스 우선 매칭하고, 제목은 비어 있으면 자동으로 건너뛴다.
        window_title = self.window_title_var.get().strip()

        # 목표 도달 자동 정지 조건 + 등수 OCR 박스를 시작 시점에 스냅샷(메인 스레드 → 안전).
        self._snapshot_stop_condition()
        self._snapshot_ocr_box()

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
        """종료 버튼, F9, ESC로 자동화를 안전하게 중단 요청합니다."""

        if self.worker_thread is None or not self.worker_thread.is_alive():
            self.log("[안내] 현재 실행 중인 자동화가 없습니다.")
            self.set_status("대기 중")
            self._set_button_state(running=False)
            return

        self.stop_event.set()
        self.set_status("종료 요청됨")
        self.log("[중지 요청] 자동화 루프를 안전하게 중단합니다.")
        self.stop_button.configure(state=tk.DISABLED)

    def _snapshot_stop_condition(self) -> None:
        """시작 시 UI의 자동 정지 조건을 평문 속성으로 고정합니다(메인 스레드 전용).

        OCR 워커 스레드가 tkinter 변수를 직접 읽는 것을 피하려고, 시작 순간의 값을
        평문(_stop_mode/_stop_value)으로 스냅샷합니다. 실행 중 바꾸려면 재시작하세요.
        값이 비었거나 잘못되면 '안 함'으로 처리하고 사용자에게 안내합니다.
        """
        self._stop_triggered = False
        mode = self.stop_mode_var.get()
        if mode not in ("rank", "score"):
            self._stop_mode = "off"
            self._stop_value = 0
            return
        raw = (self.stop_value_var.get() or "").strip().replace(",", "")
        try:
            value = int(raw)
            if value <= 0:
                raise ValueError
        except (ValueError, TypeError):
            self._stop_mode = "off"
            self._stop_value = 0
            self.log("[자동 정지] 목표 값이 올바르지 않아 '안 함'으로 실행합니다(숫자를 입력하세요).")
            return
        self._stop_mode = mode
        self._stop_value = value
        if mode == "rank":
            self.log(f"[자동 정지] 현재 등수가 {value}위 이내가 되면 자동으로 정지합니다.")
        elif mode == "score":
            self.log(f"[자동 정지] 현재 점수가 {value}점 이상이 되면 자동으로 정지합니다.")

    def _snapshot_ocr_box(self) -> None:
        """시작 시 UI의 등수 OCR 박스(%)를 평문 비율로 고정합니다(메인 스레드 전용).

        좌<우, 상<하가 아니거나 값이 잘못되면 기본 박스로 폴백하고 안내합니다.
        OCR 워커가 tkinter 변수를 직접 읽지 않도록 평문 튜플(_ocr_box)로 스냅샷합니다.
        """
        default = (0.35, 0.50, 0.48, 0.64)
        try:
            l = float(self.ocr_box_vars["left"].get()) / 100.0
            t = float(self.ocr_box_vars["top"].get()) / 100.0
            r = float(self.ocr_box_vars["right"].get()) / 100.0
            b = float(self.ocr_box_vars["bottom"].get()) / 100.0
        except (ValueError, TypeError):
            self._ocr_box = default
            self.log("[등수영역] 값이 올바르지 않아 기본 영역으로 실행합니다(0~100 숫자).")
            return
        # 범위/순서 검증: 0~1로 클램프하고 좌<우, 상<하를 보장.
        l = min(max(l, 0.0), 1.0); r = min(max(r, 0.0), 1.0)
        t = min(max(t, 0.0), 1.0); b = min(max(b, 0.0), 1.0)
        if r - l < 0.02 or b - t < 0.02:
            self._ocr_box = default
            self.log("[등수영역] 폭/높이가 너무 작아 기본 영역으로 실행합니다(좌<우, 상<하).")
            return
        self._ocr_box = (l, t, r, b)
        self.log(
            f"[등수영역] OCR 박스: 좌{l*100:.0f}~우{r*100:.0f}%, 상{t*100:.0f}~하{b*100:.0f}%"
        )

    def _maybe_stop_on_target(
        self, rank: Optional[int], score: Optional[int], tier: Optional[str] = None
    ) -> None:
        """컨센서스로 확정된 등수/점수/티어가 목표에 도달하면 자동 정지합니다(OCR 워커 스레드).

        tkinter를 직접 만지지 않고 stop_event + 큐 메시지만 사용 → 스레드 안전.
        매칭 루프가 stop_event를 보고 안전하게 종료하며 UI도 정상 복귀합니다.
        """
        if self._stop_triggered or self._stop_mode == "off":
            return
        hit_msg = None
        if self._stop_mode == "rank" and rank is not None and rank <= self._stop_value:
            hit_msg = f"목표 등수 도달: 현재 {rank}위 ≤ 목표 {self._stop_value}위"
        elif self._stop_mode == "score" and score is not None and score >= self._stop_value:
            hit_msg = f"목표 점수 도달: 현재 {score}점 ≥ 목표 {self._stop_value}점"
        if hit_msg is None:
            # 조건 미충족 → 연속 확정 카운트 리셋(스트릭이 끊기면 처음부터 다시).
            self._stop_confirm_count = 0
            return
        # 단발 오독 가드(#3): 합의로 확정된 값이라도 같은 조건이 STOP_CONFIRM_COUNT회
        # '연속' 확정될 때만 실제로 멈춘다 → 한 번의 오독이 작업 전체를 멈추는 사고를 막음.
        self._stop_confirm_count += 1
        if self._stop_confirm_count < STOP_CONFIRM_COUNT:
            self.queue_log(
                f"[자동 정지] 조건 충족({self._stop_confirm_count}/{STOP_CONFIRM_COUNT}) "
                f"— 재확인 대기: {hit_msg}"
            )
            return
        self._stop_triggered = True
        self.queue_log(f"[자동 정지] {hit_msg} → 매크로를 멈춥니다.")
        self.queue_status("목표 도달 — 정지")
        self.stop_event.set()

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

            if RANK_OCR_ENABLED and not rank_ocr.ocr_available():
                self.queue_log("[경고] 등수 OCR(winocr)을 사용할 수 없어 등수가 표시되지 않습니다.")
                self.queue_log(f"       원본 오류: {rank_ocr.WINOCR_IMPORT_ERROR}")
                self.queue_log("       'pip install winocr' + Windows 한국어 OCR 언어팩 확인.")

            requires_window = (
                capture_mode == "wgc"
                or click_mode == "postmessage"
                or any(target.action == "message" for target in targets)
            )

            if requires_window:
                manager = InactiveManager(window_title, logger=self.queue_log)

            # OCR(등수·SKIP)을 매칭 루프와 분리된 별도 스레드에서 처리합니다.
            # → 무거운 OCR이 이미지 인식(템플릿 매칭) 루프를 멈추지 않아 인식 속도 최대화.
            self._ocr_manager = manager
            self._latest_frame = None
            self._frame_seq = 0
            # 새 객체로 교체(reset 아님): 혹시 직전 run의 워커가 join 타임아웃으로 살아있어도
            # 옛 객체를 만지게 분리해, 새 워커의 투표 상태와 절대 안 섞이게 한다.
            self._rank_consensus = rank_ocr.RankConsensus()
            self._last_rank = None       # 마지막 값 유지 초기화(이전 run 잔류 방지)
            self._last_tier = None
            self._last_score = None
            self._skip_active_until = 0.0
            self._skip_seen_since = None
            self._skip_text_streak = 0
            self._skip_kind = None
            # (A) SKIP 버튼 탐색·학습 초기화. SKIP_A_BUTTON이 지정돼 있으면 그걸 바로 사용.
            self._skip_a_learned = (SKIP_A_BUTTON or "").strip().lower() or None
            self._skip_a_sweep_idx = 0
            self._skip_last_press = None
            self._skip_learned_fail = 0
            self._skip_a_dumped = False
            self._stop_confirm_count = 0
            rank_ocr.reset_skip_a_template()  # 커스텀 (A) SKIP 교체 가능성 → 캐시 새로고침
            if (RANK_OCR_ENABLED or SKIP_ENABLED) and rank_ocr.ocr_available():
                self._ocr_thread = threading.Thread(target=self._ocr_worker_loop, daemon=True)
                self._ocr_thread.start()

            # 상태 전송 타이머
            last_status_report = 0.0

            # 시작 상태 전송 (단일 워커 풀)
            self._report_status(running=True, message="매크로 시작")

            while not self.stop_event.is_set():
                # 주기적 상태 전송
                now_mono = time.monotonic()
                if self.license_key and now_mono - last_status_report >= STATUS_REPORT_INTERVAL_SECONDS:
                    last_status_report = now_mono
                    self._report_status(running=True)
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

                # 최신 프레임을 OCR 워커 스레드에 공개. (seq, gray) 튜플 대입은 원자적이라
                # 워커가 짝이 안 맞는 값을 볼 수 없다. seq로 새 프레임만 OCR(신선도 게이팅).
                self._frame_seq += 1
                self._latest_frame = (self._frame_seq, screen_gray)

                # SKIP 처리 중에는 잠깐 매칭을 멈춰 입력 충돌을 막는다(가벼운 플래그 체크).
                if now_mono < self._skip_active_until:
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
            self.stop_event.set()
            # OCR 워커 스레드 정리(최대 2초 대기).
            if self._ocr_thread is not None:
                self._ocr_thread.join(timeout=2.0)
                self._ocr_thread = None
            self._latest_frame = None
            self._ocr_manager = None
            if manager is not None:
                manager.stop_capture()
            self.queue_status("종료됨")
            self.queue_log("[종료] 자동화 루프가 종료되었습니다.")
            # 종료 상태 전송 (단일 워커 풀)
            self._report_status(running=False, message="매크로 종료")
            self.ui_queue.put(("finished", ""))

    def _report_status(self, running: bool, message: Optional[str] = None) -> None:
        """라이센스가 있으면 상태를 단일 워커 풀로 전송합니다(스레드 누적 방지).

        message가 None이면 OCR 상태(_rank_state)의 메시지를 사용합니다.
        등수·메시지를 한 번의 원자적 읽기로 가져와 짝이 항상 일치합니다.
        """
        if not self.license_key:
            return
        rank, rank_msg = self._rank_state
        msg = message if message is not None else rank_msg
        try:
            self._status_executor.submit(
                _send_status, self.license_key, rank, running, msg
            )
        except Exception:
            pass

    def _ocr_worker_loop(self) -> None:
        """별도 스레드: 매칭 루프를 막지 않고 SKIP/등수 OCR을 처리합니다.

        매칭 루프가 self._latest_frame에 올려둔 (seq, gray)를 가져와 OCR합니다.
        OCR이 아무리 무거워도 이미지 인식 루프(다른 스레드)는 영향받지 않습니다.

        등수 OCR은 '새 프레임이 올라올 때마다'(seq 변화) 실행해 0.7초 패널을 놓치지
        않고, 그 사이 SKIP은 0.3초 데드라인으로 끼어듭니다. 한 루프에서 둘을 무조건
        직렬로 돌리지 않아(가장 필요한 것만) 서로를 굶기지 않습니다.
        """
        last_rank_seq = -1
        last_rank_time = 0.0
        last_skip = 0.0
        while not self.stop_event.is_set():
            frame = self._latest_frame
            now = time.monotonic()
            did_ocr = False
            try:
                if frame is not None:
                    seq, gray = frame
                    # SKIP: 0.3초 데드라인으로 정확히 끼어듦(입력 직결이라 우선).
                    if SKIP_ENABLED and now - last_skip >= SKIP_OCR_INTERVAL_SECONDS:
                        last_skip = now
                        self._try_skip(gray, self._ocr_manager)
                        did_ocr = True
                    # 등수: 새 프레임(seq 변화)마다 OCR+투표. 같은 프레임은 재OCR 안 함.
                    # RANK_OCR_INTERVAL_SECONDS(기본 0)는 하한일 뿐 — 보통은 seq 변화가 트리거.
                    if (RANK_OCR_ENABLED and seq != last_rank_seq
                            and now - last_rank_time >= RANK_OCR_INTERVAL_SECONDS):
                        last_rank_seq = seq
                        last_rank_time = now
                        self._observe_rank(gray, now)
                        did_ocr = True
                # 확정은 '벽시계'로 매 사이클 폴링 — WGC가 정적 화면에서 프레임 공급을 멈춰
                # seq가 동결돼도 패널 소멸-후-커밋(graceful)·패널 경계 리셋이 진행되게 한다.
                if RANK_OCR_ENABLED:
                    self._commit_rank(now)
            except Exception as exc:
                self._log_to_file_only(f"[OCR thread] {exc}")
            # OCR을 돌렸으면 다음 새 프레임이 거의 대기 중 → 짧게, 아니면 살짝 길게(idle 절약).
            if self.stop_event.wait(0.005 if did_ocr else 0.02):
                break

    def _observe_rank(self, screen_gray, now: float) -> None:
        """프레임 왼쪽 일부를 OCR해 등수/티어/점수를 읽고 '투표에만' 반영합니다.

        새 프레임마다 호출. 실제 확정/보고는 _commit_rank가 벽시계로 따로 담당하므로,
        OCR이 멈춰도(WGC 정적 화면) 확정 로직이 프레임에 묶이지 않습니다. 실패해도 무시.
        """
        try:
            h, w = screen_gray.shape[:2]
            # 매치 화면 게이트: '팀 정보/유니폼 선택' 탭이 안 보이면(구단 관리 등 다른 화면)
            # 등수 OCR을 건너뛰고 '패널 없음'으로 투표 → 오인식 차단 + 패널 소멸 추적.
            if MATCH_GATE_ENABLED:
                gx1 = max(0, int(w * MATCH_GATE_LEFT_FRACTION))
                gx2 = min(w, int(w * MATCH_GATE_RIGHT_FRACTION))
                gy1 = max(0, int(h * MATCH_GATE_TOP_FRACTION))
                gy2 = min(h, int(h * MATCH_GATE_BOTTOM_FRACTION))
                gate_crop = screen_gray[gy1:gy2, gx1:gx2]
                if gate_crop.size == 0 or not rank_ocr.on_match_screen(gate_crop, MATCH_GATE_TOKENS):
                    self._rank_consensus.observe(
                        {"rank": None, "is_champion": False, "tier": None,
                         "score": None, "has_panel": False}, now)
                    # 추적성(#10): 매치 화면이 맞는데도 탭바를 못 읽으면(해상도/DPI 어긋남)
                    # 등수가 영영 안 잡힌다. 일반 화면에선 정상이라 파일 로그로 5초마다만 남긴다.
                    if now - self._gate_fail_log_at >= 5.0:
                        self._gate_fail_log_at = now
                        self._log_to_file_only(
                            "[게이트] 탭바('팀정보/유니폼') 미검출 → 등수 OCR 건너뜀"
                            "(매치 화면인데 반복되면 해상도/게이트 영역 확인)"
                        )
                    return
            l, t, r, b = self._ocr_box
            x1 = max(0, int(w * l))
            x2 = min(w, int(w * r))
            y1 = max(0, int(h * t))
            y2 = min(h, int(h * b))
            if x2 - x1 < 2 or y2 - y1 < 2:
                return
            crop = screen_gray[y1:y2, x1:x2]
            info = rank_ocr.read_rank_panel(crop, logger=None)
        except Exception:
            return
        # 이번 읽기(빈 결과 포함)를 투표에 반영 → 패널 소멸/경계 추적에 필요.
        self._rank_consensus.observe(info, now)

    def _commit_rank(self, now: float) -> None:
        """컨센서스가 확정한 (등수/티어/점수)를 _rank_state로 반영합니다(OCR 불필요).

        매 워커 사이클 벽시계로 호출 → 프레임이 멈춰도 '패널 소멸 후 graceful 확정'과
        '패널 경계 리셋'이 진행됩니다. 확정 직후엔 투표를 비워 같은 패널 재커밋과
        다음 패널과의 표 오염을 막습니다. 단발 오인식은 다수결로 이미 걸러진 값만 옵니다.
        """
        committed = self._rank_consensus.commit(now)
        if committed is None:
            return  # 아직 확정 못 함(표 부족) 또는 보고할 게 없음

        # 확정 처리 완료 → 투표를 비워 같은 패널을 재커밋하지 않고 다음 패널과 분리.
        self._rank_consensus.reset()

        rank, tier, score = committed
        # 컨센서스로 '확정된' 값에서만 목표 도달을 판정 → 단발 오인식/스테일로 잘못 멈추지 않음.
        # 정지 판정엔 '이번 패널에서 실제로 읽힌' 티어만 넘긴다(없으면 None) → 직전 매치의
        # 슈퍼챔스 티어가 다음 매치로 새서 거짓 정지하는 일을 막는다(#5). 표시용 보강은 아래에서.
        self._maybe_stop_on_target(rank, score, tier)

        # 마지막 값 유지: 이번에 '읽힌' 필드만 갱신하고, 없는 필드는 직전 값을 보존한다.
        # → 로비(등수 없음)에서 읽어도 매칭 때 잡은 등수가 안 지워지고 계속 표시된다.
        if rank is not None:
            self._last_rank = rank
        if tier is not None:
            self._last_tier = tier
        if score is not None:
            self._last_score = score

        # 표시: 유지된 등수 + (티어·점수)를 함께 보여준다(봇 임베드에 등수/메시지 동시 표시).
        parts = []
        if self._last_tier:
            parts.append(self._last_tier)
        if self._last_score is not None:
            parts.append(f"{self._last_score}점")
        # 티어·점수가 없으면 빈 메시지("") → 봇이 중복 '메시지' 줄을 안 띄움.
        message = " · ".join(parts)
        new_state = (self._last_rank, message)

        if new_state == self._rank_state:
            return  # 변화 없음(이미 같은 값을 보고함)

        # (등수, 메시지)를 한 번에 원자적으로 교체 → 다른 스레드가 짝이 안 맞는 값을 못 봄.
        self._rank_state = new_state
        if rank is not None:
            self.queue_log(f"[등수] 현재 등수: {rank}위")
        elif tier is not None or score is not None:
            self.queue_log(f"[티어/점수] {message}")
        # 변경 즉시 서버로 전송(다음 30초 주기 안 기다림).
        self._report_status(running=True)

    @staticmethod
    def _skip_a_choose(learned, sweep_idx: int, elapsed: float):
        """(A) SKIP 다음 행동 결정(순수 로직 — Mac 단위검증용).

        1) 학습된(또는 SKIP_A_BUTTON 지정) 입력이 있으면 그걸 누른다.
        2) 없으면 후보 목록을 앞에서부터 하나씩(스윕).
        3) 스윕을 다 돌았으면: 최후수단이 켜져 있고(elapsed 조건 충족) → sendinput,
           꺼져 있으면(기본) → ("restart", None)로 스윕 재시작(비활성 100% 유지).
        """
        if learned:
            return ("press", learned)
        if sweep_idx < len(SKIP_A_SWEEP_BUTTONS):
            return ("press", SKIP_A_SWEEP_BUTTONS[sweep_idx])
        if (SKIP_A_SENDINPUT_AFTER_SECONDS
                and elapsed >= SKIP_A_SENDINPUT_AFTER_SECONDS):
            return ("sendinput", None)
        return ("restart", None)

    def _press_skip_candidate(self, name: str, manager: InactiveManager) -> bool:
        """(A) SKIP 후보 입력 하나를 실행합니다(전부 비활성 유지 방식 — 화면 변화 0).

        깜빡임 0 · 유출 0(포커스·활성 안 건드림):
        - "attach_state_s": AttachThreadInput+SetKeyboardState(GetKeyState 폴링 속이기)
        - "char_s":  WM_KEYDOWN+WM_CHAR+WM_KEYUP 딥 전송(CEF UI가 WM_CHAR로 받는 경우)
        - "attach_post_s": AttachThreadInput(큐 공유)+포커스창 WM_KEYDOWN/UP(SetFocus 없음)
        - "pm_s":    WM_KEYDOWN(s)을 최상위+포커스 자식+모든 자식 창에 딥 전송
        부작용 있음(수동 지정 전용):
        - "focus_s": AttachThreadInput+SetFocus — 동작하나 창 활성화로 화면 깜빡임
        - "si_s":    SendInput s(전역) — 깜빡임 0이나 활성 앱에 s 유출 가능
        - "lt"/"rt": 가상패드 트리거 / 그 외: 가상패드 버튼(KEY_TO_GAMEPAD 매핑)
        """
        try:
            if name == "pm_s":
                if manager.hwnd and winapi.win32gui is not None:
                    vk = input_message.KEY_TO_VK.get("s")
                    if vk:
                        return input_message.post_key_deep(
                            manager.hwnd, vk, press_delay=SKIP_A_TAP_SECONDS)
                return False
            if name == "char_s":
                if manager.hwnd and winapi.win32gui is not None:
                    vk = input_message.KEY_TO_VK.get("s")
                    if vk:
                        return input_message.post_char_deep(
                            manager.hwnd, vk, ord("s"), press_delay=SKIP_A_TAP_SECONDS)
                return False
            if name == "attach_state_s":
                vk = input_message.KEY_TO_VK.get("s")
                if manager.hwnd and vk:
                    return input_message.send_key_attach_state(
                        manager.hwnd, vk, hold=SKIP_A_TAP_SECONDS)
                return False
            if name == "attach_post_s":
                vk = input_message.KEY_TO_VK.get("s")
                if manager.hwnd and vk:
                    return input_message.send_key_attach_post(
                        manager.hwnd, vk, hold=SKIP_A_TAP_SECONDS)
                return False
            if name == "focus_s":
                if manager.hwnd:
                    return input_message.send_key_focus_attach(
                        manager.hwnd, input_message.SCAN_S, hold=SKIP_A_TAP_SECONDS)
                return False
            if name == "si_s":
                return input_message.send_scancode_key(
                    input_message.SCAN_S, hold=SKIP_A_TAP_SECONDS)
            if name in input_gamepad.TRIGGER_KEYS:
                send_gamepad_trigger(name, press_delay=SKIP_A_TAP_SECONDS)
                return True
            btn = input_gamepad.KEY_TO_GAMEPAD.get(name)
            if btn is None:
                return False
            send_gamepad_button(btn, press_delay=SKIP_A_TAP_SECONDS)
            return True
        except Exception:
            return False

    def _try_skip(self, screen_gray, manager: Optional[InactiveManager]) -> bool:
        """화면에 SKIP이 보이면 종류에 맞는 버튼을 눌러 넘기고 True를 반환합니다.

        - '(A) SKIP'(초록 A 버튼 = target_skip_a.png 템플릿) → A만 누른다.
        - 그 외 일반 스킵(OCR 'skip'/'스킵', SKIP_TEXT_CONSENSUS회 연속 확인) → Start만.
        - 한 스킵이 SKIP_FALLBACK_BOTH_SECONDS 넘게 안 사라지면 A·Start 둘 다(안전망).

        반환 True면 매칭이 _skip_active_until까지 잠시 멈춥니다. 미감지 시 False이고
        스킵 상태(타이머·연속카운트)를 초기화합니다.
        """
        if winapi.vg is None or not rank_ocr.ocr_available() or manager is None:
            # 대상 창 없음/화면영역 모드 → 헛누름 방지로 건너뛰고 상태 초기화.
            self._skip_seen_since = None
            self._skip_text_streak = 0
            return False
        try:
            h, w = screen_gray.shape[:2]
            x1 = max(0, int(w * SKIP_OCR_LEFT_FRACTION))
            x2 = min(w, int(w * SKIP_OCR_RIGHT_FRACTION))
            y1 = max(0, int(h * SKIP_OCR_TOP_FRACTION))
            y2 = min(h, int(h * SKIP_OCR_BOTTOM_FRACTION))
            crop = screen_gray[y1:y2, x1:x2]   # 원본 해상도 — (A) 템플릿은 여기서 매칭
            # 1) (A) SKIP 템플릿 우선(정밀). 매칭되면 OCR을 안 보므로 A형이 Start로 안 샌다.
            is_a, _score, a_center = rank_ocr.match_skip_a(crop, self.base_dir)
            has_text = False
            if not is_a:
                # 2) 일반 스킵: OCR용으로만 폭 축소 후 텍스트 확인(글자는 크므로 인식 유지).
                ocr_crop = crop
                if SKIP_OCR_MAX_WIDTH and ocr_crop.shape[1] > SKIP_OCR_MAX_WIDTH:
                    scale = SKIP_OCR_MAX_WIDTH / ocr_crop.shape[1]
                    ocr_crop = cv2.resize(
                        ocr_crop, (SKIP_OCR_MAX_WIDTH, max(1, int(ocr_crop.shape[0] * scale))),
                        interpolation=cv2.INTER_AREA,
                    )
                has_text = rank_ocr.contains_skip(ocr_crop, logger=None)
        except Exception:
            return False

        if not is_a and not has_text:
            # 스킵 없음 → 에피소드 종료. 종료 직전(1.5초 내)에 누른 후보가 있으면
            # 그 입력이 스킵을 넘긴 것으로 보고 '학습'한다(다음부턴 바로 그 버튼).
            if self._skip_kind == "a" and self._skip_last_press is not None:
                name, t = self._skip_last_press
                if (name != "sendinput" and time.monotonic() - t <= 1.5
                        and self._skip_a_learned != name):
                    self._skip_a_learned = name
                    self._skip_learned_fail = 0
                    self.queue_log(
                        f"[SKIP] 학습 완료: (A) SKIP은 '{name.upper()}' 입력으로 넘어감 — 다음부터 바로 사용"
                    )
            self._skip_seen_since = None
            self._skip_text_streak = 0
            self._skip_kind = None
            self._skip_last_press = None
            self._skip_a_sweep_idx = 0   # 다음 에피소드는 처음부터(학습됐으면 학습값 우선)
            return False

        now = time.monotonic()
        if self._skip_seen_since is None:
            self._skip_seen_since = now
        # 감지 중엔 매칭을 잠시 멈춰 스킵 화면에서의 오클릭을 막는다(입력 여부와 무관).
        self._skip_active_until = now + 0.5

        press_a = press_start = False
        if is_a or self._skip_kind == "a":
            # A형: 이번 프레임 템플릿이 맞았거나, '이미 이 에피소드가 A형으로 확정'된 경우.
            # → 움직이는 배경(리플레이) 위에서 템플릿이 프레임마다 깜빡여도 A를 '매 주기' 누른다.
            # 한 번 A로 분류되면 에피소드 끝까지 A로 고정(sticky) → A 입력이 끊기지 않는다.
            self._skip_kind = "a"
            self._skip_text_streak = 0
            press_a = True
            kind = "A"
        else:
            # 아직 A로 분류 안 됨 + 이번 프레임 템플릿도 미스 → 일반(Start) 후보.
            # 단발 OCR 노이즈 + 'A형 첫 프레임이 깜빡인' 경우를 막기 위해 N연속 후에만 Start.
            self._skip_text_streak += 1
            if self._skip_text_streak < SKIP_TEXT_CONSENSUS:
                self.queue_status("SKIP 넘기는 중")
                return True   # 확정 전 — 매칭만 멈추고 입력은 보류(다음 프레임에 A로 바뀔 수도)
            self._skip_kind = "start"
            press_start = True
            kind = "Start"

        # 안전망: 일반(Start) 에피소드에서만, 스킵이 오래 안 사라지면 둘 다 누른다.
        # '확정된 A형'은 절대 Start를 안 눌러(계약: A형은 A만) 오작동을 막고, A형인데 템플릿이
        # 한 번도 안 맞아 Start 경로로 빠진 경우는 여기서 A까지 눌러 스킵에 갇히지 않게 한다.
        if self._skip_kind != "a" and (now - self._skip_seen_since) >= SKIP_FALLBACK_BOTH_SECONDS:
            press_a = press_start = True
            kind = "A·Start(안전망)"

        try:
            if manager.hwnd and winapi.win32gui is not None:
                WM_ACTIVATE = 0x0006
                WA_ACTIVE = 1
                winapi.win32gui.PostMessage(manager.hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
            start_btn = input_gamepad.KEY_TO_GAMEPAD.get("start")
            action_label = "입력"
            if press_a:
                # 첫 (A) SKIP에서 창 클래스 1회 진단 — CEF(Chrome_RenderWidgetHostHWND)면
                # 메시지 주입 가망, 없으면 네이티브(RawInput/DirectInput일 확률↑).
                if not self._skip_a_dumped and manager.hwnd:
                    self._skip_a_dumped = True
                    self.queue_log("[SKIP 진단] " + input_message.dump_window_classes(manager.hwnd))
                did_click = False
                if SKIP_A_CLICK and a_center is not None and manager.hwnd:
                    # (선택) 버튼 위치 클릭 — 이 게임은 실측상 안 통해 기본 꺼짐(config).
                    fx = x1 + int(a_center[0])
                    fy = y1 + int(a_center[1])
                    start_pos = manager.get_virtual_start_position(fx, fy)
                    sx, sy = start_pos if start_pos else (fx, fy)
                    did_click = bool(manager.post_curved_click(sx, sy, fx, fy))
                    if did_click:
                        action_label = "클릭"
                if not did_click:
                    plan, name = self._skip_a_choose(
                        self._skip_a_learned,
                        self._skip_a_sweep_idx,
                        now - self._skip_seen_since,
                    )
                    if plan == "press":
                        # 후보(또는 학습된) 입력을 탭. 성공 여부는 에피소드 종료 시 학습으로 판정.
                        if name == self._skip_a_learned:
                            self._skip_learned_fail += 1
                            if self._skip_learned_fail > 3:
                                # 학습된 입력이 3번 연속 무효 → 학습 취소하고 탐색 재개.
                                self.queue_log(f"[SKIP] '{name.upper()}' 무효 — 자동 탐색 재개")
                                self._skip_a_learned = None
                                self._skip_learned_fail = 0
                        else:
                            self._skip_a_sweep_idx += 1
                        if self._press_skip_candidate(name, manager):
                            self._skip_last_press = (name, time.monotonic())
                            action_label = f"시도:{name.upper()}"
                    elif plan == "sendinput" and manager.hwnd:
                        # 최후수단(기본 꺼짐): 잠깐 전면 전환+진짜 키보드 s → 원래 창 복원.
                        if input_message.press_key_foreground(
                                manager.hwnd, input_message.SCAN_S, hold=0.12):
                            self._skip_last_press = ("sendinput", time.monotonic())
                            action_label = "키보드s(전면전환)"
                    else:
                        # 탐색을 한 바퀴 다 돌았고 최후수단은 꺼짐 → 처음부터 다시 탐색.
                        self._skip_a_sweep_idx = 0
                        action_label = "탐색 재시작"
            if press_start and start_btn is not None:
                send_gamepad_button(start_btn, press_delay=SKIP_PRESS_DELAY_SECONDS)
            self._skip_active_until = max(
                self._skip_active_until, time.monotonic() + 0.5
            )
            self.queue_status("SKIP 넘기는 중")
            self.queue_log(f"[SKIP] 감지({kind}) → {action_label}")
            return True
        except Exception as exc:  # noqa: BLE001
            self.queue_log(f"[SKIP] 입력 중 오류: {exc}")
            return False

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

    def _set_accent_button_state(self, button: tk.Button, enabled: bool) -> None:
        """빨간 강조 버튼의 활성/비활성 상태와 색을 함께 바꿉니다."""

        c = self.COLORS
        button.configure(
            state=tk.NORMAL if enabled else tk.DISABLED,
            bg=c["accent"] if enabled else c["disabled"],
        )

    def _set_button_state(self, running: bool) -> None:
        """실행 상태에 따라 시작/종료/캡처 버튼 활성화를 조절합니다."""

        self._set_accent_button_state(self.start_button, enabled=not running)
        self._set_accent_button_state(self.stop_button, enabled=running)

        # 실행 중 템플릿 교체는 다음 시작까지 반영되지 않으므로 혼동을 막기 위해 잠급니다.
        for button in self._capture_buttons.values():
            self._set_accent_button_state(button, enabled=not running)
        for button in self._reset_buttons.values():
            button.configure(state=tk.DISABLED if running else tk.NORMAL)


class LicenseDialog:
    """라이센스 키 입력/검증 다이얼로그입니다."""

    def __init__(self, root: tk.Tk, base_dir: Path):
        self.root = root
        self.base_dir = base_dir
        self.result: Optional[str] = None

        self.root.title("라이센스 인증")
        self.root.geometry("480x340+200+200")
        self.root.resizable(False, False)

        bg = "#101010"
        panel_bg = "#1A1A1A"
        input_bg = "#262626"
        text_color = "#F3F3F3"
        accent = "#D93A2B"
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
            fg="#FFFFFF",
            activebackground="#B72D20",
            activeforeground="#FFFFFF",
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
