from macroapp.session import MatchCounter, SessionTracker


def test_session_counts_matches_and_rate():
    tracker = SessionTracker()
    tracker.start(now=100)

    for _ in range(4):
        assert tracker.record_match() is True

    snapshot = tracker.snapshot(now=300)
    assert snapshot.running is True
    assert snapshot.matches == 4
    assert snapshot.elapsed_seconds == 200
    assert snapshot.matches_per_hour == 72


def test_stopped_session_freezes_elapsed_time_and_ignores_matches():
    tracker = SessionTracker()
    tracker.start(now=100)
    tracker.stop(now=130)

    assert tracker.record_match() is False
    snapshot = tracker.snapshot(now=500)
    assert snapshot.running is False
    assert snapshot.elapsed_seconds == 30
    assert snapshot.matches == 0


def test_start_resets_previous_session_matches():
    tracker = SessionTracker()
    tracker.start(now=0)
    tracker.record_match()
    tracker.start(now=10)

    assert tracker.snapshot(now=10).matches == 0


def _panel(counter, times, *, commit_at=()):
    """times 동안 패널이 보이고 commit_at 시점에 확정이 있었다고 알립니다."""

    counted = 0
    for moment in times:
        if counter.observe(True, moment):
            counted += 1
        if moment in commit_at:
            counter.note_commit(moment)
    return counted


def test_one_panel_is_one_match_however_many_commits():
    """같은 패널에서 여러 번 확정돼도 1판이다. (0.3초마다 재확정되던 유령 카운트)"""

    counter = MatchCounter(gone_seconds=0.9)
    # 결과 화면에 60초 머무는 동안 0.3초마다 확정이 일어난다고 가정.
    moments = [round(0.3 * step, 1) for step in range(200)]
    assert _panel(counter, moments, commit_at=set(moments)) == 0

    # 패널이 사라져야 비로소 1판.
    assert counter.observe(False, 60.0) is False  # 아직 소멸 확정 전
    assert counter.observe(False, 61.0) is True
    assert counter.observe(False, 62.0) is False  # 두 번 세지 않는다


def test_consecutive_matches_are_counted_even_when_close_together():
    """45초 중복 제거가 삼켜버리던 '짧은 간격의 진짜 다음 경기'를 센다."""

    counter = MatchCounter(gone_seconds=0.9)
    total = 0
    start = 0.0
    for _ in range(3):
        _panel(counter, [start, start + 0.1], commit_at={start + 0.1})
        if counter.observe(False, start + 1.2):
            total += 1
        start += 2.0  # 45초보다 훨씬 짧은 간격
    assert total == 3


def test_panel_without_commit_is_not_counted():
    """확정이 한 번도 없던 화면은 세지 않는다(패널 오검출 방어)."""

    counter = MatchCounter(gone_seconds=0.9)
    _panel(counter, [0.0, 0.2, 0.4])
    assert counter.observe(False, 2.0) is False


def test_tick_settles_when_frames_stop_arriving():
    """WGC가 정적 화면에서 프레임 공급을 멈춰도 판정이 진행된다."""

    counter = MatchCounter(gone_seconds=0.9)
    counter.observe(True, 0.0)
    counter.note_commit(0.1)
    assert counter.tick(0.5) is False
    assert counter.tick(1.5) is True


def test_commit_after_panel_disappeared_is_not_lost():
    """패널이 사라진 뒤 늦게 확정되는 graceful 경로도 1판으로 센다."""

    counter = MatchCounter(gone_seconds=0.9)
    counter.note_commit(5.0)
    assert counter.tick(5.5) is False
    assert counter.tick(6.0) is True


def test_reset_drops_panel_state():
    counter = MatchCounter(gone_seconds=0.9)
    counter.observe(True, 0.0)
    counter.note_commit(0.1)
    counter.reset()
    assert counter.tick(10.0) is False
