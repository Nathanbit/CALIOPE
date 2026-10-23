from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from macroflow.risk.sizing import SymbolSpec

Side = Literal["BUY", "SELL"]


@dataclass
class OrderResult:
    ok: bool
    symbol: str
    side: str
    volume: float
    price: float | None = None
    ticket: int | None = None
    retcode: int | None = None
    message: str = ""
    extra: dict = field(default_factory=dict)


class Broker(ABC):
    """Interfaz mínima que necesita el router. Implementaciones: Paper, MT5."""

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def equity(self) -> float: ...

    @abstractmethod
    def symbol_spec(self, symbol: str) -> SymbolSpec: ...

    @abstractmethod
    def market_order(self, symbol: str, side: Side, volume: float, sl: float | None,
                     tp: float | None, comment: str) -> OrderResult: ...

    @abstractmethod
    def close_all(self, symbol: str) -> list[OrderResult]: ...

    def shutdown(self) -> None:  # opcional
        pass
