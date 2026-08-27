"""Development-only previous-season player attacking-prior experiment."""

from __future__ import annotations

import json
import math
import os
import shutil
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from .historical import HISTORICAL_CLASSIFICATION, HISTORY_CUTOFF_RULE
from .historical_backtest import (
    _create_scored_tables,
    _validate_inputs as _validate_backtest_inputs,
    _validate_scored_tables,
)
from .historical_minutes_experiment import (
    _create_rows_table,
    _fetch_dicts,
    _iso_utc,
    _metric,
    _sha256,
)
from .transform import TransformationError


EXPERIMENT_VERSION = "previous-season-attacking-prior-development-v1"
HISTORICAL_VERSION = "historical-v3.1"
BASELINE_VERSION = "xfp-v01-baseline-v1"
PRIOR_SEASON = "2023-24"
DEVELOPMENT_SEASON = "2024-25"
SEALED_HOLDOUT_SEASON = "2025-26"
TARGET_GAMEWEEKS = tuple(range(2, 39))
CANDIDATES = ("C0", "C1")
PSEUDO_MINUTES = 450.0

DEVELOPMENT_THRESHOLDS = {
    "gw2_5_combined_mae_improvement_pct": 5.0,
    "gw2_5_combined_rmse_improvement_pct": 3.0,
    "gw2_5_best_component_spearman_improvement": 0.02,
    "gw2_5_other_component_spearman_minimum_change": -0.01,
    "gw2_5_modeled_mae_improvement_pct": 3.0,
    "gw2_5_modeled_rmse_maximum_worsening_pct": 0.0,
    "gw6_10_combined_mae_maximum_worsening_pct": 1.0,
    "gw6_10_modeled_mae_maximum_worsening_pct": 1.0,
    "gw6_10_modeled_rmse_maximum_worsening_pct": 1.0,
    "gw6_10_goal_spearman_minimum_change": -0.01,
    "gw6_10_assist_spearman_minimum_change": -0.01,
    "gw6_10_modeled_spearman_minimum_change": -0.01,
    "gw11_plus_combined_mae_maximum_worsening_pct": 1.0,
    "gw11_plus_modeled_mae_maximum_worsening_pct": 1.0,
    "gw11_plus_modeled_rmse_maximum_worsening_pct": 1.0,
    "gw11_plus_goal_spearman_minimum_change": -0.01,
    "gw11_plus_assist_spearman_minimum_change": -0.01,
    "gw11_plus_modeled_spearman_minimum_change": -0.01,
    "overall_combined_absolute_bias_maximum_increase": 0.05,
    "overall_modeled_absolute_bias_maximum_increase": 0.05,
    "overall_coverage_maximum_drop_pp": 1.0,
}

TARGET_FIELDS = {
    "goal": ("gameweek_goal_xfp", "actual_goal_points"),
    "assist": ("gameweek_assist_xfp", "actual_assist_points"),
    "attacking_combined": ("gameweek_attacking_xfp", "actual_attacking_points"),
    "modeled_xfp": ("gameweek_xfp", "actual_modeled_points"),
}

PERIOD_SCOPES = (
    ("overall", "overall"),
    ("gameweek_period", "GW2-5"),
    ("gameweek_period", "GW6-10"),
    ("gameweek_period", "GW11+"),
)
PRIOR_MINUTE_BANDS = ("0", "1-90", "91-270", "271-450", "451+", "missing")


class HistoricalPreviousSeasonPriorExperimentError(TransformationError):
    """Raised when the sealed development-only experiment cannot run safely."""


class HistoricalPreviousSeasonPriorOutputExistsError(
    HistoricalPreviousSeasonPriorExperimentError
):
    """Raised rather than overwriting an immutable experiment artifact."""


@dataclass(frozen=True)
class HistoricalPreviousSeasonPriorExperimentResult:
    directory: Path
    manifest_path: Path
    development_passed: bool
    holdout_evaluated: bool = False


PRIOR_ELIGIBILITY_SCHEMA = (
    ("development_season", "VARCHAR"),
    ("element_id", "BIGINT"),
    ("code", "BIGINT"),
    ("target_position", "VARCHAR"),
    ("target_position_count", "INTEGER"),
    ("target_code_count", "INTEGER"),
    ("prior_element_id", "BIGINT"),
    ("prior_position", "VARCHAR"),
    ("prior_minutes", "BIGINT"),
    ("prior_xg", "DOUBLE"),
    ("prior_xa", "DOUBLE"),
    ("prior_xg_per_90", "DOUBLE"),
    ("prior_xa_per_90", "DOUBLE"),
    ("prior_club_count", "INTEGER"),
    ("prior_source_rows", "BIGINT"),
    ("prior_null_xg_rows", "BIGINT"),
    ("prior_null_xa_rows", "BIGINT"),
    ("prior_nonfinite_xg_rows", "BIGINT"),
    ("prior_nonfinite_xa_rows", "BIGINT"),
    ("prior_exception_rows", "BIGINT"),
    ("eligible", "BOOLEAN"),
    ("eligibility_reason", "VARCHAR"),
    ("join_key", "VARCHAR"),
    ("pseudo_minutes", "DOUBLE"),
)

PRIOR_REASON_SCHEMA = (
    ("eligibility_reason", "VARCHAR"),
    ("eligible", "BOOLEAN"),
    ("players", "BIGINT"),
)

METRIC_SCHEMA = (
    ("scope_type", "VARCHAR"),
    ("scope_value", "VARCHAR"),
    ("population", "VARCHAR"),
    ("candidate", "VARCHAR"),
    ("target", "VARCHAR"),
    ("n_eligible", "BIGINT"),
    ("n_complete_pairs", "BIGINT"),
    ("missing_prediction", "BIGINT"),
    ("missing_actual", "BIGINT"),
    ("coverage_pct", "DOUBLE"),
    ("mae", "DOUBLE"),
    ("rmse", "DOUBLE"),
    ("bias", "DOUBLE"),
    ("spearman", "DOUBLE"),
)

COMMON_METRIC_SCHEMA = (
    ("scope_type", "VARCHAR"),
    ("scope_value", "VARCHAR"),
    ("population", "VARCHAR"),
    ("target", "VARCHAR"),
    ("n_common_pairs", "BIGINT"),
    ("c0_mae", "DOUBLE"),
    ("c1_mae", "DOUBLE"),
    ("mae_improvement_pct", "DOUBLE"),
    ("c0_rmse", "DOUBLE"),
    ("c1_rmse", "DOUBLE"),
    ("rmse_improvement_pct", "DOUBLE"),
    ("c0_bias", "DOUBLE"),
    ("c1_bias", "DOUBLE"),
    ("absolute_bias_increase", "DOUBLE"),
    ("c0_spearman", "DOUBLE"),
    ("c1_spearman", "DOUBLE"),
    ("spearman_change", "DOUBLE"),
)

