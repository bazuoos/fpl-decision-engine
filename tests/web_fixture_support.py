"""Synthetic-only completed Engine v1 fixture for web/application tests."""

from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from unittest.mock import Mock

import duckdb

from fpl_decision_engine.operational_manifest import ChipState
from fpl_decision_engine.operational_runner import (
    MANAGER_EVIDENCE_VERSION,
    OperationalStages,
    prepare_gameweek,
    resume_gameweek,
)
from fpl_decision_engine.projection_provider import sha256_file
from fpl_decision_engine.refresh import RefreshResult


SYNTHETIC_SEASON = "2099-00"
SYNTHETIC_GAMEWEEK = 2
SYNTHETIC_SNAPSHOT = "20990825T073532.450889Z"
SYNTHETIC_DEADLINE = datetime(2099, 8, 28, 17, 30, tzinfo=timezone.utc)
SYNTHETIC_BEFORE = datetime(2099, 8, 27, 12, 0, tzinfo=timezone.utc)

_POSITION_NAMES = {
    "GK": (1, "Goalkeeper"),
    "DEF": (2, "Defender"),
    "MID": (3, "Midfielder"),
    "FWD": (4, "Forward"),
}
_PLAYERS = (
    (1, "GK", 1, 4.0),
    (2, "GK", 2, 1.0),
    (3, "DEF", 1, 4.0),
    (4, "DEF", 2, 3.0),
    (5, "DEF", 3, 2.0),
    (6, "DEF", 4, 1.0),
    (7, "DEF", 5, 0.5),
    (8, "MID", 1, 5.0),
    (9, "MID", 2, 4.0),
    (10, "MID", 3, 3.0),
    (11, "MID", 4, 2.0),
    (12, "MID", 5, 1.0),
    (13, "FWD", 6, 6.0),
    (14, "FWD", 3, 2.0),
    (15, "FWD", 4, 1.0),
    (16, "MID", 6, 10.0),
)
_OWNED = _PLAYERS[:15]


class SequenceClock:
    def __init__(self, *values: datetime) -> None:
        self._values = list(values)
        self._last = values[-1]

    def __call__(self) -> datetime:
        if self._values:
            self._last = self._values.pop(0)
        return self._last


@dataclass(frozen=True)
class SyntheticCompletedDecision:
    root: Path
    operations_root: Path
    preparation_id: str
    decision_id: str
    final_manifest_path: Path
    final_manifest_sha256: str
    gameweek_decision_path: Path
    gameweek_decision_sha256: str


