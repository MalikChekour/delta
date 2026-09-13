# -*- coding: utf-8 -*-
"""Ecran, souris et clavier : la passerelle « mains » et ses garde-fous.

🚨 C'EST LA CAPACITE LA PLUS DANGEREUSE DE L'AGENT. Les regles du navigateur — lire oui,
cliquer non sur les sites ou le patron est connecte — seraient contournees par un simple clic
physique dans la fenetre de Chrome. Le meme interdit doit donc exister a hauteur de souris,
et il vit dans `mains/mains.py` : le guichet est le SEUL chemin vers le bureau, et il refuse
de lui-meme. Barriere structurelle, pas consigne — la lecon de cette semaine, appliquee des
la conception cette fois.

Les tests montent un VRAI guichet sur un port libre et parlent aux outils de l'agent.
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE / "mains"))

import mains as M                                            # noqa: E402

from hermes.errors import ToolError                          # noqa: E402
from hermes.tools import ToolContext, build_registry         # noqa: E402


def port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, exec_timeout=20, output_limit=4000,
                       request_timeout=10, search_url="x")


# --------------------------------------------------------------------------- #
# Les garde-fous, testes sans bureau : ce sont des fonctions pures.
# --------------------------------------------------------------------------- #

INTERDITES = [
    ({"programme": "chrome.exe", "titre": "TikTok Studio"}, "TikTok"),
    ({"programme": "terminal64.exe", "titre": "XAUUSD"}, "ordre"),
    ({"programme": "hermes.exe", "titre": "orderflow"}, "trading"),
]


@pytest.mark.parametrize("fenetre,attendu", INTERDITES)
def test_fenetre_interdite(fenetre: dict, attendu: str) -> None:
    with pytest.raises(M.Refus) as refus:
        M._verifie_cible(fenetre, "clic")
    assert attendu in str(refus.value)
    assert fenetre["programme"] in str(refus.value)


def test_fenetre_ordinaire_permise() -> None:
    """Une garde qui bloque tout serait inutilisable : l'agent doit pouvoir agir ailleurs."""
    for programme in ("explorer.exe", "notepad.exe", "blender.exe", ""):
        M._verifie_cible({"programme": programme, "titre": "x"}, "clic")


TOUCHES_REFUSEES = ["win+l", "WIN+L", "ctrl+alt+suppr", "alt+f4"]


@pytest.mark.parametrize("combinaison", TOUCHES_REFUSEES)
def test_touche_interdite(combinaison: str, monkeypatch) -> None:
    """`win+l` verrouille la session : le bureau disparait et les mains se coupent seules."""
    monkeypatch.setattr(M, "fenetre_active", lambda: {"programme": "notepad.exe", "titre": ""})
    with pytest.raises(M.Refus):
        M.touche(combinaison)


def test_touche_inconnue_est_refusee(monkeypatch) -> None:
    monkeypatch.setattr(M, "fenetre_active", lambda: {"programme": "notepad.exe", "titre": ""})
    with pytest.raises(M.Refus) as refus:
        M.touche("ctrl+eject")
    assert "inconnue" in str(refus.value)


# --------------------------------------------------------------------------- #
# Un vrai guichet, avec un bureau simule.
# --------------------------------------------------------------------------- #

