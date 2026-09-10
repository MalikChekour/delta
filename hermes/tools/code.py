"""Outils de CODE : chercher dedans, chercher ailleurs, et modifier proprement.

Un agent qui n'a que ``read_file`` lit les fichiers un par un pour trouver ou quelque chose
est defini : c'est lent, ca remplit le contexte de decor, et ca rate ce qui est nomme
autrement. Trois manques concrets, trois outils :

1. ``code_search`` — ripgrep. Trouver OU, dans une arborescence, en une passe.
2. ``ast_grep`` — recherche STRUCTURELLE. `foo(` en texte attrape les commentaires et les
   chaines ; `foo($$$)` en AST attrape les appels, et eux seuls.
3. ``github_code`` — le code REEL des autres. Pas de la documentation reformulee : la ligne
   telle qu'elle est ecrite dans un depot qui tourne.
4. ``apply_patch`` — appliquer un diff unifie. ``edit_file`` remplace une chaine a la fois ;
   un correctif qui touche cinq endroits devient cinq appels, dont chacun peut echouer a
   mi-parcours et laisser le fichier dans un etat batard.

🚨 Tous respectent le workspace : ``resolve_in`` refuse toute sortie, y compris par lien
symbolique. Un outil de code qui peut lire hors du bac a sable, c'est une fuite.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..sandbox import clip, resolve_in, run
from . import ToolContext, tool
from .search import USER_AGENT_HONNETE



# 🚨 LE PATH DU SERVICE N'EST PAS LE MIEN. Le bot tourne en tache planifiee sous le compte
# SYSTEM, dont le PATH machine ne contient NI `%LOCALAPPDATA%\Microsoft\WinGet\Links` (ou
# winget met ripgrep) NI `%APPDATA%\npm` (ou npm met ast-grep) : ces deux dossiers vivent
# dans le profil Administrator. Mesure du 10/09 — `shutil.which("rg")` trouve le binaire
# depuis mon shell et rend None sous le service. Resultat sans ce correctif : `code_search`
# retombe en silence sur son repli Python lent, et `ast_grep` repond « pas installe » pour
# un outil pourtant present. On cherche donc AUSSI dans les emplacements connus.
_DOSSIERS = [
    Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Administrator\AppData\Local"))
    / "Microsoft" / "WinGet" / "Links",
    Path(os.environ.get("APPDATA", r"C:\Users\Administrator\AppData\Roaming")) / "npm",
    Path(r"C:\Users\Administrator\AppData\Local\Microsoft\WinGet\Links"),
    Path(r"C:\Users\Administrator\AppData\Roaming\npm"),
    Path(r"C:\ProgramData\chocolatey\bin"),
    Path(r"C:\Program Files\Git\cmd"),
    Path(r"C:\Program Files\nodejs"),
]


def _outil(nom: str) -> str | None:
    """Chemin d'un binaire, ou None. On ne suppose JAMAIS qu'il est installe — ni que le
    PATH du processus est celui d'une session interactive."""
    trouve = shutil.which(nom)
    if trouve:
        return trouve
    for dossier in _DOSSIERS:
        # 🚨 .exe/.cmd AVANT l'extension vide : npm depose DEUX fichiers, `ast-grep` (script
        # POSIX, illisible pour Windows) et `ast-grep.cmd` (le vrai point d'entree). Prendre
        # le premier trouve dans l'ordre naif donne un binaire qui echoue a l'execution.
        for suffixe in (".exe", ".cmd", ".bat", ""):
            candidat = dossier / (nom + suffixe)
            if candidat.is_file():
                return str(candidat)
    return None


