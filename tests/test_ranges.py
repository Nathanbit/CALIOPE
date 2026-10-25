"""Range forecasts: close_value questions, quantile forecasts, quantile score and coverage."""
import json
import math
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
import resolver  # noqa: E402

STEP = 900_000
DAY = 86_400_000
START = int(datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
CONCEPT = {"asset": "BTCUSDT", "event": "close_value", "interval": "15m",
           "start": "2026-10-25T12:00:00Z", "horizon_end": "2026-10-25T16:00:00Z",
           "reference_price": 80000, "reference_source": "test"}
BAND = {"0.1": 78000, "0.5": 80500, "0.9": 83000}
BEFORE = datetime(2026, 10, 25, 11, 0, tzinfo=timezone.utc)
AFTER = datetime(2026, 10, 25, 16, 30, tzinfo=timezone.utc)


def klines(final_close=81000.0, days=10):
    rows, price, i = [], 80000.0, 0
    for k in range(days * DAY // STEP, 0, -1):
        t = START - k * STEP
        i += 1
        c = price * (1.002 if i % 2 else 0.998)
        rows.append([t, str(price), str(max(price, c) * 1.001), str(min(price, c) * 0.999), str(c), "1", t + STEP - 1])
        price = c
    for k in range(16):
        t = START + k * STEP
        c = final_close if k == 15 else 80000.0
        rows.append([t, "80000", str(max(80100.0, c)), str(min(79900.0, c)), str(c), "1", t + STEP - 1])
    return {"symbol": "BTCUSDT", "interval": "15m", "fetched_at": "2026-10-25T16:10:00Z", "klines": rows}


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "l.jsonl"
        self.clock = mock.patch.object(pl, "now_utc", return_value=BEFORE)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.tmp.cleanup()

    def test_concept_wording_and_class(self):
        c = concepts.validate_concept(CONCEPT)
        self.assertIn("Precio de cierre de BTCUSDT", concepts.question_fields(c)["text"])
        self.assertEqual(concepts.concept_class(c), "BTCUSDT|close_value|value|d-|h<=6h")

    def test_seal_a_range_and_its_gates(self):
        out = pl.seal(self.path, CONCEPT, "nathan", None, "r", quantiles={"0.10": 78000, "0.5": 80500, "0.9": 83000})
        self.assertEqual(out["forecast"]["quantiles"], {"0.1": 78000.0, "0.5": 80500.0, "0.9": 83000.0})
        self.assertIsNone(out["forecast"]["probability"])
        qid = out["question"]["question_id"]
        bad = [({"0.1": 78000, "0.9": 83000}, "include"),
               ({"0.1": 80000, "0.5": 79000, "0.9": 83000}, "decrease"),
               ({"0.1": 78000, "0.5": 80500, "0.9": 83000, "0.33": 80000}, "levels"),
               ({"0.1": 78000, "0.5": -1, "0.9": 83000}, "positive finite"),
               ({"0.1": 78000, "0.5": float("nan"), "0.9": 83000}, "NaN")]
        for quantiles, message in bad:
            with self.assertRaisesRegex(pl.GateError, message):
                pl.forecast(self.path, qid, "claude", None, "r", quantiles=quantiles)
        with self.assertRaisesRegex(pl.GateError, "with quantiles, not a probability"):
            pl.forecast(self.path, qid, "claude", 0.5, "r")
        with mock.patch.object(pl, "now_utc", return_value=datetime(2026, 10, 25, 12, 1, tzinfo=timezone.utc)):
            with self.assertRaisesRegex(pl.GateError, "already started"):
                pl.forecast(self.path, qid, "claude", None, "r", quantiles=BAND)
        pl.verify(pl.load(self.path))

    def test_yes_no_questions_refuse_quantiles(self):
        qid = pl.ask(self.path, "A", "2026-10-26T00:00:00Z", "s", "c")["question_id"]
        with self.assertRaisesRegex(pl.GateError, "only belong to value"):
            pl.forecast(self.path, qid, "nathan", None, "r", quantiles=BAND)

    def test_resolve_score_and_rivals(self):
        qid = pl.seal(self.path, CONCEPT, "nathan", None, "r", quantiles=BAND)["question"]["question_id"]
        pl.forecast(self.path, qid, "claude", None, "r", quantiles={"0.1": 80900, "0.5": 81000, "0.9": 81100})
        data = Path(self.tmp.name) / "k.json"
        data.write_text(json.dumps(klines(final_close=81000.0)), encoding="utf-8")
        with mock.patch.object(pl, "now_utc", return_value=AFTER):
            res = pl.resolve_auto(self.path, qid, data)
        self.assertEqual((res["outcome"], res["value"]), ("value", 81000.0))
        for rival in ("volatility_rival", "bootstrap_rival"):
            self.assertTrue(res["observed"][rival]["available"], rival)
            self.assertEqual(set(res["observed"][rival]["quantiles"]), set(resolver.QUANTILE_LEVELS))
        records = pl.load(self.path)
        pl.verify(records)
        board = {r["author"]: r for r in fs.range_score(records)["authors"]}
        self.assertAlmostEqual(board["nathan"]["crps_rel"], round(fs.quantile_score(BAND, 81000), 6))
        self.assertLess(board["claude"]["crps_rel"], board["nathan"]["crps_rel"], "narrow and right beats wide")
        self.assertEqual(board["nathan"]["coverage_80"], 1.0)
        self.assertEqual(board["nathan"]["volatility_rival"]["n"], 1)
        report = fs.score(records)
        self.assertEqual((report["counts"]["value"], report["scoreboard"]), (1, []))


class ScoreTest(unittest.TestCase):
    def test_quantile_score(self):
        self.assertEqual(fs.quantile_score({"0.5": 100.0}, 100.0), 0.0)
        # y below the 0.9 quantile by 10: 2 * 0.1 * 10 / 100
        self.assertAlmostEqual(fs.quantile_score({"0.9": 110.0}, 100.0), 0.02)
        # y above the 0.1 quantile by 10: 2 * 0.1 * 10 / 100
        self.assertAlmostEqual(fs.quantile_score({"0.1": 90.0}, 100.0), 0.02)
        # a miss costs more than a hit
        self.assertGreater(fs.quantile_score({"0.9": 90.0}, 100.0), fs.quantile_score({"0.9": 110.0}, 100.0))

    def test_volatility_rival_bands_are_lognormal(self):
        rule = {"kind": "close_value", "symbol": "BTCUSDT", "interval": "15m", "start": CONCEPT["start"]}
        out = resolver.volatility_rival(rule, CONCEPT["horizon_end"], klines())
        q = out["quantiles"]
        self.assertLess(q["0.1"], q["0.5"])
        self.assertLess(q["0.5"], q["0.9"])
        self.assertAlmostEqual(math.log(q["0.9"] / q["0.5"]), math.log(q["0.5"] / q["0.1"]), places=3)


if __name__ == "__main__":
    unittest.main()
