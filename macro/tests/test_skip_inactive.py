"""All controller, window, timing and keyboard calls are mocked."""

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from macroapp import skip_inactive


FC, ORIGINAL, THIRD, A = 101, 202, 303, 4096


@pytest.fixture
def env(monkeypatch):
    state = SimpleNamespace(foreground=ORIGINAL, now=0.0, stopped=False, valid=True)
    state.pad = Mock()
    state.lock = Mock()
    state.lock.acquire.return_value = True
    state.gui = SimpleNamespace(
        IsWindow=Mock(side_effect=lambda hwnd: hwnd == FC and state.valid),
        PostMessage=Mock(return_value=None),
        SetForegroundWindow=Mock(side_effect=AssertionError("No activation allowed")),
        SetFocus=Mock(side_effect=AssertionError("No focus change allowed")),
    )
    state.get_pad = Mock(return_value=state.pad)
    state.global_key = Mock(side_effect=AssertionError("No global keyboard allowed"))
    monkeypatch.setattr(skip_inactive.winapi, "win32gui", state.gui)
    monkeypatch.setattr(skip_inactive.winapi, "vg", SimpleNamespace(
        XUSB_BUTTON=SimpleNamespace(XUSB_GAMEPAD_A=A)
    ))
    monkeypatch.setattr(skip_inactive.input_gamepad, "_gamepad_lock", state.lock)
    monkeypatch.setattr(skip_inactive.input_gamepad, "_get_gamepad", state.get_pad)
    monkeypatch.setattr(skip_inactive.input_message, "get_foreground_hwnd", lambda: state.foreground)
    monkeypatch.setattr(skip_inactive.input_message, "_send_scancode", state.global_key)

    def wait(seconds):
        state.now += seconds
    state.wait = Mock(side_effect=wait)

    def run(route="pad_a", **kwargs):
        return skip_inactive.run_inactive_skip_tap(
            FC, route=route, cancelled=lambda: state.stopped,
            clock=lambda: state.now, wait=state.wait, **kwargs,
        )
    state.run = run
    yield state
    state.gui.SetForegroundWindow.assert_not_called()
    state.gui.SetFocus.assert_not_called()
    state.global_key.assert_not_called()


def test_a_is_exactly_one_short_tap_and_neutral_report(env):
    result = env.run()
    assert result.sent and result.attempted and result.released and result.foreground_unchanged
    assert result.reason == "sent" and result.route == "pad_a"
    assert env.pad.mock_calls == [call.press_button(button=A), call.update(),
                                  call.release_button(button=A), call.update()]
    assert env.now == pytest.approx(0.17)
    env.gui.PostMessage.assert_not_called()
    env.lock.release.assert_called_once()


def test_s_is_two_targeted_messages_with_correct_scan_and_release_bits(env):
    result = env.run("posted_s")
    assert result.sent and result.attempted and result.released
    assert env.gui.PostMessage.call_args_list == [
        call(FC, 0x100, 0x53, (0x1F << 16) | 1),
        call(FC, 0x101, 0x53, (0x1F << 16) | 1 | (1 << 30) | (1 << 31)),
    ]
    assert env.now == pytest.approx(0.06)
    env.get_pad.assert_not_called()


@pytest.mark.parametrize("route", ["pad_a", "posted_s"])
def test_user_already_in_fc_can_use_same_nonactivating_route(env, route):
    env.foreground = FC
    assert env.run(route).sent


@pytest.mark.parametrize("route", ["pad_a", "posted_s"])
@pytest.mark.parametrize("reason", ["cancelled", "invalid_target", "foreground_changed"])
def test_interruption_during_tap_releases_and_does_not_restore(env, route, reason):
    def wait(seconds):
        env.now += seconds
        if reason == "cancelled":
            env.stopped = True
        elif reason == "invalid_target":
            env.valid = False
        else:
            env.foreground = THIRD
    env.wait.side_effect = wait
    result = env.run(route)
    assert not result.sent and result.released and result.attempted
    assert result.reason == reason
    if reason == "foreground_changed":
        assert not result.foreground_unchanged and env.foreground == THIRD
    if route == "pad_a":
        env.pad.release_button.assert_called_once_with(button=A)
    else:
        assert env.gui.PostMessage.call_args_list[-1].args[1] == 0x101


