from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import duckdb

from fpl_decision_engine.transform import (
    CleanOutputExistsError,
    DataQualityError,
    transform_latest_players,
)


def player(player_id: int | None, *, team_id: int = 1, position_id: int = 3) -> dict:
    code = 1000 + player_id if player_id is not None else None
    return {
        "id": player_id,
        "code": code,
        "opta_code": f"p{code}" if code is not None else None,
        "first_name": "Test",
        "second_name": f"Player {player_id}",
        "web_name": f"Player {player_id}",
        "team": team_id,
        "element_type": position_id,
        "now_cost": 75,
        "selected_by_percent": "12.3",
        "status": "a",
        "chance_of_playing_next_round": None,
        "news": "",
        "minutes": 90,
        "starts": 1,
        "total_points": 8,
        "event_points": 8,
        "points_per_game": "8.0",
        "form": "8.0",
        "bonus": 2,
        "bps": 30,
        "expected_goals": "0.45",
        "expected_assists": "0.20",
        "expected_goal_involvements": "0.65",
        "expected_goals_conceded": "0.80",
        "expected_goals_per_90": 0.45,
        "expected_assists_per_90": 0.20,
        "expected_goal_involvements_per_90": 0.65,
        "expected_goals_conceded_per_90": 0.80,
        "clearances_blocks_interceptions": 3,
        "recoveries": 4,
        "tackles": 2,
        "defensive_contribution": 9,
        "defensive_contribution_per_90": 9.0,
        "penalties_order": None,
        "direct_freekicks_order": 2,
        "corners_and_indirect_freekicks_order": 1,
        "ep_this": "5.1",
        "ep_next": "5.4",
    }


class TransformPlayersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.raw_root = root / "raw"
        self.clean_root = root / "clean"
        self.season = "2026-27"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_snapshot(
        self,
        players: list[dict],
        timestamp: str = "20260824T010203.456789Z",
    ) -> Path:
        snapshot = self.raw_root / self.season / timestamp / "bootstrap-static.json"
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(
            json.dumps(
                {
                    "elements": players,
                    "teams": [{"id": 1, "name": "Arsenal", "short_name": "ARS"}],
                    "element_types": [
                        {"id": 3, "singular_name": "Midfielder"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        return snapshot

    def test_transforms_latest_snapshot_to_typed_valid_parquet(self) -> None:
        self.write_snapshot([player(99)], "20260823T010203.000000Z")
        snapshot = self.write_snapshot([player(1), player(2)])
        before = snapshot.read_bytes()
        before_hash = hashlib.sha256(before).hexdigest()

        output = transform_latest_players(
            raw_data_root=self.raw_root,
            clean_data_root=self.clean_root,
            season=self.season,
        )

        self.assertEqual(
            output,
            self.clean_root
            / self.season
            / "20260824T010203.456789Z"
            / "players.parquet",
        )
        self.assertEqual(snapshot.read_bytes(), before)

        connection = duckdb.connect(":memory:")
        try:
            row = connection.execute(
                """SELECT count(*), count(DISTINCT fpl_player_id), min(price_m),
                          min(team_name), min(position),
                          count(*) FILTER (WHERE chance_of_playing_next_round IS NULL),
                          min(source_sha256)
                   FROM read_parquet(?)""",
                [str(output)],
            ).fetchone()
            self.assertEqual(row, (2, 2, 7.5, "Arsenal", "Midfielder", 2, before_hash))

            numeric_types = connection.execute(
                """SELECT typeof(ownership_pct), typeof(points_per_game), typeof(form),
                          typeof(xg), typeof(xa), typeof(xgi), typeof(xgc),
                          typeof(ep_this), typeof(ep_next)
                   FROM read_parquet(?) LIMIT 1""",
                [str(output)],
            ).fetchone()
            self.assertEqual(numeric_types, ("DOUBLE",) * 9)
        finally:
            connection.close()

    def test_rejects_player_with_unknown_team(self) -> None:
        self.write_snapshot([player(1, team_id=999)])

        with self.assertRaisesRegex(DataQualityError, "team_id"):
            transform_latest_players(
                raw_data_root=self.raw_root,
                clean_data_root=self.clean_root,
                season=self.season,
            )

    def test_rejects_player_with_unknown_position(self) -> None:
        self.write_snapshot([player(1, position_id=999)])

        with self.assertRaisesRegex(DataQualityError, "position_id"):
            transform_latest_players(
                raw_data_root=self.raw_root,
                clean_data_root=self.clean_root,
                season=self.season,
            )

    def test_rejects_duplicate_or_null_player_ids(self) -> None:
        for invalid_players in ([player(1), player(1)], [player(None)]):
            with self.subTest(players=invalid_players):
                with tempfile.TemporaryDirectory() as clean_directory:
                    self.write_snapshot(invalid_players)
                    with self.assertRaisesRegex(DataQualityError, "unique and non-null"):
                        transform_latest_players(
                            raw_data_root=self.raw_root,
                            clean_data_root=Path(clean_directory),
                            season=self.season,
                        )

    def test_rejects_non_numeric_analytical_value(self) -> None:
        invalid_player = player(1)
        invalid_player["expected_goals"] = "unknown"
        self.write_snapshot([invalid_player])

        with self.assertRaisesRegex(DataQualityError, "expected_goals"):
            transform_latest_players(
                raw_data_root=self.raw_root,
                clean_data_root=self.clean_root,
                season=self.season,
            )

    def test_rejects_negative_price(self) -> None:
        invalid_player = player(1)
        invalid_player["now_cost"] = -1
        self.write_snapshot([invalid_player])

        with self.assertRaisesRegex(DataQualityError, "price_m"):
            transform_latest_players(
                raw_data_root=self.raw_root,
                clean_data_root=self.clean_root,
                season=self.season,
            )

    def test_rejects_non_numeric_ownership(self) -> None:
        invalid_player = player(1)
        invalid_player["selected_by_percent"] = "unknown"
        self.write_snapshot([invalid_player])

        with self.assertRaisesRegex(DataQualityError, "selected_by_percent"):
            transform_latest_players(
                raw_data_root=self.raw_root,
                clean_data_root=self.clean_root,
                season=self.season,
            )

    def test_does_not_overwrite_existing_clean_output(self) -> None:
        self.write_snapshot([player(1)])
        output = transform_latest_players(
            raw_data_root=self.raw_root,
            clean_data_root=self.clean_root,
            season=self.season,
        )
        original = output.read_bytes()

        with self.assertRaises(CleanOutputExistsError):
            transform_latest_players(
                raw_data_root=self.raw_root,
                clean_data_root=self.clean_root,
                season=self.season,
            )

        self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
