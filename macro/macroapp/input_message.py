from __future__ import annotations
import ctypes
from ctypes import wintypes
from typing import Any, Optional

from macroapp import winapi
from macroapp.config import (
    CLICK_MESSAGE_DELAY_SECONDS,
    MOUSE_HOVER_BEFORE_CLICK_SECONDS,
    _parse_optional_int,
)
import time


# GUITHREADINFO 구조체는 함수 호출마다 재정의하지 않고 모듈 1회 정의합니다.
class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


_USER32_SIG_READY = False


def _setup_user32_sigs() -> None:
    """user32 함수 시그니처를 1회만 설정합니다(호출마다 반복 방지)."""
    global _USER32_SIG_READY
    if _USER32_SIG_READY or not hasattr(ctypes, "windll"):
        return
    u = ctypes.windll.user32
    u.MapVirtualKeyW.restype = ctypes.c_uint
    u.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, wintypes.LPDWORD]
    # HWND 반환 함수는 restype를 HWND로 지정해야 x64에서 64비트 포인터가 안 잘린다
    # (기본 c_int면 절단→포커스 복원이 엉뚱한 창으로 가는 잠재 버그).
    u.GetFocus.restype = wintypes.HWND
    u.SetFocus.restype = wintypes.HWND
    u.SetFocus.argtypes = [wintypes.HWND]
    u.GetForegroundWindow.restype = wintypes.HWND
    _USER32_SIG_READY = True

KEY_TO_VK: dict[str, int] = {
    "esc": 0x1B, "escape": 0x1B,
    "enter": 0x0D, "return": 0x0D,
    "space": 0x20,
    "tab": 0x09,
    "s": 0x53, "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44,
    "e": 0x45, "f": 0x46, "g": 0x47, "h": 0x48, "i": 0x49,
    "j": 0x4A, "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E,
    "o": 0x4F, "p": 0x50, "q": 0x51, "r": 0x52, "t": 0x54,
    "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59, "z": 0x5A,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
}

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_CHAR = 0x0102

