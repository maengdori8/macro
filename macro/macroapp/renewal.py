"""FC ONLINE 이적시장 갱신 감시 엔진.

OCR 대신 사용자가 지정한 가격 숫자 영역의 에지 변화만 비교합니다. 실행 중 WGC는
그 작은 영역만 grayscale로 변환하므로 전체 화면 템플릿 매칭보다 지연과 CPU 사용량이
훨씬 작습니다.
"""

from __future__ import annotations

import base64
import gc
import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

# This module is also imported directly by local verification tools, outside
# renewal_macro.py.  Apply the same single-thread numeric runtime policy before
# NumPy/OpenCV are loaded.
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import cv2
import numpy as np

from macroapp import input_message
from macroapp.capture import CapturedFrame
from macroapp.window import InactiveManager


# The recognition ROIs are tiny.  OpenCV's worker pool and IPP/OpenCL lazy
# caches cost more memory and latency than they save here, so use the
# predictable scalar/SIMD CPU path on one thread.
cv2.setUseOptimized(False)
cv2.setNumThreads(1)
# A 150x40 price ROI is far below the size where GPU transfer pays off.
# OpenCL may also allocate a large lazy cache during long runs, so pin this
# safety-critical classifier to the predictable CPU path.
cv2.ocl.setUseOpenCL(False)
if hasattr(cv2, "ipp"):
    cv2.ipp.setUseIPP(False)

LogCallback = Callable[[str], None]
StatusCallback = Callable[[str], None]
RENEWAL_PROFILE_VERSION = 9
RENEWAL_CALIBRATION_VERSION = 5
RENEWAL_CALIBRATION_OPENINGS = 5
RENEWAL_CALIBRATION_FRAMES_PER_OPENING = 4
SUPPORTED_RENEWAL_WGC_SIZES = frozenset(
    {
        (1928, 1048),
        # FC ONLINE on a 1920-wide desktop keeps an 8 px non-client frame.
        # WGC therefore settles at 1920x1040 even though GetWindowRect reports
        # 1928x1048.  Calibration remains fail-closed by binding the profile to
        # this exact stable size.
        (1920, 1040),
    }
)
WGC_SIZE_SETTLE_SECONDS = 0.50
WGC_SIZE_SETTLE_TIMEOUT_SECONDS = 4.0


def is_supported_renewal_wgc_size(width: int, height: int) -> bool:
    return (int(width), int(height)) in SUPPORTED_RENEWAL_WGC_SIZES


