# Changelog

Todos los cambios notables de aia-mcp se documentan aquí.
Servidores MCP custom para el agente amanda-IA.

---

## [v1.11.0] — 2026-09-01

> **MINOR** — `mcp_banco_bci`: nueva tool `get_bci_ingresos_mes` que consulta
> `bci.transacciones` en Atlas y devuelve los ingresos (abonos) de un mes
> con su total. Reconstruye la lógica del viejo `get_bci_cartola_ingresos`
> que se eliminó en `703ec35` (cuando se removió toda la lógica BCI del
> email MCP), pero ya no parsea PDFs — lee directo de MongoDB.

### Agregado
- **`mcp_banco_bci/server.py:get_bci_ingresos_mes(period="YYYY-MM", limit=50)`**:
  - Filtra `bci.transacciones` por regex sobre el campo `fecha` (string
    `dd-mm-yyyy`) usando el token `-MM-YYYY`.
  - Devuelve total de ingresos (suma de `abono`), total de cargos, cantidad
    de movimientos y la lista de abonos con fecha, descripción, sucursal
    y monto, ordenados desc por fecha.
  - `limit` solo aplica a la lista mostrada; el total SIEMPRE incluye
    todos los abonos del mes.
  - Helper `_to_float` para normalizar `abono`/`cargo` (algunos vienen
    como string en la colección).
- Lee `MONGODB_URI` (con fallback `MONGODB_URI_LOCAL`) del entorno.

### Notas
- Prerequisito: que `sync_bci_trx` haya corrido para ese mes. Si la
  colección está vacía devuelve mensaje sugiriendo correrlo.
- Verificado localmente con `period=2026-08`: 4 abonos, $604.980 total.
- Tras mergear, redesplegar `keitarodxs/aia-mcp:v1.11.0` en nara.

---

## [v1.10.0] — 2026-09-01

> **MINOR** — `mcp_banco_bci`: `sync_bci_trx` pasa a fire-and-forget.
> El cliente ya no se bloquea ni aborta por timeout mientras el pipeline
> BCI corre en aia-jobs. Nueva capacidad: `job_id` para polling.

### Agregado
- **`mcp_banco_bci/server.py`**: `sync_bci_trx` ahora arranca las 3 fases del
  pipeline en una background task (`asyncio.create_task`) y devuelve de
  inmediato un `job_id` (uuid12) junto al periodo. El registro local de jobs
  vive en `_JOBS` (dict protegido por `_JOBS_LOCK`) y se actualiza a medida
  que cada fase avanza (`pending → running → ok|error → completed`).
- **`get_bci_job_status(job_id=...)`**: acepta un `job_id` opcional. Cuando
  se entrega, devuelve el detalle del job local (status, started_at,
  finished_at, error y el resultado por fase con sus métricas:
  `downloaded`/`already_existed`, `transformed`/`skipped`/`errors`,
  `synced`/`skipped_duplicate`/`errors`/`total_new`). Cuando `job_id` viene
  vacío, conserva el comportamiento anterior (estado global de aia-jobs)
  y además lista los últimos 10 jobs locales del MCP.
- **`.env`**: nueva variable `AIA_JOBS_TIMEOUT=1800` (30 min) como default
  para los HTTP client hacia aia-jobs. Anteriormente el default en código
  era 600s, suficiente para el ping `/openapi.json` pero insuficiente para
  el pipeline completo.

### Cambiado
- **Default en código** (`server.py`): `AIA_JOBS_TIMEOUT` default sube
  `600 → 1800`. La variable de entorno `.env` toma precedencia.

### Notas
- El cambio es compatible con llamadas que ignoran `job_id`: el pipeline
  sigue ejecutándose íntegro en aia-jobs; lo único que cambia es que el MCP
  ya no espera el resultado. Si el cliente quiere esperar, basta con un
  loop de `get_bci_job_status(job_id=...)` hasta `status == completed`.
- `aia-jobs` no expone endpoint para lanzar jobs async; el background se
  maneja en el MCP. Esto mantiene a aia-jobs simple y la lógica de
  orquestación donde corresponde (un único punto de retry y registro).
