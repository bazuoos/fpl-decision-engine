"""Task033C1 deterministic uncertainty-model kernels.

This module is prediction-side only.  It deliberately has no path, DuckDB,
Parquet, outcome, decision, or publication API.  Callers must supply an already
audited causal view; the kernels repeat the most important chronology and value
checks defensively.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


PROTOCOL_IDENTITY = "TASK033B1B-candidate-freeze-d6716bc"
UM1_IDENTITY = "UM1-joint-minute-dirichlet-v1"
UA1_IDENTITY = "UA1-gamma-mixture-loss-update-v1"
COMBINED_IDENTITY = "UM1UA1-v1"
U0_IDENTITY = "xfp_v01"
ARTIFACT_CONTRACT_VERSION = "task033-prediction-boundary-v1"
PROBABILITY_TOLERANCE = 1e-12
MOMENT_TOLERANCE = 1e-10

POSITIONS = ("GK", "DEF", "MID", "FWD")
GOAL_POINTS = {"GK": 10, "DEF": 6, "MID": 5, "FWD": 4}
FALLBACK_RATES = {
    "GK": {"xg": 0.01, "xa": 0.01},
    "DEF": {"xg": 0.08, "xa": 0.08},
    "MID": {"xg": 0.25, "xa": 0.20},
    "FWD": {"xg": 0.40, "xa": 0.15},
}
PRIOR_STRENGTHS = (1.0, 10.0)
PRIOR_COMPONENT_WEIGHTS = (0.5, 0.5)
REGISTERED_MINUTE_BANDS = ((0, 0), (1, 29), (30, 59), (60, 89), (90, None))


class ResearchContractError(ValueError):
    """A public research contract was malformed or inconsistent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ResearchInputError(ResearchContractError):
    """A prediction input was missing, invalid, or causally unsafe."""


