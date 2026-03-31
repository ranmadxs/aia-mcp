"""Servidor MCP Airbnb - reservas y calendario desde MongoDB."""

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import tomllib
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

# ── Configuración ──────────────────────────────────────────────────────────────
PROPERTY_NAME = os.getenv("PROPERTY_NAME", "Propiedad")
MONGODB_URI = os.getenv("MONGODB_URI", "")
AIRBNB_DB = os.getenv("AIRBNB_DB", "airbnb-db")

# ── FastMCP ────────────────────────────────────────────────────────────────────
mcp = FastMCP(
    "airbnb",
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("FASTMCP_AIRBNB_PORT", "8005")),
)

_pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
if _pyproject.exists():
    with open(_pyproject, "rb") as f:
        mcp._mcp_server.version = tomllib.load(f)["tool"]["poetry"]["version"]

# ── MongoDB ────────────────────────────────────────────────────────────────────
_mongo_client = None
_mongo_db = None


def _get_db():
    global _mongo_client, _mongo_db
    if not MONGODB_URI:
        return None
    try:
        from pymongo import MongoClient
        if _mongo_client is None:
            _mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
            _mongo_client.admin.command("ping")
            _mongo_db = _mongo_client[AIRBNB_DB]
        return _mongo_db
    except Exception:
        _mongo_client = None
        _mongo_db = None
        return None


def _serialize(doc: dict) -> dict:
    """Convierte campos MongoDB a tipos serializables."""
    out = {}
    for k, v in doc.items():
        if k == "_id":
            out["id"] = str(v)
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# ── MCP Tools ──────────────────────────────────────────────────────────────────

@mcp.tool()
def get_proxima_reserva() -> str:
    """
    Retorna la reserva más próxima a partir de hoy desde MongoDB.
    Incluye nombre del huésped, fechas, noches, precio y notas.
    """
    db = _get_db()
    if db is None:
        return json.dumps({"error": "MongoDB no disponible"})

    hoy = datetime.now().strftime("%Y-%m-%d")
    doc = db["reservas"].find_one(
        {"event_end": {"$gt": hoy}, "estado": "reservado"},
        sort=[("event_start", 1)],
    )
    if not doc:
        return json.dumps({"mensaje": "No hay reservas próximas registradas"})
    return json.dumps(_serialize(doc), ensure_ascii=False)


@mcp.tool()
def get_reservas_airbnb(solo_futuras: bool = True) -> str:
    """
    Obtiene todas las reservas desde MongoDB.

    Args:
        solo_futuras: Si True (default), solo reservas con checkout >= hoy.
                      Si False, retorna todas.

    Returns:
        Lista de reservas con fechas, huésped, precio, notas, etc.
    """
    db = _get_db()
    if db is None:
        return json.dumps({"error": "MongoDB no disponible"})

    query = {"estado": "reservado"}
    if solo_futuras:
        hoy = datetime.now().strftime("%Y-%m-%d")
        query["event_end"] = {"$gt": hoy}

    cursor = db["reservas"].find(query, sort=[("event_start", 1)])
    reservas = [_serialize(doc) for doc in cursor]

    if not reservas:
        return json.dumps({"mensaje": "No hay reservas", "total": 0})

    return json.dumps({"total": len(reservas), "reservas": reservas}, ensure_ascii=False)


def _evento_formato_ical(doc: dict) -> dict:
    """Convierte un documento de reservas al mismo formato que obtener_eventos_formato_ical."""
    return {
        "id": str(doc.get("_id", "")),
        "start": doc.get("event_start"),
        "end": doc.get("event_end"),
        "days": doc.get("days", 1),
        "summary": doc.get("summary", ""),
        "reservation_url": doc.get("reservation_url"),
        "codigo_reserva": doc.get("codigo_reserva"),
        "source": doc.get("source", ""),
        "estado": doc.get("estado", "bloqueado"),
        "readonly": doc.get("readonly", False),
        "checkout": doc.get("checkout"),
        "hora_checkin": doc.get("hora_checkin", ""),
        "hora_checkout": doc.get("hora_checkout") or "18:00",
        "nombre_huesped": doc.get("nombre_huesped", ""),
        "adultos": doc.get("adultos", 0),
        "ninos": doc.get("ninos", 0),
        "mascotas": doc.get("mascotas", 0),
        "notas": doc.get("notas", ""),
        "precio": doc.get("precio", 0),
        "extra_concepto": doc.get("extra_concepto", ""),
        "extra_valor": doc.get("extra_valor", 0),
        "extra_pago_confirmado": doc.get("extra_pago_confirmado", False),
        "comuna": doc.get("comuna", ""),
        "pais": doc.get("pais", ""),
    }


