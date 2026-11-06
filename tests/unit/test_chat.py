from __future__ import annotations

from pathlib import Path

import ollama
import pytest

from personal_assistant.chat import (
    ChatServiceError,
    ConversationController,
    OllamaChatService,
)
from personal_assistant.history import ChatMessage, HistoryRepository


@pytest.fixture
def database_path(request):
    path = Path.cwd() / ".tmp" / f"test-chat-{request.node.name}.sqlite3"

    def clean_database_files():
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)

    clean_database_files()
    request.addfinalizer(clean_database_files)
    return path


class FakeOllamaClient:
    def __init__(self, response=None, error=None):
        self.response = response or {"message": {"content": "Hello there!"}}
        self.error = error
        self.calls = []

    def chat(self, *, model, messages):
        self.calls.append({"model": model, "messages": messages})
        if self.error is not None:
            raise self.error
        return self.response


class FakeChatService:
    def __init__(self, reply="That sounds fun!", error=None):
        self.reply_text = reply
        self.error = error
        self.received_messages = None

    def reply(self, messages):
        self.received_messages = list(messages)
        if self.error is not None:
            raise self.error
        return self.reply_text


class FakeStreamingClient:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def chat(self, *, model, messages, stream=False):
        self.calls.append((model, messages, stream))

        def responses():
            for chunk in self.chunks:
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk

        return responses()


class FakeStreamingChatService:
    def __init__(self, chunks):
        self.chunks = chunks

    def reply_stream(self, _messages):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


def test_streamed_reply_yields_only_visible_content():
    client = FakeStreamingClient([
        {"message": {"thinking": "private reasoning", "content": ""}},
        {"message": {"content": "Hello"}},
        {"message": {"content": " there!"}},
    ])
    service = OllamaChatService(client=client)

    assert list(service.reply_stream([])) == ["Hello", " there!"]
    assert client.calls[0][2] is True


def test_streamed_reply_without_visible_content_is_an_error():
    service = OllamaChatService(client=FakeStreamingClient([
        {"message": {"thinking": "private reasoning", "content": ""}},
    ]))

    with pytest.raises(ChatServiceError, match="without message text"):
        list(service.reply_stream([]))


def test_streamed_reply_maps_failure_after_partial_output():
    service = OllamaChatService(client=FakeStreamingClient([
        {"message": {"content": "Partial"}},
        ConnectionError("connection refused"),
    ]))
    chunks = service.reply_stream([])

    assert next(chunks) == "Partial"
    with pytest.raises(ChatServiceError, match="Ollama"):
        next(chunks)


def test_stream_controller_persists_only_a_complete_reply(database_path):
    history = HistoryRepository(database_path)
    conversation_id = history.create_conversation()
    controller = ConversationController(history, FakeStreamingChatService(["Hello", " there!"]))
    chunks = []

    reply = controller.submit_stream(conversation_id, "Hi", chunks.append)

    assert chunks == ["Hello", " there!"]
    assert reply == "Hello there!"
    assert [(m.role, m.content) for m in history.list_messages(conversation_id)] == [
        ("user", "Hi"), ("assistant", "Hello there!")
    ]


def test_stream_controller_does_not_persist_partial_reply(database_path):
    history = HistoryRepository(database_path)
    conversation_id = history.create_conversation()
    controller = ConversationController(
        history, FakeStreamingChatService(["Partial", ChatServiceError("stream failed")])
    )
    chunks = []

    with pytest.raises(ChatServiceError, match="stream failed"):
        controller.submit_stream(conversation_id, "Hi", chunks.append)

    assert chunks == ["Partial"]
    assert [(m.role, m.content) for m in history.list_messages(conversation_id)] == [
        ("user", "Hi")
    ]


def test_ollama_service_prepends_system_prompt_and_maps_conversation_messages():
    client = FakeOllamaClient()
    service = OllamaChatService(model="qwen3:1.7b", client=client)
    prior_messages = [
        ChatMessage("conversation-1", "user", "I'm bored", "2026-01-01T00:00:00+00:00"),
        ChatMessage(
            "conversation-1", "assistant", "Want to chat?", "2026-01-01T00:00:01+00:00"
        ),
    ]

    reply = service.reply(prior_messages)

    assert reply == "Hello there!"
    assert len(client.calls) == 1
    assert client.calls[0]["model"] == "qwen3:1.7b"
    sent_messages = client.calls[0]["messages"]
    assert sent_messages[0]["role"] == "system"
    assert sent_messages[0]["content"].strip()
    assert "Buggy" in sent_messages[0]["content"]
    assert sent_messages[1:] == [
        {"role": "user", "content": "I'm bored"},
        {"role": "assistant", "content": "Want to chat?"},
    ]


def test_controller_sends_prior_turn_and_persists_both_sides_of_new_turn(database_path):
    history = HistoryRepository(database_path)
    conversation_id = history.create_conversation("A chat")
    history.add_message(conversation_id, "user", "Earlier question")
    history.add_message(conversation_id, "assistant", "Earlier answer")
    chat = FakeChatService("That sounds fun!")
    controller = ConversationController(history=history, chat=chat)

    reply = controller.submit(conversation_id, "Tell me something interesting")

    assert reply == "That sounds fun!"
    assert [(message.role, message.content) for message in chat.received_messages] == [
        ("user", "Earlier question"),
        ("assistant", "Earlier answer"),
        ("user", "Tell me something interesting"),
    ]
    assert [
        (message.role, message.content)
        for message in history.list_messages(conversation_id)
    ] == [
        ("user", "Earlier question"),
        ("assistant", "Earlier answer"),
        ("user", "Tell me something interesting"),
        ("assistant", "That sounds fun!"),
    ]


def test_controller_keeps_user_message_but_stores_no_assistant_on_chat_error(
    database_path,
):
    history = HistoryRepository(database_path)
    conversation_id = history.create_conversation("A chat")
    chat = FakeChatService(error=ChatServiceError("Ollama is unavailable"))
    controller = ConversationController(history=history, chat=chat)

    with pytest.raises(ChatServiceError, match="Ollama is unavailable"):
        controller.submit(conversation_id, "Please tell me a joke")

    assert [
        (message.role, message.content)
        for message in history.list_messages(conversation_id)
    ] == [("user", "Please tell me a joke")]


@pytest.mark.parametrize(
    ("error", "expected_detail"),
    [
        (ConnectionError("connection refused"), "Ollama"),
        (ollama.ResponseError("model not found", status_code=404), "model"),
    ],
    ids=["ollama-unavailable", "model-missing"],
)
def test_ollama_failures_become_readable_chat_service_errors(error, expected_detail):
    service = OllamaChatService(client=FakeOllamaClient(error=error))

    with pytest.raises(ChatServiceError) as raised:
        service.reply([])

    message = str(raised.value)
    assert message
    assert expected_detail.lower() in message.lower()
