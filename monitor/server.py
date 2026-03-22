"""Servidor MCP Monitor - lectura en tiempo real desde MQTT y historial MongoDB."""

import json
import os
import threading
import time
from calendar import monthrange
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
# Fallback: cargar desde amanda-IA si MONGODB_URI no está en aia-mcp/.env
if not os.environ.get("MONGODB_URI"):
    _env_amanda = Path(__file__).resolve().parent.parent.parent / "amanda-IA" / ".env"
    if _env_amanda.exists():
        load_dotenv(_env_amanda)

import tomllib
from mcp.server.fastmcp import FastMCP

# MongoDB para historial (estanque-historial)
try:
    from pymongo import MongoClient
    _HAS_PYMONGO = True
except ImportError:
    _HAS_PYMONGO = False

# MQTT (mismo formato que monitor_estanque.py)
try:
    import paho.mqtt.client as mqtt
    _HAS_MQTT = True
except ImportError:
    _HAS_MQTT = False


mcp = FastMCP(
    "monitor",
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("FASTMCP_PORT", "8003")),
)

# Versión desde pyproject.toml
_pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
if _pyproject.exists():
    with open(_pyproject, "rb") as f:
        mcp._mcp_server.version = tomllib.load(f)["tool"]["poetry"]["version"]

# Configuración del estanque (env o defaults, como monitor_estanque)
ALTURA_SENSOR = int(os.environ.get("TINAJA_ALTURA_SENSOR", "145"))
CAPACIDAD_LITROS = int(os.environ.get("TINAJA_CAPACIDAD_LITROS", "5000"))
UMBRAL_ALERTA = 80
UMBRAL_PELIGRO = 140

# Configuración MQTT (mismo formato que monitor_estanque.py líneas 85-90)
MQTT_HOST = os.environ.get("MQTT_HOST", "broker.mqttdashboard.com")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "test")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "test")
MQTT_TOPIC_OUT = os.environ.get("MQTT_TOPIC_OUT", "yai-mqtt/01C40A24/out")

# Última lectura desde MQTT (buffer de 10 como monitor)
_lecturas_buffer: deque = deque(maxlen=10)
_ultima_lectura: dict | None = None
_reading_version: int = 0  # se incrementa por cada nueva lectura MQTT
_mqtt_connected = False
_mqtt_thread: threading.Thread | None = None


def _calcular_nivel(distancia_sensor: float, altura_sensor: float = ALTURA_SENSOR) -> dict:
    """Calcula litros y porcentaje desde la distancia (cm). Mismo logic que monitor_estanque."""
    altura_agua = altura_sensor - distancia_sensor
    altura_agua = max(0, min(altura_agua, altura_sensor))
    porcentaje = (altura_agua / altura_sensor) * 100 if altura_sensor else 0
    litros = (altura_agua / altura_sensor) * CAPACIDAD_LITROS if altura_sensor else 0
    if distancia_sensor > UMBRAL_PELIGRO:
        estado = "peligro"
    elif distancia_sensor > UMBRAL_ALERTA:
        estado = "alerta"
    else:
        estado = "normal"
    return {
        "distancia": distancia_sensor,
        "litros": litros,
        "porcentaje": porcentaje,
        "estado": estado,
    }


def _on_mqtt_connect(client, userdata, flags, reason_code, properties):
    global _mqtt_connected
    _mqtt_connected = reason_code == 0
    if _mqtt_connected:
        client.subscribe(MQTT_TOPIC_OUT)


def _on_mqtt_disconnect(client, userdata, flags, reason_code, properties):
    global _mqtt_connected
    _mqtt_connected = False


