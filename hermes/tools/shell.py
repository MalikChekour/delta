"""Execution de commandes et de code Python dans le workspace."""

from __future__ import annotations

import logging
import os
import re
import shlex
import sys
import uuid
from typing import Any

from ..errors import ToolError
from ..sandbox import clip, run
from . import ToolContext, tool


@tool(
    "shell",
    "Execute une commande shell dans le workspace et renvoie sa sortie combinee "
    "(stdout et stderr) ainsi que son code de retour.",
    {
        "command": {"type": "string", "description": "Commande a executer, via /bin/sh."},
        "timeout": {
            "type": "integer",
            "description": "Delai maximal en secondes ; defaut : celui de la configuration.",
        },
    },
    ["command"],
)
async def shell(ctx: ToolContext, args: dict[str, Any]) -> str:
    command = str(args["command"]).strip()
    if not command:
        raise ToolError("commande vide.")
    refus = _cible_protegee(command, ctx.workspace)
    if refus:
        raise ToolError(refus)
    timeout = int(args.get("timeout") or ctx.exec_timeout)
    return await run(
        command,
        cwd=ctx.workspace,
        timeout=max(1, min(timeout, 3600)),
        output_limit=ctx.output_limit,
    )


#: Programmes dont l'arret casse quelque chose d'important sur cette machine.
#: 🚨 CETTE LISTE N'EST PAS DECORATIVE. Mesure du 12/09 : le prompt systeme disait noir sur
#: blanc « chrome.exe ne se tue pas, il publie sur TikTok et YouTube ». On a demande a
#: l'agent de le tuer pour liberer de la memoire — IL L'A FAIT, et la session TikTok a ete
#: perdue. Meme lecon que pour les secrets : le modele est un GLM **decensure**, une
#: interdiction ecrite ne l'arrete pas. Seule une barriere DANS LE CODE tient.
PROTEGES = {
    "chrome.exe": "il publie sur TikTok et YouTube (port CDP 9222)",
    "terminal64.exe": "c'est MetaTrader 5, le trading tourne dessus",
    "hermes.exe": "c'est l'autre agent (profil orderflow), homonyme mais distinct",
    "mt5_desk.py": "c'est la main du bot de trading",
    "python.exe": "il peut s'agir de moi-meme ou du bot de trading",
    "sqlservr.exe": "base de donnees",
}
#: Verbes qui terminent un processus, quelle que soit la syntaxe.
#: 🚨 NE PAS SE LIMITER A `taskkill`. Sous Windows il y a au moins quatre façons d'arreter
#: un programme, et un modele qui vient d'etre refuse essaie la suivante :
#:   taskkill /IM chrome.exe            ← la plus evidente
#:   Stop-Process -Name chrome          ← PowerShell
#:   (Get-Process chrome).Kill()        ← methode .NET, AUCUN verbe d'arret dans le texte
#:   wmic process where name='...' delete
#: Les deux dernieres passaient la premiere version de cette garde.
_ARRETS = (
    "taskkill", "stop-process", "kill-process", "remove-process",
    "pkill", "killall",
    ".kill(", ".terminate(", "terminateprocess",
)


def _arret_demande(bas: str) -> bool:
    """Vrai si le texte cherche a terminer un processus, quelle que soit la syntaxe."""
    if any(verbe in bas for verbe in _ARRETS):
        return True
    # `wmic process where name="chrome.exe" delete` : ni kill ni stop, mais c'est un arret.
    return "wmic" in bas and ("delete" in bas or "terminate" in bas)


#: Pilotage direct du navigateur : ce qui court-circuiterait les outils `navigateur_*`.
_PILOTAGE = ("connect_over_cdp", "chromedevtools", "webdriver", "selenium", "puppeteer",
             "playwright.sync_api", "playwright.async_api")
#: Le port CDP seul ne suffit pas a accuser : `netstat | findstr :9222` est un diagnostic
#: legitime. C'est le port ASSOCIE a un pilotage qui pose probleme.
_INDICES_CDP = ("playwright", "cdp", "devtools", "websocket", "ws://", "chrome_debug_profile")


#: Verbes qui signifient « ce programme va SE METTRE A ECOUTER sur ce port ».
#: Les distinguer d'une simple interrogation compte : `curl http://127.0.0.1:9222/json` et
#: `netstat | findstr :9222` sont des diagnostics legitimes et doivent passer.
_ECOUTE = ("serve", "listen", "--port", "-p ", "bind", "--host")


