"""Banco BCI - orquestacion del pipeline BCI via API de aia-jobs."""

import os

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
AIA_JOBS_TIMEOUT = float(os.getenv("AIA_JOBS_TIMEOUT", "600"))

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
    Ejecuta el pipeline completo de BCI: descarga emails de Yahoo, transforma los
    PDFs de cartolas y sincroniza los movimientos a bci.transacciones (Atlas).
    Es una capa fina sobre la API HTTP de aia-jobs (:8080).

    Las 3 fases se ejecutan en orden y son idempotentes:
      1. /sync-bci-emails    Yahoo IMAP → email.emails
      2. /sync-historical-bci email.emails (PDFs) → bci.cartolas
      3. /sync-trx            bci.cartolas → bci.transacciones (Atlas)

    Args:
        year:               Año (ej: 2026). 0 = año actual.
        month:              Mes (1-12). 0 = mes actual.
        batch_size:         Máx. movimientos NUEVOS a subir a Atlas (fase 3).
        skip_email_download:Si True, salta fase 1 (asume emails ya descargados).
        skip_transform:     Si True, salta fase 2 (asume cartolas ya transformadas).
        sender:             Remitente BCI en Yahoo (default: bcimail@bci.cl).

    Returns:
        Resumen con los resultados de cada fase ejecutada.
    """
    import asyncio

    if not year:
        year = date.today().year
    if not month:
        month = date.today().month

    lines = [f"## Sync BCI TRX {year}-{month:02d}\n"]

    # Fase 1: descarga de emails (si no se skipea)
    if not skip_email_download:
        lines.append(f"### Fase 1/3 - descarga de emails de `{ sender}` ({year}-{month:02d})…")
        r1 = await asyncio.to_thread(
            _jobs_post,
            "/api/jobs/sync-bci-emails",
            {"sender": sender, "year": year, "month": month},
        )
        if err := r1.get("error"):
            lines.append(f"❌ Error: {err}")
        else:
            lines.append(
                f"✅ Descargados: {r1.get('downloaded', 0)} · "
                f"Ya existían: {r1.get('already_existed', 0)} · "
                f"Total buscados: {r1.get('total_searched', 0)}"
            )
    else:
        lines.append("### Fase 1/3 - (omitida) (`skip_email_download=True`)")

    # Fase 2: transformación de PDFs → cartolas
    if not skip_transform:
        lines.append("\n### Fase 2/3 - transformacion de PDFs → bci.cartolas…")
        r2 = await asyncio.to_thread(
            _jobs_post,
            "/api/jobs/sync-historical-bci",
            {"months_back": 0},
        )
        if err := r2.get("error"):
            lines.append(f"❌ Error: {err}")
        else:
            lines.append(
                f"✅ Transformadas: {r2.get('transformed', 0)} · "
                f"Skipped: {r2.get('skipped', 0)} · "
                f"Errores: {r2.get('errors', 0)}"
            )
    else:
        lines.append("\n### Fase 2/3 - (omitida) (`skip_transform=True`)")

    # Fase 3: sync de movimientos a Atlas
    lines.append(f"\n### Fase 3/3 - sync movimientos → bci.transacciones (batch={batch_size})…")
    r3 = await asyncio.to_thread(
        _jobs_post,
        "/api/jobs/sync-trx",
        {"batch_size": batch_size},
    )
    if err := r3.get("error"):
        lines.append(f"❌ Error: {err}")
    else:
        lines.append(
            f"✅ Sincronizadas: {r3.get('synced', 0)} · "
            f"Duplicadas (skipped): {r3.get('skipped_duplicate', 0)} · "
            f"Errores: {r3.get('errors', 0)} · "
            f"Total nuevos: {r3.get('total_new', 0)}"
        )

    lines.append(f"\nAPI: `{AIA_JOBS_API_URL}`")
    return "\n".join(lines)


@mcp.tool()
def get_bci_job_status() -> str:
    """
    Estado del último job BCI en aia-jobs (instantáneo desde Mongo, sin tocar Yahoo).

    Returns:
        Estado actual: running, current_job, last_run, last_result, progress
        y total de cartolas en bci.cartolas.
    """
    s = _jobs_get("/api/jobs/status")
    if err := s.get("error"):
        return f"❌ Error consultando aia-jobs: {err}"

    lines = ["## Estado jobs BCI (aia-jobs)\n"]
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
    return "\n".join(lines)


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