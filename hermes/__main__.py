"""Point d'entree : `python -m hermes` ou `hermes`."""

from __future__ import annotations

import logging
import sys

from .bot import build_application
from .config import load


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.WARNING)

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
