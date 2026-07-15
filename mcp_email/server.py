"""Servidor MCP Email — gestión de correo Yahoo vía IMAP + MongoDB Atlas."""

import base64
import email as email_lib
import imaplib
import os
import re
import tomllib
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Cargar .env desde la raíz del proyecto aia-mcp o el cwd
for _env in [Path(__file__).resolve().parent.parent / ".env", Path.cwd() / ".env"]:
    if _env.exists():
        load_dotenv(str(_env), override=False)
        break
load_dotenv()

mcp = FastMCP(
    "email",
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("FASTMCP_PORT", os.environ.get("FASTMCP_EMAIL_PORT", "8008"))),
)

_pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
if _pyproject.exists():
    with open(_pyproject, "rb") as f:
        mcp._mcp_server.version = tomllib.load(f)["tool"]["poetry"]["version"]

# ── Config IMAP ──────────────────────────────────────────────────────────────
YAHOO_EMAIL       = os.getenv("YAHOO_EMAIL", "")
YAHOO_APP_PASSWORD = os.getenv("YAHOO_APP_PASSWORD", "")
IMAP_SERVER       = "imap.mail.yahoo.com"
IMAP_PORT         = 993
MONGODB_URI       = os.getenv("MONGODB_URI", "")
DB_NAME           = "email"
COLLECTION        = "emails"
BCI_SENDER         = "bcimail@bci.cl"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _decode_str(s) -> str:
    if s is None:
        return ""
    if isinstance(s, bytes):
        return s.decode("utf-8", errors="replace")
    return str(s)


def _parse_email(msg) -> dict:
    """Extrae campos relevantes de un mensaje IMAP, incluidos adjuntos (base64)."""
    subject = ""
    subj_raw = msg.get("Subject")
    if subj_raw:
        subject = " ".join(_decode_str(p) for p, _ in decode_header(subj_raw))

    date_str = msg.get("Date", "")
    fecha_remitente = None
    if date_str:
        try:
            dt = parsedate_to_datetime(date_str)
            fecha_remitente = dt.astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            pass

    body_text = body_html = ""
    attachments = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            filename = part.get_filename()
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                payload = None
            # Adjunto (PDF, etc.) -> guardar en base64
            if filename and ctype not in ("text/plain", "text/html") and payload:
                attachments.append({
                    "filename": _decode_str(filename),
                    "content_type": ctype,
                    "size": len(payload),
                    "data_b64": base64.b64encode(payload).decode("ascii"),
                })
                continue
            if payload is None:
                continue
            decoded = payload.decode("utf-8", errors="replace")
            if ctype == "text/plain" and not body_text:
                body_text = decoded
            elif ctype == "text/html" and not body_html:
                body_html = decoded
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                body_text = payload.decode("utf-8", errors="replace")
        except Exception:
            body_text = str(msg.get_payload())

    doc = {
        "message_id": msg.get("Message-ID", ""),
        "subject": subject,
        "from_addr": msg.get("From", ""),
        "to_addr": msg.get("To", ""),
        "date_str": date_str,
        "body_text": body_text[:50000],
        "body_html": body_html[:50000],
        "attachments": attachments,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "kind": "email",
    }
    if fecha_remitente:
        doc["fecha_remitente"] = fecha_remitente
        doc["period"] = fecha_remitente.strftime("%Y-%m")
    return doc


def _get_collection():
    if not MONGODB_URI:
        return None
    try:
        from pymongo import MongoClient
        client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        return client[DB_NAME][COLLECTION]
    except Exception:
        return None


def _imap_connect():
    if not YAHOO_EMAIL or not YAHOO_APP_PASSWORD:
        raise ValueError("YAHOO_EMAIL y YAHOO_APP_PASSWORD deben estar en .env")
    mail = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
    mail.login(YAHOO_EMAIL, YAHOO_APP_PASSWORD)
    return mail


# ── MCP Tools ────────────────────────────────────────────────────────────────

