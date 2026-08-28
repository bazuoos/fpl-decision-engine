from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from pathlib import Path

import duckdb

from fpl_decision_engine.historical import (
    HISTORICAL_CLASSIFICATION,
    HISTORY_CUTOFF_RULE,
)
from fpl_decision_engine.historical_backtest import MODEL_IDENTIFIER
from fpl_decision_engine.historical_previous_season_prior_experiment import (
    CANDIDATES,
    DEVELOPMENT_SEASON,
    EXPERIMENT_VERSION,
    GATE_SCHEMA,
    PSEUDO_MINUTES,
    SEALED_HOLDOUT_SEASON,
    HistoricalPreviousSeasonPriorOutputExistsError,
    _create_candidate_predictions,
    _prior_reason,
    _write_outputs,
    blend_previous_season_rate,
    evaluate_development_gates,
)
from tests.fixture_support import materialized_task018d_fixture


class PriorFormulaAndEligibilityTests(unittest.TestCase):
    def test_exact_fixed_450_minute_blend(self) -> None:
        self.assertEqual(PSEUDO_MINUTES, 450.0)
        actual = blend_previous_season_rate(
            current_event=0.2,
            current_minutes=90,
            previous_event=4.0,
            previous_minutes=900,
        )
        previous_rate = 90 * 4.0 / 900
        expected = 90 * (0.2 + 450 * previous_rate / 90) / (90 + 450)
        self.assertAlmostEqual(actual, expected)

    def _target(self, **changes):
        value = {
            "element_id": 42,
            "code": 1001,
            "position": "MID",
            "target_code_count": 1,
            "target_position_count": 1,
        }
        value.update(changes)
        return value

    def _prior(self, **changes):
        value = {
            "element_id": 777,
            "identity_count": 1,
            "position": "MID",
            "minutes": 900,
            "source_rows": 38,
            "xg": 4.0,
            "xa": 3.0,
            "null_xg_rows": 0,
            "null_xa_rows": 0,
            "nonfinite_xg_rows": 0,
            "nonfinite_xa_rows": 0,
            "exception_rows": 0,
            "club_count": 2,
        }
        value.update(changes)
        return value

    def test_join_is_code_based_and_team_transfer_remains_eligible(self) -> None:
        target = self._target(element_id=42)
        prior = self._prior(element_id=999, club_count=3)
        self.assertEqual(_prior_reason(target, prior), "eligible")

    def test_position_change_and_low_minutes_fall_back(self) -> None:
        self.assertEqual(
            _prior_reason(self._target(), self._prior(position="FWD")),
            "position_mismatch",
        )
        self.assertEqual(
            _prior_reason(self._target(), self._prior(minutes=449)),
            "previous_season_minutes_below_450",
        )
        self.assertEqual(
            _prior_reason(self._target(), self._prior(minutes=0)),
            "zero_previous_season_minutes",
        )
        self.assertEqual(
            _prior_reason(self._target(), None),
            "no_previous_season_code_match",
        )

    def test_missing_and_nonfinite_events_fail_closed(self) -> None:
        cases = (
            ({"xg": None, "null_xg_rows": 1}, "missing_previous_season_xg"),
            ({"xg": math.inf, "nonfinite_xg_rows": 1}, "nonfinite_previous_season_xg"),
            ({"xa": None, "null_xa_rows": 1}, "missing_previous_season_xa"),
            ({"xa": math.nan, "nonfinite_xa_rows": 1}, "nonfinite_previous_season_xa"),
        )
        for changes, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(
                    _prior_reason(self._target(), self._prior(**changes)), expected
                )

    def test_identity_anomalies_fail_closed(self) -> None:
        self.assertEqual(
            _prior_reason(self._target(target_code_count=2), self._prior()),
            "target_code_collision",
        )
        self.assertEqual(
            _prior_reason(self._target(), self._prior(identity_count=2)),
            "previous_season_code_collision",
        )
        self.assertEqual(
            _prior_reason(self._target(), self._prior(exception_rows=1)),
            "previous_season_source_anomaly",
        )


class CandidatePredictionRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = duckdb.connect(":memory:")
        self.connection.execute(
            """CREATE TABLE development_c0_fixture AS SELECT
              '2024-25'::VARCHAR season,2::INTEGER target_gameweek,
              10::INTEGER fixture_id,true target_has_fixture,1::INTEGER target_fixture_count,
              42::BIGINT element_id,1001::BIGINT code,'MID'::VARCHAR "position",
              1::INTEGER team_id,'Alpha'::VARCHAR team_name,2::INTEGER opponent_team_id,
              'H'::VARCHAR home_away,'2024-08-24T14:00:00Z'::TIMESTAMPTZ kickoff_time,
              '2024-08-23T18:00:00Z'::TIMESTAMPTZ target_deadline,
              60.0::DOUBLE expected_minutes_v01,2.0::DOUBLE appearance_xfp_v01,
              90::INTEGER prior_total_minutes,0.2::DOUBLE cumulative_prior_xg,
              0.1::DOUBLE cumulative_prior_xa,0.2::DOUBLE prior_xg_per_90_used,
              0.1::DOUBLE prior_xa_per_90_used,5::INTEGER goal_points_for_position,
              (0.2*60/90*5)::DOUBLE goal_xfp_v01,
              (0.1*60/90*3)::DOUBLE assist_xfp_v01,
              (2+0.2*60/90*5+0.1*60/90*3)::DOUBLE fixture_xfp_v01,
              true prediction_complete,true attacking_rate_available,'a'::VARCHAR availability_status,
              NULL::SMALLINT chance_of_playing_next_round,true availability_known_pre_deadline,
              false availability_forced_zero,NULL::VARCHAR availability_gate_reason,
              90::INTEGER previous_gameweek_minutes_uncapped,'played'::VARCHAR previous_gw_context_status,
              false previous_gw_team_blank,false previous_gw_player_not_in_universe,
              ?::VARCHAR history_cutoff_rule,?::VARCHAR historical_classification""",
            [HISTORY_CUTOFF_RULE, HISTORICAL_CLASSIFICATION],
        )
        self.connection.execute(
            """INSERT INTO development_c0_fixture SELECT
              season,target_gameweek,11,true,1,43,1002,"position",team_id,team_name,
              opponent_team_id,home_away,kickoff_time,target_deadline,
              30.0,1.0,0,NULL,NULL,NULL,NULL,goal_points_for_position,
              NULL,NULL,1.0,false,false,availability_status,
              chance_of_playing_next_round,availability_known_pre_deadline,
              availability_forced_zero,availability_gate_reason,0,
              previous_gw_context_status,previous_gw_team_blank,
              previous_gw_player_not_in_universe,history_cutoff_rule,
              historical_classification FROM development_c0_fixture"""
        )
        self.connection.execute(
            """CREATE TABLE player_prior_eligibility AS SELECT
              42::BIGINT element_id,true eligible,'eligible'::VARCHAR eligibility_reason,
              900::BIGINT prior_element_id,'MID'::VARCHAR prior_position,
              900::BIGINT prior_minutes,4.0::DOUBLE prior_xg,3.0::DOUBLE prior_xa,
              0.4::DOUBLE prior_xg_per_90,0.3::DOUBLE prior_xa_per_90,
              2::INTEGER prior_club_count
              UNION ALL SELECT 43,true,'eligible',901,'MID',900,4.0,3.0,0.4,0.3,1"""
        )
        goal = 0.2 * 60 / 90 * 5
        assist = 0.1 * 60 / 90 * 3
        self.connection.execute(
            """CREATE TABLE player_gameweek AS SELECT * FROM (VALUES
              ('2024-25',2,42,60.0,2.0,?,?,?,true,1,90,2,5,3,10,12,'actual_available'),
              ('2024-25',2,43,30.0,1.0,NULL,NULL,1.0,false,1,0,0,0,0,0,0,'actual_zero_minutes')
              ) t(season,target_gameweek,element_id,
                  gameweek_expected_minutes_for_evaluation,
                  gameweek_appearance_xfp_for_evaluation,
                  gameweek_goal_xfp_for_evaluation,
                  gameweek_assist_xfp_for_evaluation,gameweek_xfp_v01,
                  prediction_complete,actual_fixture_count,actual_minutes,
                  actual_appearance_points,actual_goal_points,actual_assist_points,
                  actual_modeled_points,actual_full_fpl_points,actual_state)""",
            [goal, assist, 2 + goal + assist],
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_c1_changes_only_complete_attacking_rates(self) -> None:
        _create_candidate_predictions(self.connection)
        complete = self.connection.execute(
            """SELECT candidate,candidate_expected_minutes,candidate_appearance_xfp,
                      candidate_xg_per_90,candidate_xa_per_90,prediction_complete,
                      prior_applied,prior_element_id,prior_club_count
               FROM development_candidate_fixture WHERE element_id=42 ORDER BY candidate"""
        ).fetchall()
        self.assertEqual(complete[0][0:3], ("C0", 60.0, 2.0))
        self.assertEqual(complete[1][0:3], ("C1", 60.0, 2.0))
        self.assertAlmostEqual(
            complete[1][3],
            blend_previous_season_rate(
                current_event=0.2,
                current_minutes=90,
                previous_event=4.0,
                previous_minutes=900,
            ),
        )
        self.assertTrue(complete[1][6])
        self.assertEqual(complete[1][7:], (900, 2))

        incomplete = self.connection.execute(
            """SELECT candidate,candidate_xg_per_90,candidate_xa_per_90,
                      candidate_fixture_xfp,prediction_complete,prior_applied,
                      prior_application_status
               FROM development_candidate_fixture WHERE element_id=43 ORDER BY candidate"""
        ).fetchall()
        self.assertEqual(incomplete[0][1:4], incomplete[1][1:4])
        self.assertFalse(incomplete[0][4])
        self.assertFalse(incomplete[1][4])
        self.assertFalse(incomplete[1][5])
        self.assertEqual(
            incomplete[1][6], "current_attacking_rate_incomplete"
        )

    def test_actual_outcomes_cannot_change_predictions_and_runs_are_deterministic(self) -> None:
        _create_candidate_predictions(self.connection)
        before = self.connection.execute(
            """SELECT candidate,element_id,candidate_expected_minutes,
                      candidate_xg_per_90,candidate_xa_per_90,candidate_fixture_xfp
               FROM development_candidate_fixture ORDER BY candidate,element_id"""
        ).fetchall()
        self.connection.execute(
            """UPDATE player_gameweek SET actual_goal_points=999,
                 actual_assist_points=999,actual_modeled_points=1998"""
        )
        self.connection.execute("DROP TABLE development_candidate_player_gameweek")
        self.connection.execute("DROP TABLE development_candidate_fixture")
        _create_candidate_predictions(self.connection)
        after = self.connection.execute(
            """SELECT candidate,element_id,candidate_expected_minutes,
                      candidate_xg_per_90,candidate_xa_per_90,candidate_fixture_xfp
               FROM development_candidate_fixture ORDER BY candidate,element_id"""
        ).fetchall()
        self.assertEqual(after, before)


def _common_row(scope: str, target: str, *, passing: bool) -> tuple:
    c0_mae, c0_rmse, c0_bias, c0_spearman = 1.0, 2.0, 0.10, 0.20
    c1_mae, c1_rmse, c1_bias, c1_spearman = 0.9, 1.9, 0.11, 0.225
    if scope in ("GW6-10", "GW11+"):
        c1_mae, c1_rmse, c1_spearman = 1.005, 2.01, 0.195
    if not passing and scope == "GW2-5" and target == "modeled_xfp":
        c1_mae = 1.01
    return (
        "overall" if scope == "overall" else "gameweek_period",
        scope,
        "played_common_pair",
        target,
        100,
        c0_mae,
        c1_mae,
        100 * (c0_mae - c1_mae) / c0_mae,
        c0_rmse,
        c1_rmse,
        100 * (c0_rmse - c1_rmse) / c0_rmse,
        c0_bias,
        c1_bias,
        abs(c1_bias) - abs(c0_bias),
        c0_spearman,
        c1_spearman,
        c1_spearman - c0_spearman,
    )


class GateAndOutputTests(unittest.TestCase):
    def _rows(self, passing: bool = True):
        common = [
            _common_row(scope, target, passing=passing)
            for scope in ("overall", "GW2-5", "GW6-10", "GW11+")
            for target in ("goal", "assist", "attacking_combined", "modeled_xfp")
        ]
        coverage = []
        for candidate in CANDIDATES:
            coverage.append(
                (
                    "overall",
                    "overall",
                    "played",
                    "modeled_xfp",
                    candidate,
                    100,
                    100,
                    0,
                    0,
                    100.0,
                    0,
                )
            )
        return common, coverage

    def test_all_preregistered_gates_and_undefined_spearman_fails(self) -> None:
        common, coverage = self._rows()
        gates, passed = evaluate_development_gates(common, coverage)
        self.assertEqual(len(gates), 21)
        self.assertEqual(len(GATE_SCHEMA), len(gates[0]))
        self.assertTrue(passed)
        changed = list(common)
        row = list(changed[5])
        row[15] = None
        row[16] = None
        changed[5] = tuple(row)
        gates, passed = evaluate_development_gates(changed, coverage)
        self.assertFalse(passed)
        self.assertTrue(any(not row[7] for row in gates))

    def test_completed_experiment_is_not_overwritten(self) -> None:
        connection = duckdb.connect(":memory:")
        connection.execute("CREATE TABLE result AS SELECT 1 AS result_value")
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / EXPERIMENT_VERSION).mkdir()
                with self.assertRaises(HistoricalPreviousSeasonPriorOutputExistsError):
                    _write_outputs(
                        connection,
                        experiment_root=root,
                        manifest_base={},
                        tables=("result",),
                    )
        finally:
            connection.close()

    def test_holdout_constant_is_not_a_development_candidate(self) -> None:
        self.assertEqual(SEALED_HOLDOUT_SEASON, "2025-26")
        self.assertEqual(DEVELOPMENT_SEASON, "2024-25")
        self.assertNotIn(SEALED_HOLDOUT_SEASON, CANDIDATES)


