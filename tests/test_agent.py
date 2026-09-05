import json

from hermes import llm, memory
from hermes.agent import Agent
from hermes.tools import build_registry


class ScriptedClient:
    """Rejoue une liste de reponses preparees, en enregistrant ce qu'il recoit."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen: list[list[dict]] = []

    async def complete(self, system, messages, registry, model):
        self.seen.append([dict(m) for m in messages])
        return self.replies.pop(0)


def _call(name, args, cid="c1"):
    return llm.ToolCall(id=cid, name=name, arguments=json.dumps(args))


def _assistant(text, calls=()):
    msg = {"role": "assistant", "content": text}
    if calls:
        msg["tool_calls"] = [
            {"id": c.id, "type": "function",
             "function": {"name": c.name, "arguments": c.arguments}}
            for c in calls
        ]
    return llm.Reply(text=text, tool_calls=list(calls), assistant_message=msg)


def _agent(settings, replies):
    store = memory.Store(settings.data_dir / "t.db")
    agent = Agent(settings, build_registry(settings), store)
    client = ScriptedClient(replies)
    agent.client_for = lambda p, m: client  # type: ignore[assignment]
    return agent, client, store


async def test_plain_answer_without_tools(settings):
    agent, _, store = _agent(settings, [_assistant("bonjour")])
    run = await agent.respond(1, "salut")
    assert run.text == "bonjour"
    assert run.tool_calls == 0
    assert len((await store.load(1)).messages) == 2


async def test_tool_call_then_answer(settings):
    replies = [
        _assistant("", [_call("write_file", {"path": "x.txt", "content": "ok"})]),
        _assistant("c'est fait"),
    ]
    agent, client, _ = _agent(settings, replies)
    run = await agent.respond(1, "ecris x.txt")

    assert run.text == "c'est fait"
    assert run.tool_calls == 1
    assert (settings.workspace / "x.txt").read_text() == "ok"

    # Le second appel doit voir le resultat de l'outil.
    second = client.seen[1]
    assert second[-1]["role"] == "tool"
    assert second[-1]["tool_call_id"] == "c1"


async def test_parallel_calls_all_get_results(settings):
    calls = [
        _call("write_file", {"path": "a.txt", "content": "a"}, cid="1"),
        _call("write_file", {"path": "b.txt", "content": "b"}, cid="2"),
    ]
    agent, client, _ = _agent(settings, [_assistant("", calls), _assistant("fini")])
    run = await agent.respond(1, "deux fichiers")

    assert run.tool_calls == 2
    tool_msgs = [m for m in client.seen[1] if m["role"] == "tool"]
    assert {m["tool_call_id"] for m in tool_msgs} == {"1", "2"}


async def test_iteration_cap_stops_the_loop(settings):
    settings.max_tool_iterations = 3
    replies = [_assistant("", [_call("list_dir", {})]) for _ in range(3)]
    agent, _, _ = _agent(settings, replies)
    run = await agent.respond(1, "boucle")
    assert run.truncated
    assert "limite" in run.text


async def test_history_is_reused_across_turns(settings):
    agent, client, _ = _agent(settings, [_assistant("un"), _assistant("deux")])
    await agent.respond(1, "premier")
    await agent.respond(1, "second")
    assert [m["content"] for m in client.seen[1]] == ["premier", "un", "second"]


async def test_failed_turn_does_not_poison_history(settings):
    class Boom:
        async def complete(self, *a, **k):
            raise RuntimeError("502 Bad Gateway")

    store = memory.Store(settings.data_dir / "t.db")
    agent = Agent(settings, build_registry(settings), store)
    agent.client_for = lambda p, m: Boom()  # type: ignore[assignment]

    try:
        await agent.respond(1, "question")
    except RuntimeError as exc:
        assert "502" in str(exc)
    else:
        raise AssertionError("l'erreur aurait du remonter")

    assert (await store.load(1)).messages == []
