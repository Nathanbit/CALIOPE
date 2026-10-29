"""`due`: what needs attention, read-only."""
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import prediction_ledger as pl  # noqa: E402

T0 = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def rule(start):
    return {"kind": "touch", "symbol": "BTCUSDT", "interval": "15m", "start": start,
            "level": 79600, "side": "at_or_below"}


class DueTest(unittest.TestCase):
    def test_closed_opening_and_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "predictions" / "ledger.jsonl"
            path.parent.mkdir()
            with mock.patch.object(pl, "now_utc", return_value=T0):
                closed = pl.ask(path, "closed", "2026-10-24T14:00:00Z", "s", "c",
                                rule=rule("2026-10-24T12:15:00Z"))["question_id"]
                pl.forecast(path, closed, "nathan", 0.2, "r")
                soon = pl.ask(path, "soon", "2026-10-25T12:00:00Z", "s", "c",
                              rule=rule("2026-10-25T06:00:00Z"))["question_id"]
                later = pl.ask(path, "later", "2026-10-28T00:00:00Z", "s", "c",
                               rule=rule("2026-10-27T00:00:00Z"))["question_id"]
                running = pl.ask(path, "running", "2026-10-26T00:00:00Z", "s", "c",
                                 rule=rule("2026-10-24T12:30:00Z"))["question_id"]
            now = datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)
            out = pl.due(pl.load(path), "projects/macroflow/predictions/ledger.jsonl", now=now)
        [r] = out["to_resolve"]
        self.assertEqual((r["question_id"], r["authors"], r["closed_hours_ago"]), (closed, ["nathan"], 11.0))
        self.assertIn("--interval 15m --start 2026-10-24T12:15:00Z --end 2026-10-24T14:00:00Z", r["commands"][0])
        self.assertIn(f"resolve-auto --question {closed}", r["commands"][1])
        self.assertIn("projects/macroflow/predictions/data/", r["commands"][0])
        self.assertEqual([(q["question_id"], q["opens_in_minutes"]) for q in out["opening_soon"]], [(soon, 300)])
        self.assertNotIn(later, [q["question_id"] for q in out["opening_soon"] + out["running"]])
        self.assertEqual([q["question_id"] for q in out["running"]], [running])


if __name__ == "__main__":
    unittest.main()