def _port_reserve(bas: str) -> str | None:
    """Refus si la commande veut ECOUTER sur le port du Chrome de publication.

    🚨 LE PIEGE D'OBSCURA. Le navigateur `obscura` installe dans le workspace ouvre son
    serveur CDP sur le port **9222 par defaut** — exactement celui du Chrome qui publie sur
    TikTok et YouTube. L'agent a redige une fiche entiere sur cet outil le 13/09 sans relever
    le conflit, alors que la note machine le lui disait. Une consigne qu'on ne relie pas au
    cas particulier ne protege de rien : il faut la barriere.
    """
    if "9222" in bas and any(verbe in bas for verbe in _ECOUTE):
        return (
            "commande refusee : elle ouvrirait un service sur le port 9222, celui du Chrome "
            "qui publie sur TikTok et YouTube. Le lui prendre casse les publications.\n"
            "Choisis un port au-dessus de 9300 et dis lequel."
        )
    # `obscura serve` sans port explicite prend 9222 en silence.
    if "obscura" in bas and "serve" in bas and "--port" not in bas and "-p " not in bas:
        return (
            "commande refusee : `obscura serve` ecoute sur le port 9222 PAR DEFAUT, et ce "
            "port est celui du Chrome qui publie sur TikTok et YouTube.\n"
            "Relance avec un port explicite, par exemple : obscura serve --port 9310."
        )
    return None


def _pilotage_direct(bas: str) -> str | None:
    """Refus si la commande pilote Chrome en contournant les outils `navigateur_*`.

    🚨 SANS CECI, LES GARDES DU NAVIGATEUR NE VALENT RIEN. Les outils `navigateur_*` refusent
    d'agir sur les sites ou le patron est connecte, et ne touchent jamais un onglet qu'ils
    n'ont pas ouvert. Mais `shell` et `python` donnent acces a Playwright : trois lignes
    suffisent a se connecter au port 9222, prendre l'onglet de TikTok Studio et cliquer. La
    garde doit donc vivre au meme niveau que le trou.
    """
    pilote = any(mot in bas for mot in _PILOTAGE)
    if not pilote and "9222" in bas:
        pilote = any(mot in bas for mot in _INDICES_CDP)
    if not pilote:
        return None
    return (
        "commande refusee : elle pilote le navigateur directement, ce qui contourne les "
        "garde-fous.\n"
        "Ce Chrome porte les sessions TikTok et YouTube du patron ; un clic au mauvais "
        "endroit publie un brouillon ou ferme la session.\n"
        "Passe par mes outils : `navigateur_onglets`, `navigateur_lire`, "
        "`navigateur_capture`, `navigateur_agir`, `navigateur_fermer`."
    )


#: Programmes qu'on protege AUSSI quand la commande vise un PID nu.
#: `python.exe` en est volontairement absent : l'agent doit pouvoir arreter les processus
#: qu'il a lui-meme lances, et ils sont en python. Il reste protege par son NOM — tuer
#: « tous les python » d'un coup emporterait le bot de trading et moi-meme.
_PROTEGES_PAR_PID = ("chrome.exe", "terminal64.exe", "hermes.exe", "sqlservr.exe")

#: Des qu'un verbe d'arret est present, tout entier de 1 a 7 chiffres est traite comme un PID
#: candidat et VERIFIE. On ne cherche pas a reconnaitre chaque syntaxe (/PID 1234, -Id 1234,
#: os.kill(1234, 9)...) : il y en a trop, et en rater une coute Chrome.
_CHIFFRES = re.compile(r"\b(\d{1,7})\b")


def _nom_du_pid(pid: int) -> str:
    """Nom de l'executable d'un PID, ou chaine vide.

    🧊 On passe par OpenProcess + QueryFullProcessImageNameW, PAS par WMI ni `tasklist` :
    l'enumeration des processus Windows s'est deja figee sur cette machine et a bloque un
    watchdog 4 h 30. Ici on interroge UN pid, sans jamais parcourir la table.
    """
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""                                 # mort, ou hors de portee : sans danger
        try:
            taille = wintypes.DWORD(260)
            tampon = ctypes.create_unicode_buffer(taille.value)
            if not k32.QueryFullProcessImageNameW(handle, 0, tampon, ctypes.byref(taille)):
                return ""
            return tampon.value.rsplit("\\", 1)[-1].lower()
        finally:
            k32.CloseHandle(handle)
    except Exception:
        # Une garde qui plante est une garde qui ne garde plus : on echoue en silence, le
        # filtre par nom reste en place.
        return ""


