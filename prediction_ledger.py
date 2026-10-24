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
person adopted and whether following it improved later forecasts. Scoring is
read-only and lives in ``forecast_scoring.py``. Nothing is
applied automatically: models propose, deterministic gates verify, a person
decides.

Standard library only. Exit code 2 means a gate rejected the input.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import concepts
import evidence_io as eio
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
# Who forecasts. A person is a bare name ("nathan"); everything else carries
# its kind, so a model, an indicator, a simulation and an outside expert are
# never confused and each keeps its own chain and score: "ia:gemini",
# "indicador:macroflow", "sim:mirofish", "experto:fulano". Lower case only:
# "Gemini" and "gemini" are one author.
# From the merge of the continuous-state feature (PR #21) every live forecast
# must carry its thought_state; older records stay valid as they are.
THOUGHT_REQUIRED_FROM = datetime(2026, 10, 23, 12, 18, 32, tzinfo=timezone.utc)
AUTHOR_KINDS = ("ia", "indicador", "sim", "experto")
AUTHOR = re.compile(r"[a-z0-9][a-z0-9_.-]{0,39}(?::[a-z0-9][a-z0-9_.-]{0,39})?")


class GateError(Exception):
    """A deterministic gate rejected the input (fail closed)."""


def canonical(data: dict) -> str:
    """Stable serialization used for hashing and storage. NaN/inf are refused."""
    return eio.canonical_text(data, GateError, "NaN or infinity cannot be stored in the ledger")


def sha256_text(text: str) -> str:
    return eio.sha256_hex(text.encode("utf-8"), upper=False)


def parse_time(value: object, field: str) -> datetime:
    return eio.parse_aware_time(value, field, GateError)


iso_utc = eio.iso_utc


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


def normalize_author(name: str | None) -> str:
    return (name or "").strip().lower()


def _author(record: dict, field: str = "author") -> str:
    author = _text(record, field)
    if not AUTHOR.fullmatch(author):
        raise GateError(f"{field} must be a lower-case name or kind:name, e.g. nathan, "
                        f"ia:gemini, indicador:macroflow (got {author!r})")
    if ":" in author and author.split(":", 1)[0] not in AUTHOR_KINDS:
        raise GateError(f"{field} kind must be one of {AUTHOR_KINDS}")
    return author


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
            record = eio.loads_strict(line, GateError)
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
    concept = record.get("concept")
    if concept is not None:
        try:
            if concepts.validate_concept(concept) != concept:
                raise GateError("concept is not in normalized form")
            generated = concepts.question_fields(concept)
            expected_class = concepts.concept_class(concept)
        except concepts.ConceptError as exc:
            raise GateError(f"concept: {exc}") from exc
        stored = {"text": text, "source": source, "criteria": criteria, "horizon_end": horizon,
                  "rule": rule}
        if stored != generated:
            raise GateError("question fields do not match the wording generated from its concept")
        if record.get("concept_class") != expected_class:
            raise GateError("concept_class does not match the concept")
    elif record.get("concept_class") is not None:
        raise GateError("concept_class without a concept")
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
    author = _author(record)
    if "thought_state" in record:
        if record.get("sealed_evidence") is not None:
            raise GateError("backdated forecasts cannot carry a thought state")
        stored = record["thought_state"]
        version = stored.get("version") if isinstance(stored, dict) else None
        import forecast_scoring as fs
        if version not in fs.THOUGHT_VERSIONS:
            raise GateError(f"unknown thought_state version {version!r}")
        if stored != _thought(state, qid, author, version):
            raise GateError("thought_state does not match the ledger before this forecast")
    elif record.get("sealed_evidence") is None and recorded >= THOUGHT_REQUIRED_FROM:
        raise GateError("a forecast sealed now must carry its thought_state "
                        "(only backdated forecasts with sealed evidence go without)")
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
    if scope != "*":
        _author(record, "scope")
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


def _locked(path: Path):
    """Exclusive writer lock next to the ledger."""
    lock = path.with_name(path.name + ".lock")
    return eio.exclusive_lock(lock, GateError, f"ledger locked by another writer ({lock.name}); "
                              "if no other process is running, delete that file",
                              timeout=LOCK_TIMEOUT_SECONDS)


def commit_many(path: Path, builds: list[Callable[[State], dict]],
                dry_run: bool = False) -> list[dict]:
    """Load, verify, then build and check every record before writing any of
    them, all under one lock: either all records are appended or none.
    ``dry_run`` runs every gate and returns the records without writing."""
    with _locked(path):
        records = load(path)
        state = verify(records)
        previous = records[-1]["hash"] if records else GENESIS
        bodies = []
        for build in builds:
            body = dict(build(state))
            body["prev_hash"] = previous
            body["seq"] = len(records) + len(bodies) + 1
            body["hash"] = sha256_text(canonical(body))
            check(state, body)
            apply(state, body)
            bodies.append(body)
            previous = body["hash"]
        if dry_run:
            return bodies
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("".join(canonical(body) + "\n" for body in bodies))
            handle.flush()
            os.fsync(handle.fileno())
        return bodies


