# Ingeniería inversa: skills de trading de tradermonty

**Origen:** [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills),
commit `cb0f3aa`, licencia MIT. Encontradas vía [skill-issue.in](https://skill-issue.in/).
**Regla:** reimplementación en sala limpia. Ninguna línea de su código está en CALIOPE.

## Método

| Paso | Herramienta | Resultado | Tipo de evidencia |
|---|---|---|---|
| 1. Inventario con procedencia | REA `import-reference-source` | Commit, hashes raíz y lenguajes de cada skill (`rea_provenance.json`) | observation |
| 2. Especificación publicada | Lectura de `SKILL.md` y `references/` | Reglas, estados y salidas declaradas | observation |
| 3. Comportamiento real | Ejecución como caja negra, aislada (`python -I`), con entradas diseñadas | Tabla de abajo | observation |
| 4. Diseño propio | Especificación en CALIOPE adaptada a SimpleFX | `tools/discipline/` | derivation |
| 5. Verificación | `tests/test_discipline.py` | 14 pruebas | derivation |

REA no reconstruye grafos de llamadas en Python (`relationships: 0`); su valor
aquí es fijar exactamente qué versión se estudió, para que la ingeniería inversa
sea reproducible.

## Comportamiento observado (caja negra, `pre-trade-discipline-gate`)

| Entrada diseñada | Salida observada | Lo que adoptamos |
|---|---|---|
| Checklist completo y riesgo real < planeado, **sin** informes de cortacircuitos ni régimen | `REVIEW_REQUIRED` | Sin historial no hay GO (ya lo hacíamos) |
| `stop_predefined: false` y riesgo no numérico a la vez | `NO_GO` | `NO_GO` tiene prioridad sobre `REVIEW_REQUIRED` |
| Intención `WATCHLIST` | `NO_ACTIONABLE_ORDERS` | Campo `intent` en el plan |
| Riesgo real > planeado con `--fail-on-non-go` | código de salida 2 | Códigos de salida distintos por decisión |

## Qué se implementó en CALIOPE

| Idea de origen | Implementación propia | Diferencia deliberada |
|---|---|---|
| `drawdown-circuit-breaker`: límites diario/semanal/mensual con fecha de liberación y enfriamiento por racha | `rules.circuit_breaker()`: `HALTED` / `COOLDOWN` / `TRADING_ALLOWED` / `UNKNOWN`, con `release_at` | Lee el export de SimpleFX en vez de su estado YAML; límites por defecto 10/20/30 % y 12 h de enfriamiento, acordes a 5 % de riesgo por operación |
| `pre-trade-discipline-gate`: checklist, prioridad de decisiones, intenciones no operables | `pre_trade_gate.py` con `intent`, cortacircuitos integrado y checklist de ruptura/retesteo | Calcula el riesgo desde entrada/stop/lotes en lugar de pedirlo; añade ventanas de sesión y beneficio/riesgo mínimo |
| `trade-performance-coach`: etiquetas de comportamiento con evidencia, veredicto escalonado, reglas para la siguiente sesión, compuerta humana | `session_review._coach()`: `premature_exit`, `revenge_trade`, `fomo_entry`, `size_creep`, `overconfidence_after_winner`, `rule_drift`; veredictos OK/WARN/REVIEW_REQUIRED/RULE_VIOLATION/COOL_DOWN; opción por defecto `journal_only` | Las etiquetas se derivan solo de datos del export (horas, tamaños, P&L, TP/SL); la confianza baja cuando el dato es inferido; textos propios en español |

## Qué se descartó y por qué

- **`trader-memory-core`** (ciclo de vida de tesis en YAML, ~467 KB): demasiado
  pesado para un scalper intradía; el export de SimpleFX ya da lo necesario.
- **`stop_moved`**: el export no guarda el historial del stop; marcarlo sería inventar evidencia.
- **Skills con FMP, FINVIZ o Alpaca**: requieren claves de pago o ejecutan órdenes;
  CALIOPE no maneja credenciales ni envía órdenes.
- **Instalar sus scripts tal cual**: código de terceros con acceso a disco dentro
  del repo es justo la superficie que explotó GhostAction.
