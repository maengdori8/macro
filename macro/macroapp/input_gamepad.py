"""가상 Xbox 게임패드 수명주기 + 버튼/트리거 입력. 패드 싱글톤과 키 매핑 소유."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from macroapp import winapi

gamepad: Optional[Any] = None
_gamepad_lock = threading.Lock()

# 트리거 키 이름 (버튼이 아닌 아날로그 입력)
TRIGGER_KEYS = {"lt", "rt"}

KEY_TO_GAMEPAD: dict[str, Any] = {}


def build_key_to_gamepad() -> dict[str, Any]:
    """winapi.vg가 로드된 경우 키→Xbox버튼 매핑을 생성합니다."""
    if winapi.vg is None:
        return {}
    B = winapi.vg.XUSB_BUTTON
    return {
        # 별칭 (기존 호환)
        "esc": B.XUSB_GAMEPAD_START,
        "escape": B.XUSB_GAMEPAD_START,
        "s": B.XUSB_GAMEPAD_A,
        # Xbox 버튼 직접 지정용 (targets.json의 key에 아래 이름 사용)
        "a": B.XUSB_GAMEPAD_A,
        "b": B.XUSB_GAMEPAD_B,
        "x": B.XUSB_GAMEPAD_X,
        "y": B.XUSB_GAMEPAD_Y,
        "lb": B.XUSB_GAMEPAD_LEFT_SHOULDER,
        "rb": B.XUSB_GAMEPAD_RIGHT_SHOULDER,
        "back": B.XUSB_GAMEPAD_BACK,
        "select": B.XUSB_GAMEPAD_BACK,
        "start": B.XUSB_GAMEPAD_START,
        "guide": B.XUSB_GAMEPAD_GUIDE,
        "lstick": B.XUSB_GAMEPAD_LEFT_THUMB,
        "rstick": B.XUSB_GAMEPAD_RIGHT_THUMB,
        "up": B.XUSB_GAMEPAD_DPAD_UP,
        "down": B.XUSB_GAMEPAD_DPAD_DOWN,
        "left": B.XUSB_GAMEPAD_DPAD_LEFT,
        "right": B.XUSB_GAMEPAD_DPAD_RIGHT,
    }


KEY_TO_GAMEPAD = build_key_to_gamepad()


def _get_gamepad() -> Any:
    """vgamepad VX360Gamepad 인스턴스를 1회만 생성해서 재사용합니다(스레드 안전)."""
    global gamepad, KEY_TO_GAMEPAD
    if winapi.vg is None:
        raise RuntimeError(
            "vgamepad 모듈을 불러올 수 없습니다. Windows에서 'pip install vgamepad'를 실행하세요."
        )
    with _gamepad_lock:
        if not KEY_TO_GAMEPAD:
            KEY_TO_GAMEPAD = build_key_to_gamepad()
        if gamepad is None:
            gamepad = winapi.vg.VX360Gamepad()
        return gamepad


def send_gamepad_button(button: Any, press_delay: float = 0.08) -> bool:
    """vgamepad Xbox 컨트롤러 버튼을 눌렀다 뗍니다."""
    pad = _get_gamepad()
    pad.press_button(button=button)
    pad.update()
    time.sleep(press_delay)
    pad.release_button(button=button)
    pad.update()
    return True


def send_gamepad_trigger(side: str, press_delay: float = 0.08) -> bool:
    """vgamepad LT/RT 트리거를 당겼다 놓습니다."""
    pad = _get_gamepad()
    if side == "lt":
        pad.left_trigger(value=255)
        pad.update()
        time.sleep(press_delay)
        pad.left_trigger(value=0)
        pad.update()
    else:
        pad.right_trigger(value=255)
        pad.update()
        time.sleep(press_delay)
        pad.right_trigger(value=0)
        pad.update()
    return True
