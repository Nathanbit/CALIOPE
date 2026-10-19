"""Réplica en Python del indicador MacroFlow v0.9.4 (capas 1-3), fiel al .pine.

El optimizador no puede ejecutar Pine, así que optimiza esta réplica. Los
nombres de parámetros son los MISMOS que las variables del .pine, para que el
inyector pueda escribirlos de vuelta sin tabla de traducción.

Antes de confiar en cualquier parámetro del loop, compara esta réplica con el
Strategy Tester sobre el mismo histórico (mismas fechas de entrada/salida).

Columnas esperadas en df (índice datetime UTC, timeframe del gráfico):
  open, high, low, close   activo operado
  ref_close                SPY tal y como lo ve Pine: request.security(..., close[1]) ya
                           alineado al timeframe del gráfico (forward-fill del TF de transmisión)
  vix (opcional)           VIX alineado igual; si falta, el filtro Tail Risk queda desactivado

Diferencias deliberadas respecto al .pine (son correcciones, ver README):
  * transmissionActive se calcula como ta.change(SPY) != 0. En el .pine está escrito
    `not ta.change(macroSPY) == 0`, que por precedencia es `(not ta.change(...)) == 0`.
  * ta.log no existe en Pine; la réplica usa log natural (math.log en Pine).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_PARAMS = {
    # Capa 1
    "transNormLen": 100, "transExtremeZ": 2.0,
    # Capa 2
    "correlationLookback": 50, "minAbsCorrelation": 0.10, "minCorrelationTStat": 2.0,
    "requireCorrelationEvidence": True,
    # Capa 3
    "fastLen": 20, "slowLen": 50, "longTrendLen": 200, "trendEstimator": "HMA blend",
    # Riesgo
    "atrLen": 14, "slAtrMult": 2.0,
    # Simulación
    "cost_bps": 6.0, "session_tz": "America/New_York", "blocked_hours": [],
}


def _wma(x: np.ndarray, n: int) -> np.ndarray:
    n = max(1, int(n))
    w = np.arange(1, n + 1, dtype=float)
    out = np.full(x.shape, np.nan)
    if len(x) >= n:
        out[n - 1:] = np.convolve(x, w[::-1] / w.sum(), mode="valid")
    return out


def hma(x: np.ndarray, n: int) -> np.ndarray:
    """ta.hma: WMA(2*WMA(n/2) - WMA(n), floor(sqrt(n)))."""
    half, root = max(1, n // 2), max(1, int(np.floor(np.sqrt(n))))
    diff = 2 * _wma(x, half) - _wma(x, n)
    out = np.full(x.shape, np.nan)
    valid = ~np.isnan(diff)
    if valid.sum() >= root:
        out[valid] = _wma(diff[valid], root)
    return out


def ema(s: pd.Series, n: int) -> pd.Series:
    # ta.ema arranca con SMA de las n primeras barras; ewm(adjust=False) converge al mismo valor tras ~3n barras
    return s.ewm(span=int(n), adjust=False, min_periods=int(n)).mean()


def zscore(s: pd.Series, n: int) -> pd.Series:
    m, sd = s.rolling(n).mean(), s.rolling(n).std(ddof=0)   # ta.stdev es poblacional
    return ((s - m) / sd).where(sd > 0, 0.0)


def compute_signals(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    c = df["close"]

    # ---- Capa 1: Tail Risk (solo VIX bloquea, igual que el .pine) ----
    if "vix" in df:
        vix_extreme = zscore(df["vix"], int(p["transNormLen"])).abs() >= p["transExtremeZ"]
    else:
        vix_extreme = pd.Series(False, index=df.index)

    # ---- Capa 2: Pearson + t-stat sobre log-retornos emparejados ----
    n = int(p["correlationLookback"])
    ra, rb = np.log(c / c.shift()), np.log(df["ref_close"] / df["ref_close"].shift())
    r = ra.rolling(n).corr(rb).fillna(0.0)       # Pine devuelve 0 si el denominador es 0
    t = (r * np.sqrt((n - 2) / (1 - r.clip(-0.999999, 0.999999) ** 2))).where(r.abs() < 1, 0.0)
    corr_valid = (r.abs() >= p["minAbsCorrelation"]) & (t.abs() >= p["minCorrelationTStat"])
    transmission_active = df["ref_close"].diff().fillna(0) != 0

    idx = df.index if df.index.tz is not None else df.index.tz_localize("UTC")
    local = idx.tz_convert(p.get("session_tz", "America/New_York"))
    context_closed = pd.Series(local.dayofweek >= 5, index=df.index)
    if p.get("requireCorrelationEvidence", True):
        gate = corr_valid & transmission_active & ~context_closed
    else:
        gate = ~context_closed

    # ---- Capa 3: ER de Kaufman -> mezcla HMA/EMA ----
    fl = int(p["fastLen"])
    signal_d = (c - c.shift(fl)).abs()
    noise_d = c.diff().abs().rolling(fl).sum()
    er = (signal_d / noise_d).where(noise_d > 0, 0.0).fillna(0.0)
    sl_len = int(p["slowLen"])
    core_ema = ema(c, sl_len)
    core_hma = pd.Series(hma(c.to_numpy(float), sl_len), index=df.index)
    est = p.get("trendEstimator", "HMA blend")
    w_hma = er * 0.5 if est == "HMA blend" else (1.0 if est == "HMA" else 0.0)
    structural = core_hma * w_hma + core_ema * (1 - w_hma)
    long_ema = ema(c, int(p["longTrendLen"]))
    bull = (c > structural) & (c > long_ema)
    bear = (c < structural) & (c < long_ema)

    hour_ok = ~np.isin(local.hour, list(p.get("blocked_hours", [])))
    allowed = gate & ~vix_extreme & hour_ok

    tr = pd.concat([df.high - df.low, (df.high - c.shift()).abs(), (df.low - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / int(p["atrLen"]), adjust=False).mean()   # ta.atr = RMA

    return pd.DataFrame({
        "long_setup": bull & allowed, "short_setup": bear & allowed,
        "structural": structural, "atr": atr, "er": er, "pearson_r": r, "t_stat": t,
    }, index=df.index)


def backtest(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Simula el bloque de órdenes del .pine. Devuelve trades con R-múltiplos netos.

    Pine con process_orders_on_close=false: la orden se decide al cierre de la
    barra i y se llena en la apertura de i+1. Aquí igual.
    Entradas: setup y sin posición. Salidas: cierre cruza structuralTrend (en la
    apertura siguiente) o stop ATR intrabarra.
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    s = compute_signals(df, p)
    o, h, lo, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    ls, ss = s["long_setup"].to_numpy(bool), s["short_setup"].to_numpy(bool)
    st, atr = s["structural"].to_numpy(float), s["atr"].to_numpy(float)
    cost, times = p["cost_bps"] / 1e4, df.index
    warm = int(max(p["longTrendLen"], p["slowLen"], p["correlationLookback"], p["transNormLen"]))

    trades, pos, entry, stop, risk, t_in = [], 0, 0.0, 0.0, 0.0, None
    pending_exit = False
    for i in range(warm, len(df) - 1):
        if pos != 0:
            if pending_exit:
                px, reason = o[i], "structural"
            elif (pos == 1 and lo[i] <= stop) or (pos == -1 and h[i] >= stop):
                px, reason = (min(o[i], stop) if pos == 1 else max(o[i], stop)), "stop"
            else:
                px = None
            if px is not None:
                r = ((px - entry) * pos - cost * (entry + px)) / risk
                trades.append((t_in, times[i], "long" if pos == 1 else "short", entry, px, r, reason))
                pos, pending_exit = 0, False
            elif (pos == 1 and c[i] < st[i]) or (pos == -1 and c[i] > st[i]):
                pending_exit = True
        if pos == 0 and not np.isnan(atr[i]) and atr[i] > 0 and (ls[i] or ss[i]):
            pos = 1 if ls[i] else -1
            entry, t_in = o[i + 1], times[i + 1]
            risk = p["slAtrMult"] * atr[i]
            stop = entry - pos * risk
    return pd.DataFrame(trades, columns=["entry_time", "exit_time", "side", "entry", "exit", "r", "exit_reason"])
