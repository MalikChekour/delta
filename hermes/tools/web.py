"""Outils web : recherche (multi-moteurs) et lecture de page.

La logique de recherche vit dans ``search.py`` ; ce module l'expose comme outil
et fournit ``fetch_url`` pour lire une page en texte lisible.
"""

from __future__ import annotations

import html as html_module
import json
import re
from typing import Any

from ..errors import ToolError
from ..sandbox import clip
from . import ToolContext, tool
from .search import USER_AGENT, SearchConfig, search

_SCRIPTS = re.compile(r"<(script|style|noscript|svg|head)[^>]*>.*?</\1>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_BLANKS = re.compile(r"\n{3,}")


def _text(fragment: str) -> str:
    fragment = _SCRIPTS.sub(" ", fragment)
    fragment = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", fragment, flags=re.I)
    fragment = _TAGS.sub(" ", fragment)
    fragment = html_module.unescape(fragment)
    fragment = "\n".join(line.strip() for line in fragment.splitlines())
    fragment = re.sub(r"[ \t]{2,}", " ", fragment)
    return _BLANKS.sub("\n\n", fragment).strip()


def _config(ctx: ToolContext) -> SearchConfig:
    return SearchConfig(
        backends=ctx.search_backends,
        searxng_url=ctx.searxng_url,
        ddg_html_url=ctx.search_url,
        timeout=ctx.request_timeout,
    )


@tool(
    "web_search",
    "Cherche sur le web et renvoie les meilleurs resultats (titre, URL, extrait). "
    "A utiliser des qu'une information peut avoir change.",
    {
        "query": {"type": "string", "description": "Requete en langage naturel."},
        "limit": {"type": "integer", "description": "Nombre de resultats (5 par defaut)."},
    },
    ["query"],
)
async def web_search(ctx: ToolContext, args: dict[str, Any]) -> str:
    query = str(args["query"]).strip()
    if not query:
        raise ToolError("requete vide.")
    limit = max(1, min(int(args.get("limit") or 5), 15))

    results, info = await search(query, limit, _config(ctx))
    if results:
        return "\n".join(r.render(i + 1) for i, r in enumerate(results))

    detail = f"\nDetail des moteurs : {info}" if info else ""
    raise ToolError(
        f"aucun resultat pour {query!r} sur les moteurs disponibles.{detail}\n"
        "Pour une recherche fiable, renseigne une cle BRAVE_API_KEY ou TAVILY_API_KEY, "
        "ou une instance HERMES_SEARXNG_URL. Sinon, tente fetch_url sur une source connue."
    )


@tool(
    "fetch_url",
    "Recupere une page web et renvoie son contenu en texte lisible.",
    {
        "url": {"type": "string", "description": "URL complete, http(s) uniquement."},
        "raw": {"type": "boolean", "description": "Renvoyer le corps brut sans conversion."},
    },
    ["url"],
)
async def fetch_url(ctx: ToolContext, args: dict[str, Any]) -> str:
    import httpx

    url = str(args["url"]).strip()
    if not url.startswith(("http://", "https://")):
        raise ToolError("l'URL doit commencer par http:// ou https://.")
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=ctx.request_timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "fr,en;q=0.8"},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            body, final = response.text, str(response.url)
    except Exception as exc:  # noqa: BLE001 - httpx expose une dizaine de types
        raise ToolError(f"requete vers {url} echouee : {type(exc).__name__}: {exc}") from exc

    if args.get("raw"):
        content = body
    elif body.lstrip().startswith(("{", "[")):
        try:
            content = json.dumps(json.loads(body), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            content = _text(body)
    else:
        content = _text(body)

    header = f"# {final}\n\n" if final != url else ""
    return header + clip(content, ctx.output_limit)


TOOLS = (web_search, fetch_url)
