"""Execution de commandes et de code Python dans le workspace."""

from __future__ import annotations

import os
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


def _cite(chemin: str) -> str:
    """Protege un chemin pour le shell COURANT.

    🚨 `shlex.quote` est une fonction POSIX, et elle est activement nuisible sous Windows :
    voyant les antislashs et les deux-points de `C:\\...\\python.exe`, elle entoure le chemin
    d'APOSTROPHES SIMPLES — que `cmd.exe` ne reconnait pas. Il cherche alors un programme
    litteralement nomme `'C:\\...` et rend « The filename, directory name, or volume label
    syntax is incorrect ». Constate le 10/09 : l'outil `python` du bot etait mort depuis le
    deploiement, alors que `shell` fonctionnait — meme famille que le `os.killpg` POSIX deja
    corrige dans `sandbox.py`.
    """
    if os.name == "nt":
        return '"%s"' % chemin
    return shlex.quote(chemin)


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
            f"{_cite(sys.executable)} {_cite(script.name)}",
            cwd=ctx.workspace,
            timeout=max(1, min(timeout, 3600)),
            output_limit=ctx.output_limit,
            # 🚨 SANS CECI, UN SEUL ACCENT TUE LE SCRIPT. Sous Windows la sortie standard
            # d'un processus fils est en cp1252 : `print("éàü")` leve UnicodeEncodeError et
            # le script meurt — code de retour 1, resultat perdu. Sur un bot qui parle
            # FRANCAIS, c'est une panne quasi systematique. Verifie le 10/09 : « accents :
            # éàü ✅ » plantait, passe avec PYTHONIOENCODING.
            env={"PYTHONIOENCODING": "utf-8"},
        )
    finally:
        script.unlink(missing_ok=True)


TOOLS = (shell, python)
