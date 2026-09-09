from __future__ import annotations

from hermes import providers


def test_alias_principal_contient_le_heretic(monkeypatch):
    routes = providers.resolve("glm-4.7-heretic")
    assert any("heretic" in route.model.lower() for route in routes)


def test_chaine_par_defaut():
    assert providers.DEFAULT_CHAIN[0] == "glm-4.7-heretic"
    assert "venice" in providers.DEFAULT_CHAIN


def test_forme_fournisseur_deux_points():
    routes = providers.resolve("venice:un-modele-a-moi")
    assert routes == (providers.Route("venice", "un-modele-a-moi"),)


def test_identifiant_brut_utilise_les_fournisseurs_configures(monkeypatch):
    monkeypatch.setenv("VENICE_API_KEY", "x")
    routes = providers.resolve("modele/inconnu")
    assert providers.Route("venice", "modele/inconnu") in routes


def test_specification_vide():
    assert providers.resolve("  ") == ()


def test_toutes_les_routes_visent_un_fournisseur_connu():
    for alias in providers.MODELS.values():
        for route in alias.routes:
            assert route.provider in providers.PROVIDERS, route


def test_fournisseur_distant_non_configure(monkeypatch):
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    assert not providers.PROVIDERS["venice"].configured


def test_serveur_local_sonde(monkeypatch):
    monkeypatch.setattr(providers, "local_server_up", lambda url, timeout=0.3: False)
    assert not providers.PROVIDERS["ollama"].configured
    monkeypatch.setattr(providers, "local_server_up", lambda url, timeout=0.3: True)
    assert providers.PROVIDERS["ollama"].configured
