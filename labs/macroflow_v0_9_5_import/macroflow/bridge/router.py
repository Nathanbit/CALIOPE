"""Convierte alertas validadas en órdenes, en un único hilo de ejecución.

El webhook responde en milisegundos (TradingView corta la petición a los pocos
segundos) y deja el trabajo lento —MT5, cálculo de lotes— a este worker.
"""
from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from macroflow.bridge.schemas import TVAlert
from macroflow.execution.base import Broker, OrderResult
from macroflow.risk.guards import RiskGuard
from macroflow.risk.sizing import position_size, split_volume

log = logging.getLogger("macroflow.router")


class Deduplicator:
    """TradingView puede reenviar la misma alerta; solo se ejecuta una vez."""

    def __init__(self, maxlen: int = 5000):
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()
        self.maxlen = maxlen

    def first_time(self, key: str) -> bool:
        with self._lock:
            if key in self._seen:
                return False
            self._seen[key] = None
            if len(self._seen) > self.maxlen:
                self._seen.popitem(last=False)
            return True


class OrderRouter:
    def __init__(self, broker: Broker, guard: RiskGuard, symbol_map: dict[str, str],
                 partial_weights: list[float], max_risk_pct: float, journal_path: Path):
        self.broker = broker
        self.guard = guard
        self.symbol_map = symbol_map
        self.partial_weights = partial_weights
        self.max_risk_pct = max_risk_pct
        self.journal_path = journal_path
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mf-exec")

    def resolve_symbol(self, ticker: str) -> str:
        return self.symbol_map.get(ticker, self.symbol_map.get(ticker.split(":")[-1], ticker.split(":")[-1]))

    def submit(self, alert: TVAlert) -> Future:
        return self.pool.submit(self._safe_process, alert)

    def _safe_process(self, alert: TVAlert) -> dict:
        try:
            rec = self.process(alert)
        except Exception as e:  # nunca matar el worker
            log.exception("Fallo procesando %s", alert.alert_id)
            rec = {"alert_id": alert.alert_id, "status": "error", "error": repr(e)}
        self._journal(rec)
        return rec

    def process(self, alert: TVAlert) -> dict:
        symbol = self.resolve_symbol(alert.ticker)
        base = {"alert_id": alert.alert_id, "symbol": symbol, "action": alert.action,
                "grade": alert.grade, "model_version": alert.model_version, "context": alert.context}

        if alert.action == "CLOSE":
            res = self.broker.close_all(symbol)
            return base | {"status": "closed", "orders": [r.__dict__ for r in res]}

        equity = self.broker.equity()
        ok, reason, mult = self.guard.check_entry(equity)
        if not ok:
            return base | {"status": "blocked", "reason": reason, "equity": equity}

        spec = self.broker.symbol_spec(symbol)
        risk_pct = min(alert.risk_pct, self.max_risk_pct)
        total = position_size(equity, risk_pct, alert.price, alert.sl, spec, mult)
        if total <= 0:
            return base | {"status": "skipped", "reason": "volumen < mínimo del broker", "equity": equity}

        tps = alert.tp or [None]
        weights = self.partial_weights[: len(tps)] if alert.tp else [1.0]
        legs = split_volume(total, weights, spec)
        results: list[OrderResult] = []
        for i, vol in enumerate(legs):
            tp = tps[i] if i < len(tps) else tps[-1]
            comment = f"MF{alert.model_version}|{alert.grade or '-'}|L{i + 1}"
            results.append(self.broker.market_order(symbol, alert.action, vol, alert.sl, tp, comment))
        status = "filled" if all(r.ok for r in results) else ("partial" if any(r.ok for r in results) else "rejected")
        return base | {"status": status, "equity": equity, "risk_pct": risk_pct, "risk_multiplier": mult,
                       "volume": total, "legs": legs, "orders": [r.__dict__ for r in results]}

    def _journal(self, rec: dict) -> None:
        rec = {"ts": datetime.utcnow().isoformat() + "Z", **rec}
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        log.info("journal %s", rec.get("status"))
