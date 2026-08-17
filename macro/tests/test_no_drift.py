"""두 제품이 공유하는 파일이 갈라지지 않았는지 검사한다.

mAuto(프로)의 즉시 종료와 mPause 는 **같은 기능**이다. 한쪽에서 버그를 고치고
다른 쪽을 잊으면, 고쳐진 줄 알았던 버그가 다른 제품에 그대로 남는다. 그런 일이
이미 이 레포에 있었다 — `ed25519_tiny.py` 가 두 폴더에 "동일 파일"로 복사돼
있는데 같은지 확인하는 장치가 없었다.

그래서 **바이트가 같아야 하는 파일**을 여기에 못 박는다. 다르면 어느 파일인지와
첫 차이 지점을 알려 준다. 화면·입력 계층은 앱마다 다르게 다듬어 이식했으므로
여기서 비교하지 않는다(그쪽은 각자의 테스트가 지킨다).
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pytest

MACRO = Path(__file__).resolve().parents[1]
MPAUSE = MACRO.parent / "mpause"

#: (설명, mAuto 쪽 경로, mPause 쪽 경로)
SHARED_FILES = [
    (
        "즉시 종료 판정 로직(순수)",
        MACRO / "macroapp" / "exit_core.py",
        MPAUSE / "mpauseapp" / "core.py",
    ),
    (
        "프로세스 제어 계층",
        MACRO / "macroapp" / "winproc.py",
        MPAUSE / "mpauseapp" / "winproc.py",
    ),
    (
        "Ed25519 검증(서명 확인)",
        MACRO / "ed25519_tiny.py",
        MPAUSE / "ed25519_tiny.py",
    ),
]


@pytest.mark.parametrize(
    "label, left, right", SHARED_FILES, ids=[item[0] for item in SHARED_FILES]
)
def test_shared_files_are_identical(label: str, left: Path, right: Path):
    if not right.exists():
        pytest.skip(f"형제 프로젝트가 없습니다: {right}")
    assert left.exists(), f"{label}: {left} 가 없습니다"

    left_text = left.read_text(encoding="utf-8").splitlines(keepends=True)
    right_text = right.read_text(encoding="utf-8").splitlines(keepends=True)
    if left_text == right_text:
        return

    diff = "".join(
        difflib.unified_diff(
            right_text, left_text, fromfile=str(right), tofile=str(left), n=1
        )
    )
    pytest.fail(
        f"{label} 가 두 제품에서 갈라졌습니다. 한쪽만 고치면 다른 제품에 버그가 남습니다.\n"
        + diff[:2000]
    )


def test_entry_points_differ_only_by_product():
    """일반/프로 진입점은 **제품 한 줄만** 달라야 한다.

    두 파일은 시작 실패 진단(무성 크래시 대응)을 통째로 공유한다. 한쪽만 고치면
    다른 제품에서 '더블클릭해도 무반응'이 진단 없이 재발한다.
    """
    import difflib

    standard = (MACRO / "macro_main.py").read_text(encoding="utf-8").splitlines()
    pro = (MACRO / "macro_pro_main.py").read_text(encoding="utf-8").splitlines()
    changed = [
        line
        for line in difflib.unified_diff(standard, pro, lineterm="", n=0)
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    # 허용: 제품 상수, 그리고 문구에 제품 이름이 들어간 줄(제목·로그).
    unexpected = [
        line
        for line in changed
        if line.strip() not in ("+", "-")   # 빈 줄 차이는 의미 없다
        and not any(
            token in line
            for token in ("PRODUCT_STANDARD", "PRODUCT_PRO", "mAuto Pro", "Macro 시작", "진입 스크립트", "일반 빌드", "진단 로직")
        )
    ]
    assert not unexpected, "진입점이 제품 외의 이유로 갈라졌습니다:\n" + "\n".join(unexpected)


def test_pro_build_accepts_only_pro_keys():
    """프로 빌드는 프로 키만 받아야 한다 — v1(제품 없음)도 받으면 안 된다.

    서버를 옛 버전으로 되돌리면 v1 서명이 오는데, 그걸 받아 주면 아무 키로나
    프로가 열린다. 프로 사용자가 잠깐 막히는 쪽이 낫다(fail-closed).
    """
    import sys

    sys.path.insert(0, str(MACRO))
    from macroapp import edition

    try:
        edition.set_product(edition.PRODUCT_PRO)
        assert edition.signature_candidates() == (edition.PRODUCT_PRO,)
        edition.set_product(edition.PRODUCT_STANDARD)
        candidates = edition.signature_candidates()
        assert edition.PRODUCT_PRO in candidates, "일반 빌드가 프로 키를 거부한다"
        assert "" in candidates, "일반 빌드가 롤백된 서버(v1)를 못 받는다"
    finally:
        edition.set_product(edition.PRODUCT_STANDARD)


def test_startup_recovers_leftover_suspensions():
    """네 번째 방어선이 **실제로 배선돼** 있어야 한다.

    ledger 를 이식해 놓고 app.main() 에서 부르지 않으면, 강제 종료된 뒤 게임이
    영구 정지 상태로 남는다 — 파일만 쌓이고 아무도 읽지 않는다.
    권한을 먼저 켜야 되살릴 수 있는 범위가 정지시킬 때와 같아진다(순서도 검사).
    """
    source = (MACRO / "macroapp" / "app.py").read_text(encoding="utf-8")
    assert "ledger.recover()" in source, "복구가 시작 경로에 배선되지 않았다"
    assert source.index("enable_debug_privilege()") < source.index("ledger.recover()"), (
        "권한을 켜기 전에 복구하면 열 수 있는 프로세스 범위가 달라진다"
    )


def test_window_close_cancels_the_exit_runner():
    """3중 방어의 2층 — 창을 닫으면 즉시 종료 워커를 세우고 재개를 기다린다."""
    source = (MACRO / "macroapp" / "gui.py").read_text(encoding="utf-8")
    close_at = source.index("def on_close(self)")
    tail = source[close_at : close_at + 1500]
    assert "shutdown(timeout=" in tail, "창 닫기에서 종료 워커를 세우지 않는다"


def test_the_ported_runner_kept_its_shape():
    """러너는 import 만 바꿔 이식했다 — 구조가 통째로 갈라지면 알려 준다.

    바이트 비교는 못 한다(import·상수 이름이 다르다). 대신 **함수와 클래스 이름**이
    같은지 본다. 한쪽에 새 메서드가 생기면 여기서 걸린다.
    """
    import ast

    def names(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found.add(node.name)
        return found

    mine = MACRO / "macroapp" / "exit_runner.py"
    theirs = MPAUSE / "mpauseapp" / "runner.py"
    if not theirs.exists():
        pytest.skip("형제 프로젝트가 없습니다")

    only_mine = names(mine) - names(theirs)
    only_theirs = names(theirs) - names(mine)
    assert not (only_mine or only_theirs), (
        f"러너 구조가 갈라졌습니다. mAuto 에만: {sorted(only_mine)} / "
        f"mPause 에만: {sorted(only_theirs)}"
    )
