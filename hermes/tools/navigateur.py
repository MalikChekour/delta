"""Acces au VRAI navigateur de la machine, par CDP (port 9222).

Ce que cela apporte que `fetch_url` ne sait pas faire : les pages qui ne s'affichent qu'apres
execution du JavaScript, celles derriere Cloudflare, et celles qui demandent une session
ouverte (TikTok Studio, YouTube Studio, Redbubble).

🚨 C'EST LE CHROME DE PRODUCTION. Le meme profil publie sur TikTok et YouTube. Le 12/09 il a
deja ete tue par l'agent, et les sessions ont ete perdues : il faut les rouvrir a la main.
Tout ce module est donc ecrit autour d'une regle simple, appliquee DANS LE CODE et pas dans
une consigne — parce qu'une consigne ecrite n'a jamais arrete ce modele :

    l'agent REGARDE partout, il n'AGIT que la ou le patron n'est pas connecte.

Et il ne touche jamais un onglet qu'il n'a pas ouvert lui-meme.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from ..errors import ToolError
from ..sandbox import clip
from . import ToolContext, tool

#: Le Chrome de publication. Voir la note machine : ce port ne se prend pas, il se rejoint.
CDP = "http://127.0.0.1:9222"

#: 🚨 Sites ou le patron a une session ouverte. On y LIT, on n'y CLIQUE pas : un clic peut
#: publier un brouillon, supprimer une video, vider un panier ou fermer la session. La
#: distinction lecture/action est la seule chose qui rend cet outil sur.
_SITES_SENSIBLES = (
    "tiktok.com", "youtube.com", "google.com", "gstatic.com",
    "redbubble.com", "pinterest.com", "telegram.org", "web.telegram.org",
    "amazon.", "facebook.com", "instagram.com", "x.com", "twitter.com",
    "paypal.", "github.com",
)

#: Une simple NAVIGATION suffit a se deconnecter : `/logout` est une URL, pas un bouton.
_URLS_DESTRUCTRICES = re.compile(
    r"(logout|log-out|signout|sign-out|deconnexion|se-deconnecter|/delete|/supprimer"
    r"|revoke|disconnect)", re.I)

#: Seuls http et https. `file://` donnerait a l'agent la lecture de TOUT le disque, en
#: contournant la limite du workspace qui protege ses outils de fichiers ; `chrome://` donne
#: acces aux reglages du navigateur, dont l'historique et les mots de passe enregistres.
_SCHEMAS = ("http://", "https://")

#: Un seul onglet a lui, jamais plus. Trop d'onglets Studio ouverts figent CDP : `Page.enable`
#: devient muet et la publication casse — panne deja vue sur cette machine.
_ETAT: dict[str, Any] = {"pw": None, "navigateur": None, "page": None, "jeton": ""}


def _verifie_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise ToolError("url vide.")
    if not url.lower().startswith(_SCHEMAS):
        raise ToolError(
            "refuse : seules les adresses http:// et https:// sont permises. "
            "`file://` lirait tout le disque et `chrome://` les reglages du navigateur."
        )
    if _URLS_DESTRUCTRICES.search(url):
        raise ToolError(
            f"refuse : cette adresse ressemble a une deconnexion ou une suppression "
            f"({url[:90]}). Une simple visite suffirait a fermer la session du patron."
        )
    return url


def _sensible(url: str) -> str:
    """Renvoie le domaine sensible reconnu dans l'URL, ou une chaine vide."""
    bas = (url or "").lower()
    for domaine in _SITES_SENSIBLES:
        if domaine in bas:
            return domaine
    return ""


