"""감독모드 한 번의 실행 구간을 가볍게 집계하는 세션 통계.

승·무·패와 예상 적립은 더 이상 집계하지 않습니다. 둘 다 '랭킹 점수가 올랐으면 승'
이라는 추정에 기대고 있었는데, 그 추정이 서는 전제(한 경기당 확정 한 번)가 성립하지
않아 값 자체를 믿을 수 없었습니다. 정확한 승패와 적립 FC는 'FC 채굴 현황'이 넥슨
공식 기록으로 보여줍니다. 여기서는 '이 PC에서 몇 판 돌았나'만 셉니다.

이 모듈은 순수 로직이라 Windows 의존성 없이 단위 검증할 수 있습니다.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Optional


# 이 시간 이상 등수 패널이 안 보이면 그 패널은 끝난 것으로 봅니다.
# config.RANK_OCR_PANEL_GAP_SECONDS와 같은 의미이며, 이 모듈을 순수하게 두려고
# 상수를 복제하지 않고 호출부가 주입할 수 있게 기본값으로만 둡니다.
MATCH_PANEL_GONE_SECONDS = 0.9


@dataclass(frozen=True)
class SessionSnapshot:
    running: bool
    elapsed_seconds: float
    matches: int
    matches_per_hour: float


class MatchCounter:
    """등수 패널이 '떴다가 사라진' 전이 한 번을 1판으로 셉니다.

    이전 구현은 OCR 컨센서스가 확정할 때마다 1판을 셌습니다. 그런데 확정 직후
    투표만 비우고 패널은 그대로 떠 있으므로 0.3초쯤 뒤 같은 패널이 다시 확정됩니다.
    45초 중복 제거만이 유일한 방어였고, 그래서 결과 화면에 오래 머물면 45초마다
    있지도 않은 경기가 하나씩 쌓였습니다. 반대로 45초 안에 도착한 진짜 다음 경기는
    통째로 버려졌습니다.

    패널의 소멸을 기준으로 삼으면 두 문제가 같이 사라집니다. 같은 패널에서 몇 번을
    확정하든 1판이고, 다음 경기는 패널이 새로 떠야 하므로 시간 간격과 무관합니다.

    확정이 한 번도 없었던 패널은 세지 않습니다. 패널처럼 보이는 화면을 잘못 잡은
    경우를 배제하려는 것이고, 그래서 미검출은 '0판'이지 '유령 1판'이 아닙니다.
    """

    def __init__(self, *, gone_seconds: float = MATCH_PANEL_GONE_SECONDS):
        self._gone_seconds = max(0.05, float(gone_seconds))
        self._last_seen: Optional[float] = None
        self._commits = 0

    def reset(self) -> None:
        self._last_seen = None
        self._commits = 0

    def observe(self, has_panel: bool, now: float) -> bool:
        """OCR 한 번의 관측을 반영합니다. 1판이 확정되면 True."""

        if has_panel:
            self._last_seen = float(now)
            return False
        return self.tick(now)

    def note_commit(self, now: float) -> None:
        """이번 패널에서 등수/점수가 확정됐음을 기록합니다."""

        self._commits += 1
        if self._last_seen is None:
            # 패널이 사라진 뒤 늦게 확정되는 경로(graceful)에서도 판이 유실되지 않게 합니다.
            self._last_seen = float(now)

    def tick(self, now: float) -> bool:
        """벽시계만 흘려보냅니다. 소멸이 확정되고 셀 것이 있으면 True.

        프레임이 멈춰 관측이 끊겨도 판정이 진행되도록 매 사이클 호출합니다.
        """

        if self._last_seen is None:
            return False
        if float(now) - self._last_seen < self._gone_seconds:
            return False
        counted = self._commits > 0
        self.reset()
        return counted


class SessionTracker:
    """실행 구간의 경과 시간과 판수를 세는 스레드 안전 집계기."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._running = False
        self._started_at: Optional[float] = None
        self._stopped_at = 0.0
        self._matches = 0

    def start(self, now: Optional[float] = None) -> None:
        timestamp = time.monotonic() if now is None else float(now)
        with self._lock:
            self._running = True
            self._started_at = timestamp
            self._stopped_at = timestamp
            self._matches = 0

    def stop(self, now: Optional[float] = None) -> None:
        timestamp = time.monotonic() if now is None else float(now)
        with self._lock:
            if self._running:
                self._stopped_at = max(self._started_at or timestamp, timestamp)
                self._running = False

    def record_match(self) -> bool:
        """1판을 반영합니다. 실행 중이 아니면 무시하고 False를 반환합니다."""

        with self._lock:
            if not self._running:
                return False
            self._matches += 1
            return True

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
            return SessionSnapshot(
                running=self._running,
                elapsed_seconds=elapsed,
                matches=self._matches,
                matches_per_hour=match_rate,
            )
