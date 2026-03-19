"""Servidor MCP Tinaja - lectura en tiempo real desde MQTT o cálculo manual."""

import json
import os
import threading
import time
from collections import deque
from pathlib import Path

import tomllib
from mcp.server.fastmcp import FastMCP

# MQTT (mismo formato que monitor_estanque.py)
try:
    import paho.mqtt.client as mqtt
    _HAS_MQTT = True
except ImportError:
    _HAS_MQTT = False

try:
    import urllib.request
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False

mcp = FastMCP(
    "tinaja",
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
    global _ultima_lectura, _lecturas_buffer
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
    Obtiene litros y porcentaje del acumulador/estanque en tiempo real.

    Usa MQTT (MQTT_HOST, MQTT_TOPIC_OUT) o TINAJA_ESTADO_URL como fallback.
    Si no hay datos, devuelve un cálculo de ejemplo con distance=50.
    """
    if _ultima_lectura:
        d = _ultima_lectura
        return (
            f"{d['litros']:.0f}L ({d['porcentaje']:.1f}%) - {d['estado']} | "
            f"distancia: {d['distancia']:.1f} cm"
        )

    # Fallback: API HTTP (cuando tomi-metric-collector está corriendo)
    url = os.environ.get("TINAJA_ESTADO_URL", "").strip()
    if url and _HAS_URLLIB:
        try:
            req = urllib.request.Request(url)
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            litros = data.get("litros") or 0
            pct = data.get("porcentaje") or 0
            estado = data.get("estado", "?")
            dist = data.get("distancia", "?")
            return f"{float(litros):.0f}L ({float(pct):.1f}%) - {estado} | distancia: {dist} cm"
        except Exception as e:
            err = str(e).lower()
            if "refused" in err or "111" in str(e) or "61" in str(e):
                return "Sin datos MQTT y monitor no disponible. Verifique MQTT (MQTT_HOST, MQTT_TOPIC_OUT) o tomi-metric-collector."
            return f"Error: {e}"

    if not _HAS_MQTT:
        return "paho-mqtt no instalado. Ejecute: poetry add paho-mqtt"
    # Fallback: cálculo de ejemplo para que el usuario siempre reciba una respuesta
    datos = _calcular_nivel(50)
    return (
        f"{datos['litros']:.0f}L ({datos['porcentaje']:.1f}%) - {datos['estado']} | "
        f"(ejemplo con distancia=50 cm; sin datos MQTT)"
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
