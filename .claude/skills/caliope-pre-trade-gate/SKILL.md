---
name: caliope-pre-trade-gate
description: Run CALIOPE's offline pre-trade discipline gate before the person places any manual order (SimpleFX, US100/US500/US30 scalping). Use when they ask "¿entro?", "dame entrada", "revisa mi plan", share a setup with entry/stop/target, or are about to click buy/sell. Returns GO / NO_GO / REVIEW_REQUIRED against their written rules; it never places orders.
---

# Compuerta pre-operación de CALIOPE

Adaptada de `pre-trade-discipline-gate` y `drawdown-circuit-breaker`
(tradermonty/claude-trading-skills, MIT). Reimplementada: no hay código copiado.

## Cuándo usarla

Siempre **antes** de que la persona coloque una orden manual, incluso si la
señal parece obvia. Es justo cuando más se rompen las reglas.

## Pasos

1. Pide o completa un plan en JSON (no inventes valores; si falta uno, déjalo
   fuera y la compuerta pedirá revisión):

   ```json
   {
     "account": {"equity": 45.87},
     "open_positions": 0,
     "order": {"symbol": "US100", "side": "BUY", "entry": 29972, "stop": 29930,
               "target": 30056, "lots": 0.05},
     "checklist": {"range_marked": true, "breakout_close_confirmed": true,
                   "retest_confirmed": true, "news_clear": true}
   }
   ```

   Para símbolos distintos de US100/US500/US30 añade
   `"value_per_point_per_lot"` (USD por punto con 1 lote).

2. Ejecuta, idealmente con el export "closed orders" de SimpleFX del día:

   ```bash
   python tools/discipline/pre_trade_gate.py --plan plan.json \
     --simplefx-csv SFX_closed_orders_....csv --journal state/journal/pre_trade.jsonl
   ```

   Para registrar una idea sin operarla usa `"intent": "WATCHLIST"`: el
   resultado será `NO_ACTIONABLE_ORDERS`.

3. Comunica el resultado tal cual (si hay cortacircuitos activo, di la hora de
   `release_at`):
   - `GO`: el plan cumple sus reglas. Recuerda que no es una predicción.
   - `NO_GO`: lista las razones bloqueantes y **no** propongas cómo saltarlas.
     Tiene prioridad sobre `REVIEW_REQUIRED`.
   - `REVIEW_REQUIRED`: falta un dato; pídelo. Nunca lo supongas.

## Reglas por defecto (`tools/discipline/rules.py`, editables con `--rules`)

| Regla | Valor |
|---|---|
| Riesgo máximo por operación | 5% de la cuenta |
| Beneficio/riesgo mínimo | 2.0 |
| Operaciones por día | 3 (incluye posiciones abiertas) |
| Pérdidas seguidas | 2 → sesión terminada |
| Cortacircuitos | pérdida diaria 10%, semanal 20%, mensual 30% (`HALTED` hasta el día, lunes o mes siguiente); racha de 2 pérdidas → `COOLDOWN` 12 h |
| Enfriamiento tras una pérdida | 10 min |
| Ventanas (America/Guayaquil) | 19:00–21:00, 02:00–03:30, 08:30–10:30 |
| Checklist | rango marcado, cierre de ruptura, retesteo confirmado, sin noticias |

## Límites

- No es asesoría financiera ni predice el mercado.
- Sin el CSV no puede verificar límites diarios: devuelve `REVIEW_REQUIRED`.
- El export de SimpleFX solo trae operaciones cerradas; declara las abiertas en
  `open_positions`.
