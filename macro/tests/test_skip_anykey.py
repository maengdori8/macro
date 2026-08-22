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
import time
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
    app.stop_event = SimpleNamespace(is_set=lambda: False)
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
        "일반 프롬프트" in str(call.args[0]) for call in app.queue_log.call_args_list
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


def test_escape_prompt_also_goes_direct_not_into_the_experiment() -> None:
    """▷ SKIP(escape 형)도 답이 START 로 확정된 프롬프트다.

    2026-08-22 실측 결함: 템플릿이 START 를 한 번 누른 직후 OCR 이 같은 프롬프트를 실험
    에피소드로 가져가 매칭을 5.2초씩 봉쇄해 템플릿의 재시도를 굶겼다(8초 초과 6건이 전부
    이 모양). 이제 escape/escape_highlight/start 도 직행 경로가 받는다.
    """

    for hint in ("escape", "start"):
        app = _app()
        manager = SimpleNamespace(hwnd=123)
        screen = np.zeros((720, 1280), dtype=np.uint8)
        p = _patches([(True, hint)] * 2)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6] as gamepad:
            assert app._try_skip(screen, manager)
            assert app._try_skip(screen, manager)
        assert gamepad.call_count == 1, f"{hint}: 직행 START 가 안 나갔다"
        assert app._skip_kind == "anykey", f"{hint}: 실험 경로로 샜다"
        # 실험 추적기는 손대지 않는다(봉쇄 5.2초의 출처).
        for tracker in (
            app._skip_generic_experiment,
            app._skip_generic_escape_experiment,
        ):
            tracker.choose.assert_not_called()
        # 봉쇄는 0.5초뿐이라 템플릿이 곧바로 재시도할 수 있다.
        assert app._skip_active_until - time.monotonic() <= 0.6


def test_a_and_s_prompts_still_go_to_the_experiment() -> None:
    """A/S(hold-to-skip)는 답이 없는 프롬프트라 실험이 계속 돌아야 한다."""

    app = _app()
    app._skip_generic_hint = "escape"
    # is_a / is_s 가 서면 직행 경로는 물러난다.
    assert app._anykey_direct_start(1.0, True, True, False) is False
    assert app._anykey_direct_start(1.0, True, False, True) is False


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


# ─── 2026-08-22 리뷰 확정: 맨 '▷ SKIP'(힌트 미상)이 직행에서 빠져 있던 결함 ──────────


def test_plain_skip_prompt_without_any_hint_goes_direct() -> None:
    """맨 '▷ SKIP' — OCR 이 "skip" 만 읽어 힌트가 None 인 형태.

    실전 원장에서 generic 에피소드의 44%(104/236)가 이것이고, 증거 이미지 40장을 다시
    돌려 보니 classify=(True,None)·A/S/F/G 템플릿 전부 미매칭이었다. 이게 직행에서
    빠져 있으면 정작 구매자가 보고한 화면이 5.2초 봉쇄 실험에 그대로 남는다.
    """

    app = _app()
    manager = SimpleNamespace(hwnd=123)
    screen = np.zeros((720, 1280), dtype=np.uint8)
    p = _patches([(True, None)] * 2)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6] as gamepad:
        assert app._try_skip(screen, manager)
        assert app._try_skip(screen, manager)
    assert gamepad.call_count == 1, "맨 ▷ SKIP 이 직행 START 를 못 받았다"
    assert app._skip_kind == "anykey", "맨 ▷ SKIP 이 실험 경로로 샜다"
    app._skip_generic_experiment.choose.assert_not_called()
    # 봉쇄는 0.5초 — 템플릿이 곧바로 재시도할 수 있어야 한다.
    assert app._skip_active_until - time.monotonic() <= 0.6


def test_fallback_locked_episode_is_reclaimed_by_direct() -> None:
    """증거 없이 'start' 폴백으로 잠긴 에피소드는 직행이 되찾는다(영구 배제 방지)."""

    app = _app()
    app._skip_kind = "start"
    app._skip_generic_episode_hint = "start"
    app._skip_generic_hint = "start"
    assert app._anykey_direct_start(1.0, True, False, False) is True
    assert app._skip_kind == "anykey"
    app._skip_generic_experiment.reset_episode.assert_called()


def test_evidenced_escape_highlight_lock_is_respected() -> None:
    """증거로 잠긴 하이라이트 에피소드는 직행이 가로채지 않는다(실험 유지)."""

    app = _app()
    app._skip_kind = "start"
    app._skip_generic_episode_hint = "escape_highlight"
    app._skip_generic_hint = "escape_highlight"
    assert app._anykey_direct_start(1.0, True, False, False) is False
    assert app._skip_kind == "start"


def test_highlight_screen_stays_in_the_experiment_even_without_a_hint() -> None:
    """경기 후 하이라이트는 맨 START 가 통한다는 증거가 없다 — 힌트 None 이어도 제외."""

    app = _app()
    # 실측 기준: 중앙 ROI 의 어두운 픽셀 비율 0.70~0.95 가 하이라이트 요약 화면이다
    # (완전 검정 1.00 은 캡처 실패/로딩이라 제외된다).
    dark = np.full((720, 1280), 200, dtype=np.uint8)
    dark[int(720 * 0.12):int(720 * 0.74), int(1280 * 0.23):int(1280 * 0.77)] = 10
    assert app._direct_start_form(None, dark) is None
    bright = np.full((720, 1280), 200, dtype=np.uint8)
    assert app._direct_start_form(None, bright) == "plain"
    assert app._direct_start_form("escape_highlight", bright) is None


def test_direct_start_sends_the_activation_spoof_like_the_template_path() -> None:
    """템플릿 경로와 같은 입력이어야 한다 — WGC + WM_ACTIVATE 가짜 포커스 + vgamepad."""

    app = _app()
    manager = SimpleNamespace(hwnd=4242)
    posted = []
    fake = SimpleNamespace(PostMessage=lambda h, m, w, l: posted.append((h, m, w, l)))
    p = _patches([(True, "escape")] * 2)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6] as gamepad,             patch.object(gui.winapi, "win32gui", fake):
        app._try_skip(np.zeros((720, 1280), dtype=np.uint8), manager)
        app._try_skip(np.zeros((720, 1280), dtype=np.uint8), manager)
    assert gamepad.call_count == 1
    assert posted == [(4242, 0x0006, 1, 0)], f"활성 스푸핑이 안 나갔다: {posted}"


def test_direct_start_does_not_fire_after_stop() -> None:
    """'정지 후 입력 0' 불변식 — 정지가 눌렸으면 START 를 보내지 않는다."""

    app = _app()
    app.stop_event = SimpleNamespace(is_set=lambda: True)
    manager = SimpleNamespace(hwnd=123)
    p = _patches([(True, "escape")] * 2)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6] as gamepad:
        app._try_skip(np.zeros((720, 1280), dtype=np.uint8), manager)
        app._try_skip(np.zeros((720, 1280), dtype=np.uint8), manager)
    gamepad.assert_not_called()
