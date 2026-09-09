from __future__ import annotations

import pytest

from hermes import config
from hermes.errors import ConfigError


@pytest.fixture(autouse=True)
def base(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_WORKSPACE", str(tmp_path / "w"))
    monkeypatch.setenv("HERMES_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:abc")
    monkeypatch.setenv("HERMES_ALLOWED_USERS", "7")


def test_valeurs_par_defaut():
    settings = config.load()
    assert settings.model_chain == ("glm-4.7-heretic", "venice")
    assert settings.allowed_users == frozenset({7})
    assert settings.workspace.is_dir() and settings.data_dir.is_dir()


def test_token_manquant(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        config.load()


def test_liste_blanche_vide(monkeypatch):
    monkeypatch.setenv("HERMES_ALLOWED_USERS", "")
    with pytest.raises(ConfigError, match="HERMES_ALLOWED_USERS"):
        config.load()


def test_liste_blanche_vide_toleree_avec_appropriation(monkeypatch):
    monkeypatch.setenv("HERMES_ALLOWED_USERS", "")
    monkeypatch.setenv("HERMES_CLAIM_OWNER", "1")
    assert config.load().claim_owner is True


def test_identifiant_non_numerique(monkeypatch):
    monkeypatch.setenv("HERMES_ALLOWED_USERS", "@moi")
    with pytest.raises(ConfigError, match="identifiant Telegram"):
        config.load()


def test_separateurs_multiples(monkeypatch):
    monkeypatch.setenv("HERMES_ALLOWED_USERS", "1, 2;3")
    assert config.load().allowed_users == frozenset({1, 2, 3})


def test_entier_invalide(monkeypatch):
    monkeypatch.setenv("HERMES_MAX_TOKENS", "beaucoup")
    with pytest.raises(ConfigError, match="entier"):
        config.load()


def test_entier_sous_le_minimum(monkeypatch):
    monkeypatch.setenv("HERMES_MAX_TOKENS", "10")
    with pytest.raises(ConfigError, match="au moins"):
        config.load()


def test_chaine_de_modeles_personnalisee(monkeypatch):
    monkeypatch.setenv("HERMES_MODEL", "venice:un-modele, glm-4.7")
    assert config.load().model_chain == ("venice:un-modele", "glm-4.7")


def test_prompt_systeme_par_fichier(monkeypatch, tmp_path):
    fichier = tmp_path / "p.txt"
    fichier.write_text("consigne maison")
    monkeypatch.setenv("HERMES_SYSTEM_PROMPT_FILE", str(fichier))
    assert config.load().system_prompt == "consigne maison"


def test_prompt_systeme_fichier_absent(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_SYSTEM_PROMPT_FILE", str(tmp_path / "absent.txt"))
    with pytest.raises(ConfigError, match="introuvable"):
        config.load()


def test_validation_des_modeles_sans_cle():
    with pytest.raises(ConfigError, match="Aucun modele servable"):
        config.load().validate_models()


def test_validation_des_modeles_avec_cle(monkeypatch):
    monkeypatch.setenv("VENICE_API_KEY", "x")
    config.load().validate_models()


def test_messages_en_attente_traites_par_defaut():
    """Les jeter donnerait un bot qui ignore une demande sans rien dire."""
    assert config.load().drop_pending is False


def test_messages_en_attente_ignorables(monkeypatch):
    monkeypatch.setenv("HERMES_DROP_PENDING", "1")
    assert config.load().drop_pending is True
