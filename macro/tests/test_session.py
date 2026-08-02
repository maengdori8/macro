from macroapp.session import SessionTracker, estimated_fc_for_win


def test_fc_estimate_from_ocr_tier_name():
    assert estimated_fc_for_win("슈퍼 챔피언스 감독") == 20
    assert estimated_fc_for_win("챔피언스 감독") == 15
    assert estimated_fc_for_win("월드클래스 1부 감독") == 0
    assert estimated_fc_for_win(None) == 0


def test_session_tracks_results_rates_and_duplicate_panels():
    tracker = SessionTracker(dedup_seconds=45)
    tracker.start(now=100)

    assert tracker.record_result(2000, "챔피언스 감독", now=110) == "unknown"
    assert tracker.record_result(2001, "챔피언스 감독", now=120) is None
    assert tracker.record_result(2010, "챔피언스 감독", now=160) == "win"
    assert tracker.record_result(2010, "챔피언스 감독", now=220) == "draw"
    assert tracker.record_result(1995, "챔피언스 감독", now=280) == "loss"

    snapshot = tracker.snapshot(now=300)
    assert snapshot.running is True
    assert snapshot.matches == 4
    assert (snapshot.wins, snapshot.draws, snapshot.losses, snapshot.unknown) == (1, 1, 1, 1)
    assert snapshot.estimated_fc == 15
    assert snapshot.elapsed_seconds == 200
    assert snapshot.matches_per_hour == 72
    assert snapshot.fc_per_hour == 270


def test_stopped_session_freezes_elapsed_time_and_ignores_results():
    tracker = SessionTracker()
    tracker.start(now=100)
    tracker.stop(now=130)

    assert tracker.record_result(2100, "슈퍼 챔피언스 감독", now=200) is None
    snapshot = tracker.snapshot(now=500)
    assert snapshot.running is False
    assert snapshot.elapsed_seconds == 30
    assert snapshot.matches == 0
