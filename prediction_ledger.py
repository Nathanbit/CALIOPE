#!/usr/bin/env python3
"""Hash-chained ledger of binary predictions for CALIOPE.

Questions, forecasts, resolutions and lessons are appended to a JSONL file.
Every record stores the SHA-256 of the previous one, so editing, reordering or
deleting a line in the middle breaks the chain. ``verify`` also replays every
gate record by record, with the same function the writers use, so a record
crafted by hand with a correct hash is still rejected if it breaks a rule.

Design ideas adapted (no code copied) from:
- hey-research-lab/brier (MIT): sealed-before-answer ledger, refuse forecasts
  after resolution, refuse resolving questions without forecasts, unusable
  answers recorded as failures, dumb baselines.
- calibaudit (Apache-2.0): Murphy decomposition of the Brier score and
  small-sample warnings.
- Metaculus question checklist: absolute dates, explicit resolution source
  and criteria.
- google-research/harness2 (Apache-2.0): a persistent memory of
  evidence-supported lessons that guides future work and is itself judged.

Two recursion levels: ``learn`` turns past calibration into proposals, and
lessons (``lesson-propose``/``lesson-decide``/``lesson-score``) record what a
person adopted and whether following it improved later forecasts. Nothing is
applied automatically: models propose, deterministic gates verify, a person
decides.

Standard library only. Exit code 2 means a gate rejected the input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

import resolver

ROOT = Path(__file__).resolve().parent
GENESIS = "0" * 64
OUTCOMES = ("true", "false", "void")
FAILURE = "failure"
LESSON_DECISIONS = ("adopt", "reject", "retire")
RECORD_TYPES = ("question", "forecast", "resolution", "lesson", "lesson_decision")
CLOCK_SKEW_SECONDS = 300
LOCK_TIMEOUT_SECONDS = 10.0
HEX64 = re.compile(r"[0-9a-fA-F]{64}")
COMMIT = re.compile(r"[0-9a-fA-F]{7,40}")


class GateError(Exception):
    """A deterministic gate rejected the input (fail closed)."""


def _reject_constant(name: str) -> None:
    raise GateError(f"{name} is not valid JSON and is never stored")


def canonical(data: dict) -> str:
    """Stable serialization used for hashing and storage. NaN/inf are refused."""
    try:
        return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False)
    except ValueError as exc:
        raise GateError("NaN or infinity cannot be stored in the ledger") from exc


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_time(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise GateError(f"{field}: not an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateError(f"{field}: not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GateError(f"{field}: timezone required (naive datetimes are rejected)")
    return parsed.astimezone(timezone.utc)


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def now_utc() -> datetime:
    """Single clock for every write gate (patched in tests)."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return iso_utc(now_utc())


def _seconds(a: datetime, b: datetime) -> float:
    return (a - b).total_seconds()


def question_id(text: str, horizon_end: str, source: str, criteria: str,
                rule: dict | None = None) -> str:
    """Rewording any defining field, including the machine rule, yields a
    different question. Questions without a rule keep their original id."""
    body = {"text": text.strip(), "horizon_end": horizon_end, "source": source.strip(),
            "criteria": criteria.strip()}
    if rule is not None:
        body["rule"] = rule
    return "q-" + sha256_text(canonical(body))[:16]


def lesson_id(text: str, scope: str) -> str:
    return "l-" + sha256_text(canonical({"text": text.strip(), "scope": scope.strip()}))[:16]


