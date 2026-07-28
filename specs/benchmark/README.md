# Historial de Benchmarks — Ollama Qwen en `nara`

Este directorio guarda el historial de ejecuciones del benchmark
`scripts/ollama_qwen_benchmark.py` contra Ollama en la máquina `nara`.

Cada ejecución vive en su propia carpeta con formato `YYYY-MM-DD-<backend>/`
(`cpu` o `gpu-rtx3060`), conteniendo los archivos `qwen-benchmark-*.json` y
`qwen-benchmark-*.md` generados por el script.

Datos de la máquina: ver [`machine-info.md`](machine-info.md).

---

## Ejecuciones

### 2026-07-15 — GPU RTX 3060 (3 ejecuciones)
- Carpeta: [`2026-07-15-gpu-rtx3060/`](2026-07-15-gpu-rtx3060/)
- Backend: NVIDIA RTX 3060 (CUDA 13.0, driver 580.159.03)
- Modelos: qwen3:4b, qwen2.5:7b, qwen2.5:3b, qwen2.5:0.5b
- Archivos:
  - `qwen-benchmark-20260715-174027.*` — **v1**, script original (sin soporte thinking)
  - `qwen-benchmark-20260715-174443.*` — **v2**, script arreglado (thinking) pero corrió con script viejo en /opt (no aplicó fix)
  - `qwen-benchmark-20260715-174928.*` — **v3**, script arreglado aplicado (thinking extrae respuesta)

#### v1 — script original
| Modelo | Latencia avg ms | Velocidad tok/s | Score | Tests OK |
|---|---:|---:|---:|---:|
| qwen2.5:7b | 3604 | 67.35 | 5.0/5 | 5/5 |
| qwen2.5:3b | 1126 | 121.90 | 4.8/5 | 5/5 |
| qwen2.5:0.5b | 2902 | 159.37 | 4.8/5 | 5/5 |
| qwen3:4b | 8903 | 82.83 | 0.0/5 | 0/5 |

> `qwen3:4b` da 0/5 solo por el script (no maneja thinking).

#### v3 — script arreglado (thinking soportado, sin perjudicar anteriores)
| Modelo | Latencia avg ms | Velocidad tok/s | Score | Tests OK |
|---|---:|---:|---:|---:|
| qwen2.5:7b | 6900 | 7.12 | 5.0/5 | 5/5 |
| qwen2.5:3b | 4747 | 15.67 | 5.0/5 | 5/5 |
| qwen2.5:0.5b | 692 | 327.61 | 4.8/5 | 5/5 |
| qwen3:4b | 2502 | 71.72 | 2.2/5 | 1/5 |

> El fix extrae la respuesta final del bloque `thinking` de qwen3 (post-`</think:6124c78e>`
> o último párrafo). qwen3 sube de 0/5 a 2.2/5. Los modelos qwen2.5 se mantienen
> en 5/5 (no se perjudican). Las velocidades de v3 bajaron porque se corrieron 3
> benchmarks seguidos compitiendo por VRAM; el scoring es válido.

### Fix posterior: CUDA_VISIBLE_DEVICES en Ollama
Tras los benchmarks se fijó Ollama a la RTX 3060 y se ocultó la AMD integrada,
editando `/etc/systemd/system/ollama.service.d/gpu.conf`:
```
CUDA_VISIBLE_DEVICES=0
OLLAMA_IGPU_ENABLE=0
ROCR_VISIBLE_DEVICES=-1
GGML_VK_VISIBLE_DEVICES=-1
```
`systemctl daemon-reload && systemctl restart ollama` → Ollama ahora detecta
**solo** `CUDA0 NVIDIA GeForce RTX 3060` (la Radeon 740M Vulkan desaparece).

### 2026-07-02 — CPU (sin GPU dedicada)
- Carpeta: [`2026-07-02-cpu/`](2026-07-02-cpu/)
- Backend: CPU AMD Ryzen 5 8500G (12 threads)
- Script: original
- Modelos (varias corridas): qwen3:4b, qwen2.5:3b, qwen2.5:0.5b
- Ejemplo (20260702-003533):

| Modelo | Latencia avg ms | Velocidad tok/s | Score | Tests OK |
|---|---:|---:|---:|---:|
| qwen3:4b | 20490 | 8.12 | 0.8/5 | 0/5 |
| qwen2.5:3b | 5905 | 15.85 | 4.8/5 | 5/5 |
| qwen2.5:0.5b | 1903 | 48.33 | 4.8/5 | 5/5 |

> Comparativa: pasar de CPU a RTX 3060 acelera ~4-10x los tok/s.

---

## Cómo agregar una ejecución nueva
1. Correr en nara: `python3 scripts/ollama_qwen_benchmark.py --base-url http://localhost:11434 --out-dir <destino>`
2. Copiar los `.json`/`.md` resultantes a `specs/benchmark/YYYY-MM-DD-<backend>/`
3. Agregar una entrada en este README con el resumen y los datos relevantes.
