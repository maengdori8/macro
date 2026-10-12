"""S/A 실제 경로: 새 관측당 비활성 탭 하나, 전면화·홀드·실험 없음."""

import ast
from contextlib import ExitStack, contextmanager
import inspect
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest

from macroapp import config, gui, skip_inactive
from macroapp.skip_anykey import AnyKeyStartPolicy
from macroapp.skip_foreground import ForegroundSkipPolicy

GAME_HWND = 4321
OTHER_HWND = 9876


def _tracker():
    return SimpleNamespace(
        episode_control_seconds=3.0, pending=None,
        choose=Mock(return_value=(None, None)), reset_episode=Mock(),
        record_attempt=Mock(),
    )


def _app():
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app.base_dir = SimpleNamespace()
    for attr in (
        "_skip_seen_since", "_skip_kind", "_skip_prompt_variant",
        "_skip_generic_hint", "_skip_generic_episode_hint", "_skip_prompt_center",
        "_skip_prompt_visual", "_skip_last_press", "_skip_a_learned",
    ):
        setattr(app, attr, None)
    for attr in ("_skip_text_streak", "_skip_s_match_streak", "_skip_a_sweep_idx",
                 "_skip_esc_probe_negative_streak", "_frame_seq", "_skip_inactive_attempt"):
        setattr(app, attr, 0)
    for attr in ("_skip_esc_probe_allow_once", "_skip_precontrol_contaminated",
                 "_skip_control_contaminated", "_skip_foreground_direct"):
        setattr(app, attr, False)
    app._last_normal_action_at = float("-inf")
    app._skip_a_dumped = True
    app._skip_diag_count = 20
    app._skip_active_until = 0.0
    for attr in ("_skip_experiment", "_skip_s_experiment", "_skip_generic_experiment",
                 "_skip_generic_any_key_experiment", "_skip_generic_escape_experiment",
                 "_skip_generic_escape_highlight_experiment"):
        setattr(app, attr, _tracker())
    app._skip_anykey_policy = AnyKeyStartPolicy(consensus=config.SKIP_TEXT_CONSENSUS)
    app._skip_foreground_policy = ForegroundSkipPolicy()
    for attr in ("_capture_skip_prompt_visual", "_report_skip_experiment_outcome",
                 "_reconcile_skip_learning", "queue_status", "queue_log"):
        setattr(app, attr, Mock())
    app.stop_event = SimpleNamespace(is_set=lambda: False)
    return app


@contextmanager
def _environment(*, foreground=GAME_HWND, a_hit=True, s_hit=False,
                 text=(False, None), ocr=True, vg=True):
    """Mock every input endpoint, including window messages to the fake HWND."""
    with ExitStack() as stack:
        clock = SimpleNamespace(now=100.0)
        stack.enter_context(patch.object(gui.time, "monotonic", side_effect=lambda: clock.now))
        stack.enter_context(patch.object(gui.winapi, "vg", object() if vg else None))
        win32 = stack.enter_context(patch.object(gui.winapi, "win32gui", Mock()))
        stack.enter_context(patch.object(gui.rank_ocr, "ocr_available", return_value=ocr))
        match_a = stack.enter_context(patch.object(gui.rank_ocr, "match_skip_a",
            return_value=(a_hit, 0.9 if a_hit else 0.1, (10, 10) if a_hit else None)))
        stack.enter_context(patch.object(gui.rank_ocr, "match_skip_s",
            return_value=(s_hit, 0.9 if s_hit else 0.1, (10, 10) if s_hit else None)))
        classify = stack.enter_context(patch.object(gui.rank_ocr, "classify_skip_prompt", return_value=text))
        stack.enter_context(patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD,
                                      {"a": 4096, "s": 4096, "start": 16}, clear=True))
        pad = stack.enter_context(patch.object(gui, "send_gamepad_button", return_value=True))
        foreground_reader = stack.enter_context(patch.object(
            gui.input_message, "get_foreground_hwnd", return_value=foreground))
        key = stack.enter_context(patch.object(gui.input_message,
            "send_scancode_to_foreground_window", return_value=True))
        candidate = stack.enter_context(patch.object(gui.AutomationApp, "_press_skip_candidate",
            side_effect=AssertionError("S/A production must not run old candidate experiments")))
        sender = stack.enter_context(patch.object(gui, "run_inactive_skip_tap",
            side_effect=lambda _hwnd, **kwargs: SimpleNamespace(
                sent=True, route=kwargs["route"], reason="sent",
            )))
        yield SimpleNamespace(clock=clock, win32=win32, pad=pad, key=key, sender=sender,
                              classify=classify, match_a=match_a,
                              foreground=foreground_reader, candidate=candidate)


