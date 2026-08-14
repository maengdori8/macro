"""창 크기별로 위젯이 잘리지 않는지 검사하는 레이아웃 회귀 테스트.

배경: 우측 열은 pack_propagate(False)라 요구 높이가 1로 보고된다. 그래서 Tk도
minsize 상수도 '세로가 모자란다'는 사실을 알 수 없고, 우측 열에 위젯을 추가해도
아무 경고 없이 세션 패널의 마지막 위젯부터 조용히 잘렸다. 이 테스트가 그 부채를
숫자로 드러낸다.

판정은 절대 픽셀이 아니라 위젯 자신의 요구 크기와 비교한다(폰트가 플랫폼마다 달라
절대값을 박으면 곧 거짓 실패가 된다). ismapped를 함께 보는 이유는, tkinter가
unmapped 위젯의 마지막 geometry를 그대로 유지해서 높이 비교만으로는 '화면에서
사라진' 위젯을 놓치기 때문이다.
"""

from __future__ import annotations

import tkinter as tk
import unittest
from unittest import mock


def _tk_available() -> bool:
    try:
        probe = tk.Tk()
    except Exception:
        return False
    probe.destroy()
    return True


TK_AVAILABLE = _tk_available()


def scan_clipped(widget: tk.Misc) -> list[str]:
    """widget 이하 서브트리에서 잘린 위젯을 (경로, 부족분) 문자열로 모읍니다.

    단건이 아니라 서브트리 전체를 보는 이유: 새로 추가한 위젯이 잘리는 경우를
    잡아야 하는데, pack은 마지막 슬레이브부터 자르므로 기존 위젯만 검사하면
    화면이 깨져도 테스트는 통과한다.
    """

    problems: list[str] = []
    for child in widget.winfo_children():
        problems.extend(scan_clipped(child))

    # 컨테이너는 자식이 온전하면 충분하고, 내용이 없는 여백/구분선은 검사 대상이 아니다.
    request_height = widget.winfo_reqheight()
    request_width = widget.winfo_reqwidth()
    if request_height <= 1 and request_width <= 1:
        return problems

    if not widget.winfo_ismapped():
        problems.append(f"{widget} 화면에서 사라짐(unmapped)")
        return problems

    height_debt = request_height - widget.winfo_height()
    width_debt = request_width - widget.winfo_width()
    if height_debt > 0:
        problems.append(f"{widget} 세로 {height_debt}px 부족")
    if width_debt > 0:
        problems.append(f"{widget} 가로 {width_debt}px 부족")
    return problems


@unittest.skipUnless(TK_AVAILABLE, "tkinter 디스플레이를 열 수 없는 환경입니다.")
class UiLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        from macroapp.gui import AutomationApp

        self.root = tk.Tk()
        self.root.withdraw()
        # 테스트가 사용자 포커스를 빼앗지 않게 전면화만 막습니다.
        with mock.patch.object(AutomationApp, "_bring_window_to_front", lambda _self: None):
            self.app = AutomationApp(self.root, license_key=None, preview=True)
        # 앱이 선언한 minsize는 리사이즈로 건드리기 전에 잡아둡니다.
        # (_resize가 창 관리자 클램프를 피하려고 minsize를 임시로 낮춥니다.)
        self.declared_minsize = tuple(self.app.root.minsize())
        # 완전히 투명하게 띄웁니다. withdraw 상태에서는 모든 위젯 높이가 1이라 측정이 무의미합니다.
        self.root.attributes("-alpha", 0.0)
        self.root.deiconify()

    def tearDown(self) -> None:
        # closing을 먼저 세워야 after 재예약이 멈춰 destroy 뒤 'invalid command name'이 안 납니다.
        self.app.closing = True
        # 기록 스레드를 실제로 멈춥니다. 안 그러면 테스트마다 하나씩 쌓여
        # 파괴된 앱을 참조한 채 같은 SQLite 파일을 물고 남습니다.
        try:
            self.app._drain_match_writer()
        except Exception:
            pass
        try:
            self.app._close_log_file()
        except Exception:
            pass
        self.root.destroy()

    def _settle(self) -> None:
        self.root.update_idletasks()
        self.root.update()

    def _resize(self, width: int, height: int) -> tuple[int, int]:
        # minsize를 잠시 낮춰야 창 관리자가 요청 크기를 잘라내지 않습니다.
        self.root.minsize(320, 240)
        self.root.geometry(f"{width}x{height}")
        self._settle()
        return self.root.winfo_width(), self.root.winfo_height()

    def _sizes(self) -> list[tuple[str, int, int]]:
        declared_width, declared_height = self.declared_minsize
        return [
            # 선언한 minsize는 '지킬 수 있는 계약'이어야 합니다.
            ("minsize", declared_width, declared_height),
            ("기본 실행 크기", 1320, 780),
            ("사용자 스크린샷", 1302, 776),
        ]

    def test_pages_are_not_clipped(self) -> None:
        for page in ("automation", "mining"):
            for label, width, height in self._sizes():
                with self.subTest(page=page, size=label):
                    self.app._show_page(page)
                    actual_width, actual_height = self._resize(width, height)
                    problems = scan_clipped(self.app._pages[page])
                    self.assertEqual(
                        problems,
                        [],
                        f"{page} 페이지 {actual_width}x{actual_height}에서 잘린 위젯:\n  "
                        + "\n  ".join(problems),
                    )

    def test_session_panel_values_are_visible(self) -> None:
        """세션 카드의 값 라벨은 어떤 크기에서도 보여야 합니다(이번 버그의 증상 지점)."""

        self.app._show_page("automation")
        for label, width, height in self._sizes():
            with self.subTest(size=label):
                actual_width, actual_height = self._resize(width, height)
                problems = scan_clipped(self.app.session_panel)
                self.assertEqual(
                    problems,
                    [],
                    f"세션 패널 {actual_width}x{actual_height}에서 잘린 위젯:\n  "
                    + "\n  ".join(problems),
                )


if __name__ == "__main__":
    unittest.main()
