"""Configuration : une seule source de verite, lue depuis l'environnement.

Toute valeur invalide leve une ``ConfigError`` avec la marche a suivre. Hermes
refuse de demarrer a moitie configure : mieux vaut un message clair au demarrage
qu'une panne obscure au premier message recu.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from .errors import ConfigError
from .providers import DEFAULT_CHAIN, resolve

load_dotenv()

DEFAULT_SYSTEM_PROMPT = """Tu es Hermes, un agent autonome pilote depuis Telegram.

Tu disposes d'outils reels : shell, execution Python, lecture et ecriture de
fichiers, recherche web, recuperation de pages. Sers-t'en au lieu de supposer.

Methode de travail :
- Toute affirmation factuelle susceptible d'avoir change se verifie sur le web
  avant d'etre enoncee. Ne devine jamais une donnee verifiable.
- Le code s'ecrit dans le workspace puis s'execute. On n'annonce pas qu'un
  programme marche sans l'avoir lance. On rapporte les erreurs telles quelles.
- Enchaine les outils sans demander la permission a chaque etape ; l'utilisateur
  t'a deja donne son accord en te confiant la tache.
- Si une commande echoue, lis le message d'erreur et corrige. Ne relance jamais
  la meme commande a l'identique en esperant un autre resultat.
- Reponds dans la langue de l'utilisateur. Telegram coupe a 4096 caracteres :
  va droit au but, pas de preambule ni de resume de ce que tu vas faire.
"""


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _flag(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on", "oui"}


def _int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} doit etre un entier, recu {raw!r}.") from exc
    if value < minimum:
        raise ConfigError(f"{name} doit valoir au moins {minimum}, recu {value}.")
    return value


def _float_or_none(name: str) -> float | None:
    raw = _env(name)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} doit etre un nombre decimal, recu {raw!r}.") from exc


def _user_ids(name: str) -> frozenset[int]:
    out: set[int] = set()
    for chunk in _env(name).replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.add(int(chunk))
        except ValueError as exc:
            raise ConfigError(
                f"{name} : {chunk!r} n'est pas un identifiant Telegram numerique. "
                "Demande le tien a @userinfobot."
            ) from exc
    return frozenset(out)


def _chain(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = _env(name)
    if not raw:
        return default
    items = tuple(part.strip() for part in raw.split(",") if part.strip())
    return items or default


@dataclass(frozen=True)
class Settings:
    """Etat de configuration, immuable une fois charge."""

    telegram_token: str
    allowed_users: frozenset[int]
    model_chain: tuple[str, ...]
    system_prompt: str
    workspace: Path
    data_dir: Path
    temperature: float | None
    max_tokens: int
    max_tool_iterations: int
    history_messages: int
    exec_timeout: int
    output_limit: int
    request_timeout: int
    custom_base_url: str
    claim_owner: bool
    enable_shell: bool
    enable_web: bool
    search_url: str
    log_level: str
    env: dict[str, str] = field(default_factory=dict)

    @property
    def primary(self) -> str:
        return self.model_chain[0]

    def validate_models(self) -> None:
        """Verifie qu'au moins une route est servable avec les cles presentes."""
        for spec in self.model_chain:
            if any(route.usable for route in resolve(spec)):
                return
        raise ConfigError(
            "Aucun modele servable.\n"
            f"Chaine demandee : {', '.join(self.model_chain)}\n"
            "Renseigne une cle API (OPENROUTER_API_KEY, VENICE_API_KEY, CHUTES_API_KEY, "
            "ZAI_API_KEY...) ou demarre un serveur local (vLLM, Ollama). "
            "Lance `hermes doctor` pour le detail."
        )


def load(*, require_telegram: bool = True) -> Settings:
    token = _env("TELEGRAM_BOT_TOKEN")
    if require_telegram and not token:
        raise ConfigError(
            "TELEGRAM_BOT_TOKEN manquant.\n"
            "Copie .env.example vers .env et colle le token donne par @BotFather."
        )

    allowed = _user_ids("HERMES_ALLOWED_USERS")
    claim_owner = _flag("HERMES_CLAIM_OWNER", False)
    if require_telegram and not allowed and not claim_owner:
        raise ConfigError(
            "HERMES_ALLOWED_USERS est vide.\n"
            "Hermes execute du code sur cette machine : sans liste blanche, quiconque "
            "trouve le bot obtient un shell.\n"
            "Deux facons de proceder :\n"
            "  • mets ton identifiant Telegram numerique dans HERMES_ALLOWED_USERS "
            "(demande-le a @userinfobot) ;\n"
            "  • ou mets HERMES_CLAIM_OWNER=1 : le premier a envoyer /start devient "
            "proprietaire, et lui seul. Envoie /start immediatement apres le demarrage."
        )

    workspace = Path(_env("HERMES_WORKSPACE", "./workspace")).expanduser().resolve()
    data_dir = Path(_env("HERMES_DATA_DIR", "./data")).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    prompt_file = _env("HERMES_SYSTEM_PROMPT_FILE")
    if prompt_file:
        path = Path(prompt_file).expanduser()
        if not path.is_file():
            raise ConfigError(f"HERMES_SYSTEM_PROMPT_FILE : {path} est introuvable.")
        system_prompt = path.read_text(encoding="utf-8")
    else:
        system_prompt = _env("HERMES_SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT

    settings = Settings(
        telegram_token=token,
        allowed_users=allowed,
        model_chain=_chain("HERMES_MODEL", DEFAULT_CHAIN),
        system_prompt=system_prompt,
        workspace=workspace,
        data_dir=data_dir,
        temperature=_float_or_none("HERMES_TEMPERATURE"),
        max_tokens=_int("HERMES_MAX_TOKENS", 8192, minimum=256),
        max_tool_iterations=_int("HERMES_MAX_TOOL_ITERATIONS", 20),
        history_messages=_int("HERMES_HISTORY_MESSAGES", 60, minimum=2),
        exec_timeout=_int("HERMES_EXEC_TIMEOUT", 120),
        output_limit=_int("HERMES_OUTPUT_LIMIT", 12000, minimum=500),
        request_timeout=_int("HERMES_REQUEST_TIMEOUT", 300),
        custom_base_url=_env("HERMES_CUSTOM_BASE_URL"),
        claim_owner=claim_owner,
        enable_shell=_flag("HERMES_ENABLE_SHELL", True),
        enable_web=_flag("HERMES_ENABLE_WEB", True),
        search_url=_env("HERMES_SEARCH_URL", "https://duckduckgo.com/html/"),
        log_level=_env("HERMES_LOG_LEVEL", "INFO").upper(),
    )
    return settings
