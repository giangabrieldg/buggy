from pathlib import Path

from personal_assistant.config import load_settings


def test_loads_approved_defaults_from_project_settings(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", "C:/test-local-app-data")

    settings = load_settings()

    assert settings.model == "qwen3:1.7b"
    assert settings.app_data_dir == Path("C:/test-local-app-data/PersonalAssistant")
    assert settings.database_path == Path(
        "C:/test-local-app-data/PersonalAssistant/assistant.db"
    )
    assert settings.profile_root == Path.home()
    assert settings.global_shortcut == "Ctrl+Alt+A"
    assert [(app.name, app.target) for app in settings.allowed_apps] == [
        ("Notepad", "notepad.exe"),
        ("Calculator", "calc.exe"),
        ("Word", "winword.exe"),
    ]


def test_default_app_data_path_falls_back_when_localappdata_is_missing(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    settings = load_settings()

    assert settings.app_data_dir == Path.home() / "AppData" / "Local" / "PersonalAssistant"


def test_load_settings_supports_path_and_app_overrides(monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", "C:/test-override-data")
    monkeypatch.setenv("TEST_PROFILE_ROOT", "C:/test-profile")
    fixture_path = Path(__file__).parents[1] / "fixtures" / "override_settings.toml"

    settings = load_settings(fixture_path)

    assert settings.app_data_dir == Path("C:/test-override-data/AssistantTestData")
    assert settings.profile_root == Path("C:/test-profile")
    assert settings.global_shortcut == "Alt+Shift+P"
    assert [(app.name, app.target) for app in settings.allowed_apps] == [
        ("Editor", "editor.exe")
    ]


def test_reduce_motion_defaults_off_and_can_be_enabled(monkeypatch):
    from io import BytesIO

    original_open = Path.open
    def fake_open(path, *args, **kwargs):
        if path.name == "default-motion.toml":
            return BytesIO(b'model = "qwen3:1.7b"\n')
        if path.name == "enabled-motion.toml":
            return BytesIO(b'reduce_motion = true\n')
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fake_open)

    assert load_settings(Path("default-motion.toml")).reduce_motion is False
    assert load_settings(Path("enabled-motion.toml")).reduce_motion is True


def test_reduce_motion_rejects_non_boolean_values(monkeypatch):
    from io import BytesIO
    import pytest

    monkeypatch.setattr(Path, "open", lambda _path, *args, **kwargs: BytesIO(b'reduce_motion = "yes"\n'))

    with pytest.raises(ValueError, match="reduce_motion"):
        load_settings(Path("invalid-motion.toml"))
