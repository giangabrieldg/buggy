# Buggy Chat Motion and Expressions Implementation Plan

> **For agentic workers:** Use the project's subagent-driven workflow. Assign each worker explicit file ownership, keep shared edits coordinated, and run the focused checks after every task. This plan is for implementation after the design is reviewed.

**Goal:** Make Buggy's compact chat smoother with progressive local replies, restrained motion, and mascot expressions driven by app activity and clear conversation tone.

**Architecture:** A chat worker performs Ollama and SQLite work, then posts typed events to a thread-safe queue. The Tk thread polls events, owns all widgets and view state, updates the active reply bubble in place, and schedules cancelable motion. A pure expression policy maps activity and high-confidence tone cues to packaged avatar assets.

**Tech Stack:** Python 3.12, CustomTkinter, Pillow, Ollama Python client, SQLite, pytest. Use Tk `after` for motion and the existing local Ollama server. No new runtime library is planned.

**Spec:** `docs/superpowers/specs/2026-11-06-buggy-chat-motion-and-expressions-design.md`

## Global Constraints

- Keep all model work local and offline. Only display and persist Ollama `message.content`; never reveal or save `message.thinking`.
- Keep every widget change and animation callback on the Tk thread. Worker events carry request and conversation IDs. Consume completion/error even when hidden or viewing another chat; suppress only irrelevant visual updates.
- Save a user turn and a completed assistant turn at most once each. Partial reply text, animations, and expression labels remain ephemeral.
- Preserve existing chat history, search, command routing, shortcut, tray, green palette, and compact minimum size.
- Respect `reduce_motion` and cancel timers when the view is replaced, hidden, or closed.
- Follow test-driven development for behavior changes: first add a focused failing test, then implement, then run that test and the affected suite.
- This workspace currently has no Git repository, so this plan does not include commit steps.

## Review Focus

- Check the exact expression images against the approved green mascot before wiring them into the app.
- Exercise a slow Qwen reply, quick reply, empty reply, service failure, and a stream that errors after partial text.
- Switch chats while a reply is in flight; hide/show from the tray; exit while timers and worker events are pending.
- Check 380-pixel width, high-DPI Windows scaling, keyboard focus, scrolling while text arrives, and reduced motion.
- Confirm search results and app commands still appear in the right conversation without a full chat flicker.

---

### Task 1: Establish a UI-safe chat event flow

**Files:** Modify `src/personal_assistant/ui.py`; add or update `tests/unit/test_chat_ui.py` and `tests/unit/test_controller.py` as needed.

**Interfaces:** A typed event (for example `started`, `chunk`, `complete`, `error`) includes `request_id` and `conversation_id`. The worker calls the controller and places events in `queue.Queue`; only the Tk poller refreshes `ChatSession.messages`, widgets, and busy state.

