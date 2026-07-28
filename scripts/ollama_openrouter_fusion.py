#!/usr/bin/env python3
"""Proxy fusionador OpenAI-compatible para PI Agent.

Exponer en http://localhost:11435/v1 y apuntar PI Agent:
  auth.json  -> "openai": {"type":"openai","key":"x","baseUrl":"http://localhost:11435/v1"}
  settings.json -> "defaultProvider":"openai", "defaultModel":"qwen3:4b"

Comportamiento:
- GET  /v1/models  -> unión de:
    * OpenRouter free  (pricing.prompt=="0" y completion=="0")  etiquetados "[openrouter] <id> (free)"
    * Ollama en nara   (http://nara:11434/v1/models)            etiquetados "[ollamaNara] <id>"
- POST /v1/chat/completions -> si el modelo es de nara y nara responde, va a nara;
    si no, va a OpenRouter con la key real. Así nara apagado => fallback a OpenRouter.
"""
import json
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PI_AUTH = Path.home() / ".pi" / "agent" / "auth.json"
NARA_URL = "http://nara:11434/v1"
OR_URL = "https://openrouter.ai/api/v1"
LISTEN = ("127.0.0.1", 11435)
OR_CACHE_SECONDS = 300

_openrouter_key = ""
_or_cache = {"ts": 0, "models": []}


def _load_or_key():
    global _openrouter_key
    try:
        data = json.loads(PI_AUTH.read_text())
        _openrouter_key = data.get("openrouter", {}).get("key", "")
    except Exception:
        _openrouter_key = ""


def _http_json(url, headers=None, timeout=20, method="GET", body=None):
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    if body is not None:
        req.data = body
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _nara_models():
    try:
        d = _http_json(NARA_URL + "/models", timeout=4)
        return [m["id"] for m in d.get("data", [])]
    except Exception:
        return []


def _openrouter_free_models():
    now = time.time()
    if now - _or_cache["ts"] < OR_CACHE_SECONDS and _or_cache["models"]:
        return _or_cache["models"]
    try:
        d = _http_json(OR_URL + "/models",
                       headers={"Authorization": f"Bearer {_openrouter_key}"},
                       timeout=20)
        free = []
        for m in d.get("data", []):
            p = m.get("pricing", {})
            if p.get("prompt", "0") == "0" and p.get("completion", "0") == "0":
                free.append(m["id"])
        _or_cache["models"] = free
        _or_cache["ts"] = now
        return free
    except Exception:
        return []


def build_models():
    nara = _nara_models()
    or_free = _openrouter_free_models()
    data = []
    for mid in or_free:
        data.append({
            "id": f"[openrouter] {mid} (free)",
            "object": "model",
            "created": 0,
            "owned_by": "openrouter",
            # guardamos el id real para enrutar
            "x_real_id": mid,
        })
    for mid in nara:
        data.append({
            "id": f"[ollamaNara] {mid}",
            "object": "model",
            "created": 0,
            "owned_by": "ollamaNara",
            "x_real_id": mid,
        })
    return {"object": "list", "data": data}


def real_model_id(display_id):
    """El id mostrado en /models lleva prefijo; extrae el real para enrutar."""
    for entry in build_models()["data"]:
        if entry["id"] == display_id:
            return entry["x_real_id"], entry["owned_by"]
    # si llega el id real directo (sin prefijo), asumimos openrouter
    return display_id, "openrouter"


def route_chat(body: bytes):
    req = json.loads(body)
    display = req.get("model", "")
    real_id, owner = real_model_id(display)
    req["model"] = real_id

    # Intentar nara si el modelo es de nara y nara responde
    if owner == "ollamaNara":
        try:
            return _http_json(NARA_URL + "/chat/completions",
                              headers={"Content-Type": "application/json"},
                              method="POST",
                              body=json.dumps(req).encode(),
                              timeout=120), None
        except Exception as e:
            # nara caído: no fallback a openrouter para modelos locales
            return None, f"nara no disponible: {e}"

    # OpenRouter (free u otros)
    try:
        return _http_json(OR_URL + "/chat/completions",
                          headers={
                              "Content-Type": "application/json",
                              "Authorization": f"Bearer {_openrouter_key}",
                          },
                          method="POST",
                          body=json.dumps(req).encode(),
                          timeout=120), None
    except Exception as e:
        return None, f"openrouter error: {e}"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self._send(200, build_models())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") in ("/v1/chat/completions", "/chat/completions"):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            resp, err = route_chat(body)
            if err:
                self._send(502, {"error": err})
            else:
                self._send(200, resp)
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    _load_or_key()
    print(f"Fusion proxy en http://{LISTEN[0]}:{LISTEN[1]}/v1  "
          f"(openrouter key: {'OK' if _openrouter_key else 'FALTA'})")
    ThreadingHTTPServer(LISTEN, Handler).serve_forever()
