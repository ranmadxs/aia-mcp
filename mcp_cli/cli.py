"""Entry point para ejecutar servidores MCP."""

import logging
import multiprocessing
import os
import sys

# Registro: nombre -> (módulo, atributo FastMCP)
SERVERS: dict[str, tuple[str, str]] = {
    "temperatura": ("temperatura.server", "mcp"),
    "wahapedia": ("wahapedia.server", "mcp"),
}

# Puerto HTTP por servidor
SERVER_PORTS: dict[str, int] = {
    "temperatura": 8001,
    "wahapedia": 8002,
}

# Default HTTP
DEFAULT_HTTP_HOST = "0.0.0.0"


def _run_http_server(server_name: str) -> None:
    """Ejecuta un servidor HTTP en el proceso actual (para multiprocessing)."""
    os.environ.setdefault("FASTMCP_HOST", DEFAULT_HTTP_HOST)
    os.environ["FASTMCP_PORT"] = str(SERVER_PORTS[server_name])
    os.environ.setdefault("FASTMCP_LOG_LEVEL", "DEBUG")
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    module_name, attr = SERVERS[server_name]
    module = __import__(module_name, fromlist=[attr])
    mcp = getattr(module, attr)
    import uvicorn
    from mcp_cli.logging_middleware import RequestLoggingMiddleware
    app = mcp.streamable_http_app()
    app = RequestLoggingMiddleware(app)
    uvicorn.run(
        app,
        host=os.environ.get("FASTMCP_HOST", DEFAULT_HTTP_HOST),
        port=SERVER_PORTS[server_name],
        log_level="debug",
    )


def main() -> None:
    args = sys.argv[1:]
    use_http = "--http" in args or "-H" in args
    if use_http:
        args = [a for a in args if a not in ("--http", "-H")]
        server_name = (args[0] if args else "temperatura").lower()
        port = SERVER_PORTS.get(server_name, 8001)
        os.environ.setdefault("FASTMCP_HOST", DEFAULT_HTTP_HOST)
        os.environ.setdefault("FASTMCP_PORT", str(port))

    server_name = (args[0] if args else "temperatura").lower()

    if server_name in ("-h", "--help", "help"):
        print("Uso: mcp [servidor|all] [--http]")
        print("\n  --http, -H    Ejecutar por HTTP (streamable-http) en lugar de stdio")
        print("                Por defecto: 0.0.0.0:8001, endpoint /mcp")
        print("\n  all          Con --http, ejecuta todos los servidores en paralelo")
        print("\nServidores disponibles:")
        for name in SERVERS:
            print(f"  {name}")
        sys.exit(0)

    if server_name in ("-l", "--list", "list"):
        for name in SERVERS:
            print(name)
        sys.exit(0)

    if server_name == "all":
        if not use_http:
            print("Error: 'all' requiere --http para ejecutar varios servidores.", file=sys.stderr)
            sys.exit(1)
        procs = []
        for name in SERVERS:
            p = multiprocessing.Process(target=_run_http_server, args=(name,))
            p.start()
            procs.append((name, p))
        print(f"MCP HTTP: {', '.join(f'{n} :{SERVER_PORTS[n]}' for n in SERVERS)}")
        print("Ctrl+C para detener todos")
        try:
            for _, p in procs:
                p.join()
        except KeyboardInterrupt:
            for _, p in procs:
                p.terminate()
            sys.exit(0)
        sys.exit(0)

    if server_name not in SERVERS:
        print(f"Error: servidor '{server_name}' no encontrado.", file=sys.stderr)
        print("Servidores disponibles:", ", ".join(SERVERS), file=sys.stderr)
        sys.exit(1)

    module_name, attr = SERVERS[server_name]
    module = __import__(module_name, fromlist=[attr])
    mcp = getattr(module, attr)

    if use_http:
        port = os.environ.get("FASTMCP_PORT", "8001")
        print(f"MCP HTTP en http://{DEFAULT_HTTP_HOST}:{port}/mcp")
        # DEBUG logs: URI y request body
        os.environ.setdefault("FASTMCP_LOG_LEVEL", "DEBUG")
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(levelname)s %(name)s: %(message)s",
            stream=sys.stderr,
            force=True,
        )
        import uvicorn

        from mcp_cli.logging_middleware import RequestLoggingMiddleware

        app = mcp.streamable_http_app()
        app = RequestLoggingMiddleware(app)
        uvicorn.run(
            app,
            host=os.environ.get("FASTMCP_HOST", DEFAULT_HTTP_HOST),
            port=int(port),
            log_level="debug",
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
