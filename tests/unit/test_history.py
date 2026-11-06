from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from personal_assistant.history import HistoryRepository


@pytest.fixture
def database_path(request):
    # Pytest-managed temporary directories are inaccessible in this Windows
    # sandbox. Use a deterministic database path under the writable workspace.
    path = Path.cwd() / ".tmp" / f"test-history-{request.node.name}.sqlite3"

    def clean_database_files():
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)

    clean_database_files()
    request.addfinalizer(clean_database_files)
    return path


def test_create_conversation_returns_a_stable_id_and_lists_newest_first(database_path):
    repository = HistoryRepository(database_path)

    first_id = repository.create_conversation("First chat")
    second_id = repository.create_conversation("Second chat")

    assert isinstance(first_id, str)
    assert first_id
    assert second_id != first_id
    assert [item.conversation_id for item in repository.list_recent_conversations()] == [
        second_id,
        first_id,
    ]


def test_messages_keep_role_content_and_insertion_order(database_path):
    repository = HistoryRepository(database_path)
    conversation_id = repository.create_conversation("Chat")

    repository.add_message(conversation_id, "user", "Hi there")
    repository.add_message(conversation_id, "assistant", "Hello!")
    repository.add_message(conversation_id, "user", "How are you?")

    messages = repository.list_messages(conversation_id)

    assert [(message.role, message.content) for message in messages] == [
        ("user", "Hi there"),
        ("assistant", "Hello!"),
        ("user", "How are you?"),
    ]


def test_messages_survive_repository_reopen(database_path):
    first_repository = HistoryRepository(database_path)
    conversation_id = first_repository.create_conversation("Persistent chat")
    first_repository.add_message(conversation_id, "user", "Keep this message")
    first_repository.add_message(conversation_id, "assistant", "It is saved")

    reopened_repository = HistoryRepository(database_path)

    assert (
        reopened_repository.list_recent_conversations()[0].conversation_id
        == conversation_id
    )
    messages = reopened_repository.list_messages(conversation_id)
    assert [(message.role, message.content) for message in messages] == [
        ("user", "Keep this message"),
        ("assistant", "It is saved"),
    ]


def test_rejects_message_roles_other_than_user_or_assistant(database_path):
    repository = HistoryRepository(database_path)
    conversation_id = repository.create_conversation()

    with pytest.raises(ValueError):
        repository.add_message(conversation_id, "system", "Not allowed")


def test_history_schema_version_is_namespaced_for_shared_database(database_path):
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_version (version) VALUES (7)")
        connection.commit()
    finally:
        connection.close()

    repository = HistoryRepository(database_path)
    conversation_id = repository.create_conversation("History remains available")

    assert repository.list_recent_conversations()[0].conversation_id == conversation_id


def test_delete_conversation_removes_its_messages_and_preserves_other_chats(database_path):
    repository = HistoryRepository(database_path)
    keep_id = repository.create_conversation("Keep this chat")
    delete_id = repository.create_conversation("Delete this chat")
    repository.add_message(keep_id, "user", "Keep this message")
    repository.add_message(delete_id, "user", "Remove this message")
    repository.add_message(delete_id, "assistant", "Remove this reply")

    repository.delete_conversation(delete_id)

    assert [item.conversation_id for item in repository.list_recent_conversations()] == [
        keep_id
    ]
    assert repository.list_messages(delete_id) == []
    assert [message.content for message in repository.list_messages(keep_id)] == [
        "Keep this message"
    ]


def test_delete_all_conversations_removes_every_chat_and_message(database_path):
    repository = HistoryRepository(database_path)
    first_id = repository.create_conversation("First chat")
    second_id = repository.create_conversation("Second chat")
    repository.add_message(first_id, "user", "First message")
    repository.add_message(second_id, "assistant", "Second reply")

    repository.delete_all_conversations()

    assert repository.list_recent_conversations() == []
    assert repository.list_messages(first_id) == []
    assert repository.list_messages(second_id) == []
