"""S/A skip retries stay single taps even when a cutscene persists."""

from macroapp.skip_foreground import ACTION_NONE, ACTION_TAP, ForegroundSkipPolicy


def test_first_eligible_observation_taps_immediately():
    policy = ForegroundSkipPolicy()
    assert policy.observe(100.0, True, True) == ACTION_TAP
    assert policy.presses == 1
    assert policy.active


def test_no_prompt_or_unavailable_input_never_taps():
    policy = ForegroundSkipPolicy()
    assert policy.observe(100.0, False, True) == ACTION_NONE
    assert policy.observe(101.0, True, False) == ACTION_NONE
    assert policy.presses == 0
    assert not policy.active


def test_repress_interval_is_respected():
    policy = ForegroundSkipPolicy(repress_seconds=0.35)
    assert policy.observe(100.0, True, True) == ACTION_TAP
    assert policy.observe(100.2, True, True) == ACTION_NONE
    assert policy.observe(100.36, True, True) == ACTION_TAP


def test_persistent_prompt_never_escalates_to_hold_or_second_device():
    policy = ForegroundSkipPolicy()
    for now in (0.0, 1.0, 3.0, 10.0, 30.0, 89.0):
        assert policy.observe(now, True, True) == ACTION_TAP
    assert policy.presses == 6


def test_unavailable_input_keeps_episode_clock_without_consuming_tap():
    policy = ForegroundSkipPolicy()
    assert policy.observe(0.0, True, False) == ACTION_NONE
    assert policy.observe(1.0, True, False) == ACTION_NONE
    assert policy.elapsed(1.0) == 1.0
    assert policy.observe(1.1, True, True) == ACTION_TAP
    assert policy.presses == 1


def test_disappearance_resets_episode():
    policy = ForegroundSkipPolicy(repress_seconds=0.0)
    policy.observe(0.0, True, True)
    policy.observe(0.1, True, True)
    assert policy.observe(0.2, False, True) == ACTION_NONE
    assert policy.first_seen_at is None
    assert policy.presses == 0
    assert not policy.active
    assert policy.observe(0.3, True, True) == ACTION_TAP


def test_attempt_limit_stops_repeated_inputs():
    policy = ForegroundSkipPolicy(repress_seconds=0.0, max_presses=3)
    assert [policy.observe(t, True, True) for t in (0.0, 0.1, 0.2, 0.3)] == [
        ACTION_TAP, ACTION_TAP, ACTION_TAP, ACTION_NONE,
    ]


def test_persistent_prompt_uses_slower_retries_but_same_tap():
    policy = ForegroundSkipPolicy(
        repress_seconds=0.35, slow_repress_after_seconds=3.0,
        slow_repress_seconds=1.0,
    )
    assert policy.observe(0.0, True, True) == ACTION_TAP
    assert policy.observe(3.0, True, True) == ACTION_TAP
    assert policy.observe(3.4, True, True) == ACTION_NONE
    assert policy.observe(4.0, True, True) == ACTION_TAP


def test_episode_time_limit_stops_input():
    policy = ForegroundSkipPolicy(repress_seconds=0.0, max_episode_seconds=10.0)
    assert policy.observe(0.0, True, True) == ACTION_TAP
    assert policy.observe(9.9, True, True) == ACTION_TAP
    assert policy.observe(10.0, True, True) == ACTION_NONE
    assert policy.observe(30.0, True, True) == ACTION_NONE
