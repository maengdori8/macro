from __future__ import annotations

import base64
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


_REAL_SAME_ILLUMINATION_BASELINE = "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"
_REAL_SAME_ILLUMINATION_CANDIDATE = "iVBORw0KGgoAAAANSUhEUgAAAJUAAAAoCAAAAADSnec4AAAFZklEQVRYCc3Be2yVdx3H8fen5xyhrW1Cb8uGAypYtjHoLsQM3VxYBR1sAlZtNRoSWcSVJbP8gRsxkcUZFkC2EZiapWbU25aReRsDNZaMFnTEbTihcyDXYoBeWEd/ZyCHc77+nqcnuMoejiaPSV8vOd6fCBmeDATGlYmAEZAhPCOKCBgBGQgMRECOaMLwhIHAKEwYnjBhIIwrEIYnDAFGSMgRTRieMEAYhQnDkyEMhHEFwvBkeMIICTkiCcMThieMgoThCUMYCCOaMDxheMIICJMjkjA8YXjCKEgYnjCEgTCiCcMThieMgDA5IskIyAgIoxBheMIAETAiCcMTRkAYnjDkiCIMTxgBYRQiDE8YnjBhRBKGJ4yAMDxhyBFFGJ6MkDAKEYYnI08YkYThyQgJwxOGHFFkBGSEhFGIDE8YIAyEEUmGJ4yQMDwZyBFFhieMkDAKEIYnDE94RiRheMIICQOEgRwRhOHJGCaMAoThyQgIMKIJw5MxTBggDOS4ImH8j4Tx3xPGZeQYheQYheQYheQYheSI0VuHuOT2csj1v77v7VTtzLok3sWTrx5IF3/klgkJvIND/If6BHlyxOj5rVyyahK2f8tRvNI7GlNA52/6DFR5910C1nQzkjaVkCdHjJ7fyiWrJjG07rgRSH7lTji09jyhMS31wJpuRtKmEvLkiNHOVwjYQK8lv30t25+lqG7mQKdjSkvFxe8cI3HjtJN70tSuKIY3h/Byu9+gfG4NgVuS5MkRv/M/2WVTHyjLPXyamhVVud+9cKFs2XXHHs0w4/5i27YlV750GnmZx7sZu/RmRpIjfkfWD6WaGtT7TWPhQjj2VO/Eput3t2V5eCr84/H+RPMc8g6szaBZX2MkOWJnbV2Mb61i7xPQWg8XBio/AH/4WVYbyqB/41HmNxYROrP2pJKZxLL6BO8lR+yOrL7A5+6BXU/DIxPJ63wmq3WV0P9kD3Oaknh24rl91E7fmh3TfFsx7yFH7J7YS8m6EtixGVaX7jx41ccnAn/ZkGXpLDi+xtHQnAJye37Zly277/of/YniGY01/Jsccet5LM0nvwx0tMPKjleMRNPsFOkVaWpWlGd+3gUNzalz7uC2Hihd0JA40/5GDm6dNaEywTA5YpZ78VfZsQ9NAjraoe5QybtZir40O8FLW3KMn9z3VtFFGppTu3+ahuSkT98qGNr+516DylVlDJMjZoM/fJNpy0qAjnZIzZt5pn2AD3+9hgubdxtw5+EeGppTZ1c6Ku65qaL/CKWTk6f+uCPNogXkyRGzv69/V4vuFdDRDjNaZS//ODumpR7ObevMFM392KZDNDSnePnEzDpBV5vVtlSDHdg/byx5csRs+7N8cMnNeDs2wzdugsPf72PxbLzBwdKqtzceZk5TkryuNqttqWYkOWL22N+4urUGb9fT8N3xcGrjCT77GfL6NxxnXmOCvK42q22pZiQ54uUezDJ9ufD++j341hQ4sekkn5+fPXv0yKdK4dT6XjXOF3ldbVbbUs1IShOvjs3QfDeBweVZFi6C19qcltzR80iGJZ+AvRszxYtvO/sOw15/gfFfqCCUrBxDSGni9eRrsHIqAVvTTcX9demn9lv5susyD/VT8+CHBjYepmr5Nb/vYNi5QVLjEoTGfXECIaWJVaZ1CP1gLKHudVmS1X0XYfrSMl7cYiTLB3Nw+332i19zuaqWyYSUJlZHV5/n2kcZ9s/ndmYIVH71Rshs2J/FU+0DlbbnVS5XNvcqQkoTq65nMty1mLzBHb89B4kbFkwRcPqlziykPnrv1ZDL8T4SIqQ0/1eZfaeLp1wjhg3te6fshnEUpDSjkNKMQv8COJQRcmFP78QAAAAASUVORK5CYII="
_REAL_SAME_ILLUMINATION_SHIFTED_CANDIDATE = "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"


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

    def test_guard_accepts_only_structure_matched_smooth_illumination_drift(
        self,
    ) -> None:
        guard_detector = renewal.RenewalModalGuard(
            self.guard,
            self.price_box,
            shift_limit=4,
        )
        horizontal_drift = np.linspace(
            2.0,
            8.0,
            self.guard.shape[1],
            dtype=np.float32,
        )
        illuminated = np.clip(
            self.guard.astype(np.float32) + horizontal_drift[None, :],
            0,
            255,
        ).astype(np.uint8)
        shifted = renewal._translate_image(illuminated, -2, 1)
        registration = guard_detector.register(shifted, 0.0, 0.0)
        self.assertTrue(registration.valid)
        self.assertEqual(
            (registration.shift_x, registration.shift_y),
            (2, -1),
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

    def test_real_same_glyph_illumination_holdout_is_unchanged(self) -> None:
        def decode(encoded: str) -> np.ndarray:
            raw = np.frombuffer(base64.b64decode(encoded), dtype=np.uint8)
            image = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
            self.assertIsNotNone(image)
            return image

        baseline = decode(_REAL_SAME_ILLUMINATION_BASELINE)
        candidate = decode(_REAL_SAME_ILLUMINATION_CANDIDATE)
        classifier = renewal.RenewalPriceClassifier(
            baseline,
            unchanged_limit=0.040,
            stability_limit=0.030,
        )
        first = classifier.classify(candidate)
        second = classifier.classify(candidate.copy())
        self.assertIs(first.state, renewal.PriceState.UNCHANGED)
        self.assertEqual(
            first.reason,
            "illumination_normalized_same_glyph",
        )
        self.assertTrue(classifier.same_candidate(first, second))

        changed_image = baseline.copy()
        changed_image[:, 99:111] = np.fliplr(
            changed_image[:, 99:111]
        )
        changed = classifier.classify(changed_image)
        self.assertIsNot(
            changed.state,
            renewal.PriceState.UNCHANGED,
        )

    def test_real_same_glyph_raw_alignment_holdout_is_unchanged(self) -> None:
        def decode(encoded: str) -> np.ndarray:
            raw = np.frombuffer(base64.b64decode(encoded), dtype=np.uint8)
            image = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE)
            self.assertIsNotNone(image)
            return image

        baseline = decode(_REAL_SAME_ILLUMINATION_BASELINE)
        candidate = decode(_REAL_SAME_ILLUMINATION_SHIFTED_CANDIDATE)
        classifier = renewal.RenewalPriceClassifier(
            baseline,
            unchanged_limit=0.035,
            stability_limit=0.015,
        )
        first = classifier.classify(candidate)
        second = classifier.classify(candidate.copy())
        self.assertIs(first.state, renewal.PriceState.UNCHANGED)
        self.assertEqual(
            first.reason,
            "illumination_normalized_same_glyph",
        )
        self.assertTrue(classifier.same_candidate(first, second))

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

    def test_existing_open_popup_is_closed_before_any_action_click(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[[self.guard, self.guard]],
            stop_after_closes=1,
        )
        engine.mode = "open"
        engine.open_frames = [self.guard, self.guard]
        completed = _run(self.profile, engine, monitor_only=True)
        self.assertFalse(completed)
        self.assertEqual(engine.escapes, 1)
        self.assertEqual(engine.clicks, [])

    def test_monitor_stop_during_popup_confirms_closed_state(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[[self.guard, self.guard]],
        )
        original_packet = engine.get_latest_frame_packet

        def stop_after_first_open_frame(timeout: float = 0.0):
            packet = original_packet(timeout)
            if engine.mode == "open" and engine.open_index >= 1:
                stop_event.set()
            return packet

        engine.get_latest_frame_packet = stop_after_first_open_frame
        completed = _run(self.profile, engine, monitor_only=True)
        self.assertFalse(completed)
        self.assertEqual(engine.clicks, [(20, 10)])
        self.assertEqual(engine.escapes, 1)
        self.assertEqual(engine.mode, "closed")

    def test_timer_firing_on_escape_still_confirms_closed_state(self) -> None:
        stop_event = threading.Event()
        engine = _FakeEngine(
            stop_event,
            cycles=[[self.guard, self.guard]],
        )
        original_escape = engine.on_escape

        def close_then_stop(*args, **kwargs) -> None:
            original_escape(*args, **kwargs)
            stop_event.set()

        engine.on_escape = close_then_stop
        completed = _run(self.profile, engine, monitor_only=True)
        self.assertFalse(completed)
        self.assertEqual(engine.clicks, [(20, 10)])
        self.assertEqual(engine.escapes, 1)
        self.assertEqual(engine.mode, "closed")

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