@mcp.tool()
def get_email_status() -> str:
    """
    Verifica el estado de la conexión IMAP y MongoDB.

    Returns:
        Estado de conectividad con Yahoo Mail y MongoDB.
    """
    lines = ["## Estado Email MCP\n"]

    # IMAP
    try:
        mail = _imap_connect()
        mail.select("INBOX")
        _, msgs = mail.search(None, "ALL")
        total = len(msgs[0].split()) if msgs[0] else 0
        mail.logout()
        lines.append(f"✅ **Yahoo IMAP**: conectado ({total} mensajes en INBOX)")
    except Exception as e:
        lines.append(f"❌ **Yahoo IMAP**: {e}")

    # MongoDB
    col = _get_collection()
    if col is not None:
        try:
            count = col.count_documents({})
            lines.append(f"✅ **MongoDB**: conectado ({count} emails guardados en {DB_NAME}.{COLLECTION})")
        except Exception as e:
            lines.append(f"❌ **MongoDB**: {e}")
    else:
        lines.append("❌ **MongoDB**: MONGODB_URI no configurado")

    lines.append(f"\n**Cuenta**: {YAHOO_EMAIL or '(no configurado)'}")
    return "\n".join(lines)


# ── Motor de sincronización GENÉRICO (una sola implementación) ───────────────
# Todas las variantes de sync (emails, cartolas BCI, por remitente) usan este
# mismo motor: descarga de Yahoo por IMAP, guarda en Mongo con upsert por
# message_id (sin duplicar) y marca como cartola BCI cuando el remitente es
# BCI y el asunto dice "Cartola". El progreso se persiste en Mongo para
# consultarlo sin bloquear (get_email_sync_status).

_SYNC_STATE_ID = "email_sync"  # doc único en email.sync_state


def _sync_state_col():
    col = _get_collection()
    if col is None:
        return None
    return col.database["sync_state"]


def _update_sync_state(**fields):
    """Persiste el estado del sync en Mongo (doc _id='email_sync')."""
    col = _sync_state_col()
    if col is None:
        return
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        col.update_one({"_id": _SYNC_STATE_ID}, {"$set": fields}, upsert=True)
    except Exception:
        pass


def _read_sync_state() -> dict:
    col = _sync_state_col()
    if col is None:
        return {"running": False, "mode": None, "scope": None, "completed": 0,
                "total": 0, "last_error": None, "started_at": None,
                "finished_at": None, "note": "MongoDB no disponible"}
    try:
        doc = col.find_one({"_id": _SYNC_STATE_ID})
    except Exception:
        doc = None
    if not doc:
        return {"running": False, "mode": None, "scope": None, "completed": 0,
                "total": 0, "last_error": None, "started_at": None,
                "finished_at": None}
    doc.pop("_id", None)
    return doc


def _is_bci_cartola(doc: dict) -> bool:
    """Un correo es cartola BCI si viene de bcimail@bci.cl y el asunto dice Cartola."""
    from_addr = (doc.get("from_addr") or "").lower()
    subj = (doc.get("subject") or "").upper()
    return BCI_SENDER in from_addr and "CARTOLA" in subj


def _classify_and_save(col, doc: dict) -> str:
    """Etiqueta y guarda un correo en Mongo (upsert por message_id). Devuelve el kind."""
    mid = doc.get("message_id")
    if not mid:
        col.insert_one(doc)
        return doc.get("kind", "email")
    atts = doc.get("attachments", [])
    if _is_bci_cartola(doc) and atts:
        try:
            import base64 as _b64
            pdf = _b64.b64decode(atts[0]["data_b64"])
            derived = _extract_period_from_pdf(pdf, BCI_PDF_PASSWORD)
            if derived:
                doc["period"] = derived
        except Exception:
            pass
        doc["kind"] = "bci_cartola"
    else:
        doc.setdefault("kind", "email")
    col.update_one({"message_id": mid}, {"$set": doc}, upsert=True)
    return doc["kind"]


