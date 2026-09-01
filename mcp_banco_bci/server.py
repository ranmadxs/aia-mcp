"""Banco BCI - orquestacion del pipeline BCI via API de aia-jobs."""

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

# URL base de la API HTTP de aia-jobs (expone /api/jobs/* en :8080).
# Si AIA_JOBS_API_URL esta definida se usa tal cual. Si no, se descubre en
# runtime leyendo el gateway por defecto del contenedor desde /proc/net/route:
# ese gateway es el host de Docker, donde aia-jobs corre en red `host` y
# escucha en :8080. Asi no depende de IPs fijas entre reinicios.
_DEFAULT_AIA_JOBS_URL = "http://aia-jobs:8080"


def _discover_aia_jobs_url() -> str:
    """Descubre la URL de aia-jobs usando el gateway de la red del contenedor.

    Lee /proc/net/route y devuelve http://<gateway>:8080. Funciona aunque
    Docker asigne otra subred al bridge tras un reinicio del daemon.
    """
    import struct
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                fields = line.strip().split()
                # Destination 00000000 = ruta por defecto
                if fields[1] != "00000000":
                    continue
                gw_hex = fields[2]
                gw = ".".join(
                    str(int(gw_hex[i:i+2], 16))
                    for i in (6, 4, 2, 0)
                )
                return f"http://{gw}:8080"
    except Exception:
        pass
    return _DEFAULT_AIA_JOBS_URL


AIA_JOBS_API_URL = os.getenv("AIA_JOBS_API_URL") or _discover_aia_jobs_url()
AIA_JOBS_TIMEOUT = float(os.getenv("AIA_JOBS_TIMEOUT", "1800"))

mcp = FastMCP(
    "banco_bci",
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("FASTMCP_PORT", "8011")),
)


# ── Cliente HTTP a aia-jobs ──────────────────────────────────────────────────

def _jobs_post(path: str, payload: dict | None = None) -> dict:
    """POST a la API de aia-jobs. Devuelve el JSON o un dict con `error`."""
    url = f"{AIA_JOBS_API_URL.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=AIA_JOBS_TIMEOUT) as c:
            r = c.post(url, json=payload or {})
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _jobs_get(path: str) -> dict:
    """GET a la API de aia-jobs. Devuelve el JSON o un dict con `error`."""
    url = f"{AIA_JOBS_API_URL.rstrip('/')}{path}"
    try:
        with httpx.Client(timeout=AIA_JOBS_TIMEOUT) as c:
            r = c.get(url)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# ── Registro de jobs fire-and-forget ──────────────────────────────────────────
# Cuando opencode (o cualquier cliente MCP) invoca sync_bci_trx, el pipeline
# puede tardar varios minutos (IMAP + PDF + Atlas). Si el MCP esperara
# sincronamente, el cliente abortaba por timeout. En vez de eso:
#   1. sync_bci_trx arranca el pipeline en background (asyncio.create_task)
#   2. devuelve inmediatamente un job_id
#   3. get_bci_job_status(job_id=...) reporta el progreso sin volver a
#      tocar aia-jobs; consulta el estado en este registro local.

_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = asyncio.Lock()


async def _set_job(job_id: str, **fields: Any) -> None:
    async with _JOBS_LOCK:
        rec = _JOBS.setdefault(job_id, {
            "job_id": job_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "phases": [],
        })
        rec.update(fields)


async def _append_phase(job_id: str, phase: dict[str, Any]) -> None:
    async with _JOBS_LOCK:
        _JOBS[job_id]["phases"].append(phase)


