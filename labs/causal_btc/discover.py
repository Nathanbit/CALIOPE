#!/usr/bin/env python3
"""Find candidate drivers of BTC with causal-ts, on the discovery period only.

    pip install -r labs/causal_btc/requirements.txt   # aparte del núcleo
    python labs/causal_btc/discover.py --data labs/causal_btc/data/<file>.csv

Runs CEDAR (bloomberg/causal-ts) with partial-correlation tests on daily
returns, never on the holdout, and writes the edges "X at t-lag -> btc at t"
(lag >= 1, because same-day links cannot be traded) to a new hypotheses file.
Commit that file before running ``evaluate.py``: the hypotheses are then fixed
before anyone sees whether they hold.

causal-ts is GPL-3.0 and needs PyTorch. It is imported only here, at run
time; nothing of it is copied into CALIOPE, and the core never imports it.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import series  # noqa: E402


def discover(data_path: Path, holdout_days: int, max_lag: int, alpha: float) -> dict:
    import numpy as np  # noqa: PLC0415  (optional, lab only)
    import pandas as pd  # noqa: PLC0415
    from causalts import __version__ as causalts_version  # noqa: PLC0415
    from causalts.cedar import run_cedar  # noqa: PLC0415
    from causalts.ci_tests.parcorr_gpu import ParCorrGPU  # noqa: PLC0415

    data = series.transform(*series.load_csv(data_path))
    discovery, _ = series.split(data, holdout_days)
    df = pd.DataFrame(discovery["values"], columns=discovery["names"])
    values = np.ascontiguousarray(df.values)
    result = run_cedar(df=df, ci_test=ParCorrGPU(values, device="cpu"), max_lag=max_lag,
                       alpha_cond1=alpha, alpha_cond2=alpha, lag_method="pearson",
                       prune=True, verbose=False)
    names = list(df.columns)
    target = names.index(series.TARGET)
    edges, autoregressive = [], []
    for cause, name in enumerate(names):
        for lag in range(1, max_lag + 1):
            if not result.cg_tig[cause, target, lag]:
                continue
            x = df[name].to_numpy()[:-lag]
            y = df[series.TARGET].to_numpy()[lag:]
            r = float(np.corrcoef(x, y)[0, 1])
            edge = {"cause": name, "lag": lag, "sign": 1 if r >= 0 else -1,
                    "discovery_r": round(r, 4),
                    "pvalue": float(result.pvalue_matrix[cause, target, lag])}
            if cause == target:
                # CEDAR keeps self-loops by default (include_autoreg); keep only significant ones
                if edge["pvalue"] < alpha:
                    autoregressive.append(edge)
            else:
                edges.append(edge)
    return {"data": data_path.resolve().relative_to(ROOT).as_posix(),
            "data_sha256": series.file_sha256(data_path), "holdout_days": holdout_days,
            "discovery": [discovery["dates"][0], discovery["dates"][-1]],
            "discovery_end": discovery["dates"][-1], "dropped_days": data["dropped_days"],
            "method": "CEDAR + ParCorr (causal-ts)", "causalts_version": causalts_version,
            "params": {"max_lag": max_lag, "alpha": alpha, "lag_method": "pearson"},
            "pairs_searched": (len(names) - 1) * max_lag, "edges": edges,
            "autoregressive": autoregressive,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "evidence_type": "inference"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--holdout-days", type=int, default=180)
    parser.add_argument("--max-lag", type=int, default=3)
    parser.add_argument("--alpha", type=float, default=0.01)
    args = parser.parse_args(argv)
    try:
        report = discover(args.data, args.holdout_days, args.max_lag, args.alpha)
    except ImportError as exc:
        print(json.dumps({"ok": False, "error": f"falta {exc.name}: pip install -r "
                                                "labs/causal_btc/requirements.txt"}))
        return 2
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    out = HERE / "hypotheses" / f"{args.data.stem}_cedar_lag{args.max_lag}.json"
    out.parent.mkdir(exist_ok=True)
    with out.open("x", encoding="utf-8", newline="\n") as handle:  # never overwrite
        handle.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"ok": True, "hypotheses": out.relative_to(ROOT).as_posix(),
                      "edges": len(report["edges"]),
                      "next": "git add + commit este archivo, y después evaluate.py"},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
