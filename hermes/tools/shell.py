"""Execution de commandes et de code Python dans le workspace."""

from __future__ import annotations

import shlex
import sys
import uuid
from typing import Any

from ..errors import ToolError
from ..sandbox import run
from . import ToolContext, tool


@tool(
    "shell",
    "Execute une commande shell dans le workspace et renvoie sa sortie combinee "
    "(stdout et stderr) ainsi que son code de retour.",
    {
        "command": {"type": "string", "description": "Commande a executer, via /bin/sh."},
        "timeout": {
            "type": "integer",
            "description": "Delai maximal en secondes ; defaut : celui de la configuration.",
        },
    },
    ["command"],
)
async def shell(ctx: ToolContext, args: dict[str, Any]) -> str:
    command = str(args["command"]).strip()
    if not command:
        raise ToolError("commande vide.")
    timeout = int(args.get("timeout") or ctx.exec_timeout)
    return await run(
        command,
        cwd=ctx.workspace,
        timeout=max(1, min(timeout, 3600)),
        output_limit=ctx.output_limit,
    )


@tool(
    "python",
    "Execute un script Python dans le workspace, dans un processus separe. "
    "Utilise print() pour renvoyer un resultat.",
    {
        "code": {"type": "string", "description": "Code source Python complet."},
        "timeout": {"type": "integer", "description": "Delai maximal en secondes."},
    },
    ["code"],
)
async def python(ctx: ToolContext, args: dict[str, Any]) -> str:
    code = str(args["code"])
    if not code.strip():
        raise ToolError("code vide.")
    # Nom unique : le modele emet souvent plusieurs appels dans le meme tour, et
    # ils sont executes en parallele. Un nom fixe ferait executer a l'un le code
    # de l'autre, sans que rien ne le signale.
    script = ctx.workspace / f".hermes_{uuid.uuid4().hex}.py"
    script.write_text(code, encoding="utf-8")
    timeout = int(args.get("timeout") or ctx.exec_timeout)
    try:
        return await run(
            f"{shlex.quote(sys.executable)} {shlex.quote(script.name)}",
            cwd=ctx.workspace,
            timeout=max(1, min(timeout, 3600)),
            output_limit=ctx.output_limit,
        )
    finally:
        script.unlink(missing_ok=True)


TOOLS = (shell, python)
