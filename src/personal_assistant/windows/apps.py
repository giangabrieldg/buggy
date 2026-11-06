from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Callable, Sequence

from personal_assistant.config import AppEntry


@dataclass(frozen=True)
class LaunchResult:
    success: bool
    message: str


class AppLauncher:
    """Launch only entries explicitly present in the configured allowlist."""

    def __init__(
        self,
        entries: Sequence[AppEntry],
        process_runner: Callable[..., object] = subprocess.Popen,
    ):
        self._entries = tuple(entries)
        self._process_runner = process_runner

    def launch(self, name: str) -> LaunchResult:
        requested_name = name.strip().casefold() if isinstance(name, str) else ""
        entry = next(
            (item for item in self._entries if item.name.casefold() == requested_name),
            None,
        )
        if entry is None:
            return LaunchResult(False, f"'{name}' is not in the approved app list.")

        target = self._resolve_target(entry.target)
        if target is None:
            return LaunchResult(
                False,
                f"{entry.name} is unavailable because its app target could not be found.",
            )

        try:
            self._process_runner([target], shell=False)
        except Exception as error:
            return LaunchResult(
                False,
                f"Could not launch {entry.name}: {error}",
            )
        return LaunchResult(True, f"Opening {entry.name}.")

    @staticmethod
    def _resolve_target(target: str) -> str | None:
        target = target.strip()
        if not target:
            return None

        candidate = Path(target).expanduser()
        if candidate.is_absolute() or "/" in target or "\\" in target:
            if candidate.is_file():
                return str(candidate)
            return None

        resolved = shutil.which(target)
        if resolved is not None:
            return resolved

        resolved = AppLauncher._resolve_from_app_paths(target)
        if resolved is not None:
            return resolved
        if Path(target).name.casefold() == "winword.exe":
            return AppLauncher._resolve_word_install()
        return None

    @staticmethod
    def _resolve_word_install() -> str | None:
        program_roots = [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("ProgramW6432"),
        ]
        office_dirs = (
            Path("Microsoft Office") / "root" / "Office16",
            Path("Microsoft Office") / "Office16",
            Path("Microsoft Office") / "Office15",
            Path("Microsoft Office") / "Office14",
        )
        for program_root in dict.fromkeys(root for root in program_roots if root):
            for office_dir in office_dirs:
                candidate = Path(program_root) / office_dir / "WINWORD.EXE"
                if candidate.is_file():
                    return str(candidate)
        return None

    @staticmethod
    def _resolve_from_app_paths(target: str) -> str | None:
        if sys.platform != "win32":
            return None
        try:
            import winreg
        except ImportError:
            return None

        key_path = (
            "SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\"
            + Path(target).name
        )
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    app_path, _ = winreg.QueryValueEx(key, None)
            except OSError:
                continue
            if isinstance(app_path, str):
                candidate = Path(app_path.strip().strip('"'))
                if candidate.is_file():
                    return str(candidate)
        return None


def _open_with_windows_default_handler(path: Path) -> None:
    startfile = getattr(os, "startfile", None)
    if startfile is None:
        raise OSError("The Windows default file handler is unavailable on this system.")
    startfile(path)


def open_selected_file(
    path: Path,
    opener: Callable[[Path], object] | None = None,
) -> None:
    """Open the exact selected regular file using the Windows default handler."""
    if not isinstance(path, Path):
        raise TypeError("Selected file path must be a pathlib.Path")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Selected file does not exist or is not a file: {path}")

    (opener or _open_with_windows_default_handler)(path)
