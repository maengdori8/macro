"""종료 재시도 — 일시정지까지 간 판이 안 나가졌으면 같은 판에서 다시 시도한다.

사용자 요구: '일시정지→재시작까지 갔으면 그 판은 나가는 판. 마무리가 실패해 안 나가지면,
재시작 뒤 30초 후에도 종료조건(열세)이 그대로면 쿼터를 새로 세지 말고 확정적으로 나갈
때까지 다시 시도해.' 여기서는 판정(_auto_exit_retry_tick / _arm_auto_exit_retry)만 검증한다.
"""

from __future__ import annotations

import queue
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from macroapp import auto_exit, config, gui  # noqa: E402


def _rules():
    return auto_exit.ExitRules(base_deficit=2, hard_deficit=3, late_minute=70, late_deficit=1)


def _latched_tracker():
    """이미 종료가 확정된(래치 유지) 트래커 — '나가기로 확정한 판'."""
    t = auto_exit.LossTracker(confirm_count=1, reset_seconds=60, rules=_rules())
    t.feed(0.0, (0, 0))          # 선행 스코어
    assert t.observe(1.0, (0, 2)) == auto_exit.KIND_BASE
    assert t.latched is True
    return t


def _app(tracker):
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app._loss_tracker = tracker
    app._auto_exit_retry_at = None
    app._auto_exit_retries = 0
    app._auto_exit_current = False
    app._auto_exit_resumed_at = None
    app.stop_event = SimpleNamespace(is_set=lambda: False)
    app.ui_queue = queue.Queue()
    app.queue_log = Mock()
    app._log_to_file_only = Mock()
    return app


def _queued(app):
    items = []
    try:
        while True:
            items.append(app.ui_queue.get_nowait())
    except queue.Empty:
        pass
    return items


# ─── 무장 ───────────────────────────────────────────────────────────────────


def test_arm_sets_a_deadline_and_respects_the_cap():
    app = _app(_latched_tracker())
    app._arm_auto_exit_retry()
    assert app._auto_exit_retry_at is not None
    # 한도에 도달하면 더 무장하지 않는다.
    app._auto_exit_retries = config.AUTO_EXIT_RETRY_MAX
    app._arm_auto_exit_retry()
    assert app._auto_exit_retry_at is None


def test_arm_measures_from_the_resume_moment_not_completion():
    """사용자 요구 = '재시작 시점부터 30초'. 마무리가 오래 끌어도 기준은 되살아난 시각이다."""
    import time as _time

    app = _app(_latched_tracker())
    resumed = _time.monotonic() - 25.0        # 25초 전에 되살아났고 마무리가 그만큼 끌었다
    app._auto_exit_resumed_at = resumed
    app._arm_auto_exit_retry()
    assert app._auto_exit_retry_at == resumed + config.AUTO_EXIT_RETRY_SECONDS
    # 남은 대기는 30초가 아니라 5초 남짓이어야 한다(완료 기준이면 30초였다).
    assert 0 < app._auto_exit_retry_at - _time.monotonic() < 10
    # 기준 시각은 1회용이다 — 다음 종료가 옛 시각을 물려받으면 안 된다.
    assert app._auto_exit_resumed_at is None


def test_arm_falls_back_to_now_without_a_resume_stamp():
    import time as _time

    app = _app(_latched_tracker())
    app._auto_exit_resumed_at = None
    app._arm_auto_exit_retry()
    assert app._auto_exit_retry_at - _time.monotonic() > config.AUTO_EXIT_RETRY_SECONDS - 2


def test_followup_state_stamps_the_resume_moment():
    """마무리 단계 진입(STATE_FOLLOWUP) = 대상이 되살아난 시점 → 기준 시각으로 기록."""
    from macroapp import exit_core, exit_runner

    app = _app(_latched_tracker())
    app._exit_gate = SimpleNamespace(clear=Mock(), is_set=lambda: False)
    app.log = Mock()
    app.set_status = Mock()
    app.root = SimpleNamespace(after=Mock())
    app._auto_exit_current = False
    events = [
        SimpleNamespace(kind=exit_runner.EVT_STATE, text="마무리",
                        state=exit_core.STATE_FOLLOWUP),
        SimpleNamespace(kind=exit_runner.EVT_STATE, text="완료",
                        state=exit_core.STATE_DONE),
    ]
    app._exit_runner = SimpleNamespace(
        drain=lambda handle: [handle(e) for e in events]
    )
    app._poll_exit_events()
    assert app._auto_exit_resumed_at is not None, "되살아난 시각이 기록되지 않았다"


# ─── 재시도 판정 ─────────────────────────────────────────────────────────────


def test_no_arm_no_action():
    app = _app(_latched_tracker())
    app._auto_exit_retry_tick((0, 2), None, 1000.0)
    assert _queued(app) == []


def test_before_deadline_waits():
    app = _app(_latched_tracker())
    app._auto_exit_retry_at = 1000.0
    app._auto_exit_retry_tick((0, 2), None, 999.0)
    assert _queued(app) == []
    assert app._auto_exit_retry_at == 1000.0


