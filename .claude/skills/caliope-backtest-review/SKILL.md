---
name: caliope-backtest-review
description: Checklist for reviewing a MacroFlow backtest, walk-forward or optimizer result before it is trusted (look-ahead, warm-up, costs, overfitting, holdout reuse, single artifact per metric set). Use when the person shares Strategy Tester results, asks whether a backtest is valid, or a CALIOPE run wants to promote a parameter change.
---

# Revisión de backtests de MacroFlow

Resume, en clave CALIOPE, `backtesting-frameworks` (wshobson/agents, MIT) y
`backtest-expert` (tradermonty/claude-trading-skills, MIT).

## Compuertas automáticas que ya existen

- Causalidad: `python labs/macroflow_v0_9_5_import/scripts/causality_report.py datos.csv`
  (lookahead y calentamiento; código 1 = no pasa).
- Paridad, holdout y artefacto único: ver `labs/macroflow_v0_9_5_import/REVIEW_STATUS.md`.

## Lista de verificación (marca cada punto como observación, derivación, inferencia o desconocido)

1. **Look-ahead:** ¿la señal usa solo barras cerradas? `request.security` con
   `[1]` y `lookahead_off`; reporte de causalidad aprobado.
2. **Calentamiento:** ¿el backtest empieza después de `recommended_startup_bars`?
3. **Costes:** spread real del broker (US100 en SimpleFX ≈ 1–2 puntos),
   comisión y deslizamiento; sensibilidad con el doble de coste.
4. **Ejecución:** ¿`process_orders_on_close` coincide entre Pine y Python?
5. **Tamaño de muestra:** menos de 30 operaciones por grado o franja = veredicto
   `insufficient`, nunca "positivo".
6. **Sobreajuste:** robustez a ±20% en cada parámetro clave; una meseta, no un pico.
7. **Walk-forward:** métricas OOS de un solo conjunto de parámetros; no
   concatenar folds con parámetros distintos.
8. **Holdout:** ciego, de un solo uso; si ya se miró, ya no es holdout.
9. **Sesgo de selección:** ¿cuántas variantes se probaron antes de esta? Regístralo.
10. **Régimen:** resultados separados por tendencia/rango y por sesión (Asia,
    Londres, Nueva York).

Un backtest que falle cualquier punto bloqueante no se promueve: la decisión
`human_review` de CALIOPE exige estos datos.
