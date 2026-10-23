"""Contrato JSON entre las alertas de Pine v6 y el puente."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class TVAlert(BaseModel):
    # "ignore": Pine puede añadir campos analíticos (z-scores, t-stat...) sin romper el puente
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    secret: SecretStr                      # TradingView no permite cabeceras propias: va en el cuerpo
    alert_id: str = Field(min_length=1, max_length=128)
    model_version: str
    ticker: str
    action: Literal["BUY", "SELL", "CLOSE"]
    grade: Literal["A", "B", "C"] | None = None
    price: float = Field(gt=0)
    sl: float | None = Field(default=None, gt=0)
    tp: list[float] = Field(default_factory=list, max_length=5)
    risk_pct: float = Field(default=0.5, gt=0, le=10)
    bar_time: int                          # ms epoch (time de Pine)
    context: dict = Field(default_factory=dict)   # métricas macro opcionales para el journal

    @model_validator(mode="after")
    def _check_levels(self) -> "TVAlert":
        if self.action in ("BUY", "SELL"):
            if self.sl is None:
                raise ValueError("BUY/SELL requieren sl")
            if self.action == "BUY" and not self.sl < self.price:
                raise ValueError("En BUY el sl debe estar por debajo del precio")
            if self.action == "SELL" and not self.sl > self.price:
                raise ValueError("En SELL el sl debe estar por encima del precio")
            for t in self.tp:
                if (self.action == "BUY" and t <= self.price) or (self.action == "SELL" and t >= self.price):
                    raise ValueError(f"tp {t} en el lado equivocado del precio")
        return self
