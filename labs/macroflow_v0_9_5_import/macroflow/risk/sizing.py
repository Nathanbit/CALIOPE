"""Tamaño de posición por riesgo fijo sobre equity real."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolSpec:
    """Lo mínimo para dimensionar en cualquier broker.

    value_per_point_per_lot: dinero ganado/perdido por 1 lote cuando el precio
    se mueve 1.0 unidad. En MT5 = trade_tick_value / trade_tick_size.
    """
    symbol: str
    volume_min: float
    volume_max: float
    volume_step: float
    value_per_point_per_lot: float
    digits: int = 2


def round_down_to_step(x: float, step: float) -> float:
    if step <= 0:
        raise ValueError("volume_step debe ser > 0")
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(math.floor(x / step + 1e-9) * step, decimals)


def position_size(
    equity: float,
    risk_pct: float,
    entry: float,
    stop: float,
    spec: SymbolSpec,
    risk_multiplier: float = 1.0,
) -> float:
    """Lotes tales que tocar el stop cueste <= equity * risk_pct% * multiplicador.

    Redondea siempre HACIA ABAJO: si el mínimo del broker supera el riesgo
    permitido devuelve 0 (no opera) en vez de redondear al alza.
    """
    if equity <= 0 or risk_pct <= 0 or risk_multiplier <= 0:
        return 0.0
    dist = abs(entry - stop)
    if dist <= 0:
        raise ValueError("El stop no puede coincidir con la entrada")
    risk_cash = equity * risk_pct / 100.0 * risk_multiplier
    loss_per_lot = dist * spec.value_per_point_per_lot
    raw = risk_cash / loss_per_lot
    vol = round_down_to_step(min(raw, spec.volume_max), spec.volume_step)
    return vol if vol >= spec.volume_min else 0.0


def split_volume(total: float, weights: list[float], spec: SymbolSpec) -> list[float]:
    """Reparte el volumen en tramos (p. ej. 50/30/20 para salidas parciales).

    Los tramos que queden por debajo del mínimo se funden en el primero, así el
    riesgo total nunca supera `total`.
    """
    if total <= 0:
        return []
    s = sum(weights)
    legs = [round_down_to_step(total * w / s, spec.volume_step) for w in weights]
    out, carry = [], 0.0
    for v in legs:
        if v >= spec.volume_min:
            out.append(v)
        else:
            carry += v
    if not out:
        return [round_down_to_step(total, spec.volume_step)] if total >= spec.volume_min else []
    out[0] = round_down_to_step(out[0] + carry, spec.volume_step)
    return out
