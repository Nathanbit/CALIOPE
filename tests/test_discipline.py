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