def _text(record: dict, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise GateError(f"{field} is required")
    return value


# Storage

def load(path: Path) -> list[dict]:
    """Read records. Splits only on "\\n", so U+2028 and similar characters
    inside a text field cannot break a line."""
    if not path.exists():
        return []
    try:
        content = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise GateError(f"ledger unreadable as UTF-8: {exc}") from exc
    records = []
    for number, line in enumerate(content.split("\n"), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line, parse_constant=_reject_constant)
        except json.JSONDecodeError as exc:
            raise GateError(f"line {number}: invalid JSON") from exc
        if not isinstance(record, dict) or not {"type", "hash", "prev_hash", "seq"} <= set(record):
            raise GateError(f"line {number}: not a ledger record")
        records.append(record)
    return records


class State:
    """Ledger state rebuilt by replaying records in order."""

    def __init__(self) -> None:
        self.questions: dict[str, dict] = {}
        self.forecasts: dict[str, list[dict]] = {}
        self.resolutions: dict[str, dict] = {}
        self.lessons: dict[str, dict] = {}

    def lesson_status(self, lid: str) -> str:
        return lesson_status(self.lessons[lid])


def lesson_status(entry: dict) -> str:
    if not entry["decisions"]:
        return "proposed"
    return {"adopt": "adopted", "reject": "rejected", "retire": "retired"}[
        entry["decisions"][-1]["decision"]]


def check(state: State, record: dict) -> None:
    """Every gate, applied to one record against the state before it.
    Writers and ``verify`` use this same function."""
    kind = record.get("type")
    if kind not in RECORD_TYPES:
        raise GateError(f"unknown record type {kind!r}")
    recorded = parse_time(record.get("recorded_at", record.get("decided_at")), "recorded_at")
    if kind == "question":
        _check_question(state, record)
    elif kind == "forecast":
        _check_forecast(state, record, recorded)
    elif kind == "resolution":
        _check_resolution(state, record, recorded)
    elif kind == "lesson":
        _check_lesson(state, record)
    else:
        _check_decision(state, record)


def _check_question(state: State, record: dict) -> None:
    text, source, criteria = (_text(record, f) for f in ("text", "source", "criteria"))
    horizon = record.get("horizon_end")
    parse_time(horizon, "horizon_end")
    base_rate = record.get("base_rate")
    if base_rate is not None and (isinstance(base_rate, bool)
                                  or not isinstance(base_rate, (int, float))
                                  or not 0.0 < base_rate < 1.0):
        raise GateError("base_rate must be strictly between 0 and 1")
    rule = record.get("rule")
    if rule is not None:
        try:
            if resolver.validate_rule(rule, horizon) != rule:
                raise GateError("rule is not in normalized form")
        except resolver.ResolutionError as exc:
            raise GateError(str(exc)) from exc
    qid = question_id(text, horizon, source, criteria, rule)
    if record.get("question_id") != qid:
        raise GateError("question_id does not match the question fields")
    if qid in state.questions:
        raise GateError(f"{qid} already exists")


def _check_forecast(state: State, record: dict, recorded: datetime) -> None:
    qid = record.get("question_id")
    if qid not in state.questions:
        raise GateError(f"unknown question {qid}")
    if qid in state.resolutions:
        raise GateError("question already resolved: forecasts after the outcome are refused")
    question = state.questions[qid]
    author = _text(record, "author")
    status, probability = record.get("status"), record.get("probability")
    if status == "ok":
        if isinstance(probability, bool) or not isinstance(probability, (int, float)) \
                or not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise GateError("an ok forecast needs a probability between 0 and 1")
    elif status != FAILURE or probability is not None:
        raise GateError("status must be ok, or failure with no probability")
    made = parse_time(record.get("made_at"), "made_at")
    if made >= parse_time(question["horizon_end"], "horizon_end"):
        raise GateError("made_at is not before horizon_end")
    if _seconds(made, recorded) > CLOCK_SKEW_SECONDS:
        raise GateError("made_at is in the future")
    rule = question.get("rule")
    if rule and made > parse_time(rule["start"], "rule.start"):
        raise GateError("the rule's observation window has already started")
    evidence = record.get("sealed_evidence")
    backdated = _seconds(recorded, made) > CLOCK_SKEW_SECONDS
    if backdated:
        if not isinstance(evidence, dict) or not HEX64.fullmatch(str(evidence.get("sha256", ""))) \
                or not COMMIT.fullmatch(str(evidence.get("commit", ""))):
            raise GateError("backdated made_at requires sealed_evidence with a 64-hex sha256 "
                            "and the 7-40 hex commit that published it")
    applied = record.get("lessons_applied") or []
    if not isinstance(applied, list):
        raise GateError("lessons_applied must be a list")
    for lid in applied:
        if lid not in state.lessons or state.lesson_status(lid) != "adopted":
            raise GateError(f"{lid} is not an adopted lesson")
        if state.lessons[lid]["lesson"]["scope"] not in ("*", author):
            raise GateError(f"{lid} is scoped to another author")
        if backdated:
            raise GateError("backdated forecasts cannot cite lessons")


def _check_resolution(state: State, record: dict, recorded: datetime) -> None:
    qid = record.get("question_id")
    if qid not in state.questions:
        raise GateError(f"unknown question {qid}")
    if qid in state.resolutions:
        raise GateError("question already resolved")
    if not state.forecasts.get(qid):
        raise GateError("refusing to resolve a question without forecasts")
    outcome = record.get("outcome")
    if outcome not in OUTCOMES:
        raise GateError(f"outcome must be one of {OUTCOMES}")
    if outcome == "void" and not (isinstance(record.get("void_reason"), str)
                                  and record["void_reason"].strip()):
        raise GateError("void outcomes need a reason taken from the resolution criteria")
    question = state.questions[qid]
    resolved = parse_time(record.get("resolved_at"), "resolved_at")
    if resolved < parse_time(question["horizon_end"], "horizon_end"):
        raise GateError("cannot resolve before horizon_end")
    if _seconds(resolved, recorded) > CLOCK_SKEW_SECONDS:
        raise GateError("resolved_at is in the future: an outcome cannot be observed yet")
    observed = record.get("observed")
    if not isinstance(observed, dict) or not observed.get("source"):
        raise GateError("observed data must be an object with its source")
    if question.get("rule") and record.get("method") != "auto" and outcome != "void":
        raise GateError("a question with a machine rule resolves with resolve-auto "
                        "(manual resolution may only void it, with a reason)")


def _check_lesson(state: State, record: dict) -> None:
    text, scope = _text(record, "text"), _text(record, "scope")
    _text(record, "proposed_by")
    evidence = record.get("evidence_questions")
    if not isinstance(evidence, list) or not evidence:
        raise GateError("a lesson needs evidence: cite resolved questions")
    for qid in evidence:
        if qid not in state.questions:
            raise GateError(f"unknown question {qid}")
        if qid not in state.resolutions:
            raise GateError(f"{qid} is not resolved: lessons only come from outcomes")
    lid = lesson_id(text, scope)
    if record.get("lesson_id") != lid:
        raise GateError("lesson_id does not match the lesson fields")
    if lid in state.lessons:
        raise GateError(f"{lid} already exists")


def _check_decision(state: State, record: dict) -> None:
    lid = record.get("lesson_id")
    if lid not in state.lessons:
        raise GateError(f"unknown lesson {lid}")
    decision = record.get("decision")
    if decision not in LESSON_DECISIONS:
        raise GateError(f"decision must be one of {LESSON_DECISIONS}")
    _text(record, "decided_by")
    _text(record, "reason")
    status = state.lesson_status(lid)
    allowed = {"proposed": ("adopt", "reject"), "adopted": ("retire",)}.get(status, ())
    if decision not in allowed:
        raise GateError(f"cannot {decision} a lesson that is {status}")


def apply(state: State, record: dict) -> None:
    kind = record["type"]
    if kind == "question":
        state.questions[record["question_id"]] = record
    elif kind == "forecast":
        state.forecasts.setdefault(record["question_id"], []).append(record)
    elif kind == "resolution":
        state.resolutions[record["question_id"]] = record
    elif kind == "lesson":
        state.lessons[record["lesson_id"]] = {"lesson": record, "decisions": []}
    else:
        state.lessons[record["lesson_id"]]["decisions"].append(record)


def verify(records: list[dict]) -> State:
    """Check the chain and replay every gate. Returns the resulting state."""
    previous, state = GENESIS, State()
    for number, record in enumerate(records, 1):
        if record.get("prev_hash") != previous:
            raise GateError(f"line {number}: chain broken (prev_hash mismatch)")
        if record.get("seq") != number:
            raise GateError(f"line {number}: seq out of order")
        body = {k: v for k, v in record.items() if k != "hash"}
        if sha256_text(canonical(body)) != record.get("hash"):
            raise GateError(f"line {number}: record content does not match its hash")
        try:
            check(state, record)
        except GateError as exc:
            raise GateError(f"line {number}: {exc}") from exc
        apply(state, record)
        previous = record["hash"]
    return state


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Exclusive writer lock next to the ledger."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise GateError(f"ledger locked by another writer ({lock.name}); if no other "
                                "process is running, delete that file") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        os.unlink(lock)


