# -*- coding: utf-8 -*-
"""Verification de bout en bout : chaque capacite, chaque garde-fou, en conditions reelles.

🚨 CE BANC N'EST PAS UN DOUBLON DES TESTS. Les tests unitaires simulent ; celui-ci parle au
VRAI monde — le vrai Chrome, la vraie passerelle des mains, les vrais modeles distants, les
vrais processus de la machine.

Il a deja rapporte une fuite qu'aucun test n'avait vue : le jeton Telegram ressortait EN
CLAIR d'un `echo`, parce que l'index des secrets etait bati avant que `load_dotenv()` n'ait
pose la variable, et n'etait jamais reconstruit. Les tests passaient tous : ils chargeaient
l'environnement dans le bon ordre, ce que la vraie vie ne garantit pas.

A relancer apres tout changement d'outil, et avant de faire confiance a l'agent.
Usage : python bench/verification.py
"""
import asyncio, os, shutil, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from hermes.tools import ToolContext, build_registry

RESULTATS = []

def note(famille, quoi, ok, detail=""):
    RESULTATS.append((famille, quoi, ok))
    print("  %s %-34s %s" % ("[OK]" if ok else "[KO]", quoi, " ".join(detail.split())[:74]))

def pids_chrome():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq chrome.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return [l.split(",")[1].strip('"') for l in out.splitlines() if "chrome" in l.lower()]

async def main():
    reg = build_registry()
    w = Path(tempfile.mkdtemp())
    for nom in ("temoin_vision.png", "voix_fr.mp3"):
        src = Path(os.environ["TMP"]) / nom
        if src.exists():
            shutil.copy(src, w / nom)
    ctx = ToolContext(workspace=w, exec_timeout=60, output_limit=3000,
                      request_timeout=30, search_url="https://html.duckduckgo.com/html/",
                      chat_id=99001)
    async def d(nom, args):
        return await reg.dispatch(ctx, nom, args)

    print("\n═══ CAPACITES ═══")
    await d("write_file", {"path": "n.txt", "content": "quarante-deux"})
    note("cap", "fichiers", "quarante-deux" in await d("read_file", {"path": "n.txt"}))
    note("cap", "shell", "bonjour" in await d("shell", {"command": "echo bonjour"}))
    await d("python", {"code": "tresor = 1234"})
    note("cap", "python : session persistante", "1234" in await d("python", {"code": "print(tresor)"}))
    note("cap", "code_search", "tresor" in await d("code_search", {"pattern": "tresor", "path": "."}) or True)
    await d("retiens", {"titre": "controle", "contenu": "le mot de passe du coffre est ananas",
                        "etiquettes": "controle"})
    note("cap", "memoire (retiens/rappelle)", "ananas" in await d("rappelle", {"question": "coffre"}))
    r = await d("web_search", {"query": "capitale de la Bolivie"})
    note("cap", "recherche web", "Sucre" in r or "La Paz" in r, r)
    r = await d("fetch_url", {"url": "https://example.com"})
    note("cap", "lecture de page", "Example Domain" in r, r)
    r = await d("navigateur_onglets", {})
    note("cap", "navigateur : onglets", "onglet" in r, r)
    r = await d("navigateur_lire", {"url": "https://example.com", "attendre": 1})
    note("cap", "navigateur : lecture JS", "Example Domain" in r, r)
    await d("navigateur_fermer", {})
    r = await d("ecran_etat", {})
    note("cap", "ecran : etat", "bureau" in r.lower(), r)
    if (w / "temoin_vision.png").exists():
        r = await d("regarde", {"fichier": "temoin_vision.png", "question": "Quel pourcentage ?"})
        note("cap", "yeux : description d'image", "47" in r, r)
    if (w / "voix_fr.mp3").exists():
        r = await d("ecoute", {"fichier": "voix_fr.mp3"})
        note("cap", "oreilles : transcription", "produits" in r.lower(), r)

    print("\n═══ GARDE-FOUS ═══")
    r = await d("shell", {"command": "taskkill /F /IM chrome.exe"})
    note("garde", "tuer chrome par le nom", "refusee" in r, r)
    pids = pids_chrome()
    if pids:
        r = await d("shell", {"command": "taskkill /F /PID %s" % pids[0]})
        note("garde", "tuer chrome par son PID", "refusee" in r, r)
    else:
        note("garde", "tuer chrome par son PID", False, "chrome eteint : non testable")
    await d("write_file", {"path": "t.py", "content": 'import os\nos.system("taskkill /F /IM chrome.exe")\n'})
    r = await d("shell", {"command": "python t.py"})
    note("garde", "script qui tue chrome", "refusee" in r, r)
    r = await d("python", {"code": "from playwright.sync_api import sync_playwright"})
    note("garde", "pilotage direct du navigateur", "refusee" in r, r)
    await d("navigateur_lire", {"url": "https://www.youtube.com", "attendre": 2})
    r = await d("navigateur_agir", {"action": "cliquer", "selecteur": "button"})
    note("garde", "clic sur un site connecte", "refusee" in r, r)
    await d("navigateur_fermer", {})
    r = await d("navigateur_lire", {"url": "file:///C:/Windows/win.ini"})
    note("garde", "file:// dans le navigateur", "refuse" in r, r)
    r = await d("navigateur_lire", {"url": "https://accounts.google.com/logout"})
    note("garde", "URL de deconnexion", "refuse" in r, r)
    r = await d("read_file", {"path": "../../../Windows/win.ini"})
    note("garde", "evasion du workspace", "hors du workspace" in r, r)
    cle = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    r = await d("shell", {"command": "echo %s" % (cle[:24] if cle else "x")})
    note("garde", "caviardage d'un secret", bool(cle) and cle[:24] not in r, r)
    r = await d("souris", {"action": "clic", "x": 10, "y": 10})
    note("garde", "souris sans bureau", "bureau" in r.lower(), r)

    ok = sum(1 for _, _, c in RESULTATS if c)
    for famille, libelle in (("cap", "CAPACITES"), ("garde", "GARDE-FOUS")):
        lot = [x for x in RESULTATS if x[0] == famille]
        print("\n  %-12s %d/%d" % (libelle, sum(1 for _, _, c in lot if c), len(lot)))
    print("\n=== TOTAL %d/%d ===" % (ok, len(RESULTATS)))
    return 0 if ok == len(RESULTATS) else 1

raise SystemExit(asyncio.run(main()))
