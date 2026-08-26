"""Pre-registered expected-minutes experiment against frozen xFP v0.1."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from .historical import HISTORICAL_CLASSIFICATION, HISTORY_CUTOFF_RULE
from .historical_backtest import _spearman
from .transform import TransformationError


EXPERIMENT_VERSION = "minutes-v02-experiment-v1"
BASELINE_VERSION = "xfp-v01-baseline-v1"
HISTORICAL_VERSION = "historical-v2"
DEVELOPMENT_SEASON = "2023-24"
HOLDOUT_SEASON = "2024-25"
TARGET_GAMEWEEKS = tuple(range(2, 39))
CANDIDATES = ("M0", "M1", "M2", "M3")
TOP_N_VALUES = (10, 25, 50)
ORACLE_MODELED_MAE_REDUCTION_PCT = 34.53
ORACLE_MODELED_RMSE_REDUCTION_PCT = 17.35

DEVELOPMENT_THRESHOLDS = {
    "minutes_mae_reduction_pct": 5.0,
    "minutes_rmse_reduction_pct": 3.0,
    "modeled_xfp_mae_reduction_pct": 2.0,
    "maximum_coverage_drop_pp": 1.0,
}
HOLDOUT_THRESHOLDS = {
    **DEVELOPMENT_THRESHOLDS,
    "maximum_modeled_spearman_drop": 0.01,
    "appearance_mae_must_not_worsen": True,
}


class HistoricalMinutesExperimentError(TransformationError):
    """Raised when the controlled minutes experiment cannot run safely."""


class HistoricalMinutesExperimentOutputExistsError(HistoricalMinutesExperimentError):
    """Raised rather than overwriting an immutable experiment."""


@dataclass(frozen=True)
class HistoricalMinutesExperimentResult:
    directory: Path
    manifest_path: Path
    development_winner: str | None
    holdout_passed: bool | None
    final_decision: str


METRIC_SCHEMA = (
    ("phase", "VARCHAR"), ("season", "VARCHAR"),
    ("candidate", "VARCHAR"), ("population", "VARCHAR"),
    ("target", "VARCHAR"), ("n_eligible", "BIGINT"),
    ("n_complete_pairs", "BIGINT"), ("missing_prediction", "BIGINT"),
    ("missing_actual", "BIGINT"), ("coverage_pct", "DOUBLE"),
    ("mae", "DOUBLE"), ("rmse", "DOUBLE"), ("bias", "DOUBLE"),
    ("spearman", "DOUBLE"),
)

COMMON_PAIR_SCHEMA = (
    ("phase", "VARCHAR"), ("season", "VARCHAR"),
    ("comparison_candidate", "VARCHAR"), ("predictor", "VARCHAR"),
    ("target", "VARCHAR"), ("n_common_pairs", "BIGINT"),
    ("mae", "DOUBLE"), ("rmse", "DOUBLE"), ("bias", "DOUBLE"),
    ("spearman", "DOUBLE"),
)

RANKING_SCHEMA = (
    ("phase", "VARCHAR"), ("season", "VARCHAR"),
    ("candidate", "VARCHAR"), ("row_type", "VARCHAR"),
    ("target_gameweek", "INTEGER"), ("top_n", "INTEGER"),
    ("n_complete_pairs", "BIGINT"), ("strict_n_available", "BOOLEAN"),
    ("overlap_count", "DOUBLE"), ("overlap_pct", "DOUBLE"),
    ("gameweeks_summarized", "INTEGER"), ("tie_breaker", "VARCHAR"),
)

SELECTION_SCHEMA = (
    ("candidate", "VARCHAR"), ("development_qualifies", "BOOLEAN"),
    ("minutes_mae_reduction_pct", "DOUBLE"),
    ("minutes_rmse_reduction_pct", "DOUBLE"),
    ("modeled_xfp_mae_reduction_pct", "DOUBLE"),
    ("modeled_xfp_rmse", "DOUBLE"), ("minutes_mae", "DOUBLE"),
    ("coverage_drop_pp", "DOUBLE"), ("selected_for_holdout", "BOOLEAN"),
    ("holdout_passed", "BOOLEAN"),
    ("holdout_minutes_mae_reduction_pct", "DOUBLE"),
    ("holdout_minutes_rmse_reduction_pct", "DOUBLE"),
    ("holdout_modeled_xfp_mae_reduction_pct", "DOUBLE"),
    ("holdout_appearance_mae_change", "DOUBLE"),
    ("holdout_modeled_spearman_drop", "DOUBLE"),
    ("holdout_coverage_drop_pp", "DOUBLE"),
    ("oracle_mae_improvement_captured_pct", "DOUBLE"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalMinutesExperimentError(f"could not read manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HistoricalMinutesExperimentError(f"manifest is not an object: {path}")
    return value


def _write_exclusive(path: Path, body: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise HistoricalMinutesExperimentOutputExistsError(
                f"experiment output already exists and will not be overwritten: {path}"
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
    placeholders = f"({', '.join('?' for _ in schema)})"
    for start in range(0, len(rows), 250):
        batch = rows[start : start + 250]
        connection.execute(
            f'INSERT INTO "{table}" VALUES {", ".join(placeholders for _ in batch)}',
            [value for row in batch for value in row],
        )


def _validate_inputs(
    *, historical_clean_root: Path, baseline_root: Path
) -> tuple[dict[str, Path], dict[Path, str]]:
    historical_root = historical_clean_root / HISTORICAL_VERSION
    historical_manifest_path = historical_root / "historical_ingestion_manifest.json"
    baseline_directory = baseline_root / BASELINE_VERSION
    baseline_manifest_path = baseline_directory / "backtest_manifest.json"
    historical_manifest = _load_json(historical_manifest_path)
    baseline_manifest = _load_json(baseline_manifest_path)
    if historical_manifest.get("status") != "complete" or historical_manifest.get(
        "parser_schema_version"
    ) != HISTORICAL_VERSION:
        raise HistoricalMinutesExperimentError("historical-v2 manifest is not complete")
    if baseline_manifest.get("status") != "complete" or baseline_manifest.get(
        "backtest_version"
    ) != BASELINE_VERSION:
        raise HistoricalMinutesExperimentError("frozen baseline manifest is not complete")
    if baseline_manifest.get("historical_classification") != HISTORICAL_CLASSIFICATION:
        raise HistoricalMinutesExperimentError("baseline classification changed")

    historical_outputs = {
        entry["path"]: entry["sha256"] for entry in historical_manifest.get("outputs", [])
    }
    baseline_outputs = {
        entry["path"]: entry["sha256"] for entry in baseline_manifest.get("outputs", [])
    }
    paths: dict[str, Path] = {
        "historical_manifest": historical_manifest_path,
        "baseline_manifest": baseline_manifest_path,
        "baseline_fixture": baseline_directory / "fixture_predictions.parquet",
        "baseline_player_gameweek": baseline_directory / "player_gameweek.parquet",
    }
    expected: dict[Path, str] = {
        historical_manifest_path: _sha256(historical_manifest_path),
        baseline_manifest_path: _sha256(baseline_manifest_path),
        paths["baseline_fixture"]: baseline_outputs.get("fixture_predictions.parquet", ""),
        paths["baseline_player_gameweek"]: baseline_outputs.get("player_gameweek.parquet", ""),
    }
    for season in (DEVELOPMENT_SEASON, HOLDOUT_SEASON):
        for kind, filename in (
            ("features", "historical_prediction_features.parquet"),
            ("player_fixture", "historical_player_fixture.parquet"),
            ("predeadline", "historical_predeadline_player_state.parquet"),
            ("fixtures", "historical_fixtures.parquet"),
        ):
            key = f"{season}_{kind}"
            relative = f"{season}/{filename}"
            paths[key] = historical_root / relative
            expected[paths[key]] = historical_outputs.get(relative, "")
    for path, expected_digest in expected.items():
        if not path.is_file():
            raise HistoricalMinutesExperimentError(f"required immutable input is missing: {path}")
        if not expected_digest or _sha256(path) != expected_digest:
            raise HistoricalMinutesExperimentError(
                f"immutable input hash does not match its manifest: {path}"
            )
    return paths, expected


def _load_phase_inputs(
    connection: duckdb.DuckDBPyConnection,
    *, phase: str, season: str, paths: dict[str, Path], candidates: Sequence[str],
) -> None:
    if any(candidate not in CANDIDATES for candidate in candidates):
        raise HistoricalMinutesExperimentError("unknown expected-minutes candidate")
    fixture = str(paths["baseline_fixture"])
    player = str(paths["baseline_player_gameweek"])
    history = str(paths[f"{season}_player_fixture"])
    features = str(paths[f"{season}_features"])
    predeadline = str(paths[f"{season}_predeadline"])
    fixtures = str(paths[f"{season}_fixtures"])
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_base_fixture AS
            SELECT * FROM read_parquet(?)
            WHERE season=? AND target_gameweek BETWEEN 2 AND 38""",
        [fixture, season],
    )
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_base_player AS
            SELECT * FROM read_parquet(?)
            WHERE season=? AND target_gameweek BETWEEN 2 AND 38""",
        [player, season],
    )
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_history AS
            SELECT * FROM read_parquet(?) WHERE season=?""",
        [history, season],
    )
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_feature_windows AS
            SELECT DISTINCT season,target_gameweek,element_id,target_deadline,
                   rolling_3_gameweeks_with_data,rolling_3_minutes,
                   rolling_5_gameweeks_with_data,rolling_5_minutes
            FROM read_parquet(?)
            WHERE season=? AND target_gameweek BETWEEN 2 AND 38""",
        [features, season],
    )
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_source_universe AS
            SELECT DISTINCT season,target_gameweek,element_id,team_id
            FROM read_parquet(?) WHERE season=?""",
        [predeadline, season],
    )
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_fixtures AS
            SELECT season,fixture_id,gameweek,home_team_id,away_team_id,kickoff_time
            FROM read_parquet(?) WHERE season=?""",
        [fixtures, season],
    )
    candidate_values = ",".join(f"('{candidate}')" for candidate in candidates)
    connection.execute(
        f"CREATE OR REPLACE TABLE {phase}_candidate_set(candidate) AS VALUES {candidate_values}"
    )


def _create_phase_predictions(
    connection: duckdb.DuckDBPyConnection, *, phase: str, season: str
) -> tuple[str, str]:
    """Create candidate predictions without reading realized target outcomes as inputs."""
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_minute_observations AS
            WITH targets AS (
              SELECT DISTINCT season,target_gameweek,element_id,target_deadline
              FROM {phase}_base_fixture
            )
            SELECT t.season,t.target_gameweek,t.element_id,t.target_deadline,
                   h.gameweek source_gameweek,
                   sum(h.minutes)::DOUBLE observed_minutes,
                   count(*)::INTEGER observed_fixture_count,
                   max(h.kickoff_time) latest_source_kickoff
            FROM targets t
            JOIN {phase}_history h
              ON h.season=t.season AND h.element_id=t.element_id
             AND h.gameweek<t.target_gameweek
             AND h.gameweek>=t.target_gameweek-5
             AND h.kickoff_time<t.target_deadline
            JOIN {phase}_source_universe u
              ON u.season=h.season AND u.target_gameweek=h.gameweek
             AND u.element_id=h.element_id
            GROUP BY t.season,t.target_gameweek,t.element_id,t.target_deadline,h.gameweek"""
    )
    leakage = connection.execute(
        f"""SELECT count(*) FROM {phase}_minute_observations
            WHERE source_gameweek>=target_gameweek
               OR latest_source_kickoff>=target_deadline"""
    ).fetchone()[0]
    if leakage:
        raise HistoricalMinutesExperimentError(
            f"{phase} expected-minutes observations violate the deadline cutoff"
        )
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_window_estimates AS
            WITH targets AS (
              SELECT DISTINCT season,target_gameweek,element_id,target_deadline
              FROM {phase}_base_fixture
            )
            SELECT t.*,
              count(o.source_gameweek) FILTER(
                WHERE o.source_gameweek>=t.target_gameweek-3)::INTEGER m1_observations,
              avg(o.observed_minutes) FILTER(
                WHERE o.source_gameweek>=t.target_gameweek-3) m1_minutes,
              count(o.source_gameweek)::INTEGER m2_observations,
              avg(o.observed_minutes) m2_minutes,
              count(o.source_gameweek) FILTER(
                WHERE o.source_gameweek>=t.target_gameweek-3)::INTEGER m3_observations,
              sum(o.observed_minutes*CASE t.target_gameweek-o.source_gameweek
                    WHEN 1 THEN 0.60 WHEN 2 THEN 0.30 WHEN 3 THEN 0.10 END)
                FILTER(WHERE o.source_gameweek>=t.target_gameweek-3)
              /nullif(sum(CASE t.target_gameweek-o.source_gameweek
                    WHEN 1 THEN 0.60 WHEN 2 THEN 0.30 WHEN 3 THEN 0.10 END)
                FILTER(WHERE o.source_gameweek>=t.target_gameweek-3),0) m3_minutes
            FROM targets t LEFT JOIN {phase}_minute_observations o USING(
              season,target_gameweek,element_id,target_deadline)
            GROUP BY ALL"""
    )
    corrupt = connection.execute(
        f"""WITH targets AS (
              SELECT DISTINCT season,target_gameweek,element_id,target_deadline
              FROM {phase}_base_fixture
            ), eligible_team_context AS (
              SELECT DISTINCT t.season,t.target_gameweek,t.element_id,t.target_deadline,
                     u.target_gameweek source_gameweek
              FROM targets t
              CROSS JOIN range(1,6) lag
              JOIN {phase}_source_universe u
                ON u.season=t.season AND u.element_id=t.element_id
               AND u.target_gameweek=t.target_gameweek-lag.range
              JOIN {phase}_fixtures f
                ON f.season=u.season AND f.gameweek=u.target_gameweek
               AND (f.home_team_id=u.team_id OR f.away_team_id=u.team_id)
               AND f.kickoff_time<t.target_deadline
            )
            SELECT count(*) FROM eligible_team_context c
            LEFT JOIN {phase}_minute_observations o USING(
              season,target_gameweek,element_id,target_deadline,source_gameweek)
            WHERE o.source_gameweek IS NULL"""
    ).fetchone()[0]
    if corrupt:
        raise HistoricalMinutesExperimentError(
            f"{phase} has {corrupt} missing/corrupt player-GW observations where a team fixture existed"
        )

    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_candidate_fixture AS
            WITH estimates AS (
              SELECT b.*,c.candidate,w.m1_observations,w.m1_minutes,
                     w.m2_observations,w.m2_minutes,w.m3_observations,w.m3_minutes,
                     CASE c.candidate
                       WHEN 'M0' THEN b.previous_gameweek_minutes_uncapped::DOUBLE
                       WHEN 'M1' THEN w.m1_minutes
                       WHEN 'M2' THEN w.m2_minutes
                       WHEN 'M3' THEN w.m3_minutes END candidate_minutes_before_cap_gate,
                     CASE c.candidate
                       WHEN 'M0' THEN CASE WHEN b.previous_gameweek_minutes_uncapped IS NULL THEN 0 ELSE 1 END
                       WHEN 'M1' THEN w.m1_observations
                       WHEN 'M2' THEN w.m2_observations
                       WHEN 'M3' THEN w.m3_observations END candidate_observed_gameweeks
              FROM {phase}_base_fixture b
              CROSS JOIN {phase}_candidate_set c
              JOIN {phase}_window_estimates w USING(season,target_gameweek,element_id,target_deadline)
            ), gated AS (
              SELECT *,CASE
                WHEN candidate='M0' THEN expected_minutes_v01::DOUBLE
                WHEN candidate_minutes_before_cap_gate IS NULL THEN NULL
                WHEN availability_known_pre_deadline
                  AND chance_of_playing_next_round=0 THEN 0.0
                WHEN availability_known_pre_deadline
                  AND lower(availability_status) IN ('s','u') THEN 0.0
                ELSE greatest(0.0,least(90.0,candidate_minutes_before_cap_gate))
              END candidate_expected_minutes
              FROM estimates
            ), components AS (
              SELECT *,
                CASE WHEN candidate='M0' THEN appearance_xfp_v01::DOUBLE
                     WHEN candidate_expected_minutes IS NULL THEN NULL
                     WHEN candidate_expected_minutes=0 THEN 0.0
                     WHEN candidate_expected_minutes<60 THEN 1.0 ELSE 2.0 END candidate_appearance_xfp,
                CASE WHEN candidate='M0' THEN goal_xfp_v01
                     WHEN prior_xg_per_90_used IS NOT NULL AND candidate_expected_minutes IS NOT NULL
                     THEN prior_xg_per_90_used*candidate_expected_minutes/90.0*goal_points_for_position END candidate_goal_xfp,
                CASE WHEN candidate='M0' THEN assist_xfp_v01
                     WHEN prior_xa_per_90_used IS NOT NULL AND candidate_expected_minutes IS NOT NULL
                     THEN prior_xa_per_90_used*candidate_expected_minutes/90.0*3.0 END candidate_assist_xfp
              FROM gated
            )
            SELECT ?::VARCHAR phase,season,candidate,target_gameweek,fixture_id,
                   target_has_fixture,target_fixture_count,element_id,code,"position",
                   team_id,team_name,opponent_team_id,home_away,kickoff_time,target_deadline,
                   candidate_minutes_before_cap_gate,candidate_observed_gameweeks,
                   candidate_expected_minutes,candidate_appearance_xfp,
                   candidate_goal_xfp,candidate_assist_xfp,
                   CASE WHEN candidate='M0' THEN fixture_xfp_v01
                        WHEN target_has_fixture AND candidate_appearance_xfp IS NOT NULL
                        THEN candidate_appearance_xfp+coalesce(candidate_goal_xfp,0.0)
                             +coalesce(candidate_assist_xfp,0.0)
                        WHEN NOT target_has_fixture THEN NULL END candidate_fixture_xfp,
                   attacking_rate_available,
                   target_has_fixture AND candidate_appearance_xfp IS NOT NULL
                     AND candidate_goal_xfp IS NOT NULL AND candidate_assist_xfp IS NOT NULL
                     AS prediction_complete,
                   prior_xg_per_90_used,prior_xa_per_90_used,goal_points_for_position,
                   prior_total_minutes,prior_gameweeks_with_data,
                   previous_gameweek_minutes_uncapped,previous_gw_context_status,
                   previous_gw_team_blank,previous_gw_player_not_in_universe,
                   availability_status,chance_of_playing_next_round,
                   availability_known_pre_deadline,availability_forced_zero,
                   history_gameweek_max_used,history_latest_kickoff_used,history_cutoff_rule,
                   historical_classification
            FROM components""",
        [phase],
    )
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_candidate_player AS
            WITH aggregated AS (
              SELECT phase,season,candidate,target_gameweek,element_id,
                min(code) code,min("position") "position",min(team_id) team_id,
                min(team_name) team_name,
                count(*) FILTER(WHERE target_has_fixture) fixture_count,
                min(candidate_minutes_before_cap_gate) candidate_minutes_before_cap_gate,
                min(candidate_observed_gameweeks) candidate_observed_gameweeks,
                CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                     WHEN count(candidate_expected_minutes) FILTER(WHERE target_has_fixture)
                          =count(*) FILTER(WHERE target_has_fixture)
                     THEN sum(candidate_expected_minutes) FILTER(WHERE target_has_fixture) END gameweek_expected_minutes,
                CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                     WHEN count(candidate_appearance_xfp) FILTER(WHERE target_has_fixture)
                          =count(*) FILTER(WHERE target_has_fixture)
                     THEN sum(candidate_appearance_xfp) FILTER(WHERE target_has_fixture) END gameweek_appearance_xfp,
                CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                     WHEN count(candidate_goal_xfp) FILTER(WHERE target_has_fixture)
                          =count(*) FILTER(WHERE target_has_fixture)
                     THEN sum(candidate_goal_xfp) FILTER(WHERE target_has_fixture) END gameweek_goal_xfp,
                CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                     WHEN count(candidate_assist_xfp) FILTER(WHERE target_has_fixture)
                          =count(*) FILTER(WHERE target_has_fixture)
                     THEN sum(candidate_assist_xfp) FILTER(WHERE target_has_fixture) END gameweek_assist_xfp,
                CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                     WHEN count(candidate_fixture_xfp) FILTER(WHERE target_has_fixture)
                          =count(*) FILTER(WHERE target_has_fixture)
                     THEN sum(candidate_fixture_xfp) FILTER(WHERE target_has_fixture) END gameweek_xfp,
                CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN true
                     ELSE bool_and(prediction_complete) FILTER(WHERE target_has_fixture) END prediction_complete,
                bool_and(attacking_rate_available) attacking_rate_available,
                min(prior_total_minutes) prior_total_minutes,
                min(prior_gameweeks_with_data) prior_gameweeks_with_data,
                min(previous_gameweek_minutes_uncapped) previous_gameweek_minutes_uncapped,
                min(previous_gw_context_status) previous_gw_context_status,
                bool_or(previous_gw_team_blank) previous_gw_team_blank,
                bool_or(previous_gw_player_not_in_universe) previous_gw_player_not_in_universe,
                min(availability_status) availability_status,
                min(chance_of_playing_next_round) chance_of_playing_next_round,
                bool_or(availability_forced_zero) availability_forced_zero,
                min(target_deadline) target_deadline,
                min(history_cutoff_rule) history_cutoff_rule,
                min(historical_classification) historical_classification
              FROM {phase}_candidate_fixture GROUP BY phase,season,candidate,target_gameweek,element_id
            )
            SELECT a.*,b.gameweek_expected_minutes_for_evaluation baseline_expected_minutes,
                   b.availability_band,b.actual_fixture_count,b.actual_minutes,
                   b.actual_appearance_points,b.actual_goal_points,b.actual_assist_points,
                   b.actual_modeled_points,b.actual_full_fpl_points,b.actual_state
            FROM aggregated a JOIN {phase}_base_player b USING(season,target_gameweek,element_id)"""
    )
    invalid = connection.execute(
        f"""SELECT count(*) FROM {phase}_candidate_player
            WHERE historical_classification<>?
               OR history_cutoff_rule<>?
               OR "position" NOT IN ('GK','DEF','MID','FWD')
               OR (fixture_count=0 AND (gameweek_expected_minutes<>0 OR gameweek_xfp<>0
                    OR actual_minutes<>0 OR actual_modeled_points<>0))""",
        [HISTORICAL_CLASSIFICATION, HISTORY_CUTOFF_RULE],
    ).fetchone()[0]
    if invalid:
        raise HistoricalMinutesExperimentError(f"{phase} candidate validation failed")
    m0_mismatch = connection.execute(
        f"""SELECT count(*) FROM {phase}_candidate_player c
            JOIN {phase}_base_player b USING(season,target_gameweek,element_id)
            WHERE c.candidate='M0' AND (
              c.gameweek_expected_minutes IS DISTINCT FROM b.gameweek_expected_minutes_for_evaluation
              OR c.gameweek_appearance_xfp IS DISTINCT FROM b.gameweek_appearance_xfp_for_evaluation
              OR c.gameweek_xfp IS DISTINCT FROM b.gameweek_xfp_v01)"""
    ).fetchone()[0]
    if m0_mismatch:
        raise HistoricalMinutesExperimentError(
            f"{phase} M0 does not exactly reproduce frozen v0.1 in {m0_mismatch} rows"
        )
    return f"{phase}_candidate_fixture", f"{phase}_candidate_player"


def _fetch_dicts(connection: duckdb.DuckDBPyConnection, table: str) -> list[dict[str, Any]]:
    # DuckDB can write TIMESTAMPTZ without an optional Python timezone package;
    # metrics do not need the timestamp value materialized as Python objects.
    cursor = connection.execute(f'SELECT * EXCLUDE(target_deadline) FROM "{table}"')
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]


def _metric(
    rows: Sequence[dict[str, Any]], prediction: str, actual: str
) -> dict[str, Any]:
    pairs = [
        (float(row[prediction]), float(row[actual]))
        for row in rows if row[prediction] is not None and row[actual] is not None
    ]
    result: dict[str, Any] = {
        "n_eligible": len(rows), "n_complete_pairs": len(pairs),
        "missing_prediction": sum(row[prediction] is None for row in rows),
        "missing_actual": sum(row[actual] is None for row in rows),
        "coverage_pct": 100.0 * len(pairs) / len(rows) if rows else None,
        "mae": None, "rmse": None, "bias": None, "spearman": None,
    }
    if not pairs:
        return result
    predictions = [item[0] for item in pairs]
    actuals = [item[1] for item in pairs]
    errors = [left - right for left, right in pairs]
    result.update(
        mae=sum(abs(error) for error in errors) / len(errors),
        rmse=math.sqrt(sum(error * error for error in errors) / len(errors)),
        bias=sum(errors) / len(errors),
        spearman=_spearman(predictions, actuals),
    )
    return result


TARGET_FIELDS = {
    "minutes": ("gameweek_expected_minutes", "actual_minutes"),
    "appearance": ("gameweek_appearance_xfp", "actual_appearance_points"),
    "modeled_xfp": ("gameweek_xfp", "actual_modeled_points"),
}


def _metric_tuple(
    *, phase: str, season: str, candidate: str, population: str,
    target: str, metric: dict[str, Any],
) -> tuple[Any, ...]:
    return (
        phase, season, candidate, population, target,
        metric["n_eligible"], metric["n_complete_pairs"],
        metric["missing_prediction"], metric["missing_actual"],
        metric["coverage_pct"], metric["mae"], metric["rmse"],
        metric["bias"], metric["spearman"],
    )


def _phase_metrics(
    rows: Sequence[dict[str, Any]], *, phase: str, season: str,
    candidates: Sequence[str], population: str = "all",
) -> list[tuple[Any, ...]]:
    output = []
    for candidate in candidates:
        candidate_rows = [row for row in rows if row["candidate"] == candidate]
        for target, (prediction, actual) in TARGET_FIELDS.items():
            output.append(
                _metric_tuple(
                    phase=phase, season=season, candidate=candidate,
                    population=population, target=target,
                    metric=_metric(candidate_rows, prediction, actual),
                )
            )
    return output


def _common_pair_metrics(
    rows: Sequence[dict[str, Any]], *, phase: str, season: str,
    candidates: Sequence[str],
) -> list[tuple[Any, ...]]:
    by_candidate = {
        candidate: {
            (row["target_gameweek"], row["element_id"]): row
            for row in rows if row["candidate"] == candidate
        }
        for candidate in candidates
    }
    output: list[tuple[Any, ...]] = []
    baseline = by_candidate["M0"]
    for candidate in candidates:
        if candidate == "M0":
            continue
        compared = by_candidate[candidate]
        for target, (prediction, actual) in TARGET_FIELDS.items():
            common_keys = [
                key for key, baseline_row in baseline.items()
                if key in compared and baseline_row[prediction] is not None
                and compared[key][prediction] is not None
                and baseline_row[actual] is not None and compared[key][actual] is not None
            ]
            for predictor, source in (("M0", baseline), (candidate, compared)):
                common_rows = [source[key] for key in common_keys]
                metric = _metric(common_rows, prediction, actual)
                output.append(
                    (
                        phase, season, candidate, predictor, target, len(common_keys),
                        metric["mae"], metric["rmse"], metric["bias"], metric["spearman"],
                    )
                )
    return output


def _ranking_rows(
    rows: Sequence[dict[str, Any]], *, phase: str, season: str,
    candidates: Sequence[str],
) -> list[tuple[Any, ...]]:
    output: list[tuple[Any, ...]] = []
    for candidate in candidates:
        gameweeks: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            if row["candidate"] == candidate and row["gameweek_xfp"] is not None \
                    and row["actual_modeled_points"] is not None:
                gameweeks.setdefault(row["target_gameweek"], []).append(row)
        for top_n in TOP_N_VALUES:
            summarized: list[float] = []
            for gameweek, members in sorted(gameweeks.items()):
                enough = len(members) >= top_n
                overlap = None
                overlap_pct = None
                if enough:
                    predicted = sorted(
                        members, key=lambda row: (-float(row["gameweek_xfp"]), row["element_id"])
                    )[:top_n]
                    actual = sorted(
                        members, key=lambda row: (-float(row["actual_modeled_points"]), row["element_id"])
                    )[:top_n]
                    overlap = float(len(
                        {row["element_id"] for row in predicted}
                        & {row["element_id"] for row in actual}
                    ))
                    overlap_pct = 100.0 * overlap / top_n
                    summarized.append(overlap_pct)
                output.append(
                    (phase, season, candidate, "gameweek", gameweek, top_n,
                     len(members), enough, overlap, overlap_pct, 1 if enough else 0,
                     "score_desc_then_element_id_asc_strict_n")
                )
            output.append(
                (phase, season, candidate, "summary", None, top_n, None,
                 bool(summarized), None,
                 sum(summarized) / len(summarized) if summarized else None,
                 len(summarized), "score_desc_then_element_id_asc_strict_n")
            )
    return output


def _reduction(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None or baseline == 0:
        return None
    return 100.0 * (baseline - candidate) / baseline


def _metric_lookup(
    rows: Sequence[tuple[Any, ...]], *, comparison: str, predictor: str, target: str
) -> dict[str, Any]:
    for row in rows:
        if row[2] == comparison and row[3] == predictor and row[4] == target:
            return {"n": row[5], "mae": row[6], "rmse": row[7], "bias": row[8], "spearman": row[9]}
    raise HistoricalMinutesExperimentError(
        f"missing common-pair metric for {comparison}/{predictor}/{target}"
    )


def _coverage_lookup(
    rows: Sequence[tuple[Any, ...]], *, candidate: str, target: str
) -> float:
    for row in rows:
        if row[2] == candidate and row[4] == target:
            return float(row[9])
    raise HistoricalMinutesExperimentError(f"missing coverage for {candidate}/{target}")


def select_development_winner(
    development_metrics: Sequence[tuple[Any, ...]],
    common_pair_metrics: Sequence[tuple[Any, ...]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Apply the preregistered gate using development rows only."""
    if any(row[0] != "development" or row[1] != DEVELOPMENT_SEASON for row in development_metrics):
        raise HistoricalMinutesExperimentError("holdout or non-development metrics entered selection")
    if any(row[0] != "development" or row[1] != DEVELOPMENT_SEASON for row in common_pair_metrics):
        raise HistoricalMinutesExperimentError("holdout common-pair metrics entered selection")
    records: list[dict[str, Any]] = []
    for candidate in CANDIDATES[1:]:
        minutes_m0 = _metric_lookup(common_pair_metrics, comparison=candidate, predictor="M0", target="minutes")
        minutes_candidate = _metric_lookup(common_pair_metrics, comparison=candidate, predictor=candidate, target="minutes")
        modeled_m0 = _metric_lookup(common_pair_metrics, comparison=candidate, predictor="M0", target="modeled_xfp")
        modeled_candidate = _metric_lookup(common_pair_metrics, comparison=candidate, predictor=candidate, target="modeled_xfp")
        coverage_drop = _coverage_lookup(development_metrics, candidate="M0", target="modeled_xfp") - _coverage_lookup(
            development_metrics, candidate=candidate, target="modeled_xfp"
        )
        record = {
            "candidate": candidate,
            "minutes_mae_reduction_pct": _reduction(minutes_m0["mae"], minutes_candidate["mae"]),
            "minutes_rmse_reduction_pct": _reduction(minutes_m0["rmse"], minutes_candidate["rmse"]),
            "modeled_xfp_mae_reduction_pct": _reduction(modeled_m0["mae"], modeled_candidate["mae"]),
            "modeled_xfp_rmse": modeled_candidate["rmse"],
            "minutes_mae": minutes_candidate["mae"],
            "coverage_drop_pp": coverage_drop,
        }
        record["development_qualifies"] = (
            record["minutes_mae_reduction_pct"] is not None
            and record["minutes_mae_reduction_pct"] >= 5.0
            and record["minutes_rmse_reduction_pct"] >= 3.0
            and record["modeled_xfp_mae_reduction_pct"] >= 2.0
            and coverage_drop <= 1.0
        )
        records.append(record)
    qualifying = [record for record in records if record["development_qualifies"]]
    simplicity = {"M1": 0, "M2": 1, "M3": 2}
    qualifying.sort(
        key=lambda record: (
            -record["modeled_xfp_mae_reduction_pct"],
            record["modeled_xfp_rmse"], record["minutes_mae"], simplicity[record["candidate"]],
        )
    )
    return (qualifying[0]["candidate"] if qualifying else None), records


