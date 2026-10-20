# Propuesta: metadatos de reproducibilidad y salidas JSON con esquema en las observaciones

Estado: **borrador para revisión humana**. No modifica nada por sí sola.
Afecta a `contracts/observation.schema.json`, `caliope_loop.py` (`_write_requests`, `validate_response`, `ingest_review`), `workflows/*.yaml` y `tests/test_caliope_loop.py`.
Origen: revisión de `deepseek-ai/DeepInfer` (Apache-2.0). No se adopta el motor, que exige GPUs NVIDIA SM100 o Ascend 950 en Linux y no tiene API HTTP. Solo se toman dos ideas: salidas restringidas por esquema y generación determinista auditable.

## 1. Problema

Hoy una observación dice **qué** concluyó el revisor (`findings`, `blockers`) y **cuánto** costó (`budget_used`), pero no **cómo se generó**:

- No queda constancia del modelo exacto, la versión, la temperatura ni el seed. Si dos runs dan resultados distintos, no se puede saber si cambió la evidencia o cambió el modelo.
- No queda constancia de si la respuesta se generó con el esquema impuesto o si es texto libre que alguien convirtió en JSON a mano. Eso importa: una observación "arreglada" a mano deja de ser evidencia independiente.
- `ingest` valida la forma, pero no puede distinguir una respuesta copiada del navegador de una obtenida por un adaptador con parámetros fijos.

Esto choca con la regla central: *"Las compuertas deterministas verifican."* Sin estos metadatos, la compuerta no tiene qué verificar sobre el origen.

## 2. Qué NO promete esta propuesta

- **No hace que las APIs hospedadas sean deterministas.** Con `temperature = 0` y seed fijo, la mayoría de los proveedores **siguen sin garantizar** la misma salida, porque el batching y el hardware cambian. La reproducibilidad exacta solo es posible con un motor propio que lo garantice, como DeepInfer con *batch-invariant inference* en NVIDIA.
- Aquí, "reproducible" significa **auditable**: se puede reconstruir con qué parámetros se generó cada observación y detectar cuándo cambió el modelo.
- El modo JSON de las APIs **no sustituye** la validación. `ingest` sigue siendo la autoridad y rechaza cualquier cosa que no cumpla el contrato.

## 3. Cambio al contrato: `schema_version` "1.1" con el bloque `generation`

Se añade un objeto obligatorio en 1.1. Los campos que el proveedor no permite controlar van en `null` **explícitamente**: nunca se omiten ni se inventan.

```json
"generation": {
  "type": "object",
  "additionalProperties": false,
  "required": [
    "provider", "model", "model_version", "capture_method",
    "structured_output", "contract_hash", "prompt_hash",
    "temperature", "top_p", "seed", "max_output_tokens",
    "determinism", "captured_at"
  ],
  "properties": {
    "provider":        {"type": "string", "pattern": "^[a-z0-9_.-]{2,40}$"},
    "model":           {"type": "string", "minLength": 2, "maxLength": 120},
    "model_version":   {"type": ["string", "null"], "maxLength": 120},
    "capture_method":  {"enum": ["api_adapter", "local_engine", "manual_copy"]},
    "structured_output": {"enum": ["json_schema", "json_mode", "none"]},
    "contract_hash":   {"type": "string", "pattern": "^[A-Fa-f0-9]{64}$"},
    "prompt_hash":     {"type": "string", "pattern": "^[A-Fa-f0-9]{64}$"},
    "temperature":     {"type": ["number", "null"], "minimum": 0, "maximum": 2},
    "top_p":           {"type": ["number", "null"], "minimum": 0, "maximum": 1},
    "seed":            {"type": ["integer", "null"], "minimum": 0},
    "max_output_tokens": {"type": ["integer", "null"], "minimum": 1},
    "system_fingerprint": {"type": ["string", "null"], "maxLength": 200},
    "determinism":     {"enum": ["none", "seeded_best_effort", "batch_invariant"]},
    "captured_at":     {"type": "string", "format": "date-time"}
  }
}
```

Significado de los campos clave:

| Campo | Qué registra | Ejemplo |
|---|---|---|
| `model` / `model_version` | El identificador exacto que devolvió el proveedor, no el que se pidió | `deepseek-chat` / `2026-10-17` |
| `capture_method` | Cómo llegó la respuesta | `manual_copy` si alguien la pegó desde el navegador |
| `structured_output` | Cómo se forzó el JSON | `json_schema` si el proveedor aplicó el esquema del contrato; `json_mode` si solo garantizó JSON válido; `none` si fue texto libre |
| `contract_hash` | SHA-256 del archivo de contrato usado | Debe ser igual al del paquete de `requests/` |
| `prompt_hash` | SHA-256 del prompt enviado | Debe ser igual al `prompt hash` del paquete |
| `system_fingerprint` | La huella de backend que algunas APIs devuelven | `null` si no existe |
| `determinism` | Qué garantía real había | `batch_invariant` solo con un motor que la garantice |

**Nunca** se registran claves, tokens, URLs con credenciales ni cabeceras. `provider` es un nombre corto, no un endpoint.

## 4. Cambio al paquete de solicitud (`_write_requests`)

Cada paquete de `requests/iteration-N/<agent>.json` incluye además:

```json
"generation_requirements": {
  "contract_hash": "<sha256 de contracts/observation.schema.json en init>",
  "response_schema": { "...": "copia literal del contrato" },
  "structured_output_preferred": "json_schema",
  "temperature": 0,
  "seed": 20261019,
  "max_output_tokens": 1500
}
```

