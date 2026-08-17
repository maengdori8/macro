"""사용자에게 보이는 문구·위젯에 내부 동작이 드러나지 않는지 검사한다.

UI 를 아무리 비워도 상태 문구 한 줄이 동작을 설명해 버리면 소용이 없다.
그래서 두 방향으로 본다.

  1) 실제로 만들어진 위젯의 글자를 전부 훑는다(런타임).
  2) 사용자 문구가 사는 모듈의 **문자열 리터럴**을 AST 로 훑는다(정적).
     런타임 검사는 그 순간 화면에 있는 문구만 보므로, 오류 경로 문구처럼
     평소에 안 뜨는 것이 그대로 새어 나간다.

주석과 docstring 은 검사하지 않는다 — 화면에 나오지 않고, 빌드(-OO)에서
docstring 은 아예 빠진다. 코드를 읽는 사람에게는 설명이 남아 있어야 한다.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from mpauseapp import runner as runner_mod
from mpauseapp.config import TARGET_PROCESS_NAME

#: 화면 문구에 나오면 안 되는 말. 대상이 무엇인지, 무엇을 하는지 알려 주는 단어들.
BANNED = (
    "프로세스",
    "일시정지",
    "일시 정지",
    "정지",
    "재개",
    "다시 시작",
    "멈춘",
    "멈추",
    "PID",
    "핸들",
    "작업관리자",
    "작업 관리자",
    "suspend",
    "resume",
    TARGET_PROCESS_NAME,
)

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "mpauseapp"

#: 사용자 문구가 사는 모듈. 여기 있는 문자열은 화면에 뜰 수 있다고 본다.
USER_FACING_MODULES = ("gui.py", "runner.py")


def offenders(text: str) -> list[str]:
    lowered = str(text).lower()
    return [word for word in BANNED if word.lower() in lowered]


# ─── 1) 런타임: 실제 위젯 ───────────────────────────────────────────────────

tk = pytest.importorskip("tkinter")


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"tkinter 를 띄울 수 없습니다: {exc}")
    yield window
    try:
        window.destroy()
    except Exception:
        pass


def widget_texts(widget) -> list[str]:
    """위젯 트리에 실제로 그려지는 글자를 모은다(라벨·버튼·캔버스 텍스트)."""
    found: list[str] = []
    stack = [widget]
    while stack:
        item = stack.pop()
        stack.extend(item.winfo_children())
        try:
            keys = item.keys()
        except Exception:
            continue
        if "text" in keys:
            value = str(item.cget("text"))
            if value:
                found.append(value)
        if "textvariable" in keys:
            name = str(item.cget("textvariable"))
            if name:
                try:
                    found.append(str(item.tk.globalgetvar(name)))
                except Exception:
                    pass
        if isinstance(item, tk.Canvas):
            for handle in item.find_all():
                try:
                    if item.type(handle) == "text":
                        found.append(str(item.itemcget(handle, "text")))
                except Exception:
                    pass
    return [text for text in found if text.strip()]


def test_no_widget_text_reveals_behaviour(root):
    from mpauseapp.gui import PauseApp

    PauseApp(root, preview=True)
    root.update_idletasks()
    problems = [
        (text, offenders(text)) for text in widget_texts(root) if offenders(text)
    ]
    assert not problems, f"화면 글자에 내부 동작이 드러납니다: {problems}"


def test_status_messages_stay_neutral(root):
    """상태 문구가 바뀌어도 새지 않는지 — 러너가 쓰는 문구 전부를 검사한다."""
    messages = [
        value
        for name, value in vars(runner_mod).items()
        if name.startswith("MSG_") and isinstance(value, str)
    ]
    messages += [
        value for value in runner_mod._FOLLOWUP_MESSAGES.values() if value
    ]
    assert messages, "검사할 문구를 못 찾았다(상수 이름이 바뀌었나?)"
    problems = [(text, offenders(text)) for text in messages if offenders(text)]
    assert not problems, f"상태 문구에 내부 동작이 드러납니다: {problems}"


# ─── 2) 정적: 문자열 리터럴 ─────────────────────────────────────────────────


def literal_strings(path: pathlib.Path) -> list[str]:
    """docstring 을 뺀 모든 문자열 리터럴(f-string 의 고정 부분 포함)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))

    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                found.append(node.value)
    return found


@pytest.mark.parametrize("filename", USER_FACING_MODULES)
def test_module_string_literals_stay_neutral(filename):
    path = PACKAGE / filename
    problems = [
        (text, offenders(text)) for text in literal_strings(path) if offenders(text)
    ]
    assert not problems, f"{filename} 의 문자열에 내부 동작이 드러납니다: {problems}"


def test_error_messages_from_the_win32_layer_stay_neutral():
    """예외 문구는 오류 상황에만 뜨므로 런타임 검사로는 잡히지 않는다.

    winproc 은 기술 문자열(DLL 이름 등)을 쓰므로 전체를 검사하면 안 되고,
    **예외 생성자에 들어가는 문자열만** 본다 — 그게 화면에 뜨는 값이다.
    """
    tree = ast.parse((PACKAGE / "winproc.py").read_text(encoding="utf-8"))
    messages: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
        if not name.endswith("Error"):
            continue
        for argument in list(node.args) + [kw.value for kw in node.keywords]:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                messages.append(argument.value)
            elif isinstance(argument, ast.JoinedStr):
                for part in argument.values:
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        messages.append(part.value)

    assert messages, "검사할 예외 문구를 못 찾았다"
    problems = [(text, offenders(text)) for text in messages if offenders(text)]
    assert not problems, f"예외 문구에 내부 동작이 드러납니다: {problems}"


def test_display_name_does_not_hint_at_the_behaviour():
    """화면 이름에 동작을 알려 주는 단어가 들어가면 안 된다."""
    from mpauseapp.config import APP_NAME, APP_TITLE

    for name in (APP_NAME, APP_TITLE):
        assert "pause" not in name.lower(), f"표시 이름이 동작을 알려 준다: {name}"


def test_product_id_must_not_follow_the_display_name():
    """⚠️ 표시 이름과 제품 식별자는 **일부러 다르다.**

    보기 좋게 통일하려고 PRODUCT_ID 를 바꾸면 서명 메시지가 달라져 이미 발급된
    키가 전부 무효가 된다(서버는 문서의 product 와 요청의 product 가 같을 때만
    valid 를 서명한다). 이 테스트가 그 실수를 막는다.
    """
    from mpauseapp.config import APP_NAME, PRODUCT_ID

    assert PRODUCT_ID == "mpause", "제품 식별자를 바꾸면 발급된 키가 전부 무효가 된다"
    assert PRODUCT_ID != APP_NAME.lower()


def test_target_name_is_not_hardcoded_in_the_ui():
    """대상 이름은 config 한 곳에만 있어야 한다(UI 코드에 흘리지 않는다)."""
    for filename in USER_FACING_MODULES:
        source = (PACKAGE / filename).read_text(encoding="utf-8")
        assert TARGET_PROCESS_NAME not in source, f"{filename} 에 대상 이름이 박혀 있습니다"
