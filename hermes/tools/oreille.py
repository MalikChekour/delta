"""Les OREILLES : transcrire un son en texte, en local.

🚨 EN LOCAL, ET C'EST VOULU. La machine n'a pas de GPU, mais `faster-whisper` tourne sur
processeur et le modele `base` (142 Mo) est DEJA telecharge sur ce disque — il sert au calage
des sous-titres de l'usine a videos. Rien a envoyer chez un tiers : ce que le patron dit a
son bot reste sur sa machine.

Le piege a connaitre : l'agent tourne sous SYSTEM, dont le dossier personnel n'est pas celui
de l'Administrateur. Sans le chemin explicite ci-dessous, il ne trouverait pas le modele deja
present et le retelechargerait dans un coin a lui — 142 Mo pour rien, et une attente au
premier message vocal.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from ..errors import ToolError
from ..sandbox import clip
from . import ToolContext, tool

#: Le cache ou le modele se trouve deja. `HF_HOME` doit etre pose AVANT que la bibliotheque
#: ne soit importee, sinon elle a deja fige son chemin.
CACHE = Path(os.environ.get("HERMES_CACHE_HF")
             or r"C:\Users\Administrator\.cache\huggingface")

#: 🚨 `small`, PAS `base`. Mesure du 13/09 sur une phrase francaise dite par une vraie voix :
#:   base   2,4 s — 11 mots sur 13 : « les CINQ produits » devient « les SAINS produits »,
#:                   « DEMAIN matin » devient « DE MA matin » ;
#:   small  6,2 s — 13 sur 13, mot pour mot.
#: Quatre secondes de plus sur un message vocal ne se remarquent pas ; un « cinq » devenu
#: « sains » fait agir l'agent de travers, et sans qu'il puisse s'en douter.
TAILLE = os.environ.get("HERMES_WHISPER") or "small"

#: Le patron parle francais. Laisser Whisper deviner lui a deja fait rendre du charabia
#: anglais avec aplomb sur une phrase francaise : mieux vaut un defaut juste.
LANGUE_PAR_DEFAUT = os.environ.get("HERMES_LANGUE_AUDIO") or "fr"

#: Extensions acceptees. Whisper decode lui-meme l'audio, y compris l'ogg/opus que Telegram
#: utilise pour les messages vocaux.
SONS = (".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".flac", ".webm",
        ".mp4", ".mkv", ".mov")

_MODELE: Any = None


def _modele():
    """Charge le modele une seule fois pour la duree du processus (2 a 3 s au premier appel)."""
    global _MODELE
    if _MODELE is None:
        os.environ.setdefault("HF_HOME", str(CACHE))
        from faster_whisper import WhisperModel

        _MODELE = WhisperModel(TAILLE, device="cpu", compute_type="int8",
                               download_root=str(CACHE / "hub"))
    return _MODELE


def _transcris(chemin: Path, langue: str | None) -> tuple[str, str, float]:
    modele = _modele()
    segments, info = modele.transcribe(str(chemin), language=langue, vad_filter=True)
    # `transcribe` rend un generateur paresseux : rien n'est calcule tant qu'on ne le parcourt
    # pas. L'oublier donnerait une transcription vide sans la moindre erreur.
    texte = " ".join(s.text.strip() for s in segments).strip()
    return texte, info.language, info.duration


@tool(
    "ecoute",
    "Transcrit un fichier audio ou video de mon workspace en texte, en local. Sert pour un "
    "message vocal, un enregistrement, ou la bande-son d'une video.",
    {
        "fichier": {"type": "string", "description": "Fichier son ou video du workspace."},
        "langue": {
            "type": "string",
            "description": "Code de langue (fr, en...). Sans ce parametre, elle est detectee.",
        },
    },
    ["fichier"],
)
async def ecoute(ctx: ToolContext, args: dict[str, Any]) -> str:
    from .files import resolve_in

    nom = str(args.get("fichier") or "").strip()
    if not nom:
        raise ToolError("donne le nom du fichier a ecouter.")
    chemin: Path = resolve_in(ctx.workspace, nom)
    if not chemin.is_file():
        raise ToolError(f"`{nom}` n'existe pas dans mon workspace.")
    if chemin.suffix.lower() not in SONS:
        raise ToolError(f"`{chemin.suffix}` n'est pas un format que je sais ecouter. "
                        f"Connus : {', '.join(SONS)}.")
    # 🚨 ON NE LAISSE PAS WHISPER DEVINER LA LANGUE. Mesure du 13/09 sur une phrase francaise
    # dictee : le modele `base` a conclu « anglais » et rendu « per to Verifier Lesync Prodits
    # ET Publier Law Video Domain » — du charabia, mais rendu avec aplomb, ce qui est pire
    # qu'une erreur visible. Le patron parle francais : c'est donc le defaut, et `langue`
    # reste la pour les autres cas. « auto » redonne la detection automatique.
    demandee = str(args.get("langue") or "").strip().lower()
    langue = None if demandee == "auto" else (demandee or LANGUE_PAR_DEFAUT)
    try:
        # La transcription est du calcul pur : elle bloquerait la boucle du bot pendant
        # plusieurs secondes et figerait toutes les autres conversations.
        texte, detectee, duree = await asyncio.to_thread(_transcris, chemin, langue)
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"transcription impossible : {type(exc).__name__} : {exc}") from exc
    if not texte:
        return (f"{nom} : {duree:.0f} s de son, mais aucune parole reconnue "
                f"(silence, musique, ou voix trop faible).")
    entete = f"{nom} — {duree:.0f} s, langue detectee : {detectee}\n"
    return entete + clip(texte, ctx.output_limit)


TOOLS = (ecoute,)
