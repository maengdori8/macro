from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
import pytest

from macroapp import gui


def test_generic_prompt_hints_select_independent_trackers() -> None:
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app._skip_generic_experiment = object()
    app._skip_generic_any_key_experiment = object()
    app._skip_generic_escape_experiment = object()
    app._skip_generic_escape_highlight_experiment = object()
    app._skip_generic_hint = "any_key"

    assert app._generic_tracker_for_hint() is app._skip_generic_any_key_experiment
    assert (
        app._generic_tracker_for_hint("escape")
        is app._skip_generic_escape_experiment
    )
    assert app._generic_tracker_for_hint("start") is app._skip_generic_experiment
    assert app._generic_learning_key("any_key") == "generic_any_key"
    assert app._generic_learning_key("escape") == "generic_escape"
    assert (
        app._generic_tracker_for_hint("escape_highlight")
        is app._skip_generic_escape_highlight_experiment
    )
    assert (
        app._generic_learning_key("escape_highlight")
        == "generic_escape_highlight"
    )
    assert (
        app._generic_hint_for_tracker(
            app._skip_generic_escape_highlight_experiment
        )
        == "escape_highlight"
    )

    app._skip_generic_episode_hint = "escape"
    app._skip_generic_hint = "any_key"
    assert app._generic_tracker_for_hint() is app._skip_generic_escape_experiment
    assert (
        app._generic_hint_for_tracker(app._skip_generic_escape_experiment)
        == "escape"
    )


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


def test_spoof_envelope_control_matches_timing_without_gamepad_input() -> None:
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=[True, True],
        ) as spoof,
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_envelope2_control",
            manager,
        )

    assert spoof.call_args_list == [call(123, True), call(123, False)]
    sleep.assert_called_once_with(0.50)
    send.assert_not_called()


def test_spoof_b_and_back_sequences_use_two_pulses_in_one_envelope() -> None:
    manager = SimpleNamespace(hwnd=123)
    b_button = object()
    back_button = object()
    with (
        patch.dict(
            gui.input_gamepad.KEY_TO_GAMEPAD,
            {"b": b_button, "back": back_button},
            clear=False,
        ),
        patch.object(
            gui.input_message,
            "spoof_window_active",
            return_value=True,
        ) as spoof,
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep"),
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_b_envelope2",
            manager,
        )
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_back_envelope2",
            manager,
        )

    assert [call.args for call in spoof.call_args_list] == [
        (123, True), (123, False), (123, True), (123, False),
    ]
    assert [call.args[0] for call in send.call_args_list] == [
        b_button, b_button, back_button, back_button,
    ]


def test_escape_replication_block_reuses_exact_start_envelope_action() -> None:
    manager = SimpleNamespace(hwnd=123)
    start_button = object()
    with (
        patch.dict(
            gui.input_gamepad.KEY_TO_GAMEPAD,
            {"start": start_button},
            clear=False,
        ),
        patch.object(
            gui.input_message,
            "spoof_window_active",
            return_value=True,
        ) as spoof,
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep"),
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_start_envelope2_escape_block",
            manager,
        )

    assert [call.args for call in spoof.call_args_list] == [
        (123, True), (123, False),
    ]
    assert send.call_count == 2
    assert all(item.args[0] is start_button for item in send.call_args_list)


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


def test_synchronous_click_prompt_stays_target_window_only() -> None:
    calls = []
    manager = SimpleNamespace(
        hwnd=123,
        get_virtual_start_position=lambda x, y: (10, 20),
        post_curved_click=lambda *args, **kwargs: calls.append(
            (args, kwargs)
        ) or True,
    )
    assert gui.AutomationApp._press_skip_candidate(
        "click_prompt_sync",
        manager,
        (300, 400),
    )
    assert calls == [((10, 20, 300, 400), {"use_send_message": True})]


def test_noactivate_click_primes_only_target_mouse_route() -> None:
    manager = SimpleNamespace(
        hwnd=123,
        get_virtual_start_position=lambda x, y: (10, 20),
        post_curved_click=lambda *args, **kwargs: True,
    )
    with patch.object(
        gui.input_message,
        "prime_mouse_noactivate",
        return_value=True,
    ) as prime:
        assert gui.AutomationApp._press_skip_candidate(
            "click_prompt_noactivate",
            manager,
            (300, 400),
        )
    prime.assert_called_once_with(123)


def test_escape_candidate_posts_only_to_the_target_window_tree() -> None:
    manager = SimpleNamespace(hwnd=123)
    with patch.object(
        gui.input_message,
        "post_key_deep",
        return_value=True,
    ) as post:
        assert gui.AutomationApp._press_skip_candidate("pm_esc_hold", manager)
    assert post.call_args.args[:2] == (123, gui.input_message.KEY_TO_VK["esc"])
    assert post.call_args.kwargs["press_delay"] == 1.0


def test_sync_escape_candidate_uses_bounded_window_messages() -> None:
    manager = SimpleNamespace(hwnd=123)
    with patch.object(
        gui.input_message,
        "send_key_deep_sync",
        return_value=True,
    ) as send:
        assert gui.AutomationApp._press_skip_candidate("sync_pm_esc", manager)
    assert send.call_args.args[:2] == (123, gui.input_message.KEY_TO_VK["esc"])
    assert send.call_args.kwargs["char_code"] is None


def test_callback_escape_candidate_uses_target_only_callback_messages() -> None:
    manager = SimpleNamespace(hwnd=123)
    with patch.object(
        gui.input_message,
        "send_key_deep_callback",
        return_value=True,
    ) as send:
        assert gui.AutomationApp._press_skip_candidate("callback_pm_esc", manager)
    assert send.call_args.args[:2] == (123, gui.input_message.KEY_TO_VK["esc"])


def test_callback_space_candidate_uses_target_only_callback_messages() -> None:
    manager = SimpleNamespace(hwnd=123)
    with patch.object(
        gui.input_message,
        "send_key_deep_callback",
        return_value=True,
    ) as send:
        assert gui.AutomationApp._press_skip_candidate("callback_pm_space", manager)
    assert send.call_args.args[:2] == (123, gui.input_message.KEY_TO_VK["space"])


def test_ds4_touchpad_candidate_uses_special_virtual_pad_route() -> None:
    manager = SimpleNamespace(hwnd=123)
    with patch.object(gui, "send_ds4_button", return_value=True) as send:
        assert gui.AutomationApp._press_skip_candidate("ds4_touchpad", manager)
    send.assert_called_once()
    assert send.call_args.args[0] == "touchpad"


def test_device_ds4_touchpad_rescans_only_target_before_special_button() -> None:
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.object(
            gui.input_message,
            "notify_device_rescan",
            return_value=True,
        ) as rescan,
        patch.object(gui, "send_ds4_button", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "device_ds4_touchpad",
            manager,
        )
    rescan.assert_called_once_with(123)
    assert send.call_args.args[0] == "touchpad"


def test_spoof_escape_candidate_restores_inactive_activation_state() -> None:
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.object(gui.input_message, "spoof_window_active", return_value=True) as spoof,
        patch.object(gui.input_message, "post_key_deep", return_value=True),
    ):
        assert gui.AutomationApp._press_skip_candidate("spoof_pm_esc", manager)
    assert [call.args for call in spoof.call_args_list] == [(123, True), (123, False)]


def test_escape_component_candidates_split_target_activation_flags() -> None:
    manager = SimpleNamespace(hwnd=123)
    candidates = (
        ("focusmsg_pm_esc", "focus"),
        ("appmsg_pm_esc", "app"),
        ("windowmsg_pm_esc", "window"),
    )
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active_component",
            return_value=True,
        ) as spoof,
        patch.object(
            gui.input_message,
            "post_key_deep",
            return_value=True,
        ) as post,
    ):
        for candidate, _ in candidates:
            assert gui.AutomationApp._press_skip_candidate(candidate, manager)

    assert spoof.call_args_list == [
        call(123, "focus", True), call(123, "focus", False),
        call(123, "app", True), call(123, "app", False),
        call(123, "window", True), call(123, "window", False),
    ]
    assert post.call_args_list == [
        call(123, gui.input_message.KEY_TO_VK["esc"], press_delay=0.15),
    ] * 3


def test_start_component_candidates_cross_activation_with_virtual_pad() -> None:
    manager = SimpleNamespace(hwnd=123)
    start = object()
    candidates = (
        ("focusmsg_start_envelope2", "focus"),
        ("appmsg_start_envelope2", "app"),
        ("windowmsg_start_envelope2", "window"),
    )
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active_component",
            return_value=True,
        ) as spoof,
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        for candidate, _ in candidates:
            assert gui.AutomationApp._press_skip_candidate(candidate, manager)

    assert spoof.call_args_list == [
        call(123, "focus", True), call(123, "focus", False),
        call(123, "app", True), call(123, "app", False),
        call(123, "window", True), call(123, "window", False),
    ]
    assert send.call_args_list == [
        call(start, press_delay=0.18), call(start, press_delay=0.18),
    ] * 3
    assert sleep.call_args_list == [call(0.10)] * 3


def test_focus_window_start_candidate_combines_only_promising_components() -> None:
    manager = SimpleNamespace(hwnd=123)
    start = object()
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active_component",
            return_value=True,
        ) as spoof,
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "focuswindow_start_envelope2",
            manager,
        )

    assert spoof.call_args_list == [
        call(123, "focus", True),
        call(123, "window", True),
        call(123, "window", False),
        call(123, "focus", False),
    ]
    assert send.call_args_list == [
        call(start, press_delay=0.18),
        call(start, press_delay=0.18),
    ]
    assert sleep.call_args_list == [call(0.10)]


