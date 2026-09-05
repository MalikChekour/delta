"""Outils fichiers, confines au workspace."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..sandbox import resolve_in
from . import Tool, ToolContext

MAX_READ_BYTES = 400_000


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)) or "."
    except ValueError:
        return str(path)


async def _read(ctx: ToolContext, args: dict[str, Any]) -> str:
    root = ctx.settings.workspace
    target = resolve_in(root, args["path"])
    if not target.exists():
        return f"ERREUR : {args['path']} n'existe pas."
    if target.is_dir():
        return f"ERREUR : {args['path']} est un dossier. Utilise list_dir."
    if target.stat().st_size > MAX_READ_BYTES:
        return f"ERREUR : fichier trop gros ({target.stat().st_size} octets). Utilise shell + sed."
    text = target.read_text(encoding="utf-8", errors="replace")
    start = max(int(args.get("start_line") or 1), 1)
    end = args.get("end_line")
    lines = text.splitlines()
    if end is not None or start > 1:
        stop = int(end) if end is not None else len(lines)
        lines = lines[start - 1 : stop]
    width = len(str(start + len(lines)))
    return "\n".join(f"{i:>{width}} | {line}" for i, line in enumerate(lines, start=start))


async def _write(ctx: ToolContext, args: dict[str, Any]) -> str:
    root = ctx.settings.workspace
    target = resolve_in(root, args["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    content = args.get("content", "")
    target.write_text(content, encoding="utf-8")
    return f"Ecrit {len(content)} caracteres dans {_rel(target, root)}."


async def _edit(ctx: ToolContext, args: dict[str, Any]) -> str:
    root = ctx.settings.workspace
    target = resolve_in(root, args["path"])
    if not target.is_file():
        return f"ERREUR : {args['path']} n'est pas un fichier existant."
    old, new = args["old_string"], args["new_string"]
    text = target.read_text(encoding="utf-8", errors="replace")
    count = text.count(old)
    if count == 0:
        return "ERREUR : old_string introuvable. Relis le fichier, la correspondance est exacte."
    if count > 1 and not args.get("replace_all"):
        return (
            f"ERREUR : old_string apparait {count} fois. Ajoute du contexte pour la rendre "
            "unique, ou passe replace_all=true."
        )
    target.write_text(text.replace(old, new), encoding="utf-8")
    return f"Remplace {count if args.get('replace_all') else 1} occurrence(s) dans {_rel(target, root)}."


async def _list(ctx: ToolContext, args: dict[str, Any]) -> str:
    root = ctx.settings.workspace
    target = resolve_in(root, args.get("path") or ".")
    if not target.is_dir():
        return f"ERREUR : {args.get('path') or '.'} n'est pas un dossier."
    entries = []
    for item in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if item.name.startswith(".") and not args.get("show_hidden"):
            continue
        if item.is_dir():
            entries.append(f"{item.name}/")
        else:
            entries.append(f"{item.name}  ({item.stat().st_size} o)")
    if not entries:
        return f"{_rel(target, root)} est vide."
    return f"{_rel(target, root)} :\n" + "\n".join(entries)


TOOLS = [
    Tool(
        name="read_file",
        description="Lit un fichier du workspace, numerote par lignes.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Chemin relatif au workspace."},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        handler=_read,
    ),
    Tool(
        name="write_file",
        description=(
            "Ecrit un fichier dans le workspace, en ecrasant s'il existe. Cree les dossiers "
            "parents. Pour une modification ponctuelle, prefere edit_file."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        handler=_write,
    ),
    Tool(
        name="edit_file",
        description=(
            "Remplace une chaine exacte dans un fichier. old_string doit correspondre au "
            "texte du fichier caractere pour caractere, indentation comprise."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path", "old_string", "new_string"],
            "additionalProperties": False,
        },
        handler=_edit,
    ),
    Tool(
        name="list_dir",
        description="Liste le contenu d'un dossier du workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "show_hidden": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        handler=_list,
    ),
]