def _holdout_decision(
    holdout_metrics: Sequence[tuple[Any, ...]],
    common_pair_metrics: Sequence[tuple[Any, ...]],
    winner: str,
) -> dict[str, Any]:
    minutes_m0 = _metric_lookup(common_pair_metrics, comparison=winner, predictor="M0", target="minutes")
    minutes_winner = _metric_lookup(common_pair_metrics, comparison=winner, predictor=winner, target="minutes")
    appearance_m0 = _metric_lookup(common_pair_metrics, comparison=winner, predictor="M0", target="appearance")
    appearance_winner = _metric_lookup(common_pair_metrics, comparison=winner, predictor=winner, target="appearance")
    modeled_m0 = _metric_lookup(common_pair_metrics, comparison=winner, predictor="M0", target="modeled_xfp")
    modeled_winner = _metric_lookup(common_pair_metrics, comparison=winner, predictor=winner, target="modeled_xfp")
    coverage_drop = _coverage_lookup(holdout_metrics, candidate="M0", target="modeled_xfp") - _coverage_lookup(
        holdout_metrics, candidate=winner, target="modeled_xfp"
    )
    result = {
        "holdout_minutes_mae_reduction_pct": _reduction(minutes_m0["mae"], minutes_winner["mae"]),
        "holdout_minutes_rmse_reduction_pct": _reduction(minutes_m0["rmse"], minutes_winner["rmse"]),
        "holdout_modeled_xfp_mae_reduction_pct": _reduction(modeled_m0["mae"], modeled_winner["mae"]),
        "holdout_appearance_mae_change": appearance_winner["mae"] - appearance_m0["mae"],
        "holdout_modeled_spearman_drop": modeled_m0["spearman"] - modeled_winner["spearman"],
        "holdout_coverage_drop_pp": coverage_drop,
    }
    result["holdout_passed"] = (
        result["holdout_minutes_mae_reduction_pct"] >= 5.0
        and result["holdout_minutes_rmse_reduction_pct"] >= 3.0
        and result["holdout_modeled_xfp_mae_reduction_pct"] >= 2.0
        and result["holdout_appearance_mae_change"] <= 0.0
        and result["holdout_modeled_spearman_drop"] <= 0.01
        and result["holdout_coverage_drop_pp"] <= 1.0
    )
    result["oracle_mae_improvement_captured_pct"] = (
        100.0 * result["holdout_modeled_xfp_mae_reduction_pct"]
        / ORACLE_MODELED_MAE_REDUCTION_PCT
    )
    return result


