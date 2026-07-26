"""FC ONLINE 이적시장 갱신매크로 전용 실행 파일.

기존 감독모드 앱과 UI/작업 스레드를 완전히 분리합니다. 실행 중에는 OCR과 전체 화면
템플릿 매칭을 시작하지 않고, 지정한 가격 숫자 영역만 WGC로 받아 비교합니다.
"""

from __future__ import annotations

import ctypes
import gc
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from datetime import timedelta
from pathlib import Path
from tkinter import messagebox
from typing import Optional

# The renewal classifier never performs large matrix multiplication.  Prevent
# NumPy's BLAS backend from reserving hundreds of MB for idle worker stacks.
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import cv2
import numpy as np
import psutil
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
    SUPPORTED_RENEWAL_WGC_SIZES,
    FastRenewalRunner,
    NormalizedPoint,
    NormalizedRect,
    RenewalChangeDetector,
    RenewalModalGuard,
    _FastClicker,
    build_calibration_result,
    build_guard_rect,
    crop_price_from_guard,
    decode_gray_png,
    dynamic_limit_price_boxes,
    encode_gray_png,
    expand_price_rect_for_recognition,
    is_supported_renewal_wgc_size,
    load_renewal_profile,
    price_box_in_guard,
    save_renewal_profile,
    save_renewal_diagnostic,
    validate_limit_price_selection,
    validate_price_region,
    wait_for_stable_wgc_frame,
)
from macroapp.renewal_soak import run_verification_soak
from macroapp.renewal_time import (
    NAVER_TIME_REFRESH_SECONDS,
    NaverMonotonicClock,
    next_schedule_window,
)
from macroapp.turbo_session import (
    MIN_AVAILABLE_RAM_GB,
    TARGET_AVAILABLE_RAM_GB,
    TARGET_FREE_VRAM_GB,
    TARGET_WGC_SIZE,
    TurboCandidate,
    WindowResizeSnapshot,
    close_selected_gracefully,
    force_close_remaining,
    get_window_rect,
    group_candidates,
    measure_pressure,
    resize_window_no_activate,
    restore_window_no_activate,
    scan_processes,
    target_outer_size_for_wgc,
)
from macroapp.window import InactiveManager


ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
CALIBRATION_CLOSE_TIMEOUT_SECONDS = 3.0
CALIBRATION_OPEN_TIMEOUT_SECONDS = 5.0
CALIBRATION_POPUP_LUMA_FLOOR = 210.0
CALIBRATION_POPUP_WHITE_FLOOR = 250
CALIBRATION_POPUP_WHITE_RATIO = 0.70
CALIBRATION_PRICE_TARGET_HEIGHT = 48
CALIBRATION_PRICE_TARGET_WIDTH = 220
CALIBRATION_PRICE_RIGHT_X = 0.795
CALIBRATION_STABLE_FRAME_DELTA = 1.5
WGC_SIZE_STABLE_SECONDS = 0.50
WGC_SIZE_STABLE_TIMEOUT_SECONDS = 4.0
HEADLESS_MONITOR_MAX_SECONDS = 12.0 * 60.0 * 60.0
HEADLESS_MONITOR_CHECKPOINT_SECONDS = 30.0
HEADLESS_MONITOR_CPU_AVERAGE_LIMIT_PERCENT = 3.0
HEADLESS_MONITOR_CPU_P95_LIMIT_PERCENT = 8.0
HEADLESS_MONITOR_PRIVATE_GROWTH_LIMIT_MB = 10.0
HEADLESS_MONITOR_RSS_GROWTH_LIMIT_MB = 20.0
HEADLESS_MONITOR_OPEN_FAILURE_RATE_LIMIT = 0.005
HEADLESS_MONITOR_MIN_RESOURCE_SAMPLE_INTERVAL_SECONDS = 5.0


def _headless_open_failure_summary(
    telemetry: dict[str, object],
) -> dict[str, object]:
    confirmed = max(0, int(telemetry.get("confirmed_openings", 0)))
    failures = max(0, int(telemetry.get("open_failures", 0)))
    attempts = confirmed + failures
    failure_rate = (
        float(failures) / float(attempts)
        if attempts > 0
        else 0.0
    )
    return {
        "confirmed_openings": confirmed,
        "open_failures": failures,
        "open_attempts": attempts,
        "open_failure_rate": failure_rate,
        "open_failure_rate_limit": (
            HEADLESS_MONITOR_OPEN_FAILURE_RATE_LIMIT
        ),
        "within_limit": bool(
            attempts > 0
            and failure_rate
            <= HEADLESS_MONITOR_OPEN_FAILURE_RATE_LIMIT
        ),
    }


def _headless_checkpoint_resources(
    report: dict[str, object],
    *,
    finished: bool,
    live_resources: dict[str, object],
) -> dict[str, object]:
    finalized = report.get("resources")
    if finished and isinstance(finalized, dict):
        return dict(finalized)
    return dict(live_resources)


def _headless_resource_sample_due(
    resource_samples: list[dict[str, float]],
    elapsed_seconds: float,
) -> bool:
    if not resource_samples:
        return True
    previous_elapsed = float(
        resource_samples[-1].get("elapsed_seconds", 0.0)
    )
    return (
        float(elapsed_seconds) - previous_elapsed
        >= HEADLESS_MONITOR_MIN_RESOURCE_SAMPLE_INTERVAL_SECONDS
    )


def _calibration_popup_opacity(
    guard: np.ndarray,
) -> tuple[float, float, bool]:
    if guard.size == 0:
        return 0.0, 0.0, False
    luma = float(np.mean(guard))
    white_ratio = float(
        np.count_nonzero(guard >= CALIBRATION_POPUP_WHITE_FLOOR)
    ) / float(guard.size)
    return (
        luma,
        white_ratio,
        luma >= CALIBRATION_POPUP_LUMA_FLOOR
        and white_ratio >= CALIBRATION_POPUP_WHITE_RATIO,
    )


def _expand_headless_price_rect(
    rect: NormalizedRect,
    side_name: str,
    frame_width: int,
    frame_height: int,
) -> NormalizedRect:
    """Give right-aligned FC prices enough room for longer current values."""

    x1, y1, x2, y2 = rect.to_pixels(frame_width, frame_height)
    if side_name not in ("buy", "sell"):
        raise ValueError("side_name must be buy or sell")
    target_width = max(x2 - x1, CALIBRATION_PRICE_TARGET_WIDTH)
    target_right = min(
        frame_width,
        max(x2, int(round(frame_width * CALIBRATION_PRICE_RIGHT_X))),
    )
    target_left = max(0, target_right - target_width)
    # FC uses different buy/sell modal layouts.  At 1928x1048 the buy upper
    # limit and sell lower limit both occupy the 431:479 row.  The buy row
    # below this range is the amount input, not the upper-limit label.
    target_center_y = 0.4342
    target_top = int(
        round(
            frame_height * target_center_y
            - CALIBRATION_PRICE_TARGET_HEIGHT * 0.5
        )
    )
    target_top = max(
        0,
        min(frame_height - CALIBRATION_PRICE_TARGET_HEIGHT, target_top),
    )
    target_bottom = target_top + CALIBRATION_PRICE_TARGET_HEIGHT
    return NormalizedRect(
        target_left / frame_width,
        target_top / frame_height,
        target_right / frame_width,
        target_bottom / frame_height,
    )


def _calibration_selection_probe(
    side_profile,
    closed_screen_crop: np.ndarray,
) -> np.ndarray:
    """Use the stored numeric row to validate an expanded popup ROI.

    The list screen does not contain the popup's right-side limit row. After
    widening an older narrow ROI, the new crop shape intentionally differs
    from the saved baseline, so shape equality must not decide whether the
    saved numeric evidence is usable.
    """

    if side_profile.baseline_png:
        try:
            stored = decode_gray_png(side_profile.baseline_png)
            if validate_price_region(stored).valid:
                return stored
        except (TypeError, ValueError):
            pass
    return closed_screen_crop


def _calibration_guard_delta(
    previous: Optional[np.ndarray],
    current: np.ndarray,
) -> float:
    if previous is None or previous.shape != current.shape:
        return float("inf")
    return float(cv2.mean(cv2.absdiff(previous, current))[0])