def wait_for_stable_wgc_frame(
    manager: InactiveManager,
    *,
    settle_seconds: float = WGC_SIZE_SETTLE_SECONDS,
    timeout_seconds: float = WGC_SIZE_SETTLE_TIMEOUT_SECONDS,
    minimum_unique_frames: int = 2,
    first_frame: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Return a unique WGC frame only after its size has stopped transitioning."""

    first = (
        first_frame
        if first_frame is not None
        else manager.capture_client_area(window_validated=True)
    )
    if first is None:
        raise RuntimeError("Could not receive the first WGC frame.")
    engine = manager.capture_engine
    if engine is None or not hasattr(engine, "get_latest_frame_packet"):
        raise RuntimeError("The unique-frame WGC API is unavailable.")

    latest = first
    candidate_size = (int(first.shape[1]), int(first.shape[0]))
    candidate_since = time.monotonic()
    candidate_frames = 1
    required_frames = max(2, int(minimum_unique_frames))
    deadline = candidate_since + max(1.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        now = time.monotonic()
        if (
            now - candidate_since >= max(0.0, float(settle_seconds))
            and candidate_frames >= required_frames
        ):
            return latest
        packet = engine.get_latest_frame_packet(
            timeout=min(0.10, max(0.0, deadline - now)),
        )
        if packet is None:
            if engine.closed_event.is_set():
                raise RuntimeError("WGC closed while its size was settling.")
            continue
        frame = packet.image
        size = (int(frame.shape[1]), int(frame.shape[0]))
        if size != candidate_size:
            candidate_size = size
            candidate_since = time.monotonic()
            candidate_frames = 1
        else:
            candidate_frames += 1
        latest = frame
    raise RuntimeError(
        f"WGC size did not settle within {timeout_seconds:.1f}s; "
        f"last size was {candidate_size[0]}x{candidate_size[1]}."
    )


@dataclass(frozen=True)
class RenewalSpeedTuning:
    open_settle_ms: int
    close_settle_ms: int
    change_threshold: float
    confirm_frames: int
    # 닫힘 확인에 필요한 연속 '목록 화면' 프레임 수. 1이면 ESC 후 다음 프레임 한 장으로
    # 즉시 재개(사이클당 최대 한 프레임 절약), 2면 전환 중간 프레임 오인에 더 보수적.
    closed_streak: int = 2
    # 세로 슬라이스 최대 변화율 임계. 숫자 '한 자리'만 바뀌어도 그 슬라이스에선 변화율이
    # 크게 나오므로(전역 비율은 자릿수가 많을수록 희석됨) 꼬리 자릿수 변화를 놓치지 않는다.
    slice_threshold: float = 0.28


# 대기(open/close settle)는 modal 점수 게이트가 전환 프레임을 이미 걸러주므로 상위
# 레벨에선 0까지 줄인다 — 게임 렌더링 속도가 실질 하한이 되도록 죽은 대기를 제거.
_RENEWAL_SPEED_PRESETS: dict[int, RenewalSpeedTuning] = {
    1: RenewalSpeedTuning(100, 80, 0.055, 3, closed_streak=2, slice_threshold=0.16),
    2: RenewalSpeedTuning(90, 70, 0.052, 3, closed_streak=2, slice_threshold=0.15),
    3: RenewalSpeedTuning(80, 60, 0.049, 3, closed_streak=2, slice_threshold=0.14),
    4: RenewalSpeedTuning(60, 45, 0.045, 3, closed_streak=2, slice_threshold=0.13),
    5: RenewalSpeedTuning(45, 30, 0.041, 3, closed_streak=2, slice_threshold=0.12),
    6: RenewalSpeedTuning(35, 22, 0.038, 3, closed_streak=1, slice_threshold=0.11),
    7: RenewalSpeedTuning(28, 16, 0.035, 3, closed_streak=1, slice_threshold=0.10),
    8: RenewalSpeedTuning(0, 0, 0.032, 2, closed_streak=1, slice_threshold=0.095),
    9: RenewalSpeedTuning(0, 0, 0.030, 2, closed_streak=1, slice_threshold=0.09),
    10: RenewalSpeedTuning(0, 0, 0.028, 2, closed_streak=1, slice_threshold=0.085),
}


def clamp_speed_level(value: object) -> int:
    try:
        return min(10, max(1, int(round(float(value)))))
    except (TypeError, ValueError):
        return 6


def renewal_speed_tuning(value: object) -> RenewalSpeedTuning:
    return _RENEWAL_SPEED_PRESETS[clamp_speed_level(value)]


def infer_speed_level(
    open_settle_ms: object,
    close_settle_ms: object,
    change_threshold: object,
    confirm_frames: object,
) -> int:
    """기존 네 개 설정과 가장 가까운 단일 속도 단계를 찾습니다."""
    try:
        current = RenewalSpeedTuning(
            int(open_settle_ms),
            int(close_settle_ms),
            float(change_threshold),
            int(confirm_frames),
        )
    except (TypeError, ValueError):
        return 6

    def distance(item: tuple[int, RenewalSpeedTuning]) -> float:
        _level, preset = item
        return (
            abs(current.open_settle_ms - preset.open_settle_ms) / 100.0
            + abs(current.close_settle_ms - preset.close_settle_ms) / 80.0
            + abs(current.change_threshold - preset.change_threshold) / 0.060
            + abs(current.confirm_frames - preset.confirm_frames)
        )

    return min(_RENEWAL_SPEED_PRESETS.items(), key=distance)[0]


@dataclass(frozen=True)
class NormalizedPoint:
    x: float
    y: float

    def to_pixel(self, width: int, height: int) -> tuple[int, int]:
        x = int(round(min(max(self.x, 0.0), 1.0) * max(0, width - 1)))
        y = int(round(min(max(self.y, 0.0), 1.0) * max(0, height - 1)))
        return x, y

    def to_dict(self) -> dict[str, float]:
        return {"x": float(self.x), "y": float(self.y)}

    @classmethod
    def from_dict(cls, value: object) -> Optional["NormalizedPoint"]:
        if not isinstance(value, dict):
            return None
        try:
            return cls(float(value["x"]), float(value["y"]))
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class NormalizedRect:
    left: float
    top: float
    right: float
    bottom: float

    def to_pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        x1 = int(round(min(max(self.left, 0.0), 1.0) * width))
        y1 = int(round(min(max(self.top, 0.0), 1.0) * height))
        x2 = int(round(min(max(self.right, 0.0), 1.0) * width))
        y2 = int(round(min(max(self.bottom, 0.0), 1.0) * height))
        x1 = max(0, min(x1, max(0, width - 1)))
        y1 = max(0, min(y1, max(0, height - 1)))
        x2 = max(x1 + 1, min(x2, width))
        y2 = max(y1 + 1, min(y2, height))
        return x1, y1, x2, y2

    def to_dict(self) -> dict[str, float]:
        return {
            "left": float(self.left),
            "top": float(self.top),
            "right": float(self.right),
            "bottom": float(self.bottom),
        }

    @classmethod
    def from_dict(cls, value: object) -> Optional["NormalizedRect"]:
        if not isinstance(value, dict):
            return None
        try:
            rect = cls(
                float(value["left"]),
                float(value["top"]),
                float(value["right"]),
                float(value["bottom"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return rect


@dataclass
class RenewalSideProfile:
    # 목록 화면에서 가격 창을 여는 구매/판매 버튼.
    action_point: Optional[NormalizedPoint] = None
    # 열린 가격 창 안에서 실제 주문을 넣는 최종 구매/판매 버튼.
    confirm_point: Optional[NormalizedPoint] = None
    price_rect: Optional[NormalizedRect] = None
    guard_rect: Optional[NormalizedRect] = None
    limit_point: Optional[NormalizedPoint] = None
    baseline_png: str = ""
    guard_png: str = ""
    closed_guard_png: str = ""
    noise_global: float = 0.0
    noise_slice: float = 0.0
    guard_luma_noise: float = 0.0
    guard_edge_noise: float = 0.0
    closed_guard_luma_noise: float = 0.0
    closed_guard_edge_noise: float = 0.0
    unchanged_limit: float = 0.035
    stability_limit: float = 0.015
    registration_shift_limit: int = 4
    calibration_openings: int = 0
    calibration_version: int = 0
    calibrated_frame_width: int = 0
    calibrated_frame_height: int = 0

    def calibrated_frame_size(self) -> Optional[tuple[int, int]]:
        if self.calibrated_frame_width <= 0 or self.calibrated_frame_height <= 0:
            return None
        return self.calibrated_frame_width, self.calibrated_frame_height

    def matches_frame_size(self, width: int, height: int) -> bool:
        return self.calibrated_frame_size() == (int(width), int(height))

    def complete(self) -> bool:
        return bool(
            self.action_point
            and self.confirm_point
            and self.price_rect
            and self.guard_rect
            and self.limit_point
            and self.baseline_png
            and self.guard_png
            and self.closed_guard_png
            and self.calibration_openings >= RENEWAL_CALIBRATION_OPENINGS
            and self.calibration_version >= RENEWAL_CALIBRATION_VERSION
            and self.calibrated_frame_size() is not None
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "action_point": self.action_point.to_dict() if self.action_point else None,
            "confirm_point": (
                self.confirm_point.to_dict() if self.confirm_point else None
            ),
            "price_rect": self.price_rect.to_dict() if self.price_rect else None,
            "guard_rect": self.guard_rect.to_dict() if self.guard_rect else None,
            "limit_point": self.limit_point.to_dict() if self.limit_point else None,
            "baseline_png": self.baseline_png,
            "guard_png": self.guard_png,
            "closed_guard_png": self.closed_guard_png,
            "noise_global": float(self.noise_global),
            "noise_slice": float(self.noise_slice),
            "guard_luma_noise": float(self.guard_luma_noise),
            "guard_edge_noise": float(self.guard_edge_noise),
            "closed_guard_luma_noise": float(self.closed_guard_luma_noise),
            "closed_guard_edge_noise": float(self.closed_guard_edge_noise),
            "unchanged_limit": float(self.unchanged_limit),
            "stability_limit": float(self.stability_limit),
            "registration_shift_limit": int(self.registration_shift_limit),
            "calibration_openings": int(self.calibration_openings),
            "calibration_version": int(self.calibration_version),
            "calibrated_frame_width": int(self.calibrated_frame_width),
            "calibrated_frame_height": int(self.calibrated_frame_height),
        }

    @classmethod
    def from_dict(cls, value: object) -> "RenewalSideProfile":
        if not isinstance(value, dict):
            return cls()
        side = cls(
            action_point=NormalizedPoint.from_dict(value.get("action_point")),
            confirm_point=NormalizedPoint.from_dict(value.get("confirm_point")),
            price_rect=NormalizedRect.from_dict(value.get("price_rect")),
            guard_rect=NormalizedRect.from_dict(value.get("guard_rect")),
            limit_point=NormalizedPoint.from_dict(value.get("limit_point")),
            baseline_png=str(value.get("baseline_png") or ""),
            guard_png=str(value.get("guard_png") or ""),
            closed_guard_png=str(value.get("closed_guard_png") or ""),
        )
        try:
            side.noise_global = min(
                0.50, max(0.0, float(value.get("noise_global", 0.0)))
            )
            side.noise_slice = min(
                1.0, max(0.0, float(value.get("noise_slice", 0.0)))
            )
            side.guard_luma_noise = min(
                255.0, max(0.0, float(value.get("guard_luma_noise", 0.0)))
            )
            side.guard_edge_noise = min(
                1.0, max(0.0, float(value.get("guard_edge_noise", 0.0)))
            )
            side.closed_guard_luma_noise = min(
                255.0,
                max(0.0, float(value.get("closed_guard_luma_noise", 0.0))),
            )
            side.closed_guard_edge_noise = min(
                1.0,
                max(0.0, float(value.get("closed_guard_edge_noise", 0.0))),
            )
            side.unchanged_limit = min(
                0.040, max(0.035, float(value.get("unchanged_limit", 0.035)))
            )
            side.stability_limit = min(
                0.030, max(0.015, float(value.get("stability_limit", 0.015)))
            )
            side.registration_shift_limit = min(
                4, max(1, int(value.get("registration_shift_limit", 4)))
            )
            side.calibration_openings = max(
                0, int(value.get("calibration_openings", 0))
            )
            side.calibration_version = max(
                0, int(value.get("calibration_version", 0))
            )
            side.calibrated_frame_width = max(
                0, int(value.get("calibrated_frame_width", 0))
            )
            side.calibrated_frame_height = max(
                0, int(value.get("calibrated_frame_height", 0))
            )
        except (TypeError, ValueError):
            side.calibration_version = 0
            side.calibrated_frame_width = 0
            side.calibrated_frame_height = 0
        return side


@dataclass
class RenewalProfile:
    re_register_point: Optional[NormalizedPoint] = None
    confirm_point: Optional[NormalizedPoint] = None
    cancel_point: Optional[NormalizedPoint] = None
    buy: RenewalSideProfile = field(default_factory=RenewalSideProfile)
    sell: RenewalSideProfile = field(default_factory=RenewalSideProfile)
    speed_level: int = 6
    change_threshold: float = 0.045
    open_settle_ms: int = 30
    close_settle_ms: int = 18
    confirm_frames: int = 2
    closed_streak: int = 1
    slice_threshold: float = 0.29
    modal_max_score: float = 0.75

    def side(self, side: str) -> RenewalSideProfile:
        return self.buy if side == "buy" else self.sell

    def apply_speed_level(self, value: object) -> None:
        self.speed_level = clamp_speed_level(value)
        tuning = renewal_speed_tuning(self.speed_level)
        self.change_threshold = tuning.change_threshold
        self.open_settle_ms = tuning.open_settle_ms
        self.close_settle_ms = tuning.close_settle_ms
        self.confirm_frames = tuning.confirm_frames
        self.closed_streak = tuning.closed_streak
        self.slice_threshold = tuning.slice_threshold

    def missing(self, side: str) -> list[str]:
        missing: list[str] = []
        side_profile = self.side(side)
        if side_profile.action_point is None:
            missing.append("창 열기 구매/판매 버튼 위치")
        if side_profile.confirm_point is None:
            missing.append("창 안 최종 구매/판매 버튼 위치")
        if (
            side_profile.price_rect is None
            or side_profile.guard_rect is None
            or not side_profile.baseline_png
            or not side_profile.guard_png
            or not side_profile.closed_guard_png
            or side_profile.calibration_openings < RENEWAL_CALIBRATION_OPENINGS
            or side_profile.calibration_version < RENEWAL_CALIBRATION_VERSION
            or side_profile.calibrated_frame_size() is None
        ):
            missing.append("v9 우측 상한가/하한가 재설정")
        if side_profile.limit_point is None:
            missing.append("상한가 위치" if side == "buy" else "하한가 위치")
        return missing

    def to_dict(self) -> dict[str, object]:
        return {
            "version": RENEWAL_PROFILE_VERSION,
            "re_register_point": (
                self.re_register_point.to_dict() if self.re_register_point else None
            ),
            "confirm_point": self.confirm_point.to_dict() if self.confirm_point else None,
            "cancel_point": self.cancel_point.to_dict() if self.cancel_point else None,
            "buy": self.buy.to_dict(),
            "sell": self.sell.to_dict(),
            "speed_level": int(self.speed_level),
            "change_threshold": float(self.change_threshold),
            "open_settle_ms": int(self.open_settle_ms),
            "close_settle_ms": int(self.close_settle_ms),
            "confirm_frames": int(self.confirm_frames),
            "closed_streak": int(self.closed_streak),
            "slice_threshold": float(self.slice_threshold),
            "modal_max_score": float(self.modal_max_score),
        }

    @classmethod
    def from_dict(cls, value: object) -> "RenewalProfile":
        if not isinstance(value, dict):
            return cls()
        buy = RenewalSideProfile.from_dict(value.get("buy"))
        sell = RenewalSideProfile.from_dict(value.get("sell"))
        profile = cls(
            re_register_point=NormalizedPoint.from_dict(value.get("re_register_point")),
            confirm_point=NormalizedPoint.from_dict(value.get("confirm_point")),
            cancel_point=NormalizedPoint.from_dict(value.get("cancel_point")),
            buy=buy,
            sell=sell,
        )
        try:
            if "speed_level" in value:
                speed_level = clamp_speed_level(value.get("speed_level"))
            else:
                speed_level = infer_speed_level(
                    value.get("open_settle_ms", 45),
                    value.get("close_settle_ms", 25),
                    value.get("change_threshold", 0.045),
                    value.get("confirm_frames", 2),
                )
            profile.apply_speed_level(speed_level)
            profile.modal_max_score = min(
                0.95, max(0.10, float(value.get("modal_max_score", 0.75)))
            )
        except (TypeError, ValueError):
            pass
        return profile


def renewal_profile_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return root / "mAuto" / "renewal_profile.json"


def load_renewal_profile(path: Optional[Path] = None) -> RenewalProfile:
    target = path or renewal_profile_path()
    try:
        return RenewalProfile.from_dict(
            json.loads(target.read_text(encoding="utf-8-sig"))
        )
    except Exception:
        return RenewalProfile()


def save_renewal_profile(profile: RenewalProfile, path: Optional[Path] = None) -> Path:
    target = path or renewal_profile_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def encode_gray_png(image: np.ndarray) -> str:
    if image is None or image.size == 0:
        raise ValueError("빈 기준 이미지는 저장할 수 없습니다.")
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("기준 이미지를 PNG로 변환하지 못했습니다.")
    return base64.b64encode(encoded.tobytes()).decode("ascii")


def decode_gray_png(encoded: str) -> np.ndarray:
    raw = base64.b64decode(encoded, validate=True)
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None or image.size == 0:
        raise ValueError("저장된 기준 가격 이미지를 읽지 못했습니다.")
    return image


@dataclass(frozen=True)
class PriceRegionValidation:
    valid: bool
    message: str
    band_count: int


def validate_price_region(image: np.ndarray) -> PriceRegionValidation:
    """가격 ROI가 숫자 한 줄만 포함하는지 보수적으로 검사합니다."""
    if image is None or image.size == 0:
        return PriceRegionValidation(False, "가격영역이 비어 있습니다.", 0)
    gray = image
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    if width < 28 or height < 12:
        return PriceRegionValidation(
            False, "가격 숫자 한 줄이 충분히 들어오도록 조금 넓게 선택하세요.", 0
        )

    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 40, 120)
    row_counts = np.count_nonzero(edges, axis=1)
    active = (row_counts >= max(3, int(round(width * 0.025)))).astype(np.uint8)
    # 쉼표·단위 글자의 짧은 세로 공백은 같은 줄로 합치되, 두 가격 줄 사이의
    # 큰 공백은 유지합니다.
    active = cv2.morphologyEx(
        active.reshape(-1, 1),
        cv2.MORPH_CLOSE,
        np.ones((5, 1), dtype=np.uint8),
    ).reshape(-1)

    bands: list[tuple[int, int]] = []
    start: Optional[int] = None
    for index, value in enumerate(active):
        if value and start is None:
            start = index
        elif not value and start is not None:
            bands.append((start, index))
            start = None
    if start is not None:
        bands.append((start, height))

    min_band_height = max(5, int(round(height * 0.10)))
    significant: list[tuple[int, int]] = []
    for top, bottom in bands:
        if bottom - top < min_band_height:
            continue
        if int(row_counts[top:bottom].sum()) < max(24, width // 2):
            continue
        significant.append((top, bottom))

    if len(significant) != 1:
        if len(significant) > 1:
            message = (
                "두 줄 이상의 글자가 포함됐습니다. 0회·다른 가격을 빼고 "
                "상한가/하한가 숫자 한 줄만 선택하세요."
            )
        else:
            message = "가격 숫자 한 줄을 찾지 못했습니다. 숫자 부분만 다시 선택하세요."
        return PriceRegionValidation(False, message, len(significant))

    band_height = significant[0][1] - significant[0][0]
    # 한 줄 주변 여백은 허용하지만, 실제 글자 높이의 두 배가 넘는 ROI는 전환 UI나
    # 다른 행이 섞일 가능성이 높아 거부합니다.
    if height > max(48, band_height * 2 + 8):
        return PriceRegionValidation(
            False,
            "세로 영역이 너무 큽니다. 가격 숫자 한 줄 높이로 더 좁게 선택하세요.",
            1,
        )
    return PriceRegionValidation(True, "가격 숫자 한 줄 확인 완료", 1)


def validate_limit_price_selection(
    image: np.ndarray,
    rect: NormalizedRect,
    side: str,
    frame_width: int,
    frame_height: int,
) -> PriceRegionValidation:
    """Accept only the right-side upper/lower limit-price row at 1080p WGC."""

    row_validation = validate_price_region(image)
    if not row_validation.valid:
        return row_validation
    if not is_supported_renewal_wgc_size(frame_width, frame_height):
        return PriceRegionValidation(
            False,
            "v9 우측 가격 보정은 검증된 1080p 안정 WGC 크기에서만 저장할 수 있습니다.",
            row_validation.band_count,
        )
    if side not in ("buy", "sell"):
        return PriceRegionValidation(
            False,
            "구매 또는 판매 구분을 먼저 선택하세요.",
            row_validation.band_count,
        )

    width = rect.right - rect.left
    height = rect.bottom - rect.top
    center_x = (rect.left + rect.right) * 0.5
    center_y = (rect.top + rect.bottom) * 0.5
    expected_y = (
        0.410 <= center_y <= 0.455
        if side == "buy"
        else 0.365 <= center_y <= 0.405
    )
    if not (
        0.700 <= center_x <= 0.835
        and expected_y
        and 0.025 <= width <= 0.180
        and 0.015 <= height <= 0.070
    ):
        target = "우측 상한가" if side == "buy" else "우측 하한가"
        return PriceRegionValidation(
            False,
            f"{target} 숫자 한 줄만 선택하세요. 금액 입력칸, 라벨, "
            "상·하한가 두 줄은 저장할 수 없습니다.",
            row_validation.band_count,
        )
    return PriceRegionValidation(
        True,
        "우측 상한가 숫자 확인 완료"
        if side == "buy"
        else "우측 하한가 숫자 확인 완료",
        1,
    )


def build_guard_rect(
    price_rect: NormalizedRect,
    frame_width: int,
    frame_height: int,
) -> NormalizedRect:
    """가격영역 주위의 작은 정적 배경을 포함하는 팝업 가드 영역을 만듭니다."""
    x1, y1, x2, y2 = price_rect.to_pixels(frame_width, frame_height)
    roi_width = x2 - x1
    roi_height = y2 - y1
    pad_x = max(36, min(96, roi_width))
    pad_y = max(14, min(48, roi_height))
    gx1 = max(0, x1 - pad_x)
    gy1 = max(0, y1 - pad_y)
    gx2 = min(frame_width, x2 + pad_x)
    gy2 = min(frame_height, y2 + pad_y)
    return NormalizedRect(
        gx1 / frame_width,
        gy1 / frame_height,
        gx2 / frame_width,
        gy2 / frame_height,
    )


def price_box_in_guard(
    price_rect: NormalizedRect,
    guard_rect: NormalizedRect,
    frame_width: int,
    frame_height: int,
) -> tuple[int, int, int, int]:
    px1, py1, px2, py2 = price_rect.to_pixels(frame_width, frame_height)
    gx1, gy1, gx2, gy2 = guard_rect.to_pixels(frame_width, frame_height)
    return (
        max(0, px1 - gx1),
        max(0, py1 - gy1),
        min(gx2 - gx1, px2 - gx1),
        min(gy2 - gy1, py2 - gy1),
    )


def dynamic_limit_price_boxes(
    price_box: tuple[int, int, int, int],
    side: str,
    frame_height: int,
) -> tuple[tuple[int, int, int, int], ...]:
    """Return both changing right-side price rows for popup registration."""

    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    x1, y1, x2, y2 = (int(value) for value in price_box)
    row_offset = max(y2 - y1 + 2, int(round(int(frame_height) * 0.038)))
    sibling = (
        (x1, y1 - row_offset, x2, y2 - row_offset)
        if side == "buy"
        else (x1, y1 + row_offset, x2, y2 + row_offset)
    )
    return (price_box, sibling)


def crop_price_from_guard(
    guard_image: np.ndarray,
    price_box: tuple[int, int, int, int],
) -> np.ndarray:
    x1, y1, x2, y2 = price_box
    if (
        guard_image is None
        or guard_image.size == 0
        or x1 < 0
        or y1 < 0
        or x2 > guard_image.shape[1]
        or y2 > guard_image.shape[0]
        or x2 <= x1
        or y2 <= y1
    ):
        raise ValueError("팝업 가드 안의 가격영역 좌표가 올바르지 않습니다.")
    return guard_image[y1:y2, x1:x2]


def _translate_image(
    image: np.ndarray,
    dx: int,
    dy: int,
    *,
    fill: Optional[int] = None,
) -> np.ndarray:
    """보간 없이 정수 픽셀만 이동해 글자 획을 변형하지 않습니다."""
    if image is None or image.size == 0:
        raise ValueError("이동할 이미지가 비어 있습니다.")
    if dx == 0 and dy == 0:
        return image
    if fill is None:
        border = np.concatenate(
            (image[0].reshape(-1), image[-1].reshape(-1), image[:, 0], image[:, -1])
        )
        fill = int(round(float(np.median(border))))
    result = np.full_like(image, int(fill))
    height, width = image.shape[:2]
    src_x1 = max(0, -dx)
    src_y1 = max(0, -dy)
    src_x2 = min(width, width - dx)
    src_y2 = min(height, height - dy)
    if src_x2 <= src_x1 or src_y2 <= src_y1:
        return result
    dst_x1 = max(0, dx)
    dst_y1 = max(0, dy)
    dst_x2 = dst_x1 + (src_x2 - src_x1)
    dst_y2 = dst_y1 + (src_y2 - src_y1)
    result[dst_y1:dst_y2, dst_x1:dst_x2] = image[
        src_y1:src_y2, src_x1:src_x2
    ]
    return result


def _edge_ratio(
    first_edges: np.ndarray,
    second_edges: np.ndarray,
    mask: Optional[np.ndarray] = None,
) -> float:
    kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    first = first_edges if mask is None else cv2.bitwise_and(first_edges, mask)
    second = second_edges if mask is None else cv2.bitwise_and(second_edges, mask)
    first_dilated = cv2.dilate(first, kernel)
    second_dilated = cv2.dilate(second, kernel)
    difference = cv2.bitwise_or(
        cv2.bitwise_and(first, cv2.bitwise_not(second_dilated)),
        cv2.bitwise_and(second, cv2.bitwise_not(first_dilated)),
    )
    union = cv2.bitwise_or(first, second)
    union_count = cv2.countNonZero(union)
    return (
        0.0
        if union_count <= 0
        else float(cv2.countNonZero(difference)) / float(union_count)
    )


class RenewalChangeDetector:
    """숫자 모양의 에지 차이 비율로 가격 변경을 판정합니다.

    정밀도 핵심 2가지:
    - 팽창(dilate) 후 비교: 리샘플/안티앨리어싱으로 에지가 1px 흔들려도 팽창된 상대
      에지 안에 들어가면 차이로 안 세서 노이즈 플로어가 크게 낮아진다(진짜 획 변화만 남음).
    - 세로 슬라이스 최대치: 전역 비율은 자릿수가 많을수록 한 자리 변화가 희석되지만,
      바뀐 자리가 속한 슬라이스에선 비율이 크게 나온다 → 꼬리 자릿수 변화도 확실히 잡음.
    """

    _SIZE = (256, 64)
    _SLICES = 12
    # 십자(cross) 커널: 상하좌우 1px만 팽창하고 대각선은 안 함 → 리샘플 흔들림(축 방향
    # 1px)은 흡수하되 사각 커널처럼 진짜 획 변화까지 뭉개지 않아 한 자리 변화 민감도 유지.
    _DILATE_KERNEL = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))

    def __init__(self, baseline: np.ndarray):
        self.baseline_pair = self.prepare_pair(baseline)
        width, height = self._SIZE
        shape = (height, width)
        self._resized_work = np.empty(shape, dtype=np.uint8)
        self._blurred_work = np.empty(shape, dtype=np.uint8)
        # Four slots keep the first and second confirmed frame intact while
        # the direct and aligned candidates are prepared.
        self._pair_slots = [
            (
                np.empty(shape, dtype=np.uint8),
                np.empty(shape, dtype=np.uint8),
            )
            for _ in range(4)
        ]
        self._pair_slot_index = 0
        self._diff_work = np.empty(shape, dtype=np.uint8)
        self._union_work = np.empty(shape, dtype=np.uint8)
        self._first_work = np.empty(shape, dtype=np.uint8)
        self._second_work = np.empty(shape, dtype=np.uint8)
        self._inverse_first_work = np.empty(shape, dtype=np.uint8)
        self._inverse_second_work = np.empty(shape, dtype=np.uint8)

    @classmethod
    def _prepare(cls, image: np.ndarray) -> np.ndarray:
        if image is None or image.size == 0:
            raise ValueError("가격 감지영역이 비어 있습니다.")
        if image.ndim == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(image, cls._SIZE, interpolation=cv2.INTER_AREA)
        blurred = cv2.GaussianBlur(resized, (3, 3), 0)
        return cv2.Canny(blurred, 45, 120)

    @classmethod
    def prepare_pair(cls, image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(에지, 팽창에지) 쌍을 만든다. 프레임을 여러 번 비교할 때 재사용해 중복 연산을 줄인다."""
        edges = cls._prepare(image)
        return edges, cv2.dilate(edges, cls._DILATE_KERNEL)

    def prepare_pair_reusable(
        self,
        image: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        if image is None or image.size == 0:
            raise ValueError("The price detection region is empty.")
        source = image
        if source.ndim == 3:
            source = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
        edges, dilated = self._pair_slots[self._pair_slot_index]
        self._pair_slot_index = (self._pair_slot_index + 1) % len(
            self._pair_slots
        )
        cv2.resize(
            source,
            self._SIZE,
            self._resized_work,
            interpolation=cv2.INTER_AREA,
        )
        cv2.GaussianBlur(
            self._resized_work,
            (3, 3),
            0,
            self._blurred_work,
        )
        cv2.Canny(self._blurred_work, 45, 120, edges)
        cv2.dilate(edges, self._DILATE_KERNEL, dilated)
        return edges, dilated

    @classmethod
    def _diff(cls, a: tuple, b: tuple) -> tuple[np.ndarray, np.ndarray, float]:
        """두 (에지,팽창) 쌍의 (diff마스크, union마스크, 전역 변화율)을 반환한다.

        변화율 = '상대 팽창 에지에 안 덮이는 에지'(진짜 이동/생성된 획)의 합집합 대비 비율.
        1px 흔들림/노이즈는 상대 팽창 에지에 흡수돼 차이로 안 센다.
        """
        a_edges, a_dil = a
        b_edges, b_dil = b
        diff = cv2.bitwise_or(
            cv2.bitwise_and(a_edges, cv2.bitwise_not(b_dil)),
            cv2.bitwise_and(b_edges, cv2.bitwise_not(a_dil)),
        )
        union = cv2.bitwise_or(a_edges, b_edges)
        union_count = cv2.countNonZero(union)
        ratio = 0.0 if union_count <= 0 else float(cv2.countNonZero(diff)) / float(union_count)
        return diff, union, ratio

    def analyze_pair(self, pair: tuple) -> tuple[float, float]:
        """준비된 (에지,팽창) 쌍을 baseline과 비교해 (전역 변화율, 슬라이스 최대 변화율)."""
        diff, union, global_ratio = self._diff_reusable(
            self.baseline_pair,
            pair,
        )
        slice_width = self._SIZE[0] // self._SLICES
        max_slice = 0.0
        for index in range(self._SLICES):
            x1 = index * slice_width
            x2 = self._SIZE[0] if index == self._SLICES - 1 else x1 + slice_width
            u = cv2.countNonZero(union[:, x1:x2])
            if u < 12:   # 빈/여백 슬라이스는 분모가 작아 비율이 튀므로 제외
                continue
            d = cv2.countNonZero(diff[:, x1:x2])
            ratio = float(d) / float(u)
            if ratio > max_slice:
                max_slice = ratio
        return global_ratio, max_slice

    def analyze_pair_global(self, pair: tuple) -> float:
        """동일 가격 빠른 경로용: 슬라이스 순회 없이 전역 변화율만 계산합니다."""

        return self._diff_reusable(self.baseline_pair, pair)[2]

    def _diff_reusable(
        self,
        a: tuple[np.ndarray, np.ndarray],
        b: tuple[np.ndarray, np.ndarray],
    ) -> tuple[np.ndarray, np.ndarray, float]:
        a_edges, a_dilated = a
        b_edges, b_dilated = b
        cv2.bitwise_not(b_dilated, self._inverse_second_work)
        cv2.bitwise_and(
            a_edges,
            self._inverse_second_work,
            self._first_work,
        )
        cv2.bitwise_not(a_dilated, self._inverse_first_work)
        cv2.bitwise_and(
            b_edges,
            self._inverse_first_work,
            self._second_work,
        )
        cv2.bitwise_or(
            self._first_work,
            self._second_work,
            self._diff_work,
        )
        cv2.bitwise_or(a_edges, b_edges, self._union_work)
        union_count = cv2.countNonZero(self._union_work)
        ratio = (
            0.0
            if union_count <= 0
            else float(cv2.countNonZero(self._diff_work))
            / float(union_count)
        )
        return self._diff_work, self._union_work, ratio

    @classmethod
    def pair_stability(cls, a: tuple, b: tuple) -> float:
        """두 프레임(둘 다 후보) 사이의 전역 변화율. 작을수록 '같은 화면이 정지'했다는 뜻."""
        return cls._diff(a, b)[2]

    def analyze(self, image: np.ndarray) -> tuple[float, float]:
        """(전역 변화율, 슬라이스 최대 변화율)을 반환합니다(단일 이미지 편의용)."""
        return self.analyze_pair(self.prepare_pair_reusable(image))

    def score(self, image: np.ndarray) -> float:
        """기존 호환용: 전역 변화율만 반환합니다(모달/목록 분류 등)."""
        return self.analyze(image)[0]


class PriceState(str, Enum):
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class PriceClassification:
    state: PriceState
    aligned: np.ndarray
    pair: tuple[np.ndarray, np.ndarray]
    global_score: float
    slice_score: float
    shift_x: int
    shift_y: int
    geometry_valid: bool
    reason: str
    fingerprint: int = 0
    fingerprint_population: int = 0


class RenewalPriceClassifier:
    """정수 픽셀 정렬 후 가격을 유지/변경/판정 불가로 분류합니다."""

    _LOCAL_SHIFT_LIMIT = 2

    def __init__(
        self,
        baseline: np.ndarray,
        unchanged_limit: float,
        stability_limit: float,
    ):
        gray = baseline
        if gray is None or gray.size == 0:
            raise ValueError("가격 기준 이미지가 비어 있습니다.")
        if gray.ndim == 3:
            gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
        self.baseline = gray.copy()
        self.detector = RenewalChangeDetector(self.baseline)
        self.unchanged_limit = min(0.040, max(0.035, float(unchanged_limit)))
        self.stability_limit = min(0.030, max(0.015, float(stability_limit)))
        self.changed_global_limit = max(0.060, self.unchanged_limit + 0.020)
        baseline_validation = validate_price_region(self.baseline)
        if not baseline_validation.valid:
            raise ValueError(baseline_validation.message)
        self.baseline_registration_edges = self.detector.baseline_pair[0]
        self.baseline_geometry = self._geometry_from_edges(
            self.baseline_registration_edges
        )
        if self.baseline_geometry is None:
            raise ValueError("가격 기준 이미지의 글자 구조가 올바르지 않습니다.")
        self.baseline_projection_gaps = self._projection_gaps(
            self.baseline_registration_edges
        )
        if self.baseline_projection_gaps is None:
            raise ValueError("가격 기준 이미지의 내부 획 구조가 올바르지 않습니다.")
        self.baseline_prepared_geometry = self._prepared_geometry(
            self.detector.baseline_pair[0]
        )
        if self.baseline_prepared_geometry is None:
            raise ValueError("가격 기준 지문을 만들지 못했습니다.")
        self.baseline_intensity_range = self._intensity_range(self.baseline)
        if self.baseline_intensity_range < 24.0:
            raise ValueError("The baseline price glyph is not fully rendered.")
        self.baseline_border_contrast = self._border_contrast(self.baseline)
        self.baseline_local_glyph_mask = self._local_glyph_mask(
            self.baseline
        )
        self.baseline_local_glyph_count = int(
            cv2.countNonZero(self.baseline_local_glyph_mask)
        )
        if self.baseline_local_glyph_count < 64:
            raise ValueError("The baseline price glyph mask is incomplete.")
        self.baseline_local_glyph_dilated = cv2.dilate(
            self.baseline_local_glyph_mask,
            RenewalChangeDetector._DILATE_KERNEL,
        )
        self._baseline_classification = PriceClassification(
            PriceState.UNCHANGED,
            self.baseline,
            self.detector.baseline_pair,
            0.0,
            0.0,
            0,
            0,
            True,
            "exact_baseline",
            0,
            int(cv2.countNonZero(self.detector.baseline_pair[1])),
        )
        self._last_raw: Optional[np.ndarray] = None
        self._last_classification: Optional[PriceClassification] = None

    @staticmethod
    def _border_contrast(image: np.ndarray) -> float:
        high = max(
            int(image[0].max()),
            int(image[-1].max()),
            int(image[:, 0].max()),
            int(image[:, -1].max()),
        )
        low = min(
            int(image[0].min()),
            int(image[-1].min()),
            int(image[:, 0].min()),
            int(image[:, -1].min()),
        )
        return float(high - low)

    @staticmethod
    def _intensity_range(image: np.ndarray) -> float:
        minimum, maximum, _minimum_point, _maximum_point = cv2.minMaxLoc(image)
        return float(maximum - minimum)

    def _render_contrast_valid(self, image: np.ndarray) -> bool:
        """Reject partially rendered/faded glyphs before they can become orders."""
        current = self._intensity_range(image)
        return (
            self.baseline_intensity_range * 0.70
            <= current
            <= self.baseline_intensity_range * 1.35
        )

    def _boundary_clear(self, image: np.ndarray) -> bool:
        return self._border_contrast(image) <= max(
            18.0,
            self.baseline_border_contrast + 8.0,
        )

    @staticmethod
    def _registration_edges(image: np.ndarray) -> np.ndarray:
        return cv2.Canny(cv2.GaussianBlur(image, (3, 3), 0), 40, 120)

    @staticmethod
    def _local_glyph_mask(image: np.ndarray) -> np.ndarray:
        """Extract dark glyph strokes after removing smooth white-background light."""

        background = cv2.GaussianBlur(
            image,
            (0, 0),
            2.0,
        )
        foreground = cv2.subtract(background, image)
        return cv2.compare(foreground, 16, cv2.CMP_GE)

    def _same_glyph_after_illumination(self, image: np.ndarray) -> bool:
        """Prove glyph equivalence without letting smooth popup light become a change."""

        current_mask = self._local_glyph_mask(image)
        current_count = int(cv2.countNonZero(current_mask))
        if current_count < 64:
            return False
        population_ratio = (
            float(current_count) / float(self.baseline_local_glyph_count)
        )
        if not 0.85 <= population_ratio <= 1.18:
            return False
        kernel = RenewalChangeDetector._DILATE_KERNEL
        current_dilated = cv2.dilate(current_mask, kernel)
        difference = cv2.bitwise_or(
            cv2.bitwise_and(
                self.baseline_local_glyph_mask,
                cv2.bitwise_not(current_dilated),
            ),
            cv2.bitwise_and(
                current_mask,
                cv2.bitwise_not(self.baseline_local_glyph_dilated),
            ),
        )
        union = cv2.bitwise_or(
            self.baseline_local_glyph_mask,
            current_mask,
        )
        union_count = int(cv2.countNonZero(union))
        if union_count <= 0:
            return False
        difference_ratio = (
            float(cv2.countNonZero(difference)) / float(union_count)
        )
        return difference_ratio <= 0.020

    @staticmethod
    def _maximum_zero_run(values: np.ndarray) -> int:
        maximum = 0
        current = 0
        for value in values:
            if int(value) == 0:
                current += 1
                maximum = max(maximum, current)
            else:
                current = 0
        return maximum

    @classmethod
    def _projection_gaps(
        cls,
        edges: np.ndarray,
    ) -> Optional[tuple[int, int]]:
        if cv2.countNonZero(edges) <= 0:
            return None
        x, y, width, height = cv2.boundingRect(edges)
        if width <= 0 or height <= 0:
            return None
        glyph_edges = edges[y : y + height, x : x + width]
        column_counts = np.count_nonzero(glyph_edges, axis=0)
        row_counts = np.count_nonzero(glyph_edges, axis=1)
        return (
            cls._maximum_zero_run(column_counts),
            cls._maximum_zero_run(row_counts),
        )

    def _projection_integrity_edges(self, edges: np.ndarray) -> bool:
        current = self._projection_gaps(edges)
        if current is None:
            return False
        baseline_columns, baseline_rows = self.baseline_projection_gaps
        current_columns, current_rows = current
        return (
            current_columns <= baseline_columns + 2
            and current_rows <= baseline_rows + 1
        )

    def _projection_integrity(self, image: np.ndarray) -> bool:
        return self._projection_integrity_edges(
            self._registration_edges(image)
        )

    @staticmethod
    def _geometry(
        image: np.ndarray,
    ) -> Optional[tuple[int, int, int, int, int]]:
        validation = validate_price_region(image)
        if not validation.valid:
            return None
        edges = RenewalPriceClassifier._registration_edges(image)
        return RenewalPriceClassifier._geometry_from_edges(edges)

    @staticmethod
    def _geometry_from_edges(
        edges: np.ndarray,
    ) -> Optional[tuple[int, int, int, int, int]]:
        count = int(cv2.countNonZero(edges))
        if count <= 0:
            return None
        x1, y1, width_box, height_box = cv2.boundingRect(edges)
        x2 = x1 + width_box
        y2 = y1 + height_box
        height, width = edges.shape[:2]
        # 글자가 ROI 경계에 직접 닿으면 열림/닫힘 전환 중 잘린 가격일 수 있습니다.
        if x1 <= 0 or y1 <= 0 or x2 >= width or y2 >= height:
            return None
        return x1, y1, x2, y2, count

    def _geometry_matches_edges(self, edges: np.ndarray) -> bool:
        current = self._geometry_from_edges(edges)
        if current is None:
            return False
        bx1, by1, bx2, by2, baseline_edges = self.baseline_geometry
        cx1, cy1, cx2, cy2, current_edges = current
        baseline_width = bx2 - bx1
        current_width = cx2 - cx1
        baseline_height = by2 - by1
        current_height = cy2 - cy1
        image_height, image_width = edges.shape[:2]
        horizontal_tolerance = max(7, int(round(image_width * 0.020)))
        left_tolerance = max(
            horizontal_tolerance,
            int(round(baseline_width * 0.070)),
        )
        vertical_tolerance = max(1, int(round(image_height * 0.030)))
        return (
            abs(cx1 - bx1) <= left_tolerance
            and abs(cx2 - bx2) <= horizontal_tolerance
            and abs(cy1 - by1) <= vertical_tolerance
            and abs(cy2 - by2) <= vertical_tolerance
            and baseline_width * 0.80 <= current_width <= baseline_width * 1.20
            and baseline_height * 0.92 <= current_height <= baseline_height * 1.10
            and baseline_edges * 0.55 <= current_edges <= baseline_edges * 1.80
        )

    def _geometry_matches(self, image: np.ndarray) -> bool:
        return self._geometry_matches_edges(self._registration_edges(image))

    @staticmethod
    def _prepared_geometry(
        edges: np.ndarray,
    ) -> Optional[tuple[int, int, int, int, int]]:
        count = int(cv2.countNonZero(edges))
        if count <= 0:
            return None
        x, y, width, height = cv2.boundingRect(edges)
        image_height, image_width = edges.shape[:2]
        if (
            x <= 0
            or y <= 0
            or x + width >= image_width
            or y + height >= image_height
        ):
            return None
        return x, y, width, height, count

    def _prepared_geometry_matches(
        self,
        pair: tuple[np.ndarray, np.ndarray],
    ) -> bool:
        current = self._prepared_geometry(pair[0])
        if current is None:
            return False
        bx, by, baseline_width, baseline_height, baseline_count = (
            self.baseline_prepared_geometry
        )
        cx, cy, current_width, current_height, current_count = current
        baseline_right = bx + baseline_width
        current_right = cx + current_width
        left_tolerance = max(8, int(round(baseline_width * 0.070)))
        return (
            abs(cx - bx) <= left_tolerance
            and abs(current_right - baseline_right) <= 7
            and abs(cy - by) <= 2
            and baseline_width * 0.80
            <= current_width
            <= baseline_width * 1.20
            and baseline_height * 0.92
            <= current_height
            <= baseline_height * 1.10
            and baseline_count * 0.55
            <= current_count
            <= baseline_count * 1.80
        )

    def align(
        self,
        image: np.ndarray,
    ) -> tuple[
        np.ndarray,
        int,
        int,
        tuple[np.ndarray, np.ndarray],
        float,
        float,
        Optional[np.ndarray],
        bool,
        float,
    ]:
        gray = image
        if gray is None or gray.size == 0:
            raise ValueError("가격 후보 이미지가 비어 있습니다.")
        if gray.ndim == 3:
            gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
        if gray.shape != self.baseline.shape:
            raise ValueError("가격 후보 이미지 크기가 기준과 다릅니다.")
        pair = self.detector.prepare_pair_reusable(gray)
        direct_global = self.detector.analyze_pair_global(pair)
        if direct_global <= self.unchanged_limit:
            return (
                gray,
                0,
                0,
                pair,
                direct_global,
                0.0,
                None,
                True,
                direct_global,
            )
        # Reuse the already prepared 256x64 edge map.  Price text is right
        # aligned, so the right anchor and vertical center yield the residual
        # raw-ROI translation without another Canny + template correlation.
        current_geometry = self._prepared_geometry(pair[0])
        if current_geometry is None:
            global_score, slice_score = self.detector.analyze_pair(pair)
            return (
                gray,
                0,
                0,
                pair,
                global_score,
                slice_score,
                None,
                False,
                direct_global,
            )
        bx, by, baseline_width, baseline_height, _baseline_count = (
            self.baseline_prepared_geometry
        )
        cx, cy, current_width, current_height, _current_count = (
            current_geometry
        )
        baseline_right = bx + baseline_width
        current_right = cx + current_width
        baseline_bottom = by + baseline_height
        current_bottom = cy + current_height
        left_dx = bx - cx
        right_dx = baseline_right - current_right
        top_dy = by - cy
        bottom_dy = baseline_bottom - current_bottom
        horizontal_prepared_limit = (
            int(
                np.ceil(
                    (self._LOCAL_SHIFT_LIMIT + 1)
                    * self.detector._SIZE[0]
                    / gray.shape[1]
                )
            )
            + 1
        )
        vertical_prepared_limit = (
            int(
                np.ceil(
                    self._LOCAL_SHIFT_LIMIT
                    * self.detector._SIZE[1]
                    / gray.shape[0]
                )
            )
            + 1
        )
        average_vertical_shift = (top_dy + bottom_dy) * 0.5
        if (
            abs(right_dx) > horizontal_prepared_limit
            or abs(top_dy - bottom_dy) > 4
            or abs(average_vertical_shift) > vertical_prepared_limit
        ):
            global_score, slice_score = self.detector.analyze_pair(pair)
            return (
                gray,
                0,
                0,
                pair,
                global_score,
                slice_score,
                None,
                False,
                direct_global,
            )
        # FC price text is right-aligned.  A genuine last-digit change may
        # alter the left edge/total glyph width, so only the stable right
        # anchor is a valid horizontal registration reference.
        prepared_dx = float(right_dx)
        prepared_dy = average_vertical_shift
        limit = self._LOCAL_SHIFT_LIMIT
        dx = min(
            limit,
            max(
                -limit,
                int(round(prepared_dx * gray.shape[1] / self.detector._SIZE[0])),
            ),
        )
        dy = min(
            limit,
            max(
                -limit,
                int(round(prepared_dy * gray.shape[0] / self.detector._SIZE[1])),
            ),
        )
        aligned = _translate_image(gray, dx, dy)
        aligned_pair = self.detector.prepare_pair_reusable(aligned)
        global_score, slice_score = self.detector.analyze_pair(aligned_pair)
        return (
            aligned,
            dx,
            dy,
            aligned_pair,
            global_score,
            slice_score,
            None,
            True,
            direct_global,
        )

    def classify(self, image: np.ndarray) -> PriceClassification:
        gray_for_cache: Optional[np.ndarray] = None
        try:
            if image is not None and image.size:
                gray_for_cache = image
                if gray_for_cache.ndim == 3:
                    gray_for_cache = cv2.cvtColor(
                        gray_for_cache,
                        cv2.COLOR_BGR2GRAY,
                    )
                if gray_for_cache.shape == self.baseline.shape:
                    if np.array_equal(gray_for_cache, self.baseline):
                        return self._baseline_classification
                    if (
                        self._last_raw is not None
                        and self._last_classification is not None
                        and np.array_equal(gray_for_cache, self._last_raw)
                    ):
                        return self._last_classification
        except Exception:
            gray_for_cache = None

        try:
            (
                aligned,
                dx,
                dy,
                pair,
                global_score,
                slice_score,
                aligned_registration_edges,
                alignment_valid,
                direct_global_score,
            ) = self.align(gray_for_cache if gray_for_cache is not None else image)
        except Exception as exc:
            empty = np.empty((0, 0), dtype=np.uint8)
            empty_pair = self.detector.baseline_pair
            classification = PriceClassification(
                PriceState.AMBIGUOUS,
                empty,
                empty_pair,
                1.0,
                1.0,
                0,
                0,
                False,
                f"invalid:{exc}",
            )
            if gray_for_cache is not None:
                self._last_raw = gray_for_cache.copy()
                self._last_classification = classification
            return classification

        # 기준과 거의 같은 에지 구조라면 완성된 기준 글자 구조도 동시에 증명됩니다.
        # 동일 가격의 대부분을 차지하는 이 경로에서는 두 번째 Canny/행 검사를 생략합니다.
        if not alignment_valid:
            geometry_valid = False
        elif global_score <= self.unchanged_limit:
            geometry_valid = self._boundary_clear(aligned)
        else:
            structural_edges = (
                aligned_registration_edges
                if aligned_registration_edges is not None
                else pair[0]
            )
            geometry_valid = (
                self._boundary_clear(aligned)
                and self._render_contrast_valid(aligned)
                and self._geometry_matches_edges(structural_edges)
                and self._prepared_geometry_matches(pair)
                and self._projection_integrity_edges(structural_edges)
            )

        illumination_same = False
        if geometry_valid and global_score > self.unchanged_limit:
            # Prove glyph identity in whichever integer registration has the
            # lower edge difference.  The right-anchor heuristic can
            # occasionally invent a residual ±1 px shift; trying both masks
            # would double the hot-path blur cost.
            proof_image = aligned
            if (
                (dx != 0 or dy != 0)
                and gray_for_cache is not None
                and direct_global_score < global_score
                and self._boundary_clear(gray_for_cache)
                and self._render_contrast_valid(gray_for_cache)
            ):
                proof_image = gray_for_cache
            illumination_same = self._same_glyph_after_illumination(
                proof_image
            )
        if not geometry_valid:
            state = PriceState.AMBIGUOUS
            reason = "incomplete_price_row"
        elif (
            global_score <= self.unchanged_limit
            or illumination_same
        ):
            state = PriceState.UNCHANGED
            reason = (
                "illumination_normalized_same_glyph"
                if illumination_same
                else "aligned_same_price"
            )
        elif global_score >= self.changed_global_limit or (
            global_score >= 0.040 and slice_score >= 0.250
        ):
            state = PriceState.CHANGED
            reason = "stable_glyph_change"
        else:
            state = PriceState.AMBIGUOUS
            reason = "gray_zone"

        classification = PriceClassification(
            state,
            aligned,
            pair,
            global_score,
            slice_score,
            dx,
            dy,
            geometry_valid,
            reason,
            0,
            int(cv2.countNonZero(pair[1])),
        )
        if gray_for_cache is not None:
            self._last_raw = gray_for_cache.copy()
            self._last_classification = classification
        return classification

    def same_candidate(
        self,
        first: PriceClassification,
        second: PriceClassification,
    ) -> bool:
        if (
            first.state is not second.state
            or first.state is PriceState.AMBIGUOUS
        ):
            return False
        if np.array_equal(first.pair[1], second.pair[1]):
            return True
        # 안티앨리어싱으로 정확 비트가 조금 달라진 경우에는 기존 팽창 비교로
        # 안전하게 되돌아가 판정 의미를 바꾸지 않습니다.
        return (
            RenewalChangeDetector.pair_stability(first.pair, second.pair)
            <= self.stability_limit
        )


@dataclass(frozen=True)
class GuardRegistration:
    valid: bool
    aligned: np.ndarray
    shift_x: int
    shift_y: int
    luma_delta: float
    edge_delta: float


class RenewalModalGuard:
    """가격 중앙을 제외한 주변 배경으로 구매/판매 팝업이 완전히 열렸는지 확인합니다."""

    _DILATE_KERNEL = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))

    def __init__(
        self,
        baseline: np.ndarray,
        price_box: tuple[int, int, int, int],
        shift_limit: int = 4,
        dynamic_boxes: Optional[
            tuple[tuple[int, int, int, int], ...]
        ] = None,
    ):
        if baseline is None or baseline.size == 0:
            raise ValueError("팝업 가드 기준 이미지가 비어 있습니다.")
        if baseline.ndim == 3:
            baseline = cv2.cvtColor(baseline, cv2.COLOR_BGR2GRAY)
        self.baseline = baseline.copy()
        self.shift_limit = min(4, max(1, int(shift_limit)))
        self._last_shift = (0, 0)
        self.mask = np.full(self.baseline.shape, 255, dtype=np.uint8)
        boxes = tuple(dynamic_boxes or (price_box,))
        if price_box not in boxes:
            boxes = (price_box, *boxes)
        x1 = min(int(box[0]) for box in boxes)
        y1 = min(int(box[1]) for box in boxes)
        x2 = max(int(box[2]) for box in boxes)
        y2 = max(int(box[3]) for box in boxes)
        margin = 4
        for box_left, box_top, box_right, box_bottom in boxes:
            self.mask[
                max(0, int(box_top) - margin) : min(
                    self.mask.shape[0],
                    int(box_bottom) + margin,
                ),
                max(0, int(box_left) - margin) : min(
                    self.mask.shape[1],
                    int(box_right) + margin,
                ),
            ] = 0
        # Registration exposes synthetic fill pixels along the crop boundary.
        # They are not popup evidence, so exclude the entire possible shift rim.
        rim = self.shift_limit + 1
        self.mask[:rim, :] = 0
        self.mask[-rim:, :] = 0
        self.mask[:, :rim] = 0
        self.mask[:, -rim:] = 0
        if cv2.countNonZero(self.mask) < 64:
            raise ValueError("팝업 가드 여백이 너무 작습니다.")
        self.baseline_edges = cv2.bitwise_and(
            cv2.Canny(cv2.GaussianBlur(self.baseline, (3, 3), 0), 40, 120),
            self.mask,
        )
        self.baseline_dilated = cv2.dilate(
            self.baseline_edges, self._DILATE_KERNEL
        )
        self._baseline_float = self.baseline.astype(np.float32)
        unit_shift_scores = [
            self._registration_luma(
                _translate_image(self.baseline, dx, dy)
            )
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))
        ]
        unit_local_scores = [
            self._local_registration_luma(
                _translate_image(self.baseline, dx, dy)
            )
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1))
        ]
        # A direct/cached match must remain well below the weakest visible
        # one-pixel translation of this exact guard.  This prevents measured
        # brightness noise from ever widening the shortcut enough to swallow
        # real popup movement.
        self._direct_spatial_limit = max(
            0.20,
            min(1.50, min(unit_shift_scores) * 0.45),
        )
        self._local_spatial_limit = max(
            0.20,
            min(1.00, min(unit_local_scores) * 0.55),
        )
        self._registration_templates: list[
            tuple[int, int, int, int, np.ndarray]
        ] = []
        static_margin = self.shift_limit + 4
        height, width = self.baseline.shape
        candidate_rects = (
            (
                rim,
                rim,
                width - rim,
                max(rim + 8, y1 - static_margin),
            ),
            (
                rim,
                min(height - rim - 8, y2 + static_margin),
                width - rim,
                height - rim,
            ),
            (
                rim,
                rim,
                max(rim + 8, x1 - static_margin),
                height - rim,
            ),
            (
                min(width - rim - 8, x2 + static_margin),
                rim,
                width - rim,
                height - rim,
            ),
        )
        full_edges = cv2.Canny(
            cv2.GaussianBlur(self.baseline, (3, 3), 0),
            40,
            120,
        )
        for left, top, right, bottom in candidate_rects:
            if (
                left < self.shift_limit
                or top < self.shift_limit
                or right + self.shift_limit > width
                or bottom + self.shift_limit > height
                or right - left < 8
                or bottom - top < 8
            ):
                continue
            template = full_edges[top:bottom, left:right]
            if cv2.countNonZero(template) < 8:
                continue
            self._registration_templates.append(
                (left, top, right, bottom, template)
            )

    def _registration_luma(self, gray: np.ndarray) -> float:
        """Return spatial error after removing a uniform brightness offset."""
        difference = gray.astype(np.float32) - self._baseline_float
        brightness_offset = float(cv2.mean(difference, mask=self.mask)[0])
        spatial_difference = np.abs(difference - brightness_offset)
        return float(cv2.mean(spatial_difference, mask=self.mask)[0])

    def _local_registration_luma(self, gray: np.ndarray) -> float:
        """Remove only smooth illumination drift, retaining spatial structure."""

        difference = gray.astype(np.float32) - self._baseline_float
        illumination = cv2.GaussianBlur(
            difference,
            (0, 0),
            2.0,
        )
        residual = np.abs(difference - illumination)
        return float(cv2.mean(residual, mask=self.mask)[0])

    def _spatial_match(
        self,
        gray: np.ndarray,
        raw_spatial: float,
        luma_delta: float,
        edge_delta: float,
    ) -> bool:
        if raw_spatial <= self._direct_spatial_limit:
            return True
        # A smooth white-popup render drift is allowed only when the static
        # edge structure is virtually exact.  Position shifts and a different
        # popup therefore cannot enter through this illumination-only path.
        return bool(
            luma_delta <= 10.0
            and edge_delta <= 0.03
            and self._local_registration_luma(gray)
            <= self._local_spatial_limit
        )

    def _template_shift(
        self,
        candidate_edges: np.ndarray,
    ) -> Optional[tuple[int, int]]:
        if not self._registration_templates:
            return None
        combined: Optional[np.ndarray] = None
        limit = self.shift_limit
        for left, top, right, bottom, template in (
            self._registration_templates
        ):
            search = candidate_edges[
                top - limit : bottom + limit,
                left - limit : right + limit,
            ]
            scores = cv2.matchTemplate(
                search,
                template,
                cv2.TM_SQDIFF_NORMED,
            )
            if not np.all(np.isfinite(scores)):
                return None
            if combined is None:
                combined = scores
            else:
                combined = cv2.add(combined, scores)
        if combined is None:
            return None
        minimum_location = cv2.minMaxLoc(combined)[2]
        return (
            limit - int(minimum_location[0]),
            limit - int(minimum_location[1]),
        )

    def metrics(self, image: np.ndarray) -> tuple[float, float]:
        if image is None or image.size == 0:
            return 255.0, 1.0
        gray = image
        if gray.ndim == 3:
            gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
        if gray.shape != self.baseline.shape:
            return 255.0, 1.0
        absolute = cv2.absdiff(self.baseline, gray)
        luma_delta = float(cv2.mean(absolute, mask=self.mask)[0])
        edges = cv2.bitwise_and(
            cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 40, 120),
            self.mask,
        )
        dilated = cv2.dilate(edges, self._DILATE_KERNEL)
        difference = cv2.bitwise_or(
            cv2.bitwise_and(
                self.baseline_edges, cv2.bitwise_not(dilated)
            ),
            cv2.bitwise_and(
                edges, cv2.bitwise_not(self.baseline_dilated)
            ),
        )
        union = cv2.bitwise_or(self.baseline_edges, edges)
        union_count = cv2.countNonZero(union)
        edge_delta = (
            0.0
            if union_count <= 0
            else float(cv2.countNonZero(difference)) / float(union_count)
        )
        return luma_delta, edge_delta

    def register(
        self,
        image: np.ndarray,
        luma_noise: float,
        edge_noise: float,
    ) -> GuardRegistration:
        if image is None or image.size == 0:
            return GuardRegistration(
                False,
                np.empty((0, 0), dtype=np.uint8),
                0,
                0,
                255.0,
                1.0,
            )
        gray = image
        if gray.ndim == 3:
            gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
        if gray.shape != self.baseline.shape:
            return GuardRegistration(False, gray, 0, 0, 255.0, 1.0)
        luma_limit = max(12.0, luma_noise * 4.0 + 2.0)
        edge_limit = max(0.10, edge_noise * 4.0 + 0.02)
        registered_edge_limit = max(0.20, edge_limit)

        # 대부분의 정상 프레임은 이동이 없습니다. 먼저 0px을 검사해 81개 정렬
        # 후보 탐색을 피하고, 불일치일 때만 ±shift_limit 정밀 탐색으로 내려갑니다.
        direct_luma, direct_edge = self.metrics(gray)
        direct_spatial = self._registration_luma(gray)
        if (
            self._spatial_match(
                gray,
                direct_spatial,
                direct_luma,
                direct_edge,
            )
            and direct_luma <= luma_limit
            and direct_edge <= registered_edge_limit
        ):
            self._last_shift = (0, 0)
            return GuardRegistration(
                True,
                gray,
                0,
                0,
                direct_luma,
                direct_edge,
            )

        cached_dx, cached_dy = self._last_shift
        if cached_dx != 0 or cached_dy != 0:
            cached_aligned = _translate_image(
                gray,
                cached_dx,
                cached_dy,
            )
            cached_luma, cached_edge = self.metrics(cached_aligned)
            cached_spatial = self._registration_luma(cached_aligned)
            if (
                self._spatial_match(
                    cached_aligned,
                    cached_spatial,
                    cached_luma,
                    cached_edge,
                )
                and cached_luma <= luma_limit
                and cached_edge <= registered_edge_limit
            ):
                return GuardRegistration(
                    True,
                    cached_aligned,
                    cached_dx,
                    cached_dy,
                    cached_luma,
                    cached_edge,
                )

        candidate_edges = cv2.Canny(
            cv2.GaussianBlur(gray, (3, 3), 0),
            40,
            120,
        )
        candidate_dilated = cv2.dilate(
            candidate_edges,
            self._DILATE_KERNEL,
        )
        template_shift = self._template_shift(candidate_edges)
        if template_shift is not None:
            template_dx, template_dy = template_shift
            template_aligned = _translate_image(
                gray,
                template_dx,
                template_dy,
            )
            template_spatial = self._registration_luma(
                template_aligned
            )
            template_luma, template_edge = self.metrics(
                template_aligned
            )
            if (
                self._spatial_match(
                    template_aligned,
                    template_spatial,
                    template_luma,
                    template_edge,
                )
                and template_luma <= luma_limit
                and template_edge <= registered_edge_limit
            ):
                self._last_shift = (template_dx, template_dy)
                return GuardRegistration(
                    True,
                    template_aligned,
                    template_dx,
                    template_dy,
                    template_luma,
                    template_edge,
                )
        # Edge overlap alone can prefer a diagonal near-match when the whole
        # popup moved horizontally.  Choose by brightness-normalized spatial
        # error first, then raw luminance and edge structure.  This preserves
        # the exact inverse translation through reopen jitter and uniform
        # brightness changes.
        best: Optional[
            tuple[float, float, float, int, int, int]
        ] = None
        for dy in range(-self.shift_limit, self.shift_limit + 1):
            for dx in range(-self.shift_limit, self.shift_limit + 1):
                shifted_gray = _translate_image(gray, dx, dy)
                shifted_spatial = self._registration_luma(shifted_gray)
                absolute = cv2.absdiff(self.baseline, shifted_gray)
                shifted_luma = float(cv2.mean(absolute, mask=self.mask)[0])
                shifted_edges = cv2.bitwise_and(
                    _translate_image(candidate_edges, dx, dy, fill=0),
                    self.mask,
                )
                shifted_dilated = cv2.bitwise_and(
                    _translate_image(candidate_dilated, dx, dy, fill=0),
                    self.mask,
                )
                difference = cv2.bitwise_or(
                    cv2.bitwise_and(
                        self.baseline_edges,
                        cv2.bitwise_not(shifted_dilated),
                    ),
                    cv2.bitwise_and(
                        shifted_edges,
                        cv2.bitwise_not(self.baseline_dilated),
                    ),
                )
                union = cv2.bitwise_or(self.baseline_edges, shifted_edges)
                union_count = cv2.countNonZero(union)
                shifted_edge = (
                    0.0
                    if union_count <= 0
                    else float(cv2.countNonZero(difference))
                    / float(union_count)
                )
                candidate = (
                    shifted_spatial,
                    shifted_luma,
                    shifted_edge,
                    abs(dx) + abs(dy),
                    dx,
                    dy,
                )
                if best is None or candidate < best:
                    best = candidate
        assert best is not None
        _spatial, _luma, _edge, _distance, dx, dy = best
        aligned = _translate_image(gray, dx, dy)
        aligned_spatial = self._registration_luma(aligned)
        luma_delta, edge_delta = self.metrics(aligned)
        # Registration discards a narrow border, so a valid ±4 px translation
        # naturally has more unmatched edge pixels than a zero-shift frame.
        # Luminance still remains strict and prevents a different popup from
        # passing this relaxed registered-edge ceiling.
        valid = (
            self._spatial_match(
                aligned,
                aligned_spatial,
                luma_delta,
                edge_delta,
            )
            and luma_delta <= luma_limit
            and edge_delta <= registered_edge_limit
        )
        if valid:
            self._last_shift = (dx, dy)
        return GuardRegistration(
            valid,
            aligned,
            dx,
            dy,
            luma_delta,
            edge_delta,
        )

    def matches(
        self,
        image: np.ndarray,
        luma_noise: float,
        edge_noise: float,
    ) -> bool:
        return self.register(image, luma_noise, edge_noise).valid


