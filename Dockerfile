# Hermes execute du code arbitraire : le conteneur est la frontiere de securite.
FROM python:3.12-slim

# Outils que l'agent utilisera dans son workspace.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY hermes ./hermes
RUN pip install --no-cache-dir -e .

# L'agent tourne sans privileges et ne possede pas son propre code :
# il ne peut donc pas se reecrire.
RUN useradd --create-home --uid 10001 hermes \
    && mkdir -p /data/workspace /data/db \
    && chown -R hermes:hermes /data
USER hermes

ENV HERMES_WORKSPACE=/data/workspace \
    HERMES_DATA_DIR=/data/db \
    PYTHONUNBUFFERED=1

# Echoue vite et bruyamment si le token ou la cle du modele sont mauvais.
HEALTHCHECK --interval=5m --timeout=60s --start-period=30s --retries=2 \
    CMD ["python", "-m", "hermes", "--check"]

ENTRYPOINT ["python", "-m", "hermes"]
