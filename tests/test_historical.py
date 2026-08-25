from __future__ import annotations

import csv
import io
import json
import lzma
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from fpl_decision_engine.historical import (
    FEATURE_SCHEMA,
    HISTORY_CUTOFF_RULE,
    HistoricalDataQualityError,
    HistoricalOutputExistsError,
    HistoricalSourceHashError,
    _cache_source,
    _audit_chronological_exclusions,
    _build_features_and_actuals,
    _previous_context,
    _validate_feature_rows,
    build_historical_datasets,
    validate_predeadline_snapshot,
)
from fpl_decision_engine.historical_sources import HistoricalSource


def _csv_bytes(rows: list[dict[str, object]]) -> bytes:
    fields: list[str] = []
    for row in rows:
        for field in row:
            if field not in fields:
                fields.append(field)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def _source(
    season: str, path: str, kind: str, body: bytes, gameweek: int | None = None
) -> HistoricalSource:
    import hashlib

    return HistoricalSource(
        season=season,
        repository="example/pinned-history",
        commit="a" * 40,
        path=path,
        sha256=hashlib.sha256(body).hexdigest(),
        kind=kind,
        gameweek=gameweek,
    )


def _player(element: int, code: int, team: int, position: int, name: str) -> dict[str, object]:
    return {
        "id": element,
        "code": code,
        "element_type": position,
        "team": team,
        "web_name": name,
        "first_name": name,
        "second_name": "Player",
        "expected_goals": "10.10" if element == 1 else "0.00",
        "expected_assists": "0.00",
        "expected_goal_involvements": "0.00",
        "expected_goals_conceded": "0.00",
    }


def _gw_row(
    element: int,
    fixture: int,
    gameweek: int,
    position: str,
    team: str,
    opponent: int,
    was_home: bool,
    minutes: int,
    xg: str | None,
    xa: str | None = "0.00",
) -> dict[str, object]:
    return {
        "name": f"Player {element}",
        "position": position,
        "team": team,
        "xP": "999.9",
        "assists": "0",
        "bonus": "0",
        "bps": "0",
        "clean_sheets": "0",
        "creativity": "0.0",
        "element": str(element),
        "expected_assists": "" if xa is None else xa,
        "expected_goal_involvements": "" if xg is None or xa is None else f"{float(xg)+float(xa):.2f}",
        "expected_goals": "" if xg is None else xg,
        "expected_goals_conceded": "0.00",
        "fixture": str(fixture),
        "goals_conceded": "0",
        "goals_scored": "0",
        "ict_index": "0.0",
        "influence": "0.0",
        "kickoff_time": {
            1: "2023-08-12T14:00:00Z",
            2: "2023-08-19T14:00:00Z",
            3: "2023-08-20T14:00:00Z",
            4: "2023-08-26T14:00:00Z",
        }[fixture],
        "minutes": str(minutes),
        "opponent_team": str(opponent),
        "own_goals": "0",
        "penalties_missed": "0",
        "penalties_saved": "0",
        "red_cards": "0",
        "round": str(gameweek),
        "saves": "0",
        "selected": "100",
        "starts": "1" if minutes else "0",
        "team_a_score": "0",
        "team_h_score": "0",
        "threat": "0.0",
        "total_points": "2" if minutes >= 60 else ("1" if minutes else "0"),
        "transfers_balance": "0",
        "transfers_in": "0",
        "transfers_out": "0",
        "value": "50",
        "was_home": str(was_home),
        "yellow_cards": "0",
        "GW": str(gameweek),
    }


