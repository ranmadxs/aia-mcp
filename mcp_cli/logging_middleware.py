"""Middleware para log: nombre MCP + tool + parámetros ejecutados."""

import json
import logging

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("mcp_cli")


class RequestLoggingMiddleware:
    """ASGI middleware que loguea solo: MCP name, tool name y params."""

    def __init__(self, app: ASGIApp, server_name: str = "mcp") -> None:
        self.app = app
        self.server_name = server_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        if method not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        body_chunks: list[bytes] = []

        async def receive_with_log() -> dict:
            msg = await receive()
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
                                logger.info("%s: %s %s", self.server_name, tool_name, args)
                            else:
                                logger.info("%s: %s", self.server_name, m or "request")
                        except (json.JSONDecodeError, TypeError):
                            logger.info("%s: <body %d bytes>", self.server_name, len(body))
            return msg

        await self.app(scope, receive_with_log, send)
