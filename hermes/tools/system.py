"""Outils d'execution : shell et Python."""

from __future__ import annotations

from typing import Any

from .. import sandbox
from . import Tool, ToolContext


async def _shell(ctx: ToolContext, args: dict[str, Any]) -> str:
    command = (args.get("command") or "").strip()
    if not command:
        return "ERREUR : parametre 'command' vide."
    s = ctx.settings
    timeout = min(int(args.get("timeout") or s.exec_timeout), 900)
    result = await sandbox.run_shell(
        command,
        cwd=s.workspace,
        timeout=timeout,
        allow_network=s.allow_network_in_tools,
    )
    return result.render(s.output_limit)


async def _python(ctx: ToolContext, args: dict[str, Any]) -> str:
    code = args.get("code") or ""
    if not code.strip():
        return "ERREUR : parametre 'code' vide."
    s = ctx.settings
    timeout = min(int(args.get("timeout") or s.exec_timeout), 900)
    result = await sandbox.run_python(
        code,
        cwd=s.workspace,
        timeout=timeout,
        allow_network=s.allow_network_in_tools,
    )
    return result.render(s.output_limit)


TOOLS = [
    Tool(
        name="shell",
        description=(
            "Execute une commande shell dans le workspace et renvoie stdout, stderr et le "
            "code de sortie. Sert a installer des paquets, lancer des tests, manipuler git, "
            "compiler. La session n'est pas persistante : enchaine avec && plutot que cd."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "La commande a executer."},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout en secondes (defaut : configure, max 900).",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        handler=_shell,
    ),
    Tool(
        name="python",
        description=(
            "Execute un script Python dans le workspace et renvoie sa sortie. Utilise-le "
            "pour calculer, analyser des donnees ou verifier une implementation. Affiche "
            "les resultats avec print() : la valeur de la derniere expression n'est pas "
            "retournee."
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Le code Python a executer."},
                "timeout": {
                    "type": "integer",
                    "description": "Timeout en secondes (defaut : configure, max 900).",
                },
            },
            "required": ["code"],
            "additionalProperties": False,
        },
        handler=_python,
    ),
]
