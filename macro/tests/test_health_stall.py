"""가동률 감시(StallMonitor) — '켜져 있는데 서 있는 시간'을 잡는다.

근거(2026-08-22 로그 실측): 8-18 프로 로그는 게임 창이 사라진 뒤 3시간(그날 51판의 30%)
동안 '창을 찾지 못했습니다'만 7,509회 찍고 사용자가 돌아올 때까지 그대로였다. 컷신을
완벽히 스킵해도 하루 2~4판인데 이건 16판이다.

고정하는 것:
  1) 창 유실은 first_alert 뒤에 알리고, 그 뒤 repeat 간격으로만 알린다(도배 금지).
  2) 창이 돌아오면 회복으로 보고 타이머를 처음부터.
  3) 진행 없음은 경기 한 판보다 관대한 유예를 둔다(정상 경기를 정지로 오인 금지).
  4) 시작/재시작(reset)이 모든 타이머를 지금으로 맞춘다.
"""

from __future__ import annotations

from macroapp.health import (
    STALL_NONE,
    STALL_PROGRESS,
    STALL_WINDOW,
    StallMonitor,
    format_duration,
    stall_message,
)


def _monitor() -> StallMonitor:
    m = StallMonitor(
        first_alert_seconds=180.0,
        repeat_alert_seconds=900.0,
        progress_grace_seconds=420.0,
    )
    m.reset(0.0)
    return m


# ─── 창 유실 ────────────────────────────────────────────────────────────────


def test_window_loss_alerts_once_then_on_the_repeat_interval() -> None:
    m = _monitor()
    m.note_window(False, 10.0)
    # 유예 안에는 조용하다(잠깐 못 찾는 건 흔하다).
    assert m.poll(60.0) == (STALL_NONE, False, 0.0)
    state, alert, seconds = m.poll(200.0)          # 190초째 — 첫 알림
    assert (state, alert) == (STALL_WINDOW, True)
    assert seconds == 190.0
    # 곧바로 다시 물어도 두 번째 알림은 없다(7,509줄 도배 방지).
    for t in (201.0, 400.0, 900.0, 1090.0):
        assert m.poll(t)[1] is False, t
    assert m.poll(1101.0)[1] is True               # 첫 알림 +900초
    assert m.alert_count == 2


def test_window_recovery_resets_everything() -> None:
    m = _monitor()
    m.note_window(False, 0.0)
    assert m.poll(200.0)[1] is True
    m.note_window(True, 210.0)                     # 창이 돌아왔다
    assert m.poll(220.0) == (STALL_NONE, False, 0.0)
    assert m.alert_count == 0
    # 다시 잃으면 처음부터 센다(즉시 알리지 않는다).
    m.note_window(False, 230.0)
    assert m.poll(300.0)[0] == STALL_NONE
    assert m.poll(420.0)[0] == STALL_WINDOW


# ─── 진행 없음 ──────────────────────────────────────────────────────────────


def test_progress_gap_is_forgiving_enough_for_one_match() -> None:
    """한 경기는 10분 넘게 걸린다 — 경기 중 조용한 구간을 정지로 오인하면 안 된다."""

    m = _monitor()
    m.note_progress(0.0)
    for t in (100.0, 300.0, 419.0):                # 7분 미만은 정상
        assert m.poll(t)[0] == STALL_NONE, t
    assert m.poll(421.0)[0] == STALL_PROGRESS
    # 진행이 하나라도 있으면 즉시 회복.
    m.note_progress(430.0)
    assert m.poll(440.0) == (STALL_NONE, False, 0.0)
    assert m.alert_count == 0


def test_progress_stall_alerts_and_repeats() -> None:
    m = _monitor()
    m.note_progress(0.0)
    state, alert, seconds = m.poll(500.0)
    assert (state, alert) == (STALL_PROGRESS, True)
    assert seconds == 500.0
    assert m.poll(600.0)[1] is False
    assert m.poll(1401.0)[1] is True               # +900초


def test_window_loss_supersedes_progress_stall_as_a_new_alert() -> None:
    """종류가 바뀌면 새 정지다 — 원인이 달라졌으니 다시 알려야 한다."""

    m = _monitor()
    m.note_progress(0.0)
    assert m.poll(500.0)[1] is True                # 진행 없음 알림
    m.note_window(False, 510.0)
    state, alert, _ = m.poll(700.0)                # 창 유실 190초째
    assert (state, alert) == (STALL_WINDOW, True)


