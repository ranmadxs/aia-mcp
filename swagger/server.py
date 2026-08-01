"""Swagger UI documentation server for all MCP servers."""

import os
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
import uvicorn

# Import all MCP servers for documentation
from temperatura.server import mcp as temperatura_mcp
from wahapedia.server import mcp as wahapedia_mcp
from monitor.server import mcp as monitor_mcp
from airbnb.server import mcp as airbnb_mcp
from charts.server import mcp as charts_mcp
from mcp_email.server import mcp as email_mcp
from mangadex.server import mcp as mangadex_mcp

# Import custom OpenAPI generator
from .openapi_generator import generate_openapi_like

_servers = {
    "temperatura": temperatura_mcp,
    "wahapedia": wahapedia_mcp,
    "monitor": monitor_mcp,
    "airbnb": airbnb_mcp,
    "charts": charts_mcp,
    "email": email_mcp,
    "mangadex": mangadex_mcp,
}

HOST = os.environ.get("FASTMCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("SWAGGER_PORT", "8010"))


def _serialize_result(result: Any) -> Any:
    """Serialize MCP tool/prompt/resource results to JSON-serializable format."""
    if isinstance(result, (str, int, float, bool, type(None))):
        return result
    if isinstance(result, dict):
        return result
    if isinstance(result, (list, tuple)):
        return [item.model_dump() if hasattr(item, "model_dump") else str(item) for item in result]
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return str(result)


def create_app() -> FastAPI:
    """Create FastAPI app with swagger docs for all MCP servers."""
    app = FastAPI(
        title="MCP Swagger UI",
        docs_url="/",
        port=PORT
    )

    @app.get("/openapi.json/{server_name}")
    async def get_openapi_spec(server_name: str) -> Dict[str, Any]:
        if server_name not in _servers:
            raise HTTPException(status_code=404, detail=f"Server '{server_name}' not found")
        mcp_server = _servers[server_name]
        return await generate_openapi_like(mcp_server, title=f"MCP {server_name} API", version="1.0.0")

    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui_html():
        server_options = "".join(
            f'<option value="{name}">{name}</option>'
            for name in _servers.keys()
        )
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <link rel="stylesheet" type="text/css" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
            <title>MCP Swagger UI</title>
        </head>
        <body>
            <div id="swagger-ui"></div>
            <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
            <script>
                const servers = {{
                    {", ".join(f'"{name}": "/openapi.json/{name}"' for name in _servers.keys())}
                }};
                
                const urlParams = new URLSearchParams(window.location.search);
                const initialServer = urlParams.get('server') || 'temperatura';
                const initialUrl = servers[initialServer] || servers['temperatura'];
                
                const ui = SwaggerUIBundle({{
                    url: initialUrl,
                    dom_id: '#swagger-ui',
                    presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
                    layout: "BaseLayout"
                }});
                
                // Add server selector
                const select = document.createElement('select');
                select.innerHTML = '<option value="">Select server...</option>' + '{server_options}';
                select.style.cssText = 'position: fixed; top: 10px; right: 10px; z-index: 1000; padding: 5px;';
                select.value = initialServer;
                select.onchange = function() {{
                    if (this.value) {{
                        const newUrl = new URL(window.location);
                        newUrl.searchParams.set('server', this.value);
                        window.location.href = newUrl.toString();
                    }}
                }};
                document.body.appendChild(select);
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

    for name, server in _servers.items():
        # Add invocation endpoints
        _add_invocation_routes(app, server, prefix=f"/{name}")

    return app


def _add_invocation_routes(app: FastAPI, mcp_server, prefix: str = "") -> None:
    """Add endpoints to invoke MCP tools, prompts, and resources."""
    # Tool invocation endpoints
    @app.post(f"{prefix}/tool/{{tool_name}}", response_class=JSONResponse)
    async def invoke_tool(tool_name: str, arguments: Dict[str, Any] = {}) -> JSONResponse:
        try:
            result = mcp_server.call_tool(tool_name, arguments)
            return JSONResponse(_serialize_result(result))
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Prompt invocation endpoints
    @app.post(f"{prefix}/prompt/{{prompt_name}}", response_class=JSONResponse)
    async def invoke_prompt(prompt_name: str, arguments: Dict[str, Any] = None) -> JSONResponse:
        try:
            result = mcp_server.get_prompt(prompt_name, arguments or {})
            return JSONResponse(_serialize_result(result))
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Resource read endpoints
    @app.get(f"{prefix}/resource/{{resource_uri:path}}", response_class=JSONResponse)
    async def read_resource(resource_uri: str) -> JSONResponse:
        try:
            result = mcp_server.read_resource(f"resource://{resource_uri}")
            return JSONResponse(_serialize_result(result))
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))


def run_server():
    """Run the swagger documentation server."""
    app = create_app()
    uvicorn.run(app, host=HOST, port=PORT)