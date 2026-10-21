---
name: caliope-session-review
description: Review a finished trading session from the SimpleFX "closed orders" export and check it against CALIOPE's discipline rules (trade count, session windows, losing streaks, stops, winners closed by hand before TP). Use when the person says "revisa mi sesión", "cómo me fue hoy", "postmortem", or uploads a SFX_closed_orders CSV after trading.
---

# Revisión de sesión de CALIOPE

Adaptada de `trade-performance-coach` y `signal-postmortem`
(tradermonty/claude-trading-skills, MIT). Reimplementada para el export de SimpleFX.

## Pasos

1. Ejecuta sobre el CSV y la fecha de la sesión:

   ```bash
   python tools/discipline/session_review.py --simplefx-csv SFX_closed_orders_....csv \
     --date 2026-10-20 --out state/reviews/sesion-2026-10-20.md
   ```

2. Resume en pocas líneas: resultado neto, cuántas reglas se cumplieron y el
   patrón más importante. Separa:
   - **Hechos:** precios, horas, P&L del export.
   - **Inferencias:** `close_reason_inferred` (TP, stop o manual) se deduce
     comparando el cierre con TP/SL; el export no lo dice.
3. Si aparece "ganadora protegida cerrada a mano antes del TP", dilo con
   tacto: es el patrón que vuelve negativa la relación ganancia/pérdida media.
   "Faltaban X puntos al TP" no significa que el precio los hubiera alcanzado.

Para métricas de varias semanas (esperanza, rachas, franjas horarias), usa
`labs/macroflow_v0_9_5_import/scripts/discretionary_report.py`.
