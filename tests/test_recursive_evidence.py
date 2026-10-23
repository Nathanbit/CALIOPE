import json
import tempfile
import unittest
from pathlib import Path

import recursive_evidence as evidence


NOW = "2026-10-19T15:40:00+00:00"


def policy(**updates):
    value = {
        "minimum_distinct_observations": 2,
        "minimum_validation_samples": 2,
        "minimum_complete_plans_total": 6,
        "minimum_complete_plans_per_partition": 2,
        "required_partitions": ["oos", "holdout"],
        "automatic_publish": False,
        "automatic_trading": False,
    }
    value.update(updates)
    return value


def plan(tp, sl, *, updated=False):
    value = {"take_profit": tp, "stop_loss": sl, "recorded_at": NOW}
    if updated:
        value["reason"] = "portfolio risk review"
    return value


def metrics(pnl):
    return {
        "unrealized_pnl": None,
        "realized_pnl": pnl,
        "mfe": 1.5,
        "mae": 0.7,
        "excursion_unit": "R",
        "slippage": 0.2,
        "slippage_unit": "currency",
        "gap_sensitivity": [
            {"gap": 1.0, "gap_unit": "ATR", "estimated_pnl_impact": -2.5, "currency": "USD"}
        ],
    }


def snapshot(asset, timestamp=NOW):
    return {
        "asset": asset,
        "feed": asset,
        "timeframe": "5m",
        "observed_at": timestamp,
        "source": "TradingView chart",
        "model": "MacroFlow v0.9.4",
        "values": {"atr": 4.0, "pattern_present": False, "controls": "OK"},
        "decision_context": {
            "new_entry_signal": "none",
            "manage_existing": "observe_only",
            "reported_action": "SIN OPERACIÓN",
        },
    }


def observation(sample_id, *, closed=True, performed=True):
    positions = [
        {
            "position_id": f"{sample_id}-es",
            "asset": "ES1!",
            "asset_class": "index_future",
            "side": "long",
            "quantity": 1.0,
            "entry_price": 6000.0,
            "original_plan": plan(6040.0, 5990.0),
            "updated_plan": plan(6030.0, 6002.0, updated=True),
            "execution_metrics": metrics(12.0 if closed else None),
        },
        {
            "position_id": f"{sample_id}-nq",
            "asset": "NQ1!",
            "asset_class": "index_future",
            "side": "long",
            "quantity": 1.0,
            "entry_price": 21000.0,
            "original_plan": plan(21200.0, 20950.0),
            "updated_plan": None,
            "execution_metrics": metrics(8.0 if closed else None),
        },
    ]
    shares = {position["position_id"]: 0.5 for position in positions}
    return {
        "sample_id": sample_id,
        "observed_at": NOW,
        "source": "sanitized broker export plus indicator snapshots",
        "account": {"broker": "SimpleFX", "mode": "live", "base_currency": "USD", "equity": None},
        "positions": positions,
        "indicator_snapshots": [snapshot("ES1!"), snapshot("NQ1!")],
        "portfolio_validation": {
            "performed": performed,
            "method": "basket review",
            "correlation_groups": [{
                "position_ids": list(shares),
                "relationship": "shared US equity-index factor",
                "coefficient": None,
                "lookback": "not supplied",
                "source": "position basket review",
            }] if performed else [],
            "concentration": {
                "basis": "risk",
                "largest_position_share": 0.5 if performed else None,
                "shares": shares if performed else {},
            },
            "aggregate_risk": 4.0 if performed else None,
            "assessment": "positions treated as one correlated basket" if performed else "not measured",
        },
        "human_decision": {
            "decision_scope": "manage_existing",
            "action": "hold",
            "rationale": "manage existing basket separately from new entries",
            "decided_by": "human",
            "decided_at": NOW,
            "executed": True,
        },
        "final_result": {
            "status": "closed" if closed else "open",
            "closed_at": NOW if closed else None,
            "net_pnl": 20.0 if closed else None,
            "currency": "USD",
            "notes": "final basket result" if closed else "unresolved",
        },
    }


def hypothesis(observation_hashes):
    return {
        "hypothesis_id": "hyp-portfolio-001",
        "claim": "Portfolio-aware exits may reduce correlated basket drawdown.",
        "observation_hashes": observation_hashes,
        "falsification": ["No OOS drawdown improvement after costs"],
        "expected_effect": "Lower basket drawdown without degrading net expectancy.",
    }


def candidate(hypothesis_hash, artifact_hash="A" * 64):
    return {
        "candidate_id": "candidate-portfolio-001",
        "hypothesis_hash": hypothesis_hash,
        "artifact_hash": artifact_hash,
        "change_summary": "Use basket concentration and gap sensitivity as validation inputs.",
        "invariants": ["no automatic trading", "no automatic publication"],
    }


