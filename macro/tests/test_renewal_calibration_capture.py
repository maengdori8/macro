from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

import renewal_macro
from macroapp.renewal import NormalizedPoint, NormalizedRect


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
            1928,
            1048,
        )

        self.assertEqual(
            expanded.to_pixels(1928, 1048),
            (1312, 423, 1532, 463),
        )

    def test_headless_price_rect_keeps_already_wide_selection(self):
        original = NormalizedRect(
            1300 / 1928,
            423 / 1048,
            1530 / 1928,
            463 / 1048,
        )

        expanded = renewal_macro._expand_headless_price_rect(
            original,
            1928,
            1048,
        )

        self.assertEqual(
            expanded.to_pixels(1928, 1048),
            original.to_pixels(1928, 1048),
        )

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

    def test_five_openings_accept_new_stable_frames_and_skip_stale_size(self):
        height, width = 40, 80
        opened = np.full((height, width), 251, dtype=np.uint8)
        opened[0, 0] = 1
        closed = np.zeros((height, width), dtype=np.uint8)
        stale = np.full((height + 1, width), 251, dtype=np.uint8)
        stale[0, 0] = 1
        transition = np.full((height, width), 241, dtype=np.uint8)
        transition[0, 0] = 1

        frames = [opened.copy()]
        frames.extend(
            [
                stale.copy(),
                transition.copy(),
                opened.copy(),
                opened.copy(),
                opened.copy(),
                opened.copy(),
            ]
        )
        for _ in range(4):
            frames.extend(closed.copy() for _ in range(4))
            frames.append(closed.copy())
            frames.extend(
                [
                    stale.copy(),
                    transition.copy(),
                    opened.copy(),
                    opened.copy(),
                    opened.copy(),
                    opened.copy(),
                ]
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

        self.assertEqual(len(sessions), 5)
        self.assertTrue(all(len(session) == 4 for session in sessions))
        self.assertEqual(len(closed_samples), 4)
        self.assertEqual(_Clicker.instance.escape_count, 4)
        self.assertEqual(_Clicker.instance.click_count, 4)
        self.assertTrue(manager.stopped)
        self.assertTrue(
            any("5/5 팝업 확인" in line for line in app.log_messages)
        )
        popup_logs = [
            line for line in app.log_messages if "팝업 확인" in line
        ]
        self.assertEqual(len(popup_logs), 5)
        self.assertTrue(
            all("불일치 1장" in line for line in popup_logs)
        )


if __name__ == "__main__":
    unittest.main()
