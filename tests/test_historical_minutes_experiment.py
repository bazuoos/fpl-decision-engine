from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from fpl_decision_engine.historical import HISTORICAL_CLASSIFICATION, HISTORY_CUTOFF_RULE
from fpl_decision_engine.historical_minutes_experiment import (
    EXPERIMENT_VERSION,
    HistoricalMinutesExperimentError,
    HistoricalMinutesExperimentOutputExistsError,
    _common_pair_metrics,
    _create_phase_predictions,
    _holdout_candidate_set,
    _holdout_decision,
    _write_outputs,
    select_development_winner,
)


class CandidateWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = duckdb.connect(":memory:")
        self.connection.execute(
            """CREATE TABLE development_base_fixture AS SELECT
               '2023-24'::VARCHAR season,4::INTEGER target_gameweek,
               400::INTEGER fixture_id,true target_has_fixture,
               1::INTEGER target_fixture_count,42::BIGINT element_id,
               1042::BIGINT code,'MID'::VARCHAR "position",1::INTEGER team_id,
               'Alpha'::VARCHAR team_name,2::INTEGER opponent_team_id,
               'H'::VARCHAR home_away,
               '2023-09-02T14:00:00Z'::TIMESTAMPTZ kickoff_time,
               '2023-09-01T18:00:00Z'::TIMESTAMPTZ target_deadline,
               0.0::DOUBLE expected_minutes_v01,
               0::INTEGER previous_gameweek_minutes_uncapped,
               90::INTEGER prior_total_minutes,2::INTEGER prior_gameweeks_with_data,
               2::INTEGER prior_fixture_rows,3::INTEGER history_gameweek_max_used,
               '2023-08-26T14:00:00Z'::TIMESTAMPTZ history_latest_kickoff_used,
               ?::VARCHAR history_cutoff_rule,0.5::DOUBLE prior_xg_per_90_used,
               0.2::DOUBLE prior_xa_per_90_used,0.0::DOUBLE appearance_xfp_v01,
               0.0::DOUBLE goal_xfp_v01,0.0::DOUBLE assist_xfp_v01,
               0.0::DOUBLE fixture_xfp_v01,5::INTEGER goal_points_for_position,
               true attacking_rate_available,true prediction_complete,
               'a'::VARCHAR availability_status,
               NULL::SMALLINT chance_of_playing_next_round,
               true availability_known_pre_deadline,false availability_forced_zero,
               'fixture_existed_zero_minutes'::VARCHAR previous_gw_context_status,
               false previous_gw_team_blank,false previous_gw_player_not_in_universe,
               ?::VARCHAR historical_classification""",
            [HISTORY_CUTOFF_RULE, HISTORICAL_CLASSIFICATION],
        )
        self.connection.execute(
            """CREATE TABLE development_base_player AS SELECT
               '2023-24'::VARCHAR season,4::INTEGER target_gameweek,42::BIGINT element_id,
               0.0::DOUBLE gameweek_expected_minutes_for_evaluation,
               0.0::DOUBLE gameweek_appearance_xfp_for_evaluation,
               0.0::DOUBLE gameweek_xfp_v01,'available'::VARCHAR availability_band,
               1::BIGINT actual_fixture_count,80.0::DOUBLE actual_minutes,
               2.0::DOUBLE actual_appearance_points,0.0::DOUBLE actual_goal_points,
               0.0::DOUBLE actual_assist_points,2.0::DOUBLE actual_modeled_points,
               2.0::DOUBLE actual_full_fpl_points,'realized_fixture_rows'::VARCHAR actual_state"""
        )
        self.connection.execute(
            """CREATE TABLE development_history AS
               SELECT * FROM (VALUES
                 ('2023-24',42,1,90,'2023-08-12T14:00:00Z'::TIMESTAMPTZ),
                 ('2023-24',42,2,60,'2023-09-03T14:00:00Z'::TIMESTAMPTZ),
                 ('2023-24',42,3,0,'2023-08-26T14:00:00Z'::TIMESTAMPTZ),
                 ('2023-24',42,4,999,'2023-08-27T14:00:00Z'::TIMESTAMPTZ)
               ) t(season,element_id,gameweek,minutes,kickoff_time)"""
        )
        self.connection.execute(
            """CREATE TABLE development_feature_windows AS SELECT
               '2023-24'::VARCHAR season,4::INTEGER target_gameweek,42::BIGINT element_id,
               '2023-09-01T18:00:00Z'::TIMESTAMPTZ target_deadline,
               2::INTEGER rolling_3_gameweeks_with_data,90::INTEGER rolling_3_minutes,
               2::INTEGER rolling_5_gameweeks_with_data,90::INTEGER rolling_5_minutes"""
        )
        self.connection.execute(
            """CREATE TABLE development_source_universe AS
               SELECT * FROM (VALUES
                 ('2023-24',1,42,1),('2023-24',2,42,1),('2023-24',3,42,1)
               ) t(season,target_gameweek,element_id,team_id)"""
        )
        self.connection.execute(
            """CREATE TABLE development_fixtures AS
               SELECT * FROM (VALUES
                 ('2023-24',101,1,1,2,'2023-08-12T14:00:00Z'::TIMESTAMPTZ),
                 ('2023-24',301,3,1,2,'2023-08-26T14:00:00Z'::TIMESTAMPTZ)
               ) t(season,fixture_id,gameweek,home_team_id,away_team_id,kickoff_time)"""
        )
        self.connection.execute(
            "CREATE TABLE development_candidate_set(candidate) AS VALUES ('M0'),('M1'),('M2'),('M3')"
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_blank_is_missing_real_zero_remains_and_weights_are_exact(self) -> None:
        _create_phase_predictions(
            self.connection, phase="development", season="2023-24"
        )
        rows = self.connection.execute(
            """SELECT candidate,candidate_observed_gameweeks,
                      candidate_minutes_before_cap_gate,candidate_expected_minutes
               FROM development_candidate_fixture ORDER BY candidate"""
        ).fetchall()
        self.assertEqual(rows[0], ("M0", 1, 0.0, 0.0))
        self.assertEqual(rows[1][:2], ("M1", 2))
        self.assertAlmostEqual(rows[1][2], 45.0)  # GW2 blank absent; GW3 zero retained.
        self.assertAlmostEqual(rows[2][2], 45.0)
        # (90*0.10 + 0*0.60) / (0.10+0.60)
        self.assertAlmostEqual(rows[3][2], 90.0 / 7.0)
        observed = self.connection.execute(
            """SELECT source_gameweek,observed_minutes FROM development_minute_observations
               ORDER BY source_gameweek"""
        ).fetchall()
        self.assertEqual(observed, [(1, 90.0), (3, 0.0)])

    def test_lower_event_after_deadline_and_target_gameweek_cannot_leak(self) -> None:
        _create_phase_predictions(
            self.connection, phase="development", season="2023-24"
        )
        before = self.connection.execute(
            """SELECT candidate,candidate_expected_minutes,candidate_fixture_xfp
               FROM development_candidate_fixture ORDER BY candidate"""
        ).fetchall()
        self.connection.execute(
            "UPDATE development_history SET minutes=999999 WHERE gameweek IN (2,4)"
        )
        self.connection.execute(
            "UPDATE development_base_player SET actual_minutes=999999,actual_modeled_points=999999"
        )
        _create_phase_predictions(
            self.connection, phase="development", season="2023-24"
        )
        after = self.connection.execute(
            """SELECT candidate,candidate_expected_minutes,candidate_fixture_xfp
               FROM development_candidate_fixture ORDER BY candidate"""
        ).fetchall()
        self.assertEqual(after, before)

    def test_double_gameweek_minutes_are_summed_before_window(self) -> None:
        self.connection.execute(
            """INSERT INTO development_history VALUES
               ('2023-24',42,3,30,'2023-08-27T14:00:00Z'::TIMESTAMPTZ)"""
        )
        self.connection.execute(
            """INSERT INTO development_fixtures VALUES
               ('2023-24',302,3,2,1,'2023-08-27T14:00:00Z'::TIMESTAMPTZ)"""
        )
        self.connection.execute(
            """UPDATE development_feature_windows
               SET rolling_3_minutes=120,rolling_5_minutes=120"""
        )
        _create_phase_predictions(
            self.connection, phase="development", season="2023-24"
        )
        observation = self.connection.execute(
            """SELECT observed_minutes,observed_fixture_count
               FROM development_minute_observations WHERE source_gameweek=3"""
        ).fetchone()
        self.assertEqual(observation, (30.0, 2))
        m1 = self.connection.execute(
            """SELECT candidate_minutes_before_cap_gate
               FROM development_candidate_fixture WHERE candidate='M1'"""
        ).fetchone()[0]
        self.assertAlmostEqual(m1, 60.0)

    def test_player_not_in_source_universe_is_missing_not_zero(self) -> None:
        self.connection.execute(
            "DELETE FROM development_source_universe WHERE target_gameweek=1"
        )
        self.connection.execute(
            """UPDATE development_feature_windows SET
               rolling_3_gameweeks_with_data=1,rolling_3_minutes=0,
               rolling_5_gameweeks_with_data=1,rolling_5_minutes=0"""
        )
        _create_phase_predictions(
            self.connection, phase="development", season="2023-24"
        )
        observed = self.connection.execute(
            """SELECT source_gameweek,observed_minutes FROM development_minute_observations
               ORDER BY source_gameweek"""
        ).fetchall()
        self.assertEqual(observed, [(3, 0.0)])
        m1 = self.connection.execute(
            """SELECT candidate_observed_gameweeks,candidate_minutes_before_cap_gate
               FROM development_candidate_fixture WHERE candidate='M1'"""
        ).fetchone()
        self.assertEqual(m1, (1, 0.0))

    def test_missing_player_row_for_real_team_fixture_is_corrupt_not_blank(self) -> None:
        self.connection.execute("DELETE FROM development_history WHERE gameweek=3")
        with self.assertRaisesRegex(HistoricalMinutesExperimentError, "missing/corrupt"):
            _create_phase_predictions(
                self.connection, phase="development", season="2023-24"
            )


def metric_row(candidate: str, target: str, mae: float, rmse: float, coverage: float = 100.0):
    return (
        "development", "2023-24", candidate, "all", target,
        100, int(coverage), 100 - int(coverage), 0, coverage,
        mae, rmse, 0.0, 0.5,
    )


def common_row(comparison: str, predictor: str, target: str, mae: float, rmse: float):
    return (
        "development", "2023-24", comparison, predictor, target,
        90, mae, rmse, 0.0, 0.5,
    )


class DevelopmentSelectionTests(unittest.TestCase):
    def _rows(self, qualifying: bool = True):
        metrics = []
        common = []
        for candidate in ("M0", "M1", "M2", "M3"):
            for target in ("minutes", "appearance", "modeled_xfp"):
                metrics.append(metric_row(candidate, target, 1.0, 1.0))
        for candidate in ("M1", "M2", "M3"):
            for target in ("minutes", "appearance", "modeled_xfp"):
                base_mae, base_rmse = (20.0, 30.0) if target == "minutes" else (2.0, 3.0)
                common.append(common_row(candidate, "M0", target, base_mae, base_rmse))
                if qualifying:
                    candidate_mae = 18.0 if target == "minutes" else 1.9
                    candidate_rmse = 28.5 if target == "minutes" else 2.9
                else:
                    candidate_mae, candidate_rmse = base_mae, base_rmse
                common.append(
                    common_row(candidate, candidate, target, candidate_mae, candidate_rmse)
                )
        return metrics, common

    def test_development_only_selection_and_deterministic_tie_break(self) -> None:
        metrics, common = self._rows()
        winner, records = select_development_winner(metrics, common)
        self.assertEqual(winner, "M1")
        self.assertTrue(all(record["development_qualifies"] for record in records))
        self.assertEqual(_holdout_candidate_set(winner), ("M0", "M1"))

        contaminated = list(metrics)
        contaminated[0] = ("holdout", "2024-25", *contaminated[0][2:])
        with self.assertRaisesRegex(HistoricalMinutesExperimentError, "holdout"):
            select_development_winner(contaminated, common)

    def test_no_development_winner_stops_holdout(self) -> None:
        metrics, common = self._rows(qualifying=False)
        winner, _ = select_development_winner(metrics, common)
        self.assertIsNone(winner)
        with self.assertRaises(HistoricalMinutesExperimentError):
            _holdout_candidate_set("M0")

    def test_holdout_failure_does_not_pass_promotion_gate(self) -> None:
        metrics, common = self._rows(qualifying=False)
        holdout_metrics = [
            ("holdout", "2024-25", *row[2:]) for row in metrics
            if row[2] in ("M0", "M1")
        ]
        holdout_common = [
            ("holdout", "2024-25", *row[2:]) for row in common
            if row[2] == "M1"
        ]
        result = _holdout_decision(holdout_metrics, holdout_common, "M1")
        self.assertFalse(result["holdout_passed"])

    def test_common_pairs_exclude_missing_candidate_predictions(self) -> None:
        rows = [
            {"candidate": "M0", "target_gameweek": 2, "element_id": 1,
             "gameweek_expected_minutes": 90.0, "actual_minutes": 60.0,
             "gameweek_appearance_xfp": 2.0, "actual_appearance_points": 2.0,
             "gameweek_xfp": 2.0, "actual_modeled_points": 2.0},
            {"candidate": "M1", "target_gameweek": 2, "element_id": 1,
             "gameweek_expected_minutes": None, "actual_minutes": 60.0,
             "gameweek_appearance_xfp": None, "actual_appearance_points": 2.0,
             "gameweek_xfp": None, "actual_modeled_points": 2.0},
        ]
        common = _common_pair_metrics(
            rows, phase="development", season="2023-24", candidates=("M0", "M1")
        )
        self.assertTrue(all(row[5] == 0 for row in common))

    def test_completed_experiment_is_not_overwritten(self) -> None:
        connection = duckdb.connect(":memory:")
        connection.execute("CREATE TABLE result AS SELECT 1 AS result_value")
        try:
            with TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / EXPERIMENT_VERSION).mkdir()
                with self.assertRaises(HistoricalMinutesExperimentOutputExistsError):
                    _write_outputs(
                        connection, experiment_root=root,
                        manifest_base={}, tables=("result",),
                    )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
