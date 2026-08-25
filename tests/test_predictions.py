from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from fpl_decision_engine.predictions import (
    PredictionError,
    predict_xfp_v01_from_feature,
)
from fpl_decision_engine.transform import CleanOutputExistsError


FEATURE_SCHEMA = (
    ("season", "VARCHAR"),
    ("snapshot_timestamp", "VARCHAR"),
    ("fpl_player_id", "BIGINT"),
    ("web_name", "VARCHAR"),
    ("position_id", "INTEGER"),
    ("position", "VARCHAR"),
    ("snapshot_team_id", "INTEGER"),
    ("snapshot_team_name", "VARCHAR"),
    ("target_gameweek", "INTEGER"),
    ("target_fixture_id", "BIGINT"),
    ("target_has_fixture", "BOOLEAN"),
    ("target_fixture_count", "BIGINT"),
    ("target_opponent_team_id", "INTEGER"),
    ("target_opponent_team_name", "VARCHAR"),
    ("target_home_away", "VARCHAR"),
    ("target_kickoff_time", "TIMESTAMPTZ"),
    ("previous_gameweek_has_data", "BOOLEAN"),
    ("previous_gw_minutes", "DOUBLE"),
    ("prior_total_minutes", "DOUBLE"),
    ("prior_gameweeks_with_data", "BIGINT"),
    ("history_gameweek_max_used", "INTEGER"),
    ("prior_xg_per_90", "DOUBLE"),
    ("prior_xa_per_90", "DOUBLE"),
    ("target_deadline_time", "TIMESTAMPTZ"),
    ("availability_status", "VARCHAR"),
    ("chance_of_playing_next_round", "SMALLINT"),
    ("availability_news", "VARCHAR"),
    ("availability_as_of", "TIMESTAMPTZ"),
    ("availability_known_pre_deadline", "BOOLEAN"),
    ("availability_reference_gameweek", "INTEGER"),
    ("availability_is_target_next_gameweek", "BOOLEAN"),
    ("fixture_retrieved_at", "TIMESTAMPTZ"),
    ("history_retrieved_at", "TIMESTAMPTZ"),
    ("players_input_sha256", "VARCHAR"),
    ("fixtures_input_sha256", "VARCHAR"),
    ("history_input_sha256", "VARCHAR"),
    ("bootstrap_sha256", "VARCHAR"),
    # These forbidden target outcomes exist solely to prove the model ignores them.
    ("target_actual_xg", "DOUBLE"),
    ("target_actual_xa", "DOUBLE"),
    ("target_actual_points", "INTEGER"),
    ("target_home_team_score", "INTEGER"),
    ("target_away_team_score", "INTEGER"),
)


