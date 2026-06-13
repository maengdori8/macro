"""Nuitka/PyInstaller 진입 스크립트 (레포 루트).

시작 단계, 특히 네이티브 DLL 로드(cv2 등)에서 죽으면 빌드가
--windows-console-mode=disable 라 아무 메시지도 없이 종료됩니다 — 클린 PC에서
'더블클릭해도 무반응'으로 보이는 가장 흔한 원인입니다. 그래서 startup 전체를
감싸 오류를 로그 파일 + 메시지박스로 남깁니다.

핵심: cv2 같은 네이티브 import 실패는 `from macroapp.app import main`을 '불러오는
도중'에 터집니다(app.py가 모듈 최상단에서 gui를 import → gui가 cv2를 import). 따라서
import 문 자체를 try 안에 두어야 진단이 동작합니다. main() 호출만 감싸면 늦습니다.
"""

import sys


def _writable_log_dir():
    """startup_error.log를 쓸 수 있는 폴더를 찾습니다.

    설치 폴더(관리자 권한으로 실행되므로 보통 가능) → %LOCALAPPDATA%\\Macro(권한 무관
    항상 가능) → TEMP 순서. 바로가기로 실행하면 cwd가 System32라 쓰기 불가일 수 있어
    cwd는 쓰지 않습니다.
    """
    import os
    import tempfile

    candidates = []
    # 1) 설치 폴더 (paths.py는 stdlib+ctypes만 import하므로 cv2 실패와 무관하게 동작)
    try:
        from macroapp.paths import app_dir
        candidates.append(str(app_dir()))
    except Exception:
        if getattr(sys, "frozen", False) or ("__compiled__" in globals()):
            argv0 = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
            if argv0 and os.path.isfile(argv0):
                candidates.append(os.path.dirname(argv0))
    # 2) %LOCALAPPDATA%\Macro
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "Macro"))
    # 3) TEMP
    candidates.append(tempfile.gettempdir())

    for d in candidates:
        try:
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "startup_error.log"), "a", encoding="utf-8"):
                pass
            return d
        except Exception:
            continue
    return None


def _report_startup_failure(exc: BaseException) -> None:
    """창이 뜨기 전 죽은 오류를 로그와 메시지박스로 사용자에게 보여줍니다."""
    import os
    import traceback

    try:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    except Exception:
        tb = repr(exc)

    low = (str(exc) + "\n" + tb).lower()
    # cv2가 DLL 로드 단계에서 실패하면 보통 미디어 기능(Media Foundation) 누락이 원인.
    looks_like_media = ("cv2" in low) and (
        "dll load failed" in low
        or "지정된 모듈" in low
        or "module could not be found" in low
        or "specified module could not be found" in low
    )

    log_dir = _writable_log_dir()
    log_path = None
    if log_dir:
        try:
            import datetime
            log_path = os.path.join(log_dir, "startup_error.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(
                    f"[Macro 시작 실패] {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n"
                    f"python={sys.version}\nexe={sys.executable}\nargv={sys.argv}\n\n"
                    f"{tb}\n{'=' * 60}\n"
                )
        except Exception:
            log_path = None

    msg = "프로그램을 시작하지 못했습니다.\n\n"
    if looks_like_media:
        msg += (
            "가능한 원인: 이 PC에 'Media Feature Pack'(미디어 기능)이 없습니다.\n"
            "Windows N/KN 에디션에서 자주 발생합니다.\n"
            "→ 설정 > 앱 > 선택적 기능 > 기능 추가 에서 'Media Feature Pack'을\n"
            "  설치한 뒤 다시 실행해 보세요.\n\n"
        )
    if log_path:
        msg += f"자세한 오류가 저장된 위치:\n{log_path}\n\n"
    msg += "아래 내용을 개발자에게 보내주세요:\n\n" + tb[-1000:]

    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "Macro 시작 오류", 0x10)  # MB_ICONERROR
    except Exception:
        pass


def main() -> None:
    try:
        from macroapp.app import main as app_main
        app_main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - 시작 실패를 사용자에게 보여주기 위함
        _report_startup_failure(exc)
        # 소스(비프리즈) 실행 땐 원래대로 예외를 다시 던져 디버깅을 돕습니다.
        if not (getattr(sys, "frozen", False) or ("__compiled__" in globals())):
            raise


if __name__ == "__main__":
    main()
