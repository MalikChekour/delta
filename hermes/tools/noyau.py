"""Un noyau Python qui garde son etat d'un appel a l'autre.

🚨 CE QUI MANQUAIT. L'outil `python` lancait un processus NEUF a chaque appel, executait, puis
supprimait le script. Consequence invisible mais couteuse : rien ne survit. Charger un CSV,
puis l'analyser au tour suivant, obligeait a tout recharger ; et comme le modele ne le savait
pas, il ecrivait des tours qui s'appuyaient sur des variables mortes, puis repartait de zero
en boucle. C'est exactement ce qu'Open Interpreter apporte de concret — un noyau vivant — et
cela se fait ici, sans second agent ni second modele.

Un noyau PAR CONVERSATION : l'etat persiste dans un fil, jamais entre deux fils. Une variable
laissee par une discussion sur le trading n'ira pas polluer une discussion sur les videos.

Le noyau vit dans un PROCESSUS SEPARE. Une boucle infinie ou un `MemoryError` n'emporte donc
pas le bot : on interrompt, et l'etat reste.
"""

from __future__ import annotations

import atexit
import queue
import re
import time
import warnings
from pathlib import Path
from typing import Any

#: Trois conversations actives avec un noyau chacune, pas plus. Un noyau ipykernel pese
#: environ 80 Mo ; la machine a ~13 Go libres, mais elle fait tourner MT5 et Chrome.
MAX_NOYAUX = 3
#: Au-dela, un noyau qui dort ne merite pas sa memoire.
INACTIF_MAX = 30 * 60

#: Les traces d'ipykernel arrivent colorees. En Telegram, les codes ANSI sont du bruit.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_NOYAUX: dict[str, "Noyau"] = {}

# pyzmq previent qu'il ajoute un fil selecteur pour fonctionner sur la boucle Proactor.
# C'est exactement ce qu'on veut : on garde Proactor (indispensable aux sous-processus) et
# zmq s'adapte. L'avertissement n'apporte rien, il encombre le journal.
warnings.filterwarnings("ignore", message=".*Proactor event loop does not implement.*")


