# MacroFlow en CALIOPE

Este directorio contiene un snapshot versionado para revisión orquestada. La
fuente de desarrollo permanece en el repositorio hermano `macroflow`; CALIOPE no
la modifica durante una corrida.

## Snapshot

- Modelo: `MF-0.9.4-REA-VAL`
- Experimento: `REA-0065`
- Fuente congelada: `source/MacroFlow_Backtest_v0_9_4_VALIDATION.pine`
- Hashes: `MANIFEST.sha256`

## Flujo

Los agentes producen revisiones separadas en `reviews/`. Las compuertas del
workflow verifican compilación, causalidad, inputs heredados, identidad del plan,
costes y tamaño mínimo de muestra. Ningún agente puede promover ejecución real.

