from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

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


class _FakeThreadUser32:
    def __init__(self) -> None:
        self.messages = []

    @staticmethod
    def MapVirtualKeyW(vk_code, _mode):
        return int(vk_code)

    @staticmethod
    def GetWindowThreadProcessId(_hwnd, _process_id):
        return 77

    def PostThreadMessageW(self, thread_id, message, wparam, lparam):
        self.messages.append(
            (
                int(getattr(thread_id, "value", thread_id)),
                int(message),
                int(getattr(wparam, "value", wparam)),
                int(getattr(lparam, "value", lparam)),
            )
        )
        return 1


class _FakeCallbackUser32:
    def __init__(self, result: int = 1) -> None:
        self.result = result
        self.messages = []

    @staticmethod
    def MapVirtualKeyW(vk_code, _mode):
        return int(vk_code)

    def SendMessageCallbackW(
        self,
        hwnd,
        message,
        wparam,
        lparam,
        callback,
        data,
    ):
        self.messages.append(
            (
                int(hwnd.value or 0),
                int(message),
                int(wparam.value),
                int(lparam.value),
                callback,
                int(data.value),
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


class ThreadQueueKeyboardMessageTests(unittest.TestCase):
    def test_posts_escape_char_only_to_target_ui_thread_queue(self) -> None:
        user32 = _FakeThreadUser32()
        fake_windll = SimpleNamespace(user32=user32)
        with (
            patch.object(input_message.ctypes, "windll", fake_windll, create=True),
            patch.object(input_message.winapi, "win32gui", object()),
            patch.object(input_message, "_setup_user32_sigs"),
            patch.object(input_message.time, "sleep"),
        ):
            result = input_message.post_key_thread(
                123,
                input_message.KEY_TO_VK["esc"],
                char_code=0x1B,
                press_delay=0.25,
            )

        self.assertTrue(result)
        self.assertTrue(all(message[0] == 77 for message in user32.messages))
        self.assertEqual(
            [message[1] for message in user32.messages],
            [
                input_message.WM_KEYDOWN,
                input_message.WM_CHAR,
                input_message.WM_KEYUP,
            ],
        )


class CallbackKeyboardMessageTests(unittest.TestCase):
    def test_delivers_only_to_target_tree_without_focus_or_global_input(self) -> None:
        user32 = _FakeCallbackUser32()
        fake_windll = SimpleNamespace(user32=user32)
        with (
            patch.object(input_message.ctypes, "windll", fake_windll, create=True),
            patch.object(input_message.winapi, "win32gui", object()),
            patch.object(input_message, "_setup_user32_sigs"),
            patch.object(input_message, "_get_thread_focus_hwnd", return_value=0),
            patch.object(input_message, "_get_child_windows", return_value=[456]),
            patch.object(input_message.time, "sleep"),
        ):
            result = input_message.send_key_deep_callback(
                123,
                input_message.KEY_TO_VK["esc"],
                press_delay=0.25,
            )

        self.assertTrue(result)
        self.assertEqual(
            [(message[0], message[1]) for message in user32.messages],
            [
                (123, input_message.WM_KEYDOWN),
                (456, input_message.WM_KEYDOWN),
                (123, input_message.WM_KEYUP),
                (456, input_message.WM_KEYUP),
            ],
        )


class ProcessSemanticMessageTests(unittest.TestCase):
    def test_sync_raw_arrival_uses_valid_handles_and_relevant_windows_only(self) -> None:
        user32 = _FakeUser32()
        user32.GetForegroundWindow = lambda: 999
        fake_windll = SimpleNamespace(user32=user32)
        classes = {101: "FIFAKC", 202: "DIEmWin", 303: "IME"}
        gui = SimpleNamespace(GetClassName=lambda hwnd: classes[int(hwnd)])
        with (
            patch.object(input_message.ctypes, "windll", fake_windll, create=True),
            patch.object(input_message.winapi, "win32gui", gui),
            patch.object(input_message, "_setup_user32_sigs"),
            patch.object(
                input_message,
                "_get_process_windows",
                return_value=[101, 202, 303],
            ),
            patch.object(
                input_message,
                "_raw_gamepad_device_handles",
                return_value=[444, 555],
            ),
            patch.object(input_message.time, "sleep") as sleep,
        ):
            result = input_message.notify_raw_gamepad_arrival_relevant_sync(
                101,
                timeout_ms=70,
                settle_seconds=0.02,
            )

        self.assertTrue(result)
        self.assertEqual(
            [(message[0], message[3]) for message in user32.messages],
            [(101, 444), (101, 555), (202, 444), (202, 555)],
        )
        self.assertTrue(
            all(
                message[1] == input_message.WM_INPUT_DEVICE_CHANGE
                for message in user32.messages
            )
        )
        self.assertTrue(
            all(message[2] == input_message.GIDC_ARRIVAL for message in user32.messages)
        )
        self.assertTrue(all(message[5] == 70 for message in user32.messages))
        sleep.assert_called_once_with(0.02)

    def test_directinput_app_activation_targets_only_process_diemwin(self) -> None:
        user32 = _FakeUser32()
        user32.GetForegroundWindow = lambda: 999
        user32.GetWindowThreadProcessId = lambda _hwnd, _pid: 777
        fake_windll = SimpleNamespace(user32=user32)
        classes = {101: "FIFAKC", 202: "DIEmWin", 303: "IME"}
        gui = SimpleNamespace(GetClassName=lambda hwnd: classes[int(hwnd)])
        with (
            patch.object(input_message.ctypes, "windll", fake_windll, create=True),
            patch.object(input_message.winapi, "win32gui", gui),
            patch.object(input_message, "_setup_user32_sigs"),
            patch.object(
                input_message,
                "_get_process_windows",
                return_value=[101, 202, 303],
            ),
            patch.object(input_message.time, "sleep") as sleep,
        ):
            activated = input_message.spoof_directinput_app_active(101, True)
            deactivated = input_message.spoof_directinput_app_active(101, False)

        self.assertTrue(activated)
        self.assertTrue(deactivated)
        self.assertEqual([message[0] for message in user32.messages], [202, 202])
        self.assertEqual([message[1] for message in user32.messages], [0x001C, 0x001C])
        self.assertEqual([message[2] for message in user32.messages], [1, 0])
        self.assertEqual([message[3] for message in user32.messages], [777, 777])
        self.assertEqual(sleep.call_args_list, [call(0.03), call(0.03)])

    def test_synchronous_rescan_targets_only_render_and_directinput_windows(self) -> None:
        user32 = _FakeUser32()
        fake_windll = SimpleNamespace(user32=user32)
        classes = {101: "FIFAKC", 202: "DIEmWin", 303: "IME"}
        gui = SimpleNamespace(GetClassName=lambda hwnd: classes[int(hwnd)])
        with (
            patch.object(input_message.ctypes, "windll", fake_windll, create=True),
            patch.object(input_message.winapi, "win32gui", gui),
            patch.object(input_message, "_setup_user32_sigs"),
            patch.object(
                input_message,
                "_get_process_windows",
                return_value=[101, 202, 303],
            ),
            patch.object(input_message.time, "sleep") as sleep,
        ):
            result = input_message.notify_device_rescan_relevant_sync(
                101,
                timeout_ms=75,
                settle_seconds=0.02,
            )

        self.assertTrue(result)
        self.assertEqual([message[0] for message in user32.messages], [101, 202])
        self.assertTrue(
            all(message[1] == input_message.WM_DEVICECHANGE for message in user32.messages)
        )
        self.assertTrue(
            all(message[2] == input_message.DBT_DEVNODES_CHANGED for message in user32.messages)
        )
        self.assertTrue(all(message[5] == 75 for message in user32.messages))
        sleep.assert_called_once_with(0.02)

    def test_posts_only_to_windows_owned_by_target_process(self) -> None:
        gui = SimpleNamespace(PostMessage=Mock())
        with (
            patch.object(input_message.winapi, "win32gui", gui),
            patch.object(
                input_message,
                "_get_process_windows",
                return_value=[101, 202],
            ),
        ):
            result = input_message.post_process_win32_message(
                31337,
                0x0319,
                31337,
                1 << 16,
            )

        self.assertTrue(result)
        self.assertEqual(
            gui.PostMessage.call_args_list,
            [
                call(101, 0x0319, 31337, 1 << 16),
                call(202, 0x0319, 31337, 1 << 16),
            ],
        )

    def test_notify_path_stays_inside_target_process_windows(self) -> None:
        notify = Mock(return_value=1)
        user32 = SimpleNamespace(SendNotifyMessageW=notify)
        with (
            patch.object(
                input_message.ctypes,
                "windll",
                SimpleNamespace(user32=user32),
                create=True,
            ),
            patch.object(input_message.winapi, "win32gui", object()),
            patch.object(input_message, "_setup_user32_sigs"),
            patch.object(
                input_message,
                "_get_process_windows",
                return_value=[101, 202],
            ),
        ):
            result = input_message.notify_process_win32_message(
                31337,
                0x0319,
                31337,
                1 << 16,
            )

        self.assertTrue(result)
        self.assertEqual(notify.call_count, 2)
        self.assertEqual(
            [int(item.args[0].value) for item in notify.call_args_list],
            [101, 202],
        )


if __name__ == "__main__":
    unittest.main()