COVERAGE_SCHEMA = (
    ("scope_type", "VARCHAR"),
    ("scope_value", "VARCHAR"),
    ("population", "VARCHAR"),
    ("target", "VARCHAR"),
    ("candidate", "VARCHAR"),
    ("n_eligible", "BIGINT"),
    ("n_complete_pairs", "BIGINT"),
    ("missing_prediction", "BIGINT"),
    ("missing_actual", "BIGINT"),
    ("coverage_pct", "DOUBLE"),
    ("expanded_vs_c0_pairs", "BIGINT"),
)

GATE_SCHEMA = (
    ("gate_group", "VARCHAR"),
    ("gate_id", "VARCHAR"),
    ("c0_value", "DOUBLE"),
    ("c1_value", "DOUBLE"),
    ("change_value", "DOUBLE"),
    ("comparison", "VARCHAR"),
    ("threshold", "DOUBLE"),
    ("passed", "BOOLEAN"),
    ("detail", "VARCHAR"),
)


def blend_previous_season_rate(
    *,
    current_event: float,
    current_minutes: float,
    previous_event: float,
    previous_minutes: float,
) -> float:
    """Apply the preregistered 450-pseudo-minute player prior exactly."""
    values = (current_event, current_minutes, previous_event, previous_minutes)
    if any(not math.isfinite(float(value)) for value in values):
        raise HistoricalPreviousSeasonPriorExperimentError(
            "attacking-prior blend received a non-finite value"
        )
    if current_minutes <= 0 or previous_minutes < PSEUDO_MINUTES:
        raise HistoricalPreviousSeasonPriorExperimentError(
            "attacking-prior blend requires positive current minutes and an eligible prior"
        )
    previous_rate = 90.0 * previous_event / previous_minutes
    return 90.0 * (
        current_event + PSEUDO_MINUTES * previous_rate / 90.0
    ) / (current_minutes + PSEUDO_MINUTES)


def _prior_reason(target: dict[str, Any], prior: dict[str, Any] | None) -> str:
    if target.get("code") is None:
        return "missing_target_code"
    if int(target.get("target_code_count") or 0) != 1:
        return "target_code_collision"
    if int(target.get("target_position_count") or 0) != 1:
        return "target_position_anomaly"
    if prior is None:
        return "no_previous_season_code_match"
    if int(prior.get("identity_count") or 0) != 1:
        return "previous_season_code_collision"
    if int(prior.get("exception_rows") or 0) > 0:
        return "previous_season_source_anomaly"
    if prior.get("position") not in ("GK", "DEF", "MID", "FWD"):
        return "unknown_previous_season_position"
    if prior["position"] != target.get("position"):
        return "position_mismatch"
    minutes = prior.get("minutes")
    if minutes is None:
        return "missing_previous_season_minutes"
    if int(minutes) == 0:
        return "zero_previous_season_minutes"
    if int(minutes) < int(PSEUDO_MINUTES):
        return "previous_season_minutes_below_450"
    if int(prior.get("source_rows") or 0) == 0:
        return "missing_previous_season_performance"
    if int(prior.get("null_xg_rows") or 0) > 0 or prior.get("xg") is None:
        return "missing_previous_season_xg"
    if int(prior.get("nonfinite_xg_rows") or 0) > 0 or not math.isfinite(
        float(prior["xg"])
    ):
        return "nonfinite_previous_season_xg"
    if int(prior.get("null_xa_rows") or 0) > 0 or prior.get("xa") is None:
        return "missing_previous_season_xa"
    if int(prior.get("nonfinite_xa_rows") or 0) > 0 or not math.isfinite(
        float(prior["xa"])
    ):
        return "nonfinite_previous_season_xa"
    return "eligible"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalPreviousSeasonPriorExperimentError(
            f"could not read immutable manifest {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise HistoricalPreviousSeasonPriorExperimentError(
            f"immutable manifest is not an object: {path}"
        )
    return value


def _validate_experiment_inputs(
    *, historical_clean_root: Path, baseline_root: Path
) -> tuple[dict[str, Path], dict[Path, str]]:
    historical_root = historical_clean_root / HISTORICAL_VERSION
    historical_manifest_path = historical_root / "historical_ingestion_manifest.json"
    baseline_directory = baseline_root / BASELINE_VERSION
    baseline_manifest_path = baseline_directory / "backtest_manifest.json"
    historical_manifest = _load_json(historical_manifest_path)
    baseline_manifest = _load_json(baseline_manifest_path)
    if (
        historical_manifest.get("status") != "complete"
        or historical_manifest.get("parser_schema_version") != HISTORICAL_VERSION
        or historical_manifest.get("historical_classification")
        != HISTORICAL_CLASSIFICATION
    ):
        raise HistoricalPreviousSeasonPriorExperimentError(
            "historical-v3.1 manifest is not an approved complete input"
        )
    if (
        baseline_manifest.get("status") != "complete"
        or baseline_manifest.get("backtest_version") != BASELINE_VERSION
    ):
        raise HistoricalPreviousSeasonPriorExperimentError(
            "frozen xFP v0.1 baseline is not complete"
        )
    historical_outputs = {
        entry["path"]: entry["sha256"]
        for entry in historical_manifest.get("outputs", [])
    }
    baseline_outputs = {
        entry["path"]: entry["sha256"]
        for entry in baseline_manifest.get("outputs", [])
    }
    paths = {
        "historical_manifest": historical_manifest_path,
        "baseline_manifest": baseline_manifest_path,
        "baseline_fixture": baseline_directory / "fixture_predictions.parquet",
        "baseline_player": baseline_directory / "player_gameweek.parquet",
    }
    for season, kinds in {
        PRIOR_SEASON: (
            "historical_player_fixture.parquet",
            "historical_player_identity.parquet",
            "historical_reconciliation_exceptions.parquet",
        ),
        DEVELOPMENT_SEASON: (
            "historical_prediction_features.parquet",
            "historical_player_fixture.parquet",
            "historical_predeadline_player_state.parquet",
        ),
    }.items():
        for filename in kinds:
            key = f"{season}_{filename.removesuffix('.parquet')}"
            paths[key] = historical_root / season / filename

    expected: dict[Path, str] = {
        historical_manifest_path: _sha256(historical_manifest_path),
        baseline_manifest_path: _sha256(baseline_manifest_path),
        paths["baseline_fixture"]: baseline_outputs.get(
            "fixture_predictions.parquet", ""
        ),
        paths["baseline_player"]: baseline_outputs.get("player_gameweek.parquet", ""),
    }
    for key, path in paths.items():
        if key in ("historical_manifest", "baseline_manifest", "baseline_fixture", "baseline_player"):
            continue
        relative = str(path.relative_to(historical_root))
        expected[path] = historical_outputs.get(relative, "")
    for path, expected_digest in expected.items():
        if not path.is_file():
            raise HistoricalPreviousSeasonPriorExperimentError(
                f"required immutable input is missing: {path}"
            )
        if not expected_digest or _sha256(path) != expected_digest:
            raise HistoricalPreviousSeasonPriorExperimentError(
                f"immutable input hash does not match its manifest: {path}"
            )
    if any(SEALED_HOLDOUT_SEASON in str(path) for path in expected):
        raise HistoricalPreviousSeasonPriorExperimentError(
            "sealed holdout path entered development-only inputs"
        )
    return paths, expected


