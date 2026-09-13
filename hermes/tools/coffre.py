"""Le COFFRE : la memoire de l'agent en vrais fichiers Markdown, façon Obsidian.

🚨 POURQUOI DES FICHIERS PLUTOT QU'UNE BASE. La memoire vivait dans un SQLite : parfait pour
chercher, opaque pour tout le reste. Le patron ne pouvait pas lire ce que son agent savait,
ni le corriger, ni le relier. Ici, chaque note est un `.md` avec son en-tete YAML et ses
`[[liens]]` : le dossier s'ouvre tel quel dans Obsidian, avec la vue en graphe et les
retroliens.

LES FICHIERS FONT FOI. SQLite n'est plus qu'un index, reconstruit a partir du disque des
qu'un fichier a bouge. Consequence voulue : le patron edite une note dans Obsidian, et
l'agent lit la version corrigee au prochain `rappelle`. L'inverse — la base qui ferait
autorite — aurait fait taire ses corrections sans rien dire.

Les liens s'ecrivent `[[Titre d'une autre note]]`, comme dans Obsidian. Un lien vers une note
qui n'existe pas encore n'est pas une erreur : c'est une intention, et Obsidian l'affiche en
pointille.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path

#: Nom du dossier, dans le workspace. C'est celui que le patron ouvre dans Obsidian.
DOSSIER = "coffre"

#: `[[Titre]]` ou `[[Titre|texte affiche]]`, comme dans Obsidian.
LIEN = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]*)?\]\]")

_INTERDITS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def dossier(workspace: Path) -> Path:
    chemin = workspace / DOSSIER
    chemin.mkdir(parents=True, exist_ok=True)
    return chemin


def nom_de_fichier(titre: str) -> str:
    """Un nom de fichier lisible, proche du titre — c'est ce qu'Obsidian affiche.

    On garde les espaces et les accents : une note s'appelle « Voix Vivienne », pas
    « voix-vivienne-2f3a ». Seuls les caracteres que Windows refuse sont remplaces.
    """
    propre = _INTERDITS.sub("-", titre).strip().strip(".")
    propre = re.sub(r"\s+", " ", propre)[:120]
    return (propre or "note") + ".md"


def _sans_accents(texte: str) -> str:
    plie = unicodedata.normalize("NFD", texte.lower())
    return "".join(c for c in plie if unicodedata.category(c) != "Mn")


def meme_titre(a: str, b: str) -> bool:
    """Deux titres designent-ils la meme note ?

    Obsidian ignore la casse ; on ignore aussi les accents, sans quoi `[[Voix Vivienne]]`
    ecrit sans accent ne retrouverait jamais « Voix Vivienne » — et le lien resterait mort
    sans que personne ne le remarque.
    """
    return _sans_accents(a).strip() == _sans_accents(b).strip()


def liens_sortants(corps: str) -> list[str]:
    """Les titres cites en [[...]], dans l'ordre, sans doublon."""
    vus, sortie = set(), []
    for brut in LIEN.findall(corps or ""):
        titre = brut.strip()
        cle = _sans_accents(titre)
        if titre and cle not in vus:
            vus.add(cle)
            sortie.append(titre)
    return sortie


# --------------------------------------------------------------------------- #
# Lecture et ecriture
# --------------------------------------------------------------------------- #

def _echappe_yaml(valeur: str) -> str:
    return '"%s"' % valeur.replace("\\", "\\\\").replace('"', '\\"')


def ecris(workspace: Path, titre: str, contenu: str, source: str = "",
          etiquettes: str = "") -> Path:
    """Ecrit ou remplace une note. Rend le chemin du fichier."""
    chemin = dossier(workspace) / nom_de_fichier(titre)
    liste = [e.strip() for e in (etiquettes or "").replace(";", ",").split(",") if e.strip()]
    entete = ["---", "titre: %s" % _echappe_yaml(titre)]
    if liste:
        entete.append("etiquettes: [%s]" % ", ".join(_echappe_yaml(e) for e in liste))
    if source:
        entete.append("source: %s" % _echappe_yaml(source))
    entete += ["pose: %s" % date.today().isoformat(), "---", ""]
    chemin.write_text("\n".join(entete) + contenu.strip() + "\n", encoding="utf-8")
    return chemin


