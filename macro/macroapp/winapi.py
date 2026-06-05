"""Windows 전용 의존성을 한 곳에서 가드 임포트하는 키스톤 모듈.

다른 모듈은 반드시 `from macroapp import winapi` 후 `winapi.win32gui` 처럼
'속성'으로 접근해야 합니다. `from macroapp.winapi import win32gui`로 가져오면
Mac에서 None이 고정 바인딩되어 Windows에서도 None이 되는 치명적 버그가 생깁니다.
"""

from __future__ import annotations

import platform
from typing import Any

# 필요 패키지: pip install vgamepad
try:
    import vgamepad as vg
except (ImportError, OSError, Exception) as exc:  # noqa: BLE001
    vg = None
    VGAMEPAD_IMPORT_ERROR = exc
else:
    VGAMEPAD_IMPORT_ERROR = None

try:
    import pyautogui
except ImportError as exc:
    pyautogui = None
    PYAUTOGUI_IMPORT_ERROR = exc
else:
    PYAUTOGUI_IMPORT_ERROR = None
    pyautogui.FAILSAFE = True

try:
    import win32api  # type: ignore[reportMissingModuleSource]
    import win32con  # type: ignore[reportMissingModuleSource]
    import win32gui  # type: ignore[reportMissingModuleSource]
except ImportError as exc:
    win32api = None
    win32con = None
    win32gui = None
    WIN32_IMPORT_ERROR = exc
else:
    WIN32_IMPORT_ERROR = None

try:
    from windows_capture import Frame, WindowsCapture  # type: ignore[reportMissingModuleSource]
except (ImportError, OSError) as exc:
    Frame = Any
    WindowsCapture = None
    WINDOWS_CAPTURE_IMPORT_ERROR = exc
else:
    WINDOWS_CAPTURE_IMPORT_ERROR = None


def is_windows() -> bool:
    return platform.system() == "Windows"


def _require_win32con() -> Any:
    """Windows 메시지 상수를 쓰기 전에 win32con 로드를 확인합니다."""
    if win32con is None:
        raise RuntimeError("pywin32 win32con 모듈이 필요합니다.")
    return win32con