def validation(candidate_hash, partition, suffix, plans=3, artifact_hash="A" * 64, passed=True):
    return {
        "validation_id": f"validation-{partition}-{suffix}",
        "candidate_hash": candidate_hash,
        "artifact_hash": artifact_hash,
        "partition": partition,
        "sample_id": f"sample-{partition}-{suffix}",
        "dataset_hash": ("B" if partition == "oos" else "C") * 63 + suffix,
        "complete_plans": plans,
        "metrics": {"net_expectancy_r": 0.1, "max_drawdown": 2.0},
        "passed": passed,
        "sealed_before_evaluation": True,
        "used_for_design": False,
        "evidence_refs": [f"tester:{partition}:{suffix}"],
    }


def promotion(candidate_hash, validation_hashes):
    return {
        "candidate_hash": candidate_hash,
        "validation_hashes": validation_hashes,
        "human_decision": {
            "approved": True,
            "decided_by": "human-reviewer",
            "decided_at": NOW,
            "rationale": "All preregistered research gates passed.",
        },
        "automatic_publish": False,
        "automatic_trading": False,
        "final_result": "Approved for research registry only.",
    }


class RecursiveEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "ledger"
        evidence.init_ledger(self.root, "portfolio-case-001", policy())

    def tearDown(self):
        self.temp.cleanup()

    def add_two_observations_and_candidate(self):
        first = evidence.append_event(self.root, "observation", observation("obs-001"), recorded_at=NOW)
        second = evidence.append_event(self.root, "observation", observation("obs-002"), recorded_at=NOW)
        hypothesis_hash = evidence.append_event(
            self.root, "hypothesis", hypothesis([first, second]), recorded_at=NOW
        )
        candidate_hash = evidence.append_event(
            self.root, "candidate", candidate(hypothesis_hash), recorded_at=NOW
        )
        return candidate_hash

    def test_observation_records_portfolio_plans_metrics_and_context(self):
        digest = evidence.append_event(self.root, "observation", observation("obs-001"), recorded_at=NOW)
        status = evidence.status(self.root)
        self.assertEqual(status["counts"]["observation"], 1)
        self.assertEqual(len(digest), 64)
        self.assertFalse(status["automatic_publish"])
        self.assertFalse(status["automatic_trading"])

    def test_every_position_requires_indicator_snapshot(self):
        value = observation("obs-001")
        value["indicator_snapshots"] = [snapshot("ES1!")]
        with self.assertRaises(evidence.EvidenceGateError):
            evidence.append_event(self.root, "observation", value, recorded_at=NOW)

    def test_inconsistent_concentration_fails_closed(self):
        value = observation("obs-001")
        position_ids = list(value["portfolio_validation"]["concentration"]["shares"])
        value["portfolio_validation"]["concentration"]["shares"] = {
            position_ids[0]: 0.8,
            position_ids[1]: 0.8,
        }
        value["portfolio_validation"]["concentration"]["largest_position_share"] = 0.8
        with self.assertRaises(evidence.EvidenceGateError):
            evidence.append_event(self.root, "observation", value, recorded_at=NOW)

    def test_single_or_open_observation_cannot_drive_hypothesis(self):
        only = evidence.append_event(self.root, "observation", observation("obs-001"), recorded_at=NOW)
        with self.assertRaises(evidence.EvidenceGateError):
            evidence.append_event(self.root, "hypothesis", hypothesis([only, "D" * 64]), recorded_at=NOW)

        other_root = Path(self.temp.name) / "open-ledger"
        evidence.init_ledger(other_root, "portfolio-case-002", policy())
        hashes = [
            evidence.append_event(other_root, "observation", observation("obs-003", closed=False), recorded_at=NOW),
            evidence.append_event(other_root, "observation", observation("obs-004"), recorded_at=NOW),
        ]
        with self.assertRaises(evidence.EvidenceGateError):
            evidence.append_event(other_root, "hypothesis", hypothesis(hashes), recorded_at=NOW)

    def test_closed_observation_without_complete_excursions_cannot_train(self):
        values = [observation("obs-001"), observation("obs-002")]
        values[0]["positions"][0]["execution_metrics"]["mfe"] = None
        values[1]["positions"][1]["execution_metrics"]["mae"] = None
        hashes = [
            evidence.append_event(self.root, "observation", value, recorded_at=NOW)
            for value in values
        ]
        with self.assertRaises(evidence.EvidenceGateError):
            evidence.append_event(self.root, "hypothesis", hypothesis(hashes), recorded_at=NOW)

    def test_promotion_requires_oos_holdout_and_minimum_sample(self):
        candidate_hash = self.add_two_observations_and_candidate()
        oos_hash = evidence.append_event(
            self.root, "validation", validation(candidate_hash, "oos", "1"), recorded_at=NOW
        )
        with self.assertRaises(evidence.EvidenceGateError):
            evidence.append_event(
                self.root, "promotion", promotion(candidate_hash, [oos_hash, oos_hash]), recorded_at=NOW
            )
        holdout_hash = evidence.append_event(
            self.root, "validation", validation(candidate_hash, "holdout", "2"), recorded_at=NOW
        )
        promotion_hash = evidence.append_event(
            self.root, "promotion", promotion(candidate_hash, [oos_hash, holdout_hash]), recorded_at=NOW
        )
        self.assertEqual(evidence.status(self.root)["stage"], "promotion")
        self.assertEqual(len(promotion_hash), 64)

    def test_duplicate_dataset_and_artifact_mismatch_fail_closed(self):
        candidate_hash = self.add_two_observations_and_candidate()
        evidence.append_event(
            self.root, "validation", validation(candidate_hash, "oos", "1"), recorded_at=NOW
        )
        duplicate = validation(candidate_hash, "holdout", "2")
        duplicate["dataset_hash"] = "B" * 63 + "1"
        with self.assertRaises(evidence.EvidenceGateError):
            evidence.append_event(self.root, "validation", duplicate, recorded_at=NOW)
        mismatch = validation(candidate_hash, "holdout", "2", artifact_hash="F" * 64)
        with self.assertRaises(evidence.EvidenceGateError):
            evidence.append_event(self.root, "validation", mismatch, recorded_at=NOW)

    def test_preregistered_minimum_plan_count_blocks_promotion(self):
        candidate_hash = self.add_two_observations_and_candidate()
        oos_hash = evidence.append_event(
            self.root, "validation", validation(candidate_hash, "oos", "1", plans=2), recorded_at=NOW
        )
        holdout_hash = evidence.append_event(
            self.root, "validation", validation(candidate_hash, "holdout", "2", plans=2), recorded_at=NOW
        )
        with self.assertRaises(evidence.EvidenceGateError):
            evidence.append_event(
                self.root,
                "promotion",
                promotion(candidate_hash, [oos_hash, holdout_hash]),
                recorded_at=NOW,
            )

    def test_failed_validation_cannot_be_omitted_from_promotion(self):
        candidate_hash = self.add_two_observations_and_candidate()
        failed_hash = evidence.append_event(
            self.root,
            "validation",
            validation(candidate_hash, "oos", "1", passed=False),
            recorded_at=NOW,
        )
        passing_oos_hash = evidence.append_event(
            self.root,
            "validation",
            validation(candidate_hash, "oos", "2"),
            recorded_at=NOW,
        )
        passing_holdout_hash = evidence.append_event(
            self.root,
            "validation",
            validation(candidate_hash, "holdout", "3"),
            recorded_at=NOW,
        )
        with self.assertRaises(evidence.EvidenceGateError):
            evidence.append_event(
                self.root,
                "promotion",
                promotion(candidate_hash, [passing_oos_hash, passing_holdout_hash]),
                recorded_at=NOW,
            )
        with self.assertRaises(evidence.EvidenceGateError):
            evidence.append_event(
                self.root,
                "promotion",
                promotion(
                    candidate_hash,
                    [failed_hash, passing_oos_hash, passing_holdout_hash],
                ),
                recorded_at=NOW,
            )

    def test_policy_cannot_enable_operational_automation_or_one_sample_learning(self):
        with self.assertRaises(evidence.EvidenceGateError):
            evidence.validate_policy(policy(minimum_distinct_observations=1))
        with self.assertRaises(evidence.EvidenceGateError):
            evidence.validate_policy(policy(automatic_trading=True))

    def test_sanitized_live_snapshot_fixture_is_valid_and_non_promotional(self):
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "projects" / "macroflow" / "evidence" / "LIVE_INDICATOR_SNAPSHOTS_2026-10-19.json"
        )
        batch = json.loads(fixture_path.read_text(encoding="utf-8"))
        evidence.validate_indicator_batch(batch)
        self.assertEqual([item["feed"] for item in batch["snapshots"]], ["NQ1!", "ES1!", "RTY1!"])
        self.assertTrue(all(item["timeframe"] == "5m" for item in batch["snapshots"]))
        self.assertTrue(all(item["decision_context"]["new_entry_signal"] == "none" for item in batch["snapshots"]))
        self.assertTrue(all(item["decision_context"]["manage_existing"] == "not_evaluated" for item in batch["snapshots"]))
        self.assertEqual(batch["portfolio_context"]["outcome"], "open_unresolved")
        self.assertFalse(batch["portfolio_context"]["promotion_eligible"])
        self.assertAlmostEqual(batch["portfolio_context"]["basket_metrics"]["largest_position_share"], 0.83)
        self.assertAlmostEqual(
            batch["portfolio_context"]["basket_metrics"]["gap_sensitivity"][0]["adverse_slippage"],
            10.2,
        )

    def test_event_tampering_is_detected(self):
        evidence.append_event(self.root, "observation", observation("obs-001"), recorded_at=NOW)
        manifest = json.loads((self.root / "ledger.json").read_text(encoding="utf-8"))
        event_path = self.root / manifest["events"][0]["path"]
        event = json.loads(event_path.read_text(encoding="utf-8"))
        event["payload"]["final_result"]["net_pnl"] = 999999
        event_path.write_text(json.dumps(event), encoding="utf-8")
        with self.assertRaises(evidence.EvidenceGateError):
            evidence.status(self.root)


if __name__ == "__main__":
    unittest.main()
