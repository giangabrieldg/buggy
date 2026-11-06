from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib
from typing import Any


@dataclass(frozen=True)
class AppEntry:
    name: str
    target: str


@dataclass(frozen=True)
class Settings:
    model: str
    app_data_dir: Path
    profile_root: Path
    global_shortcut: str
    allowed_apps: tuple[AppEntry, ...]
    reduce_motion: bool = False

    @property
    def database_path(self) -> Path:
        return self.app_data_dir / "assistant.db"


def _expand_path(raw: str) -> Path:
    expanded = os.path.expandvars(raw)
    return Path(expanded).expanduser()


def _default_app_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "PersonalAssistant"
    return Path.home() / "AppData" / "Local" / "PersonalAssistant"


def _required_string(data: dict[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Settings value {key!r} must be a non-empty string")
    return value.strip()


def load_settings(path: Path | None = None) -> Settings:
    settings_path = path or Path(__file__).resolve().parents[2] / "settings.toml"
    with settings_path.open("rb") as settings_file:
        data = tomllib.load(settings_file)

    model = _required_string(data, "model", "qwen3:1.7b")
    app_data_value = data.get("app_data_dir")
    if app_data_value is None:
        app_data_dir = _default_app_data_dir()
    elif isinstance(app_data_value, str) and app_data_value.strip():
        app_data_dir = _expand_path(app_data_value)
    else:
        raise ValueError("Settings value 'app_data_dir' must be a non-empty path")

    profile_value = data.get("profile_root", "~")
    if not isinstance(profile_value, str) or not profile_value.strip():
        raise ValueError("Settings value 'profile_root' must be a non-empty path")
    profile_root = _expand_path(profile_value)

    global_shortcut = _required_string(data, "global_shortcut", "Ctrl+Alt+A")
    reduce_motion = data.get("reduce_motion", False)
    if not isinstance(reduce_motion, bool):
        raise ValueError("Settings value 'reduce_motion' must be a boolean")
    raw_apps = data.get(
        "allowed_apps",
        [
            {"name": "Notepad", "target": "notepad.exe"},
            {"name": "Calculator", "target": "calc.exe"},
            {"name": "Word", "target": "winword.exe"},
        ],
    )
    if not isinstance(raw_apps, list):
        raise ValueError("Settings value 'allowed_apps' must be an array of tables")

    allowed_apps: list[AppEntry] = []
    for item in raw_apps:
        if not isinstance(item, dict):
            raise ValueError("Each allowed app must have a name and target")
        name = item.get("name")
        target = item.get("target")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Each allowed app needs a non-empty name")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("Each allowed app needs a non-empty target")
        allowed_apps.append(AppEntry(name.strip(), target.strip()))

    return Settings(
        model=model,
        app_data_dir=app_data_dir,
        profile_root=profile_root,
        global_shortcut=global_shortcut,
        allowed_apps=tuple(allowed_apps),
        reduce_motion=reduce_motion,
    )
