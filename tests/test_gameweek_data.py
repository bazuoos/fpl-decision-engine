from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError

import duckdb

from fpl_decision_engine.gameweek_transform import (
    transform_fixtures_for_snapshot,
    transform_player_history_for_snapshot,
)
from fpl_decision_engine.official_data import (
    FPL_ELEMENT_SUMMARY_URL,
    FPL_FIXTURES_URL,
    PartialHistoryFetchError,
    SourceRequestError,
    fetch_fixtures_for_snapshot,
    fetch_player_histories_for_snapshot,
)
from fpl_decision_engine.transform import CleanOutputExistsError, DataQualityError


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class RoutingOpener:
    def __init__(self, routes: dict[str, bytes | Exception]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url: str, *, timeout: float) -> FakeResponse:
        self.calls.append(url)
        result = self.routes[url]
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


def json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def bootstrap() -> dict:
    return {
        "elements": [
            {
                "id": 1,
                "code": 101,
                "opta_code": "p101",
                "web_name": "One",
                "team": 1,
                "element_type": 2,
            },
            {
                "id": 2,
                "code": 102,
                "opta_code": "p102",
                "web_name": "Two",
                "team": 2,
                "element_type": 2,
            },
        ],
        "teams": [
            {"id": 1, "name": "Home"},
            {"id": 2, "name": "Away"},
        ],
        "element_types": [{"id": 2, "singular_name": "Defender"}],
        "events": [
            {
                "id": 1,
                "finished": False,
                "data_checked": False,
                "is_current": True,
                "is_next": False,
            },
            {
                "id": 2,
                "finished": False,
                "data_checked": False,
                "is_current": False,
                "is_next": True,
            },
        ],
    }


def fixture(*, fixture_id: int = 10, event: int | None = 1) -> dict:
    return {
        "code": 10010,
        "event": event,
        "finished": True,
        "finished_provisional": True,
        "id": fixture_id,
        "kickoff_time": "2026-08-21T19:00:00Z",
        "minutes": 90,
        "provisional_start_time": False,
        "started": True,
        "team_a": 2,
        "team_a_score": 0,
        "team_h": 1,
        "team_h_score": 2,
        "stats": [],
        "team_h_difficulty": 2,
        "team_a_difficulty": 4,
        "pulse_id": 500,
    }


def history_record(
    player_id: int,
    *,
    fixture_id: int = 10,
    opponent_team: int = 2,
    expected_goals: str = "0.25",
) -> dict:
    return {
        "element": player_id,
        "fixture": fixture_id,
        "opponent_team": opponent_team,
        "total_points": 8,
        "was_home": True,
        "kickoff_time": "2026-08-21T19:00:00Z",
        "team_h_score": 2,
        "team_a_score": 0,
        "round": 1,
        "modified": False,
        "minutes": 90,
        "goals_scored": 1,
        "assists": 0,
        "clean_sheets": 1,
        "goals_conceded": 0,
        "own_goals": 0,
        "penalties_saved": 0,
        "penalties_missed": 0,
        "yellow_cards": 0,
        "red_cards": 0,
        "saves": 0,
        "bonus": 2,
        "bps": 30,
        "influence": "20.0",
        "creativity": "1.5",
        "threat": "30.0",
        "ict_index": "5.2",
        "clearances_blocks_interceptions": 5,
        "recoveries": 3,
        "tackles": 2,
        "defensive_contribution": 7,
        "starts": 1,
        "expected_goals": expected_goals,
        "expected_assists": "0.05",
        "expected_goal_involvements": "0.30",
        "expected_goals_conceded": "0.40",
        "value": 50,
        "transfers_balance": 10,
        "selected": 1000,
        "transfers_in": 20,
        "transfers_out": 10,
    }


