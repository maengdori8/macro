from __future__ import annotations

import itertools
import threading
import time
from pathlib import Path
import tempfile
import unittest

from macroapp.skip_experiment import (
    GuardedActionResult,
    SkipExperimentTracker,
    load_device_learning,
    remove_device_learning,
    run_guarded_inactive_action,
    save_device_learning,
)


def _guard(*, ok: bool = True, invariant: bool = True, attempted: bool = True):
    return GuardedActionResult(
        attempted=attempted,
        action_ok=ok,
        foreground_before=10,
        foreground_after=10 if invariant else 20,
        foreground_samples=(10, 10) if invariant else (10, 20),
        invariant_ok=invariant,
        reason="ok" if ok and invariant else "foreground_changed",
        elapsed_seconds=0.01,
    )


class ForegroundGuardTests(unittest.TestCase):
    def test_refuses_when_game_is_already_foreground(self) -> None:
        called = False

        def action() -> bool:
            nonlocal called
            called = True
            return True

        result = run_guarded_inactive_action(action, lambda: 55, 55)
        self.assertFalse(result.attempted)
        self.assertFalse(called)
        self.assertEqual(result.reason, "target_already_foreground")
        self.assertIsNone(result.action_started_at)

    def test_accepts_action_when_foreground_never_changes(self) -> None:
        result = run_guarded_inactive_action(
            lambda: True,
            lambda: 10,
            55,
            poll_seconds=0.001,
        )
        self.assertTrue(result.action_ok)
        self.assertTrue(result.invariant_ok)
        self.assertEqual(result.foreground_before, result.foreground_after)
        self.assertIsNotNone(result.action_started_at)

    def test_allows_user_to_switch_between_other_foreground_windows(self) -> None:
        values = itertools.chain([10, 10, 20, 30], itertools.repeat(30))
        lock = threading.Lock()

        def foreground() -> int:
            with lock:
                return next(values)

        def action() -> bool:
            time.sleep(0.015)
            return True

        result = run_guarded_inactive_action(
            action,
            foreground,
            55,
            poll_seconds=0.001,
        )
        self.assertTrue(result.invariant_ok)
        self.assertEqual(result.reason, "ok")
        self.assertNotIn(55, result.foreground_samples)
        self.assertGreater(len(set(result.foreground_samples)), 1)

    def test_detects_transient_foreground_change_during_hold(self) -> None:
        values = itertools.chain([10, 10, 55, 10], itertools.repeat(10))
        lock = threading.Lock()

        def foreground() -> int:
            with lock:
                return next(values)

        def action() -> bool:
            time.sleep(0.015)
            return True

        result = run_guarded_inactive_action(
            action,
            foreground,
            55,
            poll_seconds=0.001,
        )
        self.assertFalse(result.invariant_ok)
        self.assertEqual(result.reason, "target_became_foreground")


