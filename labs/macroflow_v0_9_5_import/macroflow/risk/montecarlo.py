"""Auditoría Monte Carlo de drawdown y probabilidad de ruina.

Sustituye a `structural_risk_audit` del borrador original, que tenía dos fallos:
  1. Medía el drawdown sobre el PnL acumulado (sin capital inicial). Con un pico
     cercano a 0 el cociente explota y el VaR resultante no significa nada.
  2. Decía "permutaciones" pero remuestreaba con reemplazo trade a trade, lo que
     destruye cualquier autocorrelación (rachas) del sistema.

Aquí: curva = capital_inicial + PnL acumulado, bootstrap circular por bloques
opcional (block_size > 1 conserva rachas) y todo vectorizado en NumPy.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np


@dataclass(frozen=True)
class MonteCarloResult:
    n_sims: int
    n_trades: int
    dd_median: float      # drawdown máximo mediano (fracción, 0.2 = 20 %)
    dd_var: float         # percentil `confidence` del drawdown máximo
    dd_cvar: float        # media de la cola más allá del VaR
    p_ruin: float         # P(drawdown máximo >= ruin_level)
    final_equity_p05: float
    confidence: float
    ruin_level: float

    def to_dict(self) -> dict:
        return asdict(self)


def _bootstrap_indices(n: int, n_sims: int, block_size: int, rng: np.random.Generator) -> np.ndarray:
    if block_size <= 1:
        return rng.integers(0, n, size=(n_sims, n))
    n_blocks = -(-n // block_size)
    starts = rng.integers(0, n, size=(n_sims, n_blocks))
    idx = (starts[:, :, None] + np.arange(block_size)) % n  # circular
    return idx.reshape(n_sims, -1)[:, :n]


def monte_carlo_drawdown(
    pnl: np.ndarray,
    initial_capital: float,
    n_sims: int = 5000,
    confidence: float = 0.95,
    ruin_level: float = 0.30,
    block_size: int = 1,
    seed: int | None = None,
    max_cells: int = 50_000_000,
) -> MonteCarloResult:
    """`pnl` en moneda de la cuenta, un valor por plan de trade completo.

    Nota: si el sistema dimensiona como % de equity (compuesto), remuestrear PnL
    en moneda es una aproximación; para eso pasa pnl = R * riesgo_por_trade * capital.
    """
    pnl = np.asarray(pnl, dtype=float)
    pnl = pnl[np.isfinite(pnl)]
    if initial_capital <= 0:
        raise ValueError("initial_capital debe ser > 0")
    if pnl.size < 2:
        raise ValueError("Se necesitan al menos 2 trades para el Monte Carlo")

    rng = np.random.default_rng(seed)
    n = pnl.size
    # Trocear para no reventar memoria con historiales largos
    chunk = max(1, min(n_sims, max_cells // max(n, 1)))
    max_dd = np.empty(n_sims)
    final_eq = np.empty(n_sims)
    done = 0
    while done < n_sims:
        k = min(chunk, n_sims - done)
        idx = _bootstrap_indices(n, k, block_size, rng)
        eq = initial_capital + np.cumsum(pnl[idx], axis=1)
        eq = np.concatenate([np.full((k, 1), initial_capital), eq], axis=1)
        peak = np.maximum.accumulate(eq, axis=1)          # peak >= capital inicial > 0
        dd = 1.0 - eq / peak                              # > 1 si la cuenta pasa a negativo
        max_dd[done:done + k] = dd.max(axis=1)
        final_eq[done:done + k] = eq[:, -1]
        done += k

    var = float(np.quantile(max_dd, confidence))
    tail = max_dd[max_dd >= var]
    return MonteCarloResult(
        n_sims=n_sims,
        n_trades=n,
        dd_median=float(np.median(max_dd)),
        dd_var=var,
        dd_cvar=float(tail.mean()) if tail.size else var,
        p_ruin=float(np.mean(max_dd >= ruin_level)),
        final_equity_p05=float(np.quantile(final_eq, 0.05)),
        confidence=confidence,
        ruin_level=ruin_level,
    )


def bootstrap_expectancy_ci(
    values: np.ndarray, n_boot: int = 1000, alpha: float = 0.10, seed: int | None = None
) -> tuple[float, float, float]:
    """IC bootstrap de la media (esperanza por trade). Devuelve (media, low, high)."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return float("nan"), float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    means = v[rng.integers(0, v.size, size=(n_boot, v.size))].mean(axis=1)
    return float(v.mean()), float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))
