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

MPAUSE = Path(__file__).resolve().parents[1]
MACRO = MPAUSE.parent / "macro"

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
    if not left.exists():
        pytest.skip(f"형제 프로젝트가 없습니다: {left}")
    assert right.exists(), f"{label}: {right} 가 없습니다"

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
    if not mine.exists():
        pytest.skip("형제 프로젝트가 없습니다")

    only_mine = names(mine) - names(theirs)
    only_theirs = names(theirs) - names(mine)
    assert not (only_mine or only_theirs), (
        f"러너 구조가 갈라졌습니다. mAuto 에만: {sorted(only_mine)} / "
        f"mPause 에만: {sorted(only_theirs)}"
    )
