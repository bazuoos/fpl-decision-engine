from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from fpl_decision_engine.historical import HISTORICAL_CLASSIFICATION, HISTORY_CUTOFF_RULE
from fpl_decision_engine.historical_attacking_rate_experiment import (
    EXPERIMENT_VERSION,
    HistoricalAttackingRateExperimentError,
    HistoricalAttackingRateExperimentOutputExistsError,
    _create_phase_predictions,
    _holdout_candidate_set,
    _holdout_decision,
    _write_outputs,
    aggregate_position_rate,
    select_development_winner,
    stabilized_rate,
)


class StabilizationFormulaTests(unittest.TestCase):
    def test_exact_s1_k450(self) -> None:
        self.assertAlmostEqual(
            stabilized_rate("S1", raw_rate=2.0, observed_minutes=90, position_prior=1.0),
            (2.0 * 90 + 1.0 * 450) / 540,
        )

    def test_exact_s2_k900(self) -> None:
        self.assertAlmostEqual(
            stabilized_rate("S2", raw_rate=2.0, observed_minutes=90, position_prior=1.0),
            (2.0 * 90 + 1.0 * 900) / 990,
        )

    def test_exact_s3_threshold_180(self) -> None:
        self.assertEqual(
            stabilized_rate("S3", raw_rate=2.0, observed_minutes=179, position_prior=1.0),
            1.0,
        )
        self.assertEqual(
            stabilized_rate("S3", raw_rate=2.0, observed_minutes=180, position_prior=1.0),
            2.0,
        )

    def test_aggregate_prior_and_separate_xg_xa_denominators(self) -> None:
        xg = aggregate_position_rate(((1.0, 90), (None, 90), (0.0, 45)))
        xa = aggregate_position_rate(((None, 90), (0.5, 90), (0.0, 45)))
        self.assertEqual(xg[:2], (1.0, 135.0))
        self.assertAlmostEqual(xg[2], 2.0 / 3.0)
        self.assertEqual(xa[:2], (0.5, 135.0))
        self.assertAlmostEqual(xa[2], 1.0 / 3.0)

    def test_null_zero_and_no_league_fallback(self) -> None:
        self.assertEqual(
            stabilized_rate("S1", raw_rate=None, observed_minutes=0, position_prior=0.0),
            0.0,
        )
        self.assertIsNone(
            stabilized_rate("S1", raw_rate=2.0, observed_minutes=90, position_prior=None)
        )
        self.assertIsNone(aggregate_position_rate(((None, 90),))[2])


class CausalPredictionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = duckdb.connect(":memory:")
        goal = 2.0 * 60 / 90 * 5
        assist = 1.0 * 60 / 90 * 3
        self.connection.execute(
            """CREATE TABLE development_base_fixture AS SELECT
               '2023-24'::VARCHAR season,3::INTEGER target_gameweek,
               300::INTEGER fixture_id,true target_has_fixture,1::INTEGER target_fixture_count,
               42::BIGINT element_id,1042::BIGINT code,'MID'::VARCHAR "position",
               1::INTEGER team_id,'Alpha'::VARCHAR team_name,2::INTEGER opponent_team_id,
               'H'::VARCHAR home_away,'2023-08-26T14:00:00Z'::TIMESTAMPTZ kickoff_time,
               '2023-08-25T18:00:00Z'::TIMESTAMPTZ target_deadline,
               60.0::DOUBLE expected_minutes_v01,90::INTEGER prior_total_minutes,
               1::INTEGER prior_gameweeks_with_data,2.0::DOUBLE prior_xg_per_90_used,
               1.0::DOUBLE prior_xa_per_90_used,2.0::DOUBLE appearance_xfp_v01,
               ?::DOUBLE goal_xfp_v01,?::DOUBLE assist_xfp_v01,
               ?::DOUBLE fixture_xfp_v01,5::INTEGER goal_points_for_position,
               true attacking_rate_available,true prediction_complete,
               'a'::VARCHAR availability_status,NULL::SMALLINT chance_of_playing_next_round,
               true availability_known_pre_deadline,false availability_forced_zero,
               NULL::VARCHAR availability_gate_reason,90::INTEGER previous_gameweek_minutes_uncapped,
               'played'::VARCHAR previous_gw_context_status,false previous_gw_team_blank,
               false previous_gw_player_not_in_universe,?::VARCHAR history_cutoff_rule,
               ?::VARCHAR historical_classification""",
            [goal, assist, 2.0 + goal + assist, HISTORY_CUTOFF_RULE, HISTORICAL_CLASSIFICATION],
        )
        self.connection.execute(
            """INSERT INTO development_base_fixture SELECT
               season,target_gameweek,NULL,false,0,43,1043,"position",team_id,team_name,
               NULL,NULL,NULL,target_deadline,60.0,90,1,2.0,1.0,2.0,?, ?, ?,5,true,true,
               availability_status,chance_of_playing_next_round,availability_known_pre_deadline,
               availability_forced_zero,availability_gate_reason,previous_gameweek_minutes_uncapped,
               previous_gw_context_status,true,previous_gw_player_not_in_universe,
               history_cutoff_rule,historical_classification
               FROM development_base_fixture WHERE element_id=42""",
            [goal, assist, 2.0 + goal + assist],
        )
        self.connection.execute(
            """CREATE TABLE development_base_player AS
               SELECT * FROM (VALUES
                 ('2023-24',3,42,60.0,2.0,?, ?, ?, 'available',1,80.0,2.0,0.0,0.0,2.0,2.0,'realized_fixture_rows'),
                 ('2023-24',3,43,0.0,0.0,0.0,0.0,0.0,'available',0,0.0,0.0,0.0,0.0,0.0,0.0,'verified_blank')
               ) t(season,target_gameweek,element_id,gameweek_expected_minutes_for_evaluation,
                   gameweek_appearance_xfp_for_evaluation,gameweek_goal_xfp_for_evaluation,
                   gameweek_assist_xfp_for_evaluation,gameweek_xfp_v01,availability_band,
                   actual_fixture_count,actual_minutes,actual_appearance_points,actual_goal_points,
                   actual_assist_points,actual_modeled_points,actual_full_fpl_points,actual_state)""",
            [goal, assist, 2.0 + goal + assist],
        )
        self.connection.execute(
            """CREATE TABLE development_history AS SELECT * FROM (VALUES
                 ('2023-24','MID',1,'2023-08-12T14:00:00Z'::TIMESTAMPTZ,1.0,0.5,90,101),
                 ('2023-24','MID',2,'2023-08-27T14:00:00Z'::TIMESTAMPTZ,999.0,999.0,90,201),
                 ('2023-24','MID',3,'2023-08-20T14:00:00Z'::TIMESTAMPTZ,999.0,999.0,90,301)
               ) t(season,historical_position,gameweek,kickoff_time,xg,xa,minutes,fixture_id)"""
        )
        self.connection.execute(
            "CREATE TABLE development_candidate_set(candidate) AS VALUES ('S0'),('S1'),('S2'),('S3')"
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_causal_aggregate_priors_and_candidate_rates(self) -> None:
        _create_phase_predictions(self.connection, phase="development")
        prior = self.connection.execute(
            """SELECT position_xg_numerator,position_xg_minutes,position_xg_per90,
                      position_xa_numerator,position_xa_minutes,position_xa_per90,
                      history_gameweek_max_used
               FROM development_position_priors WHERE "position"='MID'"""
        ).fetchone()
        self.assertEqual(prior, (1.0, 90.0, 1.0, 0.5, 90.0, 0.5, 1))
        rates = dict(self.connection.execute(
            """SELECT candidate,stabilized_xg_per90
               FROM development_candidate_fixture WHERE element_id=42"""
        ).fetchall())
        self.assertAlmostEqual(rates["S1"], (2 * 90 + 1 * 450) / 540)
        self.assertAlmostEqual(rates["S2"], (2 * 90 + 1 * 900) / 990)
        self.assertEqual(rates["S3"], 1.0)

    def test_target_later_postponed_and_outcomes_cannot_change_predictions(self) -> None:
        _create_phase_predictions(self.connection, phase="development")
        before = self.connection.execute(
            """SELECT candidate,stabilized_xg_per90,stabilized_xa_per90,
                      candidate_goal_xfp,candidate_assist_xfp,candidate_fixture_xfp
               FROM development_candidate_fixture WHERE element_id=42 ORDER BY candidate"""
        ).fetchall()
        self.connection.execute(
            "UPDATE development_history SET xg=888.0,xa=888.0 WHERE gameweek IN (2,3)"
        )
        self.connection.execute(
            "UPDATE development_base_player SET actual_goal_points=8.0,actual_assist_points=8.0,actual_modeled_points=8.0 WHERE element_id=42"
        )
        _create_phase_predictions(self.connection, phase="development")
        after = self.connection.execute(
            """SELECT candidate,stabilized_xg_per90,stabilized_xa_per90,
                      candidate_goal_xfp,candidate_assist_xfp,candidate_fixture_xfp
               FROM development_candidate_fixture WHERE element_id=42 ORDER BY candidate"""
        ).fetchall()
        self.assertEqual(after, before)

    def test_expected_minutes_availability_nonattacking_and_blank_are_frozen(self) -> None:
        _create_phase_predictions(self.connection, phase="development")
        invariants = self.connection.execute(
            """SELECT count(DISTINCT candidate_expected_minutes),
                      count(DISTINCT candidate_appearance_xfp),
                      count(DISTINCT availability_status)
               FROM development_candidate_fixture WHERE element_id=42"""
        ).fetchone()
        self.assertEqual(invariants, (1, 1, 1))
        blanks = self.connection.execute(
            """SELECT candidate,gameweek_xfp,actual_modeled_points
               FROM development_candidate_player WHERE element_id=43 ORDER BY candidate"""
        ).fetchall()
        self.assertEqual(blanks, [(candidate, 0.0, 0.0) for candidate in ("S0", "S1", "S2", "S3")])


def metric_row(candidate: str, coverage: float = 100.0):
    return ("development", "2023-24", candidate, "played", "modeled_xfp",
            100, int(coverage), 100-int(coverage), 0, coverage, 1.0, 2.0, 0.1, 0.5)


def common_row(comparison: str, predictor: str, target: str, *, qualifying: bool):
    if predictor == "S0":
        mae, rmse, bias, spearman = 1.0, 2.0, 0.10, 0.20
    elif target in ("played:goal", "played:assist"):
        mae, rmse, bias, spearman = 1.0, 2.0, 0.10, 0.22 if qualifying else 0.205
    else:
        mae = 1.005 if qualifying else 1.02
        rmse = 2.01 if qualifying else 2.04
        bias, spearman = (0.11, 0.21) if qualifying else (0.13, 0.19)
    return ("development", "2023-24", comparison, predictor, target,
            90, mae, rmse, bias, spearman)


class SelectionAndOutputTests(unittest.TestCase):
    def _rows(self, qualifying: bool):
        metrics = [metric_row(candidate) for candidate in ("S0", "S1", "S2", "S3")]
        common = []
        for candidate in ("S1", "S2", "S3"):
            for target in ("played:goal", "played:assist", "played:modeled_xfp"):
                common.append(common_row(candidate, "S0", target, qualifying=qualifying))
                common.append(common_row(candidate, candidate, target, qualifying=qualifying))
        return metrics, common

    def test_development_only_selection_and_one_winner_holdout(self) -> None:
        metrics, common = self._rows(True)
        winner, _ = select_development_winner(metrics, common)
        self.assertEqual(winner, "S1")
        self.assertEqual(_holdout_candidate_set(winner), ("S0", "S1"))
        contaminated = list(metrics)
        contaminated[0] = ("holdout", "2024-25", *contaminated[0][2:])
        with self.assertRaisesRegex(HistoricalAttackingRateExperimentError, "holdout"):
            select_development_winner(contaminated, common)

    def test_failed_development_prevents_holdout(self) -> None:
        metrics, common = self._rows(False)
        winner, _ = select_development_winner(metrics, common)
        self.assertIsNone(winner)
        with self.assertRaises(HistoricalAttackingRateExperimentError):
            _holdout_candidate_set("S0")

    def test_failed_holdout_prevents_promotion(self) -> None:
        metrics, common = self._rows(False)
        holdout_metrics = [("holdout", "2024-25", *row[2:]) for row in metrics if row[2] in ("S0", "S1")]
        holdout_common = [("holdout", "2024-25", *row[2:]) for row in common if row[2] == "S1"]
        self.assertFalse(_holdout_decision(holdout_metrics, holdout_common, "S1")["holdout_passed"])

    def test_completed_output_is_immutable(self) -> None:
        connection = duckdb.connect(":memory:")
        connection.execute("CREATE TABLE result AS SELECT 1 result_value")
        try:
            with TemporaryDirectory() as temporary:
                root=Path(temporary);(root/EXPERIMENT_VERSION).mkdir()
                with self.assertRaises(HistoricalAttackingRateExperimentOutputExistsError):
                    _write_outputs(connection,experiment_root=root,manifest_base={},tables=("result",))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
