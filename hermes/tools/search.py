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

# 🚨 DEUX USER-AGENTS, ET C'EST DELIBERE. Le faux Chrome ci-dessus est indispensable pour
# SCRAPER (DuckDuckGo sert une page differente a un client non-navigateur), mais il fait
# REFUSER les API honnetes : mesure du 10/09, `r.jina.ai` rend 403 avec le faux Chrome et
# 200 avec l'UA ci-dessous ; l'API Wikipedia fait pareil (la Wikimedia Foundation exige un
# UA identifiable et contactable). Un seul UA pour tout le monde, c'est la moitie des
# moteurs perdue — sans que rien ne le signale.
USER_AGENT_HONNETE = "AgentMk06Bot/1.0 (+https://t.me/Agentmk06bot)"


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
# 🚨 GUILLEMETS SIMPLES *ET* DOUBLES. DuckDuckGo Lite ecrit aujourd'hui
# `class='result-link'` en APOSTROPHES : une regex qui n'accepte que `"` rend zero resultat
# — sans erreur, sans avertissement, la chaine bascule en silence sur le moteur suivant.
# Mesure du 10/09 : 0 resultat avec l'ancienne regex, 10 avec celle-ci, sur la meme requete.
_HREF = re.compile(r"""\bhref=(?:"([^"]+)"|'([^']+)')""", re.I)


def _links(body: str, css_class: str) -> list[tuple[str, str]]:
    """Renvoie (href, titre) pour chaque <a> portant la classe donnee."""
    cls = re.escape(css_class)
    motif = re.compile(
        r"<a\b(?P<attrs>[^>]*\bclass=(?:\"[^\"]*" + cls + r"[^\"]*\"|'[^']*" + cls
        + r"[^']*')[^>]*)>(?P<title>.*?)</a>",
        re.I | re.S,
    )
    out: list[tuple[str, str]] = []
    for match in motif.finditer(body):
        href = _HREF.search(match.group("attrs"))
        if href:
            out.append((href.group(1) or href.group(2), match.group("title")))
    return out


_LITE_SNIPPET = re.compile(
    r"<td[^>]*\bclass=(?:\"[^\"]*result-snippet[^\"]*\"|'[^']*result-snippet[^']*')"
    r"[^>]*>(?P<snippet>.*?)</td>",
    re.I | re.S,
)
_HTML_SNIPPET = re.compile(
    r"<a[^>]*\bclass=(?:\"[^\"]*result__snippet[^\"]*\"|'[^']*result__snippet[^']*')"
    r"[^>]*>(?P<snippet>.*?)</a>",
    re.I | re.S,
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
        # 🚨 CE N'EST PAS UNE SOURCE, C'EST UN RESUME. Tavily synthetise une reponse a
        # partir des pages trouvees ; l'ancien libelle la presentait avec l'URL
        # `https://tavily.com`, ce qui invite le modele a citer « Tavily » comme origine
        # d'un chiffre. C'est exactement la fausse autorite que le prompt systeme
        # interdit. On l'etiquette donc pour ce qu'elle est : une piste a verifier
        # dans les resultats qui suivent.
        results.append(Result(
            "[RESUME AUTOMATIQUE — PAS UNE SOURCE, ne la cite pas : verifie dans les "
            "resultats ci-dessous]",
            "", answer,
        ))
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


#: Moteurs pilotes par la bibliotheque `ddgs`, dans l'ordre de FIABILITE MESUREE le 10/09
#: (4 requetes chacun, depuis cette machine) : bing 4/4, yandex 3/4, brave 1/4, ddg 0/4.
#: 🚨 DuckDuckGo en dernier, et ce n'est pas un detail : il etait a zero apres une poignee
#: d'appels — la limitation est cumulative par IP. C'est exactement la fragilite annoncee
#: dans l'en-tete de ce module ; d'ou la ROTATION, plutot qu'un moteur favori.
DDGS_MOTEURS = ("bing", "yandex", "brave", "duckduckgo")


async def _ddgs(client, query: str, limit: int, cfg: SearchConfig) -> list[Result]:
    """Recherche via `ddgs` : Bing, Yandex, Brave et DuckDuckGo, SANS aucune cle.

    🚨 Pourquoi une bibliotheque plutot que nos propres scrapers : mes regex maison se
    faisaient refuser en 403 sur Brave et Ecosia et en 429 sur Brave-HTML, la ou `ddgs`
    passe — elle entretient les en-tetes et l'usurpation TLS que ces moteurs exigent, et
    elle est mise a jour quand ils changent. Un scraper maison, lui, casse en silence
    (constate ici meme : le parseur ddg_lite rendait zero resultat depuis un changement de
    guillemets).

    La bibliotheque est SYNCHRONE : on l'execute dans un fil pour ne pas geler la boucle
    d'evenements, donc le bot entier et toutes ses conversations.
    """
    try:
        from ddgs import DDGS
    except ImportError as exc:  # pragma: no cover - depend de l'installation
        raise _Skip() from exc

    import asyncio

    def _un_moteur(moteur: str) -> list[Result]:
        brut = DDGS().text(query, max_results=limit, backend=moteur)
        out: list[Result] = []
        for item in brut or []:
            titre = _clean(str(item.get("title") or ""))
            url = str(item.get("href") or item.get("url") or "")
            if titre and url.startswith("http"):
                out.append(Result(titre, url, _clean(str(item.get("body") or ""))))
        return out[:limit]

    dernier = ""
    for moteur in DDGS_MOTEURS:
        try:
            res = await asyncio.to_thread(_un_moteur, moteur)
        except Exception as exc:  # noqa: BLE001 - un moteur limite ne doit pas tout arreter
            dernier = f"{moteur}: {type(exc).__name__}"
            continue
        if res:
            log.info("ddgs : %d resultats via %s", len(res), moteur)
            return res
        dernier = f"{moteur}: aucun resultat"
    raise _Blocked("ddgs, tous moteurs epuises (%s)" % dernier)


async def _exa(client, query: str, limit: int, cfg: SearchConfig) -> list[Result]:
    """Exa : recherche NEURONALE (par sens, pas par mots-cles). Cle EXA_API_KEY."""
    cle = os.getenv("EXA_API_KEY", "").strip()
    if not cle:
        raise _Skip()
    response = await client.post(
        "https://api.exa.ai/search",
        headers={"x-api-key": cle, "Content-Type": "application/json"},
        json={"query": query, "numResults": limit, "contents": {"text": {"maxCharacters": 400}}},
    )
    response.raise_for_status()
    out: list[Result] = []
    for item in response.json().get("results", [])[:limit]:
        titre = _clean(str(item.get("title") or ""))
        url = str(item.get("url") or "")
        if titre and url.startswith("http"):
            out.append(Result(titre, url, _clean(str(item.get("text") or ""))))
    return out


async def _wikipedia(client, query: str, limit: int, cfg: SearchConfig) -> list[Result]:
    """Wikipedia : gratuit, sans cle, et le meilleur ancrage FACTUEL disponible.

    🚨 Exige un User-Agent identifiable : avec le faux Chrome, l'API rend une page
    d'erreur non-JSON (mesure du 10/09)."""
    response = await client.get(
        "https://fr.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query,
                "format": "json", "srlimit": limit},
        headers={"User-Agent": USER_AGENT_HONNETE},
    )
    response.raise_for_status()
    out: list[Result] = []
    for item in response.json().get("query", {}).get("search", [])[:limit]:
        titre = _clean(str(item.get("title", "")))
        if not titre:
            continue
        url = "https://fr.wikipedia.org/wiki/" + urllib.parse.quote(titre.replace(" ", "_"))
        out.append(Result(titre, url, _clean(re.sub(r"<[^>]+>", "", str(item.get("snippet", ""))))))
    return out


