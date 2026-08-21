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
    ) -> None:
        if confirm_count < 1:
            raise ValueError("confirm_count 는 1 이상이어야 합니다.")
        if int(deficit) < 1:
            raise ValueError("deficit 는 1 이상이어야 합니다.")
        self.deficit = int(deficit)
        self.confirm_count = int(confirm_count)
        self.reset_seconds = float(reset_seconds)
        self.require_prior_score = bool(require_prior_score)

        self._streak = 0
        self._latched = False
        self._last_seen_at: Optional[float] = None   # 뭔가 보인 마지막 시각
        self._seen_other = False                     # 이 경기에서 열세 아닌 스코어를 봤나

    def resume_observation(self) -> None:
        """자동화를 정지했다 재시작할 때 부른다 — **래치는 유지**하고 관측만 비운다.

        트래커를 통째로 새로 만들면 0:2 로 확정(방치 결정)된 경기 도중 정지→재시작
        하는 것만으로 같은 경기가 두 번 세어지고, '방치'로 결정된 그 경기를 나가
        버린다(적대적 리뷰 확정 결함). 래치는 경기 종료(부재 reset_seconds)로만
        풀린다 — 정지해 있던 시간도 벽시계로 그대로 흐르므로 계산은 맞다.
        """
        self._streak = 0
        self._seen_other = False

    def feed(self, now: float, reading: Reading) -> bool:
        now = float(now)

        if reading is None:
            # 계속 안 보이면 경기가 끝난 것이다 → 다음 경기를 셀 수 있게 푼다.
            if (
                self._last_seen_at is not None
                and now - self._last_seen_at >= self.reset_seconds
            ):
                self._streak = 0
                self._latched = False
                self._seen_other = False
                self._last_seen_at = None
            return False

        # 미상 포함, 뭔가 보였다 = 경기 진행 중 → 종료 타이머 리셋.
        self._last_seen_at = now
        if reading == SCORE_UNKNOWN:
            return False

        mine, theirs = int(reading[0]), int(reading[1])
        if theirs - mine < self.deficit:
            # 열세가 아니다(동점·근소한 열세·우세 전부) → 연속 판정을 깨고,
            # '이 경기의 정상 스코어를 봤다'는 선행 증거로 기록한다.
            self._streak = 0
            self._seen_other = True
            return False
        if self._latched:
            return False
        if self.require_prior_score and not self._seen_other:
            return False

        self._streak += 1
        if self._streak < self.confirm_count:
            return False

        self._latched = True
        self._streak = 0
        return True


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
