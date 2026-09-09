"""Point d'entree en ligne de commande.

    hermes            # demarre le bot Telegram
    hermes doctor     # verifie configuration, Telegram et routes de modeles
    hermes chat       # dialogue avec l'agent dans le terminal, sans Telegram
    hermes models     # affiche le catalogue et l'etat des routes
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import __version__, config, doctor, providers
from .errors import HermesError


def _logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # httpx journalise chaque requete en INFO : trop bavard pour un service.
    for noisy in ("httpx", "httpx2", "httpcore", "telegram.ext.Application"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _cmd_run(args: argparse.Namespace) -> int:
    from . import bot

    settings = config.load()
    settings.validate_models()
    _logging(settings.log_level)
    bot.run(settings)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    settings = config.load(require_telegram=False)
    _logging(settings.log_level)
    checks = asyncio.run(doctor.run(settings, deep=args.deep))
    print(doctor.render(checks))
    return 1 if any(check.level == doctor.FAIL for check in checks) else 0


def _cmd_models(args: argparse.Namespace) -> int:
    settings = config.load(require_telegram=False)
    print(f"Chaine par defaut : {', '.join(settings.model_chain)}\n")
    for alias in providers.MODELS.values():
        print(f"{alias.name}\n  {alias.description}\n  {providers.describe(alias.name)}\n")
    print("Fournisseurs :")
    for name, spec in providers.PROVIDERS.items():
        state = "configure" if spec.configured else f"{spec.api_key_env} absente"
        print(f"  {name:<12} {state}")
    return 0


def _cmd_chat(args: argparse.Namespace) -> int:
    """REPL local : la meme boucle d'agent, sans dependre de Telegram."""
    from .agent import Agent
    from .memory import Store
    from .tools import build_registry

    settings = config.load(require_telegram=False)
    settings.validate_models()
    _logging(settings.log_level)
    registry = build_registry(
        enable_shell=settings.enable_shell, enable_web=settings.enable_web
    )
    agent = Agent(settings, registry, Store(settings.data_dir / "hermes.db"))

    async def loop() -> None:
        print(f"Hermes {__version__} — {', '.join(settings.model_chain)}. Ctrl-D pour sortir.\n")
        while True:
            try:
                line = input("vous > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not line:
                continue
            if line in {"/quit", "/exit"}:
                return

            async def progress(step: str) -> None:
                print(f"  … {step}")

            try:
                run = await agent.respond(args.chat_id, line, progress)
            except HermesError as exc:
                print(f"\nErreur : {exc}\n")
                continue
            print(f"\nhermes > {run.text}\n")

    asyncio.run(loop())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hermes", description="Agent Hermes")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="demarre le bot Telegram (defaut)").set_defaults(func=_cmd_run)

    doctor_parser = sub.add_parser("doctor", help="verifie l'installation")
    doctor_parser.add_argument(
        "--deep", action="store_true", help="envoie une vraie requete a chaque route"
    )
    doctor_parser.set_defaults(func=_cmd_doctor)

    sub.add_parser("models", help="catalogue et etat des routes").set_defaults(func=_cmd_models)

    chat_parser = sub.add_parser("chat", help="dialogue dans le terminal")
    chat_parser.add_argument("--chat-id", type=int, default=-1, help="identifiant de session")
    chat_parser.set_defaults(func=_cmd_chat)

    args = parser.parse_args(argv)
    handler = getattr(args, "func", _cmd_run)
    try:
        return handler(args)
    except HermesError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
