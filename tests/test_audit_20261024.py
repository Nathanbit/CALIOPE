"""Regressions for the audit of 2026-10-24 (PRs #19-#33)."""
import json
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

NOW = datetime(2026, 10, 23, 13, 0, tzinfo=timezone.utc)
T0 = int(datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)


def pair(author, qid, y):
    return {"author": author, "p": 0.5, "y": y, "question_id": qid, "concept_class": "C",
            "seq": 1, "base_rate": None, "lessons": []}


class ClassRatesCountQuestionsTest(unittest.TestCase):
    def test_four_authors_on_one_question_weigh_once(self):
        pairs = [pair(a, "qa", 1.0) for a in ("n", "c", "g", "h")] + [pair("n", "qb", 0.0)]
        [row] = fs.by_concept(pairs)
        self.assertEqual((row["n"], row["forecasts"], row["observed_rate"]), (2, 5, 0.5))


class ThoughtStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "l.jsonl"
        self.clock = mock.patch.object(pl, "now_utc", return_value=NOW)
        self.clock.start()
        self.qid = pl.ask(self.path, "A", "2026-10-24T00:00:00Z", "s", "c")["question_id"]

    def tearDown(self):
        self.clock.stop()
        self.tmp.cleanup()

    def append(self, body):
        records = pl.load(self.path)
        body = dict(body, seq=len(records) + 1, prev_hash=records[-1]["hash"])
        body["hash"] = pl.sha256_text(pl.canonical(body))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(pl.canonical(body) + "\n")

    def test_a_live_forecast_cannot_omit_its_state(self):
        body = pl._forecast_body(self.qid, "ia:grok", 0.4, "r", None, None, None)  # no state
        self.append(body)
        with self.assertRaisesRegex(pl.GateError, "must carry its thought_state"):
            pl.verify(pl.load(self.path))

    def test_old_versions_are_recomputed_with_their_own_rules(self):
        state = pl.verify(pl.load(self.path))
        old = fs.thought_state(state, "nathan", None, version="1.0")
        body = dict(pl._forecast_body(self.qid, "nathan", 0.4, "r", None, None, None),
                    thought_state=old)
        self.append(body)
        pl.verify(pl.load(self.path))
        with self.assertRaisesRegex(ValueError, "unknown thought_state version"):
            fs.thought_state(state, "nathan", None, version="9.9")

    def test_new_states_use_the_current_version(self):
        rec = pl.forecast(self.path, self.qid, "nathan", 0.4, "r")
        self.assertEqual(rec["thought_state"]["version"], fs.THOUGHT_VERSION)


class TieNeedsFinerDataTest(unittest.TestCase):
    def test_the_resolver_cannot_choose_void_by_omitting_finer_data(self):
        rule = {"kind": "touch_first", "symbol": "BTCUSDT", "interval": "1m",
                "start": "2026-10-24T15:00:00Z", "lower": 79600, "upper": 80600,
                "true_if": "lower_first"}
        data = {"symbol": "BTCUSDT", "interval": "1m", "fetched_at": "2026-10-24T15:05:00Z",
                "klines": [[T0, "80000", "80700", "79500", "80100", "1", T0 + 59_999]]}
        with tempfile.TemporaryDirectory() as tmp:
            path, coarse = Path(tmp) / "l.jsonl", Path(tmp) / "c.json"
            coarse.write_text(json.dumps(data), encoding="utf-8")
            with mock.patch.object(pl, "now_utc",
                                   return_value=datetime(2026, 10, 24, 14, 50, tzinfo=timezone.utc)):
                qid = pl.ask(path, "tie", "2026-10-24T15:01:00Z", "s", "c", rule=rule)["question_id"]
                pl.forecast(path, qid, "nathan", 0.6, "r")
            with mock.patch.object(pl, "now_utc",
                                   return_value=datetime(2026, 10, 24, 15, 6, tzinfo=timezone.utc)):
                with self.assertRaisesRegex(pl.GateError, "--fine-klines"):
                    pl.resolve_auto(path, qid, coarse)
                res = pl.resolve(path, qid, "void", {"source": "no 1s data available"},
                                 void_reason="tie inside one 1m candle; no finer data")
            self.assertEqual(res["outcome"], "void")


if __name__ == "__main__":
    unittest.main()
