"""Entry point para ejecutar servidores MCP."""

import os
import sys

# Registro: nombre -> (módulo, atributo FastMCP)
SERVERS: dict[str, tuple[str, str]] = {
    "temperatura": ("temperatura.server", "mcp"),
}

# Default HTTP
DEFAULT_HTTP_HOST = "0.0.0.0"
DEFAULT_HTTP_PORT = 8001


def main() -> None:
    args = sys.argv[1:]
    use_http = "--http" in args or "-H" in args
    if use_http:
        args = [a for a in args if a not in ("--http", "-H")]
        os.environ.setdefault("FASTMCP_HOST", DEFAULT_HTTP_HOST)
        os.environ.setdefault("FASTMCP_PORT", str(DEFAULT_HTTP_PORT))

    server_name = (args[0] if args else "temperatura").lower()

    if server_name in ("-h", "--help", "help"):
        print("Uso: mcp [servidor] [--http]")
        print("\n  --http, -H    Ejecutar por HTTP (streamable-http) en lugar de stdio")
        print("                Por defecto: 0.0.0.0:8001, endpoint /mcp")
        print("\nServidores disponibles:")
        for name in SERVERS:
            print(f"  {name}")
        sys.exit(0)

    if server_name in ("-l", "--list", "list"):
        for name in SERVERS:
            print(name)
        sys.exit(0)

    if server_name not in SERVERS:
        print(f"Error: servidor '{server_name}' no encontrado.", file=sys.stderr)
        print("Servidores disponibles:", ", ".join(SERVERS), file=sys.stderr)
        sys.exit(1)

    module_name, attr = SERVERS[server_name]
    module = __import__(module_name, fromlist=[attr])
    mcp = getattr(module, attr)

    if use_http:
        print(f"MCP HTTP en http://{DEFAULT_HTTP_HOST}:{DEFAULT_HTTP_PORT}/mcp")
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
