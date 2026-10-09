"""S 키와 패드 A를 같은 스킵 탭으로 처리하는 재시도 정책.

전송 수단은 GUI가 현재 전면 창에 따라 선택한다. 이 모듈은 짧은 탭의 시점만
결정하며, 오래 걸려도 START나 길게 누르기로 바꾸지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

ACTION_NONE = "none"
ACTION_TAP = "tap"


@dataclass
class ForegroundSkipPolicy:
    """새 프롬프트 관측에서만 탭을 허용하고 재시도 간격을 제한한다."""

    repress_seconds: float = 0.35
    slow_repress_after_seconds: float = 3.0
    slow_repress_seconds: float = 1.0
    max_presses: int = 120
    max_episode_seconds: float = 90.0

    first_seen_at: Optional[float] = field(default=None, init=False)
    last_press_at: Optional[float] = field(default=None, init=False)
    presses: int = field(default=0, init=False)

    def reset(self) -> None:
        self.first_seen_at = None
        self.last_press_at = None
        self.presses = 0

    @property
    def active(self) -> bool:
        return self.presses > 0

    def elapsed(self, now: float) -> float:
        if self.first_seen_at is None:
            return 0.0
        return max(0.0, float(now) - self.first_seen_at)

    def observe(self, now: float, prompt_visible: bool, can_press: bool) -> str:
        now = float(now)
        if not prompt_visible:
            self.reset()
            return ACTION_NONE
        if self.first_seen_at is None:
            self.first_seen_at = now
        if not can_press or self.presses >= self.max_presses:
            return ACTION_NONE
        elapsed = self.elapsed(now)
        if self.max_episode_seconds > 0 and elapsed >= self.max_episode_seconds:
            return ACTION_NONE
        gap = (
            self.slow_repress_seconds
            if elapsed >= self.slow_repress_after_seconds
            else self.repress_seconds
        )
        if self.last_press_at is not None and now - self.last_press_at < gap:
            return ACTION_NONE
        self.presses += 1
        self.last_press_at = now
        return ACTION_TAP
