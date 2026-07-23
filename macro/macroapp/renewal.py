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


@dataclass(frozen=True)
class RenewalSpeedTuning:
    open_settle_ms: int
    close_settle_ms: int
    change_threshold: float
    confirm_frames: int


_RENEWAL_SPEED_PRESETS: dict[int, RenewalSpeedTuning] = {
    1: RenewalSpeedTuning(100, 80, 0.060, 3),
    2: RenewalSpeedTuning(90, 70, 0.057, 3),
    3: RenewalSpeedTuning(80, 60, 0.055, 3),
    4: RenewalSpeedTuning(67, 50, 0.051, 2),
    5: RenewalSpeedTuning(55, 40, 0.047, 2),
    6: RenewalSpeedTuning(45, 25, 0.045, 2),
    7: RenewalSpeedTuning(35, 22, 0.042, 2),
    8: RenewalSpeedTuning(28, 18, 0.039, 2),
    9: RenewalSpeedTuning(22, 14, 0.036, 1),
    10: RenewalSpeedTuning(18, 10, 0.033, 1),
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
    limit_point: Optional[NormalizedPoint] = None
    baseline_png: str = ""

    def complete(self) -> bool:
        return bool(
            self.action_point
            and self.confirm_point
            and self.price_rect
            and self.limit_point
            and self.baseline_png
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "action_point": self.action_point.to_dict() if self.action_point else None,
            "confirm_point": (
                self.confirm_point.to_dict() if self.confirm_point else None
            ),
            "price_rect": self.price_rect.to_dict() if self.price_rect else None,
            "limit_point": self.limit_point.to_dict() if self.limit_point else None,
            "baseline_png": self.baseline_png,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RenewalSideProfile":
        if not isinstance(value, dict):
            return cls()
        return cls(
            action_point=NormalizedPoint.from_dict(value.get("action_point")),
            confirm_point=NormalizedPoint.from_dict(value.get("confirm_point")),
            price_rect=NormalizedRect.from_dict(value.get("price_rect")),
            limit_point=NormalizedPoint.from_dict(value.get("limit_point")),
            baseline_png=str(value.get("baseline_png") or ""),
        )


@dataclass
class RenewalProfile:
    re_register_point: Optional[NormalizedPoint] = None
    confirm_point: Optional[NormalizedPoint] = None
    cancel_point: Optional[NormalizedPoint] = None
    buy: RenewalSideProfile = field(default_factory=RenewalSideProfile)
    sell: RenewalSideProfile = field(default_factory=RenewalSideProfile)
    speed_level: int = 6
    change_threshold: float = 0.045
    open_settle_ms: int = 45
    close_settle_ms: int = 25
    confirm_frames: int = 2
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

    def missing(self, side: str) -> list[str]:
        missing: list[str] = []
        side_profile = self.side(side)
        if side_profile.action_point is None:
            missing.append("창 열기 구매/판매 버튼 위치")
        if side_profile.confirm_point is None:
            missing.append("창 안 최종 구매/판매 버튼 위치")
        if side_profile.price_rect is None or not side_profile.baseline_png:
            missing.append("가격 감지영역")
        if side_profile.limit_point is None:
            missing.append("상한가 위치" if side == "buy" else "하한가 위치")
        return missing

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 4,
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


class RenewalChangeDetector:
    """숫자 모양의 에지 차이 비율로 가격 변경을 판정합니다."""

    _SIZE = (192, 48)

    def __init__(self, baseline: np.ndarray):
        self.baseline_edges = self._prepare(baseline)

    @classmethod
    def _prepare(cls, image: np.ndarray) -> np.ndarray:
        if image is None or image.size == 0:
            raise ValueError("가격 감지영역이 비어 있습니다.")
        if image.ndim == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(image, cls._SIZE, interpolation=cv2.INTER_AREA)
        blurred = cv2.GaussianBlur(resized, (3, 3), 0)
        return cv2.Canny(blurred, 45, 120)

    def score(self, image: np.ndarray) -> float:
        candidate = self._prepare(image)
        difference = cv2.bitwise_xor(self.baseline_edges, candidate)
        # 전체 사각형 넓이가 아니라 실제 글자 에지 합집합 대비 달라진 에지의 비율을
        # 사용합니다. 숫자 한 자리만 바뀌어도 충분히 크게 나오고, 주변 여백 크기에는
        # 거의 영향을 받지 않습니다.
        union = cv2.bitwise_or(self.baseline_edges, candidate)
        union_count = cv2.countNonZero(union)
        if union_count <= 0:
            return 0.0
        return float(cv2.countNonZero(difference)) / float(union_count)


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
        assert side_profile.limit_point is not None

        detector = RenewalChangeDetector(decode_gray_png(side_profile.baseline_png))
        region = side_profile.price_rect.to_pixels(frame_width, frame_height)
        clicker = _FastClicker(self.manager, frame_width, frame_height)
        action = clicker.resolve(side_profile.action_point)
        confirm = clicker.resolve(side_profile.confirm_point)
        limit_price = clicker.resolve(side_profile.limit_point)

        # 이후 WGC 콜백은 이 작은 가격 영역만 grayscale로 변환합니다.
        engine.set_capture_region(region)
        cycle_count = 0
        recovery_count = 0
        self.status("가격 변경 전까지 무한 반복 중")
        self.log(
            f"[갱신] {'구매 상한가' if self.side_name == 'buy' else '판매 하한가'} "
            f"열기 버튼/ESC 반복 시작 "
            f"(속도 {self.profile.speed_level}, 에지 기준 {self.profile.change_threshold:.3f})"
        )

        def close_modal() -> bool:
            """ESC를 보내고 가격 창이 닫힐 시간을 확보합니다.

            화면 확인이 늦어져도 전체 반복은 종료하지 않습니다. 반환값은 다음
            상태 표시와 진단 로그에만 사용하고, 사용자가 F9를 누르거나 WGC가
            종료된 경우에만 러너를 멈춥니다.
            """
            clicker.press_escape()
            started_at = time.monotonic()
            # 30/60fps 및 UI 전환 지연을 고려해 확인 상한은 넉넉히 두되,
            # 목록 프레임 2장이 확인되면 즉시 빠져나와 실제 속도는 늦추지 않습니다.
            deadline = started_at + 0.35
            not_before = started_at + max(
                0.010,
                self.profile.close_settle_ms / 1000.0,
            )
            closed_streak = 0
            while not self.stop_event.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                crop = engine.get_latest_frame(timeout=min(0.020, remaining))
                if crop is None:
                    if engine.closed_event.is_set():
                        raise RuntimeError("WGC 캡처 세션이 종료되었습니다.")
                    continue
                if (
                    time.monotonic() >= not_before
                    and detector.score(crop) > self.profile.modal_max_score
                ):
                    closed_streak += 1
                    if closed_streak >= 2:
                        return True
                else:
                    closed_streak = 0
            return False

        while not self.stop_event.is_set():
            # 구매/판매 버튼으로 가격 창을 열고, 전환 초기에 잡힌 프레임은 버립니다.
            cycle_count += 1
            engine.get_latest_frame(timeout=0.0)
            clicker.click_client(action)
            if self._wait(self.profile.open_settle_ms / 1000.0):
                return False
            engine.get_latest_frame(timeout=0.0)

            modal_seen = False
            changed_streak = 0
            unchanged = False
            check_deadline = time.monotonic() + 0.35
            grace_extended = False

            while not self.stop_event.is_set() and time.monotonic() < check_deadline:
                crop = engine.get_latest_frame(timeout=0.020)
                if crop is None:
                    if engine.closed_event.is_set():
                        raise RuntimeError("WGC 캡처 세션이 종료되었습니다.")
                    continue

                score = detector.score(crop)
                if score > self.profile.modal_max_score:
                    # 목록 화면 또는 모달 전환 중인 프레임은 가격 판정에 쓰지 않습니다.
                    changed_streak = 0
                    continue

                modal_seen = True
                if score < self.profile.change_threshold:
                    unchanged = True
                    break

                changed_streak += 1
                if not grace_extended:
                    # WGC가 프레임을 건너뛰는 순간에도 확정 프레임을 받을
                    # 짧은 여유를 한 번만 추가합니다. 가격 변경을 본 뒤
                    # 곧바로 ESC로 닫고 다시 여는 것을 방지합니다.
                    check_deadline = max(check_deadline, time.monotonic() + 0.08)
                    grace_extended = True
                if changed_streak < self.profile.confirm_frames:
                    continue

                detected_at = time.perf_counter()
                self.status(
                    "가격 변경 감지 — 즉시 "
                    + ("구매" if self.side_name == "buy" else "판매")
                )
                # 같은 HWND 메시지 큐에 순서대로 들어가므로 별도 sleep 없이 처리됩니다.
                clicker.click_client(limit_price)
                # 바깥쪽 창 열기 버튼이 아니라, 열린 창 안쪽의 최종 구매/판매
                # 버튼을 누릅니다. 두 버튼은 화면상 위치가 서로 다릅니다.
                clicker.click_client(confirm)
                elapsed_ms = (time.perf_counter() - detected_at) * 1000.0
                self.log(
                    f"[갱신 완료] {cycle_count}회 확인, 가격 변경 "
                    f"{changed_streak}프레임 확정 → 제한가/{'구매' if self.side_name == 'buy' else '판매'} "
                    f"입력 {elapsed_ms:.2f}ms"
                )
                # 클릭 지연에는 포함하지 않고, 다음 갱신 실행을 위해 새 가격을 기준값으로 저장합니다.
                try:
                    side_profile.baseline_png = encode_gray_png(crop)
                    save_renewal_profile(self.profile)
                except Exception as exc:
                    self.log(f"[갱신 주의] 새 기준 가격 저장 실패: {exc}")
                self.status("갱신 입력 완료 — 중복 주문 방지 정지")
                return True

            if self.stop_event.is_set():
                return False

            if modal_seen and unchanged:
                # 그대로면 ESC로 닫고 곧바로 다음 가격 창을 엽니다.
                if close_modal():
                    recovery_count = 0
                else:
                    recovery_count += 1
                if cycle_count % 100 == 0:
                    self.log(f"[갱신] {cycle_count}회 확인 중")
                continue

            # 전환 프레임을 놓치거나 닫힘 확인이 지연돼도 단일 실패로 전체
            # 매크로를 끝내지 않습니다. ESC로 입력 상태를 초기화하고 다음
            # 사이클에서 다시 엽니다.
            closed = close_modal()
            recovery_count = 0 if closed else recovery_count + 1
            if recovery_count == 1 or recovery_count % 25 == 0:
                self.log(
                    f"[갱신 복구] 화면 전환 확인 실패 {recovery_count}회 — "
                    "ESC 후 무한 반복을 계속합니다."
                )
            self.status(
                f"가격 변경 전까지 무한 반복 중 · {cycle_count}회"
            )

        return False
