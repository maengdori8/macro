"""대화형 가상패드 테스트 도구 (X360 ↔ DS4 전환 지원).

FC ONLINE을 띄워둔 채 실행하고, 콘솔에서 키를 눌러 가상 버튼을 하나씩 쏴 봅니다.
게임 화면을 보면서 어떤 모드/버튼에서 반응(커서 이동·확인·일시정지)이 있는지 확인하세요.

핵심 시나리오 2가지:
  ① 패드 할당 가설 — 9(START)를 먼저 눌러 게임이 패드를 인지하게 한 '직후' 1(A)을
     눌러보세요. EA류 게임은 패드 할당 전엔 START만 받고 A/B/X/Y를 무시하기도 합니다.
  ② DirectInput 가설 — m 으로 DS4 모드로 바꾼 뒤 같은 테스트를 반복하세요.
     게임이 XInput이 아니라 DirectInput만 읽으면 DS4(×버튼)는 먹힐 수 있습니다.
"""

import sys
import time

try:
    import vgamepad as vg
except Exception as e:  # noqa: BLE001
    print(f"vgamepad 로드 실패: {e}")
    print("ViGEm Bus Driver 설치 필요: https://github.com/nefarius/ViGEmBus/releases")
    input("엔터를 누르면 종료합니다...")
    sys.exit(1)

try:
    import msvcrt  # Windows 콘솔 즉시 키 입력
except Exception:  # noqa: BLE001
    msvcrt = None


class X360:
    name = "X360(Xbox)"

    def __init__(self):
        self.pad = vg.VX360Gamepad()
        B = vg.XUSB_BUTTON
        self.buttons = {
            "1": ("A", B.XUSB_GAMEPAD_A),
            "2": ("B", B.XUSB_GAMEPAD_B),
            "3": ("X", B.XUSB_GAMEPAD_X),
            "4": ("Y", B.XUSB_GAMEPAD_Y),
            "5": ("LB", B.XUSB_GAMEPAD_LEFT_SHOULDER),
            "6": ("RB", B.XUSB_GAMEPAD_RIGHT_SHOULDER),
            "9": ("START", B.XUSB_GAMEPAD_START),
            "0": ("BACK", B.XUSB_GAMEPAD_BACK),
            "u": ("DPAD_UP", B.XUSB_GAMEPAD_DPAD_UP),
            "j": ("DPAD_DOWN", B.XUSB_GAMEPAD_DPAD_DOWN),
            "h": ("DPAD_LEFT", B.XUSB_GAMEPAD_DPAD_LEFT),
            "k": ("DPAD_RIGHT", B.XUSB_GAMEPAD_DPAD_RIGHT),
        }

    def press(self, key: str, hold: float) -> str:
        if key == "7":
            self.pad.left_trigger(value=255); self.pad.update()
            time.sleep(hold)
            self.pad.left_trigger(value=0); self.pad.update()
            return "LT"
        if key == "8":
            self.pad.right_trigger(value=255); self.pad.update()
            time.sleep(hold)
            self.pad.right_trigger(value=0); self.pad.update()
            return "RT"
        item = self.buttons.get(key)
        if item is None:
            return ""
        name, btn = item
        self.pad.press_button(button=btn); self.pad.update()
        time.sleep(hold)
        self.pad.release_button(button=btn); self.pad.update()
        return name


