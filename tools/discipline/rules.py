"""Reglas de disciplina de sesión y lectura del historial de SimpleFX.

Ideas adaptadas (sin copiar código) de `pre-trade-discipline-gate`,
`drawdown-circuit-breaker` y `trade-performance-coach` de
tradermonty/claude-trading-skills (MIT). Todo es local: sin red, sin brokers,
sin órdenes. La decisión final siempre es de la persona.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / "labs" / "macroflow_v0_9_5_import"

# Valor de 1 punto por 1 lote en USD, medido en el historial real de SimpleFX
# (0.01 lote de US100 = 0.01 USD por punto). Cualquier otro símbolo exige el dato.
DEFAULT_POINT_VALUE = {"US100": 1.0, "US500": 1.0, "US30": 1.0}


@dataclass
class Rules:
    tz: str = "America/Guayaquil"
    max_risk_pct: float = 5.0
    min_reward_risk: float = 2.0
    max_trades_per_day: int = 3
    max_consecutive_losses: int = 2
    max_daily_loss_pct: float = 10.0
    max_weekly_loss_pct: float = 20.0
    max_monthly_loss_pct: float = 30.0
    streak_cooldown_hours: float = 12.0
    revenge_cooldown_min: float = 10.0
    session_windows: list[list[str]] = field(default_factory=lambda: [
        ["19:00", "21:00"],   # Tokio
        ["02:00", "03:30"],   # Londres
        ["08:30", "10:30"],   # Nueva York
    ])

    @classmethod
    def load(cls, path: str | Path | None) -> "Rules":
        if not path:
            return cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"reglas desconocidas: {sorted(unknown)}")
        return cls(**data)

    def to_dict(self) -> dict:
        return asdict(self)


def in_windows(moment: datetime, windows: list[list[str]]) -> bool:
    t = moment.timetz().replace(tzinfo=None)
    for start, end in windows:
        a, b = time.fromisoformat(start), time.fromisoformat(end)
        if (a <= t <= b) if a <= b else (t >= a or t <= b):
            return True
    return False


def finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        f = float(value)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def load_trades(csv_path: str | Path, tz: str):
    """Usa el dueño único del formato SimpleFX (labs/.../audit/discretionary.py)."""
    if str(LAB) not in sys.path:
        sys.path.insert(0, str(LAB))
    from macroflow.audit.discretionary import load_simplefx_closed_orders
    return load_simplefx_closed_orders(csv_path, source_tz=tz)


def day_slice(trades, day: str, tz: str):
    local = trades["exit_time"].dt.tz_convert(ZoneInfo(tz))
    return trades[local.dt.date.astype(str) == day].sort_values("exit_time")


def losing_streak(pnls) -> int:
    streak = 0
    for v in reversed(list(pnls)):
        if v < 0:
            streak += 1
        elif v > 0:
            break
    return streak


def _start_of(kind: str, now: datetime) -> datetime:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "day":
        return day
    if kind == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def _release_of(kind: str, now: datetime) -> datetime:
    start = _start_of(kind, now)
    if kind == "day":
        return start + timedelta(days=1)
    if kind == "week":
        return start + timedelta(days=7)
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def circuit_breaker(trades, equity: float | None, rules: Rules, now: datetime) -> dict:
    """Estado del cortacircuitos de la cuenta (ingeniería inversa de drawdown-circuit-breaker).

    Pérdida diaria/semanal/mensual como % del capital estimado al inicio del periodo
    (equity actual − P&L del periodo) y racha de pérdidas con hora de liberación.
    Sin historial o sin equity no se afirma nada: estado UNKNOWN (fail-closed).
    """
    if trades is None or equity is None or equity <= 0:
        return {"state": "UNKNOWN", "reasons": ["sin historial o sin equity: no se puede evaluar"],
                "release_at": None, "periods": {}}
    tz = ZoneInfo(rules.tz)
    now = now.astimezone(tz)
    closed = trades[trades["exit_time"] <= now].copy()
    closed["local_exit"] = closed["exit_time"].dt.tz_convert(tz)
    reasons, releases, periods = [], [], {}
    halted = False
    limits = {"day": rules.max_daily_loss_pct, "week": rules.max_weekly_loss_pct,
              "month": rules.max_monthly_loss_pct}
    names = {"day": "diaria", "week": "semanal", "month": "mensual"}
    for kind, limit in limits.items():
        pnl = float(closed.loc[closed["local_exit"] >= _start_of(kind, now), "pnl"].sum())
        start_equity = equity - pnl
        loss_pct = (-pnl / start_equity * 100) if pnl < 0 and start_equity > 0 else 0.0
        periods[kind] = {"pnl": round(pnl, 4), "loss_pct": round(loss_pct, 2), "limit_pct": limit}
        if loss_pct >= limit:
            release = _release_of(kind, now)
            reasons.append(f"pérdida {names[kind]} {loss_pct:.1f}% ≥ {limit}% (se libera {release:%Y-%m-%d %H:%M})")
            releases.append(release)
            halted = True
    ordered = closed.sort_values("exit_time")
    streak = losing_streak(ordered["pnl"])
    if streak >= rules.max_consecutive_losses:
        last_loss = ordered.loc[ordered["pnl"] < 0, "local_exit"].iloc[-1]
        release = last_loss + timedelta(hours=rules.streak_cooldown_hours)
        if release > now:
            reasons.append(f"{streak} pérdidas seguidas: enfriamiento hasta {release:%Y-%m-%d %H:%M}")
            releases.append(release)
    state = "HALTED" if halted else ("COOLDOWN" if reasons else "TRADING_ALLOWED")
    return {"state": state, "reasons": reasons, "losing_streak": streak, "periods": periods,
            "release_at": max(releases).isoformat() if releases else None}