@dataclass(frozen=True)
class RenewalCalibrationResult:
    baseline: np.ndarray
    guard: np.ndarray
    closed_guard: np.ndarray
    noise_global: float
    noise_slice: float
    guard_luma_noise: float
    guard_edge_noise: float
    closed_guard_luma_noise: float
    closed_guard_edge_noise: float
    unchanged_limit: float
    stability_limit: float
    registration_shift_limit: int
    calibration_openings: int


def build_calibration_result(
    guard_sessions: list[list[np.ndarray]],
    price_box: tuple[int, int, int, int],
    closed_samples: Optional[list[np.ndarray]] = None,
    dynamic_boxes: Optional[
        tuple[tuple[int, int, int, int], ...]
    ] = None,
) -> RenewalCalibrationResult:
    def is_safe_calibration_match(
        classification: PriceClassification,
    ) -> bool:
        # Runtime remains fail-closed: gray-zone frames never authorize an
        # order.  During calibration, however, a complete glyph row that is
        # below the real-change thresholds is useful as a same-price noise
        # sample instead of making all five openings impossible to save.
        return (
            classification.state is PriceState.UNCHANGED
            or (
                classification.state is PriceState.AMBIGUOUS
                and classification.reason == "gray_zone"
            )
        )

    if len(guard_sessions) < RENEWAL_CALIBRATION_OPENINGS:
        raise ValueError(
            f"안전 보정에는 서로 다른 팝업 {RENEWAL_CALIBRATION_OPENINGS}회가 필요합니다."
        )
    if any(
        len(session) < RENEWAL_CALIBRATION_FRAMES_PER_OPENING
        for session in guard_sessions
    ):
        raise ValueError(
            f"팝업마다 새 WGC 프레임 {RENEWAL_CALIBRATION_FRAMES_PER_OPENING}장이 필요합니다."
        )
    guard_samples = [
        sample
        for session in guard_sessions[:RENEWAL_CALIBRATION_OPENINGS]
        for sample in session[:RENEWAL_CALIBRATION_FRAMES_PER_OPENING]
    ]
    shape = guard_samples[0].shape
    if any(sample is None or sample.shape != shape for sample in guard_samples):
        raise ValueError("보정 프레임의 크기가 서로 다릅니다.")
    if closed_samples is None or len(closed_samples) < 4:
        raise ValueError("v9 보정에는 팝업이 완전히 닫힌 새 WGC 프레임 4장이 필요합니다.")
    closed_samples = closed_samples[:4]
    if any(
        sample is None or sample.shape != shape
        for sample in closed_samples
    ):
        raise ValueError("닫힌 화면 보정 프레임의 크기가 서로 다릅니다.")

    # The capture phase admits a popup only after four complete, same-price
    # frames and registers later openings against their median.  Use that same
    # robust first-opening reference here; a single transition-adjacent frame
    # must not become the permanent spatial authority.
    reference_guard = np.median(
        np.stack(
            guard_sessions[0][:RENEWAL_CALIBRATION_FRAMES_PER_OPENING]
        ),
        axis=0,
    ).astype(np.uint8)
    reference_price = crop_price_from_guard(reference_guard, price_box)
    validation = validate_price_region(reference_price)
    if not validation.valid:
        raise ValueError(validation.message)

    # 서로 다른 팝업 열기에서 생기는 1~4px 위치 흔들림을 먼저 제거합니다.
    reference_guard_detector = RenewalModalGuard(
        reference_guard,
        price_box,
        shift_limit=4,
        dynamic_boxes=dynamic_boxes,
    )
    aligned_guards: list[np.ndarray] = []
    guard_shifts: list[int] = []
    for sample in guard_samples:
        registration = reference_guard_detector.register(sample, 0.0, 0.0)
        if not registration.valid:
            raise ValueError(
                "보정 중 다른 화면이나 완성되지 않은 팝업이 감지됐습니다. 다시 설정하세요."
            )
        aligned_guards.append(registration.aligned)
        guard_shifts.append(
            max(abs(registration.shift_x), abs(registration.shift_y))
        )

    price_candidates = [
        crop_price_from_guard(guard_sample, price_box)
        for guard_sample in aligned_guards
    ]
    # The first WGC frame after a popup transition can be a valid but noisier
    # anti-aliased render.  Pick the medoid-like candidate that agrees with the
    # most complete same-price rows instead of making that first frame the
    # permanent calibration authority.
    reference_classifier: Optional[RenewalPriceClassifier] = None
    reference_price = price_candidates[0]
    best_reference_key: Optional[tuple[int, float, int]] = None
    for candidate_index, candidate in enumerate(price_candidates):
        try:
            candidate_classifier = RenewalPriceClassifier(
                candidate,
                unchanged_limit=0.035,
                stability_limit=0.015,
            )
        except ValueError:
            continue
        unsafe_count = 0
        total_score = 0.0
        for other in price_candidates:
            candidate_match = candidate_classifier.classify(other)
            if not is_safe_calibration_match(candidate_match):
                unsafe_count += 1
            total_score += float(candidate_match.global_score)
        candidate_key = (unsafe_count, total_score, candidate_index)
        if best_reference_key is None or candidate_key < best_reference_key:
            best_reference_key = candidate_key
            reference_classifier = candidate_classifier
            reference_price = candidate
    if reference_classifier is None:
        raise ValueError(
            "완전하게 표시된 상한가/하한가 숫자 한 줄을 찾지 못했습니다. "
            "우측 가격 숫자만 조금 넓게 다시 선택하세요."
        )

    aligned_prices: list[np.ndarray] = []
    for sample_index, guard_sample in enumerate(aligned_guards):
        price = crop_price_from_guard(guard_sample, price_box)
        classification = reference_classifier.classify(price)
        if not is_safe_calibration_match(classification):
            opening_number = (
                sample_index // RENEWAL_CALIBRATION_FRAMES_PER_OPENING
            ) + 1
            frame_number = (
                sample_index % RENEWAL_CALIBRATION_FRAMES_PER_OPENING
            ) + 1
            raise ValueError(
                f"보정 {opening_number}/"
                f"{RENEWAL_CALIBRATION_OPENINGS}의 {frame_number}번째 "
                "프레임에서 우측 가격 숫자 구조가 달라졌습니다. "
                f"(판정 {classification.reason}, 전역 "
                f"{classification.global_score:.4f}, 한 자리 "
                f"{classification.slice_score:.4f})"
            )
        aligned_prices.append(classification.aligned)

    guard = np.median(np.stack(aligned_guards), axis=0).astype(np.uint8)
    baseline = np.median(np.stack(aligned_prices), axis=0).astype(np.uint8)
    validation = validate_price_region(baseline)
    if not validation.valid:
        raise ValueError(validation.message)

    classifier = RenewalPriceClassifier(
        baseline,
        unchanged_limit=0.040,
        stability_limit=0.030,
    )
    guard_detector = RenewalModalGuard(
        guard,
        price_box,
        shift_limit=4,
        dynamic_boxes=dynamic_boxes,
    )
    closed_guard = np.median(np.stack(closed_samples), axis=0).astype(np.uint8)
    closed_guard_detector = RenewalModalGuard(
        closed_guard,
        price_box,
        shift_limit=4,
        dynamic_boxes=dynamic_boxes,
    )
    global_scores: list[float] = []
    slice_scores: list[float] = []
    pair_scores: list[float] = []
    luma_scores: list[float] = []
    edge_scores: list[float] = []
    closed_luma_scores: list[float] = []
    closed_edge_scores: list[float] = []
    previous: Optional[PriceClassification] = None
    for sample_index, sample in enumerate(aligned_guards):
        price = crop_price_from_guard(sample, price_box)
        classification = classifier.classify(price)
        if not is_safe_calibration_match(classification):
            opening_number = (
                sample_index // RENEWAL_CALIBRATION_FRAMES_PER_OPENING
            ) + 1
            frame_number = (
                sample_index % RENEWAL_CALIBRATION_FRAMES_PER_OPENING
            ) + 1
            raise ValueError(
                f"독립 팝업 {opening_number}/"
                f"{RENEWAL_CALIBRATION_OPENINGS}의 {frame_number}번째 "
                "우측 가격이 기준과 일치하지 않습니다. "
                f"(판정 {classification.reason}, 전역 "
                f"{classification.global_score:.4f}, 한 자리 "
                f"{classification.slice_score:.4f})"
            )
        luma_score, edge_score = guard_detector.metrics(sample)
        global_scores.append(classification.global_score)
        slice_scores.append(classification.slice_score)
        if previous is not None:
            pair_scores.append(
                RenewalChangeDetector.pair_stability(
                    previous.pair,
                    classification.pair,
                )
            )
        previous = classification
        luma_scores.append(luma_score)
        edge_scores.append(edge_score)

    for sample in closed_samples:
        if guard_detector.register(sample, 0.0, 0.0).valid:
            raise ValueError("닫힌 화면과 열린 팝업이 구분되지 않습니다. 가격영역을 다시 설정하세요.")
        registration = closed_guard_detector.register(sample, 0.0, 0.0)
        if not registration.valid:
            raise ValueError("팝업 닫힘 화면이 안정되지 않았습니다. 다시 설정하세요.")
        closed_luma_scores.append(registration.luma_delta)
        closed_edge_scores.append(registration.edge_delta)
    if any(
        closed_guard_detector.register(sample, 0.0, 0.0).valid
        for sample in aligned_guards
    ):
        raise ValueError(
            "열린 팝업과 닫힘 화면이 구분되지 않습니다. "
            "가격영역을 더 넓은 주변 배경과 함께 다시 설정하세요."
        )

    if max(luma_scores) > 10.0 or max(edge_scores) > 0.08:
        raise ValueError("보정 중 팝업 화면이 바뀌었습니다. 창을 연 상태로 다시 설정하세요.")

    same_p99 = float(np.percentile(global_scores, 99))
    pair_p99 = float(np.percentile(pair_scores or [0.0], 99))
    unchanged_limit = min(0.040, max(0.035, same_p99 + 0.005))
    stability_limit = min(0.030, max(0.015, pair_p99 + 0.005))
    # Runtime must tolerate a later reopen moving farther than the five
    # calibration openings.  The safety plan fixes the guard search at ±4 px.
    registration_shift_limit = 4

    return RenewalCalibrationResult(
        baseline=baseline.copy(),
        guard=guard,
        closed_guard=closed_guard,
        noise_global=max(global_scores),
        noise_slice=max(slice_scores),
        guard_luma_noise=max(luma_scores),
        guard_edge_noise=max(edge_scores),
        closed_guard_luma_noise=max(closed_luma_scores),
        closed_guard_edge_noise=max(closed_edge_scores),
        unchanged_limit=unchanged_limit,
        stability_limit=stability_limit,
        registration_shift_limit=registration_shift_limit,
        calibration_openings=RENEWAL_CALIBRATION_OPENINGS,
    )


