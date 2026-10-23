import time

import pytest
from fastapi.testclient import TestClient

from macroflow.bridge.app import create_app
from macroflow.config import Settings
from macroflow.execution.paper import PaperBroker


@pytest.fixture
def client(tmp_path):
    s = Settings(webhook_secret="s3cret", state_dir=tmp_path, registry_dir=tmp_path / "reg")
    with TestClient(create_app(s, PaperBroker())) as c:
        yield c


def test_page_and_state(client):
    assert "MacroFlow Control" in client.get("/").text
    st = client.get("/api/state").json()
    assert st["broker"] == "paper" and st["risk_multiplier"] == 1.0


def test_test_alert_and_journal(client):
    r = client.post("/api/test-alert", json={"action": "BUY"}).json()
    assert r["status"] == "filled" and len(r["legs"]) == 3
    assert client.get("/api/journal").json()[0]["status"] == "filled"
    assert client.post("/api/test-alert", json={"action": "CLOSE"}).json()["status"] == "closed"


def test_kill_switch_blocks(client):
    client.post("/api/kill", json={"on": True})
    assert client.post("/api/test-alert", json={"action": "SELL"}).json()["reason"] == "kill_switch"
    client.post("/api/kill", json={"on": False})


def test_panel_rejects_forwarded_requests(client):
    assert client.get("/api/state", headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 403


RUN_BODY = {"trials": 5, "min_oos_pf": 0, "require_positive_ci": False, "auto_approve": False, "interval_s": 5}


@pytest.fixture
def optimizer_client(tmp_path):
    s = Settings(webhook_secret="s3cret", state_dir=tmp_path, registry_dir=tmp_path / "reg",
                 enable_experimental_optimizer=True)
    with TestClient(create_app(s, PaperBroker())) as c:
        yield c


def test_optimizer_blocked_by_default(client):
    """La compuerta de paridad Pine-Python es el contrato por defecto (fail-closed)."""
    assert client.post("/api/optimizer/run", json=RUN_BODY).status_code == 403
    assert client.post("/api/loop/start", json=RUN_BODY).status_code == 403


def test_optimizer_run_via_api(optimizer_client):
    client = optimizer_client
    assert client.post("/api/optimizer/run", json=RUN_BODY).json()["started"]
    for _ in range(600):
        j = client.get("/api/optimizer/status").json()
        if not j["busy"] and j["iterations"] >= 1:
            break
        time.sleep(0.1)
    assert j["report"] and j["report"]["status"] in {"candidate_pending", "rejected"}
    if j["report"]["status"] == "candidate_pending":
        v = j["report"]["version"]
        # La generación de .pine sigue desactivada hasta superar la paridad Pine-Python.
        assert client.get(f"/api/pine/{v}").status_code == 404
        assert client.post("/api/approve", json={"version": v}).json()["active"] == v
