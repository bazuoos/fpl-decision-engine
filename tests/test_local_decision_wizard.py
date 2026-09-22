from __future__ import annotations

import ast
import json
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from fixture_support import materialized_frozen_gw2
from test_manager_evidence_authoring import imported_module_names
from test_operational_runner import BEFORE, DEADLINE, OperationalFixture, SequenceClock

from fpl_decision_engine.local_decision_wizard import (
    PUBLISH_PHRASE,
    RUN_PHRASE,
    WizardPorts,
    WizardState,
    default_draft_path,
    format_verified_result,
    run_guided_manager_decision,
    search_catalogue,
)
from fpl_decision_engine.manager_evidence_authoring import (
    AuthoringErrorCode,
    ManagerEvidenceAuthoringError,
    load_preparation_for_authoring,
)
from fpl_decision_engine.trusted_artifact_reader import (
    load_verified_gameweek_decision,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ScriptedIO:
    def __init__(
        self,
        manager: dict,
        *,
        chip: str = "NO_CHIP",
        free_transfers: int | None = None,
        publish: bool = True,
        run: bool = True,
        interrupt_field: str | None = None,
    ) -> None:
        self.manager = manager
        self.players = {row["element_id"]: row for row in manager["players"]}
        self.by_position: dict[str, list[int]] = {key: [] for key in ("GK", "DEF", "MID", "FWD")}
        for row in manager["players"]:
            self.by_position[row["position"]].append(row["element_id"])
        self.chip = chip
        self.free_transfers = (
            manager["free_transfers"] if free_transfers is None else free_transfers
        )
        self.publish = publish
        self.run = run
        self.interrupt_field = interrupt_field
        self.public: list[str] = []
        self.private: list[str] = []
        self.asked: list[str] = []
        self.confirmed: list[str] = []

    def show_public(self, message: str) -> None:
        self.public.append(message)

    def show_private(self, message: str) -> None:
        self.private.append(message)

    def ask_text(self, field: str, prompt: str) -> str:
        del prompt
        self.asked.append(field)
        if self.interrupt_field == field:
            raise KeyboardInterrupt
        if field.startswith(("GK_", "DEF_", "MID_", "FWD_")):
            position, raw_index = field.split("_")
            return str(self.by_position[position][int(raw_index) - 1])
        if field.startswith("selling_price_"):
            element_id = int(field.removeprefix("selling_price_"))
            return str(self.players[element_id]["selling_price_m"])
        values = {
            "entry_id": str(self.manager["entry_id"]),
            "bank_m": str(self.manager["bank_m"]),
            "free_transfers": str(self.free_transfers),
            "evidence_source": "",
            "evidence_source_sha256": "",
        }
        return values[field]

    def ask_choice(self, field: str, choices: tuple[str, ...]) -> str:
        self.asked.append(field)
        if field == "chip_state":
            return self.chip
        return choices[0]

    def confirm(self, field: str, phrase: str) -> bool:
        self.confirmed.append(phrase)
        if field == "publish":
            return self.publish and phrase == PUBLISH_PHRASE
        if field == "run":
            return self.run and phrase == RUN_PHRASE
        return True


class LocalDecisionWizardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        frozen = self.stack.enter_context(materialized_frozen_gw2())
        self.fixture = OperationalFixture(self.root / "fixture", frozen)
        self.prepared = self.fixture.prepare()
        self.preparation = load_preparation_for_authoring(
            self.prepared.preparation_manifest_path
        )
        self.manager = json.loads(self.fixture.manager_evidence().read_bytes())
        self.draft_root = self.root / "drafts"
        self.evidence_root = self.root / "evidence"

    def tearDown(self) -> None:
        self.stack.close()

    def run_wizard(self, io: ScriptedIO, **kwargs):
        return run_guided_manager_decision(
            self.prepared.preparation_manifest_path,
            io=io,
            draft_root=self.draft_root,
            evidence_root=self.evidence_root,
            clock=kwargs.pop("clock", SequenceClock(*([BEFORE] * 20))),
            **kwargs,
        )

    def test_complete_flow_uses_real_publication_runner_and_trusted_reader(self) -> None:
        io = ScriptedIO(self.manager)
        result = self.run_wizard(io)
        self.assertEqual(result.state, WizardState.VERIFIED_DECISION_AVAILABLE)
        self.assertTrue(result.evidence_path.is_file())
        self.assertTrue(result.final_manifest_path.is_file())
        verified = load_verified_gameweek_decision(result.final_manifest_path)
        self.assertEqual(verified.preparation_id, result.preparation_id)
        self.assertIn("VERIFIED ENGINE DECISION", io.private[-1])
        self.assertIn(PUBLISH_PHRASE, io.confirmed)
        self.assertIn(RUN_PHRASE, io.confirmed)

    def test_declining_publication_keeps_only_private_draft(self) -> None:
        io = ScriptedIO(self.manager, publish=False)
        result = self.run_wizard(io)
        self.assertEqual(result.state, WizardState.CANCELLED)
        self.assertIsNone(result.evidence_path)
        self.assertEqual(list(self.evidence_root.rglob("*.json")), [])
        self.assertTrue(
            default_draft_path(self.preparation, draft_root=self.draft_root).is_file()
        )

    def test_declining_run_leaves_verified_evidence_without_decision(self) -> None:
        io = ScriptedIO(self.manager, run=False)
        result = self.run_wizard(io)
        self.assertEqual(result.state, WizardState.VERIFIED_EVIDENCE_PUBLISHED)
        self.assertTrue(result.evidence_path.is_file())
        self.assertIsNone(result.final_manifest_path)

    def test_safety_failure_before_publication_preserves_only_draft(self) -> None:
        io = ScriptedIO(self.manager)
        check = Mock(side_effect=RuntimeError("synthetic block"))
        result = self.run_wizard(io, safety_check=check)
        self.assertEqual(result.state, WizardState.SAFETY_CHECK_FAILED)
        self.assertIsNone(result.evidence_path)
        self.assertEqual(list(self.evidence_root.rglob("*.json")), [])
        self.assertNotIn(PUBLISH_PHRASE, io.confirmed)
        check.assert_called_once_with()

    def test_safety_failure_after_publication_preserves_evidence_without_result(self) -> None:
        io = ScriptedIO(self.manager)
        check = Mock(side_effect=[None, RuntimeError("synthetic block")])
        run = Mock(side_effect=AssertionError("engine must not run"))
        result = self.run_wizard(
            io,
            safety_check=check,
            ports=replace(WizardPorts(), run=run),
        )
        self.assertEqual(result.state, WizardState.SAFETY_CHECK_FAILED)
        self.assertTrue(result.evidence_path.is_file())
        self.assertIsNone(result.final_manifest_path)
        self.assertNotIn(RUN_PHRASE, io.confirmed)
        self.assertNotIn("VERIFIED ENGINE DECISION", "\n".join(io.private))
        self.assertEqual(check.call_count, 2)
        run.assert_not_called()

    def test_deadline_stops_before_any_private_prompt(self) -> None:
        io = ScriptedIO(self.manager)
        result = self.run_wizard(io, clock=SequenceClock(DEADLINE))
        self.assertEqual(result.state, WizardState.DEADLINE_REACHED)
        self.assertEqual(io.asked, [])
        self.assertEqual(io.private, [])

    def test_crossing_deadline_after_confirmation_publishes_nothing(self) -> None:
        io = ScriptedIO(self.manager)
        result = self.run_wizard(io, clock=SequenceClock(BEFORE, DEADLINE))
        self.assertEqual(result.state, WizardState.DEADLINE_REACHED)
        self.assertEqual(list(self.evidence_root.rglob("*.json")), [])

    def test_interrupt_autosaves_and_never_publishes(self) -> None:
        io = ScriptedIO(self.manager, interrupt_field="entry_id")
        result = self.run_wizard(io)
        self.assertEqual(result.state, WizardState.CANCELLED)
        draft = default_draft_path(self.preparation, draft_root=self.draft_root)
        payload = json.loads(draft.read_bytes())
        self.assertEqual(len(payload["selected_element_ids"]), 15)
        self.assertEqual(list(self.evidence_root.rglob("*.json")), [])

    def test_unsupported_chip_and_zero_transfer_state_are_preserved(self) -> None:
        chip_io = ScriptedIO(self.manager, chip="WILDCARD")
        chip_result = self.run_wizard(chip_io)
        self.assertEqual(chip_result.state, WizardState.UNSUPPORTED_CHIP)
        chip_payload = json.loads(
            default_draft_path(self.preparation, draft_root=self.draft_root).read_bytes()
        )
        self.assertEqual(chip_payload["chip_state"], "WILDCARD")

        other_root = self.root / "zero-drafts"
        self.draft_root = other_root
        zero_io = ScriptedIO(self.manager, free_transfers=0)
        zero_result = self.run_wizard(zero_io)
        self.assertEqual(zero_result.state, WizardState.UNSUPPORTED_TRANSFER_STATE)
        zero_payload = json.loads(
            default_draft_path(self.preparation, draft_root=other_root).read_bytes()
        )
        self.assertEqual(zero_payload["free_transfers"], 0)

    def test_private_values_do_not_enter_public_output(self) -> None:
        private_marker = 987654321
        manager = {**self.manager, "entry_id": private_marker, "bank_m": "9.9"}
        io = ScriptedIO(manager, publish=False)
        self.run_wizard(io)
        public = "\n".join(io.public)
        private = "\n".join(io.private)
        self.assertNotIn(str(private_marker), public)
        self.assertNotIn("Bank: £9.9m", public)
        self.assertIn(str(private_marker), json.dumps(manager))
        self.assertIn("Bank: £9.9m", private)

    def test_storage_failure_stops_without_later_prompts(self) -> None:
        io = ScriptedIO(self.manager)
        save = Mock(
            side_effect=ManagerEvidenceAuthoringError(
                AuthoringErrorCode.STORAGE_FAILURE, "synthetic bounded failure"
            )
        )
        ports = replace(WizardPorts(), save_saved_draft=save)
        result = self.run_wizard(io, ports=ports)
        self.assertEqual(result.state, WizardState.STORAGE_FAILURE)
        self.assertEqual(io.asked, [])

    def test_trusted_reader_failure_never_displays_a_result(self) -> None:
        io = ScriptedIO(self.manager)
        load_result = Mock(side_effect=TypeError("synthetic reader failure"))
        ports = replace(WizardPorts(), load_verified_result=load_result)
        result = self.run_wizard(io, ports=ports)
        self.assertEqual(result.state, WizardState.TRUST_CHAIN_INVALID)
        self.assertTrue(result.evidence_path.is_file())
        self.assertNotIn("VERIFIED ENGINE DECISION", "\n".join(io.private))

    def test_resume_reuses_complete_saved_selection_without_retyping_players(self) -> None:
        first = ScriptedIO(self.manager, publish=False)
        self.assertEqual(self.run_wizard(first).state, WizardState.CANCELLED)
        resumed = ScriptedIO(self.manager, publish=False)
        self.assertEqual(self.run_wizard(resumed).state, WizardState.CANCELLED)
        self.assertIn("resume_squad", resumed.asked)
        self.assertFalse(any(field.startswith("GK_") for field in resumed.asked))
        public = "\n".join(resumed.public)
        self.assertIn("DRAFT_RESUMED", public)
        self.assertIn("squad complete", public)

    def test_search_is_bounded_deterministic_and_supports_exact_id(self) -> None:
        target = self.preparation.catalogue[-1]
        exact = search_catalogue(self.preparation.catalogue, str(target.element_id))
        self.assertEqual(exact[0], target)
        by_name = search_catalogue(
            self.preparation.catalogue,
            target.display_name.swapcase(),
            position=target.position,
        )
        self.assertIn(target, by_name)
        by_team = search_catalogue(self.preparation.catalogue, target.team_name, limit=2)
        self.assertLessEqual(len(by_team), 2)
        self.assertEqual(by_team, search_catalogue(self.preparation.catalogue, target.team_name, limit=2))
        first_page = search_catalogue(self.preparation.catalogue, "", limit=2)
        second_page = search_catalogue(
            self.preparation.catalogue, "", limit=2, offset=2
        )
        self.assertEqual(len(first_page), 2)
        self.assertTrue(set(first_page).isdisjoint(second_page))
        self.assertEqual(
            search_catalogue(
                self.preparation.catalogue,
                str(target.element_id),
                excluded_ids=frozenset({target.element_id}),
            ),
            (),
        )

    def test_result_formatter_uses_verified_payload(self) -> None:
        io = ScriptedIO(self.manager)
        result = self.run_wizard(io)
        verified = load_verified_gameweek_decision(result.final_manifest_path)
        rendered = format_verified_result(verified, observed_at=self.preparation.observed_at)
        action = verified.payload()["recommended_action"]
        self.assertIn(f"Action: {action['action_type']}", rendered)
        self.assertIn(verified.decision_id, rendered)
        self.assertIn("Any FPL action is manual", rendered)

    def test_wizard_boundary_has_no_engine_or_journal_internal_imports(self) -> None:
        path = REPOSITORY_ROOT / "src" / "fpl_decision_engine" / "local_decision_wizard.py"
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
