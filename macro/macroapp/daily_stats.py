"""'오늘 몇 판 돌렸나'의 날짜 경계와 표시 문자열을 다루는 순수 로직.

저장은 UTC로 하고 날짜 판정은 조회할 때 합니다. 기록 시점에 계산한 날짜를 컬럼으로
굳혀두면, 시스템 시계가 NTP 보정이나 절전 복귀로 흔들렸을 때 잘못된 날짜가 영구히
남아 나중에 고칠 수 없기 때문입니다. macroapp.mining이 넥슨 기록을 다루는 방식과도
같은 규칙이라, 두 화면의 '오늘'이 어긋나지 않습니다.

Windows 의존성이 없어 Mac에서도 그대로 단위 검증할 수 있습니다.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from macroapp.mining import KST


def today_kst(now: Optional[dt.datetime] = None) -> dt.date:
    """KST 기준 오늘 날짜. now를 주입할 수 있어 테스트가 실제 시계를 안 씁니다."""

    moment = dt.datetime.now(KST) if now is None else now
    if moment.tzinfo is None:
        raise ValueError("now는 타임존이 붙은 datetime이어야 합니다.")
    return moment.astimezone(KST).date()


def utc_iso(moment: dt.datetime) -> str:
    """저장용 UTC ISO 문자열. mining.py의 match_date_utc와 같은 표기입니다."""

    if moment.tzinfo is None:
        raise ValueError("moment는 타임존이 붙은 datetime이어야 합니다.")
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_bounds_utc(day: dt.date) -> tuple[str, str]:
    """KST 하루 [자정, 익일 자정)을 UTC ISO 문자열 쌍으로 반환합니다.

    반열림 구간이라 자정에 걸친 기록이 두 날에 중복되지 않습니다.
    """

    start = dt.datetime.combine(day, dt.time.min, KST)
    end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, KST)
    return utc_iso(start), utc_iso(end)


def format_matches(session_matches: int, today_matches: Optional[int]) -> str:
    """세션 판수와 오늘 판수를 카드 한 줄에 담습니다.

    today_matches가 None이면 '기록을 못 읽었다'는 뜻이라 0으로 단정하지 않고
    가운뎃점을 찍습니다. 0판과 구분되지 않으면 사용자가 잘못 믿게 됩니다.
    """

    today_text = "—" if today_matches is None else f"{int(today_matches):,}"
    return f"{int(session_matches):,}  ·  오늘 {today_text}"
