from __future__ import annotations

import time
from pathlib import Path
from tempfile import TemporaryDirectory

from macroapp.renewal_budget import RenewalRequestBudget


def _budget(
    path: Path,
    *,
    cooldown: bool = False,
) -> RenewalRequestBudget:
    return RenewalRequestBudget(
        path,
        window_seconds=18.0,
        request_limit=8,
        require_initial_cooldown=cooldown,
    )


def test_first_database_blocks_unknown_previous_request_history() -> None:
    with TemporaryDirectory() as temporary:
        started = time.time()
        budget = _budget(Path(temporary) / "budget.sqlite3", cooldown=True)
        decision = budget.inspect(started)

        assert not decision.allowed
        assert decision.initial_cooldown
        assert 17.0 <= decision.wait_seconds <= 19.0


def test_even_cadence_and_rolling_limit_are_enforced() -> None:
    with TemporaryDirectory() as temporary:
        budget = _budget(Path(temporary) / "budget.sqlite3")
        now = 10_000.0
        reservations: list[float] = []

        for _ in range(24):
            decision = budget.reserve(now)
            assert decision.allowed
            reservations.append(now)
            early = budget.reserve(now)
            assert not early.allowed
            assert early.wait_seconds > 0.0
            now += budget.interval_seconds

        for index, value in enumerate(reservations):
            rolling = [
                item
                for item in reservations
                if value - budget.window_seconds < item <= value
            ]
            assert len(rolling) <= budget.request_limit
            if index:
                assert (
                    value - reservations[index - 1]
                    >= budget.interval_seconds - 1e-6
                )


def test_budget_survives_restart_and_two_instances_share_slot() -> None:
    with TemporaryDirectory() as temporary:
        path = Path(temporary) / "budget.sqlite3"
        first = _budget(path)
        second = _budget(path)
        now = 20_000.0

        assert first.reserve(now).allowed
        denied = second.reserve(now)

        assert not denied.allowed
        assert denied.events_in_window == 1
        assert second.reserve(
            now + first.interval_seconds
        ).allowed


def test_backward_time_jump_never_creates_an_early_slot() -> None:
    with TemporaryDirectory() as temporary:
        budget = _budget(Path(temporary) / "budget.sqlite3")
        assert budget.reserve(30_000.0).allowed

        decision = budget.reserve(29_900.0)

        assert not decision.allowed
        assert decision.wait_seconds >= 100.0
