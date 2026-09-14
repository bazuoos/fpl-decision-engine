from __future__ import annotations

import hashlib
import json
import math
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

import jsonschema
import duckdb

from fpl_decision_engine.research.task033c1 import (
    ARTIFACT_CONTRACT_VERSION,
    COMBINED_IDENTITY,
    PROTOCOL_IDENTITY,
    UA1_IDENTITY,
    UM1_IDENTITY,
    ArtifactHeader,
    Availability,
    HistoryFixture,
    PeerHistory,
    ResearchContractError,
    ResearchInputError,
    TargetContext,
    U0FixtureInput,
    _gamma_mixture_cdf,
    _regularized_gamma_p,
    assemble_combined,
    assemble_ua1_only,
    assemble_um1_only,
    assert_u0_parity,
    build_ua1,
    build_um1,
    build_u0,
    canonical_json_bytes,
    expected_appearance_points,
)


def target(
    *,
    fixture_ids: tuple[int, ...] = (901,),
    availability: Availability | None = None,
    element_id: int = 1,
    position: str = "MID",
) -> TargetContext:
    return TargetContext(
        season="synthetic-01",
        target_gameweek=9,
        element_id=element_id,
        position=position,
        fixture_ids=fixture_ids,
        availability=availability or Availability("a", None, True, True),
    )


def history_row(
    *,
    element_id: int = 1,
    fixture_id: int = 801,
    gameweek: int = 8,
    position: str = "MID",
    minutes: int | None = 90,
    starts: int | None = 1,
    xg: float | None = 0.3,
    xa: float | None = 0.2,
    source_universe_member: bool = True,
    kickoff_before_cutoff: bool = True,
) -> HistoryFixture:
    return HistoryFixture(
        season="synthetic-01",
        element_id=element_id,
        fixture_id=fixture_id,
        gameweek=gameweek,
        position=position,
        minutes=minutes,
        starts=starts,
        xg=xg,
        xa=xa,
        source_universe_member=source_universe_member,
        kickoff_before_cutoff=kickoff_before_cutoff,
    )


def sufficient_peers(
    *,
    position: str = "MID",
    xg: float = 5.0,
    xa: float = 2.0,
    minutes: int = 900,
) -> tuple[PeerHistory, ...]:
    return tuple(
        PeerHistory(
            element_id=element_id,
            target_position=position,
            history=(
                history_row(
                    element_id=element_id,
                    fixture_id=10_000 + element_id,
                    position=position,
                    minutes=minutes,
                    xg=xg,
                    xa=xa,
                ),
            ),
        )
        for element_id in range(2, 22)
    )


