from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from fpl_decision_engine.historical import (
    HISTORICAL_CLASSIFICATION,
    HISTORY_CUTOFF_RULE,
)
from fpl_decision_engine.historical_opponent_strength_experiment import (
    CANDIDATES,
    EXPERIMENT_VERSION,
    HistoricalOpponentStrengthExperimentError,
    HistoricalOpponentStrengthExperimentOutputExistsError,
    _create_opponent_contexts,
    _create_phase_predictions,
    _create_team_defense,
    _holdout_candidate_set,
    _holdout_decision,
    _write_outputs,
    clipped_factor,
    select_development_winner,
)


def _create_synthetic_defense_sources(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """CREATE TABLE development_fixtures AS SELECT * FROM (VALUES
          ('2023-24',10,1,1,'Alpha',2,'Beta',
           TIMESTAMPTZ '2023-08-12 15:00:00+00',2,1,true,false,
           'finalized_fixture_assignment'),
          ('2023-24',11,2,2,'Beta',1,'Alpha',
           TIMESTAMPTZ '2023-08-19 15:00:00+00',0,3,true,false,
           'finalized_fixture_assignment')
        ) t(season,fixture_id,gameweek,home_team_id,home_team_name,
            away_team_id,away_team_name,kickoff_time,home_score,away_score,
            finished,finished_provisional,fixture_assignment_context)"""
    )
    connection.execute(
        """CREATE TABLE development_history AS SELECT * FROM (VALUES
          ('2023-24',101,10,1,0.4,999.0),
          ('2023-24',102,10,1,0.6,999.0),
          ('2023-24',201,10,2,0.3,999.0),
          ('2023-24',202,10,2,0.2,999.0),
          ('2023-24',101,11,1,1.1,999.0),
          ('2023-24',102,11,1,0.9,999.0),
          ('2023-24',201,11,2,0.1,999.0),
          ('2023-24',202,11,2,0.2,999.0)
        ) t(season,element_id,fixture_id,historical_team_id,xg,xgc)"""
    )


def _create_context_sources(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """CREATE TABLE development_base_fixture AS SELECT * FROM (VALUES
          ('2023-24',2,200,1,2,TIMESTAMPTZ '2023-01-03 09:00:00+00',true),
          ('2023-24',8,201,1,2,TIMESTAMPTZ '2023-01-08 12:00:00+00',true)
        ) t(season,target_gameweek,fixture_id,target_fixture_count,
            opponent_team_id,target_deadline,target_has_fixture)"""
    )
    prior_rows = []
    for fixture_id in range(1, 8):
        # Event numbers deliberately run backwards: chronology, not event, must win.
        prior_rows.append(
            (
                "2023-24", fixture_id, 8 - fixture_id, 2, "Beta", fixture_id,
                float(fixture_id) / 2.0,
                f"2023-01-{fixture_id:02d} 10:00:00+00",
            )
        )
    prior_rows.append(
        ("2023-24", 99, 1, 2, "Beta", 99.0, 99.0, "2023-01-09 10:00:00+00")
    )
    prior_rows.append(
        ("2023-24", 201, 8, 2, "Beta", 88.0, 88.0, "2023-01-08 14:00:00+00")
    )
    connection.execute(
        """CREATE TABLE development_team_match_defense(
          season VARCHAR,fixture_id INTEGER,gameweek INTEGER,
          defending_team_id INTEGER,defending_team_name VARCHAR,
          team_goals_conceded DOUBLE,team_xgc DOUBLE,kickoff_time TIMESTAMPTZ)"""
    )
    connection.executemany(
        "INSERT INTO development_team_match_defense VALUES (?,?,?,?,?,?,?,?)",
        prior_rows,
    )


