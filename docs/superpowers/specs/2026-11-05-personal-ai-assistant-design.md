# Personal AI Assistant MVP Design

## Intent

Build a small, local Windows assistant that feels like a chat companion and can handle a few useful laptop tasks. The user wants to talk with it when bored, search for personal documents, and open selected Windows apps without sending conversations or document contents to paid APIs.

## Goals

- Run conversation locally through Ollama using the installed `qwen3:1.7b` model.
- Provide a compact CustomTkinter chat window that can be opened or brought forward with a Windows-wide keyboard shortcut.
- Save basic conversation history in SQLite and restore recent conversation state on later launches.
- Search document names and contents throughout the current Windows user's profile using a local SQLite index built in the background.
- Show matching file paths in the chat; open only the exact result the user selects.
- Open only explicitly allowlisted Windows applications.
- Keep the app usable while the model or index is working, and explain missing-model, unavailable-Ollama, indexing, permission, and no-result states clearly.

## Non-goals

- No online or paid AI API, web search, cloud sync, or sending local data off the laptop.
- No voice input/output, broad autonomous PC control, arbitrary shell commands, file edits, or file deletion in the MVP.
- No model-directed execution of operating-system actions; the model's natural-language response is separate from deterministic file/app handlers.
- No automatic launch at Windows sign-in in the MVP. The global shortcut is active after the user starts the assistant; closing its window hides it while it runs in the background, and tray **Exit** stops it.
- PyAutoGUI-based screen/mouse control is reserved for a later PC-control milestone. Opening a chosen file/app and registering a global hotkey use their direct Windows mechanisms.

## Approaches

- **Compact desktop chat with deterministic local actions (selected):** start with CustomTkinter, Ollama for conversation, and explicit Python handlers for document search and app launching. This follows the user's choice of a small desktop interface and keeps the model from directly triggering arbitrary PC actions.
- **Terminal first, desktop later:** smaller initial UI effort, but conflicts with the user's preference for a compact desktop assistant and clickable search results.
- **Model-directed tool agent:** more flexible natural-language action selection, but gives a small model responsibility for triggering local actions and adds reliability/safety work beyond the MVP.

## User experience

- A compact single-window chat is the primary screen. It has a conversation area, text entry, send action, and small **Find files** and **Open app** controls.
- Search matches appear as readable, clickable path cards inline in the conversation. The assistant never opens a result until the user selects it; the selected path is passed to Windows' default file handler.
- App launch uses a short allowlisted app menu and recognized requests such as “open Notepad.” Unknown or unconfigured app requests receive a clear response without shell execution.
- The initial app allowlist is Notepad, Calculator, and Word. Keep the list in a small project settings file so it can be changed without modifying the command-handling logic.
- The global shortcut is `Ctrl+Alt+A`. It raises and focuses the existing assistant window, or restores it from the background. Report a conflict if Windows cannot register the shortcut.
- Closing the window hides it to the notification area while leaving the hotkey active. A tray menu offers **Show** and **Exit**. The app is started manually in the MVP, so the shortcut is available after the app has been launched once.
- Conversation uses a friendly, casual default tone suited to open-ended chat. History is local; no cross-conversation personal memory is added beyond saved conversation records.

## Search and local data

- Recursively search the current user's full profile, including ordinary and hidden subfolders. Skip directory links/junctions to avoid loops and skip files that Windows denies access to; record non-sensitive error counts for display/logging rather than stopping the scan.
- Search document filenames and contents for `.txt`, `.md`, `.pdf`, and `.docx`. Content extraction is limited to these formats; other extensions are not returned as document matches in the MVP.
- Build the first local index in the background. Store file paths, supported extracted text, and modification metadata in SQLite; use SQLite FTS5 for filename/content queries. Update changed files incrementally on later starts and provide an explicit re-index action. Search can return current indexed results while a refresh continues.
- Keep chat history and the document index in an app-data SQLite database. Store conversation IDs, roles, message text, timestamps, and file metadata/text needed for search. Provide a way to start a new conversation and restore recent saved conversations without adding a large navigation sidebar.
- Extract `.txt` and `.md` with Python's standard library, `.pdf` with `pypdf`, and `.docx` with `python-docx`. Treat unreadable/corrupt files as per-file skips and continue the index job.

## Technical constraints and decisions

- Platform: Windows laptop; user reported 32 GB RAM and Intel UHD shared graphics.
- Language/runtime: Python. The current shell does not expose a Python interpreter: `python` resolves to the Microsoft Store alias and `py` is absent. Locating or installing a Python 3.11+ interpreter is an environment prerequisite before app execution and automated checks.
- Model/runtime: Ollama with the already-installed `qwen3:1.7b`; runtime requests go only to the local Ollama service. No internet is required after model setup.
- UI: CustomTkinter. Keep model responses and background indexing off the UI thread.
- Storage/search: SQLite with FTS5. Use a stable, user-writable application-data location, not the source tree.
- Windows integration: use a global hotkey registration through the Windows API (for example `RegisterHotKey` via `ctypes`), Windows' default file handler for selected results, and an explicit app allowlist. Use a tray library only if needed for hide/restore/exit.
- The user approved Notepad, Calculator, and Word as the initial app allowlist; `Ctrl+Alt+A` as the shortcut; and `.txt`, `.md`, `.pdf`, and `.docx` as searchable content formats.

## Testing and QA

- Unit tests cover message persistence, command recognition, filename/content search, supported-format extraction, index refresh, result selection, and app allowlisting.
- Integration tests use a temporary SQLite database, synthetic documents in temporary folders, a mocked Ollama boundary, and mocked Windows launch/open functions. Tests must never index the user's real profile or launch real apps.
- Windows manual checks cover the global shortcut from another app, hide/restore/Exit behavior, local chat and history, search during background indexing, opening the selected exact path, and allowed/unknown app requests.
- QA covers unavailable Ollama/model, shortcut conflicts, no matches, long paths, permission-denied and corrupt files, large indexes, keyboard navigation, and keeping the interface responsive during generation/indexing.

## User-confirmed MVP defaults

- Initial apps: Notepad, Calculator, and Word.
- Shortcut: `Ctrl+Alt+A`, available after the user manually launches the assistant; Windows sign-in startup is excluded from the MVP.
- Content-search formats: `.txt`, `.md`, `.pdf`, and `.docx`.
- The user asked to search the whole user folder; this design includes hidden subfolders such as AppData.

## Decisions left to the builder

- Choose the smallest dependency set for tray behavior and document extraction.
- Choose module names and exact SQLite schema while keeping UI, chat service, indexing, and Windows actions separate.
- Implement cancellation/closing behavior for background work without blocking the chat window.
- Surface index progress, skipped-file counts, and model errors in plain language.