def test_focus_window_start_candidate_unwinds_partial_activation() -> None:
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active_component",
            side_effect=[True, False, True],
        ) as spoof,
        patch.object(gui, "send_gamepad_button") as send,
    ):
        assert not gui.AutomationApp._press_skip_candidate(
            "focuswindow_start_envelope2",
            manager,
        )

    assert spoof.call_args_list == [
        call(123, "focus", True),
        call(123, "window", True),
        call(123, "focus", False),
    ]
    send.assert_not_called()


def test_compact_component_candidates_reduce_hold_and_gap() -> None:
    manager = SimpleNamespace(hwnd=123)
    start = object()
    candidates = (
        "focusmsg_start_compact",
        "windowmsg_start_compact",
        "focuswindow_start_compact",
    )
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active_component",
            return_value=True,
        ),
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        for candidate in candidates:
            assert gui.AutomationApp._press_skip_candidate(candidate, manager)

    assert send.call_args_list == [
        call(start, press_delay=0.15), call(start, press_delay=0.15),
    ] * 3
    assert sleep.call_args_list == [call(0.05)] * 3


def test_window_component_refreshed_pair_republishes_held_start() -> None:
    manager = SimpleNamespace(hwnd=123)
    start = object()
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active_component",
            return_value=True,
        ) as spoof,
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(
            gui.input_gamepad,
            "send_gamepad_button_refreshed",
            return_value=True,
        ) as refreshed,
        patch.object(gui, "send_gamepad_button") as ordinary,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "windowmsg_start_refresh2",
            manager,
        )

    assert spoof.call_args_list == [
        call(123, "window", True),
        call(123, "window", False),
    ]
    assert refreshed.call_args_list == [
        call(start, press_delay=0.15),
        call(start, press_delay=0.15),
    ]
    ordinary.assert_not_called()
    sleep.assert_called_once_with(0.05)


def test_window_component_per_edge_candidate_aligns_each_start_rise() -> None:
    manager = SimpleNamespace(hwnd=123)
    start = object()
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active_component",
            return_value=True,
        ) as spoof,
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "windowmsg_start_edge2",
            manager,
        )

    assert spoof.call_args_list == [
        call(123, "window", True),
        call(123, "window", False),
        call(123, "window", True),
        call(123, "window", False),
    ]
    assert send.call_args_list == [
        call(start, press_delay=0.15),
        call(start, press_delay=0.15),
    ]
    sleep.assert_called_once_with(0.05)


@pytest.mark.parametrize(
    ("candidate", "settle"),
    (
        ("windowmsg_start_compact_settle100", 0.10),
        ("windowmsg_start_compact_settle200", 0.20),
    ),
)
def test_window_component_compact_settle_keeps_gate_after_release(
    candidate: str,
    settle: float,
) -> None:
    manager = SimpleNamespace(hwnd=123)
    start = object()
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active_component",
            return_value=True,
        ) as spoof,
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(candidate, manager)

    assert spoof.call_args_list == [
        call(123, "window", True),
        call(123, "window", False),
    ]
    assert send.call_args_list == [
        call(start, press_delay=0.15),
        call(start, press_delay=0.15),
    ]
    assert sleep.call_args_list == [call(0.05), call(settle)]


def test_spread_component_candidates_cover_separate_polling_intervals() -> None:
    manager = SimpleNamespace(hwnd=123)
    start = object()
    candidates = (
        "focusmsg_start_spread650",
        "windowmsg_start_spread650",
        "focuswindow_start_spread650",
    )
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active_component",
            return_value=True,
        ),
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        for candidate in candidates:
            assert gui.AutomationApp._press_skip_candidate(candidate, manager)

    assert send.call_args_list == [
        call(start, press_delay=0.18), call(start, press_delay=0.18),
    ] * 3
    assert sleep.call_args_list == [call(0.65)] * 3


def test_spoof_escape_sequence_keeps_one_activation_envelope() -> None:
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=[True, True],
        ) as spoof,
        patch.object(
            gui.input_message,
            "post_key_deep",
            return_value=True,
        ) as post,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_pm_esc_envelope2",
            manager,
        )
    assert spoof.call_args_list == [call(123, True), call(123, False)]
    assert post.call_count == 2
    post.assert_has_calls(
        [
            call(123, gui.input_message.KEY_TO_VK["esc"], press_delay=0.18),
            call(123, gui.input_message.KEY_TO_VK["esc"], press_delay=0.18),
        ]
    )
    sleep.assert_called_once_with(0.10)


def test_system_escape_uses_target_only_system_key_messages() -> None:
    manager = SimpleNamespace(hwnd=123)
    with patch.object(
        gui.input_message,
        "post_system_key_deep",
        return_value=True,
    ) as post:
        assert gui.AutomationApp._press_skip_candidate("sys_pm_esc", manager)
    assert post.call_args.args[:2] == (
        123,
        gui.input_message.KEY_TO_VK["esc"],
    )


def test_delayed_sync_escape_keeps_delay_inside_guarded_action() -> None:
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.object(gui.time, "sleep") as sleep,
        patch.object(gui.input_message, "send_key_deep_sync", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "sync_pm_esc_delay50",
            manager,
        )
    sleep.assert_called_once_with(0.05)
    assert send.call_args.args[:2] == (
        123,
        gui.input_message.KEY_TO_VK["esc"],
    )


def test_spoof_sync_escape_combines_activation_and_bounded_sync_message() -> None:
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.object(gui.input_message, "spoof_window_active", return_value=True) as spoof,
        patch.object(gui.input_message, "send_key_deep_sync", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_sync_pm_esc",
            manager,
        )
    assert [call.args for call in spoof.call_args_list] == [(123, True), (123, False)]
    assert send.call_args.kwargs["char_code"] is None


def test_device_rescan_candidate_notifies_fc_before_virtual_button() -> None:
    button = object()
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": button}, clear=False),
        patch.object(gui.input_message, "notify_device_rescan", return_value=True) as notify,
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate("device_start", manager)
    notify.assert_called_once_with(123)
    send.assert_called_once_with(button, press_delay=0.15)


def test_any_key_space_candidate_uses_target_window_char_messages() -> None:
    manager = SimpleNamespace(hwnd=123)
    with patch.object(
        gui.input_message,
        "post_char_deep",
        return_value=True,
    ) as post:
        assert gui.AutomationApp._press_skip_candidate(
            "char_space_hold",
            manager,
        )
    assert post.call_args.args[:3] == (
        123,
        gui.input_message.KEY_TO_VK["space"],
        0x20,
    )
    assert post.call_args.kwargs["press_delay"] == 1.0


def test_escape_char_candidate_delivers_wm_char_1b_to_target_only() -> None:
    manager = SimpleNamespace(hwnd=123)
    with patch.object(
        gui.input_message,
        "post_char_deep",
        return_value=True,
    ) as post:
        assert gui.AutomationApp._press_skip_candidate("char_esc", manager)
    assert post.call_args.args[:3] == (
        123,
        gui.input_message.KEY_TO_VK["esc"],
        0x1B,
    )


def test_synchronous_escape_char_candidate_uses_target_messages() -> None:
    manager = SimpleNamespace(hwnd=123)
    with patch.object(
        gui.input_message,
        "send_key_deep_sync",
        return_value=True,
    ) as send:
        assert gui.AutomationApp._press_skip_candidate(
            "sync_char_esc",
            manager,
        )
    assert send.call_args.args[:2] == (
        123,
        gui.input_message.KEY_TO_VK["esc"],
    )
    assert send.call_args.kwargs["char_code"] == 0x1B


def test_escape_thread_candidate_posts_only_to_fc_ui_thread_queue() -> None:
    manager = SimpleNamespace(hwnd=123)
    with patch.object(
        gui.input_message,
        "post_key_thread",
        return_value=True,
    ) as post:
        assert gui.AutomationApp._press_skip_candidate(
            "thread_char_esc",
            manager,
        )
    assert post.call_args.args[:2] == (
        123,
        gui.input_message.KEY_TO_VK["esc"],
    )
    assert post.call_args.kwargs["char_code"] == 0x1B


def test_system_escape_can_target_only_fc_ui_thread_queue() -> None:
    manager = SimpleNamespace(hwnd=123)
    with patch.object(
        gui.input_message,
        "post_system_key_thread",
        return_value=True,
    ) as post:
        assert gui.AutomationApp._press_skip_candidate(
            "thread_sys_esc",
            manager,
        )
    assert post.call_args.args[:2] == (
        123,
        gui.input_message.KEY_TO_VK["esc"],
    )


def test_spoof_thread_system_escape_restores_inactive_state() -> None:
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.object(gui.input_message, "spoof_window_active", return_value=True) as spoof,
        patch.object(gui.input_message, "post_system_key_thread", return_value=True) as post,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_thread_sys_esc_pulse2",
            manager,
        )
    assert [call.args for call in spoof.call_args_list] == [
        (123, True), (123, False),
        (123, True), (123, False),
    ]
    assert post.call_count == 2
    assert all(
        call.args[:2] == (123, gui.input_message.KEY_TO_VK["esc"])
        for call in post.call_args_list
    )


def test_partial_space_followups_use_only_target_window_routes() -> None:
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.object(gui.input_message, "send_key_deep_sync", return_value=True) as sync,
        patch.object(gui.input_message, "post_key_thread", return_value=True) as thread,
        patch.object(gui.input_message, "spoof_window_active", return_value=True) as spoof,
        patch.object(gui.input_message, "post_key_deep", return_value=True) as post,
    ):
        assert gui.AutomationApp._press_skip_candidate("sync_pm_space", manager)
        assert gui.AutomationApp._press_skip_candidate("thread_pm_space", manager)
        assert gui.AutomationApp._press_skip_candidate("spoof_pm_space", manager)

    space_vk = gui.input_message.KEY_TO_VK["space"]
    assert sync.call_args.args[:2] == (123, space_vk)
    assert thread.call_args.args[:2] == (123, space_vk)
    assert post.call_args.args[:2] == (123, space_vk)
    assert [call.args for call in spoof.call_args_list] == [(123, True), (123, False)]


