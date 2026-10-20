import json

import pytest

from macroflow.audit.discretionary import build_report, load_simplefx_closed_orders

HEADER = "﻿Símbolo,ID de la orden,Lado,Tamaño,Hora de Apertura,Hora de Cierre,Precio de Apertura,Precio de cierre,Take Profit,Stop Loss,Ganancia (USDT)\n"
ROWS = [
    "US100,1,SELL,0.01,28.09.2026 09:40:43,28.09.2026 10:07:18,30508.8,30465.6,30258.8,30465.6,0.43",
    "BTCUSD,2,SELL,0.01,28.09.2026 11:49:33,28.09.2026 12:11:30,83511.05,83901.79,83220,83900,-3.91",
    "US500,3,BUY LIMIT,0.03,-,08.10.2026 09:19:46,,,,7810,0",
    "SPY.US,4,BUY,1,28.09.2026 11:34:29,28.09.2026 11:42:59,768.2,766.52,,,-1.68",
    "US100,5,BUY,0.01,29.09.2026 09:06:36,29.09.2026 14:54:40,30887.6,30743.4,,30745.2,-1.44",
]


@pytest.fixture
def csv(tmp_path):
    p = tmp_path / "sfx.csv"
    p.write_text(HEADER + "\n".join(ROWS) + "\n", encoding="utf-8")
    return p


def test_loader_drops_pending_orders_and_converts_to_utc(csv):
    t = load_simplefx_closed_orders(csv)
    assert len(t) == 4 and "3" not in set(t.trade_id)
    first = t[t.trade_id == "1"].iloc[0]
    assert str(first.entry_time) == "2026-09-28 14:40:43+00:00"  # 09:40 Guayaquil = 14:40 UTC
    assert first.side == "short"


def test_report_is_non_promotable_derivation(csv):
    rep = build_report(csv, min_trades=2)
    json.dumps(rep, default=str)
    assert rep["promotion_eligible"] is False
    assert rep["evidence_kind"] == "derivation"
    assert rep["source"]["evidence_kind"] == "observation" and len(rep["source"]["sha256"]) == 64
    assert rep["n_trades"] == 4
    assert rep["net_pnl"] == pytest.approx(0.43 - 3.91 - 1.68 - 1.44)
    assert rep["max_consecutive_losses"] == 3
    assert rep["trades_without_stop"] == 1


def test_non_numeric_pnl_fails_closed(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text(HEADER + ROWS[0].rsplit(",", 1)[0] + ",n/a\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_simplefx_closed_orders(p)
