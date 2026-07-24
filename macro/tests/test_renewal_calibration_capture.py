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
    def __init__(self, baseline, price_box, shift_limit=4):
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
    def test_five_openings_accept_new_stable_frames_and_skip_stale_size(self):
        height, width = 40, 80
        opened = np.ones((height, width), dtype=np.uint8)
        closed = np.zeros((height, width), dtype=np.uint8)
        stale = np.ones((height + 1, width), dtype=np.uint8)
        transition = np.full((height, width), 2, dtype=np.uint8)
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
            all("가격 전환 1장" in line for line in popup_logs)
        )


if __name__ == "__main__":
    unittest.main()
