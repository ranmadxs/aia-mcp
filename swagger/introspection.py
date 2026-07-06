"""MCP introspection utilities for swagger documentation."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class MCPItem:
    name: str
    description: str = ""
    category: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


def _safe_getattr(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:
        return default


def _to_dict_maybe(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    for method_name in ("model_dump", "dict"):
        method = _safe_getattr(value, method_name)
        if callable(method):
            try:
                dumped = method()
                if isinstance(dumped, dict):
                    return dumped
            except Exception:
                pass
    return {}


def _iter_registry(mcp: Any, attr_candidates: Iterable[str]) -> List[Any]:
    for attr_name in attr_candidates:
        value = _safe_getattr(mcp, attr_name)
        if value is None:
            continue
        if isinstance(value, dict):
            return list(value.values())
        if isinstance(value, (list, tuple, set)):
            return list(value)
    return []


async def _call_list_method(mcp: Any, method_name: str) -> List[Any]:
    method = _safe_getattr(mcp, method_name)
    if callable(method):
        try:
            result = method()
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, (list, tuple, set)):
                return list(result)
        except Exception:
            return []
    return []


def _extract_name(item: Any, fallback: str) -> str:
    for field_name in ("name", "id", "key", "uri"):
        value = _safe_getattr(item, field_name)
        if isinstance(value, str) and value:
            return value
    return fallback


def _extract_description(item: Any) -> str:
    for field_name in ("description", "doc", "docs", "__doc__"):
        value = _safe_getattr(item, field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_schema(item: Any, candidates: Iterable[str]) -> Dict[str, Any]:
    for field_name in candidates:
        value = _safe_getattr(item, field_name)
        schema = _to_dict_maybe(value)
        if schema:
            return schema
        if isinstance(value, dict):
            return value
    return {}


def _sdk_obj(item: Any, candidates: Iterable[str]) -> Any:
    for method_name in candidates:
        method = _safe_getattr(item, method_name)
        if callable(method):
            try:
                obj = method()
                if obj is not None:
                    return obj
            except Exception:
                pass
    return None


def _extract_extra_metadata(item: Any, skip: Optional[set[str]] = None) -> Dict[str, Any]:
    skip = skip or set()
    out: Dict[str, Any] = {}
    raw_dict = _safe_getattr(item, "__dict__", {})
    if isinstance(raw_dict, dict):
        for key, value in raw_dict.items():
            if key in skip:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[key] = value
    return out


def _resource_template_uri(resource_obj: Any) -> str:
    for field_name in ("uriTemplate", "uri_template", "uri", "name"):
        value = _safe_getattr(resource_obj, field_name)
        if isinstance(value, str) and value:
            return value
    return ""


def _resource_input_schema(resource_obj: Any) -> Dict[str, Any]:
    uri_template = _resource_template_uri(resource_obj)
    placeholders = re.findall(r"\{([^{}]+)\}", uri_template)

    properties: Dict[str, Any] = {"uri": {"type": "string"}}
    for placeholder in placeholders:
        properties[placeholder] = {
            "type": "string",
            "description": f"Value for resource template variable `{placeholder}`.",
        }

    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if placeholders:
        schema["required"] = placeholders
    return schema


async def collect_tools(mcp: Any) -> List[MCPItem]:
    raw_tools = await _call_list_method(mcp, "list_tools")
    if not raw_tools:
        raw_tools = _iter_registry(mcp, ("tools", "_tools", "tool_registry", "_tool_registry"))

    items: List[MCPItem] = []
    for idx, tool in enumerate(raw_tools):
        name = _extract_name(tool, f"tool_{idx}")
        sdk_tool = _sdk_obj(tool, ("to_mcp_tool",))
        tool_obj = sdk_tool if sdk_tool is not None else tool
        items.append(
            MCPItem(
                name=name,
                description=_extract_description(tool_obj),
                category="tool",
                input_schema=_extract_schema(
                    tool_obj,
                    (
                        "inputSchema",
                        "input_schema",
                        "parameters_schema",
                        "schema",
                        "args_schema",
                    ),
                ),
                output_schema=_extract_schema(
                    tool_obj,
                    ("outputSchema", "output_schema", "returns_schema", "result_schema"),
                ),
                metadata=_extract_extra_metadata(
                    tool_obj,
                    skip={
                        "name",
                        "description",
                        "annotations",
                        "meta",
                        "input_schema",
                        "inputSchema",
                        "output_schema",
                        "outputSchema",
                        "schema",
                        "args_schema",
                    },
                ),
            )
        )
    return items


async def collect_prompts(mcp: Any) -> List[MCPItem]:
    raw_prompts = await _call_list_method(mcp, "list_prompts")
    if not raw_prompts:
        raw_prompts = _iter_registry(mcp, ("prompts", "_prompts", "prompt_registry", "_prompt_registry"))

    items: List[MCPItem] = []
    for idx, prompt in enumerate(raw_prompts):
        name = _extract_name(prompt, f"prompt_{idx}")
        sdk_prompt = _sdk_obj(prompt, ("to_mcp_prompt",))
        items.append(
            MCPItem(
                name=name,
                description=_extract_description(sdk_prompt if sdk_prompt is not None else prompt),
                category="prompt",
                input_schema=_prompt_args_as_schema(sdk_prompt if sdk_prompt is not None else prompt),
                output_schema={},
                metadata=_extract_extra_metadata(
                    sdk_prompt if sdk_prompt is not None else prompt, skip={"name", "description"}
                ),
            )
        )
    return items


def _prompt_args_as_schema(prompt_obj: Any) -> Dict[str, Any]:
    arguments = _safe_getattr(prompt_obj, "arguments")
    if not isinstance(arguments, list):
        return {}

    properties: Dict[str, Any] = {}
    required: List[str] = []
    for arg in arguments:
        arg_name = _safe_getattr(arg, "name")
        if not isinstance(arg_name, str) or not arg_name:
            continue
        arg_desc = _safe_getattr(arg, "description")
        properties[arg_name] = {
            "type": "string",
            "description": arg_desc if isinstance(arg_desc, str) else "",
        }
        is_required = _safe_getattr(arg, "required")
        if is_required is True:
            required.append(arg_name)

    schema: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


async def collect_resources(mcp: Any) -> List[MCPItem]:
    raw_resources = await _call_list_method(mcp, "list_resources")
    raw_resources += await _call_list_method(mcp, "list_resource_templates")
    if not raw_resources:
        raw_resources = _iter_registry(
            mcp, ("resources", "_resources", "resource_registry", "_resource_registry")
        )

    items: List[MCPItem] = []
    for idx, resource in enumerate(raw_resources):
        name = _extract_name(resource, f"resource_{idx}")
        sdk_resource = _sdk_obj(resource, ("to_mcp_resource",))
        items.append(
            MCPItem(
                name=name,
                description=_extract_description(sdk_resource if sdk_resource is not None else resource),
                category="resource",
                input_schema=_resource_input_schema(
                    sdk_resource if sdk_resource is not None else resource
                ),
                output_schema={},
                metadata=_extract_extra_metadata(
                    sdk_resource if sdk_resource is not None else resource,
                    skip={"name", "description"},
                ),
            )
        )
    return items


async def collect_all(mcp: Any) -> Dict[str, List[MCPItem]]:
    return {
        "tools": await collect_tools(mcp),
        "prompts": await collect_prompts(mcp),
        "resources": await collect_resources(mcp),
    }