def save_renewal_diagnostic(
    side: str,
    reason: str,
    baseline: np.ndarray,
    frames: list[np.ndarray],
    metadata: dict[str, object],
    *,
    aligned_frames: Optional[list[np.ndarray]] = None,
    guard_frames: Optional[list[np.ndarray]] = None,
) -> None:
    """차단 또는 주문 판정의 직전 프레임을 제한적으로 저장합니다.

    정상 유지 반복에서는 파일을 만들지 않으며 최근 50건만 남깁니다. 진단 저장 실패는
    주문 안전성이나 실행 흐름에 영향을 주지 않습니다.
    """
    try:
        local_app_data = os.environ.get("LOCALAPPDATA")
        root = (
            Path(local_app_data)
            if local_app_data
            else Path.home() / "AppData" / "Local"
        )
        diagnostic_root = root / "mAuto" / "renewal_diagnostics"
        diagnostic_root.mkdir(parents=True, exist_ok=True)
        stamp = f"{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 1_000_000_000:09d}"
        event_dir = diagnostic_root / f"{stamp}_{side}_{reason}"
        event_dir.mkdir()
        cv2.imwrite(str(event_dir / "baseline.png"), baseline)
        for index, frame in enumerate(frames[-2:], start=1):
            if frame is not None and frame.size:
                cv2.imwrite(str(event_dir / f"candidate_{index}.png"), frame)
        for index, frame in enumerate((aligned_frames or [])[-2:], start=1):
            if frame is not None and frame.size:
                cv2.imwrite(str(event_dir / f"aligned_{index}.png"), frame)
                if frame.shape == baseline.shape:
                    detector = RenewalChangeDetector(baseline)
                    difference, _union, _ratio = detector._diff(
                        detector.baseline_pair,
                        detector.prepare_pair(frame),
                    )
                    cv2.imwrite(
                        str(event_dir / f"mask_{index}.png"),
                        difference,
                    )
        diagnostic_guards = list(guard_frames or [])
        if diagnostic_guards:
            first_guard = diagnostic_guards[0]
            if first_guard is not None and first_guard.size:
                cv2.imwrite(
                    str(event_dir / "guard_baseline.png"),
                    first_guard,
                )
        for index, frame in enumerate(diagnostic_guards[-2:], start=1):
            if frame is not None and frame.size:
                cv2.imwrite(str(event_dir / f"guard_{index}.png"), frame)
        (event_dir / "metrics.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        event_dirs = sorted(
            (path for path in diagnostic_root.iterdir() if path.is_dir()),
            key=lambda path: path.name,
        )
        for old_dir in event_dirs[:-50]:
            for child in old_dir.iterdir():
                if child.is_file():
                    child.unlink(missing_ok=True)
            old_dir.rmdir()
    except Exception:
        pass