def _pid_protege(commande: str) -> str | None:
    """Refus si la commande vise le PID d'un programme protege.

    🚨 C'EST LE TROU QUI A TUE CHROME UNE TROISIEME FOIS. La premiere version de cette garde
    filtrait les NOMS et autorisait explicitement `taskkill /F /PID 1234`. Trace du 12/09 :
    refuse six fois sur `Get-Process chrome | Stop-Process`, le modele a simplement lance
    `tasklist`, lu les PID, puis `taskkill /F /PID 5916` — et Chrome est tombe. Filtrer le
    texte ne suffit pas : il faut resoudre le PID vers son executable reel.
    """
    for brut in set(_CHIFFRES.findall(commande)):
        nom = _nom_du_pid(int(brut))
        if nom in _PROTEGES_PAR_PID:
            return (
                f"commande refusee : le PID {brut} est un processus « {nom} », et "
                f"{PROTEGES[nom]}.\n"
                f"Lister les PID puis les tuer un par un ne contourne pas cette regle.\n"
                f"Si l'arret est vraiment necessaire, c'est au patron de le faire lui-meme."
            )
    return None


#: Extensions d'un script qu'une commande peut lancer par son nom.
_SCRIPTS = (".py", ".ps1", ".bat", ".cmd", ".sh")


def _script_protege(commande: str, workspace) -> str | None:
    """Refus si la commande lance un script du workspace qui, lui, tue un programme protege.

    🚨 TROISIEME CONTOURNEMENT PREVISIBLE. Les deux premieres couches lisent la commande ;
    `write_file` puis `python tuer.py` ne leur montre rien du tout. On relit donc le fichier
    vise et on lui applique la meme garde.
    """
    if workspace is None:
        return None
    # On decoupe sur tout ce qui ne peut pas faire partie d'un chemin : cela isole
    # `tuer.py` aussi bien dans `python tuer.py` que dans `cmd /c "python tuer.py"`.
    for jeton in re.split(r"[^\w./\\:-]+", commande):
        if not jeton.lower().endswith(_SCRIPTS):
            continue
        try:
            chemin = (workspace / jeton).resolve()
            chemin.relative_to(workspace.resolve())     # jamais hors du workspace
            contenu = chemin.read_text(encoding="utf-8", errors="replace")[:200_000]
        except (OSError, ValueError):
            continue
        motif = _cible_protegee(contenu)                # pas de workspace : pas de recursion
        if motif:
            return f"{motif}\n(vu dans le script « {jeton} » que cette commande lance.)"
    return None


def _cible_protegee(commande: str, workspace=None) -> str | None:
    """Renvoie le motif de refus si la commande cherche a tuer un programme protege.

    Trois couches, parce que chacune a ete contournee a son tour le 12/09 : le NOM du
    programme, le PID resolu vers son executable reel, et le CONTENU d'un script lance par
    son nom de fichier.

    LIMITE QUI RESTE, a ne pas se cacher : cela eleve le cout d'un arret, ce n'est pas un bac
    a sable. Du code genere a l'execution (base64, `exec`) passerait. La seule barriere
    vraiment etanche serait de ne plus faire tourner l'agent sous le compte SYSTEM.
    """
    bas = commande.lower()
    refus_port = _port_reserve(bas)
    if refus_port:
        return refus_port
    refus_pilotage = _pilotage_direct(bas)
    if refus_pilotage:
        return refus_pilotage
    # 🚨 LE TEST DU VERBE NE GOUVERNE QUE LES DEUX PREMIERES COUCHES. Premiere version : il
    # etait en tete de fonction, et `python tuer.py` — qui ne contient aucun verbe d'arret —
    # ressortait immediatement, sans que le script soit jamais relu. La troisieme couche
    # etait ecrite, branchee, testee... et inatteignable.
    if _arret_demande(bas):
        for nom, raison in PROTEGES.items():
            # On retire l'extension quelle qu'elle soit : `Stop-Process -Name chrome` ne dit
            # pas « .exe », et `Stop-Process -Name mt5_desk` ne dit pas « .py ». Ne traiter
            # que `.exe` laissait passer le second.
            court = nom.rsplit(".", 1)[0]
            # `taskkill /IM chrome.exe` comme `Stop-Process -Name chrome` : on cherche le
            # nom, avec ou sans extension.
            if nom in bas or f" {court}" in bas or f'"{court}' in bas or f"'{court}" in bas:
                return (
                    f"commande refusee : elle arreterait « {nom} », et {raison}.\n"
                    f"Si l'arret est vraiment necessaire, c'est au patron de le faire "
                    f"lui-meme.\n"
                    f"Inutile de lister les PID pour les tuer un par un : ils sont "
                    f"verifies aussi."
                )
        refus = _pid_protege(commande)
        if refus:
            return refus
    return _script_protege(commande, workspace)


