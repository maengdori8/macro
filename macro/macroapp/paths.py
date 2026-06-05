"""경로·버전 단일 기준.

PyInstaller(sys.frozen)와 Nuitka(__compiled__)를 모두 감지해, 어디서 실행하든
version.txt / targets.json / target_*.png / license.key 가 있는 폴더를 정확히 찾습니다.
이 모듈 하나만 고치면 모든 호출부의 경로가 일관됩니다.
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """PyInstaller 또는 Nuitka로 빌드된 실행 파일인지 확인합니다."""
    # Nuitka는 sys.frozen을 설정하지 않고 각 모듈 전역에 __compiled__를 넣습니다.
    return bool(getattr(sys, "frozen", False)) or ("__compiled__" in globals())


def app_dir() -> Path:
    """실행 파일(또는 소스)이 위치한 기준 디렉터리를 반환합니다."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # 소스 실행: macroapp/paths.py -> 상위(패키지) -> 상위(레포 루트)
    return Path(__file__).resolve().parent.parent


def read_version() -> str:
    """version.txt에서 버전 문자열을 읽습니다. 실패 시 0.0.0."""
    try:
        return (app_dir() / "version.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return "0.0.0"


APP_VERSION = read_version()
