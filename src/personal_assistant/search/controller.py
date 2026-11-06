from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import queue
import threading
import time
from threading import Event, Lock, Thread
from typing import Callable

from personal_assistant.search.indexer import IndexProgress, SearchResult


@dataclass(frozen=True)
class _ProgressUpdate:
    progress: IndexProgress


@dataclass(frozen=True)
class _ResultUpdate:
    request_id: int | None
    query: str
    results: list[SearchResult]
    error: str | None


_STOP_WORKER = object()


class _DaemonWorkerPool:
    """Small bounded worker pool whose threads cannot keep the app process alive."""

    def __init__(self, max_workers: int, queue_capacity: int = 12):
        self._max_workers = max_workers
        self._tasks: queue.Queue[object] = queue.Queue(maxsize=queue_capacity)
        self._threads: set[Thread] = set()
        self._lock = Lock()
        self._shutdown = False

    def submit(self, target: Callable, *args) -> bool:
        with self._lock:
            if self._shutdown:
                return False
            if not self._threads:
                for worker_index in range(self._max_workers):
                    worker = Thread(
                        target=self._work,
                        name=f"assistant-search-{worker_index + 1}",
                        daemon=True,
                    )
                    self._threads.add(worker)
                    worker.start()
            try:
                self._tasks.put_nowait((target, args))
            except queue.Full:
                return False
            return True

    def _work(self) -> None:
        while True:
            task = self._tasks.get()
            try:
                if task is _STOP_WORKER:
                    return
                target, args = task
                target(*args)
            except Exception:
                # SearchController's task wrappers turn expected failures into updates.
                pass
            finally:
                self._tasks.task_done()

    def shutdown(self, wait: bool = False, cancel_futures: bool = True) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            if cancel_futures:
                while True:
                    try:
                        self._tasks.get_nowait()
                    except queue.Empty:
                        break
                    else:
                        self._tasks.task_done()
            for _worker in self._threads:
                self._tasks.put_nowait(_STOP_WORKER)
            workers = tuple(self._threads)
        if wait:
            for worker in workers:
                worker.join()

    def wait_for_workers(self, timeout: float | None = None) -> bool:
        """Join workers, returning whether they all stopped within ``timeout``."""
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        with self._lock:
            workers = tuple(self._threads)
        current = threading.current_thread()
        for worker in workers:
            if worker is current:
                continue
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            worker.join(remaining)
        return all(not worker.is_alive() for worker in workers if worker is not current)


class SearchController:
    """Run indexing and searches in workers and dispatch updates on the caller thread."""

    def __init__(
        self,
        indexer,
        result_callback: Callable[
            [int | None, str, list[SearchResult], str | None], None
        ],
        progress_callback: Callable[[IndexProgress], None] | None = None,
        selected_path_callback: Callable[[Path], None] | None = None,
    ):
        self.indexer = indexer
        self.result_callback = result_callback
        self.progress_callback = progress_callback
        self.selected_path_callback = selected_path_callback
        self._updates: queue.Queue[_ProgressUpdate | _ResultUpdate] = queue.Queue()
        self._cancel = Event()
        self._state_lock = Lock()
        self._index_lock = Lock()
        self._start_called = False
        self._stopped = False
        self._next_request_id = 0
        self._executor = _DaemonWorkerPool(max_workers=3, queue_capacity=12)

    def start(self) -> None:
        """Start the incremental index in the background once per controller."""
        with self._state_lock:
            if self._stopped or self._start_called:
                return
            self._start_called = True
        self._submit_index(rebuild=False)

    def reindex(self) -> None:
        """Rebuild the index without blocking the caller."""
        if self._is_stopped():
            return
        self._submit_index(rebuild=True)

    def search(self, query: str, limit: int = 20) -> int:
        """Run a search in a worker and return its unique request identifier."""
        with self._state_lock:
            if self._stopped:
                raise RuntimeError("Search controller is stopped.")
            self._next_request_id += 1
            request_id = self._next_request_id
        submitted = self._executor.submit(self._run_search, request_id, query, limit)
        if not submitted:
            raise RuntimeError("Search queue is busy or shutting down. Try again shortly.")
        return request_id

    def _submit_index(self, *, rebuild: bool) -> None:
        if not self._executor.submit(self._run_index, rebuild):
            raise RuntimeError("Search queue is busy or shutting down. Try again shortly.")

    def _run_index(self, rebuild: bool) -> None:
        try:
            with self._index_lock:
                if self._cancel.is_set():
                    return
                method = self.indexer.rebuild if rebuild else self.indexer.index
                method(progress=self._post_progress, cancel=self._cancel)
        except Exception as error:
            self._post_result(None, "", [], str(error))

    def _run_search(self, request_id: int, query: str, limit: int) -> None:
        if self._is_stopped():
            return
        try:
            results = self.indexer.search(query, limit=limit)
        except Exception as error:
            self._post_result(request_id, query, [], str(error))
        else:
            self._post_result(request_id, query, results, None)

    def _post_progress(self, progress: IndexProgress) -> None:
        if not self._is_stopped():
            self._updates.put(_ProgressUpdate(progress))

    def _post_result(
        self,
        request_id: int | None,
        query: str,
        results: list[SearchResult],
        error: str | None,
    ) -> None:
        if not self._is_stopped():
            self._updates.put(_ResultUpdate(request_id, query, results, error))

    def process_updates(self) -> int:
        """Dispatch queued progress and results on the thread that calls this method."""
        processed = 0
        while not self._is_stopped():
            try:
                update = self._updates.get_nowait()
            except queue.Empty:
                break
            processed += 1
            if isinstance(update, _ProgressUpdate):
                if self.progress_callback is not None:
                    self.progress_callback(update.progress)
            else:
                self.result_callback(
                    update.request_id,
                    update.query,
                    update.results,
                    update.error,
                )
        return processed

    def select_result(self, path: Path) -> None:
        """Forward the selected path unchanged to the owning UI/application layer."""
        if self.selected_path_callback is not None:
            self.selected_path_callback(path)

    def stop(self) -> None:
        """Signal background index work to stop without waiting on the UI thread."""
        with self._state_lock:
            if self._stopped:
                return
            self._stopped = True
            self._cancel.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def wait_for_workers(self, timeout: float | None = None) -> bool:
        """Wait for all daemon workers to finish, optionally within a timeout."""
        return self._executor.wait_for_workers(timeout)

    def _is_stopped(self) -> bool:
        with self._state_lock:
            return self._stopped
