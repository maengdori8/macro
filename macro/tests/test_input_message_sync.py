from types import SimpleNamespace
import unittest
from unittest.mock import patch

from macroapp import input_message


class _FakeUser32:
    def __init__(self, result: int = 1) -> None:
        self.result = result
        self.messages = []

    @staticmethod
    def MapVirtualKeyW(vk_code, _mode):
        return int(vk_code)

    def SendMessageTimeoutW(
        self,
        hwnd,
        message,
        wparam,
        lparam,
        flags,
        timeout_ms,
        result,
    ):
        self.messages.append(
            (
                int(hwnd.value or 0),
                int(message),
                int(wparam.value),
                int(lparam.value),
                int(flags),
                int(timeout_ms),
            )
        )
        return self.result


class SynchronousKeyboardMessageTests(unittest.TestCase):
    def test_delivers_down_char_and_up_without_focus_api(self) -> None:
        user32 = _FakeUser32()
        fake_windll = SimpleNamespace(user32=user32)
        with (
            patch.object(input_message.ctypes, "windll", fake_windll, create=True),
            patch.object(input_message.winapi, "win32gui", object()),
            patch.object(input_message, "_setup_user32_sigs"),
            patch.object(input_message, "_get_thread_focus_hwnd", return_value=0),
            patch.object(input_message, "_get_child_windows", return_value=[]),
            patch.object(input_message.time, "sleep"),
        ):
            result = input_message.send_key_deep_sync(
                123,
                input_message.KEY_TO_VK["s"],
                char_code=ord("s"),
                press_delay=0.25,
            )

        self.assertTrue(result)
        self.assertEqual(
            [message[1] for message in user32.messages],
            [
                input_message.WM_KEYDOWN,
                input_message.WM_CHAR,
                input_message.WM_KEYUP,
            ],
        )
        self.assertTrue(all(message[0] == 123 for message in user32.messages))

    def test_reports_failure_when_target_does_not_accept_messages(self) -> None:
        user32 = _FakeUser32(result=0)
        fake_windll = SimpleNamespace(user32=user32)
        with (
            patch.object(input_message.ctypes, "windll", fake_windll, create=True),
            patch.object(input_message.winapi, "win32gui", object()),
            patch.object(input_message, "_setup_user32_sigs"),
            patch.object(input_message, "_get_thread_focus_hwnd", return_value=0),
            patch.object(input_message, "_get_child_windows", return_value=[]),
            patch.object(input_message.time, "sleep"),
        ):
            result = input_message.send_key_deep_sync(
                123,
                input_message.KEY_TO_VK["s"],
            )

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