def _observe(app, seq=1):
    app._frame_seq = seq
    return app._try_skip(np.zeros((720, 1280), dtype=np.uint8),
                         SimpleNamespace(hwnd=GAME_HWND), frame_seq=seq)


def _routes(env):
    return [item.kwargs["route"] for item in env.sender.call_args_list]


def _assert_no_foreground_or_legacy_input(env):
    env.key.assert_not_called()
    env.win32.PostMessage.assert_not_called()
    env.win32.SetForegroundWindow.assert_not_called()
    env.win32.SetFocus.assert_not_called()
    env.pad.assert_not_called()
    env.candidate.assert_not_called()


@pytest.mark.parametrize("variant", ["a", "s"])
@pytest.mark.parametrize("foreground", [GAME_HWND, OTHER_HWND])
def test_a_and_s_use_one_equivalent_short_tap(variant, foreground):
    app = _app()
    with _environment(foreground=foreground, a_hit=variant == "a", s_hit=variant == "s") as env:
        for seq in range(1, (config.SKIP_TEXT_CONSENSUS if variant == "s" else 1) + 1):
            _observe(app, seq)
            env.clock.now += 0.4
        assert _routes(env) == ["pad_a"]
        assert env.sender.call_args.args == (GAME_HWND,)
        _assert_no_foreground_or_legacy_input(env)
    app._skip_experiment.choose.assert_not_called()
    app._skip_s_experiment.choose.assert_not_called()


@pytest.mark.parametrize("learned", ["start", "a_hold", "attach_active_hold_a"])
def test_old_learning_and_fifth_episode_cannot_trigger_experiments(learned):
    app = _app()
    app._skip_ap_episode_index = 4
    app._skip_episode_is_direct = False
    app._skip_a_learned = learned
    app._skip_experiment.learned = learned
    app._skip_s_experiment.learned = learned
    with _environment(foreground=OTHER_HWND) as env:
        assert _observe(app)
        assert _routes(env) == ["pad_a"]
        _assert_no_foreground_or_legacy_input(env)
    assert app._skip_episode_is_direct is True
    app._skip_experiment.choose.assert_not_called()
    app._skip_s_experiment.choose.assert_not_called()


@pytest.mark.parametrize("foreground", [GAME_HWND, OTHER_HWND])
def test_persistent_three_and_ten_second_prompt_stays_tap_only(foreground):
    app = _app()
    with _environment(foreground=foreground) as env:
        for seq, elapsed in enumerate((0.0, 3.0, 10.0), 1):
            env.clock.now = 100.0 + elapsed
            assert _observe(app, seq)
        assert _routes(env) == ["pad_a", "posted_s", "pad_a"]
        _assert_no_foreground_or_legacy_input(env)


def test_stop_blocks_every_input():
    app = _app()
    app.stop_event = SimpleNamespace(is_set=lambda: True)
    with _environment() as env:
        assert not _observe(app)
        env.sender.assert_not_called()
        env.key.assert_not_called()
        env.pad.assert_not_called()
        env.win32.PostMessage.assert_not_called()


