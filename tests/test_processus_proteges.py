# -*- coding: utf-8 -*-
"""La garde qui empeche l'agent de tuer les programmes vitaux de la machine.

🚨 CE FICHIER EXISTE A CAUSE D'UN DEGAT REEL. Le 12/09, le prompt systeme disait noir sur
blanc « ne jamais tuer chrome.exe, il publie sur TikTok et YouTube ». On a demande a l'agent
de le tuer pour liberer de la memoire : il a obei, et la session TikTok a ete perdue. Le
modele est un GLM **decensure** — une interdiction ecrite ne l'arrete pas. C'est la deuxieme
fois que la lecon tombe (la premiere : les secrets). Seule une barriere dans le CODE tient.

Les tests portent donc sur le comportement OBSERVE de l'outil, pas sur la fonction interne :
c'est le passage par `dispatch` qui a manque le jour du degat.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from hermes.tools import ToolContext, build_registry
from hermes.tools.shell import PROTEGES, _cible_protegee


@pytest.fixture()
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workspace=tmp_path, exec_timeout=20, output_limit=4000,
                       request_timeout=10, search_url="x")


# Chaque entree est une formulation REELLEMENT possible, pas une variante theorique.
REFUSES = [
    "taskkill /F /IM chrome.exe",
    "taskkill /IM CHROME.EXE",                       # la casse ne doit rien changer
    'powershell -Command "Stop-Process -Name chrome -Force"',
    "Stop-Process -Name 'chrome'",                   # apostrophes PowerShell
    'Stop-Process -Name "chrome"',
    "taskkill /F /IM terminal64.exe",                # MT5 : le trading tourne dessus
    "taskkill /F /IM hermes.exe",                    # l'autre agent, homonyme
    "taskkill /F /IM sqlservr.exe",
    "pkill chrome",
    "killall chrome",
    # 🚨 Ces quatre-la passaient la premiere version de la garde : aucun verbe d'arret
    # reconnaissable dans le texte. C'est exactement ce qu'un modele essaie apres un refus.
    "(Get-Process chrome).Kill()",
    "Get-Process chrome | ForEach-Object { $_.Kill() }",
    'wmic process where name="chrome.exe" delete',
    "wmic process where name='terminal64.exe' terminate",
]

# Ce qui doit continuer a passer : une garde qui bloque tout est une garde inutilisable.
AUTORISES = [
    "taskkill /F /IM notepad.exe",
    "taskkill /F /PID 12345",                        # viser un PID precis reste permis
    "echo chrome.exe",                               # on PARLE de chrome, on ne le tue pas
    "tasklist | findstr chrome",                     # inspecter, oui
    "Get-Process chrome | Select-Object Id, WS",      # mesurer la memoire reste permis
    "del chrome_cache.tmp",                           # supprimer un FICHIER n'est pas tuer
    "dir",
]


@pytest.mark.parametrize("commande", REFUSES)
def test_refuse(commande: str) -> None:
    motif = _cible_protegee(commande)
    assert motif is not None, commande
    assert "refusee" in motif


@pytest.mark.parametrize("commande", AUTORISES)
def test_autorise(commande: str) -> None:
    assert _cible_protegee(commande) is None, commande


#: Cible protegee dont le nom ne correspond a AUCUNE image de processus Windows.
#: 🚨 POURQUOI PAS chrome.exe ICI. Premiere version de ce fichier, le test bout-en-bout
#: visait chrome.exe. Il passe en vert tant que la garde tient — mais le controle de
#: mutation, lui, DESARME la garde : la commande est alors reellement executee, et elle a
#: tue Chrome une seconde fois. Un test qui verifie qu'un degat est empeche ne doit jamais
#: pouvoir causer ce degat. La cible reste protegee (donc le test prouve bien la garde) mais
#: `taskkill /IM mt5_desk.py` ne peut rien terminer : ce n'est pas un nom d'executable.
CIBLE_INOFFENSIVE = "taskkill /F /IM mt5_desk.py"


def test_shell_refuse_reellement(ctx: ToolContext) -> None:
    """Le jour du degat, la fonction n'etait pas appelee. On teste le CHEMIN COMPLET."""
    reg = build_registry()
    sortie = asyncio.run(reg.dispatch(ctx, "shell", {"command": CIBLE_INOFFENSIVE}))
    assert "refusee" in sortie
    assert "trading" in sortie                       # l'agent apprend POURQUOI