class DevelopmentArtifactTests(unittest.TestCase):
    """Validate reviewed development metadata without copying metric tables."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_manager = materialized_task018d_fixture()
        cls.metadata_path, cls.manifest_path = cls.fixture_manager.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture_manager.__exit__(None, None, None)

    def test_reviewed_metadata_is_development_only_and_synthetic_hashes_are_valid(self) -> None:
        """Use real manifest metadata with metric-free temporary output bodies."""
        metadata = json.loads(self.metadata_path.read_bytes())
        manifest = json.loads(self.manifest_path.read_bytes())
        root = self.manifest_path.parent
        self.assertEqual(
            hashlib.sha256(self.metadata_path.read_bytes()).hexdigest(),
            "4c2f427f9baabc731433bf0448284dcdf6d914f9baf14a65093f8979261bae4a",
        )
        self.assertEqual(metadata["experiment_scope"], "development_only")
        self.assertFalse(metadata["holdout_evaluated"])
        self.assertEqual(metadata["holdout_input_files_read"], [])
        self.assertEqual(metadata["pseudo_minutes"], 450.0)
        self.assertEqual(metadata["development_gate_count"], 21)
        self.assertTrue(
            all("2025-26" not in row["path"] for row in metadata["immutable_inputs"])
        )
        for output in manifest["outputs"]:
            body = (root / output["path"]).read_bytes()
            self.assertEqual(
                body,
                f"test-only synthetic output: {output['path']}\n".encode(),
            )
            self.assertEqual(output["rows"], 0)
            digest = hashlib.sha256(body).hexdigest()
            self.assertEqual(digest, output["sha256"])


if __name__ == "__main__":
    unittest.main()