def commit(path: Path, build: Callable[[State], dict]) -> dict:
    return commit_many(path, [build])[0]


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


def _concept_question(concept: dict, project: str | None) -> Callable[[State], dict]:
    try:
        normalized = concepts.validate_concept(concept)
        fields = concepts.question_fields(normalized)
        label = concepts.concept_class(normalized)
    except concepts.ConceptError as exc:
        raise GateError(f"concept: {exc}") from exc

    def build(_: State) -> dict:
        return {"type": "question", "question_id": question_id(
                    fields["text"], fields["horizon_end"], fields["source"], fields["criteria"],
                    fields["rule"]),
                "base_rate": None, "project": project, "concept": normalized,
                "concept_class": label, "recorded_at": utc_now_iso(), **fields}
    return build


def seal(path: Path, concept: dict, author: str, probability: float | None, rationale: str,
         project: str | None = None, lessons_applied: list[str] | None = None,
         dry_run: bool = False) -> dict:
    """Step 2 shortcut: one concept becomes a question plus the author's
    forecast, appended together or not at all."""
    question = _concept_question(concept, project)
    holder: dict = {}

    def ask_build(state: State) -> dict:
        holder.update(question(state))
        return holder

    def forecast_build(state: State) -> dict:
        return _forecast_body(holder["question_id"], author, probability, rationale, None, None,
                              lessons_applied, state)
    q_record, f_record = commit_many(path, [ask_build, forecast_build], dry_run=dry_run)
    return {"dry_run": dry_run, "question": q_record, "forecast": f_record}


def _thought(state: State, qid: str, author: str, version: str | None = None) -> dict | None:
    import forecast_scoring as fs  # imports this module; loaded lazily to avoid a cycle

    question = state.questions.get(qid)
    if question is None:
        return None
    return fs.thought_state(state, author, question.get("concept_class"),
                            version=version or fs.THOUGHT_VERSION)


def _forecast_body(qid: str, author: str, probability: float | None, rationale: str,
                   made_at: str | None, sealed_evidence: dict | None,
                   lessons_applied: list[str] | None, state: State | None = None) -> dict:
    status = "ok"
    if probability is None or isinstance(probability, bool) \
            or not isinstance(probability, (int, float)) or not math.isfinite(probability) \
            or not 0.0 <= probability <= 1.0:
        status, probability = FAILURE, None  # never coerced to 0.5, never dropped
    now = utc_now_iso()
    made = iso_utc(parse_time(made_at, "made_at")) if made_at is not None else now
    body = {"type": "forecast", "question_id": qid, "author": normalize_author(author),
            "probability": probability, "status": status, "rationale": (rationale or "").strip(),
            "made_at": made, "sealed_evidence": sealed_evidence,
            "lessons_applied": sorted(set(lessons_applied or [])), "recorded_at": now}
    if state is not None and made_at is None:
        # Backdated forecasts carry no thought state: the ledger after made_at
        # would leak into it.
        thought = _thought(state, qid, body["author"])
        if thought is not None:
            body["thought_state"] = thought
    return body


def forecast(path: Path, qid: str, author: str, probability: float | None, rationale: str,
             made_at: str | None = None, sealed_evidence: dict | None = None,
             lessons_applied: list[str] | None = None) -> dict:
    """Seal a forecast. A ``made_at`` in the past needs ``sealed_evidence``
    (64-hex sha256 of an earlier sealed file and the commit that published it)."""
    if made_at is not None:
        parse_time(made_at, "made_at")
    return commit(path, lambda state: _forecast_body(qid, author, probability, rationale, made_at,
                                                     sealed_evidence, lessons_applied, state))


def resolve(path: Path, qid: str, outcome: str, observed: dict, resolved_at: str | None = None,
            void_reason: str | None = None, method: str = "manual") -> dict:
    def build(_: State) -> dict:
        now = utc_now_iso()
        return {"type": "resolution", "question_id": qid, "outcome": outcome,
                "void_reason": void_reason, "observed": observed, "method": method,
                "resolved_at": resolved_at or now, "recorded_at": now}
    return commit(path, build)


