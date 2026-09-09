"""Preuve de vie, et surveillance du polling.

Le pire symptome d'un bot Telegram n'est pas le plantage : c'est le silence.
Le processus tourne, la memoire est saine, mais la boucle de reception s'est
arretee — et personne ne l'apprend avant d'ecrire un message qui reste sans
reponse. Ce module rend cet etat detectable, de l'interieur comme de
l'exterieur.

Deux mecanismes complementaires :

* un *battement* ecrit dans un fichier, que le conteneur, systemd ou
  ``hermes health`` peuvent lire sans rien connaitre du processus ;
* une *surveillance* interne qui constate l'arret de la boucle de reception et
  demande un redemarrage plutot que de laisser le service sourd.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

#: Intervalle entre deux battements.
INTERVALLE = 30.0

#: Au-dela, on considere le service muet. Trois battements manques : de quoi
#: encaisser une pause du systeme sans crier au loup.
LIMITE = 120.0


class Heartbeat:
    """Horodatage du dernier signe de vie, dans un fichier."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def battre(self) -> None:
        # Ecriture atomique : un lecteur ne doit jamais tomber sur un fichier
        # a moitie ecrit et en conclure que le service est mort.
        provisoire = self.path.with_suffix(".tmp")
        provisoire.write_text(str(int(time.time())), encoding="utf-8")
        provisoire.replace(self.path)

    def age(self) -> float | None:
        """Secondes depuis le dernier battement, ou ``None`` s'il n'y en a pas."""
        try:
            return max(0.0, time.time() - int(self.path.read_text(encoding="utf-8").strip()))
        except (OSError, ValueError):
            return None

    def vivant(self, limite: float = LIMITE) -> bool:
        age = self.age()
        return age is not None and age <= limite


async def surveiller(
    heartbeat: Heartbeat,
    en_ecoute: Callable[[], bool],
    sur_panne: Callable[[], None],
    intervalle: float = INTERVALLE,
) -> None:
    """Bat regulierement, et alerte si la boucle de reception s'est arretee.

    ``en_ecoute`` est evalue *apres* le premier intervalle : au demarrage, la
    boucle n'est pas encore lancee, et la surveillance ne doit pas prendre ce
    delai normal pour une panne.
    """
    heartbeat.battre()
    while True:
        await asyncio.sleep(intervalle)
        heartbeat.battre()
        if not en_ecoute():
            log.critical(
                "La boucle de reception Telegram s'est arretee alors que le service "
                "tourne toujours : redemarrage demande."
            )
            sur_panne()
            return
