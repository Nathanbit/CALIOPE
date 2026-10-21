"""Revisión de una sesión: ¿se cumplieron las reglas? (post-mortem de ejecución humana).

Uso:
  python tools/discipline/session_review.py --simplefx-csv cerradas.csv [--date 2026-10-20]
         [--rules reglas.json] [--out state/reviews/sesion-2026-10-20.md]

Separa hechos (operaciones del export) de inferencias (p. ej. "cerrada a mano").
No usa red ni brokers.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rules import DEFAULT_POINT_VALUE, Rules, day_slice, in_windows, load_trades, losing_streak  # noqa: E402

PRICE_TOL = 1e-6


def _close_reason(row) -> str:
    """Inferencia: el export no dice si cerró el stop, el TP o la persona."""
    exit_, tp, sl = row["exit"], row["take_profit"], row["stop_loss"]
    tol = max(PRICE_TOL, abs(exit_) * 2e-5)
    if tp == tp and abs(exit_ - tp) <= tol:
        return "take_profit"
    if sl == sl and abs(exit_ - sl) <= tol:
        return "stop_loss"
    return "manual"


def review(trades, day: str, rules: Rules) -> dict:
    tz = ZoneInfo(rules.tz)
    t = day_slice(trades, day, rules.tz).copy()
    rows, violations = [], []
    streak_at_entry = 0
    pnls_so_far: list[float] = []
    for i, (_, r) in enumerate(t.iterrows(), 1):
        entry_local = r["entry_time"].tz_convert(tz)
        sign = 1 if r["side"] == "long" else -1
        reason = _close_reason(r)
        stop_in_profit = r["stop_loss"] == r["stop_loss"] and (r["stop_loss"] - r["entry"]) * sign > 0
        flags = []
        if i > rules.max_trades_per_day:
            flags.append(f"operación {i} supera el máximo de {rules.max_trades_per_day}")
        if not in_windows(entry_local, rules.session_windows):
            flags.append("entrada fuera de las ventanas de sesión")
        if r["stop_loss"] != r["stop_loss"]:
            flags.append("sin stop")
        streak_at_entry = losing_streak(pnls_so_far)
        if streak_at_entry >= rules.max_consecutive_losses:
            flags.append(f"entrada tras {streak_at_entry} pérdidas seguidas")
        if reason == "manual" and r["pnl"] > 0 and stop_in_profit:
            flags.append("ganadora protegida cerrada a mano antes del TP")
        violations.extend(f"#{i} {f}" for f in flags)
        pv = DEFAULT_POINT_VALUE.get(r["symbol"])
        rows.append({
            "n": i, "symbol": r["symbol"], "side": r["side"], "size": float(r["size"]),
            "entry_time": entry_local.strftime("%H:%M"), "exit_time": r["exit_time"].tz_convert(tz).strftime("%H:%M"),
            "entry": float(r["entry"]), "exit": float(r["exit"]), "pnl": float(r["pnl"]),
            "close_reason_inferred": reason, "stop_in_profit_at_close": bool(stop_in_profit),
            "tp_points_left": (round(float((r["take_profit"] - r["exit"]) * sign), 2)
                               if reason == "manual" and r["take_profit"] == r["take_profit"] else None),
            "tp_value_left_usd": (round(float((r["take_profit"] - r["exit"]) * sign * r["size"] * pv), 4)
                                  if pv and reason == "manual" and r["take_profit"] == r["take_profit"] else None),
            "flags": flags,
        })
        pnls_so_far.append(float(r["pnl"]))
    pnl = [x["pnl"] for x in rows]
    return {
        "evidence_kind": "derivation",
        "date": day, "tz": rules.tz,
        "trades": len(rows), "net_pnl": round(sum(pnl), 4),
        "wins": sum(p > 0 for p in pnl), "losses": sum(p < 0 for p in pnl),
        "rule_violations": violations,
        "adherence": "cumplidas" if not violations else f"{len(violations)} incumplimiento(s)",
        "detail": rows,
        "note": "close_reason_inferred es una inferencia comparando el precio de cierre con TP/SL del export.",
    }


def to_markdown(rep: dict) -> str:
    lines = [f"# Revisión de sesión — {rep['date']}", "",
             f"**Operaciones:** {rep['trades']} · **Resultado neto:** {rep['net_pnl']:+.2f} USDT · "
             f"**Ganadoras/perdedoras:** {rep['wins']}/{rep['losses']} · **Reglas:** {rep['adherence']}", "",
             "| # | Símbolo | Lado | Lotes | Entrada | Salida | P&L | Cierre (inferido) | Avisos |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rep["detail"]:
        lines.append(f"| {r['n']} | {r['symbol']} | {r['side']} | {r['size']} | {r['entry_time']} @ {r['entry']} | "
                     f"{r['exit_time']} @ {r['exit']} | {r['pnl']:+.2f} | {r['close_reason_inferred']} | "
                     f"{'; '.join(r['flags']) or '—'} |")
    left = [r for r in rep["detail"] if r["tp_value_left_usd"]]
    if left:
        lines += ["", "**Hasta el TP faltaban** (inferencia; no significa que lo hubiera alcanzado): " +
                  ", ".join(f"#{r['n']} {r['tp_points_left']} pts ≈ {r['tp_value_left_usd']:.2f} USDT" for r in left)]
    lines += ["", f"_{rep['note']}_", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--simplefx-csv", required=True)
    ap.add_argument("--date")
    ap.add_argument("--rules")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    rules = Rules.load(args.rules)
    day = args.date or datetime.now(ZoneInfo(rules.tz)).date().isoformat()
    rep = review(load_trades(args.simplefx_csv, rules.tz), day, rules)
    md = to_markdown(rep)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        out.with_suffix(".json").write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
