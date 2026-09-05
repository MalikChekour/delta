"""Execution locale bornee : cwd force sur le workspace, timeout, sortie tronquee.

Ce n'est PAS une prison. Le code lance ici tourne avec les droits du processus
Hermes. C'est un choix assume : faire tourner Hermes sur une VM ou un conteneur
dedie, jamais sur une machine qui contient autre chose de valeur.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

#: Variables retirees de l'environnement des sous-processus : le modele n'a
#: aucune raison de pouvoir lire les cles d'API depuis un `env`.
SECRET_PREFIXES = ("TELEGRAM_", "HERMES_", "ANTHROPIC_", "OPENAI_", "OPENROUTER_")
SECRET_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool = False

    def render(self, limit: int) -> str:
        parts: list[str] = []
        if self.timed_out:
            parts.append("[TIMEOUT] le processus a ete tue avant la fin")
        if self.stdout.strip():
            parts.append(f"[stdout]\n{_clip(self.stdout, limit)}")
        if self.stderr.strip():
            parts.append(f"[stderr]\n{_clip(self.stderr, limit)}")
        parts.append(f"[exit] {self.returncode}")
        return "\n".join(parts)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    cut = len(text) - limit
    return f"{head}\n... [{cut} caracteres coupes] ...\n{tail}"


def child_env(allow_network: bool = True) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(SECRET_PREFIXES) and not k.endswith(SECRET_SUFFIXES)
    }
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("DEBIAN_FRONTEND", "noninteractive")
    if not allow_network:
        # Best effort : neutralise les proxies. Pour un vrai blocage reseau,
        # lancer Hermes dans un namespace reseau isole.
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY"):
            env.pop(var, None)
        env["no_proxy"] = "*"
    return env


async def run(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    stdin: str | None = None,
    allow_network: bool = True,
) -> ExecResult:
    """Lance ``argv`` et renvoie sa sortie, en tuant l'arbre de processus au timeout."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=child_env(allow_network),
            start_new_session=True,  # groupe de processus propre, tuable d'un bloc
        )
    except FileNotFoundError:
        return ExecResult("", f"executable introuvable : {argv[0]}", 127)
    except PermissionError as exc:
        return ExecResult("", f"permission refusee : {exc}", 126)

    payload = stdin.encode() if stdin is not None else None
    try:
        out, err = await asyncio.wait_for(proc.communicate(payload), timeout=timeout)
    except TimeoutError:
        _kill_tree(proc)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=5)
        except (TimeoutError, ValueError):
            out, err = b"", b""
        return ExecResult(
            out.decode("utf-8", "replace"),
            err.decode("utf-8", "replace"),
            proc.returncode,
            timed_out=True,
        )

    return ExecResult(
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
        proc.returncode,
    )


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    import signal

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def run_shell(command: str, *, cwd: Path, timeout: int, allow_network: bool = True):
    shell = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
    return await run(
        [shell, "-lc", command], cwd=cwd, timeout=timeout, allow_network=allow_network
    )


async def run_python(code: str, *, cwd: Path, timeout: int, allow_network: bool = True):
    return await run(
        [sys.executable, "-c", code], cwd=cwd, timeout=timeout, allow_network=allow_network
    )


def resolve_in(workspace: Path, relative: str) -> Path:
    """Resout ``relative`` sous ``workspace`` en refusant toute echappee."""
    candidate = (workspace / relative).expanduser()
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise ValueError(f"chemin invalide : {relative} ({exc})") from exc
    root = workspace.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"chemin hors du workspace : {relative}")
    return resolved
