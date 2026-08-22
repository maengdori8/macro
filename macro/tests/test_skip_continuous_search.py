from __future__ import annotations

import itertools
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from macroapp import config, gui
from macroapp.skip_candidates import (
    SKIP_A_CANDIDATES,
    SKIP_CANDIDATE_SPECS,
    SKIP_GENERIC_ANY_KEY_CANDIDATES,
    SKIP_GENERIC_CANDIDATES,
    SKIP_GENERIC_ESCAPE_CANDIDATES,
    SKIP_GENERIC_HIGHLIGHT_CANDIDATES,
    SKIP_S_CANDIDATES,
    get_skip_candidate_spec,
)
from macroapp.skip_experiment import (
    GuardedActionResult,
    SkipExperimentTracker,
    run_guarded_inactive_action,
)


def _guard(ok: bool = True) -> GuardedActionResult:
    return GuardedActionResult(
        attempted=True,
        action_ok=ok,
        foreground_before=10,
        foreground_after=10,
        foreground_samples=(10,),
        invariant_ok=True,
        reason="ok" if ok else "action_failed",
        elapsed_seconds=0.01,
    )


def _finish_episode(
    tracker: SkipExperimentTracker,
    now: float,
    *,
    success: bool,
):
    candidate, _ = tracker.choose(now)
    assert candidate is not None
    selection_mode = tracker.selection_mode
    tracker.record_attempt(candidate, now, _guard())
    if success:
        outcome = tracker.prompt_disappeared(now + 0.1)
    else:
        outcome = tracker.expire_pending(now + 2.0)
        tracker.prompt_disappeared(now + 2.1)
    return candidate, outcome, selection_mode


def test_strict_install_uses_the_full_three_second_control() -> None:
    assert config.SKIP_STRICT_INACTIVE_EXPERIMENT is True
    assert config.SKIP_EXPERIMENT_CONTROL_SECONDS == 3.0
    assert config.SKIP_EXPERIMENT_PROGRESSIVE_CONTROL is False


def test_highlight_tracker_only_adds_nonnegative_control_offsets() -> None:
    app = object.__new__(gui.AutomationApp)
    app._skip_learned_profiles = {
        "generic_escape_highlight": "spoof_start_envelope2_settle350",
    }

    highlight = app._new_skip_generic_experiment_tracker("escape_highlight")
    ordinary = app._new_skip_generic_experiment_tracker("any_key")

    assert highlight.control_offsets == (
        config.SKIP_EXPERIMENT_HIGHLIGHT_CONTROL_OFFSETS
    )
    assert highlight.control_offsets == (0.0,)
    assert min(highlight.control_offsets) == 0.0
    assert ordinary.control_offsets == (0.0,)
    assert highlight.exit_confirm_seconds == (
        config.SKIP_EXPERIMENT_HIGHLIGHT_EXIT_CONFIRM_SECONDS
    )
    assert ordinary.exit_confirm_seconds == (
        config.SKIP_EXPERIMENT_EXIT_CONFIRM_SECONDS
    )
    assert highlight.learned is None
    assert highlight.persistent_retry_seconds == (
        config.SKIP_EXPERIMENT_HIGHLIGHT_RETRY_SECONDS
    )
    assert ordinary.persistent_retry_seconds == 0.0


def test_history_replay_rebuilds_only_highlight_long_absence_evidence(
    tmp_path,
) -> None:
    app = object.__new__(gui.AutomationApp)
    app._skip_learned_profiles = {}
    app._skip_device_id = "device-under-test"
    app.base_dir = tmp_path
    app._skip_experiment = object()
    app._skip_s_experiment = object()
    app._skip_generic_experiment = app._new_skip_generic_experiment_tracker()
    app._skip_generic_any_key_experiment = (
        app._new_skip_generic_experiment_tracker("any_key")
    )
    app._skip_generic_escape_experiment = (
        app._new_skip_generic_experiment_tracker("escape")
    )
    app._skip_generic_escape_highlight_experiment = (
        app._new_skip_generic_experiment_tracker("escape_highlight")
    )

    common = {
        "classifier_generation": config.SKIP_PROMPT_CLASSIFIER_GENERATION,
        "device_id": app._skip_device_id,
        "variant": "generic",
        "status": "success",
        "latency_seconds": 0.8,
    }
    events = [
        {
            **common,
            "prompt_hint": "any_key",
            "candidate": "spoof_start_envelope2",
        },
        {
            **common,
            "prompt_hint": "escape_highlight",
            "candidate": "spoof_start_envelope2_settle350",
            # A legacy 0.4-second label gap is intentionally not restored.
        },
        {
            **common,
            "prompt_hint": "escape_highlight",
            "candidate": "spoof_start_envelope2_settle350",
            "exit_confirm_seconds": (
                config.SKIP_EXPERIMENT_HIGHLIGHT_EXIT_CONFIRM_SECONDS
            ),
        },
    ]
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    path = log_dir / config.SKIP_EXPERIMENT_LOG_FILENAME
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    assert app._restore_skip_experiment_history() == 2
    assert (
        app._skip_generic_any_key_experiment.success_totals[
            "spoof_start_envelope2"
        ]
        == 1
    )
    assert (
        app._skip_generic_escape_highlight_experiment.success_totals[
            "spoof_start_envelope2_settle350"
        ]
        == 1
    )


