"""Persistance des conversations : une base SQLite, un verrou par chat.

Le verrou par chat est la raison d'etre de ce module. Telegram livre les
messages en parallele ; deux tours simultanes sur le meme chat liraient le meme
historique et le dernier a ecrire ecraserait l'autre. On serialise donc par
``chat_id`` — deux chats differents restent parallelisables.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .history import Message, sanitize

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    chat_id    INTEGER PRIMARY KEY,
    messages   TEXT    NOT NULL DEFAULT '[]',
    model      TEXT,
    updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS owners (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    claimed_at TEXT NOT NULL
);
"""


@dataclass
class Session:
    chat_id: int
    messages: list[Message] = field(default_factory=list)
    model: str | None = None


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def lock(self, chat_id: int) -> asyncio.Lock:
        """Verrou du chat. A tenir pendant toute la sequence lire → agir → ecrire."""
        return self._locks[chat_id]

    async def load(self, chat_id: int) -> Session:
        return await asyncio.to_thread(self._load, chat_id)

    async def save(self, chat_id: int, messages: list[Message], model: str | None) -> None:
        await asyncio.to_thread(self._save, chat_id, sanitize(messages), model)

    def save_blocking(self, chat_id: int, messages: list[Message], model: str | None) -> None:
        """Ecriture synchrone, pour les chemins ou l'on ne peut plus rien attendre
        (tour annule : la boucle d'evenements refuserait un nouvel await)."""
        self._save(chat_id, sanitize(messages), model)

    async def set_model(self, chat_id: int, model: str | None) -> None:
        """Change le modele sans toucher a l'historique.

        Ecrire la seule colonne concernee evite de prendre le verrou du chat :
        sinon un /model envoye pendant un tour long resterait sans reponse
        jusqu'a la fin de ce tour, et reecrirait par-dessus un historique
        entre-temps perime.
        """
        await asyncio.to_thread(self._set_model, chat_id, model)

    async def clear(self, chat_id: int) -> None:
        async with self.lock(chat_id):
            session = await self.load(chat_id)
            await self.save(chat_id, [], session.model)

    # -- proprietaires (appropriation au premier contact) --------------------

    def owners(self) -> set[int]:
        with closing(self._connect()) as conn:
            return {row[0] for row in conn.execute("SELECT user_id FROM owners")}

    def claim(self, user_id: int, username: str | None) -> bool:
        """Enregistre un proprietaire si aucun ne l'est encore.

        Renvoie ``True`` si l'appropriation a eu lieu. L'insertion conditionnelle
        se fait en une seule instruction : deux messages simultanes ne peuvent
        pas produire deux proprietaires.
        """
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO owners (user_id, username, claimed_at)
                SELECT ?, ?, ?
                WHERE NOT EXISTS (SELECT 1 FROM owners)
                """,
                (user_id, username, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            return cursor.rowcount > 0

    # -- implementations bloquantes, executees hors boucle d'evenements ------

    def _load(self, chat_id: int) -> Session:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT messages, model FROM conversations WHERE chat_id = ?", (chat_id,)
            ).fetchone()
        if row is None:
            return Session(chat_id)
        try:
            messages = json.loads(row[0])
            if not isinstance(messages, list):
                raise ValueError("racine JSON inattendue")
        except (json.JSONDecodeError, ValueError):
            log.warning("Historique illisible pour le chat %s : reinitialise.", chat_id)
            messages = []
        return Session(chat_id, sanitize(messages), row[1])

    def _set_model(self, chat_id: int, model: str | None) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO conversations (chat_id, messages, model, updated_at)
                VALUES (?, '[]', ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    model      = excluded.model,
                    updated_at = excluded.updated_at
                """,
                (chat_id, model, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )

    def _save(self, chat_id: int, messages: list[Message], model: str | None) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO conversations (chat_id, messages, model, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    messages   = excluded.messages,
                    model      = excluded.model,
                    updated_at = excluded.updated_at
                """,
                (
                    chat_id,
                    json.dumps(messages, ensure_ascii=False),
                    model,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
