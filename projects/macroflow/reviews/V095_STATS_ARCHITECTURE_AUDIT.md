# Auditoría estadística y de arquitectura — `macroflow_v0.9.5.zip`

Fecha: 2026-10-18 (America/Guayaquil)  
SHA-256 del ZIP: `7630C65966E1EB1311F9F6D49B9E434FBF4AEBC1680A760FF65BC9FA6599D3E9`

## Dictamen

El paquete aporta piezas valiosas de infraestructura —walk-forward, Optuna, bootstrap, Monte Carlo por bloques, registro de modelos, freno de riesgo y 28 pruebas declaradas—, pero **todavía no constituye evidencia de rentabilidad ni una v0.9.5 validada**. Debe entrar a CALIOPE como *propuesta experimental*, sin promoción automática y sin conexión a ejecución real.

La causa principal es una falta de paridad: `pine/MacroFlow_v0.9.4.pine` dice expresamente que es un parche y no el script completo (2.911 bytes), mientras la réplica Python implementa una estrategia reducida. Por tanto, los parámetros que optimiza y las métricas que calcula no describen el MacroFlow completo que está activo en TradingView.

## Evidencia realmente presente

- 80 archivos, 10 archivos de pruebas y 28 funciones `test_*`.
- Sintaxis Python: `compileall` pasó.
- La suite no se pudo ejecutar en el entorno de auditoría porque no estaban instalados `pytest` ni las dependencias del proyecto. No existe artefacto CI dentro del ZIP que pruebe una ejecución anterior.
- `registry/` y `state/` solo contienen `.gitkeep`: no hay candidatos, métricas OOS, folds, manifests de datos, journals ni resultados reproducibles.
- `server.log` solo registra un arranque fallido: `.venv/bin/python: No such file or directory`.
- El README declara que MT5 no fue probado contra un terminal real.
- No hay integración NVIDIA en el código del ZIP.

Conclusión: hay **código y pruebas declaradas**, pero no evidencia empírica de que los gates hayan sido superados por MacroFlow real.

## Hallazgos críticos

### P0 — Paridad de modelo inexistente

1. El archivo Pine incluido es un parche, no el indicador completo.
2. `strategy/reference.py` omite gran parte de la arquitectura real: pilares completos, MTF, WATCH/confirmación, parciales, breakeven/trailing, calendario, MFE/MAE, presets y reglas de promoción.
3. El backtest Python usa una señal simplificada de precio contra tendencia estructural y EMA larga. Optimizarla no valida el indicador real.
4. La réplica deja `transmission_active = ref_close.diff() != 0`; con SPY adelantado por *forward fill* en marcos inferiores, bloquea casi todas las barras. El propio README reconoce el problema.
5. `blocked_grades` se añade a `space.fixed`, pero la réplica Python nunca lo consume. Ese gate es inerte.

**Gate obligatorio:** ninguna optimización puede arrancar hasta obtener paridad trade-by-trade entre Pine y Python sobre el mismo símbolo, timeframe, rango, sesión y costes. Objetivo: mismas entradas/salidas y una discrepancia de P&L explicada y acotada.

### P0 — Promoción autónoma insegura

`LoopConfig` usa `source="demo"` y `auto_approve=True` por defecto. El demo genera datos sintéticos y puede activar automáticamente una versión. Esto mezcla demostración, validación y promoción.

**Corrección de gobierno:**

- `auto_approve=False` siempre para proyectos financieros.
- Los resultados `demo` o `synthetic` reciben una marca no promocionable.
- Solo un dataset versionado y autorizado puede producir un candidato.
- El cambio a `active` requiere gates deterministas, periodo *shadow/paper* y aprobación humana registrada.

### P0 — Métrica del candidato distinta al modelo desplegado

En cada fold se optimiza un `best` distinto y se concatenan sus resultados OOS en `cand_r`; al final se registra e inyecta únicamente `last_best`. Los gates evalúan el desempeño de una secuencia adaptativa de parámetros, pero se despliega solo el conjunto del último fold.

**Corrección estadística:** escoger un conjunto deployable antes del holdout (mediana estable, campeón de validación o regla adaptativa formal), congelarlo y volver a evaluarlo sobre un holdout sellado. El artefacto evaluado debe ser byte por byte el artefacto promovido.

### P1 — Riesgo de sobreajuste y selección repetida

