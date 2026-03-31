"""Servidor MCP Email — gestión de correo Yahoo vía IMAP + MongoDB Atlas."""

import email as email_lib
import imaplib
import os
import tomllib
from datetime import datetime, timezone
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
    """Extrae campos relevantes de un mensaje IMAP."""
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
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                decoded = payload.decode("utf-8", errors="replace")
                if ctype == "text/plain" and not body_text:
                    body_text = decoded
                elif ctype == "text/html" and not body_html:
                    body_html = decoded
            except Exception:
                pass
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
        "fetched_at": datetime.utcnow().isoformat(),
    }
    if fecha_remitente:
        doc["fecha_remitente"] = fecha_remitente
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


@mcp.tool()
def sync_emails(limit: int = 100) -> str:
    """
    Sincroniza los últimos N correos de Yahoo a MongoDB.
    Solo guarda emails nuevos (detecta duplicados por Message-ID).

    Args:
        limit: Cantidad máxima de correos a sincronizar (default 100, max 1500).

    Returns:
        Resumen de la sincronización: nuevos guardados, duplicados y errores.
    """
    limit = min(max(1, limit), 1500)
    col = _get_collection()
    if col is None:
        return "❌ MongoDB no disponible. Verifica MONGODB_URI en .env"

    try:
        mail = _imap_connect()
    except ValueError as e:
        return f"❌ {e}"
    except imaplib.IMAP4.error as e:
        return f"❌ Error IMAP: {e}\n\nUsa App Password de Yahoo: https://login.yahoo.com/account/security"

    try:
        mail.select("INBOX")
        _, messages = mail.search(None, "ALL")
        msg_ids = messages[0].split()[-limit:]
        total = len(msg_ids)

        inserted = duplicates = errors = 0
        for msg_id in msg_ids:
            try:
                _, data = mail.fetch(msg_id, "(RFC822)")
                if not data or data[0] is None:
                    errors += 1
                    continue
                raw_msg = email_lib.message_from_bytes(data[0][1])
                doc = _parse_email(raw_msg)
                mid = doc.get("message_id")
                if mid and col.find_one({"message_id": mid}):
                    duplicates += 1
                else:
                    col.insert_one(doc)
                    inserted += 1
            except Exception:
                errors += 1

        mail.logout()
    except Exception as e:
        try:
            mail.logout()
        except Exception:
            pass
        return f"❌ Error durante sincronización: {e}"

    return (
        f"## Sincronización completada\n\n"
        f"- **Revisados**: {total}\n"
        f"- **Guardados**: {inserted} nuevos\n"
        f"- **Duplicados**: {duplicates} (omitidos)\n"
        f"- **Errores**: {errors}\n"
    )


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