def _load_development_c0(
    connection: duckdb.DuckDBPyConnection, paths: dict[str, Path]
) -> None:
    connection.execute(
        """CREATE TABLE historical_features AS SELECT * FROM read_parquet(?)
           WHERE season=? AND target_gameweek BETWEEN 2 AND 38""",
        [
            str(paths[f"{DEVELOPMENT_SEASON}_historical_prediction_features"]),
            DEVELOPMENT_SEASON,
        ],
    )
    connection.execute(
        """CREATE TABLE historical_actual_fixture AS SELECT * FROM read_parquet(?)
           WHERE season=? AND gameweek BETWEEN 2 AND 38""",
        [
            str(paths[f"{DEVELOPMENT_SEASON}_historical_player_fixture"]),
            DEVELOPMENT_SEASON,
        ],
    )
    connection.execute(
        """CREATE TABLE historical_predeadline AS SELECT * FROM read_parquet(?)
           WHERE season=? AND target_gameweek BETWEEN 2 AND 38""",
        [
            str(paths[f"{DEVELOPMENT_SEASON}_historical_predeadline_player_state"]),
            DEVELOPMENT_SEASON,
        ],
    )
    _validate_backtest_inputs(
        connection,
        seasons=(DEVELOPMENT_SEASON,),
        gameweeks=TARGET_GAMEWEEKS,
        strict_scope=False,
    )
    _create_scored_tables(connection)
    _validate_scored_tables(connection)
    connection.execute(
        """CREATE TABLE development_c0_fixture AS
           SELECT p.*,f.cumulative_prior_xg,f.cumulative_prior_xa
           FROM fixture_predictions p
           JOIN historical_features f
             ON p.season=f.season AND p.target_gameweek=f.target_gameweek
            AND p.element_id=f.element_id
            AND p.fixture_id IS NOT DISTINCT FROM f.target_fixture_id"""
    )
    _validate_c0_reproduction(connection, paths)


def _validate_c0_reproduction(
    connection: duckdb.DuckDBPyConnection, paths: dict[str, Path]
) -> None:
    connection.execute(
        """CREATE TEMP TABLE frozen_baseline_fixture AS SELECT * FROM read_parquet(?)
           WHERE season=? AND target_gameweek BETWEEN 2 AND 38""",
        [str(paths["baseline_fixture"]), DEVELOPMENT_SEASON],
    )
    connection.execute(
        """CREATE TEMP TABLE frozen_baseline_player AS SELECT * FROM read_parquet(?)
           WHERE season=? AND target_gameweek BETWEEN 2 AND 38""",
        [str(paths["baseline_player"]), DEVELOPMENT_SEASON],
    )
    fixture_mismatch = connection.execute(
        """SELECT count(*) FROM development_c0_fixture c
           FULL JOIN frozen_baseline_fixture b
             ON c.season=b.season AND c.target_gameweek=b.target_gameweek
            AND c.element_id=b.element_id
            AND c.fixture_id IS NOT DISTINCT FROM b.fixture_id
           WHERE c.element_id IS NULL OR b.element_id IS NULL
              OR c.expected_minutes_v01 IS DISTINCT FROM b.expected_minutes_v01
              OR c.appearance_xfp_v01 IS DISTINCT FROM b.appearance_xfp_v01
              OR c.goal_xfp_v01 IS DISTINCT FROM b.goal_xfp_v01
              OR c.assist_xfp_v01 IS DISTINCT FROM b.assist_xfp_v01
              OR c.fixture_xfp_v01 IS DISTINCT FROM b.fixture_xfp_v01
              OR c.prediction_complete IS DISTINCT FROM b.prediction_complete
              OR c.prior_xg_per_90_used IS DISTINCT FROM b.prior_xg_per_90_used
              OR c.prior_xa_per_90_used IS DISTINCT FROM b.prior_xa_per_90_used"""
    ).fetchone()[0]
    player_mismatch = connection.execute(
        """SELECT count(*) FROM player_gameweek c
           FULL JOIN frozen_baseline_player b USING(season,target_gameweek,element_id)
           WHERE c.element_id IS NULL OR b.element_id IS NULL
              OR c.gameweek_expected_minutes_v01
                   IS DISTINCT FROM b.gameweek_expected_minutes_v01
              OR c.gameweek_appearance_xfp_v01
                   IS DISTINCT FROM b.gameweek_appearance_xfp_v01
              OR c.gameweek_goal_xfp_for_evaluation
                   IS DISTINCT FROM b.gameweek_goal_xfp_for_evaluation
              OR c.gameweek_assist_xfp_for_evaluation
                   IS DISTINCT FROM b.gameweek_assist_xfp_for_evaluation
              OR c.gameweek_xfp_v01 IS DISTINCT FROM b.gameweek_xfp_v01
              OR c.prediction_complete IS DISTINCT FROM b.prediction_complete"""
    ).fetchone()[0]
    if fixture_mismatch or player_mismatch:
        raise HistoricalPreviousSeasonPriorExperimentError(
            "C0 does not reproduce frozen historical baseline semantics "
            f"(fixture={fixture_mismatch}, player-GW={player_mismatch})"
        )


