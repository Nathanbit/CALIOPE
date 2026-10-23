"""Puente TradingView v6 -> riesgo -> broker.

Arranque:  uvicorn macroflow.bridge.app:app --host 127.0.0.1 --port 8000
y delante un proxy HTTPS (Caddy/nginx) en el puerto 443: TradingView solo
envía webhooks a los puertos 80 y 443.
"""
from __future__ import annotations

import hmac
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from macroflow.optimization.autoloop import AutoLoop
from macroflow.bridge.dashboard import router as dashboard_router
from macroflow.bridge.router import Deduplicator, OrderRouter
from macroflow.bridge.schemas import TVAlert
from macroflow.config import Settings, get_settings
from macroflow.execution.base import Broker
from macroflow.risk.guards import RiskGuard, RiskStateStore

log = logging.getLogger("macroflow.bridge")


def build_broker(s: Settings) -> Broker:
    if s.broker == "mt5":
        if not s.allow_live_trading:
            raise RuntimeError("MT5 bloqueado: requiere MF_ALLOW_LIVE_TRADING=1 además de MF_BROKER=mt5")
        if s.webhook_secret.get_secret_value() == "change-me":
            raise RuntimeError("MT5 bloqueado: configura un MF_WEBHOOK_SECRET no predeterminado")
        from macroflow.execution.mt5_broker import MT5Broker
        return MT5Broker(s.mt5_login, s.mt5_password.get_secret_value() if s.mt5_password else None,
                         s.mt5_server, s.mt5_path, s.mt5_magic)
    from macroflow.execution.paper import PaperBroker
    return PaperBroker()


def create_app(settings: Settings | None = None, broker: Broker | None = None) -> FastAPI:
    s = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        b = broker or build_broker(s)
        b.connect()
        store = RiskStateStore(s.risk_state_path)
        guard = RiskGuard(store, s.max_daily_loss_pct, s.session_tz, s.kill_switch_path,
                          s.max_consecutive_losses, s.cooldown_minutes)
        app.state.store = store
        app.state.router = OrderRouter(b, guard, s.symbol_map, s.partial_weights,
                                       s.max_risk_pct, s.journal_path)
        app.state.dedup = Deduplicator()
        app.state.settings = s
        app.state.autoloop = AutoLoop(s, store)
        # Reanuda solo: si el bucle estaba activo al cerrar, o si MF_AUTOLOOP=1
        if s.enable_experimental_optimizer and (s.autoloop_on_start or (s.resume_autoloop_on_start and app.state.autoloop.state.loop_active)):
            app.state.autoloop.start()
        yield
        if app.state.autoloop.state.loop_active:
            app.state.autoloop._stop.set()   # pausa sin borrar loop_active: se reanuda al arrancar
        app.state.router.pool.shutdown(wait=True)
        b.shutdown()

    app = FastAPI(title="MacroFlow Bridge", lifespan=lifespan)
    app.include_router(dashboard_router)

    @app.get("/health")
    def health(request: Request):
        st = request.app.state.store.load()
        return {"ok": True, "risk_multiplier": st.risk_multiplier, "halted_reason": st.halted_reason,
                "active_model_version": st.active_model_version,
                "kill_switch": s.kill_switch_path.exists()}

    @app.post("/webhook", status_code=202)
    async def webhook(request: Request):
        if s.ip_allowlist_enabled:
            ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "")
            if ip.split(",")[0].strip() not in s.ip_allowlist:
                raise HTTPException(403, "IP no permitida")
        body = await request.body()
        if len(body) > s.max_webhook_bytes:
            raise HTTPException(413, "payload demasiado grande")
        try:
            alert = TVAlert.model_validate_json(body)
        except Exception as e:
            raise HTTPException(422, f"payload inválido: {e}") from None

        if not hmac.compare_digest(alert.secret.get_secret_value(), s.webhook_secret.get_secret_value()):
            raise HTTPException(401, "secret inválido")

        age = time.time() - alert.bar_time / 1000.0
        if alert.action != "CLOSE" and age > s.max_alert_age_s:
            return JSONResponse({"status": "stale", "age_s": round(age, 1)}, status_code=200)

        if s.enforce_model_version and alert.action != "CLOSE":
            active = request.app.state.store.load().active_model_version
            if active and alert.model_version != active:
                return JSONResponse({"status": "version_mismatch", "active": active,
                                     "received": alert.model_version}, status_code=200)

        if not request.app.state.dedup.first_time(alert.alert_id):
            return JSONResponse({"status": "duplicate"}, status_code=200)

        request.app.state.router.submit(alert)
        return {"status": "queued", "alert_id": alert.alert_id}

    return app


app = create_app()