def test_new_ds4_back_hypotheses_are_scheduled_for_generic_prompts() -> None:
    expected = {
        "ds4_share",
        "spoof_ds4_share",
        "device_ds4_share",
        "raw_device_ds4_share",
        "device_ds4_rescan_control",
        "device_ds4_share_hold500",
    }

    assert expected.issubset(SKIP_GENERIC_ESCAPE_CANDIDATES)
    assert expected.issubset(SKIP_GENERIC_ANY_KEY_CANDIDATES)


def test_highlight_summary_has_an_independent_search_catalogue() -> None:
    assert SKIP_GENERIC_HIGHLIGHT_CANDIDATES[:69] == (
        "control_noop",
        "process_device_spoof_start_preactivate150_pair",
        "process_device_spoof_reset_start_compact",
        "process_device_sync_spoof_start_compact",
        "process_raw_spoof_start_compact",
        "process_device_raw_spoof_start_compact",
        "process_spoof_device_start_compact",
        "process_spoof_device_sync_start_compact",
        "process_spoof_device_raw_sync_start_compact",
        "process_spoof_device_raw_parallel_start_compact",
        "process_spoof_diapp_device_raw_parallel_start_compact",
        "process_spoof_device_raw_parallel_start_compact3",
        "process_spoof_device_raw_parallel_start_rehandshake2",
        "process_spoof_device_raw_stagger30_start_compact",
        "process_spoof_reset_device_raw_parallel_start_compact",
        "process_spoof_device_raw_parallel_start_refresh2",
        "process_device_spoof_start_wait300_a",
        "process_device_spoof_diapp_start_compact",
        "process_raw_sync_spoof_start_compact",
        "process_device_spoof_start_rescan2_pair",
        "process_device_spoof_start_activation_rearm_pair",
        "process_device_spoof_start_compact_control80",
        "process_device_spoof_start_pair_rehandshake2",
        "process_device_spoof_start_b_combo2",
        "process_device_spoof_start_back_combo2",
        "process_device_spoof_start_then_b_pair",
        "process_device_spoof_start_then_back_pair",
        "process_device_spoof_a_envelope2",
        "process_device_spoof_start_then_a_pair",
        "process_device_spoof_start_a_combo2",
        "process_device_spoof_a_then_start_then_a",
        "process_device_spoof_start_then_a_single",
        "process_device_spoof_ds4_options_then_cross_gap0",
        "process_appcommand_browser_back",
        "process_command_idcancel",
        "process_notify_appcommand_browser_back",
        "process_notify_command_idcancel",
        "process_cancelmode",
        "process_device_spoof_start_refresh2",
        "process_device_spoof_start_refresh650",
        "windowmsg_start_refresh2",
        "windowmsg_start_edge2",
        "windowmsg_start_compact_settle100",
        "windowmsg_start_compact_settle200",
        "process_device_spoof_start_then_a_single_gap0",
        "process_device_spoof_ds4_options_then_cross",
        "process_device_spoof_start_compact_fixed3",
        "process_device_spoof_start_compact3",
        "process_device_spoof_start_compact4_hold130",
        "process_device_spoof_start_compact4_gap10",
        "process_device_spoof_start_compact4",
        "focusmsg_start_spread650",
        "windowmsg_start_spread650",
        "focuswindow_start_spread650",
        "focusmsg_start_compact",
        "windowmsg_start_compact",
        "focuswindow_start_compact",
        "focuswindow_start_envelope2",
        "process_device_spoof_start_single150",
        "process_device_spoof_start_single180",
        "process_device_spoof_start_wake40_finish150",
        "process_device_spoof_start_wake60_finish150",
        "process_device_spoof_start_edge60_pair",
        "process_device_spoof_start_edge40_pair",
        "process_device_spoof_start_envelope2_gap50",
        "process_device_spoof_start_envelope2_settle0",
        "process_device_spoof_start_envelope2_compact",
        "process_device_spoof_start_envelope2",
        "process_device_spoof_start_fast3",
    )
    assert set(SKIP_GENERIC_ESCAPE_CANDIDATES) <= set(
        SKIP_GENERIC_HIGHLIGHT_CANDIDATES
    )
    assert "process_device_spoof_a_envelope2" in SKIP_S_CANDIDATES
    fixed_three = get_skip_candidate_spec(
        "process_device_spoof_start_compact_fixed3"
    )
    assert fixed_three is not None
    assert fixed_three.action == "process_device_spoof_start_envelope2_compact"
    assert fixed_three.hold_seconds == 0.15
    assert fixed_three.pulse_gap_seconds == 0.05
    control80 = get_skip_candidate_spec(
        "process_device_spoof_start_compact_control80"
    )
    assert control80 is not None
    assert control80.action == "process_device_spoof_start_envelope2_compact"
    assert control80.hold_seconds == 0.15
    assert control80.pulse_gap_seconds == 0.05
    assert {
        "process_device_spoof_start_envelope2_settle0",
        "process_device_spoof_start_envelope2_gap50",
        "process_device_spoof_start_envelope2_compact",
        "process_device_spoof_start_pair_rehandshake2",
        "process_device_spoof_start_rescan2_pair",
        "process_device_spoof_start_activation_rearm_pair",
        "process_device_spoof_start_compact_control80",
        "process_device_spoof_start_preactivate150_pair",
        "process_device_spoof_reset_start_compact",
        "process_device_sync_spoof_start_compact",
        "process_raw_spoof_start_compact",
        "process_device_raw_spoof_start_compact",
        "process_spoof_device_start_compact",
        "process_spoof_device_sync_start_compact",
        "process_spoof_device_raw_sync_start_compact",
        "process_spoof_device_raw_parallel_start_compact",
        "process_spoof_diapp_device_raw_parallel_start_compact",
        "process_spoof_device_raw_parallel_start_compact3",
        "process_spoof_device_raw_parallel_start_rehandshake2",
        "process_spoof_device_raw_stagger30_start_compact",
        "process_spoof_reset_device_raw_parallel_start_compact",
        "process_spoof_device_raw_parallel_start_refresh2",
        "process_device_spoof_start_wait300_a",
        "process_device_spoof_diapp_start_compact",
        "process_raw_sync_spoof_start_compact",
        "process_device_spoof_start_single150",
        "process_device_spoof_start_single180",
        "process_device_spoof_start_wake40_finish150",
        "process_device_spoof_start_wake60_finish150",
        "process_device_spoof_start_edge60_pair",
        "process_device_spoof_start_edge40_pair",
        "process_device_spoof_start_compact4",
        "process_device_spoof_start_compact4_gap10",
        "process_device_spoof_start_compact3",
        "process_device_spoof_start_compact4_hold130",
        "process_device_spoof_start_b_combo2",
        "process_device_spoof_start_back_combo2",
        "process_device_spoof_start_then_b_pair",
        "process_device_spoof_start_then_back_pair",
        "process_device_spoof_start_then_a_pair",
        "process_device_spoof_start_a_combo2",
        "process_device_spoof_a_then_start_then_a",
        "process_device_spoof_start_then_a_single",
        "process_device_spoof_start_then_a_single_gap0",
        "process_device_spoof_ds4_options_then_cross",
        "process_device_spoof_ds4_options_then_cross_gap0",
        "process_appcommand_browser_back",
        "process_command_idcancel",
        "process_notify_appcommand_browser_back",
        "process_notify_command_idcancel",
        "process_cancelmode",
        "process_device_spoof_start_refresh2",
        "process_device_spoof_start_refresh650",
        "windowmsg_start_refresh2",
        "windowmsg_start_edge2",
        "windowmsg_start_compact_settle100",
        "windowmsg_start_compact_settle200",
        "process_device_spoof_start_compact_fixed3",
    }.issubset(SKIP_GENERIC_HIGHLIGHT_CANDIDATES)
    assert len({
        get_skip_candidate_spec(name).family
        for name in (
            "process_device_spoof_start_envelope2_settle0",
            "process_device_spoof_start_envelope2_gap50",
            "process_device_spoof_start_envelope2_compact",
            "process_device_spoof_start_pair_rehandshake2",
            "process_device_spoof_start_rescan2_pair",
            "process_device_spoof_start_activation_rearm_pair",
            "process_device_spoof_start_compact_control80",
            "process_device_spoof_start_preactivate150_pair",
            "process_device_spoof_reset_start_compact",
            "process_device_sync_spoof_start_compact",
            "process_raw_spoof_start_compact",
            "process_device_raw_spoof_start_compact",
            "process_spoof_device_start_compact",
            "process_spoof_device_sync_start_compact",
            "process_spoof_device_raw_sync_start_compact",
            "process_spoof_device_raw_parallel_start_compact",
            "process_spoof_diapp_device_raw_parallel_start_compact",
            "process_spoof_device_raw_parallel_start_compact3",
            "process_spoof_device_raw_parallel_start_rehandshake2",
            "process_spoof_device_raw_stagger30_start_compact",
            "process_spoof_reset_device_raw_parallel_start_compact",
            "process_spoof_device_raw_parallel_start_refresh2",
            "process_device_spoof_start_wait300_a",
            "process_device_spoof_diapp_start_compact",
            "process_raw_sync_spoof_start_compact",
            "process_device_spoof_start_single150",
            "process_device_spoof_start_single180",
            "process_device_spoof_start_wake40_finish150",
            "process_device_spoof_start_wake60_finish150",
            "process_device_spoof_start_edge60_pair",
            "process_device_spoof_start_edge40_pair",
            "process_device_spoof_start_compact4",
            "process_device_spoof_start_compact4_gap10",
            "process_device_spoof_start_compact3",
            "process_device_spoof_start_compact4_hold130",
            "process_device_spoof_start_b_combo2",
            "process_device_spoof_start_back_combo2",
            "process_device_spoof_start_then_b_pair",
            "process_device_spoof_start_then_back_pair",
            "process_device_spoof_a_envelope2",
            "process_device_spoof_start_then_a_pair",
            "process_device_spoof_start_a_combo2",
            "process_device_spoof_a_then_start_then_a",
            "process_device_spoof_start_then_a_single",
            "process_device_spoof_start_then_a_single_gap0",
            "process_device_spoof_ds4_options_then_cross",
            "process_device_spoof_ds4_options_then_cross_gap0",
            "process_appcommand_browser_back",
            "process_command_idcancel",
            "process_notify_appcommand_browser_back",
            "process_notify_command_idcancel",
            "process_cancelmode",
            "process_device_spoof_start_refresh2",
            "process_device_spoof_start_refresh650",
            "windowmsg_start_refresh2",
            "windowmsg_start_edge2",
            "windowmsg_start_compact_settle100",
            "windowmsg_start_compact_settle200",
            "process_device_spoof_start_compact_fixed3",
        )
    }) == 59
    assert {
        "spoof_start_envelope2_settle150",
        "spoof_start_envelope2_settle250",
        "spoof_start_envelope2_settle350",
    }.issubset(SKIP_GENERIC_HIGHLIGHT_CANDIDATES)
    assert {
        "focusmsg_pm_esc",
        "appmsg_pm_esc",
        "windowmsg_pm_esc",
    }.isdisjoint(SKIP_GENERIC_HIGHLIGHT_CANDIDATES)
    assert len({
        get_skip_candidate_spec(name).family
        for name in (
            "focusmsg_pm_esc",
            "appmsg_pm_esc",
            "windowmsg_pm_esc",
        )
    }) == 3
    assert {
        "focusmsg_start_envelope2",
        "appmsg_start_envelope2",
        "windowmsg_start_envelope2",
        "focuswindow_start_envelope2",
        "focusmsg_start_compact",
        "windowmsg_start_compact",
        "focuswindow_start_compact",
        "focusmsg_start_spread650",
        "windowmsg_start_spread650",
        "focuswindow_start_spread650",
    }.issubset(SKIP_GENERIC_HIGHLIGHT_CANDIDATES)
    assert len({
        get_skip_candidate_spec(name).family
        for name in (
            "focusmsg_start_envelope2",
            "appmsg_start_envelope2",
            "windowmsg_start_envelope2",
            "focuswindow_start_envelope2",
            "focusmsg_start_compact",
            "windowmsg_start_compact",
            "windowmsg_start_refresh2",
            "windowmsg_start_edge2",
            "windowmsg_start_compact_settle100",
            "windowmsg_start_compact_settle200",
            "focuswindow_start_compact",
            "focusmsg_start_spread650",
            "windowmsg_start_spread650",
            "focuswindow_start_spread650",
        )
    }) == 14
    assert len({
        get_skip_candidate_spec(name).family
        for name in (
            "spoof_start_envelope2_settle150",
            "spoof_start_envelope2_settle250",
            "spoof_start_envelope2_settle350",
        )
    }) == 3


