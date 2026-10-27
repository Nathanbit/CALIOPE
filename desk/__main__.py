"""CALIOPE desk command line. Run on the person's computer.

    python -m desk status
    python -m desk equity
    python -m desk propose --author nathan --question q-... --side buy --margin 0.1 --leverage 3 --sl 79000 [--tp 83000] [--limit 80000]
    python -m desk approve --order o-...      (interactive: type the confirmation)
    python -m desk reject --order o-... --reason "..."
    python -m desk submit --order o-...
    python -m desk kill --reason "..." / unkill --reason "..."
    python -m desk verify

Demo by default (BloFin demo trading, fake money, real API). Real money needs
two keys: "stage": "live" in desk/policy.json AND CALIOPE_DESK_LIVE=si in the
environment of that terminal.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from desk import blofin
from desk import orders as od
from desk import policy as pol

ROOT = pol.ROOT
LIVE_SWITCH = ("CALIOPE_DESK_LIVE", "si")


def _client(policy: dict) -> blofin.Client:
    if policy["stage"] == "live":
        if os.environ.get(LIVE_SWITCH[0]) != LIVE_SWITCH[1]:
            raise od.DeskError(f"stage is live but {LIVE_SWITCH[0]}={LIVE_SWITCH[1]} is not set in "
                               "this terminal: real money needs both keys")
        return blofin.Client(blofin.LIVE)
    return blofin.Client(blofin.DEMO)


def _paths(policy: dict) -> tuple[Path, list[dict]]:
    ledger = ROOT / policy["ledger"]
    predictions = pl_load(ROOT / policy["predictions"])
    return ledger, predictions


def pl_load(path: Path) -> list[dict]:
    import prediction_ledger as pl  # noqa: PLC0415
    records = pl.load(path)
    pl.verify(records)
    return records


def _summary(p: dict) -> str:
    kind = f"límite {p['limit_price']}" if p["limit_price"] else "a mercado"
    lines = [f"Orden {p['order_id']} ({p['env'].upper()})",
             f"  {p['side'].upper()} {p['contracts']} contratos de {p['inst_id']} {kind}",
             f"  margen {p['margin_fraction']:.0%} del capital, apalancamiento {p['leverage']}x, "
             f"nocional {p['notional_usd']:,.2f} USDT",
             f"  stop-loss {p['stop_loss']}" + (f", take-profit {p['take_profit']}" if p["take_profit"] else ""),
             f"  autor {p['author']} · pregunta {p['question_id']}",
             f"  motivo: {p['rationale'] or '-'}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m desk", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("equity")
    sub.add_parser("verify")
    p = sub.add_parser("propose")
    p.add_argument("--author", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--side", required=True, choices=("buy", "sell"))
    p.add_argument("--margin", required=True, type=float, help="share of equity used as margin, e.g. 0.1")
    p.add_argument("--leverage", required=True, type=int)
    p.add_argument("--sl", required=True, type=float, help="stop-loss trigger price (required)")
    p.add_argument("--tp", type=float)
    p.add_argument("--limit", type=float, help="limit price; market order if omitted")
    p.add_argument("--inst", default="BTC-USDT")
    p.add_argument("--rationale", default="")
    for name in ("approve", "submit"):
        sub.add_parser(name).add_argument("--order", required=True)
    p = sub.add_parser("reject")
    p.add_argument("--order", required=True)
    p.add_argument("--reason", required=True)
    for name in ("kill", "unkill"):
        sub.add_parser(name).add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    try:
        policy, policy_sha = pol.load()
        ledger, predictions = _paths(policy)
        out = run(args, policy, ledger, predictions)
        out["policy_sha256"] = policy_sha[:16]
    except (od.DeskError, pol.PolicyError, blofin.BlofinError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **out}, ensure_ascii=False, indent=2, default=str))
    return 0


def run(args: argparse.Namespace, policy: dict, ledger: Path, predictions: list[dict],
        client: blofin.Client | None = None, ask: object = input) -> dict:
    command = args.command
    if command in ("status", "verify"):
        state = od.verify(od.load(ledger), predictions)
        pending = [oid for oid in state.proposals if oid not in state.decisions]
        return {"stage": policy["stage"], "records": len(state.records),
                "kill_switch": state.killed and state.killed["reason"],
                "last_equity": state.equity[-1] if state.equity else None,
                "pending_approval": pending,
                "approved_not_sent": [o for o, d in state.decisions.items()
                                      if d["decision"] == "approve" and o not in state.sends],
                "sent_without_answer": [o for o in state.sends if o not in state.results],
                "unknown_outcome": [o for o, r in state.results.items()
                                    if isinstance(r["response"], dict) and r["response"].get("unknown")]}
    if command in ("kill", "unkill"):
        if command == "unkill":
            _confirm(ask, "APAGADO OFF", "Reactivar la mesa")
        rec = od.commit(ledger, lambda s: {"type": command, "reason": args.reason,
                                            "by": policy["owner"]}, predictions)
        return {"record": rec["seq"], "kill_switch": command == "kill"}
    client = client or _client(policy)
    env = policy["stage"]
    if command == "equity":
        value = client.equity()
        rec = od.commit(ledger, lambda s: {"type": "equity", "env": env, "equity": value,
                                            "source": f"BloFin {env} /api/v1/account/balance totalEquity"},
                        predictions)
        state = od.verify(od.load(ledger), predictions)
        loss = od.daily_loss(state, env, rec)
        if loss >= policy["max_daily_loss"] and state.killed is None:
            od.commit(ledger, lambda s: {"type": "kill", "by": "desk",
                                         "reason": f"pérdida diaria {loss:.1%} >= límite "
                                                   f"{policy['max_daily_loss']:.0%}"}, predictions)
        return {"equity": value, "env": env, "daily_loss": round(loss, 4)}
    if command == "propose":
        if args.inst not in policy["instruments"]:
            raise od.DeskError(f"{args.inst} is not in the policy instruments")
        instrument = client.instrument(args.inst)
        price = client.last_price(args.inst)
        rec = od.commit(ledger, lambda s: od.build_proposal(
            s, predictions, policy, author=args.author, question_id=args.question, side=args.side,
            margin_fraction=args.margin, leverage=args.leverage, stop_loss=args.sl,
            take_profit=args.tp, limit_price=args.limit, reference_price=price,
            instrument=instrument, inst_id=args.inst, rationale=args.rationale,
            recorded_at=od.iso(od.now_utc())), predictions)
        if rec["auto"]["eligible"] and policy["auto"]["enabled"]:
            od.commit(ledger, lambda s: {"type": "decision", "order_id": rec["order_id"],
                                         "decision": "approve", "mode": "auto", "decided_by": "desk",
                                         "reason": "autor probado: " + json.dumps(rec["auto"]["verdicts"])},
                      predictions)
            return {"order": rec["order_id"], "approved": "auto", "summary": _summary(rec)}
        return {"order": rec["order_id"], "approved": False, "summary": _summary(rec),
                "next": f"python -m desk approve --order {rec['order_id']}"}
    state = od.verify(od.load(ledger), predictions)
    proposal = state.proposals.get(args.order)
    if proposal is None:
        raise od.DeskError(f"unknown order {args.order}")
    if command == "approve":
        print(_summary(proposal), file=sys.stderr)
        _confirm(ask, f"APRUEBO {args.order}", "Aprobar esta orden")
        od.commit(ledger, lambda s: {"type": "decision", "order_id": args.order, "decision": "approve",
                                     "mode": "human", "decided_by": policy["owner"], "reason": ""},
                  predictions)
        return {"order": args.order, "approved": True,
                "next": f"python -m desk submit --order {args.order} (antes de "
                        f"{policy['approval_max_age_seconds']} s)"}
    if command == "reject":
        od.commit(ledger, lambda s: {"type": "decision", "order_id": args.order, "decision": "reject",
                                     "mode": "human", "decided_by": policy["owner"],
                                     "reason": args.reason}, predictions)
        return {"order": args.order, "rejected": True}
    # submit: leverage first, then seal the exact body, then send, then seal the answer
    if proposal["env"] != env:
        raise od.DeskError(f"the order was proposed for {proposal['env']}, the policy is {env}")
    leverage_answer = client.set_leverage(proposal["inst_id"], proposal["leverage"],
                                          proposal["limits"]["margin_mode"])
    body = od.order_body(proposal)
    od.commit(ledger, lambda s: {"type": "send", "order_id": args.order, "env": env, "body": body,
                                 "leverage_answer": leverage_answer}, predictions)
    try:
        answer, ok = client.place_order(body), True
    except blofin.BlofinError as exc:
        answer, ok = {"error": str(exc), "code": exc.code, "response": exc.response}, False
    except OSError as exc:  # the connection broke: the order may or may not have arrived
        answer, ok = {"error": f"connection lost: {exc}", "unknown": True,
                      "check": f"busca clientOrderId {body['clientOrderId']} en BloFin"}, False
    od.commit(ledger, lambda s: {"type": "result", "order_id": args.order, "ok": ok,
                                 "response": answer}, predictions)
    return {"order": args.order, "sent": True, "accepted": ok, "response": answer}


def _confirm(ask, phrase: str, action: str) -> None:
    """A person types the phrase at a terminal; nothing else approves."""
    if ask is input and not sys.stdin.isatty():
        raise od.DeskError("approval is interactive only: run it yourself in a terminal")
    typed = ask(f"{action}: escribe exactamente «{phrase}» y Enter: ")
    if typed.strip() != phrase:
        raise od.DeskError("confirmation did not match: nothing was done")


if __name__ == "__main__":
    sys.exit(main())
