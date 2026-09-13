# -*- coding: utf-8 -*-
"""Le coffre : la memoire de l'agent en fichiers Markdown, façon Obsidian.

🚨 CE QUE CES TESTS PROTEGENT AVANT TOUT : le sens de l'autorite. Les FICHIERS font foi, la
base SQLite n'est qu'un index. Si la base reprenait la main, le patron corrigerait une note
dans Obsidian, l'agent continuerait a citer l'ancienne version, et personne ne verrait rien —
la pire des pannes, celle qui ne se signale pas.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from hermes.tools import ToolContext, build_registry
from hermes.tools import coffre


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, exec_timeout=20, output_limit=8000,
                       request_timeout=10, search_url="x")


def outil(ctx, nom, args=None):
    return asyncio.run(build_registry().dispatch(ctx, nom, args or {}))


# --- les fichiers ----------------------------------------------------------- #

def test_retiens_ecrit_un_fichier_markdown(ctx) -> None:
    outil(ctx, "retiens", {"titre": "Voix Vivienne", "contenu": "edge-tts, francais.",
                           "etiquettes": "audio, voix"})
    fichier = ctx.workspace / coffre.DOSSIER / "Voix Vivienne.md"
    assert fichier.exists(), "la note doit exister comme fichier, ouvrable dans Obsidian"
    texte = fichier.read_text(encoding="utf-8")
    assert texte.startswith("---")                      # en-tete YAML
    assert "titre:" in texte and "etiquettes:" in texte
    assert "edge-tts" in texte


def test_le_nom_de_fichier_reste_lisible() -> None:
    """Obsidian affiche le nom du fichier : « voix-vivienne-2f3a » serait illisible."""
    assert coffre.nom_de_fichier("Voix Vivienne") == "Voix Vivienne.md"
    assert coffre.nom_de_fichier("Débit / Crédit") == "Débit - Crédit.md"
    assert coffre.nom_de_fichier("a" * 300).endswith(".md")


def test_les_caracteres_interdits_par_windows_sont_remplaces() -> None:
    for brut in ('a:b', 'a/b', 'a\\b', 'a?b', 'a*b', 'a"b', 'a<b>', 'a|b'):
        nom = coffre.nom_de_fichier(brut)
        assert not set(nom) & set(':/\\?*"<>|'), nom


# --- les liens -------------------------------------------------------------- #

def test_les_liens_sont_lus() -> None:
    corps = "Voir [[Voix Vivienne]] et [[Montage video|le montage]], et encore [[Voix Vivienne]]."
    assert coffre.liens_sortants(corps) == ["Voix Vivienne", "Montage video"]


def test_un_lien_sans_accent_retrouve_la_note_accentuee() -> None:
    """Sinon le lien reste mort et personne ne s'en apercoit."""
    assert coffre.meme_titre("Video promo", "Vidéo promo")
    assert coffre.meme_titre("VOIX vivienne", "voix Vivienne")
    assert not coffre.meme_titre("Voix", "Voix Vivienne")


def test_retroliens(ctx) -> None:
    """Savoir QUI cite une note vaut plus que la retrouver par mot-cle."""
    outil(ctx, "retiens", {"titre": "Socle", "contenu": "la base."})
    outil(ctx, "retiens", {"titre": "Un", "contenu": "repose sur [[Socle]]."})
    outil(ctx, "retiens", {"titre": "Deux", "contenu": "aussi sur [[Socle]]."})
    assert sorted(coffre.retroliens(ctx.workspace, "Socle")) == ["Deux", "Un"]


def test_un_lien_vers_une_note_absente_est_signale(ctx) -> None:
    """Ce n'est pas une erreur : c'est une note a ecrire. Encore faut-il le dire."""
    sortie = outil(ctx, "retiens", {"titre": "Projet", "contenu": "depend de [[Inexistante]]."})
    assert "n'existent pas encore" in sortie
    assert "Inexistante" in sortie


# --- les fichiers font foi -------------------------------------------------- #

def test_une_correction_dans_obsidian_remonte_a_l_agent(ctx) -> None:
    """🚨 LE TEST CENTRAL. Le patron corrige un fichier a la main ; l'agent doit lire la
    version corrigee, pas celle qu'il avait en base."""
    outil(ctx, "retiens", {"titre": "Tarif", "contenu": "Le tarif est de 10 euros."})
    assert "10 euros" in outil(ctx, "rappelle", {"requete": "tarif"})

    fichier = ctx.workspace / coffre.DOSSIER / "Tarif.md"
    time.sleep(0.01)
    fichier.write_text("---\ntitre: \"Tarif\"\n---\nLe tarif est de 25 euros.\n",
                       encoding="utf-8")

    sortie = outil(ctx, "rappelle", {"requete": "tarif"})
    assert "25 euros" in sortie, "la correction faite dans Obsidian a ete ignoree"
    assert "10 euros" not in sortie


def test_une_note_supprimee_dans_obsidian_est_oubliee(ctx) -> None:
    outil(ctx, "retiens", {"titre": "Ephemere", "contenu": "a supprimer bientot."})
    outil(ctx, "rappelle", {"requete": "ephemere"})          # la note est connue du journal
    (ctx.workspace / coffre.DOSSIER / "Ephemere.md").unlink()
    sortie = outil(ctx, "rappelle", {"requete": "ephemere"})
    assert "aucune note" in sortie