def test_pending_skip_trial_uses_finer_observation_interval() -> None:
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app._skip_experiment = SimpleNamespace(pending=None, control_started_at=None)
    app._skip_s_experiment = SimpleNamespace(pending=None, control_started_at=None)
    app._skip_generic_experiment = SimpleNamespace(
        pending=None,
        control_started_at=None,
    )
    app._skip_generic_any_key_experiment = SimpleNamespace(
        pending=None,
        control_started_at=None,
    )
    app._skip_generic_escape_experiment = SimpleNamespace(
        pending=None,
        control_started_at=None,
    )
    app._skip_generic_escape_highlight_experiment = SimpleNamespace(
        pending=None,
        control_started_at=None,
    )

    assert app._current_skip_ocr_interval() == gui.SKIP_OCR_INTERVAL_SECONDS
    app._skip_generic_escape_highlight_experiment.control_started_at = 1.0
    assert app._current_skip_ocr_interval() == min(
        gui.SKIP_OCR_INTERVAL_SECONDS,
        gui.SKIP_PENDING_OCR_INTERVAL_SECONDS,
    )
    app._skip_generic_escape_highlight_experiment.control_started_at = None
    app._skip_generic_escape_highlight_experiment.pending = object()
    assert app._current_skip_ocr_interval() == min(
        gui.SKIP_OCR_INTERVAL_SECONDS,
        gui.SKIP_PENDING_OCR_INTERVAL_SECONDS,
    )


