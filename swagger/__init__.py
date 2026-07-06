"""Swagger documentation module for MCP servers."""

from .introspection import MCPItem, collect_all, collect_tools, collect_prompts, collect_resources
from .openapi_generator import generate_openapi_like

__all__ = [
    "MCPItem",
    "collect_all",
    "collect_tools",
    "collect_prompts",
    "collect_resources",
    "generate_openapi_like",
]