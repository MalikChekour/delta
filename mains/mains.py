# -*- coding: utf-8 -*-
r"""Les MAINS de l'agent : ecran, souris et clavier, dans la session du patron.

🚨 POURQUOI UN SERVICE SEPARE, ET PAS UN OUTIL DE PLUS DANS L'AGENT.
L'agent tourne en tache planifiee sous SYSTEM, donc en session 0. Le bureau, Chrome et
MetaTrader vivent en session 2. L'isolement de session de Windows est etanche : depuis la
session 0, `GetForegroundWindow()` rend 0, et une capture d'ecran leve « screen grab failed ».
Aucune bibliotheque n'y changera rien — ce n'est pas un manque d'outil, c'est une frontiere
du systeme.

Ce programme vit donc DANS la session interactive et ouvre un guichet local que l'agent
appelle. Trois consequences voulues :
  - l'agent reste en session 0, isole et demarre au boot, comme avant ;
  - la capacite dangereuse est confinee dans un processus a part, que le patron demarre et
    arrete quand il veut. Pas de mains lancees = pas de mains, quoi que demande l'agent ;
  - les garde-fous vivent ICI, au niveau ou l'action se produit. Les regles du navigateur
    (lire oui, cliquer non) seraient contournees par un simple clic physique : la meme regle
    doit donc exister a hauteur de souris.

Lancement, dans la session du patron :  python mains\mains.py
Le jeton d'acces est ecrit dans `.mains_jeton` a la racine du projet.
"""

from __future__ import annotations

import base64
import ctypes
import io
import json
import os
import secrets
import sys
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
JETON_FICHIER = RACINE / ".mains_jeton"

#: Au-dessus de 9300, comme l'exige la note machine : 9222 est le Chrome de publication,
#: 22346 le terminal MT5, 18789 et 18791 node.
#: Surchargeable par MAINS_PORT, pour que les tests montent un vrai guichet sur un port
#: libre sans toucher a celui de production.
PORT = int(os.environ.get("MAINS_PORT") or 9340)

#: 🚨 Programmes dans lesquels l'agent ne clique ni ne tape JAMAIS.
#: `chrome.exe` : c'est la ou se publient TikTok et YouTube. Ses outils navigateur lui
#: interdisent deja de cliquer sur ces sites ; un clic physique contournerait la regle.
#: `terminal64.exe` : MetaTrader. Un clic mal place y passe un ordre reel.
FENETRES_INTERDITES = {
    "chrome.exe": "c'est la ou se publient TikTok et YouTube ; un clic peut publier un "
                  "brouillon, supprimer une video ou fermer la session",
    "msedge.exe": "c'est un navigateur, il peut porter une session du patron",
    "firefox.exe": "c'est un navigateur, il peut porter une session du patron",
    "terminal64.exe": "c'est MetaTrader 5 ; un clic peut passer un ordre reel",
    "metaeditor64.exe": "c'est l'editeur de MetaTrader 5",
    "hermes.exe": "c'est l'autre agent, celui du trading",
    "mstsc.exe": "c'est une session distante ouverte sur une autre machine",
    # 🚨 Une console porte souvent un programme qui tourne : le bot de trading, un watchdog,
    # un montage video. Un clic suivi d'une frappe y envoie un Ctrl-C et l'arrete. L'agent
    # n'a aucune raison de cliquer dans une console : il a `shell` et `python` pour cela.
    "python.exe": "c'est une console Python — peut-etre le bot de trading, peut-etre moi",
    "pythonw.exe": "c'est un programme Python qui tourne",
    "cmd.exe": "c'est une console ; une frappe mal placee arrete ce qui y tourne",
    "powershell.exe": "c'est une console ; une frappe mal placee arrete ce qui y tourne",
    "windowsterminal.exe": "c'est une console ; une frappe mal placee arrete ce qui y tourne",
    "antigravity.exe": "le patron a demande a ne jamais fermer ce programme",
}

