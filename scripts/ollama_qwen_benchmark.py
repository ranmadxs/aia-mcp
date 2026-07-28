#!/usr/bin/env python3
import argparse
import json
import re
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


MODELS = ["qwen3:4b", "qwen2.5:7b", "qwen2.5:3b", "qwen2.5:0.5b"]

TESTS = [
    {
        "id": "hola",
        "kind": "generate",
        "prompt": (
            "Responde en español con una sola frase breve. "
            "Saluda y confirma que estás funcionando."
        ),
        "max_tokens": 80,
    },
    {
        "id": "suma",
        "kind": "generate",
        "prompt": (
            "Calcula exactamente 437 + 586. "
            "Responde sólo con el resultado y una explicación mínima en español."
        ),
        "max_tokens": 80,
        "expected_regex": r"\b1023\b",
    },
    {
        "id": "codigo",
        "kind": "generate",
        "prompt": (
            "Escribe una función Python llamada es_primo(n) que devuelva True si n "
            "es primo y False si no. Responde sólo con el código."
        ),
        "max_tokens": 220,
        "quality_terms": ["def es_primo", "return", "False", "True"],
    },
    {
        "id": "espanol_operativo",
        "kind": "generate",
        "prompt": (
            "En español chileno neutro, dame 3 pasos concretos para verificar si "
            "un servicio systemd llamado ollama está activo. No uses más de 70 palabras."
        ),
        "max_tokens": 140,
        "quality_terms": ["systemctl", "ollama", "journalctl"],
    },
    {
        "id": "tool_call",
        "kind": "chat_tools",
        "prompt": (
            "Necesito saber el estado de un servicio Linux. Usa la herramienta "
            "get_service_status con service_name='ollama'."
        ),
        "max_tokens": 120,
        "tool_name": "get_service_status",
    },
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_service_status",
            "description": "Obtiene el estado de un servicio systemd.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "Nombre del servicio systemd.",
                    }
                },
                "required": ["service_name"],
            },
        },
    }
]


def post_json(base_url, path, payload, timeout):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    wall_ms = (time.perf_counter() - start) * 1000
    return json.loads(raw), wall_ms


