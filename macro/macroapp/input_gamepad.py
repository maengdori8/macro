"""가상 Xbox 게임패드 수명주기 + 버튼/트리거 입력. 패드 싱글톤과 키 매핑 소유."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from macroapp import winapi

gamepad: Optional[Any] = None
# RLock(재진입 락): 입력 시퀀스 전체를 잠근 채 그 안에서 _get_gamepad()를 다시
# 호출해도 같은 스레드면 데드락 없이 통과합니다.
_gamepad_lock = threading.RLock()

# 연속 입력 시 게임의 폴링이 '뗌→누름' 상승엣지를 반드시 한 프레임 이상 보도록,
# release 직후 중립 상태를 잠깐 유지합니다(엣지 병합으로 인한 입력 누락 방지).
_POST_RELEASE_SETTLE_SECONDS = 0.02

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
            # 생성 직후 중립 리포트를 명시적으로 한 번 보내, 이전 세션의 잔류 비트가
            # 없는 깨끗한 상태에서 첫 입력이 나가도록 합니다.
            try:
                gamepad.reset()
                gamepad.update()
            except Exception:
                pass
        return gamepad


def send_gamepad_button(button: Any, press_delay: float = 0.08) -> bool:
    """vgamepad Xbox 컨트롤러 버튼을 눌렀다 뗍니다.

    누름→업데이트→뗌→업데이트 전체를 락으로 묶어, 메인 매칭 루프와 SKIP 워커
    스레드가 같은 가상패드 리포트를 동시에 건드려 비트가 섞이는(입력 병합/누락)
    문제를 막습니다. 공유 리포트의 일관성이 곧 입력 정확도입니다.
    """
    with _gamepad_lock:
        pad = _get_gamepad()
        pressed = False
        try:
            pad.press_button(button=button)
            pressed = True
            pad.update()
            time.sleep(press_delay)
        finally:
            # 홀드 도중 예외가 나도 A가 가상패드에 눌린 채 남지 않게 반드시 해제합니다.
            if pressed:
                pad.release_button(button=button)
                pad.update()
            time.sleep(_POST_RELEASE_SETTLE_SECONDS)
    return True


def send_gamepad_trigger(side: str, press_delay: float = 0.08) -> bool:
    """vgamepad LT/RT 트리거를 당겼다 놓습니다(시퀀스 전체를 락으로 원자화)."""
    with _gamepad_lock:
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
        time.sleep(_POST_RELEASE_SETTLE_SECONDS)
    return True
