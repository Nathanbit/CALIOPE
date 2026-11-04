"""DuckDB over the ledgers: tables from their owners, a sealed sandbox, and export only under runs/."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import caliope_sql as cs  # noqa: E402
import prediction_ledger as pl  # noqa: E402

HAS_DUCKDB = importlib.util.find_spec("duckdb") is not None


def ledger(tmp: Path) -> Path:
    path = tmp / "l.jsonl"
    q = pl.ask(path, "BTC sube", "2026-01-02T00:00:00Z", "s", "c")["question_id"]
    with mock.patch.object(pl, "now_utc", return_value=pl.parse_time("2026-01-01T00:00:00Z", "t")):
        pl.forecast(path, q, "nathan", 0.6, "r")
        pl.forecast(path, q, "ia:gemini", 0.3, "r")
    return path


class RowsTest(unittest.TestCase):
    def test_rows_come_from_the_owner_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = cs.prediction_rows(pl.load(ledger(Path(tmp))))
        self.assertEqual(len(rows["questions"]), 1)
        kinds = sorted(r[3] for r in rows["forecasts"])
        self.assertEqual(kinds, ["ia", "sin_prefijo"])
        for name, table in rows.items():
            width = len(cs.SCHEMA[name].split(", "))
            self.assertTrue(all(len(r) == width for r in table), name)

    def test_export_stays_under_runs(self):
        for bad in ("../x.duckdb", "/tmp/x.duckdb", "x.db"):
            with self.assertRaises(cs.SqlError, msg=bad):
                cs._runs_file(bad)


@unittest.skipUnless(HAS_DUCKDB, "duckdb no está instalado (pip install -r requirements.txt)")
class DuckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = ledger(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_query_and_reports(self):
        con = cs.connect(self.path, None)
        out = cs.run_query(con, "SELECT author, probability FROM forecasts ORDER BY author")
        self.assertEqual(out["rows"], [["ia:gemini", 0.3], ["nathan", 0.6]])
        for sql in cs.REPORTS.values():
            cs.run_query(con, sql)

    def test_sandbox_blocks_files_network_and_unlocking(self):
        con = cs.connect(self.path, None)
        for sql in ("SELECT * FROM read_csv('/etc/hostname')", "COPY forecasts TO 'leak.csv'",
                    "ATTACH 'other.duckdb'", "INSTALL httpfs", "SET enable_external_access = true"):
            with self.assertRaises(cs.SqlError, msg=sql):
                cs.run_query(con, sql)
        self.assertFalse(Path("leak.csv").exists())

    def test_a_broken_chain_fails_before_any_table(self):
        lines = self.path.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[1])
        rec["probability"] = 0.99
        lines[1] = json.dumps(rec)
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with self.assertRaises(pl.GateError):
            cs.connect(self.path, None)


if __name__ == "__main__":
    unittest.main()
