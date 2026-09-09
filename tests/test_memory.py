from __future__ import annotations

import asyncio
import json

from hermes.memory import Store


def store(tmp_path) -> Store:
    return Store(tmp_path / "m.db")


async def test_session_vide(tmp_path):
    session = await store(tmp_path).load(42)
    assert session.messages == [] and session.model is None


async def test_aller_retour(tmp_path):
    s = store(tmp_path)
    await s.save(1, [{"role": "user", "content": "salut"}], "venice")
    session = await s.load(1)
    assert session.messages[0]["content"] == "salut"
    assert session.model == "venice"


async def test_ecriture_assainie(tmp_path):
    s = store(tmp_path)
    await s.save(1, [{"role": "tool", "tool_call_id": "orphelin", "content": "x"}], None)
    assert (await s.load(1)).messages == []


async def test_historique_corrompu_ne_bloque_pas(tmp_path):
    s = store(tmp_path)
    await s.save(1, [{"role": "user", "content": "x"}], None)
    import sqlite3

    with sqlite3.connect(s.path) as conn:
        conn.execute("UPDATE conversations SET messages = ? WHERE chat_id = 1", ("{pas du json",))
    assert (await s.load(1)).messages == []


async def test_effacement_conserve_le_modele(tmp_path):
    s = store(tmp_path)
    await s.save(1, [{"role": "user", "content": "x"}], "venice")
    await s.clear(1)
    session = await s.load(1)
    assert session.messages == [] and session.model == "venice"


async def test_verrou_par_chat(tmp_path):
    s = store(tmp_path)
    assert s.lock(1) is s.lock(1)
    assert s.lock(1) is not s.lock(2)


async def test_chats_independants(tmp_path):
    s = store(tmp_path)
    await asyncio.gather(
        s.save(1, [{"role": "user", "content": "a"}], None),
        s.save(2, [{"role": "user", "content": "b"}], None),
    )
    assert (await s.load(1)).messages[0]["content"] == "a"
    assert (await s.load(2)).messages[0]["content"] == "b"


def test_appropriation_unique(tmp_path):
    s = store(tmp_path)
    assert s.owners() == set()
    assert s.claim(111, "moi") is True
    assert s.claim(222, "autre") is False
    assert s.owners() == {111}


def test_json_lisible_en_base(tmp_path):
    s = store(tmp_path)
    s.save_blocking(1, [{"role": "user", "content": "éàü"}], None)
    import sqlite3

    with sqlite3.connect(s.path) as conn:
        raw = conn.execute("SELECT messages FROM conversations").fetchone()[0]
    assert json.loads(raw)[0]["content"] == "éàü"
