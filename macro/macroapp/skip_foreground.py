"""게임 창이 **이미 전면**일 때의 컷신 SKIP 정책 — 순수 로직(Mac 단위검증 가능).

## 왜 이 모듈이 필요한가

배경(비활성) 컷신 스킵은 아직 답을 못 찾은 문제라 실험 기계가 붙어 있다. 그런데
그 실험의 안전 가드(`run_guarded_inactive_action`)는 **게임이 전면이면 시도 자체를
포기**한다(`target_already_foreground`) — 배경 입력의 효과를 측정하는 게 목적이라
전면 상태의 시도는 귀속이 불가능하기 때문이다.

문제는 그 포기가 **입력 자체를 없앤다**는 것이다. 컷신 프롬프트가 떠 있는데
매크로가 아무 버튼도 안 누른다. 2026-08-23 실측(logs/macro_2026-08-23.log):

    15:06:26 에 뜬 [S] SKIP 프롬프트가 **116초 동안** 화면에 있었고, 그동안
    매크로는 attach_hold_a 를 432회 "보류(target_already_foreground)" 만 찍고
    **입력을 한 번도 보내지 않았다**. 게임은 그 내내 전면이었다.

⚠️ 수치 단위 주의: 432 는 에피소드가 아니라 **시도 행** 수다(원장 행을 에피소드로
읽으면 피해가 30배로 부풀려진다). 그날 A/S 컷신 에피소드는 15개였고 그중 4개가
입력 0회였다 — 그래도 그 4개가 전체 컷신 시간의 절반을 먹었다.

게임이 전면일 때 A(또는 키보드 S)를 누르면 넘어간다는 것은 2026-07-07 사용자
실측으로 이미 확정돼 있다. 즉 이 구간의 실패는 배경 입력의 한계가 아니라 **답을
아는 상황에서 아무것도 안 보낸 것**이다.

## 이 모듈이 정하는 것

"언제 / 무엇을 / 몇 번 누를지"만 정한다. Win32 호출도 시계도 없다.

- 프롬프트가 보이고 게임이 전면이면 **대조 구간 없이 즉시** 누른다(실험 표본이 아니다).
- 안 사라지면 `repress_seconds` 간격으로 다시 누른다(첫 펄스가 씹혀도 재시도된다 —
  2026-08-22 의 '첫 펄스가 씹히면 재시도 길이 없다' 재발 방지).
- `key_escalation_seconds` 를 넘도록 안 사라지면 가상패드 A 에 더해 **키보드 S
  스캔코드**까지 같이 보낸다(사용자가 손으로 누르는 그 키다).
- `hold_escalation_seconds` 까지 버티면 마지막으로 **홀드** 변형을 쓴다. 사용자는
  "홀드 아니고 그냥 탭"이라고 했지만(2026-09-06), 2026-07-12 메모는 "전면에서 A
  홀드로 넘어감"이었다. 둘이 엇갈리므로 탭을 먼저 충분히 쓰고 안 되면 홀드도
  시도한다 — 사다리라서 어느 쪽이 맞아도 결국 넘어간다.
- 3초까지는 촘촘히(`repress_seconds`), 그 뒤로는 `slow_repress_seconds` 간격으로 늦춘다.
  옛 값(24회 × 0.35초 = 8.4초)은 실측한 116초 정체의 7%밖에 못 덮었다.
- `max_episode_seconds` 를 넘으면 이 에피소드는 포기한다. 안 되는 화면을 무한 연타하면
  다음 화면까지 눌러 버린다.

전면 상태에서만 동작하므로 "비활성 100%"·"깜빡임 0" 불변식과 무관하다 —
창을 전면으로 **올리지 않는다**. 이미 전면인 창에 누를 뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 결정 값. 문자열인 이유는 로그·테스트에서 그대로 읽히게 하기 위해서다.
ACTION_NONE = "none"
ACTION_PAD = "pad"                    # 가상패드 A 탭만
ACTION_PAD_AND_KEY = "pad_key"        # 가상패드 A 탭 + 키보드 S 스캔코드
ACTION_HOLD_AND_KEY = "hold_key"      # A 홀드 + S 홀드(마지막 폴백)


@dataclass
class ForegroundSkipPolicy:
    """전면 상태에서 컷신 프롬프트를 넘기는 재누름 정책."""

    repress_seconds: float = 0.35
    slow_repress_after_seconds: float = 3.0
    slow_repress_seconds: float = 1.0
    key_escalation_seconds: float = 0.0
    hold_escalation_seconds: float = 2.6
    max_presses: int = 120
    max_episode_seconds: float = 90.0

    first_seen_at: Optional[float] = field(default=None, init=False)
    last_press_at: Optional[float] = field(default=None, init=False)
    presses: int = field(default=0, init=False)
    key_presses: int = field(default=0, init=False)
    hold_presses: int = field(default=0, init=False)

    def reset(self) -> None:
        """에피소드(= 프롬프트 한 번 뜸)를 닫는다."""
        self.first_seen_at = None
        self.last_press_at = None
        self.presses = 0
        self.key_presses = 0
        self.hold_presses = 0

    @property
    def active(self) -> bool:
        """이번 에피소드에서 한 번이라도 눌렀는가."""
        return self.presses > 0

    def elapsed(self, now: float) -> float:
        """프롬프트를 처음 본 뒤 지난 시간(초). 아직 안 봤으면 0."""
        if self.first_seen_at is None:
            return 0.0
        return max(0.0, float(now) - float(self.first_seen_at))

    def observe(
        self,
        now: float,
        prompt_visible: bool,
        target_is_foreground: bool,
    ) -> str:
        """이번 프레임에 무엇을 누를지 결정한다.

        - 프롬프트가 사라지면 에피소드를 닫고 ACTION_NONE.
        - 게임이 전면이 아니면 **에피소드를 닫지 않고** ACTION_NONE 만 돌려준다.
          사용자가 잠깐 다른 창을 봤다가 돌아오는 동안 경과 시간이 리셋되면
          키 승격이 영원히 안 일어난다. 배경 구간은 실험 경로가 담당한다.
        """
        now = float(now)
        if not prompt_visible:
            self.reset()
            return ACTION_NONE
        if self.first_seen_at is None:
            # 전면이 아니어도 '언제부터 보였는지'는 기록한다. 단 누르지는 않는다.
            self.first_seen_at = now
        if not target_is_foreground:
            return ACTION_NONE
        if self.presses >= self.max_presses:
            return ACTION_NONE
        elapsed = self.elapsed(now)
        if self.max_episode_seconds > 0 and elapsed >= self.max_episode_seconds:
            # 이 정도로 안 사라지면 컷신이 아니라 화면 정지다. 계속 쏘지 않는다.
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
        if elapsed >= self.hold_escalation_seconds:
            self.key_presses += 1
            self.hold_presses += 1
            return ACTION_HOLD_AND_KEY
        if elapsed >= self.key_escalation_seconds:
            self.key_presses += 1
            return ACTION_PAD_AND_KEY
        return ACTION_PAD