#: 🚨 DEUXIEME COUCHE, PAR LE TITRE. Le nom du programme ne suffit pas : TikTok Studio ouvert
#: dans un autre navigateur, ou MetaTrader lance sous un autre nom d'executable, passeraient
#: la premiere liste. Consigne du patron, mot pour mot : ne pas toucher a Chrome, TikTok,
#: YouTube ni MetaTrader. On la fait donc tenir sur ce que la fenetre AFFICHE, pas seulement
#: sur ce qu'elle EST.
TITRES_INTERDITS = (
    "tiktok", "youtube", "metatrader", "mt5", "redbubble", "pinterest",
    "trading", "studio", "telegram",
)

#: Raccourcis qu'on n'envoie pas, meme sur une fenetre autorisee.
#: `win+l` verrouille la session : le bureau cesse d'etre rendu, et les mains se coupent
#: elles-memes — sur un serveur distant, c'est aussi un bon moyen de s'enfermer dehors.
TOUCHES_INTERDITES = {
    "win+l": "cela verrouille la session et coupe le bureau",
    "ctrl+alt+suppr": "Windows ne le permet pas a un programme, et cela ouvrirait l'ecran "
                      "de securite",
    "ctrl+alt+delete": "idem",
    "alt+f4": "cela ferme la fenetre au premier plan, quelle qu'elle soit",
}

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)


# --------------------------------------------------------------------------- #
# Fenetres : savoir sur quoi on est sur le point d'agir
# --------------------------------------------------------------------------- #

def _programme_du_pid(pid: int) -> str:
    if not pid:
        return ""
    poignee = k32.OpenProcess(0x1000, False, pid)      # QUERY_LIMITED_INFORMATION
    if not poignee:
        return ""
    try:
        taille = wintypes.DWORD(260)
        tampon = ctypes.create_unicode_buffer(260)
        if not k32.QueryFullProcessImageNameW(poignee, 0, tampon, ctypes.byref(taille)):
            return ""
        return tampon.value.rsplit("\\", 1)[-1].lower()
    finally:
        k32.CloseHandle(poignee)


def _infos_fenetre(hwnd: int) -> dict:
    if not hwnd:
        return {"hwnd": 0, "programme": "", "titre": ""}
    pid = wintypes.DWORD()
    u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    longueur = u32.GetWindowTextLengthW(hwnd)
    titre = ctypes.create_unicode_buffer(longueur + 1)
    u32.GetWindowTextW(hwnd, titre, longueur + 1)
    return {"hwnd": int(hwnd), "programme": _programme_du_pid(pid.value),
            "titre": titre.value}


def fenetre_active() -> dict:
    return _infos_fenetre(u32.GetForegroundWindow())


def fenetre_en(x: int, y: int) -> dict:
    """La fenetre reellement sous ce point — pas celle qui a le focus.

    🚨 Un clic n'atteint pas forcement la fenetre active : il atteint celle qui est sous le
    curseur. Verifier le premier plan seulement laisserait cliquer dans MetaTrader par-dessus
    l'epaule d'une fenetre autorisee.
    """
    point = wintypes.POINT(x, y)
    return _infos_fenetre(u32.WindowFromPoint(point))


def bureau_rendu() -> bool:
    """Y a-t-il un bureau a regarder ?

    Sur un serveur, la session RDP deconnectee n'a PAS de bureau rendu : la capture echoue et
    aucune fenetre n'existe. Ce n'est pas une panne, c'est l'etat normal quand personne n'est
    connecte — et il faut le dire clairement plutot que de rendre une image noire.
    """
    return bool(u32.GetForegroundWindow())


def _verifie_cible(fenetre: dict, quoi: str) -> None:
    """Deux couches : ce que la fenetre EST, puis ce qu'elle AFFICHE."""
    programme = fenetre.get("programme", "")
    raison = FENETRES_INTERDITES.get(programme)
    if raison:
        raise Refus(f"{quoi} refuse : la fenetre visee appartient a « {programme} », et "
                    f"{raison}. Je peux la REGARDER, pas agir dedans.")
    titre = (fenetre.get("titre") or "").lower()
    for mot in TITRES_INTERDITS:
        if mot in titre:
            raise Refus(
                f"{quoi} refuse : le titre de la fenetre contient « {mot} », donc elle touche "
                f"a la publication ou au trading du patron. Consigne explicite : ne pas y "
                f"toucher. Je peux la REGARDER et la decrire, pas agir dedans."
            )


