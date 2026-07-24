from __future__ import annotations

import os
import threading
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"

import cv2
import numpy as np

from macroapp import renewal
from macroapp.capture import CapturedFrame, WGCCaptureEngine


def _price(text: str, width: int, height: int) -> np.ndarray:
    image = np.full((height, width), 238, dtype=np.uint8)
    cv2.putText(
        image,
        text,
        (2, height - 5),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.30,
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
        self.sequence_id = 0

    def set_capture_region(self, region) -> None:
        self.capture_region = region

    def get_latest_frame(self, timeout: float = 0.0):
        packet = self.get_latest_frame_packet(timeout=timeout)
        return None if packet is None else packet.image

    def get_latest_frame_packet(self, timeout: float = 0.0):
        if timeout <= 0:
            return None
        if self.mode == "closed":
            frame = self.closed_guard.copy()
            if (
                self.stop_after_closes is not None
                and self.escapes >= self.stop_after_closes
            ):
                self.stop_event.set()
        elif self.open_index < len(self.open_frames):
            frame = self.open_frames[self.open_index]
            self.open_index += 1
        elif self.open_frames:
            frame = self.open_frames[-1]
        else:
            return None
        self.sequence_id += 1
        return CapturedFrame(frame.copy(), self.sequence_id, time.perf_counter())

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


class _DuplicateSequenceEngine(_FakeEngine):
    """Supplies images but never advances the WGC sequence number."""

    def __init__(self, stop_event: threading.Event, guard: np.ndarray):
        super().__init__(stop_event, repeat_guard=guard)
        self.packet_calls = 0

    def get_latest_frame_packet(self, timeout: float = 0.0):
        packet = super().get_latest_frame_packet(timeout=timeout)
        if packet is None:
            return None
        self.packet_calls += 1
        if self.packet_calls >= 12:
            self.stop_event.set()
        return CapturedFrame(packet.image, 1, packet.captured_at)


def _profile(baseline: np.ndarray, guard: np.ndarray) -> renewal.RenewalProfile:
    side = renewal.RenewalSideProfile(
        action_point=renewal.NormalizedPoint(20 / 199, 10 / 99),
        confirm_point=renewal.NormalizedPoint(180 / 199, 80 / 99),
        price_rect=renewal.NormalizedRect(0.40, 0.40, 0.60, 0.60),
        guard_rect=renewal.NormalizedRect(0.25, 0.20, 0.75, 0.80),
        limit_point=renewal.NormalizedPoint(140 / 199, 70 / 99),
        baseline_png=renewal.encode_gray_png(baseline),
        guard_png=renewal.encode_gray_png(guard),
        closed_guard_png=renewal.encode_gray_png(np.zeros_like(guard)),
        unchanged_limit=0.035,
        stability_limit=0.015,
        registration_shift_limit=4,
        calibration_openings=renewal.RENEWAL_CALIBRATION_OPENINGS,
        calibration_version=renewal.RENEWAL_CALIBRATION_VERSION,
        calibrated_frame_width=200,
        calibrated_frame_height=100,
    )
    profile = renewal.RenewalProfile(buy=side)
    profile.apply_speed_level(10)
    return profile


def _run(
    profile: renewal.RenewalProfile,
    engine: _FakeEngine,
    side: str = "buy",
    monitor_only: bool = False,
    diagnostic_sink=None,
) -> bool:
    manager = _FakeManager(engine)
    with ExitStack() as stack:
        # 회귀 테스트가 사용자의 실제 진단 보관함을 채우거나 오래된 자료를
        # 최근 50건 정리 정책으로 삭제하지 않도록 저장 I/O를 차단합니다.
        stack.enter_context(
            patch.object(
                renewal,
                "save_renewal_diagnostic",
                (
                    diagnostic_sink
                    if diagnostic_sink is not None
                    else lambda *_args, **_kwargs: None
                ),
            )
        )
        stack.enter_context(
            patch.object(
                renewal.input_message,
                "prepare_mouse_click",
                lambda hwnd, x, y: SimpleNamespace(
                    hwnd=hwnd,
                    x=x,
                    y=y,
                ),
            )
        )
        stack.enter_context(
            patch.object(
                renewal.input_message,
                "post_prepared_mouse_click",
                side_effect=lambda click: engine.on_click(
                    click.hwnd,
                    click.x,
                    click.y,
                ),
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
            monitor_only=monitor_only,
        ).run()


class RenewalSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base_price = _price("82400", 40, 20)
        self.changed_price = _price("82401", 40, 20)
        self.guard, self.price_box = _guard(self.base_price)
        self.changed_guard, _ = _guard(self.changed_price)
        self.profile = _profile(self.base_price, self.guard)

    def test_v8_profile_requires_v9_right_limit_price_recalibration(self) -> None:
        legacy = self.profile.to_dict()
        legacy["version"] = 8
        legacy["buy"]["calibration_version"] = 4
        legacy["buy"].pop("calibrated_frame_width")
        legacy["buy"].pop("calibrated_frame_height")
        loaded = renewal.RenewalProfile.from_dict(legacy)
        self.assertIn("v9 우측 상한가/하한가 재설정", loaded.missing("buy"))

    def test_v9_profile_round_trip_keeps_alignment_and_frame_size(self) -> None:
        self.profile.buy.noise_global = 0.004
        self.profile.buy.noise_slice = 0.018
        self.profile.buy.unchanged_limit = 0.038
        loaded = renewal.RenewalProfile.from_dict(self.profile.to_dict())
        self.assertEqual(loaded.to_dict()["version"], 9)
        self.assertTrue(loaded.buy.complete())
        self.assertAlmostEqual(loaded.buy.noise_global, 0.004)
        self.assertAlmostEqual(loaded.buy.unchanged_limit, 0.038)
        self.assertEqual(loaded.buy.calibrated_frame_size(), (200, 100))

    def test_v9_accepts_only_expected_right_limit_price_row(self) -> None:
        buy_rect = renewal.NormalizedRect(0.7128, 0.4042, 0.7904, 0.4424)
        sell_rect = renewal.NormalizedRect(0.7128, 0.3660, 0.7904, 0.4042)
        self.assertTrue(
            renewal.validate_limit_price_selection(
                self.base_price,
                buy_rect,
                "buy",
                1928,
                1048,
            ).valid
        )
        self.assertTrue(
            renewal.validate_limit_price_selection(
                self.base_price,
                sell_rect,
                "sell",
                1928,
                1048,
            ).valid
        )
        self.assertTrue(
            renewal.validate_limit_price_selection(
                self.base_price,
                buy_rect,
                "buy",
                1920,
                1040,
            ).valid
        )

        amount_input = renewal.NormalizedRect(0.610, 0.485, 0.765, 0.555)
        wrong_side_row = renewal.NormalizedRect(0.7128, 0.3660, 0.7904, 0.4042)
        self.assertFalse(
            renewal.validate_limit_price_selection(
                self.base_price,
                amount_input,
                "buy",
                1928,
                1048,
            ).valid
        )
        self.assertFalse(
            renewal.validate_limit_price_selection(
                self.base_price,
                wrong_side_row,
                "buy",
                1928,
                1048,
            ).valid
        )
        self.assertFalse(
            renewal.validate_limit_price_selection(
                self.base_price,
                buy_rect,
                "sell",
                1928,
                1048,
            ).valid
        )
        self.assertFalse(
            renewal.validate_limit_price_selection(
                self.base_price,
                buy_rect,
                "buy",
                2568,
                1408,
            ).valid
        )

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

    def test_wgc_single_slot_always_replaces_with_newest_frame(self) -> None:
        engine = WGCCaptureEngine(1, logger=lambda _message: None)
        first = np.zeros((4, 5, 4), dtype=np.uint8)
        second = np.full((4, 5, 4), 200, dtype=np.uint8)
        engine.on_frame_arrived(
            SimpleNamespace(frame_buffer=first, width=5, height=4)
        )
        engine.on_frame_arrived(
            SimpleNamespace(frame_buffer=second, width=5, height=4)
        )
        packet = engine.get_latest_frame_packet()
        self.assertIsNotNone(packet)
        self.assertEqual(packet.sequence_id, 2)
        self.assertEqual(int(packet.image[0, 0]), 200)
        self.assertEqual(engine.get_replaced_frame_count(), 1)

    def test_guard_and_price_alignment_accepts_same_and_real_change(self) -> None:
        guard_detector = renewal.RenewalModalGuard(
            self.guard,
            self.price_box,
            shift_limit=4,
        )
        classifier = renewal.RenewalPriceClassifier(
            self.base_price,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        for shift_x in range(-4, 5):
            with self.subTest(shift_x=shift_x, kind="same"):
                registration = guard_detector.register(
                    renewal._translate_image(self.guard, shift_x, 0),
                    0.0,
                    0.0,
                )
                self.assertTrue(registration.valid)
                self.assertEqual(registration.shift_x, -shift_x)
                current = renewal.crop_price_from_guard(
                    registration.aligned,
                    self.price_box,
                )
                self.assertIs(
                    classifier.classify(current).state,
                    renewal.PriceState.UNCHANGED,
                )
            with self.subTest(shift_x=shift_x, kind="changed"):
                registration = guard_detector.register(
                    renewal._translate_image(self.changed_guard, shift_x, 0),
                    0.0,
                    0.0,
                )
                self.assertTrue(registration.valid)
                self.assertEqual(registration.shift_x, -shift_x)
                current = renewal.crop_price_from_guard(
                    registration.aligned,
                    self.price_box,
                )
                self.assertIs(
                    classifier.classify(current).state,
                    renewal.PriceState.CHANGED,
                )

    def test_guard_registration_ignores_both_live_limit_price_rows(self) -> None:
        target = _price("769", 150, 40)
        sibling_before = _price("629", 150, 40)
        sibling_after = _price("630", 150, 40)
        price_box = (96, 40, 246, 80)
        baseline_guard = np.full((120, 341), 251, dtype=np.uint8)
        baseline_guard[0:40, 96:246] = sibling_before
        baseline_guard[40:80, 96:246] = target
        cv2.line(baseline_guard, (20, 100), (300, 100), 190, 1)
        changed_sibling_guard = baseline_guard.copy()
        changed_sibling_guard[0:40, 96:246] = sibling_after
        dynamic_boxes = renewal.dynamic_limit_price_boxes(
            price_box,
            "buy",
            1040,
        )
        guard_detector = renewal.RenewalModalGuard(
            baseline_guard,
            price_box,
            shift_limit=4,
            dynamic_boxes=dynamic_boxes,
        )
        for shift_x, shift_y in ((0, 0), (-4, -4), (4, 4)):
            with self.subTest(shift_x=shift_x, shift_y=shift_y):
                registration = guard_detector.register(
                    renewal._translate_image(
                        changed_sibling_guard,
                        shift_x,
                        shift_y,
                    ),
                    0.0,
                    0.0,
                )
                self.assertTrue(registration.valid)
                current = renewal.crop_price_from_guard(
                    registration.aligned,
                    price_box,
                )
                self.assertLess(
                    float(cv2.mean(cv2.absdiff(current, target))[0]),
                    0.01,
                )

    def test_guard_alignment_noise_never_hides_whole_popup_shift(self) -> None:
        for brightness in (0, 12):
            guard_detector = renewal.RenewalModalGuard(
                self.guard,
                self.price_box,
                shift_limit=4,
            )
            for shift_y in range(-4, 5):
                for shift_x in range(-4, 5):
                    with self.subTest(
                        brightness=brightness,
                        shift_x=shift_x,
                        shift_y=shift_y,
                    ):
                        shifted = renewal._translate_image(
                            self.guard,
                            shift_x,
                            shift_y,
                        )
                        shifted = np.clip(
                            shifted.astype(np.int16) + brightness,
                            0,
                            255,
                        ).astype(np.uint8)
                        registration = guard_detector.register(
                            shifted,
                            10.0,
                            0.001,
                        )
                        self.assertTrue(registration.valid)
                        self.assertEqual(
                            (
                                registration.shift_x,
                                registration.shift_y,
                            ),
                            (-shift_x, -shift_y),
                        )

    def test_incomplete_brightness_and_wrong_popup_never_become_changes(self) -> None:
        classifier = renewal.RenewalPriceClassifier(
            self.base_price,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        bright = np.clip(
            self.base_price.astype(np.int16) + 12,
            0,
            255,
        ).astype(np.uint8)
        clipped = renewal._translate_image(self.base_price, -7, 0)
        blank = np.full_like(self.base_price, 238)
        self.assertIs(
            classifier.classify(bright).state,
            renewal.PriceState.UNCHANGED,
        )
        self.assertIs(
            classifier.classify(clipped).state,
            renewal.PriceState.AMBIGUOUS,
        )
        self.assertIs(
            classifier.classify(blank).state,
            renewal.PriceState.AMBIGUOUS,
        )
        wrong_popup = np.full_like(self.guard, 50)
        guard_detector = renewal.RenewalModalGuard(
            self.guard,
            self.price_box,
            shift_limit=4,
        )
        self.assertFalse(
            guard_detector.register(wrong_popup, 0.0, 0.0).valid
        )

    def test_exact_baseline_and_repeated_candidate_use_safe_cache(self) -> None:
        classifier = renewal.RenewalPriceClassifier(
            self.base_price,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        with patch.object(
            classifier.detector,
            "prepare_pair_reusable",
            wraps=classifier.detector.prepare_pair_reusable,
        ) as prepare_pair:
            first_baseline = classifier.classify(self.base_price.copy())
            second_baseline = classifier.classify(self.base_price.copy())
            self.assertIs(first_baseline, second_baseline)
            self.assertEqual(prepare_pair.call_count, 0)

            first_changed = classifier.classify(self.changed_price.copy())
            calls_after_first = prepare_pair.call_count
            second_changed = classifier.classify(self.changed_price.copy())
            self.assertIs(first_changed, second_changed)
            self.assertGreater(calls_after_first, 0)
            self.assertEqual(prepare_pair.call_count, calls_after_first)

    def test_stable_partial_price_clipping_never_authorizes_change(self) -> None:
        classifier = renewal.RenewalPriceClassifier(
            self.base_price,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        background = int(np.median(self.base_price[:, 0]))
        for edge, clipping_widths in (
            ("left", (6, 10, 15)),
            # A 10 px cut of this tiny synthetic font is pixel-identical in
            # density and bounds to a legitimate final digit.  Keep only
            # structurally incomplete right cuts here; the embedded real FC
            # corpus separately covers its 2..20 px right-edge transitions.
            ("right", (15, 20)),
        ):
            for pixels in clipping_widths:
                with self.subTest(edge=edge, pixels=pixels):
                    clipped = self.base_price.copy()
                    if edge == "left":
                        clipped[:, :pixels] = background
                    else:
                        clipped[:, -pixels:] = background
                    first = classifier.classify(clipped)
                    second = classifier.classify(clipped.copy())
                    self.assertFalse(
                        first.state is renewal.PriceState.CHANGED
                        and classifier.same_candidate(first, second)
                    )
        for edge in ("top", "bottom"):
            for pixels in (12, 16, 20):
                with self.subTest(edge=edge, pixels=pixels):
                    clipped = self.base_price.copy()
                    if edge == "top":
                        clipped[:pixels, :] = background
                    else:
                        clipped[-pixels:, :] = background
                    first = classifier.classify(clipped)
                    second = classifier.classify(clipped.copy())
                    self.assertFalse(
                        first.state is renewal.PriceState.CHANGED
                        and classifier.same_candidate(first, second)
                    )
        for top, bottom in ((6, 10), (8, 12)):
            with self.subTest(gap="horizontal", top=top, bottom=bottom):
                occluded = self.base_price.copy()
                occluded[top:bottom, :] = background
                first = classifier.classify(occluded)
                second = classifier.classify(occluded.copy())
                self.assertFalse(
                    first.state is renewal.PriceState.CHANGED
                    and classifier.same_candidate(first, second)
                )
        for left, right in ((8, 13), (14, 19), (20, 25)):
            with self.subTest(gap="vertical", left=left, right=right):
                occluded = self.base_price.copy()
                occluded[:, left:right] = background
                first = classifier.classify(occluded)
                second = classifier.classify(occluded.copy())
                self.assertFalse(
                    first.state is renewal.PriceState.CHANGED
                    and classifier.same_candidate(first, second)
                )

    def test_five_independent_openings_build_v9_calibration(self) -> None:
        sessions = [
            [
                renewal._translate_image(self.guard, shift_x, 0)
                for _ in range(renewal.RENEWAL_CALIBRATION_FRAMES_PER_OPENING)
            ]
            for shift_x in (0, 1, -1, 2, -2)
        ]
        closed_samples = [
            np.zeros_like(self.guard)
            for _ in range(4)
        ]
        result = renewal.build_calibration_result(
            sessions,
            self.price_box,
            closed_samples,
        )
        self.assertEqual(
            result.calibration_openings,
            renewal.RENEWAL_CALIBRATION_OPENINGS,
        )
        self.assertEqual(result.registration_shift_limit, 4)
        self.assertEqual(result.closed_guard.shape, self.guard.shape)
        self.assertGreaterEqual(result.unchanged_limit, 0.035)
        self.assertLessEqual(result.unchanged_limit, 0.040)

    def test_v9_calibration_still_rejects_a_real_price_change(self) -> None:
        sessions = [
            [self.guard.copy() for _ in range(4)]
            for _ in range(renewal.RENEWAL_CALIBRATION_OPENINGS)
        ]
        sessions[-1] = [self.changed_guard.copy() for _ in range(4)]
        closed_samples = [
            np.zeros_like(self.guard)
            for _ in range(4)
        ]
        with self.assertRaisesRegex(
            ValueError,
            "숫자 구조가 달라졌습니다|기준과 일치하지 않습니다",
        ):
            renewal.build_calibration_result(
                sessions,
                self.price_box,
                closed_samples,
            )

    def test_v9_calibration_rejects_indistinguishable_closed_screen(self) -> None:
        sessions = [
            [self.guard.copy() for _ in range(4)]
            for _ in range(renewal.RENEWAL_CALIBRATION_OPENINGS)
        ]
        with self.assertRaisesRegex(ValueError, "구분되지 않습니다"):
            renewal.build_calibration_result(
                sessions,
                self.price_box,
                [self.guard.copy() for _ in range(4)],
            )

    def test_duplicate_wgc_sequence_ids_never_order(self) -> None:
        stop_event = threading.Event()
        engine = _DuplicateSequenceEngine(stop_event, self.guard)
        completed = _run(self.profile, engine)
        self.assertFalse(completed)
        self.assertEqual(
            [point for point in engine.clicks if point != (20, 10)],
            [],
        )

    def test_recorded_false_orders_replay_as_unchanged(self) -> None:
        diagnostic_root = (
            Path.home()
            / "AppData"
            / "Local"
            / "mAuto"
            / "renewal_diagnostics"
        )
        event_names = (
            "20260724_045646_675835700_buy_initial_mismatch",
            "20260724_045707_476789900_buy_initial_mismatch",
            "20260724_045715_328935100_buy_unstable_candidate",
            "20260724_045715_879773500_buy_initial_mismatch",
            "20260724_045722_348406900_buy_initial_mismatch",
            "20260724_045730_899395200_buy_unstable_candidate",
            "20260724_045731_949375200_buy_initial_mismatch",
            "20260724_045737_023254800_buy_unstable_candidate",
        )
        event_dirs = [
            diagnostic_root / name
            for name in event_names
            if (diagnostic_root / name).is_dir()
        ]
        if not event_dirs:
            self.skipTest("local recorded false-order fixtures are unavailable")
        replayed = 0
        for event_dir in event_dirs:
            baseline = cv2.imread(
                str(event_dir / "baseline.png"),
                cv2.IMREAD_GRAYSCALE,
            )
            self.assertIsNotNone(baseline)
            classifier = renewal.RenewalPriceClassifier(
                baseline,
                unchanged_limit=0.035,
                stability_limit=0.015,
            )
            for candidate_path in sorted(event_dir.glob("candidate_*.png")):
                candidate = cv2.imread(
                    str(candidate_path),
                    cv2.IMREAD_GRAYSCALE,
                )
                result = classifier.classify(candidate)
                self.assertIs(
                    result.state,
                    renewal.PriceState.UNCHANGED,
                    str(candidate_path),
                )
                replayed += 1
        self.assertGreaterEqual(replayed, 1)

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
                [self.guard, self.guard],
                [
                    wrong_popup,
                    self.changed_guard,
                    self.guard,
                    self.guard,
                ],
            ],
            stop_after_closes=3,
        )
        completed = _run(self.profile, engine)
        self.assertFalse(completed)
        self.assertEqual(
            [point for point in engine.clicks if point != (20, 10)],
            [],
        )

    def test_faded_price_render_is_never_a_confirmed_change(self) -> None:
        baseline = renewal.decode_gray_png(self.profile.buy.baseline_png)
        self.assertGreater(baseline.size, 0)
        background = np.full_like(baseline, int(np.median(baseline[0])))
        classifier = renewal.RenewalPriceClassifier(
            baseline,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        for alpha in (0.20, 0.35, 0.50, 0.65):
            faded = cv2.addWeighted(
                baseline,
                alpha,
                background,
                1.0 - alpha,
                0.0,
            )
            result = classifier.classify(faded)
            self.assertIsNot(
                result.state,
                renewal.PriceState.CHANGED,
                f"partial opacity {alpha:.2f} became an order signal",
            )

    def test_frame_size_mismatch_stops_before_first_click(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(stop_event, cycles=[[self.guard, self.guard]])
        self.profile.buy.calibrated_frame_width = 1928
        self.profile.buy.calibrated_frame_height = 1048
        with self.assertRaisesRegex(RuntimeError, "게임 창 크기"):
            _run(self.profile, engine)
        self.assertEqual(engine.clicks, [])

    def test_next_open_is_blocked_until_exact_ready_guard_returns(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[[self.guard, self.guard]],
            stop_after_closes=1,
        )
        engine.closed_guard = np.full_like(self.guard, 77)
        # Initial state must be ready; replace it with a wrong closed screen only
        # after the first open click.
        original_click = engine.on_click

        def click_then_break_ready(hwnd: int, x: int, y: int) -> None:
            original_click(hwnd, x, y)
            if (x, y) == (20, 10):
                engine.closed_guard = np.full_like(self.guard, 77)

        initial_ready = np.zeros_like(self.guard)
        engine.closed_guard = initial_ready
        engine.on_click = click_then_break_ready
        completed = _run(self.profile, engine)
        self.assertFalse(completed)
        self.assertEqual(engine.clicks, [(20, 10)])

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
        ordered_metrics: list[dict[str, object]] = []

        def collect_diagnostic(
            _side,
            reason,
            _baseline,
            _candidates,
            metadata,
            **_kwargs,
        ) -> None:
            if reason == "ordered":
                ordered_metrics.append(metadata)

        engine = _FakeEngine(
            stop_event,
            cycles=[
                [self.guard, self.guard],
                [self.guard, self.guard],
                [self.changed_guard, self.changed_guard],
            ],
        )
        completed = _run(
            self.profile,
            engine,
            diagnostic_sink=collect_diagnostic,
        )
        self.assertTrue(completed)
        self.assertEqual(
            engine.clicks,
            [
                (20, 10),
                (20, 10),
                (20, 10),
                (140, 70),
                (180, 80),
            ],
        )
        self.assertEqual(engine.escapes, 2)
        self.assertEqual(len(ordered_metrics), 1)
        self.assertLess(
            float(ordered_metrics[0]["second_frame_to_input_ms"]),
            4.0,
        )
        self.assertLess(
            float(ordered_metrics[0]["first_frame_to_input_ms"]),
            20.0,
        )

    def test_monitor_only_detects_change_without_any_order_click(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[
                [self.guard, self.guard],
                [self.guard, self.guard],
                [self.changed_guard, self.changed_guard],
            ],
        )
        with TemporaryDirectory() as temp_dir:
            with patch.dict("os.environ", {"LOCALAPPDATA": temp_dir}):
                completed = _run(
                    self.profile,
                    engine,
                    monitor_only=True,
                )
        self.assertFalse(completed)
        self.assertEqual(
            engine.clicks,
            [(20, 10), (20, 10), (20, 10)],
        )

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
                (30, 12),
                (130, 72),
                (170, 82),
            ],
        )


if __name__ == "__main__":
    unittest.main()
