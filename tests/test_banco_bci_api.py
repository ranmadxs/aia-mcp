"""Tests del cliente HTTP de mcp_banco_bci hacia la API de aia-jobs."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_httpx_client():
    """Mockea httpx.Client para devolver respuestas controladas."""
    with patch("mcp_banco_bci.server.httpx.Client") as mock_cls:
        client = MagicMock()
        mock_cls.return_value.__enter__.return_value = client
        yield client


def test_jobs_post_success(mock_httpx_client):
    from mcp_banco_bci.server import _jobs_post

    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"downloaded": 3, "already_existed": 5}
    mock_httpx_client.post.return_value = resp

    r = _jobs_post("/api/jobs/sync-bci-emails", {"year": 2026, "month": 7})
    assert r == {"downloaded": 3, "already_existed": 5}
    mock_httpx_client.post.assert_called_once()


def test_jobs_post_http_error(mock_httpx_client):
    import httpx as _httpx

    from mcp_banco_bci.server import _jobs_post

    req = _httpx.Request("POST", "http://x/api/jobs/sync-trx")
    resp = _httpx.Response(500, text="internal error", request=req)
    mock_httpx_client.post.return_value = resp

    r = _jobs_post("/api/jobs/sync-trx")
    assert "error" in r
    assert "HTTP 500" in r["error"]


def test_jobs_post_connection_error(mock_httpx_client):
    import httpx as _httpx

    from mcp_banco_bci.server import _jobs_post

    mock_httpx_client.post.side_effect = _httpx.ConnectError("nara down")

    r = _jobs_post("/api/jobs/sync-trx")
    assert "error" in r
    assert "ConnectError" in r["error"]


def test_jobs_get_success(mock_httpx_client):
    from mcp_banco_bci.server import _jobs_get

    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"running": False, "total_cartolas_bci": 7}
    mock_httpx_client.get.return_value = resp

    r = _jobs_get("/api/jobs/status")
    assert r == {"running": False, "total_cartolas_bci": 7}


def test_get_bci_api_health_ok(mock_httpx_client):
    from mcp_banco_bci.server import get_bci_api_health

    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "info": {"title": "aia-jobs API", "version": "0.7.8"},
        "paths": {"/a": {}, "/b": {}, "/c": {}},
    }
    mock_httpx_client.get.return_value = resp

    out = get_bci_api_health()
    assert "API OK" in out
    assert "0.7.8" in out
    assert "3" in out  # 3 endpoints


def test_get_bci_api_health_down(mock_httpx_client):
    import httpx as _httpx

    from mcp_banco_bci.server import get_bci_api_health

    mock_httpx_client.get.side_effect = _httpx.ConnectError("nara down")
    out = get_bci_api_health()
    assert "no responde" in out


def test_discover_aia_jobs_url():
    """Lee /proc/net/route y devuelve http://<gateway>:8080."""
    from io import StringIO

    import mcp_banco_bci.server as srv

    fake_route = (
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t010011AC\t0003\t0\t0\t0\t00000000\t1500\t0\t0\n"  # gw 172.17.0.1
        "eth0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t1500\t0\t0\n"  # subnet route
    )
    with patch("builtins.open", return_value=StringIO(fake_route)):
        url = srv._discover_aia_jobs_url()
    assert url == "http://172.17.0.1:8080"


def test_discover_aia_jobs_url_no_default_route():
    """Si no hay ruta por defecto, devuelve el fallback."""
    from io import StringIO

    import mcp_banco_bci.server as srv

    fake_route = (
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "eth0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t1500\t0\t0\n"
    )
    with patch("builtins.open", return_value=StringIO(fake_route)):
        url = srv._discover_aia_jobs_url()
    assert url == srv._DEFAULT_AIA_JOBS_URL