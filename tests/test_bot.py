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
    await bot.send(message, "**gras** et `code`")
    assert message.envoyes and message.envoyes[0][1] is None
    texte = message.envoyes[0][0]
    assert "gras" in texte and "code" in texte and "<" not in texte


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


async def test_repli_n_envoie_pas_deux_fois_les_morceaux_valides():
    """Un seul morceau refuse ne doit pas faire reenvoyer toute la reponse."""

    class RefuseLeSecond(FauxMessage):
        def __init__(self):
            super().__init__()
            self.vus = 0

        async def reply_text(self, text, parse_mode=None, **kwargs):
            if parse_mode is not None:
                self.vus += 1
                if self.vus == 2:
                    raise BadRequest("can't parse entities")
            self.envoyes.append((text, parse_mode))
            return self

    message = RefuseLeSecond()
    # Lignes distinctes : sans cela, des morceaux identiques masqueraient un
    # eventuel doublon.
    await bot.send(message, "\n".join(f"ligne {i} " + "z" * 90 for i in range(200)))
    premiers = [texte for texte, mode in message.envoyes if mode == "HTML"]
    assert len(premiers) == len(set(premiers)), "un morceau a ete envoye deux fois"


async def test_envoi_impossible_leve_au_lieu_de_perdre_le_message(monkeypatch):
    from telegram.error import TimedOut

    class ToujoursHorsService(FauxMessage):
        async def reply_text(self, text, parse_mode=None, **kwargs):
            raise TimedOut()

    async def faux_sleep(duree):
        return None

    monkeypatch.setattr(bot.asyncio, "sleep", faux_sleep)
    with pytest.raises(TimedOut):
        await bot.send(ToujoursHorsService(), "coucou")


async def test_registre_de_taches():
    """Plusieurs tours peuvent attendre en meme temps derriere le verrou du
    chat : /stop doit tous les interrompre."""
    import asyncio

    taches = bot.Taches()

    async def dort():
        await asyncio.sleep(60)

    trois = [asyncio.create_task(dort()) for _ in range(3)]
    for tache in trois:
        taches.ajouter(7, tache)
    assert len(taches.actives(7)) == 3
    assert taches.actives(8) == []

    assert taches.interrompre(7) == 3
    await asyncio.gather(*trois, return_exceptions=True)
    assert all(tache.cancelled() for tache in trois)
    assert taches.actives(7) == []
    assert taches.interrompre(7) == 0

    for tache in trois:
        taches.retirer(7, tache)
    assert taches.actives(7) == []


async def test_une_tache_terminee_n_est_plus_active():
    import asyncio

    taches = bot.Taches()
    finie = asyncio.create_task(asyncio.sleep(0))
    await finie
    taches.ajouter(1, finie)
    assert taches.actives(1) == []