@tool(
    "code_search",
    "Cherche un motif dans le code du workspace (ripgrep) : renvoie fichier, ligne et "
    "contenu. A preferer a la lecture de fichiers un par un pour savoir OU quelque chose "
    "est defini ou utilise.",
    {
        "pattern": {"type": "string", "description": "Motif, expression reguliere acceptee."},
        "path": {"type": "string", "description": "Sous-dossier a fouiller (defaut : tout)."},
        "glob": {"type": "string", "description": "Filtre de fichiers, ex. '*.py'."},
        "ignore_case": {"type": "boolean", "description": "Ignorer la casse."},
        "max_results": {"type": "integer", "description": "Nombre de lignes (60 par defaut)."},
    },
    ["pattern"],
)
async def code_search(ctx: ToolContext, args: dict[str, Any]) -> str:
    rg = _outil("rg")
    motif = str(args["pattern"])
    cible = resolve_in(ctx.workspace, str(args.get("path") or "."))
    limite = max(1, min(int(args.get("max_results") or 60), 400))

    if rg is None:
        # 🚨 Repli en Python plutot qu'une erreur : ripgrep peut manquer sur une machine,
        # l'agent ne doit pas perdre la capacite de chercher pour autant.
        import re

        try:
            rx = re.compile(motif, re.I if args.get("ignore_case") else 0)
        except re.error as exc:
            raise ToolError(f"motif invalide : {exc}") from exc
        filtre = str(args.get("glob") or "")
        lignes: list[str] = []
        for racine, _, fichiers in os.walk(cible):
            if any(p in racine for p in (".git", "venv", "node_modules", "__pycache__")):
                continue
            for f in fichiers:
                if filtre and not Path(f).match(filtre):
                    continue
                p = Path(racine) / f
                try:
                    texte = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for n, ligne in enumerate(texte.splitlines(), 1):
                    if rx.search(ligne):
                        # Barres obliques : le modele recopie ces chemins dans l'appel
                        # suivant, et `\f`/`\n` y deviendraient des echappements.
                        rel = str(p.relative_to(ctx.workspace)).replace(os.sep, "/")
                        lignes.append(f"{rel}:{n}: {ligne.strip()[:200]}")
                        if len(lignes) >= limite:
                            break
                if len(lignes) >= limite:
                    break
            if len(lignes) >= limite:
                break
        # 🚨 La mention du repli doit apparaitre sur TOUS les chemins de sortie, pas
        # seulement quand la limite est atteinte : sinon le cas courant ne dit pas que la
        # recherche etait degradee (plus lente, sans les exclusions fines de ripgrep).
        if not lignes:
            return "aucune correspondance. [ripgrep absent : repli Python]"
        return "\n".join(lignes) + "\n[ripgrep absent : repli Python]"

    cmd = [rg, "--line-number", "--no-heading", "--color", "never",
           "--max-count", "20", "-m", str(limite)]
    if args.get("ignore_case"):
        cmd.append("--ignore-case")
    if args.get("glob"):
        cmd += ["--glob", str(args["glob"])]
    cmd += ["--", motif, str(cible)]
    sortie = await run(" ".join(f'"{c}"' if " " in c else c for c in cmd),
                       cwd=ctx.workspace, timeout=ctx.exec_timeout,
                       output_limit=ctx.output_limit)
    return sortie


@tool(
    "ast_grep",
    "Recherche STRUCTURELLE dans le code (ast-grep) : le motif porte sur la syntaxe, pas "
    "sur le texte. Ex. 'console.log($$$)' trouve les appels sans attraper les commentaires. "
    "Utiliser quand une recherche textuelle rendrait trop de faux positifs.",
    {
        "pattern": {"type": "string", "description": "Motif ast-grep, ex. 'foo($$$ARGS)'."},
        "lang": {"type": "string", "description": "Langage : python, js, ts, rust, go..."},
        "path": {"type": "string", "description": "Sous-dossier (defaut : tout)."},
    },
    ["pattern", "lang"],
)
async def ast_grep(ctx: ToolContext, args: dict[str, Any]) -> str:
    binaire = _outil("ast-grep") or _outil("sg")
    if binaire is None:
        raise ToolError(
            "ast-grep n'est pas installe. Installation : npm install -g @ast-grep/cli "
            "(ou cargo install ast-grep). En attendant, utilise code_search."
        )
    cible = resolve_in(ctx.workspace, str(args.get("path") or "."))
    cmd = (f'"{binaire}" run --pattern "{args["pattern"]}" --lang {args["lang"]} '
           f'--heading never "{cible}"')
    return await run(cmd, cwd=ctx.workspace, timeout=ctx.exec_timeout,
                     output_limit=ctx.output_limit)


