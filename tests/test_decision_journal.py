from __future__ import annotations

import hashlib
import inspect
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from fixture_support import materialized_frozen_gw2, sha256_file
from test_operational_runner import BEFORE, DEADLINE, OperationalFixture, SequenceClock

from fpl_decision_engine.decision_journal import (
    JOURNAL_SCHEMA_VERSION,
    OUTCOME_SCHEMA_VERSION,
    DecisionJournalConflictError,
    DecisionJournalError,
    HumanActionKind,
    JournalClassification,
    record_decision_journal_entry,
    record_decision_outcome,
    validate_decision_journal_entry,
    validate_decision_outcome,
)
from fpl_decision_engine.operational_runner import resume_gameweek
from fpl_decision_engine.operational_manifest import canonical_json_bytes


AFTER = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
LATER_AFTER = datetime(2026, 8, 30, 13, 0, tzinfo=timezone.utc)


class DecisionJournalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.stack = ExitStack()
        cls.temporary = Path(cls.stack.enter_context(tempfile.TemporaryDirectory()))
        cls.frozen = cls.stack.enter_context(materialized_frozen_gw2())
        cls.fixture = OperationalFixture(cls.temporary, cls.frozen)
        cls.preparation = cls.fixture.prepare()
        cls.manager_evidence = cls.fixture.manager_evidence()
        cls.completed = resume_gameweek(
            preparation_manifest_path=cls.preparation.preparation_manifest_path,
            manager_evidence_path=cls.manager_evidence,
            clock=SequenceClock(BEFORE, BEFORE, BEFORE, BEFORE, BEFORE),
        )
        cls.gameweek_payload = json.loads(cls.completed.gameweek_decision_path.read_bytes())
        cls.engine_action = cls.gameweek_payload["recommended_action"]
        cls.completion_bootstrap = cls.temporary / "completed-bootstrap.json"
        bootstrap = json.loads(
            (
                cls.preparation.preparation_manifest_path.parent
                / "artifacts"
                / "bootstrap-static.json"
            ).read_bytes()
        )
        bootstrap["events"][0]["finished"] = True
        bootstrap["events"][0]["data_checked"] = True
        cls.completion_bootstrap.write_text(
            json.dumps(bootstrap, sort_keys=True), encoding="utf-8"
        )
        cls.historical_evidence = cls.temporary / "preserved-human-action.txt"
        cls.historical_evidence.write_text(
            "Preserved pre-deadline human action evidence", encoding="utf-8"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stack.close()

    def _prospective(self, **overrides):
        arguments = {
            "final_manifest_path": self.completed.final_manifest_path,
            "human_action": HumanActionKind.FOLLOW_ENGINE,
            "clock": lambda: BEFORE,
        }
        arguments.update(overrides)
        return record_decision_journal_entry(**arguments)

    def _historical(self, **overrides):
        arguments = {
            "final_manifest_path": self.completed.final_manifest_path,
            "human_action": HumanActionKind.FOLLOW_ENGINE,
            "classification": JournalClassification.HISTORICAL_BACKFILL,
            "historical_evidence_path": self.historical_evidence,
            "clock": lambda: AFTER,
        }
        arguments.update(overrides)
        return record_decision_journal_entry(**arguments)

    def _write_self_consistent_forged_journal(
        self,
        *,
        final_sha256: str,
        gameweek_sha256: str,
        reliability_sha256: str,
        engine_action: dict[str, object] | None = None,
    ) -> Path:
        final_payload = json.loads(self.completed.final_manifest_path.read_bytes())
        action = engine_action or {
            "action_type": "ROLL",
            "incoming_element_id": None,
            "outgoing_element_id": None,
        }
        recorded_at = "2026-08-27T12:00:00.000000Z"
        payload_without_id = {
            "classification": "PROSPECTIVE",
            "decision_id": final_payload["decision_id"],
            "engine_action": action,
            "evidence_cutoff": recorded_at,
            "final_operational_manifest_sha256": final_sha256,
            "gameweek_decision_sha256": gameweek_sha256,
            "historical_evidence_sha256": None,
            "human_action": action,
            "human_action_declaration": "FOLLOW_ENGINE",
            "human_action_matches_engine": True,
            "human_action_recorded_at": recorded_at,
            "journal_created_at": recorded_at,
            "manager_entry_id": self.gameweek_payload["manager_state"]["entry_id"],
            "official_deadline": DEADLINE.isoformat(timespec="microseconds").replace(
                "+00:00", "Z"
            ),
            "override_reason": None,
            "override_status": "NO_OVERRIDE",
            "preparation_id": final_payload["preparation_id"],
            "reliability_artifact_sha256": reliability_sha256,
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "season": self.gameweek_payload["season"],
            "target_gameweek": final_payload["target_gameweek"],
        }
        journal_id = "journal_" + hashlib.sha256(
            canonical_json_bytes(payload_without_id)
        ).hexdigest()
        payload = {"journal_entry_id": journal_id, **payload_without_id}
        self.assertEqual(validate_decision_journal_entry(payload), payload)
        path = (
            self.completed.final_manifest_path.parent
            / "journal"
            / journal_id
            / "decision_journal_entry.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(payload))
        return path

    def test_valid_prospective_entry_captures_system_clock_before_deadline(self) -> None:
        artifact = self._prospective()
        payload = json.loads(artifact.entry_path.read_bytes())
        self.assertEqual(payload["schema_version"], JOURNAL_SCHEMA_VERSION)
        self.assertEqual(payload["classification"], "PROSPECTIVE")
        self.assertEqual(payload["human_action_recorded_at"], "2026-08-27T12:00:00.000000Z")
        self.assertEqual(payload["journal_created_at"], payload["human_action_recorded_at"])
        self.assertEqual(payload["evidence_cutoff"], payload["human_action_recorded_at"])

    def test_user_cannot_supply_authoritative_action_timestamp(self) -> None:
        parameters = inspect.signature(record_decision_journal_entry).parameters
        self.assertNotIn("human_action_recorded_at", parameters)
        self.assertNotIn("journal_created_at", parameters)
        self.assertNotIn("evidence_cutoff", parameters)

    def test_exact_deadline_and_after_deadline_prospective_entries_fail(self) -> None:
        for value in (DEADLINE, AFTER):
            with self.subTest(value=value):
                with self.assertRaises(DecisionJournalError):
                    self._prospective(clock=lambda value=value: value)

    def test_corrupt_final_operational_manifest_fails_closed(self) -> None:
        path = self.completed.final_manifest_path
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"tamper")
            with self.assertRaises(DecisionJournalError):
                self._prospective()
        finally:
            path.write_bytes(original)

    def test_corrupt_gameweek_decision_reference_fails_closed(self) -> None:
        path = self.completed.gameweek_decision_path
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"tamper")
            with self.assertRaises(DecisionJournalError):
                self._prospective()
        finally:
            path.write_bytes(original)

    def test_corrupt_reliability_reference_fails_closed(self) -> None:
        path = next(
            self.completed.final_manifest_path.parent.rglob("decision_reliability.json")
        )
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"tamper")
            with self.assertRaises(DecisionJournalError):
                self._prospective()
        finally:
            path.write_bytes(original)

    def test_engine_action_is_extracted_from_validated_gameweek_decision(self) -> None:
        payload = json.loads(self._prospective().entry_path.read_bytes())
        expected = {
            "action_type": self.engine_action["action_type"],
            "outgoing_element_id": self.engine_action["outgoing"]["element_id"],
            "incoming_element_id": self.engine_action["incoming"]["element_id"],
        }
        self.assertEqual(payload["engine_action"], expected)
        self.assertEqual(
            payload["gameweek_decision_sha256"],
            sha256_file(self.completed.gameweek_decision_path),
        )

    def test_follow_engine_derives_no_override(self) -> None:
        payload = json.loads(self._prospective().entry_path.read_bytes())
        self.assertEqual(payload["human_action_declaration"], "FOLLOW_ENGINE")
        self.assertEqual(payload["human_action"], payload["engine_action"])
        self.assertTrue(payload["human_action_matches_engine"])
        self.assertEqual(payload["override_status"], "NO_OVERRIDE")
        self.assertIsNone(payload["override_reason"])

    def test_exact_structured_transfer_match_derives_no_override(self) -> None:
        artifact = self._prospective(
            human_action=HumanActionKind.TRANSFER,
            outgoing_element_id=self.engine_action["outgoing"]["element_id"],
            incoming_element_id=self.engine_action["incoming"]["element_id"],
        )
        payload = json.loads(artifact.entry_path.read_bytes())
        self.assertEqual(payload["human_action_declaration"], "TRANSFER")
        self.assertEqual(payload["human_action"], payload["engine_action"])
        self.assertTrue(payload["human_action_matches_engine"])
        self.assertEqual(payload["override_status"], "NO_OVERRIDE")

    def test_roll_against_engine_transfer_derives_override(self) -> None:
        artifact = self._prospective(
            human_action=HumanActionKind.ROLL,
            override_reason="Prefer to preserve the free transfer.",
        )
        payload = json.loads(artifact.entry_path.read_bytes())
        self.assertEqual(payload["engine_action"]["action_type"], "TRANSFER")
        self.assertEqual(payload["human_action"]["action_type"], "ROLL")
        self.assertFalse(payload["human_action_matches_engine"])
        self.assertEqual(payload["override_status"], "OVERRIDE")

    def test_different_transfer_derives_override(self) -> None:
        engine_out = self.engine_action["outgoing"]["element_id"]
        artifact = self._prospective(
            human_action=HumanActionKind.TRANSFER,
            outgoing_element_id=engine_out,
            incoming_element_id=1,
            override_reason="Used a different incoming player.",
        )
        payload = json.loads(artifact.entry_path.read_bytes())
        self.assertEqual(payload["override_status"], "OVERRIDE")
        self.assertNotEqual(payload["human_action"], payload["engine_action"])

    def test_prospective_override_requires_contemporaneous_reason(self) -> None:
        with self.assertRaises(DecisionJournalError):
            self._prospective(human_action=HumanActionKind.ROLL)

    def test_no_override_cannot_smuggle_override_narrative(self) -> None:
        with self.assertRaises(DecisionJournalError):
            self._prospective(override_reason="Unnecessary narrative")

    def test_identical_semantic_input_has_deterministic_identity_and_reuses_bytes(self) -> None:
        first = self._prospective()
        before = first.entry_path.read_bytes()
        second = self._prospective()
        self.assertEqual(first.journal_entry_id, second.journal_entry_id)
        self.assertTrue(second.reused)
        self.assertEqual(before, second.entry_path.read_bytes())

    def test_conflicting_existing_journal_bytes_fail_closed(self) -> None:
        artifact = self._prospective(
            human_action=HumanActionKind.ROLL,
            override_reason="Unique immutable conflict test.",
            clock=lambda: datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc),
        )
        original = artifact.entry_path.read_bytes()
        try:
            artifact.entry_path.write_bytes(original + b"conflict")
            with self.assertRaises(DecisionJournalConflictError):
                self._prospective(
                    human_action=HumanActionKind.ROLL,
                    override_reason="Unique immutable conflict test.",
                    clock=lambda: datetime(2026, 8, 27, 13, 0, tzinfo=timezone.utc),
                )
        finally:
            artifact.entry_path.write_bytes(original)

    def test_historical_backfill_is_explicit_and_keeps_unknown_action_time_null(self) -> None:
        artifact = self._historical()
        payload = json.loads(artifact.entry_path.read_bytes())
        self.assertEqual(payload["classification"], "HISTORICAL_BACKFILL")
        self.assertEqual(payload["journal_created_at"], "2026-08-30T12:00:00.000000Z")
        self.assertIsNone(payload["human_action_recorded_at"])
        self.assertEqual(
            payload["historical_evidence_sha256"], sha256_file(self.historical_evidence)
        )

    def test_historical_backfill_requires_preserved_evidence(self) -> None:
        with self.assertRaises(DecisionJournalError):
            self._historical(historical_evidence_path=None)

    def test_historical_backfill_still_requires_valid_predeadline_engine_chain(self) -> None:
        path = self.completed.final_manifest_path
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"tamper")
            with self.assertRaises(DecisionJournalError):
                self._historical()
        finally:
            path.write_bytes(original)

    def test_historical_entry_cannot_claim_prospective_timestamp_semantics(self) -> None:
        payload = json.loads(self._historical().entry_path.read_bytes())
        payload["human_action_recorded_at"] = "2026-08-27T12:00:00.000000Z"
        with self.assertRaises(DecisionJournalError):
            validate_decision_journal_entry(payload)

    def test_prospective_and_historical_identities_cannot_collide(self) -> None:
        self.assertNotEqual(
            self._prospective().journal_entry_id,
            self._historical().journal_entry_id,
        )

    def test_outcome_records_completion_without_mutating_journal(self) -> None:
        journal = self._prospective()
        before = journal.entry_path.read_bytes()
        outcome = record_decision_outcome(
            journal_entry_path=journal.entry_path,
            completion_bootstrap_path=self.completion_bootstrap,
            clock=lambda: AFTER,
        )
        payload = json.loads(outcome.outcome_path.read_bytes())
        self.assertEqual(payload["schema_version"], OUTCOME_SCHEMA_VERSION)
        self.assertEqual(payload["journal_entry_id"], journal.journal_entry_id)
        self.assertEqual(payload["journal_entry_sha256"], sha256_file(journal.entry_path))
        self.assertTrue(payload["official_completion"]["event_finished"])
        self.assertTrue(payload["official_completion"]["event_data_checked"])
        self.assertIsNone(payload["realized_manager_gameweek_points"])
        self.assertIsNone(payload["engine_action_counterfactual_points"])
        self.assertEqual(before, journal.entry_path.read_bytes())

    def test_outcome_fails_when_referenced_journal_is_corrupt(self) -> None:
        journal = self._historical()
        original = journal.entry_path.read_bytes()
        try:
            journal.entry_path.write_bytes(original + b"tamper")
            with self.assertRaises(DecisionJournalError):
                record_decision_outcome(
                    journal_entry_path=journal.entry_path,
                    completion_bootstrap_path=self.completion_bootstrap,
                    clock=lambda: AFTER,
                )
        finally:
            journal.entry_path.write_bytes(original)

    def test_outcome_rejects_self_consistent_journal_with_fabricated_artifact_hashes(
        self,
    ) -> None:
        journal_path = self._write_self_consistent_forged_journal(
            final_sha256="a" * 64,
            gameweek_sha256="b" * 64,
            reliability_sha256="c" * 64,
        )
        with self.assertRaisesRegex(
            DecisionJournalError, "not anchored to trusted evidence"
        ):
            record_decision_outcome(
                journal_entry_path=journal_path,
                completion_bootstrap_path=self.completion_bootstrap,
                clock=lambda: AFTER,
            )

    def test_outcome_rejects_self_consistent_fabricated_engine_action(
        self,
    ) -> None:
        reliability_path = next(
            self.completed.final_manifest_path.parent.rglob("decision_reliability.json")
        )
        journal_path = self._write_self_consistent_forged_journal(
            final_sha256=sha256_file(self.completed.final_manifest_path),
            gameweek_sha256=sha256_file(self.completed.gameweek_decision_path),
            reliability_sha256=sha256_file(reliability_path),
            engine_action={
                "action_type": "ROLL",
                "incoming_element_id": None,
                "outgoing_element_id": None,
            },
        )
        with self.assertRaisesRegex(
            DecisionJournalError,
            "engine action does not match the verified GameweekDecision",
        ):
            record_decision_outcome(
                journal_entry_path=journal_path,
                completion_bootstrap_path=self.completion_bootstrap,
                clock=lambda: AFTER,
            )

    def test_outcome_requires_unambiguous_official_finished_and_checked_event(self) -> None:
        journal = self._prospective()
        original = json.loads(self.completion_bootstrap.read_bytes())
        for field in ("finished", "data_checked"):
            with self.subTest(field=field):
                payload = json.loads(json.dumps(original))
                payload["events"][0][field] = False
                path = self.temporary / f"not-complete-{field}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(DecisionJournalError):
                    record_decision_outcome(
                        journal_entry_path=journal.entry_path,
                        completion_bootstrap_path=path,
                        clock=lambda: AFTER,
                    )

    def test_outcome_before_or_at_deadline_fails(self) -> None:
        journal = self._prospective()
        for value in (BEFORE, DEADLINE):
            with self.subTest(value=value):
                with self.assertRaises(DecisionJournalError):
                    record_decision_outcome(
                        journal_entry_path=journal.entry_path,
                        completion_bootstrap_path=self.completion_bootstrap,
                        clock=lambda value=value: value,
                    )

    def test_outcome_identity_is_deterministic_and_identical_bytes_reuse(self) -> None:
        journal = self._historical()
        first = record_decision_outcome(
            journal_entry_path=journal.entry_path,
            completion_bootstrap_path=self.completion_bootstrap,
            clock=lambda: LATER_AFTER,
        )
        second = record_decision_outcome(
            journal_entry_path=journal.entry_path,
            completion_bootstrap_path=self.completion_bootstrap,
            clock=lambda: LATER_AFTER,
        )
        self.assertEqual(first.outcome_id, second.outcome_id)
        self.assertTrue(second.reused)
        self.assertEqual(first.outcome_path.read_bytes(), second.outcome_path.read_bytes())

    def test_conflicting_existing_outcome_fails_closed(self) -> None:
        journal = self._historical()
        artifact = record_decision_outcome(
            journal_entry_path=journal.entry_path,
            completion_bootstrap_path=self.completion_bootstrap,
            clock=lambda: datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc),
        )
        original = artifact.outcome_path.read_bytes()
        try:
            artifact.outcome_path.write_bytes(original + b"conflict")
            with self.assertRaises(DecisionJournalConflictError):
                record_decision_outcome(
                    journal_entry_path=journal.entry_path,
                    completion_bootstrap_path=self.completion_bootstrap,
                    clock=lambda: datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc),
                )
        finally:
            artifact.outcome_path.write_bytes(original)

    def test_outcome_validator_rejects_asserted_points_and_extra_fields(self) -> None:
        journal = self._prospective()
        artifact = record_decision_outcome(
            journal_entry_path=journal.entry_path,
            completion_bootstrap_path=self.completion_bootstrap,
            clock=lambda: datetime(2026, 8, 30, 15, 0, tzinfo=timezone.utc),
        )
        payload = json.loads(artifact.outcome_path.read_bytes())
        payload["realized_manager_gameweek_points"] = 99
        with self.assertRaises(DecisionJournalError):
            validate_decision_outcome(payload)
        payload = json.loads(artifact.outcome_path.read_bytes())
        payload["hindsight_narrative"] = "not allowed"
        with self.assertRaises(DecisionJournalError):
            validate_decision_outcome(payload)


if __name__ == "__main__":
    unittest.main()
