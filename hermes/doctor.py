"""Diagnostic : dire precisement ce qui manque, avant que ca ne casse.

``hermes doctor`` verifie dans l'ordre la configuration, l'acces Telegram, puis
chaque route de la chaine de modeles. Le but est qu'aucune panne ne se decouvre
au premier message recu.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .config import Settings
from .errors import HermesError
from .providers import PROVIDERS, Route, resolve

OK = "✅"
WARN = "⚠️"
FAIL = "❌"


@dataclass
class Check:
    level: str
    title: str
    detail: str = ""

    def render(self) -> str:
        return f"{self.level} {self.title}" + (f"\n     {self.detail}" if self.detail else "")


def _whitelist(settings: Settings) -> Check:
    """Qui aura le droit de parler au bot ? La question n'est jamais anodine :
    Hermes execute des commandes sur la machine hote."""
    from .memory import Store

    stored = Store(settings.data_dir / "hermes.db").owners()
    total = set(settings.allowed_users) | stored
    if total:
        detail = f"{len(total)} utilisateur(s) autorise(s)"
        if stored:
            detail += f" (dont {len(stored)} enregistre(s) par appropriation)"
        return Check(OK, "Liste blanche", detail)
    if settings.claim_owner:
        return Check(
            WARN,
            "Liste blanche",
            "vide, mais HERMES_CLAIM_OWNER=1 : le premier a envoyer /start deviendra "
            "proprietaire. Fais-le tout de suite apres le demarrage.",
        )
    return Check(
        FAIL,
        "Liste blanche",
        "HERMES_ALLOWED_USERS est vide et HERMES_CLAIM_OWNER=0 : le bot refusera tout.",
    )


async def _telegram(settings: Settings) -> Check:
    if not settings.telegram_token:
        return Check(FAIL, "Telegram", "TELEGRAM_BOT_TOKEN absent.")
    try:
        from telegram import Bot

        async with Bot(settings.telegram_token) as bot:
            me = await bot.get_me()
    except Exception as exc:  # noqa: BLE001 - on veut le message, pas le type
        return Check(FAIL, "Telegram", f"token refuse : {type(exc).__name__}: {exc}")
    return Check(OK, "Telegram", f"@{me.username} (id {me.id})")


async def _route(settings: Settings, route: Route, *, deep: bool) -> Check:
    import httpx

    spec = PROVIDERS[route.provider]
    key = spec.api_key()
    if key is None:
        return Check(WARN, str(route), f"{spec.api_key_env} non renseignee")
    base = settings.custom_base_url if spec.name == "custom" else spec.base_url
    if not base:
        return Check(WARN, str(route), "aucune URL de base (HERMES_CUSTOM_BASE_URL ?)")

    headers = {"Authorization": f"Bearer {key}", **spec.extra_headers}
    try:
        async with httpx.AsyncClient(timeout=20, headers=headers) as client:
            response = await client.get(f"{base.rstrip('/')}/models")
    except Exception as exc:  # noqa: BLE001
        return Check(FAIL, str(route), f"injoignable : {type(exc).__name__}: {exc}")

    if response.status_code in (401, 403):
        return Check(FAIL, str(route), f"cle refusee (HTTP {response.status_code})")
    if response.status_code >= 400:
        return Check(WARN, str(route), f"listing indisponible (HTTP {response.status_code})")

    try:
        listed = {item.get("id") for item in response.json().get("data", [])}
    except Exception:  # noqa: BLE001
        listed = set()

    if listed and route.model not in listed:
        return Check(WARN, str(route), "modele absent du catalogue du fournisseur")
    if not deep:
        return Check(OK, str(route), "atteignable, modele annonce")
    return await _completion(settings, route)


async def _completion(settings: Settings, route: Route) -> Check:
    from .llm import Client
    from .tools import Registry

    try:
        client = Client(settings, route)
        reply = await client.complete(
            "Reponds par le seul mot: ok.",
            [{"role": "user", "content": "ping"}],
            Registry(),
        )
    except HermesError as exc:
        return Check(FAIL, str(route), str(exc))
    except Exception as exc:  # noqa: BLE001
        return Check(FAIL, str(route), f"{type(exc).__name__}: {exc}")
    return Check(OK, str(route), f"repond : {reply.text[:60]!r}")


async def run(settings: Settings, *, deep: bool = False) -> list[Check]:
    checks: list[Check] = []

    checks.append(
        Check(OK, "Workspace", str(settings.workspace))
        if settings.workspace.is_dir()
        else Check(FAIL, "Workspace", f"{settings.workspace} n'existe pas")
    )
    checks.append(_whitelist(settings))
    checks.append(await _telegram(settings))

    seen: set[Route] = set()
    tasks = []
    for spec in settings.model_chain:
        for route in resolve(spec):
            if route in seen:
                continue
            seen.add(route)
            if route.provider in PROVIDERS and PROVIDERS[route.provider].configured:
                tasks.append(_route(settings, route, deep=deep))
    if not tasks:
        checks.append(
            Check(
                FAIL,
                "Modeles",
                f"aucune route configuree pour {', '.join(settings.model_chain)}. "
                "Renseigne VENICE_API_KEY (ou une autre cle) dans .env.",
            )
        )
    else:
        checks.extend(await asyncio.gather(*tasks))

    if not any(check.level == OK and ":" in check.title for check in checks):
        checks.append(
            Check(FAIL, "Modeles", "aucune route ne repond : Hermes ne pourra pas repondre.")
        )

    return checks


def render(checks: list[Check]) -> str:
    body = "\n".join(check.render() for check in checks)
    failures = sum(1 for check in checks if check.level == FAIL)
    verdict = "Tout est en place." if not failures else f"{failures} probleme(s) bloquant(s)."
    return f"{body}\n\n{verdict}"
