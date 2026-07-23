from __future__ import annotations

import threading
import unittest
from contextlib import ExitStack
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cv2
import numpy as np

from macroapp import renewal


def _price(text: str, width: int, height: int) -> np.ndarray:
    image = np.full((height, width), 238, dtype=np.uint8)
    cv2.putText(
        image,
        text,
        (2, height - 5),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        30,
        1,
        cv2.LINE_AA,
    )
    return image


def _guard(price_image: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    guard = np.full((60, 100), 224, dtype=np.uint8)
    cv2.rectangle(guard, (1, 1), (98, 58), 90, 1)
    cv2.putText(
        guard,
        "BP",
        (4, 16),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.35,
        70,
        1,
        cv2.LINE_AA,
    )
    box = (30, 20, 70, 40)
    guard[box[1] : box[3], box[0] : box[2]] = price_image
    return guard, box


class _FakeEngine:
    def __init__(
        self,
        stop_event: threading.Event,
        cycles: list[list[np.ndarray]] | None = None,
        repeat_guard: np.ndarray | None = None,
        stop_after_closes: int | None = None,
    ):
        self.stop_event = stop_event
        self.cycles = cycles or []
        self.repeat_guard = repeat_guard
        self.stop_after_closes = stop_after_closes
        self.closed_event = threading.Event()
        self.capture_region = None
        self.clicks: list[tuple[int, int]] = []
        self.escapes = 0
        self.cycle_index = -1
        self.open_frames: list[np.ndarray] = []
        self.open_index = 0
        self.mode = "closed"
        self.closed_guard = np.zeros((60, 100), dtype=np.uint8)

    def set_capture_region(self, region) -> None:
        self.capture_region = region

    def get_latest_frame(self, timeout: float = 0.0):
        if timeout <= 0:
            return None
        if self.mode == "closed":
            frame = self.closed_guard.copy()
            if (
                self.stop_after_closes is not None
                and self.escapes >= self.stop_after_closes
            ):
                self.stop_event.set()
            return frame
        if self.open_index < len(self.open_frames):
            frame = self.open_frames[self.open_index]
            self.open_index += 1
            return frame.copy()
        if self.open_frames:
            return self.open_frames[-1].copy()
        return None

    def on_click(self, _hwnd: int, x: int, y: int) -> None:
        self.clicks.append((x, y))
        if (x, y) == (20, 10):
            self.cycle_index += 1
            self.mode = "open"
            self.open_index = 0
            if self.repeat_guard is not None:
                self.open_frames = [
                    self.repeat_guard,
                    self.repeat_guard,
                ]
            else:
                index = min(self.cycle_index, len(self.cycles) - 1)
                self.open_frames = self.cycles[index]

    def on_escape(self, *_args, **_kwargs) -> None:
        self.escapes += 1
        self.mode = "closed"


class _FakeManager:
    def __init__(self, engine: _FakeEngine):
        self.engine = engine
        self.capture_engine = engine
        self.hwnd = 1

    def find_window(self) -> bool:
        return True

    def capture_client_area(self, *, window_validated: bool = False):
        return np.full((100, 200), 128, dtype=np.uint8)

    def wgc_to_client(self, x: int, y: int):
        return x, y


def _profile(baseline: np.ndarray, guard: np.ndarray) -> renewal.RenewalProfile:
    side = renewal.RenewalSideProfile(
        action_point=renewal.NormalizedPoint(20 / 199, 10 / 99),
        confirm_point=renewal.NormalizedPoint(180 / 199, 80 / 99),
        price_rect=renewal.NormalizedRect(0.40, 0.40, 0.60, 0.60),
        guard_rect=renewal.NormalizedRect(0.25, 0.20, 0.75, 0.80),
        limit_point=renewal.NormalizedPoint(140 / 199, 70 / 99),
        baseline_png=renewal.encode_gray_png(baseline),
        guard_png=renewal.encode_gray_png(guard),
        calibration_version=renewal.RENEWAL_CALIBRATION_VERSION,
    )
    profile = renewal.RenewalProfile(buy=side)
    profile.apply_speed_level(10)
    return profile


def _run(
    profile: renewal.RenewalProfile,
    engine: _FakeEngine,
    side: str = "buy",
) -> bool:
    manager = _FakeManager(engine)
    with ExitStack() as stack:
        stack.enter_context(
            patch.object(renewal.input_message, "post_mouse_move", lambda *_: None)
        )
        stack.enter_context(
            patch.object(renewal.input_message, "post_mouse_down", lambda *_: None)
        )
        stack.enter_context(
            patch.object(
                renewal.input_message,
                "post_mouse_up",
                side_effect=engine.on_click,
            )
        )
        stack.enter_context(
            patch.object(
                renewal.input_message,
                "send_key_to_window",
                side_effect=engine.on_escape,
            )
        )
        return renewal.FastRenewalRunner(
            manager=manager,
            profile=profile,
            side=side,
            stop_event=engine.stop_event,
            logger=lambda _message: None,
            status=lambda _message: None,
        ).run()


class RenewalSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base_price = _price("82400", 40, 20)
        self.changed_price = _price("82401", 40, 20)
        self.guard, self.price_box = _guard(self.base_price)
        self.changed_guard, _ = _guard(self.changed_price)
        self.profile = _profile(self.base_price, self.guard)

    def test_v4_profile_requires_safe_price_recalibration(self) -> None:
        legacy = self.profile.to_dict()
        legacy["version"] = 4
        legacy["buy"].pop("guard_rect")
        legacy["buy"].pop("guard_png")
        legacy["buy"].pop("calibration_version")
        loaded = renewal.RenewalProfile.from_dict(legacy)
        self.assertIn("안전 가격영역 재설정", loaded.missing("buy"))

    def test_v5_profile_round_trip_keeps_guard_and_noise(self) -> None:
        self.profile.buy.noise_global = 0.004
        self.profile.buy.noise_slice = 0.018
        loaded = renewal.RenewalProfile.from_dict(self.profile.to_dict())
        self.assertEqual(loaded.to_dict()["version"], 5)
        self.assertTrue(loaded.buy.complete())
        self.assertAlmostEqual(loaded.buy.noise_global, 0.004)

    def test_two_line_price_region_is_rejected(self) -> None:
        two_lines = np.full((70, 120), 238, dtype=np.uint8)
        cv2.putText(
            two_lines,
            "0",
            (8, 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            20,
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            two_lines,
            "82400",
            (8, 58),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            20,
            2,
            cv2.LINE_AA,
        )
        self.assertFalse(renewal.validate_price_region(two_lines).valid)
        self.assertTrue(renewal.validate_price_region(self.base_price).valid)

    def test_initial_mismatch_never_orders(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[[self.changed_guard, self.changed_guard]],
        )
        with TemporaryDirectory() as temp_dir:
            with patch.dict("os.environ", {"LOCALAPPDATA": temp_dir}):
                completed = _run(self.profile, engine)
        self.assertFalse(completed)
        self.assertEqual(engine.clicks, [(20, 10)])

    def test_transition_and_single_frame_glitch_never_order(self) -> None:
        stop_event = threading.Event()
        wrong_popup = np.full((60, 100), 50, dtype=np.uint8)
        engine = _FakeEngine(
            stop_event,
            cycles=[
                [self.guard, self.guard],
                [
                    wrong_popup,
                    self.changed_guard,
                    self.guard,
                    self.guard,
                ],
            ],
            stop_after_closes=2,
        )
        completed = _run(self.profile, engine)
        self.assertFalse(completed)
        self.assertEqual(
            [point for point in engine.clicks if point != (20, 10)],
            [],
        )

    def test_unchanged_10000_cycles_has_zero_orders(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            repeat_guard=self.guard,
            stop_after_closes=10_000,
        )
        completed = _run(self.profile, engine)
        self.assertFalse(completed)
        self.assertEqual(engine.escapes, 10_000)
        self.assertEqual(
            [point for point in engine.clicks if point != (20, 10)],
            [],
        )

    def test_stable_change_orders_once_in_correct_sequence_and_stops(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[
                [self.guard, self.guard],
                [self.changed_guard, self.changed_guard],
            ],
        )
        completed = _run(self.profile, engine)
        self.assertTrue(completed)
        self.assertEqual(
            engine.clicks,
            [
                (20, 10),
                (20, 10),
                (140, 70),
                (180, 80),
            ],
        )
        self.assertEqual(engine.escapes, 1)

    def test_sell_uses_its_own_open_limit_and_final_coordinates(self) -> None:
        sell = renewal.RenewalSideProfile.from_dict(self.profile.buy.to_dict())
        sell.action_point = renewal.NormalizedPoint(30 / 199, 12 / 99)
        sell.limit_point = renewal.NormalizedPoint(130 / 199, 72 / 99)
        sell.confirm_point = renewal.NormalizedPoint(170 / 199, 82 / 99)
        self.profile.sell = sell
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[
                [self.guard, self.guard],
                [self.changed_guard, self.changed_guard],
            ],
        )

        original_on_click = engine.on_click

        def sell_click(hwnd: int, x: int, y: int) -> None:
            if (x, y) == (30, 12):
                engine.clicks.append((x, y))
                engine.cycle_index += 1
                engine.mode = "open"
                engine.open_index = 0
                index = min(engine.cycle_index, len(engine.cycles) - 1)
                engine.open_frames = engine.cycles[index]
                return
            original_on_click(hwnd, x, y)

        engine.on_click = sell_click
        completed = _run(self.profile, engine, side="sell")
        self.assertTrue(completed)
        self.assertEqual(
            engine.clicks,
            [
                (30, 12),
                (30, 12),
                (130, 72),
                (170, 82),
            ],
        )


if __name__ == "__main__":
    unittest.main()
