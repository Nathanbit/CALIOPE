"""Second-level candles: 1s from Binance, 5s-30s aggregated, and the touch_first tie-break."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import fetch_klines  # noqa: E402
import prediction_ledger as pl  # noqa: E402
import resolver  # noqa: E402

T0 = int(datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)


def sec(i, o, h, lo, c, vol="1"):
    return [T0 + i * 1000, str(o), str(h), str(lo), str(c), vol, T0 + i * 1000 + 999]


class AggregateTest(unittest.TestCase):
    def test_ohlcv_of_each_bucket(self):
        rows = [sec(i, 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(10)]
        out = resolver.aggregate(rows, 5_000)
        self.assertEqual(len(out), 2)
        first = out[0]
        self.assertEqual(first[0], T0)
        self.assertEqual([float(v) for v in first[1:6]], [100, 105, 99, 104.5, 5])
        self.assertEqual(first[6], T0 + 4_999)

    def test_a_missing_second_drops_the_bucket(self):
        rows = [sec(i, 100, 101, 99, 100) for i in range(10) if i != 7]
        self.assertEqual([r[0] for r in resolver.aggregate(rows, 5_000)], [T0])

    def test_only_true_1s_input(self):
        with self.assertRaisesRegex(resolver.ResolutionError, "1s candles"):
            resolver.aggregate([[T0, "1", "1", "1", "1", "1", T0 + 59_999]], 5_000)
        with self.assertRaisesRegex(resolver.ResolutionError, "duplicate"):
            resolver.aggregate([sec(0, 1, 1, 1, 1), sec(0, 1, 1, 1, 1)], 5_000)

    def test_second_intervals_validate_and_align(self):
        rule = {"kind": "touch", "symbol": "BTCUSDT", "interval": "30s",
                "start": "2026-10-24T15:00:30Z", "level": 80000, "side": "at_or_below"}
        self.assertEqual(resolver.validate_rule(rule, "2026-10-24T15:10:00Z")["interval"], "30s")
        with self.assertRaisesRegex(resolver.ResolutionError, "align"):
            resolver.validate_rule(dict(rule, start="2026-10-24T15:00:15Z"), "2026-10-24T15:10:00Z")

    def test_fetch_klines_builds_seconds_from_1s(self):
        rows = [sec(i, 100, 101, 99, 100) for i in range(30)]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(fetch_klines, "ROOT", Path(tmp).resolve()), \
                mock.patch.object(fetch_klines, "download", return_value=rows) as download:
            code = fetch_klines.main(["--symbol", "BTCUSDT", "--interval", "15s",
                                      "--start", "2026-10-24T15:00:00Z", "--end", "2026-10-24T15:00:30Z",
                                      "--lookback-hours", "0",
                                      "--out", "projects/x/predictions/data/k.json"])
            self.assertEqual(code, 0)
            self.assertIn("interval=1s", download.call_args.args[0])
            payload = json.loads((Path(tmp) / "projects/x/predictions/data/k.json").read_text())
        self.assertEqual(payload["interval"], "15s")
        self.assertEqual(len(payload["klines"]), 2)
        self.assertIn("1s aggregated to 15s", payload["source"])


class TiebreakTest(unittest.TestCase):
    RULE = {"kind": "touch_first", "symbol": "BTCUSDT", "interval": "1m",
            "start": "2026-10-24T15:00:00Z", "lower": 79600, "upper": 80600, "true_if": "lower_first"}
    H = "2026-10-24T15:02:00Z"

    def coarse(self):
        rows = [[T0, "80000", "80700", "79500", "80100", "1", T0 + 59_999],
                [T0 + 60_000, "80100", "80200", "80000", "80100", "1", T0 + 119_999]]
        return {"symbol": "BTCUSDT", "interval": "1m", "fetched_at": "2026-10-24T15:05:00Z",
                "klines": rows}

    def fine(self, first_low_at=12, high_at=40, both_at=None, gap=False):
        rows = []
        for i in range(60):
            if gap and i == 30:
                continue
            low, high = 79900, 80300
            if i == first_low_at:
                low = 79500
            if i == high_at:
                high = 80700
            if both_at == i:
                low, high = 79500, 80700
            rows.append(sec(i, 80000, high, low, 80000))
        return {"symbol": "BTCUSDT", "interval": "1s", "fetched_at": "2026-10-24T15:05:00Z",
                "klines": rows}

    def test_without_finer_data_the_tie_is_void(self):
        outcome, evidence = resolver.evaluate(self.RULE, self.H, self.coarse())
        self.assertEqual(outcome, "void")
        self.assertIn("one candle", evidence["void_reason"])

    def test_the_second_that_touched_first_decides(self):
        _, evidence = resolver.evaluate(self.RULE, self.H, self.coarse())
        outcome, extra = resolver.tiebreak(self.RULE, self.H, evidence, self.fine())
        self.assertEqual((outcome, extra["first"], extra["tiebreak_candle"]),
                         ("true", "lower_first", "2026-10-24T15:00:12Z"))
        outcome, _ = resolver.tiebreak(self.RULE, self.H, evidence, self.fine(first_low_at=50))
        self.assertEqual(outcome, "false")

    def test_still_unknowable_or_incomplete_stays_void_or_refused(self):
        _, evidence = resolver.evaluate(self.RULE, self.H, self.coarse())
        fine = self.fine(first_low_at=-1, high_at=-1, both_at=20)
        outcome, extra = resolver.tiebreak(self.RULE, self.H, evidence, fine)
        self.assertEqual(outcome, "void")
        self.assertIn("1s candle too", extra["void_reason"])
        with self.assertRaisesRegex(resolver.ResolutionError, "missing"):
            resolver.tiebreak(self.RULE, self.H, evidence, self.fine(gap=True))
        with self.assertRaisesRegex(resolver.ResolutionError, "finer interval"):
            resolver.tiebreak(self.RULE, self.H, evidence, dict(self.fine(), interval="1m"))
        with self.assertRaisesRegex(resolver.ResolutionError, "disagree"):
            resolver.tiebreak(self.RULE, self.H, evidence, self.fine(first_low_at=-1, high_at=-1))

    def test_resolve_auto_records_the_tiebreak(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, coarse, fine = (Path(tmp) / n for n in ("l.jsonl", "c.json", "f.json"))
            coarse.write_text(json.dumps(self.coarse()), encoding="utf-8")
            fine.write_text(json.dumps(self.fine()), encoding="utf-8")
            with mock.patch.object(pl, "now_utc", return_value=datetime(2026, 10, 24, 14, 50, tzinfo=timezone.utc)):
                qid = pl.ask(path, "tie", self.H, "s", "c", rule=self.RULE)["question_id"]
                pl.forecast(path, qid, "nathan", 0.6, "r")
            with mock.patch.object(pl, "now_utc", return_value=datetime(2026, 10, 24, 15, 6, tzinfo=timezone.utc)):
                res = pl.resolve_auto(path, qid, coarse, fine)
            self.assertEqual(res["outcome"], "true")
            self.assertEqual(res["observed"]["tiebreak_interval"], "1s")
            self.assertNotIn("void_reason", res["observed"])
            self.assertEqual(len(res["observed"]["tiebreak_data_sha256"]), 64)
            pl.verify(pl.load(path))


if __name__ == "__main__":
    unittest.main()
