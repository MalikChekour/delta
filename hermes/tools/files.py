"""Outils de fichiers, confines au workspace."""

from __future__ import annotations

from typing import Any

from ..errors import ToolError
from ..sandbox import clip, resolve_in
from . import ToolContext, tool

MAX_WRITE = 2_000_000


@tool(
    "read_file",
    "Lit un fichier du workspace et renvoie son contenu, numerote par ligne.",
    {
        "path": {"type": "string", "description": "Chemin relatif au workspace."},
        "start": {"type": "integer", "description": "Premiere ligne (1 par defaut)."},
        "limit": {"type": "integer", "description": "Nombre de lignes (500 par defaut)."},
    },
    ["path"],
)
def read_file(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = resolve_in(ctx.workspace, str(args["path"]))
    if not path.is_file():
        raise ToolError(f"{args['path']} n'est pas un fichier existant.")
    start = max(1, int(args.get("start") or 1))
    limit = max(1, int(args.get("limit") or 500))
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    window = lines[start - 1 : start - 1 + limit]
    if not window:
        return f"[{path.name} : {len(lines)} lignes, rien a partir de la ligne {start}]"
    body = "\n".join(f"{start + i:>5} | {line}" for i, line in enumerate(window))
    footer = ""
    if start - 1 + limit < len(lines):
        footer = f"\n[... {len(lines) - (start - 1 + limit)} lignes suivantes non affichees]"
    return clip(body, ctx.output_limit) + footer


@tool(
    "write_file",
    "Ecrit un fichier dans le workspace (cree les dossiers parents, ecrase l'existant).",
    {
        "path": {"type": "string", "description": "Chemin relatif au workspace."},
        "content": {"type": "string", "description": "Contenu complet du fichier."},
    },
    ["path", "content"],
)
def write_file(ctx: ToolContext, args: dict[str, Any]) -> str:
    content = str(args.get("content", ""))
    if len(content) > MAX_WRITE:
        raise ToolError(f"contenu trop volumineux ({len(content)} > {MAX_WRITE} caracteres).")
    path = resolve_in(ctx.workspace, str(args["path"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    lines = content.count("\n") + 1 if content else 0
    return f"Ecrit {path.relative_to(ctx.workspace)} ({len(content)} caracteres, {lines} lignes)."


@tool(
    "edit_file",
    "Remplace une portion exacte de texte dans un fichier. La portion doit etre unique.",
    {
        "path": {"type": "string", "description": "Chemin relatif au workspace."},
        "old": {"type": "string", "description": "Texte a remplacer, exact."},
        "new": {"type": "string", "description": "Texte de remplacement."},
    },
    ["path", "old", "new"],
)
def edit_file(ctx: ToolContext, args: dict[str, Any]) -> str:
    path = resolve_in(ctx.workspace, str(args["path"]))
    if not path.is_file():
        raise ToolError(f"{args['path']} n'est pas un fichier existant.")
    old = str(args["old"])
    new = str(args.get("new", ""))
    text = path.read_text(encoding="utf-8", errors="replace")
    occurrences = text.count(old)
    if occurrences == 0:
        raise ToolError("texte introuvable. Relis le fichier : il a peut-etre change.")
    if occurrences > 1:
        raise ToolError(
            f"texte present {occurrences} fois : ajoute du contexte pour le rendre unique."
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return f"Modifie {path.relative_to(ctx.workspace)}."


@tool(
    "list_files",
    "Liste le contenu d'un dossier du workspace, recursivement.",
    {
        "path": {"type": "string", "description": "Dossier, '.' par defaut."},
        "depth": {"type": "integer", "description": "Profondeur maximale (2 par defaut)."},
    },
)
def list_files(ctx: ToolContext, args: dict[str, Any]) -> str:
    root = resolve_in(ctx.workspace, str(args.get("path") or "."))
    if not root.is_dir():
        raise ToolError(f"{args.get('path', '.')} n'est pas un dossier.")
    depth = max(1, int(args.get("depth") or 2))
    entries: list[str] = []
    for item in sorted(root.rglob("*")):
        relative = item.relative_to(root)
        if len(relative.parts) > depth:
            continue
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        marker = "/" if item.is_dir() else f"  ({item.stat().st_size} o)"
        entries.append(f"{relative}{marker}")
        if len(entries) >= 500:
            entries.append("[... liste tronquee a 500 entrees]")
            break
    return "\n".join(entries) or "[dossier vide]"


TOOLS = (read_file, write_file, edit_file, list_files)
