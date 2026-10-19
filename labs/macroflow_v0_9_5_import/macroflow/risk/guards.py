"""Estado de riesgo compartido entre el puente (vivo) y el optimizador.

Es el canal por el que el Paso D "degrada el riesgo a cero": el optimizador
escribe risk_multiplier=0 en state/risk_state.json y el puente deja de abrir
posiciones en la siguiente alerta. También implementa el Daily Loss Breaker y
un kill switch manual (basta con crear el fichero KILL).
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
                 tz: str = "America/New_York", kill_switch_path: str | Path | None = None):
        self.store = store
        self.max_daily_loss_pct = max_daily_loss_pct
        self.tz = ZoneInfo(tz)
        self.kill_switch_path = Path(kill_switch_path) if kill_switch_path else None

    def check_entry(self, equity: float, now: datetime | None = None) -> tuple[bool, str, float]:
        """Devuelve (permitido, motivo, multiplicador_de_riesgo)."""
        if self.kill_switch_path and self.kill_switch_path.exists():
            return False, "kill_switch", 0.0
        now = (now or datetime.now(tz=self.tz)).astimezone(self.tz)
        today = now.date().isoformat()
        with self.store._lock:
            st = self.store.load()
            if st.day != today or not st.day_start_equity:
                st.day, st.day_start_equity = today, equity
                self.store.save(st)
        if st.risk_multiplier <= 0:
            return False, f"risk_zero:{st.halted_reason}", 0.0
        loss_pct = (st.day_start_equity - equity) / st.day_start_equity * 100.0
        if loss_pct >= self.max_daily_loss_pct:
            return False, f"daily_loss_breaker:{loss_pct:.2f}%", 0.0
        return True, "ok", st.risk_multiplier
