"""Boucle agentique : appel du modele, execution des outils, iteration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from . import llm, memory, providers
from .config import Settings
from .tools import Registry, ToolContext

log = logging.getLogger(__name__)

#: Rappel appele a chaque etape, pour afficher la progression dans Telegram.
Progress = Callable[[str], Awaitable[None]]


@dataclass
class Run:
    text: str
    steps: list[str] = field(default_factory=list)
    tool_calls: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    truncated: bool = False


class Agent:
    def __init__(self, settings: Settings, registry: Registry, store: memory.Store) -> None:
        self.settings = settings
        self.registry = registry
        self.store = store
        self._clients: dict[tuple[str, str], llm.LLMClient] = {}

    def client_for(self, provider: str, model: str) -> llm.LLMClient:
        key = (provider, model)
        if key not in self._clients:
            spec = providers.get(provider)
            self._clients[key] = llm.build_client(self.settings, spec)
        return self._clients[key]

    async def respond(self, chat_id: int, user_text: str, progress: Progress | None = None) -> Run:
        s = self.settings
        session = await self.store.load(chat_id)
        provider = session.provider or s.provider
        model = session.model or s.model

        history = memory.trim(session.messages, s.history_turns)
        history.append({"role": "user", "content": user_text})

        client = self.client_for(provider, model)
        ctx = ToolContext(settings=s, chat_id=chat_id)
        run = Run(text="")
        reply: llm.Reply | None = None

        for iteration in range(s.max_tool_iterations):
            try:
                reply = await client.complete(s.system_prompt, history, self.registry, model)
            except Exception as exc:
                log.exception("Echec de l'appel au modele")
                # L'historique reste propre : on ne persiste pas un tour casse.
                await self.store.save(chat_id, history[:-1], session.provider, session.model)
                raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc

            for k, v in reply.usage.items():
                run.usage[k] = run.usage.get(k, 0) + v
            history.append(reply.assistant_message)

            if not reply.tool_calls:
                run.text = reply.text
                break

            names = ", ".join(c.name for c in reply.tool_calls)
            run.steps.append(names)
            run.tool_calls += len(reply.tool_calls)
            if progress:
                await progress(f"[{iteration + 1}] {names}")

            # Les appels d'un meme tour sont independants : on les execute en parallele
            # et on renvoie TOUS les resultats dans le meme tour.
            outputs = await asyncio.gather(
                *(self._execute(ctx, call) for call in reply.tool_calls)
            )
            for call, output in zip(reply.tool_calls, outputs):
                history.append(llm.tool_result_message(call, output))
        else:
            run.truncated = True
            run.text = (reply.text if reply else "") or (
                f"J'ai atteint la limite de {s.max_tool_iterations} tours d'outils sans "
                "conclure. Relance-moi avec une consigne plus etroite, ou augmente "
                "HERMES_MAX_TOOL_ITERATIONS."
            )

        await self.store.save(chat_id, history, session.provider, session.model)
        return run

    async def _execute(self, ctx: ToolContext, call: llm.ToolCall) -> str:
        try:
            args = call.parsed()
        except ValueError as exc:
            return f"ERREUR : {exc}"
        log.info("outil %s %s", call.name, str(args)[:200])
        return await self.registry.dispatch(ctx, call.name, args)
