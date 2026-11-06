from __future__ import annotations

from importlib.resources import files
from threading import Thread
from typing import Callable

from PIL import Image, ImageDraw
import pystray


class TrayController:
    """Manage tray actions for hiding, restoring, and explicitly exiting the app."""

    def __init__(
        self,
        window,
        on_exit: Callable[[], None],
        icon=None,
        on_show: Callable[[], None] | None = None,
    ):
        self.window = window
        self.on_exit = on_exit
        self.on_show = on_show
        self.icon = icon if icon is not None else self._create_icon()
        self._exited = False
        if icon is None:
            self._start_icon()

    def _create_icon(self):
        try:
            mascot = files("personal_assistant").joinpath(
                "assets", "buggy-mascot.png"
            )
            with mascot.open("rb") as asset_stream, Image.open(asset_stream) as source:
                image = source.convert("RGBA")
            image.thumbnail((64, 64), Image.Resampling.LANCZOS)
        except Exception:
            image = Image.new("RGB", (64, 64), "#111827")
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((7, 7, 57, 57), radius=13, fill="#0e7490")
            draw.text((22, 17), "B", fill="#ffffff")
        menu = pystray.Menu(
            pystray.MenuItem("Show", self._menu_show, default=True),
            pystray.MenuItem("Exit", self._menu_exit),
        )
        return pystray.Icon("buggy-personal-assistant", image, "Buggy", menu)

    def _start_icon(self) -> None:
        run_detached = getattr(self.icon, "run_detached", None)
        if run_detached is not None:
            run_detached()
        else:
            Thread(target=self.icon.run, name="assistant-tray", daemon=True).start()

    def _schedule(self, callback: Callable[[], None]) -> None:
        after = getattr(self.window, "after", None)
        if after is None:
            callback()
            return
        try:
            after(0, callback)
        except Exception:
            pass

    def _menu_show(self, _icon=None, _item=None) -> None:
        self._schedule(self.show)

    def _menu_exit(self, _icon=None, _item=None) -> None:
        self._schedule(self.exit)

    def hide(self) -> None:
        self.window.withdraw()

    def show(self) -> None:
        if self.on_show is not None:
            self.on_show()
            return
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def exit(self) -> None:
        if self._exited:
            return
        self._exited = True
        self.icon.stop()
        self.on_exit()