- Tras mergear, redesplegar la imagen `keitarodxs/aia-mcp:v1.10.0` en nara.

---

## [v1.9.3] — 2026-09-01

> **PATCH** — bugfix bloqueante: el servidor `mcp_banco_bci` respondía
> `421 Misdirected Request` a cualquier conexión con `Host` distinto de
> `127.0.0.1` / `localhost`. Lo dejaba inutilizable desde opencode en nara.

### Corregido
- **`mcp_banco_bci/server.py`**: la instancia `FastMCP("banco_bci")` no
  recibía el parámetro `host`, así que el SDK usaba el default `127.0.0.1` y
  activaba el guard anti-DNS-rebinding interno (`TransportSecuritySettings`).
  Este guard rechaza con 421 cualquier `Host` que no esté en
  `["127.0.0.1:*", "localhost:*", "[::1]:*"]`. Ahora `FastMCP` se construye
  con `host=os.environ.get("FASTMCP_HOST", "127.0.0.1")`, alineado con el
  resto de servers (`temperatura`, `wahapedia`, `monitor`, `airbnb`,
  `charts`, `email`, `mangadex`). Cuando el cli lanza los servers con
  `FASTMCP_HOST=0.0.0.0`, el guard se desactiva y el server acepta conexiones
  desde cualquier Host.

### Notas
- El guard anti-DNS-rebinding del SDK **es deseable** cuando el server corre
  en stdio local. Por eso el default sigue siendo `127.0.0.1` si no hay
  `FASTMCP_HOST` en el entorno.
- En nara el contenedor `keitarodxs/aia-mcp:v1.9.3` permitirá a opencode
  conectarse a `:8011/mcp` sin necesidad de header `Host` especial.

---

## [v1.9.2] — 2026-09-01

> **PATCH** — dos bugfixes de port-mapping y selección de puerto. Sin
> cambios de API ni de tools. Compatible hacia atrás con v1.9.1.

### Corregido
- **CI/CD (`.github/workflows/docker-image.yml`)**: se elimina `-p 8005:8005`
  del `docker run` del job `deploy-nara`. El server `shell` fue removido del
  registro `SERVERS` en `f5682cf`, pero el workflow seguía publicando el puerto,
  lo que provocaba un bind zombie cada vez que se redesplegaba. El puerto ya
  no se expone desde el contenedor y se libera en el próximo redeploy.
- **`mcp_cli/cli.py`**: la ruta `mcp <server> --http` ignoraba
  `SERVER_PORTS[server_name]` y caía siempre en `:8001` (hardcodeado). Ahora
  resuelve el puerto desde el registro con `SERVER_PORTS[server_name]`,
  manteniendo fallback a `FASTMCP_PORT` env / `8001`. Esto afecta a levantar
  un server individual por HTTP; `aia-mcp all --http` ya estaba correcto.

### Notas
- El server `swagger` (`:8010`) sigue caído en nara por dependencia faltante
  (`fastapi`); no se repara en este patch porque requiere cambio de imagen.
- El contenedor actual en nara (`keitarodxs/aia-mcp:v1.9.0`) debe
  redesplegarse tras mergear para que el cambio en el workflow tome efecto.

---

## [v1.9.1] — 2026-09-01

> **PATCH** — regularización del release v1.9.0: expone `mcp_banco_bci` en
> el cliente MCP local y confirma la eliminación definitiva del server `shell`
> removido en v1.8.x. Sin cambios funcionales en código.

### Cambiado
- **cliente MCP local**: `~/.config/opencode/opencode.json` ahora incluye
  `aia-mcp-bci` apuntando a `http://nara:8011/mcp`, alineado con el puerto
  declarado en `mcp_cli/cli.py` (`SERVER_PORTS["banco_bci"] = 8011`).
- **shell**: confirmado fuera del registro `SERVERS` (eliminado en `f5682cf`).
  El proceso zombie `:8005` en nara corresponde a la imagen `aia-mcp:v1.9.0`
  previa a la limpieza y se reciclará en el próximo redeploy.

