"""Walk-forward + bootstrap de MacroFlow en PyBroker 2.0 (versión corregida del script original).

Qué cambia respecto al borrador:
  * `pybroker.register_indicator` no existe -> `pybroker.indicator(nombre, fn, **kwargs)`.
  * La ER usaba np.roll (envuelve el final de la serie al principio) y sumaba el ruido
    de TODA la serie, no una ventana móvil. Ahora reutiliza la réplica fiel al .pine.
  * `result.bootstrap(eval_vix_regime=..., random_samples=1000)` no existe: el bootstrap
    se pide con walkforward(calc_bootstrap=True) y StrategyConfig(bootstrap_samples=1000),
    y se lee en `result.bootstrap`.
  * ctx.preds() con un modelo que devuelve None falla: el filtro ML queda fuera hasta
    que haya un clasificador entrenado de verdad (ver README, fase 3).
  * stop_loss_pct=2.0 fijo contradecía el stop ATR: ahora ctx.stop_loss = slAtrMult*ATR (en puntos).
  * Yahoo Finance solo sirve velas de 15m de los últimos ~60 días: con start_date=2025-01-01
    y timeframe '15m' no hay datos. Para histórico intradía largo usa CCXT/Binance o un CSV.

Uso:  python scripts/pybroker_walkforward.py            (datos sintéticos)
      python scripts/pybroker_walkforward.py --csv datos.csv
CSV: columnas date, symbol, open, high, low, close, ref_close, vix
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pybroker
from pybroker import ExecContext, Strategy, StrategyConfig

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from macroflow.strategy.reference import DEFAULT_PARAMS, compute_signals  # noqa: E402

PARAMS = dict(DEFAULT_PARAMS)
RISK_PCT = 0.5


def _signals(data) -> pd.DataFrame:
    df = pd.DataFrame({"open": data.open, "high": data.high, "low": data.low, "close": data.close,
                       "ref_close": data.ref_close, "vix": data.vix},
                      index=pd.DatetimeIndex(data.date).tz_localize("UTC"))
    return compute_signals(df, PARAMS)


# PyBroker cachea por indicador; cada uno extrae su columna de la misma réplica
ind_long = pybroker.indicator("mf_long", lambda d: _signals(d)["long_setup"].to_numpy(float))
ind_short = pybroker.indicator("mf_short", lambda d: _signals(d)["short_setup"].to_numpy(float))
ind_struct = pybroker.indicator("mf_struct", lambda d: _signals(d)["structural"].to_numpy(float))
ind_atr = pybroker.indicator("mf_atr", lambda d: _signals(d)["atr"].to_numpy(float))


def exec_macroflow(ctx: ExecContext) -> None:
    close = ctx.close[-1]
    struct, atr = ctx.indicator("mf_struct")[-1], ctx.indicator("mf_atr")[-1]
    if not np.isfinite(struct) or not np.isfinite(atr) or atr <= 0:
        return
    pos_long, pos_short = ctx.long_pos(), ctx.short_pos()

    # Salidas estructurales (igual que strategy.close del .pine)
    if pos_long and close < struct:
        ctx.sell_all_shares()
        return
    if pos_short and close > struct:
        ctx.cover_all_shares()
        return
    if pos_long or pos_short:
        return

    sl_dist = PARAMS["slAtrMult"] * atr
    qty = float(ctx.total_equity) * RISK_PCT / 100.0 / sl_dist     # riesgo fijo por trade
    if qty <= 0:
        return
    if ctx.indicator("mf_long")[-1] > 0:
        ctx.buy_shares, ctx.stop_loss = qty, sl_dist
    elif ctx.indicator("mf_short")[-1] > 0:
        ctx.sell_shares, ctx.stop_loss = qty, sl_dist


def load_data(csv: str | None) -> pd.DataFrame:
    if csv:
        return pd.read_csv(csv, parse_dates=["date"])
    from macroflow.strategy.synthetic import make_market
    frames = []
    for sym, seed in (("BTCUSD", 3), ("ETHUSD", 4)):
        m = make_market(6000, seed=seed)
        m.index = m.index.tz_localize(None)
        frames.append(m.reset_index(names="date").assign(symbol=sym))
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--windows", type=int, default=5)
    a = ap.parse_args()

    pybroker.register_columns("ref_close", "vix")
    data = load_data(a.csv)
    cfg = StrategyConfig(initial_cash=10_000, enable_fractional_shares=True, bootstrap_samples=1000,
                         fee_mode=pybroker.FeeMode.ORDER_PERCENT, fee_amount=0.06, buy_delay=1, sell_delay=1)
    strategy = Strategy(data, data["date"].min(), data["date"].max(), cfg)
    strategy.add_execution(exec_macroflow, sorted(data["symbol"].unique()),
                           indicators=[ind_long, ind_short, ind_struct, ind_atr])
    result = strategy.walkforward(windows=a.windows, train_size=0.5, calc_bootstrap=True, warmup=300)

    m = result.metrics
    print(f"trades={m.trade_count}  PF={m.profit_factor:.3f}  win%={m.win_rate:.1f}  "
          f"maxDD%={m.max_drawdown_pct:.2f}  sharpe={m.sharpe:.3f}")
    if result.bootstrap is not None:
        print("\nIntervalos de confianza bootstrap (1000 muestras):")
        print(result.bootstrap.conf_intervals.to_string())
        print("\nDrawdown bootstrap:")
        print(result.bootstrap.drawdown_conf.to_string())


if __name__ == "__main__":
    main()
