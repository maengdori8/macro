from __future__ import annotations

from pathlib import Path

from macroapp import app
from macroapp import gui


def test_consumes_one_shot_autostart_marker(monkeypatch, tmp_path: Path) -> None:
    marker = tmp_path / "Macro" / "autostart_experiment.once"
    marker.parent.mkdir()
    marker.write_text("1", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("MAUTO_AUTOSTART_EXPERIMENT", raising=False)
    monkeypatch.setattr(app.sys, "argv", ["macro.exe"])

    assert app.consume_autostart_experiment_request() is True
    assert not marker.exists()
    assert app.consume_autostart_experiment_request() is False


def test_autostart_can_be_requested_by_cli_or_environment(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(app.sys, "argv", ["macro.exe", "--autostart-experiment"])
    monkeypatch.delenv("MAUTO_AUTOSTART_EXPERIMENT", raising=False)
    assert app.consume_autostart_experiment_request() is True

    monkeypatch.setattr(app.sys, "argv", ["macro.exe"])
    monkeypatch.setenv("MAUTO_AUTOSTART_EXPERIMENT", "1")
    assert app.consume_autostart_experiment_request() is True


def test_background_experiment_requires_explicit_cli_switch(monkeypatch) -> None:
    monkeypatch.setattr(app.sys, "argv", ["macro.exe"])
    assert app.background_experiment_requested() is False

    monkeypatch.setattr(
        app.sys,
        "argv",
        ["macro.exe", "--autostart-experiment", "--background-experiment"],
    )
    assert app.background_experiment_requested() is True


def test_background_root_is_noactivate_and_restores_foreground(monkeypatch) -> None:
    calls: list[tuple] = []

    class FakeRoot:
        def withdraw(self) -> None:
            calls.append(("withdraw",))

        def update_idletasks(self) -> None:
            calls.append(("update_idletasks",))

        def winfo_id(self) -> int:
            return 101

    class FakeWin32Gui:
        @staticmethod
        def GetAncestor(hwnd: int, flag: int) -> int:
            calls.append(("ancestor", hwnd, flag))
            return 202

        @staticmethod
        def GetWindowLong(hwnd: int, index: int) -> int:
            calls.append(("get_style", hwnd, index))
            return 0x20

        @staticmethod
        def SetWindowLong(hwnd: int, index: int, style: int) -> None:
            calls.append(("set_style", hwnd, index, style))

        @staticmethod
        def IsWindow(hwnd: int) -> bool:
            return hwnd == 303

        @staticmethod
        def SetForegroundWindow(hwnd: int) -> None:
            calls.append(("restore", hwnd))

    monkeypatch.setattr(app, "win32gui", FakeWin32Gui)
    app.prepare_background_root(FakeRoot(), 303)

    assert ("set_style", 202, -20, 0x08000020) in calls
    assert calls[-1] == ("restore", 303)


def test_background_root_creation_keeps_hook_until_native_host_is_ready(
    monkeypatch,
) -> None:
    calls: list[tuple] = []
    fake_root = object()
    retained: list[tuple] = []

    monkeypatch.setattr(
        app,
        "_install_background_noactivate_hook",
        lambda foreground: (
            lambda: calls.append(("unhook", foreground)),
            object(),
        ),
    )
    monkeypatch.setattr(
        app,
        "_attach_background_creation_queue",
        lambda foreground: lambda: calls.append(("detach", foreground)),
    )
    monkeypatch.setattr(app, "_BACKGROUND_HOOK_KEEPALIVE", retained)
    monkeypatch.setattr(app.tk, "Tk", lambda: calls.append(("create",)) or fake_root)
    monkeypatch.setattr(
        app,
        "prepare_background_root",
        lambda root, foreground: calls.append(("prepare", root, foreground)),
    )

    assert app.create_background_root(777) is fake_root
    assert calls == [
        ("create",),
        ("prepare", fake_root, 777),
        ("detach", 777),
    ]
    assert len(retained) == 1


def test_final_background_tk_root_is_hardened_again(monkeypatch) -> None:
    calls: list[tuple] = []

    class FakeRoot:
        def withdraw(self) -> None:
            calls.append(("withdraw",))

        def update_idletasks(self) -> None:
            calls.append(("update_idletasks",))

        def winfo_id(self) -> int:
            return 401

    class FakeWin32Gui:
        @staticmethod
        def GetForegroundWindow() -> int:
            return 402

        @staticmethod
        def GetAncestor(hwnd: int, flag: int) -> int:
            calls.append(("ancestor", hwnd, flag))
            return 403

        @staticmethod
        def GetWindowLong(hwnd: int, index: int) -> int:
            return 0x100

        @staticmethod
        def SetWindowLong(hwnd: int, index: int, style: int) -> None:
            calls.append(("set_style", hwnd, index, style))

        @staticmethod
        def IsWindow(hwnd: int) -> bool:
            return hwnd == 402

        @staticmethod
        def SetForegroundWindow(hwnd: int) -> None:
            calls.append(("restore", hwnd))

    monkeypatch.setattr(gui.winapi, "win32gui", FakeWin32Gui)
    gui._harden_background_tk_root(FakeRoot())

    assert ("set_style", 403, -20, 0x08000100) in calls
    assert calls[-1] == ("restore", 402)
