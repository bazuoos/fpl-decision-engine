"""Create a typed analytical player dataset from an immutable raw snapshot."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import uuid
from pathlib import Path
from typing import Any

import duckdb

from .pipeline import FPL_BOOTSTRAP_STATIC_URL

logger = logging.getLogger(__name__)

PLAYER_SCHEMA = (
    ("fpl_player_id", "BIGINT"),
    ("code", "BIGINT"),
    ("opta_code", "VARCHAR"),
    ("first_name", "VARCHAR"),
    ("second_name", "VARCHAR"),
    ("web_name", "VARCHAR"),
    ("team_id", "INTEGER"),
    ("team_name", "VARCHAR"),
    ("team_short_name", "VARCHAR"),
    ("position_id", "INTEGER"),
    ("position", "VARCHAR"),
    ("price_m", "DOUBLE"),
    ("ownership_pct", "DOUBLE"),
    ("status", "VARCHAR"),
    ("chance_of_playing_next_round", "SMALLINT"),
    ("news", "VARCHAR"),
    ("minutes", "INTEGER"),
    ("starts", "INTEGER"),
    ("total_points", "INTEGER"),
    ("event_points", "INTEGER"),
    ("points_per_game", "DOUBLE"),
    ("form", "DOUBLE"),
    ("bonus", "INTEGER"),
    ("bps", "INTEGER"),
    ("xg", "DOUBLE"),
    ("xa", "DOUBLE"),
    ("xgi", "DOUBLE"),
    ("xgc", "DOUBLE"),
    ("xg_per_90", "DOUBLE"),
    ("xa_per_90", "DOUBLE"),
    ("xgi_per_90", "DOUBLE"),
    ("xgc_per_90", "DOUBLE"),
    ("clearances_blocks_interceptions", "INTEGER"),
    ("recoveries", "INTEGER"),
    ("tackles", "INTEGER"),
    ("defensive_contribution", "INTEGER"),
    ("defensive_contribution_per_90", "DOUBLE"),
    ("penalties_order", "SMALLINT"),
    ("direct_freekicks_order", "SMALLINT"),
    ("corners_and_indirect_freekicks_order", "SMALLINT"),
    ("ep_this", "DOUBLE"),
    ("ep_next", "DOUBLE"),
    ("season", "VARCHAR"),
    ("source", "VARCHAR"),
    ("snapshot_timestamp", "VARCHAR"),
    ("source_snapshot", "VARCHAR"),
    ("source_sha256", "VARCHAR"),
)


class TransformationError(Exception):
    """Base exception for expected player-transformation failures."""


class RawSnapshotNotFoundError(TransformationError):
    """Raised when no raw bootstrap-static snapshot is available."""


class CleanOutputExistsError(TransformationError):
    """Raised rather than overwriting an existing clean snapshot."""


class DataQualityError(TransformationError):
    """Raised when source or clean data fails an analytical check."""


def find_latest_snapshot(raw_data_root: Path, season: str) -> Path:
    """Return the latest snapshot according to its sortable UTC directory name."""
    snapshots = list((raw_data_root / season).glob("*/bootstrap-static.json"))
    if not snapshots:
        raise RawSnapshotNotFoundError(
            f"no bootstrap-static snapshots found under {raw_data_root / season}"
        )
    return max(snapshots, key=lambda path: path.parent.name)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _numeric(value: Any, field: str, player_id: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DataQualityError(
            f"player {player_id!r} has non-numeric {field}: {value!r}"
        ) from exc
    if not math.isfinite(number):
        raise DataQualityError(
            f"player {player_id!r} has non-finite {field}: {value!r}"
        )
    return number


def _price_m(value: Any, player_id: Any) -> float | None:
    price = _numeric(value, "now_cost", player_id)
    return price / 10 if price is not None else None


def _player_row(
    player: dict[str, Any],
    teams: dict[int, dict[str, Any]],
    positions: dict[int, dict[str, Any]],
    *,
    season: str,
    snapshot_timestamp: str,
    source_snapshot: str,
    source_sha256: str,
) -> tuple[Any, ...]:
    player_id = player.get("id")
    team = teams.get(player.get("team"))
    position = positions.get(player.get("element_type"))

    return (
        player_id,
        player.get("code"),
        player.get("opta_code"),
        player.get("first_name"),
        player.get("second_name"),
        player.get("web_name"),
        player.get("team"),
        team.get("name") if team else None,
        team.get("short_name") if team else None,
        player.get("element_type"),
        position.get("singular_name") if position else None,
        _price_m(player.get("now_cost"), player_id),
        _numeric(player.get("selected_by_percent"), "selected_by_percent", player_id),
        player.get("status"),
        player.get("chance_of_playing_next_round"),
        player.get("news"),
        player.get("minutes"),
        player.get("starts"),
        player.get("total_points"),
        player.get("event_points"),
        _numeric(player.get("points_per_game"), "points_per_game", player_id),
        _numeric(player.get("form"), "form", player_id),
        player.get("bonus"),
        player.get("bps"),
        _numeric(player.get("expected_goals"), "expected_goals", player_id),
        _numeric(player.get("expected_assists"), "expected_assists", player_id),
        _numeric(
            player.get("expected_goal_involvements"),
            "expected_goal_involvements",
            player_id,
        ),
        _numeric(
            player.get("expected_goals_conceded"),
            "expected_goals_conceded",
            player_id,
        ),
        _numeric(player.get("expected_goals_per_90"), "expected_goals_per_90", player_id),
        _numeric(
            player.get("expected_assists_per_90"),
            "expected_assists_per_90",
            player_id,
        ),
        _numeric(
            player.get("expected_goal_involvements_per_90"),
            "expected_goal_involvements_per_90",
            player_id,
        ),
        _numeric(
            player.get("expected_goals_conceded_per_90"),
            "expected_goals_conceded_per_90",
            player_id,
        ),
        player.get("clearances_blocks_interceptions"),
        player.get("recoveries"),
        player.get("tackles"),
        player.get("defensive_contribution"),
        _numeric(
            player.get("defensive_contribution_per_90"),
            "defensive_contribution_per_90",
            player_id,
        ),
        player.get("penalties_order"),
        player.get("direct_freekicks_order"),
        player.get("corners_and_indirect_freekicks_order"),
        _numeric(player.get("ep_this"), "ep_this", player_id),
        _numeric(player.get("ep_next"), "ep_next", player_id),
        season,
        FPL_BOOTSTRAP_STATIC_URL,
        snapshot_timestamp,
        source_snapshot,
        source_sha256,
    )


def _validate_players(
    connection: duckdb.DuckDBPyConnection,
    *,
    expected_count: int,
) -> None:
    checks = (
        (
            "clean player count differs from raw player count",
            "SELECT count(*) = ? FROM players",
            [expected_count],
        ),
        (
            "fpl_player_id must be unique and non-null",
            """SELECT count(*) = count(fpl_player_id)
                       AND count(*) = count(DISTINCT fpl_player_id)
                FROM players""",
            [],
        ),
        (
            "one or more team_id values do not resolve to a valid FPL team",
            """SELECT count(*) = 0
                FROM players p
                LEFT JOIN valid_teams t ON p.team_id = t.id
                WHERE t.id IS NULL OR p.team_name IS NULL OR p.team_short_name IS NULL""",
            [],
        ),
        (
            "one or more position_id values do not resolve to a valid FPL position",
            """SELECT count(*) = 0
                FROM players p
                LEFT JOIN valid_positions t ON p.position_id = t.id
                WHERE t.id IS NULL OR p.position IS NULL""",
            [],
        ),
        (
            "price_m must be finite, numeric, non-null, and non-negative",
            "SELECT count(*) = 0 FROM players WHERE price_m IS NULL OR NOT isfinite(price_m) OR price_m < 0",
            [],
        ),
        (
            "ownership_pct must be finite and numeric when present",
            "SELECT count(*) = 0 FROM players WHERE ownership_pct IS NOT NULL AND NOT isfinite(ownership_pct)",
            [],
        ),
        (
            "expected-stat fields must be finite and numeric when present",
            """SELECT count(*) = 0 FROM players
                WHERE (xg IS NOT NULL AND NOT isfinite(xg))
                   OR (xa IS NOT NULL AND NOT isfinite(xa))
                   OR (xgi IS NOT NULL AND NOT isfinite(xgi))
                   OR (xgc IS NOT NULL AND NOT isfinite(xgc))
                   OR (xg_per_90 IS NOT NULL AND NOT isfinite(xg_per_90))
                   OR (xa_per_90 IS NOT NULL AND NOT isfinite(xa_per_90))
                   OR (xgi_per_90 IS NOT NULL AND NOT isfinite(xgi_per_90))
                   OR (xgc_per_90 IS NOT NULL AND NOT isfinite(xgc_per_90))""",
            [],
        ),
    )
    for message, query, parameters in checks:
        passed = connection.execute(query, parameters).fetchone()[0]
        if not passed:
            raise DataQualityError(message)


def transform_players_for_snapshot(
    *,
    raw_data_root: Path = Path("data/raw/fpl"),
    clean_data_root: Path = Path("data/clean/fpl"),
    season: str = "2026-27",
    snapshot_timestamp: str | None = None,
) -> Path:
    """Transform one explicit raw FPL snapshot into validated player Parquet."""
    if snapshot_timestamp is None:
        snapshot_path = find_latest_snapshot(raw_data_root, season)
    else:
        snapshot_path = (
            raw_data_root / season / snapshot_timestamp / "bootstrap-static.json"
        )
        if not snapshot_path.is_file():
            raise RawSnapshotNotFoundError(
                f"bootstrap-static snapshot does not exist: {snapshot_path}"
            )
    snapshot_timestamp = snapshot_path.parent.name
    output_dir = clean_data_root / season / snapshot_timestamp
    output_path = output_dir / "players.parquet"

    if output_path.exists():
        raise CleanOutputExistsError(
            f"clean output already exists and will not be overwritten: {output_path}"
        )

    logger.info("Transforming players from %s", snapshot_path)
    raw_sha256 = _sha256(snapshot_path)
    try:
        raw_data = json.loads(snapshot_path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TransformationError(f"could not read raw snapshot: {exc}") from exc

    players = raw_data.get("elements")
    raw_teams = raw_data.get("teams")
    raw_positions = raw_data.get("element_types")
    if not isinstance(players, list) or not isinstance(raw_teams, list) or not isinstance(raw_positions, list):
        raise DataQualityError(
            "raw snapshot must contain elements, teams, and element_types lists"
        )

    teams = {team["id"]: team for team in raw_teams}
    positions = {position["id"]: position for position in raw_positions}
    source_snapshot = snapshot_path.as_posix()
    rows = [
        _player_row(
            player,
            teams,
            positions,
            season=season,
            snapshot_timestamp=snapshot_timestamp,
            source_snapshot=source_snapshot,
            source_sha256=raw_sha256,
        )
        for player in players
    ]

    connection = duckdb.connect(":memory:")
    try:
        columns = ", ".join(f'"{name}" {data_type}' for name, data_type in PLAYER_SCHEMA)
        connection.execute(f"CREATE TABLE players ({columns})")
        placeholders = ", ".join("?" for _ in PLAYER_SCHEMA)
        connection.executemany(f"INSERT INTO players VALUES ({placeholders})", rows)
        connection.execute("CREATE TABLE valid_teams (id INTEGER)")
        connection.executemany(
            "INSERT INTO valid_teams VALUES (?)", [(team_id,) for team_id in teams]
        )
        connection.execute("CREATE TABLE valid_positions (id INTEGER)")
        connection.executemany(
            "INSERT INTO valid_positions VALUES (?)",
            [(position_id,) for position_id in positions],
        )
        _validate_players(connection, expected_count=len(players))

        if _sha256(snapshot_path) != raw_sha256:
            raise DataQualityError("raw snapshot changed during transformation")

        output_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_name(
            f".{output_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            connection.execute(
                "COPY players TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                [str(temporary_path)],
            )
            try:
                os.link(temporary_path, output_path)
            except FileExistsError as exc:
                raise CleanOutputExistsError(
                    "clean output already exists and will not be overwritten: "
                    f"{output_path}"
                ) from exc
        finally:
            temporary_path.unlink(missing_ok=True)
    finally:
        connection.close()

    logger.info("Player transformation succeeded with %d rows", len(players))
    logger.info("Clean player dataset saved to %s", output_path)
    return output_path


def transform_latest_players(
    *,
    raw_data_root: Path = Path("data/raw/fpl"),
    clean_data_root: Path = Path("data/clean/fpl"),
    season: str = "2026-27",
) -> Path:
    """Transform the latest raw FPL snapshot into a validated player Parquet."""
    return transform_players_for_snapshot(
        raw_data_root=raw_data_root,
        clean_data_root=clean_data_root,
        season=season,
        snapshot_timestamp=None,
    )
