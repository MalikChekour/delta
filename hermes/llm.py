"""Acces aux modeles : un client par route, un routeur qui bascule.

Un seul protocole est implemente, celui d'OpenAI ``/chat/completions``, que
parlent GLM/Z.ai, Venice, OpenRouter, Chutes, vLLM, Ollama et LM Studio.

Le ``Router`` est la piece qui evite les pannes : il essaie les routes dans
l'ordre, retient celle qui repond, et ne re-essaie une route morte qu'apres un
delai de quarantaine. Une cle absente, un serveur local eteint ou un modele
retire de l'hebergeur ne coupent donc pas le service tant qu'une route tient.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .errors import ConfigError, ProviderError
from .history import Message
from .providers import PROVIDERS, Route, resolve
from .tools import Registry

log = logging.getLogger(__name__)

#: Duree pendant laquelle une route en echec passager est ecartee (debit depasse,
#: panne serveur). Assez pour ne pas insister, assez court pour revenir vite.
QUARANTINE_SECONDS = 300

#: Une panne structurelle — cle refusee, modele absent du catalogue — ne se
#: resoudra pas d'elle-meme : inutile de la ressayer au meme rythme.
QUARANTINE_PERMANENT = 3600


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON brut : les modeles l'echappent de facons variables

    def parsed(self) -> dict[str, Any]:
        raw = (self.arguments or "").strip()
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"arguments JSON invalides pour {self.name} ({exc}). "
                "Reemets l'appel avec un objet JSON valide."
            ) from exc
        return value if isinstance(value, dict) else {"value": value}

    def as_message_part(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments or "{}"},
        }


@dataclass
class Reply:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_message: Message = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str | None = None
    route: Route | None = None


def tool_result(call_id: str, output: str) -> Message:
    return {"role": "tool", "tool_call_id": call_id, "content": output}


def _schemas(registry: Registry) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in registry
    ]


def _strip_for_wire(messages: list[Message]) -> list[Message]:
    """Retire les champs internes qu'aucun fournisseur n'attend."""
    clean: list[Message] = []
    for message in messages:
        clean.append({k: v for k, v in message.items() if not k.startswith("_")})
    return clean


class Client:
    """Client d'une route unique."""

    def __init__(self, settings: Settings, route: Route) -> None:
        from openai import AsyncOpenAI

        spec = PROVIDERS.get(route.provider)
        if spec is None:
            raise ConfigError(f"Fournisseur inconnu : {route.provider!r}")
        base_url = spec.base_url
        if spec.name == "custom":
            base_url = settings.custom_base_url
            if not base_url:
                raise ConfigError(
                    "provider 'custom' exige HERMES_CUSTOM_BASE_URL (ex. http://host:8000/v1)."
                )
        key = spec.api_key()
        if key is None:
            raise ConfigError(
                f"{spec.name} requiert la variable {spec.api_key_env}. Renseigne-la dans .env."
            )

        self.route = route
        self.settings = settings
        self.spec = spec
        self._client = AsyncOpenAI(
            api_key=key,
            base_url=base_url,
            timeout=settings.request_timeout,
            max_retries=2,
            default_headers=dict(spec.extra_headers) or None,
        )

    async def complete(self, system: str, messages: list[Message], registry: Registry) -> Reply:
        payload: dict[str, Any] = {
            "model": self.route.model,
            "messages": [{"role": "system", "content": system}, *_strip_for_wire(messages)],
            "max_tokens": self.settings.max_tokens,
        }
        if len(registry):
            payload["tools"] = _schemas(registry)
            payload["tool_choice"] = "auto"
        if self.settings.temperature is not None:
            payload["temperature"] = self.settings.temperature
        if self.spec.extra_body:
            payload["extra_body"] = dict(self.spec.extra_body)

        response = await self._call(payload)
        return self._to_reply(response)

    async def _call(self, payload: dict[str, Any]):
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            NotFoundError,
            RateLimitError,
        )

        try:
            return await self._client.chat.completions.create(**payload)
        except AuthenticationError as exc:
            raise ProviderError(
                f"{self.route} : cle API refusee ({self.spec.api_key_env}).", retryable=False
            ) from exc
        except NotFoundError as exc:
            raise ProviderError(
                f"{self.route} : modele introuvable chez ce fournisseur.", retryable=False
            ) from exc
        except RateLimitError as exc:
            raise ProviderError(f"{self.route} : quota ou debit depasse.", retryable=True) from exc
        except (APIConnectionError, APITimeoutError) as exc:
            raise ProviderError(f"{self.route} : injoignable ({exc}).", retryable=True) from exc
        except APIStatusError as exc:
            detail = str(getattr(exc, "message", "") or exc)[:300]
            # 4xx = requete fautive (ne pas insister) ; 5xx = panne passagere.
            retryable = exc.status_code >= 500
            raise ProviderError(
                f"{self.route} : HTTP {exc.status_code} — {detail}", retryable=retryable
            ) from exc

    def _to_reply(self, response: Any) -> Reply:
        if not getattr(response, "choices", None):
            raise ProviderError(f"{self.route} : reponse sans choix.", retryable=True)
        choice = response.choices[0]
        message = choice.message

        calls: list[ToolCall] = []
        vus: set[str] = set()
        for raw in getattr(message, "tool_calls", None) or []:
            function = getattr(raw, "function", None)
            if function is None or not getattr(function, "name", ""):
                continue  # certains hebergeurs emettent des blocs vides
            # Un identifiant absent ou repete casserait l'appariement appel /
            # resultat : on le rend unique ici plutot que de le subir plus loin.
            call_id = getattr(raw, "id", "") or f"call_{len(calls)}"
            while call_id in vus:
                call_id = f"{call_id}_{len(vus)}"
            vus.add(call_id)
            calls.append(
                ToolCall(id=call_id, name=function.name, arguments=function.arguments or "{}")
            )

        text = (message.content or "").strip()
        assistant: Message = {"role": "assistant", "content": text}
        if calls:
            assistant["tool_calls"] = [call.as_message_part() for call in calls]

        usage = {}
        raw_usage = getattr(response, "usage", None)
        if raw_usage:
            usage = {
                "input": getattr(raw_usage, "prompt_tokens", 0) or 0,
                "output": getattr(raw_usage, "completion_tokens", 0) or 0,
            }

        return Reply(
            text=text,
            tool_calls=calls,
            assistant_message=assistant,
            usage=usage,
            finish_reason=getattr(choice, "finish_reason", None),
            route=self.route,
        )


