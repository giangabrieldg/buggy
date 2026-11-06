# Windows Assistant Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the assistant available from a Windows-wide shortcut, support hide/restore/Exit behavior, launch the approved apps, and open only the document path the user selected.

**Architecture:** Put OS operations behind small Windows adapters. Register `Ctrl+Alt+A` with the Windows hotkey API while the app process runs, use a tray icon for Show/Exit, and launch only configured allowlist entries. Send selected file paths to Windows' default handler; do not use model output as a command line.

**Tech Stack:** Python 3.11+, Windows APIs through `ctypes`, CustomTkinter, `pystray` plus an icon image, pytest with mocked OS boundaries.

**Spec:** `docs/superpowers/specs/2026-11-05-personal-ai-assistant-design.md`

## Global Constraints

- Hotkey `Ctrl+Alt+A` is active after the user manually starts the app; do not register Windows sign-in autostart in the MVP.
- Closing the main window hides it to the notification area; tray **Show** restores it and tray **Exit** unregisters the hotkey and stops the app.
- Initial allowlist is Notepad, Calculator, and Word; app targets come from settings and are never constructed from untrusted chat text.
- Open only the exact document path selected by the user; do not launch arbitrary shell commands.
- Unit tests mock Windows calls and never launch real apps or open real user files.

## Review Focus

- Hotkey is already registered by another process: surface a useful conflict state and continue without crashing.
- Shortcut fires while another app is foreground: restore/focus the existing assistant window without creating a second instance.
- User closes the window: app stays resident; explicit tray Exit fully unregisters and exits.
- App is missing or its configured target is invalid: report the unavailable app and keep the assistant running.
- Selected result is a long path or contains spaces: pass the exact path safely to the default Windows file handler.

---

### Task 1: Add safe app launching and selected-file opening

**Files:**
- Create: `src/personal_assistant/windows/__init__.py`
- Create: `src/personal_assistant/windows/apps.py`
- Modify: `src/personal_assistant/config.py`
- Test: `tests/unit/test_windows_apps.py`

**Interfaces:**
- `AppEntry(name: str, target: str)` is loaded from the local settings file; defaults are Notepad, Calculator, and Word.
- `AppLauncher(entries: Sequence[AppEntry], process_runner=...)` exposes `launch(name: str) -> LaunchResult`; only exact allowlisted names resolve.
- `open_selected_file(path: Path, opener=...) -> None` sends one selected path to the Windows default handler and rejects nonexistent paths.
- No API accepts a shell command string or uses `shell=True`.

- [ ] **Step 1: Write failing tests** for allowed names, unknown names, invalid/missing targets, paths with spaces, and exact selected-file handoff.
- [ ] **Step 2: Run Windows adapter unit tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_windows_apps.py -q`

Expected: FAIL because the adapter is absent.

- [ ] **Step 3: Implement the allowlist loader and Windows launch/open adapters** with injectable process/open functions for tests.
- [ ] **Step 4: Re-run adapter tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_windows_apps.py -q`

Expected: all tests pass without starting an application.

### Task 2: Implement global shortcut and tray lifecycle

**Files:**
- Modify: `pyproject.toml`
- Create: `src/personal_assistant/windows/hotkey.py`
- Create: `src/personal_assistant/windows/tray.py`
- Modify: `src/personal_assistant/main.py`
- Test: `tests/unit/test_windows_lifecycle.py`

**Interfaces:**
- `GlobalHotkey(key: str, on_activate: Callable[[], None], register=...)` registers `Ctrl+Alt+A`, marshals activation to the Tk thread, and exposes `unregister() -> None`.
- `TrayController(window, on_exit)` exposes Show and Exit actions; window close calls hide, not quit.
- App startup creates one assistant instance, registers the hotkey, and reports registration conflicts in the UI/log.
- Explicit Exit cancels background search, unregisters the hotkey, closes the tray icon, and closes database connections before process shutdown.
- Tray runtime dependencies are `pystray` and `Pillow`; global hotkey registration uses `ctypes` rather than a separate key-hook package.

- [ ] **Step 1: Add `pystray` and `Pillow` to project runtime dependencies** in `pyproject.toml`.
- [ ] **Step 2: Install updated project dependencies**

Run: `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`

Expected: tray dependencies install into `.venv`.

- [ ] **Step 3: Write failing tests** for hotkey registration/unregistration, registration conflict, hide on close, Show restoring the window, and Exit cleanup.
- [ ] **Step 4: Run lifecycle unit tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_windows_lifecycle.py -q`

Expected: FAIL because hotkey/tray lifecycle is absent.

- [ ] **Step 5: Implement Windows hotkey registration and tray lifecycle** with injected registration functions for tests; do not add sign-in startup.
- [ ] **Step 6: Re-run lifecycle tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_windows_lifecycle.py -q`

Expected: all lifecycle tests pass with mocked platform calls.

### Task 3: Wire actions into the chat UI and complete Windows QA

**Files:**
- Create: `src/personal_assistant/commands.py`
- Modify: `src/personal_assistant/ui.py`
- Modify: `src/personal_assistant/main.py`
- Test: `tests/unit/test_command_router.py`
- Test: `tests/integration/test_assistant_actions.py`

**Interfaces:**
- `CommandRouter.route(text: str) -> ActionIntent | None` recognizes `find <query>` / `search <query>` and `open <allowlisted app>` / `launch <allowlisted app>`; unrecognized or ambiguous text returns `None` for ordinary chat.
- The Open app action and recognized `open <allowlisted name>` requests call `AppLauncher.launch`.
- Recognized find/search requests call `SearchController` with the extracted query.
- Selecting a search result calls `open_selected_file` with that result's exact `Path`.
- Unknown app requests return a friendly message and do not call Windows APIs.

- [ ] **Step 1: Write failing router and integration tests** for find/search parsing, allowed/unknown app requests, ambiguous ordinary chat, result selection and exact path forwarding, and repeat hotkey activation focusing a single window.
- [ ] **Step 2: Run router and integration tests with mocked Windows adapters**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_command_router.py tests/integration/test_assistant_actions.py -q`

Expected: FAIL until the UI is wired to the adapters.

- [ ] **Step 3: Connect deterministic action handlers to the compact chat**; keep ordinary conversational messages on the Ollama chat path.
- [ ] **Step 4: Run the complete automated suite and perform manual Windows smoke checks** for shortcut from another app, hide/restore/Exit, allowed app launch, unknown app response, and opening a selected temporary test file.

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: all automated tests pass; manual checks confirm one window, a live hotkey while hidden, explicit Exit cleanup, and user selection before file opening.