def _on_mqtt_message(client, userdata, msg):
    """Procesa mensaje MQTT. Mismo formato que monitor_estanque on_mqtt_message."""
    global _ultima_lectura, _lecturas_buffer, _reading_version
    try:
        payload = msg.payload.decode("utf-8").strip()
        payload_json = None
        try:
            payload_json = json.loads(payload)
        except Exception:
            pass

        distancia_raw = None
        altura_sensor = ALTURA_SENSOR
        fill_level = None
        status = None

        if payload_json:
            status = payload_json.get("status")
            distancia_raw = float(
                payload_json.get("distanceCm") or payload_json.get("distancia") or 0
            )
            altura_sensor = float(payload_json.get("tankDepthCm") or altura_sensor)
            fill_level = payload_json.get("fillLevelPercent")
            if fill_level is not None:
                fill_level = float(fill_level)
        else:
            # Formato CSV: device_id,OKO,distancia,...
            partes = payload.split(",")
            if len(partes) >= 3 and "OKO" in partes[1]:
                status = "OKO"
                distancia_raw = float(partes[2])

        if status == "OKO" and distancia_raw is not None:
            if distancia_raw < 21:
                distancia_raw = max(0, distancia_raw - 15)
            _lecturas_buffer.append(distancia_raw)
            distancia_promedio = sum(_lecturas_buffer) / len(_lecturas_buffer)
            datos = _calcular_nivel(distancia_promedio, altura_sensor=altura_sensor)
            if fill_level is not None:
                datos["porcentaje"] = fill_level
                datos["litros"] = round((fill_level / 100.0) * CAPACIDAD_LITROS, 2)
            _ultima_lectura = datos
            _reading_version += 1
    except Exception:
        pass


def _iniciar_mqtt():
    """Thread que mantiene conexión MQTT y actualiza _ultima_lectura."""
    global _mqtt_thread
    if not _HAS_MQTT or (_mqtt_thread is not None and _mqtt_thread.is_alive()):
        return
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_connect = _on_mqtt_connect
    client.on_disconnect = _on_mqtt_disconnect
    client.on_message = _on_mqtt_message
    _mqtt_thread = threading.Thread(
        target=lambda: _mqtt_loop(client),
        daemon=True,
    )
    _mqtt_thread.start()


def _mqtt_loop(client):
    """Loop de reconexión MQTT (como monitor_estanque)."""
    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            client.loop_forever()
        except Exception:
            pass
        time.sleep(5)


# Iniciar MQTT al cargar el módulo (si está configurado)
if _HAS_MQTT and MQTT_HOST:
    _iniciar_mqtt()


@mcp.tool()
def calculate_tinaja_level(distance: float) -> str:
    """
    Calcula litros y porcentaje del acumulador/estanque desde la distancia del sensor (cm).

    Args:
        distance: Distancia en cm desde el sensor hasta la superficie del agua.

    Returns:
        Litros, porcentaje y estado (normal/alerta/peligro).
    """
    datos = _calcular_nivel(distance)
    return (
        f"{datos['litros']:.0f}L ({datos['porcentaje']:.0f}%) - {datos['estado']}"
    )


@mcp.tool()
def get_tinaja_config() -> str:
    """Configuración del estanque y conexión MQTT."""
    mqtt_status = "conectado" if _mqtt_connected else "desconectado"
    return (
        f"Altura sensor: {ALTURA_SENSOR} cm | Capacidad: {CAPACIDAD_LITROS} L | "
        f"Alerta: >{UMBRAL_ALERTA} cm | Peligro: >{UMBRAL_PELIGRO} cm | "
        f"MQTT: {mqtt_status} ({MQTT_TOPIC_OUT})"
    )