# ─── SendInput 스캔코드 키 입력 (전면 전환 최후수단) ───
# RawInput/DirectInput 게임은 PostMessage 키를 무시하므로, '진짜 키보드'처럼 보이는
# SendInput 스캔코드가 필요하다. 단 SendInput은 전면(포커스) 창으로만 가므로,
# press_key_foreground()가 잠깐 전면 전환→키 입력→원래 창 복원까지 묶어 처리한다.
_KEYEVENTF_SCANCODE = 0x0008
_KEYEVENTF_KEYUP = 0x0002
_INPUT_KEYBOARD = 1
SCAN_S = 0x1F     # 키보드 's' (set-1 스캔코드) — FC ONLINE에서 A 버튼과 동일 동작
_SCAN_ALT = 0x38  # ALT — SetForegroundWindow 잠금 해제 트릭용


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        # MOUSEINPUT(최대 멤버)와 크기를 맞추기 위한 패딩 포함 → sizeof(INPUT)=40(x64) 보장.
        _fields_ = [("ki", _KEYBDINPUT), ("_pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send_scancode(scan: int, keyup: bool) -> bool:
    """SendInput으로 스캔코드 down 또는 up 이벤트 1개를 보낸다(전면 창으로 감)."""
    if not hasattr(ctypes, "windll"):
        return False
    inp = _INPUT()
    inp.type = _INPUT_KEYBOARD
    inp.ki.wVk = 0
    inp.ki.wScan = scan
    inp.ki.dwFlags = _KEYEVENTF_SCANCODE | (_KEYEVENTF_KEYUP if keyup else 0)
    sent = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
    return sent == 1


def send_scancode_key(scan: int, hold: float = 0.1) -> bool:
    """SendInput 스캔코드 키를 눌렀다 뗀다. 전면 창에만 전달되므로 보통
    press_key_foreground()를 통해 사용한다."""
    if not _send_scancode(scan, keyup=False):
        return False
    time.sleep(max(0.0, hold))
    _send_scancode(scan, keyup=True)
    return True


def post_key_deep(hwnd: int, vk_code: int, press_delay: float = 0.08) -> bool:
    """WM_KEYDOWN/UP을 최상위 + 포커스 자식 + 모든 자식 창에 전송합니다(비활성 유지).

    단순 PostMessage(최상위만)가 무시되던 게임도, 실제 키 처리가 자식 창(CEF 등)에
    있으면 이 딥 버전은 통할 수 있다. 스캔코드 lParam을 실제 키처럼 구성한다.
    """
    if winapi.win32gui is None or not hasattr(ctypes, "windll"):
        return False
    try:
        _setup_user32_sigs()
        scan_code = ctypes.windll.user32.MapVirtualKeyW(vk_code, 0)
        lparam_down = (scan_code << 16) | 1
        lparam_up = (scan_code << 16) | 1 | (1 << 30) | (1 << 31)
        targets = [int(hwnd)]
        focus = _get_thread_focus_hwnd(hwnd)
        if focus:
            targets.append(int(focus))
        targets.extend(_get_child_windows(hwnd))
        targets = _unique_hwnds(targets)
        if not targets:
            return False
        for t in targets:
            try:
                winapi.win32gui.PostMessage(t, WM_KEYDOWN, vk_code, lparam_down)
            except Exception:
                pass
        time.sleep(max(0.0, press_delay))
        for t in targets:
            try:
                winapi.win32gui.PostMessage(t, WM_KEYUP, vk_code, lparam_up)
            except Exception:
                pass
        return True
    except Exception:
        return False


def post_char_deep(hwnd: int, vk_code: int, char_code: int,
                   press_delay: float = 0.08) -> bool:
    """WM_KEYDOWN → WM_CHAR → WM_KEYUP을 최상위+포커스 자식+모든 자식 창에 전송.

    포커스를 전혀 안 건드려 화면 변화가 0이다(활성 창·z순서 불변 → 깜빡임 없음).
    FC ONLINE의 CEF(크롬 기반) UI 자식 창은 문자 입력을 WM_CHAR로 받는 경우가 있어,
    WM_KEYDOWN만 보내던 pm_s가 무시됐어도 이 경로는 통할 수 있다.
    """
    if winapi.win32gui is None or not hasattr(ctypes, "windll"):
        return False
    try:
        _setup_user32_sigs()
        scan_code = ctypes.windll.user32.MapVirtualKeyW(vk_code, 0)
        lparam_down = (scan_code << 16) | 1
        lparam_up = (scan_code << 16) | 1 | (1 << 30) | (1 << 31)
        targets = [int(hwnd)]
        focus = _get_thread_focus_hwnd(hwnd)
        if focus:
            targets.append(int(focus))
        targets.extend(_get_child_windows(hwnd))
        targets = _unique_hwnds(targets)
        if not targets:
            return False
        for t in targets:
            try:
                winapi.win32gui.PostMessage(t, WM_KEYDOWN, vk_code, lparam_down)
                winapi.win32gui.PostMessage(t, WM_CHAR, char_code, lparam_down)
            except Exception:
                pass
        time.sleep(max(0.0, press_delay))
        for t in targets:
            try:
                winapi.win32gui.PostMessage(t, WM_KEYUP, vk_code, lparam_up)
            except Exception:
                pass
        return True
    except Exception:
        return False


def send_key_attach_state(hwnd: int, vk_code: int, hold: float = 0.12) -> bool:
    """AttachThreadInput으로 게임 큐에 붙어 SetKeyboardState로 '키 눌림'을 위조합니다.

    깜빡임 0(포커스·활성 안 건드림), 유출 0(전역 SendInput 아님). 게임이 GetKeyState
    (동기, 큐-로컬)로 s를 폴링하면 통한다. GetAsyncKeyState/RawInput/DirectInput이면 무시.
    SetKeyboardState는 '붙은 큐'의 상태를 바꾸므로 AttachThreadInput이 필수 전제.
    """
    if winapi.win32gui is None or not hasattr(ctypes, "windll"):
        return False
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    tid_cur = k.GetCurrentThreadId()
    tid_target = 0
    attached = False
    try:
        if not winapi.win32gui.IsWindow(int(hwnd)):
            return False
        tid_target = u.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), None)
        if not tid_target or tid_target == tid_cur:
            return False
        attached = bool(u.AttachThreadInput(tid_cur, tid_target, True))
        if not attached:
            return False   # 미부착이면 SetKeyboardState가 우리 큐만 바꿔 무의미
        state = (ctypes.c_ubyte * 256)()
        u.GetKeyboardState(ctypes.byref(state))
        state[vk_code & 0xFF] = 0x80    # high bit = 눌림
        u.SetKeyboardState(ctypes.byref(state))
        time.sleep(max(0.0, hold))
        u.GetKeyboardState(ctypes.byref(state))
        state[vk_code & 0xFF] = 0x00
        u.SetKeyboardState(ctypes.byref(state))
        return True
    except Exception:
        return False
    finally:
        if attached and tid_target:
            try:
                u.AttachThreadInput(tid_cur, tid_target, False)
            except Exception:
                pass


