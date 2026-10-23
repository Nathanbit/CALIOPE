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

START = "2026-10-21T18:17:00Z"
END = "2026-10-21T18:22:00Z"  # five 1m candles
T0 = resolver.to_ms(START, "t")


def klines(*hl, symbol="BTCUSDT", interval="1m", closes=None):
    rows = []
    for i, (high, low) in enumerate(hl):
        close = closes[i] if closes else (high + low) / 2
        rows.append([T0 + i * 60_000, str(low), str(high), str(low), str(close), "1"])
    return {"symbol": symbol, "interval": interval, "source": "test", "fetched_at": END,
            "klines": rows}


def touch_first(true_if="lower_first"):
    return {"kind": "touch_first", "symbol": "BTCUSDT", "interval": "1m", "start": START,
            "lower": 84000, "upper": 85200, "true_if": true_if}


FLAT = [(84500, 84300)] * 5


class ResolverTest(unittest.TestCase):
    def test_touch_first_outcomes(self):
        data = klines(*FLAT[:2], (84400, 83990), (85300, 84100), FLAT[0])
        outcome, ev = resolver.evaluate(touch_first(), END, data)
        self.assertEqual((outcome, ev["first"]), ("true", "lower_first"))
        self.assertEqual(ev["decisive_candle"], "2026-10-21T18:19:00Z")
        self.assertEqual(resolver.evaluate(touch_first("upper_first"), END, data)[0], "false")

    def test_neither_touched_is_void_like_the_real_case(self):
        outcome, ev = resolver.evaluate(touch_first(), END, klines(*FLAT[:4], (84876.01, 84057.59)))
        self.assertEqual(outcome, "void")
        self.assertIn("neither", ev["void_reason"])
        self.assertEqual((ev["high"], ev["low"]), (84876.01, 84057.59))

    def test_both_levels_in_one_candle_is_void_not_guessed(self):
        outcome, ev = resolver.evaluate(touch_first(), END, klines(*FLAT[:2], (85300, 83900), *FLAT[:2]))
        self.assertEqual(outcome, "void")
        self.assertIn("one candle", ev["void_reason"])

    def test_gaps_and_mismatches_fail_closed(self):
        gapped = klines(*FLAT)
        del gapped["klines"][2]
        with self.assertRaisesRegex(resolver.ResolutionError, "missing"):
            resolver.evaluate(touch_first(), END, gapped)
        with self.assertRaisesRegex(resolver.ResolutionError, "missing"):
            resolver.evaluate(touch_first(), END, klines(*FLAT[:3]))
        with self.assertRaisesRegex(resolver.ResolutionError, "symbol"):
            resolver.evaluate(touch_first(), END, klines(*FLAT, symbol="ETHUSDT"))
        with self.assertRaisesRegex(resolver.ResolutionError, "interval"):
            resolver.evaluate(touch_first(), END, klines(*FLAT, interval="5m"))
        bad = klines(*FLAT)
        bad["klines"][1][2] = "nan"
        with self.assertRaisesRegex(resolver.ResolutionError, "invalid prices"):
            resolver.evaluate(touch_first(), END, bad)

    def test_rule_validation(self):
        with self.assertRaisesRegex(resolver.ResolutionError, "unexpected"):
            resolver.validate_rule(dict(touch_first(), extra=1), END)
        with self.assertRaisesRegex(resolver.ResolutionError, "align"):
            resolver.validate_rule(dict(touch_first(), start="2026-10-21T18:17:30Z"), END)
        with self.assertRaisesRegex(resolver.ResolutionError, "below"):
            resolver.validate_rule(dict(touch_first(), lower=86000), END)
        with self.assertRaisesRegex(resolver.ResolutionError, "timezone"):
            resolver.validate_rule(dict(touch_first(), start="2026-10-21T18:17:00"), END)

    def test_touch_and_close_beyond(self):
        touch = {"kind": "touch", "symbol": "BTCUSDT", "interval": "1m", "start": START,
                 "level": 84000, "side": "at_or_below"}
        self.assertEqual(resolver.evaluate(touch, END, klines(*FLAT))[0], "false")
        self.assertEqual(resolver.evaluate(touch, END, klines(*FLAT[:4], (84100, 84000)))[0], "true")
        close = {"kind": "close_beyond", "symbol": "BTCUSDT", "interval": "1m", "start": START,
                 "level": 84200, "side": "below"}
        wick_only = klines(*FLAT[:4], (84400, 84100), closes=[84400] * 5)
        outcome, ev = resolver.evaluate(close, END, wick_only)
        self.assertEqual(outcome, "false", "a wick below is not a close below")
        self.assertEqual(ev["extreme_close"], 84400)

    def test_fetch_pages_cover_window_without_overlap(self):
        urls = fetch_klines.page_urls("BTCUSDT", "1m", T0, T0 + 2500 * 60_000)
        self.assertEqual(len(urls), 3)
        self.assertIn(f"startTime={T0 + 1000 * 60_000}", urls[1])
        self.assertIn(f"endTime={T0 + 2500 * 60_000 - 1}", urls[2])


class AutoResolutionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.tmp.name) / "ledger.jsonl"
        self.data = Path(self.tmp.name) / "k.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_rule_is_part_of_question_identity(self):
        a = pl.question_id("t", END, "s", "c")
        b = pl.question_id("t", END, "s", "c", touch_first())
        self.assertNotEqual(a, b)
        self.assertNotEqual(b, pl.question_id("t", END, "s", "c", dict(touch_first(), upper=85000)))

    def test_invalid_rule_is_refused_at_ask(self):
        with self.assertRaisesRegex(pl.GateError, "align"):
            pl.ask(self.ledger, "t", END, "s", "c",
                   rule=dict(touch_first(), start="2026-10-21T18:17:30Z"))

    def test_end_to_end_resolution_records_data_hash(self):
        clock = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)
        with mock.patch.object(pl, "now_utc", return_value=clock):
            qid = pl.ask(self.ledger, "t", END, "s", "c", rule=touch_first())["question_id"]
            pl.forecast(self.ledger, qid, "claude", 0.55, "r")
        self.data.write_text(json.dumps(klines(*FLAT[:2], (84400, 83990), *FLAT[:2])), encoding="utf-8")
        with self.assertRaisesRegex(pl.GateError, "before horizon_end"):
            with mock.patch.object(pl, "now_utc", return_value=clock):
                pl.resolve_auto(self.ledger, qid, self.data)
        later = datetime(2026, 10, 21, 18, 30, tzinfo=timezone.utc)
        with mock.patch.object(pl, "now_utc", return_value=later):
            rec = pl.resolve_auto(self.ledger, qid, self.data)
        self.assertEqual(rec["outcome"], "true")
        self.assertEqual(len(rec["observed"]["data_sha256"]), 64)
        self.assertEqual(rec["observed"]["resolver_version"], resolver.RESOLVER_VERSION)
        pl.verify(pl.load(self.ledger))

    def test_question_without_rule_needs_manual_resolution(self):
        clock = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)
        with mock.patch.object(pl, "now_utc", return_value=clock):
            qid = pl.ask(self.ledger, "t", END, "s", "c")["question_id"]
        with self.assertRaisesRegex(pl.GateError, "no machine rule"):
            pl.resolve_auto(self.ledger, qid, self.data)


if __name__ == "__main__":
    unittest.main()
