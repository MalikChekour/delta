"""Recherche multi-moteurs : analyseurs testes sur des fragments reels, et
chaine de repli testee sans reseau."""
# ruff: noqa: E501 - les fixtures HTML reproduisent le balisage reel, lignes longues incluses

from __future__ import annotations

import pytest

from hermes.tools.search import (
    BACKENDS,
    KEYLESS,
    Result,
    SearchConfig,
    _Blocked,
    _Skip,
    parse_brave,
    parse_ddg_html,
    parse_ddg_lite,
    parse_searxng,
    parse_tavily,
    resolve_backends,
    search,
)

# -- fixtures : balisage tel que renvoye par chaque source ------------------

DDG_LITE = """
<html><body><table>
<tr><td><a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fasyncio.html&rut=abc" class="result-link">asyncio — Asynchronous I/O</a></td></tr>
<tr><td class="result-snippet">asyncio is a library to write concurrent code using the async/await syntax.</td></tr>
<tr><td><a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Frealpython.com%2Fasync-io-python%2F&rut=def" class="result-link">Async IO in Python: A Complete Walkthrough</a></td></tr>
<tr><td class="result-snippet">A complete guide to asynchronous programming.</td></tr>
</table></body></html>
"""

DDG_HTML = """
<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa">Titre A</a>
<a class="result__snippet" href="x">Extrait A tres informatif</a></div>
<div class="result"><a href="https://example.org/b" class="result__a">Titre B</a>
<a class="result__snippet">Extrait B</a></div>
"""

BLOCKED = "<html><body>Unfortunately, bots use DuckDuckGo too. Anomaly detected.</body></html>"


def test_lite_extrait_titres_urls_extraits():
    out = parse_ddg_lite(DDG_LITE, 5)
    assert len(out) == 2
    assert out[0].url == "https://docs.python.org/3/library/asyncio.html"
    assert out[0].title.startswith("asyncio")
    assert "concurrent" in out[0].snippet
    assert out[1].url == "https://realpython.com/async-io-python/"


def test_html_extrait_et_ordre_des_attributs_indifferent():
    out = parse_ddg_html(DDG_HTML, 5)
    assert [r.url for r in out] == ["https://example.org/a", "https://example.org/b"]
    assert out[0].snippet == "Extrait A tres informatif"


def test_limite_respectee():
    assert len(parse_ddg_lite(DDG_LITE, 1)) == 1


def test_page_anti_robot_reconnue():
    from hermes.tools.search import _looks_blocked

    assert _looks_blocked(BLOCKED)
    assert not _looks_blocked(DDG_LITE)


def test_searxng_json():
    payload = {"results": [
        {"url": "https://a.test", "title": "A", "content": "extrait a"},
        {"url": "not-a-url", "title": "rejete", "content": ""},
        {"url": "https://b.test", "title": "B", "content": "extrait b"},
    ]}
    out = parse_searxng(payload, 5)
    assert [r.url for r in out] == ["https://a.test", "https://b.test"]


def test_brave_json():
    payload = {"web": {"results": [
        {"url": "https://a.test", "title": "A", "description": "desc a"},
    ]}}
    out = parse_brave(payload, 5)
    assert out[0].snippet == "desc a"


def test_tavily_json_met_la_reponse_en_tete():
    payload = {"answer": "La reponse est 42.",
               "results": [{"url": "https://src.test", "title": "Source", "content": "detail"}]}
    out = parse_tavily(payload, 5)
    assert "42" in out[0].snippet
    assert out[1].url == "https://src.test"


# -- resolution de l'ordre des moteurs --------------------------------------

