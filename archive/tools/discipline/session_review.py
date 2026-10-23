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
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rules import DEFAULT_POINT_VALUE, Rules, day_slice, in_windows, load_trades, losing_streak  # noqa: E402

PRICE_TOL = 1e-6
OPENING_RANGE_MIN = 15

# Etiquetas de comportamiento: hipótesis con evidencia, nunca diagnósticos.
# Inspiradas en el modelo de trade-performance-coach (MIT), redactadas de nuevo.
TAGS = {
    "premature_exit": ("Posible salida prematura",
                       "¿Qué regla cumplía ese cierre, o lo decidió la incomodidad?",
                       "Con el stop en ganancia, no cerrar a mano: deciden el stop o el TP."),
    "revenge_trade": ("Posible operación de revancha",
                      "¿Habrías tomado esta operación si la anterior hubiera ganado?",
                      "Tras una pérdida, esperar el enfriamiento completo y pasar la compuerta antes de entrar."),
    "fomo_entry": ("Posible entrada por miedo a perderse el movimiento",
                   "¿Qué sabías al entrar que no habrías sabido esperando el rango de 15 minutos?",
                   "No entrar en los primeros 15 minutos de la ventana: primero se marca el rango."),
    "size_creep": ("Tamaño por encima de lo habitual",
                   "¿Cómo se habría visto esta operación con tu tamaño normal?",
                   "Usar el lote que da la compuerta; nunca más."),
    "overconfidence_after_winner": ("Posible exceso de confianza tras una ganadora",
                                    "¿La ganancia anterior cambió tu tamaño o tu exigencia con la señal?",
                                    "Después de una ganadora, el siguiente lote no sube."),
    "rule_drift": ("Deriva de reglas",
                   "¿Qué regla vas a cumplir sí o sí en la próxima sesión?",
                   "Próxima sesión: máximo 2 operaciones y cada una con compuerta GO."),
}


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
    rows, violations, raw = [], [], []
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
        raw.append({"n": i, "entry": entry_local, "exit": r["exit_time"].tz_convert(tz),
                    "size": float(r["size"]), "pnl": float(r["pnl"]), "flags": flags})
    pnl = [x["pnl"] for x in rows]
    coach = _coach(raw, violations, rules)
    return {
        "coach": coach,
        "evidence_kind": "derivation",
        "date": day, "tz": rules.tz,
        "trades": len(rows), "net_pnl": round(sum(pnl), 4),
        "wins": sum(p > 0 for p in pnl), "losses": sum(p < 0 for p in pnl),
        "rule_violations": violations,
        "adherence": "cumplidas" if not violations else f"{len(violations)} incumplimiento(s)",
        "detail": rows,
        "note": "close_reason_inferred es una inferencia comparando el precio de cierre con TP/SL del export.",
    }


def _window_start(moment, windows):
    """Inicio de la ventana de sesión que contiene `moment` (o None)."""
    for start, end in windows:
        if in_windows(moment, [[start, end]]):
            begin = datetime.combine(moment.date(), datetime.strptime(start, "%H:%M").time(), moment.tzinfo)
            return begin - timedelta(days=1) if begin > moment else begin  # ventana que cruza medianoche
    return None


def _coach(raw: list[dict], violations: list[str], rules: Rules) -> dict:
    found: dict[str, list[str]] = {}
    conf: dict[str, str] = {}

    def tag(name, evidence, confidence):
        found.setdefault(name, []).append(evidence)
        order = {"low": 0, "medium": 1, "high": 2}
        if order[confidence] > order.get(conf.get(name, "low"), -1) or name not in conf:
            conf[name] = confidence

    sizes = sorted(x["size"] for x in raw)
    median = sizes[len(sizes) // 2] if sizes else 0
    by_exit = sorted(raw, key=lambda x: x["exit"])
    for x in raw:
        if any("ganadora protegida" in f for f in x["flags"]):
            tag("premature_exit", f"#{x['n']} cerrada a mano con el stop en ganancia antes del TP", "medium")
        prev = [p for p in by_exit if p["exit"] <= x["entry"]]
        if prev:
            last = prev[-1]
            gap = (x["entry"] - last["exit"]).total_seconds() / 60
            if last["pnl"] < 0 and gap < rules.revenge_cooldown_min:
                bigger = x["size"] > last["size"]
                tag("revenge_trade", f"#{x['n']} abierta {gap:.0f} min después de una pérdida"
                    + (" y con más tamaño" if bigger else ""), "high" if bigger else "medium")
            if last["pnl"] > 0 and last["size"] > 0 and x["size"] >= 2 * last["size"]:
                tag("overconfidence_after_winner", f"#{x['n']} con {x['size']} lotes tras ganar con {last['size']}", "medium")
        start = _window_start(x["entry"], rules.session_windows)
        if start is not None and 0 <= (x["entry"] - start).total_seconds() / 60 < OPENING_RANGE_MIN:
            tag("fomo_entry", f"#{x['n']} abierta a las {x['entry']:%H:%M}, antes de cerrar el rango de "
                f"{OPENING_RANGE_MIN} min", "low")
        if median and x["size"] >= 2 * median and len(raw) >= 3:
            tag("size_creep", f"#{x['n']} con {x['size']} lotes (mediana del día {median})", "medium")
    if len(violations) >= 3:
        tag("rule_drift", f"{len(violations)} incumplimientos de reglas en la sesión", "high")

    tags = [{"tag": k, "label": TAGS[k][0], "confidence": conf[k], "evidence": v,
             "question": TAGS[k][1]} for k, v in found.items()]
    losses = sum(x["pnl"] < 0 for x in raw)
    strong = [t for t in tags if t["confidence"] != "low"]
    if ("revenge_trade" in found and losses >= 2) or len(violations) >= 5:
        verdict = "COOL_DOWN"
    elif violations:
        verdict = "RULE_VIOLATION"
    elif strong:
        verdict = "REVIEW_REQUIRED"
    elif tags:
        verdict = "WARN"
    else:
        verdict = "OK"
    next_rules = [TAGS[t["tag"]][2] for t in tags]
    if verdict == "COOL_DOWN":
        next_rules.insert(0, "Próxima sesión solo de revisión: sin operar hasta repasar este informe.")
    return {
        "verdict": verdict,
        "behavior_tags": tags or [{"tag": "no_pattern_detected", "label": "Sin patrón detectado",
                                   "confidence": "low", "evidence": [], "question": None}],
        "next_session_rules": next_rules,
        "human_decision_gate": {"default": "journal_only",
                                "options": ["accept_rules", "modify_rules", "defer", "journal_only"]},
        "disclaimer": "Hipótesis basadas en el registro de operaciones; no son diagnósticos ni consejos de inversión.",
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
    c = rep["coach"]
    lines += ["", f"## Coach — veredicto: {c['verdict']}", ""]
    for t in c["behavior_tags"]:
        if t["tag"] == "no_pattern_detected":
            lines.append("- Sin patrón de comportamiento detectado.")
            continue
        lines.append(f"- **{t['label']}** (confianza {t['confidence']}): " + "; ".join(t["evidence"]))
        lines.append(f"  - Pregunta: {t['question']}")
    if c["next_session_rules"]:
        lines += ["", "**Reglas propuestas para la próxima sesión** (tú decides: aceptar, modificar, aplazar o solo registrar):"]
        lines += [f"{i}. {r}" for i, r in enumerate(c["next_session_rules"], 1)]
    lines += ["", f"_{c['disclaimer']}_", "", f"_{rep['note']}_", ""]
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
