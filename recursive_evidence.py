"""Append-only, fail-closed evidence ledger for CALIOPE research.

The ledger turns portfolio observations into bounded research evidence.  It
does not place orders, modify a strategy, publish an indicator, or promote a
candidate automatically.  A model-promotion *record* is accepted only after
multiple completed observations, preregistered OOS/holdout validation and an
explicit human decision.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import evidence_io as eio

SCHEMA_VERSION = "1.0"
EVENT_TYPES = ("observation", "hypothesis", "candidate", "validation", "promotion")
PARTITIONS = {"oos", "holdout"}
HEX64 = eio.HEX64
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{3,100}$")


class EvidenceGateError(ValueError):
    """Evidence is incomplete, inconsistent, or unsafe to advance."""


def _canonical(value: Any) -> bytes:
    return eio.canonical_bytes(value, EvidenceGateError, "non-canonical evidence")


def _digest(value: Any) -> str:
    return eio.sha256_hex(_canonical(value), upper=True)


def _read_json(path: Path) -> dict:
    return eio.read_json_object(path, EvidenceGateError)


def _atomic_json(path: Path, value: dict) -> None:
    eio.atomic_write_json(path, value, EvidenceGateError, "non-canonical evidence")


def _text(value: Any, name: str, minimum: int = 1, maximum: int = 2_000) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise EvidenceGateError(f"invalid {name}")
    return value


def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise EvidenceGateError(f"invalid {name}")
    return value


def _hash(value: Any, name: str) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise EvidenceGateError(f"invalid {name}")
    return value.upper()


def _number(value: Any, name: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EvidenceGateError(f"invalid {name}")
    return float(value)


def _nonnegative(value: Any, name: str, *, nullable: bool = False) -> float | None:
    result = _number(value, name, nullable=nullable)
    if result is not None and result < 0:
        raise EvidenceGateError(f"invalid {name}")
    return result


def _timestamp(value: Any, name: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    text = _text(value, name, 10, 80)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise EvidenceGateError(f"invalid {name}") from None
    if parsed.tzinfo is None:
        raise EvidenceGateError(f"{name} must include a timezone")
    return text


def _exact_keys(value: Any, name: str, required: set[str], optional: set[str] | None = None) -> dict:
    if not isinstance(value, dict):
        raise EvidenceGateError(f"invalid {name}")
    optional = optional or set()
    missing = required - set(value)
    extra = set(value) - required - optional
    if missing or extra:
        raise EvidenceGateError(
            f"invalid {name} keys; missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return value


def _string_list(value: Any, name: str, minimum: int = 0, maximum: int = 100) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise EvidenceGateError(f"invalid {name}")
    if any(not isinstance(item, str) or not item for item in value):
        raise EvidenceGateError(f"invalid {name}")
    if len(set(value)) != len(value):
        raise EvidenceGateError(f"{name} must be unique")
    return value


def validate_policy(policy: dict) -> None:
    required = {
        "minimum_distinct_observations",
        "minimum_validation_samples",
        "minimum_complete_plans_total",
        "minimum_complete_plans_per_partition",
        "required_partitions",
        "automatic_publish",
        "automatic_trading",
    }
    _exact_keys(policy, "policy", required)
    for name in (
        "minimum_distinct_observations",
        "minimum_validation_samples",
        "minimum_complete_plans_total",
        "minimum_complete_plans_per_partition",
    ):
        value = policy[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise EvidenceGateError(f"invalid policy {name}")
    if policy["minimum_distinct_observations"] < 2:
        raise EvidenceGateError("policy cannot learn from one observation")
    if policy["minimum_validation_samples"] < 2:
        raise EvidenceGateError("policy cannot promote from one validation sample")
    required_partitions = _string_list(policy["required_partitions"], "required_partitions", 2, 2)
    if set(required_partitions) != PARTITIONS:
        raise EvidenceGateError("policy must require both oos and holdout")
    if policy["automatic_publish"] is not False or policy["automatic_trading"] is not False:
        raise EvidenceGateError("automatic publication and trading are forbidden")


def _validate_plan(plan: Any, name: str, *, updated: bool) -> None:
    required = {"take_profit", "stop_loss", "recorded_at"}
    optional = {"reason"} if updated else set()
    plan = _exact_keys(plan, name, required, optional)
    _number(plan["take_profit"], f"{name}.take_profit", nullable=True)
    _number(plan["stop_loss"], f"{name}.stop_loss", nullable=True)
    _timestamp(plan["recorded_at"], f"{name}.recorded_at")
    if updated:
        _text(plan.get("reason", ""), f"{name}.reason", 1, 500)


def _validate_execution_metrics(metrics: Any, name: str) -> None:
    keys = {
        "unrealized_pnl", "realized_pnl", "mfe", "mae", "excursion_unit",
        "slippage", "slippage_unit", "gap_sensitivity",
    }
    metrics = _exact_keys(metrics, name, keys)
    for field in ("unrealized_pnl", "realized_pnl", "mfe", "mae", "slippage"):
        _number(metrics[field], f"{name}.{field}", nullable=True)
    if metrics["mae"] is not None and metrics["mae"] < 0:
        raise EvidenceGateError(f"{name}.mae must be a non-negative magnitude")
    if metrics["mfe"] is not None and metrics["mfe"] < 0:
        raise EvidenceGateError(f"{name}.mfe must be a non-negative magnitude")
    if metrics["excursion_unit"] not in {"currency", "R", "percent", "price"}:
        raise EvidenceGateError(f"invalid {name}.excursion_unit")
    _text(metrics["slippage_unit"], f"{name}.slippage_unit", 1, 40)
    scenarios = metrics["gap_sensitivity"]
    if not isinstance(scenarios, list) or len(scenarios) > 20:
        raise EvidenceGateError(f"invalid {name}.gap_sensitivity")
    for index, scenario in enumerate(scenarios):
        scenario = _exact_keys(
            scenario, f"{name}.gap_sensitivity[{index}]",
            {"gap", "gap_unit", "estimated_pnl_impact", "currency"},
        )
        _number(scenario["gap"], f"{name}.gap_sensitivity[{index}].gap")
        _number(
            scenario["estimated_pnl_impact"],
            f"{name}.gap_sensitivity[{index}].estimated_pnl_impact",
        )
        _text(scenario["gap_unit"], "gap_unit", 1, 40)
        _text(scenario["currency"], "currency", 1, 12)


def _validate_indicator_snapshot(snapshot: Any, index: int) -> str:
    name = f"indicator_snapshots[{index}]"
    snapshot = _exact_keys(
        snapshot, name,
        {
            "asset", "feed", "timeframe", "observed_at", "source", "model",
            "values", "decision_context",
        },
    )
    asset = _text(snapshot["asset"], f"{name}.asset", 1, 80)
    _text(snapshot["feed"], f"{name}.feed", 1, 120)
    _text(snapshot["timeframe"], f"{name}.timeframe", 1, 40)
    _timestamp(snapshot["observed_at"], f"{name}.observed_at")
    _text(snapshot["source"], f"{name}.source", 1, 200)
    _text(snapshot["model"], f"{name}.model", 1, 120)
    values = snapshot["values"]
    if not isinstance(values, dict) or not values or len(values) > 100:
        raise EvidenceGateError(f"invalid {name}.values")
    for key, value in values.items():
        _text(key, f"{name}.indicator_name", 1, 100)
        if value is not None and not isinstance(value, (str, bool, int, float)):
            raise EvidenceGateError(f"invalid {name}.values[{key}]")
        if isinstance(value, float) and not math.isfinite(value):
            raise EvidenceGateError(f"invalid {name}.values[{key}]")
    context = _exact_keys(
        snapshot["decision_context"], f"{name}.decision_context",
        {"new_entry_signal", "manage_existing", "reported_action"},
    )
    if context["new_entry_signal"] not in {"long", "short", "none", "not_evaluated"}:
        raise EvidenceGateError(f"invalid {name}.decision_context.new_entry_signal")
    if context["manage_existing"] not in {
        "hold", "adjust", "reduce", "close", "observe_only", "not_evaluated",
    }:
        raise EvidenceGateError(f"invalid {name}.decision_context.manage_existing")
    _text(context["reported_action"], f"{name}.decision_context.reported_action", 1, 120)
    return asset


def validate_indicator_batch(batch: dict) -> None:
    """Validate raw indicator evidence before it is joined to broker positions."""
    batch = _exact_keys(
        batch, "indicator batch",
        {
            "schema_version", "batch_id", "captured_from", "timezone_status",
            "snapshots", "portfolio_context",
        },
    )
    if batch["schema_version"] != SCHEMA_VERSION:
        raise EvidenceGateError("unsupported indicator batch schema")
    _identifier(batch["batch_id"], "batch_id")
    _text(batch["captured_from"], "captured_from", 1, 200)
    if batch["timezone_status"] not in {"known", "unknown"}:
        raise EvidenceGateError("invalid timezone_status")
    snapshots = batch["snapshots"]
    if not isinstance(snapshots, list) or not snapshots:
        raise EvidenceGateError("indicator batch requires snapshots")
    keys: set[tuple[str, str, str]] = set()
    for index, snapshot in enumerate(snapshots):
        _validate_indicator_snapshot(snapshot, index)
        key = (snapshot["feed"], snapshot["timeframe"], snapshot["observed_at"])
        if key in keys:
            raise EvidenceGateError("duplicate feed/timeframe/timestamp snapshot")
        keys.add(key)
    context = _exact_keys(
        batch["portfolio_context"], "portfolio_context",
        {
            "outcome", "promotion_eligible", "new_entry_action",
            "manage_existing_action", "risk_summary", "notes",
        },
        {"basket_metrics"},
    )
    if context["outcome"] not in {"open_unresolved", "closed", "cancelled", "unknown"}:
        raise EvidenceGateError("invalid portfolio_context.outcome")
    if context["promotion_eligible"] is not False:
        raise EvidenceGateError("raw live case evidence is not promotion eligible")
    _text(context["new_entry_action"], "portfolio_context.new_entry_action", 1, 120)
    _text(context["manage_existing_action"], "portfolio_context.manage_existing_action", 1, 200)
    risk = _exact_keys(
        context["risk_summary"], "portfolio_context.risk_summary",
        {"stop_distance_atr", "original_take_profit_distance_atr", "interpretation"},
    )
    for field in ("stop_distance_atr", "original_take_profit_distance_atr"):
        bounds = _exact_keys(risk[field], field, {"minimum", "maximum"})
        minimum = _nonnegative(bounds["minimum"], f"{field}.minimum")
        maximum = _nonnegative(bounds["maximum"], f"{field}.maximum")
        if minimum > maximum:
            raise EvidenceGateError(f"invalid {field} range")
    _text(risk["interpretation"], "risk_summary.interpretation", 1, 1_000)
    _string_list(context["notes"], "portfolio_context.notes", 0, 20)
    if "basket_metrics" in context:
        metrics = _exact_keys(
            context["basket_metrics"], "portfolio_context.basket_metrics",
            {
                "open_pnl_approx", "protected_profit_gross", "all_targets_gross",
                "currency", "largest_position_share", "largest_position_label",
                "correlation_assessment", "gap_sensitivity", "provenance",
            },
        )
        for field in ("open_pnl_approx", "protected_profit_gross", "all_targets_gross"):
            _number(metrics[field], f"basket_metrics.{field}")
        share = _number(metrics["largest_position_share"], "basket_metrics.largest_position_share")
        if not 0 <= share <= 1:
            raise EvidenceGateError("basket largest_position_share must be in [0, 1]")
        _text(metrics["currency"], "basket_metrics.currency", 1, 40)
        _text(metrics["largest_position_label"], "basket_metrics.largest_position_label", 1, 80)
        _text(metrics["correlation_assessment"], "basket_metrics.correlation_assessment", 1, 1_000)
        _text(metrics["provenance"], "basket_metrics.provenance", 1, 300)
        gaps = metrics["gap_sensitivity"]
        if not isinstance(gaps, list) or len(gaps) > 20:
            raise EvidenceGateError("invalid basket_metrics.gap_sensitivity")
        for index, scenario in enumerate(gaps):
            scenario = _exact_keys(
                scenario, f"basket_metrics.gap_sensitivity[{index}]",
                {"asset", "adverse_slippage", "unit", "effect"},
            )
            _text(scenario["asset"], "gap asset", 1, 80)
            _nonnegative(scenario["adverse_slippage"], "gap adverse_slippage")
            _text(scenario["unit"], "gap unit", 1, 40)
            _text(scenario["effect"], "gap effect", 1, 500)


def _validate_portfolio_validation(value: Any, position_ids: set[str]) -> bool:
    value = _exact_keys(
        value, "portfolio_validation",
        {
            "performed", "method", "correlation_groups", "concentration",
            "aggregate_risk", "assessment",
        },
    )
    if not isinstance(value["performed"], bool):
        raise EvidenceGateError("portfolio_validation.performed must be boolean")
    _text(value["method"], "portfolio_validation.method", 1, 200)
    _nonnegative(value["aggregate_risk"], "portfolio_validation.aggregate_risk", nullable=True)
    _text(value["assessment"], "portfolio_validation.assessment", 1, 1_000)
    concentration = _exact_keys(
        value["concentration"], "portfolio_validation.concentration",
        {"basis", "largest_position_share", "shares"},
    )
    _text(concentration["basis"], "concentration.basis", 1, 80)
    largest = _number(
        concentration["largest_position_share"], "concentration.largest_position_share",
        nullable=True,
    )
    if largest is not None and not 0 <= largest <= 1:
        raise EvidenceGateError("largest_position_share must be in [0, 1]")
    shares = concentration["shares"]
    if not isinstance(shares, dict):
        raise EvidenceGateError("concentration.shares must be an object")
    for position_id, share in shares.items():
        if position_id not in position_ids:
            raise EvidenceGateError("concentration references an unknown position")
        number = _number(share, f"concentration.shares[{position_id}]")
        if not 0 <= number <= 1:
            raise EvidenceGateError("concentration share must be in [0, 1]")
    groups = value["correlation_groups"]
    if not isinstance(groups, list) or len(groups) > 50:
        raise EvidenceGateError("invalid portfolio_validation.correlation_groups")
    for index, group in enumerate(groups):
        group = _exact_keys(
            group, f"correlation_groups[{index}]",
            {"position_ids", "relationship", "coefficient", "lookback", "source"},
        )
        members = set(_string_list(group["position_ids"], "correlation position_ids", 2, 30))
        if not members.issubset(position_ids):
            raise EvidenceGateError("correlation group references an unknown position")
        coefficient = _number(group["coefficient"], "correlation coefficient", nullable=True)
        if coefficient is not None and not -1 <= coefficient <= 1:
            raise EvidenceGateError("correlation coefficient must be in [-1, 1]")
        _text(group["relationship"], "correlation relationship", 1, 300)
        _text(group["lookback"], "correlation lookback", 1, 100)
        _text(group["source"], "correlation source", 1, 200)
    if value["performed"]:
        if len(position_ids) > 1 and not groups:
            raise EvidenceGateError("multi-position observations require correlation assessment")
        if largest is None or set(shares) != position_ids:
            raise EvidenceGateError("performed validation requires concentration for every position")
        numeric_shares = [float(item) for item in shares.values()]
        if not math.isclose(sum(numeric_shares), 1.0, rel_tol=0.0, abs_tol=1e-6):
            raise EvidenceGateError("performed concentration shares must sum to 1")
        if not math.isclose(max(numeric_shares), largest, rel_tol=0.0, abs_tol=1e-6):
            raise EvidenceGateError("largest_position_share does not match concentration shares")
    return value["performed"]


def validate_observation(payload: dict) -> None:
    payload = _exact_keys(
        payload, "observation payload",
        {
            "sample_id", "observed_at", "source", "account", "positions",
            "indicator_snapshots", "portfolio_validation", "human_decision",
            "final_result",
        },
    )
    _identifier(payload["sample_id"], "sample_id")
    _timestamp(payload["observed_at"], "observed_at")
    _text(payload["source"], "source", 1, 300)
    account = _exact_keys(payload["account"], "account", {"broker", "mode", "base_currency", "equity"})
    _text(account["broker"], "account.broker", 1, 80)
    if account["mode"] not in {"demo", "paper", "live", "unknown"}:
        raise EvidenceGateError("invalid account.mode")
    _text(account["base_currency"], "account.base_currency", 1, 12)
    _nonnegative(account["equity"], "account.equity", nullable=True)

    positions = payload["positions"]
    if not isinstance(positions, list) or not 1 <= len(positions) <= 100:
        raise EvidenceGateError("observation requires positions")
    position_ids: set[str] = set()
    assets: set[str] = set()
    for index, position in enumerate(positions):
        name = f"positions[{index}]"
        position = _exact_keys(
            position, name,
            {
                "position_id", "asset", "asset_class", "side", "quantity",
                "entry_price", "original_plan", "updated_plan", "execution_metrics",
            },
        )
        position_id = _identifier(position["position_id"], f"{name}.position_id")
        if position_id in position_ids:
            raise EvidenceGateError("position_id must be unique")
        position_ids.add(position_id)
        assets.add(_text(position["asset"], f"{name}.asset", 1, 80))
        _text(position["asset_class"], f"{name}.asset_class", 1, 80)
        if position["side"] not in {"long", "short"}:
            raise EvidenceGateError(f"invalid {name}.side")
        if _nonnegative(position["quantity"], f"{name}.quantity") == 0:
            raise EvidenceGateError(f"{name}.quantity must be positive")
        _number(position["entry_price"], f"{name}.entry_price")
        _validate_plan(position["original_plan"], f"{name}.original_plan", updated=False)
        if position["updated_plan"] is not None:
            _validate_plan(position["updated_plan"], f"{name}.updated_plan", updated=True)
        _validate_execution_metrics(position["execution_metrics"], f"{name}.execution_metrics")

    snapshots = payload["indicator_snapshots"]
    if not isinstance(snapshots, list) or not snapshots:
        raise EvidenceGateError("indicator_snapshots are required")
    snapshot_assets = {_validate_indicator_snapshot(item, index) for index, item in enumerate(snapshots)}
    if not assets.issubset(snapshot_assets):
        raise EvidenceGateError("every positioned asset requires an indicator snapshot")

    _validate_portfolio_validation(payload["portfolio_validation"], position_ids)
    decision = _exact_keys(
        payload["human_decision"], "human_decision",
        {"decision_scope", "action", "rationale", "decided_by", "decided_at", "executed"},
    )
    if decision["decision_scope"] not in {"new_entry_signal", "manage_existing"}:
        raise EvidenceGateError("human_decision must distinguish entry from position management")
    _text(decision["action"], "human_decision.action", 1, 80)
    _text(decision["rationale"], "human_decision.rationale", 1, 1_000)
    _text(decision["decided_by"], "human_decision.decided_by", 1, 120)
    _timestamp(decision["decided_at"], "human_decision.decided_at")
    if not isinstance(decision["executed"], bool):
        raise EvidenceGateError("human_decision.executed must be boolean")

    result = _exact_keys(
        payload["final_result"], "final_result",
        {"status", "closed_at", "net_pnl", "currency", "notes"},
    )
    if result["status"] not in {"open", "closed", "cancelled", "unknown"}:
        raise EvidenceGateError("invalid final_result.status")
    _timestamp(result["closed_at"], "final_result.closed_at", nullable=True)
    _number(result["net_pnl"], "final_result.net_pnl", nullable=True)
    _text(result["currency"], "final_result.currency", 1, 12)
    _text(result["notes"], "final_result.notes", 0, 1_000)
    if result["status"] == "closed" and (result["closed_at"] is None or result["net_pnl"] is None):
        raise EvidenceGateError("closed results require closed_at and net_pnl")
    if result["status"] != "closed" and result["closed_at"] is not None:
        raise EvidenceGateError("only closed results may have closed_at")


def _validate_hypothesis(payload: dict) -> None:
    payload = _exact_keys(
        payload, "hypothesis payload",
        {"hypothesis_id", "claim", "observation_hashes", "falsification", "expected_effect"},
    )
    _identifier(payload["hypothesis_id"], "hypothesis_id")
    _text(payload["claim"], "hypothesis claim", 10, 2_000)
    for value in _string_list(payload["observation_hashes"], "observation_hashes", 2, 100):
        _hash(value, "observation_hash")
    _string_list(payload["falsification"], "falsification", 1, 20)
    _text(payload["expected_effect"], "expected_effect", 1, 1_000)


def _validate_candidate(payload: dict) -> None:
    payload = _exact_keys(
        payload, "candidate payload",
        {"candidate_id", "hypothesis_hash", "artifact_hash", "change_summary", "invariants"},
    )
    _identifier(payload["candidate_id"], "candidate_id")
    _hash(payload["hypothesis_hash"], "hypothesis_hash")
    _hash(payload["artifact_hash"], "artifact_hash")
    _text(payload["change_summary"], "change_summary", 1, 2_000)
    _string_list(payload["invariants"], "invariants", 1, 30)


def _validate_validation(payload: dict) -> None:
    payload = _exact_keys(
        payload, "validation payload",
        {
            "validation_id", "candidate_hash", "artifact_hash", "partition", "sample_id",
            "dataset_hash", "complete_plans", "metrics", "passed", "sealed_before_evaluation",
            "used_for_design", "evidence_refs",
        },
    )
    _identifier(payload["validation_id"], "validation_id")
    _hash(payload["candidate_hash"], "candidate_hash")
    _hash(payload["artifact_hash"], "artifact_hash")
    if payload["partition"] not in PARTITIONS:
        raise EvidenceGateError("validation partition must be oos or holdout")
    _identifier(payload["sample_id"], "validation sample_id")
    _hash(payload["dataset_hash"], "dataset_hash")
    plans = payload["complete_plans"]
    if isinstance(plans, bool) or not isinstance(plans, int) or plans < 0:
        raise EvidenceGateError("invalid complete_plans")
    metrics = payload["metrics"]
    if not isinstance(metrics, dict) or not metrics:
        raise EvidenceGateError("validation metrics are required")
    _canonical(metrics)
    for name in ("passed", "sealed_before_evaluation", "used_for_design"):
        if not isinstance(payload[name], bool):
            raise EvidenceGateError(f"{name} must be boolean")
    _string_list(payload["evidence_refs"], "validation evidence_refs", 1, 100)
    if not payload["sealed_before_evaluation"] or payload["used_for_design"]:
        raise EvidenceGateError("OOS/holdout evidence must be sealed and unused for design")


def _validate_promotion(payload: dict) -> None:
    payload = _exact_keys(
        payload, "promotion payload",
        {
            "candidate_hash", "validation_hashes", "human_decision",
            "automatic_publish", "automatic_trading", "final_result",
        },
    )
    _hash(payload["candidate_hash"], "candidate_hash")
    for value in _string_list(payload["validation_hashes"], "validation_hashes", 2, 100):
        _hash(value, "validation_hash")
    decision = _exact_keys(
        payload["human_decision"], "promotion human_decision",
        {"approved", "decided_by", "decided_at", "rationale"},
    )
    if decision["approved"] is not True:
        raise EvidenceGateError("promotion requires explicit human approval")
    _text(decision["decided_by"], "promotion decided_by", 1, 120)
    _timestamp(decision["decided_at"], "promotion decided_at")
    _text(decision["rationale"], "promotion rationale", 1, 1_000)
    if payload["automatic_publish"] is not False or payload["automatic_trading"] is not False:
        raise EvidenceGateError("promotion record cannot publish or trade")
    _text(payload["final_result"], "promotion final_result", 1, 1_000)


VALIDATORS = {
    "observation": validate_observation,
    "hypothesis": _validate_hypothesis,
    "candidate": _validate_candidate,
    "validation": _validate_validation,
    "promotion": _validate_promotion,
}


def _ledger_lock(root: Path):
    return eio.exclusive_lock(root / ".caliope-evidence.lock", EvidenceGateError,
                              "evidence ledger is locked")


def init_ledger(root: Path, case_id: str, policy: dict) -> Path:
    """Create an immutable-policy ledger for one bounded research question."""
    _identifier(case_id, "case_id")
    validate_policy(policy)
    if root.exists():
        raise EvidenceGateError(f"ledger already exists: {root}")
    (root / "events").mkdir(parents=True)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stage": "observation",
        "policy": policy,
        "events": [],
        "automatic_publish": False,
        "automatic_trading": False,
    }
    _atomic_json(root / "ledger.json", manifest)
    return root


def _load(root: Path) -> tuple[dict, list[tuple[str, dict]]]:
    manifest = _read_json(root / "ledger.json")
    _exact_keys(
        manifest, "ledger",
        {
            "schema_version", "case_id", "created_at", "stage", "policy", "events",
            "automatic_publish", "automatic_trading",
        },
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise EvidenceGateError("unsupported ledger schema")
    _identifier(manifest["case_id"], "case_id")
    _timestamp(manifest["created_at"], "created_at")
    if manifest["stage"] not in EVENT_TYPES:
        raise EvidenceGateError("invalid ledger stage")
    validate_policy(manifest["policy"])
    if manifest["automatic_publish"] is not False or manifest["automatic_trading"] is not False:
        raise EvidenceGateError("operational automation is forbidden")
    if not isinstance(manifest["events"], list):
        raise EvidenceGateError("invalid event index")

    loaded: list[tuple[str, dict]] = []
    previous_hash: str | None = None
    for index, item in enumerate(manifest["events"]):
        item = _exact_keys(item, f"events[{index}]", {"type", "path", "hash"})
        if item["type"] not in EVENT_TYPES:
            raise EvidenceGateError("invalid indexed event type")
        digest = _hash(item["hash"], "indexed event hash")
        event_path = (root / item["path"]).resolve(strict=True)
        if not event_path.is_relative_to((root / "events").resolve()):
            raise EvidenceGateError("event path escapes ledger")
        event = _read_json(event_path)
        if _digest(event) != digest:
            raise EvidenceGateError("event hash mismatch")
        _exact_keys(
            event, "event",
            {"schema_version", "case_id", "sequence", "event_type", "recorded_at", "previous_hash", "payload"},
        )
        if (
            event["schema_version"] != SCHEMA_VERSION
            or event["case_id"] != manifest["case_id"]
            or event["sequence"] != index
            or event["event_type"] != item["type"]
            or event["previous_hash"] != previous_hash
        ):
            raise EvidenceGateError("event chain mismatch")
        _timestamp(event["recorded_at"], "recorded_at")
        VALIDATORS[item["type"]](event["payload"])
        loaded.append((digest, event))
        previous_hash = digest
    expected_stage = loaded[-1][1]["event_type"] if loaded else "observation"
    if manifest["stage"] != expected_stage:
        raise EvidenceGateError("ledger stage does not match the append-only event chain")
    return manifest, loaded


def _eligible_observation(event: dict) -> bool:
    payload = event["payload"]
    return (
        payload["final_result"]["status"] == "closed"
        and payload["portfolio_validation"]["performed"] is True
        and all(
            position["execution_metrics"]["realized_pnl"] is not None
            and position["execution_metrics"]["mfe"] is not None
            and position["execution_metrics"]["mae"] is not None
            and position["execution_metrics"]["slippage"] is not None
            and bool(position["execution_metrics"]["gap_sensitivity"])
            for position in payload["positions"]
        )
    )


def _assert_transition(manifest: dict, events: list[tuple[str, dict]], event_type: str, payload: dict) -> None:
    stage = manifest["stage"]
    policy = manifest["policy"]
    if event_type == "observation":
        if stage != "observation":
            raise EvidenceGateError("observations are immutable after hypothesis registration")
        sample_id = payload["sample_id"]
        if any(event["payload"]["sample_id"] == sample_id for _, event in events if event["event_type"] == "observation"):
            raise EvidenceGateError("sample_id already recorded")
        return
    if event_type == "hypothesis":
        if stage != "observation":
            raise EvidenceGateError("hypothesis requires observation stage")
        indexed = {digest: event for digest, event in events if event["event_type"] == "observation"}
        references = [value.upper() for value in payload["observation_hashes"]]
        if len(set(references)) < policy["minimum_distinct_observations"]:
            raise EvidenceGateError("insufficient distinct observations for learning")
        if any(reference not in indexed for reference in references):
            raise EvidenceGateError("hypothesis references unknown observations")
        if any(not _eligible_observation(indexed[reference]) for reference in references):
            raise EvidenceGateError("learning requires closed outcomes and performed portfolio validation")
        sample_ids = {indexed[reference]["payload"]["sample_id"] for reference in references}
        if len(sample_ids) < policy["minimum_distinct_observations"]:
            raise EvidenceGateError("hypothesis samples are not distinct")
        return
    if event_type == "candidate":
        if stage != "hypothesis":
            raise EvidenceGateError("candidate requires a registered hypothesis")
        latest_hash, latest = events[-1]
        if latest["event_type"] != "hypothesis" or payload["hypothesis_hash"].upper() != latest_hash:
            raise EvidenceGateError("candidate does not reference the active hypothesis")
        return
    if event_type == "validation":
        if stage not in {"candidate", "validation"}:
            raise EvidenceGateError("validation requires a candidate")
        candidates = [(digest, event) for digest, event in events if event["event_type"] == "candidate"]
        candidate_hash, candidate = candidates[-1]
        if payload["candidate_hash"].upper() != candidate_hash:
            raise EvidenceGateError("validation references a different candidate")
        if payload["artifact_hash"].upper() != candidate["payload"]["artifact_hash"].upper():
            raise EvidenceGateError("validated artifact differs from candidate artifact")
        for _, event in events:
            if event["event_type"] != "validation":
                continue
            prior = event["payload"]
            if prior["sample_id"] == payload["sample_id"] or prior["dataset_hash"].upper() == payload["dataset_hash"].upper():
                raise EvidenceGateError("validation sample or dataset was already consumed")
        return
    if event_type == "promotion":
        if stage != "validation":
            raise EvidenceGateError("promotion requires validation evidence")
        candidates = [(digest, event) for digest, event in events if event["event_type"] == "candidate"]
        candidate_hash, candidate = candidates[-1]
        if payload["candidate_hash"].upper() != candidate_hash:
            raise EvidenceGateError("promotion references a different candidate")
        validations = {digest: event for digest, event in events if event["event_type"] == "validation"}
        requested = [value.upper() for value in payload["validation_hashes"]]
        if any(digest not in validations for digest in requested):
            raise EvidenceGateError("promotion references unknown validation evidence")
        # A promotion must disclose every validation run for the active
        # candidate.  Otherwise a caller could cherry-pick passing partitions
        # and silently omit a failed test from the promotion record.
        candidate_validations = {
            digest: event
            for digest, event in validations.items()
            if event["payload"]["candidate_hash"].upper() == candidate_hash
        }
        if set(requested) != set(candidate_validations):
            raise EvidenceGateError("promotion must include every candidate validation")
        selected = [validations[digest]["payload"] for digest in requested]
        if len({item["sample_id"] for item in selected}) < policy["minimum_validation_samples"]:
            raise EvidenceGateError("insufficient distinct validation samples")
        if set(item["partition"] for item in selected) != set(policy["required_partitions"]):
            raise EvidenceGateError("promotion requires all preregistered partitions")
        if any(not item["passed"] for item in selected):
            raise EvidenceGateError("a failed validation blocks promotion")
        if any(item["artifact_hash"].upper() != candidate["payload"]["artifact_hash"].upper() for item in selected):
            raise EvidenceGateError("validation artifact mismatch")
        total_plans = sum(item["complete_plans"] for item in selected)
        if total_plans < policy["minimum_complete_plans_total"]:
            raise EvidenceGateError("insufficient total completed plans")
        for partition in policy["required_partitions"]:
            partition_plans = sum(item["complete_plans"] for item in selected if item["partition"] == partition)
            if partition_plans < policy["minimum_complete_plans_per_partition"]:
                raise EvidenceGateError(f"insufficient completed plans in {partition}")
        return
    raise EvidenceGateError(f"unsupported event type: {event_type}")


def append_event(root: Path, event_type: str, payload: dict, *, recorded_at: str | None = None) -> str:
    """Validate and atomically append one research event; return its SHA-256."""
    if event_type not in EVENT_TYPES:
        raise EvidenceGateError("invalid event_type")
    VALIDATORS[event_type](payload)
    with _ledger_lock(root):
        manifest, events = _load(root)
        _assert_transition(manifest, events, event_type, payload)
        recorded_at = recorded_at or datetime.now(timezone.utc).isoformat()
        _timestamp(recorded_at, "recorded_at")
        previous_hash = events[-1][0] if events else None
        event = {
            "schema_version": SCHEMA_VERSION,
            "case_id": manifest["case_id"],
            "sequence": len(events),
            "event_type": event_type,
            "recorded_at": recorded_at,
            "previous_hash": previous_hash,
            "payload": payload,
        }
        digest = _digest(event)
        relative = Path("events") / f"{len(events):04d}-{event_type}-{digest[:12].lower()}.json"
        _atomic_json(root / relative, event)
        updated = json.loads(json.dumps(manifest))
        updated["events"].append({"type": event_type, "path": relative.as_posix(), "hash": digest})
        updated["stage"] = event_type
        _atomic_json(root / "ledger.json", updated)
        return digest


def status(root: Path) -> dict:
    """Return a validated, non-operational summary of the ledger."""
    manifest, events = _load(root)
    counts = {event_type: 0 for event_type in EVENT_TYPES}
    for _, event in events:
        counts[event["event_type"]] += 1
    return {
        "case_id": manifest["case_id"],
        "stage": manifest["stage"],
        "counts": counts,
        "automatic_publish": False,
        "automatic_trading": False,
    }
