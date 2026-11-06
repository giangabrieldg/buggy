from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sqlite3
import stat
from threading import Event, local
from typing import Callable, Iterator

from personal_assistant.search.extractors import SUPPORTED_SUFFIXES, extract_text


MAX_EXCERPT_CHARS = 240
_BUSY_TIMEOUT_MS = 5000
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


@dataclass(frozen=True)
class SearchResult:
    path: Path
    excerpt: str
    modified_ns: int


@dataclass(frozen=True)
class IndexProgress:
    scanned: int
    indexed: int
    skipped: int
    current_path: Path | None
    done: bool = False
    complete: bool = False


class FTS5UnavailableError(RuntimeError):
    """SQLite does not provide the FTS5 extension required for document search."""


class DocumentIndexer:
    """Incrementally index and search supported files beneath one profile root."""

    _SCHEMA_VERSION = 1

    def __init__(
        self,
        db_path: Path,
        root: Path,
        extractor: Callable[[Path], str] = extract_text,
    ):
        self.db_path = Path(db_path)
        self.root = Path(os.path.abspath(Path(root)))
        self.extractor = extractor
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connections = local()
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = getattr(self._connections, "connection", None)
        if connection is None:
            connection = sqlite3.connect(
                self.db_path,
                timeout=_BUSY_TIMEOUT_MS / 1000,
                check_same_thread=True,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            connection.execute("PRAGMA foreign_keys = ON")
            self._connections.connection = connection
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _initialize_schema(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS search_schema_version "
                "(version INTEGER NOT NULL)"
            )
            version = connection.execute(
                "SELECT version FROM search_schema_version LIMIT 1"
            ).fetchone()
            if version is None:
                connection.execute(
                    "INSERT INTO search_schema_version (version) VALUES (?)",
                    (self._SCHEMA_VERSION,),
                )
            elif version["version"] != self._SCHEMA_VERSION:
                raise RuntimeError(
                    f"Unsupported search schema version: {version['version']}"
                )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    path TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    content TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    modified_ns INTEGER NOT NULL
                )
                """
            )
            try:
                connection.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                        path UNINDEXED,
                        filename,
                        content,
                        tokenize = 'unicode61'
                    )
                    """
                )
            except sqlite3.OperationalError as error:
                if "fts5" in str(error).lower() or "no such module" in str(error).lower():
                    raise FTS5UnavailableError(
                        "Document search requires SQLite FTS5 support, which is not "
                        "available in this Python build."
                    ) from error
                raise
            connection.execute(
                "CREATE INDEX IF NOT EXISTS documents_modified "
                "ON documents (modified_ns)"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self.close()

    def close(self) -> None:
        """Close this thread's SQLite connection, if one has been opened."""
        connection = getattr(self._connections, "connection", None)
        if connection is not None:
            try:
                connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
            except sqlite3.Error:
                pass
            finally:
                connection.close()
                self._connections.connection = None

    @staticmethod
    def _is_reparse_point(file_stat: os.stat_result) -> bool:
        return bool(getattr(file_stat, "st_file_attributes", 0) & _REPARSE_POINT)

    def _progress(
        self,
        callback: Callable[[IndexProgress], None] | None,
        scanned: int,
        indexed: int,
        skipped: int,
        current_path: Path | None,
        *,
        done: bool = False,
        complete: bool = False,
    ) -> None:
        if callback is not None:
            callback(
                IndexProgress(
                    scanned,
                    indexed,
                    skipped,
                    current_path,
                    done=done,
                    complete=complete,
                )
            )

    def index(
        self,
        progress: Callable[[IndexProgress], None] | None = None,
        cancel: Event | None = None,
    ) -> None:
        try:
            self._scan(progress=progress, cancel=cancel, force=False)
        finally:
            self.close()

    def rebuild(
        self,
        progress: Callable[[IndexProgress], None] | None = None,
        cancel: Event | None = None,
    ) -> None:
        try:
            self._scan(progress=progress, cancel=cancel, force=True)
        finally:
            self.close()

    def _scan(
        self,
        *,
        progress: Callable[[IndexProgress], None] | None,
        cancel: Event | None,
        force: bool,
    ) -> None:
        scanned = indexed = skipped = 0
        current_path: Path | None = None
        seen_paths: set[str] = set()
        complete = True
        directories = [self.root]

        if self._is_cancelled(cancel):
            self._progress(
                progress, scanned, indexed, skipped, None, done=True, complete=False
            )
            return

        try:
            root_stat = self.root.stat(follow_symlinks=False)
            if (
                self.root.is_symlink()
                or self._is_reparse_point(root_stat)
                or not stat.S_ISDIR(root_stat.st_mode)
            ):
                skipped += 1
                self._progress(
                    progress,
                    scanned,
                    indexed,
                    skipped,
                    self.root,
                    done=True,
                    complete=False,
                )
                return
        except OSError:
            skipped += 1
            self._progress(
                progress, scanned, indexed, skipped, self.root, done=True, complete=False
            )
            return

        while directories:
            if self._is_cancelled(cancel):
                complete = False
                break
            directory = directories.pop()
            current_path = directory
            try:
                directory_stat = directory.stat(follow_symlinks=False)
                if (
                    directory.is_symlink()
                    or self._is_reparse_point(directory_stat)
                    or not stat.S_ISDIR(directory_stat.st_mode)
                ):
                    continue
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if self._is_cancelled(cancel):
                            complete = False
                            break
                        entry_path = Path(entry.path)
                        current_path = entry_path
                        try:
                            if entry.is_symlink():
                                continue
                            file_stat = entry.stat(follow_symlinks=False)
                            if self._is_reparse_point(file_stat):
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                directories.append(entry_path)
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                        except OSError:
                            complete = False
                            skipped += 1
                            self._progress(
                                progress, scanned, indexed, skipped, current_path
                            )
                            continue

                        if entry_path.suffix.lower() not in SUPPORTED_SUFFIXES:
                            continue

                        path_key = str(entry_path)
                        seen_paths.add(path_key)
                        scanned += 1
                        try:
                            current_stat = entry_path.stat(follow_symlinks=False)
                            if self._is_reparse_point(current_stat) or not stat.S_ISREG(
                                current_stat.st_mode
                            ):
                                raise OSError("Document changed to a link or non-file")
                            unchanged = (
                                not force
                                and current_stat.st_size == file_stat.st_size
                                and current_stat.st_mtime_ns == file_stat.st_mtime_ns
                                and self._is_unchanged(
                                    path_key,
                                    current_stat.st_size,
                                    current_stat.st_mtime_ns,
                                )
                            )
                            if unchanged:
                                latest_stat = entry_path.stat(follow_symlinks=False)
                                unchanged = (
                                    not self._is_reparse_point(latest_stat)
                                    and stat.S_ISREG(latest_stat.st_mode)
                                    and latest_stat.st_size == current_stat.st_size
                                    and latest_stat.st_mtime_ns
                                    == current_stat.st_mtime_ns
                                )
                            if unchanged:
                                indexed += 1
                            else:
                                self._extract_and_store(entry_path, path_key)
                                indexed += 1
                        except Exception:
                            skipped += 1
                            self._remove_document(path_key)
                        self._progress(
                            progress, scanned, indexed, skipped, current_path
                        )
                if not complete:
                    break
            except OSError:
                complete = False
                skipped += 1
                self._progress(progress, scanned, indexed, skipped, current_path)

        if self._is_cancelled(cancel):
            complete = False
        if complete:
            self._remove_unseen_documents(seen_paths)
        self._progress(
            progress,
            scanned,
            indexed,
            skipped,
            current_path,
            done=True,
            complete=complete,
        )

    @staticmethod
    def _is_cancelled(cancel: Event | None) -> bool:
        return cancel is not None and cancel.is_set()

    def _is_unchanged(self, path: str, size: int, modified_ns: int) -> bool:
        try:
            row = self._connect().execute(
                "SELECT size, modified_ns FROM documents WHERE path = ?", (path,)
            ).fetchone()
            return (
                row is not None
                and row["size"] == size
                and row["modified_ns"] == modified_ns
            )
        finally:
            self.close()

    def _extract_and_store(self, path: Path, path_key: str) -> None:
        for _attempt in range(2):
            before = path.stat(follow_symlinks=False)
            if (
                self._is_reparse_point(before)
                or not stat.S_ISREG(before.st_mode)
            ):
                raise OSError("Document changed to a link or non-file")
            content = self.extractor(path)
            after = path.stat(follow_symlinks=False)
            if (
                not self._is_reparse_point(after)
                and stat.S_ISREG(after.st_mode)
                and before.st_size == after.st_size
                and before.st_mtime_ns == after.st_mtime_ns
            ):
                self._store_document(
                    path=path_key,
                    filename=path.name,
                    content=content,
                    size=after.st_size,
                    modified_ns=after.st_mtime_ns,
                )
                return
        raise OSError("Document changed while it was being indexed")

    def _store_document(
        self,
        *,
        path: str,
        filename: str,
        content: str,
        size: int,
        modified_ns: int,
    ) -> None:
        try:
            with self._transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO documents (path, filename, content, size, modified_ns)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        filename = excluded.filename,
                        content = excluded.content,
                        size = excluded.size,
                        modified_ns = excluded.modified_ns
                    """,
                    (path, filename, content, size, modified_ns),
                )
                connection.execute("DELETE FROM documents_fts WHERE path = ?", (path,))
                connection.execute(
                    "INSERT INTO documents_fts (path, filename, content) VALUES (?, ?, ?)",
                    (path, filename, content),
                )
        finally:
            self.close()

    def _remove_document(self, path: str) -> None:
        try:
            with self._transaction() as connection:
                connection.execute("DELETE FROM documents_fts WHERE path = ?", (path,))
                connection.execute("DELETE FROM documents WHERE path = ?", (path,))
        finally:
            self.close()

    def _remove_unseen_documents(self, seen_paths: set[str]) -> None:
        try:
            connection = self._connect()
            existing = connection.execute("SELECT path FROM documents").fetchall()
            stale = [row["path"] for row in existing if row["path"] not in seen_paths]
        finally:
            self.close()
        if not stale:
            return
        try:
            with self._transaction() as connection:
                for path in stale:
                    connection.execute("DELETE FROM documents_fts WHERE path = ?", (path,))
                    connection.execute("DELETE FROM documents WHERE path = ?", (path,))
        finally:
            self.close()

    def search(self, query: str, limit: int = 20) -> list[SearchResult]:
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("Search limit must be a positive integer")
        terms = re.findall(r"\w+", query, flags=re.UNICODE)
        if not terms:
            return []
        match_query = " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        try:
            rows = self._connect().execute(
                """
                SELECT d.path, d.content, d.modified_ns
                FROM documents_fts
                JOIN documents AS d ON d.path = documents_fts.path
                WHERE documents_fts MATCH ?
                ORDER BY bm25(documents_fts)
                LIMIT ?
                """,
                (match_query, limit),
            ).fetchall()
            return [
                SearchResult(
                    path=Path(row["path"]),
                    excerpt=self._make_excerpt(row["content"], terms),
                    modified_ns=row["modified_ns"],
                )
                for row in rows
            ]
        finally:
            self.close()

    @staticmethod
    def _make_excerpt(content: str, terms: list[str]) -> str:
        if not content:
            return ""
        lowered = content.casefold()
        positions = [lowered.find(term.casefold()) for term in terms]
        positions = [position for position in positions if position >= 0]
        start = max(0, min(positions) - MAX_EXCERPT_CHARS // 3) if positions else 0
        return content[start : start + MAX_EXCERPT_CHARS]
