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
from .memory import Store
from .sandbox import resolve_in
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
            log.warning("HTML refuse par Telegram (%s) : repli en texte brut.", exc)
            for raw in formatting.plain(text):
                await _retrying(message.reply_text, raw)
            return


async def _retrying(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Rejoue un envoi sur les pannes transitoires. Les 400 remontent aussitot.

    L'ordre des clauses compte : dans python-telegram-bot, ``BadRequest`` derive
    de ``NetworkError``. Sans cette premiere clause, un balisage refuse serait
    rejoue quatre fois avec attente exponentielle avant de tomber en repli.
    """
    delay = 1.0
    for attempt in range(4):
        try:
            return await func(*args, **kwargs)
        except BadRequest:
            raise
        except RetryAfter as exc:
            await asyncio.sleep(float(exc.retry_after) + 0.5)
        except (TimedOut, NetworkError) as exc:
            if attempt == 3:
                raise
            log.warning("Envoi Telegram en echec (%s), nouvelle tentative.", exc)
            await asyncio.sleep(delay)
            delay *= 2
    return None


async def _quiet(coro: Any) -> None:
    """Execute une operation Telegram accessoire ; son echec n'a pas d'importance."""
    with contextlib.suppress(BadRequest, TimedOut, NetworkError, Forbidden, RetryAfter):
        await coro


# --------------------------------------------------------------------------- #
# Application                                                                  #
# --------------------------------------------------------------------------- #


def build_application(settings: Settings) -> Application:
    registry = build_registry(
        enable_shell=settings.enable_shell, enable_web=settings.enable_web
    )
    store = Store(settings.data_dir / "hermes.db")
    agent = Agent(settings, registry, store)
    running: dict[int, asyncio.Task[Any]] = {}

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
        busy = chat_id in running and not running[chat_id].done()
        await send(
            update.effective_message,
            f"Modele : `{current}`\n"
            f"{providers.describe(current.split(',')[0])}\n"
            f"Messages en memoire : {len(session.messages)}\n"
            f"Workspace : `{settings.workspace}`\n"
            f"Outils : {len(registry)}\n"
            f"Tache en cours : {'oui' if busy else 'non'}",
        )

    async def cmd_reset(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        await store.clear(update.effective_chat.id)
        await send(update.effective_message, "Historique efface.")

    async def cmd_stop(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        task = running.get(update.effective_chat.id)
        if task and not task.done():
            task.cancel()
            await send(update.effective_message, "Tache interrompue.")
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
        handle = await context.bot.get_file(source.file_id)
        await handle.download_to_drive(custom_path=str(target))
        await send(
            message,
            f"Fichier recu : `{target.relative_to(settings.workspace)}` "
            f"({target.stat().st_size} octets). Dis-moi quoi en faire.",
        )
        if message.caption:
            await _handle_text(update, context, message.caption)

    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = (update.effective_message.text or "").strip()
        if text:
            await _handle_text(update, context, text)

    async def _handle_text(
        update: Update, context: ContextTypes.DEFAULT_TYPE, text: str
    ) -> None:
        message = update.effective_message
        chat_id = update.effective_chat.id

        previous = running.get(chat_id)
        if previous and not previous.done():
            await send(
                message,
                "Une tache tourne deja pour ce chat ; elle sera traitee d'abord. "
                "Utilise /stop pour l'interrompre.",
            )

        status = await _retrying(message.reply_text, "⏳ …")
        typing = asyncio.create_task(_keep_typing(context, chat_id))

        async def progress(line: str) -> None:
            if status is not None:
                await _quiet(status.edit_text(f"⚙️ {line}"))

        task = asyncio.create_task(agent.respond(chat_id, text, progress))
        running[chat_id] = task
        try:
            run = await task
        except asyncio.CancelledError:
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
            running.pop(chat_id, None)

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
        MessageHandler(filters.Document.ALL | filters.PHOTO, guarded(on_document))
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, guarded(on_text)))
    application.add_error_handler(on_error)

    async def post_init(app: Application) -> None:
        await app.bot.set_my_commands(COMMANDS)
        me = await app.bot.get_me()
        log.info(
            "Hermes en ligne : @%s — modeles %s — %d outils",
            me.username,
            ", ".join(settings.model_chain),
            len(registry),
        )

    application.post_init = post_init
    return application


def run(settings: Settings) -> None:
    """Demarre le bot en long polling, jusqu'a interruption."""
    application = build_application(settings)
    # drop_pending_updates : au redemarrage, on ignore la file accumulee plutot
    # que de rejouer d'anciennes consignes hors contexte.
    application.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