def _bootstrap(
    gameweek: int,
    captured_year: int,
    include_player_two: bool,
    include_am: bool,
) -> tuple[str, bytes]:
    deadline = datetime(captured_year, 8, 11 + 7 * (gameweek - 1), 18, tzinfo=timezone.utc)
    captured = deadline.replace(hour=12)
    players = [
        _player(1, 100 + (captured_year - 2023) * 1000, 1, 3, "One"),
        _player(3, 300, 1, 2, "Three"),
        _player(4, 400, 3, 4, "Four"),
    ]
    if include_player_two:
        players.append(_player(2, 200, 2, 2, "Two"))
    if include_am:
        players.append(_player(5, 500, 1, 5, "Manager"))
    for player in players:
        player.update(
            {
                "status": "a",
                "chance_of_playing_next_round": None,
                "news": "",
                "now_cost": 50,
                "selected_by_percent": "1.0",
                "transfers_in": 0,
                "transfers_out": 0,
                "transfers_in_event": 0,
                "transfers_out_event": 0,
                "minutes": 0,
                "ep_next": "2.0",
            }
        )
    payload = {
        "events": [
            {
                "id": gameweek,
                "is_next": True,
                "deadline_time": deadline.isoformat().replace("+00:00", "Z"),
            }
        ],
        "teams": [
            {"id": 1, "name": "Alpha"},
            {"id": 2, "name": "Beta"},
            {"id": 3, "name": "Gamma"},
        ],
        "element_types": [
            {"id": 1, "singular_name": "Goalkeeper"},
            {"id": 2, "singular_name": "Defender"},
            {"id": 3, "singular_name": "Midfielder"},
            {"id": 4, "singular_name": "Forward"},
            {"id": 5, "singular_name": "Manager"},
        ],
        "elements": players,
    }
    path = (
        f"cache/{captured.year}/{captured.month}/{captured.day}/"
        f"{captured.hour:02d}{captured.minute:02d}.json.xz"
    )
    return path, lzma.compress(json.dumps(payload).encode())


def _season_sources(season: str, year: int, include_am: bool) -> tuple[list[HistoricalSource], dict[str, bytes]]:
    teams = _csv_bytes(
        [
            {"id": 1, "name": "Alpha", "short_name": "ALP"},
            {"id": 2, "name": "Beta", "short_name": "BET"},
            {"id": 3, "name": "Gamma", "short_name": "GAM"},
        ]
    )
    fixtures = _csv_bytes(
        [
            {
                "id": 1, "event": 1, "team_h": 1, "team_a": 2,
                "kickoff_time": "2023-08-12T14:00:00Z", "team_h_score": 0,
                "team_a_score": 0, "finished": "True", "finished_provisional": "True",
            },
            {
                "id": 2, "event": 2, "team_h": 1, "team_a": 3,
                "kickoff_time": "2023-08-19T14:00:00Z", "team_h_score": 0,
                "team_a_score": 0, "finished": "True", "finished_provisional": "True",
            },
            {
                "id": 3, "event": 2, "team_h": 3, "team_a": 1,
                "kickoff_time": "2023-08-20T14:00:00Z", "team_h_score": 0,
                "team_a_score": 0, "finished": "True", "finished_provisional": "True",
            },
            {
                "id": 4, "event": 3, "team_h": 1, "team_a": 2,
                "kickoff_time": "2023-08-26T14:00:00Z", "team_h_score": 0,
                "team_a_score": 0, "finished": "True", "finished_provisional": "True",
            },
        ]
    )
    players = [_player(1, 100 + (year - 2023) * 1000, 1, 3, "One")]
    players += [_player(2, 200, 2, 2, "Two"), _player(3, 300, 1, 2, "Three")]
    players += [_player(4, 400, 3, 4, "Four")]
    if include_am:
        players.append(_player(5, 500, 1, 5, "Manager"))
    identity = _csv_bytes(players)
    rows = [
        _gw_row(1, 1, 1, "MID", "Alpha", 2, True, 70, "0.50"),
        _gw_row(3, 1, 1, "DEF", "Alpha", 2, True, 0, "0.00"),
        _gw_row(1, 2, 2, "MID", "Alpha", 3, True, 70, "9.00"),
        _gw_row(1, 3, 2, "MID", "Alpha", 3, False, 60, "0.20"),
        _gw_row(3, 2, 2, "DEF", "Alpha", 3, True, 10, "0.00"),
        _gw_row(3, 3, 2, "DEF", "Alpha", 3, False, 20, "0.00"),
        _gw_row(4, 2, 2, "FWD", "Gamma", 1, False, 0, None),
        _gw_row(4, 3, 2, "FWD", "Gamma", 1, True, 0, "0.00"),
        _gw_row(1, 4, 3, "MID", "Alpha", 2, True, 90, "0.30"),
        _gw_row(2, 4, 3, "DEF", "Beta", 1, False, 90, "0.00"),
        _gw_row(3, 4, 3, "DEF", "Alpha", 2, True, 90, "0.00"),
    ]
    if include_am:
        rows.append(_gw_row(5, 2, 2, "AM", "Alpha", 3, True, 0, "0.00"))
    merged = _csv_bytes(rows)

    bodies: dict[str, bytes] = {}
    sources: list[HistoricalSource] = []
    for path, kind, body in (
        (f"data/{season}/gws/merged_gw.csv", "player_fixture", merged),
        (f"data/{season}/fixtures.csv", "fixtures", fixtures),
        (f"data/{season}/players_raw.csv", "player_identity", identity),
        (f"data/{season}/teams.csv", "teams", teams),
    ):
        source = _source(season, path, kind, body)
        sources.append(source)
        bodies[source.url] = body
    for gameweek in (1, 2, 3):
        path, body = _bootstrap(
            gameweek, year, include_player_two=gameweek > 1, include_am=include_am
        )
        source = _source(season, path, "predeadline_bootstrap", body, gameweek)
        sources.append(source)
        bodies[source.url] = body
    return sources, bodies


class HistoricalIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.raw = root / "historical/raw"
        self.clean = root / "historical/clean"
        first_sources, first_bodies = _season_sources("2023-24", 2023, False)
        second_sources, second_bodies = _season_sources("2024-25", 2024, True)
        self.sources = tuple(first_sources + second_sources)
        self.bodies = first_bodies | second_bodies
        self.result = build_historical_datasets(
            raw_data_root=self.raw,
            clean_data_root=self.clean,
            sources=self.sources,
            fetcher=self.bodies.__getitem__,
            strict_approved=False,
            clock=lambda: datetime(2026, 8, 25, tzinfo=timezone.utc),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _query(self, season: str, dataset: str, sql: str):
        path = self.result.directory / season / f"{dataset}.parquet"
        connection = duckdb.connect(":memory:")
        try:
            connection.from_parquet(str(path)).create_view("source")
            return connection.execute(sql).fetchall()
        finally:
            connection.close()

    def test_fixture_expected_stats_remain_fixture_level_and_target_does_not_leak(self) -> None:
        self.assertEqual(
            self._query(
                "2023-24",
                "historical_player_fixture",
                "SELECT gameweek, fixture_id, xg FROM source WHERE element_id=1 ORDER BY gameweek, fixture_id",
            ),
            [(1, 1, 0.5), (2, 2, 9.0), (2, 3, 0.2), (3, 4, 0.3)],
        )
        prior = self._query(
            "2023-24",
            "historical_prediction_features",
            """SELECT DISTINCT cumulative_prior_xg, history_gameweek_max_used
               FROM source WHERE target_gameweek=2 AND element_id=1""",
        )
        self.assertEqual(prior, [(0.5, 1)])
        columns = {name for name, _ in FEATURE_SCHEMA}
        self.assertNotIn("xP", columns)
        self.assertFalse(any(name.startswith("actual_") for name in columns))

    def test_blank_zero_not_in_universe_and_double_previous_minutes_are_distinct(self) -> None:
        rows = self._query(
            "2023-24",
            "historical_prediction_features",
            """SELECT element_id, previous_gameweek_minutes_uncapped,
                      previous_gw_context_status, previous_gw_team_blank,
                      previous_gw_player_not_in_universe, previous_gw_zero_minutes
               FROM source WHERE target_gameweek=2 AND target_fixture_id IS NULL
                  OR target_gameweek=2 AND target_fixture_id=2
               ORDER BY element_id""",
        )
        by_player = {row[0]: row[1:] for row in rows}
        self.assertEqual(by_player[2], (None, "player_not_in_previous_predeadline_universe", False, True, False))
        self.assertEqual(by_player[3], (0, "fixture_existed_zero_minutes", False, False, True))
        self.assertEqual(by_player[4], (None, "verified_team_blank", True, False, False))
        double_minutes = self._query(
            "2023-24",
            "historical_prediction_features",
            """SELECT DISTINCT previous_gameweek_minutes_uncapped
               FROM source WHERE target_gameweek=3 AND element_id=1""",
        )
        self.assertEqual(double_minutes, [(130,)])

    def test_am_exclusion_identity_bridge_and_cross_season_element_collision(self) -> None:
        self.assertEqual(
            self._query(
                "2024-25",
                "historical_player_fixture",
                "SELECT count(*) FROM source WHERE historical_position='AM'",
            ),
            [(0,)],
        )
        manifest = json.loads(self.result.manifest_path.read_bytes())
        self.assertEqual(
            manifest["row_counts"]["2024-25"]["assistant_manager_rows_excluded"], 1
        )
        first = self._query(
            "2023-24", "historical_player_identity",
            "SELECT season, element_id, code FROM source WHERE element_id=1",
        )
        second = self._query(
            "2024-25", "historical_player_identity",
            "SELECT season, element_id, code FROM source WHERE element_id=1",
        )
        self.assertEqual(first, [("2023-24", 1, 100)])
        self.assertEqual(second, [("2024-25", 1, 1100)])
        self.assertTrue(manifest["identity"]["element_id_cross_season_join_prohibited"])

    def test_missing_xg_context_and_reconciliation_exception_are_preserved(self) -> None:
        self.assertEqual(
            self._query(
                "2023-24", "historical_player_fixture",
                "SELECT xg FROM source WHERE element_id=4 AND fixture_id=2",
            ),
            [(None,)],
        )
        exceptions = self._query(
            "2023-24", "historical_reconciliation_exceptions",
            """SELECT field, reporting_material, audit_classification, resolution
               FROM source WHERE element_id=1 AND field='expected_goals'""",
        )
        self.assertEqual(
            exceptions,
            [(
                "expected_goals",
                True,
                "source_reconciliation_difference",
                "fixture_values_preserved_no_forced_reconciliation",
            )],
        )
        contexts = self._query(
            "2023-24", "historical_fixtures",
            "SELECT DISTINCT fixture_assignment_context FROM source",
        )
        self.assertEqual(contexts, [("finalized_fixture_assignment",)])

    def test_cached_sources_are_immutable_and_clean_output_is_not_overwritten(self) -> None:
        before = {}
        for source in self.sources:
            path = (
                self.raw
                / source.repository.replace("/", "_")
                / source.commit
                / source.path
            )
            before[path] = path.read_bytes()

        with self.assertRaises(HistoricalOutputExistsError):
            build_historical_datasets(
                raw_data_root=self.raw,
                clean_data_root=self.clean,
                sources=self.sources,
                fetcher=self.bodies.__getitem__,
                strict_approved=False,
            )

        self.assertTrue(before)
        for path, body in before.items():
            self.assertEqual(path.read_bytes(), body)


class HistoricalSourceValidationTests(unittest.TestCase):
    def test_source_hash_mismatch_refuses_before_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = HistoricalSource(
                season="2023-24", repository="example/repo", commit="a" * 40,
                path="data/file.csv", sha256="0" * 64, kind="fixtures",
            )
            with self.assertRaises(HistoricalSourceHashError):
                _cache_source(
                    source,
                    raw_root=Path(directory),
                    fetcher=lambda _: b"not-approved",
                    clock=lambda: datetime.now(timezone.utc),
                )

    def test_post_deadline_snapshot_is_rejected(self) -> None:
        payload = {
            "events": [
                {"id": 1, "is_next": True, "deadline_time": "2023-08-11T18:00:00Z"}
            ]
        }
        source = HistoricalSource(
            season="2023-24", repository="example/repo", commit="a" * 40,
            path="cache/2023/8/11/1800.json.xz", sha256="0" * 64,
            kind="predeadline_bootstrap", gameweek=1,
        )
        with self.assertRaises(HistoricalDataQualityError):
            validate_predeadline_snapshot(payload, source)

    def test_team_change_uses_recorded_previous_calendar_gameweek_minutes(self) -> None:
        deadline = datetime(2025, 2, 14, 18, tzinfo=timezone.utc)
        prior_kickoff = datetime(2025, 2, 1, 15, tzinfo=timezone.utc)
        state = {
            "season": "2024-25",
            "target_gameweek": 25,
            "element_id": 450,
            "deadline": deadline,
        }
        previous_state = {"element_id": 450, "team_id": 17}
        context = _previous_context(
            state,
            states_by_gameweek={24: {450: previous_state}},
            history={24: {"fixture_rows": 2, "team_ids": [8], "minutes": 13}},
            fixtures_by_gameweek_team={
                (24, 17): [{"fixture_id": 235, "kickoff_at": prior_kickoff}]
            },
        )
        self.assertEqual(context["minutes"], 13)
        self.assertEqual(context["status"], "played_after_deadline_team_change")

        with self.assertRaisesRegex(
            HistoricalDataQualityError, "fixture count differs"
        ):
            _previous_context(
                state,
                states_by_gameweek={24: {450: previous_state}},
                history={24: {"fixture_rows": 2, "team_ids": [17], "minutes": 13}},
                fixtures_by_gameweek_team={
                    (24, 17): [{"fixture_id": 235, "kickoff_at": prior_kickoff}]
                },
            )

    def test_postponed_lower_event_fixture_is_excluded_by_target_deadline(self) -> None:
        deadline = datetime(2023, 8, 18, 18, tzinfo=timezone.utc)
        early = datetime(2023, 8, 10, 14, tzinfo=timezone.utc)
        postponed = datetime(2023, 8, 20, 14, tzinfo=timezone.utc)
        target_kickoff = datetime(2023, 8, 19, 14, tzinfo=timezone.utc)

        def fixture(fixture_id, gameweek, kickoff):
            return {
                "fixture_id": fixture_id,
                "gameweek": gameweek,
                "home_team": 1,
                "away_team": 2,
                "kickoff_time": kickoff.isoformat(),
                "kickoff_at": kickoff,
            }

        fixtures = [
            fixture(10, 1, early),
            fixture(11, 1, postponed),
            fixture(20, 2, target_kickoff),
        ]

        def performance(fixture_id, gameweek, kickoff, minutes, xg):
            return {
                "season": "2023-24",
                "element_id": 1,
                "fixture_id": fixture_id,
                "gameweek": gameweek,
                "position": "MID",
                "team_id": 1,
                "kickoff_at": kickoff,
                "minutes": minutes,
                "xg": xg,
                "xa": 0.0,
                "xgi": xg,
                "xgc": 0.0,
                "goals": 0,
                "assists": 0,
                "total_points": 2,
                "appearance_points": 2,
                "goal_points": 0,
                "assist_points": 0,
            }

        records = [
            performance(10, 1, early, 60, 0.2),
            performance(11, 1, postponed, 30, 9.0),
            performance(20, 2, target_kickoff, 90, 8.0),
        ]
        by_gameweek_team = {
            (1, 1): fixtures[:2],
            (1, 2): fixtures[:2],
            (2, 1): fixtures[2:],
            (2, 2): fixtures[2:],
        }
        season_data = {
            "player_fixture_records": records,
            "fixture_records": {row["fixture_id"]: row for row in fixtures},
            "fixtures_by_gameweek_team": by_gameweek_team,
        }

        def state(target, target_deadline):
            return {
                "season": "2023-24",
                "target_gameweek": target,
                "element_id": 1,
                "code": 100,
                "position": "MID",
                "team_id": 1,
                "team_name": "Alpha",
                "deadline": target_deadline,
                "snapshot_timestamp": target_deadline.replace(hour=12),
                "status": "a",
                "chance": None,
                "news": "",
                "source_path": f"snapshot-gw{target}",
                "source_sha256": "a" * 64,
            }

        states = [
            state(1, datetime(2023, 8, 9, 18, tzinfo=timezone.utc)),
            state(2, deadline),
        ]
        features, actuals = _build_features_and_actuals(season_data, states)
        indexes = {name: index for index, (name, _) in enumerate(FEATURE_SCHEMA)}
        target_rows = [
            row for row in features if row[indexes["target_gameweek"]] == 2
        ]
        self.assertEqual(len(target_rows), 1)
        row = target_rows[0]
        self.assertEqual(row[indexes["prior_fixture_rows"]], 1)
        self.assertEqual(row[indexes["cumulative_prior_xg"]], 0.2)
        self.assertEqual(
            row[indexes["chronologically_excluded_prior_fixture_rows"]], 1
        )
        self.assertEqual(row[indexes["history_latest_kickoff_used"]], early)
        self.assertEqual(row[indexes["history_cutoff_rule"]], HISTORY_CUTOFF_RULE)

        cases = _audit_chronological_exclusions(season_data, states)
        self.assertEqual(
            [(case["target_gameweek"], case["fixture_id"]) for case in cases],
            [(2, 11)],
        )

        malformed = list(row)
        malformed[indexes["history_latest_kickoff_used"]] = postponed
        with self.assertRaisesRegex(
            HistoricalDataQualityError, "chronological target leakage"
        ):
            _validate_feature_rows([tuple(malformed)], actuals)


if __name__ == "__main__":
    unittest.main()
