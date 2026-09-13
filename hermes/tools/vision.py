"""Les YEUX : faire decrire une image par un modele qui voit.

🚨 LE MODELE DE L'AGENT EST AVEUGLE. Mesure du 13/09 sur le catalogue Venice : 79 modeles sur
119 acceptent les images, mais `olafangensan-glm-4.7-flash-heretic` — celui qui fait tourner
l'agent — n'en fait pas partie. Lui envoyer une capture ne servirait a rien : il repondrait
en devinant, ce qui est pire que de dire qu'il ne voit pas.

Un second modele regarde donc a sa place et rend une description en francais, que l'agent lit
comme du texte. C'est la difference avec l'OCR de `ecran_voir` : l'OCR rend les MOTS et leurs
positions, la vision rend les FORMES, les couleurs, l'agencement, et ce qui n'a pas de
libelle — une icone, une photo de produit, un graphique.

Les deux modeles retenus ont ete mesures sur une image dont je connaissais le contenu exact
(rond rouge, bouton vert « Valider », « 47 % », « 3 elements en attente ») : 6 reperes sur 6
pour l'un comme pour l'autre.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

from ..errors import ToolError
from . import ToolContext, tool

#: 🚨 DES MODELES DECENSURES, COMME LE RESTE DE L'AGENT. Consigne du patron : rester sur
#: GLM-4.7-heretic et ses semblables. Le modele principal n'a jamais bouge ; ces yeux-la
#: auraient pu introduire un modele bride par la bande, et refuser de decrire une capture
#: d'ecran au mauvais moment.
#:
#: Sur les 119 modeles du catalogue, quatre sont decensures ET voient. Mesure du 13/09 sur
#: une image dont le contenu etait connu — rond rouge, bouton vert « Valider », « 47 % »,
#: « 3 elements en attente » :
#:   gemma-4-uncensored           1,7 s — 6 reperes sur 6, le plus precis sur les POSITIONS
#:   venice-uncensored-1-2        1,7 s — 6 sur 6
#:   venice-uncensored-role-play      — HTTP 400, inutilisable
#:   olafangensan-glm-4.7-heretic     — aveugle (supportsVision = false)
#: Les positions comptent : c'est d'elles que depend le clic qui suit. Et ces deux-la sont
#: deux fois plus rapides que les modeles brides essayes d'abord.
MODELES = ("gemma-4-uncensored", "venice-uncensored-1-2")
ENDPOINT = "https://api.venice.ai/api/v1/chat/completions"

#: Au-dela, l'appel devient lent et cher pour rien : un ecran 1920x1080 encode en PNG pese
#: deja 1 a 2 Mo, et les modeles redimensionnent de toute facon.
POIDS_MAXI = 6 * 1024 * 1024

CONSIGNE = (
    "Tu decris une image pour un agent qui ne la voit pas et qui va peut-etre agir dessus. "
    "Sois factuel, concret et bref, en francais. Dis les formes, les couleurs, la position "
    "des elements les uns par rapport aux autres, les textes et les chiffres lisibles. "
    "Ne suppose rien : si quelque chose est illisible ou ambigu, dis-le."
)


def _cle() -> str:
    # 🚨 Importer la config, ce n'est pas decoratif : c'est ce qui declenche `load_dotenv()`.
    # Sans cela le module lit un environnement vide et conclut « pas de cle » alors que le
    # fichier .env en contient une — constate au premier essai.
    from .. import config  # noqa: F401

    cle = os.environ.get("VENICE_API_KEY", "").strip()
    if not cle:
        raise ToolError("pas de cle Venice : je ne peux pas faire regarder l'image.")
    return cle


async def decris(png: bytes, question: str = "", delai: int = 90) -> str:
    """Envoie l'image aux modeles qui voient, le premier qui repond gagne."""
    import httpx

    if len(png) > POIDS_MAXI:
        raise ToolError(f"image trop lourde ({len(png) // 1024} Ko) : maximum "
                        f"{POIDS_MAXI // 1024} Ko.")
    demande = question.strip() or "Decris cette image."
    charge = {
        "messages": [
            {"role": "system", "content": CONSIGNE},
            {"role": "user", "content": [
                {"type": "text", "text": demande},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}},
            ]},
        ],
        "max_tokens": 700,
    }
    entetes = {"Authorization": f"Bearer {_cle()}", "Content-Type": "application/json"}
    soucis = []
    async with httpx.AsyncClient(timeout=delai) as client:
        for modele in MODELES:
            try:
                reponse = await client.post(ENDPOINT, json={**charge, "model": modele},
                                            headers=entetes)
                if reponse.status_code != 200:
                    soucis.append(f"{modele} : {reponse.status_code}")
                    continue
                texte = reponse.json()["choices"][0]["message"]["content"].strip()
                if texte:
                    return texte
                soucis.append(f"{modele} : reponse vide")
            except Exception as exc:  # noqa: BLE001 - delai, coupure, JSON inattendu
                soucis.append(f"{modele} : {type(exc).__name__}")
    raise ToolError("aucun modele n'a pu regarder l'image (" + " ; ".join(soucis) + ").")


@tool(
    "regarde",
    "Fait DECRIRE une image par un modele qui voit : formes, couleurs, agencement, et tout "
    "ce qui n'a pas de texte. Sans `fichier`, regarde l'ecran de la machine. A utiliser "
    "quand `ecran_voir` ne suffit pas — une photo, une icone sans libelle, un graphique — "
    "ou pour une image que le patron a envoyee.",
    {
        "fichier": {
            "type": "string",
            "description": "Image de mon workspace (png, jpg, webp). Sans ce parametre, "
                           "c'est l'ecran de la machine qui est regarde.",
        },
        "question": {
            "type": "string",
            "description": "Ce que je veux savoir de l'image. Par defaut, une description "
                           "generale.",
        },
        "zone": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Pour l'ecran seulement : rectangle [gauche, haut, droite, bas].",
        },
    },
    [],
)
async def regarde(ctx: ToolContext, args: dict[str, Any]) -> str:
    fichier = str(args.get("fichier") or "").strip()
    question = str(args.get("question") or "")
    if fichier:
        from .files import resolve_in

        chemin: Path = resolve_in(ctx.workspace, fichier)
        if not chemin.is_file():
            raise ToolError(f"`{fichier}` n'existe pas dans mon workspace.")
        png = chemin.read_bytes()
        origine = fichier
    else:
        # Pas de fichier : on demande l'ecran aux mains. Elles seules y ont acces.
        from .ecran import _appel

        zone = args.get("zone")
        reponse = await _appel("/capture", {"zone": zone} if zone else {}, delai=60)
        png = reponse.content
        origine = "l'ecran"
    description = await decris(png, question)
    return f"Ce que je vois dans {origine} :\n{description}"


TOOLS = (regarde,)
