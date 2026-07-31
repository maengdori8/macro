from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from macroapp import gui


def test_start_hold_dispatches_virtual_gamepad_start() -> None:
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": object()}, clear=False),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate("start_hold", manager)
    assert send.call_args.kwargs["press_delay"] == gui.SKIP_A_SWEEP_HOLD_SECONDS


def test_spoof_start_hold_wraps_start_with_inactive_activation_messages() -> None:
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": object()}, clear=False),
        patch.object(gui.input_message, "spoof_window_active", return_value=True) as spoof,
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_start_hold",
            manager,
        )
    assert [call.args for call in spoof.call_args_list] == [(123, True), (123, False)]
    assert send.call_args.kwargs["press_delay"] == gui.SKIP_A_SWEEP_HOLD_SECONDS


def test_click_prompt_uses_only_background_window_messages() -> None:
    manager = SimpleNamespace(
        hwnd=123,
        get_virtual_start_position=lambda x, y: (10, 20),
        post_curved_click=lambda *args: args == (10, 20, 300, 400),
    )
    assert gui.AutomationApp._press_skip_candidate(
        "click_prompt",
        manager,
        (300, 400),
    )
    assert not gui.AutomationApp._press_skip_candidate(
        "click_prompt",
        manager,
        None,
    )