async def _navigateur():
    """Connexion CDP, etablie a la demande et gardee entre les appels.

    🚨 On ne ferme JAMAIS le navigateur. `browser.close()` sur une connexion CDP coupe le
    lien, mais il n'y a aucune raison de prendre le risque : ce Chrome porte les sessions de
    publication, et il a deja ete perdu une fois.
    """
    nav = _ETAT.get("navigateur")
    if nav is not None and nav.is_connected():
        return nav
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - depend de l'installation
        raise ToolError("playwright n'est pas installe dans mon environnement.") from exc
    try:
        if _ETAT.get("pw") is None:
            _ETAT["pw"] = await async_playwright().start()
        _ETAT["navigateur"] = await _ETAT["pw"].chromium.connect_over_cdp(CDP, timeout=15000)
    except Exception as exc:  # noqa: BLE001
        _ETAT["navigateur"] = None
        raise ToolError(
            f"impossible de joindre le navigateur sur {CDP} : {exc}. "
            f"Chrome doit tourner avec --remote-debugging-port=9222. "
            f"Je ne le relance pas moi-meme : c'est le Chrome de publication du patron."
        ) from exc
    return _ETAT["navigateur"]


async def _contexte(nav):
    if nav.contexts:
        return nav.contexts[0]
    raise ToolError("le navigateur est joignable mais n'a aucun contexte ouvert.")


async def _ramasse_mes_orphelins(contexte) -> int:
    """Ferme les onglets marques `__hermes` restes d'une execution precedente.

    🚨 SANS CECI LES ONGLETS S'ACCUMULENT. Mon etat vit en memoire du processus : au
    redemarrage du service, l'onglet que j'avais ouvert reste dans Chrome et je n'en sais
    plus rien. A raison d'un par redemarrage, on arrive au cas deja vu sur cette machine —
    trop d'onglets ouverts, `Page.enable` devient muet, et la publication casse.

    On ne ferme que ce qui porte MON marqueur : jamais un onglet du patron.
    """
    fermes = 0
    for page in list(contexte.pages):
        if page is _ETAT.get("page"):
            continue
        try:
            if await page.evaluate("window.__hermes || ''"):
                await page.close()
                fermes += 1
        except Exception:  # noqa: BLE001 - page chrome://, en cours de chargement, deja fermee
            continue
    return fermes


async def _ma_page(creer: bool = True):
    """La page de l'agent, et elle seule.

    🚨 Le marqueur `window.__hermes` est ce qui rend le module sur apres une reconnexion :
    sans lui on retomberait sur « la premiere page du contexte », c'est-a-dire un onglet du
    patron — potentiellement TikTok Studio en pleine publication.
    """
    page = _ETAT.get("page")
    if page is not None and not page.is_closed():
        return page
    nav = await _navigateur()
    ctx = await _contexte(nav)
    jeton = _ETAT.get("jeton")
    if jeton:
        for candidate in ctx.pages:                       # retrouver la mienne apres coupure
            try:
                if await candidate.evaluate("window.__hermes || ''") == jeton:
                    _ETAT["page"] = candidate
                    return candidate
            except Exception:  # noqa: BLE001 - page chrome://, en cours de chargement, etc.
                continue
    if not creer:
        raise ToolError("je n'ai pas d'onglet ouvert. Utilise d'abord `navigateur_lire`.")
    await _ramasse_mes_orphelins(ctx)
    page = await ctx.new_page()
    _ETAT["jeton"] = uuid.uuid4().hex
    _ETAT["page"] = page
    await page.add_init_script(f"window.__hermes = {_ETAT['jeton']!r}")
    return page


def _texte_lisible(html: str, brut: str) -> str:
    try:
        import trafilatura

        lu = trafilatura.extract(html, include_comments=False, include_tables=True)
        if lu and len(lu) > 200:
            return lu
    except Exception:  # noqa: BLE001 - l'extraction fine est un bonus, jamais un passage oblige
        pass
    return re.sub(r"\n{3,}", "\n\n", brut or "")


# --------------------------------------------------------------------------- #
# Outils
# --------------------------------------------------------------------------- #

