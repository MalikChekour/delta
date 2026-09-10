"""Tests des outils de code.

Chaque test vise une panne PRECISE deja constatee, pas la fonction en general :
un test qui se contente d'appeler l'outil passerait aussi bien avec un outil casse.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes.tools import ToolContext, build_registry
from hermes.tools.code import _DOSSIERS, _outil

SOURCE = (
    "import logging\n"
    "log = logging.getLogger(__name__)\n\n"
    "def charge(chemin):\n"
    "    # print(chemin) <- un commentaire\n"
    '    """print(chemin) dans une chaine."""\n'
    "    print(chemin)\n"
    "    return open(chemin).read()\n"
)


@pytest.fixture()
def atelier(tmp_path: Path) -> ToolContext:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "demo.py").write_text(SOURCE, encoding="utf-8")
    return ToolContext(
        workspace=tmp_path, exec_timeout=60, output_limit=8000,
        request_timeout=20, search_url="https://html.duckduckgo.com/html/",
    )


@pytest.fixture()
def registre():
    return build_registry()


def test_les_quatre_outils_sont_enregistres(registre):
    for nom in ("code_search", "ast_grep", "github_code", "apply_patch"):
        assert nom in registre


@pytest.mark.asyncio
async def test_code_search_trouve_et_situe(atelier, registre):
    out = await registre.dispatch(atelier, "code_search",
                                  {"pattern": "getLogger", "glob": "*.py"})
    assert "demo.py" in out
    assert "ERREUR" not in out


@pytest.mark.asyncio
async def test_code_search_marche_sans_ripgrep(atelier, registre, monkeypatch):
    """🚨 ripgrep peut manquer : l'agent doit garder la capacite de chercher, pas recevoir
    une erreur. Le repli Python est teste en le forcant."""
    monkeypatch.setattr("hermes.tools.code._outil", lambda nom: None)
    out = await registre.dispatch(atelier, "code_search", {"pattern": "getLogger"})
    assert "demo.py" in out
    assert "repli Python" in out


@pytest.mark.asyncio
async def test_apply_patch_refuse_ce_qui_n_est_pas_un_diff(atelier, registre):
    out = await registre.dispatch(atelier, "apply_patch", {"diff": "ajoute import os"})
    assert "ERREUR" in out and "diff unifie" in out


@pytest.mark.asyncio
async def test_apply_patch_verifie_avant_d_ecrire(atelier, registre):
    """🚨 Le point qui compte : un patch qui ne s'applique pas ne doit RIEN ecrire.
    Un diff a moitie pose est l'etat le plus difficile a rattraper."""
    faux = ("--- a/src/demo.py\n+++ b/src/demo.py\n"
            "@@ -1,2 +1,3 @@\n cette ligne n'existe pas\n+import os\n autre ligne fantome\n")
    avant = (atelier.workspace / "src" / "demo.py").read_text(encoding="utf-8")
    out = await registre.dispatch(atelier, "apply_patch", {"diff": faux})
    assert "NE s'applique PAS" in out or "ERREUR" in out
    assert (atelier.workspace / "src" / "demo.py").read_text(encoding="utf-8") == avant


@pytest.mark.asyncio
async def test_apply_patch_pose_un_diff_valide(atelier, registre):
    diff = ("--- a/src/demo.py\n+++ b/src/demo.py\n"
            "@@ -1,2 +1,3 @@\n import logging\n+import os\n log = logging.getLogger(__name__)\n")
    out = await registre.dispatch(atelier, "apply_patch", {"diff": diff})
    assert "applique" in out.lower()
    assert "import os" in (atelier.workspace / "src" / "demo.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_github_code_dit_ce_qu_il_manque(atelier, registre, monkeypatch):
    """Sans jeton, l'API rend 403 : l'outil doit l'expliquer, pas relayer l'erreur brute."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    out = await registre.dispatch(atelier, "github_code", {"query": "test"})
    assert "GITHUB_TOKEN" in out


def test_outil_cherche_hors_du_path():
    """🚨 Le service tourne sous SYSTEM, dont le PATH ne contient ni WinGet\\Links ni
    APPDATA\\npm. `_outil` doit trouver malgre tout — sinon les outils se degradent en
    silence en production alors qu'ils marchent en session interactive."""
    assert any(d.name in ("Links", "npm") for d in _DOSSIERS)
    trouve = _outil("git")
    assert trouve and Path(trouve).is_file()


def test_outil_prefere_le_cmd_au_script_posix(tmp_path, monkeypatch):
    """npm depose `ast-grep` (script POSIX) ET `ast-grep.cmd`. Sous Windows, seul le .cmd
    s'execute : prendre le premier trouve dans l'ordre naif donnerait un binaire mort."""
    (tmp_path / "truc").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "truc.cmd").write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr("hermes.tools.code._DOSSIERS", [tmp_path])
    monkeypatch.setattr("shutil.which", lambda _n: None)
    if os.name == "nt":
        assert _outil("truc").endswith(".cmd")
