from __future__ import annotations

from pathlib import Path

import pytest

from personal_assistant.config import AppEntry, load_settings
from personal_assistant.windows.apps import AppLauncher, open_selected_file


@pytest.fixture
def synthetic_file(request):
    path = (
        Path.cwd()
        / ".tmp"
        / f"test-windows-apps-{request.node.name} with spaces.txt"
    )
    path.unlink(missing_ok=True)
    path.write_text("synthetic test file", encoding="utf-8")
    request.addfinalizer(lambda: path.unlink(missing_ok=True))
    return path


@pytest.fixture
def fake_target(request):
    path = Path.cwd() / ".tmp" / f"test-windows-apps-{request.node.name}.exe"
    path.unlink(missing_ok=True)
    path.write_bytes(b"synthetic executable placeholder")
    request.addfinalizer(lambda: path.unlink(missing_ok=True))
    return path


def test_project_settings_define_the_approved_app_allowlist():
    settings = load_settings()

    assert [(entry.name, entry.target) for entry in settings.allowed_apps] == [
        ("Notepad", "notepad.exe"),
        ("Calculator", "calc.exe"),
        ("Word", "winword.exe"),
    ]


def test_allowed_app_launch_uses_argv_shell_false_and_case_insensitive_name(
    fake_target,
):
    calls = []

    def process_runner(args, **kwargs):
        calls.append((args, kwargs))

    launcher = AppLauncher(
        [AppEntry("Notepad", str(fake_target))],
        process_runner=process_runner,
    )

    result = launcher.launch("nOtEpAd")

    assert result.success is True
    assert calls == [([str(fake_target)], {"shell": False})]


def test_unknown_app_name_does_not_invoke_process_runner(fake_target):
    calls = []
    launcher = AppLauncher(
        [AppEntry("Notepad", str(fake_target))],
        process_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = launcher.launch("PowerShell")

    assert result.success is False
    assert result.message
    assert "not" in result.message.lower()
    assert calls == []


def test_missing_app_target_returns_a_useful_failure_without_launching(request):
    missing_target = (
        Path.cwd()
        / ".tmp"
        / f"test-windows-apps-missing-{request.node.name}.exe"
    )
    missing_target.unlink(missing_ok=True)
    calls = []
    launcher = AppLauncher(
        [AppEntry("Editor", str(missing_target))],
        process_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = launcher.launch("Editor")

    assert result.success is False
    assert result.message
    assert "editor" in result.message.lower() or str(missing_target).lower() in (
        result.message.lower()
    )
    assert calls == []


def test_word_target_resolves_from_a_standard_office_install_location(
    request, monkeypatch
):
    office_root = Path.cwd() / ".tmp" / f"office-install-{request.node.name}"
    word_path = office_root / "Microsoft Office" / "Office15" / "WINWORD.EXE"
    word_path.parent.mkdir(parents=True, exist_ok=True)
    word_path.write_bytes(b"synthetic Word executable")
    calls = []
    monkeypatch.setenv("ProgramFiles", str(office_root))
    monkeypatch.setattr("personal_assistant.windows.apps.shutil.which", lambda _: None)
    launcher = AppLauncher(
        [AppEntry("Word", "winword.exe")],
        process_runner=lambda args, **kwargs: calls.append((args, kwargs)),
    )

    result = launcher.launch("Word")

    assert result.success is True
    assert calls == [([str(word_path)], {"shell": False})]
    word_path.unlink(missing_ok=True)
    word_path.parent.rmdir()
    word_path.parent.parent.rmdir()
    office_root.rmdir()


def test_broken_process_target_returns_a_useful_failure(fake_target):
    def broken_runner(args, **kwargs):
        raise OSError("synthetic launch failure")

    launcher = AppLauncher(
        [AppEntry("Editor", str(fake_target))],
        process_runner=broken_runner,
    )

    result = launcher.launch("Editor")

    assert result.success is False
    assert "editor" in result.message.lower()
    assert "launch" in result.message.lower() or "start" in result.message.lower()


def test_open_selected_file_forwards_exact_path_with_spaces(synthetic_file):
    opened_paths = []

    open_selected_file(synthetic_file, opener=opened_paths.append)

    assert opened_paths == [synthetic_file]
    assert opened_paths[0] is synthetic_file


def test_open_selected_file_rejects_a_nonexistent_path(request):
    missing_path = (
        Path.cwd()
        / ".tmp"
        / f"test-windows-apps-missing-{request.node.name} with spaces.txt"
    )
    missing_path.unlink(missing_ok=True)
    opened_paths = []

    with pytest.raises(FileNotFoundError):
        open_selected_file(missing_path, opener=opened_paths.append)

    assert opened_paths == []
