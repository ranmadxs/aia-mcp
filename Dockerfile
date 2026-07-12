# ─────────────────────────────────────────────────────────────────────────────
# aia-mcp — imagen Docker
# Servidor MCP (Model Context Protocol) para el agente aia (amanda-IA)
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# Evita prompts interactivos y writes .pyc
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

# Dependencias del sistema:
# - build-essential / gcc: compilación de paquetes con extensiones C (pymongo, etc.)
# - git: requerido por el servidor "shell" y mangadex-downloader
# - ca-certificates / curl: descargas varias
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        git \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instala Poetry (gestor de dependencias del proyecto)
RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

# ── Instala Node.js + npm (prerequisito para drawio-mcp-server) ───────────────
# Usa NodeSource para tener una versión reciente de Node en Debian/Ubuntu slim.
RUN set -eux; \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -; \
    apt-get install -y --no-install-recommends nodejs; \
    rm -rf /var/lib/apt/lists/*; \
    node --version; npm --version

WORKDIR /app

# Copia primero los metadatos para aprovechar la cache de capas
COPY pyproject.toml README.md ./
COPY mcp_cli ./mcp_cli
COPY temperatura ./temperatura
COPY wahapedia ./wahapedia
COPY monitor ./monitor
COPY shell ./shell
COPY airbnb ./airbnb
COPY charts ./charts
COPY mcp_email ./mcp_email
COPY mangadex ./mangadex
COPY swagger ./swagger

# Instala dependencias (incluye grupo dev para mcp-swagger-ui)
RUN poetry install --no-root --with dev 2>&1 \
    || poetry install --no-root 2>&1

# Instala el paquete propio (registra el entry point `mcp`).
# Se mantiene el grupo dev (mcp-swagger-ui) requerido por el servidor swagger.
RUN poetry install 2>&1 || true

# ── Instala drawio-mcp-server (npm) ───────────────────────────────────────────
# Expone MCP Streamable HTTP en :3000 y WebSocket de extensión en :3333.
# Versión fijada para evitar sorpresas con @latest.
RUN npm install -g drawio-mcp-server@2.2.0 \
    && drawio-mcp-server --help >/dev/null 2>&1 || true

# ── Variables de entorno por defecto ──────────────────────────────────────────
# Host de FastMCP (0.0.0.0 para escuchar dentro del contenedor)
ENV FASTMCP_HOST=0.0.0.0 \
    FASTMCP_LOG_LEVEL=INFO

# Monitor / estanque (MQTT)
ENV MQTT_HOST=broker.mqttdashboard.com \
    MQTT_PORT=1883 \
    MQTT_USERNAME=test \
    MQTT_PASSWORD=test \
    MQTT_TOPIC_OUT=yai-mqtt/01C40A24/out \
    TINAJA_ALTURA_SENSOR=145 \
    TINAJA_CAPACIDAD_LITROS=5000 \
    TINAJA_ESTADO_URL=

# MongoDB (monitor, charts, airbnb, email)
ENV MONGODB_URI= \
    AIRBNB_DB=airbnb-db

# Email (IMAP Yahoo)
ENV YAHOO_EMAIL= \
    YAHOO_APP_PASSWORD=

# Wahapedia cache (deshabilitada por defecto)
ENV WAHAPEDIA_CACHE_ENABLED=false \
    WAHAPEDIA_CACHE_DIR=.aia/cache/wahapedia \
    WAHAPEDIA_CACHE_TTL_DAYS=60

# MangaDex descargas
ENV AIA_MANGA_DIR=.aia/manga

# Swagger UI
ENV SWAGGER_PORT=8010

# ── Directorios de datos persistentes ─────────────────────────────────────────
# logs/  → archivos de log rotados por día (logs/{servidor}_{fecha}.log)
# .aia/  → cache de wahapedia y descargas de mangadex
RUN mkdir -p /app/logs /app/.aia/cache/wahapedia /app/.aia/manga \
    && chmod -R 777 /app/logs /app/.aia

VOLUME ["/app/logs", "/app/.aia"]

# ── Puertos expuestos ─────────────────────────────────────────────────────────
# temperatura 8001 | wahapedia 8002 | monitor 8003 | shell 8005
# airbnb 8006 | charts 8007 | email 8008 | mangadex 8009 | swagger 8010
# drawio-mcp 3000 (HTTP) + 3333 (WS extensión)
EXPOSE 8001 8002 8003 8005 8006 8007 8008 8009 8010 3000 3333

# Script de arranque: levanta drawio-mcp (background) + MCPs (foreground).
RUN printf '#!/bin/sh\nset -e\n' > /app/start.sh \
    && printf 'echo "[start] drawio-mcp-server en background (HTTP :3000 / WS :3333)..."\n' >> /app/start.sh \
    && printf 'nohup drawio-mcp-server --host 0.0.0.0 --extension-port 3333 --http-port 3000 --transport http >/tmp/drawio-mcp.log 2>&1 &\n' >> /app/start.sh \
    && printf 'echo "[start] MCPs aia-mcp (all --http)..."\n' >> /app/start.sh \
    && printf 'exec mcp all --http\n' >> /app/start.sh \
    && chmod +x /app/start.sh

# Entry point: levanta TODOS los servidores MCP en modo HTTP (paralelo)
# Para un solo servidor MCP: docker run ... aia-mcp mcp temperatura --http
ENTRYPOINT ["/app/start.sh"]
