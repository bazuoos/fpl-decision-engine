from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from fpl_decision_engine.historical import HISTORICAL_CLASSIFICATION, HISTORY_CUTOFF_RULE
from fpl_decision_engine.historical_backtest import (
    HistoricalBacktestError,
    _create_rows_table,
    _create_scored_tables,
    _metric_row,
    _ranking_rows,
    _validate_scored_tables,
    _write_backtest_outputs,
)


FEATURE_SCHEMA = (
    ("season", "VARCHAR"), ("target_gameweek", "INTEGER"),
    ("element_id", "BIGINT"), ("code", "BIGINT"),
    ("historical_position", "VARCHAR"), ("snapshot_team_id", "INTEGER"),
    ("snapshot_team_name", "VARCHAR"), ("target_fixture_id", "INTEGER"),
    ("target_fixture_count", "INTEGER"), ("target_opponent_team_id", "INTEGER"),
    ("target_home_away", "VARCHAR"), ("target_kickoff_time", "TIMESTAMPTZ"),
    ("target_deadline", "TIMESTAMPTZ"), ("snapshot_timestamp", "TIMESTAMPTZ"),
    ("availability_status", "VARCHAR"),
    ("chance_of_playing_next_round", "SMALLINT"),
    ("availability_news", "VARCHAR"),
    ("availability_known_pre_deadline", "BOOLEAN"),
    ("prior_gameweeks_with_data", "INTEGER"), ("prior_fixture_rows", "INTEGER"),
    ("chronologically_excluded_prior_fixture_rows", "INTEGER"),
    ("prior_total_minutes", "INTEGER"),
    ("previous_gameweek_minutes_uncapped", "INTEGER"),
    ("previous_gw_context_status", "VARCHAR"),
    ("previous_gw_team_blank", "BOOLEAN"),
    ("previous_gw_player_not_in_universe", "BOOLEAN"),
    ("prior_xg_per_90", "DOUBLE"), ("prior_xa_per_90", "DOUBLE"),
    ("history_gameweek_max_used", "INTEGER"),
    ("history_latest_kickoff_used", "TIMESTAMPTZ"),
    ("history_cutoff_rule", "VARCHAR"),
    ("historical_classification", "VARCHAR"),
    ("predeadline_source_path", "VARCHAR"),
    ("predeadline_source_sha256", "VARCHAR"),
)

ACTUAL_SCHEMA = (
    ("season", "VARCHAR"), ("gameweek", "INTEGER"),
    ("element_id", "BIGINT"), ("fixture_id", "INTEGER"),
    ("historical_position", "VARCHAR"), ("historical_team_id", "INTEGER"),
    ("opponent_team_id", "INTEGER"), ("home_away", "VARCHAR"),
    ("kickoff_time", "TIMESTAMPTZ"), ("minutes", "INTEGER"),
    ("actual_appearance_points_v01", "INTEGER"), ("goals", "INTEGER"),
    ("assists", "INTEGER"), ("total_points", "INTEGER"),
    ("xg", "DOUBLE"), ("xa", "DOUBLE"),
    ("source_path", "VARCHAR"), ("source_sha256", "VARCHAR"),
    ("source_row_number", "BIGINT"),
)

STATE_SCHEMA = (
    ("season", "VARCHAR"), ("target_gameweek", "INTEGER"),
    ("element_id", "BIGINT"), ("ep_next", "DOUBLE"),
)


class FrozenHistoricalBacktestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = duckdb.connect(":memory:")
        deadline = "2023-08-18T18:00:00Z"
        snapshot = "2023-08-18T12:00:00Z"
        prior_kickoff = "2023-08-12T14:00:00Z"
        target_kickoff = "2023-08-19T14:00:00Z"

        def feature(
            element, fixture, fixture_count, position, previous_minutes,
            xg_rate, xa_rate, status="a", chance=None, excluded=0,
        ):
            return (
                "2023-24", 2, element, 1000 + element, position, 1, "Alpha",
                fixture, fixture_count, 2 if fixture is not None else None,
                "H" if fixture is not None else None,
                target_kickoff if fixture is not None else None,
                deadline, snapshot, status, chance, "", True, 1, 1, excluded,
                90 if previous_minutes is not None else 0, previous_minutes,
                "played" if previous_minutes is not None else "verified_team_blank",
                previous_minutes is None, False, xg_rate, xa_rate, 1,
                prior_kickoff if previous_minutes is not None else None,
                HISTORY_CUTOFF_RULE, HISTORICAL_CLASSIFICATION,
                "snapshot.json.xz", "a" * 64,
            )

        features = [
            feature(1, 101, 1, "MID", 60, 0.9, 0.45),
            feature(2, None, 0, "DEF", None, None, None),
            feature(3, 201, 2, "DEF", 90, 1.0, 0.5),
            feature(3, 202, 2, "DEF", 90, 1.0, 0.5),
            feature(4, 401, 1, "FWD", 90, None, None),
            feature(5, 501, 1, "MID", 90, 1.0, 1.0, status="u"),
        ]
        actuals = [
            ("2023-24", 2, 1, 101, "FWD", 1, 2, "H", target_kickoff,
             90, 2, 1, 1, 10, 999.0, 999.0, "merged.csv", "b" * 64, 2),
            ("2023-24", 2, 3, 201, "DEF", 1, 2, "H", target_kickoff,
             60, 2, 1, 0, 8, 0.1, 0.0, "merged.csv", "b" * 64, 3),
            ("2023-24", 2, 3, 202, "DEF", 1, 2, "A", target_kickoff,
             30, 1, 0, 1, 4, 0.0, 0.1, "merged.csv", "b" * 64, 4),
            ("2023-24", 2, 4, 401, "FWD", 1, 2, "H", target_kickoff,
             0, 0, 0, 0, 0, None, None, "merged.csv", "b" * 64, 5),
            ("2023-24", 2, 5, 501, "MID", 1, 2, "H", target_kickoff,
             0, 0, 0, 0, 0, 0.0, 0.0, "merged.csv", "b" * 64, 6),
            # Lower event number but chronologically after the target deadline.
            ("2023-24", 1, 1, 99, "MID", 1, 2, "H", "2023-08-20T14:00:00Z",
             90, 2, 999, 999, 999, 999.0, 999.0, "merged.csv", "b" * 64, 7),
            # Later-GW result must not affect GW2 predictions.
            ("2023-24", 3, 1, 301, "MID", 1, 2, "H", "2023-08-26T14:00:00Z",
             90, 2, 999, 999, 999, 999.0, 999.0, "merged.csv", "b" * 64, 8),
        ]
        states = [("2023-24", 2, element, 2.0) for element in range(1, 6)]
        _create_rows_table(self.connection, "historical_features", FEATURE_SCHEMA, features)
        _create_rows_table(
            self.connection, "historical_actual_fixture", ACTUAL_SCHEMA, actuals
        )
        _create_rows_table(
            self.connection, "historical_predeadline", STATE_SCHEMA, states
        )
        _create_scored_tables(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def _drop_scored(self) -> None:
        for table in (
            "fixture_predictions", "gameweek_predictions", "fixture_actuals",
            "gameweek_actuals", "player_gameweek",
        ):
            self.connection.execute(f"DROP TABLE {table}")

    def _predictions(self):
        return self.connection.execute(
            """SELECT element_id,fixture_id,expected_minutes_v01,
                      expected_goals_v01,expected_assists_v01,fixture_xfp_v01
               FROM fixture_predictions ORDER BY element_id,fixture_id"""
        ).fetchall()

    def test_target_postponed_and_later_actuals_cannot_change_predictions(self) -> None:
        before = self._predictions()
        self.connection.execute(
            """UPDATE historical_actual_fixture
               SET goals=777,assists=777,total_points=777,xg=777,xa=777
               WHERE gameweek IN (1,2,3)"""
        )
        self._drop_scored()
        _create_scored_tables(self.connection)
        self.assertEqual(self._predictions(), before)

    def test_frozen_status_position_missingness_blank_and_dgw_semantics(self) -> None:
        _validate_scored_tables(self.connection)
        player_one = self.connection.execute(
            """SELECT gameweek_xfp_v01,actual_goal_points,actual_modeled_points
               FROM player_gameweek WHERE element_id=1"""
        ).fetchone()
        self.assertAlmostEqual(player_one[0], 5.9)
        self.assertEqual(player_one[1:], (5, 10))  # Frozen MID, archived row says FWD.

        blank = self.connection.execute(
            """SELECT gameweek_xfp_v01,actual_modeled_points,actual_state
               FROM player_gameweek WHERE element_id=2"""
        ).fetchone()
        self.assertEqual(blank, (0.0, 0, "verified_blank"))

        double = self.connection.execute(
            """SELECT fixture_count,gameweek_xfp_v01,actual_fixture_count,
                      actual_minutes,actual_modeled_points
               FROM player_gameweek WHERE element_id=3"""
        ).fetchone()
        self.assertEqual(double[0], 2)
        self.assertAlmostEqual(double[1], 19.0)
        self.assertEqual(double[2:], (2, 90, 12))

        missing = self.connection.execute(
            """SELECT expected_goals_v01,expected_assists_v01,prediction_complete,
                      fixture_xfp_v01
               FROM fixture_predictions WHERE element_id=4"""
        ).fetchone()
        self.assertEqual(missing, (None, None, False, 2.0))

        unavailable = self.connection.execute(
            """SELECT expected_minutes_v01,fixture_xfp_v01,availability_gate_reason
               FROM fixture_predictions WHERE element_id=5"""
        ).fetchone()
        self.assertEqual(unavailable, (0.0, 0.0, "unavailable"))

    def test_chronology_detector_and_am_exclusion(self) -> None:
        self.connection.execute(
            """UPDATE fixture_predictions
               SET history_latest_kickoff_used=target_deadline
               WHERE element_id=1"""
        )
        with self.assertRaisesRegex(HistoricalBacktestError, "chronological"):
            _validate_scored_tables(self.connection)

        self.connection.execute(
            """UPDATE fixture_predictions
               SET history_latest_kickoff_used=NULL WHERE element_id=1"""
        )
        self.connection.execute(
            "UPDATE player_gameweek SET \"position\"='AM' WHERE element_id=1"
        )
        with self.assertRaisesRegex(HistoricalBacktestError, "assistant manager"):
            _validate_scored_tables(self.connection)

    def test_metric_missingness_is_not_imputed_to_zero(self) -> None:
        rows = [
            {"prediction": 2.0, "actual": 1.0},
            {"prediction": None, "actual": 8.0},
            {"prediction": 9.0, "actual": None},
        ]
        metric = _metric_row(
            rows, scope_type="test", scope_value="all", target="target",
            predictor="predictor", prediction_field="prediction", actual_field="actual",
        )
        self.assertEqual(metric[4:9], (3, 1, 1, 1, 100.0 / 3.0))
        self.assertEqual(metric[9:12], (1.0, 1.0, 1.0))

    def test_ranking_uses_strict_n_and_player_id_tie_breaker(self) -> None:
        rows = [
            {
                "season": "2023-24",
                "target_gameweek": 2,
                "element_id": element,
                "gameweek_xfp_v01": 1.0,
                "actual_modeled_points": 1.0 if element >= 3 else 0.0,
                "actual_full_fpl_points": 1.0 if element >= 3 else 0.0,
            }
            for element in range(1, 13)
        ]
        ranking = _ranking_rows(rows)
        modeled_top_10 = next(
            row for row in ranking
            if row[0] == "gameweek" and row[3] == "modeled_components"
            and row[4] == 10
        )
        self.assertEqual(modeled_top_10[7:9], (8.0, 80.0))
        self.assertEqual(
            modeled_top_10[10], "score_desc_then_element_id_asc_strict_n"
        )

    def test_completed_backtest_is_not_overwritten(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            output_root = Path(temporary_directory)
            (output_root / "xfp-v01-baseline-v1").mkdir()
            with self.assertRaisesRegex(HistoricalBacktestError, "will not be overwritten"):
                _write_backtest_outputs(
                    self.connection,
                    output_root=output_root,
                    manifest_base={},
                )


if __name__ == "__main__":
    unittest.main()
