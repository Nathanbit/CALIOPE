"""Informe de causalidad de la réplica Python sobre un CSV real.

Uso:
  python scripts/causality_report.py datos.csv --out state/causality_report.json

El CSV debe tener columna de tiempo (`time`, en UTC) y open/high/low/close/ref_close
(`vix` opcional), igual que espera `macroflow.strategy.reference`.
Sale con código 1 si alguna prueba no pasa, para poder usarlo como compuerta.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from macroflow.audit.causality import lookahead_check, warmup_check  # noqa: E402
from macroflow.strategy.reference import DEFAULT_PARAMS, compute_signals  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv")
    ap.add_argument("--out", default="state/causality_report.json")
    ap.add_argument("--checks", type=int, default=40)
    ap.add_argument("--compare-bars", type=int, default=200)
    args = ap.parse_args()

    df = pd.read_csv(args.csv, parse_dates=["time"]).set_index("time")
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    fn = lambda d: compute_signals(d, dict(DEFAULT_PARAMS))  # noqa: E731
    warm = int(max(DEFAULT_PARAMS["longTrendLen"], DEFAULT_PARAMS["transNormLen"],
                   DEFAULT_PARAMS["correlationLookback"]))
    look = lookahead_check(df, fn, min_index=warm, max_checks=args.checks)
    last = len(df) - args.compare_bars
    starts = sorted({s for s in (warm, last // 4, last // 2, max(0, last - warm)) if 0 < s < last})
    wup = warmup_check(df, fn, starts=starts, compare_bars=args.compare_bars)
    report = {"evidence_kind": "derivation", "source": str(Path(args.csv).resolve()),
              "source_sha256": hashlib.sha256(Path(args.csv).read_bytes()).hexdigest(), "rows": len(df),
              "lookahead": look.to_dict(), "warmup": wup.to_dict(),
              "passed": look.passed and wup.passed}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({"passed": report["passed"],
                      "leaking_columns": look.leaking_columns,
                      "unstable_columns": wup.unstable_columns,
                      "recommended_startup_bars": wup.recommended_startup_bars}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