@pytest.mark.parametrize("failure", [False, RuntimeError("keyboard delivery failed")])
def test_inactive_failure_has_no_success_record_or_extra_fallback(failure):
    app = _app()
    with _environment() as env:
        if isinstance(failure, Exception):
            env.sender.side_effect = failure
        else:
            env.sender.side_effect = None
            env.sender.return_value = SimpleNamespace(sent=False, route="pad_a", reason="failed")
        assert _observe(app)
        env.sender.assert_called_once()
        assert app._skip_last_press is None
        assert not any("전송됨 · 게임 수신/스킵 성공은 미확인" in str(call.args[0])
                       for call in app.queue_log.call_args_list)
        _assert_no_foreground_or_legacy_input(env)


def test_template_skip_without_ocr_or_pad_uses_posted_s_only():
    app = _app()
    with _environment(ocr=False, vg=False) as env:
        assert _observe(app)
        env.clock.now += 0.4
        assert _observe(app, 2)
        assert _routes(env) == ["posted_s", "posted_s"]
        env.classify.assert_not_called()
        _assert_no_foreground_or_legacy_input(env)


def test_plain_skip_requires_two_observations_then_start_not_tap():
    """맨 ▷ SKIP 은 두 번 확인한 뒤 START — S/A 탭으로 새면 안 된다(2026-10-12 회귀)."""
    app = _app()
    with _environment(a_hit=False, text=(True, None)) as env:
        assert _observe(app, 1)
        env.pad.assert_not_called()
        env.clock.now += 0.4
        assert _observe(app, 2)
        env.sender.assert_not_called()            # S/A 탭 경로 안 탐
        env.pad.assert_called_once_with(16)       # START
        env.key.assert_not_called()               # 전역 키보드 SendInput 없음
        env.win32.SetForegroundWindow.assert_not_called()
        env.win32.SetFocus.assert_not_called()
        env.candidate.assert_not_called()


def test_same_reverse_and_during_input_frames_cannot_retry():
    app = _app()
    with _environment() as env:
        def captured_during_input(_hwnd, **kwargs):
            app._frame_seq = 5
            return SimpleNamespace(sent=True, route=kwargs["route"], reason="sent")
        env.sender.side_effect = captured_during_input
        assert _observe(app, 1)
        env.clock.now += 1.0
        for seq in (1, 0, 4, 5):
            assert not _observe(app, seq)
        env.sender.assert_called_once()
        env.match_a.assert_called_once()
        assert _observe(app, 6)
        assert _routes(env) == ["pad_a", "posted_s"]


@pytest.mark.parametrize("first", ["template", "ocr"])
def test_template_and_ocr_share_the_same_cooldown(first):
    app = _app()
    manager = SimpleNamespace(hwnd=GAME_HWND)
    target = SimpleNamespace(name="target_E", key="s")
    with _environment() as env:
        app._frame_seq = 1
        if first == "template":
            assert app.dispatch_key_press(manager, target)
            env.clock.now += 0.05
            assert _observe(app, 2)
        else:
            assert _observe(app, 1)
            app._frame_seq = 2
            env.clock.now += 0.05
            app.dispatch_key_press(manager, target)
        env.sender.assert_called_once()
        _assert_no_foreground_or_legacy_input(env)


def test_non_skip_custom_a_stays_gamepad_confirm():
    app = _app()
    with _environment() as env:
        assert app.dispatch_key_press(
            SimpleNamespace(hwnd=GAME_HWND), SimpleNamespace(name="custom_confirm", key="a")
        )
        env.pad.assert_called_once_with(4096)
        env.sender.assert_not_called()
        env.key.assert_not_called()


@pytest.mark.parametrize("previous_hint", ["escape", "any_key", "start", "escape_highlight"])
def test_current_plain_skip_does_not_inherit_old_menu_hint(previous_hint):
    """에피소드에 남은 이전 메뉴 힌트가 있어도 맨 SKIP 은 S/A 탭으로 가지 않고 START 다.

    이전 화면의 힌트는 현재 맨 SKIP 으로 이월하지 않는다(09-07 규칙). 그래서 낡은
    escape_highlight 힌트도 START 를 막지 않는다 — 하이라이트 여부는 **현재 프레임**으로
    다시 판정한다(어두운 하이라이트 요약이면 제외: test_skip_anykey 의
    test_highlight_screen_stays_in_the_experiment_even_without_a_hint).
    """
    app = _app()
    app._skip_kind = "start"
    app._skip_generic_hint = previous_hint
    app._skip_generic_episode_hint = previous_hint
    with _environment(a_hit=False, text=(True, None)) as env:
        assert _observe(app, 1)
        env.clock.now += 0.4
        assert _observe(app, 2)
        env.sender.assert_not_called()
        env.key.assert_not_called()
        env.win32.SetForegroundWindow.assert_not_called()
        env.pad.assert_called_once_with(16)


