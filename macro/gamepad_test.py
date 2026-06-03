"""Xbox 가상패드 버튼 테스트 도구.

FC Online 감독모드를 켠 상태에서 실행하면, 각 버튼을 3초 간격으로
하나씩 눌러줍니다. 게임을 보면서 어떤 버튼에서 '스킵'이 되는지 확인하세요.
콘솔에 현재 누르는 버튼 이름이 표시됩니다.
"""

import time
import sys

try:
    import vgamepad as vg
except Exception as e:
    print(f"vgamepad 로드 실패: {e}")
    print("ViGEm Bus Driver 설치 필요: https://github.com/nefarius/ViGEmBus/releases")
    input("엔터를 누르면 종료합니다...")
    sys.exit(1)

pad = vg.VX360Gamepad()
B = vg.XUSB_BUTTON

# (이름, 종류, 값)
BUTTONS = [
    ("A", "button", B.XUSB_GAMEPAD_A),
    ("B", "button", B.XUSB_GAMEPAD_B),
    ("X", "button", B.XUSB_GAMEPAD_X),
    ("Y", "button", B.XUSB_GAMEPAD_Y),
    ("LB", "button", B.XUSB_GAMEPAD_LEFT_SHOULDER),
    ("RB", "button", B.XUSB_GAMEPAD_RIGHT_SHOULDER),
    ("BACK", "button", B.XUSB_GAMEPAD_BACK),
    ("START", "button", B.XUSB_GAMEPAD_START),
    ("LT", "trigger", "lt"),
    ("RT", "trigger", "rt"),
    ("DPAD_UP", "button", B.XUSB_GAMEPAD_DPAD_UP),
    ("DPAD_DOWN", "button", B.XUSB_GAMEPAD_DPAD_DOWN),
]


def press(kind, value, hold=0.1):
    if kind == "button":
        pad.press_button(button=value)
        pad.update()
        time.sleep(hold)
        pad.release_button(button=value)
        pad.update()
    elif kind == "trigger":
        if value == "lt":
            pad.left_trigger(value=255)
        else:
            pad.right_trigger(value=255)
        pad.update()
        time.sleep(hold)
        pad.left_trigger(value=0)
        pad.right_trigger(value=0)
        pad.update()


print("=" * 50)
print("3초 후 시작합니다. FC Online 감독모드 창을 띄워두세요.")
print("각 버튼 이름이 표시될 때 게임에서 스킵되는지 확인하세요.")
print("=" * 50)
time.sleep(3)

for name, kind, value in BUTTONS:
    print(f"\n>>> 지금 누름: {name}")
    press(kind, value)
    time.sleep(2.5)

print("\n테스트 완료. 스킵된 버튼 이름을 targets.json의 target_E key에 넣으세요.")
print("예: B 버튼이었다면  \"key\": \"b\"")
input("엔터를 누르면 종료합니다...")
