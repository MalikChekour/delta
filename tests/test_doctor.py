import pytest

from hermes import doctor
from hermes.doctor import FAIL, OK, check_provider, check_telegram, check_workspace
from tests.fakes import TELEGRAM_HEALTHY, FakeServer, openai_reply


@pytest.fixture
def telegram(monkeypatch):
    servers = []

    def serve(routes):
        server = FakeServer(routes)
        monkeypatch.setattr(doctor, "TELEGRAM_API", server.__enter__())
        servers.append(server)
        return server

    yield serve
    for server in servers:
        server.__exit__()


async def test_healthy_telegram(settings, telegram):
    telegram(TELEGRAM_HEALTHY)
    checks = await check_telegram(settings)
    assert [c.status for c in checks] == [OK, OK, OK]
    assert "hermes_bot" in checks[0].detail


async def test_bad_token_is_named_as_such(settings, telegram):
    telegram({"/getMe": (401, {"ok": False, "description": "Unauthorized"})})
    checks = await check_telegram(settings)
    assert checks[0].status == FAIL
    assert "BotFather" in checks[0].detail


async def test_second_instance_is_detected(settings, telegram):
    """409 Conflict = un autre Hermes fait deja du polling. Cause n°1 d'un bot muet."""
    telegram({
        **TELEGRAM_HEALTHY,
        "/getUpdates": (409, {"ok": False, "description": "Conflict: terminated by other"}),
    })
    checks = await check_telegram(settings)
    conflict = next(c for c in checks if "Instance" in c.label)
    assert conflict.status == FAIL
    assert "AUTRE instance" in conflict.detail


async def test_active_webhook_is_detected(settings, telegram):
    """Un webhook capte les messages a la place du polling : le bot parait mort."""
    telegram({
        **TELEGRAM_HEALTHY,
        "/getWebhookInfo": (200, {"ok": True, "result": {"url": "https://ancien.example/hook"}}),
    })
    checks = await check_telegram(settings)
    mode = next(c for c in checks if "reception" in c.label)
    assert mode.status == FAIL
    assert "ancien.example" in mode.detail


async def test_unreachable_telegram_is_reported(settings, monkeypatch):
    monkeypatch.setattr(doctor, "TELEGRAM_API", "http://127.0.0.1:1")
    checks = await check_telegram(settings)
    assert checks[0].status == FAIL


async def test_provider_probe_succeeds(settings):
    with FakeServer({"/chat/completions": (200, openai_reply())}) as base:
        settings.provider = "custom"
        settings.custom_base_url = f"{base}/v1"
        settings.model = "fake"
        import os

        os.environ["CUSTOM_API_KEY"] = "k"
        check = await check_provider(settings)
    assert check.status == OK
    assert "tokens" in check.detail


async def test_provider_bad_key_gives_actionable_hint(settings):
    with FakeServer({"/chat/completions": (401, {"error": {"message": "Invalid API key"}})}) as base:
        settings.provider = "custom"
        settings.custom_base_url = f"{base}/v1"
        settings.model = "fake"
        import os

        os.environ["CUSTOM_API_KEY"] = "wrong"
        check = await check_provider(settings)
    assert check.status == FAIL
    assert "CUSTOM_API_KEY" in check.detail


async def test_unwritable_workspace_is_reported(settings):
    settings.workspace = settings.workspace / "sous" / "dossier"
    checks = check_workspace(settings)
    assert all(c.status == OK for c in checks)  # crees a la volee
