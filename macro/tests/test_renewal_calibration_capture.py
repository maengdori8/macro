from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

import renewal_macro
from macroapp.renewal import (
    NormalizedPoint,
    NormalizedRect,
    RenewalSideProfile,
    encode_gray_png,
)


class _Value:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Root:
    @staticmethod
    def update_idletasks() -> None:
        return None


class _Engine:
    def __init__(self, frames: list[np.ndarray]):
        self._packets = [
            SimpleNamespace(
                image=frame,
                sequence_id=index + 1,
                captured_at=float(index + 1),
            )
            for index, frame in enumerate(frames)
        ]
        self.closed_event = threading.Event()
        self.region = None

    def set_capture_region(self, region) -> None:
        self.region = region

    def get_latest_frame_packet(self, timeout=0.0):
        if not self._packets:
            return None
        return self._packets.pop(0)


class _Manager:
    def __init__(self, frames: list[np.ndarray], full_frame: np.ndarray):
        self.capture_engine = _Engine(frames)
        self.full_frame = full_frame
        self.hwnd = 100
        self.stopped = False

    @staticmethod
    def find_window() -> bool:
        return True

    def capture_client_area(self, *, window_validated=False):
        return self.full_frame.copy()

    def stop_capture(self) -> None:
        self.stopped = True


class _Guard:
    def __init__(
        self,
        baseline,
        price_box,
        shift_limit=4,
        dynamic_boxes=None,
    ):
        self.shape = baseline.shape

    def register(self, image, luma_noise, edge_noise):
        valid = image.shape == self.shape and int(image[0, 0]) == 1
        return SimpleNamespace(
            valid=valid,
            aligned=image,
            shift_x=0,
            shift_y=0,
            luma_delta=0.0 if valid else 255.0,
            edge_delta=0.0 if valid else 1.0,
        )


class _Clicker:
    instance = None

    def __init__(self, manager, width, height):
        type(self).instance = self
        self.escape_count = 0
        self.click_count = 0

    @staticmethod
    def resolve(point):
        return 10, 10

    def press_escape(self) -> None:
        self.escape_count += 1

    def click_client(self, point) -> None:
        self.click_count += 1


class _Detector:
    @staticmethod
    def prepare_pair(image):
        return image.copy(), image.copy()

    @staticmethod
    def pair_stability(first, second):
        return 0.0 if np.array_equal(first[0], second[0]) else 1.0


