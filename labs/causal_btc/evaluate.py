#!/usr/bin/env python3
"""Test frozen causal hypotheses on the holdout. Standard library only.

    python labs/causal_btc/evaluate.py --hypotheses labs/causal_btc/hypotheses/<file>.json

For each edge "X at t-lag drives btc at t" found by ``discover.py`` on the
discovery period, this measures on the holdout, which discovery never read:

- the correlation between X(t-lag) and btc(t), in the sign discovery found;
- the share of days where the sign of X(t-lag) called btc's direction;
- a p-value from a block permutation of btc (blocks of 5 days, fixed seed),
  so autocorrelation does not fake significance;
- a Bonferroni correction over every edge tested.

An edge "holds" only if its adjusted p-value is below 0.05 in the expected
sign. Lessons from bhoult/correlation: lagged links mostly appear at the rate
chance produces, and a link that holds still has to beat trading costs.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import series  # noqa: E402

BLOCK = 5


def pearson(x: list[float], y: list[float]) -> float:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    return 0.0 if sxx == 0 or syy == 0 else sxy / math.sqrt(sxx * syy)


def block_shuffle(values: list[float], rng: random.Random) -> list[float]:
    blocks = [values[i:i + BLOCK] for i in range(0, len(values), BLOCK)]
    rng.shuffle(blocks)
    return [v for block in blocks for v in block]


def test_edge(holdout: dict, cause: str, lag: int, sign: int, permutations: int,
              rng: random.Random) -> dict:
    x = holdout["values"][cause][:-lag]
    y = holdout["values"][series.TARGET][lag:]
    if len(y) < 30:
        raise series.SeriesError("holdout too short for this lag")
    r = pearson(x, y) * sign
    calls = [(a * sign > 0) == (b > 0) for a, b in zip(x, y) if a != 0 and b != 0]
    hit = sum(calls) / len(calls) if calls else None
    beats = sum(pearson(x, block_shuffle(y, rng)) * sign >= r for _ in range(permutations))
    return {"cause": cause, "lag": lag, "sign": sign, "n": len(y), "r": round(r, 4),
            "hit_rate": None if hit is None else round(hit, 4),
            "p": round((beats + 1) / (permutations + 1), 5)}


def evaluate(hypotheses: dict, data_path: Path, permutations: int = 2000, seed: int = 0) -> dict:
    if series.file_sha256(data_path) != hypotheses["data_sha256"]:
        raise series.SeriesError("the data file is not the one the hypotheses were found on")
    data = series.transform(*series.load_csv(data_path))
    discovery, holdout = series.split(data, hypotheses["holdout_days"])
    if discovery["dates"][-1] != hypotheses["discovery_end"]:
        raise series.SeriesError("the split differs from the one used in discovery")
    rng = random.Random(seed)
    edges = hypotheses["edges"]
    rows = [test_edge(holdout, e["cause"], e["lag"], e["sign"], permutations, rng) for e in edges]
    k = max(1, len(rows))
    for row in rows:
        row["p_adjusted"] = min(1.0, round(row["p"] * k, 5))
        row["holds"] = row["p_adjusted"] < 0.05 and row["r"] > 0
    return {"holdout": [holdout["dates"][0], holdout["dates"][-1]], "edges_tested": len(rows),
            "edges_holding": sum(r["holds"] for r in rows), "permutations": permutations,
            "seed": seed, "results": rows, "evidence_type": "derivation",
            "note": "Que una relación se sostenga no la vuelve operable: falta ganarle a "
                    "los costos y sellarla como predicciones antes de creerla."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hypotheses", required=True, type=Path)
    parser.add_argument("--permutations", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        hypotheses = json.loads(args.hypotheses.read_text(encoding="utf-8"))
        data_path = (HERE.parents[1] / hypotheses["data"]).resolve()
        report = evaluate(hypotheses, data_path, args.permutations, args.seed)
    except (OSError, KeyError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