def lis(chemin: Path) -> dict:
    """Rend {titre, contenu, etiquettes, source, pose, chemin} depuis un `.md`.

    L'en-tete YAML est lu a la main : y ajouter une dependance pour six champs serait
    disproportionne, et cela permet de survivre a un en-tete abime par une edition manuelle
    — ce qui arrivera, puisque le patron edite ces fichiers.
    """
    brut = chemin.read_text(encoding="utf-8", errors="replace")
    entete, corps = {}, brut
    if brut.startswith("---"):
        fin = brut.find("\n---", 3)
        if fin != -1:
            for ligne in brut[3:fin].splitlines():
                if ":" in ligne:
                    cle, _, valeur = ligne.partition(":")
                    entete[cle.strip()] = valeur.strip().strip('"')
            corps = brut[fin + 4:].lstrip("\n")
    etiquettes = entete.get("etiquettes", "").strip("[]")
    return {
        "titre": entete.get("titre") or chemin.stem,
        "contenu": corps.strip(),
        "etiquettes": ", ".join(e.strip().strip('"') for e in etiquettes.split(",") if e.strip()),
        "source": entete.get("source", ""),
        "pose": entete.get("pose", ""),
        "chemin": chemin,
    }


def toutes(workspace: Path) -> list[dict]:
    return [lis(f) for f in sorted(dossier(workspace).glob("*.md"))]


def trouve(workspace: Path, titre: str) -> dict | None:
    direct = dossier(workspace) / nom_de_fichier(titre)
    if direct.exists():
        return lis(direct)
    for note in toutes(workspace):
        if meme_titre(note["titre"], titre):
            return note
    return None


def supprime(workspace: Path, titre: str) -> bool:
    note = trouve(workspace, titre)
    if note is None:
        return False
    note["chemin"].unlink(missing_ok=True)
    return True


# --------------------------------------------------------------------------- #
# Les liens, dans les deux sens
# --------------------------------------------------------------------------- #

def retroliens(workspace: Path, titre: str) -> list[str]:
    """Quelles notes pointent vers celle-ci.

    C'est ce qu'un index ne donne pas : savoir qu'une decision est citee par trois autres
    notes vaut plus que la retrouver par mot-cle.
    """
    entrants = []
    for note in toutes(workspace):
        if meme_titre(note["titre"], titre):
            continue
        if any(meme_titre(cible, titre) for cible in liens_sortants(note["contenu"])):
            entrants.append(note["titre"])
    return entrants


def carte(workspace: Path) -> dict:
    """Vue d'ensemble : combien de notes, lesquelles sont orphelines, lesquelles font noeud."""
    notes = toutes(workspace)
    titres = [n["titre"] for n in notes]
    sortants = {n["titre"]: liens_sortants(n["contenu"]) for n in notes}
    entrants: dict[str, int] = {t: 0 for t in titres}
    manquants: set[str] = set()
    for cibles in sortants.values():
        for cible in cibles:
            trouve_ = next((t for t in titres if meme_titre(t, cible)), None)
            if trouve_ is None:
                manquants.add(cible)
            else:
                entrants[trouve_] += 1
    orphelines = [t for t in titres if entrants[t] == 0 and not sortants[t]]
    noeuds = sorted(titres, key=lambda t: entrants[t] + len(sortants[t]), reverse=True)
    return {
        "notes": len(notes),
        "liens": sum(len(v) for v in sortants.values()),
        "orphelines": orphelines,
        "manquantes": sorted(manquants),
        "noeuds": [(t, entrants[t] + len(sortants[t])) for t in noeuds[:5]],
    }
