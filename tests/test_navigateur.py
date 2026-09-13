# -*- coding: utf-8 -*-
"""Les garde-fous de l'acces au navigateur de la machine.

🚨 POURQUOI ILS COMPTENT. Le Chrome joint par CDP est celui qui publie sur TikTok et
YouTube. Il a deja ete tue une fois par l'agent, et les sessions ont ete perdues. Lui donner
maintenant de quoi CLIQUER dedans serait pire : un clic peut publier un brouillon, supprimer
une video ou fermer la session. La regle est donc appliquee dans le code, pas dans une
consigne — un GLM decensure n'obeit pas a une interdiction ecrite.

    il REGARDE partout, il n'AGIT que la ou le patron n'est pas connecte.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hermes.errors import ToolError
from hermes.tools import ToolContext, build_registry
from hermes.tools.navigateur import _sensible, _verifie_url


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, exec_timeout=20, output_limit=4000,
                       request_timeout=10, search_url="x")


def test_les_outils_sont_declares() -> None:
    noms = set(build_registry().tools)
    assert {"navigateur_onglets", "navigateur_lire", "navigateur_agir",
            "navigateur_capture", "navigateur_fermer"} <= noms


# --- adresses refusees ------------------------------------------------------ #

REFUSEES = [
    # `file://` contournerait la limite du workspace : l'agent lirait tout le disque.
    "file:///C:/Windows/win.ini",
    "FILE:///C:/Users/Administrator/.env",
    # `chrome://` donne les reglages, l'historique et les mots de passe enregistres.
    "chrome://settings/passwords",
    "about:config",
    "data:text/html,<script>alert(1)</script>",
    "view-source:https://example.com",
    # Une simple VISITE suffit a fermer la session : ce n'est pas un bouton, c'est une URL.
    "https://accounts.google.com/logout",
    "https://www.tiktok.com/signout",
    "https://exemple.fr/compte/deconnexion",
    "https://exemple.fr/api/delete/42",
]


@pytest.mark.parametrize("url", REFUSEES)
def test_url_refusee(url: str) -> None:
    with pytest.raises(ToolError):
        _verifie_url(url)


ACCEPTEES = [
    "https://example.com",
    "http://127.0.0.1:9300/rapport",
    "https://www.tiktok.com/tiktokstudio/upload",     # lire un ecran connecte : permis
    "https://studio.youtube.com/channel/UCxxxx/videos",
]


@pytest.mark.parametrize("url", ACCEPTEES)
def test_url_acceptee(url: str) -> None:
    assert _verifie_url(url) == url


def test_url_vide() -> None:
    with pytest.raises(ToolError):
        _verifie_url("   ")


# --- lire oui, agir non ----------------------------------------------------- #

class FaussePage:
    """Juste ce que la garde regarde : l'adresse de la page."""

    def __init__(self, url: str) -> None:
        self.url = url

    def is_closed(self) -> bool:
        return False


SENSIBLES = [
    "https://www.tiktok.com/tiktokstudio/upload",
    "https://studio.youtube.com/channel/UCxxxx/videos",
    "https://accounts.google.com/signin/v2",
    "https://www.redbubble.com/portfolio/images",
    "https://fr.pinterest.com/pin/create/",
]


@pytest.mark.parametrize("url", SENSIBLES)
def test_action_refusee_sur_site_connecte(url: str, ctx: ToolContext, monkeypatch) -> None:
    import hermes.tools.navigateur as mod
    monkeypatch.setattr(mod, "_ma_page", lambda creer=True: _resolu(FaussePage(url)))
    sortie = asyncio.run(build_registry().dispatch(
        ctx, "navigateur_agir", {"action": "cliquer", "selecteur": "button"}))
    assert "refusee" in sortie
    assert _sensible(url) in sortie                  # l'agent apprend POURQUOI


def test_action_permise_ailleurs(ctx: ToolContext, monkeypatch) -> None:
    """Une garde qui bloque partout serait une garde inutilisable."""
    import hermes.tools.navigateur as mod
    assert _sensible("https://fr.wikipedia.org/wiki/Persil") == ""
    monkeypatch.setattr(mod, "_ma_page",
                        lambda creer=True: _resolu(FaussePage("https://fr.wikipedia.org/")))
    # On ne va pas plus loin que la garde : la page factice n'a pas de `click`.
    with pytest.raises(Exception):
        asyncio.run(mod.navigateur_agir.handler(
            ctx, {"action": "cliquer", "selecteur": "a"}))


def _resolu(valeur):
    """Rend une valeur deja prete, la ou le code attend une coroutine."""
    async def _f():
        return valeur
    return _f()


def test_capture_refuse_un_chemin(ctx: ToolContext) -> None:
    """Le nom de capture ne doit pas servir a ecrire hors du workspace."""
    for nom in ("../dehors.png", "C:/Windows/x.png", "sous/dossier.png"):
        sortie = asyncio.run(build_registry().dispatch(
            ctx, "navigateur_capture", {"nom": nom}))
        assert "simple nom de fichier" in sortie, nom


