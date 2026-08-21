"""화면에서 등수(숫자+위)를 읽어내는 OCR.

Windows 내장 OCR(winocr / Windows.Media.Ocr)을 사용합니다. 구매자는 추가 설치가
필요 없습니다(한글 OCR 언어팩만 있으면 됨 — 한국어 Windows엔 기본 포함되는 경우가 많음).
이 모듈은 실패해도 매크로 본체에 영향을 주지 않도록 모두 가드되어 있습니다.
"""

from __future__ import annotations

import re
from typing import Optional

import numpy as np

try:
    import cv2  # 전처리용(매칭에서 이미 의존). 없으면 전처리만 건너뛰고 원본으로 OCR.
except Exception:  # noqa: BLE001
    cv2 = None

try:
    import winocr  # Windows 전용
except Exception as exc:  # noqa: BLE001
    winocr = None
    WINOCR_IMPORT_ERROR = exc
else:
    WINOCR_IMPORT_ERROR = None

from macroapp.config import (
    RANK_OCR_TARGET_HEIGHT,
    RANK_OCR_MAX_UPSCALE,
    RANK_OCR_MAX_WIDTH,
    RANK_OCR_INVERT_FALLBACK,
    RANK_OCR_VOTE_MIN,
    RANK_OCR_PANEL_GAP_SECONDS,
    RANK_OCR_COMMIT_AFTER_GONE,
    SKIP_A_MATCH_THRESHOLD,
    SKIP_S_MATCH_THRESHOLD,
    SKIP_A_ICON_MATCH_THRESHOLD,
    SKIP_S_ICON_MATCH_THRESHOLD,
)

# 등수 현실 범위(거짓 양성·자릿수 폭주 차단). 0/음수/비현실값은 버린다.
_RANK_MIN = 1
_RANK_MAX = 2_000_000

# "1,681 위" / "1681위" 등에서 등수 숫자만 추출 (쉼표 제거 후).
_RANK_RE = re.compile(r"(\d{1,8})\s*위")

# "2229점" 등에서 점수만 추출 (쉼표 제거 후). 3자리 이상이라 '3부'의 3 등은 안 걸림.
_SCORE_RE = re.compile(r"(\d{3,7})점")

# 챔피언스/슈퍼 챔피언스 감독 티어 판별 토큰(OCR 오인식 대비 느슨하게).
_CHAMP_TOKENS = ("챔피언스", "챔피언", "챔피")

# 티어 추출: "월드클래스 1부 감독"처럼 'OO 감독'으로 끝나는 문구.
# FC Online 감독 모드 티어명을 앵커로 써서 앞 노이즈("내 정보 ..." 등)를 안 먹게 한다.
# (긴 이름 먼저 — '슈퍼챔피언스'가 '챔피언스'보다 먼저 와야 부분일치 방지.)
_TIER_NAMES = (
    "슈퍼챔피언스", "슈퍼 챔피언스", "챔피언스", "마스터",
    "월드클래스", "챌린저", "세미프로", "프로", "아마추어", "비기너", "유스", "레전드",
)
_TIER_RE = re.compile(r"((?:" + "|".join(_TIER_NAMES) + r")\s*\d*\s*부?\s*감독)")
# 폴백: 알려진 티어명이 아니어도 'OO 감독'(한 단어 + 선택적 N부)은 잡되 앞 노이즈는 제외.
_TIER_FALLBACK_RE = re.compile(r"([가-힣]+(?:\s*\d+\s*부)?\s*감독)")

# 티어 정규화용 표준명(공백 제거형). 긴 이름 먼저 → '슈퍼챔피언스'가 '챔피언스'에 안 묻힘.
_TIER_CANON = (
    "슈퍼챔피언스", "챔피언스", "마스터", "월드클래스", "챌린저",
    "세미프로", "아마추어", "비기너", "레전드", "프로", "유스",
)
# 표준키(공백 제거형) → 표시형(공백 복원). 디스코드 표기를 보기 좋게 유지.
_TIER_DISPLAY = {"슈퍼챔피언스": "슈퍼 챔피언스"}