### Notas
- El server `swagger` (`:8010`) sigue caído en nara por dependencia faltante
  (`fastapi` no instalado en el venv de producción); no se repara en este
  patch porque requiere cambio de imagen.
- No se bumpa a MINOR porque no se agrega ninguna capacidad nueva, solo se
  documenta y se corrige la configuración del cliente.

---

## [v1.9.0] — 2026-09-01

> **MINOR** — nueva funcionalidad compatible hacia atrás: el servidor
> `mcp_banco_bci` se conecta a la API HTTP de `aia-jobs` (en `:8080`) y expone
> el pipeline completo BCI como 3 tools MCP.

### Agregado
- **mcp_banco_bci**: nuevo tool `sync_bci_trx(year, month, batch_size,
  skip_email_download, skip_transform, sender)` que orquesta el pipeline
  completo en una sola llamada:
  1. `POST /api/jobs/sync-bci-emails` (Yahoo IMAP → `email.emails`)
  2. `POST /api/jobs/sync-historical-bci` (PDFs → `bci.cartolas`)
  3. `POST /api/jobs/sync-trx` (`bci.cartolas` → `bci.transacciones` en Atlas)
  Cada fase es idempotente y se puede saltar con `skip_*` para re-runs
  parciales. Las llamadas HTTP se hacen en `asyncio.to_thread` para no
  bloquear el event loop del MCP.
- **mcp_banco_bci**: nuevo tool `get_bci_job_status()` — estado instantáneo
  del último job en `aia-jobs` (`running`, `current_job`, `progress`,
  `last_result`, `total_cartolas_bci`).
- **mcp_banco_bci**: nuevo tool `get_bci_api_health()` — verifica que la
  API de aia-jobs responde y reporta su versión.
- **mcp_cli/cli.py**: registrado `banco_bci` en `SERVERS` con puerto HTTP
  `8011`.
- **.env.example**: nueva variable `AIA_JOBS_API_URL` (default
  `http://nara:8080`) y `AIA_JOBS_TIMEOUT` (default `600`s).

### Cambiado
- **mcp_banco_bci/server.py**: importa `httpx` para hablar con `aia-jobs`.
  Antes solo leía MongoDB directamente; ahora consume la API del listener
  para la parte de orquestación (mantiene la lectura directa para los tools
  de consulta).

---

## [v1.8.2] — 2026-07-15

### Corregido
- **Resiliencia del sync ante `SSL: BAD_LENGTH`**: el motor unificado `_do_sync`
  moría a mitad de un rango grande (Yahoo IMAP) con
  `socket error: [SSL: BAD_LENGTH] bad length`. Ahora el fetch de cada mensaje
  pasa por `_fetch_one`, que reintenta (3 intentos) y **reconecta IMAP** ante
  errores de socket/SSL, continuando con el resto de UIDs. El sync ya no se
  aborta por una caída transitoria de la conexión; los ~515 correos faltantes del
  rango dic-2025→jul-2026 se descargan en el reintento (dedup por `message_id`
  salta los ~485 ya guardados).

## [v1.8.1] — 2026-07-15

### Corregido
- **Bug crítico del MCP HTTP del email**: `mcp_cli/cli.py` levantaba el server de
  email con `workers=4` (Opción C de v1.7.8). Streamable HTTP guarda el estado de
  sesión en memoria del proceso; con workers>1 uvicorn spawnea procesos separados
  y el session manager no se comparte, causando "Connection reset by peer" en el
  handshake (`initialize`). Ahora el email MCP (y todos) corre con **1 worker**
  (`EMAIL_WORKERS` default 1). La concurrencia la aporta el thread pool de anyio
  (tools bloqueantes en `to_thread`), no los workers. El MCP HTTP vuelve a
  responder y permite llamar `sync_emails_since` y `get_email_sync_status`.

## [v1.8.0] — 2026-07-15

> **Salto de MINOR** (antes veníamos haciendo cambios de funcionalidad como patch
> en v1.7.x; corregido a MINOR según SemVer: nuevos tools y refactor de motor =
> compatibilidad hacia atrás rota en la API interna de sync).

