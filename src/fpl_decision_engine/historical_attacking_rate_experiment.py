"""Preregistered causal attacking-rate stabilization experiment."""

from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from .historical import HISTORICAL_CLASSIFICATION, HISTORY_CUTOFF_RULE
from .historical_minutes_experiment import (
    COMMON_PAIR_SCHEMA,
    DEVELOPMENT_SEASON,
    HOLDOUT_SEASON,
    METRIC_SCHEMA,
    RANKING_SCHEMA,
    TARGET_GAMEWEEKS,
    _create_rows_table,
    _fetch_dicts,
    _iso_utc,
    _metric,
    _ranking_rows,
    _reduction,
    _sha256,
    _validate_inputs,
    _write_exclusive,
)
from .transform import TransformationError


EXPERIMENT_VERSION = "attacking-rate-v02-experiment-v1"
CANDIDATES = ("S0", "S1", "S2", "S3")
TOP_N_VALUES = (10, 25, 50)
DEVELOPMENT_THRESHOLDS = {
    "minimum_goal_spearman_improvement": 0.01,
    "minimum_assist_spearman_improvement": 0.01,
    "maximum_modeled_mae_worsening_pct": 1.0,
    "maximum_modeled_rmse_worsening_pct": 1.0,
    "maximum_absolute_modeled_bias_worsening": 0.02,
    "maximum_coverage_drop_pp": 1.0,
}


class HistoricalAttackingRateExperimentError(TransformationError):
    """Raised when the attacking-rate experiment cannot run safely."""


class HistoricalAttackingRateExperimentOutputExistsError(
    HistoricalAttackingRateExperimentError
):
    """Raised rather than overwriting an immutable experiment directory."""


@dataclass(frozen=True)
class HistoricalAttackingRateExperimentResult:
    directory: Path
    manifest_path: Path
    development_winner: str | None
    holdout_passed: bool | None
    final_decision: str


SELECTION_SCHEMA = (
    ("candidate", "VARCHAR"), ("development_qualifies", "BOOLEAN"),
    ("goal_spearman_improvement", "DOUBLE"),
    ("assist_spearman_improvement", "DOUBLE"),
    ("mean_attacking_spearman_improvement", "DOUBLE"),
    ("modeled_mae_change_pct", "DOUBLE"),
    ("modeled_rmse_change_pct", "DOUBLE"),
    ("absolute_modeled_bias_worsening", "DOUBLE"),
    ("coverage_drop_pp", "DOUBLE"),
    ("modeled_mae", "DOUBLE"), ("modeled_rmse", "DOUBLE"),
    ("selected_for_holdout", "BOOLEAN"), ("holdout_passed", "BOOLEAN"),
    ("holdout_goal_spearman_improvement", "DOUBLE"),
    ("holdout_assist_spearman_improvement", "DOUBLE"),
    ("holdout_modeled_mae_change_pct", "DOUBLE"),
    ("holdout_modeled_rmse_change_pct", "DOUBLE"),
    ("holdout_absolute_modeled_bias_worsening", "DOUBLE"),
    ("holdout_coverage_drop_pp", "DOUBLE"),
)

RATE_CHANGE_SCHEMA = (
    ("phase", "VARCHAR"), ("season", "VARCHAR"),
    ("candidate", "VARCHAR"), ("prior_minutes_band", "VARCHAR"),
    ("rate", "VARCHAR"), ("n_rows", "BIGINT"),
    ("n_raw_present", "BIGINT"), ("n_candidate_present", "BIGINT"),
    ("n_common_rates", "BIGINT"), ("mean_raw_rate", "DOUBLE"),
    ("mean_candidate_rate", "DOUBLE"), ("mean_signed_change", "DOUBLE"),
    ("mean_absolute_change", "DOUBLE"), ("max_absolute_change", "DOUBLE"),
)

TARGET_FIELDS = {
    "goal": ("gameweek_goal_xfp", "actual_goal_points"),
    "assist": ("gameweek_assist_xfp", "actual_assist_points"),
    "attacking_combined": ("gameweek_attacking_xfp", "actual_attacking_points"),
    "modeled_xfp": ("gameweek_xfp", "actual_modeled_points"),
}


def stabilized_rate(
    candidate: str,
    *,
    raw_rate: float | None,
    observed_minutes: float | None,
    position_prior: float | None,
) -> float | None:
    """Reference implementation of the exact preregistered candidate formulas."""
    if candidate == "S0":
        return raw_rate
    if observed_minutes is None or position_prior is None:
        return None
    if candidate in ("S1", "S2"):
        k = 450.0 if candidate == "S1" else 900.0
        if observed_minutes == 0:
            return position_prior
        if raw_rate is None:
            return None
        return (raw_rate * observed_minutes + position_prior * k) / (
            observed_minutes + k
        )
    if candidate == "S3":
        return position_prior if observed_minutes < 180 else raw_rate
    raise HistoricalAttackingRateExperimentError(f"unknown candidate: {candidate}")


def aggregate_position_rate(
    values: Sequence[tuple[float | None, float]],
) -> tuple[float | None, float, float | None]:
    """Return numerator, usable-minute denominator, and aggregate per-90 rate."""
    usable = [(value, minutes) for value, minutes in values if value is not None]
    if not usable:
        return None, 0.0, None
    numerator = sum(float(value) for value, _ in usable)
    denominator = sum(float(minutes) for _, minutes in usable)
    return numerator, denominator, 90.0 * numerator / denominator if denominator > 0 else None


