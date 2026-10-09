"""Single S/A taps without activating a window or publishing keyboard state."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from macroapp import input_gamepad, input_message, winapi


@dataclass
class InactiveSkipResult:
    sent: bool = False
    reason: str = "not_started"
    route: str = ""
    attempted: bool = False
    released: bool = False
    foreground_unchanged: bool = True


def run_inactive_skip_tap(
    hwnd: int,
    *,
    route: str = "pad_a",
    cancelled: Callable[[], bool] = lambda: False,
    clock: Callable[[], float] = time.monotonic,
    wait: Callable[[float], None] = time.sleep,
) -> InactiveSkipResult:
    """Publish one 150 ms native A tap or one 60 ms HWND-only posted S tap.

    This function never changes foreground ownership. Window focus is only
    observed; a change or cancellation ends the tap and releases its button.
    ``sent`` means the input sequence was published without observed failure,
    not that FC consumed it. A virtual controller is a shared device, while
    posted S messages are scoped to the supplied HWND. The caller must validate
    the skip prompt and serialize attempts; this call never changes routes.
    """
    result = InactiveSkipResult(route=route)
    gui = winapi.win32gui
    original = 0

    def blocked() -> str:
        if cancelled():
            return "cancelled"
        if not gui.IsWindow(hwnd):
            return "invalid_target"
        if input_message.get_foreground_hwnd() != original:
            result.foreground_unchanged = False
            return "foreground_changed"
        return ""

    def wait_guarded(seconds: float) -> str:
        deadline = clock() + seconds
        while True:
            reason = blocked()
            if reason:
                return reason
            remaining = deadline - clock()
            if remaining <= 0:
                return ""
            wait(min(0.005, remaining))

    try:
        hwnd = int(hwnd or 0)
        if route not in ("pad_a", "posted_s"):
            result.reason = "invalid_route"
            return result
        if gui is None or not hwnd or not gui.IsWindow(hwnd):
            result.reason = "invalid_target"
            return result
        original = int(input_message.get_foreground_hwnd() or 0)
        if not original:
            result.reason = "foreground_unknown"
            return result
        result.reason = blocked()
        if result.reason:
            return result
        if route == "posted_s":
            down_lparam = (input_message.SCAN_S << 16) | 1
            up_lparam = down_lparam | (1 << 30) | (1 << 31)
            result.attempted = True
            try:
                posted = gui.PostMessage(hwnd, input_message.WM_KEYDOWN, 0x53, down_lparam)
                result.reason = "delivery_failed" if posted is False else wait_guarded(0.06)
            finally:
                # Even an interrupted wait must not leave a posted Down alone.
                posted = gui.PostMessage(hwnd, input_message.WM_KEYUP, 0x53, up_lparam)
                result.released = posted is not False
        else:
            if winapi.vg is None:
                result.reason = "pad_unavailable"
                return result
            button = winapi.vg.XUSB_BUTTON.XUSB_GAMEPAD_A
            # Share the existing controller and report lock with normal input.
            # Lock contention never queues a stale tap indefinitely.
            lock = input_gamepad._gamepad_lock
            if not lock.acquire(timeout=0.05):
                result.reason = "pad_busy"
                return result
            try:
                result.reason = blocked()
                if result.reason:
                    return result
                pad = input_gamepad._get_gamepad()
                result.reason = blocked()
                if result.reason:
                    return result
                result.attempted = True
                try:
                    pad.press_button(button=button)
                    pad.update()
                    result.reason = wait_guarded(0.15)
                finally:
                    try:
                        pad.release_button(button=button)
                    finally:
                        pad.update()
                    result.released = True
                if not result.reason:
                    result.reason = wait_guarded(0.02)
            finally:
                lock.release()
        result.reason = result.reason or blocked()
        if not result.reason:
            result.reason = "sent" if result.released else "release_failed"
            result.sent = result.released
        return result
    except Exception as exc:
        result.reason = f"error:{type(exc).__name__}"
        return result
    finally:
        if original:
            try:
                result.foreground_unchanged = (
                    result.foreground_unchanged
                    and input_message.get_foreground_hwnd() == original
                )
                if not result.foreground_unchanged and result.sent:
                    result.sent = False
                    result.reason = "foreground_changed"
            except Exception:
                result.foreground_unchanged = False
                result.sent = False