- **El esquema viaja dentro del paquete**, para que un adaptador pueda pasarlo tal cual al modo de salida estructurada del proveedor. Es la idea de `so_args` de DeepInfer, llevada a cualquier API.
- **El seed lo fija el run**, no el revisor: se deriva del `run_id` y queda en el manifest. Así se sabe que todos los revisores recibieron la misma consigna.
- `max_output_tokens` se reparte desde `LIMITS["max_output_tokens"]` (12 000 entre 8 revisores son 1 500 cada uno), para que el límite también se aplique en origen y no solo al sumar.

## 5. Compuertas nuevas en `validate_response` / `ingest_review` (fail-closed)

En el mismo estilo que las validaciones actuales:

1. `generation` existe y cumple el esquema 1.1. Si falta en un run 1.1, se rechaza.
2. `generation.contract_hash` es igual al hash registrado en el manifest en `init`. Si no coincide, se rechaza con `"contract mismatch"`.
3. `generation.prompt_hash` es igual al `prompt hash` del paquete de ese agente. Si no coincide, se rechaza.
4. `temperature` y `seed`: si el workflow declara `reproducibility: required`, deben ser no nulos e iguales a los de `generation_requirements`. Si es `recorded`, se aceptan nulos pero se anotan.
5. `structured_output`: si el workflow declara `require_structured_output: true`, se rechaza `none`.
6. `capture_method = manual_copy` no se rechaza, porque hoy es la vía real, pero queda marcado. La regla de decisión debe poder exigir un mínimo de observaciones que no sean `manual_copy` antes de permitir `accept`.
7. Ningún campo de texto de `generation` puede parecer una credencial (prefijos tipo `sk-`, `AIza`, `Bearer`, `ghp_`). Si aparece, se rechaza y se escribe un aviso de seguridad en el run.
8. El presupuesto se mantiene: `budget_used` sigue siendo la fuente de verdad para `_add_budget`. `generation` no cambia ninguna cuenta.

Ejemplo de configuración en `workflows/macroflow-review.yaml`:

```yaml
observation_contract: "1.1"
reproducibility: recorded          # recorded | required
require_structured_output: false   # true cuando existan adaptadores API
min_non_manual_for_accept: 0       # sube cuando haya adaptadores
```

Se empieza en `recorded` con `false` y `0`, para no bloquear el flujo manual actual. Los valores se endurecen cuando existan los adaptadores.

## 6. Migración

- El manifest guarda la `observation_contract` con la que se hizo `init`. **Los runs 1.0 siguen validándose como 1.0**, y el run archivado `mf-v095-consolidation-20261019` no se toca.
- Los runs nuevos usan 1.1. No hay conversión automática de 1.0 a 1.1: inventar metadatos para observaciones viejas violaría la propia regla.
- `CHANGELOG.md` registra la versión del contrato.

## 7. Pruebas que hay que añadir a `tests/test_caliope_loop.py`

- Una observación 1.1 válida se ingiere y cambia el agente a `received`.
- Una observación 1.1 sin `generation` se rechaza.
- Una `contract_hash` distinta a la del manifest se rechaza.
- Un `prompt_hash` distinto al del paquete se rechaza.
- `reproducibility: required` con `seed: null` se rechaza; con `recorded` se acepta.
- `require_structured_output: true` con `structured_output: "none"` se rechaza.
- Un `model` que contenga `sk-...` se rechaza.
- Un run creado en 1.0 sigue aceptando observaciones 1.0.
- Un paquete de `requests/` contiene `generation_requirements` con un `contract_hash` que coincide con el archivo de contrato.

## 8. Inconsistencia encontrada de paso

`contracts/observation.schema.json` permite `agent_id: "claude_themis"`, pero `ROLES` en `caliope_loop.py` solo define los 8 revisores. `claude_themis` es el **orquestador** en `workflows/macroflow-recursive-improvement.yaml` (`orchestrator: claude_themis`). Una observación suya cumple el esquema, pero `ingest` la rechaza.

Recomendación: quitar `claude_themis` del `enum` de observaciones. El orquestador consolida y propone, pero no debe emitir evidencia propia que cuente como una observación independiente. Si se quiere que observe, hay que añadirlo a `ROLES` de forma explícita y contarlo aparte en la regla de decisión.

## 9. Relación con DeepInfer (para más adelante)

Si algún día el revisor `deepseek` corre en un motor propio en lugar de la API hospedada, DeepInfer encaja así:

- `capture_method: "local_engine"`, `determinism: "batch_invariant"` y `structured_output: "json_schema"` mediante `SamplingParams(so_args=...)`.
- Con eso, una observación se podría **re-ejecutar y comparar byte a byte** como compuerta adicional.

Requisitos que hoy no se cumplen: GPUs SM100 o Ascend 950, Linux, CUDA 13, y un servidor HTTP que DeepInfer todavía no publica. No es una prioridad frente a lo que bloquea el ciclo: el builder, los gates de compilación y paridad de Pine, y la evidencia de trades.

## 10. Decisiones que necesita la persona

1. ¿Se aprueba el contrato 1.1 con `generation` obligatorio en runs nuevos?
2. ¿Se fijan `reproducibility: recorded` y `require_structured_output: false` como valores iniciales?
3. ¿Se quita `claude_themis` (el orquestador) del `enum` de observaciones, que es lo recomendado, o se añade a `ROLES` como observador explícito?
