"""Servidor MCP Temperatura — Open-Meteo (cobertura global, sin API key).

Tools:
  search_location(query)               → geocoding, retorna lat/lon
  get_current_conditions(latitude, longitude) → condiciones actuales
  get_forecast(latitude, longitude, days)     → pronóstico diario
  get_alerts(latitude, longitude)             → alertas (stub: Open-Meteo no las tiene)
  check_service_status()                      → health check
"""

import json
import os
from pathlib import Path

import httpx
import tomllib

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "temperatura",
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("FASTMCP_PORT", "8001")),
)

_pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
if _pyproject.exists():
    with open(_pyproject, "rb") as f:
        mcp._mcp_server.version = tomllib.load(f)["tool"]["poetry"]["version"]

_GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
_WX_URL  = "https://api.open-meteo.com/v1/forecast"

_WMO_CODES: dict[int, str] = {
    0: "despejado", 1: "principalmente despejado", 2: "parcialmente nublado", 3: "nublado",
    45: "niebla", 48: "niebla con escarcha",
    51: "llovizna ligera", 53: "llovizna moderada", 55: "llovizna densa",
    61: "lluvia ligera", 63: "lluvia moderada", 65: "lluvia fuerte",
    71: "nieve ligera", 73: "nieve moderada", 75: "nieve fuerte",
    80: "chubascos ligeros", 81: "chubascos moderados", 82: "chubascos violentos",
    95: "tormenta", 96: "tormenta con granizo", 99: "tormenta con granizo fuerte",
}


def _wmo(code: int) -> str:
    return _WMO_CODES.get(code, f"código {code}")


def _geo(query: str) -> list[dict]:
    resp = httpx.get(_GEO_URL, params={"name": query, "count": 5, "language": "es", "format": "json"}, timeout=10)
    resp.raise_for_status()
    return resp.json().get("results", [])


@mcp.tool()
def search_location(query: str) -> str:
    """Busca una ciudad por nombre y retorna sus coordenadas (lat/lon)."""
    try:
        results = _geo(query)
    except Exception as e:
        return f"Error buscando '{query}': {e}"

    if not results:
        return f"No se encontraron resultados para '{query}'."

    lines = [f"**Query:** \"{query}\"", f"**Found:** {len(results)} locations\n"]
    for i, r in enumerate(results, 1):
        name    = r.get("name", "?")
        country = r.get("country", "")
        admin1  = r.get("admin1", "")
        lat     = r.get("latitude", 0)
        lon     = r.get("longitude", 0)
        tz      = r.get("timezone", "")
        full    = ", ".join(filter(None, [name, admin1, country]))
        lines.append(f"## {i}. {name}")
        lines.append(f"**Full Name:** {full}")
        lines.append(f"**Coordinates:** {lat}, {lon}")
        lines.append(f"**Timezone:** {tz}\n")

    return "\n".join(lines)


