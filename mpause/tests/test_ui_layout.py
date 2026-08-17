"""UI 레이아웃 회귀 — 어떤 창 크기에서도 위젯이 잘리지 않는지 본다.

mAuto 에서 오른쪽 열이 요구 높이보다 34px 모자라 카드 하단 라벨이 통째로
unmapped 됐던 전례가 있다. 그때 배운 것 두 가지를 그대로 적용한다.

  1) 특정 라벨 몇 개만 보면 안 된다. pack 은 **마지막 슬레이브부터** 자르므로
     새로 추가한 위젯이 잘려도 통과해 버린다 → 서브트리를 재귀로 전부 검사한다.
  2) 최소 크기에서도 검사한다. 기본 크기만 보면 minsize 가 방치된다.
"""

from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from mpauseapp.config import MIN_HEIGHT, MIN_WIDTH, WINDOW_HEIGHT, WINDOW_WIDTH
from mpauseapp.gui import PauseApp


@pytest.fixture
def root():
    try:
        window = tk.Tk()
    except tk.TclError as exc:  # 디스플레이 없는 환경
        pytest.skip(f"tkinter 를 띄울 수 없습니다: {exc}")
    yield window
    try:
        window.destroy()
    except Exception:
        pass


def walk(widget):
    """위젯 트리를 재귀로 훑는다."""
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def squashed(widget) -> list[str]:
    """요구 크기보다 작게 배치되었거나 화면에서 사라진 위젯을 찾는다."""
    problems = []
    for item in walk(widget):
        # 아직 배치되지 않은(pack/grid 안 한) 위젯은 검사 대상이 아니다.
        if not item.winfo_manager():
            continue
        if not item.winfo_ismapped():
            problems.append(f"{item} 가 화면에서 사라짐(unmapped)")
            continue
        if item.winfo_height() < item.winfo_reqheight():
            problems.append(
                f"{item} 높이 부족: {item.winfo_height()} < {item.winfo_reqheight()}"
            )
        if item.winfo_width() < item.winfo_reqwidth():
            problems.append(
                f"{item} 폭 부족: {item.winfo_width()} < {item.winfo_reqwidth()}"
            )
    return problems


@pytest.mark.parametrize(
    "size",
    [
        (WINDOW_WIDTH, WINDOW_HEIGHT),   # 기본 크기
        (MIN_WIDTH, MIN_HEIGHT),         # 최소 크기
        (WINDOW_WIDTH + 300, WINDOW_HEIGHT + 240),  # 크게 늘렸을 때
    ],
)
def test_no_widget_is_squashed(root, size):
    app = PauseApp(root, preview=True)
    width, height = size
    root.geometry(f"{width}x{height}")
    root.update_idletasks()
    root.update()
    problems = squashed(root)
    assert not problems, "\n".join(problems[:12])
    assert app.run_btn.winfo_ismapped()


def test_bottom_hint_does_not_squash_the_card(root):
    """하단 안내는 평소엔 비어 있다가 나중에 채워진다 — 그때도 안 잘려야 한다.

    pack 은 마지막 슬레이브부터 자르므로, 빈 상태만 검사하면 안내가 뜨는
    순간 카드 아래쪽이 통째로 사라지는 것을 놓친다.
    """
    app = PauseApp(root, preview=True)
    app.hint_var.set("관리자 권한으로 실행하면 더 안정적으로 동작합니다.")
    root.geometry(f"{MIN_WIDTH}x{MIN_HEIGHT}")
    root.update_idletasks()
    root.update()
    problems = squashed(root)
    assert not problems, "\n".join(problems[:12])


def test_defaults_are_visible(root):
    app = PauseApp(root, preview=True)
    root.update_idletasks()
    root.update()
    assert app.status_var.get() == "대기 중"
    assert app.progress.get() == 0.0


def test_ui_has_no_input_fields(root):
    """설정값이 고정이므로 입력칸이 하나도 없어야 한다.

    입력칸은 그 자체로 '무엇을 대상으로 삼는다'는 힌트이고, 값을 바꿔 가며
    동작을 관찰당하는 통로이기도 하다.
    """
    PauseApp(root, preview=True)
    root.update_idletasks()
    entries = [w for w in walk(root) if isinstance(w, (tk.Entry, tk.Text, tk.Spinbox))]
    assert not entries, f"입력 위젯이 남아 있습니다: {entries}"


def test_expired_license_blocks_run(root):
    """만료 뒤에는 버튼을 눌러도 워커가 시작되지 않는다(로컬 강제)."""
    app = PauseApp(root, preview=True, remaining_seconds=1)
    app._license_deadline = 0.0  # 이미 지난 시점으로 만든다
    app._on_run()
    root.update()
    assert not app.runner.busy
    assert "만료" in app.status_var.get()


def test_progress_bar_reflects_tick(root):
    """워커가 보낸 진행률이 진행바에 그대로 반영된다."""
    from mpauseapp import runner as runner_mod

    app = PauseApp(root, preview=True)
    app._handle_event(runner_mod.Event(runner_mod.EVT_TICK, progress=0.42))
    root.update_idletasks()
    assert abs(app.progress.get() - 0.42) < 1e-9


def test_status_colors_reflect_meaning(root):
    app = PauseApp(root, preview=True)
    from mpauseapp import runner as runner_mod, ui_kit

    app._set_status("오류가 발생했습니다")
    assert app.status_label.cget("fg") == ui_kit.COLORS["danger"]
    app._set_status(runner_mod.MSG_DONE)
    assert app.status_label.cget("fg") == ui_kit.COLORS["ok"]