def get_json(base_url, path, timeout):
    req = urllib.request.Request(base_url.rstrip("/") + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ns_to_ms(value):
    return round((value or 0) / 1_000_000, 2)


def tokens_per_second(count, duration_ns):
    if not count or not duration_ns:
        return 0.0
    return round(count / (duration_ns / 1_000_000_000), 2)


def add_check(checks, ok, label, points, awarded=None):
    if awarded is None:
        awarded = points if ok else 0
    icon = "✅" if ok else "❌"
    checks.append(f"{icon} {label} (+{awarded}/{points})")
    return awarded


def add_partial_check(checks, awarded, points, label):
    if awarded == points:
        icon = "✅"
    elif awarded > 0:
        icon = "⚠️"
    else:
        icon = "❌"
    checks.append(f"{icon} {label} (+{awarded}/{points})")
    return awarded


def _extract_final_text(response):
    """Devuelve el texto útil de la respuesta.

    Modelos sin thinking (qwen2.5) usan 'response'. Modelos con thinking
    (qwen3) pueden dejar 'response' vacío y poner la respuesta final dentro
    de 'thinking' (típicamente tras el marcador </think:6124c78e>). Este fallback
    soporta ambos sin alterar el scoring de los modelos anteriores.
    """
    text = (response.get("response") or "").strip()
    if text:
        return text, False
    thinking = (response.get("thinking") or "").strip()
    if not thinking:
        return "", False
    # qwen3 cierra el bloque de razonamiento con </think:6124c78e> y luego responde
    if "</think:6124c78e>" in thinking:
        after = thinking.split("</think:6124c78e>", 1)[1].strip()
        if after:
            return after, True
    # sin marcador: la respuesta suele ser el último párrafo del thinking
    last_para = [p for p in thinking.splitlines() if p.strip()]
    if last_para:
        return last_para[-1].strip(), True
    return thinking, True


def score_generate(test, response):
    text, from_thinking = _extract_final_text(response)
    score = 0
    notes = []
    checks = []

    has_text = bool(text)
    score += add_check(checks, has_text, "respuesta final presente", 1)
    if not has_text and response.get("thinking"):
        notes.append("sin respuesta final; consumió tokens en thinking")
    elif from_thinking:
        checks.append("🧠 respuesta extraída de thinking")

    length_ok = has_text and len(text) <= 1200
    score += add_check(checks, length_ok, "respuesta dentro de largo esperado", 1)
    if has_text and not length_ok:
        notes.append("respuesta larga")
    elif response.get("thinking"):
        checks.append("🧠 thinking presente")

    expected = test.get("expected_regex")
    if expected:
        if re.search(expected, text):
            score += add_check(checks, True, f"contiene resultado esperado `{expected}`", 3)
        else:
            score += add_check(checks, False, f"contiene resultado esperado `{expected}`", 3)
            notes.append("no contiene resultado esperado")
        return min(score, 5), notes, checks

    terms = test.get("quality_terms") or []
    if terms:
        found = [term for term in terms if term.lower() in text.lower()]
        missing = [term for term in terms if term.lower() not in text.lower()]
        awarded = min(3, len(found))
        score += add_partial_check(
            checks,
            awarded,
            3,
            f"términos esperados {len(found)}/{len(terms)}",
        )
        if found:
            checks.append(f"🔎 encontrados: {', '.join(found)}")
        if missing:
            checks.append(f"🕳️ faltan: {', '.join(missing)}")
            notes.append("faltan términos esperados")
        return min(score, 5), notes, checks

    keyword_ok = any(word in text.lower() for word in ["hola", "funcion", "funcionando", "activo"])
    score += add_check(checks, keyword_ok, "cumple intención del prompt", 2)

    spanish_marker_ok = bool(re.search(r"[áéíóúñ¿¡]", text.lower()))
    score += add_check(checks, spanish_marker_ok, "marca clara de español", 1)
    if not spanish_marker_ok:
        notes.append("poco español explícito")
    return min(score, 5), notes, checks


def score_tool_call(test, response):
    message = response.get("message") or {}
    tool_calls = message.get("tool_calls") or []
    expected_name = test["tool_name"]
    checks = []
    if not tool_calls:
        notes = ["no emitió tool_calls"]
        checks.append("❌ tool_call presente (+0/2)")
        if message.get("thinking"):
            notes.append("consumió tokens en thinking")
            checks.append("🧠 thinking presente")
        return 0, notes, checks

    first = tool_calls[0].get("function") or {}
    name = first.get("name")
    args = first.get("arguments") or {}
    score = add_check(checks, True, "tool_call presente", 2)
    notes = []

    if name == expected_name:
        score += add_check(checks, True, f"tool correcta `{expected_name}`", 2)
    else:
        score += add_check(checks, False, f"tool correcta `{expected_name}`", 2)
        notes.append(f"tool incorrecta: {name}")

    service_name = args.get("service_name") if isinstance(args, dict) else None
    if service_name == "ollama":
        score += add_check(checks, True, "argumento service_name=ollama", 1)
    else:
        score += add_check(checks, False, "argumento service_name=ollama", 1)
        notes.append(f"argumentos incorrectos: {args}")

    return min(score, 5), notes, checks


def run_test(base_url, model, test, timeout, keep_alive):
    options = {
        "temperature": 0,
        "num_ctx": 8192,
        "num_predict": test["max_tokens"],
    }
    if test["kind"] == "chat_tools":
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": test["prompt"]}],
            "tools": TOOLS,
            "stream": False,
            "options": options,
            "keep_alive": keep_alive,
        }
        response, wall_ms = post_json(base_url, "/api/chat", payload, timeout)
        quality, notes, checks = score_tool_call(test, response)
        message = dict(response.get("message", {}))
        if message.get("thinking"):
            message["thinking"] = "<thinking omitido>"
        content = json.dumps(message, ensure_ascii=False)
    else:
        payload = {
            "model": model,
            "prompt": test["prompt"],
            "stream": False,
            "options": options,
            "keep_alive": keep_alive,
        }
        response, wall_ms = post_json(base_url, "/api/generate", payload, timeout)
        quality, notes, checks = score_generate(test, response)
        content = (response.get("response") or "").strip()
        if not content and response.get("thinking"):
            # Extrae la respuesta final del bloque de thinking (soporta qwen3)
            thinking = (response.get("thinking") or "").strip()
            if "</think:6124c78e>" in thinking:
                after = thinking.split("</think:6124c78e>", 1)[1].strip()
                content = after or "<sin respuesta final; thinking omitido>"
            else:
                paras = [p for p in thinking.splitlines() if p.strip()]
                content = (paras[-1].strip() if paras else "<sin respuesta final; thinking omitido>")

    return {
        "model": model,
        "test": test["id"],
        "kind": test["kind"],
        "wall_ms": round(wall_ms, 2),
        "ollama_total_ms": ns_to_ms(response.get("total_duration")),
        "load_ms": ns_to_ms(response.get("load_duration")),
        "prompt_eval_ms": ns_to_ms(response.get("prompt_eval_duration")),
        "eval_ms": ns_to_ms(response.get("eval_duration")),
        "prompt_tokens": response.get("prompt_eval_count", 0),
        "output_tokens": response.get("eval_count", 0),
        "eval_tps": tokens_per_second(
            response.get("eval_count", 0), response.get("eval_duration", 0)
        ),
        "quality": quality,
        "score_detail": " · ".join(checks),
        "notes": "; ".join(notes),
        "response": content,
    }


