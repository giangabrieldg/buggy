from __future__ import annotations

from collections.abc import Callable
from tkinter import TclError
from typing import Any


class MotionScheduler:
    """Own cancelable visual timers on the Tk event loop."""

    def __init__(self, root: Any):
        self._root = root
        self._pending: set[str] = set()
        self._closed = False

    def schedule(self, delay_ms: int, callback: Callable, *args: Any) -> str | None:
        if self._closed:
            return None

        def run() -> None:
            self._pending.discard(timer_id)
            if not self._closed:
                callback(*args)

        try:
            timer_id = self._root.after(delay_ms, run)
        except (TclError, RuntimeError):
            return None
        self._pending.add(timer_id)
        return timer_id

    def cancel(self, timer_id: str | None) -> None:
        if timer_id not in self._pending:
            return
        self._pending.remove(timer_id)
        try:
            self._root.after_cancel(timer_id)
        except (TclError, RuntimeError):
            pass

    def cancel_all(self) -> None:
        for timer_id in tuple(self._pending):
            self.cancel(timer_id)

    def close(self) -> None:
        self._closed = True
        self.cancel_all()
