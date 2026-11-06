from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import uuid4


@dataclass(frozen=True)
class ConversationSummary:
    conversation_id: str
    title: str
    created_at: str


@dataclass(frozen=True)
class ChatMessage:
    conversation_id: str
    role: str
    content: str
    created_at: str


class HistoryRepository:
    """Persist conversations and their messages in a local SQLite database."""

    _SCHEMA_VERSION = 1

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_schema(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS history_schema_version (
                    version INTEGER NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT version FROM history_schema_version LIMIT 1"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO history_schema_version (version) VALUES (?)",
                    (self._SCHEMA_VERSION,),
                )
            elif row["version"] != self._SCHEMA_VERSION:
                raise RuntimeError(
                    f"Unsupported history schema version: {row['version']}"
                )

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (conversation_id)
                        REFERENCES conversations (conversation_id)
                        ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS messages_by_conversation
                ON messages (conversation_id, id)
                """
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def create_conversation(self, title: str | None = None) -> str:
        conversation_id = uuid4().hex
        normalized_title = title.strip() if title and title.strip() else "New conversation"
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO conversations (conversation_id, title, created_at)
                VALUES (?, ?, ?)
                """,
                (conversation_id, normalized_title, self._now()),
            )
        return conversation_id

    def add_message(self, conversation_id: str, role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("Message role must be 'user' or 'assistant'")
        if not isinstance(content, str):
            raise TypeError("Message content must be a string")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO messages (conversation_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, role, content, self._now()),
            )

    def list_recent_conversations(
        self, limit: int = 20
    ) -> list[ConversationSummary]:
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("Conversation limit must be a positive integer")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT conversation_id, title, created_at
                FROM conversations
                ORDER BY rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            ConversationSummary(
                conversation_id=row["conversation_id"],
                title=row["title"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def list_messages(self, conversation_id: str) -> list[ChatMessage]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT conversation_id, role, content, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id ASC
                """,
                (conversation_id,),
            ).fetchall()
        return [
            ChatMessage(
                conversation_id=row["conversation_id"],
                role=row["role"],
                content=row["content"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def delete_conversation(self, conversation_id: str) -> None:
        """Delete one conversation; SQLite cascades to its messages."""
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (conversation_id,),
            )

    def delete_all_conversations(self) -> None:
        """Delete all conversations and their messages, preserving other tables."""
        with self._connection() as connection:
            connection.execute("DELETE FROM conversations")