@mcp.tool()
def get_current_conditions(latitude: float, longitude: float) -> str:
    """Obtiene las condiciones meteorológicas actuales para unas coordenadas."""
    params = {
        "latitude":  latitude,
        "longitude": longitude,
        "current": ",".join([
            "temperature_2m", "apparent_temperature", "relative_humidity_2m",
            "precipitation", "weather_code", "wind_speed_10m", "wind_direction_10m",
            "surface_pressure", "cloud_cover", "is_day",
        ]),
        "wind_speed_unit": "kmh",
        "timezone": "auto",
    }
    try:
        resp = httpx.get(_WX_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Error obteniendo condiciones para ({latitude}, {longitude}): {e}"

    cur  = data.get("current", {})
    tz   = data.get("timezone", "UTC")
    time = cur.get("time", "?")

    temp     = cur.get("temperature_2m", "?")
    feels    = cur.get("apparent_temperature", "?")
    humidity = cur.get("relative_humidity_2m", "?")
    precip   = cur.get("precipitation", 0)
    wcode    = cur.get("weather_code", 0)
    wind_sp  = cur.get("wind_speed_10m", "?")
    wind_dir = cur.get("wind_direction_10m", "?")
    pressure = cur.get("surface_pressure", "?")
    cloud    = cur.get("cloud_cover", "?")
    is_day   = cur.get("is_day", 1)
    day_str  = "día" if is_day else "noche"

    desc = _wmo(wcode)

    lines = [
        f"## Condiciones Actuales",
        f"**Ubicación:** {latitude:.4f}, {longitude:.4f} ({tz})",
        f"**Hora:** {time} ({day_str})",
        f"**Clima:** {desc}",
        f"**Temperatura:** {temp}°C (sensación {feels}°C)",
        f"**Humedad:** {humidity}%",
        f"**Precipitación:** {precip} mm",
        f"**Viento:** {wind_sp} km/h dirección {wind_dir}°",
        f"**Presión:** {pressure} hPa",
        f"**Nubosidad:** {cloud}%",
    ]
    return "\n".join(lines)


@mcp.tool()
def get_forecast(latitude: float, longitude: float, days: int = 7) -> str:
    """Obtiene el pronóstico del tiempo para los próximos N días (máx 16)."""
    days = min(max(days, 1), 16)
    params = {
        "latitude":  latitude,
        "longitude": longitude,
        "daily": ",".join([
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "precipitation_sum", "wind_speed_10m_max", "precipitation_probability_max",
        ]),
        "wind_speed_unit": "kmh",
        "timezone": "auto",
        "forecast_days": days,
    }
    try:
        resp = httpx.get(_WX_URL, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Error obteniendo pronóstico para ({latitude}, {longitude}): {e}"

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    codes = daily.get("weather_code", [])
    t_max = daily.get("temperature_2m_max", [])
    t_min = daily.get("temperature_2m_min", [])
    prec  = daily.get("precipitation_sum", [])
    wind  = daily.get("wind_speed_10m_max", [])
    prob  = daily.get("precipitation_probability_max", [])
    tz    = data.get("timezone", "UTC")

    lines = [f"## Pronóstico {days} días — {latitude:.4f}, {longitude:.4f} ({tz})\n"]
    for i, date in enumerate(dates):
        desc   = _wmo(codes[i]) if i < len(codes) else "?"
        mx     = t_max[i] if i < len(t_max) else "?"
        mn     = t_min[i] if i < len(t_min) else "?"
        pr     = prec[i] if i < len(prec) else 0
        wn     = wind[i] if i < len(wind) else "?"
        pp     = prob[i] if i < len(prob) else "?"
        lines.append(
            f"**{date}** — {desc} | {mn}°C–{mx}°C | "
            f"Lluvia: {pr}mm ({pp}%) | Viento: {wn} km/h"
        )
    return "\n".join(lines)


@mcp.tool()
def get_alerts(latitude: float, longitude: float) -> str:
    """Retorna alertas meteorológicas activas (Open-Meteo no provee alertas oficiales)."""
    return (
        "Open-Meteo no incluye alertas meteorológicas oficiales. "
        "Para alertas en Chile consulta: https://www.meteochile.gob.cl/PortalDMC-web/index.xhtml"
    )


@mcp.tool()
def check_service_status() -> str:
    """Verifica que el servicio de clima (Open-Meteo) esté disponible."""
    try:
        resp = httpx.get("https://api.open-meteo.com/v1/forecast",
                         params={"latitude": -33.45, "longitude": -70.67,
                                 "current": "temperature_2m", "timezone": "auto"},
                         timeout=8)
        resp.raise_for_status()
        temp = resp.json().get("current", {}).get("temperature_2m", "?")
        return f"OK — Open-Meteo disponible. Santiago: {temp}°C"
    except Exception as e:
        return f"ERROR — Open-Meteo no responde: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
