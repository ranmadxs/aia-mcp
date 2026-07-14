# Changelog

Todos los cambios notables de aia-mcp se documentan aquí.
Servidores MCP custom para el agente amanda-IA.

---

## [v1.5.9] — 2026-07-13

### Cambiado
- CI: `deploy-nara` ahora usa `docker run` puro (pull de la imagen con el tag exacto + `docker run` con puertos y volúmenes). Se eliminó `docker-compose.yml` (ya no se usa). Secuencia: test → build → publish → deploy.

## [v1.5.8] — 2026-07-13

### Cambiado
- CI: `deploy-nara` ahora despliega el **tag exacto** publicado por `publish` (variable `AIA_MCP_TAG`), no `latest`. El `docker-compose.yml` usa `image: keitarodxs/aia-mcp:${AIA_MCP_TAG:-latest}` (default `latest` para uso local). Secuencia: test → build → publish → deploy.

## [v1.5.7] — 2026-07-13

### Arreglado
- CI: `deploy-nara` clona el repo en un directorio temporal y lo copia al workspace (sin borrar `logs/`), en vez de `git clone .` que fallaba con `destination path '.' already exists`.

## [v1.5.6] — 2026-07-13

### Cambiado
- CI: subidas las actions a versiones con Node 24 (`actions/checkout@v4` → `@v7`, `actions/setup-python@v5` → `@v6`) para eliminar el warning de deprecación de Node.js 20.

## [v1.5.5] — 2026-07-13

### Arreglado
- CI: `deploy-nara` clona el repo si no existe `.git` (el runner self-hosted no conserva el repo entre jobs) en vez de asumir `git pull`, evitando `fatal: not a git repository` (exit 128).

## [v1.5.4] — 2026-07-13

### Cambiado
- CI: separadas las etapas `build` y `publish` en ambos workflows.
  - `docker-image.yml`: `build` construye la imagen y la sube como artifact; `publish` la descarga, hace login y push a Docker Hub (tag de versión + `latest`). `deploy-nara` ahora depende de `publish`.
  - `python-publish.yml`: `build` genera wheel+sdist y los sube como artifact; `publish` los descarga y los sube a PyPI con `twine` (en vez de `JRubics/poetry-publish`).

## [v1.5.3] — 2026-07-13

### Arreglado
- CI: `deploy-nara` ya no usa `actions/checkout` (que intentaba limpiar el workspace y chocaba con `logs/` de dueño root del contenedor → EACCES). Ahora hace `git pull` manual dentro del workspace ya clonado en el runner.

## [v1.5.2] — 2026-07-13

### Arreglado
- Dockerfile: el plugin `poetry-plugin-export` se instala ahora con `pip` (el mismo que instaló Poetry en la imagen base) en vez de `uv pip install --system`, porque Poetry no detectaba el plugin instalado por uv.

## [v1.5.1] — 2026-07-13

### Arreglado
- Dockerfile: se instala `poetry-plugin-export` antes de `poetry export`, porque Poetry 1.8.3 (en la imagen base) no incluye el comando `export` por defecto. Sin esto el build fallaba con `The requested command export does not exist.`

## [v1.5.0] — 2026-07-13

