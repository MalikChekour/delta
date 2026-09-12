# -*- coding: utf-8 -*-
"""Le noyau Python persistant.

🚨 CE QU'IL CORRIGE. L'outil `python` lancait un processus neuf a chaque appel : aucune
variable ne survivait. Le modele ecrivait alors des tours qui s'appuyaient sur des donnees
mortes, ne comprenait pas l'erreur, et rechargeait tout — d'ou des enchainements a six ou
sept appels la ou deux suffisent.

Les tests portent sur le comportement vu depuis l'outil, pas sur jupyter_client : c'est le
passage par `dispatch` qui compte.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from hermes.tools import ToolContext, build_registry
from hermes.tools import noyau


@pytest.fixture()
def reg():
    return build_registry()


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, exec_timeout=25, output_limit=6000,
                       request_timeout=10, search_url="x", chat_id=4242)


@pytest.fixture(autouse=True)
def ferme_les_noyaux():
    """Aucun noyau ne doit survivre a un test : ce sont des processus."""
    yield
    asyncio.run(noyau.arrete_tout())


def py(reg, ctx, code: str, **kw) -> str:
    return asyncio.run(reg.dispatch(ctx, "python", {"code": code, **kw}))


def test_le_noyau_est_disponible() -> None:
    """Si ipykernel disparait, l'outil doit retomber sur l'ancienne voie, pas casser."""
    assert noyau.disponible() is True


def test_l_etat_survit_entre_deux_appels(reg, ctx) -> None:
    py(reg, ctx, "import math\nbase = 41")
    assert "42" in py(reg, ctx, "print(base + 1)")
    assert "4.0" in py(reg, ctx, "print(math.sqrt(16))")


def test_les_accents_passent(reg, ctx) -> None:
    """Panne historique : stdout en cp1252 tuait le script des le premier accent."""
    assert "éàü" in py(reg, ctx, "print('éàü garçon')")


def test_une_erreur_ne_tue_pas_la_session(reg, ctx) -> None:
    py(reg, ctx, "garde = 'intact'")
    sortie = py(reg, ctx, "1/0")
    assert "ZeroDivisionError" in sortie
    assert "intact" in py(reg, ctx, "print(garde)")


def test_pas_de_codes_couleur(reg, ctx) -> None:
    """ipykernel colore ses traces ; en Telegram les codes ANSI sont du bruit."""
    assert "\x1b[" not in py(reg, ctx, "1/0")


def test_une_boucle_infinie_est_interrompue_sans_perdre_l_etat(reg, ctx) -> None:
    """Le point le plus important : interrompre, jamais tuer.

    Tuer le noyau ferait perdre tout ce que la conversation a construit, c'est-a-dire
    exactement ce que ce module existe pour eviter.
    """
    py(reg, ctx, "tresor = 7")
    depart = time.monotonic()
    sortie = py(reg, ctx, "import time\nwhile True: time.sleep(0.2)", timeout=5)
    ecoule = time.monotonic() - depart
    assert "interrompu" in sortie
    assert ecoule < 25, "l'interruption doit venir du delai, pas d'un blocage"
    assert "7" in py(reg, ctx, "print(tresor)")


def test_nouveau_repart_de_zero(reg, ctx) -> None:
    py(reg, ctx, "ephemere = 1")
    sortie = py(reg, ctx, "print('ephemere' in dir())", nouveau=True)
    assert "False" in sortie
    assert "zero" in sortie


def test_deux_conversations_ne_se_melangent_pas(reg, tmp_path: Path) -> None:
    """Une variable d'une discussion sur le trading n'a rien a faire dans une autre."""
    un = ToolContext(workspace=tmp_path, exec_timeout=25, output_limit=6000,
                     request_timeout=10, search_url="x", chat_id=111)
    deux = ToolContext(workspace=tmp_path, exec_timeout=25, output_limit=6000,
                       request_timeout=10, search_url="x", chat_id=222)
    py(reg, un, "secret_du_fil = 'un'")
    assert "False" in py(reg, deux, "print('secret_du_fil' in dir())")
    assert "un" in py(reg, un, "print(secret_du_fil)")


def test_la_garde_des_processus_tient_toujours(reg, ctx) -> None:
    """Le noyau ne doit pas rouvrir le trou ferme hier."""
    sortie = py(reg, ctx, 'import os; os.system("taskkill /F /IM chrome.exe")')
    assert "refusee" in sortie


def test_le_code_vide_est_refuse(reg, ctx) -> None:
    assert "vide" in py(reg, ctx, "   ")


def test_sans_sortie_on_le_dit(reg, ctx) -> None:
    """Un resultat muet ferait croire a une panne : on explique."""
    assert "print" in py(reg, ctx, "x = 1")


def test_le_workspace_est_le_dossier_courant(reg, ctx) -> None:
    py(reg, ctx, "open('temoin.txt', 'w').write('ok')")
    assert (ctx.workspace / "temoin.txt").exists()


def test_les_noyaux_en_trop_sont_fermes(reg, tmp_path: Path) -> None:
    """Un noyau pese ~80 Mo : on ne peut pas en ouvrir un par conversation sans limite."""
    for chat in range(noyau.MAX_NOYAUX + 2):
        ctx = ToolContext(workspace=tmp_path, exec_timeout=25, output_limit=2000,
                          request_timeout=10, search_url="x", chat_id=900 + chat)
        py(reg, ctx, "x = 1")
    assert noyau.combien() <= noyau.MAX_NOYAUX


def test_repli_si_le_noyau_ne_demarre_pas(reg, ctx, monkeypatch) -> None:
    """Une amelioration qui casse la fonction de base serait une regression.

    On simule une machine sans ipykernel : l'outil doit continuer a executer du code,
    simplement sans garder l'etat.
    """
    monkeypatch.setattr(noyau, "disponible", lambda: False)
    assert "bonjour" in py(reg, ctx, "print('bonjour')")


def test_l_agent_voit_ses_propres_corrections(reg, ctx) -> None:
    """🚨 REGRESSION MESUREE : la tache « corrige le bogue » est tombee de 8/8 a 0/3.

    Une session vivante garde les modules en cache. L'agent lisait `from panier import
    remise`, corrigeait `panier.py` sur le DISQUE, relancait — et obtenait toujours
    l'ancienne version. Il en concluait que sa correction n'avait pas pris, et tournait en
    rond. Un processus neuf n'avait pas ce defaut : il n'avait pas de cache.
    """
    module = ctx.workspace / "cible.py"
    module.write_text("def valeur():\n    return 'AVANT'\n", encoding="utf-8")
    assert "AVANT" in py(reg, ctx, "from cible import valeur; print(valeur())")
    module.write_text("def valeur():\n    return 'APRES'\n", encoding="utf-8")
    assert "APRES" in py(reg, ctx, "from cible import valeur; print(valeur())"), (
        "le noyau sert une version perimee : autoreload n'est pas actif"
    )


def test_autoreload_ne_detruit_pas_l_etat(reg, ctx) -> None:
    """Relire les modules ne doit pas effacer les variables de la conversation."""
    py(reg, ctx, "compteur = 5")
    (ctx.workspace / "annexe.py").write_text("X = 1\n", encoding="utf-8")
    py(reg, ctx, "import annexe")
    assert "5" in py(reg, ctx, "print(compteur)")
