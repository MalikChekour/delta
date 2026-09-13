# -*- coding: utf-8 -*-
"""Les OREILLES : transcription locale, sans rien envoyer a un tiers.

🚨 CE QUE CES TESTS PROTEGENT. Deux reglages ont ete choisis apres mesure, et les perdre
degraderait l'agent en silence :

  - la LANGUE est forcee au francais. Laisse libre, le modele a conclu « anglais » sur une
    phrase francaise et rendu du charabia — avec aplomb, ce qui est pire qu'une erreur
    visible ;
  - le modele est `small`, pas `base`. Sur une vraie voix francaise : base 11 mots sur 13
    (« les CINQ produits » devenait « les SAINS produits »), small 13 sur 13.
"""
from __future__ import annotations

import asyncio
import wave
from pathlib import Path

import pytest

from hermes.tools import ToolContext, build_registry
from hermes.tools import oreille


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, exec_timeout=60, output_limit=4000,
                       request_timeout=10, search_url="x")


def outil(ctx, args):
    return asyncio.run(build_registry().dispatch(ctx, "ecoute", args))


def silence(chemin: Path, secondes: float = 1.0) -> None:
    with wave.open(str(chemin), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * int(16000 * secondes))


def test_l_outil_est_declare() -> None:
    assert "ecoute" in build_registry().tools


def test_le_francais_est_le_defaut() -> None:
    """Le patron parle francais ; laisser deviner a deja produit du charabia anglais."""
    assert oreille.LANGUE_PAR_DEFAUT == "fr"


def test_le_modele_est_small() -> None:
    """`base` transformait « cinq produits » en « sains produits »."""
    assert oreille.TAILLE == "small"


def test_fichier_absent(ctx) -> None:
    assert "n'existe pas" in outil(ctx, {"fichier": "rien.ogg"})


def test_format_refuse(ctx) -> None:
    (ctx.workspace / "note.txt").write_text("pas du son", encoding="utf-8")
    assert "format" in outil(ctx, {"fichier": "note.txt"})


def test_pas_de_sortie_du_workspace(ctx) -> None:
    sortie = outil(ctx, {"fichier": "../../../Windows/win.ini"})
    assert "hors du workspace" in sortie or "n'existe pas" in sortie or "format" in sortie


def test_l_ogg_de_telegram_est_accepte() -> None:
    """Telegram envoie ses messages vocaux en ogg/opus : le refuser rendrait l'outil inutile."""
    for extension in (".ogg", ".oga", ".opus", ".m4a", ".mp3", ".wav"):
        assert extension in oreille.SONS


def test_un_silence_ne_fabrique_pas_de_paroles(ctx) -> None:
    """Whisper invente volontiers sur du silence ; il faut que l'agent l'apprenne, pas
    qu'on lui serve une phrase imaginaire."""
    silence(ctx.workspace / "vide.wav", 1.2)
    sortie = outil(ctx, {"fichier": "vide.wav"})
    assert "aucune parole" in sortie or len(sortie) < 200


def test_langue_auto_est_possible(ctx, monkeypatch) -> None:
    """`auto` doit redonner la detection, pour un son qui n'est pas en francais."""
    vues = {}

    def faux(chemin, langue):
        vues["langue"] = langue
        return "texte", "en", 2.0

    monkeypatch.setattr(oreille, "_transcris", faux)
    silence(ctx.workspace / "a.wav", 0.2)
    outil(ctx, {"fichier": "a.wav", "langue": "auto"})
    assert vues["langue"] is None
    outil(ctx, {"fichier": "a.wav"})
    assert vues["langue"] == "fr"
    outil(ctx, {"fichier": "a.wav", "langue": "es"})
    assert vues["langue"] == "es"
