"""Fail-closed research orchestrator for MacroFlow.

This runner does not call models, modify Pine, publish, or trade. It creates
bounded task packets and ingests the JSON returned by independent reviewers.
Only deterministic state transitions are automated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
CONTRACTS = ROOT / "contracts"
ROLES = {
    "perplexity": "observation",
    "deepseek": "observation",
    "qwen": "observation",
    "zai": "observation",
    "kimi": "observation",
    "gemini": "observation",
    "meta": "observation",
    "nvidia_nemotron": "observation",
}
LIMITS = {
    "max_iterations": 2,
    "max_candidates": 2,
    "max_calls": 16,
    "max_input_tokens": 60_000,
    "max_output_tokens": 12_000,
    "max_cost_usd": 2.0,
}
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
SAFE_TARGET = re.compile(r"^(source|tests|docs)/[A-Za-z0-9_./-]+$")
STAGES = {
    "observe_parallel", "evidence_merge", "decision", "human_review",
    "archived_rejected", "stopped_iteration_limit",
}


class GateError(ValueError):
    """An input failed a deterministic gate."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise GateError(f"value is not canonical JSON: {exc}") from None
    return text.encode("utf-8")


def read_json(path: Path) -> dict:
    def reject_constant(value: str) -> None:
        raise GateError(f"non-finite JSON number is forbidden: {value}")

    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise GateError(f"{path}: JSON root must be an object")
    return value


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(canonical_bytes(value) + b"\n")
    temp.replace(path)


def _required(kind: str) -> set[str]:
    schema = read_json(CONTRACTS / f"{kind}.schema.json")
    return set(schema["required"])


def _strict_keys(value: dict, kind: str) -> None:
    schema = read_json(CONTRACTS / f"{kind}.schema.json")
    allowed = set(schema["properties"])
    missing = set(schema["required"]) - set(value)
    extra = set(value) - allowed
    if missing:
        raise GateError(f"missing {kind} keys: {sorted(missing)}")
    if extra:
        raise GateError(f"unexpected {kind} keys: {sorted(extra)}")


def _bounded_text(value: Any, name: str, minimum: int = 0, maximum: int = 2_000) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise GateError(f"invalid {name}")
    return value


def _string_list(value: Any, name: str, minimum: int, maximum: int, *, unique: bool = False) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise GateError(f"invalid {name}")
    if any(not isinstance(item, str) for item in value):
        raise GateError(f"{name} must contain strings")
    if unique and len(set(value)) != len(value):
        raise GateError(f"{name} must be unique")
    return value


def _hex(value: Any, name: str) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise GateError(f"invalid {name}")
    return value.upper()


def _safe_target(value: str) -> str:
    if not SAFE_TARGET.fullmatch(value):
        raise GateError(f"unsafe target path: {value}")
    parts = Path(value).parts
    if ".." in parts or Path(value).is_absolute():
        raise GateError(f"unsafe target path: {value}")
    return value


def _proposal_diff_targets(content: str) -> set[str]:
    changed: set[str] = set()
    for line in content.splitlines():
        if not line.startswith("+++ "):
            continue
        target = line[4:].split("\t", 1)[0].strip()
        if target == "/dev/null":
            continue
        if target.startswith("b/"):
            target = target[2:]
        changed.add(_safe_target(target))
    if not changed or "@@" not in content:
        raise GateError("proposal patch is not a bounded unified diff")
    return changed


def _evidence_fingerprint(value: dict, kind: str) -> str:
    """Stable semantic fingerprint across recursive iterations.

    Identity, budget and parent hashes are deliberately omitted so an agent
    cannot resubmit the same analysis with only a new iteration number.
    """
    if kind == "observation":
        body = {
            "agent_id": value["agent_id"], "role": value["role"],
            "findings": value["findings"], "blockers": value["blockers"],
            "suggested_next_tasks": value.get("suggested_next_tasks", []),
        }
    else:
        body = {
            "agent_id": value["agent_id"], "hypothesis": value["hypothesis"],
            "target_files": value["target_files"], "invariants": value["invariants"],
            "validation_plan": value["validation_plan"], "rollback": value["rollback"],
            "patch_sha256": value["patch"]["sha256"].upper(),
        }
    return sha256_bytes(canonical_bytes(body))