@pytest.mark.parametrize("route", ["pad_a", "posted_s"])
def test_cancel_before_start_does_not_publish(env, route):
    env.stopped = True
    result = env.run(route)
    assert result.reason == "cancelled" and not result.attempted
    env.get_pad.assert_not_called()
    env.gui.PostMessage.assert_not_called()


@pytest.mark.parametrize("route", ["pad_a", "posted_s"])
def test_invalid_target_does_not_publish(env, route):
    env.valid = False
    result = env.run(route)
    assert result.reason == "invalid_target" and not result.attempted
    env.get_pad.assert_not_called()
    env.gui.PostMessage.assert_not_called()


def test_no_foreground_observation_does_not_publish(env):
    env.foreground = 0
    assert env.run().reason == "foreground_unknown"
    env.get_pad.assert_not_called()


def test_invalid_route_cannot_fall_back_to_start_or_global_keys(env):
    assert env.run("start").reason == "invalid_route"
    env.get_pad.assert_not_called()
    env.gui.PostMessage.assert_not_called()


def test_missing_pad_does_not_silently_switch_routes(env, monkeypatch):
    monkeypatch.setattr(skip_inactive.winapi, "vg", None)
    assert env.run().reason == "pad_unavailable"
    env.get_pad.assert_not_called()
    env.gui.PostMessage.assert_not_called()


def test_shared_pad_busy_does_not_queue_stale_tap(env):
    env.lock.acquire.return_value = False
    assert env.run().reason == "pad_busy"
    env.get_pad.assert_not_called()
    env.lock.release.assert_not_called()


@pytest.mark.parametrize("route", ["pad_a", "posted_s"])
def test_wait_exception_still_releases_input(env, route):
    env.wait.side_effect = RuntimeError("interrupted")
    result = env.run(route)
    assert not result.sent and result.released and result.attempted
    assert result.reason == "error:RuntimeError"
    if route == "pad_a":
        env.pad.release_button.assert_called_once_with(button=A)
        env.lock.release.assert_called_once()
    else:
        assert env.gui.PostMessage.call_args_list[-1].args[1] == 0x101


@pytest.mark.parametrize("point", ["press_button", "update"])
def test_pad_press_or_update_exception_still_releases(env, point):
    getattr(env.pad, point).side_effect = [RuntimeError("driver"), None]
    result = env.run()
    assert result.reason == "error:RuntimeError" and not result.sent
    env.pad.release_button.assert_called_once_with(button=A)
    env.lock.release.assert_called_once()


def test_pad_release_exception_never_reports_success_and_unlocks(env):
    env.pad.release_button.side_effect = RuntimeError("release rejected")
    result = env.run()
    assert result.reason == "error:RuntimeError" and not result.sent and not result.released
    assert env.pad.update.call_count == 2
    env.lock.release.assert_called_once()


def test_posted_down_exception_still_attempts_target_release(env):
    env.gui.PostMessage.side_effect = [RuntimeError("queue"), None]
    result = env.run("posted_s")
    assert result.reason == "error:RuntimeError" and result.released
    assert env.gui.PostMessage.call_count == 2


def test_posted_release_failure_never_reports_success(env):
    env.gui.PostMessage.side_effect = [None, False]
    result = env.run("posted_s")
    assert result.reason == "release_failed" and not result.sent and not result.released


def test_posted_down_failure_never_waits_or_reports_success(env):
    env.gui.PostMessage.side_effect = [False, None]
    result = env.run("posted_s")
    assert result.reason == "delivery_failed" and not result.sent and result.released
    env.wait.assert_not_called()


def test_focus_change_while_obtaining_controller_blocks_press(env):
    def get_pad():
        env.foreground = THIRD
        return env.pad
    env.get_pad.side_effect = get_pad
    result = env.run()
    assert result.reason == "foreground_changed" and not result.attempted
    env.pad.press_button.assert_not_called()
    env.lock.release.assert_called_once()


def test_stop_while_acquiring_controller_lock_blocks_press(env):
    def acquire(**kwargs):
        env.stopped = True
        return True
    env.lock.acquire.side_effect = acquire
    result = env.run()
    assert result.reason == "cancelled" and not result.attempted
    env.get_pad.assert_not_called()
    env.lock.release.assert_called_once()
