"""Frozen xFP v0.1 restricted/pseudo-historical backtest."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from .historical import (
    FIXTURE_ASSIGNMENT_CONTEXT,
    HISTORICAL_CLASSIFICATION,
    HISTORY_CUTOFF_RULE,
)
from .predictions import MODEL_VERSION
from .transform import TransformationError


BACKTEST_VERSION = "xfp-v01-baseline-v1"
MODEL_IDENTIFIER = "xfp_v01"
EXPECTED_HISTORICAL_VERSION = "historical-v2"
DEFAULT_SEASONS = ("2023-24", "2024-25")
DEFAULT_GAMEWEEKS = tuple(range(2, 39))
TOP_N_VALUES = (10, 25, 50)
CALIBRATION_BINS = (
    ("0-<1", 0.0, 1.0),
    ("1-<2", 1.0, 2.0),
    ("2-<3", 2.0, 3.0),
    ("3-<4", 3.0, 4.0),
    ("4-<5", 4.0, 5.0),
    ("5-<7", 5.0, 7.0),
    ("7+", 7.0, None),
)
POSITION_GOAL_POINTS = {"GK": 10, "DEF": 6, "MID": 5, "FWD": 4}


class HistoricalBacktestError(TransformationError):
    """Raised when the frozen historical baseline cannot be measured safely."""


class HistoricalBacktestOutputExistsError(HistoricalBacktestError):
    """Raised rather than overwriting an immutable completed backtest."""


@dataclass(frozen=True)
class HistoricalBacktestResult:
    directory: Path
    manifest_path: Path
    player_gameweek_path: Path
    observations: int
    modeled_complete_pairs: int


METRIC_SCHEMA = (
    ("scope_type", "VARCHAR"), ("scope_value", "VARCHAR"),
    ("target", "VARCHAR"), ("predictor", "VARCHAR"),
    ("n_eligible", "BIGINT"), ("n_complete_pairs", "BIGINT"),
    ("missing_prediction", "BIGINT"), ("missing_actual", "BIGINT"),
    ("coverage_pct", "DOUBLE"), ("mae", "DOUBLE"),
    ("rmse", "DOUBLE"), ("bias", "DOUBLE"),
    ("spearman", "DOUBLE"), ("mean_prediction", "DOUBLE"),
    ("mean_actual", "DOUBLE"), ("total_prediction", "DOUBLE"),
    ("total_actual", "DOUBLE"),
)

RANKING_SCHEMA = (
    ("row_type", "VARCHAR"), ("season", "VARCHAR"),
    ("target_gameweek", "INTEGER"), ("target", "VARCHAR"),
    ("top_n", "INTEGER"), ("n_complete_pairs", "BIGINT"),
    ("strict_n_available", "BOOLEAN"), ("overlap_count", "DOUBLE"),
    ("overlap_pct", "DOUBLE"), ("gameweeks_summarized", "INTEGER"),
    ("tie_breaker", "VARCHAR"),
)

CALIBRATION_SCHEMA = (
    ("bin", "VARCHAR"), ("lower_inclusive", "DOUBLE"),
    ("upper_exclusive", "DOUBLE"), ("n", "BIGINT"),
    ("mean_prediction", "DOUBLE"), ("mean_actual", "DOUBLE"),
    ("bias", "DOUBLE"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso_utc(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HistoricalBacktestError(f"could not read historical manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HistoricalBacktestError(f"historical manifest is not an object: {path}")
    return value


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
            raise HistoricalBacktestOutputExistsError(
                f"backtest output already exists and will not be overwritten: {path}"
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _create_rows_table(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    schema: tuple[tuple[str, str], ...],
    rows: Sequence[tuple[Any, ...]],
) -> None:
    columns = ", ".join(f'"{name}" {data_type}' for name, data_type in schema)
    connection.execute(f'CREATE OR REPLACE TABLE "{table}" ({columns})')
    if not rows:
        return
    row_placeholders = f"({', '.join('?' for _ in schema)})"
    for start in range(0, len(rows), 250):
        batch = rows[start : start + 250]
        values = ", ".join(row_placeholders for _ in batch)
        parameters = [value for row in batch for value in row]
        connection.execute(f'INSERT INTO "{table}" VALUES {values}', parameters)


def _average_ranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        average = ((start + 1) + end) / 2.0
        for offset in range(start, end):
            ranks[ordered[offset]] = average
        start = end
    return ranks


def _spearman(predictions: Sequence[float], actuals: Sequence[float]) -> float | None:
    if len(predictions) < 2:
        return None
    predicted_ranks = _average_ranks(predictions)
    actual_ranks = _average_ranks(actuals)
    mean_predicted = sum(predicted_ranks) / len(predicted_ranks)
    mean_actual = sum(actual_ranks) / len(actual_ranks)
    covariance = sum(
        (left - mean_predicted) * (right - mean_actual)
        for left, right in zip(predicted_ranks, actual_ranks, strict=True)
    )
    predicted_variance = sum((value - mean_predicted) ** 2 for value in predicted_ranks)
    actual_variance = sum((value - mean_actual) ** 2 for value in actual_ranks)
    denominator = math.sqrt(predicted_variance * actual_variance)
    return covariance / denominator if denominator else None


def _metric_row(
    rows: Sequence[dict[str, Any]],
    *,
    scope_type: str,
    scope_value: str,
    target: str,
    predictor: str,
    prediction_field: str,
    actual_field: str,
) -> tuple[Any, ...]:
    pairs = [
        (row[prediction_field], row[actual_field])
        for row in rows
        if row[prediction_field] is not None and row[actual_field] is not None
    ]
    missing_prediction = sum(row[prediction_field] is None for row in rows)
    missing_actual = sum(row[actual_field] is None for row in rows)
    if pairs:
        predictions = [float(pair[0]) for pair in pairs]
        actuals = [float(pair[1]) for pair in pairs]
        errors = [prediction - actual for prediction, actual in zip(predictions, actuals, strict=True)]
        mae = sum(abs(error) for error in errors) / len(errors)
        rmse = math.sqrt(sum(error * error for error in errors) / len(errors))
        bias = sum(errors) / len(errors)
        correlation = _spearman(predictions, actuals)
        mean_prediction = sum(predictions) / len(predictions)
        mean_actual = sum(actuals) / len(actuals)
        total_prediction = sum(predictions)
        total_actual = sum(actuals)
    else:
        mae = rmse = bias = correlation = None
        mean_prediction = mean_actual = total_prediction = total_actual = None
    return (
        scope_type, scope_value, target, predictor, len(rows), len(pairs),
        missing_prediction, missing_actual,
        100.0 * len(pairs) / len(rows) if rows else None,
        mae, rmse, bias, correlation, mean_prediction, mean_actual,
        total_prediction, total_actual,
    )


def _input_paths(
    historical_clean_root: Path, seasons: Sequence[str]
) -> tuple[Path, dict[str, dict[str, Path]]]:
    version_root = historical_clean_root / EXPECTED_HISTORICAL_VERSION
    manifest_path = version_root / "historical_ingestion_manifest.json"
    paths: dict[str, dict[str, Path]] = {}
    for season in seasons:
        season_root = version_root / season
        paths[season] = {
            "features": season_root / "historical_prediction_features.parquet",
            "player_fixture": season_root / "historical_player_fixture.parquet",
            "predeadline": season_root / "historical_predeadline_player_state.parquet",
        }
    return manifest_path, paths


def _validate_input_manifest(
    manifest_path: Path,
    paths: dict[str, dict[str, Path]],
    seasons: Sequence[str],
) -> tuple[dict[str, Any], dict[Path, str]]:
    manifest = _load_json(manifest_path)
    if manifest.get("status") != "complete":
        raise HistoricalBacktestError("historical-v2 ingestion is not complete")
    if manifest.get("parser_schema_version") != EXPECTED_HISTORICAL_VERSION:
        raise HistoricalBacktestError("historical input is not historical-v2")
    if manifest.get("historical_classification") != HISTORICAL_CLASSIFICATION:
        raise HistoricalBacktestError("historical classification changed")
    if list(manifest.get("seasons", [])) != list(seasons):
        raise HistoricalBacktestError("historical seasons do not match requested backtest scope")
    outputs = {entry["path"]: entry["sha256"] for entry in manifest.get("outputs", [])}
    hashes: dict[Path, str] = {manifest_path: _sha256(manifest_path)}
    version_root = manifest_path.parent
    for season_paths in paths.values():
        for path in season_paths.values():
            if not path.is_file():
                raise HistoricalBacktestError(f"missing historical-v2 input: {path}")
            relative = str(path.relative_to(version_root))
            expected = outputs.get(relative)
            observed = _sha256(path)
            if expected is None or observed != expected:
                raise HistoricalBacktestError(
                    f"historical-v2 input hash does not match its ingestion manifest: {path}"
                )
            hashes[path] = observed
    return manifest, hashes


def _load_input_tables(
    connection: duckdb.DuckDBPyConnection,
    paths: dict[str, dict[str, Path]],
    gameweeks: Sequence[int],
) -> None:
    feature_paths = [str(value["features"]) for value in paths.values()]
    fixture_paths = [str(value["player_fixture"]) for value in paths.values()]
    state_paths = [str(value["predeadline"]) for value in paths.values()]
    minimum, maximum = min(gameweeks), max(gameweeks)
    connection.execute(
        """CREATE TABLE historical_features AS
           SELECT * FROM read_parquet(?)
           WHERE target_gameweek BETWEEN ? AND ?""",
        [feature_paths, minimum, maximum],
    )
    connection.execute(
        """CREATE TABLE historical_actual_fixture AS
           SELECT * FROM read_parquet(?)
           WHERE gameweek BETWEEN ? AND ?""",
        [fixture_paths, minimum, maximum],
    )
    connection.execute(
        """CREATE TABLE historical_predeadline AS
           SELECT * FROM read_parquet(?)
           WHERE target_gameweek BETWEEN ? AND ?""",
        [state_paths, minimum, maximum],
    )
    requested = ",".join(str(int(gameweek)) for gameweek in gameweeks)
    for table, field in (
        ("historical_features", "target_gameweek"),
        ("historical_actual_fixture", "gameweek"),
        ("historical_predeadline", "target_gameweek"),
    ):
        connection.execute(
            f"DELETE FROM {table} WHERE {field} NOT IN ({requested})"
        )


def _validate_inputs(
    connection: duckdb.DuckDBPyConnection,
    *,
    seasons: Sequence[str],
    gameweeks: Sequence[int],
    strict_scope: bool,
) -> None:
    if strict_scope and (tuple(seasons) != DEFAULT_SEASONS or tuple(gameweeks) != DEFAULT_GAMEWEEKS):
        raise HistoricalBacktestError("production historical backtest scope must be both seasons and GWs 2-38")
    identity = connection.execute(
        """SELECT count(*), count(DISTINCT season), min(target_gameweek),
                  max(target_gameweek), count(DISTINCT target_gameweek)
           FROM historical_features"""
    ).fetchone()
    if identity[0] == 0:
        raise HistoricalBacktestError("historical-v2 feature input is empty")
    if strict_scope and identity[1:] != (2, 2, 38, 37):
        raise HistoricalBacktestError(f"historical feature scope is incomplete: {identity}")
    bad_temporal = connection.execute(
        """SELECT count(*) FROM historical_features
           WHERE historical_classification <> ?
              OR fixture_assignment_context <> ?
              OR history_cutoff_rule <> ?
              OR history_gameweek_max_used >= target_gameweek
              OR history_latest_kickoff_used >= target_deadline
              OR snapshot_timestamp >= target_deadline
              OR availability_known_pre_deadline IS NOT TRUE
              OR vaastav_xp_excluded IS NOT TRUE""",
        [HISTORICAL_CLASSIFICATION, FIXTURE_ASSIGNMENT_CONTEXT, HISTORY_CUTOFF_RULE],
    ).fetchone()[0]
    if bad_temporal:
        raise HistoricalBacktestError(
            f"historical input violates frozen deadline chronology in {bad_temporal} rows"
        )
    duplicate_features = connection.execute(
        """SELECT count(*) FROM (
             SELECT season,target_gameweek,element_id,coalesce(target_fixture_id,-1),count(*) n
             FROM historical_features GROUP BY ALL HAVING n<>1)"""
    ).fetchone()[0]
    if duplicate_features:
        raise HistoricalBacktestError("historical feature fixture keys are not unique")
    invalid_grain = connection.execute(
        """SELECT count(*) FROM (
             SELECT season,target_gameweek,element_id,
                    min(target_fixture_count) fixture_count,
                    count(*) row_count,
                    count(target_fixture_id) fixture_rows
             FROM historical_features GROUP BY season,target_gameweek,element_id)
           WHERE (fixture_count=0 AND (row_count<>1 OR fixture_rows<>0))
              OR (fixture_count>0 AND (row_count<>fixture_count OR fixture_rows<>fixture_count))"""
    ).fetchone()[0]
    if invalid_grain:
        raise HistoricalBacktestError("missing fixture information cannot be interpreted as a blank")
    state_mismatch = connection.execute(
        """SELECT count(*) FROM (
             SELECT DISTINCT season,target_gameweek,element_id FROM historical_features
             EXCEPT
             SELECT season,target_gameweek,element_id FROM historical_predeadline)"""
    ).fetchone()[0]
    if state_mismatch:
        raise HistoricalBacktestError("feature player universe does not resolve to pre-deadline state")
    invalid_positions = connection.execute(
        """SELECT count(*) FROM historical_features
           WHERE historical_position NOT IN ('GK','DEF','MID','FWD')"""
    ).fetchone()[0]
    if invalid_positions:
        raise HistoricalBacktestError("assistant-manager or unknown position entered backtest features")
    duplicate_actual = connection.execute(
        """SELECT count(*) FROM (
             SELECT season,element_id,fixture_id,count(*) n
             FROM historical_actual_fixture GROUP BY ALL HAVING n<>1)"""
    ).fetchone()[0]
    if duplicate_actual:
        raise HistoricalBacktestError("historical actual player-fixture keys are not unique")


def _create_scored_tables(connection: duckdb.DuckDBPyConnection) -> None:
    """Reproduce frozen live v0.1 mechanics and independent historical actuals."""
    connection.execute(
        """CREATE TABLE fixture_predictions AS
           WITH minutes_estimate AS (
             SELECT *,
                    target_fixture_id IS NOT NULL AS target_has_fixture,
                    CASE
                      WHEN previous_gameweek_minutes_uncapped IS NULL THEN NULL
                      WHEN availability_known_pre_deadline
                       AND chance_of_playing_next_round=0 THEN 0.0
                      WHEN availability_known_pre_deadline
                       AND lower(availability_status) IN ('s','u') THEN 0.0
                      ELSE greatest(0.0,least(90.0,previous_gameweek_minutes_uncapped))
                    END expected_minutes_v01,
                    CASE
                      WHEN availability_known_pre_deadline
                       AND chance_of_playing_next_round=0 THEN 'explicit_zero_chance'
                      WHEN availability_known_pre_deadline
                       AND lower(availability_status)='s' THEN 'suspended'
                      WHEN availability_known_pre_deadline
                       AND lower(availability_status)='u' THEN 'unavailable'
                    END availability_gate_reason,
                    prior_total_minutes>0 AND prior_xg_per_90 IS NOT NULL
                      AND prior_xa_per_90 IS NOT NULL AS attacking_rate_available
             FROM historical_features
           ), components AS (
             SELECT *,
                    CASE WHEN expected_minutes_v01 IS NULL THEN NULL
                         WHEN expected_minutes_v01=0 THEN 0.0
                         WHEN expected_minutes_v01<60 THEN 1.0 ELSE 2.0 END
                      AS appearance_xfp_v01,
                    CASE WHEN prior_xg_per_90 IS NOT NULL
                               AND expected_minutes_v01 IS NOT NULL
                         THEN prior_xg_per_90*expected_minutes_v01/90.0 END
                      AS expected_goals_v01,
                    CASE WHEN prior_xa_per_90 IS NOT NULL
                               AND expected_minutes_v01 IS NOT NULL
                         THEN prior_xa_per_90*expected_minutes_v01/90.0 END
                      AS expected_assists_v01,
                    CASE historical_position WHEN 'GK' THEN 10 WHEN 'DEF' THEN 6
                         WHEN 'MID' THEN 5 WHEN 'FWD' THEN 4 END
                      AS goal_points_for_position
             FROM minutes_estimate
           )
           SELECT season,?::VARCHAR model_identifier,?::VARCHAR model_version,
                  target_gameweek,target_fixture_id fixture_id,target_has_fixture,
                  target_fixture_count,element_id,code,historical_position AS "position",
                  snapshot_team_id team_id,snapshot_team_name team_name,
                  target_opponent_team_id opponent_team_id,target_home_away home_away,
                  target_kickoff_time kickoff_time,target_deadline,snapshot_timestamp,
                  expected_minutes_v01,previous_gameweek_minutes_uncapped,
                  prior_total_minutes,prior_gameweeks_with_data,prior_fixture_rows,
                  history_gameweek_max_used,history_latest_kickoff_used,
                  history_cutoff_rule,prior_xg_per_90 prior_xg_per_90_used,
                  prior_xa_per_90 prior_xa_per_90_used,appearance_xfp_v01,
                  expected_goals_v01,goal_points_for_position,
                  expected_goals_v01*goal_points_for_position goal_xfp_v01,
                  expected_assists_v01,
                  expected_assists_v01*3.0 assist_xfp_v01,
                  CASE WHEN target_has_fixture AND appearance_xfp_v01 IS NOT NULL
                       THEN appearance_xfp_v01
                            +coalesce(expected_goals_v01*goal_points_for_position,0.0)
                            +coalesce(expected_assists_v01*3.0,0.0)
                       WHEN NOT target_has_fixture THEN NULL END fixture_xfp_v01,
                  attacking_rate_available,
                  target_has_fixture AND appearance_xfp_v01 IS NOT NULL
                    AND expected_goals_v01 IS NOT NULL
                    AND expected_assists_v01 IS NOT NULL prediction_complete,
                  prior_gameweeks_with_data<3 low_sample,availability_status,
                  chance_of_playing_next_round,availability_news,
                  availability_known_pre_deadline,
                  availability_gate_reason IS NOT NULL availability_forced_zero,
                  availability_gate_reason,previous_gw_context_status,
                  previous_gw_team_blank,previous_gw_player_not_in_universe,
                  chronologically_excluded_prior_fixture_rows,
                  historical_classification,predeadline_source_path,
                  predeadline_source_sha256
           FROM components""",
        [MODEL_IDENTIFIER, MODEL_VERSION],
    )
    connection.execute(
        """CREATE TABLE gameweek_predictions AS
           SELECT season,model_identifier,model_version,target_gameweek,element_id,
                  min(code) code,min("position") AS "position",min(team_id) team_id,
                  min(team_name) team_name,
                  count(*) FILTER(WHERE target_has_fixture) fixture_count,
                  coalesce(sum(expected_minutes_v01) FILTER(WHERE target_has_fixture),0.0)
                    gameweek_expected_minutes_v01,
                  CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                       WHEN count(expected_minutes_v01) FILTER(WHERE target_has_fixture)
                            =count(*) FILTER(WHERE target_has_fixture)
                       THEN sum(expected_minutes_v01) FILTER(WHERE target_has_fixture) END
                    gameweek_expected_minutes_for_evaluation,
                  coalesce(sum(appearance_xfp_v01) FILTER(WHERE target_has_fixture),0.0)
                    gameweek_appearance_xfp_v01,
                  CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                       WHEN count(appearance_xfp_v01) FILTER(WHERE target_has_fixture)
                            =count(*) FILTER(WHERE target_has_fixture)
                       THEN sum(appearance_xfp_v01) FILTER(WHERE target_has_fixture) END
                    gameweek_appearance_xfp_for_evaluation,
                  coalesce(sum(goal_xfp_v01) FILTER(WHERE target_has_fixture),0.0)
                    gameweek_goal_xfp_v01,
                  CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                       WHEN count(goal_xfp_v01) FILTER(WHERE target_has_fixture)
                            =count(*) FILTER(WHERE target_has_fixture)
                       THEN sum(goal_xfp_v01) FILTER(WHERE target_has_fixture) END
                    gameweek_goal_xfp_for_evaluation,
                  coalesce(sum(assist_xfp_v01) FILTER(WHERE target_has_fixture),0.0)
                    gameweek_assist_xfp_v01,
                  CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                       WHEN count(assist_xfp_v01) FILTER(WHERE target_has_fixture)
                            =count(*) FILTER(WHERE target_has_fixture)
                       THEN sum(assist_xfp_v01) FILTER(WHERE target_has_fixture) END
                    gameweek_assist_xfp_for_evaluation,
                  CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                       WHEN count(fixture_xfp_v01) FILTER(WHERE target_has_fixture)
                            =count(*) FILTER(WHERE target_has_fixture)
                       THEN sum(fixture_xfp_v01) FILTER(WHERE target_has_fixture) END
                    gameweek_xfp_v01,
                  bool_and(attacking_rate_available) attacking_rate_available,
                  CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN true
                       ELSE bool_and(prediction_complete) FILTER(WHERE target_has_fixture) END
                    prediction_complete,
                  bool_or(low_sample) low_sample,min(prior_total_minutes) prior_total_minutes,
                  min(prior_gameweeks_with_data) prior_gameweeks_with_data,
                  min(previous_gameweek_minutes_uncapped) previous_gameweek_minutes_uncapped,
                  min(previous_gw_context_status) previous_gw_context_status,
                  bool_or(previous_gw_team_blank) previous_gw_team_blank,
                  bool_or(previous_gw_player_not_in_universe) previous_gw_player_not_in_universe,
                  min(availability_status) availability_status,
                  min(chance_of_playing_next_round) chance_of_playing_next_round,
                  bool_or(availability_forced_zero) availability_forced_zero,
                  min(availability_gate_reason) availability_gate_reason,
                  min(target_deadline) target_deadline,min(snapshot_timestamp) snapshot_timestamp,
                  min(history_gameweek_max_used) history_gameweek_max_used,
                  max(history_latest_kickoff_used) history_latest_kickoff_used,
                  min(history_cutoff_rule) history_cutoff_rule,
                  min(historical_classification) historical_classification
           FROM fixture_predictions
           GROUP BY season,model_identifier,model_version,target_gameweek,element_id"""
    )
    connection.execute(
        """CREATE TABLE fixture_actuals AS
           WITH frozen_state AS (
             SELECT season,target_gameweek,element_id,
                    min(historical_position) frozen_position
             FROM historical_features GROUP BY season,target_gameweek,element_id
           )
           SELECT a.season,a.gameweek target_gameweek,a.element_id,a.fixture_id,
                  s.frozen_position,a.historical_position archived_row_position,
                  a.historical_team_id actual_team_id,a.opponent_team_id,a.home_away,
                  a.kickoff_time,a.minutes actual_minutes,
                  a.actual_appearance_points_v01 actual_appearance_points,
                  a.goals,
                  a.goals*CASE s.frozen_position WHEN 'GK' THEN 10 WHEN 'DEF' THEN 6
                         WHEN 'MID' THEN 5 WHEN 'FWD' THEN 4 END actual_goal_points,
                  a.assists,a.assists*3 actual_assist_points,
                  a.actual_appearance_points_v01
                    +a.goals*CASE s.frozen_position WHEN 'GK' THEN 10 WHEN 'DEF' THEN 6
                         WHEN 'MID' THEN 5 WHEN 'FWD' THEN 4 END
                    +a.assists*3 actual_modeled_points,
                  a.total_points actual_full_fpl_points,
                  'actual_points_under_historical_season_rules' full_points_context,
                  true actuals_not_predictors,a.source_path,a.source_sha256,
                  a.source_row_number
           FROM historical_actual_fixture a JOIN frozen_state s
             ON a.season=s.season AND a.gameweek=s.target_gameweek
            AND a.element_id=s.element_id"""
    )
    connection.execute(
        """CREATE TABLE gameweek_actuals AS
           SELECT season,target_gameweek,element_id,count(*) actual_fixture_count,
                  sum(actual_minutes) actual_minutes,
                  sum(actual_appearance_points) actual_appearance_points,
                  sum(actual_goal_points) actual_goal_points,
                  sum(actual_assist_points) actual_assist_points,
                  sum(actual_modeled_points) actual_modeled_points,
                  sum(actual_full_fpl_points) actual_full_fpl_points
           FROM fixture_actuals GROUP BY season,target_gameweek,element_id"""
    )
    connection.execute(
        """CREATE TABLE player_gameweek AS
           SELECT p.*,
                  s.ep_next,
                  CASE WHEN p.fixture_count=0 THEN 0 ELSE a.actual_fixture_count END
                    actual_fixture_count,
                  CASE WHEN p.fixture_count=0 THEN 0 ELSE a.actual_minutes END actual_minutes,
                  CASE WHEN p.fixture_count=0 THEN 0 ELSE a.actual_appearance_points END
                    actual_appearance_points,
                  CASE WHEN p.fixture_count=0 THEN 0 ELSE a.actual_goal_points END
                    actual_goal_points,
                  CASE WHEN p.fixture_count=0 THEN 0 ELSE a.actual_assist_points END
                    actual_assist_points,
                  CASE WHEN p.fixture_count=0 THEN 0 ELSE a.actual_modeled_points END
                    actual_modeled_points,
                  CASE WHEN p.fixture_count=0 THEN 0 ELSE a.actual_full_fpl_points END
                    actual_full_fpl_points,
                  CASE WHEN p.fixture_count=0 THEN 'verified_blank'
                       WHEN a.element_id IS NULL THEN 'missing_or_corrupt_actual'
                       WHEN a.actual_minutes=0 THEN 'actual_zero_minutes'
                       ELSE 'actual_available' END actual_state,
                  CASE WHEN p.fixture_count=0 THEN 'verified_blank'
                       WHEN p.gameweek_xfp_v01 IS NULL THEN 'missing_expected_minutes'
                       WHEN NOT p.prediction_complete THEN 'incomplete_attacking_rate'
                       ELSE 'valid_prediction' END prediction_state,
                  CASE WHEN p.gameweek_expected_minutes_for_evaluation IS NULL THEN 'unknown'
                       WHEN p.fixture_count=0 THEN '0'
                       WHEN p.gameweek_expected_minutes_v01=0 THEN '0'
                       WHEN p.gameweek_expected_minutes_v01<30 THEN '1-29'
                       WHEN p.gameweek_expected_minutes_v01<60 THEN '30-59'
                       WHEN p.gameweek_expected_minutes_v01<90 THEN '60-89'
                       ELSE '90' END expected_minutes_band,
                  CASE WHEN NOT p.attacking_rate_available THEN 'no_usable_prior_attacking_sample'
                       WHEN p.prior_gameweeks_with_data=1 THEN '1_prior_GW'
                       WHEN p.prior_gameweeks_with_data BETWEEN 2 AND 3 THEN '2-3_prior_GWs'
                       WHEN p.prior_gameweeks_with_data BETWEEN 4 AND 5 THEN '4-5_prior_GWs'
                       ELSE '6+_prior_GWs' END prior_sample_band,
                  CASE WHEN lower(p.availability_status) IN ('s','u')
                              OR p.chance_of_playing_next_round=0
                       THEN 'unavailable_or_suspended'
                       WHEN lower(p.availability_status) IN ('d','i')
                              OR p.chance_of_playing_next_round IN (25,50,75)
                       THEN 'doubtful_or_chance_limited'
                       WHEN lower(p.availability_status)='a' THEN 'available'
                       ELSE 'unknown' END availability_band,
                  p.gameweek_xfp_v01-a.actual_modeled_points modeled_error,
                  p.gameweek_xfp_v01-a.actual_full_fpl_points full_fpl_error
           FROM gameweek_predictions p
           LEFT JOIN gameweek_actuals a USING(season,target_gameweek,element_id)
           LEFT JOIN historical_predeadline s USING(season,target_gameweek,element_id)"""
    )


def _validate_scored_tables(connection: duckdb.DuckDBPyConnection) -> None:
    checks = (
        ("chronological input entered prediction", "SELECT count(*) FROM fixture_predictions WHERE history_latest_kickoff_used>=target_deadline"),
        ("assistant manager entered backtest", "SELECT count(*) FROM player_gameweek WHERE \"position\" NOT IN ('GK','DEF','MID','FWD')"),
        ("fixture prediction key is duplicated", "SELECT count(*) FROM (SELECT season,target_gameweek,element_id,coalesce(fixture_id,-1),count(*) n FROM fixture_predictions GROUP BY ALL HAVING n<>1)"),
        ("player-gameweek key is duplicated", "SELECT count(*) FROM (SELECT season,target_gameweek,element_id,count(*) n FROM player_gameweek GROUP BY ALL HAVING n<>1)"),
        ("blank prediction/actual is not zero", "SELECT count(*) FROM player_gameweek WHERE fixture_count=0 AND (gameweek_xfp_v01<>0 OR actual_modeled_points<>0 OR actual_full_fpl_points<>0 OR actual_state<>'verified_blank')"),
        ("normal/DGW prediction aggregation failed", "SELECT count(*) FROM gameweek_predictions g JOIN (SELECT season,target_gameweek,element_id,sum(fixture_xfp_v01) x,count(*) FILTER(WHERE target_has_fixture) n,count(fixture_xfp_v01) FILTER(WHERE target_has_fixture) scored FROM fixture_predictions GROUP BY season,target_gameweek,element_id) f USING(season,target_gameweek,element_id) WHERE f.n>0 AND f.n=f.scored AND abs(g.gameweek_xfp_v01-f.x)>1e-10"),
        ("normal/DGW actual aggregation failed", "SELECT count(*) FROM gameweek_actuals g JOIN (SELECT season,target_gameweek,element_id,sum(actual_modeled_points) x,sum(actual_full_fpl_points) full_x,sum(actual_minutes) m FROM fixture_actuals GROUP BY season,target_gameweek,element_id) f USING(season,target_gameweek,element_id) WHERE g.actual_modeled_points<>f.x OR g.actual_full_fpl_points<>f.full_x OR g.actual_minutes<>f.m"),
        ("historical actual goal points did not use frozen position", "SELECT count(*) FROM fixture_actuals WHERE actual_goal_points<>goals*CASE frozen_position WHEN 'GK' THEN 10 WHEN 'DEF' THEN 6 WHEN 'MID' THEN 5 WHEN 'FWD' THEN 4 END"),
        ("missing attacking events became zero-valued events", "SELECT count(*) FROM fixture_predictions WHERE prior_xg_per_90_used IS NULL AND expected_goals_v01 IS NOT NULL OR prior_xa_per_90_used IS NULL AND expected_assists_v01 IS NOT NULL"),
    )
    for message, query in checks:
        if connection.execute(query).fetchone()[0]:
            raise HistoricalBacktestError(message)


def _player_rows(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cursor = connection.execute(
        """SELECT season,target_gameweek,element_id,"position",fixture_count,
                  gameweek_expected_minutes_v01,
                  gameweek_expected_minutes_for_evaluation,
                  gameweek_appearance_xfp_v01,
                  gameweek_appearance_xfp_for_evaluation,
                  gameweek_goal_xfp_v01,gameweek_goal_xfp_for_evaluation,
                  gameweek_assist_xfp_v01,gameweek_assist_xfp_for_evaluation,
                  gameweek_xfp_v01,attacking_rate_available,prediction_complete,
                  prior_gameweeks_with_data,previous_gameweek_minutes_uncapped,
                  previous_gw_context_status,previous_gw_team_blank,
                  previous_gw_player_not_in_universe,availability_status,
                  chance_of_playing_next_round,ep_next,actual_fixture_count,
                  actual_minutes,actual_appearance_points,actual_goal_points,
                  actual_assist_points,actual_modeled_points,actual_full_fpl_points,
                  actual_state,prediction_state,expected_minutes_band,
                  prior_sample_band,availability_band
           FROM player_gameweek"""
    )
    columns = [item[0] for item in cursor.description]
    rows = []
    for values in cursor.fetchall():
        row = dict(zip(columns, values, strict=True))
        row["zero_modeled_baseline"] = 0.0
        row["zero_on_xfp_population"] = (
            0.0 if row["gameweek_xfp_v01"] is not None else None
        )
        rows.append(row)
    return rows


def _metric_tables(
    connection: duckdb.DuckDBPyConnection,
) -> tuple[list[dict[str, Any]], dict[str, list[tuple[Any, ...]]]]:
    rows = _player_rows(connection)
    outputs: dict[str, list[tuple[Any, ...]]] = {}
    outputs["overall_metrics"] = [
        _metric_row(
            rows, scope_type="overall", scope_value="all",
            target="modeled_components", predictor=MODEL_IDENTIFIER,
            prediction_field="gameweek_xfp_v01", actual_field="actual_modeled_points",
        ),
        _metric_row(
            rows, scope_type="overall", scope_value="all",
            target="full_historical_fpl_points", predictor=MODEL_IDENTIFIER,
            prediction_field="gameweek_xfp_v01", actual_field="actual_full_fpl_points",
        ),
    ]

    def grouped_metrics(field: str, scope_type: str) -> list[tuple[Any, ...]]:
        grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[row[field]].append(row)
        return [
            _metric_row(
                group, scope_type=scope_type, scope_value=str(value),
                target="modeled_components", predictor=MODEL_IDENTIFIER,
                prediction_field="gameweek_xfp_v01", actual_field="actual_modeled_points",
            )
            for value, group in sorted(grouped.items(), key=lambda item: str(item[0]))
        ]

    outputs["season_metrics"] = grouped_metrics("season", "season")
    outputs["position_metrics"] = grouped_metrics("position", "position")
    season_gameweek: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        season_gameweek[(row["season"], row["target_gameweek"])].append(row)
    outputs["gameweek_metrics"] = [
        _metric_row(
            group, scope_type="season_gameweek",
            scope_value=f"{season}/GW{gameweek}", target="modeled_components",
            predictor=MODEL_IDENTIFIER, prediction_field="gameweek_xfp_v01",
            actual_field="actual_modeled_points",
        )
        for (season, gameweek), group in sorted(season_gameweek.items())
    ]
    outputs["expected_minutes_diagnostics"] = grouped_metrics(
        "expected_minutes_band", "expected_minutes_band"
    )
    outputs["prior_sample_diagnostics"] = grouped_metrics(
        "prior_sample_band", "prior_sample_band"
    )
    outputs["availability_diagnostics"] = grouped_metrics(
        "availability_band", "availability_band"
    )

    component_fields = (
        ("appearance", "gameweek_appearance_xfp_for_evaluation", "actual_appearance_points"),
        ("goals", "gameweek_goal_xfp_for_evaluation", "actual_goal_points"),
        ("assists", "gameweek_assist_xfp_for_evaluation", "actual_assist_points"),
    )
    outputs["component_metrics"] = [
        _metric_row(
            rows, scope_type="component", scope_value=name, target=name,
            predictor=f"{MODEL_IDENTIFIER}_{name}", prediction_field=prediction,
            actual_field=actual,
        )
        for name, prediction, actual in component_fields
    ]
    outputs["minutes_metrics"] = [
        _metric_row(
            rows, scope_type="overall", scope_value="all",
            target="gameweek_minutes", predictor="v01_expected_minutes",
            prediction_field="gameweek_expected_minutes_for_evaluation",
            actual_field="actual_minutes",
        )
    ]
    for season, group in sorted(
        ((season, [row for row in rows if row["season"] == season])
         for season in {row["season"] for row in rows})
    ):
        outputs["minutes_metrics"].append(
            _metric_row(
                group, scope_type="season", scope_value=season,
                target="gameweek_minutes", predictor="v01_expected_minutes",
                prediction_field="gameweek_expected_minutes_for_evaluation",
                actual_field="actual_minutes",
            )
        )
    outputs["baseline_comparisons"] = [
        _metric_row(
            rows, scope_type="overall", scope_value="all",
            target="modeled_components", predictor="zero",
            prediction_field="zero_modeled_baseline", actual_field="actual_modeled_points",
        ),
        _metric_row(
            rows, scope_type="overall", scope_value="xfp_complete_population",
            target="modeled_components", predictor="zero_on_xfp_population",
            prediction_field="zero_on_xfp_population",
            actual_field="actual_modeled_points",
        ),
        _metric_row(
            rows, scope_type="overall", scope_value="all",
            target="full_historical_fpl_points", predictor="fpl_ep_next",
            prediction_field="ep_next", actual_field="actual_full_fpl_points",
        ),
    ]
    return rows, outputs


def _ranking_rows(rows: Sequence[dict[str, Any]]) -> list[tuple[Any, ...]]:
    by_gameweek: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_gameweek[(row["season"], row["target_gameweek"])].append(row)
    output: list[tuple[Any, ...]] = []
    summary: dict[tuple[str, int], list[tuple[float, float]]] = defaultdict(list)
    targets = (
        ("modeled_components", "actual_modeled_points"),
        ("full_historical_fpl_points", "actual_full_fpl_points"),
    )
    for (season, gameweek), group in sorted(by_gameweek.items()):
        for target, actual_field in targets:
            complete = [
                row for row in group
                if row["gameweek_xfp_v01"] is not None
                and row[actual_field] is not None
            ]
            predicted = sorted(
                complete, key=lambda row: (-row["gameweek_xfp_v01"], row["element_id"])
            )
            actual = sorted(
                complete, key=lambda row: (-row[actual_field], row["element_id"])
            )
            for top_n in TOP_N_VALUES:
                available = len(complete) >= top_n
                if available:
                    predicted_ids = {row["element_id"] for row in predicted[:top_n]}
                    actual_ids = {row["element_id"] for row in actual[:top_n]}
                    overlap = len(predicted_ids & actual_ids)
                    overlap_pct = 100.0 * overlap / top_n
                    summary[(target, top_n)].append((float(overlap), overlap_pct))
                else:
                    overlap = overlap_pct = None
                output.append(
                    (
                        "gameweek", season, gameweek, target, top_n, len(complete),
                        available, overlap, overlap_pct, None,
                        "score_desc_then_element_id_asc_strict_n",
                    )
                )
    for (target, top_n), values in sorted(summary.items()):
        output.append(
            (
                "summary", "ALL", None, target, top_n, None, bool(values),
                sum(value[0] for value in values) / len(values) if values else None,
                sum(value[1] for value in values) / len(values) if values else None,
                len(values), "score_desc_then_element_id_asc_strict_n",
            )
        )
    return output


def _calibration_rows(rows: Sequence[dict[str, Any]]) -> list[tuple[Any, ...]]:
    output: list[tuple[Any, ...]] = []
    complete = [
        row for row in rows
        if row["gameweek_xfp_v01"] is not None
        and row["actual_modeled_points"] is not None
    ]
    for label, lower, upper in CALIBRATION_BINS:
        members = [
            row for row in complete
            if row["gameweek_xfp_v01"] >= lower
            and (upper is None or row["gameweek_xfp_v01"] < upper)
        ]
        if members:
            mean_prediction = sum(row["gameweek_xfp_v01"] for row in members) / len(members)
            mean_actual = sum(row["actual_modeled_points"] for row in members) / len(members)
            bias = mean_prediction - mean_actual
        else:
            mean_prediction = mean_actual = bias = None
        output.append(
            (label, lower, upper, len(members), mean_prediction, mean_actual, bias)
        )
    return output


def _create_metric_tables(connection: duckdb.DuckDBPyConnection) -> int:
    rows, metrics = _metric_tables(connection)
    for table, table_rows in metrics.items():
        _create_rows_table(connection, table, METRIC_SCHEMA, table_rows)
    _create_rows_table(connection, "ranking_top_n", RANKING_SCHEMA, _ranking_rows(rows))
    _create_rows_table(
        connection, "calibration", CALIBRATION_SCHEMA, _calibration_rows(rows)
    )
    return len(rows)


def _write_backtest_outputs(
    connection: duckdb.DuckDBPyConnection,
    *,
    output_root: Path,
    manifest_base: dict[str, Any],
) -> tuple[Path, Path, list[dict[str, Any]]]:
    final_directory = output_root / BACKTEST_VERSION
    if final_directory.exists():
        raise HistoricalBacktestOutputExistsError(
            f"backtest output already exists and will not be overwritten: {final_directory}"
        )
    stage = output_root / f".{BACKTEST_VERSION}.{uuid.uuid4().hex}.tmp"
    stage.mkdir(parents=True, exist_ok=False)
    tables = (
        "fixture_predictions", "fixture_actuals", "player_gameweek",
        "overall_metrics", "season_metrics", "position_metrics",
        "gameweek_metrics", "expected_minutes_diagnostics",
        "prior_sample_diagnostics", "availability_diagnostics",
        "minutes_metrics", "component_metrics", "calibration",
        "ranking_top_n", "baseline_comparisons",
    )
    outputs: list[dict[str, Any]] = []
    try:
        for table in tables:
            path = stage / f"{table}.parquet"
            connection.execute(
                f'COPY "{table}" TO ? (FORMAT PARQUET, COMPRESSION ZSTD)', [str(path)]
            )
            outputs.append(
                {
                    "path": path.name,
                    "rows": connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0],
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
        manifest = dict(manifest_base)
        manifest["outputs"] = outputs
        manifest_path = stage / "backtest_manifest.json"
        _write_exclusive(
            manifest_path,
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        output_root.mkdir(parents=True, exist_ok=True)
        try:
            stage.rename(final_directory)
        except FileExistsError as exc:
            raise HistoricalBacktestOutputExistsError(
                f"backtest output already exists and will not be overwritten: {final_directory}"
            ) from exc
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return (
        final_directory,
        final_directory / "backtest_manifest.json",
        outputs,
    )


def build_historical_xfp_v01_backtest(
    *,
    historical_clean_root: Path = Path("data/historical/clean"),
    backtest_root: Path = Path("data/historical/backtests"),
    seasons: Sequence[str] = DEFAULT_SEASONS,
    target_gameweeks: Sequence[int] = DEFAULT_GAMEWEEKS,
    strict_scope: bool = True,
    clock=lambda: datetime.now(timezone.utc),
) -> HistoricalBacktestResult:
    """Measure frozen xFP v0.1 against immutable historical-v2 inputs."""
    seasons = tuple(seasons)
    target_gameweeks = tuple(target_gameweeks)
    if not seasons or not target_gameweeks:
        raise HistoricalBacktestError("backtest seasons and target gameweeks cannot be empty")
    output_directory = backtest_root / BACKTEST_VERSION
    if output_directory.exists():
        raise HistoricalBacktestOutputExistsError(
            f"backtest output already exists and will not be overwritten: {output_directory}"
        )
    manifest_path, paths = _input_paths(historical_clean_root, seasons)
    ingestion_manifest, input_hashes = _validate_input_manifest(
        manifest_path, paths, seasons
    )
    connection = duckdb.connect(":memory:")
    try:
        _load_input_tables(connection, paths, target_gameweeks)
        _validate_inputs(
            connection, seasons=seasons, gameweeks=target_gameweeks,
            strict_scope=strict_scope,
        )
        _create_scored_tables(connection)
        _validate_scored_tables(connection)
        observations = _create_metric_tables(connection)
        modeled_complete_pairs = connection.execute(
            """SELECT count(*) FROM player_gameweek
               WHERE gameweek_xfp_v01 IS NOT NULL
                 AND actual_modeled_points IS NOT NULL"""
        ).fetchone()[0]
        if any(_sha256(path) != digest for path, digest in input_hashes.items()):
            raise HistoricalBacktestError("historical-v2 input changed during backtest")
        input_entries = [
            {
                "path": str(path), "sha256": digest,
                "kind": "historical_ingestion_manifest" if path == manifest_path else "parquet",
            }
            for path, digest in sorted(input_hashes.items(), key=lambda item: str(item[0]))
        ]
        manifest_base = {
            "status": "complete",
            "backtest_version": BACKTEST_VERSION,
            "model_identifier": MODEL_IDENTIFIER,
            "live_model_version_reproduced": MODEL_VERSION,
            "model_frozen_no_tuning": True,
            "historical_classification": HISTORICAL_CLASSIFICATION,
            "perfect_historical_replay": False,
            "historical_input_version": EXPECTED_HISTORICAL_VERSION,
            "historical_ingestion_manifest_sha256": input_hashes[manifest_path],
            "historical_inputs": input_entries,
            "source_ingestion_commits": sorted(
                {
                    (entry["source_repository"], entry["source_commit"])
                    for entry in ingestion_manifest["source_files"]
                }
            ),
            "seasons": list(seasons),
            "target_gameweeks": list(target_gameweeks),
            "generation_timestamp": _iso_utc(clock()),
            "prediction_grain": "season x element_id x target_gameweek x target_fixture",
            "evaluation_grain": "season x element_id x target_gameweek",
            "temporal_cutoff_rule": HISTORY_CUTOFF_RULE,
            "coverage_policy": (
                "Metrics use only rows where predictor and corresponding actual are "
                "both non-null; missing values are never imputed as zero."
            ),
            "incomplete_attacking_rate_policy": (
                "Frozen v0.1 retains its numeric appearance-only total via component "
                "coalesce while prediction_complete=false; this state is reported separately."
            ),
            "actual_targets": {
                "primary": "appearance + frozen-position goals + FPL assists",
                "secondary": "archived total_points under historical season rules",
            },
            "metric_definitions": {
                "bias": "prediction - actual",
                "spearman": "Pearson correlation of tie-aware average ranks",
                "ranking": "per season/GW strict N; score descending then element_id ascending",
                "top_n": list(TOP_N_VALUES),
                "calibration_bins": [item[0] for item in CALIBRATION_BINS],
            },
            "expected_minutes_dgw_semantics": (
                "fixture expected minutes are capped at 90 exactly as v0.1; gameweek "
                "expected and actual minutes both sum all target fixtures"
            ),
            "fixture_assignment_context": FIXTURE_ASSIGNMENT_CONTEXT,
            "observations": observations,
            "modeled_complete_pairs": modeled_complete_pairs,
        }
        directory, output_manifest, _ = _write_backtest_outputs(
            connection, output_root=backtest_root, manifest_base=manifest_base
        )
    except duckdb.Error as exc:
        raise HistoricalBacktestError(f"historical backtest failed: {exc}") from exc
    finally:
        connection.close()
    return HistoricalBacktestResult(
        directory=directory,
        manifest_path=output_manifest,
        player_gameweek_path=directory / "player_gameweek.parquet",
        observations=observations,
        modeled_complete_pairs=modeled_complete_pairs,
    )
