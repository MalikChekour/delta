"""Tests de la LISTE BLANCHE — le seul rempart devant un shell privilegie.

🚨 POURQUOI CE FICHIER EXISTE. L'agent tourne en tache planifiee sous le compte **SYSTEM**,
avec `RunLevel: Highest`. Son outil `shell` execute donc n'importe quelle commande avec les
droits les plus eleves de la machine, et `cwd=workspace` ne confine rien : un `cd C:\\` marche.
Entre un message Telegram et ce shell, il n'y a **qu'une seule verification** : `authorized()`.

Cette verification n'avait **aucun test** avant le 10/09 — les quinze tests de `test_bot.py`
portaient sur le rendu HTML. La piece la plus critique du projet etait la moins couverte.

Les tests ci-dessous s'attachent a ce qui ferait vraiment des degats :
un inconnu qui passe, un handler qu'on aurait oublie de proteger, et la fenetre
d'appropriation qui resterait ouverte apres le premier proprietaire.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import pytest
from telegram.ext import CommandHandler, MessageHandler

from hermes import bot
from hermes.config import Settings


@dataclass
class FauxUtilisateur:
    id: int
    username: str = "quelquun"


@dataclass
class FauxMessage:
    envoyes: list[str] = field(default_factory=list)

    async def reply_text(self, text, **kwargs):
        self.envoyes.append(text)
        return self


@dataclass
class FauxUpdate:
    effective_user: FauxUtilisateur | None
    effective_message: FauxMessage


PROPRIETAIRE = 7698730603
INTRUS = 111222333


def _reglages(base: Settings, **extra) -> Settings:
    """Repart des reglages de test communs (conftest) : `Settings` exige tous ses champs,
    et les recopier ici ferait deux listes a maintenir en parallele."""
    return replace(base, allowed_users=frozenset({PROPRIETAIRE}), **extra)


@pytest.fixture()
def application(settings):
    return bot.build_application(_reglages(settings))


def _handlers(app):
    for groupe in app.handlers.values():
        yield from groupe


def test_tous_les_handlers_sont_proteges(application):
    """🚨 LE TEST QUI COMPTE LE PLUS. Un seul handler ajoute sans `guarded` ouvrirait un
    shell SYSTEM a n'importe quel inconnu — et rien ne le signalerait. On verifie donc
    l'enrobage sur CHACUN, pas sur un echantillon."""
    vus = list(_handlers(application))
    assert vus, "aucun handler enregistre : le test ne verifie rien"
    for h in vus:
        assert isinstance(h, (CommandHandler, MessageHandler))
        # `guarded` renvoie une fonction nommee `wrapper` : c'est la signature de
        # l'enrobage. Un handler pose directement porterait son propre nom.
        assert h.callback.__name__ == "wrapper", (
            f"handler NON protege : {getattr(h.callback, '__name__', h.callback)}"
        )


async def test_un_inconnu_est_refuse(application):
    message = FauxMessage()
    update = FauxUpdate(FauxUtilisateur(INTRUS), message)
    for h in _handlers(application):
        message.envoyes.clear()
        await h.callback(update, None)
        assert message.envoyes, "un inconnu doit recevoir un refus explicite"
        assert "refus" in message.envoyes[0].lower()


async def test_un_message_sans_expediteur_est_refuse(application):
    """Telegram peut livrer un message sans `effective_user` (canal, message systeme).
    `authorized` doit rendre False, pas lever."""
    message = FauxMessage()
    update = FauxUpdate(None, message)
    for h in _handlers(application):
        await h.callback(update, None)          # ne doit pas lever


async def test_le_proprietaire_passe(application):
    """Le miroir du test precedent : une liste blanche qui refuse TOUT le monde
    passerait les tests ci-dessus tout en rendant le bot inutilisable."""
    message = FauxMessage()
    update = FauxUpdate(FauxUtilisateur(PROPRIETAIRE), message)
    aide = next(h for h in _handlers(application)
                if isinstance(h, CommandHandler) and "help" in h.commands)
    await aide.callback(update, None)
    assert message.envoyes and "refus" not in message.envoyes[0].lower()


async def test_l_appropriation_est_fermee_quand_un_proprietaire_existe(settings):
    """🚨 `HERMES_CLAIM_OWNER` permet au premier venu de s'approprier le bot. La fenetre
    doit se refermer des qu'un proprietaire existe — sinon un inconnu s'ajouterait
    lui-meme a la liste blanche et obtiendrait le shell."""
    app = bot.build_application(_reglages(settings, claim_owner=True))
    message = FauxMessage()
    update = FauxUpdate(FauxUtilisateur(INTRUS), message)
    aide = next(h for h in _handlers(app)
                if isinstance(h, CommandHandler) and "help" in h.commands)
    await aide.callback(update, None)
    assert "refus" in message.envoyes[0].lower(), (
        "un proprietaire existe deja : l'appropriation devait etre refusee"
    )