@mcp.tool()
def get_calendario_mes_airbnb(mes: int, anio: Optional[int] = None) -> str:
    """
    Retorna todos los eventos del mes en el mismo formato que el calendario admin de airbnb-agent.
    Incluye reservas, bloqueos y cancelados con campos completos (huésped, precios, notas, etc.)
    más stats de ocupación e ingresos del mes.

    Args:
        mes: Número de mes (1-12).
        anio: Año (ej: 2026). Si es 0 usa el año actual.

    Returns:
        Eventos del mes con datos completos + stats (ocupación, ingresos, total reservas, próximas).
    """
    from datetime import date as date_t
    import calendar as cal_mod

    db = _get_db()
    if db is None:
        return json.dumps({"error": "MongoDB no disponible"})

    if not anio:
        anio = datetime.now().year

    inicio_mes = date_t(anio, mes, 1)
    fin_mes = date_t(anio + 1, 1, 1) if mes == 12 else date_t(anio, mes + 1, 1)
    dias_en_mes = cal_mod.monthrange(anio, mes)[1]
    hoy = date_t.today()

    cursor = db["reservas"].find(
        {
            "event_start": {"$lt": fin_mes.isoformat()},
            "event_end":   {"$gte": inicio_mes.isoformat()},
        },
        sort=[("event_start", 1)],
    )

    eventos = []
    total_reservas = 0
    proximas = 0
    dias_ocupados = 0

    for doc in cursor:
        ev = _evento_formato_ical(doc)
        eventos.append(ev)

        if doc.get("estado") != "reservado":
            continue

        total_reservas += 1
        try:
            ev_start = date_t.fromisoformat(doc["event_start"])
            ev_end   = date_t.fromisoformat(doc["event_end"])
        except Exception:
            continue

        if ev_end > hoy:
            proximas += 1

        ev_end_excl   = ev_end + timedelta(days=1)
        overlap_start = max(ev_start, inicio_mes)
        overlap_end   = min(ev_end_excl, fin_mes)
        dias_ocupados += max(0, (overlap_end - overlap_start).days)

    dias_ocupados = min(dias_en_mes, dias_ocupados)
    ocupacion_pct = min(100, round((dias_ocupados / dias_en_mes) * 100))

    # Ingresos con prorrateo (reutiliza tool)
    ingresos_raw = json.loads(get_ingresos_mes(mes, anio))

    return json.dumps({
        "anio": anio,
        "mes": mes,
        "dias_en_mes": dias_en_mes,
        "stats": {
            "total_reservas": total_reservas,
            "proximas": proximas,
            "dias_ocupados": dias_ocupados,
            "dias_libres": dias_en_mes - dias_ocupados,
            "ocupacion_pct": ocupacion_pct,
        },
        "ingresos": {
            "arriendo": ingresos_raw.get("total_arriendo", 0),
            "tinaja": ingresos_raw.get("total_tinaja", 0),
            "total": ingresos_raw.get("total_ingresos", 0),
        },
        "total_eventos": len(eventos),
        "eventos": eventos,
    }, ensure_ascii=False)