class _FastClicker:
    """기존 일반 클릭의 hover 대기를 생략한 갱신 전용 PostMessage 입력."""

    def __init__(self, manager: InactiveManager, width: int, height: int):
        if manager.hwnd is None:
            raise RuntimeError("대상 창 HWND가 없습니다.")
        self.manager = manager
        self.hwnd = int(manager.hwnd)
        self.width = int(width)
        self.height = int(height)

    def resolve(self, point: NormalizedPoint) -> tuple[int, int]:
        wgc_x, wgc_y = point.to_pixel(self.width, self.height)
        client = self.manager.wgc_to_client(wgc_x, wgc_y)
        if client is None:
            raise RuntimeError(f"클릭 좌표를 변환하지 못했습니다: ({wgc_x}, {wgc_y})")
        return client

    def click_client(self, point: tuple[int, int]) -> None:
        x, y = point
        input_message.post_prepared_mouse_click(
            input_message.prepare_mouse_click(self.hwnd, x, y)
        )

    def prepare_client(
        self,
        point: tuple[int, int],
    ) -> input_message.PreparedMouseClick:
        x, y = point
        return input_message.prepare_mouse_click(self.hwnd, x, y)

    @staticmethod
    def click_prepared(click: input_message.PreparedMouseClick) -> None:
        input_message.post_prepared_mouse_click(click)

    def press_escape(self) -> None:
        """구매/판매 창을 닫는 ESC를 최상위 창에 한 번 전달합니다."""
        vk_escape = input_message.KEY_TO_VK["esc"]
        # ESC는 모든 자식 HWND에 브로드캐스트하면 팝업이 두 번 닫힐 수 있어
        # 최상위 창에 한 번만 보냅니다. FC의 전역 UI 단축키는 이 경로로 처리됩니다.
        input_message.send_key_to_window(self.hwnd, vk_escape, press_delay=0.0)


