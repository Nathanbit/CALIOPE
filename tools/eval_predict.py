#!/usr/bin/env python3
"""Grade an interpreter of prediction messages against evals/predict/cases.json.

Idea from microsoft/skill-recorder's evals: test the part with real variance
(the model that turns words into structure) on fixed cases, with a
deterministic, LLM-free score. Answers are a JSON object
``{case_id: {"concept": {...}, "probability": p}}``, produced by any model or
by ``tools/panel/parse.js``. Each concept must pass
``concepts.validate_concept`` and match every expected field.

    python tools/eval_predict.py --answers state/answers.json

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import concepts  # noqa: E402
import evidence_io as eio  # noqa: E402

CASES = ROOT / "evals" / "predict" / "cases.json"


class EvalError(ValueError):
    pass


def load_cases(path: Path = CASES) -> dict:
    return eio.read_json_object(path, EvalError)


def _same(a: object, b: object) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-9)
    return a == b


def grade(cases: dict, answers: dict) -> dict:
    results = []
    for case in cases["cases"]:
        answer = answers.get(case["id"])
        problems = []
        if not isinstance(answer, dict) or not isinstance(answer.get("concept"), dict):
            problems.append("no answer")
        else:
            try:
                concept = concepts.validate_concept(answer["concept"])
            except concepts.ConceptError as exc:
                concept = None
                problems.append(f"invalid concept: {exc}")
            got = dict(concept or {}, probability=answer.get("probability"))
            for key, want in case["expected"].items():
                if concept is None and key != "probability":
                    continue
                if not _same(got.get(key), want):
                    problems.append(f"{key}: expected {want!r}, got {got.get(key)!r}")
            if concept and not _same(concept["reference_price"], case["reference_price"]):
                problems.append("reference_price differs from the observed one")
        results.append({"id": case["id"], "pass": not problems, "problems": problems})
    passed = sum(r["pass"] for r in results)
    return {"cases": len(results), "passed": passed,
            "score": round(passed / len(results), 4) if results else None,
            "results": results, "evidence_type": "derivation"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--answers", required=True, type=Path)
    parser.add_argument("--cases", type=Path, default=CASES)
    args = parser.parse_args(argv)
    try:
        report = grade(load_cases(args.cases), eio.read_json_object(args.answers, EvalError))
    except (OSError, EvalError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] == report["cases"] else 1


if __name__ == "__main__":
    sys.exit(main())
