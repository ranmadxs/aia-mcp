# ─────────────────────────────────────────────────────────────────────────────
# aia-mcp — imagen Docker
# Servidor MCP (Model Context Protocol) para el agente aia (amanda-IA)
#
# Usa la imagen base del ecosistema aia (aia-utils), que ya trae:
#   Python 3.13-slim, git, curl, ca-certificates, build-essential/gcc,
#   Poetry (para `poetry export`), uv (instalador rápido) y
#   Node.js 20 + drawio-mcp-server@2.2.0.
# Ver: https://github.com/ranmadxs/aia-utils (PR #1, imagen keitarodxs/aia-utils-base)
# ─────────────────────────────────────────────────────────────────────────────

FROM keitarodxs/aia-utils-base:v1.0.0

WORKDIR /app

# ── Dependencias Python (capa cacheable) ─────────────────────────────────────
# Se copia SOLO pyproject.toml + poetry.lock ANTES del código fuente, así la
# capa de dependencias solo se reconstruye si cambian las deps, no el código.
# Se exporta a requirements.txt (respeta poetry.lock) y se instala con `uv pip
# install --system`, que es 10-100x más rápido que `pip install`.
COPY pyproject.toml poetry.lock ./
# Poetry 1.8.x no incluye `export` por defecto: instala el plugin con el mismo
# pip que instaló poetry, para que lo detecte en el mismo entorno.
RUN pip install --no-cache-dir poetry-plugin-export \
    && poetry export -f requirements.txt --without-hashes --with dev -o /tmp/requirements.txt 2>/dev/null \
    || poetry export -f requirements.txt --without-hashes -o /tmp/requirements.txt \
    && uv pip install --system -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt
# ── Código fuente (capa NO cacheable, va DESPUÉS de las deps) ───────
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
# README.md es requerido por pyproject.toml (readme = "README.md") para
# que pip install . genere los metadatos sin error.
COPY README.md ./README.md
# Instala el paquete propio (registra el entry point `aia-mcp`).
# Se usa pip (no uv) para consistencia con la imagen base y evitar fallos
# silenciosos de build que dejaban el entry point ausente.
RUN pip install --no-cache-dir .

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
    && printf 'exec aia-mcp all --http\n' >> /app/start.sh \
    && chmod +x /app/start.sh

# Entry point: levanta TODOS los servidores MCP en modo HTTP (paralelo)
# Para un solo servidor MCP: docker run ... aia-mcp aia-mcp temperatura --http
ENTRYPOINT ["/app/start.sh"]