class Noyau:
    """Un interpreteur Python vivant, dans son propre processus."""

    def __init__(self, workspace: Path, cle: str) -> None:
        self.workspace = workspace
        self.cle = cle
        self.km: Any = None
        self.kc: Any = None
        self.dernier = time.monotonic()
        self.tours = 0

    # -- cycle de vie -------------------------------------------------------- #

    async def demarre(self) -> None:
        from jupyter_client.manager import AsyncKernelManager

        self.km = AsyncKernelManager(kernel_name="python3")
        # 🚨 Deux bruits a etouffer, sinon ils partent dans le journal du service a chaque
        # demarrage de noyau : l'avertissement "TCP without encryption" d'ipykernel (le
        # noyau ecoute sur 127.0.0.1 uniquement, protege par la cle HMAC du fichier de
        # connexion — pas de chiffrement, mais pas d'exposition non plus), et le rappel de
        # pyzmq sur la boucle Proactor. On NE PEUT PAS basculer sur la boucle Selector pour
        # le faire taire : sous Windows, Selector ne sait pas lancer de sous-processus, et
        # l'outil `shell` en depend entierement.
        await self.km.start_kernel(cwd=str(self.workspace),
                                   extra_arguments=["--log-level=ERROR"])
        self.kc = self.km.client()
        self.kc.start_channels()
        await self.kc.wait_for_ready(timeout=60)
        await self._prepare()

    #: 🚨 SANS CECI, UNE SESSION VIVANTE REND L'AGENT AVEUGLE A SES PROPRES CORRECTIONS.
    #: Mesure du 12/09, et regression franche : la tache « ecris un test, corrige le bogue »
    #: est passee de 8/8 a 0/3. En cause, le cache d'imports de Python — l'agent lit
    #: `from panier import remise`, corrige `panier.py` sur le DISQUE, relance, et obtient
    #: toujours l'ancienne version ; il en conclut que sa correction n'a pas pris et se met
    #: a tourner en rond. Un processus neuf n'avait pas ce probleme : il n'avait pas de
    #: cache. `autoreload 2` relit les modules modifies avant chaque execution et rend la
    #: persistance compatible avec le fait de modifier du code.
    _PREPARATION = "%load_ext autoreload\n%autoreload 2\n"

    async def _prepare(self) -> None:
        msg_id = self.kc.execute(self._PREPARATION, store_history=False, silent=True)
        await self._reponse_shell(msg_id, 10.0)
        await self._draine(0.2)

    async def vivant(self) -> bool:
        try:
            return bool(self.km) and await self.km.is_alive()
        except Exception:  # noqa: BLE001
            return False

    async def arrete(self) -> None:
        try:
            if self.kc is not None:
                self.kc.stop_channels()
            if self.km is not None:
                await self.km.shutdown_kernel(now=True)
                # Sans ce menage, les sockets zmq sont ramasses APRES le demontage des
                # modules et lachent un « NoneType object is not callable » a la sortie du
                # processus. Cosmetique, mais une trace inexplicable dans le journal fait
                # perdre du temps le jour ou on y cherche une vraie panne.
                nettoyage = getattr(self.km, "cleanup_resources", None)
                if nettoyage is not None:
                    nettoyage()
        except Exception:  # noqa: BLE001 - un arret rate ne doit jamais remonter
            pass
        finally:
            self.km = self.kc = None

    # -- execution ----------------------------------------------------------- #

    async def execute(self, code: str, delai: int) -> str:
        """Execute du code et renvoie ce qu'il a affiche.

        🚨 On ne tue pas le noyau sur depassement de delai, on l'INTERROMPT. Tuer ferait
        perdre tout ce que la conversation a construit — precisement ce que ce module existe
        pour eviter. Une boucle infinie coute donc le delai, pas la session.
        """
        if not await self.vivant():
            await self.demarre()
        self.dernier = time.monotonic()
        self.tours += 1

        # On vide ce qu'une execution precedente aurait laisse trainer : sinon la sortie
        # d'hier se colle a la reponse d'aujourd'hui.
        await self._draine(0.05)

        msg_id = self.kc.execute(code, store_history=True)
        morceaux: list[str] = []
        fin = time.monotonic() + max(1, delai)
        interrompu = False
        while True:
            reste = fin - time.monotonic()
            if reste <= 0:
                interrompu = True
                try:
                    await self.km.interrupt_kernel()
                except Exception:  # noqa: BLE001
                    pass
                morceaux.extend(await self._draine(3.0, msg_id))
                break
            try:
                message = await self.kc.get_iopub_msg(timeout=min(reste, 1.0))
            except (queue.Empty, TimeoutError):
                continue
            except Exception:  # noqa: BLE001 - canal coupe : le noyau est mort
                morceaux.append("\n[le noyau s'est arrete en cours d'execution]")
                break
            fini, texte = self._lit(message, msg_id)
            if texte:
                morceaux.append(texte)
            if fini:
                break

        # 🚨 NE PAS RENDRE LA MAIN SUR LE SEUL « idle » DE IOPUB. Apres une interruption,
        # ipykernel ABANDONNE les requetes qui arrivent tant que le cycle precedent n'est pas
        # clos : l'appel suivant repartait sans rien executer, et rendait « aucune sortie »
        # alors que la variable existait. Mesure du 12/09 — `print(tresor)` muet juste apres
        # une boucle interrompue. La reponse du canal shell est le vrai signal de fin.
        statut = await self._reponse_shell(msg_id, 5.0)
        if statut == "aborted" and not interrompu:
            morceaux.clear()
            return await self.execute(code, delai)          # une seule reprise

        sortie = _ANSI.sub("", "".join(morceaux)).strip()
        if interrompu:
            entete = (
                f"[interrompu apres {delai} s — le code tournait encore. "
                f"L'etat de la session est CONSERVE : les variables deja definies sont "
                f"toujours la. Donne un `delai` plus grand, ou decoupe le travail.]"
            )
            return f"{entete}\n{sortie}" if sortie else entete
        return sortie or "(aucune sortie — pense a `print(...)` pour montrer un resultat)"

    def _lit(self, message: dict, msg_id: str) -> tuple[bool, str]:
        """Renvoie (execution terminee, texte a garder)."""
        if message.get("parent_header", {}).get("msg_id") != msg_id:
            return False, ""
        genre = message.get("msg_type", "")
        contenu = message.get("content", {})
        if genre == "stream":
            return False, contenu.get("text", "")
        if genre in ("execute_result", "display_data"):
            donnees = contenu.get("data", {})
            if "text/plain" in donnees:
                return False, donnees["text/plain"] + "\n"
            if "image/png" in donnees:
                # Le modele ne voit pas les images. On le dit, au lieu de deverser du base64.
                return False, ("[une image a ete produite ; enregistre-la dans un fichier "
                               "avec savefig() pour que le patron puisse la recuperer]\n")
            return False, ""
        if genre == "error":
            return False, "\n".join(contenu.get("traceback", [])) + "\n"
        if genre == "status" and contenu.get("execution_state") == "idle":
            return True, ""
        return False, ""

    async def _reponse_shell(self, msg_id: str, duree: float) -> str:
        """Attend l'accuse de reception de l'execution, et renvoie son statut.

        Tant qu'il n'est pas arrive, le noyau n'est pas pret a recevoir la requete suivante.
        """
        fin = time.monotonic() + duree
        while time.monotonic() < fin:
            try:
                message = await self.kc.get_shell_msg(timeout=0.2)
            except (queue.Empty, TimeoutError):
                continue
            except Exception:  # noqa: BLE001
                return ""
            if message.get("parent_header", {}).get("msg_id") == msg_id:
                return str(message.get("content", {}).get("status", ""))
        return ""

    async def _draine(self, duree: float, msg_id: str = "") -> list[str]:
        """Ramasse ce qui reste sur le canal pendant `duree` secondes."""
        restes: list[str] = []
        fin = time.monotonic() + duree
        while time.monotonic() < fin:
            try:
                message = await self.kc.get_iopub_msg(timeout=0.1)
            except Exception:  # noqa: BLE001
                break
            if msg_id:
                fini, texte = self._lit(message, msg_id)
                if texte:
                    restes.append(texte)
                if fini:
                    break
        return restes