def _copy_table(connection: duckdb.DuckDBPyConnection, table: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection.execute(f"COPY {table} TO ? (FORMAT PARQUET)", [str(path)])


def _write_synthetic_parquets(
    players_path: Path,
    features_path: Path,
    fixture_predictions_path: Path,
    gameweek_predictions_path: Path,
) -> None:
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            """CREATE TABLE players(
                   season VARCHAR,
                   snapshot_timestamp VARCHAR,
                   fpl_player_id BIGINT,
                   team_short_name VARCHAR,
                   price_m DOUBLE,
                   status VARCHAR,
                   chance_of_playing_next_round INTEGER,
                   team_id BIGINT,
                   position_id BIGINT
               )"""
        )
        connection.executemany(
            "INSERT INTO players VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    SYNTHETIC_SEASON,
                    SYNTHETIC_SNAPSHOT,
                    player_id,
                    f"S{team_id}",
                    5.0,
                    "a",
                    None,
                    team_id,
                    _POSITION_NAMES[position][0],
                )
                for player_id, position, team_id, _ in _PLAYERS
            ],
        )
        _copy_table(connection, "players", players_path)

        connection.execute(
            """CREATE TABLE features(
                   season VARCHAR,
                   target_gameweek INTEGER,
                   snapshot_timestamp VARCHAR,
                   target_deadline_time TIMESTAMP WITH TIME ZONE,
                   fpl_player_id BIGINT,
                   prior_total_minutes DOUBLE,
                   prior_appearances INTEGER,
                   prior_starts INTEGER,
                   cumulative_prior_xg DOUBLE,
                   cumulative_prior_xa DOUBLE,
                   availability_status VARCHAR,
                   chance_of_playing_next_round INTEGER,
                   availability_news VARCHAR
               )"""
        )
        connection.executemany(
            "INSERT INTO features VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    SYNTHETIC_SEASON,
                    SYNTHETIC_GAMEWEEK,
                    SYNTHETIC_SNAPSHOT,
                    SYNTHETIC_DEADLINE,
                    player_id,
                    900.0,
                    10,
                    10,
                    1.0,
                    0.5,
                    "a",
                    None,
                    "",
                )
                for player_id, *_ in _PLAYERS
            ],
        )
        _copy_table(connection, "features", features_path)
        feature_hash = sha256_file(features_path)

        prediction_columns = """(
            season VARCHAR,
            target_gameweek INTEGER,
            snapshot_timestamp VARCHAR,
            model_version VARCHAR,
            fpl_player_id BIGINT,
            web_name VARCHAR,
            team_id BIGINT,
            team_name VARCHAR,
            position_id BIGINT,
            position VARCHAR,
            gameweek_xfp_v01 DOUBLE,
            fixture_count INTEGER,
            prediction_complete BOOLEAN,
            gameweek_expected_minutes_v01 DOUBLE,
            gameweek_appearance_xfp_v01 DOUBLE,
            prior_minutes DOUBLE,
            prior_xg_per_90_used DOUBLE,
            prior_xa_per_90_used DOUBLE,
            low_sample BOOLEAN,
            feature_input_sha256 VARCHAR
        )"""
        connection.execute(f"CREATE TABLE gameweek_predictions {prediction_columns}")
        rows = []
        for player_id, position, team_id, projection in _PLAYERS:
            position_id, position_name = _POSITION_NAMES[position]
            rows.append(
                (
                    SYNTHETIC_SEASON,
                    SYNTHETIC_GAMEWEEK,
                    SYNTHETIC_SNAPSHOT,
                    "v0.1",
                    player_id,
                    f"Synthetic Player {player_id:02d}",
                    team_id,
                    f"Synthetic Club {team_id}",
                    position_id,
                    position_name,
                    projection,
                    1,
                    True,
                    90.0,
                    2.0,
                    900.0,
                    0.1,
                    0.05,
                    False,
                    feature_hash,
                )
            )
        connection.executemany(
            "INSERT INTO gameweek_predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        _copy_table(connection, "gameweek_predictions", gameweek_predictions_path)
        connection.execute(
            "CREATE TABLE fixture_predictions AS SELECT * FROM gameweek_predictions"
        )
        _copy_table(connection, "fixture_predictions", fixture_predictions_path)
    finally:
        connection.close()


@contextmanager
def materialized_synthetic_completed_decision() -> Iterator[SyntheticCompletedDecision]:
    """Build a complete trusted run from invented players in temporary storage."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        raw_root = root / "raw"
        clean_root = root / "clean"
        feature_root = root / "features"
        prediction_root = root / "predictions"
        operations_root = root / "operations"
        raw = raw_root / SYNTHETIC_SEASON / SYNTHETIC_SNAPSHOT
        clean = clean_root / SYNTHETIC_SEASON / SYNTHETIC_SNAPSHOT
        feature = (
            feature_root
            / SYNTHETIC_SEASON
            / SYNTHETIC_SNAPSHOT
            / f"gameweek={SYNTHETIC_GAMEWEEK}"
        )
        prediction = (
            prediction_root
            / SYNTHETIC_SEASON
            / SYNTHETIC_SNAPSHOT
            / f"gameweek={SYNTHETIC_GAMEWEEK}"
        )
        (raw / "player_history").mkdir(parents=True)
        clean.mkdir(parents=True)
        feature.mkdir(parents=True)
        prediction.mkdir(parents=True)

        bootstrap = raw / "bootstrap-static.json"
        bootstrap.write_text(
            json.dumps(
                {
                    "events": [
                        {
                            "id": SYNTHETIC_GAMEWEEK,
                            "is_next": True,
                            "deadline_time": SYNTHETIC_DEADLINE.isoformat().replace(
                                "+00:00", "Z"
                            ),
                        }
                    ]
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        fixtures = raw / "fixtures.json"
        fixtures.write_text("[]", encoding="utf-8")
        fixture_manifest = raw / "fixtures.manifest.json"
        fixture_manifest.write_text(
            json.dumps(
                {
                    "season": SYNTHETIC_SEASON,
                    "snapshot_timestamp": SYNTHETIC_SNAPSHOT,
                    "status": "complete",
                    "retrieved_at": "2099-08-25T08:00:00Z",
                    "bootstrap_sha256": sha256_file(bootstrap),
                    "response_sha256": sha256_file(fixtures),
                    "record_count": 0,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        history_manifest = raw / "player_history" / "manifest.json"
        history_manifest.write_text(
            json.dumps(
                {
                    "season": SYNTHETIC_SEASON,
                    "snapshot_timestamp": SYNTHETIC_SNAPSHOT,
                    "status": "complete",
                    "completed_at": "2099-08-25T08:01:00Z",
                    "bootstrap_sha256": sha256_file(bootstrap),
                    "expected_count": len(_PLAYERS),
                    "success_count": len(_PLAYERS),
                    "failure_count": 0,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        players = clean / "players.parquet"
        features = feature / "player_gameweek_features.parquet"
        fixture_predictions = prediction / "xfp_v01_fixtures.parquet"
        gameweek_predictions = prediction / "xfp_v01_gameweek.parquet"
        _write_synthetic_parquets(
            players,
            features,
            fixture_predictions,
            gameweek_predictions,
        )
        (clean / "fixtures.parquet").write_bytes(b"synthetic unused fixture input")
        (clean / "player_gameweek_history.parquet").write_bytes(
            b"synthetic unused history input"
        )
        refresh_manifest = raw / "refresh.manifest.json"
        refresh_manifest.write_text(
            json.dumps(
                {
                    "season": SYNTHETIC_SEASON,
                    "snapshot_timestamp": SYNTHETIC_SNAPSHOT,
                    "status": "complete",
                    "bootstrap": {"sha256": sha256_file(bootstrap)},
                    "fixtures": {
                        "sha256": sha256_file(fixtures),
                        "manifest_sha256": sha256_file(fixture_manifest),
                    },
                    "player_history": {
                        "manifest_sha256": sha256_file(history_manifest)
                    },
                    "clean_outputs": {"players": {"sha256": sha256_file(players)}},
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        refresh_result = RefreshResult(
            snapshot_timestamp=SYNTHETIC_SNAPSHOT,
            raw_directory=raw,
            clean_directory=clean,
            manifest_path=refresh_manifest,
            player_count=len(_PLAYERS),
            fixture_count=0,
            history_row_count=0,
        )
        stages = replace(
            OperationalStages(), refresh=Mock(return_value=refresh_result)
        )
        preparation = prepare_gameweek(
            target_gameweek=SYNTHETIC_GAMEWEEK,
            season=SYNTHETIC_SEASON,
            raw_data_root=raw_root,
            clean_data_root=clean_root,
            feature_data_root=feature_root,
            prediction_data_root=prediction_root,
            operations_root=operations_root,
            history_delay_seconds=0,
            clock=SequenceClock(SYNTHETIC_BEFORE, SYNTHETIC_BEFORE, SYNTHETIC_BEFORE),
            stages=stages,
        )
        manager_evidence = root / "synthetic-manager-evidence.json"
        manager_evidence.write_text(
            json.dumps(
                {
                    "version": MANAGER_EVIDENCE_VERSION,
                    "entry_id": 999999999,
                    "season": SYNTHETIC_SEASON,
                    "target_gameweek": SYNTHETIC_GAMEWEEK,
                    "bank_m": 0.0,
                    "free_transfers": 1,
                    "chip_state": ChipState.NO_CHIP.value,
                    "evidence_source": "synthetic test evidence; no real manager data",
                    "evidence_source_sha256": "0" * 64,
                    "current_selection_verified": True,
                    "players": [
                        {
                            "element_id": player_id,
                            "display_name": f"Synthetic Player {player_id:02d}",
                            "position": position,
                            "selling_price_m": 5.0,
                        }
                        for player_id, position, _, _ in _OWNED
                    ],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        completed = resume_gameweek(
            preparation_manifest_path=preparation.preparation_manifest_path,
            manager_evidence_path=manager_evidence,
            clock=SequenceClock(
                SYNTHETIC_BEFORE,
                SYNTHETIC_BEFORE,
                SYNTHETIC_BEFORE,
                SYNTHETIC_BEFORE,
                SYNTHETIC_BEFORE,
            ),
        )
        yield SyntheticCompletedDecision(
            root=root,
            operations_root=operations_root,
            preparation_id=preparation.preparation_id,
            decision_id=completed.decision_id,
            final_manifest_path=completed.final_manifest_path,
            final_manifest_sha256=completed.final_manifest_sha256,
            gameweek_decision_path=completed.gameweek_decision_path,
            gameweek_decision_sha256=completed.gameweek_decision_sha256,
        )