def _do_sync(search_criteria: str, mode: str, scope: str, limit: int) -> None:
    """Motor único. `search_criteria` es el string IMAP SEARCH a usar."""
    col = _get_collection()
    if col is None:
        return
    _update_sync_state(
        running=True, mode=mode, scope=scope, completed=0, total=0,
        started_at=datetime.now(timezone.utc).isoformat(),
        finished_at=None, last_error=None,
    )
    try:
        mail = _imap_connect()
        mail.select("INBOX")
        _, msgs = mail.search(None, search_criteria)
        msg_ids = msgs[0].split()
        if limit and limit > 0:
            msg_ids = msg_ids[-limit:]
        total = len(msg_ids)
        _update_sync_state(total=total)

        inserted = duplicates = errors = cartolas = 0
        completed = 0
        for msg_id in msg_ids:
            _update_sync_state(completed=completed)
            try:
                _, data = mail.fetch(msg_id, "(RFC822)")
                if not data or data[0] is None:
                    errors += 1
                else:
                    raw_msg = email_lib.message_from_bytes(data[0][1])
                    doc = _parse_email(raw_msg)
                    mid = doc.get("message_id")
                    if mid and col.find_one({"message_id": mid}):
                        duplicates += 1
                    else:
                        kind = _classify_and_save(col, doc)
                        if kind == "bci_cartola":
                            cartolas += 1
                        inserted += 1
            except Exception:
                errors += 1
            completed += 1
            _update_sync_state(completed=completed)
        mail.logout()
        _update_sync_state(
            running=False, scope=None,
            finished_at=datetime.now(timezone.utc).isoformat(),
            last_summary=(f"## Sync {mode} ({scope})\n"
                          f"- Revisados: {total}\n- Guardados: {inserted} "
                          f"(cartolas BCI: {cartolas})\n- Duplicados: {duplicates}\n"
                          f"- Errores: {errors}"),
        )
    except Exception as e:
        _update_sync_state(
            running=False, finished_at=datetime.now(timezone.utc).isoformat(),
            last_error=str(e),
        )


@mcp.tool()
async def sync_emails(limit: int = 100) -> str:
    """
    Sincroniza los últimos N correos del INBOX de Yahoo a MongoDB (background).
    Solo guarda los nuevos (dedup por Message-ID). Los correos de BCI con asunto
    "Cartola" se marcan como cartola BCI automáticamente. Consulta con
    get_email_sync_status().

    Args:
        limit: Cantidad máxima a sincronizar (default 100, max 1500).

    Returns:
        Confirmación de que el sync en background inició.
    """
    import anyio, asyncio
    limit = min(max(1, limit), 1500)
    if _read_sync_state().get("running"):
        return "⏳ Ya hay un sync en curso. Usa get_email_sync_status()."
    async def _launch():
        await anyio.to_thread.run_sync(_do_sync, "ALL", "inbox", limit)
    asyncio.create_task(_launch())
    return f"🚀 Sync de {limit} correos del INBOX iniciado. Usa get_email_sync_status()."


@mcp.tool()
async def sync_emails_from(from_addr: str, limit: int = 500) -> str:
    """
    Sincroniza a MongoDB todos los correos de UN remitente en Yahoo, sin importar
    la antigüedad (IMAP SEARCH BY FROM). Solo guarda los nuevos (dedup por
    Message-ID). Los correos de BCI con asunto "Cartola" se marcan como cartola
    BCI (period derivado del PDF). Corre en background; get_email_sync_status().

    Args:
        from_addr: Remitente a buscar (ej: "bcimail@bci.cl").
        limit: Máximo a fetch'eer (los más recientes). 0 = sin cap.

    Returns:
        Confirmación de que el sync en background inició.
    """
    import anyio, asyncio
    if not from_addr:
        return "❌ Debes indicar `from_addr` (remitente a sincronizar)."
    if _read_sync_state().get("running"):
        return "⏳ Ya hay un sync en curso. Usa get_email_sync_status()."
    criteria = f'FROM "{from_addr}"'
    async def _launch():
        await anyio.to_thread.run_sync(_do_sync, criteria, "from", from_addr, limit)
    asyncio.create_task(_launch())
    return f"🚀 Sync en background de '{from_addr}' iniciado. Usa get_email_sync_status()."


