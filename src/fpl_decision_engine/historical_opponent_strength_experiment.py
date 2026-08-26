"""Preregistered causal opponent-strength experiment for frozen xFP v0.1."""

from __future__ import annotations

import json
import math
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
from .historical_calibration_experiment import _validate_experiment_provenance
from .historical_minutes_experiment import (
    COMMON_PAIR_SCHEMA,
    DEVELOPMENT_SEASON,
    HOLDOUT_SEASON,
    METRIC_SCHEMA,
    TARGET_GAMEWEEKS,
    _create_rows_table,
    _iso_utc,
    _metric,
    _reduction,
    _sha256,
    _validate_inputs,
    _write_exclusive,
)
from .transform import TransformationError


EXPERIMENT_VERSION = "opponent-strength-v02-experiment-v1"
CANDIDATES = ("F0", "F1", "F2")
ROLLING_MATCHES = 6
MINIMUM_MATCHES = 3
FACTOR_MIN = 0.7
FACTOR_MAX = 1.3
TASK_009_VERSION = "minutes-v02-experiment-v1"
TASK_010_VERSION = "attacking-rate-v02-experiment-v1"
TASK_011_VERSION = "calibration-v02-experiment-v1"

DEVELOPMENT_THRESHOLDS = {
    "minimum_attacking_spearman_improvement": 0.01,
    "maximum_goal_spearman_decline": 0.005,
    "maximum_assist_spearman_decline": 0.005,
    "minimum_attacking_mae_improvement_pct": 1.0,
    "maximum_modeled_mae_worsening_pct": 0.5,
    "maximum_modeled_rmse_worsening_pct": 1.0,
    "maximum_absolute_modeled_bias_worsening": 0.02,
    "maximum_coverage_drop_pp": 1.0,
}
HOLDOUT_THRESHOLDS = {
    "minimum_attacking_spearman_improvement": 0.005,
    "maximum_goal_spearman_decline": 0.005,
    "maximum_assist_spearman_decline": 0.005,
    "minimum_attacking_mae_improvement_pct": 0.0,
    "maximum_modeled_mae_worsening_pct": 0.5,
    "maximum_modeled_rmse_worsening_pct": 1.0,
    "maximum_absolute_modeled_bias_worsening": 0.02,
    "maximum_coverage_drop_pp": 1.0,
}


class HistoricalOpponentStrengthExperimentError(TransformationError):
    """Raised when the opponent-strength experiment cannot run safely."""


class HistoricalOpponentStrengthExperimentOutputExistsError(
    HistoricalOpponentStrengthExperimentError
):
    """Raised rather than overwriting an immutable experiment."""


@dataclass(frozen=True)
class HistoricalOpponentStrengthExperimentResult:
    directory: Path
    manifest_path: Path
    development_winner: str | None
    holdout_passed: bool | None
    final_decision: str


TARGET_FIELDS = {
    "goal": ("gameweek_goal_xfp", "actual_goal_points"),
    "assist": ("gameweek_assist_xfp", "actual_assist_points"),
    "attacking_combined": ("gameweek_attacking_xfp", "actual_attacking_points"),
    "modeled_xfp": ("gameweek_xfp", "actual_modeled_points"),
}

SELECTION_SCHEMA = (
    ("candidate", "VARCHAR"), ("development_qualifies", "BOOLEAN"),
    ("attacking_spearman_improvement", "DOUBLE"),
    ("goal_spearman_decline", "DOUBLE"),
    ("assist_spearman_decline", "DOUBLE"),
    ("attacking_mae_improvement_pct", "DOUBLE"),
    ("modeled_mae_worsening_pct", "DOUBLE"),
    ("modeled_rmse_worsening_pct", "DOUBLE"),
    ("absolute_modeled_bias_worsening", "DOUBLE"),
    ("coverage_drop_pp", "DOUBLE"),
    ("attacking_mae", "DOUBLE"), ("modeled_mae", "DOUBLE"),
    ("selected_for_holdout", "BOOLEAN"), ("holdout_passed", "BOOLEAN"),
    ("holdout_attacking_spearman_improvement", "DOUBLE"),
    ("holdout_goal_spearman_decline", "DOUBLE"),
    ("holdout_assist_spearman_decline", "DOUBLE"),
    ("holdout_attacking_mae_improvement_pct", "DOUBLE"),
    ("holdout_modeled_mae_worsening_pct", "DOUBLE"),
    ("holdout_modeled_rmse_worsening_pct", "DOUBLE"),
    ("holdout_absolute_modeled_bias_worsening", "DOUBLE"),
    ("holdout_coverage_drop_pp", "DOUBLE"),
)

FACTOR_DISTRIBUTION_SCHEMA = (
    ("phase", "VARCHAR"), ("season", "VARCHAR"),
    ("candidate", "VARCHAR"), ("n_contexts", "BIGINT"),
    ("raw_min", "DOUBLE"), ("raw_p05", "DOUBLE"),
    ("raw_p25", "DOUBLE"), ("raw_median", "DOUBLE"),
    ("raw_p75", "DOUBLE"), ("raw_p95", "DOUBLE"),
    ("raw_max", "DOUBLE"), ("clipped_min", "DOUBLE"),
    ("clipped_mean", "DOUBLE"), ("clipped_max", "DOUBLE"),
    ("pct_clipped_low", "DOUBLE"), ("pct_clipped_high", "DOUBLE"),
    ("pct_neutral_insufficient_history", "DOUBLE"),
)

FACTOR_OPPONENT_SCHEMA = (
    ("phase", "VARCHAR"), ("season", "VARCHAR"),
    ("candidate", "VARCHAR"), ("opponent_team_id", "INTEGER"),
    ("opponent_team_name", "VARCHAR"), ("n_contexts", "BIGINT"),
    ("n_neutral", "BIGINT"), ("mean_raw_factor", "DOUBLE"),
    ("mean_clipped_factor", "DOUBLE"), ("min_clipped_factor", "DOUBLE"),
    ("max_clipped_factor", "DOUBLE"),
)

FACTOR_CORRELATION_SCHEMA = (
    ("phase", "VARCHAR"), ("season", "VARCHAR"),
    ("n_contexts", "BIGINT"), ("pearson", "DOUBLE"),
    ("spearman", "DOUBLE"),
)

FACTOR_BAND_RESIDUAL_SCHEMA = (
    ("phase", "VARCHAR"), ("season", "VARCHAR"),
    ("candidate", "VARCHAR"), ("factor_band", "VARCHAR"),
    ("n", "BIGINT"), ("mean_factor", "DOUBLE"),
    ("f0_mean_residual_actual_minus_prediction", "DOUBLE"),
    ("candidate_mean_residual_actual_minus_prediction", "DOUBLE"),
    ("f0_mae", "DOUBLE"), ("candidate_mae", "DOUBLE"),
    ("f0_rmse", "DOUBLE"), ("candidate_rmse", "DOUBLE"),
)

WITHIN_PLAYER_SCHEMA = (
    ("phase", "VARCHAR"), ("season", "VARCHAR"),
    ("candidate", "VARCHAR"), ("element_id", "BIGINT"),
    ("n", "BIGINT"), ("factor_min", "DOUBLE"),
    ("factor_max", "DOUBLE"), ("factor_range", "DOUBLE"),
    ("pearson_factor_vs_f0_residual", "DOUBLE"),
    ("spearman_factor_vs_f0_residual", "DOUBLE"),
)

RATE_TIER_SCHEMA = (
    ("phase", "VARCHAR"), ("season", "VARCHAR"),
    ("candidate", "VARCHAR"), ("raw_attacking_rate_tier", "VARCHAR"),
    ("n", "BIGINT"), ("mean_factor", "DOUBLE"),
    ("mean_f0_residual_actual_minus_prediction", "DOUBLE"),
    ("pearson_factor_vs_f0_residual", "DOUBLE"),
    ("spearman_factor_vs_f0_residual", "DOUBLE"),
)


def clipped_factor(raw_factor: float) -> float:
    return min(FACTOR_MAX, max(FACTOR_MIN, float(raw_factor)))


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    mean_left = math.fsum(left) / len(left)
    mean_right = math.fsum(right) / len(right)
    covariance = math.fsum(
        (x - mean_left) * (y - mean_right) for x, y in zip(left, right)
    )
    left_ss = math.fsum((x - mean_left) ** 2 for x in left)
    right_ss = math.fsum((y - mean_right) ** 2 for y in right)
    if left_ss == 0 or right_ss == 0:
        return None
    return covariance / math.sqrt(left_ss * right_ss)


