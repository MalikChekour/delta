"""Ecran, souris et clavier — par la passerelle « mains ».

🚨 POURQUOI CE N'EST PAS FAIT ICI DIRECTEMENT. L'agent tourne sous SYSTEM, en session 0. Le
bureau vit en session 2. L'isolement de session de Windows est etanche : depuis la session 0,
`GetForegroundWindow()` rend 0 et toute capture leve « screen grab failed ». Installer
pyautogui n'y changerait rien — ce n'est pas un manque de bibliotheque, c'est une frontiere
du systeme.

Le programme `mains/mains.py` tourne donc dans la session du patron et ouvre un guichet sur
127.0.0.1:9340. Ce module l'appelle.

Cette architecture a un effet secondaire precieux, apres la lecon de cette semaine : les
garde-fous ne sont plus un filtre sur du texte qu'un modele pourrait contourner en changeant
de formulation. Le SEUL chemin vers le bureau passe par le guichet, et le guichet refuse de
lui-meme d'agir dans Chrome ou MetaTrader. C'est une barriere structurelle, pas une consigne.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..sandbox import clip
from . import ToolContext, tool

#: Surchargeable par MAINS_URL : les tests montent un vrai guichet sur un port libre.
GUICHET = os.environ.get("MAINS_URL") or "http://127.0.0.1:9340"
RACINE = Path(__file__).resolve().parent.parent.parent
JETON_FICHIER = RACINE / ".mains_jeton"

_ABSENT = (
    "les mains ne repondent pas. Le programme qui les porte doit tourner DANS la session du "
    "patron — l'agent, lui, est en session 0 et n'a aucun acces au bureau.\n"
    "Le patron le lance avec : python mains\\mains.py\n"
    "Tant qu'il n'est pas lance, je n'ai ni ecran, ni souris, ni clavier."
)

_ENDORMI = (
    "il n'y a pas de bureau a voir : personne n'est connecte a la session. Sur ce serveur, "
    "une session RDP deconnectee n'affiche rien du tout — ce n'est pas une panne. Le patron "
    "doit se connecter en Bureau a distance pour que l'ecran existe."
)


#: Derniere lecture de l'ecran, pour savoir ce qu'une action a CHANGE.
#: 🚨 C'est ce qui fait la difference entre agir et comprendre ce qu'on fait. Un clic qui
#: rate ressemble exactement a un clic qui reussit : meme retour, meme silence. Sans
#: comparaison avant/apres, l'agent enchaine sur une action qui n'a rien produit, et il le
#: rapporte au patron comme un succes.
_DERNIERE_VUE: dict[str, Any] = {"lignes": []}

#: Le temps qu'une fenetre se redessine apres un clic. En dessous, on relit l'ecran d'avant
#: et on conclut « rien n'a change » a tort.
_DELAI_REDESSIN = 0.8


def _resume(lignes: list) -> set:
    return {l.get("texte", "").strip() for l in lignes if l.get("texte", "").strip()}


async def _qu_est_ce_qui_a_change() -> str:
    """Relit l'ecran et decrit l'ecart avec la lecture precedente."""
    import asyncio

    await asyncio.sleep(_DELAI_REDESSIN)
    avant = _resume(_DERNIERE_VUE.get("lignes") or [])
    try:
        lu = (await _appel("/lire", {}, delai=60)).json()
    except ToolError as exc:
        return f"\n(je n'ai pas pu relire l'ecran pour verifier : {exc})"
    apres = _resume(lu.get("lignes") or [])
    _DERNIERE_VUE["lignes"] = lu.get("lignes") or []
    if not avant:
        return ("\n(je n'avais pas lu l'ecran avant d'agir : je ne peux pas dire ce qui a "
                "change. Lis l'ecran AVANT d'agir la prochaine fois.)")
    apparu = sorted(apres - avant)[:6]
    disparu = sorted(avant - apres)[:6]
    if not apparu and not disparu:
        return ("\n🚨 RIEN N'A CHANGE a l'ecran. L'action n'a probablement eu aucun effet : "
                "mauvaise position, fenetre inactive, ou element non cliquable. Ne continue "
                "pas comme si elle avait reussi.")
    morceaux = []
    if apparu:
        morceaux.append("apparu : " + " | ".join(apparu))
    if disparu:
        morceaux.append("disparu : " + " | ".join(disparu))
    return "\nCe qui a change a l'ecran — " + " ; ".join(morceaux)


def _jeton() -> str:
    try:
        return JETON_FICHIER.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ToolError(_ABSENT) from exc


async def _appel(chemin: str, charge: dict | None = None, delai: int = 30):
    import httpx

    entetes = {"X-Jeton": _jeton()}
    try:
        async with httpx.AsyncClient(timeout=delai) as client:
            if charge is None:
                reponse = await client.get(f"{GUICHET}{chemin}", headers=entetes)
            else:
                reponse = await client.post(f"{GUICHET}{chemin}", json=charge,
                                            headers=entetes)
    except Exception as exc:  # noqa: BLE001 - connexion refusee, guichet eteint...
        raise ToolError(_ABSENT) from exc
    if reponse.status_code == 409:
        raise ToolError(_ENDORMI)
    if reponse.status_code == 403:
        # Refus d'un garde-fou : le message porte deja la raison, on le rend tel quel.
        raise ToolError(reponse.json().get("erreur", "action refusee."))
    if reponse.status_code != 200:
        raise ToolError(f"les mains ont repondu {reponse.status_code} : {reponse.text[:200]}")
    return reponse


@tool(
    "ecran_voir",
    "Regarde l'ecran de la machine : rend le TEXTE lu a l'ecran avec la position de chaque "
    "ligne, et enregistre une capture dans mon workspace. Les coordonnees servent ensuite a "
    "`souris`. A utiliser avant toute action a l'ecran, et apres, pour verifier le resultat.",
    {
        "zone": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Rectangle [gauche, haut, droite, bas] pour ne lire qu'une "
                           "partie de l'ecran. Sans zone, l'ecran entier.",
        },
        "nom": {"type": "string", "description": "Nom du PNG enregistre (defaut ecran.png)."},
    },
    [],
)
async def ecran_voir(ctx: ToolContext, args: dict[str, Any]) -> str:
    zone = args.get("zone")
    if zone is not None and (not isinstance(zone, list) or len(zone) != 4):
        raise ToolError("`zone` doit valoir [gauche, haut, droite, bas].")
    nom = str(args.get("nom") or "ecran.png").strip() or "ecran.png"
    if "/" in nom or "\\" in nom or ".." in nom:
        raise ToolError("`nom` doit etre un simple nom de fichier, sans chemin.")
    if not nom.lower().endswith(".png"):
        nom += ".png"

    reponse = await _appel("/lire", {"zone": zone} if zone else {}, delai=60)
    lu = reponse.json()
    if "erreur" in lu:
        raise ToolError(str(lu["erreur"]))

    cible = ctx.workspace / nom
    try:
        cible.write_bytes(base64.b64decode(lu.get("png_base64", "")))
    except Exception as exc:  # noqa: BLE001 - l'image est un bonus, le texte est l'essentiel
        cible = None

    fenetre = lu.get("fenetre") or {}
    ecran = lu.get("ecran") or []
    entete = [
        f"Ecran {ecran[0]}x{ecran[1]}" if len(ecran) == 2 else "Ecran",
        f"Fenetre active : {fenetre.get('titre') or '(sans titre)'} "
        f"[{fenetre.get('programme') or '?'}]",
    ]
    lignes = lu.get("lignes") or []
    _DERNIERE_VUE["lignes"] = lignes          # reference pour le "qu'est-ce qui a change"
    if not lignes:
        entete.append("Aucun texte lisible a l'ecran (image, video, ou ecran vide).")
    else:
        entete.append(f"{len(lignes)} ligne(s) lue(s) — x,y = ou cliquer :")
    corps = "\n".join(f"  ({l['x']},{l['y']})  {l['texte']}" for l in lignes)
    pied = f"\n\nCapture : {nom} — le patron la recupere avec /get {nom}" if cible else ""
    return clip("\n".join(entete) + "\n" + corps, ctx.output_limit) + pied


@tool(
    "souris",
    "Deplace la souris, clique ou fait defiler, aux coordonnees donnees par `ecran_voir`. "
    "Refuse dans les fenetres de Chrome et de MetaTrader.",
    {
        "action": {"type": "string", "description": "clic | bouge | defile"},
        "x": {"type": "integer", "description": "Abscisse a l'ecran."},
        "y": {"type": "integer", "description": "Ordonnee a l'ecran."},
        "bouton": {"type": "string", "description": "gauche | droit | milieu (defaut gauche)."},
        "double": {"type": "boolean", "description": "Double-clic."},
        "crans": {"type": "integer", "description": "Defilement : negatif vers le bas."},
    },
    ["action"],
)
async def souris(ctx: ToolContext, args: dict[str, Any]) -> str:
    action = str(args.get("action", "")).strip().lower()
    charge: dict[str, Any] = {"action": action}
    if action in ("clic", "bouge"):
        if "x" not in args or "y" not in args:
            raise ToolError("`x` et `y` sont obligatoires. Lis d'abord l'ecran avec "
                            "`ecran_voir` : il donne les coordonnees de chaque ligne.")
        charge.update(x=int(args["x"]), y=int(args["y"]),
                      bouton=str(args.get("bouton") or "gauche"),
                      double=bool(args.get("double")))
    elif action == "defile":
        charge["crans"] = int(args.get("crans") or -3)
    else:
        raise ToolError("action inconnue : attendu clic, bouge ou defile.")
    resultat = (await _appel("/souris", charge)).json()
    ecart = await _qu_est_ce_qui_a_change() if action in ("clic", "defile") else ""
    fenetre = resultat.get("fenetre") or {}
    ou = f" dans « {fenetre.get('titre') or fenetre.get('programme') or '?'} »" if fenetre else ""
    return f"{resultat.get('fait', action)} fait{ou}.{ecart}"


@tool(
    "clavier",
    "Saisit du texte ou appuie sur une touche dans la fenetre active. "
    "Refuse dans les fenetres de Chrome et de MetaTrader.",
    {
        "texte": {"type": "string", "description": "Texte a saisir tel quel, accents compris."},
        "touche": {
            "type": "string",
            "description": "Touche ou combinaison : entree, tab, echap, suppr, haut, bas, "
                           "ctrl+a, ctrl+c, ctrl+v, ctrl+s...",
        },
    },
    [],
)
async def clavier(ctx: ToolContext, args: dict[str, Any]) -> str:
    texte, touche = args.get("texte"), args.get("touche")
    if texte is None and not touche:
        raise ToolError("donne `texte` a saisir, ou `touche` a presser.")
    charge = {"texte": str(texte)} if texte is not None else {"touche": str(touche)}
    resultat = (await _appel("/clavier", charge)).json()
    ecart = await _qu_est_ce_qui_a_change()
    fenetre = resultat.get("fenetre") or {}
    ou = f" dans « {fenetre.get('titre') or fenetre.get('programme') or '?'} »" if fenetre else ""
    quoi = f"texte saisi ({len(str(texte))} caracteres)" if texte is not None else f"touche {touche}"
    return f"{quoi}{ou}.{ecart}"


@tool(
    "ecran_etat",
    "Dit si un bureau est disponible, quelle fenetre est au premier plan et ou est le "
    "curseur. A appeler en premier quand une action a l'ecran est demandee.",
    {},
    [],
)
async def ecran_etat(ctx: ToolContext, args: dict[str, Any]) -> str:
    etat = (await _appel("/etat")).json()
    if not etat.get("bureau_rendu"):
        return ("Les mains repondent, mais il n'y a AUCUN bureau : personne n'est connecte a "
                "la session. Le patron doit ouvrir une session Bureau a distance pour que "
                "l'ecran existe. Je ne peux ni voir ni cliquer d'ici la.")
    fenetre = etat.get("fenetre") or {}
    curseur = etat.get("curseur") or []
    ecran = etat.get("ecran") or []
    return (f"Bureau disponible, ecran {ecran[0]}x{ecran[1]}.\n"
            f"Fenetre active : {fenetre.get('titre') or '(sans titre)'} "
            f"[{fenetre.get('programme') or '?'}]\n"
            f"Curseur en ({curseur[0]},{curseur[1]}).")


TOOLS = (ecran_etat, ecran_voir, souris, clavier)
