import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "discipline"))
HAS_PANDAS = importlib.util.find_spec("pandas") is not None

HEADER = "﻿Símbolo,ID de la orden,Lado,Tamaño,Hora de Apertura,Hora de Cierre,Precio de Apertura,Precio de cierre,Take Profit,Stop Loss,Ganancia (USDT)\n"
ROWS = [
    # pérdida a las 19:20 local
    "US100,1,BUY,0.01,20.10.2026 19:05:00,20.10.2026 19:20:00,29900,29860,30000,29860,-0.40",
    # ganadora protegida cerrada a mano (stop en ganancia, salida distinta de TP/SL)
    "US100,2,BUY,0.01,20.10.2026 19:30:00,20.10.2026 19:40:00,29893.1,29930.8,30005.7,29924,0.38",
]
PLAN = {
    "account": {"equity": 45.87}, "open_positions": 0,
    "order": {"symbol": "US100", "side": "BUY", "entry": 29972, "stop": 29930, "target": 30056, "lots": 0.05},
    "checklist": {"range_marked": True, "breakout_close_confirmed": True, "retest_confirmed": True, "news_clear": True},
}


def at(hhmm):
    return datetime.fromisoformat(f"2026-10-20T{hhmm}:00-05:00")


@unittest.skipUnless(HAS_PANDAS, "pandas no instalado")
class DisciplineTests(unittest.TestCase):
    def setUp(self):
        import pre_trade_gate
        import rules
        import session_review
        self.gate, self.rules, self.review = pre_trade_gate, rules, session_review
        self.tmp = tempfile.TemporaryDirectory()
        self.csv = Path(self.tmp.name) / "sfx.csv"
        self.csv.write_text(HEADER + "\n".join(ROWS) + "\n", encoding="utf-8")
        self.trades = rules.load_trades(self.csv, "America/Guayaquil")

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, **order):
        p = json.loads(json.dumps(PLAN))
        p["order"].update(order)
        return p

    def test_clean_plan_in_window_is_go(self):
        r = self.gate.evaluate(self.plan(), self.rules.Rules(), self.trades, at("19:55"))
        self.assertEqual(r["decision"], "GO", r["blocking_reasons"] + r["review_reasons"])
        self.assertAlmostEqual(r["metrics"]["risk_pct"], 4.58, places=2)

    def test_oversized_and_poor_reward_risk_block(self):
        r = self.gate.evaluate(self.plan(lots=0.1, target=30055), self.rules.Rules(), self.trades, at("19:55"))
        self.assertEqual(r["decision"], "NO_GO")
        text = " ".join(r["blocking_reasons"])
        self.assertIn("riesgo", text)
        self.assertIn("beneficio/riesgo 1.98", text)

    def test_missing_stop_and_unanswered_checklist(self):
        p = self.plan(stop=None)
        del p["checklist"]["retest_confirmed"]
        r = self.gate.evaluate(p, self.rules.Rules(), self.trades, at("19:55"))
        self.assertEqual(r["decision"], "NO_GO")
        self.assertTrue(any("sin stop" in b for b in r["blocking_reasons"]))
        self.assertTrue(any("retesteo" in x for x in r["review_reasons"]))

    def test_revenge_window_and_session_window(self):
        r = self.gate.evaluate(self.plan(), self.rules.Rules(), self.trades.iloc[:1], at("19:25"))
        self.assertTrue(any("última pérdida" in b for b in r["blocking_reasons"]))
        r = self.gate.evaluate(self.plan(), self.rules.Rules(), self.trades, at("16:00"))
        self.assertTrue(any("fuera de las ventanas" in b for b in r["blocking_reasons"]))

    def test_trade_limit_counts_open_positions(self):
        p = self.plan()
        p["open_positions"] = 1
        r = self.gate.evaluate(p, self.rules.Rules(), self.trades, at("19:55"))
        self.assertTrue(any("3 operaciones" in b for b in r["blocking_reasons"]))

    def test_without_history_requires_review(self):
        r = self.gate.evaluate(self.plan(), self.rules.Rules(), None, at("19:55"))
        self.assertEqual(r["decision"], "REVIEW_REQUIRED")

    def test_unknown_symbol_needs_point_value(self):
        r = self.gate.evaluate(self.plan(symbol="EURUSD"), self.rules.Rules(), self.trades, at("19:55"))
        self.assertEqual(r["decision"], "REVIEW_REQUIRED")

    def test_session_review_flags_manual_close_of_protected_winner(self):
        rep = self.review.review(self.trades, "2026-10-20", self.rules.Rules())
        self.assertEqual(rep["trades"], 2)
        second = rep["detail"][1]
        self.assertEqual(second["close_reason_inferred"], "manual")
        self.assertIn("ganadora protegida cerrada a mano antes del TP", second["flags"])
        self.assertAlmostEqual(second["tp_points_left"], 74.9, places=1)
        self.assertEqual(rep["detail"][0]["close_reason_inferred"], "stop_loss")
        self.assertIn("Revisión de sesión", self.review.to_markdown(rep))

    def test_rules_file_rejects_unknown_keys(self):
        f = Path(self.tmp.name) / "r.json"
        f.write_text(json.dumps({"max_risk_pct": 2, "yolo": True}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.rules.Rules.load(f)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAS_PANDAS, "pandas no instalado")
class CircuitBreakerAndCoachTests(unittest.TestCase):
    def setUp(self):
        import pre_trade_gate
        import rules
        import session_review
        self.gate, self.rules, self.review = pre_trade_gate, rules, session_review
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def trades(self, rows):
        p = Path(self.tmp.name) / "t.csv"
        p.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
        return self.rules.load_trades(p, "America/Guayaquil")

    def row(self, n, day, t_in, t_out, size, pnl, entry="29900", exit_="29890", tp="30000", sl="29850"):
        return f"US100,{n},BUY,{size},{day}.10.2026 {t_in}:00,{day}.10.2026 {t_out}:00,{entry},{exit_},{tp},{sl},{pnl}"

    def test_weekly_halt_releases_next_monday(self):
        t = self.trades([self.row(1, "19", "09:00", "09:10", 0.01, -6.0),
                         self.row(2, "20", "09:00", "09:10", 0.01, -6.0)])
        br = self.rules.circuit_breaker(t, 40.0, self.rules.Rules(), at("19:55"))
        self.assertEqual(br["state"], "HALTED")
        self.assertTrue(any("semanal" in r for r in br["reasons"]))
        self.assertTrue(br["release_at"].startswith("2026-10-26T00:00"))

    def test_streak_is_cooldown_not_halt_and_expires(self):
        t = self.trades([self.row(1, "20", "09:00", "09:10", 0.01, -0.1),
                         self.row(2, "20", "09:20", "09:30", 0.01, -0.1)])
        br = self.rules.circuit_breaker(t, 45.0, self.rules.Rules(), at("19:55"))
        self.assertEqual(br["state"], "COOLDOWN")
        self.assertTrue(br["release_at"].startswith("2026-10-20T21:30"))
        later = datetime.fromisoformat("2026-10-20T21:31:00-05:00")
        self.assertEqual(self.rules.circuit_breaker(t, 45.0, self.rules.Rules(), later)["state"], "TRADING_ALLOWED")

    def test_watchlist_intent_is_not_an_order(self):
        p = json.loads(json.dumps(PLAN))
        p["intent"] = "WATCHLIST"
        r = self.gate.evaluate(p, self.rules.Rules(), None, at("19:55"))
        self.assertEqual(r["decision"], "NO_ACTIONABLE_ORDERS")

    def test_coach_tags_revenge_fomo_and_premature_exit(self):
        t = self.trades([
            self.row(1, "20", "19:03", "19:20", 0.01, -0.4),                       # fomo: antes de 19:15
            self.row(2, "20", "19:25", "19:40", 0.02, 0.38, entry="29893.1",      # revancha con más tamaño
                     exit_="29930.8", tp="30005.7", sl="29924"),                   # y cierre manual protegido
        ])
        coach = self.review.review(t, "2026-10-20", self.rules.Rules())["coach"]
        tags = {x["tag"]: x for x in coach["behavior_tags"]}
        self.assertEqual(tags["revenge_trade"]["confidence"], "high")
        self.assertEqual(tags["fomo_entry"]["confidence"], "low")
        self.assertIn("premature_exit", tags)
        self.assertEqual(coach["human_decision_gate"]["default"], "journal_only")
        self.assertIn(coach["verdict"], {"REVIEW_REQUIRED", "RULE_VIOLATION", "COOL_DOWN"})

    def test_clean_session_has_no_pattern(self):
        t = self.trades([self.row(1, "20", "19:20", "19:50", 0.01, 0.8, exit_="30000", tp="30000")])
        coach = self.review.review(t, "2026-10-20", self.rules.Rules())["coach"]
        self.assertEqual(coach["verdict"], "OK")
        self.assertEqual(coach["behavior_tags"][0]["tag"], "no_pattern_detected")