def resolve_auto(path: Path, qid: str, klines_path: Path, fine_path: Path | None = None) -> dict:
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
        same_candle = outcome == "void" and "one candle" in (evidence.get("void_reason") or "")
        if same_candle and fine_path is None:
            raise resolver.ResolutionError(
                "both levels were touched inside one candle: pass --fine-klines (e.g. 1s) to "
                "order them; if finer data cannot be had, void it by hand with that reason")
        if fine_path is not None and same_candle:
            fine, fine_sha = resolver.load_klines(fine_path)
            outcome, extra = resolver.tiebreak(question["rule"], question["horizon_end"], evidence,
                                               fine)
            evidence = {k: v for k, v in evidence.items() if k != "void_reason"}
            evidence.update(extra, tiebreak_data_sha256=fine_sha,
                            tiebreak_data_fetched_at=fine.get("fetched_at"))
    except (OSError, resolver.ResolutionError) as exc:
        raise GateError(f"automatic resolution refused: {exc}") from exc
    if _seconds(parse_time(data.get("fetched_at"), "fetched_at"), now_utc()) > CLOCK_SKEW_SECONDS:
        raise GateError("klines fetched_at is in the future")
    try:
        volatility = resolver.volatility_rival(question["rule"], question["horizon_end"], data)
    except resolver.ResolutionError as exc:
        volatility = {"available": False, "reason": str(exc)}
    observed = dict(evidence, source=f"klines file ({data.get('source', 'unknown source')})",
                    data_sha256=data_sha, data_fetched_at=data.get("fetched_at"),
                    resolver="resolver.py", resolver_version=resolver.RESOLVER_VERSION,
                    volatility_rival=volatility)
    return resolve(path, qid, outcome, observed, void_reason=evidence.get("void_reason"),
                   method="auto")


def propose_lesson(path: Path, text: str, scope: str, evidence_questions: list[str],
                   proposed_by: str) -> dict:
    """A lesson must cite resolved questions; ``scope`` is an author or ``*``."""
    def build(_: State) -> dict:
        return {"type": "lesson", "lesson_id": lesson_id(text or "", normalize_author(scope)),
                "text": (text or "").strip(), "scope": normalize_author(scope),
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
        data = eio.loads_strict(value, GateError)
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


def _state_command(args: argparse.Namespace) -> dict:
    import forecast_scoring as fs

    state = verify(load(args.ledger))
    label = None
    if args.question:
        if args.question not in state.questions:
            raise GateError(f"unknown question {args.question}")
        label = state.questions[args.question].get("concept_class")
    return fs.thought_state(state, normalize_author(args.author), label)


def _verify_command(args: argparse.Namespace) -> dict:
    records = load(args.ledger)
    verify(records)
    return {"ok": True, "records": len(records),
            "head": records[-1]["hash"] if records else GENESIS}


def build_parser() -> argparse.ArgumentParser:
    import forecast_scoring as fs  # imports this module; loaded lazily to avoid a cycle

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
    p = sub.add_parser("seal", help="concept -> question + forecast in one step")
    p.add_argument("--concept", required=True, help="inline JSON or @path (see concepts.py)")
    p.add_argument("--author", required=True)
    p.add_argument("--probability", type=float, required=True)
    p.add_argument("--rationale", default="")
    p.add_argument("--project")
    p.add_argument("--lessons", default="", help="comma-separated adopted lesson ids")
    p.add_argument("--dry-run", action="store_true",
                   help="run every gate and print the records without writing them")
    p.set_defaults(run=lambda a: seal(a.ledger, json_arg(a.concept), a.author, a.probability,
                                      a.rationale, a.project, _ids(a.lessons), a.dry_run))
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
    p.add_argument("--fine-klines", type=Path,
                   help="finer candles (e.g. 1s) to break a touch_first tie inside one candle")
    p.set_defaults(run=lambda a: resolve_auto(a.ledger, a.question, a.klines, a.fine_klines))
    sub.add_parser("score").set_defaults(run=_read_only(fs.score))
    p = sub.add_parser("rival", help="each author against a rival that only knows the past")
    p.add_argument("--resamples", type=int, default=2000)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--min-authors", type=int, default=2,
                   help="authors a question needs before the swarm forecasts it")
    p.set_defaults(run=lambda a: _read_only(
        lambda r: fs.rival(r, a.resamples, a.seed, a.min_n, a.min_authors))(a))
    p = sub.add_parser("state", help="the author's continuous thought state now")
    p.add_argument("--author", required=True)
    p.add_argument("--question", help="use this question's concept class")
    p.set_defaults(run=lambda a: _state_command(a))
    p = sub.add_parser("learn")
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--prior-strength", type=float, default=20.0)
    p.add_argument("--by", choices=("author", "concept"), default="author",
                   help="concept: learn per kind of prediction (concept class)")
    p.set_defaults(run=lambda a: _read_only(
        lambda r: fs.learn(r, a.min_n, a.prior_strength, group_by=a.by))(a))
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
        lambda r: fs.score_lessons(r, a.min_n, a.margin))(a))
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
