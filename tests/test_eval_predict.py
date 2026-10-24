"""Fixed evals for turning messages into concepts (idea from microsoft/skill-recorder)."""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import eval_predict as ev  # noqa: E402

GOOD = {"concept": {"asset": "BTCUSDT", "event": "touch", "interval": "15m",
                    "start": "2026-10-23T14:15:00Z", "horizon_end": "2026-10-24T05:00:00Z",
                    "reference_price": 81600, "reference_source": "eval", "level": 82500,
                    "side": "at_or_above"}, "probability": 0.6}


class EvalPredictTest(unittest.TestCase):
    def setUp(self):
        self.cases = ev.load_cases()
        self.one = dict(self.cases, cases=[self.cases["cases"][0]])

    def test_a_correct_answer_passes(self):
        self.assertEqual(ev.grade(self.one, {"hoy-arriba": GOOD})["passed"], 1)

    def test_wrong_fields_missing_answers_and_invented_prices_fail(self):
        wrong_side = {"concept": dict(GOOD["concept"], level=81000, side="at_or_below"),
                      "probability": 0.6}
        [r] = ev.grade(self.one, {"hoy-arriba": wrong_side})["results"]
        self.assertIn("level: expected 82500, got 81000.0", r["problems"])
        [r] = ev.grade(self.one, {})["results"]
        self.assertEqual(r["problems"], ["no answer"])
        invented = {"concept": dict(GOOD["concept"], reference_price=81700), "probability": 0.6}
        [r] = ev.grade(self.one, {"hoy-arriba": invented})["results"]
        self.assertIn("reference_price differs from the observed one", r["problems"])
        bad = {"concept": dict(GOOD["concept"], mood="bull"), "probability": 0.6}
        [r] = ev.grade(self.one, {"hoy-arriba": bad})["results"]
        self.assertTrue(r["problems"][0].startswith("invalid concept"))

    def test_the_panel_interpreter_passes_every_case(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node no está instalado: parse.js sin calificar")
        out = subprocess.run([node, str(ROOT / "tools" / "panel" / "answer_cases.mjs")],
                             capture_output=True, text=True, encoding="utf-8", timeout=60,
                             env=dict(os.environ, TZ="America/Guayaquil"), check=True)
        report = ev.grade(self.cases, json.loads(out.stdout))
        self.assertEqual(report["passed"], report["cases"],
                         [r for r in report["results"] if not r["pass"]])


if __name__ == "__main__":
    unittest.main()
