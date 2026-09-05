"""Registre d'outils, expose dans un format neutre.

Les schemas sont du JSON Schema brut : chaque adaptateur de fournisseur
(``hermes.llm``) les traduit vers son propre dialecte de tool-calling.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..config import Settings

Handler = Callable[["ToolContext", dict[str, Any]], Awaitable[str]]


@dataclass
class ToolContext:
    settings: Settings
    chat_id: int


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> str:
        return await self.handler(ctx, args)


class Registry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {t.name: t for t in tools}

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    async def dispatch(self, ctx: ToolContext, name: str, args: dict[str, Any]) -> str:
        tool = self.get(name)
        if tool is None:
            return f"ERREUR : outil inconnu {name!r}. Disponibles : {', '.join(self.names())}"
        try:
            return await tool.run(ctx, args)
        except Exception as exc:  # noqa: BLE001 - l'erreur est rendue au modele, pas levee
            return f"ERREUR pendant {name} : {type(exc).__name__}: {exc}"


def build_registry(settings: Settings) -> Registry:
    from . import files, system, web

    tools = [*system.TOOLS, *files.TOOLS, *web.build_tools(settings)]
    return Registry(tools)
