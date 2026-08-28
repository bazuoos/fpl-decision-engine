from __future__ import annotations

import json
import unittest
from dataclasses import replace

import duckdb

from fpl_decision_engine.decision import (
    DecisionSelectionValidationError,
    optimize_xi,
    validate_decision_selection,
)
from fpl_decision_engine.projection_provider import (
    ProjectionPlayer,
    ProjectionState,
    XfpV01ParquetProvider,
    sha256_file,
)
from tests.fixture_support import materialized_frozen_gw2


def player(player_id: int, position: str) -> ProjectionPlayer:
    position_id = {"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}[position]
    return ProjectionPlayer(
        season="2026-27",
        target_gameweek=2,
        fpl_player_id=player_id,
        player_name=f"Player {player_id}",
        team_id=(player_id - 1) % 6 + 1,
        team_name=f"Team {(player_id - 1) % 6 + 1}",
        team_short_name=f"T{(player_id - 1) % 6 + 1}",
        position_id=position_id,
        position=position,
        price_units=50,
        projection=player_id / 10,
        projection_state=ProjectionState.VALID,
        verified_blank=False,
        availability_status="a",
        chance_of_playing_next_round=None,
        source_model_id="synthetic_model",
        model_scope="modeled_components_only",
        source_artifact_path="/test-only/projections.parquet",
        source_artifact_sha256="a" * 64,
        expected_minutes=90.0,
    )


def squad_for_positions(counts: dict[str, int]) -> tuple[ProjectionPlayer, ...]:
    rows: list[ProjectionPlayer] = []
    player_id = 1
    for position in ("GK", "DEF", "MID", "FWD"):
        for _ in range(counts.get(position, 0)):
            rows.append(player(player_id, position))
            player_id += 1
    return tuple(rows)


BASE_SQUAD = squad_for_positions({"GK": 2, "DEF": 5, "MID": 5, "FWD": 3})
BASE_STARTERS = (1, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13)
BASE_BENCH = (2, 7, 14, 15)
BASE_CAPTAIN = 8
BASE_VICE = 9


def selection_kwargs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "starting_xi_ids": BASE_STARTERS,
        "bench_ids": BASE_BENCH,
        "captain_id": BASE_CAPTAIN,
        "vice_captain_id": BASE_VICE,
    }
    values.update(overrides)
    return values


