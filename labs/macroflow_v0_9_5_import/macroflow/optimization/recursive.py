"""Bucle de automejora recursiva de MacroFlow (Pasos A-D).

Ciclo por iteración:
  A. Auditoría: si hay log de trades reales/Strategy Tester, detecta grados y
     franjas horarias con esperanza negativa *estadísticamente significativa* y
     los fija como filtros (no se optimizan, se excluyen).
  B. Walk-forward + Optuna: en cada fold optimiza SOLO sobre el tramo in-sample
     y mide el elegido en el out-of-sample siguiente. Los OOS concatenados son
     la única evidencia que cuenta.
  C. Si el candidato pasa los gates y mejora al modelo activo: nueva versión en
     el registro (+ .pine versionado). Queda como "candidate" hasta aprobación
     humana salvo auto_approve=True.
  D. Freno: si el modelo ACTIVO (el que opera en vivo) cae por debajo de los
     gates en datos nuevos, escribe risk_multiplier=0 en el estado compartido
     —el puente deja de abrir posiciones— y detiene el bucle.

Las mismas barras OOS no se reutilizan para decidir dos veces: si no han
llegado datos nuevos desde la última iteración, el paso se salta. Reoptimizar
una y otra vez sobre el mismo OOS lo convierte, en la práctica, en in-sample.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import optuna
import pandas as pd

from macroflow.optimization.metrics import TradeStats, robust_score, trade_stats
from macroflow.optimization.pine_injector import write_versioned_pine
from macroflow.optimization.space import ParamSpace
from macroflow.optimization.walkforward import walkforward_folds
from macroflow.risk.guards import RiskStateStore
from macroflow.risk.montecarlo import bootstrap_expectancy_ci, monte_carlo_drawdown

log = logging.getLogger("macroflow.optimizer")

BacktestFn = Callable[[pd.DataFrame, dict], pd.DataFrame]   # -> trades con columnas entry_time, r
DataProvider = Callable[[], pd.DataFrame]
PlansProvider = Callable[[], pd.DataFrame | None]            # -> planes con entry_time, grade, pnl


@dataclass
class OptimizerConfig:
    base_version: str = "0.9.4"
    n_trials: int = 60
    train_bars: int = 3000
    test_bars: int = 750
    step_bars: int | None = None
    embargo_bars: int = 24
    warmup_bars: int = 300
    min_trades_is: int = 25
    min_trades_oos: int = 40
    # --- Gates (Paso D) ---
    min_oos_pf: float = 1.10
    require_positive_ci: bool = True     # IC bootstrap (1000) de la esperanza OOS > 0
    n_boot: int = 1000
    max_dd_var: float = 0.25             # drawdown máx. al 95 % (Monte Carlo)
    max_p_ruin: float = 0.01
    ruin_level: float = 0.30
    mc_sims: int = 3000
    mc_block: int = 5
    risk_per_trade_pct: float = 0.5      # para convertir R -> dinero en el Monte Carlo
    initial_capital: float = 10_000.0
    # --- Promoción ---
    improvement_margin_r: float = 0.02   # el candidato debe mejorar la esperanza OOS en >= 0.02 R
    narrow_frac: float = 0.5             # ancho de búsqueda de la siguiente iteración
    auto_approve: bool = False
    audit_min_trades: int = 30
    session_tz: str = "America/New_York"
    seed: int = 42


@dataclass
class Evaluation:
    params: dict
    stats: TradeStats
    ci: tuple[float, float, float]
    mc: dict
    gates: dict[str, bool]

    @property
    def passed(self) -> bool:
        return all(self.gates.values())

    def to_dict(self) -> dict:
        return {"params": self.params, "stats": self.stats.to_dict(),
                "expectancy_ci90": list(self.ci), "monte_carlo": self.mc, "gates": self.gates,
                "passed": self.passed}


@dataclass
class IterationReport:
    iteration: int
    status: str            # promoted | candidate_pending | kept_incumbent | rejected | BRAKE | no_new_data
    message: str
    version: str | None = None
    candidate: dict | None = None
    incumbent: dict | None = None
    audit: dict | None = None
    folds: list[dict] = field(default_factory=list)
    pine_changes: list[str] = field(default_factory=list)


def bump_patch(v: str) -> str:
    major, minor, patch = (list(map(int, v.split("."))) + [0, 0, 0])[:3]
    return f"{major}.{minor}.{patch + 1}"


class ModelRegistry:
    """registry/index.json + registry/v<ver>.json con parámetros y métricas."""

    def __init__(self, root: str | Path, base_version: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self.base_version = base_version

    def index(self) -> dict:
        if self.index_path.exists():
            return json.loads(self.index_path.read_text())
        return {"active": None, "latest": self.base_version, "versions": [], "last_data_end": None}

    def _save_index(self, idx: dict) -> None:
        self.index_path.write_text(json.dumps(idx, indent=2, default=str))

    def get(self, version: str) -> dict:
        return json.loads((self.root / f"v{version}.json").read_text())

    def active_params(self) -> dict | None:
        a = self.index()["active"]
        return self.get(a)["params"] if a else None

    def register(self, params: dict, metrics: dict, status: str) -> str:
        idx = self.index()
        version = bump_patch(idx["latest"])
        rec = {"version": version, "status": status, "created_at": datetime.utcnow().isoformat() + "Z",
               "parent": idx["active"], "params": params, "metrics": metrics}
        (self.root / f"v{version}.json").write_text(json.dumps(rec, indent=2, default=str))
        idx["latest"] = version
        idx["versions"].append(version)
        self._save_index(idx)
        return version

    def activate(self, version: str) -> None:
        rec = self.get(version)
        rec["status"], rec["activated_at"] = "active", datetime.utcnow().isoformat() + "Z"
        (self.root / f"v{version}.json").write_text(json.dumps(rec, indent=2, default=str))
        idx = self.index()
        if idx["active"] and idx["active"] != version:
            old = self.get(idx["active"])
            old["status"] = "retired"
            (self.root / f"v{idx['active']}.json").write_text(json.dumps(old, indent=2, default=str))
        idx["active"] = version
        self._save_index(idx)

    def set_last_data_end(self, ts) -> None:
        idx = self.index()
        idx["last_data_end"] = str(ts)
        self._save_index(idx)


class RecursiveOptimizer:
    def __init__(self, space: ParamSpace, backtest_fn: BacktestFn, data_provider: DataProvider,
                 config: OptimizerConfig, registry_dir: str | Path, risk_store: RiskStateStore,
                 pine_path: str | Path | None = None, pine_out_dir: str | Path | None = None,
                 plans_provider: PlansProvider | None = None):
        self.space = space
        self.backtest = backtest_fn
        self.data_provider = data_provider
        self.cfg = config
        self.registry = ModelRegistry(registry_dir, config.base_version)
        self.store = risk_store
        self.pine_path = Path(pine_path) if pine_path else None
        self.pine_out_dir = Path(pine_out_dir) if pine_out_dir else (self.pine_path.parent if self.pine_path else None)
        self.plans_provider = plans_provider
        self.iteration = 0
        self.halted = False
        optuna.logging.set_verbosity(optuna.logging.WARNING)

    # ------------------------------------------------------------------ helpers
    def _oos_trades(self, df: pd.DataFrame, fold, params: dict) -> np.ndarray:
        seg = df.iloc[fold.test]
        real_start = df.index[fold.test.stop - self.cfg.test_bars]
        tr = self.backtest(seg, params)
        if tr.empty:
            return np.empty(0)
        return tr.loc[tr["entry_time"] >= real_start, "r"].to_numpy(float)

    def _optimize_fold(self, df: pd.DataFrame, fold, space: ParamSpace) -> tuple[dict, float]:
        train = df.iloc[fold.train]
        min_tr = self.cfg.min_trades_is

        def objective(trial: optuna.Trial) -> float:
            params = space.suggest(trial)
            score = robust_score(self.backtest(train, params)["r"].to_numpy(float), min_tr)
            if not np.isfinite(score):
                raise optuna.TrialPruned()
            return score

        study = optuna.create_study(direction="maximize",
                                    sampler=optuna.samplers.TPESampler(seed=self.cfg.seed + fold.k))
        seed_point = space.in_range(space.defaults())
        if len(seed_point) == len(space.specs):   # el default solo sirve de semilla si cabe en el rango
            study.enqueue_trial(seed_point)
        study.optimize(objective, n_trials=self.cfg.n_trials, catch=(ValueError,))
        done = [t for t in study.trials if t.state == optuna.trial.TrialState.COMPLETE]
        if not done:
            return space.defaults(), float("-inf")
        return {**study.best_params, **space.fixed}, study.best_value

    def evaluate(self, r: np.ndarray, params: dict) -> Evaluation:
        c = self.cfg
        stats = trade_stats(r)
        ci = bootstrap_expectancy_ci(r, n_boot=c.n_boot, seed=c.seed) if r.size else (np.nan,) * 3
        mc: dict = {}
        if r.size >= 2:
            pnl = r * c.risk_per_trade_pct / 100.0 * c.initial_capital
            mc = monte_carlo_drawdown(pnl, c.initial_capital, c.mc_sims, 0.95, c.ruin_level,
                                      c.mc_block, c.seed).to_dict()
        gates = {
            "min_trades": stats.n >= c.min_trades_oos,
            "profit_factor": stats.profit_factor >= c.min_oos_pf,
            "dd_var95": bool(mc) and mc["dd_var"] <= c.max_dd_var,
            "p_ruin": bool(mc) and mc["p_ruin"] <= c.max_p_ruin,
        }
        if c.require_positive_ci:
            gates["expectancy_ci_positive"] = bool(np.isfinite(ci[1]) and ci[1] > 0)
        return Evaluation(params, stats, tuple(float(x) for x in ci), mc, gates)

    def _brake(self, reason: str) -> None:
        self.store.set_multiplier(0.0, reason, source="optimizer")
        self.halted = True
        log.error("FRENO DE SEGURIDAD: %s", reason)

    # ------------------------------------------------------------------ pasos
    def step(self) -> IterationReport:
        self.iteration += 1
        it, c = self.iteration, self.cfg

        # --- Paso A: auditoría -------------------------------------------------
        audit_dict = None
        if self.plans_provider:
            plans = self.plans_provider()
            if plans is not None and len(plans):
                from macroflow.audit.tradelog import audit
                rep = audit(plans, c.session_tz, c.audit_min_trades, c.seed)
                audit_dict = rep.to_dict()
                self.space.fixed["blocked_hours"] = sorted(set(self.space.fixed.get("blocked_hours", [])) | set(rep.blocked_hours))
                self.space.fixed["blocked_grades"] = sorted(set(self.space.fixed.get("blocked_grades", [])) | set(rep.blocked_grades))

        # --- Datos y control de reutilización de OOS ---------------------------
        df = self.data_provider()
        data_end = df.index[-1]
        last = self.registry.index()["last_data_end"]
        if last is not None and str(data_end) <= last:
            return IterationReport(it, "no_new_data", f"Sin barras nuevas desde {last}", audit=audit_dict)

        folds = walkforward_folds(len(df), c.train_bars, c.test_bars, c.step_bars, c.embargo_bars,
                                  warmup=c.warmup_bars)

        # --- Paso B: walk-forward + Optuna --------------------------------------
        incumbent_params = self.registry.active_params()
        cand_r, inc_r, fold_logs, last_best = [], [], [], None
        for f in folds:
            best, is_score = self._optimize_fold(df, f, self.space)
            oos = self._oos_trades(df, f, best)
            cand_r.append(oos)
            if incumbent_params:
                inc_r.append(self._oos_trades(df, f, {**incumbent_params, **self.space.fixed}))
            fold_logs.append({"fold": f.k, "is_score": is_score, "oos_n": int(oos.size),
                              "oos_exp_r": float(oos.mean()) if oos.size else None, "params": best})
            last_best = best
        cand = self.evaluate(np.concatenate(cand_r), last_best)
        inc = self.evaluate(np.concatenate(inc_r), incumbent_params) if incumbent_params else None
        stability = self._param_stability([fl["params"] for fl in fold_logs])
        report = IterationReport(it, "", "", candidate=cand.to_dict() | {"param_stability_cv": stability},
                                 incumbent=inc.to_dict() if inc else None, audit=audit_dict, folds=fold_logs)
        self.registry.set_last_data_end(data_end)

        # --- Paso D: freno sobre el modelo que está en vivo ---------------------
        if inc is not None and not inc.passed:
            failed = [k for k, v in inc.gates.items() if not v]
            self._brake(f"modelo activo v{self.registry.index()['active']} falla OOS: {failed}")
            report.status, report.message = "BRAKE", f"Riesgo a 0. Gates fallidos: {failed}"
            return report

        if not cand.passed:
            failed = [k for k, v in cand.gates.items() if not v]
            report.status, report.message = "rejected", f"Candidato no pasa gates: {failed}"
            return report

        if inc is not None and cand.stats.expectancy_r < inc.stats.expectancy_r + c.improvement_margin_r:
            report.status = "kept_incumbent"
            report.message = (f"Candidato {cand.stats.expectancy_r:.3f}R no mejora al activo "
                              f"{inc.stats.expectancy_r:.3f}R por >= {c.improvement_margin_r}R")
            return report

        # --- Paso C: registro + .pine versionado --------------------------------
        version = self.registry.register(last_best, report.candidate, status="candidate")
        report.version = version
        if self.pine_path:
            out, changes = write_versioned_pine(self.pine_path, self.pine_out_dir, last_best,
                                                self.space.pine_mapping(), version)
            report.pine_changes = changes + [f"escrito: {out.name}"]
        if c.auto_approve:
            self.approve(version)
            report.status, report.message = "promoted", f"v{version} activada automáticamente"
        else:
            report.status = "candidate_pending"
            report.message = f"v{version} pasa todos los gates; pendiente de aprobación (approve('{version}'))"
        # Búsqueda adaptativa: la siguiente iteración explora alrededor del nuevo óptimo
        self.space = self.space.narrowed_around(last_best, c.narrow_frac)
        return report

    def approve(self, version: str) -> None:
        """Activa una versión: el puente solo aceptará alertas con este model_version."""
        self.registry.activate(version)
        self.store.set_active_version(version, source="approve")

    def rearm(self, multiplier: float = 1.0) -> None:
        """Rearme manual tras un freno. Deliberadamente no automático."""
        self.store.set_multiplier(multiplier, None, source="manual_rearm")
        self.halted = False

    def run(self, max_iterations: int = 1, sleep_s: float = 0.0,
            on_report: Callable[[IterationReport], None] | None = None) -> list[IterationReport]:
        reports = []
        for _ in range(max_iterations):
            if self.halted:
                break
            rep = self.step()
            reports.append(rep)
            log.info("iter %d: %s — %s", rep.iteration, rep.status, rep.message)
            if on_report:
                on_report(rep)
            if rep.status == "BRAKE":
                break
            if sleep_s:
                time.sleep(sleep_s)
        return reports

    @staticmethod
    def _param_stability(param_sets: list[dict]) -> dict[str, float]:
        """Coef. de variación de cada parámetro entre folds. Alto = óptimo inestable (sospecha de ruido)."""
        out = {}
        for k in param_sets[0] if param_sets else []:
            vals = [p[k] for p in param_sets if isinstance(p.get(k), (int, float)) and not isinstance(p.get(k), bool)]
            if len(vals) > 1 and np.mean(vals) != 0:
                out[k] = float(np.std(vals) / abs(np.mean(vals)))
        return out


def report_to_json(rep: IterationReport) -> str:
    return json.dumps(asdict(rep), indent=2, default=str)
