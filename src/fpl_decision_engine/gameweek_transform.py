"""Transform official FPL fixtures and realized player history to Parquet."""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from pathlib import Path
from typing import Any

import duckdb

from .official_data import (
    FPL_FIXTURES_URL,
    OfficialDataError,
    resolve_bootstrap_snapshot,
)
from .transform import CleanOutputExistsError, DataQualityError

FIXTURE_SCHEMA = (
    ("fixture_id", "BIGINT"),
    ("code", "BIGINT"),
    ("gameweek_id", "INTEGER"),
    ("kickoff_time", "TIMESTAMPTZ"),
    ("started", "BOOLEAN"),
    ("finished", "BOOLEAN"),
    ("finished_provisional", "BOOLEAN"),
    ("provisional_start_time", "BOOLEAN"),
    ("minutes", "INTEGER"),
    ("home_team_id", "INTEGER"),
    ("home_team_name", "VARCHAR"),
    ("away_team_id", "INTEGER"),
    ("away_team_name", "VARCHAR"),
    ("home_score", "INTEGER"),
    ("away_score", "INTEGER"),
    ("home_difficulty", "INTEGER"),
    ("away_difficulty", "INTEGER"),
    ("pulse_id", "BIGINT"),
    ("gameweek_finished", "BOOLEAN"),
    ("gameweek_data_checked", "BOOLEAN"),
    ("gameweek_is_current", "BOOLEAN"),
    ("gameweek_is_next", "BOOLEAN"),
    ("season", "VARCHAR"),
    ("source", "VARCHAR"),
    ("snapshot_timestamp", "VARCHAR"),
    ("retrieved_at", "TIMESTAMPTZ"),
    ("source_snapshot", "VARCHAR"),
    ("source_sha256", "VARCHAR"),
    ("bootstrap_sha256", "VARCHAR"),
)