def unload_model(base_url, model, timeout):
    try:
        post_json(
            base_url,
            "/api/generate",
            {"model": model, "prompt": "", "stream": False, "keep_alive": 0},
            timeout,
        )
    except Exception:
        pass


def write_markdown(path, rows, metadata):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["model"], []).append(row)

    lines = [
        "# Ollama Qwen Benchmark",
        "",
        f"- Fecha: `{metadata['created_at']}`",
        f"- Endpoint: `{metadata['base_url']}`",
        f"- Modelos: `{', '.join(metadata['models'])}`",
        "",
        "## Rúbrica del score",
        "",
        "`Score por reglas 🧪` no es una evaluación humana; es un chequeo automático para comparar rápido.",
        "",
        "- Prompts normales: respuesta final presente (+1), largo razonable (+1), y chequeos específicos del test (+3).",
        "- Suma: exige que aparezca el resultado esperado.",
        "- Código/systemd: busca términos obligatorios definidos por el test.",
        "- Tool call: tool presente (+2), nombre correcto (+2), argumento correcto (+1).",
        "- Si el modelo consume tokens en `thinking` pero no entrega respuesta final, queda castigado.",
        "",
        "## Resumen",
        "",
        "| Modelo 🤖 | Latencia avg ⏱️ ms | Velocidad avg 🚀 tok/s | Score por reglas 🧪 | Tests OK ✅ |",
        "|---|---:|---:|---:|---:|",
    ]

    for model, items in grouped.items():
        wall_avg = statistics.mean(item["wall_ms"] for item in items)
        tps_values = [item["eval_tps"] for item in items if item["eval_tps"]]
        tps_avg = statistics.mean(tps_values) if tps_values else 0
        quality_avg = statistics.mean(item["quality"] for item in items)
        ok_count = sum(1 for item in items if item["quality"] >= 4)
        lines.append(
            f"| `{model}` | {wall_avg:.0f} | {tps_avg:.2f} | {quality_avg:.1f}/5 | {ok_count}/5 |"
        )

    lines.extend(
        [
            "",
            "## Detalle",
            "",
            "| Modelo 🤖 | Prueba 🧩 | Wall ⏱️ ms | Load 📦 ms | Tok/s 🚀 | Score por reglas 🧪 | Desglose 🧾 | Notas ⚠️ | Respuesta 💬 |",
            "|---|---|---:|---:|---:|---:|---|---|---|",
        ]
    )

    for row in rows:
        response = row["response"].replace("\n", "<br>").replace("|", "\\|")
        if len(response) > 280:
            response = response[:277] + "..."
        notes = row["notes"].replace("|", "\\|")
        score_detail = row["score_detail"].replace("|", "\\|")
        lines.append(
            f"| `{row['model']}` | `{row['test']}` | {row['wall_ms']:.0f} | "
            f"{row['load_ms']:.0f} | {row['eval_tps']:.2f} | {row['quality']}/5 | "
            f"{score_detail} | {notes} | {response} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://nara:11434")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--keep-alive", default="10m")
    parser.add_argument("--out-dir", default="artifacts-delivery/ollama-benchmark")
    args = parser.parse_args()

    available = get_json(args.base_url, "/api/tags", args.timeout)
    available_models = {item["name"] for item in available.get("models", [])}
    missing = [model for model in MODELS if model not in available_models]
    if missing:
        raise SystemExit(f"Faltan modelos en Ollama: {', '.join(missing)}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    rows = []

    for model in MODELS:
        unload_model(args.base_url, model, args.timeout)
        for test in TESTS:
            print(f"running model={model} test={test['id']}", flush=True)
            rows.append(run_test(args.base_url, model, test, args.timeout, args.keep_alive))

    metadata = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "models": MODELS,
        "tests": TESTS,
    }
    json_path = out_dir / f"qwen-benchmark-{stamp}.json"
    md_path = out_dir / f"qwen-benchmark-{stamp}.md"
    json_path.write_text(
        json.dumps({"metadata": metadata, "rows": rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_markdown(md_path, rows, metadata)

    print(f"json={json_path}")
    print(f"markdown={md_path}")


if __name__ == "__main__":
    main()