### Agregado
- Email MCP: nuevo tool `sync_emails_since(since_date, before_date="")` que
  sincroniza todos los correos del INBOX desde una fecha (IMAP `SEARCH SINCE
  BEFORE`), cubriendo rangos amplios (ej. dic 2025 a hoy) sin límite de
  antigüedad. Usa el motor genérico y reporta en `get_email_sync_status()`.

### Refactor: motor de sincronización único y genérico
- **Un solo motor** (`_do_sync`) para todas las variantes de sync: `sync_emails`
  (INBOX), `sync_emails_from` (por remitente), `sync_emails_since` (por fecha) y
  `sync_bci_cartolas` (cartolas BCI).
- **Un solo motor** (`_do_sync`) para todas las variantes de sync: `sync_emails`
  (INBOX), `sync_emails_from` (por remitente) y `sync_bci_cartolas` (cartolas BCI).
  Antes cada uno tenía su propia lógica duplicada.
- **Detección genérica de cartola**: un correo se marca como `kind:"bci_cartola"`
  si su remitente es `bcimail@bci.cl` **Y** el asunto contiene "Cartola" (case
  insensitive). El `period` se deriva del PDF igual que antes. Aplica a cualquier
  sync que traiga ese correo (no solo a `sync_bci_cartolas`).
- **Sin duplicados**: todas usan upsert por `message_id` (no `insert_one`).
- **Progreso unificado**: un solo estado en `email.sync_state` (`_id:"email_sync"`)
  con `mode` (inbox/from/bci), `scope`, `completed/total`. Se consulta con
  `get_email_sync_status()` (instantáneo, sin tocar Yahoo). Las tools `sync_*`
  son `async` y lanzan el trabajo en `anyio.to_thread` (no bloquean el server).
- Eliminados `get_bci_sync_status`, `_do_sync_bci` y las funciones de estado
  duplicadas (`_id:"bci"`). `sync_bci_cartolas` ahora busca por
  `FROM bcimail@bci.cl SUBJECT "Cartola" SINCE/BEFORE` en el rango de meses.

## [v1.7.10] — 2026-07-15

### Agregado
- Email MCP: nuevo tool `sync_emails_from(from_addr, limit=500)` que sincroniza a
  MongoDB **todos los correos de un remitente específico** en Yahoo, sin importar
  la antigüedad. A diferencia de `sync_emails` (que usa `SEARCH ALL` y solo ve los
  últimos N del INBOX), este usa `SEARCH FROM "remitente"`, que devuelve TODOS los
  UIDs de ese remitente aunque tengan 20 años. Solo guarda los nuevos: omite los
  que ya existen (dedup por `message_id`). Corre en `anyio.to_thread.run_sync`
  (no bloquea el event loop). `limit=0` trae todos los encontrados sin capar.

## [v1.7.9] — 2026-07-14

### Corregido (consistencia de cartolas BCI)
- Email MCP: `_imap_search_bci` buscaba la cartola del mes X en el mes **siguiente**,
  pero BCI envía la cartola del mes X **dentro del mes X** (ej. 2026-01 recibida
  2026-01-21, 2026-07 el 2026-07-05). Ahora busca en el mes del período y filtra por
  asunto `Cuenta Corriente` para descartar las `Cartola Trimestral Consumo` del mismo
  remitente. Antes, pedir "2026-01" devolvía la cartola 2026-02.
- Email MCP: `_fetch_bci_cartola` ahora hace `update_one(..., upsert=True)` por
  `message_id` en vez de `insert_one`. Así `force_refresh=True` reescribe el doc en
  vez de crear duplicados (se habían acumulado 5 duplicados idénticos por message_id).
- Limpieza en nara: eliminados 5 duplicados; descargada la cartola 2026-01 faltante.
  Ahora 7 cartolas (2026-01 a 2026-07), una por mes.

## [v1.7.8] — 2026-07-14

