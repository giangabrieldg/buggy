from __future__ import annotations

import ctypes
import os
import threading
from typing import Callable


_MODIFIERS = {
    "ctrl": 0x0002,
    "control": 0x0002,
    "alt": 0x0001,
    "shift": 0x0004,
    "win": 0x0008,
    "windows": 0x0008,
}
_HOTKEY_ID = 0xA551
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_PM_NOREMOVE = 0x0000


class GlobalHotkey:
    """Register a Windows global shortcut and marshal activation to the UI."""

    def __init__(
        self,
        key: str,
        on_activate: Callable[[], None],
        register: Callable[[str, Callable[[], None]], Callable[[], None]] | None = None,
        schedule: Callable[[Callable[[], None]], object] | None = None,
    ):
        self.key = key
        self.on_activate = on_activate
        self._schedule = schedule
        self._unregister: Callable[[], None] | None = None
        self.registered = False
        self.error_message: str | None = None
        register_callback = register or _register_windows_hotkey
        try:
            self._unregister = register_callback(key, self._dispatch_activation)
            self.registered = True
        except Exception as error:
            self.error_message = f"Could not register {key}: {error}"

    def _dispatch_activation(self) -> None:
        try:
            if self._schedule is None:
                self.on_activate()
            else:
                self._schedule(self.on_activate)
        except Exception:
            # A closing Tk root can reject a callback queued by the listener thread.
            pass

    def unregister(self) -> None:
        cleanup = self._unregister
        self._unregister = None
        if cleanup is None:
            self.registered = False
            return
        try:
            cleanup()
        finally:
            self.registered = False


def _parse_shortcut(key: str) -> tuple[int, int]:
    parts = [part.strip().casefold() for part in key.split("+") if part.strip()]
    if not parts:
        raise ValueError("Shortcut is empty")

    modifiers = 0
    keys: list[str] = []
    for part in parts:
        if part in _MODIFIERS:
            modifiers |= _MODIFIERS[part]
        else:
            keys.append(part)
    if len(keys) != 1:
        raise ValueError(f"Shortcut must contain one key: {key}")

    key_name = keys[0]
    if len(key_name) == 1 and key_name.isascii() and key_name.isalnum():
        virtual_key = ord(key_name.upper())
    elif key_name.startswith("f") and key_name[1:].isdigit():
        function_number = int(key_name[1:])
        if not 1 <= function_number <= 24:
            raise ValueError(f"Unsupported shortcut key: {key_name}")
        virtual_key = 0x70 + function_number - 1
    else:
        raise ValueError(f"Unsupported shortcut key: {key_name}")
    if modifiers == 0:
        raise ValueError("A global shortcut must include at least one modifier")
    return modifiers, virtual_key


def _register_windows_hotkey(
    key: str,
    callback: Callable[[], None],
) -> Callable[[], None]:
    if os.name != "nt":
        raise OSError("Global shortcuts are available only while running on Windows.")

    modifiers, virtual_key = _parse_shortcut(key)
    ready = threading.Event()
    stop = threading.Event()
    state: dict[str, object] = {}

    def listen() -> None:
        from ctypes import wintypes

        registered = False
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD
            state["user32"] = user32
            state["thread_id"] = kernel32.GetCurrentThreadId()

            message = wintypes.MSG()
            # Ensure the thread message queue exists before the caller can post WM_QUIT.
            user32.PeekMessageW(ctypes.byref(message), None, 0, 0, _PM_NOREMOVE)
            if not user32.RegisterHotKey(None, _HOTKEY_ID, modifiers, virtual_key):
                raise ctypes.WinError(ctypes.get_last_error())
            registered = True
            ready.set()

            while not stop.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if message.message == _WM_HOTKEY and message.wParam == _HOTKEY_ID:
                    try:
                        callback()
                    except Exception:
                        pass
        except Exception as error:
            state["error"] = error
            ready.set()
        finally:
            if registered:
                try:
                    user32.UnregisterHotKey(None, _HOTKEY_ID)
                except Exception as error:
                    state.setdefault("error", error)

    thread = threading.Thread(target=listen, name="assistant-global-hotkey", daemon=True)
    thread.start()
    if not ready.wait(timeout=5):
        stop.set()
        raise TimeoutError("Windows did not respond while registering the shortcut.")
    if "error" in state:
        thread.join(timeout=1)
        raise state["error"]  # type: ignore[misc]

    def unregister() -> None:
        stop.set()
        user32 = state.get("user32")
        thread_id = state.get("thread_id")
        if user32 is not None and thread_id is not None:
            user32.PostThreadMessageW(int(thread_id), _WM_QUIT, 0, 0)
        thread.join(timeout=2)

    return unregister
