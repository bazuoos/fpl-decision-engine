from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fpl_decision_engine.__main__ import build_parser
from fpl_decision_engine.decision import (
    APPEARANCE_ONLY_ALLOWED_POLICY,
    DecisionError,
    optimize_xi,
    projection_eligible_for_policy,
)
from fpl_decision_engine.editable_manager import (
    MANUAL_STATE_VERSION,
    MANUAL_VERIFICATION_SOURCE,
    ManualEditablePick,
    ManualEditableState,
    load_manual_editable_state,
)
from fpl_decision_engine.projection_provider import (
    ProjectionDataset,
    ProjectionPlayer,
    ProjectionState,
    XfpV01ParquetProvider,
    sha256_file,
)
from fpl_decision_engine.transfer_decision import (
    ROLL,
    SELLING_PRICE_SOURCE,
    TRANSFER,
    TransferDecisionError,
    TransferDecisionOutputExistsError,
    evaluate_one_transfer,
    one_transfer_payload,
    selling_price_map,
    write_one_transfer_decision,
)
from tests.fixture_support import FROZEN_GW2_ROOT


OWNED = (
    (1, "GK", 1, 4.0),
    (2, "GK", 2, 1.0),
    (3, "DEF", 1, 4.0),
    (4, "DEF", 2, 3.0),
    (5, "DEF", 3, 2.0),
    (6, "DEF", 4, 1.0),
    (7, "DEF", 5, 0.5),
    (8, "MID", 1, 5.0),
    (9, "MID", 2, 4.0),
    (10, "MID", 3, 3.0),
    (11, "MID", 4, 2.0),
    (12, "MID", 5, 1.0),
    (13, "FWD", 6, 6.0),
    (14, "FWD", 3, 2.0),
    (15, "FWD", 4, 1.0),
)