def _create_prediction_sources(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        f"""CREATE TABLE development_base_fixture AS SELECT * FROM (VALUES
          ('2023-24',2,100,true,1,1,1001,'MID',1,'Alpha',2,'H',
           TIMESTAMPTZ '2023-08-19 15:00:00+00',
           TIMESTAMPTZ '2023-08-18 17:00:00+00',60.0,2.0,450,5,
           0.3,0.2,5,1.0,0.4,3.4,true,false,'available',100,false,
           90,'played',false,false,'{HISTORY_CUTOFF_RULE}',
           '{HISTORICAL_CLASSIFICATION}'),
          ('2023-24',2,NULL,false,0,2,1002,'DEF',1,'Alpha',NULL,NULL,
           NULL,TIMESTAMPTZ '2023-08-18 17:00:00+00',0.0,0.0,0,0,
           NULL,NULL,6,NULL,NULL,NULL,false,true,'available',100,false,
           NULL,'verified_blank',true,false,'{HISTORY_CUTOFF_RULE}',
           '{HISTORICAL_CLASSIFICATION}')
        ) t(season,target_gameweek,fixture_id,target_has_fixture,target_fixture_count,
            element_id,code,position,team_id,team_name,opponent_team_id,home_away,
            kickoff_time,target_deadline,expected_minutes_v01,appearance_xfp_v01,
            prior_total_minutes,prior_gameweeks_with_data,prior_xg_per_90_used,
            prior_xa_per_90_used,goal_points_for_position,goal_xfp_v01,
            assist_xfp_v01,fixture_xfp_v01,attacking_rate_available,low_sample,
            availability_status,chance_of_playing_next_round,
            availability_forced_zero,previous_gameweek_minutes_uncapped,
            previous_gw_context_status,previous_gw_team_blank,
            previous_gw_player_not_in_universe,history_cutoff_rule,
            historical_classification)"""
    )
    connection.execute(
        f"""CREATE TABLE development_base_player AS SELECT * FROM (VALUES
          ('2023-24',2,1,60.0,2.0,1.0,0.4,3.4,1,60,2.0,5.0,3.0,10.0,10.0,
           'realized_fixture_rows','available'),
          ('2023-24',2,2,0.0,0.0,0.0,0.0,0.0,0,0,0.0,0.0,0.0,0.0,0.0,
           'verified_blank','available')
        ) t(season,target_gameweek,element_id,
            gameweek_expected_minutes_for_evaluation,
            gameweek_appearance_xfp_for_evaluation,
            gameweek_goal_xfp_for_evaluation,
            gameweek_assist_xfp_for_evaluation,gameweek_xfp_v01,
            actual_fixture_count,
            actual_minutes,actual_appearance_points,actual_goal_points,
            actual_assist_points,actual_modeled_points,actual_full_fpl_points,
            actual_state,availability_band)"""
    )
    connection.execute(
        """CREATE TABLE development_fixture_actuals AS SELECT * FROM (VALUES
          ('2023-24',2,1,100,60,5.0,3.0,10.0,true)
        ) t(season,target_gameweek,element_id,fixture_id,actual_minutes,
            actual_goal_points,actual_assist_points,actual_modeled_points,
            actuals_not_predictors)"""
    )
    connection.execute(
        """CREATE TABLE development_opponent_contexts AS SELECT * FROM (VALUES
          ('2023-24',2,100,2,1.2,1.1,1.25,1.15,6,'[1,2,3,4,5,6]',
           '[\"2023-08-01\"]',8.0,7.0,100.0,40,90.0,40,NULL)
        ) t(season,target_gameweek,target_fixture_id,opponent_team_id,
            factor_gc,factor_xgc,raw_factor_gc,raw_factor_xgc,prior_match_count,
            selected_source_fixture_ids,selected_source_kickoffs,
            opponent_gc_numerator,opponent_xgc_numerator,league_gc_numerator,
            league_gc_denominator,league_xgc_numerator,league_xgc_denominator,
            neutral_factor_reason)"""
    )
    connection.execute(
        "CREATE TABLE development_candidate_set(candidate VARCHAR)"
    )
    connection.executemany(
        "INSERT INTO development_candidate_set VALUES (?)",
        [(candidate,) for candidate in CANDIDATES],
    )


def _natural_metric(candidate: str, coverage: float = 100.0) -> tuple[object, ...]:
    return (
        "development", "2023-24", candidate, "natural", "modeled_xfp",
        100, int(coverage), 100 - int(coverage), 0, coverage,
        1.0, 2.0, 0.1, 0.5,
    )


def _comparison_rows(candidate: str, qualifies: bool) -> list[tuple[object, ...]]:
    rows = []
    for target in ("goal", "assist", "attacking_combined", "modeled_xfp"):
        control_spearman = 0.5
        if qualifies:
            changed_spearman = 0.52 if target == "attacking_combined" else 0.501
            changed_mae = 0.98 if target == "attacking_combined" else 1.0
        else:
            changed_spearman = control_spearman
            changed_mae = 1.0
        for predictor, mae, spearman in (
            ("F0", 1.0, control_spearman),
            (candidate, changed_mae, changed_spearman),
        ):
            rows.append((
                "development", "2023-24", candidate, predictor,
                f"primary:{target}", 100, mae, 2.0, 0.1, spearman,
            ))
    return rows


