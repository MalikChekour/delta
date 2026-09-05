import pytest

from hermes.config import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        telegram_token="test",
        allowed_users=frozenset({1}),
        provider="openrouter",
        model="deepseek/deepseek-chat",
        system_prompt="tu es hermes",
        workspace=tmp_path / "ws",
        data_dir=tmp_path / "data",
        temperature=None,
        max_tokens=1024,
        max_tool_iterations=5,
        history_turns=20,
        exec_timeout=10,
        output_limit=4000,
        search_backend="duckduckgo",
        request_timeout=60,
        custom_base_url=None,
        allow_network_in_tools=True,
    )


@pytest.fixture(autouse=True)
def _mkdirs(settings):
    settings.workspace.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
