"""해상도 자동 보정(캡처 프레임 정규화)의 순수 로직 검증.

winocr/WGC/pywin32 없이 Mac에서도 돌아가도록 배율 계산과 좌표 왕복만 다룹니다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from macroapp.config import (  # noqa: E402
    CAPTURE_SCALE_MAX,
    CAPTURE_SCALE_MIN,
    TEMPLATE_REFERENCE_HEIGHT,
    capture_normalization_scale,
)


def _normalized_size(width: int, height: int) -> tuple[int, int]:
    """window._normalize_frame과 같은 규칙으로 정규화 후 크기를 계산합니다."""

    scale = capture_normalization_scale(height)
    if scale == 1.0:
        return width, height
    return max(1, round(width / scale)), max(1, round(height / scale))


def _frame_to_capture(x: int, y: int, scale: float) -> tuple[int, int]:
    """InactiveManager.frame_to_capture와 같은 역변환입니다."""

    if scale == 1.0:
        return int(x), int(y)
    return int(round(x * scale)), int(round(y * scale))


class CaptureScaleTests(unittest.TestCase):
    def test_reference_resolution_is_not_resized(self) -> None:
        """기존 1920 창모드(1928x1048)는 배율 1.0이라 리사이즈를 건너뜁니다."""

        self.assertEqual(capture_normalization_scale(TEMPLATE_REFERENCE_HEIGHT), 1.0)
        self.assertEqual(_normalized_size(1928, 1048), (1928, 1048))

    def test_small_height_jitter_stays_in_deadzone(self) -> None:
        """테두리 계산 차이로 몇 픽셀 흔들려도 불필요한 리사이즈를 하지 않습니다."""

        for height in (1044, 1048, 1052):
            with self.subTest(height=height):
                self.assertEqual(capture_normalization_scale(height), 1.0)

    def test_1440_window_is_normalized_to_reference_height(self) -> None:
        """2560x1440 창모드는 축소돼 기준 세로에 맞습니다."""

        scale = capture_normalization_scale(1408)
        self.assertAlmostEqual(scale, 1408 / 1048, places=6)
        width, height = _normalized_size(2568, 1408)
        self.assertEqual(height, TEMPLATE_REFERENCE_HEIGHT)
        # 16:9 → 16:9라 정규화 가로도 기준(1928)에 가깝습니다.
        # 창 테두리는 해상도에 비례하지 않아 약간의 차이만 남습니다.
        self.assertLess(abs(width - 1928), 40)

    def test_ultrawide_keeps_native_scale(self) -> None:
        """21:9(2560x1080)는 세로가 같아 UI 크기도 같으므로 보정하지 않습니다."""

        self.assertEqual(capture_normalization_scale(1048), 1.0)
        self.assertEqual(_normalized_size(2568, 1048), (2568, 1048))

    def test_unsupported_scale_is_left_alone(self) -> None:
        """지원 범위를 벗어나면 억지로 늘리지 않고 원본을 그대로 씁니다."""

        too_small = int(TEMPLATE_REFERENCE_HEIGHT * CAPTURE_SCALE_MIN) - 1
        too_large = int(TEMPLATE_REFERENCE_HEIGHT * CAPTURE_SCALE_MAX) + 1
        self.assertEqual(capture_normalization_scale(too_small), 1.0)
        self.assertEqual(capture_normalization_scale(too_large), 1.0)

    def test_invalid_height_is_safe(self) -> None:
        for value in (0, -10, None, "abc"):
            with self.subTest(value=value):
                self.assertEqual(capture_normalization_scale(value), 1.0)

    def test_click_coordinate_round_trip(self) -> None:
        """정규화 좌표를 되돌리면 원래 프레임 좌표에 1px 이내로 복원됩니다."""

        real_width, real_height = 2568, 1408
        scale = capture_normalization_scale(real_height)
        self.assertNotEqual(scale, 1.0)

        for real_x, real_y in (
            (0, 0),
            (100, 200),
            (1284, 704),
            (real_width - 1, real_height - 1),
        ):
            with self.subTest(point=(real_x, real_y)):
                # 매칭은 정규화 프레임에서 이 위치를 찾습니다.
                norm_x = round(real_x / scale)
                norm_y = round(real_y / scale)
                back_x, back_y = _frame_to_capture(norm_x, norm_y, scale)
                self.assertLessEqual(abs(back_x - real_x), 1)
                self.assertLessEqual(abs(back_y - real_y), 1)

    def test_round_trip_is_identity_at_reference(self) -> None:
        """기준 해상도에서는 좌표가 전혀 변하지 않습니다(기존 사용자 회귀 방지)."""

        for point in ((0, 0), (964, 524), (1927, 1047)):
            with self.subTest(point=point):
                self.assertEqual(_frame_to_capture(point[0], point[1], 1.0), point)

    def test_fraction_regions_survive_normalization(self) -> None:
        """등수 OCR 박스 같은 비율 기반 영역은 정규화 후에도 같은 곳을 가리킵니다."""

        left, top, right, bottom = 0.33, 0.47, 0.50, 0.64
        real_width, real_height = 2568, 1408
        scale = capture_normalization_scale(real_height)
        norm_width, norm_height = _normalized_size(real_width, real_height)

        real_box = (
            round(real_width * left),
            round(real_height * top),
            round(real_width * right),
            round(real_height * bottom),
        )
        norm_box = (
            round(norm_width * left),
            round(norm_height * top),
            round(norm_width * right),
            round(norm_height * bottom),
        )
        # 정규화 박스를 원본 배율로 되돌리면 같은 영역이어야 합니다.
        restored = tuple(round(value * scale) for value in norm_box)
        for expected, actual in zip(real_box, restored):
            self.assertLessEqual(abs(expected - actual), 2)


if __name__ == "__main__":
    unittest.main()
