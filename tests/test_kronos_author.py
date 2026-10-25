"""Kronos as an author: paths drawn one by one and judged like real candles (fake model)."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "labs" / "kronos_author"))

import kronos_core as kc  # noqa: E402
import prediction_ledger as pl  # noqa: E402
import propose  # noqa: E402

STEP = 900_000


def klines(last_open, n=200, price=81000.0, interval="15m"):
    rows = [[last_open - i * STEP, str(price), str(price + 50), str(price - 50), str(price), "1",
             last_open - i * STEP + STEP - 1] for i in range(n - 1, -1, -1)]
    return {"symbol": "BTCUSDT", "interval": interval, "fetched_at": "x", "klines": rows}


def rule_at(start_ms):
    return {"kind": "touch", "symbol": "BTCUSDT", "interval": "15m",
            "start": datetime.fromtimestamp(start_ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z"),
            "level": 80000, "side": "at_or_below"}


NOW = int(datetime(2026, 10, 24, 12, 5, tzinfo=timezone.utc).timestamp() * 1000)
LAST = NOW - NOW % STEP - STEP          # 11:45, closed at 11:59:59.999
START = LAST + 3 * STEP                 # 12:30
H = "2026-10-24T13:30:00Z"              # 4 window candles


class HistoryTest(unittest.TestCase):
    def test_history_ends_now_and_future_runs_to_the_horizon(self):
        history, future = kc.history_and_future(rule_at(START), H, klines(LAST), NOW, 100)
        self.assertEqual((len(history), history[-1]["open_ms"]), (100, LAST))
        self.assertEqual(future[0], LAST + STEP)
        self.assertEqual(len(future), 2 + 4)  # two lead candles, then the window

    def test_refusals(self):
        with self.assertRaisesRegex(kc.KronosAuthorError, "already started"):
            kc.history_and_future(rule_at(START), H, klines(LAST), START, 100)
        with self.assertRaisesRegex(kc.KronosAuthorError, "too long before now"):
            kc.history_and_future(rule_at(START), H, klines(LAST - 4 * STEP), NOW, 100)
        with self.assertRaisesRegex(kc.KronosAuthorError, "symbol and interval"):
            kc.history_and_future(rule_at(START), H, klines(LAST, interval="1m"), NOW, 100)
        with self.assertRaisesRegex(kc.KronosAuthorError, "contiguous"):
            kc.history_and_future(rule_at(START), H, klines(LAST, n=10), NOW, 100)


class JudgeTest(unittest.TestCase):
    def setUp(self):
        _, self.future = kc.history_and_future(rule_at(START), H, klines(LAST), NOW, 100)
        self.rule = rule_at(START)

    def path(self, lows):
        return [(81000, 81050, low, 81000) for low in lows]

    def test_only_the_window_counts(self):
        lead_touch = self.path([79000, 79000, 81000, 81000, 81000, 81000])
        self.assertEqual(kc.judge_path(self.rule, self.future, lead_touch, START), "false")
        window_touch = self.path([81000, 81000, 81000, 79900, 81000, 81000])
        self.assertEqual(kc.judge_path(self.rule, self.future, window_touch, START), "true")

    def test_inconsistent_candles_are_made_consistent(self):
        # low above close: the close itself touched the level
        weird = [(81000, 81050, 81000, 81000)] * 2 + [(81000, 81050, 81000, 79950)] + [(81000,) * 4] * 3
        self.assertEqual(kc.judge_path(self.rule, self.future, weird, START), "true")

    def test_probability_counts_paths_and_skips_voids(self):
        hit = self.path([81000, 81000, 79000, 81000, 81000, 81000])
        miss = self.path([81000] * 6)
        paths = iter([hit, hit, hit, miss] * 10)
        out = kc.probability(self.rule, H, self.future, lambda n: [next(paths) for _ in range(n)], 40, 7)
        self.assertEqual((out["probability"], out["true"], out["false"], out["paths"]), (0.75, 30, 10, 40))
        with self.assertRaisesRegex(kc.KronosAuthorError, "different number"):
            kc.probability(self.rule, H, self.future, lambda n: [miss], 4, 2)
        tf = {"kind": "touch_first", "symbol": "BTCUSDT", "interval": "15m", "start": self.rule["start"],
              "lower": 80000, "upper": 82000, "true_if": "lower_first"}
        with self.assertRaisesRegex(kc.KronosAuthorError, "void"):
            kc.probability(tf, H, self.future, lambda n: [miss] * n, 4, 2)


class ProposeTest(unittest.TestCase):
    def test_end_to_end_with_a_fake_model_proposes_a_valid_forecast(self):
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        last = now - now % STEP - STEP
        start = last + 4 * STEP
        horizon = datetime.fromtimestamp((start + 4 * STEP) / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
        with tempfile.TemporaryDirectory() as tmp:
            ledger, data = Path(tmp) / "l.jsonl", Path(tmp) / "k.json"
            data.write_text(json.dumps(klines(last)), encoding="utf-8")
            qid = pl.ask(ledger, "q", horizon, "s", "c", rule=rule_at(start))["question_id"]
            hit = [(81000, 81050, 79000, 81000)] * 7
            miss = [(81000, 81050, 80950, 81000)] * 7
            fake = lambda args, history, future: (lambda n: [hit if i % 4 else miss for i in range(n)])  # noqa: E731
            with mock.patch.object(propose, "kronos_sampler", fake), \
                    mock.patch("sys.stdout") as out:
                code = propose.main(["--ledger", str(ledger), "--question", qid, "--klines", str(data),
                                     "--paths", "40", "--batch", "8"])
            self.assertEqual(code, 0)
            report = json.loads("".join(c.args[0] for c in out.write.call_args_list))
            self.assertEqual(report["probability"], 0.75)
            self.assertIn("--author ia:kronos --probability 0.75", report["command"])
            rec = pl.forecast(ledger, qid, "ia:kronos", report["probability_to_seal"], "r")
            self.assertEqual(rec["author"], "ia:kronos")


if __name__ == "__main__":
    unittest.main()
