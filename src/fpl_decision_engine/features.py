"""Build leakage-safe player × target-fixture prediction features."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from .transform import CleanOutputExistsError, DataQualityError, TransformationError


class FeatureBuildError(TransformationError):
    """Raised when prediction features cannot be built safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_snapshot_timestamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise FeatureBuildError(f"invalid snapshot timestamp: {value}") from exc


def _parse_api_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except (AttributeError, ValueError) as exc:
        raise FeatureBuildError(f"invalid API timestamp: {value!r}") from exc


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FeatureBuildError(f"could not read {path}: {exc}") from exc


def _snapshot_inputs(
    *,
    raw_data_root: Path,
    clean_data_root: Path,
    season: str,
    snapshot_timestamp: str,
) -> dict[str, Path]:
    raw_dir = raw_data_root / season / snapshot_timestamp
    clean_dir = clean_data_root / season / snapshot_timestamp
    paths = {
        "bootstrap": raw_dir / "bootstrap-static.json",
        "fixture_manifest": raw_dir / "fixtures.manifest.json",
        "history_manifest": raw_dir / "player_history/manifest.json",
        "players": clean_dir / "players.parquet",
        "fixtures": clean_dir / "fixtures.parquet",
        "history": clean_dir / "player_gameweek_history.parquet",
    }
    missing = [path for path in paths.values() if not path.is_file()]
    if missing:
        raise FeatureBuildError(
            "snapshot is missing required feature inputs: "
            + ", ".join(path.as_posix() for path in missing)
        )
    return paths


def _target_event(bootstrap_path: Path, target_gameweek: int) -> dict[str, Any]:
    bootstrap = _load_json(bootstrap_path)
    if not isinstance(bootstrap, dict) or not isinstance(bootstrap.get("events"), list):
        raise FeatureBuildError("bootstrap snapshot does not contain an events list")
    event = next(
        (event for event in bootstrap["events"] if event.get("id") == target_gameweek),
        None,
    )
    if event is None:
        raise FeatureBuildError(
            f"target gameweek {target_gameweek} is not present in the bootstrap snapshot"
        )
    return event


def _is_snapshot_safe(
    paths: dict[str, Path], snapshot_timestamp: str, target_gameweek: int
) -> tuple[bool, datetime, datetime, datetime, datetime, int | None]:
    event = _target_event(paths["bootstrap"], target_gameweek)
    bootstrap = _load_json(paths["bootstrap"])
    next_gameweek_id = next(
        (
            candidate.get("id")
            for candidate in bootstrap["events"]
            if candidate.get("is_next") is True
        ),
        None,
    )
    deadline = _parse_api_timestamp(event["deadline_time"])
    snapshot_time = _parse_snapshot_timestamp(snapshot_timestamp)
    fixture_manifest = _load_json(paths["fixture_manifest"])
    history_manifest = _load_json(paths["history_manifest"])
    if not isinstance(fixture_manifest, dict) or not isinstance(history_manifest, dict):
        raise FeatureBuildError("fixture or history manifest is not a JSON object")
    if fixture_manifest.get("status") != "complete":
        raise FeatureBuildError("fixture ingestion manifest is not complete")
    if history_manifest.get("status") != "complete":
        raise FeatureBuildError("player-history ingestion manifest is not complete")
    fixture_time = _parse_api_timestamp(fixture_manifest.get("retrieved_at"))
    history_time = _parse_api_timestamp(history_manifest.get("completed_at"))
    safe = max(snapshot_time, fixture_time, history_time) < deadline
    return (
        safe,
        deadline,
        snapshot_time,
        fixture_time,
        history_time,
        next_gameweek_id,
    )


