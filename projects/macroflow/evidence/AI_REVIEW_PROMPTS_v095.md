# MacroFlow v0.9.5 — paquetes de revisión multi-IA

## Contrato de salida para los ocho revisores

Devuelve únicamente un objeto JSON válido conforme a
`contracts/observation.schema.json`. No incluyas Markdown, diff, código aplicable,
credenciales ni instrucciones de operación. El adaptador sustituirá estos campos:

- `run_id`: `{{run_id}}`
- `iteration`: `{{iteration}}`
- `agent_id`: `{{agent_id}}`
- `input_hashes`: debe contener `{{candidate_hash}}` y `{{prompt_hash}}`

Usa como máximo ocho hallazgos, cinco bloqueos y tres tareas siguientes. Separa
hechos, inferencias, hipótesis, fallos y métricas mediante `kind`; cita la
evidencia concreta y declara incertidumbre. `budget_used` debe registrar una
estimación conservadora del consumo de esta respuesta. Eres revisor: no puedes
aprobar, publicar, aplicar cambios ni operar.

## Contexto común (público y no sensible)

- Artefacto importado: `macroflow_v0.9.5.zip`, SHA-256 `7630C65966E1EB1311F9F6D49B9E434FBF4AEBC1680A760FF65BC9FA6599D3E9`.
- Contiene un puente FastAPI TradingView → paper/MT5, un optimizador Optuna walk-forward, Monte Carlo, dashboard y bucle recursivo.
- El Pine incluido mide 2.911 bytes y declara explícitamente que es solo un parche; no es el indicador completo.
- La réplica Python no reproduce los tres pilares, WATCH, grados, niveles congelados, salidas parciales ni toda la contabilidad del Pine canónico v0.9.4.
- Riesgos actuales: `LoopConfig.auto_approve=True`, `RunBody.auto_approve=True`, `max_iterations=0` ilimitado y puente MT5 disponible por configuración.
- El baseline canónico es Pine v6 v0.9.4: 136 inputs, 19 `request.security`, datos HTF confirmados, planes completos y ejecución observada en TradingView.
- Resultado visible v0.9.4 del 11–18 oct. 2026 en BTCUSDT 1m: cero trades. Es control de daño, no evidencia de rentabilidad.
- Objetivo: consolidar un laboratorio recursivo reproducible. Ninguna IA puede publicar, promover modelos ni operar. Cada candidata requiere equivalencia Pine↔Python, gates estadísticos y aprobación humana.

## NVIDIA Nemotron — arquitectura de agentes y cómputo

Diseña el DAG de Caliope para coordinar revisores independientes, un builder determinista y validadores. Propón contratos JSON, memoria por hashes, límites de iteración/coste, aislamiento de candidatos y uso razonable de GPU. Distingue qué puede paralelizarse con NVIDIA Agent Toolkit/NIM y qué debe quedar determinista. Incluye amenazas de prompt injection, colusión entre agentes, loop infinito y promoción indebida. Entrega invariantes y pruebas de aceptación; no propongas trading automático.

## Gemini — seguridad y pruebas de sistema

Audita la arquitectura del ZIP como sistema local. Prioriza separación research/paper/live, autenticación de webhooks, secretos, replay/dedupe, cierre remoto, path traversal de CSV, persistencia, concurrencia y fail-closed. Diseña una matriz de pruebas unitarias, integración y caos. Señala cambios mínimos para que el modo predeterminado sea investigación y para impedir MT5/auto-approve sin dos controles explícitos.

## Meta AI — invariantes operativos y estados

Modela el sistema como máquina de estados: observación → propuesta → candidata → validación → revisión humana → archivo/promoción. Busca transiciones inválidas, carreras, estados fantasma, reanudación peligrosa y contradicciones entre kill switch, modelo activo y bucle. Propón invariantes simples verificables y una política de rollback.

## Claude/Themis — síntesis y libro de evidencia

Propón cómo integrar el paquete v0.9.5 en Caliope sin reemplazar el Pine canónico. Define libro de evidencia, IDs, hashes, desacuerdos, provenance y decisión final. El sintetizador no debe votar por mayoría: debe exigir pruebas mecánicas. Entrega un plan de migración por etapas con criterios de completado.

## Kimi — validación estadística

Audita el walk-forward/Optuna/Monte Carlo descrito. Busca leakage por folds solapados, selección múltiple, reutilización del OOS, bootstrap incorrecto, muestra mínima, dependencia temporal y comparación injusta contra incumbent. Propón purga/embargo, holdout ciego, deflated Sharpe/PBO o alternativas apropiadas, y reglas para declarar una mejora sin sobreajuste.

## Qwen — equivalencia Pine↔Python

Define un protocolo de paridad barra a barra entre Pine v6 y la réplica Python. Enumera series y eventos que deben coincidir: HTF confirmado, Pearson 2/3, closed context, WATCH, promoción, stop/TP congelados, sizing, parciales, costes, MFE/MAE y alertas. Explica por qué no se deben optimizar parámetros hasta superar paridad y diseña archivos CSV de intercambio y tolerancias.

## DeepSeek — riesgo y ejecución

Audita sizing, bracket 50/30/20, gaps, SL/TP, order rejection, partial fills, symbol specs, Daily Max Loss y CLOSE remoto. Propón gates universales y una capa de paper trading que no pueda escalar a live por configuración accidental. Incluye casos BTC, CFD y futuros, con fórmulas y tests.

## Z.ai — red-team de recursividad

Ataca el sistema: auto-aprobación, ilimitación, degradación del baseline, optimización sobre datos sintéticos, Goodhart, candidate flooding, manipulación del journal, reinicio que reanuda solo, fuga de secretos y agente que convence al sintetizador. Clasifica riesgos por severidad y entrega mitigaciones que fallen cerrado.

## Perplexity — verificación de fuentes

Verifica únicamente con documentación primaria y enlaces: Pine v6/TradingView webhooks y alertas, MetaTrader5 Python, Optuna, PyBroker y NVIDIA NIM/Agent Toolkit. Separa hechos de inferencias y marca afirmaciones del README que no estén respaldadas. No evalúes rentabilidad.