def _holdout_candidate_set(winner: str) -> tuple[str, str]:
    """Permit exactly frozen M0 and the one development winner into holdout."""
    if winner not in CANDIDATES[1:]:
        raise HistoricalMinutesExperimentError("holdout requires one valid development winner")
    return "M0", winner


def _population(row: dict[str, Any], population: str) -> bool:
    if population == "all":
        return True
    if population == "normal_single":
        return row["fixture_count"] == 1
    if population == "double":
        return row["fixture_count"] > 1
    if population == "available":
        return row["availability_band"] == "available"
    if population == "doubtful":
        return row["availability_band"] == "doubtful_or_chance_limited"
    if population == "previous_90":
        return row["previous_gameweek_minutes_uncapped"] == 90
    if population == "previous_0":
        return row["previous_gameweek_minutes_uncapped"] == 0
    if population == "stable":
        return (
            row["attacking_rate_available"] is True
            and row["prior_total_minutes"] >= 450
            and row["baseline_expected_minutes"] is not None
            and float(row["baseline_expected_minutes"]) >= 60
            and row["fixture_count"] == 1
        )
    raise ValueError(population)


def _diagnostic_metrics(
    rows: Sequence[dict[str, Any]], *, phase: str, season: str,
    candidates: Sequence[str],
) -> list[tuple[Any, ...]]:
    output: list[tuple[Any, ...]] = []
    for population in (
        "all", "normal_single", "double", "available", "doubtful",
        "previous_90", "previous_0", "stable",
    ):
        subset = [row for row in rows if _population(row, population)]
        output.extend(
            _phase_metrics(
                subset, phase=phase, season=season,
                candidates=candidates, population=population,
            )
        )
    return output