class DS4:
    name = "DS4(듀얼쇼크4/DirectInput)"

    def __init__(self):
        self.pad = vg.VDS4Gamepad()
        B = vg.DS4_BUTTONS
        self.buttons = {
            "1": ("크로스(×)=A", B.DS4_BUTTON_CROSS),
            "2": ("서클(○)=B", B.DS4_BUTTON_CIRCLE),
            "3": ("사각(□)=X", B.DS4_BUTTON_SQUARE),
            "4": ("삼각(△)=Y", B.DS4_BUTTON_TRIANGLE),
            "5": ("L1", B.DS4_BUTTON_SHOULDER_LEFT),
            "6": ("R1", B.DS4_BUTTON_SHOULDER_RIGHT),
            "9": ("OPTIONS=START", B.DS4_BUTTON_OPTIONS),
            "0": ("SHARE=BACK", B.DS4_BUTTON_SHARE),
        }
        D = vg.DS4_DPAD_DIRECTIONS
        self.dpad = {
            "u": ("DPAD_UP", D.DS4_BUTTON_DPAD_NORTH),
            "j": ("DPAD_DOWN", D.DS4_BUTTON_DPAD_SOUTH),
            "h": ("DPAD_LEFT", D.DS4_BUTTON_DPAD_WEST),
            "k": ("DPAD_RIGHT", D.DS4_BUTTON_DPAD_EAST),
        }
        self.dpad_none = D.DS4_BUTTON_DPAD_NONE

    def press(self, key: str, hold: float) -> str:
        if key == "7":
            self.pad.left_trigger(value=255); self.pad.update()
            time.sleep(hold)
            self.pad.left_trigger(value=0); self.pad.update()
            return "L2"
        if key == "8":
            self.pad.right_trigger(value=255); self.pad.update()
            time.sleep(hold)
            self.pad.right_trigger(value=0); self.pad.update()
            return "R2"
        if key in self.dpad:
            name, d = self.dpad[key]
            self.pad.directional_pad(direction=d); self.pad.update()
            time.sleep(hold)
            self.pad.directional_pad(direction=self.dpad_none); self.pad.update()
            return name
        item = self.buttons.get(key)
        if item is None:
            return ""
        name, btn = item
        self.pad.press_button(button=btn); self.pad.update()
        time.sleep(hold)
        self.pad.release_button(button=btn); self.pad.update()
        return name


HELP = """
────────────────────────────────────────────────────────
 [모드: {mode}]   (m: X360↔DS4 전환, ?: 도움말, q: 종료)
  1=A/×   2=B/○   3=X/□   4=Y/△
  5=LB/L1 6=RB/R1 7=LT/L2 8=RT/R2
  9=START/OPTIONS   0=BACK/SHARE
  u=↑  j=↓  h=←  k=→   (방향패드)
  같은 키를 Shift와 함께(!@#$…) 누르면 1초 '길게 홀드'
  ex) ① 9 누르고 곧바로 1 → START로 패드 인지시킨 직후 A 테스트
      ② m 으로 DS4 전환 후 1 → DirectInput 게임에서 × 버튼 테스트
────────────────────────────────────────────────────────"""

# Shift+숫자 → 길게 홀드 매핑 (미국 배열 기준)
SHIFT_MAP = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
             "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
             "U": "u", "J": "j", "H": "h", "K": "k"}


def main() -> None:
    print("가상패드 대화형 테스트를 시작합니다. FC ONLINE 창을 화면에 보이게 두세요.")
    pads = {"x360": None, "ds4": None}
    mode = "x360"
    pads[mode] = X360()
    print(HELP.format(mode=pads[mode].name))

    def read_key() -> str:
        if msvcrt is not None:
            ch = msvcrt.getwch()
            return ch
        return (input("키 입력 후 엔터: ").strip() or " ")[0]

    while True:
        ch = read_key()
        if ch in ("q", "Q", "\x03"):
            print("종료합니다.")
            return
        if ch in ("?", "/"):
            print(HELP.format(mode=pads[mode].name))
            continue
        if ch in ("m", "M"):
            mode = "ds4" if mode == "x360" else "x360"
            if pads[mode] is None:
                try:
                    pads[mode] = DS4() if mode == "ds4" else X360()
                except Exception as e:  # noqa: BLE001
                    print(f"[오류] {mode} 패드 생성 실패: {e}")
                    mode = "ds4" if mode == "x360" else "x360"
                    continue
            print(f"\n>>> 모드 전환: {pads[mode].name} (패드가 새로 연결됐습니다 — 게임이 인지할 시간을 1~2초 주세요)")
            continue
        hold = 0.15
        if ch in SHIFT_MAP:
            ch = SHIFT_MAP[ch]
            hold = 1.0
        try:
            name = pads[mode].press(ch, hold)
        except Exception as e:  # noqa: BLE001
            print(f"[오류] 입력 실패: {e}")
            continue
        if name:
            tag = "길게(1s)" if hold >= 1.0 else "탭"
            print(f">>> [{pads[mode].name}] {name} {tag}")


if __name__ == "__main__":
    main()
