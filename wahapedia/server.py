"""Servidor MCP Wahapedia - consulta estadísticas de unidades WH40K desde wahapedia.ru."""

import json
import logging
import os
import re
import threading
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
    """Busca unidad por nombre. Retorna (faction, unit_slug) o None.

    Orden de prioridad:
    1. Coincidencia exacta
    2. query contenido en unit_norm o viceversa
    3. Prefijo largo (>= mitad del query)
    4. Prefijo corto (3 chars) — último recurso
    """
    qnorm = _normalize_query(query)
    factions_to_search = [faction] if faction else FACTIONS

    # Pasada 1: exacta o contenida
    for fac in factions_to_search:
        units = _get_unit_list(fac)
        for unit_slug in units:
            unit_norm = _normalize_query(unit_slug.replace("-", " "))
            if qnorm == unit_norm or qnorm in unit_norm or unit_norm in qnorm:
                return (fac, unit_slug)

    # Pasada 2: prefijo largo (>= mitad del query, mínimo 4 chars)
    min_len = max(4, len(qnorm) // 2)
    if len(qnorm) >= min_len:
        prefix = qnorm[:min_len]
        for fac in factions_to_search:
            units = _get_unit_list(fac)
            for unit_slug in units:
                unit_norm = _normalize_query(unit_slug.replace("-", " "))
                if unit_norm.startswith(prefix):
                    return (fac, unit_slug)

    # Pasada 3: prefijo corto (3 chars) — solo si query es muy corto
    if len(qnorm) <= 5:
        for fac in factions_to_search:
            units = _get_unit_list(fac)
            for unit_slug in units:
                unit_norm = _normalize_query(unit_slug.replace("-", " "))
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


def _parse_weapons_table(soup: BeautifulSoup) -> list[str]:
    """Parsea la tabla de armas del datasheet. Retorna líneas formateadas."""
    lines = []
    for tbl in soup.find_all("table"):
        rows = tbl.find_all("tr")
        if not rows:
            continue
        first_cells = [td.get_text(strip=True) for td in rows[0].find_all(["td", "th"])]
        # Identificar tablas de armas por su cabecera
        if not any(h in first_cells for h in ("RANGED WEAPONS", "MELEE WEAPONS")):
            continue
        header = [c for c in first_cells if c]  # ['RANGED WEAPONS'/'MELEE WEAPONS', 'RANGE', 'A', 'BS'/'WS', 'S', 'AP', 'D']
        section = header[0] if header else "WEAPONS"
        lines.append(f"\n{section}: Range | A | BS/WS | S | AP | D")
        SECTION_HEADERS = {"RANGED WEAPONS", "MELEE WEAPONS"}
        seen = set()
        for row in rows[1:]:
            cells = [td.get_text(separator=" ", strip=True) for td in row.find_all(["td", "th"])]
            # Remove leading empty cell
            cells = [c for c in cells if c]
            if not cells:
                continue
            # New section header mid-table (e.g. MELEE WEAPONS after RANGED)
            if cells[0] in SECTION_HEADERS:
                lines.append(f"\n{cells[0]}: Range | A | BS/WS | S | AP | D")
                seen.clear()
                continue
            if len(cells) == 1:
                # Weapon group label (repeated before stats row) — skip
                continue
            if len(cells) >= 7:
                # Full weapon row: name, range, A, BS/WS, S, AP, D
                name = cells[0]
                if name in seen:
                    continue
                seen.add(name)
                stats = " | ".join(cells[1:7])
                special = cells[7] if len(cells) > 7 else ""
                suffix = f"  [{special}]" if special else ""
                lines.append(f"  {name}: {stats}{suffix}")
        # If section header "MELEE WEAPONS" appears mid-table, mark it
        # (already handled by separate table iteration)
    return lines


def _parse_abilities(soup: BeautifulSoup) -> list[str]:
    """Parsea las habilidades principales del datasheet."""
    lines = []
    seen = set()
    for ab in soup.find_all("div", class_="dsAbility"):
        text = ab.get_text(separator=" ", strip=True)
        # Skip overly long ability texts (detailed rules) — keep the label short
        short = text[:120]
        if short and short not in seen:
            seen.add(short)
            lines.append(f"  {short}")
    return lines


def _fetch_unit_stats(faction: str, unit_slug: str) -> str | None:
    """Obtiene estadísticas completas de una unidad desde Wahapedia (con cache).

    Incluye: perfil base (M/T/Sv/W/Ld/OC), armas (ranged+melee con Range/A/BS-WS/S/AP/D)
    y habilidades. El resultado del cache NO incluye IMAGE_PATH>>.
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
            # Strip base size notation e.g. "Carnifexes(⌀105 x 70mm)"
            unit_name = re.sub(r"\(.*?\)$", "", unit_name).strip()

            lines = [f"{unit_name}", f"Facción: {faction.replace('-',' ').title()}", f"Fuente: {url}", ""]

            # ── Perfil base (M, T, Sv, W, Ld, OC) ──────────────────────
            lines.append("PERFIL BASE:")
            chars = soup.select("div.dsCharWrap")
            for ch in chars:
                name_el = ch.find("div", {"class": "dsCharName"})
                val_el  = ch.find("div", {"class": "dsCharValue"})
                if name_el and val_el:
                    lines.append(f"  {name_el.text.strip()}: {val_el.text.strip()}")

            # Invulnerable save
            invul = soup.select_one("div.dsInvulWrap")
            if invul:
                name_el = invul.find("div", recursive=False)
                val_el  = invul.select_one("div.dsCharInvulValue")
                if name_el and val_el:
                    lines.append(f"  {name_el.text.strip()}: {val_el.text.strip()}")

            # ── Armas ────────────────────────────────────────────────────
            weapon_lines = _parse_weapons_table(soup)
            if weapon_lines:
                lines.extend(weapon_lines)

            # ── Habilidades ──────────────────────────────────────────────
            ability_lines = _parse_abilities(soup)
            if ability_lines:
                lines.append("\nHABILIDADES:")
                lines.extend(ability_lines)

            result = "\n".join(lines) if len(lines) > 3 else None
            if result:
                cache_set("unit_stats", result, faction, unit_slug)

            # Descargar imagen en background si no está en cache
            img_cached = _get_or_download_image(faction, unit_slug)
            if not img_cached:
                def _bg_fetch(name=unit_name, slug=unit_slug):
                    img_url = _search_image_bing(f"{name} Warhammer 40k Tyranids miniature")
                    if img_url:
                        _download_image(img_url, slug)
                    else:
                        logger.warning("No image found via Bing for %s", slug)
                threading.Thread(target=_bg_fetch, daemon=True).start()

            return result
    except Exception as e:
        logger.exception("Error fetching unit stats for %s/%s: %s", faction, unit_slug, e)
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
        # No en cache: descarga en background, la imagen estará disponible en la próxima consulta
        unit_name = unit_slug.replace("-", " ").title()
        def _bg_fetch(name=unit_name, slug=unit_slug):
            img_url = _search_image_bing(name)
            if img_url:
                _download_image(img_url, slug)
        threading.Thread(target=_bg_fetch, daemon=True).start()
    else:
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
        # Descargar sincrónicamente ya que esta tool fue llamada explícitamente para la imagen
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


def _fetch_unit_stratagems_raw(faction: str, unit_slug: str) -> list[dict] | None:
    """Obtiene lista de estratagemas de la página de unidad (con cache).
    Retorna lista de dicts {name, cp, type, text} o None."""
    cache_key_prefix = "unit_stratagems"
    cached = cache_get(cache_key_prefix, faction, unit_slug)
    if cached is not None:
        logger.info("Cache request GET: unit_stratagems %s %s", faction, unit_slug)
        try:
            return json.loads(cached)
        except Exception:
            pass
    url = f"{BASE_URL}/{EDITION}/factions/{faction}/{unit_slug}"
    logger.info("Http request stratagems: %s", url)
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return None
            soup = BeautifulSoup(resp.content, "html.parser")
            strats = []
            for block in soup.find_all("div", class_="str10Wrap"):
                name_el = block.find("div", class_="str10Name")
                cp_el   = block.find("div", class_="str10CP")
                type_el = block.find("div", class_="str10Type")
                text_el = block.find("div", class_="str10Text")
                if not name_el:
                    continue
                strats.append({
                    "name": name_el.get_text(strip=True),
                    "cp":   cp_el.get_text(strip=True) if cp_el else "?",
                    "type": type_el.get_text(strip=True) if type_el else "",
                    "text": text_el.get_text(separator=" ", strip=True) if text_el else "",
                })
            if strats:
                cache_set(cache_key_prefix, json.dumps(strats, ensure_ascii=False), faction, unit_slug)
            return strats or None
    except Exception as e:
        logger.warning("Error fetching unit stratagems %s/%s: %s", faction, unit_slug, e)
        return None


@mcp.tool()
def get_unit_stratagems(query: str, faction: str = "") -> str:
    """
    Lista las estratagemas disponibles para una unidad de Warhammer 40K.
    Muestra título, coste en CP y tipo de cada estratagema.
    Para ver el detalle completo de una, usa get_stratagem_detail().

    Args:
        query:   Nombre de la unidad (ej: "Carnifex", "Hormagaunts").
        faction: Facción opcional (ej: "tyranids"). Si está vacío, busca en todas.

    Returns:
        Lista de estratagemas aplicables con nombre, CP y tipo.
    """
    fac = _resolve_faction_slug(faction) if faction.strip() else None
    found = _find_unit_slug(query, fac)
    if not found and fac:
        found = _find_unit_slug(query, None)
    if not found:
        return f"No se encontró la unidad '{query}'."
    fac, unit_slug = found
    strats = _fetch_unit_stratagems_raw(fac, unit_slug)
    if not strats:
        return f"No se encontraron estratagemas para '{unit_slug}' ({fac})."
    unit_display = unit_slug.replace("-", " ").title()
    lines = [f"## Estratagemas de {unit_display} ({fac.replace('-',' ').title()})\n"]
    for s in strats:
        lines.append(f"- **{s['name']}** [{s['cp']}]  —  {s['type']}")
    lines.append(f"\nTotal: {len(strats)} estratagemas.")
    lines.append(f"\nUsa `get_stratagem_detail(name=\"...\", faction=\"{fac}\")` para ver el texto completo de una.")
    return "\n".join(lines)


@mcp.tool()
def get_stratagem_detail(name: str, faction: str = "") -> str:
    """
    Devuelve el texto completo de una estratagema de Warhammer 40K.

    Args:
        name:    Nombre exacto o aproximado de la estratagema
                 (ej: "RAPID REGENERATION", "ADRENAL SURGE").
        faction: Facción (ej: "tyranids", "space-marines"). Recomendado para mayor velocidad.

    Returns:
        Nombre, coste CP, tipo y reglas completas de la estratagema.
    """
    # Buscar la facción
    fac = _resolve_faction_slug(faction) if faction.strip() else None
    facs_to_search = [fac] if fac else FACTIONS

    qnorm = _normalize_query(name)

    for f in facs_to_search:
        cached_strats = None
        # Intentar encontrar en cache de cualquier unidad de esa facción
        # Más rápido: usar la página de facción directamente (stratagems ya parseada)
        cached_fac = cache_get("stratagems", f)
        if cached_fac is None:
            # Hacer fetch de la página de facción para obtener todas las estratagemas
            url = f"{BASE_URL}/{EDITION}/factions/{f}/"
            try:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.get(url)
                    if resp.status_code != 200:
                        continue
                    soup = BeautifulSoup(resp.content, "html.parser")
                    all_strats = []
                    for block in soup.find_all("div", class_="str10Wrap"):
                        n_el  = block.find("div", class_="str10Name")
                        cp_el = block.find("div", class_="str10CP")
                        t_el  = block.find("div", class_="str10Type")
                        tx_el = block.find("div", class_="str10Text")
                        if n_el:
                            all_strats.append({
                                "name": n_el.get_text(strip=True),
                                "cp":   cp_el.get_text(strip=True) if cp_el else "?",
                                "type": t_el.get_text(strip=True) if t_el else "",
                                "text": tx_el.get_text(separator=" ", strip=True) if tx_el else "",
                            })
                    if all_strats:
                        cache_set("stratagems_detail", json.dumps(all_strats, ensure_ascii=False), f)
                        cached_strats = all_strats
            except Exception:
                continue
        else:
            # Try the detail cache
            raw = cache_get("stratagems_detail", f)
            if raw:
                try:
                    cached_strats = json.loads(raw)
                except Exception:
                    pass

        if not cached_strats:
            continue

        # Buscar por nombre normalizado (exacto primero, luego contenido)
        match = None
        for s in cached_strats:
            snorm = _normalize_query(s["name"])
            if snorm == qnorm:
                match = s; break
        if not match:
            for s in cached_strats:
                snorm = _normalize_query(s["name"])
                if qnorm in snorm or snorm in qnorm:
                    match = s; break

        if match:
            lines = [
                f"## {match['name']}",
                f"**Coste:** {match['cp']}  |  **Tipo:** {match['type']}",
                "",
                match["text"],
            ]
            return "\n".join(lines)

    return f"No se encontró la estratagema '{name}'" + (f" en facción '{faction}'." if faction else ".")


if __name__ == "__main__":
    mcp.run(transport="stdio")
