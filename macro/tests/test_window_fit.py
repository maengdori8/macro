"""창 맞춤(window_fit)의 순수 로직 검증.

Win32 호출은 전부 주입 가능한 함수로 분리돼 있어 Windows 없이도 수렴/롤백을 검증합니다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from macroapp.config import TEMPLATE_REFERENCE_SIZE  # noqa: E402
from macroapp.window_fit import (  # noqa: E402
    WindowRect,
    WindowResizeSnapshot,
    fit_window_to_reference,
    target_outer_size_for_wgc,
)

REF = TEMPLATE_REFERENCE_SIZE  # (1928, 1048)


class TargetOuterSizeTests(unittest.TestCase):
    def test_border_difference_is_preserved(self) -> None:
        """외곽창이 WGC보다 16x39 큰 창은 목표에도 같은 차이를 더합니다."""

        outer = WindowRect(0, 0, 2576, 1416)      # 2576x1416
        size = target_outer_size_for_wgc(outer, (2560, 1377), REF)
        self.assertEqual(size, (REF[0] + 16, REF[1] + 39))

    def test_zero_difference_maps_directly(self) -> None:
        outer = WindowRect(-8, -8, 2568, 1408)    # 2576x1416
        self.assertEqual(target_outer_size_for_wgc(outer, (2576, 1416), REF), REF)

    def test_rejects_absurd_border_difference(self) -> None:
        """차이가 64px을 넘으면 다른 창을 잡은 것으로 보고 거부합니다."""

        outer = WindowRect(0, 0, 2800, 1500)
        with self.assertRaises(ValueError):
            target_outer_size_for_wgc(outer, (2560, 1377), REF)

    def test_rejects_tiny_frame(self) -> None:
        outer = WindowRect(0, 0, 320, 240)
        with self.assertRaises(ValueError):
            target_outer_size_for_wgc(outer, (320, 240), REF)


class _FakeWindow:
    """크기를 설정하면 그 크기 - 테두리만큼 WGC 프레임이 나오는 가짜 창."""

    def __init__(self, outer: tuple[int, int], border: tuple[int, int] = (16, 39),
                 *, apply_error: tuple[int, int] = (0, 0)):
        self.outer = outer
        self.border = border
        self.apply_error = apply_error
        self.resize_calls: list[tuple[int, int]] = []
        self.restored = False

    def read_rect(self, _hwnd: int) -> WindowRect:
        return WindowRect(0, 0, self.outer[0], self.outer[1])

    def resize(self, hwnd: int, size: tuple[int, int]) -> WindowResizeSnapshot:
        self.resize_calls.append(size)
        before = WindowRect(0, 0, self.outer[0], self.outer[1])
        # 게임이 요청 크기를 그대로 적용하지 않는 상황도 흉내 냅니다.
        self.outer = (size[0] + self.apply_error[0], size[1] + self.apply_error[1])
        return WindowResizeSnapshot(
            hwnd, before, WindowRect(0, 0, self.outer[0], self.outer[1])
        )

    def measure(self):
        return (self.outer[0] - self.border[0], self.outer[1] - self.border[1])


class FitWindowTests(unittest.TestCase):
    def test_already_reference_does_not_touch_window(self) -> None:
        """이미 기준 크기면 창을 건드리지 않습니다(불필요한 리사이즈 방지)."""

        window = _FakeWindow((REF[0] + 16, REF[1] + 39))
        result = fit_window_to_reference(
            1, window.measure, resize=window.resize, read_rect=window.read_rect
        )
        self.assertTrue(result.ok)
        self.assertFalse(result.changed)
        self.assertEqual(window.resize_calls, [])
        self.assertIsNone(result.snapshot)

    def test_fits_2560_window_in_one_pass(self) -> None:
        """2560 창은 한 번에 기준 크기로 수렴합니다."""

        window = _FakeWindow((2576, 1416))
        result = fit_window_to_reference(
            1, window.measure, resize=window.resize, read_rect=window.read_rect
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.changed)
        self.assertEqual(result.before, (2560, 1377))
        self.assertEqual(result.after, REF)
        self.assertEqual(len(window.resize_calls), 1)
        self.assertIsNotNone(result.snapshot)

    def test_converges_when_game_applies_size_imprecisely(self) -> None:
        """게임이 크기를 살짝 다르게 적용해도 재보정으로 수렴합니다."""

        window = _FakeWindow((2576, 1416), apply_error=(4, 6))
        # 첫 적용은 +4,+6 만큼 어긋나고, 재보정 시 그 차이를 흡수하도록 오차를 없앱니다.
        original_resize = window.resize

        def resize_once_wrong(hwnd, size):
            snapshot = original_resize(hwnd, size)
            window.apply_error = (0, 0)
            return snapshot

        result = fit_window_to_reference(
            1, window.measure, resize=resize_once_wrong, read_rect=window.read_rect
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.after, REF)
        self.assertEqual(len(window.resize_calls), 2)

    def test_rolls_back_when_it_never_converges(self) -> None:
        """끝내 수렴 못 하면 실패로 보고하고 창을 되돌립니다."""

        window = _FakeWindow((2576, 1416), apply_error=(40, 40))
        restored: list[WindowResizeSnapshot] = []

        import macroapp.window_fit as window_fit

        original_restore = window_fit.restore_window_no_activate
        window_fit.restore_window_no_activate = lambda snapshot: restored.append(snapshot)
        try:
            result = fit_window_to_reference(
                1, window.measure, resize=window.resize, read_rect=window.read_rect
            )
        finally:
            window_fit.restore_window_no_activate = original_restore

        self.assertFalse(result.ok)
        self.assertFalse(result.changed)
        self.assertEqual(len(restored), 1, "실패하면 반드시 되돌려야 합니다")

    def test_missing_frame_is_reported_not_raised(self) -> None:
        """WGC 프레임을 못 받으면 예외 대신 실패 결과를 돌려줍니다."""

        result = fit_window_to_reference(1, lambda: None)
        self.assertFalse(result.ok)
        self.assertIn("WGC", result.message)


if __name__ == "__main__":
    unittest.main()
