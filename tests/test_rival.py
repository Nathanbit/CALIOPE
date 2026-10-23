"""Author identities and the base rival with bootstrap."""
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import forecast_scoring as fs  # noqa: E402
import prediction_ledger as pl  # noqa: E402

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.jsonl"
        self.clock = mock.patch.object(pl, "now_utc", return_value=NOW)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.tmp.cleanup()

    def q(self, text):
        return pl.ask(self.path, text, "2026-10-23T18:00:00Z", "s", "c")["question_id"]


class AuthorTest(LedgerCase):
    def test_kinds_are_accepted_and_case_is_folded(self):
        for name in ("nathan", "ia:gemini", "indicador:macroflow", "sim:mirofish",
                     "experto:juan.p"):
            pl.forecast(self.path, self.q(name), name, 0.5, "r")
        rec = pl.forecast(self.path, self.q("x"), "  IA:ChatGPT ", 0.5, "r")
        self.assertEqual(rec["author"], "ia:chatgpt")
        pl.verify(pl.load(self.path))

    def test_unknown_kind_or_free_text_is_refused(self):
        for bad in ("broker:x", "Juan Pérez", "ia:", ":x", "a:b:c"):
            with self.assertRaises(pl.GateError, msg=bad):
                pl.forecast(self.path, self.q(bad), bad, 0.5, "r")

    def test_lesson_scope_uses_the_same_identity(self):
        qid = self.q("A")
        pl.forecast(self.path, qid, "ia:gemini", 0.5, "r")
        with mock.patch.object(pl, "now_utc", return_value=NOW + timedelta(days=1)):
            pl.resolve(self.path, qid, "true", {"source": "t"})
            lesson = pl.propose_lesson(self.path, "t", "IA:Gemini", [qid], "claude")
        self.assertEqual(lesson["scope"], "ia:gemini")
        with self.assertRaisesRegex(pl.GateError, "scope"):
            pl.propose_lesson(self.path, "u", "a b", [qid], "claude")


class RivalTest(LedgerCase):
    def run_history(self, author, probability, outcomes):
        moment = NOW
        for i, outcome in enumerate(outcomes):
            with mock.patch.object(pl, "now_utc", return_value=moment):
                qid = pl.ask(self.path, f"{author}{i}", pl.eio.iso_utc(moment + timedelta(hours=1)),
                             "s", "c")["question_id"]
                pl.forecast(self.path, qid, author, probability(outcome), "r")
            moment += timedelta(hours=2)
            with mock.patch.object(pl, "now_utc", return_value=moment):
                pl.resolve(self.path, qid, outcome, {"source": "t"})

    def test_rival_only_uses_the_past(self):
        self.run_history("nathan", lambda o: 0.9 if o == "true" else 0.1,
                         ["true", "true", "false"])
        rival_p = fs._rival_probabilities(pl.load(self.path))
        self.assertEqual(sorted(rival_p.values()), [0.5, 2 / 3, 0.75])

    def test_skilled_author_beats_rival_and_coin_flipper_does_not(self):
        outcomes = ["true", "false"] * 20
        random.Random(3).shuffle(outcomes)  # strict alternation would punish any history
        self.run_history("ia:gemini", lambda o: 0.9 if o == "true" else 0.1, outcomes)
        self.run_history("sim:mirofish", lambda o: 0.5, outcomes)
        report = fs.rival(pl.load(self.path), resamples=500, seed=1, min_n=30)
        verdicts = {row["author"]: row["verdict"] for row in report["authors"]}
        self.assertEqual(verdicts["ia:gemini"], "beats_rival")
        self.assertIn(verdicts["sim:mirofish"], ("undistinguished", "worse_than_rival"))
        self.assertEqual(report, fs.rival(pl.load(self.path), resamples=500, seed=1, min_n=30),
                         "fixed seed: reproducible")

    def test_small_samples_are_insufficient(self):
        self.run_history("nathan", lambda o: 0.9, ["true"] * 3)
        [row] = fs.rival(pl.load(self.path), resamples=200)["authors"]
        self.assertEqual(row["verdict"], "insufficient")


if __name__ == "__main__":
    unittest.main()
