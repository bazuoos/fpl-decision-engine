from __future__ import annotations

import copy
import os
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from fpl_decision_engine.operational_manifest import (
    FINAL_MANIFEST_VERSION,
    IDEMPOTENCY_POLICY_VERSION,
    MODELED_TRANSFER_COST_POINTS,
    PREPARATION_MANIFEST_VERSION,
    ChipState,
    EvidenceObservation,
    OperationalContractError,
    build_decision_id,
    build_final_operational_manifest,
    build_preparation_id,
    build_preparation_manifest,
    canonical_json_bytes,
    validate_final_operational_manifest,
    validate_preparation_manifest,
)


H = {
    "refresh": "1" * 64,
    "snapshot": "2" * 64,
    "fixtures": "3" * 64,
    "history": "4" * 64,
    "feature_fixture": "5" * 64,
    "feature_gameweek": "6" * 64,
    "prediction_fixture": "7" * 64,
    "prediction_gameweek": "8" * 64,
    "players": "9" * 64,
    "manager": "a" * 64,
    "manager_evidence": "b" * 64,
    "candidates": "c" * 64,
    "decision": "d" * 64,
    "reliability": "e" * 64,
    "schema": "f" * 64,
    "contract": "0" * 64,
}


class OperationalManifestContractTests(unittest.TestCase):
    def preparation(self, **overrides: object):
        arguments: dict[str, object] = {
            "target_gameweek": 2,
            "official_deadline": "2026-08-28T17:30:00Z",
            "refresh_manifest_sha256": H["refresh"],
            "frozen_snapshot_sha256": H["snapshot"],
            "frozen_snapshot_observed_at": "2026-08-27T17:30:00Z",
            "accepted_evidence": (
                EvidenceObservation(
                    "official_fpl_fixtures",
                    "2026-08-27T17:31:00Z",
                    H["fixtures"],
                ),
                EvidenceObservation(
                    "official_fpl_player_history",
                    "2026-08-27T17:32:00Z",
                    H["history"],
                ),
            ),
            "feature_artifacts": {
                "fixture_features": H["feature_fixture"],
                "gameweek_features": H["feature_gameweek"],
            },
            "prediction_artifacts": {
                "fixture_predictions": H["prediction_fixture"],
                "gameweek_predictions": H["prediction_gameweek"],
            },
            "frozen_player_artifact_sha256": H["players"],
            "producer_versions": {
                "feature_builder": "feature-builder-v1",
                "prediction_model": "v0.1",
            },
        }
        arguments.update(overrides)
        return build_preparation_manifest(**arguments)  # type: ignore[arg-type]

    def final(self, **overrides: object):
        arguments: dict[str, object] = {
            "preparation": self.preparation(),
            "manager_state_sha256": H["manager"],
            "manager_verification_timestamp": "2026-08-27T18:00:00Z",
            "manager_evidence_source": "official FPL Transfers screenshot transcription",
            "manager_evidence_source_sha256": H["manager_evidence"],
            "chip_state": ChipState.NO_CHIP,
            "candidate_artifact_sha256": H["candidates"],
            "one_transfer_decision_sha256": H["decision"],
            "reliability_artifact_sha256": H["reliability"],
            "gameweek_decision_schema_version": "1.0.0",
            "gameweek_decision_schema_sha256": H["schema"],
            "gameweek_decision_contract_sha256": H["contract"],
            "finalization_timestamp": "2026-08-27T18:05:00Z",
        }
        arguments.update(overrides)
        return build_final_operational_manifest(**arguments)  # type: ignore[arg-type]

    def test_identical_preparation_semantics_produce_same_id(self) -> None:
        first = build_preparation_id(
            target_gameweek=2,
            official_deadline="2026-08-28T17:30:00Z",
            refresh_manifest_sha256=H["refresh"],
        )
        second = build_preparation_id(
            refresh_manifest_sha256=H["refresh"],
            official_deadline="2026-08-28T17:30:00.000000Z",
            target_gameweek=2,
        )
        self.assertEqual(first, second)

    def test_each_material_preparation_identity_input_changes_id(self) -> None:
        base = build_preparation_id(
            target_gameweek=2,
            official_deadline="2026-08-28T17:30:00Z",
            refresh_manifest_sha256=H["refresh"],
        )
        variants = (
            {"target_gameweek": 3},
            {"official_deadline": "2026-08-28T17:31:00Z"},
            {"refresh_manifest_sha256": "a" * 64},
            {"contract_version": "operational-preparation-manifest-v2"},
        )
        defaults: dict[str, object] = {
            "target_gameweek": 2,
            "official_deadline": "2026-08-28T17:30:00Z",
            "refresh_manifest_sha256": H["refresh"],
        }
        for changed in variants:
            with self.subTest(changed=changed):
                self.assertNotEqual(
                    base, build_preparation_id(**(defaults | changed))  # type: ignore[arg-type]
                )

    def test_paths_environment_and_mapping_order_do_not_affect_identity_or_bytes(self) -> None:
        with patch.dict(
            os.environ,
            {"PWD": "/different/machine/path", "TZ": "Pacific/Honolulu"},
        ):
            identity = build_preparation_id(
                target_gameweek=2,
                official_deadline="2026-08-28T17:30:00Z",
                refresh_manifest_sha256=H["refresh"],
            )
        first = self.preparation()
        second = self.preparation(
            accepted_evidence=tuple(reversed(first.accepted_evidence)),
            feature_artifacts={
                "gameweek_features": H["feature_gameweek"],
                "fixture_features": H["feature_fixture"],
            },
            producer_versions={
                "prediction_model": "v0.1",
                "feature_builder": "feature-builder-v1",
            },
        )
        self.assertEqual(identity, first.preparation_id)
        self.assertEqual(first.preparation_id, second.preparation_id)
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())

    def test_decision_identity_is_stable_and_changes_only_with_semantic_inputs(self) -> None:
        preparation = self.preparation().preparation_id
        same = build_decision_id(
            preparation_id=preparation, manager_state_sha256=H["manager"]
        )
        self.assertEqual(
            same,
            build_decision_id(
                manager_state_sha256=H["manager"], preparation_id=preparation
            ),
        )
        self.assertNotEqual(
            same,
            build_decision_id(
                preparation_id=preparation, manager_state_sha256="f" * 64
            ),
        )
        different_preparation = build_preparation_id(
            target_gameweek=3,
            official_deadline="2026-09-05T17:30:00Z",
            refresh_manifest_sha256=H["refresh"],
        )
        self.assertNotEqual(
            same,
            build_decision_id(
                preparation_id=different_preparation,
                manager_state_sha256=H["manager"],
            ),
        )

    def test_identity_inputs_fail_closed(self) -> None:
        invalid_preparations = (
            {"target_gameweek": True},
            {"target_gameweek": 0},
            {"official_deadline": "2026-08-28T17:30:00"},
            {"official_deadline": "2026-08-28T19:30:00+02:00"},
            {"refresh_manifest_sha256": "A" * 64},
            {"refresh_manifest_sha256": "short"},
            {"contract_version": "unversioned"},
            {"contract_version": ""},
        )
        defaults: dict[str, object] = {
            "target_gameweek": 2,
            "official_deadline": "2026-08-28T17:30:00Z",
            "refresh_manifest_sha256": H["refresh"],
        }
        for changed in invalid_preparations:
            with self.subTest(changed=changed), self.assertRaises(
                OperationalContractError
            ):
                build_preparation_id(**(defaults | changed))  # type: ignore[arg-type]
        for invalid_id in ("", "prep_" + "A" * 64, "decision_" + "1" * 64):
            with self.subTest(invalid_id=invalid_id), self.assertRaises(
                OperationalContractError
            ):
                build_decision_id(
                    preparation_id=invalid_id,
                    manager_state_sha256=H["manager"],
                )

    def test_preparation_cutoff_is_latest_source_observation(self) -> None:
        manifest = self.preparation()
        self.assertEqual(manifest.evidence_cutoff, "2026-08-27T17:32:00.000000Z")
        payload = manifest.to_payload()
        self.assertEqual(
            [row["source"] for row in payload["accepted_evidence"]],
            ["official_fpl_fixtures", "official_fpl_player_history"],
        )
        validate_preparation_manifest(payload)

    def test_final_cutoff_adds_manager_evidence_but_excludes_processing_time(self) -> None:
        earlier = self.final(finalization_timestamp="2026-08-27T18:05:00Z")
        later = self.final(finalization_timestamp="2026-08-27T19:05:00Z")
        self.assertEqual(earlier.evidence_cutoff, "2026-08-27T18:00:00.000000Z")
        self.assertEqual(earlier.evidence_cutoff, later.evidence_cutoff)
        self.assertEqual(earlier.decision_id, later.decision_id)
        self.assertNotEqual(earlier.sha256, later.sha256)

    def test_evidence_must_be_strictly_before_deadline(self) -> None:
        for observed_at in (
            "2026-08-28T17:30:00Z",
            "2026-08-28T17:30:00.000001Z",
        ):
            with self.subTest(observed_at=observed_at), self.assertRaisesRegex(
                OperationalContractError, "strictly before"
            ):
                self.preparation(frozen_snapshot_observed_at=observed_at)

    def test_manager_evidence_must_be_strictly_before_deadline(self) -> None:
        for observed_at in (
            "2026-08-28T17:30:00Z",
            "2026-08-28T17:30:00.000001Z",
        ):
            with self.subTest(observed_at=observed_at), self.assertRaisesRegex(
                OperationalContractError, "strictly before"
            ):
                self.final(manager_verification_timestamp=observed_at)

    def test_finalization_must_be_strictly_before_deadline(self) -> None:
        for finalized_at in (
            "2026-08-28T17:30:00Z",
            "2026-08-28T17:30:00.000001Z",
        ):
            with self.subTest(finalized_at=finalized_at), self.assertRaisesRegex(
                OperationalContractError, "strictly before"
            ):
                self.final(finalization_timestamp=finalized_at)

    def test_non_utc_and_naive_evidence_and_finalization_fail(self) -> None:
        for timestamp in (
            "2026-08-27T18:00:00",
            "2026-08-27T20:00:00+02:00",
        ):
            with self.subTest(kind="manager", timestamp=timestamp), self.assertRaises(
                OperationalContractError
            ):
                self.final(manager_verification_timestamp=timestamp)
            with self.subTest(kind="finalization", timestamp=timestamp), self.assertRaises(
                OperationalContractError
            ):
                self.final(finalization_timestamp=timestamp)

    def test_non_utc_host_timezone_does_not_change_time_behavior(self) -> None:
        original_timezone = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Asia/Bangkok"
            if hasattr(time, "tzset"):
                time.tzset()
            manifest = self.final()
        finally:
            if original_timezone is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original_timezone
            if hasattr(time, "tzset"):
                time.tzset()
        self.assertEqual(
            manifest.manager_verification_timestamp,
            "2026-08-27T18:00:00.000000Z",
        )
        self.assertEqual(
            manifest.finalization_timestamp, "2026-08-27T18:05:00.000000Z"
        )

    def test_preparation_manifest_has_no_manager_or_decision_source_of_truth(self) -> None:
        payload = self.preparation().to_payload()
        prohibited = {
            "manager_state",
            "recommendation",
            "reliability",
            "human_action",
            "filesystem_path",
        }
        self.assertTrue(prohibited.isdisjoint(payload))

    def test_final_manifest_uses_unambiguous_artifact_hash_names(self) -> None:
        payload = self.final().to_payload()
        self.assertEqual(payload["one_transfer_decision_sha256"], H["decision"])
        self.assertEqual(
            payload["gameweek_decision_contract_sha256"], H["contract"]
        )
        self.assertEqual(payload["gameweek_decision_schema_sha256"], H["schema"])
        self.assertNotIn("recommendation", payload)
        self.assertNotIn("reliability_state", payload)

    def test_all_declared_chip_states_validate_without_implementing_behavior(self) -> None:
        for state in ChipState:
            with self.subTest(state=state):
                payload = self.final(chip_state=state).to_payload()
                self.assertEqual(
                    validate_final_operational_manifest(payload, self.preparation()).chip_state,
                    state,
                )

    def test_unknown_chip_state_fails_closed(self) -> None:
        payload = self.final().to_payload()
        payload["chip_state"] = "MYSTERY_CHIP"
        with self.assertRaisesRegex(OperationalContractError, "known value"):
            validate_final_operational_manifest(payload, self.preparation())

    def test_engine_v1_transfer_cost_is_derived_zero_only(self) -> None:
        self.assertEqual(self.final().modeled_transfer_cost_points, 0)
        self.assertEqual(MODELED_TRANSFER_COST_POINTS, 0)
        with self.assertRaisesRegex(OperationalContractError, "exactly zero"):
            self.final(modeled_transfer_cost_points=4)

    def test_strict_schema_rejects_malformed_hashes_and_additional_fields(self) -> None:
        preparation = self.preparation()
        malformed = preparation.to_payload()
        malformed["refresh_manifest_sha256"] = "bad"
        with self.assertRaises(OperationalContractError):
            validate_preparation_manifest(malformed)
        additional = preparation.to_payload()
        additional["manager_state_sha256"] = H["manager"]
        with self.assertRaisesRegex(OperationalContractError, "additional"):
            validate_preparation_manifest(additional)
        final_payload = self.final().to_payload()
        final_payload["roll"] = True
        with self.assertRaisesRegex(OperationalContractError, "additional"):
            validate_final_operational_manifest(final_payload, preparation)

    def test_final_manifest_rejects_malformed_hashes_and_boolean_gameweek(self) -> None:
        preparation = self.preparation()
        for field in (
            "candidate_artifact_sha256",
            "manager_state_sha256",
            "one_transfer_decision_sha256",
            "reliability_artifact_sha256",
            "gameweek_decision_schema_sha256",
            "gameweek_decision_contract_sha256",
        ):
            payload = self.final(preparation=preparation).to_payload()
            payload[field] = "BAD"
            with self.subTest(field=field), self.assertRaises(
                OperationalContractError
            ):
                validate_final_operational_manifest(payload, preparation)
        payload = self.final(preparation=preparation).to_payload()
        payload["target_gameweek"] = True
        with self.assertRaisesRegex(OperationalContractError, "non-boolean"):
            validate_final_operational_manifest(payload, preparation)

    def test_manifest_ids_and_cutoffs_cannot_be_spoofed(self) -> None:
        preparation = self.preparation()
        bad_id = preparation.to_payload()
        bad_id["preparation_id"] = "prep_" + "f" * 64
        with self.assertRaisesRegex(OperationalContractError, "does not match"):
            validate_preparation_manifest(bad_id)
        bad_cutoff = preparation.to_payload()
        bad_cutoff["evidence_cutoff"] = "2026-08-27T17:31:00Z"
        with self.assertRaisesRegex(OperationalContractError, "maximum"):
            validate_preparation_manifest(bad_cutoff)

    def test_final_manifest_must_reference_exact_preparation_id_and_hash(self) -> None:
        preparation = self.preparation()
        payload = self.final(preparation=preparation).to_payload()
        payload["preparation_id"] = "prep_" + "f" * 64
        with self.assertRaisesRegex(OperationalContractError, "preparation_id"):
            validate_final_operational_manifest(payload, preparation)
        payload = self.final(preparation=preparation).to_payload()
        payload["preparation_manifest_sha256"] = "f" * 64
        with self.assertRaisesRegex(OperationalContractError, "manifest hash"):
            validate_final_operational_manifest(payload, preparation)

    def test_final_builder_revalidates_supplied_preparation(self) -> None:
        forged = replace(self.preparation(), evidence_cutoff="2026-08-27T00:00:00Z")
        with self.assertRaisesRegex(OperationalContractError, "maximum"):
            self.final(preparation=forged)

    def test_changed_evidence_cannot_collide_or_overwrite_semantic_identity(self) -> None:
        first_preparation = self.preparation()
        changed_preparation = self.preparation(refresh_manifest_sha256="a" * 64)
        self.assertNotEqual(
            first_preparation.preparation_id, changed_preparation.preparation_id
        )
        first_decision = self.final(preparation=first_preparation)
        changed_manager = self.final(
            preparation=first_preparation, manager_state_sha256="f" * 64
        )
        self.assertNotEqual(first_decision.decision_id, changed_manager.decision_id)
        self.assertEqual(
            first_decision.idempotency_policy_version, IDEMPOTENCY_POLICY_VERSION
        )

    def test_canonical_serialization_is_stable_utf8_and_rejects_non_finite(self) -> None:
        first = {"z": "ก", "a": {"y": 2, "x": 1}}
        second = {"a": {"x": 1, "y": 2}, "z": "ก"}
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        self.assertIn("ก".encode("utf-8"), canonical_json_bytes(first))
        with self.assertRaisesRegex(OperationalContractError, "non-finite"):
            canonical_json_bytes({"value": float("nan")})

    def test_round_trip_validation_is_byte_identical(self) -> None:
        preparation = self.preparation()
        validated_preparation = validate_preparation_manifest(
            copy.deepcopy(preparation.to_payload())
        )
        self.assertEqual(
            preparation.canonical_bytes(), validated_preparation.canonical_bytes()
        )
        final = self.final(preparation=preparation)
        validated_final = validate_final_operational_manifest(
            copy.deepcopy(final.to_payload()), preparation
        )
        self.assertEqual(final.canonical_bytes(), validated_final.canonical_bytes())
        self.assertEqual(final.schema_version, FINAL_MANIFEST_VERSION)
        self.assertEqual(preparation.schema_version, PREPARATION_MANIFEST_VERSION)


if __name__ == "__main__":
    unittest.main()
