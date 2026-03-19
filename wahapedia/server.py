"""Servidor MCP Wahapedia - consulta estadísticas de unidades WH40K desde wahapedia.ru."""

import json
import logging
import os
import re
from pathlib import Path

import httpx

logger = logging.getLogger("wahapedia")
import tomllib
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

from wahapedia.cache import get as cache_get, set_ as cache_set

mcp = FastMCP(
    "wahapedia",
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("FASTMCP_PORT", "8002")),
)

# Versión desde pyproject.toml
_pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
if _pyproject.exists():
    with open(_pyproject, "rb") as f:
        mcp._mcp_server.version = tomllib.load(f)["tool"]["poetry"]["version"]

BASE_URL = "https://wahapedia.ru"
EDITION = "wh40k10ed"

# Facciones conocidas (slug en URL)
FACTIONS = [
    "space-marines",
    "adepta-sororitas",
    "adeptus-custodes",
    "adeptus-mechanicus",
    "astra-militarum",
    "grey-knights",
    "imperial-agents",
    "imperial-knights",
    "chaos-daemons",
    "chaos-knights",
    "chaos-space-marines",
    "death-guard",
    "emperor-children",
    "thousand-sons",
    "world-eaters",
    "aeldari",
    "drukhari",
    "genestealer-cults",
    "leagues-of-votann",
    "necrons",
    "orks",
    "tau-empire",
    "tyranids",
]


def _slugify(name: str) -> str:
    """Convierte nombre a slug estilo Wahapedia (ej: Saint Celestine -> Saint-Celestine)."""
    return re.sub(r"\s+", "-", name.strip())


def _normalize_query(q: str) -> str:
    """Normaliza búsqueda para comparación."""
    return re.sub(r"[^a-z0-9]", "", q.lower())


def _get_unit_list(faction: str) -> list[str]:
    """Obtiene lista de unidades de una facción desde Wahapedia (con cache)."""
    cached = cache_get("unit_list", faction)
    if cached is not None:
        try:
            logger.info("Cache request GET: unit_list %s", faction)
            return json.loads(cached)
        except Exception:
            pass
    url = f"{BASE_URL}/{EDITION}/factions/{faction}/"
    logger.info("Http request: %s", url)
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.content, "html.parser")
            divs = soup.find_all("div", {"class": "NavDropdown-content_P"})
            if not divs:
                return []
            urls = divs[0].find_all("a", {"class": "contentColor"})
            prefix = f"/{EDITION}/factions/{faction}/"
            units = []
            for a in urls:
                href = a.get("href", "")
                if prefix in href:
                    token = href.replace(prefix, "").rstrip("/")
                    if token:
                        units.append(token)
            cache_set("unit_list", json.dumps(units), faction)
            return units
    except Exception:
        return []


def _find_unit_slug(query: str, faction: str | None) -> tuple[str, str] | None:
    """Busca unidad por nombre. Retorna (faction, unit_slug) o None."""
    qnorm = _normalize_query(query)
    factions_to_search = [faction] if faction else FACTIONS

    for fac in factions_to_search:
        units = _get_unit_list(fac)
        for unit_slug in units:
            unit_norm = _normalize_query(unit_slug.replace("-", " "))
            if qnorm in unit_norm or unit_norm in qnorm:
                return (fac, unit_slug)
            # Coincidencia parcial
            if len(qnorm) >= 3 and qnorm[:3] in unit_norm:
                return (fac, unit_slug)

    return None


def _fetch_unit_stats(faction: str, unit_slug: str) -> str | None:
    """Obtiene estadísticas de una unidad desde Wahapedia (con cache)."""
    cached = cache_get("unit_stats", faction, unit_slug)
    if cached is not None:
        logger.info("Cache request GET: unit_stats %s %s", faction, unit_slug)
        return cached
    url = f"{BASE_URL}/{EDITION}/factions/{faction}/{unit_slug}"
    logger.info("Http request: %s", url)
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return None
            soup = BeautifulSoup(resp.content, "html.parser")

            # Nombre de la unidad
            unit_div = soup.select_one("div.dsH2Header div:first-child")
            unit_name = unit_div.text.strip() if unit_div else unit_slug

            # Características base (M, T, Sv, W, Ld, OC)
            chars = soup.select("div.dsCharWrap")
            lines = [f"{unit_name}\n{url}\n"]
            for ch in chars:
                name_el = ch.find("div", {"class": "dsCharName"})
                val_el = ch.find("div", {"class": "dsCharValue"})
                if name_el and val_el:
                    lines.append(f"{name_el.text.strip()}\t{val_el.text.strip()}")

            # Invulnerable save
            invul = soup.select_one("div.dsInvulWrap")
            if invul:
                name_el = invul.find("div", recursive=False)
                val_el = invul.select_one("div.dsCharInvulValue")
                if name_el and val_el:
                    lines.append(f"{name_el.text.strip()}\t{val_el.text.strip()}")

            result = "\n".join(lines) if len(lines) > 1 else None
            if result:
                cache_set("unit_stats", result, faction, unit_slug)
            return result
    except Exception:
        return None


@mcp.tool()
def get_unit_stats(query: str, faction: str = "") -> str:
    """
    Busca estadísticas de una unidad de Warhammer 40K en Wahapedia.

    Args:
        query: Nombre de la unidad (ej: "Rhino", "Saint Celestine", "Space Marine")
        faction: Facción opcional (ej: "space-marines", "adepta-sororitas"). Si está vacío, busca en todas.

    Returns:
        Estadísticas de la unidad (M, T, Sv, W, Ld, OC, etc.) y URL de Wahapedia.
    """
    fac = faction.strip().lower().replace(" ", "-") if faction else None
    found = _find_unit_slug(query, fac)
    if not found:
        return f"No se encontró la unidad '{query}' en Wahapedia. Prueba con otro nombre o especifica la facción."
    fac, unit_slug = found
    result = _fetch_unit_stats(fac, unit_slug)
    if not result:
        return f"Error al obtener datos de {unit_slug} en Wahapedia."
    return result


@mcp.tool()
def search_wahapedia(query: str) -> str:
    """
    Busca información en Wahapedia. Útil para preguntas en español como:
    - "estadísticas de un Rhino"
    - "datos de Saint Celestine"
    - "características de un Space Marine"

    Args:
        query: Pregunta o búsqueda en español (ej: "estadísticas de Saint Celestine")

    Returns:
        Estadísticas de la unidad encontrada o mensaje de error.
    """
    # Extraer nombre de unidad de frases comunes
    q = query.strip()
    for prefix in [
        "estadísticas de ",
        "estadisticas de ",
        "datos de ",
        "características de ",
        "caracteristicas de ",
        "stats de ",
        "información de ",
        "informacion de ",
        "cuáles son las estadísticas de ",
        "cuales son las estadisticas de ",
        "dame las estadísticas de ",
        "dame las estadisticas de ",
    ]:
        if q.lower().startswith(prefix):
            q = q[len(prefix) :].strip()
            break
    return get_unit_stats(q, "")


if __name__ == "__main__":
    mcp.run(transport="stdio")
