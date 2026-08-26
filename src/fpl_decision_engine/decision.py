"""Deterministic ranking and FPL squad/XI optimization over supplied projections."""

from __future__ import annotations

import itertools
import json
import math
import os
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import duckdb
import highspy

from .projection_provider import (
    ProjectionDataset,
    ProjectionPlayer,
    ProjectionState,
    sha256_file,
)


DECISION_ENGINE_VERSION = "decision-engine-v2"
DECISION_OUTPUT_CLASSIFICATION = (
    "experimental_decision_output_using_xfp_v01_modeled_components_only"
)
DEFAULT_BUDGET_UNITS = 1000
SQUAD_POSITION_COUNTS = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
POSITION_ORDER = {"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}
OBJECTIVE_TOLERANCE = 1e-8
DECISION_POLICY_VERSION = "decision-eligibility-policy-v1"
STRICT_COMPLETE_ONLY_POLICY = "strict_complete_only"
APPEARANCE_ONLY_ALLOWED_POLICY = "appearance_only_allowed"
DEFAULT_DECISION_POLICY = STRICT_COMPLETE_ONLY_POLICY
DECISION_POLICIES = {
    STRICT_COMPLETE_ONLY_POLICY,
    APPEARANCE_ONLY_ALLOWED_POLICY,
}


class DecisionError(Exception):
    """Raised for invalid inputs or an infeasible decision problem."""


class DecisionOutputExistsError(DecisionError):
    """Raised rather than overwriting a generated decision artifact."""


def budget_m_to_units(value: str | float | Decimal) -> int:
    """Convert a £m budget to exact integer £0.1m units."""
    try:
        units = Decimal(str(value)) * Decimal(10)
    except InvalidOperation as exc:
        raise DecisionError(f"invalid budget: {value!r}") from exc
    if not units.is_finite() or units < 0 or units != units.to_integral_value():
        raise DecisionError(
            "budget must be a non-negative amount in exact £0.1m increments"
        )
    return int(units)


@dataclass(frozen=True)
class RankedPlayer:
    position_rank: int
    player: ProjectionPlayer


@dataclass(frozen=True)
class RankingResult:
    rows: tuple[RankedPlayer, ...]
    excluded_counts: dict[str, int]


@dataclass(frozen=True)
class SelectedPlayer:
    player: ProjectionPlayer
    is_starter: bool
    is_captain: bool
    is_vice_captain: bool
    base_contribution: float
    captain_bonus: float

    @property
    def total_contribution(self) -> float:
        return self.base_contribution + self.captain_bonus


@dataclass(frozen=True)
class DecisionResult:
    squad: tuple[ProjectionPlayer, ...]
    selections: tuple[SelectedPlayer, ...]
    formation: str
    squad_cost_units: int
    budget_units: int
    base_xi_projection: float
    captain_bonus: float
    total_objective: float
    club_counts: dict[int, int]
    excluded_counts: dict[str, int]

    @property
    def captain(self) -> ProjectionPlayer:
        return next(row.player for row in self.selections if row.is_captain)

    @property
    def vice_captain(self) -> ProjectionPlayer:
        return next(row.player for row in self.selections if row.is_vice_captain)


@dataclass(frozen=True)
class DecisionArtifacts:
    directory: Path
    rankings_path: Path
    squad_path: Path
    manifest_path: Path
    rankings_sha256: str
    squad_sha256: str
    manifest_sha256: str


def projection_exclusion_counts(players: Iterable[ProjectionPlayer]) -> dict[str, int]:
    counts = Counter(
        player.projection_state.value for player in players if not player.eligible
    )
    return {
        ProjectionState.INCOMPLETE.value: counts[ProjectionState.INCOMPLETE.value],
        ProjectionState.MISSING.value: counts[ProjectionState.MISSING.value],
    }


def rank_players(dataset: ProjectionDataset) -> RankingResult:
    """Rank eligible players within position; never compare across positions."""
    rows: list[RankedPlayer] = []
    for position in POSITION_ORDER:
        eligible = sorted(
            (player for player in dataset.players if player.position == position and player.eligible),
            key=lambda player: (-_projection(player), player.fpl_player_id),
        )
        rows.extend(
            RankedPlayer(position_rank=index, player=player)
            for index, player in enumerate(eligible, start=1)
        )
    return RankingResult(
        rows=tuple(rows),
        excluded_counts=projection_exclusion_counts(dataset.players),
    )


def _projection(player: ProjectionPlayer) -> float:
    if not player.eligible or player.projection is None:
        raise DecisionError(
            f"player {player.fpl_player_id} has no eligible numeric projection"
        )
    if not math.isfinite(player.projection):
        raise DecisionError(
            f"player {player.fpl_player_id} has a non-finite eligible projection"
        )
    return player.projection


def projection_eligible_for_policy(
    player: ProjectionPlayer, decision_policy: str = DEFAULT_DECISION_POLICY
) -> bool:
    """Return eligibility without changing the projection's original state."""
    if decision_policy not in DECISION_POLICIES:
        raise DecisionError(f"unsupported decision policy: {decision_policy!r}")
    if decision_policy == STRICT_COMPLETE_ONLY_POLICY:
        return player.eligible
    if player.projection is None or not math.isfinite(player.projection):
        return False
    if player.projection_state == ProjectionState.INCOMPLETE:
        if player.expected_minutes != 0.0:
            raise DecisionError(
                "appearance_only_allowed invariant failed: numeric incomplete "
                f"projection for player {player.fpl_player_id} must have exactly "
                "zero expected minutes"
            )
        return True
    return player.projection_state in {
        ProjectionState.VALID,
        ProjectionState.VERIFIED_BLANK,
    }


def _policy_projection(player: ProjectionPlayer, decision_policy: str) -> float:
    if (
        not projection_eligible_for_policy(player, decision_policy)
        or player.projection is None
    ):
        raise DecisionError(
            f"player {player.fpl_player_id} has no numeric projection eligible under "
            f"decision policy {decision_policy}"
        )
    if not math.isfinite(player.projection):
        raise DecisionError(
            f"player {player.fpl_player_id} has a non-finite eligible projection"
        )
    return player.projection


def _validate_squad_players(
    squad: Iterable[ProjectionPlayer],
    *,
    budget_units: int | None = None,
    decision_policy: str = DEFAULT_DECISION_POLICY,
) -> tuple[ProjectionPlayer, ...]:
    if decision_policy not in DECISION_POLICIES:
        raise DecisionError(f"unsupported decision policy: {decision_policy!r}")
    players = tuple(squad)
    if len(players) != 15:
        raise DecisionError(f"a squad must contain exactly 15 players, found {len(players)}")
    ids = [player.fpl_player_id for player in players]
    if len(set(ids)) != len(ids):
        raise DecisionError("a squad cannot contain duplicate player IDs")
    ineligible = [
        player.fpl_player_id
        for player in players
        if not projection_eligible_for_policy(player, decision_policy)
    ]
    if ineligible:
        raise DecisionError(
            f"squad contains projections ineligible under {decision_policy} for player IDs: "
            + ", ".join(map(str, sorted(ineligible)))
        )
    position_counts = Counter(player.position for player in players)
    if dict(position_counts) != SQUAD_POSITION_COUNTS:
        raise DecisionError(
            f"squad positions must be {SQUAD_POSITION_COUNTS}, found {dict(position_counts)}"
        )
    club_counts = Counter(player.team_id for player in players)
    over_club_limit = {
        team: count for team, count in club_counts.items() if count > 3
    }
    if over_club_limit:
        raise DecisionError(
            f"squad exceeds the three-per-club limit: {over_club_limit}"
        )
    if budget_units is not None:
        if budget_units < 0:
            raise DecisionError("budget cannot be negative")
        cost = sum(player.price_units for player in players)
        if cost > budget_units:
            raise DecisionError(
                f"squad costs {cost} price units but budget is {budget_units}"
            )
    return players


def resolve_existing_squad(
    dataset: ProjectionDataset,
    player_ids: Iterable[int],
    *,
    budget_units: int | None = None,
    decision_policy: str = DEFAULT_DECISION_POLICY,
) -> tuple[ProjectionPlayer, ...]:
    requested = tuple(int(player_id) for player_id in player_ids)
    if len(set(requested)) != len(requested):
        raise DecisionError("duplicate player IDs were supplied")
    by_id = {player.fpl_player_id: player for player in dataset.players}
    missing = sorted(set(requested) - set(by_id))
    if missing:
        raise DecisionError(
            "player IDs do not resolve in the projection provider: "
            + ", ".join(map(str, missing))
        )
    return _validate_squad_players(
        (by_id[player_id] for player_id in requested),
        budget_units=budget_units,
        decision_policy=decision_policy,
    )


def _formation_counts() -> tuple[tuple[int, int, int], ...]:
    formations: list[tuple[int, int, int]] = []
    for defenders in range(3, 6):
        for midfielders in range(2, 6):
            forwards = 10 - defenders - midfielders
            if 1 <= forwards <= 3:
                formations.append((defenders, midfielders, forwards))
    return tuple(formations)


def optimize_xi(
    squad: Iterable[ProjectionPlayer],
    *,
    budget_units: int | None = None,
    excluded_counts: dict[str, int] | None = None,
    decision_policy: str = DEFAULT_DECISION_POLICY,
) -> DecisionResult:
    """Enumerate the small legal-XI space for one validated 15-player squad."""
    players = _validate_squad_players(
        squad,
        budget_units=budget_units,
        decision_policy=decision_policy,
    )
    by_position = {
        position: tuple(player for player in players if player.position == position)
        for position in POSITION_ORDER
    }
    best_objective: float | None = None
    best_starter_ids: tuple[int, ...] | None = None
    best_starters: tuple[ProjectionPlayer, ...] | None = None
    for goalkeeper in by_position["GK"]:
        for defenders, midfielders, forwards in _formation_counts():
            for defender_group in itertools.combinations(by_position["DEF"], defenders):
                for midfielder_group in itertools.combinations(
                    by_position["MID"], midfielders
                ):
                    for forward_group in itertools.combinations(
                        by_position["FWD"], forwards
                    ):
                        starters = (
                            (goalkeeper,) + defender_group + midfielder_group + forward_group
                        )
                        captain_projection = max(
                            _policy_projection(player, decision_policy)
                            for player in starters
                        )
                        objective = (
                            sum(
                                _policy_projection(player, decision_policy)
                                for player in starters
                            )
                            + captain_projection
                        )
                        starter_ids = tuple(
                            sorted(player.fpl_player_id for player in starters)
                        )
                        if (
                            best_objective is None
                            or objective > best_objective + OBJECTIVE_TOLERANCE
                            or (
                                math.isclose(
                                    objective,
                                    best_objective,
                                    rel_tol=0.0,
                                    abs_tol=OBJECTIVE_TOLERANCE,
                                )
                                and (
                                    best_starter_ids is None
                                    or starter_ids < best_starter_ids
                                )
                            )
                        ):
                            best_objective = objective
                            best_starter_ids = starter_ids
                            best_starters = starters
    if best_starters is None:
        raise DecisionError("no valid starting XI exists for the supplied squad")

    starter_ids = {player.fpl_player_id for player in best_starters}
    captain_order = sorted(
        best_starters,
        key=lambda player: (
            -_policy_projection(player, decision_policy),
            player.fpl_player_id,
        ),
    )
    captain, vice_captain = captain_order[:2]
    formation_counts = Counter(player.position for player in best_starters)
    formation = (
        f"{formation_counts['DEF']}-{formation_counts['MID']}-{formation_counts['FWD']}"
    )
    ordered_players = sorted(
        players,
        key=lambda player: (
            player.fpl_player_id not in starter_ids,
            POSITION_ORDER[player.position],
            player.fpl_player_id,
        ),
    )
    selections = tuple(
        SelectedPlayer(
            player=player,
            is_starter=player.fpl_player_id in starter_ids,
            is_captain=player.fpl_player_id == captain.fpl_player_id,
            is_vice_captain=player.fpl_player_id == vice_captain.fpl_player_id,
            base_contribution=(
                _policy_projection(player, decision_policy)
                if player.fpl_player_id in starter_ids
                else 0.0
            ),
            captain_bonus=(
                _policy_projection(player, decision_policy)
                if player.fpl_player_id == captain.fpl_player_id
                else 0.0
            ),
        )
        for player in ordered_players
    )
    base_projection = sum(row.base_contribution for row in selections)
    captain_bonus = _policy_projection(captain, decision_policy)
    cost = sum(player.price_units for player in players)
    return DecisionResult(
        squad=tuple(sorted(players, key=lambda player: player.fpl_player_id)),
        selections=selections,
        formation=formation,
        squad_cost_units=cost,
        budget_units=budget_units if budget_units is not None else cost,
        base_xi_projection=base_projection,
        captain_bonus=captain_bonus,
        total_objective=base_projection + captain_bonus,
        club_counts=dict(sorted(Counter(player.team_id for player in players).items())),
        excluded_counts=excluded_counts or {},
    )


def _sum_variables(variables: Any, indexes: Iterable[int]) -> Any:
    return sum((variables[index] for index in indexes), 0)


def _build_squad_model(
    players: tuple[ProjectionPlayer, ...], budget_units: int
) -> tuple[highspy.Highs, Any, Any, Any, Any, Any, Any]:
    model = highspy.Highs()
    model.setOptionValue("output_flag", False)
    model.setOptionValue("threads", 1)
    model.setOptionValue("random_seed", 0)
    model.setOptionValue("mip_feasibility_tolerance", 1e-9)
    count = len(players)
    squad = model.addVariables(
        count, lb=0, ub=1, type=highspy.HighsVarType.kInteger
    )
    starter = model.addVariables(
        count, lb=0, ub=1, type=highspy.HighsVarType.kInteger
    )
    captain = model.addVariables(
        count, lb=0, ub=1, type=highspy.HighsVarType.kInteger
    )
    vice_captain = model.addVariables(
        count, lb=0, ub=1, type=highspy.HighsVarType.kInteger
    )
    for index in range(count):
        model.addConstr(starter[index] <= squad[index])
        model.addConstr(captain[index] <= starter[index])
        model.addConstr(vice_captain[index] <= starter[index])
        model.addConstr(captain[index] + vice_captain[index] <= 1)
    model.addConstr(_sum_variables(squad, range(count)) == 15)
    model.addConstr(_sum_variables(starter, range(count)) == 11)
    model.addConstr(_sum_variables(captain, range(count)) == 1)
    model.addConstr(_sum_variables(vice_captain, range(count)) == 1)
    for position, required in SQUAD_POSITION_COUNTS.items():
        indexes = [
            index for index, player in enumerate(players) if player.position == position
        ]
        model.addConstr(_sum_variables(squad, indexes) == required)
    for team_id in sorted({player.team_id for player in players}):
        indexes = [
            index for index, player in enumerate(players) if player.team_id == team_id
        ]
        model.addConstr(_sum_variables(squad, indexes) <= 3)
    goalkeeper_indexes = [
        index for index, player in enumerate(players) if player.position == "GK"
    ]
    defender_indexes = [
        index for index, player in enumerate(players) if player.position == "DEF"
    ]
    midfielder_indexes = [
        index for index, player in enumerate(players) if player.position == "MID"
    ]
    forward_indexes = [
        index for index, player in enumerate(players) if player.position == "FWD"
    ]
    model.addConstr(_sum_variables(starter, goalkeeper_indexes) == 1)
    model.addConstr(_sum_variables(starter, defender_indexes) >= 3)
    model.addConstr(_sum_variables(starter, midfielder_indexes) >= 2)
    model.addConstr(_sum_variables(starter, forward_indexes) >= 1)
    cost_expression = sum(
        (player.price_units * squad[index] for index, player in enumerate(players)), 0
    )
    model.addConstr(cost_expression <= budget_units)
    objective_expression = sum(
        (
            _projection(player) * starter[index]
            + _projection(player) * captain[index]
            for index, player in enumerate(players)
        ),
        0,
    )
    return (
        model,
        squad,
        starter,
        captain,
        vice_captain,
        objective_expression,
        cost_expression,
    )


def _require_optimal(model: highspy.Highs, message: str) -> None:
    status = model.getModelStatus()
    if status != highspy.HighsModelStatus.kOptimal:
        raise DecisionError(f"{message}: solver status {model.modelStatusToString(status)}")


def optimize_squad(
    dataset: ProjectionDataset, *, budget_units: int = DEFAULT_BUDGET_UNITS
) -> DecisionResult:
    """Solve the exact legal squad, XI and captain problem with deterministic ties."""
    if budget_units < 0:
        raise DecisionError("budget cannot be negative")
    eligible = tuple(
        sorted(
            (player for player in dataset.players if player.eligible),
            key=lambda player: player.fpl_player_id,
        )
    )
    position_counts = Counter(player.position for player in eligible)
    shortages = {
        position: required - position_counts[position]
        for position, required in SQUAD_POSITION_COUNTS.items()
        if position_counts[position] < required
    }
    if shortages:
        raise DecisionError(f"insufficient eligible players by position: {shortages}")

    (
        model,
        squad,
        _,
        _,
        _,
        objective_expression,
        cost_expression,
    ) = _build_squad_model(eligible, budget_units)
    model.maximize(objective_expression)
    _require_optimal(model, "no legal squad can maximize the projection objective")
    optimum = model.getObjectiveValue()

    model.addConstr(objective_expression >= optimum - OBJECTIVE_TOLERANCE)
    model.addConstr(objective_expression <= optimum + OBJECTIVE_TOLERANCE)
    model.minimize(cost_expression)
    _require_optimal(model, "no minimum-cost objective-tied squad exists")
    minimum_cost = int(round(model.getObjectiveValue()))
    model.addConstr(cost_expression == minimum_cost)

    # A fixed-size set is lexicographically minimized by greedily including each
    # ascending ID whenever a still-optimal, minimum-cost completion exists.
    current_values = model.val(squad)
    selected_fixed = 0
    for index, _player in enumerate(eligible):
        if selected_fixed == 15:
            break
        if current_values[index] > 0.5:
            model.addConstr(squad[index] == 1)
            selected_fixed += 1
            continue
        trial = model.addConstr(squad[index] == 1)
        model.solve()
        trial_status = model.getModelStatus()
        if trial_status == highspy.HighsModelStatus.kOptimal:
            selected_fixed += 1
            current_values = model.val(squad)
        elif trial_status == highspy.HighsModelStatus.kInfeasible:
            model.removeConstr(trial)
            model.addConstr(squad[index] == 0)
            model.solve()
            _require_optimal(model, "lexicographic tie-breaking lost feasibility")
            current_values = model.val(squad)
        else:
            _require_optimal(model, "lexicographic feasibility was not proven")

    if selected_fixed != 15:
        raise DecisionError("lexicographic tie-breaking did not select 15 players")
    selected = tuple(
        player
        for index, player in enumerate(eligible)
        if current_values[index] > 0.5
    )
    result = optimize_xi(
        selected,
        budget_units=budget_units,
        excluded_counts=projection_exclusion_counts(dataset.players),
    )
    if not math.isclose(
        result.total_objective,
        optimum,
        rel_tol=0.0,
        abs_tol=OBJECTIVE_TOLERANCE,
    ):
        raise DecisionError(
            "deterministic XI reconstruction does not match the proven joint optimum"
        )
    return result


def decision_result_dict(
    dataset: ProjectionDataset, result: DecisionResult
) -> dict[str, Any]:
    return {
        "classification": DECISION_OUTPUT_CLASSIFICATION,
        "decision_engine_version": DECISION_ENGINE_VERSION,
        "season": dataset.season,
        "target_gameweek": dataset.target_gameweek,
        "snapshot_timestamp": dataset.snapshot_timestamp,
        "projection_provider_id": dataset.provider_id,
        "projection_provider_version": dataset.provider_version,
        "projection_model_id": dataset.source_model_id,
        "projection_model_scope": dataset.model_scope,
        "source_artifact_path": dataset.source_artifact_path,
        "source_artifact_sha256": dataset.source_artifact_sha256,
        "players_artifact_path": dataset.players_artifact_path,
        "players_artifact_sha256": dataset.players_artifact_sha256,
        "budget_units": result.budget_units,
        "budget_m": result.budget_units / 10,
        "squad_cost_units": result.squad_cost_units,
        "squad_cost_m": result.squad_cost_units / 10,
        "remaining_budget_units": result.budget_units - result.squad_cost_units,
        "remaining_budget_m": (result.budget_units - result.squad_cost_units) / 10,
        "formation": result.formation,
        "base_xi_projection": result.base_xi_projection,
        "captain_bonus": result.captain_bonus,
        "total_objective": result.total_objective,
        "captain": result.captain.player_name,
        "vice_captain": result.vice_captain.player_name,
        "club_counts": {str(key): value for key, value in result.club_counts.items()},
        "excluded_player_counts": result.excluded_counts,
        "constraints": {
            "squad_size": 15,
            "squad_positions": SQUAD_POSITION_COUNTS,
            "max_per_club": 3,
            "starter_count": 11,
            "starter_goalkeepers": 1,
            "minimum_starting_defenders": 3,
            "minimum_starting_midfielders": 2,
            "minimum_starting_forwards": 1,
        },
        "tie_breaking": [
            "maximum total objective",
            "lower total squad cost within objective tolerance 1e-8",
            "lexicographically lower sorted fpl_player_id squad tuple",
            "lexicographically lower sorted starter fpl_player_id tuple",
            "lower fpl_player_id for captain/vice projection ties",
        ],
        "players": [
            {
                "fpl_player_id": row.player.fpl_player_id,
                "player_name": row.player.player_name,
                "team_id": row.player.team_id,
                "team": row.player.team_short_name,
                "position": row.player.position,
                "price_units": row.player.price_units,
                "price_m": row.player.price_m,
                "projection": row.player.projection,
                "projection_state": row.player.projection_state.value,
                "availability_status": row.player.availability_status,
                "starter": row.is_starter,
                "captain": row.is_captain,
                "vice_captain": row.is_vice_captain,
                "base_contribution": row.base_contribution,
                "captain_bonus": row.captain_bonus,
                "total_contribution": row.total_contribution,
            }
            for row in result.selections
        ],
    }


def _write_rankings_parquet(path: Path, rankings: RankingResult) -> None:
    connection = duckdb.connect()
    try:
        connection.execute(
            """CREATE TABLE rankings (
                position VARCHAR, position_rank INTEGER, fpl_player_id BIGINT,
                player_name VARCHAR, team_id INTEGER, team_name VARCHAR,
                team_short_name VARCHAR, price_units INTEGER, price_m DOUBLE,
                projection DOUBLE, projection_state VARCHAR,
                availability_status VARCHAR, verified_blank BOOLEAN,
                source_model_id VARCHAR, model_scope VARCHAR,
                source_artifact_path VARCHAR, source_artifact_sha256 VARCHAR
            )"""
        )
        connection.executemany(
            "INSERT INTO rankings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row.player.position,
                    row.position_rank,
                    row.player.fpl_player_id,
                    row.player.player_name,
                    row.player.team_id,
                    row.player.team_name,
                    row.player.team_short_name,
                    row.player.price_units,
                    row.player.price_m,
                    row.player.projection,
                    row.player.projection_state.value,
                    row.player.availability_status,
                    row.player.verified_blank,
                    row.player.source_model_id,
                    row.player.model_scope,
                    row.player.source_artifact_path,
                    row.player.source_artifact_sha256,
                )
                for row in rankings.rows
            ],
        )
        connection.execute("COPY rankings TO ? (FORMAT PARQUET)", [str(path)])
    finally:
        connection.close()