def send_key_attach_post(hwnd: int, vk_code: int, hold: float = 0.12) -> bool:
    """AttachThreadInput으로 큐만 공유(SetFocus 없음)한 뒤, 게임이 이미 자기 포커스로
    여기는 창(GUITHREADINFO.hwndFocus)에 WM_KEYDOWN/UP을 PostMessage.

    SetFocus를 안 하므로 activate/깜빡임이 없다. 큐가 공유돼 메시지 문맥이 게임 관점과
    일치 → post_key_deep(비부착)이 놓친 메시지 기반 처리를 잡을 수 있다. RawInput은 무시.
    """
    if winapi.win32gui is None or not hasattr(ctypes, "windll"):
        return False
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    tid_cur = k.GetCurrentThreadId()
    tid_target = 0
    attached = False
    try:
        _setup_user32_sigs()
        if not winapi.win32gui.IsWindow(int(hwnd)):
            return False
        tid_target = u.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), None)
        if not tid_target or tid_target == tid_cur:
            return False
        attached = bool(u.AttachThreadInput(tid_cur, tid_target, True))
        info = _GUITHREADINFO()
        info.cbSize = ctypes.sizeof(_GUITHREADINFO)
        target = int(hwnd)
        if u.GetGUIThreadInfo(tid_target, ctypes.byref(info)) and info.hwndFocus:
            target = int(info.hwndFocus)
        scan_code = ctypes.windll.user32.MapVirtualKeyW(vk_code, 0)
        lparam_down = (scan_code << 16) | 1
        lparam_up = (scan_code << 16) | 1 | (1 << 30) | (1 << 31)
        winapi.win32gui.PostMessage(target, WM_KEYDOWN, vk_code, lparam_down)
        time.sleep(max(0.0, hold))
        winapi.win32gui.PostMessage(target, WM_KEYUP, vk_code, lparam_up)
        return True
    except Exception:
        return False
    finally:
        if attached and tid_target:
            try:
                u.AttachThreadInput(tid_cur, tid_target, False)
            except Exception:
                pass


def dump_window_classes(hwnd: int, limit: int = 60) -> str:
    """대상 창 + 자식들의 클래스명을 한 줄 문자열로 덤프(진단용).

    'Chrome_RenderWidgetHostHWND'가 보이면 UI가 CEF(크롬) → 메시지 기반 키 주입 가망.
    안 보이면 네이티브 렌더(입력이 RawInput/DirectInput일 확률↑ → 유저모드 한계).
    """
    if winapi.win32gui is None:
        return "(win32 불가)"
    try:
        top = _get_class_name_safe(hwnd)
        counts: dict[str, int] = {}
        for ch in _get_child_windows(hwnd, limit=limit):
            cn = _get_class_name_safe(ch) or "?"
            counts[cn] = counts.get(cn, 0) + 1
        parts = [f"{c}×{n}" if n > 1 else c for c, n in sorted(counts.items())]
        return f"top='{top}' children[{sum(counts.values())}]: " + ", ".join(parts)
    except Exception as exc:  # noqa: BLE001
        return f"(덤프 실패: {exc})"


