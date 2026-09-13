"""Interface Telegram.

Ce module ne contient aucune logique d'agent : il traduit des evenements
Telegram en appels a ``Agent``, et des reponses en messages valides. Toutes les
erreurs d'envoi y sont traitees, car c'est la couche ou une exception non
rattrapee ferait taire le bot sans que personne ne le sache.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from telegram import BotCommand, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import formatting, providers
from .agent import Agent, format_error
from .config import Settings
from .errors import ToolError
from .health import Heartbeat, surveiller
from .memory import Store
from .sandbox import disk_free, resolve_in
from .tools import build_registry

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand("help", "Aide et commandes"),
    BotCommand("model", "Voir ou changer de modele"),
    BotCommand("models", "Modeles et fournisseurs disponibles"),
    BotCommand("tools", "Outils dont dispose l'agent"),
    BotCommand("status", "Etat de la session"),
    BotCommand("stop", "Interrompre la tache en cours"),
    BotCommand("get", "Recuperer un fichier du workspace"),
    BotCommand("reset", "Effacer l'historique"),
]

HELP = """**Hermes** — agent autonome.

Ecris-moi une consigne en langage naturel. Je peux executer du shell et du
Python, lire et ecrire des fichiers, chercher sur le web et lire des pages.
Envoie-moi un fichier et je le depose dans mon workspace.

**Commandes**
/model — modele courant ; `/model venice` pour changer
/models — le catalogue et les routes disponibles
/tools — mes outils
/status — modele, historique, workspace
/stop — interrompre la tache en cours
/get `chemin` — te renvoyer un fichier du workspace
/reset — repartir de zero
"""


# --------------------------------------------------------------------------- #
# Envoi                                                                        #
# --------------------------------------------------------------------------- #


async def send(message: Any, markdown: str) -> None:
    """Envoie une reponse, decoupee et rendue en HTML, avec repli en texte brut."""
    text = (markdown or "").strip() or "_(reponse vide)_"
    for part in formatting.chunks(text):
        if not part.strip():
            continue
        try:
            await _retrying(message.reply_text, part, parse_mode=ParseMode.HTML)
        except BadRequest as exc:
            # Repli sur ce seul morceau : renvoyer tout le texte dupliquerait ce
            # qui est deja parti.
            log.warning("HTML refuse par Telegram (%s) : repli en texte brut.", exc)
            await _retrying(message.reply_text, formatting.strip_tags(part))


async def _retrying(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Rejoue un envoi sur les pannes transitoires. Les 400 remontent aussitot.

    L'ordre des clauses compte : dans python-telegram-bot, ``BadRequest`` derive
    de ``NetworkError``. Sans cette premiere clause, un balisage refuse serait
    rejoue quatre fois avec attente exponentielle avant de tomber en repli.
    """
    delay = 1.0
    derniere: Exception | None = None
    for attempt in range(4):
        try:
            return await func(*args, **kwargs)
        except BadRequest:
            raise
        except RetryAfter as exc:
            derniere = exc
            await asyncio.sleep(float(exc.retry_after) + 0.5)
        except (TimedOut, NetworkError) as exc:
            derniere = exc
            if attempt == 3:
                raise
            log.warning("Envoi Telegram en echec (%s), nouvelle tentative.", exc)
            await asyncio.sleep(delay)
            delay *= 2
    # Tentatives epuisees : on leve plutot que de renvoyer None, sans quoi le
    # message serait perdu sans que personne ne le sache.
    raise derniere or NetworkError("envoi impossible apres plusieurs tentatives")


class Taches:
    """Taches d'agent en cours, groupees par chat.

    Les messages envoyes coup sur coup s'empilent derriere le verrou du chat :
    il y a donc plusieurs taches vivantes a la fois, et /stop doit toutes les
    liberer, pas seulement la derniere inscrite.
    """

    def __init__(self) -> None:
        self._par_chat: dict[int, set[asyncio.Task[Any]]] = defaultdict(set)

    def ajouter(self, chat_id: int, tache: asyncio.Task[Any]) -> None:
        self._par_chat[chat_id].add(tache)

    def retirer(self, chat_id: int, tache: asyncio.Task[Any]) -> None:
        self._par_chat[chat_id].discard(tache)
        if not self._par_chat[chat_id]:
            self._par_chat.pop(chat_id, None)

    def actives(self, chat_id: int) -> list[asyncio.Task[Any]]:
        return [tache for tache in self._par_chat.get(chat_id, ()) if not tache.done()]

    def interrompre(self, chat_id: int) -> int:
        """Annule tout ce qui tourne pour ce chat. Renvoie le nombre annule."""
        taches = self.actives(chat_id)
        for tache in taches:
            tache.cancel()
        return len(taches)


