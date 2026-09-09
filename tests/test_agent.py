from __future__ import annotations

import asyncio

import pytest

from hermes.agent import Agent
from hermes.errors import ProviderError
from hermes.memory import Store
from hermes.tools import Registry, Tool
from tests.fakes import FakeRouter, text_reply, tool_reply


def registre(fonction) -> Registry:
    registry = Registry()
    registry.add(
        Tool(
            name="essai",
            description="Outil de test.",
            parameters={"type": "object", "properties": {"x": {"type": "string"}}},
            handler=fonction,
        )
    )
    return registry


def agent_avec(settings, replies, registry=None) -> tuple[Agent, FakeRouter]:
    router = FakeRouter(replies)
    agent = Agent(settings, registry or Registry(), Store(settings.data_dir / "t.db"))
    agent.router = lambda chain: router  # type: ignore[assignment]
    return agent, router


async def test_reponse_simple(settings):
    agent, _ = agent_avec(settings, [text_reply("bonjour")])
    run = await agent.respond(1, "salut")
    assert run.text == "bonjour"
    assert run.tool_calls == 0


async def test_boucle_avec_outil(settings):
    appels = []

    async def handler(ctx, args):
        appels.append(args)
        return "42"

    agent, router = agent_avec(
        settings, [tool_reply("essai", '{"x": "a"}'), text_reply("le resultat est 42")],
        registre(handler),
    )
    run = await agent.respond(1, "calcule")
    assert run.text == "le resultat est 42"
    assert run.tool_calls == 1
    assert appels == [{"x": "a"}]
    # Le second appel au modele doit contenir le resultat de l'outil.
    dernier = router.calls[-1]
    assert dernier[-1] == {"role": "tool", "tool_call_id": "c1", "content": "42"}


async def test_outil_en_erreur_ne_casse_pas_la_boucle(settings):
    async def handler(ctx, args):
        raise RuntimeError("ca casse")

    agent, router = agent_avec(
        settings, [tool_reply("essai"), text_reply("j'ai vu l'erreur")], registre(handler)
    )
    run = await agent.respond(1, "vas-y")
    assert run.text == "j'ai vu l'erreur"
    assert "ca casse" in router.calls[-1][-1]["content"]


async def test_outil_inconnu_est_rapporte_au_modele(settings):
    agent, router = agent_avec(settings, [tool_reply("fantome"), text_reply("compris")])
    await agent.respond(1, "vas-y")
    assert "outil inconnu" in router.calls[-1][-1]["content"]


async def test_arguments_json_invalides(settings):
    async def handler(ctx, args):
        return "jamais"

    agent, router = agent_avec(
        settings, [tool_reply("essai", "{pas du json"), text_reply("je corrige")], registre(handler)
    )
    await agent.respond(1, "vas-y")
    assert "JSON invalides" in router.calls[-1][-1]["content"]


async def test_limite_d_iterations(settings):
    async def handler(ctx, args):
        return "encore"

    replies = [tool_reply("essai", call_id=f"c{i}") for i in range(settings.max_tool_iterations)]
    agent, _ = agent_avec(settings, replies, registre(handler))
    run = await agent.respond(1, "boucle")
    assert run.truncated
    assert "limite" in run.text


async def test_historique_persiste_et_reste_conforme(settings):
    async def handler(ctx, args):
        return "ok"

    agent, _ = agent_avec(
        settings, [tool_reply("essai"), text_reply("fini")], registre(handler)
    )
    await agent.respond(7, "premier")
    session = await agent.store.load(7)
    roles = [m["role"] for m in session.messages]
    assert roles == ["user", "assistant", "tool", "assistant"]


async def test_echec_du_modele_ne_corrompt_pas_l_historique(settings):
    agent, _ = agent_avec(settings, [ProviderError("plus de route")])
    with pytest.raises(ProviderError):
        await agent.respond(3, "salut")
    session = await agent.store.load(3)
    # Le message utilisateur est conserve, sans appel d'outil pendant.
    assert [m["role"] for m in session.messages] == ["user"]


async def test_echec_apres_un_appel_laisse_un_historique_valide(settings):
    async def handler(ctx, args):
        return "ok"

    agent, _ = agent_avec(
        settings, [tool_reply("essai"), ProviderError("coupure")], registre(handler)
    )
    with pytest.raises(ProviderError):
        await agent.respond(4, "vas-y")
    session = await agent.store.load(4)
    assert [m["role"] for m in session.messages] == ["user", "assistant", "tool"]


async def test_tours_simultanes_sont_serialises(settings):
    ordre = []

    async def handler(ctx, args):
        ordre.append("debut")
        await asyncio.sleep(0.05)
        ordre.append("fin")
        return "ok"

    agent, _ = agent_avec(
        settings,
        [tool_reply("essai"), text_reply("un"), tool_reply("essai"), text_reply("deux")],
        registre(handler),
    )
    await asyncio.gather(agent.respond(9, "a"), agent.respond(9, "b"))
    # Sans verrou par chat, les deux tours s'entrelaceraient.
    assert ordre == ["debut", "fin", "debut", "fin"]
    session = await agent.store.load(9)
    assert [m["role"] for m in session.messages].count("user") == 2


async def test_reponse_vide_du_modele_est_expliquee(settings):
    """Un modele renvoie parfois ni texte ni appel d'outil : sans garde-fou,
    l'utilisateur ne verrait rien et croirait a une panne."""
    agent, _ = agent_avec(settings, [text_reply("   ")])
    run = await agent.respond(1, "salut")
    assert "n'a rien renvoye" in run.text


async def test_reponse_tronquee_est_signalee(settings):
    vide = text_reply("")
    vide.finish_reason = "length"
    agent, _ = agent_avec(settings, [vide])
    run = await agent.respond(1, "salut")
    assert "jetons" in run.text
