"""Configuration d'Hermes, lue depuis l'environnement / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from . import providers

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "oui"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _ids(name: str) -> frozenset[int]:
    raw = os.getenv(name, "")
    out: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.add(int(chunk))
        except ValueError:
            raise SystemExit(f"{name} : {chunk!r} n'est pas un identifiant Telegram valide")
    return frozenset(out)


DEFAULT_SYSTEM_PROMPT = """Tu es Hermes, un agent autonome pilote depuis Telegram.

Tu disposes d'outils reels : shell, execution Python, lecture/ecriture de fichiers,
recherche web et recuperation de pages. Utilise-les au lieu de supposer.

Methode :
- Pour toute question factuelle susceptible d'avoir change, cherche sur le web
  avant de repondre. Ne devine pas.
- Pour du code, ecris-le dans le workspace puis execute-le pour verifier qu'il
  marche avant de l'annoncer comme fonctionnel. Rapporte les erreurs telles quelles.
- Enchaine plusieurs outils sans demander la permission a chaque etape.
- Reponds dans la langue de l'utilisateur, en Markdown sobre. Telegram coupe a
  4096 caracteres : va a l'essentiel.
- Si une commande echoue, lis le message d'erreur et corrige, ne repete pas la
  meme commande a l'identique.
"""


@dataclass
class Settings:
    telegram_token: str
    allowed_users: frozenset[int]
    provider: str
    model: str
    system_prompt: str
    workspace: Path
    data_dir: Path
    temperature: float | None
    max_tokens: int
    max_tool_iterations: int
    history_turns: int
    exec_timeout: int
    output_limit: int
    search_backend: str
    request_timeout: int
    custom_base_url: str | None
    allow_network_in_tools: bool
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def spec(self) -> providers.ProviderSpec:
        return providers.get(self.provider)

    def base_url_for(self, spec: providers.ProviderSpec) -> str | None:
        if spec.name == "custom":
            if not self.custom_base_url:
                raise SystemExit(
                    "provider=custom exige HERMES_CUSTOM_BASE_URL (ex: http://host:8000/v1)"
                )
            return self.custom_base_url
        return spec.base_url

    def api_key_for(self, spec: providers.ProviderSpec) -> str:
        key = os.getenv(spec.api_key_env, "").strip()
        if not key:
            if spec.local:
                return "local"  # les serveurs locaux ignorent la cle mais en exigent une
            raise SystemExit(
                f"Le fournisseur '{spec.name}' requiert la variable {spec.api_key_env}. "
                f"Renseigne-la dans .env."
            )
        return key


def load() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN manquant.\n"
            "Ouvre Telegram, parle a @BotFather, envoie /newbot, puis colle le token "
            "dans le fichier .env (voir .env.example)."
        )

    allowed = _ids("HERMES_ALLOWED_USERS")
    if not allowed:
        raise SystemExit(
            "HERMES_ALLOWED_USERS est vide.\n"
            "Hermes execute du code sur cette machine : sans liste blanche, n'importe qui "
            "trouvant le bot obtiendrait un shell. Mets-y ton identifiant numerique "
            "Telegram (demande-le a @userinfobot)."
        )

    provider = os.getenv("HERMES_PROVIDER", "openrouter").strip().lower()
    spec = providers.get(provider)
    model = os.getenv("HERMES_MODEL", "").strip() or spec.default_model
    if not model:
        raise SystemExit(f"HERMES_MODEL est requis pour le fournisseur '{provider}'.")

    workspace = Path(os.getenv("HERMES_WORKSPACE", "./workspace")).expanduser().resolve()
    data_dir = Path(os.getenv("HERMES_DATA_DIR", "./data")).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    temp_raw = os.getenv("HERMES_TEMPERATURE", "").strip()
    temperature: float | None
    try:
        temperature = float(temp_raw) if temp_raw else None
    except ValueError:
        temperature = None

    prompt_file = os.getenv("HERMES_SYSTEM_PROMPT_FILE", "").strip()
    if prompt_file:
        system_prompt = Path(prompt_file).expanduser().read_text(encoding="utf-8")
    else:
        system_prompt = os.getenv("HERMES_SYSTEM_PROMPT", "").strip() or DEFAULT_SYSTEM_PROMPT

    return Settings(
        telegram_token=token,
        allowed_users=allowed,
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        workspace=workspace,
        data_dir=data_dir,
        temperature=temperature,
        max_tokens=_int("HERMES_MAX_TOKENS", 8192),
        max_tool_iterations=max(1, _int("HERMES_MAX_TOOL_ITERATIONS", 25)),
        history_turns=_int("HERMES_HISTORY_TURNS", 40),
        exec_timeout=_int("HERMES_EXEC_TIMEOUT", 120),
        output_limit=_int("HERMES_OUTPUT_LIMIT", 12000),
        search_backend=os.getenv("HERMES_SEARCH_BACKEND", "auto").strip().lower(),
        request_timeout=_int("HERMES_REQUEST_TIMEOUT", 600),
        custom_base_url=os.getenv("HERMES_CUSTOM_BASE_URL", "").strip() or None,
        allow_network_in_tools=_bool("HERMES_ALLOW_NETWORK_IN_TOOLS", True),
    )
