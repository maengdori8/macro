"""전면 직행이 _try_skip 에 실제로 배선돼 있다 — 배선 누락 재발 방지.

## 고정하는 계약

1. 게임 창이 **전면**이고 컷신(A/S) 프롬프트가 보이면, 실험 추적기를 건드리지 않고
   가상패드 A 를 바로 누른다. (실험 가드가 이 경우를 통째로
   `deferred(target_already_foreground)` 로 버려 입력이 0이던 것이 원인이었다 —
   2026-08-23 에는 한 프롬프트가 116초 동안 입력 0회였다.)
2. 게임이 **배경**이면 전면 직행은 발동하지 않고 기존 실험 경로가 그대로 돈다.
3. 프롬프트가 오래 버티면 키보드 S 스캔코드까지 같이 나간다.
4. 전면 직행 에피소드는 실험 봉쇄(5.2초)를 걸지 않는다 — 재누름이 곧바로 돌아야 한다.
5. 정지 뒤에는 입력이 나가지 않는다.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from macroapp import config, gui
from macroapp.skip_anykey import AnyKeyStartPolicy
from macroapp.skip_foreground import ForegroundSkipPolicy

GAME_HWND = 4321


def _tracker() -> SimpleNamespace:
    return SimpleNamespace(
        episode_control_seconds=3.0,
        pending=None,
        choose=Mock(return_value=(None, None)),
        reset_episode=Mock(),
        record_attempt=Mock(),
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
    # 재누름 간격 0 — 매 패스마다 눌러 배선을 관찰할 수 있게 한다.
    app._skip_foreground_policy = ForegroundSkipPolicy(
        repress_seconds=0.0,
        key_escalation_seconds=config.SKIP_FOREGROUND_KEY_AFTER_SECONDS,
        max_presses=config.SKIP_FOREGROUND_MAX_PRESSES,
    )
    app._skip_foreground_direct = False
    app._capture_skip_prompt_visual = Mock()
    app._report_skip_experiment_outcome = Mock()
    app._reconcile_skip_learning = Mock()
    app.queue_status = Mock()
    app.queue_log = Mock()
    app.stop_event = SimpleNamespace(is_set=lambda: False)
    return app


def _patches(*, foreground_hwnd: int, a_hit: bool = True):
    """A 템플릿이 맞은 컷신 프롬프트 + 지정한 포그라운드 창."""
    return (
        patch.object(gui.winapi, "vg", object()),
        patch.object(gui.rank_ocr, "ocr_available", return_value=True),
        patch.object(
            gui.rank_ocr, "match_skip_a", return_value=(a_hit, 0.9, (10, 10))
        ),
        patch.object(gui.rank_ocr, "match_skip_s", return_value=(False, 0.1, None)),
        patch.object(
            gui.rank_ocr, "classify_skip_prompt", return_value=(False, None)
        ),
        patch.dict(
            gui.input_gamepad.KEY_TO_GAMEPAD, {"a": 4096, "start": 16}, clear=False
        ),
        patch.object(gui, "send_gamepad_button", return_value=True),
        patch.object(
            gui.input_message, "get_foreground_hwnd", return_value=foreground_hwnd
        ),
        patch.object(
            gui.input_message,
            "send_scancode_to_foreground_window",
            return_value=True,
        ),
    )


def _screen() -> np.ndarray:
    return np.zeros((720, 1280), dtype=np.uint8)


def test_게임이_전면이면_실험_없이_A_를_바로_누른다() -> None:
    app = _app()
    manager = SimpleNamespace(hwnd=GAME_HWND)
    p = _patches(foreground_hwnd=GAME_HWND)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6] as gamepad, p[7], p[8]:
        assert app._try_skip(_screen(), manager) is True
        gamepad.assert_called_once_with(4096, config.SKIP_A_TAP_SECONDS)

    # 실험 추적기의 후보 선택은 절대 돌지 않는다 — 이 화면은 표본이 아니다.
    app._skip_experiment.choose.assert_not_called()
    app._skip_s_experiment.choose.assert_not_called()
    app._report_skip_experiment_outcome.assert_not_called()
    assert app._skip_foreground_direct is True
    assert app._skip_kind == "a"
    assert any(
        "[SKIP 직행] 전면" in str(call.args[0])
        for call in app.queue_log.call_args_list
    )


def test_전면_직행은_긴_봉쇄를_걸지_않는다() -> None:
    """실험 경로는 5.2초를 봉쇄한다. 직행은 재누름이 곧바로 돌아야 한다."""
    app = _app()
    manager = SimpleNamespace(hwnd=GAME_HWND)
    p = _patches(foreground_hwnd=GAME_HWND)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8]:
        app._try_skip(_screen(), manager)
    assert app._skip_active_until - time.monotonic() <= 0.5


def test_실험_몫_에피소드는_배경에서_기존_실험_경로가_돈다() -> None:
    """N 번에 1번은 실험에 양보한다 — 배경 전달 가설을 계속 검증해야 하기 때문."""
    app = _app()
    # 다음 에피소드가 실험 차례가 되도록 일련번호를 맞춘다.
    app._skip_ap_episode_index = config.SKIP_S_EXPERIMENT_EVERY - 1
    manager = SimpleNamespace(hwnd=GAME_HWND)
    p = _patches(foreground_hwnd=999999)   # 다른 창이 전면
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6] as gamepad, p[7], p[8]:
        assert app._try_skip(_screen(), manager) is True
        gamepad.assert_not_called()

    assert app._skip_foreground_direct is False
    assert app._skip_episode_is_direct is False
    app._skip_experiment.choose.assert_called_once()


def test_직행_몫_에피소드는_배경에서도_A_를_누른다() -> None:
    """실험 봉쇄가 작동하는 경로(target_E)까지 굶기던 것이 근본 문제였다."""
    app = _app()
    manager = SimpleNamespace(hwnd=GAME_HWND)
    p = _patches(foreground_hwnd=999999)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6] as gamepad, p[7], p[8] as send_key:
        assert app._try_skip(_screen(), manager) is True
        gamepad.assert_called_once_with(4096, config.SKIP_A_TAP_SECONDS)
        # ⚠️ 배경에서는 키보드 S(SendInput)를 절대 보내지 않는다 — 다른 앱 유출.
        send_key.assert_not_called()

    assert app._skip_episode_is_direct is True
    app._skip_experiment.choose.assert_not_called()
    assert any(
        "[SKIP 직행] 배경" in str(call.args[0])
        for call in app.queue_log.call_args_list
    )


def test_배경에서_오래_버텨도_키보드_S_는_안_나간다() -> None:
    app = _app()
    manager = SimpleNamespace(hwnd=GAME_HWND)
    p = _patches(foreground_hwnd=999999)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8] as send_key:
        app._try_skip(_screen(), manager)
        app._skip_foreground_policy.first_seen_at = (
            time.monotonic() - config.SKIP_FOREGROUND_HOLD_AFTER_SECONDS - 1.0
        )
        app._try_skip(_screen(), manager)
        send_key.assert_not_called()


def test_전면이면_첫_발부터_키보드_S_를_같이_보낸다() -> None:
    """2026-07-02 실측: 전면에서 통한 유일한 입력이 키보드 s '탭' 이었다.

    패드 A 는 "탭·1s 홀드 모두 무시" 로 기록돼 있다. 그러니 키보드 S 를 뒤로 미루면
    안 된다 — 첫 발에 둘 다 보낸다.
    """
    app = _app()
    manager = SimpleNamespace(hwnd=GAME_HWND)
    p = _patches(foreground_hwnd=GAME_HWND)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6] as gamepad, p[7], p[8] as send_key:
        app._try_skip(_screen(), manager)
        gamepad.assert_called_once_with(4096, config.SKIP_A_TAP_SECONDS)
        send_key.assert_called_once()
        assert send_key.call_args.args[0] == GAME_HWND
        assert send_key.call_args.args[1] == gui.input_message.SCAN_S


def test_정지_뒤에는_입력이_나가지_않는다() -> None:
    app = _app()
    app.stop_event = SimpleNamespace(is_set=lambda: True)
    manager = SimpleNamespace(hwnd=GAME_HWND)
    p = _patches(foreground_hwnd=GAME_HWND)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6] as gamepad, p[7], p[8] as send_key:
        assert app._try_skip(_screen(), manager) is True
        gamepad.assert_not_called()
        send_key.assert_not_called()


def test_프롬프트가_사라지면_직행_상태가_정리된다() -> None:
    app = _app()
    manager = SimpleNamespace(hwnd=GAME_HWND)
    p = _patches(foreground_hwnd=GAME_HWND)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8]:
        app._try_skip(_screen(), manager)
        assert app._skip_foreground_direct is True

    gone = _patches(foreground_hwnd=GAME_HWND, a_hit=False)
    with gone[0], gone[1], gone[2], gone[3], gone[4], gone[5], gone[6], gone[7], gone[8]:
        assert app._try_skip(_screen(), manager) is False

    assert app._skip_foreground_direct is False
    assert app._skip_foreground_policy.presses == 0
    assert app._skip_kind is None
    # 전면 입력이 배경 후보의 성공으로 귀속되면 안 된다.
    app._report_skip_experiment_outcome.assert_not_called()
    assert any(
        "[SKIP 직행] 소멸" in str(call.args[0])
        for call in app.queue_log.call_args_list
    )


def test_설정_기본값_고정() -> None:
    assert config.SKIP_FOREGROUND_DIRECT is True
    assert 0.1 <= config.SKIP_FOREGROUND_REPRESS_SECONDS <= 1.0
    # 0.0 = 첫 발부터 키보드 S. 이 값을 올리면 07-02 실측이 "통한다"고 기록한
    # 유일한 입력을 다시 뒤로 미루는 것이라 회귀다.
    assert config.SKIP_FOREGROUND_KEY_AFTER_SECONDS == 0.0
    assert config.SKIP_FOREGROUND_MAX_PRESSES >= 60
    assert config.SKIP_FOREGROUND_MAX_EPISODE_SECONDS >= 40.0
    assert 0 < config.SKIP_S_EXPERIMENT_EVERY <= 20


def test_일반_프롬프트도_전면이면_START_직행() -> None:
    """같은 굶주림이 일반(하이라이트) 프롬프트에도 있었다 — 전면이면 바로 누른다."""
    app = _app()
    manager = SimpleNamespace(hwnd=GAME_HWND)
    screen = np.zeros((720, 1280), dtype=np.uint8)
    with patch.object(gui.winapi, "vg", object()), \
        patch.object(gui.rank_ocr, "ocr_available", return_value=True), \
        patch.object(gui.rank_ocr, "match_skip_a", return_value=(False, 0.1, None)), \
        patch.object(gui.rank_ocr, "match_skip_s", return_value=(False, 0.1, None)), \
        patch.object(
            gui.rank_ocr, "classify_skip_prompt", return_value=(True, "escape_highlight")
        ), \
        patch.dict(
            gui.input_gamepad.KEY_TO_GAMEPAD, {"a": 4096, "start": 16}, clear=False
        ), \
        patch.object(gui, "send_gamepad_button", return_value=True) as gamepad, \
        patch.object(
            gui.input_message, "get_foreground_hwnd", return_value=GAME_HWND
        ):
        for _ in range(config.SKIP_TEXT_CONSENSUS):
            assert app._try_skip(screen, manager) is True

    gamepad.assert_called_once_with(16)
    # 실험 후보 선택은 돌지 않는다 — 전면이면 어차피 deferred 였다.
    app._skip_generic_escape_highlight_experiment.choose.assert_not_called()
    assert any(
        "[SKIP 전면] 게임이 전면 → 일반 프롬프트" in str(call.args[0])
        for call in app.queue_log.call_args_list
    )


def test_전면에서_누른_뒤_배경이_돼도_실험에_넘기지_않는다() -> None:
    """전면 입력으로 사라진 컷신이 배경 후보의 '성공'으로 귀속되면 가짜 승자를 학습한다."""
    app = _app()
    manager = SimpleNamespace(hwnd=GAME_HWND)
    p = _patches(foreground_hwnd=GAME_HWND)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8]:
        app._try_skip(_screen(), manager)
    assert app._skip_foreground_direct is True

    # 컷신 도중 사용자가 다른 창을 눌러 게임이 배경이 됐다.
    app._skip_episode_is_direct = False      # 실험 몫이었다고 가정(최악의 경우)
    bg = _patches(foreground_hwnd=999999)
    with bg[0], bg[1], bg[2], bg[3], bg[4], bg[5], bg[6] as gamepad, bg[7], bg[8]:
        assert app._try_skip(_screen(), manager) is True
        gamepad.assert_not_called()          # 실험 몫이라 배경에서는 안 누른다

    # 이 에피소드는 끝까지 직행 소유 — 실험이 후보를 고르지 않는다.
    # (전면에서 누른 A 로 사라진 컷신이 배경 후보의 '성공' 으로 귀속되면 가짜 승자를
    #  학습한다.)
    app._skip_experiment.choose.assert_not_called()
    app._skip_s_experiment.choose.assert_not_called()


def test_아주_오래_버티면_홀드로_승격한다() -> None:
    app = _app()
    manager = SimpleNamespace(hwnd=GAME_HWND)
    p = _patches(foreground_hwnd=GAME_HWND)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6] as gamepad, p[7], p[8] as send_key:
        app._try_skip(_screen(), manager)
        # 홀드 승격 시각을 넘기고, 저속 재누름 간격도 지난 상태로 만든다.
        policy = app._skip_foreground_policy
        policy.first_seen_at = (
            time.monotonic() - config.SKIP_FOREGROUND_HOLD_AFTER_SECONDS - 5.0
        )
        policy.last_press_at = (
            time.monotonic() - config.SKIP_FOREGROUND_SLOW_REPRESS_SECONDS - 1.0
        )
        app._try_skip(_screen(), manager)

    # 마지막 패드 입력은 탭이 아니라 홀드 길이여야 한다.
    assert gamepad.call_args.args[1] == config.SKIP_FOREGROUND_HOLD_SECONDS
    assert send_key.call_args.args[2] == config.SKIP_FOREGROUND_HOLD_SECONDS


def test_보류된_패스는_5초_봉쇄를_걸지_않는다() -> None:
    """입력이 0인 패스에서 5.2초를 얹으면 답을 아는 템플릿 경로까지 굶는다.

    실측 2026-08-23: 15:06:26~15:08:12 **106초 동안 타겟 감지 0건**. 봉쇄가 풀린
    순간 target_E([S] 키캡 → 패드 A)가 눌러 프롬프트가 끝났다.
    """
    from macroapp.skip_experiment import GuardedActionResult, SkipOutcome

    app = _app()
    app._skip_ap_episode_index = config.SKIP_S_EXPERIMENT_EVERY - 1  # 실험 몫
    manager = SimpleNamespace(hwnd=GAME_HWND)
    deferred = GuardedActionResult(
        attempted=False, action_ok=False, foreground_before=GAME_HWND,
        foreground_after=GAME_HWND, foreground_samples=(GAME_HWND,),
        invariant_ok=False, reason="target_already_foreground", elapsed_seconds=0.0,
    )
    app._skip_s_experiment.choose = Mock(return_value=("attach_char_s", None))
    app._skip_s_experiment.record_attempt = Mock(
        return_value=SkipOutcome("deferred", "attach_char_s", detail="target_already_foreground")
    )
    app._skip_experiment.choose = Mock(return_value=("attach_char_s", None))
    app._skip_experiment.record_attempt = app._skip_s_experiment.record_attempt

    p = _patches(foreground_hwnd=999999)
    with p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7], p[8], \
        patch.object(gui, "run_guarded_inactive_action", return_value=deferred):
        assert app._try_skip(_screen(), manager) is True

    remaining = app._skip_active_until - time.monotonic()
    assert remaining <= 1.0, (
        f"보류 패스가 {remaining:.2f}초를 봉쇄했다 — 템플릿 재시도가 굶는다"
    )
