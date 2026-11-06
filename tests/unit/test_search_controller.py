from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from personal_assistant.search.controller import SearchController


@dataclass(frozen=True)
class FakeProgress:
    scanned: int = 2
    indexed: int = 2
    skipped: int = 0
    current_path: Path | None = None


class FakeIndexer:
    def __init__(
        self,
        results=None,
        error=None,
        index_started: threading.Event | None = None,
        release_index: threading.Event | None = None,
    ):
        self.results = list(results or [])
        self.error = error
        self.index_started = index_started
        self.release_index = release_index
        self.search_calls = []
        self.index_calls = 0
        self.rebuild_calls = 0

    def index(self, progress=None, cancel=None):
        self.index_calls += 1
        if self.index_started is not None:
            self.index_started.set()
        if progress is not None:
            progress(FakeProgress(current_path=Path("C:/synthetic/indexing.txt")))
        if self.release_index is not None:
            self.release_index.wait(timeout=3)

    def rebuild(self, progress=None, cancel=None):
        self.rebuild_calls += 1
        if progress is not None:
            progress(FakeProgress(current_path=Path("C:/synthetic/rebuild.txt")))

    def search(self, query, limit=20):
        self.search_calls.append((query, limit))
        if self.error is not None:
            raise self.error
        return self.results