HISTORY_SCHEMA = (
    ("fpl_player_id", "BIGINT"),
    ("code", "BIGINT"),
    ("opta_code", "VARCHAR"),
    ("web_name", "VARCHAR"),
    ("snapshot_team_id", "INTEGER"),
    ("team_id", "INTEGER"),
    ("team_name", "VARCHAR"),
    ("position_id", "INTEGER"),
    ("position", "VARCHAR"),
    ("fixture_id", "BIGINT"),
    ("gameweek_id", "INTEGER"),
    ("opponent_team_id", "INTEGER"),
    ("opponent_team_name", "VARCHAR"),
    ("home_team_id", "INTEGER"),
    ("away_team_id", "INTEGER"),
    ("was_home", "BOOLEAN"),
    ("home_away", "VARCHAR"),
    ("kickoff_time", "TIMESTAMPTZ"),
    ("home_score", "INTEGER"),
    ("away_score", "INTEGER"),
    ("gameweek_finished", "BOOLEAN"),
    ("gameweek_data_checked", "BOOLEAN"),
    ("gameweek_is_current", "BOOLEAN"),
    ("gameweek_is_next", "BOOLEAN"),
    ("modified", "BOOLEAN"),
    ("minutes", "INTEGER"),
    ("starts", "INTEGER"),
    ("total_points", "INTEGER"),
    ("goals_scored", "INTEGER"),
    ("assists", "INTEGER"),
    ("clean_sheets", "INTEGER"),
    ("goals_conceded", "INTEGER"),
    ("bonus", "INTEGER"),
    ("bps", "INTEGER"),
    ("yellow_cards", "INTEGER"),
    ("red_cards", "INTEGER"),
    ("own_goals", "INTEGER"),
    ("penalties_missed", "INTEGER"),
    ("penalties_saved", "INTEGER"),
    ("saves", "INTEGER"),
    ("xg", "DOUBLE"),
    ("xa", "DOUBLE"),
    ("xgi", "DOUBLE"),
    ("xgc", "DOUBLE"),
    ("influence", "DOUBLE"),
    ("creativity", "DOUBLE"),
    ("threat", "DOUBLE"),
    ("ict_index", "DOUBLE"),
    ("clearances_blocks_interceptions", "INTEGER"),
    ("recoveries", "INTEGER"),
    ("tackles", "INTEGER"),
    ("defensive_contribution", "INTEGER"),
    ("price_m", "DOUBLE"),
    ("selected", "BIGINT"),
    ("transfers_balance", "BIGINT"),
    ("transfers_in", "BIGINT"),
    ("transfers_out", "BIGINT"),
    ("season", "VARCHAR"),
    ("source", "VARCHAR"),
    ("snapshot_timestamp", "VARCHAR"),
    ("retrieved_at", "TIMESTAMPTZ"),
    ("source_snapshot", "VARCHAR"),
    ("source_sha256", "VARCHAR"),
    ("bootstrap_sha256", "VARCHAR"),
    ("fixture_source_sha256", "VARCHAR"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise DataQualityError(f"could not read {path}: {exc}") from exc


def _number(value: Any, field: str, identifier: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DataQualityError(
            f"record {identifier!r} has non-numeric {field}: {value!r}"
        ) from exc
    if not math.isfinite(number):
        raise DataQualityError(
            f"record {identifier!r} has non-finite {field}: {value!r}"
        )
    return number


def _create_table(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    schema: tuple[tuple[str, str], ...],
) -> None:
    columns = ", ".join(f'"{name}" {data_type}' for name, data_type in schema)
    connection.execute(f'CREATE TABLE "{table}" ({columns})')


def _insert_rows(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    schema: tuple[tuple[str, str], ...],
    rows: list[tuple[Any, ...]],
) -> None:
    if not rows:
        return
    placeholders = ", ".join("?" for _ in schema)
    connection.executemany(
        f'INSERT INTO "{table}" VALUES ({placeholders})', rows
    )


def _copy_parquet_exclusive(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    output_path: Path,
) -> None:
    if output_path.exists():
        raise CleanOutputExistsError(
            f"clean output already exists and will not be overwritten: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(
        f".{output_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        connection.execute(
            f'COPY "{table}" TO ? (FORMAT PARQUET, COMPRESSION ZSTD)',
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


def _load_context(snapshot_path: Path) -> tuple[
    dict[str, Any],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
]:
    bootstrap = _load_json(snapshot_path)
    if not isinstance(bootstrap, dict):
        raise DataQualityError("bootstrap snapshot is not a JSON object")
    try:
        teams = {team["id"]: team for team in bootstrap["teams"]}
        events = {event["id"]: event for event in bootstrap["events"]}
        positions = {
            position["id"]: position for position in bootstrap["element_types"]
        }
    except (KeyError, TypeError) as exc:
        raise DataQualityError(
            "bootstrap snapshot is missing team, event, or position identifiers"
        ) from exc
    return bootstrap, teams, events, positions


def _fixture_rows(
    fixtures: list[dict[str, Any]],
    *,
    teams: dict[int, dict[str, Any]],
    events: dict[int, dict[str, Any]],
    season: str,
    snapshot_timestamp: str,
    retrieved_at: str,
    source_snapshot: str,
    source_sha256: str,
    bootstrap_sha256: str,
    source: str,
) -> list[tuple[Any, ...]]:
    rows = []
    for fixture in fixtures:
        event = events.get(fixture.get("event"))
        home_team = teams.get(fixture.get("team_h"))
        away_team = teams.get(fixture.get("team_a"))
        rows.append(
            (
                fixture.get("id"),
                fixture.get("code"),
                fixture.get("event"),
                fixture.get("kickoff_time"),
                fixture.get("started"),
                fixture.get("finished"),
                fixture.get("finished_provisional"),
                fixture.get("provisional_start_time"),
                fixture.get("minutes"),
                fixture.get("team_h"),
                home_team.get("name") if home_team else None,
                fixture.get("team_a"),
                away_team.get("name") if away_team else None,
                fixture.get("team_h_score"),
                fixture.get("team_a_score"),
                fixture.get("team_h_difficulty"),
                fixture.get("team_a_difficulty"),
                fixture.get("pulse_id"),
                event.get("finished") if event else None,
                event.get("data_checked") if event else None,
                event.get("is_current") if event else None,
                event.get("is_next") if event else None,
                season,
                source,
                snapshot_timestamp,
                retrieved_at,
                source_snapshot,
                source_sha256,
                bootstrap_sha256,
            )
        )
    return rows


def _validate_fixtures(
    connection: duckdb.DuckDBPyConnection, expected_count: int
) -> None:
    checks = (
        (
            "clean fixture count differs from raw fixture count",
            "SELECT count(*) = ? FROM fixtures",
            [expected_count],
        ),
        (
            "fixture IDs must be unique and non-null",
            """SELECT count(*) = count(fixture_id)
                       AND count(*) = count(DISTINCT fixture_id)
                FROM fixtures""",
            [],
        ),
        (
            "fixture team IDs must resolve to bootstrap teams",
            """SELECT count(*) = 0 FROM fixtures f
                LEFT JOIN valid_teams h ON f.home_team_id = h.id
                LEFT JOIN valid_teams a ON f.away_team_id = a.id
                WHERE h.id IS NULL OR a.id IS NULL
                   OR home_team_name IS NULL OR away_team_name IS NULL""",
            [],
        ),
        (
            "non-null fixture gameweek IDs must resolve to bootstrap events",
            """SELECT count(*) = 0 FROM fixtures f
                LEFT JOIN valid_events e ON f.gameweek_id = e.id
                WHERE f.gameweek_id IS NOT NULL AND e.id IS NULL""",
            [],
        ),
    )
    for message, query, parameters in checks:
        if not connection.execute(query, parameters).fetchone()[0]:
            raise DataQualityError(message)


def transform_fixtures_for_snapshot(
    *,
    raw_data_root: Path = Path("data/raw/fpl"),
    clean_data_root: Path = Path("data/clean/fpl"),
    season: str = "2026-27",
    snapshot_timestamp: str | None = None,
) -> Path:
    snapshot_path = resolve_bootstrap_snapshot(
        raw_data_root, season, snapshot_timestamp
    )
    snapshot_dir = snapshot_path.parent
    fixture_path = snapshot_dir / "fixtures.json"
    manifest_path = snapshot_dir / "fixtures.manifest.json"
    if not fixture_path.is_file() or not manifest_path.is_file():
        raise OfficialDataError(
            f"fixture response and manifest are required under {snapshot_dir}"
        )
    output_path = clean_data_root / season / snapshot_dir.name / "fixtures.parquet"
    if output_path.exists():
        raise CleanOutputExistsError(
            f"clean output already exists and will not be overwritten: {output_path}"
        )

    raw_hashes = {
        snapshot_path: _sha256(snapshot_path),
        fixture_path: _sha256(fixture_path),
        manifest_path: _sha256(manifest_path),
    }
    bootstrap, teams, events, _ = _load_context(snapshot_path)
    fixtures = _load_json(fixture_path)
    manifest = _load_json(manifest_path)
    if not isinstance(fixtures, list) or not isinstance(manifest, dict):
        raise DataQualityError("fixture response or manifest has an invalid shape")
    if manifest.get("status") != "complete":
        raise DataQualityError("fixture manifest is not complete")
    if manifest.get("response_sha256") != raw_hashes[fixture_path]:
        raise DataQualityError("fixture response does not match its manifest SHA-256")

    rows = _fixture_rows(
        fixtures,
        teams=teams,
        events=events,
        season=season,
        snapshot_timestamp=snapshot_dir.name,
        retrieved_at=manifest["retrieved_at"],
        source_snapshot=fixture_path.as_posix(),
        source_sha256=raw_hashes[fixture_path],
        bootstrap_sha256=raw_hashes[snapshot_path],
        source=manifest.get("source_endpoint", FPL_FIXTURES_URL),
    )

    connection = duckdb.connect(":memory:")
    try:
        _create_table(connection, "fixtures", FIXTURE_SCHEMA)
        _insert_rows(connection, "fixtures", FIXTURE_SCHEMA, rows)
        connection.execute("CREATE TABLE valid_teams (id INTEGER)")
        _insert_rows(
            connection,
            "valid_teams",
            (("id", "INTEGER"),),
            [(team_id,) for team_id in teams],
        )
        connection.execute("CREATE TABLE valid_events (id INTEGER)")
        _insert_rows(
            connection,
            "valid_events",
            (("id", "INTEGER"),),
            [(event_id,) for event_id in events],
        )
        _validate_fixtures(connection, len(fixtures))
        if any(_sha256(path) != digest for path, digest in raw_hashes.items()):
            raise DataQualityError("a raw source changed during fixture transformation")
        _copy_parquet_exclusive(connection, "fixtures", output_path)
    finally:
        connection.close()
    return output_path


def _history_row(
    history: dict[str, Any],
    *,
    player: dict[str, Any],
    position: dict[str, Any] | None,
    fixture: dict[str, Any],
    teams: dict[int, dict[str, Any]],
    events: dict[int, dict[str, Any]],
    season: str,
    snapshot_timestamp: str,
    retrieved_at: str,
    source: str,
    source_snapshot: str,
    source_sha256: str,
    bootstrap_sha256: str,
    fixture_source_sha256: str,
) -> tuple[Any, ...]:
    player_id = player["id"]
    fixture_id = history.get("fixture")
    was_home = history.get("was_home")
    if not isinstance(was_home, bool):
        raise DataQualityError(
            f"player {player_id} fixture {fixture_id} has invalid was_home"
        )
    home_team_id = fixture.get("team_h")
    away_team_id = fixture.get("team_a")
    team_id = home_team_id if was_home else away_team_id
    opponent_team_id = away_team_id if was_home else home_team_id
    if history.get("opponent_team") != opponent_team_id:
        raise DataQualityError(
            f"player {player_id} fixture {fixture_id} has inconsistent opponent/home-away data"
        )
    gameweek_id = history.get("round")
    if fixture.get("event") is not None and fixture.get("event") != gameweek_id:
        raise DataQualityError(
            f"player {player_id} fixture {fixture_id} has inconsistent gameweek data"
        )
    event = events.get(gameweek_id)
    team = teams.get(team_id)
    opponent = teams.get(opponent_team_id)

    return (
        player_id,
        player.get("code"),
        player.get("opta_code"),
        player.get("web_name"),
        player.get("team"),
        team_id,
        team.get("name") if team else None,
        player.get("element_type"),
        position.get("singular_name") if position else None,
        fixture_id,
        gameweek_id,
        opponent_team_id,
        opponent.get("name") if opponent else None,
        home_team_id,
        away_team_id,
        was_home,
        "H" if was_home else "A",
        fixture.get("kickoff_time") or history.get("kickoff_time"),
        history.get("team_h_score"),
        history.get("team_a_score"),
        event.get("finished") if event else None,
        event.get("data_checked") if event else None,
        event.get("is_current") if event else None,
        event.get("is_next") if event else None,
        history.get("modified"),
        history.get("minutes"),
        history.get("starts"),
        history.get("total_points"),
        history.get("goals_scored"),
        history.get("assists"),
        history.get("clean_sheets"),
        history.get("goals_conceded"),
        history.get("bonus"),
        history.get("bps"),
        history.get("yellow_cards"),
        history.get("red_cards"),
        history.get("own_goals"),
        history.get("penalties_missed"),
        history.get("penalties_saved"),
        history.get("saves"),
        _number(history.get("expected_goals"), "expected_goals", fixture_id),
        _number(history.get("expected_assists"), "expected_assists", fixture_id),
        _number(
            history.get("expected_goal_involvements"),
            "expected_goal_involvements",
            fixture_id,
        ),
        _number(
            history.get("expected_goals_conceded"),
            "expected_goals_conceded",
            fixture_id,
        ),
        _number(history.get("influence"), "influence", fixture_id),
        _number(history.get("creativity"), "creativity", fixture_id),
        _number(history.get("threat"), "threat", fixture_id),
        _number(history.get("ict_index"), "ict_index", fixture_id),
        history.get("clearances_blocks_interceptions"),
        history.get("recoveries"),
        history.get("tackles"),
        history.get("defensive_contribution"),
        (
            _number(history.get("value"), "value", fixture_id) / 10
            if history.get("value") is not None
            else None
        ),
        history.get("selected"),
        history.get("transfers_balance"),
        history.get("transfers_in"),
        history.get("transfers_out"),
        season,
        source,
        snapshot_timestamp,
        retrieved_at,
        source_snapshot,
        source_sha256,
        bootstrap_sha256,
        fixture_source_sha256,
    )


def _validate_history(
    connection: duckdb.DuckDBPyConnection, expected_count: int
) -> None:
    checks = (
        (
            "clean player-history count differs from raw history count",
            "SELECT count(*) = ? FROM player_history",
            [expected_count],
        ),
        (
            "player-history player/fixture keys must be unique and non-null",
            """SELECT count(*) = count(fpl_player_id)
                       AND count(*) = count(fixture_id)
                       AND count(*) = count(DISTINCT (fpl_player_id, fixture_id))
                FROM player_history""",
            [],
        ),
        (
            "player-history player IDs must resolve to the bootstrap snapshot",
            """SELECT count(*) = 0 FROM player_history h
                LEFT JOIN valid_players p ON h.fpl_player_id = p.id
                WHERE p.id IS NULL""",
            [],
        ),
        (
            "player-history fixture IDs must resolve to official fixtures",
            """SELECT count(*) = 0 FROM player_history h
                LEFT JOIN valid_fixtures f ON h.fixture_id = f.id
                WHERE f.id IS NULL""",
            [],
        ),
        (
            "player-history team IDs must resolve to bootstrap teams",
            """SELECT count(*) = 0 FROM player_history h
                LEFT JOIN valid_teams t ON h.team_id = t.id
                LEFT JOIN valid_teams o ON h.opponent_team_id = o.id
                WHERE t.id IS NULL OR o.id IS NULL
                   OR team_name IS NULL OR opponent_team_name IS NULL""",
            [],
        ),
        (
            "player-history gameweek IDs must resolve to bootstrap events",
            """SELECT count(*) = 0 FROM player_history h
                LEFT JOIN valid_events e ON h.gameweek_id = e.id
                WHERE h.gameweek_id IS NOT NULL AND e.id IS NULL""",
            [],
        ),
        (
            "player-history home/away derivation is inconsistent",
            """SELECT count(*) = 0 FROM player_history
                WHERE (was_home AND (home_away <> 'H' OR team_id <> home_team_id
                                     OR opponent_team_id <> away_team_id))
                   OR (NOT was_home AND (home_away <> 'A' OR team_id <> away_team_id
                                         OR opponent_team_id <> home_team_id))""",
            [],
        ),
        (
            "player-history expected statistics must be finite when present",
            """SELECT count(*) = 0 FROM player_history
                WHERE (xg IS NOT NULL AND NOT isfinite(xg))
                   OR (xa IS NOT NULL AND NOT isfinite(xa))
                   OR (xgi IS NOT NULL AND NOT isfinite(xgi))
                   OR (xgc IS NOT NULL AND NOT isfinite(xgc))
                   OR (price_m IS NOT NULL AND NOT isfinite(price_m))""",
            [],
        ),
    )
    for message, query, parameters in checks:
        if not connection.execute(query, parameters).fetchone()[0]:
            raise DataQualityError(message)


def transform_player_history_for_snapshot(
    *,
    raw_data_root: Path = Path("data/raw/fpl"),
    clean_data_root: Path = Path("data/clean/fpl"),
    season: str = "2026-27",
    snapshot_timestamp: str | None = None,
) -> Path:
    snapshot_path = resolve_bootstrap_snapshot(
        raw_data_root, season, snapshot_timestamp
    )
    snapshot_dir = snapshot_path.parent
    history_dir = snapshot_dir / "player_history"
    manifest_path = history_dir / "manifest.json"
    fixture_path = snapshot_dir / "fixtures.json"
    if not manifest_path.is_file() or not fixture_path.is_file():
        raise OfficialDataError(
            "complete player-history ingestion and fixtures are required before transformation"
        )
    output_path = (
        clean_data_root
        / season
        / snapshot_dir.name
        / "player_gameweek_history.parquet"
    )
    if output_path.exists():
        raise CleanOutputExistsError(
            f"clean output already exists and will not be overwritten: {output_path}"
        )

    bootstrap, teams, events, positions = _load_context(snapshot_path)
    players = {player["id"]: player for player in bootstrap["elements"]}
    fixtures_list = _load_json(fixture_path)
    manifest = _load_json(manifest_path)
    if not isinstance(fixtures_list, list) or not isinstance(manifest, dict):
        raise DataQualityError("fixture response or history manifest has invalid shape")
    if manifest.get("status") != "complete" or manifest.get("failure_count") != 0:
        failed_ids = [
            failure.get("fpl_player_id") for failure in manifest.get("failures", [])
        ]
        raise DataQualityError(
            f"player-history manifest is partial; failed player IDs: {failed_ids}"
        )
    expected_ids = set(manifest.get("expected_player_ids", []))
    if expected_ids != set(players):
        raise DataQualityError(
            "player-history manifest IDs do not match the bootstrap snapshot"
        )
    responses = {
        response["fpl_player_id"]: response
        for response in manifest.get("responses", [])
    }
    if set(responses) != expected_ids:
        raise DataQualityError(
            "player-history manifest does not contain one response for every player"
        )
    fixtures = {fixture["id"]: fixture for fixture in fixtures_list}

    raw_hashes = {
        snapshot_path: _sha256(snapshot_path),
        fixture_path: _sha256(fixture_path),
        manifest_path: _sha256(manifest_path),
    }
    rows: list[tuple[Any, ...]] = []
    for player_id in sorted(expected_ids):
        response_path = history_dir / f"{player_id}.json"
        if not response_path.is_file():
            raise DataQualityError(f"missing player-history response: {response_path}")
        response_hash = _sha256(response_path)
        raw_hashes[response_path] = response_hash
        response_metadata = responses[player_id]
        if response_metadata.get("response_sha256") != response_hash:
            raise DataQualityError(
                f"player {player_id} history does not match its manifest SHA-256"
            )
        payload = _load_json(response_path)
        if not isinstance(payload, dict) or not isinstance(payload.get("history"), list):
            raise DataQualityError(
                f"player {player_id} response does not contain a history list"
            )
        player = players[player_id]
        position = positions.get(player.get("element_type"))
        for history in payload["history"]:
            if history.get("element") != player_id:
                raise DataQualityError(
                    f"player {player_id} history contains a different element ID"
                )
            fixture = fixtures.get(history.get("fixture"))
            if fixture is None:
                raise DataQualityError(
                    f"player {player_id} history references unknown fixture {history.get('fixture')}"
                )
            rows.append(
                _history_row(
                    history,
                    player=player,
                    position=position,
                    fixture=fixture,
                    teams=teams,
                    events=events,
                    season=season,
                    snapshot_timestamp=snapshot_dir.name,
                    retrieved_at=response_metadata["retrieved_at"],
                    source=response_metadata["source_endpoint"],
                    source_snapshot=response_path.as_posix(),
                    source_sha256=response_hash,
                    bootstrap_sha256=raw_hashes[snapshot_path],
                    fixture_source_sha256=raw_hashes[fixture_path],
                )
            )

    connection = duckdb.connect(":memory:")
    try:
        _create_table(connection, "player_history", HISTORY_SCHEMA)
        _insert_rows(connection, "player_history", HISTORY_SCHEMA, rows)
        for table, identifiers in (
            ("valid_players", players),
            ("valid_fixtures", fixtures),
            ("valid_teams", teams),
            ("valid_events", events),
        ):
            connection.execute(f'CREATE TABLE "{table}" (id BIGINT)')
            _insert_rows(
                connection,
                table,
                (("id", "BIGINT"),),
                [(identifier,) for identifier in identifiers],
            )
        _validate_history(connection, len(rows))
        if any(_sha256(path) != digest for path, digest in raw_hashes.items()):
            raise DataQualityError(
                "a raw source changed during player-history transformation"
            )
        _copy_parquet_exclusive(connection, "player_history", output_path)
    finally:
        connection.close()
    return output_path
