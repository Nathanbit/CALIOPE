import json
import random
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import prediction_ledger as pl  # noqa: E402

H = "2026-10-22T00:15:00Z"
FAR = datetime(2099, 1, 3, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)
OBS = {"source": "test", "low": 1.0}


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.jsonl"
        self.clock = mock.patch.object(pl, "now_utc", return_value=NOW)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
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
        lines = self.path.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[1])
        tampered["probability"] = 0.9
        self.path.write_text("\n".join([lines[0], json.dumps(tampered), lines[2]]) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(pl.GateError, "line 2"):
            pl.verify(pl.load(self.path))
        self.path.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
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
        with self.assertRaisesRegex(pl.GateError, "in the future"):
            pl.resolve(self.path, qid, "true", OBS, resolved_at="2099-01-01T00:00:00Z")

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
                          sealed_evidence={"sha256": "a" * 64, "commit": "17235a6"})
        self.assertEqual(rec["status"], "failure")
        self.assertIsNone(rec["probability"])
        self._sealed_forecast(qid, 0.6)
        pl.resolve(self.path, qid, "true", OBS, resolved_at="2026-10-22T00:21:00Z")
        report = pl.score(pl.load(self.path))
        self.assertEqual(report["counts"]["failures"], 1)
        board = {r["author"]: r for r in report["scoreboard"]}
        self.assertEqual(board["claude"]["n"], 1)
        self.assertEqual((board["model"]["n"], board["model"]["failures"]), (0, 1))
        self.assertEqual(board["model"]["failure_rate"], 1.0, "abstaining is visible, not hidden")

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
        before = self.path.read_text(encoding="utf-8")
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
        self.assertEqual(self.path.read_text(encoding="utf-8"), before, "learn must never write the ledger")

    def test_doctor_fails_on_tampered_ledger(self):
        import caliope_loop
        project = Path(self.tmp.name) / "projects" / "demo" / "predictions"
        ledger = project / "ledger.jsonl"
        qid = pl.ask(ledger, "Q", "2099-01-01T00:00:00Z", "s", "c")["question_id"]
        pl.forecast(ledger, qid, "a", 0.5, "r")
        with mock.patch.object(caliope_loop, "ROOT", Path(self.tmp.name)):
            self.assertEqual([c["status"] for c in caliope_loop._check_prediction_ledgers()], ["pass"])
            ledger.write_text(ledger.read_text(encoding="utf-8").replace('"probability":0.5', '"probability":0.9'),
                              encoding="utf-8")
            self.assertEqual([c["status"] for c in caliope_loop._check_prediction_ledgers()], ["fail"])

    # Lessons (second recursion level)
    FUT = "2099-01-01T00:00:00Z"

    def _resolved_question(self, i, p=0.8, outcome="true", author="claude"):
        qid = pl.ask(self.path, f"E{i}", H, "s", "c")["question_id"]
        self._sealed_forecast(qid, p, author=author)
        pl.resolve(self.path, qid, outcome, OBS, resolved_at="2026-10-22T00:21:00Z")
        return qid

    def test_lesson_gates(self):
        open_q = pl.ask(self.path, "open", self.FUT, "s", "c")["question_id"]
        with self.assertRaisesRegex(pl.GateError, "needs evidence"):
            pl.propose_lesson(self.path, "x", "claude", [], "learn")
        with self.assertRaisesRegex(pl.GateError, "not resolved"):
            pl.propose_lesson(self.path, "x", "claude", [open_q], "learn")
        q = self._resolved_question(0)
        lid = pl.propose_lesson(self.path, "Shade 80% calls", "claude", [q], "learn")["lesson_id"]
        with self.assertRaisesRegex(pl.GateError, "already exists"):
            pl.propose_lesson(self.path, "Shade 80% calls", "claude", [q], "learn")
        with self.assertRaisesRegex(pl.GateError, "not an adopted lesson"):
            pl.forecast(self.path, open_q, "claude", 0.6, "r", lessons_applied=[lid])
        with self.assertRaisesRegex(pl.GateError, "decided_by"):
            pl.decide_lesson(self.path, lid, "adopt", " ", "ok")
        with self.assertRaisesRegex(pl.GateError, "cannot retire"):
            pl.decide_lesson(self.path, lid, "retire", "nathan", "early")
        pl.decide_lesson(self.path, lid, "adopt", "nathan", "looks right")
        rec = pl.forecast(self.path, open_q, "claude", 0.6, "r", lessons_applied=[lid])
        self.assertEqual(rec["lessons_applied"], [lid])
        pl.decide_lesson(self.path, lid, "retire", "nathan", "stopped helping")
        with self.assertRaisesRegex(pl.GateError, "not an adopted lesson"):
            pl.forecast(self.path, open_q, "other", 0.6, "r", lessons_applied=[lid])
        pl.verify(pl.load(self.path))

    def test_lesson_score_compares_before_and_after_adoption(self):
        for i in range(4):
            self._resolved_question(i, p=0.9, outcome="true" if i % 2 else "false")
        evidence = [self._resolved_question(9, p=0.99, outcome="false")]
        lid = pl.propose_lesson(self.path, "Cap at 60%", "claude", evidence, "learn")["lesson_id"]
        pl.decide_lesson(self.path, lid, "adopt", "nathan", "test")
        horizon = "2099-01-01T00:00:00Z"
        for i in range(4):
            qid = pl.ask(self.path, f"A{i}", horizon, "s", "c")["question_id"]
            pl.forecast(self.path, qid, "claude", 0.6, "r", lessons_applied=[lid])
            with mock.patch.object(pl, "now_utc", return_value=FAR):
                pl.resolve(self.path, qid, "true" if i % 2 else "false", OBS,
                           resolved_at="2099-01-02T00:00:00Z")
        report = pl.score_lessons(pl.load(self.path), min_n=4)
        row = report["lessons"][0]
        self.assertEqual((row["n_before"], row["n_after"]), (4, 4))
        self.assertAlmostEqual(row["brier_before"], (0.01 + 0.81) / 2)
        self.assertAlmostEqual(row["brier_after"], (0.16 + 0.36) / 2)
        self.assertEqual(row["verdict"], "helps")
        self.assertTrue(report["observation_only"])
        self.assertEqual(pl.score_lessons(pl.load(self.path))["lessons"][0]["verdict"], "insufficient")

    def test_lesson_that_hurts_is_proposed_for_retirement(self):
        for i in range(4):
            self._resolved_question(i, p=0.6, outcome="true" if i % 2 else "false")
        evidence = [self._resolved_question(9, p=0.6, outcome="true")]
        lid = pl.propose_lesson(self.path, "Be bolder", "claude", evidence, "learn")["lesson_id"]
        pl.decide_lesson(self.path, lid, "adopt", "nathan", "test")
        for i in range(4):
            qid = pl.ask(self.path, f"B{i}", "2099-01-01T00:00:00Z", "s", "c")["question_id"]
            pl.forecast(self.path, qid, "claude", 0.95, "r", lessons_applied=[lid])
            with mock.patch.object(pl, "now_utc", return_value=FAR):
                pl.resolve(self.path, qid, "true" if i % 2 else "false", OBS,
                           resolved_at="2099-01-02T00:00:00Z")
        row = pl.score_lessons(pl.load(self.path), min_n=4)["lessons"][0]
        self.assertEqual(row["verdict"], "hurts")
        self.assertIn("retiring", row["proposal"])
        self.assertEqual(row["status"], "adopted", "scoring never retires on its own")

    def test_cli_exit_code_two_on_gate(self):
        code = pl.main(["--ledger", str(self.path), "resolve", "--question", "q-missing",
                        "--outcome", "true", "--observed", json.dumps(OBS)])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
