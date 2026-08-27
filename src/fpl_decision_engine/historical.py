"""Pinned historical FPL ingestion for restricted/pseudo-backtesting."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import lzma
import math
import os
import shutil
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import duckdb

from .historical_sources import (
    APPROVED_HISTORICAL_SOURCES,
    HISTORICAL_CLASSIFICATION,
    PARSER_SCHEMA_VERSION,
    PRESEASON_PRIOR_COMMITS,
    HistoricalSource,
)
from .tls import network_error_reason, verified_urlopen

logger = logging.getLogger(__name__)

FIXTURE_ASSIGNMENT_CONTEXT = "finalized_fixture_assignment"
EXPECTED_STAT_SEMANTICS = "fixture_level_official_fpl_archive_value"
HISTORICAL_POINTS_CONTEXT = "actual_points_under_historical_season_rules"
HISTORY_CUTOFF_RULE = (
    "performance_gameweek < target_gameweek AND fixture_kickoff < target_deadline"
)
MATERIAL_RECONCILIATION_THRESHOLD = 0.05
DUPLICATE_VALIDATION_MODE = "exact_original_csv_record_bytes"
POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
GOAL_POINTS = {"GK": 10, "DEF": 6, "MID": 5, "FWD": 4}
APPROVED_COUNTS = {
    "2023-24": {"raw_player_rows": 29_725, "player_rows": 29_725, "duplicate_rows": 0, "minutes_rows": 11_384, "am_rows": 0, "am_elements": 0, "identity_rows": 865},
    "2024-25": {"raw_player_rows": 27_605, "player_rows": 27_283, "duplicate_rows": 0, "minutes_rows": 11_566, "am_rows": 322, "am_elements": 20, "identity_rows": 784},
    "2025-26": {"raw_player_rows": 29_757, "player_rows": 29_747, "duplicate_rows": 10, "minutes_rows": 11_492, "am_rows": 0, "am_elements": 0, "identity_rows": 841},
}


class HistoricalIngestionError(Exception):
    """Raised when pinned historical ingestion cannot safely continue."""


class HistoricalSourceHashError(HistoricalIngestionError):
    """Raised when source bytes do not match the audited digest."""


class HistoricalDataQualityError(HistoricalIngestionError):
    """Raised when historical source relationships or temporal rules fail."""


class HistoricalOutputExistsError(HistoricalIngestionError):
    """Raised rather than overwriting an immutable historical output."""


@dataclass(frozen=True)
class HistoricalBuildResult:
    directory: Path
    manifest_path: Path
    row_counts: dict[str, dict[str, int]]


Fetcher = Callable[[str], bytes]


PLAYER_FIXTURE_SCHEMA = (
    ("season", "VARCHAR"), ("element_id", "BIGINT"), ("code", "BIGINT"),
    ("fixture_id", "INTEGER"), ("gameweek", "INTEGER"),
    ("historical_position", "VARCHAR"), ("historical_team_id", "INTEGER"),
    ("historical_team", "VARCHAR"), ("opponent_team_id", "INTEGER"),
    ("was_home", "BOOLEAN"), ("home_away", "VARCHAR"),
    ("kickoff_time", "TIMESTAMPTZ"), ("minutes", "INTEGER"),
    ("starts", "INTEGER"), ("goals", "INTEGER"), ("assists", "INTEGER"),
    ("total_points", "INTEGER"), ("xg", "DOUBLE"), ("xa", "DOUBLE"),
    ("xgi", "DOUBLE"), ("xgc", "DOUBLE"), ("clean_sheets", "INTEGER"),
    ("goals_conceded", "INTEGER"), ("saves", "INTEGER"),
    ("penalties_saved", "INTEGER"), ("bonus", "INTEGER"), ("bps", "INTEGER"),
    ("yellow_cards", "INTEGER"), ("red_cards", "INTEGER"),
    ("own_goals", "INTEGER"), ("penalties_missed", "INTEGER"),
    ("price_value_tenths", "INTEGER"), ("selected", "BIGINT"),
    ("transfers_balance", "BIGINT"), ("transfers_in", "BIGINT"),
    ("transfers_out", "BIGINT"), ("actual_appearance_points_v01", "INTEGER"),
    ("actual_goal_points_v01", "INTEGER"), ("actual_assist_points_v01", "INTEGER"),
    ("actual_modeled_points_v01", "INTEGER"),
    ("actual_points_context", "VARCHAR"), ("expected_stat_semantics", "VARCHAR"),
    ("source_repository", "VARCHAR"), ("source_commit", "VARCHAR"),
    ("source_path", "VARCHAR"), ("source_sha256", "VARCHAR"),
    ("source_row_number", "BIGINT"),
)

FIXTURE_SCHEMA = (
    ("season", "VARCHAR"), ("fixture_id", "INTEGER"), ("gameweek", "INTEGER"),
    ("home_team_id", "INTEGER"), ("home_team_name", "VARCHAR"),
    ("away_team_id", "INTEGER"), ("away_team_name", "VARCHAR"),
    ("kickoff_time", "TIMESTAMPTZ"), ("home_score", "INTEGER"),
    ("away_score", "INTEGER"), ("finished", "BOOLEAN"),
    ("finished_provisional", "BOOLEAN"), ("fixture_assignment_context", "VARCHAR"),
    ("fixture_assignment_verified_predeadline", "BOOLEAN"),
    ("source_repository", "VARCHAR"), ("source_commit", "VARCHAR"),
    ("source_path", "VARCHAR"), ("source_sha256", "VARCHAR"),
)

IDENTITY_SCHEMA = (
    ("season", "VARCHAR"), ("element_id", "BIGINT"), ("code", "BIGINT"),
    ("display_name", "VARCHAR"), ("first_name", "VARCHAR"),
    ("second_name", "VARCHAR"), ("position_id", "INTEGER"),
    ("position", "VARCHAR"), ("team_id", "INTEGER"), ("team_name", "VARCHAR"),
    ("identity_metadata_context", "VARCHAR"), ("source_repository", "VARCHAR"),
    ("source_commit", "VARCHAR"), ("source_path", "VARCHAR"),
    ("source_sha256", "VARCHAR"),
)

PREDEADLINE_SCHEMA = (
    ("season", "VARCHAR"), ("target_gameweek", "INTEGER"),
    ("element_id", "BIGINT"), ("snapshot_timestamp", "TIMESTAMPTZ"),
    ("deadline", "TIMESTAMPTZ"), ("code", "BIGINT"), ("team_id", "INTEGER"),
    ("team_name", "VARCHAR"), ("position_id", "INTEGER"), ("position", "VARCHAR"),
    ("status", "VARCHAR"), ("chance_of_playing_next_round", "SMALLINT"),
    ("news", "VARCHAR"), ("price_value_tenths", "INTEGER"),
    ("ownership_pct", "DOUBLE"), ("transfers_in", "BIGINT"),
    ("transfers_out", "BIGINT"), ("transfers_in_event", "BIGINT"),
    ("transfers_out_event", "BIGINT"), ("cumulative_minutes", "INTEGER"),
    ("cumulative_xg", "DOUBLE"), ("cumulative_xa", "DOUBLE"),
    ("cumulative_xgi", "DOUBLE"), ("cumulative_xgc", "DOUBLE"),
    ("ep_next", "DOUBLE"), ("ep_next_context", "VARCHAR"),
    ("availability_known_pre_deadline", "BOOLEAN"),
    ("historical_classification", "VARCHAR"), ("source_repository", "VARCHAR"),
    ("source_commit", "VARCHAR"), ("source_path", "VARCHAR"),
    ("source_sha256", "VARCHAR"),
)

FEATURE_SCHEMA = (
    ("season", "VARCHAR"), ("target_gameweek", "INTEGER"),
    ("element_id", "BIGINT"), ("code", "BIGINT"),
    ("historical_position", "VARCHAR"), ("snapshot_team_id", "INTEGER"),
    ("snapshot_team_name", "VARCHAR"), ("target_fixture_id", "INTEGER"),
    ("target_fixture_count", "INTEGER"), ("target_opponent_team_id", "INTEGER"),
    ("target_home_away", "VARCHAR"), ("target_kickoff_time", "TIMESTAMPTZ"),
    ("fixture_assignment_context", "VARCHAR"), ("target_deadline", "TIMESTAMPTZ"),
    ("fixture_assignment_verified_predeadline", "BOOLEAN"),
    ("snapshot_timestamp", "TIMESTAMPTZ"), ("availability_status", "VARCHAR"),
    ("chance_of_playing_next_round", "SMALLINT"), ("availability_news", "VARCHAR"),
    ("availability_known_pre_deadline", "BOOLEAN"),
    ("restricted_mode_available", "BOOLEAN"),
    ("predeadline_enhanced_mode_available", "BOOLEAN"),
    ("prior_gameweeks_with_data", "INTEGER"), ("prior_fixture_rows", "INTEGER"),
    ("chronologically_excluded_prior_fixture_rows", "INTEGER"),
    ("prior_total_minutes", "INTEGER"), ("previous_gameweek", "INTEGER"),
    ("previous_gameweek_minutes_uncapped", "INTEGER"),
    ("previous_gw_context_status", "VARCHAR"),
    ("previous_gw_fixture_existed", "BOOLEAN"), ("previous_gw_played", "BOOLEAN"),
    ("previous_gw_zero_minutes", "BOOLEAN"),
    ("previous_gw_team_blank", "BOOLEAN"),
    ("previous_gw_player_not_in_universe", "BOOLEAN"),
    ("cumulative_prior_xg", "DOUBLE"), ("cumulative_prior_xa", "DOUBLE"),
    ("prior_xg_per_90", "DOUBLE"), ("prior_xa_per_90", "DOUBLE"),
    ("rolling_3_gameweeks_with_data", "INTEGER"),
    ("rolling_3_minutes", "INTEGER"), ("rolling_3_xg", "DOUBLE"),
    ("rolling_3_xa", "DOUBLE"), ("rolling_5_gameweeks_with_data", "INTEGER"),
    ("rolling_5_minutes", "INTEGER"), ("rolling_5_xg", "DOUBLE"),
    ("rolling_5_xa", "DOUBLE"), ("history_gameweek_max_used", "INTEGER"),
    ("history_latest_kickoff_used", "TIMESTAMPTZ"),
    ("history_cutoff_rule", "VARCHAR"), ("vaastav_xp_excluded", "BOOLEAN"),
    ("historical_classification", "VARCHAR"),
    ("predeadline_source_path", "VARCHAR"), ("predeadline_source_sha256", "VARCHAR"),
)

ACTUAL_SCHEMA = (
    ("season", "VARCHAR"), ("target_gameweek", "INTEGER"),
    ("element_id", "BIGINT"), ("historical_position", "VARCHAR"),
    ("actual_minutes", "INTEGER"), ("actual_appearance_points_v01", "INTEGER"),
    ("actual_goal_points_v01", "INTEGER"), ("actual_assist_points_v01", "INTEGER"),
    ("actual_modeled_points_v01", "INTEGER"),
    ("actual_points_under_historical_season_rules", "INTEGER"),
    ("actual_fixture_count", "INTEGER"),
    ("prediction_target_fixture_count", "INTEGER"),
    ("actual_team_ids", "INTEGER[]"),
    ("actual_team_changed_after_deadline", "BOOLEAN"),
    ("actuals_not_predictors", "BOOLEAN"),
)

RECONCILIATION_SCHEMA = (
    ("season", "VARCHAR"), ("element_id", "BIGINT"), ("code", "BIGINT"),
    ("display_name", "VARCHAR"), ("field", "VARCHAR"),
    ("fixture_sum", "DOUBLE"), ("season_total", "DOUBLE"),
    ("difference", "DOUBLE"), ("reporting_material", "BOOLEAN"),
    ("audit_classification", "VARCHAR"), ("audit_evidence", "VARCHAR"),
    ("resolution", "VARCHAR"),
)


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso_utc(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_fetcher(url: str) -> bytes:
    try:
        with verified_urlopen(url, timeout=60) as response:
            if not 200 <= response.status < 300:
                raise HistoricalIngestionError(
                    f"historical source returned HTTP {response.status}: {url}"
                )
            return response.read()
    except HTTPError as exc:
        raise HistoricalIngestionError(
            f"historical source returned HTTP {exc.code}: {url}"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HistoricalIngestionError(
            f"could not retrieve pinned historical source: {network_error_reason(exc)}"
        ) from exc


def _write_exclusive(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise HistoricalOutputExistsError(
                f"historical file already exists and will not be overwritten: {path}"
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _cache_source(
    source: HistoricalSource,
    *,
    raw_root: Path,
    fetcher: Fetcher,
    clock: Callable[[], datetime],
) -> tuple[Path, str, bool]:
    destination = raw_root / source.repository.replace("/", "_") / source.commit / source.path
    metadata_path = destination.with_name(f"{destination.name}.source.json")
    reused = destination.exists()
    if reused:
        observed = _sha256_path(destination)
        if observed != source.sha256:
            raise HistoricalSourceHashError(
                f"cached source hash mismatch for {source.path}: expected "
                f"{source.sha256}, observed {observed}"
            )
        if not metadata_path.is_file():
            raise HistoricalDataQualityError(
                f"cached historical source lacks retrieval metadata: {metadata_path}"
            )
        metadata = json.loads(metadata_path.read_bytes())
        retrieved_at = metadata.get("retrieved_at")
        if not isinstance(retrieved_at, str):
            raise HistoricalDataQualityError(
                f"cached historical source has invalid retrieval metadata: {metadata_path}"
            )
        return destination, retrieved_at, True

    body = fetcher(source.url)
    observed = _sha256_bytes(body)
    if observed != source.sha256:
        raise HistoricalSourceHashError(
            f"downloaded source hash mismatch for {source.path}: expected "
            f"{source.sha256}, observed {observed}"
        )
    retrieved_at = _iso_utc(clock())
    _write_exclusive(destination, body)
    metadata = {
        "repository": source.repository,
        "commit": source.commit,
        "source_path": source.path,
        "expected_sha256": source.sha256,
        "observed_sha256": observed,
        "retrieved_at": retrieved_at,
    }
    _write_exclusive(
        metadata_path,
        json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8") + b"\n",
    )
    return destination, retrieved_at, False


def _int(value: Any, *, field: str, nullable: bool = False) -> int | None:
    if value is None or value == "":
        if nullable:
            return None
        raise HistoricalDataQualityError(f"missing required integer field {field}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HistoricalDataQualityError(f"invalid integer {field}: {value!r}") from exc


def _number(value: Any, *, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HistoricalDataQualityError(f"invalid numeric {field}: {value!r}") from exc
    if not math.isfinite(number):
        raise HistoricalDataQualityError(f"non-finite numeric {field}: {value!r}")
    return number


def _utc_datetime(value: Any, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise HistoricalDataQualityError(f"invalid timestamp {field}: {value!r}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HistoricalDataQualityError(
            f"invalid timestamp {field}: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        raise HistoricalDataQualityError(
            f"timestamp lacks timezone {field}: {value!r}"
        )
    return parsed.astimezone(timezone.utc)


def _bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value == "True":
        return True
    if value == "False":
        return False
    raise HistoricalDataQualityError(f"invalid boolean {field}: {value!r}")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source_file:
        return list(csv.DictReader(source_file))


def _deduplicate_player_fixture_rows(
    rows: list[dict[str, str]], *, season: str,
    raw_record_bytes: list[bytes] | None = None,
) -> tuple[list[tuple[int, dict[str, str]]], list[dict[str, int]]]:
    """Accept one byte-equivalent CSV record per key; reject conflicting duplicates."""
    accepted: list[tuple[int, dict[str, str]]] = []
    first_by_key: dict[tuple[int, int], tuple[int, dict[str, str], bytes | None]] = {}
    rejected: list[dict[str, int]] = []
    for source_row_number, row in enumerate(rows, start=2):
        element_id = _int(row.get("element"), field="gameweek.element")
        fixture_id = _int(row.get("fixture"), field="gameweek.fixture")
        key = (element_id, fixture_id)
        raw_bytes = (
            raw_record_bytes[source_row_number - 2]
            if raw_record_bytes is not None
            else None
        )
        prior = first_by_key.get(key)
        if prior is None:
            first_by_key[key] = (source_row_number, row, raw_bytes)
            accepted.append((source_row_number, row))
            continue
        accepted_row_number, accepted_row, accepted_bytes = prior
        identical = (
            raw_bytes == accepted_bytes
            if raw_record_bytes is not None
            else row == accepted_row
        )
        if not identical:
            raise HistoricalDataQualityError(
                "non-identical duplicate player-fixture rows: "
                f"{season}/{element_id}/{fixture_id} at source rows "
                f"{accepted_row_number} and {source_row_number}"
            )
        rejected.append(
            {
                "element_id": element_id,
                "fixture_id": fixture_id,
                "accepted_source_row_number": accepted_row_number,
                "rejected_source_row_number": source_row_number,
            }
        )
    return accepted, rejected


def _source_for(
    sources: Iterable[HistoricalSource], season: str, kind: str
) -> HistoricalSource:
    matches = [source for source in sources if source.season == season and source.kind == kind]
    if len(matches) != 1:
        raise HistoricalDataQualityError(
            f"expected one {kind} source for {season}, found {len(matches)}"
        )
    return matches[0]


def _snapshot_timestamp(source_path: str) -> datetime:
    parts = Path(source_path).parts
    try:
        year, month, day = map(int, parts[-4:-1])
        hhmm = parts[-1].split(".")[0]
        return datetime(year, month, day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=timezone.utc)
    except (ValueError, IndexError) as exc:
        raise HistoricalDataQualityError(
            f"cannot derive snapshot timestamp from {source_path}"
        ) from exc


def _load_bootstrap(path: Path) -> dict[str, Any]:
    try:
        with lzma.open(path, "rt", encoding="utf-8") as source_file:
            payload = json.load(source_file)
    except (OSError, lzma.LZMAError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HistoricalDataQualityError(f"invalid compressed bootstrap {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise HistoricalDataQualityError(f"bootstrap must be a JSON object: {path}")
    return payload


def validate_predeadline_snapshot(
    payload: dict[str, Any], source: HistoricalSource
) -> tuple[datetime, datetime]:
    if source.gameweek is None:
        raise HistoricalDataQualityError("pre-deadline source lacks target gameweek")
    captured = _snapshot_timestamp(source.path)
    event = next(
        (event for event in payload.get("events", []) if event.get("id") == source.gameweek),
        None,
    )
    if not isinstance(event, dict):
        raise HistoricalDataQualityError(
            f"snapshot lacks target GW{source.gameweek}: {source.path}"
        )
    if event.get("is_next") is not True:
        raise HistoricalDataQualityError(
            f"target GW{source.gameweek} is not is_next in {source.path}"
        )
    try:
        deadline = _utc_datetime(event["deadline_time"], field="event.deadline_time")
    except (KeyError, HistoricalDataQualityError) as exc:
        raise HistoricalDataQualityError(
            f"snapshot has invalid target deadline: {source.path}"
        ) from exc
    if captured >= deadline:
        raise HistoricalDataQualityError(
            f"snapshot is not strictly pre-deadline for GW{source.gameweek}: {source.path}"
        )
    return captured, deadline


def _appearance_points(minutes: int) -> int:
    if minutes <= 0:
        return 0
    return 1 if minutes < 60 else 2


def _sum_nullable(values: Iterable[float | None]) -> float | None:
    materialized = list(values)
    if not materialized or any(value is None for value in materialized):
        return None
    return sum(value for value in materialized if value is not None)


def _write_parquet(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    schema: tuple[tuple[str, str], ...],
    rows: list[tuple[Any, ...]],
    path: Path,
) -> None:
    columns = ", ".join(f'"{name}" {data_type}' for name, data_type in schema)
    connection.execute(f'CREATE OR REPLACE TABLE "{table}" ({columns})')
    if rows:
        expressions: list[str] = []
        parameters: list[list[Any]] = []
        for index, (_, data_type) in enumerate(schema):
            values = [row[index] for row in rows]
            if data_type == "TIMESTAMPTZ":
                values = [
                    _iso_utc(value) if isinstance(value, datetime) else value
                    for value in values
                ]
                expressions.append("unnest(?::VARCHAR[])::TIMESTAMPTZ")
            else:
                expressions.append(f"unnest(?::{data_type}[])")
            parameters.append(values)
        connection.execute(
            f'INSERT INTO "{table}" SELECT {", ".join(expressions)}', parameters
        )
    connection.execute(
        f'COPY "{table}" TO ? (FORMAT PARQUET, COMPRESSION ZSTD)', [str(path)]
    )


def _parse_season_sources(
    season: str,
    *,
    sources: tuple[HistoricalSource, ...],
    cached: dict[HistoricalSource, Path],
    strict_approved: bool,
) -> dict[str, Any]:
    merged_source = _source_for(sources, season, "player_fixture")
    fixture_source = _source_for(sources, season, "fixtures")
    identity_source = _source_for(sources, season, "player_identity")
    team_source = _source_for(sources, season, "teams")

    team_rows = _read_csv(cached[team_source])
    teams = {_int(row.get("id"), field="team.id"): row for row in team_rows}
    if len(teams) != len(team_rows):
        raise HistoricalDataQualityError(f"duplicate team ID in {season}")

    raw_players = _read_csv(cached[identity_source])
    all_player_master = {
        _int(row.get("id"), field="player.id"): row for row in raw_players
    }
    player_ids: set[int] = set()
    player_codes: set[int] = set()
    player_master: dict[int, dict[str, Any]] = {}
    identities: list[tuple[Any, ...]] = []
    am_master_ids: set[int] = set()
    for row in raw_players:
        element_id = _int(row.get("id"), field="player.id")
        code = _int(row.get("code"), field="player.code")
        element_type = _int(row.get("element_type"), field="player.element_type")
        if element_id in player_ids:
            raise HistoricalDataQualityError(
                f"duplicate element ID {element_id} in {season} player identity"
            )
        player_ids.add(element_id)
        if element_type not in POSITIONS:
            if element_type == 5:
                am_master_ids.add(element_id)
                continue
            raise HistoricalDataQualityError(
                f"unknown element type {element_type} for {season} element {element_id}"
            )
        if code in player_codes:
            raise HistoricalDataQualityError(
                f"duplicate player code {code} in {season}"
            )
        player_codes.add(code)
        team_id = _int(row.get("team"), field="player.team")
        if team_id not in teams:
            raise HistoricalDataQualityError(
                f"identity element {element_id} has unresolved team {team_id}"
            )
        player_master[element_id] = row
        identities.append(
            (
                season,
                element_id,
                code,
                row.get("web_name"),
                row.get("first_name"),
                row.get("second_name"),
                element_type,
                POSITIONS[element_type],
                team_id,
                teams[team_id].get("name"),
                "end_of_season_identity_reference_not_prediction_state",
                identity_source.repository,
                identity_source.commit,
                identity_source.path,
                identity_source.sha256,
            )
        )

    raw_fixtures = _read_csv(cached[fixture_source])
    fixtures: list[tuple[Any, ...]] = []
    fixture_records: dict[int, dict[str, Any]] = {}
    fixtures_by_gameweek_team: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in raw_fixtures:
        fixture_id = _int(row.get("id"), field="fixture.id")
        if fixture_id in fixture_records:
            raise HistoricalDataQualityError(
                f"duplicate fixture ID {fixture_id} in {season}"
            )
        gameweek = _int(row.get("event"), field="fixture.event")
        home_team = _int(row.get("team_h"), field="fixture.team_h")
        away_team = _int(row.get("team_a"), field="fixture.team_a")
        if home_team not in teams or away_team not in teams:
            raise HistoricalDataQualityError(
                f"fixture {fixture_id} has an unresolved team"
            )
        kickoff_time = row.get("kickoff_time")
        record = {
            "fixture_id": fixture_id,
            "gameweek": gameweek,
            "home_team": home_team,
            "away_team": away_team,
            "kickoff_time": kickoff_time,
            "kickoff_at": _utc_datetime(
                kickoff_time, field=f"fixture[{fixture_id}].kickoff_time"
            ),
            "home_score": _int(row.get("team_h_score"), field="fixture.team_h_score", nullable=True),
            "away_score": _int(row.get("team_a_score"), field="fixture.team_a_score", nullable=True),
        }
        fixture_records[fixture_id] = record
        fixtures_by_gameweek_team[(gameweek, home_team)].append(record)
        fixtures_by_gameweek_team[(gameweek, away_team)].append(record)
        fixtures.append(
            (
                season,
                fixture_id,
                gameweek,
                home_team,
                teams[home_team].get("name"),
                away_team,
                teams[away_team].get("name"),
                row.get("kickoff_time"),
                record["home_score"],
                record["away_score"],
                _bool(row.get("finished"), field="fixture.finished"),
                _bool(row.get("finished_provisional"), field="fixture.finished_provisional"),
                FIXTURE_ASSIGNMENT_CONTEXT,
                False,
                fixture_source.repository,
                fixture_source.commit,
                fixture_source.path,
                fixture_source.sha256,
            )
        )

    raw_gameweeks = _read_csv(cached[merged_source])
    raw_record_bytes = cached[merged_source].read_bytes().splitlines()[1:]
    if len(raw_record_bytes) != len(raw_gameweeks):
        raise HistoricalDataQualityError(
            f"multi-line or malformed CSV records prevent byte audit for {season}"
        )
    accepted_gameweeks, duplicate_rows = _deduplicate_player_fixture_rows(
        raw_gameweeks, season=season, raw_record_bytes=raw_record_bytes
    )
    player_fixtures: list[tuple[Any, ...]] = []
    player_fixture_records: list[dict[str, Any]] = []
    am_rows = 0
    am_elements: set[int] = set()
    keys: set[tuple[int, int]] = set()
    for source_row_number, row in accepted_gameweeks:
        position = row.get("position")
        element_id = _int(row.get("element"), field="gameweek.element")
        if position not in GOAL_POINTS:
            master = all_player_master.get(element_id)
            is_am = position == "AM" or (
                isinstance(master, dict) and master.get("element_type") == "5"
            )
            if is_am:
                am_rows += 1
                am_elements.add(element_id)
                continue
            raise HistoricalDataQualityError(
                f"unknown modelling position {position!r} at {season} row {source_row_number}"
            )
        if element_id not in player_master:
            raise HistoricalDataQualityError(
                f"gameweek row has unresolved player {element_id} in {season}"
            )
        fixture_id = _int(row.get("fixture"), field="gameweek.fixture")
        key = (element_id, fixture_id)
        if key in keys:
            raise HistoricalDataQualityError(f"internal duplicate key {season}/{element_id}/{fixture_id}")
        keys.add(key)
        fixture = fixture_records.get(fixture_id)
        if fixture is None:
            raise HistoricalDataQualityError(
                f"player row has unresolved fixture {fixture_id} in {season}"
            )
        gameweek = _int(row.get("round"), field="gameweek.round")
        if gameweek != fixture["gameweek"]:
            raise HistoricalDataQualityError(
                f"player row gameweek differs from fixture event: {season}/{element_id}/{fixture_id}"
            )
        was_home = _bool(row.get("was_home"), field="gameweek.was_home")
        team_id = fixture["home_team"] if was_home else fixture["away_team"]
        opponent_id = fixture["away_team"] if was_home else fixture["home_team"]
        if row.get("team") != teams[team_id].get("name"):
            raise HistoricalDataQualityError(
                f"historical team does not match fixture: {season}/{element_id}/{fixture_id}"
            )
        if _int(row.get("opponent_team"), field="gameweek.opponent_team") != opponent_id:
            raise HistoricalDataQualityError(
                f"opponent does not match fixture: {season}/{element_id}/{fixture_id}"
            )
        if row.get("kickoff_time") != fixture["kickoff_time"]:
            raise HistoricalDataQualityError(
                f"kickoff does not match fixture: {season}/{element_id}/{fixture_id}"
            )
        minutes = _int(row.get("minutes"), field="gameweek.minutes")
        goals = _int(row.get("goals_scored"), field="gameweek.goals")
        assists = _int(row.get("assists"), field="gameweek.assists")
        appearance_points = _appearance_points(minutes)
        goal_points = goals * GOAL_POINTS[position]
        assist_points = assists * 3
        xg = _number(row.get("expected_goals"), field="gameweek.expected_goals")
        xa = _number(row.get("expected_assists"), field="gameweek.expected_assists")
        xgi = _number(
            row.get("expected_goal_involvements"),
            field="gameweek.expected_goal_involvements",
        )
        xgc = _number(
            row.get("expected_goals_conceded"),
            field="gameweek.expected_goals_conceded",
        )
        record = {
            "season": season,
            "element_id": element_id,
            "fixture_id": fixture_id,
            "gameweek": gameweek,
            "position": position,
            "team_id": team_id,
            "kickoff_at": fixture["kickoff_at"],
            "minutes": minutes,
            "xg": xg,
            "xa": xa,
            "xgi": xgi,
            "xgc": xgc,
            "goals": goals,
            "assists": assists,
            "total_points": _int(row.get("total_points"), field="gameweek.total_points"),
            "appearance_points": appearance_points,
            "goal_points": goal_points,
            "assist_points": assist_points,
        }
        player_fixture_records.append(record)
        player_fixtures.append(
            (
                season,
                element_id,
                _int(player_master[element_id].get("code"), field="player.code"),
                fixture_id,
                gameweek,
                position,
                team_id,
                row.get("team"),
                opponent_id,
                was_home,
                "H" if was_home else "A",
                row.get("kickoff_time"),
                minutes,
                _int(row.get("starts"), field="gameweek.starts"),
                goals,
                assists,
                record["total_points"],
                xg,
                xa,
                xgi,
                xgc,
                _int(row.get("clean_sheets"), field="gameweek.clean_sheets"),
                _int(row.get("goals_conceded"), field="gameweek.goals_conceded"),
                _int(row.get("saves"), field="gameweek.saves"),
                _int(row.get("penalties_saved"), field="gameweek.penalties_saved"),
                _int(row.get("bonus"), field="gameweek.bonus"),
                _int(row.get("bps"), field="gameweek.bps"),
                _int(row.get("yellow_cards"), field="gameweek.yellow_cards"),
                _int(row.get("red_cards"), field="gameweek.red_cards"),
                _int(row.get("own_goals"), field="gameweek.own_goals"),
                _int(row.get("penalties_missed"), field="gameweek.penalties_missed"),
                _int(row.get("value"), field="gameweek.value"),
                _int(row.get("selected"), field="gameweek.selected"),
                _int(row.get("transfers_balance"), field="gameweek.transfers_balance"),
                _int(row.get("transfers_in"), field="gameweek.transfers_in"),
                _int(row.get("transfers_out"), field="gameweek.transfers_out"),
                appearance_points,
                goal_points,
                assist_points,
                appearance_points + goal_points + assist_points,
                HISTORICAL_POINTS_CONTEXT,
                EXPECTED_STAT_SEMANTICS,
                merged_source.repository,
                merged_source.commit,
                merged_source.path,
                merged_source.sha256,
                source_row_number,
            )
        )

    minutes_rows = sum(record["minutes"] > 0 for record in player_fixture_records)
    if strict_approved:
        expected = APPROVED_COUNTS[season]
        observed = {
            "raw_player_rows": len(raw_gameweeks),
            "player_rows": len(player_fixtures),
            "duplicate_rows": len(duplicate_rows),
            "minutes_rows": minutes_rows,
            "am_rows": am_rows,
            "am_elements": len(am_elements),
            "identity_rows": len(identities),
        }
        if observed != expected:
            raise HistoricalDataQualityError(
                f"audited row counts changed for {season}: expected {expected}, observed {observed}"
            )
        if len(fixtures) != 380:
            raise HistoricalDataQualityError(
                f"audited fixture count changed for {season}: {len(fixtures)}"
            )

    return {
        "season": season,
        "teams": teams,
        "raw_players": raw_players,
        "player_master": player_master,
        "identities": identities,
        "fixtures": fixtures,
        "fixture_records": fixture_records,
        "fixtures_by_gameweek_team": fixtures_by_gameweek_team,
        "player_fixtures": player_fixtures,
        "player_fixture_records": player_fixture_records,
        "raw_player_fixture_rows": len(raw_gameweeks),
        "duplicate_rows": duplicate_rows,
        "am_rows": am_rows,
        "am_elements": len(am_elements),
        "minutes_rows": minutes_rows,
        "sources": {
            "merged": merged_source,
            "fixtures": fixture_source,
            "identity": identity_source,
            "teams": team_source,
        },
    }


def _parse_predeadline_states(
    season: str,
    *,
    sources: tuple[HistoricalSource, ...],
    cached: dict[HistoricalSource, Path],
    strict_approved: bool,
) -> tuple[list[tuple[Any, ...]], list[dict[str, Any]], dict[int, dict[str, Any]]]:
    snapshot_sources = sorted(
        (
            source
            for source in sources
            if source.season == season and source.kind == "predeadline_bootstrap"
        ),
        key=lambda source: source.gameweek or 0,
    )
    if strict_approved and [source.gameweek for source in snapshot_sources] != list(range(1, 39)):
        raise HistoricalDataQualityError(
            f"approved pre-deadline coverage is not exactly GW1-38 for {season}"
        )
    output_rows: list[tuple[Any, ...]] = []
    records: list[dict[str, Any]] = []
    snapshot_stats: dict[int, dict[str, Any]] = {}
    for source in snapshot_sources:
        payload = _load_bootstrap(cached[source])
        captured, deadline = validate_predeadline_snapshot(payload, source)
        raw_teams = payload.get("teams")
        raw_positions = payload.get("element_types")
        raw_elements = payload.get("elements")
        if not all(isinstance(value, list) for value in (raw_teams, raw_positions, raw_elements)):
            raise HistoricalDataQualityError(
                f"snapshot lacks teams, element_types, or elements: {source.path}"
            )
        teams = {team.get("id"): team for team in raw_teams}
        position_names = {
            position.get("id"): position.get("singular_name") for position in raw_positions
        }
        accepted = 0
        rejected_am = 0
        keys: set[int] = set()
        for player in raw_elements:
            element_id = _int(player.get("id"), field="snapshot.player.id")
            position_id = _int(
                player.get("element_type"), field="snapshot.player.element_type"
            )
            if position_id not in POSITIONS:
                if position_id == 5:
                    rejected_am += 1
                    continue
                raise HistoricalDataQualityError(
                    f"unknown snapshot position {position_id}: {source.path}"
                )
            if element_id in keys:
                raise HistoricalDataQualityError(
                    f"duplicate player {element_id} in snapshot {source.path}"
                )
            keys.add(element_id)
            team_id = _int(player.get("team"), field="snapshot.player.team")
            team = teams.get(team_id)
            if not isinstance(team, dict):
                raise HistoricalDataQualityError(
                    f"unresolved snapshot team {team_id}: {source.path}"
                )
            record = {
                "season": season,
                "target_gameweek": source.gameweek,
                "element_id": element_id,
                "snapshot_timestamp": captured,
                "deadline": deadline,
                "code": _int(player.get("code"), field="snapshot.player.code"),
                "team_id": team_id,
                "team_name": team.get("name"),
                "position_id": position_id,
                "position": POSITIONS[position_id],
                "status": player.get("status"),
                "chance": _int(
                    player.get("chance_of_playing_next_round"),
                    field="chance_of_playing_next_round",
                    nullable=True,
                ),
                "news": player.get("news"),
                "source_path": source.path,
                "source_sha256": source.sha256,
            }
            records.append(record)
            output_rows.append(
                (
                    season,
                    source.gameweek,
                    element_id,
                    captured,
                    deadline,
                    record["code"],
                    team_id,
                    team.get("name"),
                    position_id,
                    POSITIONS[position_id],
                    player.get("status"),
                    record["chance"],
                    player.get("news"),
                    _int(player.get("now_cost"), field="snapshot.player.now_cost"),
                    _number(
                        player.get("selected_by_percent"),
                        field="snapshot.player.selected_by_percent",
                    ),
                    _int(player.get("transfers_in"), field="snapshot.player.transfers_in"),
                    _int(player.get("transfers_out"), field="snapshot.player.transfers_out"),
                    _int(
                        player.get("transfers_in_event"),
                        field="snapshot.player.transfers_in_event",
                    ),
                    _int(
                        player.get("transfers_out_event"),
                        field="snapshot.player.transfers_out_event",
                    ),
                    _int(player.get("minutes"), field="snapshot.player.minutes"),
                    _number(player.get("expected_goals"), field="snapshot.player.xg"),
                    _number(player.get("expected_assists"), field="snapshot.player.xa"),
                    _number(
                        player.get("expected_goal_involvements"),
                        field="snapshot.player.xgi",
                    ),
                    _number(
                        player.get("expected_goals_conceded"),
                        field="snapshot.player.xgc",
                    ),
                    _number(player.get("ep_next"), field="snapshot.player.ep_next"),
                    "benchmark_only_not_model_input",
                    True,
                    HISTORICAL_CLASSIFICATION,
                    source.repository,
                    source.commit,
                    source.path,
                    source.sha256,
                )
            )
            accepted += 1
        snapshot_stats[source.gameweek or 0] = {
            "accepted_records": accepted,
            "rejected_assistant_manager_records": rejected_am,
            "snapshot_timestamp": captured,
            "deadline": deadline,
            "source_path": source.path,
            "source_sha256": source.sha256,
        }
    return output_rows, records, snapshot_stats


def _aggregate_gameweek_records(
    records: list[dict[str, Any]],
) -> dict[tuple[str, int], dict[int, dict[str, Any]]]:
    grouped: dict[tuple[str, int], dict[int, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for record in records:
        grouped[(record["season"], record["element_id"])][record["gameweek"]].append(record)
    output: dict[tuple[str, int], dict[int, dict[str, Any]]] = defaultdict(dict)
    for player_key, by_gameweek in grouped.items():
        for gameweek, rows in by_gameweek.items():
            output[player_key][gameweek] = {
                "fixture_rows": len(rows),
                "fixture_ids": sorted(row["fixture_id"] for row in rows),
                "team_ids": sorted({row["team_id"] for row in rows}),
                "minutes": sum(row["minutes"] for row in rows),
                "xg": _sum_nullable(row["xg"] for row in rows),
                "xa": _sum_nullable(row["xa"] for row in rows),
                "goals": sum(row["goals"] for row in rows),
                "assists": sum(row["assists"] for row in rows),
                "total_points": sum(row["total_points"] for row in rows),
                "appearance_points": sum(row["appearance_points"] for row in rows),
                "goal_points": sum(row["goal_points"] for row in rows),
                "assist_points": sum(row["assist_points"] for row in rows),
            }
    return output


def _audit_chronological_exclusions(
    season_data: dict[str, Any], state_records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """List finalized lower-event fixtures that were not prior to a target deadline."""
    target_deadlines: dict[int, datetime] = {}
    for state in state_records:
        target = state["target_gameweek"]
        deadline = state["deadline"]
        existing = target_deadlines.setdefault(target, deadline)
        if existing != deadline:
            raise HistoricalDataQualityError(
                f"conflicting target deadlines for {state['season']}/GW{target}"
            )
    player_rows_by_fixture: dict[int, int] = defaultdict(int)
    for record in season_data["player_fixture_records"]:
        player_rows_by_fixture[record["fixture_id"]] += 1

    cases: list[dict[str, Any]] = []
    for target, deadline in sorted(target_deadlines.items()):
        for fixture in sorted(
            season_data["fixture_records"].values(),
            key=lambda item: item["fixture_id"],
        ):
            if (
                fixture["gameweek"] < target
                and fixture["kickoff_at"] >= deadline
            ):
                cases.append(
                    {
                        "season": state_records[0]["season"],
                        "target_gameweek": target,
                        "prior_gameweek": fixture["gameweek"],
                        "fixture_id": fixture["fixture_id"],
                        "home_team_id": fixture["home_team"],
                        "away_team_id": fixture["away_team"],
                        "kickoff_time": _iso_utc(fixture["kickoff_at"]),
                        "target_deadline": _iso_utc(deadline),
                        "player_fixture_rows_excluded": player_rows_by_fixture[
                            fixture["fixture_id"]
                        ],
                    }
                )
    return cases


def _window_aggregate(
    history: dict[int, dict[str, Any]], gameweeks: list[int]
) -> dict[str, Any]:
    rows = [history[gameweek] for gameweek in gameweeks]
    return {
        "gameweeks": len(rows),
        "fixture_rows": sum(row["fixture_rows"] for row in rows),
        "minutes": sum(row["minutes"] for row in rows) if rows else None,
        "xg": _sum_nullable(row["xg"] for row in rows),
        "xa": _sum_nullable(row["xa"] for row in rows),
    }


def _previous_context(
    state: dict[str, Any],
    *,
    states_by_gameweek: dict[int, dict[int, dict[str, Any]]],
    history: dict[int, dict[str, Any]],
    fixtures_by_gameweek_team: dict[tuple[int, int], list[dict[str, Any]]],
) -> dict[str, Any]:
    target = state["target_gameweek"]
    if target == 1:
        return {
            "previous_gameweek": None,
            "minutes": None,
            "status": "no_prior_gameweek_in_scope",
            "fixture_existed": False,
            "played": False,
            "zero_minutes": False,
            "team_blank": False,
            "not_in_universe": False,
        }
    previous_gameweek = target - 1
    previous_state = states_by_gameweek.get(previous_gameweek, {}).get(state["element_id"])
    if previous_state is None:
        return {
            "previous_gameweek": previous_gameweek,
            "minutes": None,
            "status": "player_not_in_previous_predeadline_universe",
            "fixture_existed": False,
            "played": False,
            "zero_minutes": False,
            "team_blank": False,
            "not_in_universe": True,
        }
    team_fixtures = fixtures_by_gameweek_team.get(
        (previous_gameweek, previous_state["team_id"]), []
    )
    if not team_fixtures:
        return {
            "previous_gameweek": previous_gameweek,
            "minutes": None,
            "status": "verified_team_blank",
            "fixture_existed": False,
            "played": False,
            "zero_minutes": False,
            "team_blank": True,
            "not_in_universe": False,
        }
    eligible_team_fixtures = [
        fixture
        for fixture in team_fixtures
        if fixture["kickoff_at"] < state["deadline"]
    ]
    later_team_fixtures = len(team_fixtures) - len(eligible_team_fixtures)
    if not eligible_team_fixtures:
        return {
            "previous_gameweek": previous_gameweek,
            "minutes": None,
            "status": "fixture_not_kicked_off_before_target_deadline",
            "fixture_existed": True,
            "played": False,
            "zero_minutes": False,
            "team_blank": False,
            "not_in_universe": False,
        }
    previous_history = history.get(previous_gameweek)
    if previous_history is None:
        raise HistoricalDataQualityError(
            "missing/corrupt previous-GW player data where finalized fixture exists: "
            f"{state['season']}/GW{previous_gameweek}/element {state['element_id']}"
        )
    previous_team_changed = set(previous_history["team_ids"]) != {
        previous_state["team_id"]
    }
    if (
        previous_history["fixture_rows"] != len(eligible_team_fixtures)
        and not previous_team_changed
    ):
        raise HistoricalDataQualityError(
            "previous-GW player fixture count differs from team fixture count: "
            f"{state['season']}/GW{previous_gameweek}/element {state['element_id']}"
        )
    minutes = previous_history["minutes"]
    if previous_team_changed:
        context_status = (
            "played_after_deadline_team_change"
            if minutes > 0
            else "zero_minutes_after_deadline_team_change"
        )
    elif later_team_fixtures:
        context_status = (
            "partially_played_before_target_deadline"
            if minutes > 0
            else "partial_gameweek_zero_minutes_before_target_deadline"
        )
    else:
        context_status = "played" if minutes > 0 else "fixture_existed_zero_minutes"
    return {
        "previous_gameweek": previous_gameweek,
        "minutes": minutes,
        "status": context_status,
        "fixture_existed": True,
        "played": minutes > 0,
        "zero_minutes": minutes == 0,
        "team_blank": False,
        "not_in_universe": False,
    }


def _build_features_and_actuals(
    season_data: dict[str, Any],
    state_records: list[dict[str, Any]],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    states_by_gameweek: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    for state in state_records:
        states_by_gameweek[state["target_gameweek"]][state["element_id"]] = state
    full_history_by_player = _aggregate_gameweek_records(
        season_data["player_fixture_records"]
    )
    records_by_player: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for record in season_data["player_fixture_records"]:
        records_by_player[(record["season"], record["element_id"])].append(record)
    features: list[tuple[Any, ...]] = []
    actuals: list[tuple[Any, ...]] = []
    for state in state_records:
        player_key = (state["season"], state["element_id"])
        target = state["target_gameweek"]
        logically_prior_records = [
            record
            for record in records_by_player.get(player_key, [])
            if record["gameweek"] < target
        ]
        eligible_prior_records = [
            record
            for record in logically_prior_records
            if record["kickoff_at"] < state["deadline"]
        ]
        chronologically_excluded = len(logically_prior_records) - len(
            eligible_prior_records
        )
        history = _aggregate_gameweek_records(eligible_prior_records).get(
            player_key, {}
        )
        prior_gameweeks = sorted(history)
        if any(gameweek >= target for gameweek in prior_gameweeks):
            raise HistoricalDataQualityError("target gameweek leaked into prior history")
        prior = _window_aggregate(history, prior_gameweeks)
        rolling_3_gameweeks = [
            gameweek for gameweek in prior_gameweeks if gameweek >= target - 3
        ]
        rolling_5_gameweeks = [
            gameweek for gameweek in prior_gameweeks if gameweek >= target - 5
        ]
        rolling_3 = _window_aggregate(history, rolling_3_gameweeks)
        rolling_5 = _window_aggregate(history, rolling_5_gameweeks)
        previous = _previous_context(
            state,
            states_by_gameweek=states_by_gameweek,
            history=history,
            fixtures_by_gameweek_team=season_data["fixtures_by_gameweek_team"],
        )
        prior_minutes = prior["minutes"]
        prior_xg_per_90 = (
            prior["xg"] * 90.0 / prior_minutes
            if prior_minutes and prior["xg"] is not None
            else None
        )
        prior_xa_per_90 = (
            prior["xa"] * 90.0 / prior_minutes
            if prior_minutes and prior["xa"] is not None
            else None
        )
        target_fixtures = season_data["fixtures_by_gameweek_team"].get(
            (target, state["team_id"]), []
        )
        fixture_contexts: list[dict[str, Any] | None] = target_fixtures or [None]
        for fixture in fixture_contexts:
            opponent = None
            home_away = None
            if fixture is not None:
                if fixture["home_team"] == state["team_id"]:
                    opponent = fixture["away_team"]
                    home_away = "H"
                elif fixture["away_team"] == state["team_id"]:
                    opponent = fixture["home_team"]
                    home_away = "A"
                else:
                    raise HistoricalDataQualityError("target fixture/team join failed")
            features.append(
                (
                    state["season"],
                    target,
                    state["element_id"],
                    state["code"],
                    state["position"],
                    state["team_id"],
                    state["team_name"],
                    fixture["fixture_id"] if fixture else None,
                    len(target_fixtures),
                    opponent,
                    home_away,
                    fixture["kickoff_time"] if fixture else None,
                    FIXTURE_ASSIGNMENT_CONTEXT,
                    state["deadline"],
                    False,
                    state["snapshot_timestamp"],
                    state["status"],
                    state["chance"],
                    state["news"],
                    True,
                    True,
                    True,
                    prior["gameweeks"],
                    prior["fixture_rows"],
                    chronologically_excluded,
                    prior_minutes,
                    previous["previous_gameweek"],
                    previous["minutes"],
                    previous["status"],
                    previous["fixture_existed"],
                    previous["played"],
                    previous["zero_minutes"],
                    previous["team_blank"],
                    previous["not_in_universe"],
                    prior["xg"],
                    prior["xa"],
                    prior_xg_per_90,
                    prior_xa_per_90,
                    rolling_3["gameweeks"],
                    rolling_3["minutes"],
                    rolling_3["xg"],
                    rolling_3["xa"],
                    rolling_5["gameweeks"],
                    rolling_5["minutes"],
                    rolling_5["xg"],
                    rolling_5["xa"],
                    max(prior_gameweeks) if prior_gameweeks else None,
                    max(
                        (record["kickoff_at"] for record in eligible_prior_records),
                        default=None,
                    ),
                    HISTORY_CUTOFF_RULE,
                    True,
                    HISTORICAL_CLASSIFICATION,
                    state["source_path"],
                    state["source_sha256"],
                )
            )

        target_history = full_history_by_player.get(player_key, {}).get(target)
        if target_fixtures and target_history is None:
            raise HistoricalDataQualityError(
                f"target actual player rows missing for {state['season']}/GW{target}/"
                f"element {state['element_id']}"
            )
        actual_team_changed = bool(
            target_history
            and set(target_history["team_ids"]) != {state["team_id"]}
        )
        if (
            target_history is not None
            and target_history["fixture_rows"] != len(target_fixtures)
            and not actual_team_changed
        ):
            raise HistoricalDataQualityError(
                f"target actual fixture count mismatch for {state['season']}/GW{target}/"
                f"element {state['element_id']}"
            )
        goals = target_history["goals"] if target_history else 0
        assists = target_history["assists"] if target_history else 0
        actual_minutes = target_history["minutes"] if target_history else 0
        appearance = target_history["appearance_points"] if target_history else 0
        goal_points = goals * GOAL_POINTS[state["position"]]
        assist_points = assists * 3
        actuals.append(
            (
                state["season"],
                target,
                state["element_id"],
                state["position"],
                actual_minutes,
                appearance,
                goal_points,
                assist_points,
                appearance + goal_points + assist_points,
                target_history["total_points"] if target_history else 0,
                target_history["fixture_rows"] if target_history else 0,
                len(target_fixtures),
                target_history["team_ids"] if target_history else [],
                actual_team_changed,
                True,
            )
        )
    return features, actuals


def _reconciliation_exceptions(
    season_data: dict[str, Any]
) -> list[tuple[Any, ...]]:
    sums: dict[int, dict[str, list[float | None]]] = defaultdict(
        lambda: {"expected_goals": [], "expected_assists": [],
                 "expected_goal_involvements": [], "expected_goals_conceded": []}
    )
    record_fields = {
        "expected_goals": "xg",
        "expected_assists": "xa",
        "expected_goal_involvements": "xgi",
        "expected_goals_conceded": "xgc",
    }
    for record in season_data["player_fixture_records"]:
        for raw_field, clean_field in record_fields.items():
            sums[record["element_id"]][raw_field].append(record[clean_field])
    exceptions: list[tuple[Any, ...]] = []
    identity_source = season_data["sources"]["identity"]
    del identity_source  # provenance is already available in the identity dataset/manifest.
    for element_id, player in season_data["player_master"].items():
        code = _int(player.get("code"), field="player.code")
        season = season_data["player_fixture_records"][0]["season"]
        is_ferguson_correction = season == "2024-25" and code == 487117
        if is_ferguson_correction:
            audit_classification = (
                "upstream_gw27_fixture_row_precedes_later_official_correction"
            )
            audit_evidence = (
                "Pinned merged_gw.csv row 18254 records GW27 fixture 266 as "
                "17 minutes, xA 0.00, xGI 0.11, xGC 0.06. The pinned "
                "pre-GW27 to pre-GW28 official snapshots advance by 34 minutes, "
                "xA 0.01, xGI 0.12, xGC 0.80; players_raw.csv retains those "
                "corrected cumulative totals. Fixture values remain unchanged."
            )
        else:
            audit_classification = "source_reconciliation_difference"
            audit_evidence = (
                "Fixture-level archive sum differs from the end-of-season "
                "players_raw cumulative value."
            )
        for field in record_fields:
            fixture_sum = _sum_nullable(sums[element_id][field])
            season_total = _number(player.get(field), field=f"player.{field}")
            if fixture_sum is None or season_total is None:
                if fixture_sum != season_total:
                    exceptions.append(
                        (
                            season,
                            element_id,
                            code,
                            player.get("web_name"),
                            field,
                            fixture_sum,
                            season_total,
                            None,
                            False,
                            audit_classification,
                            audit_evidence,
                            "preserved_source_null_mismatch_no_forced_reconciliation",
                        )
                    )
                continue
            difference = fixture_sum - season_total
            if abs(difference) > 0.001:
                exceptions.append(
                    (
                        season,
                        element_id,
                        code,
                        player.get("web_name"),
                        field,
                        fixture_sum,
                        season_total,
                        difference,
                        abs(difference) > MATERIAL_RECONCILIATION_THRESHOLD,
                        audit_classification,
                        audit_evidence,
                        "fixture_values_preserved_no_forced_reconciliation",
                    )
                )
    return exceptions


def _duplicate_reconciliation_rows(
    season: str, season_data: dict[str, Any]
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for duplicate in season_data["duplicate_rows"]:
        element_id = duplicate["element_id"]
        player = season_data["player_master"].get(element_id, {})
        rows.append(
            (
                season,
                element_id,
                _int(player.get("code"), field="player.code", nullable=True),
                player.get("web_name"),
                "duplicate_player_fixture_row",
                None,
                None,
                None,
                False,
                "byte_identical_duplicate_source_row",
                json.dumps(duplicate, sort_keys=True),
                "first_row_accepted_identical_duplicate_rejected",
            )
        )
    return rows


def _validate_2025_reconciliation(season_data: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "expected_goals": "xg",
        "expected_assists": "xa",
        "expected_goal_involvements": "xgi",
        "expected_goals_conceded": "xgc",
    }
    exact_fields = {
        "minutes": "minutes",
        "goals_scored": "goals",
        "assists": "assists",
        "total_points": "total_points",
    }
    by_player: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in season_data["player_fixture_records"]:
        by_player[record["element_id"]].append(record)
    expected_mismatches: list[dict[str, Any]] = []
    exact_mismatches: list[dict[str, Any]] = []
    null_expected_rows = 0
    for element_id, player in season_data["player_master"].items():
        records = by_player.get(element_id, [])
        for raw_field, clean_field in fields.items():
            values = [record[clean_field] for record in records]
            null_expected_rows += sum(value is None for value in values)
            fixture_sum = _sum_nullable(values)
            season_total = _number(player.get(raw_field), field=f"player.{raw_field}")
            if fixture_sum is None or season_total is None or not math.isclose(
                fixture_sum, season_total, rel_tol=0.0, abs_tol=1e-9
            ):
                expected_mismatches.append(
                    {"element_id": element_id, "field": raw_field,
                     "fixture_sum": fixture_sum, "season_total": season_total}
                )
        for raw_field, clean_field in exact_fields.items():
            fixture_sum = sum(record[clean_field] for record in records)
            season_total = _int(player.get(raw_field), field=f"player.{raw_field}")
            if fixture_sum != season_total:
                exact_mismatches.append(
                    {"element_id": element_id, "field": raw_field,
                     "fixture_sum": fixture_sum, "season_total": season_total}
                )
    if expected_mismatches or exact_mismatches or null_expected_rows:
        raise HistoricalDataQualityError(
            "2025/26 season reconciliation changed: "
            f"expected-stat mismatches={len(expected_mismatches)}, "
            f"actual mismatches={len(exact_mismatches)}, null expected rows={null_expected_rows}"
        )
    return {
        "players_checked": len(season_data["player_master"]),
        "expected_stat_mismatches": 0,
        "actual_stat_mismatches": 0,
        "null_expected_stat_values": 0,
    }


def _identity_transition_audit(
    left: dict[str, Any], right: dict[str, Any]
) -> dict[str, Any]:
    def by_code(data: dict[str, Any]) -> dict[int, tuple[int, str, str, int]]:
        return {
            _int(row[2], field="identity.code"): (
                _int(row[1], field="identity.element_id"),
                str(row[7]),
                str(row[9]),
                _int(row[2], field="identity.code"),
            )
            for row in data["identities"]
        }

    left_map = by_code(left)
    right_map = by_code(right)
    shared = sorted(set(left_map) & set(right_map))
    element_changes = sum(left_map[code][0] != right_map[code][0] for code in shared)
    return {
        "from_season": left["season"],
        "to_season": right["season"],
        "shared_codes": len(shared),
        "element_id_changes": element_changes,
        "element_id_unchanged": len(shared) - element_changes,
        "position_changes": sum(left_map[code][1] != right_map[code][1] for code in shared),
        "team_changes": sum(left_map[code][2] != right_map[code][2] for code in shared),
        "join_key": "code",
        "element_id_join_prohibited": True,
    }


def _fixture_structure_audit(season_data: dict[str, Any]) -> dict[str, Any]:
    team_ids = sorted(season_data["teams"])
    special: list[dict[str, Any]] = []
    for gameweek in range(1, 39):
        counts = {
            team_id: len(season_data["fixtures_by_gameweek_team"].get((gameweek, team_id), []))
            for team_id in team_ids
        }
        blanks = [team for team, count in counts.items() if count == 0]
        doubles = [team for team, count in counts.items() if count > 1]
        if blanks or doubles:
            special.append(
                {"gameweek": gameweek, "fixture_count": sum(counts.values()) // 2,
                 "blank_team_ids": blanks, "double_team_ids": doubles}
            )
    return {"fixture_rows": len(season_data["fixtures"]), "special_gameweeks": special}


def _validate_feature_rows(
    features: list[tuple[Any, ...]], actuals: list[tuple[Any, ...]]
) -> None:
    names = [name for name, _ in FEATURE_SCHEMA]
    indexes = {name: index for index, name in enumerate(names)}
    forbidden = {"xP", "xp", "actual_points", "target_xg", "target_xa"}
    if forbidden & set(names) or any(name.startswith("actual_") for name in names):
        raise HistoricalDataQualityError("actual or prohibited xP fields entered predictors")
    keys: set[tuple[Any, ...]] = set()
    for row in features:
        key = (
            row[indexes["season"]],
            row[indexes["target_gameweek"]],
            row[indexes["element_id"]],
            row[indexes["target_fixture_id"]],
        )
        if key in keys:
            raise HistoricalDataQualityError(f"duplicate historical feature key: {key}")
        keys.add(key)
        maximum = row[indexes["history_gameweek_max_used"]]
        target = row[indexes["target_gameweek"]]
        if maximum is not None and maximum >= target:
            raise HistoricalDataQualityError(
                f"target leakage in historical feature key {key}: max history GW {maximum}"
            )
        latest_kickoff = row[indexes["history_latest_kickoff_used"]]
        target_deadline = row[indexes["target_deadline"]]
        if latest_kickoff is not None:
            if isinstance(latest_kickoff, str):
                latest_kickoff = _utc_datetime(
                    latest_kickoff, field="feature.history_latest_kickoff_used"
                )
            if isinstance(target_deadline, str):
                target_deadline = _utc_datetime(
                    target_deadline, field="feature.target_deadline"
                )
            if latest_kickoff >= target_deadline:
                raise HistoricalDataQualityError(
                    "chronological target leakage in historical feature key "
                    f"{key}: latest kickoff {latest_kickoff} is not before "
                    f"deadline {target_deadline}"
                )
        if row[indexes["history_cutoff_rule"]] != HISTORY_CUTOFF_RULE:
            raise HistoricalDataQualityError(
                f"historical feature key {key} has the wrong chronology cutoff rule"
            )
        if row[indexes["fixture_assignment_context"]] != FIXTURE_ASSIGNMENT_CONTEXT:
            raise HistoricalDataQualityError("fixture context is not explicitly finalized")
        if row[indexes["fixture_assignment_verified_predeadline"]] is not False:
            raise HistoricalDataQualityError(
                "finalized fixture assignment was incorrectly labelled pre-deadline verified"
            )
    actual_names = {name for name, _ in ACTUAL_SCHEMA}
    if not actual_names or not actuals:
        raise HistoricalDataQualityError("historical actual section is empty")


def _source_manifest_entry(
    source: HistoricalSource,
    *,
    path: Path,
    retrieved_at: str,
    reused: bool,
    accepted_records: int | None = None,
    rejected_records: int | None = None,
) -> dict[str, Any]:
    observed = _sha256_path(path)
    return {
        "season": source.season,
        "source_repository": source.repository,
        "source_commit": source.commit,
        "source_file_path": source.path,
        "expected_sha256": source.sha256,
        "observed_sha256": observed,
        "retrieval_timestamp": retrieved_at,
        "reused_from_verified_cache": reused,
        "parser_schema_version": PARSER_SCHEMA_VERSION,
        "kind": source.kind,
        "target_gameweek": source.gameweek,
        "accepted_records": accepted_records,
        "rejected_records": rejected_records,
    }


def build_historical_datasets(
    *,
    raw_data_root: Path = Path("data/historical/raw"),
    clean_data_root: Path = Path("data/historical/clean"),
    sources: tuple[HistoricalSource, ...] = APPROVED_HISTORICAL_SOURCES,
    fetcher: Fetcher = _default_fetcher,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    strict_approved: bool = True,
) -> HistoricalBuildResult:
    """Retrieve pinned sources and build immutable historical Parquet datasets."""
    seasons = sorted({source.season for source in sources})
    if strict_approved:
        if sources != APPROVED_HISTORICAL_SOURCES:
            raise HistoricalDataQualityError(
                "historical source catalogue differs from the exact audited source set"
            )
        if seasons != ["2023-24", "2024-25", "2025-26"]:
            raise HistoricalDataQualityError(f"unexpected approved seasons: {seasons}")
    final_directory = clean_data_root / PARSER_SCHEMA_VERSION
    if final_directory.exists():
        raise HistoricalOutputExistsError(
            f"historical output already exists and will not be overwritten: {final_directory}"
        )

    logger.info("Retrieving %d commit-pinned historical source files", len(sources))
    cached: dict[HistoricalSource, Path] = {}
    retrievals: dict[HistoricalSource, tuple[str, bool]] = {}
    for source in sources:
        path, retrieved_at, reused = _cache_source(
            source, raw_root=raw_data_root, fetcher=fetcher, clock=clock
        )
        cached[source] = path
        retrievals[source] = (retrieved_at, reused)

    source_hashes_before = {source: _sha256_path(path) for source, path in cached.items()}
    stage = clean_data_root / f".{PARSER_SCHEMA_VERSION}.{uuid.uuid4().hex}.tmp"
    if stage.exists():
        raise HistoricalOutputExistsError(f"temporary historical output exists: {stage}")
    stage.mkdir(parents=True, exist_ok=False)
    manifest_sources: list[dict[str, Any]] = []
    overall_counts: dict[str, dict[str, int]] = {}
    chronology_audit_by_season: dict[str, list[dict[str, Any]]] = {}
    identity_variants: dict[int, list[dict[str, Any]]] = defaultdict(list)
    output_details: list[dict[str, Any]] = []
    season_data_by_season: dict[str, dict[str, Any]] = {}
    reconciliation_audit_by_season: dict[str, dict[str, Any]] = {}
    fixture_structure_by_season: dict[str, dict[str, Any]] = {}
    player_universe_edges: list[dict[str, Any]] = []
    try:
        for season in seasons:
            season_sources = tuple(source for source in sources if source.season == season)
            season_data = _parse_season_sources(
                season,
                sources=season_sources,
                cached=cached,
                strict_approved=strict_approved,
            )
            season_data_by_season[season] = season_data
            predeadline_rows, state_records, snapshot_stats = _parse_predeadline_states(
                season,
                sources=season_sources,
                cached=cached,
                strict_approved=strict_approved,
            )
            chronology_cases = _audit_chronological_exclusions(
                season_data, state_records
            )
            chronology_audit_by_season[season] = chronology_cases
            feature_rows, actual_rows = _build_features_and_actuals(
                season_data, state_records
            )
            _validate_feature_rows(feature_rows, actual_rows)
            reconciliation_rows = _reconciliation_exceptions(season_data)
            reconciliation_rows.extend(_duplicate_reconciliation_rows(season, season_data))
            fixture_structure_by_season[season] = _fixture_structure_audit(season_data)
            if season == "2025-26":
                reconciliation_audit_by_season[season] = _validate_2025_reconciliation(
                    season_data
                )
                sillah = season_data["player_master"].get(841)
                if (
                    not sillah
                    or sillah.get("web_name") != "Sillah"
                    or _int(sillah.get("minutes"), field="Sillah.minutes") != 0
                    or _int(sillah.get("total_points"), field="Sillah.total_points") != 0
                    or any(record["element_id"] == 841 for record in state_records)
                ):
                    raise HistoricalDataQualityError(
                        "audited post-GW38 Sillah player-universe edge changed"
                    )
                player_universe_edges.append(
                    {
                        "season": season,
                        "element_id": 841,
                        "code": _int(sillah.get("code"), field="Sillah.code"),
                        "display_name": "Sillah",
                        "end_of_season_minutes": 0,
                        "end_of_season_total_points": 0,
                        "predeadline_snapshot_rows": 0,
                        "classification": "added_after_final_predeadline_snapshot_no_state_fabricated",
                    }
                )

            for identity in season_data["identities"]:
                identity_variants[identity[2]].append(
                    {"season": season, "element_id": identity[1], "display_name": identity[3]}
                )

            season_dir = stage / season
            season_dir.mkdir(parents=True, exist_ok=False)
            datasets = (
                ("historical_player_fixture", PLAYER_FIXTURE_SCHEMA, season_data["player_fixtures"]),
                ("historical_fixtures", FIXTURE_SCHEMA, season_data["fixtures"]),
                ("historical_player_identity", IDENTITY_SCHEMA, season_data["identities"]),
                ("historical_predeadline_player_state", PREDEADLINE_SCHEMA, predeadline_rows),
                ("historical_prediction_features", FEATURE_SCHEMA, feature_rows),
                ("historical_prediction_actuals", ACTUAL_SCHEMA, actual_rows),
                ("historical_reconciliation_exceptions", RECONCILIATION_SCHEMA, reconciliation_rows),
            )
            connection = duckdb.connect(":memory:")
            try:
                for name, schema, rows in datasets:
                    output_path = season_dir / f"{name}.parquet"
                    _write_parquet(connection, name, schema, rows, output_path)
                    observed_rows = connection.execute(
                        "SELECT count(*) FROM read_parquet(?)", [str(output_path)]
                    ).fetchone()[0]
                    if observed_rows != len(rows):
                        raise HistoricalDataQualityError(
                            f"Parquet row count mismatch for {output_path}"
                        )
                    output_details.append(
                        {
                            "season": season,
                            "path": str(output_path.relative_to(stage)),
                            "rows": len(rows),
                            "sha256": _sha256_path(output_path),
                            "bytes": output_path.stat().st_size,
                        }
                    )
            finally:
                connection.close()

            overall_counts[season] = {
                "raw_player_fixture_rows": season_data["raw_player_fixture_rows"],
                "player_fixture_rows": len(season_data["player_fixtures"]),
                "byte_identical_duplicate_rows_excluded": len(season_data["duplicate_rows"]),
                "player_rows_with_minutes": season_data["minutes_rows"],
                "fixture_rows": len(season_data["fixtures"]),
                "identity_rows": len(season_data["identities"]),
                "assistant_manager_rows_excluded": season_data["am_rows"],
                "assistant_manager_elements_excluded": season_data["am_elements"],
                "predeadline_snapshots": len(snapshot_stats),
                "predeadline_player_state_rows": len(predeadline_rows),
                "prediction_feature_rows": len(feature_rows),
                "prediction_actual_rows": len(actual_rows),
                "reconciliation_exceptions": len(reconciliation_rows),
                "chronological_fixture_target_cases_excluded": len(
                    chronology_cases
                ),
                "chronological_player_fixture_rows_excluded": sum(
                    case["player_fixture_rows_excluded"]
                    for case in chronology_cases
                ),
            }

            for source in season_sources:
                accepted = None
                rejected = None
                if source.kind == "player_fixture":
                    accepted = len(season_data["player_fixtures"])
                    rejected = season_data["am_rows"] + len(season_data["duplicate_rows"])
                elif source.kind == "fixtures":
                    accepted = len(season_data["fixtures"])
                    rejected = 0
                elif source.kind == "player_identity":
                    accepted = len(season_data["identities"])
                    rejected = season_data["am_elements"]
                elif source.kind == "teams":
                    accepted = len(season_data["teams"])
                    rejected = 0
                elif source.kind == "predeadline_bootstrap":
                    stats = snapshot_stats[source.gameweek or 0]
                    accepted = stats["accepted_records"]
                    rejected = stats["rejected_assistant_manager_records"]
                retrieved_at, reused = retrievals[source]
                manifest_sources.append(
                    _source_manifest_entry(
                        source,
                        path=cached[source],
                        retrieved_at=retrieved_at,
                        reused=reused,
                        accepted_records=accepted,
                        rejected_records=rejected,
                    )
                )

        if any(_sha256_path(cached[source]) != digest for source, digest in source_hashes_before.items()):
            raise HistoricalDataQualityError("historical source changed during transformation")

        code_name_variants = [
            {"code": code, "mappings": mappings}
            for code, mappings in sorted(identity_variants.items())
            if len({mapping["display_name"] for mapping in mappings}) > 1
        ]
        identity_transitions = [
            _identity_transition_audit(
                season_data_by_season[left], season_data_by_season[right]
            )
            for left, right in zip(seasons, seasons[1:])
        ]
        if strict_approved:
            audited_transition = next(
                audit for audit in identity_transitions
                if audit["from_season"] == "2024-25"
                and audit["to_season"] == "2025-26"
            )
            expected_transition = {
                "shared_codes": 534,
                "element_id_changes": 533,
                "element_id_unchanged": 1,
                "position_changes": 12,
                "team_changes": 74,
            }
            observed_transition = {
                key: audited_transition[key] for key in expected_transition
            }
            if observed_transition != expected_transition:
                raise HistoricalDataQualityError(
                    "audited 2024/25 to 2025/26 identity transition changed: "
                    f"expected {expected_transition}, observed {observed_transition}"
                )
        manifest = {
            "status": "complete",
            "historical_classification": HISTORICAL_CLASSIFICATION,
            "perfect_historical_deadline_replay": False,
            "parser_schema_version": PARSER_SCHEMA_VERSION,
            "created_at": _iso_utc(clock()),
            "seasons": seasons,
            "source_files": manifest_sources,
            "row_counts": overall_counts,
            "outputs": output_details,
            "identity": {
                "within_season_key": ["season", "element_id"],
                "cross_season_candidate_bridge": "code",
                "element_id_cross_season_join_prohibited": True,
                "cross_season_code_name_variants": code_name_variants,
                "code_collisions_within_season": [],
                "adjacent_season_audits": identity_transitions,
            },
            "preseason_prior_source_boundaries": {
                season: {
                    "repository": "vaastav/Fantasy-Premier-League",
                    "commit": commit,
                    "classification": "later_finalized_archive_possible_retroactive_corrections",
                }
                for season, commit in PRESEASON_PRIOR_COMMITS.items()
            },
            "fixture_structure": fixture_structure_by_season,
            "player_universe_edges": player_universe_edges,
            "temporal_policy": {
                "prediction_history_cutoff": HISTORY_CUTOFF_RULE,
                "source_performance_must_be_known_before_target_deadline": True,
                "kickoff_at_or_after_target_deadline_prohibited": True,
                "previous_gameweek_minutes": "sum(minutes where gameweek = target_gameweek - 1)",
                "most_recent_fixture_semantics_prohibited": True,
                "later_snapshot_gap_fill_prohibited": True,
            },
            "chronological_leakage_audit": {
                "cases_by_season": chronology_audit_by_season,
                "completion_timestamp_available": False,
                "guard": "fixture_kickoff < target_deadline",
            },
            "fixture_assignment_context": FIXTURE_ASSIGNMENT_CONTEXT,
            "fixture_assignment_verified_predeadline": False,
            "expected_stat_semantics": EXPECTED_STAT_SEMANTICS,
            "reconciliation_audit": {
                "material_absolute_difference_threshold": (
                    MATERIAL_RECONCILIATION_THRESHOLD
                ),
                "threshold_is_reporting_only": True,
                "source_values_mutated": False,
                "by_season": reconciliation_audit_by_season,
                "identical_duplicate_policy": (
                    "accept_first_and_reject_only-byte-for-byte-identical-CSV-records; "
                    "fail_on_non-identical_duplicate_key"
                ),
                "duplicate_validation_mode": DUPLICATE_VALIDATION_MODE,
                "duplicate_validation_fix_applied_before_build": True,
                "intermediate_output_from_parsed_field_validation_reused": False,
            },
            "leakage_exclusions": [
                "vaastav_same_gameweek_xP",
                "target_gameweek_xg_xa",
                "post_deadline_bootstrap",
                "later_bootstrap_gap_fill",
                "end_of_season_prediction_state",
                "assistant_manager_elements",
                "missing_expected_stats_as_zero",
                "final_fixture_assignment_as_known_pre_deadline",
                "cross_season_element_id_join",
            ],
            "scoring_policy": {
                "total_points": HISTORICAL_POINTS_CONTEXT,
                "modeled_actual_components": ["appearance", "historical_position_goals", "assists"],
                "not_retrofitted": ["DEFCON", "2026/27 BPS", "modern assist rules"],
            },
        }
        manifest_path = stage / "historical_ingestion_manifest.json"
        _write_exclusive(
            manifest_path,
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        clean_data_root.mkdir(parents=True, exist_ok=True)
        try:
            stage.rename(final_directory)
        except FileExistsError as exc:
            raise HistoricalOutputExistsError(
                f"historical output already exists and will not be overwritten: {final_directory}"
            ) from exc
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    logger.info("Historical ingestion completed: %s", final_directory)
    return HistoricalBuildResult(
        directory=final_directory,
        manifest_path=final_directory / "historical_ingestion_manifest.json",
        row_counts=overall_counts,
    )