# --- le contournement par shell --------------------------------------------- #

PILOTAGES = [
    "python -c 'from playwright.sync_api import sync_playwright'",
    "p.chromium.connect_over_cdp('http://127.0.0.1:9222')",
    "node script.js --cdp ws://127.0.0.1:9222/devtools/browser",
    "pip install selenium && python pilote.py",
    "puppeteer.connect({browserURL: 'http://127.0.0.1:9222'})",
]


@pytest.mark.parametrize("commande", PILOTAGES)
def test_shell_refuse_le_pilotage_direct(commande: str) -> None:
    """Sans ceci les gardes du navigateur ne valent rien : shell donne Playwright."""
    from hermes.tools.shell import _cible_protegee
    motif = _cible_protegee(commande)
    assert motif is not None, commande
    assert "navigateur_lire" in motif                # on lui dit par ou passer


DIAGNOSTICS = [
    "netstat -ano | findstr :9222",                  # regarder si le port repond : legitime
    "curl http://127.0.0.1:9222/json/version",
]


@pytest.mark.parametrize("commande", DIAGNOSTICS)
def test_le_diagnostic_du_port_reste_permis(commande: str) -> None:
    from hermes.tools.shell import _cible_protegee
    assert _cible_protegee(commande) is None, commande


# --- les onglets orphelins --------------------------------------------------- #

class PageMarquee:
    """Onglet ouvert par une execution precedente de l'agent."""

    def __init__(self, marque: str, url: str = "https://exemple.fr") -> None:
        self.marque, self.url, self.ferme = marque, url, False

    async def evaluate(self, _js: str) -> str:
        return self.marque

    async def close(self) -> None:
        self.ferme = True

    def is_closed(self) -> bool:
        return self.ferme


class PageRecalcitrante(PageMarquee):
    """Onglet chrome:// ou en cours de chargement : evaluate leve."""

    async def evaluate(self, _js: str) -> str:
        raise RuntimeError("execution context was destroyed")


class FauxContexte:
    def __init__(self, pages) -> None:
        self.pages = pages


def test_les_orphelins_sont_ramasses() -> None:
    """Un onglet par redemarrage du service finirait par figer la publication."""
    from hermes.tools.navigateur import _ramasse_mes_orphelins
    mien, patron = PageMarquee("abc123"), PageMarquee("")
    fermes = asyncio.run(_ramasse_mes_orphelins(FauxContexte([mien, patron])))
    assert fermes == 1
    assert mien.ferme and not patron.ferme       # on ne ferme JAMAIS un onglet du patron


def test_un_onglet_illisible_ne_bloque_pas() -> None:
    from hermes.tools.navigateur import _ramasse_mes_orphelins
    rétif, mien = PageRecalcitrante("x"), PageMarquee("abc")
    assert asyncio.run(_ramasse_mes_orphelins(FauxContexte([rétif, mien]))) == 1
    assert not rétif.ferme and mien.ferme


# --------------------------------------------------------------------------- #
# Le port 9222 ne se prend pas — le cas d'Obscura.
# 🚨 Le navigateur `obscura` installe dans le workspace ouvre son serveur CDP sur le port
# 9222 PAR DEFAUT : celui du Chrome qui publie sur TikTok et YouTube. L'agent a redige une
# fiche entiere sur cet outil sans relever le conflit, alors que sa note machine le lui
# disait. Une consigne qu'on ne relie pas au cas particulier ne protege de rien.
# --------------------------------------------------------------------------- #

ECOUTES_REFUSEES = [
    "obscura serve",
    "workspace/bin/obscura.exe serve",
    "obscura serve --port 9222",
    "obscura serve -p 9222",
    "python -m http.server --port 9222",
    "node serveur.js --port 9222",
]


@pytest.mark.parametrize("commande", ECOUTES_REFUSEES)
def test_ecouter_sur_9222_est_refuse(commande: str) -> None:
    from hermes.tools.shell import _cible_protegee
    motif = _cible_protegee(commande)
    assert motif is not None, commande
    assert "9222" in motif
    assert "9300" in motif or "9310" in motif        # on lui dit quoi faire a la place


ECOUTES_PERMISES = [
    "obscura serve --port 9310",
    "obscura serve -p 9400",
    "obscura fetch https://example.com",             # lire une page ne prend aucun port
    "obscura scrape https://a.fr https://b.fr",
    "curl http://127.0.0.1:9222/json/version",       # diagnostiquer reste permis
    "netstat -ano | findstr :9222",
    "python -m http.server --port 9310",
]


@pytest.mark.parametrize("commande", ECOUTES_PERMISES)
def test_le_reste_passe(commande: str) -> None:
    from hermes.tools.shell import _cible_protegee
    assert _cible_protegee(commande) is None, commande
