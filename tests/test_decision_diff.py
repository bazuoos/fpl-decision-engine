from __future__ import annotations

import ast
import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from fixture_support import materialized_frozen_gw2
from test_operational_runner import BEFORE, OperationalFixture, SequenceClock

import fpl_decision_engine.decision_diff as decision_diff_module
from fpl_decision_engine.decision_diff import (
    DECISION_DIFF_SCHEMA_VERSION,
    DecisionDiffError,
    DecisionDiffErrorCode,
    _OfficialPlayer,
    _Projection,
    _compare_runs,
    _trusted_run,
    build_decision_diff,
    build_decision_diff_id,
    serialize_decision_diff,
    validate_decision_diff,
    write_decision_diff,
)
from fpl_decision_engine.operational_runner import resume_gameweek


def _replace_run_fields(run, **changes):
    fields = dict(run.run_fields)
    fields.update(changes)
    return replace(run, run_fields=tuple(sorted(fields.items())))


def _replace_action(run, **changes):
    action = copy.deepcopy(dict(run.action))
    action.update(changes)
    return replace(run, action=action)


def _replace_selection(run, **changes):
    action = copy.deepcopy(dict(run.action))
    selection = dict(action["selection"])
    selection.update(changes)
    action["selection"] = selection
    return replace(run, action=action)