def test_space_timing_candidates_keep_distinct_release_and_pulse_shapes() -> None:
    hold_1150 = gui.get_skip_candidate_spec("pm_space_hold_1150")
    hold = gui.get_skip_candidate_spec("pm_space_hold_1250")
    hold_1350 = gui.get_skip_candidate_spec("pm_space_hold_1350")
    pulse = gui.get_skip_candidate_spec("pm_space_pulse2")
    assert hold_1150 is not None and hold_1150.hold_seconds == 1.15
    assert hold is not None and hold.hold_seconds == 1.25 and hold.pulses == 1
    assert hold_1350 is not None and hold_1350.hold_seconds == 1.35
    assert pulse is not None and pulse.hold_seconds == 0.40 and pulse.pulses == 2


def test_notify_and_single_transition_candidates_remain_target_only() -> None:
    manager = SimpleNamespace(hwnd=123)
    with (
        patch.object(gui.input_message, "send_notify_key_deep", return_value=True) as notify,
        patch.object(gui.input_message, "post_key_transition_deep", return_value=True) as transition,
    ):
        assert gui.AutomationApp._press_skip_candidate("notify_pm_esc", manager)
        assert gui.AutomationApp._press_skip_candidate("up_pm_space", manager)
        assert gui.AutomationApp._press_skip_candidate("down_pm_esc", manager)

    assert notify.call_args.args[:2] == (
        123,
        gui.input_message.KEY_TO_VK["esc"],
    )
    assert transition.call_args_list[0].args[:2] == (
        123,
        gui.input_message.KEY_TO_VK["space"],
    )
    assert transition.call_args_list[0].kwargs["key_up"] is True
    assert transition.call_args_list[1].args[:2] == (
        123,
        gui.input_message.KEY_TO_VK["esc"],
    )
    assert transition.call_args_list[1].kwargs["key_up"] is False


def test_ds4_candidates_use_independent_virtual_controller_path() -> None:
    manager = SimpleNamespace(hwnd=123)
    with patch.object(gui, "send_ds4_button", return_value=True) as send:
        assert gui.AutomationApp._press_skip_candidate("ds4_cross_hold500", manager)
        assert gui.AutomationApp._press_skip_candidate("ds4_options", manager)
        assert gui.AutomationApp._press_skip_candidate("ds4_circle", manager)
        assert gui.AutomationApp._press_skip_candidate("ds4_share", manager)
    assert send.call_args_list[0].args == ("cross",)
    assert send.call_args_list[0].kwargs["press_delay"] == 0.50
    assert send.call_args_list[1].args == ("options",)
    assert send.call_args_list[2].args == ("circle",)
    assert send.call_args_list[3].args == ("share",)


def test_ds4_spoof_candidates_restore_inactive_state() -> None:
    manager = SimpleNamespace(hwnd=31337)
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=[True, True],
        ) as spoof,
        patch.object(gui, "send_ds4_button", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_ds4_options",
            manager,
        )
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    send.assert_called_once_with("options", press_delay=0.15)


def test_ds4_circle_spoof_restores_inactive_state() -> None:
    manager = SimpleNamespace(hwnd=31337)
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=[True, True],
        ) as spoof,
        patch.object(gui, "send_ds4_button", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_ds4_circle",
            manager,
        )
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    send.assert_called_once_with("circle", press_delay=0.15)


def test_ds4_share_spoof_restores_inactive_state() -> None:
    manager = SimpleNamespace(hwnd=31337)
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=[True, True],
        ) as spoof,
        patch.object(gui, "send_ds4_button", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_ds4_share",
            manager,
        )
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    send.assert_called_once_with("share", press_delay=0.15)


def test_ds4_rescan_control_sends_no_button() -> None:
    manager = SimpleNamespace(hwnd=31337)
    with (
        patch.object(
            gui.input_gamepad,
            "ensure_virtual_gamepad",
            return_value=True,
        ) as ensure,
        patch.object(
            gui.input_message,
            "notify_device_rescan",
            return_value=True,
        ) as notify,
        patch.object(gui, "send_ds4_button") as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "device_ds4_rescan_control",
            manager,
        )
    ensure.assert_called_once_with("ds4")
    notify.assert_called_once_with(31337)
    send.assert_not_called()


def test_ds4_share_rescan_hold500_reuses_device_route() -> None:
    manager = SimpleNamespace(hwnd=31337)
    with (
        patch.object(
            gui.input_message,
            "notify_device_rescan",
            return_value=True,
        ) as notify,
        patch.object(gui, "send_ds4_button", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "device_ds4_share_hold500",
            manager,
        )
    notify.assert_called_once_with(31337)
    send.assert_called_once_with("share", press_delay=0.50)


def test_start_sequence_keeps_one_activation_envelope() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=[True, True],
        ) as spoof,
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_start_envelope2",
            manager,
        )
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    assert send.call_count == 2
    send.assert_has_calls(
        [call(start, press_delay=0.18), call(start, press_delay=0.18)]
    )
    sleep.assert_called_once_with(0.10)


def test_process_device_envelopes_rescan_then_send_targeted_xbox_pulses() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    button_a = object()
    with (
        patch.dict(
            gui.input_gamepad.KEY_TO_GAMEPAD,
            {"start": start, "a": button_a},
        ),
        patch.object(
            gui.input_gamepad,
            "ensure_virtual_gamepad",
            return_value=True,
        ) as ensure,
        patch.object(
            gui.input_message,
            "notify_device_rescan_process",
            return_value=True,
        ) as rescan,
        patch.object(
            gui.input_message,
            "spoof_window_active",
            return_value=True,
        ) as spoof,
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_envelope2",
            manager,
        )
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_a_envelope2",
            manager,
        )
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_fast3",
            manager,
        )
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_envelope2_settle0",
            manager,
        )
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_envelope2_gap50",
            manager,
        )
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_envelope2_compact",
            manager,
        )
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_edge60_pair",
            manager,
        )
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_edge40_pair",
            manager,
        )
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_single150",
            manager,
        )
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_single180",
            manager,
        )
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_wake40_finish150",
            manager,
        )
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_wake60_finish150",
            manager,
        )

    assert ensure.call_args_list == [call("xbox")] * 12
    assert rescan.call_args_list == [
        call(31337, settle_seconds=0.0),
        call(31337, settle_seconds=0.0),
        call(31337, settle_seconds=0.0),
        call(31337, settle_seconds=0.0),
        call(31337, settle_seconds=0.0),
        call(31337, settle_seconds=0.0),
    ] * 2
    assert spoof.call_args_list == [
        call(31337, True), call(31337, False),
        call(31337, True), call(31337, False),
        call(31337, True), call(31337, False),
        call(31337, True), call(31337, False),
        call(31337, True), call(31337, False),
        call(31337, True), call(31337, False),
    ] * 2
    assert send.call_args_list == [
        call(start, press_delay=0.18),
        call(start, press_delay=0.18),
        call(button_a, press_delay=0.18),
        call(button_a, press_delay=0.18),
        call(start, press_delay=0.06),
        call(start, press_delay=0.06),
        call(start, press_delay=0.06),
        call(start, press_delay=0.18),
        call(start, press_delay=0.18),
        call(start, press_delay=0.18),
        call(start, press_delay=0.18),
        call(start, press_delay=0.15),
        call(start, press_delay=0.15),
        call(start, press_delay=0.06),
        call(start, press_delay=0.06),
        call(start, press_delay=0.04),
        call(start, press_delay=0.04),
        call(start, press_delay=0.15),
        call(start, press_delay=0.18),
        call(start, press_delay=0.04),
        call(start, press_delay=0.15),
        call(start, press_delay=0.06),
        call(start, press_delay=0.15),
    ]
    assert sleep.call_args_list == [
        call(0.12), call(0.10), call(0.12), call(0.10),
        call(0.02), call(0.02),
        call(0.10), call(0.05), call(0.05), call(0.02), call(0.01),
        call(0.01), call(0.02),
    ]


def test_process_device_compact4_covers_four_proven_length_edges() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(
            gui.input_gamepad,
            "ensure_virtual_gamepad",
            return_value=True,
        ) as ensure,
        patch.object(
            gui.input_message,
            "notify_device_rescan_process",
            return_value=True,
        ) as rescan,
        patch.object(
            gui.input_message,
            "spoof_window_active",
            return_value=True,
        ) as spoof,
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_compact4",
            manager,
        )

    ensure.assert_called_once_with("xbox")
    rescan.assert_called_once_with(31337, settle_seconds=0.0)
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    assert send.call_args_list == [call(start, press_delay=0.15)] * 4
    assert sleep.call_args_list == [call(0.05)] * 3


def test_process_device_pair_rehandshake_repeats_the_complete_handshake() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(
            gui.input_gamepad,
            "ensure_virtual_gamepad",
            return_value=True,
        ) as ensure,
        patch.object(
            gui.input_message,
            "notify_device_rescan_process",
            return_value=True,
        ) as rescan,
        patch.object(
            gui.input_message,
            "spoof_window_active",
            return_value=True,
        ) as spoof,
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_pair_rehandshake2",
            manager,
        )

    ensure.assert_called_once_with("xbox")
    assert rescan.call_args_list == [
        call(31337, settle_seconds=0.0),
        call(31337, settle_seconds=0.0),
    ]
    assert spoof.call_args_list == [
        call(31337, True),
        call(31337, False),
        call(31337, True),
        call(31337, False),
    ]
    assert send.call_args_list == [call(start, press_delay=0.15)] * 4
    assert sleep.call_args_list == [call(0.05)] * 3


