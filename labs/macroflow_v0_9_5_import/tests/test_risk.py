import numpy as np
import pytest

from macroflow.risk.guards import RiskGuard, RiskStateStore
from macroflow.risk.montecarlo import monte_carlo_drawdown
from macroflow.risk.sizing import SymbolSpec, position_size, split_volume

SPEC = SymbolSpec("BTCUSD", 0.01, 100, 0.01, 1.0, 2)


def test_mc_no_explosion_when_pnl_starts_flat():
    # El borrador original dividía por un pico ~0: aquí el DD debe ser pequeño y finito
    pnl = np.array([0.0, 1.0, -1.0, 2.0, -0.5] * 40)
    res = monte_carlo_drawdown(pnl, 10_000, n_sims=500, seed=0)
    assert 0 <= res.dd_var < 0.01


def test_mc_losing_system_ruins():
    pnl = np.full(200, -100.0)
    res = monte_carlo_drawdown(pnl, 10_000, n_sims=200, ruin_level=0.3, seed=0)
    assert res.p_ruin == 1.0 and res.dd_var > 1.0


def test_mc_block_bootstrap_runs():
    rng = np.random.default_rng(0)
    res = monte_carlo_drawdown(rng.normal(5, 50, 300), 10_000, n_sims=300, block_size=10, seed=1)
    assert res.dd_median <= res.dd_var <= res.dd_cvar


def test_position_size_rounds_down_and_respects_risk():
    vol = position_size(10_000, 1.0, 60_000, 59_000, SPEC)  # riesgo 100$ / 1000 pts = 0.1
    assert vol == pytest.approx(0.10)
    assert vol * 1000 <= 100 + 1e-9


def test_position_size_zero_when_below_min():
    assert position_size(100, 0.5, 60_000, 50_000, SPEC) == 0.0


def test_position_size_risk_multiplier_zero():
    assert position_size(10_000, 1.0, 60_000, 59_000, SPEC, risk_multiplier=0) == 0.0


def test_split_volume_never_exceeds_total():
    legs = split_volume(0.07, [0.5, 0.3, 0.2], SPEC)
    assert sum(legs) <= 0.07 + 1e-9 and all(v >= SPEC.volume_min for v in legs)


def test_guard_daily_loss_and_zero_multiplier(tmp_path):
    store = RiskStateStore(tmp_path / "s.json")
    g = RiskGuard(store, max_daily_loss_pct=3.0, kill_switch_path=tmp_path / "KILL")
    assert g.check_entry(10_000)[0]
    ok, reason, _ = g.check_entry(9_650)
    assert not ok and reason.startswith("daily_loss_breaker")
    store.set_multiplier(0, "test", "pytest")
    assert g.check_entry(10_000)[1].startswith("risk_zero")
    store.set_multiplier(1, None, "pytest")
    (tmp_path / "KILL").touch()
    assert g.check_entry(10_000)[1] == "kill_switch"
