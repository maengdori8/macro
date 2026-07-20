"""대화형 입력 테스트 도구 — 가상패드(X360/DS4) + '비활성 키보드 s' 여러 기법.

FC ONLINE(FIFAKC)을 띄워둔 채 실행하고 콘솔에서 키를 누르면, 해당 입력을 게임에 쏜다.
가상패드는 원래 전역(비활성·무깜빡임)이고, 키보드 s는 '게임을 앞으로 꺼내지 않고'
(=100% 비활성) 전달하는 방법을 여러 개 각각의 키에 붙여 두어, 어느 게 실제로
(A) SKIP을 넘기는지 게임 화면을 보며 하나씩 확인할 수 있다.

키보드 s 기법(전부 비활성 유지, 화면 변화 0 목표):
  z = si_s        SendInput 전역(폴링 게임용; 활성 앱에 s 샐 수 있음)
  x = pm_s        WM_KEYDOWN/UP을 창+모든 자식에 Post
  c = char_s      WM_KEYDOWN+WM_CHAR+WM_KEYUP Post
  v = state_s     AttachThreadInput+SetKeyboardState(GetKeyState 폴링 위조)
  n = post_s      AttachThreadInput(큐 공유)+포커스창에 Post(SetFocus 없음)
  , = child_s     AttachThreadInput+자식창 SetFocus+SendInput
  . = focus_s     최상위 SetFocus+SendInput  ★유일 실측 성공이나 '깜빡임' 있음(비교용)
  d = 창 클래스 진단 덤프(자식 창 목록)
가상패드: 1234=A/B/X/Y  5678=LB/RB/LT/RT  90=START/BACK  u/j/h/k=방향
  m=X360↔DS4 전환  p=START→A 콤보  q=종료  ?=도움말
"""

import sys
import time
import ctypes
from ctypes import wintypes

try:
    import vgamepad as vg
except Exception as e:  # noqa: BLE001
    print(f"vgamepad 로드 실패: {e}")
    print("ViGEm Bus Driver 설치 필요: https://github.com/nefarius/ViGEmBus/releases")
    input("엔터를 누르면 종료합니다...")
    sys.exit(1)

try:
    import msvcrt
except Exception:  # noqa: BLE001
    msvcrt = None

# ─────────────────────────────────────────────────────────────────────────────
# 비활성 키보드 s 기법 (순수 ctypes, macroapp 의존성 없음 — 빌드 가볍게)
# ─────────────────────────────────────────────────────────────────────────────
_u = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
_k = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None

VK_S = 0x53
SCAN_S = 0x1F
WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x100, 0x101, 0x102
KEYEVENTF_SCANCODE, KEYEVENTF_KEYUP = 0x0008, 0x0002
GAME_TITLE_TOKENS = ("fc online", "fc 온라인", "fifa online")


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT)]


def _setup():
    if _u is None:
        return
    _u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u.PostMessageW.restype = wintypes.BOOL
    _u.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
    _u.SendInput.restype = wintypes.UINT
    _u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
    _u.GetWindowThreadProcessId.restype = wintypes.DWORD
    _u.GetFocus.restype = wintypes.HWND
    _u.SetFocus.restype = wintypes.HWND
    _u.SetFocus.argtypes = [wintypes.HWND]
    _u.GetForegroundWindow.restype = wintypes.HWND
    _u.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]
    _u.MapVirtualKeyW.restype = ctypes.c_uint


def _lp_down():
    return (SCAN_S << 16) | 1


def _lp_up():
    return (SCAN_S << 16) | 1 | (1 << 30) | (1 << 31)


def _tid(hwnd):
    return _u.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), None)


def _class(hwnd):
    b = ctypes.create_unicode_buffer(256)
    _u.GetClassNameW(wintypes.HWND(int(hwnd)), b, 256)
    return b.value or ""


