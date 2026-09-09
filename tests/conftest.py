from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes import providers  # noqa: E402

# hermes.config appelle load_dotenv() a l'import : le .env du poste de travail
# se retrouverait dans l'environnement des tests, qui contacteraient alors de
# vrais fournisseurs. On importe d'abord, puis on purge.
from hermes.config import Settings  # noqa: E402

_PREFIXES = (
    "HERMES_", "TELEGRAM_", "VENICE_", "OPENROUTER_", "CHUTES_", "ZAI_",
    "DEEPSEEK_", "GROQ_", "TOGETHER_", "OPENAI_", "CUSTOM_", "VLLM_", "OLLAMA_",
    "LMSTUDIO_",
)
for name in list(os.environ):
    if name.startswith(_PREFIXES):
        del os.environ[name]


@pytest.fixture(autouse=True)
def pas_de_serveur_local(monkeypatch):
    """Aucun test ne doit dependre d'un serveur qui tournerait sur la machine."""
    monkeypatch.setattr(providers, "local_server_up", lambda url, timeout=0.3: False)
    providers._probes.clear()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    workspace = tmp_path / "workspace"
    data = tmp_path / "data"
    workspace.mkdir()
    data.mkdir()
    return Settings(
        telegram_token="test:token",
        allowed_users=frozenset({1}),
        model_chain=("glm-4.7-heretic",),
        system_prompt="Tu es Hermes.",
        workspace=workspace,
        data_dir=data,
        temperature=None,
        max_tokens=512,
        max_tool_iterations=4,
        history_messages=20,
        exec_timeout=10,
        output_limit=2000,
        request_timeout=10,
        custom_base_url="",
        claim_owner=False,
        enable_shell=True,
        enable_web=True,
        search_url="https://example.invalid/",
        log_level="WARNING",
    )


@pytest.fixture
def context(settings):
    from hermes.tools import ToolContext

    return ToolContext(
        workspace=settings.workspace,
        exec_timeout=settings.exec_timeout,
        output_limit=settings.output_limit,
        request_timeout=settings.request_timeout,
        search_url=settings.search_url,
        chat_id=1,
    )