def test_total_allocation_is_40_40_20_in_each_five_episode_block() -> None:
    tracker = SkipExperimentTracker(
        ("control_noop", "new_route", "known_route"),
        result_window_seconds=1.5,
        attempt_gap_seconds=0.0,
        confirm_successes=30,
        sham_candidate="control_noop",
        sham_every=5,
        candidate_families={
            "control_noop": "sham",
            "new_route": "new",
            "known_route": "known",
        },
        min_family_attempts=1,
        real_allocation=("explore", "explore", "exploit", "exploit"),
    )

    modes = []
    for episode in range(5):
        candidate, _, mode = _finish_episode(
            tracker, episode * 10.0, success=True,
        )
        modes.append("sham" if candidate == "control_noop" else mode)

    assert modes == ["explore", "explore", "exploit", "exploit", "sham"]


def test_exploration_balances_families_before_repeating_one_family() -> None:
    candidates = ("a1", "a2", "b1", "b2")
    tracker = SkipExperimentTracker(
        candidates,
        result_window_seconds=1.0,
        attempt_gap_seconds=0.0,
        confirm_successes=30,
        candidate_families={
            "a1": "family_a", "a2": "family_a",
            "b1": "family_b", "b2": "family_b",
        },
        min_family_attempts=3,
        real_allocation=("explore",),
    )

    picked_families = []
    for episode in range(6):
        candidate, _, _ = _finish_episode(
            tracker, episode * 10.0, success=False,
        )
        picked_families.append(tracker.candidate_families[candidate])

    assert picked_families == [
        "family_a", "family_b", "family_a",
        "family_b", "family_a", "family_b",
    ]
    assert tracker.discovery_complete()