def _children(hwnd, limit=80):
    out = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _l):
        if len(out) >= limit:
            return False
        if _u.IsWindow(h):
            out.append(int(h))
        return True

    try:
        _u.EnumChildWindows(wintypes.HWND(int(hwnd)), cb, 0)
    except Exception:  # noqa: BLE001
        pass
    return out


def _send_scancode(keyup):
    inp = _INPUT()
    inp.type = 1
    inp.ki.wVk = 0
    inp.ki.wScan = SCAN_S
    inp.ki.dwFlags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if keyup else 0)
    return _u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) == 1


def si_s(hwnd, hold=0.12):
    ok = _send_scancode(False)
    time.sleep(hold)
    _send_scancode(True)
    return ok


def pm_s(hwnd, hold=0.12):
    targets = [int(hwnd)] + _children(hwnd)
    for t in targets:
        _u.PostMessageW(wintypes.HWND(t), WM_KEYDOWN, VK_S, _lp_down())
    time.sleep(hold)
    for t in targets:
        _u.PostMessageW(wintypes.HWND(t), WM_KEYUP, VK_S, _lp_up())
    return True


def char_s(hwnd, hold=0.12):
    targets = [int(hwnd)] + _children(hwnd)
    for t in targets:
        _u.PostMessageW(wintypes.HWND(t), WM_KEYDOWN, VK_S, _lp_down())
        _u.PostMessageW(wintypes.HWND(t), WM_CHAR, ord("s"), _lp_down())
    time.sleep(hold)
    for t in targets:
        _u.PostMessageW(wintypes.HWND(t), WM_KEYUP, VK_S, _lp_up())
    return True


def state_s(hwnd, hold=0.12):
    tid, cur = _tid(hwnd), _k.GetCurrentThreadId()
    if not tid or tid == cur:
        return False
    if not _u.AttachThreadInput(cur, tid, True):
        return False
    try:
        st = (ctypes.c_ubyte * 256)()
        _u.GetKeyboardState(ctypes.byref(st))
        st[VK_S] = 0x80
        _u.SetKeyboardState(ctypes.byref(st))
        time.sleep(hold)
        _u.GetKeyboardState(ctypes.byref(st))
        st[VK_S] = 0x00
        _u.SetKeyboardState(ctypes.byref(st))
        return True
    finally:
        _u.AttachThreadInput(cur, tid, False)


def post_s(hwnd, hold=0.12):
    tid, cur = _tid(hwnd), _k.GetCurrentThreadId()
    if not tid or tid == cur:
        return False
    att = bool(_u.AttachThreadInput(cur, tid, True))
    try:
        gti = _GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(_GUITHREADINFO)
        tgt = int(hwnd)
        if _u.GetGUIThreadInfo(tid, ctypes.byref(gti)) and gti.hwndFocus:
            tgt = int(gti.hwndFocus)
        _u.PostMessageW(wintypes.HWND(tgt), WM_KEYDOWN, VK_S, _lp_down())
        time.sleep(hold)
        _u.PostMessageW(wintypes.HWND(tgt), WM_KEYUP, VK_S, _lp_up())
        return True
    finally:
        if att:
            _u.AttachThreadInput(cur, tid, False)


def _pick_child(hwnd):
    tid = _tid(hwnd)
    gti = _GUITHREADINFO()
    gti.cbSize = ctypes.sizeof(_GUITHREADINFO)
    if tid and _u.GetGUIThreadInfo(tid, ctypes.byref(gti)) and gti.hwndFocus:
        f = int(gti.hwndFocus)
        if f and f != int(hwnd):
            return f
    ch = _children(hwnd)
    for c in ch:
        if "renderwidgethost" in _class(c).lower():
            return c
    return ch[0] if ch else 0


