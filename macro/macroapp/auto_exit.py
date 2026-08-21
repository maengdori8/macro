"""0:2 패배 자동 종료 — 판정 로직.

경기 중 스코어가 0:2(내 팀 0, 상대 2)가 되면 즉시 종료(exit_runner)를 자동으로
돌린다. 단, **매번 나가지 않는다** — 무조건 나가면 비매너 점수가 쌓인다.
0:2 로 지고 있던 판만 따로 세고(lost_games), 그중 일정 비율(기본 40%)만 나간다.
20판이면 8판을 나가고 12판은 그대로 둔다.

스코어 읽기는 **OCR 이 아니라 글리프 템플릿 매칭**이다. 실측 근거:
winocr 는 맥락 없는 숫자 한두 개를 프레임에 따라 통째로 못 읽는다 — 녹화의
'0 0' 은 읽었지만 실전 스크린샷의 '1 2' 는 완벽하게 조인 크롭에서도 빈
문자열이었다(6배 확대·검은 패딩·pad 스윕 전부). 게임 스코어보드 폰트는
고정이므로, 실물 캡처에서 뽑은 글리프와의 IoU 비교가 결정적이고 ~1ms 로 끝나
OCR 워커 예산도 잠식하지 않는다. 템플릿은 macroapp/score_glyphs.py(자동 생성).

읽기 결과는 세 가지로 구분한다 — 이 구분이 판정의 핵심이다:
  (a, b)          스코어를 읽었다
  SCORE_UNKNOWN   박스는 보이는데 숫자를 모른다(템플릿 없는 숫자, 예: 3~9)
                  → 경기 '진행 중' 증거로만 쓴다(래치 유지, 카운트 없음)
  None            스코어보드 자체가 없다 → 오래 지속되면 경기 종료로 간주

판정(LossTracker/ExitQuota)은 순수 로직이라 Windows 없이 전부 단위 검증된다.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Optional, Union

try:
    import cv2
    import numpy as np
except Exception:  # noqa: BLE001 - 없으면 읽기만 꺼진다(판정 로직은 그대로 검증 가능)
    cv2 = None  # type: ignore[assignment]
    np = None  # type: ignore[assignment]

#: '박스는 보이는데 숫자를 모른다' — None(스코어보드 없음)과 반드시 구분한다.
#: 섞으면 3:1 같은 (템플릿 없는) 스코어 화면이 '경기 종료'로 오인돼 래치가 풀리고,
#: 같은 경기가 두 번 세어진다.
SCORE_UNKNOWN = "unknown"

Reading = Union[None, str, tuple[int, int]]

#: 글리프 정규화 크기(폭, 높이). score_glyphs.py 의 템플릿과 같아야 한다.
_GLYPH_SIZE = (20, 28)

#: 같은 숫자 IoU ≥ 0.79, 다른 숫자 ≤ 0.51 (실물 캡처 실측) → 가운데보다 높게.
_GLYPH_IOU_THRESHOLD = 0.65

_glyph_cache: Optional[dict] = None


def _load_glyphs() -> Optional[dict]:
    """임베드된 글리프 템플릿을 {숫자: [bool 배열, ...]} 로 푼다(1회 캐시)."""
    global _glyph_cache
    if _glyph_cache is not None:
        return _glyph_cache
    if cv2 is None:
        return None
    try:
        from macroapp.score_glyphs import GLYPH_PNGS_B64
    except Exception:
        return None
    glyphs: dict = {}
    for digit, blobs in GLYPH_PNGS_B64.items():
        masks = []
        for blob in blobs:
            buf = np.frombuffer(base64.b64decode(blob), dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
            if img is not None and img.shape == (_GLYPH_SIZE[1], _GLYPH_SIZE[0]):
                masks.append(img > 127)
        if masks:
            glyphs[int(digit)] = masks
    _glyph_cache = glyphs or None
    return _glyph_cache


# ---------------------------------------------------------------------------
# 프레임 → 스코어
# ---------------------------------------------------------------------------


def extract_score_boxes(crop):
    """크롭에서 스코어 박스(속이 꽉 찬 밝은 정사각형 덩어리)들을 왼쪽부터 찾는다.

    연결 요소를 쓰는 이유(실측): 밝기 열 스캔은 팀명 글자(흰 텍스트)가 박스와
    붙어 있으면 한 덩어리로 오려 낸다. 박스는 '크고, 정사각형에 가깝고, 속이
    꽉 찬' 성질로만 골라야 글자·잡음과 갈린다. 숫자가 파여 있어도 박스 테두리로
    이어져 한 요소다(면적 기준 0.45 는 그 구멍을 감안한 값).
    """
    if cv2 is None:
        return []
    peak = int(crop.max())
    if peak < 180:
        return []                     # 스코어보드 없음(어두운 화면)
    mask = (crop >= max(180, peak - 25)).astype(np.uint8)
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    found = []
    for i in range(1, count):
        x, y, width, height, area = stats[i]
        if width < 15 or height < 15:
            continue                  # 글자 획·잡음
        if not (0.6 <= width / height <= 1.7):
            continue                  # 박스는 정사각형에 가깝다
        if area < 0.45 * width * height:
            continue                  # 속이 비면 글자 뭉치다
        found.append((x, y, width, height))
    found.sort()
    return [crop[y:y + h, x:x + w] for x, y, w, h in found]


def glyph_mask(box):
    """박스 안의 어두운 숫자를 20x28 이진 마스크로 정규화한다. 못 뽑으면 None."""
    if cv2 is None:
        return None
    peak = int(box.max())
    dark = (box <= peak - 100).astype(np.uint8)
    margin = 2                        # 박스 테두리의 어두운 잡음 제거
    dark[:margin, :] = 0
    dark[-margin:, :] = 0
    dark[:, :margin] = 0
    dark[:, -margin:] = 0
    ys, xs = dark.nonzero()
    if len(xs) < 10:
        return None
    tight = dark[ys.min():ys.max() + 1, xs.min():xs.max() + 1] * 255
    resized = cv2.resize(tight, _GLYPH_SIZE, interpolation=cv2.INTER_AREA)
    return resized > 127


def classify_glyph(mask, glyphs=None) -> Optional[int]:
    """글리프 마스크를 숫자로 분류한다. 임계값 미달이면 None(미상)."""
    if mask is None:
        return None
    glyphs = glyphs if glyphs is not None else _load_glyphs()
    if not glyphs:
        return None
    best_digit, best_iou = None, 0.0
    for digit, templates in glyphs.items():
        for template in templates:
            union = (mask | template).sum()
            if not union:
                continue
            iou = (mask & template).sum() / union
            if iou > best_iou:
                best_digit, best_iou = digit, iou
    if best_iou < _GLYPH_IOU_THRESHOLD:
        return None
    return best_digit


def crop_score_region(gray, region_fractions):
    """스코어보드 영역(프레임 비율 좌표)을 원본 픽셀로 잘라 준다. 너무 작으면 None.

    read_score_from_frame 과 '숫자 미상' 표본 저장이 **같은 크롭**을 쓰게 하는 단일
    지점이다 — 저장된 표본으로 만든 글리프가 실전 크롭과 어긋나면 안 된다.
    """
    height, width = gray.shape[:2]
    x1 = max(0, int(width * float(region_fractions[0])))
    y1 = max(0, int(height * float(region_fractions[1])))
    x2 = min(width, int(width * float(region_fractions[2])))
    y2 = min(height, int(height * float(region_fractions[3])))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return gray[y1:y2, x1:x2]


def read_score_from_frame(gray, region_fractions, glyphs=None) -> Reading:
    """프레임에서 스코어를 읽는다. (a,b) / SCORE_UNKNOWN / None(스코어보드 없음).

    실패는 전부 None 으로 수렴한다(자동 종료는 '있으면 좋은 것'이지, 이것 때문에
    자동화가 죽으면 안 된다).
    """
    try:
        crop = crop_score_region(gray, region_fractions)
        if crop is None:
            return None
        boxes = extract_score_boxes(crop)
        if len(boxes) != 2:
            # 박스가 0개면 스코어보드가 없다. 1개나 3개+는 화면 전환·가림의
            # 순간이다 — '없음'으로 보면 경기 종료 타이머가 돌아 래치가 일찍
            # 풀릴 수 있으므로, 하나라도 보이면 '진행 중'으로 취급한다.
            return None if not boxes else SCORE_UNKNOWN
        digits = [classify_glyph(glyph_mask(box), glyphs) for box in boxes]
        if digits[0] is None or digits[1] is None:
            return SCORE_UNKNOWN      # 박스는 있는데 템플릿 없는 숫자(3~9 등)
        return int(digits[0]), int(digits[1])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 종료 규칙(구매자별 운영 설정) — 어떤 스코어·분에 '진 판'으로 볼지
# ---------------------------------------------------------------------------

#: 판정 종류. base/late 는 쿼터(비율)를 따르고, hard 는 비율을 무시하고 무조건 나간다.
KIND_BASE = "base"     # 기본: 상대−나 ≥ base_deficit (예: 0:2)
KIND_HARD = "hard"     # 대량 실점: 상대−나 ≥ hard_deficit (예: 0:3) → 비율 무시
KIND_LATE = "late"     # 후반: 경기 분 ≥ late_minute 이고 상대−나 ≥ late_deficit (예: 70분 0:1)

#: 설정값 허용 범위(클라이언트 fail-safe 검증 — 서버도 같은 범위를 쓴다).
SETTING_BOUNDS = {
    "base_deficit": (1, 9),
    "hard_deficit": (0, 9),     # 0 = 끔
    "late_minute": (0, 120),    # 0 = 끔
    "late_deficit": (1, 9),
}


@dataclass(frozen=True)
class ExitRules:
    """스코어 규칙 세 개. 전부 구매자별로 바뀔 수 있어 상수가 아니라 값이다."""

    base_deficit: int = 2
    hard_deficit: int = 3
    late_minute: int = 70
    late_deficit: int = 1

    def classify(self, mine: int, theirs: int, minute: Optional[int]) -> Optional[str]:
        """한 관측이 어느 규칙에 걸리는지(우선순위 hard > base > late). 아니면 None."""

        losing = int(theirs) - int(mine)
        if self.hard_deficit > 0 and losing >= self.hard_deficit:
            return KIND_HARD
        if losing >= self.base_deficit:
            return KIND_BASE
        if (
            self.late_minute > 0
            and minute is not None
            and int(minute) >= self.late_minute
            and losing >= self.late_deficit
        ):
            return KIND_LATE
        return None


@dataclass(frozen=True)
class ExitSettings:
    """서버가 내려주는 구매자별 자동 종료 설정 전부(규칙 + 비율)."""

    rules: ExitRules = ExitRules()
    ratio: float = 0.4                    # base(+late_ratio 없을 때 late) 쿼터 비율
    late_ratio: Optional[float] = None    # None = ratio 를 따른다


def _coerce_int(value, bounds: tuple[int, int]) -> Optional[int]:
    """정수 범위 검증. bool·소수·범위 밖·None 은 전부 None(= 기본값 유지)."""

    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number != int(number):   # NaN·소수
        return None
    number = int(number)
    if not (bounds[0] <= number <= bounds[1]):
        return None
    return number


def _coerce_ratio(value) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return None
    if not (0.0 <= ratio <= 1.0):        # NaN 도 여기서 걸린다
        return None
    return ratio


def parse_exit_settings(payload, defaults: Optional[ExitSettings] = None) -> ExitSettings:
    """서버 응답의 ``auto_exit_settings`` 를 검증해 ExitSettings 로 만든다.

    필드 단위 fail-safe: 빠졌거나 깨진 필드는 **그 필드만** 기본값(defaults)으로
    남고 나머지는 반영된다. 통째로 거부하면 필드 하나 오타에 모든 규칙이 기본으로
    돌아가 "패널에서 바꿨는데 안 바뀐다"가 된다. 이 값은 서명 밖 운영값이라
    (license_client 참고) 위조로 얻는 것도 자기 종료 규칙 조절뿐이다.
    """

    base = defaults or ExitSettings()
    if not isinstance(payload, dict):
        return base
    rules = base.rules
    fields = {}
    for name, bounds in SETTING_BOUNDS.items():
        value = _coerce_int(payload.get(name), bounds)
        fields[name] = getattr(rules, name) if value is None else value
    ratio = _coerce_ratio(payload.get("ratio"))
    late_ratio = _coerce_ratio(payload.get("late_ratio"))
    return ExitSettings(
        rules=ExitRules(**fields),
        ratio=base.ratio if ratio is None else ratio,
        # late_ratio 는 '없음(None)=기본 비율 따름'이 정상값이라 빠지면 None 으로 둔다.
        late_ratio=late_ratio,
    )


# ---------------------------------------------------------------------------
# 한 경기당 한 번 세기
# ---------------------------------------------------------------------------


class LossTracker:
    """'상대에게 deficit 점차 이상으로 지고 있는 경기'를 **경기당 한 번만** 세는 상태 머신.

    feed(now, reading) 로 관측을 넣으면 이번 관측으로 새 패배가 확정됐을 때만
    True 를 돌려준다. reading 은 (내 점수, 상대 점수) / SCORE_UNKNOWN / None. 규칙:

      * 열세 스코어(상대−나 >= deficit, 예: 0:2, 0:3, 1:3)가 confirm_count 번
        **연속** 읽혀야 확정한다. 0:2→0:3 처럼 값이 바뀌어도 열세이기만 하면
        연속으로 친다(같은 경기가 더 나빠진 것뿐이다).
      * None(스코어보드 없음)과 SCORE_UNKNOWN 은 연속 판정을 깨지 않는다 —
        리플레이·오버레이로 한두 프레임 가려지는 것은 정상이다. **열세가 아닌
        스코어**가 읽히면 그때 깬다.
      * 같은 경기에서 열세가 아닌 스코어(0:0/0:1/1:2)가 먼저 읽힌 적이 있어야
        확정한다. 스코어는 단조 증가라 진짜 2점차 열세는 반드시 0점차 구간을
        지나고, 글리프 매칭이 결정적이므로 이 조건이 '정적 화면 하나의 일관
        오독'을 차단하는 핵심 방어다(적대적 리뷰 확정). 대가: 경기 도중(이미
        열세)에 자동화를 켜면 그 경기는 못 센다 — 보수적 방향.
      * 한 번 확정하면 그 경기에서는 다시 세지 않는다(래치).
      * 아무것도(박스조차) 안 보이는 상태가 reset_seconds 지속돼야 경기 종료로
        간주해 래치·연속 판정·선행 관측을 푼다. SCORE_UNKNOWN 은 '진행 중'
        증거라 이 타이머를 리셋한다 — 미상 스코어 화면이 오래 이어져도
        래치가 풀리면 안 된다.
    """

    def __init__(
        self,
        *,
        deficit: int = 2,
        confirm_count: int = 3,
        reset_seconds: float = 60.0,
        require_prior_score: bool = True,
        rules: Optional[ExitRules] = None,
    ) -> None:
        if confirm_count < 1:
            raise ValueError("confirm_count 는 1 이상이어야 합니다.")
        if int(deficit) < 1:
            raise ValueError("deficit 는 1 이상이어야 합니다.")
        # rules 를 안 주면 예전 그대로 '기본 규칙 하나'다(hard/late 꺼짐) — 기존 호출부·
        # 테스트의 의미가 바뀌지 않는다. 규칙 세 개를 쓰려면 rules 를 준다.
        self.rules = rules or ExitRules(
            base_deficit=int(deficit), hard_deficit=0, late_minute=0
        )
        self.confirm_count = int(confirm_count)
        self.reset_seconds = float(reset_seconds)
        self.require_prior_score = bool(require_prior_score)

        # base/late 가 같은 쿼터(한 '진 판' 카운트)를 쓰면 래치도 하나다. late 비율이
        # 따로 있어 쿼터가 둘이면 래치도 둘 — 안 그러면 late 의 '방치' 결정이 같은
        # 경기의 base(다른 장부) 확정을 굶긴다(리뷰 확정).
        self.shared_quota_latch = True

        self._streak = 0                             # 어떤 규칙이든 연속으로 걸린 횟수
        self._hard_streak = 0                        # hard 규칙만 연속으로 걸린 횟수
        self._quota_fired = False                    # 이 경기에서 쿼터 규칙(base/late)이 확정됐나
        self._base_fired = False                     # (래치 분리 시) base 확정 여부
        self._late_fired = False                     # (래치 분리 시) late 확정 여부
        self._hard_fired = False                     # 이 경기에서 hard 가 확정됐나
        self._last_seen_at: Optional[float] = None   # 뭔가 보인 마지막 시각
        # 이 경기에서 본 '상대−나'의 최솟값. 선행 스코어 방어의 근거다: 어떤 규칙이든
        # **그 규칙의 점차보다 나은 스코어**를 먼저 봤어야 확정한다. 예전의 '규칙에 안
        # 걸린 스코어를 봤나'는 late(1점차) 에서 같은 (0,1) 픽셀이 선행 증거이자 판정
        # 근거가 되는 구멍이 있었다(리뷰 확정).
        self._min_losing_seen: Optional[int] = None
        self.last_kind: Optional[str] = None         # 마지막 확정 종류(로그용)

    @property
    def deficit(self) -> int:
        return self.rules.base_deficit

    @property
    def _latched(self) -> bool:
        """기존 이름 호환 — '이 경기는 쿼터 규칙으로 이미 확정됐다'."""

        return self._quota_fired

    @property
    def _seen_other(self) -> bool:
        """기존 이름 호환 — 기본 규칙 기준으로 '열세 아닌 스코어를 봤나'."""

        return (
            self._min_losing_seen is not None
            and self._min_losing_seen < self.rules.base_deficit
        )

    def set_rules(self, rules: ExitRules) -> None:
        """규칙만 바꾼다(경기 상태·래치는 보존). 서버 설정이 내려왔을 때 쓴다."""

        self.rules = rules

    def _deficit_of(self, kind: str) -> int:
        if kind == KIND_HARD:
            return self.rules.hard_deficit
        if kind == KIND_LATE:
            return self.rules.late_deficit
        return self.rules.base_deficit

    def _prior_ok(self, kind: str) -> bool:
        """선행 스코어 방어: 이 규칙의 점차보다 **나은** 스코어를 이 경기에서 봤나."""

        if not self.require_prior_score:
            return True
        return (
            self._min_losing_seen is not None
            and self._min_losing_seen < self._deficit_of(kind)
        )

    def resume_observation(self) -> None:
        """자동화를 정지했다 재시작할 때 부른다 — **래치는 유지**하고 관측만 비운다.

        트래커를 통째로 새로 만들면 0:2 로 확정(방치 결정)된 경기 도중 정지→재시작
        하는 것만으로 같은 경기가 두 번 세어지고, '방치'로 결정된 그 경기를 나가
        버린다(적대적 리뷰 확정 결함). 래치는 경기 종료(부재 reset_seconds)로만
        풀린다 — 정지해 있던 시간도 벽시계로 그대로 흐르므로 계산은 맞다.
        """
        self._streak = 0
        self._hard_streak = 0
        self._min_losing_seen = None

    def feed(self, now: float, reading: Reading, minute: Optional[int] = None) -> bool:
        """관측 한 번. 이번 관측으로 '진 판'이 새로 확정됐으면 True(종류는 last_kind)."""

        return self.observe(now, reading, minute) is not None

    def observe(self, now: float, reading: Reading, minute: Optional[int] = None) -> Optional[str]:
        """feed 와 같되 확정 종류(KIND_*)를 돌려준다. 확정이 아니면 None.

        경기당 래치가 둘이다: 쿼터 규칙(base/late)은 **한 번만** 확정되고(둘 다 같은
        '진 판' 카운트라 둘이 따로 걸리면 한 경기가 두 번 세어진다), hard 는 쿼터
        규칙이 '방치'로 결정한 **뒤에도** 한 번 더 확정될 수 있다 — 0:2 방치 판이
        0:3 으로 벌어지면 비율과 무관하게 나가라는 게 규칙의 뜻이다. hard 가 먼저
        확정되면 그 경기는 끝난 것이므로 쿼터 래치도 같이 건다.
        """

        now = float(now)

        if reading is None:
            # 계속 안 보이면 경기가 끝난 것이다 → 다음 경기를 셀 수 있게 푼다.
            if (
                self._last_seen_at is not None
                and now - self._last_seen_at >= self.reset_seconds
            ):
                self._streak = 0
                self._hard_streak = 0
                self._quota_fired = False
                self._base_fired = False
                self._late_fired = False
                self._hard_fired = False
                self._min_losing_seen = None
                self._last_seen_at = None
            return None

        # 미상 포함, 뭔가 보였다 = 경기 진행 중 → 종료 타이머 리셋.
        self._last_seen_at = now
        if reading == SCORE_UNKNOWN:
            return None

        mine, theirs = int(reading[0]), int(reading[1])
        losing = theirs - mine
        # 이 경기에서 본 가장 나은 스코어(상대−나 최솟값)를 기록한다 — 선행 증거.
        if self._min_losing_seen is None or losing < self._min_losing_seen:
            self._min_losing_seen = losing
        kind = self.rules.classify(mine, theirs, minute)
        if kind is None:
            # 어느 규칙에도 안 걸린다(동점·근소한 열세·우세·후반 전 1점차 전부) →
            # 연속 판정을 깬다.
            self._streak = 0
            self._hard_streak = 0
            return None
        if not self._prior_ok(kind):
            return None

        self._streak += 1
        self._hard_streak = self._hard_streak + 1 if kind == KIND_HARD else 0

        if kind == KIND_HARD:
            if self._hard_fired or self._hard_streak < self.confirm_count:
                return None
            self._hard_fired = True
            self._quota_fired = True
            self._hard_streak = 0
            self._streak = 0
            self.last_kind = KIND_HARD
            return KIND_HARD

        if self._hard_fired:
            return None
        if self.shared_quota_latch:
            if self._quota_fired:
                return None
        elif (self._base_fired if kind == KIND_BASE else self._late_fired):
            return None
        if self._streak < self.confirm_count:
            return None
        self._quota_fired = True
        if kind == KIND_BASE:
            self._base_fired = True
        else:
            self._late_fired = True
        self._streak = 0
        self.last_kind = kind
        return kind


# ---------------------------------------------------------------------------
# 40% 쿼터
# ---------------------------------------------------------------------------


class ExitQuota:
    """센 패배 중 몇 번째에 나갈지 정한다 — 장기 비율을 정확히 보장한다.

    규칙: n 번째 패배까지의 종료 허용량 = floor(n × ratio). 아직 허용량보다 적게
    나갔으면 이번에 나간다. ratio=0.4 면 20판에서 정확히 8판 나간다
    (3·5·8·10·13·15·18·20번째). 난수를 쓰지 않는 이유: 확률로 하면 표본이 작을 때
    비율이 크게 어긋날 수 있고(운 나쁘면 연속으로 나가 비매너가 몰린다),
    이 방식은 어떤 구간을 잘라도 비율을 넘지 않는다.
    """

    def __init__(self, ratio: float = 0.4) -> None:
        self.lost_games = 0
        self.exits_done = 0
        self.set_ratio(ratio)

    def set_ratio(self, ratio: float) -> None:
        """비율만 바꾼다(카운트는 보존) — 서버 운영 설정이 내려왔을 때 쓴다.

        카운트를 지우면 안 되는 이유: 비율은 앱 세션 전체의 '지금까지 판수' 위에서
        floor(n×ratio) 로 계산된다. 카운트가 초기화되면 이미 나간 판이 잊혀
        직후 몇 판을 연속으로 나가 비매너가 몰린다.
        """
        # NaN 도 여기서 걸린다(비교가 False → not → 거부).
        if not (0.0 <= ratio <= 1.0):
            raise ValueError("ratio 는 0.0~1.0 이어야 합니다.")
        self.ratio = float(ratio)

    def register_loss(self) -> bool:
        """패배 한 판을 등록하고, 이번 판에 나갈지 돌려준다."""
        self.lost_games += 1
        allowed = int(self.lost_games * self.ratio)
        if self.exits_done < allowed:
            self.exits_done += 1
            return True
        return False


# ---------------------------------------------------------------------------
# 경기 시계 — '70분 이후' 규칙의 재료
# ---------------------------------------------------------------------------
#
# 스코어 숫자와 달리 시계("84:10")는 winocr 가 읽는다(실측: 저장 프레임 8장 전부,
# 2배 확대·en). 시계는 상대 닉네임 오른쪽의 **흰 가로 박스**에만 있으므로 스코어
# 박스처럼 연결 요소로 그 박스를 먼저 찾고(위치가 아니라 모양으로 — 닉네임 길이에
# 흔들리지 않게) 그 안만 OCR 한다. 숫자 닉네임("4903")은 검은 박스라 여기 안 들어온다.


def find_clock_box(gray, region_fractions):
    """시계가 든 흰 가로 박스를 크롭으로 돌려준다(원본 픽셀). 없으면 None.

    스코어 박스(정사각형)와 달리 가로로 긴 밝은 덩어리를 고른다. 영역 안에서 가장
    큰 후보 하나만 쓴다 — 시계 박스는 하나뿐이다.
    """
    if cv2 is None:
        return None
    try:
        crop = crop_score_region(gray, region_fractions)
        if crop is None:
            return None
        peak = int(crop.max())
        if peak < 180:
            return None
        mask = (crop >= max(180, peak - 25)).astype(np.uint8)
        count, _labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        best = None
        for i in range(1, count):
            x, y, width, height, area = stats[i]
            if height < 15 or width < 40:
                continue
            if not (1.8 <= width / height <= 5.0):
                continue                  # 정사각형(스코어)·가는 선은 제외
            if area < 0.45 * width * height:
                continue
            if best is None or area > best[0]:
                best = (area, x, y, width, height)
        if best is None:
            return None
        _, x, y, width, height = best
        return crop[y:y + height, x:x + width]
    except Exception:
        return None


_CLOCK_COLON_RE = re.compile(r"(\d{1,3})\s*[:;.•·,]\s*(\d{2})")


def parse_clock_text(text) -> Optional[tuple[int, int]]:
    """OCR 문자열 → (분, 초). 못 읽으면 None.

    실측 출력 형태를 다 받는다: "84:10", "62•.04", "6454"(콜론 누락), "90:oo"(0→o),
    "90:00+3"(추가시간 — '+' 앞만 본다). 초가 60 이상이거나 분이 130 을 넘으면 버린다.
    """
    if not text:
        return None
    cleaned = str(text).replace("O", "0").replace("o", "0").replace("l", "1").replace("I", "1")
    cleaned = cleaned.split("+", 1)[0]
    match = _CLOCK_COLON_RE.search(cleaned)
    if match:
        minute, second = int(match.group(1)), int(match.group(2))
    else:
        # 콜론이 통째로 빠진 경우("6454")만 받는다. 3자리("841" = "84:1" 의 자리 누락)는
        # (8,41) 로 76분 역행을 만들어 확정을 흔들 뿐이라 버린다.
        digits = re.sub(r"\D", "", cleaned)
        if len(digits) != 4:
            return None
        minute, second = int(digits[:-2]), int(digits[-2:])
    if not (0 <= second <= 59 and 0 <= minute <= 130):
        return None
    return minute, second


class ClockTracker:
    """시계 읽기의 그럴듯함을 검사해 '확정 분'을 낸다(순수 로직).

    규칙: 연속 consensus 회 읽힌 값이 **서로 이어져야**(직전 값에서 jump_minutes 분
    안쪽으로 올라가야) 확정한다(경기 시계는 단조 증가·5초에 1분 미만). 크게 내려가면
    (새 경기·오독) 확정을 버리고, 크게 **올라가도**(1↔7 같은 상향 오독) 확정값은 두고
    새 값이 consensus 회 이어져야 받는다 — 상향 단발 오독 한 번이 '70분 이후'를 열면
    14분 경기에서 종료가 나간다(리뷰 확정 결함). None(시계 없음)이 reset_seconds 이상
    이어지면 경기가 끝난 것으로 보고 비운다. 확정된 분은 다음 확정 때까지 유지된다
    (정지·하프타임 중 같은 값 반복은 정상).
    """

    def __init__(
        self,
        *,
        consensus: int = 2,
        reset_seconds: float = 60.0,
        jump_minutes: int = 2,
    ) -> None:
        self.consensus = max(1, int(consensus))
        self.reset_seconds = float(reset_seconds)
        self.jump_minutes = max(1, int(jump_minutes))
        self.minute: Optional[int] = None
        self._pending: Optional[tuple[int, int]] = None
        self._streak = 0
        self._last_seen_at: Optional[float] = None

    def reset(self) -> None:
        self.minute = None
        self._pending = None
        self._streak = 0
        self._last_seen_at = None

    def feed(self, now: float, reading: Optional[tuple[int, int]]) -> Optional[int]:
        now = float(now)
        if reading is None:
            if (
                self._last_seen_at is not None
                and now - self._last_seen_at >= self.reset_seconds
            ):
                self.reset()
            return self.minute
        self._last_seen_at = now
        minute, second = int(reading[0]), int(reading[1])
        if self._pending is not None:
            regressed = (minute, second) < self._pending
            jumped_up = minute - self._pending[0] > self.jump_minutes
            if regressed or jumped_up:
                # **확정값** 기준으로 크게 역행했을 때만 확정을 버린다(새 경기·오독).
                # pending 기준으로 비교하면 '14→(오독)74→14' 에서 멀쩡한 확정 14 까지 버린다.
                if self.minute is not None and self.minute - minute > self.jump_minutes:
                    self.minute = None
                # 상향 점프는 확정값을 **유지**한다(단발 오독일 가능성) — 새 값이
                # consensus 회 이어지면 그때 받는다(가림 뒤 재등장·늦은 시작 경로 보존).
                self._pending = (minute, second)
                self._streak = 1
                return self.minute
        self._pending = (minute, second)
        self._streak += 1
        if self._streak >= self.consensus:
            self.minute = minute
        return self.minute