def commit(path: Path, build: Callable[[State], dict]) -> dict:
    """Load, verify, build, check and append one record under the lock."""
    with _locked(path):
        records = load(path)
        state = verify(records)
        body = dict(build(state))
        body["prev_hash"] = records[-1]["hash"] if records else GENESIS
        body["seq"] = len(records) + 1
        body["hash"] = sha256_text(canonical(body))
        check(state, body)
        line = canonical(body) + "\n"
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        return body


def index(records: list[dict]) -> tuple[dict, dict, dict]:
    state = State()
    for record in records:
        apply(state, record)
    return state.questions, state.forecasts, state.resolutions


def lessons_index(records: list[dict]) -> dict:
    state = State()
    for record in records:
        apply(state, record)
    return state.lessons


# Writers

def ask(path: Path, text: str, horizon_end: str, source: str, criteria: str,
        base_rate: float | None = None, project: str | None = None,
        rule: dict | None = None) -> dict:
    horizon = iso_utc(parse_time(horizon_end, "horizon_end"))
    if rule is not None:
        try:
            rule = resolver.validate_rule(rule, horizon)
        except resolver.ResolutionError as exc:
            raise GateError(str(exc)) from exc
    clean = {"text": (text or "").strip(), "source": (source or "").strip(),
             "criteria": (criteria or "").strip()}
    for field, value in clean.items():
        if not value:
            raise GateError(f"{field} is required: a question must say how it resolves")

    def build(_: State) -> dict:
        return {"type": "question", "question_id": question_id(
                    clean["text"], horizon, clean["source"], clean["criteria"], rule),
                "horizon_end": horizon, "base_rate": base_rate, "project": project,
                "rule": rule, "recorded_at": utc_now_iso(), **clean}
    return commit(path, build)


