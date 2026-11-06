from __future__ import annotations

from pathlib import Path
from threading import Event

import personal_assistant.ui as assistant_ui
import personal_assistant.windows.tray as assistant_tray
from personal_assistant.ui import AssistantApp
from personal_assistant.windows.hotkey import GlobalHotkey
from personal_assistant.windows.tray import TrayController
from PIL import Image, ImageChops


class FakeWindow:
    def __init__(self):
        self.calls = []

    def withdraw(self):
        self.calls.append("withdraw")

    def deiconify(self):
        self.calls.append("deiconify")

    def lift(self):
        self.calls.append("lift")

    def focus_force(self):
        self.calls.append("focus_force")


class FakeIcon:
    def __init__(self):
        self.stop_calls = 0

    def stop(self):
        self.stop_calls += 1


def test_global_hotkey_registers_key_and_marshaled_activation():
    registration = {}
    scheduled = []
    activations = []

    def register(key, callback):
        registration["key"] = key
        registration["callback"] = callback
        return lambda: registration.update(unregistered=True)

    hotkey = GlobalHotkey(
        "Ctrl+Alt+A",
        on_activate=lambda: activations.append("shown"),
        register=register,
        schedule=scheduled.append,
    )

    assert registration["key"] == "Ctrl+Alt+A"
    assert hotkey.registered is True
    registration["callback"]()
    assert activations == []
    assert len(scheduled) == 1
    scheduled.pop()()
    assert activations == ["shown"]


def test_global_hotkey_registration_conflict_is_reported_without_raising():
    def conflict(key, callback):
        raise OSError("shortcut is already registered")

    hotkey = GlobalHotkey(
        "Ctrl+Alt+A",
        on_activate=lambda: None,
        register=conflict,
        schedule=lambda callback: callback(),
    )

    assert hotkey.registered is False
    assert "already registered" in hotkey.error_message.lower()


def test_global_hotkey_unregister_calls_registered_cleanup():
    unregister_calls = []
    hotkey = GlobalHotkey(
        "Ctrl+Alt+A",
        on_activate=lambda: None,
        register=lambda key, callback: unregister_calls.append("registered")
        or (lambda: unregister_calls.append("unregistered")),
        schedule=lambda callback: callback(),
    )

    hotkey.unregister()

    assert unregister_calls == ["registered", "unregistered"]


def test_tray_hide_withdraws_window():
    window = FakeWindow()
    tray = TrayController(window, on_exit=lambda: None, icon=FakeIcon())

    tray.hide()

    assert window.calls == ["withdraw"]


def test_tray_show_restores_and_focuses_existing_window():
    window = FakeWindow()
    tray = TrayController(window, on_exit=lambda: None, icon=FakeIcon())

    tray.show()

    assert window.calls == ["deiconify", "lift", "focus_force"]


def test_tray_show_uses_app_restore_callback_when_provided():
    window = FakeWindow()
    restored = []
    tray = TrayController(
        window,
        on_exit=lambda: None,
        on_show=lambda: restored.append("refreshed"),
        icon=FakeIcon(),
    )

    tray.show()

    assert restored == ["refreshed"]
    assert window.calls == []


def test_tray_exit_stops_icon_and_invokes_cleanup():
    window = FakeWindow()
    icon = FakeIcon()
    exit_calls = []
    tray = TrayController(window, on_exit=lambda: exit_calls.append("exit"), icon=icon)

    tray.exit()

    assert icon.stop_calls == 1
    assert exit_calls == ["exit"]
    assert window.calls == []


def test_tray_icon_uses_buggy_mascot_asset(monkeypatch):
    calls = []
    monkeypatch.setattr(
        assistant_tray.pystray,
        "Icon",
        lambda *args: calls.append(args) or object(),
    )

    TrayController.__new__(TrayController)._create_icon()

    assert calls[0][0] == "buggy-personal-assistant"
    assert calls[0][2] == "Buggy"
    mascot_path = (
        Path(__file__).parents[2]
        / "src"
        / "personal_assistant"
        / "assets"
        / "buggy-mascot.png"
    )
    with Image.open(mascot_path) as asset:
        expected = asset.convert("RGBA")
    expected.thumbnail((64, 64), Image.Resampling.LANCZOS)
    assert ImageChops.difference(calls[0][1], expected).getbbox() is None


def test_window_icon_applies_packaged_ico_path(monkeypatch):
    class FakeRoot:
        def __init__(self):
            self.bitmap_paths = []

        def iconbitmap(self, bitmap=None, default=None):
            self.bitmap_paths.append(bitmap if bitmap is not None else default)

    resolved_assets = []
    real_files = assistant_ui.files

    class RecordingPackage:
        def joinpath(self, *parts):
            resolved_assets.append(parts)
            return real_files("personal_assistant").joinpath(*parts)

    monkeypatch.setattr(assistant_ui, "files", lambda _package: RecordingPackage())

    app = AssistantApp.__new__(AssistantApp)
    app.root = FakeRoot()

    app._set_window_icon()

    expected_path = (
        Path(__file__).parents[2]
        / "src"
        / "personal_assistant"
        / "assets"
        / "buggy-window-icon.ico"
    )
    assert resolved_assets == [("assets", "buggy-window-icon.ico")]
    assert app.root.bitmap_paths == [str(expected_path)]