class U0AdapterTests(unittest.TestCase):
    def fixture(
        self,
        fixture_id: int,
        *,
        minutes: int | float | None = 90,
        xg: float | None = 0.2,
        xa: float | None = 0.1,
        availability: Availability | None = None,
    ) -> U0FixtureInput:
        return U0FixtureInput(
            fixture_id=fixture_id,
            position="MID",
            previous_gameweek_minutes=minutes,
            raw_xg_per90=xg,
            raw_xa_per90=xa,
            availability=availability or Availability("a", None, True, True),
        )

    def test_exact_single_and_double_fixture_scoring_and_adapters(self) -> None:
        single = build_u0((self.fixture(1),))
        double = build_u0((self.fixture(1), self.fixture(2)))
        self.assertEqual(single.gameweek_expected_minutes, 90.0)
        self.assertAlmostEqual(single.modeled_points or 0.0, 3.3)
        self.assertEqual(single.band_probabilities, (0.0, 0.0, 0.0, 0.0, 1.0))
        self.assertEqual(double.gameweek_expected_minutes, 180.0)
        self.assertAlmostEqual(double.modeled_points or 0.0, 6.6)
        self.assertEqual(double.minute_pmf[180] if double.minute_pmf else None, 1.0)
        self.assertEqual(double.band_probabilities, (0.0, 0.0, 0.0, 0.0, 1.0))
        self.assertIsNone(double.start_probability)
        self.assertAlmostEqual(double.goal_count_prediction.mean or 0.0, 0.4)
        self.assertAlmostEqual(double.assist_count_prediction.mean or 0.0, 0.2)
        self.assertAlmostEqual(
            math.fsum(double.goal_count_prediction.probability(i) or 0.0 for i in range(100)),
            1.0,
            places=12,
        )

    def test_blank_is_complete_deterministic_zero(self) -> None:
        blank = build_u0(())
        self.assertEqual(blank.fixture_count, 0)
        self.assertEqual(blank.modeled_points, 0.0)
        self.assertTrue(blank.prediction_complete)
        self.assertEqual(blank.minute_pmf, (1.0,))

    def test_missing_minutes_precedes_hard_availability_gate(self) -> None:
        gated = Availability("u", 0, True, True)
        prediction = build_u0((self.fixture(1, minutes=None, availability=gated),))
        fixture = prediction.fixture_predictions[0]
        self.assertIsNone(fixture.expected_minutes)
        self.assertIsNone(fixture.availability_gate_reason)
        self.assertEqual(prediction.gameweek_expected_minutes, 0.0)
        self.assertIsNone(prediction.expected_minutes_for_evaluation)
        self.assertIsNone(prediction.modeled_points)
        self.assertFalse(prediction.prediction_complete)
        self.assertEqual(prediction.goal_count_prediction.status, "MISSING_MEAN")

    def test_numeric_incomplete_total_and_completeness_are_both_preserved(self) -> None:
        prediction = build_u0((self.fixture(1, xg=None),))
        self.assertAlmostEqual(prediction.modeled_points or 0.0, 2.3)
        self.assertEqual(prediction.expected_goals, 0.0)
        self.assertIsNone(prediction.expected_goals_for_evaluation)
        self.assertFalse(prediction.prediction_complete)

    def test_availability_and_position_scoring_constants(self) -> None:
        for position, multiplier in (("GK", 10), ("DEF", 6), ("MID", 5), ("FWD", 4)):
            fixture = U0FixtureInput(
                1, position, 90, 1.0, 1.0, Availability("s", None, True, True)
            )
            forced = build_u0((fixture,)).fixture_predictions[0]
            self.assertEqual(forced.expected_minutes, 0.0)
            self.assertEqual(forced.goal_points, 0.0)
            available = build_u0((replace(fixture, availability=Availability("a", None, True, True)),)).fixture_predictions[0]
            self.assertEqual(available.goal_points, float(multiplier))
            self.assertEqual(available.assist_points, 3.0)

    def test_u0_parity_check_accepts_exact_values_and_rejects_drift(self) -> None:
        prediction = build_u0((self.fixture(1),))
        expected = {
            "fixture_count": 1,
            "gameweek_expected_minutes": 90.0,
            "gameweek_appearance_points": 2.0,
            "expected_goals": 0.2,
            "expected_assists": 0.1,
            "modeled_points": 3.3,
            "prediction_complete": True,
        }
        assert_u0_parity(prediction, expected)
        with self.assertRaisesRegex(ResearchInputError, "modeled_points") as caught:
            assert_u0_parity(prediction, {**expected, "modeled_points": 3.4})
        self.assertEqual(caught.exception.code, "U0_PARITY_FAILURE")

    def test_u0_adapter_parity_against_tracked_production_implementation(self) -> None:
        from fpl_decision_engine.predictions import predict_xfp_v01_from_feature
        from tests.test_predictions import feature_row, write_feature

        rows = [
            feature_row(1, "Available", 3, "Midfielder", previous_minutes=90, xg_per_90=0.2, xa_per_90=0.1),
            feature_row(2, "Suspended", 2, "Defender", previous_minutes=60, xg_per_90=0.3, xa_per_90=0.2, status="s"),
            feature_row(3, "Missing", 3, "Midfielder", previous_minutes=None, prior_minutes=None, xg_per_90=None, xa_per_90=None, chance=0),
            feature_row(4, "Double", 4, "Forward", fixture_id=30, fixture_count=2, previous_minutes=45, xg_per_90=0.4, xa_per_90=0.2),
            feature_row(4, "Double", 4, "Forward", fixture_id=31, fixture_count=2, previous_minutes=45, xg_per_90=0.4, xa_per_90=0.2),
            feature_row(5, "Blank", 1, "Goalkeeper", fixture_id=None, fixture_count=0),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            feature_path = root / "features.parquet"
            write_feature(feature_path, rows)
            outputs = predict_xfp_v01_from_feature(
                feature_path=feature_path,
                prediction_data_root=root / "predictions",
                season="2026-27",
                snapshot_timestamp="20260825T073532.450889Z",
                target_gameweek=2,
            )
            connection = duckdb.connect(":memory:")
            try:
                observed_rows = connection.execute(
                    """SELECT g.fpl_player_id,g.fixture_count,
                              g.gameweek_expected_minutes_v01,
                              g.gameweek_appearance_xfp_v01,
                              coalesce(sum(f.expected_goals_v01)
                                FILTER(WHERE f.target_has_fixture),0.0),
                              coalesce(sum(f.expected_assists_v01)
                                FILTER(WHERE f.target_has_fixture),0.0),
                              g.gameweek_xfp_v01,g.prediction_complete
                       FROM read_parquet(?) g JOIN read_parquet(?) f
                         USING(fpl_player_id,target_gameweek)
                       GROUP BY ALL ORDER BY g.fpl_player_id""",
                    [str(outputs.gameweek_path), str(outputs.fixture_path)],
                ).fetchall()
            finally:
                connection.close()
        inputs = {
            1: (U0FixtureInput(20, "MID", 90, 0.2, 0.1, Availability("a", None, True, True)),),
            2: (U0FixtureInput(20, "DEF", 60, 0.3, 0.2, Availability("s", None, True, True)),),
            3: (U0FixtureInput(20, "MID", None, None, None, Availability("a", 0, True, True)),),
            4: (
                U0FixtureInput(30, "FWD", 45, 0.4, 0.2, Availability("a", None, True, True)),
                U0FixtureInput(31, "FWD", 45, 0.4, 0.2, Availability("a", None, True, True)),
            ),
            5: (),
        }
        for player_id, fixture_count, minutes, appearance, goals, assists, modeled, complete in observed_rows:
            with self.subTest(player_id=player_id):
                assert_u0_parity(
                    build_u0(inputs[player_id]),
                    {
                        "fixture_count": fixture_count,
                        "gameweek_expected_minutes": minutes,
                        "gameweek_appearance_points": appearance,
                        "expected_goals": goals,
                        "expected_assists": assists,
                        "modeled_points": modeled,
                        "prediction_complete": complete,
                    },
                )

    def test_nonintegral_band_adapter_minutes_fail(self) -> None:
        with self.assertRaises(ResearchInputError) as caught:
            self.fixture(1, minutes=59.5)
        self.assertEqual(caught.exception.code, "U0_NONINTEGRAL_MINUTES")


class UM1KernelTests(unittest.TestCase):
    def assert_pmf(self, values: tuple[float, ...]) -> None:
        self.assertTrue(all(math.isfinite(value) and value >= 0 for value in values))
        self.assertAlmostEqual(math.fsum(values), 1.0, places=12)

    def test_prior_only_formula_normalization_moments_and_identities(self) -> None:
        prediction = build_um1(target(), ())
        fixture = prediction.fixture_predictions[0]
        self.assertEqual(prediction.candidate_identity, UM1_IDENTITY)
        self.assertEqual(prediction.history_state, "PRIOR_ONLY")
        self.assert_pmf(fixture.minute_pmf)
        self.assert_pmf(fixture.band_probabilities)
        self.assertAlmostEqual(
            fixture.expected_minutes,
            math.fsum(index * mass for index, mass in enumerate(fixture.minute_pmf)),
        )
        self.assertAlmostEqual(fixture.appearance_probability, 1.0 - fixture.minute_pmf[0])
        self.assertAlmostEqual(fixture.start_probability, math.fsum(fixture.start_pmf))
        self.assertAlmostEqual(
            fixture.expected_appearance_points,
            math.fsum(fixture.minute_pmf[1:]) + math.fsum(fixture.minute_pmf[60:]),
        )
        self.assertAlmostEqual(fixture.start_probability, 0.35)
        for observed, expected in zip(
            fixture.band_probabilities,
            (0.5035, 0.119, 0.065, 0.171, 0.1415),
        ):
            self.assertAlmostEqual(observed, expected)
        self.assertAlmostEqual(math.fsum(fixture.no_start_pmf[1:30]), 0.15 * 0.70)
        self.assertAlmostEqual(math.fsum(fixture.start_pmf[60:90]), 0.35 * 0.45)

    def test_exact_weighted_joint_update_and_recorded_start_at_zero(self) -> None:
        rows = (
            history_row(minutes=60, starts=1),
            history_row(fixture_id=802, minutes=0, starts=1, xg=0.0, xa=0.0),
        )
        prediction = build_um1(target(), rows)
        fixture = prediction.fixture_predictions[0]
        prior_s60 = 0.35 * 0.45 / 30.0
        prior_s0 = 0.35 * 0.01
        self.assertAlmostEqual(fixture.start_pmf[60], (4 * prior_s60 + 1) / 6)
        self.assertAlmostEqual(fixture.start_pmf[0], (4 * prior_s0 + 1) / 6)
        self.assertGreater(fixture.start_probability, fixture.appearance_probability)

    def test_window_weights_double_observations_and_overflow(self) -> None:
        rows = (
            history_row(gameweek=8, fixture_id=801, minutes=100, starts=1),
            history_row(gameweek=8, fixture_id=802, minutes=10, starts=0),
            history_row(gameweek=1, fixture_id=101, minutes=90, starts=1),
        )
        prediction = build_um1(target(), rows)
        self.assertEqual(prediction.history_observations, 3)
        self.assertEqual(prediction.overflow_count, 1)
        self.assertIn("MINUTES_OVERFLOW_CAPPED_AT_90", prediction.flags)
        fixture = prediction.fixture_predictions[0]
        self.assertGreater(fixture.start_pmf[90], 0.35 * 0.40)
        self.assertGreater(fixture.no_start_pmf[10], 0.15 * 0.70 / 29.0)

    def test_um1_window_includes_boundary_and_excludes_older_row(self) -> None:
        context = replace(target(), target_gameweek=12)
        rows = (
            history_row(gameweek=3, fixture_id=301, minutes=10, starts=0),
            history_row(gameweek=4, fixture_id=401, minutes=90, starts=1),
        )
        prediction = build_um1(context, rows)
        self.assertEqual(prediction.history_observations, 1)
        self.assertGreater(prediction.fixture_predictions[0].start_pmf[90], 0.35 * 0.40)

    def test_availability_hard_soft_unknown_and_unknown_risk(self) -> None:
        base = build_um1(target(), ())
        hard = build_um1(target(availability=Availability("u", None, True, True)), ())
        soft = build_um1(target(availability=Availability("d", 50, True, True)), ())
        unknown = build_um1(target(availability=Availability("u", 0, False, True)), ())
        risk = build_um1(target(availability=Availability("d", None, True, True)), ())
        self.assertEqual(hard.expected_minutes, 0.0)
        self.assertEqual(hard.gameweek_minute_pmf[0], 1.0)
        self.assertAlmostEqual(soft.expected_minutes, base.expected_minutes / 2)
        self.assertAlmostEqual(soft.start_probability, base.start_probability / 2)
        self.assertEqual(unknown.expected_minutes, base.expected_minutes)
        self.assertIn("AVAILABILITY_UNKNOWN", unknown.flags)
        self.assertIn("AVAILABILITY_UNKNOWN_RISK", risk.flags)

    def test_dgw_convolution_and_per_fixture_appearance_points(self) -> None:
        single = build_um1(target(), ())
        double = build_um1(target(fixture_ids=(901, 902)), ())
        self.assertEqual(len(double.gameweek_minute_pmf), 181)
        self.assert_pmf(double.gameweek_minute_pmf)
        self.assertAlmostEqual(double.expected_minutes, 2 * single.expected_minutes)
        self.assertAlmostEqual(
            double.expected_appearance_points, 2 * single.expected_appearance_points
        )
        thresholded_total = math.fsum(double.gameweek_minute_pmf[1:]) + math.fsum(
            double.gameweek_minute_pmf[60:]
        )
        self.assertNotAlmostEqual(double.expected_appearance_points, thresholded_total)
        self.assertAlmostEqual(
            double.appearance_probability,
            1 - single.gameweek_minute_pmf[0] ** 2,
        )

    def test_two_cameos_and_one_start_keep_per_fixture_scoring_identity(self) -> None:
        cameo = tuple(1.0 if minute == 20 else 0.0 for minute in range(91))
        start = tuple(1.0 if minute == 60 else 0.0 for minute in range(91))
        self.assertEqual(2 * expected_appearance_points(cameo), 2.0)
        self.assertEqual(expected_appearance_points(start), 2.0)
        convolved_cameos = tuple(
            1.0 if minute == 40 else 0.0 for minute in range(181)
        )
        incorrectly_thresholded_total = (
            math.fsum(convolved_cameos[1:]) + math.fsum(convolved_cameos[60:])
        )
        self.assertEqual(incorrectly_thresholded_total, 1.0)

    def test_blank_and_new_entrant_prior_only(self) -> None:
        blank = build_um1(target(fixture_ids=()), ())
        self.assertEqual(blank.gameweek_minute_pmf, (1.0,))
        self.assertEqual(blank.band_probabilities, (1.0, 0.0, 0.0, 0.0, 0.0))
        self.assertEqual(blank.expected_appearance_points, 0.0)
        self.assertEqual(blank.appearance_probability, 0.0)
        self.assertEqual(blank.start_probability, 0.0)
        self.assertEqual(blank.history_state, "PRIOR_ONLY")

    def test_missing_duplicate_and_causally_unsafe_history_fail(self) -> None:
        cases = (
            ((history_row(minutes=None),), "UM1_MISSING_START_OR_MINUTES"),
            ((history_row(starts=None),), "UM1_MISSING_START_OR_MINUTES"),
            ((history_row(), history_row()), "DUPLICATE_HISTORY_FIXTURE"),
            ((history_row(gameweek=9),), "CAUSAL_CUTOFF_VIOLATION"),
        )
        for rows, code in cases:
            with self.subTest(code=code), self.assertRaises(ResearchInputError) as caught:
                build_um1(target(), rows)
            self.assertEqual(caught.exception.code, code)

    def test_ineligible_source_universe_and_post_cutoff_rows_are_not_observations(self) -> None:
        rows = (
            history_row(source_universe_member=False),
            history_row(fixture_id=802, kickoff_before_cutoff=False),
        )
        prediction = build_um1(target(), rows)
        self.assertEqual(prediction.history_state, "PRIOR_ONLY")
        self.assertEqual(prediction.history_observations, 0)


class UA1KernelTests(unittest.TestCase):
    def test_fixed_fallback_and_exact_prior_only_mixture(self) -> None:
        prediction = build_ua1(target(), (), ())
        xg = prediction.xg
        xa = prediction.xa
        self.assertTrue(xg.population_prior.used_fixed_fallback)
        self.assertEqual(xg.population_prior.prior_mean, 0.25)
        self.assertEqual(xa.population_prior.prior_mean, 0.20)
        self.assertEqual(xg.history_state, "PRIOR_ONLY")
        self.assertEqual(xg.component_weights, (0.5, 0.5))
        self.assertEqual(xg.shapes, (0.25, 2.5))
        self.assertEqual(xg.rates, (1.0, 10.0))
        self.assertAlmostEqual(xg.rate_mean or 0.0, 0.25)
        self.assertAlmostEqual(xg.rate_variance or 0.0, 0.1375)
        self.assertAlmostEqual(xg.weighted_prior_minutes_summary or 0.0, 495.0)

    def test_peer_population_mean_excludes_target_and_caps_each_peer(self) -> None:
        peers = sufficient_peers(minutes=1800, xg=10.0, xa=4.0)
        prediction = build_ua1(target(), (), peers)
        prior = prediction.xg.population_prior
        self.assertEqual(prior.contributing_peers, 20)
        self.assertAlmostEqual(prior.population_exposure, 200.0)
        self.assertAlmostEqual(prior.population_events, 100.0)
        self.assertEqual(prior.population_missing_exposure, 0.0)
        self.assertAlmostEqual(prior.unclipped_mean or 0.0, 0.5)
        self.assertEqual(prior.prior_mean, 0.5)
        self.assertFalse(prior.used_fixed_fallback)
        with self.assertRaises(ResearchInputError) as caught:
            build_ua1(
                target(),
                (),
                (PeerHistory(1, "MID", (history_row(),)), *peers),
            )
        self.assertEqual(caught.exception.code, "TARGET_INCLUDED_IN_PRIOR")

    def test_population_minimums_and_clipping(self) -> None:
        too_few = sufficient_peers()[:19]
        fallback = build_ua1(target(), (), too_few).xg.population_prior
        self.assertTrue(fallback.used_fixed_fallback)
        self.assertAlmostEqual(fallback.unclipped_mean or 0.0, 0.5)
        low = build_ua1(target(), (), sufficient_peers(xg=0.001)).xg.population_prior
        self.assertEqual(low.prior_mean, 0.01)
        self.assertTrue(low.mean_clipped)
        high = build_ua1(target(), (), sufficient_peers(xg=30.0)).xg.population_prior
        self.assertEqual(high.prior_mean, 2.0)
        self.assertTrue(high.mean_clipped)

    def test_observed_zero_is_not_missing(self) -> None:
        row = history_row(xg=0.0, xa=0.0)
        prediction = build_ua1(target(), (row,), ())
        self.assertEqual(prediction.xg.status, "COMPLETE")
        self.assertEqual(prediction.xg.observed_events, 0.0)
        self.assertGreater(prediction.xg.observed_exposure, 0.0)
        self.assertEqual(prediction.xg.missing_exposure, 0.0)
        self.assertFalse(prediction.xg.partial_missingness)

    def test_positive_playing_exposure_with_all_event_values_missing_is_null(self) -> None:
        prediction = build_ua1(target(), (history_row(xg=None, xa=None),), ())
        self.assertEqual(prediction.xg.status, "MISSING_EVENT_HISTORY")
        self.assertEqual(prediction.xg.history_state, "ALL_RELEVANT_EVENTS_MISSING")
        self.assertGreater(prediction.xg.playing_exposure, 0)
        self.assertEqual(prediction.xg.observed_exposure, 0)
        self.assertIsNone(prediction.xg.rate_mean)
        self.assertEqual(prediction.xa.status, "MISSING_EVENT_HISTORY")

    def test_xg_xa_are_separate_and_partial_missingness_is_explicit(self) -> None:
        rows = (
            history_row(fixture_id=801, xg=None, xa=0.2),
            history_row(fixture_id=802, gameweek=7, xg=0.0, xa=None),
        )
        prediction = build_ua1(target(), rows, ())
        self.assertTrue(prediction.xg.partial_missingness)
        self.assertTrue(prediction.xa.partial_missingness)
        self.assertNotEqual(prediction.xg.observed_exposure, prediction.xa.observed_exposure)
        self.assertEqual(prediction.xg.observed_events, 0.0)
        self.assertGreater(prediction.xa.observed_events, 0.0)

    def test_generalized_bayes_algebra_and_quantile_accuracy(self) -> None:
        peers = sufficient_peers()
        prediction = build_ua1(target(), (history_row(xg=0.3),), peers).xg
        self.assertEqual(prediction.shapes, (0.8, 5.3))
        self.assertEqual(prediction.rates, (2.0, 11.0))
        weights = prediction.component_weights or ()
        self.assertAlmostEqual(math.fsum(weights), 1.0)
        expected_mean = math.fsum(
            weight * shape / rate
            for weight, shape, rate in zip(weights, prediction.shapes or (), prediction.rates or ())
        )
        self.assertAlmostEqual(prediction.rate_mean or 0.0, expected_mean)
        for level, quantile in zip((0.1, 0.5, 0.9), prediction.rate_quantiles or ()):
            self.assertLessEqual(
                abs(_gamma_mixture_cdf(quantile, weights, prediction.shapes or (), prediction.rates or ()) - level),
                1e-10,
            )

    def test_regularized_gamma_matches_independent_closed_forms(self) -> None:
        cases = (
            (0.5, 0.25, math.erf(0.5)),
            (
                5.0,
                3.0,
                1.0
                - math.exp(-3.0)
                * math.fsum(3.0**index / math.factorial(index) for index in range(5)),
            ),
            (1.0, 3.0, 1.0 - math.exp(-3.0)),
            (2.0, 5.0, 1.0 - math.exp(-5.0) * 6.0),
        )
        for shape, value, expected in cases:
            with self.subTest(shape=shape, value=value):
                self.assertAlmostEqual(
                    _regularized_gamma_p(shape, value), expected, places=12
                )

    def test_ua1_window_includes_boundary_and_excludes_older_row(self) -> None:
        context = replace(target(), target_gameweek=14)
        rows = (
            history_row(gameweek=1, fixture_id=101, xg=100.0, xa=100.0),
            history_row(gameweek=2, fixture_id=201, xg=0.3, xa=0.2),
        )
        prediction = build_ua1(context, rows, ())
        boundary_weight = 2 ** (-11 / 6)
        self.assertAlmostEqual(prediction.xg.observed_events, boundary_weight * 0.3)
        self.assertAlmostEqual(prediction.xa.observed_events, boundary_weight * 0.2)

    def test_count_distribution_moments_and_combined_minute_integration(self) -> None:
        context = target(fixture_ids=(901, 902))
        rates = build_ua1(context, (), ())
        u0 = build_u0(
            (
                U0FixtureInput(901, "MID", 60, 0.1, 0.1, context.availability),
                U0FixtureInput(902, "MID", 60, 0.1, 0.1, context.availability),
            )
        )
        ua_only = assemble_ua1_only(context, u0, rates)
        self.assertAlmostEqual(ua_only.expected_goals or 0.0, 120 / 90 * 0.25)
        goal = ua_only.goal_count_prediction
        self.assertIsNotNone(goal)
        self.assertAlmostEqual(math.fsum(goal.probability(i) or 0 for i in range(100)), 1.0, places=10)
        self.assertEqual(goal.central_80_interval[0], 0)

        minutes = build_um1(context, ())
        combined = assemble_combined(context, minutes, rates)
        self.assertEqual(combined.candidate_identity, COMBINED_IDENTITY)
        self.assertAlmostEqual(combined.expected_goals or 0.0, minutes.expected_minutes / 90 * 0.25)
        self.assertIsNotNone(combined.goal_count_prediction.integrated_minute_pmf)
        self.assertAlmostEqual(
            math.fsum(combined.goal_count_prediction.probability(i) or 0 for i in range(100)),
            1.0,
            places=10,
        )

    def test_missing_minutes_invalid_values_duplicates_and_identity_fail(self) -> None:
        with self.assertRaises(ResearchInputError) as missing:
            build_ua1(target(), (history_row(minutes=None),), ())
        self.assertEqual(missing.exception.code, "UA1_MISSING_MINUTES")
        with self.assertRaises(ResearchContractError):
            history_row(xg=float("nan"))
        with self.assertRaises(ResearchContractError):
            history_row(minutes=0, xg=0.1)
        with self.assertRaises(ResearchInputError) as duplicate:
            build_ua1(target(), (history_row(), history_row()), ())
        self.assertEqual(duplicate.exception.code, "DUPLICATE_HISTORY_FIXTURE")
        wrong = replace(history_row(), season="other")
        with self.assertRaises(ResearchInputError) as identity:
            build_ua1(target(), (wrong,), ())
        self.assertEqual(identity.exception.code, "HISTORY_IDENTITY_MISMATCH")

    def test_hostile_target_outcome_rows_cannot_enter_prediction_history(self) -> None:
        base = build_ua1(target(), (), ())
        for hostile_xg in (0.0, 1000.0):
            with self.assertRaises(ResearchInputError) as caught:
                build_ua1(target(), (history_row(gameweek=9, xg=hostile_xg),), ())
            self.assertEqual(caught.exception.code, "CAUSAL_CUTOFF_VIOLATION")
        self.assertEqual(base, build_ua1(target(), (), ()))

    def test_extreme_valid_values_remain_finite(self) -> None:
        prediction = build_ua1(
            target(),
            (history_row(minutes=1_000_000, xg=1_000_000, xa=500_000),),
            sufficient_peers(),
        )
        for event in (prediction.xg, prediction.xa):
            self.assertTrue(math.isfinite(event.rate_mean or float("nan")))
            self.assertTrue(math.isfinite(event.rate_variance or float("nan")))
            self.assertTrue(all(math.isfinite(value) for value in event.rate_quantiles or ()))


class AssemblyAndContractTests(unittest.TestCase):
    def test_three_candidate_assemblies_and_missingness_rules(self) -> None:
        context = target()
        minutes = build_um1(context, ())
        rates = build_ua1(context, (), ())
        um1 = assemble_um1_only(context, minutes, raw_xg_per90=0.3, raw_xa_per90=0.2)
        self.assertEqual(um1.candidate_identity, UM1_IDENTITY)
        self.assertTrue(um1.prediction_complete)
        missing_um1 = assemble_um1_only(context, minutes, raw_xg_per90=None, raw_xa_per90=0.2)
        self.assertFalse(missing_um1.prediction_complete)
        self.assertAlmostEqual(
            missing_um1.modeled_points or 0.0,
            minutes.expected_appearance_points
            + minutes.expected_minutes / 90.0 * 0.2 * 3.0,
        )

        missing_rates = build_ua1(context, (history_row(xg=None, xa=None),), ())
        u0 = build_u0((U0FixtureInput(901, "MID", 90, 0.3, 0.2, context.availability),))
        ua1 = assemble_ua1_only(context, u0, missing_rates)
        combined = assemble_combined(context, minutes, missing_rates)
        self.assertFalse(ua1.prediction_complete)
        self.assertEqual(ua1.modeled_points, 2.0)
        self.assertFalse(combined.prediction_complete)
        self.assertIsNone(combined.modeled_points)

    def test_blank_overrides_missing_component_inputs(self) -> None:
        context = target(fixture_ids=())
        minutes = build_um1(context, ())
        rates = build_ua1(context, (history_row(xg=None, xa=None),), ())
        self.assertEqual(assemble_um1_only(context, minutes, raw_xg_per90=None, raw_xa_per90=None).modeled_points, 0.0)
        self.assertEqual(assemble_ua1_only(context, build_u0(()), rates).modeled_points, 0.0)
        self.assertEqual(assemble_combined(context, minutes, rates).modeled_points, 0.0)

    def header(self) -> ArtifactHeader:
        return ArtifactHeader(
            contract_version=ARTIFACT_CONTRACT_VERSION,
            protocol_identity=PROTOCOL_IDENTITY,
            candidate_identities=("xfp_v01", UM1_IDENTITY, UA1_IDENTITY, COMBINED_IDENTITY),
            stage="PREDICTION_ONLY",
            source_manifest_identity="UNRESOLVED_PUBLIC_SOURCE_MANIFEST",
            source_manifest_sha256="UNRESOLVED",
            cutoff_view_identity="UNRESOLVED_CAUSAL_VIEW",
            cutoff_view_sha256="UNRESOLVED",
            outcome_joined=False,
            canonical_order="season,target_gameweek,element_id,fixture_id",
            publication_policy="EXCLUSIVE_NO_OVERWRITE",
            confirmation_state="UNOPENED_UNAUTHORIZED",
            hash_claim_scope="BYTE_IDENTITY_ONLY_NOT_SEMANTIC_OR_HISTORICAL_PROOF",
        )

    def test_artifact_boundary_schema_is_strict_and_matches_runtime_contract(self) -> None:
        header = self.header()
        payload = json.loads(canonical_json_bytes(header))
        schema_path = Path(__file__).resolve().parents[1] / "contracts/research/task033c1_prediction_header.schema.json"
        schema = json.loads(schema_path.read_bytes())
        jsonschema.Draft202012Validator(schema).validate(payload)
        self.assertEqual(ArtifactHeader.from_mapping(payload), header)
        with self.assertRaises(ResearchContractError) as extra:
            ArtifactHeader.from_mapping({**payload, "target_outcomes": []})
        self.assertEqual(extra.exception.code, "WRONG_FIELDS")
        with self.assertRaises(ResearchContractError) as outcome:
            ArtifactHeader(**{**asdict(header), "outcome_joined": True})
        self.assertEqual(outcome.exception.code, "OUTCOME_LEAKAGE")
        with self.assertRaises(ResearchContractError) as candidate:
            ArtifactHeader(**{**asdict(header), "candidate_identities": ("M1",)})
        self.assertEqual(candidate.exception.code, "WRONG_CANDIDATE_IDENTITY")

    def test_strict_input_mapping_rejects_extra_outcome_and_malformed_values(self) -> None:
        payload = asdict(history_row())
        with self.assertRaises(ResearchContractError) as caught:
            HistoryFixture.from_mapping({**payload, "actual_goals": 99})
        self.assertEqual(caught.exception.code, "WRONG_FIELDS")
        with self.assertRaises(ResearchContractError):
            Availability.from_mapping(
                {
                    "status": "a",
                    "chance_of_playing_next_round": 101,
                    "known_pre_deadline": True,
                    "is_target_next_round": True,
                }
            )

    def test_public_result_contracts_reject_inconsistent_moments_and_probabilities(self) -> None:
        minutes = build_um1(target(), ())
        with self.assertRaises(ResearchContractError) as moment:
            replace(minutes, expected_minutes=minutes.expected_minutes + 1.0)
        self.assertEqual(moment.exception.code, "INCONSISTENT_MOMENT")
        fixture = minutes.fixture_predictions[0]
        malformed = list(fixture.minute_pmf)
        malformed[0] += 0.01
        with self.assertRaises(ResearchContractError) as probability:
            replace(fixture, minute_pmf=tuple(malformed))
        self.assertEqual(probability.exception.code, "PMF_NOT_NORMALIZED")

    def test_canonical_serialization_is_stable_finite_and_newline_terminated(self) -> None:
        header = self.header()
        first = canonical_json_bytes(header)
        second = canonical_json_bytes(asdict(header))
        self.assertEqual(first, second)
        self.assertTrue(first.endswith(b"\n"))
        self.assertEqual(hashlib.sha256(first).hexdigest(), hashlib.sha256(second).hexdigest())
        with self.assertRaises(ResearchContractError):
            canonical_json_bytes({"bad": float("inf")})

    def test_results_are_deterministic_and_ordered(self) -> None:
        rows = (
            history_row(fixture_id=802, gameweek=7),
            history_row(fixture_id=801, gameweek=8),
        )
        first = build_um1(target(fixture_ids=(901, 902)), rows)
        second = build_um1(target(fixture_ids=(901, 902)), tuple(reversed(rows)))
        self.assertEqual(first, second)
        self.assertEqual(
            canonical_json_bytes(first),
            canonical_json_bytes(second),
        )


if __name__ == "__main__":
    unittest.main()