def test_still_losing_at_deadline_retries_without_touching_quota():
    tracker = _latched_tracker()
    app = _app(tracker)
    app._exit_quota = auto_exit.ExitQuota(0.4)
    before = app._exit_quota.lost_games
    app._auto_exit_retry_at = 1000.0
    app._auto_exit_retry_tick((0, 2), None, 1000.0)      # 마감 도달, 아직 0:2 열세
    assert _queued(app) == [("auto_exit", "")]
    assert app._auto_exit_retries == 1
    assert app._exit_quota.lost_games == before, "재시도가 쿼터를 새로 셌다"
    # 즉시 재무장(유실 대비)돼 있다.
    assert app._auto_exit_retry_at == 1000.0 + config.AUTO_EXIT_RETRY_SECONDS


def test_worse_score_still_retries():
    """0:2 로 나가려다 실패해 0:3 이 돼도 여전히 종료조건 → 재시도."""
    app = _app(_latched_tracker())
    app._auto_exit_retry_at = 1000.0
    app._auto_exit_retry_tick((0, 3), None, 1000.0)
    assert _queued(app) == [("auto_exit", "")]


def test_recovered_score_cancels_retry():
    """열세가 아니게 되면(회복/오독) 종료조건 미충족 → 재시도 안 하고 해제."""
    app = _app(_latched_tracker())
    app._auto_exit_retry_at = 1000.0
    app._auto_exit_retries = 1
    app._auto_exit_retry_tick((1, 1), None, 1000.0)       # 동점
    assert _queued(app) == []
    assert app._auto_exit_retry_at is None
    assert app._auto_exit_retries == 0


def test_unreadable_score_rechecks_later_does_not_retry():
    """스코어를 못 읽으면(None=리플레이 가림 / 미상) 확정 못 하니 잠깐 뒤 다시 본다."""
    app = _app(_latched_tracker())
    app._auto_exit_retry_at = 1000.0
    for reading in (None, auto_exit.SCORE_UNKNOWN):
        app._auto_exit_retry_at = 1000.0
        app._auto_exit_retry_tick(reading, None, 1000.0)
        assert _queued(app) == []
        assert app._auto_exit_retry_at == 1000.0 + config.AUTO_EXIT_RETRY_RECHECK_SECONDS


def test_match_ended_releases_retry():
    """스코어보드 60초 부재로 판이 끝나면(래치 풀림 = 나갔음) 재시도 대기 해제."""
    tracker = _latched_tracker()
    # 60초 부재 → 래치 해제
    tracker.feed(2.0, None)
    tracker.feed(70.0, None)
    assert tracker.latched is False
    app = _app(tracker)
    app._auto_exit_retry_at = 1000.0
    app._auto_exit_retries = 2
    app._auto_exit_retry_tick((0, 2), None, 1000.0)
    assert _queued(app) == []
    assert app._auto_exit_retry_at is None
    assert app._auto_exit_retries == 0


def test_stop_clears_retry():
    app = _app(_latched_tracker())
    app.stop_event = SimpleNamespace(is_set=lambda: True)
    app._auto_exit_retry_at = 1000.0
    app._auto_exit_retry_tick((0, 2), None, 1000.0)
    assert _queued(app) == []
    assert app._auto_exit_retry_at is None


def test_cap_is_enforced_in_the_tick_too():
    """한도가 _arm 에만 있으면, 넘긴 요청이 드롭돼 완료-무장이 안 돌 때 무한히 쏜다."""
    app = _app(_latched_tracker())
    app._auto_exit_retries = config.AUTO_EXIT_RETRY_MAX
    app._auto_exit_retry_at = 1000.0
    app._auto_exit_retry_tick((0, 2), None, 1000.0)
    assert _queued(app) == [], "한도를 넘겼는데 tick 이 재시도를 또 쐈다"
    assert app._auto_exit_retry_at is None
    # 무장도 당연히 거부된다.
    app._arm_auto_exit_retry()
    assert app._auto_exit_retry_at is None


# ─── 완료 시 무장 배선 ────────────────────────────────────────────────────────


def _auto_exit_app():
    app = _app(_latched_tracker())
    app._is_pro = True
    app._exit_gate = SimpleNamespace(is_set=lambda: False)
    app.stop_event = SimpleNamespace(is_set=lambda: False)
    app.worker_thread = SimpleNamespace(is_alive=lambda: True)
    return app


def test_auto_exit_marks_current_only_when_it_actually_started():
    app = _auto_exit_app()
    app.run_quick_exit = Mock(return_value=True)
    app._run_auto_exit()
    assert app._auto_exit_current is True, "자동 종료가 재시도 대상으로 표시되지 않았다"
    app.run_quick_exit.assert_called_once()


def test_auto_exit_does_not_mark_current_when_the_exit_never_started():
    """만료·busy·start 실패로 조용히 돌아서면 재시도 대상이 아니다 — 그렇지 않으면
    나중의 다른 종료 완료가 이 판을 '자동 종료였다'로 오귀속해 엉뚱하게 무장한다."""
    app = _auto_exit_app()
    app.run_quick_exit = Mock(return_value=False)
    app._run_auto_exit()
    assert app._auto_exit_current is False
