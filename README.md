# aia-mcp

Servidor MCP (Model Context Protocol) que expone herramientas para el agente **aia** del proyecto hermano [amanda-IA](https://github.com/your-org/amanda-IA).

`aia-mcp` agrupa varios servidores MCP independientes (temperatura, Warhammer 40K, monitor de estanque, Airbnb, charts, email, MangaDex, swagger) que el agente aia consume vía stdio o HTTP.

## Estructura

```
aia-mcp/
├── README.md
├── pyproject.toml
├── Dockerfile                 # imagen keitarodxs/aia-mcp
├── docker-compose.yml         # despliegue en nara
├── mcp_cli/                   # CLI (entry point `mcp`) y registro de servidores
├── specs/                     # especificaciones (ej. SPEC_TEMPERATURA.md)
├── temperatura/  wahapedia/  monitor/  airbnb/
├── charts/  mcp_email/  mangadex/  swagger/
├── resources/mcp-ssh/         # config de mcp-ssh (no usado en la imagen)
└── tests/                     # tests unitarios (pytest)
```

Cada servidor MCP vive en su propio directorio y se registra en `mcp_cli/cli.py`
(diccionario `SERVERS`). Ejecuta cualquiera con `poetry run mcp <servidor>`.

## Requisitos

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- (Opcional, para despliegue) Docker + un host con el self-hosted runner registrado

## Instalación

```bash
cd aia-mcp
poetry install
```

## Ejecutar servidores MCP

Desde el directorio `aia-mcp/`:

```bash
poetry run aia-mcp                    # temperatura (por defecto, stdio)
poetry run aia-mcp temperatura        # explícito
poetry run aia-mcp --list             # listar servidores disponibles

# Modo HTTP (para conexión remota / agente aia por red)
poetry run aia-mcp temperatura --http   # puerto 8001
poetry run aia-mcp all --http           # todos los servidores en paralelo
```

- **stdio** (por defecto): para Cursor, Claude Desktop, etc.
- **HTTP** (`--http`): servidor en `http://0.0.0.0:<puerto>/mcp`.

### Puertos por servidor

| Servidor     | Puerto | Servidor   | Puerto |
|--------------|--------|------------|--------|
| temperatura  | 8001   | airbnb     | 8006   |
| wahapedia    | 8002   | charts     | 8007   |
| monitor      | 8003   | email      | 8008   |
| swagger      | 8010   | banco_bci  | 8011   |
| drawio-mcp   | 3000/3333 |        |        |

> `drawio-mcp-server` se instala dentro de la imagen Docker (no es un servidor
> MCP de este repo) y expone HTTP en `:3000` + WebSocket de extensión en `:3333`.

## Swagger UI (documentación de APIs)

```bash
poetry run mcp-swagger           # puerto 8010
# o
poetry run mcp swagger --http
```

Accede a: http://localhost:8010/

## Conectar con el agente (Cursor / amanda-IA)

Configura el servidor MCP en Cursor o en el agente aia. Ejemplo para `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "temperatura": {
      "command": "poetry",
      "args": ["run", "mcp", "temperatura"],
      "cwd": "/ruta/a/aia-mcp"
    }
  }
}
```

Ajusta `cwd` a la ruta absoluta de tu proyecto `aia-mcp`.

### Conectar aia por HTTP

1. Inicia el servidor MCP en modo HTTP: `poetry run aia-mcp temperatura --http`
2. En otra terminal, ejecuta el agente con la variable de entorno:
   ```bash
   MCP_URL=http://localhost:8001/mcp poetry run aia
   ```

**Añadir nuevos servidores:** crea el directorio (ej. `nuevo_servidor/`), implementa
el servidor con FastMCP, y regístralo en `mcp_cli/cli.py` en el diccionario `SERVERS`
(sumando su puerto en `SERVER_PORTS`).

## Despliegue (Docker)

El repo publica automáticamente la imagen `keitarodxs/aia-mcp` en Docker Hub al
pushear un tag `v*.*.*` (workflow `.github/workflows/docker-image.yml`).

### Local / servidor con Docker
deprecated?
```bash
docker compose pull
docker compose up -d
```

Esto levanta el contenedor `aia-mcp` con todos los puertos mapeados y los volúmenes
`logs/` y `.aia/` montados. Carga las variables desde `.env` (ver `.env.example`).

> **Build de la imagen:** el proyecto se sigue gestionando con Poetry
> (`pyproject.toml` + `poetry.lock`), pero dentro del `Dockerfile` las dependencias
> se instalan con [`uv`](https://github.com/astral-sh/uv) (`uv pip install --system`)
> por velocidad. El `poetry.lock` se respeta vía `poetry export` a `requirements.txt`.
> La imagen parte de la base del ecosistema `keitarodxs/aia-utils-base:v1.0.0` (Python 3.13,
> git, uv, Node 20 + drawio-mcp-server ya incluidos).

## Tests

```bash
poetry install --with dev
poetry run pytest
```

Cobertura actual: `wahapedia` (slug/normalización/facciones) y `airbnb`
(serialización MongoDB / formato iCal).

## Tools disponibles

### temperatura — `get_temperature`

Obtiene la temperatura actual de una ciudad (valores simulados).

- **Parámetro:** `city` (string, opcional)
- **Retorno:** String con la temperatura

Ciudades soportadas: Santiago, Buenos Aires, Lima, Bogotá, Madrid, New York, Londres, Tokio.

### monitor — estanque / acumulador

- **get_lectura_actual()**: litros y porcentaje en tiempo real vía MQTT (`MQTT_HOST`,
  `MQTT_TOPIC_OUT`) o `TINAJA_ESTADO_URL` como fallback.
- **calculate_tinaja_level(distance)**: litros/% desde la distancia del sensor (cm).
- **get_tinaja_config()**: configuración del estanque y estado MQTT.
- **get_velocidad_disminucion_agua(db_name, horas_atras)**: velocidad de bajada (L/h)
  usando historial en MongoDB. Requiere `MONGODB_URI`.

Variables `.env`: `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`,
`MQTT_TOPIC_OUT`, `TINAJA_ESTADO_URL`, `MONGODB_URI`.

### wahapedia — Warhammer 40K

- **get_unit_stats(query, faction)** / **search_wahapedia(query)**
- **get_factions()** / **get_units(faction)** / **get_stratagems(faction)**

Cache en disco configurable: `WAHAPEDIA_CACHE_ENABLED`, `WAHAPEDIA_CACHE_DIR`,
`WAHAPEDIA_CACHE_TTL_DAYS` (por defecto deshabilitada; TTL 60 días).

### airbnb — reservas y calendario (MongoDB)

- **get_proxima_reserva()**, **get_reservas_futuras(solo_futuras)**,
  **get_calendario_mes_airbnb(mes, anio)**, **get_ingresos_mes(mes, anio)**

Requiere `MONGODB_URI` y `AIRBNB_DB`.

### banco_bci — cartolas BCI y pipeline completo

Consulta de cartolas almacenadas en MongoDB y orquestación del pipeline BCI
vía API HTTP de `aia-jobs` (expone `/api/jobs/*` en `:8080`).

- **banco_bci(period)**: cartolas BCI del período con sus movimientos.
- **banco_bci_list_periods()**: lista de períodos disponibles en `bci.cartolas`.
- **sync_bci_trx(year, month, batch_size, skip_email_download, skip_transform,
  sender)**: ejecuta el pipeline completo en una sola llamada
  (descarga emails BCI de Yahoo → transforma PDFs → sincroniza movimientos a
  `bci.transacciones` en Atlas). Las 3 fases son idempotentes y se pueden
  saltar con `skip_email_download` / `skip_transform` para re-runs parciales.
- **get_bci_job_status()**: estado del último job BCI en `aia-jobs`
  (`running`, `current_job`, `progress`, `last_result`).
- **get_bci_api_health()**: health-check de la API de `aia-jobs`.

Variables `.env`: `MONGODB_URI` (consulta), `AIA_JOBS_API_URL`
(default `http://172.17.0.1:8080` = gateway bridge de Docker al host donde
corre `aia-jobs`), `AIA_JOBS_TIMEOUT` (default `600`s).

### charts / email / mangadex / swagger

- **charts**: genera gráficos (matplotlib) desde datos de MongoDB.
- **email**: consulta correo IMAP Yahoo (`YAHOO_EMAIL`, `YAHOO_APP_PASSWORD`).
- **mangadex**: descarga/consulta mangas (`AIA_MANGA_DIR`).
- **swagger**: UI de documentación de APIs en `:8010`.

