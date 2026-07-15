#!/usr/bin/env python3
"""
Monitor en vivo de la colección `email` (DB `email`) en MongoDB Atlas.

Muestra en la terminal, a intervalos regulares, cómo crece la tabla:
  - Total de documentos
  - Conteo por `kind` (ej. bci_cartola)
  - Tasa de inserción (docs/seg)
  - Último documento insertado (por _id ObjectId)

Uso:
  python3 scripts/monitor_email_live.py            # cada 3s
  python3 scripts/monitor_email_live.py -i 5       # cada 5s
  python3 scripts/monitor_email_live.py --once     # solo un snapshot

Requiere la var de entorno MONGODB_URI (la levanta desde .env si existe).
"""
from __future__ import annotations

import os
import sys
import time
import argparse
from datetime import datetime
from pathlib import Path

# Cargar .env si existe (sin dependencias externas)
def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip("'\"")
            os.environ.setdefault(key, val)

_load_dotenv()

try:
    from pymongo import MongoClient
    from bson import ObjectId
except ImportError:
    sys.exit("ERROR: instala pymongo -> pip install pymongo[srv]")

MONGODB_URI = os.environ.get("MONGODB_URI")
if not MONGODB_URI:
    sys.exit("ERROR: falta MONGODB_URI en el entorno o en .env")

DB_NAME = "email"
COLL_NAME = "emails"


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def human_delta(seconds: float) -> str:
    if seconds < 0:
        return "?"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}m"
    return f"{seconds/3600:.1f}h"


def snapshot(client: MongoClient):
    coll = client[DB_NAME][COLL_NAME]
    total = coll.count_documents({})
    by_kind = list(coll.aggregate([
        {"$group": {"_id": "$kind", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]))
    last = coll.find_one({}, sort=[("_id", -1)])
    last_ts = None
    if last and isinstance(last.get("_id"), ObjectId):
        last_ts = last["_id"].generation_time
    return total, by_kind, last, last_ts


def main() -> None:
    ap = argparse.ArgumentParser(description="Monitor en vivo de la colección email")
    ap.add_argument("-i", "--interval", type=float, default=3.0, help="segundos entre muestras")
    ap.add_argument("--once", action="store_true", help="solo un snapshot y salir")
    ap.add_argument("--no-clear", action="store_true", help="no limpiar pantalla")
    args = ap.parse_args()

    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
    # Verificar conexión
    try:
        client.admin.command("ping")
    except Exception as e:
        sys.exit(f"ERROR: no se pudo conectar a MongoDB: {e}")

    prev_total = None
    prev_time = None

    while True:
        try:
            total, by_kind, last, last_ts = snapshot(client)
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] ERROR al leer: {e}")
            if args.once:
                return
            time.sleep(args.interval)
            continue

        now = time.time()
        rate = 0.0
        if prev_total is not None and prev_time is not None:
            dt = now - prev_time
            if dt > 0:
                rate = (total - prev_total) / dt

        if not args.no_clear and not args.once:
            clear_screen()

        print("=" * 60)
        print(f"  MONITOR email.email   [{datetime.now():%Y-%m-%d %H:%M:%S}]")
        print("=" * 60)
        print(f"  Total documentos : {total:,}")
        if prev_total is not None:
            diff = total - prev_total
            arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "■")
            print(f"  Variación        : {arrow} {diff:+,}  ({rate:+.2f} docs/s)")
        print("-" * 60)
        print("  Por kind:")
        if by_kind:
            for row in by_kind:
                kind = row["_id"] or "(sin kind)"
                print(f"    {kind:<20} {row['n']:,}")
        else:
            print("    (sin documentos)")
        print("-" * 60)
        if last_ts:
            age = now - last_ts.timestamp()
            print(f"  Último insertado : hace {human_delta(age)}  ({last_ts:%Y-%m-%d %H:%M:%S})")
        else:
            print("  Último insertado : n/d")
        print("=" * 60)
        if args.once:
            return

        prev_total = total
        prev_time = now
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nDetenido.")
            return


if __name__ == "__main__":
    main()
