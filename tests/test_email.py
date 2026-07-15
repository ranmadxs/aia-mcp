"""Tests unitarios para el servidor MCP Email (Yahoo + cartolas BCI)."""

from datetime import date, datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import mcp_email.server as srv
from mcp_email.server import (
    BCI_SENDER,
    _fetch_bci_cartola,
    _imap_search_bci,
    _parse_email,
    _resolve_period,
)


def _make_bci_email(period: str, with_pdf: bool = True) -> MIMEMultipart:
    """Construye un correo BCI con (opcional) adjunto PDF para un período."""
    y, m = (int(x) for x in period.split("-"))
    msg = MIMEMultipart()
    msg["From"] = BCI_SENDER
    msg["To"] = "cliente@yahoo.es"
    msg["Subject"] = f"Cartola Cuenta Corriente {period}"
    msg["Message-ID"] = f"<cartola-{period}@bci.cl>"
    msg["Date"] = datetime(y, m, 15, 12, 0, tzinfo=timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )
    msg.attach(MIMEText("Estimado cliente, adjuntamos su cartola.", "plain"))
    if with_pdf:
        pdf = MIMEApplication(b"%PDF-1.4 fake cartola content", name="cartola.pdf")
        pdf.add_header("Content-Disposition", "attachment", filename="cartola.pdf")
        msg.attach(pdf)
    return msg


def test_resolve_period_usa_mes_actual_por_defecto():
    assert _resolve_period("") == date.today().strftime("%Y-%m")
    assert _resolve_period("2026-07") == "2026-07"


def test_parse_email_extrae_adjunto_pdf_en_base64():
    msg = _make_bci_email("2026-07", with_pdf=True)
    doc = _parse_email(msg)
    assert doc["from_addr"] == BCI_SENDER
    assert doc["period"] == "2026-07"
    assert doc["kind"] == "email"
    atts = doc["attachments"]
    assert len(atts) == 1
    a = atts[0]
    assert a["filename"] == "cartola.pdf"
    # El Content-Type real lo define el correo emisor; BCI lo envía como PDF.
    assert a["content_type"] in ("application/pdf", "application/octet-stream")
    assert a["size"] > 0
    # base64 válido y decodificable al contenido original
    import base64

    decoded = base64.b64decode(a["data_b64"])
    assert decoded.startswith(b"%PDF")


def test_parse_email_sin_adjuntos_lista_vacia():
    msg = _make_bci_email("2026-07", with_pdf=False)
    doc = _parse_email(msg)
    assert doc["attachments"] == []


def test_imap_search_bci_construye_ventana_de_fechas(monkeypatch):
    """Verifica que la búsqueda IMAP use FROM + SINCE/BEFORE para el período."""
    captured = {}

    def fake_imap_connect():
        class _Mail:
            def select(self, box):
                return ("OK", [b"1"])

            def search(self, *args):
                captured["criteria"] = args[1:]
                return ("OK", [b"1 2 3"])

            def logout(self):
                return ("OK", [b"bye"])

        return _Mail()

    monkeypatch.setattr(srv, "_imap_connect", fake_imap_connect)
    ids = _imap_search_bci("2026-07")
    assert ids == [b"1", b"2", b"3"]
    crit = " ".join(captured["criteria"])
    assert BCI_SENDER in crit
    assert "SINCE" in crit and "BEFORE" in crit


def test_fetch_bci_cartola_cache_first(monkeypatch):
    """Si está en Mongo, NO toca Yahoo (cache hit)."""
    period = "2026-07"
    cached = {
        "message_id": "<cached@bci.cl>",
        "subject": "Cartola Cuenta Corriente 2026-07",
        "from_addr": BCI_SENDER,
        "period": period,
        "kind": "bci_cartola",
        "attachments": [],
    }
    calls = {"imap": 0}

    class _Col:
        def find_one(self, filt, sort=None):
            assert filt["period"] == period
            return cached

        def update_one(self, filt, upd, upsert=False):
            raise AssertionError("no debe escribir en cache hit")

    monkeypatch.setattr(srv, "_get_collection", lambda: _Col())
    monkeypatch.setattr(srv, "_imap_search_bci", lambda p: calls.__setitem__("imap", 1) or [b"1"])

    doc, hit = _fetch_bci_cartola(period)
    assert hit is True
    assert doc["message_id"] == "<cached@bci.cl>"
    assert calls["imap"] == 0  # Yahoo no fue consultado


def test_fetch_bci_cartola_descarga_si_no_hay_cache(monkeypatch):
    """Si NO está en Mongo, descarga de Yahoo y guarda (cache miss)."""
    period = "2026-07"
    raw = _make_bci_email(period, with_pdf=True).as_bytes()

    class _Col:
        def find_one(self, filt, sort=None):
            return None  # miss

        def update_one(self, filt, upd, upsert=False):
            _Col.saved = upd["$set"]
            return None

    monkeypatch.setattr(srv, "_get_collection", lambda: _Col())
    monkeypatch.setattr(srv, "_imap_search_bci", lambda p: [b"99"])

    class _Mail:
        def select(self, box):
            return ("OK", [b"1"])

        def fetch(self, uid, parts):
            return ("OK", [(None, raw)])

        def logout(self):
            return ("OK", [b"bye"])

    monkeypatch.setattr(srv, "_imap_connect", lambda: _Mail())

    doc, hit = _fetch_bci_cartola(period)
    assert hit is False
    assert doc["period"] == period
    assert doc["kind"] == "bci_cartola"
    assert len(doc["attachments"]) == 1
    assert _Col.saved["period"] == period  # se guardó en Mongo