@mcp.tool()
def get_lectura_actual() -> str:
    """
    Obtiene la última lectura del estanque: litros, porcentaje, estado y distancia.

    Primero intenta la última lectura recibida por MQTT.
    Si no hay datos MQTT disponibles, cae al último registro en MongoDB.
    Los valores se muestran tal como vienen, sin cálculos adicionales.
    """
    # 1. Última lectura MQTT en memoria
    if _ultima_lectura:
        d = _ultima_lectura
        return json.dumps({
            "fuente": "mqtt",
            "litros": d.get("litros"),
            "porcentaje": d.get("porcentaje"),
            "estado": d.get("estado"),
            "distancia_cm": d.get("distancia"),
        }, ensure_ascii=False)

    # 2. Fallback: último registro en MongoDB
    if _HAS_PYMONGO:
        docs = _get_estanque_historial(limit=1)
        if docs:
            d = docs[0]
            ts = d.get("timestamp")
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            return json.dumps({
                "fuente": "mongodb",
                "timestamp": ts_str,
                "litros": d.get("litros"),
                "porcentaje": d.get("porcentaje"),
                "estado": d.get("estado"),
                "distancia_cm": d.get("distancia_cm"),
            }, ensure_ascii=False)

    return "Sin datos disponibles. MQTT no conectado y sin registros en MongoDB."


