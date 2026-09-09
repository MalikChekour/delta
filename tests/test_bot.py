from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from telegram.error import BadRequest

from hermes import bot


@dataclass
class FauxMessage:
    """Message Telegram minimal : enregistre ce qui est envoye."""

    envoyes: list[tuple[str, str | None]] = field(default_factory=list)
    refuse_html: bool = False

    async def reply_text(self, text, parse_mode=None, **kwargs):
        if self.refuse_html and parse_mode is not None:
            raise BadRequest("can't parse entities")
        self.envoyes.append((text, parse_mode))
        return self


async def test_envoi_en_html():
    message = FauxMessage()
    await bot.send(message, "**gras**")
    assert message.envoyes == [("<b>gras</b>", "HTML")]


async def test_repli_en_texte_brut_si_html_refuse():
    message = FauxMessage(refuse_html=True)
    await bot.send(message, "**gras**")
    assert message.envoyes and message.envoyes[0][1] is None
    assert "**gras**" in message.envoyes[0][0]


async def test_html_refuse_ne_declenche_pas_de_reessai(monkeypatch):
    """BadRequest derive de NetworkError dans PTB : sans clause dediee, le repli
    n'arriverait qu'apres plusieurs secondes d'attente."""
    dormi: list[float] = []

    async def faux_sleep(duree):
        dormi.append(duree)

    monkeypatch.setattr(bot.asyncio, "sleep", faux_sleep)
    await bot.send(FauxMessage(refuse_html=True), "**gras**")
    assert dormi == []


async def test_reponse_vide_reste_un_message():
    message = FauxMessage()
    await bot.send(message, "   ")
    assert len(message.envoyes) == 1


async def test_message_long_decoupe():
    message = FauxMessage()
    await bot.send(message, "\n".join("ligne " + "z" * 90 for _ in range(200)))
    assert len(message.envoyes) > 1
    assert all(len(texte) <= 4096 for texte, _ in message.envoyes)


def test_application_construite(settings, monkeypatch):
    monkeypatch.setenv("VENICE_API_KEY", "x")
    application = bot.build_application(settings)
    commandes = {
        nom
        for handler in application.handlers[0]
        for nom in getattr(handler, "commands", None) or []
    }
    attendues = {"start", "help", "model", "models", "tools", "status", "reset", "stop", "get"}
    assert attendues <= commandes


def test_commandes_declarees_correspondent_aux_handlers(settings, monkeypatch):
    monkeypatch.setenv("VENICE_API_KEY", "x")
    application = bot.build_application(settings)
    enregistrees = {
        nom
        for handler in application.handlers[0]
        for nom in getattr(handler, "commands", None) or []
    }
    for commande in bot.COMMANDS:
        assert commande.command in enregistrees, commande.command


@pytest.mark.parametrize("texte", ["**a**", "```\ncode\n```", "<b>brut</b>", "* liste"])
async def test_aucun_markdown_ne_fait_echouer_l_envoi(texte):
    message = FauxMessage()
    await bot.send(message, texte)
    assert message.envoyes
