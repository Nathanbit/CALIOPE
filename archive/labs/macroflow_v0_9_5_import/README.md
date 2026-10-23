# MacroFlow v0.9.5 — importación experimental en cuarentena

> **No es una versión validada del indicador.** El Pine incluido originalmente
> es solo un fragmento y la réplica Python todavía no tiene paridad con el
> modelo canónico. En esta copia Caliope bloquea por defecto el optimizador,
> la auto-aprobación, el reinicio recursivo y MT5. Consulte
> `REVIEW_STATUS.md` antes de ejecutar cualquier componente.

## Arranque rápido: panel web local

- **Windows:** doble clic en `run_local.bat`
- **Mac/Linux:** `./run_local.sh`

La primera vez crea un entorno `.venv` e instala dependencias (un par de minutos). Luego abre
**http://localhost:8000** con: riesgo en vivo y kill switch, alertas de prueba, journal,
el bucle de automejora con sus gates y gráfico por fold, y el registro de versiones con
aprobación y descarga del `.pine`. Requiere Python 3.11+.

Por defecto usa el broker simulado. Para MT5 (solo Windows): `pip install -e ".[mt5]"` en el
`.venv`, y `MF_BROKER=mt5` en un archivo `.env` junto a `pyproject.toml`.

El panel solo responde a peticiones desde tu propia máquina; aunque expongas `/webhook` a
internet tras un proxy, `/` y `/api/*` devuelven 403 desde fuera.

## Estructura

```
macroflow/
├── macroflow/
│   ├── config.py                  Settings (env MF_*): secret, broker, símbolos, límites de riesgo
│   ├── bridge/
│   │   ├── app.py                 FastAPI: POST /webhook (202 en ms), GET /health
│   │   ├── schemas.py             Contrato JSON Pine v6 ↔ puente (validación de SL/TP por lado)
│   │   └── router.py              Worker único: guardas → lotaje → tramos 50/30/20 → broker → journal
│   ├── execution/
│   │   ├── base.py                Interfaz Broker
│   │   ├── paper.py               Simulado (Linux/macOS, tests)
│   │   └── mt5_broker.py          MetaTrader 5 (solo Windows)
│   ├── risk/
│   │   ├── sizing.py              Lotes por riesgo fijo, redondeo a la baja, reparto en tramos
│   │   ├── guards.py              Estado compartido: risk_multiplier, Daily Loss Breaker, kill switch
│   │   └── montecarlo.py          DD VaR/CVaR, prob. de ruina, IC bootstrap de la esperanza
│   ├── audit/tradelog.py          Paso A: export de TradingView → planes completos → esperanza por grado/hora
│   ├── strategy/
│   │   ├── reference.py           Réplica Python fiel del indicador (capas 1-3)
│   │   └── synthetic.py           Datos sintéticos para tests/demo
│   └── optimization/
│       ├── space.py               Espacio de parámetros dinámico (JSON, límites duros + búsqueda adaptativa)
│       ├── walkforward.py         Folds con embargo y warmup
│       ├── metrics.py             PF, esperanza R, t-stat
│       ├── pine_injector.py       Paso C: escribe un .pine versionado (nunca sobrescribe)
│       └── recursive.py           RecursiveOptimizer (Pasos A-D) + ModelRegistry
├── config/param_space.json        Qué se optimiza y entre qué límites (nombres = variables del .pine)
├── pine/                          .pine base y versiones generadas
├── registry/                      index.json + v<versión>.json (parámetros + métricas OOS)
├── state/                         risk_state.json, journal.jsonl, KILL
├── scripts/
│   ├── run_optimizer.py           CLI del bucle (--demo, --data, --trades, --approve, --rearm)
│   └── pybroker_walkforward.py    Walk-forward + bootstrap 1000 en PyBroker 2.0
└── tests/                         20 tests (puente, riesgo, optimizador)
```

## Flujo en vivo

```
TradingView (Pine v6, alerta con {{strategy.order.alert_message}})
   │ HTTPS :443  (TradingView solo envía a 80/443)
   ▼
Caddy/nginx (TLS) → uvicorn 127.0.0.1:8000
   ▼
/webhook: secret (compare_digest) → antigüedad (<120 s) → model_version activa → dedupe → 202
   ▼  cola de 1 hilo (la API de MT5 no es thread-safe)
RiskGuard (kill switch · risk_multiplier · pérdida diaria) → sizing por equity real → 3 tramos → MT5
   ▼
state/journal.jsonl
```

El optimizador y el puente solo se comunican por `state/risk_state.json`: el freno escribe
`risk_multiplier = 0` y el puente bloquea entradas desde la siguiente alerta (los cierres siempre pasan).

**Despliegue**: el paquete `MetaTrader5` solo funciona en Windows con el terminal abierto, así que
puente + terminal van en un VPS Windows cercano al servidor del broker. El optimizador puede ir en
cualquier máquina con acceso a `state/` y `registry/`.

