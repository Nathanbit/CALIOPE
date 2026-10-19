# Revisión consolidada de IA — CALIOPE/MacroFlow v0.9.5

**Fecha:** 2026-10-19
**Run:** `mf-v095-consolidation-20261019` · iteración 0 · `stage=evidence_merge`
**Artefacto evaluado:** `macroflow_v0.9.5.zip` (SHA-256 `7630C65966E1EB1311F9F6D49B9E434FBF4AEBC1680A760FF65BC9FA6599D3E9`)
**Baseline de referencia:** Pine v0.9.4 (SHA-256 `63CFF54E2A36A3A3B5DB2AAA812D0F1BCF70D751D579D566DFB18A26DEA4816C`)
**Prompt de revisión:** SHA-256 `A44F5DD1DF09671DB213A3BF1728A9080D80DFDE980D6BEFFC99E9AE97A2E41F`

---

## 1. Alcance

Este documento es **solo análisis**. Sintetiza ocho revisiones independientes de modelos de IA (8/8 recibidas), las deduplica, conserva los desacuerdos y separa:

- **Hechos verificados:** constatados localmente en el repositorio y el runner. Son autoritativos.
- **Inferencias / hipótesis:** afirmaciones de revisores no respaldadas por inspección local, o que no han sido probadas.
- **Afirmaciones descartadas:** las que contradicen hechos verificados.

No contiene código, diffs, órdenes ni acciones externas. No se ejecutaron pruebas, builds ni backtests para producir este informe. El acuerdo entre IAs **no** se trata como verificación independiente: ocho modelos que leen el mismo material pueden compartir el mismo error.

---

## 2. Veredicto ejecutivo

> ### ❌ RECHAZAR PROMOCIÓN · 🔒 MANTENER CUARENTENA · 🔧 REVISAR (remediar antes de nueva evaluación)

`macroflow_v0.9.5.zip` **no puede promoverse**. Motivos determinantes, todos verificados localmente:

1. El Pine v0.9.5 es un **stub de 44 líneas**; no es una estrategia funcional.
2. El Python **no reproduce la lógica canónica v0.9.4**: no hay paridad.
3. El optimizador **contamina la selección**: concatena resultados OOS de folds con parámetros distintos y promueve `last_best`.
4. Hay **0 trades** en la ventana observada: no existe evidencia de rentabilidad, ejecución, Sharpe ni estabilidad.
5. **No hay candidata ni builder ejecutado.** No hay nada que promover.

El artefacto permanece en cuarentena. Live y autoaprobación siguen OFF y deben seguir así durante toda la remediación.

---

## 3. Hechos verificados

| # | Hecho | Evidencia | Confianza |
|---|---|---|---|
| H1 | Run `mf-v095-consolidation-20261019`, iteración 0, `stage=evidence_merge`, 8/8 revisores recibidos | Estado del runner | Alta |
| H2 | Hash baseline Pine v0.9.4 = `63CFF5…4816C`; hash prompt = `A44F5D…2E41F` | Provenance del runner | Alta |
| H3 | ZIP v0.9.5 hash = `7630C6…9D3E9`; en cuarentena | Provenance + estado de cuarentena | Alta |
| H4 | Pine v0.9.5 tiene 44 líneas y es un stub | Inspección del archivo | Alta |
| H5 | El Python no reproduce la lógica canónica v0.9.4 | Comparación con baseline | Alta |
| H6 | El optimizador concatena OOS de folds con parámetros distintos y promueve `last_best` | Inspección del optimizador | Alta |
| H7 | Live OFF, autoapprove OFF, `automatic_publish=false`, `automatic_trading=false` | Configuración | Alta |
| H8 | El runner impone: 2 iteraciones, 16 llamadas, 60k tokens entrada, 12k salida, USD 2 | Configuración del runner | Alta |
| H9 | El runner implementa lock, deduplicación, hashes/provenance, validación contractual, builder futuro determinista y revisores observation-only | Código del runner | Alta (existencia); **no probado bajo fallo** |
| H10 | Presupuesto usado: 8 llamadas, 6 440 tokens entrada, 3 330 salida, USD 0 | Contadores del runner | Alta |
| H11 | Saldo restante: 8 llamadas, 53 560 tokens entrada, 8 670 salida, USD 2; 1 iteración adicional | Derivado de H8 − H10 | Alta |
| H12 | 0 trades en la ventana observada | Resultados de la ventana | Alta |
| H13 | No hay candidata ni builder ejecutado; no hay promoción automática posible | Estado del runner + H7 | Alta |

