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
import backtest  # noqa: E402
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


def candles_file(n, start_ms=0, price=81000.0, drift=0.0, interval="15m"):
    rows, p = [], price
    for i in range(n):
        o = p
        p = p + drift
        t = start_ms + i * STEP
        rows.append([t, str(o), str(max(o, p) + 10), str(min(o, p) - 10), str(p), "1", t + STEP - 1])
    return {"symbol": "BTCUSDT", "interval": interval, "fetched_at": "x", "klines": rows}


class FanTest(unittest.TestCase):
    """The fan: value quantiles, kept paths and the replay that checks Kronos."""

    def test_value_question_gives_quantiles_instead_of_crashing(self):
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        last = now - now % STEP - STEP
        start = last + 4 * STEP
        horizon = datetime.fromtimestamp((start + 4 * STEP) / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
        rule = {"kind": "close_value", "symbol": "BTCUSDT", "interval": "15m",
                "start": datetime.fromtimestamp(start / 1000, timezone.utc).isoformat().replace("+00:00", "Z")}
        with tempfile.TemporaryDirectory() as tmp:
            ledger, data = Path(tmp) / "l.jsonl", Path(tmp) / "k.json"
            data.write_text(json.dumps(klines(last)), encoding="utf-8")
            qid = pl.ask(ledger, "q", horizon, "s", "c", rule=rule)["question_id"]

            closes = iter(79000.0 + 100 * i for i in range(21))

            def fake(args, history, future):
                return lambda n: [[(80000.0, 80000.0, 80000.0, next(closes))] * len(future) for _ in range(n)]
            name = "test_fan_paths.json"
            with mock.patch.object(propose, "kronos_sampler", fake), mock.patch("sys.stdout") as out:
                code = propose.main(["--ledger", str(ledger), "--question", qid, "--klines", str(data),
                                     "--paths", "21", "--batch", "7", "--save-paths", name])
            saved = kc.ROOT / "runs" / "kronos" / name
            try:
                self.assertEqual(code, 0)
                report = json.loads("".join(c.args[0] for c in out.write.call_args_list))
                q = report["quantiles"]
                self.assertEqual((q["0.1"], q["0.5"], q["0.9"]), (79200.0, 80000.0, 80800.0))
                self.assertIn("--quantiles", report["command"])
                kept = json.loads(saved.read_text(encoding="utf-8"))
                self.assertEqual(len(kept["paths"]), 21)                  # every path kept
                rec = pl.forecast(ledger, qid, "ia:kronos", None, "r", quantiles=q)
                self.assertEqual(rec["quantiles"]["0.5"], 80000.0)
            finally:
                saved.unlink(missing_ok=True)

    def test_saved_paths_stay_inside_runs_kronos(self):
        for bad in ("../x.json", "../../ledger.jsonl", "x.txt", "/tmp/x.json"):
            with self.assertRaises(kc.KronosAuthorError, msg=bad):
                propose.runs_path(bad)
        self.assertEqual(propose.runs_path("a.json").parent, (kc.ROOT / "runs" / "kronos").resolve())

    def test_draw_and_quantiles_refuse_bad_shapes(self):
        with self.assertRaises(kc.KronosAuthorError):
            kc.draw([1, 2], lambda n: [[(1, 1, 1, 1)]] * n, 4, 2)       # one candle short
        with self.assertRaises(kc.KronosAuthorError):
            kc.draw([1], lambda n: [[(1, 1, 1, 1)]] * (n - 1), 4, 2)    # one path short
        with self.assertRaises(kc.KronosAuthorError):
            kc.final_close_quantiles([[(1, 1, 1, 1)]] * 19)

    def replay(self, sampler_for, **kw):
        data = candles_file(128 + 48 * 3 + 5, drift=5.0)
        args = dict(lookback=128, horizon=48, paths=20, batch=10, windows=10)
        args.update(kw)
        return data, kc.replay(data, sampler_for, **args)

    def test_a_perfect_model_covers_every_window_and_sees_only_the_past(self):
        candles = kc.closed_candles(candles_file(128 + 48 * 3 + 5, drift=5.0))
        by_open = {c["open_ms"]: c for c in candles}

        def oracle(history, future):
            self.assertLess(history[-1]["open_ms"], future[0])          # no peeking
            real = [(by_open[t]["open"], by_open[t]["high"], by_open[t]["low"], by_open[t]["close"]) for t in future]
            return lambda n: [real] * n
        _, report = self.replay(oracle)
        self.assertEqual(report["windows"], 3)                           # non-overlapping only
        self.assertEqual(report["coverage"], 1.0)
        self.assertIn("warning", report)                                 # 3 < 30

    def test_a_bearish_model_misses_a_rally_and_stop_target_is_scored(self):
        def bearish(history, future):
            c = history[-1]["close"]
            down = [(c - 5 * k, c - 5 * k + 1, c - 5 * (k + 1), c - 5 * (k + 1)) for k in range(len(future))]
            return lambda n: [down] * n
        _, report = self.replay(bearish, stop_pct=0.001, target_pct=0.002)
        self.assertEqual(report["coverage"], 0.0)                        # it went up, the fan went down
        st = report["stop_target"]
        self.assertTrue(all(r["real"] == "true" for r in report["rows"]))  # drift up: target first
        self.assertEqual(st["scored"], 3)
        self.assertEqual(st["brier"], 1.0)                               # every path said stop first

    def test_paths_that_touch_neither_level_are_not_scored(self):
        def flat(history, future):
            c = history[-1]["close"]
            return lambda n: [[(c, c + 1, c - 1, c)] * len(future)] * n
        _, report = self.replay(flat, stop_pct=0.01, target_pct=0.02)
        self.assertEqual(report["stop_target"]["scored"], 0)
        self.assertIsNone(report["stop_target"]["brier"])

    def test_backtest_cli_reports_coverage_and_writes_only_to_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "k.json"
            data.write_text(json.dumps(candles_file(128 + 48 * 2)), encoding="utf-8")
            flat = lambda args: (lambda h, f: (lambda n: [[(1.0, 1.0, 1.0, 1.0)] * len(f)] * n))  # noqa: E731
            name = "test_backtest.json"
            with mock.patch.object(backtest, "make_sampler_for", flat), mock.patch("sys.stdout") as out:
                code = backtest.main(["--klines", str(data), "--paths", "20", "--out", name])
            saved = kc.ROOT / "runs" / "kronos" / name
            try:
                self.assertEqual(code, 0)
                report = json.loads("".join(c.args[0] for c in out.write.call_args_list))
                self.assertEqual((report["windows"], report["coverage"]), (2, 0.0))
                self.assertEqual(report["evidence_type"], "derivation")
                self.assertTrue(saved.exists())
            finally:
                saved.unlink(missing_ok=True)
            with mock.patch("sys.stdout"):
                self.assertEqual(backtest.main(["--klines", str(data), "--out", "../escape.json"]), 2)

    def test_replay_refusals(self):
        ok = lambda h, f: (lambda n: [[(1, 1, 1, 1)] * len(f)] * n)    # noqa: E731
        with self.assertRaises(kc.KronosAuthorError):
            self.replay(ok, stop_pct=0.01)                               # stop without target
        with self.assertRaises(kc.KronosAuthorError):
            kc.replay(candles_file(100), ok, lookback=128, horizon=48, paths=20, batch=10, windows=5)
        gappy = candles_file(128 + 48 + 5)
        del gappy["klines"][100]
        with self.assertRaises(kc.KronosAuthorError):                    # the only window has a gap
            kc.replay(gappy, ok, lookback=128, horizon=48, paths=20, batch=10, windows=5)


if __name__ == "__main__":
    unittest.main()
