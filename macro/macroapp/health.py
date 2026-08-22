"""가동률 감시 — '켜져 있는데 아무것도 안 하고 서 있는 시간'을 잡는다.

왜 이게 최우선인가(2026-08-22 로그 실측): 하루 판수를 가장 크게 깎는 건 컷신(판당 ~1분)이
아니라 매크로가 멈춰 서 있는 시간이다. 8-18 프로 로그에서 게임 창이 사라진 뒤 **3시간
(그날 51판의 30%)** 동안 '창을 찾지 못했습니다'만 7,509회 찍고 사용자가 돌아올 때까지
그대로 있었다. 컷신을 완벽히 스킵해도 하루 2~4판인데, 이건 16판이다.

이 모듈은 **순수 로직**이다(시각을 인자로 받는다) — Windows 없이 단위 검증한다.
판정만 하고, 알림/재탐색 같은 부작용은 호출부(gui)가 한다.

두 가지 정지를 구분한다:
  - ``STALL_WINDOW``   : 대상 창을 못 찾는다(게임 종료·크래시·다른 계정 로그인).
                         → 사용자가 게임을 다시 켜야 회복된다. 알림이 유일한 답.
  - ``STALL_PROGRESS`` : 창은 있는데 진행이 없다(같은 화면에 갇힘·팝업·서버 점검 대기).
                         → 창 재결합/캡처 재시작으로 스스로 회복될 여지가 있다.
"""

from __future__ import annotations

from typing import Optional

STALL_NONE = "none"
STALL_WINDOW = "window"
STALL_PROGRESS = "progress"


class StallMonitor:
    """진행 신호를 받아 '멈춰 있음'을 판정하고, 알림 시점을 정한다.

    - ``note_progress``  : 판이 늘거나 타겟을 눌렀을 때. 정상 진행의 증거.
    - ``note_window``    : 매 루프. 창을 찾았는지(bool).
    - ``poll``           : 지금 상태와 '알림을 내보낼지'를 돌려준다.

    알림은 처음 ``first_alert_seconds`` 에 한 번, 그 뒤 ``repeat_alert_seconds`` 마다
    한 번만 낸다(로그·디스코드를 도배하지 않는다 — 8-18 의 7,509줄이 그 반면교사다).
    """

    def __init__(
        self,
        *,
        first_alert_seconds: float = 180.0,
        repeat_alert_seconds: float = 900.0,
        progress_grace_seconds: float = 420.0,
    ) -> None:
        self.first_alert_seconds = float(first_alert_seconds)
        self.repeat_alert_seconds = float(repeat_alert_seconds)
        # 진행 없음은 창 유실보다 관대해야 한다 — 한 경기는 10분 넘게 걸리고 그동안
        # '진행 신호'가 드물 수 있다. 경기 한 판보다 짧게 잡으면 오탐이 된다.
        self.progress_grace_seconds = float(progress_grace_seconds)
        self.reset(0.0)

    def reset(self, now: float) -> None:
        """시작/재시작 시점. 모든 타이머를 지금으로 맞춘다."""

        self._last_progress_at = float(now)
        self._window_lost_since: Optional[float] = None
        self._state = STALL_NONE
        self._alerted_at: Optional[float] = None
        self._alert_count = 0

    # ── 입력 ────────────────────────────────────────────────────────────────
    def note_progress(self, now: float) -> None:
        """실제로 뭔가 진행됐다(판 증가·타겟 입력). 두 타이머를 모두 되살린다."""

        self._last_progress_at = float(now)
        if self._state != STALL_NONE:
            self._state = STALL_NONE
            self._alerted_at = None
            self._alert_count = 0

    def note_window(self, found: bool, now: float) -> None:
        """창 탐색 결과. 못 찾은 '시작 시각'만 붙잡아 둔다(연속 실패 길이를 재려고)."""

        if found:
            if self._window_lost_since is not None:
                self._window_lost_since = None
                if self._state == STALL_WINDOW:
                    # 창이 돌아왔다 = 회복. 다음 정지는 처음부터 다시 센다.
                    self._state = STALL_NONE
                    self._alerted_at = None
                    self._alert_count = 0
                    self._last_progress_at = float(now)
        elif self._window_lost_since is None:
            self._window_lost_since = float(now)

    # ── 판정 ────────────────────────────────────────────────────────────────
    def stalled_seconds(self, now: float) -> float:
        """현재 정지가 이어진 시간(초). 정지가 아니면 0."""

        if self._window_lost_since is not None:
            return max(0.0, float(now) - self._window_lost_since)
        gap = float(now) - self._last_progress_at
        return gap if gap >= self.progress_grace_seconds else 0.0

    def classify(self, now: float) -> str:
        if self._window_lost_since is not None:
            if float(now) - self._window_lost_since >= self.first_alert_seconds:
                return STALL_WINDOW
            return STALL_NONE
        if float(now) - self._last_progress_at >= self.progress_grace_seconds:
            return STALL_PROGRESS
        return STALL_NONE

    def poll(self, now: float) -> tuple[str, bool, float]:
        """(상태, 이번에 알릴까, 정지 지속 초)를 돌려준다.

        같은 정지에 대해 처음 한 번, 그 뒤 repeat_alert_seconds 마다 True 를 낸다.
        """

        state = self.classify(now)
        if state == STALL_NONE:
            if self._state != STALL_NONE:
                self._state = STALL_NONE
                self._alerted_at = None
                self._alert_count = 0
            return STALL_NONE, False, 0.0

        duration = self.stalled_seconds(now)
        if state != self._state:
            # 종류가 바뀌면(진행 없음 → 창 유실) 새 정지로 본다.
            self._state = state
            self._alerted_at = None
            self._alert_count = 0

        should_alert = False
        if self._alerted_at is None:
            should_alert = True
        elif float(now) - self._alerted_at >= self.repeat_alert_seconds:
            should_alert = True
        if should_alert:
            self._alerted_at = float(now)
            self._alert_count += 1
        return state, should_alert, duration

    @property
    def alert_count(self) -> int:
        return self._alert_count


def format_duration(seconds: float) -> str:
    """알림 문구용 — '12분', '1시간 3분'."""

    total = int(max(0.0, seconds))
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours}시간 {minutes}분"
    if hours:
        return f"{hours}시간"
    if minutes:
        return f"{minutes}분"
    return f"{total}초"


def stall_message(state: str, seconds: float) -> str:
    """구매자가 디스코드 상태로 보는 한 줄."""

    if state == STALL_WINDOW:
        return f"⚠️ 게임 창을 {format_duration(seconds)}째 찾지 못했습니다 — 게임이 꺼졌는지 확인하세요"
    if state == STALL_PROGRESS:
        return f"⚠️ {format_duration(seconds)}째 경기 진행이 없습니다 — 화면이 멈췄는지 확인하세요"
    return ""