def test_auto_place_les_moteurs_avec_cle_en_tete(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "x")
    for var in ("TAVILY_API_KEY", "EXA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    cfg = SearchConfig(backends=("auto",), searxng_url="https://s.test")
    ordre = resolve_backends(cfg)
    assert ordre[0] == "brave"
    assert "searxng" in ordre
    # Les moteurs sans cle ferment la marche, dans l'ordre declare par KEYLESS.
    assert ordre[-len(KEYLESS):] == list(KEYLESS)


def test_auto_sans_cle_ne_garde_que_les_moteurs_libres(monkeypatch):
    for var in ("TAVILY_API_KEY", "BRAVE_API_KEY", "EXA_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert resolve_backends(SearchConfig(backends=("auto",))) == list(KEYLESS)


def test_ddg_html_passe_avant_ddg_lite():
    """🚨 Mesure du 10/09 : `ddg_html` rend 9-10 resultats de facon stable la ou `ddg_lite`
    est le premier a servir une page anti-robot. L'ordre n'est donc pas cosmetique."""
    assert KEYLESS.index("ddg_html") < KEYLESS.index("ddg_lite")


def test_les_moteurs_sans_cle_sont_tous_connus():
    """Un nom mal orthographie dans KEYLESS disparaitrait en silence (BACKENDS.get -> None)."""
    assert all(nom in BACKENDS for nom in KEYLESS)


def test_liste_explicite_respectee():
    cfg = SearchConfig(backends=("searxng", "ddg_html", "inconnu"))
    assert resolve_backends(cfg) == ["searxng", "ddg_html"]


# -- chaine de repli (sans reseau) ------------------------------------------

class FakeClient:
    def __init__(self, comportement):
        self.comportement = comportement
        self.appels = []

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def _agir(self, cle):
        self.appels.append(cle)
        action = self.comportement[cle]
        if isinstance(action, Exception):
            raise action
        return action

    async def get(self, url, **kw): return await self._agir("get")
    async def post(self, url, **kw): return await self._agir("post")


async def test_repli_du_premier_moteur_mort_au_suivant(monkeypatch):
    """Le coeur du correctif : un moteur qui echoue ne coupe pas la recherche."""
    appels = []

    async def mort(client, q, limit, cfg):
        appels.append("ddg_lite")
        raise _Blocked("anti-robot")

    async def vivant(client, q, limit, cfg):
        appels.append("ddg_html")
        return [Result("Trouve", "https://ok.test", "extrait")]

    monkeypatch.setitem(BACKENDS, "ddg_lite", mort)
    monkeypatch.setitem(BACKENDS, "ddg_html", vivant)
    results, gagnant = await search("q", 5, SearchConfig(backends=("ddg_lite", "ddg_html")))
    assert gagnant == "ddg_html"
    assert results[0].url == "https://ok.test"
    assert appels == ["ddg_lite", "ddg_html"]


async def test_moteur_non_configure_est_saute_sans_echec(monkeypatch):
    async def saute(client, q, limit, cfg):
        raise _Skip("pas de cle")

    async def vivant(client, q, limit, cfg):
        return [Result("T", "https://ok.test")]

    monkeypatch.setitem(BACKENDS, "tavily", saute)
    monkeypatch.setitem(BACKENDS, "ddg_lite", vivant)
    results, gagnant = await search("q", 5, SearchConfig(backends=("tavily", "ddg_lite")))
    assert gagnant == "ddg_lite" and results


async def test_echec_total_renvoie_le_detail(monkeypatch):
    async def mort(client, q, limit, cfg):
        raise RuntimeError("reseau coupe")

    monkeypatch.setitem(BACKENDS, "ddg_lite", mort)
    monkeypatch.setitem(BACKENDS, "ddg_html", mort)
    results, info = await search("q", 5, SearchConfig(backends=("ddg_lite", "ddg_html")))
    assert results == []
    assert "reseau coupe" in info


async def test_premier_moteur_qui_repond_gagne(monkeypatch):
    async def premier(client, q, limit, cfg):
        return [Result("P", "https://p.test")]

    async def second(client, q, limit, cfg):
        raise AssertionError("ne doit pas etre appele")

    monkeypatch.setitem(BACKENDS, "brave", premier)
    monkeypatch.setitem(BACKENDS, "ddg_lite", second)
    results, gagnant = await search("q", 5, SearchConfig(backends=("brave", "ddg_lite")))
    assert gagnant == "brave"


# -- moteurs reels contre un faux client HTTP -------------------------------

class Reponse:
    def __init__(self, text="", payload=None):
        self.text = text
        self._payload = payload

    def raise_for_status(self): pass
    def json(self): return self._payload


class ClientScriptable:
    def __init__(self, get=None, post=None):
        self._get, self._post = get, post

    async def get(self, url, **kw): return self._get

    async def post(self, url, **kw): return self._post


async def test_backend_ddg_lite_de_bout_en_bout():
    from hermes.tools.search import _ddg_lite

    client = ClientScriptable(post=Reponse(text=DDG_LITE))
    out = await _ddg_lite(client, "q", 5, SearchConfig(backends=("ddg_lite",)))
    assert out[0].url == "https://docs.python.org/3/library/asyncio.html"


async def test_backend_ddg_lite_detecte_le_blocage():
    from hermes.tools.search import _Blocked, _ddg_lite

    client = ClientScriptable(post=Reponse(text=BLOCKED))
    with pytest.raises(_Blocked):
        await _ddg_lite(client, "q", 5, SearchConfig(backends=("ddg_lite",)))


async def test_backend_searxng_de_bout_en_bout():
    from hermes.tools.search import _searxng

    payload = {"results": [{"url": "https://ok.test", "title": "OK", "content": "c"}]}
    client = ClientScriptable(get=Reponse(payload=payload))
    out = await _searxng(client, "q", 5, SearchConfig(backends=("searxng",), searxng_url="https://s.test"))
    assert out[0].url == "https://ok.test"


async def test_backend_searxng_saute_si_non_configure():
    from hermes.tools.search import _searxng, _Skip

    with pytest.raises(_Skip):
        await _searxng(ClientScriptable(), "q", 5, SearchConfig(backends=("searxng",)))


async def test_backend_brave_saute_sans_cle(monkeypatch):
    from hermes.tools.search import _brave, _Skip

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    with pytest.raises(_Skip):
        await _brave(ClientScriptable(), "q", 5, SearchConfig(backends=("brave",)))


# -- moteurs sans cle ajoutes le 2026-09-10 ---------------------------------

def test_ddgs_est_en_tete_des_moteurs_libres():
    """🚨 `ddgs` est le seul a atteindre Bing, Yandex et Brave depuis cette machine ;
    les scrapers maison y recoltaient 403 et 429. Il passe donc AVANT eux."""
    assert KEYLESS[0] == "ddgs"
    assert KEYLESS.index("ddgs") < KEYLESS.index("ddg_html")


def test_les_scrapers_maison_restent_en_filet():
    """Ils ne dependent d'aucun paquet : ils survivent a une desinstallation de `ddgs`."""
    assert "ddg_html" in KEYLESS and "ddg_lite" in KEYLESS


def test_ordre_ddgs_par_fiabilite_mesuree():
    """Mesure du 10/09, 4 requetes par moteur : bing 4/4, yandex 3/4, brave 1/4, ddg 0/4."""
    from hermes.tools.search import DDGS_MOTEURS

    assert DDGS_MOTEURS[0] == "bing"
    assert DDGS_MOTEURS.index("duckduckgo") == len(DDGS_MOTEURS) - 1


def test_le_resume_tavily_est_signale_comme_non_citable():
    """🚨 Tavily renvoie un `answer` SYNTHETISE a partir des pages trouvees. L'ancien
    libelle le presentait avec l'URL `https://tavily.com` : le modele etait invite a citer
    « Tavily » comme origine d'un chiffre, soit exactement la fausse autorite que le prompt
    systeme interdit. Il doit etre etiquete pour ce qu'il est."""
    payload = {"answer": "La reponse est 42.",
               "results": [{"url": "https://src.test", "title": "Source", "content": "detail"}]}
    out = parse_tavily(payload, 5)
    resume = out[0]
    assert "PAS UNE SOURCE" in resume.title
    assert resume.url == "", "un resume ne doit porter AUCUNE URL citable"
    assert "42" in resume.snippet
    # la vraie source, elle, garde son URL
    assert out[1].url == "https://src.test"
