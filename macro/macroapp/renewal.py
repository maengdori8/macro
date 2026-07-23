"""FC ONLINE 이적시장 갱신 감시 엔진.

OCR 대신 사용자가 지정한 가격 숫자 영역의 에지 변화만 비교합니다. 실행 중 WGC는
그 작은 영역만 grayscale로 변환하므로 전체 화면 템플릿 매칭보다 지연과 CPU 사용량이
훨씬 작습니다.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import cv2
import numpy as np

from macroapp import input_message
from macroapp.window import InactiveManager


LogCallback = Callable[[str], None]
StatusCallback = Callable[[str], None]
RENEWAL_PROFILE_VERSION = 5
RENEWAL_CALIBRATION_VERSION = 1


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
    noise_global: float = 0.0
    noise_slice: float = 0.0
    guard_luma_noise: float = 0.0
    guard_edge_noise: float = 0.0
    calibration_version: int = 0

    def complete(self) -> bool:
        return bool(
            self.action_point
            and self.confirm_point
            and self.price_rect
            and self.guard_rect
            and self.limit_point
            and self.baseline_png
            and self.guard_png
            and self.calibration_version >= RENEWAL_CALIBRATION_VERSION
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
            "noise_global": float(self.noise_global),
            "noise_slice": float(self.noise_slice),
            "guard_luma_noise": float(self.guard_luma_noise),
            "guard_edge_noise": float(self.guard_edge_noise),
            "calibration_version": int(self.calibration_version),
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
            side.calibration_version = max(
                0, int(value.get("calibration_version", 0))
            )
        except (TypeError, ValueError):
            side.calibration_version = 0
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
            or side_profile.calibration_version < RENEWAL_CALIBRATION_VERSION
        ):
            missing.append("안전 가격영역 재설정")
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
        diff, union, global_ratio = self._diff(self.baseline_pair, pair)
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

    @classmethod
    def pair_stability(cls, a: tuple, b: tuple) -> float:
        """두 프레임(둘 다 후보) 사이의 전역 변화율. 작을수록 '같은 화면이 정지'했다는 뜻."""
        return cls._diff(a, b)[2]

    def analyze(self, image: np.ndarray) -> tuple[float, float]:
        """(전역 변화율, 슬라이스 최대 변화율)을 반환합니다(단일 이미지 편의용)."""
        return self.analyze_pair(self.prepare_pair(image))

    def score(self, image: np.ndarray) -> float:
        """기존 호환용: 전역 변화율만 반환합니다(모달/목록 분류 등)."""
        return self.analyze(image)[0]


class RenewalModalGuard:
    """가격 중앙을 제외한 주변 배경으로 구매/판매 팝업이 완전히 열렸는지 확인합니다."""

    _DILATE_KERNEL = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))

    def __init__(
        self,
        baseline: np.ndarray,
        price_box: tuple[int, int, int, int],
    ):
        if baseline is None or baseline.size == 0:
            raise ValueError("팝업 가드 기준 이미지가 비어 있습니다.")
        if baseline.ndim == 3:
            baseline = cv2.cvtColor(baseline, cv2.COLOR_BGR2GRAY)
        self.baseline = baseline.copy()
        self.mask = np.full(self.baseline.shape, 255, dtype=np.uint8)
        x1, y1, x2, y2 = price_box
        margin = 4
        self.mask[
            max(0, y1 - margin) : min(self.mask.shape[0], y2 + margin),
            max(0, x1 - margin) : min(self.mask.shape[1], x2 + margin),
        ] = 0
        if cv2.countNonZero(self.mask) < 64:
            raise ValueError("팝업 가드 여백이 너무 작습니다.")
        self.baseline_edges = cv2.bitwise_and(
            cv2.Canny(cv2.GaussianBlur(self.baseline, (3, 3), 0), 40, 120),
            self.mask,
        )
        self.baseline_dilated = cv2.dilate(
            self.baseline_edges, self._DILATE_KERNEL
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

    def matches(
        self,
        image: np.ndarray,
        luma_noise: float,
        edge_noise: float,
    ) -> bool:
        luma_delta, edge_delta = self.metrics(image)
        luma_limit = max(12.0, luma_noise * 4.0 + 2.0)
        edge_limit = max(0.10, edge_noise * 4.0 + 0.02)
        return luma_delta <= luma_limit and edge_delta <= edge_limit


@dataclass(frozen=True)
class RenewalCalibrationResult:
    baseline: np.ndarray
    guard: np.ndarray
    noise_global: float
    noise_slice: float
    guard_luma_noise: float
    guard_edge_noise: float


def build_calibration_result(
    guard_samples: list[np.ndarray],
    price_box: tuple[int, int, int, int],
) -> RenewalCalibrationResult:
    if len(guard_samples) < 4:
        raise ValueError("안전 보정에는 최소 4개의 새 WGC 프레임이 필요합니다.")
    shape = guard_samples[0].shape
    if any(sample is None or sample.shape != shape for sample in guard_samples):
        raise ValueError("보정 프레임의 크기가 서로 다릅니다.")
    guard = np.median(np.stack(guard_samples), axis=0).astype(np.uint8)
    baseline = crop_price_from_guard(guard, price_box)
    validation = validate_price_region(baseline)
    if not validation.valid:
        raise ValueError(validation.message)

    detector = RenewalChangeDetector(baseline)
    guard_detector = RenewalModalGuard(guard, price_box)
    global_scores: list[float] = []
    slice_scores: list[float] = []
    luma_scores: list[float] = []
    edge_scores: list[float] = []
    for sample in guard_samples:
        price = crop_price_from_guard(sample, price_box)
        global_score, slice_score = detector.analyze(price)
        luma_score, edge_score = guard_detector.metrics(sample)
        global_scores.append(global_score)
        slice_scores.append(slice_score)
        luma_scores.append(luma_score)
        edge_scores.append(edge_score)

    # 보정 중 UI가 움직인 경우 저장하지 않습니다. 정상적인 정지 WGC 프레임은 거의 0입니다.
    if max(global_scores) > 0.025 or max(slice_scores) > 0.10:
        raise ValueError("보정 중 가격 화면이 흔들렸습니다. 창이 멈춘 뒤 다시 설정하세요.")
    if max(luma_scores) > 10.0 or max(edge_scores) > 0.08:
        raise ValueError("보정 중 팝업 화면이 바뀌었습니다. 창을 연 상태로 다시 설정하세요.")

    return RenewalCalibrationResult(
        baseline=baseline.copy(),
        guard=guard,
        noise_global=max(global_scores),
        noise_slice=max(slice_scores),
        guard_luma_noise=max(luma_scores),
        guard_edge_noise=max(edge_scores),
    )


def save_renewal_diagnostic(
    side: str,
    reason: str,
    baseline: np.ndarray,
    frames: list[np.ndarray],
    metadata: dict[str, object],
) -> None:
    """차단된 의심 판정만 제한적으로 저장합니다.

    정상 반복에서는 파일을 만들지 않으며 최근 20건만 남깁니다. 진단 저장 실패는
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
        (event_dir / "metrics.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        event_dirs = sorted(
            (path for path in diagnostic_root.iterdir() if path.is_dir()),
            key=lambda path: path.name,
        )
        for old_dir in event_dirs[:-20]:
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
        input_message.post_mouse_move(self.hwnd, x, y)
        input_message.post_mouse_down(self.hwnd, x, y)
        input_message.post_mouse_up(self.hwnd, x, y)

    def press_escape(self) -> None:
        """구매/판매 창을 닫는 ESC를 최상위 창에 한 번 전달합니다."""
        vk_escape = input_message.KEY_TO_VK["esc"]
        # ESC는 모든 자식 HWND에 브로드캐스트하면 팝업이 두 번 닫힐 수 있어
        # 최상위 창에 한 번만 보냅니다. FC의 전역 UI 단축키는 이 경로로 처리됩니다.
        input_message.send_key_to_window(self.hwnd, vk_escape, press_delay=0.005)


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
    ):
        if side not in ("buy", "sell"):
            raise ValueError(f"지원하지 않는 갱신 구분입니다: {side}")
        self.manager = manager
        self.profile = profile
        self.side_name = side
        self.stop_event = stop_event
        self.log = logger
        self.status = status

    def _wait(self, seconds: float) -> bool:
        return bool(self.stop_event.wait(max(0.0, seconds)))

    def run(self) -> bool:
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
