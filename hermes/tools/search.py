"""Recherche web multi-moteurs, avec repli automatique.

Un agent qui ne peut pas chercher est a moitie aveugle. Or scraper un seul
moteur grand public est fragile : DuckDuckGo, Google et consorts renvoient de
plus en plus une page anti-robot (HTTP 202) au lieu de resultats, et changent
leur balisage sans prevenir. Une seule source, c'est une panne garantie tot ou
tard.

D'ou une *chaine* de moteurs, essayes dans l'ordre :

1. les API concues pour les agents, qui renvoient du JSON propre et ne se font
   pas bloquer — Tavily, Brave, ou une instance SearXNG que l'on heberge ;
2. a defaut de cle, DuckDuckGo Lite puis HTML, scrapes, en dernier recours.

Le premier moteur qui rend au moins un resultat gagne. Si tous echouent, le
message d'erreur dit precisement quoi faire — ajouter une cle, renseigner une
instance SearXNG — plutot que de laisser l'agent deviner.
"""

from __future__ import annotations

import html as html_module
import logging
import os
import re
import urllib.parse
from dataclasses import dataclass

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


@dataclass(frozen=True)
class Result:
    title: str
    url: str
    snippet: str = ""

    def render(self, index: int) -> str:
        line = f"{index}. {self.title}\n   {self.url}"
        if self.snippet:
            line += f"\n   {self.snippet[:400]}"
        return line


@dataclass
class SearchConfig:
    """Ce dont la recherche a besoin, resolu depuis la configuration."""

    backends: tuple[str, ...]
    searxng_url: str = ""
    ddg_html_url: str = "https://html.duckduckgo.com/html/"
    ddg_lite_url: str = "https://lite.duckduckgo.com/lite/"
    timeout: int = 20


# --------------------------------------------------------------------------- #
# Nettoyage de texte                                                          #
# --------------------------------------------------------------------------- #

_TAGS = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_module.unescape(_TAGS.sub(" ", fragment))).strip()


def _unwrap(href: str) -> str:
    """DuckDuckGo enveloppe ses liens dans /l/?uddg=... : on rend l'URL reelle."""
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href or href.startswith("/l/"):
        query = urllib.parse.urlparse(href).query
        target = urllib.parse.parse_qs(query).get("uddg")
        if target:
            return target[0]
    return href


# --------------------------------------------------------------------------- #
# Analyseurs (testes hors-ligne sur des fragments reels)                      #
# --------------------------------------------------------------------------- #

# Les moteurs varient l'ordre des attributs (`href` avant `class` ou l'inverse) :
# on capture donc la balise <a ...>titre</a> entiere des lors qu'elle porte la
# classe voulue, puis on en extrait href separement. Un parsing dependant de
# l'ordre casse au premier changement de gabarit.
_HREF = re.compile(r'\bhref="([^"]+)"', re.I)


def _links(body: str, css_class: str) -> list[tuple[str, str]]:
    """Renvoie (href, titre) pour chaque <a> portant la classe donnee."""
    motif = re.compile(
        r'<a\b(?P<attrs>[^>]*\bclass="[^"]*' + re.escape(css_class) + r'[^"]*"[^>]*)>'
        r'(?P<title>.*?)</a>',
        re.I | re.S,
    )
    out: list[tuple[str, str]] = []
    for match in motif.finditer(body):
        href = _HREF.search(match.group("attrs"))
        if href:
            out.append((href.group(1), match.group("title")))
    return out


_LITE_SNIPPET = re.compile(
    r'<td[^>]*\bclass="[^"]*result-snippet[^"]*"[^>]*>(?P<snippet>.*?)</td>', re.I | re.S
)
_HTML_SNIPPET = re.compile(
    r'<a[^>]*\bclass="[^"]*result__snippet[^"]*"[^>]*>(?P<snippet>.*?)</a>', re.I | re.S
)


def _looks_blocked(body: str) -> bool:
    low = body.lower()
    return (
        "anomaly" in low
        or "unfortunately, bots use duckduckgo too" in low
        or ("challenge" in low and "cf-" in low)
    )


def _assemble(links: list[tuple[str, str]], snippets: list[str], limit: int) -> list[Result]:
    results: list[Result] = []
    for i, (href, raw_title) in enumerate(links):
        url = _unwrap(html_module.unescape(href))
        title = _clean(raw_title)
        if not title or not url.startswith("http"):
            continue
        snippet = _clean(snippets[i]) if i < len(snippets) else ""
        results.append(Result(title, url, snippet))
        if len(results) >= limit:
            break
    return results


def parse_ddg_lite(body: str, limit: int) -> list[Result]:
    return _assemble(_links(body, "result-link"), _LITE_SNIPPET.findall(body), limit)


def parse_ddg_html(body: str, limit: int) -> list[Result]:
    return _assemble(_links(body, "result__a"), _HTML_SNIPPET.findall(body), limit)


def parse_searxng(payload: dict, limit: int) -> list[Result]:
    results: list[Result] = []
    for item in payload.get("results", []):
        url = str(item.get("url", ""))
        title = _clean(str(item.get("title", "")))
        if not title or not url.startswith("http"):
            continue
        results.append(Result(title, url, _clean(str(item.get("content", "")))))
        if len(results) >= limit:
            break
    return results


def parse_brave(payload: dict, limit: int) -> list[Result]:
    results: list[Result] = []
    for item in payload.get("web", {}).get("results", []):
        url = str(item.get("url", ""))
        title = _clean(str(item.get("title", "")))
        if not title or not url.startswith("http"):
            continue
        results.append(Result(title, url, _clean(str(item.get("description", "")))))
        if len(results) >= limit:
            break
    return results