def test_new_candidate_is_not_hidden_by_completed_family_history() -> None:
    tracker = SkipExperimentTracker(
        ("old_a", "new_a", "old_b"),
        result_window_seconds=1.0,
        attempt_gap_seconds=0.0,
        confirm_successes=30,
        candidate_families={
            "old_a": "family_a",
            "new_a": "family_a",
            "old_b": "family_b",
        },
        min_family_attempts=3,
        real_allocation=("explore",),
    )
    tracker.attempt_counts.update({"old_a": 20, "old_b": 20})

    candidate, _ = tracker.choose(0.0)

    assert candidate == "new_a"


def test_exploit_rejects_lucky_route_at_sham_baseline() -> None:
    tracker = SkipExperimentTracker(
        ("control_noop", "lucky", "untested"),
        result_window_seconds=1.0,
        attempt_gap_seconds=0.0,
        confirm_successes=30,
        sham_candidate="control_noop",
        candidate_families={
            "control_noop": "sham",
            "lucky": "old",
            "untested": "new",
        },
        min_family_attempts=3,
    )
    tracker.attempt_counts["lucky"] = 7
    tracker.success_totals["lucky"] = 2
    tracker.failure_totals["lucky"] = 5
    tracker.sham_attempts = 10
    tracker.sham_successes = 3

    assert tracker._select_exploit() == "untested"