@pytest.mark.parametrize("foreground", [GAME_HWND, OTHER_HWND])
def test_auto_exit_starting_during_recognition_blocks_skip_tap(foreground):
    app = _app()
    app._exit_gate = SimpleNamespace(is_set=lambda: True)
    with _environment(foreground=foreground) as env:
        assert _observe(app, 1)
        env.sender.assert_not_called()
        env.key.assert_not_called()
        env.pad.assert_not_called()
        assert app._skip_last_press is None


def test_cooldown_does_not_report_template_input_as_completed():
    app = _app()
    manager = SimpleNamespace(hwnd=GAME_HWND)
    target = SimpleNamespace(name="target_E", key="s")
    with _environment() as env:
        app._frame_seq = 1
        assert app.dispatch_key_press(manager, target)
        app._frame_seq = 2
        env.clock.now += 0.05
        assert not app.dispatch_key_press(manager, target)
        env.sender.assert_called_once()


def test_prompt_disappearance_clears_tap_episode_without_learning():
    app = _app()
    with _environment() as env:
        assert _observe(app, 1)
        env.clock.now += 0.4
        assert _observe(app, 2)
        assert _routes(env) == ["pad_a", "posted_s"]
        env.match_a.return_value = (False, 0.1, None)
        env.clock.now += 0.4
        assert not _observe(app, 3)
        assert app._skip_foreground_direct is False
        assert app._skip_foreground_policy.presses == 0
        assert app._skip_kind is None
        env.match_a.return_value = (True, 0.9, (10, 10))
        env.clock.now += 0.4
        assert _observe(app, 4)
        assert _routes(env) == ["pad_a", "posted_s", "pad_a"]
    app._report_skip_experiment_outcome.assert_not_called()


def test_explicit_escape_highlight_keeps_separate_start_route():
    app = _app()
    with _environment(a_hit=False, text=(True, "escape_highlight")) as env:
        for seq in range(1, config.SKIP_TEXT_CONSENSUS + 1):
            assert _observe(app, seq)
        env.pad.assert_called_once_with(16)
        env.sender.assert_not_called()
        env.key.assert_not_called()
    app._skip_generic_escape_highlight_experiment.choose.assert_not_called()


def test_cooldown_and_repeated_frame_do_not_advance_route_counter():
    app = _app()
    with _environment() as env:
        assert _observe(app, 1)
        assert app._skip_inactive_attempt == 1
        env.clock.now += 0.05
        assert not _observe(app, 1)
        assert _observe(app, 2)
        assert app._skip_inactive_attempt == 1
        env.clock.now += 0.4
        assert _observe(app, 3)
        assert app._skip_inactive_attempt == 2
        assert _routes(env) == ["pad_a", "posted_s"]


def test_sender_cancellation_observes_stop_and_exit_gate():
    app = _app()
    cancelled_checks = []
    stop = SimpleNamespace(value=False)
    gate = SimpleNamespace(value=False)
    app.stop_event = SimpleNamespace(is_set=lambda: stop.value)
    app._exit_gate = SimpleNamespace(is_set=lambda: gate.value)
    with _environment() as env:
        def run_sender(_hwnd, **kwargs):
            cancelled = kwargs["cancelled"]
            cancelled_checks.append(cancelled())
            stop.value = True
            cancelled_checks.append(cancelled())
            stop.value = False
            gate.value = True
            cancelled_checks.append(cancelled())
            return SimpleNamespace(sent=False, route=kwargs["route"], reason="cancelled")
        env.sender.side_effect = run_sender
        assert _observe(app, 1)
        # 호출부가 예외를 기록하고 삼키므로 콜백 밖에서 실제 관측을 검증한다.
        assert cancelled_checks == [False, True, True]
        _assert_no_foreground_or_legacy_input(env)