class TeamDefenseAndCausalityTests(unittest.TestCase):
    def test_team_gc_uses_scores_and_xgc_uses_opponent_summed_xg(self) -> None:
        connection = duckdb.connect(":memory:")
        try:
            _create_synthetic_defense_sources(connection)
            _create_team_defense(
                connection, phase="development", expected_team_match_rows=4
            )
            alpha_first = connection.execute(
                """SELECT team_goals_conceded,team_xgc,
                          goals_conceded_construction,xgc_construction
                   FROM development_team_match_defense
                   WHERE fixture_id=10 AND defending_team_id=1"""
            ).fetchone()
            self.assertEqual(alpha_first[:2], (1.0, 0.5))
            self.assertEqual(
                alpha_first[2:],
                ("fixture_score_away_score", "sum_opponent_player_fixture_xg"),
            )
            self.assertNotEqual(alpha_first[1], 1998.0)
        finally:
            connection.close()

    def test_duplicate_or_missing_player_xg_fails_closed(self) -> None:
        connection = duckdb.connect(":memory:")
        try:
            _create_synthetic_defense_sources(connection)
            connection.execute(
                "INSERT INTO development_history VALUES "
                "('2023-24',101,10,1,0.4,999.0)"
            )
            with self.assertRaisesRegex(
                HistoricalOpponentStrengthExperimentError, "duplicates=1"
            ):
                _create_team_defense(
                    connection, phase="development", expected_team_match_rows=4
                )
        finally:
            connection.close()

    def test_causal_window_uses_kickoff_order_and_excludes_later_lower_event(self) -> None:
        connection = duckdb.connect(":memory:")
        try:
            _create_context_sources(connection)
            _create_opponent_contexts(connection, phase="development")
            before = connection.execute(
                """SELECT selected_source_fixture_ids,factor_gc,factor_xgc,
                          cast(latest_source_kickoff AS VARCHAR)
                   FROM development_opponent_contexts
                   WHERE target_gameweek=8"""
            ).fetchone()
            selected = json.loads(before[0])
            self.assertEqual(selected, [7, 6, 5, 4, 3, 2])
            self.assertNotIn(99, selected)
            self.assertNotIn(201, selected)
            self.assertLess(before[3], "2023-01-08 12:00:00+00")

            connection.execute(
                """UPDATE development_team_match_defense
                   SET team_goals_conceded=9999,team_xgc=9999
                   WHERE fixture_id IN (99,201)"""
            )
            _create_opponent_contexts(connection, phase="development")
            after = connection.execute(
                """SELECT selected_source_fixture_ids,factor_gc,factor_xgc
                   FROM development_opponent_contexts WHERE target_gameweek=8"""
            ).fetchone()
            self.assertEqual(before[:3], after)
        finally:
            connection.close()

    def test_fewer_than_three_prior_matches_is_exactly_neutral(self) -> None:
        connection = duckdb.connect(":memory:")
        try:
            _create_context_sources(connection)
            _create_opponent_contexts(connection, phase="development")
            row = connection.execute(
                """SELECT prior_match_count,factor_gc,factor_xgc,
                          neutral_factor_reason
                   FROM development_opponent_contexts WHERE target_gameweek=2"""
            ).fetchone()
            self.assertEqual(
                row, (2, 1.0, 1.0, "fewer_than_3_prior_matches")
            )
        finally:
            connection.close()

    def test_clipping_is_exact(self) -> None:
        self.assertEqual(clipped_factor(0.1), 0.7)
        self.assertEqual(clipped_factor(0.7), 0.7)
        self.assertEqual(clipped_factor(1.0), 1.0)
        self.assertEqual(clipped_factor(1.3), 1.3)
        self.assertEqual(clipped_factor(9.0), 1.3)


