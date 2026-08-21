"""target_J — 경기 중 하단 전술창을 '−' 로 접는 타겟.

실측(2026-08-22): 처음 템플릿(아이콘 줄만, 28px)은 **접힌 상태**(아이콘 줄이 프레임 맨 아래로
내려가고 '−' 가 초록으로 바뀜)에도 0.997 로 맞아 접힌 전술창의 '−' 를 계속 눌렀다.
그레이스케일 상관은 밝기 변화에 둔감해 초록/흰 '−' 를 못 가른다. 그래서 템플릿에 아이콘 줄
**아래 어두운 면 6px**(그 아래는 핫키 바의 선택 박스가 흰색이라 포함하면 선택 상태에 따라 흔들린다) 를 포함했다 — 접힌 상태에선 그 아래가 프레임 밖이라 안 맞는다
(펼침 1.000 / 접힘 ≤0.68). 대신 템플릿 중심이 버튼보다 3px 아래라 click_offset_y=-3 으로
클릭 지점을 버튼으로 되돌린다. 이 테스트는 그 약속들을 고정한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from macroapp.config import DEFAULT_TARGET_CONFIGS, _target_from_config  # noqa: E402


def _named(configs, name):
    for item in configs:
        if item["name"] == name:
            return item
    raise AssertionError(f"{name} 정의가 사라졌다")


def _sources():
    data = json.loads((ROOT / "targets.json").read_text(encoding="utf-8"))
    # ⚠️ 런타임은 targets.json(임베드)을 쓰고 DEFAULT 는 폴백이다 — 둘 다 있어야 한다.
    yield "config", _named(DEFAULT_TARGET_CONFIGS, "target_J")
    yield "targets.json", _named(data["targets"], "target_J")


@pytest.mark.parametrize("label, item", list(_sources()), ids=lambda v: v if isinstance(v, str) else "")
def test_target_j_is_a_click_with_offset_back_onto_the_button(label, item):
    assert item["action"] == "click"
    assert float(item["threshold"]) >= 0.85, f"{label}: 어두운 면이 넓은 템플릿이라 임계값을 낮추면 오탐"
    assert int(item["click_offset_y"]) == -3, f"{label}: 중심이 버튼 아래라 보정이 없으면 버튼을 빗맞힌다"
    assert float(item.get("wait_after_action", 0)) >= 0.5


def test_template_includes_the_dark_band_below_the_icon_row():
    cv2 = pytest.importorskip("cv2")
    tpl = cv2.imread(str(ROOT / "target_J.png"), cv2.IMREAD_GRAYSCALE)
    assert tpl is not None
    h, w = tpl.shape
    assert (h, w) == (34, 128), (h, w)
    # 아래 6px 는 어두운 면(접힘 상태 구분의 근거) — 평균 밝기가 낮아야 한다.
    assert float(tpl[28:, :].mean()) < 60, "템플릿 아래 띠가 어둡지 않다 — 접힘 구분이 깨진다"
    # 위 28px 에는 흰 아이콘/'−' 가 있다.
    assert float(tpl[:28, :].max()) > 200


def test_click_offset_is_parsed_and_applied():
    from macroapp import gui

    target = _target_from_config({
        "name": "t", "filename": "x.png", "action": "click", "click_offset_y": -3,
    }, 0)
    assert target is not None
    assert target.click_offset_x == 0 and target.click_offset_y == -3
    assert gui.AutomationApp._click_point(target, (100, 200)) == (100, 197)
    # 보정이 없는 타겟은 중심 그대로.
    plain = _target_from_config({"name": "p", "filename": "x.png", "action": "click"}, 1)
    assert gui.AutomationApp._click_point(plain, (100, 200)) == (100, 200)