def _resolver_fecha(valor: str) -> datetime | None:
    """
    Resuelve una expresión de fecha a datetime UTC.

    Acepta:
    - ISO 8601: '2026-03-21', '2026-03-21T10:00:00'
    - Relativas: 'today', 'yesterday', 'now', 'now-1 day', 'now-3 days',
                 'now-2 hours', 'now-1 week', 'now-1 month'

    Returns:
        datetime con tzinfo=UTC, o None si no se pudo parsear.
    """
    import re
    now = datetime.now(timezone.utc)
    v = valor.strip().lower()

    if v in ("today", "now"):
        return now
    if v == "yesterday":
        return now - timedelta(days=1)

    # Patrón: now-N unit(s) — con o sin espacios, ej: 'now-2days', 'now-2 days', 'now - 2 days'
    m = re.match(r"now\s*-\s*(\d+)\s*(second|minute|hour|day|week|month)s?", v)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta_map = {
            "second": timedelta(seconds=n),
            "minute": timedelta(minutes=n),
            "hour":   timedelta(hours=n),
            "day":    timedelta(days=n),
            "week":   timedelta(weeks=n),
            "month":  timedelta(days=n * 30),
        }
        return now - delta_map[unit]

    # ISO 8601
    try:
        dt = datetime.fromisoformat(valor.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _parsear_rango_fechas(
    fecha_desde: str,
    fecha_hasta: str = "",
) -> tuple[datetime, datetime] | str:
    """
    Parsea y normaliza un rango de fechas para consultas.

    Acepta ISO 8601 o expresiones relativas ('yesterday', 'now-2 days', etc.).
    - fecha_desde se fuerza a las 00:00:00 del día resuelto.
    - fecha_hasta se fuerza a las 23:59:59 del día resuelto.
      Si no se provee, se usa el mismo día que fecha_desde.

    Returns:
        Tupla (desde_dt, hasta_dt) con timezone UTC, o string de error.
    """
    desde_dt = _resolver_fecha(fecha_desde)
    if desde_dt is None:
        return f"fecha_desde inválida: '{fecha_desde}'. Use ISO 8601 (ej: '2026-03-21') o relativa (ej: 'yesterday', 'now-2 days')."

    # Forzar inicio del día (00:00:00)
    desde_dt = desde_dt.replace(hour=0, minute=0, second=0, microsecond=0)

    if fecha_hasta:
        hasta_dt = _resolver_fecha(fecha_hasta)
        if hasta_dt is None:
            return f"fecha_hasta inválida: '{fecha_hasta}'. Use ISO 8601 o relativa."
        hasta_dt = hasta_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        hasta_dt = desde_dt.replace(hour=23, minute=59, second=59, microsecond=999999)

    return desde_dt, hasta_dt


def _get_estanque_historial(
    db_name: str = "tomi-db",
    limit: int = 200,
    desde: float | None = None,
    hasta: float | None = None,
) -> list[dict]:
    """Obtiene registros de estanque-historial desde MongoDB con filtro opcional de fechas."""
    uri = os.environ.get("MONGODB_URI", "").strip()
    if not uri or not _HAS_PYMONGO:
        return []
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        db = client[db_name]
        coll = db["estanque-historial"]
        query: dict = {}
        if desde is not None or hasta is not None:
            ts_filter: dict = {}
            if desde is not None:
                ts_filter["$gte"] = datetime.fromtimestamp(desde, tz=timezone.utc)
            if hasta is not None:
                ts_filter["$lte"] = datetime.fromtimestamp(hasta, tz=timezone.utc)
            query["timestamp"] = ts_filter
        cursor = coll.find(query).sort("timestamp", -1).limit(limit)
        docs = list(cursor)
        client.close()
        return docs
    except Exception:
        return []


@mcp.tool()
def get_historial(
    fecha_desde: str,
    fecha_hasta: str = "",
    limit: int = 500,
    db_name: str = "tomi-db",
) -> str:
    """
    Retorna registros del historial del estanque en un rango de fechas.

    Args:
        fecha_desde: Fecha/hora inicio en formato ISO 8601, ej: "2025-03-20" o "2025-03-20T10:00:00".
        fecha_hasta: Fecha/hora fin en formato ISO 8601 (opcional, default: ahora).
        limit: Máximo de registros a retornar (default: 500).
        db_name: Base de datos MongoDB (default: tomi-db).

    Returns:
        JSON con lista de registros ordenados del más reciente al más antiguo.
    """
    if not _HAS_PYMONGO:
        return "pymongo no instalado. Ejecute: poetry add pymongo"

    uri = os.environ.get("MONGODB_URI", "").strip()
    if not uri:
        return "MONGODB_URI no configurado."

    rango = _parsear_rango_fechas(fecha_desde, fecha_hasta)
    if isinstance(rango, str):
        return rango
    desde_dt, hasta_dt = rango

    docs = _get_estanque_historial(
        db_name=db_name,
        limit=limit,
        desde=desde_dt.timestamp(),
        hasta=hasta_dt.timestamp(),
    )
    if not docs:
        return f"No hay registros entre {desde_dt.isoformat()} y {hasta_dt.isoformat()}."

    result = []
    for d in docs:
        ts = d.get("timestamp")
        ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        result.append({
            "timestamp": ts_str,
            "litros": d.get("litros"),
            "porcentaje": d.get("porcentaje"),
            "distancia_cm": d.get("distancia_cm"),
            "estado": d.get("estado"),
        })

    return json.dumps(
        {"total": len(result), "desde": desde_dt.isoformat(), "hasta": hasta_dt.isoformat(), "registros": result},
        ensure_ascii=False,
    )


@mcp.tool()
def get_velocidad_disminucion_agua(
    fecha_desde: str,
    fecha_hasta: str = "",
    db_name: str = "tomi-db",
) -> str:
    """
    Calcula la velocidad de disminución del agua (L/h) en un rango de fechas.

    Args:
        fecha_desde: Fecha/hora inicio en formato ISO 8601, ej: "2025-03-20" o "2025-03-20T10:00:00".
        fecha_hasta: Fecha/hora fin en formato ISO 8601 (opcional, default: ahora).
        db_name: Base de datos MongoDB (default: tomi-db).

    Returns:
        Velocidad en L/h (positivo = agua bajando, negativo = subiendo) y resumen.
    """
    if not _HAS_PYMONGO:
        return "pymongo no instalado. Ejecute: poetry add pymongo"

    uri = os.environ.get("MONGODB_URI", "").strip()
    if not uri:
        return (
            "MONGODB_URI no configurado. Agregue MONGODB_URI en .env (aia-mcp o amanda-IA) "
            "para consultar estanque-historial."
        )

    rango = _parsear_rango_fechas(fecha_desde, fecha_hasta)
    if isinstance(rango, str):
        return rango
    desde_dt, hasta_dt = rango

    docs = _get_estanque_historial(
        db_name=db_name,
        limit=5000,
        desde=desde_dt.timestamp(),
        hasta=hasta_dt.timestamp(),
    )
    if not docs:
        return f"No hay registros en estanque-historial entre {desde_dt.isoformat()} y {hasta_dt.isoformat()}."

    # Ordenar por timestamp ascendente (más antiguo primero)
    docs_asc = sorted(docs, key=_ts_to_float)

    if len(docs_asc) < 2:
        return (
            f"Solo hay {len(docs_asc)} registro(s) en el rango indicado. "
            "Se necesitan al menos 2 para calcular velocidad."
        )

    primero = docs_asc[0]
    ultimo = docs_asc[-1]
    litros_ini = float(primero.get("litros") or 0)
    litros_fin = float(ultimo.get("litros") or 0)
    ts_ini = _ts_to_float(primero)
    ts_fin = _ts_to_float(ultimo)
    delta_horas = (ts_fin - ts_ini) / 3600 if ts_fin > ts_ini else 0

    if delta_horas < 0.01:
        return "Intervalo de tiempo demasiado corto para calcular velocidad."

    delta_litros = litros_ini - litros_fin  # positivo = agua bajó
    velocidad = delta_litros / delta_horas

    direccion = "bajando" if velocidad > 0 else "subiendo"
    return (
        f"Velocidad: {abs(velocidad):.1f} L/h ({direccion}) | "
        f"Desde: {desde_dt.isoformat()} | Hasta: {hasta_dt.isoformat()} | "
        f"Litros: {litros_ini:.0f} → {litros_fin:.0f} | "
        f"Registros: {len(docs_asc)}"
    )


def _ts_to_float(d: dict) -> float:
    """Convierte timestamp o hora_local a segundos desde epoch."""
    ts = d.get("timestamp")
    if ts is None:
        try:
            s = d.get("hora_local", "")
            return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            return 0
    if hasattr(ts, "timestamp"):
        return ts.timestamp()
    return float(ts) if ts else 0


@mcp.tool()
def estimar_duracion_agua(db_name: str = "tomi-db") -> str:
    """
    Estima cuántos días queda agua en el estanque basándose en el consumo reciente.

    Analiza los últimos 10 días de registros en MongoDB para calcular la tasa de
    consumo diario (regresión lineal). Con la cantidad actual de litros y la
    capacidad total de 5000 L estima cuándo se agotará el agua.

    No requiere parámetros de fecha: usa now-10 días como inicio y now como fin.

    Returns:
        JSON con litros actuales, consumo diario estimado, días restantes y fecha estimada de agotamiento.
    """
    if not _HAS_PYMONGO:
        return "pymongo no instalado. Ejecute: poetry add pymongo"

    uri = os.environ.get("MONGODB_URI", "").strip()
    if not uri:
        return "MONGODB_URI no configurado."

    CAPACIDAD_TOTAL = 5000.0

    now = datetime.now(timezone.utc)
    desde_dt = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=10)
    hasta_dt = now

    docs = _get_estanque_historial(
        db_name=db_name,
        limit=10000,
        desde=desde_dt.timestamp(),
        hasta=hasta_dt.timestamp(),
    )
    if not docs:
        return f"No hay registros en los últimos 10 días ({desde_dt.date()} → {hasta_dt.date()})."

    # Ordenar ascendente y filtrar registros con litros válidos
    docs_asc = sorted(
        [d for d in docs if d.get("litros") is not None],
        key=_ts_to_float,
    )
    if len(docs_asc) < 2:
        return "Datos insuficientes para estimar consumo (se necesitan al menos 2 registros con litros)."

    # Regresión lineal: x = tiempo en horas desde primer registro, y = litros
    t0 = _ts_to_float(docs_asc[0])
    xs = [((_ts_to_float(d) - t0) / 3600) for d in docs_asc]  # horas
    ys = [float(d["litros"]) for d in docs_asc]

    n = len(xs)
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xx = sum(x * x for x in xs)
    sum_xy = sum(x * y for x, y in zip(xs, ys))

    denom = n * sum_xx - sum_x ** 2
    if abs(denom) < 1e-9:
        return "No se pudo calcular la tendencia: todos los registros tienen el mismo timestamp."

    # pendiente en L/h (negativa = consumo)
    pendiente_lh = (n * sum_xy - sum_x * sum_y) / denom
    consumo_lh = -pendiente_lh  # positivo = se consume
    consumo_diario = consumo_lh * 24

    # Litros actuales = último registro
    litros_actuales = ys[-1]

    if consumo_diario <= 0:
        return json.dumps({
            "litros_actuales": round(litros_actuales, 1),
            "capacidad_total_l": CAPACIDAD_TOTAL,
            "consumo_diario_l": round(consumo_diario, 1),
            "mensaje": "El nivel no muestra tendencia de consumo en los últimos 10 días.",
            "registros_analizados": n,
        }, ensure_ascii=False)

    dias_restantes = litros_actuales / consumo_diario
    fecha_agotamiento = now + timedelta(days=dias_restantes)

    return json.dumps({
        "litros_actuales": round(litros_actuales, 1),
        "capacidad_total_l": CAPACIDAD_TOTAL,
        "porcentaje_actual": round((litros_actuales / CAPACIDAD_TOTAL) * 100, 1),
        "consumo_diario_l": round(consumo_diario, 1),
        "consumo_por_hora_l": round(consumo_lh, 2),
        "dias_restantes": round(dias_restantes, 1),
        "fecha_estimada_agotamiento": fecha_agotamiento.strftime("%Y-%m-%d"),
        "periodo_analizado_dias": 10,
        "registros_analizados": n,
        "desde": desde_dt.date().isoformat(),
        "hasta": hasta_dt.date().isoformat(),
    }, ensure_ascii=False)



