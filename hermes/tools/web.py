"""Recherche et lecture de pages web.

La recherche passe par l'endpoint HTML de DuckDuckGo : pas de cle API, pas de
quota, et une extraction stable. La lecture de page convertit le HTML en texte
lisible sans dependance lourde.
"""

from __future__ import annotations

import html as html_module
import json
import re
import urllib.parse
from typing import Any

from ..errors import ToolError
from ..sandbox import clip
from . import ToolContext, tool

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

_SCRIPTS = re.compile(r"<(script|style|noscript|svg|head)[^>]*>.*?</\1>", re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_BLANKS = re.compile(r"\n{3,}")
_RESULT = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'(?:.*?<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>)?',
    re.S | re.I,
)


def _text(fragment: str) -> str:
    fragment = _SCRIPTS.sub(" ", fragment)
    fragment = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", fragment, flags=re.I)
    fragment = _TAGS.sub(" ", fragment)
    fragment = html_module.unescape(fragment)
    fragment = "\n".join(line.strip() for line in fragment.splitlines())
    fragment = re.sub(r"[ \t]{2,}", " ", fragment)
    return _BLANKS.sub("\n\n", fragment).strip()


def _unwrap(href: str) -> str:
    """DuckDuckGo enveloppe les liens dans /l/?uddg=... : on rend l'URL reelle."""
    if "duckduckgo.com/l/" in href or href.startswith("/l/"):
        query = urllib.parse.urlparse(href).query
        target = urllib.parse.parse_qs(query).get("uddg")
        if target:
            return target[0]
    if href.startswith("//"):
        return "https:" + href
    return href


async def _fetch(
    url: str, timeout: int, *, params: dict[str, str] | None = None
) -> tuple[str, str]:
    import httpx

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "fr,en;q=0.8"},
        ) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.text, str(response.url)
    except Exception as exc:  # noqa: BLE001 - httpx expose une dizaine de types
        raise ToolError(f"requete vers {url} echouee : {type(exc).__name__}: {exc}") from exc


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
    body, _ = await _fetch(ctx.search_url, ctx.request_timeout, params={"q": query})

    results: list[str] = []
    for match in _RESULT.finditer(body):
        url = _unwrap(html_module.unescape(match.group("href")))
        title = _text(match.group("title"))
        snippet = _text(match.group("snippet") or "")
        if not title or not url.startswith("http"):
            continue
        entry = f"{len(results) + 1}. {title}\n   {url}"
        if snippet:
            entry += f"\n   {snippet[:400]}"
        results.append(entry)
        if len(results) >= limit:
            break

    if not results:
        return (
            f"Aucun resultat exploitable pour {query!r}. "
            "Reformule, ou passe par fetch_url sur une source connue."
        )
    return "\n".join(results)


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
    url = str(args["url"]).strip()
    if not url.startswith(("http://", "https://")):
        raise ToolError("l'URL doit commencer par http:// ou https://.")
    body, final = await _fetch(url, ctx.request_timeout)

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
