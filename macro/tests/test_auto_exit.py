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
    ExitRules,
    LossTracker,
    classify_glyph,
    read_score_from_frame,
)


# ─── 한 경기당 한 번 세기 ──────────────────────────────────────────────────


def make_tracker(**kw):
    options = dict(deficit=2, confirm_count=3, reset_seconds=60.0)
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


def test_any_two_goal_deficit_counts():
    """0:2 만이 아니라 점수차 2 이상 열세 전부가 대상이다(0:3, 1:3, 2:4…)."""
    for losing in ((0, 2), (0, 3), (1, 3), (2, 4)):
        tracker = with_prior(make_tracker(confirm_count=1))
        assert tracker.feed(0.0, losing) is True, f"{losing} 이 확정되지 않았다"


def test_three_goal_scores_are_readable_now():
    """숫자 3 글리프가 추가돼 0:3 / 1:3 같은 스코어도 판정된다(2026-08-17 실측 2:3 화면)."""
    from macroapp.score_glyphs import GLYPH_PNGS_B64
    assert "3" in GLYPH_PNGS_B64, "숫자 3 템플릿이 사라졌다"
    tracker = with_prior(make_tracker(confirm_count=1))
    assert tracker.feed(0.0, (0, 3)) is True


def test_glyph_templates_do_not_collide():
    """서로 다른 숫자의 IoU 가 임계값(0.65) 아래여야 한다.

    숫자를 추가할 때마다 교차 IoU 가 올라간다 - '3' 이 들어오면서 최악값이
    0.51 → 0.606 이 됐다. 여기서 더 붙었다가는 오인식이 시작되므로 고정해 둔다.
    """
    from macroapp.auto_exit import _load_glyphs, _GLYPH_IOU_THRESHOLD
    glyphs = _load_glyphs()
    worst, pair = 0.0, None
    for a, masks_a in glyphs.items():
        for b, masks_b in glyphs.items():
            if a >= b:
                continue
            for ma in masks_a:
                for mb in masks_b:
                    inter = (ma & mb).sum()
                    union = (ma | mb).sum() or 1
                    score = inter / union
                    if score > worst:
                        worst, pair = score, (a, b)
    assert worst < _GLYPH_IOU_THRESHOLD, f"{pair} 가 임계값을 넘었다: {worst:.3f}"


def test_leading_or_close_scores_never_trigger():
    """내가 이기거나(2:0) 1점차 열세(1:2)는 절대 확정되면 안 된다."""
    tracker = with_prior(make_tracker(confirm_count=1))
    for safe in ((0, 0), (2, 0), (3, 1), (1, 2), (2, 3)):
        assert tracker.feed(0.0, safe) is False, f"{safe} 에서 나가 버렸다"


def test_worsening_deficit_keeps_the_streak():
    """0:2 → 0:3 처럼 열세가 깊어져도 연속 판정이 이어진다(같은 경기다)."""
    tracker = with_prior(make_tracker(confirm_count=3))
    tracker.feed(0.0, (0, 2))
    tracker.feed(1.0, (0, 3))
    assert tracker.feed(2.0, (1, 3)) is True


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


# ---------------------------------------------------------------------------
# '숫자 미상' 표본 자동 저장 — 4~9 글리프를 실전에서 모은다
# ---------------------------------------------------------------------------


def test_crop_score_region_matches_read_path_and_rejects_tiny():
    np = pytest.importorskip("numpy")
    from macroapp.auto_exit import crop_score_region

    gray = np.zeros((1080, 1920), dtype=np.uint8)
    crop = crop_score_region(gray, (0.150, 0.050, 0.210, 0.130))
    assert crop.shape == (int(1080 * 0.130) - int(1080 * 0.050), int(1920 * 0.210) - int(1920 * 0.150))
    assert crop_score_region(gray, (0.5, 0.5, 0.501, 0.501)) is None


