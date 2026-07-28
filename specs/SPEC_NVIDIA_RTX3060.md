# SPEC: Instalación driver NVIDIA RTX 3060 en nara

**Fecha**: 2026-07-15
**Host**: nara (Ubuntu server, kernel 7.0.0-27-generic)
**Objetivo**: Dejar la GPU NVIDIA GeForce RTX 3060 (GA106) operativa con
`nvidia-smi` / CUDA para cargas de trabajo (p.ej. inferencia, contenedores Docker
con GPU).

---

## 1. Estado inicial (antes de instalar)

- `lspci` detecta la GPU:
  - `01:00.0 VGA compatible controller: NVIDIA Corporation GA106 [GeForce RTX 3060] (rev a1)`
  - `01:00.1 Audio device: NVIDIA Corporation GA106 High Definition Audio Controller`
- GPU integrada presente también: `10:00.0 VGA compatible controller: Advanced Micro Devices, Inc. [AMD/ATI] Phoenix2` (APU)
- **Sin drivers**: `lsmod | grep nvidia` vacío, `nvidia-smi` ausente,
  `/usr/lib/nvidia*` solo tenía marcador `alternate-install-available`.
- `ubuntu-drivers` disponible. Versiones en repo: 535/570 (transitional),
  **580** (actual, metapaquete `nvidia-driver-580`).

## 2. Decisión

- Instalar `nvidia-driver-580` (más nuevo estable en repo; soporta GA106/Ampere).
- Incluye DKMS (compila módulo contra el kernel actual) + `nvidia-smi`.
- Headless: acceso por SSH (LAN `enp5s0` 192.168.1.29 / WiFi `wlp7s0` 192.168.1.22).
  El reboot es seguro; la WiFi quedó configurada en netplan (`10-wifi.yaml`).

## 3. Procedimiento

```bash
# 1. Actualizar índice de paquetes
sudo apt update

# 2. Instalar driver (metapaquete 580)
sudo apt install -y nvidia-driver-580

# 3. Reboot para cargar el módulo del kernel
sudo reboot
```

## 4. Verificación post-reboot (primer intento)

```bash
lsmod | grep nvidia          # -> vacío
nvidia-smi                   # -> "couldn't communicate with the NVIDIA driver"
sudo dkms status             # -> nvidia/580.159.03, 7.0.0-27-generic: installed
sudo modprobe nvidia         # -> ERROR: Key was rejected by service
sudo mokutil --sb-state      # -> SecureBoot enabled
```

**Resultado del primer reboot**: driver instalado y compilado por DKMS, pero el
módulo NO carga por **Secure Boot habilitado**. El módulo se firmó con la clave
`nara Secure Boot Module Signature key` (generada por DKMS) que **no está
enrollada en el MOK** del firmware → el kernel la rechaza.

## 5. Fix requerido: enrollar la clave MOK (o desactivar Secure Boot)

El módulo ya está firmado; falta que el firmware confíe en la clave.

### Opción A — Enroll MOK (mantiene Secure Boot)
```bash
# 1. Importar la clave que DKMS generó (pide contraseña temporal de enroll)
sudo mokutil --import /var/lib/shim-signed/mok/MOK.der
# 2. reboot
# 3. En el menú azul "MOK Management" -> Enroll MOK -> ingresar la clave temporal
# 4. Al volver, verificar:
sudo modprobe nvidia && nvidia-smi
```
> El paso 3 es PRE-BOOT: requiere consola física/KVM (no se hace por SSH).

### Opción B — Desactivar Secure Boot (headless-friendly)
- Entrar a BIOS y apagar Secure Boot, O
- `sudo mokutil --disable-validation` + reboot + confirmar en menú MOK.
- Tras reboot el módulo carga sin firma.

**Estado actual**: pendiente de enroll MOK (requiere interacción en consola del
host durante el reboot). `nvidia-smi` sigue fallando hasta completar el enroll.

## 6. Enroll MOK ejecutado (2026-07-15)

```bash
# Import de la clave DKMS (hecho por SSH, contraseña temporal de enroll: naraenroll88)
sudo mokutil --import /var/lib/shim-signed/mok/MOK.der
# Reboot -> menu MOK Management -> Enroll MOK -> password naraenroll88 -> Reboot
```

**Verificación post-enroll:**
```bash
lsmod | grep nvidia
# nvidia, nvidia_uvm, nvidia_drm, nvidia_modeset  (cargados)

nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
# NVIDIA GeForce RTX 3060, 580.159.03, 12288 MiB

mokutil --sb-state
# SecureBoot enabled   (se mantuvo; el modulo ahora esta firmado y confiado)
```

## 7. Resultado final

| Ítem | Estado |
|------|--------|
| `nvidia-smi` disponible | ✅ |
| Módulo `nvidia` cargado | ✅ |
| RTX 3060 lista (12 GB VRAM) | ✅ |
| Secure Boot | ✅ enabled (MOK enrollado) |

La GPU es portable: se puede sacar y poner en otro equipo (el enroll MOK vive en
la NVRAM de la placa, no en la GPU). Sacar la tarjeta de nara no afecta el
funcionamiento (usa AMD integrada Phoenix2).

## 6. Troubleshooting

- **Pantalla negra en consola local**: la GPU integrada AMD sigue siendo la
  primaria de arranque; el driver nvidia solo afecta a la RTX. Si hay conflicto,
  fijar `PrimaryGPU` en la config de la AMD o usar `nomodeset` temporal.
- **Módulo no carga (DKMS falló)**: `sudo apt install --reinstall nvidia-driver-580`
  o `sudo dkms autoinstall`.
- **`nvidia-smi` dice "could not communicate"**: reboot pendiente o servicio
  `nvidia-persistenced` caído → `sudo systemctl restart nvidia-persistenced`.
