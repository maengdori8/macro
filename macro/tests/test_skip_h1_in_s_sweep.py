"""H1(attach_active_hold_a)이 S 스윕 맨 앞에 있다 — 마지막 미검증 배경 스킵 가설.

2026-08-22 원장 재집계: H1 은 A 목록(index 4·5)에만 있어 실전에서 **0행**이었다. 구매자 PC 가
보는 컷신 프롬프트는 S 형(키보드 표시 장치, 8-22 감지 1,964회)이라 S 목록을 타는데, 거기엔
없었다. 이 테스트는 S 에서도 H1 이 대조(attach_hold_a)와 함께 맨 앞에 서 있음을 고정한다.
(전면 전환은 사용자 결정으로 하지 않는다 — 배경 경로만 남는다.)
"""

from __future__ import annotations

from macroapp import skip_candidates


def test_h1_is_at_the_front_of_the_s_sweep() -> None:
    s = list(skip_candidates.SKIP_S_CANDIDATES)
    assert "attach_active_hold_a" in s, "H1 이 S 목록에 없다 — 구매자 PC 에선 영영 안 돈다"
    assert "attach_hold_a" in s, "H1 대조(큐 공유만)가 S 목록에 없다"
    assert s[0] == "control_noop"
    assert tuple(s[1:3]) == ("attach_hold_a", "attach_active_hold_a")
    # A 목록에도 그대로(둘 다 있어야 한다).
    assert "attach_active_hold_a" in skip_candidates.SKIP_A_CANDIDATES
    assert "attach_hold_a" in skip_candidates.SKIP_A_CANDIDATES


def test_h1_specs_keep_the_inactive_invariants() -> None:
    for name in ("attach_hold_a", "attach_active_hold_a"):
        spec = skip_candidates.get_skip_candidate_spec(name)
        assert spec is not None, name
        assert spec.input_scope == "virtual_gamepad", name
        assert (spec.hold_seconds or 0) >= 1.0, f"{name}: hold-to-skip 은 홀드여야 한다"