def _prior_eligibility_rows(
    connection: duckdb.DuckDBPyConnection, paths: dict[str, Path]
) -> list[tuple[Any, ...]]:
    connection.execute(
        """CREATE TEMP TABLE prior_identity AS SELECT * FROM read_parquet(?)""",
        [str(paths[f"{PRIOR_SEASON}_historical_player_identity"])],
    )
    connection.execute(
        """CREATE TEMP TABLE prior_performance AS SELECT * FROM read_parquet(?)""",
        [str(paths[f"{PRIOR_SEASON}_historical_player_fixture"])],
    )
    connection.execute(
        """CREATE TEMP TABLE prior_exceptions AS SELECT * FROM read_parquet(?)""",
        [str(paths[f"{PRIOR_SEASON}_historical_reconciliation_exceptions"])],
    )
    prior_cursor = connection.execute(
        """SELECT i.code,count(DISTINCT i.element_id)::INTEGER identity_count,
             min(i.element_id)::BIGINT element_id,min(i.position) AS "position",
             count(DISTINCT p.historical_team_id)::INTEGER club_count,
             count(p.fixture_id)::BIGINT source_rows,
             sum(p.minutes)::BIGINT AS "minutes",
             sum(p.xg) FILTER(WHERE p.xg IS NOT NULL AND isfinite(p.xg)) AS xg,
             sum(p.xa) FILTER(WHERE p.xa IS NOT NULL AND isfinite(p.xa)) AS xa,
             count(*) FILTER(WHERE p.fixture_id IS NOT NULL AND p.xg IS NULL)::BIGINT null_xg_rows,
             count(*) FILTER(WHERE p.fixture_id IS NOT NULL AND p.xa IS NULL)::BIGINT null_xa_rows,
             count(*) FILTER(WHERE p.xg IS NOT NULL AND NOT isfinite(p.xg))::BIGINT nonfinite_xg_rows,
             count(*) FILTER(WHERE p.xa IS NOT NULL AND NOT isfinite(p.xa))::BIGINT nonfinite_xa_rows,
             count(DISTINCT e.field)::BIGINT exception_rows
           FROM prior_identity i
           LEFT JOIN prior_performance p USING(season,element_id,code)
           LEFT JOIN prior_exceptions e USING(season,element_id,code)
           GROUP BY i.code"""
    )
    prior_columns = [item[0] for item in prior_cursor.description]
    priors = {
        row[0]: dict(zip(prior_columns, row, strict=True))
        for row in prior_cursor.fetchall()
    }
    target_cursor = connection.execute(
        """WITH target AS (
             SELECT element_id,min(code)::BIGINT code,min("position") AS "position",
                    count(DISTINCT "position")::INTEGER target_position_count
             FROM gameweek_predictions GROUP BY element_id
           ), code_counts AS (
             SELECT code,count(*)::INTEGER target_code_count FROM target GROUP BY code
           )
           SELECT t.*,coalesce(c.target_code_count,0) target_code_count
           FROM target t LEFT JOIN code_counts c USING(code)
           ORDER BY element_id"""
    )
    target_columns = [item[0] for item in target_cursor.description]
    rows: list[tuple[Any, ...]] = []
    for values in target_cursor.fetchall():
        target = dict(zip(target_columns, values, strict=True))
        prior = priors.get(target["code"])
        reason = _prior_reason(target, prior)
        eligible = reason == "eligible"
        prior_minutes = prior.get("minutes") if prior else None
        prior_xg = prior.get("xg") if prior else None
        prior_xa = prior.get("xa") if prior else None
        xg_rate = (
            90.0 * float(prior_xg) / float(prior_minutes)
            if eligible
            else None
        )
        xa_rate = (
            90.0 * float(prior_xa) / float(prior_minutes)
            if eligible
            else None
        )
        rows.append(
            (
                DEVELOPMENT_SEASON,
                target["element_id"],
                target["code"],
                target["position"],
                target["target_position_count"],
                target["target_code_count"],
                prior.get("element_id") if prior else None,
                prior.get("position") if prior else None,
                prior_minutes,
                prior_xg,
                prior_xa,
                xg_rate,
                xa_rate,
                prior.get("club_count") if prior else None,
                prior.get("source_rows") if prior else None,
                prior.get("null_xg_rows") if prior else None,
                prior.get("null_xa_rows") if prior else None,
                prior.get("nonfinite_xg_rows") if prior else None,
                prior.get("nonfinite_xa_rows") if prior else None,
                prior.get("exception_rows") if prior else None,
                eligible,
                reason,
                "audited_unique_fpl_code",
                PSEUDO_MINUTES,
            )
        )
    return rows


