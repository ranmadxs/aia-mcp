#!/usr/bin/env bash
#
# test_pi_tools.sh — Batería de pruebas para las extensiones instaladas en PI Agent.
#
# Prueba las herramientas instaladas vía `pi install`:
#   - @tmustier/pi-weather   -> /weather (tiempo/temperatura)
#   - @ogulcancelik/pi-ssh-tools -> ssh_bash / ssh_read (SSH a nara)
#   - pi-peekaboo            -> inspección de archivos
#   - oh-my-pi               -> mejoras/orquestación
#   - fecha/hora Chile       -> TZ=America/Santiago date (tool bash built-in)
#
# Uso:
#   ./scripts/test_pi_tools.sh            # todas las pruebas
#   ./scripts/test_pi_tools.sh weather    # solo clima
#   CITY="Valparaíso" COUNTRY="Chile" ./scripts/test_pi_tools.sh weather
#
# Requiere: pi en PATH, extensiones instaladas, y (para SSH) acceso a nara.

set -u

# --- Configurables ---
CITY="${CITY:-Valparaíso}"
COUNTRY="${COUNTRY:-Chile}"
NARA_HOST="${NARA_HOST:-nara}"
OUT_DIR="${OUT_DIR:-/tmp/pi_tools_test}"
PI_MODEL="${PI_MODEL:-qwen3:4b}"
PI_PROVIDER="${PI_PROVIDER:-nara}"

mkdir -p "$OUT_DIR"
LOG="$OUT_DIR/results.txt"
: > "$LOG"

log() { echo "### $*" | tee -a "$LOG"; }
run_pi() {
  # $1 = etiqueta, $2.. = prompt
  local label="$1"; shift
  log "PRUEBA: $label"
  local out
  out=$(pi --provider "$PI_PROVIDER" --model "$PI_MODEL" -p "$*" 2>&1)
  echo "$out" | tee -a "$LOG"
  echo "" | tee -a "$LOG"
}

do_weather() {
  log "===== WEATHER (@tmustier/pi-weather) ====="
  log "NOTA: pi-weather es un widget INTERACTIVO (/weather). En modo -p no responde."
  log "      Se prueba el comando pi y, como respaldo, wttr.in (API publica sin key)."
  run_pi "Temperatura en $CITY, $COUNTRY (via pi)" "dame la temperatura actual en $CITY, $COUNTRY. Responde solo con la temperatura y la ciudad."
  # Respaldo directo: wttr.in (no requiere API key)
  log "RESPALDO wttr.in: temperatura en $CITY, $COUNTRY"
  curl -s --max-time 15 "https://wttr.in/${CITY},${COUNTRY}?format=3" 2>&1 | tee -a "$LOG"
  echo "" | tee -a "$LOG"
}

do_ssh() {
  log "===== SSH (@ogulcancelik/pi-ssh-tools) ====="
  run_pi "SSH hostname en $NARA_HOST" "conéctate por SSH a $NARA_HOST y ejecuta 'hostname' usando las herramientas ssh. Responde con el hostname."
  run_pi "SSH uptime en $NARA_HOST" "por SSH a $NARA_HOST ejecuta 'uptime'. Responde con el resultado."
}

do_files() {
  log "===== ARCHIVOS (pi-peekaboo / built-in) ====="
  run_pi "Listar este directorio" "lista los archivos del directorio actual de trabajo y di cuántos hay."
  run_pi "Leer este script" "lee el archivo scripts/test_pi_tools.sh y di en una línea qué hace."
}

do_chile_time() {
  log "===== FECHA/HORA CHILE (bash built-in) ====="
  run_pi "Hora Chile" "ejecuta en bash: TZ=America/Santiago date y dime la fecha y hora actual en Chile."
}

do_ohmypi() {
  log "===== OH-MY-PI ====="
  run_pi "Estado oh-my-pi" "¿tienes disponible el framework oh-my-pi? describe brevemente qué aporta."
}

# --- Selector ---
case "${1:-all}" in
  weather) do_weather ;;
  ssh)     do_ssh ;;
  files)   do_files ;;
  chile)   do_chile_time ;;
  ohmypi)  do_ohmypi ;;
  all|"")
    do_weather
    do_ssh
    do_files
    do_chile_time
    do_ohmypi
    ;;
  *) echo "Uso: $0 [weather|ssh|files|chile|ohmypi|all]"; exit 1 ;;
esac

log "===== FIN ====="
echo "Resultados en: $LOG"