def test_exploit_accepts_reproduced_route_above_sham_baseline() -> None:
    tracker = SkipExperimentTracker(
        ("control_noop", "strong", "other"),
        result_window_seconds=1.0,
        attempt_gap_seconds=0.0,
        confirm_successes=30,
        sham_candidate="control_noop",
        min_family_attempts=3,
    )
    tracker.attempt_counts["strong"] = 3
    tracker.success_totals["strong"] = 2
    tracker.failure_totals["strong"] = 1
    tracker.sham_attempts = 10
    tracker.sham_successes = 1

    assert tracker._select_exploit() == "strong"


def test_clean_one_off_gets_one_limited_retest_then_loses_priority() -> None:
    tracker = SkipExperimentTracker(
        ("control_noop", "signal", "untested"),
        result_window_seconds=1.0,
        attempt_gap_seconds=0.0,
        confirm_successes=30,
        sham_candidate="control_noop",
        candidate_families={
            "control_noop": "sham",
            "signal": "signal",
            "untested": "new",
        },
        min_family_attempts=3,
    )
    tracker.sham_attempts = 10
    tracker.sham_successes = 1
    tracker.attempt_counts["signal"] = 1
    tracker.success_totals["signal"] = 1

    assert tracker._select_exploit() == "signal"

    tracker.attempt_counts["signal"] = 2
    tracker.failure_totals["signal"] = 1

    assert tracker._select_exploit() == "untested"


def test_three_coincident_exits_do_not_lock_below_sham_baseline() -> None:
    tracker = SkipExperimentTracker(
        ("control_noop", "coincident"),
        result_window_seconds=1.0,
        attempt_gap_seconds=0.0,
        confirm_successes=30,
        confirmation_lock_successes=3,
        sham_candidate="control_noop",
        min_family_attempts=3,
    )
    tracker.sham_attempts = 14
    tracker.sham_successes = 5
    tracker.attempt_counts["coincident"] = 10
    tracker.success_totals["coincident"] = 3
    tracker.failure_totals["coincident"] = 7
    tracker.success_counts["coincident"] = 3

    assert not tracker._beats_sham_baseline("coincident")
    assert tracker.confirmation_candidate is None


def test_new_sham_evidence_revokes_unsupported_confirmation_lock() -> None:
    tracker = SkipExperimentTracker(
        ("control_noop", "route"),
        result_window_seconds=1.0,
        attempt_gap_seconds=0.0,
        confirm_successes=30,
        confirmation_lock_successes=3,
        sham_candidate="control_noop",
        min_family_attempts=3,
    )
    tracker.attempt_counts["route"] = 3
    tracker.success_totals["route"] = 3
    tracker.success_counts["route"] = 3
    tracker.confirmation_candidate = "route"
    tracker.sham_attempts = 2
    tracker.sham_successes = 2

    tracker._record_final("control_noop", success=True, latency=0.3)

    assert tracker.confirmation_candidate is None
    assert tracker.success_counts["route"] == 0


