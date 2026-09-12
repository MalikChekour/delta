# -*- coding: utf-8 -*-
"""Banc de mesure de l'agent : competence et efficacite, jugees par EXECUTION.

🚨 POURQUOI CE FICHIER EST VERSIONNE. « L'agent est-il meilleur ? » n'a de sens que si on
peut le CHIFFRER avant et apres. Les deux jours d'ameliorations du 10 et 11/09 ont tous ete
valides ainsi : mesurer, changer une chose, re-mesurer. Sans cet instrument, chaque
modification du prompt redevient une question d'opinion.

🚨 CHAQUE VERDICT VIENT D'UNE EXECUTION, jamais de la lecture de la reponse : un agent sait
tres bien decrire une solution juste et livrer un fichier qui ne tourne pas. Les taches de
codage sont donc verifiees en LANCANT le code produit sur des cas qu'on choisit.

🚨 ON PASSE PAR `hermes chat`, le REPL local : il n'ouvre AUCUN poller Telegram, donc on peut
mesurer pendant que la tache planifiee tourne, sans provoquer le conflit 409.

Usage :   python bench/banc.py            (tout)
          python bench/banc.py algorithme debogage
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
RACINE = Path(__file__).resolve().parent.parent
PY = RACINE / "venv" / "Scripts" / "python.exe"
ATELIER = RACINE / "workspace"


def _execute(code: str, cwd: Path) -> tuple[int, str]:
    """Lance un bout de Python DANS l'atelier, isole du PATH et de l'encodage de la session."""
    p = subprocess.run([str(PY), "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(cwd), timeout=90,
                       env={"PYTHONIOENCODING": "utf-8", "PATH": r"C:\Windows\system32"})
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ══════════════════════════════════════════════════ taches de RAISONNEMENT
def v_calcul(_a: Path, rep: str) -> tuple[bool, str]:
    return ("448" in rep), "17*23+456/8 = 448"


def v_source(_a: Path, rep: str) -> tuple[bool, str]:
    """🚨 Le controle n'exige PAS un nombre precis. Mesure du 11/09 : les sources divergent
    reellement sur la vitamine C du persil — USDA 133, reprises de Ciqual 177, ailleurs 190,
    Aprifel 85, soit un facteur 2,2. J'avais d'abord accuse l'agent d'inventer : c'etait FAUX.
    Ce qu'on exige, c'est un chiffre plausible ACCOMPAGNE de sa source."""
    chiffre = any(n in rep for n in ("133", "177", "190", "85"))
    source = any(m in rep.lower() for m in ("http", "usda", "ciqual", "anses", "aprifel"))
    return (chiffre and source), "chiffre sourcé"


def v_famille(_a: Path, rep: str) -> tuple[bool, str]:
    return ("zingiber" in rep.lower()), "famille du curcuma"


def v_memoire(_a: Path, rep: str) -> tuple[bool, str]:
    return ("3752" in rep or "3 752" in rep), "fait retenu puis restitue"


# ══════════════════════════════════════════════════ taches de CODAGE
def v_duree(atelier: Path, _r: str = "") -> tuple[bool, str]:
    if not (atelier / "duree.py").exists():
        return False, "duree.py absent"
    rc, out = _execute(
        "import duree\n"
        "cas=[(0,'0 seconde'),(1,'1 seconde'),(45,'45 secondes'),(60,'1 minute'),"
        "(3661,'1 heure 1 minute 1 seconde'),(7200,'2 heures')]\n"
        "r=[]\n"
        "for e,a in cas:\n"
        "    try: o=duree.formate(e)\n"
        "    except Exception as x: r.append(f'{e}: LEVE {x}'); continue\n"
        "    if str(o).strip().lower()!=a: r.append(f'{e}: {o!r} != {a!r}')\n"
        "try: duree.formate(-5); r.append('-5 : aucune exception')\n"
        "except ValueError: pass\n"
        "except Exception as x: r.append(f'-5 : {type(x).__name__}')\n"
        "print('RATES:'+' | '.join(r) if r else 'JUSTE')\n", atelier)
    return ("JUSTE" in out), " ".join(out.split())[:150]


def v_bulletin(atelier: Path, _r: str = "") -> tuple[bool, str]:
    rc, out = _execute(
        "import bulletin\n"
        "r=[]\n"
        "for e,a in [([],0.0),([None,None],0.0),([10,20],15.0),([12,None,18],15.0)]:\n"
        "    try: o=bulletin.moyenne(list(e))\n"
        "    except Exception as x: r.append(f'{e}: LEVE {type(x).__name__}'); continue\n"
        "    if abs(float(o)-a)>1e-9: r.append(f'{e}: {o} != {a}')\n"
        "if bulletin.mention(15)!='bien': r.append('mention cassee')\n"
        "print('RATES:'+' | '.join(r) if r else 'JUSTE')\n", atelier)
    return ("JUSTE" in out), " ".join(out.split())[:150]


def v_renommage(atelier: Path, _r: str = "") -> tuple[bool, str]:
    restants = [f for f in ("tva.py", "facture.py", "devis.py")
                if (atelier / f).exists()
                and re.search(r"\bcalc_tva\b", (atelier / f).read_text(encoding="utf-8"))]
    if restants:
        return False, "ancien nom encore la : " + ", ".join(restants)
    rc, out = _execute("import facture; print(facture.total(100))", atelier)
    return (rc == 0 and "120" in out), " ".join(out.split())[:120]


def v_test_ecrit(atelier: Path, _r: str = "") -> tuple[bool, str]:
    if not (atelier / "test_panier.py").exists():
        return False, "test_panier.py absent"
    rc, out = _execute(
        "import panier\n"
        "r=[]\n"
        "if panier.remise(100,150)<0: r.append('bogue NON corrige')\n"
        "if abs(panier.remise(100,10)-90)>1e-9: r.append('cas normal casse')\n"
        "print('RATES:'+' | '.join(r) if r else 'JUSTE')\n", atelier)
    if "JUSTE" not in out:
        return False, " ".join(out.split())[:120]
    p = subprocess.run([str(PY), "-m", "pytest", "-q", "test_panier.py"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(atelier), timeout=150)
    return (p.returncode == 0), "le test ecrit ne passe pas"


BULLETIN = ('def moyenne(notes):\n    total = 0\n    compte = 0\n    for n in notes:\n'
            '        if n is None:\n            continue\n        total += n\n'
            '        compte += 1\n    return total / compte\n\n\n'
            'def mention(m):\n    if m >= 16:\n        return "tres bien"\n'
            '    elif m >= 14:\n        return "bien"\n    elif m >= 12:\n'
            '        return "assez bien"\n    elif m >= 10:\n        return "passable"\n'
            '    return "insuffisant"\n')

TACHES = [
    ("calcul", "raisonnement",
     "Combien font 17 * 23 + 456 / 8 ? Donne uniquement le resultat numerique.",
     v_calcul, {}),
    ("fait source", "raisonnement",
     "Quelle est la teneur en vitamine C du persil frais pour 100 g ? Cite ta source.",
     v_source, {}),
    ("lecture page", "raisonnement",
     "Va lire https://fr.wikipedia.org/wiki/Curcuma et dis-moi a quelle famille botanique "
     "appartient le curcuma.", v_famille, {}),
    ("memoire", "raisonnement",
     "Retiens que la chaine TikTok du patron s'appelle healthy.vibes.natural et compte "
     "3752 abonnes. Puis dis-moi combien d'abonnes.", v_memoire, {}),
    ("algorithme", "codage",
     "Ecris dans le workspace le fichier `duree.py` avec une fonction `formate(secondes)` "
     "rendant une duree lisible en francais : 0 -> '0 seconde', 1 -> '1 seconde', "
     "45 -> '45 secondes', 60 -> '1 minute', 3661 -> '1 heure 1 minute 1 seconde', "
     "7200 -> '2 heures' (pas d'unite nulle), -5 -> leve ValueError. Verifie en l'executant.",
     v_duree, {}),
    ("debogage", "codage",
     "Le fichier `bulletin.py` du workspace plante sur `moyenne([])` et `moyenne([None, "
     "None])` au lieu de RENDRE 0.0. Repare-le sans changer le comportement sur les listes "
     "non vides, puis verifie en l'executant.", v_bulletin, {"bulletin.py": BULLETIN}),
    ("renommage", "codage",
     "Trois fichiers du workspace utilisent une fonction `calc_tva`. Trouve-les avec ton "
     "outil de recherche de code, renomme-la `calcule_tva` PARTOUT, et verifie que "
     "`import facture; facture.total(100)` marche.", v_renommage, {
         "tva.py": "TAUX = 0.20\n\n\ndef calc_tva(m):\n    return m * TAUX\n",
         "facture.py": "from tva import calc_tva\n\n\ndef total(ht):\n    return ht + calc_tva(ht)\n",
         "devis.py": "from tva import calc_tva\n\n\ndef estime(ht):\n    return round(ht + calc_tva(ht), 2)\n",
     }),
    ("test ecrit", "codage",
     "`panier.py` a un bogue : `remise(prix, pourcent)` rend un prix NEGATIF au-dela de "
     "100 %. Ecris `test_panier.py` (pytest) qui echoue sur ce bogue, montre-le, corrige "
     "`panier.py`, puis relance pytest pour montrer qu'il passe.", v_test_ecrit,
     {"panier.py": "def remise(prix, pourcent):\n    return prix - prix * pourcent / 100\n"}),
]


def joue(consigne: str, minutes: int = 10) -> tuple[str, int, float]:
    t = time.time()
    p = subprocess.run([str(PY), "-m", "hermes", "chat"], input=consigne + "\n",
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(RACINE), timeout=minutes * 60)
    s = (p.stdout or "") + (p.stderr or "")
    etapes = len(re.findall(r"\[\d+/\d+\]", s))
    rep = s.split("hermes >")[-1] if "hermes >" in s else s
    return rep.strip(), etapes, time.time() - t


def _atelier_propre(fichiers: dict[str, str]) -> None:
    ATELIER.mkdir(exist_ok=True)
    for f in ATELIER.glob("*.py"):
        f.unlink(missing_ok=True)
    for d in ("__pycache__", ".pytest_cache"):
        shutil.rmtree(ATELIER / d, ignore_errors=True)
    for nom, contenu in fichiers.items():
        (ATELIER / nom).write_text(contenu, encoding="utf-8")


if __name__ == "__main__":
    voulues = sys.argv[1:]
    resultats = []
    for nom, famille, consigne, verif, fichiers in TACHES:
        if voulues and nom not in voulues:
            continue
        _atelier_propre(fichiers)
        try:
            rep, etapes, secondes = joue(consigne)
            ok, detail = verif(ATELIER, rep)
        except subprocess.TimeoutExpired:
            rep, etapes, secondes, ok, detail = "", 0, 600.0, False, "DELAI DEPASSE"
        except Exception as exc:  # noqa: BLE001
            rep, etapes, secondes, ok, detail = "", 0, 0.0, False, f"ERREUR {exc}"
        resultats.append(dict(nom=nom, famille=famille, ok=ok, etapes=etapes,
                              secondes=round(secondes, 1), detail=detail))
        print("  %s %-13s %-12s %2d etape(s) %6.1f s  %s"
              % ("[OK]" if ok else "[KO]", nom, famille, etapes, secondes,
                 "" if ok else detail[:80]), flush=True)

    for famille in ("raisonnement", "codage"):
        lot = [r for r in resultats if r["famille"] == famille]
        if lot:
            print("\n  %-13s %d/%d | %3.0f s | %.1f etape(s) en moyenne"
                  % (famille, sum(1 for r in lot if r["ok"]), len(lot),
                     sum(r["secondes"] for r in lot), sum(r["etapes"] for r in lot) / len(lot)))
    print("\n=== TOTAL %d/%d | %.0f s ===" % (sum(1 for r in resultats if r["ok"]),
                                              len(resultats),
                                              sum(r["secondes"] for r in resultats)))
    (Path(__file__).parent / "dernier_resultat.json").write_text(
        json.dumps(resultats, ensure_ascii=False, indent=1), encoding="utf-8")
