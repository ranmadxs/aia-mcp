# Spec: Monitor en tiempo real del host `nara`

## Objetivo
Un dashboard (web o informe) que muestre en **tiempo real** el estado de `nara`
(MSI H81M-E33, Intel i3-4150 @ 3.5GHz, 8 GB RAM, NVIDIA RTX 3060 12 GB, SSD 240 GB).
Accesible desde el navegador del Mac en `http://nara:<puerto>`.

## Métricas requeridas (todas en vivo, refresco ~1-2 s)

### 1. CPU
- Uso porcentual (%)
- Carga (load average 1/5/15)
- Núcleos / hilos (2c/4t)
- Temperatura (vía `lm-sensors` → `coretemp`, Package id 0)

### 2. GPU (NVIDIA RTX 3060)
- Uso de GPU (%)
- Memoria VRAM usada / total (12 GB)
- **Consumo en Watts** (vía `nvidia-smi --query-gpu=power.draw`)
- Temperatura de la GPU
- Límite de potencia (170 W)

### 3. Total Watts (gauge único)
- Suma de **CPU + GPU**:
  - CPU: RAPL `/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj` (µJ acumulado, delta/1s → W)
  - GPU: `nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits`
- NOTA: es consumo de componentes, NO de la pared (eso requiere smart plug).

### 4. RAM
- Consumo usado / total (8 GB)
- Porcentaje de uso
- Temperatura: el i3-4150 / placa MSI H81M-E33 usualmente NO expone sensor de DIMM → marcar N/A si no existe.

### 5. Disco duro
- Uso de partición raíz `/` (LVM `ubuntu-lv` en SSD Crucial 240 GB)
- I/O: lectura/escritura (MB/s)
- Temperatura (vía `smartctl -A /dev/sda` → `Temperature_Celsius`)

### 6. Temperaturas de componentes
- CPU (Package + cores)
- GPU
- RAM (si hay sensor; si no, N/A)
- Disco (SMART)

### 7. Ancho de banda de red (entrada/salida en tiempo real)
Por cada interfaz, mostrar RX y TX (Mbps o KB/s):
- Ethernet `enp2s0` (actualmente DOWN)
- WiFi principal `wlx0013eff21155` (UP, 192.168.1.37, driver rtl8192cu, señal -49 dBm)
- Internet de salida = interfaz con default route (`ip route | grep default`)
- (Opcional) `docker0`, `vxlan.calico` si se quiere desglosar tráfico de contenedores

## Restricciones / contexto del host
- Docker 29.6.1 instalado, runtime `nvidia` ya registrado (nvidia-container-toolkit 1.19.1).
- Driver NVIDIA 580.159.03, CUDA 13.0, legacy BIOS (sin Secure Boot).
- `lm-sensors` + `smartmontools` instalados.
- Ollama 0.32.0 corriendo en `0.0.0.0:11434` (modelos: qwen3:4b, qwen2.5:7b, qwen2.5:3b, qwen2.5:0.5b).
- Tarjeta USB WiFi RTL8188FU (`wlx00e0262e0265`) descartada: driver rtl8xxxu inestable.

## Lo que NO se quiere
- Netdata se probó y no mostró la GPU en el dashboard principal (aunque el contenedor
  sí la veía vía `nvidia-smi`). El usuario prefiere un informe tipo tabla claro y legible
  (como el que entregó el asistente con mediciones manuales) por sobre la UI de Netdata.

## Formato de entrega preferido
Un informe de texto (tablas) actualizable, O una web ligera tipo Glances
(`docker run -p 61208:61208 -e GLANCES_OPT="-w" nicolargo/glances`) que muestre
CPU/GPU/RAM/red/temp en una sola pantalla sin configuración compleja.
Glances soporta GPU NVIDIA y sensores de temperatura/red por interfaz nativamente.

## Pasos sugeridos (alternativa Glances, más simple que Netdata)
1. `docker run -d --name glances --restart always \
   --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all \
   -p 61208:61208 \
   -v /var/run/docker.sock:/var/run/docker.sock:ro \
   -v /run/nvidia:/run/nvidia:ro \
   --pid host \
   nicolargo/glances`
2. Abrir `http://nara:61208`
3. Verificar que la sección GPU (RTX 3060, watts, temp) y las interfaces de red aparezcan.
4. Si falta Total Watts o temp RAM, agregar un script auxiliar que lo exponga.

## Verificación
- [ ] CPU % y temp visibles
- [ ] GPU % , VRAM, watts y temp visibles
- [ ] Total Watts (CPU+GPU) visible
- [ ] RAM usada/total visible
- [ ] Disco uso + I/O + temp visible
- [ ] Ancho de banda RX/TX por interfaz (enp2s0, wlx0013eff21155, internet) visible
- [ ] Refresco en tiempo real (~1-2 s)
- [ ] Accesible desde el Mac en `http://nara:<puerto>`
