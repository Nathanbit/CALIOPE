import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import concepts  # noqa: E402
import forecast_scoring as fs  # noqa: E402
import prediction_ledger as pl  # noqa: E402

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)
TOUCH = {"asset": "btcusdt", "event": "touch", "interval": "15m",
         "start": "2026-10-23T12:15:00Z", "horizon_end": "2026-10-24T12:15:00+00:00",
         "reference_price": 81270, "reference_source": "Binance BTCUSDT last price 12:00Z",
         "level": 80000, "side": "at_or_below"}
FIRST = {"asset": "BTCUSDT", "event": "touch_first", "interval": "1m",
         "start": "2026-10-23T12:15:00Z", "horizon_end": "2026-10-23T18:15:00Z",
         "reference_price": 81270.0, "reference_source": "Binance",
         "lower": 80500, "upper": 82000, "true_if": "lower_first"}


class ConceptTest(unittest.TestCase):
    def test_normalization_and_deterministic_wording(self):
        c = concepts.validate_concept(TOUCH)
        self.assertEqual((c["asset"], c["horizon_end"], c["level"]),
                         ("BTCUSDT", "2026-10-24T12:15:00Z", 80000.0))
        fields = concepts.question_fields(c)
        self.assertEqual(fields, concepts.question_fields(concepts.validate_concept(dict(TOUCH))))
        self.assertIn("80,000", fields["text"])
        self.assertEqual(fields["rule"]["kind"], "touch")

    def test_not_a_prediction_is_refused(self):
        with self.assertRaisesRegex(concepts.ConceptError, "already satisfies"):
            concepts.validate_concept(dict(TOUCH, reference_price=79000))
        with self.assertRaisesRegex(concepts.ConceptError, "between lower and upper"):
            concepts.validate_concept(dict(FIRST, reference_price=83000))
        with self.assertRaisesRegex(concepts.ConceptError, "reference_source"):
            concepts.validate_concept(dict(TOUCH, reference_source=" "))
        with self.assertRaisesRegex(concepts.ConceptError, "unexpected"):
            concepts.validate_concept(dict(TOUCH, mood="bearish"))

    def test_concept_class_drops_exact_numbers(self):
        a = concepts.concept_class(concepts.validate_concept(TOUCH))
        b = concepts.concept_class(concepts.validate_concept(dict(TOUCH, level=79900)))
        c = concepts.concept_class(concepts.validate_concept(dict(TOUCH, level=76000)))
        self.assertEqual(a, "BTCUSDT|touch|at_or_below|d1-2%|h<=24h")
        self.assertEqual(a, b, "same kind of prediction, same class")
        self.assertNotEqual(a, c, "a 6% move is a different kind of prediction")


class SealTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.jsonl"
        self.clock = mock.patch.object(pl, "now_utc", return_value=NOW)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.tmp.cleanup()

    def test_one_message_seals_question_and_forecast(self):
        out = pl.seal(self.path, TOUCH, "nathan", 0.3, "lower highs since 84.8k")
        records = pl.load(self.path)
        pl.verify(records)
        self.assertEqual([r["type"] for r in records], ["question", "forecast"])
        self.assertEqual(out["forecast"]["question_id"], out["question"]["question_id"])
        self.assertEqual(out["question"]["concept_class"], "BTCUSDT|touch|at_or_below|d1-2%|h<=24h")

    def test_dry_run_checks_everything_and_writes_nothing(self):
        out = pl.seal(self.path, TOUCH, "nathan", 0.3, "r", dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertIn("80,000", out["question"]["text"])
        self.assertFalse(self.path.exists() and self.path.read_text(encoding="utf-8").strip())
        with self.assertRaisesRegex(pl.GateError, "window has already started"):
            pl.seal(self.path, dict(TOUCH, start="2026-10-23T11:00:00Z"), "nathan", 0.3, "r",
                    dry_run=True)

    def test_seal_is_all_or_nothing(self):
        late = dict(TOUCH, start="2026-10-23T11:00:00Z")
        with self.assertRaisesRegex(pl.GateError, "window has already started"):
            pl.seal(self.path, late, "nathan", 0.3, "r")
        self.assertFalse(self.path.exists() and self.path.read_text(encoding="utf-8").strip())

    def test_same_concept_twice_is_one_question(self):
        pl.seal(self.path, TOUCH, "nathan", 0.3, "r")
        with self.assertRaisesRegex(pl.GateError, "already exists"):
            pl.seal(self.path, dict(TOUCH, asset="BTCUSDT", level=80000.0), "claude", 0.2, "r")

    def test_tampered_wording_fails_verify_even_with_valid_hash(self):
        pl.seal(self.path, TOUCH, "nathan", 0.3, "r")
        records = pl.load(self.path)
        forged = {k: v for k, v in records[0].items() if k != "hash"}
        forged["text"] = forged["text"] + " (aprox.)"
        forged["question_id"] = pl.question_id(forged["text"], forged["horizon_end"],
                                               forged["source"], forged["criteria"], forged["rule"])
        forged["hash"] = pl.sha256_text(pl.canonical(forged))
        self.path.write_text(pl.canonical(forged) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(pl.GateError, "wording generated from its concept"):
            pl.verify(pl.load(self.path))

    def test_learning_transfers_across_questions_of_one_class(self):
        short = dict(TOUCH, horizon_end="2026-10-23T13:15:00Z")  # four 15m candles
        t0 = int(datetime(2026, 10, 23, 12, 15, tzinfo=timezone.utc).timestamp() * 1000)
        outcomes = []
        for i, level in enumerate((80000, 79950, 79900, 80050)):
            q = pl.seal(self.path, dict(short, level=level), "nathan", 0.8, "r")["question"]
            low = level - 10 if i < 2 else level + 300  # two touched, two not
            rows = [[t0 + k * 900_000, "81000", "81100", str(low), "81000", "1",
                     t0 + k * 900_000 + 899_999] for k in range(4)]
            data = Path(self.tmp.name) / f"k{i}.json"
            data.write_text(json.dumps({"symbol": "BTCUSDT", "interval": "15m",
                                        "fetched_at": "2026-10-23T13:30:00Z", "klines": rows}),
                            encoding="utf-8")
            with mock.patch.object(pl, "now_utc", return_value=LATER):
                outcomes.append(pl.resolve_auto(self.path, q["question_id"], data)["outcome"])
        self.assertEqual(outcomes, ["true", "true", "false", "false"])
        report = fs.learn(pl.load(self.path), min_n=4, group_by="concept")
        [row] = report["lessons"]
        self.assertEqual((row["concept_class"], row["n"], row["observed_freq"]),
                         ("BTCUSDT|touch|at_or_below|d1-2%|h<=6h", 4, 0.5))
        self.assertTrue(row["ready"])
        self.assertIn("nathan on BTCUSDT|touch", row["proposal"])
        by = fs.score(pl.load(self.path))["by_concept"]
        self.assertEqual((by[0]["n"], by[0]["observed_rate"]), (4, 0.5))

if __name__ == "__main__":
    unittest.main()
