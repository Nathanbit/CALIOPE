"""The volatility rival and the family-wise verdicts of ``rival``."""
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
START = int(datetime(2026, 10, 23, 12, 15, tzinfo=timezone.utc).timestamp() * 1000)
END = START + 4 * STEP
H = "2026-10-23T13:15:00Z"
RULE = {"kind": "touch", "symbol": "BTCUSDT", "interval": "15m", "start": "2026-10-23T12:15:00Z",
        "level": 82000, "side": "at_or_above"}
A = 0.002  # log move per candle before the window


def row(open_ms, close, high=None, low=None):
    high = high if high is not None else close
    low = low if low is not None else close
    return [open_ms, str(close), str(high), str(low), str(close), "1", open_ms + STEP - 1]


def klines(lookback=96, window_high=81500.0, gap=False):
    rows = []
    for i in range(lookback):
        if gap and i == 50:
            continue
        rows.append(row(START - (lookback - i) * STEP, 81000 * math.exp(A * (i % 2))))
    for k in range(4):
        rows.append(row(START + k * STEP, 81000.0, high=window_high, low=80900.0))
    return {"symbol": "BTCUSDT", "interval": "15m", "fetched_at": "2026-10-23T13:30:00Z",
            "klines": rows}


def expected_touch(level, spot, n_window=4, lookback=96):
    returns = [A if i % 2 else -A for i in range(1, lookback)]
    mean = sum(returns) / len(returns)
    sigma = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
    d = math.log(level / spot) / (sigma * math.sqrt(n_window))
    return 2 * (1 - 0.5 * (1 + math.erf(d / math.sqrt(2))))


class VolatilityRivalTest(unittest.TestCase):
    def test_touch_uses_the_reflection_principle(self):
        out = resolver.volatility_rival(RULE, H, klines())
        spot = 81000 * math.exp(A)  # the last candle before the window is odd
        self.assertTrue(out["available"])
        self.assertEqual((out["lookback_candles"], out["window_candles"]), (96, 4))
        self.assertAlmostEqual(out["reference_close"], spot)
        self.assertAlmostEqual(out["p"], round(expected_touch(82000, spot), 6), places=6)

    def test_the_window_never_leaks_in(self):
        calm = resolver.volatility_rival(RULE, H, klines(window_high=81500))
        touched = resolver.volatility_rival(RULE, H, klines(window_high=90000))
        self.assertEqual(calm, touched)

    def test_fails_closed_without_earlier_candles(self):
        for data in (klines(gap=True), klines(lookback=0)):
            out = resolver.volatility_rival(RULE, H, data)
            self.assertFalse(out["available"])
            self.assertIn("missing", out["reason"])

    def test_touch_first_is_the_gamblers_ruin_ratio(self):
        rule = {"kind": "touch_first", "symbol": "BTCUSDT", "interval": "15m",
                "start": "2026-10-23T12:15:00Z", "lower": 80000, "upper": 83000,
                "true_if": "lower_first"}
        out = resolver.volatility_rival(rule, H, klines())
        spot = 81000 * math.exp(A)
        self.assertAlmostEqual(out["p"], round(math.log(83000 / spot) / math.log(83000 / 80000), 6))

    def test_already_beyond_the_level_is_certain(self):
        out = resolver.volatility_rival(dict(RULE, level=80000, side="at_or_below"), H,
                                        klines())
        self.assertLess(out["p"], 0.5)
        below = resolver.volatility_rival(dict(RULE, level=81500, side="at_or_below"), H, klines())
        self.assertEqual(below["p"], 0.999)


class RivalFamilyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.jsonl"
        self.data = Path(self.tmp.name) / "k.json"
        import json
        self.data.write_text(json.dumps(klines(window_high=82100)), encoding="utf-8")
        self.clock = mock.patch.object(pl, "now_utc",
                                       return_value=datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc))
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.tmp.cleanup()

    def test_resolve_auto_stores_the_rival_and_rival_scores_against_it(self):
        concept = {"asset": "BTCUSDT", "event": "touch", "interval": "15m",
                   "start": "2026-10-23T12:15:00Z", "horizon_end": H, "reference_price": 81200,
                   "reference_source": "test", "level": 82000, "side": "at_or_above"}
        qid = pl.seal(self.path, concept, "nathan", 0.9, "r")["question"]["question_id"]
        pl.forecast(self.path, qid, "ia:gemini", 0.2, "r")
        with mock.patch.object(pl, "now_utc",
                               return_value=datetime(2026, 10, 23, 14, 0, tzinfo=timezone.utc)):
            resolution = pl.resolve_auto(self.path, qid, self.data)
        self.assertEqual(resolution["outcome"], "true")
        vol = resolution["observed"]["volatility_rival"]
        self.assertTrue(vol["available"])
        pl.verify(pl.load(self.path))
        report = fs.rival(pl.load(self.path), resamples=300)
        self.assertEqual(report["family_size"], 6)  # (nathan, ia:gemini, ~enjambre) x 2 rivals
        rows = {r["author"]: r for r in report["authors"]}
        self.assertEqual(rows["nathan"]["volatility"]["rival_brier"], round((vol["p"] - 1) ** 2, 6))
        for r in rows.values():
            low, high = r["ci_family"]
            self.assertLessEqual(low, r["ci95"][0])
            self.assertGreaterEqual(high, r["ci95"][1])
        with self.assertRaisesRegex(pl.GateError, "at least 240 for 6 verdict"):
            fs.rival(pl.load(self.path), resamples=230)


if __name__ == "__main__":
    unittest.main()