### Mejorado (no bloqueo del servidor de email)
- **Opción A (background sync)**: `sync_bci_cartolas` ahora lanza el trabajo pesado
  (fetch Yahoo + parse PDF) en un hilo vía `anyio.to_thread.run_sync` y devuelve
  inmediatamente ("🚀 Sync BCI en background iniciado…"). No congela el event loop.
- **Visibilidad**: nuevo tool `get_bci_sync_status()` que lee el estado persistido en
  MongoDB (`email.sync_state`) de forma instantánea (sin tocar Yahoo ni el PDF): mes
  actual, progreso `completed/total`, último error y resumen final. El estado se
  persiste en Mongo para que cualquier worker pueda consultarlo.
- **Opción C (workers)**: el server de email corre con `uvicorn.run(..., workers=N)`
  (default 4 vía env `EMAIL_WORKERS`) para mayor concurrencia y tolerancia a tools
  bloqueantes. Los demás servers quedan en 1 worker.
- `get_bci_cartola` y `get_bci_cartola_ingresos` ahora son `async` y ejecutan su
  lógica bloqueante en `anyio.to_thread.run_sync`, de modo que una descarga de Yahoo
  no bloquea a otros agentes conectados al mismo puerto.

## [v1.7.7] — 2026-07-14

### Corregido
- Email MCP: el `period` de cache de la cartola BCI se deriva del CONTENIDO del PDF (`PERIODO : ... al DD-MM-YYYY`), no del mes solicitado ni de la fecha de recepción. Antes, la ventana IMAP amplia hacía que la cartola de febrero se guardara con movimientos de marzo. Ahora cada cartola se guarda con su mes real.
- Email MCP: `_imap_search_bci` busca por fecha de recepción usando todo el mes siguiente (sin solapamiento entre meses contiguos).

## [v1.7.6] — 2026-07-14

### Corregido
- CI: `BCI_PDF_PASSWORD` corregido al RUT real de la cuenta (`17536222`, sin dígito verificador). El deploy v1.7.5 usaba `175362223` y fallaba al abrir el PDF cifrado de la cartola.

## [v1.7.5] — 2026-07-14

### Añadido
- Email MCP: `get_bci_cartola_ingresos(period, rut_password, force_refresh)` extrae solo los ingresos (abonos/transferencias recibidas) de la cartola BCI. El PDF cifrado se abre con el RUT (`BCI_PDF_PASSWORD` o arg `rut_password`). Detección por saldo diario + palabras clave de abono.
- Email MCP: `pdfplumber` como dependencia principal para parsear el PDF de la cartola.
- Tests: `tests/test_email_bci.py` + fixture PDF cifrado (`tests/fixtures/cartola_bci_fixture_enc.pdf`) que valida la detección de ingresos/cargos.
- CI: `deploy-nara` inyecta `BCI_PDF_PASSWORD` vía GitHub secret.

## [v1.7.4] — 2026-07-14

### Añadido
- CI: informe de tests estilo LTP (`tests/conftest.py`) que imprime tabla `Test | Result` + resumen `Total/Passed/Failed/Skipped/Error` y lo sube como artefacto `test-report-ltp` en el job `test`.

## [v1.7.3] — 2026-07-14

### Añadido
- Email MCP: extracción de adjuntos (PDF, etc.) en base64 dentro de `attachments` en `_parse_email`.
- Email MCP: cartolas BCI cache-first. `get_bci_cartola(period, force_refresh)` lee de MongoDB por período `YYYY-MM`; si no existe, descarga de Yahoo (`bcimail@bci.cl`) y guarda. Cada mes = nueva clave de cache, así las cartolas nuevas se resuelven solas.
- Email MCP: `sync_bci_cartolas(months_back)` sincroniza las últimas N cartolas (una por mes) y `list_bci_cartolas()` lista las en cache.
- Tests: `tests/test_email.py` cubre parser de adjuntos PDF (base64), resolución de período, ventana de búsqueda IMAP y lógica cache-first (hit/miss/force_refresh) con mocks.

## [v1.7.2] — 2026-07-14

### Cambiado
- CI: `deploy-nara` inyecta `YAHOO_EMAIL`, `YAHOO_APP_PASSWORD` y `MONGODB_URI` al contenedor vía GitHub Actions secrets, para habilitar el servidor de email (Yahoo IMAP) en nara.