def _pick_focus_child(hwnd: int) -> int:
    """SetFocus 대상으로 쓸 '자식 창'을 고른다(top-level activation 회피용).

    우선순위: 게임이 이미 포커스로 보는 자식(GUITHREADINFO.hwndFocus) →
    CEF 렌더 위젯(Chrome_RenderWidgetHostHWND) → 첫 자식. 없으면 0.
    """
    if winapi.win32gui is None or not hasattr(ctypes, "windll"):
        return 0
    try:
        _setup_user32_sigs()
        tid = ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), None)
        info = _GUITHREADINFO()
        info.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if tid and ctypes.windll.user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
            f = int(info.hwndFocus or 0)
            if f and f != int(hwnd) and _is_child_or_same(int(hwnd), f):
                return f
        children = _get_child_windows(hwnd)
        for ch in children:
            if "renderwidgethost" in _get_class_name_safe(ch).lower():
                return int(ch)
        return int(children[0]) if children else 0
    except Exception:
        return 0


def send_key_focus_child(hwnd: int, scan: int = SCAN_S, hold: float = 0.12) -> bool:
    """focus_s와 같은 원리(AttachThreadInput+포커스+SendInput)지만, top-level이 아니라
    '자식 창'에 SetFocus한다 → 깜빡임(top-level activate)을 피한다.

    자식 창은 '활성' 개념이 없어 SetFocus해도 WM_ACTIVATE/캡션 하이라이트/z순서 변화가
    없다(화면 변화 0). focus_s가 전면화 없이 동작한 사실이 '큐 포커스 + SendInput'만으로
    게임이 s를 받는다는 증거이므로, 자식 포커스로도 전달될 것으로 기대한다.
    자식 창이 없으면(OSR CEF 등) 대상이 없어 False → 스윕이 다음 후보로.
    """
    if winapi.win32gui is None or not hasattr(ctypes, "windll"):
        return False
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    attached = []
    tid_cur = k.GetCurrentThreadId()
    try:
        _setup_user32_sigs()
        if not winapi.win32gui.IsWindow(int(hwnd)):
            return False
        child = _pick_focus_child(hwnd)
        if not child:
            return False   # 자식 창 없음 → top-level SetFocus는 깜빡이므로 하지 않는다
        tid_target = u.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), None)
        fg = u.GetForegroundWindow()
        tid_fg = u.GetWindowThreadProcessId(wintypes.HWND(fg), None) if fg else 0
        for tid in {tid_target, tid_fg}:
            if tid and tid != tid_cur and u.AttachThreadInput(tid_cur, tid, True):
                attached.append(tid)
        prev_focus = u.GetFocus()
        u.SetFocus(int(child))
        time.sleep(0.02)
        ok = send_scancode_key(scan, hold)
        try:
            if prev_focus:
                u.SetFocus(prev_focus)
        except Exception:
            pass
        return bool(ok)
    except Exception:
        return False
    finally:
        for tid in attached:
            try:
                u.AttachThreadInput(tid_cur, tid, False)
            except Exception:
                pass


def send_key_focus_attach(hwnd: int, scan: int = SCAN_S, hold: float = 0.12) -> bool:
    """AttachThreadInput+SetFocus로 대상 창에 포커스를 붙여 SendInput 키를 보냅니다.

    ⚠️ 주의(적대적 리뷰 확인): 백그라운드 top-level 창에 SetFocus하면 그 창이
    activate되어 z순서/활성창이 바뀐다 → '화면 깜빡임'이 발생한다. 즉 이 경로는
    깜빡임 0이 아니다(focus_s와 동급). 확실히 동작하지만 화면 변화가 있어,
    기본 스윕에서 제외하고 SKIP_A_BUTTON로 수동 지정할 때만 쓴다(최후수단).
    """
    if winapi.win32gui is None or not hasattr(ctypes, "windll"):
        return False
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    attached = []
    tid_cur = k.GetCurrentThreadId()
    try:
        _setup_user32_sigs()
        if not winapi.win32gui.IsWindow(int(hwnd)):
            return False
        tid_target = u.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), None)
        fg = u.GetForegroundWindow()
        tid_fg = u.GetWindowThreadProcessId(wintypes.HWND(fg), None) if fg else 0
        for tid in {tid_target, tid_fg}:
            if tid and tid != tid_cur and u.AttachThreadInput(tid_cur, tid, True):
                attached.append(tid)
        prev_focus = u.GetFocus()
        u.SetFocus(int(hwnd))
        time.sleep(0.03)
        ok = send_scancode_key(scan, hold)
        try:
            if prev_focus:
                u.SetFocus(prev_focus)
        except Exception:
            pass
        return bool(ok)
    except Exception:
        return False
    finally:
        for tid in attached:
            try:
                u.AttachThreadInput(tid_cur, tid, False)
            except Exception:
                pass


