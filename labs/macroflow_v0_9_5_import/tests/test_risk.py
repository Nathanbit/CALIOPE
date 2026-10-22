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


from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def test_guard_loss_streak_blocks_and_resets(tmp_path):
    store = RiskStateStore(tmp_path / "s.json")
    g = RiskGuard(store, max_daily_loss_pct=50, max_consecutive_losses=2)
    t0 = datetime(2026, 10, 20, 10, 0, tzinfo=NY)
    assert g.check_entry(1000, t0)[0]
    g.record_close(-5, t0)
    assert g.check_entry(1000, t0)[0]
    g.record_close(-5, t0)
    ok, reason, mult = g.check_entry(1000, t0)
    assert not ok and reason == "loss_streak:2" and mult == 0.0
    # día nuevo: la racha vuelve a cero
    assert g.check_entry(990, t0 + timedelta(days=1))[0]


def test_guard_win_resets_streak(tmp_path):
    store = RiskStateStore(tmp_path / "s.json")
    g = RiskGuard(store, max_daily_loss_pct=50, max_consecutive_losses=2)
    t0 = datetime(2026, 10, 20, 10, 0, tzinfo=NY)
    g.check_entry(1000, t0)
    g.record_close(-1, t0)
    g.record_close(+2, t0)
    g.record_close(-1, t0)
    assert g.check_entry(1000, t0)[0]


def test_guard_cooldown(tmp_path):
    store = RiskStateStore(tmp_path / "s.json")
    g = RiskGuard(store, max_daily_loss_pct=50, cooldown_minutes=10)
    t0 = datetime(2026, 10, 20, 10, 0, tzinfo=NY)
    g.check_entry(1000, t0)
    g.record_close(+1, t0)
    ok, reason, _ = g.check_entry(1000, t0 + timedelta(minutes=4))
    assert not ok and reason.startswith("cooldown:")
    assert g.check_entry(1000, t0 + timedelta(minutes=10))[0]


def test_guard_blocked_grade_and_hour(tmp_path):
    store = RiskStateStore(tmp_path / "s.json")
    g = RiskGuard(store, max_daily_loss_pct=50)
    store.set_blocked(["C"], [3], "audit")
    t0 = datetime(2026, 10, 20, 10, 0, tzinfo=NY)
    assert g.check_entry(1000, t0, grade="A")[0]
    assert g.check_entry(1000, t0, grade="C")[1] == "blocked_grade:C"
    assert g.check_entry(1000, t0.replace(hour=3), grade="A")[1] == "blocked_hour:3"


def test_guard_new_protections_off_by_default(tmp_path):
    store = RiskStateStore(tmp_path / "s.json")
    g = RiskGuard(store, max_daily_loss_pct=50)
    t0 = datetime(2026, 10, 20, 10, 0, tzinfo=NY)
    g.check_entry(1000, t0)
    for _ in range(5):
        g.record_close(-1, t0)
    assert g.check_entry(1000, t0)[0]


def test_guard_rejects_invalid_config(tmp_path):
    store = RiskStateStore(tmp_path / "s.json")
    with pytest.raises(ValueError):
        RiskGuard(store, 3.0, max_consecutive_losses=0)
    with pytest.raises(ValueError):
        RiskGuard(store, 3.0, cooldown_minutes=-1)


def test_old_state_file_still_loads(tmp_path):
    # Un risk_state.json previo a esta versión no tiene los campos nuevos.
    import json
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"risk_multiplier": 1.0, "halted_reason": None, "day": None,
                             "day_start_equity": None, "active_model_version": None,
                             "updated_at": None, "history": []}))
    st = RiskStateStore(p).load()
    assert st.consecutive_losses == 0 and st.blocked_grades == []


# --- Entradas no válidas: las compuertas fallan cerradas -----------------------

def test_nan_pnl_counts_as_loss_for_streak(tmp_path):
    store = RiskStateStore(tmp_path / "s.json")
    g = RiskGuard(store, max_daily_loss_pct=50, max_consecutive_losses=2)
    t0 = datetime(2026, 10, 20, 10, 0, tzinfo=NY)
    assert g.check_entry(1000, t0)[0]
    g.record_close(float("nan"), t0)
    g.record_close(float("inf"), t0)
    ok, reason, _ = g.check_entry(1000, t0)
    assert not ok and reason == "loss_streak:2"
    last = store.load().history[-1]
    assert last["close_pnl"] is None and last["invalid_pnl"] == "inf"


def test_nan_equity_blocks_and_never_becomes_day_start(tmp_path):
    store = RiskStateStore(tmp_path / "s.json")
    g = RiskGuard(store, max_daily_loss_pct=2)
    t0 = datetime(2026, 10, 20, 10, 0, tzinfo=NY)
    for bad in (float("nan"), float("inf"), 0.0, -5.0):
        assert g.check_entry(bad, t0) == (False, "invalid_equity", 0.0)
    assert store.load().day_start_equity is None
    assert g.check_entry(1000, t0)[0]
    ok, reason, _ = g.check_entry(900, t0)
    assert not ok and reason.startswith("daily_loss_breaker")


def test_corrupt_persisted_day_start_blocks_without_resetting_streak(tmp_path):
    store = RiskStateStore(tmp_path / "s.json")
    g = RiskGuard(store, max_daily_loss_pct=2, max_consecutive_losses=5)
    t0 = datetime(2026, 10, 20, 10, 0, tzinfo=NY)
    assert g.check_entry(1000, t0)[0]
    g.record_close(-1, t0)
    st = store.load()
    st.day_start_equity = float("nan")
    store.save(st)
    assert g.check_entry(1000, t0) == (False, "invalid_day_start_equity", 0.0)
    assert store.load().consecutive_losses == 1
    # Día nuevo: la base se reconstruye con un equity válido.
    assert g.check_entry(1000, t0 + timedelta(days=1))[0]


def test_naive_datetimes_are_rejected(tmp_path):
    g = RiskGuard(RiskStateStore(tmp_path / "s.json"), max_daily_loss_pct=50)
    with pytest.raises(ValueError, match="naive"):
        g.check_entry(1000, datetime(2026, 10, 20, 10, 0))
    with pytest.raises(ValueError, match="naive"):
        g.record_close(-1, datetime(2026, 10, 20, 10, 0))
