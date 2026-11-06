# Assistant Chat Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the first usable local assistant: compact desktop chat with Qwen3 conversation and persistent SQLite history.

**Architecture:** Keep the CustomTkinter view thin. A conversation controller stores user/assistant messages through a SQLite repository and calls an Ollama chat adapter configured for `qwen3:1.7b`; the app sends all model requests to the local Ollama service.

**Tech Stack:** Python 3.11+, CustomTkinter, Ollama Python client, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-11-05-personal-ai-assistant-design.md`

## Global Constraints

- Do not call paid or remote AI APIs; use the local Ollama service and `qwen3:1.7b`.
- Save conversation data in a user-writable application-data directory, not the source tree.
- Keep model calls off the Tkinter event loop and display service failures in the UI.
- The compact chat is the selected UI; voice and arbitrary PC control are out of scope.
- A Python 3.11+ interpreter is required. The current shell exposes only the Windows Store alias for `python`; do not install software until the user authorizes it.

## Review Focus

- Python is missing or older than 3.11: environment task must stop and request a usable interpreter before running tests.
- Ollama is stopped or the model tag is absent: chat reports a useful error and keeps history intact.
- SQLite is closed/reopened: stored messages retain conversation ID, role, order, and text.
- A model response is delayed or raises: the UI remains responsive and restores send controls.
- App restarts: the most recent conversation can be restored and a new one can be started.

---

### Task 1: Create the Python project and test environment

**Files:**
- Create: `pyproject.toml`
- Create: `settings.toml`
- Create: `src/personal_assistant/__init__.py`
- Create: `src/personal_assistant/config.py`
- Create: `tests/conftest.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Project package import path: `personal_assistant` from `src/`.
- Runtime dependencies: `customtkinter` and `ollama`; optional `dev` dependency group: `pytest`.
- Python requirement: `>=3.11` so the standard-library `tomllib` can load `settings.toml`.
- `Settings` contains model tag, app-data path, profile root, global shortcut, and `tuple[AppEntry, ...]` allowlist; `load_settings(path: Path | None = None) -> Settings` loads TOML and applies safe path defaults.

- [ ] **Step 1: Confirm an available interpreter**

Run: `python --version`

Expected: Python 3.11 or newer. If unavailable, stop and ask the user to install/provide an interpreter; do not install system software automatically.

- [ ] **Step 2: Create project metadata and the package skeleton** in `pyproject.toml` and `src/personal_assistant/__init__.py`; require Python `>=3.11`, declare runtime dependencies and a `dev` group containing `pytest`, and configure pytest's `src` layout.

- [ ] **Step 3: Create `.venv` and install the project dependencies**

Run:
- `python -m venv .venv`
- `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`

Expected: project and development dependencies install into `.venv`.

- [ ] **Step 4: Write failing settings tests** for the `qwen3:1.7b` default, `%LOCALAPPDATA%` database location, `Path.home()` search root, `Ctrl+Alt+A`, and the three confirmed allowlisted apps with targets `notepad.exe`, `calc.exe`, and `winword.exe`.

- [ ] **Step 5: Run the settings tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_config.py -q`

Expected: FAIL because `load_settings` is not implemented.

- [ ] **Step 6: Implement `Settings`, `AppEntry`, `load_settings`, and the default `settings.toml`** with the approved model, shortcut, and app targets.

- [ ] **Step 7: Run the settings tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_config.py -q`

Expected: all settings tests pass, and paths can be overridden with temporary values in tests.

### Task 2: Implement persistent conversation history

**Files:**
- Create: `src/personal_assistant/history.py`
- Test: `tests/unit/test_history.py`

**Interfaces:**
- `HistoryRepository(db_path: Path)` creates the schema when opened.
- `create_conversation(title: str | None = None) -> str` returns a stable conversation ID.
- `add_message(conversation_id: str, role: str, content: str) -> None` accepts only `user` and `assistant` roles.
- `list_recent_conversations(limit: int = 20) -> list[ConversationSummary]` returns newest first.
- `list_messages(conversation_id: str) -> list[ChatMessage]` returns messages oldest first.

- [ ] **Step 1: Write failing tests** for create/list conversations, ordered user/assistant messages, persistence after repository reopen, and rejecting an invalid role.
- [ ] **Step 2: Run the history tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_history.py -q`

Expected: FAIL because `HistoryRepository` and its data types are not implemented.

- [ ] **Step 3: Implement the SQLite repository** in `history.py`, using parameterized queries and a schema version table.
- [ ] **Step 4: Re-run the history tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_history.py -q`

Expected: all history tests pass.

### Task 3: Add the local Ollama chat service and controller

**Files:**
- Create: `src/personal_assistant/chat.py`
- Test: `tests/unit/test_chat.py`

**Interfaces:**
- `OllamaChatService(model: str = "qwen3:1.7b", client: ChatClient | None = None)` encapsulates local model calls.
- `reply(messages: Sequence[ChatMessage]) -> str` prepends a friendly, casual system prompt, maps conversation messages to the Ollama client, and returns assistant text.
- `ConversationController(history: HistoryRepository, chat: OllamaChatService)` exposes `submit(conversation_id: str, text: str) -> str`, loads that conversation's prior turns, and persists both user and assistant messages.

- [ ] **Step 1: Write failing tests** for system-prompt inclusion, mapping prior conversation turns to a fake client, persisting both sides of a turn, and converting Ollama-unavailable/model-missing errors into a user-readable domain error.
- [ ] **Step 2: Run the chat tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_chat.py -q`

Expected: FAIL because the service/controller are not implemented.

- [ ] **Step 3: Implement the Ollama adapter and controller** with client injection for deterministic tests and a local endpoint only.
- [ ] **Step 4: Run chat and history tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_chat.py tests/unit/test_history.py -q`

Expected: all tests pass without contacting an external service.

### Task 4: Build the compact CustomTkinter chat window

**Files:**
- Create: `src/personal_assistant/ui.py`
- Create: `src/personal_assistant/main.py`
- Test: `tests/unit/test_controller.py`

**Interfaces:**
- `AssistantApp(root: CTk, controller: ConversationController, history: HistoryRepository)` renders the compact chat and dispatches UI events.
- `main()` creates settings, repository, service, controller, and window, then runs the Tk main loop.
- UI callbacks schedule model work off the Tk main thread and marshal responses back through `root.after`.

- [ ] **Step 1: Write failing controller/UI-boundary tests** for blank input, successful submit, saved history restoration, and model error display.
- [ ] **Step 2: Run the focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_controller.py -q`

Expected: FAIL until controller/UI integration exists.

- [ ] **Step 3: Implement the compact chat screen** with conversation history selection, message list, input, Send, New chat, Find files, and Open app controls; tool buttons may show a clear “not available yet” state until their plans are implemented.
- [ ] **Step 4: Run the full core test suite and manually launch the window**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: all core tests pass; manual launch shows the compact chat and handles a local Ollama turn without freezing the window.
