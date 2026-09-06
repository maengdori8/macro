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
    # 2026-09-06 사용자 실측으로 **모양**을 고쳤다: "start 아니고, 홀드 아니고, 그냥 키".
    # 컷신이 실제로 표시하는 건 키보드 S 키캡("[S] SKIP", 증거
    # prompt_20260823_150628_e3662b759605.png)이다. H1 은 지금까지 'A 1.25초 홀드'
    # 한 모양으로만 돌아서, 답이 S 탭이라면 가설이 한 번도 검증되지 않은 것이다.
    # 이제 맨 앞은 H1 의 **키보드 S 탭**과 **A 탭** 모양이고, 각각 대조와 짝이다.
    assert tuple(s[1:5]) == (
        "attach_char_s", "attach_active_char_s",
        "attach_a_tap", "attach_active_a_tap",
    )
    # 예전 홀드 모양도 목록에 남는다(폐기가 아니라 우선순위 강등).
    assert "attach_hold_a" in s and "attach_active_hold_a" in s
    # 사용자가 "start 아니다" 라고 한 계열은 S 스윕에서 키보드/A 탭보다 뒤다.
    assert s.index("attach_active_char_s") < s.index("spoof_start_envelope2")
    assert s.index("a") < s.index("spoof_start_envelope2")
    # A 목록에도 그대로(둘 다 있어야 한다).
    assert "attach_active_hold_a" in skip_candidates.SKIP_A_CANDIDATES
    assert "attach_hold_a" in skip_candidates.SKIP_A_CANDIDATES


def test_h1_specs_keep_the_inactive_invariants() -> None:
    for name in ("attach_hold_a", "attach_active_hold_a"):
        spec = skip_candidates.get_skip_candidate_spec(name)
        assert spec is not None, name
        assert spec.input_scope == "virtual_gamepad", name
        assert (spec.hold_seconds or 0) >= 1.0, f"{name}: 예전 홀드 모양은 홀드로 남는다"


def test_h1_has_tap_and_keyboard_shapes() -> None:
    """같은 전달 경로(H1)를 사용자가 말한 모양(탭·키보드 S)으로도 시도한다."""
    for name in ("attach_a_tap", "attach_active_a_tap"):
        spec = skip_candidates.get_skip_candidate_spec(name)
        assert spec is not None, name
        assert spec.input_scope == "virtual_gamepad", name
        assert (spec.hold_seconds or 0) <= 0.3, f"{name}: 탭이어야 한다"
    for name in ("attach_char_s", "attach_active_char_s"):
        spec = skip_candidates.get_skip_candidate_spec(name)
        assert spec is not None, name
        assert (spec.hold_seconds or 0) <= 0.3, f"{name}: 탭이어야 한다"
        # 키보드 후보는 창 메시지 경로다 — SendInput(전역 유출)이 아니다.
        assert spec.input_scope == "target_window", name


def test_h1_candidates_are_paired_with_their_control() -> None:
    """활성 창을 세우는 후보는 항상 '큐 공유만' 대조와 짝이어야 한다.

    짝이 없으면 성공해도 SetActiveWindow 덕인지 큐 공유 덕인지 못 가른다.
    """
    s = list(skip_candidates.SKIP_S_CANDIDATES)
    for active, control in (
        ("attach_active_char_s", "attach_char_s"),
        ("attach_active_a_tap", "attach_a_tap"),
        ("attach_active_hold_a", "attach_hold_a"),
    ):
        assert active in s and control in s, (active, control)
        assert (
            skip_candidates.get_skip_candidate_spec(active).hold_seconds
            == skip_candidates.get_skip_candidate_spec(control).hold_seconds
        ), f"{active}/{control}: 대조는 모양이 같아야 한다"


def test_h1_keyboard_candidate_is_wired_to_attach_and_activate() -> None:
    """attach_active_char_s 가 실제로 '붙은 큐 + SetActiveWindow + S 딥 메시지' 인지.

    카탈로그에만 넣고 배선을 빠뜨리면 후보가 조용히 False 를 돌려주고, 원장에는
    'action_failed' 만 쌓인다(2026-08-22 의 '목록에 없어 0행' 과 같은 종류의 사고).
    """
    from types import SimpleNamespace
    from unittest.mock import patch

    from macroapp import gui

    manager = SimpleNamespace(hwnd=777)
    calls = {}

    def fake_run(hwnd, action, *, activate=False):
        calls["hwnd"] = hwnd
        calls["activate"] = activate
        return bool(action())

    def fake_char(hwnd, vk, char_code, press_delay=0.0):
        calls["char"] = (hwnd, vk, char_code, press_delay)
        return True

    with patch.object(gui.input_message, "run_attached_to_window", fake_run), \
        patch.object(gui.input_message, "post_char_deep", fake_char):
        assert gui.AutomationApp._press_skip_candidate(
            "attach_active_char_s", manager
        ) is True
    assert calls["hwnd"] == 777
    assert calls["activate"] is True          # H1 의 핵심: 활성 창을 세운다
    assert calls["char"][1] == ord("S")       # VK_S
    assert calls["char"][2] == ord("s")

    calls.clear()
    with patch.object(gui.input_message, "run_attached_to_window", fake_run), \
        patch.object(gui.input_message, "post_char_deep", fake_char):
        assert gui.AutomationApp._press_skip_candidate(
            "attach_char_s", manager
        ) is True
    assert calls["activate"] is False         # 대조는 큐 공유만
