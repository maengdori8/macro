"""전면 상태 컷신 SKIP 정책 단위 검증(순수 로직 — Windows 불필요).

이 테스트가 고정하는 계약:
1. 게임이 전면이면 **대조 구간 없이 첫 프레임에 즉시** 누른다.
2. 안 사라지면 정해진 간격으로 재누름한다(첫 펄스가 씹혀도 재시도가 있다).
3. 오래 버티면 키보드 S 까지 같이 보낸다.
4. 배경 구간은 누르지 않지만 **경과 시간을 리셋하지 않는다**.
5. 무한 연타하지 않는다.
"""

from __future__ import annotations

from macroapp.skip_foreground import (
    ACTION_NONE,
    ACTION_PAD,
    ACTION_PAD_AND_KEY,
    ForegroundSkipPolicy,
)


def test_전면이면_첫_프레임에_바로_누른다():
    """⚠️ 기본값은 **첫 발부터 패드 A + 키보드 S 를 같이** 보낸다.

    2026-07-02 실측: "가상 A는 탭·1s 홀드 모두 무시, 전면전환+키보드 s '탭'은 통함".
    전면에서 성공이 기록된 유일한 입력이 키보드 S 탭이므로 그걸 뒤로 미루면 안 된다.
    """
    policy = ForegroundSkipPolicy()
    assert policy.key_escalation_seconds == 0.0
    assert policy.observe(100.0, True, True) == ACTION_PAD_AND_KEY
    assert policy.presses == 1
    assert policy.key_presses == 1


def test_배경이면_누르지_않는다():
    policy = ForegroundSkipPolicy()
    assert policy.observe(100.0, True, False) == ACTION_NONE
    assert policy.presses == 0


def test_프롬프트가_없으면_아무것도_안_한다():
    policy = ForegroundSkipPolicy()
    assert policy.observe(100.0, False, True) == ACTION_NONE
    assert policy.presses == 0


def test_재누름_간격_안에서는_다시_누르지_않는다():
    policy = ForegroundSkipPolicy(repress_seconds=0.35, key_escalation_seconds=99.0)
    assert policy.observe(100.0, True, True) == ACTION_PAD
    assert policy.observe(100.2, True, True) == ACTION_NONE
    assert policy.observe(100.36, True, True) == ACTION_PAD
    assert policy.presses == 2


def test_안_사라지면_키보드_S_까지_승격한다():
    policy = ForegroundSkipPolicy(
        repress_seconds=0.3, key_escalation_seconds=1.2
    )
    assert policy.observe(0.0, True, True) == ACTION_PAD
    assert policy.observe(0.5, True, True) == ACTION_PAD
    assert policy.observe(1.0, True, True) == ACTION_PAD
    # 1.2초를 넘긴 순간부터 패드 + 키보드
    assert policy.observe(1.5, True, True) == ACTION_PAD_AND_KEY
    assert policy.observe(1.9, True, True) == ACTION_PAD_AND_KEY
    assert policy.key_presses == 2


def test_배경_구간은_경과시간을_리셋하지_않는다():
    """사용자가 잠깐 다른 창을 봐도 키 승격 시계는 계속 간다.

    리셋하면 alt-tab 이 잦은 PC 에서 키 승격이 영원히 안 일어난다.
    """
    policy = ForegroundSkipPolicy(
        repress_seconds=0.3, key_escalation_seconds=1.0
    )
    assert policy.observe(0.0, True, False) == ACTION_NONE   # 배경에서 프롬프트 등장
    assert policy.observe(0.5, True, False) == ACTION_NONE
    # 1.0초가 지난 뒤 전면으로 돌아오면 곧바로 키까지 승격돼야 한다.
    assert policy.observe(1.3, True, True) == ACTION_PAD_AND_KEY


def test_프롬프트가_사라지면_에피소드가_닫힌다():
    policy = ForegroundSkipPolicy(repress_seconds=0.0, key_escalation_seconds=99.0)
    policy.observe(0.0, True, True)
    policy.observe(0.1, True, True)
    assert policy.presses == 2
    assert policy.observe(0.2, False, True) == ACTION_NONE
    assert policy.presses == 0
    assert policy.first_seen_at is None
    # 다음 컷신은 다시 처음부터 — 승격 상태를 물려받지 않는다.
    assert policy.observe(0.3, True, True) == ACTION_PAD


def test_무한_연타하지_않는다():
    policy = ForegroundSkipPolicy(
        repress_seconds=0.0, key_escalation_seconds=99.0, max_presses=3
    )
    assert policy.observe(0.0, True, True) == ACTION_PAD
    assert policy.observe(0.1, True, True) == ACTION_PAD
    assert policy.observe(0.2, True, True) == ACTION_PAD
    assert policy.observe(0.3, True, True) == ACTION_NONE
    assert policy.presses == 3


def test_active_는_한_번이라도_눌렀는지를_말한다():
    policy = ForegroundSkipPolicy()
    assert not policy.active
    policy.observe(0.0, True, False)
    assert not policy.active          # 배경에서는 안 눌렀다
    policy.observe(0.1, True, True)
    assert policy.active


def test_홀드까지_사다리로_승격한다():
    """사용자는 '탭'이라 했고 07-12 메모는 '홀드'였다 — 사다리라 둘 다 결국 시도한다."""
    from macroapp.skip_foreground import ACTION_HOLD_AND_KEY

    policy = ForegroundSkipPolicy(
        repress_seconds=0.3,
        key_escalation_seconds=1.0,
        hold_escalation_seconds=2.0,
    )
    assert policy.observe(0.0, True, True) == ACTION_PAD
    assert policy.observe(1.1, True, True) == ACTION_PAD_AND_KEY
    assert policy.observe(2.1, True, True) == ACTION_HOLD_AND_KEY
    assert policy.hold_presses == 1
    assert policy.key_presses == 2
    policy.reset()
    assert policy.hold_presses == 0


def test_오래_버티면_재누름_간격이_늘어난다():
    """옛 값(24회 × 0.35초 = 8.4초)은 실측 116초 정체의 7%밖에 못 덮었다."""
    policy = ForegroundSkipPolicy(
        repress_seconds=0.35,
        slow_repress_after_seconds=3.0,
        slow_repress_seconds=1.0,
        key_escalation_seconds=99.0,
        hold_escalation_seconds=99.0,
    )
    t = 0.0
    presses = 0
    while t <= 60.0:
        if policy.observe(t, True, True) != ACTION_NONE:
            presses += 1
        t += 0.3
    # 0~3초는 촘촘히(≈9발), 3~60초는 1초 간격(≈57발)
    # 0.3초 샘플링이라 1.0초 간격은 실효 1.2초가 된다.
    assert 45 <= presses <= 72, presses


def test_시간_상한을_넘으면_멈춘다():
    policy = ForegroundSkipPolicy(
        repress_seconds=0.0, max_episode_seconds=10.0, key_escalation_seconds=99.0
    )
    assert policy.observe(0.0, True, True) != ACTION_NONE
    assert policy.observe(9.9, True, True) != ACTION_NONE
    assert policy.observe(10.0, True, True) == ACTION_NONE
    assert policy.observe(30.0, True, True) == ACTION_NONE
