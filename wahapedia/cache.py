"""Cache para Wahapedia: lee config desde .aia/mcp.json."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


def _find_mcp_config() -> Path | None:
    """Busca .aia/mcp.json en cwd, padres o sibling amanda-IA."""
    env_path = __import__("os").environ.get("AIA_MCP_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        candidate = parent / ".aia" / "mcp.json"
        if candidate.exists():
            return candidate
        # Sibling amanda-IA
        sibling = parent / "amanda-IA" / ".aia" / "mcp.json"
        if sibling.exists():
            return sibling
    return None


def _load_cache_config() -> dict[str, Any]:
    """Carga config de cache para wahapedia desde mcp.json."""
    path = _find_mcp_config()
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    servers = data.get("servers", [])
    for s in servers:
        if isinstance(s, dict) and s.get("name") == "wahapedia":
            return s.get("cache", {}) or {}
    return {}


def _cache_dir() -> Path | None:
    """Directorio de cache. None si cache deshabilitado."""
    cfg = _load_cache_config()
    if not cfg.get("enabled", False):
        return None
    dir_rel = cfg.get("dir", ".aia/cache/wahapedia")
    if not dir_rel:
        return None
    # Resolver ruta: si es relativa, usar el directorio donde está mcp.json
    mcp_path = _find_mcp_config()
    if mcp_path:
        base = mcp_path.parent.parent  # .aia/mcp.json -> proyecto
        return (base / dir_rel).resolve()
    return Path.cwd() / dir_rel


def _ttl_seconds() -> float:
    """TTL en segundos desde config (ttlDays, default 60)."""
    cfg = _load_cache_config()
    return float(cfg.get("ttlDays", 60)) * 86400


def _cache_key(prefix: str, *parts: str) -> str:
    """Genera clave de cache."""
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(f"{prefix}:{raw}".encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path | None:
    """Ruta del archivo de cache."""
    d = _cache_dir()
    if not d:
        return None
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def get(prefix: str, *parts: str) -> str | None:
    """Obtiene valor cacheado. None si no existe o expiró."""
    d = _cache_dir()
    if not d:
        return None
    key = _cache_key(prefix, *parts)
    path = _cache_path(key)
    if not path or not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            entry = json.load(f)
        ts = entry.get("timestamp", 0)
        if time.time() - ts >= _ttl_seconds():
            path.unlink(missing_ok=True)
            return None
        return entry.get("value")
    except Exception:
        return None


def set_(prefix: str, value: str, *parts: str) -> None:
    """Guarda valor en cache."""
    d = _cache_dir()
    if not d:
        return
    key = _cache_key(prefix, *parts)
    path = _cache_path(key)
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"value": value, "timestamp": time.time()}, f, ensure_ascii=False)
    except Exception:
        pass
