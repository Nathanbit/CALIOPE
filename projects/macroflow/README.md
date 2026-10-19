# MacroFlow en CALIOPE

Este directorio contiene un snapshot versionado para revisión orquestada. La
fuente de desarrollo permanece en el repositorio hermano `macroflow`; CALIOPE no
la modifica durante una corrida.

## Snapshot

- Modelo: `MF-0.9.4-REA-VAL`
- Experimento: `REA-0065`
- Fuente congelada: `source/MacroFlow_Backtest_v0_9_4_VALIDATION.pine`
- Hashes: `MANIFEST.sha256`

## Consolidación v0.9.5

La revisión externa del 19 de octubre de 2026 recibió las ocho respuestas
previstas: Perplexity, DeepSeek, Qwen, Z.ai, Kimi, Gemini, Meta y NVIDIA
Nemotron. Claude ordenó la evidencia como sintetizador, sin autoridad de voto o
promoción.

El run terminó en `archived_rejected` con decisión `reject`; no abrió una
segunda iteración ni materializó una candidata.

- Informe final: `reviews/AI_CONSOLIDATED_REVIEW_2026-10-19.md`
- Índice verificable: `reviews/AI_REVIEW_EVIDENCE_INDEX_2026-10-19.json`
- Decisión: rechazar la promoción de v0.9.5, mantener el ZIP en cuarentena y
  revisar sus bloqueadores antes de crear una candidata.

El resultado no demuestra rentabilidad. La ventana observada contiene cero
trades y no permite estimar rendimiento, Sharpe, drawdown o estabilidad.

## Evidencia operativa posterior

`evidence/LIVE_INDICATOR_SNAPSHOTS_2026-10-19.json` conserva una lectura
sanitizada de NQ1!, ES1! y RTY1! en 5 minutos. El caso separa explícitamente
`SIN OPERACIÓN` para entradas nuevas de la gestión de posiciones existentes,
registra la concentración de la cesta y permanece `open_unresolved` con
`promotion_eligible=false`. Su resultado futuro debe reconciliarse con el
historial del broker antes de incorporarse a una hipótesis.

## Flujo

Los agentes producen revisiones separadas en `reviews/`. Las compuertas del
workflow verifican compilación, causalidad, inputs heredados, identidad del plan,
costes y tamaño mínimo de muestra. Ningún agente puede promover ejecución real.

