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
    import winocr  # Windows 전용
except Exception as exc:  # noqa: BLE001
    winocr = None
    WINOCR_IMPORT_ERROR = exc
else:
    WINOCR_IMPORT_ERROR = None

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
    "슈퍼챔피언스", "슈퍼 챔피언스", "챔피언스",
    "월드클래스", "챌린저", "세미프로", "프로", "아마추어", "비기너", "유스", "레전드",
)
_TIER_RE = re.compile(r"((?:" + "|".join(_TIER_NAMES) + r")\s*\d*\s*부?\s*감독)")
# 폴백: 알려진 티어명이 아니어도 'OO 감독'(한 단어 + 선택적 N부)은 잡되 앞 노이즈는 제외.
_TIER_FALLBACK_RE = re.compile(r"([가-힣]+(?:\s*\d+\s*부)?\s*감독)")

# SKIP 자동 넘기기 토큰 (대소문자 무관, 공백 제거 후 부분일치). "skip"이 SKIP/Skip/skip 모두 커버.
_SKIP_TOKENS = ("skip", "스킵")


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


def read_rank_panel(image_bgr_or_gray: np.ndarray, logger=None) -> dict:
    """OCR 1회로 등수 패널 정보를 반환합니다.

    반환:
    - rank: 등수 숫자(없으면 None)
    - is_champion: '챔피언스/슈퍼 챔피언스 감독' 티어 여부
    - tier: 'OO 감독' 티어 텍스트(예: '챌린저 3부 감독'). 없으면 None.
    - score: 점수 숫자(예: 2229). 없으면 None.
    - has_panel: 등수/티어/점수 중 하나라도 보여 패널이 떠 있는지
    """
    result = {"rank": None, "is_champion": False, "tier": None,
              "score": None, "has_panel": False}
    if winocr is None:
        return result
    try:
        text = _recognize_text(image_bgr_or_gray)
    except Exception as exc:
        if logger:
            logger(f"[OCR] 인식 중 오류: {exc}")
        return result
    raw = text or ""
    cleaned = raw.replace(",", "").replace(" ", "")
    m = _RANK_RE.search(cleaned)
    if m:
        result["rank"] = int(m.group(1))
        result["has_panel"] = True
    result["is_champion"] = any(tok in cleaned for tok in _CHAMP_TOKENS)
    # 점수("2229점") 추출.
    sm = _SCORE_RE.search(cleaned)
    if sm:
        result["score"] = int(sm.group(1))
        result["has_panel"] = True
    # 티어("OO 감독") 추출 — 공백 보존 위해 원본에서 찾는다.
    tm = _TIER_RE.search(raw) or _TIER_FALLBACK_RE.search(raw)
    if tm:
        result["tier"] = re.sub(r"\s+", " ", tm.group(1)).strip()
        result["has_panel"] = True
    return result


def contains_skip(image_bgr_or_gray: np.ndarray, logger=None) -> bool:
    """이미지(또는 일부 영역)에 SKIP/스킵 글자가 보이면 True. OCR 불가/실패 시 False.

    대소문자 무관: 인식 텍스트를 소문자화하고 공백을 제거한 뒤 'skip'/'스킵' 부분일치.
    """
    if winocr is None:
        return False
    try:
        text = _recognize_text(image_bgr_or_gray)
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger(f"[SKIP OCR] 인식 중 오류: {exc}")
        return False
    cleaned = (text or "").lower().replace(" ", "")
    return any(tok in cleaned for tok in _SKIP_TOKENS)