**Nota sobre H9:** que los mecanismos existan no demuestra que resistan concurrencia, caídas o reinicios. Eso sigue pendiente (ver P1).

---

## 4. Consenso entre revisores

Los puntos en que coinciden varios revisores **y** que están respaldados por hechos locales:

| Tema | Revisores | Respaldo local |
|---|---|---|
| v0.9.5 es un stub incompleto | Gemini, Nemotron, (implícito en Qwen) | H4 ✔ |
| Falta paridad Pine ↔ Python | Gemini, Qwen, Nemotron | H5 ✔ |
| Selección OOS sesgada / contaminada | Gemini, Meta, Kimi, Nemotron | H6 ✔ |
| Live y autoapprove deben seguir OFF | Nemotron, DeepSeek (antes de live) | H7 ✔ (ya OFF) |
| Sin trades no hay métricas estimables | Kimi | H12 ✔ |
| Procedencia/hashes como requisito de promoción | Gemini, Meta | H2, H3, H9 ✔ (existe; falta probar) |

Puntos de consenso **sin** respaldo local todavía (siguen siendo recomendaciones, no hechos):

- Vectores de prueba deterministas y gates de paridad (Gemini, Qwen).
- Walk-forward purgado, holdout ciego y métricas anti-sobreajuste (Kimi, Meta).
- Requisitos de ejecución realista: sizing, brackets, gaps, costes (Qwen, DeepSeek).

---

## 5. Desacuerdos, contradicciones y afirmaciones degradadas

### 5.1 Descartadas (contradicen hechos verificados)

| Revisor | Afirmación | Motivo del descarte |
|---|---|---|
| Z.ai | Ausencia de caps/límites en el runner | Contradice H8: el runner impone límites de iteraciones, llamadas, tokens y coste |

### 5.2 Degradadas a hipótesis (no inspeccionadas o no probadas)

| Revisor | Afirmación | Estado |
|---|---|---|
| Meta | Riesgos de carrera / condiciones de concurrencia | Hipótesis. Lock y dedup existen (H9), pero no se han probado con inyección de fallos |
| Qwen | Detalles concretos de HTF confirmado, Pearson 2/3, contexto cerrado, WATCH, stops/TP congelados, parciales | Varios no inspeccionados; válidos como **lista de verificación**, no como diagnóstico |
| Z.ai | Afirmaciones de infraestructura (journal, restart, secretos, flooding) | Hipótesis: el revisor no inspeccionó el workspace |
| Nemotron | Riesgo de prompt injection y de promoción indebida | Inferencias. La promoción automática está deshabilitada (H7, H13); el riesgo residual es de proceso humano |
| DeepSeek | Requisitos de reconciliación/CLOSE remoto, Daily Max Loss | Pertinentes solo antes de live; hoy no aplican porque live está OFF |

### 5.3 Redundantes

- **Nemotron:** sugerencia de volver a apagar flags de live/autoapprove. Ya están OFF (H7). Lo útil es **verificar que sigan OFF** en cada gate, no reapagarlos.

### 5.4 Matices entre revisores (no contradicciones)

- **Profundidad de paridad:** Gemini pide gates de paridad generales; Qwen exige paridad barra a barra con lista detallada. Se adopta el criterio más estricto (Qwen) como objetivo, con Gemini como mecanismo (vectores deterministas).
- **Rigor estadístico:** Kimi exige DSR/PBO/bootstrap; ningún otro revisor lo contradice, pero tampoco lo refuerza. Con 0 trades (H12) estas métricas son inestimables hoy; se fijan como gate posterior.
- **Fuentes externas (Perplexity):** prueban **capacidades de plataformas**, no corrección del repositorio. No cuentan como evidencia sobre v0.9.5.

---

## 6. Bloqueadores

### P0 — Impiden cualquier evaluación de la estrategia

