# Datos de la máquina `nara` (para historial de benchmarks)

Última actualización: 2026-07-15

| Componente | Detalle |
|---|---|
| Host | `nara` |
| OS | Ubuntu 26.04 LTS (Resolute Raccoon) |
| Kernel | 7.0.0-27-generic |
| CPU | AMD Ryzen 5 8500G w/ Radeon 740M Graphics (6 cores / 12 threads) |
| RAM | 14 GiB |
| GPU dedicada | NVIDIA GeForce RTX 3060 (PCIe 01:00.0), 12288 MiB VRAM, compute cap 8.6 |
| GPU integrada | AMD Radeon 740M (Phoenix2, iGPU) — usada solo como respaldo/Vulkan |
| Driver NVIDIA | 580.159.03 (CUDA 13.0) |
| Secure Boot | Enabled (MOK enrollado para módulo nvidia) |
| Ollama | 0.31.1 |
| PyTorch (sistema) | 2.9.1+debian (build ROCm; no usa CUDA NVIDIA) |
| PyTorch (venv prueba) | 2.9.1+cu130 en `/opt/gpu-test` (CUDA NVIDIA) |
| Red | LAN `enp5s0` 192.168.1.29 (optional) + WiFi `wlp7s0` 192.168.1.22 (default) |
| Endpoint Ollama | http://localhost:11434 (y http://nara:11434) |

## Notas de configuración relevantes
- Ollama detecta la RTX 3060 vía CUDA (`library=CUDA compute=8.6`) y la AMD vía
  Vulkan. Para inferencia se usa la RTX 3060 (11.5 GiB libres).
- El benchmark previo al 2026-07-15 corría en **CPU** (sin GPU dedicada
  disponible/enrollada), de ahí la diferencia de tok/s.
- Script de benchmark: `scripts/ollama_qwen_benchmark.py` (soporta modo thinking
  de qwen3 desde la revisión 2026-07-15).
