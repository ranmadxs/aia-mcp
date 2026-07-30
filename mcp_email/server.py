"""Servidor MCP Email — gestión de correo Yahoo vía IMAP + MongoDB Atlas."""

import base64
import email as email_lib
import imaplib
import os
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


def _safe_logout(mail):
    try:
        if mail:
            mail.logout()
    except Exception:
        pass


def _fetch_one(mail, msg_id, retries: int = 3):
    """Fetch de un mensaje con reintentos ante error de socket/SSL.
    Devuelve los bytes RFC822 o None. Ante error persistente, reconecta."""
    last_err = None
    for attempt in range(retries):
        try:
            _, data = mail.fetch(msg_id, "(RFC822)")
            if data and data[0] is not None:
                return data[0][1]
        except Exception as e:  # SSL BAD_LENGTH, socket error, etc.
            last_err = e
            try:
                mail.logout()
            except Exception:
                pass
            try:
                mail = _imap_connect()
                mail.select("INBOX")
            except Exception:
                pass
    return None, last_err


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
# Todas las variantes de sync usan este mismo motor: descarga de Yahoo por
# IMAP, guarda en Mongo con upsert por message_id (sin duplicar) y el
# campo "kind" indica el tipo de email. El progreso se persiste en Mongo
# para consultarlo sin bloquear (get_email_sync_status).

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


def _classify_and_save(col, doc: dict) -> str:
    """Etiqueta y guarda un correo en Mongo (upsert por message_id). Devuelve el kind."""
    mid = doc.get("message_id")
    if not mid:
        col.insert_one(doc)
        return doc.get("kind", "email")
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

        inserted = duplicates = errors = 0
        completed = 0
        for msg_id in msg_ids:
            _update_sync_state(completed=completed)
            try:
                raw = _fetch_one(mail, msg_id)
                if raw is None:
                    errors += 1
                else:
                    raw_msg = email_lib.message_from_bytes(raw)
                    doc = _parse_email(raw_msg)
                    mid = doc.get("message_id")
                    if mid and col.find_one({"message_id": mid}):
                        duplicates += 1
                    else:
                        kind = _classify_and_save(col, doc)
                        inserted += 1
            except Exception:
                errors += 1
            completed += 1
            _update_sync_state(completed=completed)
        _safe_logout(mail)
        _update_sync_state(
            running=False, scope=None,
            finished_at=datetime.now(timezone.utc).isoformat(),
            last_summary=(f"## Sync {mode} ({scope})\n"
                          f"- Revisados: {total}\n- Guardados: {inserted}\n"
                          f"- Duplicados: {duplicates}\n"
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
    Solo guarda los nuevos (dedup por Message-ID). Consulta con
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
async def sync_emails_since(since_date: str, before_date: str = "") -> str:
    """
    Sincroniza a MongoDB todos los correos del INBOX recibidos desde una fecha
    (y opcionalmente hasta otra), usando IMAP SEARCH SINCE/BEFORE. Cubre rangos
    amplios (ej. dic 2025 a hoy) sin importar la antigüedad. Solo guarda los
    nuevos (dedup por Message-ID). Corre en background; consulta con
    get_email_sync_status().

    Args:
        since_date: Fecha mínima ISO (ej: "2025-12-01"). Inclusive.
        before_date: Fecha máxima ISO opcional (ej: "2026-07-15"). Exclusive.

    Returns:
        Confirmación de que el sync en background inició.
    """
    import anyio, asyncio
    try:
        since = datetime.fromisoformat(since_date)
    except ValueError:
        return f"❌ since_date inválido: '{since_date}'. Usa ISO (ej: '2025-12-01')."
    if before_date:
        try:
            before = datetime.fromisoformat(before_date)
        except ValueError:
            return f"❌ before_date inválido: '{before_date}'. Usa ISO (ej: '2026-07-15')."
    else:
        before = date.today()
    if _read_sync_state().get("running"):
        return "⏳ Ya hay un sync en curso. Usa get_email_sync_status()."
    since_s = since.strftime("%d-%b-%Y")
    before_s = before.strftime("%d-%b-%Y")
    criteria = f'SINCE {since_s} BEFORE {before_s}'
    scope = f"{since_date}..{before_date or 'hoy'}"
    async def _launch():
        await anyio.to_thread.run_sync(_do_sync, criteria, "since", scope, 0)
    asyncio.create_task(_launch())
    return (f"🚀 Sync en background desde {since_date} "
             f"({before_date or 'hasta hoy'}) iniciado. Usa get_email_sync_status().")


@mcp.tool()
async def sync_emails_from(from_addr: str, limit: int = 500) -> str:
    """
    Sincroniza a MongoDB todos los correos de UN remitente en Yahoo, sin importar
    la antigüedad (IMAP SEARCH BY FROM). Solo guarda los nuevos (dedup por
    Message-ID). Corre en background; get_email_sync_status().

    Args:
        from_addr: Remitente a buscar (ej: "notificaciones@banco.com").
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
def get_email_sync_status() -> str:
    """
    Estado del sync en background. Lectura instantánea desde Mongo,
    sin tocar Yahoo. Muestra modo, progreso y último error.

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


if __name__ == "__main__":
    mcp.run(transport="stdio")