```
pip install -e ".[dev,mt5]"           # en Windows
set MF_BROKER=mt5 & set MF_WEBHOOK_SECRET=...
uvicorn macroflow.bridge.app:app --host 127.0.0.1 --port 8000
```

## Bucle de automejora (recursivo y autónomo)

Desde el panel: **Iniciar bucle autónomo**. A partir de ahí se ejecuta solo cada N segundos:

1. Un único optimizador persistente: cada iteración parte del espacio de búsqueda que dejó
   la anterior, recentrado alrededor del último óptimo, y compite contra el modelo activo.
2. Si el candidato pasa los gates y mejora al activo, se crea la versión siguiente
   (v0.9.5 → v0.9.6 → …), su `.pine`, y con *Auto-aprobar* se activa sola.
3. Tras 3 iteraciones sin mejora, reabre el espacio a los límites completos para no quedarse
   atascado en un óptimo local.
4. El espacio aprendido (`state/param_space_current.json`) y el estado del bucle
   (`state/autoloop.json`) se guardan: si cierras y vuelves a abrir, **el bucle retoma solo**.
5. Se detiene únicamente por el freno de seguridad (modelo activo degradado → riesgo 0),
   por el máximo de iteraciones o porque lo paras tú. Tras un freno el rearme es manual.

Sin panel, el mismo motor desde terminal:

```
python scripts/run_optimizer.py --forever --interval 3600 --auto-approve            # demo
python scripts/run_optimizer.py --forever --data btc_1h.csv --interval 3600 --auto-approve
python scripts/run_optimizer.py --data btc_1h.csv --trades list_of_trades.csv        # 1 iteración
python scripts/run_optimizer.py --approve 0.9.5     # activa: el puente exige esa versión
python scripts/run_optimizer.py --rearm             # tras un freno, solo manual
```

`MF_AUTOLOOP_ON_START=1` en `.env` arranca el bucle con el servidor aunque nunca lo hayas iniciado.

Con datos CSV el bucle solo optimiza cuando el CSV trae barras nuevas: reoptimizar sobre el
mismo tramo fuera de muestra lo convertiría en dentro de muestra. Reemplaza el CSV periódicamente
(o conecta un proveedor de datos) para que aprenda de verdad.

Gates fuera de muestra (todos obligatorios): ≥40 trades, PF ≥ 1.10, IC bootstrap 90 % de la
esperanza > 0, DD al 95 % (Monte Carlo por bloques) ≤ 25 %, P(ruina 30 %) ≤ 1 %.

- **Freno**: si el modelo *activo* falla los gates en datos nuevos → riesgo a 0 y el bucle para.
- **Candidato**: se registra solo si pasa los gates *y* mejora la esperanza OOS del activo en ≥0.02 R.
  Queda `candidate` hasta `--approve` (o `--auto-approve`, desaconsejado).
- **Sin datos nuevos, no hay iteración**: reoptimizar sobre el mismo OOS lo convierte en in-sample.
- **Estabilidad**: el informe trae el coef. de variación de cada parámetro entre folds. Valores >0.5
  indican que el "óptimo" es ruido.

## Hallazgos en el indicador v0.9.4

1. `ta.log` no existe en Pine → `math.log`.
2. `not ta.change(macroSPY) == 0` se evalúa como `(not ta.change(...)) == 0` y no compila en v6 → `ta.change(macroSPY) != 0`.
3. **Las salidas no emiten alerta**: `strategy.close` no tiene `alert_message`, así que MT5 nunca recibiría el cierre estructural. Corregido en `pine/MacroFlow_v0.9.4.pine`.
4. El JSON de alerta (`signal/model/sl`) no lleva secret, id, ticker, precio ni hora → sustituido por `f_alert()`.
5. Si `transTf` es mayor que el TF del gráfico, SPY solo cambia una vez por vela de `transTf`: `transmissionActive` estará en falso la mayoría de barras y Pearson se calcula sobre una serie llena de ceros. Calcular la correlación en `transTf` dentro del `request.security`.
6. `isDxyExtreme` se calcula pero no bloquea nada (solo VIX entra en el setup).
7. `request.security(..., close[1], lookahead_off)` no repinta, pero añade una vela de retraso extra; la forma estándar es `close[1]` con `lookahead_on`.

## Estado de las integraciones pedidas

| Pieza | Estado |
|---|---|
| PyBroker walk-forward + bootstrap 1000 | Hecho, probado contra lib-pybroker 2.0.1 |
| Optuna | Hecho (propio, con walk-forward); PyBroker 2.0 también trae `Strategy.optimize` |
| MT5 | Hecho, **no probado contra un terminal real**: validar en cuenta demo |
| Monte Carlo de ruina (finmath-lib es Java) | Reimplementado en NumPy |
| Filtro ML (XGBoost/RF) | Pendiente: requiere etiquetas por triple barrera y CV purgada |
| Optimización multi-activo GPU | Pendiente; con 3 activos NumPy resuelve en microsegundos |
| QML (qiskit/pennylane) | No incluido: no aporta ventaja demostrada para este tamaño de problema |
