"""mPause 실행 진입점.

⚠️ import 문 자체를 try 안에 둔다.
네이티브 import 실패는 `from mpauseapp.app import main` 을 '불러오는 도중'에
터진다. main() 호출만 감싸면 이미 늦어서, 콘솔이 꺼진 빌드(--windows-console-mode
=disable)에서는 아무 메시지 없이 조용히 종료된다(= '더블클릭해도 무반응').
mAuto 에서 실제로 겪은 문제라 같은 진단 패턴을 그대로 가져왔다.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _writable_log_dir() -> Path:
    """로그를 쓸 수 있는 첫 번째 폴더를 찾는다(설치 폴더 → LOCALAPPDATA → TEMP)."""
    import os
    import tempfile

    candidates = []
    argv0 = sys.argv[0] if sys.argv and sys.argv[0] else ""
    if argv0:
        candidates.append(Path(os.path.abspath(argv0)).parent)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "mPause")
    candidates.append(Path(tempfile.gettempdir()))

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".mpause_write_test"
            probe.write_text("", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except Exception:
            continue
    return Path(tempfile.gettempdir())


def _report(exc: BaseException) -> None:
    """콘솔이 없어도 사용자가 원인을 알 수 있게 파일 + 메시지 상자로 알린다."""
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        (_writable_log_dir() / "startup_error.log").write_text(detail, encoding="utf-8")
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            f"mPause 를 시작하지 못했습니다.\n\n{exc}\n\n"
            "startup_error.log 파일에 자세한 내용이 기록되었습니다.",
            "mPause 시작 오류",
            0x10,
        )
    except Exception:
        pass


def main() -> int:
    try:
        from mpauseapp.app import main as app_main
    except BaseException as exc:  # noqa: BLE001 - import 단계 실패를 잡는 것이 목적
        _report(exc)
        return 1

    try:
        return app_main()
    except BaseException as exc:  # noqa: BLE001
        _report(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
