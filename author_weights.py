#!/usr/bin/env python3
"""How much to trust each author, as a portfolio problem solved with skfolio (BSD-3).

    python author_weights.py                       # authors with 30+ shared questions
    python author_weights.py --authors nathan,claude,ia:kronos

Each author is an asset. Its "return" on a resolved question is its log-score
skill against a coin: ln(p of what happened) - ln(0.5). Positive means it
beat the coin on that question. A weighted mix of authors then has the
weighted skill as a lower bound of the skill of their mixed forecast (the log
is concave), so maximising the first never overstates the second.

Three allocations are compared out of sample with a walk-forward split (train
on the past, test on the next block, never the other way round):

- ``igual``: the plain swarm, every author the same weight (the benchmark).
- ``hrp``: Hierarchical Risk Parity. Authors that fail together (correlated
  skill) share weight; it ignores the mean, so it is a pure risk view.
- ``cvar``: Mean-CVaR, the best mean skill per unit of tail loss (the worst 5%
  of questions).

Below 30 shared questions it refuses: weights fitted on a handful of
questions are noise. It never writes anything: weights are an ``inference``
for the person to consider, like a lesson. Scores still belong to
``forecast_scoring``; this module reuses its pairing (first forecast per author
and question).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import forecast_scoring as fs  # noqa: E402
import prediction_ledger as pl  # noqa: E402

LEDGER = ROOT / "projects" / "macroflow" / "predictions" / "ledger.jsonl"
MIN_N = 30
EPS = 1e-6


class WeightsError(ValueError):
    pass


def skill(p: float, y: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p if y == 1.0 else 1 - p) - math.log(0.5)


def skill_matrix(records: list[dict], authors: list[str] | None = None,
                 min_n: int = MIN_N) -> tuple[list[str], list[str], list[list[float]]]:
    """(question ids in time order, authors, rows of skill): only questions every author forecast.

    A missing forecast is unknown, never a zero, so a question enters only if
    all chosen authors forecast it.
    """
    pairs, _ = fs.scored_pairs(records)
    questions, _, _ = pl.index(records)
    by_q: dict[str, dict[str, float]] = {}
    for x in pairs:
        by_q.setdefault(x["question_id"], {})[x["author"]] = skill(x["p"], x["y"])
    if authors is None:
        counts: dict[str, int] = {}
        for row in by_q.values():
            for a in row:
                counts[a] = counts.get(a, 0) + 1
        authors = sorted(a for a, n in counts.items() if n >= min_n)
    if len(authors) < 2:
        raise WeightsError(f"need at least 2 authors with {min_n}+ resolved forecasts; have {authors}")
    shared = [q for q, row in by_q.items() if all(a in row for a in authors)]
    shared.sort(key=lambda q: (questions[q]["horizon_end"], q))
    if len(shared) < min_n:
        raise WeightsError(f"only {len(shared)} questions forecast by all of {authors}: "
                           f"weights need {min_n}+ (they would be noise)")
    return shared, authors, [[by_q[q][a] for a in authors] for q in shared]


def allocate(question_ids: list[str], authors: list[str], rows: list[list[float]],
             splits: int = 5) -> dict:
    try:
        import pandas as pd  # noqa: PLC0415
        from skfolio import RiskMeasure  # noqa: PLC0415
        from skfolio.exceptions import SkfolioError  # noqa: PLC0415
        from skfolio.model_selection import WalkForward, cross_val_predict  # noqa: PLC0415
        from skfolio.optimization import (EqualWeighted, HierarchicalRiskParity,  # noqa: PLC0415
                                          MeanRisk, ObjectiveFunction)
    except ImportError as exc:
        raise WeightsError("skfolio is not installed: pip install -r requirements.txt") from exc
    X = pd.DataFrame(rows, index=pd.RangeIndex(len(rows)), columns=authors)
    models = {
        "igual": EqualWeighted(),
        "hrp": HierarchicalRiskParity(risk_measure=RiskMeasure.VARIANCE),  # CVaR < 0 when an author never loses
        "cvar": MeanRisk(risk_measure=RiskMeasure.CVAR, objective_function=ObjectiveFunction.MAXIMIZE_UTILITY,
                         risk_aversion=1.0),
    }
    test = max(len(rows) // (splits + 1), 5)
    cv = WalkForward(train_size=len(rows) - test * splits, test_size=test)
    flat = [a for i, a in enumerate(authors) if len({round(r[i], 12) for r in rows}) == 1]
    if flat:
        raise WeightsError(f"{flat} gave the same skill on every question (one fixed probability): "
                           "no spread to weigh")
    out = {}
    for name, model in models.items():
        try:
            fitted = model.fit(X)
            oos = cross_val_predict(model, X, cv=cv)
        except (SkfolioError, ValueError) as exc:
            raise WeightsError(f"skfolio could not fit {name}: {exc}") from exc
        weights = {a: round(float(w), 4) for a, w in zip(authors, fitted.weights_)}
        returns = [float(v) for v in oos.returns]
        out[name] = {"weights": weights,
                     "out_of_sample": {"questions": len(returns),
                                       "mean_skill": round(sum(returns) / len(returns), 4),
                                       "worst_5pct": round(sorted(returns)[max(0, len(returns) // 20 - 1)], 4)}}
    return out


def report(records: list[dict], authors: list[str] | None = None, min_n: int = MIN_N) -> dict:
    qids, chosen, rows = skill_matrix(records, authors, min_n)
    result = allocate(qids, chosen, rows)
    best = max(result, key=lambda k: result[k]["out_of_sample"]["mean_skill"])
    return {"authors": chosen, "shared_questions": len(qids), "first": qids[0], "last": qids[-1],
            "skill": "ln(p of the outcome) - ln(0.5) per question: > 0 beats a coin",
            "allocations": result, "best_out_of_sample": best,
            "note": "inference: a weighting for the person to consider; nothing is written",
            "evidence_type": "inference"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument("--authors", help="comma-separated; default: everyone with 30+ resolved forecasts")
    parser.add_argument("--min-n", type=int, default=MIN_N)
    args = parser.parse_args(argv)
    try:
        if args.min_n < MIN_N:
            raise WeightsError(f"--min-n cannot go below {MIN_N}")
        records = pl.load(args.ledger)
        pl.verify(records)
        authors = [a.strip() for a in args.authors.split(",")] if args.authors else None
        print(json.dumps({"ok": True, **report(records, authors, args.min_n)}, ensure_ascii=False, indent=2))
    except (WeightsError, pl.GateError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
