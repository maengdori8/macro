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