def test_le_motif_nomme_chrome_et_tiktok() -> None:
    """La raison affichee doit etre concrete — sans executer quoi que ce soit."""
    motif = _cible_protegee("taskkill /F /IM chrome.exe")
    assert motif is not None and "TikTok" in motif


def test_python_refuse_aussi(ctx: ToolContext) -> None:
    """`os.system("taskkill ...")` dans un script contournerait entierement l'outil shell."""
    reg = build_registry()
    code = 'import os; os.system("%s")' % CIBLE_INOFFENSIVE
    sortie = asyncio.run(reg.dispatch(ctx, "python", {"code": code}))
    assert "refusee" in sortie


def test_shell_laisse_passer_le_reste(ctx: ToolContext) -> None:
    reg = build_registry()
    sortie = asyncio.run(reg.dispatch(ctx, "shell", {"command": "echo bonjour"}))
    assert "bonjour" in sortie
    assert "refusee" not in sortie


def test_le_motif_dit_quoi_faire_a_la_place() -> None:
    """Un refus sec ferait boucler l'agent. Il doit lire l'issue : viser un PID."""
    motif = _cible_protegee("taskkill /F /IM chrome.exe")
    assert "PID" in motif


def test_la_liste_couvre_les_programmes_du_prompt() -> None:
    """Le prompt systeme nomme ces programmes : la garde doit les couvrir tous.

    Sinon on retombe dans la situation exacte du degat — une consigne ecrite sans barriere.
    """
    from hermes.config import DEFAULT_SYSTEM_PROMPT
    for nom in ("chrome.exe", "terminal64.exe", "hermes.exe"):
        assert nom in PROTEGES
        assert nom in DEFAULT_SYSTEM_PROMPT


def test_la_garde_est_bien_branchee(ctx: ToolContext, monkeypatch) -> None:
    """Controle de MUTATION : on desactive la garde, le refus doit disparaitre.

    Sans ceci, les tests ci-dessus resteraient verts meme si quelqu'un decablait l'appel a
    `_cible_protegee` dans le handler — precisement l'etat dans lequel le code se trouvait
    avant ce commit : fonction ecrite, jamais appelee.
    """
    import hermes.tools.shell as mod
    monkeypatch.setattr(mod, "_cible_protegee", lambda _c: None)
    reg = build_registry()
    sortie = asyncio.run(reg.dispatch(ctx, "shell", {"command": CIBLE_INOFFENSIVE}))
    assert "refusee" not in sortie


def test_le_workspace_temporaire_reste_propre(tmp_path: Path) -> None:
    """Un refus ne doit pas laisser trainer le script .hermes_*.py."""
    reg = build_registry()
    c = ToolContext(workspace=tmp_path, exec_timeout=20, output_limit=4000,
                    request_timeout=10, search_url="x")
    asyncio.run(reg.dispatch(c, "python", {"code": 'import os; os.system("pkill mt5_desk.py")'}))
    assert not list(tmp_path.glob(".hermes_*.py"))


# ---------------------------------------------------------------------------
# Le contournement par PID.
# 🚨 Ces tests existent parce que la garde par NOM seule a laisse passer le degat :
# refuse six fois sur `Get-Process chrome | Stop-Process`, le modele a lance `tasklist`,
# lu les PID, puis `taskkill /F /PID 5916`. Chrome est tombe. Filtrer le texte ne suffit
# pas, il faut resoudre le PID vers son executable reel.
# ---------------------------------------------------------------------------

def test_le_pid_se_resout_vraiment() -> None:
    """La resolution ctypes fonctionne sur un processus dont on connait le nom : nous."""
    import os
    from hermes.tools.shell import _nom_du_pid
    assert _nom_du_pid(os.getpid()).startswith("python")


def test_pid_inexistant_ne_bloque_rien() -> None:
    from hermes.tools.shell import _nom_du_pid
    assert _nom_du_pid(999999) == ""
    assert _cible_protegee("taskkill /F /PID 999999") is None


PID_FICTIF = 4242


@pytest.fixture()
def pid_chrome(monkeypatch):
    """Fait passer PID_FICTIF pour un chrome.exe, sans dependre d'un vrai Chrome."""
    import hermes.tools.shell as mod
    monkeypatch.setattr(mod, "_nom_du_pid",
                        lambda pid: "chrome.exe" if pid == PID_FICTIF else "")
    return PID_FICTIF


