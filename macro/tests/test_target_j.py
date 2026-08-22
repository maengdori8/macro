"""target_J — 경기 중 하단 전술창을 '−' 로 접는 타겟.

실측(2026-08-22): 템플릿(아이콘 줄+'−')은 전술창 펼침·접힘 두 상태에 모두 맞는다
(NCC 1.000/0.997). 그레이스케일로는 못 가른다 — '아래 어두운 면' 트릭은 실전 프레임마다
밝기가 달라 펼침도 놓쳤다(0.82). 대신 **위치**로 가른다: 펼침은 아이콘 줄이 y≈0.75,
접힘은 y≈0.97(맨 아래). match_top/bottom_frac 밴드로 펼침만 눌러 접고, 접힌 뒤엔 다시
안 누른다. 이 테스트는 그 약속들을 고정한다.
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
def test_target_j_is_click_gated_to_the_expanded_band(label, item):
    assert item["action"] == "click"
    assert float(item["threshold"]) >= 0.85, f"{label}"
    # 펼침(y≈0.75)은 밴드 안, 접힘(y≈0.97)은 밴드 밖이어야 한다.
    top, bottom = float(item["match_top_frac"]), float(item["match_bottom_frac"])
    assert 0.0 < top < 0.75 < bottom < 0.97, f"{label}: 밴드가 펼침만 받고 접힘은 걸러야 한다 ({top},{bottom})"
    assert float(item.get("wait_after_action", 0)) >= 0.5


def test_template_is_the_plain_icon_row_no_dark_band():
    cv2 = pytest.importorskip("cv2")
    tpl = cv2.imread(str(ROOT / "target_J.png"), cv2.IMREAD_GRAYSCALE)
    assert tpl is not None
    assert tpl.shape == (28, 128), tpl.shape          # 아이콘 줄만, 아래 띠 없음
    assert float(tpl[:, :].max()) > 200               # 흰 아이콘/'−' 가 있다


def test_match_band_accepts_expanded_and_rejects_minimized():
    from macroapp import gui

    target = _target_from_config(_named(DEFAULT_TARGET_CONFIGS, "target_J"), 9)
    assert target is not None
    h = 1040
    # 펼침 아이콘 줄 중심 y≈768 → 0.74, 접힘 y≈986 → 0.95
    assert gui.AutomationApp._match_in_band(target, (1608, 768), h) is True
    assert gui.AutomationApp._match_in_band(target, (1608, 986), h) is False
    # 밴드 없는 일반 타겟은 어디든 통과.
    plain = _target_from_config({"name": "p", "filename": "x.png", "action": "click"}, 0)
    assert gui.AutomationApp._match_in_band(plain, (10, 10), h) is True
    assert gui.AutomationApp._match_in_band(plain, (10, 1030), h) is True


def test_target_j_click_point_has_no_offset():
    from macroapp import gui

    target = _target_from_config(_named(DEFAULT_TARGET_CONFIGS, "target_J"), 9)
    assert gui.AutomationApp._click_point(target, (1608, 804)) == (1608, 804)
