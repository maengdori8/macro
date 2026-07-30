from __future__ import annotations

import queue
import threading
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import renewal_macro
from macroapp.renewal_time import KST


class _Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class _TextValue:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = str(value)


class _Manager:
    def __init__(self, *_args, **_kwargs):
        self.stopped = False

    def stop_capture(self):
        self.stopped = True


class _ForbiddenClock:
    def __getattr__(self, name):
        raise AssertionError(
            f"infinite mode must not access the Naver clock: {name}"
        )


class RenewalScheduleToggleTests(unittest.TestCase):
    def _app(self):
        app = renewal_macro.RenewalApp.__new__(
            renewal_macro.RenewalApp
        )
        app.stop_event = threading.Event()
        app.naver_clock = _ForbiddenClock()
        app.window_title_var = _Value("FC ONLINE")
        app.profile = SimpleNamespace(speed_level=10)
        app.ui_queue = queue.SimpleQueue()
        app.logs = []
        app.log = app.logs.append
        app._report = lambda *_args, **_kwargs: None
        return app

    def test_schedule_is_off_by_default_status(self):
        app = self._app()
        app.schedule_enabled_var = _Value(False)
        app.schedule_status_var = _TextValue()

        app._refresh_schedule_status()

        self.assertIn("무한반복", app.schedule_status_var.value)

    def test_infinite_worker_does_not_require_naver_time(self):
        app = self._app()
        captured = {}

        class _Runner:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def run(self):
                return False

        with (
            mock.patch.object(
                renewal_macro,
                "InactiveManager",
                _Manager,
            ),
            mock.patch.object(
                renewal_macro,
                "FastRenewalRunner",
                _Runner,
            ),
        ):
            app._worker("buy", False, False, 20)

        self.assertIsNone(captured["active_until"])
        self.assertIsNone(captured["time_valid"])
        self.assertIs(captured["request_time"], renewal_macro.time.time)
        self.assertTrue(captured["video_speed_mode"])
        self.assertTrue(
            any("무한반복" in message for message in app.logs)
        )

    def test_checked_schedule_keeps_existing_naver_window(self):
        app = self._app()
        captured = {}

        class _Clock:
            last_error = ""

            @staticmethod
            def is_fresh():
                return True

            @staticmethod
            def sync():
                return True

            @staticmethod
            def now():
                return datetime(2026, 7, 30, 13, 19, tzinfo=KST)

            @staticmethod
            def to_monotonic(_target):
                return 12345.0

        class _Runner:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def run(self):
                return False

        app.naver_clock = _Clock()
        with (
            mock.patch.object(
                renewal_macro,
                "InactiveManager",
                _Manager,
            ),
            mock.patch.object(
                renewal_macro,
                "FastRenewalRunner",
                _Runner,
            ),
        ):
            app._worker("buy", False, True, 20)

        self.assertEqual(captured["active_until"], 12345.0)
        self.assertTrue(callable(captured["time_valid"]))
        self.assertTrue(callable(captured["request_time"]))


if __name__ == "__main__":
    unittest.main()