#: Au-dela, une reponse tardive merite d'etre expliquee.
RETARD_NOTABLE = 120


def _retard(message: Any) -> str:
    """Mentionne l'age du message quand la reponse arrive longtemps apres.

    Un message envoye pendant une coupure est traite au redemarrage : sans
    cette mention, la reponse tombe sans contexte, des heures plus tard.
    """
    envoye = getattr(message, "date", None)
    if envoye is None:
        return ""
    try:
        age = (datetime.now(timezone.utc) - envoye).total_seconds()
    except TypeError:
        return ""
    if age < RETARD_NOTABLE:
        return ""
    if age < 3600:
        delai = f"{age / 60:.0f} min"
    elif age < 86400:
        delai = f"{age / 3600:.0f} h"
    else:
        delai = f"{age / 86400:.0f} j"
    return f" (ton message datait d'il y a {delai}, je le traite maintenant)"


async def _quiet(coro: Any) -> None:
    """Execute une operation Telegram accessoire ; son echec n'a pas d'importance."""
    with contextlib.suppress(BadRequest, TimedOut, NetworkError, Forbidden, RetryAfter):
        await coro


async def annoncer(bot: Any, destinataires: set[int], texte: str) -> int:
    """Previent les proprietaires. Renvoie le nombre d'avis remis.

    C'est le message qui dit « je suis revenu » : son echec ne doit pas
    interrompre le demarrage, mais il ne doit pas non plus passer inapercu —
    sans quoi on retombe sur un bot silencieux dont personne ne sait rien.
    """
    remis = 0
    for destinataire in sorted(destinataires):
        try:
            await bot.send_message(destinataire, texte)
            remis += 1
        except Exception as exc:  # noqa: BLE001 - un avis raté ne bloque rien
            log.warning("Avis non remis a %s : %s", destinataire, exc)
    return remis


# --------------------------------------------------------------------------- #
# Application                                                                  #
# --------------------------------------------------------------------------- #