def _compute_consumo_docs(docs):
    from datetime import datetime, timezone
    if len(docs) < 2:
        return {"total_consumido": 0.0, "promedio_lh": 0.0, "horas": 0.0,
                "registros": len(docs), "max_consumo_hora": 0.0, "hora_pico": None}
    total = 0.0
    for i in range(1, len(docs)):
        diff = float(docs[i - 1].get("litros") or 0) - float(docs[i].get("litros") or 0)
        if diff > 0:
            total += diff
    ts_ini = _ts_to_float(docs[0])
    ts_fin = _ts_to_float(docs[-1])
    horas = (ts_fin - ts_ini) / 3600 if ts_fin > ts_ini else 0
    promedio_lh = total / horas if horas > 0 else 0
    hourly = {}
    for doc in docs:
        ts = _ts_to_float(doc)
        if ts:
            key = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H")
            hourly.setdefault(key, []).append(doc)
    max_h, hora_pico = 0.0, None
    for key, hdocs in hourly.items():
        if len(hdocs) < 2:
            continue
        hdocs_s = sorted(hdocs, key=_ts_to_float)
        consumo_h = float(hdocs_s[0].get("litros") or 0) - float(hdocs_s[-1].get("litros") or 0)
        if consumo_h > max_h:
            max_h, hora_pico = consumo_h, key
    return {"total_consumido": total, "promedio_lh": promedio_lh, "horas": horas,
            "registros": len(docs), "max_consumo_hora": max_h, "hora_pico": hora_pico}


