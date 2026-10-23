---
name: caliope-predict
description: Seal a prediction in CALIOPE from one plain message ("sella: BTC toca 80.000 antes del viernes, 30%"). Turns the message into a concept, shows the generated question with a dry run, and seals only after the person confirms. Use when the person says "sella", "predice", "registra mi predicción", "apuesto a que", or gives a level, a deadline and a probability. Never places orders.
---

# Sellar una predicción con un mensaje

El modelo propone el concepto; `prediction_ledger.py` verifica; la persona
confirma. Nunca se colocan órdenes ni se tocan exchanges: esto solo registra
una predicción.

## 1. Convertir el mensaje en un concepto

Rellena **todos** los campos de `concepts.py` (`validate_concept` rechaza
cualquier clave que falte o sobre):

| Campo | Cómo obtenerlo |
|---|---|
| `asset` | Símbolo de Binance: "BTC" → `BTCUSDT` |
| `event` | "toca X" → `touch`; "cierra por debajo/encima de X" → `close_beyond`; "toca X antes que Y" → `touch_first` |
| `level` / `side` | `touch`: `at_or_below` si X está por debajo del precio, `at_or_above` si está por encima. `close_beyond`: `below` / `above` |
| `lower` / `upper` / `true_if` | Solo `touch_first`. `true_if` = `lower_first` si la persona apuesta a que toca primero el nivel bajo |
| `interval` | `1m` si el plazo es ≤ 6 h; `15m` si no |
| `start` | Próximo cierre de vela **después de ahora** (p. ej. siguiente múltiplo de 15 min), en UTC con `Z` |
| `horizon_end` | La fecha que dice la persona, convertida a UTC y alineada a la vela. Fechas relativas ("el viernes") se resuelven con su zona horaria y se dicen en voz alta |
| `reference_price` / `reference_source` | **Observado, nunca inventado**: precio actual de Binance (navegador o captura de la persona) y de dónde/cuándo salió. Si no lo puedes observar, pregúntalo |

Autor: la persona (`nathan`) salvo que diga que es tuya (`ia:claude`), de otra
IA (`ia:gemini`), de un indicador (`indicador:macroflow`), de una simulación
(`sim:mirofish`) o de un tercero (`experto:<nombre>`). Siempre en minúsculas. Probabilidad: la que dio, entre 0 y 1. Si no
dio probabilidad, pregúntala: sin número no hay predicción puntuable.

## 2. Mostrar el estado continuo del autor

```bash
python prediction_ledger.py --ledger <ledger> state --author <autor>
```

Resume en una línea lo que dice su historial (por ejemplo, "tus 80% han
salido 50% en 4 casos" o "aún sin historial"), y si existe, la tasa observada
de esta clase de concepto. Es información, no una corrección: la persona
decide si ajusta su probabilidad. Ese mismo estado queda grabado en el
pronóstico y `verify` lo recalcula.

## 3. Mostrar antes de sellar

```bash
python prediction_ledger.py --ledger projects/<proyecto>/predictions/ledger.jsonl \
  seal --concept '<json>' --author <autor> --probability <p> --rationale "<por qué>" --dry-run
```

Enseña a la persona, en dos o tres líneas: el texto generado de la pregunta,
la clase de concepto y la probabilidad. Si el dry run falla (por ejemplo,
"already satisfies": el precio ya cumple el evento), explícalo y propone el
ajuste; no fuerces el sellado.

## 4. Sellar solo con confirmación

Con un "sí" explícito, repite el comando sin `--dry-run`. Después:

```bash
python prediction_ledger.py --ledger <ledger> verify
```

Di el `question_id` y cuándo vence. Recuerda que la resolución es automática
con `tools/fetch_klines.py` + `resolve-auto` tras `horizon_end`, y que el
sellado de tiempo independiente (OpenTimestamps) se hace desde su PC.

## Nunca

- Inventar el precio de referencia ni redondear la probabilidad.
- Sellar sin confirmación, o sellar dos veces la misma idea con otro wording
  (el ledger lo rechaza por concepto).
- Interpretar la predicción como orden de compra/venta.