def spoof_window_active(hwnd: int, active: bool = True) -> bool:
    """전면화 없이(화면 변화 0) 대상 창에 '활성' 메시지를 주입해 포커스가 있다고 속입니다.

    WM_ACTIVATEAPP(앱 전체 활성) + WM_ACTIVATE(창 활성) + WM_NCACTIVATE를 Post.
    엔진이 '스레드-로컬 활성 플래그'로 입력 게이팅을 하면(SDL #4450류) 이걸로 속아
    비활성 상태에서도 컷신 스킵 입력을 받는다. GetForegroundWindow를 직접 보면 안 통함.
    실제 z순서/포그라운드는 안 바뀌므로 깜빡임이 없다.
    """
    if winapi.win32gui is None:
        return False
    WM_ACTIVATE = 0x0006
    WM_ACTIVATEAPP = 0x001C
    WM_NCACTIVATE = 0x0086
    WA_ACTIVE = 1
    w = 1 if active else 0
    try:
        winapi.win32gui.PostMessage(int(hwnd), WM_ACTIVATEAPP, w, 0)
        winapi.win32gui.PostMessage(int(hwnd), WM_NCACTIVATE, w, 0)
        winapi.win32gui.PostMessage(int(hwnd), WM_ACTIVATE,
                                    WA_ACTIVE if active else 0, 0)
        return True
    except Exception:
        return False


def get_foreground_hwnd() -> int:
    """현재 전면(포그라운드) 창 HWND. 실패 시 0."""
    if not hasattr(ctypes, "windll"):
        return 0
    try:
        _setup_user32_sigs()
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def bring_foreground(hwnd: int) -> bool:
    """대상 창을 전면으로 올린다(공개 래퍼). 실패 시 False."""
    if winapi.win32gui is None or not hasattr(ctypes, "windll") or not hwnd:
        return False
    try:
        _setup_user32_sigs()
        if not winapi.win32gui.IsWindow(int(hwnd)):
            return False
        return _bring_to_foreground(int(hwnd))
    except Exception:
        return False


def _bring_to_foreground(hwnd: int) -> bool:
    """대상 창을 전면으로. 포그라운드 잠금에 막히면 ALT 트릭으로 한 번 더 시도."""
    u = ctypes.windll.user32
    hwnd = int(hwnd)
    if u.GetForegroundWindow() == hwnd:
        return True
    try:
        SW_RESTORE = 9
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, SW_RESTORE)
    except Exception:
        pass
    u.SetForegroundWindow(hwnd)
    if u.GetForegroundWindow() == hwnd:
        return True
    # ALT 트릭: 우리 프로세스가 '마지막 입력'을 만든 상태면 잠금이 풀린다.
    _send_scancode(_SCAN_ALT, keyup=False)
    _send_scancode(_SCAN_ALT, keyup=True)
    u.SetForegroundWindow(hwnd)
    return u.GetForegroundWindow() == hwnd