class SkipExperimentTrackerTests(unittest.TestCase):
    def test_does_not_restore_learned_candidate_removed_for_safety(self) -> None:
        tracker = SkipExperimentTracker(
            ("safe",),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=2,
            learned="unsafe_removed",
        )
        self.assertIsNone(tracker.learned)
        candidate, _ = tracker.choose(0.0)
        self.assertEqual(candidate, "safe")

    def test_restored_candidate_uses_full_no_input_control(self) -> None:
        tracker = SkipExperimentTracker(
            ("winner", "other"),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=3,
            learned="winner",
            control_seconds=3.0,
            progressive_control=True,
        )
        candidate, _ = tracker.choose(0.0)
        self.assertIsNone(candidate)
        self.assertEqual(tracker.episode_control_seconds, 3.0)
        candidate, _ = tracker.choose(2.99)
        self.assertIsNone(candidate)
        candidate, _ = tracker.choose(3.0)
        self.assertEqual(candidate, "winner")

    def test_control_offsets_cycle_without_shortening_mandatory_control(self) -> None:
        tracker = SkipExperimentTracker(
            ("control_noop", "route"),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=3,
            control_seconds=3.0,
            control_offsets=(0.0, 0.17, 0.41, 0.73),
            sham_candidate="control_noop",
            sham_every=4,
        )

        observed = []
        for episode in range(4):
            started = float(episode * 10)
            self.assertEqual(tracker.choose(started), (None, None))
            observed.append(tracker.episode_control_seconds)
            tracker.prompt_disappeared(started + 0.1)

        self.assertEqual(observed, [3.0, 3.17, 3.41, 3.73])

    def test_failed_restored_candidate_is_invalidated(self) -> None:
        tracker = SkipExperimentTracker(
            ("winner", "other"),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=3,
            learned="winner",
        )
        candidate, _ = tracker.choose(0.0)
        outcome = tracker.record_attempt(
            candidate,
            0.0,
            _guard(ok=False),
        )
        self.assertEqual(outcome.status, "failed")
        self.assertIsNone(tracker.learned)
        self.assertEqual(tracker.choose(0.1), (None, None))
        tracker.prompt_disappeared(0.2)
        candidate, _ = tracker.choose(1.0)
        self.assertEqual(candidate, "other")

    def test_cycles_after_visible_prompt_timeout(self) -> None:
        tracker = SkipExperimentTracker(
            ("one", "two"),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=2,
        )
        candidate, _ = tracker.choose(0.0)
        self.assertEqual(candidate, "one")
        tracker.record_attempt(candidate, 0.0, _guard())

        candidate, expired = tracker.choose(1.1)
        self.assertEqual(expired.status, "failed")
        self.assertIsNone(candidate, "같은 프롬프트에 두 후보를 섞으면 안 됨")
        tracker.prompt_disappeared(1.2)
        candidate, _ = tracker.choose(2.0)
        self.assertEqual(candidate, "two")

    def test_learns_only_after_repeated_prompt_exits(self) -> None:
        tracker = SkipExperimentTracker(
            ("one", "two"),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=2,
        )
        for episode in range(2):
            now = float(episode * 2)
            candidate, _ = tracker.choose(now)
            self.assertEqual(candidate, "one")
            tracker.record_attempt(candidate, now, _guard())
            outcome = tracker.prompt_disappeared(now + 0.2)
        self.assertEqual(outcome.learned, "one")
        self.assertEqual(tracker.learned, "one")

    def test_quarantines_candidate_that_changes_foreground(self) -> None:
        tracker = SkipExperimentTracker(
            ("unsafe", "safe"),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=2,
        )
        candidate, _ = tracker.choose(0.0)
        outcome = tracker.record_attempt(candidate, 0.0, _guard(invariant=False))
        self.assertEqual(outcome.status, "quarantined")
        self.assertIn("unsafe", tracker.quarantined)
        self.assertEqual(tracker.choose(0.1), (None, None))
        tracker.prompt_disappeared(0.2)
        next_candidate, _ = tracker.choose(1.0)
        self.assertEqual(next_candidate, "safe")

    def test_failed_action_advances_without_waiting(self) -> None:
        tracker = SkipExperimentTracker(
            ("bad", "good"),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=2,
        )
        candidate, _ = tracker.choose(0.0)
        outcome = tracker.record_attempt(candidate, 0.0, _guard(ok=False))
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(tracker.choose(0.1), (None, None))
        tracker.prompt_disappeared(0.2)
        next_candidate, _ = tracker.choose(1.0)
        self.assertEqual(next_candidate, "good")

    def test_waits_for_no_input_control_before_candidate(self) -> None:
        tracker = SkipExperimentTracker(
            ("one",),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=2,
            control_seconds=0.75,
        )
        self.assertEqual(tracker.choose(0.0), (None, None))
        self.assertEqual(tracker.choose(0.74), (None, None))
        candidate, outcome = tracker.choose(0.75)
        self.assertEqual(candidate, "one")
        self.assertIsNone(outcome)

    def test_prompt_exit_during_control_is_not_attributed(self) -> None:
        tracker = SkipExperimentTracker(
            ("one",),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=2,
            control_seconds=0.75,
        )
        self.assertEqual(tracker.choose(0.0), (None, None))
        outcome = tracker.prompt_disappeared(0.3)
        self.assertEqual(outcome.status, "unattributed")
        self.assertEqual(
            outcome.detail,
            "opponent_or_natural_exit_during_control",
        )
        self.assertAlmostEqual(outcome.latency_seconds, 0.3)
        self.assertEqual(tracker.success_counts, {})
        self.assertIsNone(tracker.learned)

    def test_progressive_control_reaches_full_window_by_final_confirmation(self) -> None:
        tracker = SkipExperimentTracker(
            ("one",),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=3,
            control_seconds=1.2,
            progressive_control=True,
        )

        self.assertEqual(tracker.choose(0.0), (None, None))
        self.assertEqual(tracker.choose(0.04), (None, None))
        candidate, _ = tracker.choose(0.045)
        self.assertEqual(candidate, "one")
        tracker.record_attempt(candidate, 0.045, _guard())
        self.assertEqual(tracker.prompt_disappeared(0.1).detail, "confirmation=1/3")

        self.assertEqual(tracker.choose(2.0), (None, None))
        self.assertEqual(tracker.choose(2.35), (None, None))
        candidate, _ = tracker.choose(2.356)
        self.assertEqual(candidate, "one")
        tracker.record_attempt(candidate, 2.356, _guard())
        self.assertEqual(tracker.prompt_disappeared(2.4).detail, "confirmation=2/3")

        self.assertEqual(tracker.choose(4.0), (None, None))
        self.assertEqual(tracker.choose(5.19), (None, None))
        candidate, _ = tracker.choose(5.2)
        self.assertEqual(candidate, "one")

    def test_progressive_control_can_require_repeated_full_window_successes(self) -> None:
        tracker = SkipExperimentTracker(
            ("one",),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=5,
            control_seconds=3.0,
            progressive_control=True,
            control_ramp_successes=3,
        )

        expected_controls = (3.0 / 27.0, 24.0 / 27.0, 3.0, 3.0, 3.0)
        now = 0.0
        for confirmation, expected_control in enumerate(expected_controls, 1):
            self.assertEqual(tracker.choose(now), (None, None))
            self.assertAlmostEqual(
                tracker.episode_control_seconds,
                expected_control,
            )
            candidate, _ = tracker.choose(now + expected_control)
            self.assertEqual(candidate, "one")
            tracker.record_attempt(candidate, now + expected_control, _guard())
            outcome = tracker.prompt_disappeared(now + expected_control + 0.1)
            self.assertEqual(
                outcome.detail,
                f"confirmation={confirmation}/5",
            )
            if confirmation < 5:
                self.assertIsNone(outcome.learned)
            else:
                self.assertEqual(outcome.learned, "one")
            now += 5.0

    def test_control_exit_does_not_erase_progressive_confirmation_stage(self) -> None:
        tracker = SkipExperimentTracker(
            ("one",),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=3,
            control_seconds=1.2,
            progressive_control=True,
        )
        tracker.choose(0.0)
        candidate, _ = tracker.choose(0.045)
        tracker.record_attempt(candidate, 0.045, _guard())
        tracker.prompt_disappeared(0.1)

        tracker.choose(2.0)
        outcome = tracker.prompt_disappeared(2.3)
        self.assertEqual(outcome.status, "unattributed")
        self.assertEqual(tracker.success_counts["one"], 1)

        self.assertEqual(tracker.choose(3.0), (None, None))
        self.assertEqual(tracker.choose(3.35), (None, None))
        candidate, _ = tracker.choose(3.356)
        self.assertEqual(candidate, "one")

    def test_success_after_control_can_be_learned(self) -> None:
        tracker = SkipExperimentTracker(
            ("one",),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=1,
            control_seconds=0.5,
        )
        self.assertEqual(tracker.choose(0.0), (None, None))
        candidate, _ = tracker.choose(0.5)
        tracker.record_attempt(candidate, 0.5, _guard())
        outcome = tracker.prompt_disappeared(0.7)
        self.assertEqual(outcome.status, "success")
        self.assertEqual(outcome.learned, "one")

    def test_single_absent_frame_does_not_count_as_success(self) -> None:
        tracker = SkipExperimentTracker(
            ("one",),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=1,
            exit_confirm_seconds=0.4,
        )
        candidate, _ = tracker.choose(0.0)
        tracker.record_attempt(candidate, 0.0, _guard())

        self.assertIsNone(tracker.prompt_disappeared(0.1))
        candidate, outcome = tracker.choose(0.2)
        self.assertIsNone(candidate)
        self.assertIsNone(outcome)
        self.assertEqual(tracker.success_counts, {})
        self.assertIsNotNone(tracker.pending)

    def test_sustained_absence_counts_only_after_confirmation_window(self) -> None:
        tracker = SkipExperimentTracker(
            ("one",),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=1,
            exit_confirm_seconds=0.4,
        )
        candidate, _ = tracker.choose(0.0)
        tracker.record_attempt(candidate, 0.0, _guard())

        self.assertIsNone(tracker.prompt_disappeared(0.1))
        self.assertIsNone(tracker.prompt_disappeared(0.49))
        outcome = tracker.prompt_disappeared(0.5)
        self.assertEqual(outcome.status, "success")
        self.assertEqual(outcome.learned, "one")

    def test_result_deadline_uses_first_absent_frame_not_confirmation_end(self) -> None:
        tracker = SkipExperimentTracker(
            ("one",),
            result_window_seconds=1.5,
            attempt_gap_seconds=0.0,
            confirm_successes=1,
            exit_confirm_seconds=0.4,
        )
        candidate, _ = tracker.choose(0.0)
        tracker.record_attempt(candidate, 0.0, _guard())

        self.assertIsNone(tracker.prompt_disappeared(1.4))
        outcome = tracker.prompt_disappeared(1.8)

        self.assertEqual(outcome.status, "success")
        self.assertAlmostEqual(outcome.latency_seconds, 1.4)
        self.assertEqual(outcome.learned, "one")

    def test_first_absent_frame_after_result_deadline_still_fails(self) -> None:
        tracker = SkipExperimentTracker(
            ("one",),
            result_window_seconds=1.5,
            attempt_gap_seconds=0.0,
            confirm_successes=1,
            exit_confirm_seconds=0.4,
        )
        candidate, _ = tracker.choose(0.0)
        tracker.record_attempt(candidate, 0.0, _guard())

        self.assertIsNone(tracker.prompt_disappeared(1.6))
        outcome = tracker.prompt_disappeared(2.01)

        self.assertEqual(outcome.status, "failed")
        self.assertAlmostEqual(outcome.latency_seconds, 1.6)
        self.assertEqual(outcome.detail, "late_prompt_exit")

    def test_persistent_prompt_reopens_only_after_washout_and_new_control(
        self,
    ) -> None:
        tracker = SkipExperimentTracker(
            ("one", "two"),
            result_window_seconds=1.5,
            attempt_gap_seconds=0.0,
            confirm_successes=30,
            control_seconds=3.0,
            persistent_retry_seconds=8.0,
        )

        self.assertEqual(tracker.choose(0.0), (None, None))
        candidate, _ = tracker.choose(3.0)
        self.assertEqual(candidate, "one")
        tracker.record_attempt(candidate, 3.0, _guard())

        self.assertIsNone(tracker.choose(5.0)[0])
        self.assertEqual(tracker.failure_totals["one"], 1)
        self.assertIsNone(tracker.choose(10.99)[0])

        # Eight seconds after the first action begins a fresh episode, not an
        # immediate action. Its own full three-second control still applies.
        self.assertEqual(tracker.choose(11.0), (None, None))
        self.assertIsNone(tracker.choose(13.99)[0])
        candidate, _ = tracker.choose(14.0)
        self.assertEqual(candidate, "two")

    def test_three_restored_successes_enter_confirmation_phase(self) -> None:
        tracker = SkipExperimentTracker(
            ("one", "two"),
            result_window_seconds=1.5,
            attempt_gap_seconds=0.0,
            confirm_successes=30,
            confirmation_lock_successes=3,
            real_allocation=("explore", "exploit"),
        )
        for _ in range(3):
            self.assertTrue(tracker.restore_final_outcome("one", "success", 0.5))

        candidate, _ = tracker.choose(10.0)

        self.assertEqual(candidate, "one")
        self.assertEqual(tracker.selection_mode, "confirm")
        self.assertEqual(tracker.confirmation_candidate, "one")

    def test_confirmation_failure_releases_lock_and_resumes_discovery(self) -> None:
        tracker = SkipExperimentTracker(
            ("one", "two"),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=30,
            confirmation_lock_successes=3,
            real_allocation=("explore",),
        )
        for _ in range(3):
            tracker.restore_final_outcome("one", "success", 0.5)
        candidate, _ = tracker.choose(10.0)
        tracker.record_attempt(candidate, 10.0, _guard())
        outcome = tracker.expire_pending(11.1)
        self.assertEqual(outcome.status, "failed")
        self.assertIsNone(tracker.confirmation_candidate)
        self.assertIn("one", tracker.quarantined)

        tracker.reset_episode()
        candidate, _ = tracker.choose(20.0)
        self.assertEqual(candidate, "two")
        self.assertEqual(tracker.selection_mode, "explore")

    def test_three_trials_retire_a_mixed_success_failure_route(self) -> None:
        tracker = SkipExperimentTracker(
            ("unstable", "next"),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=30,
            min_family_attempts=3,
            real_allocation=("exploit",),
        )
        self.assertTrue(
            tracker.restore_final_outcome("unstable", "success", 0.4)
        )
        self.assertTrue(
            tracker.restore_final_outcome("unstable", "success", 0.5)
        )
        self.assertNotIn("unstable", tracker.quarantined)

        self.assertTrue(
            tracker.restore_final_outcome("unstable", "failed", 1.1)
        )

        self.assertIn("unstable", tracker.quarantined)
        self.assertIsNone(tracker.preferred)
        self.assertEqual(tracker._select_exploit(), "next")

    def test_third_trial_success_still_retires_a_previously_failed_route(self) -> None:
        tracker = SkipExperimentTracker(
            ("unstable", "next"),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=30,
            min_family_attempts=3,
            real_allocation=("exploit",),
        )
        self.assertTrue(
            tracker.restore_final_outcome("unstable", "failed", 1.1)
        )
        self.assertTrue(
            tracker.restore_final_outcome("unstable", "success", 0.4)
        )
        self.assertNotIn("unstable", tracker.quarantined)

        self.assertTrue(
            tracker.restore_final_outcome("unstable", "success", 0.5)
        )

        self.assertIn("unstable", tracker.quarantined)
        self.assertIsNone(tracker.preferred)
        self.assertEqual(tracker._select_exploit(), "next")

    def test_restored_sham_is_not_a_real_candidate_failure(self) -> None:
        tracker = SkipExperimentTracker(
            ("control_noop", "one"),
            result_window_seconds=1.0,
            attempt_gap_seconds=0.0,
            confirm_successes=30,
            sham_candidate="control_noop",
            sham_every=5,
        )
        self.assertTrue(
            tracker.restore_final_outcome(
                "control_noop", "failed", None
            )
        )
        self.assertEqual(tracker.sham_attempts, 1)
        self.assertEqual(tracker.failure_totals, {})


