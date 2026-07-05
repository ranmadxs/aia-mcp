"""Cache para Wahapedia: configuración exclusiva por variables de entorno."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any


def _load_cache_config() -> dict[str, Any]:
    """Carga config de cache para wahapedia desde variables de entorno.

    Vars soportadas:
    - WAHAPEDIA_CACHE_ENABLED (true/false, default: false)
    - WAHAPEDIA_CACHE_DIR (ruta, relativa o absoluta, default: .aia/cache/wahapedia)
    - WAHAPEDIA_CACHE_TTL_DAYS (número, default 60)
    """
    env_enabled = os.environ.get("WAHAPEDIA_CACHE_ENABLED", "false")
    if env_enabled.lower() not in ("true", "1", "yes"):
        return {}
    return {
        "enabled": True,
        "dir": os.environ.get("WAHAPEDIA_CACHE_DIR", ".aia/cache/wahapedia"),
        "ttlDays": float(os.environ.get("WAHAPEDIA_CACHE_TTL_DAYS", "60")),
    }


def _cache_dir() -> Path | None:
    cfg = _load_cache_config()
    if not cfg.get("enabled", False):
        return None
    dir_rel = cfg.get("dir")
    if not dir_rel:
        return None
    return Path.cwd() / dir_rel


def _ttl_seconds() -> float:
    cfg = _load_cache_config()
    return float(cfg.get("ttlDays", 60)) * 86400


def _cache_key(prefix: str, *parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(f"{prefix}:{raw}".encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path | None:
    d = _cache_dir()
    if not d:
        return None
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.json"


def get(prefix: str, *parts: str) -> str | None:
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