def press_key_foreground(hwnd: int, scan: int = SCAN_S, hold: float = 0.12,
                         settle: float = 0.06) -> bool:
    """게임 창을 잠깐 전면으로 올려 SendInput 스캔코드 키를 보내고, 원래 창을 복원한다.

    PostMessage 키를 무시하는(RawInput) 게임에 '진짜 키보드'로 인식되는 최후수단.
    포커스를 ~0.2초 훔치므로 (A) SKIP 같은 드문 이벤트에서만 사용할 것.
    실패(전면 전환 불가/Windows 아님)하면 False — 호출부는 다른 수단으로 폴백.
    """
    if winapi.win32gui is None or not hasattr(ctypes, "windll"):
        return False
    u = ctypes.windll.user32
    try:
        if not winapi.win32gui.IsWindow(int(hwnd)):
            return False
        prev = u.GetForegroundWindow()
        if not _bring_to_foreground(hwnd):
            return False
        time.sleep(max(0.0, settle))   # 포커스 정착 대기(게임이 입력 장치 전환할 시간)
        ok = send_scancode_key(scan, hold)
        time.sleep(0.03)
        if prev and prev != int(hwnd):
            try:
                _bring_to_foreground(prev)   # 원래 전면 창 복원
            except Exception:
                pass
        return bool(ok)
    except Exception:
        return False

def send_key_to_window(hwnd: int, vk_code: int, press_delay: float = 0.05) -> bool:
    """PostMessage로 대상 창에 WM_KEYDOWN/WM_KEYUP을 전송합니다."""
    if winapi.win32gui is None:
        raise RuntimeError("pywin32 win32gui 모듈이 필요합니다.")
    _setup_user32_sigs()
    scan_code = ctypes.windll.user32.MapVirtualKeyW(vk_code, 0)
    lparam_down = (scan_code << 16) | 1
    lparam_up = (scan_code << 16) | 1 | (1 << 30) | (1 << 31)
    winapi.win32gui.PostMessage(hwnd, WM_KEYDOWN, vk_code, lparam_down)
    time.sleep(press_delay)
    winapi.win32gui.PostMessage(hwnd, WM_KEYUP, vk_code, lparam_up)
    return True

def _make_mouse_lparam(x: int, y: int) -> int:
    """Windows 마우스 메시지 lParam을 클라이언트 좌표로 만듭니다."""

    x = int(x)
    y = int(y)
    if winapi.win32api is not None and hasattr(winapi.win32api, "MAKELONG"):
        return int(winapi.win32api.MAKELONG(x, y))

    return (y & 0xFFFF) << 16 | (x & 0xFFFF)

def _send_mouse_message(
    hwnd: int,
    message: int,
    wparam: int,
    x: int,
    y: int,
    *,
    use_send_message: bool = False,
) -> bool:
    """PostMessage 또는 SendMessage로 대상 HWND에 마우스 메시지를 보냅니다."""

    if winapi.win32gui is None:
        raise RuntimeError("pywin32 win32gui 모듈이 필요합니다.")
    if not winapi.win32gui.IsWindow(hwnd):
        raise RuntimeError(f"유효하지 않은 HWND입니다: {hwnd}")

    lparam = _make_mouse_lparam(x, y)
    if use_send_message:
        winapi.win32gui.SendMessage(hwnd, message, wparam, lparam)
    else:
        winapi.win32gui.PostMessage(hwnd, message, wparam, lparam)
    return True

def _is_child_or_same(parent_hwnd: int, child_hwnd: int) -> bool:
    """child_hwnd가 parent_hwnd 자신이거나 그 자식 창인지 확인합니다."""

    if int(parent_hwnd) == int(child_hwnd):
        return True
    if winapi.win32gui is None:
        return False
    try:
        return bool(winapi.win32gui.IsChild(int(parent_hwnd), int(child_hwnd)))
    except Exception:
        return False


def _get_thread_focus_hwnd(hwnd: int) -> Optional[int]:
    """대상 창 스레드의 포커스 HWND를 가져옵니다."""

    if winapi.win32gui is None or not hasattr(ctypes, "windll"):
        return None

    try:
        _setup_user32_sigs()
        thread_id = ctypes.windll.user32.GetWindowThreadProcessId(wintypes.HWND(int(hwnd)), None)
        if not thread_id:
            return None

        info = _GUITHREADINFO()
        info.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if not ctypes.windll.user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            return None

        focus_hwnd = int(info.hwndFocus or 0)
        if focus_hwnd and winapi.win32gui.IsWindow(focus_hwnd) and _is_child_or_same(hwnd, focus_hwnd):
            return focus_hwnd
    except Exception:
        return None

    return None


