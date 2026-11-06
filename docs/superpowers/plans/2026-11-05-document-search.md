# Local Document Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Search the user's complete Windows profile by document filename and content, display selectable matches in chat, and build/refresh the index in the background.

**Architecture:** Extract text only from `.txt`, `.md`, `.pdf`, and `.docx`; store paths, extracted text, and file metadata in the existing SQLite database with FTS5. A background indexer scans the profile and sends progress/results to the UI through a queue; it never blocks the Tk main loop.

**Tech Stack:** Python 3.11+, SQLite FTS5, `pypdf`, `python-docx`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-05-personal-ai-assistant-design.md`

## Global Constraints

- Search the complete current-user profile, including hidden folders; skip junctions/symlinks and continue past inaccessible or corrupt files.
- Search filename and extracted content for `.txt`, `.md`, `.pdf`, and `.docx` only.
- Store the index locally in the app-data SQLite database; no network or cloud search.
- All automated file tests use temporary synthetic folders; never scan the real user profile in tests.
- Return paths/excerpts to the user and defer opening the selected path to the Windows integration layer.

## Review Focus

- Hidden or inaccessible directories: traversal continues and reports skipped paths/counts without stopping.
- Corrupt or unsupported documents: index job continues and does not create false content matches.
- File changed after initial indexing: incremental refresh replaces stale content and metadata.
- Search query contains punctuation or FTS operators: query is safely tokenized/quoted and does not expose raw SQL.
- Indexing is still running: search reads the current index and the chat UI remains responsive.

---

### Task 1: Add document text extractors

**Files:**
- Modify: `pyproject.toml`
- Create: `src/personal_assistant/search/__init__.py`
- Create: `src/personal_assistant/search/extractors.py`
- Test: `tests/unit/test_extractors.py`

**Interfaces:**
- `SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf", ".docx"}`.
- `extract_text(path: Path) -> str` reads a supported document and returns normalized searchable text.
- `MAX_INDEXED_TEXT_CHARS = 1_000_000` bounds indexed text per document.
- Raise `UnsupportedDocumentError` for other suffixes; malformed/unreadable inputs produce a per-file extraction error.

- [ ] **Step 1: Add `pypdf` and `python-docx` runtime dependencies and `reportlab` as a test-only dependency** to `pyproject.toml`.
- [ ] **Step 2: Install the updated project dependencies**

Run: `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`

Expected: `pypdf`, `python-docx`, and the PDF test-fixture generator install into the project environment.

- [ ] **Step 3: Write failing extractor tests** for UTF-8 text/Markdown, generated PDF, generated DOCX, unsupported extensions, corrupt PDF/DOCX, and content truncation at `MAX_INDEXED_TEXT_CHARS`.
- [ ] **Step 4: Run extractor tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_extractors.py -q`

Expected: FAIL because extractors are absent.

- [ ] **Step 5: Implement the extractors** using the standard library, `pypdf`, and `python-docx`; normalize whitespace and cap indexed text per document at `MAX_INDEXED_TEXT_CHARS`.
- [ ] **Step 6: Re-run extractor tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_extractors.py -q`

Expected: all format and failure-case tests pass.

### Task 2: Implement FTS5 indexing and search

**Files:**
- Create: `src/personal_assistant/search/indexer.py`
- Test: `tests/unit/test_indexer.py`

**Interfaces:**
- `SearchResult(path: Path, excerpt: str, modified_ns: int)` is the UI-facing result type.
- `DocumentIndexer(db_path: Path, root: Path, extractor: Callable[[Path], str] = extract_text)` owns scan/index/search operations.
- `index(progress: Callable[[IndexProgress], None] | None = None, cancel: Event | None = None) -> None` walks the profile and updates changed supported files using size and nanosecond mtime.
- `IndexProgress` reports scanned, indexed, and skipped counts plus the current path without retaining file contents in progress events.
- `search(query: str, limit: int = 20) -> list[SearchResult]` searches indexed filename and content through parameterized FTS queries.
- `rebuild(...)` clears and repopulates document rows; normal startup performs an incremental scan.
- Document tables are created by the search module in the same `db_path`; history and search own separate tables and migrations.

- [ ] **Step 1: Write failing tests** using `tmp_path` fixtures for filename match, content match, updated/deleted files, FTS punctuation, skipped junction/inaccessible paths via an injected walker, progress events, cancel handling, and FTS5 schema availability.
- [ ] **Step 2: Run indexer tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_indexer.py -q`

Expected: FAIL because the indexer is absent.

- [ ] **Step 3: Implement the SQLite document schema and indexer** with FTS5, parameterized queries, incremental metadata checks, and per-file error handling.
- [ ] **Step 4: Re-run indexer and extractor tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_indexer.py tests/unit/test_extractors.py -q`

Expected: all indexing/search tests pass using only temporary test data.

### Task 3: Connect background search to chat results

**Files:**
- Modify: `src/personal_assistant/ui.py`
- Create: `src/personal_assistant/search/controller.py`
- Test: `tests/unit/test_search_controller.py`

**Interfaces:**
- `SearchController(indexer: DocumentIndexer, result_callback, progress_callback)` runs indexing/search on a worker and posts immutable updates to the UI queue.
- `AssistantApp` renders progress, no-match, error, and result-card states; each result card calls `on_result_selected(path: Path)` only after the user chooses it.
- `SearchController.stop() -> None` signals cancellation without blocking the Tk main loop; startup triggers an incremental index, and the Find files panel offers **Re-index**.
- Search uses the full profile root `Path.home()` and never calls a file-opening function directly.

- [ ] **Step 1: Write failing controller tests** for query dispatch, progress reporting, no results, worker errors, search during indexing, and selected-path callback behavior.
- [ ] **Step 2: Run controller tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit/test_search_controller.py -q`

Expected: FAIL until the controller is implemented.

- [ ] **Step 3: Implement the worker/controller and inline clickable result cards** without blocking the UI thread.
- [ ] **Step 4: Run all search tests and a temporary-folder UI smoke check**

Run: `.\.venv\Scripts\python.exe -m pytest tests/unit -q`

Expected: all unit tests pass; a manual search against a temporary test profile shows paths and opens nothing until a result is selected.
