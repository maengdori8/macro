"""테스트가 어느 파일부터 수집되든 레포 루트를 import 경로에 넣습니다.

지금까지는 알파벳순 첫 테스트가 sys.path를 심어주는 우연에 기대고 있어서
`pytest tests/test_session.py` 처럼 단독 실행하면 ModuleNotFoundError가 났습니다.
"""

from pathlib import Path
import sys

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ── 테스트가 진짜 매크로 창을 띄우거나 포커스를 뺏지 않게 한다 ──────────────
# 2026-09-24 실측: 전체 테스트 한 번에 매크로 프로그램이 15초 동안 9번 켜졌다 꺼졌다
# (logs/macro_2026-09-24.log 19:06:05~20). AutomationApp.__init__ 이
# _bring_window_to_front() 로 창을 띄우고 1.2초 동안 최상위 고정 + focus_force 를 하는데,
# test_exit_feature 는 그걸 안 막고 있었다. 사용자가 게임 중이면 창이 뜰 때마다 게임
# 포커스까지 빼앗긴다.
# → 모든 테스트에서 (1) 앞으로 끌어오기를 끄고 (2) 새 Tk 창은 숨긴 투명 상태로 시작한다.
#   창이 실제로 매핑돼야 하는 테스트(test_ui_layout)는 스스로 투명(alpha 0)인 채 deiconify 한다.
#   tests/test_gui_tests_stay_hidden.py 가 이 보호 자체를 고정한다.
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _gui_tests_never_show_windows(monkeypatch):
    try:
        import tkinter as tk
    except Exception:  # tkinter 없는 환경 — 띄울 창도 없다
        yield
        return

    original_init = tk.Tk.__init__

    def hidden_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        # 이벤트 루프가 돌기 전(=화면에 그려지기 전)에 숨겨야 한 번도 안 보인다.
        try:
            self.withdraw()
            self.attributes("-alpha", 0.0)
        except tk.TclError:
            pass

    monkeypatch.setattr(tk.Tk, "__init__", hidden_init)
    try:
        from macroapp.gui import AutomationApp
    except Exception:
        yield
        return
    monkeypatch.setattr(AutomationApp, "_bring_window_to_front", lambda self: None)
    yield
