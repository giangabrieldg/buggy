from __future__ import annotations

from pathlib import Path
import os
import shutil
import sqlite3
from threading import Event

import pytest

from personal_assistant.search.indexer import DocumentIndexer


@pytest.fixture
def indexer_paths(request):
    name = request.node.name.replace("/", "_").replace("\\", "_")
    base = Path.cwd() / ".tmp"
    root = base / f"test-indexer-{name}-files"
    db_path = base / f"test-indexer-{name}.sqlite3"

    def clean_files():
        shutil.rmtree(root, ignore_errors=True)
        for suffix in ("", "-wal", "-shm"):
            Path(f"{db_path}{suffix}").unlink(missing_ok=True)

    clean_files()
    root.mkdir(parents=True)
    request.addfinalizer(clean_files)
    return root, db_path


def make_indexer(root: Path, db_path: Path) -> DocumentIndexer:
    return DocumentIndexer(
        db_path=db_path,
        root=root,
        extractor=lambda path: path.read_text(encoding="utf-8"),
    )


def test_search_matches_document_filename_and_content(indexer_paths):
    root, db_path = indexer_paths
    filename_match = root / "quarterly-budget.txt"
    content_match = root / "notes.txt"
    filename_match.write_text("The file name contains the term.", encoding="utf-8")
    content_match.write_text("Find the hidden phrase: cobalt telescope.", encoding="utf-8")
    indexer = make_indexer(root, db_path)

    indexer.index()

    assert [result.path for result in indexer.search("quarterly-budget")] == [
        filename_match
    ]
    assert [result.path for result in indexer.search("cobalt telescope")] == [
        content_match
    ]


def test_index_includes_dot_and_hidden_profile_subfolders(indexer_paths):
    root, db_path = indexer_paths
    hidden_folder = root / ".hidden-notes"
    hidden_folder.mkdir()
    document = hidden_folder / "buried.txt"
    document.write_text("hidden profile content", encoding="utf-8")
    indexer = make_indexer(root, db_path)

    indexer.index()

    assert [result.path for result in indexer.search("hidden profile")] == [document]


def test_index_reconciles_changed_and_deleted_files(indexer_paths):
    root, db_path = indexer_paths
    changed_file = root / "change.txt"
    deleted_file = root / "delete.txt"
    changed_file.write_text("old searchable phrase", encoding="utf-8")
    deleted_file.write_text("file scheduled for removal", encoding="utf-8")
    indexer = make_indexer(root, db_path)
    indexer.index()

    changed_file.write_text("new searchable phrase", encoding="utf-8")
    deleted_file.unlink()
    indexer.index()

    assert indexer.search("old searchable") == []
    assert [result.path for result in indexer.search("new searchable")] == [
        changed_file
    ]
    assert indexer.search("scheduled removal") == []


def test_search_safely_handles_fts_punctuation_and_operators(indexer_paths):
    root, db_path = indexer_paths
    document = root / "private-alpha.txt"
    document.write_text("unique safe content", encoding="utf-8")
    indexer = make_indexer(root, db_path)
    indexer.index()

    assert indexer.search('" OR *:* --') == []
    assert [result.path for result in indexer.search("unique")] == [document]


def test_index_reports_scanned_indexed_and_skipped_counts(indexer_paths):
    root, db_path = indexer_paths
    (root / "good.txt").write_text("index this", encoding="utf-8")
    (root / "bad.txt").write_text("extractor will fail", encoding="utf-8")

    def extractor(path: Path) -> str:
        if path.name == "bad.txt":
            raise OSError("synthetic unreadable document")
        return path.read_text(encoding="utf-8")

    indexer = DocumentIndexer(db_path=db_path, root=root, extractor=extractor)
    updates = []

    indexer.index(progress=updates.append)

    assert updates
    final = updates[-1]
    assert final.scanned == 2
    assert final.indexed == 1
    assert final.skipped == 1
    assert final.current_path is not None
    assert final.done is True
    assert final.complete is True


def test_cancelled_scan_preserves_existing_entries(indexer_paths):
    root, db_path = indexer_paths
    document = root / "saved.txt"
    document.write_text("previous indexed content", encoding="utf-8")
    indexer = make_indexer(root, db_path)
    indexer.index()

    document.write_text("replacement content", encoding="utf-8")
    cancellation = Event()
    cancellation.set()

    updates = []
    indexer.index(progress=updates.append, cancel=cancellation)

    assert [result.path for result in indexer.search("previous indexed")] == [
        document
    ]
    assert indexer.search("replacement content") == []
    assert updates[-1].done is True
    assert updates[-1].complete is False


def test_inaccessible_subtree_does_not_delete_existing_index_entries(
    indexer_paths, monkeypatch
):
    root, db_path = indexer_paths
    protected = root / "protected"
    protected.mkdir()
    document = protected / "kept.txt"
    document.write_text("keep this indexed record", encoding="utf-8")
    indexer = make_indexer(root, db_path)
    indexer.index()

    original_scandir = os.scandir

    def deny_protected_directory(path):
        if not isinstance(path, int) and Path(path) == protected:
            raise PermissionError("synthetic inaccessible subtree")
        return original_scandir(path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "scandir", deny_protected_directory)
        indexer.index()

    assert [result.path for result in indexer.search("keep indexed record")] == [
        document
    ]


def test_non_directory_profile_root_does_not_delete_existing_entries(indexer_paths):
    root, db_path = indexer_paths
    document = root / "saved.txt"
    document.write_text("preserve indexed root content", encoding="utf-8")
    make_indexer(root, db_path).index()
    invalid_root = root / "not-a-directory.txt"
    invalid_root.write_text("this is a file", encoding="utf-8")
    indexer = DocumentIndexer(
        db_path=db_path,
        root=invalid_root,
        extractor=lambda path: path.read_text(encoding="utf-8"),
    )

    indexer.index()

    assert [result.path for result in indexer.search("preserve indexed root")] == [
        document
    ]


def test_database_schema_uses_fts5(indexer_paths):
    root, db_path = indexer_paths
    indexer = make_indexer(root, db_path)

    indexer.index()

    connection = sqlite3.connect(db_path)
    try:
        fts_tables = connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND lower(sql) LIKE '%using fts5%'"
        ).fetchall()
    finally:
        connection.close()

    assert fts_tables
