"""Confinement des outils : chemins, execution, taille des sorties.

Hermes execute des commandes reelles. Le confinement ici n'est pas une barriere
de securite contre un attaquant — la liste blanche Telegram joue ce role — mais
un garde-fou contre les erreurs : ecrire hors du workspace, boucler sans fin,
ou renvoyer 200 Mo de logs dans un chat.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from pathlib import Path

from .errors import ToolError


def resolve_in(root: Path, relative: str) -> Path:
    """Resout un chemin sous ``root``, en refusant toute sortie.

    Un chemin absolu n'est accepte que s'il vise deja le workspace : le replier
    silencieusement dedans donnerait au modele l'illusion d'avoir ecrit ailleurs.
    Les liens symboliques sont resolus avant verification, donc un lien vers
    ``/etc`` ne suffit pas a sortir.
    """
    root = root.resolve()
    text = str(relative).strip()
    if not text:
        raise ToolError("chemin vide.")
    if text.startswith("~"):
        raise ToolError(
            f"Chemin refuse : {relative!r}. Les chemins sont relatifs au workspace {root}."
        )

    candidate = Path(text)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ToolError(
            f"Chemin hors du workspace : {relative!r}. "
            f"Utilise un chemin relatif ; tous les fichiers vivent sous {root}."
        )
    return resolved


def clip(text: str, limit: int) -> str:
    """Tronque au milieu : le debut et la fin d'un log portent l'information."""
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    removed = len(text) - limit
    return f"{text[:head]}\n\n[... {removed} caracteres omis ...]\n\n{text[-tail:]}"


async def run(
    command: str,
    *,
    cwd: Path,
    timeout: int,
    output_limit: int,
    env: dict[str, str] | None = None,
) -> str:
    """Execute une commande shell et renvoie un compte rendu textuel.

    Ne leve jamais sur un echec de la commande : un code de retour non nul est
    une information utile pour le modele, pas une exception a remonter.
    """
    cwd.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "PYTHONUNBUFFERED": "1", **(env or {})}

    try:
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            stdin=asyncio.subprocess.DEVNULL,
            env=environment,
            start_new_session=True,  # permet de tuer tout l'arbre de processus
        )
    except OSError as exc:  # pragma: no cover - depend du systeme
        raise ToolError(f"Impossible de lancer la commande : {exc}") from exc

    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        _kill_tree(process)
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
        except (asyncio.TimeoutError, OSError):
            stdout = b""
        partial = clip(stdout.decode("utf-8", "replace"), output_limit)
        return (
            f"[TIMEOUT apres {timeout}s — processus tue]\n{partial}"
            "\n\nRelance en tache de fond, ou decoupe le travail."
        )

    text = clip(stdout.decode("utf-8", "replace"), output_limit).rstrip()
    code = process.returncode
    if code == 0:
        return text or "[commande terminee, aucune sortie]"
    return f"[code de retour {code}]\n{text}" if text else f"[code de retour {code}, aucune sortie]"


def _kill_tree(process: asyncio.subprocess.Process) -> None:
    """Tue le groupe de processus : sinon les enfants survivent au timeout."""
    import signal

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except OSError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()


def disk_free(path: Path) -> str:
    usage = shutil.disk_usage(path)
    return f"{usage.free / 1e9:.1f} Go libres sur {usage.total / 1e9:.1f}"
