"""Interface Telegram d'Hermes."""

from __future__ import annotations

import html
import logging

from telegram import BotCommand, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import memory, providers
from .agent import Agent
from .config import Settings
from .sandbox import resolve_in
from .tools import build_registry

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4000

COMMANDS = [
    BotCommand("help", "Aide et liste des commandes"),
    BotCommand("model", "Voir ou changer de modele"),
    BotCommand("providers", "Lister les fournisseurs disponibles"),
    BotCommand("tools", "Lister les outils de l'agent"),
    BotCommand("status", "Etat de la session"),
    BotCommand("get", "Recuperer un fichier du workspace"),
    BotCommand("reset", "Effacer l'historique de la conversation"),
]

HELP = """*Hermes* — agent autonome multi-modele.

Envoie-moi une consigne en langage naturel. Je peux :
• executer du shell et du Python dans mon workspace
• lire, ecrire et modifier des fichiers
• chercher sur le web et lire des pages

*Commandes*
/model — voir le modele courant
/model `<fournisseur>` `<modele>` — en changer, ex : `/model deepseek deepseek-chat`
/providers — la liste des fournisseurs cables
/tools — les outils dont je dispose
/get `<chemin>` — te renvoyer un fichier du workspace
/status — fournisseur, modele, taille de l'historique
/reset — repartir de zero
"""


def _chunks(text: str, size: int = TELEGRAM_LIMIT) -> list[str]:
    """Decoupe sur des frontieres de ligne, en preservant les blocs de code."""
    if len(text) <= size:
        return [text]
    out: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:  # ligne unique demesuree
            if current:
                out.append(current)
                current = ""
            out.append(line[:size])
            line = line[size:]
        if len(current) + len(line) > size:
            out.append(current)
            current = line
        else:
            current += line
    if current:
        out.append(current)
    return out


async def _send(update: Update, text: str) -> None:
    """Envoie en Markdown, avec repli en texte brut si le modele l'a mal forme."""
    if not text.strip():
        text = "(reponse vide)"
    for part in _chunks(text):
        try:
            await update.effective_message.reply_text(part, parse_mode=ParseMode.MARKDOWN)
        except BadRequest:
            await update.effective_message.reply_text(part)


def _guard(settings: Settings):
    """Filtre d'acces. Hermes ouvre un shell : la liste blanche n'est pas negociable."""

    def allowed(update: Update) -> bool:
        user = update.effective_user
        return bool(user and user.id in settings.allowed_users)

    return allowed


def build_application(settings: Settings) -> Application:
    registry = build_registry(settings)
    store = memory.Store(settings.data_dir / "hermes.db")
    agent = Agent(settings, registry, store)
    allowed = _guard(settings)

    async def deny(update: Update) -> None:
        user = update.effective_user
        log.warning("Acces refuse a %s (%s)", user.id if user else "?", user.username if user else "?")
        await update.effective_message.reply_text(
            "Acces refuse.\n"
            f"Ton identifiant Telegram est `{user.id if user else '?'}` — "
            "ajoute-le a HERMES_ALLOWED_USERS puis redemarre Hermes.",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update):
            return await deny(update)
        await _send(update, HELP)

    async def cmd_providers(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update):
            return await deny(update)
        lines = ["*Fournisseurs cables*", ""]
        for name, spec in providers.PROVIDERS.items():
            mark = "local" if spec.local else spec.api_key_env
            lines.append(f"• `{name}` — {mark}")
            if spec.notes:
                lines.append(f"  _{spec.notes}_")
            if spec.suggested:
                lines.append(f"  modeles : `{'`, `'.join(spec.suggested[:4])}`")
        await _send(update, "\n".join(lines))

    async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update):
            return await deny(update)
        chat_id = update.effective_chat.id
        session = await store.load(chat_id)
        if not context.args:
            await _send(
                update,
                f"Fournisseur : `{session.provider or settings.provider}`\n"
                f"Modele : `{session.model or settings.model}`\n\n"
                "Pour changer : `/model <fournisseur> <modele>`",
            )
            return
        name = context.args[0].lower()
        try:
            spec = providers.get(name)
        except KeyError as exc:
            await _send(update, f"❌ {exc}")
            return
        model = " ".join(context.args[1:]).strip() or spec.default_model
        if not model:
            await _send(update, f"Le fournisseur `{name}` n'a pas de modele par defaut.")
            return
        await store.set_model(chat_id, name, model)
        await _send(update, f"✅ Bascule sur `{name}` / `{model}`.")

    async def cmd_tools(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update):
            return await deny(update)
        lines = [f"*{len(registry)} outils*", ""]
        for tool in registry:
            lines.append(f"• `{tool.name}` — {tool.description.split('.')[0]}.")
        await _send(update, "\n".join(lines))

    async def cmd_status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update):
            return await deny(update)
        session = await store.load(update.effective_chat.id)
        await _send(
            update,
            f"Fournisseur : `{session.provider or settings.provider}`\n"
            f"Modele : `{session.model or settings.model}`\n"
            f"Messages en memoire : {len(session.messages)}\n"
            f"Workspace : `{settings.workspace}`\n"
            f"Recherche : `{settings.search_backend}`",
        )

    async def cmd_reset(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update):
            return await deny(update)
        await store.reset(update.effective_chat.id)
        await _send(update, "🧹 Historique efface.")

    async def cmd_get(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update):
            return await deny(update)
        if not context.args:
            await _send(update, "Usage : `/get chemin/vers/fichier`")
            return
        try:
            target = resolve_in(settings.workspace, " ".join(context.args))
        except ValueError as exc:
            await _send(update, f"❌ {exc}")
            return
        if not target.is_file():
            await _send(update, f"❌ `{target.name}` n'est pas un fichier du workspace.")
            return
        with target.open("rb") as fh:
            await update.effective_message.reply_document(fh, filename=target.name)

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update):
            return await deny(update)
        text = (update.effective_message.text or "").strip()
        if not text:
            return
        chat_id = update.effective_chat.id
        status = await update.effective_message.reply_text("⏳ ...")

        async def progress(line: str) -> None:
            try:
                await status.edit_text(f"⚙️ {line}")
            except BadRequest:
                pass  # message identique ou trop d'editions : sans consequence

        await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
        try:
            run = await agent.respond(chat_id, text, progress)
        except Exception as exc:
            log.exception("Le tour a echoue")
            await status.edit_text(f"❌ Echec : {html.escape(str(exc))[:900]}")
            return

        try:
            await status.delete()
        except BadRequest:
            pass

        footer = ""
        if run.tool_calls:
            footer = f"\n\n_{run.tool_calls} appel(s) d'outil_"
        await _send(update, run.text + footer)

    app = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .rate_limiter(AIORateLimiter())
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("providers", cmd_providers))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("tools", cmd_tools))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("get", cmd_get))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands(COMMANDS)
        me = await application.bot.get_me()
        log.info("Hermes en ligne : @%s", me.username)

    app.post_init = post_init
    return app