def test_reset_clears_state_on_restart() -> None:
    m = _monitor()
    m.note_window(False, 0.0)
    m.poll(200.0)
    m.reset(1000.0)
    assert m.poll(1010.0) == (STALL_NONE, False, 0.0)
    assert m.alert_count == 0
    assert m.poll(1200.0)[0] == STALL_NONE         # 유실 기록도 지워졌다


# ─── 문구 ───────────────────────────────────────────────────────────────────


def test_duration_and_message_are_readable_korean() -> None:
    assert format_duration(45) == "45초"
    assert format_duration(600) == "10분"
    assert format_duration(3600) == "1시간"
    assert format_duration(3780) == "1시간 3분"
    assert "게임 창" in stall_message(STALL_WINDOW, 720)
    assert "12분" in stall_message(STALL_WINDOW, 720)
    assert "진행이 없" in stall_message(STALL_PROGRESS, 600)
    assert stall_message(STALL_NONE, 0) == ""


# ─── 배선(_check_stall) — 1.0.45 사고를 막는 테스트 ─────────────────────────
#
# 위의 순수 로직 테스트는 전부 통과했는데도 실제로 실행되는 단 한 줄(알림 전송)의
# 오타(report_status → _report_status)가 첫 알림에서 AutomationApp 을 죽였다.
# 교훈: 순수 로직만 고정하면 '유일하게 돌아가는 배선'이 미검증으로 남는다.


def _wired_app(monitor_kwargs=None):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from macroapp import config, gui

    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app._stall = StallMonitor(
        first_alert_seconds=config.STALL_FIRST_ALERT_SECONDS,
        repeat_alert_seconds=config.STALL_REPEAT_ALERT_SECONDS,
        progress_grace_seconds=config.STALL_PROGRESS_GRACE_SECONDS,
        **(monitor_kwargs or {}),
    )
    app._stall.reset(0.0)
    app._stall_state = STALL_NONE
    app.queue_log = Mock()
    app._report_status = Mock()
    return app, SimpleNamespace


def test_check_stall_alerts_through_the_real_wiring() -> None:
    """창 유실 알림이 로그와 상태 전송으로 실제로 나간다(메서드 이름 포함)."""

    from macroapp import config

    app, _ = _wired_app()
    app._stall.note_window(False, 0.0)
    app._check_stall(config.STALL_FIRST_ALERT_SECONDS + 10.0)
    logged = str(app.queue_log.call_args.args[0])
    assert "[가동률]" in logged and "게임 창" in logged
    app._report_status.assert_called_once()
    kwargs = app._report_status.call_args.kwargs
    assert kwargs["running"] is True
    assert "게임 창" in kwargs["message"]


def test_check_stall_is_quiet_while_healthy() -> None:
    app, _ = _wired_app()
    app._stall.note_progress(0.0)
    app._check_stall(10.0)
    app.queue_log.assert_not_called()
    app._report_status.assert_not_called()
    assert app._stall_state == STALL_NONE


def test_check_stall_never_raises_into_the_automation_loop() -> None:
    """감시는 어떤 경우에도 본체를 못 막는다 — 1.0.45 가 이걸 어겨 매크로가 죽었다."""

    from macroapp import config

    app, _ = _wired_app()
    app._stall.note_window(False, 0.0)
    # 알림 경로의 어느 조각이 터져도 루프로 예외가 새면 안 된다.
    for broken in ("queue_log", "_report_status"):
        setattr(app, broken, _raise)
        app._stall.reset(0.0)
        app._stall.note_window(False, 0.0)
        app._check_stall(config.STALL_FIRST_ALERT_SECONDS + 10.0)   # 예외 없이 통과해야 한다
        setattr(app, broken, __import__("unittest.mock", fromlist=["Mock"]).Mock())
    # 판정 자체가 터져도 마찬가지.
    app._stall = _BrokenMonitor()
    app._check_stall(1.0)


def _raise(*args, **kwargs):
    raise RuntimeError("의도적 실패")


class _BrokenMonitor:
    def poll(self, now):
        raise RuntimeError("의도적 실패")
