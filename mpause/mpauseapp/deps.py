"""네이티브·Windows 전용 의존성을 한 곳에서 가드 임포트하는 키스톤 모듈.

다른 모듈은 반드시 ``from mpauseapp import deps`` 후 ``deps.win32gui`` 처럼
**속성으로** 접근한다. ``from mpauseapp.deps import win32gui`` 로 가져오면
로드 실패 환경에서 None 이 고정 바인딩돼, 나중에 로드에 성공해도 계속 None 인
치명적 버그가 생긴다(mAuto ``macroapp/winapi.py`` 와 같은 규칙).

여기서 무엇이 실패해도 **앱 본체는 그대로 돈다.** 이 의존성이 필요한 부분만
조용히 꺼지고, 핵심 동작(정지 → 유지 → 재개)은 표준 라이브러리만으로 돈다.
"""

from __future__ import annotations

import sys
from typing import Any

IS_WINDOWS = sys.platform == "win32"

try:
    import numpy as np
except Exception as exc:  # noqa: BLE001 - 없으면 인식 기능만 끈다
    np = None  # type: ignore[assignment]
    NUMPY_IMPORT_ERROR: Any = exc
else:
    NUMPY_IMPORT_ERROR = None

try:
    import cv2
except Exception as exc:  # noqa: BLE001
    cv2 = None  # type: ignore[assignment]
    CV2_IMPORT_ERROR: Any = exc
else:
    CV2_IMPORT_ERROR = None

try:
    import win32api  # type: ignore[reportMissingModuleSource]
    import win32con  # type: ignore[reportMissingModuleSource]
    import win32gui  # type: ignore[reportMissingModuleSource]
except Exception as exc:  # noqa: BLE001
    win32api = None  # type: ignore[assignment]
    win32con = None  # type: ignore[assignment]
    win32gui = None  # type: ignore[assignment]
    WIN32_IMPORT_ERROR: Any = exc
else:
    WIN32_IMPORT_ERROR = None

try:
    from windows_capture import Frame, WindowsCapture  # type: ignore[reportMissingModuleSource]
except Exception as exc:  # noqa: BLE001
    Frame = Any  # type: ignore[assignment]
    WindowsCapture = None  # type: ignore[assignment]
    WINDOWS_CAPTURE_IMPORT_ERROR: Any = exc
else:
    WINDOWS_CAPTURE_IMPORT_ERROR = None

# vgamepad 는 **import 만 해도** ViGEm 드라이버가 없으면 죽는다(OSError 가 아니라
# 다른 예외로도 나온다). 그래서 광범위하게 잡는다 — 여기서 죽으면 앱이 아예 안 뜬다.
try:
    import vgamepad as vg
except Exception as exc:  # noqa: BLE001
    vg = None  # type: ignore[assignment]
    VGAMEPAD_IMPORT_ERROR: Any = exc
else:
    VGAMEPAD_IMPORT_ERROR = None


def vision_ready() -> bool:
    """화면 인식에 필요한 것이 전부 로드됐는가."""
    return (
        IS_WINDOWS
        and cv2 is not None
        and np is not None
        and win32gui is not None
        and WindowsCapture is not None
    )