@mcp.tool()
def get_consumo_periodo(fecha_inicio: str, fecha_fin: str = "", db_name: str = "tomi-db") -> str:
    """Calcula consumo real de agua para un dia, mes o rango de fechas."""
    if not _HAS_PYMONGO:
        return "pymongo no instalado."
    uri = os.environ.get("MONGODB_URI", "").strip()
    if not uri:
        return "MONGODB_URI no configurado."
    rango = _parsear_rango_fechas(fecha_inicio, fecha_fin)
    if isinstance(rango, str):
        return rango
    desde_dt, hasta_dt = rango
    docs = _get_estanque_historial(db_name=db_name, limit=50000,
                                    desde=desde_dt.timestamp(), hasta=hasta_dt.timestamp())
    if not docs:
        return f"No hay registros entre {desde_dt.date()} y {hasta_dt.date()}."
    docs_asc = sorted(docs, key=_ts_to_float)
    stats = _compute_consumo_docs(docs_asc)
    return json.dumps({
        "desde": desde_dt.date().isoformat(),
        "hasta": hasta_dt.date().isoformat(),
        "total_consumido_l": round(stats["total_consumido"], 1),
        "promedio_lh": round(stats["promedio_lh"], 2),
        "hora_pico": stats["hora_pico"],
        "max_consumo_hora_l": round(stats["max_consumo_hora"], 1),
        "horas_analizadas": round(stats["horas"], 1),
        "registros": stats["registros"],
    }, ensure_ascii=False)