def _load_phase_inputs(
    connection: duckdb.DuckDBPyConnection,
    *,
    phase: str,
    season: str,
    paths: dict[str, Path],
    candidates: Sequence[str],
) -> None:
    if not candidates or any(candidate not in CANDIDATES for candidate in candidates):
        raise HistoricalAttackingRateExperimentError("unknown attacking-rate candidate")
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
        f"""CREATE OR REPLACE TABLE {phase}_history AS
            SELECT * FROM read_parquet(?) WHERE season=?""",
        [str(paths[f"{season}_player_fixture"]), season],
    )
    values = ",".join(f"('{candidate}')" for candidate in candidates)
    connection.execute(
        f"CREATE OR REPLACE TABLE {phase}_candidate_set(candidate) AS VALUES {values}"
    )


def _create_phase_predictions(
    connection: duckdb.DuckDBPyConnection, *, phase: str
) -> tuple[str, str, str]:
    """Build priors and predictions before joining realized target outcomes."""
    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_position_priors AS
            WITH targets AS (
              SELECT DISTINCT season,target_gameweek,"position",target_deadline
              FROM {phase}_base_fixture
            )
            SELECT t.season,t.target_gameweek,t."position",t.target_deadline,
              sum(h.xg) FILTER(WHERE h.xg IS NOT NULL) position_xg_numerator,
              sum(h.minutes) FILTER(WHERE h.xg IS NOT NULL)::DOUBLE position_xg_minutes,
              CASE WHEN sum(h.minutes) FILTER(WHERE h.xg IS NOT NULL)>0
                   THEN 90.0*sum(h.xg) FILTER(WHERE h.xg IS NOT NULL)
                        /sum(h.minutes) FILTER(WHERE h.xg IS NOT NULL) END position_xg_per90,
              sum(h.xa) FILTER(WHERE h.xa IS NOT NULL) position_xa_numerator,
              sum(h.minutes) FILTER(WHERE h.xa IS NOT NULL)::DOUBLE position_xa_minutes,
              CASE WHEN sum(h.minutes) FILTER(WHERE h.xa IS NOT NULL)>0
                   THEN 90.0*sum(h.xa) FILTER(WHERE h.xa IS NOT NULL)
                        /sum(h.minutes) FILTER(WHERE h.xa IS NOT NULL) END position_xa_per90,
              count(h.fixture_id)::BIGINT eligible_fixture_rows,
              max(h.gameweek) history_gameweek_max_used,
              max(h.kickoff_time) history_latest_kickoff_used,
              ?::VARCHAR history_cutoff_rule,
              ?::VARCHAR historical_classification
            FROM targets t LEFT JOIN {phase}_history h
              ON h.season=t.season AND h.historical_position=t."position"
             AND h.gameweek<t.target_gameweek
             AND h.kickoff_time<t.target_deadline
            GROUP BY t.season,t.target_gameweek,t."position",t.target_deadline""",
        [HISTORY_CUTOFF_RULE, HISTORICAL_CLASSIFICATION],
    )
    bad_prior = connection.execute(
        f"""SELECT count(*) FROM {phase}_position_priors
            WHERE history_gameweek_max_used>=target_gameweek
               OR history_latest_kickoff_used>=target_deadline
               OR (position_xg_minutes=0 AND position_xg_per90 IS NOT NULL)
               OR (position_xa_minutes=0 AND position_xa_per90 IS NOT NULL)"""
    ).fetchone()[0]
    if bad_prior:
        raise HistoricalAttackingRateExperimentError(
            f"{phase} causal position-prior validation failed"
        )

    connection.execute(
        f"""CREATE OR REPLACE TABLE {phase}_candidate_fixture AS
            WITH rates AS (
              SELECT b.*,c.candidate,
                p.position_xg_numerator,p.position_xg_minutes,p.position_xg_per90,
                p.position_xa_numerator,p.position_xa_minutes,p.position_xa_per90,
                p.eligible_fixture_rows position_prior_fixture_rows,
                p.history_gameweek_max_used position_prior_gameweek_max_used,
                p.history_latest_kickoff_used position_prior_latest_kickoff_used,
                CASE c.candidate
                  WHEN 'S0' THEN b.prior_xg_per_90_used
                  WHEN 'S1' THEN CASE
                    WHEN p.position_xg_per90 IS NULL OR b.prior_total_minutes IS NULL THEN NULL
                    WHEN b.prior_total_minutes=0 THEN p.position_xg_per90
                    WHEN b.prior_xg_per_90_used IS NULL THEN NULL
                    ELSE (b.prior_xg_per_90_used*b.prior_total_minutes+p.position_xg_per90*450.0)
                         /(b.prior_total_minutes+450.0) END
                  WHEN 'S2' THEN CASE
                    WHEN p.position_xg_per90 IS NULL OR b.prior_total_minutes IS NULL THEN NULL
                    WHEN b.prior_total_minutes=0 THEN p.position_xg_per90
                    WHEN b.prior_xg_per_90_used IS NULL THEN NULL
                    ELSE (b.prior_xg_per_90_used*b.prior_total_minutes+p.position_xg_per90*900.0)
                         /(b.prior_total_minutes+900.0) END
                  WHEN 'S3' THEN CASE
                    WHEN b.prior_total_minutes IS NULL THEN NULL
                    WHEN b.prior_total_minutes<180 THEN p.position_xg_per90
                    ELSE b.prior_xg_per_90_used END END stabilized_xg_per90,
                CASE c.candidate
                  WHEN 'S0' THEN b.prior_xa_per_90_used
                  WHEN 'S1' THEN CASE
                    WHEN p.position_xa_per90 IS NULL OR b.prior_total_minutes IS NULL THEN NULL
                    WHEN b.prior_total_minutes=0 THEN p.position_xa_per90
                    WHEN b.prior_xa_per_90_used IS NULL THEN NULL
                    ELSE (b.prior_xa_per_90_used*b.prior_total_minutes+p.position_xa_per90*450.0)
                         /(b.prior_total_minutes+450.0) END
                  WHEN 'S2' THEN CASE
                    WHEN p.position_xa_per90 IS NULL OR b.prior_total_minutes IS NULL THEN NULL
                    WHEN b.prior_total_minutes=0 THEN p.position_xa_per90
                    WHEN b.prior_xa_per_90_used IS NULL THEN NULL
                    ELSE (b.prior_xa_per_90_used*b.prior_total_minutes+p.position_xa_per90*900.0)
                         /(b.prior_total_minutes+900.0) END
                  WHEN 'S3' THEN CASE
                    WHEN b.prior_total_minutes IS NULL THEN NULL
                    WHEN b.prior_total_minutes<180 THEN p.position_xa_per90
                    ELSE b.prior_xa_per_90_used END END stabilized_xa_per90
              FROM {phase}_base_fixture b CROSS JOIN {phase}_candidate_set c
              JOIN {phase}_position_priors p USING(season,target_gameweek,"position",target_deadline)
            ), components AS (
              SELECT *,
                CASE WHEN candidate='S0' THEN goal_xfp_v01
                     WHEN stabilized_xg_per90 IS NOT NULL AND expected_minutes_v01 IS NOT NULL
                     THEN stabilized_xg_per90*expected_minutes_v01/90.0*goal_points_for_position END candidate_goal_xfp,
                CASE WHEN candidate='S0' THEN assist_xfp_v01
                     WHEN stabilized_xa_per90 IS NOT NULL AND expected_minutes_v01 IS NOT NULL
                     THEN stabilized_xa_per90*expected_minutes_v01/90.0*3.0 END candidate_assist_xfp
              FROM rates
            )
            SELECT ?::VARCHAR phase,season,candidate,target_gameweek,fixture_id,
              target_has_fixture,target_fixture_count,element_id,code,"position",team_id,
              team_name,opponent_team_id,home_away,kickoff_time,target_deadline,
              expected_minutes_v01 candidate_expected_minutes,
              appearance_xfp_v01 candidate_appearance_xfp,
              prior_total_minutes,prior_gameweeks_with_data,
              prior_xg_per_90_used raw_xg_per90,prior_xa_per_90_used raw_xa_per90,
              stabilized_xg_per90,stabilized_xa_per90,
              position_xg_numerator,position_xg_minutes,position_xg_per90,
              position_xa_numerator,position_xa_minutes,position_xa_per90,
              position_prior_fixture_rows,position_prior_gameweek_max_used,
              position_prior_latest_kickoff_used,
              candidate_goal_xfp,candidate_assist_xfp,
              CASE WHEN target_has_fixture AND appearance_xfp_v01 IS NOT NULL
                   THEN appearance_xfp_v01+coalesce(candidate_goal_xfp,0.0)
                        +coalesce(candidate_assist_xfp,0.0)
                   WHEN NOT target_has_fixture THEN NULL END candidate_fixture_xfp,
              target_has_fixture AND appearance_xfp_v01 IS NOT NULL
                AND candidate_goal_xfp IS NOT NULL AND candidate_assist_xfp IS NOT NULL
                prediction_complete,
              attacking_rate_available,availability_status,
              chance_of_playing_next_round,availability_known_pre_deadline,
              availability_forced_zero,availability_gate_reason,
              previous_gameweek_minutes_uncapped,previous_gw_context_status,
              previous_gw_team_blank,previous_gw_player_not_in_universe,
              history_cutoff_rule,historical_classification
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
                min(raw_xg_per90) raw_xg_per90,min(raw_xa_per90) raw_xa_per90,
                min(stabilized_xg_per90) stabilized_xg_per90,
                min(stabilized_xa_per90) stabilized_xa_per90,
                min(position_xg_per90) position_xg_per90,
                min(position_xa_per90) position_xa_per90,
                min(previous_gameweek_minutes_uncapped) previous_gameweek_minutes_uncapped,
                min(availability_status) availability_status,
                min(chance_of_playing_next_round) chance_of_playing_next_round,
                bool_or(availability_forced_zero) availability_forced_zero,
                min(target_deadline) target_deadline,
                min(history_cutoff_rule) history_cutoff_rule,
                min(historical_classification) historical_classification
              FROM {phase}_candidate_fixture
              GROUP BY phase,season,candidate,target_gameweek,element_id
            )
            SELECT a.*,b.gameweek_expected_minutes_for_evaluation baseline_expected_minutes,
              b.gameweek_appearance_xfp_for_evaluation baseline_appearance_xfp,
              b.availability_band,b.actual_fixture_count,b.actual_minutes,
              b.actual_appearance_points,b.actual_goal_points,b.actual_assist_points,
              b.actual_goal_points+b.actual_assist_points actual_attacking_points,
              b.actual_modeled_points,b.actual_full_fpl_points,b.actual_state
            FROM aggregated a JOIN {phase}_base_player b USING(season,target_gameweek,element_id)"""
    )
    invalid = connection.execute(
        f"""SELECT count(*) FROM {phase}_candidate_player
            WHERE historical_classification<>? OR history_cutoff_rule<>?
              OR "position" NOT IN ('GK','DEF','MID','FWD')
              OR gameweek_expected_minutes IS DISTINCT FROM baseline_expected_minutes
              OR gameweek_appearance_xfp IS DISTINCT FROM baseline_appearance_xfp
              OR (fixture_count=0 AND (gameweek_xfp<>0 OR actual_modeled_points<>0))""",
        [HISTORICAL_CLASSIFICATION, HISTORY_CUTOFF_RULE],
    ).fetchone()[0]
    if invalid:
        raise HistoricalAttackingRateExperimentError(
            f"{phase} frozen-component or blank invariance failed in {invalid} rows"
        )
    fixture_invariance = connection.execute(
        f"""SELECT count(*) FROM {phase}_candidate_fixture c
            JOIN {phase}_candidate_fixture s USING(season,target_gameweek,element_id,fixture_id)
            WHERE s.candidate='S0' AND c.candidate<>'S0' AND (
              c.candidate_expected_minutes IS DISTINCT FROM s.candidate_expected_minutes
              OR c.candidate_appearance_xfp IS DISTINCT FROM s.candidate_appearance_xfp
              OR c.availability_status IS DISTINCT FROM s.availability_status
              OR c.chance_of_playing_next_round IS DISTINCT FROM s.chance_of_playing_next_round
              OR c.availability_forced_zero IS DISTINCT FROM s.availability_forced_zero
              OR c.target_has_fixture IS DISTINCT FROM s.target_has_fixture)"""
    ).fetchone()[0]
    if fixture_invariance:
        raise HistoricalAttackingRateExperimentError(
            f"{phase} expected-minutes/availability/non-attacking invariance failed"
        )
    s0_mismatch = connection.execute(
        f"""SELECT count(*) FROM {phase}_candidate_player s
            JOIN {phase}_base_player b USING(season,target_gameweek,element_id)
            WHERE s.candidate='S0' AND (
              s.gameweek_xfp IS DISTINCT FROM b.gameweek_xfp_v01
              OR s.gameweek_goal_xfp IS DISTINCT FROM b.gameweek_goal_xfp_for_evaluation
              OR s.gameweek_assist_xfp IS DISTINCT FROM b.gameweek_assist_xfp_for_evaluation)"""
    ).fetchone()[0]
    if s0_mismatch:
        raise HistoricalAttackingRateExperimentError(
            f"{phase} S0 does not reproduce frozen baseline-v1 in {s0_mismatch} rows"
        )
    return (
        f"{phase}_position_priors",
        f"{phase}_candidate_fixture",
        f"{phase}_candidate_player",
    )


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
    candidates: Sequence[str], populations: Sequence[str] = ("all", "played"),
) -> list[tuple[Any, ...]]:
    output = []
    for population in populations:
        population_rows = [
            row for row in rows
            if population == "all" or (population == "played" and row["actual_minutes"] > 0)
        ]
        for candidate in candidates:
            candidate_rows = [row for row in population_rows if row["candidate"] == candidate]
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
    output = []
    for population in ("all", "played"):
        population_rows = [
            row for row in rows
            if population == "all" or row["actual_minutes"] > 0
        ]
        indexed = {
            candidate: {
                (row["target_gameweek"], row["element_id"]): row
                for row in population_rows if row["candidate"] == candidate
            }
            for candidate in candidates
        }
        baseline = indexed["S0"]
        for candidate in candidates:
            if candidate == "S0":
                continue
            compared = indexed[candidate]
            for target, (prediction, actual) in TARGET_FIELDS.items():
                keys = [
                    key for key, s0 in baseline.items()
                    if key in compared and s0[prediction] is not None
                    and compared[key][prediction] is not None
                    and s0[actual] is not None and compared[key][actual] is not None
                ]
                for predictor, source in (("S0", baseline), (candidate, compared)):
                    metric = _metric([source[key] for key in keys], prediction, actual)
                    output.append(
                        (phase, season, candidate, predictor,
                         f"{population}:{target}", len(keys), metric["mae"],
                         metric["rmse"], metric["bias"], metric["spearman"])
                    )
    return output


def _common_lookup(
    rows: Sequence[tuple[Any, ...]], *, comparison: str, predictor: str,
    population: str, target: str,
) -> dict[str, Any]:
    label = f"{population}:{target}"
    for row in rows:
        if row[2] == comparison and row[3] == predictor and row[4] == label:
            return {"n": row[5], "mae": row[6], "rmse": row[7],
                    "bias": row[8], "spearman": row[9]}
    raise HistoricalAttackingRateExperimentError(
        f"missing common metric {comparison}/{predictor}/{label}"
    )


def _coverage_lookup(
    rows: Sequence[tuple[Any, ...]], *, candidate: str, population: str
) -> float:
    for row in rows:
        if row[2] == candidate and row[3] == population and row[4] == "modeled_xfp":
            return float(row[9])
    raise HistoricalAttackingRateExperimentError("missing modeled prediction coverage")


def select_development_winner(
    metrics: Sequence[tuple[Any, ...]], common: Sequence[tuple[Any, ...]],
) -> tuple[str | None, list[dict[str, Any]]]:
    if any(row[0] != "development" or row[1] != DEVELOPMENT_SEASON for row in metrics):
        raise HistoricalAttackingRateExperimentError("holdout metrics entered development selection")
    if any(row[0] != "development" or row[1] != DEVELOPMENT_SEASON for row in common):
        raise HistoricalAttackingRateExperimentError("holdout common pairs entered development selection")
    records = []
    for candidate in CANDIDATES[1:]:
        goal_s0 = _common_lookup(common, comparison=candidate, predictor="S0", population="played", target="goal")
        goal_c = _common_lookup(common, comparison=candidate, predictor=candidate, population="played", target="goal")
        assist_s0 = _common_lookup(common, comparison=candidate, predictor="S0", population="played", target="assist")
        assist_c = _common_lookup(common, comparison=candidate, predictor=candidate, population="played", target="assist")
        modeled_s0 = _common_lookup(common, comparison=candidate, predictor="S0", population="played", target="modeled_xfp")
        modeled_c = _common_lookup(common, comparison=candidate, predictor=candidate, population="played", target="modeled_xfp")
        goal_improvement = goal_c["spearman"] - goal_s0["spearman"]
        assist_improvement = assist_c["spearman"] - assist_s0["spearman"]
        mae_change = -_reduction(modeled_s0["mae"], modeled_c["mae"])
        rmse_change = -_reduction(modeled_s0["rmse"], modeled_c["rmse"])
        bias_worsening = abs(modeled_c["bias"]) - abs(modeled_s0["bias"])
        coverage_drop = _coverage_lookup(metrics, candidate="S0", population="played") - _coverage_lookup(
            metrics, candidate=candidate, population="played"
        )
        record = {
            "candidate": candidate,
            "goal_spearman_improvement": goal_improvement,
            "assist_spearman_improvement": assist_improvement,
            "mean_attacking_spearman_improvement": (goal_improvement + assist_improvement) / 2,
            "modeled_mae_change_pct": mae_change,
            "modeled_rmse_change_pct": rmse_change,
            "absolute_modeled_bias_worsening": bias_worsening,
            "coverage_drop_pp": coverage_drop,
            "modeled_mae": modeled_c["mae"], "modeled_rmse": modeled_c["rmse"],
        }
        record["development_qualifies"] = (
            goal_improvement >= 0.01 and assist_improvement >= 0.01
            and mae_change <= 1.0 and rmse_change <= 1.0
            and bias_worsening <= 0.02 and coverage_drop <= 1.0
        )
        records.append(record)
    simplicity = {"S1": 0, "S2": 1, "S3": 2}
    qualifying = sorted(
        (record for record in records if record["development_qualifies"]),
        key=lambda record: (-record["mean_attacking_spearman_improvement"],
                            record["modeled_mae"], record["modeled_rmse"],
                            simplicity[record["candidate"]]),
    )
    return (qualifying[0]["candidate"] if qualifying else None), records


def _holdout_candidate_set(winner: str) -> tuple[str, str]:
    if winner not in CANDIDATES[1:]:
        raise HistoricalAttackingRateExperimentError("holdout requires one development winner")
    return "S0", winner


def _holdout_decision(
    metrics: Sequence[tuple[Any, ...]], common: Sequence[tuple[Any, ...]], winner: str,
) -> dict[str, Any]:
    goal_s0 = _common_lookup(common, comparison=winner, predictor="S0", population="played", target="goal")
    goal_c = _common_lookup(common, comparison=winner, predictor=winner, population="played", target="goal")
    assist_s0 = _common_lookup(common, comparison=winner, predictor="S0", population="played", target="assist")
    assist_c = _common_lookup(common, comparison=winner, predictor=winner, population="played", target="assist")
    modeled_s0 = _common_lookup(common, comparison=winner, predictor="S0", population="played", target="modeled_xfp")
    modeled_c = _common_lookup(common, comparison=winner, predictor=winner, population="played", target="modeled_xfp")
    result = {
        "holdout_goal_spearman_improvement": goal_c["spearman"] - goal_s0["spearman"],
        "holdout_assist_spearman_improvement": assist_c["spearman"] - assist_s0["spearman"],
        "holdout_modeled_mae_change_pct": -_reduction(modeled_s0["mae"], modeled_c["mae"]),
        "holdout_modeled_rmse_change_pct": -_reduction(modeled_s0["rmse"], modeled_c["rmse"]),
        "holdout_absolute_modeled_bias_worsening": abs(modeled_c["bias"]) - abs(modeled_s0["bias"]),
        "holdout_coverage_drop_pp": _coverage_lookup(metrics, candidate="S0", population="played") - _coverage_lookup(
            metrics, candidate=winner, population="played"
        ),
    }
    result["holdout_passed"] = (
        result["holdout_goal_spearman_improvement"] >= 0.01
        and result["holdout_assist_spearman_improvement"] >= 0.01
        and result["holdout_modeled_mae_change_pct"] <= 1.0
        and result["holdout_modeled_rmse_change_pct"] <= 1.0
        and result["holdout_absolute_modeled_bias_worsening"] <= 0.02
        and result["holdout_coverage_drop_pp"] <= 1.0
    )
    return result


def _population(row: dict[str, Any], population: str) -> bool:
    minutes = row["prior_total_minutes"]
    if population == "all_complete": return True
    if population == "actual_minutes_gt_0": return row["actual_minutes"] > 0
    if population == "expected_minutes_gt_0": return row["gameweek_expected_minutes"] is not None and row["gameweek_expected_minutes"] > 0
    if population == "prior_minutes_1_90": return minutes is not None and 1 <= minutes <= 90
    if population == "prior_minutes_91_270": return minutes is not None and 91 <= minutes <= 270
    if population == "prior_minutes_271_450": return minutes is not None and 271 <= minutes <= 450
    if population == "prior_minutes_451_900": return minutes is not None and 451 <= minutes <= 900
    if population == "prior_minutes_901_plus": return minutes is not None and minutes >= 901
    if population in ("GK", "DEF", "MID", "FWD"): return row["position"] == population
    if population == "normal_single": return row["fixture_count"] == 1
    if population == "double": return row["fixture_count"] > 1
    if population == "stable":
        return (row["raw_attacking_rate_available"] is True and minutes >= 450
                and row["baseline_expected_minutes"] is not None
                and row["baseline_expected_minutes"] >= 60 and row["fixture_count"] == 1)
    raise ValueError(population)


DIAGNOSTIC_POPULATIONS = (
    "all_complete", "actual_minutes_gt_0", "expected_minutes_gt_0",
    "prior_minutes_1_90", "prior_minutes_91_270", "prior_minutes_271_450",
    "prior_minutes_451_900", "prior_minutes_901_plus",
    "GK", "DEF", "MID", "FWD", "normal_single", "double", "stable",
)


def _diagnostic_metrics(
    rows: Sequence[dict[str, Any]], *, phase: str, season: str,
    candidates: Sequence[str],
) -> list[tuple[Any, ...]]:
    output = []
    for population in DIAGNOSTIC_POPULATIONS:
        subset = [row for row in rows if _population(row, population)]
        for candidate in candidates:
            candidate_rows = [row for row in subset if row["candidate"] == candidate]
            for target, (prediction, actual) in TARGET_FIELDS.items():
                output.append(_metric_tuple(
                    phase=phase, season=season, candidate=candidate,
                    population=population, target=target,
                    metric=_metric(candidate_rows, prediction, actual),
                ))
    return output


def _minutes_band(value: int | None) -> str:
    if value is None: return "missing"
    if value == 0: return "0"
    if value <= 90: return "1-90"
    if value <= 270: return "91-270"
    if value <= 450: return "271-450"
    if value <= 900: return "451-900"
    return "901+"


def _rate_change_rows(
    rows: Sequence[dict[str, Any]], *, phase: str, season: str,
    candidates: Sequence[str],
) -> list[tuple[Any, ...]]:
    output = []
    for candidate in candidates:
        if candidate == "S0": continue
        candidate_rows = [row for row in rows if row["candidate"] == candidate]
        for band in ("0", "1-90", "91-270", "271-450", "451-900", "901+", "missing"):
            members = [row for row in candidate_rows if _minutes_band(row["prior_total_minutes"]) == band]
            for rate, raw_field, candidate_field in (
                ("xg_per90", "raw_xg_per90", "stabilized_xg_per90"),
                ("xa_per90", "raw_xa_per90", "stabilized_xa_per90"),
            ):
                raw = [float(row[raw_field]) for row in members if row[raw_field] is not None]
                stabilized = [float(row[candidate_field]) for row in members if row[candidate_field] is not None]
                common = [(float(row[raw_field]), float(row[candidate_field])) for row in members
                          if row[raw_field] is not None and row[candidate_field] is not None]
                changes = [right-left for left, right in common]
                output.append((
                    phase, season, candidate, band, rate, len(members), len(raw),
                    len(stabilized), len(common), sum(raw)/len(raw) if raw else None,
                    sum(stabilized)/len(stabilized) if stabilized else None,
                    sum(changes)/len(changes) if changes else None,
                    sum(abs(change) for change in changes)/len(changes) if changes else None,
                    max((abs(change) for change in changes), default=None),
                ))
    return output


def _selection_rows(
    development: Sequence[dict[str, Any]], winner: str | None,
    holdout: dict[str, Any] | None,
) -> list[tuple[Any, ...]]:
    output = []
    for record in development:
        selected = record["candidate"] == winner
        held = holdout if selected and holdout else {}
        output.append((
            record["candidate"],record["development_qualifies"],
            record["goal_spearman_improvement"],record["assist_spearman_improvement"],
            record["mean_attacking_spearman_improvement"],record["modeled_mae_change_pct"],
            record["modeled_rmse_change_pct"],record["absolute_modeled_bias_worsening"],
            record["coverage_drop_pp"],record["modeled_mae"],record["modeled_rmse"],selected,
            held.get("holdout_passed"),held.get("holdout_goal_spearman_improvement"),
            held.get("holdout_assist_spearman_improvement"),
            held.get("holdout_modeled_mae_change_pct"),held.get("holdout_modeled_rmse_change_pct"),
            held.get("holdout_absolute_modeled_bias_worsening"),held.get("holdout_coverage_drop_pp"),
        ))
    return output


def _write_outputs(
    connection: duckdb.DuckDBPyConnection, *, experiment_root: Path,
    manifest_base: dict[str, Any], tables: Sequence[str],
) -> tuple[Path, Path]:
    final = experiment_root / EXPERIMENT_VERSION
    if final.exists():
        raise HistoricalAttackingRateExperimentOutputExistsError(
            f"experiment output already exists and will not be overwritten: {final}"
        )
    stage = experiment_root / f".{EXPERIMENT_VERSION}.{uuid.uuid4().hex}.tmp"
    stage.mkdir(parents=True, exist_ok=False)
    outputs = []
    try:
        for table in tables:
            path = stage / f"{table}.parquet"
            connection.execute(f'COPY "{table}" TO ? (FORMAT PARQUET, COMPRESSION ZSTD)', [str(path)])
            outputs.append({"path": path.name,
                            "rows": connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0],
                            "bytes": path.stat().st_size,"sha256": _sha256(path)})
        _write_exclusive(stage/"experiment_manifest.json",
                         json.dumps({**manifest_base,"outputs":outputs},indent=2,sort_keys=True).encode()+b"\n")
        experiment_root.mkdir(parents=True,exist_ok=True)
        try: stage.rename(final)
        except FileExistsError as exc:
            raise HistoricalAttackingRateExperimentOutputExistsError(
                f"experiment output already exists and will not be overwritten: {final}"
            ) from exc
    except Exception:
        shutil.rmtree(stage,ignore_errors=True)
        raise
    return final,final/"experiment_manifest.json"


def run_historical_attacking_rate_experiment(
    *, historical_clean_root: Path = Path("data/historical/clean"),
    baseline_root: Path = Path("data/historical/backtests"),
    experiment_root: Path = Path("data/historical/experiments"),
    clock=lambda: datetime.now(timezone.utc),
) -> HistoricalAttackingRateExperimentResult:
    final = experiment_root/EXPERIMENT_VERSION
    if final.exists():
        raise HistoricalAttackingRateExperimentOutputExistsError(
            f"experiment output already exists and will not be overwritten: {final}"
        )
    try:
        paths,protected_hashes=_validate_inputs(
            historical_clean_root=historical_clean_root,baseline_root=baseline_root)
    except TransformationError as exc:
        raise HistoricalAttackingRateExperimentError(str(exc)) from exc
    connection=duckdb.connect(":memory:")
    try:
        _load_phase_inputs(connection,phase="development",season=DEVELOPMENT_SEASON,
                           paths=paths,candidates=CANDIDATES)
        dev_prior,dev_fixture,dev_player=_create_phase_predictions(connection,phase="development")
        dev_rows=_fetch_dicts(connection,dev_player)
        dev_metrics=_phase_metrics(dev_rows,phase="development",season=DEVELOPMENT_SEASON,candidates=CANDIDATES)
        dev_common=_common_pair_metrics(dev_rows,phase="development",season=DEVELOPMENT_SEASON,candidates=CANDIDATES)
        dev_ranking=_ranking_rows(dev_rows,phase="development",season=DEVELOPMENT_SEASON,candidates=CANDIDATES)
        winner,development_selection=select_development_winner(dev_metrics,dev_common)

        holdout_result=None
        holdout_metrics=[];holdout_common=[];holdout_ranking=[];holdout_rows=[]
        if winner:
            holdout_candidates=_holdout_candidate_set(winner)
            _load_phase_inputs(connection,phase="holdout",season=HOLDOUT_SEASON,
                               paths=paths,candidates=holdout_candidates)
            hold_prior,hold_fixture,hold_player=_create_phase_predictions(connection,phase="holdout")
            holdout_rows=_fetch_dicts(connection,hold_player)
            holdout_metrics=_phase_metrics(holdout_rows,phase="holdout",season=HOLDOUT_SEASON,candidates=holdout_candidates)
            holdout_common=_common_pair_metrics(holdout_rows,phase="holdout",season=HOLDOUT_SEASON,candidates=holdout_candidates)
            holdout_ranking=_ranking_rows(holdout_rows,phase="holdout",season=HOLDOUT_SEASON,candidates=holdout_candidates)
            holdout_result=_holdout_decision(holdout_metrics,holdout_common,winner)
            connection.execute(f"CREATE TABLE candidate_fixture_predictions AS SELECT * FROM {dev_fixture} UNION ALL SELECT * FROM {hold_fixture}")
            connection.execute(f"CREATE TABLE candidate_player_gameweek AS SELECT * FROM {dev_player} UNION ALL SELECT * FROM {hold_player}")
            connection.execute(f"CREATE TABLE causal_position_priors AS SELECT * FROM {dev_prior} UNION ALL SELECT * FROM {hold_prior}")
        else:
            connection.execute(f"CREATE TABLE candidate_fixture_predictions AS SELECT * FROM {dev_fixture}")
            connection.execute(f"CREATE TABLE candidate_player_gameweek AS SELECT * FROM {dev_player}")
            connection.execute(f"CREATE TABLE causal_position_priors AS SELECT * FROM {dev_prior}")

        _create_rows_table(connection,"development_metrics",METRIC_SCHEMA,dev_metrics)
        _create_rows_table(connection,"development_common_pair_metrics",COMMON_PAIR_SCHEMA,dev_common)
        _create_rows_table(connection,"development_ranking",RANKING_SCHEMA,dev_ranking)
        tables=["candidate_fixture_predictions","candidate_player_gameweek","causal_position_priors",
                "development_metrics","development_common_pair_metrics","development_ranking"]
        if winner:
            _create_rows_table(connection,"holdout_metrics",METRIC_SCHEMA,holdout_metrics)
            _create_rows_table(connection,"holdout_common_pair_metrics",COMMON_PAIR_SCHEMA,holdout_common)
            _create_rows_table(connection,"holdout_ranking",RANKING_SCHEMA,holdout_ranking)
            tables += ["holdout_metrics","holdout_common_pair_metrics","holdout_ranking"]
        selected=("S0",winner) if winner else ("S0",)
        diagnostics=_diagnostic_metrics(dev_rows,phase="development",season=DEVELOPMENT_SEASON,candidates=selected)
        if winner:
            diagnostics += _diagnostic_metrics(holdout_rows,phase="holdout",season=HOLDOUT_SEASON,candidates=selected)
        _create_rows_table(connection,"diagnostic_metrics",METRIC_SCHEMA,diagnostics)
        rate_changes=_rate_change_rows(dev_rows,phase="development",season=DEVELOPMENT_SEASON,candidates=CANDIDATES)
        if winner:
            rate_changes += _rate_change_rows(holdout_rows,phase="holdout",season=HOLDOUT_SEASON,candidates=selected)
        _create_rows_table(connection,"rate_change_diagnostics",RATE_CHANGE_SCHEMA,rate_changes)
        _create_rows_table(connection,"selection_decision",SELECTION_SCHEMA,
                           _selection_rows(development_selection,winner,holdout_result))
        tables += ["diagnostic_metrics","rate_change_diagnostics","selection_decision"]

        if any(_sha256(path)!=digest for path,digest in protected_hashes.items()):
            raise HistoricalAttackingRateExperimentError("an immutable input changed during the experiment")
        holdout_passed=holdout_result["holdout_passed"] if holdout_result else None
        decision=("PROMOTE ATTACKING-RATE CANDIDATE TO xFP v0.2 DESIGN"
                  if holdout_passed else "DO NOT PROMOTE — KEEP v0.1 ATTACKING RATES")
        manifest={
            "status":"complete","experiment_version":EXPERIMENT_VERSION,
            "historical_classification":HISTORICAL_CLASSIFICATION,
            "model_formula_frozen":"xfp_v01","live_model_modified":False,
            "development_season":DEVELOPMENT_SEASON,"holdout_season":HOLDOUT_SEASON,
            "target_gameweeks":list(TARGET_GAMEWEEKS),
            "primary_selection_population":"actual_target_minutes > 0, applied only after predictions are generated",
            "candidate_definitions":{
                "S0":"raw cumulative prior xG/90 and xA/90",
                "S1":"(raw_rate*observed_minutes + causal_position_rate*450)/(observed_minutes+450)",
                "S2":"(raw_rate*observed_minutes + causal_position_rate*900)/(observed_minutes+900)",
                "S3":"causal_position_rate when observed_minutes<180, otherwise raw_rate",
            },
            "position_prior_definition":"90*sum(eligible prior event value)/sum(minutes for rows where that event value is non-null), separately for xG and xA",
            "position_prior_uses_player_own_history":True,"league_prior_fallback":False,
            "temporal_cutoff_rule":HISTORY_CUTOFF_RULE,
            "frozen_components":["expected_minutes","availability_hard_gate","blanks","DGWs","position_scoring","appearance","aggregation","evaluation_definitions"],
            "coverage_policy":"Metrics use only non-null prediction/actual pairs; modeled numeric appearance-only predictions retain baseline-v1 semantics; coverage is reported separately.",
            "selection_common_pair_policy":"S0, candidate, and actual all non-null within the played population",
            "development_thresholds":DEVELOPMENT_THRESHOLDS,
            "development_tie_breakers":["mean goal/assist Spearman improvement","lower modeled MAE","lower modeled RMSE","simpler S1 then S2 then S3"],
            "development_winner":winner,"holdout_evaluated":winner is not None,
            "holdout_passed":holdout_passed,"final_decision":decision,
            "generation_timestamp":_iso_utc(clock()),
            "immutable_inputs":[{"path":str(path),"sha256":digest} for path,digest in sorted(protected_hashes.items(),key=lambda item:str(item[0]))],
        }
        directory,manifest_path=_write_outputs(connection,experiment_root=experiment_root,
                                                manifest_base=manifest,tables=tables)
    except duckdb.Error as exc:
        raise HistoricalAttackingRateExperimentError(f"attacking-rate experiment failed: {exc}") from exc
    finally:
        connection.close()
    return HistoricalAttackingRateExperimentResult(directory,manifest_path,winner,holdout_passed,decision)
