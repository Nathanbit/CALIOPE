"""Panel web local: control del puente y del optimizador desde el navegador.

Todas las rutas /api/* y / solo aceptan peticiones desde la propia máquina
(127.0.0.1 / ::1, sin cabecera X-Forwarded-For). Así, si publicas /webhook a
internet detrás de un proxy, el panel sigue siendo inaccesible desde fuera.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from dataclasses import asdict
from macroflow.timeutil import utc_now_iso
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from macroflow.bridge.schemas import TVAlert

STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def local_only(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in LOCAL_HOSTS or request.headers.get("x-forwarded-for"):
        raise HTTPException(403, "El panel solo es accesible desde esta máquina")


router = APIRouter(dependencies=[Depends(local_only)])


# --------------------------------------------------------------------- páginas
@router.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


# --------------------------------------------------------------------- estado
@router.get("/api/state")
def state(request: Request):
    s = request.app.state.settings
    st = request.app.state.store.load()
    broker = request.app.state.router.broker
    try:
        equity = broker.equity()
    except Exception as e:  # MT5 desconectado, etc.
        equity = None
        request.app.state.last_error = repr(e)
    return {
        "broker": s.broker, "equity": equity,
        "risk_multiplier": st.risk_multiplier, "halted_reason": st.halted_reason,
        "active_model_version": st.active_model_version,
        "day_start_equity": st.day_start_equity, "max_daily_loss_pct": s.max_daily_loss_pct,
        "kill_switch": s.kill_switch_path.exists(),
        "open_positions": len(getattr(broker, "positions", {}) or {}),
        "autoloop": request.app.state.autoloop.state.loop_active and request.app.state.autoloop.state.running,
        "history": st.history[-15:][::-1],
    }


@router.get("/api/journal")
def journal(request: Request, limit: int = 50):
    p = request.app.state.settings.journal_path
    if not p.exists():
        return []
    lines = p.read_text().splitlines()[-limit:]
    return [json.loads(x) for x in reversed(lines)]


class KillBody(BaseModel):
    on: bool


@router.post("/api/kill")
def kill(body: KillBody, request: Request):
    p = request.app.state.settings.kill_switch_path
    p.parent.mkdir(parents=True, exist_ok=True)
    if body.on:
        p.write_text(utc_now_iso())
    elif p.exists():
        p.unlink()
    return {"kill_switch": p.exists()}


class RiskBody(BaseModel):
    multiplier: float = Field(ge=0, le=2)


@router.post("/api/risk")
def set_risk(body: RiskBody, request: Request):
    st = request.app.state.store.set_multiplier(body.multiplier, "manual (panel)" if body.multiplier == 0 else None,
                                                source="panel")
    return {"risk_multiplier": st.risk_multiplier}


# --------------------------------------------------------------------- alertas de prueba
class TestAlertBody(BaseModel):
    ticker: str = "BINANCE:BTCUSDT"
    action: str = "BUY"
    grade: str | None = "A"
    price: float = 60000
    sl_pct: float = Field(1.5, gt=0, le=20)
    risk_pct: float = Field(0.5, gt=0, le=5)
    with_tps: bool = True


@router.post("/api/test-alert")
def test_alert(body: TestAlertBody, request: Request):
    """Construye una alerta como la de Pine (con el secret del servidor) y la mete por el mismo camino."""
    s = request.app.state.settings
    if s.broker != "paper":
        raise HTTPException(403, "Las alertas de prueba solo están permitidas con broker=paper")
    st = request.app.state.store.load()
    broker = request.app.state.router.broker
    symbol = request.app.state.router.resolve_symbol(body.ticker)
    if hasattr(broker, "last_price"):
        broker.last_price[symbol] = body.price
    side = 1 if body.action == "BUY" else -1
    dist = body.price * body.sl_pct / 100
    payload = {
        "secret": s.webhook_secret.get_secret_value(),
        "alert_id": f"panel-{time.time_ns()}", "model_version": st.active_model_version or "0.9.4",
        "ticker": body.ticker, "action": body.action, "grade": body.grade, "price": body.price,
        "risk_pct": body.risk_pct, "bar_time": int(time.time() * 1000),
        "context": {"source": "panel"},
    }
    if body.action != "CLOSE":
        payload["sl"] = body.price - side * dist
        if body.with_tps:
            payload["tp"] = [body.price + side * dist * k for k in (1, 2, 3)]
    try:
        alert = TVAlert.model_validate(payload)
    except Exception as e:
        raise HTTPException(422, str(e)) from None
    if not request.app.state.dedup.first_time(alert.alert_id):
        return {"status": "duplicate"}
    rec = request.app.state.router.submit(alert).result(timeout=30)
    return rec


# --------------------------------------------------------------------- optimizador
class RunBody(BaseModel):
    source: str = "demo"                  # demo | csv
    trials: int = Field(30, ge=5, le=500)
    auto_approve: bool = False
    min_oos_pf: float = Field(1.10, ge=0)
    require_positive_ci: bool = True
    interval_s: float = Field(3600, ge=5, le=86_400)
    max_iterations: int = Field(2, ge=1, le=20)


def _loop_config(body: RunBody, request: Request):
    from macroflow.optimization.autoloop import LoopConfig
    if body.auto_approve and not request.app.state.settings.allow_auto_approve:
        raise HTTPException(403, "Auto-aprobación bloqueada; requiere MF_ALLOW_AUTO_APPROVE=1")
    return LoopConfig(source=body.source, trials=body.trials, auto_approve=body.auto_approve,
                      min_oos_pf=body.min_oos_pf, require_positive_ci=body.require_positive_ci,
                      interval_s=body.interval_s, max_iterations=body.max_iterations)


def _check_source(request: Request, body: RunBody) -> None:
    if body.source == "csv" and not (request.app.state.settings.state_dir / "uploaded_data.csv").exists():
        raise HTTPException(400, "Primero sube un CSV")


@router.post("/api/optimizer/run")
def run_optimizer(body: RunBody, request: Request):
    """Una sola iteración (manual). Usa el mismo optimizador persistente que el bucle."""
    loop = request.app.state.autoloop
    if not request.app.state.settings.enable_experimental_optimizer:
        raise HTTPException(403, "Optimizador bloqueado hasta superar paridad Pine-Python")
    _check_source(request, body)
    if loop.state.busy:
        raise HTTPException(409, "Ya hay una iteración en curso")
    if not loop.state.loop_active:
        loop.cfg = _loop_config(body, request)
    threading.Thread(target=loop.run_once, daemon=True, name="mf-once").start()
    return {"started": True}


@router.post("/api/loop/start")
def loop_start(body: RunBody, request: Request):
    if not request.app.state.settings.enable_experimental_optimizer:
        raise HTTPException(403, "Optimizador bloqueado hasta superar paridad Pine-Python")
    _check_source(request, body)
    try:
        request.app.state.autoloop.start(_loop_config(body, request))
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from None
    return {"loop_active": True}


@router.post("/api/loop/stop")
def loop_stop(request: Request):
    request.app.state.autoloop.stop()
    return {"loop_active": False}


@router.post("/api/loop/reset-space")
def loop_reset_space(request: Request):
    request.app.state.autoloop.reset_space()
    return {"ok": True}


@router.get("/api/optimizer/status")
def optimizer_status(request: Request):
    return request.app.state.autoloop.snapshot()


@router.post("/api/optimizer/upload")
async def upload_csv(request: Request, file: UploadFile = File(...)):
    import pandas as pd

    s = request.app.state.settings
    dest = s.state_dir / "uploaded_data.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = await file.read()
    if len(payload) > s.max_upload_bytes:
        raise HTTPException(413, "CSV demasiado grande")
    dest.write_bytes(payload)
    try:
        df = pd.read_csv(dest, nrows=5)
        missing = {"time", "open", "high", "low", "close", "ref_close"} - set(df.columns)
        if missing:
            dest.unlink()
            raise HTTPException(422, f"Faltan columnas: {sorted(missing)}")
    except HTTPException:
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, f"CSV ilegible: {e}") from None
    rows = sum(1 for _ in dest.open()) - 1
    return {"ok": True, "rows": rows, "columns": list(df.columns)}


# --------------------------------------------------------------------- registro
@router.get("/api/registry")
def registry(request: Request):
    from macroflow.optimization.recursive import ModelRegistry

    reg = ModelRegistry(request.app.state.settings.registry_dir, "0.9.4")
    idx = reg.index()
    versions = []
    for v in reversed(idx["versions"]):
        rec = reg.get(v)
        stats = (rec.get("metrics") or {}).get("stats", {})
        versions.append({"version": v, "status": rec["status"], "created_at": rec["created_at"],
                         "profit_factor": stats.get("profit_factor"), "expectancy_r": stats.get("expectancy_r"),
                         "n": stats.get("n"), "params": rec["params"]})
    return {"active": idx["active"], "latest": idx["latest"], "last_data_end": idx["last_data_end"],
            "versions": versions}


class ApproveBody(BaseModel):
    version: str


@router.post("/api/approve")
def approve(body: ApproveBody, request: Request):
    from macroflow.optimization.recursive import ModelRegistry

    reg = ModelRegistry(request.app.state.settings.registry_dir, "0.9.4")
    if body.version not in reg.index()["versions"]:
        raise HTTPException(404, "Versión desconocida")
    reg.activate(body.version)
    request.app.state.store.set_active_version(body.version, source="panel")
    return {"active": body.version}


@router.get("/api/pine/{version}", include_in_schema=False)
def download_pine(version: str, request: Request):
    p = request.app.state.settings.registry_dir / "pine" / f"MacroFlow_v{version}.pine"
    if not p.exists() or "/" in version or ".." in version:
        raise HTTPException(404, "No hay .pine para esa versión")
    return FileResponse(p, filename=p.name, media_type="text/plain")