| ID | Bloqueador | Base |
|---|---|---|
| P0-1 | Pine v0.9.5 es un stub (44 líneas) | H4 |
| P0-2 | Python sin paridad con la lógica canónica v0.9.4 | H5 |
| P0-3 | Selección OOS contaminada: mezcla de parámetros entre folds y promoción de `last_best` | H6 |
| P0-4 | No existe candidata ni ejecución del builder | H13 |

### P1 — Impiden confiar en cualquier resultado que se produzca

| ID | Bloqueador | Base |
|---|---|---|
| P1-1 | Sin protocolo OOS anti-sobreajuste (walk-forward purgado con embargo, holdout ciego, DSR/PBO/bootstrap) | Kimi; H12 |
| P1-2 | Lock, dedup, idempotencia y replay no probados bajo fallos (crash, reinicio, duplicados) | Meta; H9 |
| P1-3 | Semántica de ejecución no especificada/verificada: sizing por instrumento, bracket 50/30/20, gaps, fills parciales, costes, MFE/MAE | Qwen, DeepSeek |
| P1-4 | Evidencia insuficiente: 0 trades | H12 |

### P2 — Requeridos antes de considerar paper extendido o live

| ID | Bloqueador | Base |
|---|---|---|
| P2-1 | Journal append-only con WAL/replay verificado | Meta, Z.ai (hipótesis) |
| P2-2 | Gestión de secretos, protección contra flooding de alertas/webhooks | Z.ai (hipótesis) |
| P2-3 | Daily Max Loss, reconciliación y CLOSE remoto | DeepSeek |
| P2-4 | Defensa frente a Goodhart (optimizar el gate en vez de la estrategia) y evidencia sintética | Z.ai |
| P2-5 | Revisión de superficie de prompt injection en el pipeline de revisores | Nemotron (hipótesis) |

---

## 7. Plan mínimo de remediación (secuencial)

Cada fase tiene un gate medible. **No se avanza sin superar el gate anterior.** Live y autoaprobación permanecen OFF en todas las fases; cada gate incluye la comprobación de que siguen OFF.

| Fase | Objetivo | Gate de salida |
|---|---|---|
| **F0. Congelar** | Mantener v0.9.5 en cuarentena; fijar baseline v0.9.4 por hash | Hash del ZIP y del baseline sin cambios; flags H7 en su valor actual |
| **F1. Especificación canónica** | Documentar la lógica v0.9.4 de forma ejecutable: HTF confirmado, Pearson 2/3, contexto cerrado, WATCH, stops/TP congelados, sizing, parciales, costes, alertas | Especificación revisada por un humano y versionada con hash |
| **F2. Implementación completa** | Sustituir el stub por una implementación Pine y Python completas | Ambas implementaciones cubren el 100 % de los elementos de la especificación F1 (checklist trazable) |
| **F3. Paridad** | Vectores de prueba deterministas, comparación barra a barra Pine ↔ Python ↔ baseline | Criterios §8.1 cumplidos |
| **F4. Corregir optimizador** | Cada resultado OOS ligado a un único hash de parámetros; eliminar promoción de `last_best` | Criterios §8.2 cumplidos; prueba que demuestre que no se mezclan parámetros |
| **F5. Robustez del runner** | Probar lock, dedup, idempotencia, replay con inyección de fallos | Criterios §8.4 (infraestructura) cumplidos |
| **F6. Builder y candidata** | Ejecutar el builder determinista y generar una candidata con provenance completa | Candidata con hashes reproducibles en dos ejecuciones independientes |
| **F7. OOS riguroso** | Walk-forward purgado con embargo + holdout ciego | Criterios §8.2 cumplidos con muestra suficiente |
| **F8. Paper trading** | Ejecución simulada con semántica real de órdenes | Criterios §8.3 cumplidos |
| **F9. Decisión humana** | Revisión del paquete de evidencia completo | Criterios §8.5 cumplidos |

Fuera de alcance de este plan: activación de live, autoaprobación, publicación automática o trading automático.

---

## 8. Criterios de aceptación cuantificables

> Los umbrales siguientes son **propuestas** de este informe. Deben ser ratificados por un humano y **congelados antes** de ver cualquier resultado nuevo, para evitar ajustarlos a posteriori.

