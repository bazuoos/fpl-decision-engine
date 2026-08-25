"""Generate the deliberately small, explainable xFP v0.1 baseline."""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import duckdb

from .features import (
    FeatureBuildError,
    build_player_gameweek_features,
    find_feature_snapshot,
)
from .transform import CleanOutputExistsError, DataQualityError, TransformationError


MODEL_VERSION = "v0.1"


class PredictionError(TransformationError):
    """Raised when xFP predictions cannot be generated safely."""


@dataclass(frozen=True)
class PredictionOutputs:
    """Paths and row counts produced by one prediction run."""

    fixture_path: Path
    gameweek_path: Path
    fixture_rows: int
    gameweek_rows: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_feature_identity(
    connection: duckdb.DuckDBPyConnection,
    *,
    season: str,
    snapshot_timestamp: str,
    target_gameweek: int,
) -> None:
    identity = connection.execute(
        """SELECT count(DISTINCT season), min(season),
                  count(DISTINCT snapshot_timestamp), min(snapshot_timestamp),
                  count(DISTINCT target_gameweek), min(target_gameweek),
                  count(*)
           FROM feature_input"""
    ).fetchone()
    if identity[6] == 0:
        raise PredictionError("feature dataset is empty")
    if identity[:6] != (1, season, 1, snapshot_timestamp, 1, target_gameweek):
        raise PredictionError(
            "feature provenance does not match the requested season, snapshot, "
            "and target gameweek"
        )

    unsafe_rows = connection.execute(
        """SELECT count(*) FROM feature_input
           WHERE history_gameweek_max_used >= target_gameweek
              OR target_deadline_time <= availability_as_of
              OR target_deadline_time <= fixture_retrieved_at
              OR target_deadline_time <= history_retrieved_at"""
    ).fetchone()[0]
    if unsafe_rows:
        raise PredictionError(
            "feature dataset contains target/future history or post-deadline inputs"
        )

    invalid_rates = connection.execute(
        """SELECT count(*) FROM feature_input
           WHERE prior_xg_per_90 < 0 OR prior_xa_per_90 < 0"""
    ).fetchone()[0]
    if invalid_rates:
        raise PredictionError("historical attacking rates must be non-negative")