def validate_response(value: dict, kind: str, run_id: str, iteration: int, agent: str) -> None:
    if kind not in {"observation", "proposal"}:
        raise GateError("reviewer response kind must be observation or proposal")
    _strict_keys(value, kind)
    if value.get("schema_version") != "1.0" or value.get("run_id") != run_id:
        raise GateError("schema_version or run_id mismatch")
    if value.get("iteration") != iteration or value.get("agent_id") != agent:
        raise GateError("iteration or agent_id mismatch")
    if ROLES.get(agent) != kind:
        raise GateError(f"{agent} must return {ROLES.get(agent)}, not {kind}")
    hashes = value.get("input_hashes", []) if kind == "observation" else [value.get("parent_candidate_hash")]
    if not hashes or any(not isinstance(x, str) or not HEX64.fullmatch(x) for x in hashes):
        raise GateError("invalid input hash")
    if kind == "observation":
        _bounded_text(value.get("role"), "role", 3, 100)
        _string_list(value.get("input_hashes"), "input_hashes", 1, 32, unique=True)
        findings = value.get("findings")
        if not isinstance(findings, list) or len(findings) > 8:
            raise GateError("invalid findings")
        finding_keys = {"id", "kind", "claim", "evidence_refs", "confidence", "uncertainty"}
        for finding in findings:
            if not isinstance(finding, dict) or set(finding) != finding_keys:
                raise GateError("invalid finding object")
            if not re.fullmatch(r"[A-Za-z0-9_-]{3,80}", str(finding.get("id", ""))):
                raise GateError("invalid finding id")
            if finding.get("kind") not in {"fact", "inference", "hypothesis", "failure", "metric"}:
                raise GateError("invalid finding kind")
            _bounded_text(finding.get("claim"), "finding claim", 1, 2_000)
            refs = _string_list(finding.get("evidence_refs"), "finding evidence_refs", 0, 40, unique=True)
            if any(not 3 <= len(ref) <= 300 for ref in refs):
                raise GateError("invalid finding evidence_ref")
            confidence = finding.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise GateError("invalid finding confidence")
            _bounded_text(finding.get("uncertainty"), "finding uncertainty", 0, 1_000)
        blockers = _string_list(value.get("blockers"), "blockers", 0, 5)
        if any(len(item) > 500 for item in blockers):
            raise GateError("invalid blocker")
        if "suggested_next_tasks" in value:
            tasks = _string_list(value["suggested_next_tasks"], "suggested_next_tasks", 0, 3)
            if any(len(item) > 500 for item in tasks):
                raise GateError("invalid suggested_next_task")
        budget = value.get("budget_used", {})
        if not isinstance(budget, dict) or set(budget) != {"input_tokens", "output_tokens", "cost_usd"}:
            raise GateError("invalid budget_used")
        token_values = (budget.get("input_tokens"), budget.get("output_tokens"))
        if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in token_values):
            raise GateError("invalid token budget_used")
        cost = budget.get("cost_usd")
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
            raise GateError("invalid budget_used")
    else:
        _hex(value.get("parent_candidate_hash"), "parent_candidate_hash")
        _bounded_text(value.get("hypothesis"), "hypothesis", 10, 1_500)
        if "expected_effect" in value:
            _bounded_text(value["expected_effect"], "expected_effect", 0, 1_000)
        targets = _string_list(value.get("target_files"), "target_files", 1, 3, unique=True)
        targets = [_safe_target(target) for target in targets]
        invariants = _string_list(value.get("invariants"), "invariants", 1, 12)
        validation = _string_list(value.get("validation_plan"), "validation_plan", 1, 12)
        evidence = _string_list(value.get("evidence_refs"), "evidence_refs", 1, 40, unique=True)
        if any(len(item) > 300 for item in invariants) or any(len(item) > 500 for item in validation):
            raise GateError("proposal plan exceeds bounds")
        if any(not 3 <= len(item) <= 300 for item in evidence):
            raise GateError("invalid proposal evidence_ref")
        _bounded_text(value.get("rollback"), "rollback", 3, 500)
        patch = value.get("patch", {})
        if not isinstance(patch, dict) or set(patch) != {"format", "sha256", "content"}:
            raise GateError("invalid proposal patch")
        content = patch.get("content", "")
        if not isinstance(content, str) or not 1 <= len(content) <= 100_000:
            raise GateError("invalid proposal patch content")
        _hex(patch.get("sha256"), "proposal patch hash")
        if patch.get("format") != "unified_diff" or sha256_bytes(content.encode("utf-8")) != patch["sha256"].upper():
            raise GateError("proposal patch hash mismatch")
        changed = _proposal_diff_targets(content)
        if not changed.issubset(set(targets)):
            raise GateError("proposal diff modifies a file outside target_files")