def _fetch_rows(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    *,
    exclude: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    excluded = f" EXCLUDE ({', '.join(exclude)})" if exclude else ""
    cursor = connection.execute(f"SELECT *{excluded} FROM {table}")
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _validate_all_inputs(
    *, historical_clean_root: Path, baseline_root: Path, experiment_root: Path,
) -> tuple[dict[str, Path], dict[Path, str], dict[str, str]]:
    try:
        paths, protected = _validate_inputs(
            historical_clean_root=historical_clean_root, baseline_root=baseline_root
        )
    except TransformationError as exc:
        raise HistoricalOpponentStrengthExperimentError(str(exc)) from exc
    baseline_manifest = json.loads(paths["baseline_manifest"].read_bytes())
    baseline_outputs = {
        entry["path"]: entry["sha256"] for entry in baseline_manifest.get("outputs", [])
    }
    actual_path = baseline_root / "xfp-v01-baseline-v1" / "fixture_actuals.parquet"
    expected = baseline_outputs.get("fixture_actuals.parquet")
    if not actual_path.is_file() or not expected or _sha256(actual_path) != expected:
        raise HistoricalOpponentStrengthExperimentError(
            "frozen fixture-actual input is missing or changed"
        )
    paths["baseline_fixture_actuals"] = actual_path
    protected[actual_path] = expected
    try:
        for version in (TASK_009_VERSION, TASK_010_VERSION, TASK_011_VERSION):
            protected.update(
                _validate_experiment_provenance(experiment_root / version, version)
            )
    except TransformationError as exc:
        raise HistoricalOpponentStrengthExperimentError(str(exc)) from exc
    provenance = {
        "historical_manifest_sha256": protected[paths["historical_manifest"]],
        "baseline_manifest_sha256": protected[paths["baseline_manifest"]],
        "task_009_manifest_sha256": protected[
            experiment_root / TASK_009_VERSION / "experiment_manifest.json"
        ],
        "task_010_manifest_sha256": protected[
            experiment_root / TASK_010_VERSION / "experiment_manifest.json"
        ],
        "task_011_manifest_sha256": protected[
            experiment_root / TASK_011_VERSION / "experiment_manifest.json"
        ],
    }
    return paths, protected, provenance


def _load_phase_inputs(
    connection: duckdb.DuckDBPyConnection, *, phase: str, season: str,
    paths: dict[str, Path], candidates: Sequence[str],
) -> None:
    if not candidates or any(candidate not in CANDIDATES for candidate in candidates):
        raise HistoricalOpponentStrengthExperimentError("unknown opponent candidate")
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_base_fixture AS
            SELECT * FROM read_parquet(?)
            WHERE season=? AND target_gameweek BETWEEN 2 AND 38""",
        [str(paths["baseline_fixture"]), season],
    )
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_base_player AS
            SELECT * FROM read_parquet(?)
            WHERE season=? AND target_gameweek BETWEEN 2 AND 38""",
        [str(paths["baseline_player_gameweek"]), season],
    )
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_fixture_actuals AS
            SELECT * FROM read_parquet(?)
            WHERE season=? AND target_gameweek BETWEEN 2 AND 38""",
        [str(paths["baseline_fixture_actuals"]), season],
    )
    connection.execute(
        f"CREATE OR REPLACE TABLE {phase}_history AS SELECT * FROM read_parquet(?) WHERE season=?",
        [str(paths[f"{season}_player_fixture"]), season],
    )
    connection.execute(
        f"CREATE OR REPLACE TABLE {phase}_fixtures AS SELECT * FROM read_parquet(?) WHERE season=?",
        [str(paths[f"{season}_fixtures"]), season],
    )
    values = ",".join(f"('{candidate}')" for candidate in candidates)
    connection.execute(
        f"CREATE OR REPLACE TABLE {phase}_candidate_set(candidate) AS VALUES {values}"
    )


def _create_team_defense(
    connection: duckdb.DuckDBPyConnection, *, phase: str,
    expected_team_match_rows: int | None = 760,
) -> str:
    duplicate = connection.execute(
        f"""SELECT count(*)-count(DISTINCT (season,element_id,fixture_id))
            FROM {phase}_history"""
    ).fetchone()[0]
    null_xg = connection.execute(
        f"SELECT count(*) FROM {phase}_history WHERE xg IS NULL"
    ).fetchone()[0]
    invalid_fixtures = connection.execute(
        f"""SELECT count(*) FROM {phase}_fixtures
            WHERE NOT finished OR home_score IS NULL OR away_score IS NULL"""
    ).fetchone()[0]
    if duplicate or null_xg or invalid_fixtures:
        raise HistoricalOpponentStrengthExperimentError(
            f"{phase} team-defence source invalid: duplicates={duplicate}, "
            f"null_xg={null_xg}, invalid_fixtures={invalid_fixtures}"
        )
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_team_match_defense AS
            WITH team_xg AS (
              SELECT season,fixture_id,historical_team_id attacking_team_id,
                sum(xg)::DOUBLE team_xg
              FROM {phase}_history GROUP BY 1,2,3
            )
            SELECT f.season,f.fixture_id,f.gameweek,
              f.home_team_id defending_team_id,f.home_team_name defending_team_name,
              f.away_team_id attacking_team_id,f.away_team_name attacking_team_name,
              f.kickoff_time,f.away_score::DOUBLE team_goals_conceded,
              x.team_xg team_xgc,
              'fixture_score_away_score'::VARCHAR goals_conceded_construction,
              'sum_opponent_player_fixture_xg'::VARCHAR xgc_construction,
              f.fixture_assignment_context
            FROM {phase}_fixtures f JOIN team_xg x
              ON x.season=f.season AND x.fixture_id=f.fixture_id
             AND x.attacking_team_id=f.away_team_id
            UNION ALL
            SELECT f.season,f.fixture_id,f.gameweek,
              f.away_team_id,f.away_team_name,f.home_team_id,f.home_team_name,
              f.kickoff_time,f.home_score::DOUBLE,x.team_xg,
              'fixture_score_home_score','sum_opponent_player_fixture_xg',
              f.fixture_assignment_context
            FROM {phase}_fixtures f JOIN team_xg x
              ON x.season=f.season AND x.fixture_id=f.fixture_id
             AND x.attacking_team_id=f.home_team_id"""
    )
    invalid = connection.execute(
        f"""SELECT count(*) FROM (
              SELECT season,fixture_id,defending_team_id,count(*) n
              FROM {phase}_team_match_defense GROUP BY 1,2,3
              HAVING n<>1 OR min(team_goals_conceded) IS NULL OR min(team_xgc) IS NULL
            )"""
    ).fetchone()[0]
    rows = connection.execute(
        f"SELECT count(*) FROM {phase}_team_match_defense"
    ).fetchone()[0]
    if invalid or (
        expected_team_match_rows is not None and rows != expected_team_match_rows
    ):
        raise HistoricalOpponentStrengthExperimentError(
            f"{phase} team-defence grain validation failed: rows={rows}, invalid={invalid}"
        )
    return f"{phase}_team_match_defense"