def _selection_rows(
    development: Sequence[dict[str, Any]], winner: str | None,
    holdout: dict[str, Any] | None,
) -> list[tuple[Any, ...]]:
    output = []
    for record in development:
        selected = record["candidate"] == winner
        held = holdout if selected and holdout else {}
        output.append(
            (
                record["candidate"], record["development_qualifies"],
                record["minutes_mae_reduction_pct"], record["minutes_rmse_reduction_pct"],
                record["modeled_xfp_mae_reduction_pct"], record["modeled_xfp_rmse"],
                record["minutes_mae"], record["coverage_drop_pp"], selected,
                held.get("holdout_passed"), held.get("holdout_minutes_mae_reduction_pct"),
                held.get("holdout_minutes_rmse_reduction_pct"),
                held.get("holdout_modeled_xfp_mae_reduction_pct"),
                held.get("holdout_appearance_mae_change"),
                held.get("holdout_modeled_spearman_drop"),
                held.get("holdout_coverage_drop_pp"),
                held.get("oracle_mae_improvement_captured_pct"),
            )
        )
    return output


def _write_outputs(
    connection: duckdb.DuckDBPyConnection, *, experiment_root: Path,
    manifest_base: dict[str, Any], tables: Sequence[str],
) -> tuple[Path, Path, list[dict[str, Any]]]:
    final = experiment_root / EXPERIMENT_VERSION
    if final.exists():
        raise HistoricalMinutesExperimentOutputExistsError(
            f"experiment output already exists and will not be overwritten: {final}"
        )
    stage = experiment_root / f".{EXPERIMENT_VERSION}.{uuid.uuid4().hex}.tmp"
    stage.mkdir(parents=True, exist_ok=False)
    outputs: list[dict[str, Any]] = []
    try:
        for table in tables:
            path = stage / f"{table}.parquet"
            connection.execute(
                f'COPY "{table}" TO ? (FORMAT PARQUET, COMPRESSION ZSTD)', [str(path)]
            )
            outputs.append(
                {"path": path.name,
                 "rows": connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0],
                 "bytes": path.stat().st_size, "sha256": _sha256(path)}
            )
        manifest = {**manifest_base, "outputs": outputs}
        _write_exclusive(
            stage / "experiment_manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        experiment_root.mkdir(parents=True, exist_ok=True)
        try:
            stage.rename(final)
        except FileExistsError as exc:
            raise HistoricalMinutesExperimentOutputExistsError(
                f"experiment output already exists and will not be overwritten: {final}"
            ) from exc
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return final, final / "experiment_manifest.json", outputs


def run_historical_minutes_experiment(
    *, historical_clean_root: Path = Path("data/historical/clean"),
    baseline_root: Path = Path("data/historical/backtests"),
    experiment_root: Path = Path("data/historical/experiments"),
    clock=lambda: datetime.now(timezone.utc),
) -> HistoricalMinutesExperimentResult:
    """Run the preregistered development/holdout experiment exactly once."""
    output_directory = experiment_root / EXPERIMENT_VERSION
    if output_directory.exists():
        raise HistoricalMinutesExperimentOutputExistsError(
            f"experiment output already exists and will not be overwritten: {output_directory}"
        )
    paths, protected_hashes = _validate_inputs(
        historical_clean_root=historical_clean_root, baseline_root=baseline_root
    )
    connection = duckdb.connect(":memory:")
    try:
        # Development is deliberately built and selected before any holdout table is loaded.
        _load_phase_inputs(
            connection, phase="development", season=DEVELOPMENT_SEASON,
            paths=paths, candidates=CANDIDATES,
        )
        development_fixture, development_player = _create_phase_predictions(
            connection, phase="development", season=DEVELOPMENT_SEASON
        )
        development_rows = _fetch_dicts(connection, development_player)
        development_metrics = _phase_metrics(
            development_rows, phase="development", season=DEVELOPMENT_SEASON,
            candidates=CANDIDATES,
        )
        development_common = _common_pair_metrics(
            development_rows, phase="development", season=DEVELOPMENT_SEASON,
            candidates=CANDIDATES,
        )
        development_ranking = _ranking_rows(
            development_rows, phase="development", season=DEVELOPMENT_SEASON,
            candidates=CANDIDATES,
        )
        winner, development_selection = select_development_winner(
            development_metrics, development_common
        )

        holdout_rows: list[dict[str, Any]] = []
        holdout_metrics: list[tuple[Any, ...]] = []
        holdout_common: list[tuple[Any, ...]] = []
        holdout_ranking: list[tuple[Any, ...]] = []
        holdout_result: dict[str, Any] | None = None
        tables = [development_fixture, development_player]
        if winner is not None:
            holdout_candidates = _holdout_candidate_set(winner)
            _load_phase_inputs(
                connection, phase="holdout", season=HOLDOUT_SEASON,
                paths=paths, candidates=holdout_candidates,
            )
            holdout_fixture, holdout_player = _create_phase_predictions(
                connection, phase="holdout", season=HOLDOUT_SEASON
            )
            holdout_rows = _fetch_dicts(connection, holdout_player)
            holdout_metrics = _phase_metrics(
                holdout_rows, phase="holdout", season=HOLDOUT_SEASON,
                candidates=holdout_candidates,
            )
            holdout_common = _common_pair_metrics(
                holdout_rows, phase="holdout", season=HOLDOUT_SEASON,
                candidates=holdout_candidates,
            )
            holdout_ranking = _ranking_rows(
                holdout_rows, phase="holdout", season=HOLDOUT_SEASON,
                candidates=holdout_candidates,
            )
            holdout_result = _holdout_decision(
                holdout_metrics, holdout_common, winner
            )
            connection.execute(
                f"""CREATE TABLE candidate_fixture_predictions AS
                    SELECT * FROM {development_fixture} UNION ALL SELECT * FROM {holdout_fixture}"""
            )
            connection.execute(
                f"""CREATE TABLE candidate_player_gameweek AS
                    SELECT * FROM {development_player} UNION ALL SELECT * FROM {holdout_player}"""
            )
        else:
            connection.execute(
                f"CREATE TABLE candidate_fixture_predictions AS SELECT * FROM {development_fixture}"
            )
            connection.execute(
                f"CREATE TABLE candidate_player_gameweek AS SELECT * FROM {development_player}"
            )

        _create_rows_table(connection, "development_metrics", METRIC_SCHEMA, development_metrics)
        _create_rows_table(
            connection, "development_common_pair_metrics", COMMON_PAIR_SCHEMA, development_common
        )
        _create_rows_table(connection, "development_ranking", RANKING_SCHEMA, development_ranking)
        output_tables = [
            "candidate_fixture_predictions", "candidate_player_gameweek",
            "development_metrics", "development_common_pair_metrics",
            "development_ranking",
        ]
        if winner is not None:
            _create_rows_table(connection, "holdout_metrics", METRIC_SCHEMA, holdout_metrics)
            _create_rows_table(
                connection, "holdout_common_pair_metrics", COMMON_PAIR_SCHEMA, holdout_common
            )
            _create_rows_table(connection, "holdout_ranking", RANKING_SCHEMA, holdout_ranking)
            output_tables.extend(
                ["holdout_metrics", "holdout_common_pair_metrics", "holdout_ranking"]
            )
        diagnostic_rows = _diagnostic_metrics(
            development_rows, phase="development", season=DEVELOPMENT_SEASON,
            candidates=("M0", winner) if winner else ("M0",),
        )
        if winner:
            diagnostic_rows.extend(
                _diagnostic_metrics(
                    holdout_rows, phase="holdout", season=HOLDOUT_SEASON,
                    candidates=("M0", winner),
                )
            )
        _create_rows_table(connection, "diagnostic_metrics", METRIC_SCHEMA, diagnostic_rows)
        _create_rows_table(
            connection, "selection_decision", SELECTION_SCHEMA,
            _selection_rows(development_selection, winner, holdout_result),
        )
        output_tables.extend(["diagnostic_metrics", "selection_decision"])

        if any(_sha256(path) != digest for path, digest in protected_hashes.items()):
            raise HistoricalMinutesExperimentError("an immutable input changed during the experiment")
        holdout_passed = holdout_result["holdout_passed"] if holdout_result else None
        final_decision = (
            "PROMOTE CANDIDATE TO xFP v0.2 DESIGN"
            if holdout_passed else "DO NOT PROMOTE — KEEP v0.1 MINUTES"
        )
        manifest_base = {
            "status": "complete",
            "experiment_version": EXPERIMENT_VERSION,
            "historical_classification": HISTORICAL_CLASSIFICATION,
            "model_formula_frozen": "xfp_v01",
            "live_model_modified": False,
            "development_season": DEVELOPMENT_SEASON,
            "holdout_season": HOLDOUT_SEASON,
            "target_gameweeks": list(TARGET_GAMEWEEKS),
            "candidate_definitions": {
                "M0": "previous calendar-GW minutes persistence",
                "M1": "mean of observed minutes in prior 3 calendar FPL GWs",
                "M2": "mean of observed minutes in prior 5 calendar FPL GWs",
                "M3": "0.60/0.30/0.10 recency weights over prior 3 calendar FPL GWs, renormalized over observed GWs",
            },
            "observation_policy": (
                "A real fixture with zero minutes is observed zero. A verified blank or player "
                "not in the historical universe is missing, not zero. Eligible DGW fixture "
                "minutes are summed to player-GW before the window. At least one observed GW "
                "is required. Candidate fixture minutes are capped to 0-90 before the frozen "
                "availability hard gate."
            ),
            "temporal_cutoff_rule": HISTORY_CUTOFF_RULE,
            "selection_population": "common complete M0/candidate/actual pairs; coverage checked separately",
            "development_thresholds": DEVELOPMENT_THRESHOLDS,
            "development_tie_breakers": [
                "largest modeled xFP MAE reduction", "lower modeled xFP RMSE",
                "lower minutes MAE", "simpler M1 then M2 then M3",
            ],
            "holdout_thresholds": HOLDOUT_THRESHOLDS,
            "development_winner": winner,
            "holdout_evaluated": winner is not None,
            "holdout_passed": holdout_passed,
            "final_decision": final_decision,
            "oracle_reference": {
                "evaluation_only": True,
                "modeled_mae_reduction_pct": ORACLE_MODELED_MAE_REDUCTION_PCT,
                "modeled_rmse_reduction_pct": ORACLE_MODELED_RMSE_REDUCTION_PCT,
                "holdout_oracle_mae_improvement_captured_pct": (
                    holdout_result.get("oracle_mae_improvement_captured_pct")
                    if holdout_result else None
                ),
            },
            "generation_timestamp": _iso_utc(clock()),
            "immutable_inputs": [
                {"path": str(path), "sha256": digest}
                for path, digest in sorted(protected_hashes.items(), key=lambda item: str(item[0]))
            ],
        }
        directory, manifest_path, _ = _write_outputs(
            connection, experiment_root=experiment_root,
            manifest_base=manifest_base, tables=output_tables,
        )
    except duckdb.Error as exc:
        raise HistoricalMinutesExperimentError(
            f"historical expected-minutes experiment failed: {exc}"
        ) from exc
    finally:
        connection.close()
    return HistoricalMinutesExperimentResult(
        directory=directory, manifest_path=manifest_path,
        development_winner=winner, holdout_passed=holdout_passed,
        final_decision=final_decision,
    )