class DecisionSelectionValidatorTests(unittest.TestCase):
    def assert_violation(
        self,
        code: str,
        *,
        squad: tuple[ProjectionPlayer, ...] = BASE_SQUAD,
        **overrides: object,
    ) -> DecisionSelectionValidationError:
        with self.assertRaises(DecisionSelectionValidationError) as caught:
            validate_decision_selection(
                squad,
                **selection_kwargs(**overrides),  # type: ignore[arg-type]
            )
        self.assertIn(code, caught.exception.violation_codes)
        self.assertIn(code, str(caught.exception))
        return caught.exception

    def test_valid_structural_selection_passes(self) -> None:
        result = validate_decision_selection(BASE_SQUAD, **selection_kwargs())
        self.assertEqual(result.formation, "4-5-1")
        self.assertEqual(result.squad_ids, tuple(range(1, 16)))

    def test_existing_optimizer_output_passes_unchanged(self) -> None:
        optimized = optimize_xi(BASE_SQUAD)
        starters = tuple(
            row.player.fpl_player_id for row in optimized.selections if row.is_starter
        )
        bench = tuple(
            row.player.fpl_player_id for row in optimized.selections if not row.is_starter
        )
        result = validate_decision_selection(
            optimized.squad,
            starting_xi_ids=starters,
            bench_ids=bench,
            captain_id=optimized.captain.fpl_player_id,
            vice_captain_id=optimized.vice_captain.fpl_player_id,
        )
        self.assertEqual(result.formation, optimized.formation)

    def test_optimizer_and_validator_accept_three_defender_five_midfielder_edge(
        self,
    ) -> None:
        preferred_ids = {3, 4, 5, 8, 9, 10, 11, 12, 13, 14}
        boundary_squad = tuple(
            replace(row, projection=10.0 if row.fpl_player_id in preferred_ids else 0.0)
            for row in BASE_SQUAD
        )
        optimized = optimize_xi(boundary_squad)
        starters = tuple(
            row.player.fpl_player_id for row in optimized.selections if row.is_starter
        )
        bench = tuple(
            row.player.fpl_player_id for row in optimized.selections if not row.is_starter
        )
        validated = validate_decision_selection(
            optimized.squad,
            starting_xi_ids=starters,
            bench_ids=bench,
            captain_id=optimized.captain.fpl_player_id,
            vice_captain_id=optimized.vice_captain.fpl_player_id,
        )
        self.assertEqual(optimized.formation, "3-5-2")
        self.assertEqual(validated.formation, "3-5-2")
        self.assertEqual(len(validated.squad_ids), 15)
        self.assertEqual(len(validated.starting_xi_ids), 11)
        self.assertEqual(len(validated.bench_ids), 4)

    def test_ten_starters_fails(self) -> None:
        self.assert_violation("starter_count", starting_xi_ids=BASE_STARTERS[:-1])

    def test_twelve_starters_fails(self) -> None:
        self.assert_violation("starter_count", starting_xi_ids=BASE_STARTERS + (7,))

    def test_wrong_bench_count_fails(self) -> None:
        self.assert_violation("bench_count", bench_ids=BASE_BENCH[:-1])

    def test_duplicate_player_across_xi_and_bench_fails(self) -> None:
        self.assert_violation(
            "starter_substitute_overlap",
            starting_xi_ids=BASE_STARTERS[:-1] + (7,),
        )

    def test_omitted_squad_player_fails(self) -> None:
        self.assert_violation("unaccounted_squad_players", bench_ids=BASE_BENCH[:-1])

    def test_non_squad_player_fails(self) -> None:
        self.assert_violation(
            "non_squad_selection", bench_ids=BASE_BENCH[:-1] + (999,)
        )

    def test_zero_starting_goalkeepers_fails(self) -> None:
        self.assert_violation(
            "starting_goalkeeper_count",
            starting_xi_ids=BASE_STARTERS[1:] + (7,),
            bench_ids=(1, 2, 14, 15),
        )

    def test_two_starting_goalkeepers_fails(self) -> None:
        self.assert_violation(
            "starting_goalkeeper_count",
            starting_xi_ids=BASE_STARTERS[:-1] + (2,),
            bench_ids=(7, 13, 14, 15),
        )

    def test_two_starting_defenders_fails(self) -> None:
        self.assert_violation(
            "starting_defender_count",
            starting_xi_ids=(1, 3, 4, 8, 9, 10, 11, 12, 13, 14, 15),
            bench_ids=(2, 5, 6, 7),
        )

    def test_invalid_squad_position_counts_fail(self) -> None:
        squad = squad_for_positions({"GK": 2, "DEF": 6, "MID": 4, "FWD": 3})
        self.assert_violation(
            "squad_position_counts",
            squad=squad,
            starting_xi_ids=(1, 3, 4, 5, 9, 10, 11, 12, 13, 14, 15),
            bench_ids=(2, 6, 7, 8),
            captain_id=9,
            vice_captain_id=10,
        )

    def test_six_starting_defenders_fails(self) -> None:
        squad = squad_for_positions({"GK": 2, "DEF": 6, "MID": 4, "FWD": 3})
        self.assert_violation(
            "starting_defender_count",
            squad=squad,
            starting_xi_ids=(1, 3, 4, 5, 6, 7, 8, 9, 10, 13, 14),
            bench_ids=(2, 11, 12, 15),
            captain_id=9,
            vice_captain_id=10,
        )

    def test_fewer_than_two_starting_midfielders_fails(self) -> None:
        squad = squad_for_positions({"GK": 2, "DEF": 5, "MID": 4, "FWD": 4})
        self.assert_violation(
            "starting_midfielder_count",
            squad=squad,
            starting_xi_ids=(1, 3, 4, 5, 6, 7, 8, 12, 13, 14, 15),
            bench_ids=(2, 9, 10, 11),
            captain_id=3,
            vice_captain_id=4,
        )

    def test_more_than_five_starting_midfielders_fails(self) -> None:
        squad = squad_for_positions({"GK": 2, "DEF": 4, "MID": 6, "FWD": 3})
        self.assert_violation(
            "starting_midfielder_count",
            squad=squad,
            starting_xi_ids=(1, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13),
            bench_ids=(2, 6, 14, 15),
            captain_id=7,
            vice_captain_id=8,
        )

    def test_zero_starting_forwards_fails(self) -> None:
        self.assert_violation(
            "starting_forward_count",
            starting_xi_ids=(1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12),
            bench_ids=(2, 13, 14, 15),
        )

    def test_more_than_three_starting_forwards_fails(self) -> None:
        squad = squad_for_positions({"GK": 2, "DEF": 5, "MID": 4, "FWD": 4})
        self.assert_violation(
            "starting_forward_count",
            squad=squad,
            starting_xi_ids=(1, 3, 4, 5, 8, 9, 12, 13, 14, 15, 16),
            bench_ids=(2, 6, 7, 10),
            captain_id=8,
            vice_captain_id=9,
        )

    def test_bench_with_zero_goalkeepers_fails(self) -> None:
        self.assert_violation(
            "bench_goalkeeper_count",
            starting_xi_ids=BASE_STARTERS[:-1] + (2,),
            bench_ids=(7, 13, 14, 15),
        )

    def test_bench_with_two_goalkeepers_fails(self) -> None:
        self.assert_violation(
            "bench_goalkeeper_count",
            starting_xi_ids=BASE_STARTERS[1:] + (7,),
            bench_ids=(1, 2, 14, 15),
        )

    def test_null_captain_fails(self) -> None:
        self.assert_violation("captain_required", captain_id=None)

    def test_null_vice_captain_fails(self) -> None:
        self.assert_violation("vice_captain_required", vice_captain_id=None)

    def test_captain_not_in_starting_xi_fails(self) -> None:
        self.assert_violation("captain_in_starting_xi", captain_id=2)

    def test_vice_captain_not_in_starting_xi_fails(self) -> None:
        self.assert_violation("vice_captain_in_starting_xi", vice_captain_id=2)

    def test_captain_and_vice_captain_must_be_distinct(self) -> None:
        self.assert_violation("captain_vice_distinct", vice_captain_id=BASE_CAPTAIN)

    def test_validator_does_not_mutate_inputs(self) -> None:
        squad = list(BASE_SQUAD)
        starters = list(BASE_STARTERS)
        bench = list(BASE_BENCH)
        before = (tuple(squad), tuple(starters), tuple(bench))
        validate_decision_selection(
            squad,
            starting_xi_ids=starters,
            bench_ids=bench,
            captain_id=BASE_CAPTAIN,
            vice_captain_id=BASE_VICE,
        )
        self.assertEqual((tuple(squad), tuple(starters), tuple(bench)), before)

    def test_current_prices_and_affordability_are_not_validated(self) -> None:
        unaffordable = tuple(replace(row, price_units=10000) for row in BASE_SQUAD)
        result = validate_decision_selection(unaffordable, **selection_kwargs())
        self.assertEqual(result.formation, "4-5-1")

    def test_current_projection_eligibility_is_not_validated(self) -> None:
        currently_missing = tuple(
            replace(
                row,
                projection=None,
                projection_state=ProjectionState.MISSING,
                expected_minutes=None,
            )
            for row in BASE_SQUAD
        )
        result = validate_decision_selection(currently_missing, **selection_kwargs())
        self.assertEqual(result.formation, "4-5-1")

    def test_positions_come_from_trusted_player_records(self) -> None:
        changed = tuple(
            replace(row, position="GK", position_id=1)
            if row.fpl_player_id == 6
            else row
            for row in BASE_SQUAD
        )
        self.assert_violation("starting_goalkeeper_count", squad=changed)

    def test_malformed_player_ids_fail_closed_without_coercion(self) -> None:
        malformed = (replace(BASE_SQUAD[0], fpl_player_id=True),) + BASE_SQUAD[1:]
        caught = self.assert_violation(
            "malformed_squad_player_id",
            squad=malformed,
            starting_xi_ids=(True,) + BASE_STARTERS[1:],
        )
        self.assertIn("malformed_starting_xi_id", caught.violation_codes)

    def test_unhashable_malformed_id_raises_domain_error(self) -> None:
        malformed = (
            replace(BASE_SQUAD[0], fpl_player_id=[1]),  # type: ignore[arg-type]
        ) + BASE_SQUAD[1:]
        self.assert_violation("malformed_squad_player_id", squad=malformed)

    def test_wrong_squad_size_and_duplicate_squad_ids_fail_closed(self) -> None:
        self.assert_violation("squad_size", squad=BASE_SQUAD[:-1])
        self.assert_violation(
            "squad_size", squad=BASE_SQUAD + (player(16, "FWD"),)
        )
        duplicate = BASE_SQUAD[:-1] + (BASE_SQUAD[-2],)
        self.assert_violation("duplicate_squad_players", squad=duplicate)

    def test_all_structural_violations_are_collected(self) -> None:
        caught = self.assert_violation(
            "starter_count",
            starting_xi_ids=(1, 2),
            bench_ids=(1,),
            captain_id=None,
            vice_captain_id=None,
        )
        self.assertTrue(
            {
                "bench_count",
                "starter_substitute_overlap",
                "unaccounted_squad_players",
                "starting_goalkeeper_count",
                "bench_outfield_count",
                "captain_required",
                "vice_captain_required",
            }.issubset(caught.violation_codes)
        )

    def test_violation_code_order_is_deterministic_for_repeated_invalid_input(
        self,
    ) -> None:
        observed: list[tuple[str, ...]] = []
        for _ in range(5):
            with self.assertRaises(DecisionSelectionValidationError) as caught:
                validate_decision_selection(
                    BASE_SQUAD,
                    starting_xi_ids=(1, 2),
                    bench_ids=(1,),
                    captain_id=None,
                    vice_captain_id=None,
                )
            observed.append(caught.exception.violation_codes)
        expected = (
            "starter_count",
            "bench_count",
            "starter_substitute_overlap",
            "unaccounted_squad_players",
            "starting_goalkeeper_count",
            "starting_defender_count",
            "starting_midfielder_count",
            "starting_forward_count",
            "bench_outfield_count",
            "captain_required",
            "vice_captain_required",
        )
        self.assertEqual(observed, [expected] * 5)


