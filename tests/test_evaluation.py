from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from fpl_decision_engine.evaluation import (
    GameweekNotFinalizedError,
    evaluate_xfp_from_paths,
)
from fpl_decision_engine.transform import CleanOutputExistsError


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_parquet(path: Path, schema: tuple[tuple[str, str], ...], rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE source ("
            + ", ".join(f'"{name}" {kind}' for name, kind in schema)
            + ")"
        )
        placeholders = ", ".join("?" for _ in schema)
        if rows:
            connection.executemany(f"INSERT INTO source VALUES ({placeholders})", rows)
        connection.execute("COPY source TO ? (FORMAT PARQUET)", [str(path)])
    finally:
        connection.close()


class EvaluationTests(unittest.TestCase):
    season = "2026-27"
    target_gameweek = 2
    prediction_snapshot = "20260825T073532.450889Z"
    realized_snapshot = "20260901T120000.000000Z"
    evaluation_time = datetime(2026, 9, 1, 13, tzinfo=timezone.utc)

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.prediction = self.root / "prediction.parquet"
        self.realized_bootstrap = self.root / "realized-bootstrap.json"
        self.realized_fixtures = self.root / "realized-fixtures.parquet"
        self.realized_history = self.root / "realized-history.parquet"
        self.pre_bootstrap = self.root / "pre-bootstrap.json"
        self.pre_players = self.root / "pre-players.parquet"
        self.features = self.root / "features.parquet"

        pre_event = {
            "id": 2,
            "name": "Gameweek 2",
            "deadline_time": "2026-08-28T17:30:00Z",
            "is_next": True,
            "finished": False,
            "data_checked": False,
        }
        final_event = dict(pre_event, is_next=False, finished=True, data_checked=True)
        self.pre_bootstrap.write_text(json.dumps({"events": [pre_event]}))
        self.realized_bootstrap.write_text(json.dumps({"events": [final_event]}))

        player_schema = (
            ("fpl_player_id", "BIGINT"),
            ("ep_next", "DOUBLE"),
            ("snapshot_timestamp", "VARCHAR"),
        )
        write_parquet(
            self.pre_players,
            player_schema,
            [(player, value, self.prediction_snapshot) for player, value in (
                (1, 6.0), (2, 8.0), (3, 1.0), (4, 2.0), (5, 3.0)
            )],
        )
        feature_schema = (
            ("fpl_player_id", "BIGINT"),
            ("previous_gw_points", "DOUBLE"),
            ("average_prior_points", "DOUBLE"),
            ("snapshot_timestamp", "VARCHAR"),
            ("target_gameweek", "INTEGER"),
            ("history_gameweek_max_used", "INTEGER"),
        )
        write_parquet(
            self.features,
            feature_schema,
            [
                (player, float(player), float(player) + 0.5,
                 self.prediction_snapshot, 2, 1)
                for player in range(1, 6)
            ],
        )
        self._write_prediction()
        self._write_realized_inputs()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _write_prediction(self, players_hash: str | None = None) -> None:
        prediction_schema = (
            ("season", "VARCHAR"),
            ("snapshot_timestamp", "VARCHAR"),
            ("model_version", "VARCHAR"),
            ("target_gameweek", "INTEGER"),
            ("fpl_player_id", "BIGINT"),
            ("web_name", "VARCHAR"),
            ("position_id", "INTEGER"),
            ("position", "VARCHAR"),
            ("team_id", "INTEGER"),
            ("team_name", "VARCHAR"),
            ("fixture_count", "BIGINT"),
            ("gameweek_xfp_v01", "DOUBLE"),
            ("gameweek_expected_minutes_v01", "DOUBLE"),
            ("low_sample", "BOOLEAN"),
            ("attacking_rate_available", "BOOLEAN"),
            ("feature_input_sha256", "VARCHAR"),
            ("players_input_sha256", "VARCHAR"),
            ("bootstrap_sha256", "VARCHAR"),
        )
        values = (
            (1, "Keeper", 1, "Goalkeeper", 1, 4.0, 90.0, False, True),
            (2, "Double", 2, "Defender", 2, 6.0, 180.0, True, True),
            (3, "Blank", 3, "Midfielder", 0, 0.0, 0.0, True, False),
            (4, "Bench", 4, "Forward", 1, 2.0, 90.0, True, False),
            (5, "Missing prediction", 2, "Defender", 1, None, None, True, False),
        )
        rows = [
            (
                self.season,
                self.prediction_snapshot,
                "v0.1",
                2,
                player,
                name,
                position_id,
                position,
                1,
                "Team",
                fixture_count,
                prediction,
                expected_minutes,
                low_sample,
                attack,
                sha256(self.features),
                players_hash or sha256(self.pre_players),
                sha256(self.pre_bootstrap),
            )
            for (
                player, name, position_id, position, fixture_count, prediction,
                expected_minutes, low_sample, attack
            ) in values
        ]
        write_parquet(self.prediction, prediction_schema, rows)

    def _write_realized_inputs(self, keeper_total: int = 17) -> None:
        fixture_schema = (
            ("fixture_id", "BIGINT"),
            ("gameweek_id", "INTEGER"),
            ("finished", "BOOLEAN"),
            ("gameweek_finished", "BOOLEAN"),
            ("gameweek_data_checked", "BOOLEAN"),
            ("retrieved_at", "TIMESTAMPTZ"),
        )
        write_parquet(
            self.realized_fixtures,
            fixture_schema,
            [
                (20, 2, True, True, True, "2026-09-01T11:00:00Z"),
                (21, 2, True, True, True, "2026-09-01T11:00:00Z"),
            ],
        )
        history_schema = (
            ("fpl_player_id", "BIGINT"),
            ("fixture_id", "BIGINT"),
            ("gameweek_id", "INTEGER"),
            ("web_name", "VARCHAR"),
            ("position_id", "INTEGER"),
            ("position", "VARCHAR"),
            ("minutes", "INTEGER"),
            ("goals_scored", "INTEGER"),
            ("assists", "INTEGER"),
            ("total_points", "INTEGER"),
            ("gameweek_finished", "BOOLEAN"),
            ("gameweek_data_checked", "BOOLEAN"),
            ("retrieved_at", "TIMESTAMPTZ"),
        )
        rows = [
            (1, 20, 2, "Keeper", 1, "Goalkeeper", 90, 1, 1, keeper_total,
             True, True, "2026-09-01T11:05:00Z"),
            (2, 20, 2, "Double", 2, "Defender", 60, 1, 0, 8,
             True, True, "2026-09-01T11:05:00Z"),
            (2, 21, 2, "Double", 2, "Defender", 30, 0, 1, 4,
             True, True, "2026-09-01T11:05:00Z"),
            (4, 20, 2, "Bench", 4, "Forward", 30, 1, 0, 5,
             True, True, "2026-09-01T11:05:00Z"),
            (5, 20, 2, "Missing prediction", 2, "Defender", 90, 0, 0, 6,
             True, True, "2026-09-01T11:05:00Z"),
            (6, 20, 2, "New", 3, "Midfielder", 90, 1, 0, 7,
             True, True, "2026-09-01T11:05:00Z"),
        ]
        write_parquet(self.realized_history, history_schema, rows)

    def evaluate(self, output_name: str = "evaluations"):
        return evaluate_xfp_from_paths(
            prediction_path=self.prediction,
            realized_bootstrap_path=self.realized_bootstrap,
            realized_fixtures_path=self.realized_fixtures,
            realized_history_path=self.realized_history,
            evaluation_data_root=self.root / output_name,
            season=self.season,
            target_gameweek=2,
            model_version="v0.1",
            prediction_snapshot_timestamp=self.prediction_snapshot,
            realized_snapshot_timestamp=self.realized_snapshot,
            predeadline_bootstrap_path=self.pre_bootstrap,
            predeadline_players_path=self.pre_players,
            feature_path=self.features,
            top_n=2,
            evaluation_time=self.evaluation_time,
        )

    def test_hand_calculated_scoring_metrics_breakdowns_and_ranking(self) -> None:
        original_inputs = {
            path: path.read_bytes()
            for path in (
                self.prediction, self.realized_bootstrap, self.realized_fixtures,
                self.realized_history, self.pre_bootstrap, self.pre_players,
                self.features,
            )
        }
        outputs = self.evaluate()
        self.assertEqual(outputs.player_rows, 6)
        self.assertEqual(outputs.evaluated_players, 4)
        connection = duckdb.connect(":memory:")
        try:
            rows = {
                row[0]: row[1:]
                for row in connection.execute(
                    """SELECT fpl_player_id, actual_minutes,
                              actual_appearance_points_v01,
                              actual_goal_points_v01,
                              actual_assist_points_v01,
                              actual_modeled_points_v01,
                              actual_total_fpl_points
                       FROM read_parquet(?)""",
                    [str(outputs.player_path)],
                ).fetchall()
            }
            self.assertEqual(rows[1], (90, 2, 10, 3, 15, 17))
            self.assertEqual(rows[2], (90, 3, 6, 3, 12, 12))
            self.assertEqual(rows[3], (0, 0, 0, 0, 0, 0))
            self.assertEqual(rows[4], (30, 1, 4, 0, 5, 5))
            self.assertEqual(rows[6], (90, 2, 5, 0, 7, 7))

            modeled = connection.execute(
                """SELECT evaluated_players, missing_prediction_count,
                          missing_actual_count, prediction_coverage_pct,
                          mae, rmse, bias
                   FROM read_parquet(?)
                   WHERE target_name='modeled_points' AND predictor='xfp_v01'""",
                [str(outputs.metrics_path)],
            ).fetchone()
            self.assertEqual(modeled[:3], (4, 2, 0))
            self.assertAlmostEqual(modeled[3], 100 * 4 / 6)
            self.assertAlmostEqual(modeled[4], 5.0)
            self.assertAlmostEqual(modeled[5], math.sqrt(41.5))
            self.assertAlmostEqual(modeled[6], -5.0)

            full = connection.execute(
                """SELECT mae, rmse, bias FROM read_parquet(?)
                   WHERE target_name='full_fpl_points' AND predictor='xfp_v01'""",
                [str(outputs.metrics_path)],
            ).fetchone()
            self.assertAlmostEqual(full[0], 5.5)
            self.assertAlmostEqual(full[1], math.sqrt(53.5))
            self.assertAlmostEqual(full[2], -5.5)

            positions = connection.execute(
                """SELECT count(DISTINCT position) FROM read_parquet(?)
                   WHERE predictor='xfp_v01' AND target_name='modeled_points'""",
                [str(outputs.position_metrics_path)],
            ).fetchone()[0]
            self.assertEqual(positions, 4)
            diagnostic_groups = {
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT group_type FROM read_parquet(?)",
                    [str(outputs.diagnostic_metrics_path)],
                ).fetchall()
            }
            self.assertEqual(
                diagnostic_groups,
                {"actual_minutes", "low_sample", "attacking_rate_available"},
            )
            ranking = connection.execute(
                """SELECT evaluated_players, spearman_rank_correlation,
                          overlap_count, overlap_pct
                   FROM read_parquet(?)
                   WHERE target_name='modeled_points' AND predictor='xfp_v01'""",
                [str(outputs.ranking_path)],
            ).fetchone()
            self.assertEqual(ranking[0], 4)
            self.assertAlmostEqual(ranking[1], 0.8)
            self.assertEqual(ranking[2:], (2, 100.0))
        finally:
            connection.close()
        for path, original in original_inputs.items():
            self.assertEqual(path.read_bytes(), original)

    def test_refuses_non_finalized_gameweek(self) -> None:
        event = {
            "id": 2,
            "deadline_time": "2026-08-28T17:30:00Z",
            "finished": True,
            "data_checked": False,
        }
        self.realized_bootstrap.write_text(json.dumps({"events": [event]}))
        with self.assertRaisesRegex(GameweekNotFinalizedError, "not finalized"):
            self.evaluate()

    def test_post_deadline_ep_next_is_not_used(self) -> None:
        write_parquet(
            self.pre_players,
            (
                ("fpl_player_id", "BIGINT"),
                ("ep_next", "DOUBLE"),
                ("snapshot_timestamp", "VARCHAR"),
            ),
            [(player, 999.0, self.realized_snapshot) for player in range(1, 6)],
        )
        self._write_prediction(players_hash=sha256(self.pre_players))
        outputs = self.evaluate()
        connection = duckdb.connect(":memory:")
        try:
            available = connection.execute(
                "SELECT count(*) FROM read_parquet(?) WHERE ep_next_available",
                [str(outputs.player_path)],
            ).fetchone()[0]
            evaluated = connection.execute(
                """SELECT evaluated_players FROM read_parquet(?)
                   WHERE target_name='full_fpl_points' AND predictor='fpl_ep_next'""",
                [str(outputs.metrics_path)],
            ).fetchone()[0]
            self.assertEqual((available, evaluated), (0, 0))
        finally:
            connection.close()

    def test_later_actuals_do_not_change_frozen_predictions_or_overwrite(self) -> None:
        prediction_before = self.prediction.read_bytes()
        first = self.evaluate("first")
        self._write_realized_inputs(keeper_total=99)
        second = self.evaluate("second")
        connection = duckdb.connect(":memory:")
        try:
            first_predictions = connection.execute(
                "SELECT fpl_player_id, gameweek_xfp_v01 FROM read_parquet(?) ORDER BY 1",
                [str(first.player_path)],
            ).fetchall()
            second_predictions = connection.execute(
                "SELECT fpl_player_id, gameweek_xfp_v01 FROM read_parquet(?) ORDER BY 1",
                [str(second.player_path)],
            ).fetchall()
            self.assertEqual(first_predictions, second_predictions)
        finally:
            connection.close()
        self.assertEqual(self.prediction.read_bytes(), prediction_before)
        with self.assertRaises(CleanOutputExistsError):
            self.evaluate("second")


if __name__ == "__main__":
    unittest.main()