class Refus(Exception):
    """Une action interdite par les garde-fous."""


# --------------------------------------------------------------------------- #
# Souris et clavier : SendInput, sans dependance
# --------------------------------------------------------------------------- #

class _SOURIS(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _CLAVIER(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]


class _UNION(ctypes.Union):
    _fields_ = [("souris", _SOURIS), ("clavier", _CLAVIER)]


class _ENTREE(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]


ENTREE_SOURIS, ENTREE_CLAVIER = 0, 1
CLIC = {"gauche": (0x0002, 0x0004), "droit": (0x0008, 0x0010), "milieu": (0x0020, 0x0040)}
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004

#: Les touches nommees qu'on accepte. Volontairement court : ce qui sert a remplir un
#: formulaire ou naviguer, rien qui touche au systeme.
TOUCHES = {
    "entree": 0x0D, "enter": 0x0D, "tab": 0x09, "echap": 0x1B, "esc": 0x1B,
    "retour": 0x08, "backspace": 0x08, "suppr": 0x2E, "delete": 0x2E,
    "espace": 0x20, "space": 0x20, "haut": 0x26, "bas": 0x28,
    "gauche": 0x25, "droite": 0x27, "debut": 0x24, "fin": 0x23,
    "pagehaut": 0x21, "pagebas": 0x22,
    "ctrl": 0x11, "alt": 0x12, "maj": 0x10, "shift": 0x10, "win": 0x5B,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "a": 0x41, "c": 0x43, "v": 0x56, "x": 0x58, "z": 0x5A, "s": 0x53,
}


def _envoie(entrees: list) -> None:
    tableau = (_ENTREE * len(entrees))(*entrees)
    u32.SendInput(len(entrees), tableau, ctypes.sizeof(_ENTREE))


def _entree_souris(flags: int, data: int = 0) -> _ENTREE:
    return _ENTREE(type=ENTREE_SOURIS,
                   u=_UNION(souris=_SOURIS(0, 0, data, flags, 0, None)))


def _entree_touche(vk: int, monte: bool = False) -> _ENTREE:
    return _ENTREE(type=ENTREE_CLAVIER,
                   u=_UNION(clavier=_CLAVIER(vk, 0, KEYEVENTF_KEYUP if monte else 0, 0, None)))


def _entree_caractere(car: str, monte: bool = False) -> _ENTREE:
    drapeaux = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if monte else 0)
    return _ENTREE(type=ENTREE_CLAVIER,
                   u=_UNION(clavier=_CLAVIER(0, ord(car), drapeaux, 0, None)))


def bouge(x: int, y: int) -> None:
    u32.SetCursorPos(int(x), int(y))


def position() -> tuple[int, int]:
    p = wintypes.POINT()
    u32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def clique(x: int, y: int, bouton: str = "gauche", double: bool = False) -> dict:
    cible = fenetre_en(x, y)
    _verifie_cible(cible, "clic")
    bas, haut = CLIC.get(bouton, CLIC["gauche"])
    bouge(x, y)
    time.sleep(0.05)
    for _ in range(2 if double else 1):
        _envoie([_entree_souris(bas), _entree_souris(haut)])
        time.sleep(0.06)
    return cible


def defile(crans: int) -> None:
    _envoie([_entree_souris(0x0800, crans * 120)])          # MOUSEEVENTF_WHEEL


def tape(texte: str) -> dict:
    """Saisit du texte au clavier, caractere par caractere, en Unicode.

    🚨 On passe par KEYEVENTF_UNICODE et non par les codes de touche : la disposition du
    clavier de la machine est inconnue, et « a » sur un clavier QWERTY devient « q » sur un
    AZERTY. Le mode Unicode ignore la disposition — c'est la seule facon d'ecrire du francais
    accentue de maniere fiable.
    """
    cible = fenetre_active()
    _verifie_cible(cible, "saisie")
    for car in texte:
        _envoie([_entree_caractere(car), _entree_caractere(car, monte=True)])
        time.sleep(0.012)
    return cible


