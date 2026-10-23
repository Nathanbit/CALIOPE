"""CLI del bucle de automejora.

  python scripts/run_optimizer.py --data data/btc_1h.csv --iterations 1
  python scripts/run_optimizer.py --demo                       # datos sintéticos
  python scripts/run_optimizer.py --approve 0.9.5              # activar versión
  python scripts/run_optimizer.py --rearm                      # rearmar tras freno

El CSV debe tener columnas time, open, high, low, close, ref_close (SPY alineado).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from macroflow.config import get_settings  # noqa: E402
from macroflow.optimization.recursive import OptimizerConfig, RecursiveOptimizer, report_to_json  # noqa: E402
from macroflow.optimization.space import ParamSpace  # noqa: E402
from macroflow.risk.guards import RiskStateStore  # noqa: E402
from macroflow.strategy.reference import backtest  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--trades", help="Export de TradingView (List of trades) para el Paso A")
    ap.add_argument("--space", default=str(ROOT / "config" / "param_space.json"))
    ap.add_argument("--pine", default=None,
                    help="Pine canónico; deshabilitado por defecto hasta superar paridad barra-a-barra")
    ap.add_argument("--iterations", type=int, default=2)
    ap.add_argument("--sleep", type=float, default=0.0, help="segundos entre iteraciones")
    ap.add_argument("--trials", type=int, default=60)
    ap.add_argument("--auto-approve", action="store_true")
    ap.add_argument("--forever", action="store_true",
                    help="bucle autónomo recursivo (mismo motor que el panel); Ctrl+C para parar")
    ap.add_argument("--interval", type=float, default=3600.0, help="segundos entre iteraciones con --forever")
    ap.add_argument("--approve")
    ap.add_argument("--rearm", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    s = get_settings()
    if not s.enable_experimental_optimizer and not (a.approve or a.rearm):
        ap.error("Optimizador bloqueado: requiere MF_ENABLE_EXPERIMENTAL_OPTIMIZER=1 tras validar paridad")
    if a.auto_approve and not s.allow_auto_approve:
        ap.error("--auto-approve requiere MF_ALLOW_AUTO_APPROVE=1")
    store = RiskStateStore(s.risk_state_path)

    if a.forever:
        from macroflow.optimization.autoloop import AutoLoop, LoopConfig
        if a.data:
            s.state_dir.mkdir(parents=True, exist_ok=True)
            (s.state_dir / "uploaded_data.csv").write_bytes(Path(a.data).read_bytes())
        loop = AutoLoop(s, store)
        loop.start(LoopConfig(source="csv" if a.data else "demo", trials=a.trials, auto_approve=a.auto_approve,
                              interval_s=a.interval, max_iterations=max(1, a.iterations)))
        try:
            while loop._thread.is_alive():
                loop._thread.join(1.0)
        except KeyboardInterrupt:
            loop.stop("Ctrl+C")
            loop._thread.join()
        print(f"Bucle terminado: {loop.state.stop_reason} · {loop.state.iterations} iteraciones")
        return
    cfg = OptimizerConfig(n_trials=a.trials, auto_approve=a.auto_approve)

    if a.demo:
        from macroflow.strategy.synthetic import make_market
        data = make_market()
        provider = lambda: data  # noqa: E731
    elif a.data:
        provider = lambda: pd.read_csv(a.data, parse_dates=["time"], index_col="time")  # noqa: E731
    else:
        provider = None

    plans_provider = None
    if a.trades:
        from macroflow.audit.tradelog import load_tradingview_export, rebuild_plans
        plans_provider = lambda: rebuild_plans(load_tradingview_export(a.trades))  # noqa: E731

    opt = RecursiveOptimizer(ParamSpace.from_json(a.space), backtest, provider, cfg, s.registry_dir,
                             store, pine_path=a.pine, plans_provider=plans_provider)
    if a.approve:
        opt.approve(a.approve)
        print(f"v{a.approve} activada")
        return
    if a.rearm:
        opt.rearm()
        print("Riesgo rearmado a 1.0")
        return
    if provider is None:
        ap.error("--data o --demo")
    for rep in opt.run(a.iterations, a.sleep):
        print(report_to_json(rep))


if __name__ == "__main__":
    main()