def _require_finite(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResearchContractError("INVALID_NUMBER", f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ResearchContractError("NONFINITE_NUMBER", f"{field} must be finite")
    return result


def _require_nonnegative(value: float, field: str) -> float:
    result = _require_finite(value, field)
    if result < 0:
        raise ResearchContractError("NEGATIVE_NUMBER", f"{field} must be nonnegative")
    return result


def _require_integer(value: Any, field: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResearchContractError("INVALID_INTEGER", f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ResearchContractError("INTEGER_OUT_OF_RANGE", f"{field} is out of range")
    return value


def _require_position(position: str) -> None:
    if position not in POSITIONS:
        raise ResearchContractError("INVALID_POSITION", "position must be GK/DEF/MID/FWD")


def _validate_probability(value: float, field: str) -> float:
    result = _require_finite(value, field)
    if result < -PROBABILITY_TOLERANCE or result > 1.0 + PROBABILITY_TOLERANCE:
        raise ResearchContractError("INVALID_PROBABILITY", f"{field} is outside [0,1]")
    return min(1.0, max(0.0, result))


def _validate_pmf(values: Sequence[float], field: str) -> tuple[float, ...]:
    if not values:
        raise ResearchContractError("EMPTY_PMF", f"{field} must not be empty")
    checked = tuple(_validate_probability(value, field) for value in values)
    total = math.fsum(checked)
    if abs(total - 1.0) > PROBABILITY_TOLERANCE:
        raise ResearchContractError("PMF_NOT_NORMALIZED", f"{field} must sum to one")
    return checked


def _require_close(left: float, right: float, field: str) -> None:
    if abs(left - right) > MOMENT_TOLERANCE:
        raise ResearchContractError("INCONSISTENT_MOMENT", f"{field} is inconsistent")


def _strict_fields(value: Mapping[str, Any], expected: set[str], contract: str) -> None:
    if not isinstance(value, Mapping):
        raise ResearchContractError("INVALID_OBJECT", f"{contract} must be an object")
    observed = set(value)
    if observed != expected:
        missing = sorted(expected - observed)
        extra = sorted(observed - expected)
        raise ResearchContractError(
            "WRONG_FIELDS", f"{contract} fields differ (missing={missing}, extra={extra})"
        )


@dataclass(frozen=True)
class Availability:
    status: str | None
    chance_of_playing_next_round: int | None
    known_pre_deadline: bool
    is_target_next_round: bool

    def __post_init__(self) -> None:
        if self.status is not None and self.status.lower() not in {"a", "d", "i", "s", "u"}:
            raise ResearchContractError("INVALID_AVAILABILITY_STATUS", "unknown status code")
        chance = self.chance_of_playing_next_round
        if chance is not None:
            _require_integer(chance, "chance_of_playing_next_round", minimum=0)
            if chance > 100:
                raise ResearchContractError("INVALID_AVAILABILITY_CHANCE", "chance exceeds 100")
        if not isinstance(self.known_pre_deadline, bool) or not isinstance(
            self.is_target_next_round, bool
        ):
            raise ResearchContractError("INVALID_BOOLEAN", "availability flags must be Boolean")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> Availability:
        expected = {
            "status",
            "chance_of_playing_next_round",
            "known_pre_deadline",
            "is_target_next_round",
        }
        _strict_fields(value, expected, "Availability")
        return cls(**dict(value))

    def multiplier_and_flags(self) -> tuple[float, tuple[str, ...]]:
        if not self.known_pre_deadline:
            return 1.0, ("AVAILABILITY_UNKNOWN",)
        status = self.status.lower() if self.status is not None else None
        chance = self.chance_of_playing_next_round
        if status in {"s", "u"} or (self.is_target_next_round and chance == 0):
            return 0.0, ("AVAILABILITY_FORCED_ZERO",)
        flags: list[str] = []
        if status in {"d", "i"} and chance is None:
            flags.append("AVAILABILITY_UNKNOWN_RISK")
        if self.is_target_next_round and chance is not None:
            return chance / 100.0, tuple(flags)
        return 1.0, tuple(flags)


@dataclass(frozen=True)
class TargetContext:
    season: str
    target_gameweek: int
    element_id: int
    position: str
    fixture_ids: tuple[int, ...]
    availability: Availability

    def __post_init__(self) -> None:
        if not isinstance(self.season, str) or not self.season:
            raise ResearchContractError("INVALID_SEASON", "season must be non-empty")
        _require_integer(self.target_gameweek, "target_gameweek", minimum=1)
        _require_integer(self.element_id, "element_id", minimum=1)
        _require_position(self.position)
        if not isinstance(self.fixture_ids, tuple):
            raise ResearchContractError("INVALID_FIXTURE_IDS", "fixture_ids must be a tuple")
        for fixture_id in self.fixture_ids:
            _require_integer(fixture_id, "fixture_id", minimum=1)
        if len(set(self.fixture_ids)) != len(self.fixture_ids):
            raise ResearchContractError("DUPLICATE_TARGET_FIXTURE", "target fixtures must be unique")
        if tuple(sorted(self.fixture_ids)) != self.fixture_ids:
            raise ResearchContractError("NONCANONICAL_FIXTURE_ORDER", "fixture_ids must be sorted")
        if not isinstance(self.availability, Availability):
            raise ResearchContractError("INVALID_AVAILABILITY", "availability contract required")


@dataclass(frozen=True)
class HistoryFixture:
    season: str
    element_id: int
    fixture_id: int
    gameweek: int
    position: str
    minutes: int | None
    starts: int | None
    xg: float | None
    xa: float | None
    source_universe_member: bool = True
    kickoff_before_cutoff: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.season, str) or not self.season:
            raise ResearchContractError("INVALID_SEASON", "season must be non-empty")
        _require_integer(self.element_id, "element_id", minimum=1)
        _require_integer(self.fixture_id, "fixture_id", minimum=1)
        _require_integer(self.gameweek, "gameweek", minimum=1)
        _require_position(self.position)
        if self.minutes is not None:
            _require_integer(self.minutes, "minutes", minimum=0)
        if self.starts is not None:
            if isinstance(self.starts, bool) or self.starts not in (0, 1):
                raise ResearchContractError("INVALID_START", "starts must be 0, 1, or null")
        for name, value in (("xg", self.xg), ("xa", self.xa)):
            if value is not None:
                _require_nonnegative(value, name)
                if self.minutes == 0 and value > 0:
                    raise ResearchContractError(
                        "POSITIVE_EVENT_WITH_ZERO_MINUTES",
                        f"positive {name} with zero minutes is contradictory",
                    )
        if not isinstance(self.source_universe_member, bool) or not isinstance(
            self.kickoff_before_cutoff, bool
        ):
            raise ResearchContractError("INVALID_BOOLEAN", "history eligibility flags must be Boolean")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> HistoryFixture:
        expected = {
            "season",
            "element_id",
            "fixture_id",
            "gameweek",
            "position",
            "minutes",
            "starts",
            "xg",
            "xa",
            "source_universe_member",
            "kickoff_before_cutoff",
        }
        _strict_fields(value, expected, "HistoryFixture")
        return cls(**dict(value))


@dataclass(frozen=True)
class PeerHistory:
    element_id: int
    target_position: str
    history: tuple[HistoryFixture, ...]

    def __post_init__(self) -> None:
        _require_integer(self.element_id, "peer element_id", minimum=1)
        _require_position(self.target_position)
        if not isinstance(self.history, tuple):
            raise ResearchContractError("INVALID_PEER_HISTORY", "peer history must be a tuple")
        if any(not isinstance(row, HistoryFixture) for row in self.history):
            raise ResearchContractError("INVALID_PEER_HISTORY", "peer history rows must be typed")


@dataclass(frozen=True)
class U0FixtureInput:
    fixture_id: int
    position: str
    previous_gameweek_minutes: int | float | None
    raw_xg_per90: float | None
    raw_xa_per90: float | None
    availability: Availability

    def __post_init__(self) -> None:
        _require_integer(self.fixture_id, "fixture_id", minimum=1)
        _require_position(self.position)
        if self.previous_gameweek_minutes is not None:
            minutes = _require_nonnegative(
                self.previous_gameweek_minutes, "previous_gameweek_minutes"
            )
            if not minutes.is_integer():
                raise ResearchInputError(
                    "U0_NONINTEGRAL_MINUTES",
                    "U0 band adapter refuses unexpected nonintegral baseline minutes",
                )
        for name, value in (("raw_xg_per90", self.raw_xg_per90), ("raw_xa_per90", self.raw_xa_per90)):
            if value is not None:
                _require_nonnegative(value, name)
        if not isinstance(self.availability, Availability):
            raise ResearchContractError("INVALID_AVAILABILITY", "availability contract required")


@dataclass(frozen=True)
class U0FixturePrediction:
    fixture_id: int
    expected_minutes: float | None
    appearance_points: float | None
    expected_goals: float | None
    expected_assists: float | None
    goal_points: float | None
    assist_points: float | None
    fixture_points: float | None
    prediction_complete: bool
    availability_gate_reason: str | None
    minute_pmf: tuple[float, ...] | None
    appearance_probability: float | None
    start_probability: None = None

    def __post_init__(self) -> None:
        if self.minute_pmf is not None:
            if len(self.minute_pmf) != 91:
                raise ResearchContractError("WRONG_MINUTE_SUPPORT", "U0 fixture minute support must be 0-90")
            _validate_pmf(self.minute_pmf, "U0 fixture minute PMF")
        if self.appearance_probability is not None:
            _validate_probability(self.appearance_probability, "U0 appearance probability")


@dataclass(frozen=True)
class PoissonCountPrediction:
    status: str
    mean: float | None
    central_80_interval: tuple[int, int] | None

    def __post_init__(self) -> None:
        if self.status == "COMPLETE":
            if self.mean is None or self.central_80_interval is None:
                raise ResearchContractError("INCOMPLETE_COUNT_CONTRACT", "complete Poisson output lacks values")
            _require_nonnegative(self.mean, "Poisson mean")
            low, high = self.central_80_interval
            _require_integer(low, "Poisson lower quantile", minimum=0)
            _require_integer(high, "Poisson upper quantile", minimum=0)
            if low > high:
                raise ResearchContractError("INVALID_COUNT_INTERVAL", "count interval is reversed")
        elif self.status == "MISSING_MEAN":
            if self.mean is not None or self.central_80_interval is not None:
                raise ResearchContractError("INCONSISTENT_MISSING_STATE", "missing Poisson mean has values")
        else:
            raise ResearchContractError("INVALID_COUNT_STATUS", "unknown Poisson status")

    def probability(self, count: int) -> float | None:
        _require_integer(count, "count", minimum=0)
        if self.status != "COMPLETE" or self.mean is None:
            return None
        if self.mean == 0:
            return 1.0 if count == 0 else 0.0
        return math.exp(-self.mean + count * math.log(self.mean) - math.lgamma(count + 1))

    def cdf(self, count: int) -> float | None:
        _require_integer(count, "count", minimum=0)
        if self.status != "COMPLETE":
            return None
        return min(1.0, math.fsum(self.probability(value) or 0.0 for value in range(count + 1)))


@dataclass(frozen=True)
class U0GameweekPrediction:
    candidate_identity: str
    fixture_predictions: tuple[U0FixturePrediction, ...]
    fixture_count: int
    gameweek_expected_minutes: float
    expected_minutes_for_evaluation: float | None
    gameweek_appearance_points: float
    appearance_points_for_evaluation: float | None
    expected_goals: float
    expected_goals_for_evaluation: float | None
    expected_assists: float
    expected_assists_for_evaluation: float | None
    modeled_points: float | None
    prediction_complete: bool
    minute_pmf: tuple[float, ...] | None
    band_probabilities: tuple[float, ...] | None
    appearance_probability: float | None
    goal_count_prediction: PoissonCountPrediction
    assist_count_prediction: PoissonCountPrediction
    start_probability: None = None

    def __post_init__(self) -> None:
        if self.candidate_identity != U0_IDENTITY:
            raise ResearchContractError("WRONG_CANDIDATE_IDENTITY", "U0 identity changed")
        if self.fixture_count != len(self.fixture_predictions):
            raise ResearchContractError("FIXTURE_COUNT_MISMATCH", "U0 fixture count differs")
        if self.minute_pmf is not None:
            if len(self.minute_pmf) != 90 * self.fixture_count + 1:
                raise ResearchContractError("WRONG_MINUTE_SUPPORT", "U0 GW minute support differs")
            _validate_pmf(self.minute_pmf, "U0 GW minute PMF")
        if self.band_probabilities is not None and self.minute_pmf is not None:
            for left, right in zip(self.band_probabilities, _minute_bands(self.minute_pmf)):
                _require_close(left, right, "U0 minute bands")


@dataclass(frozen=True)
class UM1FixturePrediction:
    fixture_id: int
    minute_pmf: tuple[float, ...]
    no_start_pmf: tuple[float, ...]
    start_pmf: tuple[float, ...]
    band_probabilities: tuple[float, ...]
    expected_minutes: float
    appearance_probability: float
    start_probability: float
    expected_appearance_points: float

    def __post_init__(self) -> None:
        for name, values in (
            ("minute_pmf", self.minute_pmf),
            ("no_start_pmf", self.no_start_pmf),
            ("start_pmf", self.start_pmf),
        ):
            if len(values) != 91:
                raise ResearchContractError("WRONG_MINUTE_SUPPORT", f"{name} support must be 0-90")
        marginal = _validate_pmf(self.minute_pmf, "UM1 fixture minute PMF")
        for index, (no_start, start, total) in enumerate(
            zip(self.no_start_pmf, self.start_pmf, marginal)
        ):
            _validate_probability(no_start, "UM1 no-start mass")
            _validate_probability(start, "UM1 start mass")
            _require_close(no_start + start, total, f"UM1 joint marginal minute {index}")
        for left, right in zip(self.band_probabilities, _minute_bands(marginal)):
            _require_close(left, right, "UM1 fixture minute bands")
        _require_close(
            self.expected_minutes,
            math.fsum(index * mass for index, mass in enumerate(marginal)),
            "UM1 fixture expected minutes",
        )
        _require_close(self.appearance_probability, 1.0 - marginal[0], "UM1 appearance probability")
        _require_close(self.start_probability, math.fsum(self.start_pmf), "UM1 start probability")
        _require_close(
            self.expected_appearance_points,
            expected_appearance_points(marginal),
            "UM1 fixture appearance points",
        )


@dataclass(frozen=True)
class UM1Prediction:
    candidate_identity: str
    history_state: str
    history_observations: int
    overflow_count: int
    availability_multiplier: float
    flags: tuple[str, ...]
    fixture_predictions: tuple[UM1FixturePrediction, ...]
    fixture_count: int
    gameweek_minute_pmf: tuple[float, ...]
    band_probabilities: tuple[float, ...]
    expected_minutes: float
    appearance_probability: float
    start_probability: float
    expected_appearance_points: float

    def __post_init__(self) -> None:
        if self.candidate_identity != UM1_IDENTITY:
            raise ResearchContractError("WRONG_CANDIDATE_IDENTITY", "UM1 identity changed")
        if self.history_state not in {"PRIOR_ONLY", "HISTORY_UPDATED"}:
            raise ResearchContractError("INVALID_HISTORY_STATE", "unknown UM1 history state")
        _require_integer(self.history_observations, "history_observations", minimum=0)
        _require_integer(self.overflow_count, "overflow_count", minimum=0)
        _validate_probability(self.availability_multiplier, "availability multiplier")
        if len(set(self.flags)) != len(self.flags):
            raise ResearchContractError("DUPLICATE_FLAG", "UM1 flags are duplicated")
        if self.fixture_count != len(self.fixture_predictions):
            raise ResearchContractError("FIXTURE_COUNT_MISMATCH", "UM1 fixture count differs")
        fixture_ids = tuple(row.fixture_id for row in self.fixture_predictions)
        if tuple(sorted(fixture_ids)) != fixture_ids or len(set(fixture_ids)) != len(fixture_ids):
            raise ResearchContractError("NONCANONICAL_FIXTURE_ORDER", "UM1 fixture predictions are not canonical")
        if len(self.gameweek_minute_pmf) != 90 * self.fixture_count + 1:
            raise ResearchContractError("WRONG_MINUTE_SUPPORT", "UM1 GW minute support differs")
        gameweek_pmf = _validate_pmf(self.gameweek_minute_pmf, "UM1 GW minute PMF")
        for left, right in zip(self.band_probabilities, _minute_bands(gameweek_pmf)):
            _require_close(left, right, "UM1 GW minute bands")
        _require_close(
            self.expected_minutes,
            math.fsum(index * mass for index, mass in enumerate(gameweek_pmf)),
            "UM1 GW expected minutes",
        )
        expected_appearance = math.fsum(
            row.expected_appearance_points for row in self.fixture_predictions
        )
        _require_close(self.expected_appearance_points, expected_appearance, "UM1 GW appearance points")
        appearance = 0.0 if not self.fixture_predictions else 1.0 - math.prod(
            row.minute_pmf[0] for row in self.fixture_predictions
        )
        start = 0.0 if not self.fixture_predictions else 1.0 - math.prod(
            1.0 - row.start_probability for row in self.fixture_predictions
        )
        _require_close(self.appearance_probability, appearance, "UM1 GW appearance probability")
        _require_close(self.start_probability, start, "UM1 GW start probability")


@dataclass(frozen=True)
class PopulationPrior:
    event: str
    contributing_peers: int
    population_exposure: float
    population_events: float
    population_missing_exposure: float
    unclipped_mean: float | None
    prior_mean: float
    used_fixed_fallback: bool
    mean_clipped: bool

    def __post_init__(self) -> None:
        if self.event not in {"xg", "xa"}:
            raise ResearchContractError("INVALID_EVENT", "population event must be xg or xa")
        _require_integer(self.contributing_peers, "contributing_peers", minimum=0)
        for field in (
            "population_exposure",
            "population_events",
            "population_missing_exposure",
            "prior_mean",
        ):
            _require_nonnegative(getattr(self, field), field)
        if self.unclipped_mean is not None:
            _require_nonnegative(self.unclipped_mean, "unclipped_mean")
        if not 0.01 <= self.prior_mean <= 2.0:
            raise ResearchContractError("INVALID_PRIOR_MEAN", "prior mean is outside frozen caps")


@dataclass(frozen=True)
class EventRatePosterior:
    event: str
    status: str
    history_state: str
    playing_exposure: float
    observed_exposure: float
    observed_events: float
    missing_exposure: float
    partial_missingness: bool
    population_prior: PopulationPrior
    component_weights: tuple[float, ...] | None
    prior_minutes: tuple[float, ...]
    shapes: tuple[float, ...] | None
    rates: tuple[float, ...] | None
    rate_mean: float | None
    rate_variance: float | None
    rate_quantiles: tuple[float, float, float] | None
    weighted_prior_minutes_summary: float | None

    def __post_init__(self) -> None:
        if self.event not in {"xg", "xa"} or self.population_prior.event != self.event:
            raise ResearchContractError("INVALID_EVENT", "event posterior identity differs")
        for field in (
            "playing_exposure",
            "observed_exposure",
            "observed_events",
            "missing_exposure",
        ):
            _require_nonnegative(getattr(self, field), field)
        if abs(self.playing_exposure - self.observed_exposure - self.missing_exposure) > MOMENT_TOLERANCE:
            raise ResearchContractError("INCONSISTENT_EXPOSURE", "playing exposure is not observed plus missing")
        if self.prior_minutes != (90.0, 900.0):
            raise ResearchContractError("WRONG_PRIOR_STRENGTH", "UA1 prior minutes changed")
        optional = (
            self.component_weights,
            self.shapes,
            self.rates,
            self.rate_mean,
            self.rate_variance,
            self.rate_quantiles,
            self.weighted_prior_minutes_summary,
        )
        if self.status == "MISSING_EVENT_HISTORY":
            if any(value is not None for value in optional) or self.partial_missingness:
                raise ResearchContractError("INCONSISTENT_MISSING_STATE", "missing event posterior has values")
            return
        if self.status != "COMPLETE" or any(value is None for value in optional):
            raise ResearchContractError("INCOMPLETE_POSTERIOR", "complete event posterior lacks values")
        assert self.component_weights is not None and self.shapes is not None and self.rates is not None
        if not (len(self.component_weights) == len(self.shapes) == len(self.rates) == 2):
            raise ResearchContractError("WRONG_MIXTURE_SIZE", "UA1 mixture must have two components")
        weights = _validate_pmf(self.component_weights, "UA1 mixture weights")
        if any(value <= 0 or not math.isfinite(value) for value in (*self.shapes, *self.rates)):
            raise ResearchContractError("INVALID_GAMMA_PARAMETER", "Gamma shape/rate must be finite and positive")
        assert self.rate_mean is not None and self.rate_variance is not None
        mean = math.fsum(weight * shape / rate for weight, shape, rate in zip(weights, self.shapes, self.rates))
        variance = math.fsum(
            weight * (shape / rate ** 2 + (shape / rate) ** 2)
            for weight, shape, rate in zip(weights, self.shapes, self.rates)
        ) - mean ** 2
        _require_close(self.rate_mean, mean, "UA1 rate mean")
        _require_close(self.rate_variance, max(0.0, variance), "UA1 rate variance")
        assert self.rate_quantiles is not None and self.weighted_prior_minutes_summary is not None
        if len(self.rate_quantiles) != 3 or tuple(sorted(self.rate_quantiles)) != self.rate_quantiles:
            raise ResearchContractError("INVALID_RATE_QUANTILES", "rate quantiles are malformed")
        for value in self.rate_quantiles:
            _require_nonnegative(value, "rate quantile")
        summary = math.fsum(weight * minutes for weight, minutes in zip(weights, self.prior_minutes))
        _require_close(self.weighted_prior_minutes_summary, summary, "weighted prior minutes")


@dataclass(frozen=True)
class UA1Prediction:
    candidate_identity: str
    xg: EventRatePosterior
    xa: EventRatePosterior

    def __post_init__(self) -> None:
        if self.candidate_identity != UA1_IDENTITY:
            raise ResearchContractError("WRONG_CANDIDATE_IDENTITY", "UA1 identity changed")
        if self.xg.event != "xg" or self.xa.event != "xa":
            raise ResearchContractError("INVALID_EVENT", "UA1 event outputs are not separate xg/xa")


@dataclass(frozen=True)
class CountPrediction:
    status: str
    mean: float | None
    variance: float | None
    central_80_interval: tuple[int, int] | None
    exposure: float | None
    mixture_weights: tuple[float, ...] | None
    shapes: tuple[float, ...] | None
    rates: tuple[float, ...] | None
    integrated_minute_pmf: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if self.status != "COMPLETE":
            if any(value is not None for value in (
                self.mean,
                self.variance,
                self.central_80_interval,
                self.mixture_weights,
                self.shapes,
                self.rates,
                self.integrated_minute_pmf,
            )):
                raise ResearchContractError("INCONSISTENT_MISSING_STATE", "missing count output has moments")
            if self.exposure is not None:
                _require_nonnegative(self.exposure, "missing count exposure")
            return
        if any(value is None for value in (
            self.mean,
            self.variance,
            self.central_80_interval,
            self.mixture_weights,
            self.shapes,
            self.rates,
        )):
            raise ResearchContractError("INCOMPLETE_COUNT_CONTRACT", "complete count output lacks values")
        assert self.mean is not None and self.variance is not None
        _require_nonnegative(self.mean, "count mean")
        _require_nonnegative(self.variance, "count variance")
        assert self.central_80_interval is not None
        low, high = self.central_80_interval
        _require_integer(low, "count lower quantile", minimum=0)
        _require_integer(high, "count upper quantile", minimum=0)
        if low > high:
            raise ResearchContractError("INVALID_COUNT_INTERVAL", "count interval is reversed")
        assert self.mixture_weights is not None and self.shapes is not None and self.rates is not None
        if not (len(self.mixture_weights) == len(self.shapes) == len(self.rates)):
            raise ResearchContractError("WRONG_MIXTURE_SIZE", "count mixture parameter lengths differ")
        _validate_pmf(self.mixture_weights, "count mixture weights")
        if any(value <= 0 or not math.isfinite(value) for value in (*self.shapes, *self.rates)):
            raise ResearchContractError("INVALID_GAMMA_PARAMETER", "count Gamma parameters are invalid")
        if self.exposure is not None:
            _require_nonnegative(self.exposure, "count exposure")
        if self.integrated_minute_pmf is not None:
            _validate_pmf(self.integrated_minute_pmf, "count integrated minute PMF")

    def probability(self, count: int) -> float | None:
        _require_integer(count, "count", minimum=0)
        if self.status != "COMPLETE":
            return None
        if self.integrated_minute_pmf is not None:
            return math.fsum(
                probability
                * _negative_binomial_mixture_pmf(
                    count,
                    minutes / 90.0,
                    self.mixture_weights or (),
                    self.shapes or (),
                    self.rates or (),
                )
                for minutes, probability in enumerate(self.integrated_minute_pmf)
            )
        assert self.exposure is not None
        return _negative_binomial_mixture_pmf(
            count,
            self.exposure,
            self.mixture_weights or (),
            self.shapes or (),
            self.rates or (),
        )

    def cdf(self, count: int) -> float | None:
        _require_integer(count, "count", minimum=0)
        if self.status != "COMPLETE":
            return None
        return min(1.0, math.fsum(self.probability(value) or 0.0 for value in range(count + 1)))


@dataclass(frozen=True)
class ModeledComponents:
    candidate_identity: str
    fixture_count: int
    expected_minutes: float | None
    expected_appearance_points: float | None
    expected_goals: float | None
    goal_points: float | None
    expected_assists: float | None
    assist_points: float | None
    modeled_points: float | None
    prediction_complete: bool
    goal_count_prediction: CountPrediction | None
    assist_count_prediction: CountPrediction | None

    def __post_init__(self) -> None:
        if self.candidate_identity not in {UM1_IDENTITY, UA1_IDENTITY, COMBINED_IDENTITY}:
            raise ResearchContractError("WRONG_CANDIDATE_IDENTITY", "modeled component identity is not registered")
        _require_integer(self.fixture_count, "fixture_count", minimum=0)
        for field in (
            "expected_minutes",
            "expected_appearance_points",
            "expected_goals",
            "goal_points",
            "expected_assists",
            "assist_points",
            "modeled_points",
        ):
            value = getattr(self, field)
            if value is not None:
                _require_nonnegative(value, field)
        if self.prediction_complete and any(
            value is None
            for value in (
                self.expected_minutes,
                self.expected_appearance_points,
                self.expected_goals,
                self.goal_points,
                self.expected_assists,
                self.assist_points,
                self.modeled_points,
            )
        ):
            raise ResearchContractError("INCONSISTENT_COMPLETENESS", "complete modeled output lacks components")
        if self.goal_count_prediction is not None and self.goal_count_prediction.mean is not None:
            if self.expected_goals is None:
                raise ResearchContractError("INCONSISTENT_MOMENT", "goal count mean lacks expected goals")
            _require_close(self.expected_goals, self.goal_count_prediction.mean, "expected goals/count mean")
        if self.assist_count_prediction is not None and self.assist_count_prediction.mean is not None:
            if self.expected_assists is None:
                raise ResearchContractError("INCONSISTENT_MOMENT", "assist count mean lacks expected assists")
            _require_close(self.expected_assists, self.assist_count_prediction.mean, "expected assists/count mean")
        if self.modeled_points is not None and self.expected_appearance_points is not None:
            if self.candidate_identity == COMBINED_IDENTITY:
                if self.goal_points is None or self.assist_points is None:
                    raise ResearchContractError("INCONSISTENT_MOMENT", "combined total hides a missing component")
                expected_total = self.expected_appearance_points + self.goal_points + self.assist_points
            else:
                expected_total = self.expected_appearance_points + (self.goal_points or 0.0) + (self.assist_points or 0.0)
            _require_close(self.modeled_points, expected_total, "modeled points")


@dataclass(frozen=True)
class ArtifactHeader:
    contract_version: str
    protocol_identity: str
    candidate_identities: tuple[str, ...]
    stage: str
    source_manifest_identity: str
    source_manifest_sha256: str
    cutoff_view_identity: str
    cutoff_view_sha256: str
    outcome_joined: bool
    canonical_order: str
    publication_policy: str
    confirmation_state: str
    hash_claim_scope: str

    def __post_init__(self) -> None:
        if self.contract_version != ARTIFACT_CONTRACT_VERSION:
            raise ResearchContractError("WRONG_CONTRACT_VERSION", "artifact contract version changed")
        if self.protocol_identity != PROTOCOL_IDENTITY:
            raise ResearchContractError("WRONG_PROTOCOL_IDENTITY", "protocol identity changed")
        allowed = {U0_IDENTITY, UM1_IDENTITY, UA1_IDENTITY, COMBINED_IDENTITY}
        if not self.candidate_identities or any(value not in allowed for value in self.candidate_identities):
            raise ResearchContractError("WRONG_CANDIDATE_IDENTITY", "candidate identity is not registered")
        if len(set(self.candidate_identities)) != len(self.candidate_identities):
            raise ResearchContractError("DUPLICATE_CANDIDATE", "candidate identities must be unique")
        registered_order = (U0_IDENTITY, UM1_IDENTITY, UA1_IDENTITY, COMBINED_IDENTITY)
        if tuple(sorted(self.candidate_identities, key=registered_order.index)) != self.candidate_identities:
            raise ResearchContractError("NONCANONICAL_CANDIDATE_ORDER", "candidate identities are not canonical")
        fixed = {
            "stage": "PREDICTION_ONLY",
            "canonical_order": "season,target_gameweek,element_id,fixture_id",
            "publication_policy": "EXCLUSIVE_NO_OVERWRITE",
            "confirmation_state": "UNOPENED_UNAUTHORIZED",
            "hash_claim_scope": "BYTE_IDENTITY_ONLY_NOT_SEMANTIC_OR_HISTORICAL_PROOF",
        }
        for field, expected in fixed.items():
            if getattr(self, field) != expected:
                raise ResearchContractError("INVALID_ARTIFACT_BOUNDARY", f"{field} must be {expected}")
        if self.outcome_joined is not False:
            raise ResearchContractError("OUTCOME_LEAKAGE", "prediction header cannot join outcomes")
        for field in (
            "source_manifest_identity",
            "source_manifest_sha256",
            "cutoff_view_identity",
            "cutoff_view_sha256",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value:
                raise ResearchContractError("MISSING_SOURCE_PLACEHOLDER", f"{field} is required")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ArtifactHeader:
        expected = set(cls.__dataclass_fields__)
        _strict_fields(value, expected, "ArtifactHeader")
        converted = dict(value)
        identities = converted.get("candidate_identities")
        if not isinstance(identities, list):
            raise ResearchContractError("INVALID_CANDIDATE_LIST", "candidate_identities must be a list")
        converted["candidate_identities"] = tuple(identities)
        return cls(**converted)


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a public research contract deterministically and finitely."""
    materialized = asdict(value) if hasattr(value, "__dataclass_fields__") else value

    def check(item: Any) -> None:
        if isinstance(item, float) and not math.isfinite(item):
            raise ResearchContractError("NONFINITE_SERIALIZATION", "canonical JSON rejects non-finite values")
        if isinstance(item, Mapping):
            if any(not isinstance(key, str) for key in item):
                raise ResearchContractError("NONSTRING_JSON_KEY", "canonical JSON keys must be strings")
            for nested in item.values():
                check(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                check(nested)

    check(materialized)
    return json.dumps(
        materialized,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8") + b"\n"


def _minute_bands(pmf: Sequence[float]) -> tuple[float, ...]:
    values = (
        pmf[0],
        math.fsum(pmf[1:30]),
        math.fsum(pmf[30:60]),
        math.fsum(pmf[60:90]),
        math.fsum(pmf[90:]),
    )
    return _validate_pmf(values, "minute bands")


def expected_appearance_points(minute_pmf: Sequence[float]) -> float:
    """Return E[1[M>=1]+1[M>=60]] for one fixture."""
    checked = _validate_pmf(minute_pmf, "fixture minute PMF")
    if len(checked) != 91:
        raise ResearchContractError("WRONG_MINUTE_SUPPORT", "fixture minute PMF support must be 0-90")
    return math.fsum(checked[1:]) + math.fsum(checked[60:])


def _convolve(left: Sequence[float], right: Sequence[float]) -> tuple[float, ...]:
    result = [0.0] * (len(left) + len(right) - 1)
    for left_index, left_mass in enumerate(left):
        if left_mass == 0:
            continue
        for right_index, right_mass in enumerate(right):
            if right_mass:
                result[left_index + right_index] += left_mass * right_mass
    return _validate_pmf(result, "convolved minute PMF")


def _u0_fixture(value: U0FixtureInput) -> U0FixturePrediction:
    minutes: float | None
    gate_reason: str | None = None
    if value.previous_gameweek_minutes is None:
        minutes = None
    elif value.availability.known_pre_deadline and value.availability.is_target_next_round \
            and value.availability.chance_of_playing_next_round == 0:
        minutes = 0.0
        gate_reason = "explicit_zero_chance"
    elif value.availability.known_pre_deadline and value.availability.status is not None \
            and value.availability.status.lower() in {"s", "u"}:
        minutes = 0.0
        gate_reason = "suspended" if value.availability.status.lower() == "s" else "unavailable"
    else:
        minutes = min(90.0, max(0.0, float(value.previous_gameweek_minutes)))
    appearance = None if minutes is None else 0.0 if minutes == 0 else 1.0 if minutes < 60 else 2.0
    expected_goals = None if minutes is None or value.raw_xg_per90 is None else value.raw_xg_per90 * minutes / 90.0
    expected_assists = None if minutes is None or value.raw_xa_per90 is None else value.raw_xa_per90 * minutes / 90.0
    goal_points = None if expected_goals is None else expected_goals * GOAL_POINTS[value.position]
    assist_points = None if expected_assists is None else expected_assists * 3.0
    fixture_points = None if appearance is None else appearance + (goal_points or 0.0) + (assist_points or 0.0)
    complete = appearance is not None and goal_points is not None and assist_points is not None
    if minutes is None:
        pmf = None
        appearance_probability = None
    else:
        point = int(minutes)
        pmf_values = [0.0] * 91
        pmf_values[point] = 1.0
        pmf = tuple(pmf_values)
        appearance_probability = 1.0 if minutes > 0 else 0.0
    return U0FixturePrediction(
        fixture_id=value.fixture_id,
        expected_minutes=minutes,
        appearance_points=appearance,
        expected_goals=expected_goals,
        expected_assists=expected_assists,
        goal_points=goal_points,
        assist_points=assist_points,
        fixture_points=fixture_points,
        prediction_complete=complete,
        availability_gate_reason=gate_reason,
        minute_pmf=pmf,
        appearance_probability=appearance_probability,
    )


def _poisson_prediction(mean: float | None) -> PoissonCountPrediction:
    if mean is None:
        return PoissonCountPrediction("MISSING_MEAN", None, None)
    checked = _require_nonnegative(mean, "Poisson mean")

    def probability_at(count: int) -> float:
        if checked == 0:
            return 1.0 if count == 0 else 0.0
        return math.exp(-checked + count * math.log(checked) - math.lgamma(count + 1))

    return PoissonCountPrediction(
        "COMPLETE",
        checked,
        (
            _count_quantile_from_probability(0.1, probability_at),
            _count_quantile_from_probability(0.9, probability_at),
        ),
    )


def build_u0(fixtures: Sequence[U0FixtureInput]) -> U0GameweekPrediction:
    """Apply exact historical U0 mechanics and the registered score adapters."""
    if len({fixture.fixture_id for fixture in fixtures}) != len(fixtures):
        raise ResearchInputError("DUPLICATE_TARGET_FIXTURE", "U0 target fixture is duplicated")
    ordered = tuple(_u0_fixture(value) for value in sorted(fixtures, key=lambda item: item.fixture_id))
    if not ordered:
        return U0GameweekPrediction(
            candidate_identity=U0_IDENTITY,
            fixture_predictions=(),
            fixture_count=0,
            gameweek_expected_minutes=0.0,
            expected_minutes_for_evaluation=0.0,
            gameweek_appearance_points=0.0,
            appearance_points_for_evaluation=0.0,
            expected_goals=0.0,
            expected_goals_for_evaluation=0.0,
            expected_assists=0.0,
            expected_assists_for_evaluation=0.0,
            modeled_points=0.0,
            prediction_complete=True,
            minute_pmf=(1.0,),
            band_probabilities=(1.0, 0.0, 0.0, 0.0, 0.0),
            appearance_probability=0.0,
            goal_count_prediction=_poisson_prediction(0.0),
            assist_count_prediction=_poisson_prediction(0.0),
        )

    def numeric_sum(field: str) -> float:
        return math.fsum(float(getattr(row, field)) for row in ordered if getattr(row, field) is not None)

    def complete_sum(field: str) -> float | None:
        values = [getattr(row, field) for row in ordered]
        return math.fsum(float(value) for value in values) if all(value is not None for value in values) else None

    fixture_totals = [row.fixture_points for row in ordered]
    minute_pmfs = [row.minute_pmf for row in ordered]
    if all(value is not None for value in minute_pmfs):
        pmf: tuple[float, ...] = (1.0,)
        for fixture_pmf in minute_pmfs:
            assert fixture_pmf is not None
            pmf = _convolve(pmf, fixture_pmf)
        bands = _minute_bands(pmf)
        appearance_probability = 1.0 - math.prod(row.minute_pmf[0] for row in ordered if row.minute_pmf)
    else:
        pmf = None
        bands = None
        appearance_probability = None
    goal_mean = complete_sum("expected_goals")
    assist_mean = complete_sum("expected_assists")
    return U0GameweekPrediction(
        candidate_identity=U0_IDENTITY,
        fixture_predictions=ordered,
        fixture_count=len(ordered),
        gameweek_expected_minutes=numeric_sum("expected_minutes"),
        expected_minutes_for_evaluation=complete_sum("expected_minutes"),
        gameweek_appearance_points=numeric_sum("appearance_points"),
        appearance_points_for_evaluation=complete_sum("appearance_points"),
        expected_goals=numeric_sum("expected_goals"),
        expected_goals_for_evaluation=goal_mean,
        expected_assists=numeric_sum("expected_assists"),
        expected_assists_for_evaluation=assist_mean,
        modeled_points=math.fsum(float(value) for value in fixture_totals) if all(value is not None for value in fixture_totals) else None,
        prediction_complete=all(row.prediction_complete for row in ordered),
        minute_pmf=pmf,
        band_probabilities=bands,
        appearance_probability=appearance_probability,
        goal_count_prediction=_poisson_prediction(goal_mean),
        assist_count_prediction=_poisson_prediction(assist_mean),
    )


def assert_u0_parity(
    prediction: U0GameweekPrediction,
    expected: Mapping[str, Any],
    *,
    tolerance: float = 1e-10,
) -> None:
    """Fail when a tracked production/historical adapter comparison differs."""
    allowed = {
        "fixture_count",
        "gameweek_expected_minutes",
        "gameweek_appearance_points",
        "expected_goals",
        "expected_assists",
        "modeled_points",
        "prediction_complete",
    }
    _strict_fields(expected, allowed, "U0 parity fields")
    for field in sorted(allowed):
        left = getattr(prediction, field)
        right = expected[field]
        if isinstance(left, float) and isinstance(right, (int, float)) and not isinstance(right, bool):
            if not math.isfinite(float(right)) or abs(left - float(right)) > tolerance:
                raise ResearchInputError("U0_PARITY_FAILURE", f"U0 parity failed for {field}")
        elif left != right:
            raise ResearchInputError("U0_PARITY_FAILURE", f"U0 parity failed for {field}")


def _eligible_history(
    target: TargetContext,
    history: Sequence[HistoryFixture],
    *,
    window: int,
) -> tuple[HistoryFixture, ...]:
    keys: set[tuple[int, int]] = set()
    eligible: list[HistoryFixture] = []
    for row in history:
        if row.season != target.season or row.element_id != target.element_id:
            raise ResearchInputError("HISTORY_IDENTITY_MISMATCH", "history target identity differs")
        key = (row.gameweek, row.fixture_id)
        if key in keys:
            raise ResearchInputError("DUPLICATE_HISTORY_FIXTURE", "history fixture is duplicated")
        keys.add(key)
        if row.gameweek >= target.target_gameweek:
            raise ResearchInputError("CAUSAL_CUTOFF_VIOLATION", "target/future history reached predictor")
        if not row.source_universe_member or not row.kickoff_before_cutoff:
            continue
        if row.gameweek >= max(1, target.target_gameweek - window):
            eligible.append(row)
    return tuple(sorted(eligible, key=lambda row: (row.gameweek, row.fixture_id)))


def _um1_prior() -> tuple[tuple[float, ...], tuple[float, ...]]:
    no_start = [0.0] * 91
    start = [0.0] * 91
    no_start[0] = 0.50
    for minute in range(1, 30):
        no_start[minute] = 0.15 * 0.70 / 29.0
        start[minute] = 0.35 * 0.04 / 29.0
    for minute in range(30, 60):
        no_start[minute] = 0.15 * 0.20 / 30.0
        start[minute] = 0.35 * 0.10 / 30.0
    for minute in range(60, 90):
        no_start[minute] = 0.15 * 0.09 / 30.0
        start[minute] = 0.35 * 0.45 / 30.0
    no_start[90] = 0.15 * 0.01
    start[0] = 0.35 * 0.01
    start[90] = 0.35 * 0.40
    _validate_pmf(tuple(a + b for a, b in zip(no_start, start)), "UM1 prior")
    return tuple(no_start), tuple(start)


def build_um1(target: TargetContext, history: Sequence[HistoryFixture]) -> UM1Prediction:
    """Build the literal UM1 joint start/minute PMF and GW convolution."""
    rows = _eligible_history(target, history, window=8)
    no_start_prior, start_prior = _um1_prior()
    no_start_counts = [0.0] * 91
    start_counts = [0.0] * 91
    total_weight = 0.0
    overflow_count = 0
    for row in rows:
        if row.minutes is None or row.starts is None:
            raise ResearchInputError(
                "UM1_MISSING_START_OR_MINUTES",
                "eligible UM1 history requires starts and minutes",
            )
        bounded_minutes = min(row.minutes, 90)
        overflow_count += int(row.minutes > 90)
        weight = 0.8 ** (target.target_gameweek - 1 - row.gameweek)
        total_weight += weight
        if row.starts == 1:
            start_counts[bounded_minutes] += weight
        elif bounded_minutes == 0:
            no_start_counts[0] += weight
        else:
            no_start_counts[bounded_minutes] += weight
    denominator = 4.0 + total_weight
    no_start = tuple(
        (4.0 * prior + observed) / denominator
        for prior, observed in zip(no_start_prior, no_start_counts)
    )
    start = tuple(
        (4.0 * prior + observed) / denominator
        for prior, observed in zip(start_prior, start_counts)
    )
    multiplier, availability_flags = target.availability.multiplier_and_flags()
    thinned_no_start = [multiplier * value for value in no_start]
    thinned_start = [multiplier * value for value in start]
    thinned_no_start[0] += 1.0 - multiplier
    marginal = _validate_pmf(
        tuple(a + b for a, b in zip(thinned_no_start, thinned_start)),
        "UM1 marginal PMF",
    )
    expected_minutes = math.fsum(minute * probability for minute, probability in enumerate(marginal))
    appearance_probability = 1.0 - marginal[0]
    start_probability = math.fsum(thinned_start)
    appearance_points = expected_appearance_points(marginal)
    bands = _minute_bands(marginal)
    per_fixture = tuple(
        UM1FixturePrediction(
            fixture_id=fixture_id,
            minute_pmf=marginal,
            no_start_pmf=tuple(thinned_no_start),
            start_pmf=tuple(thinned_start),
            band_probabilities=bands,
            expected_minutes=expected_minutes,
            appearance_probability=appearance_probability,
            start_probability=start_probability,
            expected_appearance_points=appearance_points,
        )
        for fixture_id in target.fixture_ids
    )
    gameweek_pmf: tuple[float, ...] = (1.0,)
    for _ in per_fixture:
        gameweek_pmf = _convolve(gameweek_pmf, marginal)
    fixture_count = len(per_fixture)
    gameweek_appearance_probability = (
        0.0 if not per_fixture else 1.0 - marginal[0] ** fixture_count
    )
    gameweek_start_probability = (
        0.0 if not per_fixture else 1.0 - (1.0 - start_probability) ** fixture_count
    )
    flags = list(availability_flags)
    if overflow_count:
        flags.append("MINUTES_OVERFLOW_CAPPED_AT_90")
    return UM1Prediction(
        candidate_identity=UM1_IDENTITY,
        history_state="PRIOR_ONLY" if not rows else "HISTORY_UPDATED",
        history_observations=len(rows),
        overflow_count=overflow_count,
        availability_multiplier=multiplier,
        flags=tuple(flags),
        fixture_predictions=per_fixture,
        fixture_count=fixture_count,
        gameweek_minute_pmf=gameweek_pmf,
        band_probabilities=_minute_bands(gameweek_pmf),
        expected_minutes=fixture_count * expected_minutes,
        appearance_probability=gameweek_appearance_probability,
        start_probability=gameweek_start_probability,
        expected_appearance_points=fixture_count * appearance_points,
    )


def _event_summary(
    target: TargetContext,
    history: Sequence[HistoryFixture],
    event: str,
) -> tuple[float, float, float, float]:
    rows = _eligible_history(target, history, window=12)
    playing_exposure = observed_exposure = observed_events = missing_exposure = 0.0
    for row in rows:
        if row.minutes is None:
            raise ResearchInputError("UA1_MISSING_MINUTES", "eligible UA1 history requires minutes")
        weight = 2.0 ** (-(target.target_gameweek - 1 - row.gameweek) / 6.0)
        if row.minutes <= 0:
            continue
        exposure = weight * row.minutes / 90.0
        playing_exposure += exposure
        value = getattr(row, event)
        if value is None:
            missing_exposure += exposure
        else:
            observed_exposure += exposure
            observed_events += weight * value
    return playing_exposure, observed_exposure, observed_events, missing_exposure


def _peer_target(target: TargetContext, element_id: int) -> TargetContext:
    return TargetContext(
        season=target.season,
        target_gameweek=target.target_gameweek,
        element_id=element_id,
        position=target.position,
        fixture_ids=(),
        availability=target.availability,
    )


def _population_prior(
    target: TargetContext,
    peer_histories: Sequence[PeerHistory],
    event: str,
) -> PopulationPrior:
    exposure_total = event_total = missing_total = 0.0
    contributors = 0
    seen: set[int] = set()
    for peer in sorted(peer_histories, key=lambda value: value.element_id):
        if not isinstance(peer, PeerHistory):
            raise ResearchContractError("INVALID_PEER_HISTORY", "peer histories must use PeerHistory")
        if peer.element_id in seen:
            raise ResearchInputError("DUPLICATE_PEER", "peer appears more than once")
        seen.add(peer.element_id)
        if peer.element_id == target.element_id:
            raise ResearchInputError("TARGET_INCLUDED_IN_PRIOR", "target player must be excluded from peer prior")
        if peer.target_position != target.position:
            continue
        peer_target = _peer_target(target, peer.element_id)
        validated = _eligible_history(peer_target, peer.history, window=12)
        rows = tuple(row for row in validated if row.position == target.position)
        _, exposure, events, missing = _event_summary(peer_target, rows, event)
        missing_total += missing
        if exposure <= 0:
            continue
        contribution = min(1.0, 10.0 / exposure)
        exposure_total += contribution * exposure
        event_total += contribution * events
        contributors += 1
    fallback = contributors < 20 or exposure_total < 100.0
    raw_mean = event_total / exposure_total if exposure_total > 0 else None
    if fallback:
        return PopulationPrior(
            event, contributors, exposure_total, event_total, missing_total, raw_mean,
            FALLBACK_RATES[target.position][event], True, False,
        )
    assert raw_mean is not None
    prior_mean = min(2.0, max(0.01, raw_mean))
    return PopulationPrior(
        event, contributors, exposure_total, event_total, missing_total, raw_mean, prior_mean,
        False, prior_mean != raw_mean,
    )


def _regularized_gamma_p(shape: float, value: float) -> float:
    """Regularized lower incomplete gamma using series/continued fraction."""
    if shape <= 0 or value < 0 or not math.isfinite(shape) or not math.isfinite(value):
        raise ResearchContractError("INVALID_GAMMA_ARGUMENT", "gamma CDF arguments are invalid")
    if value == 0:
        return 0.0
    epsilon = 1e-14
    tiny = 1e-300
    log_scale = -value + shape * math.log(value) - math.lgamma(shape)
    if value < shape + 1.0:
        term = 1.0 / shape
        total = term
        current = shape
        for _ in range(1, 10001):
            current += 1.0
            term *= value / current
            total += term
            if abs(term) <= abs(total) * epsilon:
                return min(1.0, max(0.0, total * math.exp(log_scale)))
        raise ResearchContractError("GAMMA_CDF_DID_NOT_CONVERGE", "gamma series did not converge")
    b = value + 1.0 - shape
    c = 1.0 / tiny
    d = 1.0 / b
    fraction = d
    for index in range(1, 10001):
        coefficient = -index * (index - shape)
        b += 2.0
        d = coefficient * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + coefficient / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        fraction *= delta
        if abs(delta - 1.0) <= epsilon:
            return min(1.0, max(0.0, 1.0 - math.exp(log_scale) * fraction))
    raise ResearchContractError("GAMMA_CDF_DID_NOT_CONVERGE", "gamma fraction did not converge")


def _gamma_mixture_cdf(
    value: float,
    weights: Sequence[float],
    shapes: Sequence[float],
    rates: Sequence[float],
) -> float:
    return math.fsum(
        weight * _regularized_gamma_p(shape, rate * value)
        for weight, shape, rate in zip(weights, shapes, rates)
    )


def _gamma_mixture_quantile(
    probability: float,
    weights: Sequence[float],
    shapes: Sequence[float],
    rates: Sequence[float],
) -> float:
    _validate_probability(probability, "quantile probability")
    if probability == 0:
        return 0.0
    low = 0.0
    high = max(shape / rate for shape, rate in zip(shapes, rates))
    high = max(1.0, high)
    while _gamma_mixture_cdf(high, weights, shapes, rates) < probability:
        high *= 2.0
        if not math.isfinite(high):
            raise ResearchContractError("GAMMA_QUANTILE_BRACKET_FAILURE", "gamma quantile did not bracket")
    for _ in range(256):
        middle = (low + high) / 2.0
        cdf = _gamma_mixture_cdf(middle, weights, shapes, rates)
        if abs(cdf - probability) <= 1e-10:
            return middle
        if cdf < probability:
            low = middle
        else:
            high = middle
    middle = (low + high) / 2.0
    if abs(_gamma_mixture_cdf(middle, weights, shapes, rates) - probability) > 1e-10:
        raise ResearchContractError("GAMMA_QUANTILE_ACCURACY_FAILURE", "gamma quantile accuracy failed")
    return middle


def _event_posterior(
    target: TargetContext,
    player_history: Sequence[HistoryFixture],
    peer_histories: Sequence[PeerHistory],
    event: str,
) -> EventRatePosterior:
    playing, exposure, events, missing = _event_summary(target, player_history, event)
    prior = _population_prior(target, peer_histories, event)
    prior_minutes = tuple(90.0 * value for value in PRIOR_STRENGTHS)
    if playing > 0 and exposure == 0:
        return EventRatePosterior(
            event, "MISSING_EVENT_HISTORY", "ALL_RELEVANT_EVENTS_MISSING",
            playing, exposure, events, missing, False, prior, None,
            prior_minutes, None, None, None, None, None, None,
        )
    shapes = tuple(value * prior.prior_mean + events for value in PRIOR_STRENGTHS)
    rates = tuple(value + exposure for value in PRIOR_STRENGTHS)
    log_weights = []
    for prior_weight, strength, shape, rate in zip(
        PRIOR_COMPONENT_WEIGHTS, PRIOR_STRENGTHS, shapes, rates
    ):
        prior_shape = strength * prior.prior_mean
        log_z = (
            prior_shape * math.log(strength)
            - math.lgamma(prior_shape)
            + math.lgamma(shape)
            - shape * math.log(rate)
        )
        log_weights.append(math.log(prior_weight) + log_z)
    maximum = max(log_weights)
    normalizer = maximum + math.log(math.fsum(math.exp(value - maximum) for value in log_weights))
    weights = tuple(math.exp(value - normalizer) for value in log_weights)
    _validate_pmf(weights, f"{event} mixture weights")
    means = tuple(shape / rate for shape, rate in zip(shapes, rates))
    mean = math.fsum(weight * value for weight, value in zip(weights, means))
    second_moment = math.fsum(
        weight * (shape / (rate * rate) + (shape / rate) ** 2)
        for weight, shape, rate in zip(weights, shapes, rates)
    )
    variance = max(0.0, second_moment - mean * mean)
    quantiles = tuple(
        _gamma_mixture_quantile(probability, weights, shapes, rates)
        for probability in (0.1, 0.5, 0.9)
    )
    for name, value in (("rate_mean", mean), ("rate_variance", variance), *[("quantile", q) for q in quantiles]):
        _require_finite(value, name)
    return EventRatePosterior(
        event=event,
        status="COMPLETE",
        history_state="PRIOR_ONLY" if playing == 0 else "HISTORY_UPDATED",
        playing_exposure=playing,
        observed_exposure=exposure,
        observed_events=events,
        missing_exposure=missing,
        partial_missingness=missing > 0 and exposure > 0,
        population_prior=prior,
        component_weights=weights,
        prior_minutes=prior_minutes,
        shapes=shapes,
        rates=rates,
        rate_mean=mean,
        rate_variance=variance,
        rate_quantiles=quantiles,
        weighted_prior_minutes_summary=math.fsum(
            weight * minutes for weight, minutes in zip(weights, prior_minutes)
        ),
    )


def build_ua1(
    target: TargetContext,
    player_history: Sequence[HistoryFixture],
    peer_histories: Sequence[PeerHistory],
) -> UA1Prediction:
    """Build separate xG/xA generalized-Bayes Gamma-mixture posteriors."""
    if not isinstance(peer_histories, (list, tuple)):
        raise ResearchContractError("INVALID_PEER_COLLECTION", "peer histories must be a sequence")
    return UA1Prediction(
        candidate_identity=UA1_IDENTITY,
        xg=_event_posterior(target, player_history, peer_histories, "xg"),
        xa=_event_posterior(target, player_history, peer_histories, "xa"),
    )


def _negative_binomial_mixture_pmf(
    count: int,
    exposure: float,
    weights: Sequence[float],
    shapes: Sequence[float],
    rates: Sequence[float],
) -> float:
    if exposure == 0:
        return 1.0 if count == 0 else 0.0
    terms = []
    for weight, shape, rate in zip(weights, shapes, rates):
        log_probability = (
            math.log(weight)
            + math.lgamma(count + shape)
            - math.lgamma(shape)
            - math.lgamma(count + 1)
            + shape * math.log(rate / (rate + exposure))
            + count * math.log(exposure / (rate + exposure))
        )
        terms.append(math.exp(log_probability))
    return math.fsum(terms)


def _count_quantile_from_probability(probability: float, probability_at: Any) -> int:
    cumulative = 0.0
    for count in range(10001):
        cumulative += probability_at(count)
        if cumulative >= probability:
            return count
    raise ResearchContractError("COUNT_QUANTILE_CEILING", "count quantile exceeded 10000")


def _count_prediction(
    posterior: EventRatePosterior,
    *,
    exposure: float | None = None,
    minute_pmf: Sequence[float] | None = None,
) -> CountPrediction:
    if posterior.status != "COMPLETE":
        return CountPrediction("MISSING_RATE", None, None, None, exposure, None, None, None)
    assert posterior.rate_mean is not None and posterior.rate_variance is not None
    assert posterior.component_weights and posterior.shapes and posterior.rates
    if minute_pmf is not None:
        checked_pmf = _validate_pmf(minute_pmf, "integrated minute PMF")
        mean_exposure = math.fsum(index * mass / 90.0 for index, mass in enumerate(checked_pmf))
        second_exposure = math.fsum((index / 90.0) ** 2 * mass for index, mass in enumerate(checked_pmf))
        mean = mean_exposure * posterior.rate_mean
        # Var(Y)=E[t]*E[lambda]+Var(t*lambda), with independent t and lambda.
        second_rate = posterior.rate_variance + posterior.rate_mean ** 2
        variance = mean + second_exposure * second_rate - mean ** 2

        def probability_at(count: int) -> float:
            return math.fsum(
                mass
                * _negative_binomial_mixture_pmf(
                    count,
                    minutes / 90.0,
                    posterior.component_weights or (),
                    posterior.shapes or (),
                    posterior.rates or (),
                )
                for minutes, mass in enumerate(checked_pmf)
            )

        resolved_exposure = None
        integrated = checked_pmf
    else:
        if exposure is None:
            raise ResearchContractError("MISSING_EXPOSURE", "count exposure is required")
        resolved_exposure = _require_nonnegative(exposure, "count exposure")
        mean = resolved_exposure * posterior.rate_mean
        variance = resolved_exposure * posterior.rate_mean + resolved_exposure ** 2 * posterior.rate_variance

        def probability_at(count: int) -> float:
            return _negative_binomial_mixture_pmf(
                count,
                resolved_exposure,
                posterior.component_weights or (),
                posterior.shapes or (),
                posterior.rates or (),
            )

        integrated = None
    interval = (
        _count_quantile_from_probability(0.1, probability_at),
        _count_quantile_from_probability(0.9, probability_at),
    )
    return CountPrediction(
        "COMPLETE", mean, max(0.0, variance), interval, resolved_exposure,
        posterior.component_weights, posterior.shapes, posterior.rates, integrated,
    )


def _component_total(
    appearance: float | None,
    goal_points: float | None,
    assist_points: float | None,
    *,
    coalesce_missing_attacks: bool,
) -> float | None:
    if appearance is None:
        return None
    if coalesce_missing_attacks:
        return appearance + (goal_points or 0.0) + (assist_points or 0.0)
    if goal_points is None or assist_points is None:
        return None
    return appearance + goal_points + assist_points


def assemble_um1_only(
    target: TargetContext,
    minutes: UM1Prediction,
    *,
    raw_xg_per90: float | None,
    raw_xa_per90: float | None,
) -> ModeledComponents:
    if minutes.candidate_identity != UM1_IDENTITY or minutes.fixture_count != len(target.fixture_ids):
        raise ResearchContractError("WRONG_CANDIDATE_IDENTITY", "UM1 assembly identity differs")
    for name, value in (("raw_xg_per90", raw_xg_per90), ("raw_xa_per90", raw_xa_per90)):
        if value is not None:
            _require_nonnegative(value, name)
    if not target.fixture_ids:
        return ModeledComponents(UM1_IDENTITY, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, True, None, None)
    goals = None if raw_xg_per90 is None else raw_xg_per90 * minutes.expected_minutes / 90.0
    assists = None if raw_xa_per90 is None else raw_xa_per90 * minutes.expected_minutes / 90.0
    goal_points = None if goals is None else goals * GOAL_POINTS[target.position]
    assist_points = None if assists is None else assists * 3.0
    return ModeledComponents(
        UM1_IDENTITY, minutes.fixture_count, minutes.expected_minutes,
        minutes.expected_appearance_points, goals, goal_points, assists, assist_points,
        _component_total(minutes.expected_appearance_points, goal_points, assist_points, coalesce_missing_attacks=True),
        goals is not None and assists is not None, None, None,
    )


def assemble_ua1_only(
    target: TargetContext,
    u0: U0GameweekPrediction,
    rates: UA1Prediction,
) -> ModeledComponents:
    if rates.candidate_identity != UA1_IDENTITY or u0.candidate_identity != U0_IDENTITY:
        raise ResearchContractError("WRONG_CANDIDATE_IDENTITY", "UA1 assembly identity differs")
    if u0.fixture_count != len(target.fixture_ids):
        raise ResearchContractError("FIXTURE_COUNT_MISMATCH", "U0 fixture count differs")
    if not target.fixture_ids:
        zero_count = CountPrediction("COMPLETE", 0.0, 0.0, (0, 0), 0.0, (1.0,), (1.0,), (1.0,))
        return ModeledComponents(UA1_IDENTITY, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, True, zero_count, zero_count)
    exposure = None if u0.expected_minutes_for_evaluation is None else u0.expected_minutes_for_evaluation / 90.0
    goal_count = _count_prediction(rates.xg, exposure=exposure) if exposure is not None else CountPrediction("MISSING_MINUTES", None, None, None, None, None, None, None)
    assist_count = _count_prediction(rates.xa, exposure=exposure) if exposure is not None else CountPrediction("MISSING_MINUTES", None, None, None, None, None, None, None)
    goals = goal_count.mean
    assists = assist_count.mean
    goal_points = None if goals is None else goals * GOAL_POINTS[target.position]
    assist_points = None if assists is None else assists * 3.0
    appearance = u0.appearance_points_for_evaluation
    complete = appearance is not None and goals is not None and assists is not None
    return ModeledComponents(
        UA1_IDENTITY, u0.fixture_count, u0.expected_minutes_for_evaluation,
        appearance, goals, goal_points, assists, assist_points,
        _component_total(appearance, goal_points, assist_points, coalesce_missing_attacks=True),
        complete, goal_count, assist_count,
    )


def assemble_combined(
    target: TargetContext,
    minutes: UM1Prediction,
    rates: UA1Prediction,
) -> ModeledComponents:
    if minutes.candidate_identity != UM1_IDENTITY or rates.candidate_identity != UA1_IDENTITY:
        raise ResearchContractError("WRONG_CANDIDATE_IDENTITY", "combined assembly identity differs")
    if minutes.fixture_count != len(target.fixture_ids):
        raise ResearchContractError("FIXTURE_COUNT_MISMATCH", "UM1 fixture count differs")
    if not target.fixture_ids:
        zero_count = CountPrediction("COMPLETE", 0.0, 0.0, (0, 0), None, (1.0,), (1.0,), (1.0,), (1.0,))
        return ModeledComponents(COMBINED_IDENTITY, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, True, zero_count, zero_count)
    goal_count = _count_prediction(rates.xg, minute_pmf=minutes.gameweek_minute_pmf)
    assist_count = _count_prediction(rates.xa, minute_pmf=minutes.gameweek_minute_pmf)
    goals = goal_count.mean
    assists = assist_count.mean
    goal_points = None if goals is None else goals * GOAL_POINTS[target.position]
    assist_points = None if assists is None else assists * 3.0
    complete = goals is not None and assists is not None
    return ModeledComponents(
        COMBINED_IDENTITY, minutes.fixture_count, minutes.expected_minutes,
        minutes.expected_appearance_points, goals, goal_points, assists, assist_points,
        _component_total(minutes.expected_appearance_points, goal_points, assist_points, coalesce_missing_attacks=False),
        complete, goal_count, assist_count,
    )
