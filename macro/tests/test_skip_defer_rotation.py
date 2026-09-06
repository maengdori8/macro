"""defer 된 후보는 차례를 넘긴다 — 스윕 굶주림(starvation) 재발 방지.

## 왜 이 테스트가 있나

`run_guarded_inactive_action` 은 게임이 이미 전면이면 시도를 포기한다
(`detail="target_already_foreground"`). 그런데 `record_attempt` 가 그때
후보 차례를 안 넘겨서, **defer 를 유발한 조건이 계속되는 동안 같은 후보가
영원히 재선택**됐다.

2026-08-23 실측:

    attach_hold_a  n=434  → deferred 432 (전부 target_already_foreground)
    그 432회는 15:06:26 에 뜬 [S] SKIP 프롬프트 **하나**에서 나왔고, 그 화면은
    116초 동안 입력 0회로 방치됐다.

즉 실험이 스스로를 굶겨서 인매치 컷신에 아무 입력도 안 나갔다. 이 테스트는
"defer 는 실패가 아니지만 차례는 넘긴다"를 고정한다.
"""

from __future__ import annotations

from macroapp.skip_experiment import (
    GuardedActionResult,
    SkipExperimentTracker,
)


def _deferred_guard(reason: str = "target_already_foreground") -> GuardedActionResult:
    """입력이 아예 안 나간 가드 결과(= attempted False)."""
    return GuardedActionResult(
        attempted=False,
        action_ok=False,
        foreground_before=111,
        foreground_after=111,
        foreground_samples=(111,),
        invariant_ok=False,
        reason=reason,
        elapsed_seconds=0.0,
    )


def _experiment(*candidates: str) -> SkipExperimentTracker:
    return SkipExperimentTracker(
        candidates,
        result_window_seconds=1.5,
        attempt_gap_seconds=0.0,
        confirm_successes=3,
        control_seconds=0.0,
    )


def test_defer_는_다음_후보로_차례를_넘긴다() -> None:
    exp = _experiment("alpha", "bravo", "charlie")
    exp.record_attempt("alpha", 10.0, _deferred_guard())
    # index 가 alpha 다음(bravo)을 가리켜야 한다.
    assert exp.candidates[exp.index] == "bravo"


def test_defer_가_반복돼도_같은_후보에_갇히지_않는다() -> None:
    """같은 후보를 연속 defer 시키면 목록을 한 바퀴 돌아야 한다."""
    exp = _experiment("alpha", "bravo", "charlie")
    seen = []
    for i in range(6):
        candidate = exp.candidates[exp.index]
        seen.append(candidate)
        exp.record_attempt(candidate, 10.0 + i, _deferred_guard())
    assert seen == ["alpha", "bravo", "charlie"] * 2, seen


def test_defer_는_실패로_기록되지_않는다() -> None:
    """defer 는 입력이 안 나간 것이라 성공률 통계를 오염시키면 안 된다."""
    exp = _experiment("alpha", "bravo")
    outcome = exp.record_attempt("alpha", 10.0, _deferred_guard())
    assert outcome.status == "deferred"
    assert "alpha" not in exp.quarantined
    assert exp.success_counts.get("alpha", 0) == 0
    assert exp.pending is None
    # 에피소드를 소비하지 않는다 — 배경으로 돌아오면 같은 컷신에서 다시 시도할 수 있다.
    assert exp.attempted_this_episode is False


def test_defer_횟수는_따로_센다() -> None:
    """어떤 후보가 계속 막히는지 진단할 수 있어야 한다."""
    exp = _experiment("alpha", "bravo")
    exp.record_attempt("alpha", 10.0, _deferred_guard())
    exp.record_attempt("alpha", 11.0, _deferred_guard("missing_foreground"))
    assert exp.deferral_counts["alpha"] == 2
    assert exp.deferral_counts.get("bravo", 0) == 0


def test_대조군은_defer_해도_스윕_인덱스를_안_옮긴다() -> None:
    """sham 은 스윕 순서 밖에서 끼어드는 것이라 차례를 먹으면 안 된다."""
    exp = SkipExperimentTracker(
        ("control_noop", "alpha", "bravo"),
        result_window_seconds=1.5,
        attempt_gap_seconds=0.0,
        confirm_successes=3,
        control_seconds=0.0,
        sham_candidate="control_noop",
        sham_every=2,
    )
    before = exp.index
    exp.record_attempt("control_noop", 10.0, _deferred_guard())
    assert exp.index == before


def test_defer_가_반복되면_실제_선택도_회전한다() -> None:
    """⚠️ `_advance_past`(self.index) 만으로는 **안 고쳐진다**.

    self.index 는 이 클래스에서 설정·변경만 되고 **한 번도 읽히지 않는다** — 실제 선택은
    `_select_explore` 의 attempt_counts/family 기준이다. 그리고 defer 는 attempt_counts
    증가 지점(`_record_final`)을 안 타므로 카운트가 0에 고정돼 매번 같은 후보가 뽑힌다.
    (수정 전 재현: 항상 defer 하는 가드로 200회 → 200회 모두 동일 후보.)

    이 테스트는 실제 선택 경로(choose)가 회전하는지를 본다.
    """
    from macroapp import config, skip_candidates

    exp = SkipExperimentTracker(
        skip_candidates.SKIP_S_CANDIDATES,
        result_window_seconds=config.SKIP_EXPERIMENT_RESULT_WINDOW_SECONDS,
        attempt_gap_seconds=0.0,
        confirm_successes=config.SKIP_EXPERIMENT_CONFIRM_SUCCESSES,
        control_seconds=0.0,
        sham_candidate=config.SKIP_SHAM_CANDIDATE,
        sham_every=config.SKIP_SHAM_EVERY,
        candidate_families=skip_candidates.skip_candidate_families(
            skip_candidates.SKIP_S_CANDIDATES
        ),
        min_family_attempts=config.SKIP_EXPERIMENT_MIN_FAMILY_ATTEMPTS,
        real_allocation=config.SKIP_EXPERIMENT_REAL_ALLOCATION,
    )
    seen = []
    now = 100.0
    for _ in range(60):
        candidate, _expired = exp.choose(now)
        if candidate:
            exp.record_attempt(candidate, now, _deferred_guard())
            seen.append(candidate)
        now += 0.5

    assert len(seen) >= 50, seen
    assert len(set(seen)) >= 10, (
        f"defer 가 반복되는데 후보가 {len(set(seen))}종밖에 안 돌았다 — 굶주림 재발"
    )
    # 첫 후보가 두 번째 시도까지 붙들고 있으면 안 된다.
    assert seen[0] != seen[1], seen[:4]