- Se exploran muchos trials y versiones, pero no hay corrección por pruebas múltiples ni control de *Probability of Backtest Overfitting*.
- El fin de datos se controla solo con `last_data_end`; no hay hash del dataset. Una corrección histórica con la misma fecha final no se detecta.
- La historia OOS vuelve a entrar en evaluaciones posteriores. Con cada iteración deja de ser un holdout verdaderamente sellado.
- La comparación de fin de datos se hace como texto (`str(data_end) <= last`).
- `last_data_end` se guarda incluso cuando el candidato es rechazado o el activo frena; un cambio de código con el mismo dataset no puede reevaluarse de forma trazable.
- Los horarios y grados bloqueados se acumulan por unión y nunca caducan, generando dependencia permanente de muestras viejas.

**Corrección:** usar `dataset_hash + code_hash + config_hash + model_hash`, un holdout sellado por release, *nested walk-forward*, un test de superioridad pareado y control de selección (Deflated Sharpe/White Reality Check o SPA, y PBO/CSCV cuando haya muestra suficiente).

### P1 — Métricas que requieren endurecimiento

- El PF ≥ 1,10 es un estimador puntual; debe acompañarse de intervalo o stress.
- El IC de esperanza usa bootstrap IID, mientras el Monte Carlo sí usa bloques. La esperanza debe usar bootstrap temporal por bloques.
- La simulación convierte R a dólares usando capital inicial fijo; no modela dimensionamiento compuesto, gaps, latencia, funding variable ni deslizamiento dependiente de volatilidad/liquidez.
- `cost_bps=6` se aplica a entrada y salida, pero no está reconciliado con la comisión 0,08 %, funding y costes reales del broker.
- La auditoría por grado/hora usa P&L absoluto; debe usar R neto por plan completo para no confundir ventaja con tamaño de posición.
- `rebuild_plans` agrupa por `entry_time + side`; puede fusionar instrumentos/modelos simultáneos. Debe usar `plan_id`, ticker, versión y lado.
- El contexto cerrado solo distingue fin de semana; faltan sesión de Wall Street, feriados, staleness y decay de SPY/VIX/DXY.

## Gates propuestos para la recursividad

Orden de fallo cerrado; si uno falla, la iteración termina sin escribir Pine activo:

1. **Proveniencia:** hashes de datos/código/configuración, zona horaria, proveedor, cobertura y reglas de ajuste.
2. **Calidad de datos:** duplicados, huecos, barras futuras, timestamps, sesiones y staleness.
3. **Paridad Pine↔Python:** mismo plan de trade, entrada, stop, salidas y costes.
4. **Causalidad:** sin lookahead/repaint; requests MTF confirmadas; decisiones solo con información disponible.
5. **Separación estadística:** train → validation → holdout sellado; embargo y purge alrededor de fronteras.
6. **Muestra:** mínimo 100 planes OOS globales y, para cualquier afirmación segmentada, al menos 30 planes completos por activo/sesión/grado. Si no alcanza, resultado `INCONCLUSIVE`.
7. **Ventaja neta:** IC por bloques de la esperanza neta > 0; PF estresado > 1; mejora pareada frente al campeón con límite inferior > 0.
8. **Stress:** sigue positivo con 1,5× y 2× costes, slippage adverso, gaps y retraso de una barra/tick razonable.
9. **Riesgo:** DD VaR/CVaR dentro de presupuesto, P(ruina) ≤ 1 %, pérdida diaria y exposición agregada verificadas.
10. **Estabilidad:** parámetros no se pegan sistemáticamente a límites; sensibilidad local y estabilidad entre folds/regímenes aceptables.
11. **Red-team:** un agente adversarial intenta invalidar causalidad, costes, muestra y dependencia de régimen.
12. **Shadow:** paper trading inmutable por al menos 50 planes y dos regímenes relevantes; no se usa ese periodo para retocar el mismo candidato.
13. **Aprobación:** promoción humana final; rollback automático al último campeón íntegro si falla salud en producción.

## Orquestación de IAs bajo CALIOPE

Las IAs no votan la rentabilidad. Proponen hipótesis y revisiones; los gates deterministas deciden si existe evidencia suficiente.

| Rol | Modelo/agente | Salida permitida |
|---|---|---|
| Integración y reproducibilidad | Codex | diff, pruebas, manifest, reporte |
| Pine v6 y causalidad | Qwen | hallazgos de compilación/repaint |
| Ejecución y riesgo | DeepSeek | sizing, stops, costes, estados |
| Contexto y fuentes | Perplexity | calendario y contexto con fuentes primarias |
| Arquitectura/evidencia | Claude/Themis | DAG, contratos, trazabilidad |
| Falsificación | Z.ai | escenarios que rompen la hipótesis |
| Orquestación/telemetría | NVIDIA NeMo Agent Toolkit | workflow, trazas, evaluación, perfilado |
| Crítico NVIDIA | NIM/Nemotron | revisión estructurada independiente, sin acceso a broker |

