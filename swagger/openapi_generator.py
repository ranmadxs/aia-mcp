"""Custom OpenAPI generation with executable endpoints for MCP tools."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from .introspection import MCPItem, collect_all


def _schema_properties(schema: Dict[str, Any]) -> Dict[str, Any]:
    properties = schema.get("properties", {})
    if isinstance(properties, dict):
        return properties
    return {}


def _schema_required(schema: Dict[str, Any]) -> List[str]:
    required = schema.get("required", [])
    if isinstance(required, list):
        return [item for item in required if isinstance(item, str)]
    return []


def _example_value_from_schema(schema: Dict[str, Any]) -> Any:
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]

    schema_type = schema.get("type")
    if schema_type == "string":
        return "string"
    if schema_type == "integer":
        return 0
    if schema_type == "number":
        return 0
    if schema_type == "boolean":
        return False
    if schema_type == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            return [_example_value_from_schema(items)]
        return []
    if schema_type == "object":
        properties = _schema_properties(schema)
        return {
            name: _example_value_from_schema(value)
            for name, value in properties.items()
            if isinstance(value, dict)
        }

    return None


def _example_object_from_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    example: Dict[str, Any] = {}
    for name, value in _schema_properties(schema).items():
        if isinstance(value, dict):
            example[name] = _example_value_from_schema(value)
    return example


def _required_and_optional_text(schema: Dict[str, Any]) -> str:
    properties = _schema_properties(schema)
    if not properties:
        return "Arguments: none documented."

    required = _schema_required(schema)
    optional = [name for name in properties.keys() if name not in required]

    lines: List[str] = []
    if required:
        lines.append(f"Required arguments: {', '.join(required)}.")
    else:
        lines.append("Required arguments: none.")

    if optional:
        lines.append(f"Optional arguments: {', '.join(optional)}.")
    else:
        lines.append("Optional arguments: none.")

    return " ".join(lines)


def _field_requirements(properties: Dict[str, Any], required: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            continue
        rows.append(
            {
                "name": name,
                "required": name in required,
                "type": schema.get("type", "any"),
                "description": schema.get("description", ""),
                "default": schema.get("default"),
            }
        )
    return rows


def _operation_summary(kind: str, item: MCPItem) -> str:
    verb = {
        "tools": "Invoke tool",
        "prompts": "Invoke prompt",
        "resources": "Read resource",
    }.get(kind, "Inspect MCP item")
    return f"{verb} `{item.name}`"


def _operation_description(kind: str, item: MCPItem) -> str:
    base = item.description or f"{kind[:-1].capitalize()} definition."
    argument_notes = _required_and_optional_text(item.input_schema or {})
    return f"{base}\n\n{argument_notes}"


def _parameter_description(schema: Dict[str, Any]) -> str:
    parts: List[str] = []
    description = schema.get("description")
    if isinstance(description, str) and description.strip():
        parts.append(description.strip())

    schema_type = schema.get("type")
    if isinstance(schema_type, str) and schema_type:
        parts.append(f"Type: {schema_type}.")

    if "default" in schema:
        parts.append(f"Default: {schema['default']}.")

    return " ".join(parts)


def _responses(item: MCPItem) -> Dict[str, Any]:
    response_schema = item.output_schema or {"type": "object"}
    response_payload: Dict[str, Any] = {
        "description": "Success",
        "content": {
            "application/json": {
                "schema": response_schema,
            }
        },
    }
    example = _example_value_from_schema(response_schema)
    if example is not None:
        response_payload["content"]["application/json"]["example"] = example
    return {"200": response_payload}


def _build_route_names(registries: Dict[str, List[MCPItem]]) -> Dict[tuple[str, str], str]:
    name_counts = Counter(item.name for items in registries.values() for item in items)
    routes: Dict[tuple[str, str], str] = {}

    for kind in ("tools", "prompts", "resources"):
        for item in registries[kind]:
            if name_counts[item.name] == 1:
                routes[(kind, item.name)] = f"/{kind}s/{item.name}"
            else:
                routes[(kind, item.name)] = f"/{kind}s/{item.name}"

    return routes


def _request_body_schema(item: MCPItem) -> Dict[str, Any]:
    """Generate request body schema for POST endpoints."""
    properties = _schema_properties(item.input_schema or {})
    required = _schema_required(item.input_schema or {})

    return {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required if required else None,
                }
            }
        }
    }


async def generate_openapi_like(
    mcp: Any, title: str = "MCP API Docs", version: str = "1.0.0"
) -> Dict[str, Any]:
    registries = await collect_all(mcp)
    routes = _build_route_names(registries)
    paths: Dict[str, Any] = {}

    for kind in ("tools", "prompts", "resources"):
        for item in registries[kind]:
            # Tools and prompts use POST for invocation
            if kind in ("tools", "prompts"):
                route = routes[(kind, item.name)]
                operation: Dict[str, Any] = {
                    "summary": _operation_summary(kind, item),
                    "description": _operation_description(kind, item),
                    "operationId": f"{kind}_{item.name}".replace("-", "_").replace("/", "_"),
                    "tags": [kind],
                    "x-mcp-kind": item.category,
                    "x-mcp-metadata": item.metadata,
                    "x-mcp-argument-requirements": _field_requirements(
                        _schema_properties(item.input_schema or {}),
                        _schema_required(item.input_schema or {}),
                    ),
                    "requestBody": _request_body_schema(item),
                    "responses": _responses(item),
                }
                paths[route] = {"post": operation}
            else:
                # Resources use GET
                route = routes[(kind, item.name)]
                properties = _schema_properties(item.input_schema or {})
                required = _schema_required(item.input_schema or {})

                operation: Dict[str, Any] = {
                    "summary": _operation_summary(kind, item),
                    "description": _operation_description(kind, item),
                    "operationId": f"{kind}_{item.name}".replace("-", "_").replace("/", "_"),
                    "tags": [kind],
                    "x-mcp-kind": item.category,
                    "x-mcp-metadata": item.metadata,
                    "responses": _responses(item),
                }

                if properties:
                    operation["parameters"] = [
                        {
                            "name": name,
                            "in": "query",
                            "required": name in required,
                            "schema": schema if isinstance(schema, dict) else {"type": "string"},
                            "description": _parameter_description(schema)
                            if isinstance(schema, dict)
                            else "",
                            "example": _example_value_from_schema(schema)
                            if isinstance(schema, dict)
                            else None,
                        }
                        for name, schema in properties.items()
                    ]

                paths[route] = {"get": operation}

    return {
        "openapi": "3.1.0",
        "info": {
            "title": title,
            "version": version,
            "description": "Auto-generated Swagger-like documentation for FastMCP registries.",
        },
        "paths": paths,
        "x-mcp": {
            "tools": [item.name for item in registries["tools"]],
            "prompts": [item.name for item in registries["prompts"]],
            "resources": [item.name for item in registries["resources"]],
        },
    }