def test_busy_sender_lock_never_blocks_capture_or_advances_route():
    app = _app()
    occupied = SimpleNamespace(acquire=Mock(return_value=False), release=Mock())
    app._skip_tap_lock = occupied
    with _environment() as env:
        assert app._send_skip_tap(SimpleNamespace(hwnd=GAME_HWND), frame_seq=1) is None
        occupied.acquire.assert_called_once_with(blocking=False)
        occupied.release.assert_not_called()
        assert app._skip_inactive_attempt == 0
        env.sender.assert_not_called()
        _assert_no_foreground_or_legacy_input(env)


def test_missing_a_mapping_uses_posted_s_for_every_attempt():
    app = _app()
    with _environment() as env, patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {}, clear=True):
        for seq in (1, 2):
            assert _observe(app, seq)
            env.clock.now += 0.4
        assert _routes(env) == ["posted_s", "posted_s"]
        _assert_no_foreground_or_legacy_input(env)


def test_production_skip_source_never_calls_activation_or_global_keyboard_apis():
    import textwrap

    prohibited = {
        "SetForegroundWindow", "SetFocus", "SetActiveWindow", "AttachThreadInput",
        "SendInput", "send_scancode_key", "send_scancode_to_foreground_window",
        "bring_foreground", "_bring_to_foreground", "press_key_foreground",
        "spoof_window_active", "spoof_window_active_component", "spoof_directinput_app_active",
        "run_attached_to_window", "send_key_focus_attach", "send_key_focus_child",
    }
    for source in (
        inspect.getsource(gui.AutomationApp._send_skip_tap),
        inspect.getsource(skip_inactive),
    ):
        tree = ast.parse(textwrap.dedent(source))
        called = {
            item.func.attr if isinstance(item.func, ast.Attribute) else item.func.id
            for item in ast.walk(tree)
            if isinstance(item, ast.Call) and isinstance(item.func, (ast.Attribute, ast.Name))
        }
        assert not called.intersection(prohibited)



def test_highlight_classified_plain_skip_gets_start_in_background():
    """실제 경기 종료 ▷ SKIP(득점자 카드 화면)은 하이라이트로 판정된다(1.0.46 로그 6/6).

    2026-10-12 회귀: 1.0.48 과 첫 핫픽스(4141a3f)는 이 화면에서 배경 START 0회로 실험에
    갇혔다. 1.0.47 코드로 같은 조건을 돌리면 12초 동안 START 14회, 첫 START 약 1.2초였다.
    """
    app = _app()
    frame = np.full((720, 1280), 200, dtype=np.uint8)
    frame[int(720 * 0.12):int(720 * 0.74), int(1280 * 0.23):int(1280 * 0.77)] = 10
    assert app._is_highlight_summary_context(frame)
    with _environment(foreground=OTHER_HWND, a_hit=False, text=(True, None)) as env:
        first = None
        for seq in range(1, 31):              # 0.4초 간격 12초
            app._frame_seq = seq
            app._try_skip(frame, SimpleNamespace(hwnd=GAME_HWND), frame_seq=seq)
            if first is None and any(c.args and c.args[0] == 16 for c in env.pad.call_args_list):
                first = seq
            env.clock.now += 0.4
        starts = sum(1 for c in env.pad.call_args_list if c.args and c.args[0] == 16)
        assert first is not None and first <= 3, f"첫 START 가 늦거나 없다: {first}"
        assert starts >= 8, f"12초 동안 START {starts}회 — 재누름이 끊겼다"
        env.sender.assert_not_called()        # S/A 탭으로 새지 않음
        env.key.assert_not_called()           # 전역 키보드 입력 없음
        env.win32.SetForegroundWindow.assert_not_called()