def test_window_icon_failure_is_ignored(monkeypatch):
    class FakeRoot:
        def iconphoto(self, *_args):
            raise AssertionError("iconphoto should not run without an image")

    def missing_asset(_package):
        raise OSError("asset missing")

    monkeypatch.setattr(assistant_ui, "files", missing_asset)
    app = AssistantApp.__new__(AssistantApp)
    app.root = FakeRoot()

    app._set_window_icon()

    assert app._window_icon is None


def test_window_icon_falls_back_to_iconphoto_if_iconbitmap_fails(monkeypatch):
    class FakeRoot:
        def __init__(self):
            self.iconphoto_calls = []

        def iconbitmap(self, bitmap):
            raise OSError(f"cannot apply {bitmap}")

        def iconphoto(self, default, photo):
            self.iconphoto_calls.append((default, photo))

    photo = object()
    monkeypatch.setattr(
        assistant_ui,
        "ImageTk",
        type("FakeImageTk", (), {"PhotoImage": staticmethod(lambda *_args, **_kwargs: photo)}),
        raising=False,
    )
    app = AssistantApp.__new__(AssistantApp)
    app.root = FakeRoot()

    app._set_window_icon()

    assert app.root.iconphoto_calls == [(True, photo)]
    assert app._window_icon is photo


def test_tray_icon_falls_back_if_mascot_asset_cannot_load(monkeypatch):
    calls = []

    def missing_asset(_package):
        raise OSError("asset missing")

    monkeypatch.setattr(assistant_tray, "files", missing_asset)
    monkeypatch.setattr(
        assistant_tray.pystray,
        "Icon",
        lambda *args: calls.append(args) or object(),
    )

    TrayController.__new__(TrayController)._create_icon()

    assert calls[0][1].size == (64, 64)
    assert calls[0][1].mode == "RGB"
    assert calls[0][1].getpixel((32, 32)) == (14, 116, 144)


def test_app_exit_restores_window_while_waiting_for_workers():
    class FakeWidget:
        def __init__(self):
            self.values = {}

        def configure(self, **kwargs):
            self.values.update(kwargs)

    class FakeRoot(FakeWindow):
        def after(self, _delay, callback):
            self.pending_callback = callback

        def destroy(self):
            self.calls.append("destroy")

    class FakeIndexer:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeSearchController:
        def __init__(self):
            self.worker_entered = Event()
            self.release_worker = Event()
            self.indexer = FakeIndexer()

        def stop(self):
            pass

        def wait_for_workers(self, timeout=None):
            self.worker_entered.set()
            self.release_worker.wait(timeout=2)
            return self.release_worker.is_set()

    app = AssistantApp.__new__(AssistantApp)
    app.root = FakeRoot()
    app.status_label = FakeWidget()
    app.send_button = FakeWidget()
    app.input_box = FakeWidget()
    app.search_controller = FakeSearchController()
    app.hotkey = None
    app._chat_worker = None
    app._shutdown_started = False
    app._shutdown_worker_done = Event()
    app._closed = False

    app.exit()

    assert app.search_controller.worker_entered.wait(timeout=1)
    assert app.root.calls[:2] == ["deiconify", "lift"]
    assert "Finishing background work" in app.status_label.values["text"]
    assert app.root.calls[-1] != "destroy"

    app.search_controller.release_worker.set()
    assert app._shutdown_worker_done.wait(timeout=1)
    app._poll_shutdown()
    assert app.search_controller.indexer.closed is True
    assert app.root.calls[-1] == "destroy"


def test_app_exit_timeout_destroys_window_without_closing_busy_indexer(monkeypatch):
    class FakeWidget:
        def __init__(self):
            self.values = {}

        def configure(self, **kwargs):
            self.values.update(kwargs)

    class FakeRoot(FakeWindow):
        def after(self, _delay, callback):
            self.pending_callback = callback

        def destroy(self):
            self.calls.append("destroy")

    class FakeIndexer:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class StuckSearchController:
        def __init__(self):
            self.indexer = FakeIndexer()
            self.wait_timeout = None

        def stop(self):
            pass

        def wait_for_workers(self, timeout=None):
            self.wait_timeout = timeout
            return False

    monkeypatch.setattr(assistant_ui, "SHUTDOWN_WAIT_TIMEOUT_SECONDS", 0.02)
    app = AssistantApp.__new__(AssistantApp)
    app.root = FakeRoot()
    app.status_label = FakeWidget()
    app.send_button = FakeWidget()
    app.input_box = FakeWidget()
    app.search_controller = StuckSearchController()
    app.hotkey = None
    app._chat_worker = None
    app._shutdown_started = False
    app._shutdown_worker_done = Event()
    app._shutdown_timed_out = False
    app._closed = False

    app.exit()
    assert app._shutdown_worker_done.wait(timeout=1)
    app._poll_shutdown()

    assert app.search_controller.wait_timeout is not None
    assert app.search_controller.indexer.closed is False
    assert "still running" in app.status_label.values["text"]
    assert app.root.calls[:2] == ["deiconify", "lift"]
    assert app.root.calls[-1] == "destroy"