class FastRenewalRunner:
    """구매/판매 창을 열고 가격 변경 시 제한가와 같은 버튼을 즉시 클릭합니다."""

    def __init__(
        self,
        manager: InactiveManager,
        profile: RenewalProfile,
        side: str,
        stop_event,
        logger: LogCallback,
        status: StatusCallback,
        monitor_only: bool = False,
    ):
        if side not in ("buy", "sell"):
            raise ValueError(f"지원하지 않는 갱신 구분입니다: {side}")
        self.manager = manager
        self.profile = profile
        self.side_name = side
        self.stop_event = stop_event
        self.log = logger
        self.status = status
        self.monitor_only = bool(monitor_only)
        self.telemetry: dict[str, object] = {
            "cycle_count": 0,
            "armed_openings": 0,
            "ambiguous_count": 0,
            "monitor_detected": False,
            "initial_mismatch": False,
            "order_clicks": 0,
            "popup_may_be_open": False,
            "current_popup_escape_sent": False,
            "startup_popup_recovered": False,
        }

    def _wait(self, seconds: float) -> bool:
        return bool(self.stop_event.wait(max(0.0, seconds)))

    def run(self) -> bool:
        """정렬된 3상태 판정과 고유 WGC 프레임만 사용해 한 번 주문합니다."""
        missing = self.profile.missing(self.side_name)
        if missing:
            raise RuntimeError("갱신 설정이 필요합니다: " + ", ".join(missing))
        if not self.manager.find_window():
            raise RuntimeError("FC ONLINE 창을 찾지 못했습니다.")

        side_profile = self.profile.side(self.side_name)
        calibrated_size = side_profile.calibrated_frame_size()
        self.status("WGC 연결 중")
        full_frame = self.manager.capture_client_area(window_validated=True)
        if full_frame is None:
            raise RuntimeError("FC ONLINE 첫 화면을 캡처하지 못했습니다.")
        frame_height, frame_width = full_frame.shape[:2]
        if calibrated_size != (frame_width, frame_height):
            full_frame = wait_for_stable_wgc_frame(
                self.manager,
                first_frame=full_frame,
            )
            frame_height, frame_width = full_frame.shape[:2]
        if calibrated_size != (frame_width, frame_height):
            expected_text = (
                f"{calibrated_size[0]}x{calibrated_size[1]}"
                if calibrated_size is not None
                else "미설정"
            )
            raise RuntimeError(
                "게임 창 크기가 안전 보정과 다릅니다. "
                f"현재 {frame_width}x{frame_height}, 보정 {expected_text}. "
                "현재 크기에서 v9 우측 가격영역을 다시 설정하세요."
            )
        engine = self.manager.capture_engine
        if engine is None:
            raise RuntimeError("WGC 캡처 엔진이 시작되지 않았습니다.")
        if not hasattr(engine, "get_latest_frame_packet"):
            raise RuntimeError("WGC 프레임 순번 API가 없는 구버전 캡처 엔진입니다.")
        del full_frame
        gc.collect()

        assert side_profile.action_point is not None
        assert side_profile.confirm_point is not None
        assert side_profile.price_rect is not None
        assert side_profile.guard_rect is not None
        assert side_profile.limit_point is not None

        baseline = decode_gray_png(side_profile.baseline_png)
        guard_baseline = decode_gray_png(side_profile.guard_png)
        closed_guard_baseline = decode_gray_png(
            side_profile.closed_guard_png
        )
        price_box = price_box_in_guard(
            side_profile.price_rect,
            side_profile.guard_rect,
            frame_width,
            frame_height,
        )
        dynamic_boxes = dynamic_limit_price_boxes(
            price_box,
            self.side_name,
            frame_height,
        )
        classifier = RenewalPriceClassifier(
            baseline,
            side_profile.unchanged_limit,
            side_profile.stability_limit,
        )
        modal_guard = RenewalModalGuard(
            guard_baseline,
            price_box,
            shift_limit=side_profile.registration_shift_limit,
            dynamic_boxes=dynamic_boxes,
        )
        ready_guard = RenewalModalGuard(
            closed_guard_baseline,
            price_box,
            shift_limit=side_profile.registration_shift_limit,
            dynamic_boxes=dynamic_boxes,
        )
        guard_region = side_profile.guard_rect.to_pixels(frame_width, frame_height)

        clicker = _FastClicker(self.manager, frame_width, frame_height)
        action = clicker.resolve(side_profile.action_point)
        confirm = clicker.resolve(side_profile.confirm_point)
        limit_price = clicker.resolve(side_profile.limit_point)
        action_click = clicker.prepare_client(action)
        confirm_click = clicker.prepare_client(confirm)
        limit_click = clicker.prepare_client(limit_price)
        engine.set_capture_region(guard_region)

        required_frames = 2 if self.profile.speed_level >= 8 else 3
        required_arm_openings = 2
        armed_openings = 0
        order_latched = False
        cycle_count = 0
        last_sequence_id = -1
        last_captured_at = float("-inf")
        frame_intervals: deque[float] = deque(maxlen=240)
        open_latencies: deque[float] = deque(maxlen=120)
        close_latencies: deque[float] = deque(maxlen=120)
        classify_latencies: deque[float] = deque(maxlen=240)
        cycle_window_started = time.perf_counter()
        run_started = cycle_window_started
        last_status_update = 0.0
        self.status("기준 가격 독립 확인 0/2")
        self.log(
            f"[갱신 v9] {'구매/상한가' if self.side_name == 'buy' else '판매/하한가'} "
            f"속도 {self.profile.speed_level}, 명확한 변경 {required_frames}프레임, "
            f"동일가격 상한 {side_profile.unchanged_limit:.4f}, "
            f"{'무주문 측정' if self.monitor_only else '실주문'}, 애매하면 주문 금지"
        )

        def update_performance_telemetry() -> None:
            elapsed = max(1e-9, time.perf_counter() - run_started)

            def percentile(
                values: deque[float],
                rank: float,
            ) -> float:
                return (
                    float(np.percentile(values, rank))
                    if values
                    else 0.0
                )

            frame_p50 = percentile(frame_intervals, 50) * 1000.0
            self.telemetry.update(
                {
                    "elapsed_seconds": elapsed,
                    "cycles_per_second": cycle_count / elapsed,
                    "frame_interval_p50_ms": frame_p50,
                    "frame_interval_p95_ms": (
                        percentile(frame_intervals, 95) * 1000.0
                    ),
                    "capture_hz": (
                        1000.0 / frame_p50 if frame_p50 > 0 else 0.0
                    ),
                    "open_p50_ms": percentile(open_latencies, 50),
                    "open_p95_ms": percentile(open_latencies, 95),
                    "close_p50_ms": percentile(close_latencies, 50),
                    "close_p95_ms": percentile(close_latencies, 95),
                    "classify_p95_ms": percentile(
                        classify_latencies,
                        95,
                    ),
                    "classify_p99_ms": percentile(
                        classify_latencies,
                        99,
                    ),
                    "replaced_frames": (
                        engine.get_replaced_frame_count()
                        if hasattr(engine, "get_replaced_frame_count")
                        else 0
                    ),
                }
            )

        def next_guard_packet(
            deadline: float,
            *,
            not_before: float = float("-inf"),
            allow_stopped: bool = False,
        ) -> Optional[CapturedFrame]:
            nonlocal last_sequence_id, last_captured_at
            while allow_stopped or not self.stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                packet = engine.get_latest_frame_packet(
                    timeout=min(0.050, remaining)
                )
                if packet is None:
                    if engine.closed_event.is_set():
                        raise RuntimeError("WGC 캡처 세션이 종료되었습니다.")
                    continue
                if (
                    packet.sequence_id <= last_sequence_id
                    or packet.captured_at <= last_captured_at
                ):
                    continue
                if last_captured_at > 0:
                    interval = packet.captured_at - last_captured_at
                    if 0.001 <= interval <= 0.100:
                        frame_intervals.append(interval)
                last_sequence_id = packet.sequence_id
                last_captured_at = packet.captured_at
                if packet.captured_at <= not_before:
                    continue
                return packet
            return None

        def wait_until_ready(
            deadline: float,
            *,
            not_before: float = float("-inf"),
            allow_stopped: bool = False,
        ) -> bool:
            while allow_stopped or not self.stop_event.is_set():
                packet = next_guard_packet(
                    deadline,
                    not_before=not_before,
                    allow_stopped=allow_stopped,
                )
                if packet is None:
                    return False
                registration = ready_guard.register(
                    packet.image,
                    side_profile.closed_guard_luma_noise,
                    side_profile.closed_guard_edge_noise,
                )
                if registration.valid:
                    return True
            return False

        def detect_start_state(deadline: float) -> str:
            """Return ready/modal only for a fully registered calibrated state."""

            while not self.stop_event.is_set():
                packet = next_guard_packet(deadline)
                if packet is None:
                    return "unknown"
                ready_registration = ready_guard.register(
                    packet.image,
                    side_profile.closed_guard_luma_noise,
                    side_profile.closed_guard_edge_noise,
                )
                if ready_registration.valid:
                    return "ready"
                modal_registration = modal_guard.register(
                    packet.image,
                    side_profile.guard_luma_noise,
                    side_profile.guard_edge_noise,
                )
                if modal_registration.valid:
                    return "modal"
            return "unknown"

        def close_modal(*, allow_stopped: bool = False) -> bool:
            self.telemetry["current_popup_escape_sent"] = True
            clicker.press_escape()
            closing_started = time.perf_counter()
            if self.profile.close_settle_ms > 0:
                settle_seconds = self.profile.close_settle_ms / 1000.0
                # Once ESC has been sent, finish the bounded cleanup even if a
                # duration timer fires.  Aborting here can leave the popup
                # open and make the next run fail its initial state check.
                time.sleep(settle_seconds)
            ready = wait_until_ready(
                time.monotonic() + 0.75,
                not_before=closing_started,
                allow_stopped=True,
            )
            if ready:
                self.telemetry["popup_may_be_open"] = False
                close_latencies.append(
                    (time.perf_counter() - closing_started) * 1000.0
                )
            return ready

        self.status("목록 준비 화면 확인 중")
        start_state = detect_start_state(time.monotonic() + 1.0)
        if start_state == "modal":
            self.telemetry["popup_may_be_open"] = True
            self.telemetry["current_popup_escape_sent"] = False
            self.status("열린 팝업 확인 · 안전하게 닫는 중")
            if not close_modal():
                raise RuntimeError(
                    "열린 팝업에 ESC를 보냈지만 목록 화면 복귀를 확인하지 "
                    "못했습니다."
                )
            self.telemetry["startup_popup_recovered"] = True
            self.status("열린 팝업 정리 완료 · 목록 감시 시작")
        elif start_state != "ready":
            raise RuntimeError(
                "보정된 목록 화면이나 구매/판매 팝업을 확인하지 못했습니다. "
                "보정한 선수의 목록 화면에서 다시 시작하세요."
            )

        while not self.stop_event.is_set():
            cycle_count += 1
            self.telemetry["cycle_count"] = cycle_count
            action_started = time.perf_counter()
            clicker.click_prepared(action_click)
            self.telemetry["popup_may_be_open"] = True
            self.telemetry["current_popup_escape_sent"] = False
            opening_not_before = time.perf_counter()
            if self.profile.open_settle_ms > 0 and self._wait(
                self.profile.open_settle_ms / 1000.0
            ):
                close_modal(allow_stopped=True)
                update_performance_telemetry()
                return False

            last_result: Optional[PriceClassification] = None
            stable_count = 0
            raw_prices: list[np.ndarray] = []
            aligned_prices: list[np.ndarray] = []
            raw_guards: list[np.ndarray] = []
            sequence_ids: list[int] = []
            capture_timestamps: list[float] = []
            guard_shifts: list[tuple[int, int]] = []
            ambiguous = False
            decision: Optional[PriceState] = None
            open_recorded = False
            deadline = time.monotonic() + 0.45

            while not self.stop_event.is_set():
                packet = next_guard_packet(
                    deadline,
                    not_before=opening_not_before,
                )
                if packet is None:
                    break
                registration = modal_guard.register(
                    packet.image,
                    side_profile.guard_luma_noise,
                    side_profile.guard_edge_noise,
                )
                if not registration.valid:
                    last_result = None
                    stable_count = 0
                    raw_prices.clear()
                    aligned_prices.clear()
                    raw_guards.clear()
                    sequence_ids.clear()
                    capture_timestamps.clear()
                    guard_shifts.clear()
                    continue

                if not open_recorded:
                    open_latencies.append(
                        (time.perf_counter() - action_started) * 1000.0
                    )
                    open_recorded = True
                price = crop_price_from_guard(registration.aligned, price_box)
                classify_started = time.perf_counter()
                result = classifier.classify(price)
                classify_latencies.append(
                    (time.perf_counter() - classify_started) * 1000.0
                )
                raw_prices.append(price)
                aligned_prices.append(result.aligned)
                raw_guards.append(packet.image)
                sequence_ids.append(packet.sequence_id)
                capture_timestamps.append(packet.captured_at)
                guard_shifts.append(
                    (registration.shift_x, registration.shift_y)
                )
                raw_prices[:] = raw_prices[-required_frames:]
                aligned_prices[:] = aligned_prices[-required_frames:]
                raw_guards[:] = raw_guards[-required_frames:]
                sequence_ids[:] = sequence_ids[-required_frames:]
                capture_timestamps[:] = capture_timestamps[-required_frames:]
                guard_shifts[:] = guard_shifts[-required_frames:]

                if result.state is PriceState.AMBIGUOUS:
                    ambiguous = True
                    last_result = result
                    break

                if (
                    last_result is not None
                    and classifier.same_candidate(last_result, result)
                ):
                    stable_count += 1
                else:
                    stable_count = 1
                    raw_prices[:] = raw_prices[-1:]
                    aligned_prices[:] = aligned_prices[-1:]
                    raw_guards[:] = raw_guards[-1:]
                    sequence_ids[:] = sequence_ids[-1:]
                    capture_timestamps[:] = capture_timestamps[-1:]
                    guard_shifts[:] = guard_shifts[-1:]
                last_result = result

                if stable_count < required_frames:
                    continue
                decision = result.state
                break

            if self.stop_event.is_set():
                if bool(self.telemetry["popup_may_be_open"]):
                    close_modal(allow_stopped=True)
                update_performance_telemetry()
                return False

            if ambiguous and last_result is not None:
                self.telemetry["ambiguous_count"] = (
                    int(self.telemetry["ambiguous_count"]) + 1
                )
                self.status("판정 불가 · 주문 금지 후 재감시")
                save_renewal_diagnostic(
                    self.side_name,
                    "ambiguous",
                    baseline,
                    raw_prices,
                    {
                        "state": last_result.state.value,
                        "reason": last_result.reason,
                        "global_score": last_result.global_score,
                        "slice_score": last_result.slice_score,
                        "price_shift": [
                            last_result.shift_x,
                            last_result.shift_y,
                        ],
                        "guard_shifts": guard_shifts,
                        "sequence_ids": sequence_ids,
                        "capture_timestamps": capture_timestamps,
                        "cycle": cycle_count,
                    },
                    aligned_frames=aligned_prices,
                    guard_frames=raw_guards,
                )
                if not close_modal():
                    self.status("닫힘 준비 화면 미확인 · 안전 정지")
                    return False
                continue

            if decision is PriceState.CHANGED and last_result is not None:
                if armed_openings < required_arm_openings:
                    self.telemetry["initial_mismatch"] = True
                    self.status("기준 가격 불일치 · v9 우측 가격영역 재설정 필요")
                    self.log(
                        "[안전 차단] 독립 기준 확인 전에 다른 가격이 감지되어 "
                        "주문 없이 정지합니다."
                    )
                    save_renewal_diagnostic(
                        self.side_name,
                        "initial_mismatch",
                        baseline,
                        raw_prices,
                        {
                            "global_score": last_result.global_score,
                            "slice_score": last_result.slice_score,
                            "price_shift": [
                                last_result.shift_x,
                                last_result.shift_y,
                            ],
                            "guard_shifts": guard_shifts,
                            "sequence_ids": sequence_ids,
                            "capture_timestamps": capture_timestamps,
                            "cycle": cycle_count,
                        },
                        aligned_frames=aligned_prices,
                        guard_frames=raw_guards,
                    )
                    close_modal()
                    update_performance_telemetry()
                    return False

                if self.monitor_only:
                    self.telemetry["monitor_detected"] = True
                    self.status("무주문 측정 · 가격 변경 감지 · 입력 0회")
                    self.log(
                        f"[무주문 측정 v9] {cycle_count}회에서 변경 확정, "
                        f"고유 프레임 {sequence_ids}, 입력하지 않고 정지"
                    )
                    save_renewal_diagnostic(
                        self.side_name,
                        "monitor_detected",
                        baseline,
                        raw_prices,
                        {
                            "state": last_result.state.value,
                            "global_score": last_result.global_score,
                            "slice_score": last_result.slice_score,
                            "sequence_ids": sequence_ids,
                            "capture_timestamps": capture_timestamps,
                            "cycle": cycle_count,
                            "ordered": False,
                        },
                        aligned_frames=aligned_prices,
                        guard_frames=raw_guards,
                    )
                    close_modal()
                    update_performance_telemetry()
                    return False

                if order_latched:
                    return True
                order_latched = True
                detected_at = time.perf_counter()
                self.status(
                    "가격 변경 확정 · "
                    + ("상한가 구매" if self.side_name == "buy" else "하한가 판매")
                )
                clicker.click_prepared(limit_click)
                clicker.click_prepared(confirm_click)
                self.telemetry["order_clicks"] = 2
                input_completed_at = time.perf_counter()
                elapsed_ms = (input_completed_at - detected_at) * 1000.0
                second_frame_to_input_ms = (
                    max(
                        0.0,
                        input_completed_at - capture_timestamps[-1],
                    )
                    * 1000.0
                )
                first_frame_to_input_ms = (
                    max(
                        0.0,
                        input_completed_at - capture_timestamps[0],
                    )
                    * 1000.0
                )
                self.log(
                    f"[갱신 완료 v9] {cycle_count}회 확인, "
                    f"고유 프레임 {sequence_ids}, 전역 {last_result.global_score:.4f}, "
                    f"한자리 {last_result.slice_score:.4f}, "
                    f"가격 이동 ({last_result.shift_x},{last_result.shift_y}), "
                    f"주문 입력 {elapsed_ms:.2f}ms · 즉시 정지"
                )
                save_renewal_diagnostic(
                    self.side_name,
                    "ordered",
                    baseline,
                    raw_prices,
                    {
                        "state": last_result.state.value,
                        "reason": last_result.reason,
                        "global_score": last_result.global_score,
                        "slice_score": last_result.slice_score,
                        "price_shift": [
                            last_result.shift_x,
                            last_result.shift_y,
                        ],
                        "guard_shifts": guard_shifts,
                        "sequence_ids": sequence_ids,
                        "capture_timestamps": capture_timestamps,
                        "click_ms": elapsed_ms,
                        "second_frame_to_input_ms": (
                            second_frame_to_input_ms
                        ),
                        "first_frame_to_input_ms": first_frame_to_input_ms,
                        "cycle": cycle_count,
                    },
                    aligned_frames=aligned_prices,
                    guard_frames=raw_guards,
                )
                update_performance_telemetry()
                return True

            if decision is PriceState.UNCHANGED:
                if armed_openings < required_arm_openings:
                    armed_openings += 1
                    self.telemetry["armed_openings"] = armed_openings
                    self.status(
                        f"기준 가격 독립 확인 {armed_openings}/{required_arm_openings}"
                    )
                    if armed_openings == required_arm_openings:
                        self.log(
                            "[안전 확인 v9] 서로 다른 두 팝업에서 기준 가격 일치 · 주문 가능"
                        )
                if not close_modal():
                    self.status("닫힘 준비 화면 미확인 · 안전 정지")
                    return False
            else:
                # 팝업이 완전히 열리지 않았거나 고유 프레임이 부족한 사이클입니다.
                if not close_modal():
                    self.status("닫힘 준비 화면 미확인 · 안전 정지")
                    return False

            if cycle_count % 100 == 0:
                update_performance_telemetry()
                window_seconds = time.perf_counter() - cycle_window_started
                cycle_window_started = time.perf_counter()
                frame_ms = (
                    float(np.median(frame_intervals)) * 1000.0
                    if frame_intervals
                    else 0.0
                )
                capture_hz = 1000.0 / frame_ms if frame_ms > 0 else 0.0
                classify_p95 = (
                    float(np.percentile(classify_latencies, 95))
                    if classify_latencies
                    else 0.0
                )
                open_p50 = (
                    float(np.median(open_latencies))
                    if open_latencies
                    else 0.0
                )
                close_p50 = (
                    float(np.median(close_latencies))
                    if close_latencies
                    else 0.0
                )
                replaced = (
                    engine.get_replaced_frame_count()
                    if hasattr(engine, "get_replaced_frame_count")
                    else 0
                )
                self.log(
                    f"[갱신 v9] {cycle_count}회 확인 중 · "
                    f"최근 100회 평균 {window_seconds * 10.0:.1f}ms/사이클 · "
                    f"WGC {capture_hz:.1f}Hz · 열림 {open_p50:.1f}ms · "
                    f"닫힘 {close_p50:.1f}ms · 판정 p95 {classify_p95:.3f}ms · "
                    f"최신교체 {replaced}"
                )
            now = time.perf_counter()
            if (
                armed_openings >= required_arm_openings
                and now - last_status_update >= 0.5
            ):
                last_status_update = now
                frame_ms = (
                    float(np.median(frame_intervals)) * 1000.0
                    if frame_intervals
                    else 0.0
                )
                capture_hz = 1000.0 / frame_ms if frame_ms > 0 else 0.0
                self.status(
                    f"120Hz 직결 {capture_hz:.0f}Hz · 변경 {required_frames}프레임 · "
                    f"{'무주문' if self.monitor_only else '실주문'} · {cycle_count}회"
                )

        update_performance_telemetry()
        return False

    def _run_v5_legacy(self) -> bool:
        """팝업과 가격이 모두 안정된 새 WGC 프레임에서만 한 번 주문합니다."""
        missing = self.profile.missing(self.side_name)
        if missing:
            raise RuntimeError("갱신 설정이 필요합니다: " + ", ".join(missing))
        if not self.manager.find_window():
            raise RuntimeError("FC ONLINE 창을 찾지 못했습니다.")

        self.status("WGC 연결 중")
        full_frame = self.manager.capture_client_area(window_validated=True)
        if full_frame is None:
            raise RuntimeError("FC ONLINE 첫 화면을 캡처하지 못했습니다.")
        frame_height, frame_width = full_frame.shape[:2]
        engine = self.manager.capture_engine
        if engine is None:
            raise RuntimeError("WGC 캡처 엔진이 시작되지 않았습니다.")

        side_profile = self.profile.side(self.side_name)
        assert side_profile.action_point is not None
        assert side_profile.confirm_point is not None
        assert side_profile.price_rect is not None
        assert side_profile.guard_rect is not None
        assert side_profile.limit_point is not None

        baseline = decode_gray_png(side_profile.baseline_png)
        guard_baseline = decode_gray_png(side_profile.guard_png)
        price_box = price_box_in_guard(
            side_profile.price_rect,
            side_profile.guard_rect,
            frame_width,
            frame_height,
        )
        detector = RenewalChangeDetector(baseline)
        modal_guard = RenewalModalGuard(guard_baseline, price_box)
        guard_region = side_profile.guard_rect.to_pixels(frame_width, frame_height)

        clicker = _FastClicker(self.manager, frame_width, frame_height)
        action = clicker.resolve(side_profile.action_point)
        confirm = clicker.resolve(side_profile.confirm_point)
        limit_price = clicker.resolve(side_profile.limit_point)
        engine.set_capture_region(guard_region)

        required_frames = 2 if self.profile.speed_level >= 8 else 3
        global_trigger = max(
            0.055,
            self.profile.change_threshold,
            side_profile.noise_global * 6.0,
        )
        global_floor = max(0.035, side_profile.noise_global * 4.0)
        slice_trigger = max(
            0.16,
            self.profile.slice_threshold,
            side_profile.noise_slice * 6.0,
        )
        stability_limit = max(
            0.015,
            min(0.035, side_profile.noise_global * 4.0 + 0.01),
        )

        armed = False
        order_latched = False
        cycle_count = 0
        cycle_window_started = time.perf_counter()
        self.status("기준 가격 확인 준비 중")
        self.log(
            f"[갱신] {'구매/상한가' if self.side_name == 'buy' else '판매/하한가'} "
            f"무한 감시 시작 (속도 {self.profile.speed_level}, 안전검증 "
            f"{required_frames}프레임, 전역 {global_trigger:.3f}, "
            f"한자리 {slice_trigger:.3f})"
        )

        def next_guard_frame(deadline: float) -> Optional[np.ndarray]:
            while not self.stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                frame = engine.get_latest_frame(timeout=min(0.050, remaining))
                if frame is not None:
                    return frame
                if engine.closed_event.is_set():
                    raise RuntimeError("WGC 캡처 세션이 종료되었습니다.")
            return None

        def close_modal() -> bool:
            clicker.press_escape()
            if self.profile.close_settle_ms > 0 and self._wait(
                self.profile.close_settle_ms / 1000.0
            ):
                return False
            closed_streak = 0
            deadline = time.monotonic() + 0.35
            while not self.stop_event.is_set():
                frame = next_guard_frame(deadline)
                if frame is None:
                    return False
                if modal_guard.matches(
                    frame,
                    side_profile.guard_luma_noise,
                    side_profile.guard_edge_noise,
                ):
                    closed_streak = 0
                    continue
                closed_streak += 1
                if closed_streak >= max(1, self.profile.closed_streak):
                    return True
            return False

        while not self.stop_event.is_set():
            cycle_count += 1
            # 영역 변경 직후 또는 직전 사이클에서 남은 프레임을 버리고 새 프레임만 봅니다.
            engine.get_latest_frame(timeout=0.0)
            clicker.click_client(action)
            if self.profile.open_settle_ms > 0 and self._wait(
                self.profile.open_settle_ms / 1000.0
            ):
                return False
            engine.get_latest_frame(timeout=0.0)

            modal_seen = False
            stable_count = 0
            candidate_changed: Optional[bool] = None
            last_pair: Optional[tuple[np.ndarray, np.ndarray]] = None
            candidate_frames: list[np.ndarray] = []
            last_score = 0.0
            last_slice = 0.0
            decision_made = False
            deadline = time.monotonic() + 0.45

            while not self.stop_event.is_set():
                guard_frame = next_guard_frame(deadline)
                if guard_frame is None:
                    break
                if not modal_guard.matches(
                    guard_frame,
                    side_profile.guard_luma_noise,
                    side_profile.guard_edge_noise,
                ):
                    # 닫힌 화면과 열리는 중간 프레임은 가격 후보가 될 수 없습니다.
                    stable_count = 0
                    candidate_changed = None
                    last_pair = None
                    candidate_frames.clear()
                    continue

                modal_seen = True
                price_frame = crop_price_from_guard(guard_frame, price_box)
                pair = detector.prepare_pair(price_frame)
                score, slice_score = detector.analyze_pair(pair)
                changed = score >= global_trigger or (
                    score >= global_floor and slice_score >= slice_trigger
                )

                structurally_stable = (
                    last_pair is not None
                    and detector.pair_stability(last_pair, pair) <= stability_limit
                )
                if structurally_stable and candidate_changed is changed:
                    stable_count += 1
                else:
                    stable_count = 1
                    candidate_changed = changed
                    candidate_frames.clear()

                last_pair = pair
                last_score = score
                last_slice = slice_score
                candidate_frames.append(price_frame.copy())
                candidate_frames[:] = candidate_frames[-required_frames:]
                if stable_count < required_frames:
                    continue

                decision_made = True
                if not armed:
                    if changed:
                        self.status("기준 가격 불일치 · 가격영역 재설정 필요")
                        self.log(
                            "[안전 차단] 첫 화면이 저장 기준과 다릅니다. "
                            "주문하지 않고 정지합니다."
                        )
                        save_renewal_diagnostic(
                            self.side_name,
                            "initial_mismatch",
                            baseline,
                            candidate_frames,
                            {
                                "global_score": last_score,
                                "slice_score": last_slice,
                                "global_trigger": global_trigger,
                                "slice_trigger": slice_trigger,
                                "required_frames": required_frames,
                                "cycle": cycle_count,
                            },
                        )
                        return False
                    armed = True
                    self.status("주문 가능 · 가격 변경 무한 감시 중")
                    self.log("[안전 확인] 저장 기준과 현재 가격이 일치하여 주문 가능")
                    break

                if not changed:
                    break

                # 잠금을 클릭보다 먼저 걸어 어떤 후속 프레임도 두 번째 주문을 만들지 못합니다.
                if order_latched:
                    return True
                order_latched = True
                detected_at = time.perf_counter()
                self.status(
                    "가격 변경 확정 · "
                    + ("상한가 구매" if self.side_name == "buy" else "하한가 판매")
                )
                clicker.click_client(limit_price)
                clicker.click_client(confirm)
                elapsed_ms = (time.perf_counter() - detected_at) * 1000.0
                self.log(
                    f"[갱신 완료] {cycle_count}회 확인, 안정 {stable_count}프레임, "
                    f"전역 {last_score:.4f}, 한자리 {last_slice:.4f}, "
                    f"주문 입력 {elapsed_ms:.2f}ms · 즉시 정지"
                )
                return True

            if self.stop_event.is_set():
                return False

            if (
                modal_seen
                and not decision_made
                and candidate_changed
                and candidate_frames
            ):
                save_renewal_diagnostic(
                    self.side_name,
                    "unstable_candidate",
                    baseline,
                    candidate_frames,
                    {
                        "global_score": last_score,
                        "slice_score": last_slice,
                        "stable_frames": stable_count,
                        "required_frames": required_frames,
                        "cycle": cycle_count,
                    },
                )

            close_modal()
            if cycle_count % 100 == 0:
                window_seconds = time.perf_counter() - cycle_window_started
                cycle_window_started = time.perf_counter()
                self.log(
                    f"[갱신] {cycle_count}회 확인 중 · "
                    f"최근 100회 평균 {window_seconds * 10.0:.1f}ms/사이클"
                )
            self.status(
                (
                    "주문 가능 · 가격 변경 무한 감시 중"
                    if armed
                    else "기준 가격 확인 준비 중"
                )
                + f" · {cycle_count}회"
            )

        return False
