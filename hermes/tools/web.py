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
from .search import USER_AGENT, USER_AGENT_HONNETE, Result, SearchConfig, search

_CACHES: dict[str, Any] = {}


def _cache(ctx: ToolContext):
    """Cache de recherche, un par workspace, cree a la demande.

    🚨 Jamais un point de panne : si SQLite refuse (disque plein, droits), on renvoie None
    et la recherche part sur le reseau comme avant. Un cache casse doit degrader, pas bloquer.
    """
    cle = str(ctx.workspace)
    if cle not in _CACHES:
        try:
            from ..cache import CacheRecherche

            _CACHES[cle] = CacheRecherche(ctx.workspace / ".hermes_cache" / "recherches.db")
        except Exception:  # noqa: BLE001
            _CACHES[cle] = None
    return _CACHES[cle]

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
    """🚨 Passe par le CACHE avant le reseau. La recherche de l'agent tient sur un seul
    moteur solide et une seule IP : chaque requete evitee est de la marge gagnee avant la
    limitation. Voir `hermes/cache.py`."""
    query = str(args["query"]).strip()
    if not query:
        raise ToolError("requete vide.")
    limit = max(1, min(int(args.get("limit") or 5), 15))

    cache = _cache(ctx)
    if cache is not None:
        garde = cache.lit(query, limit)
        if garde is not None:
            lignes, moteur = garde
            return "\n".join(
                Result(x["title"], x["url"], x.get("snippet", "")).render(i + 1)
                for i, x in enumerate(lignes)
            ) + f"\n[en cache, moteur {moteur}]"

    results, info = await search(query, limit, _config(ctx))
    if results:
        if cache is not None:
            cache.ecrit(query, limit,
                        [{"title": r.title, "url": r.url, "snippet": r.snippet}
                         for r in results], info)
        return "\n".join(r.render(i + 1) for i, r in enumerate(results))

    detail = f"\nDetail des moteurs : {info}" if info else ""
    raise ToolError(
        f"aucun resultat pour {query!r} sur les moteurs disponibles.{detail}\n"
        "Pour une recherche fiable, renseigne une cle BRAVE_API_KEY ou TAVILY_API_KEY, "
        "ou une instance HERMES_SEARXNG_URL. Sinon, tente fetch_url sur une source connue."
    )


def _via_trafilatura(html: str) -> str:
    """Extrait le contenu PRINCIPAL, en local, sans appeler personne.

    Mesure du 10/09 sur l'article Wikipedia « Safran » : 512 k caracteres bruts, 62 k apres
    le strip de balises maison (12 % — menus, pieds de page et bandeaux compris), **52 k avec
    trafilatura en 1,3 s**. Le service distant en rendait 131 k en 4,2 s : plus lent, plus
    bavard, et il fait dependre le bot d'un tiers.
    """
    try:
        import trafilatura
    except ImportError:
        return ""
    try:
        return trafilatura.extract(html, include_links=False, include_comments=False) or ""
    except Exception:  # noqa: BLE001 - un extracteur qui echoue ne doit pas casser l'outil
        return ""


async def _via_lecteur(url: str, timeout: int) -> str:
    """Lit la page via r.jina.ai, qui rend du MARKDOWN propre (titres, listes, liens gardes).

    🚨 Pourquoi ce detour plutot que le strip de balises maison : `_text()` ci-dessus efface
    les balises mais garde TOUT — menus, pieds de page, bandeaux cookies, scripts inline
    survivants. Sur un article, l'essentiel se noie et le budget de contexte part en decor.
    Le lecteur, lui, extrait le contenu PRINCIPAL et le structure.

    🚨 ET SURTOUT : PAS DE FAUX USER-AGENT ICI. Mesure du 10/09 — avec le Chrome usurpe le
    service rend 403, sans lui (ou avec un UA honnete) il rend 200. C'est le meme piege que
    pour l'API Wikipedia. Le deguisement qui debloque les scrapers bloque les API honnetes.
    """
    import httpx

    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        r = await client.get("https://r.jina.ai/" + url,
                             headers={"User-Agent": USER_AGENT_HONNETE})
        r.raise_for_status()
        return r.text


@tool(
    "fetch_url",
    "Recupere une page web et renvoie son contenu principal en texte lisible.",
    {
        "url": {"type": "string", "description": "URL complete, http(s) uniquement."},
        "raw": {"type": "boolean", "description": "Renvoyer le corps brut sans conversion."},
    },
    ["url"],
)
async def fetch_url(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Trois etages, du moins couteux au plus couteux.

    🚨 L'ORDRE EST UNE MESURE, PAS UNE PREFERENCE (10/09). trafilatura est local, rapide et
    ne depend de personne — il passe donc en premier. Mais il ne peut rien extraire d'une
    page que l'on n'a pas pu RECUPERER : EUR-Lex, notre source d'allegations de sante, rend
    **zero caractere** en recuperation directe. Le lecteur distant, lui, en a rendu 87 000.
    Aucun des deux ne suffit seul ; c'est pour ca qu'ils sont chaines dans cet ordre.
    """
    import httpx

    url = str(args["url"]).strip()
    if not url.startswith(("http://", "https://")):
        raise ToolError("l'URL doit commencer par http:// ou https://.")

    # 🚨 DEUX TENTATIVES, DEUX IDENTITES. Certains sites refusent un client qui n'a pas
    # l'air d'un navigateur ; d'autres refusent justement le deguisement. Mesure du 10/09 :
    # Wikipedia rend **403 au faux Chrome et 200 a l'UA honnete** — sans cette seconde
    # tentative, la lecture directe echouait en silence sur toute la Wikipedia et retombait
    # sur le lecteur distant, plus lent et dependant d'un tiers.
    body, final, echec = "", url, ""
    for ua in (USER_AGENT, USER_AGENT_HONNETE):
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=ctx.request_timeout,
                headers={"User-Agent": ua, "Accept-Language": "fr,en;q=0.8"},
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                body, final, echec = response.text, str(response.url), ""
                break
        except Exception as exc:  # noqa: BLE001 - httpx expose une dizaine de types
            echec = f"{type(exc).__name__}: {exc}"

    if args.get("raw"):
        if not body:
            raise ToolError(f"requete vers {url} echouee : {echec}")
        return clip(body, ctx.output_limit)

    if body.lstrip().startswith(("{", "[")):
        try:
            contenu = json.dumps(json.loads(body), ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            contenu = _text(body)
        return (f"# {final}\n\n" if final != url else "") + clip(contenu, ctx.output_limit)

    # 1) extraction locale du contenu principal
    contenu = _via_trafilatura(body) if body else ""
    # 2) lecteur distant : le seul a passer sur les pages qui refusent la recuperation
    if len(contenu.strip()) < 200:
        try:
            lu = await _via_lecteur(url, max(ctx.request_timeout, 45))
            if len(lu.strip()) > len(contenu.strip()):
                return clip(lu, ctx.output_limit)
        except Exception:  # noqa: BLE001 - le lecteur est un bonus, jamais un point de panne
            pass
    # 3) filet : le retrait de balises, qui garde le decor mais ne rend jamais vide
    if len(contenu.strip()) < 200 and body:
        contenu = _text(body)
    if not contenu.strip():
        raise ToolError(f"aucun contenu lisible sur {url}." + (f" ({echec})" if echec else ""))

    return (f"# {final}\n\n" if final != url else "") + clip(contenu, ctx.output_limit)


TOOLS = (web_search, fetch_url)
