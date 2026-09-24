"""테스트가 매크로 창을 띄우지 않는다는 보호 자체를 고정한다.

2026-09-24: 전체 테스트 한 번에 매크로 프로그램이 15초 동안 9번 켜졌다 꺼졌다.
conftest.py 의 _gui_tests_never_show_windows 가 빠지면 이 테스트가 먼저 실패한다.
"""

from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")


def test_새_Tk_창은_숨긴_투명_상태로_시작한다() -> None:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"tkinter 를 띄울 수 없습니다: {exc}")
    try:
        root.update_idletasks()
        assert root.state() == "withdrawn"
        assert float(root.attributes("-alpha")) == 0.0
        assert not root.winfo_ismapped()
    finally:
        root.destroy()


def test_앱이_창을_앞으로_끌어오지_않는다() -> None:
    from macroapp.gui import AutomationApp

    calls = []
    fake = type("FakeRoot", (), {
        "update_idletasks": lambda self: calls.append("update"),
        "deiconify": lambda self: calls.append("deiconify"),
        "lift": lambda self: calls.append("lift"),
        "focus_force": lambda self: calls.append("focus_force"),
        "attributes": lambda self, *a: calls.append(("attributes", a)),
        "after": lambda self, *a: calls.append("after"),
    })()
    app = AutomationApp.__new__(AutomationApp)
    app.root = fake
    app._bring_window_to_front()
    assert calls == [], f"테스트 중 창을 앞으로 끌어왔다: {calls}"
