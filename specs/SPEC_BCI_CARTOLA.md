# Spec: Extracción de Ingresos de Cartola BCI (email MCP)

## Contexto

- **aia-mcp** expone el servidor `mcp_email` que, entre otras cosas, descarga las
  cartolas de cuenta corriente BCI (remitente `bcimail@bci.cl`) desde Yahoo IMAP,
  las guarda en MongoDB (`email.emails`, `kind:"bci_cartola"`) y extrae los
  **ingresos** (abonos, depósitos, transferencias recibidas) del PDF cifrado.
- El PDF de la cartola está cifrado con el **RUT de la cuenta** (sin dígito
  verificador), inyectado vía env var `BCI_PDF_PASSWORD`.
- Este spec documenta cómo se detectan y extraen los ingresos, y sirve de
  referencia para validar el parser y agregar nuevos casos.

---

## Formato de la cartola BCI (PDF)

Cada página del PDF tiene una tabla de movimientos con columnas:

```
FECHA | SUCURSAL | DESCRIPCION | Nº DE DOCUMENTO | CHEQUES Y OTROS CARGOS | DEPOSITOS Y ABONOS | SALDO DIARIO
```

Cada línea de movimiento real se extrae como texto plano en la forma:

```
DD-MM-YYYY  <SUCURSAL>  <DESCRIPCION>  <MONTO>  <SALDO>
```

Ejemplo crudo (tal como lo entrega `pdfplumber` en una sola línea):

```
15-04-2026  OF VIRT U  PAGO RECIBIDO PRV 061801000-7 BCO ESTAD  352.736  30.047.450
```

> **Nota de parsing:** el campo `descripcion` que retorna `_extract_movements`
> trae pegados al final el monto y el saldo (ej.
> `"OF VIRT U PAGO RECIBIDO PRV 061801000-7 BCO ESTAD 352.736 30.047.450"`).
> El monto y el saldo se recuperan por posición (`nums[-2]` y `nums[-1]`), no
> desde la descripción. Para mostrar la descripción limpia hay que recortar los
> dos últimos números.

---

## Lógica de detección de ingresos (`_extract_movements`)

Implementada en `mcp_email/server.py`. Reglas en orden:

1. **Filtro de línea:** solo se procesan líneas que empiezan con `DD-MM-YYYY`
   (fecha de movimiento). Se descartan encabezados (`SALDO`, `al `, etc.).
2. **Tokens numéricos:** se requieren al menos 2 números (`MONTO` y `SALDO`).
   `monto = nums[-2]`, `saldo = nums[-1]` (parseados con `_parse_amount` que
   convierte `1.300.000` -> `1300000.0`).
3. **Detección de ingreso** (`is_ingreso`):
   - **Primera fila** (sin `prev_saldo`): se marca ingreso solo si la
     descripción contiene una palabra clave de abono.
   - **Filas siguientes:** si `saldo > prev_saldo` => ingreso (señal más fiable,
     porque BCI no separa cargo/abono en columnas distintas).
   - **Refuerzo:** si `saldo >= prev_saldo` y la descripción tiene palabra clave
     de abono => ingreso.
4. **Palabras clave de abono** (`_ABONO_KEYWORDS`):
   `TRANSFER`, `ABONO`, `TRASPASO FONDOS`, `PAGO RECIBIDO`, `DEPOSITO`,
   `DEPÓSITO`, `RECAUDACION`, `ACREDITACION`, `ACREDITACIÓN`, `NOTA ABONO`,
   `REINTEGRO`, `DEVOLUCION`, `DEVOLUCIÓN`.
   - **Excluidas a propósito:** `CREDITO` / `CRÉDITO` (ambiguas: "PAGO CREDITO"
     es un cargo, no un abono).

---

## Ejemplo validado: ingreso 2026-04-15

Cartola `period = "2026-04"`. Uno de los 7 ingresos detectados:

| Campo | Valor |
|-------|-------|
| fecha | `15-04-2026` |
| descripcion (cruda) | `OF VIRT U PAGO RECIBIDO PRV 061801000-7 BCO ESTAD 352.736 30.047.450` |
| descripcion (limpia) | `OF VIRT U PAGO RECIBIDO PRV 061801000-7 BCO ESTAD` |
| monto | `352,736` |
| saldo | `30,047,450` |
| is_ingreso | `True` |

**Por qué se detecta como ingreso:**
- La descripción contiene `PAGO RECIBIDO` (palabra clave de abono en
  `_ABONO_KEYWORDS`).
- El saldo de la fila (`30.047.450`) es mayor al de la fila anterior, lo que
  confirma el abono.

