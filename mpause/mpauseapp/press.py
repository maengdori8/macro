"""찾은 자리를 누르는 두 가지 방법.

mAuto(``macroapp/input_mouse.py`` + ``macroapp/input_gamepad.py``)에서 이식했다.

  click : 대상 창에 마우스 메시지를 보낸다(WM_MOUSEMOVE → LBUTTONDOWN → LBUTTONUP).
          목적지로 바로 튀지 않고 베지에 곡선을 따라 이동한 뒤 누른다.
          실제 커서를 옮기지 않으므로 사용자가 하던 작업을 방해하지 않는다.
  pad   : 가상 Xbox 패드(vgamepad/ViGEm)의 버튼을 누른다. 게임 메뉴는 패드
          입력을 받으므로, 좌표 클릭이 안 먹는 화면에서도 확정이 가능하다.

click 을 먼저 쓴다 — mAuto 가 같은 그림을 같은 방식으로 눌러 온 검증된 경로다.
그게 안 먹혔을 때만 pad 로 한 번 더 시도한다(followup 참조).
"""

from __future__ import annotations

import random
import threading
import time
from typing import Optional

from mpauseapp import deps

# ─── 곡선 이동 상수 (mAuto 와 같은 값) ─────────────────────────────────────
CURVED_CLICK_MIN_STEPS = 15
CURVED_CLICK_MAX_STEPS = 25
CURVED_CLICK_MOVE_DURATION_SECONDS = 0.2
CLICK_MESSAGE_DELAY_SECONDS = 0.01

#: 중심에서 이만큼까지 무작위로 흔들어서 누른다.
CLICK_JITTER_PIXELS = 3

_WM_MOUSEMOVE = 0x0200
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_MK_LBUTTON = 0x0001

_WM_ACTIVATE = 0x0006
_WA_ACTIVE = 1


def fake_focus(hwnd) -> bool:
    """패드를 누르기 직전 대상 창에 WM_ACTIVATE 가짜 포커스를 보낸다.

    게임은 **자기가 활성일 때만** 패드 상태를 읽는다(런타임이 스스로 게이팅).
    그래서 가상 패드 값이 전역으로 꽂혀 있어도 배경 창은 입력을 무시한다 —
    mpause 첫 실측에서 START 가 안 눌린 원인. 전면화 없이 이 메시지 하나로
    '활성'이라고 믿게 만드는 것이 mAuto 의 검증된 배경 입력 방식이다
    (macroapp gui.dispatch_key_press 와 동일, 화면 변화 0).
    """

    gui = deps.win32gui
    if gui is None or not hwnd:
        return False
    try:
        if not gui.IsWindow(hwnd):
            return False
        gui.PostMessage(hwnd, _WM_ACTIVATE, _WA_ACTIVE, 0)
        return True
    except Exception:
        return False


# ─── 마우스 메시지 ─────────────────────────────────────────────────────────


def _lparam(x: int, y: int) -> int:
    return (int(y) & 0xFFFF) << 16 | (int(x) & 0xFFFF)


def _post(hwnd: int, message: int, wparam: int, x: int, y: int) -> bool:
    gui = deps.win32gui
    if gui is None:
        return False
    if not gui.IsWindow(hwnd):
        return False
    gui.PostMessage(hwnd, message, wparam, _lparam(x, y))
    return True


def bezier_point(p1, p2, p3, t: float) -> tuple[int, int]:
    """2차 베지에 곡선 위의 한 점."""

    t = max(0.0, min(1.0, float(t)))
    inv = 1.0 - t
    x = inv * inv * p1[0] + 2 * inv * t * p2[0] + t * t * p3[0]
    y = inv * inv * p1[1] + 2 * inv * t * p2[1] + t * t * p3[1]
    return int(round(x)), int(round(y))