class SkipLearningStoreTests(unittest.TestCase):
    def test_round_trip_is_scoped_by_device_and_variant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skip_learning.json"
            self.assertTrue(
                save_device_learning(path, "device-a", "s", "CHAR_S_HOLD")
            )
            self.assertTrue(
                save_device_learning(path, "device-a", "a", "SPOOF_A_HOLD")
            )
            self.assertTrue(
                save_device_learning(path, "device-a", "generic", "PM_ESC_HOLD")
            )
            self.assertTrue(
                save_device_learning(path, "device-b", "s", "PM_S_HOLD")
            )

            self.assertEqual(
                load_device_learning(path, "device-a"),
                {
                    "a": "spoof_a_hold",
                    "s": "char_s_hold",
                    "generic": "pm_esc_hold",
                },
            )
            self.assertEqual(
                load_device_learning(path, "device-b"),
                {"s": "pm_s_hold"},
            )

            self.assertTrue(remove_device_learning(path, "device-a", "s"))
            self.assertEqual(
                load_device_learning(path, "device-a"),
                {"a": "spoof_a_hold", "generic": "pm_esc_hold"},
            )
            self.assertTrue(remove_device_learning(path, "device-a", "s"))

    def test_corrupt_file_and_invalid_write_fail_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skip_learning.json"
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(load_device_learning(path, "device-a"), {})
            self.assertFalse(
                save_device_learning(path, "device-a", "unknown", "candidate")
            )

    def test_legacy_noncausal_learning_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skip_learning.json"
            path.write_text(
                '{"version":1,"devices":{"device-a":{"a":"spoof_a_hold"}}}',
                encoding="utf-8",
            )
            self.assertEqual(load_device_learning(path, "device-a"), {})
            self.assertTrue(
                save_device_learning(path, "device-a", "s", "char_s_hold")
            )
            self.assertEqual(
                load_device_learning(path, "device-a"),
                {"s": "char_s_hold"},
            )

    def test_schema_two_learning_is_invalid_after_long_control_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skip_learning.json"
            path.write_text(
                '{"version":2,"devices":{"device-a":{"s":"sync_pm_s"}}}',
                encoding="utf-8",
            )
            self.assertEqual(load_device_learning(path, "device-a"), {})

    def test_schema_three_learning_is_invalid_after_repeated_control_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skip_learning.json"
            path.write_text(
                '{"version":3,"devices":{"device-a":{"s":"spoof_start_hold"}}}',
                encoding="utf-8",
            )
            self.assertEqual(load_device_learning(path, "device-a"), {})


if __name__ == "__main__":
    unittest.main()
