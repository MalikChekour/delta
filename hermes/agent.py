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
            searxng_url=settings.searxng_url,
            search_backends=settings.search_backends,
            chat_id=chat_id,
        )
        run = Run()
        # 🚨 GARDE-BOUCLE. Mesure du 11/09 : sur « teneur en vitamine C du persil », l'agent
        # trouvait la reponse en 3 etapes puis appelait `retiens` SIX FOIS de suite avec des
        # arguments identiques — 6 etapes sur 9 gaspillees. Sur quatre executions de la meme
        # tache : 3, 6, 7 et 20 etapes, la derniere epuisant la limite sans conclure.
        # Le probleme n'est pas la competence, c'est la CONVERGENCE. Reexecuter un appel
        # identique ne peut rien apprendre de neuf : on rend le resultat deja obtenu, et on
        # le DIT au modele pour qu'il cesse et conclue.
        deja: dict[tuple[str, str], tuple[int, str]] = {}

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
                    run.text = reply.text.strip() or _reponse_vide(reply.finish_reason)
                    break

                names = ", ".join(call.name for call in reply.tool_calls)
                run.steps.append(names)
                run.tool_calls += len(reply.tool_calls)
                if progress:
                    await progress(f"[{iteration + 1}/{settings.max_tool_iterations}] {names}")

                # Les appels d'un meme tour sont independants : on les execute en
                # parallele et on rend tous les resultats dans le meme lot.
                outputs = await asyncio.gather(
                    *(self._execute(context, call, deja, iteration + 1)
                      for call in reply.tool_calls)
                )
                # 🚨 AVERTIR AVANT LE MUR. Le garde-boucle ci-dessus n'attrape que les
                # appels IDENTIQUES ; il ne peut rien contre une suite de recherches
                # differentes mais steriles — mesure du 11/09 : « teneur en vitamine C »
                # consommait encore les 20 tours en enchainant des requetes voisines.
                # Sans cet avertissement, le modele decouvre la limite en la heurtant, et
                # l'utilisateur recoit « j'ai atteint la limite » au lieu d'une reponse.
                # 🚨 ET PAS AVANT D'AVOIR TRAVAILLE. Premiere version fautive, attrapee par
                # `test_boucle_avec_outil` : avec un budget de 4 tours, `reste <= 3` etait
                # vrai des le PREMIER appel — l'agent etait somme de conclure avant d'avoir
                # commence. On n'avertit donc qu'une fois passe le gros du budget.
                reste = settings.max_tool_iterations - iteration - 1
                entame = (iteration + 1) >= settings.max_tool_iterations * 0.6
                if 0 < reste <= 3 and entame and outputs:
                    outputs[-1] += (
                        f"\n\n[Il te reste {reste} tour(s) d'outils. Arrete de chercher : "
                        f"conclus MAINTENANT avec ce que tu as deja, en disant ce qui est "
                        f"etabli et ce qui ne l'est pas.]"
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

    async def _execute(self, context: ToolContext, call: llm.ToolCall,
                       deja: dict[tuple[str, str], tuple[int, str]] | None = None,
                       tour: int = 0) -> str:
        try:
            arguments = call.parsed()
        except ValueError as exc:
            return f"ERREUR : {exc}"

        if deja is not None:
            import json as _json

            cle = (call.name, _json.dumps(arguments, sort_keys=True, ensure_ascii=False))
            vu = deja.get(cle)
            if vu is not None:
                precedent, resultat = vu
                log.info("outil %s : appel IDENTIQUE au tour %d, non reexecute",
                         call.name, precedent)
                # 🚨 On ne se contente pas de rendre le meme resultat : on dit au modele
                # qu'il se repete. Sans cette phrase, il relance le meme appel indefiniment
                # — six fois d'affilee sur `retiens` lors de la mesure du 11/09.
                return (
                    f"[Appel IDENTIQUE deja effectue au tour {precedent} : non reexecute. "
                    f"Le resultat est inchange, le refaire n'apprendra rien. Sers-toi de ce "
                    f"qui suit et REPONDS.]\n{resultat}"
                )

        log.info("outil %s(%s)", call.name, str(arguments)[:160])
        resultat = await self.registry.dispatch(context, call.name, arguments)
        if deja is not None:
            deja[cle] = (tour, resultat)
        return resultat


def _reponse_vide(finish_reason: str | None) -> str:
    """Message affiche quand le modele ne renvoie ni texte ni appel d'outil.

    Cela arrive : reponse tronquee, ou modele qui part en vrille. Sans ce
    garde-fou, l'utilisateur ne verrait rien du tout et croirait a une panne.
    """
    if finish_reason == "length":
        return (
            "Ma reponse a ete coupee avant d'avoir commence : la limite de jetons est "
            "atteinte. Augmente HERMES_MAX_TOKENS, ou demande quelque chose de plus court."
        )
    return (
        "Le modele n'a rien renvoye. Reformule ta demande, ou change de modele "
        "avec /model venice."
    )


def format_error(exc: BaseException) -> str:
    """Message d'echec destine a l'utilisateur, sans traceback."""
    if isinstance(exc, HermesError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"
