from __future__ import annotations
import io
import json
import os
import platform
import queue
import random
import threading
import time
import traceback
import zlib
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
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
from macroapp.config import (
    TargetImage, WINDOW_TITLE, LOOP_SLEEP_SECONDS, WINDOW_RETRY_SECONDS,
    WINDOW_VALIDATION_INTERVAL_SECONDS,
    CLICK_JITTER_PIXELS, MOUSE_HOVER_BEFORE_CLICK_SECONDS,
    DEFAULT_REGION_X, DEFAULT_REGION_Y, DEFAULT_REGION_WIDTH, DEFAULT_REGION_HEIGHT,
    CUSTOM_TARGETS_DIR_NAME,
    NOTIFICATION_PANEL_GUARD_ENABLED, NOTIFICATION_PANEL_CHECK_INTERVAL_SECONDS,
    NOTIFICATION_PANEL_CONFIRM_COUNT, NOTIFICATION_PANEL_RETRY_SECONDS,
    NOTIFICATION_TOGGLE_X_FRACTION, NOTIFICATION_TOGGLE_Y_FRACTION,
    RANK_OCR_ENABLED, RANK_OCR_INTERVAL_SECONDS, RANK_OCR_CACHE_SECONDS,
    MATCH_GATE_ENABLED, MATCH_GATE_LEFT_FRACTION, MATCH_GATE_RIGHT_FRACTION,
    MATCH_GATE_TOP_FRACTION, MATCH_GATE_BOTTOM_FRACTION, MATCH_GATE_TOKENS,
    SKIP_ENABLED, SKIP_OCR_INTERVAL_SECONDS, SKIP_PRESS_DELAY_SECONDS,
    SKIP_A_BUTTON, SKIP_A_SWEEP_BUTTONS, SKIP_A_TAP_SECONDS, SKIP_A_SWEEP_HOLD_SECONDS,
    SKIP_A_SENDINPUT_AFTER_SECONDS, SKIP_A_FOREGROUND, SKIP_A_HOLD_SECONDS,
    SKIP_A_FG_COOLDOWN_SECONDS, SKIP_A_ACTIVATE_SPOOF, SKIP_A_FOREGROUND_AFTER_SECONDS,
    SKIP_STRICT_INACTIVE_EXPERIMENT, SKIP_INACTIVE_EXPERIMENT_CANDIDATES,
    SKIP_S_INACTIVE_EXPERIMENT_CANDIDATES,
    SKIP_EXPERIMENT_ATTEMPT_GAP_SECONDS, SKIP_EXPERIMENT_RESULT_WINDOW_SECONDS,
    SKIP_EXPERIMENT_CONFIRM_SUCCESSES, SKIP_EXPERIMENT_CONTROL_SECONDS,
    SKIP_EXPERIMENT_EXIT_CONFIRM_SECONDS,
    SKIP_EXPERIMENT_PROGRESSIVE_CONTROL,
    SKIP_EXPERIMENT_CONTROL_RAMP_SUCCESSES,
    SKIP_EXPERIMENT_FOREGROUND_POLL_SECONDS,
    SKIP_EXPERIMENT_LOG_FILENAME, SKIP_EXPERIMENT_LEARNING_FILENAME,
    SKIP_A_MATCH_THRESHOLD, SKIP_S_MATCH_THRESHOLD,
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
from macroapp.matching import find_template_center, downscale_screen, is_notification_panel_open
from macroapp.mining_ui import MiningDashboard
from macroapp.session import SessionTracker
from macroapp.skip_experiment import (
    SkipExperimentTracker,
    SkipOutcome,
    load_device_learning,
    remove_device_learning,
    run_guarded_inactive_action,
    save_device_learning,
)
from macroapp.window import InactiveManager
from macroapp import window_fit


def _ocr_regions_fingerprint(*regions: np.ndarray) -> tuple:
    """OCR 영역의 저비용 지문. 같은 정지 UI의 winocr 재호출을 피합니다."""

    values: list[object] = []
    for region in regions:
        frame = np.asarray(region)
        if frame.ndim != 2 or frame.size == 0:
            values.extend((0, 0, 0))
            continue
        height, width = frame.shape
        step_y = max(1, height // 24)
        step_x = max(1, width // 48)
        sampled = np.ascontiguousarray(frame[::step_y, ::step_x])
        values.extend((height, width, zlib.adler32(sampled.tobytes())))
    return tuple(values)


class AutomationApp:
    """tkinter UI와 자동화 스레드를 관리합니다."""

    # 장시간 봐도 눈이 편한 뉴트럴 다크 + 단일 강조색(인디고) 팔레트.
    # 채도를 낮춘 배경 위에 강조색 하나만 쓰면 정보 위계가 또렷해집니다.
    COLORS = {
        "bg": "#0A0D14",            # 앱 배경(가장 어두운 층)
        "sidebar": "#070910",       # 사이드바 — 배경보다 한 단계 더 깊게
        "sidebar_active": "#151A26",
        "panel": "#11151E",         # 카드 표면
        "row": "#161B26",           # 카드 안 리스트 행(한 단계 밝게)
        "row_hover": "#1B2130",
        "border": "#1E2532",        # 1px 실선 테두리
        "input": "#0D1119",         # 입력/트랙 배경
        "grid": "#1A2028",
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

    def __init__(
        self,
        root: tk.Tk,
        license_key: Optional[str] = None,
        *,
        preview: bool = False,
    ):
        self.root = root
        self.license_key = license_key
        self.preview = bool(preview)
        self.license_info: Optional[dict] = None

        self.root.title("mAuto")
        # LicenseDialog가 고정 크기로 만든 root를 재사용하므로 리사이즈를 다시 허용합니다.
        self.root.resizable(True, True)
        # 1366x768 같은 작은 화면에서도 하단 시작/정지 버튼이 잘리지 않게 높이를 화면에 맞춥니다.
        screen_height = self.root.winfo_screenheight()
        window_height = min(780, max(560, screen_height - 110))
        offset_y = max(0, min(60, screen_height - window_height - 90))
        self.root.geometry(f"1320x{window_height}+60+{offset_y}")
        # 오른쪽 설정 열이 고정폭이라, 이보다 좁히면 타겟 행 끝(기본값 버튼)이 잘립니다.
        self.root.minsize(1280, 640)
        self.window_title_var = tk.StringVar(value=WINDOW_TITLE)
        initial_status = "대기 중"
        self.status_var = tk.StringVar(value=initial_status)
        self.base_dir = app_dir()
        self.target_definitions = load_target_definitions(self.base_dir)
        self.target_names = tuple(target.name for target in self.target_definitions)
        self.threshold_lock = threading.Lock()
        self._threshold_revision = 0
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
        self.clock_var = tk.StringVar(value="00:00:00")
        self.session_matches_var = tk.StringVar(value="0")
        self.session_record_var = tk.StringVar(value="0승 · 0무 · 0패")
        self.session_rate_var = tk.StringVar(value="0.0 경기/h")
        self.session_fc_var = tk.StringVar(value="0 FC")
        self._session_tracker = SessionTracker()
        self._thumb_refs: dict[str, Any] = {}
        self._thumb_labels: dict[str, tk.Label] = {}
        self._target_source_vars: dict[str, tk.StringVar] = {}
        self._target_source_labels: dict[str, tk.Label] = {}
        self._capture_buttons: dict[str, tk.Button] = {}
        self._reset_buttons: dict[str, tk.Button] = {}
        self._threshold_canvases: dict[str, tk.Canvas] = {}
        self._capturing_template = False
        # 게임 창 맞춤 상태(되돌리기용 스냅샷).
        self.fit_status_var = tk.StringVar(value="")
        self._fit_snapshot = None

        self.stop_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.ui_queue: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self._last_queued_status: Optional[str] = None
        self.closing = False
        # 상태 전송 전용 단일 워커 풀 (스레드 누적 방지)
        self._status_executor = ThreadPoolExecutor(max_workers=1)
        self._log_dirty = False
        self._log_lock = threading.Lock()
        self._close_deadline = 0.0
        # (등수, 상태메시지)를 한 튜플로 묶어 스레드 간 원자적으로 교체/읽기.
        self._rank_state = (None, "실행 중")
        # 매칭 루프 → OCR 워커로 공개하는 최신 프레임. (시퀀스번호, gray) 튜플을 원자적으로 교체.
        # 시퀀스번호로 '아직 OCR 안 한 새 프레임'만 골라 같은 프레임 중복 OCR을 막는다(신선도 게이팅).
        self._latest_frame = None
        self._frame_seq = 0
        self._rank_consensus = rank_ocr.RankConsensus()  # 다중 프레임 투표(단발 오인식 폐기)
        self._rank_ocr_cache_key = None
        self._rank_ocr_cache_info = None
        self._rank_ocr_cache_at = 0.0
        self._rank_ocr_cache_hits = 0
        self._ocr_manager = None        # OCR 워커가 SKIP 입력에 쓰는 매니저
        self._ocr_thread = None         # OCR 전용 스레드
        self._skip_active_until = 0.0   # 이 시각 전까지 매칭 일시정지(SKIP 처리 중)
        self._skip_seen_since = None    # 현재 스킵이 처음 감지된 시각(안전망 타이머용)
        self._skip_text_streak = 0      # 일반 스킵(OCR) 연속 감지 횟수(단발 노이즈 차단)
        self._skip_kind = None          # 현재 스킵 에피소드 종류: None|"a"|"start" (A형은 sticky)
        self._skip_prompt_variant = None  # hold-to-skip 표시: None|"a"|"s"
        self._skip_prompt_center = None   # WGC 좌표의 A/S 표시 중심(비활성 클릭 후보용)
        # (A) SKIP 자동 버튼 탐색·학습 상태 — 게임이 가상 A를 무시해서, 어떤 입력이
        # 실제로 스킵을 넘기는지 후보를 하나씩 눌러보고 성공한 것을 학습한다.
        self._skip_a_learned = None     # 학습된(또는 SKIP_A_BUTTON 지정) 입력 이름
        self._skip_a_sweep_idx = 0      # 다음에 시도할 후보 인덱스
        self._skip_last_press = None    # (입력이름, 시각) — 에피소드 종료 시 학습 판정용
        self._skip_learned_fail = 0     # 학습된 입력이 연속 무효면 학습 취소용
        self._skip_a_dumped = False     # (A) SKIP 첫 감지 시 창 클래스 1회 진단 덤프
        self._skip_diag_count = 0       # 세션당 진단 이미지 최대 개수 제한용
        self._skip_fg_last_at = 0.0     # 마지막 전면화-홀드 시각(쿨다운 — 반복 번쩍임 방지)
        try:
            self._skip_device_id = get_hwid()
        except Exception:
            self._skip_device_id = "unknown"
        self._skip_learning_path = (
            self.base_dir / "logs" / SKIP_EXPERIMENT_LEARNING_FILENAME
        )
        self._skip_learned_profiles = load_device_learning(
            self._skip_learning_path,
            self._skip_device_id,
        )
        self._skip_experiment = self._new_skip_experiment_tracker()
        self._skip_s_experiment = self._new_skip_s_experiment_tracker()
        # 잘못 열린 알림 패널 감지·복구 상태. 열린 동안 일반 타겟 클릭을 막아 추가 오작동을 피한다.
        self._notification_check_at = 0.0
        self._notification_streak = 0
        self._notification_visible = False
        self._notification_last_action_at = 0.0
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

        # UI 로그는 만들지 않고 진단 파일만 유지합니다.
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
        if not self.preview and winapi.vg is not None:
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

    @staticmethod
    def _round_rect(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs):
        """캔버스에 둥근 사각형을 그립니다(tkinter 기본 위젯에는 라운드가 없습니다)."""

        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        return canvas.create_polygon(points, smooth=True, **kwargs)

    def _panel(
        self,
        parent: tk.Misc,
        title: Optional[str] = None,
        *,
        subtitle: Optional[str] = None,
        padx: int = 16,
        pady: int = 14,
    ) -> tk.Frame:
        """1px 테두리의 카드 프레임을 만듭니다. 제목은 좌측 정렬 + 강조 바."""

        c = self.COLORS
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
                head,
                text=title,
                bg=c["panel"],
                fg=c["text"],
                font=self._font(11, bold=True),
            ).pack(side=tk.LEFT, padx=(9, 0))
            if subtitle:
                tk.Label(
                    head,
                    text=subtitle,
                    bg=c["panel"],
                    fg=c["muted"],
                    font=self._font(8),
                ).pack(side=tk.RIGHT, pady=(3, 0))
        return frame

    def _button(
        self,
        parent: tk.Misc,
        text: str,
        command,
        *,
        tone: str = "primary",
        small: bool = False,
    ) -> tk.Button:
        """톤(primary/ghost/danger)에 맞춘 플랫 버튼 + 호버 효과."""

        c = self.COLORS
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
            font=self._font(9 if small else 11, bold=True),
            padx=10 if small else 18,
            pady=2 if small else 9,
        )
        button._tone_colors = (base, hover)  # 호버·비활성 복원용
        button.bind("<Enter>", lambda _e, b=button, v=hover: self._hover_button(b, v))
        button.bind("<Leave>", lambda _e, b=button, v=base: self._hover_button(b, v))
        return button

    @staticmethod
    def _hover_button(button: tk.Button, color: str) -> None:
        """비활성 버튼은 호버 색을 무시합니다."""

        if str(button["state"]) == tk.DISABLED:
            return
        button.configure(bg=color)

    def _segmented(
        self,
        parent: tk.Misc,
        variable: tk.StringVar,
        options: tuple,
        command=None,
    ) -> tk.Frame:
        """라디오 버튼을 대신하는 세그먼트 토글(선택 항목만 강조)."""

        c = self.COLORS
        holder = tk.Frame(
            parent,
            bg=c["input"],
            padx=2,
            pady=2,
            highlightbackground=c["border"],
            highlightthickness=1,
        )
        items: dict[str, tk.Label] = {}

        def paint(*_args) -> None:
            current = variable.get()
            for value, label in items.items():
                selected = value == current
                label.configure(
                    bg=c["accent"] if selected else c["input"],
                    fg="#FFFFFF" if selected else c["muted"],
                )

        def choose(value: str) -> None:
            variable.set(value)
            paint()
            if command is not None:
                command()

        for text, value in options:
            label = tk.Label(
                holder,
                text=text,
                bg=c["input"],
                fg=c["muted"],
                font=self._font(9, bold=True),
                padx=12,
                pady=5,
                cursor="hand2",
            )
            label.pack(side=tk.LEFT)
            label.bind("<Button-1>", lambda _e, v=value: choose(v))
            label.bind(
                "<Enter>",
                lambda _e, v=value, w=label: (
                    None if variable.get() == v else w.configure(bg=c["row_hover"], fg=c["text"])
                ),
            )
            label.bind("<Leave>", lambda _e: paint())
            items[value] = label

        paint()
        # on_capture_mode_changed 처럼 코드가 값을 바꿔도 강조가 따라오게 합니다.
        variable.trace_add("write", paint)
        return holder

    def _entry(
        self,
        parent: tk.Misc,
        variable,
        *,
        width: int = 7,
        center: bool = True,
    ) -> tk.Entry:
        """테두리만 있는 납작한 입력칸(포커스 시 강조색 테두리)."""

        c = self.COLORS
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
            font=self._font(9),
            highlightbackground=c["border"],
            highlightcolor=c["accent"],
            highlightthickness=1,
        )

    def _field(self, parent: tk.Misc, label: str, variable, *, width: int = 6) -> tk.Frame:
        """작은 라벨 + 입력칸 묶음(영역 X/Y/W/H 처럼 짧은 값용)."""

        c = self.COLORS
        holder = tk.Frame(parent, bg=c["panel"])
        tk.Label(
            holder,
            text=label,
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(8, bold=True),
            width=2,
        ).pack(side=tk.LEFT)
        self._entry(holder, variable, width=width).pack(side=tk.LEFT, ipady=4)
        return holder

    def _setting_row(self, parent: tk.Misc, label: str) -> tk.Frame:
        """'기본 설정' 카드의 한 줄(왼쪽 라벨 + 오른쪽 컨트롤)."""

        c = self.COLORS
        row = tk.Frame(parent, bg=c["panel"])
        row.pack(fill=tk.X, pady=4)
        tk.Label(
            row,
            text=label,
            bg=c["panel"],
            fg=c["muted"],
            width=11,
            anchor=tk.W,
            font=self._font(9, bold=True),
        ).pack(side=tk.LEFT)
        return row

    # ── 임계값 슬라이더(캔버스 자체 구현 — tk.Scale은 옛 윈도우 느낌이라 대체) ──

    def _slider(self, parent: tk.Misc, name: str, *, width: int = 120, height: int = 24) -> tk.Canvas:
        c = self.COLORS
        canvas = tk.Canvas(
            parent,
            width=width,
            height=height,
            bg=c["row"],
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        canvas._hover = False
        canvas.bind("<Configure>", lambda _e, n=name: self._draw_slider(n))
        canvas.bind("<Button-1>", lambda e, n=name: self._slider_set(n, e.x))
        canvas.bind("<B1-Motion>", lambda e, n=name: self._slider_set(n, e.x))
        canvas.bind("<Enter>", lambda _e, n=name, w=canvas: (setattr(w, "_hover", True), self._draw_slider(n)))
        canvas.bind("<Leave>", lambda _e, n=name, w=canvas: (setattr(w, "_hover", False), self._draw_slider(n)))
        self._threshold_canvases[name] = canvas
        return canvas

    def _slider_set(self, name: str, x: int) -> None:
        """클릭/드래그한 x좌표를 0.50~1.00 임계값으로 변환합니다."""

        canvas = self._threshold_canvases.get(name)
        if canvas is None:
            return
        pad = 10
        width = max(1, canvas.winfo_width() - pad * 2)
        ratio = min(1.0, max(0.0, (x - pad) / width))
        value = round(0.5 + ratio * 0.5, 2)
        self.threshold_vars[name].set(value)
        self.update_threshold(name, value)

    def _draw_slider(self, name: str) -> None:
        """트랙 + 채워진 구간 + 손잡이를 다시 그립니다."""

        canvas = self._threshold_canvases.get(name)
        if canvas is None:
            return
        c = self.COLORS
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width <= 1:
            return
        pad = 10
        mid = height // 2
        left, right = pad, width - pad
        value = float(self.threshold_vars[name].get())
        ratio = min(1.0, max(0.0, (value - 0.5) / 0.5))
        knob_x = left + (right - left) * ratio

        canvas.delete("all")
        canvas.create_line(left, mid, right, mid, fill=c["input"], width=5, capstyle=tk.ROUND)
        if knob_x > left:
            canvas.create_line(
                left, mid, knob_x, mid,
                fill=c["accent"], width=5, capstyle=tk.ROUND,
            )
        radius = 7 if getattr(canvas, "_hover", False) else 6
        canvas.create_oval(
            knob_x - radius, mid - radius, knob_x + radius, mid + radius,
            fill="#FFFFFF",
            outline=c["accent_hover"] if getattr(canvas, "_hover", False) else c["accent"],
            width=2,
        )

    def _sidebar_button(self, parent: tk.Misc, icon: str, text: str, page: str) -> tk.Frame:
        """왼쪽 강조 바가 붙는 사이드바 내비게이션 항목을 만듭니다."""

        c = self.COLORS
        item = tk.Frame(parent, bg=c["sidebar"], cursor="hand2")
        item.pack(fill=tk.X, pady=2)
        indicator = tk.Frame(item, bg=c["sidebar"], width=3)
        indicator.pack(side=tk.LEFT, fill=tk.Y)
        body = tk.Frame(item, bg=c["sidebar"], padx=13, pady=10)
        body.pack(side=tk.LEFT, fill=tk.X, expand=True)
        icon_label = tk.Label(
            body,
            text=icon,
            bg=c["sidebar"],
            fg=c["muted"],
            font=self._font(9),
        )
        icon_label.pack(side=tk.LEFT)
        text_label = tk.Label(
            body,
            text=text,
            bg=c["sidebar"],
            fg=c["muted"],
            anchor=tk.W,
            font=self._font(10, bold=True),
        )
        text_label.pack(side=tk.LEFT, padx=(9, 0))

        widgets = (item, body, icon_label, text_label)
        for widget in widgets:
            widget.bind("<Button-1>", lambda _e, p=page: self._show_page(p))
            widget.bind("<Enter>", lambda _e, p=page: self._hover_nav(p, True))
            widget.bind("<Leave>", lambda _e, p=page: self._hover_nav(p, False))
        self._nav_buttons[page] = {
            "item": item,
            "indicator": indicator,
            "body": body,
            "icon": icon_label,
            "text": text_label,
        }
        return item

    def _hover_nav(self, page: str, entering: bool) -> None:
        parts = self._nav_buttons.get(page)
        if parts is None or page == self._current_page:
            return
        c = self.COLORS
        bg = c["sidebar_active"] if entering else c["sidebar"]
        fg = c["text"] if entering else c["muted"]
        for key in ("item", "body", "icon", "text"):
            parts[key].configure(bg=bg)
        parts["icon"].configure(fg=fg)
        parts["text"].configure(fg=fg)

    def _show_page(self, page: str) -> None:
        """선택한 본문 페이지만 표시하고 내비게이션 강조를 갱신합니다."""

        if page not in self._pages:
            return
        c = self.COLORS
        self._current_page = page
        self._pages[page].tkraise()
        for name, parts in self._nav_buttons.items():
            selected = name == page
            bg = c["sidebar_active"] if selected else c["sidebar"]
            fg = c["text"] if selected else c["muted"]
            for key in ("item", "body", "icon", "text"):
                parts[key].configure(bg=bg)
            parts["indicator"].configure(bg=c["accent"] if selected else c["sidebar"])
            parts["icon"].configure(fg=c["accent"] if selected else c["muted"])
            parts["text"].configure(fg=fg)

    def _build_ui(self) -> None:
        """뉴트럴 다크 테마의 사이드바 + 2단 레이아웃 UI를 만듭니다."""

        c = self.COLORS
        self.root.configure(bg=c["bg"])

        shell = tk.Frame(self.root, bg=c["bg"])
        shell.pack(fill=tk.BOTH, expand=True)

        sidebar = tk.Frame(shell, bg=c["sidebar"], width=214, pady=18)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)
        # 사이드바와 본문을 가르는 1px 라인(면 분리가 또렷해집니다).
        tk.Frame(shell, bg=c["border"], width=1).pack(side=tk.LEFT, fill=tk.Y)

        brand = tk.Frame(sidebar, bg=c["sidebar"])
        brand.pack(fill=tk.X, padx=16, pady=(0, 24))
        mark = tk.Canvas(
            brand,
            width=34,
            height=34,
            bg=c["sidebar"],
            highlightthickness=0,
            bd=0,
        )
        mark.pack(side=tk.LEFT)
        self._round_rect(mark, 1, 1, 33, 33, 10, fill=c["accent"], outline="")
        mark.create_text(17, 17, text="M", fill="#FFFFFF", font=self._font(13, bold=True))
        brand_text = tk.Frame(brand, bg=c["sidebar"])
        brand_text.pack(side=tk.LEFT, padx=(11, 0))
        tk.Label(
            brand_text,
            text="mAuto",
            bg=c["sidebar"],
            fg=c["text"],
            font=self._font(13, bold=True),
            anchor=tk.W,
        ).pack(anchor=tk.W)
        tk.Label(
            brand_text,
            text="FC ONLINE",
            bg=c["sidebar"],
            fg=c["muted"],
            font=self._font(7, bold=True),
            anchor=tk.W,
        ).pack(anchor=tk.W, pady=(1, 0))

        tk.Label(
            sidebar,
            text="M E N U",
            bg=c["sidebar"],
            fg=c["muted"],
            font=self._font(7, bold=True),
            anchor=tk.W,
        ).pack(fill=tk.X, padx=19, pady=(0, 7))
        self._pages: dict[str, tk.Frame] = {}
        self._nav_buttons: dict[str, dict[str, tk.Misc]] = {}
        self._current_page = ""
        nav_host = tk.Frame(sidebar, bg=c["sidebar"], padx=8)
        nav_host.pack(fill=tk.X)
        self._sidebar_button(nav_host, "◆", "운영 · 자동화", "automation")
        self._sidebar_button(nav_host, "◈", "FC 채굴 현황", "mining")

        sidebar_bottom = tk.Frame(sidebar, bg=c["sidebar"], padx=16)
        sidebar_bottom.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Frame(sidebar_bottom, bg=c["border"], height=1).pack(fill=tk.X, pady=(0, 11))
        status_line = tk.Frame(sidebar_bottom, bg=c["sidebar"])
        status_line.pack(fill=tk.X)
        self.sidebar_dot = tk.Label(
            status_line,
            text="●",
            bg=c["sidebar"],
            fg=c["muted"],
            font=self._font(8),
        )
        self.sidebar_dot.pack(side=tk.LEFT)
        tk.Label(
            status_line,
            textvariable=self.status_var,
            bg=c["sidebar"],
            fg=c["text"],
            font=self._font(9, bold=True),
            anchor=tk.W,
        ).pack(side=tk.LEFT, padx=(7, 0))
        tk.Label(
            status_line,
            text=f"v{APP_VERSION}",
            bg=c["sidebar"],
            fg=c["muted"],
            font=self._font(7),
        ).pack(side=tk.RIGHT)

        page_host = tk.Frame(shell, bg=c["bg"])
        page_host.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        page_host.grid_rowconfigure(0, weight=1)
        page_host.grid_columnconfigure(0, weight=1)
        for name in ("automation", "mining"):
            frame = tk.Frame(page_host, bg=c["bg"])
            frame.grid(row=0, column=0, sticky="nsew")
            self._pages[name] = frame

        main_frame = tk.Frame(
            self._pages["automation"],
            bg=c["bg"],
            padx=20,
            pady=18,
        )
        main_frame.pack(fill=tk.BOTH, expand=True)

        page_header = tk.Frame(main_frame, bg=c["bg"])
        page_header.pack(fill=tk.X, pady=(0, 16))
        header_text = tk.Frame(page_header, bg=c["bg"])
        header_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(
            header_text,
            text="감독모드 자동화",
            bg=c["bg"],
            fg=c["text"],
            font=self._font(20, bold=True),
            anchor=tk.W,
        ).pack(anchor=tk.W)
        tk.Label(
            header_text,
            text="게임 창을 백그라운드에 둔 채 감지와 입력을 처리합니다.",
            bg=c["bg"],
            fg=c["muted"],
            font=self._font(9),
            anchor=tk.W,
        ).pack(anchor=tk.W, pady=(5, 0))

        columns = tk.Frame(main_frame, bg=c["bg"])
        columns.pack(fill=tk.BOTH, expand=True)

        left_column = tk.Frame(columns, bg=c["bg"])
        left_column.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right_column = tk.Frame(columns, bg=c["bg"], width=452)
        right_column.pack(side=tk.LEFT, fill=tk.Y, padx=(16, 0))
        right_column.pack_propagate(False)

        # ── 왼쪽: 타겟 설정 (target_A~H, 템플릿 캡처/임계값) ──
        targets_panel = self._panel(
            left_column,
            "타겟 설정",
            subtitle="임계값이 높을수록 더 엄격하게 매칭합니다",
        )
        targets_panel.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            targets_panel,
            text="캡처: 화면에서 드래그한 영역으로 템플릿 교체   ·   기본값: 빌드 내장 이미지로 복원",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(8),
        ).pack(side=tk.BOTTOM, pady=(10, 0))

        for name in self.target_names:
            # fill=BOTH + expand: 남는 세로 공간을 행이 나눠 가져 표처럼 균일하게 보입니다.
            row = tk.Frame(targets_panel, bg=c["row"], padx=10, pady=6)
            row.pack(fill=tk.BOTH, pady=2, expand=True)

            tk.Label(
                row,
                text=name,
                bg=c["row"],
                fg=c["text"],
                width=9,
                anchor=tk.W,
                font=self._font(10, bold=True),
            ).pack(side=tk.LEFT)

            thumb_holder = tk.Frame(
                row,
                bg=c["input"],
                width=68,
                height=26,
                highlightbackground=c["border"],
                highlightthickness=1,
            )
            thumb_holder.pack_propagate(False)
            thumb_holder.pack(side=tk.LEFT, padx=(0, 12))
            thumb_label = tk.Label(thumb_holder, bg=c["input"], fg=c["muted"], font=self._font(8))
            thumb_label.pack(fill=tk.BOTH, expand=True)
            self._thumb_labels[name] = thumb_label

            self._slider(row, name).pack(side=tk.LEFT, fill=tk.X, expand=True)

            tk.Label(
                row,
                textvariable=self.threshold_label_vars[name],
                bg=c["row"],
                fg=c["accent_soft"],
                width=5,
                anchor=tk.E,
                font=("Consolas", 11, "bold"),
            ).pack(side=tk.LEFT, padx=(8, 12))

            source_var = tk.StringVar(value="기본")
            source_label = tk.Label(
                row,
                textvariable=source_var,
                bg=c["input"],
                fg=c["muted"],
                width=5,
                font=self._font(8, bold=True),
                pady=3,
            )
            source_label.pack(side=tk.LEFT)
            self._target_source_vars[name] = source_var
            self._target_source_labels[name] = source_label

            capture_button = self._button(
                row,
                "캡처",
                lambda target_name=name: self.capture_target_template(target_name),
                small=True,
            )
            capture_button.pack(side=tk.LEFT, padx=(10, 5))
            self._capture_buttons[name] = capture_button

            reset_button = self._button(
                row,
                "기본값",
                lambda target_name=name: self.reset_target_template(target_name),
                tone="ghost",
                small=True,
            )
            reset_button.pack(side=tk.LEFT)
            self._reset_buttons[name] = reset_button

        # ── 오른쪽: 세션(버전·시계·상태) / 기본 설정 / 시작·정지 ──
        control_panel = self._panel(right_column, padx=18, pady=16)
        control_panel.pack(side=tk.BOTTOM, fill=tk.X, pady=(14, 0))
        button_row = tk.Frame(control_panel, bg=c["panel"])
        button_row.pack(fill=tk.X)
        self.start_button = self._button(button_row, "시작  ·  F8", self.start_automation)
        self.start_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.stop_button = self._button(
            button_row,
            "정지  ·  F9 / ESC",
            self.stop_automation,
            tone="danger",
        )
        self.stop_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))

        settings_panel = self._panel(right_column, "기본 설정")
        settings_panel.pack(side=tk.BOTTOM, fill=tk.X, pady=(14, 0))

        # ── 기본 설정: 캡처 / 클릭 / 영역 / 자동 정지 / 등수영역% ──
        capture_row = self._setting_row(settings_panel, "캡처")
        self._segmented(
            capture_row,
            self.capture_mode_var,
            (("비활성 WGC", "wgc"), ("화면 영역 캡처", "region")),
            command=self.on_capture_mode_changed,
        ).pack(side=tk.LEFT)

        click_row = self._setting_row(settings_panel, "클릭")
        self._segmented(
            click_row,
            self.click_mode_var,
            (("PostMessage", "postmessage"), ("마우스 이동 후 복귀", "mouse")),
        ).pack(side=tk.LEFT)

        region_row = self._setting_row(settings_panel, "영역")
        for key, label in (("x", "X"), ("y", "Y"), ("width", "W"), ("height", "H")):
            self._field(region_row, label, self.region_vars[key], width=6).pack(
                side=tk.LEFT,
                padx=(0, 12),
            )

        # ── 자동 정지: 목표 등수/점수 도달 시 멈춤(둘 중 하나) 또는 안 멈춤 ──
        stop_row = self._setting_row(settings_panel, "자동 정지")
        self._segmented(
            stop_row,
            self.stop_mode_var,
            (("안 함", "off"), ("등수 이내", "rank"), ("점수 이상", "score")),
        ).pack(side=tk.LEFT)
        self._field(stop_row, "목표", self.stop_value_var, width=7).pack(side=tk.LEFT, padx=(12, 0))

        # ── 등수/점수/티어 OCR 박스(프레임 비율 %) — 그 영역만 정확히 읽어 노이즈 제거 ──
        ocr_row = self._setting_row(settings_panel, "등수영역 %")
        for key, label in (("left", "좌"), ("top", "상"), ("right", "우"), ("bottom", "하")):
            self._field(ocr_row, label, self.ocr_box_vars[key], width=5).pack(
                side=tk.LEFT,
                padx=(0, 12),
            )

        # ── 창 맞춤: 게임 창을 템플릿 기준 크기(1928x1048)로 맞춰 배율 보정 자체를 없앰 ──
        fit_row = self._setting_row(settings_panel, "게임 창")
        self.fit_button = self._button(
            fit_row,
            "기준 크기로 맞춤",
            self.fit_game_window,
            tone="ghost",
            small=True,
        )
        self.fit_button.pack(side=tk.LEFT)
        self.restore_fit_button = self._button(
            fit_row,
            "원래대로",
            self.restore_game_window,
            tone="ghost",
            small=True,
        )
        self.restore_fit_button.pack(side=tk.LEFT, padx=(6, 0))
        tk.Label(
            fit_row,
            textvariable=self.fit_status_var,
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(8),
            anchor=tk.W,
        ).pack(side=tk.LEFT, padx=(10, 0), fill=tk.X, expand=True)

        session_panel = self._panel(right_column, padx=18, pady=16)
        session_panel.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        version_row = tk.Frame(session_panel, bg=c["panel"])
        version_row.pack(fill=tk.X)
        tk.Label(
            version_row,
            text="VERSION",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(7, bold=True),
        ).pack(side=tk.LEFT, pady=(3, 0))
        tk.Label(
            version_row,
            text=f"{APP_VERSION}{'  👑' if self.license_key else ''}",
            bg=c["panel"],
            fg=c["accent_soft"],
            font=self._font(10, bold=True),
        ).pack(side=tk.RIGHT)
        tk.Frame(session_panel, bg=c["border"], height=1).pack(side=tk.TOP, fill=tk.X, pady=(11, 0))

        # 상태 줄을 먼저 아래에 고정해, 창이 낮아져도 상태가 사라지지 않게 합니다.
        status_row = tk.Frame(session_panel, bg=c["panel"])
        status_row.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Frame(session_panel, bg=c["border"], height=1).pack(
            side=tk.BOTTOM,
            fill=tk.X,
            pady=(0, 12),
        )

        clock_box = tk.Frame(session_panel, bg=c["panel"])
        clock_box.pack(side=tk.TOP, fill=tk.X, pady=(12, 8))
        tk.Label(
            clock_box,
            text="세션 진행 시간",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(8, bold=True),
        ).pack(side=tk.LEFT)
        self.clock_label = tk.Label(
            clock_box,
            textvariable=self.clock_var,
            bg=c["panel"],
            fg=c["text"],
            font=("Consolas", 21, "bold"),
        )
        self.clock_label.pack(side=tk.RIGHT)

        metrics = tk.Frame(session_panel, bg=c["panel"])
        metrics.pack(side=tk.TOP, fill=tk.X)
        metric_specs = (
            (("완료 경기", self.session_matches_var), ("승 · 무 · 패", self.session_record_var)),
            (("경기 속도", self.session_rate_var), ("예상 적립", self.session_fc_var)),
        )
        for row_index, row_specs in enumerate(metric_specs):
            row = tk.Frame(metrics, bg=c["panel"])
            row.pack(fill=tk.X, pady=(0, 7 if row_index == 0 else 0))
            for column_index, (label, variable) in enumerate(row_specs):
                card = tk.Frame(
                    row,
                    bg=c["input"],
                    highlightthickness=1,
                    highlightbackground=c["border"],
                    padx=11,
                    pady=6,
                )
                card.pack(
                    side=tk.LEFT,
                    fill=tk.X,
                    expand=True,
                    padx=(0, 4) if column_index == 0 else (4, 0),
                )
                tk.Label(
                    card,
                    text=label,
                    bg=c["input"],
                    fg=c["muted"],
                    font=self._font(7, bold=True),
                ).pack(anchor="w")
                tk.Label(
                    card,
                    textvariable=variable,
                    bg=c["input"],
                    fg=c["text"],
                    font=self._font(9, bold=True),
                ).pack(anchor="w", pady=(2, 0))
        tk.Label(
            status_row,
            text="상태",
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(9, bold=True),
        ).pack(side=tk.LEFT)
        self.status_label = tk.Label(
            status_row,
            textvariable=self.status_var,
            bg=c["input"],
            fg=c["muted"],
            font=self._font(9, bold=True),
            padx=11,
            pady=4,
        )
        self.status_label.pack(side=tk.RIGHT)

        self._refresh_all_target_rows()
        self._paint_status(self.status_var.get())

        self.mining_dashboard = MiningDashboard(
            self._pages["mining"],
            base_dir=self.base_dir,
            colors=c,
            font_factory=lambda size, bold=False: self._font(size, bold),
            automation_running=lambda: bool(
                self.worker_thread is not None and self.worker_thread.is_alive()
            ),
            logger=self.queue_log,
        )
        self.mining_dashboard.pack(fill=tk.BOTH, expand=True, padx=18, pady=16)
        self._show_page("automation")

    def _tick_clock(self) -> None:
        """오른쪽 패널의 세션 경과 시간과 처리량을 1초마다 갱신합니다."""

        self._refresh_session_ui()
        if not self.closing:
            self.root.after(1000, self._tick_clock)

    def _refresh_session_ui(self) -> None:
        snapshot = self._session_tracker.snapshot()
        seconds = max(0, int(snapshot.elapsed_seconds))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        self.clock_var.set(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        self.session_matches_var.set(f"{snapshot.matches:,}")
        record = f"{snapshot.wins}승 · {snapshot.draws}무 · {snapshot.losses}패"
        if snapshot.unknown:
            record += f" · ?{snapshot.unknown}"
        self.session_record_var.set(record)
        self.session_rate_var.set(f"{snapshot.matches_per_hour:.1f} 경기/h")
        self.session_fc_var.set(
            f"{snapshot.estimated_fc:,} FC · {snapshot.fc_per_hour:.1f}/h"
        )

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

    # ── 게임 창 맞춤 ──

    def fit_game_window(self) -> None:
        """게임 창을 템플릿 기준 WGC 프레임 크기로 맞춥니다(전면화 없음).

        기준 크기가 되면 해상도 보정 배율이 1.0이 되어 리샘플링 없이 원본 그대로
        매칭합니다. 2560 같은 해상도에서도 보정으로 동작하지만, 맞추면 점수가 더 높습니다.
        """

        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.log("[창 맞춤] 자동화 실행 중에는 창 크기를 바꿀 수 없습니다. 정지 후 다시 시도하세요.")
            self.fit_status_var.set("실행 중에는 불가")
            return

        self.fit_status_var.set("맞추는 중...")
        self.root.update_idletasks()

        manager = InactiveManager(self.window_title_var.get(), logger=self.log)
        if not manager.find_window():
            self.fit_status_var.set("게임 창을 찾지 못했습니다")
            self.log("[창 맞춤] 대상 창을 찾지 못했습니다. 게임이 실행 중인지 확인하세요.")
            return

        def measure() -> Optional[tuple[int, int]]:
            # 크기를 바꾸면 새 캡처 세션이 필요하므로 매번 새로 열어 측정합니다.
            manager.stop_capture()
            for _ in range(60):
                if manager.capture_client_area(window_validated=True) is not None:
                    return manager.capture_engine.get_frame_size()
                time.sleep(0.1)
            return None

        try:
            result = window_fit.fit_window_to_reference(
                manager.hwnd,
                measure,
                logger=self.log,
            )
        except Exception as exc:  # noqa: BLE001 - UI 버튼에서 예외가 새면 안 됩니다.
            self.fit_status_var.set("맞춤 실패")
            self.log(f"[창 맞춤] 예기치 못한 오류: {exc}")
            return
        finally:
            manager.stop_capture()

        self.log(f"[창 맞춤] {result.message}")
        if result.ok and result.changed:
            self._fit_snapshot = result.snapshot
            self.fit_status_var.set(f"{result.after[0]}x{result.after[1]} 맞춤 완료")
        elif result.ok:
            self.fit_status_var.set(f"이미 기준 크기 ({result.before[0]}x{result.before[1]})")
        else:
            self.fit_status_var.set("맞춤 실패 — 로그 확인")

    def restore_game_window(self) -> None:
        """창 맞춤 이전 크기·위치로 되돌립니다."""

        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.log("[창 맞춤] 자동화 실행 중에는 창 크기를 바꿀 수 없습니다.")
            return
        if self._fit_snapshot is None:
            self.fit_status_var.set("되돌릴 기록이 없습니다")
            return

        try:
            restored = window_fit.restore_window_no_activate(self._fit_snapshot)
        except Exception as exc:  # noqa: BLE001
            self.log(f"[창 맞춤] 복원 실패: {exc}")
            self.fit_status_var.set("복원 실패")
            return

        self._fit_snapshot = None
        self.fit_status_var.set(f"원래 크기로 복원 ({restored.width}x{restored.height})")
        self.log(f"[창 맞춤] 원래 크기로 되돌렸습니다 ({restored.width}x{restored.height}).")

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
            # 커스텀은 초록 알약, 기본은 조용한 회색 알약으로 한눈에 구분합니다.
            source_label.configure(
                fg=c["ok"] if is_custom else c["muted"],
                bg=c["ok_bg"] if is_custom else c["input"],
            )
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
        self._session_tracker.start()
        self._refresh_session_ui()
        self._set_button_state(running=True)
        self.set_status("실행 중")
        self.log(
            f"[시작] 자동화를 시작합니다. "
            f"캡처={capture_mode}, 클릭={click_mode}, 영역={region}, 창='{window_title}'"
        )
        if self._skip_learned_profiles:
            restored = ", ".join(
                f"{variant.upper()}={candidate.upper()}"
                for variant, candidate in sorted(
                    self._skip_learned_profiles.items()
                )
            )
            self.log(f"[SKIP 학습] 이 기기 우선 후보 복원: {restored}")

        self.worker_thread = threading.Thread(
            target=self._automation_loop,
            args=(window_title, capture_mode, click_mode, region),
            daemon=True,
        )
        self.worker_thread.start()

    def update_threshold(self, target_name: str, value: float | str) -> None:
        """슬라이더 값 변경을 thread-safe하게 저장합니다."""

        threshold = max(0.0, min(1.0, float(value)))
        with self.threshold_lock:
            self.threshold_values[target_name] = threshold
            self._threshold_revision += 1
        self.threshold_label_vars[target_name].set(f"{threshold:.2f}")
        self._draw_slider(target_name)

    def get_threshold(self, target_name: str) -> float:
        """작업 스레드에서 사용할 현재 임계값을 가져옵니다."""

        with self.threshold_lock:
            return float(self.threshold_values.get(target_name, 0.8))

    def apply_current_thresholds(
        self,
        targets: list[TargetImage],
        applied_revision: int = -1,
    ) -> int:
        """변경된 경우에만 UI 임계값을 타겟 목록에 한 번에 반영합니다."""

        with self.threshold_lock:
            revision = self._threshold_revision
            if revision == applied_revision:
                return revision
            values = self.threshold_values.copy()
        for target in targets:
            target.threshold = float(values.get(target.name, target.threshold))
        return revision

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
        self._set_accent_button_state(self.stop_button, enabled=False)

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
        mining_dashboard = getattr(self, "mining_dashboard", None)
        if mining_dashboard is not None:
            mining_dashboard.close()
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
        with self._log_lock:
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
            self._rank_ocr_cache_key = None
            self._rank_ocr_cache_info = None
            self._rank_ocr_cache_at = 0.0
            self._rank_ocr_cache_hits = 0
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
            self._skip_diag_count = 0
            self._skip_fg_last_at = 0.0
            self._skip_experiment = self._new_skip_experiment_tracker()
            self._skip_s_experiment = self._new_skip_s_experiment_tracker()
            self._skip_prompt_variant = None
            self._skip_prompt_center = None
            self._notification_check_at = 0.0
            self._notification_streak = 0
            self._notification_visible = False
            self._notification_last_action_at = 0.0
            self._stop_confirm_count = 0
            rank_ocr.reset_skip_a_template()  # 커스텀 (A) SKIP 교체 가능성 → 캐시 새로고침
            if (RANK_OCR_ENABLED or SKIP_ENABLED) and rank_ocr.ocr_available():
                self._ocr_thread = threading.Thread(target=self._ocr_worker_loop, daemon=True)
                self._ocr_thread.start()

            # 상태 전송 타이머
            last_status_report = 0.0
            applied_threshold_revision = -1
            last_window_validation = float("-inf")
            window_is_valid = False

            # 시작 상태 전송 (단일 워커 풀)
            self._report_status(running=True, message="매크로 시작")

            while not self.stop_event.is_set():
                # 주기적 상태 전송
                now_mono = time.monotonic()
                if self.license_key and now_mono - last_status_report >= STATUS_REPORT_INTERVAL_SECONDS:
                    last_status_report = now_mono
                    self._report_status(running=True)
                if applied_threshold_revision != self._threshold_revision:
                    applied_threshold_revision = self.apply_current_thresholds(
                        targets, applied_threshold_revision
                    )

                if requires_window and manager is not None:
                    needs_validation = (
                        manager.hwnd is None
                        or now_mono - last_window_validation
                        >= WINDOW_VALIDATION_INTERVAL_SECONDS
                    )
                    if needs_validation:
                        last_window_validation = now_mono
                        window_is_valid = manager.is_valid_window()
                    if not window_is_valid:
                        self.queue_status("대상 창 검색 중")
                        window_is_valid = manager.find_window()
                        last_window_validation = time.monotonic()

                        if self.stop_event.is_set():
                            break

                        if not window_is_valid:
                            self.queue_log(f"[대기] {WINDOW_RETRY_SECONDS}초 후 창을 다시 찾습니다.")
                            self.interruptible_sleep(WINDOW_RETRY_SECONDS)
                            continue

                self.queue_status("실행 중")
                if capture_mode == "region":
                    screen_gray = self.capture_screen_region(region)
                elif manager is not None:
                    screen_gray = manager.capture_client_area(window_validated=True)
                else:
                    self.queue_log("[오류] 캡처 관리자가 준비되지 않았습니다.")
                    screen_gray = None

                if screen_gray is None:
                    # WGC는 새 프레임 이벤트를 이미 기다렸으므로 추가 polling sleep이 필요 없습니다.
                    if capture_mode != "wgc":
                        self.interruptible_sleep(LOOP_SLEEP_SECONDS)
                    continue

                # 일부 PC에서 좌표 오차로 하단 종 버튼이 눌려 알림 패널이 열리면 모든 타겟을
                # 가립니다. 패널이 보이는 동안 일반 매칭/OCR을 멈추고 종 버튼을 다시 눌러 닫습니다.
                if self._try_close_notification_panel(
                    screen_gray,
                    manager,
                    click_mode,
                    capture_mode,
                    region,
                    now_mono,
                ):
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
            if self._rank_ocr_cache_hits:
                self._log_to_file_only(
                    f"[성능] 동일 OCR 결과 재사용 {self._rank_ocr_cache_hits:,}회"
                )
            if manager is not None:
                manager.stop_capture()
            self.queue_status("종료됨")
            self.queue_log("[종료] 자동화 루프가 종료되었습니다.")
            self._session_tracker.stop()
            self.ui_queue.put(("session", ""))
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
            l, t, r, b = self._ocr_box
            x1 = max(0, int(w * l))
            x2 = min(w, int(w * r))
            y1 = max(0, int(h * t))
            y2 = min(h, int(h * b))
            if x2 - x1 < 2 or y2 - y1 < 2:
                return
            crop = screen_gray[y1:y2, x1:x2]

            # 매치 화면 게이트: '팀 정보/유니폼 선택' 탭이 안 보이면(구단 관리 등 다른 화면)
            # 등수 OCR을 건너뛰고 '패널 없음'으로 투표 → 오인식 차단 + 패널 소멸 추적.
            gate_crop = np.empty((0, 0), dtype=np.uint8)
            if MATCH_GATE_ENABLED:
                gx1 = max(0, int(w * MATCH_GATE_LEFT_FRACTION))
                gx2 = min(w, int(w * MATCH_GATE_RIGHT_FRACTION))
                gy1 = max(0, int(h * MATCH_GATE_TOP_FRACTION))
                gy2 = min(h, int(h * MATCH_GATE_BOTTOM_FRACTION))
                gate_crop = screen_gray[gy1:gy2, gx1:gx2]

            cache_key = _ocr_regions_fingerprint(gate_crop, crop)
            if (
                cache_key == self._rank_ocr_cache_key
                and self._rank_ocr_cache_info is not None
                and now - self._rank_ocr_cache_at <= RANK_OCR_CACHE_SECONDS
            ):
                self._rank_ocr_cache_hits += 1
                self._rank_consensus.observe(dict(self._rank_ocr_cache_info), now)
                return

            if MATCH_GATE_ENABLED:
                if gate_crop.size == 0 or not rank_ocr.on_match_screen(gate_crop, MATCH_GATE_TOKENS):
                    info = {
                        "rank": None,
                        "is_champion": False,
                        "tier": None,
                        "score": None,
                        "has_panel": False,
                    }
                    self._rank_ocr_cache_key = cache_key
                    self._rank_ocr_cache_info = dict(info)
                    self._rank_ocr_cache_at = now
                    self._rank_consensus.observe(info, now)
                    # 추적성(#10): 매치 화면이 맞는데도 탭바를 못 읽으면(해상도/DPI 어긋남)
                    # 등수가 영영 안 잡힌다. 일반 화면에선 정상이라 파일 로그로 5초마다만 남긴다.
                    if now - self._gate_fail_log_at >= 5.0:
                        self._gate_fail_log_at = now
                        self._log_to_file_only(
                            "[게이트] 탭바('팀정보/유니폼') 미검출 → 등수 OCR 건너뜀"
                            "(매치 화면인데 반복되면 해상도/게이트 영역 확인)"
                        )
                    return
            info = rank_ocr.read_rank_panel(crop, logger=None)
            self._rank_ocr_cache_key = cache_key
            self._rank_ocr_cache_info = dict(info)
            self._rank_ocr_cache_at = now
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
        self._record_session_result(score, tier, now)
        # 컨센서스로 '확정된' 값에서만 목표 도달을 판정 → 단발 오인식/스테일로 잘못 멈추지 않음.
        # 정지 판정엔 '이번 패널에서 실제로 읽힌' 티어만 넘긴다(없으면 None) → 직전 매치의
        # 슈퍼챔스 티어가 다음 매치로 새서 거짓 정지하는 일을 막는다(#5). 표시용 보강은 아래에서.
        self._maybe_stop_on_target(rank, score, tier)

        # 마지막 값 유지: 이번에 '읽힌' 필드만 갱신한다.
        # 다만 새 결과 패널에서 등수/점수는 읽혔는데 티어만 없으면 이전 매치 티어를
        # 그대로 표시하지 않는다. 새 티어 OCR 실패는 빈 값으로 처리하는 편이 안전하다.
        if rank is not None:
            self._last_rank = rank
        if tier is not None:
            self._last_tier = tier
        elif rank is not None or score is not None:
            self._last_tier = None
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

    def _record_session_result(
        self,
        score: Optional[int],
        tier: Optional[str],
        now: float,
    ) -> None:
        """같은 결과 패널의 중복 OCR을 제외하고 세션 통계를 갱신합니다."""

        tracker = getattr(self, "_session_tracker", None)
        if tracker is None:
            return
        outcome = tracker.record_result(score, tier, now=now)
        if outcome is None:
            return
        outcome_label = {
            "win": "승",
            "draw": "무",
            "loss": "패",
            "unknown": "미판정",
        }.get(outcome, outcome)
        self.queue_log(f"[세션] 경기 결과 반영: {outcome_label}")
        self.ui_queue.put(("session", ""))

    def _new_skip_experiment_tracker(self) -> SkipExperimentTracker:
        return SkipExperimentTracker(
            SKIP_INACTIVE_EXPERIMENT_CANDIDATES,
            result_window_seconds=SKIP_EXPERIMENT_RESULT_WINDOW_SECONDS,
            attempt_gap_seconds=SKIP_EXPERIMENT_ATTEMPT_GAP_SECONDS,
            confirm_successes=SKIP_EXPERIMENT_CONFIRM_SUCCESSES,
            control_seconds=SKIP_EXPERIMENT_CONTROL_SECONDS,
            exit_confirm_seconds=SKIP_EXPERIMENT_EXIT_CONFIRM_SECONDS,
            progressive_control=SKIP_EXPERIMENT_PROGRESSIVE_CONTROL,
            control_ramp_successes=SKIP_EXPERIMENT_CONTROL_RAMP_SUCCESSES,
            learned=(
                self._skip_learned_profiles.get("a")
                or (SKIP_A_BUTTON or "").strip().lower()
                or None
            ),
        )

    def _new_skip_s_experiment_tracker(self) -> SkipExperimentTracker:
        return SkipExperimentTracker(
            SKIP_S_INACTIVE_EXPERIMENT_CANDIDATES,
            result_window_seconds=SKIP_EXPERIMENT_RESULT_WINDOW_SECONDS,
            attempt_gap_seconds=SKIP_EXPERIMENT_ATTEMPT_GAP_SECONDS,
            confirm_successes=SKIP_EXPERIMENT_CONFIRM_SUCCESSES,
            control_seconds=SKIP_EXPERIMENT_CONTROL_SECONDS,
            exit_confirm_seconds=SKIP_EXPERIMENT_EXIT_CONFIRM_SECONDS,
            progressive_control=SKIP_EXPERIMENT_PROGRESSIVE_CONTROL,
            control_ramp_successes=SKIP_EXPERIMENT_CONTROL_RAMP_SUCCESSES,
            learned=self._skip_learned_profiles.get("s"),
        )

    def _save_skip_learning(self, variant: str, candidate: str) -> None:
        if save_device_learning(
            self._skip_learning_path,
            self._skip_device_id,
            variant,
            candidate,
        ):
            self._skip_learned_profiles[variant] = candidate

    def _reconcile_skip_learning(
        self,
        variant: str,
        experiment: SkipExperimentTracker,
    ) -> None:
        """Remove a persisted winner immediately after runtime invalidation."""

        persisted = self._skip_learned_profiles.get(variant)
        if not persisted or experiment.learned == persisted:
            return
        if remove_device_learning(
            self._skip_learning_path,
            self._skip_device_id,
            variant,
        ):
            self._skip_learned_profiles.pop(variant, None)

    def _write_skip_experiment_event(
        self,
        outcome: SkipOutcome,
        *,
        guard=None,
        variant: Optional[str] = None,
        control_seconds: Optional[float] = None,
    ) -> None:
        """Append one machine-readable SKIP experiment result without touching UI."""

        event = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "device_id": self._skip_device_id,
            "variant": variant,
            "status": outcome.status,
            "candidate": outcome.candidate,
            "latency_seconds": outcome.latency_seconds,
            "learned": outcome.learned,
            "detail": outcome.detail,
            "control_seconds": (
                SKIP_EXPERIMENT_CONTROL_SECONDS
                if control_seconds is None else float(control_seconds)
            ),
            "control_policy": (
                "progressive"
                if SKIP_EXPERIMENT_PROGRESSIVE_CONTROL else "fixed"
            ),
            "control_result": (
                "exited_before_input"
                if outcome.detail == "opponent_or_natural_exit_during_control"
                else "survived_to_input"
                if (
                    (guard is not None and guard.attempted)
                    or (
                        outcome.candidate is not None
                        and outcome.status in {
                            "pending",
                            "success",
                            "failed",
                            "quarantined",
                        }
                    )
                )
                else None
            ),
        }
        if guard is not None:
            event["guard"] = {
                "attempted": guard.attempted,
                "action_ok": guard.action_ok,
                "foreground_before": guard.foreground_before,
                "foreground_after": guard.foreground_after,
                "foreground_samples": list(guard.foreground_samples),
                "invariant_ok": guard.invariant_ok,
                "reason": guard.reason,
                "elapsed_seconds": guard.elapsed_seconds,
            }
        try:
            log_dir = self.base_dir / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(
                log_dir / SKIP_EXPERIMENT_LOG_FILENAME,
                "a",
                encoding="utf-8",
            ) as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _report_skip_experiment_outcome(
        self,
        outcome: Optional[SkipOutcome],
        *,
        guard=None,
        variant: Optional[str] = None,
        control_seconds: Optional[float] = None,
    ) -> None:
        if outcome is None:
            return
        self._write_skip_experiment_event(
            outcome,
            guard=guard,
            variant=variant,
            control_seconds=control_seconds,
        )
        candidate = (outcome.candidate or "-").upper()
        if outcome.status == "success":
            suffix = f", {outcome.latency_seconds:.2f}초" if outcome.latency_seconds is not None else ""
            self.queue_log(f"[SKIP 실험] {candidate} 성공 후보{suffix} ({outcome.detail})")
            if outcome.learned:
                self.queue_log(
                    f"[SKIP 실험] {candidate} {SKIP_EXPERIMENT_CONFIRM_SUCCESSES}회 확인 → 우선 입력으로 학습"
                )
        elif outcome.status == "quarantined":
            self.queue_log(
                f"[SKIP 실험] {candidate} 전면창 불변 위반 → 세션에서 격리 ({outcome.detail})"
            )
        elif outcome.status == "failed":
            self.queue_log(f"[SKIP 실험] {candidate} 실패 → 다음 후보 ({outcome.detail})")
        elif outcome.status == "blocked":
            self.queue_log("[SKIP 실험] 안전 후보가 모두 격리됨 → 입력 없이 자연 종료 대기")
        elif (
            outcome.status == "unattributed"
            and outcome.detail == "opponent_or_natural_exit_during_control"
        ):
            self.queue_log(
                "[SKIP 대조] 입력 전에 종료됨 → 상대 스킵/자연 종료로 분류, 학습 제외"
            )

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

    @staticmethod
    def _press_skip_candidate(
        name: str,
        manager: InactiveManager,
        prompt_center: Optional[tuple[int, int]] = None,
    ) -> bool:
        """(A) SKIP 후보 입력 하나를 실행합니다(전부 비활성 유지 방식 — 화면 변화 0).

        키보드 S, 게임패드 A와 사용자가 요청한 Start 경로를 안전 후보로 탐색한다.
        전달 경로 분기와 홀드 변형을 깔끔히 다루려고
        '<방법>_<키>'(vk 기반)로 일반화해 둔다.
        - 키보드 '<방법>_s' (딥 메시지 — 게임이 메시지 계층=CEF로 s를 읽을 때 배경 통과):
          · "pm_s":           WM_KEYDOWN 딥 전송(최상위+포커스 자식+모든 자식 창)
          · "char_s":         WM_KEYDOWN+WM_CHAR+WM_KEYUP 딥(CEF UI가 WM_CHAR로 받음)
          · "attach_state_s": AttachThreadInput+SetKeyboardState(GetKeyState 폴링 속이기)
          · "attach_post_s":  AttachThreadInput(큐 공유)+포커스창 WM_KEYDOWN/UP
        - 스캔코드 전용('s' 고정): "focus_child_s", "focus_s"(깜빡임), "si_s"(유출)
        - "a"/"start": 가상패드 버튼 / "lt"/"rt": 트리거
        - "*_hold": 위 입력을 1초 홀드(hold-to-skip 대응 — 탭으론 원리상 못 넘김)
        """
        # '*_hold' 후보 = 같은 버튼을 길게 홀드(hold-to-skip 프롬프트 대응).
        # 이름에서 '_hold'를 떼어 실제 버튼을 얻고, 탭 대신 홀드 길이를 쓴다.
        hold_secs = SKIP_A_TAP_SECONDS
        if name.endswith("_hold"):
            name = name[:-len("_hold")]
            hold_secs = SKIP_A_SWEEP_HOLD_SECONDS
        try:
            if name == "click_prompt":
                if prompt_center is None:
                    return False
                end_x, end_y = prompt_center
                start_x, start_y = manager.get_virtual_start_position(
                    end_x,
                    end_y,
                )
                return manager.post_curved_click(
                    start_x,
                    start_y,
                    end_x,
                    end_y,
                )
            if name in {"spoof_a", "spoof_start"}:
                if not manager.hwnd:
                    return False
                gamepad_name = name.removeprefix("spoof_")
                button = input_gamepad.KEY_TO_GAMEPAD.get(gamepad_name)
                if button is None:
                    return False
                spoofed = input_message.spoof_window_active(manager.hwnd, True)
                try:
                    if not spoofed:
                        return False
                    return send_gamepad_button(button, press_delay=hold_secs)
                finally:
                    input_message.spoof_window_active(manager.hwnd, False)
            if name in {"spoof_char_s", "spoof_pm_s"}:
                if not manager.hwnd:
                    return False
                vk = input_message.KEY_TO_VK.get("s")
                if not vk:
                    return False
                spoofed = input_message.spoof_window_active(
                    manager.hwnd,
                    True,
                )
                try:
                    if not spoofed:
                        return False
                    if name == "spoof_char_s":
                        return input_message.post_char_deep(
                            manager.hwnd,
                            vk,
                            ord("s"),
                            press_delay=hold_secs,
                        )
                    return input_message.post_key_deep(
                        manager.hwnd,
                        vk,
                        press_delay=hold_secs,
                    )
                finally:
                    input_message.spoof_window_active(
                        manager.hwnd,
                        False,
                    )
            if name in {"sync_char_s", "sync_pm_s"}:
                if not manager.hwnd:
                    return False
                vk = input_message.KEY_TO_VK.get("s")
                if not vk:
                    return False
                return input_message.send_key_deep_sync(
                    manager.hwnd,
                    vk,
                    char_code=ord("s") if name == "sync_char_s" else None,
                    press_delay=hold_secs,
                )
            # 스캔코드 전용(아무 키 일반화 불가 — 's' 고정).
            if name == "focus_child_s":
                if manager.hwnd:
                    return input_message.send_key_focus_child(
                        manager.hwnd, input_message.SCAN_S, hold=hold_secs)
                return False
            if name == "focus_s":
                if manager.hwnd:
                    return input_message.send_key_focus_attach(
                        manager.hwnd, input_message.SCAN_S, hold=hold_secs)
                return False
            if name == "si_s":
                return input_message.send_scancode_key(
                    input_message.SCAN_S, hold=hold_secs)
            # 일반화 키보드: '<방법>_<키>'(vk 기반). 스캔코드 분기를 안 탄 키보드 후보 처리.
            # 현재 키는 s만 사용(스킵=s 확정) — 파싱 일반화는 홀드/딜리버리 분기 정리용.
            if "_" in name:
                method, key = name.rsplit("_", 1)
                vk = input_message.KEY_TO_VK.get(key)
                if vk and manager.hwnd:
                    if method == "pm":
                        return input_message.post_key_deep(
                            manager.hwnd, vk, press_delay=hold_secs)
                    if method == "char":
                        char_code = {"enter": 0x0D, "return": 0x0D,
                                     "space": 0x20, "tab": 0x09}.get(
                            key, ord(key) if len(key) == 1 else 0)
                        if char_code:
                            return input_message.post_char_deep(
                                manager.hwnd, vk, char_code, press_delay=hold_secs)
                        return False
                    if method == "attach_state":
                        return input_message.send_key_attach_state(
                            manager.hwnd, vk, hold=hold_secs)
                    if method == "attach_post":
                        return input_message.send_key_attach_post(
                            manager.hwnd, vk, hold=hold_secs)
            if name in input_gamepad.TRIGGER_KEYS:
                send_gamepad_trigger(name, press_delay=hold_secs)
                return True
            btn = input_gamepad.KEY_TO_GAMEPAD.get(name)
            if btn is None:
                return False
            send_gamepad_button(btn, press_delay=hold_secs)
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
            is_a, skip_a_score, a_center = rank_ocr.match_skip_a(crop, self.base_dir)
            is_s, skip_s_score, s_center = rank_ocr.match_skip_s(crop, self.base_dir)
            has_text = False
            if not is_a and not is_s:
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

        if not is_a and not is_s and not has_text:
            # 스킵 없음 → 에피소드 종료. 엄격 실험 모드에서는 직전 입력과 소멸 시각을
            # 연결하고, 같은 후보가 반복 확인된 뒤에만 학습한다. 자연 종료는 귀속하지 않는다.
            if self._skip_kind == "a" and SKIP_STRICT_INACTIVE_EXPERIMENT:
                variant = "s" if self._skip_prompt_variant == "s" else "a"
                experiment = (
                    self._skip_s_experiment
                    if variant == "s"
                    else self._skip_experiment
                )
                episode_control_seconds = experiment.episode_control_seconds
                outcome = experiment.prompt_disappeared(time.monotonic())
                if outcome is None:
                    # Keep the episode/candidate attached while absence is only
                    # tentative. A reappearing prompt cancels it in choose().
                    self._skip_active_until = time.monotonic() + 0.3
                    return True
                self._report_skip_experiment_outcome(
                    outcome,
                    variant=variant,
                    control_seconds=episode_control_seconds,
                )
                self._reconcile_skip_learning(variant, experiment)
                if outcome.learned:
                    self._save_skip_learning(variant, outcome.learned)
                    if variant == "a":
                        self._skip_a_learned = outcome.learned
                        self._skip_learned_fail = 0
            elif SKIP_STRICT_INACTIVE_EXPERIMENT and (
                self._skip_experiment.pending is not None
                or self._skip_s_experiment.pending is not None
            ):
                # 일반 Start 에피소드 안전망에서 실행된 입력을 다음 A 에피소드에 귀속하지 않는다.
                self._skip_experiment.reset_episode()
                self._skip_s_experiment.reset_episode()
            elif self._skip_kind == "a" and self._skip_last_press is not None:
                # 기존 호환 모드: 마지막 입력 직후 사라졌으면 바로 학습.
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
            self._skip_prompt_variant = None
            self._skip_prompt_center = None
            self._skip_last_press = None
            self._skip_a_dumped = False
            self._skip_a_sweep_idx = 0   # 다음 에피소드는 처음부터(학습됐으면 학습값 우선)
            return False

        now = time.monotonic()
        if self._skip_seen_since is None:
            self._skip_seen_since = now
            if has_text and not is_a and not is_s:
                self.queue_log(
                    f"[SKIP 진단] OCR 감지, A={skip_a_score:.3f}/"
                    f"{SKIP_A_MATCH_THRESHOLD:.3f}, S={skip_s_score:.3f}/"
                    f"{SKIP_S_MATCH_THRESHOLD:.3f}"
                )
        if (has_text and not is_a and not is_s and not self._skip_a_dumped
                and self._skip_diag_count < 20):
            self._skip_a_dumped = True
            self._skip_diag_count += 1
            try:
                diag_dir = self.base_dir / "logs" / "skip_diagnostics"
                diag_dir.mkdir(parents=True, exist_ok=True)
                diag_path = diag_dir / (
                    f"skip_text_{time.strftime('%Y%m%d_%H%M%S')}_"
                    f"{self._skip_diag_count:02d}_{skip_a_score:.3f}.png"
                )
                if cv2.imwrite(str(diag_path), crop):
                    self.queue_log(f"[SKIP 진단] 하단 프레임 저장: {diag_path.name}")
            except Exception:
                pass
        # 감지 중엔 매칭을 잠시 멈춰 스킵 화면에서의 오클릭을 막는다(입력 여부와 무관).
        self._skip_active_until = now + 0.5

        press_a = press_start = False
        if is_a or is_s or self._skip_kind == "a":
            # A/S hold형: 템플릿이 맞았거나 이미 이 에피소드가 hold형으로 확정된 경우.
            # → 움직이는 배경(리플레이) 위에서 템플릿이 프레임마다 깜빡여도 A를 '매 주기' 누른다.
            # hold형 자체는 sticky지만, 실제 장치 입력 전환으로 고신뢰 템플릿이 S↔A로
            # 바뀌면 변형은 갱신한다. 이전 입력 뒤의 표시 전환을 성공으로 귀속하지 않는다.
            detected_variant = "a" if is_a else "s" if is_s else None
            detected_center = a_center if is_a else s_center if is_s else None
            if detected_center is not None:
                self._skip_prompt_center = (
                    x1 + int(detected_center[0]),
                    y1 + int(detected_center[1]),
                )
            if (
                detected_variant is not None
                and detected_variant != self._skip_prompt_variant
            ):
                previous_variant = self._skip_prompt_variant
                if previous_variant == "a":
                    self._skip_experiment.reset_episode()
                elif previous_variant == "s":
                    self._skip_s_experiment.reset_episode()
                self._skip_prompt_variant = detected_variant
                if previous_variant is not None:
                    self.queue_log(
                        f"[SKIP 실험] 표시 전환 "
                        f"{previous_variant.upper()}→{detected_variant.upper()} "
                        "확인 → 이전 입력 귀속 취소"
                    )
            self._skip_kind = "a"
            self._skip_text_streak = 0
            press_a = True
            kind = (self._skip_prompt_variant or "A").upper()
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
            if (not SKIP_STRICT_INACTIVE_EXPERIMENT
                    and manager.hwnd and winapi.win32gui is not None):
                WM_ACTIVATE = 0x0006
                WA_ACTIVE = 1
                winapi.win32gui.PostMessage(manager.hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
            start_btn = input_gamepad.KEY_TO_GAMEPAD.get("start")
            action_label = "입력"
            if press_a:
                if SKIP_STRICT_INACTIVE_EXPERIMENT and self._skip_kind == "a":
                    variant = (
                        "s" if self._skip_prompt_variant == "s" else "a"
                    )
                    experiment = (
                        self._skip_s_experiment
                        if variant == "s"
                        else self._skip_experiment
                    )
                    candidate, expired = experiment.choose(now)
                    self._report_skip_experiment_outcome(
                        expired,
                        variant=variant,
                        control_seconds=experiment.episode_control_seconds,
                    )
                    self._reconcile_skip_learning(variant, experiment)
                    if candidate:
                        guard = run_guarded_inactive_action(
                            lambda: self._press_skip_candidate(
                                candidate,
                                manager,
                                self._skip_prompt_center,
                            ),
                            input_message.get_foreground_hwnd,
                            manager.hwnd,
                            poll_seconds=SKIP_EXPERIMENT_FOREGROUND_POLL_SECONDS,
                        )
                        outcome = experiment.record_attempt(
                            candidate,
                            time.monotonic(),
                            guard,
                        )
                        self._report_skip_experiment_outcome(
                            outcome,
                            guard=guard,
                            variant=variant,
                            control_seconds=experiment.episode_control_seconds,
                        )
                        self._reconcile_skip_learning(variant, experiment)
                        action_label = (
                            f"비활성 {kind} 실험 {candidate.upper()}"
                            if guard.attempted
                            else f"비활성 실험 보류({guard.reason})"
                        )
                    elif expired is None:
                        action_label = "비활성 실험 결과 대기"
                    self._skip_active_until = time.monotonic() + 0.3
                elif SKIP_STRICT_INACTIVE_EXPERIMENT:
                    # 일반 Start 스킵의 A 안전망은 후보 학습에서 제외합니다. 전면창 감시만
                    # 적용한 단순 A 홀드로 오분류 화면이 갇히는 것만 방지합니다.
                    a_btn = input_gamepad.KEY_TO_GAMEPAD.get("a")
                    guard = run_guarded_inactive_action(
                        lambda: (
                            send_gamepad_button(
                                a_btn,
                                press_delay=SKIP_A_SWEEP_HOLD_SECONDS,
                            )
                            if a_btn is not None else False
                        ),
                        input_message.get_foreground_hwnd,
                        manager.hwnd,
                        poll_seconds=SKIP_EXPERIMENT_FOREGROUND_POLL_SECONDS,
                    )
                    action_label = (
                        "Start 안전망 A홀드(비활성 유지)"
                        if guard.invariant_ok and guard.action_ok
                        else f"Start 안전망 보류({guard.reason})"
                    )
                else:
                    # 호환 모드: 전면화 폴백을 명시적으로 켠 기존 설치만 이 경로를 사용합니다.
                    a_btn = input_gamepad.KEY_TO_GAMEPAD.get("a")
                    elapsed = now - self._skip_seen_since
                    want_fg = (SKIP_A_FOREGROUND and manager.hwnd and a_btn is not None
                               and elapsed >= SKIP_A_FOREGROUND_AFTER_SECONDS)
                    if want_fg and now - self._skip_fg_last_at >= SKIP_A_FG_COOLDOWN_SECONDS:
                    # 폴백(옵트인): 스푸핑으로 안 넘어감 → 잠깐 전면화+A홀드+복원.
                    # 컷신당 1번(쿨다운). ALT트릭은 bring_foreground 안에 있음.
                        self._skip_fg_last_at = now
                        prev = input_message.get_foreground_hwnd()
                        if input_message.bring_foreground(manager.hwnd):
                            time.sleep(0.10)
                            send_gamepad_button(a_btn, press_delay=SKIP_A_HOLD_SECONDS)
                            if prev and prev != int(manager.hwnd):
                                input_message.bring_foreground(prev)
                            action_label = "전면화+A홀드"
                        else:
                            action_label = "전면화 실패"
                        self._skip_active_until = time.monotonic() + 0.5
                    elif SKIP_A_ACTIVATE_SPOOF and manager.hwnd and a_btn is not None:
                        if now - self._skip_fg_last_at >= SKIP_A_HOLD_SECONDS + 0.3:
                            self._skip_fg_last_at = now
                            spoofed = input_message.spoof_window_active(manager.hwnd, True)
                            try:
                                if spoofed:
                                    send_gamepad_button(a_btn, press_delay=SKIP_A_HOLD_SECONDS)
                                    still_inactive = (
                                        input_message.get_foreground_hwnd() != int(manager.hwnd)
                                    )
                                    action_label = (
                                        "활성스푸핑+A홀드(비활성 유지)"
                                        if still_inactive else "비활성 유지 실패"
                                    )
                                else:
                                    action_label = "활성스푸핑 실패(A 미전송)"
                            finally:
                                input_message.spoof_window_active(manager.hwnd, False)
                        self._skip_active_until = time.monotonic() + 0.3
                    else:
                        action_label = "스킵끔(자연종료 대기)"
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
        with self._log_lock:
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

    def _try_close_notification_panel(
        self,
        screen_gray: np.ndarray,
        manager: Optional[InactiveManager],
        click_mode: str,
        capture_mode: str,
        region: tuple[int, int, int, int],
        now: float,
    ) -> bool:
        """열린 알림 패널을 감지해 하단 종 버튼을 다시 누르고, 처리 중이면 True를 반환합니다."""

        if not NOTIFICATION_PANEL_GUARD_ENABLED:
            return False

        if now - self._notification_check_at < NOTIFICATION_PANEL_CHECK_INTERVAL_SECONDS:
            return self._notification_visible
        self._notification_check_at = now

        detected = is_notification_panel_open(screen_gray)
        if not detected:
            if self._notification_visible:
                self.queue_log("[알림 복구] 패널 닫힘 확인 → 자동화 재개")
            self._notification_streak = 0
            self._notification_visible = False
            return False

        self._notification_visible = True
        self._notification_streak += 1
        self.queue_status("알림 패널 닫는 중")

        # 한 프레임의 밝기 변화만으로는 누르지 않고 연속 프레임 합의를 기다립니다.
        if self._notification_streak < max(1, NOTIFICATION_PANEL_CONFIRM_COUNT):
            return True

        # 클릭 직후의 이전 프레임이 잠깐 남아도 종 버튼을 연타해 다시 여는 일이 없게 합니다.
        if now - self._notification_last_action_at < NOTIFICATION_PANEL_RETRY_SECONDS:
            return True
        self._notification_last_action_at = now

        height, width = screen_gray.shape[:2]
        toggle_x = min(width - 1, max(0, round((width - 1) * NOTIFICATION_TOGGLE_X_FRACTION)))
        toggle_y = min(height - 1, max(0, round((height - 1) * NOTIFICATION_TOGGLE_Y_FRACTION)))
        guard = None
        if manager is not None and manager.hwnd:
            start_x, start_y = manager.get_virtual_start_position(
                toggle_x,
                toggle_y,
            )
            guard = run_guarded_inactive_action(
                lambda: manager.post_curved_click(
                    start_x,
                    start_y,
                    toggle_x,
                    toggle_y,
                ),
                input_message.get_foreground_hwnd,
                manager.hwnd,
                poll_seconds=SKIP_EXPERIMENT_FOREGROUND_POLL_SECONDS,
            )
        action_ok = bool(
            guard is not None
            and guard.action_ok
            and guard.invariant_ok
        )
        if action_ok:
            self.queue_log(
                f"[알림 복구] 열린 패널 감지 → 종 버튼 비활성 클릭 "
                f"({toggle_x},{toggle_y}), 전면 불변 유지"
            )
        else:
            reason = guard.reason if guard is not None else "missing_window"
            self.queue_log(
                f"[알림 복구] 종 버튼 비활성 클릭 보류({reason}) → 잠시 후 재시도"
            )
        return True

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
        """UI에는 표시하지 않고 진단 파일에만 로그를 남깁니다."""

        timestamp = time.strftime("%H:%M:%S")
        line = f"{timestamp} {message}\n"

        # 여러 작업 스레드에서 호출될 수 있으므로 파일 객체를 잠깐만 잠급니다.
        # flush는 _flush_log_periodic이 1초마다 묶어서 처리합니다.
        with self._log_lock:
            if self._log_file is not None:
                try:
                    self._log_file.write(line)
                    self._log_dirty = True
                except Exception:
                    pass

    def queue_log(self, message: str) -> None:
        """작업 스레드 로그를 UI 큐 없이 진단 파일에 바로 기록합니다."""

        self.log(message)

    def set_status(self, status: str) -> None:
        """UI 스레드에서 상태 라벨을 갱신합니다."""

        self.status_var.set(status)
        self._paint_status(status)

    def _paint_status(self, status: str) -> None:
        """상태 문구의 의미에 맞춰 상태 알약과 사이드바 점 색을 맞춥니다."""

        c = self.COLORS
        text = status or ""
        if any(word in text for word in ("오류", "실패", "만료")):
            fg, bg = c["danger"], c["danger_bg"]
        elif any(word in text for word in ("요청", "정지", "종료")):
            fg, bg = c["warn"], c["warn_bg"]
        elif text.strip() in ("", "대기 중"):
            fg, bg = c["muted"], c["input"]
        else:
            fg, bg = c["ok"], c["ok_bg"]

        label = getattr(self, "status_label", None)
        if label is not None:
            label.configure(fg=fg, bg=bg)
        dot = getattr(self, "sidebar_dot", None)
        if dot is not None:
            dot.configure(fg=fg)

    def queue_status(self, status: str) -> None:
        """변경된 상태만 UI 큐에 넣어 반복 프레임의 큐 적재를 피합니다."""

        if status == self._last_queued_status:
            return
        self._last_queued_status = status
        self.ui_queue.put(("status", status))

    def _poll_ui_queue(self) -> None:
        """root.after로 UI 큐를 주기적으로 확인하고 위젯을 갱신합니다."""

        processed = 0
        while processed < 32:
            try:
                kind, message = self.ui_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "status":
                self.set_status(message)
            elif kind == "session":
                self._refresh_session_ui()
            elif kind == "finished":
                self._set_button_state(running=False)
            processed += 1

        if not self.closing:
            self.root.after(50, self._poll_ui_queue)

    def _flush_log_periodic(self) -> None:
        """로그 파일을 1초마다 한 번씩 묶어서 flush합니다(매 줄 flush 제거)."""
        with self._log_lock:
            if self._log_file is not None and self._log_dirty:
                try:
                    self._log_file.flush()
                    self._log_dirty = False
                except Exception:
                    pass
        if not self.closing:
            self.root.after(1000, self._flush_log_periodic)

    def _set_accent_button_state(self, button: tk.Button, enabled: bool) -> None:
        """버튼의 활성/비활성 상태와 색을 함께 바꿉니다(원래 톤으로 복원)."""

        c = self.COLORS
        base = getattr(button, "_tone_colors", (c["accent"], c["accent_hover"]))[0]
        button.configure(
            state=tk.NORMAL if enabled else tk.DISABLED,
            bg=base if enabled else c["disabled"],
        )

    def _set_button_state(self, running: bool) -> None:
        """실행 상태에 따라 시작/종료/캡처 버튼 활성화를 조절합니다."""

        self._set_accent_button_state(self.start_button, enabled=not running)
        self._set_accent_button_state(self.stop_button, enabled=running)
        # 실행 중 템플릿 교체는 다음 시작까지 반영되지 않으므로 혼동을 막기 위해 잠급니다.
        for button in self._capture_buttons.values():
            self._set_accent_button_state(button, enabled=not running)
        for button in self._reset_buttons.values():
            self._set_accent_button_state(button, enabled=not running)
        # 실행 중 창 크기를 바꾸면 캡처 세션과 좌표가 어긋나므로 잠급니다.
        for button in (
            getattr(self, "fit_button", None),
            getattr(self, "restore_fit_button", None),
        ):
            if button is not None:
                self._set_accent_button_state(button, enabled=not running)


class LicenseDialog:
    """라이센스 키 입력/검증 다이얼로그입니다."""

    def __init__(self, root: tk.Tk, base_dir: Path):
        self.root = root
        self.base_dir = base_dir
        self.result: Optional[str] = None

        self.root.title("라이센스 인증")
        self.root.geometry("460x400+200+200")
        self.root.resizable(False, False)

        # 본 화면(AutomationApp)과 같은 팔레트를 써서 첫인상을 통일합니다.
        palette = AutomationApp.COLORS
        bg = palette["bg"]
        panel = palette["panel"]
        border = palette["border"]
        input_bg = palette["input"]
        text_color = palette["text"]
        muted = palette["muted"]
        accent = palette["accent"]
        accent_hover = palette["accent_hover"]
        error_color = palette["danger"]
        success_color = palette["ok"]
        font_family = "Malgun Gothic" if platform.system() == "Windows" else "Arial"

        self.root.configure(bg=bg)

        main_frame = tk.Frame(self.root, bg=bg, padx=30, pady=30)
        main_frame.pack(fill=tk.BOTH, expand=True)

        mark = tk.Canvas(main_frame, width=44, height=44, bg=bg, highlightthickness=0, bd=0)
        mark.pack(anchor=tk.W)
        AutomationApp._round_rect(mark, 1, 1, 43, 43, 13, fill=accent, outline="")
        mark.create_text(22, 22, text="M", fill="#FFFFFF", font=(font_family, 17, "bold"))

        tk.Label(
            main_frame,
            text="mAuto 라이센스 인증",
            bg=bg,
            fg=text_color,
            font=(font_family, 16, "bold"),
        ).pack(anchor=tk.W, pady=(16, 4))
        tk.Label(
            main_frame,
            text="구매하신 라이센스 키를 입력하면 자동으로 이 PC에 등록됩니다.",
            bg=bg,
            fg=muted,
            font=(font_family, 9),
        ).pack(anchor=tk.W, pady=(0, 20))

        tk.Label(
            main_frame,
            text="LICENSE KEY",
            bg=bg,
            fg=muted,
            font=(font_family, 7, "bold"),
        ).pack(anchor=tk.W, pady=(0, 6))

        self.key_var = tk.StringVar()
        self.key_entry = tk.Entry(
            main_frame,
            textvariable=self.key_var,
            bg=input_bg,
            fg=text_color,
            insertbackground=accent,
            font=("Consolas", 12),
            relief=tk.FLAT,
            bd=0,
            highlightbackground=border,
            highlightcolor=accent,
            highlightthickness=1,
            justify=tk.CENTER,
            width=34,
        )
        self.key_entry.pack(fill=tk.X, ipady=9)
        self.key_entry.bind("<Return>", lambda _e: self._activate())

        self.message_var = tk.StringVar()
        self.message_label = tk.Label(
            main_frame,
            textvariable=self.message_var,
            bg=bg,
            fg=muted,
            font=(font_family, 9),
            wraplength=390,
            justify=tk.LEFT,
            anchor=tk.W,
        )
        self.message_label.pack(fill=tk.X, pady=(10, 18))

        btn_frame = tk.Frame(main_frame, bg=bg)
        btn_frame.pack(fill=tk.X)

        self.activate_btn = tk.Button(
            btn_frame,
            text="인증하기",
            command=self._activate,
            bg=accent,
            fg="#FFFFFF",
            activebackground=accent_hover,
            activeforeground="#FFFFFF",
            font=(font_family, 11, "bold"),
            relief=tk.FLAT,
            bd=0,
            pady=9,
            cursor="hand2",
        )
        self.activate_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 8))
        self.activate_btn.bind("<Enter>", lambda _e: self.activate_btn.configure(bg=accent_hover))
        self.activate_btn.bind("<Leave>", lambda _e: self.activate_btn.configure(bg=accent))

        self.exit_btn = tk.Button(
            btn_frame,
            text="종료",
            command=self.root.destroy,
            bg=panel,
            fg=muted,
            activebackground=border,
            activeforeground=text_color,
            font=(font_family, 11, "bold"),
            relief=tk.FLAT,
            bd=0,
            padx=22,
            pady=9,
            cursor="hand2",
        )
        self.exit_btn.pack(side=tk.RIGHT)

        self.info_label = tk.Label(
            main_frame,
            text="",
            bg=bg,
            fg=muted,
            font=(font_family, 8),
        )
        self.info_label.pack(side=tk.BOTTOM, pady=(14, 0))

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
        app = AutomationApp(self.root, license_key=key)
        # 실기 회귀 러너용 옵트인. 일반 실행에는 환경변수가 없으므로 UI 동작은 그대로이며,
        # 테스트 프로세스만 인증 직후 F8과 같은 시작 경로를 자동 호출합니다.
        if os.environ.get("MAUTO_AUTOSTART_EXPERIMENT", "").strip() == "1":
            self.root.after(1200, app.start_automation)
