"""Servidor MCP Shell — ejecuta comandos en el proyecto para modo dev."""

import os
import subprocess

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "shell",
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("FASTMCP_PORT", "8005")),
)


@mcp.tool()
def run_command(command: str, cwd: str | None = None) -> str:
    """
    Ejecuta un comando shell y retorna stdout + stderr combinados.

    Args:
        command: Comando a ejecutar. Ejemplos:
                 "pytest tests/ -q --no-header -m 'not ollama'"
                 "git diff HEAD~1"
                 "git log --oneline -10"
                 "grep -rn 'def process' amanda_ia/"
        cwd: Directorio de trabajo absoluto. Si se omite, usa el directorio actual.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=60,
        )
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output.strip() or "(sin salida)"
    except subprocess.TimeoutExpired:
        return "[timeout: el comando tardó más de 60s]"
    except Exception as e:
        return f"[error: {e}]"