async def _openalex(client, query: str, limit: int, cfg: SearchConfig) -> list[Result]:
    """OpenAlex : 250 millions de travaux scientifiques, sans cle.

    Sert a REPONDRE PAR UNE SOURCE plutot que par une affirmation — meme logique que le
    registre UE 432/2012 pour les allegations de sante : on cite, on n'invente pas."""
    response = await client.get(
        "https://api.openalex.org/works",
        params={"search": query, "per-page": limit},
        headers={"User-Agent": USER_AGENT_HONNETE},
    )
    response.raise_for_status()
    out: list[Result] = []
    for w in response.json().get("results", [])[:limit]:
        titre = _clean(str(w.get("display_name") or ""))
        if not titre:
            continue
        url = str(w.get("doi") or w.get("id") or "")
        annee = w.get("publication_year") or "?"
        cite = w.get("cited_by_count", 0)
        out.append(Result(titre, url, f"{annee} · cite {cite} fois"))
    return out


async def _github(client, query: str, limit: int, cfg: SearchConfig) -> list[Result]:
    """GitHub : depots reels. Avec GITHUB_TOKEN le quota passe de 10 a 30 requetes/min."""
    entetes = {"User-Agent": USER_AGENT_HONNETE, "Accept": "application/vnd.github+json"}
    jeton = os.getenv("GITHUB_TOKEN", "").strip()
    if jeton:
        entetes["Authorization"] = "Bearer " + jeton
    response = await client.get(
        "https://api.github.com/search/repositories",
        params={"q": query, "per_page": limit, "sort": "stars"},
        headers=entetes,
    )
    response.raise_for_status()
    out: list[Result] = []
    for it in response.json().get("items", [])[:limit]:
        out.append(Result(
            str(it.get("full_name", "")),
            str(it.get("html_url", "")),
            "%s ★ · %s · %s" % (it.get("stargazers_count", 0),
                                it.get("language") or "?",
                                _clean(str(it.get("description") or ""))),
        ))
    return out


BACKENDS = {
    "tavily": _tavily,
    "exa": _exa,
    "brave": _brave,
    "searxng": _searxng,
    "ddgs": _ddgs,
    "ddg_html": _ddg_html,
    "ddg_lite": _ddg_lite,
    "wikipedia": _wikipedia,
    "openalex": _openalex,
    "github": _github,
}

#: Moteurs sans cle, toujours tentes en dernier recours — dans l'ordre de FIABILITE MESUREE.
#: 🚨 `ddgs` EN TETE : c'est le seul a atteindre Bing, Yandex et Brave depuis cette machine.
#: Mes propres scrapers y recoltaient 403 et 429 ; la bibliotheque passe, et elle est
#: maintenue quand les moteurs changent leur balisage.
#: Les scrapers maison restent DERRIERE, en filet : ils ne dependent d'aucun paquet, donc
#: ils survivent a une desinstallation ou a une rupture d'API de `ddgs`.
#: wikipedia/openalex/github ferment la marche : ils ne repondent pas a tout, mais quand ils
#: repondent c'est une SOURCE (article, etude citee, depot), pas un resultat de moteur.
KEYLESS = ("ddgs", "ddg_html", "ddg_lite", "wikipedia", "openalex", "github")


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
    if os.getenv("EXA_API_KEY", "").strip():
        ordered.append("exa")
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
                # 🚨 Espacement AVANT l'appel reseau. Le modele emet souvent plusieurs
                # recherches dans le meme tour : sans ce frein elles partent en rafale, et
                # c'est la rafale qui fait limiter l'IP — la panne qu'on cherche a eviter.
                from ..cache import patiente

                await patiente(name)
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