def _create_opponent_contexts(
    connection: duckdb.DuckDBPyConnection, *, phase: str,
) -> str:
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_opponent_contexts AS
            WITH targets AS (
              SELECT DISTINCT season,target_gameweek,fixture_id target_fixture_id,
                target_fixture_count,opponent_team_id,target_deadline
              FROM {phase}_base_fixture WHERE target_has_fixture
            ), eligible AS (
              SELECT t.*,d.fixture_id source_fixture_id,d.gameweek source_gameweek,
                d.kickoff_time source_kickoff,d.team_goals_conceded,d.team_xgc,
                d.defending_team_name opponent_team_name,
                row_number() OVER(
                  PARTITION BY t.season,t.target_gameweek,t.target_fixture_id
                  ORDER BY d.kickoff_time DESC,d.fixture_id DESC
                ) chronology_rank
              FROM targets t LEFT JOIN {phase}_team_match_defense d
                ON d.season=t.season AND d.defending_team_id=t.opponent_team_id
               AND d.kickoff_time<t.target_deadline
            ), opponent AS (
              SELECT season,target_gameweek,target_fixture_id,target_fixture_count,
                opponent_team_id,target_deadline,min(opponent_team_name) opponent_team_name,
                count(source_fixture_id) FILTER(WHERE chronology_rank<=?)::INTEGER prior_match_count,
                sum(team_goals_conceded) FILTER(WHERE chronology_rank<=?)::DOUBLE opponent_gc_numerator,
                sum(team_xgc) FILTER(WHERE chronology_rank<=?)::DOUBLE opponent_xgc_numerator,
                to_json(list(source_fixture_id ORDER BY source_kickoff DESC,source_fixture_id DESC)
                  FILTER(WHERE chronology_rank<=? AND source_fixture_id IS NOT NULL)) selected_source_fixture_ids,
                to_json(list(cast(source_kickoff AS VARCHAR) ORDER BY source_kickoff DESC,source_fixture_id DESC)
                  FILTER(WHERE chronology_rank<=? AND source_fixture_id IS NOT NULL)) selected_source_kickoffs,
                max(source_kickoff) FILTER(WHERE chronology_rank<=?) latest_source_kickoff,
                min(source_kickoff) FILTER(WHERE chronology_rank<=?) oldest_source_kickoff
              FROM eligible GROUP BY 1,2,3,4,5,6
            ), league AS (
              SELECT t.season,t.target_deadline,
                sum(d.team_goals_conceded)::DOUBLE league_gc_numerator,
                count(*)::INTEGER league_gc_denominator,
                sum(d.team_xgc)::DOUBLE league_xgc_numerator,
                count(*)::INTEGER league_xgc_denominator
              FROM (SELECT DISTINCT season,target_deadline FROM targets) t
              JOIN {phase}_team_match_defense d
                ON d.season=t.season AND d.kickoff_time<t.target_deadline
              GROUP BY 1,2
            ), rates AS (
              SELECT o.*,l.league_gc_numerator,l.league_gc_denominator,
                l.league_xgc_numerator,l.league_xgc_denominator,
                o.opponent_gc_numerator/o.prior_match_count opponent_gc_rate,
                o.opponent_xgc_numerator/o.prior_match_count opponent_xgc_rate,
                l.league_gc_numerator/l.league_gc_denominator league_gc_rate,
                l.league_xgc_numerator/l.league_xgc_denominator league_xgc_rate
              FROM opponent o JOIN league l USING(season,target_deadline)
            ), raw AS (
              SELECT *,opponent_gc_rate/league_gc_rate raw_factor_gc,
                opponent_xgc_rate/league_xgc_rate raw_factor_xgc
              FROM rates
            )
            SELECT ?::VARCHAR phase,*,
              CASE WHEN prior_match_count<? THEN 1.0
                   ELSE least(?,greatest(?,raw_factor_gc)) END factor_gc,
              CASE WHEN prior_match_count<? THEN 1.0
                   ELSE least(?,greatest(?,raw_factor_xgc)) END factor_xgc,
              CASE WHEN prior_match_count<? THEN 'fewer_than_3_prior_matches' END neutral_factor_reason,
              false fixture_assignment_verified_predeadline,
              target_fixture_count<>1 finalized_rearrangement_risk,
              'kickoff_time_desc_then_fixture_id_desc'::VARCHAR source_ordering,
              'source_fixture.kickoff_time < frozen_target_deadline'::VARCHAR causal_cutoff_rule
            FROM raw""",
        [
            ROLLING_MATCHES, ROLLING_MATCHES, ROLLING_MATCHES,
            ROLLING_MATCHES, ROLLING_MATCHES, ROLLING_MATCHES, ROLLING_MATCHES,
            phase, MINIMUM_MATCHES, FACTOR_MAX, FACTOR_MIN,
            MINIMUM_MATCHES, FACTOR_MAX, FACTOR_MIN, MINIMUM_MATCHES,
        ],
    )
    invalid = connection.execute(
        f"""SELECT count(*) FROM {phase}_opponent_contexts
            WHERE prior_match_count>? OR latest_source_kickoff>=target_deadline
               OR factor_gc NOT BETWEEN ? AND ? OR factor_xgc NOT BETWEEN ? AND ?
               OR (prior_match_count<? AND (factor_gc<>1 OR factor_xgc<>1
                    OR neutral_factor_reason IS NULL))
               OR league_gc_denominator<=0 OR league_xgc_denominator<=0""",
        [ROLLING_MATCHES, FACTOR_MIN, FACTOR_MAX, FACTOR_MIN, FACTOR_MAX,
         MINIMUM_MATCHES],
    ).fetchone()[0]
    if invalid:
        raise HistoricalOpponentStrengthExperimentError(
            f"{phase} causal opponent-context validation failed in {invalid} rows"
        )
    return f"{phase}_opponent_contexts"


def _create_phase_predictions(
    connection: duckdb.DuckDBPyConnection, *, phase: str,
) -> tuple[str, str]:
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_candidate_fixture AS
            WITH factored AS (
              SELECT b.*,c.candidate,
                CASE c.candidate WHEN 'F0' THEN 1.0
                     WHEN 'F1' THEN o.factor_gc WHEN 'F2' THEN o.factor_xgc END opponent_factor,
                CASE c.candidate WHEN 'F1' THEN o.raw_factor_gc
                     WHEN 'F2' THEN o.raw_factor_xgc END raw_opponent_factor,
                o.prior_match_count opponent_prior_match_count,
                o.selected_source_fixture_ids,o.selected_source_kickoffs,
                o.opponent_gc_numerator,o.opponent_xgc_numerator,
                o.league_gc_numerator,o.league_gc_denominator,
                o.league_xgc_numerator,o.league_xgc_denominator,
                o.neutral_factor_reason,
                false fixture_assignment_verified_predeadline,
                b.target_fixture_count<>1 finalized_rearrangement_risk
              FROM {phase}_base_fixture b CROSS JOIN {phase}_candidate_set c
              LEFT JOIN {phase}_opponent_contexts o
                ON o.season=b.season AND o.target_gameweek=b.target_gameweek
               AND o.target_fixture_id=b.fixture_id
               AND o.opponent_team_id=b.opponent_team_id
            ), rates AS (
              SELECT *,
                CASE WHEN candidate='F0' THEN prior_xg_per_90_used
                     WHEN prior_xg_per_90_used IS NOT NULL
                     THEN prior_xg_per_90_used*opponent_factor END adjusted_xg_per90,
                CASE WHEN candidate='F0' THEN prior_xa_per_90_used
                     WHEN prior_xa_per_90_used IS NOT NULL
                     THEN prior_xa_per_90_used*opponent_factor END adjusted_xa_per90
              FROM factored
            ), components AS (
              SELECT *,
                CASE WHEN candidate='F0' THEN goal_xfp_v01
                     WHEN adjusted_xg_per90 IS NOT NULL AND expected_minutes_v01 IS NOT NULL
                     THEN adjusted_xg_per90*expected_minutes_v01/90.0*goal_points_for_position END candidate_goal_xfp,
                CASE WHEN candidate='F0' THEN assist_xfp_v01
                     WHEN adjusted_xa_per90 IS NOT NULL AND expected_minutes_v01 IS NOT NULL
                     THEN adjusted_xa_per90*expected_minutes_v01/90.0*3.0 END candidate_assist_xfp
              FROM rates
            ), predictions AS (
              SELECT ?::VARCHAR phase,season,candidate,target_gameweek,
                fixture_id,target_has_fixture,target_fixture_count,element_id,code,"position",
                team_id,team_name,opponent_team_id,home_away,kickoff_time,target_deadline,
                expected_minutes_v01 candidate_expected_minutes,
                appearance_xfp_v01 candidate_appearance_xfp,
                prior_total_minutes,prior_gameweeks_with_data,
                prior_xg_per_90_used raw_xg_per90,prior_xa_per_90_used raw_xa_per90,
                adjusted_xg_per90,adjusted_xa_per90,opponent_factor,raw_opponent_factor,
                opponent_prior_match_count,selected_source_fixture_ids,selected_source_kickoffs,
                opponent_gc_numerator,opponent_xgc_numerator,
                league_gc_numerator,league_gc_denominator,
                league_xgc_numerator,league_xgc_denominator,neutral_factor_reason,
                candidate_goal_xfp,candidate_assist_xfp,
                CASE WHEN candidate='F0' THEN fixture_xfp_v01
                     WHEN target_has_fixture AND appearance_xfp_v01 IS NOT NULL
                     THEN appearance_xfp_v01+coalesce(candidate_goal_xfp,0.0)
                          +coalesce(candidate_assist_xfp,0.0)
                     WHEN NOT target_has_fixture THEN NULL END candidate_fixture_xfp,
                CASE WHEN NOT target_has_fixture THEN true
                     ELSE appearance_xfp_v01 IS NOT NULL AND candidate_goal_xfp IS NOT NULL
                          AND candidate_assist_xfp IS NOT NULL END prediction_complete,
                attacking_rate_available,low_sample,availability_status,
                chance_of_playing_next_round,availability_forced_zero,
                previous_gameweek_minutes_uncapped,previous_gw_context_status,
                previous_gw_team_blank,previous_gw_player_not_in_universe,
                fixture_assignment_verified_predeadline,finalized_rearrangement_risk,
                history_cutoff_rule,historical_classification
              FROM components
            )
            SELECT p.*,a.actual_minutes fixture_actual_minutes,
              a.actual_goal_points fixture_actual_goal_points,
              a.actual_assist_points fixture_actual_assist_points,
              a.actual_goal_points+a.actual_assist_points fixture_actual_attacking_points,
              a.actual_modeled_points fixture_actual_modeled_points,
              a.actuals_not_predictors
            FROM predictions p LEFT JOIN {phase}_fixture_actuals a
              ON p.target_has_fixture AND a.season=p.season
             AND a.target_gameweek=p.target_gameweek AND a.element_id=p.element_id
             AND a.fixture_id=p.fixture_id""",
        [phase],
    )
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_candidate_player AS
            WITH aggregated AS (
              SELECT phase,season,candidate,target_gameweek,element_id,
                min(code) code,min("position") "position",min(team_id) team_id,
                min(team_name) team_name,
                count(*) FILTER(WHERE target_has_fixture) fixture_count,
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
                     WHEN count(candidate_goal_xfp) FILTER(WHERE target_has_fixture)
                          =count(*) FILTER(WHERE target_has_fixture)
                      AND count(candidate_assist_xfp) FILTER(WHERE target_has_fixture)
                          =count(*) FILTER(WHERE target_has_fixture)
                     THEN sum(candidate_goal_xfp+candidate_assist_xfp)
                          FILTER(WHERE target_has_fixture) END gameweek_attacking_xfp,
                CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                     WHEN count(candidate_fixture_xfp) FILTER(WHERE target_has_fixture)
                          =count(*) FILTER(WHERE target_has_fixture)
                     THEN sum(candidate_fixture_xfp) FILTER(WHERE target_has_fixture) END gameweek_xfp,
                CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN true
                     ELSE bool_and(prediction_complete) FILTER(WHERE target_has_fixture) END prediction_complete,
                bool_and(attacking_rate_available) raw_attacking_rate_available,
                min(prior_total_minutes) prior_total_minutes,
                min(prior_gameweeks_with_data) prior_gameweeks_with_data,
                min(previous_gameweek_minutes_uncapped) previous_gameweek_minutes_uncapped,
                min(availability_status) availability_status,
                min(chance_of_playing_next_round) chance_of_playing_next_round,
                bool_or(availability_forced_zero) availability_forced_zero,
                min(opponent_factor) min_opponent_factor,max(opponent_factor) max_opponent_factor,
                avg(opponent_factor) FILTER(WHERE target_has_fixture) mean_opponent_factor,
                false fixture_assignment_verified_predeadline,
                count(*) FILTER(WHERE target_has_fixture)<>1 finalized_rearrangement_risk,
                min(history_cutoff_rule) history_cutoff_rule,
                min(historical_classification) historical_classification
              FROM {phase}_candidate_fixture GROUP BY 1,2,3,4,5
            )
            SELECT a.*,b.gameweek_expected_minutes_for_evaluation baseline_expected_minutes,
              b.gameweek_appearance_xfp_for_evaluation baseline_appearance_xfp,
              b.availability_band,b.actual_fixture_count,b.actual_minutes,
              b.actual_appearance_points,b.actual_goal_points,b.actual_assist_points,
              b.actual_goal_points+b.actual_assist_points actual_attacking_points,
              b.actual_modeled_points,b.actual_full_fpl_points,b.actual_state
            FROM aggregated a JOIN {phase}_base_player b
              USING(season,target_gameweek,element_id)"""
    )
    invalid = connection.execute(
        f"""SELECT count(*) FROM {phase}_candidate_player
            WHERE historical_classification<>? OR history_cutoff_rule<>?
              OR gameweek_expected_minutes IS DISTINCT FROM baseline_expected_minutes
              OR gameweek_appearance_xfp IS DISTINCT FROM baseline_appearance_xfp
              OR (fixture_count=0 AND (gameweek_xfp<>0 OR actual_modeled_points<>0))""",
        [HISTORICAL_CLASSIFICATION, HISTORY_CUTOFF_RULE],
    ).fetchone()[0]
    f0_mismatch = connection.execute(
        f"""SELECT count(*) FROM {phase}_candidate_player c
            JOIN {phase}_base_player b USING(season,target_gameweek,element_id)
            WHERE c.candidate='F0' AND (
              c.gameweek_xfp IS DISTINCT FROM b.gameweek_xfp_v01
              OR c.gameweek_goal_xfp IS DISTINCT FROM b.gameweek_goal_xfp_for_evaluation
              OR c.gameweek_assist_xfp IS DISTINCT FROM b.gameweek_assist_xfp_for_evaluation)"""
    ).fetchone()[0]
    leakage = connection.execute(
        f"""SELECT count(*) FROM {phase}_candidate_fixture
            WHERE target_has_fixture AND candidate IN ('F1','F2') AND (
              opponent_factor IS NULL OR opponent_prior_match_count>?
              OR fixture_assignment_verified_predeadline)""",
        [ROLLING_MATCHES],
    ).fetchone()[0]
    if invalid or f0_mismatch or leakage:
        raise HistoricalOpponentStrengthExperimentError(
            f"{phase} prediction invariance failed: invalid={invalid}, "
            f"f0_mismatch={f0_mismatch}, leakage={leakage}"
        )
    return f"{phase}_candidate_fixture", f"{phase}_candidate_player"


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


def _is_primary(row: dict[str, Any]) -> bool:
    return row["fixture_count"] == 1 and row["actual_minutes"] > 0


def _phase_metrics(
    rows: Sequence[dict[str, Any]], *, phase: str, season: str,
    candidates: Sequence[str], populations: Sequence[str] = ("natural", "primary"),
) -> list[tuple[Any, ...]]:
    output = []
    for population in populations:
        members = rows if population == "natural" else [row for row in rows if _is_primary(row)]
        for candidate in candidates:
            candidate_rows = [row for row in members if row["candidate"] == candidate]
            for target, (prediction, actual) in TARGET_FIELDS.items():
                output.append(_metric_tuple(
                    phase=phase,season=season,candidate=candidate,population=population,
                    target=target,metric=_metric(candidate_rows,prediction,actual),
                ))
    return output


def _common_pair_metrics(
    rows: Sequence[dict[str, Any]], *, phase: str, season: str,
    candidates: Sequence[str],
) -> list[tuple[Any, ...]]:
    primary = [row for row in rows if _is_primary(row)]
    indexed = {
        candidate: {(row["target_gameweek"],row["element_id"]):row
                    for row in primary if row["candidate"]==candidate}
        for candidate in candidates
    }
    baseline=indexed["F0"];output=[]
    for candidate in candidates[1:]:
        compared=indexed[candidate]
        for target,(prediction,actual) in TARGET_FIELDS.items():
            keys=[key for key,control in baseline.items() if key in compared
                  and control[prediction] is not None and compared[key][prediction] is not None
                  and control[actual] is not None and compared[key][actual] is not None]
            for predictor,source in (("F0",baseline),(candidate,compared)):
                metric=_metric([source[key] for key in keys],prediction,actual)
                output.append((phase,season,candidate,predictor,f"primary:{target}",len(keys),
                               metric["mae"],metric["rmse"],metric["bias"],metric["spearman"]))
    return output


def _common_lookup(
    rows: Sequence[tuple[Any,...]], *, comparison: str,predictor: str,target: str,
) -> dict[str,Any]:
    label=f"primary:{target}"
    for row in rows:
        if row[2]==comparison and row[3]==predictor and row[4]==label:
            return {"n":row[5],"mae":row[6],"rmse":row[7],"bias":row[8],"spearman":row[9]}
    raise HistoricalOpponentStrengthExperimentError(f"missing metric {comparison}/{predictor}/{label}")


def _coverage_lookup(rows: Sequence[tuple[Any,...]],candidate: str) -> float:
    for row in rows:
        if row[2]==candidate and row[3]=="natural" and row[4]=="modeled_xfp":
            return float(row[9])
    raise HistoricalOpponentStrengthExperimentError(f"missing coverage {candidate}")


def select_development_winner(
    metrics: Sequence[tuple[Any,...]],common: Sequence[tuple[Any,...]],
) -> tuple[str|None,list[dict[str,Any]]]:
    if any(row[0]!="development" or row[1]!=DEVELOPMENT_SEASON for row in metrics+common):
        raise HistoricalOpponentStrengthExperimentError("holdout data entered development selection")
    records=[]
    for candidate in CANDIDATES[1:]:
        control={target:_common_lookup(common,comparison=candidate,predictor="F0",target=target)
                 for target in TARGET_FIELDS}
        changed={target:_common_lookup(common,comparison=candidate,predictor=candidate,target=target)
                 for target in TARGET_FIELDS}
        record={
            "candidate":candidate,
            "attacking_spearman_improvement":changed["attacking_combined"]["spearman"]-control["attacking_combined"]["spearman"],
            "goal_spearman_decline":control["goal"]["spearman"]-changed["goal"]["spearman"],
            "assist_spearman_decline":control["assist"]["spearman"]-changed["assist"]["spearman"],
            "attacking_mae_improvement_pct":_reduction(control["attacking_combined"]["mae"],changed["attacking_combined"]["mae"]),
            "modeled_mae_worsening_pct":-_reduction(control["modeled_xfp"]["mae"],changed["modeled_xfp"]["mae"]),
            "modeled_rmse_worsening_pct":-_reduction(control["modeled_xfp"]["rmse"],changed["modeled_xfp"]["rmse"]),
            "absolute_modeled_bias_worsening":abs(changed["modeled_xfp"]["bias"])-abs(control["modeled_xfp"]["bias"]),
            "coverage_drop_pp":_coverage_lookup(metrics,"F0")-_coverage_lookup(metrics,candidate),
            "attacking_mae":changed["attacking_combined"]["mae"],
            "modeled_mae":changed["modeled_xfp"]["mae"],
        }
        record["development_qualifies"]=(
            record["attacking_spearman_improvement"]
                >= DEVELOPMENT_THRESHOLDS["minimum_attacking_spearman_improvement"]
            and record["goal_spearman_decline"]
                <= DEVELOPMENT_THRESHOLDS["maximum_goal_spearman_decline"]
            and record["assist_spearman_decline"]
                <= DEVELOPMENT_THRESHOLDS["maximum_assist_spearman_decline"]
            and record["attacking_mae_improvement_pct"]
                >= DEVELOPMENT_THRESHOLDS["minimum_attacking_mae_improvement_pct"]
            and record["modeled_mae_worsening_pct"]
                <= DEVELOPMENT_THRESHOLDS["maximum_modeled_mae_worsening_pct"]
            and record["modeled_rmse_worsening_pct"]
                <= DEVELOPMENT_THRESHOLDS["maximum_modeled_rmse_worsening_pct"]
            and record["absolute_modeled_bias_worsening"]
                <= DEVELOPMENT_THRESHOLDS["maximum_absolute_modeled_bias_worsening"]
            and record["coverage_drop_pp"]
                <= DEVELOPMENT_THRESHOLDS["maximum_coverage_drop_pp"])
        records.append(record)
    qualifying=sorted((r for r in records if r["development_qualifies"]),
                      key=lambda r:(-r["attacking_spearman_improvement"],r["attacking_mae"],
                                    r["modeled_mae"],0 if r["candidate"]=="F1" else 1))
    return (qualifying[0]["candidate"] if qualifying else None),records


def _holdout_candidate_set(winner: str) -> tuple[str,str]:
    if winner not in CANDIDATES[1:]:
        raise HistoricalOpponentStrengthExperimentError("holdout requires one development winner")
    return "F0",winner


def _holdout_decision(
    metrics: Sequence[tuple[Any,...]],common: Sequence[tuple[Any,...]],winner: str,
) -> dict[str,Any]:
    control={target:_common_lookup(common,comparison=winner,predictor="F0",target=target)
             for target in TARGET_FIELDS}
    changed={target:_common_lookup(common,comparison=winner,predictor=winner,target=target)
             for target in TARGET_FIELDS}
    result={
        "holdout_attacking_spearman_improvement":changed["attacking_combined"]["spearman"]-control["attacking_combined"]["spearman"],
        "holdout_goal_spearman_decline":control["goal"]["spearman"]-changed["goal"]["spearman"],
        "holdout_assist_spearman_decline":control["assist"]["spearman"]-changed["assist"]["spearman"],
        "holdout_attacking_mae_improvement_pct":_reduction(control["attacking_combined"]["mae"],changed["attacking_combined"]["mae"]),
        "holdout_modeled_mae_worsening_pct":-_reduction(control["modeled_xfp"]["mae"],changed["modeled_xfp"]["mae"]),
        "holdout_modeled_rmse_worsening_pct":-_reduction(control["modeled_xfp"]["rmse"],changed["modeled_xfp"]["rmse"]),
        "holdout_absolute_modeled_bias_worsening":abs(changed["modeled_xfp"]["bias"])-abs(control["modeled_xfp"]["bias"]),
        "holdout_coverage_drop_pp":_coverage_lookup(metrics,"F0")-_coverage_lookup(metrics,winner),
    }
    result["holdout_passed"]=(
        result["holdout_attacking_spearman_improvement"]
            >= HOLDOUT_THRESHOLDS["minimum_attacking_spearman_improvement"]
        and result["holdout_goal_spearman_decline"]
            <= HOLDOUT_THRESHOLDS["maximum_goal_spearman_decline"]
        and result["holdout_assist_spearman_decline"]
            <= HOLDOUT_THRESHOLDS["maximum_assist_spearman_decline"]
        and result["holdout_attacking_mae_improvement_pct"]
            >= HOLDOUT_THRESHOLDS["minimum_attacking_mae_improvement_pct"]
        and result["holdout_modeled_mae_worsening_pct"]
            <= HOLDOUT_THRESHOLDS["maximum_modeled_mae_worsening_pct"]
        and result["holdout_modeled_rmse_worsening_pct"]
            <= HOLDOUT_THRESHOLDS["maximum_modeled_rmse_worsening_pct"]
        and result["holdout_absolute_modeled_bias_worsening"]
            <= HOLDOUT_THRESHOLDS["maximum_absolute_modeled_bias_worsening"]
        and result["holdout_coverage_drop_pp"]
            <= HOLDOUT_THRESHOLDS["maximum_coverage_drop_pp"])
    return result


def _population(row: dict[str,Any],name: str) -> bool:
    prior=row["prior_total_minutes"]
    if name=="primary_normal_single_played": return _is_primary(row)
    if name=="all_complete": return row["gameweek_xfp"] is not None and row["actual_modeled_points"] is not None
    if name=="expected_minutes_gt_0": return row["gameweek_expected_minutes"] is not None and row["gameweek_expected_minutes"]>0
    if name=="prior_minutes_1_90": return prior is not None and 1<=prior<=90
    if name=="prior_minutes_91_179": return prior is not None and 91<=prior<=179
    if name=="prior_minutes_180_450": return prior is not None and 180<=prior<=450
    if name=="prior_minutes_451_plus": return prior is not None and prior>=451
    if name in ("GK","DEF","MID","FWD"): return row["position"]==name
    if name=="double": return row["fixture_count"]>1
    if name=="verified_blank": return row["fixture_count"]==0 and row["actual_state"]=="verified_blank"
    if name=="stable": return (row["raw_attacking_rate_available"] is True and prior is not None
                                and prior>=450 and row["baseline_expected_minutes"] is not None
                                and row["baseline_expected_minutes"]>=60 and row["fixture_count"]==1)
    raise ValueError(name)


DIAGNOSTIC_POPULATIONS=(
    "primary_normal_single_played","all_complete","expected_minutes_gt_0",
    "prior_minutes_1_90","prior_minutes_91_179","prior_minutes_180_450",
    "prior_minutes_451_plus","GK","DEF","MID","FWD","double","verified_blank","stable",
)


def _diagnostic_metrics(rows: Sequence[dict[str,Any]],*,phase: str,season: str,
                        candidates: Sequence[str]) -> list[tuple[Any,...]]:
    output=[]
    for population in DIAGNOSTIC_POPULATIONS:
        subset=[row for row in rows if _population(row,population)]
        for candidate in candidates:
            members=[row for row in subset if row["candidate"]==candidate]
            for target,(prediction,actual) in TARGET_FIELDS.items():
                output.append(_metric_tuple(phase=phase,season=season,candidate=candidate,
                              population=population,target=target,metric=_metric(members,prediction,actual)))
    return output


def _factor_values(rows: Sequence[dict[str,Any]],candidate: str) -> list[tuple[float|None,float,bool]]:
    raw="raw_factor_gc" if candidate=="F1" else "raw_factor_xgc"
    clipped="factor_gc" if candidate=="F1" else "factor_xgc"
    return [(float(row[raw]) if row[raw] is not None else None,float(row[clipped]),
             row["neutral_factor_reason"] is not None) for row in rows]


def _quantile(values: Sequence[float],q: float) -> float|None:
    if not values:return None
    ordered=sorted(values);index=(len(ordered)-1)*q;low=math.floor(index);high=math.ceil(index)
    if low==high:return ordered[low]
    return ordered[low]*(high-index)+ordered[high]*(index-low)


def _factor_distribution_rows(contexts: Sequence[dict[str,Any]],*,phase: str,season: str,
                              candidates: Sequence[str]) -> list[tuple[Any,...]]:
    output=[]
    for candidate in candidates:
        if candidate=="F0":continue
        values=_factor_values(contexts,candidate);raw=[v[0] for v in values if v[0] is not None]
        clipped=[v[1] for v in values];n=len(values)
        output.append((phase,season,candidate,n,min(raw),_quantile(raw,.05),_quantile(raw,.25),
                       _quantile(raw,.5),_quantile(raw,.75),_quantile(raw,.95),max(raw),
                       min(clipped),math.fsum(clipped)/n,max(clipped),
                       100*sum(value==FACTOR_MIN and not neutral for _,value,neutral in values)/n,
                       100*sum(value==FACTOR_MAX and not neutral for _,value,neutral in values)/n,
                       100*sum(neutral for _,_,neutral in values)/n))
    return output


def _factor_opponent_rows(contexts: Sequence[dict[str,Any]],*,phase: str,season: str,
                          candidates: Sequence[str]) -> list[tuple[Any,...]]:
    output=[]
    for candidate in candidates:
        if candidate=="F0":continue
        raw_field="raw_factor_gc" if candidate=="F1" else "raw_factor_xgc"
        factor_field="factor_gc" if candidate=="F1" else "factor_xgc"
        keys=sorted({(row["opponent_team_id"],row["opponent_team_name"]) for row in contexts})
        for team_id,name in keys:
            members=[row for row in contexts if row["opponent_team_id"]==team_id]
            raw=[float(row[raw_field]) for row in members if row[raw_field] is not None]
            values=[float(row[factor_field]) for row in members]
            output.append((phase,season,candidate,team_id,name,len(members),
                           sum(row["neutral_factor_reason"] is not None for row in members),
                           math.fsum(raw)/len(raw) if raw else None,math.fsum(values)/len(values),
                           min(values),max(values)))
    return output


def _factor_correlation_row(contexts: Sequence[dict[str,Any]],*,phase: str,season: str)->tuple[Any,...]:
    left=[float(row["factor_gc"]) for row in contexts];right=[float(row["factor_xgc"]) for row in contexts]
    return phase,season,len(left),_pearson(left,right),_spearman(left,right)


def _factor_band(value: float) -> str:
    if value==FACTOR_MIN:return "0.70"
    if value<.85:return ">0.70-<0.85"
    if value<.95:return "0.85-<0.95"
    if value<1.05:return "0.95-<1.05"
    if value<1.15:return "1.05-<1.15"
    if value<FACTOR_MAX:return "1.15-<1.30"
    return "1.30"


FACTOR_BANDS=("0.70",">0.70-<0.85","0.85-<0.95","0.95-<1.05","1.05-<1.15","1.15-<1.30","1.30")


def _factor_band_residual_rows(fixture_rows: Sequence[dict[str,Any]],*,phase: str,season: str,
                               candidates: Sequence[str])->list[tuple[Any,...]]:
    indexed={candidate:{(r["target_gameweek"],r["element_id"],r["fixture_id"]):r
                        for r in fixture_rows if r["candidate"]==candidate}
             for candidate in candidates};baseline=indexed["F0"];output=[]
    for candidate in candidates[1:]:
        for band in FACTOR_BANDS:
            pairs=[]
            for key,row in indexed[candidate].items():
                control=baseline[key]
                if (row["target_fixture_count"]==1 and row["fixture_actual_minutes"] is not None
                    and row["fixture_actual_minutes"]>0 and row["candidate_goal_xfp"] is not None
                    and row["candidate_assist_xfp"] is not None and control["candidate_goal_xfp"] is not None
                    and control["candidate_assist_xfp"] is not None
                    and row["fixture_actual_attacking_points"] is not None
                    and _factor_band(float(row["opponent_factor"]))==band):
                    pairs.append((control,row))
            if not pairs:
                output.append((phase,season,candidate,band,0,None,None,None,None,None,None,None));continue
            factors=[float(row["opponent_factor"]) for _,row in pairs]
            f0_errors=[];candidate_errors=[]
            for control,row in pairs:
                actual=float(row["fixture_actual_attacking_points"])
                f0=actual-float(control["candidate_goal_xfp"]+control["candidate_assist_xfp"])
                changed=actual-float(row["candidate_goal_xfp"]+row["candidate_assist_xfp"])
                f0_errors.append(f0);candidate_errors.append(changed)
            output.append((phase,season,candidate,band,len(pairs),math.fsum(factors)/len(factors),
                           math.fsum(f0_errors)/len(f0_errors),math.fsum(candidate_errors)/len(candidate_errors),
                           math.fsum(abs(x) for x in f0_errors)/len(f0_errors),
                           math.fsum(abs(x) for x in candidate_errors)/len(candidate_errors),
                           math.sqrt(math.fsum(x*x for x in f0_errors)/len(f0_errors)),
                           math.sqrt(math.fsum(x*x for x in candidate_errors)/len(candidate_errors))))
    return output


def _primary_fixture_residuals(fixture_rows: Sequence[dict[str,Any]],candidate: str)->list[dict[str,Any]]:
    baseline={(r["target_gameweek"],r["element_id"],r["fixture_id"]):r for r in fixture_rows if r["candidate"]=="F0"}
    output=[]
    for row in fixture_rows:
        if row["candidate"]!=candidate:continue
        key=(row["target_gameweek"],row["element_id"],row["fixture_id"]);control=baseline[key]
        if (row["target_fixture_count"]==1 and row["fixture_actual_minutes"] is not None
            and row["fixture_actual_minutes"]>0 and row["fixture_actual_attacking_points"] is not None
            and control["candidate_goal_xfp"] is not None and control["candidate_assist_xfp"] is not None
            and row["raw_xg_per90"] is not None and row["raw_xa_per90"] is not None):
            output.append({**row,"f0_residual":float(row["fixture_actual_attacking_points"])-
                           float(control["candidate_goal_xfp"]+control["candidate_assist_xfp"])})
    return output


def _within_player_rows(fixture_rows: Sequence[dict[str,Any]],*,phase: str,season: str,
                        candidates: Sequence[str])->list[tuple[Any,...]]:
    output=[]
    for candidate in candidates[1:]:
        rows=_primary_fixture_residuals(fixture_rows,candidate)
        for element_id in sorted({r["element_id"] for r in rows}):
            members=[r for r in rows if r["element_id"]==element_id]
            factors=[float(r["opponent_factor"]) for r in members]
            residuals=[float(r["f0_residual"]) for r in members]
            span=max(factors)-min(factors)
            if len(members)>=6 and span>=0.20:
                output.append((phase,season,candidate,element_id,len(members),min(factors),max(factors),span,
                               _pearson(factors,residuals),_spearman(factors,residuals)))
    return output


def _raw_rate_tier(value: float)->str:
    if value<.25:return "0-<0.25"
    if value<.50:return "0.25-<0.50"
    if value<.75:return "0.50-<0.75"
    return "0.75+"


def _rate_tier_rows(fixture_rows: Sequence[dict[str,Any]],*,phase: str,season: str,
                    candidates: Sequence[str])->list[tuple[Any,...]]:
    output=[]
    for candidate in candidates[1:]:
        rows=_primary_fixture_residuals(fixture_rows,candidate)
        for tier in ("0-<0.25","0.25-<0.50","0.50-<0.75","0.75+"):
            members=[r for r in rows if _raw_rate_tier(float(r["raw_xg_per90"]+r["raw_xa_per90"]))==tier]
            factors=[float(r["opponent_factor"]) for r in members]
            residuals=[float(r["f0_residual"]) for r in members]
            output.append((phase,season,candidate,tier,len(members),
                           math.fsum(factors)/len(factors) if factors else None,
                           math.fsum(residuals)/len(residuals) if residuals else None,
                           _pearson(factors,residuals),_spearman(factors,residuals)))
    return output


def _selection_rows(development: Sequence[dict[str,Any]],winner: str|None,
                    holdout: dict[str,Any]|None)->list[tuple[Any,...]]:
    output=[]
    for record in development:
        selected=record["candidate"]==winner;held=holdout if selected and holdout else {}
        output.append((record["candidate"],record["development_qualifies"],
            record["attacking_spearman_improvement"],record["goal_spearman_decline"],
            record["assist_spearman_decline"],record["attacking_mae_improvement_pct"],
            record["modeled_mae_worsening_pct"],record["modeled_rmse_worsening_pct"],
            record["absolute_modeled_bias_worsening"],record["coverage_drop_pp"],
            record["attacking_mae"],record["modeled_mae"],selected,held.get("holdout_passed"),
            held.get("holdout_attacking_spearman_improvement"),held.get("holdout_goal_spearman_decline"),
            held.get("holdout_assist_spearman_decline"),held.get("holdout_attacking_mae_improvement_pct"),
            held.get("holdout_modeled_mae_worsening_pct"),held.get("holdout_modeled_rmse_worsening_pct"),
            held.get("holdout_absolute_modeled_bias_worsening"),held.get("holdout_coverage_drop_pp")))
    return output


def _write_outputs(connection: duckdb.DuckDBPyConnection,*,experiment_root: Path,
                   manifest_base: dict[str,Any],tables: Sequence[str])->tuple[Path,Path]:
    final=experiment_root/EXPERIMENT_VERSION
    if final.exists():
        raise HistoricalOpponentStrengthExperimentOutputExistsError(
            f"experiment output already exists and will not be overwritten: {final}")
    stage=experiment_root/f".{EXPERIMENT_VERSION}.{uuid.uuid4().hex}.tmp"
    stage.mkdir(parents=True,exist_ok=False);outputs=[]
    try:
        for table in tables:
            path=stage/f"{table}.parquet"
            connection.execute(f'COPY "{table}" TO ? (FORMAT PARQUET, COMPRESSION ZSTD)',[str(path)])
            outputs.append({"path":path.name,"rows":connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0],
                            "bytes":path.stat().st_size,"sha256":_sha256(path)})
        _write_exclusive(stage/"experiment_manifest.json",
                         json.dumps({**manifest_base,"outputs":outputs},indent=2,sort_keys=True).encode()+b"\n")
        experiment_root.mkdir(parents=True,exist_ok=True)
        try:stage.rename(final)
        except FileExistsError as exc:
            raise HistoricalOpponentStrengthExperimentOutputExistsError(
                f"experiment output already exists and will not be overwritten: {final}") from exc
    except Exception:
        shutil.rmtree(stage,ignore_errors=True);raise
    return final,final/"experiment_manifest.json"


def run_historical_opponent_strength_experiment(
    *,historical_clean_root: Path=Path("data/historical/clean"),
    baseline_root: Path=Path("data/historical/backtests"),
    experiment_root: Path=Path("data/historical/experiments"),
    clock=lambda:datetime.now(timezone.utc),
)->HistoricalOpponentStrengthExperimentResult:
    final=experiment_root/EXPERIMENT_VERSION
    if final.exists():
        raise HistoricalOpponentStrengthExperimentOutputExistsError(
            f"experiment output already exists and will not be overwritten: {final}")
    paths,protected_hashes,provenance=_validate_all_inputs(
        historical_clean_root=historical_clean_root,baseline_root=baseline_root,
        experiment_root=experiment_root)
    connection=duckdb.connect(":memory:")
    try:
        _load_phase_inputs(connection,phase="development",season=DEVELOPMENT_SEASON,
                           paths=paths,candidates=CANDIDATES)
        dev_team=_create_team_defense(connection,phase="development")
        dev_context=_create_opponent_contexts(connection,phase="development")
        dev_fixture,dev_player=_create_phase_predictions(connection,phase="development")
        dev_rows=_fetch_rows(connection,dev_player)
        dev_fixture_rows=_fetch_rows(
            connection,dev_fixture,exclude=("kickoff_time","target_deadline"))
        dev_context_rows=_fetch_rows(
            connection,dev_context,
            exclude=("target_deadline","latest_source_kickoff","oldest_source_kickoff"))
        dev_metrics=_phase_metrics(dev_rows,phase="development",season=DEVELOPMENT_SEASON,candidates=CANDIDATES)
        dev_common=_common_pair_metrics(dev_rows,phase="development",season=DEVELOPMENT_SEASON,candidates=CANDIDATES)
        winner,selection=select_development_winner(dev_metrics,dev_common)

        holdout_result=None;holdout_rows=[];holdout_fixture_rows=[];holdout_context_rows=[]
        holdout_metrics=[];holdout_common=[]
        if winner:
            holdout_candidates=_holdout_candidate_set(winner)
            _load_phase_inputs(connection,phase="holdout",season=HOLDOUT_SEASON,
                               paths=paths,candidates=holdout_candidates)
            hold_team=_create_team_defense(connection,phase="holdout")
            hold_context=_create_opponent_contexts(connection,phase="holdout")
            hold_fixture,hold_player=_create_phase_predictions(connection,phase="holdout")
            holdout_rows=_fetch_rows(connection,hold_player)
            holdout_fixture_rows=_fetch_rows(
                connection,hold_fixture,exclude=("kickoff_time","target_deadline"))
            holdout_context_rows=_fetch_rows(
                connection,hold_context,
                exclude=("target_deadline","latest_source_kickoff","oldest_source_kickoff"))
            holdout_metrics=_phase_metrics(holdout_rows,phase="holdout",season=HOLDOUT_SEASON,candidates=holdout_candidates)
            holdout_common=_common_pair_metrics(holdout_rows,phase="holdout",season=HOLDOUT_SEASON,candidates=holdout_candidates)
            holdout_result=_holdout_decision(holdout_metrics,holdout_common,winner)
            connection.execute(f"CREATE TABLE causal_team_match_defense AS SELECT * FROM {dev_team} UNION ALL SELECT * FROM {hold_team}")
            connection.execute(f"CREATE TABLE rolling_opponent_contexts AS SELECT * FROM {dev_context} UNION ALL SELECT * FROM {hold_context}")
            connection.execute(f"CREATE TABLE candidate_fixture_predictions AS SELECT * FROM {dev_fixture} UNION ALL SELECT * FROM {hold_fixture}")
            connection.execute(f"CREATE TABLE candidate_player_gameweek AS SELECT * FROM {dev_player} UNION ALL SELECT * FROM {hold_player}")
        else:
            connection.execute(f"CREATE TABLE causal_team_match_defense AS SELECT * FROM {dev_team}")
            connection.execute(f"CREATE TABLE rolling_opponent_contexts AS SELECT * FROM {dev_context}")
            connection.execute(f"CREATE TABLE candidate_fixture_predictions AS SELECT * FROM {dev_fixture}")
            connection.execute(f"CREATE TABLE candidate_player_gameweek AS SELECT * FROM {dev_player}")

        _create_rows_table(connection,"development_metrics",METRIC_SCHEMA,dev_metrics)
        _create_rows_table(connection,"development_common_pair_metrics",COMMON_PAIR_SCHEMA,dev_common)
        tables=["causal_team_match_defense","rolling_opponent_contexts","candidate_fixture_predictions",
                "candidate_player_gameweek","development_metrics","development_common_pair_metrics"]
        if winner:
            _create_rows_table(connection,"holdout_metrics",METRIC_SCHEMA,holdout_metrics)
            _create_rows_table(connection,"holdout_common_pair_metrics",COMMON_PAIR_SCHEMA,holdout_common)
            tables += ["holdout_metrics","holdout_common_pair_metrics"]
        diagnostics=_diagnostic_metrics(dev_rows,phase="development",season=DEVELOPMENT_SEASON,candidates=CANDIDATES)
        if winner:
            diagnostics += _diagnostic_metrics(holdout_rows,phase="holdout",season=HOLDOUT_SEASON,
                                               candidates=_holdout_candidate_set(winner))
        _create_rows_table(connection,"diagnostic_metrics",METRIC_SCHEMA,diagnostics)
        factor_distributions=_factor_distribution_rows(dev_context_rows,phase="development",season=DEVELOPMENT_SEASON,candidates=CANDIDATES)
        factor_opponents=_factor_opponent_rows(dev_context_rows,phase="development",season=DEVELOPMENT_SEASON,candidates=CANDIDATES)
        factor_correlations=[_factor_correlation_row(dev_context_rows,phase="development",season=DEVELOPMENT_SEASON)]
        band_residuals=_factor_band_residual_rows(dev_fixture_rows,phase="development",season=DEVELOPMENT_SEASON,candidates=CANDIDATES)
        within_players=_within_player_rows(dev_fixture_rows,phase="development",season=DEVELOPMENT_SEASON,candidates=CANDIDATES)
        rate_tiers=_rate_tier_rows(dev_fixture_rows,phase="development",season=DEVELOPMENT_SEASON,candidates=CANDIDATES)
        if winner:
            selected=_holdout_candidate_set(winner)
            factor_distributions += _factor_distribution_rows(holdout_context_rows,phase="holdout",season=HOLDOUT_SEASON,candidates=selected)
            factor_opponents += _factor_opponent_rows(holdout_context_rows,phase="holdout",season=HOLDOUT_SEASON,candidates=selected)
            factor_correlations.append(_factor_correlation_row(holdout_context_rows,phase="holdout",season=HOLDOUT_SEASON))
            band_residuals += _factor_band_residual_rows(holdout_fixture_rows,phase="holdout",season=HOLDOUT_SEASON,candidates=selected)
            within_players += _within_player_rows(holdout_fixture_rows,phase="holdout",season=HOLDOUT_SEASON,candidates=selected)
            rate_tiers += _rate_tier_rows(holdout_fixture_rows,phase="holdout",season=HOLDOUT_SEASON,candidates=selected)
        _create_rows_table(connection,"factor_distributions",FACTOR_DISTRIBUTION_SCHEMA,factor_distributions)
        _create_rows_table(connection,"factor_by_opponent",FACTOR_OPPONENT_SCHEMA,factor_opponents)
        _create_rows_table(connection,"factor_correlations",FACTOR_CORRELATION_SCHEMA,factor_correlations)
        _create_rows_table(connection,"factor_band_residuals",FACTOR_BAND_RESIDUAL_SCHEMA,band_residuals)
        _create_rows_table(connection,"within_player_confound",WITHIN_PLAYER_SCHEMA,within_players)
        _create_rows_table(connection,"raw_rate_tier_confound",RATE_TIER_SCHEMA,rate_tiers)
        _create_rows_table(connection,"selection_decision",SELECTION_SCHEMA,
                           _selection_rows(selection,winner,holdout_result))
        tables += ["diagnostic_metrics","factor_distributions","factor_by_opponent",
                   "factor_correlations","factor_band_residuals","within_player_confound",
                   "raw_rate_tier_confound","selection_decision"]

        if any(_sha256(path)!=digest for path,digest in protected_hashes.items()):
            raise HistoricalOpponentStrengthExperimentError("an immutable input changed during experiment")
        holdout_passed=holdout_result["holdout_passed"] if holdout_result else None
        decision=("PROMOTE OPPONENT-STRENGTH CANDIDATE TO xFP v0.2 DESIGN"
                  if holdout_passed else "DO NOT PROMOTE — KEEP xFP v0.1 WITHOUT OPPONENT ADJUSTMENT")
        manifest={
            "status":"complete","experiment_version":EXPERIMENT_VERSION,
            "historical_classification":HISTORICAL_CLASSIFICATION,
            "model_formula_frozen":"xfp_v01","live_model_modified":False,
            "development_season":DEVELOPMENT_SEASON,"holdout_season":HOLDOUT_SEASON,
            "target_gameweeks":list(TARGET_GAMEWEEKS),
            "candidate_definitions":{
                "F0":"immutable xFP v0.1 raw attacking rates",
                "F1":"raw xG/90 and xA/90 multiplied independently by causal rolling actual-GC factor",
                "F2":"raw xG/90 and xA/90 multiplied independently by causal rolling opponent-summed-xG xGC factor",
            },
            "factor_constants":{"rolling_matches":ROLLING_MATCHES,"minimum_prior_matches":MINIMUM_MATCHES,
                                "clip_min":FACTOR_MIN,"clip_max":FACTOR_MAX},
            "team_goals_conceded_construction":"home conceded=away score; away conceded=home score; player goals_conceded never summed",
            "team_xgc_construction":"sum opponent player-fixture xG at season/fixture/defending-team grain; player xgc never summed",
            "opponent_history_order":"kickoff_time DESC, fixture_id DESC",
            "causal_cutoff_rule":"source fixture kickoff_time < frozen target deadline",
            "causal_league_baseline":"all eligible league team-matches before the same target deadline",
            "primary_selection_population":"2023/24 target_fixture_count=1, actual_target_minutes>0, strict F0/candidate/actual common pairs",
            "fixture_assignment_verified_predeadline":False,
            "finalized_rearrangement_risk":"target_fixture_count != 1",
            "development_thresholds":DEVELOPMENT_THRESHOLDS,
            "development_tie_breakers":["largest combined attacking Spearman gain","lower combined attacking MAE",
                                        "lower modeled xFP MAE","simpler observable F1 before F2"],
            "holdout_thresholds":HOLDOUT_THRESHOLDS,
            "diagnostic_only_confound_definitions":{
                "within_player":"at least 6 primary fixture rows and clipped-factor range >=0.20",
                "raw_attacking_rate_tiers":["0-<0.25","0.25-<0.50","0.50-<0.75","0.75+"],
                "residual":"actual attacking points - frozen F0 attacking prediction",
            },
            "development_winner":winner,"holdout_evaluated":winner is not None,
            "holdout_passed":holdout_passed,"final_decision":decision,
            "interpretation":"Tests incremental opponent defensive context only; no claim of optimality or perfect historical fixture replay.",
            **provenance,"generation_timestamp":_iso_utc(clock()),
            "immutable_inputs":[{"path":str(path),"sha256":digest}
                                for path,digest in sorted(protected_hashes.items(),key=lambda item:str(item[0]))],
        }
        directory,manifest_path=_write_outputs(connection,experiment_root=experiment_root,
                                                manifest_base=manifest,tables=tables)
    except duckdb.Error as exc:
        raise HistoricalOpponentStrengthExperimentError(f"opponent experiment failed: {exc}") from exc
    finally:
        connection.close()
    return HistoricalOpponentStrengthExperimentResult(directory,manifest_path,winner,holdout_passed,decision)
