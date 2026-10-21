# Determinismo y reproducibilidad de las revisiones

Ideas adaptadas de [deepseek-ai/DeepInfer](https://github.com/deepseek-ai/DeepInfer)
(Apache-2.0). No se copia código: CALIOPE no ejecuta modelos, y DeepInfer
requiere GPU NVIDIA SM100 o NPU Ascend 950.

## Qué promete un motor y qué exige CALIOPE

DeepInfer separa tres niveles, que coinciden con el campo `generation.determinism`
del contrato de observación 1.1:

| Nivel | Qué garantiza | Lo que CALIOPE exige para aceptarlo |
|---|---|---|
| `none` | Nada; la misma pregunta puede dar otra respuesta | — |
| `seeded_best_effort` | La API acepta semilla, pero el lote o el hardware pueden cambiar el resultado | `seed` registrado |
| `batch_invariant` | Resultado idéntico sin importar con qué otras peticiones se agrupe (en DeepInfer: `DEEPINFER_BATCH_INVARIANT=1`, sin decodificación especulativa y `seq_seed` fijo) | `capture_method = local_engine`, `seed` y `model_version` (identidad del motor y del checkpoint) |

Una afirmación sin esos datos se rechaza al ingerir: no se puede reproducir, y
aceptarla sería registrar como hecho algo que solo es una afirmación.

## Verificar en vez de creer: `replay`

```powershell
python caliope_loop.py replay --run-id <run> --agent <agente> --file <segunda-captura.json>
```

La segunda captura debe usar exactamente los mismos ajustes de muestreo. CALIOPE
compara hallazgos y bloqueos (no la fecha, la identidad ni el presupuesto) y
guarda un registro en `runs/<run>/replays/`:

| Coincide | Afirmación | Veredicto |
|---|---|---|
| Sí | `batch_invariant` | `verified_deterministic` |
| Sí | `seeded_best_effort` | `reproduced` |
| No | `batch_invariant` | `claim_contradicted`: tratar la observación con desconfianza |
| No | `seeded_best_effort` / `none` | `not_reproduced` / `not_reproduced_expected` |

El replay consume presupuesto, no reemplaza la respuesta original y no cambia la
etapa del run.

## Otras ideas de DeepInfer, aún no implementadas

- **Generación separada de la evaluación.** DeepInfer guarda las salidas brutas
  y puntúa después, para poder repetir la evaluación con otro juez sin volver a
  generar. CALIOPE ya lo hace: `ingest` guarda la respuesta y `decision` es una
  etapa aparte.
- **Salidas estructuradas por gramática (regex/EBNF).** El contrato 1.1 solo
  distingue `json_schema`, `json_mode` y `none`. Añadir `grammar` exige un
  contrato 1.2.
- **Pass@k.** Medir la estabilidad de un revisor con varias muestras y semillas
  distintas. Requiere un adaptador local y presupuesto propio.
