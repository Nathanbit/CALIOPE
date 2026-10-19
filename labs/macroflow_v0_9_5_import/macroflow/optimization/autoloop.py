"""Motor autónomo del bucle de automejora recursiva.

Qué lo hace recursivo (y no una sucesión de ejecuciones sueltas):
  * UN optimizador persistente: cada iteración parte del espacio de búsqueda que dejó
    la anterior (recentrado alrededor del último óptimo aprobado) y del modelo activo
    como rival a batir.
  * El espacio vigente se guarda en state/param_space_current.json y el estado del bucle
    en state/autoloop.json: si reinicias el servidor, el bucle continúa donde iba.
  * Si el estrechamiento lleva a un callejón (N rechazos seguidos), el espacio vuelve a
    los límites duros para volver a explorar.
  * Se detiene solo con el freno de seguridad (modelo activo degradado -> riesgo 0),
    al llegar a max_iterations o cuando lo paras tú.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from macroflow.config import ROOT, Settings
from macroflow.optimization.recursive import OptimizerConfig, RecursiveOptimizer
from macroflow.optimization.space import ParamSpace
from macroflow.risk.guards import RiskStateStore

log = logging.getLogger("macroflow.autoloop")


@dataclass
class LoopConfig:
    source: str = "demo"                 # demo | csv
    trials: int = 30
    auto_approve: bool = False           # promoción siempre pendiente de revisión humana
    min_oos_pf: float = 1.10
    require_positive_ci: bool = True
    interval_s: float = 3600.0           # pausa entre iteraciones
    max_iterations: int = 2              # ciclo acotado; no existe infinito por defecto
    reset_after_rejections: int = 3      # vuelve a explorar todo el espacio tras N rechazos seguidos


@dataclass
class LoopState:
    running: bool = False                # hay un hilo trabajando
    loop_active: bool = False            # modo autónomo encendido
    busy: bool = False                   # iteración en curso
    iterations: int = 0
    consecutive_rejections: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    next_run_at: float | None = None
    stop_reason: str | None = None
    config: dict = field(default_factory=dict)
    report: dict | None = None
    error: str | None = None
    history: list[dict] = field(default_factory=list)
    log: list[str] = field(default_factory=list)


class AutoLoop:
    def __init__(self, settings: Settings, store: RiskStateStore):
        self.s = settings
        self.store = store
        self.lock = threading.Lock()
        self.state = LoopState()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.opt: RecursiveOptimizer | None = None
        self.cfg = LoopConfig()
        self._load()

    # ------------------------------------------------------------ persistencia
    @property
    def _state_path(self) -> Path:
        return self.s.state_dir / "autoloop.json"

    @property
    def space_path(self) -> Path:
        return self.s.state_dir / "param_space_current.json"

    def _load(self) -> None:
        if self._state_path.exists():
            try:
                raw = json.loads(self._state_path.read_text())
                self.cfg = LoopConfig(**raw.get("config", {}))
                for k in ("iterations", "consecutive_rejections", "history", "report", "stop_reason", "finished_at"):
                    if k in raw:
                        setattr(self.state, k, raw[k])
                self.state.config = asdict(self.cfg)
                self.state.loop_active = bool(raw.get("loop_active"))
            except Exception:  # estado corrupto: se empieza de cero
                log.exception("autoloop.json ilegible; se ignora")

    def _save(self) -> None:
        st = self.state
        data = {"config": asdict(self.cfg), "loop_active": st.loop_active, "iterations": st.iterations,
                "consecutive_rejections": st.consecutive_rejections, "history": st.history[-200:],
                "report": st.report, "stop_reason": st.stop_reason, "finished_at": st.finished_at}
        self.s.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str))
        tmp.replace(self._state_path)

    def _log(self, msg: str) -> None:
        with self.lock:
            self.state.log.append(f"{datetime.now():%H:%M:%S} {msg}")
            self.state.log = self.state.log[-200:]
        log.info(msg)

    # ------------------------------------------------------------ optimizador persistente
    def _data_provider(self):
        import pandas as pd

        if self.cfg.source == "csv":
            path = self.s.state_dir / "uploaded_data.csv"
            if not path.exists():
                raise FileNotFoundError("Fuente CSV elegida pero no hay CSV subido")
            return lambda: pd.read_csv(path, parse_dates=["time"], index_col="time")
        from macroflow.strategy.synthetic import make_market
        # Demo: en cada iteración "llegan" 250 barras nuevas, como en vivo
        return lambda: make_market(9000 + 250 * self.state.iterations, seed=3)

    def _ensure_optimizer(self) -> RecursiveOptimizer:
        from macroflow.strategy.reference import backtest

        oc = OptimizerConfig(n_trials=self.cfg.trials, auto_approve=self.cfg.auto_approve,
                             min_oos_pf=self.cfg.min_oos_pf, require_positive_ci=self.cfg.require_positive_ci)
        if self.opt is None:
            space_file = self.space_path if self.space_path.exists() else ROOT / "config" / "param_space.json"
            self.opt = RecursiveOptimizer(ParamSpace.from_json(space_file), backtest, self._data_provider(), oc,
                                          self.s.registry_dir, self.store,
                                          # La réplica Python aún no tiene paridad con el Pine canónico.
                                          # No se genera ningún Pine hasta superar el gate barra-a-barra.
                                          pine_path=None, pine_out_dir=None)
            self.opt.iteration = self.state.iterations
        else:  # la config puede cambiar entre iteraciones sin perder el espacio aprendido
            self.opt.cfg = oc
            self.opt.data_provider = self._data_provider()
        return self.opt

    def reset_space(self) -> None:
        """Olvida el espacio estrechado y vuelve a los límites del config."""
        self.space_path.unlink(missing_ok=True)
        if self.opt is not None:
            self.opt.space = ParamSpace.from_json(ROOT / "config" / "param_space.json")
        self._log("espacio de búsqueda reiniciado a los límites duros")

    # ------------------------------------------------------------ una iteración
    def run_once(self) -> dict | None:
        with self.lock:
            if self.state.busy:
                raise RuntimeError("Ya hay una iteración en curso")
            self.state.busy = True
            self.state.started_at, self.state.finished_at = datetime.now().isoformat(timespec="seconds"), None
        t0 = time.time()
        rep_dict = None
        try:
            opt = self._ensure_optimizer()
            rep = opt.step()
            rep_dict = asdict(rep)
            opt.space.to_json(self.space_path)          # memoria recursiva entre iteraciones y reinicios
            cand = (rep.candidate or {}).get("stats", {})
            with self.lock:
                st = self.state
                st.iterations += 1
                st.report, st.error = rep_dict, None
                if rep.status in ("rejected", "kept_incumbent"):
                    st.consecutive_rejections += 1
                elif rep.status in ("promoted", "candidate_pending"):
                    st.consecutive_rejections = 0
                st.history.append({"iteration": st.iterations, "at": datetime.now().isoformat(timespec="seconds"),
                                   "status": rep.status, "version": rep.version,
                                   "expectancy_r": cand.get("expectancy_r"), "profit_factor": cand.get("profit_factor"),
                                   "n": cand.get("n"), "active": self.store.load().active_model_version,
                                   "seconds": round(time.time() - t0, 1)})
            self._log(f"iteración {self.state.iterations}: {rep.status} — {rep.message} ({time.time() - t0:.1f}s)")
            if self.state.consecutive_rejections >= self.cfg.reset_after_rejections:
                self.reset_space()
                with self.lock:
                    self.state.consecutive_rejections = 0
        except Exception as e:
            with self.lock:
                self.state.error = f"{e!r}\n{traceback.format_exc(limit=4)}"
            self._log(f"error: {e!r}")
        finally:
            with self.lock:
                self.state.busy = False
                self.state.finished_at = datetime.now().isoformat(timespec="seconds")
            self._save()
        return rep_dict

    # ------------------------------------------------------------ modo autónomo
    def start(self, cfg: LoopConfig | None = None) -> None:
        with self.lock:
            if self._thread and self._thread.is_alive():
                raise RuntimeError("El bucle ya está en marcha")
            if cfg:
                self.cfg = cfg
            self.state.config = asdict(self.cfg)
            self.state.loop_active, self.state.running, self.state.stop_reason = True, True, None
        if self.store.load().risk_multiplier <= 0:
            self._log("aviso: el riesgo está a 0 por un freno anterior; el bucle sigue evaluando, rearma cuando lo revises")
        self._stop.clear()
        self._save()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="mf-autoloop")
        self._thread.start()
        self._log(f"bucle autónomo iniciado (cada {self.cfg.interval_s:.0f}s, "
                  f"{'sin límite' if not self.cfg.max_iterations else f'máx. {self.cfg.max_iterations}'})")

    def stop(self, reason: str = "detenido manualmente") -> None:
        self._stop.set()
        with self.lock:
            self.state.loop_active, self.state.stop_reason, self.state.next_run_at = False, reason, None
        self._save()
        self._log(f"bucle detenido: {reason}")

    def _loop(self) -> None:
        done = 0
        try:
            while not self._stop.is_set():
                rep = self.run_once()
                done += 1
                status = (rep or {}).get("status")
                if status == "BRAKE":
                    self.stop("FRENO DE SEGURIDAD: el modelo activo se degradó; riesgo a 0")
                    break
                if self.cfg.max_iterations and done >= self.cfg.max_iterations:
                    self.stop(f"alcanzado el máximo de {self.cfg.max_iterations} iteraciones")
                    break
                with self.lock:
                    self.state.next_run_at = time.time() + self.cfg.interval_s
                if self._stop.wait(self.cfg.interval_s):
                    break
        finally:
            with self.lock:
                self.state.running, self.state.next_run_at = False, None

    def snapshot(self) -> dict:
        with self.lock:
            d = asdict(self.state)
        d["log"] = d["log"][-40:]
        d["history"] = d["history"][-60:]
        d["seconds_to_next"] = max(0, round(d["next_run_at"] - time.time())) if d["next_run_at"] else None
        return d
