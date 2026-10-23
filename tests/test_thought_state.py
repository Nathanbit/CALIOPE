"""Continuous thought state (Coconut idea): every forecast carries the
author's reproducible state, linked to their previous forecast."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import forecast_scoring as fs  # noqa: E402
import prediction_ledger as pl  # noqa: E402

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc)
H = "2026-10-23T18:00:00Z"


class ThoughtStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.jsonl"
        self.clock = mock.patch.object(pl, "now_utc", return_value=NOW)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.tmp.cleanup()

    def q(self, text):
        return pl.ask(self.path, text, H, "s", "c")["question_id"]

    def test_forecasts_form_a_linked_chain_per_author(self):
        first = pl.forecast(self.path, self.q("A"), "nathan", 0.6, "r")
        other = pl.forecast(self.path, self.q("B"), "claude", 0.4, "r")
        second = pl.forecast(self.path, self.q("C"), "nathan", 0.7, "r")
        self.assertIsNone(first["thought_state"]["parent"])
        self.assertIsNone(other["thought_state"]["parent"], "chains are per author")
        self.assertEqual(second["thought_state"]["parent"], first["hash"])
        pl.verify(pl.load(self.path))

    def test_state_evolves_with_outcomes(self):
        qid = self.q("A")
        before = pl.forecast(self.path, qid, "nathan", 0.8, "r")["thought_state"]
        with mock.patch.object(pl, "now_utc", return_value=LATER):
            pl.resolve(self.path, qid, "false", {"source": "t"})
            after = pl.forecast(self.path, pl.ask(self.path, "B", "2026-10-25T00:00:00Z", "s",
                                                  "c")["question_id"], "nathan", 0.8, "r")
        self.assertEqual((before["n"], before["brier"]), (0, None))
        state = after["thought_state"]
        self.assertEqual((state["n"], state["brier"]), (1, 0.64))
        self.assertEqual(state["calibration"][0]["bin"], "0.8-1.0")
        self.assertAlmostEqual(state["calibration"][0]["shrunk_freq"], round(16 / 21, 6))

    def test_inflated_state_with_valid_hash_fails_verify(self):
        pl.forecast(self.path, self.q("A"), "nathan", 0.6, "r")
        records = pl.load(self.path)
        forged = {k: v for k, v in records[-1].items() if k != "hash"}
        forged["thought_state"] = dict(forged["thought_state"], n=500, brier=0.01)
        forged["hash"] = pl.sha256_text(pl.canonical(forged))
        lines = self.path.read_text(encoding="utf-8").splitlines()
        lines[-1] = pl.canonical(forged)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(pl.GateError, "thought_state does not match"):
            pl.verify(pl.load(self.path))

    def test_state_command_shows_what_the_next_forecast_will_carry(self):
        pl.forecast(self.path, self.q("A"), "nathan", 0.6, "r")
        qid = self.q("B")
        with mock.patch("sys.stdout") as out:
            pl.main(["--ledger", str(self.path), "state", "--author", "nathan", "--question", qid])
        shown = json.loads("".join(call.args[0] for call in out.write.call_args_list))
        recorded = pl.forecast(self.path, qid, "nathan", 0.5, "r")["thought_state"]
        self.assertEqual(shown, recorded)

    def test_backdated_forecasts_carry_no_state(self):
        rec = pl.forecast(self.path, self.q("A"), "nathan", 0.6, "r",
                          made_at="2026-10-23T08:00:00Z",
                          sealed_evidence={"sha256": "a" * 64, "commit": "17235a6"})
        self.assertNotIn("thought_state", rec)

    def test_state_reports_concept_class_history(self):
        state = fs.thought_state(pl.verify(pl.load(self.path)), "nathan",
                                 "BTCUSDT|touch|at_or_below|d1-2%|h<=24h")
        self.assertEqual((state["class_n"], state["class_observed_rate"]), (0, None))


if __name__ == "__main__":
    unittest.main()