def forecast(path: Path, qid: str, author: str, probability: float | None, rationale: str,
             made_at: str | None = None, sealed_evidence: dict | None = None,
             lessons_applied: list[str] | None = None) -> dict:
    """Seal a forecast. A ``made_at`` in the past needs ``sealed_evidence``
    (64-hex sha256 of an earlier sealed file and the commit that published it)."""
    status = "ok"
    if probability is None or isinstance(probability, bool) \
            or not isinstance(probability, (int, float)) or not math.isfinite(probability) \
            or not 0.0 <= probability <= 1.0:
        status, probability = FAILURE, None  # never coerced to 0.5, never dropped
    made = iso_utc(parse_time(made_at, "made_at")) if made_at is not None else None

    def build(_: State) -> dict:
        now = utc_now_iso()
        return {"type": "forecast", "question_id": qid, "author": (author or "").strip(),
                "probability": probability, "status": status,
                "rationale": (rationale or "").strip(), "made_at": made or now,
                "sealed_evidence": sealed_evidence,
                "lessons_applied": sorted(set(lessons_applied or [])), "recorded_at": now}
    return commit(path, build)


def resolve(path: Path, qid: str, outcome: str, observed: dict, resolved_at: str | None = None,
            void_reason: str | None = None, method: str = "manual") -> dict:
    def build(_: State) -> dict:
        now = utc_now_iso()
        return {"type": "resolution", "question_id": qid, "outcome": outcome,
                "void_reason": void_reason, "observed": observed, "method": method,
                "resolved_at": resolved_at or now, "recorded_at": now}
    return commit(path, build)


