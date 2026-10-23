#!/usr/bin/env python3
"""Hash-chained ledger of binary predictions for CALIOPE.

Questions, forecasts and resolutions are appended to a JSONL file. Every
record stores the SHA-256 of the previous record, so editing, reordering or
deleting a line breaks the chain and ``verify`` reports where.

Design ideas adapted (no code copied) from:
- hey-research-lab/brier (MIT): sealed-before-answer ledger, refuse forecasts
  after resolution, refuse resolving questions without forecasts, unusable
  answers recorded as failures, dumb baselines.
- calibaudit (Apache-2.0): Murphy decomposition of the Brier score and
  small-sample warnings.
- Metaculus question checklist: absolute dates, explicit resolution source
  and criteria.

Recursive learning (``learn``) turns past calibration into a *proposal* for
future forecasts. It never edits the ledger or any forecast: models propose,
deterministic gates verify, a person decides.

Standard library only. Exit code 2 means a gate rejected the input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64
OUTCOMES = ("true", "false", "void")
FAILURE = "failure"


class GateError(Exception):
    """A deterministic gate rejected the input (fail closed)."""


# ── Primitives ──────────────────────────────────────────────────────────────

def canonical(data: dict) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_time(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise GateError(f"{field}: not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GateError(f"{field}: timezone required (naive datetimes are rejected)")
    return parsed.astimezone(timezone.utc)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def question_id(text: str, horizon_end: str, source: str, criteria: str) -> str:
    """Rewording any defining field yields a different question."""
    body = {"text": text.strip(), "horizon_end": horizon_end, "source": source.strip(),
            "criteria": criteria.strip()}
    return "q-" + sha256_text(canonical(body))[:16]


# ── Ledger storage ──────────────────────────────────────────────────────────

def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise GateError(f"line {number}: invalid JSON") from exc
    return records


def verify(records: list[dict]) -> None:
    previous = GENESIS
    for number, record in enumerate(records, 1):
        if record.get("prev_hash") != previous:
            raise GateError(f"line {number}: chain broken (prev_hash mismatch)")
        body = {k: v for k, v in record.items() if k != "hash"}
        if sha256_text(canonical(body)) != record.get("hash"):
            raise GateError(f"line {number}: record content does not match its hash")
        previous = record["hash"]


def append(path: Path, records: list[dict], body: dict) -> dict:
    body = dict(body)
    body["prev_hash"] = records[-1]["hash"] if records else GENESIS
    body["seq"] = len(records) + 1
    body["hash"] = sha256_text(canonical(body))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical(body) + "\n")
    records.append(body)
    return body


def index(records: list[dict]) -> tuple[dict, dict, dict]:
    questions, forecasts, resolutions = {}, {}, {}
    for record in records:
        kind = record["type"]
        if kind == "question":
            questions[record["question_id"]] = record
        elif kind == "forecast":
            forecasts.setdefault(record["question_id"], []).append(record)
        elif kind == "resolution":
            resolutions[record["question_id"]] = record
    return questions, forecasts, resolutions


# ── Commands that write ─────────────────────────────────────────────────────

def ask(path: Path, text: str, horizon_end: str, source: str, criteria: str,
        base_rate: float | None = None, project: str | None = None) -> dict:
    records = load(path)
    verify(records)
    parse_time(horizon_end, "horizon_end")
    for field, value in (("text", text), ("source", source), ("criteria", criteria)):
        if not value or not value.strip():
            raise GateError(f"{field} is required: a question must say how it resolves")
    if base_rate is not None and not 0.0 < base_rate < 1.0:
        raise GateError("base_rate must be strictly between 0 and 1")
    qid = question_id(text, horizon_end, source, criteria)
    questions, _, _ = index(records)
    if qid in questions:
        raise GateError(f"{qid} already exists")
    return append(path, records, {
        "type": "question", "question_id": qid, "text": text.strip(),
        "horizon_end": horizon_end, "source": source.strip(), "criteria": criteria.strip(),
        "base_rate": base_rate, "project": project, "recorded_at": utc_now_iso(),
    })


def forecast(path: Path, qid: str, author: str, probability: float | None, rationale: str,
             made_at: str | None = None, sealed_evidence: dict | None = None) -> dict:
    """Seal a forecast. ``made_at`` in the past requires ``sealed_evidence``
    (hash of an earlier sealed file and the commit that published it)."""
    records = load(path)
    verify(records)
    questions, _, resolutions = index(records)
    if qid not in questions:
        raise GateError(f"unknown question {qid}")
    if qid in resolutions:
        raise GateError("question already resolved: forecasts after the outcome are refused")
    made = made_at or utc_now_iso()
    made_dt = parse_time(made, "made_at")
    if made_dt >= parse_time(questions[qid]["horizon_end"], "horizon_end"):
        raise GateError("made_at is not before horizon_end")
    if made_at is not None and not sealed_evidence:
        raise GateError("backdated made_at requires sealed_evidence (sha256 + commit)")
    if not author or not author.strip():
        raise GateError("author is required")
    status = "ok"
    if probability is None or not isinstance(probability, (int, float)) \
            or not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        status, probability = FAILURE, None  # never coerced to 0.5, never dropped
    return append(path, records, {
        "type": "forecast", "question_id": qid, "author": author.strip(),
        "probability": probability, "status": status, "rationale": rationale.strip(),
        "made_at": made, "sealed_evidence": sealed_evidence, "recorded_at": utc_now_iso(),
    })


def resolve(path: Path, qid: str, outcome: str, observed: dict, resolved_at: str | None = None,
            void_reason: str | None = None) -> dict:
    records = load(path)
    verify(records)
    questions, forecasts, resolutions = index(records)
    if qid not in questions:
        raise GateError(f"unknown question {qid}")
    if qid in resolutions:
        raise GateError("question already resolved")
    if not forecasts.get(qid):
        raise GateError("refusing to resolve a question without forecasts")
    if outcome not in OUTCOMES:
        raise GateError(f"outcome must be one of {OUTCOMES}")
    if outcome == "void" and not (void_reason and void_reason.strip()):
        raise GateError("void outcomes need a reason taken from the resolution criteria")
    when = resolved_at or utc_now_iso()
    if parse_time(when, "resolved_at") < parse_time(questions[qid]["horizon_end"], "horizon_end"):
        raise GateError("cannot resolve before horizon_end")
    if not isinstance(observed, dict) or not observed.get("source"):
        raise GateError("observed data must be an object with its source")
    return append(path, records, {
        "type": "resolution", "question_id": qid, "outcome": outcome,
        "void_reason": void_reason, "observed": observed, "resolved_at": when,
        "recorded_at": utc_now_iso(),
    })


# ── Scoring ─────────────────────────────────────────────────────────────────

def scored_pairs(records: list[dict]) -> tuple[list[dict], dict]:
    """(author, probability, outcome) for resolved non-void questions, plus counts."""
    questions, forecasts, resolutions = index(records)
    pairs, counts = [], {"questions": len(questions), "open": 0, "void": 0,
                         "resolved": 0, "failures": 0}
    for qid in questions:
        resolution = resolutions.get(qid)
        if resolution is None:
            counts["open"] += 1
            continue
        if resolution["outcome"] == "void":
            counts["void"] += 1
            continue
        counts["resolved"] += 1
        y = 1.0 if resolution["outcome"] == "true" else 0.0
        for item in forecasts.get(qid, []):
            if item["status"] == FAILURE:
                counts["failures"] += 1
                continue
            pairs.append({"author": item["author"], "p": float(item["probability"]), "y": y,
                          "base_rate": questions[qid].get("base_rate")})
    return pairs, counts


def brier(pairs: list[dict]) -> float | None:
    return sum((x["p"] - x["y"]) ** 2 for x in pairs) / len(pairs) if pairs else None


def log_score(pairs: list[dict], eps: float = 1e-6) -> float | None:
    if not pairs:
        return None
    total = 0.0
    for x in pairs:
        p = min(max(x["p"], eps), 1 - eps)
        total += math.log(p if x["y"] == 1.0 else 1 - p)
    return total / len(pairs)


def murphy(pairs: list[dict], bins: int = 10) -> dict | None:
    """Brier = REL - RES + UNC (+ within-bin terms when forecasts share a bin
    but differ). Reports the residual so the identity is checkable."""
    if not pairs:
        return None
    n = len(pairs)
    base = sum(x["y"] for x in pairs) / n
    groups: dict[int, list[dict]] = {}
    for x in pairs:
        groups.setdefault(min(int(x["p"] * bins), bins - 1), []).append(x)
    rel = res = 0.0
    table = []
    for key in sorted(groups):
        group = groups[key]
        mean_p = sum(x["p"] for x in group) / len(group)
        freq = sum(x["y"] for x in group) / len(group)
        rel += len(group) * (mean_p - freq) ** 2
        res += len(group) * (freq - base) ** 2
        table.append({"bin": f"{key / bins:.1f}-{(key + 1) / bins:.1f}", "n": len(group),
                      "mean_forecast": round(mean_p, 4), "observed_freq": round(freq, 4)})
    rel, res, unc = rel / n, res / n, base * (1 - base)
    score = brier(pairs)
    return {"brier": score, "reliability": rel, "resolution": res, "uncertainty": unc,
            "within_bin_residual": score - (rel - res + unc), "bins": table,
            "ece": sum(row["n"] * abs(row["mean_forecast"] - row["observed_freq"])
                       for row in table) / n}


def score(records: list[dict]) -> dict:
    pairs, counts = scored_pairs(records)
    authors = sorted({x["author"] for x in pairs})
    board = []
    for author in authors:
        own = [x for x in pairs if x["author"] == author]
        board.append({"author": author, "n": len(own), "brier": brier(own),
                      "log_score": log_score(own)})
    coin = [dict(x, p=0.5) for x in pairs]
    based = [dict(x, p=x["base_rate"]) for x in pairs if x["base_rate"] is not None]
    report = {"counts": counts, "scoreboard": board,
              "baselines": {"coin": brier(coin), "base_rate": brier(based) if based else None},
              "calibration": murphy(pairs)}
    if len(pairs) < 30:
        report["warning"] = (f"only {len(pairs)} scored forecasts: calibration and skill are "
                             "not statistically meaningful yet")
    return report


# ── Recursive learning (proposal only) ──────────────────────────────────────

def learn(records: list[dict], min_n: int = 30, prior_strength: float = 20.0,
          bins: int = 5) -> dict:
    """Per author and probability bin, compare stated confidence with observed
    frequency. Shrinks the observed frequency toward the stated probability
    with ``prior_strength`` pseudo-observations, and only proposes an
    adjustment once a bin has ``min_n`` resolved forecasts. Output is an
    observation-only proposal; nothing is applied automatically."""
    pairs, counts = scored_pairs(records)
    lessons = []
    for author in sorted({x["author"] for x in pairs}):
        own = [x for x in pairs if x["author"] == author]
        groups: dict[int, list[dict]] = {}
        for x in own:
            groups.setdefault(min(int(x["p"] * bins), bins - 1), []).append(x)
        for key in sorted(groups):
            group = groups[key]
            n = len(group)
            stated = sum(x["p"] for x in group) / n
            hits = sum(x["y"] for x in group)
            shrunk = (hits + prior_strength * stated) / (n + prior_strength)
            ready = n >= min_n
            lessons.append({
                "author": author, "bin": f"{key / bins:.1f}-{(key + 1) / bins:.1f}", "n": n,
                "stated_mean": round(stated, 4), "observed_freq": round(hits / n, 4),
                "shrunk_freq": round(shrunk, 4), "ready": ready,
                "proposal": (f"When {author} states ~{stated:.0%}, treat it as ~{shrunk:.0%}"
                             if ready else f"gathering evidence ({n}/{min_n})"),
            })
    return {"evidence_type": "inference", "observation_only": True, "automatic_edit": False,
            "requires_human_decision": True, "min_n": min_n, "prior_strength": prior_strength,
            "counts": counts, "lessons": lessons}


# ── CLI ─────────────────────────────────────────────────────────────────────

def _json_arg(value: str | None) -> dict | None:
    if value is None:
        return None
    candidate = Path(value)
    text = candidate.read_text(encoding="utf-8") if candidate.exists() else value
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GateError("expected a JSON object or a path to one") from exc
    if not isinstance(data, dict):
        raise GateError("expected a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify")
    p = sub.add_parser("ask")
    p.add_argument("--text", required=True)
    p.add_argument("--horizon-end", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--criteria", required=True)
    p.add_argument("--base-rate", type=float)
    p.add_argument("--project")
    p = sub.add_parser("forecast")
    p.add_argument("--question", required=True)
    p.add_argument("--author", required=True)
    p.add_argument("--probability", type=float)
    p.add_argument("--rationale", default="")
    p.add_argument("--made-at")
    p.add_argument("--sealed-evidence")
    p = sub.add_parser("resolve")
    p.add_argument("--question", required=True)
    p.add_argument("--outcome", required=True, choices=OUTCOMES)
    p.add_argument("--observed", required=True)
    p.add_argument("--resolved-at")
    p.add_argument("--void-reason")
    sub.add_parser("score")
    p = sub.add_parser("learn")
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--prior-strength", type=float, default=20.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            records = load(args.ledger)
            verify(records)
            out = {"ok": True, "records": len(records),
                   "head": records[-1]["hash"] if records else GENESIS}
        elif args.command == "ask":
            out = ask(args.ledger, args.text, args.horizon_end, args.source, args.criteria,
                      args.base_rate, args.project)
        elif args.command == "forecast":
            out = forecast(args.ledger, args.question, args.author, args.probability,
                           args.rationale, args.made_at, _json_arg(args.sealed_evidence))
        elif args.command == "resolve":
            out = resolve(args.ledger, args.question, args.outcome, _json_arg(args.observed),
                          args.resolved_at, args.void_reason)
        else:
            records = load(args.ledger)
            verify(records)
            out = score(records) if args.command == "score" else \
                learn(records, args.min_n, args.prior_strength)
    except GateError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