# SKIP 자동 넘기기 토큰 (대소문자 무관, 공백 제거 후 부분일치). "skip"이 SKIP/Skip/skip 모두 커버.
_SKIP_TOKENS = ("skip", "스킵")
# '아무 키나' 프롬프트 근거 토큰. FC 의 일반 프롬프트는
# ``SKIP 하려면 아무 키나 누르세요. (Enter 키 제외)`` 로 표시된다. 공백을 지운 뒤
# 부분일치하므로 "아무 키나"→"아무키나" 도 "아무키" 로 잡힌다. 한글과 영문 Enter 를
# 둘 다 보는 이유: 실측(저장된 크롭 112장)에서 한글 문구 85%·Enter 84% 가 각각
# 읽혔고 어느 한쪽만 읽히는 프레임이 흔했다 — 둘을 합쳐야 한 프레임 인식률이 오른다.
# SKIP 토큰이 함께 있어야만 쓰인다(classify_skip_prompt) — "계속하려면 아무 키나" 류
# 다른 화면에 오반응하지 않게. "아무기" 는 winocr 가 실제로 자주 내놓는 오독
# ("아무기나누르세요" — 실측 크롭 전부가 이렇게 읽혔다).
_ANY_KEY_TOKENS = ("enter", "anykey", "아무키", "아무기", "하려면")


def _normalize_ocr_text(raw: str) -> str:
    """OCR 텍스트의 공백류를 일관되게 제거한다(ASCII 공백 + 줄바꿈 + 전각공백 U+3000 + NBSP).

    winocr가 작은 박스를 멀티라인으로 돌려줄 때('2229\\n점' · 's\\nkip' · '팀\\n정\\n보')
    부분일치 검사(게이트·점수·SKIP)가 깨지지 않게 한다. 쉼표는 숫자 필드에서만 따로 제거.
    """
    if not raw:
        return ""
    return (raw.replace("\r", "").replace("\n", "")
               .replace("　", "").replace("\xa0", "").replace(" ", ""))


def on_match_screen(image_bgr_or_gray: np.ndarray, tokens=("팀정보", "유니폼"), logger=None) -> bool:
    """탭 바 영역에 '팀 정보/유니폼' 글자가 보이면 True(=공식경기 감독모드 화면).

    이 화면에서만 등수/점수를 읽게 해서 구단 관리 등 다른 화면의 오인식을 막는다.
    OCR을 못 쓰면(winocr 없음) 게이트를 무력화(True)해 기존 동작을 유지한다.
    인식 실패/미검출이면 False(보수적으로 등수 OCR 건너뜀).
    """
    if winocr is None:
        return True
    try:
        text = _recognize_text(image_bgr_or_gray)
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger(f"[게이트 OCR] 인식 중 오류: {exc}")
        return False
    cleaned = _normalize_ocr_text(text)
    return any(tok in cleaned for tok in tokens)


def ocr_available() -> bool:
    return winocr is not None


def _recognize_text(image_bgr_or_gray: np.ndarray, lang: str = "ko") -> str:
    """이미지를 텍스트로 변환. winocr의 여러 API 형태를 방어적으로 시도합니다."""
    if winocr is None:
        return ""
    img = np.ascontiguousarray(image_bgr_or_gray)
    # 1) 동기 cv2 API
    for fn_name in ("recognize_cv2_sync", "recognize_cv2"):
        fn = getattr(winocr, fn_name, None)
        if fn is None:
            continue
        try:
            res = fn(img, lang)
            text = _result_text(res)
            if text is not None:
                return text
        except TypeError:
            try:
                res = fn(img)
                text = _result_text(res)
                if text is not None:
                    return text
            except Exception:
                pass
        except Exception:
            pass
    # 2) 코루틴 API → asyncio로 실행
    afn = getattr(winocr, "recognize_cv2", None)
    if afn is not None:
        try:
            import asyncio
            res = asyncio.run(afn(img, lang))
            text = _result_text(res)
            if text is not None:
                return text
        except Exception:
            pass
    return ""