### Cambiado
- Dockerfile: `FROM` actualizado a la imagen base release `keitarodxs/aia-utils-base:v1.0.0` (publicada desde aia-utils PR #1). Antes usaba el tag de rama `feat-dockerfile-base-breaking`.

---

## [v1.4.1] — 2026-07-13

### Arreglado
- `docker-image.yml`: `deploy-nara` ahora usa `actions/checkout@v4` con `clean: false`. Antes el checkout intentaba borrar `logs/` (archivos de dueño root del contenedor) y fallaba con `EACCES: permission denied`, rompiendo el despliegue en nara.

---

## [v1.4.0] — 2026-07-13

### Cambiado
- Dockerfile: ahora usa la imagen base del ecosistema `keitarodxs/aia-utils-base:feat-dockerfile-base-breaking` (PR #1 de aia-utils). Se elimina del Dockerfile la instalación de Python, apt (git/curl/ca-certificates/build-essential), Poetry, uv, Node.js y drawio-mcp-server, que ya vienen en la base. El Dockerfile queda mucho más pequeño: solo copia el código, instala deps con `uv` y define ENV/VOLUME/EXPOSE/start.sh.

---

## [v1.3.2] — 2026-07-13

### Arreglado
- `python-publish.yml`: añadido job `test` (pytest) y `build` ahora tiene `needs: test`. Antes la publicación en PyPI no validaba tests; ahora si los tests fallan no se publica la versión (igual que `docker-image.yml`).

---

## [v1.3.1] — 2026-07-13

### Documentación
- `README.md`: añadida nota en "Despliegue (Docker)" explicando que la imagen instala deps con `uv` (`uv pip install --system`) mientras el proyecto sigue con Poetry.

---

## [v1.3.0] — 2026-07-13

### Cambiado
- Dockerfile: instalación de dependencias migrada de `pip install` a `uv pip install --system` (10-100x más rápido). El proyecto sigue gestionado con poetry (`pyproject.toml` + `poetry.lock`); se usa `poetry export` para generar `requirements.txt` y luego `uv` para instalar. También el paquete propio se instala con `uv pip install --system .`.

---

## [v1.2.9] — 2026-07-13

### Arreglado
- `docker-compose.yml`: healthcheck corregido. Antes hacía `GET /mcp` plano y el MCP Streamable HTTP respondía 406, marcando el contenedor `unhealthy` (falso positivo). Ahora hace `POST initialize` con headers MCP válidos y espera 200, así el contenedor queda `healthy` de verdad.

---

## [v1.2.8] — 2026-07-13

### Arreglado
- `docker-image.yml`: `deploy-nara` usa `runs-on: [self-hosted, nara]`. Para que el runner `nara` coincida, se le agregó la **etiqueta `nara`** (custom) vía API (antes solo tenía `self-hosted, Linux, X64`, y el job se quedaba en cola). Re-trigger del deploy con tag v1.2.8.

---

## [v1.2.7] — 2026-07-13

### Arreglado
- `docker-image.yml`: eliminado el job `check-nara`. El `GITHUB_TOKEN` de integración no tiene permiso para leer `/actions/runners` (HTTP 403 "Resource not accessible by integration"), así que siempre reportaba "nara NO disponible" y el despliegue nunca corría aunque nara estuviera online. Ahora `deploy-nara` corre directo en el runner `nara` (`runs-on: [self-hosted, nara]`) con `continue-on-error` + `timeout-minutes: 10`: si nara está up despliega, si está down hace timeout limpio (run queda verde).

---

## [v1.2.6] — 2026-07-12

### Cambiado
- Dockerfile: base actualizada de `python:3.11-slim` a `python:3.13-slim` (Python más reciente, drop-in sin otros cambios)

---

## [v1.2.5] — 2026-07-12

### Diagnóstico
- `docker-image.yml`: `check-nara` ahora imprime la respuesta cruda de la API y el valor de `ONLINE` para diagnosticar por qué reporta "nara NO disponible" en el workflow aunque el runner está online localmente

---

## [v1.2.4] — 2026-07-12

### Arreglado
- `docker-image.yml`: `check-nara` ahora tiene `permissions: actions: read` para que el `GITHUB_TOKEN` pueda consultar el estado del runner nara (antes devolvía vacío y reportaba "nara NO disponible" aunque estaba online)
- `docker-compose.yml`: `env_file` ahora es `required: false`, así el despliegue no falla si no existe `.env` en nara (el Dockerfile ya define defaults vía ENV)

---

## [v1.2.3] — 2026-07-12

### Arreglado
- `docker-image.yml`: `check-nara` ahora publica output `online=true|false` en lugar de hacer `exit 1` con `continue-on-error`. Antes, el `exit 1` se trataba como success para `needs`, así que `deploy-nara` corría igual y fallaba aunque nara estuviera offline. Ahora `deploy-nara` se skipea limpio (run queda verde) cuando nara no está disponible.

---

## [v1.2.2] — 2026-07-12

### Arreglado
- `docker-image.yml`: corregida indentación del paso "Build & Push to Docker Hub" (`uses`/`with`/`env` a 8 espacios bajo el `- name:`). El YAML inválido hacía que GitHub ignorara el workflow y solo corriera el de poetry.

---

## [v1.2.1] — 2026-07-12

### Arreglado
- `docker-image.yml`: corregida indentación inválida en el job `build` (rompía el parseo del workflow)
- `docker-image.yml`: `TAG_NAME` restaurado usando `github.ref_name` para que la imagen se publique con el tag del release (ej. `keitarodxs/aia-mcp:v1.2.1`) y no solo `latest`

---

## [v1.2.0] — 2026-07-12

### Cambiado
- Optimización del Dockerfile (build ~3-5x más rápido):
  - Dependencias Python instaladas vía `pip install` desde `requirements.txt` exportado con `poetry export` (en lugar de `poetry install`, que resuelve lento)
  - Reorden de capas: `pyproject.toml` + `poetry.lock` se copian ANTES del código fuente, así la capa de dependencias queda cacheada y solo se reconstruye si cambian las deps
  - `COPY` del código fuente movido DESPUÉS de instalar dependencias

---

## [v1.1.9] — 2026-07-12

### Cambiado
- Actions subidas a v4/v5 (evita warning de deprecación de Node 20 en runners)

---

## [v1.1.8] — 2026-07-12

### Cambiado
- Fix del despliegue best-effort en self-hosted runner: `check-nara` hace `exit 1` si el runner no está online (con `continue-on-error`), y `deploy-nara` usa `if: needs.check-nara.result == 'success'` en lugar de depender de output propagado (frágil)

---

## [v1.1.7] — 2026-07-12

### Cambiado
- README y CHANGELOG actualizados a la realidad actual del proyecto (servidores, puertos, Docker, tests)
- Se omite mención a infraestructura local del README/CHANGELOG

---

## [v1.1.6] — 2026-07-12

### Agregado
- Tests unitarios (`pytest`) para `wahapedia` (slug/normalización/facciones) y `airbnb` (serialización MongoDB / formato iCal)
- Job `test` en CI que corre `poetry run pytest` antes del build de Docker

### Cambiado
- `docker-compose.yml` apunta a `keitarodxs/aia-mcp:latest` (sin build local); agrega puertos 3000/3333 de drawio-mcp; quita puerto 7408 (mcp-ssh) y volumen SSH

---

## [v1.1.5] — 2026-07-12

### Cambiado
- Eliminado `mcp-ssh` y el toolchain de Go de la imagen Docker (ya no requerido)
- Build de Docker restringido a `linux/amd64` (compatible con `nara`)
- `poetry install` conserva el grupo dev (`mcp-swagger-ui`) requerido por el servidor swagger
- `drawio-mcp-server` fijado a versión `2.2.0`

---

## [v1.1.0] — 2026-07-12

### Agregado
- Workflows de GitHub Actions: `docker-image.yml` (publica `keitarodxs/aia-mcp` en Docker Hub) y `python-publish.yml` (publica el paquete en PyPI)
- `drawio-mcp-server` (npm) instalado en la imagen; expone HTTP `:3000` + WS `:3333`
- `Node.js 20` en la imagen (prerrequisito de drawio-mcp-server)

---

## [v1.0.0] — 2026-03-22

### Agregado
- Primera versión estable con los servidores: temperatura, wahapedia, monitor, shell, airbnb, charts, email, mangadex, swagger
- CLI `mcp` con `all --http` y puertos por servidor
- Variables de entorno y `.env.example`

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
- Caché de respuestas Wahapedia en disco (configurable vía variables de entorno)
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
