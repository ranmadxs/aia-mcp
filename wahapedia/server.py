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


def _resolve_faction_slug(faction: str) -> str | None:
    """Resuelve nombre de facción a slug (ej: 'adeptus custodes' -> 'adeptus-custodes')."""
    q = faction.strip().lower().replace(" ", "-")
    if q in FACTIONS:
        return q
    qnorm = _normalize_query(q.replace("-", ""))
    for fac in FACTIONS:
        if qnorm == _normalize_query(fac.replace("-", "")):
            return fac
    return None


def _fetch_stratagems(faction: str) -> str | None:
    """Obtiene estratagemas de una facción desde Wahapedia (con cache)."""
    fac = _resolve_faction_slug(faction)
    if not fac or fac not in FACTIONS:
        return None
    cached = cache_get("stratagems", fac)
    if cached is not None:
        logger.info("Cache request GET: stratagems %s", fac)
        return cached
    url = f"{BASE_URL}/{EDITION}/factions/{fac}/"
    logger.info("Http request: %s", url)
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return None
            soup = BeautifulSoup(resp.content, "html.parser")
            stratagems = []
            for block in soup.find_all("div", class_="str10Wrap"):
                name_el = block.find("div", class_="str10Name")
                cp_el = block.find("div", class_="str10CP")
                type_el = block.find("div", class_="str10Type")
                text_el = block.find("div", class_="str10Text")
                if not (name_el and cp_el and type_el and text_el):
                    continue
                name = name_el.get_text(strip=True)
                cost = cp_el.get_text(strip=True)
                stype = type_el.get_text(strip=True)
                desc = text_el.get_text(separator=" ", strip=True)
                stratagems.append(f"{name} ({cost}) – {stype}\n{desc}")
            if not stratagems:
                return None
            result = f"{url}#Stratagems\n\n" + "\n\n".join(stratagems)
            cache_set("stratagems", result, fac)
            return result
    except Exception:
        return None


def _find_aia_root() -> Path | None:
    """Encuentra el directorio raíz del proyecto amanda-IA (donde está .aia/)."""
    from wahapedia.cache import _find_mcp_config
    cfg = _find_mcp_config()
    if cfg:
        return cfg.parent.parent  # .aia/mcp.json → proyecto
    return Path.cwd()


def _image_cache_path(slug: str) -> Path:
    """Ruta permanente de imagen: .aia/images/<slug>.jpg"""
    root = _find_aia_root() or Path.cwd()
    img_dir = root / ".aia" / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", slug)
    return img_dir / f"{safe}.jpg"


def _search_image_bing(unit_name: str) -> str | None:
    """Busca la imagen de una unidad WH40K usando Bing Image Search."""
    _ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
    query = f"{unit_name} Warhammer 40k miniature"
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            r = client.get(
                "https://www.bing.com/images/search",
                params={"q": query, "form": "HDRSC2", "first": "1"},
                headers={"User-Agent": _ua, "Accept-Language": "en-US,en;q=0.9"},
            )
            if r.status_code != 200:
                return None
            # Bing embeds murl (media URL) HTML-encoded in the page
            import html
            matches = re.findall(r'&quot;murl&quot;:&quot;([^&]+)&quot;', r.text)
            if not matches:
                # Fallback: unescaped JSON
                matches = re.findall(r'"murl":"([^"]+)"', r.text)
            if matches:
                url = html.unescape(matches[0])
                logger.info("Bing image found for %s: %s", unit_name, url[:100])
                return url
    except Exception as e:
        logger.warning("Bing image search failed for %s: %s", unit_name, e)
    return None


def _download_image(img_url: str, unit_slug: str) -> str | None:
    """Descarga una imagen y la guarda en cache. Retorna la ruta local o None."""
    path = _image_cache_path(unit_slug)
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            resp = client.get(img_url)
            if resp.status_code == 200:
                # Ajustar extensión según Content-Type
                ct = resp.headers.get("content-type", "")
                if "png" in ct:
                    path = path.with_suffix(".png")
                elif "webp" in ct:
                    path = path.with_suffix(".webp")
                path.write_bytes(resp.content)
                logger.info("Image downloaded: %s → %s", img_url, path)
                return str(path)
    except Exception as e:
        logger.warning("Image download failed %s: %s", img_url, e)
    return None


def _get_or_download_image(faction: str, unit_slug: str) -> str | None:
    """
    Retorna la ruta local de la imagen de la unidad.
    Cache en disco: si ya existe el archivo, lo retorna directamente.
    Si no, requiere que se llame desde _fetch_unit_stats_with_image para reusar el soup.
    """
    for ext in (".jpg", ".png", ".webp"):
        p = _image_cache_path(unit_slug).with_suffix(ext)
        if p.exists():
            logger.info("Image cache HIT: %s", p)
            return str(p)
    return None


