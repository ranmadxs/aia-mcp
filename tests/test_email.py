"""Tests unitarios para el servidor MCP Email (Yahoo + MongoDB)."""

from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import mcp_email.server as srv
from mcp_email.server import _parse_email


def _make_email(subject: str = "Test", with_pdf: bool = False) -> MIMEMultipart:
    """Construye un correo simple (sin BCI)."""
    msg = MIMEMultipart()
    msg["From"] = "test@example.com"
    msg["To"] = "cliente@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{subject}@example.com>"
    msg.attach(MIMEText("Cuerpo del mensaje.", "plain"))
    if with_pdf:
        pdf = MIMEApplication(b"%PDF-1.4 fake pdf content", name="doc.pdf")
        pdf.add_header("Content-Disposition", "attachment", filename="doc.pdf")
        msg.attach(pdf)
    return msg


def test_parse_email_extrae_adjunto_pdf_en_base64():
    msg = _make_email("Test with PDF", with_pdf=True)
    doc = _parse_email(msg)
    assert doc["from_addr"] == "test@example.com"
    assert doc["subject"] == "Test with PDF"
    assert doc["kind"] == "email"
    atts = doc["attachments"]
    assert len(atts) == 1
    a = atts[0]
    assert a["filename"] == "doc.pdf"
    assert a["content_type"] in ("application/pdf", "application/octet-stream")
    assert a["size"] > 0
    import base64

    decoded = base64.b64decode(a["data_b64"])
    assert decoded.startswith(b"%PDF")


def test_parse_email_sin_adjuntos_lista_vacia():
    msg = _make_email("Test plain", with_pdf=False)
    doc = _parse_email(msg)
    assert doc["attachments"] == []


def test_parse_email_maneja_mime_simple():
    msg = MIMEText("Solo texto plano.")
    msg["From"] = "alice@example.com"
    msg["Subject"] = "Solo texto"
    msg["Message-ID"] = "<alice@example.com>"
    doc = _parse_email(msg)
    assert doc["from_addr"] == "alice@example.com"
    assert doc["attachments"] == []
    assert "texto plano" in doc["body_text"]


def test_fetch_one_reintenta_y_reconecta_en_error_ssl(monkeypatch):
    """_fetch_one reintenta y reconecta ante SSL BAD_LENGTH."""
    class _SSLBad(Exception):
        pass

    class _BadMail:
        def __init__(self):
            self.calls = 0

        def fetch(self, uid, parts):
            self.calls += 1
            raise _SSLBad("SSL: BAD_LENGTH")

        def logout(self):
            pass

    class _GoodMail:
        def __init__(self):
            self.calls = 0

        def fetch(self, uid, parts):
            self.calls += 1
            return ("OK", [(None, b"From: x@y.com\nSubject: a\nMessage-ID: <m@x>\n\n")])

        def logout(self):
            pass

    bad = _BadMail()
    good = _GoodMail()
    monkeypatch.setattr(srv, "_imap_connect", lambda: good)
    raw = srv._fetch_one(bad, b"1")
    assert raw is not None
    assert b"Message-ID" in raw
    assert bad.calls == 1
    assert good.calls == 1