@mcp.tool()
async def sync_bci_cartolas(months_back: int = 6, force_refresh: bool = False) -> str:
    """
    Sincroniza las cartolas BCI (bcimail@bci.cl, asunto "Cartola") de los últimos
    N meses a MongoDB, EN SEGUNDO PLANO. Usa el mismo motor genérico que los otros
    sync: descarga de Yahoo, upsert por message_id (sin duplicar) y marca como
    cartola BCI con period derivado del PDF. Consulta con get_email_sync_status().

    Args:
        months_back: Meses hacia atrás a sincronizar (default 6).
        force_refresh: Si True, re-descarga aunque existan en cache.

    Returns:
        Confirmación de que el sync en background inició.
    """
    import anyio, asyncio
    if _read_sync_state().get("running"):
        return "⏳ Ya hay un sync en curso. Usa get_email_sync_status()."
    today = date.today()
    periods = []
    for i in range(max(1, months_back)):
        y, m = today.year, today.month - i
        while m <= 0:
            m += 12
            y -= 1
        periods.append(f"{y:04d}-{m:02d}")
    # Busca por remitente BCI + asunto Cartola en el rango de meses.
    y0, m0 = (int(x) for x in periods[-1].split("-"))
    y1, m1 = (int(x) for x in periods[0].split("-"))
    ny0, nm0 = (y0, m0)
    ny1, nm1 = (y1 + 1, 1) if m1 == 12 else (y1, m1 + 1)
    since = date(ny0, nm0, 1).strftime("%d-%b-%Y")
    before = date(ny1, nm1, 1).strftime("%d-%b-%Y")
    criteria = f'FROM "{BCI_SENDER}" SUBJECT "Cartola" SINCE {since} BEFORE {before}'
    if force_refresh:
        # fuerza re-descarga: borra las cartolas del rango antes de sync
        col = _get_collection()
        if col is not None:
            col.delete_many({"kind": "bci_cartola", "period": {"$in": periods}})
    async def _launch():
        await anyio.to_thread.run_sync(_do_sync, criteria, "bci", f"{periods[0]}..{periods[-1]}", 0)
    asyncio.create_task(_launch())
    return (f"🚀 Sync BCI en background para {max(1, months_back)} meses "
             f"(force_refresh={force_refresh}). Usa get_email_sync_status().")


@mcp.tool()
def get_email_sync_status() -> str:
    """
    Estado del sync (cualquiera: inbox, por remitente o BCI) en background.
    Lectura instantánea desde Mongo, sin tocar Yahoo. Muestra modo, progreso,
    cartolas guardadas y último error.

    Returns:
        Estado actual de la sincronización.
    """
    s = _read_sync_state()
    lines = ["## Estado sync de emails\n"]
    if s.get("running"):
        lines.append("**Estado**: 🔄 EN CURSO")
        lines.append(f"**Modo**: {s.get('mode')}  |  **Alcance**: {s.get('scope')}")
        lines.append(f"**Progreso**: {s.get('completed')}/{s.get('total')}")
        lines.append(f"**Iniciado**: {s.get('started_at')}")
    else:
        lines.append("**Estado**: ✅ terminado (o inactivo)")
        if s.get("finished_at"):
            lines.append(f"**Terminó**: {s.get('finished_at')}")
        lines.append(f"**Último progreso**: {s.get('completed')}/{s.get('total')}")
    if s.get("last_error"):
        lines.append(f"**Último error**: {s.get('last_error')}")
    if s.get("last_summary"):
        lines.append("\n" + s["last_summary"])
    return "\n".join(lines)


