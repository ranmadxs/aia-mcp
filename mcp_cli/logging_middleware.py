"""Middleware para log: nombre MCP + tool + parámetros + status de respuesta."""

import json
import logging
import time

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("mcp_cli")


class RequestLoggingMiddleware:
    """ASGI middleware que loguea tool calls con params y status HTTP."""

    def __init__(self, app: ASGIApp, server_name: str = "mcp") -> None:
        self.app = app
        self.server_name = server_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        t0 = time.monotonic()

        # Para POST capturamos el body y lo parseamos
        tool_label = ""
        if method in ("POST", "PUT", "PATCH"):
            body_chunks: list[bytes] = []
            _orig_receive = receive  # capturar antes de reasignar

            async def receive_with_capture() -> dict:
                msg = await _orig_receive()
                if msg["type"] == "http.request":
                    body_chunks.append(msg.get("body", b""))
                    if not msg.get("more_body", False):
                        body = b"".join(body_chunks)
                        if body:
                            try:
                                data = json.loads(body.decode("utf-8", errors="replace"))
                                m = data.get("method", "")
                                params = data.get("params", {})
                                if m == "tools/call":
                                    tool_name = params.get("name", "?")
                                    args = params.get("arguments", {})
                                    nonlocal tool_label
                                    tool_label = f"{tool_name} {args}"
                                    logger.info("→ %s", tool_label)
                                else:
                                    logger.debug("→ %s", m or "request")
                            except (json.JSONDecodeError, TypeError):
                                pass
                return msg

            receive = receive_with_capture

        # Capturamos el status de la respuesta
        status_code = 0

        async def send_with_status(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            await send(message)

        await self.app(scope, receive, send_with_status)

        elapsed = (time.monotonic() - t0) * 1000
        if method in ("POST", "PUT", "PATCH"):
            label = tool_label or path
            if status_code >= 400:
                logger.error("← %s %s  %.0fms", status_code, label, elapsed)
            else:
                logger.info("← %s %s  %.0fms", status_code, label, elapsed)
        elif status_code >= 400:
            logger.warning("← %s %s %s  %.0fms", status_code, method, path, elapsed)
