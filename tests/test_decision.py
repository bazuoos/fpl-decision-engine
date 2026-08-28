from __future__ import annotations

import hashlib
import itertools
import math
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path

import duckdb
import highspy

from fpl_decision_engine.__main__ import build_parser
from fpl_decision_engine.decision import (
    DECISION_OUTPUT_CLASSIFICATION,
    DecisionError,
    DecisionOutputExistsError,
    OBJECTIVE_TOLERANCE,
    _formation_counts,
    _require_optimal,
    budget_m_to_units,
    decision_result_dict,
    optimize_squad,
    optimize_xi,
    rank_players,
    resolve_existing_squad,
    write_decision_artifacts,
)
from fpl_decision_engine.projection_provider import (
    ProjectionDataset,
    ProjectionPlayer,
    ProjectionProviderError,
    ProjectionState,
    XfpV01ParquetProvider,
    sha256_file,
)
from tests.fixture_support import materialized_frozen_gw2


def player(
    player_id: int,
    position: str,
    *,
    team_id: int | None = None,
    projection: float | None = None,
    price_units: int = 50,
    state: ProjectionState = ProjectionState.VALID,
) -> ProjectionPlayer:
    team = team_id if team_id is not None else ((player_id - 1) % 6) + 1
    value = player_id / 10 if projection is None and state is ProjectionState.VALID else projection
    if state is ProjectionState.VERIFIED_BLANK:
        value = 0.0
    return ProjectionPlayer(
        season="2026-27",
        target_gameweek=2,
        fpl_player_id=player_id,
        player_name=f"Player {player_id}",
        team_id=team,
        team_name=f"Team {team}",
        team_short_name=f"T{team}",
        position_id={"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}[position],
        position=position,
        price_units=price_units,
        projection=value,
        projection_state=state,
        verified_blank=state is ProjectionState.VERIFIED_BLANK,
        availability_status="a",
        chance_of_playing_next_round=None,
        source_model_id="synthetic_model",
        model_scope="modeled_components_only",
        source_artifact_path="/immutable/projections.parquet",
        source_artifact_sha256="a" * 64,
    )


def legal_pool() -> tuple[ProjectionPlayer, ...]:
    rows = [player(index, "GK") for index in (1, 2)]
    rows.extend(player(index, "DEF") for index in range(3, 8))
    rows.extend(player(index, "MID") for index in range(8, 13))
    rows.extend(player(index, "FWD") for index in range(13, 16))
    return tuple(rows)


def dataset(rows: tuple[ProjectionPlayer, ...] | None = None) -> ProjectionDataset:
    return ProjectionDataset(
        season="2026-27",
        target_gameweek=2,
        snapshot_timestamp="20260825T073532.450889Z",
        provider_id="synthetic_provider_v1",
        provider_version="projection-provider-v1",
        source_model_id="synthetic_model",
        model_scope="modeled_components_only",
        source_artifact_path="/immutable/projections.parquet",
        source_artifact_sha256="a" * 64,
        players_artifact_path="/immutable/players.parquet",
        players_artifact_sha256="b" * 64,
        players=rows or legal_pool(),
    )


def brute_force_xi(
    squad: tuple[ProjectionPlayer, ...],
) -> tuple[float, tuple[int, ...]]:
    """Independent XI enumeration used only to verify the production MIP."""
    best_objective = -math.inf
    best_ids: tuple[int, ...] | None = None
    for starters in itertools.combinations(squad, 11):
        counts = Counter(row.position for row in starters)
        if not (
            counts["GK"] == 1
            and counts["DEF"] >= 3
            and counts["MID"] >= 2
            and counts["FWD"] >= 1
        ):
            continue
        projections = [float(row.projection) for row in starters]
        objective = sum(projections) + max(projections)
        ids = tuple(sorted(row.fpl_player_id for row in starters))
        if (
            objective > best_objective + OBJECTIVE_TOLERANCE
            or (
                math.isclose(
                    objective,
                    best_objective,
                    rel_tol=0.0,
                    abs_tol=OBJECTIVE_TOLERANCE,
                )
                and (best_ids is None or ids < best_ids)
            )
        ):
            best_objective = objective
            best_ids = ids
    if best_ids is None:
        raise AssertionError("synthetic squad has no legal XI")
    return best_objective, best_ids


def brute_force_squad(
    rows: tuple[ProjectionPlayer, ...], budget_units: int
) -> tuple[float, int, tuple[int, ...], tuple[int, ...]]:
    """Exhaust every legal reduced-pool squad without using HiGHS."""
    by_position = {
        position: tuple(row for row in rows if row.position == position)
        for position in ("GK", "DEF", "MID", "FWD")
    }
    best: tuple[float, int, tuple[int, ...], tuple[int, ...]] | None = None
    for groups in itertools.product(
        itertools.combinations(by_position["GK"], 2),
        itertools.combinations(by_position["DEF"], 5),
        itertools.combinations(by_position["MID"], 5),
        itertools.combinations(by_position["FWD"], 3),
    ):
        squad = tuple(itertools.chain.from_iterable(groups))
        cost = sum(row.price_units for row in squad)
        if cost > budget_units or max(Counter(row.team_id for row in squad).values()) > 3:
            continue
        objective, starter_ids = brute_force_xi(squad)
        squad_ids = tuple(sorted(row.fpl_player_id for row in squad))
        if best is None:
            best = (objective, cost, squad_ids, starter_ids)
            continue
        if objective > best[0] + OBJECTIVE_TOLERANCE:
            best = (objective, cost, squad_ids, starter_ids)
        elif math.isclose(
            objective, best[0], rel_tol=0.0, abs_tol=OBJECTIVE_TOLERANCE
        ) and (cost, squad_ids) < (best[1], best[2]):
            best = (objective, cost, squad_ids, starter_ids)
    if best is None:
        raise AssertionError("synthetic pool has no legal squad")
    return best


def old_total_squad_projection_choice(
    rows: tuple[ProjectionPlayer, ...], budget_units: int
) -> tuple[float, tuple[int, ...]]:
    """Counterfactual old decomposition: maximize all 15 projections first."""
    by_position = {
        position: tuple(row for row in rows if row.position == position)
        for position in ("GK", "DEF", "MID", "FWD")
    }
    candidates: list[tuple[float, int, tuple[int, ...], tuple[ProjectionPlayer, ...]]] = []
    for groups in itertools.product(
        itertools.combinations(by_position["GK"], 2),
        itertools.combinations(by_position["DEF"], 5),
        itertools.combinations(by_position["MID"], 5),
        itertools.combinations(by_position["FWD"], 3),
    ):
        squad = tuple(itertools.chain.from_iterable(groups))
        cost = sum(row.price_units for row in squad)
        if cost > budget_units or max(Counter(row.team_id for row in squad).values()) > 3:
            continue
        total = sum(float(row.projection) for row in squad)
        ids = tuple(sorted(row.fpl_player_id for row in squad))
        candidates.append((total, cost, ids, squad))
    maximum = max(row[0] for row in candidates)
    tied = [row for row in candidates if math.isclose(row[0], maximum)]
    chosen = min(tied, key=lambda row: (row[1], row[2]))
    return brute_force_xi(chosen[3])[0], chosen[2]


def decomposition_counterexample_pool() -> tuple[ProjectionPlayer, ...]:
    # Two £5.0m/6-point GKs maximize total squad projection. The true joint
    # optimum instead combines a £9.0m/10-point starter with a £1.0m/0 bench GK.
    rows = [
        player(1, "GK", team_id=1, projection=6.0, price_units=50),
        player(2, "GK", team_id=2, projection=6.0, price_units=50),
        player(3, "GK", team_id=3, projection=10.0, price_units=90),
        player(4, "GK", team_id=4, projection=0.0, price_units=10),
    ]
    rows.extend(
        player(index, "DEF", team_id=((index - 5) % 7) + 5, projection=1.0)
        for index in range(5, 10)
    )
    rows.extend(
        player(index, "MID", team_id=((index - 5) % 7) + 5, projection=1.0)
        for index in range(10, 15)
    )
    rows.extend(
        player(index, "FWD", team_id=((index - 5) % 7) + 5, projection=1.0)
        for index in range(15, 18)
    )
    return tuple(rows)


class DecisionTests(unittest.TestCase):
    def test_formation_enumeration_covers_every_rule_legal_shape(self) -> None:
        expected = {
            (3, 4, 3),
            (3, 5, 2),
            (4, 3, 3),
            (4, 4, 2),
            (4, 5, 1),
            (5, 2, 3),
            (5, 3, 2),
            (5, 4, 1),
        }
        self.assertEqual(set(_formation_counts()), expected)
        self.assertTrue(
            all(defenders + midfielders + forwards == 10 for defenders, midfielders, forwards in expected)
        )

    def test_decision_cli_commands_accept_explicit_inputs(self) -> None:
        parser = build_parser()
        rank = parser.parse_args(
            ["rank-players", "--target-gameweek", "2", "--json"]
        )
        self.assertEqual(rank.command, "rank-players")
        xi = parser.parse_args(
            [
                "optimize-xi", "--target-gameweek", "2", "--player-ids",
                *[str(index) for index in range(1, 16)],
            ]
        )
        self.assertEqual(len(xi.player_ids), 15)
        squad = parser.parse_args(
            ["optimize-squad", "--target-gameweek", "2", "--budget", "99.9"]
        )
        self.assertEqual(squad.budget, "99.9")

    def test_integer_budget_arithmetic(self) -> None:
        self.assertEqual(budget_m_to_units("100.0"), 1000)
        self.assertEqual(budget_m_to_units("75.0"), 750)
        with self.assertRaises(DecisionError):
            budget_m_to_units("100.05")

    def test_full_pool_optimizer_enforces_squad_and_xi_rules(self) -> None:
        result = optimize_squad(dataset(), budget_units=750)
        self.assertEqual(len(result.squad), 15)
        self.assertEqual(
            {position: sum(p.position == position for p in result.squad)
             for position in ("GK", "DEF", "MID", "FWD")},
            {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3},
        )
        self.assertLessEqual(max(result.club_counts.values()), 3)
        self.assertEqual(result.squad_cost_units, 750)
        starters = [row for row in result.selections if row.is_starter]
        self.assertEqual(len(starters), 11)
        self.assertEqual(sum(row.player.position == "GK" for row in starters), 1)
        self.assertGreaterEqual(sum(row.player.position == "DEF" for row in starters), 3)
        self.assertGreaterEqual(sum(row.player.position == "MID" for row in starters), 2)
        self.assertGreaterEqual(sum(row.player.position == "FWD" for row in starters), 1)
        self.assertEqual(len([row for row in result.selections if not row.is_starter]), 4)
        with self.assertRaises(DecisionError):
            optimize_squad(dataset(), budget_units=749)

    def test_captain_and_vice_are_starters_and_objective_is_decomposed(self) -> None:
        result = optimize_xi(legal_pool(), budget_units=750)
        captains = [row for row in result.selections if row.is_captain]
        vice_captains = [row for row in result.selections if row.is_vice_captain]
        self.assertEqual(len(captains), 1)
        self.assertEqual(len(vice_captains), 1)
        captain = captains[0]
        vice = vice_captains[0]
        self.assertTrue(captain.is_starter)
        self.assertTrue(vice.is_starter)
        self.assertNotEqual(captain.player.fpl_player_id, vice.player.fpl_player_id)
        ordered_starters = sorted(
            (row for row in result.selections if row.is_starter),
            key=lambda row: (-float(row.player.projection), row.player.fpl_player_id),
        )
        self.assertEqual(captain.player.fpl_player_id, ordered_starters[0].player.fpl_player_id)
        self.assertEqual(vice.player.fpl_player_id, ordered_starters[1].player.fpl_player_id)
        self.assertAlmostEqual(
            result.total_objective,
            result.base_xi_projection + result.captain_bonus,
        )
        self.assertAlmostEqual(captain.captain_bonus, captain.player.projection)
        self.assertEqual(vice.captain_bonus, 0.0)
        self.assertAlmostEqual(vice.total_contribution, vice.base_contribution)

    def test_joint_mip_has_distinct_captain_and_vice_binaries(self) -> None:
        from fpl_decision_engine.decision import _build_squad_model

        rows = legal_pool()
        model, squad, starter, captain, vice, objective, _ = _build_squad_model(
            rows, 750
        )
        model.maximize(objective)
        _require_optimal(model, "synthetic joint MIP did not solve")
        self.assertEqual(round(sum(model.val(squad))), 15)
        self.assertEqual(round(sum(model.val(starter))), 11)
        self.assertEqual(round(sum(model.val(captain))), 1)
        self.assertEqual(round(sum(model.val(vice))), 1)
        self.assertTrue(
            all(
                model.val(captain)[index] + model.val(vice)[index] <= 1.0 + 1e-9
                for index in range(len(rows))
            )
        )

    def test_ranking_is_within_position_and_player_id_breaks_ties(self) -> None:
        rows = (
            player(8, "MID", projection=4.0),
            player(3, "MID", projection=4.0),
            player(1, "GK", projection=9.0),
        )
        result = rank_players(dataset(rows))
        mids = [row for row in result.rows if row.player.position == "MID"]
        self.assertEqual([row.player.fpl_player_id for row in mids], [3, 8])
        self.assertEqual([row.position_rank for row in mids], [1, 2])
        self.assertEqual(
            [row.position_rank for row in result.rows if row.player.position == "GK"],
            [1],
        )

    def test_verified_blank_zero_is_eligible_but_missing_and_incomplete_are_not(self) -> None:
        rows = (
            player(1, "GK", state=ProjectionState.VERIFIED_BLANK),
            player(2, "GK", projection=None, state=ProjectionState.MISSING),
            player(3, "GK", projection=1.5, state=ProjectionState.INCOMPLETE),
        )
        result = rank_players(dataset(rows))
        self.assertEqual([row.player.fpl_player_id for row in result.rows], [1])
        self.assertEqual(result.rows[0].player.projection, 0.0)
        self.assertEqual(
            result.excluded_counts,
            {"incomplete_projection": 1, "missing_projection": 1},
        )

    def test_existing_squad_refuses_duplicates_unresolved_and_malformed_counts(self) -> None:
        data = dataset()
        ids = [player.fpl_player_id for player in data.players]
        with self.assertRaises(DecisionError):
            resolve_existing_squad(data, ids[:-1] + [ids[0]])
        with self.assertRaises(DecisionError):
            resolve_existing_squad(data, ids[:-1] + [999])
        malformed = tuple(
            replace(row, position="DEF", position_id=2) if row.fpl_player_id == 1 else row
            for row in data.players
        )
        with self.assertRaises(DecisionError):
            resolve_existing_squad(dataset(malformed), ids)
        club_invalid = tuple(replace(row, team_id=1) for row in data.players)
        with self.assertRaises(DecisionError):
            resolve_existing_squad(dataset(club_invalid), ids)

    def test_existing_squad_refuses_ineligible_projection(self) -> None:
        rows = tuple(
            replace(row, projection_state=ProjectionState.INCOMPLETE)
            if row.fpl_player_id == 1 else row
            for row in legal_pool()
        )
        with self.assertRaises(DecisionError):
            resolve_existing_squad(dataset(rows), range(1, 16))

    def test_optimizer_can_select_verified_blank_but_never_missing(self) -> None:
        rows = tuple(
            replace(
                row,
                projection=0.0,
                projection_state=ProjectionState.VERIFIED_BLANK,
                verified_blank=True,
            )
            if row.fpl_player_id == 1 else row
            for row in legal_pool()
        )
        rows += (
            player(16, "GK", projection=None, state=ProjectionState.MISSING),
        )
        result = optimize_squad(dataset(rows), budget_units=750)
        selected_ids = {row.fpl_player_id for row in result.squad}
        self.assertIn(1, selected_ids)
        self.assertNotIn(16, selected_ids)
        self.assertEqual(result.excluded_counts["missing_projection"], 1)

    def test_attractive_incomplete_and_malformed_missing_values_stay_ineligible(self) -> None:
        rows = legal_pool() + (
            player(
                16,
                "FWD",
                projection=999999.0,
                state=ProjectionState.INCOMPLETE,
            ),
            player(
                17,
                "MID",
                projection=float("nan"),
                state=ProjectionState.MISSING,
            ),
        )
        result = optimize_squad(dataset(rows), budget_units=750)
        selected_ids = {row.fpl_player_id for row in result.squad}
        self.assertNotIn(16, selected_ids)
        self.assertNotIn(17, selected_ids)
        self.assertEqual(
            result.excluded_counts,
            {"incomplete_projection": 1, "missing_projection": 1},
        )

    def test_non_finite_valid_projection_is_refused(self) -> None:
        rows = tuple(
            replace(row, projection=float("nan")) if row.fpl_player_id == 1 else row
            for row in legal_pool()
        )
        with self.assertRaisesRegex(DecisionError, "non-finite"):
            optimize_squad(dataset(rows), budget_units=750)

    def test_total_squad_projection_decomposition_counterexample(self) -> None:
        rows = decomposition_counterexample_pool()
        old_objective, old_ids = old_total_squad_projection_choice(rows, 750)
        result = optimize_squad(dataset(rows), budget_units=750)
        new_ids = tuple(row.fpl_player_id for row in result.squad)
        self.assertEqual(set(old_ids) & {1, 2, 3, 4}, {1, 2})
        self.assertEqual(set(new_ids) & {1, 2, 3, 4}, {3, 4})
        self.assertEqual(old_objective, 22.0)
        self.assertEqual(result.total_objective, 30.0)
        self.assertGreater(result.total_objective, old_objective)

    def test_joint_mip_matches_independent_brute_force_on_reduced_pools(self) -> None:
        second_pool: list[ProjectionPlayer] = []
        position_ids = {
            "GK": range(20, 23),
            "DEF": range(23, 29),
            "MID": range(29, 34),
            "FWD": range(34, 37),
        }
        for position, ids in position_ids.items():
            second_pool.extend(
                player(
                    player_id,
                    position,
                    team_id=(player_id % 7) + 1,
                    projection=((player_id * 7) % 19) / 3,
                    price_units=50,
                )
                for player_id in ids
            )
        for rows in (decomposition_counterexample_pool(), tuple(second_pool)):
            expected_objective, expected_cost, expected_squad, expected_starters = (
                brute_force_squad(rows, 750)
            )
            actual = optimize_squad(dataset(rows), budget_units=750)
            self.assertAlmostEqual(actual.total_objective, expected_objective)
            self.assertEqual(actual.squad_cost_units, expected_cost)
            self.assertEqual(
                tuple(row.fpl_player_id for row in actual.squad), expected_squad
            )
            self.assertEqual(
                tuple(
                    sorted(
                        row.player.fpl_player_id
                        for row in actual.selections
                        if row.is_starter
                    )
                ),
                expected_starters,
            )

    def test_non_optimal_highs_statuses_are_refused(self) -> None:
        class FakeModel:
            def __init__(self, status: highspy.HighsModelStatus) -> None:
                self.status = status

            def getModelStatus(self) -> highspy.HighsModelStatus:
                return self.status

            def modelStatusToString(self, status: highspy.HighsModelStatus) -> str:
                return status.name

        refused = (
            highspy.HighsModelStatus.kInfeasible,
            highspy.HighsModelStatus.kUnbounded,
            highspy.HighsModelStatus.kTimeLimit,
            highspy.HighsModelStatus.kIterationLimit,
            highspy.HighsModelStatus.kSolutionLimit,
            highspy.HighsModelStatus.kSolveError,
            highspy.HighsModelStatus.kUnknown,
        )
        for status in refused:
            with self.subTest(status=status), self.assertRaises(DecisionError):
                _require_optimal(FakeModel(status), "must prove optimal")  # type: ignore[arg-type]

    def test_optimizer_uses_lower_cost_then_lexicographic_squad_ties(self) -> None:
        rows = tuple(replace(row, projection=1.0) for row in legal_pool())
        rows += (player(16, "GK", team_id=6, projection=1.0, price_units=40),)
        result = optimize_squad(dataset(rows), budget_units=750)
        goalkeeper_ids = sorted(
            row.fpl_player_id for row in result.squad if row.position == "GK"
        )
        self.assertEqual(result.squad_cost_units, 740)
        self.assertEqual(goalkeeper_ids, [1, 16])
        repeated = optimize_squad(dataset(rows), budget_units=750)
        self.assertEqual(
            tuple(player.fpl_player_id for player in result.squad),
            tuple(player.fpl_player_id for player in repeated.squad),
        )
        self.assertEqual(
            tuple(row.player.fpl_player_id for row in result.selections if row.is_starter),
            tuple(row.player.fpl_player_id for row in repeated.selections if row.is_starter),
        )
        tied_starter_ids = sorted(
            row.player.fpl_player_id for row in result.selections if row.is_starter
        )
        self.assertEqual(result.captain.fpl_player_id, tied_starter_ids[0])
        self.assertEqual(result.vice_captain.fpl_player_id, tied_starter_ids[1])
        self.assertEqual(result.captain, repeated.captain)
        self.assertEqual(result.vice_captain, repeated.vice_captain)

    def test_decision_layer_accepts_a_fake_provider_without_xfp_imports(self) -> None:
        class FakeProvider:
            provider_id = "fake"
            provider_version = "v1"

            def load(self, *, season: str, target_gameweek: int) -> ProjectionDataset:
                self.request = (season, target_gameweek)
                return dataset()

        provider = FakeProvider()
        supplied = provider.load(season="2026-27", target_gameweek=2)
        result = optimize_squad(supplied, budget_units=750)
        self.assertEqual(provider.request, ("2026-27", 2))
        self.assertEqual(len(result.squad), 15)

    def test_artifacts_are_immutable_and_preserve_provenance(self) -> None:
        data = dataset()
        rankings = rank_players(data)
        result = optimize_squad(data, budget_units=750)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "decisions"
            artifacts = write_decision_artifacts(
                data,
                rankings,
                result,
                decision_data_root=root,
                generation_timestamp="20260826T010203.000000Z",
            )
            before = {
                path: sha256_file(path)
                for path in (
                    artifacts.rankings_path,
                    artifacts.squad_path,
                    artifacts.manifest_path,
                )
            }
            manifest = artifacts.manifest_path.read_text(encoding="utf-8")
            self.assertIn(DECISION_OUTPUT_CLASSIFICATION, manifest)
            self.assertIn("modeled_components_only", manifest)
            with self.assertRaises(DecisionOutputExistsError):
                write_decision_artifacts(
                    data,
                    rankings,
                    result,
                    decision_data_root=root,
                    generation_timestamp="20260826T010203.000000Z",
                )
            self.assertEqual(before, {path: sha256_file(path) for path in before})

    def test_result_explainability_payload(self) -> None:
        data = dataset()
        result = optimize_squad(data, budget_units=750)
        payload = decision_result_dict(data, result)
        self.assertEqual(payload["classification"], DECISION_OUTPUT_CLASSIFICATION)
        self.assertEqual(payload["projection_model_scope"], "modeled_components_only")
        self.assertEqual(len(payload["players"]), 15)
        self.assertEqual(payload["remaining_budget_units"], 0)


class XfpV01ProviderTests(unittest.TestCase):
    def _write_provider_inputs(self, root: Path) -> tuple[Path, Path]:
        projection = root / "projection.parquet"
        players = root / "players.parquet"
        connection = duckdb.connect()
        try:
            connection.execute(
                """CREATE TABLE projection AS SELECT * FROM (VALUES
                    ('2026-27', 2, 'snapshot', 'v0.1', 1, 'Valid', 1, 'Team', 1, 'Goalkeeper', 1, 2.5, true, 90.0),
                    ('2026-27', 2, 'snapshot', 'v0.1', 2, 'Blank', 2, 'Team', 2, 'Defender', 0, 0.0, true, 0.0),
                    ('2026-27', 2, 'snapshot', 'v0.1', 3, 'Incomplete', 3, 'Team', 3, 'Midfielder', 1, 1.0, false, 0.0),
                    ('2026-27', 2, 'snapshot', 'v0.1', 4, 'Missing', 4, 'Team', 4, 'Forward', 1, NULL, false, 0.0)
                ) AS t(season, target_gameweek, snapshot_timestamp, model_version,
                       fpl_player_id, web_name, team_id, team_name, position_id,
                       position, fixture_count, gameweek_xfp_v01, prediction_complete,
                       gameweek_expected_minutes_v01)"""
            )
            connection.execute("COPY projection TO ? (FORMAT PARQUET)", [str(projection)])
            connection.execute(
                """CREATE TABLE players AS SELECT * FROM (VALUES
                    (1, 'T1', 4.5, 'a', NULL, '2026-27', 'snapshot', 1, 1),
                    (2, 'T2', 5.0, 'a', 100, '2026-27', 'snapshot', 2, 2),
                    (3, 'T3', 5.5, 'd', 75, '2026-27', 'snapshot', 3, 3),
                    (4, 'T4', 6.0, 'u', 0, '2026-27', 'snapshot', 4, 4)
                ) AS t(fpl_player_id, team_short_name, price_m, status,
                       chance_of_playing_next_round, season, snapshot_timestamp,
                       team_id, position_id)"""
            )
            connection.execute("COPY players TO ? (FORMAT PARQUET)", [str(players)])
        finally:
            connection.close()
        return projection, players

    def test_provider_maps_all_projection_states_and_integer_prices(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            projection, players = self._write_provider_inputs(Path(temporary))
            projection_before = hashlib.sha256(projection.read_bytes()).hexdigest()
            players_before = hashlib.sha256(players.read_bytes()).hexdigest()
            data = XfpV01ParquetProvider(
                projection_artifact=projection, players_artifact=players
            ).load(season="2026-27", target_gameweek=2)
            self.assertEqual(
                [row.projection_state for row in data.players],
                [
                    ProjectionState.VALID,
                    ProjectionState.VERIFIED_BLANK,
                    ProjectionState.INCOMPLETE,
                    ProjectionState.MISSING,
                ],
            )
            self.assertEqual([row.price_units for row in data.players], [45, 50, 55, 60])
            self.assertEqual(
                [row.expected_minutes for row in data.players], [90.0, 0.0, 0.0, 0.0]
            )
            self.assertEqual(data.source_artifact_sha256, projection_before)
            self.assertEqual(data.players_artifact_sha256, players_before)
            self.assertEqual(projection_before, hashlib.sha256(projection.read_bytes()).hexdigest())
            self.assertEqual(players_before, hashlib.sha256(players.read_bytes()).hexdigest())

    def test_provider_rejects_nonzero_verified_blank(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            projection, players = self._write_provider_inputs(Path(temporary))
            connection = duckdb.connect()
            try:
                connection.execute(
                    "CREATE TABLE bad AS SELECT * REPLACE (1.0 AS gameweek_xfp_v01) FROM read_parquet(?) WHERE fpl_player_id = 2",
                    [str(projection)],
                )
                bad = Path(temporary) / "bad.parquet"
                connection.execute("COPY bad TO ? (FORMAT PARQUET)", [str(bad)])
            finally:
                connection.close()
            with self.assertRaises(ProjectionProviderError):
                XfpV01ParquetProvider(
                    projection_artifact=bad, players_artifact=players
                ).load(season="2026-27", target_gameweek=2)

    def test_read_only_decision_use_does_not_modify_reviewed_test_fixtures(self) -> None:
        """Exercise provider/ranking semantics on committed reviewed-byte copies."""
        with materialized_frozen_gw2() as fixture:
            fixture_sources = (
                fixture.gameweek_predictions,
                fixture.fixture_predictions,
                fixture.features,
                fixture.players,
            )
            before = {path: sha256_file(path) for path in fixture_sources}
            data = XfpV01ParquetProvider(
                projection_artifact=fixture.gameweek_predictions,
                players_artifact=fixture.players,
            ).load(season="2026-27", target_gameweek=2)
            ranking = rank_players(data)
            connection = duckdb.connect()
            try:
                direct_counts = connection.execute(
                    """SELECT
                           count(*) FILTER (
                               WHERE prediction_complete
                                 AND fixture_count > 0
                                 AND gameweek_xfp_v01 IS NOT NULL
                           ) AS valid,
                           count(*) FILTER (
                               WHERE fixture_count = 0
                                 AND gameweek_xfp_v01 = 0
                           ) AS verified_blank,
                           count(*) FILTER (
                               WHERE NOT prediction_complete
                                 AND fixture_count > 0
                                 AND gameweek_xfp_v01 IS NOT NULL
                           ) AS incomplete,
                           count(*) FILTER (
                               WHERE fixture_count > 0
                                 AND gameweek_xfp_v01 IS NULL
                           ) AS missing
                         FROM read_parquet(?)""",
                    [str(fixture.gameweek_predictions)],
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(direct_counts, (310, 0, 300, 0))
            self.assertEqual(len(ranking.rows), 310)
            self.assertEqual(
                ranking.excluded_counts,
                {"incomplete_projection": 300, "missing_projection": 0},
            )
            self.assertEqual(
                before, {path: sha256_file(path) for path in fixture_sources}
            )


if __name__ == "__main__":
    unittest.main()