def resolve_auto(path: Path, qid: str, klines_path: Path) -> dict:
    """Resolve from observed candles with the question's own rule. The
    resolver sees only the rule and the data, never the forecasts."""
    questions, _, _ = index(load(path))
    if qid not in questions:
        raise GateError(f"unknown question {qid}")
    question = questions[qid]
    if not question.get("rule"):
        raise GateError("question has no machine rule: resolve it manually with evidence")
    try:
        data, data_sha = resolver.load_klines(klines_path)
        outcome, evidence = resolver.evaluate(question["rule"], question["horizon_end"], data)
    except (OSError, resolver.ResolutionError) as exc:
        raise GateError(f"automatic resolution refused: {exc}") from exc
    if _seconds(parse_time(data.get("fetched_at"), "fetched_at"), now_utc()) > CLOCK_SKEW_SECONDS:
        raise GateError("klines fetched_at is in the future")
    observed = dict(evidence, source=f"klines file ({data.get('source', 'unknown source')})",
                    data_sha256=data_sha, data_fetched_at=data.get("fetched_at"),
                    resolver="resolver.py", resolver_version=resolver.RESOLVER_VERSION)
    return resolve(path, qid, outcome, observed, void_reason=evidence.get("void_reason"),
                   method="auto")


def propose_lesson(path: Path, text: str, scope: str, evidence_questions: list[str],
                   proposed_by: str) -> dict:
    """A lesson must cite resolved questions; ``scope`` is an author or ``*``."""
    def build(_: State) -> dict:
        return {"type": "lesson", "lesson_id": lesson_id(text or "", scope or ""),
                "text": (text or "").strip(), "scope": (scope or "").strip(),
                "evidence_questions": sorted(set(evidence_questions or [])),
                "proposed_by": (proposed_by or "").strip(), "evidence_type": "inference",
                "recorded_at": utc_now_iso()}
    return commit(path, build)


def decide_lesson(path: Path, lid: str, decision: str, decided_by: str, reason: str) -> dict:
    """Human gate: proposed -> adopt|reject; adopted -> retire."""
    def build(_: State) -> dict:
        return {"type": "lesson_decision", "lesson_id": lid, "decision": decision,
                "decided_by": (decided_by or "").strip(), "reason": (reason or "").strip(),
                "human_decision": True, "decided_at": utc_now_iso()}
    return commit(path, build)


# Scoring (read-only)

def scored_pairs(records: list[dict]) -> tuple[list[dict], dict]:
    """One forecast per author and question: the first one sealed. Later
    forecasts by the same author are counted but never scored, so repeating
    a forecast cannot improve a score."""
    questions, forecasts, resolutions = index(records)
    pairs = []
    counts = {"questions": len(questions), "open": 0, "void": 0, "resolved": 0,
              "failures": 0, "repeat_forecasts_ignored": 0}
    failures_by_author: dict[str, int] = {}
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
        seen: set[str] = set()
        for item in sorted(forecasts.get(qid, []), key=lambda r: r["seq"]):
            if item["author"] in seen:
                counts["repeat_forecasts_ignored"] += 1
                continue
            seen.add(item["author"])
            if item["status"] == FAILURE:
                counts["failures"] += 1
                failures_by_author[item["author"]] = failures_by_author.get(item["author"], 0) + 1
                continue
            pairs.append({"author": item["author"], "p": float(item["probability"]), "y": y,
                          "base_rate": questions[qid].get("base_rate"), "question_id": qid,
                          "seq": item["seq"], "lessons": item.get("lessons_applied") or []})
    counts["failures_by_author"] = failures_by_author
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


def _bin(p: float, bins: int) -> int:
    return min(int(p * bins), bins - 1)


def _bin_label(key: int, bins: int) -> str:
    return f"{key / bins:.1f}-{(key + 1) / bins:.1f}"


def _grouped(pairs: list[dict], bins: int) -> dict[int, list[dict]]:
    groups: dict[int, list[dict]] = {}
    for x in pairs:
        groups.setdefault(_bin(x["p"], bins), []).append(x)
    return groups