def test_process_device_rescan2_pair_isolates_the_rescan_retry() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_message,
            "notify_device_rescan_process",
            return_value=True,
        ) as rescan,
        patch.object(
            gui.input_message,
            "spoof_window_active",
            return_value=True,
        ) as spoof,
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_rescan2_pair",
            manager,
        )

    assert rescan.call_args_list == [
        call(31337, settle_seconds=0.0),
        call(31337, settle_seconds=0.0),
    ]
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    assert send.call_args_list == [call(start, press_delay=0.15)] * 2
    assert sleep.call_args_list == [call(0.05), call(0.05)]


def test_process_device_activation_rearm_pair_isolates_activation_edges() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_message,
            "notify_device_rescan_process",
            return_value=True,
        ) as rescan,
        patch.object(
            gui.input_message,
            "spoof_window_active",
            return_value=True,
        ) as spoof,
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_activation_rearm_pair",
            manager,
        )

    rescan.assert_called_once_with(31337, settle_seconds=0.0)
    assert spoof.call_args_list == [
        call(31337, True),
        call(31337, False),
        call(31337, True),
        call(31337, False),
    ]
    assert send.call_args_list == [call(start, press_delay=0.15)] * 2
    sleep.assert_called_once_with(0.05)


def test_process_device_preactivate_pair_samples_neutral_while_active() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(gui.input_message, "notify_device_rescan_process", return_value=True),
        patch.object(gui.input_message, "spoof_window_active", return_value=True),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_preactivate150_pair",
            manager,
        )

    assert send.call_args_list == [call(start, press_delay=0.15)] * 2
    assert sleep.call_args_list == [call(0.15), call(0.05)]


def test_process_device_reset_compact_publishes_neutral_before_rescan() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    calls: list[str] = []
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_gamepad,
            "reset_virtual_gamepad",
            side_effect=lambda *_args, **_kwargs: calls.append("reset") or True,
        ) as reset,
        patch.object(
            gui.input_message,
            "notify_device_rescan_process",
            side_effect=lambda *_args, **_kwargs: calls.append("rescan") or True,
        ),
        patch.object(gui.input_message, "spoof_window_active", return_value=True),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_reset_start_compact",
            manager,
        )

    reset.assert_called_once_with("xbox", settle_seconds=0.05)
    assert calls == ["reset", "rescan"]
    assert send.call_args_list == [call(start, press_delay=0.15)] * 2
    sleep.assert_called_once_with(0.05)


def test_process_device_sync_compact_waits_for_relevant_rescan() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    calls: list[str] = []
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_message,
            "notify_device_rescan_relevant_sync",
            side_effect=lambda *_args, **_kwargs: calls.append("sync_rescan") or True,
        ) as sync_rescan,
        patch.object(
            gui.input_message,
            "notify_device_rescan_process",
            side_effect=AssertionError("async rescan must not run"),
        ),
        patch.object(gui.input_message, "spoof_window_active", return_value=True) as spoof,
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_sync_spoof_start_compact",
            manager,
        )

    sync_rescan.assert_called_once_with(
        31337,
        timeout_ms=80,
        settle_seconds=0.0,
    )
    assert calls == ["sync_rescan"]
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    assert send.call_args_list == [call(start, press_delay=0.15)] * 2
    sleep.assert_called_once_with(0.05)


@pytest.mark.parametrize(
    ("candidate", "expected_discovery"),
    (
        ("process_spoof_device_start_compact", "async"),
        ("process_spoof_device_sync_start_compact", "sync"),
    ),
)
def test_active_first_device_factorial_rescans_inside_envelope(
    candidate: str,
    expected_discovery: str,
) -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    calls: list[str] = []
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_message,
            "notify_device_rescan_process",
            side_effect=lambda *_args, **_kwargs: calls.append("async") or True,
        ) as async_rescan,
        patch.object(
            gui.input_message,
            "notify_device_rescan_relevant_sync",
            side_effect=lambda *_args, **_kwargs: calls.append("sync") or True,
        ) as sync_rescan,
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=lambda _hwnd, active: calls.append(
                "active" if active else "inactive"
            ) or True,
        ),
        patch.object(
            gui,
            "send_gamepad_button",
            side_effect=lambda *_args, **_kwargs: calls.append("start") or True,
        ) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(candidate, manager)

    assert calls == ["active", expected_discovery, "start", "start", "inactive"]
    if expected_discovery == "async":
        async_rescan.assert_called_once_with(31337, settle_seconds=0.05)
        sync_rescan.assert_not_called()
    else:
        sync_rescan.assert_called_once_with(
            31337,
            timeout_ms=80,
            settle_seconds=0.0,
        )
        async_rescan.assert_not_called()
    assert send.call_args_list == [call(start, press_delay=0.15)] * 2
    sleep.assert_called_once_with(0.05)


def test_active_first_sync_device_and_raw_factorial_stays_inside_envelope() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    calls: list[str] = []
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_message,
            "notify_device_rescan_relevant_sync",
            side_effect=lambda *_args, **_kwargs: calls.append("sync_device") or True,
        ) as sync_device,
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_relevant_sync",
            side_effect=lambda *_args, **_kwargs: calls.append("sync_raw") or True,
        ) as sync_raw,
        patch.object(
            gui.input_message,
            "notify_device_rescan_process",
            side_effect=AssertionError("async device rescan must not run"),
        ),
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_process",
            side_effect=AssertionError("async Raw Input delivery must not run"),
        ),
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=lambda _hwnd, active: calls.append(
                "active" if active else "inactive"
            ) or True,
        ),
        patch.object(
            gui,
            "send_gamepad_button",
            side_effect=lambda *_args, **_kwargs: calls.append("start") or True,
        ) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_spoof_device_raw_sync_start_compact",
            manager,
        )

    assert calls == [
        "active",
        "sync_device",
        "sync_raw",
        "start",
        "start",
        "inactive",
    ]
    sync_device.assert_called_once_with(
        31337,
        timeout_ms=80,
        settle_seconds=0.0,
    )
    sync_raw.assert_called_once_with(
        31337,
        timeout_ms=80,
        settle_seconds=0.0,
    )
    assert send.call_args_list == [call(start, press_delay=0.15)] * 2
    sleep.assert_called_once_with(0.05)


def test_active_first_device_and_raw_factorial_overlaps_bounded_discovery() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    calls: list[str] = []
    rendezvous = threading.Barrier(2)

    def discover(label: str) -> bool:
        calls.append(label)
        rendezvous.wait(timeout=1.0)
        return True

    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_message,
            "notify_device_rescan_relevant_sync",
            side_effect=lambda *_args, **_kwargs: discover("sync_device"),
        ) as sync_device,
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_relevant_sync",
            side_effect=lambda *_args, **_kwargs: discover("sync_raw"),
        ) as sync_raw,
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=lambda _hwnd, active: calls.append(
                "active" if active else "inactive"
            ) or True,
        ),
        patch.object(
            gui,
            "send_gamepad_button",
            side_effect=lambda *_args, **_kwargs: calls.append("start") or True,
        ) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_spoof_device_raw_parallel_start_compact",
            manager,
        )

    assert calls[0] == "active"
    assert set(calls[1:3]) == {"sync_device", "sync_raw"}
    assert calls[3:] == ["start", "start", "inactive"]
    sync_device.assert_called_once_with(
        31337,
        timeout_ms=80,
        settle_seconds=0.0,
    )
    sync_raw.assert_called_once_with(
        31337,
        timeout_ms=80,
        settle_seconds=0.0,
    )
    assert send.call_args_list == [call(start, press_delay=0.15)] * 2
    sleep.assert_called_once_with(0.05)


def test_directinput_active_parallel_factorial_orders_activation_and_cleanup() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    calls: list[str] = []
    rendezvous = threading.Barrier(2)

    def discover(label: str) -> bool:
        calls.append(label)
        rendezvous.wait(timeout=1.0)
        return True

    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_message,
            "notify_device_rescan_relevant_sync",
            side_effect=lambda *_args, **_kwargs: discover("sync_device"),
        ) as sync_device,
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_relevant_sync",
            side_effect=lambda *_args, **_kwargs: discover("sync_raw"),
        ) as sync_raw,
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=lambda _hwnd, active: calls.append(
                "active" if active else "inactive"
            ) or True,
        ),
        patch.object(
            gui.input_message,
            "spoof_directinput_app_active",
            side_effect=lambda _hwnd, active, **_kwargs: calls.append(
                "di_active" if active else "di_inactive"
            ) or True,
        ) as diapp,
        patch.object(
            gui,
            "send_gamepad_button",
            side_effect=lambda *_args, **_kwargs: calls.append("start") or True,
        ) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_spoof_diapp_device_raw_parallel_start_compact",
            manager,
        )

    assert calls[:2] == ["active", "di_active"]
    assert set(calls[2:4]) == {"sync_device", "sync_raw"}
    assert calls[4:] == ["start", "start", "di_inactive", "inactive"]
    sync_device.assert_called_once_with(
        31337,
        timeout_ms=80,
        settle_seconds=0.0,
    )
    sync_raw.assert_called_once_with(
        31337,
        timeout_ms=80,
        settle_seconds=0.0,
    )
    assert diapp.call_args_list == [
        call(31337, True, settle_seconds=0.0),
        call(31337, False, settle_seconds=0.0),
    ]
    assert send.call_args_list == [call(start, press_delay=0.15)] * 2
    sleep.assert_called_once_with(0.05)


def test_parallel_discovery_triple_adds_one_distinct_start_edge() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    calls: list[str] = []
    rendezvous = threading.Barrier(2)

    def discover(label: str) -> bool:
        calls.append(label)
        rendezvous.wait(timeout=1.0)
        return True

    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_message,
            "notify_device_rescan_relevant_sync",
            side_effect=lambda *_args, **_kwargs: discover("sync_device"),
        ),
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_relevant_sync",
            side_effect=lambda *_args, **_kwargs: discover("sync_raw"),
        ),
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=lambda _hwnd, active: calls.append(
                "active" if active else "inactive"
            ) or True,
        ),
        patch.object(
            gui,
            "send_gamepad_button",
            side_effect=lambda *_args, **_kwargs: calls.append("start") or True,
        ) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_spoof_device_raw_parallel_start_compact3",
            manager,
        )

    assert calls[0] == "active"
    assert set(calls[1:3]) == {"sync_device", "sync_raw"}
    assert calls[3:] == ["start", "start", "start", "inactive"]
    assert send.call_args_list == [call(start, press_delay=0.15)] * 3
    assert sleep.call_args_list == [call(0.05), call(0.05)]


