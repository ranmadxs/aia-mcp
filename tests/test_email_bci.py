"""Tests del parser de cartolas BCI (extracción de ingresos desde PDF cifrado)."""

import base64
from pathlib import Path

import mcp_email.server as srv
from mcp_email.server import _extract_movements

FIXTURE = Path(__file__).parent / "fixtures" / "cartola_bci_fixture_enc.pdf"
PW = "17536222"


def _fixture_bytes() -> bytes:
    return FIXTURE.read_bytes()


def test_extract_movements_detecta_ingresos_y_cargos():
    movs = _extract_movements(_fixture_bytes(), PW)
    assert len(movs) == 4
    ingresos = [m for m in movs if m["is_ingreso"]]
    cargos = [m for m in movs if not m["is_ingreso"]]
    # 2 ingresos (saldo sube) y 2 cargos (saldo baja)
    assert len(ingresos) == 2
    assert len(cargos) == 2


def test_extract_movements_montos_correctos():
    movs = _extract_movements(_fixture_bytes(), PW)
    by_desc = {m["descripcion"]: m for m in movs}
    # TRANSFER DE RADAR CHILE S -> ingreso 100.000
    radar = [m for m in movs if "RADAR CHILE" in m["descripcion"]][0]
    assert radar["is_ingreso"] is True
    assert radar["monto"] == 100_000
    # ABONO POR TRF -> ingreso 200.000
    abono = [m for m in movs if "ABONO POR TRF" in m["descripcion"]][0]
    assert abono["is_ingreso"] is True
    assert abono["monto"] == 200_000
    # FERRETERIA HIGUER -> cargo 30.000
    ferre = [m for m in movs if "FERRETERIA HIGUER" in m["descripcion"]][0]
    assert ferre["is_ingreso"] is False
    assert ferre["monto"] == 30_000


def test_extract_movements_password_incorrecta_falla():
    try:
        _extract_movements(_fixture_bytes(), "wrongpass")
    except Exception:
        return
    raise AssertionError("debió fallar con contraseña incorrecta")


def test_get_bci_cartola_ingresos_tool_formato(monkeypatch):
    """La tool debe leer el PDF del attachment y listar ingresos."""
    fixture_b64 = base64.b64encode(_fixture_bytes()).decode()

    class _Col:
        def find_one(self, filt, sort=None):
            return {
                "kind": "bci_cartola",
                "period": "2026-07",
                "from_addr": srv.BCI_SENDER,
                "attachments": [{"filename": "cartola.pdf", "data_b64": fixture_b64}],
            }

        def insert_one(self, doc):
            pass

    monkeypatch.setattr(srv, "_get_collection", lambda: _Col())
    monkeypatch.setattr(srv, "BCI_PDF_PASSWORD", PW)
    result = srv.get_bci_cartola_ingresos("2026-07")
    assert "INGRESOS" in result.upper() or "Ingresos" in result
    assert "100.000" in result  # formateado con puntos
    assert "200.000" in result
