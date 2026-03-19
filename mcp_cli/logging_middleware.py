"""Middleware para log DEBUG de URI y request en aia-mcp."""

import logging

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("mcp_cli")


class RequestLoggingMiddleware:
    """ASGI middleware que loguea en DEBUG: method, path, query, headers y body."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        query = scope.get("query_string", b"").decode("utf-8", errors="replace")
        uri = f"{path}" + (f"?{query}" if query else "")

        logger.debug(">>> Request URI: %s %s", method, uri)

        # Leer body (solo para POST/PUT/PATCH)
        if method in ("POST", "PUT", "PATCH"):
            body_chunks: list[bytes] = []

            async def receive_with_log() -> dict:
                msg = await receive()
                if msg["type"] == "http.request":
                    body_chunks.append(msg.get("body", b""))
                    if not msg.get("more_body", False):
                        body = b"".join(body_chunks)
                        try:
                            if body:
                                body_str = body.decode("utf-8", errors="replace")
                                preview = body_str[:2000] + ("..." if len(body_str) > 2000 else "")
                                logger.debug(">>> Request body: %s", preview)
                        except Exception:
                            logger.debug(">>> Request body: <binary %d bytes>", len(body))
                return msg

            await self.app(scope, receive_with_log, send)
        else:
            await self.app(scope, receive, send)
