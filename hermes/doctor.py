"""Diagnostic de demarrage : `python -m hermes --check`.

Repond a la seule question qui compte quand le bot ne repond pas :
qu'est-ce qui est casse, exactement.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

import httpx

from . import providers
from .config import Settings, load
from .tools import build_registry

OK, WARN, FAIL = "  OK  ", " ALERTE", " ECHEC"

#: Surcharge par les tests pour viser un faux serveur Telegram.
TELEGRAM_API = "https://api.telegram.org"


@dataclass
class Check:
    status: str
    label: str
    detail: str = ""

    def render(self) -> str:
        line = f"[{self.status}] {self.label}"
        return f"{line}\n         {self.detail}" if self.detail else line

    @property
    def failed(self) -> bool:
        return self.status == FAIL


async def check_telegram(settings: Settings) -> list[Check]:
    """Valide le token et detecte l'erreur la plus frequente : deux instances."""
    base = f"{TELEGRAM_API}/bot{settings.telegram_token}"
    out: list[Check] = []
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(f"{base}/getMe")
        except httpx.HTTPError as exc:
            return [Check(FAIL, "Telegram joignable", f"{type(exc).__name__}: {exc}")]

        if resp.status_code == 401:
            return [
                Check(
                    FAIL,
                    "Token Telegram",
                    "401 Unauthorized : le token est faux ou revoque. "
                    "Redemande-le a @BotFather (/mybots -> API Token).",
                )
            ]
        if resp.status_code != 200:
            return [Check(FAIL, "Token Telegram", f"HTTP {resp.status_code} : {resp.text[:200]}")]

        me = resp.json().get("result", {})
        out.append(Check(OK, "Token Telegram", f"@{me.get('username')} (id {me.get('id')})"))

        # 409 Conflict = un autre processus fait deja du polling sur ce token.
        # C'est la cause n°1 d'un bot qui "ne repond plus".
        try:
            poll = await client.get(f"{base}/getUpdates", params={"timeout": 0, "limit": 1})
            if poll.status_code == 409:
                out.append(
                    Check(
                        FAIL,
                        "Instance unique",
                        "409 Conflict : une AUTRE instance d'Hermes tourne deja avec ce "
                        "token. Telegram n'en autorise qu'une. Arrete l'autre, ou "
                        "genere un second bot pour tes tests.",
                    )
                )
            else:
                out.append(Check(OK, "Instance unique", "aucun autre polling sur ce token"))
        except httpx.HTTPError as exc:
            out.append(Check(WARN, "Instance unique", f"verification impossible : {exc}"))

        # Un webhook actif empeche le polling de recevoir quoi que ce soit.
        try:
            hook = (await client.get(f"{base}/getWebhookInfo")).json().get("result", {})
            if hook.get("url"):
                out.append(
                    Check(
                        FAIL,
                        "Mode reception",
                        f"un webhook est configure ({hook['url']}). Il capte les messages "
                        f"a la place du polling. Supprime-le : {base[:34]}.../deleteWebhook",
                    )
                )
            else:
                out.append(Check(OK, "Mode reception", "polling, aucun webhook concurrent"))
        except httpx.HTTPError:
            pass
    return out


async def check_provider(settings: Settings) -> Check:
    """Appel reel au modele : c'est le seul test qui prouve que la cle marche."""
    from .llm import build_client

    spec = providers.get(settings.provider)
    label = f"Modele {settings.provider} / {settings.model}"

    # Sonde a 32 tokens : on veut savoir si l'appel passe, pas depenser.
    probe = Settings(**{**settings.__dict__, "max_tokens": 32})
    try:
        client = build_client(probe, spec)
    except SystemExit as exc:  # cle absente : le message porte deja le nom de la variable
        return Check(FAIL, label, str(exc))
    except Exception as exc:  # noqa: BLE001 - un diagnostic ne doit jamais planter
        return Check(FAIL, label, f"{type(exc).__name__}: {exc}")

    started = time.monotonic()
    try:
        reply = await client.complete(
            "Reponds par un seul mot.",
            [{"role": "user", "content": "ping"}],
            build_registry(probe),
            settings.model,
        )
    except Exception as exc:  # noqa: BLE001 - toute panne doit devenir un diagnostic lisible
        hint = ""
        text = f"{exc}".lower()
        if "401" in text or "auth" in text or "api key" in text:
            hint = f" — verifie {spec.api_key_env} dans .env"
        elif "404" in text or "not found" in text or "model" in text:
            hint = f" — le modele '{settings.model}' n'existe pas chez {settings.provider}"
        elif "connect" in text or "refused" in text or "timeout" in text:
            hint = (
                f" — endpoint injoignable ({settings.base_url_for(spec)})"
                if spec.local
                else " — reseau ou endpoint injoignable"
            )
        return Check(FAIL, label, f"{type(exc).__name__}: {exc}{hint}")

    ms = int((time.monotonic() - started) * 1000)
    used = reply.usage.get("input", 0) + reply.usage.get("output", 0)
    return Check(OK, label, f"reponse en {ms} ms, {used} tokens")


def check_workspace(settings: Settings) -> list[Check]:
    out = []
    for label, path in (("Workspace", settings.workspace), ("Base de donnees", settings.data_dir)):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".hermes-write-test"
            probe.write_text("ok")
            probe.unlink()
            out.append(Check(OK, label, str(path)))
        except OSError as exc:
            out.append(Check(FAIL, label, f"{path} non inscriptible : {exc}"))
    return out


async def run() -> int:
    print("Diagnostic Hermes\n" + "=" * 60)

    if not os.path.exists(".env"):
        print(f"[{WARN}] Fichier .env absent — lecture depuis l'environnement seul.")
        print("         Si le bot ne demarre pas : cp .env.example .env puis remplis-le.\n")

    try:
        settings = load()
    except SystemExit as exc:
        print(f"[{FAIL}] Configuration\n         " + str(exc).replace("\n", "\n         "))
        return 1

    checks: list[Check] = [
        Check(
            OK,
            "Utilisateurs autorises",
            ", ".join(map(str, sorted(settings.allowed_users))),
        ),
        *check_workspace(settings),
        *await check_telegram(settings),
        await check_provider(settings),
    ]

    registry = build_registry(settings)
    checks.append(Check(OK, "Outils", f"{len(registry)} charges : {', '.join(registry.names())}"))

    for check in checks:
        print(check.render())

    failures = [c for c in checks if c.failed]
    print("=" * 60)
    if failures:
        print(f"{len(failures)} probleme(s) bloquant(s). Hermes ne repondra pas en l'etat.")
        return 1
    print("Tout est vert. `python -m hermes` doit fonctionner.")
    return 0


def main() -> int:
    return asyncio.run(run())