def _create_prediction_tables(
    connection: duckdb.DuckDBPyConnection,
    *,
    feature_sha256: str,
) -> None:
    connection.execute(
        """CREATE TEMP TABLE scored_features AS
           WITH minutes_estimate AS (
               SELECT *,
                      CASE
                        WHEN NOT previous_gameweek_has_data THEN NULL
                        WHEN availability_known_pre_deadline
                         AND availability_is_target_next_gameweek
                         AND chance_of_playing_next_round = 0 THEN 0.0
                        WHEN availability_known_pre_deadline
                         AND lower(availability_status) IN ('s', 'u') THEN 0.0
                        ELSE greatest(0.0, least(90.0, previous_gw_minutes))
                      END AS expected_minutes_v01,
                      CASE
                        WHEN availability_known_pre_deadline
                         AND availability_is_target_next_gameweek
                         AND chance_of_playing_next_round = 0
                          THEN 'explicit_zero_chance'
                        WHEN availability_known_pre_deadline
                         AND lower(availability_status) = 's' THEN 'suspended'
                        WHEN availability_known_pre_deadline
                         AND lower(availability_status) = 'u' THEN 'unavailable'
                      END AS availability_gate_reason,
                      prior_total_minutes > 0
                        AND prior_xg_per_90 IS NOT NULL
                        AND prior_xa_per_90 IS NOT NULL
                        AS attacking_rate_available
               FROM feature_input
           ), components AS (
               SELECT *,
                      CASE WHEN expected_minutes_v01 IS NULL THEN NULL
                           WHEN expected_minutes_v01 = 0 THEN 0.0
                           WHEN expected_minutes_v01 < 60 THEN 1.0
                           ELSE 2.0 END AS appearance_xfp_v01,
                      CASE WHEN prior_xg_per_90 IS NOT NULL
                                  AND expected_minutes_v01 IS NOT NULL
                           THEN prior_xg_per_90 * expected_minutes_v01 / 90.0 END
                           AS expected_goals_v01,
                      CASE WHEN prior_xa_per_90 IS NOT NULL
                                  AND expected_minutes_v01 IS NOT NULL
                           THEN prior_xa_per_90 * expected_minutes_v01 / 90.0 END
                           AS expected_assists_v01,
                      CASE position
                        WHEN 'Goalkeeper' THEN 10
                        WHEN 'Defender' THEN 6
                        WHEN 'Midfielder' THEN 5
                        WHEN 'Forward' THEN 4
                      END AS goal_points_for_position
               FROM minutes_estimate
           )
           SELECT *,
                  expected_goals_v01 * goal_points_for_position AS goal_xfp_v01,
                  expected_assists_v01 * 3.0 AS assist_xfp_v01
           FROM components"""
    )
    connection.execute(
        """CREATE TABLE fixture_predictions AS
           SELECT season,
                  snapshot_timestamp,
                  'v0.1'::VARCHAR AS model_version,
                  target_gameweek,
                  target_fixture_id AS fixture_id,
                  target_has_fixture,
                  target_fixture_count,
                  fpl_player_id,
                  web_name,
                  position_id,
                  position,
                  snapshot_team_id AS team_id,
                  snapshot_team_name AS team_name,
                  target_opponent_team_id AS opponent_team_id,
                  target_opponent_team_name AS opponent_team_name,
                  target_home_away AS home_away,
                  target_kickoff_time AS kickoff_time,
                  expected_minutes_v01,
                  previous_gw_minutes AS previous_gameweek_minutes,
                  prior_total_minutes AS prior_minutes,
                  prior_gameweeks_with_data,
                  history_gameweek_max_used,
                  prior_xg_per_90 AS prior_xg_per_90_used,
                  prior_xa_per_90 AS prior_xa_per_90_used,
                  appearance_xfp_v01,
                  expected_goals_v01,
                  goal_points_for_position,
                  goal_xfp_v01,
                  expected_assists_v01,
                  assist_xfp_v01,
                  CASE WHEN target_has_fixture AND appearance_xfp_v01 IS NOT NULL
                       THEN appearance_xfp_v01
                            + coalesce(goal_xfp_v01, 0.0)
                            + coalesce(assist_xfp_v01, 0.0)
                       WHEN NOT target_has_fixture THEN NULL
                  END AS fixture_xfp_v01,
                  attacking_rate_available,
                  target_has_fixture AND appearance_xfp_v01 IS NOT NULL
                    AND goal_xfp_v01 IS NOT NULL AND assist_xfp_v01 IS NOT NULL
                    AS prediction_complete,
                  prior_gameweeks_with_data < 3 AS low_sample,
                  availability_status,
                  chance_of_playing_next_round,
                  availability_news,
                  availability_as_of,
                  availability_known_pre_deadline,
                  availability_reference_gameweek,
                  availability_is_target_next_gameweek,
                  availability_gate_reason IS NOT NULL AS availability_forced_zero,
                  availability_gate_reason,
                  target_deadline_time,
                  ?::VARCHAR AS feature_input_sha256,
                  players_input_sha256,
                  fixtures_input_sha256,
                  history_input_sha256,
                  bootstrap_sha256
           FROM scored_features""",
        [feature_sha256],
    )
    connection.execute(
        """CREATE TABLE gameweek_predictions AS
           SELECT season,
                  snapshot_timestamp,
                  model_version,
                  target_gameweek,
                  fpl_player_id,
                  min(web_name) AS web_name,
                  min(position_id) AS position_id,
                  min(position) AS position,
                  min(team_id) AS team_id,
                  min(team_name) AS team_name,
                  count(*) FILTER (WHERE target_has_fixture) AS fixture_count,
                  coalesce(sum(expected_minutes_v01)
                           FILTER (WHERE target_has_fixture), 0.0)
                      AS gameweek_expected_minutes_v01,
                  coalesce(sum(appearance_xfp_v01)
                           FILTER (WHERE target_has_fixture), 0.0)
                      AS gameweek_appearance_xfp_v01,
                  coalesce(sum(goal_xfp_v01)
                           FILTER (WHERE target_has_fixture), 0.0)
                      AS gameweek_goal_xfp_v01,
                  coalesce(sum(assist_xfp_v01)
                           FILTER (WHERE target_has_fixture), 0.0)
                      AS gameweek_assist_xfp_v01,
                  CASE
                    WHEN count(*) FILTER (WHERE target_has_fixture) = 0 THEN 0.0
                    WHEN count(fixture_xfp_v01) FILTER (WHERE target_has_fixture)
                         = count(*) FILTER (WHERE target_has_fixture)
                      THEN sum(fixture_xfp_v01) FILTER (WHERE target_has_fixture)
                  END AS gameweek_xfp_v01,
                  bool_and(attacking_rate_available) AS attacking_rate_available,
                  CASE WHEN count(*) FILTER (WHERE target_has_fixture) = 0 THEN true
                       ELSE bool_and(prediction_complete)
                            FILTER (WHERE target_has_fixture) END
                      AS prediction_complete,
                  bool_or(low_sample) AS low_sample,
                  min(prior_minutes) AS prior_minutes,
                  min(prior_gameweeks_with_data) AS prior_gameweeks_with_data,
                  min(history_gameweek_max_used) AS history_gameweek_max_used,
                  min(prior_xg_per_90_used) AS prior_xg_per_90_used,
                  min(prior_xa_per_90_used) AS prior_xa_per_90_used,
                  min(feature_input_sha256) AS feature_input_sha256,
                  min(players_input_sha256) AS players_input_sha256,
                  min(fixtures_input_sha256) AS fixtures_input_sha256,
                  min(history_input_sha256) AS history_input_sha256,
                  min(bootstrap_sha256) AS bootstrap_sha256
           FROM fixture_predictions
           GROUP BY season, snapshot_timestamp, model_version,
                    target_gameweek, fpl_player_id"""
    )