def _create_candidate_predictions(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """CREATE TABLE development_candidate_fixture AS
           WITH candidates(candidate) AS (VALUES ('C0'),('C1')),
           rates AS (
             SELECT b.*,p.eligible prior_eligible,p.eligibility_reason,
               p.prior_element_id,p.prior_position,p.prior_minutes,
               p.prior_xg,p.prior_xa,p.prior_xg_per_90,p.prior_xa_per_90,
               p.prior_club_count,c.candidate,
               c.candidate='C1' AND p.eligible AND b.target_has_fixture
                 AND b.prediction_complete AS prior_applied,
               CASE
                 WHEN c.candidate='C0' THEN 'control'
                 WHEN NOT p.eligible THEN p.eligibility_reason
                 WHEN NOT b.target_has_fixture THEN 'verified_blank'
                 WHEN NOT b.prediction_complete THEN 'current_attacking_rate_incomplete'
                 ELSE 'prior_applied'
               END prior_application_status
             FROM development_c0_fixture b
             JOIN player_prior_eligibility p USING(element_id)
             CROSS JOIN candidates c
           ), blended AS (
             SELECT *,
               CASE WHEN prior_applied THEN
                 90.0*(cumulative_prior_xg+?*prior_xg_per_90/90.0)
                 /(prior_total_minutes+?)
               ELSE prior_xg_per_90_used END candidate_xg_per_90,
               CASE WHEN prior_applied THEN
                 90.0*(cumulative_prior_xa+?*prior_xa_per_90/90.0)
                 /(prior_total_minutes+?)
               ELSE prior_xa_per_90_used END candidate_xa_per_90
             FROM rates
           ), components AS (
             SELECT *,
               CASE WHEN candidate='C0' OR NOT prior_applied THEN goal_xfp_v01
                    WHEN candidate_xg_per_90 IS NOT NULL
                     AND expected_minutes_v01 IS NOT NULL
                    THEN candidate_xg_per_90*expected_minutes_v01/90.0
                         *goal_points_for_position END candidate_goal_xfp,
               CASE WHEN candidate='C0' OR NOT prior_applied THEN assist_xfp_v01
                    WHEN candidate_xa_per_90 IS NOT NULL
                     AND expected_minutes_v01 IS NOT NULL
                    THEN candidate_xa_per_90*expected_minutes_v01/90.0*3.0 END
                 candidate_assist_xfp
             FROM blended
           )
           SELECT ?::VARCHAR phase,season,candidate,target_gameweek,fixture_id,
             target_has_fixture,target_fixture_count,element_id,code,"position",team_id,
             team_name,opponent_team_id,home_away,kickoff_time,target_deadline,
             expected_minutes_v01 candidate_expected_minutes,
             appearance_xfp_v01 candidate_appearance_xfp,
             prior_total_minutes current_prior_minutes,
             cumulative_prior_xg current_prior_xg,
             cumulative_prior_xa current_prior_xa,
             prior_xg_per_90_used c0_xg_per_90,
             prior_xa_per_90_used c0_xa_per_90,
             candidate_xg_per_90,candidate_xa_per_90,
             prior_eligible,eligibility_reason,prior_applied,prior_application_status,
             prior_element_id,prior_position,prior_minutes,prior_xg,prior_xa,
             prior_xg_per_90,prior_xa_per_90,prior_club_count,
             candidate_goal_xfp,candidate_assist_xfp,
             CASE WHEN target_has_fixture AND appearance_xfp_v01 IS NOT NULL
                  THEN appearance_xfp_v01+coalesce(candidate_goal_xfp,0.0)
                       +coalesce(candidate_assist_xfp,0.0)
                  WHEN NOT target_has_fixture THEN NULL END candidate_fixture_xfp,
             prediction_complete,attacking_rate_available,availability_status,
             chance_of_playing_next_round,availability_known_pre_deadline,
             availability_forced_zero,availability_gate_reason,
             previous_gameweek_minutes_uncapped,previous_gw_context_status,
             previous_gw_team_blank,previous_gw_player_not_in_universe,
             history_cutoff_rule,historical_classification
           FROM components""",
        [
            PSEUDO_MINUTES,
            PSEUDO_MINUTES,
            PSEUDO_MINUTES,
            PSEUDO_MINUTES,
            "development",
        ],
    )
    connection.execute(
        """CREATE TABLE development_candidate_player_gameweek AS
           WITH aggregated AS (
             SELECT phase,season,candidate,target_gameweek,element_id,
               min(code) code,min("position") "position",
               count(*) FILTER(WHERE target_has_fixture) fixture_count,
               CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                    WHEN count(candidate_expected_minutes) FILTER(WHERE target_has_fixture)
                         =count(*) FILTER(WHERE target_has_fixture)
                    THEN sum(candidate_expected_minutes) FILTER(WHERE target_has_fixture) END
                 gameweek_expected_minutes,
               CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                    WHEN count(candidate_appearance_xfp) FILTER(WHERE target_has_fixture)
                         =count(*) FILTER(WHERE target_has_fixture)
                    THEN sum(candidate_appearance_xfp) FILTER(WHERE target_has_fixture) END
                 gameweek_appearance_xfp,
               CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                    WHEN count(candidate_goal_xfp) FILTER(WHERE target_has_fixture)
                         =count(*) FILTER(WHERE target_has_fixture)
                    THEN sum(candidate_goal_xfp) FILTER(WHERE target_has_fixture) END
                 gameweek_goal_xfp,
               CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN 0.0
                    WHEN count(candidate_assist_xfp) FILTER(WHERE target_has_fixture)
                         =count(*) FILTER(WHERE target_has_fixture)
                    THEN sum(candidate_assist_xfp) FILTER(WHERE target_has_fixture) END
                 gameweek_assist_xfp,
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
                    THEN sum(candidate_fixture_xfp) FILTER(WHERE target_has_fixture) END
                 gameweek_xfp,
               min(current_prior_minutes) current_prior_minutes,
               min(current_prior_xg) current_prior_xg,
               min(current_prior_xa) current_prior_xa,
               min(c0_xg_per_90) c0_xg_per_90,
               min(c0_xa_per_90) c0_xa_per_90,
               min(candidate_xg_per_90) candidate_xg_per_90,
               min(candidate_xa_per_90) candidate_xa_per_90,
               bool_or(prior_eligible) prior_eligible,
               min(eligibility_reason) eligibility_reason,
               bool_or(prior_applied) prior_applied,
               min(prior_application_status) prior_application_status,
               min(prior_minutes) prior_minutes,min(prior_xg) prior_xg,
               min(prior_xa) prior_xa,min(prior_position) prior_position,
               CASE WHEN count(*) FILTER(WHERE target_has_fixture)=0 THEN true
                    ELSE bool_and(prediction_complete) FILTER(WHERE target_has_fixture) END
                 prediction_complete,
               min(availability_status) availability_status,
               min(chance_of_playing_next_round) chance_of_playing_next_round,
               min(target_deadline) target_deadline,
               min(history_cutoff_rule) history_cutoff_rule,
               min(historical_classification) historical_classification
             FROM development_candidate_fixture
             GROUP BY phase,season,candidate,target_gameweek,element_id
           )
           SELECT a.*,b.actual_fixture_count,b.actual_minutes,b.actual_appearance_points,
             b.actual_goal_points,b.actual_assist_points,
             b.actual_goal_points+b.actual_assist_points actual_attacking_points,
             b.actual_modeled_points,b.actual_full_fpl_points,b.actual_state
           FROM aggregated a
           JOIN player_gameweek b USING(season,target_gameweek,element_id)"""
    )
    _validate_candidate_predictions(connection)


def _validate_candidate_predictions(connection: duckdb.DuckDBPyConnection) -> None:
    checks = (
        (
            "C1 altered expected minutes, appearance, completeness, or availability",
            """SELECT count(*) FROM development_candidate_fixture c
               JOIN development_candidate_fixture b USING(
                 season,target_gameweek,element_id,fixture_id)
               WHERE b.candidate='C0' AND c.candidate='C1' AND (
                 c.candidate_expected_minutes IS DISTINCT FROM b.candidate_expected_minutes
                 OR c.candidate_appearance_xfp IS DISTINCT FROM b.candidate_appearance_xfp
                 OR c.prediction_complete IS DISTINCT FROM b.prediction_complete
                 OR c.availability_status IS DISTINCT FROM b.availability_status
                 OR c.chance_of_playing_next_round
                      IS DISTINCT FROM b.chance_of_playing_next_round)""",
        ),
        (
            "C1 changed a fallback row instead of reverting to C0",
            """SELECT count(*) FROM development_candidate_fixture c
               JOIN development_candidate_fixture b USING(
                 season,target_gameweek,element_id,fixture_id)
               WHERE b.candidate='C0' AND c.candidate='C1' AND NOT c.prior_applied
                 AND (c.candidate_xg_per_90 IS DISTINCT FROM b.candidate_xg_per_90
                   OR c.candidate_xa_per_90 IS DISTINCT FROM b.candidate_xa_per_90
                   OR c.candidate_goal_xfp IS DISTINCT FROM b.candidate_goal_xfp
                   OR c.candidate_assist_xfp IS DISTINCT FROM b.candidate_assist_xfp
                   OR c.candidate_fixture_xfp IS DISTINCT FROM b.candidate_fixture_xfp)""",
        ),
        (
            "C1 completed an incomplete current attacking projection",
            """SELECT count(*) FROM development_candidate_fixture
               WHERE candidate='C1' AND NOT prediction_complete AND prior_applied""",
        ),
        (
            "C1 used an invalid player-prior join",
            """SELECT count(*) FROM development_candidate_fixture
               WHERE candidate='C1' AND prior_applied
                 AND (NOT prior_eligible OR prior_minutes<450
                   OR prior_position<>"position")""",
        ),
        (
            "historical chronology changed",
            """SELECT count(*) FROM development_candidate_fixture
               WHERE history_cutoff_rule<>? OR historical_classification<>?""",
        ),
    )
    for message, query in checks:
        parameters = (
            [HISTORY_CUTOFF_RULE, HISTORICAL_CLASSIFICATION]
            if "?" in query
            else []
        )
        if connection.execute(query, parameters).fetchone()[0]:
            raise HistoricalPreviousSeasonPriorExperimentError(message)
    c0_mismatch = connection.execute(
        """SELECT count(*) FROM development_candidate_player_gameweek c
           JOIN player_gameweek b USING(season,target_gameweek,element_id)
           WHERE c.candidate='C0' AND (
             c.gameweek_expected_minutes IS DISTINCT FROM
               b.gameweek_expected_minutes_for_evaluation
             OR c.gameweek_appearance_xfp IS DISTINCT FROM
               b.gameweek_appearance_xfp_for_evaluation
             OR c.gameweek_goal_xfp IS DISTINCT FROM
               b.gameweek_goal_xfp_for_evaluation
             OR c.gameweek_assist_xfp IS DISTINCT FROM
               b.gameweek_assist_xfp_for_evaluation
             OR c.gameweek_xfp IS DISTINCT FROM b.gameweek_xfp_v01
             OR c.prediction_complete IS DISTINCT FROM b.prediction_complete)"""
    ).fetchone()[0]
    if c0_mismatch:
        raise HistoricalPreviousSeasonPriorExperimentError(
            f"C0 candidate failed frozen baseline reproduction in {c0_mismatch} rows"
        )


