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
- google-research/harness2 (Apache-2.0): a persistent memory of
  evidence-supported lessons that guides future work and is itself judged
  ("improving the improver").

Two recursion levels: ``learn`` turns past calibration into proposals, and
lessons (``lesson-propose``/``lesson-decide``/``lesson-score``) record what a
person adopted and whether following it improved later forecasts. It never edits the ledger or any forecast: models propose,
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
LESSON_DECISIONS = ("adopt", "reject", "retire")


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


def now_utc() -> datetime:
    """Single clock for every gate (patched in tests)."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return now_utc().isoformat().replace("+00:00", "Z")


CLOCK_SKEW_SECONDS = 300


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
             made_at: str | None = None, sealed_evidence: dict | None = None,
             lessons_applied: list[str] | None = None) -> dict:
    """Seal a forecast. ``made_at`` in the past requires ``sealed_evidence``
    (hash of an earlier sealed file and the commit that published it).
    ``lessons_applied`` may only cite lessons a person has adopted."""
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
    applied = sorted(set(lessons_applied or []))
    lessons = lessons_index(records)
    for lid in applied:
        if lid not in lessons or lesson_status(lessons[lid]) != "adopted":
            raise GateError(f"{lid} is not an adopted lesson")
        if made_at is not None:
            raise GateError("backdated forecasts cannot cite lessons")
    status = "ok"
    if probability is None or not isinstance(probability, (int, float)) \
            or not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        status, probability = FAILURE, None  # never coerced to 0.5, never dropped
    return append(path, records, {
        "type": "forecast", "question_id": qid, "author": author.strip(),
        "probability": probability, "status": status, "rationale": rationale.strip(),
        "made_at": made, "sealed_evidence": sealed_evidence, "lessons_applied": applied,
        "recorded_at": utc_now_iso(),
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
    resolved_dt = parse_time(when, "resolved_at")
    if resolved_dt < parse_time(questions[qid]["horizon_end"], "horizon_end"):
        raise GateError("cannot resolve before horizon_end")
    if (resolved_dt - now_utc()).total_seconds() > CLOCK_SKEW_SECONDS:
        raise GateError("resolved_at is in the future: an outcome cannot be observed yet")
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
                          "base_rate": questions[qid].get("base_rate"), "question_id": qid,
                          "seq": item["seq"], "lessons": item.get("lessons_applied") or []})
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
                "evidence_questions": sorted({x["question_id"] for x in group}),
                "author": author, "bin": f"{key / bins:.1f}-{(key + 1) / bins:.1f}", "n": n,
                "stated_mean": round(stated, 4), "observed_freq": round(hits / n, 4),
                "shrunk_freq": round(shrunk, 4), "ready": ready,
                "proposal": (f"When {author} states ~{stated:.0%}, treat it as ~{shrunk:.0%}"
                             if ready else f"gathering evidence ({n}/{min_n})"),
            })
    return {"evidence_type": "inference", "observation_only": True, "automatic_edit": False,
            "requires_human_decision": True, "min_n": min_n, "prior_strength": prior_strength,
            "counts": counts, "lessons": lessons}


# ── Lessons: the improvement memory (second recursion level) ────────────────
#
# Idea adapted from google-research/harness2 (Apache-2.0): an "improvement
# harness" of evidence-supported lessons that guides future work, and is
# itself judged by results. Unlike harness2, nothing is adopted without a
# recorded human decision, and a lesson is only scored on forecasts made
# after its adoption (never on the evidence that produced it).

def lesson_id(text: str, scope: str) -> str:
    return "l-" + sha256_text(canonical({"text": text.strip(), "scope": scope.strip()}))[:16]


def lessons_index(records: list[dict]) -> dict:
    lessons: dict[str, dict] = {}
    for record in records:
        if record["type"] == "lesson":
            lessons[record["lesson_id"]] = {"lesson": record, "decisions": []}
        elif record["type"] == "lesson_decision":
            lessons[record["lesson_id"]]["decisions"].append(record)
    return lessons


def lesson_status(entry: dict) -> str:
    if not entry["decisions"]:
        return "proposed"
    return {"adopt": "adopted", "reject": "rejected", "retire": "retired"}[
        entry["decisions"][-1]["decision"]]


def propose_lesson(path: Path, text: str, scope: str, evidence_questions: list[str],
                   proposed_by: str) -> dict:
    """A lesson must cite resolved questions; ``scope`` is an author or ``*``."""
    records = load(path)
    verify(records)
    if not text or not text.strip():
        raise GateError("lesson text is required")
    if not scope or not scope.strip():
        raise GateError("scope is required (an author name or '*')")
    if not proposed_by or not proposed_by.strip():
        raise GateError("proposed_by is required")
    evidence = sorted(set(evidence_questions or []))
    if not evidence:
        raise GateError("a lesson needs evidence: cite resolved questions")
    questions, _, resolutions = index(records)
    for qid in evidence:
        if qid not in questions:
            raise GateError(f"unknown question {qid}")
        if qid not in resolutions:
            raise GateError(f"{qid} is not resolved: lessons only come from outcomes")
    lid = lesson_id(text, scope)
    if lid in lessons_index(records):
        raise GateError(f"{lid} already exists")
    return append(path, records, {
        "type": "lesson", "lesson_id": lid, "text": text.strip(), "scope": scope.strip(),
        "evidence_questions": evidence, "proposed_by": proposed_by.strip(),
        "evidence_type": "inference", "recorded_at": utc_now_iso(),
    })


def decide_lesson(path: Path, lid: str, decision: str, decided_by: str, reason: str) -> dict:
    """Human gate: proposed -> adopt|reject; adopted -> retire."""
    records = load(path)
    verify(records)
    lessons = lessons_index(records)
    if lid not in lessons:
        raise GateError(f"unknown lesson {lid}")
    if decision not in LESSON_DECISIONS:
        raise GateError(f"decision must be one of {LESSON_DECISIONS}")
    if not decided_by or not decided_by.strip():
        raise GateError("decided_by is required: a person decides")
    if not reason or not reason.strip():
        raise GateError("a decision needs a reason")
    status = lesson_status(lessons[lid])
    allowed = {"proposed": ("adopt", "reject"), "adopted": ("retire",)}.get(status, ())
    if decision not in allowed:
        raise GateError(f"cannot {decision} a lesson that is {status}")
    return append(path, records, {
        "type": "lesson_decision", "lesson_id": lid, "decision": decision,
        "decided_by": decided_by.strip(), "reason": reason.strip(), "human_decision": True,
        "decided_at": utc_now_iso(),
    })


def score_lessons(records: list[dict], min_n: int = 30, margin: float = 0.01) -> dict:
    """Second recursion level: does following a lesson improve later forecasts?

    ``before``: scored forecasts in the lesson's scope sealed before adoption.
    ``after``: scored forecasts that cite the lesson (necessarily sealed after
    adoption). Verdicts need ``min_n`` on both sides; the output only proposes."""
    pairs, _ = scored_pairs(records)
    report = []
    for lid, entry in lessons_index(records).items():
        adoption = next((d for d in entry["decisions"] if d["decision"] == "adopt"), None)
        lesson = entry["lesson"]
        row = {"lesson_id": lid, "text": lesson["text"], "scope": lesson["scope"],
               "status": lesson_status(entry)}
        if adoption is None:
            report.append(dict(row, verdict="not_adopted"))
            continue
        in_scope = [x for x in pairs if lesson["scope"] in ("*", x["author"])]
        before = [x for x in in_scope if x["seq"] < adoption["seq"]]
        after = [x for x in in_scope if lid in x["lessons"]]
        b_before, b_after = brier(before), brier(after)
        delta = None if b_before is None or b_after is None else b_after - b_before
        if len(before) < min_n or len(after) < min_n:
            verdict = "insufficient"
        elif delta < -margin:
            verdict = "helps"
        elif delta > margin:
            verdict = "hurts"
        else:
            verdict = "neutral"
        report.append(dict(row, n_before=len(before), n_after=len(after), brier_before=b_before,
                           brier_after=b_after, delta=delta, verdict=verdict,
                           proposal=("propose retiring it: forecasts got worse"
                                     if verdict == "hurts" else None)))
    return {"evidence_type": "inference", "observation_only": True,
            "requires_human_decision": True, "min_n": min_n, "margin": margin,
            "lessons": report}


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
    p.add_argument("--lessons", default="", help="comma-separated adopted lesson ids")
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
    p = sub.add_parser("lesson-propose")
    p.add_argument("--text", required=True)
    p.add_argument("--scope", required=True)
    p.add_argument("--evidence", required=True, help="comma-separated resolved question ids")
    p.add_argument("--proposed-by", required=True)
    p = sub.add_parser("lesson-decide")
    p.add_argument("--lesson", required=True)
    p.add_argument("--decision", required=True, choices=LESSON_DECISIONS)
    p.add_argument("--decided-by", required=True)
    p.add_argument("--reason", required=True)
    p = sub.add_parser("lesson-score")
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--margin", type=float, default=0.01)
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
                           args.rationale, args.made_at, _json_arg(args.sealed_evidence),
                           [x.strip() for x in args.lessons.split(",") if x.strip()])
        elif args.command == "resolve":
            out = resolve(args.ledger, args.question, args.outcome, _json_arg(args.observed),
                          args.resolved_at, args.void_reason)
        elif args.command == "lesson-propose":
            out = propose_lesson(args.ledger, args.text, args.scope,
                                 [x.strip() for x in args.evidence.split(",") if x.strip()],
                                 args.proposed_by)
        elif args.command == "lesson-decide":
            out = decide_lesson(args.ledger, args.lesson, args.decision, args.decided_by,
                                args.reason)
        else:
            records = load(args.ledger)
            verify(records)
            if args.command == "score":
                out = score(records)
            elif args.command == "learn":
                out = learn(records, args.min_n, args.prior_strength)
            else:
                out = score_lessons(records, args.min_n, args.margin)
    except GateError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
