"""Servidor MCP MangaDex — busca, descarga y gestiona manga desde MangaDex.org.

Usa la API REST pública de MangaDex v5 (api.mangadex.org) para búsqueda,
metadata y gestión de capítulos. Para descarga usa mangadex-downloader CLI.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import threading
from pathlib import Path

import httpx
import tomllib
from mcp.server.fastmcp import FastMCP

# Carga .env desde la carpeta del servidor si existe
try:
    from dotenv import load_dotenv as _load_dotenv
    # Busca .env en la carpeta del servidor y luego en la raíz del proyecto
    _load_dotenv(Path(__file__).resolve().parent / ".env")
    _load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

logger = logging.getLogger("mangadex")

mcp = FastMCP(
    "mangadex",
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("FASTMCP_PORT", "8009")),
)

_pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
if _pyproject.exists():
    with open(_pyproject, "rb") as f:
        mcp._mcp_server.version = tomllib.load(f)["tool"]["poetry"]["version"]

# ── Constantes ────────────────────────────────────────────────────────────────

API = "https://api.mangadex.org"
CDN = "https://uploads.mangadex.org"
HEADERS = {"User-Agent": "aia-mcp/mangadex (github.com/ranmadxs/aia-mcp)"}
TIMEOUT = 15.0

# Directorio de descarga: env var AIA_MANGA_DIR o ~/.aia/manga como fallback
_DEFAULT_DOWNLOAD_DIR = os.environ.get(
    "AIA_MANGA_DIR",
    str(Path.home() / "trabajos" / "amanda-IA" / ".aia" / "manga"),
)

# Ruta al CLI de mangadex-downloader (instalado con pipx)
_MDX_CLI = str(Path.home() / ".local" / "bin" / "mangadex-dl")


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _get(path: str, params: dict | None = None) -> dict:
    """GET a api.mangadex.org con manejo de errores."""
    with httpx.Client(timeout=TIMEOUT, headers=HEADERS) as client:
        r = client.get(f"{API}{path}", params=params or {})
        r.raise_for_status()
        return r.json()


def _title(attrs: dict) -> str:
    """Extrae título preferentemente en inglés."""
    t = attrs.get("title", {})
    return t.get("en") or t.get("ja-ro") or t.get("ja") or next(iter(t.values()), "?")


def _desc(attrs: dict, max_len: int = 300) -> str:
    d = attrs.get("description", {})
    text = d.get("en") or d.get("ja-ro") or next(iter(d.values()), "")
    return text[:max_len] + ("…" if len(text) > max_len else "")


def _cover_url(manga_id: str, relationships: list) -> str | None:
    rel = next((r for r in relationships if r["type"] == "cover_art"), None)
    if rel and rel.get("attributes"):
        fn = rel["attributes"]["fileName"]
        return f"{CDN}/covers/{manga_id}/{fn}.256.jpg"
    return None


def _author_names(relationships: list) -> list[str]:
    names = []
    for r in relationships:
        if r["type"] in ("author", "artist") and r.get("attributes"):
            names.append(r["attributes"].get("name", ""))
    return list(dict.fromkeys(n for n in names if n))


def _fmt_manga(m: dict) -> str:
    """Formatea un manga en texto legible."""
    mid = m["id"]
    a = m["attributes"]
    rels = m.get("relationships", [])
    title = _title(a)
    status = a.get("status", "?")
    year = a.get("year") or "?"
    lang = a.get("originalLanguage", "?")
    chapters = a.get("lastChapter") or "?"
    rating = a.get("contentRating", "?")
    tags = [t["attributes"]["name"].get("en", "") for t in a.get("tags", [])]
    tags_str = ", ".join(tags[:8]) if tags else "—"
    authors = _author_names(rels)
    authors_str = ", ".join(authors) if authors else "?"
    cover = _cover_url(mid, rels) or ""
    desc = _desc(a)
    lines = [
        f"**{title}**",
        f"ID: {mid}",
        f"URL: https://mangadex.org/title/{mid}",
        f"Estado: {status} | Año: {year} | Idioma: {lang} | Rating: {rating}",
        f"Capítulos: {chapters} | Autor: {authors_str}",
        f"Tags: {tags_str}",
    ]
    if cover:
        lines.append(f"Portada: {cover}")
    if desc:
        lines.append(f"\n{desc}")
    return "\n".join(lines)


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def search_manga(
    query: str,
    language: str = "en",
    limit: int = 5,
    tags: str = "",
    status: str = "",
    content_rating: str = "safe,suggestive,erotica",
) -> str:
    """
    Busca manga en MangaDex por título, tags, estado e idioma.

    Args:
        query:          Título o palabras clave a buscar.
        language:       Idioma de traducción disponible (ej: "en", "es", "ja"). Default "en".
        limit:          Cantidad de resultados (1-20). Default 5.
        tags:           Tags separados por coma (ej: "Action,Romance,Isekai").
        status:         Estado del manga: ongoing, completed, hiatus, cancelled.
        content_rating: Ratings separados por coma: safe, suggestive, erotica, pornographic.

    Returns:
        Lista de manga con ID, título, autores, estado, tags y URL.
    """
    try:
        params: dict = {
            "title": query,
            "limit": max(1, min(20, limit)),
            "includes[]": ["cover_art", "author", "artist"],
            "order[relevance]": "desc",
        }
        if language:
            params["availableTranslatedLanguage[]"] = language
        if status:
            params["status[]"] = status
        for rating in [r.strip() for r in content_rating.split(",") if r.strip()]:
            params.setdefault("contentRating[]", [])
            if isinstance(params["contentRating[]"], str):
                params["contentRating[]"] = [params["contentRating[]"]]
            params["contentRating[]"].append(rating)

        if tags:
            # Resolver tag names a IDs
            tag_data = _get("/manga/tag")
            tag_map = {
                t["attributes"]["name"].get("en", "").lower(): t["id"]
                for t in tag_data.get("data", [])
            }
            tag_ids = []
            for tag_name in [t.strip().lower() for t in tags.split(",") if t.strip()]:
                tid = tag_map.get(tag_name)
                if tid:
                    tag_ids.append(tid)
                else:
                    # Búsqueda parcial
                    for key, val in tag_map.items():
                        if tag_name in key:
                            tag_ids.append(val)
                            break
            if tag_ids:
                params["includedTags[]"] = tag_ids

        data = _get("/manga", params)
        results = data.get("data", [])
        total = data.get("total", 0)
        if not results:
            return f"No se encontró manga para '{query}'."
        lines = [f"## Resultados para '{query}' ({total} totales, mostrando {len(results)})\n"]
        for m in results:
            lines.append(_fmt_manga(m))
            lines.append("---")
        return "\n".join(lines)
    except Exception as e:
        return f"Error buscando manga: {e}"


@mcp.tool()
def get_manga_info(manga_id: str) -> str:
    """
    Obtiene información detallada de un manga por su ID de MangaDex.

    Args:
        manga_id: UUID del manga en MangaDex (ej: "a1c7c817-4e59-43b7-9365-09675a149a6f")
                  o URL completa de MangaDex.

    Returns:
        Información completa: título, autores, tags, estado, descripción, portada y estadísticas.
    """
    try:
        manga_id = _extract_id(manga_id)
        data = _get(f"/manga/{manga_id}", {"includes[]": ["cover_art", "author", "artist"]})
        m = data.get("data")
        if not m:
            return f"Manga '{manga_id}' no encontrado."
        # Estadísticas (rating, follows, etc.)
        stats = {}
        try:
            sr = _get(f"/statistics/manga/{manga_id}")
            stats = sr.get("statistics", {}).get(manga_id, {})
        except Exception:
            pass
        lines = [_fmt_manga(m)]
        if stats:
            rating_val = stats.get("rating", {}).get("bayesian", 0)
            follows = stats.get("follows", 0)
            lines.append(f"\nRating: {rating_val:.2f} | Seguidores: {follows:,}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error obteniendo manga: {e}"


@mcp.tool()
def get_manga_chapters(
    manga_id: str,
    language: str = "en",
    limit: int = 20,
    order: str = "asc",
    volume: str = "",
) -> str:
    """
    Lista los capítulos disponibles de un manga.

    Args:
        manga_id:  UUID o URL del manga en MangaDex.
        language:  Idioma de traducción (ej: "en", "es", "ja"). Default "en".
        limit:     Cantidad de capítulos a listar (1-100). Default 20.
        order:     Orden: "asc" (del 1 en adelante) o "desc" (últimos primero).
        volume:    Filtrar por volumen específico (ej: "1").

    Returns:
        Lista de capítulos con número, volumen, título, grupo de traducción y fecha.
    """
    try:
        manga_id = _extract_id(manga_id)
        params: dict = {
            "translatedLanguage[]": language,
            "limit": max(1, min(100, limit)),
            f"order[chapter]": order,
            "includes[]": ["scanlation_group"],
        }
        if volume:
            params["volume[]"] = volume
        data = _get(f"/manga/{manga_id}/feed", params)
        chapters = data.get("data", [])
        total = data.get("total", 0)
        if not chapters:
            return f"No hay capítulos en idioma '{language}' para este manga."
        lines = [f"## Capítulos ({total} disponibles en '{language}', mostrando {len(chapters)})\n"]
        for ch in chapters:
            a = ch["attributes"]
            vol = f"Vol.{a.get('volume')}" if a.get("volume") else ""
            num = f"Cap.{a.get('chapter','?')}"
            title = a.get("title") or ""
            pub_date = (a.get("publishAt") or "")[:10]
            groups = [
                r["attributes"]["name"]
                for r in ch.get("relationships", [])
                if r["type"] == "scanlation_group" and r.get("attributes")
            ]
            group_str = f" [{', '.join(groups)}]" if groups else ""
            title_str = f" — {title}" if title else ""
            lines.append(f"  {vol} {num}{title_str}{group_str} ({pub_date}) → ID: {ch['id']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error obteniendo capítulos: {e}"


@mcp.tool()
def get_chapter_pages(chapter_id: str, data_saver: bool = False) -> str:
    """
    Obtiene las URLs de las páginas de un capítulo específico.

    Args:
        chapter_id:  UUID del capítulo en MangaDex.
        data_saver:  Si True, devuelve imágenes comprimidas (menor calidad). Default False.

    Returns:
        Lista de URLs directas a las imágenes del capítulo.
    """
    try:
        server = _get(f"/at-home/server/{chapter_id}")
        base = server["baseUrl"]
        ch_hash = server["chapter"]["hash"]
        mode = "dataSaver" if data_saver else "data"
        pages = server["chapter"][mode if not data_saver else "dataSaver"]
        urls = [f"{base}/{mode}/{ch_hash}/{page}" for page in pages]
        lines = [f"## Páginas del capítulo {chapter_id} ({len(urls)} páginas)\n"]
        for i, url in enumerate(urls, 1):
            lines.append(f"  {i:03d}: {url}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error obteniendo páginas: {e}"


@mcp.tool()
def download_manga(
    url_or_id: str,
    save_as: str = "cbz",
    language: str = "en",
    path: str = "",
    start_chapter: str = "",
    end_chapter: str = "",
    start_volume: str = "",
    end_volume: str = "",
    no_oneshot: bool = False,
    replace: bool = False,
) -> str:
    """
    Descarga un manga, capítulo, lista o portada usando mangadex-downloader.

    Args:
        url_or_id:     URL de MangaDex o UUID del manga/capítulo/lista.
        save_as:       Formato de descarga: raw, raw-volume, raw-single,
                       pdf, pdf-volume, pdf-single,
                       cbz (Comic Book ZIP), cbz-volume, cbz-single,
                       cb7, cb7-volume, cb7-single,
                       epub, epub-volume, epub-single.
                       Default "cbz".
        language:      Idioma de traducción (ej: "en", "es"). Default "en".
        path:          Directorio de descarga. Default ~/Manga.
        start_chapter: Capítulo inicial (ej: "1").
        end_chapter:   Capítulo final (ej: "10").
        start_volume:  Volumen inicial (ej: "1").
        end_volume:    Volumen final (ej: "3").
        no_oneshot:    Si True, omite capítulos oneshot.
        replace:       Si True, reemplaza archivos existentes.

    Returns:
        Resultado del proceso de descarga con ruta de destino.
    """
    try:
        # Construir URL si es solo UUID
        dl_url = _to_url(url_or_id)
        dl_path = path.strip() or _DEFAULT_DOWNLOAD_DIR
        Path(dl_path).mkdir(parents=True, exist_ok=True)

        cmd = [_MDX_CLI, dl_url,
               "--save-as", save_as,
               "-lang", language,
               "--path", dl_path,
               "--log-level", "INFO"]
        if start_chapter:
            cmd += ["--start-chapter", start_chapter]
        if end_chapter:
            cmd += ["--end-chapter", end_chapter]
        if start_volume:
            cmd += ["--start-volume", start_volume]
        if end_volume:
            cmd += ["--end-volume", end_volume]
        if no_oneshot:
            cmd += ["--no-oneshot-chapter"]
        if replace:
            cmd += ["--replace"]

        logger.info("mangadex-dl: %s", " ".join(shlex.quote(c) for c in cmd))
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
        output = (result.stdout + result.stderr).strip()
        # Filtrar warnings de requests
        output = "\n".join(l for l in output.splitlines() if "RequestsDependencyWarning" not in l and "warnings.warn" not in l)
        if result.returncode == 0:
            return f"✅ Descarga completada en {dl_path}\n\n{output[-2000:] if len(output) > 2000 else output}"
        else:
            return f"❌ Error en descarga (código {result.returncode})\n\n{output[-2000:]}"
    except subprocess.TimeoutExpired:
        return "❌ Timeout: la descarga tardó más de 10 minutos."
    except Exception as e:
        return f"❌ Error: {e}"


@mcp.tool()
def download_chapter(
    chapter_id: str,
    save_as: str = "cbz",
    path: str = "",
) -> str:
    """
    Descarga un capítulo específico por su ID.

    Args:
        chapter_id: UUID del capítulo en MangaDex.
        save_as:    Formato: raw, pdf, cbz, cb7, epub (y variantes -single). Default "cbz".
        path:       Directorio de descarga. Default ~/Manga.

    Returns:
        Resultado de la descarga.
    """
    url = f"https://mangadex.org/chapter/{chapter_id}"
    return download_manga(url, save_as=save_as, path=path)


@mcp.tool()
def list_tags() -> str:
    """
    Lista todos los tags/géneros disponibles en MangaDex.

    Returns:
        Lista de tags agrupados por categoría (género, tema, formato, contenido).
    """
    try:
        data = _get("/manga/tag")
        groups: dict[str, list[str]] = {}
        for tag in data.get("data", []):
            a = tag["attributes"]
            name = a["name"].get("en", "?")
            group = a.get("group", "other").title()
            groups.setdefault(group, []).append(name)
        lines = ["## Tags disponibles en MangaDex\n"]
        for group, names in sorted(groups.items()):
            lines.append(f"**{group}**: {', '.join(sorted(names))}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error obteniendo tags: {e}"


@mcp.tool()
def list_languages() -> str:
    """
    Lista los idiomas de traducción disponibles para usar con search y download.

    Returns:
        Lista de códigos de idioma soportados por MangaDex.
    """
    # Lista estática con los más comunes — la API no tiene endpoint de idiomas
    langs = {
        "en": "Inglés", "es": "Español (Latinoamérica)", "es-la": "Español (España)",
        "ja": "Japonés", "ja-ro": "Japonés (Romanji)", "zh": "Chino Simplificado",
        "zh-hk": "Chino Tradicional", "ko": "Coreano", "ko-ro": "Coreano (Romanji)",
        "fr": "Francés", "de": "Alemán", "it": "Italiano", "pt": "Portugués",
        "pt-br": "Portugués (Brasil)", "ru": "Ruso", "ar": "Árabe", "pl": "Polaco",
        "tr": "Turco", "nl": "Holandés", "id": "Indonesio", "th": "Tailandés",
        "vi": "Vietnamita", "uk": "Ucraniano", "cs": "Checo", "hu": "Húngaro",
        "ro": "Rumano", "bg": "Búlgaro", "fi": "Finlandés", "sr": "Serbio",
        "ca": "Catalán", "fa": "Persa", "he": "Hebreo", "hi": "Hindi",
    }
    lines = ["## Idiomas disponibles en MangaDex\n"]
    for code, name in sorted(langs.items(), key=lambda x: x[1]):
        lines.append(f"  `{code}` — {name}")
    return "\n".join(lines)


@mcp.tool()
def get_manga_cover(manga_id: str, size: str = "512px") -> str:
    """
    Obtiene la URL de la portada de un manga.

    Args:
        manga_id: UUID o URL del manga en MangaDex.
        size:     Tamaño: "original", "512px" o "256px". Default "512px".

    Returns:
        URL directa a la imagen de portada.
    """
    try:
        manga_id = _extract_id(manga_id)
        data = _get(f"/manga/{manga_id}", {"includes[]": ["cover_art"]})
        m = data.get("data")
        if not m:
            return f"Manga '{manga_id}' no encontrado."
        rels = m.get("relationships", [])
        rel = next((r for r in rels if r["type"] == "cover_art"), None)
        if not rel or not rel.get("attributes"):
            return "No hay portada disponible para este manga."
        fn = rel["attributes"]["fileName"]
        suffix = "" if size == "original" else f".{size}"
        url = f"{CDN}/covers/{manga_id}/{fn}{suffix}"
        title = _title(m["attributes"])
        return f"**{title}**\nPortada ({size}): {url}"
    except Exception as e:
        return f"Error obteniendo portada: {e}"


@mcp.tool()
def random_manga(
    language: str = "en",
    tags: str = "",
    content_rating: str = "safe,suggestive",
) -> str:
    """
    Obtiene un manga aleatorio de MangaDex.

    Args:
        language:       Idioma disponible (ej: "en", "es").
        tags:           Tags obligatorios separados por coma (ej: "Action,Fantasy").
        content_rating: Ratings: safe, suggestive, erotica, pornographic.

    Returns:
        Información de un manga aleatorio.
    """
    try:
        params: dict = {
            "includes[]": ["cover_art", "author", "artist"],
        }
        for rating in [r.strip() for r in content_rating.split(",") if r.strip()]:
            params.setdefault("contentRating[]", [])
            if isinstance(params["contentRating[]"], str):
                params["contentRating[]"] = [params["contentRating[]"]]
            params["contentRating[]"].append(rating)
        if language:
            params["includedTagsMode"] = "AND"
        data = _get("/manga/random", params)
        m = data.get("data")
        if not m:
            return "No se pudo obtener manga aleatorio."
        return _fmt_manga(m)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_manga_list(list_id: str) -> str:
    """
    Obtiene información de una lista/colección de MangaDex.

    Args:
        list_id: UUID de la lista o URL de MangaDex.

    Returns:
        Título de la lista, creador y manga incluidos.
    """
    try:
        list_id = _extract_id(list_id)
        data = _get(f"/list/{list_id}", {"includes[]": ["user", "manga"]})
        lst = data.get("data")
        if not lst:
            return f"Lista '{list_id}' no encontrada."
        a = lst["attributes"]
        rels = lst.get("relationships", [])
        creator = next((r["attributes"]["username"] for r in rels
                        if r["type"] == "user" and r.get("attributes")), "?")
        mangas = [r for r in rels if r["type"] == "manga"]
        lines = [
            f"**{a.get('name','Lista')}**",
            f"ID: {list_id}",
            f"Creado por: {creator} | Visibilidad: {a.get('visibility','?')}",
            f"Manga en la lista: {len(mangas)}\n",
        ]
        for r in mangas[:20]:
            if r.get("attributes"):
                lines.append(f"  - {_title(r['attributes'])} ({r['id']})")
            else:
                lines.append(f"  - {r['id']}")
        if len(mangas) > 20:
            lines.append(f"  ... y {len(mangas) - 20} más")
        return "\n".join(lines)
    except Exception as e:
        return f"Error obteniendo lista: {e}"


@mcp.tool()
def search_author(name: str, limit: int = 5) -> str:
    """
    Busca autores o artistas en MangaDex.

    Args:
        name:  Nombre del autor/artista a buscar.
        limit: Cantidad de resultados (1-20). Default 5.

    Returns:
        Lista de autores con ID, nombre y obras.
    """
    try:
        data = _get("/author", {"name": name, "limit": max(1, min(20, limit)),
                                "includes[]": ["manga"]})
        results = data.get("data", [])
        if not results:
            return f"No se encontró autor '{name}'."
        lines = [f"## Autores para '{name}'\n"]
        for a in results:
            aid = a["id"]
            attrs = a["attributes"]
            aname = attrs.get("name", "?")
            bio = (attrs.get("biography", {}) or {}).get("en", "")[:150]
            mangas = [r for r in a.get("relationships", []) if r["type"] == "manga"]
            lines.append(f"**{aname}**  (ID: {aid})")
            if bio:
                lines.append(f"  {bio}")
            lines.append(f"  Obras: {len(mangas)}")
            lines.append("---")
        return "\n".join(lines)
    except Exception as e:
        return f"Error buscando autor: {e}"


@mcp.tool()
def get_author_manga(author_id: str, limit: int = 10) -> str:
    """
    Lista los manga de un autor/artista específico.

    Args:
        author_id: UUID del autor en MangaDex.
        limit:     Cantidad de resultados (1-50). Default 10.

    Returns:
        Lista de manga del autor con estado y tags.
    """
    try:
        data = _get("/manga", {
            "authorOrArtist": author_id,
            "limit": max(1, min(50, limit)),
            "includes[]": ["cover_art", "author", "artist"],
            "order[followedCount]": "desc",
        })
        results = data.get("data", [])
        total = data.get("total", 0)
        if not results:
            return f"No se encontraron manga para el autor '{author_id}'."
        lines = [f"## Manga del autor (total: {total})\n"]
        for m in results:
            a = m["attributes"]
            title = _title(a)
            status = a.get("status", "?")
            lines.append(f"  - **{title}** [{status}] — {m['id']}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def list_downloaded_manga(path: str = "") -> str:
    """
    Lista el manga descargado localmente.

    Args:
        path: Directorio a listar. Default ~/Manga.

    Returns:
        Lista de archivos/carpetas descargadas con tamaño.
    """
    try:
        dl_path = Path(path.strip() or _DEFAULT_DOWNLOAD_DIR)
        if not dl_path.exists():
            return f"El directorio {dl_path} no existe. Aún no se ha descargado ningún manga."
        items = sorted(dl_path.iterdir())
        if not items:
            return f"El directorio {dl_path} está vacío."
        lines = [f"## Manga descargado en {dl_path}\n"]
        total_size = 0
        for item in items:
            if item.is_dir():
                size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                files = sum(1 for _ in item.rglob("*") if _.is_file())
                lines.append(f"  📁 {item.name}/ ({files} archivos, {size/1e6:.1f} MB)")
            else:
                size = item.stat().st_size
                lines.append(f"  📄 {item.name} ({size/1e6:.1f} MB)")
                total_size += size
        lines.append(f"\nTotal visible: {sum(i.stat().st_size for i in items if i.is_file())/1e6:.1f} MB en archivos directos")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listando directorio: {e}"


# ── Helpers de ID/URL ──────────────────────────────────────────────────────────

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def _extract_id(url_or_id: str) -> str:
    """Extrae UUID de una URL de MangaDex o lo devuelve si ya es UUID."""
    m = _UUID_RE.search(url_or_id)
    return m.group(0) if m else url_or_id.strip()


def _to_url(url_or_id: str) -> str:
    """Convierte UUID a URL de MangaDex si no es ya una URL. Siempre usa /title/<uuid> limpio."""
    uid = _extract_id(url_or_id.strip())
    return f"https://mangadex.org/title/{uid}"


# ── Download live state ────────────────────────────────────────────────────────

_dl_state: dict = {
    "active": False,
    "manga_title": "",
    "chapter_current": 0,
    "chapter_total": 0,
    "volume_current": 0,      # volumen que se está descargando ahora
    "volume_total": 0,        # total de volúmenes
    "volume_label": "",       # e.g. "Vol. 3"
    "volume_chapters": 0,     # capítulos en el volumen actual
    "volumes_done": [],       # lista de volúmenes completados
    "pct": 0.0,
    "bandwidth": "",
    "current_file": "",
    "status": "idle",
    "log": "",
    "dest": "",
}
_dl_version: int = 0
_dl_lock = threading.Lock()
_dl_proc: subprocess.Popen | None = None


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mKJHfABCDsu]|\r", "", text)


_last_chapter_seen: int = -1  # para detectar cambios de capítulo


def _parse_mdl_line(line: str) -> dict:
    global _last_chapter_seen
    line = _strip_ansi(line).strip()
    updates: dict = {}
    if not line:
        return updates
    updates["log"] = line[:250]

    # mangadex-dl: "[INFO] Downloading [Group] Volume. X Chapter. Y page Z"
    m = re.search(r"Chapter\.\s*(\d+(?:\.\d+)?)\s+page\s+(\d+)", line, re.I)
    if m:
        ch = float(m.group(1))
        updates["status"] = "downloading"
        updates["current_file"] = f"Cap {ch} pág {m.group(2)}"
        if int(ch) != _last_chapter_seen:
            _last_chapter_seen = int(ch)
            updates["chapter_current"] = _last_chapter_seen

    # Patrones alternativos X/Y
    if "chapter_current" not in updates:
        for pat in [
            r"chapter[s]?\s+(\d+)\s*/\s*(\d+)",
            r"\((\d+)\s*/\s*(\d+)\)",
            r"(\d+)\s*/\s*(\d+)\s+chapter",
        ]:
            m = re.search(pat, line, re.I)
            if m:
                cur, total = int(m.group(1)), int(m.group(2))
                if 0 < cur <= total:
                    updates["chapter_current"] = cur
                    updates["chapter_total"] = total
                    updates["pct"] = round(cur / total * 100, 1)
                break

    # Total de capítulos encontrados
    m = re.search(r"(?:found|total|got)[:\s]+(\d+)\s+chapter", line, re.I)
    if m:
        updates["chapter_total"] = int(m.group(1))

    # Ancho de banda del tqdm: "NNk/NNNk [time, speed]" o "NN MB/s"
    m = re.search(r"(\d+(?:\.\d+)?)\s*(MB|KB|GB|kB)/s", line, re.I)
    if m:
        updates["bandwidth"] = f"{m.group(1)} {m.group(2)}/s"

    # Archivo actual
    m = re.search(r"(\S+\.(?:jpg|jpeg|png|webp|gif))", line, re.I)
    if m:
        updates["current_file"] = m.group(1).split("/")[-1]

    # Estado
    if re.search(r"completed|finished|success", line, re.I):
        updates["status"] = "completed"
    elif re.search(r"error|failed|exception", line, re.I):
        updates["status"] = "error"
    elif re.search(r"download", line, re.I):
        updates["status"] = "downloading"
    elif re.search(r"fetch|connect|resolv", line, re.I):
        updates["status"] = "fetching"

    return updates


def _bw_monitor(dest: str) -> None:
    """Thread secundario: calcula ancho de banda midiendo cambio de bytes en disco."""
    import time as _t
    dest_path = Path(dest)
    prev_size = 0
    prev_ts = _t.time()
    while True:
        with _dl_lock:
            if not _dl_state["active"]:
                break
        _t.sleep(1.5)
        try:
            size = sum(f.stat().st_size for f in dest_path.rglob("*") if f.is_file())
        except Exception:
            continue
        now = _t.time()
        dt = now - prev_ts
        if dt > 0 and size > prev_size:
            bps = (size - prev_size) / dt
            bw = f"{bps/1e6:.1f} MB/s" if bps >= 1e6 else f"{bps/1e3:.0f} KB/s"
            with _dl_lock:
                global _dl_version
                _dl_state["bandwidth"] = bw
                _dl_version += 1
        prev_size = size
        prev_ts = now


def _run_download_bg(cmds: list[list[str]], manga_title: str, chapter_total: int, dest: str) -> None:
    global _dl_state, _dl_version, _dl_proc
    import time as _t

    # Calcular capítulos por volumen desde los comandos
    # Los cmds llevan --start-volume/--end-volume para filtrar
    vol_total = len(cmds)
    volumes_done: list[str] = []

    with _dl_lock:
        _dl_state.update({
            "active": True,
            "manga_title": manga_title,
            "chapter_current": 0,
            "chapter_total": chapter_total,
            "volume_current": 0,
            "volume_total": vol_total,
            "volume_label": "",
            "volume_chapters": 0,
            "volumes_done": [],
            "pct": 0.0,
            "bandwidth": "",
            "current_file": "",
            "status": "starting",
            "log": f"Iniciando descarga ({vol_total} volúmenes, {chapter_total} caps)...",
            "dest": dest,
        })
        _dl_version += 1

    threading.Thread(target=_bw_monitor, args=(dest,), daemon=True).start()

    try:
        for vol_idx, cmd in enumerate(cmds):
            # Extraer número de volumen del comando
            vol_label = ""
            if "--start-volume" in cmd:
                vi = cmd.index("--start-volume")
                vol_label = f"Vol. {cmd[vi+1]}"

            # Contar capítulos en este volumen contando los ya descargados + estimación
            vol_ch_done = sum(
                len(list((Path(dest) / v).glob("*.cbz")))
                for v in volumes_done
            ) if volumes_done else 0

            with _dl_lock:
                _dl_state.update({
                    "status": "downloading",
                    "volume_current": vol_idx + 1,
                    "volume_label": vol_label or f"lote {vol_idx+1}",
                    "volumes_done": list(volumes_done),
                    "log": f"▶ {vol_label or 'descargando'} ({vol_idx+1}/{vol_total})",
                    "active": True,
                })
                _dl_version += 1

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env={**os.environ, "FORCE_COLOR": "0", "NO_COLOR": "1"},
            )
            with _dl_lock:
                _dl_proc = proc

            vol_ch_seen: set = set()
            for raw in proc.stdout:
                upd = _parse_mdl_line(raw)
                if upd:
                    with _dl_lock:
                        _dl_state.update(upd)
                        _dl_state["active"] = True
                        # Rastrear capítulos únicos vistos en este volumen
                        if "chapter_current" in upd:
                            vol_ch_seen.add(upd["chapter_current"])
                            _dl_state["volume_chapters"] = len(vol_ch_seen)
                        # Recalcular pct global
                        total = _dl_state.get("chapter_total") or 0
                        cur = _dl_state.get("chapter_current") or 0
                        if total > 0 and "pct" not in upd:
                            _dl_state["pct"] = round(cur / total * 100, 1)
                        _dl_version += 1

            proc.wait()
            with _dl_lock:
                _dl_proc = None

            if proc.returncode != 0:
                with _dl_lock:
                    _dl_state.update({
                        "status": "error",
                        "log": f"❌ Error en {vol_label} (código {proc.returncode})",
                        "active": False,
                    })
                    _dl_version += 1
                return

            # Volumen completado
            volumes_done.append(vol_label or f"lote {vol_idx+1}")
            with _dl_lock:
                _dl_state["volumes_done"] = list(volumes_done)
                _dl_state["log"] = f"✓ {vol_label} completado ({len(volumes_done)}/{vol_total})"
                _dl_version += 1

        # Todos los volúmenes completados
        with _dl_lock:
            ch_total = _dl_state["chapter_total"]
            _dl_state.update({
                "status": "completed",
                "pct": 100.0,
                "chapter_current": ch_total,
                "volume_current": vol_total,
                "volumes_done": list(volumes_done),
                "log": f"✅ Descarga completada — {vol_total} volúmenes, {ch_total} caps",
                "active": False,
            })
            _dl_version += 1
        try:
            (Path(_DEFAULT_DOWNLOAD_DIR) / ".dl_resume.json").unlink(missing_ok=True)
        except Exception:
            pass
        # Pequeña pausa para que el SSE emita el estado final, luego resetear a idle
        import time as _t2; _t2.sleep(3)
        with _dl_lock:
            _dl_state.update({
                "active": False, "manga_title": "", "chapter_current": 0,
                "chapter_total": 0, "pct": 0.0, "bandwidth": "",
                "current_file": "", "status": "idle", "log": "", "dest": "",
            })
            _dl_version += 1
    except Exception as exc:
        with _dl_lock:
            _dl_proc = None
            _dl_state.update({"status": "error", "log": f"❌ {exc}", "active": False})
            _dl_version += 1
        import time as _t2; _t2.sleep(3)
        with _dl_lock:
            _dl_state.update({
                "active": False, "manga_title": "", "chapter_current": 0,
                "chapter_total": 0, "pct": 0.0, "bandwidth": "",
                "current_file": "", "status": "idle", "log": "",
            })
            _dl_version += 1


@mcp.tool()
def start_download(
    url_or_id: str,
    language: str = "es",
    save_as: str = "cbz",
    start_chapter: str = "",
    end_chapter: str = "",
) -> str:
    """
    Inicia la descarga de un manga en segundo plano y retorna inmediatamente.
    Usa /live (o get_download_status) para seguir el progreso en tiempo real.

    Args:
        url_or_id:     URL o UUID del manga en MangaDex.
        language:      Idioma (ej: "es", "en"). Default "es".
        save_as:       Formato: cbz, raw, pdf, epub. Default "cbz".
        start_chapter: Capítulo inicial (opcional).
        end_chapter:   Capítulo final (opcional).

    Returns:
        Confirmación de inicio con total de capítulos y destino.
    """
    global _dl_proc
    with _dl_lock:
        if _dl_state.get("active"):
            return f"⚠️ Ya hay una descarga activa: {_dl_state['manga_title']} ({_dl_state['pct']:.0f}%). Usa stop_download() primero."

    # Resolver título a UUID siempre que no sea un UUID puro
    _slug_match = re.search(r"/title/[^/]+/([^/?#]+)", url_or_id)
    _is_pure_uuid = bool(_UUID_RE.fullmatch(url_or_id.strip()))

    if _slug_match:
        # URL con slug: buscar por el título del slug para evitar UUIDs hallucinated
        _raw_slug = _slug_match.group(1)
        # Eliminar sufijos de idioma comunes: -es-la, -es, -en, -ja, -zh, -fr, -pt-br
        _raw_slug = re.sub(r"-(es-la|pt-br|zh-hk|zh-cn|[a-z]{2})$", "", _raw_slug)
        _title_query = _raw_slug.replace("-", " ")
        search_res = _get("/manga", {
            "title": _title_query,
            "limit": 1,
            "order[relevance]": "desc",
            "contentRating[]": ["safe", "suggestive", "erotica", "pornographic"],
        })
        hits = search_res.get("data", [])
        if hits:
            url_or_id = hits[0]["id"]
    elif not _is_pure_uuid and not url_or_id.strip().startswith("http"):
        # Texto libre: buscar por título
        search_res = _get("/manga", {
            "title": url_or_id.strip(),
            "limit": 1,
            "order[relevance]": "desc",
            "contentRating[]": ["safe", "suggestive", "erotica", "pornographic"],
        })
        hits = search_res.get("data", [])
        if not hits:
            return f"❌ No se encontró '{url_or_id}' en MangaDex. Intenta con search_manga() primero."
        url_or_id = hits[0]["id"]

    manga_id = _extract_id(url_or_id)
    dl_url = _to_url(url_or_id)

    # Obtener metadata del manga
    manga_title = manga_id
    chapter_total = 0
    volumes = []
    try:
        data = _get(f"/manga/{manga_id}", {"includes[]": ["cover_art"]})
        m = data.get("data", {})
        manga_title = _title(m.get("attributes", {})) if m else manga_id

        # Obtener volúmenes disponibles en el idioma pedido
        agg = _get(f"/manga/{manga_id}/aggregate", {"translatedLanguage[]": [language]})
        vol_data = agg.get("volumes", {})
        if not vol_data and language != "en":
            agg = _get(f"/manga/{manga_id}/aggregate", {"translatedLanguage[]": ["es-la"]})
            vol_data = agg.get("volumes", {})

        # Ordenar volúmenes: numéricos primero, luego "none"
        def _vol_sort_key(v):
            try: return (0, float(v))
            except: return (1, v)

        volumes = sorted(vol_data.keys(), key=_vol_sort_key)
        chapter_total = sum(v.get("count", 0) for v in vol_data.values())
        if chapter_total == 0:
            ch_data = _get(f"/manga/{manga_id}/feed", {"translatedLanguage[]": language, "limit": 1})
            chapter_total = ch_data.get("total", 0)
    except Exception as _meta_err:
        logger.warning("No se pudo obtener metadata del manga %s: %s", manga_id, _meta_err)

    # Estructura: manga_title/Vol. {volume}/Ch. {chapter}.cbz
    manga_dest = str(Path(_DEFAULT_DOWNLOAD_DIR) / manga_title)
    Path(manga_dest).mkdir(parents=True, exist_ok=True)
    dest = manga_dest

    # Construir lista de comandos: uno por volumen en orden
    def _make_cmd(vol_num=None):
        vol_path = manga_dest + (f"/Vol. {vol_num}" if vol_num and vol_num != "none" else "/Sin volumen")
        c = [_MDX_CLI, dl_url, "--save-as", save_as, "-lang", language,
             "--path", vol_path, "--filename-chapter", "Ch. {chapter}",
             "--log-level", "INFO"]
        if vol_num and vol_num != "none":
            c += ["--start-volume", vol_num, "--end-volume", vol_num]
        if start_chapter:
            c += ["--start-chapter", start_chapter]
        if end_chapter:
            c += ["--end-chapter", end_chapter]
        return c

    def _vol_already_done(vol_num) -> bool:
        """True si el directorio del volumen tiene TODOS los capítulos esperados."""
        if not vol_num or vol_num == "none":
            return False
        vol_dir = Path(manga_dest) / f"Vol. {vol_num}"
        if not vol_dir.is_dir():
            return False
        cbz_count = len(list(vol_dir.glob("*.cbz")))
        if cbz_count == 0:
            return False
        # Comparar con capítulos únicos del aggregate (no "count" que incluye duplicados de grupos)
        chapters_dict = vol_data.get(vol_num, {}).get("chapters", {})
        expected = len(chapters_dict) if chapters_dict else vol_data.get(vol_num, {}).get("count", 0)
        if expected > 0 and cbz_count < expected:
            logger.info("Resume: Vol. %s incompleto (%d/%d caps), se re-descargará", vol_num, cbz_count, expected)
            return False
        return True

    if volumes:
        skipped = [v for v in volumes if _vol_already_done(v)]
        pending = [v for v in volumes if not _vol_already_done(v)]
        cmds = [_make_cmd(v) for v in pending]
        if skipped:
            logger.info("Resume: saltando %d volúmenes ya descargados: %s", len(skipped), skipped)
    else:
        skipped = []
        cmds = [_make_cmd()]  # fallback: todo de una vez

    # Guardar estado de resume para que el scheduler de aia pueda reiniciar si cae el MCP
    _resume_file = Path(_DEFAULT_DOWNLOAD_DIR) / ".dl_resume.json"
    try:
        _resume_file.write_text(json.dumps({
            "url_or_id": dl_url,
            "language": language,
            "save_as": save_as,
            "manga_title": manga_title,
            "chapter_total": chapter_total,
            "start_chapter": start_chapter,
            "end_chapter": end_chapter,
            "volumes": volumes,
        }))
    except Exception:
        pass

    vol_total = len(volumes) if volumes else 1
    vol_pending = len(cmds)
    if skipped:
        last_skipped = skipped[-1]
        next_vol = (int(last_skipped) + 1) if last_skipped not in ("none", "") else "?"
        resume_note = f" (retomando desde Vol. {next_vol}, {len(skipped)} ya descargados)"
    else:
        resume_note = ""
    vol_info = f"{vol_pending}/{vol_total} volúmenes pendientes{resume_note}" if volumes else "sin info de volúmenes"

    if not cmds:
        # Todo ya descargado
        _resume_file = Path(_DEFAULT_DOWNLOAD_DIR) / ".dl_resume.json"
        try:
            _resume_file.unlink(missing_ok=True)
        except Exception:
            pass
        return (
            f"✅ '{manga_title}' ya está completamente descargado ({vol_total} volúmenes).\n"
            f"   Destino: {dest}"
        )

    threading.Thread(
        target=_run_download_bg,
        args=(cmds, manga_title, chapter_total, dest),
        daemon=True,
    ).start()

    live_url = "http://localhost:8009/live"
    return (
        f"▶️ Descarga iniciada en segundo plano\n"
        f"   Manga: {manga_title}\n"
        f"   Capítulos: {chapter_total} en '{language}' · {vol_info}\n"
        f"   Destino: {dest}\n"
        f"[LIVE:{live_url}:manga]"
    )


@mcp.tool()
def stop_download() -> str:
    """Detiene la descarga activa en segundo plano."""
    global _dl_proc
    with _dl_lock:
        proc = _dl_proc
        active = _dl_state.get("active", False)
    if not active or proc is None:
        return "No hay descarga activa."
    try:
        proc.terminate()
        return f"⏹️ Descarga de '{_dl_state['manga_title']}' detenida."
    except Exception as e:
        return f"❌ Error al detener: {e}"


@mcp.tool()
def get_download_status() -> str:
    """Retorna el estado actual de una descarga en progreso o completada."""
    with _dl_lock:
        s = dict(_dl_state)
    if not s["manga_title"]:
        return "No hay descarga activa ni reciente."
    bar_filled = int(s["pct"] / 5)
    bar = "█" * bar_filled + "░" * (20 - bar_filled)
    lines = [
        f"**{s['manga_title']}**",
        f"Estado : {s['status']}",
        f"Progreso: [{bar}] {s['pct']:.1f}%",
        f"Capítulo: {s['chapter_current']}/{s['chapter_total']}",
    ]
    if s["bandwidth"]:
        lines.append(f"Velocidad: {s['bandwidth']}")
    if s["current_file"]:
        lines.append(f"Archivo: {s['current_file']}")
    if s["log"]:
        lines.append(f"Log: {s['log']}")
    return "\n".join(lines)


@mcp.custom_route("/live", methods=["GET"])
async def live_stream(request):
    """SSE endpoint: emite el estado de descarga en tiempo real cada 0.5 s."""
    import asyncio as _asyncio
    from sse_starlette.sse import EventSourceResponse

    async def _gen():
        last_ver = -1
        while True:
            cur_ver = _dl_version
            if cur_ver != last_ver:
                last_ver = cur_ver
                with _dl_lock:
                    state = dict(_dl_state)
                yield {"data": json.dumps(state)}
            else:
                yield {"comment": "keepalive"}
            await _asyncio.sleep(0.5)

    return EventSourceResponse(_gen())
