"""대화형 가상패드 테스트 도구 (X360 ↔ DS4, 게임 자동 전면화).

FC ONLINE을 띄워둔 채 실행하고, 콘솔에서 키를 누르면:
  ① 도구가 FC ONLINE 창을 자동으로 '전면(활성)'으로 올리고
  ② 그 상태에서 가상 버튼을 쏜 뒤
  ③ 콘솔로 포커스를 되돌려 줍니다.
→ 버튼은 항상 게임이 활성인 순간에 나가므로 "활성인데 A가 되나"를 정확히 검증합니다.

핵심 시나리오:
  p : START 탭 → 0.6초 → A 탭 (한 번의 활성 세션에서 연속 실행)
      EA류 게임은 패드 '할당' 전엔 START만 받고 A/B/X/Y를 무시하기도 합니다.
  m : X360 ↔ DS4 전환 — 게임이 XInput 대신 DirectInput만 읽으면 DS4(×)가 먹힐 수 있음.
  b : 전면화 끄기/켜기 — 끄면 비활성(백그라운드) 상태에서의 인식을 테스트.
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

import ctypes
from ctypes import wintypes

_user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
_kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None

GAME_TITLE_TOKENS = ("fc online", "fc 온라인")


def _window_title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value or ""


def find_game_window() -> int:
    """제목에 'FC ONLINE'이 들어간 보이는 최상위 창을 찾습니다(없으면 0)."""
    if _user32 is None:
        return 0
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_cb(hwnd, _l):
        if _user32.IsWindowVisible(hwnd):
            t = _window_title(hwnd).lower()
            if t and any(tok in t for tok in GAME_TITLE_TOKENS):
                found.append(int(hwnd))
                return False
        return True

    try:
        _user32.EnumWindows(enum_cb, 0)
    except Exception:  # noqa: BLE001
        pass
    return found[0] if found else 0


def _alt_tap() -> None:
    """ALT 탭 — SetForegroundWindow 포그라운드 잠금 해제 트릭."""
    KEYEVENTF_KEYUP = 0x0002
    _user32.keybd_event(0x12, 0, 0, 0)
    _user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)


def bring_to_front(hwnd: int) -> bool:
    if _user32 is None or not hwnd:
        return False
    if _user32.GetForegroundWindow() == hwnd:
        return True
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    _user32.SetForegroundWindow(hwnd)
    if _user32.GetForegroundWindow() == hwnd:
        return True
    _alt_tap()
    _user32.SetForegroundWindow(hwnd)
    return _user32.GetForegroundWindow() == hwnd


def console_hwnd() -> int:
    if _kernel32 is None:
        return 0
    return int(_kernel32.GetConsoleWindow() or 0)


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
 [모드: {mode}]  [전면화: {fg}]
  1=A/×   2=B/○   3=X/□   4=Y/△
  5=LB/L1 6=RB/R1 7=LT/L2 8=RT/R2
  9=START/OPTIONS   0=BACK/SHARE   u/j/h/k=방향패드
  Shift+키 = 1초 '길게 홀드'
  p = START→(0.6s)→A 콤보 (패드 할당 가설 판별, 한 활성 세션에서)
  m = X360↔DS4 전환    b = 전면화 켜기/끄기(끄면 비활성 테스트)
  ? = 도움말    q = 종료
 사용법: 이 콘솔에서 키만 누르세요. 도구가 게임을 잠깐 활성으로 올려
 버튼을 쏜 뒤 콘솔로 돌아옵니다. 게임 화면의 반응을 관찰하세요.
────────────────────────────────────────────────────────"""

SHIFT_MAP = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
             "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
             "U": "u", "J": "j", "H": "h", "K": "k"}


def main() -> None:
    game = find_game_window()
    if game:
        print(f"게임 창 발견: '{_window_title(game)}' — 키 입력 시 자동으로 활성화 후 버튼을 쏩니다.")
    else:
        print("[경고] FC ONLINE 창을 못 찾았습니다. 게임을 켜고 다시 실행하거나,")
        print("       b 로 전면화를 끈 채(비활성 모드) 테스트할 수 있습니다.")

    pads = {"x360": None, "ds4": None}
    mode = "x360"
    pads[mode] = X360()
    use_fg = True
    print(HELP.format(mode=pads[mode].name, fg="켜짐" if use_fg else "꺼짐(비활성 테스트)"))

    con = console_hwnd()

    def read_key() -> str:
        if msvcrt is not None:
            return msvcrt.getwch()
        return (input("키 입력 후 엔터: ").strip() or " ")[0]

    def fire(fn) -> None:
        """게임을 활성으로 올린 상태에서 fn()을 실행하고 콘솔로 복귀합니다."""
        nonlocal game
        if use_fg:
            if not game or (_user32 and not _user32.IsWindow(game)):
                game = find_game_window()
            if game and not bring_to_front(game):
                print("[경고] 게임 창 전면화 실패 — 그대로 쏩니다(비활성 상태).")
            time.sleep(0.30)   # 게임이 포커스/입력장치를 인지할 시간
        fn()
        if use_fg and con:
            time.sleep(0.10)
            bring_to_front(con)

    while True:
        ch = read_key()
        if ch in ("q", "Q", "\x03"):
            print("종료합니다.")
            return
        if ch in ("?", "/"):
            print(HELP.format(mode=pads[mode].name, fg="켜짐" if use_fg else "꺼짐(비활성 테스트)"))
            continue
        if ch in ("b", "B"):
            use_fg = not use_fg
            print(f">>> 전면화 {'켜짐 — 활성 상태 테스트' if use_fg else '꺼짐 — 비활성(백그라운드) 테스트'}")
            continue
        if ch in ("m", "M"):
            new_mode = "ds4" if mode == "x360" else "x360"
            if pads[new_mode] is None:
                try:
                    pads[new_mode] = DS4() if new_mode == "ds4" else X360()
                except Exception as e:  # noqa: BLE001
                    print(f"[오류] {new_mode} 패드 생성 실패: {e}")
                    continue
            mode = new_mode
            print(f"\n>>> 모드 전환: {pads[mode].name} (새 패드 연결 — 게임이 인지하도록 1~2초 기다렸다 테스트)")
            continue
        if ch in ("p", "P"):
            pad = pads[mode]

            def combo():
                n1 = pad.press("9", 0.15)
                print(f">>> [{pad.name}] {n1} 탭")
                time.sleep(0.6)
                n2 = pad.press("1", 0.15)
                print(f">>> [{pad.name}] {n2} 탭 (START 직후 — 반응을 보세요!)")

            fire(combo)
            continue
        hold = 0.15
        if ch in SHIFT_MAP:
            ch = SHIFT_MAP[ch]
            hold = 1.0
        key = ch
        pad = pads[mode]
        if key not in pad.buttons and key not in ("7", "8") and not (hasattr(pad, "dpad") and key in getattr(pad, "dpad", {})):
            continue

        def single():
            try:
                name = pad.press(key, hold)
            except Exception as e:  # noqa: BLE001
                print(f"[오류] 입력 실패: {e}")
                return
            if name:
                tag = "길게(1s)" if hold >= 1.0 else "탭"
                print(f">>> [{pad.name}] {name} {tag}")

        fire(single)


if __name__ == "__main__":
    main()