def _validate_predictions(connection: duckdb.DuckDBPyConnection) -> None:
    checks = (
        (
            "fixture prediction keys are not unique",
            """SELECT count(*) = count(DISTINCT (
                       fpl_player_id, target_gameweek, coalesce(fixture_id, -1)))
                 FROM fixture_predictions""",
        ),
        (
            "gameweek prediction keys are not unique",
            """SELECT count(*) = count(DISTINCT (fpl_player_id, target_gameweek))
                 FROM gameweek_predictions""",
        ),
        (
            "a player was lost in gameweek aggregation",
            """SELECT count(DISTINCT fpl_player_id) =
                       (SELECT count(*) FROM gameweek_predictions)
                 FROM fixture_predictions""",
        ),
        (
            "expected minutes must be between zero and 90",
            """SELECT count(*) = 0 FROM fixture_predictions
                 WHERE expected_minutes_v01 NOT BETWEEN 0 AND 90""",
        ),
        (
            "appearance xFP must be 0, 1, or 2",
            """SELECT count(*) = 0 FROM fixture_predictions
                 WHERE appearance_xfp_v01 NOT IN (0, 1, 2)""",
        ),
        (
            "expected attacking events must be non-negative",
            """SELECT count(*) = 0 FROM fixture_predictions
                 WHERE expected_goals_v01 < 0 OR expected_assists_v01 < 0""",
        ),
        (
            "goal scoring is inconsistent with position",
            """SELECT count(*) = 0 FROM fixture_predictions
                 WHERE goal_points_for_position IS NULL
                    OR goal_points_for_position <>
                       CASE position WHEN 'Goalkeeper' THEN 10
                                     WHEN 'Defender' THEN 6
                                     WHEN 'Midfielder' THEN 5
                                     WHEN 'Forward' THEN 4 END
                    OR abs(goal_xfp_v01 - expected_goals_v01
                           * goal_points_for_position) > 1e-10""",
        ),
        (
            "assist scoring is not three points per expected assist",
            """SELECT count(*) = 0 FROM fixture_predictions
                 WHERE abs(assist_xfp_v01 - expected_assists_v01 * 3.0) > 1e-10""",
        ),
        (
            "fixture total violates the documented null policy",
            """SELECT count(*) = 0 FROM fixture_predictions
                 WHERE target_has_fixture AND appearance_xfp_v01 IS NOT NULL
                   AND abs(fixture_xfp_v01 - appearance_xfp_v01
                           - coalesce(goal_xfp_v01, 0.0)
                           - coalesce(assist_xfp_v01, 0.0)) > 1e-10
                    OR target_has_fixture AND appearance_xfp_v01 IS NULL
                       AND fixture_xfp_v01 IS NOT NULL
                    OR NOT target_has_fixture AND fixture_xfp_v01 IS NOT NULL""",
        ),
        (
            "blank gameweek is not represented as zero",
            """SELECT count(*) = 0 FROM gameweek_predictions
                 WHERE fixture_count = 0 AND gameweek_xfp_v01 <> 0""",
        ),
        (
            "normal/double gameweek aggregation is incorrect",
            """SELECT count(*) = 0
                 FROM gameweek_predictions g
                 JOIN (SELECT fpl_player_id, target_gameweek,
                              count(*) FILTER (WHERE target_has_fixture) AS n,
                              count(fixture_xfp_v01)
                                FILTER (WHERE target_has_fixture) AS scored,
                              sum(fixture_xfp_v01)
                                FILTER (WHERE target_has_fixture) AS expected
                       FROM fixture_predictions
                       GROUP BY fpl_player_id, target_gameweek) f
                   USING (fpl_player_id, target_gameweek)
                 WHERE f.n > 0 AND f.n = f.scored
                   AND abs(g.gameweek_xfp_v01 - f.expected) > 1e-10""",
        ),
        (
            "low-sample flag is inconsistent",
            """SELECT count(*) = 0 FROM fixture_predictions
                 WHERE low_sample <> (prior_gameweeks_with_data < 3)""",
        ),
    )
    for message, query in checks:
        if not connection.execute(query).fetchone()[0]:
            raise DataQualityError(message)