def test_un_coffre_vide_n_efface_pas_la_memoire(ctx) -> None:
    """🚨 « Connu puis disparu », pas « absent ». Sinon un coffre pas encore synchronise —
    ou un dossier deplace — ferait tout oublier d'un coup."""
    co = None
    outil(ctx, "retiens", {"titre": "Important", "contenu": "a ne pas perdre."})
    dossier = ctx.workspace / coffre.DOSSIER
    for f in dossier.glob("*.md"):
        f.unlink()
    # Premier passage : les fichiers manquent mais n'ont jamais ete vus ; la note est
    # reexportee plutot qu'oubliee.
    outil(ctx, "rappelle", {"requete": "important"})
    assert (dossier / "Important.md").exists()
    assert "a ne pas perdre" in outil(ctx, "rappelle", {"requete": "important"})


def test_oublie_supprime_aussi_le_fichier(ctx) -> None:
    outil(ctx, "retiens", {"titre": "Jetable", "contenu": "bon a jeter."})
    outil(ctx, "oublie", {"titre": "Jetable"})
    assert not (ctx.workspace / coffre.DOSSIER / "Jetable.md").exists()


# --- l'outil `ouvre` -------------------------------------------------------- #

def test_ouvre_rend_la_note_entiere_et_ses_liens(ctx) -> None:
    outil(ctx, "retiens", {"titre": "Socle", "contenu": "la base."})
    outil(ctx, "retiens", {"titre": "Batiment", "contenu": "pose sur [[Socle]].",
                           "etiquettes": "chantier"})
    sortie = outil(ctx, "ouvre", {"titre": "Batiment"})
    assert "pose sur" in sortie
    assert "chantier" in sortie
    assert "Renvoie vers" in sortie and "Socle" in sortie
    assert "Cite par" in outil(ctx, "ouvre", {"titre": "Socle"})


def test_ouvre_tolere_la_casse_et_les_accents(ctx) -> None:
    outil(ctx, "retiens", {"titre": "Vidéo promo", "contenu": "neuf secondes."})
    assert "neuf secondes" in outil(ctx, "ouvre", {"titre": "video PROMO"})


def test_ouvre_sans_titre_rend_la_carte(ctx) -> None:
    outil(ctx, "retiens", {"titre": "A", "contenu": "vers [[B]]."})
    outil(ctx, "retiens", {"titre": "B", "contenu": "fin."})
    outil(ctx, "retiens", {"titre": "Seule", "contenu": "personne ne me cite."})
    carte = outil(ctx, "ouvre", {})
    assert "3 note(s)" in carte
    assert "Seule" in carte                                   # signalee comme isolee


def test_ouvre_une_note_absente_oriente(ctx) -> None:
    sortie = outil(ctx, "ouvre", {"titre": "Fantome"})
    assert "aucune note" in sortie
    assert "rappelle" in sortie                               # on dit quoi faire a la place


def test_la_carte_signale_les_liens_morts(ctx) -> None:
    outil(ctx, "retiens", {"titre": "Depart", "contenu": "vers [[Jamais ecrite]]."})
    carte = outil(ctx, "ouvre", {})
    assert "Jamais ecrite" in carte


# --- une memoire ne doit jamais porter un secret ---------------------------- #

def test_un_secret_ne_peut_pas_entrer_dans_la_memoire(ctx, monkeypatch) -> None:
    """🚨 FUITE REELLE, TROUVEE LE 13/09 EN OUVRANT LE COFFRE.

    Le caviardage protegeait ce que les outils RENVOIENT. Il ne voyait pas ce que l'agent
    ECRIT dans sa propre memoire : il avait lu le .env avec `shell`, puis range le resultat
    dans une note — jeton GitHub et cle Tavily en clair, sur le disque, destines a etre relus
    et recites indefiniment. Une memoire est le pire endroit ou laisser un secret : c'est
    fait pour ressortir.
    """
    import os
    from hermes.tools import oublie_les_secrets

    faux = "ghp_ZzYyXxWwVvUuTtSsRrQqPpOoNnMmLlKkJj"
    os.environ["CONTROLE_COFFRE_TOKEN"] = faux
    oublie_les_secrets()
    try:
        outil(ctx, "retiens", {"titre": "Configuration", "contenu": f"le jeton est {faux}"})
        fichier = ctx.workspace / coffre.DOSSIER / "Configuration.md"
        assert faux not in fichier.read_text(encoding="utf-8"), "le secret est sur le disque"
        assert "CONTROLE_COFFRE_TOKEN" in fichier.read_text(encoding="utf-8")
        assert faux not in outil(ctx, "rappelle", {"requete": "jeton"})
        assert faux not in outil(ctx, "ouvre", {"titre": "Configuration"})
    finally:
        del os.environ["CONTROLE_COFFRE_TOKEN"]
        oublie_les_secrets()


def test_un_secret_dans_le_titre_est_caviarde_aussi(ctx) -> None:
    import os
    from hermes.tools import oublie_les_secrets

    faux = "tvly-dev-QqWwEeRrTtYyUuIiOoPpAaSsDdFf"
    os.environ["CONTROLE_TITRE_KEY"] = faux
    oublie_les_secrets()
    try:
        sortie = outil(ctx, "retiens", {"titre": f"cle {faux}", "contenu": "peu importe"})
        assert faux not in sortie
        assert not any(faux in f.name for f in (ctx.workspace / coffre.DOSSIER).glob("*.md"))
    finally:
        del os.environ["CONTROLE_TITRE_KEY"]
        oublie_les_secrets()