def test_fetch_bci_cartola_force_refresh_ignora_cache(monkeypatch):
    """force_refresh=True debe descargar aunque exista en cache."""
    period = "2026-07"
    raw = _make_bci_email(period, with_pdf=False).as_bytes()
    monkeypatch.setattr(srv, "_get_collection", lambda: None)  # sin Mongo -> fuerza Yahoo
    monkeypatch.setattr(srv, "_imap_search_bci", lambda p: [b"99"])

    class _Mail:
        def select(self, box):
            return ("OK", [b"1"])

        def fetch(self, uid, parts):
            return ("OK", [(None, raw)])

        def logout(self):
            return ("OK", [b"bye"])

    monkeypatch.setattr(srv, "_imap_connect", lambda: _Mail())
    doc, hit = _fetch_bci_cartola(period, force_refresh=True)
    assert hit is False
    assert doc["period"] == period


def test_sync_emails_from_usa_search_by_from_y_dedup(monkeypatch):
    """El motor genérico _do_sync busca por FROM y marca cartolas BCI."""
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    def _mk(mid, frm, subject, with_pdf=False):
        if with_pdf:
            m = MIMEMultipart()
            m.attach(MIMEText("hola"))
            pdf = MIMEApplication(b"%PDF-1.4 cartola", name="cartola.pdf")
            pdf.add_header("Content-Disposition", "attachment", filename="cartola.pdf")
            m.attach(pdf)
        else:
            m = MIMEText("hola")
        m["From"] = frm
        m["Subject"] = subject
        m["Message-ID"] = mid
        return m.as_bytes()

    raws = {
        b"10": _mk("<a@x.com>", "viejo@x.com", "antiguo"),
        b"11": _mk("<b@x.com>", "bcimail@bci.cl", "Cartola Cuenta Corriente", with_pdf=True),
        b"12": _mk("<c@x.com>", "viejo@x.com", "otro"),
    }
    seen = {"<a@x.com>"}  # ya existe en Mongo

    class _Col:
        def find_one(self, filt, sort=None):
            return {"message_id": filt["message_id"]} if filt["message_id"] in seen else None

        def update_one(self, filt, upd, upsert=False):
            _Col.upserts.append((filt.get("message_id"), upd["$set"].get("kind")))
            return None

        @property
        def database(self):
            return self

        def __getitem__(self, name):
            return self

    _Col.upserts = []

    class _Mail:
        def select(self, box):
            return ("OK", [b"1"])

        def search(self, *args):
            assert 'FROM "viejo@x.com"' in args[1]
            return ("OK", [b"10 11 12"])

        def fetch(self, uid, parts):
            return ("OK", [(None, raws[uid])])

        def logout(self):
            return ("OK", [b"bye"])

    monkeypatch.setattr(srv, "_get_collection", lambda: _Col())
    monkeypatch.setattr(srv, "_imap_connect", lambda: _Mail())
    monkeypatch.setattr(srv, "_extract_period_from_pdf", lambda pdf, pw: "2026-01")

    srv._do_sync('FROM "viejo@x.com"', "from", "viejo@x.com", 500)
    # <a> duplicado (omitido), <b> cartola BCI (kind bci_cartola), <c> email
    kinds = dict(_Col.upserts)
    assert kinds.get("<b@x.com>") == "bci_cartola"
    assert kinds.get("<c@x.com>") == "email"
    assert "<a@x.com>" not in kinds  # duplicado no se reescribe


def test_sync_emails_since_construye_search_since(monkeypatch):
    """sync_emails_since usa IMAP SEARCH SINCE/BEFORE y corre en background."""
    captured = {}

    # Captura los argumentos con los que se llamaria al motor generico.
    def _fake_do_sync(criteria, mode, scope, limit):
        captured["criteria"] = criteria
        captured["mode"] = mode

    monkeypatch.setattr(srv, "_do_sync", _fake_do_sync)
    monkeypatch.setattr(srv, "_read_sync_state", lambda: {"running": False})
    # Captura la coroutine de background en vez de lanzarla.
    import asyncio
    monkeypatch.setattr(asyncio, "create_task", lambda coro: captured.setdefault("task", coro))

    out = asyncio.run(srv.sync_emails_since("2025-12-01", "2026-07-15"))
    assert "background" in out
    # Ejecuta la tarea de background capturada.
    asyncio.run(captured["task"])
    assert captured["mode"] == "since"
    assert "SINCE" in captured["criteria"] and "BEFORE" in captured["criteria"]
    assert "01-Dec-2025" in captured["criteria"]
    assert "15-Jul-2026" in captured["criteria"]
