"""The swarm (idea from MiroFish) as a derived forecaster, scored like any author."""
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


class SwarmTest(unittest.TestCase):
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

    def resolve(self, qid, outcome):
        with mock.patch.object(pl, "now_utc", return_value=LATER):
            pl.resolve(self.path, qid, outcome, {"source": "t"})

    def test_swarm_is_mean_log_odds_and_needs_several_authors(self):
        crowded, alone = self.q("A"), self.q("B")
        pl.forecast(self.path, crowded, "nathan", 0.8, "r")
        pl.forecast(self.path, crowded, "sim:mirofish", 0.2, "r")
        pl.forecast(self.path, alone, "nathan", 0.7, "r")
        self.resolve(crowded, "true")
        self.resolve(alone, "false")
        pairs, _ = fs.scored_pairs(pl.load(self.path))
        [swarm] = fs.swarm_pairs(pairs)
        self.assertEqual(swarm["question_id"], crowded)
        self.assertAlmostEqual(swarm["p"], 0.5)
        self.assertEqual(swarm["members"], ["nathan", "sim:mirofish"])
        self.assertEqual(swarm["seq"], max(x["seq"] for x in pairs if x["question_id"] == crowded))
        self.assertEqual(fs.swarm_pairs(pairs, min_authors=3), [])

    def test_swarm_is_scored_against_the_rival_and_never_a_ledger_author(self):
        qid = self.q("A")
        pl.forecast(self.path, qid, "nathan", 0.9, "r")
        pl.forecast(self.path, qid, "ia:gemini", 0.6, "r")
        self.resolve(qid, "true")
        rows = {r["author"]: r for r in fs.rival(pl.load(self.path), 200)["authors"]}
        self.assertEqual(set(rows), {"nathan", "ia:gemini", fs.SWARM})
        self.assertEqual(rows[fs.SWARM]["verdict"], "insufficient")
        self.assertFalse(pl.AUTHOR.fullmatch(fs.SWARM))
        with self.assertRaises(pl.GateError):
            pl.forecast(self.path, self.q("B"), fs.SWARM, 0.5, "r")

    def test_extreme_forecasts_are_clipped(self):
        qid = self.q("A")
        pl.forecast(self.path, qid, "nathan", 1.0, "r")
        pl.forecast(self.path, qid, "ia:chatgpt", 1.0, "r")
        self.resolve(qid, "true")
        [swarm] = fs.swarm_pairs(fs.scored_pairs(pl.load(self.path))[0])
        self.assertAlmostEqual(swarm["p"], 0.99)


if __name__ == "__main__":
    unittest.main()