def touche(combinaison: str) -> dict:
    cible = fenetre_active()
    _verifie_cible(cible, "touche")
    propre = combinaison.strip().lower().replace(" ", "")
    interdit = TOUCHES_INTERDITES.get(propre)
    if interdit:
        raise Refus(f"touche refusee : « {combinaison} » n'est pas permise, {interdit}.")
    morceaux = [m for m in propre.split("+") if m]
    codes = []
    for m in morceaux:
        if m not in TOUCHES:
            raise Refus(f"touche inconnue : « {m} ». Connues : {', '.join(sorted(TOUCHES))}.")
        codes.append(TOUCHES[m])
    _envoie([_entree_touche(c) for c in codes]
            + [_entree_touche(c, monte=True) for c in reversed(codes)])
    return cible


def capture(zone: tuple | None = None) -> bytes:
    from PIL import ImageGrab

    image = ImageGrab.grab(bbox=zone)
    tampon = io.BytesIO()
    image.save(tampon, format="PNG")
    return tampon.getvalue()


async def _ocr(png: bytes, langue: str = "fr-FR") -> dict:
    from winsdk.windows.globalization import Language
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

    flux = InMemoryRandomAccessStream()
    plume = DataWriter(flux.get_output_stream_at(0))
    plume.write_bytes(png)
    await plume.store_async()
    decodeur = await BitmapDecoder.create_async(flux)
    bitmap = await decodeur.get_software_bitmap_async()
    moteur = (OcrEngine.try_create_from_language(Language(langue))
              or OcrEngine.try_create_from_user_profile_languages())
    if moteur is None:
        return {"erreur": "aucun moteur OCR installe"}
    resultat = await moteur.recognize_async(bitmap)
    lignes = []
    for ligne in resultat.lines:
        mots = list(ligne.words)
        if not mots:
            continue
        # 🚨 LE TEXTE SEUL NE SERT A RIEN. Pour cliquer sur « Publier », l'agent a besoin de
        # SAVOIR OU EST « Publier ». On rend donc, pour chaque ligne, le centre de sa boite :
        # ce sont ces coordonnees qu'il passera ensuite a l'outil souris.
        gauche = min(m.bounding_rect.x for m in mots)
        haut = min(m.bounding_rect.y for m in mots)
        droite = max(m.bounding_rect.x + m.bounding_rect.width for m in mots)
        bas = max(m.bounding_rect.y + m.bounding_rect.height for m in mots)
        lignes.append({"texte": ligne.text,
                       "x": int((gauche + droite) / 2), "y": int((haut + bas) / 2)})
    return {"langue": moteur.recognizer_language.language_tag, "lignes": lignes}


def lis_l_ecran(zone: tuple | None = None, langue: str = "fr-FR") -> dict:
    import asyncio

    png = capture(zone)
    lu = asyncio.run(_ocr(png, langue))
    if zone and "lignes" in lu:
        # Les coordonnees sortent relatives a la zone capturee ; l'agent, lui, clique en
        # coordonnees d'ecran. On les remet dans le bon repere tout de suite, sinon il
        # cliquera systematiquement a cote.
        for ligne in lu["lignes"]:
            ligne["x"] += int(zone[0])
            ligne["y"] += int(zone[1])
    lu["png"] = png
    return lu


# --------------------------------------------------------------------------- #
# Guichet local
# --------------------------------------------------------------------------- #

JETON = ""