def _write_outputs_exclusive(
    connection: duckdb.DuckDBPyConnection,
    output_directory: Path,
) -> tuple[Path, Path]:
    fixture_path = output_directory / "xfp_v01_fixtures.parquet"
    gameweek_path = output_directory / "xfp_v01_gameweek.parquet"
    if output_directory.exists():
        raise CleanOutputExistsError(
            "prediction output already exists and will not be overwritten: "
            f"{output_directory}"
        )

    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary_directory = output_directory.parent / f".{uuid.uuid4().hex}.tmp"
    temporary_directory.mkdir()
    temporary_fixture = temporary_directory / fixture_path.name
    temporary_gameweek = temporary_directory / gameweek_path.name
    output_created = False
    try:
        connection.execute(
            "COPY fixture_predictions TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(temporary_fixture)],
        )
        connection.execute(
            "COPY gameweek_predictions TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
            [str(temporary_gameweek)],
        )
        try:
            output_directory.mkdir()
            output_created = True
        except FileExistsError as exc:
            raise CleanOutputExistsError(
                "prediction output already exists and will not be overwritten: "
                f"{output_directory}"
            ) from exc
        os.link(temporary_fixture, fixture_path)
        os.link(temporary_gameweek, gameweek_path)
    except Exception:
        if output_created:
            fixture_path.unlink(missing_ok=True)
            gameweek_path.unlink(missing_ok=True)
            output_directory.rmdir()
        raise
    finally:
        temporary_fixture.unlink(missing_ok=True)
        temporary_gameweek.unlink(missing_ok=True)
        temporary_directory.rmdir()
    return fixture_path, gameweek_path