@mcp.tool()
def get_ocupacion_stats_airbnb() -> str:
    """
    Estadísticas de ocupación: próximos 30 días, reservas futuras y próxima reserva.
    """
    db = _get_db()
    if db is None:
        return json.dumps({"error": "MongoDB no disponible"})

    hoy = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    next_30 = hoy + timedelta(days=30)
    hoy_str = hoy.strftime("%Y-%m-%d")

    reservas = list(db["reservas"].find(
        {"estado": "reservado", "event_end": {"$gt": hoy_str}},
        sort=[("event_start", 1)],
    ))

    total_dias_reservados = 0
    upcoming = 0
    for r in reservas:
        start = datetime.strptime(r["event_start"], "%Y-%m-%d")
        end = datetime.strptime(r["event_end"], "%Y-%m-%d")
        if start > hoy:
            upcoming += 1
        if start <= next_30 and end >= hoy:
            overlap_start = max(start, hoy)
            overlap_end = min(end, next_30)
            total_dias_reservados += (overlap_end - overlap_start).days

    ocupacion_pct = round((total_dias_reservados / 30) * 100)

    proxima = None
    if reservas:
        r = reservas[0]
        proxima = {
            "fecha_inicio": r["event_start"],
            "fecha_fin": r["event_end"],
            "noches": r.get("days", 0),
            "huesped": r.get("nombre_huesped", ""),
            "codigo": r.get("codigo_reserva", ""),
        }

    return json.dumps({
        "propiedad": PROPERTY_NAME,
        "hoy": hoy_str,
        "reservas_futuras": upcoming,
        "dias_reservados_proximos_30": total_dias_reservados,
        "ocupacion_30_dias_pct": ocupacion_pct,
        "proxima_reserva": proxima,
    }, ensure_ascii=False)


@mcp.tool()
def get_reservas_mes_airbnb(mes: int, anio: Optional[int] = None) -> str:
    """
    Lista todas las reservas del mes en formato completo (igual que admin de airbnb-agent).
    Incluye reservados, bloqueados y cancelados con todos los campos del huésped.
    El LLM debe formatear con emojis: ✅ reservado, 🔒 bloqueado, ❌ cancelado.

    Args:
        mes: Número de mes (1-12).
        anio: Año (ej: 2026). Si es 0 usa el año actual.

    Returns:
        Lista de eventos con campos completos: huésped, precios, notas, adultos, checkout, etc.
    """
    db = _get_db()
    if db is None:
        return json.dumps({"error": "MongoDB no disponible"})

    if not anio:
        anio = datetime.now().year

    inicio, fin = _mes_rango(mes, anio)

    cursor = db["reservas"].find(
        {
            "event_start": {"$lt": fin},
            "event_end":   {"$gte": inicio},
            "estado":      {"$ne": "bloqueado"},
        },
        sort=[("event_start", 1)],
    )

    eventos = []
    seen = set()
    for doc in cursor:
        key = f"{doc.get('event_start')}_{doc.get('event_end')}_{doc.get('estado')}"
        if key in seen:
            continue
        seen.add(key)
        eventos.append(_evento_formato_ical(doc))

    if not eventos:
        return json.dumps({"mensaje": f"Sin eventos para {mes}/{anio}"})

    return json.dumps({
        "anio": anio,
        "mes": mes,
        "total": len(eventos),
        "eventos": eventos,
    }, ensure_ascii=False)


@mcp.tool()
def buscar_reserva_airbnb(query: str) -> str:
    """
    Busca una reserva por código de reserva (ej: HM123456789) o por fecha (ej: 2026-04-15).

    Args:
        query: Código de reserva o fecha en formato YYYY-MM-DD.

    Returns:
        Datos completos de la reserva encontrada o mensaje de no encontrado.
    """
    db = _get_db()
    if db is None:
        return json.dumps({"error": "MongoDB no disponible"})

    query = query.strip()
    doc = None

    if re.match(r"^\d{4}-\d{2}-\d{2}$", query):
        doc = db["reservas"].find_one({
            "event_start": {"$lte": query},
            "event_end": {"$gt": query},
        })
    else:
        doc = db["reservas"].find_one({"codigo_reserva": query})

    if not doc:
        return json.dumps({"mensaje": f"No se encontró reserva para '{query}'"})
    return json.dumps(_serialize(doc), ensure_ascii=False)


def _mes_rango(mes: int, anio: int) -> tuple[str, str]:
    """Retorna (inicio_str, fin_str) del mes en formato YYYY-MM-DD."""
    inicio = f"{anio}-{mes:02d}-01"
    if mes == 12:
        fin = f"{anio + 1}-01-01"
    else:
        fin = f"{anio}-{mes + 1:02d}-01"
    return inicio, fin


