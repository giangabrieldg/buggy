from __future__ import annotations

from typing import Callable, Iterator, Protocol, Sequence

import ollama

from personal_assistant.history import ChatMessage, HistoryRepository


_SYSTEM_PROMPT = (
    "You are Buggy, a friendly, casual personal assistant. Keep the conversation natural "
    "and welcoming. The user may simply want company or an interesting chat. "
    "Answer clearly and helpfully, without claiming to control the computer."
)


class ChatServiceError(RuntimeError):
    """A user-readable error raised when the local chat service fails."""


class ChatClient(Protocol):
    def chat(
        self, *, model: str, messages: list[dict[str, str]], stream: bool = False
    ) -> object: ...


class OllamaChatService:
    """Call an Ollama model running on this computer."""

    def __init__(self, model: str = "qwen3:1.7b", client: ChatClient | None = None):
        self.model = model
        self._client = client or ollama.Client(host="http://127.0.0.1:11434")

    def reply(self, messages: Sequence[ChatMessage]) -> str:
        ollama_messages = self._ollama_messages(messages)
        try:
            response = self._client.chat(model=self.model, messages=ollama_messages)
        except ollama.ResponseError as error:
            if error.status_code == 404 or "model" in str(error).lower() and "not found" in str(error).lower():
                raise ChatServiceError(
                    f"The Ollama model '{self.model}' was not found. Check that it is installed."
                ) from error
            raise ChatServiceError(f"Ollama returned an error: {error}") from error
        except Exception as error:
            raise ChatServiceError(
                f"Ollama is unavailable or the local request failed: {error}"
            ) from error

        content = self._response_content(response)
        if not isinstance(content, str):
            raise ChatServiceError("Ollama returned a response without message text.")
        return content

    def reply_stream(self, messages: Sequence[ChatMessage]) -> Iterator[str]:
        """Yield visible answer chunks from the local model only."""
        visible: list[str] = []
        try:
            response = self._client.chat(
                model=self.model,
                messages=self._ollama_messages(messages),
                stream=True,
            )
            for chunk in response:
                content = self._response_content(chunk)
                if content is None or content == "":
                    continue
                if not isinstance(content, str):
                    raise ChatServiceError("Ollama returned a response without message text.")
                visible.append(content)
                yield content
        except ChatServiceError:
            raise
        except ollama.ResponseError as error:
            if error.status_code == 404 or (
                "model" in str(error).lower() and "not found" in str(error).lower()
            ):
                raise ChatServiceError(
                    f"The Ollama model '{self.model}' was not found. Check that it is installed."
                ) from error
            raise ChatServiceError(f"Ollama returned an error: {error}") from error
        except Exception as error:
            raise ChatServiceError(
                f"Ollama is unavailable or the local request failed: {error}"
            ) from error

        if not "".join(visible).strip():
            raise ChatServiceError("Ollama returned a response without message text.")

    @staticmethod
    def _ollama_messages(messages: Sequence[ChatMessage]) -> list[dict[str, str]]:
        ollama_messages = [{"role": "system", "content": _SYSTEM_PROMPT}]
        ollama_messages.extend(
            {"role": message.role, "content": message.content}
            for message in messages
        )
        return ollama_messages

    @staticmethod
    def _response_content(response: object) -> object:
        if isinstance(response, dict):
            message = response.get("message")
            return message.get("content") if isinstance(message, dict) else None
        message = getattr(response, "message", None)
        return getattr(message, "content", None) if message is not None else None


class ConversationController:
    """Coordinate local model replies with persistent conversation history."""

    def __init__(self, history: HistoryRepository, chat: OllamaChatService):
        self.history = history
        self.chat = chat

    def submit(self, conversation_id: str, text: str) -> str:
        if not text.strip():
            raise ValueError("Message cannot be empty")

        self.history.add_message(conversation_id, "user", text)
        messages = self.history.list_messages(conversation_id)
        reply = self.chat.reply(messages)
        self.history.add_message(conversation_id, "assistant", reply)
        return reply

    def submit_stream(
        self, conversation_id: str, text: str, on_chunk: Callable[[str], None]
    ) -> str:
        """Persist a user turn, stream visible chunks, then save a complete answer."""
        if not text.strip():
            raise ValueError("Message cannot be empty")

        self.history.add_message(conversation_id, "user", text)
        messages = self.history.list_messages(conversation_id)
        chunks: list[str] = []
        for chunk in self.chat.reply_stream(messages):
            chunks.append(chunk)
            on_chunk(chunk)
        reply = "".join(chunks)
        if not reply.strip():
            raise ChatServiceError("Ollama returned a response without message text.")
        self.history.add_message(conversation_id, "assistant", reply)
        return reply
