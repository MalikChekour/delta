FROM python:3.12-slim

# L'agent execute des commandes : on lui laisse de quoi travailler.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY hermes ./hermes
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1 \
    HERMES_WORKSPACE=/data/workspace \
    HERMES_DATA_DIR=/data
VOLUME ["/data"]

# On verifie que le bot *ecoute*, pas seulement qu'il est vivant : un service
# qui tourne sans recevoir les messages est le pire des etats, car il ne
# ressemble a rien de visible.
HEALTHCHECK --interval=60s --timeout=10s --start-period=45s --retries=3 \
    CMD hermes health >/dev/null || exit 1

CMD ["hermes", "run"]
