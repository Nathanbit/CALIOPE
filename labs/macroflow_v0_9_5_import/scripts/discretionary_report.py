"""Informe de ejecución discrecional a partir del export "closed orders" de SimpleFX.

Uso:
  python scripts/discretionary_report.py SFX_closed_orders_....csv --out state/discretionary_report.json

El resultado es evidencia separada (promotion_eligible=false): no entra al ledger
de MacroFlow ni entrena el modelo.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from macroflow.audit.discretionary import build_report  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv")
    ap.add_argument("--out", default="state/discretionary_report.json")
    ap.add_argument("--tz", default="America/Guayaquil", help="zona horaria del export y del informe")
    ap.add_argument("--min-trades", type=int, default=20)
    args = ap.parse_args()
    rep = build_report(args.csv, source_tz=args.tz, tz=args.tz, min_trades=args.min_trades)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=2, ensure_ascii=False, default=str))
    keys = ("n_trades", "net_pnl", "win_rate", "avg_win", "avg_loss", "payoff_ratio",
            "max_consecutive_losses", "net_pnl_without_top3", "overtrading")
    print(json.dumps({k: rep[k] for k in keys}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