@mcp.tool()
def get_top_consumo(top: int = 5, db_name: str = "tomi-db") -> str:
    """Retorna los N dias con mayor consumo de agua."""
    if not _HAS_PYMONGO:
        return "pymongo no instalado."
    uri = os.environ.get("MONGODB_URI", "").strip()
    if not uri:
        return "MONGODB_URI no configurado."
    docs = _get_estanque_historial(db_name=db_name, limit=100000)
    if not docs:
        return "No hay registros en MongoDB."
    docs_asc = sorted(docs, key=_ts_to_float)
    by_day = {}
    for doc in docs_asc:
        ts = _ts_to_float(doc)
        if ts:
            from datetime import datetime, timezone
            day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            by_day.setdefault(day, []).append(doc)
    day_stats = []
    for day, ddocs in by_day.items():
        if len(ddocs) < 2:
            continue
        stats = _compute_consumo_docs(sorted(ddocs, key=_ts_to_float))
        day_stats.append({
            "fecha": day,
            "total_consumido_l": round(stats["total_consumido"], 1),
            "promedio_lh": round(stats["promedio_lh"], 2),
            "hora_pico": stats["hora_pico"],
            "max_consumo_hora_l": round(stats["max_consumo_hora"], 1),
            "registros": stats["registros"],
        })
    day_stats.sort(key=lambda x: x["total_consumido_l"], reverse=True)
    ranking = day_stats[:top]
    for i, d in enumerate(ranking, 1):
        d["posicion"] = i
    return json.dumps({"top": top, "dias_analizados": len(day_stats), "ranking": ranking},
                       ensure_ascii=False)


@mcp.tool()
def start_live_monitor() -> str:
    """
    Inicia el monitor en vivo del estanque/acumulador en la interfaz.
    Muestra las lecturas de agua (litros, porcentaje, distancia) en tiempo real
    a medida que llegan por MQTT. Úsalo cuando el usuario pida monitorear,
    ver en tiempo real, activar el live, o similar.
    """
    return "Monitor en vivo iniciado. Las lecturas aparecerán en pantalla automáticamente."


@mcp.tool()
def stop_live_monitor() -> str:
    """
    Detiene el monitor en vivo del estanque/acumulador.
    Úsalo cuando el usuario pida detener, parar o desactivar el monitor en vivo.
    """
    return "Monitor en vivo detenido."


@mcp.custom_route("/live", methods=["GET"])
async def live_stream(request):
    """SSE endpoint: emite la última lectura MQTT cada vez que cambia."""
    import asyncio as _asyncio
    from sse_starlette.sse import EventSourceResponse

    async def _generator():
        last_version = -1
        while True:
            if _reading_version != last_version and _ultima_lectura is not None:
                last_version = _reading_version
                yield {"data": json.dumps(_ultima_lectura)}
            else:
                yield {"comment": "keepalive"}
            await _asyncio.sleep(1.0)

    return EventSourceResponse(_generator())


if __name__ == "__main__":
    mcp.run(transport="stdio")
