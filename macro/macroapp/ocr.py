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
    text = getattr(res, "text", None)
    if isinstance(text, str):
        return text
    return None


def extract_rank(image_bgr_or_gray: np.ndarray, logger=None) -> Optional[int]:
    """이미지에서 '숫자+위' 패턴의 첫 등수를 반환합니다. 없으면 None."""
    if winocr is None:
        return None
    try:
        text = _recognize_text(image_bgr_or_gray)
        if not text:
            return None
        cleaned = text.replace(",", "").replace(" ", "")
        m = _RANK_RE.search(cleaned)
        if m:
            return int(m.group(1))
    except Exception as exc:
        if logger:
            logger(f"[OCR] 등수 인식 중 오류: {exc}")
    return None