class GameweekDataTests(unittest.TestCase):
    timestamp = "20260825T073532.450889Z"
    season = "2026-27"
    fixed_time = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.raw_root = root / "raw"
        self.clean_root = root / "clean"
        self.snapshot = (
            self.raw_root / self.season / self.timestamp / "bootstrap-static.json"
        )
        self.snapshot.parent.mkdir(parents=True)
        self.bootstrap_bytes = json_bytes(bootstrap())
        self.snapshot.write_bytes(self.bootstrap_bytes)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def ingest_complete_data(
        self,
        *,
        fixtures: list[dict] | None = None,
        player_one_history: list[dict] | None = None,
    ) -> tuple[bytes, bytes, bytes]:
        fixture_body = json_bytes(fixtures or [fixture()])
        history_one = json_bytes(
            {
                "fixtures": [],
                "history": player_one_history
                if player_one_history is not None
                else [history_record(1)],
                "history_past": [],
            }
        )
        history_two = json_bytes(
            {"fixtures": [], "history": [], "history_past": []}
        )
        opener = RoutingOpener(
            {
                FPL_FIXTURES_URL: fixture_body,
                FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=1): history_one,
                FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=2): history_two,
            }
        )
        fetch_fixtures_for_snapshot(
            raw_data_root=self.raw_root,
            season=self.season,
            snapshot_timestamp=self.timestamp,
            opener=opener,
            clock=lambda: self.fixed_time,
            sleeper=lambda seconds: None,
        )
        fetch_player_histories_for_snapshot(
            raw_data_root=self.raw_root,
            season=self.season,
            snapshot_timestamp=self.timestamp,
            delay_seconds=0,
            opener=opener,
            clock=lambda: self.fixed_time,
            sleeper=lambda seconds: None,
        )
        return fixture_body, history_one, history_two

    def test_ingests_exact_bytes_and_transforms_typed_datasets(self) -> None:
        fixture_body, history_one, history_two = self.ingest_complete_data()
        snapshot_dir = self.snapshot.parent
        self.assertEqual((snapshot_dir / "fixtures.json").read_bytes(), fixture_body)
        self.assertEqual(
            (snapshot_dir / "player_history/1.json").read_bytes(), history_one
        )
        self.assertEqual(
            (snapshot_dir / "player_history/2.json").read_bytes(), history_two
        )

        fixture_output = transform_fixtures_for_snapshot(
            raw_data_root=self.raw_root,
            clean_data_root=self.clean_root,
            season=self.season,
            snapshot_timestamp=self.timestamp,
        )
        history_output = transform_player_history_for_snapshot(
            raw_data_root=self.raw_root,
            clean_data_root=self.clean_root,
            season=self.season,
            snapshot_timestamp=self.timestamp,
        )
        self.assertEqual(self.snapshot.read_bytes(), self.bootstrap_bytes)
        self.assertEqual((snapshot_dir / "fixtures.json").read_bytes(), fixture_body)
        self.assertEqual(
            (snapshot_dir / "player_history/1.json").read_bytes(), history_one
        )

        connection = duckdb.connect(":memory:")
        try:
            fixture_row = connection.execute(
                """SELECT fixture_id, home_team_name, away_team_name,
                          gameweek_finished, gameweek_data_checked
                   FROM read_parquet(?)""",
                [str(fixture_output)],
            ).fetchone()
            self.assertEqual(fixture_row, (10, "Home", "Away", False, False))
            history_row = connection.execute(
                """SELECT fpl_player_id, fixture_id, team_id, opponent_team_id,
                          home_away, typeof(xg), xg, price_m,
                          gameweek_finished, gameweek_data_checked
                   FROM read_parquet(?)""",
                [str(history_output)],
            ).fetchone()
            self.assertEqual(
                history_row,
                (1, 10, 1, 2, "H", "DOUBLE", 0.25, 5.0, False, False),
            )
        finally:
            connection.close()

        with self.assertRaises(CleanOutputExistsError):
            transform_fixtures_for_snapshot(
                raw_data_root=self.raw_root,
                clean_data_root=self.clean_root,
                season=self.season,
                snapshot_timestamp=self.timestamp,
            )
        with self.assertRaises(CleanOutputExistsError):
            transform_player_history_for_snapshot(
                raw_data_root=self.raw_root,
                clean_data_root=self.clean_root,
                season=self.season,
                snapshot_timestamp=self.timestamp,
            )

    def test_partial_history_failure_is_recorded_and_detectable(self) -> None:
        player_one = json_bytes(
            {"fixtures": [], "history": [history_record(1)], "history_past": []}
        )
        opener = RoutingOpener(
            {
                FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=1): player_one,
                FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=2): URLError("offline"),
            }
        )

        with self.assertRaises(PartialHistoryFetchError) as raised:
            fetch_player_histories_for_snapshot(
                raw_data_root=self.raw_root,
                season=self.season,
                snapshot_timestamp=self.timestamp,
                attempts=1,
                delay_seconds=0,
                opener=opener,
                clock=lambda: self.fixed_time,
                sleeper=lambda seconds: None,
            )

        self.assertEqual(raised.exception.failed_player_ids, [2])
        manifest = json.loads(raised.exception.manifest_path.read_bytes())
        self.assertEqual(manifest["status"], "partial")
        self.assertEqual(manifest["success_count"], 1)
        self.assertEqual(manifest["failure_count"], 1)
        self.assertTrue((self.snapshot.parent / "player_history/1.json").is_file())
        self.assertFalse((self.snapshot.parent / "player_history/2.json").exists())

    def test_fixture_validation_rejects_duplicate_ids(self) -> None:
        opener = RoutingOpener(
            {FPL_FIXTURES_URL: json_bytes([fixture(), fixture()])}
        )
        with self.assertRaisesRegex(SourceRequestError, "duplicate fixture IDs"):
            fetch_fixtures_for_snapshot(
                raw_data_root=self.raw_root,
                season=self.season,
                snapshot_timestamp=self.timestamp,
                opener=opener,
                clock=lambda: self.fixed_time,
                sleeper=lambda seconds: None,
            )
        self.assertFalse((self.snapshot.parent / "fixtures.json").exists())

    def test_history_validation_rejects_unknown_fixture(self) -> None:
        self.ingest_complete_data(
            player_one_history=[history_record(1, fixture_id=999)]
        )
        with self.assertRaisesRegex(DataQualityError, "unknown fixture"):
            transform_player_history_for_snapshot(
                raw_data_root=self.raw_root,
                clean_data_root=self.clean_root,
                season=self.season,
                snapshot_timestamp=self.timestamp,
            )

    def test_history_validation_rejects_inconsistent_home_away(self) -> None:
        self.ingest_complete_data(
            player_one_history=[history_record(1, opponent_team=1)]
        )
        with self.assertRaisesRegex(DataQualityError, "home-away"):
            transform_player_history_for_snapshot(
                raw_data_root=self.raw_root,
                clean_data_root=self.clean_root,
                season=self.season,
                snapshot_timestamp=self.timestamp,
            )

    def test_history_validation_rejects_non_numeric_expected_stat(self) -> None:
        self.ingest_complete_data(
            player_one_history=[history_record(1, expected_goals="unknown")]
        )
        with self.assertRaisesRegex(DataQualityError, "expected_goals"):
            transform_player_history_for_snapshot(
                raw_data_root=self.raw_root,
                clean_data_root=self.clean_root,
                season=self.season,
                snapshot_timestamp=self.timestamp,
            )


if __name__ == "__main__":
    unittest.main()
