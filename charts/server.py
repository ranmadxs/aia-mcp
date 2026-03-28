"""Servidor MCP Charts - genera gráficos financieros para modo airbnb."""

import base64
import json
import os
import tempfile
from datetime import datetime, timedelta, date as date_t
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # backend no interactivo
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import tomllib
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "")
AIRBNB_DB = os.getenv("AIRBNB_DB", "airbnb-db")

mcp = FastMCP(
    "charts",
    host=os.environ.get("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("FASTMCP_CHARTS_PORT", "8007")),
)

_pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
if _pyproject.exists():
    with open(_pyproject, "rb") as f:
        mcp._mcp_server.version = tomllib.load(f)["tool"]["poetry"]["version"]

# ── MongoDB ────────────────────────────────────────────────────────────────────
_mongo_client = None
_mongo_db = None


def _get_db():
    global _mongo_client, _mongo_db
    if not MONGODB_URI:
        return None
    try:
        from pymongo import MongoClient
        if _mongo_client is None:
            _mongo_client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=3000)
            _mongo_client.admin.command("ping")
            _mongo_db = _mongo_client[AIRBNB_DB]
        return _mongo_db
    except Exception:
        _mongo_client = None
        return None


def _mes_rango(mes: int, anio: int) -> tuple[str, str]:
    inicio = date_t(anio, mes, 1)
    if mes == 12:
        fin = date_t(anio + 1, 1, 1)
    else:
        fin = date_t(anio, mes + 1, 1)
    return inicio.isoformat(), fin.isoformat()


CATEGORIAS = ["gasolina", "aseo", "agua", "otros", "internet"]
COLECCIONES = {
    "gasolina": "gastos_gasolina",
    "aseo": "gastos_aseo",
    "agua": "gastos_agua",
    "otros": "gastos_otros",
    "internet": "gastos_internet",
}
COLORES = {
    "gasolina": "#e74c3c",
    "aseo":     "#3498db",
    "agua":     "#2ecc71",
    "otros":    "#f39c12",
    "internet": "#9b59b6",
}
MESES_ES = ["", "Ene", "Feb", "Mar", "Abr", "May", "Jun",
            "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def _gastos_mes_raw(mes: int, anio: int) -> dict:
    db = _get_db()
    if db is None:
        return {}
    inicio, fin = _mes_rango(mes, anio)
    query = {"fecha_pago": {"$gte": inicio, "$lt": fin}}
    return {
        cat: sum(d.get("valor", 0) for d in db[col].find(query))
        for cat, col in COLECCIONES.items()
    }


def _ingresos_mes_raw(mes: int, anio: int) -> dict:
    db = _get_db()
    if db is None:
        return {"arriendo": 0, "tinaja": 0}
    inicio_mes = date_t(anio, mes, 1)
    fin_mes = date_t(anio + 1, 1, 1) if mes == 12 else date_t(anio, mes + 1, 1)
    cursor = db["reservas"].find({
        "event_start": {"$lt": fin_mes.isoformat()},
        "event_end": {"$gte": inicio_mes.isoformat()},
        "estado": "reservado",
    })
    arriendo = tinaja = 0
    for doc in cursor:
        try:
            ev_start = date_t.fromisoformat(doc["event_start"])
            ev_end = date_t.fromisoformat(doc["event_end"])
        except Exception:
            continue
        dias_totales = max(1, (ev_end - ev_start).days + 1)
        overlap_start = max(ev_start, inicio_mes)
        overlap_end = min(ev_end + timedelta(days=1), fin_mes)
        prop = max(0, (overlap_end - overlap_start).days) / dias_totales
        arriendo += round((doc.get("precio") or 0) * prop)
        extra = doc.get("extra_valor") or 0
        if "tinaja" in (doc.get("extra_concepto") or "").lower():
            tinaja += round(extra * prop)
    return {"arriendo": arriendo, "tinaja": tinaja}


def _save_fig(fig) -> str:
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False, prefix="aia_chart_")
    fig.savefig(tmp.name, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return tmp.name


def _fmt_clp(x, _=None) -> str:
    if x >= 1_000_000:
        return f"${x/1_000_000:.1f}M"
    if x >= 1_000:
        return f"${x/1_000:.0f}k"
    return f"${int(x)}"


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def grafico_gastos_mes(mes: int, anio: Optional[int] = None) -> str:
    """
    Genera un gráfico de barras con los gastos por categoría de un mes.

    Args:
        mes: Número de mes (1-12).
        anio: Año (ej: 2026). Si no se indica usa el año actual.

    Returns:
        Marcador [AIA_IMG:ruta] que amanda-IA convierte en imagen inline.
    """
    if not anio:
        anio = datetime.now().year

    gastos = _gastos_mes_raw(mes, anio)
    if not gastos:
        return json.dumps({"error": "No hay datos de gastos o MongoDB no disponible"})

    cats = [c for c in CATEGORIAS if gastos.get(c, 0) > 0]
    valores = [gastos[c] for c in cats]
    colores = [COLORES[c] for c in cats]

    fig, ax = plt.subplots(figsize=(8, 4.5), facecolor="#1e1e2e")
    ax.set_facecolor("#1e1e2e")

    bars = ax.bar(cats, valores, color=colores, width=0.6, edgecolor="#2e2e4e", linewidth=0.8)

    # Etiquetas encima de cada barra
    for bar, val in zip(bars, valores):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + max(valores) * 0.02,
            _fmt_clp(val),
            ha="center", va="bottom", fontsize=9, color="white", fontweight="bold",
        )

    total = sum(valores)
    ax.set_title(
        f"Gastos {MESES_ES[mes]} {anio}  —  Total: {_fmt_clp(total)}",
        color="white", fontsize=12, fontweight="bold", pad=12,
    )
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_fmt_clp))
    ax.tick_params(colors="white", labelsize=9)
    ax.spines[:].set_visible(False)
    ax.set_ylim(0, max(valores) * 1.18)
    for spine in ax.spines.values():
        spine.set_edgecolor("#444")

    fig.tight_layout()
    path = _save_fig(fig)
    return f"[AIA_IMG:{path}]"


