"""Doublures : aucun test ne doit sortir sur le reseau."""

from __future__ import annotations

from hermes.errors import ProviderError
from hermes.llm import Reply, ToolCall
from hermes.providers import Route


class FakeRouter:
    """Rejoue une liste de reponses preparees, et enregistre les appels."""

    def __init__(self, replies: list[Reply | Exception]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    async def complete(self, system, messages, registry):
        self.calls.append([dict(m) for m in messages])
        if not self.replies:
            raise AssertionError("plus de reponse preparee")
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def text_reply(text: str) -> Reply:
    return Reply(
        text=text,
        assistant_message={"role": "assistant", "content": text},
        route=Route("venice", "test"),
    )


def tool_reply(name: str, arguments: str = "{}", call_id: str = "c1") -> Reply:
    call = ToolCall(id=call_id, name=name, arguments=arguments)
    return Reply(
        text="",
        tool_calls=[call],
        assistant_message={
            "role": "assistant",
            "content": "",
            "tool_calls": [call.as_message_part()],
        },
        route=Route("venice", "test"),
    )


def failure(message: str = "route morte", *, retryable: bool = True) -> ProviderError:
    return ProviderError(message, retryable=retryable)