def _cite(chemin: str) -> str:
    """Protege un chemin pour le shell COURANT.

    🚨 `shlex.quote` est une fonction POSIX, et elle est activement nuisible sous Windows :
    voyant les antislashs et les deux-points de `C:\\...\\python.exe`, elle entoure le chemin
    d'APOSTROPHES SIMPLES — que `cmd.exe` ne reconnait pas. Il cherche alors un programme
    litteralement nomme `'C:\\...` et rend « The filename, directory name, or volume label
    syntax is incorrect ». Constate le 10/09 : l'outil `python` du bot etait mort depuis le
    deploiement, alors que `shell` fonctionnait — meme famille que le `os.killpg` POSIX deja
    corrige dans `sandbox.py`.
    """
    if os.name == "nt":
        return '"%s"' % chemin
    return shlex.quote(chemin)


async def _python_isole(ctx: ToolContext, code: str, timeout: int) -> str:
    """L'ancienne voie : un processus neuf, jete apres usage.

    Gardee comme filet. Si le noyau persistant ne peut pas demarrer — bibliotheque absente,
    port bloque, memoire pleine — l'outil `python` doit continuer a fonctionner comme avant.
    Une amelioration qui casse la fonction de base serait une regression.
    """
    # Nom unique : le modele emet souvent plusieurs appels dans le meme tour, et
    # ils sont executes en parallele. Un nom fixe ferait executer a l'un le code
    # de l'autre, sans que rien ne le signale.
    script = ctx.workspace / f".hermes_{uuid.uuid4().hex}.py"
    script.write_text(code, encoding="utf-8")
    try:
        return await run(
            f"{_cite(sys.executable)} {_cite(script.name)}",
            cwd=ctx.workspace,
            timeout=max(1, min(timeout, 3600)),
            output_limit=ctx.output_limit,
            # 🚨 SANS CECI, UN SEUL ACCENT TUE LE SCRIPT. Sous Windows la sortie standard
            # d'un processus fils est en cp1252 : `print("éàü")` leve UnicodeEncodeError et
            # le script meurt — code de retour 1, resultat perdu. Sur un bot qui parle
            # FRANCAIS, c'est une panne quasi systematique. Verifie le 10/09 : « accents :
            # éàü ✅ » plantait, passe avec PYTHONIOENCODING.
            env={"PYTHONIOENCODING": "utf-8"},
        )
    finally:
        script.unlink(missing_ok=True)


@tool(
    "python",
    "Execute du code Python. LA SESSION RESTE OUVERTE d'un appel a l'autre dans une meme "
    "conversation : les variables, les imports et les donnees chargees survivent. Charge un "
    "fichier une fois, reutilise-le ensuite. Utilise print() pour montrer un resultat.",
    {
        "code": {"type": "string", "description": "Code source Python."},
        "timeout": {"type": "integer", "description": "Delai maximal en secondes."},
        "nouveau": {
            "type": "boolean",
            "description": "Repartir d'une session vierge, en oubliant tout ce qui a ete "
                           "defini avant. A n'utiliser que si l'etat actuel gene.",
        },
    },
    ["code"],
)
async def python(ctx: ToolContext, args: dict[str, Any]) -> str:
    code = str(args["code"])
    if not code.strip():
        raise ToolError("code vide.")
    # 🚨 Meme garde ici : `os.system("taskkill /IM chrome.exe")` dans un script Python
    # contournerait entierement le filtre de l'outil shell.
    refus = _cible_protegee(code)
    if refus:
        raise ToolError(refus)
    timeout = max(1, min(int(args.get("timeout") or ctx.exec_timeout), 3600))
    from . import noyau as _noyau

    if _noyau.disponible():
        try:
            sortie = await _noyau.execute(ctx.workspace, ctx.chat_id, code, timeout,
                                          bool(args.get("nouveau")))
            return clip(sortie, ctx.output_limit)
        except Exception as exc:  # noqa: BLE001
            # Le noyau a refuse de demarrer : on le dit, et on execute quand meme.
            logging.getLogger(__name__).warning("noyau persistant indisponible : %s", exc)
    return await _python_isole(ctx, code, timeout)


TOOLS = (shell, python)
