"""Entry point para ejecutar servidores MCP."""

import logging
import multiprocessing
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Registro: nombre -> (módulo, atributo FastMCP)
SERVERS: dict[str, tuple[str, str]] = {
    "temperatura": ("temperatura.server", "mcp"),
    "wahapedia": ("wahapedia.server", "mcp"),
    "monitor": ("monitor.server", "mcp"),
    "shell": ("shell.server", "mcp"),
    "airbnb": ("airbnb.server", "mcp"),
    "charts": ("charts.server", "mcp"),
    "email": ("mcp_email.server", "mcp"),
    "mangadex": ("mangadex.server", "mcp"),
}

# Puerto HTTP por servidor
SERVER_PORTS: dict[str, int] = {
    "temperatura": 8001,
    "wahapedia": 8002,
    "monitor": 8003,
    "shell": 8005,
    "airbnb": 8006,
    "charts": 8007,
    "email": 8008,
    "mangadex": 8009,
}

# Default HTTP
DEFAULT_HTTP_HOST = "0.0.0.0"

# Directorio de logs
_LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


def _read_version() -> str:
    try:
        import tomllib
        toml_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("tool", {}).get("poetry", {}).get("version", "?")
    except Exception:
        return "?"


def _setup_logging(server_name: str) -> None:
    """Configura logging a consola + archivo rotado por día."""
    _LOGS_DIR.mkdir(exist_ok=True)
    log_file = _LOGS_DIR / f"{server_name}_{time.strftime('%Y%m%d')}.log"

    fmt = logging.Formatter(
        f"%(asctime)s [{server_name}] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(fmt)
    console_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Silenciar loggers muy verbosos
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logging.info("aia-mcp v%s  |  %s  |  logs → %s", _read_version(), server_name, log_file)


def _run_http_server(server_name: str) -> None:
    """Ejecuta un servidor HTTP en el proceso actual (para multiprocessing)."""
    try:
        os.environ.setdefault("FASTMCP_HOST", DEFAULT_HTTP_HOST)
        os.environ["FASTMCP_PORT"] = str(SERVER_PORTS[server_name])
        os.environ.setdefault("FASTMCP_LOG_LEVEL", "INFO")
        _setup_logging(server_name)
        module_name, attr = SERVERS[server_name]
        module = __import__(module_name, fromlist=[attr])
        mcp = getattr(module, attr)
        import uvicorn
        from mcp_cli.logging_middleware import RequestLoggingMiddleware
        app = mcp.streamable_http_app()
        app = RequestLoggingMiddleware(app, server_name=server_name)
        uvicorn.run(
            app,
            host=os.environ.get("FASTMCP_HOST", DEFAULT_HTTP_HOST),
            port=SERVER_PORTS[server_name],
            log_level="warning",
        )
    except Exception as e:
        logging.exception("Error fatal en %s: %s", server_name, e)
        raise


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
            print(f"Iniciando {name} :{SERVER_PORTS[name]}...", flush=True)
            p = multiprocessing.Process(target=_run_http_server, args=(name,))
            p.start()
            procs.append((name, p))
            time.sleep(0.5)  # Evita conflictos al bindear puertos
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
        _setup_logging(server_name)
        logging.info("MCP HTTP en http://%s:%s/mcp", DEFAULT_HTTP_HOST, port)
        import uvicorn

        from mcp_cli.logging_middleware import RequestLoggingMiddleware

        app = mcp.streamable_http_app()
        app = RequestLoggingMiddleware(app, server_name=server_name)
        uvicorn.run(
            app,
            host=os.environ.get("FASTMCP_HOST", DEFAULT_HTTP_HOST),
            port=int(port),
            log_level="warning",
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()