@mcp.tool()
def grafico_comparar_meses(meses: str, anio: Optional[int] = None) -> str:
    """
    Genera un gráfico de barras agrupadas comparando gastos totales de varios meses.

    Args:
        meses: Meses separados por coma, ej: "1,2,3" para enero, febrero, marzo.
        anio: Año (ej: 2026). Si no se indica usa el año actual.

    Returns:
        Marcador [AIA_IMG:ruta] que amanda-IA convierte en imagen inline.
    """
    if not anio:
        anio = datetime.now().year

    try:
        lista_meses = [int(m.strip()) for m in meses.split(",") if m.strip()]
    except ValueError:
        return json.dumps({"error": f"Formato inválido para meses: {meses}"})

    data = {}
    for m in lista_meses:
        data[m] = _gastos_mes_raw(m, anio)

    if not data:
        return json.dumps({"error": "No hay datos"})

    x = [MESES_ES[m] for m in lista_meses]
    totales = [sum(data[m].values()) for m in lista_meses]

    fig, ax = plt.subplots(figsize=(9, 5), facecolor="#1e1e2e")
    ax.set_facecolor("#1e1e2e")

    # Barras apiladas por categoría
    bottom = [0] * len(lista_meses)
    for cat in CATEGORIAS:
        vals = [data[m].get(cat, 0) for m in lista_meses]
        if any(v > 0 for v in vals):
            ax.bar(x, vals, bottom=bottom, label=cat.capitalize(),
                   color=COLORES[cat], width=0.55, edgecolor="#2e2e4e", linewidth=0.5)
            bottom = [b + v for b, v in zip(bottom, vals)]

    # Total encima de cada barra
    for i, total in enumerate(totales):
        ax.text(i, total + max(totales) * 0.02, _fmt_clp(total),
                ha="center", va="bottom", fontsize=9, color="white", fontweight="bold")

    ax.set_title(f"Comparativa gastos {anio}", color="white", fontsize=12,
                 fontweight="bold", pad=12)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_fmt_clp))
    ax.tick_params(colors="white", labelsize=9)
    ax.spines[:].set_visible(False)
    ax.set_ylim(0, max(totales) * 1.20)
    ax.legend(loc="upper right", facecolor="#2e2e4e", labelcolor="white",
              edgecolor="#444", fontsize=8)

    fig.tight_layout()
    path = _save_fig(fig)
    return f"[AIA_IMG:{path}]"