def _result_text(res) -> Optional[str]:
    if res is None:
        return None
    if isinstance(res, str):
        return res
    # winocr 0.0.15: recognize_*_sync는 dict를 반환 → {'text': ..., 'lines': [...]}
    if isinstance(res, dict):
        text = res.get("text")
        return text if isinstance(text, str) else None
    # 구버전 winocr: .text 속성을 가진 결과 객체
    text = getattr(res, "text", None)
    return text if isinstance(text, str) else None


def extract_rank(image_bgr_or_gray: np.ndarray, logger=None) -> Optional[int]:
    """이미지에서 '숫자+위' 패턴의 첫 등수를 반환합니다. 없으면 None."""
    info = read_rank_panel(image_bgr_or_gray, logger=logger)
    return info["rank"]


def _levenshtein(a: str, b: str) -> int:
    """짧은 문자열용 편집거리(티어명 스냅 판정에만 사용)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _canon_tier(tier_text: str) -> Optional[str]:
    """OCR로 읽은 티어 문구를 표준 티어명으로 보수적으로 스냅합니다.

    편집거리가 충분히 가까울 때만 표준명으로 교체하고(자모 1~2글자 오인식 보정),
    너무 멀면 None을 반환해 버립니다 — 모든 실제 티어는 _TIER_CANON에 있으므로,
    못 좁히는 'OO 감독'은 상단 제목 등 노이즈('공식경기 감독'→'경기 감독')로 보고 폐기.
    'N부'는 살려 두고, 표시형은 공백을 복원합니다('슈퍼챔피언스'→'슈퍼 챔피언스').
    """
    floor = re.search(r"([1-9])\s*부", tier_text)
    name_part = re.sub(r"[0-9]|부|감독|\s", "", tier_text)
    if not name_part:
        return None
    best = min(_TIER_CANON, key=lambda c: _levenshtein(name_part, c))
    # 길이 가드: 표준명이 읽은 조각보다 크게 길면(짧은 노이즈→긴 티어 스냅) 거부.
    # '공'·'경기' 같은 짧은 노이즈가 '슈퍼챔피언스' 등으로 둔갑하는 거짓 티어를 차단.
    if len(best) > len(name_part) + 1:
        return None
    if _levenshtein(name_part, best) <= max(1, len(best) // 4):
        disp = _TIER_DISPLAY.get(best, best)
        return f"{disp} {floor.group(1)}부 감독" if floor else f"{disp} 감독"
    return None   # 알려진 티어로 못 좁힘 → 노이즈로 보고 버림(거짓 티어 차단)


def _parse_rank_text(raw: str) -> dict:
    """OCR 텍스트(원문)에서 등수/티어/점수를 추출하는 순수 함수.

    winocr 없이도(Mac에서) 단위 테스트할 수 있도록 OCR 호출과 분리되어 있습니다.
    """
    result = {"rank": None, "is_champion": False, "tier": None,
              "score": None, "has_panel": False}
    raw = raw or ""
    cleaned = _normalize_ocr_text(raw).replace(",", "")
    m = _RANK_RE.search(cleaned)
    if m:
        rank = int(m.group(1))
        if _RANK_MIN <= rank <= _RANK_MAX:   # 0/비현실값 폐기
            result["rank"] = rank
            result["has_panel"] = True
    result["is_champion"] = any(tok in cleaned for tok in _CHAMP_TOKENS)
    sm = _SCORE_RE.search(cleaned)
    if sm:
        result["score"] = int(sm.group(1))
        result["has_panel"] = True
    # 티어("OO 감독") 추출 — 공백 보존 위해 원본에서 찾고, 표준명으로 스냅.
    # 못 좁히면(노이즈) 버린다 → 거짓 티어가 패널로 오인되지 않게.
    tm = _TIER_RE.search(raw) or _TIER_FALLBACK_RE.search(raw)
    if tm:
        canon = _canon_tier(tm.group(1))
        if canon:
            result["tier"] = canon
            result["has_panel"] = True
    return result


def _preprocess_rank(gray: np.ndarray) -> np.ndarray:
    """등수 crop을 OCR하기 좋게 만든다: 목표 높이로 업스케일 + 대비 스트레칭.

    - 해상도 무관하게 글자 크기를 일정화(작은 글자에서 winocr 인식률↑).
    - 폭 상한으로 OCR 1회 비용을 눌러 0.7초 윈도우 내 표본 수를 확보.
    - 이진화/디노이즈는 한글 획을 망쳐서 일부러 안 함(grayscale 그대로가 winocr에 유리).
    """
    if cv2 is None or gray is None or gray.size == 0:
        return gray
    h, w = gray.shape[:2]
    if h <= 0 or w <= 0:
        return gray
    # 목표 높이·폭 상한·업스케일 상한을 한 번에 반영한 단일 스케일(업→다운 이중 리샘플 제거).
    # 폭 제약이 있으면 그만큼만 키우고(또는 줄이고) OCR 1회 비용을 상한 안에 유지.
    scale = min(RANK_OCR_TARGET_HEIGHT / float(h), RANK_OCR_MAX_UPSCALE)
    if RANK_OCR_MAX_WIDTH:
        scale = min(scale, RANK_OCR_MAX_WIDTH / float(w))
    if scale > 1.001:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    elif scale < 0.999:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    # 2/98 퍼센타일 대비 스트레칭(반짝이 픽셀에 안 휘둘리게 클리핑).
    lo, hi = np.percentile(gray, (2, 98))
    if hi > lo:
        gray = np.clip((gray.astype(np.float32) - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
    return gray


def read_rank_panel(image_bgr_or_gray: np.ndarray, logger=None) -> dict:
    """OCR로 등수 패널 정보를 읽습니다(전처리 + 실패 시 반전 폴백).

    반환:
    - rank: 등수 숫자(없으면 None)
    - is_champion: '챔피언스/슈퍼 챔피언스 감독' 티어 여부
    - tier: 'OO 감독' 티어 텍스트(예: '챌린저 3부 감독'). 없으면 None.
    - score: 점수 숫자(예: 2229). 없으면 None.
    - has_panel: 등수/티어/점수 중 하나라도 보여 패널이 떠 있는지
    """
    empty = {"rank": None, "is_champion": False, "tier": None,
             "score": None, "has_panel": False}
    if winocr is None:
        return empty
    try:
        prepped = _preprocess_rank(image_bgr_or_gray)
    except Exception:
        prepped = image_bgr_or_gray
    try:
        text = _recognize_text(prepped)
    except Exception as exc:
        if logger:
            logger(f"[OCR] 인식 중 오류: {exc}")
        return empty
    info = _parse_rank_text(text)
    # 1차에서 아무것도 못 읽었고 cv2가 있으면, 흑백 반전 후 1회만 재시도(light-on-dark 보강).
    if not info["has_panel"] and RANK_OCR_INVERT_FALLBACK and cv2 is not None:
        try:
            inv = cv2.bitwise_not(prepped)
            text2 = _recognize_text(inv)
            info2 = _parse_rank_text(text2)
            if info2["has_panel"]:
                info = info2
        except Exception:
            pass
    return info


class RankConsensus:
    """0.7초 동안 얻은 여러 OCR 읽기를 필드별 최빈값으로 합쳐 최종값을 확정합니다.

    - 단발 오인식(예: 1681→1631)을 다수결로 폐기.
    - 등수 단독 1표 + 동반 근거(티어/점수) 없음 → 거짓 양성 의심으로 보류.
    - 패널이 사라지면(소멸 후 일정 시간) 1표라도 graceful 확정(놓치지 않기).
    이 클래스는 winocr와 무관한 순수 로직이라 Mac에서 단위 테스트할 수 있습니다.
    """

    def __init__(self, vote_min: int = RANK_OCR_VOTE_MIN,
                 gap: float = RANK_OCR_PANEL_GAP_SECONDS,
                 commit_after_gone: float = RANK_OCR_COMMIT_AFTER_GONE):
        self.vote_min = vote_min
        self.gap = gap
        self.commit_after_gone = commit_after_gone
        self.reset()

    def reset(self) -> None:
        self._t0: Optional[float] = None
        self._last_seen = -1.0
        self._rank: dict = {}
        self._tier: dict = {}
        self._score: dict = {}

    @staticmethod
    def _bump(table: dict, value, now: float) -> None:
        if value is None:
            return
        if value in table:
            table[value][0] += 1
            table[value][1] = now
        else:
            table[value] = [1, now]

    @staticmethod
    def _mode(table: dict):
        """(값, 표수) 반환. 동률은 가장 최근 본 값 우선."""
        if not table:
            return None, 0
        value, (count, _ts) = max(table.items(), key=lambda kv: (kv[1][0], kv[1][1]))
        return value, count

    def observe(self, info: dict, now: float) -> None:
        """OCR 1회 결과를 투표에 반영. 패널 경계(긴 공백)는 자동 초기화."""
        if self._t0 is not None and (now - self._last_seen) > self.gap:
            self.reset()   # 새 패널 등장 → 옛 표와 안 섞이게
        if not info.get("has_panel"):
            return
        if self._t0 is None:
            self._t0 = now
        self._last_seen = now
        self._bump(self._rank, info.get("rank"), now)
        self._bump(self._tier, info.get("tier"), now)
        self._bump(self._score, info.get("score"), now)

    def commit(self, now: float):
        """확정할 (rank, tier, score)를 반환하거나, 아직이면 None.

        확정 트리거: 최빈값 표 ≥ vote_min(패널 떠있는 중 즉시) 또는
        패널 소멸(now-last_seen ≥ commit_after_gone) 후 1표라도 있으면.
        """
        if self._t0 is None:
            return None
        rank, rc = self._mode(self._rank)
        tier, tc = self._mode(self._tier)
        score, sc = self._mode(self._score)
        strength = (rank is not None) + (tier is not None) + (score is not None)
        if strength == 0:
            return None
        gone = (now - self._last_seen) >= self.commit_after_gone
        # 거짓 양성 차단: 등수 단독 1표 + 동반 근거 없음 → 아직 떠 있으면 보류.
        if rank is not None and rc < self.vote_min and tier is None and score is None and not gone:
            return None
        ready = (rc >= self.vote_min or tc >= self.vote_min or sc >= self.vote_min) or (gone and strength >= 1)
        if not ready:
            return None
        return (rank, tier, score)


def classify_skip_prompt(image_bgr_or_gray: np.ndarray, logger=None):
    """Return ``(has_skip, input_hint)`` from a single OCR pass.

    The hint is deliberately conservative: ``"escape"`` is returned only
    when OCR actually contains ESC together with SKIP text.  This separates a
    captured ESC episode from later A/S template false positives without
    guessing from coordinates or sending input.
    """
    if winocr is None:
        return (False, None)
    try:
        text = _recognize_text(image_bgr_or_gray)
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger(f"[SKIP OCR] 인식 중 오류: {exc}")
        return (False, None)
    cleaned = _normalize_ocr_text(text).lower()
    has_skip = any(tok in cleaned for tok in _SKIP_TOKENS)
    if not has_skip:
        return (False, None)
    if "esc" in cleaned:
        return (True, "escape")
    # FC의 일반 프롬프트는 ``SKIP 하려면 아무 키나 누르세요. (Enter 키 제외)``로
    # 표시된다. 한글 문구와 영문 Enter 어느 쪽이든 읽히면 명시적 '아무 키나'
    # 프롬프트로 본다(A/S 템플릿보다 우선).
    if any(tok in cleaned for tok in _ANY_KEY_TOKENS):
        return (True, "any_key")
    if "start" in cleaned:
        return (True, "start")
    return (True, None)


def contains_skip(image_bgr_or_gray: np.ndarray, logger=None) -> bool:
    """이미지(또는 일부 영역)에 SKIP/스킵 글자가 보이면 True."""

    return classify_skip_prompt(image_bgr_or_gray, logger=logger)[0]


def read_clock_text(box_gray: np.ndarray) -> str:
    """경기 시계 박스(auto_exit.find_clock_box 가 잘라 준 흰 박스)의 글자를 읽는다.

    실측(저장 프레임 8장): 2배 확대 + 'en' 이 가장 안정적이었다 — ko 는 한 장을
    빈 문자열로 냈고, 3배는 콜론을 '•.' 로 더 자주 깨뜨렸다. 해석(분·초 파싱)은
    auto_exit.parse_clock_text 가 한다(순수 로직, winocr 없이 검증).
    """
    if winocr is None or cv2 is None or box_gray is None or box_gray.size == 0:
        return ""
    try:
        up = cv2.resize(box_gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        return _recognize_text(up, "en")
    except Exception:
        return ""


# '(A) SKIP'(초록 A 버튼이 붙은 스킵) 전용 템플릿. 빌드 시 gen_assets가 target_skip_a.png를
# _assets에 박아 넣으면 거기서, 개발 중엔 exe/소스 옆 느슨한 파일에서 로드한다.
# None=아직 시도 안 함, False=없음(다시 안 찾음), ndarray=로드된 grayscale 템플릿.
_SKIP_A_TEMPLATE = None
_SKIP_A_FILENAME = "target_skip_a.png"
_SKIP_S_TEMPLATES = None
_SKIP_S_FILENAMES = ("target_skip_s.png", "target_skip_s_dark.png")


def _load_skip_a_template(base_dir):
    """(A) SKIP 템플릿(grayscale)을 1회 로드해 캐시한다. 없거나 cv2 불가면 None."""
    global _SKIP_A_TEMPLATE
    if _SKIP_A_TEMPLATE is not None:
        return _SKIP_A_TEMPLATE if _SKIP_A_TEMPLATE is not False else None
    if cv2 is None:
        _SKIP_A_TEMPLATE = False
        return None
    try:
        from macroapp import config as _cfg
        raw = _cfg.read_target_image_bytes(base_dir, _SKIP_A_FILENAME)
        if not raw:
            _SKIP_A_TEMPLATE = False
            return None
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is None or img.size == 0:
            _SKIP_A_TEMPLATE = False
            return None
        _SKIP_A_TEMPLATE = img
        return img
    except Exception:
        _SKIP_A_TEMPLATE = False
        return None


def reset_skip_a_template() -> None:
    """A/S SKIP 캐시를 비워 다음 호출 때 템플릿을 다시 로드하게 한다."""
    global _SKIP_A_TEMPLATE, _SKIP_S_TEMPLATES
    _SKIP_A_TEMPLATE = None
    _SKIP_S_TEMPLATES = None


def _load_skip_s_templates(base_dir):
    """'[S] SKIP' 템플릿(grayscale)을 1회 로드해 캐시한다."""
    global _SKIP_S_TEMPLATES
    if _SKIP_S_TEMPLATES is not None:
        return _SKIP_S_TEMPLATES if _SKIP_S_TEMPLATES is not False else ()
    if cv2 is None:
        _SKIP_S_TEMPLATES = False
        return ()
    try:
        from macroapp import config as _cfg
        templates = []
        for filename in _SKIP_S_FILENAMES:
            raw = _cfg.read_target_image_bytes(base_dir, filename)
            if not raw:
                continue
            arr = np.frombuffer(raw, dtype=np.uint8)
            image = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
            if image is not None and image.size:
                templates.append(image)
        if not templates:
            _SKIP_S_TEMPLATES = False
            return ()
        _SKIP_S_TEMPLATES = tuple(templates)
        return _SKIP_S_TEMPLATES
    except Exception:
        _SKIP_S_TEMPLATES = False
        return ()


def match_skip_a(image_gray: np.ndarray, base_dir,
                 threshold: float = SKIP_A_MATCH_THRESHOLD):
    """'(A) SKIP' 버튼 템플릿이 image_gray 안에 보이면 (True, score, (cx,cy))를 반환한다.

    (cx,cy)는 매칭 중심(이미지 좌표) — 그 자리를 클릭해 A 버튼 없이 스킵할 때 쓴다.
    템플릿(target_skip_a.png)이 없거나 cv2 불가/이미지 이상이면 (False, 0.0, None) — 호출부는
    이 경우 OCR 텍스트→Start 경로로 안전하게 폴백한다. 매칭은 원본 해상도 grayscale에서
    수행해야 하므로(템플릿이 게임 해상도로 캡처됨) OCR용 축소본이 아닌 원본 crop을 넘길 것.
    """
    tmpl = _load_skip_a_template(base_dir)
    if tmpl is None or cv2 is None or image_gray is None or image_gray.size == 0:
        return (False, 0.0, None)
    th, tw = tmpl.shape[:2]
    ih, iw = image_gray.shape[:2]
    if th > ih or tw > iw:
        return (False, 0.0, None)
    try:
        res = cv2.matchTemplate(image_gray, tmpl, cv2.TM_CCOEFF_NORMED)
        _, mx, _, max_loc = cv2.minMaxLoc(res)
    except Exception:
        return (False, 0.0, None)
    # 공통 SKIP 글자만 일치하는 일반 프롬프트를 A형으로 오인하지 않도록,
    # 같은 위치의 왼쪽 A 아이콘도 독립적으로 검증한다.
    icon_width = max(8, int(round(tw * 0.32)))
    patch = image_gray[
        max_loc[1]:max_loc[1] + th,
        max_loc[0]:max_loc[0] + tw,
    ]
    try:
        icon_score = float(cv2.matchTemplate(
            patch[:, :icon_width],
            tmpl[:, :icon_width],
            cv2.TM_CCOEFF_NORMED,
        )[0, 0])
    except Exception:
        icon_score = 0.0
    # 매칭 중심(이미지 좌표) — 호출부가 이 자리를 '클릭'해서 A 없이 스킵할 수 있게 반환.
    center = (int(max_loc[0] + tw // 2), int(max_loc[1] + th // 2))
    return (
        float(mx) >= threshold and icon_score >= SKIP_A_ICON_MATCH_THRESHOLD,
        float(mx),
        center,
    )


def match_skip_s(
    image_gray: np.ndarray,
    base_dir,
    threshold: float = SKIP_S_MATCH_THRESHOLD,
):
    """'[S] SKIP' 템플릿이 보이면 (True, score, center)를 반환한다."""
    templates = _load_skip_s_templates(base_dir)
    if not templates or cv2 is None or image_gray is None or image_gray.size == 0:
        return (False, 0.0, None)
    ih, iw = image_gray.shape[:2]
    best_score = 0.0
    best_center = None
    matched = False
    for template in templates:
        th, tw = template.shape[:2]
        if th > ih or tw > iw:
            continue
        try:
            result = cv2.matchTemplate(
                image_gray,
                template,
                cv2.TM_CCOEFF_NORMED,
            )
            _, score, _, location = cv2.minMaxLoc(result)
        except Exception:
            continue
        if best_center is None or float(score) > best_score:
            best_score = float(score)
            best_center = (
                int(location[0] + tw // 2),
                int(location[1] + th // 2),
            )
        icon_width = max(8, int(round(tw * 0.40)))
        patch = image_gray[
            location[1]:location[1] + th,
            location[0]:location[0] + tw,
        ]
        try:
            icon_score = float(cv2.matchTemplate(
                patch[:, :icon_width],
                template[:, :icon_width],
                cv2.TM_CCOEFF_NORMED,
            )[0, 0])
        except Exception:
            icon_score = 0.0
        if (
            float(score) >= float(threshold)
            and icon_score >= SKIP_S_ICON_MATCH_THRESHOLD
        ):
            matched = True
    return (matched, best_score, best_center)
