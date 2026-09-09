"""Boucle agentique : appeler le modele, executer les outils, recommencer.

Trois garanties tiennent ici :

* l'historique persiste toujours dans un etat conforme (``history.sanitize``),
  meme si le tour est interrompu par une erreur ou une annulation ;
* un tour est serialise par chat, donc deux messages envoyes coup sur coup ne
  se marchent pas dessus ;
* aucune erreur d'outil ne fait sortir de la boucle : elle est rendue au modele
  sous forme de texte, a charge pour lui de corriger.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from . import history as hist
from . import llm
from .config import Settings
from .errors import HermesError
from .memory import Store
from .tools import Registry, ToolContext

log = logging.getLogger(__name__)

#: Rappel de progression, appele entre deux tours d'outils.
Progress = Callable[[str], Awaitable[None]]


@dataclass
class Run:
    text: str = ""
    steps: list[str] = field(default_factory=list)
    tool_calls: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    route: str = ""
    truncated: bool = False


class Agent:
    def __init__(self, settings: Settings, registry: Registry, store: Store) -> None:
        self.settings = settings
        self.registry = registry
        self.store = store
        self._routers: dict[tuple[str, ...], llm.Router] = {}

    def router(self, chain: tuple[str, ...]) -> llm.Router:
        router = self._routers.get(chain)
        if router is None:
            router = llm.Router(self.settings, chain)
            self._routers[chain] = router
        return router

    async def respond(self, chat_id: int, user_text: str, progress: Progress | None = None) -> Run:
        async with self.store.lock(chat_id):
            return await self._respond(chat_id, user_text, progress)

    async def _respond(self, chat_id: int, user_text: str, progress: Progress | None) -> Run:
        settings = self.settings
        session = await self.store.load(chat_id)
        chain = (session.model,) if session.model else settings.model_chain
        router = self.router(tuple(chain))

        messages = hist.trim(session.messages, settings.history_messages)
        messages.append({"role": "user", "content": user_text})

        context = ToolContext(
            workspace=settings.workspace,
            exec_timeout=settings.exec_timeout,
            output_limit=settings.output_limit,
            request_timeout=settings.request_timeout,
            search_url=settings.search_url,
            chat_id=chat_id,
        )
        run = Run()

        try:
            for iteration in range(settings.max_tool_iterations):
                reply = await router.complete(
                    settings.system_prompt, hist.sanitize(messages), self.registry
                )
                run.route = str(reply.route or "")
                for key, value in reply.usage.items():
                    run.usage[key] = run.usage.get(key, 0) + value
                messages.append(reply.assistant_message)

                if not reply.tool_calls:
                    run.text = reply.text
                    break

                names = ", ".join(call.name for call in reply.tool_calls)
                run.steps.append(names)
                run.tool_calls += len(reply.tool_calls)
                if progress:
                    await progress(f"[{iteration + 1}/{settings.max_tool_iterations}] {names}")

                # Les appels d'un meme tour sont independants : on les execute en
                # parallele et on rend tous les resultats dans le meme lot.
                outputs = await asyncio.gather(
                    *(self._execute(context, call) for call in reply.tool_calls)
                )
                for call, output in zip(reply.tool_calls, outputs, strict=True):
                    messages.append(llm.tool_result(call.id, output))
            else:
                run.truncated = True
                run.text = run.text or (
                    f"J'ai atteint la limite de {settings.max_tool_iterations} tours d'outils "
                    "sans conclure. Redemande avec une consigne plus etroite, ou augmente "
                    "HERMES_MAX_TOOL_ITERATIONS."
                )
        finally:
            # Quoi qu'il arrive — succes, erreur du fournisseur, annulation — on
            # ecrit un historique conforme. C'est ce qui empeche une panne
            # ponctuelle de corrompre la conversation pour de bon.
            try:
                await self.store.save(chat_id, messages, session.model)
            except asyncio.CancelledError:
                # Tour annule : plus rien n'est attendable, on ecrit en direct.
                self.store.save_blocking(chat_id, messages, session.model)
                raise

        return run

    async def _execute(self, context: ToolContext, call: llm.ToolCall) -> str:
        try:
            arguments = call.parsed()
        except ValueError as exc:
            return f"ERREUR : {exc}"
        log.info("outil %s(%s)", call.name, str(arguments)[:160])
        return await self.registry.dispatch(context, call.name, arguments)


def format_error(exc: BaseException) -> str:
    """Message d'echec destine a l'utilisateur, sans traceback."""
    if isinstance(exc, HermesError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"
