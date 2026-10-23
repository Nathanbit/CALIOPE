"""Compuerta pre-operación: GO / NO_GO / REVIEW_REQUIRED antes de abrir una orden manual.

Uso:
  python tools/discipline/pre_trade_gate.py --plan plan.json [--simplefx-csv cerradas.csv]
         [--rules reglas.json] [--as-of 2026-10-20T19:58:00-05:00] [--journal state/journal/pre_trade.jsonl]

No envía órdenes ni consulta brokers. Códigos de salida: 0 GO, 2 NO_GO, 3 REVIEW_REQUIRED,
1 error de entrada. Un dato faltante o inválido nunca se trata como aprobado.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rules import (DEFAULT_POINT_VALUE, Rules, circuit_breaker, day_slice, finite_number, in_windows,  # noqa: E402
                   load_trades)

CHECKLIST = {
    "range_marked": "rango de apertura marcado",
    "breakout_close_confirmed": "vela de ruptura cerrada fuera del rango",
    "retest_confirmed": "retesteo confirmado con cierre",
    "news_clear": "sin noticia de alto impacto en los próximos minutos",
}
EXIT = {"GO": 0, "NO_ACTIONABLE_ORDERS": 0, "NO_GO": 2, "REVIEW_REQUIRED": 3}
ACTIONABLE = {"ENTRY", "ENTRY_READY", "MANUAL_ORDER"}


def evaluate(plan: dict, rules: Rules, trades=None, as_of: datetime | None = None) -> dict:
    tz = ZoneInfo(rules.tz)
    now = (as_of or datetime.now(tz)).astimezone(tz)
    intent = str(plan.get("intent", "ENTRY")).upper()
    if intent not in ACTIONABLE:
        return {"evidence_kind": "derivation", "decision": "NO_ACTIONABLE_ORDERS", "as_of": now.isoformat(),
                "blocking_reasons": [], "review_reasons": [], "metrics": {}, "plan": plan,
                "rules": rules.to_dict(), "note": f"intención {intent}: se registra, no autoriza ninguna orden."}
    blocks: list[str] = []
    review: list[str] = []
    order = plan.get("order") or {}
    account = plan.get("account") or {}
    checklist = plan.get("checklist") or {}

    side = str(order.get("side", "")).upper()
    if side not in {"BUY", "SELL"}:
        review.append("side debe ser BUY o SELL")
    entry, stop, target = (finite_number(order.get(k)) for k in ("entry", "stop", "target"))
    lots = finite_number(order.get("lots"))
    equity = finite_number(account.get("equity"))
    symbol = str(order.get("symbol", "")).upper()
    pv = finite_number(order.get("value_per_point_per_lot")) or DEFAULT_POINT_VALUE.get(symbol)

    if stop is None:
        blocks.append("sin stop definido: no se entra sin stop")
    for name, v in (("entry", entry), ("target", target)):
        if v is None:
            review.append(f"falta {name}")
    if lots is None or lots <= 0:
        review.append("lots inválido")
    if equity is None or equity <= 0:
        review.append("equity inválida")
    if pv is None:
        review.append(f"falta value_per_point_per_lot para {symbol or 'el símbolo'}")

    metrics: dict = {}
    if None not in (entry, stop) and side in {"BUY", "SELL"}:
        sign = 1 if side == "BUY" else -1
        risk_pts = (entry - stop) * sign
        if risk_pts <= 0:
            blocks.append("el stop está del lado equivocado de la entrada")
        else:
            metrics["risk_points"] = round(risk_pts, 4)
            if target is not None:
                reward_pts = (target - entry) * sign
                rr = reward_pts / risk_pts
                metrics["reward_points"] = round(reward_pts, 4)
                metrics["reward_risk"] = round(rr, 2)
                if reward_pts <= 0:
                    blocks.append("el objetivo está del lado equivocado de la entrada")
                elif rr < rules.min_reward_risk:
                    blocks.append(f"beneficio/riesgo {rr:.2f} < {rules.min_reward_risk}")
            if None not in (lots, equity, pv) and lots > 0 and equity > 0:
                risk_usd = risk_pts * lots * pv
                risk_pct = risk_usd / equity * 100
                metrics.update(risk_usd=round(risk_usd, 4), risk_pct=round(risk_pct, 2),
                               max_lots_at_rule=round(rules.max_risk_pct / 100 * equity / (risk_pts * pv), 4))
                if risk_pct > rules.max_risk_pct + 1e-9:
                    blocks.append(f"riesgo {risk_pct:.2f}% > máximo {rules.max_risk_pct}%"
                                  f" (lotes máximos ≈ {metrics['max_lots_at_rule']})")

    for key, label in CHECKLIST.items():
        value = checklist.get(key)
        if value is None:
            review.append(f"checklist sin responder: {label}")
        elif value is not True:
            blocks.append(f"checklist no cumplido: {label}")

    if not in_windows(now, rules.session_windows):
        blocks.append(f"fuera de las ventanas de sesión ({now:%H:%M} {rules.tz})")

    breaker = circuit_breaker(trades, equity, rules, now)
    metrics["circuit_breaker"] = breaker
    if breaker["state"] == "UNKNOWN":
        review.append("sin historial del día: no se pueden verificar límites ni venganza")
    else:
        blocks.extend(breaker["reasons"])
        today = day_slice(trades, now.date().isoformat(), rules.tz)
        today = today[today["exit_time"] <= now]
        open_now = int(plan.get("open_positions", 0) or 0)
        n = len(today) + open_now
        metrics.update(trades_today=n, losing_streak=breaker["losing_streak"],
                       day_pnl=breaker["periods"]["day"]["pnl"])
        if n >= rules.max_trades_per_day:
            blocks.append(f"ya hiciste {n} operaciones hoy (máximo {rules.max_trades_per_day})")
        losses = today[today["pnl"] < 0]
        if len(losses):
            mins = (now - losses["exit_time"].iloc[-1].tz_convert(tz)).total_seconds() / 60
            if mins < rules.revenge_cooldown_min:
                blocks.append(f"última pérdida hace {mins:.0f} min: espera {rules.revenge_cooldown_min:.0f} min")

    decision = "NO_GO" if blocks else ("REVIEW_REQUIRED" if review else "GO")
    return {
        "evidence_kind": "derivation",
        "decision": decision,
        "as_of": now.isoformat(),
        "blocking_reasons": blocks,
        "review_reasons": review,
        "metrics": metrics,
        "plan": plan,
        "rules": rules.to_dict(),
        "note": "Compuerta advisory: no coloca órdenes. GO solo significa que el plan cumple tus reglas.",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plan", required=True)
    ap.add_argument("--simplefx-csv")
    ap.add_argument("--rules")
    ap.add_argument("--as-of")
    ap.add_argument("--journal")
    args = ap.parse_args(argv)
    try:
        rules = Rules.load(args.rules)
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        trades = load_trades(args.simplefx_csv, rules.tz) if args.simplefx_csv else None
        as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
        if as_of is not None and as_of.tzinfo is None:
            raise ValueError("--as-of necesita zona horaria")
    except (OSError, ValueError, KeyError) as exc:
        print(f"ENTRADA INVÁLIDA: {exc}", file=sys.stderr)
        return 1
    result = evaluate(plan, rules, trades, as_of)
    if args.simplefx_csv:
        result["source"] = {"file": Path(args.simplefx_csv).name,
                            "sha256": hashlib.sha256(Path(args.simplefx_csv).read_bytes()).hexdigest()}
    if args.journal:
        j = Path(args.journal)
        j.parent.mkdir(parents=True, exist_ok=True)
        with j.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return EXIT[result["decision"]]


if __name__ == "__main__":
    raise SystemExit(main())