def _wait_for_stable_wgc_frame(
    manager: InactiveManager,
    *,
    timeout_seconds: float = WGC_SIZE_STABLE_TIMEOUT_SECONDS,
    first_frame: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Return only after one WGC size remains stable across the settle window."""

    return wait_for_stable_wgc_frame(
        manager,
        settle_seconds=WGC_SIZE_STABLE_SECONDS,
        timeout_seconds=timeout_seconds,
        first_frame=first_frame,
    )


def _measure_stable_wgc_frame(
    hwnd: int,
    logger=None,
) -> np.ndarray:
    manager = InactiveManager(
        WINDOW_TITLE,
        logger=logger or (lambda _message: None),
    )
    manager.hwnd = int(hwnd)
    try:
        return _wait_for_stable_wgc_frame(manager)
    finally:
        manager.stop_capture()


def _fit_game_window_to_wgc(
    hwnd: int,
    logger=None,
    *,
    allow_supported_fallback: bool = False,
) -> tuple[WindowResizeSnapshot, tuple[int, int], tuple[int, int]]:
    """Fit without activation, verify stable WGC size, and roll back on failure."""

    original = get_window_rect(hwnd)
    before_frame = _measure_stable_wgc_frame(hwnd, logger)
    before_size = (int(before_frame.shape[1]), int(before_frame.shape[0]))
    # The 1920x1040 fallback is safe for loading an existing size-bound
    # profile, but "1080p fit" must still converge on the preferred
    # 1928x1048 capture size.  Treating every supported size as already fitted
    # left this machine permanently on the fallback and made the documented
    # target impossible to calibrate.
    if before_size == TARGET_WGC_SIZE:
        return (
            WindowResizeSnapshot(int(hwnd), original, original),
            before_size,
            before_size,
        )
    if (
        allow_supported_fallback
        and is_supported_renewal_wgc_size(*before_size)
    ):
        return (
            WindowResizeSnapshot(int(hwnd), original, original),
            before_size,
            before_size,
        )

    target_outer_size = target_outer_size_for_wgc(
        original,
        before_size,
        TARGET_WGC_SIZE,
    )
    snapshot = resize_window_no_activate(hwnd, target_outer_size)
    original_snapshot = snapshot
    try:
        for correction in range(3):
            after_frame = _measure_stable_wgc_frame(hwnd, logger)
            after_size = (int(after_frame.shape[1]), int(after_frame.shape[0]))
            if after_size == TARGET_WGC_SIZE:
                return snapshot, before_size, after_size
            if correction >= 2:
                break
            corrected_outer_size = target_outer_size_for_wgc(
                snapshot.resized,
                after_size,
                TARGET_WGC_SIZE,
            )
            corrected = resize_window_no_activate(hwnd, corrected_outer_size)
            snapshot = WindowResizeSnapshot(
                int(hwnd),
                original_snapshot.original,
                corrected.resized,
                original_snapshot.original_was_maximized,
            )
        raise RuntimeError(
            f"Stable WGC is {after_size[0]}x{after_size[1]} after fitting; "
            f"expected {TARGET_WGC_SIZE[0]}x{TARGET_WGC_SIZE[1]}."
        )
    except Exception:
        restore_window_no_activate(snapshot)
        raise


def _save_calibration_diagnostic(
    side: str,
    reason: str,
    guard_frames: list[np.ndarray],
    price_box: tuple[int, int, int, int],
    metadata: dict[str, object],
) -> None:
    """Best-effort local evidence for a failed calibration; never changes control flow."""

    usable_guards = [
        frame
        for frame in guard_frames
        if frame is not None and frame.size
    ]
    if not usable_guards:
        return
    price_frames: list[np.ndarray] = []
    expected_shape = usable_guards[0].shape
    for frame in usable_guards:
        if frame.shape != expected_shape:
            continue
        try:
            price = crop_price_from_guard(frame, price_box)
            if price.ndim == 3:
                price = cv2.cvtColor(price, cv2.COLOR_BGR2GRAY)
            if price.size:
                price_frames.append(price.copy())
        except Exception:
            continue
    if not price_frames:
        return
    save_renewal_diagnostic(
        side,
        reason,
        price_frames[0],
        price_frames,
        metadata,
        guard_frames=usable_guards,
    )


def _enable_safe_process_priority() -> Optional[int]:
    """현재 매크로만 Above Normal로 올리고 원래 우선순위를 반환합니다."""

    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetCurrentProcess()
        original = int(kernel32.GetPriorityClass(handle))
        if original and original != ABOVE_NORMAL_PRIORITY_CLASS:
            if not kernel32.SetPriorityClass(
                handle,
                ABOVE_NORMAL_PRIORITY_CLASS,
            ):
                return None
        return original
    except Exception:
        return None


def _restore_process_priority(original: Optional[int]) -> None:
    if os.name != "nt" or not original:
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), int(original))
    except Exception:
        pass


def _available_ram_gb() -> Optional[float]:
    if os.name != "nt":
        return None

    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(
            ctypes.byref(status)
        ):
            return None
        return float(status.ullAvailPhys) / float(1024**3)
    except Exception:
        return None


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
        self._turbo_buttons: list[tk.Button] = []
        self._turbo_checkboxes: list[tk.Checkbutton] = []
        self._original_priority_class = _enable_safe_process_priority()
        self._turbo_candidates: tuple[TurboCandidate, ...] = ()
        self._turbo_vars: dict[str, tk.BooleanVar] = {}
        self._turbo_window_snapshot: Optional[WindowResizeSnapshot] = None
        self.naver_clock = NaverMonotonicClock()
        self._clock_sync_inflight = False
        self._clock_last_attempt = 0.0

        self.window_title_var = tk.StringVar(value=WINDOW_TITLE)
        self.side_var = tk.StringVar(value="buy")
        self.speed_var = tk.IntVar(value=self.profile.speed_level)
        self.target_minute_var = tk.IntVar(value=self.profile.target_minute)
        self.monitor_only_var = tk.BooleanVar(value=False)
        self.speed_status_var = tk.StringVar()
        self.status_var = tk.StringVar(value="좌표 설정 후 F8")
        self.clock_var = tk.StringVar(value="네이버 --:--:--")
        self.schedule_status_var = tk.StringVar(value="네이버 시간 동기화 중")
        self.setting_status_var = tk.StringVar(value="")
        self.turbo_status_var = tk.StringVar(value="터보 상태 확인 중")

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
        self.root.geometry("820x1010+120+10")
        self.root.minsize(760, 920)
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
        self._request_clock_sync(force=True)

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
        self.monitor_checkbox = tk.Checkbutton(
            speed_panel,
            text="무주문 측정 모드 (가격 변경을 감지해도 클릭하지 않음)",
            variable=self.monitor_only_var,
            bg=c["panel"],
            fg=c["text"],
            activebackground=c["panel"],
            activeforeground=c["text"],
            selectcolor=c["input"],
            highlightthickness=0,
            font=self._font(9, bold=True),
        )
        self.monitor_checkbox.pack(anchor=tk.W, pady=(8, 0))
        self._on_speed_changed(str(self.speed_var.get()))

        schedule_panel = self._panel(body, "4. 네이버 시간창")
        schedule_panel.pack(fill=tk.X, pady=(0, 10))
        schedule_row = tk.Frame(schedule_panel, bg=c["panel"])
        schedule_row.pack(fill=tk.X)
        tk.Label(
            schedule_row,
            text="목표 분",
            bg=c["panel"],
            fg=c["text"],
            font=self._font(10, bold=True),
        ).pack(side=tk.LEFT)
        self.target_minute_spinbox = tk.Spinbox(
            schedule_row,
            from_=0,
            to=59,
            width=4,
            format="%02.0f",
            textvariable=self.target_minute_var,
            command=self._refresh_schedule_status,
            bg=c["input"],
            fg=c["text"],
            insertbackground=c["text"],
            buttonbackground=c["border"],
            relief=tk.FLAT,
            font=("Consolas", 11, "bold"),
        )
        self.target_minute_spinbox.pack(side=tk.LEFT, padx=(10, 12))
        self.target_minute_spinbox.bind(
            "<KeyRelease>",
            lambda _event: self._refresh_schedule_status(),
        )
        self.naver_sync_button = self._button(
            schedule_row,
            "네이버 시간 재동기화",
            lambda: self._request_clock_sync(force=True),
            small=True,
        )
        self.naver_sync_button.pack(side=tk.LEFT)
        self._turbo_buttons.append(self.naver_sync_button)
        tk.Label(
            schedule_panel,
            textvariable=self.schedule_status_var,
            justify=tk.LEFT,
            anchor=tk.W,
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(9),
        ).pack(fill=tk.X, pady=(8, 0))

        turbo_panel = self._panel(body, "5. 16GB 터보 세션")
        turbo_panel.pack(fill=tk.X, pady=(0, 10))
        turbo_controls = tk.Frame(turbo_panel, bg=c["panel"])
        turbo_controls.pack(fill=tk.X, pady=(0, 7))
        refresh_button = self._button(
            turbo_controls,
            "앱·메모리 검사",
            self._refresh_turbo_session,
            small=True,
        )
        refresh_button.pack(side=tk.LEFT, padx=(0, 7))
        close_button = self._button(
            turbo_controls,
            "선택 앱 종료",
            self._close_selected_turbo_apps,
            small=True,
        )
        close_button.pack(side=tk.LEFT, padx=(0, 7))
        resize_button = self._button(
            turbo_controls,
            "1080p 창 맞춤",
            self._resize_game_window,
            small=True,
        )
        resize_button.pack(side=tk.LEFT, padx=(0, 7))
        restore_button = self._button(
            turbo_controls,
            "원래 크기 복원",
            self._restore_game_window,
            small=True,
        )
        restore_button.pack(side=tk.LEFT)
        self._turbo_buttons.extend(
            (refresh_button, close_button, resize_button, restore_button)
        )

        self.turbo_apps_frame = tk.Frame(turbo_panel, bg=c["panel"])
        self.turbo_apps_frame.pack(fill=tk.X)
        tk.Label(
            turbo_panel,
            textvariable=self.turbo_status_var,
            justify=tk.LEFT,
            anchor=tk.W,
            bg=c["panel"],
            fg=c["muted"],
            font=self._font(8),
        ).pack(fill=tk.X, pady=(7, 0))
        self._refresh_turbo_session()

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
            and side.baseline_variants_png
            and side.guard_png
            and side.closed_guard_png
            and side.calibration_openings >= RENEWAL_CALIBRATION_OPENINGS
            and side.calibration_version >= RENEWAL_CALIBRATION_VERSION
            and side.calibrated_frame_size() is not None
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
            self.profile.target_minute = self._target_minute()
            self.target_minute_var.set(self.profile.target_minute)
            self._refresh_schedule_status()
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
            text = (
                "속도 10/10 · 전환 중 가격 선판정 · "
                "고유 2프레임 즉시 ESC/변경 확인"
            )
        else:
            mode = "안정" if level <= 3 else ("균형" if level <= 7 else "초고속")
            text = (
                f"속도 {level}/10 · {mode} · 명확한 변경 "
                f"{required_frames}프레임 · 애매하면 주문 금지"
            )
        self.speed_status_var.set(text)

    def _target_minute(self) -> int:
        try:
            return min(59, max(0, int(self.target_minute_var.get())))
        except (tk.TclError, TypeError, ValueError):
            return int(self.profile.target_minute)

    def _refresh_schedule_status(self) -> None:
        minute = self._target_minute()
        if not self.naver_clock.is_fresh():
            error = self.naver_clock.last_error
            self.schedule_status_var.set(
                f"목표 :{minute:02d} · 네이버 시간 "
                + (f"동기화 실패: {error}" if error else "동기화 중")
            )
            return
        now = self.naver_clock.now()
        window = next_schedule_window(now, minute)
        self.schedule_status_var.set(
            f"목표 :{minute:02d} · 동작 "
            f"{window.start:%H:%M:%S}~"
            f"{(window.end_exclusive - timedelta(seconds=1)):%H:%M:%S} "
            f"· 오차 ±{self.naver_clock.uncertainty_seconds:.2f}초"
        )

    def _request_clock_sync(self, *, force: bool = False) -> None:
        if self.closing or self._clock_sync_inflight:
            return
        now = time.monotonic()
        if (
            not force
            and self.naver_clock.is_fresh()
            and now - self._clock_last_attempt < NAVER_TIME_REFRESH_SECONDS
        ):
            return
        self._clock_sync_inflight = True
        self._clock_last_attempt = now
        self.schedule_status_var.set("네이버 시간 동기화 중")

        def worker() -> None:
            success = self.naver_clock.sync()
            if not self.closing:
                self.root.after(0, lambda: self._clock_sync_finished(success))

        threading.Thread(target=worker, daemon=True).start()

    def _clock_sync_finished(self, success: bool) -> None:
        self._clock_sync_inflight = False
        if success:
            self.log(
                "[네이버 시간] 동기화 완료 "
                f"· 오차 ±{self.naver_clock.uncertainty_seconds:.2f}초"
            )
        else:
            self.log(
                "[네이버 시간] 동기화 실패 · "
                f"{self.naver_clock.last_error}"
            )
        self._refresh_schedule_status()

    def _refresh_turbo_session(self) -> None:
        """종료 허용 앱과 시스템 압박을 한 번만 읽어 UI에 표시합니다."""

        previous = {
            key: bool(variable.get())
            for key, variable in self._turbo_vars.items()
        }
        try:
            processes = scan_processes()
            candidates = group_candidates(processes)
            pressure = measure_pressure(processes)
        except Exception as exc:
            self._turbo_candidates = ()
            self.turbo_status_var.set(f"터보 검사 실패: {exc}")
            return

        self._turbo_candidates = candidates
        for child in self.turbo_apps_frame.winfo_children():
            child.destroy()
        self._turbo_vars = {}
        self._turbo_checkboxes = []
        for index, candidate in enumerate(candidates):
            variable = tk.BooleanVar(
                value=previous.get(candidate.key, True)
            )
            self._turbo_vars[candidate.key] = variable
            text = (
                f"{candidate.label} "
                f"{candidate.working_set_mb:.0f}MB/{candidate.process_count}개"
            )
            checkbox = tk.Checkbutton(
                self.turbo_apps_frame,
                text=text,
                variable=variable,
                bg=self.COLORS["panel"],
                fg=self.COLORS["text"],
                activebackground=self.COLORS["panel"],
                activeforeground=self.COLORS["text"],
                selectcolor=self.COLORS["input"],
                highlightthickness=0,
                font=self._font(8, bold=True),
            )
            checkbox.grid(
                row=index // 2,
                column=index % 2,
                sticky=tk.W,
                padx=(0, 20),
                pady=1,
            )
            self._turbo_checkboxes.append(checkbox)
        if not candidates:
            tk.Label(
                self.turbo_apps_frame,
                text="종료 가능한 백그라운드 앱 없음",
                bg=self.COLORS["panel"],
                fg=self.COLORS["ok"],
                font=self._font(8, bold=True),
            ).pack(anchor=tk.W)

        ram_text = (
            f"RAM 여유 {pressure.available_ram_gb:.1f}GB"
            if pressure.available_ram_gb is not None
            else "RAM 측정 불가"
        )
        pagefile_text = (
            f"페이지파일 사용 {pressure.pagefile_used_gb:.1f}GB"
            if pressure.pagefile_used_gb is not None
            else "페이지파일 측정 불가"
        )
        vram_text = (
            f"VRAM 여유 {pressure.free_vram_gb:.1f}/"
            f"{pressure.total_vram_gb:.1f}GB"
            if (
                pressure.free_vram_gb is not None
                and pressure.total_vram_gb is not None
            )
            else "VRAM 측정 불가"
        )
        overlay_text = (
            " · NVIDIA Overlay 켜짐(수동 비활성 권장)"
            if pressure.nvidia_overlay_running
            else ""
        )
        selected_mb = sum(
            candidate.working_set_mb
            for candidate in candidates
            if self._turbo_vars[candidate.key].get()
        )
        self.turbo_status_var.set(
            f"{ram_text} · {pagefile_text} · {vram_text}{overlay_text}\n"
            f"선택 앱 현재 점유 {selected_mb:.0f}MB · "
            f"목표 RAM {TARGET_AVAILABLE_RAM_GB:.0f}GB+/VRAM "
            f"{TARGET_FREE_VRAM_GB:.0f}GB+ · WGC "
            f"{TARGET_WGC_SIZE[0]}x{TARGET_WGC_SIZE[1]}"
        )

    def _selected_turbo_keys(self) -> set[str]:
        return {
            key
            for key, variable in self._turbo_vars.items()
            if bool(variable.get())
        }

    def _close_selected_turbo_apps(self) -> bool:
        """선택 앱을 정상 종료하고 남은 동일 PID는 재확인 뒤 종료합니다."""

        selected_keys = self._selected_turbo_keys()
        selected = tuple(
            candidate
            for candidate in self._turbo_candidates
            if candidate.key in selected_keys
        )
        if not selected:
            self.status_var.set("종료할 터보 앱 없음")
            return True
        lines = [
            f"• {candidate.label}: {candidate.working_set_mb:.0f}MB "
            f"({candidate.process_count}개)"
            for candidate in selected
        ]
        if not messagebox.askokcancel(
            "선택 앱 종료",
            "아래 앱을 정상 종료합니다. 저장하지 않은 작업을 먼저 확인하세요.\n\n"
            + "\n".join(lines)
            + "\n\nFC ONLINE·안티치트·Windows·매크로는 건드리지 않습니다.",
            parent=self.root,
            default=messagebox.CANCEL,
        ):
            return False

        self.status_var.set("선택 앱 정상 종료 중")
        self.root.update_idletasks()
        result = close_selected_gracefully(
            self._turbo_candidates,
            selected_keys,
            timeout_seconds=2.0,
        )
        closed_count = len(result.closed)
        remaining = result.remaining
        if remaining:
            remaining_pids = len(remaining)
            if messagebox.askyesno(
                "종료되지 않은 앱",
                f"정상 종료 후에도 선택 앱 프로세스 {remaining_pids}개가 남았습니다.\n"
                "같은 PID와 생성 시각을 다시 확인한 뒤 강제 종료할까요?\n\n"
                "저장하지 않은 브라우저 입력 내용은 사라질 수 있습니다.",
                parent=self.root,
                default=messagebox.NO,
            ):
                forced = force_close_remaining(remaining)
                closed_count += len(forced.closed)
                remaining = forced.remaining

        self._refresh_turbo_session()
        if remaining:
            self.status_var.set(
                f"터보 앱 {closed_count}개 종료 · {len(remaining)}개 남음"
            )
        else:
            self.status_var.set(f"터보 앱 {closed_count}개 종료 완료")
        return True

    def _find_game_hwnd(self) -> Optional[int]:
        manager = InactiveManager(
            self.window_title_var.get().strip(),
            logger=self.log,
        )
        if not manager.find_window() or manager.hwnd is None:
            self.status_var.set("FC ONLINE 창을 찾지 못함")
            return None
        return int(manager.hwnd)

    def _invalidate_size_calibration(self) -> None:
        for side in (self.profile.buy, self.profile.sell):
            side.baseline_variants_png = []
            side.calibration_openings = 0
            side.calibration_version = 0
            side.calibrated_frame_width = 0
            side.calibrated_frame_height = 0
        self._save_settings()
        self._refresh_status()

    def _resize_game_window(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.status_var.set("실행 중에는 창 크기 변경 불가")
            return
        hwnd = self._find_game_hwnd()
        if hwnd is None:
            return
        try:
            snapshot, before_size, after_size = _fit_game_window_to_wgc(
                hwnd,
                self.log,
            )
            if snapshot.original == snapshot.resized:
                self.status_var.set(
                    f"이미 안정 WGC {after_size[0]}x{after_size[1]}"
                )
                return
        except Exception as exc:
            messagebox.showerror(
                "1080p 창 맞춤 실패",
                str(exc),
                parent=self.root,
            )
            self.status_var.set("1080p 창 맞춤 실패")
            return
        self._turbo_window_snapshot = snapshot
        self._invalidate_size_calibration()
        self.status_var.set(
            f"FC 창 {snapshot.original.width}x{snapshot.original.height} → "
            f"{snapshot.resized.width}x{snapshot.resized.height} · "
            f"WGC {before_size[0]}x{before_size[1]} → "
            f"{after_size[0]}x{after_size[1]} · 구매/판매 가격영역 재설정 필요"
        )

    def _restore_game_window(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.status_var.set("실행 중에는 창 크기 복원 불가")
            return
        snapshot = self._turbo_window_snapshot
        if snapshot is None:
            self.status_var.set("이번 세션에 저장된 원래 창 크기 없음")
            return
        try:
            restored = restore_window_no_activate(snapshot)
        except Exception as exc:
            messagebox.showerror(
                "원래 크기 복원 실패",
                str(exc),
                parent=self.root,
            )
            self.status_var.set("원래 창 크기 복원 실패")
            return
        self._turbo_window_snapshot = None
        self._invalidate_size_calibration()
        self.status_var.set(
            f"FC 창 {restored.width}x{restored.height} 복원 · "
            "구매/판매 가격영역 재설정 필요"
        )

    def _confirm_turbo_pressure(self) -> bool:
        """시작 직전 압박이 목표보다 높으면 사용자가 명시적으로 결정합니다."""

        try:
            processes = scan_processes()
            pressure = measure_pressure(processes)
        except Exception:
            return True
        warnings: list[str] = []
        if (
            pressure.available_ram_gb is not None
            and pressure.available_ram_gb < TARGET_AVAILABLE_RAM_GB
        ):
            level = (
                "매우 부족"
                if pressure.available_ram_gb < MIN_AVAILABLE_RAM_GB
                else "목표 미달"
            )
            warnings.append(
                f"RAM 여유 {pressure.available_ram_gb:.1f}GB ({level}, "
                f"목표 {TARGET_AVAILABLE_RAM_GB:.0f}GB)"
            )
        if (
            pressure.free_vram_gb is not None
            and pressure.free_vram_gb < TARGET_FREE_VRAM_GB
        ):
            warnings.append(
                f"VRAM 여유 {pressure.free_vram_gb:.1f}GB "
                f"(목표 {TARGET_FREE_VRAM_GB:.0f}GB)"
            )
        if pressure.nvidia_overlay_running:
            warnings.append("NVIDIA Overlay 실행 중")
        if not warnings:
            return True
        return bool(
            messagebox.askyesno(
                "터보 목표 미달",
                "\n".join(f"• {warning}" for warning in warnings)
                + "\n\n이 상태에서도 갱신을 시작할까요?",
                parent=self.root,
                default=messagebox.NO,
            )
        )

    def _capture_frame(self) -> Optional[np.ndarray]:
        manager = InactiveManager(self.window_title_var.get().strip(), logger=self.log)
        try:
            if not manager.find_window():
                self.status_var.set("FC ONLINE 창을 찾지 못함")
                return None
            self.status_var.set("WGC 화면 캡처 중")
            self.root.update_idletasks()
            return _wait_for_stable_wgc_frame(manager).copy()
        except Exception as exc:
            self.log(f"[WGC 안정화 실패] {exc}")
            self.status_var.set("WGC 화면 안정화 실패")
            return None
        finally:
            manager.stop_capture()

    def _capture_guard_samples(
        self,
        guard_rect: NormalizedRect,
        price_box: tuple[int, int, int, int],
        action_point: NormalizedPoint,
        expected_size: tuple[int, int],
    ) -> tuple[list[list[np.ndarray]], list[np.ndarray]]:
        """열린 팝업 8회와 안정된 닫힘 화면의 고유 WGC 프레임을 모읍니다."""
        manager = InactiveManager(self.window_title_var.get().strip(), logger=self.log)
        sessions: list[list[np.ndarray]] = []
        closed_samples: list[np.ndarray] = []
        recent_guard_frames: list[np.ndarray] = []
        recent_packets: list[dict[str, object]] = []
        reference_guard: Optional[np.ndarray] = None
        try:
            if not manager.find_window():
                raise RuntimeError("FC ONLINE 창을 찾지 못했습니다.")
            full_frame = manager.capture_client_area(window_validated=True)
            if full_frame is None:
                raise RuntimeError("팝업 보정용 WGC 프레임을 받지 못했습니다.")
            capture_engine = manager.capture_engine
            if capture_engine is not None and hasattr(
                capture_engine,
                "get_frame_size",
            ):
                full_frame = _wait_for_stable_wgc_frame(
                    manager,
                    first_frame=full_frame,
                )
            frame_height, frame_width = full_frame.shape[:2]
            if (frame_width, frame_height) != expected_size:
                full_frame = _wait_for_stable_wgc_frame(
                    manager,
                    first_frame=full_frame,
                )
                frame_height, frame_width = full_frame.shape[:2]
                if (frame_width, frame_height) != expected_size:
                    raise RuntimeError(
                        "좌표 선택 중 게임 창 크기가 바뀌었습니다. 다시 설정하세요."
                    )
            gx1, gy1, gx2, gy2 = guard_rect.to_pixels(frame_width, frame_height)
            reference_guard = full_frame[gy1:gy2, gx1:gx2].copy()
            dynamic_boxes = dynamic_limit_price_boxes(
                price_box,
                self.side_var.get(),
                frame_height,
            )
            provisional_guard = RenewalModalGuard(
                reference_guard,
                price_box,
                shift_limit=4,
                dynamic_boxes=dynamic_boxes,
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
                    recent_guard_frames.append(packet.image.copy())
                    recent_packets.append(
                        {
                            "sequence_id": int(packet.sequence_id),
                            "captured_at": float(packet.captured_at),
                            "shape": list(packet.image.shape),
                        }
                    )
                    del recent_guard_frames[:-50]
                    del recent_packets[:-50]
                    return packet
                return None

            def wait_until_closed(opening_number: int) -> list[np.ndarray]:
                deadline = (
                    time.monotonic() + CALIBRATION_CLOSE_TIMEOUT_SECONDS
                )
                stable: list[np.ndarray] = []
                wrong_shape_count = 0
                while True:
                    packet = next_packet(deadline)
                    if packet is None:
                        raise RuntimeError(
                            f"{opening_number}번째 팝업을 ESC로 닫았지만 "
                            "안정된 닫힘 화면을 확인하지 못했습니다. "
                            "팝업이 열린 상태인지 확인한 뒤 다시 시도하세요."
                        )
                    if packet.image.shape != reference_guard.shape:
                        wrong_shape_count += 1
                        stable.clear()
                        continue
                    registration = provisional_guard.register(
                        packet.image,
                        0.0,
                        0.0,
                    )
                    if registration.valid:
                        stable.clear()
                        continue
                    sample = packet.image
                    if stable:
                        delta = float(
                            cv2.mean(cv2.absdiff(stable[-1], sample))[0]
                        )
                        if delta > CALIBRATION_STABLE_FRAME_DELTA:
                            stable.clear()
                    stable.append(sample.copy())
                    if len(stable) >= 4:
                        self.log(
                            f"[안전 보정] {opening_number}번째 팝업 닫힘 확인 "
                            f"· 새 프레임 4장 · 다른 크기 {wrong_shape_count}장"
                        )
                        return stable[-4:]

            flush()
            for opening in range(RENEWAL_CALIBRATION_OPENINGS):
                opening_number = opening + 1
                if opening > 0:
                    self.status_var.set(
                        f"v10 우측 가격 보정 {opening_number}/"
                        f"{RENEWAL_CALIBRATION_OPENINGS} · 팝업 닫힘 확인"
                    )
                    self.root.update_idletasks()
                    clicker.press_escape()
                    closed_samples = wait_until_closed(opening)
                    flush()
                    clicker.click_client(action)

                self.status_var.set(
                    f"v10 우측 가격 보정 {opening_number}/"
                    f"{RENEWAL_CALIBRATION_OPENINGS} · "
                    "안정된 팝업 확인 중"
                )
                self.root.update_idletasks()
                session: list[np.ndarray] = []
                deadline = time.monotonic() + CALIBRATION_OPEN_TIMEOUT_SECONDS
                invalid_count = 0
                wrong_shape_count = 0
                incomplete_price_count = 0
                unstable_price_count = 0
                last_luma_delta = 255.0
                last_edge_delta = 1.0
                previous_price_pair = None
                previous_stable_guard = None
                while (
                    len(session) < RENEWAL_CALIBRATION_FRAMES_PER_OPENING
                    and time.monotonic() < deadline
                ):
                    packet = next_packet(deadline)
                    if packet is None:
                        break
                    sample = packet.image
                    if sample.shape != reference_guard.shape:
                        wrong_shape_count += 1
                        session.clear()
                        previous_price_pair = None
                        previous_stable_guard = None
                        continue
                    _, _, popup_is_opaque = _calibration_popup_opacity(sample)
                    if not popup_is_opaque:
                        invalid_count += 1
                        session.clear()
                        previous_price_pair = None
                        previous_stable_guard = None
                        continue
                    registration = provisional_guard.register(sample, 0.0, 0.0)
                    last_luma_delta = registration.luma_delta
                    last_edge_delta = registration.edge_delta
                    if not registration.valid:
                        invalid_count += 1
                        session.clear()
                        previous_price_pair = None
                        previous_stable_guard = None
                        continue
                    price = crop_price_from_guard(
                        registration.aligned,
                        price_box,
                    )
                    price_validation = validate_price_region(price)
                    if not price_validation.valid:
                        incomplete_price_count += 1
                        session.clear()
                        previous_price_pair = None
                        previous_stable_guard = None
                        continue
                    price_pair = RenewalChangeDetector.prepare_pair(price)
                    guard_stability = _calibration_guard_delta(
                        previous_stable_guard,
                        registration.aligned,
                    )
                    pair_stability = (
                        float("inf")
                        if previous_price_pair is None
                        else RenewalChangeDetector.pair_stability(
                            previous_price_pair,
                            price_pair,
                        )
                    )
                    if previous_price_pair is None:
                        session[:] = [sample.copy()]
                    elif (
                        pair_stability <= 0.030
                        and guard_stability <= CALIBRATION_STABLE_FRAME_DELTA
                    ):
                        session.append(sample.copy())
                    else:
                        unstable_price_count += 1
                        session[:] = [sample.copy()]
                    previous_price_pair = price_pair
                    previous_stable_guard = registration.aligned.copy()
                if len(session) < RENEWAL_CALIBRATION_FRAMES_PER_OPENING:
                    if opening == 0:
                        hint = (
                            "가격영역을 선택하기 전에 구매/판매 팝업을 완전히 "
                            "열어 두고 다시 시도하세요."
                        )
                    else:
                        hint = (
                            "목록 화면의 구매/판매 버튼 좌표가 맞는지 다시 "
                            "설정하세요."
                        )
                    raise RuntimeError(
                        f"{opening_number}번째 팝업이 5초 안에 안정되지 "
                        f"않았습니다. {hint} "
                        f"(불일치 {invalid_count}장, 다른 크기 "
                        f"{wrong_shape_count}장, 미완성 가격 "
                        f"{incomplete_price_count}장, 가격 전환 "
                        f"{unstable_price_count}장, 밝기 차이 "
                        f"{last_luma_delta:.2f}, 구조 차이 "
                        f"{last_edge_delta:.3f})"
                    )
                sessions.append(session)
                self.log(
                    f"[안전 보정] {opening_number}/"
                    f"{RENEWAL_CALIBRATION_OPENINGS} 팝업 확인 "
                    f"· 불일치 {invalid_count}장 · 다른 크기 "
                    f"{wrong_shape_count}장 · 미완성 가격 "
                    f"{incomplete_price_count}장 · 가격 전환 "
                    f"{unstable_price_count}장 · 마지막 밝기 "
                    f"{last_luma_delta:.2f} · 구조 "
                    f"{last_edge_delta:.3f}"
                )
            return sessions, closed_samples
        except Exception as exc:
            diagnostic_frames = list(recent_guard_frames)
            if reference_guard is not None and reference_guard.size:
                diagnostic_frames.insert(0, reference_guard)
            _save_calibration_diagnostic(
                self.side_var.get(),
                "calibration_capture_failed",
                diagnostic_frames,
                price_box,
                {
                    "phase": "capture",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "completed_openings": len(sessions),
                    "expected_size": list(expected_size),
                    "price_box": list(price_box),
                    "recent_packets": recent_packets,
                },
            )
            raise
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
                    f"{RENEWAL_CALIBRATION_OPENINGS}회 독립 팝업 보정을 위해 "
                    "먼저 창 열기 구매/판매 버튼을 설정하세요.",
                    parent=self.root,
                )
                return
            frame_height, frame_width = frame.shape[:2]
            selected_x1, selected_y1, selected_x2, selected_y2 = (
                selection.to_pixels(frame_width, frame_height)
            )
            selected_price = frame[
                selected_y1:selected_y2,
                selected_x1:selected_x2,
            ]
            validation = validate_limit_price_selection(
                selected_price,
                selection,
                self.side_var.get(),
                frame_width,
                frame_height,
            )
            if not validation.valid:
                self.status_var.set("가격영역 거부 · 숫자 한 줄만 다시 선택")
                messagebox.showerror(
                    "가격영역을 다시 선택하세요",
                    validation.message,
                    parent=self.root,
                )
                return

            selection = expand_price_rect_for_recognition(
                selection,
                frame_width,
                frame_height,
            )
            x1, y1, x2, y2 = selection.to_pixels(frame_width, frame_height)
            padded_price = frame[y1:y2, x1:x2]
            padded_validation = validate_limit_price_selection(
                padded_price,
                selection,
                self.side_var.get(),
                frame_width,
                frame_height,
            )
            if not padded_validation.valid:
                self.status_var.set("가격 안전 여백 생성 실패 · 다시 선택")
                messagebox.showerror(
                    "가격영역을 다시 선택하세요",
                    padded_validation.message,
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
            samples: list[list[np.ndarray]] = []
            closed_samples: list[np.ndarray] = []
            self.status_var.set("팝업 안전 보정 중 · 창을 그대로 두세요")
            self.root.update_idletasks()
            try:
                samples, closed_samples = self._capture_guard_samples(
                    guard_rect,
                    price_box,
                    side.action_point,
                    (frame_width, frame_height),
                )
                calibration = build_calibration_result(
                    samples,
                    price_box,
                    closed_samples,
                    dynamic_limit_price_boxes(
                        price_box,
                        self.side_var.get(),
                        frame_height,
                    ),
                )
            except Exception as exc:
                captured_guards = [
                    sample
                    for opening_samples in samples
                    for sample in opening_samples
                ]
                captured_guards.extend(closed_samples)
                _save_calibration_diagnostic(
                    self.side_var.get(),
                    "calibration_validation_failed",
                    captured_guards,
                    price_box,
                    {
                        "phase": "validation",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "completed_openings": len(samples),
                        "price_box": list(price_box),
                        "frame_size": [frame_width, frame_height],
                    },
                )
                self.log(
                    f"[안전 보정 실패] {type(exc).__name__}: {exc}"
                )
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
            side.baseline_variants_png = [
                encode_gray_png(variant)
                for variant in calibration.baseline_variants
            ]
            side.guard_png = encode_gray_png(calibration.guard)
            side.closed_guard_png = encode_gray_png(calibration.closed_guard)
            side.noise_global = calibration.noise_global
            side.noise_slice = calibration.noise_slice
            side.guard_luma_noise = calibration.guard_luma_noise
            side.guard_edge_noise = calibration.guard_edge_noise
            side.closed_guard_luma_noise = (
                calibration.closed_guard_luma_noise
            )
            side.closed_guard_edge_noise = (
                calibration.closed_guard_edge_noise
            )
            side.unchanged_limit = calibration.unchanged_limit
            side.stability_limit = calibration.stability_limit
            side.registration_shift_limit = calibration.registration_shift_limit
            side.calibration_openings = calibration.calibration_openings
            side.calibration_version = RENEWAL_CALIBRATION_VERSION
            side.calibrated_frame_width = frame_width
            side.calibrated_frame_height = frame_height
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
            if self._prepare_turbo_start():
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
        self._set_running(False)
        if self._prepare_turbo_start():
            self._start_worker()

    def _prepare_turbo_start(self) -> bool:
        self._refresh_turbo_session()
        if self._selected_turbo_keys() and self._turbo_candidates:
            if not self._close_selected_turbo_apps():
                self.status_var.set("터보 앱 종료 취소")
                return False
        if not self._confirm_turbo_pressure():
            self.status_var.set("터보 목표 미달 · 시작 취소")
            return False
        return True

    def _start_worker(self) -> None:
        gc.collect()
        self.stop_event.clear()
        self._set_running(True)
        side = self.side_var.get()
        monitor_only = bool(self.monitor_only_var.get())
        target_minute = self._target_minute()
        available_ram = _available_ram_gb()
        self.status_var.set("갱신매크로 시작")
        fast_policy = (
            "전환 가격 고유 2프레임 선판정"
            if (
                self.profile.first_frame_fast_exit
                and self.profile.speed_level == 10
            )
            else "동일가격 다중 프레임 확인"
        )
        self.log(
            f"[시작 v12] {'구매/상한가' if side == 'buy' else '판매/하한가'} "
            f"속도={self.profile.speed_level}/10 "
            f"열기={self.profile.open_settle_ms}ms "
            f"명확한 변경={2 if self.profile.speed_level >= 8 else 3}프레임 "
            f"{fast_policy} · "
            f"{'무주문 측정' if monitor_only else '실주문'} · 애매하면 주문 금지"
        )
        if available_ram is not None:
            self.log(f"[터보] 사용 가능 RAM {available_ram:.1f}GB · 매크로 Above Normal")
            if available_ram < 4.0:
                self.log(
                    "[주의] 사용 가능 RAM이 4GB 미만입니다. 브라우저/Discord를 "
                    "정리하면 프레임 끊김을 줄일 수 있습니다."
                )
        self._report(True, "갱신매크로 시작")
        self.worker_thread = threading.Thread(
            target=self._worker,
            args=(side, monitor_only, target_minute),
            daemon=True,
        )
        self.worker_thread.start()

    def _worker(
        self,
        side: str,
        monitor_only: bool,
        target_minute: int,
    ) -> None:
        manager: Optional[InactiveManager] = None
        refresh_stop = threading.Event()
        completed = False
        failed = False
        try:
            if not self.naver_clock.is_fresh() and not self.naver_clock.sync():
                raise RuntimeError(
                    "네이버 시간 동기화 실패: "
                    + (self.naver_clock.last_error or "응답 없음")
                )
            window = next_schedule_window(
                self.naver_clock.now(),
                target_minute,
            )
            self.log(
                f"[시간창 v12] 목표 :{target_minute:02d} · "
                f"{window.start:%Y-%m-%d %H:%M:%S}~"
                f"{window.end_exclusive:%H:%M:%S}(미포함)"
            )
            boundary_resynced = False
            last_wait_second = -1
            while not self.stop_event.is_set():
                if not self.naver_clock.is_fresh():
                    if not self.naver_clock.sync():
                        raise RuntimeError(
                            "대기 중 네이버 시간 동기화가 만료되었습니다."
                        )
                    window = next_schedule_window(
                        self.naver_clock.now(),
                        target_minute,
                    )
                now = self.naver_clock.now()
                remaining = (window.start - now).total_seconds()
                if remaining <= 0:
                    break
                if remaining <= 15.0 and not boundary_resynced:
                    if not self.naver_clock.sync():
                        raise RuntimeError(
                            "시간창 시작 직전 네이버 재동기화에 실패했습니다."
                        )
                    window = next_schedule_window(
                        self.naver_clock.now(),
                        target_minute,
                    )
                    boundary_resynced = True
                    continue
                wait_second = int(max(0.0, remaining))
                if wait_second != last_wait_second:
                    last_wait_second = wait_second
                    self.ui_queue.put(
                        (
                            "status",
                            f"네이버 시간 대기 · {window.start:%H:%M:%S}부터 "
                            f"동작 · {wait_second}초 남음",
                        )
                    )
                self.stop_event.wait(min(0.25, max(0.01, remaining)))
            if self.stop_event.is_set():
                return
            if not self.naver_clock.is_fresh():
                raise RuntimeError("네이버 시간 정보가 만료되어 입력을 차단했습니다.")

            active_until = self.naver_clock.to_monotonic(
                window.end_exclusive
            )
            self.ui_queue.put(
                (
                    "status",
                    f"시간창 활성 · {window.end_exclusive:%H:%M:%S} 전까지 반복",
                )
            )

            def refresh_clock() -> None:
                next_refresh = time.monotonic() + NAVER_TIME_REFRESH_SECONDS
                next_retry = 0.0
                while not refresh_stop.wait(1.0):
                    now_monotonic = time.monotonic()
                    must_sync = (
                        not self.naver_clock.is_fresh()
                        or now_monotonic >= next_refresh
                    )
                    if not must_sync or now_monotonic < next_retry:
                        continue
                    if self.naver_clock.sync():
                        next_refresh = (
                            time.monotonic() + NAVER_TIME_REFRESH_SECONDS
                        )
                        next_retry = 0.0
                    else:
                        next_retry = time.monotonic() + 5.0

            threading.Thread(target=refresh_clock, daemon=True).start()
            manager = InactiveManager(
                self.window_title_var.get().strip(),
                logger=self.log,
            )
            completed = FastRenewalRunner(
                manager=manager,
                profile=self.profile,
                side=side,
                stop_event=self.stop_event,
                logger=self.log,
                status=lambda message: self.ui_queue.put(("status", message)),
                monitor_only=monitor_only,
                active_until=active_until,
                time_valid=lambda: (
                    self.naver_clock.is_fresh()
                    and self.naver_clock.now() < window.end_exclusive
                ),
            ).run()
        except Exception as exc:
            failed = True
            self.log(f"[갱신 오류] {exc}")
            self.log(traceback.format_exc())
        finally:
            refresh_stop.set()
            self.stop_event.set()
            if manager is not None:
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
        self.target_minute_spinbox.configure(
            state=tk.DISABLED if running else tk.NORMAL
        )
        self.monitor_checkbox.configure(
            state=tk.DISABLED if running else tk.NORMAL
        )
        for button in self._turbo_buttons:
            button.configure(state=tk.DISABLED if running else tk.NORMAL)
        for checkbox in self._turbo_checkboxes:
            checkbox.configure(state=tk.DISABLED if running else tk.NORMAL)

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
        if self.naver_clock.is_fresh():
            self.clock_var.set(f"네이버 {self.naver_clock.now():%H:%M:%S}")
        else:
            self.clock_var.set("네이버 --:--:--")
        self._refresh_schedule_status()
        self._request_clock_sync()
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
        _restore_process_priority(self._original_priority_class)
        self.root.destroy()


def _startup_selftest() -> int:
    """Build the complete local UI and exit without license or game input."""

    root: Optional[tk.Tk] = None
    app: Optional[RenewalApp] = None
    try:
        root = tk.Tk()
        root.withdraw()
        app = RenewalApp(root, "local-startup-selftest")
        root.update_idletasks()
        root.update()
        return 0
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        if app is not None:
            try:
                app.on_close()
            except Exception:
                pass
        elif root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def _headless_fit_wgc() -> int:
    """Verify a supported stable 1080p WGC frame, fitting when possible."""

    report_path = app_dir() / "renewal_headless_fit_wgc.json"
    report: dict[str, object] = {
        "schema_version": 1,
        "started_at_unix": time.time(),
        "passed": False,
    }
    try:
        manager = InactiveManager(WINDOW_TITLE, logger=lambda _message: None)
        if not manager.find_window() or manager.hwnd is None:
            raise RuntimeError("FC ONLINE window was not found.")
        snapshot, before_size, after_size = _fit_game_window_to_wgc(
            manager.hwnd,
        )
        report.update(
            {
                "passed": True,
                "hwnd": int(manager.hwnd),
                "wgc_size_before": list(before_size),
                "wgc_size_after": list(after_size),
                "wgc_size_mode": (
                    "preferred"
                    if after_size == TARGET_WGC_SIZE
                    else "verified_stable_fallback"
                ),
                "supported_wgc_sizes": [
                    list(size)
                    for size in sorted(SUPPORTED_RENEWAL_WGC_SIZES)
                ],
                "outer_size_before": [
                    snapshot.original.width,
                    snapshot.original.height,
                ],
                "outer_size_after": [
                    snapshot.resized.width,
                    snapshot.resized.height,
                ],
            }
        )
        return 0
    except Exception as exc:
        report.update(
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        return 1
    finally:
        report["finished_at_unix"] = time.time()
        temporary = report_path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(report_path)
        except Exception:
            pass


def _headless_monitor_existing(
    side_name: str,
    duration_seconds: float,
) -> int:
    """Run the real renewal loop with all order inputs permanently disabled.

    The report is checkpointed throughout long sessions so a crash or a true
    price change leaves actionable evidence instead of an empty ten-hour gap.
    """

    report_path = app_dir() / "renewal_headless_monitor.json"
    report: dict[str, object] = {
        "schema_version": 2,
        "side": side_name,
        "duration_requested_seconds": float(duration_seconds),
        "started_at_unix": time.time(),
        "updated_at_unix": time.time(),
        "finished": False,
        "passed": False,
        "order_inputs": 0,
        "pid": os.getpid(),
    }
    manager: Optional[InactiveManager] = None
    runner: Optional[FastRenewalRunner] = None
    stop_event = threading.Event()
    checkpoint_stop = threading.Event()
    timer: Optional[threading.Timer] = None
    checkpoint_thread: Optional[threading.Thread] = None
    logs: list[str] = []
    statuses: list[str] = []
    resource_samples: list[dict[str, float]] = []
    report_lock = threading.Lock()
    cleanup_escape_sent = False
    original_priority = _enable_safe_process_priority()
    started = time.perf_counter()
    process = psutil.Process()
    logical_cpus = max(1, int(psutil.cpu_count(logical=True) or 1))
    process.cpu_percent(interval=None)

    def sample_resources(*, force: bool = False) -> None:
        try:
            elapsed_seconds = time.perf_counter() - started
            if (
                not force
                and not _headless_resource_sample_due(
                    resource_samples,
                    elapsed_seconds,
                )
            ):
                return
            memory = process.memory_info()
            try:
                private_bytes = int(
                    getattr(process.memory_full_info(), "private")
                )
            except (AttributeError, psutil.Error):
                private_bytes = int(memory.rss)
            resource_samples.append(
                {
                    "elapsed_seconds": elapsed_seconds,
                    "cpu_system_percent": (
                        float(process.cpu_percent(interval=None))
                        / float(logical_cpus)
                    ),
                    "rss_mb": float(memory.rss) / (1024.0 * 1024.0),
                    "private_mb": float(private_bytes) / (1024.0 * 1024.0),
                }
            )
            del resource_samples[:-2000]
        except psutil.Error:
            pass

    def resource_summary() -> dict[str, object]:
        if not resource_samples:
            return {
                "samples": 0,
                "within_limits": False,
            }
        cpu_values = [
            float(sample["cpu_system_percent"])
            for sample in resource_samples[1:]
        ]
        if not cpu_values:
            cpu_values = [
                float(resource_samples[-1]["cpu_system_percent"])
            ]
        # PyInstaller extraction, WGC startup and the first OpenCV buffers are
        # one-time initialization costs, not long-session memory growth.  Use
        # the first post-startup checkpoint as the soak baseline.  A short
        # preflight still has its final sample as a conservative baseline.
        stable_samples = (
            resource_samples[1:]
            if len(resource_samples) > 1
            else resource_samples
        )
        rss_values = [
            float(sample["rss_mb"]) for sample in stable_samples
        ]
        private_values = [
            float(sample["private_mb"]) for sample in stable_samples
        ]
        cpu_average = float(np.mean(cpu_values))
        cpu_p95 = float(np.percentile(cpu_values, 95))
        rss_growth = max(rss_values) - rss_values[0]
        private_growth = max(private_values) - private_values[0]
        within_limits = bool(
            cpu_average <= HEADLESS_MONITOR_CPU_AVERAGE_LIMIT_PERCENT
            and cpu_p95 <= HEADLESS_MONITOR_CPU_P95_LIMIT_PERCENT
            and rss_growth <= HEADLESS_MONITOR_RSS_GROWTH_LIMIT_MB
            and private_growth
            <= HEADLESS_MONITOR_PRIVATE_GROWTH_LIMIT_MB
        )
        return {
            "samples": len(resource_samples),
            "sample_interval_seconds": (
                HEADLESS_MONITOR_CHECKPOINT_SECONDS
            ),
            "cpu_system_percent_average": cpu_average,
            "cpu_system_percent_p95": cpu_p95,
            "cpu_average_limit_percent": (
                HEADLESS_MONITOR_CPU_AVERAGE_LIMIT_PERCENT
            ),
            "cpu_p95_limit_percent": (
                HEADLESS_MONITOR_CPU_P95_LIMIT_PERCENT
            ),
            "startup_rss_mb": float(resource_samples[0]["rss_mb"]),
            "rss_start_mb": rss_values[0],
            "rss_peak_mb": max(rss_values),
            "rss_growth_mb": rss_growth,
            "rss_growth_limit_mb": HEADLESS_MONITOR_RSS_GROWTH_LIMIT_MB,
            "startup_private_mb": float(
                resource_samples[0]["private_mb"]
            ),
            "private_start_mb": private_values[0],
            "private_peak_mb": max(private_values),
            "private_growth_mb": private_growth,
            "private_growth_limit_mb": (
                HEADLESS_MONITOR_PRIVATE_GROWTH_LIMIT_MB
            ),
            "within_limits": within_limits,
        }

    def current_telemetry() -> dict[str, object]:
        if runner is None:
            return {}
        try:
            return dict(runner.telemetry)
        except RuntimeError:
            return {}

    def write_checkpoint(*, finished: bool) -> None:
        snapshot = dict(report)
        telemetry = current_telemetry()
        if telemetry:
            snapshot["telemetry"] = telemetry
            snapshot["order_inputs"] = int(
                telemetry.get("order_clicks", 0)
            )
            snapshot["open_failure_summary"] = (
                _headless_open_failure_summary(telemetry)
            )
        checkpoint_resources = _headless_checkpoint_resources(
            report,
            finished=finished,
            live_resources=resource_summary(),
        )
        snapshot.update(
            {
                "finished": bool(finished),
                "updated_at_unix": time.time(),
                "elapsed_seconds": time.perf_counter() - started,
                "resources": checkpoint_resources,
                "log_tail": logs[-100:],
                "status_tail": statuses[-50:],
            }
        )
        temporary = report_path.with_suffix(".tmp")
        try:
            with report_lock:
                temporary.write_text(
                    json.dumps(snapshot, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temporary.replace(report_path)
        except Exception:
            pass

    def checkpoint_worker() -> None:
        while not checkpoint_stop.wait(
            HEADLESS_MONITOR_CHECKPOINT_SECONDS
        ):
            sample_resources()
            write_checkpoint(finished=False)

    sample_resources()
    write_checkpoint(finished=False)
    try:
        if side_name not in ("buy", "sell"):
            raise ValueError("Only buy or sell can be monitored.")
        duration = min(
            HEADLESS_MONITOR_MAX_SECONDS,
            max(1.0, float(duration_seconds)),
        )
        report["duration_effective_seconds"] = duration
        profile = load_renewal_profile()
        side = profile.side(side_name)
        missing = profile.missing(side_name)
        if missing:
            raise RuntimeError(
                "Renewal calibration is incomplete: " + ", ".join(missing)
            )
        manager = InactiveManager(WINDOW_TITLE, logger=logs.append)
        runner = FastRenewalRunner(
            manager,
            profile,
            side_name,
            stop_event,
            logs.append,
            statuses.append,
            monitor_only=True,
            continuous_monitor=True,
        )
        checkpoint_thread = threading.Thread(
            target=checkpoint_worker,
            name="renewal-monitor-checkpoint",
            daemon=True,
        )
        checkpoint_thread.start()
        timer = threading.Timer(duration, stop_event.set)
        timer.daemon = True
        timer.start()
        runner.run()
        checkpoint_stop.set()
        checkpoint_thread.join(timeout=2.0)
        sample_resources()
        telemetry = current_telemetry()
        order_inputs = int(telemetry.get("order_clicks", 0))
        armed_openings = int(telemetry.get("armed_openings", 0))
        confirmed_openings = int(
            telemetry.get("confirmed_openings", 0)
        )
        max_open_failures = int(
            telemetry.get("max_consecutive_open_failures", 0)
        )
        initial_mismatch = bool(telemetry.get("initial_mismatch", False))
        minimum_confirmed_openings = min(
            1000,
            max(2, int(duration * 1.5)),
        )
        monitor_detected = bool(telemetry.get("monitor_detected", False))
        monitor_pending_change = bool(
            telemetry.get("monitor_pending_change", False)
        )
        server_pressure_detected = bool(
            telemetry.get("server_pressure_detected", False)
        )
        elapsed = time.perf_counter() - started
        duration_completed = bool(
            not monitor_detected
            and not initial_mismatch
            and elapsed >= max(0.0, duration - 0.5)
        )
        resources = resource_summary()
        open_failure_summary = _headless_open_failure_summary(telemetry)
        passed = bool(
            order_inputs == 0
            and armed_openings >= 2
            and not initial_mismatch
            and not monitor_pending_change
            and not server_pressure_detected
            and max_open_failures < 3
            and bool(open_failure_summary["within_limit"])
            and confirmed_openings >= minimum_confirmed_openings
            and duration_completed
            and bool(resources.get("within_limits", False))
        )
        report.update(
            {
                "passed": passed,
                "order_inputs": order_inputs,
                "minimum_confirmed_openings": minimum_confirmed_openings,
                "duration_completed": duration_completed,
                "telemetry": telemetry,
                "resources": resources,
                "open_failure_summary": open_failure_summary,
                "stopped_by": (
                    "server_pressure_detected"
                    if server_pressure_detected
                    else (
                        "price_change_detected"
                        if monitor_detected
                        else (
                            "initial_price_mismatch"
                            if initial_mismatch
                            else (
                                "duration"
                                if duration_completed
                                else "early_stop"
                            )
                        )
                    )
                ),
                "calibrated_frame_size": list(
                    side.calibrated_frame_size() or ()
                ),
            }
        )
        return 0 if passed else 1
    except Exception as exc:
        report.update(
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "telemetry": current_telemetry(),
            }
        )
        return 1
    finally:
        checkpoint_stop.set()
        if checkpoint_thread is not None:
            checkpoint_thread.join(timeout=2.0)
        stop_event.set()
        if timer is not None:
            timer.cancel()
        if (
            runner is not None
            and manager is not None
            and bool(runner.telemetry.get("popup_may_be_open", False))
            and not bool(
                runner.telemetry.get("current_popup_escape_sent", False)
            )
            and manager.hwnd is not None
        ):
            try:
                profile = runner.profile
                calibrated_size = profile.side(
                    side_name
                ).calibrated_frame_size()
                if calibrated_size is not None:
                    _FastClicker(
                        manager,
                        calibrated_size[0],
                        calibrated_size[1],
                    ).press_escape()
                    cleanup_escape_sent = True
            except Exception:
                pass
        if manager is not None:
            manager.stop_capture()
        if "resources" not in report:
            sample_resources(force=True)
            report["resources"] = resource_summary()
        report.update(
            {
                "finished": True,
                "elapsed_seconds": time.perf_counter() - started,
                "cleanup_escape_sent": cleanup_escape_sent,
                "log_tail": logs[-100:],
                "status_tail": statuses[-50:],
                "updated_at_unix": time.time(),
                "finished_at_unix": time.time(),
            }
        )
        write_checkpoint(finished=True)
        _restore_process_priority(original_priority)


def _headless_calibrate_existing(side_name: str) -> int:
    """Calibrate an existing right-side ROI using open/ESC only.

    This local diagnostic path deliberately has no access to the limit-price or
    final-order click coordinates.  A failed or ambiguous capture leaves the
    existing profile untouched.
    """

    report_path = app_dir() / "renewal_headless_calibration.json"
    report: dict[str, object] = {
        "schema_version": 1,
        "side": side_name,
        "started_at_unix": time.time(),
        "passed": False,
    }
    manager: Optional[InactiveManager] = None
    clicker: Optional[_FastClicker] = None
    popup_may_be_open = False
    corpus_frames: list[tuple[str, np.ndarray]] = []
    packet_metadata: list[dict[str, object]] = []
    recent_guard_frames: list[np.ndarray] = []
    capture_metrics: list[dict[str, object]] = []
    diagnostic_price_box: Optional[tuple[int, int, int, int]] = None
    initial_guard_luma: Optional[float] = None
    initial_guard_white_ratio: Optional[float] = None
    initial_is_open_popup = False
    try:
        if side_name not in ("buy", "sell"):
            raise ValueError("구매 또는 판매만 보정할 수 있습니다.")
        profile = load_renewal_profile()
        side = profile.side(side_name)
        if side.action_point is None or side.price_rect is None:
            raise RuntimeError("기존 창 열기 좌표와 가격영역이 필요합니다.")

        manager = InactiveManager(WINDOW_TITLE, logger=lambda _message: None)
        if not manager.find_window():
            raise RuntimeError("FC ONLINE 창을 찾지 못했습니다.")
        if manager.hwnd is None:
            raise RuntimeError("FC ONLINE HWND가 없습니다.")
        fit_snapshot, fit_before_size, fit_after_size = (
            _fit_game_window_to_wgc(
                manager.hwnd,
                allow_supported_fallback=True,
            )
        )
        full_frame = _wait_for_stable_wgc_frame(manager)
        frame_height, frame_width = full_frame.shape[:2]
        if not is_supported_renewal_wgc_size(frame_width, frame_height):
            raise RuntimeError(
                f"WGC 크기가 {frame_width}x{frame_height}입니다. "
                "검증된 1080p 안정 크기에서만 v10 자동 보정을 실행합니다."
            )

        original_price_rect = side.price_rect
        price_rect = _expand_headless_price_rect(
            original_price_rect,
            side_name,
            frame_width,
            frame_height,
        )
        x1, y1, x2, y2 = price_rect.to_pixels(frame_width, frame_height)
        existing_price = _calibration_selection_probe(
            side,
            full_frame[y1:y2, x1:x2],
        )
        selection_validation = validate_limit_price_selection(
            existing_price,
            price_rect,
            side_name,
            frame_width,
            frame_height,
        )
        if not selection_validation.valid:
            raise RuntimeError(selection_validation.message)

        guard_rect = build_guard_rect(
            price_rect,
            frame_width,
            frame_height,
        )
        price_box = price_box_in_guard(
            price_rect,
            guard_rect,
            frame_width,
            frame_height,
        )
        dynamic_boxes = dynamic_limit_price_boxes(
            price_box,
            side_name,
            frame_height,
        )
        diagnostic_price_box = price_box
        gx1, gy1, gx2, gy2 = guard_rect.to_pixels(
            frame_width,
            frame_height,
        )
        initial_guard = full_frame[gy1:gy2, gx1:gx2].copy()
        (
            initial_guard_luma,
            initial_guard_white_ratio,
            initial_guard_is_opaque,
        ) = _calibration_popup_opacity(initial_guard)
        initial_price = crop_price_from_guard(initial_guard, price_box)
        initial_price_validation = validate_limit_price_selection(
            initial_price,
            price_rect,
            side_name,
            frame_width,
            frame_height,
        )
        initial_is_open_popup = bool(
            initial_price_validation.valid
            and initial_guard_is_opaque
        )
        initial_state_detector = RenewalModalGuard(
            initial_guard,
            price_box,
            shift_limit=4,
            dynamic_boxes=dynamic_boxes,
        )

        engine = manager.capture_engine
        if engine is None or not hasattr(engine, "get_latest_frame_packet"):
            raise RuntimeError("WGC 고유 프레임 API를 사용할 수 없습니다.")
        clicker = _FastClicker(manager, frame_width, frame_height)
        action = clicker.resolve(side.action_point)
        last_sequence = -1
        last_timestamp = float("-inf")

        def flush() -> None:
            nonlocal last_sequence, last_timestamp
            packet = engine.get_latest_frame_packet(timeout=0.0)
            if packet is not None:
                last_sequence = max(last_sequence, int(packet.sequence_id))
                last_timestamp = max(last_timestamp, float(packet.captured_at))

        def next_full(deadline: float) -> Optional[np.ndarray]:
            nonlocal last_sequence, last_timestamp
            while time.monotonic() < deadline:
                packet = engine.get_latest_frame_packet(timeout=0.10)
                if packet is None:
                    if engine.closed_event.is_set():
                        raise RuntimeError("WGC 세션이 보정 중 종료되었습니다.")
                    continue
                if (
                    int(packet.sequence_id) <= last_sequence
                    or float(packet.captured_at) <= last_timestamp
                ):
                    continue
                last_sequence = int(packet.sequence_id)
                last_timestamp = float(packet.captured_at)
                packet_metadata.append(
                    {
                        "sequence_id": last_sequence,
                        "captured_at": last_timestamp,
                    }
                )
                del packet_metadata[:-1000]
                if packet.image.shape != full_frame.shape:
                    continue
                return packet.image
            return None

        def capture_closed(
            guard_detector: RenewalModalGuard,
            opening_number: int,
        ) -> list[np.ndarray]:
            deadline = time.monotonic() + CALIBRATION_CLOSE_TIMEOUT_SECONDS
            stable: list[np.ndarray] = []
            while time.monotonic() < deadline:
                frame = next_full(deadline)
                if frame is None:
                    break
                guard = frame[gy1:gy2, gx1:gx2].copy()
                if guard_detector.register(guard, 0.0, 0.0).valid:
                    stable.clear()
                    continue
                if stable:
                    delta = float(
                        cv2.mean(cv2.absdiff(stable[-1], guard))[0]
                    )
                    if delta > CALIBRATION_STABLE_FRAME_DELTA:
                        stable.clear()
                stable.append(guard)
                if len(stable) >= 4:
                    return stable[-4:]
            raise RuntimeError(
                f"{opening_number}번째 ESC 뒤 닫힘 화면 4프레임을 확인하지 못했습니다."
            )

        sessions: list[list[np.ndarray]] = []
        closed_samples: list[np.ndarray] = []
        guard_detector: Optional[RenewalModalGuard] = (
            initial_state_detector if initial_is_open_popup else None
        )
        popup_may_be_open = initial_is_open_popup
        flush()
        for opening in range(RENEWAL_CALIBRATION_OPENINGS):
            opening_number = opening + 1
            if opening > 0 or not initial_is_open_popup:
                clicker.click_client(action)
            deadline = time.monotonic() + CALIBRATION_OPEN_TIMEOUT_SECONDS
            session: list[np.ndarray] = []
            previous_pair = None
            previous_stable_guard = None
            while (
                len(session) < RENEWAL_CALIBRATION_FRAMES_PER_OPENING
                and time.monotonic() < deadline
            ):
                frame = next_full(deadline)
                if frame is None:
                    break
                raw_guard = frame[gy1:gy2, gx1:gx2].copy()
                recent_guard_frames.append(raw_guard.copy())
                del recent_guard_frames[:-1000]
                (
                    raw_guard_luma,
                    raw_guard_white_ratio,
                    raw_guard_is_opaque,
                ) = _calibration_popup_opacity(raw_guard)
                if raw_guard_is_opaque:
                    popup_may_be_open = True
                metric: dict[str, object] = {
                    "opening": opening_number,
                    "guard_luma": raw_guard_luma,
                    "guard_white_ratio": raw_guard_white_ratio,
                    "guard_opaque": raw_guard_is_opaque,
                    "guard_valid": False,
                    "price_valid": False,
                }
                if guard_detector is None:
                    still_initial = initial_state_detector.register(
                        raw_guard,
                        0.0,
                        0.0,
                    ).valid
                    metric["still_initial_state"] = still_initial
                    if (
                        still_initial
                        or not raw_guard_is_opaque
                    ):
                        capture_metrics.append(metric)
                        del capture_metrics[:-1000]
                        session.clear()
                        previous_pair = None
                        previous_stable_guard = None
                        continue
                    aligned_guard = raw_guard
                    metric["guard_valid"] = True
                else:
                    if not raw_guard_is_opaque:
                        capture_metrics.append(metric)
                        del capture_metrics[:-1000]
                        session.clear()
                        previous_pair = None
                        previous_stable_guard = None
                        continue
                    registration = guard_detector.register(
                        raw_guard,
                        0.0,
                        0.0,
                    )
                    if not registration.valid:
                        metric.update(
                            {
                                "guard_luma_delta": registration.luma_delta,
                                "guard_edge_delta": registration.edge_delta,
                            }
                        )
                        capture_metrics.append(metric)
                        del capture_metrics[:-1000]
                        session.clear()
                        previous_pair = None
                        previous_stable_guard = None
                        continue
                    aligned_guard = registration.aligned
                    metric.update(
                        {
                            "guard_valid": True,
                            "guard_shift": [
                                registration.shift_x,
                                registration.shift_y,
                            ],
                            "guard_luma_delta": registration.luma_delta,
                            "guard_edge_delta": registration.edge_delta,
                        }
                    )

                price = crop_price_from_guard(aligned_guard, price_box)
                price_validation = validate_limit_price_selection(
                    price,
                    price_rect,
                    side_name,
                    frame_width,
                    frame_height,
                )
                if not price_validation.valid:
                    metric["price_message"] = price_validation.message
                    capture_metrics.append(metric)
                    del capture_metrics[:-1000]
                    session.clear()
                    previous_pair = None
                    previous_stable_guard = None
                    continue
                metric["price_valid"] = True
                pair = RenewalChangeDetector.prepare_pair(price)
                guard_stability = _calibration_guard_delta(
                    previous_stable_guard,
                    aligned_guard,
                )
                pair_stability = (
                    float("inf")
                    if previous_pair is None
                    else RenewalChangeDetector.pair_stability(previous_pair, pair)
                )
                metric["guard_stability"] = (
                    None
                    if previous_stable_guard is None
                    else guard_stability
                )
                if previous_pair is None:
                    session[:] = [raw_guard]
                    metric["pair_stability"] = None
                elif (
                    pair_stability <= 0.030
                    and guard_stability <= CALIBRATION_STABLE_FRAME_DELTA
                ):
                    metric["pair_stability"] = pair_stability
                    session.append(raw_guard)
                else:
                    metric["pair_stability"] = pair_stability
                    session[:] = [raw_guard]
                previous_pair = pair
                previous_stable_guard = aligned_guard.copy()
                capture_metrics.append(metric)
                del capture_metrics[:-1000]

            if len(session) < RENEWAL_CALIBRATION_FRAMES_PER_OPENING:
                raise RuntimeError(
                    f"{opening_number}번째 팝업에서 완성된 동일 가격 "
                    f"{RENEWAL_CALIBRATION_FRAMES_PER_OPENING}프레임을 확인하지 못했습니다."
                )
            sessions.append(
                session[-RENEWAL_CALIBRATION_FRAMES_PER_OPENING:]
            )
            if guard_detector is None:
                first_guard = np.median(
                    np.stack(
                        session[-RENEWAL_CALIBRATION_FRAMES_PER_OPENING:]
                    ),
                    axis=0,
                ).astype(np.uint8)
                guard_detector = RenewalModalGuard(
                    first_guard,
                    price_box,
                    shift_limit=4,
                    dynamic_boxes=dynamic_boxes,
                )

            for index, guard in enumerate(
                session[-RENEWAL_CALIBRATION_FRAMES_PER_OPENING:],
                start=1,
            ):
                corpus_frames.append(
                    (f"open_{opening_number}_{index}", guard.copy())
                )
            clicker.press_escape()
            closed_samples = capture_closed(
                guard_detector,
                opening_number,
            )
            popup_may_be_open = False
            for index, guard in enumerate(closed_samples, start=1):
                corpus_frames.append(
                    (f"closed_{opening_number}_{index}", guard.copy())
                )
            flush()

        calibration = build_calibration_result(
            sessions,
            price_box,
            closed_samples,
            dynamic_boxes,
        )
        side.price_rect = price_rect
        side.guard_rect = guard_rect
        side.baseline_png = encode_gray_png(calibration.baseline)
        side.baseline_variants_png = [
            encode_gray_png(variant)
            for variant in calibration.baseline_variants
        ]
        side.guard_png = encode_gray_png(calibration.guard)
        side.closed_guard_png = encode_gray_png(calibration.closed_guard)
        side.noise_global = calibration.noise_global
        side.noise_slice = calibration.noise_slice
        side.guard_luma_noise = calibration.guard_luma_noise
        side.guard_edge_noise = calibration.guard_edge_noise
        side.closed_guard_luma_noise = calibration.closed_guard_luma_noise
        side.closed_guard_edge_noise = calibration.closed_guard_edge_noise
        side.unchanged_limit = calibration.unchanged_limit
        side.stability_limit = calibration.stability_limit
        side.registration_shift_limit = calibration.registration_shift_limit
        side.calibration_openings = calibration.calibration_openings
        side.calibration_version = RENEWAL_CALIBRATION_VERSION
        side.calibrated_frame_width = frame_width
        side.calibrated_frame_height = frame_height
        saved_profile = save_renewal_profile(profile)

        local_app_data = os.environ.get("LOCALAPPDATA")
        corpus_root = (
            Path(local_app_data)
            if local_app_data
            else Path.home() / "AppData" / "Local"
        )
        corpus_dir = (
            corpus_root
            / "mAuto"
            / "renewal_corpus"
            / f"{time.strftime('%Y%m%d_%H%M%S')}_{side_name}_v12"
        )
        corpus_dir.mkdir(parents=True, exist_ok=False)
        for name, guard in corpus_frames:
            cv2.imwrite(str(corpus_dir / f"{name}.png"), guard)
        (corpus_dir / "packets.json").write_text(
            json.dumps(packet_metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        report.update(
            {
                "passed": True,
                "profile": str(saved_profile),
                "corpus": str(corpus_dir),
                "frame_size": [frame_width, frame_height],
                "wgc_size_before_fit": list(fit_before_size),
                "wgc_size_after_fit": list(fit_after_size),
                "outer_size_after_fit": [
                    fit_snapshot.resized.width,
                    fit_snapshot.resized.height,
                ],
                "openings": len(sessions),
                "open_frames": sum(len(session) for session in sessions),
                "closed_frames": len(closed_samples),
                "noise_global": calibration.noise_global,
                "noise_slice": calibration.noise_slice,
                "unchanged_limit": calibration.unchanged_limit,
                "stability_limit": calibration.stability_limit,
            }
        )
        return 0
    except Exception as exc:
        if diagnostic_price_box is not None:
            accepted_open_guards = [
                guard
                for name, guard in corpus_frames
                if name.startswith("open_")
            ]
            diagnostic_guards = (
                accepted_open_guards + recent_guard_frames[-2:]
                if accepted_open_guards
                else recent_guard_frames
            )
            _save_calibration_diagnostic(
                side_name,
                "headless_calibration_failed",
                diagnostic_guards,
                diagnostic_price_box,
                {
                    "phase": "headless_existing_roi",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "packets": packet_metadata,
                    "capture_metrics": capture_metrics,
                    "initial_guard_luma": initial_guard_luma,
                    "initial_guard_white_ratio": initial_guard_white_ratio,
                    "initial_is_open_popup": initial_is_open_popup,
                },
            )
        report.update(
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "packets_seen": len(packet_metadata),
                "capture_metrics": capture_metrics,
                "initial_guard_luma": initial_guard_luma,
                "initial_guard_white_ratio": initial_guard_white_ratio,
                "initial_is_open_popup": initial_is_open_popup,
            }
        )
        return 1
    finally:
        if popup_may_be_open and clicker is not None:
            try:
                clicker.press_escape()
            except Exception:
                pass
        if manager is not None:
            manager.stop_capture()
        report["finished_at_unix"] = time.time()
        temporary = report_path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(report_path)
        except Exception:
            pass


def _startup_selftest_repeat(count: int) -> int:
    """Run the exact frozen artifact repeatedly and persist an auditable result."""

    count = min(1000, max(1, int(count)))
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--startup-selftest"]
    else:
        command = [sys.executable, str(Path(__file__).resolve()), "--startup-selftest"]
    creation_flags = (
        int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if os.name == "nt"
        else 0
    )
    child_environment = dict(os.environ)
    # PyInstaller 6.9+ otherwise treats the same executable as a worker and
    # reuses the parent's extraction directory.  The gate must exercise a
    # completely independent onefile bootstrap on every iteration.
    child_environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    child_environment["PYINSTALLER_STRICT_UNPACK_MODE"] = "1"
    elapsed_ms: list[float] = []
    return_codes: list[int] = []
    failures: list[dict[str, object]] = []
    started_at = time.time()
    for iteration in range(1, count + 1):
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10.0,
                check=False,
                creationflags=creation_flags,
                env=child_environment,
            )
            return_code = int(completed.returncode)
        except subprocess.TimeoutExpired:
            return_code = -1
        duration_ms = (time.perf_counter() - started) * 1000.0
        elapsed_ms.append(duration_ms)
        return_codes.append(return_code)
        if return_code != 0:
            failures.append(
                {
                    "iteration": iteration,
                    "return_code": return_code,
                    "elapsed_ms": duration_ms,
                }
            )

    ordered = sorted(elapsed_ms)

    def percentile(percent: float) -> float:
        if not ordered:
            return 0.0
        position = (len(ordered) - 1) * percent
        lower = int(position)
        upper = min(len(ordered) - 1, lower + 1)
        fraction = position - lower
        return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction

    report = {
        "schema_version": 1,
        "artifact": str(Path(sys.executable).resolve()),
        "started_at_unix": started_at,
        "finished_at_unix": time.time(),
        "requested_runs": count,
        "completed_runs": len(return_codes),
        "failures": failures,
        "return_codes": return_codes,
        "timing_ms": {
            "minimum": min(elapsed_ms, default=0.0),
            "median": percentile(0.50),
            "p95": percentile(0.95),
            "p99": percentile(0.99),
            "maximum": max(elapsed_ms, default=0.0),
        },
        "passed": (
            len(return_codes) == count
            and not failures
            # PyInstaller onefile extraction is intentionally reset for every
            # run. On this 16 GB machine healthy cold starts cluster around
            # 2.5 s and can cross 3.0 s without any UI initialization fault.
            and percentile(0.95) <= 3500.0
            and max(elapsed_ms, default=0.0) <= 5000.0
        ),
    }
    target = app_dir() / "renewal_startup_selftest.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)
    return 0 if bool(report["passed"]) else 1


def main() -> int:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    if "--startup-selftest" in sys.argv[1:]:
        return _startup_selftest()
    if "--headless-fit-wgc" in sys.argv[1:]:
        return _headless_fit_wgc()
    monitor_seconds = 30.0
    for argument in sys.argv[1:]:
        if argument.startswith("--headless-monitor-seconds="):
            try:
                monitor_seconds = float(argument.split("=", 1)[1])
            except ValueError:
                return 2
    for argument in sys.argv[1:]:
        if argument.startswith("--headless-monitor-existing="):
            return _headless_monitor_existing(
                argument.split("=", 1)[1].strip().lower(),
                monitor_seconds,
            )
    for argument in sys.argv[1:]:
        if argument.startswith("--verification-soak-seconds="):
            try:
                seconds = float(argument.split("=", 1)[1])
            except ValueError:
                return 2
            return run_verification_soak(
                seconds,
                app_dir() / "renewal_verification_soak.json",
            )
        if argument.startswith("--headless-calibrate-existing="):
            return _headless_calibrate_existing(argument.split("=", 1)[1])
        if argument.startswith("--startup-selftest-repeat="):
            try:
                repeat = int(argument.split("=", 1)[1])
            except ValueError:
                return 2
            return _startup_selftest_repeat(repeat)

    root = tk.Tk()
    RenewalLicenseDialog(root, app_dir())
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