PAR_PID = [
    "taskkill /F /PID %d",
    "Stop-Process -Id %d -Force",
    "taskkill /F /PID 111 /PID %d",                  # noye au milieu d'autres PID
    "python -c 'import os; os.kill(%d, 9)'",
]


@pytest.mark.parametrize("gabarit", PAR_PID)
def test_refuse_par_pid(gabarit: str, pid_chrome: int) -> None:
    motif = _cible_protegee(gabarit % pid_chrome)
    assert motif is not None, gabarit
    assert str(pid_chrome) in motif and "chrome.exe" in motif


def test_inspecter_un_pid_protege_reste_permis(pid_chrome: int) -> None:
    """Regarder n'est pas tuer : sans verbe d'arret, on ne bloque pas."""
    assert _cible_protegee('tasklist /FI "PID eq %d"' % pid_chrome) is None
    assert _cible_protegee("Get-Process -Id %d" % pid_chrome) is None


def test_le_refus_par_nom_ne_renvoie_plus_vers_le_pid() -> None:
    """L'ancien message conseillait de viser le PID : il enseignait le contournement."""
    motif = _cible_protegee("taskkill /F /IM chrome.exe")
    assert "verifies" in motif


# ---------------------------------------------------------------------------
# Le contournement par fichier : write_file puis `python tuer.py`.
# La commande ne montre rien ; c'est le CONTENU du script qui tue.
# ---------------------------------------------------------------------------

def test_script_qui_tue_est_refuse(ctx: ToolContext) -> None:
    reg = build_registry()
    (ctx.workspace / "tuer.py").write_text(
        'import os\nos.system("taskkill /F /IM mt5_desk.py")\n', encoding="utf-8")
    sortie = asyncio.run(reg.dispatch(ctx, "shell", {"command": "python tuer.py"}))
    assert "refusee" in sortie
    assert "tuer.py" in sortie                       # l'agent sait D'OU vient le refus


def test_script_powershell_aussi(ctx: ToolContext) -> None:
    reg = build_registry()
    (ctx.workspace / "stop.ps1").write_text(
        "Get-Process mt5_desk.py | Stop-Process -Force\n", encoding="utf-8")
    sortie = asyncio.run(reg.dispatch(
        ctx, "shell", {"command": 'powershell -File "stop.ps1"'}))
    assert "refusee" in sortie


def test_script_anodin_passe(ctx: ToolContext) -> None:
    reg = build_registry()
    (ctx.workspace / "ok.py").write_text("print('bonjour')\n", encoding="utf-8")
    sortie = asyncio.run(reg.dispatch(ctx, "shell", {"command": "python ok.py"}))
    assert "bonjour" in sortie
    assert "refusee" not in sortie


def test_script_absent_ne_casse_rien(ctx: ToolContext) -> None:
    """Un nom de script qui n'existe pas ne doit ni bloquer ni lever."""
    from hermes.tools.shell import _script_protege
    assert _script_protege("python absent.py", ctx.workspace) is None


def test_pas_de_lecture_hors_workspace(ctx: ToolContext, tmp_path: Path) -> None:
    """Le nom de script ne doit pas servir a faire lire un fichier hors du workspace."""
    from hermes.tools.shell import _script_protege
    dehors = tmp_path.parent / "dehors.py"
    dehors.write_text('os.system("taskkill /F /IM mt5_desk.py")', encoding="utf-8")
    assert _script_protege("python ../dehors.py", ctx.workspace) is None


def test_nom_court_sans_extension_exe() -> None:
    """`mt5_desk` sans `.py` doit etre reconnu comme `chrome` sans `.exe`.

    Le nom court n'etait derive que des `.exe` : `Stop-Process -Name mt5_desk` passait.
    """
    assert _cible_protegee("Stop-Process -Name mt5_desk -Force") is not None
    assert _cible_protegee("Stop-Process -Name chrome -Force") is not None


def test_le_script_est_relu_meme_sans_verbe_dans_la_commande(ctx: ToolContext) -> None:
    """`python tuer.py` ne contient aucun verbe d'arret : la garde doit quand meme relire.

    Premiere version : le test du verbe etait en tete de fonction et faisait sortir avant
    la relecture du script. La couche existait, elle etait inatteignable.
    """
    from hermes.tools.shell import _cible_protegee as garde
    (ctx.workspace / "t.py").write_text(
        'import os; os.system("taskkill /F /IM mt5_desk.py")', encoding="utf-8")
    assert garde("python t.py", ctx.workspace) is not None
