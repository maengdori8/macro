from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from macroapp import gui
from macroapp.matching import is_notification_panel_open


def _notification_frame(*, windowed: bool = False) -> np.ndarray:
    height, width = 1048, 1928
    frame = np.full((height, width), 20, dtype=np.uint8)
    panel_x = int(width * 0.795)
    header_bottom = int(height * (0.058 if windowed else 0.03))

    if windowed:
        frame[: int(height * 0.026), :] = 245
        frame[int(height * 0.026):header_bottom, panel_x:] = 22
    else:
        frame[:header_bottom, panel_x:] = 22

    frame[header_bottom:int(height * 0.92), panel_x:] = 248
    return frame


class NotificationPanelDetectionTests(unittest.TestCase):
    def test_detects_borderless_notification_panel(self) -> None:
        self.assertTrue(is_notification_panel_open(_notification_frame()))

    def test_detects_windowed_notification_panel_below_title_bar(self) -> None:
        self.assertTrue(is_notification_panel_open(_notification_frame(windowed=True)))

    def test_rejects_bright_right_side_without_dimmed_backdrop(self) -> None:
        frame = _notification_frame()
        frame[:, : int(frame.shape[1] * 0.795)] = 120
        self.assertFalse(is_notification_panel_open(frame))

    def test_rejects_dim_screen_without_bright_panel(self) -> None:
        frame = np.full((1048, 1928), 20, dtype=np.uint8)
        self.assertFalse(is_notification_panel_open(frame))

    def test_rejects_bright_panel_without_dark_header(self) -> None:
        frame = _notification_frame()
        frame[: int(frame.shape[0] * 0.06), int(frame.shape[1] * 0.795):] = 248
        self.assertFalse(is_notification_panel_open(frame))

    def test_rejects_invalid_frame_shape(self) -> None:
        self.assertFalse(is_notification_panel_open(np.zeros((10, 10), dtype=np.uint8)))
        self.assertFalse(is_notification_panel_open(np.zeros((480, 640, 3), dtype=np.uint8)))

    def test_recovery_always_uses_guarded_background_click(self) -> None:
        app = gui.AutomationApp.__new__(gui.AutomationApp)
        app._notification_check_at = 0.0
        app._notification_streak = 0
        app._notification_visible = False
        app._notification_last_action_at = 0.0
        logs = []
        app.queue_status = lambda _message: None
        app.queue_log = logs.append
        app.dispatch_click = lambda *_args, **_kwargs: self.fail(
            "physical/configured click path must not be used"
        )
        clicks = []
        manager = SimpleNamespace(
            hwnd=55,
            get_virtual_start_position=lambda x, y: (10, 20),
            post_curved_click=lambda *args: clicks.append(args) or True,
        )
        frame = _notification_frame()

        with patch.object(
            gui.input_message,
            "get_foreground_hwnd",
            return_value=10,
        ):
            self.assertTrue(
                app._try_close_notification_panel(
                    frame,
                    manager,
                    "mouse",
                    "wgc",
                    (0, 0, 1928, 1048),
                    1.0,
                )
            )
            self.assertEqual(clicks, [])
            self.assertTrue(
                app._try_close_notification_panel(
                    frame,
                    manager,
                    "mouse",
                    "wgc",
                    (0, 0, 1928, 1048),
                    1.5,
                )
            )

        self.assertEqual(len(clicks), 1)
        self.assertEqual(clicks[0][:2], (10, 20))
        self.assertTrue(any("전면 불변 유지" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
