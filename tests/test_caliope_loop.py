import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import caliope_loop as loop


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest().upper()


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.baseline = self.root / "baseline.pine"
        self.prompts = self.root / "prompts.md"
        self.baseline.write_text("//@version=6\n", encoding="utf-8")
        self.prompts.write_text("review", encoding="utf-8")
        self.runs = self.root / "runs"
        self.runs_patch = patch.object(loop, "RUNS", self.runs)
        self.runs_patch.start()
        self.folder = loop.init_run(self.baseline, self.prompts, "test-run-0001")

    def tearDown(self):
        self.runs_patch.stop()
        self.temp.cleanup()

    def manifest(self):
        return loop.read_json(self.folder / "run_manifest.json")

    def observation(self, agent="perplexity", iteration=None, *, input_tokens=10):
        manifest = self.manifest()
        return {
            "schema_version": "1.0", "run_id": "test-run-0001",
            "iteration": manifest["iteration"] if iteration is None else iteration,
            "agent_id": agent, "role": f"review-{agent}",
            "input_hashes": [manifest["current_candidate_hash"]],
            "findings": [], "blockers": [],
            "budget_used": {"input_tokens": input_tokens, "output_tokens": 20, "cost_usd": 0.0},
        }

    def ingest_observation(self, agent, **kwargs):
        path = self.root / f"{agent}-{self.manifest()['iteration']}.json"
        path.write_text(json.dumps(self.observation(agent, **kwargs)), encoding="utf-8")
        return loop.ingest_review("test-run-0001", agent, path)

    def complete_observations(self):
        for agent in loop.ROLES:
            if self.manifest()["agents"][agent] == "pending":
                self.ingest_observation(agent)
        self.assertEqual(self.manifest()["stage"], "evidence_merge")

    def decision(self, decision="revise", status="fail"):
        manifest = self.manifest()
        return {
            "schema_version": "1.0", "run_id": "test-run-0001",
            "iteration": manifest["iteration"], "baseline_hash": manifest["baseline_hash"],
            "candidate_hash": manifest["current_candidate_hash"],
            "gate_results": [{
                "gate_id": "parity.gate", "status": status, "observed": False,
                "threshold": True, "evidence_refs": ["evidence:test"],
            }],
            "decision": decision, "reasons": ["bounded test decision"],
            "next_tasks": ["collect new evidence"], "human_approval_required": True,
        }

    def write_decision(self, value):
        path = self.root / f"decision-{self.manifest()['iteration']}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_init_is_bounded_non_operational_and_snapshotted(self):
        manifest = self.manifest()
        self.assertEqual(manifest["limits"]["max_iterations"], 2)
        self.assertFalse(manifest["automatic_publish"])
        self.assertFalse(manifest["automatic_trading"])
        self.assertEqual(set(manifest["agents"]), set(loop.ROLES))
        baseline_snapshot = Path(manifest["baseline_path"])
        prompt_snapshot = Path(manifest["prompt_path"])
        self.assertTrue(baseline_snapshot.is_relative_to(self.folder / "inputs"))
        self.assertTrue(prompt_snapshot.is_relative_to(self.folder / "inputs"))
        self.assertEqual(baseline_snapshot.read_text(encoding="utf-8"), "//@version=6\n")
        request = loop.read_json(self.folder / "requests" / "perplexity.json")
        self.assertEqual(request["baseline_path"], str(baseline_snapshot))
        self.assertEqual(request["baseline_hash"], manifest["baseline_hash"])
        self.assertEqual(request["prompt_path"], str(prompt_snapshot))
        self.assertEqual(request["prompt_hash"], manifest["prompt_hash"])
        self.baseline.write_text("changed source", encoding="utf-8")
        self.prompts.write_text("changed source", encoding="utf-8")
        loop._load_manifest(self.folder, "test-run-0001")

    def test_tampered_snapshot_blocks_ingest(self):
        Path(self.manifest()["baseline_path"]).write_text("tampered", encoding="utf-8")
        path = self.root / "obs.json"
        path.write_text(json.dumps(self.observation()), encoding="utf-8")
        with self.assertRaises(loop.GateError):
            loop.ingest_review("test-run-0001", "perplexity", path)

    def test_ingest_updates_budget_and_rejects_duplicate_agent(self):
        self.ingest_observation("perplexity")
        self.assertEqual(self.manifest()["budget_used"]["calls"], 1)
        with self.assertRaises(loop.GateError):
            self.ingest_observation("perplexity")

    def test_parallel_reviewers_cannot_submit_patches(self):
        content = "--- a/tests/parity.py\n+++ b/tests/parity.py\n@@ -0,0 +1 @@\n+pass\n"
        value = {
            "schema_version": "1.0", "run_id": "test-run-0001", "iteration": 0,
            "agent_id": "qwen", "parent_candidate_hash": self.manifest()["baseline_hash"],
            "hypothesis": "A bounded parity test prevents false promotion.",
            "target_files": ["tests/parity.py"], "invariants": ["baseline immutable"],
            "validation_plan": ["run parity fixture"], "rollback": "delete candidate",
            "evidence_refs": ["baseline"],
            "patch": {"format": "unified_diff", "sha256": digest(content), "content": content},
        }
        path = self.root / "proposal.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(loop.GateError):
            loop.ingest_review("test-run-0001", "qwen", path)

    def test_budget_failure_is_transactional(self):
        with self.assertRaises(loop.GateError):
            self.ingest_observation("perplexity", input_tokens=loop.LIMITS["max_input_tokens"] + 1)
        manifest = self.manifest()
        self.assertEqual(manifest["budget_used"]["calls"], 0)
        self.assertEqual(manifest["agents"]["perplexity"], "pending")

    def test_manifest_operational_tampering_blocks(self):
        manifest = self.manifest()
        manifest["automatic_trading"] = True
        loop.atomic_json(self.folder / "run_manifest.json", manifest)
        path = self.root / "obs.json"
        path.write_text(json.dumps(self.observation()), encoding="utf-8")
        with self.assertRaises(loop.GateError):
            loop.ingest_review("test-run-0001", "perplexity", path)

    def test_revise_creates_next_iteration_packets_and_repeated_evidence_stops(self):
        self.complete_observations()
        loop.ingest_decision("test-run-0001", self.write_decision(self.decision("revise", "fail")))
        manifest = self.manifest()
        self.assertEqual((manifest["iteration"], manifest["stage"]), (1, "observe_parallel"))
        packet = loop.read_json(self.folder / "requests" / "iteration-1" / "perplexity.json")
        self.assertEqual(packet["iteration"], 1)
        self.assertEqual(packet["parent_candidate_hash"], manifest["current_candidate_hash"])
        with self.assertRaises(loop.GateError):
            self.ingest_observation("perplexity")

    def test_human_review_without_builder_candidate_is_blocked(self):
        self.complete_observations()
        value = self.decision("human_review", "pass")
        with self.assertRaises(loop.GateError):
            loop.ingest_decision("test-run-0001", self.write_decision(value))
        self.assertEqual(self.manifest()["stage"], "evidence_merge")

    def test_non_finite_json_is_rejected(self):
        value = self.observation()
        value["budget_used"]["cost_usd"] = float("nan")
        path = self.root / "nan.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(loop.GateError):
            loop.ingest_review("test-run-0001", "perplexity", path)

    def test_existing_lock_fails_closed(self):
        (self.folder / ".caliope.lock").write_text("other-process", encoding="utf-8")
        path = self.root / "obs.json"
        path.write_text(json.dumps(self.observation()), encoding="utf-8")
        with self.assertRaises(loop.GateError):
            loop.ingest_review("test-run-0001", "perplexity", path)


if __name__ == "__main__":
    unittest.main()
