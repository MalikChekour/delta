# -*- coding: utf-8 -*-
"""Missions complexes : plusieurs capacites qui doivent s'enchainer pour reussir.

🚨 CE BANC N'EST PAS `banc.py`. Celui-la pose des questions courtes et mesure une capacite a
la fois. Ici, chaque mission demande d'enchainer — chercher PUIS recouper, ecrire du code
PUIS le tester, charger des donnees PUIS les reutiliser au tour suivant, produire un
graphique PUIS le REGARDER pour en conclure quelque chose. C'est la ou un agent qui repond
bien aux questions simples se casse.

Chaque mission est verifiee par du code, jamais a l'oeil : un fichier qui existe et dont le
contenu est juste, une fonction qui rend la bonne valeur, un mot precis dans la reponse.
L'agent peut raconter ce qu'il veut, c'est le disque qui tranche.

Usage : python bench/missions.py [nom de mission...]
"""
from __future__ import annotations

import csv
import json
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
SUPPORTS = Path(__file__).resolve().parent / "supports"
sys.path.insert(0, str(RACINE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PY = RACINE / "venv" / "Scripts" / "python.exe"
ATELIER = RACINE / "workspace"

#: Mot que seule l'execution du JavaScript fait apparaitre dans la page.
SECRET_JS = "ORAGE-7741"
PORT_JS = 0


# --------------------------------------------------------------------------- #
# Le serveur de la page JavaScript
# --------------------------------------------------------------------------- #

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Coffre</title></head>
<body><h1>Coffre</h1><p id="cible">Chargement...</p>
<script>
  // 🚨 Le mot n'est PAS dans le HTML servi : `fetch_url` ne peut pas le trouver.
  // Seul un vrai navigateur qui execute le script le fait apparaitre.
  var morceaux = ["OR", "AGE", "-", "77", "41"];
  document.getElementById("cible").textContent = "Mot de passe : " + morceaux.join("");
</script></body></html>"""


class PageJS(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a) -> None:
        pass

    def do_GET(self) -> None:                        # noqa: N802
        corps = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)


def _port_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------- #
# Preparations
# --------------------------------------------------------------------------- #

def _propre(garder: tuple = ()) -> None:
    ATELIER.mkdir(exist_ok=True)
    for motif in ("*.py", "*.csv", "*.txt", "*.png", "*.mp3"):
        for f in ATELIER.glob(motif):
            if f.name not in garder:
                f.unlink(missing_ok=True)
    for d in ("__pycache__", ".pytest_cache"):
        shutil.rmtree(ATELIER / d, ignore_errors=True)


def prep_rien() -> None:
    _propre()


def prep_image() -> None:
    _propre()
    shutil.copy(SUPPORTS / "barres.png", ATELIER / "barres.png")


def prep_voix() -> None:
    _propre()
    shutil.copy(SUPPORTS / "consigne.mp3", ATELIER / "consigne.mp3")


def prep_mesures() -> None:
    """Une serie franchement croissante, avec du bruit : la tendance doit etre indiscutable."""
    _propre()
    alea = random.Random(11)
    with (ATELIER / "mesures.csv").open("w", newline="", encoding="utf-8") as f:
        plume = csv.writer(f)
        plume.writerow(["date", "valeur"])
        for jour in range(1, 61):
            plume.writerow(["2026-07-%02d" % jour if jour <= 31 else "2026-08-%02d" % (jour - 31),
                            round(40 + jour * 3.2 + alea.uniform(-6, 6), 1)])


# --------------------------------------------------------------------------- #
# Verifications — c'est le disque qui tranche, pas le recit de l'agent
# --------------------------------------------------------------------------- #

def _mots(texte: str) -> str:
    return " ".join(texte.lower().split())


def v_sources(atelier: Path, reponses: list) -> tuple[bool, str]:
    texte = " ".join(reponses)
    domaines = set(re.findall(r"https?://([\w.-]+)", texte))
    nombres = re.findall(r"\b\d{1,3}[\s.,]?\d{3}[\s.,]?\d{3}\b|\b\d{1,3}[.,]\d\s*millions?\b"
                         r"|\b\d{1,3}\s*millions?\b", texte)
    if len(domaines) < 2:
        return False, "moins de deux sources citees (%s)" % (", ".join(domaines) or "aucune")
    if len(nombres) < 2:
        return False, "moins de deux chiffres donnes"
    return True, ""


def v_luhn(atelier: Path, reponses: list) -> tuple[bool, str]:
    module = atelier / "siret.py"
    if not module.exists():
        return False, "siret.py n'a pas ete ecrit"
    essai = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "from siret import valide\n"
        "print('OK' if (valide('73282932000074') is True and "
        "valide('40483304800022') is True and valide('73282932000075') is False and "
        "valide('1234') is False) else 'FAUX')\n" % atelier
    )
    r = subprocess.run([str(PY), "-c", essai], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    if "OK" not in (r.stdout or ""):
        return False, "la fonction est fausse : %s" % _mots((r.stdout or "") + (r.stderr or ""))[:90]
    if not (atelier / "test_siret.py").exists():
        return False, "test_siret.py n'a pas ete ecrit"
    p = subprocess.run([str(PY), "-m", "pytest", "-q", "test_siret.py"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=str(atelier),
                       timeout=150)
    if p.returncode != 0:
        return False, "les tests ecrits ne passent pas"
    return True, ""


def v_session(atelier: Path, reponses: list) -> tuple[bool, str]:
    fichier = atelier / "top.csv"
    if not fichier.exists():
        return False, "top.csv n'a pas ete ecrit"
    valeurs = [l.strip() for l in fichier.read_text(encoding="utf-8").splitlines() if l.strip()]
    valeurs = [v for v in valeurs if re.fullmatch(r"-?\d+(\.\d+)?", v.replace(",", "."))]
    if len(valeurs) != 10:
        return False, "%d valeurs dans top.csv au lieu de 10" % len(valeurs)
    nombres = [float(v.replace(",", ".")) for v in valeurs]
    if nombres != sorted(nombres, reverse=True):
        return False, "les valeurs ne sont pas des dix PLUS GRANDES triees"
    if not all(5 <= n <= 900 for n in nombres):
        return False, "des valeurs sortent de l'intervalle demande"
    return True, ""


def v_page_js(atelier: Path, reponses: list) -> tuple[bool, str]:
    texte = " ".join(reponses).upper().replace(" ", "")
    if SECRET_JS.replace("-", "") not in texte.replace("-", ""):
        return False, "le mot de passe n'a pas ete rapporte"
    return True, ""


def v_voir(atelier: Path, reponses: list) -> tuple[bool, str]:
    texte = _mots(" ".join(reponses))
    if "sud" not in texte:
        return False, "la barre la plus haute n'est pas identifiee"
    if "rouge" not in texte:
        return False, "la couleur n'est pas donnee"
    return True, ""


def v_ecouter(atelier: Path, reponses: list) -> tuple[bool, str]:
    fichier = atelier / "tournesol.txt"
    if not fichier.exists():
        presents = ", ".join(f.name for f in atelier.glob("*.txt")) or "aucun"
        return False, "tournesol.txt absent (fichiers txt : %s)" % presents
    if "soleil" not in fichier.read_text(encoding="utf-8").lower():
        return False, "le fichier ne contient pas le mot demande"
    return True, ""


def v_memoire(atelier: Path, reponses: list) -> tuple[bool, str]:
    if "8814" not in " ".join(reponses):
        return False, "le code n'a pas ete retrouve dans l'autre conversation"
    return True, ""


def v_chaine(atelier: Path, reponses: list) -> tuple[bool, str]:
    images = [f for f in atelier.glob("*.png")]
    if not images:
        return False, "aucun graphique produit"
    if not any(f.stat().st_size > 3000 for f in images):
        return False, "le graphique produit est vide ou minuscule"
    texte = _mots(reponses[-1])
    monte = any(m in texte for m in ("monte", "croissant", "croissante", "hausse", "augmente",
                                     "ascendante", "progresse", "positive"))
    if not monte:
        return False, "la tendance n'est pas decrite comme croissante"
    return True, ""


# --------------------------------------------------------------------------- #
# Les missions
# --------------------------------------------------------------------------- #

def missions() -> list:
    return [
        ("sources", "recherche", prep_rien, [
            "Combien d'habitants compte l'agglomeration de Lagos au Nigeria ? Recoupe DEUX "
            "sources differentes, donne les deux chiffres AVEC leurs adresses, et dis-moi "
            "clairement si elles concordent ou divergent."
        ], v_sources, 8),

        ("luhn", "code", prep_rien, [
            "Ecris `siret.py` avec une fonction `valide(siret)` qui rend True si la chaine "
            "est un SIRET valide : exactement 14 chiffres, et somme de Luhn multiple de 10. "
            "Ecris ensuite `test_siret.py` (pytest) avec au moins quatre cas, dont "
            "73282932000074 valide, 40483304800022 valide, 73282932000075 invalide et '1234' "
            "invalide. Lance pytest et montre-moi le resultat reel."
        ], v_luhn, 10),

        ("session", "donnees", prep_rien, [
            "En Python, cree une liste `ventes` de 5000 entiers pseudo-aleatoires entre 5 et "
            "900 avec la graine 7. Dis-moi seulement combien il y en a.",
            "Sans recreer la liste, donne-moi la moyenne et la mediane de `ventes`.",
            "Toujours sans la recreer : ecris dans `top.csv` les dix plus grandes valeurs de "
            "`ventes`, une par ligne, de la plus grande a la plus petite, sans en-tete.",
        ], v_session, 10),

        ("page_js", "navigateur", prep_rien, [
            "La page http://127.0.0.1:{port}/ affiche un mot de passe, mais il n'apparait "
            "qu'une fois le JavaScript execute — une simple lecture du HTML ne le montre pas. "
            "Donne-le-moi exactement."
        ], v_page_js, 8),

        ("voir", "vision", prep_image, [
            "Le fichier `barres.png` est un graphique a barres sans aucun chiffre. Quelle "
            "barre est la plus haute, et de quelle couleur est-elle ?"
        ], v_voir, 8),

        ("ecouter", "audio", prep_voix, [
            "Le fichier `consigne.mp3` contient une instruction parlee. Ecoute-la et execute "
            "exactement ce qu'elle demande."
        ], v_ecouter, 8),

        ("memoire", "memoire", prep_rien, [
            "Retiens ceci : le code du coffre de l'atelier est 8814, et la cle de secours est "
            "sous le pot bleu.",
            ("__NOUVELLE_CONVERSATION__", "Quel est le code du coffre de l'atelier ?"),
        ], v_memoire, 8),

        ("chaine", "composite", prep_mesures, [
            "Le fichier `mesures.csv` a deux colonnes : date et valeur. Trace l'evolution de "
            "`valeur` dans un graphique `courbe.png` — installe ce qui te manque si besoin. "
            "Ensuite REGARDE ton propre graphique et dis-moi si la tendance monte ou descend, "
            "en te fondant sur ce que tu vois dessus.",
        ], v_chaine, 14),
    ]


# --------------------------------------------------------------------------- #

async def joue(mission, config, registre) -> tuple[bool, str, int, float, str]:
    import hermes.config as C  # noqa: F401
    from hermes.agent import Agent
    from hermes.memory import Store
    import tempfile

    nom, _famille, preparer, tours, verifier, _minutes = mission
    preparer()
    agent = Agent(config, registre, Store(Path(tempfile.mkdtemp()) / f"{nom}.db"))
    chat = abs(hash(nom)) % 100000 + 500000
    reponses, etapes = [], 0
    depart = time.time()
    for tour in tours:
        if isinstance(tour, tuple) and tour[0] == "__NOUVELLE_CONVERSATION__":
            chat += 1                                  # autre fil : la memoire doit suivre
            tour = tour[1]
        tour = tour.replace("{port}", str(PORT_JS))
        reponse = await agent.respond(chat, tour)
        reponses.append(reponse.text or "")
        # `tool_calls` est deja un COMPTE, pas une liste : en prendre la longueur levait
        # « object of type 'int' has no len() » et faisait echouer la mission avant meme
        # qu'elle ne commence.
        etapes += int(getattr(reponse, "tool_calls", 0) or 0)
    duree = time.time() - depart
    try:
        ok, detail = verifier(ATELIER, reponses)
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, f"verification impossible : {type(exc).__name__} : {exc}"
    return ok, detail, etapes, duree, (reponses[-1] if reponses else "")


async def principal() -> int:
    import hermes.config as C
    from hermes.tools import build_registry

    global PORT_JS
    PORT_JS = _port_libre()
    serveur = ThreadingHTTPServer(("127.0.0.1", PORT_JS), PageJS)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()

    config = C.load(require_telegram=False)
    registre = build_registry()
    voulues = sys.argv[1:]
    resultats = []
    print("\nMissions complexes — chaque resultat est verifie sur le disque, pas cru sur parole.\n")
    for mission in missions():
        nom, famille = mission[0], mission[1]
        if voulues and nom not in voulues:
            continue
        try:
            ok, detail, etapes, duree, derniere = await joue(mission, config, registre)
        except Exception as exc:  # noqa: BLE001
            ok, detail, etapes, duree, derniere = (
                False, f"ERREUR {type(exc).__name__} : {exc}", 0, 0.0, "")
        # On garde la derniere reponse : sans elle, un echec ne dit que « rate », et il faut
        # tout rejouer a la main pour comprendre pourquoi.
        resultats.append({"nom": nom, "famille": famille, "ok": ok,
                          "secondes": round(duree, 1), "detail": detail,
                          "reponse": (derniere or "")[:600]})
        print("  %s %-10s %-11s %6.1f s  %s"
              % ("[OK]" if ok else "[KO]", nom, famille, duree, detail[:74]), flush=True)

    serveur.shutdown()
    ok = sum(1 for r in resultats if r["ok"])
    print("\n=== MISSIONS %d/%d | %.0f s ===" % (ok, len(resultats),
                                                 sum(r["secondes"] for r in resultats)))
    (Path(__file__).parent / "dernieres_missions.json").write_text(
        json.dumps(resultats, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if ok == len(resultats) else 1


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(principal()))
