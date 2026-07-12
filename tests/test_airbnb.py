"""Tests unitarios para el servidor MCP Airbnb."""

from datetime import datetime

from airbnb.server import _evento_formato_ical, _serialize


def test_serialize_convierte_id_y_datetime():
    doc = {
        "_id": "abc123",
        "event_start": datetime(2026, 7, 12, 14, 0, 0),
        "summary": "Reserva Juan",
        "precio": 50000,
    }
    out = _serialize(doc)
    assert out["id"] == "abc123"
    assert out["event_start"] == "2026-07-12T14:00:00"
    assert out["summary"] == "Reserva Juan"
    assert out["precio"] == 50000
    assert "_id" not in out


def test_serialize_sin_campos_especiales():
    doc = {"summary": "Bloqueo", "estado": "bloqueado"}
    out = _serialize(doc)
    assert out["summary"] == "Bloqueo"
    assert out["estado"] == "bloqueado"


def test_evento_formato_ical_mapea_campos():
    doc = {
        "_id": "res1",
        "event_start": "2026-08-01",
        "event_end": "2026-08-05",
        "summary": "Reserva Test",
        "estado": "reservado",
        "nombre_huesped": "Maria",
        "precio": 120000,
        "days": 4,
    }
    ev = _evento_formato_ical(doc)
    assert ev["id"] == "res1"
    assert ev["start"] == "2026-08-01"
    assert ev["end"] == "2026-08-05"
    assert ev["nombre_huesped"] == "Maria"
    assert ev["precio"] == 120000
    assert ev["days"] == 4
    # Valores por defecto
    assert ev["hora_checkout"] == "18:00"
    assert ev["readonly"] is False


def test_evento_formato_ical_defaults_cuando_faltan():
    doc = {"_id": "x"}
    ev = _evento_formato_ical(doc)
    assert ev["id"] == "x"
    assert ev["estado"] == "bloqueado"
    assert ev["adultos"] == 0
    assert ev["ninos"] == 0