class DecisionDiffTests(unittest.TestCase):
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
        cls.final_manifest = cls.completed.final_manifest_path
        cls.base = _trusted_run(cls.final_manifest, "left")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stack.close()

    def test_identical_trusted_run_has_no_semantic_changes(self) -> None:
        result = build_decision_diff(self.final_manifest, self.final_manifest)
        self.assertEqual(
            result.payload["summary"],
            {
                "captaincy_changed": False,
                "engine_action_changed": False,
                "lineup_changed": False,
                "manager_provenance_changed": False,
                "manager_state_changed": False,
                "official_state_changed": False,
                "projections_changed": False,
                "reliability_changed": False,
                "run_provenance_changed": False,
            },
        )

    def test_player_team_change_is_reported_once_by_element_id(self) -> None:
        player = self.base.official_players[0]
        changed = replace(
            player,
            team_id=player.team_id + 20,
            team_name="Changed Club",
            team_short_name="CHG",
        )
        right = replace(
            self.base,
            official_players=(changed, *self.base.official_players[1:]),
        )
        rows = _compare_runs(self.base, right).payload["official_player_state"][
            "changed"
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["element_id"], player.element_id)
        self.assertEqual(
            rows[0]["changed_fields"], ["team_id", "team_name", "team_short_name"]
        )

    def test_price_change_reports_exact_before_after_and_delta(self) -> None:
        player = self.base.official_players[0]
        changed = replace(player, price_units=player.price_units + 1)
        right = replace(
            self.base,
            official_players=(changed, *self.base.official_players[1:]),
        )
        row = _compare_runs(self.base, right).payload["official_player_state"][
            "changed"
        ][0]
        self.assertEqual(row["before"]["price_units"], player.price_units)
        self.assertEqual(row["after"]["price_units"], player.price_units + 1)
        self.assertEqual(row["price_delta_units"], 1)

    def test_status_availability_and_news_changes_are_structured(self) -> None:
        player = self.base.official_players[0]
        changed = replace(
            player,
            status="d",
            chance_of_playing_next_round=50,
            news="Minor doubt",
        )
        right = replace(
            self.base,
            official_players=(changed, *self.base.official_players[1:]),
        )
        row = _compare_runs(self.base, right).payload["official_player_state"][
            "changed"
        ][0]
        self.assertEqual(
            row["changed_fields"],
            ["chance_of_playing_next_round", "news", "status"],
        )

    def test_player_added_and_removed_use_stable_identity(self) -> None:
        removed = self.base.official_players[0]
        added = replace(
            self.base.official_players[-1],
            element_id=max(row.element_id for row in self.base.official_players) + 1,
            name="Added Player",
        )
        right = replace(
            self.base,
            official_players=tuple(sorted((*self.base.official_players[1:], added))),
        )
        diff = _compare_runs(self.base, right).payload["official_player_state"]
        self.assertEqual([row["element_id"] for row in diff["removed"]], [removed.element_id])
        self.assertEqual([row["element_id"] for row in diff["added"]], [added.element_id])

    def test_projection_change_reports_exact_signed_delta(self) -> None:
        projection = next(
            row for row in self.base.projections if row.projected_xfp is not None
        )
        changed = replace(projection, projected_xfp=projection.projected_xfp + 0.125)
        right = replace(
            self.base,
            projections=tuple(
                changed if row.element_id == projection.element_id else row
                for row in self.base.projections
            ),
        )
        row = _compare_runs(self.base, right).payload["projections"]["changed"][0]
        self.assertAlmostEqual(row["xfp_delta"], 0.125)
        self.assertEqual(row["before"]["projected_xfp"], projection.projected_xfp)
        self.assertEqual(row["after"]["projected_xfp"], changed.projected_xfp)

    def test_null_projection_is_preserved_and_never_fabricated_as_zero(self) -> None:
        projection = next(
            row for row in self.base.projections if row.projected_xfp is not None
        )
        missing = replace(
            projection,
            projected_xfp=None,
            expected_minutes=None,
            projection_state="missing_projection",
            prediction_complete=False,
            attacking_rate_available=False,
        )
        right = replace(
            self.base,
            projections=tuple(
                missing if row.element_id == projection.element_id else row
                for row in self.base.projections
            ),
        )
        row = _compare_runs(self.base, right).payload["projections"]["changed"][0]
        self.assertIsNone(row["after"]["projected_xfp"])
        self.assertIsNone(row["after"]["expected_minutes"])
        self.assertIsNone(row["xfp_delta"])
        self.assertEqual(row["after"]["projection_state"], "missing_projection")

    def test_same_manager_state_reverified_is_only_a_provenance_change(self) -> None:
        right = _replace_run_fields(
            self.base,
            manager_evidence_source_sha256="b" * 64,
            manager_state_sha256="c" * 64,
            manager_verification_timestamp="2026-08-27T13:00:00.000000Z",
        )
        diff = _compare_runs(self.base, right).payload["manager_state"]
        self.assertFalse(diff["semantic_changed"])
        self.assertTrue(diff["provenance_changed"])
        self.assertEqual(len(diff["provenance_changes"]), 3)

    def test_actual_manager_changes_are_structurally_reported(self) -> None:
        manager = self.base.manager
        first_price = manager.selling_prices[0]
        incoming = next(
            row
            for row in self.base.official_players
            if row.element_id not in manager.squad
        )
        changed_prices = tuple(
            sorted(
                (
                    *manager.selling_prices[1:],
                    (incoming.element_id, incoming.name, incoming.price_units),
                )
            )
        )
        changed_squad = tuple(
            sorted((*manager.squad[1:], incoming.element_id))
        )
        right = replace(
            self.base,
            manager=replace(
                manager,
                bank_units=manager.bank_units + 1,
                free_transfers=manager.free_transfers + 1,
                chip_state="WILDCARD",
                squad=changed_squad,
                selling_prices=tuple(changed_prices),
            ),
        )
        diff = _compare_runs(self.base, right).payload["manager_state"]
        self.assertTrue(diff["semantic_changed"])
        self.assertEqual(
            [row["field"] for row in diff["field_changes"]],
            ["bank_units", "chip_state", "free_transfers"],
        )
        self.assertEqual(diff["squad_removed"], [first_price[0]])
        self.assertEqual(diff["squad_added"], [incoming.element_id])
        self.assertEqual(len(diff["selling_price_changes"]), 2)

    def test_roll_to_transfer_action_change_is_reported(self) -> None:
        roll = _replace_action(
            self.base,
            action_type="ROLL",
            incoming_element_id=None,
            incoming_name=None,
            outgoing_element_id=None,
            outgoing_name=None,
            objective_gain_vs_roll_xfp=0.0,
            resulting_bank_units=self.base.manager.bank_units,
            transfer_cost_points=0,
        )
        diff = _compare_runs(roll, self.base).payload["engine_action"]
        self.assertTrue(diff["changed"])
        self.assertEqual(diff["left"]["action_type"], "ROLL")
        self.assertEqual(diff["right"]["action_type"], "TRANSFER")

    def test_transfer_a_to_transfer_b_reports_player_ids(self) -> None:
        alternative = next(
            row
            for row in self.base.official_players
            if row.element_id != self.base.action["incoming_element_id"]
        )
        right = _replace_action(
            self.base,
            incoming_element_id=alternative.element_id,
            incoming_name=alternative.name,
        )
        changes = _compare_runs(self.base, right).payload["engine_action"][
            "action_changes"
        ]
        self.assertEqual(changes, [{
            "field": "incoming_element_id",
            "left": self.base.action["incoming_element_id"],
            "right": alternative.element_id,
        }])

    def test_xi_change_reports_starter_and_bench_sets(self) -> None:
        selection = self.base.action["selection"]
        old_starter, old_bench = selection["starting_xi"][0], selection["bench"][0]
        starters = sorted((set(selection["starting_xi"]) - {old_starter}) | {old_bench})
        bench = sorted((set(selection["bench"]) - {old_bench}) | {old_starter})
        right = _replace_selection(self.base, starting_xi=starters, bench=bench)
        diff = _compare_runs(self.base, right).payload["engine_action"]
        self.assertTrue(diff["lineup_changed"])
        self.assertEqual(diff["lineup"]["starters_added"], [old_bench])
        self.assertEqual(diff["lineup"]["starters_removed"], [old_starter])

    def test_captain_and_vice_change_are_separate(self) -> None:
        selection = self.base.action["selection"]
        right = _replace_selection(
            self.base,
            captain=selection["vice_captain"],
            vice_captain=selection["captain"],
        )
        diff = _compare_runs(self.base, right).payload["engine_action"]
        self.assertTrue(diff["captaincy_changed"])
        self.assertTrue(diff["captain"]["changed"])
        self.assertTrue(diff["vice_captain"]["changed"])
        self.assertFalse(diff["lineup_changed"])

    def test_existing_reliability_views_are_compared_without_recalculation(self) -> None:
        reliability = copy.deepcopy(dict(self.base.reliability))
        views = list(reliability["sensitivity_results"])
        changed_view = dict(views[0])
        changed_view["action_type"] = (
            "ROLL" if changed_view["action_type"] == "TRANSFER" else "TRANSFER"
        )
        views[0] = changed_view
        reliability["sensitivity_results"] = views
        right = replace(self.base, reliability=reliability)
        diff = _compare_runs(self.base, right).payload["reliability"]
        self.assertTrue(diff["changed"])
        self.assertEqual(diff["views_changed"][0]["view_id"], changed_view["view_id"])

    def test_structurally_valid_looking_untrusted_left_run_fails_closed(self) -> None:
        forged = self.temporary / "forged-final.json"
        forged.write_text(
            json.dumps(
                {
                    "schema_version": "operational-final-manifest-v1",
                    "preparation_id": "prep_" + "1" * 64,
                    "decision_id": "decision_" + "2" * 64,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        with self.assertRaises(DecisionDiffError) as raised:
            build_decision_diff(forged, self.final_manifest)
        self.assertEqual(raised.exception.code, DecisionDiffErrorCode.LEFT_RUN_INVALID)

    def test_hash_tampering_in_pinned_candidate_artifact_fails_closed(self) -> None:
        candidate = next(self.final_manifest.parent.rglob("legal_transfer_candidates.json"))
        original = candidate.read_bytes()
        try:
            candidate.write_bytes(original + b"\n")
            with self.assertRaises(DecisionDiffError) as raised:
                build_decision_diff(self.final_manifest, self.final_manifest)
            self.assertEqual(
                raised.exception.code, DecisionDiffErrorCode.TRUST_CHAIN_HASH_MISMATCH
            )
        finally:
            candidate.write_bytes(original)

    def test_cross_gameweek_comparison_is_rejected(self) -> None:
        with self.assertRaises(DecisionDiffError) as raised:
            _compare_runs(self.base, replace(self.base, target_gameweek=3))
        self.assertEqual(
            raised.exception.code, DecisionDiffErrorCode.DIFFERENT_TARGET_GAMEWEEK
        )

    def test_cross_season_comparison_is_rejected(self) -> None:
        with self.assertRaises(DecisionDiffError) as raised:
            _compare_runs(self.base, replace(self.base, season="2025-26"))
        self.assertEqual(raised.exception.code, DecisionDiffErrorCode.DIFFERENT_SEASON)

    def test_cross_deadline_comparison_is_rejected(self) -> None:
        with self.assertRaises(DecisionDiffError) as raised:
            _compare_runs(
                self.base,
                replace(self.base, official_deadline="2026-08-28T17:31:00.000000Z"),
            )
        self.assertEqual(
            raised.exception.code, DecisionDiffErrorCode.DIFFERENT_OFFICIAL_DEADLINE
        )

    def test_repeated_construction_has_identical_bytes_id_and_hash(self) -> None:
        first = _compare_runs(self.base, self.base)
        second = _compare_runs(self.base, self.base)
        self.assertEqual(first.decision_diff_id, second.decision_diff_id)
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(first.sha256, second.sha256)

    def test_semantic_identity_is_construction_order_independent(self) -> None:
        identity = {
            "season": self.base.season,
            "target_gameweek": self.base.target_gameweek,
            "official_deadline": self.base.official_deadline,
            "left_preparation_id": self.base.preparation_id,
            "left_decision_id": self.base.decision_id,
            "right_preparation_id": "prep_" + "b" * 64,
            "right_decision_id": "decision_" + "c" * 64,
        }
        reversed_construction = dict(reversed(tuple(identity.items())))
        self.assertEqual(
            build_decision_diff_id(**identity),
            build_decision_diff_id(**reversed_construction),
        )

    def test_reverse_order_reverses_values_and_has_distinct_identity(self) -> None:
        player = self.base.official_players[0]
        changed = replace(player, price_units=player.price_units + 1)
        right = replace(
            self.base,
            preparation_id="prep_" + "b" * 64,
            decision_id="decision_" + "c" * 64,
            official_players=(changed, *self.base.official_players[1:]),
        )
        forward = _compare_runs(self.base, right)
        reverse = _compare_runs(right, self.base)
        self.assertNotEqual(forward.decision_diff_id, reverse.decision_diff_id)
        forward_change = forward.payload["official_player_state"]["changed"][0]
        reverse_change = reverse.payload["official_player_state"]["changed"][0]
        self.assertEqual(forward_change["before"], reverse_change["after"])
        self.assertEqual(forward_change["after"], reverse_change["before"])
        self.assertEqual(forward_change["price_delta_units"], 1)
        self.assertEqual(reverse_change["price_delta_units"], -1)

    def test_immutable_publication_safely_reuses_identical_bytes(self) -> None:
        output = self.temporary / "published"
        first = write_decision_diff(
            left_final_manifest_path=self.final_manifest,
            right_final_manifest_path=self.final_manifest,
            output_root=output,
        )
        second = write_decision_diff(
            left_final_manifest_path=self.final_manifest,
            right_final_manifest_path=self.final_manifest,
            output_root=output,
        )
        self.assertFalse(first.reused)
        self.assertTrue(second.reused)
        self.assertEqual(first.artifact_sha256, second.artifact_sha256)

    def test_immutable_publication_conflict_fails_closed(self) -> None:
        output = self.temporary / "conflict"
        first = write_decision_diff(
            left_final_manifest_path=self.final_manifest,
            right_final_manifest_path=self.final_manifest,
            output_root=output,
        )
        first.artifact_path.write_bytes(b"conflicting bytes\n")
        with self.assertRaises(DecisionDiffError) as raised:
            write_decision_diff(
                left_final_manifest_path=self.final_manifest,
                right_final_manifest_path=self.final_manifest,
                output_root=output,
            )
        self.assertEqual(
            raised.exception.code,
            DecisionDiffErrorCode.IMMUTABLE_PUBLICATION_CONFLICT,
        )

    def test_schema_rejects_extra_fields_and_unsupported_versions(self) -> None:
        payload = _compare_runs(self.base, self.base).to_payload()
        payload["unexpected"] = True
        with self.assertRaises(DecisionDiffError) as extra:
            validate_decision_diff(payload)
        self.assertEqual(extra.exception.code, DecisionDiffErrorCode.MALFORMED_ARTIFACT)
        payload.pop("unexpected")
        payload["schema_version"] = "2.0.0"
        with self.assertRaises(DecisionDiffError) as version:
            validate_decision_diff(payload)
        self.assertEqual(
            version.exception.code,
            DecisionDiffErrorCode.UNSUPPORTED_CONTRACT_VERSION,
        )

    def test_non_finite_values_fail_closed(self) -> None:
        payload = _compare_runs(self.base, self.base).to_payload()
        payload["engine_action"]["left"]["selection"]["total_xfp"] = float("nan")
        with self.assertRaises(DecisionDiffError) as raised:
            serialize_decision_diff(payload)
        self.assertEqual(raised.exception.code, DecisionDiffErrorCode.NON_FINITE_NUMBER)

    def test_source_boundary_has_no_model_or_optimizer_recomputation_import(self) -> None:
        source = Path(decision_diff_module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_modules = {
            "decision",
            "features",
            "predictions",
            "transfer_decision",
            "decision_reliability",
        }
        imported = {
            node.module.rsplit(".", 1)[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertTrue(forbidden_modules.isdisjoint(imported))
        forbidden_calls = {
            "optimize_xi",
            "evaluate_one_transfer",
            "predict_xfp_v01",
            "write_decision_reliability",
        }
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertTrue(forbidden_calls.isdisjoint(called))

    def test_contract_uses_current_version_and_trailing_newline(self) -> None:
        result = _compare_runs(self.base, self.base)
        self.assertEqual(result.payload["schema_version"], DECISION_DIFF_SCHEMA_VERSION)
        self.assertTrue(result.canonical_bytes().endswith(b"\n"))


if __name__ == "__main__":
    unittest.main()
