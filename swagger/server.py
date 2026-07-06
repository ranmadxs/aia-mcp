"""Swagger UI documentation server for all MCP servers."""

import os

from fastapi import FastAPI
from mcp_swagger_ui import mount_mcp_docs
import uvicorn

# Import all MCP servers for documentation
from temperatura.server import mcp as temperatura_mcp
from wahapedia.server import mcp as wahapedia_mcp
from monitor.server import mcp as monitor_mcp
from shell.server import mcp as shell_mcp
from airbnb.server import mcp as airbnb_mcp
from charts.server import mcp as charts_mcp
from mcp_email.server import mcp as email_mcp
from mangadex.server import mcp as mangadex_mcp

_servers = {
    "temperatura": temperatura_mcp,
    "wahapedia": wahapedia_mcp,
    "monitor": monitor_mcp,
    "shell": shell_mcp,
    "airbnb": airbnb_mcp,
    "charts": charts_mcp,
    "email": email_mcp,
    "mangadex": mangadex_mcp,
}

HOST = os.environ.get("FASTMCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("SWAGGER_PORT", "8010"))


def create_app() -> FastAPI:
    """Create FastAPI app with swagger docs for all MCP servers."""
    app = FastAPI(title="MCP Swagger UI", docs_url="/", port=PORT)

    for name, server in _servers.items():
        mount_mcp_docs(app, server, mount_path=f"/{name}", title=f"MCP {name}")

    return app


def run_server():
    """Run the swagger documentation server."""
    app = create_app()
    uvicorn.run(app, host=HOST, port=PORT)