@tool(
    "github_code",
    "Cherche du CODE REEL sur GitHub (pas de la documentation) : renvoie depot, chemin et "
    "extrait. Utile pour voir comment une bibliotheque s'utilise vraiment.",
    {
        "query": {"type": "string", "description": "Ex. 'new_session bria-rmbg language:python'."},
        "limit": {"type": "integer", "description": "Nombre de resultats (5 par defaut)."},
    },
    ["query"],
)
async def github_code(ctx: ToolContext, args: dict[str, Any]) -> str:
    import httpx

    jeton = os.getenv("GITHUB_TOKEN", "").strip()
    if not jeton:
        # 🚨 L'API code search de GitHub EXIGE une authentification (403 sans jeton) —
        # contrairement a la recherche de depots. On le dit franchement plutot que de
        # rendre une erreur HTTP brute que le modele interpretera de travers.
        raise ToolError(
            "la recherche de code GitHub exige un jeton. Ajoute GITHUB_TOKEN dans le .env "
            "(jeton personnel, portee publique suffisante : aucun droit d'ecriture requis). "
            "Sans jeton, web_search sait deja trouver des DEPOTS via le moteur 'github'."
        )
    limite = max(1, min(int(args.get("limit") or 5), 20))
    async with httpx.AsyncClient(timeout=ctx.request_timeout, follow_redirects=True) as client:
        r = await client.get(
            "https://api.github.com/search/code",
            params={"q": str(args["query"]), "per_page": limite},
            headers={"User-Agent": USER_AGENT_HONNETE,
                     "Accept": "application/vnd.github.text-match+json",
                     "Authorization": "Bearer " + jeton},
        )
        if r.status_code == 403:
            raise ToolError("GitHub a refuse (quota ou jeton insuffisant) : " + r.text[:200])
        r.raise_for_status()
        items = r.json().get("items", [])
    if not items:
        return "aucun code trouve."
    out = []
    for i, it in enumerate(items, 1):
        depot = it.get("repository", {}).get("full_name", "?")
        out.append(f"{i}. {depot} — {it.get('path', '?')}\n   {it.get('html_url', '')}")
        for m in (it.get("text_matches") or [])[:1]:
            extrait = " ".join((m.get("fragment") or "").split())[:240]
            if extrait:
                out.append("   > " + extrait)
    return clip("\n".join(out), ctx.output_limit)


@tool(
    "apply_patch",
    "Applique un diff unifie (format `git diff`) sur le workspace. A preferer a plusieurs "
    "edit_file quand un correctif touche plusieurs endroits : c'est tout ou rien.",
    {
        "diff": {"type": "string", "description": "Diff unifie complet, avec --- et +++."},
        "check": {"type": "boolean", "description": "Verifier sans ecrire (defaut : false)."},
    },
    ["diff"],
)
async def apply_patch(ctx: ToolContext, args: dict[str, Any]) -> str:
    git = _outil("git")
    if git is None:
        raise ToolError("git est introuvable : impossible d'appliquer un diff.")
    diff = str(args["diff"])
    if not diff.endswith("\n"):
        diff += "\n"
    if "+++" not in diff or "---" not in diff:
        raise ToolError("ce n'est pas un diff unifie : il manque les en-tetes --- / +++.")

    tmp = ctx.workspace / ".hermes_patch.diff"
    tmp.write_text(diff, encoding="utf-8", newline="\n")
    try:
        # 🚨 On verifie TOUJOURS avant d'ecrire : `git apply --check` dit si le patch
        # s'applique proprement. Sans ce controle, un diff a moitie bon laisse la moitie
        # des fichiers modifies et l'autre non — l'etat le plus difficile a rattraper.
        verif = await run(f'"{git}" apply --check --unsafe-paths --directory=. "{tmp.name}"',
                          cwd=ctx.workspace, timeout=ctx.exec_timeout,
                          output_limit=ctx.output_limit)
        if "error" in verif.lower() or "fatal" in verif.lower():
            return "Le patch NE s'applique PAS (rien n'a ete ecrit) :\n" + verif
        if args.get("check"):
            return "Le patch s'applique proprement (verification seule, rien n'a ete ecrit)."
        pose = await run(f'"{git}" apply --unsafe-paths --directory=. "{tmp.name}"',
                         cwd=ctx.workspace, timeout=ctx.exec_timeout,
                         output_limit=ctx.output_limit)
        return "Patch applique.\n" + pose if pose.strip() else "Patch applique."
    finally:
        tmp.unlink(missing_ok=True)


TOOLS = (code_search, ast_grep, github_code, apply_patch)