def _write_squad_parquet(path: Path, result: DecisionResult) -> None:
    connection = duckdb.connect()
    try:
        connection.execute(
            """CREATE TABLE squad (
                fpl_player_id BIGINT, player_name VARCHAR, team_id INTEGER,
                team_name VARCHAR, team_short_name VARCHAR, position VARCHAR,
                price_units INTEGER, price_m DOUBLE, projection DOUBLE,
                projection_state VARCHAR, availability_status VARCHAR,
                starter BOOLEAN, captain BOOLEAN, vice_captain BOOLEAN,
                base_contribution DOUBLE, captain_bonus DOUBLE,
                total_contribution DOUBLE, source_model_id VARCHAR,
                model_scope VARCHAR, source_artifact_path VARCHAR,
                source_artifact_sha256 VARCHAR
            )"""
        )
        connection.executemany(
            "INSERT INTO squad VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row.player.fpl_player_id,
                    row.player.player_name,
                    row.player.team_id,
                    row.player.team_name,
                    row.player.team_short_name,
                    row.player.position,
                    row.player.price_units,
                    row.player.price_m,
                    row.player.projection,
                    row.player.projection_state.value,
                    row.player.availability_status,
                    row.is_starter,
                    row.is_captain,
                    row.is_vice_captain,
                    row.base_contribution,
                    row.captain_bonus,
                    row.total_contribution,
                    row.player.source_model_id,
                    row.player.model_scope,
                    row.player.source_artifact_path,
                    row.player.source_artifact_sha256,
                )
                for row in result.selections
            ],
        )
        connection.execute("COPY squad TO ? (FORMAT PARQUET)", [str(path)])
    finally:
        connection.close()