def _get_child_windows(hwnd: int, limit: int = 80) -> list[int]:
    """대상 창의 자식 HWND 목록을 가져옵니다."""

    if winapi.win32gui is None:
        return []

    children: list[int] = []

    def enum_child(child_hwnd: int, _extra: object) -> bool:
        if len(children) >= limit:
            return False
        try:
            if winapi.win32gui.IsWindow(child_hwnd):
                children.append(int(child_hwnd))
        except Exception:
            pass
        return True

    try:
        winapi.win32gui.EnumChildWindows(int(hwnd), enum_child, None)
    except Exception:
        return children

    return children


def _unique_hwnds(hwnds: list[int]) -> list[int]:
    """순서를 유지하면서 HWND 중복을 제거합니다."""

    seen: set[int] = set()
    result: list[int] = []
    for hwnd in hwnds:
        hwnd = int(hwnd)
        if hwnd in seen:
            continue
        seen.add(hwnd)
        result.append(hwnd)
    return result

def _resolve_win32_message(message: object) -> int:
    """WM_COMMAND, BM_CLICK 같은 메시지 이름 또는 숫자를 int 메시지로 변환합니다."""

    if not isinstance(message, str):
        parsed = _parse_optional_int(message, "message")
        if parsed is not None:
            return parsed

    if not isinstance(message, str) or not message.strip():
        raise ValueError("message 값이 비어 있습니다.")

    constants = winapi._require_win32con()
    message_name = message.strip().upper()
    try:
        return int(message_name, 0)
    except ValueError:
        pass

    if hasattr(constants, message_name):
        return int(getattr(constants, message_name))

    aliases = {
        "BM_CLICK": 0x00F5,
        "WM_COMMAND": 0x0111,
        "WM_CLOSE": 0x0010,
        "WM_SETTEXT": 0x000C,
    }
    if message_name in aliases:
        return aliases[message_name]

    raise ValueError(f"지원하지 않는 Win32 메시지 이름입니다: {message!r}")


def _make_command_wparam(command_id: int, notify_code: int = 0) -> int:
    """WM_COMMAND wParam을 만듭니다."""

    if winapi.win32api is not None and hasattr(winapi.win32api, "MAKELONG"):
        return int(winapi.win32api.MAKELONG(int(command_id), int(notify_code)))
    return ((int(notify_code) & 0xFFFF) << 16) | (int(command_id) & 0xFFFF)

def _get_window_text_safe(hwnd: int) -> str:
    """창 텍스트를 안전하게 가져옵니다."""

    if winapi.win32gui is None:
        return ""
    try:
        return winapi.win32gui.GetWindowText(int(hwnd)) or ""
    except Exception:
        return ""


def _get_class_name_safe(hwnd: int) -> str:
    """창 클래스 이름을 안전하게 가져옵니다."""

    if winapi.win32gui is None:
        return ""
    try:
        return winapi.win32gui.GetClassName(int(hwnd)) or ""
    except Exception:
        return ""


def _get_dlg_item(hwnd: int, control_id: int) -> Optional[int]:
    """GetDlgItem으로 자식 컨트롤 HWND를 찾습니다."""

    if winapi.win32gui is None:
        return None
    try:
        child_hwnd = int(winapi.win32gui.GetDlgItem(int(hwnd), int(control_id)) or 0)
        if child_hwnd and winapi.win32gui.IsWindow(child_hwnd):
            return child_hwnd
    except Exception:
        pass

    if hasattr(ctypes, "windll"):
        try:
            child_hwnd = int(ctypes.windll.user32.GetDlgItem(int(hwnd), int(control_id)) or 0)
            if child_hwnd and winapi.win32gui.IsWindow(child_hwnd):
                return child_hwnd
        except Exception:
            pass

    return None