def test_parallel_rehandshake_repeats_discovery_and_compact_pair() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    calls: list[str] = []
    rendezvous = threading.Barrier(2)

    def discover(label: str) -> bool:
        calls.append(label)
        rendezvous.wait(timeout=1.0)
        return True

    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_message,
            "notify_device_rescan_relevant_sync",
            side_effect=lambda *_args, **_kwargs: discover("sync_device"),
        ) as sync_device,
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_relevant_sync",
            side_effect=lambda *_args, **_kwargs: discover("sync_raw"),
        ) as sync_raw,
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=lambda _hwnd, active: calls.append(
                "active" if active else "inactive"
            ) or True,
        ),
        patch.object(
            gui,
            "send_gamepad_button",
            side_effect=lambda *_args, **_kwargs: calls.append("start") or True,
        ) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_spoof_device_raw_parallel_start_rehandshake2",
            manager,
        )

    assert calls[0] == "active"
    assert set(calls[1:3]) == {"sync_device", "sync_raw"}
    assert calls[3:5] == ["start", "start"]
    assert set(calls[5:7]) == {"sync_device", "sync_raw"}
    assert calls[7:] == ["start", "start", "inactive"]
    assert sync_device.call_count == 2
    assert sync_raw.call_count == 2
    assert send.call_args_list == [call(start, press_delay=0.15)] * 4
    assert sleep.call_args_list == [call(0.05), call(0.05), call(0.05)]


def test_staggered_parallel_discovery_starts_device_before_raw() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    calls: list[str] = []
    rendezvous = threading.Barrier(2)

    def discover(label: str) -> bool:
        calls.append(label)
        rendezvous.wait(timeout=1.0)
        return True

    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_message,
            "notify_device_rescan_relevant_sync",
            side_effect=lambda *_args, **_kwargs: discover("sync_device"),
        ),
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_relevant_sync",
            side_effect=lambda *_args, **_kwargs: discover("sync_raw"),
        ),
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=lambda _hwnd, active: calls.append(
                "active" if active else "inactive"
            ) or True,
        ),
        patch.object(
            gui,
            "send_gamepad_button",
            side_effect=lambda *_args, **_kwargs: calls.append("start") or True,
        ),
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_spoof_device_raw_stagger30_start_compact",
            manager,
        )

    assert calls == [
        "active",
        "sync_device",
        "sync_raw",
        "start",
        "start",
        "inactive",
    ]
    assert sleep.call_args_list == [call(0.03), call(0.05)]


def test_parallel_discovery_neutral_reset_precedes_discovery() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    calls: list[str] = []
    rendezvous = threading.Barrier(2)

    def discover(label: str) -> bool:
        calls.append(label)
        rendezvous.wait(timeout=1.0)
        return True

    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_gamepad,
            "reset_virtual_gamepad",
            side_effect=lambda *_args, **_kwargs: calls.append("neutral") or True,
        ) as reset,
        patch.object(
            gui.input_message,
            "notify_device_rescan_relevant_sync",
            side_effect=lambda *_args, **_kwargs: discover("sync_device"),
        ),
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_relevant_sync",
            side_effect=lambda *_args, **_kwargs: discover("sync_raw"),
        ),
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=lambda _hwnd, active: calls.append(
                "active" if active else "inactive"
            ) or True,
        ),
        patch.object(
            gui,
            "send_gamepad_button",
            side_effect=lambda *_args, **_kwargs: calls.append("start") or True,
        ),
        patch.object(gui.time, "sleep"),
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_spoof_reset_device_raw_parallel_start_compact",
            manager,
        )

    assert calls[0:2] == ["active", "neutral"]
    assert set(calls[2:4]) == {"sync_device", "sync_raw"}
    assert calls[4:] == ["start", "start", "inactive"]
    reset.assert_called_once_with("xbox", settle_seconds=0.05)


def test_parallel_discovery_refreshed_pair_republishes_held_start() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    calls: list[str] = []
    rendezvous = threading.Barrier(2)

    def discover(label: str) -> bool:
        calls.append(label)
        rendezvous.wait(timeout=1.0)
        return True

    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_message,
            "notify_device_rescan_relevant_sync",
            side_effect=lambda *_args, **_kwargs: discover("sync_device"),
        ),
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_relevant_sync",
            side_effect=lambda *_args, **_kwargs: discover("sync_raw"),
        ),
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=lambda _hwnd, active: calls.append(
                "active" if active else "inactive"
            ) or True,
        ),
        patch.object(gui, "send_gamepad_button") as ordinary,
        patch.object(
            gui.input_gamepad,
            "send_gamepad_button_refreshed",
            side_effect=lambda *_args, **_kwargs: calls.append("refresh") or True,
        ) as refreshed,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_spoof_device_raw_parallel_start_refresh2",
            manager,
        )

    assert calls[0] == "active"
    assert set(calls[1:3]) == {"sync_device", "sync_raw"}
    assert calls[3:] == ["refresh", "refresh", "inactive"]
    ordinary.assert_not_called()
    assert refreshed.call_args_list == [call(start, press_delay=0.15)] * 2
    sleep.assert_called_once_with(0.05)


def test_start_wait300_a_allows_controller_mode_to_settle() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    confirm = object()
    with (
        patch.dict(
            gui.input_gamepad.KEY_TO_GAMEPAD,
            {"start": start, "a": confirm},
        ),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(gui.input_message, "notify_device_rescan_process", return_value=True),
        patch.object(gui.input_message, "spoof_window_active", return_value=True),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_wait300_a",
            manager,
        )

    assert send.call_args_list == [
        call(start, press_delay=0.15),
        call(confirm, press_delay=0.15),
    ]
    sleep.assert_called_once_with(0.30)


def test_directinput_app_active_compact_wraps_start_pair() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    calls: list[str] = []
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(gui.input_message, "notify_device_rescan_process", return_value=True),
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=lambda _hwnd, active: calls.append(
                "render_on" if active else "render_off"
            ) or True,
        ),
        patch.object(
            gui.input_message,
            "spoof_directinput_app_active",
            side_effect=lambda _hwnd, active: calls.append(
                "di_on" if active else "di_off"
            ) or True,
        ),
        patch.object(
            gui,
            "send_gamepad_button",
            side_effect=lambda *_args, **_kwargs: calls.append("start") or True,
        ) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_diapp_start_compact",
            manager,
        )

    assert calls == ["render_on", "di_on", "start", "start", "di_off", "render_off"]
    assert send.call_args_list == [call(start, press_delay=0.15)] * 2
    sleep.assert_called_once_with(0.05)


def test_synchronous_raw_arrival_compact_avoids_async_discovery() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_relevant_sync",
            return_value=True,
        ) as sync_raw,
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_process",
            side_effect=AssertionError("async Raw Input delivery must not run"),
        ),
        patch.object(gui.input_message, "spoof_window_active", return_value=True),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_raw_sync_spoof_start_compact",
            manager,
        )

    sync_raw.assert_called_once_with(
        31337,
        timeout_ms=80,
        settle_seconds=0.0,
    )
    assert send.call_args_list == [call(start, press_delay=0.15)] * 2
    sleep.assert_called_once_with(0.05)


@pytest.mark.parametrize(
    ("candidate", "expected_calls"),
    (
        ("process_raw_spoof_start_compact", ["raw"]),
        ("process_device_raw_spoof_start_compact", ["rescan", "raw"]),
    ),
)
def test_process_raw_compact_factorial_keeps_discovery_target_local(
    candidate: str,
    expected_calls: list[str],
) -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    calls: list[str] = []
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True) as ensure,
        patch.object(
            gui.input_message,
            "notify_device_rescan_process",
            side_effect=lambda *_args, **_kwargs: calls.append("rescan") or True,
        ),
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_process",
            side_effect=lambda *_args, **_kwargs: calls.append("raw") or True,
        ) as raw,
        patch.object(gui.input_message, "spoof_window_active", return_value=True) as spoof,
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(candidate, manager)

    ensure.assert_called_once_with("xbox")
    assert calls == expected_calls
    raw.assert_called_once_with(31337, settle_seconds=0.0)
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    assert send.call_args_list == [call(start, press_delay=0.15)] * 2
    sleep.assert_called_once_with(0.05)


def test_process_device_compact4_gap10_removes_only_idle_delivery_time() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_message,
            "notify_device_rescan_process",
            return_value=True,
        ),
        patch.object(gui.input_message, "spoof_window_active", return_value=True),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_compact4_gap10",
            manager,
        )

    assert send.call_args_list == [call(start, press_delay=0.15)] * 4
    assert sleep.call_args_list == [call(0.01)] * 3


@pytest.mark.parametrize(
    ("candidate", "holds", "gaps"),
    (
        ("process_device_spoof_start_refresh2", [0.15, 0.15], [0.05]),
        ("process_device_spoof_start_refresh650", [0.65], []),
    ),
)
def test_process_device_refreshed_reports_preserve_target_only_envelope(
    candidate: str,
    holds: list[float],
    gaps: list[float],
) -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(
            gui.input_message,
            "notify_device_rescan_process",
            return_value=True,
        ) as rescan,
        patch.object(
            gui.input_message,
            "spoof_window_active",
            return_value=True,
        ) as spoof,
        patch.object(
            gui.input_gamepad,
            "send_gamepad_button_refreshed",
            return_value=True,
        ) as refreshed,
        patch.object(gui, "send_gamepad_button") as ordinary,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(candidate, manager)

    rescan.assert_called_once_with(31337, settle_seconds=0.0)
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    assert refreshed.call_args_list == [
        call(start, press_delay=hold) for hold in holds
    ]
    ordinary.assert_not_called()
    assert sleep.call_args_list == [call(gap) for gap in gaps]


