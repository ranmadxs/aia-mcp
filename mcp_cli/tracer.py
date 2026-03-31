"""Tracer distribuido para MCP servers.

Cuando un MCP server llama a otro MCP internamente, puede reportar el span
a amanda-IA vía POST /trace para que aparezca en el MCP Stream como traza hija.

Uso:
    from mcp_cli.tracer import trace_span
    import time

    t0 = time.monotonic()
    result = otra_tool(args)
    trace_span(
        server="mongodb",
        tool="find",
        args={"collection": "rentabilidad"},
        elapsed_ms=(time.monotonic() - t0) * 1000,
        parent_tool="get_rentabilidad_mes",   # opcional
    )
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_AIA_AGENT_API = os.environ.get("AIA_AGENT_API", "http://127.0.0.1:8081")


def trace_span(
    server: str,
    tool: str,
    args: dict[str, Any] | None = None,
    elapsed_ms: float | None = None,
    parent_tool: str | None = None,
) -> None:
    """
    Reporta un span de traza distribuida al agent_api de amanda-IA.

    El span aparece en el MCP Stream como una entrada TRACE>> anidada bajo
    la tool padre, con tiempo de ejecución y nombre del servidor hijo.

    Args:
        server:      Nombre del servidor MCP hijo (ej: "mongodb")
        tool:        Nombre de la tool llamada (ej: "find")
        args:        Argumentos pasados a la tool (opcional)
        elapsed_ms:  Tiempo de ejecución en ms (opcional)
        parent_tool: Nombre de la tool padre que originó esta llamada (opcional)
    """
    try:
        import httpx
        payload: dict[str, Any] = {"server": server, "tool": tool}
        if args:
            payload["args"] = args
        if elapsed_ms is not None:
            payload["elapsed_ms"] = elapsed_ms
        if parent_tool:
            payload["parent_tool"] = parent_tool
        httpx.post(f"{_AIA_AGENT_API}/trace", json=payload, timeout=1.0)
    except Exception as e:
        logger.debug("trace_span failed: %s", e)
