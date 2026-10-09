"""S 탭의 전송 실패·전면 변경·예외 경로를 실제 입력 없이 검증한다."""

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from macroapp import input_message


HWND = 123


@pytest.fixture
def delivery(monkeypatch):
    # 모든 OS 입력은 모의 객체다. 테스트 실행으로 키가 전송되지 않는다.
    monkeypatch.setattr(input_message.ctypes, "windll", SimpleNamespace(), raising=False)
    send = Mock(return_value=True)
    foreground = Mock(return_value=HWND)
    monotonic = Mock(side_effect=[0.0, 0.0, 0.005, 0.01])
    sleep = Mock()
    monkeypatch.setattr(input_message, "_send_scancode", send)
    monkeypatch.setattr(input_message, "get_foreground_hwnd", foreground)
    monkeypatch.setattr(input_message.time, "monotonic", monotonic)
    monkeypatch.setattr(input_message.time, "sleep", sleep)
    return SimpleNamespace(send=send, foreground=foreground, sleep=sleep)


def test_s_tap_reports_success_only_after_both_edges(delivery):
    assert input_message.send_scancode_to_foreground_window(
        HWND, input_message.SCAN_S, hold=0.01
    )
    assert delivery.send.call_args_list == [
        call(input_message.SCAN_S, keyup=False),
        call(input_message.SCAN_S, keyup=True),
    ]
    assert delivery.foreground.call_count == 4


def test_inactive_target_receives_no_input(delivery):
    delivery.foreground.return_value = 456
    assert not input_message.send_scancode_to_foreground_window(HWND, input_message.SCAN_S)
    delivery.send.assert_not_called()


def test_rejected_key_down_is_not_success(delivery):
    delivery.send.return_value = False
    assert not input_message.send_scancode_to_foreground_window(HWND, input_message.SCAN_S)
    delivery.send.assert_called_once_with(input_message.SCAN_S, keyup=False)


def test_rejected_key_up_is_not_success(delivery):
    delivery.send.side_effect = [True, False]
    assert not input_message.send_scancode_to_foreground_window(
        HWND, input_message.SCAN_S, hold=0.01
    )
    assert delivery.send.call_count == 2


def test_focus_loss_during_tap_still_releases_key(delivery):
    delivery.foreground.side_effect = [HWND, 456, 456]
    assert not input_message.send_scancode_to_foreground_window(
        HWND, input_message.SCAN_S, hold=0.01
    )
    assert delivery.send.call_args_list[-1] == call(input_message.SCAN_S, keyup=True)
    delivery.sleep.assert_not_called()


def test_zero_duration_tap_checks_focus_again_before_release(delivery):
    delivery.foreground.side_effect = [HWND, 456]
    assert not input_message.send_scancode_to_foreground_window(
        HWND, input_message.SCAN_S, hold=0.0
    )
    assert delivery.send.call_args_list[-1] == call(input_message.SCAN_S, keyup=True)


def test_focus_loss_after_final_poll_is_not_success(delivery):
    delivery.foreground.side_effect = [HWND, HWND, HWND, 456]
    assert not input_message.send_scancode_to_foreground_window(
        HWND, input_message.SCAN_S, hold=0.01
    )
    assert delivery.send.call_args_list[-1] == call(input_message.SCAN_S, keyup=True)


def test_wait_exception_still_releases_key(delivery):
    delivery.sleep.side_effect = RuntimeError("wait interrupted")
    with pytest.raises(RuntimeError, match="wait interrupted"):
        input_message.send_scancode_to_foreground_window(HWND, input_message.SCAN_S)
    assert delivery.send.call_args_list[-1] == call(input_message.SCAN_S, keyup=True)
