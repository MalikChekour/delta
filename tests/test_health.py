"""Le silence est le pire symptome : ces tests couvrent sa detection."""

from __future__ import annotations

import asyncio
import time

from hermes.bot import BACKOFF_MAX, RETARD_NOTABLE, _retard, prochain_delai
from hermes.health import Heartbeat, surveiller


def test_aucun_battement(tmp_path):
    battements = Heartbeat(tmp_path / "hb")
    assert battements.age() is None
    assert not battements.vivant()


def test_battement_frais(tmp_path):
    battements = Heartbeat(tmp_path / "hb")
    battements.battre()
    assert battements.vivant()
    assert battements.age() is not None and battements.age() <= 2


def test_battement_perime(tmp_path):
    battements = Heartbeat(tmp_path / "hb")
    (tmp_path / "hb").write_text(str(int(time.time()) - 600))
    assert battements.age() > 500
    assert not battements.vivant()


def test_fichier_corrompu_vaut_absence(tmp_path):
    """Un fichier illisible ne doit pas passer pour un service en bonne sante."""
    battements = Heartbeat(tmp_path / "hb")
    (tmp_path / "hb").write_text("pas un horodatage")
    assert battements.age() is None
    assert not battements.vivant()


def test_ecriture_atomique(tmp_path):
    battements = Heartbeat(tmp_path / "hb")
    battements.battre()
    battements.battre()
    assert not (tmp_path / "hb.tmp").exists()


async def test_surveillance_bat_regulierement(tmp_path):
    battements = Heartbeat(tmp_path / "hb")
    tache = asyncio.create_task(
        surveiller(battements, lambda: True, lambda: None, intervalle=0.01)
    )
    await asyncio.sleep(0.05)
    tache.cancel()
    assert battements.vivant()


async def test_surveillance_detecte_la_surdite(tmp_path):
    """Boucle de reception arretee alors que le service tourne : le cas ou le
    bot ne repond plus sans que rien ne le signale."""
    alertes: list[bool] = []
    ecoute = {"actif": True}

    async def scenario():
        await surveiller(
            Heartbeat(tmp_path / "hb"),
            lambda: ecoute["actif"],
            lambda: alertes.append(True),
            intervalle=0.01,
        )

    tache = asyncio.create_task(scenario())
    await asyncio.sleep(0.03)
    assert alertes == []  # tant qu'on ecoute, rien ne se passe
    ecoute["actif"] = False
    await asyncio.wait_for(tache, timeout=1)
    assert alertes == [True]


async def test_surveillance_tolere_le_demarrage(tmp_path):
    """Au demarrage, la boucle n'ecoute pas encore : ce delai normal ne doit
    pas etre pris pour une panne."""
    alertes: list[bool] = []
    tache = asyncio.create_task(
        surveiller(Heartbeat(tmp_path / "hb"), lambda: False, lambda: alertes.append(True),
                   intervalle=5)
    )
    await asyncio.sleep(0.05)
    assert alertes == []
    tache.cancel()


def test_attente_avant_redemarrage_plafonnee():
    delai = 2.0
    for _ in range(20):
        delai = prochain_delai(delai)
    assert delai == BACKOFF_MAX


def test_retard_non_mentionne_si_recent():
    from datetime import datetime, timedelta, timezone

    class Message:
        date = datetime.now(timezone.utc) - timedelta(seconds=RETARD_NOTABLE - 30)

    assert _retard(Message()) == ""


def test_retard_mentionne_si_ancien():
    from datetime import datetime, timedelta, timezone

    class Message:
        date = datetime.now(timezone.utc) - timedelta(hours=3)

    assert "3 h" in _retard(Message())


def test_retard_sans_date():
    class Message:
        date = None

    assert _retard(Message()) == ""


async def test_avis_remis_a_chaque_proprietaire():
    from hermes import bot

    recus: list[tuple[int, str]] = []

    class FauxBot:
        async def send_message(self, chat_id, texte):
            recus.append((chat_id, texte))

    assert await bot.annoncer(FauxBot(), {2, 1}, "coucou") == 2
    assert recus == [(1, "coucou"), (2, "coucou")]


async def test_avis_non_remis_est_journalise_sans_bloquer(caplog):
    """Un proprietaire ayant bloque le bot ne doit pas empecher le demarrage,
    mais l'echec doit laisser une trace."""
    from hermes import bot

    class BotMuet:
        async def send_message(self, chat_id, texte):
            raise RuntimeError("bloque par l'utilisateur")

    with caplog.at_level("WARNING"):
        assert await bot.annoncer(BotMuet(), {1}, "coucou") == 0
    assert "Avis non remis" in caplog.text