def _focus_sendinput(hwnd, target_hwnd, hold):
    tid, cur = _tid(hwnd), _k.GetCurrentThreadId()
    fg = _u.GetForegroundWindow()
    tidfg = _tid(fg) if fg else 0
    att = []
    for t in {tid, tidfg}:
        if t and t != cur and _u.AttachThreadInput(cur, t, True):
            att.append(t)
    try:
        prev = _u.GetFocus()
        _u.SetFocus(wintypes.HWND(int(target_hwnd)))
        time.sleep(0.02)
        ok = si_s(hwnd, hold)
        try:
            if prev:
                _u.SetFocus(prev)
        except Exception:  # noqa: BLE001
            pass
        return ok
    finally:
        for t in att:
            try:
                _u.AttachThreadInput(cur, t, False)
            except Exception:  # noqa: BLE001
                pass


def child_s(hwnd, hold=0.12):
    child = _pick_child(hwnd)
    if not child:
        return False
    return _focus_sendinput(hwnd, child, hold)


def focus_s(hwnd, hold=0.12):
    return _focus_sendinput(hwnd, hwnd, hold)


def dump_classes(hwnd):
    top = _class(hwnd)
    ch = _children(hwnd)
    counts = {}
    for c in ch:
        cn = _class(c) or "?"
        counts[cn] = counts.get(cn, 0) + 1
    parts = [f"{c}×{n}" if n > 1 else c for c, n in sorted(counts.items())]
    return f"top='{top}' children[{len(ch)}]: " + (", ".join(parts) if parts else "(없음)")


# 키 → (이름, 함수) : 전부 비활성 유지(게임을 앞으로 안 꺼냄)
KBD_TESTS = {
    "z": ("si_s(SendInput 전역)", si_s),
    "x": ("pm_s(자식 Post)", pm_s),
    "c": ("char_s(WM_CHAR Post)", char_s),
    "v": ("state_s(SetKeyboardState)", state_s),
    "n": ("post_s(Attach+Post)", post_s),
    ",": ("child_s(자식 SetFocus)", child_s),
    ".": ("focus_s(최상위 SetFocus·깜빡임)", focus_s),
}

# 대문자·Shift = 같은 기법을 '1초 홀드'로 (hold-to-skip 스킵 대응). <,> = Shift+,.
KBD_HOLD_MAP = {"Z": "z", "X": "x", "C": "c", "V": "v", "N": "n", "<": ",", ">": "."}


def _window_title(hwnd):
    b = ctypes.create_unicode_buffer(256)
    _u.GetWindowTextW(hwnd, b, 256)
    return b.value or ""


def find_game_window():
    if _u is None:
        return 0
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _l):
        if _u.IsWindowVisible(hwnd):
            t = _window_title(hwnd).lower()
            c = _class(hwnd).lower()
            if (t and any(tok in t for tok in GAME_TITLE_TOKENS)) or c == "fifakc":
                found.append(int(hwnd))
                return False
        return True

    try:
        _u.EnumWindows(cb, 0)
    except Exception:  # noqa: BLE001
        pass
    return found[0] if found else 0


# ─────────────────────────────────────────────────────────────────────────────
# 가상패드
# ─────────────────────────────────────────────────────────────────────────────
class X360:
    name = "X360(Xbox)"

    def __init__(self):
        self.pad = vg.VX360Gamepad()
        B = vg.XUSB_BUTTON
        self.buttons = {
            "1": ("A", B.XUSB_GAMEPAD_A), "2": ("B", B.XUSB_GAMEPAD_B),
            "3": ("X", B.XUSB_GAMEPAD_X), "4": ("Y", B.XUSB_GAMEPAD_Y),
            "5": ("LB", B.XUSB_GAMEPAD_LEFT_SHOULDER), "6": ("RB", B.XUSB_GAMEPAD_RIGHT_SHOULDER),
            "9": ("START", B.XUSB_GAMEPAD_START), "0": ("BACK", B.XUSB_GAMEPAD_BACK),
            "[": ("L3(왼스틱클릭=게임BACK)", B.XUSB_GAMEPAD_LEFT_THUMB),
            "]": ("R3(오른스틱클릭=게임START)", B.XUSB_GAMEPAD_RIGHT_THUMB),
            "u": ("DPAD_UP", B.XUSB_GAMEPAD_DPAD_UP), "j": ("DPAD_DOWN", B.XUSB_GAMEPAD_DPAD_DOWN),
            "h": ("DPAD_LEFT", B.XUSB_GAMEPAD_DPAD_LEFT), "k": ("DPAD_RIGHT", B.XUSB_GAMEPAD_DPAD_RIGHT),
        }

    def press(self, key, hold):
        if key in ("7", "8"):
            (self.pad.left_trigger if key == "7" else self.pad.right_trigger)(value=255)
            self.pad.update(); time.sleep(hold)
            self.pad.left_trigger(value=0); self.pad.right_trigger(value=0); self.pad.update()
            return "LT" if key == "7" else "RT"
        it = self.buttons.get(key)
        if it is None:
            return ""
        name, btn = it
        self.pad.press_button(button=btn); self.pad.update(); time.sleep(hold)
        self.pad.release_button(button=btn); self.pad.update()
        return name


