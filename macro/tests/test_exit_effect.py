"""자동 종료 '실효' 측정 — 성공을 '완료 메시지'가 아니라 **다음 로비 도달**로 잰다.

근거(2026-08-22 프로 로그): 03:55:46 에 '[종료] 완료' 가 찍힌 뒤에도 04:00:11 경기후
프롬프트까지 경기가 4분 이상 계속됐다. 러너의 '완료' 는 '시도가 끝났다'는 뜻일 뿐이고,
마무리 확인 클릭이 빗나가면 판은 그대로 돈다. 로비 진입 타겟을 다시 누르는 순간이
'판이 진짜 끝났다'는 관측 가능한 유일한 증거다.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from macroapp import config, gui


def _app() -> gui.AutomationApp:
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app._auto_exit_done_at = None
    app._log_to_file_only = Mock()
    app.queue_log = Mock()
    return app


def _target(name: str):
    return SimpleNamespace(name=name)


def test_config_pins() -> None:
    assert "target_D" in config.EXIT_EFFECT_LOBBY_TARGETS
    assert config.EXIT_EFFECT_FAST_SECONDS >= 30.0


def test_no_measurement_when_no_auto_exit_ran() -> None:
    app = _app()
    app._note_exit_effect(_target("target_D"), 100.0)
    app._log_to_file_only.assert_not_called()
    app.queue_log.assert_not_called()


def test_fast_return_to_lobby_is_logged_quietly() -> None:
    app = _app()
    app._auto_exit_done_at = 100.0
    app._note_exit_effect(_target("target_D"), 120.0)      # 20초 — 정상
    assert app._auto_exit_done_at is None, "측정은 한 번만"
    text = str(app._log_to_file_only.call_args.args[0])
    assert "20초" in text and "즉시" in text
    # 정상인데 사용자 로그를 어지럽히면 안 된다.
    app.queue_log.assert_not_called()


def test_slow_return_warns_the_user() -> None:
    """느리면 마무리가 빗나가 경기가 계속 돌았다는 뜻 — 재시도 설계의 표적이다."""

    app = _app()
    app._auto_exit_done_at = 100.0
    app._note_exit_effect(_target("target_D"), 100.0 + 260.0)   # 4분 20초(8-22 실측 유형)
    assert "지연" in str(app._log_to_file_only.call_args.args[0])
    warned = str(app.queue_log.call_args.args[0])
    assert "260초" in warned and "마무리" in warned


def test_non_lobby_targets_do_not_end_the_measurement() -> None:
    app = _app()
    app._auto_exit_done_at = 100.0
    for name in ("target_H", "target_F", "target_J"):
        app._note_exit_effect(_target(name), 110.0)
    assert app._auto_exit_done_at == 100.0, "로비 타겟이 아닌데 측정이 끝났다"
    app._log_to_file_only.assert_not_called()
    # 진짜 로비 타겟에서만 끝난다.
    app._note_exit_effect(_target("target_B"), 130.0)
    assert app._auto_exit_done_at is None


# ─── 재시도 홀드 상승 + 로비 미도달 재시도 (2026-08-23 실측 기반) ──────────────
#
# 8-22 프로 로그 자동 종료 8건 전수 추적:
#   성공 5건 = 마무리 15~20초, 로비까지 16~37초
#   실패 3건 = 마무리 50초(홀드10+타임아웃40) 뒤 '게임 화면을 확인해 주세요',
#              경기는 249·391·598초 더 진행([7]은 완료 직후 target_J 전술창=인게임)
# → 실패 원인은 '확인 버튼을 못 눌러서'가 아니라 **정지 10초로 접속이 안 끊겨 확인 창이
#   안 뜬 것**. 그래서 (a) 재시도는 홀드를 늘려서 하고 (b) 스코어를 못 읽어도
#   '로비 미도달'만으로 재시도가 걸려야 한다([4]는 재시도가 아예 안 걸렸다).


def config_rules():
    from macroapp import auto_exit

    return auto_exit.ExitRules(
        base_deficit=2, hard_deficit=3, late_minute=70, late_deficit=1
    )


def _tick_app(latched: bool = True):
    """latched=True 는 '아직 같은 판' — 재시도가 겨냥하는 유일한 상태."""
    from types import SimpleNamespace
    from unittest.mock import Mock

    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app._auto_exit_done_at = None
    app._auto_exit_retries = 0
    app._auto_exit_retry_at = None
    app.stop_event = SimpleNamespace(is_set=lambda: False)
    app._loss_tracker = SimpleNamespace(
        latched=latched,
        rules=config_rules(),
    )
    app._last_match_score = None          # 못 읽는 상태 = 이 경로의 기본 전제
    app._clock_tracker = SimpleNamespace(confirmed=None)
    app._log_to_file_only = Mock()
    app.queue_log = Mock()
    app.ui_queue = SimpleNamespace(put=Mock())
    return app


def test_blind_retry_never_attacks_a_new_match() -> None:
    """로비 타겟을 '놓친' 경우 이 재시도는 **다음 경기를 정지**시킬 수 있다.

    래치가 풀렸다(=판이 끝났다)면 쏘지 않는다. 24시간 무인에서 남의 판을 끊는 사고를
    막는 가드다(Codex 2차 의견으로 발견).
    """

    app = _tick_app(latched=False)
    app._auto_exit_done_at = 100.0
    app._exit_effect_tick(100.0 + config.EXIT_EFFECT_FAST_SECONDS + 1)
    app.ui_queue.put.assert_not_called()
    assert app._auto_exit_retries == 0


def test_hold_escalates_with_each_retry_up_to_a_cap() -> None:
    """같은 10초로 다시 하면 같은 실패다 — 실측 [6]→[7]이 그랬다."""

    app = _tick_app()
    app._auto_exit_retries = 0
    assert app._exit_hold_seconds() == config.EXIT_HOLD_SECONDS
    app._auto_exit_retries = 1
    assert app._exit_hold_seconds() == (
        config.EXIT_HOLD_SECONDS + config.EXIT_RETRY_HOLD_STEP_SECONDS
    )
    app._auto_exit_retries = 99
    assert app._exit_hold_seconds() == config.EXIT_RETRY_HOLD_MAX_SECONDS
    assert config.EXIT_RETRY_HOLD_MAX_SECONDS > config.EXIT_HOLD_SECONDS


def test_hold_never_exceeds_the_game_crash_threshold() -> None:
    """⚠️ 정지가 11초를 넘으면 **게임이 통째로 꺼진다**(사용자 실측 2026-08-23).

    무인 방치가 기본이라 게임이 꺼지면 창을 못 찾아 그날 나머지가 날아간다. 이 상한을
    올리는 변경은 이 테스트가 막는다.
    """

    assert config.EXIT_RETRY_HOLD_MAX_SECONDS <= 11.0, "게임이 꺼지는 값이다"
    app = _tick_app()
    for retries in range(0, 200):
        app._auto_exit_retries = retries
        assert app._exit_hold_seconds() <= 11.0, retries


def test_no_lobby_in_time_triggers_a_retry_without_reading_the_score() -> None:
    """[4] 실패는 스코어 기반 재시도가 안 걸렸다 — 로비 미도달만으로도 걸려야 한다."""

    app = _tick_app()
    app._auto_exit_done_at = 100.0
    app._exit_effect_tick(100.0 + config.EXIT_EFFECT_FAST_SECONDS - 1)   # 아직
    app.ui_queue.put.assert_not_called()
    app._exit_effect_tick(100.0 + config.EXIT_EFFECT_FAST_SECONDS + 1)   # 시간 초과
    app.ui_queue.put.assert_called_once_with(("auto_exit", ""))
    assert app._auto_exit_retries == 1
    assert app._auto_exit_done_at is None, "같은 종료로 두 번 재시도하면 안 된다"
    assert app._auto_exit_retry_at is None, "스코어 예약과 중복 발사 금지"


def test_reaching_lobby_in_time_cancels_the_retry() -> None:
    app = _tick_app()
    app._auto_exit_done_at = 100.0
    app._note_exit_effect(_target("target_B"), 130.0)      # 30초 — 정상 도달
    app._exit_effect_tick(100.0 + config.EXIT_EFFECT_FAST_SECONDS + 10)
    app.ui_queue.put.assert_not_called()
    assert app._auto_exit_retries == 0


def test_effect_tick_respects_stop_and_the_retry_cap() -> None:
    from types import SimpleNamespace

    app = _tick_app()
    app.stop_event = SimpleNamespace(is_set=lambda: True)
    app._auto_exit_done_at = 100.0
    app._exit_effect_tick(100.0 + config.EXIT_EFFECT_FAST_SECONDS + 1)
    app.ui_queue.put.assert_not_called()                   # 정지 후 입력 0

    app = _tick_app()
    app._auto_exit_retries = config.AUTO_EXIT_RETRY_MAX
    app._auto_exit_done_at = 100.0
    app._exit_effect_tick(100.0 + config.EXIT_EFFECT_FAST_SECONDS + 1)
    app.ui_queue.put.assert_not_called()                   # 한도 초과
    assert app._auto_exit_retries == config.AUTO_EXIT_RETRY_MAX



def test_recovered_score_cancels_the_blind_retry() -> None:
    """스코어가 회복됐으면 더 나가지 않는다.

    이 경로는 '스코어를 못 읽을 때'를 위한 보조인데, 읽히는데도 무시하면 사용자가 정한
    규칙(2점차·후반 1점차)에 안 맞는 판을 강제로 나간다. 실측(2026-08-23 19:06): 0:1
    이던 판이 1:1 동점이 됐는데 스코어 경로는 '회복'으로 재시도를 취소한 반면 이 경로가
    덮어써서 계속 나가려 했다.
    """

    app = _tick_app()
    app._auto_exit_done_at = 100.0
    app._last_match_score = (1, 1)            # 동점 = 어떤 규칙에도 안 걸린다
    app._exit_effect_tick(100.0 + config.EXIT_EFFECT_FAST_SECONDS + 1)
    app.ui_queue.put.assert_not_called()
    assert app._auto_exit_retries == 0

    # 아직 열세면 그대로 재시도한다.
    app = _tick_app()
    app._auto_exit_done_at = 100.0
    app._last_match_score = (0, 2)
    app._exit_effect_tick(100.0 + config.EXIT_EFFECT_FAST_SECONDS + 1)
    app.ui_queue.put.assert_called_once_with(("auto_exit", ""))
