from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from fpl_decision_engine.historical import HISTORICAL_CLASSIFICATION
from fpl_decision_engine.historical_calibration_experiment import (
    EXPERIMENT_VERSION,
    HistoricalCalibrationExperimentError,
    HistoricalCalibrationExperimentOutputExistsError,
    _candidate_rows,
    _fit_development_mappings,
    _holdout_candidate_set,
    _holdout_decision,
    _ranking_for_members,
    _write_outputs,
    fit_isotonic_calibration,
    fit_linear_calibration,
    select_development_winner,
)


def baseline_row(
    *, element_id: int, raw: float | None, actual: float = 1.0,
    fixture_count: int = 1, season: str = "2023-24",
) -> dict[str, object]:
    return {
        "season": season,
        "target_gameweek": 2,
        "element_id": element_id,
        "code": 1000 + element_id,
        "position": "MID",
        "team_id": 1,
        "team_name": "Alpha",
        "fixture_count": fixture_count,
        "raw_xfp_v01": raw,
        "expected_minutes": 60.0 if fixture_count else 0.0,
        "actual_minutes": 60.0 if fixture_count else 0.0,
        "actual_modeled_points": actual,
        "actual_full_fpl_points": actual,
        "actual_state": "realized_fixture_rows" if fixture_count else "verified_blank",
        "baseline_prediction_state": "complete" if raw is not None else "missing",
        "attacking_rate_available": raw is not None,
        "low_sample": False,
        "prior_total_minutes": 450,
        "prior_gameweeks_with_data": 5,
        "availability_band": "available",
        "historical_classification": HISTORICAL_CLASSIFICATION,
    }


class CalibrationFormulaTests(unittest.TestCase):
    def test_ordinary_least_squares_is_exact_and_unconstrained(self) -> None:
        fitted = fit_linear_calibration(((0.0, 1.0), (1.0, 3.0), (2.0, 5.0)))
        self.assertAlmostEqual(fitted.intercept, 1.0)
        self.assertAlmostEqual(fitted.slope, 2.0)
        self.assertAlmostEqual(fitted.transform(4.0), 9.0)
        self.assertEqual(fitted.development_n, 3)

    def test_pava_mapping_is_deterministic_non_decreasing_and_weighted(self) -> None:
        fitted = fit_isotonic_calibration(
            ((0.0, 0.0), (1.0, 2.0), (2.0, 1.0), (3.0, 3.0))
        )
        self.assertEqual(len(fitted.blocks), 3)
        self.assertEqual((fitted.blocks[1].lower_x, fitted.blocks[1].upper_x), (1.0, 2.0))
        self.assertAlmostEqual(fitted.blocks[1].fitted_y, 1.5)
        self.assertEqual(fitted.blocks[1].weight, 2)
        transformed = [fitted.transform(value) for value in (-1.0, 0.0, 1.0, 2.0, 3.0, 9.0)]
        self.assertEqual(transformed, sorted(transformed))
        self.assertEqual((transformed[0], transformed[-1]), (0.0, 3.0))

    def test_fit_rejects_non_development_rows(self) -> None:
        rows = [
            baseline_row(element_id=1, raw=1.0),
            baseline_row(element_id=2, raw=2.0, season="2024-25"),
        ]
        with self.assertRaisesRegex(
            HistoricalCalibrationExperimentError, "non-development"
        ):
            _fit_development_mappings(rows)


class CandidateInvarianceTests(unittest.TestCase):
    def setUp(self) -> None:
        fit_rows = [
            baseline_row(element_id=1, raw=0.0, actual=0.0),
            baseline_row(element_id=2, raw=1.0, actual=1.0),
            baseline_row(element_id=3, raw=2.0, actual=2.0),
        ]
        self.linear, self.isotonic = _fit_development_mappings(fit_rows)

    def test_control_exact_missing_stays_missing_and_blank_stays_zero(self) -> None:
        rows = [
            baseline_row(element_id=10, raw=2.0, actual=2.0),
            baseline_row(element_id=11, raw=None, actual=1.0),
            baseline_row(element_id=12, raw=0.0, actual=0.0, fixture_count=0),
        ]
        output = _candidate_rows(
            rows, phase="development", candidates=("C0", "C1", "C2"),
            linear=self.linear, isotonic=self.isotonic,
        )
        indexed = {(row["candidate"], row["element_id"]): row for row in output}
        self.assertEqual(indexed[("C0", 10)]["calibrated_xfp"], 2.0)
        for candidate in ("C0", "C1", "C2"):
            self.assertIsNone(indexed[(candidate, 11)]["calibrated_xfp"])
            self.assertEqual(indexed[(candidate, 12)]["calibrated_xfp"], 0.0)
        self.assertTrue(indexed[("C1", 12)]["verified_blank_override"])
        self.assertTrue(indexed[("C2", 12)]["verified_blank_override"])

    def test_target_outcome_change_cannot_change_raw_xfp(self) -> None:
        original = baseline_row(element_id=20, raw=3.5, actual=1.0)
        changed = {**original, "actual_modeled_points": 999.0}
        before = _candidate_rows(
            [original], phase="development", candidates=("C0",),
            linear=self.linear, isotonic=self.isotonic,
        )[0]
        after = _candidate_rows(
            [changed], phase="development", candidates=("C0",),
            linear=self.linear, isotonic=self.isotonic,
        )[0]
        self.assertEqual(before["raw_xfp_v01"], after["raw_xfp_v01"])
        self.assertEqual(before["calibrated_xfp"], after["calibrated_xfp"])

    def test_holdout_actuals_cannot_enter_development_fit(self) -> None:
        development = [
            baseline_row(element_id=30, raw=0.0, actual=1.0),
            baseline_row(element_id=31, raw=1.0, actual=3.0),
        ]
        first = _fit_development_mappings(development)
        holdout = baseline_row(
            element_id=32, raw=1.0, actual=1_000_000.0, season="2024-25"
        )
        holdout["actual_modeled_points"] = -1_000_000.0
        second = _fit_development_mappings(development)
        self.assertEqual(first, second)


