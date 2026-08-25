"""Leakage-safe, read-only evaluation for frozen xFP predictions."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from .transform import CleanOutputExistsError, DataQualityError, TransformationError


SUPPORTED_MODEL_VERSION = "v0.1"


class EvaluationError(TransformationError):
    """Raised when an evaluation cannot be built safely."""


class GameweekNotFinalizedError(EvaluationError):
    """Raised when official realized data is not final enough to evaluate."""


@dataclass(frozen=True)
class EvaluationOutputs:
    """Paths and core row counts from one immutable evaluation run."""

    directory: Path
    player_path: Path
    metrics_path: Path
    position_metrics_path: Path
    diagnostic_metrics_path: Path
    ranking_path: Path
    manifest_path: Path
    player_rows: int
    evaluated_players: int


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
        raise EvaluationError(f"could not read {path}: {exc}") from exc


def _parse_snapshot_timestamp(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise EvaluationError(f"invalid snapshot timestamp: {value}") from exc


def _parse_api_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except (AttributeError, ValueError) as exc:
        raise EvaluationError(f"invalid API timestamp: {value!r}") from exc


def _event(bootstrap_path: Path, target_gameweek: int) -> dict[str, Any]:
    bootstrap = _load_json(bootstrap_path)
    if not isinstance(bootstrap, dict) or not isinstance(bootstrap.get("events"), list):
        raise EvaluationError(f"bootstrap has no events list: {bootstrap_path}")
    event = next(
        (row for row in bootstrap["events"] if row.get("id") == target_gameweek),
        None,
    )
    if event is None:
        raise EvaluationError(
            f"gameweek {target_gameweek} is absent from {bootstrap_path}"
        )
    return event


def _require_files(paths: list[Path]) -> None:
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise EvaluationError(
            "missing evaluation input(s): "
            + ", ".join(path.as_posix() for path in missing)
        )


def _validate_finalization(
    connection: duckdb.DuckDBPyConnection,
    *,
    realized_bootstrap_path: Path,
    target_gameweek: int,
    realized_snapshot_timestamp: str,
) -> tuple[datetime, str]:
    event = _event(realized_bootstrap_path, target_gameweek)
    if event.get("finished") is not True or event.get("data_checked") is not True:
        raise GameweekNotFinalizedError(
            f"gameweek {target_gameweek} is not finalized: official event requires "
            f"finished=true and data_checked=true (found finished="
            f"{event.get('finished')!r}, data_checked={event.get('data_checked')!r})"
        )
    deadline = _parse_api_timestamp(event.get("deadline_time"))
    realized_snapshot_time = _parse_snapshot_timestamp(realized_snapshot_timestamp)
    if realized_snapshot_time <= deadline:
        raise GameweekNotFinalizedError(
            "realized snapshot is not after the target-gameweek deadline"
        )

    fixture_state = connection.execute(
        """SELECT count(*) AS fixtures,
                  count(*) FILTER (WHERE finished IS TRUE) AS finished,
                  count(*) FILTER (WHERE gameweek_finished IS TRUE) AS gw_finished,
                  count(*) FILTER (WHERE gameweek_data_checked IS TRUE) AS checked,
                  min(retrieved_at) > ?::TIMESTAMPTZ AS collected_after_deadline
           FROM realized_fixtures WHERE gameweek_id = ?""",
        [deadline.isoformat(), target_gameweek],
    ).fetchone()
    if fixture_state[0] == 0:
        raise GameweekNotFinalizedError(
            f"no official fixtures were collected for gameweek {target_gameweek}"
        )
    if fixture_state[1:4] != (fixture_state[0],) * 3:
        raise GameweekNotFinalizedError(
            f"gameweek {target_gameweek} fixtures are not all finished and data-checked"
        )
    if fixture_state[4] is not True:
        raise GameweekNotFinalizedError(
            "fixture results were not collected after the target deadline"
        )

    history_state = connection.execute(
        """SELECT count(*),
                  count(*) FILTER (WHERE gameweek_finished IS TRUE),
                  count(*) FILTER (WHERE gameweek_data_checked IS TRUE),
                  min(retrieved_at) > ?::TIMESTAMPTZ AS collected_after_deadline
           FROM realized_history WHERE gameweek_id = ?""",
        [deadline.isoformat(), target_gameweek],
    ).fetchone()
    if history_state[0] == 0:
        raise GameweekNotFinalizedError(
            f"no realized player history was collected for gameweek {target_gameweek}"
        )
    if history_state[1:3] != (history_state[0],) * 2:
        raise GameweekNotFinalizedError(
            f"gameweek {target_gameweek} player history is not fully finalized"
        )
    if history_state[3] is not True:
        raise GameweekNotFinalizedError(
            "player history was not collected after the target deadline"
        )
    return deadline, event.get("name", f"Gameweek {target_gameweek}")


def _validate_prediction(
    connection: duckdb.DuckDBPyConnection,
    *,
    season: str,
    target_gameweek: int,
    model_version: str,
    prediction_snapshot_timestamp: str,
    deadline: datetime,
) -> tuple[str, str, str]:
    identity = connection.execute(
        """SELECT count(*), count(DISTINCT season), min(season),
                  count(DISTINCT target_gameweek), min(target_gameweek),
                  count(DISTINCT model_version), min(model_version),
                  count(DISTINCT snapshot_timestamp), min(snapshot_timestamp),
                  count(DISTINCT feature_input_sha256), min(feature_input_sha256),
                  count(DISTINCT players_input_sha256), min(players_input_sha256),
                  count(DISTINCT bootstrap_sha256), min(bootstrap_sha256)
           FROM prediction_input"""
    ).fetchone()
    expected = (
        identity[0] > 0
        and identity[1:9]
        == (
            1,
            season,
            1,
            target_gameweek,
            1,
            model_version,
            1,
            prediction_snapshot_timestamp,
        )
        and identity[9] == identity[11] == identity[13] == 1
    )
    if not expected:
        raise EvaluationError(
            "frozen prediction provenance does not match the requested evaluation"
        )
    if _parse_snapshot_timestamp(prediction_snapshot_timestamp) >= deadline:
        raise EvaluationError("prediction snapshot is not strictly pre-deadline")
    return identity[10], identity[12], identity[14]


def _validate_fixture_prediction_grain(
    connection: duckdb.DuckDBPyConnection,
    *,
    season: str,
    target_gameweek: int,
    model_version: str,
    prediction_snapshot_timestamp: str,
    expected_feature_hash: str,
    expected_players_hash: str,
    expected_bootstrap_hash: str,
) -> None:
    identity = connection.execute(
        """SELECT count(*), count(DISTINCT season), min(season),
                  count(DISTINCT target_gameweek), min(target_gameweek),
                  count(DISTINCT model_version), min(model_version),
                  count(DISTINCT snapshot_timestamp), min(snapshot_timestamp),
                  count(DISTINCT feature_input_sha256), min(feature_input_sha256),
                  count(DISTINCT players_input_sha256), min(players_input_sha256),
                  count(DISTINCT bootstrap_sha256), min(bootstrap_sha256)
           FROM fixture_prediction_input"""
    ).fetchone()
    if not (
        identity[0] > 0
        and identity[1:9]
        == (
            1,
            season,
            1,
            target_gameweek,
            1,
            model_version,
            1,
            prediction_snapshot_timestamp,
        )
        and identity[9:15]
        == (
            1,
            expected_feature_hash,
            1,
            expected_players_hash,
            1,
            expected_bootstrap_hash,
        )
    ):
        raise EvaluationError(
            "frozen fixture prediction provenance does not match the gameweek prediction"
        )

    connection.execute(
        """CREATE TEMP TABLE fixture_prediction_summary AS
           SELECT fpl_player_id,
                  min(position_id) AS position_id,
                  min(position) AS position,
                  count(*) AS physical_rows,
                  count(*) FILTER (WHERE target_has_fixture) AS fixture_count,
                  count(*) FILTER (WHERE NOT target_has_fixture) AS blank_rows,
                  count(fixture_xfp_v01) FILTER (WHERE target_has_fixture)
                    AS scored_fixture_count,
                  sum(fixture_xfp_v01) FILTER (WHERE target_has_fixture)
                    AS summed_fixture_xfp_v01,
                  min(target_fixture_count) AS minimum_declared_fixture_count,
                  max(target_fixture_count) AS maximum_declared_fixture_count,
                  count(fixture_id) AS nonnull_fixture_ids
           FROM fixture_prediction_input
           GROUP BY fpl_player_id"""
    )
    invalid_keys = connection.execute(
        """SELECT count(*) FROM (
               SELECT fpl_player_id, coalesce(fixture_id, -1), count(*) AS n
               FROM fixture_prediction_input
               GROUP BY fpl_player_id, coalesce(fixture_id, -1)
               HAVING n > 1
           )"""
    ).fetchone()[0]
    if invalid_keys:
        raise DataQualityError("frozen fixture prediction keys are not unique")

    inconsistent = connection.execute(
        """SELECT count(*)
           FROM prediction_input p
           FULL OUTER JOIN fixture_prediction_summary f USING (fpl_player_id)
           WHERE p.fpl_player_id IS NULL OR f.fpl_player_id IS NULL
              OR p.position_id IS DISTINCT FROM f.position_id
              OR p.position IS DISTINCT FROM f.position
              OR p.fixture_count IS DISTINCT FROM f.fixture_count
              OR f.minimum_declared_fixture_count IS DISTINCT FROM p.fixture_count
              OR f.maximum_declared_fixture_count IS DISTINCT FROM p.fixture_count
              OR (p.fixture_count = 0 AND (
                     f.physical_rows <> 1 OR f.blank_rows <> 1
                     OR f.nonnull_fixture_ids <> 0
                     OR p.gameweek_xfp_v01 IS DISTINCT FROM 0))
              OR (p.fixture_count > 0 AND (
                     f.physical_rows <> p.fixture_count OR f.blank_rows <> 0
                     OR f.nonnull_fixture_ids <> p.fixture_count
                     OR (p.gameweek_xfp_v01 IS NULL
                         AND f.scored_fixture_count = p.fixture_count)
                     OR (p.gameweek_xfp_v01 IS NOT NULL AND (
                           f.scored_fixture_count <> p.fixture_count
                           OR abs(p.gameweek_xfp_v01
                                  - f.summed_fixture_xfp_v01) > 1e-10))))"""
    ).fetchone()[0]
    if inconsistent:
        raise DataQualityError(
            "frozen fixture predictions do not aggregate consistently to the "
            "gameweek prediction, or blank evidence is missing/corrupt"
        )

    connection.execute(
        """CREATE TEMP TABLE verified_blank_players AS
           SELECT p.fpl_player_id
           FROM prediction_input p
           JOIN fixture_prediction_summary f USING (fpl_player_id)
           WHERE p.fixture_count = 0 AND f.physical_rows = 1
             AND f.blank_rows = 1 AND f.nonnull_fixture_ids = 0"""
    )


def _load_baselines(
    connection: duckdb.DuckDBPyConnection,
    *,
    predeadline_bootstrap_path: Path | None,
    predeadline_players_path: Path | None,
    feature_path: Path | None,
    target_gameweek: int,
    prediction_snapshot_timestamp: str,
    deadline: datetime,
    expected_feature_hash: str,
    expected_players_hash: str,
    expected_bootstrap_hash: str,
) -> dict[str, Any]:
    connection.execute(
        """CREATE TABLE baselines (
               fpl_player_id BIGINT,
               fpl_ep_next DOUBLE,
               previous_gameweek_points DOUBLE,
               average_prior_points DOUBLE,
               ep_next_available BOOLEAN,
               historical_points_baselines_available BOOLEAN
           )"""
    )
    metadata: dict[str, Any] = {
        "fpl_ep_next": {"available": False, "reason": "safe source unavailable"},
        "previous_gameweek_points": {
            "available": False,
            "reason": "safe source unavailable",
        },
        "average_prior_points": {
            "available": False,
            "reason": "safe source unavailable",
        },
    }

    ep_safe = False
    if predeadline_bootstrap_path and predeadline_players_path:
        if predeadline_bootstrap_path.is_file() and predeadline_players_path.is_file():
            event = _event(predeadline_bootstrap_path, target_gameweek)
            ep_safe = (
                _parse_snapshot_timestamp(prediction_snapshot_timestamp) < deadline
                and event.get("is_next") is True
                and _sha256(predeadline_bootstrap_path) == expected_bootstrap_hash
                and _sha256(predeadline_players_path) == expected_players_hash
            )
            if ep_safe:
                connection.execute(
                    "CREATE TEMP TABLE safe_players AS SELECT * FROM read_parquet(?)",
                    [str(predeadline_players_path)],
                )
                ep_safe = connection.execute(
                    """SELECT count(*) > 0
                              AND count(*) FILTER (
                                  WHERE snapshot_timestamp <> ?) = 0
                       FROM safe_players""",
                    [prediction_snapshot_timestamp],
                ).fetchone()[0]
    if ep_safe:
        metadata["fpl_ep_next"] = {
            "available": True,
            "source": str(predeadline_players_path),
            "sha256": expected_players_hash,
            "meaning": "official FPL pre-deadline expected points for is_next",
        }

    feature_safe = False
    if feature_path and feature_path.is_file():
        feature_safe = _sha256(feature_path) == expected_feature_hash
        if feature_safe:
            connection.execute(
                "CREATE TEMP TABLE safe_features AS SELECT * FROM read_parquet(?)",
                [str(feature_path)],
            )
            feature_safe = connection.execute(
                """SELECT count(*) > 0
                          AND count(*) FILTER (
                              WHERE snapshot_timestamp <> ?
                                 OR target_gameweek <> ?
                                 OR history_gameweek_max_used >= ?) = 0
                   FROM safe_features""",
                [
                    prediction_snapshot_timestamp,
                    target_gameweek,
                    target_gameweek,
                ],
            ).fetchone()[0]
    if feature_safe:
        source = {"source": str(feature_path), "sha256": expected_feature_hash}
        metadata["previous_gameweek_points"] = {"available": True, **source}
        metadata["average_prior_points"] = {"available": True, **source}

    if ep_safe and feature_safe:
        connection.execute(
            """INSERT INTO baselines
               SELECT p.fpl_player_id,
                      p.ep_next,
                      min(f.previous_gw_points),
                      min(f.average_prior_points),
                      p.ep_next IS NOT NULL,
                      min(f.previous_gw_points) IS NOT NULL
                        OR min(f.average_prior_points) IS NOT NULL
               FROM safe_players p
               LEFT JOIN safe_features f USING (fpl_player_id)
               GROUP BY p.fpl_player_id, p.ep_next"""
        )
    elif ep_safe:
        connection.execute(
            """INSERT INTO baselines
               SELECT fpl_player_id, ep_next, NULL, NULL,
                      ep_next IS NOT NULL, false FROM safe_players"""
        )
    elif feature_safe:
        connection.execute(
            """INSERT INTO baselines
               SELECT fpl_player_id, NULL,
                      min(previous_gw_points), min(average_prior_points),
                      false,
                      min(previous_gw_points) IS NOT NULL
                        OR min(average_prior_points) IS NOT NULL
               FROM safe_features GROUP BY fpl_player_id"""
        )
    return metadata


def _create_player_evaluation(
    connection: duckdb.DuckDBPyConnection,
    *,
    season: str,
    target_gameweek: int,
    model_version: str,
    prediction_snapshot_timestamp: str,
    realized_snapshot_timestamp: str,
    prediction_path: Path,
    prediction_hash: str,
    fixture_prediction_path: Path,
    fixture_prediction_hash: str,
    history_path: Path,
    history_hash: str,
    evaluation_generated_at: datetime,
) -> None:
    duplicates = connection.execute(
        """SELECT count(*) FROM (
               SELECT fpl_player_id, fixture_id, count(*) AS n
               FROM realized_history WHERE gameweek_id = ?
               GROUP BY fpl_player_id, fixture_id HAVING n > 1
           )""",
        [target_gameweek],
    ).fetchone()[0]
    if duplicates:
        raise DataQualityError("realized history has duplicate player/fixture rows")

    connection.execute(
        """CREATE TEMP TABLE actual_by_player AS
           SELECT fpl_player_id,
                  min(web_name) AS web_name,
                  min(position_id) AS realized_position_id,
                  min(position) AS realized_position,
                  count(*) AS realized_fixture_count,
                  sum(minutes) AS actual_minutes,
                  sum(CASE WHEN minutes = 0 THEN 0
                           WHEN minutes < 60 THEN 1 ELSE 2 END)
                      AS actual_appearance_points_v01,
                  sum(goals_scored) AS actual_goals,
                  sum(assists) AS actual_assists,
                  sum(assists * 3) AS actual_assist_points_v01,
                  sum(total_points) AS actual_total_fpl_points
           FROM realized_history
           WHERE gameweek_id = ?
           GROUP BY fpl_player_id""",
        [target_gameweek],
    )
    invalid_positions = connection.execute(
        """SELECT count(*) FROM prediction_input
           WHERE position NOT IN ('Goalkeeper', 'Defender', 'Midfielder', 'Forward')"""
    ).fetchone()[0]
    if invalid_positions:
        raise DataQualityError("frozen prediction contains an unknown FPL position")

    connection.execute(
        """CREATE TABLE player_evaluation AS
           WITH joined AS (
               SELECT coalesce(p.fpl_player_id, a.fpl_player_id) AS fpl_player_id,
                      coalesce(p.web_name, a.web_name) AS web_name,
                      coalesce(p.position_id, a.realized_position_id) AS position_id,
                      coalesce(p.position, a.realized_position) AS position,
                      p.position_id AS frozen_position_id,
                      p.position AS frozen_position,
                      a.realized_position_id,
                      a.realized_position,
                      p.team_id,
                      p.team_name,
                      p.fixture_count AS predicted_fixture_count,
                      a.realized_fixture_count,
                      p.gameweek_xfp_v01,
                      p.gameweek_expected_minutes_v01 AS expected_minutes_v01,
                      p.low_sample,
                      p.attacking_rate_available,
                      CASE WHEN a.fpl_player_id IS NOT NULL THEN a.actual_minutes
                           WHEN vb.fpl_player_id IS NOT NULL THEN 0 END AS actual_minutes,
                      CASE WHEN a.fpl_player_id IS NOT NULL
                             THEN a.actual_appearance_points_v01
                           WHEN vb.fpl_player_id IS NOT NULL THEN 0 END
                           AS actual_appearance_points_v01,
                      CASE WHEN a.fpl_player_id IS NOT NULL THEN a.actual_goals
                           WHEN vb.fpl_player_id IS NOT NULL THEN 0 END AS actual_goals,
                      CASE WHEN a.fpl_player_id IS NOT NULL AND p.position IS NOT NULL
                             THEN a.actual_goals * CASE p.position
                               WHEN 'Goalkeeper' THEN 10
                               WHEN 'Defender' THEN 6
                               WHEN 'Midfielder' THEN 5
                               WHEN 'Forward' THEN 4 END
                           WHEN vb.fpl_player_id IS NOT NULL THEN 0 END
                           AS actual_goal_points_v01,
                      CASE WHEN a.fpl_player_id IS NOT NULL THEN a.actual_assists
                           WHEN vb.fpl_player_id IS NOT NULL THEN 0 END AS actual_assists,
                      CASE WHEN a.fpl_player_id IS NOT NULL
                             THEN a.actual_assist_points_v01
                           WHEN vb.fpl_player_id IS NOT NULL THEN 0 END
                           AS actual_assist_points_v01,
                      CASE WHEN a.fpl_player_id IS NOT NULL
                             THEN a.actual_appearance_points_v01
                                  + a.actual_goals * CASE p.position
                                      WHEN 'Goalkeeper' THEN 10
                                      WHEN 'Defender' THEN 6
                                      WHEN 'Midfielder' THEN 5
                                      WHEN 'Forward' THEN 4 END
                                  + a.actual_assist_points_v01
                           WHEN vb.fpl_player_id IS NOT NULL THEN 0 END
                           AS actual_modeled_points_v01,
                      CASE WHEN a.fpl_player_id IS NOT NULL
                             THEN a.actual_total_fpl_points
                           WHEN vb.fpl_player_id IS NOT NULL THEN 0 END
                           AS actual_total_fpl_points,
                      b.fpl_ep_next,
                      coalesce(b.ep_next_available, false) AS ep_next_available,
                      b.previous_gameweek_points,
                      b.average_prior_points,
                      coalesce(b.historical_points_baselines_available, false)
                        AS historical_points_baselines_available
               FROM prediction_input p
               FULL OUTER JOIN actual_by_player a USING (fpl_player_id)
               LEFT JOIN verified_blank_players vb
                 ON coalesce(p.fpl_player_id, a.fpl_player_id) = vb.fpl_player_id
               LEFT JOIN baselines b
                 ON coalesce(p.fpl_player_id, a.fpl_player_id) = b.fpl_player_id
           )
           SELECT ?::VARCHAR AS season,
                  ?::INTEGER AS target_gameweek,
                  ?::VARCHAR AS model_version,
                  ?::VARCHAR AS prediction_snapshot_timestamp,
                  ?::VARCHAR AS realized_snapshot_timestamp,
                  ?::VARCHAR AS prediction_source_path,
                  ?::VARCHAR AS prediction_sha256,
                  ?::VARCHAR AS fixture_prediction_source_path,
                  ?::VARCHAR AS fixture_prediction_sha256,
                  ?::VARCHAR AS realized_history_source_path,
                  ?::VARCHAR AS realized_history_sha256,
                  ?::TIMESTAMPTZ AS evaluation_generated_at,
                  joined.*,
                  gameweek_xfp_v01 - actual_modeled_points_v01
                    AS modeled_points_error,
                  abs(gameweek_xfp_v01 - actual_modeled_points_v01)
                    AS modeled_points_absolute_error,
                  pow(gameweek_xfp_v01 - actual_modeled_points_v01, 2)
                    AS modeled_points_squared_error,
                  gameweek_xfp_v01 - actual_total_fpl_points
                    AS total_points_error,
                  abs(gameweek_xfp_v01 - actual_total_fpl_points)
                    AS total_points_absolute_error,
                  pow(gameweek_xfp_v01 - actual_total_fpl_points, 2)
                    AS total_points_squared_error
           FROM joined""",
        [
            season,
            target_gameweek,
            model_version,
            prediction_snapshot_timestamp,
            realized_snapshot_timestamp,
            str(prediction_path),
            prediction_hash,
            str(fixture_prediction_path),
            fixture_prediction_hash,
            str(history_path),
            history_hash,
            evaluation_generated_at,
        ],
    )


def _create_metric_tables(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """CREATE TEMP TABLE metric_inputs AS
           SELECT fpl_player_id, position, 'modeled_points'::VARCHAR AS target_name,
                  'xfp_v01'::VARCHAR AS predictor,
                  gameweek_xfp_v01 AS prediction,
                  actual_modeled_points_v01 AS actual
           FROM player_evaluation
           UNION ALL
           SELECT fpl_player_id, position, 'full_fpl_points', 'xfp_v01',
                  gameweek_xfp_v01, actual_total_fpl_points
           FROM player_evaluation
           UNION ALL
           SELECT fpl_player_id, position, 'full_fpl_points', 'fpl_ep_next',
                  CASE WHEN ep_next_available THEN fpl_ep_next END,
                  actual_total_fpl_points
           FROM player_evaluation
           UNION ALL
           SELECT fpl_player_id, position, 'full_fpl_points',
                  'previous_gameweek_points', previous_gameweek_points,
                  actual_total_fpl_points
           FROM player_evaluation
           UNION ALL
           SELECT fpl_player_id, position, 'full_fpl_points',
                  'average_prior_points', average_prior_points,
                  actual_total_fpl_points
           FROM player_evaluation"""
    )
    metric_columns = """count(*) AS total_player_rows,
           count(prediction) AS prediction_available_count,
           count(actual) AS actual_available_count,
           count(*) FILTER (WHERE prediction IS NOT NULL AND actual IS NOT NULL)
             AS evaluated_players,
           count(*) FILTER (WHERE prediction IS NULL) AS missing_prediction_count,
           count(*) FILTER (WHERE actual IS NULL) AS missing_actual_count,
           100.0 * count(prediction) / nullif(count(*), 0)
             AS prediction_coverage_pct,
           100.0 * count(*) FILTER (
               WHERE prediction IS NOT NULL AND actual IS NOT NULL)
             / nullif(count(actual), 0) AS evaluation_coverage_pct,
           avg(abs(prediction - actual)) FILTER (
               WHERE prediction IS NOT NULL AND actual IS NOT NULL) AS mae,
           sqrt(avg(pow(prediction - actual, 2)) FILTER (
               WHERE prediction IS NOT NULL AND actual IS NOT NULL)) AS rmse,
           avg(prediction - actual) FILTER (
               WHERE prediction IS NOT NULL AND actual IS NOT NULL) AS bias"""
    connection.execute(
        f"""CREATE TABLE metrics AS
            SELECT target_name, predictor, {metric_columns}
            FROM metric_inputs GROUP BY target_name, predictor"""
    )
    connection.execute(
        f"""CREATE TABLE position_metrics AS
            SELECT target_name, predictor, position, {metric_columns}
            FROM metric_inputs
            WHERE position IS NOT NULL
            GROUP BY target_name, predictor, position"""
    )
    connection.execute(
        """CREATE TEMP TABLE diagnostic_groups AS
           SELECT fpl_player_id, 'actual_minutes'::VARCHAR AS group_type,
                  CASE WHEN actual_minutes = 0 THEN '0 minutes'
                       WHEN actual_minutes < 60 THEN '1-59 minutes'
                       ELSE '60+ minutes' END AS group_value
           FROM player_evaluation WHERE actual_minutes IS NOT NULL
           UNION ALL
           SELECT fpl_player_id, 'low_sample',
                  CASE WHEN low_sample THEN 'true' ELSE 'false' END
           FROM player_evaluation WHERE low_sample IS NOT NULL
           UNION ALL
           SELECT fpl_player_id, 'attacking_rate_available',
                  CASE WHEN attacking_rate_available THEN 'true' ELSE 'false' END
           FROM player_evaluation WHERE attacking_rate_available IS NOT NULL"""
    )
    connection.execute(
        f"""CREATE TABLE diagnostic_metrics AS
            SELECT m.target_name, m.predictor, d.group_type, d.group_value,
                   {metric_columns}
            FROM metric_inputs m
            JOIN diagnostic_groups d USING (fpl_player_id)
            WHERE m.predictor = 'xfp_v01'
            GROUP BY m.target_name, m.predictor, d.group_type, d.group_value"""
    )


def _create_ranking_summary(
    connection: duckdb.DuckDBPyConnection, *, top_n: int
) -> None:
    connection.execute(
        """CREATE TABLE ranking_summary (
               target_name VARCHAR,
               predictor VARCHAR,
               evaluated_players BIGINT,
               spearman_rank_correlation DOUBLE,
               top_n INTEGER,
               predicted_top_n_player_ids BIGINT[],
               actual_top_n_player_ids BIGINT[],
               overlap_count BIGINT,
               overlap_pct DOUBLE
           )"""
    )
    combinations = connection.execute(
        "SELECT DISTINCT target_name, predictor FROM metric_inputs"
    ).fetchall()
    for target_name, predictor in combinations:
        connection.execute(
            """INSERT INTO ranking_summary
               WITH pairs AS (
                   SELECT fpl_player_id, prediction, actual
                   FROM metric_inputs
                   WHERE target_name = ? AND predictor = ?
                     AND prediction IS NOT NULL AND actual IS NOT NULL
               ), ranked_base AS (
                   SELECT *,
                          rank() OVER (ORDER BY prediction DESC) AS prediction_rank,
                          count(*) OVER (PARTITION BY prediction) AS prediction_ties,
                          rank() OVER (ORDER BY actual DESC) AS actual_rank,
                          count(*) OVER (PARTITION BY actual) AS actual_ties,
                          row_number() OVER (
                              ORDER BY prediction DESC, fpl_player_id) AS prediction_n,
                          row_number() OVER (
                              ORDER BY actual DESC, fpl_player_id) AS actual_n
                   FROM pairs
               ), ranked AS (
                   SELECT *,
                          prediction_rank + (prediction_ties - 1) / 2.0
                            AS prediction_average_rank,
                          actual_rank + (actual_ties - 1) / 2.0
                            AS actual_average_rank
                   FROM ranked_base
               )
               SELECT ?, ?, count(*),
                      corr(prediction_average_rank, actual_average_rank),
                      ?,
                      list(fpl_player_id ORDER BY prediction DESC, fpl_player_id)
                        FILTER (WHERE prediction_n <= ?),
                      list(fpl_player_id ORDER BY actual DESC, fpl_player_id)
                        FILTER (WHERE actual_n <= ?),
                      count(*) FILTER (
                          WHERE prediction_n <= ? AND actual_n <= ?),
                      100.0 * count(*) FILTER (
                          WHERE prediction_n <= ? AND actual_n <= ?)
                        / nullif(least(?, count(*)), 0)
               FROM ranked""",
            [
                target_name,
                predictor,
                target_name,
                predictor,
                top_n,
                top_n,
                top_n,
                top_n,
                top_n,
                top_n,
                top_n,
                top_n,
            ],
        )


def _validate_evaluation(connection: duckdb.DuckDBPyConnection) -> None:
    checks = (
        (
            "player evaluation IDs are not unique",
            "SELECT count(*) = count(DISTINCT fpl_player_id) FROM player_evaluation",
        ),
        (
            "actual modeled components do not add up",
            """SELECT count(*) = 0 FROM player_evaluation
               WHERE actual_modeled_points_v01 <>
                     actual_appearance_points_v01 + actual_goal_points_v01
                     + actual_assist_points_v01""",
        ),
        (
            "bias error sign is inconsistent",
            """SELECT count(*) = 0 FROM player_evaluation
               WHERE modeled_points_error <>
                       gameweek_xfp_v01 - actual_modeled_points_v01
                  OR total_points_error <>
                       gameweek_xfp_v01 - actual_total_fpl_points""",
        ),
        (
            "metric counts are inconsistent",
            """SELECT count(*) = 0 FROM metrics
               WHERE evaluated_players > prediction_available_count
                  OR evaluated_players > actual_available_count
                  OR missing_prediction_count + prediction_available_count
                     <> total_player_rows
                  OR missing_actual_count + actual_available_count
                     <> total_player_rows""",
        ),
    )
    for message, query in checks:
        if not connection.execute(query).fetchone()[0]:
            raise DataQualityError(message)


def _write_outputs(
    connection: duckdb.DuckDBPyConnection,
    *,
    output_directory: Path,
    manifest: dict[str, Any],
) -> dict[str, Path]:
    if output_directory.exists():
        raise CleanOutputExistsError(
            f"evaluation output exists and will not be overwritten: {output_directory}"
        )
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary_directory = output_directory.parent / f".{uuid.uuid4().hex}.tmp"
    temporary_directory.mkdir()
    outputs = {
        "player": "player_evaluation.parquet",
        "metrics": "metrics.parquet",
        "position": "position_metrics.parquet",
        "diagnostic": "diagnostic_metrics.parquet",
        "ranking": "ranking_summary.parquet",
        "manifest": "manifest.json",
    }
    tables = {
        "player": "player_evaluation",
        "metrics": "metrics",
        "position": "position_metrics",
        "diagnostic": "diagnostic_metrics",
        "ranking": "ranking_summary",
    }
    created = False
    try:
        for key, table in tables.items():
            connection.execute(
                f"COPY {table} TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                [str(temporary_directory / outputs[key])],
            )
        (temporary_directory / outputs["manifest"]).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        try:
            output_directory.mkdir()
            created = True
        except FileExistsError as exc:
            raise CleanOutputExistsError(
                "evaluation output exists and will not be overwritten: "
                f"{output_directory}"
            ) from exc
        for filename in outputs.values():
            os.link(temporary_directory / filename, output_directory / filename)
    except Exception:
        if created:
            for filename in outputs.values():
                (output_directory / filename).unlink(missing_ok=True)
            output_directory.rmdir()
        raise
    finally:
        for filename in outputs.values():
            (temporary_directory / filename).unlink(missing_ok=True)
        temporary_directory.rmdir()
    return {key: output_directory / value for key, value in outputs.items()}


def evaluate_xfp_from_paths(
    *,
    prediction_path: Path,
    prediction_fixture_path: Path,
    realized_bootstrap_path: Path,
    realized_fixtures_path: Path,
    realized_history_path: Path,
    evaluation_data_root: Path,
    season: str,
    target_gameweek: int,
    model_version: str,
    prediction_snapshot_timestamp: str,
    realized_snapshot_timestamp: str,
    predeadline_bootstrap_path: Path | None = None,
    predeadline_players_path: Path | None = None,
    feature_path: Path | None = None,
    top_n: int = 10,
    evaluation_time: datetime | None = None,
) -> EvaluationOutputs:
    """Evaluate one frozen prediction against one finalized realized snapshot."""
    if model_version != SUPPORTED_MODEL_VERSION:
        raise EvaluationError(f"unsupported model version: {model_version}")
    if target_gameweek < 1 or top_n < 1:
        raise EvaluationError("target_gameweek and top_n must be positive")
    _require_files(
        [
            prediction_path,
            prediction_fixture_path,
            realized_bootstrap_path,
            realized_fixtures_path,
            realized_history_path,
        ]
    )
    generated_at = (evaluation_time or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    evaluation_timestamp = generated_at.strftime("%Y%m%dT%H%M%S.%fZ")
    output_directory = (
        evaluation_data_root
        / season
        / f"gameweek={target_gameweek}"
        / model_version
        / evaluation_timestamp
    )
    if output_directory.exists():
        raise CleanOutputExistsError(
            f"evaluation output exists and will not be overwritten: {output_directory}"
        )

    paths_to_hash = [
        prediction_path,
        prediction_fixture_path,
        realized_bootstrap_path,
        realized_fixtures_path,
        realized_history_path,
    ]
    for optional in (
        predeadline_bootstrap_path,
        predeadline_players_path,
        feature_path,
    ):
        if optional and optional.is_file():
            paths_to_hash.append(optional)
    initial_hashes = {path: _sha256(path) for path in paths_to_hash}

    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE prediction_input AS SELECT * FROM read_parquet(?)",
            [str(prediction_path)],
        )
        connection.execute(
            "CREATE TABLE fixture_prediction_input AS SELECT * FROM read_parquet(?)",
            [str(prediction_fixture_path)],
        )
        connection.execute(
            "CREATE TABLE realized_fixtures AS SELECT * FROM read_parquet(?)",
            [str(realized_fixtures_path)],
        )
        connection.execute(
            "CREATE TABLE realized_history AS SELECT * FROM read_parquet(?)",
            [str(realized_history_path)],
        )
        deadline, event_name = _validate_finalization(
            connection,
            realized_bootstrap_path=realized_bootstrap_path,
            target_gameweek=target_gameweek,
            realized_snapshot_timestamp=realized_snapshot_timestamp,
        )
        expected_feature_hash, expected_players_hash, expected_bootstrap_hash = (
            _validate_prediction(
                connection,
                season=season,
                target_gameweek=target_gameweek,
                model_version=model_version,
                prediction_snapshot_timestamp=prediction_snapshot_timestamp,
                deadline=deadline,
            )
        )
        _validate_fixture_prediction_grain(
            connection,
            season=season,
            target_gameweek=target_gameweek,
            model_version=model_version,
            prediction_snapshot_timestamp=prediction_snapshot_timestamp,
            expected_feature_hash=expected_feature_hash,
            expected_players_hash=expected_players_hash,
            expected_bootstrap_hash=expected_bootstrap_hash,
        )
        baseline_metadata = _load_baselines(
            connection,
            predeadline_bootstrap_path=predeadline_bootstrap_path,
            predeadline_players_path=predeadline_players_path,
            feature_path=feature_path,
            target_gameweek=target_gameweek,
            prediction_snapshot_timestamp=prediction_snapshot_timestamp,
            deadline=deadline,
            expected_feature_hash=expected_feature_hash,
            expected_players_hash=expected_players_hash,
            expected_bootstrap_hash=expected_bootstrap_hash,
        )
        _create_player_evaluation(
            connection,
            season=season,
            target_gameweek=target_gameweek,
            model_version=model_version,
            prediction_snapshot_timestamp=prediction_snapshot_timestamp,
            realized_snapshot_timestamp=realized_snapshot_timestamp,
            prediction_path=prediction_path,
            prediction_hash=initial_hashes[prediction_path],
            fixture_prediction_path=prediction_fixture_path,
            fixture_prediction_hash=initial_hashes[prediction_fixture_path],
            history_path=realized_history_path,
            history_hash=initial_hashes[realized_history_path],
            evaluation_generated_at=generated_at,
        )
        _create_metric_tables(connection)
        _create_ranking_summary(connection, top_n=top_n)
        _validate_evaluation(connection)
        if any(_sha256(path) != digest for path, digest in initial_hashes.items()):
            raise DataQualityError("an evaluation input changed during evaluation")

        player_rows = connection.execute(
            "SELECT count(*) FROM player_evaluation"
        ).fetchone()[0]
        evaluated_players = connection.execute(
            """SELECT evaluated_players FROM metrics
               WHERE target_name = 'modeled_points' AND predictor = 'xfp_v01'"""
        ).fetchone()[0]
        manifest = {
            "status": "complete",
            "season": season,
            "target_gameweek": target_gameweek,
            "event_name": event_name,
            "model_version": model_version,
            "evaluation_generated_at": generated_at.isoformat().replace("+00:00", "Z"),
            "bias_sign_convention": "prediction - actual; positive means overprediction",
            "metric_missing_value_policy": (
                "include only rows where predictor and actual are both non-null; "
                "never impute missing values"
            ),
            "ranking_population": (
                "all players with both frozen prediction and realized actual, "
                "including zero-minute players"
            ),
            "top_n": top_n,
            "top_n_policy": (
                "strict N-player set; fpl_player_id ascending breaks cutoff ties"
            ),
            "prediction": {
                "snapshot_timestamp": prediction_snapshot_timestamp,
                "source_path": str(prediction_path),
                "sha256": initial_hashes[prediction_path],
                "generation_timestamp": None,
                "generation_timestamp_note": "not present in frozen v0.1 output",
                "fixture_source_path": str(prediction_fixture_path),
                "fixture_sha256": initial_hashes[prediction_fixture_path],
            },
            "realized_data": {
                "snapshot_timestamp": realized_snapshot_timestamp,
                "bootstrap_path": str(realized_bootstrap_path),
                "bootstrap_sha256": initial_hashes[realized_bootstrap_path],
                "fixtures_path": str(realized_fixtures_path),
                "fixtures_sha256": initial_hashes[realized_fixtures_path],
                "history_path": str(realized_history_path),
                "history_sha256": initial_hashes[realized_history_path],
            },
            "baselines": baseline_metadata,
            "player_rows": player_rows,
            "evaluated_players": evaluated_players,
        }
        written = _write_outputs(
            connection, output_directory=output_directory, manifest=manifest
        )
    except duckdb.Error as exc:
        raise EvaluationError(f"could not evaluate xFP: {exc}") from exc
    finally:
        connection.close()
    return EvaluationOutputs(
        directory=output_directory,
        player_path=written["player"],
        metrics_path=written["metrics"],
        position_metrics_path=written["position"],
        diagnostic_metrics_path=written["diagnostic"],
        ranking_path=written["ranking"],
        manifest_path=written["manifest"],
        player_rows=player_rows,
        evaluated_players=evaluated_players,
    )


def _resolve_prediction_snapshot(
    prediction_data_root: Path,
    season: str,
    target_gameweek: int,
    snapshot_timestamp: str | None,
) -> tuple[str, Path, Path]:
    if snapshot_timestamp:
        candidates = [snapshot_timestamp]
    else:
        season_root = prediction_data_root / season
        candidates = sorted(
            (path.name for path in season_root.iterdir() if path.is_dir()),
            reverse=True,
        ) if season_root.is_dir() else []
    for candidate in candidates:
        path = (
            prediction_data_root
            / season
            / candidate
            / f"gameweek={target_gameweek}"
            / "xfp_v01_gameweek.parquet"
        )
        fixture_path = path.with_name("xfp_v01_fixtures.parquet")
        if path.is_file() and fixture_path.is_file():
            return candidate, path, fixture_path
    raise EvaluationError("no frozen xFP v0.1 prediction was found")


def _resolve_realized_snapshot(
    raw_data_root: Path,
    clean_data_root: Path,
    season: str,
    snapshot_timestamp: str | None,
) -> tuple[str, Path, Path, Path]:
    if snapshot_timestamp:
        candidates = [snapshot_timestamp]
    else:
        season_root = clean_data_root / season
        candidates = sorted(
            (path.name for path in season_root.iterdir() if path.is_dir()),
            reverse=True,
        ) if season_root.is_dir() else []
    for candidate in candidates:
        bootstrap = raw_data_root / season / candidate / "bootstrap-static.json"
        fixtures = clean_data_root / season / candidate / "fixtures.parquet"
        history = clean_data_root / season / candidate / "player_gameweek_history.parquet"
        if all(path.is_file() for path in (bootstrap, fixtures, history)):
            return candidate, bootstrap, fixtures, history
    raise EvaluationError("no complete local realized-data snapshot was found")


def evaluate_xfp(
    *,
    target_gameweek: int,
    model_version: str = SUPPORTED_MODEL_VERSION,
    prediction_snapshot_timestamp: str | None = None,
    realized_snapshot_timestamp: str | None = None,
    raw_data_root: Path = Path("data/raw/fpl"),
    clean_data_root: Path = Path("data/clean/fpl"),
    feature_data_root: Path = Path("data/features/fpl"),
    prediction_data_root: Path = Path("data/predictions/fpl"),
    evaluation_data_root: Path = Path("data/evaluations/fpl"),
    season: str = "2026-27",
    top_n: int = 10,
) -> EvaluationOutputs:
    """Resolve local frozen/realized inputs and evaluate without network access."""
    selected_prediction, prediction_path, prediction_fixture_path = (
        _resolve_prediction_snapshot(
        prediction_data_root,
        season,
        target_gameweek,
        prediction_snapshot_timestamp,
        )
    )
    selected_realized, bootstrap, fixtures, history = _resolve_realized_snapshot(
        raw_data_root,
        clean_data_root,
        season,
        realized_snapshot_timestamp,
    )
    predeadline_bootstrap = (
        raw_data_root / season / selected_prediction / "bootstrap-static.json"
    )
    predeadline_players = (
        clean_data_root / season / selected_prediction / "players.parquet"
    )
    feature_path = (
        feature_data_root
        / season
        / selected_prediction
        / f"gameweek={target_gameweek}"
        / "player_gameweek_features.parquet"
    )
    return evaluate_xfp_from_paths(
        prediction_path=prediction_path,
        prediction_fixture_path=prediction_fixture_path,
        realized_bootstrap_path=bootstrap,
        realized_fixtures_path=fixtures,
        realized_history_path=history,
        evaluation_data_root=evaluation_data_root,
        season=season,
        target_gameweek=target_gameweek,
        model_version=model_version,
        prediction_snapshot_timestamp=selected_prediction,
        realized_snapshot_timestamp=selected_realized,
        predeadline_bootstrap_path=predeadline_bootstrap,
        predeadline_players_path=predeadline_players,
        feature_path=feature_path,
        top_n=top_n,
    )
