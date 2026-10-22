from datetime import datetime, timedelta, timezone

from macroflow.bridge.router import OrderRouter
from macroflow.bridge.schemas import TVAlert
from macroflow.execution.paper import PaperBroker
from macroflow.risk.guards import RiskGuard, RiskStateStore


def _alert(i, action, grade="A"):
    kw = dict(secret="x", alert_id=f"a{i}", model_version="t", ticker="BTCUSDT", action=action,
              grade=grade, price=60_000, bar_time=int(datetime.now(timezone.utc).timestamp() * 1000))
    if action == "BUY":
        kw.update(sl=59_000, tp=[61_000])
    return TVAlert(**kw)


def test_two_losing_closes_block_next_entry(tmp_path):
    broker = PaperBroker(starting_equity=10_000)
    broker.last_price["BTCUSD"] = 60_000
    store = RiskStateStore(tmp_path / "s.json")
    guard = RiskGuard(store, max_daily_loss_pct=50, max_consecutive_losses=2)
    r = OrderRouter(broker, guard, {"BTCUSDT": "BTCUSD"}, [1.0], 2.0, tmp_path / "j.jsonl")
    eq = 10_000.0
    for i in range(2):
        assert r.process(_alert(2 * i, "BUY"))["status"] == "filled"
        eq -= 50
        broker.set_equity(eq)
        rec = r.process(_alert(2 * i + 1, "CLOSE"))
        assert rec["approx_pnl"] == -50
    blocked = r.process(_alert(99, "BUY"))
    assert blocked["status"] == "blocked" and blocked["reason"] == "loss_streak:2"
    # Los cierres siempre pasan aunque el guard bloquee entradas
    assert r.process(_alert(100, "CLOSE"))["status"] == "closed"


def test_router_passes_grade_to_guard(tmp_path):
    broker = PaperBroker()
    broker.last_price["BTCUSD"] = 60_000
    store = RiskStateStore(tmp_path / "s.json")
    store.set_blocked(["C"], [], "audit")
    r = OrderRouter(broker, RiskGuard(store, 50), {"BTCUSDT": "BTCUSD"}, [1.0], 2.0, tmp_path / "j.jsonl")
    assert r.process(_alert(1, "BUY", grade="C"))["reason"] == "blocked_grade:C"
    assert r.process(_alert(2, "BUY", grade="B"))["status"] == "filled"
