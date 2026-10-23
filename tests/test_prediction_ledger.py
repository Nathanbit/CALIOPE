import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import prediction_ledger as pl  # noqa: E402

H = "2026-10-22T00:15:00Z"
OBS = {"source": "test", "low": 1.0}


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def _question(self, text="Q", horizon=H, base_rate=None):
        return pl.ask(self.path, text, horizon, "BINANCE:BTCUSDT 1m", "touch rule",
                      base_rate)["question_id"]

    def _sealed_forecast(self, qid, p, author="claude"):
        return pl.forecast(self.path, qid, author, p, "r", made_at="2026-10-21T18:17:00Z",
                           sealed_evidence={"sha256": "a" * 64, "commit": "17235a6"})

    def test_chain_detects_edit_and_deletion(self):
        qid = self._question()
        self._sealed_forecast(qid, 0.55)
        pl.resolve(self.path, qid, "false", OBS, resolved_at="2026-10-22T00:21:00Z")
        pl.verify(pl.load(self.path))
        lines = self.path.read_text().splitlines()
        tampered = json.loads(lines[1])
        tampered["probability"] = 0.9
        self.path.write_text("\n".join([lines[0], json.dumps(tampered), lines[2]]) + "\n")
        with self.assertRaisesRegex(pl.GateError, "line 2"):
            pl.verify(pl.load(self.path))
        self.path.write_text("\n".join([lines[0], lines[2]]) + "\n")
        with self.assertRaisesRegex(pl.GateError, "chain broken"):
            pl.verify(pl.load(self.path))

    def test_forecast_after_resolution_is_refused(self):
        qid = self._question()
        self._sealed_forecast(qid, 0.55)
        pl.resolve(self.path, qid, "true", OBS, resolved_at="2026-10-22T00:21:00Z")
        with self.assertRaisesRegex(pl.GateError, "after the outcome"):
            self._sealed_forecast(qid, 0.9, author="late")

    def test_resolution_gates(self):
        qid = self._question()
        with self.assertRaisesRegex(pl.GateError, "without forecasts"):
            pl.resolve(self.path, qid, "true", OBS, resolved_at="2026-10-22T00:21:00Z")
        self._sealed_forecast(qid, 0.55)
        with self.assertRaisesRegex(pl.GateError, "before horizon_end"):
            pl.resolve(self.path, qid, "true", OBS, resolved_at="2026-10-21T23:00:00Z")
        with self.assertRaisesRegex(pl.GateError, "reason"):
            pl.resolve(self.path, qid, "void", OBS, resolved_at="2026-10-22T00:21:00Z")
        with self.assertRaisesRegex(pl.GateError, "source"):
            pl.resolve(self.path, qid, "true", {"low": 1}, resolved_at="2026-10-22T00:21:00Z")

    def test_backdating_needs_evidence_and_naive_times_fail(self):
        qid = self._question()
        with self.assertRaisesRegex(pl.GateError, "sealed_evidence"):
            pl.forecast(self.path, qid, "x", 0.5, "r", made_at="2026-10-21T18:17:00Z")
        with self.assertRaisesRegex(pl.GateError, "timezone"):
            pl.ask(self.path, "Q2", "2026-10-22T00:15:00", "s", "c")

    def test_rewording_creates_a_new_question(self):
        a = pl.question_id("BTC above 85k", H, "src", "rule")
        self.assertNotEqual(a, pl.question_id("BTC above 85,000", H, "src", "rule"))
        self.assertEqual(a, pl.question_id("BTC above 85k ", H, "src", "rule"))

    def test_unusable_probability_is_a_failure_not_a_coin(self):
        qid = self._question()
        rec = pl.forecast(self.path, qid, "model", float("nan"), "r",
                          made_at="2026-10-21T18:17:00Z",
                          sealed_evidence={"sha256": "a" * 64, "commit": "x"})
        self.assertEqual(rec["status"], "failure")
        self.assertIsNone(rec["probability"])
        self._sealed_forecast(qid, 0.6)
        pl.resolve(self.path, qid, "true", OBS, resolved_at="2026-10-22T00:21:00Z")
        report = pl.score(pl.load(self.path))
        self.assertEqual(report["counts"]["failures"], 1)
        self.assertEqual([r["author"] for r in report["scoreboard"]], ["claude"])

    def test_void_is_excluded_from_scoring(self):
        qid = self._question()
        self._sealed_forecast(qid, 0.55)
        pl.resolve(self.path, qid, "void", OBS, resolved_at="2026-10-22T00:21:00Z",
                   void_reason="neither level touched")
        report = pl.score(pl.load(self.path))
        self.assertEqual(report["counts"]["void"], 1)
        self.assertEqual(report["scoreboard"], [])
        self.assertIsNone(report["calibration"])

    def test_brier_is_proper_and_murphy_identity_holds(self):
        world = 0.7
        ys = [1.0] * 70 + [0.0] * 30
        scores = {p / 100: pl.brier([{"p": p / 100, "y": y} for y in ys]) for p in range(101)}
        self.assertAlmostEqual(min(scores, key=scores.get), world)
        rng = random.Random(7)
        pairs = []
        for _ in range(400):
            p = rng.choice([0.1, 0.3, 0.5, 0.7, 0.9])
            pairs.append({"p": p, "y": 1.0 if rng.random() < p else 0.0})
        m = pl.murphy(pairs)
        self.assertAlmostEqual(m["within_bin_residual"], 0.0, places=12)
        self.assertAlmostEqual(m["brier"], m["reliability"] - m["resolution"] + m["uncertainty"])

    def test_learn_is_gated_and_only_proposes(self):
        for i in range(12):
            qid = self._question(text=f"Q{i}")
            self._sealed_forecast(qid, 0.8)
            pl.resolve(self.path, qid, "true" if i < 6 else "false", OBS,
                       resolved_at="2026-10-22T00:21:00Z")
        before = self.path.read_text()
        result = pl.learn(pl.load(self.path), min_n=30)
        self.assertTrue(result["observation_only"])
        self.assertFalse(result["automatic_edit"])
        lesson = result["lessons"][0]
        self.assertFalse(lesson["ready"])
        self.assertIn("12/30", lesson["proposal"])
        ready = pl.learn(pl.load(self.path), min_n=10, prior_strength=20.0)["lessons"][0]
        self.assertTrue(ready["ready"])
        # 6 hits of 12 at 0.8 stated, shrunk toward 0.8 with 20 pseudo-observations
        self.assertAlmostEqual(ready["shrunk_freq"], round((6 + 20 * 0.8) / 32, 4))
        self.assertEqual(self.path.read_text(), before, "learn must never write the ledger")

    def test_doctor_fails_on_tampered_ledger(self):
        import caliope_loop
        project = Path(self.tmp.name) / "projects" / "demo" / "predictions"
        ledger = project / "ledger.jsonl"
        qid = pl.ask(ledger, "Q", "2099-01-01T00:00:00Z", "s", "c")["question_id"]
        pl.forecast(ledger, qid, "a", 0.5, "r")
        original = caliope_loop.ROOT
        caliope_loop.ROOT = Path(self.tmp.name)
        try:
            self.assertEqual([c["status"] for c in caliope_loop._check_prediction_ledgers()], ["pass"])
            ledger.write_text(ledger.read_text().replace('"probability":0.5', '"probability":0.9'))
            self.assertEqual([c["status"] for c in caliope_loop._check_prediction_ledgers()], ["fail"])
        finally:
            caliope_loop.ROOT = original

    def test_cli_exit_code_two_on_gate(self):
        code = pl.main(["--ledger", str(self.path), "resolve", "--question", "q-missing",
                        "--outcome", "true", "--observed", json.dumps(OBS)])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