def parse_tavily(payload: dict, limit: int) -> list[Result]:
    results: list[Result] = []
    answer = _clean(str(payload.get("answer") or ""))
    if answer:
        results.append(
            Result("Reponse directe (Tavily)", "https://tavily.com", answer)
        )
    for item in payload.get("results", []):
        url = str(item.get("url", ""))
        title = _clean(str(item.get("title", "")))
        if not title or not url.startswith("http"):
            continue
        results.append(Result(title, url, _clean(str(item.get("content", "")))))
        if len(results) >= limit:
            break
    return results[:limit] if not answer else results[: limit + 1]


# --------------------------------------------------------------------------- #
# Moteurs                                                                      #
# --------------------------------------------------------------------------- #


async def _tavily(client, query: str, limit: int, cfg: SearchConfig) -> list[Result]:
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        raise _Skip("TAVILY_API_KEY absente")
    response = await client.post(
        "https://api.tavily.com/search",
        json={"api_key": key, "query": query, "max_results": limit, "include_answer": True},
    )
    response.raise_for_status()
    return parse_tavily(response.json(), limit)


async def _brave(client, query: str, limit: int, cfg: SearchConfig) -> list[Result]:
    key = os.getenv("BRAVE_API_KEY", "").strip()
    if not key:
        raise _Skip("BRAVE_API_KEY absente")
    response = await client.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": limit},
        headers={"X-Subscription-Token": key, "Accept": "application/json"},
    )
    response.raise_for_status()
    return parse_brave(response.json(), limit)


async def _searxng(client, query: str, limit: int, cfg: SearchConfig) -> list[Result]:
    if not cfg.searxng_url:
        raise _Skip("HERMES_SEARXNG_URL non renseignee")
    base = cfg.searxng_url.rstrip("/")
    url = base if base.endswith("/search") else base + "/search"
    response = await client.get(url, params={"q": query, "format": "json"})
    response.raise_for_status()
    return parse_searxng(response.json(), limit)


async def _ddg_lite(client, query: str, limit: int, cfg: SearchConfig) -> list[Result]:
    response = await client.post(
        cfg.ddg_lite_url,
        data={"q": query},
        headers={"Referer": cfg.ddg_lite_url, "Origin": "https://lite.duckduckgo.com"},
    )
    response.raise_for_status()
    if _looks_blocked(response.text):
        raise _Blocked("DuckDuckGo Lite a renvoye une page anti-robot")
    return parse_ddg_lite(response.text, limit)


async def _ddg_html(client, query: str, limit: int, cfg: SearchConfig) -> list[Result]:
    response = await client.post(
        cfg.ddg_html_url,
        data={"q": query},
        headers={"Referer": cfg.ddg_html_url, "Origin": "https://html.duckduckgo.com"},
    )
    response.raise_for_status()
    if _looks_blocked(response.text):
        raise _Blocked("DuckDuckGo HTML a renvoye une page anti-robot")
    return parse_ddg_html(response.text, limit)


BACKENDS = {
    "tavily": _tavily,
    "brave": _brave,
    "searxng": _searxng,
    "ddg_lite": _ddg_lite,
    "ddg_html": _ddg_html,
}

#: Moteurs sans cle, toujours tentes en dernier recours.
KEYLESS = ("ddg_lite", "ddg_html")


class _Skip(Exception):
    """Moteur non configure : on passe au suivant sans le compter comme un echec."""


class _Blocked(Exception):
    """Le moteur a repondu par une page anti-robot."""


def resolve_backends(cfg: SearchConfig) -> list[str]:
    """Ordre effectif des moteurs.

    'auto' place devant les moteurs reellement configures (cle ou URL presente),
    puis complete par les moteurs sans cle. Une liste explicite est respectee
    telle quelle, en ne gardant que les noms connus.
    """
    if cfg.backends and cfg.backends != ("auto",):
        return [b for b in cfg.backends if b in BACKENDS]

    ordered: list[str] = []
    if os.getenv("TAVILY_API_KEY", "").strip():
        ordered.append("tavily")
    if os.getenv("BRAVE_API_KEY", "").strip():
        ordered.append("brave")
    if cfg.searxng_url:
        ordered.append("searxng")
    for name in KEYLESS:
        if name not in ordered:
            ordered.append(name)
    return ordered


async def search(query: str, limit: int, cfg: SearchConfig) -> tuple[list[Result], str]:
    """Interroge les moteurs dans l'ordre. Renvoie (resultats, moteur gagnant).

    Ne leve jamais : en cas d'echec total, renvoie une liste vide et, en seconde
    place, un compte rendu des tentatives, exploitable par l'appelant.
    """
    import httpx

    problems: list[str] = []
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=cfg.timeout,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "fr,en;q=0.8"},
    ) as client:
        for name in resolve_backends(cfg):
            backend = BACKENDS.get(name)
            if backend is None:
                continue
            try:
                results = await backend(client, query, limit, cfg)
            except _Skip:
                continue  # non configure : ni succes ni echec
            except _Blocked as exc:
                problems.append(f"{name} : {exc}")
                continue
            except Exception as exc:  # noqa: BLE001 - un moteur mort ne doit pas tout arreter
                problems.append(f"{name} : {type(exc).__name__}: {exc}")
                log.warning("Moteur de recherche %s en echec : %s", name, exc)
                continue
            if results:
                return results, name
            problems.append(f"{name} : aucun resultat")

    return [], "; ".join(problems)
