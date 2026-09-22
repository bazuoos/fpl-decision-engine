from __future__ import annotations

import ast
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from fixture_support import materialized_frozen_gw2
from test_operational_runner import BEFORE, DEADLINE, OperationalFixture, SequenceClock

from fpl_decision_engine.manager_evidence_authoring import (
    AuthoringErrorCode,
    ManagerEvidenceAuthoringError,
    ManagerEvidenceDraft,
    load_draft,
    load_preparation_for_authoring,
    parse_draft_pick,
    publish_verified_evidence,
    run_existing_resume,
    save_draft,
    validate_draft,
)
from fpl_decision_engine.operational_runner import load_verified_manager_evidence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def imported_module_names(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 1:
                module = "fpl_decision_engine"
                if node.module:
                    module = f"{module}.{node.module}"
            else:
                module = node.module
            if module:
                imported.add(module)
                imported.update(f"{module}.{alias.name}" for alias in node.names)
    return imported


class ManagerEvidenceAuthoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        frozen = self.stack.enter_context(materialized_frozen_gw2())
        self.fixture = OperationalFixture(self.root / "fixture", frozen)
        self.prepared = self.fixture.prepare()
        self.preparation = load_preparation_for_authoring(
            self.prepared.preparation_manifest_path
        )
        manager_path = self.fixture.manager_evidence()
        manager = json.loads(manager_path.read_bytes())
        self.draft = ManagerEvidenceDraft(
            preparation_manifest_sha256=self.preparation.manifest.sha256,
            entry_id=manager["entry_id"],
            selected_element_ids=[row["element_id"] for row in manager["players"]],
            bank_m=manager["bank_m"],
            free_transfers=manager["free_transfers"],
            chip_state=manager["chip_state"],
            selling_price_m_by_element_id={
                row["element_id"]: row["selling_price_m"] for row in manager["players"]
            },
            evidence_source="Synthetic Transfers screen confirmation",
            evidence_source_sha256=None,
            current_selection_confirmed=True,
        )

    def tearDown(self) -> None:
        self.stack.close()

    def test_loads_only_hash_pinned_preparation_catalogue(self) -> None:
        self.assertGreater(len(self.preparation.catalogue), 15)
        self.assertEqual(self.preparation.season, "2026-27")
        self.assertEqual(self.preparation.manifest.target_gameweek, 2)
        self.assertTrue(all(row.element_id > 0 for row in self.preparation.catalogue))

    def test_publishes_canonical_owner_only_evidence_accepted_by_runner(self) -> None:
        result = publish_verified_evidence(
            self.draft,
            self.preparation,
            output_root=self.root / "private-evidence",
            clock=SequenceClock(BEFORE),
        )
        loaded = load_verified_manager_evidence(result.path)
        self.assertEqual(len(loaded.players), 15)
        self.assertEqual(loaded.input_sha256, result.sha256)
        self.assertEqual(os.stat(result.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(result.path.parent).st_mode & 0o777, 0o700)
        self.assertEqual(
            result.path.read_bytes(),
            json.dumps(
                json.loads(result.path.read_bytes()),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8"),
        )

    def test_identical_publication_reuses_and_conflict_fails(self) -> None:
        root = self.root / "private-evidence"
        first = publish_verified_evidence(
            self.draft, self.preparation, output_root=root, clock=SequenceClock(BEFORE)
        )
        second = publish_verified_evidence(
            self.draft, self.preparation, output_root=root, clock=SequenceClock(BEFORE)
        )
        self.assertFalse(first.reused)
        self.assertTrue(second.reused)
        first.path.write_text("conflict", encoding="utf-8")
        with self.assertRaises(ManagerEvidenceAuthoringError) as raised:
            publish_verified_evidence(
                self.draft,
                self.preparation,
                output_root=root,
                clock=SequenceClock(BEFORE),
            )
        self.assertEqual(raised.exception.code, AuthoringErrorCode.IMMUTABLE_CONFLICT)

    def test_deadline_gate_precedes_reuse_of_existing_evidence(self) -> None:
        root = self.root / "private-evidence"
        first = publish_verified_evidence(
            self.draft,
            self.preparation,
            output_root=root,
            clock=SequenceClock(BEFORE),
        )
        self.assertTrue(first.path.is_file())

        with self.assertRaises(ManagerEvidenceAuthoringError) as raised:
            publish_verified_evidence(
                self.draft,
                self.preparation,
                output_root=root,
                clock=SequenceClock(DEADLINE),
            )
        self.assertEqual(raised.exception.code, AuthoringErrorCode.DEADLINE_REACHED)
        self.assertEqual(
            load_verified_manager_evidence(first.path).input_sha256,
            first.sha256,
        )

    def test_draft_round_trip_is_mutable_private_convenience_state(self) -> None:
        path = self.root / "drafts" / "current.json"
        save_draft(self.draft, path)
        expected = replace(
            self.draft,
            selling_price_m_by_element_id={
                str(key): value
                for key, value in self.draft.selling_price_m_by_element_id.items()
            },
        )
        self.assertEqual(load_draft(path), expected)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        changed = replace(self.draft, bank_m="1.1")
        save_draft(changed, path)
        self.assertEqual(load_draft(path).bank_m, "1.1")

    def test_deadline_is_strict_and_utc(self) -> None:
        for value in (DEADLINE, DEADLINE.replace(microsecond=1)):
            with self.subTest(value=value), self.assertRaises(
                ManagerEvidenceAuthoringError
            ) as raised:
                publish_verified_evidence(
                    self.draft,
                    self.preparation,
                    output_root=self.root / value.isoformat(),
                    clock=SequenceClock(value),
                )
            self.assertEqual(raised.exception.code, AuthoringErrorCode.DEADLINE_REACHED)
        naive = datetime(2026, 8, 27, 12, 0)
        with self.assertRaises(ManagerEvidenceAuthoringError):
            publish_verified_evidence(
                self.draft,
                self.preparation,
                output_root=self.root / "naive",
                clock=SequenceClock(naive),
            )

        with self.assertRaises(ManagerEvidenceAuthoringError) as raised:
            publish_verified_evidence(
                self.draft,
                self.preparation,
                output_root=self.root / "crossed-deadline",
                clock=SequenceClock(BEFORE, DEADLINE),
            )
        self.assertEqual(raised.exception.code, AuthoringErrorCode.DEADLINE_REACHED)
        self.assertEqual(
            list(
                (self.root / "crossed-deadline").rglob(
                    "verified_manager_evidence.json"
                )
            ),
            [],
        )

    def test_field_validation_covers_private_and_public_boundaries(self) -> None:
        cases = {
            "wrong_preparation": replace(
                self.draft, preparation_manifest_sha256="0" * 64
            ),
            "duplicate": replace(
                self.draft,
                selected_element_ids=[self.draft.selected_element_ids[0]] * 15,
            ),
            "unknown": replace(
                self.draft,
                selected_element_ids=[*self.draft.selected_element_ids[:-1], 999999],
            ),
            "missing_price": replace(self.draft, selling_price_m_by_element_id={}),
            "bad_bank": replace(self.draft, bank_m="0.01"),
            "bad_transfer": replace(self.draft, free_transfers=-1),
            "chip": replace(self.draft, chip_state="WILDCARD"),
            "unconfirmed": replace(self.draft, current_selection_confirmed=False),
            "malformed_ids": replace(
                self.draft, selected_element_ids=[["private malformed value"]]
            ),
        }
        for name, draft in cases.items():
            with self.subTest(name=name):
                self.assertTrue(validate_draft(draft, self.preparation))

    def test_position_and_club_constraints_use_frozen_public_identity(self) -> None:
        selected = set(self.draft.selected_element_ids)
        changed = []
        for row in self.preparation.catalogue:
            changed.append(
                replace(row, team_id=1) if row.element_id in selected else row
            )
        altered = replace(self.preparation, catalogue=tuple(changed))
        codes = {item.code for item in validate_draft(self.draft, altered)}
        self.assertIn("CLUB_LIMIT", codes)

        first = self.draft.selected_element_ids[0]
        changed = [
            replace(row, position="MID") if row.element_id == first else row
            for row in self.preparation.catalogue
        ]
        altered = replace(self.preparation, catalogue=tuple(changed))
        codes = {item.code for item in validate_draft(self.draft, altered)}
        self.assertIn("POSITION_COMPOSITION", codes)

    def test_tampered_preparation_and_draft_fail_closed(self) -> None:
        players = (
            self.prepared.preparation_manifest_path.parent
            / "artifacts"
            / "players.parquet"
        )
        with players.open("ab") as output:
            output.write(b"tamper")
        with self.assertRaises(ManagerEvidenceAuthoringError) as raised:
            load_preparation_for_authoring(self.prepared.preparation_manifest_path)
        self.assertEqual(raised.exception.code, AuthoringErrorCode.INVALID_PREPARATION)
        with self.assertRaises(ManagerEvidenceAuthoringError) as raised:
            publish_verified_evidence(
                self.draft,
                self.preparation,
                output_root=self.root / "private-evidence",
                clock=SequenceClock(BEFORE),
            )
        self.assertEqual(raised.exception.code, AuthoringErrorCode.INVALID_PREPARATION)

        invalid = self.root / "invalid-draft.json"
        invalid.write_text("{}", encoding="utf-8")
        with self.assertRaises(ManagerEvidenceAuthoringError):
            load_draft(invalid)

    def test_public_resume_wrapper_delegates_to_existing_operational_path(self) -> None:
        published = publish_verified_evidence(
            self.draft,
            self.preparation,
            output_root=self.root / "private-evidence",
            clock=SequenceClock(BEFORE),
        )
        completed = run_existing_resume(
            self.preparation,
            published,
            clock=SequenceClock(BEFORE, BEFORE, BEFORE, BEFORE, BEFORE),
        )
        self.assertEqual(completed.status, "COMPLETED")

    def test_pick_parser_is_exact_and_private_errors_do_not_echo_values(self) -> None:
        self.assertEqual(parse_draft_pick("42:5.7"), (42, "5.7"))
        private_value = "42:not-a-private-price"
        with self.assertRaises(ManagerEvidenceAuthoringError) as raised:
            parse_draft_pick(private_value)
        self.assertNotIn(private_value, str(raised.exception))

    def test_authoring_boundary_has_no_decision_internal_imports(self) -> None:
        path = (
            REPOSITORY_ROOT
            / "src"
            / "fpl_decision_engine"
            / "manager_evidence_authoring.py"
        )
        imported = imported_module_names(ast.parse(path.read_text(encoding="utf-8")))
        forbidden = {
            "fpl_decision_engine.decision",
            "fpl_decision_engine.decision_journal",
            "fpl_decision_engine.decision_reliability",
            "fpl_decision_engine.features",
            "fpl_decision_engine.predictions",
            "fpl_decision_engine.transfer_decision",
        }
        self.assertFalse(
            any(
                name == item or name.startswith(item + ".")
                for name in imported
                for item in forbidden
            )
        )


if __name__ == "__main__":
    unittest.main()