class FrozenPredictionTests(unittest.TestCase):
    def test_f0_expected_minutes_availability_and_blanks_are_invariant(self) -> None:
        connection = duckdb.connect(":memory:")
        try:
            _create_prediction_sources(connection)
            _, player_table = _create_phase_predictions(
                connection, phase="development"
            )
            rows = connection.execute(
                f"""SELECT candidate,element_id,gameweek_expected_minutes,
                           gameweek_appearance_xfp,gameweek_goal_xfp,
                           gameweek_assist_xfp,gameweek_xfp,availability_status
                    FROM {player_table} ORDER BY candidate,element_id"""
            ).fetchall()
            indexed = {(row[0], row[1]): row for row in rows}
            self.assertEqual(indexed[("F0", 1)][2:7], (60.0, 2.0, 1.0, 0.4, 3.4))
            self.assertAlmostEqual(indexed[("F1", 1)][4], 1.2)
            self.assertAlmostEqual(indexed[("F1", 1)][5], 0.48)
            self.assertEqual(
                {indexed[(candidate, 1)][2] for candidate in CANDIDATES}, {60.0}
            )
            self.assertEqual(
                {indexed[(candidate, 1)][7] for candidate in CANDIDATES},
                {"available"},
            )
            for candidate in CANDIDATES:
                self.assertEqual(indexed[(candidate, 2)][2:7], (0.0, 0.0, 0.0, 0.0, 0.0))
        finally:
            connection.close()

    def test_target_actual_change_cannot_change_any_prediction(self) -> None:
        connection = duckdb.connect(":memory:")
        try:
            _create_prediction_sources(connection)
            _create_phase_predictions(connection, phase="development")
            before = connection.execute(
                """SELECT candidate,candidate_fixture_xfp
                   FROM development_candidate_fixture
                   WHERE element_id=1 ORDER BY candidate"""
            ).fetchall()
            connection.execute(
                """UPDATE development_fixture_actuals SET actual_goal_points=9,
                   actual_assist_points=9,actual_modeled_points=18"""
            )
            _create_phase_predictions(connection, phase="development")
            after = connection.execute(
                """SELECT candidate,candidate_fixture_xfp
                   FROM development_candidate_fixture
                   WHERE element_id=1 ORDER BY candidate"""
            ).fetchall()
            self.assertEqual(before, after)
        finally:
            connection.close()


class GatesAndOutputTests(unittest.TestCase):
    def test_development_selection_is_strict_and_prefers_f1_on_exact_tie(self) -> None:
        metrics = [_natural_metric(candidate) for candidate in CANDIDATES]
        common = _comparison_rows("F1", True) + _comparison_rows("F2", True)
        winner, records = select_development_winner(metrics, common)
        self.assertEqual(winner, "F1")
        self.assertTrue(all(record["development_qualifies"] for record in records))
        self.assertEqual(_holdout_candidate_set(winner), ("F0", "F1"))

    def test_failed_development_keeps_holdout_closed(self) -> None:
        metrics = [_natural_metric(candidate) for candidate in CANDIDATES]
        common = _comparison_rows("F1", False) + _comparison_rows("F2", False)
        winner, _ = select_development_winner(metrics, common)
        self.assertIsNone(winner)
        with self.assertRaises(HistoricalOpponentStrengthExperimentError):
            _holdout_candidate_set("F0")

    def test_2024_25_rows_cannot_enter_development_selection(self) -> None:
        metrics = [_natural_metric(candidate) for candidate in CANDIDATES]
        common = _comparison_rows("F1", False) + _comparison_rows("F2", False)
        metrics[0] = ("holdout", "2024-25", *metrics[0][2:])
        with self.assertRaisesRegex(
            HistoricalOpponentStrengthExperimentError, "holdout data"
        ):
            select_development_winner(metrics, common)

    def test_failed_holdout_prevents_promotion(self) -> None:
        metrics = [
            (
                "holdout", "2024-25", candidate, "natural", "modeled_xfp",
                100, 100, 0, 0, 100.0, 1.0, 2.0, 0.1, 0.5,
            )
            for candidate in ("F0", "F1")
        ]
        common = []
        for row in _comparison_rows("F1", True):
            changed = list(row)
            changed[0:2] = ["holdout", "2024-25"]
            if changed[3] == "F1" and changed[4] == "primary:attacking_combined":
                changed[9] = 0.5
            common.append(tuple(changed))
        self.assertFalse(_holdout_decision(metrics, common, "F1")["holdout_passed"])

    def test_completed_output_is_immutable(self) -> None:
        connection = duckdb.connect(":memory:")
        connection.execute("CREATE TABLE result AS SELECT 1 result_value")
        try:
            with TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / EXPERIMENT_VERSION).mkdir()
                with self.assertRaises(
                    HistoricalOpponentStrengthExperimentOutputExistsError
                ):
                    _write_outputs(
                        connection,
                        experiment_root=root,
                        manifest_base={},
                        tables=("result",),
                    )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
