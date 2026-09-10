"""Registre d'outils.

Un outil = un nom, une description, un schema JSON, une coroutine. Le registre
valide les arguments avant appel et transforme toute erreur en texte renvoye au
modele : un outil qui echoue ne doit jamais interrompre la boucle de l'agent,
seulement lui apprendre quelque chose.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import ToolError

log = logging.getLogger(__name__)


@dataclass
class ToolContext:
    """Ce qu'un outil a le droit de connaitre du monde exterieur."""

    workspace: Path
    exec_timeout: int
    output_limit: int
    request_timeout: int
    search_url: str
    searxng_url: str = ""
    search_backends: tuple[str, ...] = ("auto",)
    chat_id: int = 0


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[str] | str]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler

    @property
    def required(self) -> list[str]:
        return list(self.parameters.get("required", []))

    def check(self, arguments: dict[str, Any]) -> None:
        missing = [key for key in self.required if arguments.get(key) in (None, "")]
        if missing:
            raise ToolError(f"argument(s) manquant(s) : {', '.join(missing)}")
        known = set(self.parameters.get("properties", {}))
        unknown = [key for key in arguments if key not in known]
        if unknown:
            # On ne rejette pas : les modeles ajoutent parfois des champs parasites.
            log.debug("%s : arguments ignores %s", self.name, unknown)


def tool(
    name: str, description: str, properties: dict[str, Any], required: list[str] | None = None
) -> Callable[[Handler], Tool]:
    """Decorateur : transforme une fonction en outil declare."""

    def wrap(handler: Handler) -> Tool:
        return Tool(
            name=name,
            description=description,
            parameters={
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
            handler=handler,
        )

    return wrap


@dataclass
class Registry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def add(self, *items: Tool) -> None:
        for item in items:
            if item.name in self.tools:
                raise ValueError(f"Outil deja enregistre : {item.name}")
            self.tools[item.name] = item

    def __iter__(self) -> Iterator[Tool]:
        return iter(self.tools.values())

    def __len__(self) -> int:
        return len(self.tools)

    def __contains__(self, name: object) -> bool:
        return name in self.tools

    async def dispatch(self, ctx: ToolContext, name: str, arguments: dict[str, Any]) -> str:
        """Appelle un outil. Renvoie toujours du texte, jamais une exception."""
        item = self.tools.get(name)
        if item is None:
            known = ", ".join(sorted(self.tools))
            return f"ERREUR : outil inconnu {name!r}. Outils disponibles : {known}."
        try:
            item.check(arguments)
            # Un outil synchrone lit des fichiers ou parcourt des dossiers : execute
            # dans la boucle d'evenements, il gelerait tout le bot — y compris les
            # autres conversations et la relance du polling Telegram.
            if inspect.iscoroutinefunction(item.handler):
                result = await item.handler(ctx, arguments)
            else:
                result = await asyncio.to_thread(item.handler, ctx, arguments)
                if inspect.isawaitable(result):
                    result = await result
            text = result if isinstance(result, str) else str(result)
            return text or "[aucune sortie]"
        except ToolError as exc:
            return f"ERREUR : {exc}"
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - le modele doit voir l'erreur, pas planter
            log.exception("Outil %s en erreur", name)
            return f"ERREUR : {type(exc).__name__}: {exc}"


def build_registry(*, enable_shell: bool = True, enable_web: bool = True) -> Registry:
    from . import code, files, savoir, shell, web

    registry = Registry()
    registry.add(*files.TOOLS)
    # Memoire de connaissances : elle vit dans le workspace, comme les fichiers.
    registry.add(*savoir.TOOLS)
    # Les outils de code cherchent et modifient DANS le workspace, comme les outils de
    # fichiers : ils suivent donc le meme interrupteur, pas celui du shell.
    registry.add(*code.TOOLS)
    if enable_shell:
        registry.add(*shell.TOOLS)
    if enable_web:
        registry.add(*web.TOOLS)
    return registry