class Guichet(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:          # pas de journal a chaque appel
        pass

    def _repond(self, code: int, charge, binaire: bool = False) -> None:
        corps = charge if binaire else json.dumps(charge, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type",
                         "image/png" if binaire else "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def _autorise(self) -> bool:
        if self.headers.get("X-Jeton", "") == JETON:
            return True
        self._repond(403, {"erreur": "jeton invalide"})
        return False

    def do_GET(self) -> None:                        # noqa: N802
        if not self._autorise():
            return
        if self.path == "/etat":
            rendu = bureau_rendu()
            self._repond(200, {
                "bureau_rendu": rendu,
                "session": os.environ.get("SESSIONNAME", "?"),
                "fenetre": fenetre_active() if rendu else None,
                "curseur": position() if rendu else None,
                "ecran": _taille_ecran(),
            })
        else:
            self._repond(404, {"erreur": "inconnu"})

    def do_POST(self) -> None:                       # noqa: N802
        if not self._autorise():
            return
        taille = int(self.headers.get("Content-Length") or 0)
        try:
            args = json.loads(self.rfile.read(taille) or b"{}")
        except ValueError:
            self._repond(400, {"erreur": "corps illisible"})
            return
        if not bureau_rendu():
            self._repond(409, {"erreur": "bureau non rendu",
                               "detail": "personne n'est connecte a la session : il n'y a "
                                         "aucun ecran a voir ni fenetre ou cliquer."})
            return
        try:
            if self.path == "/capture":
                zone = args.get("zone")
                self._repond(200, capture(tuple(zone) if zone else None), binaire=True)
            elif self.path == "/lire":
                zone = args.get("zone")
                lu = lis_l_ecran(tuple(zone) if zone else None,
                                 str(args.get("langue") or "fr-FR"))
                png = lu.pop("png", b"")
                lu["png_base64"] = base64.b64encode(png).decode("ascii")
                lu["fenetre"] = fenetre_active()
                lu["ecran"] = _taille_ecran()
                self._repond(200, lu)
            elif self.path == "/souris":
                acte = args.get("action", "clic")
                if acte == "clic":
                    cible = clique(int(args["x"]), int(args["y"]),
                                   str(args.get("bouton", "gauche")),
                                   bool(args.get("double")))
                    self._repond(200, {"fait": "clic", "fenetre": cible})
                elif acte == "bouge":
                    bouge(int(args["x"]), int(args["y"]))
                    self._repond(200, {"fait": "deplacement", "curseur": position()})
                elif acte == "defile":
                    defile(int(args.get("crans", -3)))
                    self._repond(200, {"fait": "defilement"})
                else:
                    self._repond(400, {"erreur": f"action souris inconnue : {acte}"})
            elif self.path == "/clavier":
                if "texte" in args:
                    cible = tape(str(args["texte"]))
                    self._repond(200, {"fait": "saisie", "fenetre": cible})
                else:
                    cible = touche(str(args.get("touche", "")))
                    self._repond(200, {"fait": "touche", "fenetre": cible})
            else:
                self._repond(404, {"erreur": "inconnu"})
        except Refus as refus:
            self._repond(403, {"erreur": str(refus)})
        except Exception as exc:                     # noqa: BLE001
            self._repond(500, {"erreur": f"{type(exc).__name__}: {exc}"})


def _taille_ecran() -> list:
    return [u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)]


def jeton() -> str:
    """Jeton partage, cree une fois et relu ensuite.

    Le guichet n'ecoute que sur 127.0.0.1 ; le jeton evite qu'un autre programme local — il y
    en a beaucoup sur cette machine — ne se serve des mains sans le savoir.
    """
    if JETON_FICHIER.exists():
        valeur = JETON_FICHIER.read_text(encoding="utf-8").strip()
        if valeur:
            return valeur
    valeur = secrets.token_urlsafe(24)
    JETON_FICHIER.write_text(valeur, encoding="utf-8")
    return valeur


def principal() -> int:
    global JETON
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    JETON = jeton()
    serveur = ThreadingHTTPServer(("127.0.0.1", PORT), Guichet)
    print(f"Les mains ecoutent sur 127.0.0.1:{PORT} — session "
          f"{os.environ.get('SESSIONNAME', '?')}, bureau "
          f"{'rendu' if bureau_rendu() else 'NON rendu (personne connecte)'}.")
    print("Ctrl-C pour les retirer.")
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        print("\nmains retirees.")
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
