"""경로·버전 단일 기준.

PyInstaller(sys.frozen)와 Nuitka(__compiled__)를 모두 감지해, 어디서 실행하든
version.txt / license.key / custom_targets / logs 가 있는 폴더를 정확히 찾습니다.
이 모듈 하나만 고치면 모든 호출부의 경로가 일관됩니다.

주의: Nuitka onefile에서 sys.executable은 '임시 압축해제 폴더'를 가리킵니다.
과거 이 값을 썼다가 license.key와 로그가 매 실행마다 사라지는 임시 폴더에
저장되고, 런처는 version.txt를 못 찾아 0.0.0으로 읽는 버그가 있었습니다.
원본 exe 경로를 보존하는 sys.argv[0] → GetModuleFileNameW 순서로 찾습니다.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """PyInstaller 또는 Nuitka로 빌드된 실행 파일인지 확인합니다."""
    # Nuitka는 sys.frozen을 설정하지 않고 각 모듈 전역에 __compiled__를 넣습니다.
    return bool(getattr(sys, "frozen", False)) or ("__compiled__" in globals())


def _frozen_exe_path() -> Path:
    """사용자가 실제로 실행한 exe의 경로를 반환합니다 (임시 폴더 아님)."""
    argv0 = sys.argv[0] if sys.argv and sys.argv[0] else ""
    if argv0:
        candidate = Path(os.path.abspath(argv0))
        if candidate.is_file():
            return candidate
    try:
        buf = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetModuleFileNameW(None, buf, 32768):
            return Path(buf.value)
    except Exception:
        pass
    return Path(sys.executable)


def app_dir() -> Path:
    """실행 파일(또는 소스)이 위치한 기준 디렉터리를 반환합니다."""
    if is_frozen():
        return _frozen_exe_path().resolve().parent
    # 소스 실행: macroapp/paths.py -> 상위(패키지) -> 상위(레포 루트)
    return Path(__file__).resolve().parent.parent


def read_version() -> str:
    """version.txt에서 버전 문자열을 읽습니다. 실패 시 0.0.0."""
    try:
        # utf-8-sig: Notepad 등이 추가한 BOM도 안전하게 처리.
        return (app_dir() / "version.txt").read_text(encoding="utf-8-sig").strip()
    except Exception:
        return "0.0.0"


APP_VERSION = read_version()
