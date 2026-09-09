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

# Verifie que la configuration tient debout avant de declarer le service sain.
HEALTHCHECK --interval=5m --timeout=60s --start-period=30s \
    CMD hermes doctor >/dev/null || exit 1

CMD ["hermes", "run"]