@mcp.tool()
def search_emails(
    query: str = "",
    from_addr: str = "",
    subject: str = "",
    since_date: str = "",
    limit: int = 20,
) -> str:
    """
    Busca emails en MongoDB por texto libre, remitente, asunto o fecha.

    Args:
        query:      Texto a buscar en asunto o cuerpo.
        from_addr:  Filtrar por remitente (parcial).
        subject:    Filtrar por asunto (parcial).
        since_date: Fecha mínima ISO (ej: "2025-01-01"). Filtra por fecha_remitente.
        limit:      Máximo de resultados (default 20).

    Returns:
        Lista de emails encontrados con asunto, remitente y fecha.
    """
    col = _get_collection()
    if col is None:
        return "❌ MongoDB no disponible."

    filt: dict = {}
    if query:
        filt["$or"] = [
            {"subject":   {"$regex": query,     "$options": "i"}},
            {"body_text": {"$regex": query,     "$options": "i"}},
        ]
    if from_addr:
        filt["from_addr"] = {"$regex": from_addr, "$options": "i"}
    if subject:
        filt["subject"] = {"$regex": subject, "$options": "i"}
    if since_date:
        try:
            dt = datetime.fromisoformat(since_date)
            filt["fecha_remitente"] = {"$gte": dt}
        except ValueError:
            return f"❌ Formato de fecha inválido: '{since_date}'. Usa ISO (ej: '2025-01-01')"

    try:
        docs = list(col.find(filt, {"body_html": 0}).sort("fecha_remitente", -1).limit(limit))
    except Exception as e:
        return f"❌ Error en búsqueda: {e}"

    if not docs:
        return "No se encontraron emails con esos criterios."

    lines = [f"## {len(docs)} emails encontrados\n"]
    for d in docs:
        fecha = d.get("fecha_remitente") or d.get("date_str", "sin fecha")
        if isinstance(fecha, datetime):
            fecha = fecha.strftime("%Y-%m-%d %H:%M")
        lines.append(f"**{d.get('subject','(sin asunto)')}**")
        lines.append(f"  De: {d.get('from_addr','')}  |  {fecha}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def get_email_body(message_id: str) -> str:
    """
    Obtiene el cuerpo completo de un email por su Message-ID.

    Args:
        message_id: El Message-ID del email (obtenido con search_emails).

    Returns:
        Asunto, remitente, fecha y cuerpo del mensaje.
    """
    col = _get_collection()
    if col is None:
        return "❌ MongoDB no disponible."

    doc = col.find_one({"message_id": message_id})
    if not doc:
        return f"❌ No se encontró el email con Message-ID: {message_id}"

    fecha = doc.get("fecha_remitente") or doc.get("date_str", "")
    if isinstance(fecha, datetime):
        fecha = fecha.strftime("%Y-%m-%d %H:%M UTC")

    body = doc.get("body_text") or doc.get("body_html", "(sin cuerpo)")
    return (
        f"**Asunto**: {doc.get('subject','')}\n"
        f"**De**: {doc.get('from_addr','')}\n"
        f"**Para**: {doc.get('to_addr','')}\n"
        f"**Fecha**: {fecha}\n\n"
        f"---\n\n{body[:8000]}"
    )


@mcp.tool()
def get_recent_emails(limit: int = 10) -> str:
    """
    Lista los emails más recientes guardados en MongoDB.

    Args:
        limit: Cantidad de emails a mostrar (default 10).

    Returns:
        Lista de los últimos N emails con asunto, remitente y fecha.
    """
    col = _get_collection()
    if col is None:
        return "❌ MongoDB no disponible."

    try:
        docs = list(col.find({}, {"body_html": 0, "body_text": 0}).sort("fecha_remitente", -1).limit(limit))
    except Exception as e:
        return f"❌ Error: {e}"

    if not docs:
        return "No hay emails guardados. Usa sync_emails() para sincronizar."

    lines = [f"## Últimos {len(docs)} emails\n"]
    for d in docs:
        fecha = d.get("fecha_remitente") or d.get("date_str", "")
        if isinstance(fecha, datetime):
            fecha = fecha.strftime("%Y-%m-%d %H:%M")
        lines.append(f"**{d.get('subject','(sin asunto)')}**")
        lines.append(f"  De: {d.get('from_addr','')}  |  {fecha}")
        lines.append(f"  ID: `{d.get('message_id','')}`")
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def get_email_stats() -> str:
    """
    Muestra estadísticas del buzón: total de emails, remitentes más frecuentes,
    distribución por mes.

    Returns:
        Estadísticas del buzón guardado en MongoDB.
    """
    col = _get_collection()
    if col is None:
        return "❌ MongoDB no disponible."

    try:
        total = col.count_documents({})
        if total == 0:
            return "No hay emails. Usa sync_emails() primero."

        # Top remitentes
        pipeline_from = [
            {"$group": {"_id": "$from_addr", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]
        top_senders = list(col.aggregate(pipeline_from))

        # Por mes (últimos 6)
        pipeline_month = [
            {"$match": {"fecha_remitente": {"$exists": True}}},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m", "date": "$fecha_remitente"}},
                "count": {"$sum": 1},
            }},
            {"$sort": {"_id": -1}},
            {"$limit": 6},
        ]
        by_month = list(col.aggregate(pipeline_month))

    except Exception as e:
        return f"❌ Error: {e}"

    lines = [f"## Estadísticas del buzón\n", f"**Total emails**: {total}\n"]

    lines.append("### Top remitentes\n")
    for s in top_senders:
        lines.append(f"- {s['_id']} — {s['count']} emails")

    lines.append("\n### Por mes\n")
    for m in by_month:
        lines.append(f"- {m['_id']}: {m['count']} emails")

    return "\n".join(lines)


# ── Cartolas BCI (cache-first: MongoDB -> Yahoo) ─────────────────────────────

def _resolve_period(period: str) -> str:
    return period or datetime.now(timezone.utc).strftime("%Y-%m")


def _imap_search_bci(period: str):
    """Busca UIDs de correos de BCI en Yahoo para el período.

    BCI envía la cartola del mes X DENTRO del mes X (observado: 2026-01 recibida
    2026-01-21, 2026-02 el 2026-02-23, 2026-07 el 2026-07-05). Buscamos por la
    FECHA DE RECEPCIÓN del correo usando TODO el mes X, filtrando por asunto
    "Cuenta Corriente" para descartar las "Cartola Trimestral Consumo" del mismo
    remitente.
    """
    y, m = (int(x) for x in period.split("-"))
    since = date(y, m, 1)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    before = date(ny, nm, 1)
    fmt = "%d-%b-%Y"
    mail = _imap_connect()
    mail.select("INBOX")
    _, msgs = mail.search(
        None,
        f'FROM "{BCI_SENDER}" SUBJECT "Cuenta Corriente" '
        f'SINCE {since.strftime(fmt)} BEFORE {before.strftime(fmt)}',
    )
    ids = msgs[0].split()
    mail.logout()
    return ids


_PERIOD_RE = re.compile(r"al\s+(\d{2})-(\d{2})-(\d{4})")


def _extract_period_from_pdf(pdf_bytes: bytes, password: str) -> str | None:
    """Extrae el período real de la cartola desde el texto del PDF.

    BCI imprime 'PERIODO : 22-06-2026 al 03-07-2026'. Usamos la fecha final
    (el mes de cierre) como clave de cache 'YYYY-MM'.
    """
    with pdfplumber.open(__import__("io").BytesIO(pdf_bytes), password=password or "") as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            m = _PERIOD_RE.search(text)
            if m:
                d, mo, y = m.groups()
                return f"{y}-{mo}"
    return None


def _fetch_bci_cartola(period: str, force_refresh: bool = False):
    """Cache-first: lee de MongoDB por período; si no existe, descarga de Yahoo.
    Devuelve (doc, cache_hit). doc=None si no encontrado."""
    col = _get_collection()
    if col is not None and not force_refresh:
        doc = col.find_one(
            {"from_addr": {"$regex": BCI_SENDER, "$options": "i"}, "period": period},
            sort=[("fecha_remitente", -1)],
        )
        if doc:
            return doc, True
    try:
        ids = _imap_search_bci(period)
    except Exception as e:
        return {"error": str(e)}, False
    if not ids:
        return None, False
    mail = _imap_connect()
    mail.select("INBOX")
    _, data = mail.fetch(ids[-1], "(RFC822)")
    mail.logout()
    if not data or data[0] is None:
        return None, False
    doc = _parse_email(email_lib.message_from_bytes(data[0][1]))
    # El período de cache se deriva del CONTENIDO del PDF (fecha de cierre de la
    # cartola), no del mes solicitado ni de cuándo llegó el correo. Así la cartola
    # de febrero siempre se guarda como 2026-02 aunque BCI la envíe en abril.
    atts = doc.get("attachments", [])
    real_period = period
    if atts:
        try:
            import base64 as _b64
            pdf_bytes = _b64.b64decode(atts[0]["data_b64"])
            derived = _extract_period_from_pdf(pdf_bytes, BCI_PDF_PASSWORD)
            if derived:
                real_period = derived
        except Exception:
            pass
    doc["period"] = real_period
    doc["kind"] = "bci_cartola"
    if col is not None:
        # Upsert por message_id: force_refresh reescribe en vez de duplicar.
        col.update_one(
            {"message_id": doc.get("message_id")},
            {"$set": doc},
            upsert=True,
        )
    return doc, False


# ── Parser de movimientos de la cartola BCI (PDF cifrado) ────────────────────

import re

import pdfplumber

BCI_PDF_PASSWORD = os.getenv("BCI_PDF_PASSWORD", "")

# Palabras que en la descripción de BCI indican un abono/ingreso.
# Se excluye CREDITO/CRÉDITO (ambiguous: "PAGO CREDITO" es un cargo).
_ABONO_KEYWORDS = (
    "TRANSFER", "ABONO", "TRASPASO FONDOS", "PAGO RECIBIDO", "DEPOSITO",
    "DEPÓSITO", "RECAUDACION", "ACREDITACION", "ACREDITACIÓN", "NOTA ABONO",
    "REINTEGRO", "DEVOLUCION", "DEVOLUCIÓN",
)

_AMOUNT_RE = re.compile(r"[\d.]{2,}")


def _parse_amount(token: str) -> float:
    """Convierte '1.300.000' -> 1300000.0."""
    return float(token.replace(".", "").replace(",", "."))


def _extract_movements(pdf_bytes: bytes, password: str) -> list[dict]:
    """Extrae los movimientos de la cartola BCI desde el PDF cifrado.

    Cada línea de movimiento tiene la forma:
        FECHA  SUCURSAL  DESCRIPCION  [Nº DOC]  MONTO  SALDO
    El ingreso se detecta comparando el saldo con la fila anterior (sube => abono)
    o por palabras clave de abono en la descripción.
    """
    movements: list[dict] = []
    prev_saldo = None
    with pdfplumber.open(__import__("io").BytesIO(pdf_bytes), password=password or "") as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            lineas: dict[int, list[str]] = {}
            for w in words:
                lineas.setdefault(round(w["top"]), []).append(w["text"])
            for y in sorted(lineas):
                txt = " ".join(lineas[y])
                # Solo líneas que EMPieZAN con fecha DD-MM-YYYY (movimientos reales)
                if not re.match(r"^\d{2}-\d{2}-\d{4}", txt):
                    continue
                if "SALDO" in txt or "al " in txt:
                    continue
                # tokens numéricos (montos y saldos)
                nums = _AMOUNT_RE.findall(txt)
                if len(nums) < 2:
                    continue
                fecha = txt[:10]
                descripcion = txt[10:].split(nums[0])[0].strip()
                monto = _parse_amount(nums[-2])
                saldo = _parse_amount(nums[-1])
                # Detectar ingreso: el saldo sube respecto a la fila anterior
                # es la señal más fiable (BCI no separa cargo/abono por columna).
                is_ingreso = False
                has_abono_kw = any(k in descripcion.upper() for k in _ABONO_KEYWORDS)
                if prev_saldo is None:
                    # Primera fila: sin referencia de saldo, usar palabra clave.
                    is_ingreso = has_abono_kw
                elif saldo > prev_saldo:
                    is_ingreso = True
                # Refuerzo por palabra clave de abono (solo si el saldo no bajó).
                elif saldo >= prev_saldo and has_abono_kw:
                    is_ingreso = True
                movements.append({
                    "fecha": fecha,
                    "descripcion": descripcion.strip(),
                    "monto": monto,
                    "saldo": saldo,
                    "is_ingreso": is_ingreso,
                })
                prev_saldo = saldo
    return movements


@mcp.tool()
async def get_bci_cartola_ingresos(period: str = "", rut_password: str = "",
                             force_refresh: bool = False) -> str:
    """
    Extrae solo los INGRESOS (abonos, depósitos, transferencias recibidas) de la
    cartola cuenta corriente BCI del período. Cache-first igual que get_bci_cartola.

    El PDF de la cartola está cifrado; se abre con el RUT de la cuenta (sin dígito
    verificador) vía `rut_password` o la env var BCI_PDF_PASSWORD.

    Args:
        period: Período "YYYY-MM" (ej: "2026-07"). Vacío = mes actual.
        rut_password: Clave del PDF (RUT de la cuenta). Si vacío usa BCI_PDF_PASSWORD.
        force_refresh: Si True, re-descarga la cartola de Yahoo aunque exista en cache.

    Returns:
        Lista de ingresos (fecha, descripción, monto) y total del mes.
    """
    import anyio

    def _blocking():
        period_r = _resolve_period(period)
        doc, cache_hit = _fetch_bci_cartola(period_r, force_refresh)
        if doc is None:
            return (f"❌ No se encontró cartola BCI para {period_r} en Yahoo ni MongoDB "
                    f"(¿aún no enviada para ese mes?).")
        if isinstance(doc, dict) and doc.get("error"):
            return f"❌ Error IMAP: {doc['error']}\n\nUsa App Password de Yahoo."
        atts = doc.get("attachments", [])
        if not atts:
            return f"⚠️ La cartola {period_r} no tiene adjunto PDF para analizar."
        password = rut_password or BCI_PDF_PASSWORD
        if not password:
            return ("❌ Falta la clave del PDF. Pasa `rut_password` (RUT de la cuenta) "
                    "o define BCI_PDF_PASSWORD en el entorno.")
        try:
            import base64 as _b64
            pdf_bytes = _b64.b64decode(atts[0]["data_b64"])
            movements = _extract_movements(pdf_bytes, password)
        except Exception as e:
            return (f"❌ No se pudo leer el PDF de la cartola ({e}). "
                    f"Verifica la clave (RUT de la cuenta BCI).")
        ingresos = [m for m in movements if m["is_ingreso"]]
        total = sum(m["monto"] for m in ingresos)
        src = "📦 MongoDB (cache)" if cache_hit else "⬇️ Yahoo (nueva)"
        lines = [
            f"## Ingresos cartola BCI {period_r} — {src}",
            f"**Total ingresos**: {total:,.0f}  |  **Movimientos**: {len(movements)}  "
            f"|  **Ingresos**: {len(ingresos)}\n",
        ]
        for m in ingresos:
            lines.append(f"- **{m['fecha']}**  {m['descripcion']}: {m['monto']:,.0f}")
        return "\n".join(lines)

    return await anyio.to_thread.run_sync(_blocking)


@mcp.tool()
async def get_bci_cartola(period: str = "", force_refresh: bool = False) -> str:
    """
    Obtiene la cartola cuenta corriente BCI (bcimail@bci.cl) para un período.
    Cache-first: si ya está en MongoDB la lee de ahí; si no, la descarga de Yahoo
    y la guarda (adjunto PDF en base64). Cada mes = nueva clave de cache.

    Args:
        period: Período "YYYY-MM" (ej: "2026-07"). Vacío = mes actual.
        force_refresh: Si True, re-descarga de Yahoo aunque exista en cache.

    Returns:
        Resumen de la cartola y sus adjuntos (o error si no se encontró).
    """
    import anyio

    def _blocking():
        period_r = _resolve_period(period)
        doc, cache_hit = _fetch_bci_cartola(period_r, force_refresh)
        if doc is None:
            return (f"❌ No se encontró cartola BCI para {period_r} en Yahoo ni MongoDB "
                    f"(¿aún no enviada para ese mes?).")  # noqa: E501
        if isinstance(doc, dict) and doc.get("error"):
            return f"❌ Error IMAP: {doc['error']}\n\nUsa App Password de Yahoo."
        src = "📦 MongoDB (cache)" if cache_hit else "⬇️ Yahoo (nueva)"
        atts = doc.get("attachments", [])
        lines = [
            f"## Cartola BCI {period_r} — {src}\n",
            f"**Asunto**: {doc.get('subject', '')}",
            f"**De**: {doc.get('from_addr', '')}",
            f"**Fecha**: {doc.get('fecha_remitente', doc.get('date_str', ''))}",
        ]
        if atts:
            lines.append(f"\n**Adjuntos ({len(atts)})**:")
            for a in atts:
                lines.append(f"- `{a['filename']}` ({a['content_type']}, {a['size']} bytes, base64 en MongoDB)")
        else:
            lines.append("\n⚠️ Sin adjuntos PDF en este correo.")
        lines.append(f"\n**Message-ID**: `{doc.get('message_id', '')}`")
        return "\n".join(lines)

    return await anyio.to_thread.run_sync(_blocking)


@mcp.tool()
def list_bci_cartolas() -> str:
    """
    Lista las cartolas BCI guardadas en MongoDB, agrupadas por período.

    Returns:
        Lista de períodos en cache con asunto y fecha.
    """
    col = _get_collection()
    if col is None:
        return "❌ MongoDB no disponible."
    docs = list(col.find(
        {"kind": "bci_cartola"},
        {"attachments": 0, "body_text": 0, "body_html": 0},
    ).sort("period", -1))
    if not docs:
        return "No hay cartolas BCI en cache. Usa sync_bci_cartolas() o get_bci_cartola()."
    lines = [f"## {len(docs)} cartolas BCI en cache\n"]
    for d in docs:
        lines.append(f"- **{d.get('period')}**: {d.get('subject', '')}  |  {d.get('fecha_remitente', '')}")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
