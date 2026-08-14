"""가짜 입력 대조군(sham) 검증.

컷신은 상대가 눌러도 양쪽 다 끝나므로, 후보의 '성공'만으로는 우리 입력의 효과와
상대의 스킵을 구분할 수 없다. 대조군은 같은 판정 경로를 타면서 입력만 보내지 않아
'우리가 안 눌렀을 때의 종료율'을 같은 조건에서 실측한다.

여기서는 그 대조군이 (1) 주기적으로 선택되고 (2) 절대 학습되지 않으며
(3) 스윕 순서를 망가뜨리지 않는지를 확인한다. 전부 순수 로직이라 Windows가 필요 없다.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from macroapp.skip_experiment import (  # noqa: E402
    GuardedActionResult,
    SkipExperimentTracker,
)

SHAM = "control_noop"


def _guard() -> GuardedActionResult:
    return GuardedActionResult(
        attempted=True,
        action_ok=True,
        foreground_before=10,
        foreground_after=10,
        foreground_samples=(10, 10),
        invariant_ok=True,
        reason="ok",
        elapsed_seconds=0.01,
    )


def _tracker(**kwargs) -> SkipExperimentTracker:
    options = dict(
        result_window_seconds=1.5,
        attempt_gap_seconds=0.0,
        confirm_successes=3,
        control_seconds=3.0,
        exit_confirm_seconds=0.0,
        sham_candidate=SHAM,
        sham_every=3,
    )
    options.update(kwargs)
    return SkipExperimentTracker((SHAM, "char_s_hold", "pm_s_hold"), **options)


def _run_episode(tracker: SkipExperimentTracker, clock: float, *, skipped: bool):
    """한 에피소드를 끝까지 돌리고 (선택된 후보, 결과)를 돌려준다."""

    candidate, _ = tracker.choose(clock)          # 대조 관찰 시작
    assert candidate is None, "대조 구간에는 아직 누르면 안 된다"
    clock += 3.0                                   # 무입력 관찰 통과
    candidate, _ = tracker.choose(clock)
    if candidate is None:
        return None, None
    tracker.record_attempt(candidate, clock, _guard())
    if not skipped:
        outcome = tracker.expire_pending(clock + 2.0)
    else:
        outcome = tracker.prompt_disappeared(clock + 0.1)
    tracker.reset_episode()
    return candidate, outcome


class ShamControlTests(unittest.TestCase):
    def test_sham_runs_on_every_nth_episode(self) -> None:
        tracker = _tracker()
        picked = []
        clock = 100.0
        for _ in range(9):
            candidate, _ = _run_episode(tracker, clock, skipped=False)
            picked.append(candidate)
            clock += 60.0
        self.assertEqual(picked.count(SHAM), 3, f"3회마다 1회여야 함: {picked}")
        self.assertEqual([i for i, c in enumerate(picked) if c == SHAM], [2, 5, 8])

    def test_sham_success_is_never_learned(self) -> None:
        """대조군이 계속 성공해도 학습되면 안 된다(그러면 매크로가 입력을 멈춘다)."""

        tracker = _tracker(sham_every=1)
        clock = 100.0
        for _ in range(6):
            candidate, outcome = _run_episode(tracker, clock, skipped=True)
            self.assertEqual(candidate, SHAM)
            self.assertEqual(outcome.status, "success")
            self.assertIsNone(outcome.learned, "대조군은 절대 학습되면 안 됨")
            clock += 60.0
        self.assertIsNone(tracker.learned)
        self.assertIsNone(tracker.preferred, "선호 후보로도 올라가면 안 됨")

    def test_sham_success_is_labelled_as_baseline(self) -> None:
        tracker = _tracker(sham_every=1)
        _, outcome = _run_episode(tracker, 100.0, skipped=True)
        self.assertTrue(outcome.detail.startswith("sham_baseline="), outcome.detail)

    def test_real_candidate_still_learns_alongside_sham(self) -> None:
        """대조군이 끼어들어도 진짜 후보의 학습은 정상 동작해야 한다."""

        tracker = _tracker(sham_every=100)   # 사실상 대조군 없음
        clock = 100.0
        learned = None
        for _ in range(3):
            candidate, outcome = _run_episode(tracker, clock, skipped=True)
            self.assertNotEqual(candidate, SHAM)
            learned = outcome.learned or learned
            clock += 60.0
        self.assertIsNotNone(learned, "3회 확정되면 학습돼야 함")

    def test_sham_does_not_consume_sweep_turn(self) -> None:
        """대조 에피소드가 진짜 후보의 차례를 잡아먹으면 스윕이 왜곡된다."""

        tracker = _tracker(sham_every=2)
        clock = 100.0
        real_order = []
        for _ in range(8):
            candidate, _ = _run_episode(tracker, clock, skipped=False)
            if candidate != SHAM:
                real_order.append(candidate)
            clock += 60.0
        # 대조군을 빼고 보면 진짜 후보들이 순서대로 번갈아 나와야 한다.
        self.assertEqual(real_order, ["char_s_hold", "pm_s_hold"] * 2)

    def test_disabled_when_every_is_zero(self) -> None:
        tracker = _tracker(sham_every=0)
        clock = 100.0
        picked = [_run_episode(tracker, clock + i * 60.0, skipped=False)[0] for i in range(6)]
        self.assertNotIn(SHAM, picked)

    def test_unknown_sham_name_is_ignored(self) -> None:
        tracker = SkipExperimentTracker(
            ("char_s_hold",),
            result_window_seconds=1.5,
            attempt_gap_seconds=0.0,
            confirm_successes=3,
            sham_candidate="does_not_exist",
            sham_every=2,
        )
        self.assertIsNone(tracker.sham_candidate)


if __name__ == "__main__":
    unittest.main()