@tool(
    "navigateur_onglets",
    "Liste les onglets ouverts dans le navigateur de la machine (titre et adresse). "
    "Lecture seule : cela ne change rien a l'ecran du patron.",
    {},
    [],
)
async def navigateur_onglets(ctx: ToolContext, args: dict[str, Any]) -> str:
    nav = await _navigateur()
    contexte = await _contexte(nav)
    lignes = []
    for i, page in enumerate(contexte.pages):
        try:
            titre = await page.title()
        except Exception:  # noqa: BLE001
            titre = "?"
        mienne = " (le mien)" if page is _ETAT.get("page") else ""
        lignes.append(f"[{i}] {titre[:70]}{mienne}\n    {page.url[:150]}")
    if not lignes:
        return "aucun onglet ouvert."
    return "%d onglet(s) :\n%s" % (len(lignes), "\n".join(lignes))


@tool(
    "navigateur_lire",
    "Ouvre une adresse dans MON onglet du navigateur de la machine et renvoie le texte de la "
    "page une fois le JavaScript execute. A utiliser quand `fetch_url` rend une page vide, "
    "un captcha, ou quand la page demande d'etre connecte.",
    {
        "url": {"type": "string", "description": "Adresse http(s) a ouvrir."},
        "attendre": {
            "type": "integer",
            "description": "Secondes d'attente apres chargement, pour le JavaScript lent "
                           "(defaut 3, maximum 30).",
        },
        "selecteur": {
            "type": "string",
            "description": "Selecteur CSS a attendre avant de lire, si la page se remplit "
                           "progressivement.",
        },
    },
    ["url"],
)
async def navigateur_lire(ctx: ToolContext, args: dict[str, Any]) -> str:
    url = _verifie_url(str(args.get("url", "")))
    attendre = max(0, min(int(args.get("attendre") or 3), 30))
    page = await _ma_page()
    try:
        await page.goto(url, wait_until="domcontentloaded",
                        timeout=max(15, ctx.request_timeout) * 1000)
        selecteur = str(args.get("selecteur") or "").strip()
        if selecteur:
            try:
                await page.wait_for_selector(selecteur, timeout=attendre * 1000 or 5000)
            except Exception:  # noqa: BLE001 - l'absence du selecteur n'empeche pas de lire
                pass
        elif attendre:
            await page.wait_for_timeout(attendre * 1000)
        final = page.url
        titre = await page.title()
        html = await page.content()
        brut = await page.inner_text("body")
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"lecture impossible : {exc}") from exc
    entete = f"# {titre}\n{final}\n"
    if _URLS_DESTRUCTRICES.search(final) or "login" in final.lower():
        entete += (
            "\n🚨 Cette page est un ecran de connexion : la session du patron est fermee "
            "sur ce site. Dis-le-lui, il doit se reconnecter lui-meme.\n"
        )
    return entete + "\n" + clip(_texte_lisible(html, brut), ctx.output_limit)