def murphy(pairs: list[dict], bins: int = 10) -> dict | None:
    """Brier = REL - RES + UNC (+ within-bin terms when forecasts share a bin
    but differ). Reports the residual so the identity is checkable."""
    if not pairs:
        return None
    n = len(pairs)
    base = sum(x["y"] for x in pairs) / n
    rel = res = ece = 0.0
    table = []
    for key, group in sorted(_grouped(pairs, bins).items()):
        mean_p = sum(x["p"] for x in group) / len(group)
        freq = sum(x["y"] for x in group) / len(group)
        rel += len(group) * (mean_p - freq) ** 2
        res += len(group) * (freq - base) ** 2
        ece += len(group) * abs(mean_p - freq)
        table.append({"bin": _bin_label(key, bins), "n": len(group),
                      "mean_forecast": round(mean_p, 4), "observed_freq": round(freq, 4)})
    rel, res, unc = rel / n, res / n, base * (1 - base)
    score_value = brier(pairs)
    return {"brier": score_value, "reliability": rel, "resolution": res, "uncertainty": unc,
            "within_bin_residual": score_value - (rel - res + unc), "bins": table, "ece": ece / n}


def score(records: list[dict]) -> dict:
    pairs, counts = scored_pairs(records)
    board = []
    for author in sorted({x["author"] for x in pairs} | set(counts["failures_by_author"])):
        own = [x for x in pairs if x["author"] == author]
        failures = counts["failures_by_author"].get(author, 0)
        board.append({"author": author, "n": len(own), "brier": brier(own),
                      "log_score": log_score(own), "failures": failures,
                      "failure_rate": failures / (len(own) + failures)})
    with_base = [x for x in pairs if x["base_rate"] is not None]
    report = {"counts": counts, "scoreboard": board,
              "baselines": {
                  "coin": {"n": len(pairs), "brier": brier([dict(x, p=0.5) for x in pairs])},
                  "base_rate": {"n": len(with_base),
                                "brier": brier([dict(x, p=x["base_rate"]) for x in with_base]),
                                "authors_on_same_questions": brier(with_base)}},
              "calibration": murphy(pairs)}
    if len(pairs) < 30:
        report["warning"] = (f"only {len(pairs)} scored forecasts: calibration and skill are "
                             "not statistically meaningful yet")
    return report


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
        for key, group in sorted(_grouped(own, bins).items()):
            n = len(group)
            stated = sum(x["p"] for x in group) / n
            hits = sum(x["y"] for x in group)
            shrunk = (hits + prior_strength * stated) / (n + prior_strength)
            ready = n >= min_n
            lessons.append({
                "evidence_questions": sorted({x["question_id"] for x in group}),
                "author": author, "bin": _bin_label(key, bins), "n": n,
                "stated_mean": round(stated, 4), "observed_freq": round(hits / n, 4),
                "shrunk_freq": round(shrunk, 4), "ready": ready,
                "proposal": (f"When {author} states ~{stated:.0%}, treat it as ~{shrunk:.0%}"
                             if ready else f"gathering evidence ({n}/{min_n})"),
            })
    return {"evidence_type": "inference", "observation_only": True, "automatic_edit": False,
            "requires_human_decision": True, "min_n": min_n, "prior_strength": prior_strength,
            "counts": counts, "lessons": lessons}


def score_lessons(records: list[dict], min_n: int = 30, margin: float = 0.01) -> dict:
    """Second recursion level: does following a lesson improve later forecasts?

    ``before``: scored forecasts in the lesson's scope sealed before adoption,
    excluding the questions cited as the lesson's own evidence (they were
    chosen because they went badly, which would flatter the lesson).
    ``after``: scored forecasts that cite the lesson. Verdicts need ``min_n``
    on both sides; the output only proposes."""
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
        evidence = set(lesson["evidence_questions"])
        in_scope = [x for x in pairs if lesson["scope"] in ("*", x["author"])]
        before = [x for x in in_scope
                  if x["seq"] < adoption["seq"] and x["question_id"] not in evidence]
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


# CLI

