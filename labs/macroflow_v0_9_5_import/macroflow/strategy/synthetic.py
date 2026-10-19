"""Datos sintéticos (BTC-like + SPY con fines de semana congelados) para tests y demos."""
from __future__ import annotations

import numpy as np
import pandas as pd


def make_market(n: int = 12_000, seed: int = 1, freq: str = "1h", drift_regimes: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq=freq, tz="UTC")
    common = rng.normal(0, 0.004, n)
    mu = np.zeros(n)
    if drift_regimes:  # tramos con tendencia persistente para que exista algo que aprender
        for start in range(0, n, 400):
            mu[start:start + 400] = rng.choice([-1, 1]) * rng.uniform(0.0002, 0.0007)
    r_asset = mu + 0.6 * common + rng.normal(0, 0.006, n)
    r_ref = 0.5 * common + rng.normal(0, 0.002, n)
    weekend = idx.dayofweek >= 5
    r_ref[weekend] = 0.0                      # SPY congelado: "contexto cerrado"
    close = 40_000 * np.exp(np.cumsum(r_asset))
    open_ = np.concatenate([[close[0]], close[:-1]])
    spread = np.abs(rng.normal(0, 0.003, n)) * close
    vix = 18 + np.cumsum(rng.normal(0, 0.15, n)) * 0.2
    vix[weekend] = np.nan
    return pd.DataFrame({
        "open": open_, "close": close,
        "high": np.maximum(open_, close) + spread, "low": np.minimum(open_, close) - spread,
        "ref_close": 500 * np.exp(np.cumsum(r_ref)),
        "vix": pd.Series(vix, index=idx).ffill().clip(lower=9).to_numpy(),
    }, index=idx)