def write_decision_artifacts(
    dataset: ProjectionDataset,
    rankings: RankingResult,
    result: DecisionResult,
    *,
    decision_data_root: Path = Path("data/decisions/fpl"),
    generation_timestamp: str | None = None,
) -> DecisionArtifacts:
    generated_at = generation_timestamp or datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S.%fZ"
    )
    directory = (
        decision_data_root
        / dataset.season
        / dataset.snapshot_timestamp
        / f"gameweek={dataset.target_gameweek}"
        / DECISION_ENGINE_VERSION
        / generated_at
    )
    if directory.exists():
        raise DecisionOutputExistsError(
            f"decision output already exists and will not be overwritten: {directory}"
        )
    directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = directory.parent / f".{generated_at}.{uuid.uuid4().hex}.tmp"
    temporary.mkdir()
    try:
        rankings_path = temporary / "within_position_rankings.parquet"
        squad_path = temporary / "optimized_squad.parquet"
        manifest_path = temporary / "decision_manifest.json"
        _write_rankings_parquet(rankings_path, rankings)
        _write_squad_parquet(squad_path, result)
        payload = decision_result_dict(dataset, result)
        payload.update(
            {
                "generation_timestamp": generated_at,
                "outputs": {
                    "within_position_rankings.parquet": sha256_file(rankings_path),
                    "optimized_squad.parquet": sha256_file(squad_path),
                },
            }
        )
        with manifest_path.open("x", encoding="utf-8") as manifest:
            json.dump(payload, manifest, indent=2, sort_keys=True)
            manifest.write("\n")
            manifest.flush()
            os.fsync(manifest.fileno())
        temporary.rename(directory)
    except Exception:
        for child in temporary.glob("*"):
            child.unlink()
        if temporary.exists():
            temporary.rmdir()
        raise
    rankings_path = directory / "within_position_rankings.parquet"
    squad_path = directory / "optimized_squad.parquet"
    manifest_path = directory / "decision_manifest.json"
    return DecisionArtifacts(
        directory=directory,
        rankings_path=rankings_path,
        squad_path=squad_path,
        manifest_path=manifest_path,
        rankings_sha256=sha256_file(rankings_path),
        squad_sha256=sha256_file(squad_path),
        manifest_sha256=sha256_file(manifest_path),
    )
