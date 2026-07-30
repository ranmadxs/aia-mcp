"""Banco BCI — consulta de cartolas y movimientos desde MongoDB (bci.cartolas)."""

import os
from datetime import date

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI", "")
DB_NAME = "bci"
COLLECTION = "cartolas"

mcp = FastMCP("banco_bci")


def _get_collection():
    if not MONGODB_URI:
        return None
    try:
        from pymongo import MongoClient
        client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
        return client[DB_NAME][COLLECTION]
    except Exception:
        return None


@mcp.tool()
def banco_bci(period: str = "") -> str:
    """
    Obtiene todas las cartolas BCI (Cuenta Corriente) y sus movimientos
    del período indicado desde MongoDB.

    Args:
        period: Período "YYYY-MM" (ej: "2026-07"). Vacío = mes actual.

    Returns:
        Lista de cartolas del período con movimiento resumido o mensaje.
    """
    col = _get_collection()
    if col is None:
        return "❌ MongoDB no disponible (MONGODB_URI no configurado)."

    if not period:
        y, m = date.today().year, date.today().month
        period = f"{y:04d}-{m:02d}"

    docs = list(col.find({"period": period}).sort("n_cuenta", 1))
    if not docs:
        return f"⚠️ No hay cartolas en bci.cartolas para el período {period}."

    lines = [f"## Cartolas BCI {period}  ({len(docs)} encontrada(s))\n"]
    for d in docs:
        n_cuenta = d.get("n_cuenta", "")
        total = d.get("total_movimientos", 0)
        subject = d.get("subject", "")
        bci_cartola_period = d.get("bci_cartola_period", d.get("period", ""))

        lines.append(f"### {subject} ({n_cuenta})")
        lines.append(f"- **Período cartola**: {bci_cartola_period}")
        lines.append(f"- **Movimientos totales**: {total}")

        movs = d.get("movimientos") or []
        if movs:
            abonos = [m for m in movs if m.get("abono") and m["abono"] > 0]
            cargos = [m for m in movs if m.get("cargo") and m["cargo"] > 0]
            total_abonos = sum(m["abono"] for m in abonos)
            total_cargos = sum(m["cargo"] for m in cargos)
            lines.append(
                f"- **Abonos**: {len(abonos)} (total ${total_abonos:,.0f})"
            )
            lines.append(
                f"- **Cargos**: {len(cargos)} (total ${total_cargos:,.0f})"
            )

            if len(abonos) <= 20 or len(cargos) <= 20:
                lines.append("\n**Abonos:**")
                for m in abonos:
                    lines.append(
                        f"  - {m.get('fecha', '?')}  {m.get('descripcion', '')}"
                        f"  → ${m['abono']:,.0f}"
                    )
                lines.append("\n**Cargos (últimos 20):**")
                for m in cargos[-20:]:
                    lines.append(
                        f"  - {m.get('fecha', '?')}  {m.get('descripcion', '')}"
                        f"  → ${m['cargo']:,.0f}"
                    )
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def banco_bci_list_periods() -> str:
    """
    Lista los períodos disponibles en bci.cartolas.

    Returns:
        Lista de períodos con cantidad de cartolas por período.
    """
    col = _get_collection()
    if col is None:
        return "❌ MongoDB no disponible (MONGODB_URI no configurado)."

    pipeline = [
        {"$group": {"_id": "$period", "count": {"$sum": 1}}},
        {"$sort": {"_id": -1}},
        {"$limit": 24},
    ]
    results = list(col.aggregate(pipeline))
    if not results:
        return "No hay períodos en bci.cartolas."

    lines = ["## Períodos en bci.cartolas\n"]
    for r in results:
        lines.append(f"- **{r['_id']}**: {r['count']} cartola(s)")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")