def json_arg(value: str | None) -> dict | None:
    """Inline JSON, or ``@path`` to a JSON file inside the CALIOPE folder."""
    if value is None:
        return None
    if value.startswith("@"):
        target = (ROOT / value[1:]).resolve()
        if ROOT not in target.parents:
            raise GateError("@path must point to a file inside the CALIOPE folder")
        try:
            value = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise GateError(f"cannot read {value}: {exc}") from exc
    try:
        data = json.loads(value, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise GateError("expected a JSON object, or @path to one") from exc
    if not isinstance(data, dict):
        raise GateError("expected a JSON object")
    return data


def _ids(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def _read_only(command: Callable[[list[dict]], dict]) -> Callable[[argparse.Namespace], dict]:
    def run(args: argparse.Namespace) -> dict:
        records = load(args.ledger)
        verify(records)
        return command(records)
    return run


def _verify_command(args: argparse.Namespace) -> dict:
    records = load(args.ledger)
    verify(records)
    return {"ok": True, "records": len(records),
            "head": records[-1]["hash"] if records else GENESIS}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ledger", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify").set_defaults(run=_verify_command)
    p = sub.add_parser("ask")
    for flag in ("--text", "--horizon-end", "--source", "--criteria"):
        p.add_argument(flag, required=True)
    p.add_argument("--base-rate", type=float)
    p.add_argument("--project")
    p.add_argument("--rule", help="machine rule as inline JSON or @path (see resolver.py)")
    p.set_defaults(run=lambda a: ask(a.ledger, a.text, a.horizon_end, a.source, a.criteria,
                                     a.base_rate, a.project, json_arg(a.rule)))
    p = sub.add_parser("forecast")
    p.add_argument("--question", required=True)
    p.add_argument("--author", required=True)
    p.add_argument("--probability", type=float)
    p.add_argument("--rationale", default="")
    p.add_argument("--made-at")
    p.add_argument("--sealed-evidence", help="inline JSON or @path")
    p.add_argument("--lessons", default="", help="comma-separated adopted lesson ids")
    p.set_defaults(run=lambda a: forecast(a.ledger, a.question, a.author, a.probability,
                                          a.rationale, a.made_at, json_arg(a.sealed_evidence),
                                          _ids(a.lessons)))
    p = sub.add_parser("resolve")
    p.add_argument("--question", required=True)
    p.add_argument("--outcome", required=True, choices=OUTCOMES)
    p.add_argument("--observed", required=True, help="inline JSON or @path")
    p.add_argument("--resolved-at")
    p.add_argument("--void-reason")
    p.set_defaults(run=lambda a: resolve(a.ledger, a.question, a.outcome, json_arg(a.observed),
                                         a.resolved_at, a.void_reason))
    p = sub.add_parser("resolve-auto")
    p.add_argument("--question", required=True)
    p.add_argument("--klines", required=True, type=Path)
    p.set_defaults(run=lambda a: resolve_auto(a.ledger, a.question, a.klines))
    sub.add_parser("score").set_defaults(run=_read_only(score))
    p = sub.add_parser("learn")
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--prior-strength", type=float, default=20.0)
    p.set_defaults(run=lambda a: _read_only(
        lambda r: learn(r, a.min_n, a.prior_strength))(a))
    p = sub.add_parser("lesson-propose")
    p.add_argument("--text", required=True)
    p.add_argument("--scope", required=True)
    p.add_argument("--evidence", required=True, help="comma-separated resolved question ids")
    p.add_argument("--proposed-by", required=True)
    p.set_defaults(run=lambda a: propose_lesson(a.ledger, a.text, a.scope, _ids(a.evidence),
                                                a.proposed_by))
    p = sub.add_parser("lesson-decide")
    p.add_argument("--lesson", required=True)
    p.add_argument("--decision", required=True, choices=LESSON_DECISIONS)
    p.add_argument("--decided-by", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(run=lambda a: decide_lesson(a.ledger, a.lesson, a.decision, a.decided_by,
                                               a.reason))
    p = sub.add_parser("lesson-score")
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--margin", type=float, default=0.01)
    p.set_defaults(run=lambda a: _read_only(
        lambda r: score_lessons(r, a.min_n, a.margin))(a))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        out = args.run(args)
    except GateError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
