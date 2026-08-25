from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import duckdb

from fpl_decision_engine.features import FeatureBuildError, build_player_gameweek_features
from fpl_decision_engine.transform import CleanOutputExistsError


def write_parquet(
    path: Path,
    schema: tuple[tuple[str, str], ...],
    rows: list[tuple],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(":memory:")
    try:
        columns = ", ".join(f'"{name}" {data_type}' for name, data_type in schema)
        connection.execute(f"CREATE TABLE source ({columns})")
        placeholders = ", ".join("?" for _ in schema)
        if rows:
            connection.executemany(
                f"INSERT INTO source VALUES ({placeholders})", rows
            )
        connection.execute(
            "COPY source TO ? (FORMAT PARQUET)", [str(path)]
        )
    finally:
        connection.close()


class FeatureBuilderTests(unittest.TestCase):
    season = "2026-27"
    timestamp = "20260825T073532.450889Z"

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.raw_root = root / "raw"
        self.clean_root = root / "clean"
        self.feature_root = root / "features"
        raw_dir = self.raw_root / self.season / self.timestamp
        clean_dir = self.clean_root / self.season / self.timestamp
        raw_dir.mkdir(parents=True)
        clean_dir.mkdir(parents=True)

        bootstrap = {
            "events": [
                {
                    "id": 2,
                    "deadline_time": "2026-08-28T17:30:00Z",
                    "is_next": True,
                },
                {
                    "id": 4,
                    "deadline_time": "2026-09-12T12:30:00Z",
                    "is_next": False,
                },
            ]
        }
        (raw_dir / "bootstrap-static.json").write_text(
            json.dumps(bootstrap), encoding="utf-8"
        )
        (raw_dir / "fixtures.manifest.json").write_text(
            json.dumps(
                {
                    "status": "complete",
                    "retrieved_at": "2026-08-25T08:00:00Z",
                }
            ),
            encoding="utf-8",
        )
        (raw_dir / "player_history").mkdir()
        (raw_dir / "player_history/manifest.json").write_text(
            json.dumps(
                {
                    "status": "complete",
                    "completed_at": "2026-08-25T08:05:00Z",
                }
            ),
            encoding="utf-8",
        )

        player_schema = (
            ("fpl_player_id", "BIGINT"),
            ("web_name", "VARCHAR"),
            ("position_id", "INTEGER"),
            ("position", "VARCHAR"),
            ("team_id", "INTEGER"),
            ("team_name", "VARCHAR"),
            ("status", "VARCHAR"),
            ("chance_of_playing_next_round", "SMALLINT"),
            ("news", "VARCHAR"),
        )
        write_parquet(
            clean_dir / "players.parquet",
            player_schema,
            [
                (1, "History", 3, "Midfielder", 1, "Home", "a", None, ""),
                (2, "Zero", 1, "Goalkeeper", 2, "Away", "a", None, ""),
                (3, "Missing", 2, "Defender", 1, "Home", "d", 75, "Knock"),
            ],
        )
        fixture_schema = (
            ("fixture_id", "BIGINT"),
            ("gameweek_id", "INTEGER"),
            ("home_team_id", "INTEGER"),
            ("home_team_name", "VARCHAR"),
            ("away_team_id", "INTEGER"),
            ("away_team_name", "VARCHAR"),
            ("kickoff_time", "TIMESTAMPTZ"),
        )
        write_parquet(
            clean_dir / "fixtures.parquet",
            fixture_schema,
            [
                (20, 2, 1, "Home", 2, "Away", "2026-08-29T14:00:00Z"),
                (40, 4, 1, "Home", 2, "Away", "2026-09-13T14:00:00Z"),
            ],
        )
        history_schema = (
            ("fpl_player_id", "BIGINT"),
            ("fixture_id", "BIGINT"),
            ("gameweek_id", "INTEGER"),
            ("minutes", "INTEGER"),
            ("starts", "INTEGER"),
            ("total_points", "INTEGER"),
            ("xg", "DOUBLE"),
            ("xa", "DOUBLE"),
            ("xgc", "DOUBLE"),
            ("defensive_contribution", "INTEGER"),
            ("saves", "INTEGER"),
        )
        write_parquet(
            clean_dir / "player_gameweek_history.parquet",
            history_schema,
            [
                (1, 10, 1, 90, 1, 8, 1.0, 0.2, 0.5, 5, 0),
                (1, 20, 2, 45, 0, 3, 2.0, 0.3, 0.7, 2, 0),
                (1, 30, 3, 90, 1, 10, 3.0, 0.4, 0.2, 8, 0),
                (1, 40, 4, 90, 1, 999, 999.0, 999.0, 999.0, 999, 0),
                (2, 11, 1, 0, 0, 0, 0.0, 0.0, 0.0, 0, 0),
            ],
        )
        self.input_paths = [
            raw_dir / "bootstrap-static.json",
            raw_dir / "fixtures.manifest.json",
            raw_dir / "player_history/manifest.json",
            clean_dir / "players.parquet",
            clean_dir / "fixtures.parquet",
            clean_dir / "player_gameweek_history.parquet",
        ]

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def build(self, target_gameweek: int) -> Path:
        return build_player_gameweek_features(
            target_gameweek=target_gameweek,
            raw_data_root=self.raw_root,
            clean_data_root=self.clean_root,
            feature_data_root=self.feature_root,
            season=self.season,
            snapshot_timestamp=self.timestamp,
        )

    def test_gw2_uses_only_gw1_and_preserves_missing_vs_zero(self) -> None:
        before = {path: path.read_bytes() for path in self.input_paths}
        output = self.build(2)

        connection = duckdb.connect(":memory:")
        try:
            history = connection.execute(
                """SELECT target_home_away, prior_gameweeks_with_data,
                          prior_total_minutes, previous_gw_xg, cumulative_prior_xg,
                          prior_xg_per_90, previous_gw_xa, prior_xa_per_90,
                          previous_gw_points, history_gameweek_max_used,
                          rolling_3_gameweeks_with_data,
                          rolling_5_gameweeks_with_data
                   FROM read_parquet(?) WHERE fpl_player_id = 1""",
                [str(output)],
            ).fetchone()
            self.assertEqual(
                history,
                ("H", 1, 90, 1.0, 1.0, 1.0, 0.2, 0.2, 8, 1, 1, 1),
            )

            zero = connection.execute(
                """SELECT target_home_away, prior_gameweeks_with_data,
                          prior_total_minutes, previous_gw_xg, prior_xg_per_90
                   FROM read_parquet(?) WHERE fpl_player_id = 2""",
                [str(output)],
            ).fetchone()
            self.assertEqual(zero, ("A", 1, 0, 0.0, None))

            missing = connection.execute(
                """SELECT prior_gameweeks_with_data, prior_total_minutes,
                          previous_gw_xg, cumulative_prior_xg,
                          previous_gameweek_has_data,
                          chance_of_playing_next_round,
                          availability_reference_gameweek,
                          availability_is_target_next_gameweek
                   FROM read_parquet(?) WHERE fpl_player_id = 3""",
                [str(output)],
            ).fetchone()
            self.assertEqual(missing, (0, None, None, None, False, 75, 2, True))
        finally:
            connection.close()

        for path, original in before.items():
            self.assertEqual(path.read_bytes(), original)

    def test_rolling_windows_exclude_enormous_target_gameweek(self) -> None:
        output = self.build(4)
        connection = duckdb.connect(":memory:")
        try:
            row = connection.execute(
                """SELECT previous_gw_xg, cumulative_prior_xg,
                          rolling_3_xg, rolling_5_xg,
                          previous_gw_points, average_prior_points,
                          history_gameweek_max_used,
                          rolling_3_gameweeks_with_data,
                          chance_of_playing_next_round,
                          availability_reference_gameweek,
                          availability_is_target_next_gameweek
                   FROM read_parquet(?) WHERE fpl_player_id = 1""",
                [str(output)],
            ).fetchone()
            self.assertEqual(
                row,
                (3.0, 6.0, 6.0, 6.0, 10, 7.0, 3, 3, None, 2, False),
            )
            later_chance = connection.execute(
                """SELECT chance_of_playing_next_round,
                          availability_reference_gameweek,
                          availability_is_target_next_gameweek
                   FROM read_parquet(?) WHERE fpl_player_id = 3""",
                [str(output)],
            ).fetchone()
            self.assertEqual(later_chance, (None, 2, False))
        finally:
            connection.close()

    def test_feature_output_is_not_overwritten(self) -> None:
        output = self.build(2)
        original = output.read_bytes()
        with self.assertRaises(CleanOutputExistsError):
            self.build(2)
        self.assertEqual(output.read_bytes(), original)

    def test_rejects_snapshot_collected_after_target_deadline(self) -> None:
        manifest_path = (
            self.raw_root
            / self.season
            / self.timestamp
            / "player_history/manifest.json"
        )
        manifest_path.write_text(
            json.dumps(
                {
                    "status": "complete",
                    "completed_at": "2026-08-29T08:05:00Z",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(FeatureBuildError, "not fully collected"):
            self.build(2)


if __name__ == "__main__":
    unittest.main()
