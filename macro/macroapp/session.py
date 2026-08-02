"""감독모드 한 번의 실행 구간을 가볍게 집계하는 세션 통계."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Optional


RESULT_DEDUP_SECONDS = 45.0


@dataclass(frozen=True)
class SessionSnapshot:
    running: bool
    elapsed_seconds: float
    matches: int
    wins: int
    draws: int
    losses: int
    unknown: int
    estimated_fc: int
    matches_per_hour: float
    fc_per_hour: float


def estimated_fc_for_win(tier: Optional[str]) -> int:
    """OCR 티어명으로 승리 시 예상 적립 FC를 반환합니다."""

    normalized = (tier or "").replace(" ", "")
    if "슈퍼" in normalized and "챔피" in normalized:
        return 20
    if "챔피" in normalized:
        return 15
    return 0


class SessionTracker:
    """점수 변화로 승무패를 추정하는 스레드 안전 세션 집계기.

    첫 결과는 비교할 이전 점수가 없으므로 경기 수에는 포함하되 미판정으로 둡니다.
    같은 결과 패널에서 OCR 확정이 반복되는 경우는 짧은 시간 안에 한 경기로 합칩니다.
    """

    def __init__(self, *, dedup_seconds: float = RESULT_DEDUP_SECONDS) -> None:
        self._lock = threading.RLock()
        self._dedup_seconds = max(1.0, float(dedup_seconds))
        self._running = False
        self._started_at: Optional[float] = None
        self._stopped_at = 0.0
        self._last_result_at = float("-inf")
        self._previous_score: Optional[int] = None
        self._matches = 0
        self._wins = 0
        self._draws = 0
        self._losses = 0
        self._unknown = 0
        self._estimated_fc = 0

    def start(self, now: Optional[float] = None) -> None:
        timestamp = time.monotonic() if now is None else float(now)
        with self._lock:
            self._running = True
            self._started_at = timestamp
            self._stopped_at = timestamp
            self._last_result_at = float("-inf")
            self._previous_score = None
            self._matches = 0
            self._wins = 0
            self._draws = 0
            self._losses = 0
            self._unknown = 0
            self._estimated_fc = 0

    def stop(self, now: Optional[float] = None) -> None:
        timestamp = time.monotonic() if now is None else float(now)
        with self._lock:
            if self._running:
                self._stopped_at = max(self._started_at, timestamp)
                self._running = False

    def record_result(
        self,
        score: Optional[int],
        tier: Optional[str],
        *,
        now: Optional[float] = None,
    ) -> Optional[str]:
        """결과 한 건을 반영하고 ``win/draw/loss/unknown`` 또는 None을 반환합니다."""

        timestamp = time.monotonic() if now is None else float(now)
        normalized_score = None if score is None else int(score)
        with self._lock:
            if not self._running:
                return None
            if timestamp - self._last_result_at < self._dedup_seconds:
                return None

            self._last_result_at = timestamp
            self._matches += 1
            previous = self._previous_score
            if normalized_score is None or previous is None:
                outcome = "unknown"
                self._unknown += 1
            elif normalized_score > previous:
                outcome = "win"
                self._wins += 1
                self._estimated_fc += estimated_fc_for_win(tier)
            elif normalized_score < previous:
                outcome = "loss"
                self._losses += 1
            else:
                outcome = "draw"
                self._draws += 1

            if normalized_score is not None:
                self._previous_score = normalized_score
            return outcome

    def snapshot(self, now: Optional[float] = None) -> SessionSnapshot:
        timestamp = time.monotonic() if now is None else float(now)
        with self._lock:
            end = timestamp if self._running else self._stopped_at
            elapsed = (
                max(0.0, end - self._started_at)
                if self._started_at is not None
                else 0.0
            )
            hours = elapsed / 3600.0
            match_rate = self._matches / hours if hours > 0.0 else 0.0
            fc_rate = self._estimated_fc / hours if hours > 0.0 else 0.0
            return SessionSnapshot(
                running=self._running,
                elapsed_seconds=elapsed,
                matches=self._matches,
                wins=self._wins,
                draws=self._draws,
                losses=self._losses,
                unknown=self._unknown,
                estimated_fc=self._estimated_fc,
                matches_per_hour=match_rate,
                fc_per_hour=fc_rate,
            )