def test_restored_confirmation_failure_retires_concrete_candidate() -> None:
    tracker = SkipExperimentTracker(
        ("control_noop", "missed", "next"),
        result_window_seconds=1.5,
        attempt_gap_seconds=0.0,
        confirm_successes=30,
        confirmation_lock_successes=3,
        sham_candidate="control_noop",
        sham_every=5,
        candidate_families={
            "control_noop": "sham",
            "missed": "old_timing",
            "next": "new_timing",
        },
        real_allocation=("exploit",),
    )
    for _ in range(3):
        assert tracker.restore_final_outcome("missed", "success", 0.6)
    assert tracker.confirmation_candidate == "missed"

    assert tracker.restore_final_outcome("missed", "failed", 1.6)

    assert tracker.confirmation_candidate is None
    assert "missed" in tracker.quarantined
    assert tracker._select_exploit() == "next"


def test_strict_guard_rejects_any_foreground_change_not_only_the_game() -> None:
    values = itertools.chain([10, 10, 20, 10], itertools.repeat(10))
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
        preserve_foreground=True,
    )
    assert not result.invariant_ok
    assert result.reason == "foreground_changed"


def test_catalogue_contains_only_non_global_input_scopes_and_unique_names() -> None:
    assert len(SKIP_A_CANDIDATES) == len(set(SKIP_A_CANDIDATES))
    assert len(SKIP_S_CANDIDATES) == len(set(SKIP_S_CANDIDATES))
    assert len(SKIP_GENERIC_CANDIDATES) == len(set(SKIP_GENERIC_CANDIDATES))
    assert len(SKIP_GENERIC_ANY_KEY_CANDIDATES) == len(
        set(SKIP_GENERIC_ANY_KEY_CANDIDATES)
    )
    assert len(SKIP_GENERIC_ESCAPE_CANDIDATES) == len(
        set(SKIP_GENERIC_ESCAPE_CANDIDATES)
    )
    direct_escape = {
        name for name, spec in SKIP_CANDIDATE_SPECS.items()
        if spec.family.startswith("window_escape")
        and spec.family != "window_escape_gamepad_spoof_sequence"
    }
    assert set(SKIP_CANDIDATE_SPECS) - direct_escape == (
        set(SKIP_A_CANDIDATES)
        | set(SKIP_S_CANDIDATES)
        | set(SKIP_GENERIC_CANDIDATES)
    )
    assert {
        spec.input_scope for spec in SKIP_CANDIDATE_SPECS.values()
    } <= {"none", "target_window", "virtual_gamepad"}
    assert all(
        spec.action not in {"focus_child_s", "focus_child_esc", "focus_s", "si_s"}
        for spec in SKIP_CANDIDATE_SPECS.values()
    )


def test_candidate_metadata_records_hypothesis_and_rejection_rule() -> None:
    spec = get_skip_candidate_spec("char_s_hold_1250")
    assert spec is not None
    metadata = spec.event_metadata()
    assert metadata["family"] == "window_char"
    assert metadata["hold_seconds"] == 1.25
    assert metadata["hypothesis"]
    assert metadata["reject_condition"]