@tool(
    "navigateur_agir",
    "Clique, saisit du texte, appuie sur une touche ou fait defiler MON onglet. "
    "Refuse sur les sites ou le patron a une session ouverte (TikTok, YouTube, Google, "
    "Redbubble...) : la je peux seulement lire.",
    {
        "action": {
            "type": "string",
            "description": "cliquer | taper | touche | defiler",
        },
        "selecteur": {
            "type": "string",
            "description": "Selecteur CSS de la cible, pour cliquer ou taper.",
        },
        "texte": {
            "type": "string",
            "description": "Texte a saisir (action taper) ou nom de la touche (action touche, "
                           "par exemple Enter).",
        },
    },
    ["action"],
)
async def navigateur_agir(ctx: ToolContext, args: dict[str, Any]) -> str:
    action = str(args.get("action", "")).strip().lower()
    selecteur = str(args.get("selecteur") or "").strip()
    texte = str(args.get("texte") or "")
    page = await _ma_page(creer=False)
    domaine = _sensible(page.url)
    if domaine:
        raise ToolError(
            f"action refusee : cette page est sur « {domaine} », ou le patron a une session "
            f"ouverte. Un clic peut publier, supprimer ou deconnecter. Je peux la LIRE et la "
            f"CAPTURER, pas agir dessus. Si une action y est necessaire, c'est au patron de "
            f"la faire."
        )
    try:
        if action == "cliquer":
            if not selecteur:
                raise ToolError("`selecteur` est obligatoire pour cliquer.")
            await page.click(selecteur, timeout=10000)
        elif action == "taper":
            if not selecteur:
                raise ToolError("`selecteur` est obligatoire pour taper.")
            await page.fill(selecteur, texte, timeout=10000)
        elif action == "touche":
            await page.keyboard.press(texte or "Enter")
        elif action == "defiler":
            await page.mouse.wheel(0, 900)
        else:
            raise ToolError("action inconnue : attendu cliquer, taper, touche ou defiler.")
        await page.wait_for_timeout(1200)
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"action impossible : {exc}") from exc
    titre = await page.title()
    return f"{action} fait.\n# {titre}\n{page.url}"


@tool(
    "navigateur_capture",
    "Enregistre une capture d'ecran dans mon workspace, pour que le patron la recupere avec "
    "/get. Sans numero d'onglet, capture le mien ; avec, capture un onglet du patron sans y "
    "toucher.",
    {
        "nom": {"type": "string", "description": "Nom du fichier PNG (defaut capture.png)."},
        "onglet": {
            "type": "integer",
            "description": "Numero d'onglet donne par `navigateur_onglets`. Lecture seule.",
        },
        "pleine_page": {
            "type": "boolean",
            "description": "Capturer toute la hauteur de la page plutot que l'ecran visible.",
        },
    },
    [],
)
async def navigateur_capture(ctx: ToolContext, args: dict[str, Any]) -> str:
    nom = (str(args.get("nom") or "capture.png").strip() or "capture.png")
    if "/" in nom or "\\" in nom or ".." in nom:
        raise ToolError("`nom` doit etre un simple nom de fichier, sans chemin.")
    if not nom.lower().endswith(".png"):
        nom += ".png"
    numero = args.get("onglet")
    if numero is None:
        page = await _ma_page(creer=False)
    else:
        # 🚨 Onglet du patron : on le photographie, on ne le touche pas. Pas de goto, pas de
        # bring_to_front — ce serait deja modifier son ecran.
        nav = await _navigateur()
        contexte = await _contexte(nav)
        try:
            page = contexte.pages[int(numero)]
        except (IndexError, ValueError) as exc:
            raise ToolError(f"pas d'onglet numero {numero}.") from exc
    cible = ctx.workspace / nom
    try:
        await page.screenshot(path=str(cible), full_page=bool(args.get("pleine_page")))
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"capture impossible : {exc}") from exc
    taille = cible.stat().st_size // 1024
    return (f"capture enregistree : {nom} ({taille} Ko) — de « {page.url[:110]} ».\n"
            f"Le patron peut la recuperer avec /get {nom}")


@tool(
    "navigateur_fermer",
    "Ferme MON onglet. A faire quand j'ai fini : un onglet de trop fige la publication.",
    {},
    [],
)
async def navigateur_fermer(ctx: ToolContext, args: dict[str, Any]) -> str:
    page = _ETAT.get("page")
    if page is None or page.is_closed():
        _ETAT["page"] = None
        return "je n'avais pas d'onglet ouvert."
    try:
        await page.close()
    except Exception as exc:  # noqa: BLE001
        raise ToolError(f"fermeture impossible : {exc}") from exc
    _ETAT["page"] = None
    _ETAT["jeton"] = ""
    return "mon onglet est ferme."


TOOLS = (navigateur_onglets, navigateur_lire, navigateur_agir,
         navigateur_capture, navigateur_fermer)