class RenewalCalibrationCaptureTests(unittest.TestCase):
    def test_headless_price_rect_expands_left_for_long_right_aligned_price(self):
        original = NormalizedRect(
            1374 / 1928,
            423 / 1048,
            1524 / 1928,
            463 / 1048,
        )

        expanded = renewal_macro._expand_headless_price_rect(
            original,
            "buy",
            1928,
            1048,
        )

        self.assertEqual(
            expanded.to_pixels(1928, 1048),
            (1313, 431, 1533, 479),
        )

    def test_headless_price_rect_keeps_already_wide_horizontal_selection(self):
        original = NormalizedRect(
            1300 / 1928,
            423 / 1048,
            1530 / 1928,
            463 / 1048,
        )

        expanded = renewal_macro._expand_headless_price_rect(
            original,
            "buy",
            1928,
            1048,
        )

        self.assertEqual(
            expanded.to_pixels(1928, 1048),
            (1303, 431, 1533, 479),
        )

    def test_headless_price_rect_is_idempotent_after_expansion(self):
        original = NormalizedRect(
            1313 / 1928,
            431 / 1048,
            1533 / 1928,
            479 / 1048,
        )

        expanded = renewal_macro._expand_headless_price_rect(
            original,
            "buy",
            1928,
            1048,
        )

        self.assertEqual(
            expanded.to_pixels(1928, 1048),
            original.to_pixels(1928, 1048),
        )

    def test_headless_sell_price_rect_targets_lower_limit_row(self):
        original = NormalizedRect(
            1420 / 1928,
            391 / 1048,
            1511 / 1928,
            424 / 1048,
        )

        expanded = renewal_macro._expand_headless_price_rect(
            original,
            "sell",
            1928,
            1048,
        )

        self.assertEqual(
            expanded.to_pixels(1928, 1048),
            (1313, 431, 1533, 479),
        )

    def test_expanded_calibration_uses_valid_stored_numeric_row(self):
        stored = np.full((40, 150), 238, dtype=np.uint8)
        # Reuse the validator fixture font path through OpenCV exposed by the
        # runtime module; the stored row intentionally differs from the new
        # 48x220 expanded crop shape.
        renewal_macro.cv2.putText(
            stored,
            "777",
            (25, 30),
            renewal_macro.cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            30,
            2,
            renewal_macro.cv2.LINE_AA,
        )
        side = RenewalSideProfile(baseline_png=encode_gray_png(stored))
        closed_screen = np.full((48, 220), 90, dtype=np.uint8)

        selected = renewal_macro._calibration_selection_probe(
            side,
            closed_screen,
        )

        self.assertEqual(selected.shape, stored.shape)
        self.assertTrue(np.array_equal(selected, stored))

    def test_calibration_popup_opacity_rejects_stable_crossfade(self):
        faded = np.full((40, 80), 249, dtype=np.uint8)
        opaque = np.full((40, 80), 251, dtype=np.uint8)
        faded[:, :4] = 40
        opaque[:, :4] = 40

        faded_luma, faded_white_ratio, faded_valid = (
            renewal_macro._calibration_popup_opacity(faded)
        )
        opaque_luma, opaque_white_ratio, opaque_valid = (
            renewal_macro._calibration_popup_opacity(opaque)
        )

        self.assertGreaterEqual(
            faded_luma,
            renewal_macro.CALIBRATION_POPUP_LUMA_FLOOR,
        )
        self.assertLess(
            faded_white_ratio,
            renewal_macro.CALIBRATION_POPUP_WHITE_RATIO,
        )
        self.assertFalse(faded_valid)
        self.assertGreaterEqual(
            opaque_luma,
            renewal_macro.CALIBRATION_POPUP_LUMA_FLOOR,
        )
        self.assertGreaterEqual(
            opaque_white_ratio,
            renewal_macro.CALIBRATION_POPUP_WHITE_RATIO,
        )
        self.assertTrue(opaque_valid)

        # A real opaque buy guard contains a long price and the gray edge of
        # the amount input.  Its 72% white background is complete even though
        # the full-crop mean is only about 216.
        dense_opaque = np.full((100, 100), 251, dtype=np.uint8)
        dense_opaque[:, 72:] = 125
        dense_luma, dense_white_ratio, dense_valid = (
            renewal_macro._calibration_popup_opacity(dense_opaque)
        )
        self.assertGreaterEqual(
            dense_luma,
            renewal_macro.CALIBRATION_POPUP_LUMA_FLOOR,
        )
        self.assertGreaterEqual(
            dense_white_ratio,
            renewal_macro.CALIBRATION_POPUP_WHITE_RATIO,
        )
        self.assertTrue(dense_valid)

    def test_calibration_guard_stability_rejects_crossfade_frames(self):
        early = np.full((40, 80), 223, dtype=np.uint8)
        later = np.full((40, 80), 225, dtype=np.uint8)

        self.assertEqual(
            renewal_macro._calibration_guard_delta(early, early.copy()),
            0.0,
        )
        self.assertGreater(
            renewal_macro._calibration_guard_delta(early, later),
            renewal_macro.CALIBRATION_STABLE_FRAME_DELTA,
        )

    def test_closed_calibration_rejects_repeated_esc_transition(self):
        class PixelDetector:
            def __init__(self, expected: int):
                self.expected = expected

            def register(self, image, _luma_noise, _edge_noise):
                valid = int(image[0, 0]) == self.expected
                return SimpleNamespace(valid=valid, aligned=image.copy())

        open_detector = PixelDetector(251)
        closed_detector = PixelDetector(42)
        repeated_transition = np.full((40, 80), 233, dtype=np.uint8)
        closed = np.full((40, 80), 42, dtype=np.uint8)

        self.assertIsNone(
            renewal_macro._calibration_closed_guard_candidate(
                open_detector,
                closed_detector,
                repeated_transition,
            )
        )
        accepted = renewal_macro._calibration_closed_guard_candidate(
            open_detector,
            closed_detector,
            closed,
        )
        self.assertIsNotNone(accepted)
        self.assertTrue(np.array_equal(accepted, closed))

    def test_stable_wgc_wait_discards_transient_oversized_frame(self):
        transient = np.zeros((1056, 1936), dtype=np.uint8)
        stable = np.zeros((1048, 1928), dtype=np.uint8)
        manager = _Manager(
            [stable.copy(), stable.copy(), stable.copy()],
            transient,
        )
        with mock.patch.object(
            renewal_macro,
            "WGC_SIZE_STABLE_SECONDS",
            0.001,
        ):
            result = renewal_macro._wait_for_stable_wgc_frame(
                manager,
                timeout_seconds=0.1,
            )
        self.assertEqual(result.shape, stable.shape)

    def test_wgc_fit_promotes_verified_fallback_and_fits_unsupported_size(self):
        from macroapp.turbo_session import WindowRect

        current_outer = WindowRect(0, 0, 1928, 1048)
        promoted_outer = WindowRect(0, 0, 1936, 1056)
        promoted_snapshot = renewal_macro.WindowResizeSnapshot(
            100,
            current_outer,
            promoted_outer,
            True,
        )
        corrected_outer = WindowRect(0, 0, 1944, 1064)
        corrected_snapshot = renewal_macro.WindowResizeSnapshot(
            100,
            promoted_outer,
            corrected_outer,
        )
        fallback = np.zeros((1040, 1920), dtype=np.uint8)
        preferred = np.zeros((1048, 1928), dtype=np.uint8)
        with (
            mock.patch.object(
                renewal_macro,
                "get_window_rect",
                return_value=current_outer,
            ),
            mock.patch.object(
                renewal_macro,
                "_measure_stable_wgc_frame",
                side_effect=(fallback, fallback, preferred),
            ),
            mock.patch.object(
                renewal_macro,
                "resize_window_no_activate",
                side_effect=(promoted_snapshot, corrected_snapshot),
            ) as resize,
        ):
            result, before_size, after_size = (
                renewal_macro._fit_game_window_to_wgc(100)
            )
        self.assertEqual(result.original, current_outer)
        self.assertEqual(result.resized, corrected_outer)
        self.assertTrue(result.original_was_maximized)
        self.assertEqual(before_size, (1920, 1040))
        self.assertEqual(after_size, (1928, 1048))
        self.assertEqual(
            resize.call_args_list,
            [
                mock.call(100, (1936, 1056)),
                mock.call(100, (1944, 1064)),
            ],
        )

        unsupported_outer = WindowRect(0, 0, 1608, 908)
        resized_outer = WindowRect(0, 0, 1936, 1056)
        snapshot = renewal_macro.WindowResizeSnapshot(
            100,
            unsupported_outer,
            resized_outer,
        )
        before = np.zeros((900, 1600), dtype=np.uint8)
        after = np.zeros((1048, 1928), dtype=np.uint8)
        with (
            mock.patch.object(
                renewal_macro,
                "get_window_rect",
                return_value=unsupported_outer,
            ),
            mock.patch.object(
                renewal_macro,
                "_measure_stable_wgc_frame",
                side_effect=(before, after),
            ),
            mock.patch.object(
                renewal_macro,
                "resize_window_no_activate",
                return_value=snapshot,
            ) as resize,
            mock.patch.object(
                renewal_macro,
                "restore_window_no_activate",
            ) as restore,
        ):
            result, before_size, after_size = (
                renewal_macro._fit_game_window_to_wgc(100)
            )
        self.assertEqual(result, snapshot)
        self.assertEqual(before_size, (1600, 900))
        self.assertEqual(after_size, (1928, 1048))
        resize.assert_called_once_with(100, (1936, 1056))
        restore.assert_not_called()

        wrong_after = np.zeros((1044, 1924), dtype=np.uint8)
        with (
            mock.patch.object(
                renewal_macro,
                "get_window_rect",
                return_value=unsupported_outer,
            ),
            mock.patch.object(
                renewal_macro,
                "_measure_stable_wgc_frame",
                side_effect=(before, wrong_after, wrong_after, wrong_after),
            ),
            mock.patch.object(
                renewal_macro,
                "resize_window_no_activate",
                return_value=snapshot,
            ),
            mock.patch.object(
                renewal_macro,
                "restore_window_no_activate",
            ) as restore,
        ):
            with self.assertRaises(RuntimeError):
                renewal_macro._fit_game_window_to_wgc(100)
        restore.assert_called_once_with(snapshot)

        supported_fallback = np.zeros((1040, 1920), dtype=np.uint8)
        with (
            mock.patch.object(
                renewal_macro,
                "get_window_rect",
                return_value=current_outer,
            ),
            mock.patch.object(
                renewal_macro,
                "_measure_stable_wgc_frame",
                return_value=supported_fallback,
            ),
            mock.patch.object(
                renewal_macro,
                "resize_window_no_activate",
            ) as resize,
        ):
            result, before_size, after_size = (
                renewal_macro._fit_game_window_to_wgc(
                    100,
                    allow_supported_fallback=True,
                )
            )
        self.assertEqual(result.original, current_outer)
        self.assertEqual(result.resized, current_outer)
        self.assertEqual(before_size, (1920, 1040))
        self.assertEqual(after_size, (1920, 1040))
        resize.assert_not_called()

    def test_headless_monitor_supports_ten_hour_checkpointed_soak(self):
        self.assertGreaterEqual(
            renewal_macro.HEADLESS_MONITOR_MAX_SECONDS,
            10.0 * 60.0 * 60.0,
        )
        self.assertLessEqual(
            renewal_macro.HEADLESS_MONITOR_CHECKPOINT_SECONDS,
            60.0,
        )
        self.assertGreater(
            renewal_macro.HEADLESS_VIDEO_CPU_AVERAGE_LIMIT_PERCENT,
            renewal_macro.HEADLESS_MONITOR_CPU_AVERAGE_LIMIT_PERCENT,
        )
        self.assertLessEqual(
            renewal_macro.HEADLESS_VIDEO_CPU_AVERAGE_LIMIT_PERCENT,
            renewal_macro.HEADLESS_MONITOR_CPU_P95_LIMIT_PERCENT,
        )

    def test_headless_monitor_acceptance_tracks_stable_request_budget(self):
        self.assertEqual(
            renewal_macro._headless_minimum_confirmed_openings(
                60.0,
                10,
            ),
            26,
        )
        self.assertEqual(
            renewal_macro._headless_minimum_confirmed_openings(
                600.0,
                9,
            ),
            266,
        )
        self.assertEqual(
            renewal_macro._headless_minimum_confirmed_openings(
                36000.0,
                10,
            ),
            1000,
        )
        self.assertEqual(
            renewal_macro._headless_minimum_confirmed_openings(
                60.0,
                10,
                video_speed_mode=True,
            ),
            133,
        )
        self.assertEqual(
            renewal_macro._headless_minimum_confirmed_openings(
                1800.0,
                10,
                video_speed_mode=True,
            ),
            4000,
        )

    def test_headless_monitor_rejects_recorded_sustained_open_failures(
        self,
    ):
        for fixture_name in (
            "renewal_sustained_open_failure_870s.json",
            "renewal_sustained_open_failure_390s.json",
        ):
            with self.subTest(fixture=fixture_name):
                fixture_path = (
                    Path(__file__).parent
                    / "fixtures"
                    / fixture_name
                )
                fixture = json.loads(
                    fixture_path.read_text(encoding="utf-8")
                )
                summary = renewal_macro._headless_open_failure_summary(
                    {
                        "confirmed_openings": (
                            fixture["confirmed_openings"]
                        ),
                        "open_failures": fixture["open_failures"],
                    }
                )
                self.assertFalse(summary["within_limit"])
                self.assertGreater(
                    summary["open_failure_rate"],
                    summary["open_failure_rate_limit"],
                )

    def test_recorded_server_pressure_is_rejected_below_rate_limit(self):
        for fixture_name in (
            "renewal_sustained_open_failure_552s.json",
            "renewal_sustained_open_failure_388s.json",
        ):
            with self.subTest(fixture=fixture_name):
                fixture_path = (
                    Path(__file__).parent
                    / "fixtures"
                    / fixture_name
                )
                fixture = json.loads(
                    fixture_path.read_text(encoding="utf-8")
                )
                self.assertGreaterEqual(int(fixture["open_failures"]), 2)
                self.assertTrue(
                    bool(fixture["server_pressure_detected"])
                )
                self.assertEqual(int(fixture["order_inputs"]), 0)

    def test_headless_monitor_accepts_rare_recovered_open_failure(self):
        summary = renewal_macro._headless_open_failure_summary(
            {
                "confirmed_openings": 100_000,
                "open_failures": 5,
            }
        )
        self.assertTrue(summary["within_limit"])

    def test_headless_monitor_separates_safe_reselection_recovery(self):
        summary = renewal_macro._headless_open_failure_summary(
            {
                "confirmed_openings": 100,
                "open_failures": 50,
                "recovered_open_failures": 50,
            }
        )
        self.assertEqual(summary["recovered_open_failures"], 50)
        self.assertEqual(summary["unrecovered_open_failures"], 0)
        self.assertEqual(summary["open_failure_rate"], 0.0)
        self.assertGreater(summary["raw_open_failure_rate"], 0.3)
        self.assertTrue(summary["within_limit"])

    def test_headless_monitor_still_rejects_unrecovered_failure(self):
        summary = renewal_macro._headless_open_failure_summary(
            {
                "confirmed_openings": 100,
                "open_failures": 50,
                "recovered_open_failures": 49,
            }
        )
        self.assertEqual(summary["unrecovered_open_failures"], 1)
        self.assertFalse(summary["within_limit"])

    def test_finished_checkpoint_keeps_the_resources_used_for_pass(self):
        accepted = {
            "within_limits": True,
            "cpu_system_percent_average": 2.5,
        }
        later_sample = {
            "within_limits": False,
            "cpu_system_percent_average": 3.5,
        }
        selected = renewal_macro._headless_checkpoint_resources(
            {"resources": accepted},
            finished=True,
            live_resources=later_sample,
        )
        self.assertEqual(selected, accepted)
        self.assertIsNot(selected, accepted)

    def test_running_checkpoint_uses_current_resources(self):
        previous = {"within_limits": True}
        current = {"within_limits": False}
        selected = renewal_macro._headless_checkpoint_resources(
            {"resources": previous},
            finished=False,
            live_resources=current,
        )
        self.assertEqual(selected, current)

    def test_resource_sampler_rejects_near_duplicate_final_sample(self):
        samples = [{"elapsed_seconds": 30.0}]
        self.assertFalse(
            renewal_macro._headless_resource_sample_due(samples, 30.1)
        )
        self.assertTrue(
            renewal_macro._headless_resource_sample_due(samples, 35.0)
        )

    def test_eight_openings_accept_new_stable_frames_and_skip_stale_size(self):
        height, width = 40, 80
        opened = np.full((height, width), 251, dtype=np.uint8)
        opened[0, 0] = 1
        closed = np.zeros((height, width), dtype=np.uint8)
        stale = np.full((height + 1, width), 251, dtype=np.uint8)
        stale[0, 0] = 1
        transition = np.full((height, width), 241, dtype=np.uint8)
        transition[0, 0] = 1

        frames = [opened.copy()]
        frames.extend([stale.copy(), transition.copy()])
        frames.extend(
            opened.copy()
            for _ in range(
                renewal_macro.RENEWAL_CALIBRATION_FRAMES_PER_OPENING
            )
        )
        for _ in range(
            renewal_macro.RENEWAL_CALIBRATION_OPENINGS - 1
        ):
            frames.extend(closed.copy() for _ in range(4))
            frames.append(closed.copy())
            frames.extend([stale.copy(), transition.copy()])
            frames.extend(
                opened.copy()
                for _ in range(
                    renewal_macro.RENEWAL_CALIBRATION_FRAMES_PER_OPENING
                )
            )

        manager = _Manager(frames, opened)
        app = SimpleNamespace(
            window_title_var=_Value("FC ONLINE"),
            side_var=_Value("buy"),
            status_var=_Value(),
            root=_Root(),
            log_messages=[],
        )
        app.log = app.log_messages.append

        with (
            mock.patch.object(
                renewal_macro,
                "InactiveManager",
                return_value=manager,
            ),
            mock.patch.object(renewal_macro, "RenewalModalGuard", _Guard),
            mock.patch.object(
                renewal_macro,
                "RenewalChangeDetector",
                _Detector,
            ),
            mock.patch.object(
                renewal_macro,
                "validate_price_region",
                return_value=SimpleNamespace(valid=True),
            ),
            mock.patch.object(renewal_macro, "_FastClicker", _Clicker),
        ):
            sessions, closed_samples = renewal_macro.RenewalApp._capture_guard_samples(
                app,
                NormalizedRect(0.0, 0.0, 1.0, 1.0),
                (20, 10, 60, 30),
                NormalizedPoint(0.5, 0.5),
                (width, height),
            )

        self.assertEqual(
            len(sessions),
            renewal_macro.RENEWAL_CALIBRATION_OPENINGS,
        )
        self.assertTrue(
            all(
                len(session)
                == renewal_macro.RENEWAL_CALIBRATION_FRAMES_PER_OPENING
                for session in sessions
            )
        )
        self.assertEqual(len(closed_samples), 4)
        self.assertEqual(
            _Clicker.instance.escape_count,
            renewal_macro.RENEWAL_CALIBRATION_OPENINGS - 1,
        )
        self.assertEqual(
            _Clicker.instance.click_count,
            renewal_macro.RENEWAL_CALIBRATION_OPENINGS - 1,
        )
        self.assertTrue(manager.stopped)
        self.assertTrue(
            any("8/8 팝업 확인" in line for line in app.log_messages)
        )
        popup_logs = [
            line for line in app.log_messages if "팝업 확인" in line
        ]
        self.assertEqual(
            len(popup_logs),
            renewal_macro.RENEWAL_CALIBRATION_OPENINGS,
        )
        self.assertTrue(
            all("불일치 1장" in line for line in popup_logs)
        )


if __name__ == "__main__":
    unittest.main()
