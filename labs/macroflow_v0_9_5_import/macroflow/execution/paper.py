"""Broker simulado: para desarrollo, tests y para correr el puente en Linux/macOS."""
from __future__ import annotations

import itertools
import threading

from macroflow.execution.base import Broker, OrderResult, Side
from macroflow.risk.sizing import SymbolSpec

DEFAULT_SPECS = {
    "BTCUSD": SymbolSpec("BTCUSD", 0.01, 100.0, 0.01, 1.0, 2),
    "ETHUSD": SymbolSpec("ETHUSD", 0.01, 1000.0, 0.01, 1.0, 2),
    "SOLUSD": SymbolSpec("SOLUSD", 0.1, 10000.0, 0.1, 1.0, 3),
}


class PaperBroker(Broker):
    def __init__(self, starting_equity: float = 10_000.0, specs: dict[str, SymbolSpec] | None = None):
        self._equity = starting_equity
        self.specs = dict(DEFAULT_SPECS, **(specs or {}))
        self.positions: dict[int, dict] = {}
        self._tickets = itertools.count(1)
        self._lock = threading.Lock()
        self.last_price: dict[str, float] = {}

    def connect(self) -> None:
        pass

    def equity(self) -> float:
        return self._equity

    def set_equity(self, value: float) -> None:  # útil en tests del breaker diario
        self._equity = value

    def symbol_spec(self, symbol: str) -> SymbolSpec:
        if symbol not in self.specs:
            raise KeyError(f"Símbolo desconocido en PaperBroker: {symbol}")
        return self.specs[symbol]

    def market_order(self, symbol: str, side: Side, volume: float, sl: float | None,
                     tp: float | None, comment: str) -> OrderResult:
        with self._lock:
            price = self.last_price.get(symbol)
            t = next(self._tickets)
            self.positions[t] = dict(symbol=symbol, side=side, volume=volume, price=price,
                                     sl=sl, tp=tp, comment=comment)
            return OrderResult(True, symbol, side, volume, price, t, 0, "paper fill")

    def close_all(self, symbol: str) -> list[OrderResult]:
        with self._lock:
            out = []
            for t in [k for k, p in self.positions.items() if p["symbol"] == symbol]:
                p = self.positions.pop(t)
                opp = "SELL" if p["side"] == "BUY" else "BUY"
                out.append(OrderResult(True, symbol, opp, p["volume"], self.last_price.get(symbol), t, 0, "paper close"))
            return out