- [ ] **Test first:** Add a focused test where a reply finishes after the user switches conversations. Assert that no reply appears in the other chat, busy state resets, and the original conversation can be refreshed. Add a test that a late event after close is ignored.
- [ ] **Verify failure:** Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_chat_ui.py -q` and confirm the new tests fail for the intended reason.
- [ ] **Implement:** Replace worker-side `ChatSession.send()`/`root.after()` state mutation with queue delivery and a Tk `after` poller. Keep request correlation and clear busy state on both completion and error. Ensure the poller stops on exit.
- [ ] **Verify pass:** Run the focused tests, then `tests/unit/test_chat.py tests/unit/test_controller.py`.

### Task 2: Stream visible Ollama text and persist only completed replies

**Files:** Modify `src/personal_assistant/chat.py`; update `tests/unit/test_chat.py` and `tests/unit/test_controller.py`.

**Interfaces:** Update the `ChatClient` protocol to accept `stream=True`; add a streaming method such as `reply_stream(messages) -> Iterator[str]` and a controller method that accepts a chunk callback. Keep the existing synchronous `reply`/`submit` behavior if other callers or tests depend on it. The controller writes the user message before the request and writes the assistant message only after a valid complete stream. A stream with no visible content raises a readable `ChatServiceError` without writing an assistant row.

- [ ] **Test first:** Cover content chunks, separate thinking chunks, no visible content, a mid-stream exception, and exactly-once persistence of complete replies.
- [ ] **Verify failure:** Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_chat.py tests/unit/test_controller.py -q` and confirm the new tests fail as expected.
- [ ] **Implement:** Call the local Ollama Python client with `stream=True`, yield only content, map service errors to the existing readable error type, and post chunks from the worker queue. Do not save partial output after failure.
- [ ] **Verify pass:** Re-run the two focused test files. Review the [Ollama streaming contract](https://docs.ollama.com/capabilities/streaming) while implementing.

### Task 3: Update rows in place and add restrained motion

**Files:** Modify `src/personal_assistant/ui.py`; create `src/personal_assistant/motion.py`; update `tests/unit/test_chat_ui.py`; create `tests/unit/test_motion.py`.

**Interfaces:** A cancelable scheduler wraps Tk `after` IDs. The view exposes append/update/remove operations for the current row. Full `_render_messages()` is used for conversation restore or history deletion, not for each incoming chunk. The active assistant bubble is temporary until completion.

- [ ] **Test first:** Cover immediate user-row display, one temporary assistant row reused across chunks, no destruction of older rows on completion, stale timer cancellation, and preserving scroll position when the reader is above the bottom. Verify that the input accepts a follow-up draft while Send is disabled and Enter preserves that draft.
- [ ] **Verify failure:** Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_chat_ui.py tests/unit/test_motion.py -q` and confirm the new tests fail as intended.
- [ ] **Implement:** Add 150–180 ms row color reveal, 300–400 ms thinking dots, and roughly 50 ms batching for streamed text. Give the active row a stable widget reference; append command/search rows in place where possible. Cancel pending effects on rerender/hide/exit. Keep keyboard focus and content readable throughout.
- [ ] **Verify pass:** Re-run the focused tests and manually inspect the 380 × 460 window.

### Task 4: Package matching expression artwork

**Files:** Add `src/personal_assistant/assets/expressions/*.png`; modify `pyproject.toml`; create or update an asset-loading test in `tests/unit/test_chat_ui.py`.

**Interfaces:** Neutral, thinking, happy, curious, caring, and concerned keys map to transparent PNGs at the same square dimensions/crop. Load each once as `CTkImage` from package resources and retain references for the lifetime of the window.

- [ ] **Prepare assets:** Edit the approved green mascot into five matching expressions. Inspect the generated images at avatar size and on both light/dark preview backgrounds; remove any mismatch or stray background.
- [ ] **Test first:** Add a resource test for all six expression keys and ensure package data includes the nested asset directory.
- [ ] **Verify failure:** Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_chat_ui.py -q` and confirm the new asset test fails before wiring the files.
- [ ] **Implement:** Add the vetted PNGs, package-data glob, image loader, and neutral fallback if one optional variant is unreadable.
- [ ] **Verify pass:** Run the asset test and inspect the installed-package resource path.

### Task 5: Add activity and reply-tone expression policy

**Files:** Create `src/personal_assistant/expressions.py`; modify `src/personal_assistant/ui.py`; create `tests/unit/test_expressions.py`; update `tests/unit/test_chat_ui.py`.

**Interfaces:** A pure policy returns `neutral`, `thinking`, `happy`, `curious`, `caring`, or `concerned`. Activity priority is error, then thinking, then completed-reply tone, then neutral. Tone rules use the user's message and the completed assistant reply; ambiguous or contradictory cues return neutral. The active header/latest-reply avatar uses the current expression; older bubbles use neutral.

- [ ] **Test first:** Cover each state, ambiguous tone fallback, state priority, return-to-neutral timing, and no emotion change to stored text.
- [ ] **Verify failure:** Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_expressions.py tests/unit/test_chat_ui.py -q` and confirm the new tests fail as intended.
- [ ] **Implement:** Write the conservative classifier and state controller; switch `CTkImage` on the active avatar with a short transition. Hold the completion expression for about four seconds. Use the concerned face for failures and a neutral face for old history.
- [ ] **Verify pass:** Re-run focused tests and manually inspect all faces at normal and high-DPI scaling.

### Task 6: Add reduced motion, integrate lifecycle, and complete QA

**Files:** Modify `src/personal_assistant/config.py`, `settings.toml`, `src/personal_assistant/main.py`, `src/personal_assistant/ui.py`, and `README.md`; update `tests/unit/test_config.py`, `tests/unit/test_chat_ui.py`, and `tests/unit/test_windows_lifecycle.py`.

**Interfaces:** `Settings.reduce_motion: bool` defaults to `False` and is passed into `AssistantApp`. When enabled, rows, text, and avatar changes are immediate, and the thinking indicator is static. Invalid TOML types fail clearly.

- [ ] **Test first:** Cover the default and explicit setting, invalid setting type, timer cleanup on tray hide/exit, and restoration after show. Verify that hidden-window completion/error still resets busy state, and that showing the window refreshes the saved conversation.
- [ ] **Verify failure:** Run `.\.venv\Scripts\python.exe -m pytest tests/unit/test_config.py tests/unit/test_windows_lifecycle.py tests/unit/test_chat_ui.py -q` and confirm the new tests fail as intended.
- [ ] **Implement:** Wire settings and lifecycle behavior, update the README with the motion setting, and ensure hidden-window events still leave the saved chat correct.
- [ ] **Verify pass:** Run `.\.venv\Scripts\python.exe -m pytest -q`. Then launch Buggy with the installed local model and perform the Review Focus scenarios. Report any environment-limited checks separately.

## Execution handoff

Use the established subagent-driven process after review. A test engineer can own the new expression/motion tests; an asset worker can own the expression PNGs and `pyproject.toml` package-data entry; a builder can own the chat streaming/controller work. Coordinate `ui.py` through one builder because its event flow, widgets, and lifecycle touch the same file. A QA reviewer should run the Windows interaction matrix after integration and report observed results.