def find_feature_snapshot(
    *,
    raw_data_root: Path,
    clean_data_root: Path,
    season: str,
    target_gameweek: int,
    snapshot_timestamp: str | None,
) -> tuple[
    str,
    dict[str, Path],
    tuple[datetime, datetime, datetime, datetime, int | None],
]:
    if snapshot_timestamp is not None:
        candidates = [snapshot_timestamp]
    else:
        season_root = clean_data_root / season
        candidates = sorted(
            (path.name for path in season_root.iterdir() if path.is_dir()),
            reverse=True,
        ) if season_root.is_dir() else []

    errors: list[str] = []
    for candidate in candidates:
        try:
            paths = _snapshot_inputs(
                raw_data_root=raw_data_root,
                clean_data_root=clean_data_root,
                season=season,
                snapshot_timestamp=candidate,
            )
            (
                safe,
                deadline,
                snapshot_time,
                fixture_time,
                history_time,
                next_gameweek_id,
            ) = (
                _is_snapshot_safe(paths, candidate, target_gameweek)
            )
            if safe:
                return candidate, paths, (
                    deadline,
                    snapshot_time,
                    fixture_time,
                    history_time,
                    next_gameweek_id,
                )
            errors.append(f"{candidate} was not fully collected before the deadline")
        except FeatureBuildError as exc:
            errors.append(f"{candidate}: {exc}")

    qualifier = "requested" if snapshot_timestamp else "available"
    details = "; ".join(errors) if errors else "no complete clean snapshots found"
    raise FeatureBuildError(
        f"no leakage-safe {qualifier} snapshot for gameweek {target_gameweek}: {details}"
    )


