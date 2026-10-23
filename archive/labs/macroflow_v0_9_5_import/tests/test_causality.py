import json

import pandas as pd

from macroflow.audit.causality import lookahead_check, warmup_check
from macroflow.strategy.reference import DEFAULT_PARAMS, compute_signals
from macroflow.strategy.synthetic import make_market


def _ref(df):
    return compute_signals(df, dict(DEFAULT_PARAMS))


def test_reference_has_no_lookahead():
    df = make_market(n=1500, seed=3)
    rep = lookahead_check(df, _ref, min_index=300, max_checks=20)
    assert rep.checked_bars == 20
    assert rep.passed, rep.leaking_columns
    json.dumps(rep.to_dict())  # serializable para el ledger


def test_detects_future_leak():
    df = make_market(n=800, seed=4)

    def leaky(d):
        out = _ref(d)
        out["long_setup"] = d["close"].shift(-1) > d["close"]  # mira la barra siguiente
        return out

    rep = lookahead_check(df, leaky, min_index=300, max_checks=15)
    assert not rep.passed
    assert "long_setup" in rep.leaking_columns


def test_missing_column_fails_closed():
    df = make_market(n=600, seed=5)
    rep = lookahead_check(df, _ref, columns=["no_existe"], min_index=300, max_checks=3)
    assert rep.unknown_columns == ["no_existe"]
    assert not rep.passed


def test_warmup_detects_ema_dependence_and_convergence():
    df = make_market(n=3000, seed=6)
    rep = warmup_check(df, _ref, starts=[100, 1000, 2750], compare_bars=200,
                       columns=["structural"], tol=1e-6)
    col = rep.columns[0]
    # Con 50 barras de historia la EMA aún no converge; con 1800 sí.
    assert col.max_abs_diff_by_start["2750"] > 1e-6
    assert col.max_abs_diff_by_start["100"] <= 1e-6
    assert col.converged_from_start in (100, 1000)
    assert rep.recommended_startup_bars is not None and rep.recommended_startup_bars >= 1800
    json.dumps(rep.to_dict())


def test_warmup_flags_unstable_column():
    idx = pd.date_range("2024-01-01", periods=500, freq="1h", tz="UTC")
    df = pd.DataFrame({"close": range(500)}, index=idx, dtype=float)

    def cumulative(d):  # depende de TODO el histórico: nunca converge
        return pd.DataFrame({"cum": d["close"].cumsum()}, index=d.index)

    rep = warmup_check(df, cumulative, starts=[50, 100], compare_bars=100)
    assert rep.unstable_columns == ["cum"]
    assert rep.recommended_startup_bars is None
    assert not rep.passed