def write_feature(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(":memory:")
    try:
        columns = ", ".join(f'"{name}" {kind}' for name, kind in FEATURE_SCHEMA)
        connection.execute(f"CREATE TABLE features ({columns})")
        placeholders = ", ".join("?" for _ in FEATURE_SCHEMA)
        connection.executemany(f"INSERT INTO features VALUES ({placeholders})", rows)
        connection.execute("COPY features TO ? (FORMAT PARQUET)", [str(path)])
    finally:
        connection.close()


def feature_row(
    player_id: int,
    name: str,
    position_id: int,
    position: str,
    *,
    fixture_id: int | None = 20,
    fixture_count: int = 1,
    previous_minutes: float | None = 90,
    prior_minutes: float | None = 90,
    prior_gameweeks: int = 1,
    history_max: int | None = 1,
    xg_per_90: float | None = 0.1,
    xa_per_90: float | None = 0.2,
    status: str = "a",
    chance: int | None = None,
    target_values: tuple[float, float, int, int, int] = (0, 0, 0, 0, 0),
) -> tuple:
    has_fixture = fixture_id is not None
    return (
        "2026-27",
        "20260825T073532.450889Z",
        player_id,
        name,
        position_id,
        position,
        1,
        "Home",
        2,
        fixture_id,
        has_fixture,
        fixture_count,
        2 if has_fixture else None,
        "Away" if has_fixture else None,
        "H" if has_fixture else None,
        "2026-08-29T14:00:00Z" if has_fixture else None,
        previous_minutes is not None,
        previous_minutes,
        prior_minutes,
        prior_gameweeks,
        history_max,
        xg_per_90,
        xa_per_90,
        "2026-08-28T17:30:00Z",
        status,
        chance,
        "",
        "2026-08-25T07:35:32Z",
        True,
        2,
        True,
        "2026-08-25T08:00:00Z",
        "2026-08-25T08:05:00Z",
        "players-hash",
        "fixtures-hash",
        "history-hash",
        "bootstrap-hash",
        *target_values,
    )


class XFPV01Tests(unittest.TestCase):
    season = "2026-27"
    timestamp = "20260825T073532.450889Z"

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.feature_path = self.root / "features.parquet"
        self.rows = [
            feature_row(1, "Keeper", 1, "Goalkeeper"),
            feature_row(
                2, "Defender", 2, "Defender", previous_minutes=60
            ),
            feature_row(
                3, "Double", 3, "Midfielder", fixture_id=20,
                fixture_count=2, previous_minutes=45, xg_per_90=0.9,
                xa_per_90=0.3,
            ),
            feature_row(
                3, "Double", 3, "Midfielder", fixture_id=21,
                fixture_count=2, previous_minutes=45, xg_per_90=0.9,
                xa_per_90=0.3,
            ),
            feature_row(4, "Blank", 4, "Forward", fixture_id=None, fixture_count=0),
            feature_row(
                5, "Zero", 4, "Forward", previous_minutes=0,
                prior_minutes=0, xg_per_90=None, xa_per_90=None,
            ),
            feature_row(
                6, "Missing", 3, "Midfielder", previous_minutes=None,
                prior_minutes=None, prior_gameweeks=0, history_max=None,
                xg_per_90=None, xa_per_90=None,
            ),
            feature_row(7, "Suspended", 2, "Defender", status="s"),
            feature_row(
                8, "Veteran", 4, "Forward", prior_gameweeks=3,
                xg_per_90=1.0, xa_per_90=0.0,
            ),
            feature_row(9, "Ruled Out", 2, "Defender", chance=0),
            feature_row(10, "Doubtful", 3, "Midfielder", chance=50),
        ]
        write_feature(self.feature_path, self.rows)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def predict(self, output_name: str = "predictions"):
        return predict_xfp_v01_from_feature(
            feature_path=self.feature_path,
            prediction_data_root=self.root / output_name,
            season=self.season,
            snapshot_timestamp=self.timestamp,
            target_gameweek=2,
        )

    def test_components_minutes_positions_null_policy_and_aggregation(self) -> None:
        original = self.feature_path.read_bytes()
        outputs = self.predict()
        self.assertEqual(outputs.fixture_rows, 11)
        self.assertEqual(outputs.gameweek_rows, 10)

        connection = duckdb.connect(":memory:")
        try:
            fixture_rows = connection.execute(
                """SELECT fpl_player_id, expected_minutes_v01,
                          appearance_xfp_v01, expected_goals_v01,
                          goal_points_for_position, goal_xfp_v01,
                          expected_assists_v01, assist_xfp_v01,
                          fixture_xfp_v01, attacking_rate_available,
                          prediction_complete, low_sample,
                          availability_gate_reason
                   FROM read_parquet(?) ORDER BY fpl_player_id, fixture_id""",
                [str(outputs.fixture_path)],
            ).fetchall()
            keeper = fixture_rows[0]
            self.assertEqual(keeper[:5], (1, 90.0, 2.0, 0.1, 10))
            self.assertAlmostEqual(keeper[5], 1.0)
            self.assertAlmostEqual(keeper[6], 0.2)
            self.assertAlmostEqual(keeper[7], 0.6)
            self.assertAlmostEqual(keeper[8], 3.6)

            defender = fixture_rows[1]
            self.assertEqual(defender[1:5], (60.0, 2.0, 1 / 15, 6))
            self.assertAlmostEqual(defender[5], 0.4)
            self.assertAlmostEqual(defender[7], 0.4)
            self.assertAlmostEqual(defender[8], 2.8)

            zero = next(row for row in fixture_rows if row[0] == 5)
            self.assertEqual(zero[1:3], (0.0, 0.0))
            self.assertIsNone(zero[3])
            self.assertEqual(zero[8], 0.0)
            self.assertFalse(zero[9])
            self.assertFalse(zero[10])

            missing = next(row for row in fixture_rows if row[0] == 6)
            self.assertIsNone(missing[1])
            self.assertIsNone(missing[2])
            self.assertIsNone(missing[8])

            suspended = next(row for row in fixture_rows if row[0] == 7)
            self.assertEqual(suspended[1:3], (0.0, 0.0))
            self.assertEqual(suspended[8], 0.0)
            self.assertEqual(suspended[12], "suspended")

            veteran = next(row for row in fixture_rows if row[0] == 8)
            self.assertEqual(veteran[4], 4)
            self.assertEqual(veteran[5], 4.0)
            self.assertFalse(veteran[11])

            ruled_out = next(row for row in fixture_rows if row[0] == 9)
            self.assertEqual(ruled_out[1:3], (0.0, 0.0))
            self.assertEqual(ruled_out[12], "explicit_zero_chance")
            doubtful = next(row for row in fixture_rows if row[0] == 10)
            self.assertEqual(doubtful[1], 90.0)
            self.assertIsNone(doubtful[12])

            gameweeks = {
                row[0]: row[1:]
                for row in connection.execute(
                    """SELECT fpl_player_id, fixture_count,
                              gameweek_xfp_v01, prediction_complete
                       FROM read_parquet(?)""",
                    [str(outputs.gameweek_path)],
                ).fetchall()
            }
            self.assertAlmostEqual(gameweeks[1][1], 3.6)
            self.assertEqual(gameweeks[3][0], 2)
            self.assertAlmostEqual(gameweeks[3][1], 7.4)
            self.assertEqual(gameweeks[4], (0, 0.0, True))
            self.assertIsNone(gameweeks[6][1])
        finally:
            connection.close()
        self.assertEqual(self.feature_path.read_bytes(), original)

    def test_predictions_are_not_overwritten(self) -> None:
        outputs = self.predict()
        original_fixture = outputs.fixture_path.read_bytes()
        original_gameweek = outputs.gameweek_path.read_bytes()
        with self.assertRaises(CleanOutputExistsError):
            self.predict()
        self.assertEqual(outputs.fixture_path.read_bytes(), original_fixture)
        self.assertEqual(outputs.gameweek_path.read_bytes(), original_gameweek)

    def test_forbidden_target_outcomes_do_not_change_predictions(self) -> None:
        first = self.predict("first")
        write_feature(
            self.feature_path,
            [
                row[:-5] + (999999.0, 999999.0, 999999, 99, 88)
                for row in self.rows
            ],
        )
        second = self.predict("second")
        connection = duckdb.connect(":memory:")
        try:
            columns = """fpl_player_id, fixture_id, expected_minutes_v01,
                         prior_xg_per_90_used, prior_xa_per_90_used,
                         appearance_xfp_v01, goal_xfp_v01,
                         assist_xfp_v01, fixture_xfp_v01"""
            first_values = connection.execute(
                f"SELECT {columns} FROM read_parquet(?) ORDER BY 1, 2",
                [str(first.fixture_path)],
            ).fetchall()
            second_values = connection.execute(
                f"SELECT {columns} FROM read_parquet(?) ORDER BY 1, 2",
                [str(second.fixture_path)],
            ).fetchall()
            self.assertEqual(first_values, second_values)
        finally:
            connection.close()

    def test_rejects_target_or_future_history_and_snapshot_mismatch(self) -> None:
        unsafe = list(self.rows)
        unsafe[0] = feature_row(1, "Keeper", 1, "Goalkeeper", history_max=2)
        write_feature(self.feature_path, unsafe)
        with self.assertRaisesRegex(PredictionError, "target/future history"):
            self.predict()

        write_feature(self.feature_path, self.rows)
        with self.assertRaisesRegex(PredictionError, "provenance"):
            predict_xfp_v01_from_feature(
                feature_path=self.feature_path,
                prediction_data_root=self.root / "later",
                season=self.season,
                snapshot_timestamp="20260826T073532.450889Z",
                target_gameweek=2,
            )


if __name__ == "__main__":
    unittest.main()
