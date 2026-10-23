# Guía para agentes que trabajan en CALIOPE

Este archivo lo leen los agentes de código (Claude, Codex, Cursor, Gemini CLI y
otros) antes de tocar el repositorio. Adapta las prácticas de
[morluto/rea](https://github.com/morluto/rea) (MIT) a un orquestador de
investigación de trading.

## Autoridad

- Los modelos proponen. Las compuertas deterministas verifican. Una persona
  decide cualquier acción real.
- Ningún cambio puede activar `automatic_publish`, `automatic_trading`,
  `live_execution`, auto-aprobación o un broker real. `python caliope_loop.py
  doctor` falla si un workflow los enciende.
- No integres una capacidad mientras sigan abiertas preguntas de alcance,
  autoridad, efectos secundarios o verificación. Un prototipo o borrador no
  autoriza la integración.
- Nunca leas, escribas ni registres credenciales de brokers, wallets o APIs.

## Clasificación de la evidencia

Todo informe, revisión o salida de herramienta separa cuatro tipos:

| Tipo | Significado | Puede usarse como hecho |
|---|---|---|
| `observation` | Constatado directamente (archivo, export, historial del broker) | Sí |
| `derivation` | Calculado de forma determinista a partir de observaciones | Sí, si el cálculo es reproducible |
| `inference` | Interpretación o hipótesis, incluida la opinión de un modelo | No |
| `unknown` | No se pudo determinar | No; nunca se trata como ausente ni como "ok" |

- Un valor desconocido sigue siendo desconocido. Un dato faltante no es un dato
  normal (por ejemplo, volumen ausente ≠ volumen medio).
- El acuerdo entre varias IA no convierte una inferencia en hecho.
- Conserva la procedencia: ruta, hash SHA-256 y fecha de cada fuente.

## Propiedad de cada interpretación

- Cada formato tiene un único dueño que lo parsea; los consumidores usan sus
  hechos ya parseados en vez de reinterpretar el archivo bruto:
  `prediction_ledger.py` para el ledger de predicciones, `resolver.py` para
  reglas y velas, `recursive_evidence.py` para el ledger de operaciones y
  `caliope_loop.py` para los expedientes del runner.
- Al corregir un error, arregla la representación que lo produce antes de
  añadir parches aguas abajo, y elimina el código que quede obsoleto.
- Los fallos esperados se modelan explícitamente y fallan cerrados.

## Verificación obligatoria por tipo de cambio

| Cambio | Comprobación |
|---|---|
| Cualquiera | `python tools/verify.py` (ejecuta `doctor` y lo que corresponda según lo que cambió) |
| Runner / ledger | `python -m unittest discover -s tests -p "test_*.py"` |
| Predicción sellada | `python prediction_ledger.py --ledger <ledger> verify` y `doctor` en verde antes del resultado; nunca editar líneas del ledger |
| `archive/` | No se modifica salvo para restaurar una pieza; si se restaura, recupera antes su verificación |

Indica explícitamente qué no se verificó. Los datos sintéticos no prueban el
comportamiento con datos reales, y cero operaciones no permiten estimar
rendimiento.

## Eficiencia

Optimiza primero los tokens y el contexto del agente, luego el tiempo de
análisis y por último CPU y memoria. Devuelve la respuesta mínima suficiente
con su procedencia y sus límites; deja la evidencia completa en archivos.
