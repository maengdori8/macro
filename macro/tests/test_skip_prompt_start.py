"""▷ SKIP 화면(target_F) — START 입력이 실제로 나가는 배선을 고정한다.

실사고(2026-08-18, 구매자 다수 보고): 이 화면에서 자동화가 멈췄다.
원인은 세 겹이 겹친 것이었다:
  1. target_F 의 key 가 "esc" — esc/start 는 같은 게임패드 버튼(16)이지만,
  2. 이름이 esc 면 SKIP 실험의 ESC 보류 게이트(_defer_escape_target_for_skip_probe)
     에 걸려 엄격 모드(기본값)에서 입력이 영구 억류되고,
  3. 그 억류를 풀어 줄 OCR 경로는 winocr 이 없거나 작은 스타일 글자를 못 읽는
     구매자 PC 에서 조용히 침묵한다.
사용자 실측: 이 화면은 START 로 넘어간다. 그래서 key 를 "start" 로 바꿔
게이트를 설계적으로 비켜가게 했다 — 이 테스트들은 그 배선이 되돌아가는 것을 막는다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from macroapp import input_gamepad  # noqa: E402
from macroapp.config import DEFAULT_TARGET_CONFIGS  # noqa: E402


def _named(configs, name):
    for item in configs:
        if item["name"] == name:
            return item
    raise AssertionError(f"{name} 정의가 사라졌다")


def _sources():
    data = json.loads((ROOT / "targets.json").read_text(encoding="utf-8"))
    # target_G 는 target_F 바로 다음에 오는 같은 계열 프롬프트다 — F 만 고치면
    # 한 화면 뒤에서 똑같이 멈춘다(같은 ESC 보류 게이트).
    for name in ("target_F", "target_G"):
        yield f"config:{name}", _named(DEFAULT_TARGET_CONFIGS, name)
        yield f"targets.json:{name}", _named(data["targets"], name)


@pytest.mark.parametrize("label, item", list(_sources()), ids=lambda v: v if isinstance(v, str) else "")
def test_target_f_presses_start_not_esc(label, item):
    """key 가 "esc" 로 돌아가면 보류 게이트에 다시 걸린다 — 두 정의 소스 모두 고정."""
    assert item["action"] == "key"
    assert item["key"] == "start", f"{label}: esc 로 되돌리면 구매자가 다시 멈춘다"


@pytest.mark.parametrize("label, item", list(_sources()), ids=lambda v: v if isinstance(v, str) else "")
def test_target_f_waits_for_the_transition(label, item):
    """전환 중 무한 연타 방지 + 펄스 불발 시 재시도 여력 유지의 절충 구간.

    0 이면 과거처럼 6회/초 연타(스팸), 1.0 이상이면 3초 창에서 재시도가 3회로
    줄어 펄스 한 번 불발이 곧 실패가 된다(적대적 리뷰 지적). 0.3~0.8 로 고정.
    """
    wait = float(item.get("wait_after_action", 0.0))
    assert 0.3 <= wait <= 0.8


def test_start_is_a_real_gamepad_button():
    """key→버튼 매핑이 없으면 dispatch_key_press 가 조용히 건너뛴다(무입력 사고 유형)."""
    assert input_gamepad.KEY_TO_GAMEPAD.get("start") == 16
    # esc 와 같은 버튼임을 문서화해 둔다 — 문제는 버튼이 아니라 '이름'이었다.
    assert input_gamepad.KEY_TO_GAMEPAD.get("esc") == 16


def test_defer_gate_does_not_capture_start_targets():
    """보류 게이트는 esc 이름만 잡아야 한다 — start 타겟을 잡기 시작하면 재발이다."""
    tk = pytest.importorskip("tkinter")
    from macroapp import edition
    from macroapp.gui import AutomationApp

    edition.set_product(edition.PRODUCT_STANDARD)
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"tkinter 를 띄울 수 없습니다: {exc}")
    try:
        root.withdraw()
        from unittest import mock
        with mock.patch.object(AutomationApp, "_bring_window_to_front", lambda s: None):
            app = AutomationApp(root, license_key=None, preview=True)

        class _T:
            action = "key"

        _T.key = "start"
        assert app._defer_escape_target_for_skip_probe(_T(), 0.0) is False, (
            "start 타겟이 보류 게이트에 걸렸다 — ▷ SKIP 이 또 멈춘다"
        )
        _T.key = "esc"
        assert app._defer_escape_target_for_skip_probe(_T(), 0.0) is True, (
            "esc 보류가 풀렸다 — SKIP 실험 오염 방지가 사라진다"
        )
        app.closing = True
        try:
            app._drain_match_writer()
        except Exception:
            pass
        try:
            app._close_log_file()
        except Exception:
            pass
    finally:
        root.destroy()
