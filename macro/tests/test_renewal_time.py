from __future__ import annotations

from datetime import datetime, timedelta

from macroapp.renewal_time import KST, NaverMonotonicClock, next_schedule_window


class FakeMonotonic:
    def __init__(self, value: float = 1000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value


class FakeWallClock(FakeMonotonic):
    pass


def test_target_20_uses_19_through_41() -> None:
    now = datetime(2026, 7, 25, 18, 0, tzinfo=KST)
    window = next_schedule_window(now, 20)
    assert window.start == datetime(2026, 7, 25, 18, 19, tzinfo=KST)
    assert window.end_exclusive == datetime(
        2026, 7, 25, 18, 42, tzinfo=KST
    )
    assert window.contains(datetime(2026, 7, 25, 18, 41, 59, tzinfo=KST))
    assert not window.contains(window.end_exclusive)


def test_current_window_starts_immediately_when_it_is_already_active() -> None:
    now = datetime(2026, 7, 25, 18, 30, tzinfo=KST)
    window = next_schedule_window(now, 20)
    assert window.target.hour == 18
    assert window.contains(now)


def test_finished_window_moves_to_next_hour() -> None:
    now = datetime(2026, 7, 25, 18, 42, tzinfo=KST)
    window = next_schedule_window(now, 20)
    assert window.target == datetime(2026, 7, 25, 19, 20, tzinfo=KST)


def test_zero_minute_window_crosses_previous_hour_and_date() -> None:
    now = datetime(2026, 7, 26, 0, 0, tzinfo=KST)
    window = next_schedule_window(now, 0)
    assert window.start == datetime(2026, 7, 25, 23, 59, tzinfo=KST)
    assert window.end_exclusive == datetime(
        2026, 7, 26, 0, 22, tzinfo=KST
    )


def test_installed_naver_time_advances_only_by_monotonic_clock() -> None:
    monotonic = FakeMonotonic()
    wall_clock = FakeWallClock(10_000.0)
    clock = NaverMonotonicClock(
        monotonic=monotonic,
        wall_time=wall_clock,
    )
    initial = datetime(2026, 7, 25, 19, 19, tzinfo=KST)
    clock.install_for_test(initial)
    monotonic.value += 12.5
    wall_clock.value += 12.5
    assert clock.now() == initial + timedelta(seconds=12.5)
    assert clock.is_fresh()
    monotonic.value += 601.0
    wall_clock.value += 601.0
    assert not clock.is_fresh()


def test_windows_clock_jump_invalidates_input_until_resync() -> None:
    monotonic = FakeMonotonic()
    wall_clock = FakeWallClock(10_000.0)
    clock = NaverMonotonicClock(
        monotonic=monotonic,
        wall_time=wall_clock,
    )
    clock.install_for_test(datetime(2026, 7, 25, 19, 19, tzinfo=KST))
    monotonic.value += 1.0
    wall_clock.value += 10.0
    assert not clock.is_fresh()