def predict_xfp_v01_from_feature(
    *,
    feature_path: Path,
    prediction_data_root: Path,
    season: str,
    snapshot_timestamp: str,
    target_gameweek: int,
) -> PredictionOutputs:
    """Generate xFP v0.1 from one already-built, leakage-safe feature file."""
    if not feature_path.is_file():
        raise PredictionError(f"feature dataset does not exist: {feature_path}")
    output_directory = (
        prediction_data_root
        / season
        / snapshot_timestamp
        / f"gameweek={target_gameweek}"
    )
    if output_directory.exists():
        raise CleanOutputExistsError(
            "prediction output already exists and will not be overwritten: "
            f"{output_directory}"
        )

    feature_hash = _sha256(feature_path)
    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE feature_input AS SELECT * FROM read_parquet(?)",
            [str(feature_path)],
        )
        _validate_feature_identity(
            connection,
            season=season,
            snapshot_timestamp=snapshot_timestamp,
            target_gameweek=target_gameweek,
        )
        _create_prediction_tables(connection, feature_sha256=feature_hash)
        _validate_predictions(connection)
        if _sha256(feature_path) != feature_hash:
            raise DataQualityError("feature input changed during prediction generation")
        fixture_rows = connection.execute(
            "SELECT count(*) FROM fixture_predictions"
        ).fetchone()[0]
        gameweek_rows = connection.execute(
            "SELECT count(*) FROM gameweek_predictions"
        ).fetchone()[0]
        fixture_path, gameweek_path = _write_outputs_exclusive(
            connection, output_directory
        )
    except duckdb.Error as exc:
        raise PredictionError(f"could not generate xFP v0.1: {exc}") from exc
    finally:
        connection.close()
    return PredictionOutputs(
        fixture_path=fixture_path,
        gameweek_path=gameweek_path,
        fixture_rows=fixture_rows,
        gameweek_rows=gameweek_rows,
    )


def predict_xfp_v01(
    *,
    target_gameweek: int,
    raw_data_root: Path = Path("data/raw/fpl"),
    clean_data_root: Path = Path("data/clean/fpl"),
    feature_data_root: Path = Path("data/features/fpl"),
    prediction_data_root: Path = Path("data/predictions/fpl"),
    season: str = "2026-27",
    snapshot_timestamp: str | None = None,
) -> PredictionOutputs:
    """Locate or build safe features, then generate immutable xFP v0.1 outputs."""
    if target_gameweek < 1:
        raise PredictionError("target_gameweek must be at least 1")
    try:
        selected_snapshot, _, _ = find_feature_snapshot(
            raw_data_root=raw_data_root,
            clean_data_root=clean_data_root,
            season=season,
            target_gameweek=target_gameweek,
            snapshot_timestamp=snapshot_timestamp,
        )
    except FeatureBuildError as exc:
        raise PredictionError(str(exc)) from exc

    feature_path = (
        feature_data_root
        / season
        / selected_snapshot
        / f"gameweek={target_gameweek}"
        / "player_gameweek_features.parquet"
    )
    if not feature_path.is_file():
        feature_path = build_player_gameweek_features(
            target_gameweek=target_gameweek,
            raw_data_root=raw_data_root,
            clean_data_root=clean_data_root,
            feature_data_root=feature_data_root,
            season=season,
            snapshot_timestamp=selected_snapshot,
        )
    return predict_xfp_v01_from_feature(
        feature_path=feature_path,
        prediction_data_root=prediction_data_root,
        season=season,
        snapshot_timestamp=selected_snapshot,
        target_gameweek=target_gameweek,
    )