class DS4:
    name = "DS4(듀얼쇼크4/DirectInput)"

    def __init__(self):
        self.pad = vg.VDS4Gamepad()
        B = vg.DS4_BUTTONS
        self.buttons = {
            "1": ("크로스(×)", B.DS4_BUTTON_CROSS), "2": ("서클(○)", B.DS4_BUTTON_CIRCLE),
            "3": ("사각(□)", B.DS4_BUTTON_SQUARE), "4": ("삼각(△)", B.DS4_BUTTON_TRIANGLE),
            "5": ("L1", B.DS4_BUTTON_SHOULDER_LEFT), "6": ("R1", B.DS4_BUTTON_SHOULDER_RIGHT),
            "9": ("OPTIONS", B.DS4_BUTTON_OPTIONS), "0": ("SHARE", B.DS4_BUTTON_SHARE),
            "[": ("L3", B.DS4_BUTTON_THUMB_LEFT), "]": ("R3", B.DS4_BUTTON_THUMB_RIGHT),
        }
        D = vg.DS4_DPAD_DIRECTIONS
        self.dpad = {"u": ("↑", D.DS4_BUTTON_DPAD_NORTH), "j": ("↓", D.DS4_BUTTON_DPAD_SOUTH),
                     "h": ("←", D.DS4_BUTTON_DPAD_WEST), "k": ("→", D.DS4_BUTTON_DPAD_EAST)}
        self.dpad_none = D.DS4_BUTTON_DPAD_NONE

    def press(self, key, hold):
        if key in ("7", "8"):
            (self.pad.left_trigger if key == "7" else self.pad.right_trigger)(value=255)
            self.pad.update(); time.sleep(hold)
            self.pad.left_trigger(value=0); self.pad.right_trigger(value=0); self.pad.update()
            return "L2" if key == "7" else "R2"
        if key in self.dpad:
            name, d = self.dpad[key]
            self.pad.directional_pad(direction=d); self.pad.update(); time.sleep(hold)
            self.pad.directional_pad(direction=self.dpad_none); self.pad.update()
            return name
        it = self.buttons.get(key)
        if it is None:
            return ""
        name, btn = it
        self.pad.press_button(button=btn); self.pad.update(); time.sleep(hold)
        self.pad.release_button(button=btn); self.pad.update()
        return name


HELP = """
────────────────────────────────────────────────────────────────
 [패드: {mode}]   게임창: {game}
 ── 비활성 키보드 s (게임 앞으로 안 꺼냄, 화면 변화 0 목표) ──
   z si_s(전역)   x pm_s(자식Post)   c char_s(WM_CHAR)
   v state_s(키상태)  n post_s(Attach+Post)  , child_s(자식포커스)
   . focus_s(★됨·깜빡임 비교용)     d 창 클래스 진단 덤프
   ※ 대문자(Z X C V N)·Shift = 같은 기법을 '1초 홀드'(hold-to-skip 대응)
 ── 가상패드(전역·무깜빡임) ──
   1 A  2 B  3 X  4 Y   5 LB 6 RB 7 LT 8 RT   9 START 0 BACK   [ L3(=게임BACK)  ] R3(=게임START·스킵후보)
   u ↑  j ↓  h ←  k →      Shift+키 = 1초 홀드 ({{ }}=[ ]홀드)
   m X360↔DS4   p START→A콤보   ? 도움말   q 종료
 사용법: 이 콘솔에서 키만 누르세요. (A) SKIP 화면에서 눌러보고
 게임이 넘어가는 기법을 찾으세요. 넘어가면 그게 정답입니다.
────────────────────────────────────────────────────────────────"""

