from hermes.memory import Store, trim


def test_trim_never_orphans_a_tool_result():
    messages = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "res"},
        {"role": "assistant", "content": "r1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "r2"},
    ]
    # Une coupe naive garderait les 3 derniers et laisserait un `tool` orphelin.
    kept = trim(messages, 3)
    assert kept[0]["role"] == "user"
    assert kept[0]["content"] == "q2"


def test_trim_is_a_noop_when_short():
    messages = [{"role": "user", "content": "x"}]
    assert trim(messages, 10) == messages


async def test_store_roundtrip(tmp_path):
    store = Store(tmp_path / "h.db")
    await store.save(42, [{"role": "user", "content": "salut"}], "deepseek", "deepseek-chat")
    session = await store.load(42)
    assert session.provider == "deepseek"
    assert session.messages[0]["content"] == "salut"

    await store.reset(42)
    session = await store.load(42)
    assert session.messages == []
    assert session.provider == "deepseek"  # le choix de modele survit au reset


async def test_load_unknown_chat_is_empty(tmp_path):
    store = Store(tmp_path / "h.db")
    session = await store.load(999)
    assert session.messages == [] and session.model is None