def test_process_device_compact3_adds_one_proven_edge() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(gui.input_message, "notify_device_rescan_process", return_value=True),
        patch.object(gui.input_message, "spoof_window_active", return_value=True),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_compact3",
            manager,
        )
    assert send.call_args_list == [call(start, press_delay=0.15)] * 3
    assert sleep.call_args_list == [call(0.05)] * 2


def test_process_device_compact4_hold130_preserves_distinct_gaps() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(gui.input_message, "notify_device_rescan_process", return_value=True),
        patch.object(gui.input_message, "spoof_window_active", return_value=True),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_compact4_hold130",
            manager,
        )
    assert send.call_args_list == [call(start, press_delay=0.13)] * 4
    assert sleep.call_args_list == [call(0.05)] * 3


@pytest.mark.parametrize(
    ("candidate", "secondary_name"),
    (
        ("process_device_spoof_start_a_combo2", "a"),
        ("process_device_spoof_start_b_combo2", "b"),
        ("process_device_spoof_start_back_combo2", "back"),
    ),
)
def test_process_device_start_combo_uses_two_simultaneous_reports(
    candidate: str,
    secondary_name: str,
) -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    secondary = object()
    with (
        patch.dict(
            gui.input_gamepad.KEY_TO_GAMEPAD,
            {"start": start, secondary_name: secondary},
        ),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True) as ensure,
        patch.object(gui.input_message, "notify_device_rescan_process", return_value=True) as rescan,
        patch.object(gui.input_message, "spoof_window_active", return_value=True) as spoof,
        patch.object(gui, "send_gamepad_buttons", return_value=True) as send_combo,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(candidate, manager)

    ensure.assert_called_once_with("xbox")
    rescan.assert_called_once_with(31337, settle_seconds=0.0)
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    assert send_combo.call_args_list == [
        call((start, secondary), press_delay=0.15),
        call((start, secondary), press_delay=0.15),
    ]
    sleep.assert_called_once_with(0.05)


@pytest.mark.parametrize(
    ("candidate", "secondary_name"),
    (
        ("process_device_spoof_start_then_a_pair", "a"),
        ("process_device_spoof_start_then_b_pair", "b"),
        ("process_device_spoof_start_then_back_pair", "back"),
    ),
)
def test_process_device_start_then_cancel_uses_distinct_reports(
    candidate: str,
    secondary_name: str,
) -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    secondary = object()
    with (
        patch.dict(
            gui.input_gamepad.KEY_TO_GAMEPAD,
            {"start": start, secondary_name: secondary},
        ),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(gui.input_message, "notify_device_rescan_process", return_value=True),
        patch.object(gui.input_message, "spoof_window_active", return_value=True),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(candidate, manager)

    assert send.call_args_list == [
        call(start, press_delay=0.15),
        call(secondary, press_delay=0.15),
        call(secondary, press_delay=0.15),
    ]
    assert sleep.call_args_list == [call(0.05), call(0.05)]


def test_process_device_a_then_start_then_a_uses_three_distinct_reports() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    confirm = object()
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start, "a": confirm}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(gui.input_message, "notify_device_rescan_process", return_value=True),
        patch.object(gui.input_message, "spoof_window_active", return_value=True),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_a_then_start_then_a",
            manager,
        )

    assert send.call_args_list == [
        call(confirm, press_delay=0.15),
        call(start, press_delay=0.15),
        call(confirm, press_delay=0.15),
    ]
    assert sleep.call_args_list == [call(0.05), call(0.05)]


def test_process_device_start_then_single_a_removes_redundant_confirm() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    confirm = object()
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start, "a": confirm}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(gui.input_message, "notify_device_rescan_process", return_value=True),
        patch.object(gui.input_message, "spoof_window_active", return_value=True),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_then_a_single",
            manager,
        )

    assert send.call_args_list == [
        call(start, press_delay=0.15),
        call(confirm, press_delay=0.15),
    ]
    sleep.assert_called_once_with(0.05)


def test_process_device_start_then_single_a_gap0_keeps_only_release_gap() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    confirm = object()
    with (
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start, "a": confirm}),
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(gui.input_message, "notify_device_rescan_process", return_value=True),
        patch.object(gui.input_message, "spoof_window_active", return_value=True),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_start_then_a_single_gap0",
            manager,
        )

    assert send.call_args_list == [
        call(start, press_delay=0.15),
        call(confirm, press_delay=0.15),
    ]
    sleep.assert_not_called()


def test_process_device_ds4_options_then_cross_uses_independent_protocol() -> None:
    manager = SimpleNamespace(hwnd=31337)
    with (
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True) as ensure,
        patch.object(gui.input_message, "notify_device_rescan_process", return_value=True) as rescan,
        patch.object(gui.input_message, "spoof_window_active", return_value=True) as spoof,
        patch.object(gui, "send_ds4_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_ds4_options_then_cross",
            manager,
        )

    ensure.assert_called_once_with("ds4")
    rescan.assert_called_once_with(31337, settle_seconds=0.0)
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    assert send.call_args_list == [
        call("options", press_delay=0.15),
        call("cross", press_delay=0.15),
    ]
    sleep.assert_called_once_with(0.05)


def test_process_device_ds4_options_then_cross_gap0_removes_only_idle_gap() -> None:
    manager = SimpleNamespace(hwnd=31337)
    with (
        patch.object(gui.input_gamepad, "ensure_virtual_gamepad", return_value=True),
        patch.object(gui.input_message, "notify_device_rescan_process", return_value=True),
        patch.object(gui.input_message, "spoof_window_active", return_value=True),
        patch.object(gui, "send_ds4_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_spoof_ds4_options_then_cross_gap0",
            manager,
        )

    assert send.call_args_list == [
        call("options", press_delay=0.15),
        call("cross", press_delay=0.15),
    ]
    sleep.assert_not_called()


@pytest.mark.parametrize(
    ("candidate", "message", "wparam", "lparam"),
    (
        ("process_appcommand_browser_back", 0x0319, 31337, 1 << 16),
        ("process_command_idcancel", 0x0111, 2, 0),
        ("process_cancelmode", 0x001F, 0, 0),
    ),
)
def test_process_semantic_commands_stay_inside_target_hwnd_tree(
    candidate: str,
    message: int,
    wparam: int,
    lparam: int,
) -> None:
    manager = SimpleNamespace(hwnd=31337)
    with patch.object(
        gui.input_message,
        "post_process_win32_message",
        return_value=True,
    ) as post:
        assert gui.AutomationApp._press_skip_candidate(candidate, manager)
    post.assert_called_once_with(31337, message, wparam, lparam)


@pytest.mark.parametrize(
    ("candidate", "message", "wparam", "lparam"),
    (
        ("process_notify_appcommand_browser_back", 0x0319, 31337, 1 << 16),
        ("process_notify_command_idcancel", 0x0111, 2, 0),
    ),
)
def test_process_notify_semantic_commands_stay_inside_target_hwnd_tree(
    candidate: str,
    message: int,
    wparam: int,
    lparam: int,
) -> None:
    manager = SimpleNamespace(hwnd=31337)
    with patch.object(
        gui.input_message,
        "notify_process_win32_message",
        return_value=True,
    ) as notify:
        assert gui.AutomationApp._press_skip_candidate(candidate, manager)
    notify.assert_called_once_with(31337, message, wparam, lparam)


def test_start_envelope_settle_sweep_keeps_only_target_local_activation() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    candidates = (
        "spoof_start_envelope2_settle150",
        "spoof_start_envelope2_settle250",
        "spoof_start_envelope2_settle350",
    )
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active",
            return_value=True,
        ) as spoof,
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        for candidate in candidates:
            assert gui.AutomationApp._press_skip_candidate(candidate, manager)

    assert spoof.call_args_list == [
        call(31337, True), call(31337, False),
        call(31337, True), call(31337, False),
        call(31337, True), call(31337, False),
    ]
    assert send.call_args_list == [call(start, press_delay=0.18)] * 6
    assert sleep.call_args_list == [
        call(0.10), call(0.15),
        call(0.10), call(0.25),
        call(0.10), call(0.35),
    ]


def test_highlight_context_uses_dark_central_panel_only() -> None:
    gameplay = np.full((1000, 1600), 160, dtype=np.uint8)
    highlight = gameplay.copy()
    highlight[120:860, 368:1232] = 20
    highlight[120:210, 368:1232] = 160

    assert not gui.AutomationApp._is_highlight_summary_context(gameplay)
    assert gui.AutomationApp._is_highlight_summary_context(highlight)
    assert not gui.AutomationApp._is_highlight_summary_context(
        np.zeros((20, 20), dtype=np.uint8)
    )
    assert not gui.AutomationApp._is_highlight_summary_context(
        np.zeros((1000, 1600), dtype=np.uint8)
    )


def test_start_preactivation_burst_waits_before_three_fast_pulses() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=[True, True],
        ) as spoof,
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_start_preactivate80_burst3",
            manager,
        )
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    assert send.call_args_list == [call(start, press_delay=0.08)] * 3
    assert sleep.call_args_list == [call(0.08), call(0.04), call(0.04)]


