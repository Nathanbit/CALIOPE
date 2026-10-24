"""The causal lab's standard-library parts: alignment, split, holdout test, fetchers."""
import csv
import json
import math
import random
import sys
import tempfile
from datetime import date, timedelta
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "labs" / "causal_btc"
sys.path.insert(0, str(LAB))

import evaluate  # noqa: E402
import fetch_series  # noqa: E402
import series  # noqa: E402


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def synthetic(n=700, seed=3):
    """btc(t) follows ndx(t-1); noise is unrelated."""
    rng = random.Random(seed)
    btc, ndx, noise = 100.0, 100.0, 100.0
    rows, last_ndx_ret = [], 0.0
    for i in range(n):
        ndx_ret = rng.gauss(0, 0.01)
        btc *= math.exp(0.8 * last_ndx_ret + rng.gauss(0, 0.004))
        ndx *= math.exp(ndx_ret)
        noise *= math.exp(rng.gauss(0, 0.01))
        last_ndx_ret = ndx_ret
        rows.append({"date": (date(2024, 1, 1) + timedelta(days=i)).isoformat(), "btc": btc,
                     "ndx": ndx, "noise": noise})
    return rows


class SeriesTest(unittest.TestCase):
    def test_missing_days_are_dropped_never_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.csv"
            write_csv(path, [{"date": "2026-01-01", "btc": 100, "ndx": 10, "btc_funding": 0.0001},
                             {"date": "2026-01-02", "btc": 110, "ndx": "", "btc_funding": 0.0002},
                             {"date": "2026-01-03", "btc": 121, "ndx": 11, "btc_funding": 0.0003}])
            data = series.transform(*series.load_csv(path))
        self.assertEqual(data["dates"], ["2026-01-03"])
        self.assertEqual(data["dropped_days"], 1)
        self.assertAlmostEqual(data["values"]["btc"][0], math.log(1.21))
        self.assertAlmostEqual(data["values"]["ndx"][0], math.log(1.1))
        self.assertEqual(data["values"]["btc_funding"][0], 0.0003, "funding stays a level")

    def test_bad_inputs_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "s.csv"
            write_csv(path, [{"date": "2026-01-02", "btc": 1}, {"date": "2026-01-01", "btc": 2}])
            with self.assertRaisesRegex(series.SeriesError, "increasing"):
                series.load_csv(path)
            write_csv(path, [{"date": "2026-01-01", "eth": 1}])
            with self.assertRaisesRegex(series.SeriesError, "'btc'"):
                series.load_csv(path)
        data = {"dates": ["d"] * 150, "names": ["btc"], "values": {"btc": [0.0] * 150}}
        with self.assertRaisesRegex(series.SeriesError, "100 discovery days"):
            series.split(data, 60)


class EvaluateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "s.csv"
        write_csv(self.path, synthetic())
        data = series.transform(*series.load_csv(self.path))
        discovery, _ = series.split(data, 200)
        self.hyp = {"data_sha256": series.file_sha256(self.path), "holdout_days": 200,
                    "discovery_end": discovery["dates"][-1],
                    "edges": [{"cause": "ndx", "lag": 1, "sign": 1},
                              {"cause": "noise", "lag": 1, "sign": 1}]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_real_link_holds_and_noise_does_not(self):
        report = evaluate.evaluate(self.hyp, self.path, permutations=300)
        rows = {r["cause"]: r for r in report["results"]}
        self.assertTrue(rows["ndx"]["holds"])
        self.assertGreater(rows["ndx"]["hit_rate"], 0.7)
        self.assertFalse(rows["noise"]["holds"])
        self.assertEqual(report["edges_holding"], 1)
        self.assertEqual(report, evaluate.evaluate(self.hyp, self.path, permutations=300))

    def test_the_wrong_sign_does_not_hold(self):
        hyp = dict(self.hyp, edges=[{"cause": "ndx", "lag": 1, "sign": -1}])
        self.assertFalse(evaluate.evaluate(hyp, self.path, permutations=300)["results"][0]["holds"])

    def test_changed_data_or_split_is_refused(self):
        with self.assertRaisesRegex(series.SeriesError, "not the one"):
            evaluate.evaluate(dict(self.hyp, data_sha256="0" * 64), self.path, 100)
        with self.assertRaisesRegex(series.SeriesError, "split differs"):
            evaluate.evaluate(dict(self.hyp, discovery_end="x"), self.path, 100)


class FetchTest(unittest.TestCase):
    def test_stooq_and_binance_parsing(self):
        stooq = b"Date,Open,High,Low,Close,Volume\n2026-01-02,1,1,1,20000.5,0\n2026-01-05,1,1,1,20100,0\n"
        with mock.patch.object(fetch_series, "get", return_value=stooq):
            self.assertEqual(fetch_series.stooq_closes("^ndx"), {"2026-01-02": 20000.5, "2026-01-05": 20100.0})
        with mock.patch.object(fetch_series, "get", return_value=b"No data"):
            with self.assertRaisesRegex(ValueError, "no table"):
                fetch_series.stooq_closes("^ndx")
        day = 86_400_000
        start = 1767225600000  # 2026-01-01
        pages = [json.dumps([[start, "1", "1", "1", "100", "1", start + day - 1],
                             [start + day, "1", "1", "1", "101", "1", start + 2 * day - 1]]).encode(),
                 b"[]"]
        with mock.patch.object(fetch_series, "get", side_effect=pages):
            closes = fetch_series.binance_closes("BTCUSDT", start, start + 2 * day)
        self.assertEqual(closes, {"2026-01-01": 100.0, "2026-01-02": 101.0})

    def test_mempool_series_are_dated_by_the_day_they_measure(self):
        hashrate = json.dumps({"hashrates": [
            {"timestamp": 1767312000, "avgHashrate": 9.1e20},   # 2026-01-02T00:00Z ends Jan 1
            {"timestamp": 1767398400, "avgHashrate": 0}],       # zero is dropped, not kept
            "difficulty": []}).encode()
        with mock.patch.object(fetch_series, "get", return_value=hashrate):
            self.assertEqual(fetch_series.mempool_hashrate(), {"2026-01-01": 9.1e20})
        fees = json.dumps([{"avgHeight": 930000, "timestamp": 1767268800, "avgFees": 4000000},
                           {"avgHeight": 930144, "timestamp": 1767355200, "avgFees": 5000000}]).encode()
        with mock.patch.object(fetch_series, "get", return_value=fees):
            self.assertEqual(fetch_series.mempool_fees(), {"2026-01-01": 4e6, "2026-01-02": 5e6})
        with mock.patch.object(fetch_series, "get", return_value=b'{"error": "x"}'):
            with self.assertRaisesRegex(ValueError, "unexpected"):
                fetch_series.mempool_hashrate()

    def test_a_failing_source_is_reported_not_filled(self):
        def fake(url):
            if "stooq" in url:
                raise OSError("blocked")
            if "fundingRate" in url or "startTime=1767225600000" not in url:
                return b"[]"
            return json.dumps([[1767225600000, "1", "1", "1", "100", "1", 1767311999999]]).encode()
        with mock.patch.object(fetch_series, "get", side_effect=fake):
            rows, report = fetch_series.build("2026-01-01", "2026-01-03")
        self.assertFalse(report["ndx"]["ok"])
        self.assertNotIn("ndx", rows[0])
        self.assertEqual(rows[0]["btc"], 100.0)


if __name__ == "__main__":
    unittest.main()
