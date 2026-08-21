"""'SKIP 하려면 아무 키나 누르세요' 프롬프트 — 실험 없이 바로 START 누르기 판정.

왜 따로 있나
    OCR 워커(gui._try_skip)는 일반 SKIP 프롬프트를 '비활성 입력 실험'으로 보낸다
    (3초 무입력 대조 → 후보 버튼 하나 → 결과 창, 5번에 1번은 입력 없는 대조군).
    그 절차는 **어떤 입력이 통하는지 모르는** 프롬프트를 위한 것이다. "아무 키나
    누르세요" 화면은 답이 알려져 있다 — START 로 넘어간다(7-28 실측 53/53, 사용자
    확인). 모르는 척 6초를 기다릴 이유가 없고, 대조군 차례엔 아예 안 누른다.

    이 모듈은 그 판정(연속 확인 → 누름 → 재누름 간격)만 순수 로직으로 분리해 둔
    것이다. Windows·winocr 없이 단위 검증된다. 입력 자체는 gui 가 보낸다.

규칙
    - 같은 프롬프트가 ``consensus`` 회 연속 읽혀야 누른다(단발 OCR 노이즈 차단).
      OCR 간격이 0.3초라 2회면 0.3초 지연 — 체감 0.
    - 프롬프트가 계속 보이면 ``repress_seconds`` 마다 다시 누른다(첫 펄스가 씹힌
      경우 대비). 7-28 데이터는 에피소드당 평균 11펄스로도 부작용이 없었다.
    - 안 보이면 연속 카운트를 비운다. 마지막 누름 시각은 남긴다 — 직후 새
      에피소드가 0.8초 안에 또 뜨면 그만큼만 늦게 누른다(이중 펄스 방지).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

ACTION_IDLE = "idle"    # 프롬프트 없음
ACTION_WAIT = "wait"    # 보이지만 아직 연속 확인 중
ACTION_PRESS = "press"  # 지금 START 를 누른다
ACTION_HOLD = "hold"    # 확인됐지만 재누름 간격 안 — 이번엔 안 누른다


@dataclass
class AnyKeyStartPolicy:
    consensus: int = 2
    repress_seconds: float = 0.8
    streak: int = 0
    last_press_at: float = -math.inf

    def observe(self, now: float, seen: bool) -> str:
        """이번 OCR 패스의 관측을 넣고 할 일을 돌려준다."""

        if not seen:
            self.streak = 0
            return ACTION_IDLE
        self.streak += 1
        if self.streak < max(1, int(self.consensus)):
            return ACTION_WAIT
        if now - self.last_press_at >= float(self.repress_seconds):
            self.last_press_at = now
            return ACTION_PRESS
        return ACTION_HOLD

    def reset(self) -> None:
        """자동화 (재)시작 때 — 이전 실행의 누름 시각이 첫 누름을 늦추지 않게."""

        self.streak = 0
        self.last_press_at = -math.inf