def player(
    player_id: int,
    position: str,
    team_id: int,
    projection: float | None,
    *,
    price_units: int = 50,
    state: ProjectionState = ProjectionState.VALID,
    expected_minutes: float | None = 90.0,
) -> ProjectionPlayer:
    return ProjectionPlayer(
        season="2026-27",
        target_gameweek=2,
        fpl_player_id=player_id,
        player_name=f"Player {player_id}",
        team_id=team_id,
        team_name=f"Team {team_id}",
        team_short_name=f"T{team_id}",
        position_id={"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}[position],
        position=position,
        price_units=price_units,
        projection=projection,
        projection_state=state,
        verified_blank=state is ProjectionState.VERIFIED_BLANK,
        availability_status="a",
        chance_of_playing_next_round=None,
        source_model_id="xfp_v01",
        model_scope="modeled_components_only",
        source_artifact_path="/frozen/gw2.parquet",
        source_artifact_sha256="a" * 64,
        expected_minutes=expected_minutes,
    )


def owned_players() -> tuple[ProjectionPlayer, ...]:
    return tuple(player(*row) for row in OWNED)


def dataset(extras: tuple[ProjectionPlayer, ...] = ()) -> ProjectionDataset:
    return ProjectionDataset(
        season="2026-27",
        target_gameweek=2,
        snapshot_timestamp="20260825T073532.450889Z",
        provider_id="xfp_v01_parquet_v1",
        provider_version="projection-provider-v1",
        source_model_id="xfp_v01",
        model_scope="modeled_components_only",
        source_artifact_path="/frozen/gw2.parquet",
        source_artifact_sha256="a" * 64,
        players_artifact_path="/frozen/players.parquet",
        players_artifact_sha256="b" * 64,
        players=owned_players() + extras,
    )


def state(root: Path, *, bank_units: int = 0) -> ManualEditableState:
    artifact = root / "manual_editable_state.json"
    if not artifact.exists():
        artifact.write_text("{}\n", encoding="utf-8")
    picks = tuple(
        ManualEditablePick(
            element_id=player_id,
            display_name=f"Player {player_id}",
            position=position,
        )
        for player_id, position, _, _ in OWNED
    )
    return ManualEditableState(
        version=MANUAL_STATE_VERSION,
        entry_id=12345,
        season="2026-27",
        target_gameweek=2,
        verification_source=MANUAL_VERIFICATION_SOURCE,
        verification_timestamp=None,
        recorded_timestamp="2026-08-26T13:20:44.123523Z",
        bank_units=bank_units,
        free_transfers=1,
        current_transfer_cost_points=0,
        post_deadline_transfers_known=True,
        selling_prices_verified=False,
        picks=picks,
        current_selection_verified=False,
        third_party_price_change_metadata=None,
        artifact_path=artifact,
        artifact_sha256=sha256_file(artifact),
    )


def prices(units: int = 50) -> dict[int, int]:
    return {player_id: units for player_id, *_ in OWNED}


def evaluate(
    root: Path,
    extras: tuple[ProjectionPlayer, ...],
    *,
    selling_prices: dict[int, int] | None = None,
    bank_units: int = 0,
):
    return evaluate_one_transfer(
        state(root, bank_units=bank_units),
        dataset(extras),
        selling_prices or prices(),
        decision_policy=APPEARANCE_ONLY_ALLOWED_POLICY,
        selling_price_source=SELLING_PRICE_SOURCE,
    )


class OneTransferDecisionTests(unittest.TestCase):
    def test_exact_affordability_and_point_one_over_budget_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exact = player(18, "MID", 6, 8.0, price_units=50)
            over = player(19, "MID", 6, 20.0, price_units=51)
            result = evaluate(root, (exact, over))
            incoming_ids = {row.incoming.fpl_player_id for row in result.transfer_candidates}
            self.assertIn(18, incoming_ids)
            self.assertNotIn(19, incoming_ids)
            self.assertTrue(
                all(row.resulting_bank_units == 0 for row in result.transfer_candidates)
            )

    def test_replacements_are_same_position_unique_and_exactly_one_out_in(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = evaluate(
                Path(temporary),
                (
                    player(16, "GK", 6, 9.0),
                    player(17, "DEF", 6, 9.0),
                    player(18, "MID", 6, 9.0),
                    player(19, "FWD", 6, 9.0),
                ),
            )
            owned_ids = {row[0] for row in OWNED}
            for candidate in result.transfer_candidates:
                self.assertEqual(candidate.outgoing.position, candidate.incoming.position)
                self.assertNotIn(candidate.incoming.fpl_player_id, owned_ids)
                resulting = {
                    player.fpl_player_id for player in candidate.optimized_result.squad
                }
                self.assertEqual(
                    owned_ids - resulting, {candidate.outgoing.fpl_player_id}
                )
                self.assertEqual(
                    resulting - owned_ids, {candidate.incoming.fpl_player_id}
                )
                self.assertEqual(len(resulting), 15)

            duplicate_universe = dataset(
                (player(18, "MID", 6, 9.0), player(18, "MID", 6, 9.0))
            )
            with self.assertRaisesRegex(TransferDecisionError, "duplicate player IDs"):
                evaluate_one_transfer(
                    state(Path(temporary)),
                    duplicate_universe,
                    prices(),
                    decision_policy=APPEARANCE_ONLY_ALLOWED_POLICY,
                )

    def test_three_per_club_is_enforced_after_transfer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            team_one_mid = player(20, "MID", 1, 50.0)
            result = evaluate(Path(temporary), (team_one_mid,))
            outgoing_ids = {
                row.outgoing.fpl_player_id for row in result.transfer_candidates
            }
            self.assertEqual(outgoing_ids, {8})
            self.assertTrue(
                all(max(row.optimized_result.club_counts.values()) <= 3 for row in result.transfer_candidates)
            )

    def test_attractive_fourth_player_from_a_club_cannot_be_a_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            # The owned squad already has three Team 1 players and no Team 1
            # forward. Replacing any owned forward with this row would create
            # an illegal fourth Team 1 player.
            illegal_fourth = player(20, "FWD", 1, 100.0, price_units=50)
            result = evaluate(Path(temporary), (illegal_fourth,))
            self.assertEqual(result.transfer_candidates, ())
            self.assertIsNone(result.best_transfer)
            self.assertEqual(result.recommended_action, ROLL)

    def test_roll_transfer_and_tie_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            worse = evaluate(root, (player(18, "MID", 6, 0.0),))
            self.assertEqual(worse.recommended_action, ROLL)
            self.assertIsNotNone(worse.roll_result)

            better = evaluate(root, (player(18, "MID", 6, 20.0),))
            self.assertEqual(better.recommended_action, TRANSFER)
            self.assertGreater(
                better.best_transfer.optimized_result.total_objective,
                better.roll_result.total_objective,
            )

            tied = evaluate(root, (player(18, "MID", 6, 1.0),))
            self.assertAlmostEqual(
                tied.best_transfer.optimized_result.total_objective,
                tied.roll_result.total_objective,
            )
            self.assertEqual(tied.recommended_action, ROLL)

    def test_resulting_bank_and_deterministic_candidate_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidates = (
                player(18, "MID", 6, 9.0, price_units=49),
                player(17, "DEF", 6, 9.0, price_units=49),
            )
            first = evaluate(root, candidates, bank_units=2)
            second = evaluate(root, candidates, bank_units=2)
            first_rows = [
                (
                    row.outgoing.fpl_player_id,
                    row.incoming.fpl_player_id,
                    row.resulting_bank_units,
                    row.optimized_result.total_objective,
                )
                for row in first.transfer_candidates
            ]
            second_rows = [
                (
                    row.outgoing.fpl_player_id,
                    row.incoming.fpl_player_id,
                    row.resulting_bank_units,
                    row.optimized_result.total_objective,
                )
                for row in second.transfer_candidates
            ]
            self.assertEqual(first_rows, second_rows)
            self.assertTrue(all(row[2] == 3 for row in first_rows))

    def test_tied_transfer_candidates_use_outgoing_then_incoming_id_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tied = (
                player(18, "FWD", 7, 9.0),
                player(19, "FWD", 7, 9.0),
            )
            first = evaluate(root, tied)
            second = evaluate(root, tied)
            first_order = [
                (row.outgoing.fpl_player_id, row.incoming.fpl_player_id)
                for row in first.transfer_candidates
            ]
            second_order = [
                (row.outgoing.fpl_player_id, row.incoming.fpl_player_id)
                for row in second.transfer_candidates
            ]
            self.assertEqual(first_order, second_order)
            self.assertEqual(first_order[:2], [(15, 18), (15, 19)])
            self.assertEqual(
                first.transfer_candidates[0].optimized_result.total_objective,
                first.transfer_candidates[1].optimized_result.total_objective,
            )
            self.assertEqual(first.recommended_action, TRANSFER)
            self.assertEqual(
                (first.best_transfer.outgoing.fpl_player_id,
                 first.best_transfer.incoming.fpl_player_id),
                (15, 18),
            )

    def test_incomplete_expected_minutes_invariant_fails_loudly(self) -> None:
        admitted = player(
            18,
            "MID",
            6,
            0.0,
            state=ProjectionState.INCOMPLETE,
            expected_minutes=0.0,
        )
        self.assertTrue(
            projection_eligible_for_policy(admitted, APPEARANCE_ONLY_ALLOWED_POLICY)
        )
        with tempfile.TemporaryDirectory() as temporary:
            admitted_result = evaluate(Path(temporary), (admitted,))
            admitted_candidates = [
                row
                for row in admitted_result.transfer_candidates
                if row.incoming.fpl_player_id == admitted.fpl_player_id
            ]
            self.assertTrue(admitted_candidates)
            self.assertTrue(
                all(
                    row.incoming.projection_state is ProjectionState.INCOMPLETE
                    and row.incoming.expected_minutes == 0.0
                    for row in admitted_candidates
                )
            )
        violated = replace(admitted, expected_minutes=1.0)
        with self.assertRaisesRegex(DecisionError, "exactly zero expected minutes"):
            projection_eligible_for_policy(
                violated, APPEARANCE_ONLY_ALLOWED_POLICY
            )
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
            TransferDecisionError, "exactly zero expected minutes"
        ):
            evaluate(Path(temporary), (violated,))

    def test_null_nan_and_infinite_incoming_projections_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rows = (
                player(18, "MID", 6, None, state=ProjectionState.MISSING),
                player(19, "MID", 6, float("nan")),
                player(20, "MID", 6, float("inf")),
                player(21, "MID", 6, float("-inf")),
            )
            result = evaluate(Path(temporary), rows)
            self.assertEqual(result.transfer_candidates, ())
            self.assertEqual(result.recommended_action, ROLL)

    def test_selling_price_coverage_and_parser_reject_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(TransferDecisionError, "missing"):
                evaluate(root, (), selling_prices={1: 50})
            with self.assertRaisesRegex(TransferDecisionError, "duplicate"):
                selling_price_map(["1:5.0", "1:5.1"])
            self.assertEqual(selling_price_map(["1:5.0"]), {1: 50})

    def test_task014_optimizer_is_reused_for_roll_and_every_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch(
            "fpl_decision_engine.transfer_decision.optimize_xi",
            wraps=optimize_xi,
        ) as reused:
            result = evaluate(
                Path(temporary),
                (player(18, "MID", 6, 9.0),),
            )
            self.assertEqual(reused.call_count, 1 + len(result.transfer_candidates))
            self.assertTrue(
                all(
                    call.kwargs["decision_policy"]
                    == APPEARANCE_ONLY_ALLOWED_POLICY
                    for call in reused.call_args_list
                )
            )
            owned_ids = {row[0] for row in OWNED}
            roll_ids = {
                row.fpl_player_id for row in reused.call_args_list[0].args[0]
            }
            self.assertEqual(roll_ids, owned_ids)
            for call in reused.call_args_list[1:]:
                candidate_ids = {row.fpl_player_id for row in call.args[0]}
                self.assertEqual(len(owned_ids - candidate_ids), 1)
                self.assertEqual(len(candidate_ids - owned_ids), 1)

    def test_artifacts_are_immutable_and_capture_required_provenance(self) -> None:
        """Exercise the real Task 016 writer and overwrite refusal in isolation."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            decision = evaluate(root, (player(18, "MID", 6, 20.0),))
            generated = datetime(2026, 8, 26, 14, 0, tzinfo=timezone.utc)
            artifacts = write_one_transfer_decision(
                decision,
                decision_data_root=root / "decisions",
                generated_at=generated,
            )
            payload = json.loads(artifacts.decision_path.read_text())
            self.assertEqual(payload["decision_policy"], APPEARANCE_ONLY_ALLOWED_POLICY)
            self.assertEqual(
                payload["selling_price_inputs"]["source"], SELLING_PRICE_SOURCE
            )
            self.assertTrue(payload["optimizer"]["reused_task014_optimize_xi"])
            self.assertEqual(
                payload["candidate_summaries_artifact"]["sha256"],
                artifacts.candidates_sha256,
            )
            before = {
                artifacts.decision_path: artifacts.decision_path.read_bytes(),
                artifacts.candidates_path: artifacts.candidates_path.read_bytes(),
            }
            with self.assertRaises(TransferDecisionOutputExistsError):
                write_one_transfer_decision(
                    decision,
                    decision_data_root=root / "decisions",
                    generated_at=generated,
                )
            self.assertEqual(
                before, {path: path.read_bytes() for path in before}
            )

    def test_cli_requires_explicit_policy_and_screenshot_acknowledgement(self) -> None:
        parser = build_parser()
        required = [
            "evaluate-one-transfer",
            "--manual-state-artifact",
            "/tmp/manual.json",
            "--selling-price",
            "1:5.0",
            "--selling-prices-transcribed-from-official-fpl-screenshot",
            "--decision-policy",
            APPEARANCE_ONLY_ALLOWED_POLICY,
        ]
        parsed = parser.parse_args(required)
        self.assertEqual(parsed.decision_policy, APPEARANCE_ONLY_ALLOWED_POLICY)
        self.assertTrue(
            parsed.selling_prices_transcribed_from_official_fpl_screenshot
        )

    def test_reviewed_full_gw2_fixture_result_hashes_and_savinho_club_gate(self) -> None:
        """Reproduce Task 016 and isolate the later Savinho club reassignment."""
        manual_path = FROZEN_GW2_ROOT / "manual_editable_state.json"
        reviewed_fixture_hashes = {
            FROZEN_GW2_ROOT / "player_gameweek_features.parquet": (
                "f7749a924f1223043f2d0d5c3be5004999157cde839a4c379c498e9a0c7a6887"
            ),
            FROZEN_GW2_ROOT / "xfp_v01_fixtures.parquet": (
                "5dc0042ca8e7da6ab96fb87e6bf8ef8b00f75ec8b4e017e68d140070de78c961"
            ),
            FROZEN_GW2_ROOT / "xfp_v01_gameweek.parquet": (
                "105fc489991b568d1d572213f188543fbe8fd07504f0f7845504fa76a3eaa5fc"
            ),
            FROZEN_GW2_ROOT / "players.parquet": (
                "0ddbe5be615b2e5fc7eeb631035d5b65a382d70bf7e1acf3e9a269ec9cd35589"
            ),
        }
        before = {path: sha256_file(path) for path in reviewed_fixture_hashes}
        self.assertEqual(before, reviewed_fixture_hashes)
        projection_path = next(
            path
            for path in reviewed_fixture_hashes
            if path.name == "xfp_v01_gameweek.parquet"
        )
        players_path = next(
            path for path in reviewed_fixture_hashes if path.name == "players.parquet"
        )
        projections = XfpV01ParquetProvider(
            projection_artifact=projection_path,
            players_artifact=players_path,
        ).load(season="2026-27", target_gameweek=2)
        self.assertEqual(len(projections.players), 610)
        self.assertEqual(
            Counter(player.position for player in projections.players),
            Counter({"GK": 67, "DEF": 202, "MID": 268, "FWD": 73}),
        )
        self.assertEqual(
            Counter(player.team_id for player in projections.players),
            Counter(
                {
                    1: 29, 2: 31, 3: 28, 4: 26, 5: 33,
                    6: 38, 7: 33, 8: 32, 9: 24, 10: 24,
                    11: 36, 12: 34, 13: 27, 14: 35, 15: 30,
                    16: 33, 17: 27, 18: 28, 19: 37, 20: 25,
                }
            ),
        )
        self.assertEqual(
            Counter(player.price_units for player in projections.players),
            Counter(
                {
                    40: 74, 45: 138, 50: 174, 55: 113, 60: 56,
                    65: 25, 70: 8, 75: 10, 80: 6, 85: 1,
                    90: 1, 95: 2, 120: 1, 155: 1,
                }
            ),
        )
        manager_state = load_manual_editable_state(manual_path)
        verified_prices = {
            1: 60,
            111: 40,
            8: 55,
            175: 40,
            201: 55,
            391: 55,
            499: 55,
            40: 75,
            368: 70,
            426: 120,
            481: 65,
            557: 65,
            272: 45,
            321: 45,
            411: 155,
        }
        result = evaluate_one_transfer(
            manager_state,
            projections,
            verified_prices,
            decision_policy=APPEARANCE_ONLY_ALLOWED_POLICY,
            selling_price_source=SELLING_PRICE_SOURCE,
        )
        self.assertEqual(result.recommended_action, TRANSFER)
        self.assertEqual(len(result.transfer_candidates), 2107)
        self.assertAlmostEqual(result.roll_result.total_objective, 47.82, places=2)
        winner = result.best_transfer
        self.assertEqual(winner.outgoing.fpl_player_id, 499)
        self.assertEqual(winner.incoming.fpl_player_id, 115)
        self.assertEqual(winner.selling_price_units, 55)
        self.assertEqual(winner.purchase_price_units, 45)
        self.assertEqual(winner.resulting_bank_units, 10)
        self.assertEqual(
            winner.selling_price_units + manager_state.bank_units
            - winner.purchase_price_units,
            winner.resulting_bank_units,
        )
        self.assertEqual(
            winner.optimized_result.club_counts,
            {1: 3, 5: 2, 6: 1, 7: 1, 8: 1, 10: 1, 12: 1, 14: 1, 15: 3, 16: 1},
        )
        self.assertLessEqual(max(winner.optimized_result.club_counts.values()), 3)
        self.assertEqual(winner.optimized_result.formation, "4-5-1")
        self.assertEqual(winner.optimized_result.captain.fpl_player_id, 115)
        self.assertEqual(winner.optimized_result.vice_captain.fpl_player_id, 40)
        self.assertAlmostEqual(
            winner.optimized_result.base_xi_projection, 50.14, places=2
        )
        self.assertAlmostEqual(winner.optimized_result.captain_bonus, 11.45, places=2)
        self.assertAlmostEqual(winner.optimized_result.total_objective, 61.59, places=2)
        base_gain = (
            winner.optimized_result.base_xi_projection
            - result.roll_result.base_xi_projection
        )
        captain_gain = (
            winner.optimized_result.captain_bonus
            - result.roll_result.captain_bonus
        )
        total_gain = (
            winner.optimized_result.total_objective
            - result.roll_result.total_objective
        )
        self.assertAlmostEqual(base_gain, 9.42, places=2)
        self.assertAlmostEqual(captain_gain, 4.35, places=2)
        self.assertAlmostEqual(total_gain, 13.77, places=2)
        self.assertAlmostEqual(base_gain + captain_gain, total_gain, places=12)
        self.assertAlmostEqual(
            winner.incoming.projection,
            winner.optimized_result.captain_bonus,
            places=12,
        )

        old_keys = {
            (row.outgoing.fpl_player_id, row.incoming.fpl_player_id)
            for row in result.transfer_candidates
        }
        self.assertEqual(
            {key for key in old_keys if key[1] == 403},
            {(481, 403)},
        )
        savinho = next(
            player for player in projections.players if player.fpl_player_id == 403
        )
        self.assertEqual((savinho.player_name, savinho.team_id), ("Savinho", 15))
        reassigned_players = tuple(
            replace(
                player,
                team_id=19,
                team_name="Spurs",
                team_short_name="TOT",
            )
            if player.fpl_player_id == 403
            else player
            for player in projections.players
        )
        reassigned = evaluate_one_transfer(
            manager_state,
            replace(projections, players=reassigned_players),
            verified_prices,
            decision_policy=APPEARANCE_ONLY_ALLOWED_POLICY,
            selling_price_source=SELLING_PRICE_SOURCE,
        )
        refreshed_keys = {
            (row.outgoing.fpl_player_id, row.incoming.fpl_player_id)
            for row in reassigned.transfer_candidates
        }
        self.assertEqual(len(refreshed_keys), 2111)
        self.assertEqual(
            refreshed_keys - old_keys,
            {(40, 403), (368, 403), (426, 403), (557, 403)},
        )
        self.assertEqual(old_keys - refreshed_keys, set())
        self.assertEqual(
            {key for key in refreshed_keys if key[1] == 403},
            {(40, 403), (368, 403), (426, 403), (481, 403), (557, 403)},
        )
        self.assertEqual(
            {path: sha256_file(path) for path in reviewed_fixture_hashes}, before
        )


if __name__ == "__main__":
    unittest.main()
