"""Historial discrecional de SimpleFX como evidencia separada de MacroFlow.

Dueño único del formato "closed orders" que exporta SimpleFX (columnas en
español). Las operaciones manuales NO tienen snapshot del indicador, así que no
pueden entrar al ledger de `recursive_evidence.py` ni entrenar el modelo: este
módulo las conserva como `observation` (filas tal cual) y produce métricas
`derivation` reproducibles, siempre con `promotion_eligible = False`.

Sirve para medir la ejecución humana: dónde gana o pierde la persona por
símbolo, franja horaria y día, y si sus pérdidas medias superan a sus ganancias.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from macroflow.audit.tradelog import _expectancy_table

COLUMNS = {
    "symbol": ["Símbolo", "Simbolo", "Symbol"],
    "order_id": ["ID de la orden", "Order ID"],
    "side": ["Lado", "Side"],
    "size": ["Tamaño", "Tamano", "Size"],
    "open_time": ["Hora de Apertura", "Open Time"],
    "close_time": ["Hora de Cierre", "Close Time"],
    "open_price": ["Precio de Apertura", "Open Price"],
    "close_price": ["Precio de cierre", "Close Price"],
    "take_profit": ["Take Profit"],
    "stop_loss": ["Stop Loss"],
    "pnl": ["Ganancia (USDT)", "Ganancia", "Profit (USDT)", "Profit"],
}
TIME_FORMAT = "%d.%m.%Y %H:%M:%S"


def _pick(df: pd.DataFrame, key: str) -> str:
    for c in COLUMNS[key]:
        if c in df.columns:
            return c
    raise KeyError(f"No encuentro columna para '{key}'. Columnas: {list(df.columns)}")


def load_simplefx_closed_orders(path: str | Path, source_tz: str = "America/Guayaquil") -> pd.DataFrame:
    """Devuelve una fila por operación ejecutada (BUY/SELL), en UTC.

    Las órdenes pendientes canceladas (p. ej. "BUY LIMIT" sin hora de apertura)
    se descartan: no son operaciones. ``source_tz`` es la zona del export; el
    nombre del archivo de SimpleFX incluye el desfase (p. ej. -05_00).
    """
    raw = pd.read_csv(path, encoding="utf-8-sig")
    c = {k: _pick(raw, k) for k in ("symbol", "order_id", "side", "size", "open_time",
                                   "close_time", "open_price", "close_price", "pnl")}
    tp = next((x for x in COLUMNS["take_profit"] if x in raw.columns), None)
    sl = next((x for x in COLUMNS["stop_loss"] if x in raw.columns), None)
    side = raw[c["side"]].astype(str).str.strip().str.upper()
    executed = side.isin(["BUY", "SELL"])
    t = raw[executed]

    def _ts(col: str) -> pd.Series:
        return pd.to_datetime(t[col], format=TIME_FORMAT).dt.tz_localize(source_tz).dt.tz_convert("UTC")

    out = pd.DataFrame({
        "trade_id": t[c["order_id"]].astype(str),
        "symbol": t[c["symbol"]].astype(str),
        "side": np.where(side[executed] == "BUY", "long", "short"),
        "size": pd.to_numeric(t[c["size"]], errors="coerce"),
        "entry_time": _ts(c["open_time"]),
        "exit_time": _ts(c["close_time"]),
        "entry": pd.to_numeric(t[c["open_price"]], errors="coerce"),
        "exit": pd.to_numeric(t[c["close_price"]], errors="coerce"),
        "take_profit": pd.to_numeric(t[tp], errors="coerce") if tp else np.nan,
        "stop_loss": pd.to_numeric(t[sl], errors="coerce") if sl else np.nan,
        "pnl": pd.to_numeric(t[c["pnl"]], errors="coerce"),
    })
    if out["pnl"].isna().any():
        bad = out.loc[out["pnl"].isna(), "trade_id"].tolist()
        raise ValueError(f"P&L no numérico en operaciones {bad}")
    return out.sort_values("exit_time").reset_index(drop=True)


def summarize(trades: pd.DataFrame, *, tz: str = "America/Guayaquil", min_trades: int = 20,
              seed: int = 7) -> dict:
    """Métricas de ejecución humana. Los veredictos usan el mismo IC bootstrap que tradelog."""
    t = trades.copy()
    p = t["pnl"]
    local = t["entry_time"].dt.tz_convert(tz)
    t["hour"] = local.dt.hour
    t["weekday"] = local.dt.day_name()
    t["duration_min"] = (t["exit_time"] - t["entry_time"]).dt.total_seconds() / 60.0
    t["has_stop"] = t["stop_loss"].notna()
    t["day"] = local.dt.date.astype(str)

    wins, losses = p[p > 0], p[p < 0]
    equity = p.cumsum()
    by_day_count = t.groupby("day").size()
    day_pnl = t.groupby("day")["pnl"].sum()
    overtrading = pd.DataFrame({"n": by_day_count, "pnl": day_pnl})
    heavy = overtrading[overtrading["n"] >= overtrading["n"].quantile(0.75)]
    light = overtrading[overtrading["n"] < overtrading["n"].quantile(0.75)]

    def _table(key: str) -> list[dict]:
        tab = _expectancy_table(t, key, min_trades, seed)
        return tab.replace([np.inf], None).round(4).to_dict("records") if len(tab) else []

    # Racha máxima de pérdidas consecutivas
    streak = best = 0
    for v in p:
        streak = streak + 1 if v < 0 else 0
        best = max(best, streak)

    return {
        "evidence_kind": "derivation",
        "promotion_eligible": False,
        "scope": "discretionary_execution",
        "n_trades": int(len(t)),
        "period": [str(t["entry_time"].min()), str(t["exit_time"].max())],
        "net_pnl": round(float(p.sum()), 4),
        "win_rate": round(float((p > 0).mean()), 4),
        "avg_win": round(float(wins.mean()), 4) if len(wins) else None,
        "avg_loss": round(float(losses.mean()), 4) if len(losses) else None,
        "payoff_ratio": round(float(wins.mean() / -losses.mean()), 4) if len(wins) and len(losses) else None,
        "expectancy_per_trade": round(float(p.mean()), 4),
        "max_drawdown": round(float((equity.cummax().clip(lower=0) - equity).max()), 4),
        "max_consecutive_losses": int(best),
        "net_pnl_without_top3": round(float(p.sum() - p.nlargest(3).sum()), 4),
        "trades_without_stop": int((~t["has_stop"]).sum()),
        "overtrading": {
            "heavy_days_threshold": int(overtrading["n"].quantile(0.75)) if len(overtrading) else None,
            "heavy_days_pnl_per_trade": round(float(heavy["pnl"].sum() / max(1, heavy["n"].sum())), 4),
            "light_days_pnl_per_trade": round(float(light["pnl"].sum() / max(1, light["n"].sum())), 4),
        },
        "by_symbol": _table("symbol"),
        "by_hour_local": _table("hour"),
        "by_weekday": _table("weekday"),
        "min_trades_for_verdict": min_trades,
        "note": ("Veredictos 'negative'/'positive' solo con IC bootstrap entero de un lado de 0 y "
                 "n >= min_trades; con menos muestras el veredicto es 'insufficient'."),
    }


def build_report(path: str | Path, **kw) -> dict:
    path = Path(path)
    trades = load_simplefx_closed_orders(path, source_tz=kw.pop("source_tz", "America/Guayaquil"))
    rep = summarize(trades, **kw)
    rep["source"] = {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "evidence_kind": "observation"}
    return rep