SHIFT_MAP = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6",
             "&": "7", "*": "8", "(": "9", ")": "0",
             "{": "[", "}": "]",
             "U": "u", "J": "j", "H": "h", "K": "k"}


def main():
    _setup()
    game = find_game_window()
    gtxt = f"'{_window_title(game)}' (hwnd={game})" if game else "(못 찾음 — 게임 먼저 켜세요)"
    print("입력 테스트 시작. FC ONLINE(FIFAKC)을 켜 두세요.")
    print(f"게임창: {gtxt}")
    if game:
        print("진단:", dump_classes(game))
    pads = {"x360": X360(), "ds4": None}
    mode = "x360"
    print(HELP.format(mode=pads[mode].name, game=gtxt))

    def rk():
        return msvcrt.getwch() if msvcrt is not None else (input("키: ").strip() or " ")[0]

    while True:
        ch = rk()
        if ch in ("q", "Q", "\x03"):
            print("종료합니다.")
            return
        if ch in ("?", "/"):
            print(HELP.format(mode=pads[mode].name, game=gtxt))
            continue
        if ch in ("d", "D"):
            game = find_game_window()
            print(">>> 진단:", dump_classes(game) if game else "게임창 못 찾음")
            continue
        if ch in ("m", "M"):
            nm = "ds4" if mode == "x360" else "x360"
            if pads[nm] is None:
                try:
                    pads[nm] = DS4() if nm == "ds4" else X360()
                except Exception as e:  # noqa: BLE001
                    print(f"[오류] {nm} 생성 실패: {e}")
                    continue
            mode = nm
            print(f"\n>>> 패드 전환: {pads[mode].name} (게임이 인지하도록 1~2초 대기 후 테스트)")
            continue
        # ── 비활성 키보드 s 기법 (소문자=탭 0.12s / 대문자·Shift=홀드 1초) ──
        kbd_key, kbd_hold = ch, 0.12
        if ch in KBD_HOLD_MAP:
            kbd_key, kbd_hold = KBD_HOLD_MAP[ch], 1.0
        if kbd_key in KBD_TESTS:
            game = find_game_window()
            if not game:
                print(">>> [키보드] 게임창을 못 찾음 — 게임을 켜세요.")
                continue
            label, fn = KBD_TESTS[kbd_key]
            try:
                ok = fn(game, kbd_hold)
            except Exception as e:  # noqa: BLE001
                print(f">>> [키보드] {label} 오류: {e}")
                continue
            note = "  ← 이건 깜빡임 있음(비교 기준)" if kbd_key == "." else ""
            tag = "홀드1s" if kbd_hold >= 1.0 else "탭"
            print(f">>> [키보드 s] {label} {tag} → {'전송' if ok else '실패/불가'}{note}")
            continue
        if ch in ("p", "P"):
            pad = pads[mode]
            n1 = pad.press("9", 0.15); print(f">>> [{pad.name}] {n1} 탭")
            time.sleep(0.6)
            n2 = pad.press("1", 0.15); print(f">>> [{pad.name}] {n2} 탭 (START 직후)")
            continue
        # ── 가상패드 버튼 ──
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
            print(f">>> [{pads[mode].name}] {name} {'길게(1s)' if hold >= 1.0 else '탭'}")


if __name__ == "__main__":
    main()