async def _run_pipeline(
    job_id: str,
    year: int,
    month: int,
    batch_size: int,
    skip_email_download: bool,
    skip_transform: bool,
    sender: str,
) -> None:
    """Ejecuta las 3 fases secuencialmente y actualiza _JOBS[job_id]."""
    await _set_job(job_id, status="running", started_at=datetime.now(timezone.utc).isoformat())

    # Fase 1: descarga de emails
    if not skip_email_download:
        phase: dict[str, Any] = {"name": "sync-bci-emails", "status": "running"}
        await _append_phase(job_id, phase)
        r1 = await asyncio.to_thread(
            _jobs_post,
            "/api/jobs/sync-bci-emails",
            {"sender": sender, "year": year, "month": month},
        )
        phase["result"] = r1
        phase["status"] = "error" if r1.get("error") else "ok"
        phase["finished_at"] = datetime.now(timezone.utc).isoformat()
        if r1.get("error"):
            await _set_job(job_id, status="error", error=f"fase 1: {r1['error']}")
            return

    # Fase 2: transformacion de PDFs → cartolas
    if not skip_transform:
        phase = {"name": "sync-historical-bci", "status": "running"}
        await _append_phase(job_id, phase)
        r2 = await asyncio.to_thread(
            _jobs_post,
            "/api/jobs/sync-historical-bci",
            {"months_back": 0},
        )
        phase["result"] = r2
        phase["status"] = "error" if r2.get("error") else "ok"
        phase["finished_at"] = datetime.now(timezone.utc).isoformat()
        if r2.get("error"):
            await _set_job(job_id, status="error", error=f"fase 2: {r2['error']}")
            return

    # Fase 3: sync de movimientos a Atlas
    phase = {"name": "sync-trx", "status": "running", "batch_size": batch_size}
    await _append_phase(job_id, phase)
    r3 = await asyncio.to_thread(
        _jobs_post,
        "/api/jobs/sync-trx",
        {"batch_size": batch_size},
    )
    phase["result"] = r3
    phase["status"] = "error" if r3.get("error") else "ok"
    phase["finished_at"] = datetime.now(timezone.utc).isoformat()
    if r3.get("error"):
        await _set_job(job_id, status="error", error=f"fase 3: {r3['error']}")
        return

    await _set_job(
        job_id,
        status="completed",
        finished_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Tools de orquestacion del pipeline BCI ───────────────────────────────────


@mcp.tool()
async def sync_bci_trx(
    year: int = 0,
    month: int = 0,
    batch_size: int = 500,
    skip_email_download: bool = False,
    skip_transform: bool = False,
    sender: str = "bcimail@bci.cl",
) -> str:
    """
    Lanza el pipeline completo de BCI en background y devuelve un `job_id`
    inmediato. Las 3 fases se ejecutan en orden en una tarea background:

      1. /sync-bci-emails    Yahoo IMAP → email.emails
      2. /sync-historical-bci email.emails (PDFs) → bci.cartolas
      3. /sync-trx            bci.cartolas → bci.transacciones (Atlas)

    Esto evita que el cliente MCP (opencode, CLI, etc.) aborte por timeout
    mientras aia-jobs procesa IMAP + PDF + Atlas, que puede tardar minutos.

    Para seguir el progreso usa `get_bci_job_status(job_id=<id>)`.

    Args:
        year:               Año (ej: 2026). 0 = año actual.
        month:              Mes (1-12). 0 = mes actual.
        batch_size:         Máx. movimientos NUEVOS a subir a Atlas (fase 3).
        skip_email_download:Si True, salta fase 1 (asume emails ya descargados).
        skip_transform:     Si True, salta fase 2 (asume cartolas ya transformadas).
        sender:             Remitente BCI en Yahoo (default: bcimail@bci.cl).

    Returns:
        Markdown con `job_id` y el estado inicial del job.
    """
    if not year:
        year = datetime.now().year
    if not month:
        month = datetime.now().month

    job_id = uuid.uuid4().hex[:12]
    await _set_job(job_id, year=year, month=month, sender=sender, batch_size=batch_size)

    asyncio.create_task(
        _run_pipeline(
            job_id=job_id,
            year=year,
            month=month,
            batch_size=batch_size,
            skip_email_download=skip_email_download,
            skip_transform=skip_transform,
            sender=sender,
        )
    )

    return (
        f"## Sync BCI TRX lanzado\n\n"
        f"- **job_id**: `{job_id}`\n"
        f"- **periodo**: {year}-{month:02d}\n"
        f"- **API**: `{AIA_JOBS_API_URL}`\n"
        f"- **batch_size**: {batch_size}\n"
        f"- **fases**: "
        f"{'sync-bci-emails → ' if not skip_email_download else ''}"
        f"{'sync-historical-bci → ' if not skip_transform else ''}"
        f"sync-trx\n\n"
        f"Consulta el progreso con `get_bci_job_status(job_id=\"{job_id}\")`."
    )


@mcp.tool()
def get_bci_job_status(job_id: str = "") -> str:
    """
    Estado del pipeline BCI en el MCP.

    - Si `job_id` se entrega: muestra el detalle de ese job local
      (lanzado por `sync_bci_trx`).
    - Si `job_id` está vacío: muestra el estado global de aia-jobs (instantáneo
      desde Mongo, sin tocar Yahoo) más el listado de jobs locales recientes.

    Returns:
        Markdown con running, current_job, last_run, last_result, progress
        y total de cartolas en bci.cartolas, mas jobs locales del MCP.
    """
    lines: list[str] = []

    if job_id:
        rec = _JOBS.get(job_id)
        if not rec:
            return f"❌ Job `{job_id}` no encontrado en este MCP."
        lines.append(f"## Job BCI `{job_id}`\n")
        lines.append(f"- **status**: `{rec.get('status')}`")
        lines.append(f"- **periodo**: {rec.get('year')}-{rec.get('month'):02d}")
        lines.append(f"- **created_at**: {rec.get('created_at')}")
        if rec.get("started_at"):
            lines.append(f"- **started_at**: {rec.get('started_at')}")
        if rec.get("finished_at"):
            lines.append(f"- **finished_at**: {rec.get('finished_at')}")
        if rec.get("error"):
            lines.append(f"- **error**: {rec.get('error')}")
        phases = rec.get("phases", [])
        if phases:
            lines.append(f"\n### Fases ({len(phases)})\n")
            for p in phases:
                icon = "✅" if p.get("status") == "ok" else ("❌" if p.get("status") == "error" else "🔄")
                lines.append(f"{icon} **{p.get('name')}** — `{p.get('status')}`")
                if "finished_at" in p:
                    lines.append(f"   - finished_at: {p['finished_at']}")
                if "batch_size" in p:
                    lines.append(f"   - batch_size: {p['batch_size']}")
                if res := p.get("result"):
                    for k, v in res.items():
                        lines.append(f"   - `{k}`: {v}")
        return "\n".join(lines)

    # Sin job_id: estado global + listado de jobs locales
    s = _jobs_get("/api/jobs/status")
    if err := s.get("error"):
        lines.append(f"❌ Error consultando aia-jobs: {err}")
    else:
        lines.append("## Estado jobs BCI (aia-jobs)\n")
        lines.append(f"**API**: `{AIA_JOBS_API_URL}`")
        lines.append(f"**Running**: {'🔄 SÍ' if s.get('running') else '✅ no'}")
        if s.get("current_job"):
            lines.append(f"**Job actual**: `{s.get('current_job')}`")
        if s.get("last_run"):
            lines.append(f"**Última corrida**: {s.get('last_run')}")
        if s.get("progress"):
            lines.append(f"**Progreso**: {s.get('progress')}")
        if s.get("last_result"):
            lines.append("\n**Último resultado**:")
            for k, v in s["last_result"].items():
                lines.append(f"  - `{k}`: {v}")
        lines.append(f"\n**Total cartolas en bci.cartolas**: {s.get('total_cartolas_bci', 0)}")

    if _JOBS:
        lines.append(f"\n## Jobs locales en este MCP ({len(_JOBS)})\n")
        for jid, rec in list(_JOBS.items())[-10:]:
            icon = {
                "pending": "⏳",
                "running": "🔄",
                "completed": "✅",
                "error": "❌",
            }.get(rec.get("status"), "•")
            lines.append(
                f"{icon} `{jid}` — {rec.get('status')} — "
                f"{rec.get('year')}-{rec.get('month', 0):02d} — "
                f"{len(rec.get('phases', []))} fases"
            )

    return "\n".join(lines)


@mcp.tool()
def get_bci_ingresos_mes(period: str = "", limit: int = 50) -> str:
    """
    Ingresos (abonos) de la cuenta corriente BCI para un período, leídos
    directamente desde MongoDB Atlas (`bci.transacciones`).

    Reconstruye la lógica eliminada en `703ec35` (cuando se removió
    `get_bci_cartola_ingresos` del email MCP), pero ahora consulta la
    colección sincronizada por `sync_bci_trx` en vez de parsear el PDF.

    Args:
        period: Período "YYYY-MM" (ej: "2026-08"). Vacío = mes actual.
        limit:  Máx. ingresos a listar (default 50). El total siempre
                incluye TODOS los abonos del mes, no se trunca.

    Returns:
        Total de ingresos del mes, cantidad de movimientos (abonados
        y cargos) y lista de abonos con fecha, descripción, sucursal
        y monto.
    """
    import os as _os
    from datetime import datetime as _dt

    if not period:
        period = f"{_dt.now().year:04d}-{_dt.now().month:02d}"

    try:
        year_s, month_s = period.split("-")
        year, month = int(year_s), int(month_s)
        if not (1 <= month <= 12) or year < 2000:
            raise ValueError
    except (ValueError, IndexError):
        return f"❌ Período inválido: `{period}`. Usa formato YYYY-MM (ej: 2026-08)."

    # `fecha` se guarda como string 'dd-mm-yyyy' (formato de la cartola BCI).
    month_token = f"-{month:02d}-{year:04d}"
    fecha_filter: dict = {"fecha": {"$regex": f".*{month_token}"}}

    uri = _os.getenv("MONGODB_URI") or _os.getenv("MONGODB_URI_LOCAL")
    if not uri:
        return "❌ Falta MONGODB_URI (o MONGODB_URI_LOCAL) en el entorno."

    try:
        from pymongo import MongoClient
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        col = client["bci"]["transacciones"]
    except Exception as e:
        return f"❌ No se pudo conectar a MongoDB Atlas: {type(e).__name__}: {e}"

    # Todas las trx del mes (para stats de cargos y total abonos)
    all_trx = list(col.find(fecha_filter))
    if not all_trx:
        return (
            f"⚠️ No hay transacciones en `bci.transacciones` para {period}.\n"
            f"¿Corriste `sync_bci_trx` para ese mes?"
        )

    abonos = [t for t in all_trx if _to_float(t.get("abono")) > 0]
    cargos = [t for t in all_trx if _to_float(t.get("cargo")) > 0]
    total_ingresos = sum(_to_float(t["abono"]) for t in abonos)
    total_cargos = sum(_to_float(t["cargo"]) for t in cargos)

    lines = [
        f"## Ingresos BCI {period} — desde Atlas (`bci.transacciones`)",
        f"**Total ingresos**: ${total_ingresos:,.0f}  |  "
        f"**Abonos**: {len(abonos)}  |  "
        f"**Cargos**: {len(cargos)} (${total_cargos:,.0f})  |  "
        f"**Movimientos totales**: {len(all_trx)}\n",
    ]

    # Ordenar abonos por fecha desc (string dd-mm-yyyy ordena OK si mismo año)
    abonos_sorted = sorted(
        abonos,
        key=lambda t: t.get("fecha", ""),
        reverse=True,
    )

    shown = abonos_sorted[:limit]
    if abonos_sorted:
        lines.append(f"### Abonos (mostrando {len(shown)}/{len(abonos_sorted)})\n")
        for t in shown:
            fecha = t.get("fecha", "?")
            desc = t.get("descripcion", "")
            suc = t.get("sucursal", "")
            monto = _to_float(t["abono"])
            suc_str = f" ({suc})" if suc else ""
            lines.append(f"- **{fecha}**  {desc}{suc_str}  → ${monto:,.0f}")
        if len(abonos_sorted) > limit:
            lines.append(
                f"\n_…{len(abonos_sorted) - limit} abonos más no mostrados. "
                f"Subí `limit` si querés ver todos._"
            )

    return "\n".join(lines)


def _to_float(v) -> float:
    """Convierte el campo `abono`/`cargo` a float. BCI lo guarda como string."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


@mcp.tool()
def get_bci_api_health() -> str:
    """
    Verifica que la API de aia-jobs (puerto 8080) responde.

    Returns:
        Estado del endpoint /openapi.json y versión reportada.
    """
    spec = _jobs_get("/openapi.json")
    if err := spec.get("error"):
        return f"❌ API no responde en `{AIA_JOBS_API_URL}`: {err}"
    info = spec.get("info", {})
    return (
        f"✅ API OK en `{AIA_JOBS_API_URL}`\n"
        f"**Servicio**: {info.get('title', '?')}\n"
        f"**Versión**: {info.get('version', '?')}\n"
        f"**Endpoints**: {len(spec.get('paths', {}))}"
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")