def test_s_controller_a_envelopes_use_a_inside_one_activation_window() -> None:
    manager = SimpleNamespace(hwnd=31337)
    button_a = object()
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=[True, True, True, True],
        ) as spoof,
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"a": button_a}),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_a_envelope2",
            manager,
        )
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_a_preactivate80_burst3",
            manager,
        )
    assert spoof.call_args_list == [
        call(31337, True), call(31337, False),
        call(31337, True), call(31337, False),
    ]
    assert send.call_args_list == [
        call(button_a, press_delay=0.18),
        call(button_a, press_delay=0.18),
        call(button_a, press_delay=0.08),
        call(button_a, press_delay=0.08),
        call(button_a, press_delay=0.08),
    ]
    assert sleep.call_args_list == [
        call(0.10), call(0.08), call(0.04), call(0.04),
    ]


def test_start_dense_envelope_emits_four_fast_pulses() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=[True, True],
        ) as spoof,
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_start_envelope4_fast",
            manager,
        )
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    assert send.call_args_list == [call(start, press_delay=0.08)] * 4
    assert sleep.call_args_list == [call(0.04), call(0.04), call(0.04)]


def test_ds4_rescan_candidates_notify_only_fc_before_button() -> None:
    manager = SimpleNamespace(hwnd=31337)
    with (
        patch.object(
            gui.input_message,
            "notify_device_rescan",
            return_value=True,
        ) as rescan,
        patch.object(gui, "send_ds4_button", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "device_ds4_cross",
            manager,
        )
    rescan.assert_called_once_with(31337)
    send.assert_called_once_with("cross", press_delay=0.15)


def test_process_window_key_candidates_remain_inside_fc_pid_route() -> None:
    manager = SimpleNamespace(hwnd=31337)
    with patch.object(
        gui.input_message,
        "post_key_process_windows",
        return_value=True,
    ) as post:
        assert gui.AutomationApp._press_skip_candidate(
            "process_sys_esc",
            manager,
        )
    post.assert_called_once_with(
        31337,
        gui.input_message.KEY_TO_VK["esc"],
        system_key=True,
        press_delay=0.15,
    )


def test_process_thread_spoof_candidate_restores_inactive_state() -> None:
    manager = SimpleNamespace(hwnd=31337)
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=[True, True],
        ) as spoof,
        patch.object(
            gui.input_message,
            "post_key_process_threads",
            return_value=True,
        ) as post,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_process_thread_sys_esc",
            manager,
        )
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    post.assert_called_once_with(
        31337,
        gui.input_message.KEY_TO_VK["esc"],
        system_key=True,
        press_delay=0.15,
    )


def test_process_thread_spoof_sequence_keeps_one_activation_envelope() -> None:
    manager = SimpleNamespace(hwnd=31337)
    with (
        patch.object(
            gui.input_message,
            "spoof_window_active",
            side_effect=[True, True],
        ) as spoof,
        patch.object(
            gui.input_message,
            "post_key_process_threads",
            return_value=True,
        ) as post,
        patch.object(gui.time, "sleep") as sleep,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "spoof_process_thread_sys_esc_envelope2",
            manager,
        )
    assert spoof.call_args_list == [call(31337, True), call(31337, False)]
    assert post.call_count == 2
    post.assert_has_calls(
        [
            call(
                31337,
                gui.input_message.KEY_TO_VK["esc"],
                system_key=True,
                press_delay=0.12,
            ),
            call(
                31337,
                gui.input_message.KEY_TO_VK["esc"],
                system_key=True,
                press_delay=0.12,
            ),
        ]
    )
    sleep.assert_called_once_with(0.06)