**Cómo se llegó a este dato (trazabilidad):**
1. La cartola 2026-04 está guardada en MongoDB (`email.emails`,
   `kind:"bci_cartola"`, `period:"2026-04"`) como adjunto PDF en base64.
2. Se decodifica el adjunto y se abre con `pdfplumber.open(..., password=BCI_PDF_PASSWORD)`.
3. `_extract_movements` recorre las palabras por línea (`top` redondeado) y
   reconstruye cada movimiento.
4. Se filtran los movimientos con `is_ingreso == True`.
5. El ingreso del 15-04-2026 aparece como el 7º de 7, con monto `352,736`.

Resumen de la cartola 2026-04 (ingresos):

| FECHA | DESCRIPCIÓN (limpia) | MONTO |
|-------|----------------------|-------|
| 06-04-2026 | OF CENTRA TRANSFER A HORTENCIA HOYA | 30,000 |
| 06-04-2026 | OF VIRT U PAGO RECIBIDO REM 077398220-1 BCO CHILE | 6,432,912 |
| 06-04-2026 | OF CENTRA TRANSFER DE RADAR CHILE S | 100,575 |
| 07-04-2026 | OF CENTRA DEVOLUCION DE COMISIONES | 5,689 |
| 09-04-2026 | OF CENTRA TRANSFER DE RADAR CHILE S | 60,000 |
| 09-04-2026 | OF CENTRA TRANSFER DE RADAR CHILE S | 241,068 |
| **15-04-2026** | **OF VIRT U PAGO RECIBIDO PRV 061801000-7 BCO ESTAD** | **352,736** |
| **TOTAL** | | **7,222,980** |

---

## Tool de consulta (`get_bci_cartola_ingresos`)

- **Entrada:** `period` ("YYYY-MM", vacío = mes actual), `rut_password` (RUT del
  PDF, opcional), `force_refresh` (re-descargar de Yahoo).
- **Comportamiento:** cache-first en MongoDB; si no está, descarga de Yahoo y
  guarda (upsert por `message_id` desde v1.7.9). El cuerpo corre en
  `anyio.to_thread.run_sync` (no bloquea el event loop).
- **Salida:** markdown con total de ingresos, nº de movimientos y la lista
  (fecha, descripción, monto).

---

## Motor de sincronización genérico (v1.7.11+)

Desde v1.7.11 todas las variantes de sync usan **un solo motor** (`_do_sync`):

- `sync_emails(limit)` — INBOX completo (últimos N).
- `sync_emails_from(from_addr, limit)` — todos los correos de un remitente
  (IMAP `SEARCH FROM`), sin límite de antigüedad.
- `sync_bci_cartolas(months_back, force_refresh)` — cartolas BCI de los últimos N
  meses (IMAP `FROM bcimail@bci.cl SUBJECT "Cartola" SINCE/BEFORE`).

**Comportamiento común:**
1. Descarga de Yahoo por IMAP en un hilo (`anyio.to_thread`, no bloquea).
2. Por cada correo: si el `message_id` ya existe en Mongo → omitido (dedup).
   Si es nuevo → `_classify_and_save` lo etiqueta:
   - `kind:"bci_cartola"` si `from_addr` contiene `bcimail@bci.cl` **Y** el asunto
     contiene "Cartola" (case-insensitive) **Y** trae adjunto PDF. El `period` se
     deriva del PDF.
   - `kind:"email"` en cualquier otro caso.
   Guarda con `update_one({"message_id": mid}, {"$set": doc}, upsert=True)`.
3. Progreso persistido en `email.sync_state` (`_id:"email_sync"`) con `mode`,
   `scope`, `completed/total`. Se consulta con `get_email_sync_status()`
   (instantáneo, sin tocar Yahoo).

Esto garantiza que **cualquier** sync que traiga una cartola BCI la marque y
guarde igual, sin replicar lógica ni duplicar documentos.

---

## Notas de versión relevantes

- **v1.7.7:** el `period` de cache se deriva del contenido del PDF
  (`PERIODO : ... al DD-MM-YYYY`), no del mes solicitado.
- **v1.7.9:** `_imap_search_bci` busca en el **mes del período** (BCI envía la
  cartola del mes X dentro del mes X) y filtra por asunto `Cuenta Corriente`.
  `_fetch_bci_cartola` usa `update_one(..., upsert=True)` por `message_id` para
  no duplicar en `force_refresh`.
- **v1.7.11:** motor de sync único y genérico; detección de cartola por
  remitente BCI + asunto "Cartola"; progreso unificado en `get_email_sync_status`.
