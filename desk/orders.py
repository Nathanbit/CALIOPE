"""Orders ledger and gates of the CALIOPE desk. Standard library only.

A hash-chained JSONL like the prediction ledger. Record types:

- ``equity``: account equity read from the exchange (the base of every limit).
- ``kill`` / ``unkill``: the kill switch. While active, nothing is proposed,
  approved or sent. A daily loss at the limit turns it on by itself.
- ``proposal``: an order an author proposes. It must come from that author's
  sealed forecast, carry a stop-loss, and fit the policy limits frozen into
  the record (margin share, leverage, daily loss, loss at the stop).
- ``decision``: approve or reject. ``human`` only by the policy owner (the
  CLI asks for a typed confirmation at a terminal); ``auto`` only when the
  policy enables it and the author beat the volatility and bootstrap rivals
  with enough resolved forecasts, recomputed from the prediction ledger as it
  stood when the order was proposed.
- ``send``: sealed *before* the request leaves, with the exact body; it must
  be the body the proposal implies, within the approval's time limit.
- ``result``: what the exchange answered.

``verify`` replays every gate, so a forged or edited record fails even with
a valid hash.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import evidence_io as eio  # noqa: E402
import prediction_ledger as pl  # noqa: E402

from desk import policy as pol  # noqa: E402

GENESIS = "0" * 64
TYPES = ("equity", "kill", "unkill", "proposal", "decision", "send", "result")
LIMIT_KEYS = ("owner", "max_order_fraction", "max_leverage", "max_daily_loss",
              "equity_max_age_seconds", "proposal_max_age_seconds", "approval_max_age_seconds",
              "margin_mode", "position_mode", "auto")


class DeskError(ValueError):
    """A gate refused the record (fail closed)."""


@dataclass
class State:
    records: list[dict] = field(default_factory=list)
    equity: list[dict] = field(default_factory=list)
    proposals: dict[str, dict] = field(default_factory=dict)
    decisions: dict[str, dict] = field(default_factory=dict)
    sends: dict[str, dict] = field(default_factory=dict)
    results: dict[str, dict] = field(default_factory=dict)
    killed: dict | None = None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return eio.iso_utc(moment)


def _time(value: object, name: str) -> datetime:
    return eio.parse_aware_time(value, name, DeskError)


def canonical(record: dict) -> str:
    return eio.canonical_text(record, DeskError, "record cannot be stored")


def _hash(record: dict) -> str:
    return eio.sha256_hex(canonical({k: v for k, v in record.items() if k != "hash"})
                          .encode("utf-8"), upper=False)


# Sizing ---------------------------------------------------------------------

def size_contracts(notional: float, price: float, contract_value: float, lot: float) -> str:
    """Whole lots of contracts for a notional in USDT, rounded down."""
    contracts = Decimal(str(notional)) / Decimal(str(price)) / Decimal(str(contract_value))
    lots = (contracts / Decimal(str(lot))).to_integral_value(rounding=ROUND_DOWN)
    return format((lots * Decimal(str(lot))).normalize(), "f")


def order_body(proposal: dict) -> dict:
    """The exact request body a proposal implies."""
    position = "net"
    if proposal["limits"]["position_mode"] == "hedge":
        position = "long" if proposal["side"] == "buy" else "short"
    body = {"instId": proposal["inst_id"], "marginMode": proposal["limits"]["margin_mode"],
            "positionSide": position, "side": proposal["side"],
            "orderType": "limit" if proposal["limit_price"] is not None else "market",
            "size": proposal["contracts"], "clientOrderId": proposal["order_id"].replace("-", ""),
            "slTriggerPrice": _num(proposal["stop_loss"]), "slOrderPrice": "-1"}
    if proposal["limit_price"] is not None:
        body["price"] = _num(proposal["limit_price"])
    if proposal["take_profit"] is not None:
        body.update(tpTriggerPrice=_num(proposal["take_profit"]), tpOrderPrice="-1")
    return body


def _num(value: float) -> str:
    return format(Decimal(str(value)).normalize(), "f")


# Gates ------------------------------------------------------------------------

def _positive(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or value <= 0:
        raise DeskError(f"{name} must be a positive finite number")
    return float(value)


def _limits(record: dict) -> dict:
    limits = record.get("limits")
    if not isinstance(limits, dict) or set(limits) != set(LIMIT_KEYS):
        raise DeskError("the proposal must freeze the policy limits it was checked against")
    for key, cap in pol.HARD_CAPS.items():
        _positive(limits[key], key)
        if limits[key] > cap:
            raise DeskError(f"{key} above the hard cap {cap}")
    return limits


def day_start_equity(state: State, env: str, moment: datetime) -> dict | None:
    day = moment.date()
    for e in state.equity:
        if e["env"] == env and _time(e["recorded_at"], "recorded_at").date() == day:
            return e
    return None


def daily_loss(state: State, env: str, equity: dict) -> float:
    start = day_start_equity(state, env, _time(equity["recorded_at"], "recorded_at"))
    if start is None:
        return 0.0
    return max(0.0, (start["equity"] - equity["equity"]) / start["equity"])


_ELIGIBLE: dict[tuple, dict] = {}


def author_eligible(prediction_records: list[dict], author: str, auto: dict) -> dict:
    """Did this author beat every required rival with enough resolved forecasts?
    Memoized by the ledger prefix it was computed on: the answer for a prefix
    never changes, and verify recomputes it for every proposal."""
    key = (author, len(prediction_records),
           prediction_records[-1].get("hash") if prediction_records else None,
           auto["min_n"], tuple(sorted(auto["must_beat"])))
    if key not in _ELIGIBLE:
        _ELIGIBLE[key] = _author_eligible(prediction_records, author, auto)
    return _ELIGIBLE[key]


def _author_eligible(prediction_records: list[dict], author: str, auto: dict) -> dict:
    import forecast_scoring as fs  # noqa: PLC0415  (heavy; only for proposals)

    report = fs.rival(prediction_records, resamples=2000, seed=0, min_n=auto["min_n"])
    row = next((r for r in report["authors"] if r["author"] == author), None)
    verdicts = {name: (row or {}).get(name) for name in auto["must_beat"]}
    eligible = all(v is not None and v["verdict"] == "beats_rival" and v["n"] >= auto["min_n"]
                   for v in verdicts.values())
    return {"eligible": eligible, "verdicts": {k: (v or {}).get("verdict") for k, v in verdicts.items()}}


def _check_proposal(state: State, record: dict, recorded: datetime,
                    predictions: list[dict]) -> None:
    limits = _limits(record)
    if state.killed is not None:
        raise DeskError(f"kill switch is on: {state.killed['reason']}")
    oid = record.get("order_id")
    core = {k: record.get(k) for k in ("author", "question_id", "forecast_hash", "inst_id", "side",
                                        "limit_price", "leverage", "margin_fraction", "stop_loss",
                                        "take_profit", "equity_seq", "recorded_at")}
    if oid != "o-" + eio.sha256_hex(canonical(core).encode("utf-8"), upper=False)[:24]:
        raise DeskError("order_id does not match the proposal")
    if oid in state.proposals:
        raise DeskError(f"{oid} already proposed")
    author = record.get("author")
    if not isinstance(author, str) or not pl.AUTHOR.fullmatch(author):
        raise DeskError("author must be a ledger author (nathan, ia:kronos, ...)")
    forecast = next((r for r in predictions[:record.get("predictions_seq", 0)]
                     if r.get("type") == "forecast" and r.get("hash") == record.get("forecast_hash")), None)
    if forecast is None or forecast.get("author") != author \
            or forecast.get("question_id") != record.get("question_id"):
        raise DeskError("every order must come from its author's sealed forecast on that question")
    if record.get("side") not in ("buy", "sell"):
        raise DeskError("side must be buy or sell")
    leverage = record.get("leverage")
    if not isinstance(leverage, int) or isinstance(leverage, bool) or not 1 <= leverage <= limits["max_leverage"] \
            or leverage > record.get("instrument", {}).get("maxLeverage", 0):
        raise DeskError(f"leverage must be an integer from 1 to {limits['max_leverage']}")
    fraction = _positive(record.get("margin_fraction"), "margin_fraction")
    if fraction > limits["max_order_fraction"]:
        raise DeskError(f"margin above {limits['max_order_fraction']:.0%} of equity")
    reference = _positive(record.get("reference_price"), "reference_price")
    stop = _positive(record.get("stop_loss"), "stop_loss")
    buy = record["side"] == "buy"
    if (buy and stop >= reference) or (not buy and stop <= reference):
        raise DeskError("the stop-loss must be below the price to buy and above it to sell")
    if record.get("take_profit") is not None:
        tp = _positive(record["take_profit"], "take_profit")
        if (buy and tp <= reference) or (not buy and tp >= reference):
            raise DeskError("the take-profit must be on the winning side of the price")
    if record.get("limit_price") is not None:
        _positive(record["limit_price"], "limit_price")
    seq = record.get("equity_seq")
    equity = next((e for e in state.equity if e["seq"] == seq), None)
    if equity is None or equity["env"] != record.get("env"):
        raise DeskError("the proposal must cite an equity reading of the same environment")
    if (recorded - _time(equity["recorded_at"], "recorded_at")).total_seconds() > limits["equity_max_age_seconds"]:
        raise DeskError("the equity reading is too old: read it again")
    if daily_loss(state, record["env"], equity) >= limits["max_daily_loss"]:
        raise DeskError(f"daily loss limit reached ({limits['max_daily_loss']:.0%})")
    notional = equity["equity"] * fraction * leverage
    if abs(record.get("notional_usd", 0) - round(notional, 2)) > 0.011:
        raise DeskError("notional does not match equity x margin x leverage")
    price = record["limit_price"] or reference
    inst = record.get("instrument") or {}
    contracts = size_contracts(notional, price, _positive(inst.get("contractValue"), "contractValue"),
                               _positive(inst.get("lotSize"), "lotSize"))
    if record.get("contracts") != contracts:
        raise DeskError("contracts do not match the notional, price and contract size")
    if Decimal(contracts) < Decimal(str(_positive(inst.get("minSize"), "minSize"))):
        raise DeskError("order below the exchange minimum size")
    loss_at_stop = abs(price - stop) / price * notional / equity["equity"]
    if loss_at_stop > limits["max_daily_loss"]:
        raise DeskError(f"hitting the stop would lose {loss_at_stop:.1%} of equity, above the "
                        f"{limits['max_daily_loss']:.0%} daily limit")
    expected = author_eligible(predictions[:record["predictions_seq"]], author, limits["auto"])
    if record.get("auto") != expected:
        raise DeskError("auto eligibility does not match the prediction ledger at proposal time")


def _check_decision(state: State, record: dict, recorded: datetime) -> None:
    oid = record.get("order_id")
    proposal = state.proposals.get(oid)
    if proposal is None or oid in state.decisions:
        raise DeskError("decisions need a pending proposal")
    if state.killed is not None:
        raise DeskError(f"kill switch is on: {state.killed['reason']}")
    age = (recorded - _time(proposal["recorded_at"], "recorded_at")).total_seconds()
    if age > proposal["limits"]["proposal_max_age_seconds"]:
        raise DeskError("the proposal expired: the price has moved, propose again")
    decision, mode = record.get("decision"), record.get("mode")
    if decision not in ("approve", "reject") or mode not in ("human", "auto"):
        raise DeskError("decision must be approve or reject; mode human or auto")
    if mode == "human" and record.get("decided_by") != proposal["limits"]["owner"]:
        raise DeskError("only the policy owner decides by hand")
    if mode == "auto" and (decision != "approve" or not proposal["limits"]["auto"]["enabled"]
                           or not proposal["auto"]["eligible"]):
        raise DeskError("automatic approval needs auto enabled and an author who beat the rivals")
    if decision == "reject" and not str(record.get("reason") or "").strip():
        raise DeskError("a rejection needs a reason")


def _check_send(state: State, record: dict, recorded: datetime) -> None:
    oid = record.get("order_id")
    proposal, decision = state.proposals.get(oid), state.decisions.get(oid)
    if proposal is None or decision is None or decision["decision"] != "approve":
        raise DeskError("only approved orders are sent")
    if oid in state.sends:
        raise DeskError("this order was already sent")
    if state.killed is not None:
        raise DeskError(f"kill switch is on: {state.killed['reason']}")
    age = (recorded - _time(decision["recorded_at"], "recorded_at")).total_seconds()
    if age > proposal["limits"]["approval_max_age_seconds"]:
        raise DeskError("the approval expired: propose and approve again")
    if record.get("env") != proposal["env"]:
        raise DeskError("an order is sent to the environment it was proposed in")
    if record.get("body") != order_body(proposal):
        raise DeskError("the body sent must be exactly the approved order")


def _check_result(state: State, record: dict) -> None:
    oid = record.get("order_id")
    if oid not in state.sends or oid in state.results:
        raise DeskError("a result needs a send without a result")
    if not isinstance(record.get("ok"), bool) or not isinstance(record.get("response"), (dict, list, str)):
        raise DeskError("a result needs ok and the exchange's response")


def check(state: State, record: dict, predictions: list[dict]) -> None:
    kind = record.get("type")
    if kind not in TYPES:
        raise DeskError(f"type must be one of {TYPES}")
    recorded = _time(record.get("recorded_at"), "recorded_at")
    if state.records and recorded < _time(state.records[-1]["recorded_at"], "recorded_at"):
        raise DeskError("records must be in time order")
    if kind == "equity":
        if record.get("env") not in pol.STAGES:
            raise DeskError("env must be demo or live")
        _positive(record.get("equity"), "equity")
        if not str(record.get("source") or "").strip():
            raise DeskError("equity needs its source")
    elif kind in ("kill", "unkill"):
        if not str(record.get("reason") or "").strip() or not str(record.get("by") or "").strip():
            raise DeskError("the kill switch needs who and why")
        if kind == "kill" and state.killed is not None:
            raise DeskError("the kill switch is already on")
        if kind == "unkill" and state.killed is None:
            raise DeskError("the kill switch is not on")
    elif kind == "proposal":
        _check_proposal(state, record, recorded, predictions)
    elif kind == "decision":
        _check_decision(state, record, recorded)
    elif kind == "send":
        _check_send(state, record, recorded)
    else:
        _check_result(state, record)


def apply(state: State, record: dict) -> None:
    state.records.append(record)
    kind = record["type"]
    if kind == "equity":
        state.equity.append(record)
    elif kind == "kill":
        state.killed = record
    elif kind == "unkill":
        state.killed = None
    elif kind == "proposal":
        state.proposals[record["order_id"]] = record
    elif kind == "decision":
        state.decisions[record["order_id"]] = record
    elif kind == "send":
        state.sends[record["order_id"]] = record
    else:
        state.results[record["order_id"]] = record


# Storage ----------------------------------------------------------------------

def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [eio.loads_strict(line, DeskError) for line in text.split("\n") if line.strip()]


def verify(records: list[dict], predictions: list[dict]) -> State:
    state, previous = State(), GENESIS
    for i, record in enumerate(records, 1):
        if record.get("seq") != i or record.get("prev_hash") != previous:
            raise DeskError(f"record {i}: broken chain")
        if record.get("hash") != _hash(record):
            raise DeskError(f"record {i}: hash mismatch")
        try:
            check(state, {k: v for k, v in record.items() if k not in ("seq", "prev_hash", "hash")}
                  | {"seq": record["seq"]}, predictions)
        except DeskError as exc:
            raise DeskError(f"record {i}: {exc}") from None
        apply(state, record)
        previous = record["hash"]
    return state


def commit(path: Path, build: Callable[[State], dict], predictions: list[dict]) -> dict:
    """Append one record after replaying the ledger and checking it."""
    with eio.exclusive_lock(path.with_suffix(path.suffix + ".lock"), DeskError,
                            "the orders ledger is locked by another process", timeout=10):
        records = load(path)
        state = verify(records, predictions)
        body = build(state)
        body["recorded_at"] = body.get("recorded_at") or iso(now_utc())
        seq = len(records) + 1
        check(state, dict(body, seq=seq), predictions)
        record = dict(body, seq=seq, prev_hash=records[-1]["hash"] if records else GENESIS)
        record["hash"] = _hash(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical(record) + "\n")
            handle.flush()
            import os  # noqa: PLC0415
            os.fsync(handle.fileno())
        return record


def build_proposal(state: State, predictions: list[dict], policy: dict, *, author: str,
                   question_id: str, side: str, margin_fraction: float, leverage: int,
                   stop_loss: float, take_profit: float | None, limit_price: float | None,
                   reference_price: float, instrument: dict, inst_id: str, rationale: str,
                   recorded_at: str) -> dict:
    """Fill in a proposal from the latest forecast and equity; the gates decide."""
    author = pl.normalize_author(author)
    forecasts = [r for r in predictions if r.get("type") == "forecast"
                 and r.get("author") == author and r.get("question_id") == question_id]
    if not forecasts:
        raise DeskError(f"{author} has no sealed forecast on {question_id}")
    env = policy["stage"]
    equity = next((e for e in reversed(state.equity) if e["env"] == env), None)
    if equity is None:
        raise DeskError(f"no {env} equity reading yet: run `python -m desk equity`")
    notional = round(equity["equity"] * margin_fraction * leverage, 2)
    price = limit_price or reference_price
    record = {"type": "proposal", "author": author, "question_id": question_id,
              "forecast_hash": forecasts[-1]["hash"], "predictions_seq": len(predictions),
              "env": env, "inst_id": inst_id, "side": side, "limit_price": limit_price,
              "leverage": leverage, "margin_fraction": margin_fraction, "stop_loss": stop_loss,
              "take_profit": take_profit, "reference_price": reference_price,
              "equity_seq": equity["seq"], "notional_usd": notional, "instrument": instrument,
              "contracts": size_contracts(equity["equity"] * margin_fraction * leverage, price,
                                          instrument["contractValue"], instrument["lotSize"]),
              "limits": {k: policy[k] for k in LIMIT_KEYS},
              "auto": author_eligible(predictions, author, policy["auto"]),
              "rationale": (rationale or "").strip(), "recorded_at": recorded_at}
    core = {k: record.get(k) for k in ("author", "question_id", "forecast_hash", "inst_id", "side",
                                        "limit_price", "leverage", "margin_fraction", "stop_loss",
                                        "take_profit", "equity_seq", "recorded_at")}
    record["order_id"] = "o-" + eio.sha256_hex(canonical(core).encode("utf-8"), upper=False)[:24]
    return record
