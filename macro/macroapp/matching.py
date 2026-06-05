from __future__ import annotations
from typing import Optional

import cv2
import numpy as np

from macroapp.logging_util import LogCallback
from macroapp.config import TargetImage, DOWNSCALE_FACTOR

_INTER_AREA = getattr(cv2, "INTER_AREA", getattr(cv2, "INTER_LINEAR", 1))

def find_template_center(
    screen_gray: np.ndarray,
    target: TargetImage,
    logger: Optional[LogCallback] = None,
) -> tuple[Optional[tuple[int, int]], float]:
    """
    2단계 템플릿 매칭: 축소 이미지로 빠른 사전 필터 → 원본에서 정밀 매칭.
    """

    log = logger or print

    if target.image_gray is None:
        log(f"[오류] {target.name} 이미지가 로드되지 않았습니다.")
        return None, 0.0

    target_height, target_width = target.image_gray.shape[:2]
    screen_height, screen_width = screen_gray.shape[:2]

    if target_width > screen_width or target_height > screen_height:
        return None, 0.0

    f = DOWNSCALE_FACTOR
    small_tw = target_width // f
    small_th = target_height // f

    if small_tw < 4 or small_th < 4:
        result = cv2.matchTemplate(screen_gray, target.image_gray, cv2.TM_CCOEFF_NORMED)
        _, max_value, _, max_location = cv2.minMaxLoc(result)
        score = float(max_value)
        if score < target.threshold:
            return None, score
        top_left_x, top_left_y = max_location
        return (top_left_x + target_width // 2, top_left_y + target_height // 2), score

    if not hasattr(target, '_small_gray') or target._small_gray is None:
        target._small_gray = cv2.resize(target.image_gray, (small_tw, small_th), interpolation=_INTER_AREA)

    # 캡처 스레드가 버퍼를 덮어써도 안전하도록 다운샘플은 독립 복사본으로 만듭니다.
    small_screen = screen_gray[::f, ::f].copy()
    small_result = cv2.matchTemplate(small_screen, target._small_gray, cv2.TM_CCOEFF_NORMED)
    _, small_max, _, small_loc = cv2.minMaxLoc(small_result)

    if small_max < target.threshold * 0.85:
        return None, float(small_max)

    rough_x = small_loc[0] * f
    rough_y = small_loc[1] * f
    margin = max(target_width, target_height) // 2
    roi_x1 = max(0, rough_x - margin)
    roi_y1 = max(0, rough_y - margin)
    roi_x2 = min(screen_width, rough_x + target_width + margin)
    roi_y2 = min(screen_height, rough_y + target_height + margin)

    # ROI가 템플릿보다 작거나(경계) 0/음수 크기면 전체 화면 재매칭으로 폴백합니다.
    if (roi_x2 - roi_x1 < target_width or roi_y2 - roi_y1 < target_height
            or roi_x2 - roi_x1 <= 0 or roi_y2 - roi_y1 <= 0):
        result = cv2.matchTemplate(screen_gray, target.image_gray, cv2.TM_CCOEFF_NORMED)
        _, max_value, _, max_location = cv2.minMaxLoc(result)
        score = float(max_value)
        if score < target.threshold:
            return None, score
        top_left_x, top_left_y = max_location
        return (top_left_x + target_width // 2, top_left_y + target_height // 2), score

    roi = screen_gray[roi_y1:roi_y2, roi_x1:roi_x2]
    try:
        result = cv2.matchTemplate(roi, target.image_gray, cv2.TM_CCOEFF_NORMED)
        _, max_value, _, max_location = cv2.minMaxLoc(result)
    except cv2.error:
        return None, 0.0
    score = float(max_value)

    if score < target.threshold:
        return None, score

    top_left_x = max_location[0] + roi_x1
    top_left_y = max_location[1] + roi_y1
    center_x = top_left_x + target_width // 2
    center_y = top_left_y + target_height // 2

    return (center_x, center_y), score
