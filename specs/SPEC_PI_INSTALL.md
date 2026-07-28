# Spec: Instalación de PI Agent (replicable en otro PC)

Guía paso a paso para dejar PI Agent igual que en la máquina de `ranmadxs` (Mac),
con los mismos proveedores y extensiones. Pensado para replicar en otro equipo.

## 1. Instalar PI Agent

PI Agent se instala vía npm (binario `pi` en `/opt/homebrew/bin/pi` en macOS):

```bash
npm install -g pi-coding-agent
# o seguir la doc oficial de earendil-works/pi-coding-agent
pi --version
```

Los archivos de configuración viven en `~/.pi/agent/`:
- `settings.json` — provider por defecto, modelo y paquetes habilitados.
- `auth.json`    — keys de proveedores (OpenRouter, etc.).
- `models.json`  — proveedores custom (Ollama/nara, vLLM, LM Studio).

## 2. Proveedores (models.json)

PI Agent NO lee `baseUrl` de `auth.json` para el provider `openai` (lo ignora y
usa api.openai.com). Para proveedores custom (Ollama local/remoto) se usa
`models.json` con `baseUrl` explícito.

Crear `~/.pi/agent/models.json`:

```json
{
  "providers": {
    "nara": {
      "baseUrl": "http://nara:11434/v1",
      "api": "openai-completions",
      "apiKey": "ollama",
      "compat": {
        "supportsDeveloperRole": false,
        "supportsReasoningEffort": false
      },
      "models": [
        { "id": "qwen3:4b",    "name": "Qwen3 4B (nara)",    "reasoning": true,  "input": ["text"], "contextWindow": 40000,  "maxTokens": 8000,  "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 } },
        { "id": "qwen2.5:7b",  "name": "Qwen2.5 7B (nara)",  "reasoning": false, "input": ["text"], "contextWindow": 128000, "maxTokens": 32000, "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 } },
        { "id": "qwen2.5:3b",  "name": "Qwen2.5 3B (nara)",  "reasoning": false, "input": ["text"], "contextWindow": 128000, "maxTokens": 32000, "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 } },
        { "id": "qwen2.5:0.5b", "name": "Qwen2.5 0.5B (nara)", "reasoning": false, "input": ["text"], "contextWindow": 128000, "maxTokens": 32000, "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 } }
      ]
    }
  }
}
```

Notas:
- `nara` es el host remoto con Ollama (en `/etc/hosts` del Mac: `192.168.1.37 nara`).
  En otro PC, ajustar la IP o usar el DNS/local del host.
- `api: openai-completions` es el más compatible con Ollama.
- `compat.supportsDeveloperRole=false` y `supportsReasoningEffort=false` porque
  Ollama no entiende el rol `developer` ni `reasoning_effort`.

## 3. auth.json (proveedores con key)

Para usar OpenRouter (modelos free o de pago) se guarda la key en `auth.json`:

```json
{
  "openrouter": {
    "type": "api_key",
    "key": "sk-or-v1-<TU_KEY_AQUI>"
  }
}
```

> La key NO se comparte. En otro PC, pegar la key propia de OpenRouter.

## 4. settings.json

```json
{
  "lastChangelogVersion": "0.80.7",
  "theme": "light",
  "defaultProvider": "nara",
  "defaultModel": "qwen3:4b",
  "packages": [
    "npm:pi-peekaboo",
    "npm:@ogulcancelik/pi-ssh-tools",
    "npm:@tmustier/pi-weather",
    "npm:oh-my-pi"
  ]
}
```

- `defaultProvider` puede ser `nara` (Ollama local/remoto) u `openrouter`.
- `defaultModel` debe coincidir con un id de `models.json` (si es `nara`) o con un
  modelo real de OpenRouter.

## 5. Extensiones instaladas (`pi install`)

Se instalan con `pi install npm:<paquete>`. Listado actual:

| Paquete                        | Propósito                                              | Cubre                  |
|--------------------------------|--------------------------------------------------------|------------------------|
| `npm:pi-peekaboo`             | Utilidad de inspección de archivos/código             | Archivos               |
| `npm:@ogulcancelik/pi-ssh-tools` | SSH explícito: `ssh_read`, `ssh_write`, `ssh_edit`, `ssh_bash` | SSH (a nara, VPS) |
| `npm:@tmustier/pi-weather`    | Clima/tiempo, comando `/weather`                      | Tiempo                 |
| `npm:oh-my-pi`                | Framework de mejoras (orquestación, memoria)          | Archivos/organización |

Comandos de instalación (ejecutar en orden):

```bash
pi install npm:pi-peekaboo
pi install npm:@ogulcancelik/pi-ssh-tools
pi install npm:@tmustier/pi-weather
pi install npm:oh-my-pi
```

Verificar:

```bash
pi list
# debe mostrar los 4 paquetes arriba
```

## 6. Hora y fecha en Chile

No hay paquete específico de "hora Chile", pero PI Agent ya deduce la hora del
sistema y, con las tools built-in de bash, se obtiene fecha/hora de Chile así:

```bash
TZ=America/Santiago date
```

O pidiéndoselo al agente:

```bash
pi -p "dame la fecha y hora actual en Chile (America/Santiago) ejecutando TZ=America/Santiago date"
```

## 7. Uso

- Modelos de nara (Ollama remoto):
  ```bash
  pi --provider nara --model qwen3:4b -p "hola"
  ```
- Modelos de OpenRouter:
  ```bash
  pi --provider openrouter --model tencent/hy3:free -p "hola"
  ```
- Selector interactivo: abrir `pi`, `Ctrl+P` para ciclar modelos, `/model` para elegir.
- Clima: `/weather Santiago, Chile` (dentro de `pi` interactivo).
- SSH a nara: activar con `/ssh` y usar `ssh_bash`, `ssh_read`, etc.

## 8. Checklist de verificación en el nuevo PC

- [ ] `pi --version` funciona.
- [ ] `~/.pi/agent/models.json` con provider `nara` (o el host Ollama que toque).
- [ ] `~/.pi/agent/auth.json` con key de OpenRouter (si se usa).
- [ ] `~/.pi/agent/settings.json` con los 4 paquetes en `packages`.
- [ ] `pi list` muestra los 4 paquetes.
- [ ] `pi --provider nara --model qwen3:4b -p "hola"` responde.
- [ ] `pi -p "fecha en Chile" ` devuelve hora de America/Santiago.
- [ ] `/weather Santiago, Chile` funciona (requiere conexión a internet).
- [ ] SSH a nara funciona (`ssh_bash` llega al host).

## 9. Notas / lecciones aprendidas

- PI Agent IGNORA `baseUrl` de `auth.json` para el provider `openai`. Usar
  `models.json` para proveedores custom.
- El provider `openrouter` SÍ usa el `baseUrl` de `auth.json` (pero aquí apuntamos
  nara directo vía `models.json`, sin proxy).
- No existe `pi install search`: hay que saber el nombre del paquete (npm o git).
- El proxy fusionador (OpenRouter + nara) que se usó antes fue ELIMINADO; ya no
  hace falta porque Ollama es OpenAI-compatible y se apunta directo.
