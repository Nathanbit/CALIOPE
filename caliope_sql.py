#!/usr/bin/env python3
"""SQL over CALIOPE's ledgers with DuckDB: read-only analysis.

    python caliope_sql.py tables
    python caliope_sql.py query "SELECT author, count(*) FROM forecasts GROUP BY 1"
    python caliope_sql.py report
    python caliope_sql.py export --out caliope.duckdb      # -> runs/caliope.duckdb

The ledgers stay the only source of truth. Tables are built from the records
their owners already parsed and verified (``prediction_ledger`` for questions,
forecasts and resolutions; ``desk.orders`` for the desk), never by reading the
JSONL again: a broken chain fails here exactly as it fails there. The
database lives in memory, and before any query runs DuckDB's access to files
and the network is switched off and the configuration locked, so a query can
read the tables and nothing else, and cannot write anywhere.

Scores (Brier, log, rivals) still belong to ``forecast_scoring``: SQL here is
for looking around, not for a second scoring.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import prediction_ledger as pl  # noqa: E402

LEDGER = ROOT / "projects" / "macroflow" / "predictions" / "ledger.jsonl"
ORDERS = ROOT / "projects" / "macroflow" / "desk" / "orders.jsonl"
RUNS = ROOT / "runs"

SCHEMA = {
    "questions": ("question_id VARCHAR PRIMARY KEY, text VARCHAR, horizon_end TIMESTAMP, "
                  "recorded_at TIMESTAMP, project VARCHAR, source VARCHAR, base_rate DOUBLE, "
                  "kind VARCHAR, symbol VARCHAR, interval VARCHAR, window_start TIMESTAMP, rule JSON, "
                  "concept_class VARCHAR, seq INTEGER, hash VARCHAR"),
    "forecasts": ("hash VARCHAR PRIMARY KEY, question_id VARCHAR, author VARCHAR, author_kind VARCHAR, "
                  "probability DOUBLE, quantiles JSON, status VARCHAR, made_at TIMESTAMP, "
                  "recorded_at TIMESTAMP, rationale VARCHAR, lessons JSON, seq INTEGER"),
    "resolutions": ("question_id VARCHAR PRIMARY KEY, outcome VARCHAR, value DOUBLE, void_reason VARCHAR, "
                    "resolved_at TIMESTAMP, recorded_at TIMESTAMP, observed JSON, seq INTEGER, hash VARCHAR"),
    "orders": ("order_id VARCHAR PRIMARY KEY, author VARCHAR, question_id VARCHAR, forecast_hash VARCHAR, "
               "env VARCHAR, inst_id VARCHAR, side VARCHAR, margin_fraction DOUBLE, leverage INTEGER, "
               "notional_usd DOUBLE, reference_price DOUBLE, stop_loss DOUBLE, take_profit DOUBLE, "
               "limit_price DOUBLE, contracts VARCHAR, proposed_at TIMESTAMP, auto_eligible BOOLEAN, "
               "decision VARCHAR, decision_mode VARCHAR, decided_at TIMESTAMP, sent_at TIMESTAMP, "
               "accepted BOOLEAN, outcome_unknown BOOLEAN"),
    "equity": "seq INTEGER PRIMARY KEY, env VARCHAR, equity DOUBLE, source VARCHAR, recorded_at TIMESTAMP",
}

REPORTS = {
    "autores": """
        SELECT f.author, f.author_kind, count(*) AS pronosticos,
               count(r.question_id) FILTER (WHERE r.outcome IN ('true','false','value')) AS resueltos,
               round(avg(f.probability), 3) AS p_media,
               min(f.made_at) AS primero, max(f.made_at) AS ultimo
        FROM forecasts f LEFT JOIN resolutions r USING (question_id)
        GROUP BY ALL ORDER BY pronosticos DESC""",
    "abiertas": """
        SELECT q.question_id, q.horizon_end, q.kind, count(f.hash) AS pronosticos, q.text
        FROM questions q LEFT JOIN forecasts f USING (question_id)
        LEFT JOIN resolutions r USING (question_id)
        WHERE r.question_id IS NULL GROUP BY ALL ORDER BY q.horizon_end""",
    "mesa": """
        SELECT author, count(*) AS propuestas,
               count(*) FILTER (WHERE decision = 'approve') AS aprobadas,
               count(*) FILTER (WHERE accepted) AS aceptadas,
               count(*) FILTER (WHERE outcome_unknown) AS desconocidas,
               round(avg(margin_fraction), 3) AS margen_medio, round(avg(leverage), 1) AS apalancamiento_medio
        FROM orders GROUP BY ALL ORDER BY propuestas DESC""",
}


class SqlError(ValueError):
    pass


def _j(value: object) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


def prediction_rows(records: list[dict]) -> dict[str, list[tuple]]:
    """Rows from a ledger already verified by its owner."""
    import concepts  # noqa: PLC0415
    questions, forecasts, resolutions = pl.index(records)
    q_rows = []
    for q in questions.values():
        rule = q.get("rule") or {}
        concept = q.get("concept")
        concept_class = None
        if concept:
            try:
                concept_class = concepts.concept_class(concept)
            except (ValueError, KeyError):
                concept_class = None
        q_rows.append((q["question_id"], q["text"], q["horizon_end"], q["recorded_at"], q.get("project"),
                       q.get("source"), q.get("base_rate"), rule.get("kind"), rule.get("symbol"),
                       rule.get("interval"), rule.get("start"), _j(q.get("rule")), concept_class,
                       q["seq"], q["hash"]))
    f_rows = [(f["hash"], f["question_id"], f["author"], f["author"].split(":", 1)[0] if ":" in f["author"]
               else "sin_prefijo", f.get("probability"), _j(f.get("quantiles")), f.get("status"), f.get("made_at"),
               f["recorded_at"], f.get("rationale"), _j(f.get("lessons_applied")), f["seq"])
              for items in forecasts.values() for f in items]
    r_rows = []
    for r in resolutions.values():
        value = r.get("value")
        r_rows.append((r["question_id"], r["outcome"], value if isinstance(value, (int, float)) else None,
                       r.get("void_reason"), r.get("resolved_at"), r["recorded_at"], _j(r.get("observed")),
                       r["seq"], r["hash"]))
    return {"questions": q_rows, "forecasts": f_rows, "resolutions": r_rows}


def order_rows(state) -> dict[str, list[tuple]]:
    """Rows from a desk ledger already replayed by ``desk.orders.verify``."""
    rows = []
    for oid, p in state.proposals.items():
        d, s, r = state.decisions.get(oid), state.sends.get(oid), state.results.get(oid)
        unknown = bool(r and isinstance(r.get("response"), dict) and r["response"].get("unknown"))
        rows.append((oid, p["author"], p["question_id"], p["forecast_hash"], p["env"], p["inst_id"], p["side"],
                     p["margin_fraction"], p["leverage"], p["notional_usd"], p["reference_price"], p["stop_loss"],
                     p["take_profit"], p["limit_price"], p["contracts"], p["recorded_at"],
                     bool(p.get("auto", {}).get("eligible")), d and d["decision"], d and d["mode"],
                     d and d["recorded_at"], s and s["recorded_at"], r and bool(r["ok"]), unknown))
    equity = [(e["seq"], e["env"], e["equity"], e.get("source"), e["recorded_at"]) for e in state.equity]
    return {"orders": rows, "equity": equity}


def connect(ledger: Path = LEDGER, orders: Path | None = None):
    """An in-memory DuckDB with every table loaded, then sealed against files and network."""
    try:
        import duckdb  # noqa: PLC0415
    except ImportError as exc:
        raise SqlError("DuckDB is not installed: pip install -r requirements.txt") from exc
    records = pl.load(ledger)
    pl.verify(records)
    tables = prediction_rows(records)
    if orders is not None and orders.exists():
        from desk import orders as od  # noqa: PLC0415
        tables.update(order_rows(od.verify(od.load(orders), records)))
    else:
        tables.update({"orders": [], "equity": []})
    con = duckdb.connect(":memory:")            # times are stored as UTC TIMESTAMP
    for name, columns in SCHEMA.items():
        con.execute(f"CREATE TABLE {name} ({columns})")
        if tables[name]:
            marks = ", ".join("?" * len(tables[name][0]))
            con.executemany(f"INSERT INTO {name} VALUES ({marks})", tables[name])
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    return con


def run_query(con, sql: str) -> dict:
    import duckdb  # noqa: PLC0415
    try:
        result = con.execute(sql)
        if result.description is None:
            return {"columns": [], "rows": []}
        return {"columns": [d[0] for d in result.description], "rows": [list(r) for r in result.fetchall()]}
    except duckdb.Error as exc:
        raise SqlError(f"DuckDB: {exc}") from exc


def _runs_file(name: str) -> Path:
    target = (RUNS / name).resolve()
    if RUNS.resolve() not in target.parents or target.suffix != ".duckdb":
        raise SqlError("--out takes a .duckdb file name inside runs/")
    return target


def export(ledger: Path, orders: Path | None, out: Path) -> Path:
    """Write the same tables to a DuckDB file under runs/ (ignored by git) for notebooks or the DuckDB UI."""
    import duckdb  # noqa: PLC0415
    records = pl.load(ledger)
    pl.verify(records)
    tables = prediction_rows(records)
    if orders is not None and orders.exists():
        from desk import orders as od  # noqa: PLC0415
        tables.update(order_rows(od.verify(od.load(orders), records)))
    else:
        tables.update({"orders": [], "equity": []})
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".duckdb.tmp")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    try:
        for name, columns in SCHEMA.items():
            con.execute(f"CREATE TABLE {name} ({columns})")
            if tables[name]:
                con.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * len(tables[name][0]))})",
                                tables[name])
    finally:
        con.close()
    tmp.replace(out)
    return out


def _print(result: dict, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps([dict(zip(result["columns"], r)) for r in result["rows"]], default=str,
                         ensure_ascii=False, indent=2))
        return
    cols = result["columns"]
    cells = [[("" if v is None else str(v)) for v in row] for row in result["rows"]]
    widths = [min(60, max([len(c)] + [len(r[i]) for r in cells])) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    for row in cells:
        print("  ".join(v[:w].ljust(w) for v, w in zip(row, widths)))
    print(f"({len(cells)} filas)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--orders", type=Path, default=ORDERS)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tables")
    q = sub.add_parser("query")
    q.add_argument("sql")
    q.add_argument("--format", choices=("table", "json"), default="table")
    r = sub.add_parser("report")
    r.add_argument("--format", choices=("table", "json"), default="table")
    e = sub.add_parser("export")
    e.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            print(json.dumps({"ok": True, "file": export(args.ledger, args.orders, _runs_file(args.out))
                              .relative_to(ROOT).as_posix()}))
            return 0
        con = connect(args.ledger, args.orders)
        if args.command == "tables":
            _print(run_query(con, "SELECT table_name AS tabla, estimated_size AS filas FROM duckdb_tables() "
                                  "ORDER BY 1"), "table")
        elif args.command == "query":
            _print(run_query(con, args.sql), args.format)
        else:
            for name, sql in REPORTS.items():
                print(f"\n== {name}")
                _print(run_query(con, sql), args.format)
    except (SqlError, pl.GateError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
