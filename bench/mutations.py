# -*- coding: utf-8 -*-
"""Controle de MUTATION : les tests mordent-ils vraiment ?

🚨 UN TEST VERT NE PROUVE RIEN TANT QU'ON N'A PAS VU QU'IL SAIT ROUGIR. Le 12/09, la garde
qui empeche de tuer Chrome etait ecrite, testee, et... jamais appelee : la fonction existait,
le handler ne l'invoquait pas. Tous les tests passaient. C'est en la desactivant exprES qu'on
a vu qu'ils ne la traversaient pas.

Ce banc desactive chaque garde-fou a tour de role et exige que les tests correspondants
ECHOUENT. Si une mutation laisse tout vert, la protection n'est pas reellement eprouvee.

⚠️ Les cibles ont ete choisies INOFFENSIVES : quand une garde est desactivee, la commande du
test s'execute pour de vrai. C'est ainsi que mes propres tests ont tue Chrome deux fois.

Usage : python bench/mutations.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
PY = RACINE / "venv" / "Scripts" / "python.exe"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: (nom, fichier, texte d'origine, texte mute, fichier de tests attendu rouge)
MUTATIONS = [
    ("arret de processus", "hermes/tools/shell.py",
     "    refus = _cible_protegee(command, ctx.workspace)",
     "    refus = None  # MUTATION",
     "tests/test_processus_proteges.py"),

    ("resolution du PID", "hermes/tools/shell.py",
     "    for brut in set(_CHIFFRES.findall(commande)):",
     "    for brut in []:  # MUTATION",
     "tests/test_processus_proteges.py"),

    ("port 9222 reserve", "hermes/tools/shell.py",
     "    refus_port = _port_reserve(bas)",
     "    refus_port = None  # MUTATION",
     "tests/test_navigateur.py"),

    ("pilotage direct du navigateur", "hermes/tools/shell.py",
     "    refus_pilotage = _pilotage_direct(bas)",
     "    refus_pilotage = None  # MUTATION",
     "tests/test_navigateur.py"),

    ("clic sur site connecte", "hermes/tools/navigateur.py",
     "    domaine = _sensible(page.url)",
     "    domaine = \"\"  # MUTATION",
     "tests/test_navigateur.py"),

    ("schemas d'URL interdits", "hermes/tools/navigateur.py",
     "    if not url.lower().startswith(_SCHEMAS):",
     "    if False:  # MUTATION",
     "tests/test_navigateur.py"),

    ("caviardage des secrets", "hermes/tools/__init__.py",
     "    signature = _signature_env()",
     "    signature = ()  # MUTATION",
     "tests/test_caviardage.py"),

    ("fenetres interdites (souris)", "mains/mains.py",
     "    raison = FENETRES_INTERDITES.get(programme)",
     "    raison = None  # MUTATION",
     "tests/test_ecran.py"),

    ("titres interdits (souris)", "mains/mains.py",
     "    for mot in TITRES_INTERDITS:",
     "    for mot in []:  # MUTATION",
     "tests/test_ecran.py"),

    ("modeles de vision decensures", "hermes/tools/vision.py",
     'MODELES = ("gemma-4-uncensored", "venice-uncensored-1-2")',
     'MODELES = ("mistral-small-3-2-24b-instruct",)  # MUTATION',
     "tests/test_vision.py"),

    ("langue de transcription", "hermes/tools/oreille.py",
     'LANGUE_PAR_DEFAUT = os.environ.get("HERMES_LANGUE_AUDIO") or "fr"',
     'LANGUE_PAR_DEFAUT = "en"  # MUTATION',
     "tests/test_oreille.py"),

    ("autoreload du noyau", "hermes/tools/noyau.py",
     '    _PREPARATION = "%load_ext autoreload\\n%autoreload 2\\n"',
     '    _PREPARATION = "pass\\n"  # MUTATION',
     "tests/test_noyau.py"),
]


def joue(chemin: str, avant: str, apres: str, tests: str) -> tuple[bool, str]:
    """Desactive une garde, lance ses tests, et RESTITUE le fichier a l'octet pres.

    🚨 EN OCTETS, PAS EN TEXTE. Premiere version : `read_text` puis `write_text`. Python lit
    alors en mode universel — les retours chariot Windows deviennent des sauts de ligne
    simples — et reecrit en \\n. Resultat : quatre fichiers sources « modifies » apres une
    execution qui n'etait censee rien laisser, et un diff illisible de plusieurs centaines de
    lignes. Un banc qui abime le depot qu'il controle est pire qu'inutile.
    """
    fichier = RACINE / chemin
    source = fichier.read_bytes()
    motif = avant.encode("utf-8")
    if motif not in source:
        return False, "motif introuvable — le code a change, la mutation est perimee"
    fichier.write_bytes(source.replace(motif, apres.encode("utf-8"), 1))
    try:
        r = subprocess.run([str(PY), "-m", "pytest", "-q", "-x", tests],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(RACINE), timeout=900)
        rouge = r.returncode != 0
        premiere = ""
        for ligne in (r.stdout or "").splitlines():
            if ligne.startswith("FAILED"):
                premiere = ligne.split(" - ")[0].replace("FAILED ", "")
                break
        return rouge, premiere or ("aucun test n'a echoue" if not rouge else "")
    finally:
        fichier.write_bytes(source)


def principal() -> int:
    print("\nControle de mutation — chaque garde est desactivee, les tests DOIVENT rougir.\n")
    resultats = []
    for nom, chemin, avant, apres, tests in MUTATIONS:
        rouge, detail = joue(chemin, avant, apres, tests)
        resultats.append(rouge)
        print("  %s %-32s %s" % ("[OK]" if rouge else "[KO]", nom, detail[:70]), flush=True)
    ok = sum(1 for r in resultats if r)
    print("\n=== MUTATIONS ATTRAPEES : %d/%d ===" % (ok, len(resultats)))
    if ok < len(resultats):
        print("Une mutation non attrapee = une protection que les tests ne traversent pas.")
    return 0 if ok == len(resultats) else 1


if __name__ == "__main__":
    raise SystemExit(principal())
