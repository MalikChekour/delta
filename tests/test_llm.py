"""Le routeur est la piece qui evite qu'une panne de fournisseur coupe le bot."""

from __future__ import annotations

import pytest

from hermes.errors import ConfigError, ProviderError
from hermes.llm import QUARANTINE_SECONDS, Router, ToolCall
from hermes.providers import Route
from hermes.tools import Registry
from tests.fakes import text_reply


class ClientStub:
    def __init__(self, route: Route, comportement: dict) -> None:
        self.route = route
        self.comportement = comportement
        self.appels = 0

    async def complete(self, system, messages, registry):
        self.appels += 1
        action = self.comportement.get(self.route)
        if isinstance(action, Exception):
            raise action
        reply = text_reply(f"reponse de {self.route}")
        reply.route = self.route
        return reply


def routeur(settings, comportement, chain=("glm-4.7-heretic",)):
    router = Router(settings, chain)
    stubs: dict[Route, ClientStub] = {}

    def fabrique(route: Route) -> ClientStub:
        stubs.setdefault(route, ClientStub(route, comportement))
        return stubs[route]

    router._client = fabrique  # type: ignore[assignment]
    return router, stubs


@pytest.fixture
def venice(monkeypatch):
    monkeypatch.setenv("VENICE_API_KEY", "cle-de-test")


async def test_bascule_sur_la_route_suivante(settings, venice):
    routes = [r for r in Router(settings, ("glm-4.7-heretic",)).candidates()]
    assert len(routes) >= 2
    router, stubs = routeur(settings, {routes[0]: ProviderError("morte", retryable=True)})
    reply = await router.complete("s", [], Registry())
    assert reply.route == routes[1]


async def test_route_en_echec_est_mise_en_quarantaine(settings, venice):
    routes = Router(settings, ("glm-4.7-heretic",)).candidates()
    router, stubs = routeur(settings, {routes[0]: ProviderError("morte")})
    await router.complete("s", [], Registry())
    await router.complete("s", [], Registry())
    # La premiere route n'est ressayee qu'une fois, pas a chaque message.
    assert stubs[routes[0]].appels == 1
    assert router._quarantine[routes[0]] > 0


async def test_route_qui_repond_devient_preferee(settings, venice):
    routes = Router(settings, ("glm-4.7-heretic",)).candidates()
    router, _ = routeur(settings, {routes[0]: ProviderError("morte")})
    await router.complete("s", [], Registry())
    assert router.candidates()[0] == routes[1]


async def test_toutes_les_routes_mortes(settings, venice):
    routes = Router(settings, ("glm-4.7-heretic",)).candidates()
    router, _ = routeur(settings, {route: ProviderError("morte") for route in routes})
    with pytest.raises(ProviderError) as exc:
        await router.complete("s", [], Registry())
    assert "Toutes les routes ont echoue" in str(exc.value)


async def test_aucune_cle_configuree(settings):
    router = Router(settings, ("glm-4.7-heretic",))
    with pytest.raises(ConfigError):
        await router.complete("s", [], Registry())


def test_une_exception_inattendue_n_arrete_pas_la_bascule(settings, venice):
    routes = Router(settings, ("glm-4.7-heretic",)).candidates()
    router, _ = routeur(settings, {routes[0]: ValueError("bug interne")})
    import asyncio

    reply = asyncio.run(router.complete("s", [], Registry()))
    assert reply.route == routes[1]


def test_arguments_d_outil_parses():
    assert ToolCall("1", "t", '{"a": 1}').parsed() == {"a": 1}
    assert ToolCall("1", "t", "").parsed() == {}
    assert ToolCall("1", "t", '"brut"').parsed() == {"value": "brut"}
    with pytest.raises(ValueError):
        ToolCall("1", "t", "{casse").parsed()


def test_quarantaine_raisonnable():
    assert 60 <= QUARANTINE_SECONDS <= 3600