Cada agente entrega JSON validado contra esquema con: `run_id`, rol, modelo/versión, prompt_hash, input_hashes, afirmaciones, evidencia, incertidumbre, propuesta y veto. CALIOPE rechaza texto libre como decisión operativa.

### Integración NVIDIA recomendada

Usar **NVIDIA NeMo Agent Toolkit** como capa de workflow y observabilidad, no como motor estadístico ni ejecutor. Sus capacidades oficiales permiten multiagente/MCP, evaluación, trazas, perfilado y optimización de parámetros de workflow. `nat eval` conserva salida, configuración efectiva, metadatos y trazas; encaja con el ledger reproducible de CALIOPE.

- NIM/Nemotron puede ocupar los roles de crítico y verificador de evidencia.
- NeMo Agent Toolkit debe registrar latencia, tokens, fallos y trayectorias.
- NeMo Guardrails aplica rails de entrada, recuperación, ejecución y salida.
- Su optimizador puede ajustar prompts/temperatura/modelo del workflow, pero **jamás** los umbrales de riesgo, los gates financieros o el holdout.
- El harness de finetuning/RL figura como experimental en la documentación: no debe entrar al camino de promoción de trading.

Fuentes oficiales consultadas:

- https://docs.nvidia.com/nemo/agent-toolkit/latest/workflows/evaluate.html
- https://docs.nvidia.com/nemo/agent-toolkit/latest/improve-workflows/optimizer.html
- https://docs.nvidia.com/nemo/guardrails/latest/about-nemo-guardrails-library/rail-types
- https://github.com/NVIDIA/NeMo-Agent-Toolkit

## Ciclo recursivo seguro

```text
OBSERVAR datos nuevos con hash
  → PROPONER hipótesis independientes
  → FALSAR primero
  → IMPLEMENTAR en rama candidata
  → PARIDAD Pine/Python
  → WALK-FORWARD + HOLDOUT sellado
  → STRESS + RIESGO
  → CONSENSO documental de agentes
  → GATES deterministas
  → SHADOW/PAPER
  → APROBACIÓN humana
  → PROMOVER o RECHAZAR
  → MONITOREAR + ROLLBACK
```

Reglas de recursividad:

- Una iteración cambia una sola hipótesis principal.
- Ningún agente puede modificar sus propios criterios de aceptación.
- El evaluador no recibe el nombre del proveedor ni la conclusión esperada.
- Los resultados negativos también se guardan para evitar repetir búsquedas.
- Presupuesto por ciclo: trials, llamadas, tokens, tiempo y número de cambios.
- Tres ciclos sin mejora no justifican relajar gates; se amplía la hipótesis o se detiene.
- El dominio de trading y la futura IA física comparten CALIOPE, contratos y auditoría, pero no credenciales, actuadores ni rutas de ejecución.

## Plan de consolidación

1. Importar el ZIP como `candidate/vendor/macroflow_v0.9.5` conservando hash; no llamarlo versión activa.
2. Sustituir el parche Pine por una copia versionada del MacroFlow completo y generar un mapa explícito de parámetros.
3. Construir un export dorado de TradingView y una prueba de paridad plan a plan.
4. Corregir el desfase `cand_r`/`last_best`, desactivar autoaprobación y separar demo de datasets autorizados.
5. Añadir manifest de datos, lockfile, CI y artefactos de `pytest`, backtest, MC y folds.
6. Integrar NeMo Agent Toolkit detrás de un adaptador CALIOPE advisory-only con telemetría y JSON Schema.
7. Ejecutar un ciclo seco completo. Solo tras pasar paridad y gates se abre paper trading.

## Estado final de esta auditoría

- Código Python: sintácticamente válido.
- Pruebas: 28 declaradas, no demostradas en este entorno.
- Backtest rentable: no demostrado.
- Paridad con MacroFlow activo: falla por diseño del paquete.
- Automejora: existe como prototipo, pero sus defaults actuales no son seguros para promoción.
- NVIDIA: no integrado; arquitectura de integración definida.
- Aptitud para live/IA física: **NO**.
- Aptitud para rama experimental y consolidación en CALIOPE: **SÍ, después de aplicar los P0**.
