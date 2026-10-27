"""The desk policy and the hard caps no policy edit can exceed. Standard library only.

``desk/policy.json`` is the person's choice (here: up to 20% of equity as
margin per order, leverage up to 5x, a 5% daily loss limit). The caps below
live in code, so loosening them takes a reviewed code change, not a quiet
edit of a JSON file.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "desk" / "policy.json"
HARD_CAPS = {"max_order_fraction": 0.5, "max_leverage": 20, "max_daily_loss": 0.10}
STAGES = ("demo", "live")
KEYS = {"version", "owner", "stage", "instruments", "margin_mode", "position_mode",
        "max_order_fraction", "max_leverage", "max_daily_loss", "require_stop_loss",
        "equity_max_age_seconds", "proposal_max_age_seconds", "approval_max_age_seconds",
        "auto", "ledger", "predictions"}


class PolicyError(ValueError):
    pass


def validate(policy: dict) -> dict:
    if not isinstance(policy, dict) or set(policy) != KEYS:
        raise PolicyError(f"policy keys must be exactly {sorted(KEYS)}")
    if policy["stage"] not in STAGES:
        raise PolicyError(f"stage must be one of {STAGES}")
    if policy["require_stop_loss"] is not True:
        raise PolicyError("require_stop_loss must stay true")
    for key, cap in HARD_CAPS.items():
        value = policy[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= cap:
            raise PolicyError(f"{key} must be above 0 and at most {cap}")
    if not isinstance(policy["max_leverage"], int):
        raise PolicyError("max_leverage must be an integer")
    if policy["margin_mode"] not in ("isolated", "cross"):
        raise PolicyError("margin_mode must be isolated or cross")
    if policy["position_mode"] not in ("net", "hedge"):
        raise PolicyError("position_mode must be net or hedge")
    if not policy["instruments"] or not all(isinstance(i, str) and "-" in i for i in policy["instruments"]):
        raise PolicyError("instruments must be BloFin ids like BTC-USDT")
    for key in ("equity_max_age_seconds", "proposal_max_age_seconds", "approval_max_age_seconds"):
        if not isinstance(policy[key], int) or not 10 <= policy[key] <= 3600:
            raise PolicyError(f"{key} must be an integer between 10 and 3600")
    auto = policy["auto"]
    if not isinstance(auto, dict) or set(auto) != {"enabled", "min_n", "must_beat"} \
            or not isinstance(auto["enabled"], bool) or not isinstance(auto["min_n"], int) \
            or auto["min_n"] < 30 or not set(auto["must_beat"]) >= {"volatility", "bootstrap"}:
        raise PolicyError("auto needs enabled (bool), min_n >= 30 and must_beat including "
                          "volatility and bootstrap")
    if not isinstance(policy["owner"], str) or not policy["owner"].strip():
        raise PolicyError("owner is required: the person who approves orders")
    return policy


def load(path: Path = POLICY) -> tuple[dict, str]:
    raw = path.read_bytes()
    try:
        policy = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"{path}: not valid JSON") from exc
    return validate(policy), hashlib.sha256(raw).hexdigest()