def _scope_matches(row: dict[str, Any], scope_type: str, scope_value: str) -> bool:
    gameweek = int(row["target_gameweek"])
    minutes = row["current_prior_minutes"]
    if scope_type == "overall":
        return True
    if scope_type == "gameweek_period":
        if scope_value == "GW2-5":
            return 2 <= gameweek <= 5
        if scope_value == "GW6-10":
            return 6 <= gameweek <= 10
        if scope_value == "GW11+":
            return gameweek >= 11
    if scope_type == "current_prior_minutes":
        if scope_value == "missing":
            return minutes is None
        if minutes is None:
            return False
        if scope_value == "0":
            return minutes == 0
        if scope_value == "1-90":
            return 1 <= minutes <= 90
        if scope_value == "91-270":
            return 91 <= minutes <= 270
        if scope_value == "271-450":
            return 271 <= minutes <= 450
        if scope_value == "451+":
            return minutes >= 451
    raise HistoricalPreviousSeasonPriorExperimentError(
        f"unknown diagnostic scope: {scope_type}/{scope_value}"
    )


def _reduction(control: float | None, candidate: float | None) -> float | None:
    if control is None or candidate is None or control == 0:
        return None
    return 100.0 * (control - candidate) / control


def _development_metric_rows(
    rows: Sequence[dict[str, Any]],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    scopes = list(PERIOD_SCOPES) + [
        ("current_prior_minutes", band) for band in PRIOR_MINUTE_BANDS
    ]
    metrics: list[tuple[Any, ...]] = []
    common: list[tuple[Any, ...]] = []
    coverage: list[tuple[Any, ...]] = []
    for scope_type, scope_value in scopes:
        members = [
            row
            for row in rows
            if row["actual_minutes"] is not None
            and row["actual_minutes"] > 0
            and _scope_matches(row, scope_type, scope_value)
        ]
        indexed = {
            candidate: {
                (row["target_gameweek"], row["element_id"]): row
                for row in members
                if row["candidate"] == candidate
            }
            for candidate in CANDIDATES
        }
        for target, (prediction, actual) in TARGET_FIELDS.items():
            for candidate in CANDIDATES:
                metric = _metric(list(indexed[candidate].values()), prediction, actual)
                metrics.append(
                    (
                        scope_type,
                        scope_value,
                        "played",
                        candidate,
                        target,
                        metric["n_eligible"],
                        metric["n_complete_pairs"],
                        metric["missing_prediction"],
                        metric["missing_actual"],
                        metric["coverage_pct"],
                        metric["mae"],
                        metric["rmse"],
                        metric["bias"],
                        metric["spearman"],
                    )
                )
            keys = [
                key
                for key, c0 in indexed["C0"].items()
                if key in indexed["C1"]
                and c0[prediction] is not None
                and indexed["C1"][key][prediction] is not None
                and c0[actual] is not None
                and indexed["C1"][key][actual] is not None
            ]
            c0_metric = _metric([indexed["C0"][key] for key in keys], prediction, actual)
            c1_metric = _metric([indexed["C1"][key] for key in keys], prediction, actual)
            spearman_change = (
                c1_metric["spearman"] - c0_metric["spearman"]
                if c0_metric["spearman"] is not None
                and c1_metric["spearman"] is not None
                else None
            )
            common.append(
                (
                    scope_type,
                    scope_value,
                    "played_common_pair",
                    target,
                    len(keys),
                    c0_metric["mae"],
                    c1_metric["mae"],
                    _reduction(c0_metric["mae"], c1_metric["mae"]),
                    c0_metric["rmse"],
                    c1_metric["rmse"],
                    _reduction(c0_metric["rmse"], c1_metric["rmse"]),
                    c0_metric["bias"],
                    c1_metric["bias"],
                    (
                        abs(c1_metric["bias"]) - abs(c0_metric["bias"])
                        if c0_metric["bias"] is not None
                        and c1_metric["bias"] is not None
                        else None
                    ),
                    c0_metric["spearman"],
                    c1_metric["spearman"],
                    spearman_change,
                )
            )
            c0_keys = {
                key
                for key, row in indexed["C0"].items()
                if row[prediction] is not None and row[actual] is not None
            }
            for candidate in CANDIDATES:
                metric = _metric(list(indexed[candidate].values()), prediction, actual)
                expanded = sum(
                    key not in c0_keys
                    and row[prediction] is not None
                    and row[actual] is not None
                    for key, row in indexed[candidate].items()
                )
                coverage.append(
                    (
                        scope_type,
                        scope_value,
                        "played",
                        target,
                        candidate,
                        metric["n_eligible"],
                        metric["n_complete_pairs"],
                        metric["missing_prediction"],
                        metric["missing_actual"],
                        metric["coverage_pct"],
                        expanded,
                    )
                )
    return metrics, common, coverage


def _common_lookup(
    common: Sequence[tuple[Any, ...]], scope: str, target: str
) -> dict[str, Any]:
    scope_type = "overall" if scope == "overall" else "gameweek_period"
    for row in common:
        if row[0] == scope_type and row[1] == scope and row[3] == target:
            return {
                "n": row[4],
                "c0_mae": row[5],
                "c1_mae": row[6],
                "mae_improvement": row[7],
                "c0_rmse": row[8],
                "c1_rmse": row[9],
                "rmse_improvement": row[10],
                "c0_bias": row[11],
                "c1_bias": row[12],
                "absolute_bias_increase": row[13],
                "c0_spearman": row[14],
                "c1_spearman": row[15],
                "spearman_change": row[16],
            }
    raise HistoricalPreviousSeasonPriorExperimentError(
        f"missing common-pair metric for {scope}/{target}"
    )


def _coverage_lookup(
    coverage: Sequence[tuple[Any, ...]], scope: str, candidate: str
) -> float | None:
    scope_type = "overall" if scope == "overall" else "gameweek_period"
    for row in coverage:
        if (
            row[0] == scope_type
            and row[1] == scope
            and row[3] == "modeled_xfp"
            and row[4] == candidate
        ):
            return row[9]
    raise HistoricalPreviousSeasonPriorExperimentError(
        f"missing coverage metric for {scope}/{candidate}"
    )


def evaluate_development_gates(
    common: Sequence[tuple[Any, ...]], coverage: Sequence[tuple[Any, ...]]
) -> tuple[list[tuple[Any, ...]], bool]:
    gates: list[tuple[Any, ...]] = []

    def add(
        group: str,
        gate_id: str,
        c0: float | None,
        c1: float | None,
        change: float | None,
        comparison: str,
        threshold: float,
        passed: bool,
        detail: str,
    ) -> None:
        gates.append((group, gate_id, c0, c1, change, comparison, threshold, passed, detail))

    early_attack = _common_lookup(common, "GW2-5", "attacking_combined")
    early_modeled = _common_lookup(common, "GW2-5", "modeled_xfp")
    early_goal = _common_lookup(common, "GW2-5", "goal")
    early_assist = _common_lookup(common, "GW2-5", "assist")
    goal_delta = early_goal["spearman_change"]
    assist_delta = early_assist["spearman_change"]
    defined_early_rank = goal_delta is not None and assist_delta is not None
    best_delta = max(goal_delta, assist_delta) if defined_early_rank else None
    other_delta = min(goal_delta, assist_delta) if defined_early_rank else None
    add("GW2-5", "combined_attacking_mae_improves_5pct", early_attack["c0_mae"], early_attack["c1_mae"], early_attack["mae_improvement"], ">=", 5.0, early_attack["mae_improvement"] is not None and early_attack["mae_improvement"] >= 5.0, "positive change is improvement")
    add("GW2-5", "combined_attacking_rmse_improves_3pct", early_attack["c0_rmse"], early_attack["c1_rmse"], early_attack["rmse_improvement"], ">=", 3.0, early_attack["rmse_improvement"] is not None and early_attack["rmse_improvement"] >= 3.0, "positive change is improvement")
    add("GW2-5", "best_goal_or_assist_spearman_improves_0_02", max(early_goal["c0_spearman"], early_assist["c0_spearman"]) if defined_early_rank else None, max(early_goal["c1_spearman"], early_assist["c1_spearman"]) if defined_early_rank else None, best_delta, ">=", 0.02, best_delta is not None and best_delta >= 0.02, "maximum of goal and assist Spearman changes")
    add("GW2-5", "other_component_spearman_declines_no_more_0_01", min(early_goal["c0_spearman"], early_assist["c0_spearman"]) if defined_early_rank else None, min(early_goal["c1_spearman"], early_assist["c1_spearman"]) if defined_early_rank else None, other_delta, ">=", -0.01, other_delta is not None and other_delta >= -0.01, "minimum of goal and assist Spearman changes")
    add("GW2-5", "modeled_xfp_mae_improves_3pct", early_modeled["c0_mae"], early_modeled["c1_mae"], early_modeled["mae_improvement"], ">=", 3.0, early_modeled["mae_improvement"] is not None and early_modeled["mae_improvement"] >= 3.0, "positive change is improvement")
    early_rmse_worsening = -early_modeled["rmse_improvement"] if early_modeled["rmse_improvement"] is not None else None
    add("GW2-5", "modeled_xfp_rmse_does_not_worsen", early_modeled["c0_rmse"], early_modeled["c1_rmse"], early_rmse_worsening, "<=", 0.0, early_rmse_worsening is not None and early_rmse_worsening <= 0.0, "positive change is worsening")

    for scope, prefix in (("GW6-10", "gw6_10"), ("GW11+", "gw11_plus")):
        attack = _common_lookup(common, scope, "attacking_combined")
        modeled = _common_lookup(common, scope, "modeled_xfp")
        goal = _common_lookup(common, scope, "goal")
        assist = _common_lookup(common, scope, "assist")
        attack_worsening = -attack["mae_improvement"] if attack["mae_improvement"] is not None else None
        modeled_mae_worsening = -modeled["mae_improvement"] if modeled["mae_improvement"] is not None else None
        modeled_rmse_worsening = -modeled["rmse_improvement"] if modeled["rmse_improvement"] is not None else None
        for gate_id, c0, c1, change, threshold, detail in (
            ("combined_attacking_mae_worsens_no_more_1pct", attack["c0_mae"], attack["c1_mae"], attack_worsening, 1.0, "positive change is worsening"),
            ("modeled_xfp_mae_worsens_no_more_1pct", modeled["c0_mae"], modeled["c1_mae"], modeled_mae_worsening, 1.0, "positive change is worsening"),
            ("modeled_xfp_rmse_worsens_no_more_1pct", modeled["c0_rmse"], modeled["c1_rmse"], modeled_rmse_worsening, 1.0, "positive change is worsening"),
        ):
            add(scope, f"{prefix}_{gate_id}", c0, c1, change, "<=", threshold, change is not None and change <= threshold, detail)
        for label, metric in (("goal", goal), ("assist", assist), ("modeled_xfp", modeled)):
            change = metric["spearman_change"]
            add(scope, f"{prefix}_{label}_spearman_declines_no_more_0_01", metric["c0_spearman"], metric["c1_spearman"], change, ">=", -0.01, change is not None and change >= -0.01, "candidate minus control Spearman")

    overall_attack = _common_lookup(common, "overall", "attacking_combined")
    overall_modeled = _common_lookup(common, "overall", "modeled_xfp")
    c0_coverage = _coverage_lookup(coverage, "overall", "C0")
    c1_coverage = _coverage_lookup(coverage, "overall", "C1")
    coverage_drop = (
        c0_coverage - c1_coverage
        if c0_coverage is not None and c1_coverage is not None
        else None
    )
    add("overall", "combined_attacking_absolute_bias_increase_no_more_0_05", overall_attack["c0_bias"], overall_attack["c1_bias"], overall_attack["absolute_bias_increase"], "<=", 0.05, overall_attack["absolute_bias_increase"] is not None and overall_attack["absolute_bias_increase"] <= 0.05, "absolute candidate bias minus absolute control bias")
    add("overall", "modeled_xfp_absolute_bias_increase_no_more_0_05", overall_modeled["c0_bias"], overall_modeled["c1_bias"], overall_modeled["absolute_bias_increase"], "<=", 0.05, overall_modeled["absolute_bias_increase"] is not None and overall_modeled["absolute_bias_increase"] <= 0.05, "absolute candidate bias minus absolute control bias")
    add("overall", "prediction_coverage_falls_no_more_1pp", c0_coverage, c1_coverage, coverage_drop, "<=", 1.0, coverage_drop is not None and coverage_drop <= 1.0, "percentage-point coverage drop")
    return gates, all(bool(row[7]) for row in gates)


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
            raise HistoricalPreviousSeasonPriorOutputExistsError(
                f"experiment output already exists and will not be overwritten: {path}"
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _write_outputs(
    connection: duckdb.DuckDBPyConnection,
    *,
    experiment_root: Path,
    manifest_base: dict[str, Any],
    tables: Sequence[str],
) -> tuple[Path, Path]:
    final = experiment_root / EXPERIMENT_VERSION
    if final.exists():
        raise HistoricalPreviousSeasonPriorOutputExistsError(
            f"experiment output already exists and will not be overwritten: {final}"
        )
    stage = experiment_root / f".{EXPERIMENT_VERSION}.{uuid.uuid4().hex}.tmp"
    stage.mkdir(parents=True, exist_ok=False)
    outputs = []
    try:
        for table in tables:
            path = stage / f"{table}.parquet"
            connection.execute(
                f'COPY "{table}" TO ? (FORMAT PARQUET, COMPRESSION ZSTD)', [str(path)]
            )
            outputs.append(
                {
                    "path": path.name,
                    "rows": connection.execute(
                        f'SELECT count(*) FROM "{table}"'
                    ).fetchone()[0],
                    "bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
        manifest_path = stage / "experiment_manifest.json"
        _write_exclusive(
            manifest_path,
            json.dumps(
                {**manifest_base, "outputs": outputs}, indent=2, sort_keys=True
            ).encode("utf-8")
            + b"\n",
        )
        experiment_root.mkdir(parents=True, exist_ok=True)
        try:
            stage.rename(final)
        except FileExistsError as exc:
            raise HistoricalPreviousSeasonPriorOutputExistsError(
                f"experiment output already exists and will not be overwritten: {final}"
            ) from exc
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return final, final / "experiment_manifest.json"


def run_previous_season_prior_development_experiment(
    *,
    historical_clean_root: Path = Path("data/historical/clean"),
    baseline_root: Path = Path("data/historical/backtests"),
    experiment_root: Path = Path("data/historical/experiments"),
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> HistoricalPreviousSeasonPriorExperimentResult:
    final = experiment_root / EXPERIMENT_VERSION
    if final.exists():
        raise HistoricalPreviousSeasonPriorOutputExistsError(
            f"experiment output already exists and will not be overwritten: {final}"
        )
    paths, protected_hashes = _validate_experiment_inputs(
        historical_clean_root=historical_clean_root, baseline_root=baseline_root
    )
    connection = duckdb.connect(":memory:")
    try:
        _load_development_c0(connection, paths)
        prior_rows = _prior_eligibility_rows(connection, paths)
        _create_rows_table(
            connection,
            "player_prior_eligibility",
            PRIOR_ELIGIBILITY_SCHEMA,
            prior_rows,
        )
        reason_counts = Counter((row[21], row[20]) for row in prior_rows)
        _create_rows_table(
            connection,
            "prior_eligibility_reason_counts",
            PRIOR_REASON_SCHEMA,
            [
                (reason, eligible, count)
                for (reason, eligible), count in sorted(reason_counts.items())
            ],
        )
        _create_candidate_predictions(connection)
        player_rows = _fetch_dicts(connection, "development_candidate_player_gameweek")
        metrics, common, coverage = _development_metric_rows(player_rows)
        gates, development_passed = evaluate_development_gates(common, coverage)
        _create_rows_table(connection, "development_metrics", METRIC_SCHEMA, metrics)
        _create_rows_table(
            connection, "development_common_pair_metrics", COMMON_METRIC_SCHEMA, common
        )
        _create_rows_table(
            connection, "development_coverage", COVERAGE_SCHEMA, coverage
        )
        _create_rows_table(connection, "development_gates", GATE_SCHEMA, gates)
        if any(_sha256(path) != digest for path, digest in protected_hashes.items()):
            raise HistoricalPreviousSeasonPriorExperimentError(
                "an immutable experiment input changed during development evaluation"
            )
        eligible_count = sum(bool(row[20]) for row in prior_rows)
        manifest = {
            "status": "complete",
            "experiment_version": EXPERIMENT_VERSION,
            "experiment_scope": "development_only",
            "historical_classification": HISTORICAL_CLASSIFICATION,
            "historical_input_version": HISTORICAL_VERSION,
            "historical_manifest_sha256": protected_hashes[paths["historical_manifest"]],
            "model_formula_frozen": "xfp_v01",
            "live_model_modified": False,
            "candidate_definitions": {
                "C0": "frozen xFP v0.1 semantics",
                "C1": "C0 with only eligible player-specific previous-season xG/90 and xA/90 blended using 450 pseudo-minutes",
            },
            "pseudo_minutes": PSEUDO_MINUTES,
            "prior_season": PRIOR_SEASON,
            "development_season": DEVELOPMENT_SEASON,
            "development_gameweeks": list(TARGET_GAMEWEEKS),
            "holdout_season": SEALED_HOLDOUT_SEASON,
            "holdout_evaluated": False,
            "holdout_input_files_read": [],
            "prospective_2026_27_gw2_evaluated": False,
            "cross_season_join_key": "audited_unique_fpl_code",
            "cross_season_element_id_join_prohibited": True,
            "team_changes_invalidate_prior": False,
            "previous_season_clubs_aggregated": True,
            "position_match_required": True,
            "minimum_previous_season_minutes": 450,
            "missing_previous_statistics_imputed": False,
            "incomplete_current_attacking_projection_completed_by_prior": False,
            "temporal_cutoff_rule": HISTORY_CUTOFF_RULE,
            "same_gameweek_xp_prohibited": True,
            "primary_performance_population": "played player-GWs with C0, C1, and corresponding actual all non-null",
            "expanded_c1_coverage_is_not_a_performance_gate": True,
            "development_thresholds": DEVELOPMENT_THRESHOLDS,
            "development_gate_count": len(gates),
            "development_gates_passed": sum(bool(row[7]) for row in gates),
            "development_pass": development_passed,
            "prior_eligibility": {
                "target_players": len(prior_rows),
                "eligible_players": eligible_count,
                "fallback_players": len(prior_rows) - eligible_count,
                "reason_counts": {
                    reason: count for (reason, _), count in sorted(reason_counts.items())
                },
            },
            "generation_timestamp": _iso_utc(clock()),
            "immutable_inputs": [
                {"path": str(path), "sha256": digest}
                for path, digest in sorted(
                    protected_hashes.items(), key=lambda item: str(item[0])
                )
            ],
        }
        tables = (
            "player_prior_eligibility",
            "prior_eligibility_reason_counts",
            "development_candidate_fixture",
            "development_candidate_player_gameweek",
            "development_metrics",
            "development_common_pair_metrics",
            "development_coverage",
            "development_gates",
        )
        directory, manifest_path = _write_outputs(
            connection,
            experiment_root=experiment_root,
            manifest_base=manifest,
            tables=tables,
        )
    except duckdb.Error as exc:
        raise HistoricalPreviousSeasonPriorExperimentError(
            f"previous-season prior development experiment failed: {exc}"
        ) from exc
    finally:
        connection.close()
    return HistoricalPreviousSeasonPriorExperimentResult(
        directory=directory,
        manifest_path=manifest_path,
        development_passed=development_passed,
        holdout_evaluated=False,
    )
