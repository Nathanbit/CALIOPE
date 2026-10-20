"""Verificadores deterministas de causalidad para la réplica Python.

Inspirado en los comandos ``lookahead-analysis`` y ``recursive-analysis`` de
Freqtrade (GPL-3.0). Es una reimplementación independiente de la idea, sin
código copiado.

Dos preguntas, dos pruebas:

1. **Lookahead** — ¿la señal de la barra *t* cambia si se borran las barras
   posteriores a *t*? Se calculan las señales con todo el histórico y otra vez
   con el histórico truncado en *t*. Cualquier diferencia significa que la
   función usó información que en tiempo real no existiría (``shift(-1)``,
   ``request.security`` sin ``[1]``, normalizaciones centradas, etc.).

2. **Calentamiento (recursive)** — ¿el valor de un indicador depende de cuántas
   barras previas se cargaron? Se recalcula empezando en distintos puntos y se
   comparan los valores sobre una ventana final común. Si difieren, Pine y
   Python solo coincidirán cuando ambos carguen el mismo histórico; el informe
   indica a partir de qué arranque el valor converge.

Ambas pruebas son de solo lectura, no tocan brokers y devuelven un informe
JSON-serializable con ``passed`` explícito. Una columna que no puede compararse
se marca como ``unknown``, nunca como coincidente (fallo cerrado).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable

import numpy as np
import pandas as pd

SignalFn = Callable[[pd.DataFrame], pd.DataFrame]


@dataclass
class ColumnMismatch:
    column: str
    bar_time: str
    full_value: object
    truncated_value: object


@dataclass
class LookaheadReport:
    checked_bars: int
    columns: list[str]
    mismatches: list[ColumnMismatch] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)

    @property
    def leaking_columns(self) -> list[str]:
        return sorted({m.column for m in self.mismatches})

    @property
    def passed(self) -> bool:
        return self.checked_bars > 0 and not self.mismatches and not self.unknown_columns

    def to_dict(self) -> dict:
        d = asdict(self)
        d["leaking_columns"] = self.leaking_columns
        d["passed"] = self.passed
        return d


@dataclass
class WarmupColumn:
    column: str
    max_abs_diff_by_start: dict[str, float]
    converged_from_start: int | None


@dataclass
class WarmupReport:
    starts: list[int]
    window_start: int
    compare_bars: int
    tolerance: float
    columns: list[WarmupColumn]

    @property
    def unstable_columns(self) -> list[str]:
        return [c.column for c in self.columns if c.converged_from_start is None]

    @property
    def recommended_startup_bars(self) -> int | None:
        """Barras previas mínimas para que todas las columnas converjan."""
        conv = [c.converged_from_start for c in self.columns]
        if not conv or any(v is None for v in conv):
            return None
        # Historia previa a la ventana comparada con la que todo ya coincide.
        return self.window_start - min(conv)

    @property
    def passed(self) -> bool:
        return bool(self.columns) and not self.unstable_columns

    def to_dict(self) -> dict:
        d = asdict(self)
        d["unstable_columns"] = self.unstable_columns
        d["recommended_startup_bars"] = self.recommended_startup_bars
        d["passed"] = self.passed
        return d


def _same(a: object, b: object, tol: float) -> bool | None:
    """True/False si se puede comparar; None si los tipos no son comparables."""
    a_na, b_na = pd.isna(a), pd.isna(b)
    if a_na or b_na:
        return bool(a_na and b_na)
    if isinstance(a, (bool, np.bool_)) or isinstance(b, (bool, np.bool_)):
        return bool(a) == bool(b)
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return a == b if type(a) is type(b) else None
    return abs(fa - fb) <= tol * max(1.0, abs(fa), abs(fb))


def _pick_bars(signals: pd.DataFrame, min_index: int, max_checks: int, seed: int) -> list[int]:
    """Prioriza barras con señal activa (donde un leak más importa) y completa al azar."""
    n = len(signals)
    if n <= min_index:
        return []
    bool_cols = [c for c in signals.columns if signals[c].dtype == bool]
    active = np.zeros(n, dtype=bool)
    for c in bool_cols:
        active |= signals[c].to_numpy(bool)
    candidates = np.arange(min_index, n - 1)  # la última barra no tiene futuro que borrar
    hot = candidates[active[candidates]]
    rng = np.random.default_rng(seed)
    picked = list(rng.choice(hot, size=min(len(hot), max_checks // 2), replace=False)) if len(hot) else []
    rest = np.setdiff1d(candidates, picked)
    k = max_checks - len(picked)
    if k > 0 and len(rest):
        picked += list(rng.choice(rest, size=min(k, len(rest)), replace=False))
    return sorted(int(i) for i in picked)


def lookahead_check(df: pd.DataFrame, fn: SignalFn, *, columns: Iterable[str] | None = None,
                    min_index: int = 0, max_checks: int = 40, tol: float = 1e-9,
                    seed: int = 0) -> LookaheadReport:
    full = fn(df)
    cols = list(columns) if columns is not None else list(full.columns)
    missing = [c for c in cols if c not in full.columns]
    report = LookaheadReport(checked_bars=0, columns=cols, unknown_columns=missing)
    cols = [c for c in cols if c in full.columns]
    unknown: set[str] = set(missing)
    for i in _pick_bars(full[cols], min_index, max_checks, seed):
        trunc = fn(df.iloc[: i + 1])
        report.checked_bars += 1
        t = df.index[i]
        for c in cols:
            if c not in trunc.columns:
                unknown.add(c)
                continue
            same = _same(full[c].iloc[i], trunc[c].iloc[-1], tol)
            if same is None:
                unknown.add(c)
            elif not same:
                report.mismatches.append(ColumnMismatch(c, str(t), _plain(full[c].iloc[i]),
                                                        _plain(trunc[c].iloc[-1])))
    report.unknown_columns = sorted(unknown)
    return report


def warmup_check(df: pd.DataFrame, fn: SignalFn, *, starts: Iterable[int],
                 compare_bars: int = 200, tol: float = 1e-6,
                 columns: Iterable[str] | None = None) -> WarmupReport:
    """Compara una ventana final común calculada desde distintos arranques.

    ``starts`` son índices de inicio sobre ``df`` (0 = todo el histórico). El
    arranque 0 es la referencia: se asume que es el que más historia tiene.
    """
    starts = sorted(set(int(s) for s in starts) | {0})
    if len(starts) < 2:
        raise ValueError("se necesita al menos un arranque distinto de 0")
    if len(df) - max(starts) < compare_bars:
        raise ValueError("compare_bars excede las barras disponibles tras el arranque más tardío")
    window = df.index[-compare_bars:]
    ref = fn(df).loc[window]
    cols = list(columns) if columns is not None else [
        c for c in ref.columns if pd.api.types.is_numeric_dtype(ref[c]) or ref[c].dtype == bool]
    runs = {s: fn(df.iloc[s:]).loc[window] for s in starts if s != 0}
    out: list[WarmupColumn] = []
    for c in cols:
        diffs: dict[str, float] = {}
        a = ref[c].astype(float).to_numpy()
        for s, r in runs.items():
            b = r[c].astype(float).to_numpy() if c in r else np.full_like(a, np.nan)
            both_nan = np.isnan(a) & np.isnan(b)
            one_nan = np.isnan(a) ^ np.isnan(b)
            scale = np.maximum(1.0, np.maximum(np.abs(np.nan_to_num(a)), np.abs(np.nan_to_num(b))))
            rel = np.where(both_nan, 0.0, np.where(one_nan, np.inf, np.abs(a - b) / scale))
            diffs[str(s)] = float(np.max(rel)) if rel.size else 0.0
        # Mayor arranque (menos historia) que, junto con todos los anteriores,
        # coincide con la referencia. Si ninguno coincide no hay prueba de
        # convergencia: queda None (inestable), nunca se asume estable.
        converged: int | None = None
        for s in starts[1:]:
            if diffs[str(s)] <= tol:
                converged = s
            else:
                break
        out.append(WarmupColumn(c, diffs, converged))
    return WarmupReport(starts, len(df) - compare_bars, compare_bars, tol, out)


def _plain(v: object) -> object:
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (np.floating, np.integer)):
        return v.item()
    return None if pd.isna(v) else v
