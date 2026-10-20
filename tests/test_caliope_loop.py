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

    def generation(self, **overrides):
        manifest = self.manifest()
        value = {
            "provider": "deepseek", "model": "deepseek-chat", "model_version": None,
            "capture_method": "manual_copy", "structured_output": "none",
            "contract_hash": manifest["observation_contract_hash"],
            "prompt_hash": manifest["prompt_hash"],
            "temperature": None, "top_p": None, "seed": None, "max_output_tokens": None,
            "system_fingerprint": None, "determinism": "none",
            "captured_at": "2026-10-20T17:30:00+00:00",
        }
        value.update(overrides)
        return value

    def observation(self, agent="perplexity", iteration=None, *, input_tokens=10, **generation):
        manifest = self.manifest()
        return {
            "schema_version": "1.1", "run_id": "test-run-0001",
            "iteration": manifest["iteration"] if iteration is None else iteration,
            "agent_id": agent, "role": f"review-{agent}",
            "input_hashes": [manifest["current_candidate_hash"]],
            "findings": [], "blockers": [],
            "budget_used": {"input_tokens": input_tokens, "output_tokens": 20, "cost_usd": 0.0},
            "generation": self.generation(**generation),
        }

    def ingest_observation(self, agent, **kwargs):
        return self.ingest_value(agent, self.observation(agent, **kwargs))

    def ingest_value(self, agent, value):
        path = self.root / f"{agent}-{self.manifest()['iteration']}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return loop.ingest_review("test-run-0001", agent, path)

    def set_policy(self, **policy):
        """Run policy is pinned to the code constant, so tests patch both."""
        values = {**loop.OBSERVATION_POLICY, **policy}
        policy_patch = patch.dict(loop.OBSERVATION_POLICY, values)
        policy_patch.start()
        self.addCleanup(policy_patch.stop)
        manifest = self.manifest()
        manifest["observation_policy"] = dict(loop.OBSERVATION_POLICY)
        loop.atomic_json(self.folder / "run_manifest.json", manifest)

    def make_legacy_run(self):
        """Turn the fixture into a run initialised under contract 1.0."""
        manifest = self.manifest()
        for key in (
            "observation_contract", "observation_contract_path", "observation_contract_hash",
            "observation_policy", "generation_requirements", "capture_methods",
        ):
            manifest.pop(key)
        loop.atomic_json(self.folder / "run_manifest.json", manifest)

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

    def test_contract_1_1_observation_is_ingested_and_capture_recorded(self):
        self.ingest_observation("deepseek", capture_method="api_adapter", structured_output="json_mode")
        manifest = self.manifest()
        self.assertEqual(manifest["agents"]["deepseek"], "received")
        self.assertEqual(manifest["capture_methods"], {"deepseek": "api_adapter"})
        self.assertEqual(manifest["observation_contract"], "1.1")

    def test_request_packet_carries_contract_and_generation_requirements(self):
        manifest = self.manifest()
        snapshot = Path(manifest["observation_contract_path"])
        self.assertTrue(snapshot.is_relative_to(self.folder / "inputs"))
        self.assertEqual(loop.sha256_file(snapshot), manifest["observation_contract_hash"])
        self.assertEqual(loop.sha256_file(loop.CONTRACTS / "observation.schema.json"), manifest["observation_contract_hash"])
        packet = loop.read_json(self.folder / "requests" / "perplexity.json")
        requirements = packet["generation_requirements"]
        self.assertEqual(packet["response_contract_hash"], manifest["observation_contract_hash"])
        self.assertEqual(requirements["contract_hash"], manifest["observation_contract_hash"])
        self.assertEqual(requirements["response_schema"], loop.read_json(snapshot))
        self.assertEqual(requirements["temperature"], 0)
        self.assertEqual(requirements["seed"], loop._run_seed("test-run-0001"))
        self.assertEqual(requirements["max_output_tokens"], loop.LIMITS["max_output_tokens"] // len(loop.ROLES))
        self.assertEqual(requirements["policy"], loop.OBSERVATION_POLICY)

    def test_observation_without_generation_is_rejected(self):
        value = self.observation()
        del value["generation"]
        with self.assertRaises(loop.GateError):
            self.ingest_value("perplexity", value)
        self.assertEqual(self.manifest()["agents"]["perplexity"], "pending")

    def test_contract_hash_mismatch_is_rejected(self):
        with self.assertRaisesRegex(loop.GateError, "contract mismatch"):
            self.ingest_observation("perplexity", contract_hash="A" * 64)

    def test_prompt_hash_mismatch_is_rejected(self):
        with self.assertRaisesRegex(loop.GateError, "prompt mismatch"):
            self.ingest_observation("perplexity", prompt_hash="B" * 64)

    def test_reproducibility_recorded_accepts_null_seed_but_required_rejects_it(self):
        self.ingest_observation("perplexity", seed=None, temperature=None)
        self.set_policy(reproducibility="required")
        with self.assertRaisesRegex(loop.GateError, "reproducibility required"):
            self.ingest_observation("deepseek", seed=None, temperature=None)
        required = self.manifest()["generation_requirements"]
        self.ingest_observation("deepseek", seed=required["seed"], temperature=required["temperature"])

    def test_structured_output_policy_rejects_free_text(self):
        self.set_policy(require_structured_output=True)
        with self.assertRaisesRegex(loop.GateError, "structured output"):
            self.ingest_observation("perplexity", structured_output="none")
        self.ingest_observation("perplexity", structured_output="json_schema")

    def test_credential_in_generation_is_rejected(self):
        for leaked in ("sk-abcdefghijklmnop", "AIzaSyBc6c-nw_1eb7453", "Bearer abcdefghijkl", "ghp_abcdefghijklmn"):
            with self.subTest(leaked=leaked), self.assertRaisesRegex(loop.GateError, "credential"):
                self.ingest_observation("perplexity", model=leaked)
        with self.assertRaisesRegex(loop.GateError, "credential"):
            self.ingest_observation("perplexity", system_fingerprint="https://user:secret@host.example/v1")

    def test_batch_invariant_claim_requires_local_engine(self):
        with self.assertRaisesRegex(loop.GateError, "batch_invariant"):
            self.ingest_observation("perplexity", determinism="batch_invariant", capture_method="api_adapter")
        self.ingest_observation("perplexity", determinism="batch_invariant", capture_method="local_engine")

    def test_naive_captured_at_is_rejected(self):
        with self.assertRaisesRegex(loop.GateError, "captured_at"):
            self.ingest_observation("perplexity", captured_at="2026-10-20T17:30:00")

    def test_tampered_contract_snapshot_blocks_ingest(self):
        Path(self.manifest()["observation_contract_path"]).write_text("{}", encoding="utf-8")
        with self.assertRaises(loop.GateError):
            self.ingest_observation("perplexity")

    def test_modified_policy_in_manifest_blocks(self):
        manifest = self.manifest()
        manifest["observation_policy"]["reproducibility"] = "off"
        loop.atomic_json(self.folder / "run_manifest.json", manifest)
        with self.assertRaisesRegex(loop.GateError, "policy"):
            self.ingest_observation("perplexity")

    def test_legacy_1_0_run_keeps_accepting_1_0_observations(self):
        upgraded = self.observation("deepseek")
        value = self.observation()
        value["schema_version"] = "1.0"
        del value["generation"]
        self.make_legacy_run()
        self.ingest_value("perplexity", value)
        self.assertEqual(self.manifest()["agents"]["perplexity"], "received")
        with self.assertRaises(loop.GateError):
            self.ingest_value("deepseek", upgraded)

    def test_1_0_observation_is_rejected_in_a_1_1_run(self):
        value = self.observation()
        value["schema_version"] = "1.0"
        del value["generation"]
        with self.assertRaises(loop.GateError):
            self.ingest_value("perplexity", value)

    def test_orchestrator_is_not_an_observation_agent(self):
        for name in ("observation.schema.json", "observation-1.0.schema.json"):
            schema = loop.read_json(loop.CONTRACTS / name)
            agents = set(schema["properties"]["agent_id"]["enum"])
            if name == "observation.schema.json":
                self.assertNotIn("claude_themis", agents)
                self.assertEqual(agents, set(loop.ROLES))
            else:
                self.assertIn("claude_themis", agents, "1.0 contract stays frozen as published")

    def test_revise_resets_capture_methods_for_next_iteration(self):
        self.complete_observations()
        self.assertEqual(len(self.manifest()["capture_methods"]), len(loop.ROLES))
        loop.ingest_decision("test-run-0001", self.write_decision(self.decision("revise", "fail")))
        self.assertEqual(self.manifest()["capture_methods"], {})
        packet = loop.read_json(self.folder / "requests" / "iteration-1" / "perplexity.json")
        self.assertIn("generation_requirements", packet)

    def test_existing_lock_fails_closed(self):
        (self.folder / ".caliope.lock").write_text("other-process", encoding="utf-8")
        path = self.root / "obs.json"
        path.write_text(json.dumps(self.observation()), encoding="utf-8")
        with self.assertRaises(loop.GateError):
            loop.ingest_review("test-run-0001", "perplexity", path)


if __name__ == "__main__":
    unittest.main()