def _control_point(start, end) -> tuple[int, int]:
    """출발점과 목적지 사이에 무작위 휨을 가진 제어점."""

    dx = end[0] - start[0]
    dy = end[1] - start[1]
    distance = max(1.0, (dx * dx + dy * dy) ** 0.5)

    mid_x = (start[0] + end[0]) / 2.0
    mid_y = (start[1] + end[1]) / 2.0
    perp_x = -dy / distance
    perp_y = dx / distance

    bend = random.uniform(-0.35, 0.35) * distance
    if abs(bend) < 12.0 and distance >= 24.0:
        bend = 12.0 if bend >= 0 else -12.0

    control_x = mid_x + perp_x * bend + random.uniform(-8.0, 8.0)
    control_y = mid_y + perp_y * bend + random.uniform(-8.0, 8.0)
    return int(round(control_x)), int(round(control_y))


def _clamp(point, width: int, height: int) -> tuple[int, int]:
    max_x = max(0, int(width) - 1)
    max_y = max(0, int(height) - 1)
    return max(0, min(max_x, int(point[0]))), max(0, min(max_y, int(point[1])))


def jitter(center_x: int, center_y: int, size: tuple[int, int]) -> tuple[int, int]:
    """그림 영역 안에서 중심 주변으로 조금 흔든다(영역 밖으로 나가지 않게).

    size 는 (높이, 너비) — numpy shape 순서 그대로 받는다.
    """

    height, width = int(size[0]), int(size[1])
    max_x = min(CLICK_JITTER_PIXELS, max(0, (width - 1) // 2))
    max_y = min(CLICK_JITTER_PIXELS, max(0, (height - 1) // 2))
    return (
        center_x + random.randint(-max_x, max_x),
        center_y + random.randint(-max_y, max_y),
    )


def curved_click(
    hwnd: int,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    steps: Optional[int] = None,
) -> bool:
    """클라이언트 좌표 기준으로 곡선 이동 후 왼쪽 클릭 메시지를 보낸다."""

    gui = deps.win32gui
    if gui is None or not gui.IsWindow(hwnd):
        return False

    try:
        left, top, right, bottom = gui.GetClientRect(hwnd)
    except Exception:
        return False
    width = int(right - left)
    height = int(bottom - top)
    if width <= 0 or height <= 0:
        return False

    if steps is None:
        steps = random.randint(CURVED_CLICK_MIN_STEPS, CURVED_CLICK_MAX_STEPS)
    steps = max(2, int(steps))
    step_delay = max(0.0, CURVED_CLICK_MOVE_DURATION_SECONDS) / max(1, steps - 1)

    start_pos = _clamp(start, width, height)
    end_pos = _clamp(end, width, height)
    control = _clamp(_control_point(start_pos, end_pos), width, height)

    for index in range(steps):
        t = index / (steps - 1)
        move = _clamp(bezier_point(start_pos, control, end_pos, t), width, height)
        _post(hwnd, _WM_MOUSEMOVE, 0, move[0], move[1])
        if index < steps - 1 and step_delay > 0:
            time.sleep(step_delay)

    _post(hwnd, _WM_LBUTTONDOWN, _MK_LBUTTON, end_pos[0], end_pos[1])
    if CLICK_MESSAGE_DELAY_SECONDS > 0:
        time.sleep(CLICK_MESSAGE_DELAY_SECONDS)
    _post(hwnd, _WM_LBUTTONUP, 0, end_pos[0], end_pos[1])
    return True


# ─── 가상 패드 ─────────────────────────────────────────────────────────────

_pad = None
# RLock: 입력 시퀀스 전체를 잠근 채 안에서 _get_pad() 를 다시 불러도 데드락이 없다.
_pad_lock = threading.RLock()

#: release 직후 중립 상태를 잠깐 유지해, 게임 폴링이 '뗌→누름' 상승엣지를
#: 반드시 한 프레임 이상 보게 한다(엣지 병합으로 인한 입력 누락 방지).
_SETTLE_SECONDS = 0.02


def pad_available() -> bool:
    return deps.vg is not None


def _get_pad():
    """가상 패드를 1회만 만들어 재사용한다(스레드 안전)."""

    global _pad
    if deps.vg is None:
        raise RuntimeError("가상 패드를 사용할 수 없습니다.")
    with _pad_lock:
        if _pad is None:
            _pad = deps.vg.VX360Gamepad()
            # 생성 직후 중립 리포트를 한 번 보내, 잔류 비트가 없는 상태에서
            # 첫 입력이 나가게 한다.
            try:
                _pad.reset()
                _pad.update()
            except Exception:
                pass
        return _pad


def ensure_pad() -> bool:
    """패드를 미리 연결해 둔다. 실패해도 안전.

    한 번 만든 패드는 **앱이 살아 있는 동안 유지한다**(mAuto 와 동일). 매 실행
    꽂았다 뽑으면 게임이 장치를 매번 다시 잡아야 하고, '컨트롤러 분리' 반응을
    일으킬 수 있다. 뽑는 건 앱 종료 경로(gui._on_close)의 release_pad() 한 곳이다.
    """

    try:
        _get_pad()
        return True
    except Exception:
        return False


def release_pad() -> None:
    """만들어 둔 가상 패드를 뽑는다(여러 번 불러도 안전).

    앱 종료 경로에서만 부른다 — 실행 중에는 mAuto 처럼 패드를 유지한다.
    프로세스가 죽으면 ViGEm 이 장치를 알아서 제거하므로 이건 정리용 최선 노력이다.

    ⚠️ `__del__` 을 직접 부르지 않는다. vgamepad 의 `__del__` 은 네이티브
    vigem_target_remove/free 를 호출하는데, 여기서 한 번 부르고 나중에 GC 가
    또 부르면 **같은 포인터를 두 번 해제**한다. 참조만 놓아 주면 참조 카운트가
    0이 되는 순간 정확히 한 번 실행된다.
    """

    global _pad
    with _pad_lock:
        pad = _pad
        _pad = None
        if pad is None:
            return
        try:
            # 뽑기 전에 중립 상태를 한 번 보낸다(눌린 채로 사라지지 않게).
            pad.reset()
            pad.update()
        except Exception:
            pass
        del pad  # 참조 해제 = 장치 제거(정확히 한 번)


def _button(name: str):
    if deps.vg is None:
        return None
    table = deps.vg.XUSB_BUTTON
    # 표가 넉넉한 이유: 게임이 패드를 DirectInput 번호로 읽는 계층에서는
    # XUSB↔DInput 번호가 어긋나, 보내는 버튼과 게임이 받는 버튼이 다를 수 있다
    # (mAuto 실측: vgamepad START 가 게임에서 RT 로 받힘). 그럴 때 config 의
    # 버튼 이름 하나만 바꿔 실측할 수 있게 후보를 미리 열어 둔다.
    return {
        "a": table.XUSB_GAMEPAD_A,
        "b": table.XUSB_GAMEPAD_B,
        "x": table.XUSB_GAMEPAD_X,
        "y": table.XUSB_GAMEPAD_Y,
        "start": table.XUSB_GAMEPAD_START,
        "back": table.XUSB_GAMEPAD_BACK,
        "lb": table.XUSB_GAMEPAD_LEFT_SHOULDER,
        "rb": table.XUSB_GAMEPAD_RIGHT_SHOULDER,
        "ls": table.XUSB_GAMEPAD_LEFT_THUMB,
        "rs": table.XUSB_GAMEPAD_RIGHT_THUMB,
        "up": table.XUSB_GAMEPAD_DPAD_UP,
        "down": table.XUSB_GAMEPAD_DPAD_DOWN,
        "left": table.XUSB_GAMEPAD_DPAD_LEFT,
        "right": table.XUSB_GAMEPAD_DPAD_RIGHT,
    }.get(str(name).strip().lower())


def pad_press(name: str, press_delay: float = 0.08) -> bool:
    """패드 버튼을 눌렀다 뗀다.

    누름→업데이트→뗌→업데이트 전체를 락으로 묶는다. 공유 리포트를 두 스레드가
    동시에 건드리면 비트가 섞여 입력이 병합·누락된다(mAuto 에서 실측된 문제).
    """

    button = _button(name)
    if button is None:
        return False
    try:
        with _pad_lock:
            pad = _get_pad()
            pressed = False
            try:
                pad.press_button(button=button)
                pressed = True
                pad.update()
                time.sleep(max(0.0, float(press_delay)))
            finally:
                # 예외가 나도 버튼이 눌린 채로 남지 않게 반드시 해제한다.
                if pressed:
                    pad.release_button(button=button)
                    pad.update()
                time.sleep(_SETTLE_SECONDS)
        return True
    except Exception:
        return False
