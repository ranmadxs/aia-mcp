"""Servidor MCP Temperatura - expone get_temperature para consultar temperatura por ciudad."""

import os
from pathlib import Path

import tomllib

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "temperatura",
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("FASTMCP_PORT", "8001")),
)

# Versión desde pyproject.toml para serverInfo
_pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
if _pyproject.exists():
    with open(_pyproject, "rb") as f:
        mcp._mcp_server.version = tomllib.load(f)["tool"]["poetry"]["version"]

# Valores simulados según SPEC_TEMPERATURA.md
_TEMPERATURAS: dict[str, str] = {
    "santiago": "22°C",
    "buenos aires": "18°C",
    "lima": "24°C",
    "bogotá": "19°C",
    "bogota": "19°C",
    "madrid": "16°C",
    "new york": "14°C",
    "londres": "12°C",
    "tokio": "20°C",
}


@mcp.tool()
def get_temperature(city: str = "") -> str:
    """Obtiene la temperatura actual de una ciudad. Retorna valores simulados."""
    city_normalized = city.strip().lower() if city else ""

    if not city_normalized:
        return "Temperatura simulada: 21°C"

    if city_normalized in _TEMPERATURAS:
        return _TEMPERATURAS[city_normalized]

    return f"Temperatura simulada: 21°C (ciudad '{city.strip()}' no en base)"


if __name__ == "__main__":
    mcp.run(transport="stdio")