def _fetch_unit_stats(faction: str, unit_slug: str) -> str | None:
    """Obtiene estadísticas + imagen de una unidad desde Wahapedia (con cache).

    El resultado del cache de stats NO incluye IMAGE_PATH>> porque la imagen
    se gestiona por separado en disco. get_unit_stats() añade IMAGE_PATH>> después.
    """
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

            # Descargar imagen si no está en cache (buscar via DDG)
            img_cached = _get_or_download_image(faction, unit_slug)
            if not img_cached:
                img_url = _search_image_bing(unit_name)
                if img_url:
                    _download_image(img_url, unit_slug)
                else:
                    logger.warning("No image found via DDG for %s/%s", faction, unit_slug)

            return result
    except Exception:
        return None


@mcp.tool()
def get_factions() -> str:
    """
    Lista todas las facciones de Warhammer 40K disponibles en Wahapedia.

    Returns:
        Lista de facciones con su nombre legible y slug para usar en otras tools.
    """
    lines = ["## Facciones Warhammer 40K (10ª edición)\n"]
    for slug in FACTIONS:
        name = slug.replace("-", " ").title()
        lines.append(f"- **{name}** → `{slug}`")
    lines.append(f"\nTotal: {len(FACTIONS)} facciones")
    lines.append("\nUsa el slug en `get_units`, `get_stratagems` o `get_unit_stats`.")
    return "\n".join(lines)


@mcp.tool()
def get_units(faction: str) -> str:
    """
    Lista todas las unidades de una facción de Warhammer 40K desde Wahapedia.

    Args:
        faction: Nombre o slug de la facción (ej: "space-marines", "adeptus custodes", "necrons").

    Returns:
        Lista de unidades de la facción con sus slugs.
    """
    fac = _resolve_faction_slug(faction)
    if not fac:
        valid = ", ".join(FACTIONS[:6]) + ", ..."
        return f"Facción '{faction}' no encontrada. Usa get_factions() para ver la lista. Ejemplos: {valid}"
    units = _get_unit_list(fac)
    if not units:
        return f"No se pudieron obtener unidades para '{fac}'. Puede ser un problema de conexión con Wahapedia."
    name = fac.replace("-", " ").title()
    lines = [f"## Unidades de {name} ({len(units)} unidades)\n"]
    for u in units:
        display = u.replace("-", " ").title()
        lines.append(f"- {display}")
    lines.append(f"\nUsa `get_unit_stats(query=..., faction=\"{fac}\")` para ver estadísticas.")
    return "\n".join(lines)


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
    fac = _resolve_faction_slug(faction) if faction and faction.strip() else None
    found = _find_unit_slug(query, fac)
    # Si no se encontró con la facción dada, buscar en todas (typo del LLM)
    if not found and fac:
        found = _find_unit_slug(query, None)
    if not found:
        return f"No se encontró la unidad '{query}' en Wahapedia. Prueba con otro nombre o especifica la facción."
    fac, unit_slug = found
    result = _fetch_unit_stats(fac, unit_slug)
    if not result:
        return f"Error al obtener datos de {unit_slug} en Wahapedia."
    img_path = _get_or_download_image(fac, unit_slug)
    if not img_path:
        unit_name = unit_slug.replace("-", " ").title()
        img_url = _search_image_bing(unit_name)
        if img_url:
            img_path = _download_image(img_url, unit_slug)
    if img_path:
        result += f"\n\nIMAGE_PATH>> {img_path}"
    return result


@mcp.tool()
def get_stratagems(faction: str) -> str:
    """
    Obtiene las estratagemas de una facción de Warhammer 40K desde Wahapedia.

    Args:
        faction: Nombre de la facción en inglés (ej: "adeptus-custodes", "space-marines",
                 "adepta-sororitas"). Puede usar guiones o espacios.

    Returns:
        Lista de estratagemas con nombre, coste en CP, tipo y descripción. Incluye URL.
    """
    result = _fetch_stratagems(faction)
    if not result:
        valid = ", ".join(FACTIONS[:8]) + ", ..."
        return f"No se encontraron estratagemas para '{faction}'. Facciones válidas: {valid}"
    return result


@mcp.tool()
def get_unit_image(query: str, faction: str = "") -> str:
    """
    Descarga y retorna la imagen de una unidad de Warhammer 40K desde Wahapedia.
    La imagen se guarda en .aia/download/images/ (cache en disco).

    Args:
        query: Nombre de la unidad (ej: "Rhino", "Saint Celestine")
        faction: Facción opcional (ej: "space-marines"). Si está vacío, busca en todas.

    Returns:
        Ruta local de la imagen como IMAGE_PATH>> .aia/download/images/<unidad>.jpg
    """
    fac = faction.strip().lower().replace(" ", "-") if faction else None
    found = _find_unit_slug(query, fac)
    if not found:
        return f"No se encontró la unidad '{query}' en Wahapedia."
    fac, unit_slug = found
    # Intentar desde cache disco primero
    img_path = _get_or_download_image(fac, unit_slug)
    if not img_path:
        unit_name = unit_slug.replace("-", " ").title()
        img_url = _search_image_bing(unit_name)
        if img_url:
            img_path = _download_image(img_url, unit_slug)
    if not img_path:
        return f"No se encontró imagen para '{query}' en Wahapedia."
    return f"IMAGE_PATH>> {img_path}"


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