def _find_child_window(
    hwnd: int,
    *,
    control_id: Optional[int] = None,
    control_hwnd: Optional[int] = None,
    control_class: Optional[str] = None,
    control_text: Optional[str] = None,
) -> Optional[int]:
    """설정된 조건으로 자식 컨트롤 HWND를 찾습니다."""

    if winapi.win32gui is None:
        return None

    if control_hwnd is not None:
        try:
            candidate = int(control_hwnd)
            if winapi.win32gui.IsWindow(candidate) and _is_child_or_same(int(hwnd), candidate):
                return candidate
        except Exception:
            return None

    if control_id is not None:
        child_hwnd = _get_dlg_item(hwnd, control_id)
        if child_hwnd is not None:
            return child_hwnd

    class_filter = control_class.strip().lower() if control_class else None
    text_filter = control_text.strip().lower() if control_text else None
    if class_filter is None and text_filter is None:
        return None

    for child_hwnd in _get_child_windows(hwnd):
        class_name = _get_class_name_safe(child_hwnd).lower()
        window_text = _get_window_text_safe(child_hwnd).lower()
        if class_filter is not None and class_filter not in class_name:
            continue
        if text_filter is not None and text_filter not in window_text:
            continue
        return child_hwnd

    return None


def _send_win32_message(
    hwnd: int,
    message: int,
    wparam: int = 0,
    lparam: int = 0,
    *,
    use_send_message: bool = True,
) -> bool:
    """PostMessage 또는 SendMessage로 일반 Win32 메시지를 보냅니다."""

    if winapi.win32gui is None:
        raise RuntimeError("pywin32 win32gui 모듈이 필요합니다.")
    if not winapi.win32gui.IsWindow(int(hwnd)):
        raise RuntimeError(f"유효하지 않은 HWND입니다: {hwnd}")

    if use_send_message:
        winapi.win32gui.SendMessage(int(hwnd), int(message), int(wparam), int(lparam))
    else:
        winapi.win32gui.PostMessage(int(hwnd), int(message), int(wparam), int(lparam))
    return True

def post_mouse_move(
    hwnd: int,
    x: int,
    y: int,
    *,
    use_send_message: bool = False,
) -> bool:
    """대상 창의 클라이언트 영역 좌표로 WM_MOUSEMOVE를 보냅니다."""

    constants = winapi._require_win32con()
    return _send_mouse_message(
        hwnd,
        constants.WM_MOUSEMOVE,
        0,
        x,
        y,
        use_send_message=use_send_message,
    )


def post_mouse_down(
    hwnd: int,
    x: int,
    y: int,
    *,
    use_send_message: bool = False,
) -> bool:
    """대상 창의 클라이언트 영역 좌표로 마우스 왼쪽 버튼 Down을 보냅니다."""

    constants = winapi._require_win32con()
    return _send_mouse_message(
        hwnd,
        constants.WM_LBUTTONDOWN,
        constants.MK_LBUTTON,
        x,
        y,
        use_send_message=use_send_message,
    )


def post_mouse_up(
    hwnd: int,
    x: int,
    y: int,
    *,
    use_send_message: bool = False,
) -> bool:
    """대상 창의 클라이언트 영역 좌표로 마우스 왼쪽 버튼 Up을 보냅니다."""

    constants = winapi._require_win32con()
    return _send_mouse_message(
        hwnd,
        constants.WM_LBUTTONUP,
        0,
        x,
        y,
        use_send_message=use_send_message,
    )


def post_mouse_click(
    hwnd: int,
    x: int,
    y: int,
    *,
    use_send_message: bool = False,
    hover_delay: float = MOUSE_HOVER_BEFORE_CLICK_SECONDS,
    down_up_delay: float = CLICK_MESSAGE_DELAY_SECONDS,
) -> bool:
    """대상 HWND의 클라이언트 좌표를 왼쪽 클릭합니다."""

    post_mouse_move(hwnd, x, y, use_send_message=use_send_message)
    if hover_delay > 0:
        time.sleep(hover_delay)

    post_mouse_down(hwnd, x, y, use_send_message=use_send_message)
    if down_up_delay > 0:
        time.sleep(down_up_delay)
    post_mouse_up(hwnd, x, y, use_send_message=use_send_message)
    return True
