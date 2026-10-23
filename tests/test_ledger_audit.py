"""Regression tests for the 2026-10-23 Thermos audit: each test replays an
attack the reviewers confirmed against the previous version."""
import json
import multiprocessing
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
import forecast_scoring as fs  # noqa: E402
import prediction_ledger as pl  # noqa: E402
import resolver  # noqa: E402

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
H = "2026-10-22T00:15:00Z"
SEAL = {"sha256": "a" * 64, "commit": "17235a6"}
OBS = {"source": "test"}


def _append_many(path: str, qid: str, author: str, n: int) -> None:
    for i in range(n):
        pl.forecast(Path(path), qid, f"{author}-{i}", 0.5, "r")


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ledger.jsonl"
        self.clock = mock.patch.object(pl, "now_utc", return_value=NOW)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.tmp.cleanup()

    def at(self, moment):
        return mock.patch.object(pl, "now_utc", return_value=moment)

    def q(self, text="Q", rule=None, horizon=H):
        return pl.ask(self.path, text, horizon, "s", "c", rule=rule)["question_id"]

    def resolve_later(self, qid, outcome="true", **kw):
        with self.at(datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc)):
            return pl.resolve(self.path, qid, outcome, OBS, **kw)

    # CRITICAL 1: backdating with fake evidence, and forward dating
    def test_backdating_needs_real_looking_seal_and_no_future_dates(self):
        qid = self.q()
        for fake in ({"x": 1}, {"sha256": "zz", "commit": "17235a6"}, {"sha256": "a" * 64}):
            with self.subTest(fake=fake), self.assertRaisesRegex(pl.GateError, "sealed_evidence"):
                pl.forecast(self.path, qid, "a", 0.5, "r", made_at="2000-01-01T00:00:00Z",
                            sealed_evidence=fake)
        with self.assertRaisesRegex(pl.GateError, "future"):
            pl.forecast(self.path, qid, "a", 0.5, "r", made_at="2026-10-21T20:00:00Z",
                        sealed_evidence=SEAL)
        pl.forecast(self.path, qid, "a", 0.5, "r", made_at="2026-10-21T08:00:00Z",
                    sealed_evidence=SEAL)

    # CRITICAL 2: forecasting once the rule's window is already running
    def test_no_forecast_after_rule_window_starts(self):
        rule = {"kind": "touch", "symbol": "BTCUSDT", "interval": "1m",
                "start": "2026-10-21T11:00:00Z", "level": 84000, "side": "at_or_below"}
        qid = self.q(rule=rule)
        with self.assertRaisesRegex(pl.GateError, "window has already started"):
            pl.forecast(self.path, qid, "a", 0.99, "r")

    # CRITICAL 3: U+2028 in a text field used to brick the ledger
    def test_unicode_line_separators_do_not_break_the_ledger(self):
        qid = self.q()
        pl.forecast(self.path, qid, "a", 0.5, "pasted from an\x85LLM")
        records = pl.load(self.path)
        pl.verify(records)
        self.assertEqual(len(records), 2)

    # HIGH 4: hand-crafted record with a correct hash
    def test_crafted_record_with_valid_hash_fails_verify(self):
        qid = self.q()
        pl.forecast(self.path, qid, "a", 0.5, "r")
        self.resolve_later(qid)
        records = pl.load(self.path)
        forged = {"type": "forecast", "question_id": qid, "author": "late", "probability": 1.0,
                  "status": "ok", "rationale": "", "made_at": "2026-10-21T12:00:00Z",
                  "sealed_evidence": None, "lessons_applied": [],
                  "recorded_at": "2026-10-21T12:00:00Z", "prev_hash": records[-1]["hash"],
                  "seq": len(records) + 1}
        forged["hash"] = pl.sha256_text(pl.canonical(forged))
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(pl.canonical(forged) + "\n")
        with self.assertRaisesRegex(pl.GateError, "line 4: question already resolved"):
            pl.verify(pl.load(self.path))

    # HIGH 5: concurrent writers
    def test_concurrent_writers_keep_the_chain_intact(self):
        self.clock.stop()
        try:
            qid = pl.ask(self.path, "Q", "2099-01-01T00:00:00Z", "s", "c")["question_id"]
            ctx = multiprocessing.get_context("spawn")
            procs = [ctx.Process(target=_append_many, args=(str(self.path), qid, f"w{i}", 5))
                     for i in range(4)]
            for proc in procs:
                proc.start()
            for proc in procs:
                proc.join(60)
            self.assertTrue(all(proc.exitcode == 0 for proc in procs))
            records = pl.load(self.path)
            pl.verify(records)
            self.assertEqual(len(records), 1 + 4 * 5)
        finally:
            self.clock.start()

    # HIGH 6: spamming forecasts on one question
    def test_only_the_first_forecast_per_author_counts(self):
        qid = self.q()
        pl.forecast(self.path, qid, "spammer", 0.5, "r")
        for _ in range(50):
            pl.forecast(self.path, qid, "spammer", 0.99, "r")
        self.resolve_later(qid)
        report = fs.score(pl.load(self.path))
        self.assertEqual(report["scoreboard"][0]["brier"], 0.25)
        self.assertEqual(report["counts"]["repeat_forecasts_ignored"], 50)

    # HIGH 8: rule questions cannot be resolved by opinion
    def test_rule_question_resolves_only_automatically_or_void(self):
        rule = {"kind": "touch", "symbol": "BTCUSDT", "interval": "1m",
                "start": "2026-10-21T18:00:00Z", "level": 84000, "side": "at_or_below"}
        qid = self.q(rule=rule)
        pl.forecast(self.path, qid, "a", 0.4, "r")
        with self.assertRaisesRegex(pl.GateError, "resolve-auto"):
            self.resolve_later(qid, "false")
        self.resolve_later(qid, "void", void_reason="data provider outage")

    # HIGH 9: NaN never reaches the ledger
    def test_nan_is_refused(self):
        with self.assertRaisesRegex(pl.GateError, "non-finite"):
            pl.json_arg('{"source": "x", "v": NaN}')
        qid = self.q()
        pl.forecast(self.path, qid, "a", 0.5, "r")
        with self.assertRaisesRegex(pl.GateError, "NaN"):
            with self.at(datetime(2026, 10, 22, 1, 0, tzinfo=timezone.utc)):
                pl.resolve(self.path, qid, "true", {"source": "x", "v": float("nan")})

    # MEDIUM 11: malformed lines fail closed instead of crashing
    def test_malformed_ledger_lines_are_gate_errors(self):
        for content in (b"[]\n", b"1\n", b'{"type":"question"}\n', b"\xff\xfe\n"):
            with self.subTest(content=content):
                self.path.write_bytes(content)
                with self.assertRaises(pl.GateError):
                    pl.verify(pl.load(self.path))

    # MEDIUM 12: JSON arguments
    def test_json_arguments_are_inline_or_inside_the_repo(self):
        long_inline = json.dumps({"source": "x", "pad": "y" * 400})
        self.assertEqual(pl.json_arg(long_inline)["source"], "x")
        with self.assertRaisesRegex(pl.GateError, "inside the CALIOPE folder"):
            pl.json_arg("@../../etc/passwd")

    # MEDIUM 15/16: lessons
    def test_lesson_cannot_be_cited_outside_its_scope(self):
        qid = self.q()
        pl.forecast(self.path, qid, "alice", 0.5, "r")
        self.resolve_later(qid)
        lid = pl.propose_lesson(self.path, "x", "alice", [qid], "learn")["lesson_id"]
        pl.decide_lesson(self.path, lid, "adopt", "nathan", "ok")
        other = pl.ask(self.path, "Q2", "2099-01-01T00:00:00Z", "s", "c")["question_id"]
        with self.assertRaisesRegex(pl.GateError, "scoped to another author"):
            pl.forecast(self.path, other, "bob", 0.5, "r", lessons_applied=[lid])

    # MEDIUM 17: equivalent spellings are the same question
    def test_equivalent_times_and_numbers_give_one_question(self):
        rule = {"kind": "touch", "symbol": "btcusdt", "interval": "1m",
                "start": "2026-10-21T18:00:00+00:00", "level": 84000, "side": "at_or_below"}
        self.q(rule=rule)
        with self.assertRaisesRegex(pl.GateError, "already exists"):
            self.q(rule=dict(rule, level=84000.0, start="2026-10-21T18:00:00Z", symbol="BTCUSDT"),
                   horizon="2026-10-22T00:15:00+00:00")

    # LOW 18: booleans are not probabilities
    def test_bool_probability_is_a_failure(self):
        qid = self.q()
        self.assertEqual(pl.forecast(self.path, qid, "a", True, "r")["status"], "failure")


