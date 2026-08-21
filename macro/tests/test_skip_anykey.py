"""'SKIP 하려면 아무 키나 누르세요' 프롬프트 — 실험 없이 바로 START.

고정하는 것:
  1) 판정(연속 확인 → 누름 → 재누름 간격)은 순수 로직(skip_anykey)이다.
  2) OCR 워커(_try_skip)가 any_key 힌트를 보면 실험 추적기(choose/3초 대조)를 건드리지
     않고 START 를 누른다 — 그리고 프롬프트가 사라지면 상태를 깨끗이 비운다.
  3) 에피소드에 이미 잠긴 다른 힌트(escape 등)는 존중한다(실험 귀속 불변식).
  4) 플래그를 끄면 예전 흐름(실험)으로 돌아간다.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from macroapp import config, gui
from macroapp.skip_anykey import (
    ACTION_HOLD,
    ACTION_IDLE,
    ACTION_PRESS,
    ACTION_WAIT,
    AnyKeyStartPolicy,
)


# ─── 순수 판정 ─────────────────────────────────────────────────────────────


def test_policy_requires_consensus_then_presses_once_per_interval() -> None:
    policy = AnyKeyStartPolicy(consensus=2, repress_seconds=0.8)
    assert policy.observe(10.0, True) == ACTION_WAIT      # 1회째 — 아직
    assert policy.observe(10.3, True) == ACTION_PRESS     # 2회째 — 누름
    assert policy.observe(10.6, True) == ACTION_HOLD      # 0.3초 뒤 — 간격 안
    assert policy.observe(11.2, True) == ACTION_PRESS     # 0.8초 지남 — 다시 누름
    assert policy.observe(11.5, True) == ACTION_HOLD


def test_policy_absence_resets_streak_but_keeps_last_press() -> None:
    policy = AnyKeyStartPolicy(consensus=2, repress_seconds=0.8)
    policy.observe(10.0, True)
    assert policy.observe(10.3, True) == ACTION_PRESS
    assert policy.observe(10.6, False) == ACTION_IDLE
    assert policy.streak == 0
    # 바로 다음 에피소드: 연속 확인은 처음부터, 누름 간격은 이어진다(이중 펄스 방지).
    assert policy.observe(10.7, True) == ACTION_WAIT
    assert policy.observe(10.9, True) == ACTION_HOLD      # 마지막 누름(10.3)에서 0.6초
    assert policy.observe(11.2, True) == ACTION_PRESS


def test_policy_reset_clears_everything() -> None:
    policy = AnyKeyStartPolicy(consensus=2, repress_seconds=0.8)
    policy.observe(10.0, True)
    policy.observe(10.3, True)
    policy.reset()
    assert policy.streak == 0
    assert policy.last_press_at == -math.inf
    assert policy.observe(10.4, True) == ACTION_WAIT
    assert policy.observe(10.5, True) == ACTION_PRESS


def test_config_pins() -> None:
    assert config.SKIP_ANYKEY_DIRECT_START is True
    assert 0.5 <= config.SKIP_ANYKEY_REPRESS_SECONDS <= 1.5
    assert config.SKIP_TEXT_CONSENSUS >= 2


# ─── _try_skip 배선 ─────────────────────────────────────────────────────────


def _tracker() -> SimpleNamespace:
    return SimpleNamespace(
        episode_control_seconds=3.0,
        pending=None,
        choose=Mock(return_value=(None, None)),
        reset_episode=Mock(),
    )


def _app() -> gui.AutomationApp:
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app.base_dir = SimpleNamespace()
    app._skip_seen_since = None
    app._skip_text_streak = 0
    app._skip_s_match_streak = 0
    app._skip_kind = None
    app._skip_prompt_variant = None
    app._skip_generic_hint = None
    app._skip_generic_episode_hint = None
    app._skip_prompt_center = None
    app._skip_prompt_visual = None
    app._skip_last_press = None
    app._skip_a_learned = None
    app._skip_a_sweep_idx = 0
    app._last_normal_action_at = float("-inf")
    app._skip_esc_probe_negative_streak = 0
    app._skip_esc_probe_allow_once = False
    app._skip_precontrol_contaminated = False
    app._skip_control_contaminated = False
    app._skip_a_dumped = True
    app._skip_diag_count = 20
    app._skip_active_until = 0.0
    app._skip_experiment = _tracker()
    app._skip_s_experiment = _tracker()
    app._skip_generic_experiment = _tracker()
    app._skip_generic_any_key_experiment = _tracker()
    app._skip_generic_escape_experiment = _tracker()
    app._skip_generic_escape_highlight_experiment = _tracker()
    app._skip_anykey_policy = AnyKeyStartPolicy(
        consensus=config.SKIP_TEXT_CONSENSUS,
        repress_seconds=config.SKIP_ANYKEY_REPRESS_SECONDS,
    )
    app._capture_skip_prompt_visual = Mock()
    app._report_skip_experiment_outcome = Mock()
    app._reconcile_skip_learning = Mock()
    app.queue_status = Mock()
    app.queue_log = Mock()
    return app


def _patches(classify_side_effect):
    return (
        patch.object(gui.winapi, "vg", object()),
        patch.object(gui.rank_ocr, "ocr_available", return_value=True),
        patch.object(gui.rank_ocr, "match_skip_a", return_value=(False, 0.1, None)),
        patch.object(gui.rank_ocr, "match_skip_s", return_value=(False, 0.1, None)),
        patch.object(
            gui.rank_ocr, "classify_skip_prompt", side_effect=classify_side_effect
        ),
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": 16}, clear=False),
        patch.object(gui, "send_gamepad_button", return_value=True),
    )


def test_any_key_prompt_presses_start_without_touching_the_experiment() -> None:
    app = _app()
    manager = SimpleNamespace(hwnd=123)
    screen = np.zeros((720, 1280), dtype=np.uint8)
    p = _patches([(True, "any_key")] * 3)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6] as gamepad:
        assert app._try_skip(screen, manager)      # 1회째 — 연속 확인 대기
        gamepad.assert_not_called()
        assert app._try_skip(screen, manager)      # 2회째 — START
        gamepad.assert_called_once_with(16)
        assert app._try_skip(screen, manager)      # 0.8초 안 — 재누름 없음
        gamepad.assert_called_once()

    assert app._skip_kind == "anykey"
    assert app._skip_generic_episode_hint == "any_key"
    assert app._skip_active_until > 0.0
    # 실험 추적기는 손대지 않는다 — 이 화면은 표본이 아니다.
    for tracker in (
        app._skip_generic_experiment,
        app._skip_generic_any_key_experiment,
        app._skip_generic_escape_experiment,
    ):
        tracker.choose.assert_not_called()
    app._report_skip_experiment_outcome.assert_not_called()
    assert any(
        "아무 키나" in str(call.args[0]) for call in app.queue_log.call_args_list
    )


def test_any_key_prompt_represses_after_interval_and_resets_when_gone() -> None:
    app = _app()
    manager = SimpleNamespace(hwnd=123)
    screen = np.zeros((720, 1280), dtype=np.uint8)
    p = _patches([(True, "any_key")] * 3 + [(False, None)] * 2)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6] as gamepad:
        app._try_skip(screen, manager)
        app._try_skip(screen, manager)
        gamepad.assert_called_once()
        # 간격이 지난 것처럼 — 마지막 누름 시각을 되돌린다.
        app._skip_anykey_policy.last_press_at -= config.SKIP_ANYKEY_REPRESS_SECONDS
        app._try_skip(screen, manager)
        assert gamepad.call_count == 2
        # 프롬프트가 사라짐 — 한 프레임은 구멍으로 보고 유지, 두 번째에 에피소드 종료.
        assert app._try_skip(screen, manager)
        assert app._try_skip(screen, manager) is False

    assert app._skip_kind is None
    assert app._skip_generic_episode_hint is None
    assert app._skip_anykey_policy.streak == 0
    assert app._skip_seen_since is None


def test_locked_escape_episode_is_not_hijacked_by_a_later_any_key_read() -> None:
    """이미 escape 로 잠긴 에피소드(실험 진행 중)는 any_key 가 읽혀도 그대로 둔다."""

    app = _app()
    app._skip_kind = "start"
    app._skip_generic_episode_hint = "escape"
    app._skip_generic_hint = "escape"
    assert app._anykey_direct_start(1.0, True, False, False) is False
    assert app._skip_kind == "start"


def test_disabled_flag_falls_back_to_the_experiment_path() -> None:
    app = _app()
    with patch.object(gui, "SKIP_ANYKEY_DIRECT_START", False):
        app._skip_generic_hint = "any_key"
        assert app._anykey_direct_start(1.0, True, False, False) is False
    assert app._skip_kind is None


def test_a_or_s_template_match_never_takes_the_direct_path() -> None:
    app = _app()
    app._skip_generic_hint = "any_key"
    assert app._anykey_direct_start(1.0, True, True, False) is False
    assert app._anykey_direct_start(1.0, True, False, True) is False
    assert app._anykey_direct_start(1.0, False, False, False) is False
