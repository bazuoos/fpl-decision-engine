"""Preregistered post-hoc calibration experiment for frozen xFP v0.1."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import shutil
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from .historical import HISTORICAL_CLASSIFICATION
from .historical_backtest import _spearman
from .historical_minutes_experiment import (
    BASELINE_VERSION,
    COMMON_PAIR_SCHEMA,
    DEVELOPMENT_SEASON,
    HISTORICAL_VERSION,
    HOLDOUT_SEASON,
    METRIC_SCHEMA,
    TARGET_GAMEWEEKS,
    _create_rows_table,
    _metric,
    _reduction,
)
from .transform import TransformationError


EXPERIMENT_VERSION = "calibration-v02-experiment-v1"
CANDIDATES = ("C0", "C1", "C2")
TOP_N_VALUES = (10, 25, 50)
TASK_009_VERSION = "minutes-v02-experiment-v1"
TASK_010_VERSION = "attacking-rate-v02-experiment-v1"

DEVELOPMENT_THRESHOLDS = {
    "minimum_rmse_reduction_pct": 3.0,
    "minimum_mae_reduction_pct": 2.0,
    "minimum_absolute_bias_improvement_pct": 20.0,
    "maximum_calibrated_absolute_bias": 0.03,
    "maximum_spearman_decline": 0.005,
    "maximum_top_n_overlap_decline_pp": 1.0,
    "maximum_coverage_drop_pp": 1.0,
}
HOLDOUT_THRESHOLDS = {
    "minimum_rmse_reduction_pct": 2.0,
    "minimum_mae_reduction_pct": 1.0,
    "maximum_absolute_bias_worsening": 0.02,
    "maximum_spearman_decline": 0.005,
    "maximum_top_n_overlap_decline_pp": 1.0,
    "maximum_coverage_drop_pp": 1.0,
}


class HistoricalCalibrationExperimentError(TransformationError):
    """Raised when the calibration experiment cannot run safely."""


class HistoricalCalibrationExperimentOutputExistsError(
    HistoricalCalibrationExperimentError
):
    """Raised rather than overwriting an immutable experiment."""


@dataclass(frozen=True)
class HistoricalCalibrationExperimentResult:
    directory: Path
    manifest_path: Path
    development_winner: str | None
    holdout_passed: bool | None
    final_decision: str


@dataclass(frozen=True)
class LinearCalibration:
    intercept: float
    slope: float
    development_n: int

    def transform(self, value: float) -> float:
        return self.intercept + self.slope * value


@dataclass(frozen=True)
class IsotonicBlock:
    lower_x: float
    upper_x: float
    fitted_y: float
    weight: int


@dataclass(frozen=True)
class IsotonicCalibration:
    blocks: tuple[IsotonicBlock, ...]
    development_n: int

    def transform(self, value: float) -> float:
        """Apply the PAVA step function with constant boundary extension."""
        if not self.blocks:
            raise HistoricalCalibrationExperimentError("empty isotonic mapping")
        boundaries = [
            (left.upper_x + right.lower_x) / 2.0
            for left, right in zip(self.blocks, self.blocks[1:])
        ]
        return self.blocks[bisect.bisect_right(boundaries, value)].fitted_y


PLAYER_SCHEMA = (
    ("phase", "VARCHAR"), ("season", "VARCHAR"),
    ("candidate", "VARCHAR"), ("target_gameweek", "INTEGER"),
    ("element_id", "BIGINT"), ("code", "BIGINT"),
    ("position", "VARCHAR"), ("team_id", "INTEGER"),
    ("team_name", "VARCHAR"), ("fixture_count", "BIGINT"),
    ("raw_xfp_v01", "DOUBLE"), ("calibrated_xfp", "DOUBLE"),
    ("calibration_adjustment", "DOUBLE"),
    ("mapping_fitted_season", "VARCHAR"),
    ("verified_blank_override", "BOOLEAN"),
    ("prediction_complete", "BOOLEAN"),
    ("baseline_prediction_state", "VARCHAR"),
    ("expected_minutes", "DOUBLE"), ("actual_minutes", "DOUBLE"),
    ("actual_modeled_points", "DOUBLE"),
    ("actual_full_fpl_points", "DOUBLE"), ("actual_state", "VARCHAR"),
    ("attacking_rate_available", "BOOLEAN"), ("low_sample", "BOOLEAN"),
    ("prior_total_minutes", "INTEGER"),
    ("prior_gameweeks_with_data", "INTEGER"),
    ("availability_band", "VARCHAR"),
    ("historical_classification", "VARCHAR"),
)

RANKING_SCHEMA = (
    ("phase", "VARCHAR"), ("season", "VARCHAR"),
    ("candidate", "VARCHAR"), ("row_type", "VARCHAR"),
    ("target_gameweek", "INTEGER"), ("top_n", "INTEGER"),
    ("n_complete_pairs", "BIGINT"), ("strict_n_available", "BOOLEAN"),
    ("overlap_count", "DOUBLE"), ("overlap_pct", "DOUBLE"),
    ("gameweeks_summarized", "INTEGER"), ("tie_breaker", "VARCHAR"),
)

COMMON_RANKING_SCHEMA = (
    ("phase", "VARCHAR"), ("season", "VARCHAR"),
    ("comparison_candidate", "VARCHAR"), ("predictor", "VARCHAR"),
    ("row_type", "VARCHAR"), ("target_gameweek", "INTEGER"),
    ("top_n", "INTEGER"), ("n_common_pairs", "BIGINT"),
    ("strict_n_available", "BOOLEAN"), ("overlap_count", "DOUBLE"),
    ("overlap_pct", "DOUBLE"), ("gameweeks_summarized", "INTEGER"),
    ("tie_breaker", "VARCHAR"),
)

CALIBRATION_PARAMETER_SCHEMA = (
    ("candidate", "VARCHAR"), ("method", "VARCHAR"),
    ("development_n", "BIGINT"), ("intercept", "DOUBLE"),
    ("slope", "DOUBLE"), ("isotonic_blocks", "BIGINT"),
    ("sample_weight", "VARCHAR"), ("monotonic_direction", "VARCHAR"),
    ("interpolation", "VARCHAR"), ("out_of_range_policy", "VARCHAR"),
    ("verified_blank_policy", "VARCHAR"),
)

ISOTONIC_MAPPING_SCHEMA = (
    ("candidate", "VARCHAR"), ("block_index", "BIGINT"),
    ("raw_xfp_lower", "DOUBLE"), ("raw_xfp_upper", "DOUBLE"),
    ("calibrated_xfp", "DOUBLE"), ("development_weight", "BIGINT"),
)

TRANSFORM_EXAMPLE_SCHEMA = (
    ("candidate", "VARCHAR"), ("raw_xfp", "DOUBLE"),
    ("calibrated_xfp", "DOUBLE"),
    ("verified_blank_override_applied", "BOOLEAN"),
)

CALIBRATION_BAND_SCHEMA = (
    ("phase", "VARCHAR"), ("season", "VARCHAR"),
    ("candidate", "VARCHAR"), ("raw_xfp_band", "VARCHAR"),
    ("n", "BIGINT"), ("mean_raw_prediction", "DOUBLE"),
    ("mean_calibrated_prediction", "DOUBLE"),
    ("mean_actual_modeled_points", "DOUBLE"),
    ("bias", "DOUBLE"), ("mae", "DOUBLE"), ("rmse", "DOUBLE"),
)

SELECTION_SCHEMA = (
    ("candidate", "VARCHAR"), ("development_qualifies", "BOOLEAN"),
    ("rmse_reduction_pct", "DOUBLE"), ("mae_reduction_pct", "DOUBLE"),
    ("absolute_bias_improvement_pct", "DOUBLE"),
    ("calibrated_absolute_bias", "DOUBLE"), ("bias_guard_passed", "BOOLEAN"),
    ("spearman_decline", "DOUBLE"),
    ("top10_overlap_decline_pp", "DOUBLE"),
    ("top25_overlap_decline_pp", "DOUBLE"),
    ("top50_overlap_decline_pp", "DOUBLE"),
    ("coverage_drop_pp", "DOUBLE"), ("selected_for_holdout", "BOOLEAN"),
    ("holdout_passed", "BOOLEAN"),
    ("holdout_rmse_reduction_pct", "DOUBLE"),
    ("holdout_mae_reduction_pct", "DOUBLE"),
    ("holdout_absolute_bias_worsening", "DOUBLE"),
    ("holdout_spearman_decline", "DOUBLE"),
    ("holdout_top10_overlap_decline_pp", "DOUBLE"),
    ("holdout_top25_overlap_decline_pp", "DOUBLE"),
    ("holdout_top50_overlap_decline_pp", "DOUBLE"),
    ("holdout_coverage_drop_pp", "DOUBLE"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalCalibrationExperimentError(
            f"could not read manifest {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise HistoricalCalibrationExperimentError(f"manifest is not an object: {path}")
    return value


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fit_linear_calibration(
    pairs: Sequence[tuple[float, float]],
) -> LinearCalibration:
    """Fit deterministic unweighted OLS with an intercept."""
    if len(pairs) < 2:
        raise HistoricalCalibrationExperimentError("linear calibration needs two pairs")
    xs = [float(pair[0]) for pair in pairs]
    ys = [float(pair[1]) for pair in pairs]
    mean_x = math.fsum(xs) / len(xs)
    mean_y = math.fsum(ys) / len(ys)
    denominator = math.fsum((value - mean_x) ** 2 for value in xs)
    if denominator == 0:
        raise HistoricalCalibrationExperimentError(
            "linear calibration predictor has zero variance"
        )
    slope = math.fsum(
        (x_value - mean_x) * (y_value - mean_y)
        for x_value, y_value in zip(xs, ys)
    ) / denominator
    return LinearCalibration(mean_y - slope * mean_x, slope, len(pairs))


def fit_isotonic_calibration(
    pairs: Sequence[tuple[float, float]],
) -> IsotonicCalibration:
    """Fit unit-weight increasing least-squares isotonic regression via PAVA."""
    if not pairs:
        raise HistoricalCalibrationExperimentError("isotonic calibration needs pairs")
    grouped: dict[float, list[float]] = {}
    for x_value, y_value in sorted((float(x), float(y)) for x, y in pairs):
        grouped.setdefault(x_value, []).append(y_value)
    working: list[list[float | int]] = []
    for x_value, targets in grouped.items():
        weight = len(targets)
        working.append([x_value, x_value, math.fsum(targets), weight])
        while len(working) >= 2:
            left, right = working[-2], working[-1]
            if float(left[2]) / int(left[3]) <= float(right[2]) / int(right[3]):
                break
            working[-2:] = [[
                float(left[0]), float(right[1]),
                float(left[2]) + float(right[2]), int(left[3]) + int(right[3]),
            ]]
    blocks = tuple(
        IsotonicBlock(
            lower_x=float(block[0]), upper_x=float(block[1]),
            fitted_y=float(block[2]) / int(block[3]), weight=int(block[3]),
        )
        for block in working
    )
    if any(left.fitted_y > right.fitted_y for left, right in zip(blocks, blocks[1:])):
        raise HistoricalCalibrationExperimentError("isotonic mapping is not monotonic")
    return IsotonicCalibration(blocks, len(pairs))


def _validate_experiment_provenance(
    directory: Path, version: str,
) -> dict[Path, str]:
    manifest_path = directory / "experiment_manifest.json"
    manifest = _load_json(manifest_path)
    if manifest.get("status") != "complete" or manifest.get("experiment_version") != version:
        raise HistoricalCalibrationExperimentError(
            f"required completed experiment is invalid: {directory}"
        )
    protected = {manifest_path: _sha256(manifest_path)}
    for output in manifest.get("outputs", []):
        path = directory / output["path"]
        if not path.is_file() or _sha256(path) != output.get("sha256"):
            raise HistoricalCalibrationExperimentError(
                f"experiment provenance hash mismatch: {path}"
            )
        protected[path] = output["sha256"]
    return protected


def _validate_inputs(
    *, historical_clean_root: Path, baseline_root: Path, experiment_root: Path,
) -> tuple[Path, dict[Path, str], dict[str, str]]:
    historical_directory = historical_clean_root / HISTORICAL_VERSION
    historical_manifest_path = historical_directory / "historical_ingestion_manifest.json"
    baseline_directory = baseline_root / BASELINE_VERSION
    baseline_manifest_path = baseline_directory / "backtest_manifest.json"
    historical_manifest = _load_json(historical_manifest_path)
    baseline_manifest = _load_json(baseline_manifest_path)
    if historical_manifest.get("status") != "complete" or historical_manifest.get(
        "parser_schema_version"
    ) != HISTORICAL_VERSION:
        raise HistoricalCalibrationExperimentError("historical-v2 manifest is not complete")
    if baseline_manifest.get("status") != "complete" or baseline_manifest.get(
        "backtest_version"
    ) != BASELINE_VERSION:
        raise HistoricalCalibrationExperimentError("frozen baseline manifest is not complete")
    if baseline_manifest.get("historical_classification") != HISTORICAL_CLASSIFICATION:
        raise HistoricalCalibrationExperimentError("baseline classification changed")

    protected = {
        historical_manifest_path: _sha256(historical_manifest_path),
        baseline_manifest_path: _sha256(baseline_manifest_path),
    }
    for manifest, directory in (
        (historical_manifest, historical_directory),
        (baseline_manifest, baseline_directory),
    ):
        for output in manifest.get("outputs", []):
            path = directory / output["path"]
            if not path.is_file() or _sha256(path) != output.get("sha256"):
                raise HistoricalCalibrationExperimentError(
                    f"immutable input hash does not match its manifest: {path}"
                )
            protected[path] = output["sha256"]

    for version in (TASK_009_VERSION, TASK_010_VERSION):
        protected.update(
            _validate_experiment_provenance(experiment_root / version, version)
        )
    player_path = baseline_directory / "player_gameweek.parquet"
    if player_path not in protected:
        raise HistoricalCalibrationExperimentError("baseline player-gameweek input unpinned")
    provenance = {
        "baseline_manifest_sha256": protected[baseline_manifest_path],
        "historical_manifest_sha256": protected[historical_manifest_path],
        "task_009_manifest_sha256": protected[
            experiment_root / TASK_009_VERSION / "experiment_manifest.json"
        ],
        "task_010_manifest_sha256": protected[
            experiment_root / TASK_010_VERSION / "experiment_manifest.json"
        ],
    }
    return player_path, protected, provenance


def _load_phase_rows(
    connection: duckdb.DuckDBPyConnection, *, player_path: Path,
    season: str,
) -> list[dict[str, Any]]:
    cursor = connection.execute(
        """SELECT season,target_gameweek,element_id,code,"position",team_id,team_name,
                  fixture_count,gameweek_xfp_v01 raw_xfp_v01,
                  gameweek_expected_minutes_for_evaluation expected_minutes,
                  actual_minutes,actual_modeled_points,actual_full_fpl_points,
                  actual_state,prediction_state baseline_prediction_state,
                  attacking_rate_available,low_sample,prior_total_minutes,
                  prior_gameweeks_with_data,availability_band,historical_classification
           FROM read_parquet(?)
           WHERE season=? AND target_gameweek BETWEEN 2 AND 38
           ORDER BY target_gameweek,element_id""",
        [str(player_path), season],
    )
    columns = [column[0] for column in cursor.description]
    rows = [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]
    if not rows or any(row["season"] != season for row in rows):
        raise HistoricalCalibrationExperimentError(f"invalid {season} baseline rows")
    if any(row["historical_classification"] != HISTORICAL_CLASSIFICATION for row in rows):
        raise HistoricalCalibrationExperimentError("historical classification changed")
    invalid_blanks = [
        row for row in rows if row["fixture_count"] == 0
        and (row["raw_xfp_v01"] != 0 or row["actual_modeled_points"] != 0)
    ]
    if invalid_blanks:
        raise HistoricalCalibrationExperimentError(
            f"verified blank semantics changed in {len(invalid_blanks)} rows"
        )
    return rows


def _fit_development_mappings(
    rows: Sequence[dict[str, Any]],
) -> tuple[LinearCalibration, IsotonicCalibration]:
    if any(row["season"] != DEVELOPMENT_SEASON for row in rows):
        raise HistoricalCalibrationExperimentError(
            "non-development observations entered calibration fitting"
        )
    pairs = [
        (float(row["raw_xfp_v01"]), float(row["actual_modeled_points"]))
        for row in rows
        if row["raw_xfp_v01"] is not None and row["actual_modeled_points"] is not None
    ]
    return fit_linear_calibration(pairs), fit_isotonic_calibration(pairs)


def _candidate_prediction(
    candidate: str, row: dict[str, Any],
    linear: LinearCalibration, isotonic: IsotonicCalibration,
) -> tuple[float | None, bool]:
    raw = row["raw_xfp_v01"]
    if raw is None:
        return None, False
    if row["fixture_count"] == 0:
        return 0.0, candidate != "C0"
    if candidate == "C0":
        return float(raw), False
    if candidate == "C1":
        return linear.transform(float(raw)), False
    if candidate == "C2":
        return isotonic.transform(float(raw)), False
    raise HistoricalCalibrationExperimentError(f"unknown candidate: {candidate}")


def _candidate_rows(
    rows: Sequence[dict[str, Any]], *, phase: str, candidates: Sequence[str],
    linear: LinearCalibration, isotonic: IsotonicCalibration,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        for candidate in candidates:
            prediction, blank_override = _candidate_prediction(
                candidate, row, linear, isotonic
            )
            raw = row["raw_xfp_v01"]
            output.append({
                "phase": phase, **row, "candidate": candidate,
                "calibrated_xfp": prediction,
                "calibration_adjustment": (
                    prediction - float(raw)
                    if prediction is not None and raw is not None else None
                ),
                "mapping_fitted_season": DEVELOPMENT_SEASON,
                "verified_blank_override": blank_override,
                "prediction_complete": prediction is not None,
            })
    c0 = [row for row in output if row["candidate"] == "C0"]
    if any(row["calibrated_xfp"] != row["raw_xfp_v01"] for row in c0):
        raise HistoricalCalibrationExperimentError("C0 does not reproduce frozen baseline")
    for row in output:
        if row["raw_xfp_v01"] is None and row["calibrated_xfp"] is not None:
            raise HistoricalCalibrationExperimentError("missing raw prediction was imputed")
        if row["fixture_count"] == 0 and row["calibrated_xfp"] != 0:
            raise HistoricalCalibrationExperimentError("verified blank was recalibrated")
    return output


def _player_tuples(rows: Sequence[dict[str, Any]]) -> list[tuple[Any, ...]]:
    fields = [name for name, _ in PLAYER_SCHEMA]
    return [tuple(row[field] for field in fields) for row in rows]


def _metric_tuple(
    *, phase: str, season: str, candidate: str, population: str,
    metric: dict[str, Any],
) -> tuple[Any, ...]:
    return (
        phase, season, candidate, population, "modeled_xfp",
        metric["n_eligible"], metric["n_complete_pairs"],
        metric["missing_prediction"], metric["missing_actual"],
        metric["coverage_pct"], metric["mae"], metric["rmse"],
        metric["bias"], metric["spearman"],
    )


def _phase_metrics(
    rows: Sequence[dict[str, Any]], *, phase: str, season: str,
    candidates: Sequence[str], population: str = "natural",
) -> list[tuple[Any, ...]]:
    return [
        _metric_tuple(
            phase=phase, season=season, candidate=candidate, population=population,
            metric=_metric(
                [row for row in rows if row["candidate"] == candidate],
                "calibrated_xfp", "actual_modeled_points",
            ),
        )
        for candidate in candidates
    ]


def _common_pair_metrics(
    rows: Sequence[dict[str, Any]], *, phase: str, season: str,
    candidates: Sequence[str],
) -> list[tuple[Any, ...]]:
    indexed = {
        candidate: {
            (row["target_gameweek"], row["element_id"]): row
            for row in rows if row["candidate"] == candidate
        }
        for candidate in candidates
    }
    baseline = indexed["C0"]
    output = []
    for candidate in candidates[1:]:
        compared = indexed[candidate]
        keys = [
            key for key, control in baseline.items()
            if key in compared and control["calibrated_xfp"] is not None
            and compared[key]["calibrated_xfp"] is not None
            and control["actual_modeled_points"] is not None
            and compared[key]["actual_modeled_points"] is not None
        ]
        for predictor, source in (("C0", baseline), (candidate, compared)):
            metric = _metric(
                [source[key] for key in keys],
                "calibrated_xfp", "actual_modeled_points",
            )
            output.append((
                phase, season, candidate, predictor, "modeled_xfp", len(keys),
                metric["mae"], metric["rmse"], metric["bias"], metric["spearman"],
            ))
    return output


def _ranking_for_members(
    members: Sequence[dict[str, Any]], top_n: int,
) -> tuple[bool, float | None, float | None]:
    if len(members) < top_n:
        return False, None, None
    predicted = sorted(
        members, key=lambda row: (-float(row["calibrated_xfp"]), row["element_id"])
    )[:top_n]
    actual = sorted(
        members, key=lambda row: (-float(row["actual_modeled_points"]), row["element_id"])
    )[:top_n]
    overlap = float(
        len({row["element_id"] for row in predicted} & {row["element_id"] for row in actual})
    )
    return True, overlap, 100.0 * overlap / top_n


def _ranking_rows(
    rows: Sequence[dict[str, Any]], *, phase: str, season: str,
    candidates: Sequence[str],
) -> list[tuple[Any, ...]]:
    output = []
    for candidate in candidates:
        eligible = [
            row for row in rows if row["candidate"] == candidate
            and row["calibrated_xfp"] is not None
            and row["actual_modeled_points"] is not None
        ]
        gameweeks: dict[int, list[dict[str, Any]]] = {}
        for row in eligible:
            gameweeks.setdefault(row["target_gameweek"], []).append(row)
        for top_n in TOP_N_VALUES:
            summarized = []
            for gameweek, members in sorted(gameweeks.items()):
                enough, overlap, overlap_pct = _ranking_for_members(members, top_n)
                if overlap_pct is not None:
                    summarized.append(overlap_pct)
                output.append((
                    phase, season, candidate, "gameweek", gameweek, top_n,
                    len(members), enough, overlap, overlap_pct, 1 if enough else 0,
                    "score_desc_then_element_id_asc_strict_n",
                ))
            output.append((
                phase, season, candidate, "summary", None, top_n, None,
                bool(summarized), None,
                math.fsum(summarized) / len(summarized) if summarized else None,
                len(summarized), "score_desc_then_element_id_asc_strict_n",
            ))
    return output


def _common_ranking_rows(
    rows: Sequence[dict[str, Any]], *, phase: str, season: str,
    candidates: Sequence[str],
) -> list[tuple[Any, ...]]:
    indexed = {
        candidate: {
            (row["target_gameweek"], row["element_id"]): row
            for row in rows if row["candidate"] == candidate
        }
        for candidate in candidates
    }
    output = []
    baseline = indexed["C0"]
    for candidate in candidates[1:]:
        compared = indexed[candidate]
        keys = [
            key for key, control in baseline.items()
            if key in compared and control["calibrated_xfp"] is not None
            and compared[key]["calibrated_xfp"] is not None
            and control["actual_modeled_points"] is not None
        ]
        for predictor, source in (("C0", baseline), (candidate, compared)):
            gameweeks: dict[int, list[dict[str, Any]]] = {}
            for key in keys:
                gameweeks.setdefault(key[0], []).append(source[key])
            for top_n in TOP_N_VALUES:
                summarized = []
                for gameweek, members in sorted(gameweeks.items()):
                    enough, overlap, overlap_pct = _ranking_for_members(members, top_n)
                    if overlap_pct is not None:
                        summarized.append(overlap_pct)
                    output.append((
                        phase, season, candidate, predictor, "gameweek", gameweek,
                        top_n, len(members), enough, overlap, overlap_pct,
                        1 if enough else 0,
                        "score_desc_then_element_id_asc_strict_n",
                    ))
                output.append((
                    phase, season, candidate, predictor, "summary", None, top_n,
                    None, bool(summarized), None,
                    math.fsum(summarized) / len(summarized) if summarized else None,
                    len(summarized), "score_desc_then_element_id_asc_strict_n",
                ))
    return output


def _common_lookup(
    rows: Sequence[tuple[Any, ...]], *, comparison: str, predictor: str,
) -> dict[str, Any]:
    for row in rows:
        if row[2] == comparison and row[3] == predictor and row[4] == "modeled_xfp":
            return {
                "n": row[5], "mae": row[6], "rmse": row[7],
                "bias": row[8], "spearman": row[9],
            }
    raise HistoricalCalibrationExperimentError(
        f"missing common metric {comparison}/{predictor}"
    )


def _coverage_lookup(
    rows: Sequence[tuple[Any, ...]], *, candidate: str,
) -> float:
    for row in rows:
        if row[2] == candidate and row[3] == "natural":
            return float(row[9])
    raise HistoricalCalibrationExperimentError(f"missing coverage for {candidate}")


def _ranking_lookup(
    rows: Sequence[tuple[Any, ...]], *, comparison: str, predictor: str, top_n: int,
) -> float:
    for row in rows:
        if row[2] == comparison and row[3] == predictor \
                and row[4] == "summary" and row[6] == top_n:
            return float(row[10])
    raise HistoricalCalibrationExperimentError(
        f"missing common ranking {comparison}/{predictor}/top-{top_n}"
    )


def select_development_winner(
    metrics: Sequence[tuple[Any, ...]], common: Sequence[tuple[Any, ...]],
    common_ranking: Sequence[tuple[Any, ...]],
) -> tuple[str | None, list[dict[str, Any]]]:
    if any(row[0] != "development" or row[1] != DEVELOPMENT_SEASON for row in metrics):
        raise HistoricalCalibrationExperimentError("holdout metrics entered selection")
    if any(row[0] != "development" or row[1] != DEVELOPMENT_SEASON for row in common):
        raise HistoricalCalibrationExperimentError("holdout common pairs entered selection")
    if any(row[0] != "development" or row[1] != DEVELOPMENT_SEASON for row in common_ranking):
        raise HistoricalCalibrationExperimentError("holdout rankings entered selection")
    records = []
    for candidate in CANDIDATES[1:]:
        control = _common_lookup(common, comparison=candidate, predictor="C0")
        calibrated = _common_lookup(common, comparison=candidate, predictor=candidate)
        bias_improvement = (
            100.0 * (abs(control["bias"]) - abs(calibrated["bias"]))
            / abs(control["bias"])
            if control["bias"] not in (None, 0) else None
        )
        top_declines = {
            top_n: _ranking_lookup(
                common_ranking, comparison=candidate, predictor="C0", top_n=top_n
            ) - _ranking_lookup(
                common_ranking, comparison=candidate, predictor=candidate, top_n=top_n
            )
            for top_n in TOP_N_VALUES
        }
        record = {
            "candidate": candidate,
            "rmse_reduction_pct": _reduction(control["rmse"], calibrated["rmse"]),
            "mae_reduction_pct": _reduction(control["mae"], calibrated["mae"]),
            "absolute_bias_improvement_pct": bias_improvement,
            "calibrated_absolute_bias": abs(calibrated["bias"]),
            "bias_guard_passed": (
                (bias_improvement is not None and bias_improvement >= 20.0)
                or abs(calibrated["bias"]) <= 0.03
            ),
            "spearman_decline": control["spearman"] - calibrated["spearman"],
            "top10_overlap_decline_pp": top_declines[10],
            "top25_overlap_decline_pp": top_declines[25],
            "top50_overlap_decline_pp": top_declines[50],
            "coverage_drop_pp": _coverage_lookup(metrics, candidate="C0")
            - _coverage_lookup(metrics, candidate=candidate),
        }
        record["development_qualifies"] = (
            record["rmse_reduction_pct"] >= 3.0
            and record["mae_reduction_pct"] >= 2.0
            and record["bias_guard_passed"]
            and record["spearman_decline"] <= 0.005
            and all(value <= 1.0 for value in top_declines.values())
            and record["coverage_drop_pp"] <= 1.0
        )
        records.append(record)
    simplicity = {"C1": 0, "C2": 1}
    qualifying = sorted(
        (row for row in records if row["development_qualifies"]),
        key=lambda row: (
            -row["rmse_reduction_pct"], -row["mae_reduction_pct"],
            simplicity[row["candidate"]],
        ),
    )
    return (qualifying[0]["candidate"] if qualifying else None), records


def _holdout_candidate_set(winner: str) -> tuple[str, str]:
    if winner not in CANDIDATES[1:]:
        raise HistoricalCalibrationExperimentError(
            "holdout requires one development winner"
        )
    return "C0", winner


def _holdout_decision(
    metrics: Sequence[tuple[Any, ...]], common: Sequence[tuple[Any, ...]],
    common_ranking: Sequence[tuple[Any, ...]], winner: str,
) -> dict[str, Any]:
    control = _common_lookup(common, comparison=winner, predictor="C0")
    calibrated = _common_lookup(common, comparison=winner, predictor=winner)
    top_declines = {
        top_n: _ranking_lookup(
            common_ranking, comparison=winner, predictor="C0", top_n=top_n
        ) - _ranking_lookup(
            common_ranking, comparison=winner, predictor=winner, top_n=top_n
        )
        for top_n in TOP_N_VALUES
    }
    result = {
        "holdout_rmse_reduction_pct": _reduction(control["rmse"], calibrated["rmse"]),
        "holdout_mae_reduction_pct": _reduction(control["mae"], calibrated["mae"]),
        "holdout_absolute_bias_worsening": abs(calibrated["bias"]) - abs(control["bias"]),
        "holdout_spearman_decline": control["spearman"] - calibrated["spearman"],
        "holdout_top10_overlap_decline_pp": top_declines[10],
        "holdout_top25_overlap_decline_pp": top_declines[25],
        "holdout_top50_overlap_decline_pp": top_declines[50],
        "holdout_coverage_drop_pp": _coverage_lookup(metrics, candidate="C0")
        - _coverage_lookup(metrics, candidate=winner),
    }
    result["holdout_passed"] = (
        result["holdout_rmse_reduction_pct"] >= 2.0
        and result["holdout_mae_reduction_pct"] >= 1.0
        and result["holdout_absolute_bias_worsening"] <= 0.02
        and result["holdout_spearman_decline"] <= 0.005
        and all(value <= 1.0 for value in top_declines.values())
        and result["holdout_coverage_drop_pp"] <= 1.0
    )
    return result


def _population(row: dict[str, Any], name: str) -> bool:
    prior = row["prior_total_minutes"]
    if name == "all_complete":
        return row["calibrated_xfp"] is not None and row["actual_modeled_points"] is not None
    if name == "actual_minutes_gt_0": return row["actual_minutes"] > 0
    if name == "expected_minutes_gt_0": return row["expected_minutes"] is not None and row["expected_minutes"] > 0
    if name == "normal_single": return row["fixture_count"] == 1
    if name == "double": return row["fixture_count"] > 1
    if name in ("GK", "DEF", "MID", "FWD"): return row["position"] == name
    if name == "prior_minutes_1_90": return prior is not None and 1 <= prior <= 90
    if name == "prior_minutes_91_179": return prior is not None and 91 <= prior <= 179
    if name == "prior_minutes_180_450": return prior is not None and 180 <= prior <= 450
    if name == "prior_minutes_451_plus": return prior is not None and prior >= 451
    if name == "stable":
        return (
            row["attacking_rate_available"] is True and prior is not None and prior >= 450
            and row["expected_minutes"] is not None and row["expected_minutes"] >= 60
            and row["fixture_count"] == 1
        )
    raise ValueError(name)


DIAGNOSTIC_POPULATIONS = (
    "all_complete", "actual_minutes_gt_0", "expected_minutes_gt_0",
    "normal_single", "double", "GK", "DEF", "MID", "FWD",
    "prior_minutes_1_90", "prior_minutes_91_179",
    "prior_minutes_180_450", "prior_minutes_451_plus", "stable",
)


def _diagnostic_metrics(
    rows: Sequence[dict[str, Any]], *, phase: str, season: str,
    candidates: Sequence[str],
) -> list[tuple[Any, ...]]:
    output = []
    for population in DIAGNOSTIC_POPULATIONS:
        subset = [row for row in rows if _population(row, population)]
        output.extend(
            _phase_metrics(
                subset, phase=phase, season=season,
                candidates=candidates, population=population,
            )
        )
    return output


def _raw_band(value: float) -> str:
    if value < 1: return "0-<1"
    if value < 2: return "1-<2"
    if value < 3: return "2-<3"
    if value < 4: return "3-<4"
    if value < 5: return "4-<5"
    if value < 7: return "5-<7"
    return "7+"


def _calibration_band_rows(
    rows: Sequence[dict[str, Any]], *, phase: str, season: str,
    candidates: Sequence[str],
) -> list[tuple[Any, ...]]:
    output = []
    bands = ("0-<1", "1-<2", "2-<3", "3-<4", "4-<5", "5-<7", "7+")
    for candidate in candidates:
        candidate_rows = [row for row in rows if row["candidate"] == candidate]
        for band in bands:
            pairs = [
                row for row in candidate_rows
                if row["raw_xfp_v01"] is not None
                and _raw_band(float(row["raw_xfp_v01"])) == band
                and row["calibrated_xfp"] is not None
                and row["actual_modeled_points"] is not None
            ]
            if not pairs:
                output.append((phase, season, candidate, band, 0, None, None, None, None, None, None))
                continue
            raw = [float(row["raw_xfp_v01"]) for row in pairs]
            predictions = [float(row["calibrated_xfp"]) for row in pairs]
            actuals = [float(row["actual_modeled_points"]) for row in pairs]
            errors = [left - right for left, right in zip(predictions, actuals)]
            output.append((
                phase, season, candidate, band, len(pairs),
                math.fsum(raw) / len(raw), math.fsum(predictions) / len(predictions),
                math.fsum(actuals) / len(actuals), math.fsum(errors) / len(errors),
                math.fsum(abs(error) for error in errors) / len(errors),
                math.sqrt(math.fsum(error * error for error in errors) / len(errors)),
            ))
    return output


def _parameter_rows(
    linear: LinearCalibration, isotonic: IsotonicCalibration,
) -> list[tuple[Any, ...]]:
    blank_policy = "fixture_count=0 remains explicit calibrated_xfp=0"
    return [
        ("C0", "identity", linear.development_n, 0.0, 1.0, None, "unit", "none", "identity", "identity", blank_policy),
        ("C1", "ordinary_least_squares_with_intercept", linear.development_n,
         linear.intercept, linear.slope, None, "unit", "unconstrained", "linear", "linear", blank_policy),
        ("C2", "pool_adjacent_violators_least_squares", isotonic.development_n,
         None, None, len(isotonic.blocks), "unit", "non_decreasing",
         "right-continuous_step_at_midpoint_between_adjacent_blocks",
         "constant_boundary_extension", blank_policy),
    ]


def _isotonic_rows(isotonic: IsotonicCalibration) -> list[tuple[Any, ...]]:
    return [
        ("C2", index, block.lower_x, block.upper_x, block.fitted_y, block.weight)
        for index, block in enumerate(isotonic.blocks)
    ]


def _transform_examples(
    linear: LinearCalibration, isotonic: IsotonicCalibration,
) -> list[tuple[Any, ...]]:
    output = []
    for raw in (0.0, 0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0):
        for candidate, transform in (
            ("C0", lambda value: value),
            ("C1", linear.transform), ("C2", isotonic.transform),
        ):
            output.append((candidate, raw, transform(raw), False))
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
            record["candidate"], record["development_qualifies"],
            record["rmse_reduction_pct"], record["mae_reduction_pct"],
            record["absolute_bias_improvement_pct"],
            record["calibrated_absolute_bias"], record["bias_guard_passed"],
            record["spearman_decline"], record["top10_overlap_decline_pp"],
            record["top25_overlap_decline_pp"], record["top50_overlap_decline_pp"],
            record["coverage_drop_pp"], selected, held.get("holdout_passed"),
            held.get("holdout_rmse_reduction_pct"),
            held.get("holdout_mae_reduction_pct"),
            held.get("holdout_absolute_bias_worsening"),
            held.get("holdout_spearman_decline"),
            held.get("holdout_top10_overlap_decline_pp"),
            held.get("holdout_top25_overlap_decline_pp"),
            held.get("holdout_top50_overlap_decline_pp"),
            held.get("holdout_coverage_drop_pp"),
        ))
    return output


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
            raise HistoricalCalibrationExperimentOutputExistsError(
                f"experiment output already exists and will not be overwritten: {path}"
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _write_outputs(
    connection: duckdb.DuckDBPyConnection, *, experiment_root: Path,
    manifest_base: dict[str, Any], tables: Sequence[str],
) -> tuple[Path, Path]:
    final = experiment_root / EXPERIMENT_VERSION
    if final.exists():
        raise HistoricalCalibrationExperimentOutputExistsError(
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
            outputs.append({
                "path": path.name,
                "rows": connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0],
                "bytes": path.stat().st_size, "sha256": _sha256(path),
            })
        manifest = {**manifest_base, "outputs": outputs}
        _write_exclusive(
            stage / "experiment_manifest.json",
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        experiment_root.mkdir(parents=True, exist_ok=True)
        try:
            stage.rename(final)
        except FileExistsError as exc:
            raise HistoricalCalibrationExperimentOutputExistsError(
                f"experiment output already exists and will not be overwritten: {final}"
            ) from exc
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return final, final / "experiment_manifest.json"


def run_historical_calibration_experiment(
    *, historical_clean_root: Path = Path("data/historical/clean"),
    baseline_root: Path = Path("data/historical/backtests"),
    experiment_root: Path = Path("data/historical/experiments"),
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> HistoricalCalibrationExperimentResult:
    """Run the preregistered development/conditional-holdout experiment once."""
    final = experiment_root / EXPERIMENT_VERSION
    if final.exists():
        raise HistoricalCalibrationExperimentOutputExistsError(
            f"experiment output already exists and will not be overwritten: {final}"
        )
    player_path, protected_hashes, provenance = _validate_inputs(
        historical_clean_root=historical_clean_root,
        baseline_root=baseline_root, experiment_root=experiment_root,
    )
    connection = duckdb.connect(":memory:")
    try:
        # The holdout season is not queried until exactly one development winner exists.
        development_base = _load_phase_rows(
            connection, player_path=player_path, season=DEVELOPMENT_SEASON
        )
        linear, isotonic = _fit_development_mappings(development_base)
        development = _candidate_rows(
            development_base, phase="development", candidates=CANDIDATES,
            linear=linear, isotonic=isotonic,
        )
        development_metrics = _phase_metrics(
            development, phase="development", season=DEVELOPMENT_SEASON,
            candidates=CANDIDATES,
        )
        development_common = _common_pair_metrics(
            development, phase="development", season=DEVELOPMENT_SEASON,
            candidates=CANDIDATES,
        )
        development_ranking = _ranking_rows(
            development, phase="development", season=DEVELOPMENT_SEASON,
            candidates=CANDIDATES,
        )
        development_common_ranking = _common_ranking_rows(
            development, phase="development", season=DEVELOPMENT_SEASON,
            candidates=CANDIDATES,
        )
        winner, selection = select_development_winner(
            development_metrics, development_common, development_common_ranking
        )

        holdout: list[dict[str, Any]] = []
        holdout_metrics: list[tuple[Any, ...]] = []
        holdout_common: list[tuple[Any, ...]] = []
        holdout_ranking: list[tuple[Any, ...]] = []
        holdout_common_ranking: list[tuple[Any, ...]] = []
        holdout_result: dict[str, Any] | None = None
        if winner is not None:
            holdout_candidates = _holdout_candidate_set(winner)
            holdout_base = _load_phase_rows(
                connection, player_path=player_path, season=HOLDOUT_SEASON
            )
            holdout = _candidate_rows(
                holdout_base, phase="holdout", candidates=holdout_candidates,
                linear=linear, isotonic=isotonic,
            )
            holdout_metrics = _phase_metrics(
                holdout, phase="holdout", season=HOLDOUT_SEASON,
                candidates=holdout_candidates,
            )
            holdout_common = _common_pair_metrics(
                holdout, phase="holdout", season=HOLDOUT_SEASON,
                candidates=holdout_candidates,
            )
            holdout_ranking = _ranking_rows(
                holdout, phase="holdout", season=HOLDOUT_SEASON,
                candidates=holdout_candidates,
            )
            holdout_common_ranking = _common_ranking_rows(
                holdout, phase="holdout", season=HOLDOUT_SEASON,
                candidates=holdout_candidates,
            )
            holdout_result = _holdout_decision(
                holdout_metrics, holdout_common, holdout_common_ranking, winner
            )

        all_candidate_rows = development + holdout
        _create_rows_table(
            connection, "candidate_player_gameweek", PLAYER_SCHEMA,
            _player_tuples(all_candidate_rows),
        )
        _create_rows_table(
            connection, "calibration_parameters", CALIBRATION_PARAMETER_SCHEMA,
            _parameter_rows(linear, isotonic),
        )
        _create_rows_table(
            connection, "isotonic_mapping", ISOTONIC_MAPPING_SCHEMA,
            _isotonic_rows(isotonic),
        )
        _create_rows_table(
            connection, "calibration_transform_examples", TRANSFORM_EXAMPLE_SCHEMA,
            _transform_examples(linear, isotonic),
        )
        _create_rows_table(
            connection, "development_metrics", METRIC_SCHEMA, development_metrics,
        )
        _create_rows_table(
            connection, "development_common_pair_metrics", COMMON_PAIR_SCHEMA,
            development_common,
        )
        _create_rows_table(
            connection, "development_ranking", RANKING_SCHEMA, development_ranking,
        )
        _create_rows_table(
            connection, "development_common_pair_ranking", COMMON_RANKING_SCHEMA,
            development_common_ranking,
        )
        tables = [
            "candidate_player_gameweek", "calibration_parameters",
            "isotonic_mapping", "calibration_transform_examples",
            "development_metrics", "development_common_pair_metrics",
            "development_ranking", "development_common_pair_ranking",
        ]
        if winner is not None:
            _create_rows_table(connection, "holdout_metrics", METRIC_SCHEMA, holdout_metrics)
            _create_rows_table(
                connection, "holdout_common_pair_metrics", COMMON_PAIR_SCHEMA,
                holdout_common,
            )
            _create_rows_table(connection, "holdout_ranking", RANKING_SCHEMA, holdout_ranking)
            _create_rows_table(
                connection, "holdout_common_pair_ranking", COMMON_RANKING_SCHEMA,
                holdout_common_ranking,
            )
            tables.extend([
                "holdout_metrics", "holdout_common_pair_metrics",
                "holdout_ranking", "holdout_common_pair_ranking",
            ])
        diagnostic_rows = _diagnostic_metrics(
            development, phase="development", season=DEVELOPMENT_SEASON,
            candidates=CANDIDATES,
        )
        if winner is not None:
            diagnostic_rows.extend(_diagnostic_metrics(
                holdout, phase="holdout", season=HOLDOUT_SEASON,
                candidates=_holdout_candidate_set(winner),
            ))
        _create_rows_table(connection, "diagnostic_metrics", METRIC_SCHEMA, diagnostic_rows)
        band_rows = _calibration_band_rows(
            development, phase="development", season=DEVELOPMENT_SEASON,
            candidates=CANDIDATES,
        )
        if winner is not None:
            band_rows.extend(_calibration_band_rows(
                holdout, phase="holdout", season=HOLDOUT_SEASON,
                candidates=_holdout_candidate_set(winner),
            ))
        _create_rows_table(
            connection, "calibration_band_diagnostics", CALIBRATION_BAND_SCHEMA, band_rows,
        )
        _create_rows_table(
            connection, "selection_decision", SELECTION_SCHEMA,
            _selection_rows(selection, winner, holdout_result),
        )
        tables.extend([
            "diagnostic_metrics", "calibration_band_diagnostics", "selection_decision",
        ])

        if any(_sha256(path) != digest for path, digest in protected_hashes.items()):
            raise HistoricalCalibrationExperimentError(
                "an immutable input changed during the experiment"
            )
        holdout_passed = holdout_result["holdout_passed"] if holdout_result else None
        decision = (
            "PROMOTE CALIBRATION CANDIDATE TO xFP v0.2 DESIGN"
            if holdout_passed else "DO NOT PROMOTE — KEEP RAW xFP v0.1"
        )
        manifest = {
            "status": "complete", "experiment_version": EXPERIMENT_VERSION,
            "historical_classification": HISTORICAL_CLASSIFICATION,
            "model_formula_frozen": "xfp_v01", "live_model_modified": False,
            "development_season": DEVELOPMENT_SEASON,
            "holdout_season": HOLDOUT_SEASON,
            "target_gameweeks": list(TARGET_GAMEWEEKS),
            "calibration_target": "actual_modeled_points (appearance + historical-position goals + FPL assists)",
            "fit_population": "2023/24 rows where raw_xfp_v01 and actual_modeled_points are both non-null",
            "natural_coverage_policy": "Missing raw xFP remains missing for every candidate; no zero imputation.",
            "selection_common_pair_policy": "C0, candidate, and actual_modeled_points all non-null on exactly the same player-gameweek rows.",
            "verified_blank_policy": "fixture_count=0 remains explicit calibrated_xfp=0 for every candidate",
            "candidate_definitions": {
                "C0": "identity mapping of immutable gameweek_xfp_v01",
                "C1": "unconstrained ordinary least squares with intercept, fit once on development",
                "C2": "unit-weight non-decreasing least-squares PAVA step mapping, fit once on development",
            },
            "isotonic_parameters": {
                "algorithm": "pool_adjacent_violators",
                "sample_weight": "one per complete development observation",
                "increasing": True,
                "step_boundary": "midpoint between adjacent fitted blocks",
                "out_of_range": "constant boundary extension",
                "manual_knot_adjustment": False,
                "output_clipping": False,
            },
            "linear_output_clipping": False,
            "development_thresholds": DEVELOPMENT_THRESHOLDS,
            "development_tie_breakers": [
                "largest RMSE reduction", "largest MAE reduction", "simpler C1 then C2",
            ],
            "holdout_thresholds": HOLDOUT_THRESHOLDS,
            "ranking_policy": "strict N per gameweek; score descending then element_id ascending; mean overlap across gameweeks",
            "development_winner": winner,
            "holdout_evaluated": winner is not None,
            "holdout_passed": holdout_passed,
            "final_decision": decision,
            "interpretation": "Calibration changes prediction magnitude only; it does not add football information or establish stronger attacking-event ranking.",
            "leakage_exclusions": [
                "2024/25 cannot enter fitting or development selection",
                "target outcomes cannot alter immutable raw xFP",
                "Vaastav xP is not read",
                "future xG/xA/minutes/points are not read upstream",
                "Task 009/010 candidate predictions are provenance-only and are not read as model inputs",
            ],
            **provenance,
            "generation_timestamp": _iso_utc(clock()),
            "immutable_inputs": [
                {"path": str(path), "sha256": digest}
                for path, digest in sorted(protected_hashes.items(), key=lambda item: str(item[0]))
            ],
        }
        directory, manifest_path = _write_outputs(
            connection, experiment_root=experiment_root,
            manifest_base=manifest, tables=tables,
        )
    except duckdb.Error as exc:
        raise HistoricalCalibrationExperimentError(
            f"calibration experiment failed: {exc}"
        ) from exc
    finally:
        connection.close()
    return HistoricalCalibrationExperimentResult(
        directory, manifest_path, winner, holdout_passed, decision
    )
