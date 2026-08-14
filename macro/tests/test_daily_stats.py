import datetime as dt

from macroapp.daily_stats import (
    day_bounds_utc,
    format_matches,
    today_kst,
    utc_iso,
)
from macroapp.mining import KST


def test_today_uses_kst_regardless_of_caller_timezone():
    # 같은 순간을 세 타임존으로 표현해도 KST 기준 날짜는 하나여야 합니다.
    moment_utc = dt.datetime(2026, 8, 9, 16, 30, tzinfo=dt.timezone.utc)
    moment_kst = moment_utc.astimezone(KST)
    moment_la = moment_utc.astimezone(dt.timezone(dt.timedelta(hours=-7)))

    assert today_kst(moment_utc) == dt.date(2026, 8, 10)
    assert today_kst(moment_kst) == dt.date(2026, 8, 10)
    assert today_kst(moment_la) == dt.date(2026, 8, 10)


def test_midnight_boundary_is_half_open():
    start, end = day_bounds_utc(dt.date(2026, 8, 9))
    # KST 자정 = 전날 15:00 UTC
    assert start == "2026-08-08T15:00:00Z"
    assert end == "2026-08-09T15:00:00Z"

    just_before = utc_iso(dt.datetime(2026, 8, 9, 23, 59, 59, tzinfo=KST))
    just_after = utc_iso(dt.datetime(2026, 8, 10, 0, 0, 0, tzinfo=KST))
    assert start <= just_before < end
    assert not (start <= just_after < end)


def test_consecutive_days_do_not_overlap_or_gap():
    first_start, first_end = day_bounds_utc(dt.date(2026, 8, 9))
    second_start, second_end = day_bounds_utc(dt.date(2026, 8, 10))
    assert first_end == second_start
    assert first_start < first_end < second_end


def test_naive_datetime_is_rejected():
    for call in (
        lambda: today_kst(dt.datetime(2026, 8, 9, 12, 0)),
        lambda: utc_iso(dt.datetime(2026, 8, 9, 12, 0)),
    ):
        try:
            call()
        except ValueError:
            continue
        raise AssertionError("타임존 없는 datetime을 받아들이면 안 됩니다.")


def test_format_matches_shows_dash_when_today_is_unknown():
    # 저장소를 못 읽었을 때 0판으로 단정하면 사용자가 잘못 믿습니다.
    assert format_matches(12, None) == "12  ·  오늘 —"
    assert format_matches(12, 0) == "12  ·  오늘 0"
    assert format_matches(1234, 5678) == "1,234  ·  오늘 5,678"


def test_macro_match_ledger_roundtrip_and_day_filter(tmp_path):
    """원장에 적고 KST 하루 경계로 되읽는 왕복. 자정 양쪽이 서로 다른 날로 갈립니다."""

    from macroapp.mining import MiningStore

    store = MiningStore(tmp_path)
    for moment in (
        dt.datetime(2026, 8, 9, 0, 0, 0, tzinfo=KST),
        dt.datetime(2026, 8, 9, 23, 59, 59, tzinfo=KST),
        dt.datetime(2026, 8, 10, 0, 0, 0, tzinfo=KST),
    ):
        store.append_macro_match("session-1", utc_iso(moment))

    start, end = day_bounds_utc(dt.date(2026, 8, 9))
    assert store.count_macro_matches(start, end) == 2

    start, end = day_bounds_utc(dt.date(2026, 8, 10))
    assert store.count_macro_matches(start, end) == 1


def test_existing_database_is_upgraded_without_error(tmp_path):
    """구버전 DB 파일을 다시 열어도 새 테이블이 조용히 추가돼야 합니다."""

    from macroapp.mining import MiningStore

    MiningStore(tmp_path)
    reopened = MiningStore(tmp_path)
    start, end = day_bounds_utc(dt.date(2026, 8, 9))
    assert reopened.count_macro_matches(start, end) == 0