class FrozenDecisionSelectionContractTests(unittest.TestCase):
    @staticmethod
    def _dataset(fixture: object):
        return XfpV01ParquetProvider(
            projection_artifact=fixture.gameweek_predictions,
            players_artifact=fixture.players,
        ).load(season="2026-27", target_gameweek=2)

    def test_task014_frozen_optimizer_output_passes_and_is_unchanged(self) -> None:
        with materialized_frozen_gw2() as fixture:
            before = {
                fixture.task014_squad: sha256_file(fixture.task014_squad),
                fixture.task014_manifest: sha256_file(fixture.task014_manifest),
                fixture.gameweek_predictions: sha256_file(
                    fixture.gameweek_predictions
                ),
            }
            self.assertEqual(
                before[fixture.task014_squad],
                "d1432d5766157ba4f20319a7185688838861417dd980203c79ed8e563ba65d31",
            )
            connection = duckdb.connect(":memory:")
            try:
                rows = connection.execute(
                    """SELECT fpl_player_id, starter, captain, vice_captain
                         FROM read_parquet(?)""",
                    [str(fixture.task014_squad)],
                ).fetchall()
            finally:
                connection.close()
            dataset = self._dataset(fixture)
            by_id = {row.fpl_player_id: row for row in dataset.players}
            squad_ids = tuple(int(row[0]) for row in rows)
            result = validate_decision_selection(
                tuple(by_id[player_id] for player_id in squad_ids),
                starting_xi_ids=tuple(int(row[0]) for row in rows if row[1]),
                bench_ids=tuple(int(row[0]) for row in rows if not row[1]),
                captain_id=next(int(row[0]) for row in rows if row[2]),
                vice_captain_id=next(int(row[0]) for row in rows if row[3]),
            )
            manifest = json.loads(fixture.task014_manifest.read_bytes())
            self.assertEqual(result.formation, "3-4-3")
            self.assertEqual(manifest["total_objective"], 86.55000000000001)
            self.assertEqual(manifest["captain"], "De Cuyper")
            self.assertEqual(manifest["vice_captain"], "Hinshelwood")
            self.assertEqual(
                {path: sha256_file(path) for path in before},
                before,
            )

    def test_task016_frozen_roll_output_passes_and_is_unchanged(self) -> None:
        with materialized_frozen_gw2() as fixture:
            decision_before = sha256_file(fixture.decision_template)
            candidates_before = sha256_file(fixture.candidates)
            payload = json.loads(fixture.decision.read_bytes())
            roll = payload["roll"]
            dataset = self._dataset(fixture)
            by_id = {row.fpl_player_id: row for row in dataset.players}
            result = validate_decision_selection(
                tuple(by_id[row["element_id"]] for row in roll["squad"]),
                starting_xi_ids=tuple(
                    row["element_id"] for row in roll["starting_xi"]
                ),
                bench_ids=tuple(row["element_id"] for row in roll["bench"]),
                captain_id=roll["captain"]["element_id"],
                vice_captain_id=roll["vice_captain"]["element_id"],
            )
            self.assertEqual(result.formation, "4-5-1")
            self.assertEqual(roll["total_objective"], 47.82)
            self.assertEqual(roll["captain"]["element_id"], 40)
            self.assertEqual(roll["vice_captain"]["element_id"], 368)
            self.assertEqual(payload["legal_transfer_candidate_count"], 2107)
            self.assertEqual(sha256_file(fixture.decision_template), decision_before)
            self.assertEqual(sha256_file(fixture.candidates), candidates_before)

    def test_task016_frozen_transfer_output_passes_and_is_unchanged(self) -> None:
        with materialized_frozen_gw2() as fixture:
            decision_before = sha256_file(fixture.decision_template)
            candidates_before = sha256_file(fixture.candidates)
            payload = json.loads(fixture.decision.read_bytes())
            transfer = payload["best_transfer"]
            optimized = transfer["optimized_squad"]
            dataset = self._dataset(fixture)
            by_id = {row.fpl_player_id: row for row in dataset.players}
            result = validate_decision_selection(
                tuple(by_id[row["element_id"]] for row in optimized["squad"]),
                starting_xi_ids=tuple(
                    row["element_id"] for row in optimized["starting_xi"]
                ),
                bench_ids=tuple(row["element_id"] for row in optimized["bench"]),
                captain_id=optimized["captain"]["element_id"],
                vice_captain_id=optimized["vice_captain"]["element_id"],
            )
            self.assertEqual(result.formation, "4-5-1")
            self.assertEqual(transfer["out"]["element_id"], 499)
            self.assertEqual(transfer["in"]["element_id"], 115)
            self.assertEqual(transfer["total_objective"], 61.59)
            self.assertEqual(transfer["captain_id"], 115)
            self.assertEqual(transfer["vice_captain_id"], 40)
            self.assertEqual(payload["legal_transfer_candidate_count"], 2107)
            self.assertEqual(sha256_file(fixture.decision_template), decision_before)
            self.assertEqual(sha256_file(fixture.candidates), candidates_before)


if __name__ == "__main__":
    unittest.main()