def test_automatic_generic_catalogues_never_send_escape() -> None:
    direct_escape = {
        name for name, spec in SKIP_CANDIDATE_SPECS.items()
        if spec.family.startswith("window_escape")
        and spec.family != "window_escape_gamepad_spoof_sequence"
    }
    assert direct_escape.isdisjoint(SKIP_GENERIC_CANDIDATES)
    assert direct_escape.isdisjoint(SKIP_GENERIC_ANY_KEY_CANDIDATES)
    assert direct_escape.isdisjoint(SKIP_GENERIC_ESCAPE_CANDIDATES)
    assert direct_escape.isdisjoint(SKIP_GENERIC_HIGHLIGHT_CANDIDATES)
    assert "start" in SKIP_GENERIC_CANDIDATES
    assert "pm_space" in SKIP_GENERIC_CANDIDATES
    assert "pm_space_hold_1150" in SKIP_GENERIC_CANDIDATES
    assert "pm_space_hold_1250" in SKIP_GENERIC_CANDIDATES
    assert "pm_space_hold_1350" in SKIP_GENERIC_CANDIDATES
    assert "pm_space_pulse2" in SKIP_GENERIC_CANDIDATES
    assert "char_space" in SKIP_GENERIC_CANDIDATES
    assert "sync_pm_space" in SKIP_GENERIC_CANDIDATES
    assert "thread_pm_space" in SKIP_GENERIC_CANDIDATES
    assert "spoof_pm_space" in SKIP_GENERIC_CANDIDATES
    assert "notify_pm_space" in SKIP_GENERIC_CANDIDATES
    assert "callback_pm_space" in SKIP_GENERIC_CANDIDATES
    assert "up_pm_space" in SKIP_GENERIC_CANDIDATES
    assert "down_pm_space" in SKIP_GENERIC_CANDIDATES
    assert "attach_state_space" in SKIP_GENERIC_CANDIDATES
    assert "device_start" in SKIP_GENERIC_CANDIDATES
    assert "click_prompt_sync" in SKIP_GENERIC_CANDIDATES
    assert "click_prompt_noactivate" in SKIP_GENERIC_CANDIDATES
    assert "ds4_cross" in SKIP_GENERIC_CANDIDATES
    assert "ds4_options" in SKIP_GENERIC_CANDIDATES
    assert "ds4_touchpad" in SKIP_GENERIC_CANDIDATES
    assert "device_ds4_touchpad" in SKIP_GENERIC_CANDIDATES
    assert "spoof_envelope2_control" in SKIP_GENERIC_CANDIDATES
    assert "spoof_b_envelope2" in SKIP_GENERIC_ESCAPE_CANDIDATES
    assert "spoof_back_envelope2" in SKIP_GENERIC_ESCAPE_CANDIDATES
    assert "spoof_start_envelope2_escape_block" in SKIP_GENERIC_ESCAPE_CANDIDATES
    assert "spoof_start_preactivate80_burst3" in SKIP_GENERIC_ESCAPE_CANDIDATES
    assert "spoof_start_envelope4_fast" in SKIP_GENERIC_ESCAPE_CANDIDATES
    assert "spoof_start_envelope2" in SKIP_S_CANDIDATES
    assert "spoof_start_preactivate80_burst3" in SKIP_S_CANDIDATES
    assert "spoof_start_envelope4_fast" in SKIP_S_CANDIDATES
    # 2026-08-22: H1(attach_active_hold_a)+대조(attach_hold_a)가 S 맨 앞(1·2)에 선다 —
    # 구매자 PC 가 보는 프롬프트는 S 형이라 A 목록에만 있으면 영영 시도되지 않았다(원장 0행).
    assert SKIP_S_CANDIDATES[1:3] == ("attach_hold_a", "attach_active_hold_a")
    assert SKIP_S_CANDIDATES[3:9] == (
        "process_device_spoof_a_envelope2",
        "spoof_a_envelope2",
        "spoof_a_preactivate80_burst3",
        "spoof_start_preactivate80_burst3",
        "spoof_start_envelope4_fast",
        "spoof_start_envelope2",
    )
    assert "pm_esc" not in SKIP_A_CANDIDATES
    assert "pm_esc" not in SKIP_S_CANDIDATES
    assert all(
        SKIP_CANDIDATE_SPECS[name].input_scope
        in {"none", "target_window", "virtual_gamepad"}
        for name in SKIP_GENERIC_CANDIDATES
    )


def test_generic_subtypes_have_evidence_driven_independent_priorities() -> None:
    assert SKIP_GENERIC_ANY_KEY_CANDIDATES[1] == "spoof_start_hold"
    assert SKIP_CANDIDATE_SPECS[
        SKIP_GENERIC_ESCAPE_CANDIDATES[1]
    ].family == "window_escape_gamepad_spoof_sequence"
    assert set(SKIP_GENERIC_ANY_KEY_CANDIDATES) <= set(
        SKIP_GENERIC_CANDIDATES
    )
    assert set(SKIP_GENERIC_ESCAPE_CANDIDATES) <= set(
        SKIP_GENERIC_CANDIDATES
    )
    assert "click_prompt" in SKIP_GENERIC_ANY_KEY_CANDIDATES
    assert "click_prompt" in SKIP_GENERIC_ESCAPE_CANDIDATES


def test_pulse_candidate_dispatches_two_independent_gamepad_taps() -> None:
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"a": object()}, clear=False),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep"),
    ):
        assert gui.AutomationApp._press_skip_candidate("a_pulse2", manager)
    assert send.call_count == 2
    assert all(
        call.kwargs["press_delay"] == 0.18 for call in send.call_args_list
    )


def test_long_hold_candidate_uses_catalogue_duration() -> None:
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": object()}, clear=False),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "start_hold_1250",
            manager,
        )
    assert send.call_args.kwargs["press_delay"] == 1.25
