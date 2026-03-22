# Changelog

Todos los cambios notables de aia-mcp se documentan aquí.
Servidores MCP custom para el agente amanda-IA.

---

## [v0.4.1-aia] --- 2026-03-22

### Agregado
-  get_consumo_periodo: consumo real de agua por dia/mes/rango
-  get_top_consumo: ranking de N dias con mayor consumo y hora pico
-  helper _compute_consumo_docs para calculos de consumo compartidos

---

## [v0.4.0-aia] — 2026-03-22

### Agregado
- **Servidor MCP Shell** (`shell/server.py`): tool `run_command(command, cwd)` para ejecutar comandos desde modo Dev de amanda-IA
- Servidor Monitor renombrado desde `tinaja/` a `monitor/` con live monitor en tiempo real
- `start_live_monitor()` y `stop_live_monitor()` para streaming de datos del estanque
- `get_velocidad_disminucion_agua()`: calcula velocidad de disminución del nivel

### Cambiado
- `tinaja` renombrado a `monitor` como módulo y servidor MCP
- `wahapedia/server.py`: mejoras en búsqueda y retorno de resultados

---

## [v0.3.0-alpha.1] — 2026-03-19

### Agregado
- **Servidor MCP Tinaja/Monitor**: `get_lectura_actual()`, `calculate_tinaja_level()`
- Caché de respuestas Wahapedia en disco (configurable vía `mcp.json`)
- Log de requests HTTP y caché hit/miss en todos los servidores

---

## [v0.1.1-alpha.1] — 2026-03-19

### Agregado
- **Servidor MCP Wahapedia**: búsqueda de unidades, stats y estratagemas de Warhammer 40K
- CLI `mcp all --http`: levanta todos los servidores HTTP en paralelo
- Middleware de logging para requests entrantes
- Soporte multi-servidor con puertos configurables por `.env`

---

## [v0.1.0-alpha.1] — 2026-03-18

### Agregado
- **Servidor MCP Temperatura**: `get_temperature(city)` vía Open-Meteo API
- CLI inicial: `mcp temperatura --http`
- Configuración vía `.env` (`FASTMCP_HOST`, `FASTMCP_PORT`)
- Transporte HTTP streamable con FastMCP
