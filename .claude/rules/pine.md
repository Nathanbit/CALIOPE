---
paths:
  - "**/*.pine"
---
# Pine Script de MacroFlow

- Los `.pine` de `projects/*/source/` están congelados en `MANIFEST.sha256`:
  un cambio es un archivo nuevo con versión nueva, nunca una edición.
- La estrategia canónica usa `process_orders_on_close=true`; la réplica Python
  del lab asume ejecución en la apertura siguiente. No compares resultados de
  ambos sin declarar esa diferencia.
- Toda señal debe ser causal: nada de `request.security` con `lookahead_on`
  sin desplazar la serie, ni valores de barras futuras. El lab tiene
  `scripts/causality_report.py` para la réplica Python.
- El comportamiento en TradingView no se verifica localmente: dilo en el
  resumen y adjunta la evidencia (captura, export) cuando la haya.
- Cero operaciones en un backtest no permiten estimar rendimiento.
