from __future__ import annotations

from pathlib import Path

import pytest

from personal_assistant.chat import (
    ChatServiceError,
    ConversationController,
)
from personal_assistant.history import HistoryRepository
from personal_assistant.ui import ChatSession


@pytest.fixture
def database_path(request):
    path = Path.cwd() / ".tmp" / f"test-controller-{request.node.name}.sqlite3"

    def clean_database_files():
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)

    clean_database_files()
    request.addfinalizer(clean_database_files)
    return path


class FakeChatService:
    def __init__(self, reply="Hello!", error=None):
        self.reply_text = reply
        self.error = error

    def reply(self, messages):
        if self.error is not None:
            raise self.error
        return self.reply_text


def make_controller(history, *, reply="Hello!", error=None):
    return ConversationController(
        history=history,
        chat=FakeChatService(reply=reply, error=error),
    )


def test_session_restores_the_most_recent_conversation_and_messages(database_path):
    history = HistoryRepository(database_path)
    history.create_conversation("Older chat")
    latest_id = history.create_conversation("Latest chat")
    history.add_message(latest_id, "user", "Saved question")
    history.add_message(latest_id, "assistant", "Saved answer")

    session = ChatSession(history, make_controller(history))

    assert session.active_conversation_id == latest_id
    assert [(message.role, message.content) for message in session.messages] == [
        ("user", "Saved question"),
        ("assistant", "Saved answer"),
    ]


def test_session_creates_a_conversation_when_history_is_empty(database_path):
    history = HistoryRepository(database_path)

    session = ChatSession(history, make_controller(history))

    assert session.active_conversation_id
    assert session.messages == []
    assert [item.conversation_id for item in history.list_recent_conversations()] == [
        session.active_conversation_id
    ]


def test_session_can_start_and_select_conversations(database_path):
    history = HistoryRepository(database_path)
    first_id = history.create_conversation("First chat")
    history.add_message(first_id, "user", "First message")
    second_id = history.create_conversation("Second chat")
    history.add_message(second_id, "assistant", "Second message")
    session = ChatSession(history, make_controller(history))

    session.select_conversation(first_id)
    assert session.active_conversation_id == first_id
    assert [(message.role, message.content) for message in session.messages] == [
        ("user", "First message")
    ]

    new_id = session.start_conversation()
    assert new_id not in {first_id, second_id}
    assert session.active_conversation_id == new_id
    assert session.messages == []


def test_blank_input_does_not_change_history(database_path):
    history = HistoryRepository(database_path)
    session = ChatSession(history, make_controller(history))
    before = history.list_messages(session.active_conversation_id)

    session.send("   ")

    assert history.list_messages(session.active_conversation_id) == before


def test_successful_send_updates_active_messages(database_path):
    history = HistoryRepository(database_path)
    session = ChatSession(
        history,
        make_controller(history, reply="Here is something interesting."),
    )

    reply = session.send("Tell me something interesting")

    assert reply == "Here is something interesting."
    assert [(message.role, message.content) for message in session.messages] == [
        ("user", "Tell me something interesting"),
        ("assistant", "Here is something interesting."),
    ]


def test_chat_error_propagates_and_keeps_the_user_message(database_path):
    history = HistoryRepository(database_path)
    session = ChatSession(
        history,
        make_controller(
            history,
            error=ChatServiceError("Ollama is unavailable"),
        ),
    )

    with pytest.raises(ChatServiceError, match="Ollama is unavailable"):
        session.send("Keep this question")

    assert [(message.role, message.content) for message in session.messages] == [
        ("user", "Keep this question")
    ]


def test_action_history_records_user_then_final_assistant_result(database_path):
    history = HistoryRepository(database_path)
    session = ChatSession(history, make_controller(history))
    action_conversation_id = session.active_conversation_id

    session.record_user_action(
        "find my resume",
        conversation_id=action_conversation_id,
    )

    assert [
        (message.role, message.content)
        for message in history.list_messages(action_conversation_id)
    ] == [("user", "find my resume")]

    session.start_conversation()
    session.record_assistant_result(
        "I found resume.pdf.",
        conversation_id=action_conversation_id,
    )

    assert [
        (message.role, message.content)
        for message in history.list_messages(action_conversation_id)
    ] == [
        ("user", "find my resume"),
        ("assistant", "I found resume.pdf."),
    ]
    assert session.messages == []


def test_deleting_active_conversation_starts_a_fresh_chat(database_path):
    history = HistoryRepository(database_path)
    older_id = history.create_conversation("Older chat")
    history.add_message(older_id, "user", "Keep older message")
    active_id = history.create_conversation("Current chat")
    history.add_message(active_id, "user", "Delete this message")
    session = ChatSession(history, make_controller(history))

    session.delete_conversation(active_id)

    assert session.active_conversation_id not in {older_id, active_id}
    assert session.messages == []
    assert [item.conversation_id for item in history.list_recent_conversations()] == [
        session.active_conversation_id,
        older_id,
    ]
    assert history.list_messages(active_id) == []
    assert [message.content for message in history.list_messages(older_id)] == [
        "Keep older message"
    ]


def test_deleting_non_active_conversation_preserves_the_active_chat(database_path):
    history = HistoryRepository(database_path)
    old_id = history.create_conversation("Old chat")
    history.add_message(old_id, "user", "Delete this old message")
    active_id = history.create_conversation("Active chat")
    history.add_message(active_id, "assistant", "Keep this reply")
    session = ChatSession(history, make_controller(history))

    session.delete_conversation(old_id)

    assert session.active_conversation_id == active_id
    assert [(message.role, message.content) for message in session.messages] == [
        ("assistant", "Keep this reply")
    ]
    assert history.list_messages(old_id) == []


def test_clearing_all_history_leaves_a_fresh_active_conversation(database_path):
    history = HistoryRepository(database_path)
    history.create_conversation("First chat")
    second_id = history.create_conversation("Second chat")
    history.add_message(second_id, "user", "Delete this message")
    session = ChatSession(history, make_controller(history))

    session.delete_all_conversations()

    assert session.active_conversation_id
    assert session.messages == []
    assert [item.conversation_id for item in history.list_recent_conversations()] == [
        session.active_conversation_id
    ]
    assert history.list_messages(second_id) == []
