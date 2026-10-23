"""Paso A: ingesta del Strategy Tester / journal y detección de esperanza negativa.

Las salidas parciales 50/30/20 aparecen en TradingView como trades separados
con la misma entrada. `rebuild_plans` los reagrupa en planes completos antes de
medir nada, igual que el medidor MFE/MAE del script Pine.

Un setup/franja solo se marca como negativo si (a) tiene suficientes trades y
(b) el IC bootstrap de su esperanza queda entero por debajo de 0. Sin eso, con
pocas muestras acabarías bloqueando horas por puro ruido.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from macroflow.risk.montecarlo import bootstrap_expectancy_ci

# Nombres de columna habituales en el export "List of trades" de TradingView.
# El formato ha cambiado entre versiones: ajusta este mapa si tu export difiere.
TV_COLUMNS = {
    "trade": ["Trade #", "Trade number"],
    "type": ["Type"],
    "signal": ["Signal"],
    "time": ["Date/Time", "Date and time", "Date"],
    "price": ["Price USD", "Price USDT", "Price"],
    "pnl": ["Profit USD", "Net P&L USD", "Profit USDT", "Net P&L USDT", "Profit"],
}
GRADE_RE = re.compile(r"(?:^|[\s_\-|:])([ABC])(?:$|[\s_\-|:])")


def _pick(df: pd.DataFrame, key: str) -> str:
    for c in TV_COLUMNS[key]:
        if c in df.columns:
            return c
    raise KeyError(f"No encuentro columna para '{key}'. Columnas: {list(df.columns)}")


def load_tradingview_export(path: str | Path) -> pd.DataFrame:
    """Lee CSV/XLSX de TradingView (filas Entry/Exit) y devuelve 1 fila por trade."""
    path = Path(path)
    raw = pd.read_excel(path) if path.suffix.lower() in (".xlsx", ".xls") else pd.read_csv(path)
    c_trade, c_type, c_sig, c_time, c_pnl = (_pick(raw, k) for k in ("trade", "type", "signal", "time", "pnl"))
    raw[c_time] = pd.to_datetime(raw[c_time])
    is_entry = raw[c_type].astype(str).str.contains("Entry", case=False)
    entries = raw[is_entry].set_index(c_trade)
    exits = raw[~is_entry].set_index(c_trade)
    out = pd.DataFrame({
        "entry_time": entries[c_time],
        "exit_time": exits[c_time],
        "side": np.where(entries[c_type].str.contains("long", case=False), "long", "short"),
        "signal": entries[c_sig].astype(str),
        "pnl": pd.to_numeric(exits[c_pnl], errors="coerce"),
    }).dropna(subset=["pnl"]).reset_index(names="trade_id")
    return out


def extract_grade(signal: str) -> str | None:
    m = GRADE_RE.search(f" {signal} ")
    return m.group(1) if m else None


def rebuild_plans(trades: pd.DataFrame) -> pd.DataFrame:
    """Funde las salidas parciales que comparten entrada en un único plan."""
    t = trades.copy()
    if "grade" not in t.columns:
        t["grade"] = t["signal"].map(extract_grade)
    g = t.groupby(["entry_time", "side"], as_index=False).agg(
        exit_time=("exit_time", "max"), signal=("signal", "first"), grade=("grade", "first"),
        pnl=("pnl", "sum"), n_legs=("pnl", "size"),
    )
    return g.sort_values("entry_time").reset_index(drop=True)


@dataclass
class AuditReport:
    by_grade: pd.DataFrame
    by_hour: pd.DataFrame
    blocked_grades: list[str] = field(default_factory=list)
    blocked_hours: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"by_grade": self.by_grade.to_dict("records"), "by_hour": self.by_hour.to_dict("records"),
                "blocked_grades": self.blocked_grades, "blocked_hours": self.blocked_hours}


def _expectancy_table(df: pd.DataFrame, key: str, min_trades: int, seed: int) -> pd.DataFrame:
    rows = []
    for k, grp in df.groupby(key, dropna=True):
        mean, lo, hi = bootstrap_expectancy_ci(grp["pnl"].to_numpy(), seed=seed)
        wins, losses = grp.pnl[grp.pnl > 0].sum(), -grp.pnl[grp.pnl < 0].sum()
        n = len(grp)
        verdict = "insufficient" if n < min_trades else ("negative" if hi < 0 else ("positive" if lo > 0 else "inconclusive"))
        rows.append({key: k, "n": n, "expectancy": mean, "ci_low": lo, "ci_high": hi,
                     "profit_factor": wins / losses if losses > 0 else np.inf, "verdict": verdict})
    return pd.DataFrame(rows)


def audit(plans: pd.DataFrame, tz: str = "America/New_York", min_trades: int = 30, seed: int = 7) -> AuditReport:
    p = plans.copy()
    et = pd.to_datetime(p["entry_time"])
    et = et.dt.tz_localize("UTC") if et.dt.tz is None else et
    p["hour"] = et.dt.tz_convert(ZoneInfo(tz)).dt.hour
    bg = _expectancy_table(p, "grade", min_trades, seed)
    bh = _expectancy_table(p, "hour", min_trades, seed)
    return AuditReport(
        by_grade=bg, by_hour=bh,
        blocked_grades=sorted(bg.loc[bg.verdict == "negative", "grade"].tolist()) if len(bg) else [],
        blocked_hours=sorted(int(h) for h in bh.loc[bh.verdict == "negative", "hour"]) if len(bh) else [],
    )
