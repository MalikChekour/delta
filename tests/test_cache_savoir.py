"""Tests du cache de recherche et de la memoire de connaissances.

Chaque test vise une panne PRECISE, souvent deja constatee — pas la fonction en general.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermes.cache import CacheRecherche, _cle, patiente
from hermes.tools import ToolContext, build_registry


@pytest.fixture()
def atelier(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, exec_timeout=30, output_limit=8000,
                       request_timeout=20, search_url="https://html.duckduckgo.com/html/")


# -- cache de recherche -----------------------------------------------------

def test_la_cle_ignore_casse_et_espaces():
    """« Reglement UE 432 » et « reglement  ue 432 » sont la MEME question : sans
    normalisation, le cache manquerait la moitie des repetitions."""
    assert _cle("Reglement UE 432", 5) == _cle("  reglement   ue 432 ", 5)
    assert _cle("a", 5) != _cle("a", 10)          # la limite fait partie de la question


def test_relecture_et_peremption(tmp_path):
    c = CacheRecherche(tmp_path / "c.db")
    res = [{"title": "T", "url": "https://x.test", "snippet": "s"}]
    c.ecrit("q", 5, res, "bing")
    lu = c.lit("q", 5)
    assert lu is not None and lu[0][0]["url"] == "https://x.test" and lu[1] == "bing"
    # une entree perimee ne doit pas etre servie
    with c._co() as co:
        co.execute("UPDATE recherches SET pose = ?", (time.time() - 10 * 24 * 3600,))
    assert c.lit("q", 5) is None


def test_un_echec_n_est_jamais_cache(tmp_path):
    """🚨 Cacher une liste vide figerait une panne passagere pour 24 h : l'agent croirait
    durablement qu'il n'y a aucun resultat."""
    c = CacheRecherche(tmp_path / "c.db")
    c.ecrit("q", 5, [], "bing")
    assert c.lit("q", 5) is None
    assert c.compte() == 0


def test_purge(tmp_path):
    c = CacheRecherche(tmp_path / "c.db")
    c.ecrit("q", 5, [{"title": "T", "url": "https://x.test"}], "bing")
    assert c.compte() == 1
    assert c.purge(avant=time.time() + 1) == 1
    assert c.compte() == 0


async def test_l_espacement_freine_la_rafale():
    """🚨 Le modele emet souvent plusieurs recherches dans le meme tour, en parallele.
    C'est la rafale qui fait limiter l'IP — le frein doit donc s'appliquer meme quand les
    appels partent simultanement."""
    import asyncio

    await patiente("moteur_test")                 # amorce
    t = time.monotonic()
    await asyncio.gather(patiente("moteur_test"), patiente("moteur_test"))
    assert time.monotonic() - t > 3.0             # deux attentes serialisees, pas une


async def test_moteurs_differents_ne_se_freinent_pas():
    import asyncio

    t = time.monotonic()
    await asyncio.gather(patiente("m_a"), patiente("m_b"))
    assert time.monotonic() - t < 2.0


# -- memoire de connaissances ----------------------------------------------

async def test_les_trois_outils_sont_enregistres():
    reg = build_registry()
    for nom in ("retiens", "rappelle", "oublie"):
        assert nom in reg


async def test_retenir_puis_rappeler(atelier):
    reg = build_registry()
    await reg.dispatch(atelier, "retiens",
                       {"titre": "Voix", "contenu": "Uniquement la voix Vivienne."})
    out = await reg.dispatch(atelier, "rappelle", {"requete": "Vivienne"})
    assert "Voix" in out and "Vivienne" in out


async def test_recherche_insensible_aux_accents(atelier):
    """🚨 Corpus francais : sans `remove_diacritics`, « allegation » ne trouverait jamais
    « allégation », et la memoire paraitrait vide alors qu'elle est pleine."""
    reg = build_registry()
    await reg.dispatch(atelier, "retiens",
                       {"titre": "Regle", "contenu": "Citer les allégations mot pour mot."})
    assert "Regle" in await reg.dispatch(atelier, "rappelle", {"requete": "allegations"})


async def test_singulier_trouve_le_pluriel(atelier):
    """🚨 FTS5 compare des mots EXACTS, sans radical : « allegation » ne trouvait pas
    « allégations ». Mesure du 10/09 — le pluriel suffisait a rendre la memoire muette."""
    reg = build_registry()
    await reg.dispatch(atelier, "retiens",
                       {"titre": "Regle", "contenu": "Citer les allégations mot pour mot."})
    assert "Regle" in await reg.dispatch(atelier, "rappelle", {"requete": "allegation"})


async def test_meme_titre_remplace_au_lieu_d_empiler(atelier):
    """Sinon la memoire accumule des versions contradictoires du meme fait."""
    reg = build_registry()
    await reg.dispatch(atelier, "retiens", {"titre": "T", "contenu": "version une"})
    out = await reg.dispatch(atelier, "retiens", {"titre": "T", "contenu": "version deux"})
    assert "mise a jour" in out and "1 note" in out
    assert "version deux" in await reg.dispatch(atelier, "rappelle", {"requete": "version"})


async def test_requete_avec_operateur_ne_casse_pas(atelier):
    """🚨 Une apostrophe ou un operateur FTS5 mal place ( « allegation OR » ) leve une
    erreur de syntaxe : l'agent croirait sa memoire vide alors qu'elle est pleine."""
    reg = build_registry()
    await reg.dispatch(atelier, "retiens", {"titre": "T", "contenu": "l'allégation du jour"})
    for mechante in ("allegation OR", 'guillemet "', "l'apostrophe", "NEAR(a b)", "*"):
        out = await reg.dispatch(atelier, "rappelle", {"requete": mechante})
        assert "ERREUR" not in out or "trop courte" in out


async def test_oublier(atelier):
    reg = build_registry()
    await reg.dispatch(atelier, "retiens", {"titre": "T", "contenu": "c"})
    assert "supprimee" in await reg.dispatch(atelier, "oublie", {"titre": "T"})
    assert "aucune note" in await reg.dispatch(atelier, "oublie", {"titre": "T"})
