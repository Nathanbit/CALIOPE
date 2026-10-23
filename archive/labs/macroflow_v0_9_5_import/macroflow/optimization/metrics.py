from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np


@dataclass(frozen=True)
class TradeStats:
    n: int
    profit_factor: float
    expectancy_r: float
    win_rate: float
    t_stat: float
    max_dd_r: float

    def to_dict(self) -> dict:
        return asdict(self)


def trade_stats(r: np.ndarray) -> TradeStats:
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    n = r.size
    if n == 0:
        return TradeStats(0, 0.0, 0.0, 0.0, 0.0, 0.0)
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    pf = float(wins / losses) if losses > 0 else (float("inf") if wins > 0 else 0.0)
    sd = r.std(ddof=1) if n > 1 else 0.0
    t = float(r.mean() / sd * np.sqrt(n)) if sd > 0 else 0.0
    eq = np.concatenate([[0.0], np.cumsum(r)])
    max_dd = float(np.max(np.maximum.accumulate(eq) - eq))
    return TradeStats(n, pf, float(r.mean()), float((r > 0).mean()), t, max_dd)


def robust_score(r: np.ndarray, min_trades: int) -> float:
    """Objetivo para Optuna: t-stat de la esperanza en R.

    Se prefiere al Profit Factor puro porque premia consistencia y número de
    trades; el PF se dispara con 5 trades ganadores y es muy fácil de sobreajustar.
    """
    s = trade_stats(r)
    if s.n < min_trades:
        return float("-inf")
    return s.t_stat