def metric_row(candidate: str, coverage: float = 100.0) -> tuple[object, ...]:
    return (
        "development", "2023-24", candidate, "natural", "modeled_xfp",
        100, int(coverage), 100 - int(coverage), 0, coverage,
        1.0, 2.0, 0.1, 0.5,
    )


def common_rows(candidate: str, *, qualifies: bool) -> list[tuple[object, ...]]:
    control = (
        "development", "2023-24", candidate, "C0", "modeled_xfp",
        100, 1.0, 2.0, 0.10, 0.500,
    )
    calibrated = (
        "development", "2023-24", candidate, candidate, "modeled_xfp", 100,
        0.97 if qualifies else 0.99,
        1.90 if qualifies else 1.96,
        0.02 if qualifies else 0.09,
        0.497 if qualifies else 0.490,
    )
    return [control, calibrated]


def common_ranking_rows(candidate: str, *, qualifies: bool) -> list[tuple[object, ...]]:
    rows = []
    for top_n in (10, 25, 50):
        for predictor, overlap in (
            ("C0", 50.0),
            (candidate, 49.5 if qualifies else 48.0),
        ):
            rows.append((
                "development", "2023-24", candidate, predictor, "summary",
                None, top_n, None, True, None, overlap, 37,
                "score_desc_then_element_id_asc_strict_n",
            ))
    return rows


class SelectionRankingAndOutputTests(unittest.TestCase):
    def test_all_development_gates_and_simplicity_tie_break(self) -> None:
        metrics = [metric_row(candidate) for candidate in ("C0", "C1", "C2")]
        common = common_rows("C1", qualifies=True) + common_rows("C2", qualifies=True)
        ranking = common_ranking_rows("C1", qualifies=True) + common_ranking_rows("C2", qualifies=True)
        winner, records = select_development_winner(metrics, common, ranking)
        self.assertEqual(winner, "C1")
        self.assertTrue(all(record["development_qualifies"] for record in records))
        self.assertEqual(_holdout_candidate_set(winner), ("C0", "C1"))

    def test_failed_development_keeps_holdout_closed(self) -> None:
        metrics = [metric_row(candidate) for candidate in ("C0", "C1", "C2")]
        common = common_rows("C1", qualifies=False) + common_rows("C2", qualifies=False)
        ranking = common_ranking_rows("C1", qualifies=False) + common_ranking_rows("C2", qualifies=False)
        winner, _ = select_development_winner(metrics, common, ranking)
        self.assertIsNone(winner)
        with self.assertRaises(HistoricalCalibrationExperimentError):
            _holdout_candidate_set("C0")

    def test_changing_holdout_outcomes_cannot_change_development_winner(self) -> None:
        metrics = [metric_row(candidate) for candidate in ("C0", "C1", "C2")]
        common = common_rows("C1", qualifies=True) + common_rows("C2", qualifies=False)
        ranking = common_ranking_rows("C1", qualifies=True) + common_ranking_rows("C2", qualifies=False)
        winner_before, _ = select_development_winner(metrics, common, ranking)
        holdout_outcomes = [0.0, 1.0, 1_000_000.0]
        holdout_outcomes[:] = [-1_000_000.0, 999.0, 42.0]
        winner_after, _ = select_development_winner(metrics, common, ranking)
        self.assertEqual((winner_before, winner_after), ("C1", "C1"))

    def test_holdout_gate_uses_frozen_winner_and_all_criteria(self) -> None:
        metrics = [
            ("holdout", "2024-25", candidate, "natural", "modeled_xfp",
             100, 100, 0, 0, 100.0, 1.0, 2.0, 0.1, 0.5)
            for candidate in ("C0", "C1")
        ]
        common = [
            ("holdout", "2024-25", "C1", "C0", "modeled_xfp", 100, 1.0, 2.0, 0.10, 0.500),
            ("holdout", "2024-25", "C1", "C1", "modeled_xfp", 100, 0.98, 1.94, 0.11, 0.497),
        ]
        ranking = [
            ("holdout", "2024-25", "C1", predictor, "summary", None, top_n,
             None, True, None, overlap, 37, "score_desc_then_element_id_asc_strict_n")
            for top_n in (10, 25, 50)
            for predictor, overlap in (("C0", 50.0), ("C1", 49.5))
        ]
        self.assertTrue(_holdout_decision(metrics, common, ranking, "C1")["holdout_passed"])

    def test_strict_ranking_tie_breaks_by_element_id(self) -> None:
        members = [
            {"element_id": 1, "calibrated_xfp": 2.0, "actual_modeled_points": 0.0},
            {"element_id": 2, "calibrated_xfp": 2.0, "actual_modeled_points": 2.0},
            {"element_id": 3, "calibrated_xfp": 2.0, "actual_modeled_points": 1.0},
        ]
        enough, overlap, overlap_pct = _ranking_for_members(members, 2)
        self.assertTrue(enough)
        self.assertEqual((overlap, overlap_pct), (1.0, 50.0))

    def test_completed_output_is_immutable(self) -> None:
        connection = duckdb.connect(":memory:")
        connection.execute("CREATE TABLE result AS SELECT 1 result_value")
        try:
            with TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / EXPERIMENT_VERSION).mkdir()
                with self.assertRaises(HistoricalCalibrationExperimentOutputExistsError):
                    _write_outputs(
                        connection, experiment_root=root,
                        manifest_base={}, tables=("result",),
                    )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
