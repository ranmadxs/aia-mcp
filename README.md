# aia-mcp

Servidor MCP (Model Context Protocol) que expone herramientas para el agente **aia** del proyecto hermano [amanda-IA](https://github.com/your-org/amanda-IA).

## Estructura

```
aia-mcp/
├── README.md
├── pyproject.toml
├── mcp_cli/           # CLI para ejecutar servidores
├── specs/
│   └── SPEC_TEMPERATURA.md
├── temperatura/       # get_temperature (ciudades)
│   ├── __init__.py
│   └── server.py
└── tinaja/            # acumulador/estanque - litros, porcentaje
    ├── __init__.py
    └── server.py
```

Cada servidor MCP vive en su propio directorio. Ejecuta cualquiera con `poetry run mcp <servidor>`.

## Requisitos

- Python 3.11+
- [Poetry](https://python-poetry.org/)

## Instalación

```bash
cd aia-mcp
poetry install
```

## Ejecutar servidores MCP

Desde el directorio `aia-mcp/`:

```bash
poetry run mcp                    # temperatura (por defecto, stdio)
poetry run mcp temperatura        # explícito
poetry run mcp tinaja             # acumulador/estanque (litros, %)
poetry run mcp --list             # listar servidores disponibles

# Modo HTTP (para conexión remota)
poetry run mcp temperatura --http   # puerto 8001
poetry run mcp tinaja --http       # puerto 8003

# Levantar todos los servidores
poetry run mcp all --http
```

- **stdio** (por defecto): para Cursor, Claude Desktop, etc.
- **HTTP** (`--http`): servidor en `http://0.0.0.0:8001/mcp` para que el agente aia se conecte por red.

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

1. Inicia el servidor MCP en modo HTTP: `poetry run mcp temperatura --http`
2. En otra terminal, ejecuta el agente con la variable de entorno:
   ```bash
   MCP_URL=http://localhost:8001/mcp poetry run aia
   ```

**Añadir nuevos servidores:** crea el directorio (ej. `nuevo_servidor/`), implementa el servidor con FastMCP, y regístralo en `mcp_cli/cli.py` en el diccionario `SERVERS`.

## Tools disponibles

### get_temperature

Obtiene la temperatura actual de una ciudad (valores simulados).

- **Parámetro:** `city` (string, opcional) — Nombre de la ciudad
- **Retorno:** String con la temperatura

Ciudades soportadas: Santiago, Buenos Aires, Lima, Bogotá, Madrid, New York, Londres, Tokio.

### Tinaja (acumulador / estanque)

- **get_lectura_actual()**: Obtiene litros y porcentaje del acumulador en tiempo real. Usa MQTT (`MQTT_HOST`, `MQTT_TOPIC_OUT`) o `TINAJA_ESTADO_URL` como fallback. Si no hay datos, devuelve un cálculo de ejemplo.
- **calculate_tinaja_level(distance)**: Calcula litros y porcentaje desde la distancia del sensor (cm).
- **get_tinaja_config()**: Configuración del estanque y estado MQTT.

Variables en `.env`: `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_TOPIC_OUT`. Fallback HTTP: `TINAJA_ESTADO_URL`.

### Wahapedia (Warhammer 40K)

- **get_unit_stats(query, faction)**: Estadísticas de una unidad.
- **search_wahapedia(query)**: Búsqueda en español.

Cache: config en `.aia/mcp.json` → `wahapedia.cache` (`enabled`, `dir`, `ttlDays`). Default 60 días. Guarda en `.aia/cache/wahapedia/`.