@mcp.tool()
def get_ingresos_mes(mes: int, anio: Optional[int] = None) -> str:
    """
    Calcula ingresos del mes desde la colección 'reservas' con prorrateo proporcional.
    Si una reserva abarca días de otros meses, solo se cuenta la proporción que cae en el mes.
    La lógica usa event_end como día INCLUSIVO (igual que el calendario de airbnb-agent).

    Args:
        mes: Número de mes (1-12).
        anio: Año (ej: 2026). Si es 0 usa el año actual.

    Returns:
        Desglose por reserva con proporción aplicada y totales (arriendo, tinaja, total).
    """
    from datetime import date as date_t

    db = _get_db()
    if db is None:
        return json.dumps({"error": "MongoDB no disponible"})

    if not anio:
        anio = datetime.now().year

    inicio_mes = date_t(anio, mes, 1)
    fin_mes = date_t(anio + 1, 1, 1) if mes == 12 else date_t(anio, mes + 1, 1)
    inicio_str, fin_str = inicio_mes.isoformat(), fin_mes.isoformat()

    # Todas las reservas que se solapan con el mes
    cursor = db["reservas"].find(
        {
            "event_start": {"$lt": fin_str},
            "event_end": {"$gte": inicio_str},
            "estado": "reservado",
        },
        sort=[("event_start", 1)],
    )

    total_arriendo = 0
    total_tinaja = 0
    detalle = []

    for doc in cursor:
        try:
            ev_start = date_t.fromisoformat(doc["event_start"])
            ev_end = date_t.fromisoformat(doc["event_end"])
        except Exception:
            continue

        # event_end es INCLUSIVO → dias_totales incluye ese día
        dias_totales = max(1, (ev_end - ev_start).days + 1)
        ev_end_excl = ev_end + timedelta(days=1)
        overlap_start = max(ev_start, inicio_mes)
        overlap_end = min(ev_end_excl, fin_mes)
        dias_en_mes = max(0, (overlap_end - overlap_start).days)
        proporcion = dias_en_mes / dias_totales

        precio_raw = doc.get("precio") or 0
        extra_raw = doc.get("extra_valor") or 0
        extra_concepto = (doc.get("extra_concepto") or "").lower()

        precio_prop = round(precio_raw * proporcion)
        tinaja_prop = round(extra_raw * proporcion) if "tinaja" in extra_concepto else 0

        total_arriendo += precio_prop
        total_tinaja += tinaja_prop
        detalle.append({
            "huesped": doc.get("nombre_huesped", ""),
            "fecha_inicio": doc["event_start"],
            "fecha_fin": doc["event_end"],
            "dias_totales": dias_totales,
            "dias_en_mes": dias_en_mes,
            "proporcion": round(proporcion, 4),
            "arriendo": precio_prop,
            "tinaja": tinaja_prop,
        })

    return json.dumps({
        "anio": anio,
        "mes": mes,
        "total_arriendo": total_arriendo,
        "total_tinaja": total_tinaja,
        "total_ingresos": total_arriendo + total_tinaja,
        "reservas": len(detalle),
        "detalle": detalle,
    }, ensure_ascii=False)


@mcp.tool()
def get_gastos_mes(mes: int, anio: Optional[int] = None) -> str:
    """
    Suma todos los gastos del mes desde las colecciones de gastos:
    gastos_gasolina, gastos_aseo, gastos_agua, gastos_otros, gastos_internet.
    Filtra por campo 'fecha_pago' dentro del mes.

    Args:
        mes: Número de mes (1-12).
        anio: Año (ej: 2026). Si es 0 usa el año actual.

    Returns:
        Total de gastos por categoría y suma total.
    """
    db = _get_db()
    if db is None:
        return json.dumps({"error": "MongoDB no disponible"})

    if not anio:
        anio = datetime.now().year

    inicio, fin = _mes_rango(mes, anio)
    # fecha_pago se guarda como string 'YYYY-MM-DD'
    query = {"fecha_pago": {"$gte": inicio, "$lt": fin}}

    categorias = {
        "gasolina": "gastos_gasolina",
        "aseo": "gastos_aseo",
        "agua": "gastos_agua",
        "otros": "gastos_otros",
        "internet": "gastos_internet",
    }

    total_general = 0
    resumen = {}
    detalle = []

    for nombre, col in categorias.items():
        docs = list(db[col].find(query, sort=[("fecha_pago", 1)]))
        subtotal = sum(d.get("valor", 0) for d in docs)
        total_general += subtotal
        resumen[nombre] = subtotal
        for d in docs:
            detalle.append({
                "categoria": nombre,
                "fecha": d.get("fecha_pago"),
                "razon": d.get("razon", ""),
                "valor": d.get("valor", 0),
                "pagado": d.get("pagado", False),
            })

    detalle.sort(key=lambda x: x["fecha"] or "")

    return json.dumps({
        "anio": anio,
        "mes": mes,
        "total_gastos": total_general,
        "por_categoria": resumen,
        "detalle": detalle,
    }, ensure_ascii=False)