def build_application(settings: Settings, panne: dict[str, bool] | None = None) -> Application:
    """Assemble l'application Telegram.

    ``panne`` est le drapeau partage avec ``run`` : la surveillance y inscrit
    une demande de redemarrage quand elle constate que la boucle de reception
    s'est arretee.
    """
    registry = build_registry(
        enable_shell=settings.enable_shell, enable_web=settings.enable_web
    )
    store = Store(settings.data_dir / "hermes.db")
    agent = Agent(settings, registry, store)
    taches = Taches()

    # La liste blanche du fichier .env, plus les proprietaires enregistres en base.
    owners: set[int] = set(settings.allowed_users) | store.owners()

    def authorized(update: Update) -> bool:
        user = update.effective_user
        return bool(user and user.id in owners)

    async def claim(update: Update) -> bool:
        """Appropriation au premier contact, si elle est activee et libre.

        Rend le demarrage possible sans connaitre son identifiant numerique. La
        fenetre se referme des le premier /start : le proprietaire est ecrit en
        base, et plus personne d'autre ne peut la revendiquer.
        """
        if not settings.claim_owner or owners:
            return False
        user = update.effective_user
        if user is None:
            return False
        if not await asyncio.to_thread(store.claim, user.id, user.username):
            owners.update(store.owners())
            return False
        owners.add(user.id)
        log.warning("Proprietaire enregistre : %s (@%s)", user.id, user.username)
        await _quiet(
            update.effective_message.reply_text(
                f"Bot approprie : tu en es le seul utilisateur autorise (id {user.id}).\n"
                "Ajoute cet identifiant a HERMES_ALLOWED_USERS dans .env pour le figer."
            )
        )
        return True

    async def refuse(update: Update) -> None:
        user = update.effective_user
        log.warning(
            "Acces refuse : %s (%s)", getattr(user, "id", "?"), getattr(user, "username", "?")
        )
        await _quiet(
            update.effective_message.reply_text(
                "Acces refuse.\n"
                f"Ton identifiant Telegram est {getattr(user, 'id', '?')} — ajoute-le a "
                "HERMES_ALLOWED_USERS puis redemarre Hermes."
            )
        )

    def guarded(handler: Any) -> Any:
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if update.effective_message is None:
                return
            if not authorized(update):
                await claim(update)
                if not authorized(update):
                    await refuse(update)
                    return
            await handler(update, context)

        return wrapper

    # -- commandes ---------------------------------------------------------- #

    async def cmd_help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await send(update.effective_message, HELP)

    async def cmd_models(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        lines = ["**Modeles**", ""]
        for alias in providers.MODELS.values():
            lines.append(f"`{alias.name}` — {alias.description}")
            lines.append(f"  {providers.describe(alias.name)}")
        lines += ["", "**Fournisseurs**", ""]
        for name, spec in providers.PROVIDERS.items():
            state = "✅ configure" if spec.configured else f"⚪ {spec.api_key_env} absente"
            lines.append(f"`{name}` — {state}")
        lines += ["", "✅ = route utilisable. `/model <nom>` pour basculer."]
        await send(update.effective_message, "\n".join(lines))

    async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        session = await store.load(chat_id)
        current = session.model or ", ".join(settings.model_chain)
        if not context.args:
            await send(
                update.effective_message,
                f"Modele courant : `{current}`\n{providers.describe(current.split(',')[0])}\n\n"
                "Pour changer : `/model glm-4.7-heretic`, `/model venice`, "
                "`/model venice:venice-uncensored`, ou `/model auto` pour revenir a la "
                "chaine par defaut.",
            )
            return

        choice = " ".join(context.args).strip()
        if choice.lower() in {"auto", "defaut", "default", "reset"}:
            await store.set_model(chat_id, None)
            await send(
                update.effective_message,
                f"Retour a la chaine par defaut : `{', '.join(settings.model_chain)}`.",
            )
            return

        routes = providers.resolve(choice)
        if not routes:
            await send(
                update.effective_message,
                f"Aucune route pour `{choice}`. Voir /models.",
            )
            return
        if not any(route.usable for route in routes):
            await send(
                update.effective_message,
                f"`{choice}` n'a aucune route utilisable :\n{providers.describe(choice)}\n"
                "Renseigne la cle API correspondante dans .env.",
            )
            return
        await store.set_model(chat_id, choice)
        await send(
            update.effective_message,
            f"Bascule sur `{choice}`.\n{providers.describe(choice)}",
        )

    async def cmd_tools(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        lines = [f"**{len(registry)} outils**", ""]
        for item in registry:
            first = item.description.split(".")[0]
            lines.append(f"`{item.name}` — {first}.")
        await send(update.effective_message, "\n".join(lines))

    async def cmd_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        session = await store.load(chat_id)
        current = session.model or ", ".join(settings.model_chain)
        busy = bool(taches.actives(chat_id))
        await send(
            update.effective_message,
            f"Modele : `{current}`\n"
            f"{providers.describe(current.split(',')[0])}\n"
            f"Messages en memoire : {len(session.messages)}\n"
            f"Workspace : `{settings.workspace}` ({disk_free(settings.workspace)})\n"
            f"Outils : {len(registry)}\n"
            f"Tache en cours : {'oui' if busy else 'non'}",
        )

    async def cmd_reset(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await store.clear(update.effective_chat.id)
        await send(update.effective_message, "Historique efface.")

    async def cmd_stop(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        annulees = taches.interrompre(update.effective_chat.id)
        if annulees:
            await send(update.effective_message, f"{annulees} tache(s) interrompue(s).")
        else:
            await send(update.effective_message, "Rien en cours.")

    async def cmd_get(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await send(update.effective_message, "Usage : `/get chemin/vers/fichier`")
            return
        try:
            target = resolve_in(settings.workspace, " ".join(context.args))
        except ToolError as exc:
            await send(update.effective_message, str(exc))
            return
        if not target.is_file():
            await send(update.effective_message, f"`{target.name}` n'existe pas.")
            return
        with target.open("rb") as handle:
            await _retrying(
                update.effective_message.reply_document, handle, filename=target.name
            )

    # -- messages ----------------------------------------------------------- #

    async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        source = message.document or (message.photo[-1] if message.photo else None)
        if source is None:
            return
        name = getattr(source, "file_name", None) or f"telegram_{source.file_unique_id}.bin"
        try:
            target = resolve_in(settings.workspace, name)
        except ToolError:
            target = settings.workspace / f"telegram_{source.file_unique_id}.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        handle = await context.bot.get_file(source.file_id)
        await handle.download_to_drive(custom_path=str(target))
        await send(
            message,
            f"Fichier recu : `{target.relative_to(settings.workspace)}` "
            f"({target.stat().st_size} octets). Dis-moi quoi en faire.",
        )
        if message.caption:
            await _handle_text(update, context, message.caption)

    async def on_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Un message vocal : on l'ecoute, puis on le traite comme s'il avait ete ecrit.

        🚨 La transcription est RENVOYEE AU PATRON avant d'agir. Whisper se trompe parfois
        d'un mot, et un mot change la consigne : mesure du 13/09, « les CINQ produits »
        devenait « les SAINS produits ». S'il voit ce que le bot a compris, il peut corriger
        tout de suite au lieu de decouvrir le malentendu dans le resultat.
        """
        message = update.effective_message
        source = message.voice or message.audio or message.video_note
        if source is None:
            return
        cible = settings.workspace / f"vocal_{source.file_unique_id}.ogg"
        cible.parent.mkdir(parents=True, exist_ok=True)
        poignee = await context.bot.get_file(source.file_id)
        await poignee.download_to_drive(custom_path=str(cible))
        try:
            from .tools.oreille import _transcris

            texte, _langue, duree = await asyncio.to_thread(_transcris, cible, None)
        except Exception as exc:  # noqa: BLE001
            await send(message, f"Je n'ai pas pu ecouter ce message : {exc}")
            return
        if not texte:
            await send(message, "Je n'ai entendu aucune parole dans ce message.")
            return
        await send(message, f"🎧 J'ai entendu ({duree:.0f} s) :\n« {texte} »")
        await _handle_text(update, context, texte)

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (update.effective_message.text or "").strip()
        if text:
            await _handle_text(update, context, text)

    async def _handle_text(
        update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
    ) -> None:
        message = update.effective_message
        chat_id = update.effective_chat.id

        if taches.actives(chat_id):
            await send(
                message,
                "Une tache tourne deja pour ce chat ; elle sera traitee d'abord. "
                "Utilise /stop pour l'interrompre.",
            )

        # Le message d'attente est un confort : s'il echoue, on traite quand meme
        # la demande plutot que de la perdre.
        status = None
        with contextlib.suppress(Exception):
            status = await _retrying(message.reply_text, f"⏳ …{_retard(message)}")
        typing = asyncio.create_task(_keep_typing(context, chat_id))

        async def progress(line: str) -> None:
            if status is not None:
                await _quiet(status.edit_text(f"⚙️ {line}"))

        task = asyncio.create_task(agent.respond(chat_id, text, progress))
        taches.ajouter(chat_id, task)
        try:
            run = await task
        except asyncio.CancelledError:
            # Si c'est nous qu'on annule — arret du bot — la propagation doit se
            # poursuivre ; seule une annulation demandee par /stop se rattrape.
            if not task.cancelled():
                raise
            if status is not None:
                await _quiet(status.edit_text("⏹ Interrompu."))
            return
        except Exception as exc:  # noqa: BLE001 - tout echec doit etre annonce
            log.exception("Tour en echec")
            if status is not None:
                await _quiet(status.delete())
            await send(message, f"❌ Echec.\n\n{format_error(exc)}")
            return
        finally:
            typing.cancel()
            taches.retirer(chat_id, task)

        if status is not None:
            await _quiet(status.delete())

        footer = ""
        if run.tool_calls:
            footer = f"\n\n_{run.tool_calls} appel(s) d'outil · {run.route}_"
        await send(message, run.text + footer)

    async def _keep_typing(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
        """Maintient l'indicateur « en train d'ecrire » pendant un long tour."""
        try:
            while True:
                await _quiet(context.bot.send_chat_action(chat_id, ChatAction.TYPING))
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        error = context.error
        if isinstance(error, (NetworkError, TimedOut, RetryAfter)):
            log.warning("Incident reseau Telegram : %s", error)
            return
        log.exception("Erreur non rattrapee", exc_info=error)
        if isinstance(update, Update) and update.effective_message and authorized(update):
            await _quiet(
                update.effective_message.reply_text(f"❌ Erreur interne : {format_error(error)}")
            )

    # -- assemblage --------------------------------------------------------- #

    application = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .rate_limiter(AIORateLimiter())
        .concurrent_updates(True)
        .build()
    )

    application.add_handler(CommandHandler(["start", "help"], guarded(cmd_help)))
    application.add_handler(CommandHandler("models", guarded(cmd_models)))
    application.add_handler(CommandHandler("model", guarded(cmd_model)))
    application.add_handler(CommandHandler("tools", guarded(cmd_tools)))
    application.add_handler(CommandHandler("status", guarded(cmd_status)))
    application.add_handler(CommandHandler("reset", guarded(cmd_reset)))
    application.add_handler(CommandHandler("stop", guarded(cmd_stop)))
    application.add_handler(CommandHandler("get", guarded(cmd_get)))
    application.add_handler(
        MessageHandler(filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE,
                       guarded(on_voice))
    )
    application.add_handler(
        MessageHandler(filters.Document.ALL | filters.PHOTO, guarded(on_document))
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guarded(on_text)))
    application.add_error_handler(on_error)

    battements = Heartbeat(settings.data_dir / "heartbeat")
    surveillance: dict[str, asyncio.Task[None] | None] = {"tache": None}

    async def post_init(app: Application) -> None:
        await app.bot.set_my_commands(COMMANDS)
        me = await app.bot.get_me()
        log.info(
            "Hermes en ligne : @%s — modeles %s — %d outils",
            me.username,
            ", ".join(settings.model_chain),
            len(registry),
        )

        def en_ecoute() -> bool:
            return bool(app.updater and app.updater.running)

        def sur_panne() -> None:
            if panne is not None:
                panne["redemarrer"] = True
            app.stop_running()

        surveillance["tache"] = asyncio.create_task(
            surveiller(battements, en_ecoute, sur_panne)
        )

        if settings.announce:
            # Savoir que le bot est revenu vaut mieux que de le deviner en lui
            # ecrivant dans le vide.
            await annoncer(
                app.bot,
                owners,
                f"Hermes est en ligne — {', '.join(settings.model_chain)}, "
                f"{len(registry)} outils.",
            )

    async def post_shutdown(app: Application) -> None:
        tache = surveillance["tache"]
        if tache is not None:
            tache.cancel()
        if settings.announce:
            await annoncer(app.bot, owners, "Hermes s'arrete.")

    application.post_init = post_init
    application.post_shutdown = post_shutdown
    return application


#: Attente initiale avant un redemarrage automatique, doublee a chaque echec.
BACKOFF_INITIAL = 2.0
BACKOFF_MAX = 60.0


def prochain_delai(delai: float) -> float:
    return min(delai * 2, BACKOFF_MAX)


def run(settings: Settings) -> None:
    """Demarre le bot, et le maintient en vie.

    Une exception non rattrapee ou une boucle de reception arretee ne doivent
    pas laisser un bot muet : on relance, avec une attente qui double a chaque
    echec pour ne pas marteler l'API si la panne est durable. Seul un arret
    demande — Ctrl-C, SIGTERM — met fin a la boucle.

    Cette supervision interne complete, sans la remplacer, celle de Docker ou
    de systemd : elle protege aussi ceux qui lancent `hermes run` a la main.
    """
    panne = {"redemarrer": False}
    delai = BACKOFF_INITIAL

    while True:
        panne["redemarrer"] = False
        application = build_application(settings, panne)
        try:
            # Par defaut, les messages recus pendant un arret sont traites au
            # redemarrage. Les jeter donnerait un bot qui ignore une demande
            # sans rien dire — le pire des symptomes. HERMES_DROP_PENDING=1
            # pour l'inverse, apres une longue interruption ou d'anciennes
            # consignes n'ont plus de sens.
            application.run_polling(
                drop_pending_updates=settings.drop_pending,
                allowed_updates=Update.ALL_TYPES,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:  # noqa: BLE001 - toute panne doit mener a un redemarrage
            log.exception("Le service s'est interrompu sur une erreur")
            panne["redemarrer"] = True

        if not panne["redemarrer"]:
            log.info("Arret demande.")
            return

        log.warning("Redemarrage dans %.0f s.", delai)
        time.sleep(delai)
        delai = prochain_delai(delai)