class Router:
    """Choisit une route vivante parmi celles d'une chaine de modeles."""

    def __init__(self, settings: Settings, chain: tuple[str, ...] | None = None) -> None:
        self.settings = settings
        self.chain = tuple(chain or settings.model_chain)
        self._clients: dict[Route, Client] = {}
        self._quarantine: dict[Route, float] = {}
        self._preferred: Route | None = None

    # -- selection ----------------------------------------------------------

    def candidates(self) -> list[Route]:
        """Routes utilisables, sans doublon, dans l'ordre de preference."""
        seen: set[Route] = set()
        routes: list[Route] = []
        for spec in self.chain:
            for route in resolve(spec):
                if route.usable and route not in seen:
                    seen.add(route)
                    routes.append(route)
        if self._preferred in routes:
            routes.remove(self._preferred)
            routes.insert(0, self._preferred)
        return routes

    def _available(self) -> list[Route]:
        now = time.monotonic()
        fresh = [r for r in self.candidates() if self._quarantine.get(r, 0.0) <= now]
        # Toutes en quarantaine : on les re-essaie plutot que de ne rien faire.
        return fresh or self.candidates()

    def _client(self, route: Route) -> Client:
        client = self._clients.get(route)
        if client is None:
            client = Client(self.settings, route)
            self._clients[route] = client
        return client

    # -- appel --------------------------------------------------------------

    async def complete(self, system: str, messages: list[Message], registry: Registry) -> Reply:
        routes = self._available()
        if not routes:
            raise ConfigError(
                f"Aucune route servable pour {', '.join(self.chain)}. "
                "Renseigne une cle API ou demarre un serveur local ; `hermes doctor` detaille."
            )

        problems: list[str] = []
        for route in routes:
            try:
                client = self._client(route)
            except ConfigError as exc:
                problems.append(str(exc))
                continue
            try:
                reply = await client.complete(system, messages, registry)
            except ProviderError as exc:
                problems.append(str(exc))
                duree = QUARANTINE_SECONDS if exc.retryable else QUARANTINE_PERMANENT
                self._quarantine[route] = time.monotonic() + duree
                log.warning("Route ecartee %s pour %ds : %s", route, duree, exc)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - une route ne doit jamais tuer le tour
                problems.append(f"{route} : {type(exc).__name__}: {exc}")
                self._quarantine[route] = time.monotonic() + QUARANTINE_PERMANENT
                log.exception("Route %s en erreur inattendue", route)
                continue

            self._preferred = route
            self._quarantine.pop(route, None)
            return reply

        raise ProviderError(
            "Toutes les routes ont echoue :\n" + "\n".join(f"• {p}" for p in problems)
        )