@mcp.tool()
def grafico_rentabilidad_mes(mes: int, anio: Optional[int] = None) -> str:
    """
    Genera un gráfico de barras con ingresos vs gastos del mes, mostrando el neto.

    Args:
        mes: Número de mes (1-12).
        anio: Año (ej: 2026). Si no se indica usa el año actual.

    Returns:
        Marcador [AIA_IMG:ruta] que amanda-IA convierte en imagen inline.
    """
    if not anio:
        anio = datetime.now().year

    gastos = _gastos_mes_raw(mes, anio)
    ingresos = _ingresos_mes_raw(mes, anio)

    total_gastos = sum(gastos.values())
    total_ingresos = ingresos["arriendo"] + ingresos["tinaja"]
    neto = total_ingresos - total_gastos

    fig, ax = plt.subplots(figsize=(7, 4.5), facecolor="#1e1e2e")
    ax.set_facecolor("#1e1e2e")

    labels = ["Ingresos", "Gastos", "Neto"]
    valores = [total_ingresos, total_gastos, neto]
    colores = ["#2ecc71", "#e74c3c", "#3498db" if neto >= 0 else "#e67e22"]

    bars = ax.bar(labels, valores, color=colores, width=0.5,
                  edgecolor="#2e2e4e", linewidth=0.8)

    for bar, val in zip(bars, valores):
        y = bar.get_height() if val >= 0 else 0
        ax.text(bar.get_x() + bar.get_width() / 2,
                y + abs(max(valores)) * 0.02,
                _fmt_clp(val),
                ha="center", va="bottom", fontsize=10, color="white", fontweight="bold")

    margen = round(neto / total_ingresos * 100, 1) if total_ingresos > 0 else 0
    ax.set_title(
        f"Rentabilidad {MESES_ES[mes]} {anio}  —  Margen: {margen}%",
        color="white", fontsize=12, fontweight="bold", pad=12,
    )
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_fmt_clp))
    ax.tick_params(colors="white", labelsize=9)
    ax.spines[:].set_visible(False)
    ax.set_ylim(0, max(valores) * 1.20)

    fig.tight_layout()
    path = _save_fig(fig)
    return f"[AIA_IMG:{path}]"


@mcp.tool()
def grafico_generico(
    titulo: str,
    labels: str,
    valores: str,
    tipo: str = "bar",
    unidad: str = "",
    color: str = "#3498db",
) -> str:
    """
    Genera un gráfico con datos arbitrarios. Úsalo con datos de CUALQUIER modo.

    Args:
        titulo: Título del gráfico.
        labels: Etiquetas del eje X separadas por coma. Ej: "10:00,10:01,10:02"
        valores: Valores numéricos separados por coma. Ej: "80.5,79.3,78.1"
        tipo: Tipo de gráfico: "bar" (barras) o "line" (línea). Default: "bar".
        unidad: Sufijo para los valores en el eje Y. Ej: "L", "%", "cm", "$".
        color: Color principal en hex. Default: "#3498db" (azul).

    Returns:
        Marcador [AIA_IMG:ruta] que amanda-IA convierte en imagen inline.
    """
    try:
        label_list = [lb.strip() for lb in labels.split(",") if lb.strip()]
        valor_list  = [float(v.strip()) for v in valores.split(",") if v.strip()]
    except ValueError as e:
        return json.dumps({"error": f"Formato inválido: {e}"})

    if not label_list or not valor_list:
        return json.dumps({"error": "labels y valores no pueden estar vacíos"})

    n = min(len(label_list), len(valor_list))
    label_list = label_list[:n]
    valor_list  = valor_list[:n]

    fig, ax = plt.subplots(figsize=(max(6, n * 0.7 + 2), 4.5), facecolor="#1e1e2e")
    ax.set_facecolor("#1e1e2e")

    if tipo == "line":
        ax.plot(range(n), valor_list, color=color, linewidth=2, marker="o",
                markersize=4, markerfacecolor=color)
        ax.fill_between(range(n), valor_list, alpha=0.15, color=color)
        ax.set_xticks(range(n))
        ax.set_xticklabels(label_list, rotation=45 if n > 8 else 0, ha="right")
    else:
        bars = ax.bar(label_list, valor_list, color=color, width=0.6,
                      edgecolor="#2e2e4e", linewidth=0.8)
        max_v = max(abs(v) for v in valor_list) if valor_list else 1
        for bar, val in zip(bars, valor_list):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max_v * 0.02,
                f"{val:g}{unidad}",
                ha="center", va="bottom", fontsize=8, color="white", fontweight="bold",
            )

    def _fmt_unidad(x, _=None):
        return f"{x:g}{unidad}"

    ax.set_title(titulo, color="white", fontsize=12, fontweight="bold", pad=12)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_fmt_unidad))
    ax.tick_params(colors="white", labelsize=8)
    ax.spines[:].set_visible(False)
    if valor_list:
        vmax = max(valor_list)
        vmin = min(valor_list)
        margin = (vmax - vmin) * 0.15 or abs(vmax) * 0.15 or 1
        ax.set_ylim(min(0, vmin - margin), vmax + margin * 2)

    fig.tight_layout()
    path = _save_fig(fig)
    return f"[AIA_IMG:{path}]"
