"""네이버 서버 시각을 단조 시계에 고정하는 갱신 전용 시간원."""

from __future__ import annotations

import email.utils
import statistics
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional


NAVER_TIME_URL = "https://www.naver.com"
KST = timezone(timedelta(hours=9), name="KST")
NAVER_TIME_MAX_AGE_SECONDS = 600.0
NAVER_TIME_REFRESH_SECONDS = 300.0


@dataclass(frozen=True)
class RenewalScheduleWindow:
    target: datetime
    start: datetime
    end_exclusive: datetime

    def contains(self, value: datetime) -> bool:
        return self.start <= value < self.end_exclusive


def next_schedule_window(
    now: datetime,
    target_minute: int,
) -> RenewalScheduleWindow:
    """현재 구간이 남아 있으면 그것을, 끝났으면 다음 시간 구간을 반환합니다."""

    minute = min(59, max(0, int(target_minute)))
    if now.tzinfo is None:
        raise ValueError("schedule time must be timezone-aware")
    target = now.replace(minute=minute, second=0, microsecond=0)
    start = target - timedelta(minutes=1)
    end_exclusive = target + timedelta(minutes=22)
    if now >= end_exclusive:
        target += timedelta(hours=1)
        start = target - timedelta(minutes=1)
        end_exclusive = target + timedelta(minutes=22)
    return RenewalScheduleWindow(target, start, end_exclusive)


class NaverMonotonicClock:
    """HTTP Date 표본을 로컬 monotonic 축에 고정해 시스템 시계 변경을 격리합니다."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
    ):
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._lock = threading.Lock()
        self._anchor_epoch: Optional[float] = None
        self._anchor_monotonic: Optional[float] = None
        self._anchor_wall_epoch: Optional[float] = None
        self._synced_monotonic: Optional[float] = None
        self._uncertainty_seconds = float("inf")
        self._last_error = ""

    @staticmethod
    def _fetch_date(timeout: float) -> tuple[float, float, float]:
        started = time.monotonic()
        request = urllib.request.Request(
            NAVER_TIME_URL,
            method="HEAD",
            headers={"User-Agent": "mAuto-renewal-clock/1.0"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_date = response.headers.get("Date", "")
        finished = time.monotonic()
        parsed = email.utils.parsedate_to_datetime(raw_date)
        if parsed is None:
            raise RuntimeError("네이버 응답에 Date 헤더가 없습니다.")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp(), started, finished

    def sync(
        self,
        *,
        sample_count: int = 5,
        timeout: float = 2.0,
        fetch_date: Optional[Callable[[float], tuple[float, float, float]]] = None,
    ) -> bool:
        fetcher = fetch_date or self._fetch_date
        samples: list[tuple[float, float, float]] = []
        errors: list[str] = []
        for _ in range(max(3, int(sample_count))):
            try:
                server_second, started, finished = fetcher(timeout)
                rtt = max(0.0, finished - started)
                midpoint = (started + finished) * 0.5
                # HTTP-date는 초 단위입니다. 해당 초의 중앙을 표본값으로 사용하고
                # 양방향 지연의 절반과 0.5초 양자화 오차를 불확실성에 포함합니다.
                offset = (server_second + 0.5) - midpoint
                samples.append((rtt, offset, midpoint))
            except Exception as exc:
                errors.append(str(exc))
        if not samples:
            with self._lock:
                self._last_error = errors[-1] if errors else "네이버 시간 응답 없음"
            return False

        samples.sort(key=lambda item: item[0])
        selected = samples[: max(1, min(3, len(samples)))]
        offset = float(statistics.median(item[1] for item in selected))
        uncertainty = 0.5 + max(item[0] for item in selected) * 0.5
        anchor_monotonic = self._monotonic()
        anchor_epoch = anchor_monotonic + offset
        with self._lock:
            self._anchor_epoch = anchor_epoch
            self._anchor_monotonic = anchor_monotonic
            self._anchor_wall_epoch = self._wall_time()
            self._synced_monotonic = anchor_monotonic
            self._uncertainty_seconds = uncertainty
            self._last_error = ""
        return True

    def install_for_test(
        self,
        value: datetime,
        *,
        uncertainty_seconds: float = 0.0,
    ) -> None:
        if value.tzinfo is None:
            raise ValueError("test clock value must be timezone-aware")
        anchor = self._monotonic()
        with self._lock:
            self._anchor_epoch = value.timestamp()
            self._anchor_monotonic = anchor
            self._anchor_wall_epoch = self._wall_time()
            self._synced_monotonic = anchor
            self._uncertainty_seconds = max(0.0, float(uncertainty_seconds))
            self._last_error = ""

    def is_fresh(
        self,
        max_age_seconds: float = NAVER_TIME_MAX_AGE_SECONDS,
    ) -> bool:
        with self._lock:
            synced = self._synced_monotonic
            anchor_monotonic = self._anchor_monotonic
            anchor_wall_epoch = self._anchor_wall_epoch
        current_monotonic = self._monotonic()
        if (
            synced is None
            or anchor_monotonic is None
            or anchor_wall_epoch is None
        ):
            return False
        monotonic_elapsed = current_monotonic - anchor_monotonic
        wall_elapsed = self._wall_time() - anchor_wall_epoch
        return bool(
            current_monotonic - synced <= max(0.0, max_age_seconds)
            and abs(wall_elapsed - monotonic_elapsed) <= 2.0
        )

    def now(self) -> datetime:
        with self._lock:
            anchor_epoch = self._anchor_epoch
            anchor_monotonic = self._anchor_monotonic
        if anchor_epoch is None or anchor_monotonic is None:
            raise RuntimeError("네이버 시간이 아직 동기화되지 않았습니다.")
        epoch = anchor_epoch + (self._monotonic() - anchor_monotonic)
        return datetime.fromtimestamp(epoch, tz=KST)

    def to_monotonic(self, value: datetime) -> float:
        if value.tzinfo is None:
            raise ValueError("deadline must be timezone-aware")
        with self._lock:
            anchor_epoch = self._anchor_epoch
            anchor_monotonic = self._anchor_monotonic
        if anchor_epoch is None or anchor_monotonic is None:
            raise RuntimeError("네이버 시간이 아직 동기화되지 않았습니다.")
        return anchor_monotonic + (value.timestamp() - anchor_epoch)

    @property
    def uncertainty_seconds(self) -> float:
        with self._lock:
            return self._uncertainty_seconds

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error
