"""Persistent FC renewal request budget.

Every popup-open input is reserved in SQLite before it is sent.  The database
survives process restarts and uses an immediate transaction so two local macro
processes cannot both consume the same slot.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


RENEWAL_REQUEST_WINDOW_SECONDS = 30.0 * 60.0
RENEWAL_REQUEST_LIMIT = 840
RENEWAL_REQUEST_INTERVAL_SECONDS = (
    RENEWAL_REQUEST_WINDOW_SECONDS / RENEWAL_REQUEST_LIMIT
)


def renewal_request_budget_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = (
        Path(local_app_data)
        if local_app_data
        else Path.home() / "AppData" / "Local"
    )
    return root / "mAuto" / "renewal_request_budget.sqlite3"


@dataclass(frozen=True)
class RenewalBudgetDecision:
    allowed: bool
    wait_seconds: float
    events_in_window: int
    next_allowed_at: float
    initial_cooldown: bool = False


class RenewalRequestBudget:
    """Crash-safe rolling request budget with evenly spaced action slots."""

    def __init__(
        self,
        path: Optional[Path] = None,
        *,
        window_seconds: float = RENEWAL_REQUEST_WINDOW_SECONDS,
        request_limit: int = RENEWAL_REQUEST_LIMIT,
        require_initial_cooldown: bool = True,
        session_id: Optional[str] = None,
    ):
        self.path = Path(path or renewal_request_budget_path())
        self.window_seconds = max(1.0, float(window_seconds))
        self.request_limit = max(1, int(request_limit))
        self.interval_seconds = self.window_seconds / self.request_limit
        self.session_id = session_id or uuid.uuid4().hex
        self.path.parent.mkdir(parents=True, exist_ok=True)
        database_existed = self.path.exists()
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS request_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at REAL NOT NULL,
                    session_id TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS request_events_occurred_at
                ON request_events(occurred_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            if not database_existed:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO metadata(key, value)
                    VALUES('uncertain_until', ?)
                    """,
                    ("0",),
                )
                connection.execute(
                    """
                    INSERT OR REPLACE INTO metadata(key, value)
                    VALUES('initial_cooldown_pending', ?)
                    """,
                    ("1" if require_initial_cooldown else "0",),
                )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
        )
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _metadata_float(
        connection: sqlite3.Connection,
        key: str,
    ) -> float:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return 0.0
        try:
            return float(row[0])
        except (TypeError, ValueError):
            return float("inf")

    def _decision_locked(
        self,
        connection: sqlite3.Connection,
        now: float,
    ) -> RenewalBudgetDecision:
        cutoff = now - self.window_seconds
        connection.execute(
            "DELETE FROM request_events WHERE occurred_at <= ?",
            (cutoff,),
        )
        row = connection.execute(
            """
            SELECT COUNT(*), MIN(occurred_at), MAX(occurred_at)
            FROM request_events
            """
        ).fetchone()
        count = int(row[0] or 0)
        oldest = float(row[1]) if row[1] is not None else 0.0
        newest = float(row[2]) if row[2] is not None else 0.0
        cooldown_pending = (
            self._metadata_float(
                connection,
                "initial_cooldown_pending",
            )
            > 0.0
        )
        if cooldown_pending:
            uncertain_until = now + self.window_seconds
            connection.execute(
                """
                INSERT OR REPLACE INTO metadata(key, value)
                VALUES('uncertain_until', ?)
                """,
                (f"{uncertain_until:.9f}",),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO metadata(key, value)
                VALUES('initial_cooldown_pending', '0')
                """
            )
        else:
            uncertain_until = self._metadata_float(
                connection,
                "uncertain_until",
            )
        initial_cooldown = uncertain_until > now
        next_allowed = uncertain_until if initial_cooldown else now
        if newest > 0.0:
            next_allowed = max(
                next_allowed,
                newest + self.interval_seconds,
            )
        if count >= self.request_limit and oldest > 0.0:
            next_allowed = max(
                next_allowed,
                oldest + self.window_seconds,
            )
        wait_seconds = max(0.0, next_allowed - now)
        return RenewalBudgetDecision(
            allowed=wait_seconds <= 1e-6,
            wait_seconds=wait_seconds,
            events_in_window=count,
            next_allowed_at=next_allowed,
            initial_cooldown=initial_cooldown,
        )

    def inspect(self, now: float) -> RenewalBudgetDecision:
        now = float(now)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                decision = self._decision_locked(connection, now)
                connection.execute("COMMIT")
                return decision
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def reserve(self, now: float) -> RenewalBudgetDecision:
        """Atomically reserve one popup-open input if a slot is available."""

        now = float(now)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                decision = self._decision_locked(connection, now)
                if not decision.allowed:
                    connection.execute("COMMIT")
                    return decision
                connection.execute(
                    """
                    INSERT INTO request_events(occurred_at, session_id)
                    VALUES(?, ?)
                    """,
                    (now, self.session_id),
                )
                connection.execute("COMMIT")
                return RenewalBudgetDecision(
                    allowed=True,
                    wait_seconds=0.0,
                    events_in_window=decision.events_in_window + 1,
                    next_allowed_at=now + self.interval_seconds,
                    initial_cooldown=False,
                )
            except Exception:
                connection.execute("ROLLBACK")
                raise