# --------------------------------------------------------------------------- #
# Gestion des noyaux
# --------------------------------------------------------------------------- #

def disponible() -> bool:
    """Le noyau persistant est-il installable ici ?

    🚨 S'il ne l'est pas, l'outil `python` doit continuer a marcher comme avant. Une
    amelioration qui casse la fonction de base est une regression.
    """
    try:
        import jupyter_client  # noqa: F401
        import ipykernel  # noqa: F401
    except ImportError:
        return False
    return True


async def _fais_de_la_place() -> None:
    """Ferme les noyaux endormis, puis le plus ancien s'il en reste trop."""
    maintenant = time.monotonic()
    for cle, noyau in list(_NOYAUX.items()):
        if maintenant - noyau.dernier > INACTIF_MAX:
            await noyau.arrete()
            _NOYAUX.pop(cle, None)
    while len(_NOYAUX) >= MAX_NOYAUX:
        cle = min(_NOYAUX, key=lambda c: _NOYAUX[c].dernier)
        await _NOYAUX.pop(cle).arrete()


async def execute(workspace: Path, chat_id: int, code: str, delai: int,
                  nouveau: bool = False) -> str:
    """Execute `code` dans le noyau de cette conversation, en le creant au besoin."""
    cle = f"{workspace}|{chat_id}"
    if nouveau and cle in _NOYAUX:
        await _NOYAUX.pop(cle).arrete()
    noyau = _NOYAUX.get(cle)
    if noyau is None:
        await _fais_de_la_place()
        noyau = Noyau(workspace, cle)
        await noyau.demarre()
        _NOYAUX[cle] = noyau
    sortie = await noyau.execute(code, delai)
    if nouveau:
        return "[session Python repartie de zero]\n" + sortie
    return sortie


async def arrete_tout() -> None:
    """Ferme tous les noyaux. Pour les tests et l'arret du service."""
    for cle in list(_NOYAUX):
        await _NOYAUX.pop(cle).arrete()


def combien() -> int:
    return len(_NOYAUX)


def _menage_final() -> None:
    """Ferme les canaux zmq AVANT le demontage de l'interpreteur.

    Sans cela, les sockets sont ramasses apres que Python a vide les modules, et chaque
    sortie de processus se termine par « TypeError: 'NoneType' object is not callable » dans
    `zmq/_future.py`. C'est inoffensif, mais une trace inexpliquee dans un journal fait
    perdre du temps le jour ou on y cherche une vraie panne — et elle noyait la sortie des
    controles.
    """
    for noyau in list(_NOYAUX.values()):
        try:
            if noyau.kc is not None:
                noyau.kc.stop_channels()
        except Exception:  # noqa: BLE001 - on est en train de mourir, rien ne doit remonter
            pass


atexit.register(_menage_final)
