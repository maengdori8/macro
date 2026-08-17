"""0:2 패배 자동 종료 — 판정·인식 로직 검증.

여기서 막으려는 사고:
  1. 잘못 읽은 스코어 하나가 곧장 종료로 이어진다
  2. 같은 경기를 두 번 세거나(하프타임·미상 스코어 오인) 다음 경기를 못 센다
  3. 비율이 40%를 넘어 비매너 점수가 몰린다

인식은 OCR 이 아니라 글리프 템플릿 매칭이다(실측: winocr 는 실전 '1 2' 를
완벽한 크롭에서도 못 읽었다). 합성 프레임은 임베드된 실물 템플릿으로 만든다 —
그래야 '템플릿과 같은 픽셀이면 반드시 읽힌다'가 검증된다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from macroapp.auto_exit import (  # noqa: E402
    SCORE_UNKNOWN,
    ExitQuota,
    LossTracker,
    classify_glyph,
    read_score_from_frame,
)


# ─── 한 경기당 한 번 세기 ──────────────────────────────────────────────────


def make_tracker(**kw):
    options = dict(target=(0, 2), confirm_count=3, reset_seconds=60.0)
    options.update(kw)
    return LossTracker(**options)


def with_prior(tracker, at=-10.0):
    """경기 초반 0:0 관측 — 진짜 경기는 반드시 이 구간을 지난다."""
    tracker.feed(at, (0, 0))
    return tracker


def test_needs_consecutive_confirmations():
    tracker = with_prior(make_tracker())
    assert tracker.feed(0.0, (0, 2)) is False
    assert tracker.feed(1.0, (0, 2)) is False
    assert tracker.feed(2.0, (0, 2)) is True, "3번 연속이면 확정"


def test_static_misread_alone_cannot_confirm():
    """⚠️ 핵심 방어(리뷰 확정): OCR 파이프라인은 결정적이라 정적 화면 하나의
    일관 오독은 '연속 3회'를 그냥 채운다. 같은 경기에서 0:2 **아닌** 스코어가
    먼저 읽힌 적이 없으면 절대 확정하지 않는다 — 로비·대기 화면의 밝은 글자가
    (0,2) 로 오독돼도 경기 흐름(0:0 구간) 없이는 나가지 않는다."""
    tracker = make_tracker(confirm_count=3)
    for t in range(20):
        assert tracker.feed(float(t), (0, 2)) is False, "선행 스코어 없이 확정됐다"


def test_prior_score_then_target_confirms():
    tracker = make_tracker(confirm_count=2)
    tracker.feed(0.0, (0, 0))          # 경기 초반
    tracker.feed(60.0, (0, 1))         # 한 골 먹힘
    tracker.feed(120.0, (0, 2))
    assert tracker.feed(121.0, (0, 2)) is True


def test_single_misread_does_not_count():
    """오독 한 번(8:2 를 0:2 로) 뒤에 진짜 스코어가 읽히면 streak 이 깨진다."""
    tracker = with_prior(make_tracker())
    tracker.feed(0.0, (0, 2))
    tracker.feed(1.0, (3, 2))     # 실제 스코어
    assert tracker.feed(2.0, (0, 2)) is False
    assert tracker.feed(3.0, (0, 2)) is False   # streak 2 — 아직


def test_none_frames_do_not_break_the_streak():
    """리플레이·오버레이로 한두 프레임 못 읽는 건 정상이다."""
    tracker = with_prior(make_tracker())
    tracker.feed(0.0, (0, 2))
    tracker.feed(1.0, None)
    tracker.feed(2.0, (0, 2))
    assert tracker.feed(3.0, (0, 2)) is True


def test_counted_once_per_match():
    tracker = with_prior(make_tracker(confirm_count=1))
    assert tracker.feed(0.0, (0, 2)) is True
    assert tracker.feed(1.0, (0, 2)) is False, "같은 경기 재확정 금지"
    # 0:3 으로 더 져도, 방치로 끝까지 가도 한 경기는 한 번이다.
    assert tracker.feed(2.0, (0, 3)) is False
    assert tracker.feed(3.0, (0, 2)) is False


def test_halftime_gap_is_not_a_new_match():
    """스코어보드가 잠깐(리셋 시간 미만) 사라져도 같은 경기다."""
    tracker = with_prior(make_tracker(confirm_count=1, reset_seconds=60.0))
    assert tracker.feed(0.0, (0, 2)) is True
    for t in range(1, 50):                      # 49초 동안 안 보임
        tracker.feed(float(t), None)
    assert tracker.feed(50.0, (0, 2)) is False, "하프타임을 새 경기로 오인했다"


def test_next_match_counts_again_after_reset():
    tracker = with_prior(make_tracker(confirm_count=1, reset_seconds=60.0))
    assert tracker.feed(0.0, (0, 2)) is True
    tracker.feed(10.0, None)
    assert tracker.feed(200.0, None) is False   # 60초 넘게 안 보임 → 경기 종료
    tracker.feed(205.0, (0, 0))                 # 다음 경기 초반
    assert tracker.feed(210.0, (0, 2)) is True, "다음 경기를 못 센다"


def test_match_reset_also_clears_the_prior():
    """경기 종료 리셋은 선행 관측도 지운다 — 이전 경기의 0:0 이 다음 경기의
    정적 오독을 허가하면 안 된다."""
    tracker = make_tracker(confirm_count=1, reset_seconds=60.0)
    tracker.feed(0.0, (0, 0))
    tracker.feed(100.0, None)                   # 경기 종료
    assert tracker.feed(101.0, (0, 2)) is False, "이전 경기 선행 관측이 이어졌다"


def test_reset_also_clears_the_streak():
    """경기 종료 리셋은 연속 판정도 지운다 — 다른 경기의 관측을 이어붙이면 안 된다."""
    tracker = make_tracker(confirm_count=2, reset_seconds=60.0)
    with_prior(tracker, at=-1.0)
    tracker.feed(0.0, (0, 2))                   # streak 1
    tracker.feed(100.0, None)                   # 경기 종료(마지막 관측 후 100초)
    tracker.feed(100.5, (0, 0))                 # 새 경기 선행 관측
    assert tracker.feed(101.0, (0, 2)) is False, "이전 경기 streak 이 이어졌다"
    assert tracker.feed(102.0, (0, 2)) is True


def test_resume_observation_keeps_the_latch():
    """정지→재시작(트래커 유지)에도 확정된 경기는 다시 세지 않는다(리뷰 확정 결함).

    래치·마지막 관측 시각은 남기고 streak·선행 관측만 비운다 — 래치는 경기 종료
    (스코어 부재 reset_seconds)로만 풀린다."""
    tracker = with_prior(make_tracker(confirm_count=1))
    assert tracker.feed(0.0, (0, 2)) is True    # 확정(방치 결정이었다고 하자)
    tracker.resume_observation()                # 사용자가 정지→재시작
    tracker.feed(5.0, (0, 0))                   # 같은 경기 화면 계속
    assert tracker.feed(6.0, (0, 2)) is False, "재시작만으로 같은 경기가 두 번 세어졌다"
    # 경기가 진짜 끝난 뒤에는 다시 센다.
    tracker.feed(100.0, None)
    tracker.feed(105.0, (0, 0))
    assert tracker.feed(110.0, (0, 2)) is True


def test_unknown_reading_keeps_the_match_alive():
    """미상 스코어(3:1 등)는 '진행 중' 증거다 — 오래 이어져도 래치가 안 풀린다."""
    tracker = with_prior(make_tracker(confirm_count=1, reset_seconds=60.0))
    assert tracker.feed(0.0, (0, 2)) is True     # 확정(방치했다고 하자)
    for t in range(1, 200):                      # 199초 동안 미상 스코어만 보임
        tracker.feed(float(t), SCORE_UNKNOWN)
    assert tracker.feed(200.0, (0, 2)) is False, "미상 구간을 경기 종료로 오인했다"


def test_unknown_does_not_break_the_streak():
    tracker = with_prior(make_tracker())
    tracker.feed(0.0, (0, 2))
    tracker.feed(1.0, SCORE_UNKNOWN)
    tracker.feed(2.0, (0, 2))
    assert tracker.feed(3.0, (0, 2)) is True


def test_unknown_does_not_count_as_prior():
    """미상은 '다른 스코어를 봤다'는 증거가 아니다 — 선행 조건을 채우면 안 된다."""
    tracker = make_tracker(confirm_count=1)
    tracker.feed(0.0, SCORE_UNKNOWN)
    assert tracker.feed(1.0, (0, 2)) is False


# ─── 40% 쿼터 ──────────────────────────────────────────────────────────────


def test_twenty_losses_exit_exactly_eight():
    quota = ExitQuota(0.4)
    exits = [quota.register_loss() for _ in range(20)]
    assert sum(exits) == 8, "사용자 명세: 20판이면 8판"
    # 어느 시점에서 잘라도 비율을 넘지 않는다(비매너가 몰리지 않는다).
    running = 0
    for index, decision in enumerate(exits, 1):
        running += int(decision)
        assert running <= index * 0.4 + 1e-9


def test_quota_boundaries():
    never = ExitQuota(0.0)
    assert not any(never.register_loss() for _ in range(10))
    always = ExitQuota(1.0)
    assert all(always.register_loss() for _ in range(10))
    with pytest.raises(ValueError):
        ExitQuota(1.5)


def test_set_ratio_preserves_counts():
    """서버 운영 설정으로 비율이 바뀌어도 세션 카운트는 이어진다."""
    quota = ExitQuota(0.4)
    for _ in range(5):
        quota.register_loss()          # 0.4 면 5판 중 2판 종료(3·5번째)
    assert (quota.lost_games, quota.exits_done) == (5, 2)

    quota.set_ratio(1.0)
    assert quota.lost_games == 5, "비율 변경이 카운트를 지웠다"
    # 새 비율은 다음 판부터 전체 판수 기준으로 적용된다: floor(6×1.0)=6 > 2 → 종료.
    assert quota.register_loss() is True

    quota.set_ratio(0.0)
    assert quota.register_loss() is False, "0% 로 내렸는데 계속 나간다"


def test_set_ratio_rejects_bad_values():
    quota = ExitQuota(0.4)
    for bad in (1.5, -0.1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            quota.set_ratio(bad)
    assert quota.ratio == 0.4, "거부된 값이 비율을 바꿨다"


# ─── 프레임 → 스코어 (글리프 매칭) ─────────────────────────────────────────

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from macroapp.auto_exit import _load_glyphs  # noqa: E402

REGION = (0.150, 0.050, 0.210, 0.130)


def draw_box(frame, x, y, glyph=None, size=35, bright=235):
    """흰 스코어 박스 하나를 그린다. glyph(20x28 bool)를 주면 숫자로 판다."""
    frame[y:y + size, x:x + size] = bright
    if glyph is not None:
        g = cv2.resize(glyph.astype(np.uint8) * 255, (20, 28),
                       interpolation=cv2.INTER_NEAREST) > 127
        oy, ox = y + (size - 28) // 2, x + (size - 20) // 2
        region = frame[oy:oy + 28, ox:ox + 20]
        region[g] = 30                 # 어두운 숫자
    return frame


def synth_frame(left=None, right=None, bright=235):
    """실측 배치(왼쪽 상단)와 같은 자리에 박스 두 개를 놓은 합성 프레임."""
    frame = np.full((1016, 1920), 40, dtype=np.uint8)
    if left is not None or right is not None:
        if left is not None:
            draw_box(frame, 300, 70, left, bright=bright)
        if right is not None:
            draw_box(frame, 346, 70, right, bright=bright)
    return frame


@pytest.fixture(scope="module")
def glyphs():
    loaded = _load_glyphs()
    if not loaded:
        pytest.skip("글리프 템플릿을 불러올 수 없습니다")
    return loaded


def test_reads_the_target_score(glyphs):
    frame = synth_frame(glyphs[0][0], glyphs[2][0])
    assert read_score_from_frame(frame, REGION) == (0, 2)


def test_reads_other_scores(glyphs):
    assert read_score_from_frame(synth_frame(glyphs[1][0], glyphs[2][0]), REGION) == (1, 2)
    assert read_score_from_frame(synth_frame(glyphs[0][0], glyphs[0][1]), REGION) == (0, 0)


def test_full_range_and_tv_range_both_read(glyphs):
    """녹화(tv 레인지, 흰색≈235)와 실캡처(255)가 달라도 같은 결과여야 한다."""
    for bright in (235, 255):
        frame = synth_frame(glyphs[0][0], glyphs[2][0], bright=bright)
        assert read_score_from_frame(frame, REGION) == (0, 2), f"밝기 {bright} 실패"


def test_unknown_digit_is_reported_as_unknown_not_absent(glyphs):
    """템플릿에 없는 숫자(3~9)는 '미상'이지 '스코어보드 없음'이 아니다.

    없음으로 처리하면 3:1 화면이 오래 이어질 때 경기 종료로 오인돼 래치가 풀리고
    같은 경기가 두 번 세어진다."""
    checker = np.indices((28, 20)).sum(axis=0) % 2 == 0   # 어떤 숫자도 아닌 무늬
    frame = synth_frame(glyphs[0][0], checker)
    assert read_score_from_frame(frame, REGION) == SCORE_UNKNOWN


def test_no_scoreboard_is_none(glyphs):
    assert read_score_from_frame(synth_frame(), REGION) is None


def test_single_box_is_unknown_not_absent(glyphs):
    """화면 전환 중 박스가 하나만 보이는 순간 — 없음으로 보면 종료 타이머가 돈다."""
    frame = synth_frame(left=glyphs[0][0])
    assert read_score_from_frame(frame, REGION) == SCORE_UNKNOWN


def test_templates_do_not_cross_classify(glyphs):
    """임베드된 실물 템플릿끼리 서로 다른 숫자로 분류되면 안 된다(오탐 근원)."""
    for digit, templates in glyphs.items():
        for template in templates:
            assert classify_glyph(template, glyphs) == digit


def test_name_text_next_to_the_box_is_not_a_box(glyphs):
    """팀명 흰 글자가 박스에 붙어 있어도 박스 개수를 흐리면 안 된다(실측 결함)."""
    frame = synth_frame(glyphs[0][0], glyphs[2][0])
    # 박스 왼쪽에 글자 획처럼 가는 밝은 세로줄들을 붙인다
    for x in (280, 284, 288, 292):
        frame[75:100, x:x + 2] = 235
    assert read_score_from_frame(frame, REGION) == (0, 2)


def test_read_failures_collapse_to_none():
    assert read_score_from_frame(None, REGION) is None
    frame = np.zeros((100, 100), dtype=np.uint8)
    assert read_score_from_frame(frame, (0.5, 0.5, 0.5, 0.5)) is None
