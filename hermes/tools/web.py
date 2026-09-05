"""Recherche web et recuperation de pages.

Quatre backends de recherche, choisis par HERMES_SEARCH_BACKEND :
  tavily | brave | searxng | duckduckgo | auto (defaut)

``auto`` prend le premier disponible selon les cles presentes, et retombe sur
DuckDuckGo qui ne demande aucune cle.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from ..config import Settings
from . import Tool, ToolContext

UA = "Mozilla/5.0 (compatible; HermesAgent/0.1; +https://github.com/MalikChekour/delta)"
FETCH_LIMIT = 40_000


def _pick_backend(settings: Settings) -> str:
    choice = settings.search_backend
    if choice != "auto":
        return choice
    if os.getenv("TAVILY_API_KEY"):
        return "tavily"
    if os.getenv("BRAVE_API_KEY"):
        return "brave"
    if os.getenv("SEARXNG_URL"):
        return "searxng"
    return "duckduckgo"


def _format(results: list[dict[str, str]], query: str) -> str:
    if not results:
        return f"Aucun resultat pour {query!r}."
    lines = [f"Resultats pour {query!r} :", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title') or '(sans titre)'}")
        lines.append(f"   {r.get('url', '')}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet']}")
        lines.append("")
    lines.append("Utilise web_fetch sur une URL pour en lire le contenu complet.")
    return "\n".join(lines)


async def _tavily(client: httpx.AsyncClient, query: str, n: int) -> list[dict[str, str]]:
    resp = await client.post(
        "https://api.tavily.com/search",
        json={
            "api_key": os.environ["TAVILY_API_KEY"],
            "query": query,
            "max_results": n,
            "search_depth": "advanced",
        },
    )
    resp.raise_for_status()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in resp.json().get("results", [])
    ]


async def _brave(client: httpx.AsyncClient, query: str, n: int) -> list[dict[str, str]]:
    resp = await client.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": n},
        headers={
            "X-Subscription-Token": os.environ["BRAVE_API_KEY"],
            "Accept": "application/json",
        },
    )
    resp.raise_for_status()
    return [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("description", ""),
        }
        for r in resp.json().get("web", {}).get("results", [])[:n]
    ]


async def _searxng(client: httpx.AsyncClient, query: str, n: int) -> list[dict[str, str]]:
    base = os.environ["SEARXNG_URL"].rstrip("/")
    resp = await client.get(
        f"{base}/search", params={"q": query, "format": "json", "language": "auto"}
    )
    resp.raise_for_status()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in resp.json().get("results", [])[:n]
    ]


async def _duckduckgo(client: httpx.AsyncClient, query: str, n: int) -> list[dict[str, str]]:
    resp = await client.post(
        "https://html.duckduckgo.com/html/",
        data={"q": query},
        headers={"User-Agent": UA},
    )
    resp.raise_for_status()
    tree = HTMLParser(resp.text)
    out: list[dict[str, str]] = []
    for node in tree.css("div.result")[: n * 2]:
        link = node.css_first("a.result__a")
        if link is None:
            continue
        snippet = node.css_first("a.result__snippet")
        out.append(
            {
                "title": link.text(strip=True),
                "url": link.attributes.get("href", ""),
                "snippet": snippet.text(strip=True) if snippet else "",
            }
        )
        if len(out) >= n:
            break
    return out


BACKENDS = {
    "tavily": _tavily,
    "brave": _brave,
    "searxng": _searxng,
    "duckduckgo": _duckduckgo,
}


def build_tools(settings: Settings) -> list[Tool]:
    async def _search(ctx: ToolContext, args: dict[str, Any]) -> str:
        query = (args.get("query") or "").strip()
        if not query:
            return "ERREUR : parametre 'query' vide."
        n = max(1, min(int(args.get("max_results") or 6), 15))
        backend = _pick_backend(ctx.settings)
        fn = BACKENDS.get(backend)
        if fn is None:
            return f"ERREUR : backend de recherche inconnu {backend!r}."
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            try:
                results = await fn(client, query, n)
            except KeyError as exc:
                return f"ERREUR : le backend {backend} exige la variable {exc}."
            except httpx.HTTPStatusError as exc:
                return f"ERREUR : {backend} a repondu {exc.response.status_code}."
            except httpx.HTTPError as exc:
                return f"ERREUR reseau via {backend} : {exc}"
        return _format(results, query)

    async def _fetch(ctx: ToolContext, args: dict[str, Any]) -> str:
        url = (args.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return "ERREUR : url doit commencer par http:// ou https://."
        async with httpx.AsyncClient(
            timeout=45, follow_redirects=True, headers={"User-Agent": UA}
        ) as client:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                return f"ERREUR : {url} a repondu {exc.response.status_code}."
            except httpx.HTTPError as exc:
                return f"ERREUR reseau sur {url} : {exc}"

        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "xml" not in ctype:
            body = resp.text[:FETCH_LIMIT]
            return f"{url} ({ctype})\n\n{body}"

        tree = HTMLParser(resp.text)
        for tag in ("script", "style", "noscript", "nav", "footer", "svg", "form"):
            for node in tree.css(tag):
                node.decompose()
        title_node = tree.css_first("title")
        title = title_node.text(strip=True) if title_node else ""
        body_node = tree.css_first("main") or tree.css_first("article") or tree.body
        text = body_node.text(separator="\n", strip=True) if body_node else ""
        text = "\n".join(line for line in text.splitlines() if line.strip())
        truncated = "\n\n[... page tronquee ...]" if len(text) > FETCH_LIMIT else ""
        return f"# {title}\n{url}\n\n{text[:FETCH_LIMIT]}{truncated}"

    return [
        Tool(
            name="web_search",
            description=(
                "Cherche sur le web et renvoie titres, URLs et extraits. Utilise-le pour "
                "tout ce qui a pu changer depuis l'entrainement : versions, actualites, "
                "documentation, prix."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "description": "1 a 15, defaut 6."},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=_search,
        ),
        Tool(
            name="web_fetch",
            description=(
                "Telecharge une URL et renvoie son texte principal, debarrasse du HTML. "
                "A enchainer apres web_search pour lire une source en entier."
            ),
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=_fetch,
        ),
    ]
