import json
import time

import pytest
from fastapi.testclient import TestClient

from macroflow.bridge.app import create_app
from macroflow.config import Settings
from macroflow.execution.paper import PaperBroker


@pytest.fixture
def env(tmp_path):
    s = Settings(webhook_secret="s3cret", state_dir=tmp_path, registry_dir=tmp_path / "reg")
    broker = PaperBroker()
    app = create_app(s, broker)
    with TestClient(app) as c:
        yield c, app, broker, s


def alert(**kw):
    a = {"secret": "s3cret", "alert_id": f"id-{time.time_ns()}", "model_version": "0.9.4",
         "ticker": "BINANCE:BTCUSDT", "action": "BUY", "grade": "A", "price": 60000, "sl": 59000,
         "tp": [61000, 62000, 63000], "risk_pct": 1.0, "bar_time": int(time.time() * 1000)}
    a.update(kw)
    return a


def wait_journal(s, n=1):
    for _ in range(100):
        if s.journal_path.exists() and len(s.journal_path.read_text().splitlines()) >= n:
            return [json.loads(x) for x in s.journal_path.read_text().splitlines()]
        time.sleep(0.02)
    raise AssertionError("journal vacío")


def test_buy_splits_into_legs(env):
    c, app, broker, s = env
    r = c.post("/webhook", json=alert())
    assert r.status_code == 202
    rec = wait_journal(s)[-1]
    assert rec["status"] == "filled" and rec["symbol"] == "BTCUSD"
    assert rec["legs"] == [0.05, 0.03, 0.02] and len(broker.positions) == 3


def test_bad_secret_and_bad_levels(env):
    c, *_ = env
    assert c.post("/webhook", json=alert(secret="nope")).status_code == 401
    assert c.post("/webhook", json=alert(sl=61000)).status_code == 422


def test_duplicate_and_stale(env):
    c, *_ = env
    a = alert(alert_id="dup")
    assert c.post("/webhook", json=a).status_code == 202
    assert c.post("/webhook", json=a).json()["status"] == "duplicate"
    assert c.post("/webhook", json=alert(bar_time=0)).json()["status"] == "stale"


def test_brake_blocks_entries_but_not_closes(env):
    c, app, broker, s = env
    app.state.store.set_multiplier(0, "oos decay", "pytest")
    c.post("/webhook", json=alert())
    assert wait_journal(s)[-1]["status"] == "blocked"
    c.post("/webhook", json=alert(action="CLOSE", sl=None, tp=[]))
    assert wait_journal(s, 2)[-1]["status"] == "closed"


def test_version_mismatch(env):
    c, app, *_ = env
    app.state.store.set_active_version("0.9.5", "pytest")
    assert c.post("/webhook", json=alert()).json()["status"] == "version_mismatch"
