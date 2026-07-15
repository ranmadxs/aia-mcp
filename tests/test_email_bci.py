"""Tests del parser de cartolas BCI (extracción de ingresos desde PDF cifrado)."""

import base64
from pathlib import Path

import mcp_email.server as srv
from mcp_email.server import BCI_SENDER, _extract_movements
from tests.test_email import _make_bci_email

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
    import anyio
    result = anyio.run(srv.get_bci_cartola_ingresos, "2026-07")
    assert "INGRESOS" in result.upper() or "Ingresos" in result
    assert "100.000" in result  # formateado con puntos
    assert "200.000" in result


def test_extract_period_from_pdf_deriva_mes_cierre():
    """El período se deriva de la fecha de cierre del PDF (al DD-MM-YYYY)."""
    period = srv._extract_period_from_pdf(_fixture_bytes(), PW)
    assert period == "2026-07"


def test_imap_search_bci_usa_mes_correcto(monkeypatch):
    """La búsqueda IMAP debe usar el mes del período (BCI envía dentro del mes X)."""
    captured = {}

    def fake_imap_connect():
        class _Mail:
            def select(self, box):
                return ("OK", [b"1"])

            def search(self, *args):
                captured["criteria"] = args[1:]
                return ("OK", [b"1"])

            def logout(self):
                return ("OK", [b"bye"])

        return _Mail()

    monkeypatch.setattr(srv, "_imap_connect", fake_imap_connect)
    srv._imap_search_bci("2026-02")
    crit = " ".join(captured["criteria"])
    assert srv.BCI_SENDER in crit
    assert "Cuenta Corriente" in crit
    assert "SINCE" in crit and "BEFORE" in crit
    # BCI envía la cartola de feb dentro de feb -> SINCE debe mencionar Feb
    assert "Feb" in crit


def test_get_email_sync_status_lee_estado(monkeypatch):
    """get_email_sync_status (motor genérico) lee el estado sin tocar Yahoo."""

    class _DB:
        def __getitem__(self, name):
            return _Col()

    class _Col:
        def find_one(self, filt, sort=None):
            return {"_id": "email_sync", "running": True, "mode": "bci",
                    "scope": "2026-07..2026-01", "completed": 3, "total": 7,
                    "started_at": "t", "finished_at": None}

        def update_one(self, q, u, upsert=False):
            return None

        @property
        def database(self):
            return _DB()

    monkeypatch.setattr(srv, "_get_collection", lambda: _Col())
    out = srv.get_email_sync_status()
    assert "EN CURSO" in out
    assert "3/7" in out
    assert "bci" in out


def test_fetch_bci_cartola_guarda_periodo_derivado(monkeypatch):
    """Al guardar, el período debe derivarse del PDF, no del mes solicitado."""
    # Email BCI con el PDF fixture (que dice período 2026-07) como adjunto.
    msg = _make_bci_email("2026-07", with_pdf=False)
    from email.mime.application import MIMEApplication
    pdf = MIMEApplication(_fixture_bytes(), name="cartola.pdf")
    pdf.add_header("Content-Disposition", "attachment", filename="cartola.pdf")
    msg.attach(pdf)
    raw = msg.as_bytes()

    saved = {}

    class _Col:
        def find_one(self, filt, sort=None):
            return None

        def update_one(self, filt, upd, upsert=False):
            saved["doc"] = upd["$set"]

    monkeypatch.setattr(srv, "_get_collection", lambda: _Col())
    monkeypatch.setattr(srv, "_imap_search_bci", lambda p: [b"1"])

    class _Mail:
        def select(self, box):
            return ("OK", [b"1"])

        def fetch(self, uid, parts):
            return ("OK", [(None, raw)])

        def logout(self):
            return ("OK", [b"bye"])

    monkeypatch.setattr(srv, "_imap_connect", lambda: _Mail())
    monkeypatch.setattr(srv, "BCI_PDF_PASSWORD", PW)
    doc, hit = srv._fetch_bci_cartola("2026-02")  # solicitado feb, PDF dice julio
    assert hit is False
    assert saved["doc"]["period"] == "2026-07"  # derivado del PDF
