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

# Directorio de descarga por defecto: ~/Manga
_DEFAULT_DOWNLOAD_DIR = str(Path.home() / "Manga")

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
    """Convierte UUID a URL de MangaDex si no es ya una URL."""
    url_or_id = url_or_id.strip()
    if url_or_id.startswith("http"):
        return url_or_id
    uid = _extract_id(url_or_id)
    return f"https://mangadex.org/title/{uid}"