### 8.1 Paridad

- Señales (entrada/salida/WATCH): **100 %** de coincidencia barra a barra entre Pine y Python en todos los vectores de prueba.
- Precios de stop/TP/entrada: diferencia **≤ 1 tick** del instrumento.
- Sizing: diferencia **= 0 unidades** tras redondeo al lote mínimo.
- Estados HTF confirmado y contexto cerrado: sin uso de barras no cerradas (0 casos de lookahead detectados).
- Cobertura: vectores que incluyan como mínimo apertura de sesión, gaps, barra de stop y TP en la misma vela, y parciales.

### 8.2 OOS y anti-sobreajuste

- Cada métrica OOS asociada a **un único** hash de parámetros: **0** concatenaciones entre parámetros distintos.
- Selección de parámetros realizada **solo** con datos in-sample; el holdout ciego se abre **una sola vez**.
- Purga y embargo explícitos entre folds (longitud documentada y ≥ horizonte máximo de la posición).
- Muestra mínima OOS fijada de antemano (número de trades); por debajo de ella el resultado es **inconcluyente**, no positivo.
- Reporte de DSR, PBO e intervalos bootstrap; PBO alto o DSR no significativo **bloquea** el avance.

### 8.3 Ejecución paper

- Reconciliación: **100 %** de órdenes simuladas con estado final coherente (sin órdenes huérfanas).
- Bracket 50/30/20: suma de parciales = 100 % del tamaño en todos los casos.
- Costes (comisión, spread, slippage) aplicados en **100 %** de los fills.
- Gaps y fills parciales cubiertos por casos explícitos.
- Daily Max Loss simulado: se dispara y bloquea nuevas entradas en las pruebas diseñadas para ello.
- MFE/MAE registrados para el 100 % de los trades.

### 8.4 Seguridad e infraestructura

- Live, autoapprove, `automatic_publish` y `automatic_trading` en OFF verificados en **cada** gate.
- Presupuesto del runner respetado: **0** ejecuciones que superen los límites de H8.
- Pruebas de fallo (crash a mitad de escritura, reinicio, mensaje duplicado): **0** duplicados, **0** pérdidas en el journal, replay determinista.
- Secretos: **0** credenciales en repo, logs o prompts.
- Revisores observation-only: **0** acciones con efecto lateral.

### 8.5 Decisión humana

- Paquete de evidencia completo: hashes, especificación F1, resultados F3–F8, este informe.
- Firma explícita de un responsable humano identificado; sin ruta de promoción automática.
- Registro de la decisión con fecha, hash del artefacto y motivo.

---

## 9. Límites de este informe

- **No hay promesa ni indicio de rentabilidad.** Con 0 trades no hay base para estimar rendimiento, Sharpe, drawdown ni estabilidad.
- **0 trades no valida nada**: ni la estrategia, ni la ausencia de errores, ni el funcionamiento de la ejecución.
- No se ejecutaron pruebas, builds, backtests ni inspecciones adicionales para elaborar este documento; solo se sintetizan hechos locales provistos y revisiones de IA.
- El consenso entre IAs no es verificación independiente.
- Las fuentes externas describen capacidades de plataformas, no la corrección de este repositorio.
- Los umbrales de §8 son propuestas sujetas a ratificación humana.

---

## 10. Fuentes primarias (referidas por el revisor Perplexity)

Estas fuentes respaldan capacidades de plataforma; no prueban la corrección de MacroFlow.

- Pine Script v6 — release notes: https://www.tradingview.com/pine-script-docs/release-notes/
- TradingView — configuración de webhooks: https://www.tradingview.com/support/solutions/43000529348-how-to-configure-webhook-alerts/
- Pine Script — FAQ de alertas: https://www.tradingview.com/pine-script-docs/faq/alerts/
- MetaTrader 5 — integración Python: https://www.metatrader5.com/en/metaeditor/help/development/python
- Optuna: https://optuna.org/
- PyBroker: https://www.pybroker.com/en/latest/index.html
- NVIDIA NeMo Agent Toolkit: https://docs.nvidia.com/nemo/agent-toolkit/latest/index.html
