# -*- coding: utf-8 -*-
r"""Ecrit dans la memoire de connaissances de l'agent les contraintes de la machine.

🚨 POURQUOI. L'agent code correctement mais ne connait pas le terrain : le 12/09, laisse
seul, il a ecrit un wrapper dont le serveur ecoutait par defaut sur le port 9222 —
exactement celui du Chrome qui publie sur TikTok et YouTube. Un appel aurait casse les
publications. Il ne pouvait pas le deviner : personne ne le lui avait dit.

Ce qui previent un degat immediat (ports pris, programmes a ne pas tuer) vit dans le PROMPT
SYSTEME, toujours present. Le detail vit ici, dans sa memoire, consultable par `rappelle`.

A relancer apres tout changement de la machine : python bench/_notes_machine.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import hermes.config as C                                  # noqa: E402
from hermes.tools import ToolContext, build_registry       # noqa: E402

NOTES = [
    ("machine — systeme et materiel",
     "Windows Server 2025. PowerShell et cmd : pas de sudo, pas d'apt ; rm, ls et cat "
     "n'existent que dans le Git Bash. AUCUN GPU, aucun CUDA (« Microsoft Basic Display "
     "Adapter », 0 Go de VRAM) : ne jamais telecharger un modele a faire tourner en local. "
     "Memoire 23,4 Go dont environ 11 libres. Disque C: 57 Go libres sur 200. PAS DE DOCKER : "
     "toute solution qui suppose un conteneur est hors de portee. Python 3.11. Binaires utiles "
     "absents du PATH systeme : rg dans AppData\\Local\\Microsoft\\WinGet\\Links, ast-grep "
     "dans AppData\\Roaming\\npm.",
     "machine,systeme,materiel,windows,gpu,docker"),

    ("machine — ports occupes",
     "NE JAMAIS prendre ces ports. 9222 : le Chrome qui publie sur TikTok et YouTube — le lui "
     "voler casse les publications. Ce Chrome-la, je ne le PRENDS pas, je le REJOINS : mes "
     "outils navigateur_onglets, navigateur_lire, navigateur_agir, navigateur_capture et "
     "navigateur_fermer s'y connectent proprement. Je n'y touche jamais autrement. 22346 : le terminal MT5. 18789 et 18791 : node. 3389 : "
     "RDP. 5985 : WinRM. Pour lancer un serveur local, choisir au-dessus de 9300 et dire "
     "lequel.",
     "machine,ports,reseau,danger"),

    ("machine — programmes a ne jamais tuer",
     "chrome.exe : publie sur TikTok et YouTube via le port 9222. terminal64.exe : Vantage "
     "MT5, trading en cours. hermes.exe dans AppData\\Local\\hermes\\hermes-agent : c'est "
     "NousResearch/hermes-agent avec le profil orderflow — il porte le meme nom que moi sans "
     "etre moi. Avant tout taskkill ou Stop-Process, verifier la LIGNE DE COMMANDE de la "
     "cible, jamais seulement son nom.",
     "machine,processus,danger"),

    ("machine — reseau et recherche",
     "Une seule adresse IP fixe, aucun proxy. Les moteurs finissent par la limiter : mesure du "
     "10/09 sur quatre requetes — bing 4 sur 4, yandex 3 sur 4, brave 1 sur 4, duckduckgo 0 "
     "sur 4. Un cache de 24 heures et un espacement de 2,5 secondes sont en place. Interroger "
     "`rappelle` avant de chercher, et ne jamais relancer une requete identique.",
     "machine,reseau,recherche"),

    ("machine — ma propre configuration",
     "Mon fichier .env et mon code vivent dans C:\\Users\\Administrator\\agentmk06-bot, "
     "AU-DESSUS de mon workspace : mes outils de fichier et de code les refusent. Pour changer "
     "un reglage — une limite, une cle, un modele — je nomme le reglage et le fichier, et je "
     "demande au patron de l'editer. Je ne le cherche pas dans mon workspace : il n'y est pas.",
     "machine,configuration,limites"),

    ("machine — ma session Python",
     "Mon outil python garde son etat dans une meme conversation : variables, imports et "
     "donnees chargees survivent d'un appel a l'autre (noyau ipykernel, un par conversation, "
     "trois au maximum). Je charge un fichier une fois et je le reutilise. Une erreur ne "
     "detruit pas la session. Un code trop long est interrompu au bout du delai, pas tue : "
     "l'etat reste, je relance avec un delai plus grand ou je decoupe. nouveau=true repart "
     "de zero. Rien ne passe d'une conversation a l'autre.",
     "machine,python,session,noyau"),

    ("machine — le navigateur",
     "Le Chrome de la machine est joignable par mes outils navigateur_*. Je m'en sers quand "
     "fetch_url rend une page vide (tout en JavaScript), un captcha, un blocage Cloudflare, "
     "ou quand la page demande d'etre connecte. REGLE : je regarde partout, je n'agis que la "
     "ou le patron n'est pas connecte — sur TikTok, YouTube, Google, Redbubble et Pinterest "
     "je peux lire et capturer, cliquer y est refuse par le code. Je n'ouvre qu'un onglet et "
     "je le ferme en partant ; je ne touche jamais un onglet que je n'ai pas ouvert. Une "
     "capture va dans mon workspace : le patron la recupere avec /get nom.png.",
     "machine,navigateur,chrome,web"),

    ("machine — ce que je ne dois pas oublier de verifier",
     "Je tourne en tache planifiee sous le compte SYSTEM, avec les droits les plus eleves de "
     "la machine : une commande destructrice n'aura rien pour l'arreter. Avant une suppression "
     "ou un arret de service, je dis ce que je vais faire et sur quoi. Le serveur subit une "
     "force brute RDP permanente : ne jamais recopier une cle ou un jeton dans une reponse, "
     "meme partiellement.",
     "machine,securite,prudence"),
]


async def principal() -> None:
    s = C.load(require_telegram=False)
    reg = build_registry()
    ctx = ToolContext(workspace=s.workspace, exec_timeout=30, output_limit=4000,
                      request_timeout=20, search_url="x")
    for titre, contenu, etiquettes in NOTES:
        r = await reg.dispatch(ctx, "retiens",
                               {"titre": titre, "contenu": contenu, "etiquettes": etiquettes})
        print("  " + " ".join(r.split())[:88])


if __name__ == "__main__":
    asyncio.run(principal())