@mcp.tool()
def get_valor_neto_mes(mes: int, anio: Optional[int] = None) -> str:
    """
    Calcula el valor neto del mes: ingresos (arriendo + tinaja) menos gastos.
    Ingresos vienen de 'reservas', gastos de las colecciones gastos_*.

    Args:
        mes: Número de mes (1-12).
        anio: Año (ej: 2026). Si es 0 usa el año actual.

    Returns:
        Resumen con ingresos, gastos y valor neto.
    """
    ingresos_raw = json.loads(get_ingresos_mes(mes, anio))
    gastos_raw = json.loads(get_gastos_mes(mes, anio))

    if "error" in ingresos_raw or "error" in gastos_raw:
        return json.dumps({"error": ingresos_raw.get("error") or gastos_raw.get("error")})

    total_ingresos = ingresos_raw["total_ingresos"]
    total_gastos = gastos_raw["total_gastos"]
    neto = total_ingresos - total_gastos

    # 1 estrella por cada $100.000 de beneficio neto (misma lógica que airbnb-agent)
    estrellas = max(0, int(neto // 100_000))

    return json.dumps({
        "anio": ingresos_raw["anio"],
        "mes": mes,
        "ingresos": {
            "arriendo": ingresos_raw["total_arriendo"],
            "tinaja": ingresos_raw["total_tinaja"],
            "total": total_ingresos,
        },
        "gastos": {
            "total": total_gastos,
            "por_categoria": gastos_raw["por_categoria"],
        },
        "valor_neto": neto,
        "estrellas": estrellas,
        "estrellas_display": "⭐" * estrellas,
    }, ensure_ascii=False)


_CATEGORIAS_GASTOS = {
    "gasolina": "gastos_gasolina",
    "aseo":     "gastos_aseo",
    "agua":     "gastos_agua",
    "otros":    "gastos_otros",
    "internet": "gastos_internet",
}


def _gastos_query(db, col_name: str, query: dict) -> list:
    """Consulta una colección de gastos y serializa los resultados."""
    docs = list(db[col_name].find(query, sort=[("fecha_pago", 1)]))
    result = []
    for doc in docs:
        result.append({
            "id": str(doc["_id"]),
            "fecha": doc.get("fecha_pago", ""),
            "razon": doc.get("razon", ""),
            "nombre": doc.get("nombre", ""),
            "tipo": doc.get("tipo", ""),
            "descripcion": doc.get("descripcion", ""),
            "valor": doc.get("valor", 0),
            "pagado": doc.get("pagado", False),
            "whatsapp": doc.get("whatsapp", ""),
        })
    return result


@mcp.tool()
def get_gastos_desglose(mes: int, anio: Optional[int] = None) -> str:
    """
    Lista todos los gastos del mes con desglose completo por categoría y por ítem.
    Incluye razón, proveedor, tipo, valor, estado de pago.

    Args:
        mes: Número de mes (1-12).
        anio: Año (ej: 2026). Si es 0 usa el año actual.

    Returns:
        Gastos agrupados por categoría con detalle de cada ítem y totales.
    """
    db = _get_db()
    if db is None:
        return json.dumps({"error": "MongoDB no disponible"})

    if not anio:
        anio = datetime.now().year

    inicio, fin = _mes_rango(mes, anio)
    query = {"fecha_pago": {"$gte": inicio, "$lt": fin}}

    categorias_result = {}
    total_general = 0

    for nombre, col in _CATEGORIAS_GASTOS.items():
        items = _gastos_query(db, col, query)
        subtotal = sum(i["valor"] for i in items)
        total_general += subtotal
        if items:
            categorias_result[nombre] = {"subtotal": subtotal, "items": items}

    return json.dumps({
        "anio": anio,
        "mes": mes,
        "total_gastos": total_general,
        "categorias": categorias_result,
    }, ensure_ascii=False)


@mcp.tool()
def get_gastos_categoria(categoria: str, mes: int = 0, anio: Optional[int] = None) -> str:
    """
    Lista los gastos de una categoría específica, con filtro opcional de mes.
    Categorías válidas: gasolina, aseo, agua, otros, internet.

    Args:
        categoria: Nombre de la categoría (gasolina, aseo, agua, otros, internet).
        mes: Mes a filtrar (1-12). Si es 0, retorna todos los registros sin filtro de mes.
        anio: Año (ej: 2026). Si es 0 usa el año actual. Solo aplica si mes != 0.

    Returns:
        Lista de gastos de esa categoría con total.
    """
    db = _get_db()
    if db is None:
        return json.dumps({"error": "MongoDB no disponible"})

    categoria = categoria.lower().strip()
    if categoria not in _CATEGORIAS_GASTOS:
        validas = ", ".join(_CATEGORIAS_GASTOS.keys())
        return json.dumps({"error": f"Categoría inválida. Válidas: {validas}"})

    query = {}
    if mes != 0:
        if not anio:
            anio = datetime.now().year
        inicio, fin = _mes_rango(mes, anio)
        query = {"fecha_pago": {"$gte": inicio, "$lt": fin}}

    col = _CATEGORIAS_GASTOS[categoria]
    items = _gastos_query(db, col, query)
    total = sum(i["valor"] for i in items)

    return json.dumps({
        "categoria": categoria,
        "mes": mes if mes != 0 else "todos",
        "anio": anio if mes != 0 else "todos",
        "total": total,
        "cantidad": len(items),
        "items": items,
    }, ensure_ascii=False)


@mcp.tool()
def get_gastos_proveedor(nombre: str, mes: int = 0, anio: Optional[int] = None) -> str:
    """
    Lista todos los gastos asociados a un proveedor (busca por nombre, case-insensitive).
    Busca en todas las categorías de gastos.

    Args:
        nombre: Nombre del proveedor (ej: 'Shell', 'Severina', 'Starlink').
        mes: Mes a filtrar (1-12). Si es 0, retorna todos sin filtro de mes.
        anio: Año (ej: 2026). Si es 0 usa el año actual. Solo aplica si mes != 0.

    Returns:
        Lista de gastos del proveedor con total acumulado.
    """
    db = _get_db()
    if db is None:
        return json.dumps({"error": "MongoDB no disponible"})

    query: dict = {"nombre": {"$regex": nombre.strip(), "$options": "i"}}
    if mes != 0:
        if not anio:
            anio = datetime.now().year
        inicio, fin = _mes_rango(mes, anio)
        query["fecha_pago"] = {"$gte": inicio, "$lt": fin}

    todos = []
    for cat_nombre, col in _CATEGORIAS_GASTOS.items():
        items = _gastos_query(db, col, query)
        for item in items:
            item["categoria"] = cat_nombre
        todos.extend(items)

    todos.sort(key=lambda x: x.get("fecha", ""))
    total = sum(i["valor"] for i in todos)

    if not todos:
        return json.dumps({"mensaje": f"No se encontraron gastos para '{nombre}'"})

    return json.dumps({
        "proveedor": nombre,
        "mes": mes if mes != 0 else "todos",
        "total": total,
        "cantidad": len(todos),
        "gastos": todos,
    }, ensure_ascii=False)


@mcp.tool()
def get_proveedores(tipo: str = "") -> str:
    """
    Lista todos los proveedores registrados.

    Args:
        tipo: Filtrar por tipo de proveedor (agua, aseo, gasolinera, internet, etc.).
              Si está vacío, retorna todos.

    Returns:
        Lista de proveedores con nombre, servicio, tipo, banco y contacto.
    """
    db = _get_db()
    if db is None:
        return json.dumps({"error": "MongoDB no disponible"})

    query = {}
    if tipo.strip():
        query = {"tipo": {"$regex": tipo.strip(), "$options": "i"}}

    proveedores = []
    for doc in db["proveedores"].find(query, sort=[("tipo", 1), ("nombre", 1)]):
        proveedores.append({
            "id": str(doc["_id"]),
            "nombre": doc.get("nombre", ""),
            "servicio": doc.get("servicio", ""),
            "tipo": doc.get("tipo", ""),
            "banco": doc.get("banco", ""),
            "tipo_cuenta": doc.get("tipo_cuenta", ""),
            "numero_cuenta": doc.get("numero_cuenta", ""),
            "rut": doc.get("rut", ""),
            "whatsapp": doc.get("whatsapp", ""),
            "email": doc.get("email", ""),
        })

    if not proveedores:
        return json.dumps({"mensaje": f"No se encontraron proveedores" + (f" de tipo '{tipo}'" if tipo else "")})

    return json.dumps({
        "total": len(proveedores),
        "proveedores": proveedores,
    }, ensure_ascii=False)


@mcp.tool()
def get_ocupacion_mes(mes: int, anio: Optional[int] = None) -> str:
    """
    Calcula la ocupación del mes: total de reservas que tocan el mes,
    reservas próximas (sin checkout y con end > hoy), días ocupados y porcentaje de ocupación.
    Usa la misma lógica que el widget de estadísticas de airbnb-agent:
    - días_ocupados = suma del overlap de cada reserva con el mes (end INCLUSIVO)
    - ocupacion_pct = min(100, round(dias_ocupados / dias_en_mes * 100))
    - proximas = reservas que aún no finalizaron (end > hoy)

    Args:
        mes: Número de mes (1-12).
        anio: Año (ej: 2026). Si es 0 usa el año actual.

    Returns:
        total_reservas, proximas, dias_ocupados, dias_libres, ocupacion_pct y detalle.
    """
    from datetime import date as date_t
    import calendar

    db = _get_db()
    if db is None:
        return json.dumps({"error": "MongoDB no disponible"})

    if not anio:
        anio = datetime.now().year

    inicio_mes = date_t(anio, mes, 1)
    fin_mes = date_t(anio + 1, 1, 1) if mes == 12 else date_t(anio, mes + 1, 1)
    dias_en_mes = calendar.monthrange(anio, mes)[1]
    hoy = date_t.today()

    cursor = db["reservas"].find(
        {
            "event_start": {"$lt": fin_mes.isoformat()},
            "event_end":   {"$gte": inicio_mes.isoformat()},
            "estado": "reservado",
        },
        sort=[("event_start", 1)],
    )

    total_reservas = 0
    proximas = 0
    dias_ocupados = 0
    detalle = []

    for doc in cursor:
        try:
            ev_start = date_t.fromisoformat(doc["event_start"])
            ev_end   = date_t.fromisoformat(doc["event_end"])
        except Exception:
            continue

        total_reservas += 1

        # Próximas = no finalizadas (end > hoy)
        if ev_end > hoy:
            proximas += 1

        # Días ocupados en el mes (end INCLUSIVO, misma lógica que ingresos)
        ev_end_excl   = ev_end + timedelta(days=1)
        overlap_start = max(ev_start, inicio_mes)
        overlap_end   = min(ev_end_excl, fin_mes)
        dias_mes = max(0, (overlap_end - overlap_start).days)
        dias_ocupados += dias_mes

        detalle.append({
            "huesped": doc.get("nombre_huesped", ""),
            "fecha_inicio": doc["event_start"],
            "fecha_fin": doc["event_end"],
            "dias_en_mes": dias_mes,
            "proxima": ev_end > hoy,
        })

    dias_ocupados = min(dias_en_mes, dias_ocupados)
    ocupacion_pct = min(100, round((dias_ocupados / dias_en_mes) * 100))
    dias_libres   = dias_en_mes - dias_ocupados

    return json.dumps({
        "anio": anio,
        "mes": mes,
        "dias_en_mes": dias_en_mes,
        "total_reservas": total_reservas,
        "proximas": proximas,
        "dias_ocupados": dias_ocupados,
        "dias_libres": dias_libres,
        "ocupacion_pct": ocupacion_pct,
        "detalle": detalle,
    }, ensure_ascii=False)


@mcp.tool()
def get_rentabilidad_mes(mes: int, anio: Optional[int] = None) -> str:
    """
    Calcula la rentabilidad del mes con las mismas métricas que el widget de airbnb-agent:
    - ROI: (ingresos - gastos) / gastos * 100
    - Margen: (ingresos - gastos) / ingresos * 100
    - Promedio anual: promedio de ingresos de meses cerrados (dic año anterior + meses del año hasta hoy)
    - Variación vs promedio: cuánto se desvía el mes del promedio histórico
    - Tendencia: ↑ sobre el promedio, ↓ bajo el promedio, → igual o sin datos

    Args:
        mes: Número de mes (1-12).
        anio: Año (ej: 2026). Si es 0 usa el año actual.

    Returns:
        ROI, margen, promedio anual, variación y tendencia del mes.
    """
    from datetime import date as date_t

    if not anio:
        anio = datetime.now().year

    # Ingresos y gastos del mes consultado
    ingresos_raw = json.loads(get_ingresos_mes(mes, anio))
    gastos_raw = json.loads(get_gastos_mes(mes, anio))
    if "error" in ingresos_raw or "error" in gastos_raw:
        return json.dumps({"error": ingresos_raw.get("error") or gastos_raw.get("error")})

    ingresos_mes = ingresos_raw["total_ingresos"]
    gastos_mes = gastos_raw["total_gastos"]
    neto = ingresos_mes - gastos_mes

    # ROI
    if gastos_mes > 0:
        roi = ((ingresos_mes - gastos_mes) / gastos_mes) * 100
    elif ingresos_mes > 0:
        roi = 100.0
    else:
        roi = 0.0

    # Margen
    if ingresos_mes > 0:
        margen = ((ingresos_mes - gastos_mes) / ingresos_mes) * 100
    elif gastos_mes > 0:
        margen = -100.0
    else:
        margen = 0.0

    # Promedio anual: dic año anterior + meses cerrados del año actual (hasta mes anterior al hoy)
    hoy = date_t.today()
    meses_cerrados = [{"mes": 12, "anio": anio - 1}]
    for m in range(1, mes):  # solo meses anteriores al consultado en el mismo año
        if anio < hoy.year or (anio == hoy.year and m < hoy.month):
            meses_cerrados.append({"mes": m, "anio": anio})

    promedio_ingresos = 0
    resultados_promedio = []
    for mc in meses_cerrados:
        r = json.loads(get_ingresos_mes(mc["mes"], mc["anio"]))
        ing = r.get("total_ingresos", 0)
        resultados_promedio.append({"mes": mc["mes"], "anio": mc["anio"], "ingresos": ing})

    if resultados_promedio:
        promedio_ingresos = round(sum(r["ingresos"] for r in resultados_promedio) / len(resultados_promedio))

    # Variación vs promedio
    if promedio_ingresos > 0:
        variacion = ((ingresos_mes - promedio_ingresos) / promedio_ingresos) * 100
    else:
        variacion = None

    # Tendencia
    if variacion is None:
        tendencia = "→"
    elif ingresos_mes > promedio_ingresos:
        tendencia = "↑"
    elif ingresos_mes < promedio_ingresos:
        tendencia = "↓"
    else:
        tendencia = "→"

    # Estrellas
    estrellas = max(0, int(neto // 100_000))

    return json.dumps({
        "anio": anio,
        "mes": mes,
        "ingresos": ingresos_mes,
        "gastos": gastos_mes,
        "neto": neto,
        "roi_pct": round(roi),
        "margen_pct": round(margen),
        "promedio_anual_ingresos": promedio_ingresos,
        "meses_para_promedio": len(resultados_promedio),
        "variacion_vs_promedio_pct": round(variacion) if variacion is not None else None,
        "tendencia": tendencia,
        "estrellas": estrellas,
        "estrellas_display": "⭐" * estrellas,
    }, ensure_ascii=False)


@mcp.tool()
def get_airbnb_status() -> str:
    """Estado de la conexión MongoDB y configuración del servidor Airbnb."""
    db = _get_db()
    mongo_ok = db is not None
    return json.dumps({
        "propiedad": PROPERTY_NAME,
        "mongodb": "conectado" if mongo_ok else "desconectado",
        "db": AIRBNB_DB,
    })
