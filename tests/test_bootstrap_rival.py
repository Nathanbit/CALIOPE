"""The bootstrap rival (idea: trader-timemachine): replays of past candles by time of day."""
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

import forecast_scoring as fs  # noqa: E402
import prediction_ledger as pl  # noqa: E402
import resolver  # noqa: E402

STEP = 900_000
DAY = 86_400_000
START = int(datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
H = "2026-10-23T14:00:00Z"  # 8 candles of 15m
RULE = {"kind": "touch", "symbol": "BTCUSDT", "interval": "15m", "start": "2026-10-23T12:00:00Z",
        "level": 80000, "side": "at_or_below"}


def candles(days=30, drop_slot=None, drop=0.0, window_low=81000.0):
    """Flat 81,000 with a tiny wiggle; optionally a big wick down at one time of day."""
    rows, price = [], 81000.0
    for i in range(days * DAY // STEP, 0, -1):
        t = START - i * STEP
        slot = (t % DAY) // STEP
        low = price * (1 - drop) if slot == drop_slot else price * 0.999
        rows.append([t, str(price), str(price * 1.001), str(low), str(price), "1", t + STEP - 1])
    for k in range(8):
        t = START + k * STEP
        rows.append([t, "81000", "81100", str(window_low), "81000", "1", t + STEP - 1])
    return {"symbol": "BTCUSDT", "interval": "15m", "fetched_at": "2026-10-23T15:00:00Z", "klines": rows}


class BootstrapRivalTest(unittest.TestCase):
    def test_deterministic_and_blind_to_the_window(self):
        a = resolver.bootstrap_rival(RULE, H, candles(window_low=81000))
        b = resolver.bootstrap_rival(RULE, H, candles(window_low=70000))
        self.assertTrue(a["available"])
        self.assertEqual(a, b)
        self.assertEqual(a["pool_days"], 30.0)

    def test_needs_a_week_of_earlier_candles(self):
        out = resolver.bootstrap_rival(RULE, H, candles(days=3))
        self.assertFalse(out["available"])
        self.assertIn("7 days", out["reason"])

    def test_calm_history_says_unlikely_and_wicks_say_likely(self):
        calm = resolver.bootstrap_rival(RULE, H, candles())
        self.assertEqual(calm["p"], 0.001)  # 1.2% away, candles never move more than 0.1%
        wicky = resolver.bootstrap_rival(RULE, H, candles(drop_slot=(START % DAY) // STEP + 2, drop=0.02))
        # the wick lives in 1 of the 5 neighbouring slots, met by 5 window candles: 1 - 0.8**5
        self.assertAlmostEqual(wicky["p"], 1 - 0.8 ** 5, delta=0.04)

    def test_time_of_day_matters(self):
        near = (START % DAY) // STEP + 2          # inside the window's hours
        far = (near + 48) % (DAY // STEP)         # twelve hours away
        self.assertGreater(resolver.bootstrap_rival(RULE, H, candles(drop_slot=near, drop=0.02))["p"],
                           resolver.bootstrap_rival(RULE, H, candles(drop_slot=far, drop=0.02))["p"])

    def test_touch_first_void_replays_do_not_count(self):
        rule = {"kind": "touch_first", "symbol": "BTCUSDT", "interval": "15m",
                "start": "2026-10-23T12:00:00Z", "lower": 80930, "upper": 81070, "true_if": "lower_first"}
        both = resolver.bootstrap_rival(rule, H, candles())  # every candle touches both levels
        self.assertFalse(both["available"])
        self.assertIn("void", both["reason"])
        lower_only = resolver.bootstrap_rival(dict(rule, upper=81200), H, candles())
        self.assertEqual((lower_only["true"], lower_only["void"], lower_only["p"]),
                         (lower_only["replays"], 0, 0.999))


class StoredAndScoredTest(unittest.TestCase):
    def test_resolve_auto_stores_it_and_rival_judges_against_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, data = Path(tmp) / "l.jsonl", Path(tmp) / "k.json"
            data.write_text(json.dumps(candles(drop_slot=(START % DAY) // STEP + 2, drop=0.02,
                                               window_low=79900)), encoding="utf-8")
            concept = {"asset": "BTCUSDT", "event": "touch", "interval": "15m",
                       "start": "2026-10-23T12:00:00Z", "horizon_end": H, "reference_price": 81000,
                       "reference_source": "t", "level": 80000, "side": "at_or_below"}
            with mock.patch.object(pl, "now_utc", return_value=datetime(2026, 10, 23, 11, 50, tzinfo=timezone.utc)):
                qid = pl.seal(path, concept, "nathan", 0.3, "r")["question"]["question_id"]
            with mock.patch.object(pl, "now_utc", return_value=datetime(2026, 10, 23, 15, 5, tzinfo=timezone.utc)):
                res = pl.resolve_auto(path, qid, data)
            boot = res["observed"]["bootstrap_rival"]
            self.assertTrue(boot["available"])
            pl.verify(pl.load(path))
            report = fs.rival(pl.load(path), resamples=500)
            self.assertFalse(res["observed"]["volatility_rival"]["available"])  # flat closes
            self.assertEqual(report["family_size"], 2)  # one author x (history, bootstrap)
            [row] = report["authors"]
            self.assertEqual(row["bootstrap"]["rival_brier"], round((boot["p"] - 1) ** 2, 6))


if __name__ == "__main__":
    unittest.main()
