from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from macroapp import turbo_session as turbo


def _process(
    pid: int,
    name: str,
    path: str,
    *,
    created: int = 100,
    working_mb: int = 100,
) -> turbo.ProcessIdentity:
    return turbo.ProcessIdentity(
        pid=pid,
        created_at_100ns=created,
        executable_name=name,
        executable_path=path,
        working_set_bytes=working_mb * 1024**2,
        private_bytes=(working_mb + 20) * 1024**2,
    )


class TurboSessionTests(unittest.TestCase):
    def test_candidates_require_exact_name_and_install_path(self) -> None:
        chrome = _process(
            10,
            "chrome.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            working_mb=250,
        )
        spoofed = _process(
            11,
            "chrome.exe",
            r"C:\Temp\chrome.exe",
            working_mb=900,
        )
        game = _process(
            12,
            "fczf.exe",
            r"C:\Nexon\EA SPORTS(TM) FC ONLINE\fczf.exe",
            working_mb=3000,
        )
        candidates = turbo.group_candidates((chrome, spoofed, game))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].key, "chrome")
        self.assertEqual(candidates[0].processes, (chrome,))
        self.assertEqual(candidates[0].working_set_mb, 250.0)

    def test_graceful_close_only_posts_to_selected_allowlisted_pids(self) -> None:
        chrome = _process(
            20,
            "chrome.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        )
        discord = _process(
            21,
            "discord.exe",
            r"C:\Users\m\AppData\Local\Discord\app-1\Discord.exe",
        )
        candidates = turbo.group_candidates((chrome, discord))
        posted: list[set[int]] = []
        with (
            patch.object(
                turbo,
                "_post_close_to_windows",
                side_effect=lambda pids: posted.append(set(pids)) or len(pids),
            ),
            patch.object(turbo, "_same_process", return_value=None),
        ):
            result = turbo.close_selected_gracefully(
                candidates,
                {"chrome"},
                timeout_seconds=0.0,
            )
        self.assertEqual(posted, [{20}])
        self.assertEqual(result.requested, (chrome,))
        self.assertEqual(result.closed, (chrome,))
        self.assertEqual(result.remaining, ())

    def test_pid_reuse_never_reaches_terminate_process(self) -> None:
        chrome = _process(
            30,
            "chrome.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        )
        fake_kernel32 = SimpleNamespace(
            OpenProcess=Mock(side_effect=AssertionError("must not open reused PID"))
        )
        with (
            patch.object(turbo, "_same_process", return_value=None),
            patch.object(turbo, "_kernel32", fake_kernel32),
        ):
            result = turbo.force_close_remaining((chrome,))
        self.assertEqual(result.closed, (chrome,))
        self.assertEqual(result.remaining, ())
        fake_kernel32.OpenProcess.assert_not_called()

    def test_graceful_timeout_never_forces_a_process(self) -> None:
        discord = _process(
            35,
            "discord.exe",
            r"C:\Users\m\AppData\Local\Discord\app-1\Discord.exe",
        )
        candidates = turbo.group_candidates((discord,))
        with (
            patch.object(turbo, "_post_close_to_windows", return_value=1),
            patch.object(turbo, "_same_process", return_value=discord),
        ):
            result = turbo.close_selected_gracefully(
                candidates,
                {"discord"},
                timeout_seconds=0.0,
            )
        self.assertEqual(result.closed, ())
        self.assertEqual(result.remaining, (discord,))

    def test_protected_process_is_never_a_force_close_request(self) -> None:
        game = _process(
            40,
            "fczf.exe",
            r"C:\Nexon\EA SPORTS(TM) FC ONLINE\fczf.exe",
        )
        result = turbo.force_close_remaining((game,))
        self.assertEqual(result.requested, ())
        self.assertEqual(result.closed, ())
        self.assertEqual(result.remaining, ())

    def test_resize_and_restore_keep_window_inactive(self) -> None:
        original = turbo.WindowRect(-4, -4, 2564, 1404)
        resized = turbo.WindowRect(-4, -4, 1924, 1044)
        fake_user32 = SimpleNamespace(SetWindowPos=Mock(return_value=True))
        with (
            patch.object(turbo, "_user32", fake_user32),
            patch.object(
                turbo,
                "_get_window_rect",
                side_effect=(original, resized),
            ),
        ):
            snapshot = turbo.resize_window_no_activate(
                99,
                turbo.TARGET_WGC_SIZE,
            )
        self.assertEqual(
            (snapshot.resized.width, snapshot.resized.height),
            turbo.TARGET_WGC_SIZE,
        )
        resize_flags = fake_user32.SetWindowPos.call_args.args[-1]
        self.assertTrue(resize_flags & turbo._SWP_NOACTIVATE)
        self.assertTrue(resize_flags & turbo._SWP_NOZORDER)

        fake_user32.SetWindowPos.reset_mock()
        with (
            patch.object(turbo, "_user32", fake_user32),
            patch.object(
                turbo,
                "_get_window_rect",
                side_effect=(resized, original),
            ),
        ):
            restored = turbo.restore_window_no_activate(snapshot)
        self.assertEqual((restored.width, restored.height), (2568, 1408))
        restore_flags = fake_user32.SetWindowPos.call_args.args[-1]
        self.assertTrue(restore_flags & turbo._SWP_NOACTIVATE)

    def test_target_outer_size_uses_measured_wgc_difference(self) -> None:
        current_outer = turbo.WindowRect(0, 0, 1928, 1048)
        self.assertEqual(
            turbo.target_outer_size_for_wgc(
                current_outer,
                (1920, 1040),
            ),
            (1936, 1056),
        )
        with self.assertRaises(ValueError):
            turbo.target_outer_size_for_wgc(
                current_outer,
                (1800, 900),
            )


if __name__ == "__main__":
    unittest.main()
