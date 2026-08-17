"""진입점 — 라이센스 인증 게이트를 통과해야 본 화면이 만들어진다.

인증에 실패하면 PauseApp 객체 자체가 생성되지 않는다(mAuto 와 같은 구조).
"""

from __future__ import annotations

import sys
import tkinter as tk

from mpauseapp import ledger, winproc
from mpauseapp.gui import PauseApp
from mpauseapp.license_dialog import LicenseDialog
from mpauseapp.paths import app_dir, is_frozen


def _launch(root: tk.Tk, key: str, remaining_seconds: int) -> None:
    PauseApp(root, license_key=key, remaining_seconds=remaining_seconds)


def _preview_allowed() -> bool:
    """--preview 는 **소스 실행에서만** 허용한다.

    ⚠️ 이 가드를 절대 지우지 말 것. 없으면 `mpause.exe --preview` 한 줄로
    라이센스 게이트가 통째로 열린다 — Ed25519 서명·nonce·hwid·제품 바인딩으로
    막아 둔 것이 커맨드라인 문자열 하나로 무력화된다(적대적 리뷰에서 실측 확인).
    빌드된 exe 에서는 인자를 무시하고 정상 인증 경로로 간다.
    UI 회귀 테스트는 이 플래그를 쓰지 않고 PauseApp(preview=True) 를 직접 만든다.
    """
    return "--preview" in sys.argv and not is_frozen()


def main() -> int:
    # 관리자 권한이 있으면 SeDebugPrivilege 를 켜 둔다. 이게 없으면 권한이 있어도
    # 다른 세션·다른 사용자의 프로세스를 열지 못해 '접근 거부'가 난다.
    # 실패해도 무시한다 — 같은 사용자 프로세스는 이 권한 없이도 제어된다.
    winproc.enable_debug_privilege()

    # 지난 실행이 비정상으로 끝나(작업 관리자 강제 종료·크래시·설치 프로그램의
    # 강제 닫기) 되살리지 못한 것이 있으면 **UI 를 만들기 전에** 먼저 되살린다.
    # 3중 방어는 전부 프로세스 안에 있어서 그 경로들에서는 하나도 돌지 않는다.
    # 권한 설정 뒤에 부르는 순서가 중요하다(그래야 열 수 있는 범위가 같다).
    ledger.recover()

    root = tk.Tk()

    if _preview_allowed():
        PauseApp(root, preview=True)
    else:
        LicenseDialog(root, app_dir(), _launch)

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
