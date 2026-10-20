"""Estado de riesgo compartido entre el puente (vivo) y el optimizador.

Es el canal por el que el Paso D "degrada el riesgo a cero": el optimizador
escribe risk_multiplier=0 en state/risk_state.json y el puente deja de abrir
posiciones en la siguiente alerta. También implementa el Daily Loss Breaker y
un kill switch manual (basta con crear el fichero KILL).

Protecciones adicionales (ideas de las "protections" de Freqtrade, reimplementadas):
  * Racha de pérdidas: bloquea entradas tras N cierres perdedores seguidos en el día.
  * Enfriamiento: espera mínima tras cerrar una operación antes de abrir otra.
  * Bloqueo por grado/hora: aplica los veredictos `negative` de audit/tradelog
    (IC bootstrap entero < 0), nunca por pocas muestras.
Todas son opt-in en RiskGuard (por defecto desactivadas) y los cierres siempre pasan.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass
class RiskState:
    risk_multiplier: float = 1.0
    halted_reason: str | None = None
    day: str | None = None
    day_start_equity: float | None = None
    active_model_version: str | None = None
    consecutive_losses: int = 0
    last_close_at: str | None = None
    blocked_grades: list[str] = field(default_factory=list)
    blocked_hours: list[int] = field(default_factory=list)
    updated_at: str | None = None
    history: list[dict] = field(default_factory=list)


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


class RiskStateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> RiskState:
        if not self.path.exists():
            return RiskState()
        return RiskState(**json.loads(self.path.read_text()))

    def save(self, state: RiskState) -> None:
        state.updated_at = datetime.utcnow().isoformat() + "Z"
        state.history = state.history[-200:]
        _atomic_write_json(self.path, asdict(state))

    def set_multiplier(self, value: float, reason: str | None, source: str) -> RiskState:
        with self._lock:
            st = self.load()
            st.risk_multiplier = max(0.0, float(value))
            st.halted_reason = reason if value == 0 else None
            st.history.append({"at": datetime.utcnow().isoformat() + "Z", "source": source,
                               "risk_multiplier": st.risk_multiplier, "reason": reason})
            self.save(st)
            return st

    def set_blocked(self, grades: list[str], hours: list[int], source: str) -> RiskState:
        """Aplica los bloqueos de un AuditReport (blocked_grades / blocked_hours)."""
        with self._lock:
            st = self.load()
            st.blocked_grades = sorted({str(g) for g in grades})
            st.blocked_hours = sorted({int(h) for h in hours})
            st.history.append({"at": datetime.utcnow().isoformat() + "Z", "source": source,
                               "blocked_grades": st.blocked_grades, "blocked_hours": st.blocked_hours})
            self.save(st)
            return st

    def record_close(self, pnl: float, at: datetime, source: str) -> RiskState:
        """Registra el resultado de un cierre. pnl < 0 suma a la racha; > 0 la reinicia."""
        with self._lock:
            st = self.load()
            if pnl < 0:
                st.consecutive_losses += 1
            elif pnl > 0:
                st.consecutive_losses = 0
            st.last_close_at = at.isoformat()
            st.history.append({"at": datetime.utcnow().isoformat() + "Z", "source": source,
                               "close_pnl": float(pnl), "consecutive_losses": st.consecutive_losses})
            self.save(st)
            return st

    def set_active_version(self, version: str, source: str) -> RiskState:
        with self._lock:
            st = self.load()
            st.active_model_version = version
            st.history.append({"at": datetime.utcnow().isoformat() + "Z", "source": source,
                               "active_model_version": version})
            self.save(st)
            return st


class RiskGuard:
    """Decide si una nueva entrada está permitida. Los cierres siempre pasan."""

    def __init__(self, store: RiskStateStore, max_daily_loss_pct: float,
                 tz: str = "America/New_York", kill_switch_path: str | Path | None = None,
                 max_consecutive_losses: int | None = None, cooldown_minutes: float = 0.0):
        self.store = store
        self.max_daily_loss_pct = max_daily_loss_pct
        self.tz = ZoneInfo(tz)
        self.kill_switch_path = Path(kill_switch_path) if kill_switch_path else None
        if max_consecutive_losses is not None and max_consecutive_losses < 1:
            raise ValueError("max_consecutive_losses debe ser >= 1 o None")
        if cooldown_minutes < 0:
            raise ValueError("cooldown_minutes no puede ser negativo")
        self.max_consecutive_losses = max_consecutive_losses
        self.cooldown_minutes = float(cooldown_minutes)

    def record_close(self, pnl: float, now: datetime | None = None, source: str = "router") -> RiskState:
        now = (now or datetime.now(tz=self.tz)).astimezone(self.tz)
        return self.store.record_close(pnl, now, source)

    def check_entry(self, equity: float, now: datetime | None = None, *,
                    grade: str | None = None) -> tuple[bool, str, float]:
        """Devuelve (permitido, motivo, multiplicador_de_riesgo)."""
        if self.kill_switch_path and self.kill_switch_path.exists():
            return False, "kill_switch", 0.0
        now = (now or datetime.now(tz=self.tz)).astimezone(self.tz)
        today = now.date().isoformat()
        with self.store._lock:
            st = self.store.load()
            if st.day != today or not st.day_start_equity:
                # Día nuevo: la racha de pérdidas se reinicia con el día de sesión.
                st.day, st.day_start_equity, st.consecutive_losses = today, equity, 0
                self.store.save(st)
        if st.risk_multiplier <= 0:
            return False, f"risk_zero:{st.halted_reason}", 0.0
        loss_pct = (st.day_start_equity - equity) / st.day_start_equity * 100.0
        if loss_pct >= self.max_daily_loss_pct:
            return False, f"daily_loss_breaker:{loss_pct:.2f}%", 0.0
        if self.max_consecutive_losses and st.consecutive_losses >= self.max_consecutive_losses:
            return False, f"loss_streak:{st.consecutive_losses}", 0.0
        if self.cooldown_minutes and st.last_close_at:
            elapsed = (now - datetime.fromisoformat(st.last_close_at)).total_seconds() / 60.0
            if elapsed < self.cooldown_minutes:
                return False, f"cooldown:{self.cooldown_minutes - elapsed:.1f}min", 0.0
        if grade is not None and grade in st.blocked_grades:
            return False, f"blocked_grade:{grade}", 0.0
        if now.hour in st.blocked_hours:
            return False, f"blocked_hour:{now.hour}", 0.0
        return True, "ok", st.risk_multiplier