def test_unknown_score_crops_are_saved_with_cap_and_interval(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from unittest.mock import Mock

    from macroapp import gui

    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app.base_dir = tmp_path
    app._score_unknown_dumped = 0
    app._score_unknown_dumped_at = float("-inf")
    app._log_to_file_only = Mock()
    gray = np.full((1080, 1920), 40, dtype=np.uint8)

    app._save_score_unknown_crop(gray, 100.0, fresh=True)      # 새 미상 구간 → 저장
    app._save_score_unknown_crop(gray, 101.0, fresh=False)     # 간격 안 → 안 저장
    app._save_score_unknown_crop(gray, 100.0 + gui.AUTO_EXIT_UNKNOWN_DUMP_INTERVAL_SECONDS, fresh=False)  # 간격 지남 → 저장
    files = sorted((tmp_path / "logs" / "score_unknown").glob("score_*.png"))
    assert len(files) == 2
    assert app._score_unknown_dumped == 2

    # 세션 상한을 넘기면 더 안 쓴다.
    app._score_unknown_dumped = gui.AUTO_EXIT_UNKNOWN_DUMP_LIMIT
    app._save_score_unknown_crop(gray, 9999.0, fresh=True)
    assert len(list((tmp_path / "logs" / "score_unknown").glob("score_*.png"))) == 2


# ---------------------------------------------------------------------------
# 종료 규칙 세 개(기본·대량 실점·후반) + 구매자별 설정 파싱
# ---------------------------------------------------------------------------


def _rules(**over):
    from macroapp.auto_exit import ExitRules

    base = dict(base_deficit=2, hard_deficit=3, late_minute=70, late_deficit=1)
    base.update(over)
    return ExitRules(**base)


def test_rules_classify_priority_and_minute_gate():
    from macroapp.auto_exit import KIND_BASE, KIND_HARD, KIND_LATE

    r = _rules()
    assert r.classify(0, 3, None) == KIND_HARD       # 3점차 → 무조건(분 무관)
    assert r.classify(1, 4, 10) == KIND_HARD
    assert r.classify(0, 2, None) == KIND_BASE       # 2점차 → 기본(비율)
    assert r.classify(0, 2, 80) == KIND_BASE         # 후반이어도 2점차는 base 가 먼저
    assert r.classify(0, 1, 70) == KIND_LATE         # 70분 1점차 → 후반
    assert r.classify(0, 1, 69) is None              # 69분은 아직
    assert r.classify(0, 1, None) is None            # 시계를 모르면 후반 규칙 없음
    assert r.classify(1, 1, 90) is None              # 동점
    assert r.classify(2, 0, 90) is None              # 내가 앞섬
    # 끄기: 0 이면 그 규칙은 없다.
    assert _rules(hard_deficit=0).classify(0, 5, None) == KIND_BASE
    assert _rules(late_minute=0).classify(0, 1, 89) is None


def test_tracker_without_rules_behaves_like_before():
    """rules 를 안 주면 기본 규칙 하나 — hard/late 는 꺼져 있다(기존 테스트의 의미 보존)."""
    from macroapp.auto_exit import LossTracker

    t = LossTracker(deficit=2, confirm_count=1, reset_seconds=60)
    assert t.rules.hard_deficit == 0 and t.rules.late_minute == 0
    t.feed(0.0, (0, 0))
    assert t.observe(1.0, (0, 1), 80) is None        # late 꺼짐
    assert t.observe(2.0, (0, 2)) == "base"


def test_hard_rule_fires_even_after_base_decided_to_stay():
    """0:2 로 '방치' 결정된 판이 0:3 으로 벌어지면 비율 무관하게 hard 가 한 번 더 확정된다."""
    from macroapp.auto_exit import KIND_BASE, KIND_HARD, LossTracker

    t = LossTracker(confirm_count=3, reset_seconds=60, rules=_rules())
    t.feed(0.0, (0, 0))                               # 선행 스코어
    assert [t.observe(1.0 + i, (0, 2)) for i in range(3)] == [None, None, KIND_BASE]
    assert t.observe(5.0, (0, 2)) is None             # 같은 경기 재확정 없음(래치)
    assert [t.observe(6.0 + i, (0, 3)) for i in range(3)] == [None, None, KIND_HARD]
    assert t.observe(10.0, (0, 3)) is None            # hard 도 한 번만
    assert t.observe(11.0, (0, 4)) is None


def test_hard_first_latches_quota_rules_too():
    from macroapp.auto_exit import KIND_HARD, LossTracker

    t = LossTracker(confirm_count=2, reset_seconds=60, rules=_rules())
    t.feed(0.0, (0, 1))
    assert [t.observe(1.0, (0, 3)), t.observe(2.0, (0, 3))] == [None, KIND_HARD]
    # 같은 경기에서 뒤늦게 2점차로 읽혀도 쿼터 규칙이 다시 서지 않는다.
    assert t.observe(3.0, (0, 2)) is None
    assert t.observe(4.0, (0, 2)) is None


def test_late_rule_needs_minute_and_shares_the_quota_latch():
    from macroapp.auto_exit import KIND_LATE, LossTracker

    t = LossTracker(confirm_count=2, reset_seconds=60, rules=_rules())
    t.feed(0.0, (0, 0), 10)                           # 0:0 — 1점차보다 나은 스코어(선행 증거)
    t.feed(0.5, (0, 1), 30)                           # 전반 0:1 — 규칙 없음
    assert t.observe(1.0, (0, 1), 71) is None
    assert t.observe(2.0, (0, 1), 71) == KIND_LATE
    assert t.observe(3.0, (0, 1), 80) is None         # 래치
    # 같은 경기에서 0:2(base)가 돼도 쿼터 규칙은 이미 확정됐으므로 다시 안 선다.
    assert t.observe(4.0, (0, 2), 85) is None
    assert t.observe(5.0, (0, 2), 85) is None


def test_minute_none_keeps_late_rule_silent_but_base_still_works():
    from macroapp.auto_exit import KIND_BASE, LossTracker

    t = LossTracker(confirm_count=1, reset_seconds=60, rules=_rules())
    t.feed(0.0, (0, 0))
    assert t.observe(1.0, (0, 1), None) is None
    assert t.observe(2.0, (0, 2), None) == KIND_BASE


def test_match_end_releases_both_latches():
    from macroapp.auto_exit import KIND_HARD, LossTracker

    t = LossTracker(confirm_count=1, reset_seconds=60, rules=_rules())
    t.feed(0.0, (0, 0))
    assert t.observe(1.0, (0, 3)) == KIND_HARD
    t.feed(2.0, None)
    t.feed(70.0, None)                                # 60초 부재 → 새 경기
    t.feed(71.0, (0, 0))
    assert t.observe(72.0, (0, 3)) == KIND_HARD


def test_parse_exit_settings_is_field_level_fail_safe():
    from macroapp.auto_exit import ExitRules, ExitSettings, parse_exit_settings

    defaults = ExitSettings(rules=ExitRules(2, 3, 70, 1), ratio=0.4)
    got = parse_exit_settings(
        {"ratio": 0.6, "base_deficit": 2, "hard_deficit": 4, "late_minute": 75,
         "late_deficit": 1, "late_ratio": 1.0},
        defaults,
    )
    assert got.rules == ExitRules(2, 4, 75, 1)
    assert got.ratio == 0.6 and got.late_ratio == 1.0

    # 깨진 필드만 기본값으로 남고 나머지는 반영된다.
    got = parse_exit_settings(
        {"ratio": 7, "base_deficit": "x", "hard_deficit": 0, "late_minute": 999,
         "late_deficit": 2.5, "late_ratio": "abc"},
        defaults,
    )
    assert got.rules == ExitRules(2, 0, 70, 1)       # hard 0 = 끔(유효), late_minute 999 = 기본
    assert got.ratio == 0.4 and got.late_ratio is None

    # dict 가 아니면 전부 기본값.
    assert parse_exit_settings(None, defaults) == defaults
    assert parse_exit_settings("junk", defaults) == defaults
    # bool 은 숫자로 받지 않는다(True→1 같은 조용한 변환 금지).
    assert parse_exit_settings({"hard_deficit": True}, defaults).rules.hard_deficit == 3


# ---------------------------------------------------------------------------
# 경기 시계
# ---------------------------------------------------------------------------


def test_parse_clock_text_accepts_real_ocr_shapes():
    from macroapp.auto_exit import parse_clock_text

    assert parse_clock_text("84:10") == (84, 10)
    assert parse_clock_text("62•.04") == (62, 4)
    assert parse_clock_text("6454") == (64, 54)
    assert parse_clock_text("2211") == (22, 11)
    assert parse_clock_text("03:26") == (3, 26)
    assert parse_clock_text("90:oo") == (90, 0)
    assert parse_clock_text("90:00+3") == (90, 0)
    assert parse_clock_text("") is None
    assert parse_clock_text(None) is None
    assert parse_clock_text("84:70") is None          # 초 60 이상
    assert parse_clock_text("999:00") is None         # 분 130 초과
    assert parse_clock_text("12") is None
    assert parse_clock_text("12345") is None


def test_clock_tracker_confirms_on_consensus_and_resets_on_regression():
    from macroapp.auto_exit import ClockTracker

    c = ClockTracker(consensus=2, reset_seconds=60)
    assert c.feed(0.0, (70, 5)) is None               # 1회 — 아직
    assert c.feed(5.0, (70, 40)) == 70                # 2회 연속 비역행 → 확정
    assert c.feed(10.0, (71, 10)) == 71
    assert c.feed(15.0, None) == 71                   # 잠깐 못 읽어도 유지
    assert c.feed(20.0, (3, 0)) is None               # 크게 역행 → 새 경기/오독, 확정 해제
    assert c.feed(25.0, (3, 30)) == 3
    c.feed(30.0, None)
    assert c.feed(100.0, None) is None                # 60초 부재 → 비움
    assert c.minute is None


def test_find_clock_box_picks_the_wide_white_box_not_the_score_squares():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from macroapp.auto_exit import find_clock_box

    frame = np.full((1056, 1936), 40, dtype=np.uint8)
    frame[93:128, 300:335] = 235                      # 스코어 박스(정사각형) — 영역 안에 있어도 제외
    frame[93:128, 592:686] = 235                      # 시계 박스(가로 94x35)
    box = find_clock_box(frame, (0.22, 0.05, 0.45, 0.13))
    assert box is not None and box.shape == (35, 94)
    assert find_clock_box(np.full((1056, 1936), 40, dtype=np.uint8), (0.22, 0.05, 0.45, 0.13)) is None


def test_find_clock_box_on_saved_real_frames_if_present():
    """저장된 실전 프레임(dist/fc_state_*.png)이 있으면 전부에서 같은 자리의 박스를 찾는다."""
    pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    from macroapp.auto_exit import find_clock_box

    frames = sorted(Path("dist").glob("fc_state_*.png")) if Path("dist").exists() else []
    if not frames:
        pytest.skip("실전 프레임 없음")
    for path in frames:
        gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        box = find_clock_box(gray, (0.22, 0.05, 0.45, 0.13))
        assert box is not None, path.name
        assert 30 <= box.shape[0] <= 40 and 80 <= box.shape[1] <= 110, (path.name, box.shape)


# ---------------------------------------------------------------------------
# 적대적 리뷰(2026-08-21)가 확정한 결함의 회귀 테스트
# ---------------------------------------------------------------------------


def test_clock_single_upward_misread_does_not_confirm():
    """14:10→14:15→'74:20'(1↔7 오독)→14:25 — 74 가 확정되면 14분 경기에서 후반 규칙이 선다."""
    from macroapp.auto_exit import ClockTracker

    c = ClockTracker(consensus=2, reset_seconds=60)
    assert c.feed(0.0, (14, 10)) is None
    assert c.feed(5.0, (14, 15)) == 14
    assert c.feed(10.0, (74, 20)) == 14, "상향 단발 오독이 즉시 확정됐다"
    assert c.feed(15.0, (14, 25)) == 14
    # 반대로 점프가 2회 이어지면(가림 뒤 재등장·늦은 시작) 그때 받는다.
    assert c.feed(20.0, (46, 0)) == 14
    assert c.feed(25.0, (46, 30)) == 46
    # +1분 이내의 정상 진행은 예전처럼 바로 따라간다.
    assert c.feed(30.0, (47, 5)) == 47


def test_clock_misread_timeline_does_not_fire_late_rule():
    """스코어 1초·시계 5초 간격 실제 타임라인: 0:1 진행 중 시계가 한 번 83 으로 튀어도 late 가 안 선다."""
    from macroapp.auto_exit import ClockTracker, LossTracker

    clock = ClockTracker(consensus=2, reset_seconds=60)
    tracker = LossTracker(confirm_count=3, reset_seconds=60, rules=_rules())
    clock_reads = {0: (3, 26), 5: (3, 31), 10: (83, 36), 15: (3, 41), 20: (3, 46)}
    fired = []
    minute = None
    for t in range(0, 25):
        if t in clock_reads:
            minute = clock.feed(float(t), clock_reads[t])
        score = (0, 0) if t < 4 else (0, 1)
        kind = tracker.observe(float(t), score, minute)
        if kind:
            fired.append((t, minute, kind))
    assert fired == [], fired


def test_late_rule_requires_a_better_score_than_its_deficit_first():
    """정적 (0,1) 화면만 본 경기는 late 를 못 연다 — 같은 픽셀이 선행 증거이자 판정 근거면 방어가 없다."""
    from macroapp.auto_exit import KIND_LATE, LossTracker

    t = LossTracker(confirm_count=3, reset_seconds=60, rules=_rules())
    for i in range(5):
        assert t.observe(float(i), (0, 1), None) is None
    assert [t.observe(10.0 + i, (0, 1), 75) for i in range(3)] == [None, None, None]

    t2 = LossTracker(confirm_count=3, reset_seconds=60, rules=_rules())
    t2.feed(0.0, (0, 0), 5)
    assert [t2.observe(10.0 + i, (0, 1), 75) for i in range(3)] == [None, None, KIND_LATE]


def test_separate_quotas_get_separate_latches():
    """late 비율이 따로면(쿼터 둘) late 의 '방치'가 같은 경기의 base 확정을 굶기지 않는다."""
    from macroapp.auto_exit import KIND_BASE, KIND_LATE, LossTracker

    t = LossTracker(confirm_count=1, reset_seconds=60, rules=_rules())
    t.shared_quota_latch = False
    t.feed(0.0, (0, 0), 10)
    assert t.observe(1.0, (0, 1), 75) == KIND_LATE
    assert t.observe(2.0, (0, 1), 76) is None          # late 는 한 번
    assert t.observe(3.0, (0, 2), 80) == KIND_BASE      # 다른 장부 — 한 번 더 선다
    assert t.observe(4.0, (0, 2), 81) is None

    shared = LossTracker(confirm_count=1, reset_seconds=60, rules=_rules())
    shared.feed(0.0, (0, 0), 10)
    assert shared.observe(1.0, (0, 1), 75) == KIND_LATE
    assert shared.observe(3.0, (0, 2), 80) is None       # 같은 장부 — 한 경기 한 번


def test_parse_clock_text_rejects_three_digit_fallback():
    from macroapp.auto_exit import parse_clock_text

    assert parse_clock_text("841") is None              # "84:1" 자리 누락 → 8:41 로 역행시키지 않는다
    assert parse_clock_text("84:1") is None
    assert parse_clock_text("6454") == (64, 54)


def test_read_clock_text_falls_back_to_korean_pack():
    from unittest.mock import patch

    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    from macroapp import ocr

    box = np.full((35, 94), 235, dtype=np.uint8)
    calls = []

    def fake(img, lang="ko"):
        calls.append(lang)
        if lang == "en":
            raise RuntimeError("language pack missing")
        return "84:10"

    with patch.object(ocr, "winocr", object()), patch.object(ocr, "_recognize_text", side_effect=fake):
        assert ocr.read_clock_text(box) == "84:10"
    assert calls == ["en", "ko"]

    with patch.object(ocr, "winocr", object()), patch.object(ocr, "_recognize_text", return_value=""):
        assert ocr.read_clock_text(box) == ""


# ─── 2026-08-23 실측: '숫자 미상'이 판 경계를 지워 자동 종료가 세션 내내 죽었다 ────


def _rules_100():
    return ExitRules(
        base_deficit=2, hard_deficit=3, late_minute=70, late_deficit=1
    )


def _play(tracker, reading, seconds, minute=None, clock=None):
    """초 단위로 같은 값을 먹이고 발동된 종류들을 돌려준다."""
    fired = []
    for _ in range(int(seconds)):
        clock[0] += 1.0
        kind = tracker.observe(clock[0], reading, minute)
        if kind:
            fired.append(kind)
    return fired


def test_unknown_only_must_not_keep_a_match_alive_forever() -> None:
    """판 사이 공백에 '미상'이 섞여도 래치가 풀려야 한다.

    실측(2026-08-23): 구단 엠블럼·결과 패널·빈 박스 같은 **경기가 아닌 화면**도
    SCORE_UNKNOWN 으로 읽힌다. 미상이 종료 타이머를 되살리는 바람에 06:11 에 한 판이
    쿼터를 쓴 뒤 세션이 끝날 때까지 발동 0회였고, 0:2 를 26 게임분 방치했다.
    """

    tracker = LossTracker(rules=_rules_100())
    clock = [0.0]
    _play(tracker, (0, 0), 60, clock=clock)
    assert _play(tracker, (0, 2), 30, clock=clock), "1판이 발동해야 한다"
    assert tracker.latched

    # 판 사이 공백 200초 — 10초마다 '미상'이 한 번씩 섞인다(실제 로그 모양).
    for i in range(200):
        clock[0] += 1.0
        tracker.observe(
            clock[0], SCORE_UNKNOWN if i % 10 == 0 else None, None
        )
    assert not tracker.latched, "미상 때문에 판 경계가 지워졌다"

    # 다음 판도 정상적으로 발동해야 한다.
    _play(tracker, (0, 0), 40, clock=clock)
    assert _play(tracker, (0, 2), 40, clock=clock), "2판이 발동하지 않았다"


def test_unknown_still_protects_a_live_match_from_early_release() -> None:
    """반대 방향도 지킨다 — 경기 중 잠깐 못 읽는다고 판이 끝난 걸로 보면 안 된다.

    (숫자 3~9 글리프가 없어 3:1 같은 화면이 미상으로 읽히는 경우가 그것이다.)
    """

    tracker = LossTracker(rules=_rules_100())
    clock = [0.0]
    _play(tracker, (0, 0), 60, clock=clock)
    _play(tracker, (0, 2), 30, clock=clock)
    assert tracker.latched
    # 진짜 스코어를 계속 읽는 중이라면 미상이 섞여도 판은 이어진다.
    for i in range(150):
        clock[0] += 1.0
        tracker.observe(
            clock[0], SCORE_UNKNOWN if i % 3 else (0, 2), None
        )
    assert tracker.latched, "경기 중인데 판이 끝난 것으로 처리됐다"


def test_unknown_reset_is_longer_than_a_match_gap_but_shorter_than_a_session() -> None:
    from macroapp import config

    assert config.AUTO_EXIT_UNKNOWN_RESET_SECONDS >= 120.0
    assert config.AUTO_EXIT_UNKNOWN_RESET_SECONDS <= 300.0


def test_digit_five_is_readable() -> None:
    """숫자 5 글리프가 있어야 4:5 같은 스코어를 판정할 수 있다.

    실측(2026-08-23 07:22): 사용자가 4:5 로 지고 있는데 자동 종료가 안 걸렸다. 글리프가
    0~4 뿐이라 상대가 5점째를 넣는 순간 '숫자 미상'이 되어 판정 자체가 불가능했다
    (로그: '스코어 읽음: 4:4' → 그 뒤로 전부 미상). 5~9 는 실전 표본에서 계속 넓힌다.
    """

    from macroapp import score_glyphs

    have = {str(k) for k in score_glyphs.GLYPH_PNGS_B64}
    assert "5" in have, f"숫자 5 글리프가 없다: {sorted(have)}"
    for digit in ("0", "1", "2", "3", "4", "5"):
        assert score_glyphs.GLYPH_PNGS_B64[digit], f"{digit} 템플릿이 비었다"


def test_unknown_timeout_must_not_double_count_the_same_match() -> None:
    """미상 타임아웃으로 판을 끝냈어도 **새 판 증거 없이는** 다시 발동하지 않는다.

    6~9 득점으로 오래 미상이던 같은 판이 새 판으로 둔갑하면 한 경기를 두 번 세고
    두 번 나간다(쿼터 비율이 무너진다). Codex 2차 의견으로 발견, 시뮬로 재현했다.
    새 판 증거는 '총 1골 이하'(킥오프 직후로만 가능한 스코어)로 본다.
    """

    tracker = LossTracker(rules=_rules_100())
    clock = [0.0]
    _play(tracker, (0, 0), 60, clock=clock)
    assert _play(tracker, (0, 2), 30, clock=clock), "첫 발동이 있어야 한다"

    # 같은 판이 계속되는데 숫자를 못 읽는다(5~9 글리프 없음).
    for _ in range(200):
        clock[0] += 1.0
        tracker.observe(clock[0], SCORE_UNKNOWN, None)

    # 같은 판이 이어진다 — 절대 다시 발동하면 안 된다.
    again = _play(tracker, (3, 4), 10, clock=clock)
    again += _play(tracker, (3, 5), 30, clock=clock)
    assert not again, "같은 판을 두 번 셌다"


def test_a_genuine_new_match_still_fires_after_an_unknown_timeout() -> None:
    """반대로 진짜 새 판(0:0 부터 시작)은 정상적으로 발동해야 한다."""

    tracker = LossTracker(rules=_rules_100())
    clock = [0.0]
    _play(tracker, (0, 0), 60, clock=clock)
    _play(tracker, (0, 2), 30, clock=clock)
    for _ in range(200):
        clock[0] += 1.0
        tracker.observe(clock[0], SCORE_UNKNOWN, None)
    _play(tracker, (0, 0), 40, clock=clock)          # 새 판 증거(킥오프)
    assert _play(tracker, (0, 2), 40, clock=clock), "새 판이 발동하지 않았다"