class ResolverAuditTest(unittest.TestCase):
    RULE = {"kind": "touch", "symbol": "BTCUSDT", "interval": "1m",
            "start": "2026-10-21T18:00:00Z", "level": 84000, "side": "at_or_below"}
    END = "2026-10-21T18:02:00Z"
    T0 = resolver.to_ms("2026-10-21T18:00:00Z", "t")

    def data(self, rows=None, fetched_at="2026-10-21T18:05:00Z"):
        rows = rows if rows is not None else [
            [self.T0 + i * 60_000, "84500", "84600", "84400", "84500", "1", self.T0 + i * 60_000 + 59_999]
            for i in range(2)]
        return {"symbol": "BTCUSDT", "interval": "1m", "fetched_at": fetched_at, "klines": rows}

    def test_good_data_resolves(self):
        self.assertEqual(resolver.evaluate(self.RULE, self.END, self.data())[0], "false")

    def test_data_fetched_before_the_window_closed_is_refused(self):
        with self.assertRaisesRegex(resolver.ResolutionError, "fetched before horizon_end"):
            resolver.evaluate(self.RULE, self.END, self.data(fetched_at="2026-10-21T18:01:30Z"))

    def test_duplicates_bad_prices_and_bad_rows_are_refused(self):
        dup = self.data()
        dup["klines"].append([self.T0, "500", "600", "400", "500", "1", self.T0 + 59_999])
        cases = {
            "duplicate": dup,
            "inconsistent": self.data([[self.T0, "84500", "84600", "84400", "99999", "1",
                                        self.T0 + 59_999]]),
            "malformed": self.data([{"open": 1}]),
            "not closed": self.data([[self.T0 + i * 60_000, "84500", "84600", "84400", "84500",
                                      "1", self.T0 + i * 60_000 + 30_000] for i in range(2)]),
        }
        for word, data in cases.items():
            with self.subTest(word=word), self.assertRaisesRegex(resolver.ResolutionError, word):
                resolver.evaluate(self.RULE, self.END, data)

    def test_window_and_symbol_limits(self):
        with self.assertRaisesRegex(resolver.ResolutionError, "too long"):
            resolver.validate_rule(dict(self.RULE, start="1970-01-01T00:00:00Z"), self.END)
        with self.assertRaisesRegex(resolver.ResolutionError, "A-Z"):
            resolver.validate_rule(dict(self.RULE, symbol="BTC٣USDT"), self.END)


class FetchAuditTest(unittest.TestCase):
    def test_output_only_in_predictions_data_and_never_overwrites(self):
        for bad in ("prediction_ledger.py", "projects/macroflow/predictions/ledger.jsonl",
                    "projects/macroflow/predictions/data/x.txt", "../outside.json"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                fetch_klines.output_path(Path(bad))
        good = fetch_klines.output_path(Path("projects/macroflow/predictions/data/new_file.json"))
        self.assertEqual(good.name, "new_file.json")
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "projects" / "p" / "predictions" / "data" / "x.json"
            existing.parent.mkdir(parents=True)
            existing.write_text("{}", encoding="utf-8")
            with mock.patch.object(fetch_klines, "ROOT", Path(tmp).resolve()), \
                    self.assertRaisesRegex(ValueError, "never overwritten"):
                fetch_klines.output_path(existing)


if __name__ == "__main__":
    unittest.main()