def wait_for(controller: SearchController, condition, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        controller.process_updates()
        if condition():
            controller.process_updates()
            return
        time.sleep(0.005)
    controller.process_updates()
    assert condition(), "search-controller callback was not dispatched in time"


def test_search_dispatches_query_and_results_to_callback():
    result = SimpleNamespace(
        path=Path("C:/synthetic/meeting-notes.txt"),
        excerpt="meeting notes",
        modified_ns=1,
    )
    indexer = FakeIndexer(results=[result])
    result_updates = []
    controller = SearchController(
        indexer,
        result_callback=lambda request_id, query, results, error: result_updates.append(
            (request_id, query, results, error)
        ),
        progress_callback=lambda progress: None,
    )

    request_id = controller.search("meeting notes")
    wait_for(controller, lambda: bool(result_updates))

    assert request_id
    assert indexer.search_calls == [("meeting notes", 20)]
    assert result_updates == [(request_id, "meeting notes", [result], None)]


def test_same_query_concurrent_requests_keep_distinct_ids_when_completed_out_of_order():
    first_started = threading.Event()
    release_first = threading.Event()
    first_result = SimpleNamespace(
        path=Path("C:/synthetic/first.pdf"), excerpt="first result", modified_ns=1
    )
    second_result = SimpleNamespace(
        path=Path("C:/synthetic/second.pdf"), excerpt="second result", modified_ns=2
    )

    class OutOfOrderIndexer(FakeIndexer):
        def __init__(self):
            super().__init__()
            self._call_lock = threading.Lock()
            self._search_number = 0

        def search(self, query, limit=20):
            with self._call_lock:
                self._search_number += 1
                search_number = self._search_number
            self.search_calls.append((query, limit))
            if search_number == 1:
                first_started.set()
                release_first.wait(timeout=3)
                return [first_result]
            return [second_result]

    indexer = OutOfOrderIndexer()
    result_updates = []
    controller = SearchController(
        indexer,
        result_callback=lambda request_id, query, results, error: result_updates.append(
            (request_id, query, results, error)
        ),
        progress_callback=lambda progress: None,
    )

    try:
        first_request_id = controller.search("resume")
        assert first_started.wait(timeout=1)
        second_request_id = controller.search("resume")
        assert first_request_id != second_request_id
        wait_for(
            controller,
            lambda: any(item[0] == second_request_id for item in result_updates),
        )
        assert result_updates[0] == (
            second_request_id,
            "resume",
            [second_result],
            None,
        )

        release_first.set()
        wait_for(controller, lambda: len(result_updates) == 2)
        assert result_updates[1] == (
            first_request_id,
            "resume",
            [first_result],
            None,
        )
    finally:
        release_first.set()
        controller.stop()


def test_progress_is_dispatched_on_the_caller_ui_thread():
    indexer = FakeIndexer()
    progress_updates = []
    callback_threads = []
    caller_thread = threading.get_ident()

    def on_progress(progress):
        progress_updates.append(progress)
        callback_threads.append(threading.get_ident())

    controller = SearchController(
        indexer,
        result_callback=lambda request_id, query, results, error: None,
        progress_callback=on_progress,
    )

    controller.start()
    wait_for(controller, lambda: bool(progress_updates))

    assert progress_updates[0].indexed == 2
    assert callback_threads == [caller_thread]


def test_empty_search_results_are_delivered_as_no_match_state():
    indexer = FakeIndexer(results=[])
    result_updates = []
    controller = SearchController(
        indexer,
        result_callback=lambda request_id, query, results, error: result_updates.append(
            (request_id, query, results, error)
        ),
        progress_callback=lambda progress: None,
    )

    request_id = controller.search("nothing matches")
    wait_for(controller, lambda: bool(result_updates))

    assert result_updates == [(request_id, "nothing matches", [], None)]


def test_search_worker_errors_are_reported_in_result_callback():
    indexer = FakeIndexer(error=OSError("synthetic index read failure"))
    result_updates = []
    controller = SearchController(
        indexer,
        result_callback=lambda request_id, query, results, error: result_updates.append(
            (request_id, query, results, error)
        ),
        progress_callback=lambda progress: None,
    )

    request_id = controller.search("a query")
    wait_for(controller, lambda: bool(result_updates))

    callback_request_id, query, results, error = result_updates[0]
    assert callback_request_id == request_id
    assert query == "a query"
    assert error
    assert "synthetic index read failure" in error


def test_search_can_finish_while_background_indexing_is_blocked():
    index_started = threading.Event()
    release_index = threading.Event()
    result = SimpleNamespace(
        path=Path("C:/synthetic/available-now.txt"),
        excerpt="current indexed match",
        modified_ns=2,
    )
    indexer = FakeIndexer(
        results=[result],
        index_started=index_started,
        release_index=release_index,
    )
    result_updates = []
    controller = SearchController(
        indexer,
        result_callback=lambda request_id, query, results, error: result_updates.append(
            (request_id, query, results, error)
        ),
        progress_callback=lambda progress: None,
    )

    try:
        controller.start()
        assert index_started.wait(timeout=1)
        request_id = controller.search("current indexed match")
        wait_for(controller, lambda: bool(result_updates))

        assert not release_index.is_set()
        assert result_updates[0] == (
            request_id,
            "current indexed match",
            [result],
            None,
        )
    finally:
        release_index.set()
        controller.stop()


def test_stop_is_prompt_and_blocked_index_workers_are_daemon_threads():
    index_started = threading.Event()
    release_index = threading.Event()
    indexer = FakeIndexer(
        index_started=index_started,
        release_index=release_index,
    )
    controller = SearchController(
        indexer,
        result_callback=lambda request_id, query, results, error: None,
        progress_callback=lambda progress: None,
    )

    try:
        controller.start()
        assert index_started.wait(timeout=1)
        worker_threads = tuple(controller._executor._threads)

        started_at = time.monotonic()
        controller.stop()
        elapsed = time.monotonic() - started_at

        assert elapsed < 0.25
        assert worker_threads
        assert all(thread.daemon for thread in worker_threads)
        assert not release_index.is_set()
    finally:
        release_index.set()
        controller.stop()


def test_shutdown_can_wait_until_blocked_index_worker_finishes():
    index_started = threading.Event()
    release_index = threading.Event()
    indexer = FakeIndexer(
        index_started=index_started,
        release_index=release_index,
    )
    controller = SearchController(
        indexer,
        result_callback=lambda request_id, query, results, error: None,
        progress_callback=lambda progress: None,
    )

    controller.start()
    assert index_started.wait(timeout=1)
    controller.stop()

    try:
        assert controller.wait_for_workers(timeout=0.01) is False
    finally:
        release_index.set()

    assert controller.wait_for_workers(timeout=2) is True


def test_reindex_requests_a_background_rebuild():
    indexer = FakeIndexer()
    controller = SearchController(
        indexer,
        result_callback=lambda request_id, query, results, error: None,
        progress_callback=lambda progress: None,
    )

    controller.reindex()
    deadline = time.monotonic() + 2
    while indexer.rebuild_calls == 0 and time.monotonic() < deadline:
        controller.process_updates()
        time.sleep(0.005)

    assert indexer.rebuild_calls == 1


def test_selected_path_is_forwarded_unchanged():
    selected_paths = []
    selected_path = Path("C:/synthetic/selected report.pdf")
    controller = SearchController(
        FakeIndexer(),
        result_callback=lambda request_id, query, results, error: None,
        progress_callback=lambda progress: None,
        selected_path_callback=selected_paths.append,
    )

    controller.select_result(selected_path)

    assert selected_paths == [selected_path]
    assert selected_paths[0] is selected_path


def test_rejected_search_submission_raises_a_clear_error(monkeypatch):
    controller = SearchController(
        FakeIndexer(),
        result_callback=lambda request_id, query, results, error: None,
        progress_callback=lambda progress: None,
    )

    def reject_submission(*args, **kwargs):
        raise RuntimeError("search queue is full")

    monkeypatch.setattr(controller._executor, "submit", reject_submission)

    with pytest.raises(RuntimeError, match="search queue is full"):
        controller.search("another query")

    controller.stop()
