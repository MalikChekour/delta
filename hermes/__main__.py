"""Point d'entree : `python -m hermes` ou `hermes`.

`--check` lance le diagnostic sans demarrer le bot.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .bot import build_application
from .config import load


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="hermes", description="Agent Telegram multi-modele.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Diagnostique la configuration, le token Telegram et le modele, puis sort.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Journalisation en DEBUG.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.WARNING)

    if args.check:
        from .doctor import main as doctor

        return doctor()

    settings = load()
    log = logging.getLogger("hermes")
    log.info("Fournisseur : %s / %s", settings.provider, settings.model)
    log.info("Workspace   : %s", settings.workspace)
    log.info("Utilisateurs autorises : %s", ", ".join(map(str, sorted(settings.allowed_users))))

    app = build_application(settings)
    app.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
