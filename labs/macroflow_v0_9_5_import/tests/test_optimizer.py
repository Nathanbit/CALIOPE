import numpy as np
import pandas as pd

from macroflow.audit.tradelog import audit, rebuild_plans
from macroflow.optimization.pine_injector import inject_params
from macroflow.optimization.recursive import OptimizerConfig, RecursiveOptimizer, bump_patch
from macroflow.optimization.space import ParamSpace, ParamSpec
from macroflow.optimization.walkforward import walkforward_folds
from macroflow.risk.guards import RiskStateStore
from macroflow.strategy.reference import backtest
from macroflow.strategy.synthetic import make_market

SPACE_JSON = "config/param_space.json"


def test_walkforward_no_overlap():
    folds = walkforward_folds(1000, 400, 100, embargo=10)
    for f in folds:
        assert f.train.stop + 10 == f.test.start


def test_space_narrowing_respects_hard_bounds():
    s = ParamSpec("x", "float", 1.0, hard_low=0.0, hard_high=10.0, step=0.1)
    n = s.narrowed(9.8, 0.5)
    assert n.low == 7.3 and n.high == 10.0
    i = ParamSpec("n", "int", 5, hard_low=5, hard_high=40).narrowed(5, 0.2)
    assert i.low == 5 and i.high > i.low


def test_reference_backtest_no_lookahead_and_produces_trades():
    df = make_market(3000)
    tr = backtest(df)
    assert len(tr) > 20
    assert (tr["exit_time"] >= tr["entry_time"]).all()


def test_pine_injection():
    src = 'const string MODEL_VERSION = "0.9.4"\natrMult  = input.float(2.0, "ATR")\nint corrLen = input.int(defval = 50, title="P")\n'
    out, ch = inject_params(src, {"atrMult": 2.7, "corrLen": 75}, "0.9.5")
    assert 'input.float(2.7, "ATR")' in out and "defval = 75" in out and '"0.9.5"' in out
    assert bump_patch("0.9.4") == "0.9.5"


def test_audit_flags_only_significant_negative_hours():
    rng = np.random.default_rng(0)
    t = pd.date_range("2025-01-01", periods=600, freq="h", tz="UTC")
    pnl = rng.normal(10, 30, 600)
    hours = t.tz_convert("America/New_York").hour
    pnl[hours == 3] = rng.normal(-40, 10, (hours == 3).sum())
    trades = pd.DataFrame({"entry_time": t, "exit_time": t, "side": "long", "signal": "Long A", "pnl": pnl})
    rep = audit(rebuild_plans(trades), min_trades=20)
    assert rep.blocked_hours == [3]


def _optimizer(tmp_path, data, **cfg):
    conf = OptimizerConfig(n_trials=8, train_bars=2500, test_bars=800, embargo_bars=10,
                           warmup_bars=300, min_trades_is=10, min_trades_oos=15, mc_sims=300, n_boot=300, **cfg)
    store = RiskStateStore(tmp_path / "risk.json")
    return RecursiveOptimizer(ParamSpace.from_json(SPACE_JSON), backtest, lambda: data, conf,
                              tmp_path / "reg", store, pine_path="pine/MacroFlow_v0.9.4.pine",
                              pine_out_dir=tmp_path / "pine"), store


def test_loop_promotes_then_skips_reused_data(tmp_path):
    data = make_market(6000, seed=3)
    opt, store = _optimizer(tmp_path, data, min_oos_pf=0.0, max_dd_var=10, max_p_ruin=1,
                            require_positive_ci=False, auto_approve=True)
    rep = opt.run(1)[0]
    assert rep.status == "promoted" and rep.version == "0.9.5"
    assert store.load().active_model_version == "0.9.5"
    assert (tmp_path / "pine" / "MacroFlow_v0.9.5.pine").exists()
    assert opt.run(1)[0].status == "no_new_data"


def test_brake_when_active_model_decays(tmp_path):
    data = make_market(6000, seed=3)
    opt, store = _optimizer(tmp_path, data, min_oos_pf=0.0, max_dd_var=10, max_p_ruin=1,
                            require_positive_ci=False, auto_approve=True)
    opt.run(1)
    # Llegan datos nuevos y endurecemos el gate: el modelo activo ya no lo cumple
    opt.data_provider = lambda: make_market(6100, seed=3)
    opt.cfg.min_oos_pf = 50.0
    reps = opt.run(3)
    assert len(reps) == 1 and reps[0].status == "BRAKE"
    assert store.load().risk_multiplier == 0.0 and opt.halted
