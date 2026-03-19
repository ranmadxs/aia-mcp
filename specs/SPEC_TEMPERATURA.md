# Spec: Servidor MCP para Temperatura

## Contexto

- **amanda-IA** (proyecto hermano): contiene el agente aia que consume las tools.
- **aia-mcp** (este proyecto): servidor MCP que expone las herramientas que el agente necesita.

---

## Objetivo

Crear un servidor MCP (Model Context Protocol) que exponga una herramienta `get_temperature` para consultar la temperatura. Este servidor será consumido por el agente aia.

---

## Estructura del proyecto aia-mcp

```
aia-mcp/
├── README.md
├── pyproject.toml
├── specs/
│   └── SPEC_TEMPERATURA.md    # Este archivo
└── temperatura/
    ├── __init__.py
    └── server.py              # Servidor MCP - get_temperature
```

**Regla:** Cada servidor MCP debe vivir en su propio directorio dentro de `aia-mcp/`. Un archivo principal por servidor.

---

## Especificación del tool `get_temperature`

### Nombre
`get_temperature`

### Descripción
Obtiene la temperatura actual de una ciudad. Por ahora retorna valores simulados.

### Parámetros (JSON Schema)
```json
{
  "type": "object",
  "properties": {
    "city": {
      "type": "string",
      "description": "Nombre de la ciudad"
    }
  }
}
```
- `city` es opcional. Si está vacío o no se envía, retornar temperatura genérica.

### Valores de retorno (simulados)
| Ciudad        | Temperatura |
|---------------|-------------|
| santiago      | 22°C        |
| buenos aires  | 18°C        |
| lima          | 24°C        |
| bogotá        | 19°C        |
| madrid        | 16°C        |
| new york      | 14°C        |
| londres       | 12°C        |
| tokio         | 20°C        |

Si la ciudad no está en la lista: `"Temperatura simulada: 21°C (ciudad 'X' no en base)"`  
Si no se pasa ciudad: `"Temperatura simulada: 21°C"`

### Tipo de retorno
String (texto plano).

---

## Formato MCP esperado

El servidor debe implementar el protocolo MCP estándar. Herramientas expuestas vía `tools/list` y `tools/call`.

### Tool definition (para tools/list)
```json
{
  "name": "get_temperature",
  "description": "Obtiene la temperatura actual de una ciudad. Retorna valores simulados.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "city": {
        "type": "string",
        "description": "Nombre de la ciudad (opcional)"
      }
    }
  }
}
```

### Tool call (para tools/call)
- **name:** `get_temperature`
- **arguments:** `{ "city": "santiago" }` (o `{}` si no hay ciudad)

---

## Stack técnico

- **Lenguaje:** Python 3.11+
- **Librería MCP:** `mcp` (PyPI: `pip install mcp`)
- **API recomendada:** FastMCP (decorador `@mcp.tool()`)
- **Transporte:** stdio (`mcp.run(transport="stdio")`)

### Ejemplo mínimo FastMCP
```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("temperatura")

@mcp.tool()
def get_temperature(city: str = "") -> str:
    """Obtiene la temperatura actual de una ciudad."""
    # implementación
    return "22°C"

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

---

## Cómo ejecutar el servidor

Desde el directorio `aia-mcp/`:
```bash
cd aia-mcp
poetry install
poetry run mcp temperatura
```

El servidor debe escuchar en stdio para recibir mensajes JSON-RPC del protocolo MCP.

---

## Referencias

- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP Python SDK Docs](https://modelcontextprotocol.github.io/python-sdk/)
- [FastMCP Server Guide](https://anish-natekar.github.io/mcp_docs/server-guide.html)
- [MCP Specification](https://spec.modelcontextprotocol.io/)

---

## Checklist para la IA que implemente

- [ ] Crear `aia-mcp/temperatura/server.py`
- [ ] Implementar `get_temperature` como tool MCP
- [ ] Usar la librería `mcp` oficial
- [ ] Valores simulados según la tabla
- [ ] Servidor ejecutable por stdio
- [ ] Añadir `mcp` a dependencias (pyproject.toml de aia-mcp)
- [ ] Documentar en `aia-mcp/README.md` cómo ejecutar y conectar con el agente
