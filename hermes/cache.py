"""Cache local des recherches web, et espacement des appels reseau.

🚨 POURQUOI CE MODULE EXISTE. La recherche de l'agent tient sur UN SEUL moteur solide : sur
4 requetes, bing a rendu 4 fois, yandex 3, brave 1 et duckduckgo **zero** — ce dernier s'etant
effondre sous mes propres essais, la limitation etant cumulative par adresse IP. Or la machine
n'a qu'une IP fixe et aucun proxy.

On ne peut donc pas repartir la charge : on ne peut que **consommer moins**. C'est exactement
ce que fait ce module, et c'est une parade a la CAUSE, pas au symptome :

  - le cache evite de redemander ce qu'on a deja demande (une conversation revient souvent
    sur le meme sujet en quelques minutes) ;
  - l'espacement empeche les rafales, qui sont ce qui declenche la limitation.

⚠️ Ce n'est pas un contournement de protection : c'est l'inverse. On interroge moins souvent.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)

#: Duree de vie d'une reponse. Une journee : au-dela, l'actualite a pu bouger.
TTL = 24 * 3600
#: Delai minimal entre deux appels reseau au MEME moteur, plus une gigue aleatoire.
#: Sans gigue, un rythme parfaitement regulier est lui-meme un signal de robot.
ESPACEMENT = 2.5
GIGUE = 1.5

_verrou = asyncio.Lock()
_dernier: dict[str, float] = {}


def _cle(requete: str, limite: int) -> str:
    """Normalise avant de hacher : « Reglement UE 432 » et « reglement  ue 432 » sont la
    meme question, et doivent partager la meme entree."""
    norme = re.sub(r"\s+", " ", requete.strip().lower())
    return hashlib.sha256(f"{norme}|{limite}".encode()).hexdigest()


class CacheRecherche:
    """Table SQLite unique, ouverte a la demande. Aucun serveur, aucun demon."""

    def __init__(self, chemin: Path) -> None:
        self.chemin = Path(chemin)
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        with self._co() as co:
            co.execute(
                "CREATE TABLE IF NOT EXISTS recherches ("
                " cle TEXTE PRIMARY KEY, requete TEXT, moteur TEXT,"
                " resultats TEXT, pose REAL)".replace("TEXTE", "TEXT")
            )
            co.execute("CREATE INDEX IF NOT EXISTS i_pose ON recherches(pose)")

    def _co(self) -> sqlite3.Connection:
        co = sqlite3.connect(self.chemin, timeout=10)
        co.execute("PRAGMA journal_mode=WAL")
        return co

    def lit(self, requete: str, limite: int) -> tuple[list[dict], str] | None:
        cle = _cle(requete, limite)
        with self._co() as co:
            ligne = co.execute(
                "SELECT resultats, moteur, pose FROM recherches WHERE cle=?", (cle,)
            ).fetchone()
        if not ligne:
            return None
        resultats, moteur, pose = ligne
        if time.time() - pose > TTL:
            return None            # perime : on laissera la prochaine ecriture l'ecraser
        try:
            return json.loads(resultats), moteur
        except json.JSONDecodeError:
            return None

    def ecrit(self, requete: str, limite: int, resultats: list[dict], moteur: str) -> None:
        if not resultats:
            return                 # 🚨 ne JAMAIS cacher un echec : on figerait une panne
        with self._co() as co:
            co.execute(
                "INSERT OR REPLACE INTO recherches VALUES (?,?,?,?,?)",
                (_cle(requete, limite), requete, moteur,
                 json.dumps(resultats, ensure_ascii=False), time.time()),
            )

    def purge(self, avant: float | None = None) -> int:
        limite = (avant if avant is not None else time.time() - TTL * 7)
        with self._co() as co:
            cur = co.execute("DELETE FROM recherches WHERE pose < ?", (limite,))
            return cur.rowcount

    def compte(self) -> int:
        with self._co() as co:
            return co.execute("SELECT COUNT(*) FROM recherches").fetchone()[0]


async def patiente(moteur: str) -> float:
    """Attend, si besoin, avant d'interroger `moteur`. Renvoie l'attente observee.

    🚨 Le verrou est indispensable : le modele emet souvent plusieurs recherches dans le
    meme tour, executees en parallele. Sans lui, elles partiraient toutes en meme temps —
    exactement la rafale que l'espacement doit empecher.
    """
    async with _verrou:
        creux = _dernier.get(moteur, 0.0)
        attente = creux + ESPACEMENT + random.uniform(0, GIGUE) - time.monotonic()
        if attente > 0:
            await asyncio.sleep(attente)
        else:
            attente = 0.0
        _dernier[moteur] = time.monotonic()
    return attente
