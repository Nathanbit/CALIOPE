from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]

# IPs que TradingView publica para sus webhooks. Verifícalas en su documentación
# antes de activar la allowlist: si cambian, el puente rechazará alertas reales.
TRADINGVIEW_IPS = ["52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MF_", env_file=".env", extra="ignore")

    webhook_secret: SecretStr = SecretStr("change-me")
    broker: str = "paper"                      # paper | mt5
    # Two independent opt-ins are required before any live broker can load.
    allow_live_trading: bool = False
    allow_auto_approve: bool = False
    enable_experimental_optimizer: bool = False
    mt5_login: int | None = None
    mt5_password: SecretStr | None = None
    mt5_server: str | None = None
    mt5_path: str | None = None
    mt5_magic: int = 2026094

    # TradingView ticker -> símbolo del broker
    symbol_map: dict[str, str] = {
        "BINANCE:BTCUSDT": "BTCUSD", "BINANCE:ETHUSDT": "ETHUSD", "BINANCE:SOLUSDT": "SOLUSD",
        "BTCUSDT": "BTCUSD", "ETHUSDT": "ETHUSD", "SOLUSDT": "SOLUSD",
    }
    partial_weights: list[float] = [0.5, 0.3, 0.2]   # salidas 50/30/20
    max_risk_pct: float = 2.0                        # tope duro aunque Pine pida más
    max_daily_loss_pct: float = 3.0
    max_consecutive_losses: int | None = 3           # MF_MAX_CONSECUTIVE_LOSSES; vacío = desactivado
    cooldown_minutes: float = 10.0                   # espera mínima tras cada cierre
    session_tz: str = "America/New_York"
    max_alert_age_s: int = 120                       # descarta alertas viejas (reintentos/colas)
    max_webhook_bytes: int = 64_000
    max_upload_bytes: int = 50_000_000
    enforce_model_version: bool = True               # rechaza alertas de un .pine no aprobado
    ip_allowlist_enabled: bool = False
    ip_allowlist: list[str] = TRADINGVIEW_IPS

    autoloop_on_start: bool = False                 # MF_AUTOLOOP_ON_START=1: el bucle arranca con el servidor
    resume_autoloop_on_start: bool = False

    state_dir: Path = ROOT / "state"
    registry_dir: Path = ROOT / "registry"

    @property
    def risk_state_path(self) -> Path:
        return self.state_dir / "risk_state.json"

    @property
    def kill_switch_path(self) -> Path:
        return self.state_dir / "KILL"

    @property
    def journal_path(self) -> Path:
        return self.state_dir / "journal.jsonl"


@lru_cache
def get_settings() -> Settings:
    return Settings()
