"""Persistance des conversations, une base SQLite par installation."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Message = dict[str, Any]

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    chat_id    INTEGER PRIMARY KEY,
    messages   TEXT    NOT NULL DEFAULT '[]',
    provider   TEXT,
    model      TEXT,
    updated_at TEXT    NOT NULL
);
"""


@dataclass
class Session:
    chat_id: int
    messages: list[Message]
    provider: str | None
    model: str | None


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self._connect().close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        return conn

    # -- API asynchrone ----------------------------------------------------- #

    async def load(self, chat_id: int) -> Session:
        return await asyncio.to_thread(self._load, chat_id)

    async def save(
        self, chat_id: int, messages: list[Message], provider: str | None, model: str | None
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save, chat_id, messages, provider, model)

    async def set_model(self, chat_id: int, provider: str, model: str) -> None:
        session = await self.load(chat_id)
        await self.save(chat_id, session.messages, provider, model)

    async def reset(self, chat_id: int) -> None:
        session = await self.load(chat_id)
        await self.save(chat_id, [], session.provider, session.model)

    # -- implementations bloquantes ----------------------------------------- #

    def _load(self, chat_id: int) -> Session:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT messages, provider, model FROM conversations WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return Session(chat_id, [], None, None)
        try:
            messages = json.loads(row[0])
        except json.JSONDecodeError:
            messages = []
        return Session(chat_id, messages, row[1], row[2])

    def _save(
        self, chat_id: int, messages: list[Message], provider: str | None, model: str | None
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO conversations (chat_id, messages, provider, model, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    messages = excluded.messages,
                    provider = excluded.provider,
                    model = excluded.model,
                    updated_at = excluded.updated_at
                """,
                (
                    chat_id,
                    json.dumps(messages, ensure_ascii=False),
                    provider,
                    model,
                    datetime.now(UTC).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()


def trim(messages: list[Message], keep: int) -> list[Message]:
    """Tronque l'historique sans jamais casser une paire tool_call / tool_result.

    Un message ``tool`` orphelin en tete fait echouer la requete chez tous les
    fournisseurs : on recule donc jusqu'au premier ``user`` qui n'est pas un
    porteur de resultats d'outil.
    """
    if len(messages) <= keep:
        return messages

    cut = len(messages) - keep
    while cut < len(messages):
        candidate = messages[cut]
        if candidate.get("role") == "user" and "tool_call_id" not in candidate:
            break
        cut += 1
    if cut >= len(messages):
        return []
    return messages[cut:]