## [v1.7.1] — 2026-07-14

### Cambiado
- CI: `deploy-nara` monta los volúmenes en `/opt/aia-mcp/logs` y `/opt/aia-mcp/.aia` (ruta persistente de nara fuera del workspace del runner), según preferencia del usuario.

## [v1.7.0] — 2026-07-14

### Cambiado
- CI: `deploy-nara` monta los volúmenes en rutas persistentes de nara (`/home/ranmadxs/aia-mcp/logs` y `/home/ranmadxs/aia-mcp/.aia`) en vez del workspace del runner. Se crea `/home/ranmadxs/aia-mcp/config` para configuración futura.

## [v1.6.9] — 2026-07-14

### Cambiado
- CI: `deploy-nara` monta los volúmenes en rutas persistentes de nara (`/home/ranmadxs/aia-mcp/logs` y `/home/ranmadxs/aia-mcp/.aia`) en vez del workspace del runner. Se crea `/home/ranmadxs/aia-mcp/config` para configuración futura.

## [v1.6.8] — 2026-07-14

### Arreglado
- CI: subidas `upload-artifact`/`download-artifact` a `@v7`/`@v8` (Node 24) para eliminar el warning de deprecación de Node.js 20 en los jobs `build` y `publish`.

## [v1.6.7] — 2026-07-14

### Arreglado
- CI: cada test de `test-nara` (temperatura/wahapedia/airbnb) ahora espera a que su puerto responda con reintentos (60x3s). Antes airbnb daba `empty reply` porque arrancaba ~6s después de los otros y el curl corría sin espera.

## [v1.6.6] — 2026-07-14

### Arreglado
- Dockerfile: se copia `README.md` al contenedor antes de `pip install .`. Sin esto el build fallaba con `Readme path /app/README.md does not exist` (el `pyproject.toml` referencia `readme = "README.md"`).

## [v1.6.5] — 2026-07-14

### Arreglado
- Dockerfile: el paquete propio se instala con `pip install .` (no `uv pip install . || true`). El `|| true` enmascaraba un fallo de instalación que dejaba el entry point `aia-mcp` ausente (`exec: aia-mcp: not found`). Ahora el build falla si no se instala.

## [v1.6.4] — 2026-07-14

### Arreglado
- Entry point renombrado `mcp` → `aia-mcp` en `pyproject.toml` para evitar colisión con el script `mcp` de la librería `mcp` (que no tiene el comando `all`). El `start.sh` ahora corre `aia-mcp all --http`. Sin esto el contenedor entraba en restart loop con `No such command 'all'`.

## [v1.6.3] — 2026-07-14

### Arreglado
- Deps: `mcp` ahora usa el extra `[cli]` (`mcp = {version = ">=1.0", extras = ["cli"]}`) para instalar `typer`. Sin esto el entry point `mcp` fallaba con `Error: typer is required` y el contenedor entraba en restart loop (puertos nunca escuchaban). Regenerado `poetry.lock`.

## [v1.6.2] — 2026-07-13

### Arreglado
- CI: `test-nara` aumenta la espera del puerto 8001 a 60 intentos x 5s (300s) y vuelca `docker logs aia-mcp` si hay timeout, para diagnosticar arranque de los servidores MCP.

## [v1.6.1] — 2026-07-13

### Arreglado
- CI: `test-nara` ahora espera a que el puerto 8001 (temperatura) responda antes de testear (los servidores tardan en arrancar tras el deploy). Subidas `upload-artifact`/`download-artifact` a `@v5` (Node 24) para eliminar el warning de Node.js 20.

## [v1.6.0] — 2026-07-13

### Agregado
- CI: nueva etapa `test-nara` que corre en el runner `nara` tras el deploy y hace curls HTTP a los servidores MCP levantados en el contenedor (temperatura :8001, wahapedia :8002, airbnb :8006) verificando el handshake `initialize` sobre `/mcp`. Best-effort (`continue-on-error`).

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