def test_process_device_candidate_rescans_fc_siblings_before_start() -> None:
    manager = SimpleNamespace(hwnd=31337)
    start = object()
    with (
        patch.object(
            gui.input_message,
            "notify_device_rescan_process",
            return_value=True,
        ) as rescan,
        patch.dict(gui.input_gamepad.KEY_TO_GAMEPAD, {"start": start}),
        patch.object(gui, "send_gamepad_button", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "process_device_start",
            manager,
        )
    rescan.assert_called_once_with(31337)
    send.assert_called_once_with(start, press_delay=0.15)


def test_raw_arrival_ds4_candidate_prepares_pad_before_target_notification() -> None:
    manager = SimpleNamespace(hwnd=31337)
    with (
        patch.object(
            gui.input_gamepad,
            "ensure_virtual_gamepad",
            return_value=True,
        ) as ensure,
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_process",
            return_value=True,
        ) as notify,
        patch.object(gui, "send_ds4_button", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "raw_device_ds4_options",
            manager,
        )
    ensure.assert_called_once_with("ds4")
    notify.assert_called_once_with(31337)
    send.assert_called_once_with("options", press_delay=0.15)


def test_raw_arrival_ds4_circle_uses_cancel_mapping() -> None:
    manager = SimpleNamespace(hwnd=31337)
    with (
        patch.object(
            gui.input_gamepad,
            "ensure_virtual_gamepad",
            return_value=True,
        ),
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_process",
            return_value=True,
        ),
        patch.object(gui, "send_ds4_button", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "raw_device_ds4_circle",
            manager,
        )
    send.assert_called_once_with("circle", press_delay=0.15)


def test_raw_arrival_ds4_share_uses_back_mapping() -> None:
    manager = SimpleNamespace(hwnd=31337)
    with (
        patch.object(
            gui.input_gamepad,
            "ensure_virtual_gamepad",
            return_value=True,
        ),
        patch.object(
            gui.input_message,
            "notify_raw_gamepad_arrival_process",
            return_value=True,
        ),
        patch.object(gui, "send_ds4_button", return_value=True) as send,
    ):
        assert gui.AutomationApp._press_skip_candidate(
            "raw_device_ds4_share",
            manager,
        )
    send.assert_called_once_with("share", press_delay=0.15)


def test_prompt_visual_evidence_is_small_and_content_addressed(tmp_path) -> None:
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app.base_dir = tmp_path
    app._skip_prompt_center = (300, 200)
    app._skip_prompt_visual = None
    frame = np.zeros((400, 640), dtype=np.uint8)
    frame[175:225, 260:440] = 220

    app._capture_skip_prompt_visual(frame)

    evidence = app._skip_prompt_visual
    assert evidence is not None
    assert len(evidence["sha256"]) == 64
    assert evidence["shape"] == [120, 340]
    evidence_path = tmp_path / "logs" / "skip_evidence" / evidence["file"]
    assert evidence_path.is_file()
    saved = gui.cv2.imread(str(evidence_path), gui.cv2.IMREAD_GRAYSCALE)
    assert saved.shape == (120, 340)


def test_s_template_requires_consecutive_frames() -> None:
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app._skip_s_match_streak = 0

    assert not app._confirmed_s_template_match(True)
    assert app._confirmed_s_template_match(True)
    assert not app._confirmed_s_template_match(False)
    assert app._skip_s_match_streak == 0


def test_prompt_visual_without_center_saves_lower_right_search_area(tmp_path) -> None:
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app.base_dir = tmp_path
    app._skip_prompt_center = None
    app._skip_prompt_visual = None
    frame = np.zeros((400, 640), dtype=np.uint8)
    frame[330:380, 500:620] = 220

    app._capture_skip_prompt_visual(frame)

    evidence = app._skip_prompt_visual
    assert evidence is not None
    assert evidence["shape"] == [100, 288]
    saved = gui.cv2.imread(
        str(tmp_path / "logs" / "skip_evidence" / evidence["file"]),
        gui.cv2.IMREAD_GRAYSCALE,
    )
    assert saved.shape == (100, 288)


def test_esc_icon_promotes_ambiguous_skip_text_before_tracker_lock() -> None:
    tracker = SimpleNamespace(
        episode_control_seconds=3.0,
        pending=None,
        choose=Mock(return_value=(None, None)),
    )
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app.base_dir = SimpleNamespace()
    app._skip_seen_since = None
    app._skip_text_streak = gui.SKIP_TEXT_CONSENSUS - 1
    app._skip_kind = None
    app._skip_prompt_variant = None
    app._skip_generic_hint = None
    app._skip_prompt_center = None
    app._skip_click_target = object()
    app._last_normal_action_at = float("-inf")
    app._skip_precontrol_contaminated = False
    app._skip_control_contaminated = False
    app._skip_a_dumped = True
    app._skip_diag_count = 20
    app._skip_active_until = 0.0
    app._skip_generic_experiment = tracker
    app._report_skip_experiment_outcome = Mock()
    app._reconcile_skip_learning = Mock()
    app.queue_status = Mock()
    app.queue_log = Mock()
    manager = SimpleNamespace(hwnd=123)

    with (
        patch.object(gui.winapi, "vg", object()),
        patch.object(gui.rank_ocr, "ocr_available", return_value=True),
        patch.object(gui.rank_ocr, "match_skip_a", return_value=(False, 0.1, None)),
        patch.object(gui.rank_ocr, "match_skip_s", return_value=(False, 0.1, None)),
        patch.object(
            gui.rank_ocr,
            "classify_skip_prompt",
            return_value=(True, None),
        ),
        patch.object(
            gui,
            "find_template_center",
            return_value=((100, 20), 0.91),
        ) as find_center,
        patch.object(gui, "send_gamepad_button") as gamepad,
    ):
        assert app._try_skip(np.zeros((720, 1280), dtype=np.uint8), manager)

    tracker.choose.assert_called_once()
    gamepad.assert_not_called()
    assert app._skip_kind == "start"
    assert app._skip_generic_hint == "escape"
    assert app._skip_prompt_center == (100, 581)
    find_center.assert_called_once()


def test_generic_episode_keeps_first_hint_when_later_ocr_hint_flickers() -> None:
    fallback = SimpleNamespace(
        episode_control_seconds=3.0,
        pending=None,
        choose=Mock(return_value=(None, None)),
    )
    escape = SimpleNamespace(
        episode_control_seconds=3.0,
        pending=None,
        choose=Mock(return_value=(None, None)),
    )
    any_key = SimpleNamespace(
        episode_control_seconds=3.0,
        pending=None,
        choose=Mock(return_value=(None, None)),
    )
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app.base_dir = SimpleNamespace()
    app._skip_seen_since = None
    app._skip_text_streak = gui.SKIP_TEXT_CONSENSUS - 1
    app._skip_kind = None
    app._skip_prompt_variant = None
    app._skip_generic_hint = None
    app._skip_generic_episode_hint = None
    app._skip_prompt_center = None
    app._last_normal_action_at = float("-inf")
    app._skip_precontrol_contaminated = False
    app._skip_control_contaminated = False
    app._skip_a_dumped = True
    app._skip_diag_count = 20
    app._skip_active_until = 0.0
    app._skip_generic_experiment = fallback
    app._skip_generic_escape_experiment = escape
    app._skip_generic_any_key_experiment = any_key
    app._report_skip_experiment_outcome = Mock()
    app._reconcile_skip_learning = Mock()
    app.queue_status = Mock()
    app.queue_log = Mock()
    manager = SimpleNamespace(hwnd=123)
    screen = np.zeros((720, 1280), dtype=np.uint8)

    with (
        patch.object(gui.winapi, "vg", object()),
        patch.object(gui.rank_ocr, "ocr_available", return_value=True),
        patch.object(gui.rank_ocr, "match_skip_a", return_value=(False, 0.1, None)),
        patch.object(gui.rank_ocr, "match_skip_s", return_value=(False, 0.1, None)),
        patch.object(
            gui.rank_ocr,
            "classify_skip_prompt",
            side_effect=[(True, "escape"), (True, "any_key")],
        ),
    ):
        assert app._try_skip(screen, manager)
        assert app._try_skip(screen, manager)

    assert app._skip_kind == "start"
    assert app._skip_generic_episode_hint == "escape"
    assert app._skip_generic_hint == "escape"
    assert escape.choose.call_count == 2
    any_key.choose.assert_not_called()
    fallback.choose.assert_not_called()


def test_provisional_escape_episode_ignores_later_a_template_false_positive() -> None:
    tracker = SimpleNamespace(
        episode_control_seconds=3.0,
        pending=None,
        choose=Mock(return_value=(None, None)),
    )
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app.base_dir = SimpleNamespace()
    app._skip_seen_since = 1.0
    app._skip_text_streak = gui.SKIP_TEXT_CONSENSUS
    app._skip_kind = None
    app._skip_prompt_variant = None
    app._skip_generic_hint = "escape"
    app._skip_prompt_center = None
    app._last_normal_action_at = float("-inf")
    app._skip_precontrol_contaminated = False
    app._skip_control_contaminated = False
    app._skip_a_dumped = True
    app._skip_diag_count = 20
    app._skip_active_until = 0.0
    app._skip_generic_experiment = tracker
    app._report_skip_experiment_outcome = Mock()
    app._reconcile_skip_learning = Mock()
    app.queue_status = Mock()
    app.queue_log = Mock()
    manager = SimpleNamespace(hwnd=123)

    with (
        patch.object(gui.winapi, "vg", object()),
        patch.object(gui.rank_ocr, "ocr_available", return_value=True),
        patch.object(gui.rank_ocr, "match_skip_a", return_value=(True, 0.99, (10, 10))) as match_a,
        patch.object(gui.rank_ocr, "match_skip_s", return_value=(False, 0.1, None)) as match_s,
        patch.object(
            gui.rank_ocr,
            "classify_skip_prompt",
            return_value=(True, "escape"),
        ),
        patch.object(gui, "send_gamepad_button") as gamepad,
    ):
        assert app._try_skip(np.zeros((720, 1280), dtype=np.uint8), manager)

    match_a.assert_not_called()
    match_s.assert_not_called()
    tracker.choose.assert_called_once()
    gamepad.assert_not_called()
    assert app._skip_kind == "start"
    assert app._skip_generic_hint == "escape"


def test_fast_skip_template_gate_prefers_a_s_over_normal_targets() -> None:
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app.base_dir = SimpleNamespace()
    screen = np.zeros((720, 1280), dtype=np.uint8)
    with (
        patch.object(gui.rank_ocr, "match_skip_a", return_value=(False, 0.1, None)) as match_a,
        patch.object(gui.rank_ocr, "match_skip_s", return_value=(True, 0.95, (20, 20))) as match_s,
    ):
        assert app._fast_skip_template_visible(screen)
    match_a.assert_called_once()
    match_s.assert_called_once()


def test_normal_escape_target_is_deferred_once_for_skip_ocr_probe() -> None:
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app._skip_esc_probe_at = float("-inf")
    app._skip_esc_probe_negative_streak = 0
    app._skip_esc_probe_allow_once = False
    app._skip_generic_hint = None
    app._skip_active_until = 0.0
    target = SimpleNamespace(action="key", key="ESC")

    assert app._defer_escape_target_for_skip_probe(target, 10.0)
    assert app._skip_generic_hint == "escape_probe"
    assert app._skip_active_until >= 10.8
    assert app._defer_escape_target_for_skip_probe(target, 10.9)


def test_positive_escape_hint_overrides_stale_one_shot_release() -> None:
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app._skip_esc_probe_at = 10.0
    app._skip_esc_probe_negative_streak = 0
    app._skip_esc_probe_allow_once = True
    app._skip_generic_hint = "escape"
    app._skip_active_until = 0.0
    target = SimpleNamespace(action="key", key="ESC")

    assert app._defer_escape_target_for_skip_probe(target, 10.9)
    assert not app._skip_esc_probe_allow_once
    assert app._skip_active_until >= 11.7


def test_strict_escape_probe_never_releases_ambiguous_normal_escape() -> None:
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app.base_dir = SimpleNamespace()
    app._skip_seen_since = None
    app._skip_text_streak = 0
    app._skip_kind = None
    app._skip_prompt_variant = None
    app._skip_generic_hint = "escape_probe"
    app._skip_prompt_center = None
    app._last_normal_action_at = float("-inf")
    app._skip_esc_probe_at = 10.0
    app._skip_esc_probe_negative_streak = 0
    app._skip_esc_probe_allow_once = False
    app._skip_precontrol_contaminated = False
    app._skip_control_contaminated = False
    app._skip_a_dumped = True
    app._skip_diag_count = 20
    app._skip_active_until = 0.0
    app._skip_experiment = SimpleNamespace(pending=None)
    app._skip_s_experiment = SimpleNamespace(pending=None)
    app._skip_generic_experiment = SimpleNamespace(pending=None)
    app.queue_log = Mock()
    manager = SimpleNamespace(hwnd=123)
    frame = np.zeros((720, 1280), dtype=np.uint8)

    with (
        patch.object(gui.winapi, "vg", object()),
        patch.object(gui.rank_ocr, "ocr_available", return_value=True),
        patch.object(gui.rank_ocr, "classify_skip_prompt", return_value=(False, None)),
    ):
        assert app._try_skip(frame, manager)
        assert app._skip_generic_hint == "escape_probe"
        assert app._try_skip(frame, manager) is False

    target = SimpleNamespace(action="key", key="ESC")
    # Even after OCR-negative consensus, strict mode never releases a normal
    # ESC because target_F is pixel-identical to the real ESC-SKIP prompt.
    assert app._defer_escape_target_for_skip_probe(target, 10.9)
    assert not app._skip_esc_probe_allow_once
    assert app._defer_escape_target_for_skip_probe(target, 11.6)
    assert app._defer_escape_target_for_skip_probe(target, 13.2)


def test_escape_probe_forces_ocr_before_a_template_matching() -> None:
    tracker = SimpleNamespace(
        episode_control_seconds=3.0,
        pending=None,
        choose=Mock(return_value=(None, None)),
    )
    app = gui.AutomationApp.__new__(gui.AutomationApp)
    app.base_dir = SimpleNamespace()
    app._skip_seen_since = None
    app._skip_text_streak = gui.SKIP_TEXT_CONSENSUS - 1
    app._skip_kind = None
    app._skip_prompt_variant = None
    app._skip_generic_hint = "escape_probe"
    app._skip_prompt_center = None
    app._last_normal_action_at = float("-inf")
    app._skip_esc_probe_negative_streak = 0
    app._skip_esc_probe_allow_once = False
    app._skip_precontrol_contaminated = False
    app._skip_control_contaminated = False
    app._skip_a_dumped = True
    app._skip_diag_count = 20
    app._skip_active_until = 0.0
    app._skip_generic_experiment = tracker
    app._report_skip_experiment_outcome = Mock()
    app._reconcile_skip_learning = Mock()
    app.queue_status = Mock()
    app.queue_log = Mock()
    manager = SimpleNamespace(hwnd=123)

    with (
        patch.object(gui.winapi, "vg", object()),
        patch.object(gui.rank_ocr, "ocr_available", return_value=True),
        patch.object(gui.rank_ocr, "match_skip_a", return_value=(True, 0.99, (10, 10))) as match_a,
        patch.object(gui.rank_ocr, "match_skip_s", return_value=(False, 0.1, None)) as match_s,
        patch.object(
            gui.rank_ocr,
            "classify_skip_prompt",
            return_value=(True, None),
        ),
    ):
        assert app._try_skip(np.zeros((720, 1280), dtype=np.uint8), manager)

    match_a.assert_not_called()
    match_s.assert_not_called()
    tracker.choose.assert_called_once()
    assert app._skip_kind == "start"
    assert app._skip_generic_hint == "escape"


def test_strict_skip_quiet_gate_covers_control_and_result_windows() -> None:
    expected = (
        gui.SKIP_EXPERIMENT_CONTROL_SECONDS
        + gui.SKIP_EXPERIMENT_RESULT_WINDOW_SECONDS
        + gui.SKIP_EXPERIMENT_EXIT_CONFIRM_SECONDS
        + gui.SKIP_OCR_INTERVAL_SECONDS
    )
    assert gui.AutomationApp._strict_skip_quiet_seconds() >= expected