@pytest.fixture()
def guichet(monkeypatch):
    """Monte le guichet sur un port libre, avec un faux bureau."""
    fenetre = {"hwnd": 1, "programme": "notepad.exe", "titre": "Sans titre - Bloc-notes"}
    etat = {"fenetre": fenetre, "clics": [], "frappes": []}

    monkeypatch.setattr(M, "bureau_rendu", lambda: etat["fenetre"] is not None)
    monkeypatch.setattr(M, "fenetre_active", lambda: etat["fenetre"] or {})
    monkeypatch.setattr(M, "fenetre_en", lambda x, y: etat["fenetre"] or {})
    monkeypatch.setattr(M, "position", lambda: (100, 200))
    monkeypatch.setattr(M, "_taille_ecran", lambda: [1920, 1080])
    monkeypatch.setattr(M, "bouge", lambda x, y: None)
    monkeypatch.setattr(M, "_envoie", lambda entrees: etat["clics"].append(len(entrees)))
    monkeypatch.setattr(M, "capture", lambda zone=None: b"\x89PNG\r\n\x1a\nfaux")
    etat["lignes"] = [{"texte": "Enregistrer", "x": 640, "y": 480}]
    monkeypatch.setattr(M, "lis_l_ecran", lambda zone=None, langue="fr-FR": {
        "langue": "fr-FR", "png": b"\x89PNG\r\n\x1a\nfaux",
        "lignes": list(etat["lignes"]),
    })
    # Chaque test part d'une memoire vierge de l'ecran, sinon l'ordre des tests decide du
    # resultat de la comparaison avant/apres.
    import hermes.tools.ecran as _E
    _E._DERNIERE_VUE["lignes"] = []
    monkeypatch.setattr(_E, "_DELAI_REDESSIN", 0.01)    # pas d'attente reelle en test
    M.JETON = "jeton-de-test"

    port = port_libre()
    serveur = ThreadingHTTPServer(("127.0.0.1", port), M.Guichet)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()

    import hermes.tools.ecran as E
    monkeypatch.setattr(E, "GUICHET", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(E, "_jeton", lambda: "jeton-de-test")
    try:
        yield etat
    finally:
        serveur.shutdown()
        serveur.server_close()


def outil(ctx, nom, args=None):
    return asyncio.run(build_registry().dispatch(ctx, nom, args or {}))


def test_etat_du_bureau(ctx, guichet) -> None:
    sortie = outil(ctx, "ecran_etat")
    assert "1920x1080" in sortie
    assert "Bloc-notes" in sortie


def test_lecture_de_l_ecran_rend_du_texte_et_des_positions(ctx, guichet) -> None:
    """Le modele est aveugle : une capture PNG ne lui apprend rien. Il lui faut du TEXTE,
    et surtout les coordonnees, sinon il sait quoi cliquer mais pas ou."""
    sortie = outil(ctx, "ecran_voir", {"nom": "vue.png"})
    assert "Enregistrer" in sortie
    assert "(640,480)" in sortie
    assert (ctx.workspace / "vue.png").exists()
    assert "/get vue.png" in sortie


def test_clic_dans_une_fenetre_ordinaire(ctx, guichet) -> None:
    sortie = outil(ctx, "souris", {"action": "clic", "x": 640, "y": 480})
    assert "clic fait" in sortie
    assert guichet["clics"], "aucune entree n'a ete envoyee"


def test_clic_refuse_dans_chrome(ctx, guichet) -> None:
    """Le coeur du sujet : un clic physique contournerait les regles du navigateur."""
    guichet["fenetre"] = {"hwnd": 2, "programme": "chrome.exe", "titre": "TikTok Studio"}
    sortie = outil(ctx, "souris", {"action": "clic", "x": 10, "y": 10})
    assert "refuse" in sortie
    assert "chrome.exe" in sortie


def test_saisie_refusee_dans_metatrader(ctx, guichet) -> None:
    guichet["fenetre"] = {"hwnd": 3, "programme": "terminal64.exe", "titre": "MT5"}
    sortie = outil(ctx, "clavier", {"texte": "0.5"})
    assert "refuse" in sortie


def test_saisie_dans_une_fenetre_ordinaire(ctx, guichet) -> None:
    sortie = outil(ctx, "clavier", {"texte": "bonjour éàü"})
    assert "saisi" in sortie


def test_sans_bureau_le_message_est_explicite(ctx, guichet) -> None:
    """Session RDP deconnectee : ce n'est pas une panne, et l'agent doit savoir le dire."""
    guichet["fenetre"] = None
    sortie = outil(ctx, "ecran_voir")
    assert "personne n'est connecte" in sortie
    assert "Bureau a distance" in sortie or "bureau" in sortie.lower()


def test_le_jeton_protege_le_guichet(ctx, guichet, monkeypatch) -> None:
    """Beaucoup de programmes tournent sur cette machine ; le guichet n'est pas public."""
    import hermes.tools.ecran as E
    monkeypatch.setattr(E, "_jeton", lambda: "mauvais-jeton")
    sortie = outil(ctx, "ecran_etat")
    assert "403" in sortie or "jeton" in sortie.lower()


def test_coordonnees_obligatoires_pour_cliquer(ctx, guichet) -> None:
    sortie = outil(ctx, "souris", {"action": "clic"})
    assert "ecran_voir" in sortie                   # on lui dit par ou commencer


def test_nom_de_capture_sans_chemin(ctx, guichet) -> None:
    for nom in ("../dehors.png", "C:/Windows/x.png"):
        assert "simple nom de fichier" in outil(ctx, "ecran_voir", {"nom": nom})


# --------------------------------------------------------------------------- #
# Sans passerelle lancee : pas de mains, et un message qui l'explique.
# --------------------------------------------------------------------------- #

def test_sans_passerelle_le_message_explique_quoi_faire(ctx, monkeypatch) -> None:
    import hermes.tools.ecran as E
    monkeypatch.setattr(E, "GUICHET", "http://127.0.0.1:%d" % port_libre())
    monkeypatch.setattr(E, "_jeton", lambda: "peu-importe")
    sortie = outil(ctx, "ecran_etat")
    assert "session du patron" in sortie
    assert "mains" in sortie


def test_les_outils_sont_declares() -> None:
    noms = set(build_registry().tools)
    assert {"ecran_etat", "ecran_voir", "souris", "clavier"} <= noms


# --------------------------------------------------------------------------- #
# Deuxieme couche : le TITRE de la fenetre.
# Consigne du patron : ne pas toucher a Chrome, TikTok, YouTube ni MetaTrader.
# Le nom du programme ne suffit pas — TikTok Studio ouvert dans un autre navigateur,
# ou MetaTrader lance sous un autre executable, passeraient la premiere liste.
# --------------------------------------------------------------------------- #

TITRES = [
    ({"programme": "notepad.exe", "titre": "TikTok Studio — Mozilla"}, "tiktok"),
    ({"programme": "inconnu.exe", "titre": "YouTube Studio"}, "youtube"),
    ({"programme": "autre.exe", "titre": "MetaTrader 5 — Vantage"}, "metatrader"),
    ({"programme": "x.exe", "titre": "Redbubble — portfolio"}, "redbubble"),
    ({"programme": "x.exe", "titre": "Telegram Desktop"}, "telegram"),
]


@pytest.mark.parametrize("fenetre,mot", TITRES)
def test_titre_interdit(fenetre: dict, mot: str) -> None:
    with pytest.raises(M.Refus) as refus:
        M._verifie_cible(fenetre, "clic")
    assert mot in str(refus.value)


CONSOLES = ["python.exe", "cmd.exe", "powershell.exe", "windowsterminal.exe"]


@pytest.mark.parametrize("programme", CONSOLES)
def test_console_interdite(programme: str) -> None:
    """Une console porte souvent un programme qui tourne : un Ctrl-C mal place l'arrete."""
    with pytest.raises(M.Refus):
        M._verifie_cible({"programme": programme, "titre": "C:\\"}, "saisie")


AUTRES_NAVIGATEURS = ["msedge.exe", "firefox.exe"]


@pytest.mark.parametrize("programme", AUTRES_NAVIGATEURS)
def test_tout_navigateur_est_protege(programme: str) -> None:
    with pytest.raises(M.Refus):
        M._verifie_cible({"programme": programme, "titre": "page quelconque"}, "clic")


def test_une_fenetre_de_travail_reste_utilisable() -> None:
    """Sans cela l'outil ne servirait a rien : il doit rester des fenetres ou agir."""
    for fenetre in ({"programme": "blender.exe", "titre": "Blender"},
                    {"programme": "notepad.exe", "titre": "brouillon.txt - Bloc-notes"},
                    {"programme": "explorer.exe", "titre": "Telechargements"}):
        M._verifie_cible(fenetre, "clic")


def test_le_clic_verifie_la_fenetre_SOUS_le_curseur(ctx, guichet, monkeypatch) -> None:
    """Un clic n'atteint pas la fenetre active, mais celle qui est sous le pointeur.

    🚨 Ne verifier que le premier plan laisserait cliquer dans MetaTrader par-dessus l'epaule
    d'une fenetre autorisee : le Bloc-notes a le focus, mais les coordonnees visees tombent
    sur la fenetre du trading. C'est le cas que cette garde doit attraper.
    """
    monkeypatch.setattr(M, "fenetre_active",
                        lambda: {"programme": "notepad.exe", "titre": "au premier plan"})
    monkeypatch.setattr(M, "fenetre_en",
                        lambda x, y: {"programme": "terminal64.exe", "titre": "XAUUSD"})
    sortie = outil(ctx, "souris", {"action": "clic", "x": 5, "y": 5})
    assert "refuse" in sortie
    assert "terminal64.exe" in sortie


def test_la_saisie_verifie_la_fenetre_ACTIVE(ctx, guichet, monkeypatch) -> None:
    """Symetrique : une frappe va au focus, pas sous le curseur."""
    monkeypatch.setattr(M, "fenetre_active",
                        lambda: {"programme": "chrome.exe", "titre": "TikTok"})
    monkeypatch.setattr(M, "fenetre_en",
                        lambda x, y: {"programme": "notepad.exe", "titre": "ok"})
    sortie = outil(ctx, "clavier", {"texte": "publier"})
    assert "refuse" in sortie
    assert "chrome.exe" in sortie


# --------------------------------------------------------------------------- #
# Comprendre ce qu'on vient de faire.
# 🚨 Un clic qui rate ressemble exactement a un clic qui reussit : meme retour, meme
# silence. Sans comparaison avant/apres, l'agent enchaine sur une action sans effet et
# la rapporte au patron comme un succes. C'est la difference entre agir et comprendre.
# --------------------------------------------------------------------------- #

def test_l_action_rend_compte_de_ce_qui_a_change(ctx, guichet) -> None:
    outil(ctx, "ecran_voir")                                   # lecture de reference
    guichet["lignes"] = [{"texte": "Document enregistre", "x": 300, "y": 200}]
    sortie = outil(ctx, "souris", {"action": "clic", "x": 640, "y": 480})
    assert "Ce qui a change" in sortie
    assert "Document enregistre" in sortie                     # apparu
    assert "Enregistrer" in sortie                             # disparu


def test_une_action_sans_effet_est_signalee(ctx, guichet) -> None:
    """Le cas le plus important : l'ecran est identique, donc le clic n'a rien fait."""
    outil(ctx, "ecran_voir")
    sortie = outil(ctx, "souris", {"action": "clic", "x": 640, "y": 480})
    assert "RIEN N'A CHANGE" in sortie
    assert "pas comme si elle avait reussi" in sortie


def test_agir_sans_avoir_regarde_est_dit(ctx, guichet) -> None:
    """Sans lecture prealable, il n'y a rien a comparer — et l'agent doit l'apprendre."""
    sortie = outil(ctx, "souris", {"action": "clic", "x": 10, "y": 10})
    assert "je n'avais pas lu l'ecran avant" in sortie


def test_la_saisie_rend_compte_aussi(ctx, guichet) -> None:
    outil(ctx, "ecran_voir")
    guichet["lignes"] = [{"texte": "bonjour", "x": 100, "y": 100}]
    sortie = outil(ctx, "clavier", {"texte": "bonjour"})
    assert "Ce qui a change" in sortie