def _copy_parquet_exclusive(
    connection: duckdb.DuckDBPyConnection, output_path: Path
) -> None:
    if output_path.exists():
        raise CleanOutputExistsError(
            f"feature output already exists and will not be overwritten: {output_path}"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(
        f".{output_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        connection.execute(
            "COPY player_gameweek_features TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(temporary_path)],
        )
        try:
            os.link(temporary_path, output_path)
        except FileExistsError as exc:
            raise CleanOutputExistsError(
                f"feature output already exists and will not be overwritten: {output_path}"
            ) from exc
    finally:
        temporary_path.unlink(missing_ok=True)


def _create_feature_table(
    connection: duckdb.DuckDBPyConnection,
    *,
    target_gameweek: int,
) -> None:
    connection.execute(
        """CREATE TEMP TABLE history_by_gameweek AS
           SELECT fpl_player_id,
                  gameweek_id,
                  count(*) AS history_fixture_records,
                  count(*) FILTER (WHERE minutes > 0) AS appearances,
                  sum(starts) AS starts,
                  sum(minutes) AS minutes,
                  sum(total_points) AS total_points,
                  sum(xg) AS xg,
                  sum(xa) AS xa,
                  sum(xgc) AS xgc,
                  sum(defensive_contribution) AS defensive_contribution,
                  sum(saves) AS saves
           FROM history_input
           WHERE gameweek_id < ?
           GROUP BY fpl_player_id, gameweek_id""",
        [target_gameweek],
    )
    connection.execute(
        """CREATE TABLE player_gameweek_features AS
           WITH prior AS (
               SELECT fpl_player_id,
                      count(*) AS gameweeks_with_data,
                      sum(history_fixture_records) AS history_rows,
                      sum(appearances) AS appearances,
                      sum(starts) AS starts,
                      sum(minutes) AS minutes,
                      avg(minutes) AS avg_minutes,
                      sum(total_points) AS points,
                      avg(total_points) AS avg_points,
                      sum(xg) AS xg,
                      sum(xa) AS xa,
                      sum(xgc) AS xgc,
                      sum(defensive_contribution) AS defensive_contribution,
                      sum(saves) AS saves,
                      max(gameweek_id) AS max_gameweek
               FROM history_by_gameweek GROUP BY fpl_player_id
           ),
           previous AS (
               SELECT * FROM history_by_gameweek WHERE gameweek_id = ? - 1
           ),
           rolling_3 AS (
               SELECT fpl_player_id,
                      count(*) AS sample_count,
                      sum(minutes) AS minutes,
                      avg(minutes) AS avg_minutes,
                      sum(total_points) AS points,
                      avg(total_points) AS avg_points,
                      sum(xg) AS xg,
                      sum(xa) AS xa,
                      sum(xgc) AS xgc,
                      sum(defensive_contribution) AS defensive_contribution,
                      sum(saves) AS saves
               FROM history_by_gameweek
               WHERE gameweek_id >= ? - 3
               GROUP BY fpl_player_id
           ),
           rolling_5 AS (
               SELECT fpl_player_id,
                      count(*) AS sample_count,
                      sum(minutes) AS minutes,
                      avg(minutes) AS avg_minutes,
                      sum(total_points) AS points,
                      avg(total_points) AS avg_points,
                      sum(xg) AS xg,
                      sum(xa) AS xa,
                      sum(xgc) AS xgc,
                      sum(defensive_contribution) AS defensive_contribution,
                      sum(saves) AS saves
               FROM history_by_gameweek
               WHERE gameweek_id >= ? - 5
               GROUP BY fpl_player_id
           ),
           target_fixtures AS (
               SELECT * FROM fixtures_input WHERE gameweek_id = ?
           )
           SELECT c.season,
                  c.snapshot_timestamp,
                  p.fpl_player_id,
                  p.web_name,
                  p.position_id,
                  p.position,
                  p.team_id AS snapshot_team_id,
                  p.team_name AS snapshot_team_name,
                  c.target_gameweek,
                  tf.fixture_id AS target_fixture_id,
                  tf.home_team_id AS target_home_team_id,
                  tf.away_team_id AS target_away_team_id,
                  CASE WHEN p.team_id = tf.home_team_id THEN tf.away_team_id
                       WHEN p.team_id = tf.away_team_id THEN tf.home_team_id END
                      AS target_opponent_team_id,
                  CASE WHEN p.team_id = tf.home_team_id THEN tf.away_team_name
                       WHEN p.team_id = tf.away_team_id THEN tf.home_team_name END
                      AS target_opponent_team_name,
                  CASE WHEN p.team_id = tf.home_team_id THEN 'H'
                       WHEN p.team_id = tf.away_team_id THEN 'A' END
                      AS target_home_away,
                  tf.kickoff_time AS target_kickoff_time,
                  tf.fixture_id IS NOT NULL AS target_has_fixture,
                  count(tf.fixture_id) OVER (PARTITION BY p.fpl_player_id)
                      AS target_fixture_count,
                  c.target_deadline_time,
                  p.status AS availability_status,
                  CASE WHEN c.availability_reference_gameweek = c.target_gameweek
                       THEN p.chance_of_playing_next_round END
                      AS chance_of_playing_next_round,
                  p.news AS availability_news,
                  c.availability_as_of,
                  true AS availability_known_pre_deadline,
                  c.availability_reference_gameweek,
                  c.availability_reference_gameweek = c.target_gameweek
                      AS availability_is_target_next_gameweek,
                  coalesce(prior.history_rows, 0) AS prior_history_rows,
                  coalesce(prior.appearances, 0) AS prior_appearances,
                  prior.starts AS prior_starts,
                  prior.minutes AS prior_total_minutes,
                  coalesce(prior.gameweeks_with_data, 0)
                      AS prior_gameweeks_with_data,
                  prior.max_gameweek AS history_gameweek_max_used,
                  previous.gameweek_id AS previous_gameweek_id_used,
                  previous.gameweek_id IS NOT NULL AS previous_gameweek_has_data,
                  previous.minutes AS previous_gw_minutes,
                  prior.avg_minutes AS average_prior_minutes,
                  rolling_3.avg_minutes AS rolling_3_avg_minutes,
                  rolling_5.avg_minutes AS rolling_5_avg_minutes,
                  coalesce(rolling_3.sample_count, 0)
                      AS rolling_3_gameweeks_with_data,
                  coalesce(rolling_5.sample_count, 0)
                      AS rolling_5_gameweeks_with_data,
                  previous.xg AS previous_gw_xg,
                  prior.xg AS cumulative_prior_xg,
                  CASE WHEN prior.minutes > 0 THEN prior.xg * 90.0 / prior.minutes END
                      AS prior_xg_per_90,
                  rolling_3.xg AS rolling_3_xg,
                  CASE WHEN rolling_3.minutes > 0
                       THEN rolling_3.xg * 90.0 / rolling_3.minutes END
                      AS rolling_3_xg_per_90,
                  rolling_5.xg AS rolling_5_xg,
                  CASE WHEN rolling_5.minutes > 0
                       THEN rolling_5.xg * 90.0 / rolling_5.minutes END
                      AS rolling_5_xg_per_90,
                  previous.xa AS previous_gw_xa,
                  prior.xa AS cumulative_prior_xa,
                  CASE WHEN prior.minutes > 0 THEN prior.xa * 90.0 / prior.minutes END
                      AS prior_xa_per_90,
                  rolling_3.xa AS rolling_3_xa,
                  CASE WHEN rolling_3.minutes > 0
                       THEN rolling_3.xa * 90.0 / rolling_3.minutes END
                      AS rolling_3_xa_per_90,
                  rolling_5.xa AS rolling_5_xa,
                  CASE WHEN rolling_5.minutes > 0
                       THEN rolling_5.xa * 90.0 / rolling_5.minutes END
                      AS rolling_5_xa_per_90,
                  previous.xg + previous.xa AS previous_gw_xgi,
                  prior.xg + prior.xa AS cumulative_prior_xgi,
                  CASE WHEN prior.minutes > 0
                       THEN (prior.xg + prior.xa) * 90.0 / prior.minutes END
                      AS prior_xgi_per_90,
                  rolling_3.xg + rolling_3.xa AS rolling_3_xgi,
                  CASE WHEN rolling_3.minutes > 0
                       THEN (rolling_3.xg + rolling_3.xa) * 90.0
                            / rolling_3.minutes END AS rolling_3_xgi_per_90,
                  rolling_5.xg + rolling_5.xa AS rolling_5_xgi,
                  CASE WHEN rolling_5.minutes > 0
                       THEN (rolling_5.xg + rolling_5.xa) * 90.0
                            / rolling_5.minutes END AS rolling_5_xgi_per_90,
                  previous.xgc AS previous_gw_xgc,
                  prior.xgc AS cumulative_prior_xgc,
                  CASE WHEN prior.minutes > 0
                       THEN prior.xgc * 90.0 / prior.minutes END AS prior_xgc_per_90,
                  rolling_3.xgc AS rolling_3_xgc,
                  CASE WHEN rolling_3.minutes > 0
                       THEN rolling_3.xgc * 90.0 / rolling_3.minutes END
                      AS rolling_3_xgc_per_90,
                  rolling_5.xgc AS rolling_5_xgc,
                  CASE WHEN rolling_5.minutes > 0
                       THEN rolling_5.xgc * 90.0 / rolling_5.minutes END
                      AS rolling_5_xgc_per_90,
                  previous.defensive_contribution AS previous_gw_defensive_contribution,
                  prior.defensive_contribution AS cumulative_prior_defensive_contribution,
                  CASE WHEN prior.minutes > 0
                       THEN prior.defensive_contribution * 90.0 / prior.minutes END
                      AS prior_defensive_contribution_per_90,
                  rolling_3.defensive_contribution
                      AS rolling_3_defensive_contribution,
                  CASE WHEN rolling_3.minutes > 0
                       THEN rolling_3.defensive_contribution * 90.0
                            / rolling_3.minutes END
                      AS rolling_3_defensive_contribution_per_90,
                  rolling_5.defensive_contribution
                      AS rolling_5_defensive_contribution,
                  CASE WHEN rolling_5.minutes > 0
                       THEN rolling_5.defensive_contribution * 90.0
                            / rolling_5.minutes END
                      AS rolling_5_defensive_contribution_per_90,
                  previous.saves AS previous_gw_saves,
                  prior.saves AS cumulative_prior_saves,
                  CASE WHEN prior.minutes > 0
                       THEN prior.saves * 90.0 / prior.minutes END AS prior_saves_per_90,
                  rolling_3.saves AS rolling_3_saves,
                  CASE WHEN rolling_3.minutes > 0
                       THEN rolling_3.saves * 90.0 / rolling_3.minutes END
                      AS rolling_3_saves_per_90,
                  rolling_5.saves AS rolling_5_saves,
                  CASE WHEN rolling_5.minutes > 0
                       THEN rolling_5.saves * 90.0 / rolling_5.minutes END
                      AS rolling_5_saves_per_90,
                  previous.total_points AS previous_gw_points,
                  prior.avg_points AS average_prior_points,
                  rolling_3.avg_points AS rolling_3_avg_points,
                  rolling_5.avg_points AS rolling_5_avg_points,
                  c.players_input_sha256,
                  c.fixtures_input_sha256,
                  c.history_input_sha256,
                  c.bootstrap_sha256,
                  c.fixture_retrieved_at,
                  c.history_retrieved_at
           FROM players_input p
           CROSS JOIN build_context c
           LEFT JOIN target_fixtures tf
             ON p.team_id = tf.home_team_id OR p.team_id = tf.away_team_id
           LEFT JOIN prior ON p.fpl_player_id = prior.fpl_player_id
           LEFT JOIN previous ON p.fpl_player_id = previous.fpl_player_id
           LEFT JOIN rolling_3 ON p.fpl_player_id = rolling_3.fpl_player_id
           LEFT JOIN rolling_5 ON p.fpl_player_id = rolling_5.fpl_player_id""",
        [
            target_gameweek,
            target_gameweek,
            target_gameweek,
            target_gameweek,
        ],
    )


def _validate_features(
    connection: duckdb.DuckDBPyConnection, target_gameweek: int
) -> None:
    expected_rows = connection.execute(
        """SELECT sum(CASE WHEN fixture_count = 0 THEN 1 ELSE fixture_count END)
           FROM (
               SELECT p.fpl_player_id, count(f.fixture_id) AS fixture_count
               FROM players_input p
               LEFT JOIN fixtures_input f
                 ON f.gameweek_id = ?
                AND (p.team_id = f.home_team_id OR p.team_id = f.away_team_id)
               GROUP BY p.fpl_player_id
           )""",
        [target_gameweek],
    ).fetchone()[0]
    checks = (
        (
            "feature row count does not match player/target-fixture grain",
            "SELECT count(*) = ? FROM player_gameweek_features",
            [expected_rows],
        ),
        (
            "not every source player is represented in features",
            """SELECT count(DISTINCT fpl_player_id) =
                       (SELECT count(DISTINCT fpl_player_id) FROM players_input)
                FROM player_gameweek_features""",
            [],
        ),
        (
            "feature player/gameweek/fixture keys are not unique",
            """SELECT count(*) = count(DISTINCT (
                           fpl_player_id, target_gameweek,
                           coalesce(target_fixture_id, -1)))
                FROM player_gameweek_features""",
            [],
        ),
        (
            "target-gameweek or future history entered feature calculations",
            """SELECT count(*) = 0 FROM player_gameweek_features
                WHERE history_gameweek_max_used >= target_gameweek
                   OR previous_gameweek_id_used >= target_gameweek""",
            [],
        ),
        (
            "target fixture or team context does not resolve",
            """SELECT count(*) = 0 FROM player_gameweek_features f
                LEFT JOIN fixtures_input x ON f.target_fixture_id = x.fixture_id
                LEFT JOIN valid_teams t ON f.snapshot_team_id = t.id
                LEFT JOIN valid_teams o ON f.target_opponent_team_id = o.id
                WHERE t.id IS NULL
                   OR (f.target_fixture_id IS NOT NULL
                       AND (x.fixture_id IS NULL OR o.id IS NULL))""",
            [],
        ),
        (
            "target home/away derivation is inconsistent",
            """SELECT count(*) = 0 FROM player_gameweek_features
                WHERE (target_home_away = 'H'
                       AND (snapshot_team_id <> target_home_team_id
                            OR target_opponent_team_id <> target_away_team_id))
                   OR (target_home_away = 'A'
                       AND (snapshot_team_id <> target_away_team_id
                            OR target_opponent_team_id <> target_home_team_id))
                   OR (target_fixture_id IS NULL AND target_home_away IS NOT NULL)""",
            [],
        ),
        (
            "rolling sample counts exceed their windows",
            """SELECT count(*) = 0 FROM player_gameweek_features
                WHERE rolling_3_gameweeks_with_data > 3
                   OR rolling_5_gameweeks_with_data > 5
                   OR rolling_3_gameweeks_with_data > prior_gameweeks_with_data
                   OR rolling_5_gameweeks_with_data > prior_gameweeks_with_data""",
            [],
        ),
        (
            "missing history was converted into observed zero statistics",
            """SELECT count(*) = 0 FROM player_gameweek_features
                WHERE prior_gameweeks_with_data = 0
                  AND (prior_total_minutes IS NOT NULL
                       OR cumulative_prior_xg IS NOT NULL
                       OR cumulative_prior_xa IS NOT NULL
                       OR average_prior_points IS NOT NULL)""",
            [],
        ),
        (
            "per-90 values must be null when historical minutes are zero",
            """SELECT count(*) = 0 FROM player_gameweek_features
                WHERE coalesce(prior_total_minutes, 0) = 0
                  AND (prior_xg_per_90 IS NOT NULL
                       OR prior_xa_per_90 IS NOT NULL
                       OR prior_xgc_per_90 IS NOT NULL
                       OR prior_defensive_contribution_per_90 IS NOT NULL
                       OR prior_saves_per_90 IS NOT NULL)""",
            [],
        ),
    )
    for message, query, parameters in checks:
        if not connection.execute(query, parameters).fetchone()[0]:
            raise DataQualityError(message)


def build_player_gameweek_features(
    *,
    target_gameweek: int,
    raw_data_root: Path = Path("data/raw/fpl"),
    clean_data_root: Path = Path("data/clean/fpl"),
    feature_data_root: Path = Path("data/features/fpl"),
    season: str = "2026-27",
    snapshot_timestamp: str | None = None,
) -> Path:
    """Build pre-deadline features using only history before the target gameweek."""
    if target_gameweek < 1:
        raise FeatureBuildError("target_gameweek must be at least 1")
    selected_snapshot, paths, temporal_context = find_feature_snapshot(
        raw_data_root=raw_data_root,
        clean_data_root=clean_data_root,
        season=season,
        target_gameweek=target_gameweek,
        snapshot_timestamp=snapshot_timestamp,
    )
    (
        deadline,
        snapshot_time,
        fixture_time,
        history_time,
        next_gameweek_id,
    ) = temporal_context
    output_path = (
        feature_data_root
        / season
        / selected_snapshot
        / f"gameweek={target_gameweek}"
        / "player_gameweek_features.parquet"
    )
    if output_path.exists():
        raise CleanOutputExistsError(
            f"feature output already exists and will not be overwritten: {output_path}"
        )

    input_hashes = {name: _sha256(path) for name, path in paths.items()}
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE players_input AS SELECT * FROM read_parquet(?)",
            [str(paths["players"])],
        )
        connection.execute(
            "CREATE TABLE fixtures_input AS SELECT * FROM read_parquet(?)",
            [str(paths["fixtures"])],
        )
        connection.execute(
            "CREATE TABLE history_input AS SELECT * FROM read_parquet(?)",
            [str(paths["history"])],
        )
        connection.execute(
            "CREATE TABLE valid_teams AS "
            "SELECT DISTINCT team_id AS id FROM players_input"
        )
        connection.execute(
            """CREATE TABLE build_context (
                   season VARCHAR,
                   snapshot_timestamp VARCHAR,
                   target_gameweek INTEGER,
                   target_deadline_time TIMESTAMPTZ,
                   availability_as_of TIMESTAMPTZ,
                   fixture_retrieved_at TIMESTAMPTZ,
                   history_retrieved_at TIMESTAMPTZ,
                   availability_reference_gameweek INTEGER,
                   players_input_sha256 VARCHAR,
                   fixtures_input_sha256 VARCHAR,
                   history_input_sha256 VARCHAR,
                   bootstrap_sha256 VARCHAR
               )"""
        )
        connection.execute(
            "INSERT INTO build_context VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                season,
                selected_snapshot,
                target_gameweek,
                deadline,
                snapshot_time,
                fixture_time,
                history_time,
                next_gameweek_id,
                input_hashes["players"],
                input_hashes["fixtures"],
                input_hashes["history"],
                input_hashes["bootstrap"],
            ],
        )
        _create_feature_table(connection, target_gameweek=target_gameweek)
        _validate_features(connection, target_gameweek)
        if any(_sha256(paths[name]) != digest for name, digest in input_hashes.items()):
            raise DataQualityError("a raw or clean input changed during feature building")
        _copy_parquet_exclusive(connection, output_path)
    finally:
        connection.close()
    return output_path
