from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

from macroapp import input_gamepad


def test_touchpad_click_uses_ds4_special_button_and_releases_it() -> None:
    touchpad = object()
    pad = Mock()
    fake_vg = SimpleNamespace(
        DS4_BUTTONS=SimpleNamespace(
            DS4_BUTTON_CROSS=object(),
            DS4_BUTTON_CIRCLE=object(),
            DS4_BUTTON_SHARE=object(),
            DS4_BUTTON_OPTIONS=object(),
        ),
        DS4_SPECIAL_BUTTONS=SimpleNamespace(
            DS4_SPECIAL_BUTTON_TOUCHPAD=touchpad,
        ),
    )

    with (
        patch.object(input_gamepad.winapi, "vg", fake_vg),
        patch.object(input_gamepad, "_get_ds4_gamepad", return_value=pad),
        patch.object(input_gamepad.time, "sleep"),
    ):
        assert input_gamepad.send_ds4_button("touchpad", press_delay=0.15)

    pad.press_special_button.assert_called_once_with(special_button=touchpad)
    pad.release_special_button.assert_called_once_with(special_button=touchpad)
    pad.press_button.assert_not_called()


def test_xbox_button_combo_is_one_report_and_releases_in_reverse_order() -> None:
    start = object()
    back = object()
    pad = Mock()

    with (
        patch.object(input_gamepad, "_get_gamepad", return_value=pad),
        patch.object(input_gamepad.time, "sleep") as sleep,
    ):
        assert input_gamepad.send_gamepad_buttons(
            (start, back, start),
            press_delay=0.15,
        )

    assert pad.press_button.call_args_list == [
        call(button=start),
        call(button=back),
    ]
    assert pad.release_button.call_args_list == [
        call(button=back),
        call(button=start),
    ]
    assert pad.update.call_count == 2
    assert sleep.call_args_list == [call(0.15), call(0.02)]


def test_xbox_button_combo_releases_buttons_after_update_failure() -> None:
    start = object()
    b_button = object()
    pad = Mock()
    pad.update.side_effect = [RuntimeError("report failed"), None]

    with (
        patch.object(input_gamepad, "_get_gamepad", return_value=pad),
        patch.object(input_gamepad.time, "sleep"),
        pytest.raises(RuntimeError, match="report failed"),
    ):
        input_gamepad.send_gamepad_buttons((start, b_button))

    assert pad.release_button.call_args_list == [
        call(button=b_button),
        call(button=start),
    ]


def test_xbox_refreshed_hold_republishes_state_and_releases() -> None:
    start = object()
    pad = Mock()

    with (
        patch.object(input_gamepad, "_get_gamepad", return_value=pad),
        patch.object(input_gamepad.time, "sleep") as sleep,
    ):
        assert input_gamepad.send_gamepad_button_refreshed(
            start,
            press_delay=0.10,
            refresh_interval=0.025,
        )

    pad.press_button.assert_called_once_with(button=start)
    pad.release_button.assert_called_once_with(button=start)
    assert pad.update.call_count == 5  # four held reports plus release
    assert sleep.call_args_list == [call(0.025)] * 4 + [call(0.02)]


def test_xbox_refreshed_hold_releases_after_report_failure() -> None:
    start = object()
    pad = Mock()
    pad.update.side_effect = [RuntimeError("report failed"), None]

    with (
        patch.object(input_gamepad, "_get_gamepad", return_value=pad),
        patch.object(input_gamepad.time, "sleep"),
        pytest.raises(RuntimeError, match="report failed"),
    ):
        input_gamepad.send_gamepad_button_refreshed(start)

    pad.release_button.assert_called_once_with(button=start)


def test_reset_virtual_gamepad_publishes_neutral_under_lock() -> None:
    pad = Mock()
    with (
        patch.object(input_gamepad, "_get_gamepad", return_value=pad),
        patch.object(input_gamepad.time, "sleep") as sleep,
    ):
        assert input_gamepad.reset_virtual_gamepad(
            "xbox",
            settle_seconds=0.05,
        )

    pad.reset.assert_called_once_with()
    pad.update.assert_called_once_with()
    sleep.assert_called_once_with(0.05)


def test_virtual_gamepad_status_reads_existing_slot_without_creating_pad() -> None:
    pad = Mock()
    pad.get_index.return_value = 1
    with patch.object(input_gamepad, "gamepad", pad):
        assert input_gamepad.virtual_gamepad_status() == {
            "connected": True,
            "index": 1,
        }
    with patch.object(input_gamepad, "gamepad", None):
        assert input_gamepad.virtual_gamepad_status() == {
            "connected": False,
            "index": None,
        }