def validate_decision(value: dict, manifest: dict) -> None:
    _strict_keys(value, "decision")
    if value.get("schema_version") != "1.0" or value.get("run_id") != manifest["run_id"]:
        raise GateError("decision identity mismatch")
    if value.get("iteration") != manifest["iteration"]:
        raise GateError("decision iteration mismatch")
    if value.get("baseline_hash") != manifest["baseline_hash"]:
        raise GateError("decision baseline hash mismatch")
    candidate_hash = _hex(value.get("candidate_hash"), "candidate_hash")
    known_candidates = {manifest["current_candidate_hash"], *manifest.get("proposal_hashes", [])}
    if candidate_hash not in known_candidates:
        raise GateError("decision candidate hash is not known to this run")
    if value.get("decision") not in {"reject", "revise", "human_review"}:
        raise GateError("invalid decision")
    if value.get("human_approval_required") is not True:
        raise GateError("human approval must remain required")
    gates = value.get("gate_results")
    if not isinstance(gates, list) or not 1 <= len(gates) <= 40:
        raise GateError("decision lacks gates or reasons")
    gate_ids: set[str] = set()
    statuses: list[str] = []
    expected_gate_keys = {"gate_id", "status", "observed", "threshold", "evidence_refs"}
    for gate in gates:
        if not isinstance(gate, dict) or set(gate) != expected_gate_keys:
            raise GateError("invalid gate result")
        gate_id = gate.get("gate_id")
        if not isinstance(gate_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{3,100}", gate_id) or gate_id in gate_ids:
            raise GateError("invalid or repeated gate_id")
        gate_ids.add(gate_id)
        if gate.get("status") not in {"pass", "fail", "insufficient_data"}:
            raise GateError("invalid gate status")
        statuses.append(gate["status"])
        refs = _string_list(gate.get("evidence_refs"), "gate evidence_refs", 1, 40)
        if any(not 3 <= len(ref) <= 300 for ref in refs):
            raise GateError("invalid gate evidence_ref")
        canonical_bytes(gate.get("observed"))
        canonical_bytes(gate.get("threshold"))
    reasons = _string_list(value.get("reasons"), "reasons", 1, 10)
    tasks = _string_list(value.get("next_tasks"), "next_tasks", 0, 3)
    if any(len(item) > 600 for item in reasons) or any(len(item) > 500 for item in tasks):
        raise GateError("decision text exceeds bounds")
    scope = value.get("approval_scope")
    if scope == "live_trade":
        raise GateError("live_trade is outside CALIOPE authority")
    if scope is not None and scope not in {"none", "merge", "publish", "paper_trade"}:
        raise GateError("invalid approval_scope")
    if value["decision"] == "human_review":
        if any(status != "pass" for status in statuses):
            raise GateError("human_review requires every deterministic gate to pass")
        if candidate_hash not in set(manifest.get("proposal_hashes", [])):
            raise GateError("human_review requires a known proposed candidate")


def run_path(run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{8,80}", run_id):
        raise GateError("invalid run_id")
    return RUNS / run_id


@contextmanager
def _run_lock(folder: Path):
    """Fail rather than race two manifest read/modify/write transactions."""
    lock = folder / ".caliope.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise GateError("run is locked by another CALIOPE transaction") from None
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def _validate_manifest(manifest: dict, run_id: str) -> None:
    required = {
        "schema_version", "run_id", "created_at", "stage", "iteration", "candidate_count",
        "baseline_source_path", "baseline_path", "baseline_hash",
        "prompt_source_path", "prompt_path", "prompt_hash", "current_candidate_hash",
        "limits", "budget_used", "evidence_hashes", "evidence_fingerprints", "proposal_hashes",
        "agents", "decision", "automatic_publish", "automatic_trading",
    }
    if not required.issubset(manifest):
        raise GateError(f"manifest missing keys: {sorted(required - set(manifest))}")
    if manifest.get("schema_version") != "1.0" or manifest.get("run_id") != run_id:
        raise GateError("manifest identity mismatch")
    if manifest.get("stage") not in STAGES:
        raise GateError("invalid manifest stage")
    if manifest.get("limits") != LIMITS:
        raise GateError("manifest limits were modified")
    iteration = manifest.get("iteration")
    if isinstance(iteration, bool) or not isinstance(iteration, int) or not 0 <= iteration < LIMITS["max_iterations"]:
        raise GateError("invalid manifest iteration")
    count = manifest.get("candidate_count")
    if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= LIMITS["max_candidates"]:
        raise GateError("invalid candidate_count")
    if manifest.get("automatic_publish") is not False or manifest.get("automatic_trading") is not False:
        raise GateError("operational automation is forbidden")
    if set(manifest.get("agents", {})) != set(ROLES):
        raise GateError("manifest agent roster mismatch")
    if any(state not in {"pending", "received"} for state in manifest["agents"].values()):
        raise GateError("invalid agent state")
    for name in ("baseline_hash", "prompt_hash", "current_candidate_hash"):
        _hex(manifest.get(name), f"manifest {name}")
    for name in ("evidence_hashes", "evidence_fingerprints", "proposal_hashes"):
        values = _string_list(manifest.get(name), name, 0, LIMITS["max_calls"], unique=True)
        for value in values:
            _hex(value, name)
    budget = manifest.get("budget_used")
    if not isinstance(budget, dict) or set(budget) != {"calls", "input_tokens", "output_tokens", "cost_usd"}:
        raise GateError("invalid manifest budget")
    for name in ("calls", "input_tokens", "output_tokens"):
        value = budget.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > LIMITS[f"max_{name}"]:
            raise GateError(f"invalid manifest budget: {name}")
    cost = budget.get("cost_usd")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or not 0 <= cost <= LIMITS["max_cost_usd"]:
        raise GateError("invalid manifest budget: cost_usd")
    baseline = Path(manifest.get("baseline_path", ""))
    prompts = Path(manifest.get("prompt_path", ""))
    input_root = (run_path(run_id) / "inputs").resolve()
    try:
        baseline = baseline.resolve(strict=True)
        prompts = prompts.resolve(strict=True)
        if not baseline.is_relative_to(input_root) or not prompts.is_relative_to(input_root):
            raise GateError("manifest inputs must point to immutable run snapshots")
        if sha256_file(baseline) != manifest["baseline_hash"] or sha256_file(prompts) != manifest["prompt_hash"]:
            raise GateError("baseline or prompt snapshot changed after run initialization")
    except OSError as exc:
        raise GateError(f"baseline or prompt snapshot is unavailable: {exc}") from None


def _load_manifest(folder: Path, run_id: str) -> dict:
    manifest = read_json(folder / "run_manifest.json")
    _validate_manifest(manifest, run_id)
    return manifest


def _write_requests(folder: Path, manifest: dict) -> None:
    iteration = manifest["iteration"]
    archive = folder / "requests" / f"iteration-{iteration}"
    archive.mkdir(parents=True, exist_ok=True)
    for agent, kind in ROLES.items():
        packet = {
            "run_id": manifest["run_id"],
            "iteration": iteration,
            "agent_id": agent,
            "response_contract": f"contracts/{kind}.schema.json",
            "baseline_path": manifest["baseline_path"],
            "baseline_hash": manifest["baseline_hash"],
            "parent_candidate_hash": manifest["current_candidate_hash"],
            "prompt_path": manifest["prompt_path"],
            "prompt_hash": manifest["prompt_hash"],
            "evidence_hashes": manifest["evidence_hashes"],
            "instructions": "Return only contract-valid JSON. You may review; you may not publish, approve or trade.",
        }
        atomic_json(archive / f"{agent}.json", packet)
        atomic_json(folder / "requests" / f"{agent}.json", packet)


def init_run(baseline: Path, prompt_file: Path, run_id: str | None = None) -> Path:
    baseline = baseline.resolve(strict=True)
    prompt_file = prompt_file.resolve(strict=True)
    baseline_bytes = baseline.read_bytes()
    prompt_bytes = prompt_file.read_bytes()
    baseline_hash = sha256_bytes(baseline_bytes)
    run_id = run_id or f"mf-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{baseline_hash[:8].lower()}"
    folder = run_path(run_id)
    if folder.exists():
        raise GateError(f"run already exists: {run_id}")
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "requests").mkdir()
    (folder / "responses" / "iteration-0").mkdir(parents=True)
    (folder / "evidence").mkdir(parents=True)
    inputs = folder / "inputs"
    inputs.mkdir()
    baseline_snapshot = inputs / f"baseline{baseline.suffix or '.bin'}"
    prompt_snapshot = inputs / f"prompts{prompt_file.suffix or '.txt'}"
    baseline_snapshot.write_bytes(baseline_bytes)
    prompt_snapshot.write_bytes(prompt_bytes)
    manifest = {
        "schema_version": "1.0",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "stage": "observe_parallel",
        "iteration": 0,
        "candidate_count": 0,
        "baseline_source_path": str(baseline),
        "baseline_path": str(baseline_snapshot.resolve()),
        "baseline_hash": baseline_hash,
        "current_candidate_hash": baseline_hash,
        "prompt_source_path": str(prompt_file),
        "prompt_path": str(prompt_snapshot.resolve()),
        "prompt_hash": sha256_bytes(prompt_bytes),
        "limits": dict(LIMITS),
        "budget_used": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        "evidence_hashes": [],
        "evidence_fingerprints": [],
        "proposal_hashes": [],
        "agents": {role: "pending" for role in ROLES},
        "decision": None,
        "automatic_publish": False,
        "automatic_trading": False,
    }
    atomic_json(folder / "run_manifest.json", manifest)
    _write_requests(folder, manifest)
    return folder


def _add_budget(manifest: dict, value: dict, kind: str) -> None:
    budget = value.get("budget_used", {}) if kind == "observation" else {}
    used = manifest["budget_used"]
    used["calls"] += 1
    used["input_tokens"] += int(budget.get("input_tokens", 0))
    used["output_tokens"] += int(budget.get("output_tokens", 0))
    used["cost_usd"] = round(float(used["cost_usd"]) + float(budget.get("cost_usd", 0)), 6)
    checks = {
        "calls": "max_calls",
        "input_tokens": "max_input_tokens",
        "output_tokens": "max_output_tokens",
        "cost_usd": "max_cost_usd",
    }
    for key, limit_key in checks.items():
        if used[key] > manifest["limits"][limit_key]:
            raise GateError(f"budget exceeded: {key}")


def ingest_review(run_id: str, agent: str, response_file: Path) -> Path:
    folder = run_path(run_id)
    with _run_lock(folder):
        manifest = _load_manifest(folder, run_id)
        if manifest["stage"] != "observe_parallel":
            raise GateError(f"run stage is {manifest['stage']}, not observe_parallel")
        if agent not in ROLES or manifest["agents"].get(agent) != "pending":
            raise GateError("unknown agent or response already ingested")
        kind = ROLES[agent]
        value = read_json(response_file)
        validate_response(value, kind, run_id, manifest["iteration"], agent)
        supplied_hashes = {item.upper() for item in value["input_hashes"]}
        known_hashes = {
            manifest["baseline_hash"], manifest["prompt_hash"], manifest["current_candidate_hash"],
            *manifest["evidence_hashes"], *manifest["proposal_hashes"],
        }
        if manifest["current_candidate_hash"] not in supplied_hashes or not supplied_hashes.issubset(known_hashes):
            raise GateError("review input hashes do not match run provenance")
        digest = sha256_bytes(canonical_bytes(value))
        fingerprint = _evidence_fingerprint(value, kind)
        if digest in manifest["evidence_hashes"] or fingerprint in manifest["evidence_fingerprints"]:
            raise GateError("repeated evidence; recursion stopped")
        snapshot = json.loads(json.dumps(manifest))
        _add_budget(snapshot, value, kind)
        snapshot["evidence_hashes"].append(digest)
        snapshot["evidence_fingerprints"].append(fingerprint)
        snapshot["agents"][agent] = "received"
        if all(state == "received" for state in snapshot["agents"].values()):
            snapshot["stage"] = "evidence_merge"
        out = folder / "responses" / f"iteration-{manifest['iteration']}" / f"{agent}-{digest[:12].lower()}.json"
        atomic_json(out, value)
        atomic_json(folder / "run_manifest.json", snapshot)
        return out


def ingest_decision(run_id: str, decision_file: Path) -> Path:
    folder = run_path(run_id)
    with _run_lock(folder):
        manifest = _load_manifest(folder, run_id)
        if manifest["stage"] not in {"evidence_merge", "decision"}:
            raise GateError(f"decision not allowed from stage {manifest['stage']}")
        value = read_json(decision_file)
        validate_decision(value, manifest)
        digest = sha256_bytes(canonical_bytes(value))
        archive = folder / "decisions" / f"iteration-{manifest['iteration']}-{digest[:12].lower()}.json"
        atomic_json(archive, value)
        out = folder / "decision.json"
        atomic_json(out, value)
        manifest["decision"] = value["decision"]
        manifest["decision_hash"] = digest
        candidate_hash = value["candidate_hash"].upper()
        if candidate_hash != manifest["current_candidate_hash"]:
            manifest["candidate_count"] += 1
            if manifest["candidate_count"] > manifest["limits"]["max_candidates"]:
                raise GateError("candidate limit exceeded")
            manifest["current_candidate_hash"] = candidate_hash
        if value["decision"] == "revise":
            if manifest["iteration"] + 1 >= manifest["limits"]["max_iterations"]:
                manifest["stage"] = "stopped_iteration_limit"
            else:
                manifest["iteration"] += 1
                manifest["stage"] = "observe_parallel"
                manifest["agents"] = {role: "pending" for role in ROLES}
                (folder / "responses" / f"iteration-{manifest['iteration']}").mkdir(parents=True, exist_ok=True)
                _write_requests(folder, manifest)
        elif value["decision"] == "human_review":
            manifest["stage"] = "human_review"
        else:
            manifest["stage"] = "archived_rejected"
        atomic_json(folder / "run_manifest.json", manifest)
        return out


def print_status(run_id: str) -> None:
    manifest = _load_manifest(run_path(run_id), run_id)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init")
    p_init.add_argument("--baseline", required=True, type=Path)
    p_init.add_argument("--prompts", required=True, type=Path)
    p_init.add_argument("--run-id")
    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("--run-id", required=True)
    p_ingest.add_argument("--agent", choices=sorted(ROLES), required=True)
    p_ingest.add_argument("--file", required=True, type=Path)
    p_decision = sub.add_parser("decision")
    p_decision.add_argument("--run-id", required=True)
    p_decision.add_argument("--file", required=True, type=Path)
    p_status = sub.add_parser("status")
    p_status.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(init_run(args.baseline, args.prompts, args.run_id))
        elif args.command == "ingest":
            print(ingest_review(args.run_id, args.agent, args.file))
        elif args.command == "decision":
            print(ingest_decision(args.run_id, args.file))
        else:
            print_status(args.run_id)
        return 0
    except (OSError, json.JSONDecodeError, GateError) as exc:
        print(f"CALIOPE BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
