"""Read-only reliability diagnostics for an immutable one-transfer decision."""

from __future__ import annotations

import json
import math
import os
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import duckdb

from .decision import (
    APPEARANCE_ONLY_ALLOWED_POLICY,
    OBJECTIVE_TOLERANCE,
    SQUAD_POSITION_COUNTS,
)
from .projection_provider import (
    ProjectionDataset,
    ProjectionState,
    XfpV01ParquetProvider,
    sha256_file,
)
from .transfer_decision import (
    ONE_TRANSFER_DECISION_VERSION,
    ROLL,
    TRANSFER,
)


DECISION_RELIABILITY_VERSION = "decision-reliability-v1"
DECISION_RELIABILITY_CLASSIFICATION = "diagnostic_only_decision_reliability"
REFERENCE_QUANTILES = (0.90, 0.95, 0.99)
MINIMUM_PRIOR_MINUTES = (30, 60, 90, 91)
GOAL_POINTS = {"GK": 6, "DEF": 6, "MID": 5, "FWD": 4}
EXPECTED_SENSITIVITY_VIEW_IDS = (
    *(f"minimum_prior_minutes_{threshold}" for threshold in MINIMUM_PRIOR_MINUTES),
    *(f"exclude_rates_above_position_p{int(q * 100)}" for q in REFERENCE_QUANTILES),
    *(f"cap_rates_at_position_p{int(q * 100)}" for q in REFERENCE_QUANTILES),
    "xi_only_without_captain_amplification",
)


class DecisionReliabilityError(Exception):
    """Raised when frozen reliability provenance cannot be established."""


class DecisionReliabilityOutputExistsError(DecisionReliabilityError):
    """Raised rather than overwriting an immutable reliability artifact."""


@dataclass(frozen=True)
class PlayerReliability:
    fpl_player_id: int
    player_name: str
    position_id: int
    position: str
    projection: float | None
    projection_state: str
    prior_total_minutes: float
    prior_appearances: int
    prior_starts: int
    cumulative_prior_xg: float | None
    cumulative_prior_xa: float | None
    prior_xg_per_90: float | None
    prior_xa_per_90: float | None
    low_sample: bool
    prediction_complete: bool
    expected_minutes: float | None
    appearance_xfp: float | None
    availability_status: str | None
    chance_of_playing_next_round: int | None
    availability_news: str | None


@dataclass(frozen=True)
class FixedSquadDiagnostic:
    starting_xi_ids: tuple[int, ...]
    formation: str
    captain_id: int | None
    vice_captain_id: int | None
    base_xi_projection: float
    captain_bonus: float
    total_objective: float


@dataclass(frozen=True)
class ReliabilityContext:
    decision_path: Path
    decision_sha256: str
    candidate_path: Path
    candidate_sha256: str
    feature_path: Path
    feature_sha256: str
    decision: dict[str, Any]
    candidates: tuple[dict[str, Any], ...]
    projections: ProjectionDataset
    players: dict[int, PlayerReliability]
    reference: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class DecisionReliabilityArtifacts:
    directory: Path
    reliability_path: Path
    reliability_sha256: str


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DecisionReliabilityError(f"{label} must be a JSON object")
    return value


def _read_json(path: Path, label: str) -> Any:
    if not path.is_file():
        raise DecisionReliabilityError(f"{label} does not exist: {path}")
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionReliabilityError(f"could not read {label}: {exc}") from exc


def _verified_link(payload: Mapping[str, Any], label: str) -> Path:
    try:
        path = Path(str(payload["path"])).resolve()
        expected = str(payload["sha256"])
    except KeyError as exc:
        raise DecisionReliabilityError(f"{label} provenance is incomplete") from exc
    observed = sha256_file(path) if path.is_file() else None
    if observed != expected:
        raise DecisionReliabilityError(
            f"{label} hash mismatch: expected {expected}, observed {observed}"
        )
    return path


def _float(value: Any, label: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionReliabilityError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise DecisionReliabilityError(f"{label} must be finite")
    return result


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionReliabilityError(f"{label} must be an integer")
    result = int(value)
    if result != value:
        raise DecisionReliabilityError(f"{label} must be an integer")
    return result


def _quantile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise DecisionReliabilityError("a percentile reference population is empty")
    index = (len(ordered) - 1) * quantile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _defined_finite_rate(
    player: PlayerReliability, field: str
) -> float | None:
    value = getattr(player, field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionReliabilityError(
            f"player {player.fpl_player_id} {field} must be numeric or null"
        )
    rate = float(value)
    if not math.isfinite(rate):
        raise DecisionReliabilityError(
            f"player {player.fpl_player_id} {field} must be finite when defined"
        )
    return rate


def build_rate_reference(
    players: Mapping[int, PlayerReliability],
) -> dict[str, dict[str, Any]]:
    """Build per-metric positional distributions from complete, defined rates."""
    by_position: dict[str, list[PlayerReliability]] = defaultdict(list)
    for player in players.values():
        if player.position not in GOAL_POINTS:
            raise DecisionReliabilityError(
                f"player {player.fpl_player_id} has unsupported position {player.position}"
            )
        by_position[player.position].append(player)
    reference: dict[str, dict[str, Any]] = {}
    for position in GOAL_POINTS:
        rows = by_position.get(position, [])
        if not rows:
            raise DecisionReliabilityError(
                f"no reliability reference rows exist for {position}"
            )
        section: dict[str, Any] = {}
        for field in ("prior_xg_per_90", "prior_xa_per_90"):
            validated = [(row, _defined_finite_rate(row, field)) for row in rows]
            rates = [
                rate
                for row, rate in validated
                if row.prediction_complete and rate is not None
            ]
            if not rates:
                raise DecisionReliabilityError(
                    f"no complete, defined {field} reference values exist for {position}"
                )
            section[field] = {
                "eligibility": (
                    "prediction_complete=true and this metric's rate is non-null "
                    "and finite"
                ),
                "population_n": len(rates),
                "position_rows_n": len(rows),
                "prediction_complete_n": sum(
                    row.prediction_complete for row, _ in validated
                ),
                "excluded_incomplete_n": sum(
                    not row.prediction_complete for row, _ in validated
                ),
                "excluded_null_rate_n": sum(
                    rate is None for _, rate in validated
                ),
                "excluded_complete_null_rate_n": sum(
                    row.prediction_complete and rate is None
                    for row, rate in validated
                ),
                "defined_incomplete_rate_n": sum(
                    not row.prediction_complete and rate is not None
                    for row, rate in validated
                ),
                "p90": _quantile(rates, 0.90),
                "p95": _quantile(rates, 0.95),
                "p99": _quantile(rates, 0.99),
            }
        reference[position] = section
    return reference


def _rate_diagnostic(
    player: PlayerReliability,
    field: str,
    players: Mapping[int, PlayerReliability],
    reference: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    value = _defined_finite_rate(player, field)
    population_n = reference[player.position][field]["population_n"]
    if value is None or not player.prediction_complete:
        return {
            "value": value,
            "eligible_for_reference_population": False,
            "reference_exclusion_reason": (
                "undefined_rate" if value is None else "prediction_incomplete"
            ),
            "position_rank_desc": None,
            "position_population_n": population_n,
            "empirical_percentile_pct": None,
            "above_position_p95": False,
        }
    peers = sorted(
        (
            row
            for row in players.values()
            if row.position == player.position
            and row.prediction_complete
            and _defined_finite_rate(row, field) is not None
        ),
        key=lambda row: (-float(_defined_finite_rate(row, field)), row.fpl_player_id),
    )
    if len(peers) != population_n:
        raise DecisionReliabilityError(
            f"{player.position} {field} rank population does not match its percentile population"
        )
    rank = next(
        index
        for index, row in enumerate(peers, 1)
        if row.fpl_player_id == player.fpl_player_id
    )
    percentile = 100.0 * sum(
        float(_defined_finite_rate(row, field)) <= value for row in peers
    ) / len(peers)
    return {
        "value": value,
        "eligible_for_reference_population": True,
        "reference_exclusion_reason": None,
        "position_rank_desc": rank,
        "position_population_n": len(peers),
        "empirical_percentile_pct": percentile,
        "above_position_p95": value > reference[player.position][field]["p95"],
    }


def player_reliability_payload(
    player: PlayerReliability,
    *,
    roles: Iterable[str],
    players: Mapping[int, PlayerReliability],
    reference: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    xg = _rate_diagnostic(player, "prior_xg_per_90", players, reference)
    xa = _rate_diagnostic(player, "prior_xa_per_90", players, reference)
    return {
        "element_id": player.fpl_player_id,
        "name": player.player_name,
        "position": player.position,
        "roles": sorted(set(roles)),
        "projection": player.projection,
        "projection_state": player.projection_state,
        "prior_total_minutes": player.prior_total_minutes,
        "prior_appearances": player.prior_appearances,
        "prior_starts": player.prior_starts,
        "cumulative_prior_xg": player.cumulative_prior_xg,
        "cumulative_prior_xa": player.cumulative_prior_xa,
        "prior_xg_per_90": player.prior_xg_per_90,
        "prior_xa_per_90": player.prior_xa_per_90,
        "rate_diagnostics": {"xg_per_90": xg, "xa_per_90": xa},
        "unusually_extreme_attacking_rate": (
            xg["above_position_p95"] or xa["above_position_p95"]
        ),
        "low_sample": player.low_sample,
        "prediction_complete": player.prediction_complete,
        "expected_minutes": player.expected_minutes,
        "availability": {
            "status": player.availability_status,
            "chance_of_playing_next_round": player.chance_of_playing_next_round,
            "news": player.availability_news,
        },
    }


def _load_feature_rows(feature_path: Path) -> dict[int, tuple[Any, ...]]:
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """SELECT fpl_player_id, prior_total_minutes, prior_appearances,
                      prior_starts, cumulative_prior_xg, cumulative_prior_xa,
                      availability_status, chance_of_playing_next_round,
                      availability_news
                 FROM read_parquet(?)
             ORDER BY fpl_player_id""",
            [str(feature_path)],
        ).fetchall()
    except duckdb.Error as exc:
        raise DecisionReliabilityError(
            f"could not read frozen feature provenance: {exc}"
        ) from exc
    finally:
        connection.close()
    grouped: dict[int, set[tuple[Any, ...]]] = defaultdict(set)
    for row in rows:
        grouped[int(row[0])].add(tuple(row[1:]))
    collapsed: dict[int, tuple[Any, ...]] = {}
    for player_id, values in grouped.items():
        if len(values) != 1:
            raise DecisionReliabilityError(
                f"frozen feature reliability fields conflict across fixtures for player {player_id}"
            )
        collapsed[player_id] = next(iter(values))
    return collapsed


def _load_prediction_rows(projection_path: Path) -> tuple[dict[int, tuple[Any, ...]], str]:
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """SELECT fpl_player_id, gameweek_xfp_v01,
                      gameweek_expected_minutes_v01,
                      CAST(gameweek_appearance_xfp_v01 AS DOUBLE),
                      prior_minutes, prior_xg_per_90_used, prior_xa_per_90_used,
                      low_sample, prediction_complete, feature_input_sha256
                 FROM read_parquet(?)
             ORDER BY fpl_player_id""",
            [str(projection_path)],
        ).fetchall()
    except duckdb.Error as exc:
        raise DecisionReliabilityError(
            f"could not read frozen prediction provenance: {exc}"
        ) from exc
    finally:
        connection.close()
    if len(rows) != len({row[0] for row in rows}):
        raise DecisionReliabilityError("frozen prediction player IDs are not unique")
    feature_hashes = {str(row[9]) for row in rows if row[9] is not None}
    if len(feature_hashes) != 1:
        raise DecisionReliabilityError("frozen prediction does not identify one feature hash")
    return {int(row[0]): tuple(row[1:9]) for row in rows}, next(iter(feature_hashes))


def load_reliability_context(
    decision_artifact: Path, feature_artifact: Path
) -> ReliabilityContext:
    """Load and hash-verify every immutable source needed by the diagnostic."""
    decision_path = decision_artifact.resolve()
    decision = _require_dict(
        _read_json(decision_path, "Task 016 decision artifact"),
        "Task 016 decision",
    )
    if decision.get("version") != ONE_TRANSFER_DECISION_VERSION:
        raise DecisionReliabilityError("Task 016 decision version is unsupported")
    if decision.get("decision_policy") != APPEARANCE_ONLY_ALLOWED_POLICY:
        raise DecisionReliabilityError("reliability analysis requires appearance_only_allowed")
    decision_hash = sha256_file(decision_path)
    candidate_link = _require_dict(
        decision.get("candidate_summaries_artifact"), "candidate artifact provenance"
    )
    candidate_path = _verified_link(candidate_link, "Task 016 candidate artifact")
    candidates_raw = _read_json(candidate_path, "Task 016 candidate artifact")
    if not isinstance(candidates_raw, list) or not all(
        isinstance(row, dict) for row in candidates_raw
    ):
        raise DecisionReliabilityError("Task 016 candidates must be a JSON array of objects")
    if len(candidates_raw) != decision.get("legal_transfer_candidate_count"):
        raise DecisionReliabilityError("Task 016 candidate count does not reconcile")

    projection_info = _require_dict(
        decision.get("projection_provenance"), "projection provenance"
    )
    projection_path = _verified_link(
        {
            "path": projection_info.get("artifact_path"),
            "sha256": projection_info.get("artifact_sha256"),
        },
        "frozen projection artifact",
    )
    purchase_info = _require_dict(
        decision.get("purchase_price_provenance"), "purchase-price provenance"
    )
    players_path = _verified_link(
        {
            "path": purchase_info.get("players_artifact_path"),
            "sha256": purchase_info.get("players_artifact_sha256"),
        },
        "frozen player artifact",
    )
    projections = XfpV01ParquetProvider(
        projection_artifact=projection_path,
        players_artifact=players_path,
    ).load(
        season=str(decision.get("season")),
        target_gameweek=_integer(decision.get("target_gameweek"), "target gameweek"),
    )
    feature_path = feature_artifact.resolve()
    if not feature_path.is_file():
        raise DecisionReliabilityError(
            f"frozen feature artifact does not exist: {feature_path}"
        )
    feature_hash = sha256_file(feature_path)
    prediction_rows, expected_feature_hash = _load_prediction_rows(projection_path)
    if feature_hash != expected_feature_hash:
        raise DecisionReliabilityError(
            f"feature hash mismatch: prediction expects {expected_feature_hash}, observed {feature_hash}"
        )
    feature_rows = _load_feature_rows(feature_path)
    projected_by_id = {player.fpl_player_id: player for player in projections.players}
    if set(projected_by_id) != set(prediction_rows):
        raise DecisionReliabilityError("projection provider and prediction provenance IDs differ")
    missing_feature = sorted(set(projected_by_id) - set(feature_rows))
    if missing_feature:
        raise DecisionReliabilityError(
            "required frozen feature provenance is missing for player IDs: "
            + ", ".join(map(str, missing_feature))
        )

    reliability: dict[int, PlayerReliability] = {}
    for player_id, projected in projected_by_id.items():
        (
            projection,
            expected_minutes,
            appearance_xfp,
            prior_minutes,
            xg_per_90,
            xa_per_90,
            low_sample,
            prediction_complete,
        ) = prediction_rows[player_id]
        (
            feature_minutes,
            appearances,
            starts,
            cumulative_xg,
            cumulative_xa,
            availability_status,
            chance,
            news,
        ) = feature_rows[player_id]
        numeric_minutes = _float(feature_minutes, f"player {player_id} prior total minutes")
        if prior_minutes is None or not math.isclose(
            numeric_minutes,
            float(prior_minutes),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise DecisionReliabilityError(
                f"player {player_id} prior minutes do not reconcile between feature and prediction"
            )
        numeric_projection = _float(projection, f"player {player_id} projection", nullable=True)
        if numeric_projection != projected.projection:
            raise DecisionReliabilityError(f"player {player_id} projection does not reconcile")
        reliability[player_id] = PlayerReliability(
            fpl_player_id=player_id,
            player_name=projected.player_name,
            position_id=projected.position_id,
            position=projected.position,
            projection=numeric_projection,
            projection_state=projected.projection_state.value,
            prior_total_minutes=numeric_minutes,
            prior_appearances=_integer(appearances, f"player {player_id} prior appearances"),
            prior_starts=_integer(starts, f"player {player_id} prior starts"),
            cumulative_prior_xg=_float(
                cumulative_xg, f"player {player_id} cumulative xG", nullable=True
            ),
            cumulative_prior_xa=_float(
                cumulative_xa, f"player {player_id} cumulative xA", nullable=True
            ),
            prior_xg_per_90=_float(
                xg_per_90, f"player {player_id} xG/90", nullable=True
            ),
            prior_xa_per_90=_float(
                xa_per_90, f"player {player_id} xA/90", nullable=True
            ),
            low_sample=bool(low_sample),
            prediction_complete=bool(prediction_complete),
            expected_minutes=_float(
                expected_minutes,
                f"player {player_id} expected minutes",
                nullable=True,
            ),
            appearance_xfp=_float(
                appearance_xfp,
                f"player {player_id} appearance xFP",
                nullable=True,
            ),
            availability_status=(
                str(availability_status)
                if availability_status is not None
                else None
            ),
            chance_of_playing_next_round=(int(chance) if chance is not None else None),
            availability_news=(str(news) if news is not None else None),
        )

    manual = _require_dict(decision.get("manual_state"), "manual-state provenance")
    _verified_link(
        {"path": manual.get("artifact_path"), "sha256": manual.get("artifact_sha256")},
        "manual manager-state artifact",
    )
    reference = build_rate_reference(reliability)
    return ReliabilityContext(
        decision_path=decision_path,
        decision_sha256=decision_hash,
        candidate_path=candidate_path,
        candidate_sha256=sha256_file(candidate_path),
        feature_path=feature_path,
        feature_sha256=feature_hash,
        decision=decision,
        candidates=tuple(candidates_raw),
        projections=projections,
        players=reliability,
        reference=reference,
    )


def _formation_counts() -> tuple[tuple[int, int, int], ...]:
    return tuple(
        (defenders, midfielders, 10 - defenders - midfielders)
        for defenders in range(3, 6)
        for midfielders in range(2, 6)
        if 1 <= 10 - defenders - midfielders <= 3
    )


def _fixed_squad_diagnostic(
    squad_ids: Iterable[int],
    players: Mapping[int, PlayerReliability],
    projections: Mapping[int, float | None],
    *,
    include_captain: bool,
) -> FixedSquadDiagnostic:
    ids = tuple(sorted(int(player_id) for player_id in squad_ids))
    if len(ids) != 15 or len(set(ids)) != 15:
        raise DecisionReliabilityError("a diagnostic squad must contain 15 unique players")
    by_position: dict[str, tuple[int, ...]] = {
        position: tuple(player_id for player_id in ids if players[player_id].position == position)
        for position in GOAL_POINTS
    }
    if {position: len(rows) for position, rows in by_position.items()} != SQUAD_POSITION_COUNTS:
        raise DecisionReliabilityError("diagnostic squad position counts are invalid")
    if any(projections.get(player_id) is None for player_id in ids):
        raise DecisionReliabilityError("diagnostic squad contains a missing projection")
    ranked = {
        position: tuple(
            sorted(
                rows,
                key=lambda player_id: (-float(projections[player_id]), player_id),
            )
        )
        for position, rows in by_position.items()
    }
    best: tuple[float, tuple[int, ...], tuple[int, ...]] | None = None
    for defenders, midfielders, forwards in _formation_counts():
        # Every legal XI contains at least one player from every position, so
        # the squad's highest projection is always selected. For a fixed
        # formation, taking the highest projections within each position is
        # therefore exactly equivalent to Task 014's exhaustive XI objective.
        xi = tuple(sorted(
            ranked["GK"][:1]
            + ranked["DEF"][:defenders]
            + ranked["MID"][:midfielders]
            + ranked["FWD"][:forwards]
        ))
        base = sum(float(projections[player_id]) for player_id in xi)
        captain_projection = max(float(projections[player_id]) for player_id in xi)
        objective = base + (captain_projection if include_captain else 0.0)
        candidate = (objective, xi, (defenders, midfielders, forwards))
        if (
            best is None
            or objective > best[0] + OBJECTIVE_TOLERANCE
            or (
                math.isclose(objective, best[0], rel_tol=0.0, abs_tol=OBJECTIVE_TOLERANCE)
                and xi < best[1]
            )
        ):
            best = candidate
    if best is None:
        raise DecisionReliabilityError("no legal diagnostic XI exists")
    _, xi, formation_counts = best
    captain_order = sorted(xi, key=lambda player_id: (-float(projections[player_id]), player_id))
    captain_id = captain_order[0] if include_captain else None
    vice_id = captain_order[1] if include_captain else None
    base = sum(float(projections[player_id]) for player_id in xi)
    bonus = float(projections[captain_id]) if captain_id is not None else 0.0
    return FixedSquadDiagnostic(
        starting_xi_ids=xi,
        formation="-".join(map(str, formation_counts)),
        captain_id=captain_id,
        vice_captain_id=vice_id,
        base_xi_projection=base,
        captain_bonus=bonus,
        total_objective=base + bonus,
    )


def _squad_ids(current_ids: tuple[int, ...], candidate: Mapping[str, Any]) -> tuple[int, ...]:
    outgoing = _integer(candidate["out"]["element_id"], "candidate outgoing ID")
    incoming = _integer(candidate["in"]["element_id"], "candidate incoming ID")
    if outgoing not in current_ids or incoming in current_ids:
        raise DecisionReliabilityError("candidate does not represent exactly one transfer")
    return tuple(player_id for player_id in current_ids if player_id != outgoing) + (incoming,)


def _validate_official_surface(context: ReliabilityContext) -> tuple[int, ...]:
    decision = context.decision
    roll = _require_dict(decision.get("roll"), "Task 016 ROLL result")
    squad = roll.get("squad")
    if not isinstance(squad, list):
        raise DecisionReliabilityError("Task 016 ROLL squad is missing")
    current_ids = tuple(_integer(row.get("element_id"), "ROLL squad player ID") for row in squad)
    required_ids = set(current_ids)
    for candidate in context.candidates:
        required_ids.add(_integer(candidate["out"]["element_id"], "candidate outgoing ID"))
        required_ids.add(_integer(candidate["in"]["element_id"], "candidate incoming ID"))
    missing = sorted(required_ids - set(context.players))
    if missing:
        raise DecisionReliabilityError(
            "required reliability provenance is missing for player IDs: "
            + ", ".join(map(str, missing))
        )
    original = {player_id: player.projection for player_id, player in context.players.items()}
    computed_roll = _fixed_squad_diagnostic(
        current_ids, context.players, original, include_captain=True
    )
    _require_result_match(computed_roll, roll, "ROLL")
    for index, candidate in enumerate(context.candidates, 1):
        computed = _fixed_squad_diagnostic(
            _squad_ids(current_ids, candidate),
            context.players,
            original,
            include_captain=True,
        )
        _require_result_match(computed, candidate, f"candidate {index}")
    return current_ids


def _require_result_match(
    computed: FixedSquadDiagnostic, persisted: Mapping[str, Any], label: str
) -> None:
    comparisons = {
        "formation": (computed.formation, persisted.get("formation")),
        "captain_id": (computed.captain_id, persisted.get("captain_id") or persisted.get("captain", {}).get("element_id")),
        "vice_captain_id": (computed.vice_captain_id, persisted.get("vice_captain_id") or persisted.get("vice_captain", {}).get("element_id")),
    }
    for field, (actual, expected) in comparisons.items():
        if actual != expected:
            raise DecisionReliabilityError(f"{label} {field} does not reproduce Task 016")
    for field, actual in (
        ("base_xi_projection", computed.base_xi_projection),
        ("captain_bonus", computed.captain_bonus),
        ("total_objective", computed.total_objective),
    ):
        expected = _float(persisted.get(field), f"{label} {field}")
        if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=OBJECTIVE_TOLERANCE):
            raise DecisionReliabilityError(f"{label} {field} does not reproduce Task 016")


def _action_payload(
    candidate: Mapping[str, Any] | None,
    *,
    objective: float,
    roll_objective: float,
) -> dict[str, Any]:
    if candidate is None or objective <= roll_objective + OBJECTIVE_TOLERANCE:
        return {
            "action": ROLL,
            "outgoing": None,
            "incoming": None,
            "objective": roll_objective,
            "gain_vs_roll": 0.0,
        }
    return {
        "action": TRANSFER,
        "outgoing": {
            "element_id": candidate["out"]["element_id"],
            "name": candidate["out"]["name"],
        },
        "incoming": {
            "element_id": candidate["in"]["element_id"],
            "name": candidate["in"]["name"],
        },
        "objective": objective,
        "gain_vs_roll": objective - roll_objective,
    }


def _rank_persisted_candidates(
    candidates: Iterable[dict[str, Any]], roll_objective: float
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ordered = sorted(
        candidates,
        key=lambda row: (
            -float(row["total_objective"]),
            int(row["out"]["element_id"]),
            int(row["in"]["element_id"]),
        ),
    )
    best = ordered[0] if ordered else None
    action = _action_payload(
        best,
        objective=(float(best["total_objective"]) if best is not None else roll_objective),
        roll_objective=roll_objective,
    )
    return action, [
        _action_payload(row, objective=float(row["total_objective"]), roll_objective=roll_objective)
        for row in ordered[:3]
    ]


def _diagnostic_view(
    *,
    view_id: str,
    category: str,
    assumptions: list[str],
    candidate_count: int,
    action: dict[str, Any],
    top_actions: list[dict[str, Any]],
    roll_objective: float,
    comparison_semantics: Mapping[str, Any],
    transformation_audit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "view_id": view_id,
        "diagnostic_only": True,
        "category": category,
        "assumptions": assumptions,
        "comparison_semantics": dict(comparison_semantics),
        "eligible_transfer_candidate_count": candidate_count,
        "roll_objective": roll_objective,
        "recommended_action_under_view": action,
        "top_actions": top_actions,
    }
    if transformation_audit is not None:
        payload["transformation_audit"] = dict(transformation_audit)
    return payload


def build_rate_capped_projections(
    players: Mapping[int, PlayerReliability],
    reference: Mapping[str, Mapping[str, Any]],
    percentile_label: str,
) -> tuple[dict[int, float | None], tuple[int, ...]]:
    """Apply one diagnostic rate cap to every complete numeric projection."""
    transformed: dict[int, float | None] = {}
    changed: list[int] = []
    for player_id, player in players.items():
        if player.projection_state != ProjectionState.VALID.value:
            transformed[player_id] = player.projection
            continue
        if (
            player.appearance_xfp is None
            or player.expected_minutes is None
            or player.prior_xg_per_90 is None
            or player.prior_xa_per_90 is None
        ):
            raise DecisionReliabilityError(
                f"complete player {player_id} lacks components required for rate-cap diagnostic"
            )
        xg = _defined_finite_rate(player, "prior_xg_per_90")
        xa = _defined_finite_rate(player, "prior_xa_per_90")
        if xg is None or xa is None:
            raise DecisionReliabilityError(
                f"complete player {player_id} has an undefined rate-cap input"
            )
        xg_limit = float(
            reference[player.position]["prior_xg_per_90"][percentile_label]
        )
        xa_limit = float(
            reference[player.position]["prior_xa_per_90"][percentile_label]
        )
        value = (
            player.appearance_xfp
            + min(xg, xg_limit)
            * player.expected_minutes
            / 90.0
            * GOAL_POINTS[player.position]
            + min(xa, xa_limit)
            * player.expected_minutes
            / 90.0
            * 3.0
        )
        transformed[player_id] = value
        if player.projection is None or not math.isclose(
            value,
            player.projection,
            rel_tol=0.0,
            abs_tol=OBJECTIVE_TOLERANCE,
        ):
            changed.append(player_id)
    return transformed, tuple(sorted(changed))


def build_sensitivity_views(
    context: ReliabilityContext, current_ids: tuple[int, ...]
) -> list[dict[str, Any]]:
    """Calculate declared diagnostics without changing persisted xFP or Task 016."""
    roll_objective = float(context.decision["comparison"]["roll_objective"])
    views: list[dict[str, Any]] = []
    for threshold in MINIMUM_PRIOR_MINUTES:
        filtered = [
            candidate
            for candidate in context.candidates
            if context.players[int(candidate["in"]["element_id"])].prior_total_minutes
            >= threshold
        ]
        action, top = _rank_persisted_candidates(filtered, roll_objective)
        views.append(_diagnostic_view(
            view_id=f"minimum_prior_minutes_{threshold}",
            category="minimum_prior_minutes",
            assumptions=[
                f"newly acquired player must have prior_total_minutes >= {threshold}",
                "this is an incoming-acquisition screen, not a projection transform",
                "owned players are grandfathered and remain eligible in ROLL and transfer squads",
                "persisted xFP and official candidate objectives are unchanged",
            ],
            candidate_count=len(filtered),
            action=action,
            top_actions=top,
            roll_objective=roll_objective,
            comparison_semantics={
                "kind": "incoming_transfer_acquisition_screen",
                "incoming_treatment": f"require prior_total_minutes >= {threshold}",
                "owned_player_treatment": "grandfathered; projections unchanged",
                "roll_treatment": "unchanged Task 016 ROLL objective",
                "projection_transformation": "none",
            },
        ))

    for quantile in REFERENCE_QUANTILES:
        label = f"p{int(quantile * 100)}"
        filtered: list[dict[str, Any]] = []
        for candidate in context.candidates:
            incoming = context.players[int(candidate["in"]["element_id"])]
            xg_limit = context.reference[incoming.position]["prior_xg_per_90"][label]
            xa_limit = context.reference[incoming.position]["prior_xa_per_90"][label]
            xg_extreme = incoming.prior_xg_per_90 is not None and incoming.prior_xg_per_90 > xg_limit
            xa_extreme = incoming.prior_xa_per_90 is not None and incoming.prior_xa_per_90 > xa_limit
            if not (xg_extreme or xa_extreme):
                filtered.append(candidate)
        action, top = _rank_persisted_candidates(filtered, roll_objective)
        views.append(_diagnostic_view(
            view_id=f"exclude_rates_above_position_{label}",
            category="descriptive_rate_exclusion",
            assumptions=[
                f"exclude newly acquired players whose xG/90 or xA/90 is above their position-specific {label.upper()}",
                "this is an incoming-acquisition screen, not a projection transform",
                "owned players are grandfathered and remain eligible in ROLL and transfer squads",
                "reference population is complete predictions from the same frozen player-GW artifact",
                "persisted xFP is not altered for retained candidates",
            ],
            candidate_count=len(filtered),
            action=action,
            top_actions=top,
            roll_objective=roll_objective,
            comparison_semantics={
                "kind": "incoming_transfer_acquisition_screen",
                "incoming_treatment": f"exclude rates strictly above positional {label.upper()}",
                "owned_player_treatment": "grandfathered; projections unchanged",
                "roll_treatment": "unchanged Task 016 ROLL objective",
                "projection_transformation": "none",
            },
        ))

    original = {player_id: player.projection for player_id, player in context.players.items()}
    for quantile in REFERENCE_QUANTILES:
        label = f"p{int(quantile * 100)}"
        capped, changed = build_rate_capped_projections(
            context.players, context.reference, label
        )
        roll_result = _fixed_squad_diagnostic(
            current_ids, context.players, capped, include_captain=True
        )
        ranked: list[tuple[FixedSquadDiagnostic, dict[str, Any]]] = []
        for candidate in context.candidates:
            result = _fixed_squad_diagnostic(
                _squad_ids(current_ids, candidate),
                context.players,
                capped,
                include_captain=True,
            )
            ranked.append((result, candidate))
        ranked.sort(key=lambda row: (
            -row[0].total_objective,
            int(row[1]["out"]["element_id"]),
            int(row[1]["in"]["element_id"]),
        ))
        best_result, best_candidate = ranked[0]
        action = _action_payload(
            best_candidate,
            objective=best_result.total_objective,
            roll_objective=roll_result.total_objective,
        )
        top = [
            _action_payload(
                candidate,
                objective=result.total_objective,
                roll_objective=roll_result.total_objective,
            )
            for result, candidate in ranked[:3]
        ]
        views.append(_diagnostic_view(
            view_id=f"cap_rates_at_position_{label}",
            category="descriptive_rate_cap",
            assumptions=[
                f"cap every valid player's xG/90 and xA/90 at their position-specific {label.upper()}",
                "recalculate diagnostic appearance+goal+assist xFP in memory only",
                "apply the same cap to ROLL and every legal transfer squad",
                "stored prediction and Task 016 artifacts remain unchanged",
            ],
            candidate_count=len(ranked),
            action=action,
            top_actions=top,
            roll_objective=roll_result.total_objective,
            comparison_semantics={
                "kind": "symmetric_projection_transform",
                "incoming_treatment": f"rates capped at positional {label.upper()}",
                "owned_player_treatment": f"rates capped at positional {label.upper()}",
                "roll_treatment": "re-optimized from the transformed owned squad",
                "projection_transformation": "same cap applied before every ROLL and transfer objective",
            },
            transformation_audit={
                "transformed_player_ids": list(changed),
                "transformed_player_count": len(changed),
                "transformed_owned_player_ids": sorted(set(changed) & set(current_ids)),
                "transformed_incoming_player_ids": sorted(
                    set(changed)
                    & {
                        int(candidate["in"]["element_id"])
                        for candidate in context.candidates
                    }
                ),
            },
        ))

    roll_xi = _fixed_squad_diagnostic(
        current_ids, context.players, original, include_captain=False
    )
    ranked_xi: list[tuple[FixedSquadDiagnostic, dict[str, Any]]] = []
    for candidate in context.candidates:
        result = _fixed_squad_diagnostic(
            _squad_ids(current_ids, candidate),
            context.players,
            original,
            include_captain=False,
        )
        ranked_xi.append((result, candidate))
    ranked_xi.sort(key=lambda row: (
        -row[0].total_objective,
        int(row[1]["out"]["element_id"]),
        int(row[1]["in"]["element_id"]),
    ))
    best_result, best_candidate = ranked_xi[0]
    action = _action_payload(
        best_candidate,
        objective=best_result.total_objective,
        roll_objective=roll_xi.total_objective,
    )
    top = [
        _action_payload(
            candidate,
            objective=result.total_objective,
            roll_objective=roll_xi.total_objective,
        )
        for result, candidate in ranked_xi[:3]
    ]
    views.append(_diagnostic_view(
        view_id="xi_only_without_captain_amplification",
        category="captain_amplification",
        assumptions=[
            "rank fixed squads by starting-XI xFP only",
            "captain bonus is excluded from both ROLL and transfer objectives",
            "stored Task 016 objective and recommendation remain unchanged",
        ],
        candidate_count=len(ranked_xi),
        action=action,
        top_actions=top,
        roll_objective=roll_xi.total_objective,
        comparison_semantics={
            "kind": "symmetric_objective_transform",
            "incoming_treatment": "captain bonus excluded",
            "owned_player_treatment": "captain bonus excluded",
            "roll_treatment": "re-optimized under the XI-only objective",
            "projection_transformation": "none; the objective changes symmetrically",
        },
    ))
    actual_view_ids = tuple(row["view_id"] for row in views)
    if actual_view_ids != EXPECTED_SENSITIVITY_VIEW_IDS:
        raise DecisionReliabilityError(
            "diagnostic sensitivity view set or ordering is incomplete"
        )
    return views


def _action_key(action: Mapping[str, Any]) -> str:
    if action.get("action") == ROLL:
        return ROLL
    return f"TRANSFER:{action['outgoing']['element_id']}->{action['incoming']['element_id']}"


def build_stability_summary(
    official_action: Mapping[str, Any], views: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    rows = list(views)
    official_key = _action_key(official_action)
    actions = [row["recommended_action_under_view"] for row in rows]
    keys = [_action_key(action) for action in actions]
    official_incoming = (
        official_action.get("incoming", {}).get("element_id")
        if official_action.get("incoming")
        else None
    )
    same_incoming = sum(
        action.get("incoming", {}).get("element_id") == official_incoming
        if action.get("incoming")
        else official_incoming is None
        for action in actions
    )
    gains = [float(action["gain_vs_roll"]) for action in actions]
    return {
        "official_action_key": official_key,
        "diagnostic_view_count": len(rows),
        "same_exact_action_count": sum(key == official_key for key in keys),
        "different_action_count": sum(key != official_key for key in keys),
        "same_incoming_player_count": same_incoming,
        "recommended_transfer_remains_same_across_all_views": all(
            key == official_key for key in keys
        ),
        "incoming_player_remains_same_across_all_views": same_incoming == len(rows),
        "diagnostic_gain_min": min(gains) if gains else None,
        "diagnostic_gain_max": max(gains) if gains else None,
        "distinct_diagnostic_actions": sorted(set(keys)),
        "unsupported_confidence_grade_emitted": False,
    }


def _material_roles(
    context: ReliabilityContext,
    current_ids: tuple[int, ...],
) -> tuple[dict[int, set[str]], dict[str, Any]]:
    roles: dict[int, set[str]] = defaultdict(set)
    roll = context.decision["roll"]
    roll_xi = tuple(int(row["element_id"]) for row in roll["starting_xi"])
    for player_id in roll_xi:
        roles[player_id].add("roll.starting_xi")
    roll_captain = int(roll["captain"]["element_id"])
    roll_vice = int(roll["vice_captain"]["element_id"])
    roles[roll_captain].add("roll.captain")
    roles[roll_vice].add("roll.vice_captain")
    admitted_incomplete_ids = sorted(
        int(row["element_id"])
        for row in context.decision.get("admitted_incomplete_roll_squad", [])
    )
    for player_id in admitted_incomplete_ids:
        roles[player_id].add("roll.admitted_incomplete_squad")
    result: dict[str, Any] = {
        "roll": {
            "starting_xi_ids": list(roll_xi),
            "captain_id": roll_captain,
            "vice_captain_id": roll_vice,
            "admitted_incomplete_ids": admitted_incomplete_ids,
        },
        "official_recommendation": None,
        "top_transfer_alternatives": [],
    }
    best = context.decision.get("best_transfer")
    if context.decision["comparison"]["recommended_action"] == TRANSFER and best is not None:
        out_id = int(best["out"]["element_id"])
        in_id = int(best["in"]["element_id"])
        roles[out_id].add("official.outgoing")
        roles[in_id].add("official.incoming")
        optimized = best["optimized_squad"]
        xi = tuple(int(row["element_id"]) for row in optimized["starting_xi"])
        for player_id in xi:
            roles[player_id].add("official.resulting_xi")
        captain = int(optimized["captain"]["element_id"])
        vice = int(optimized["vice_captain"]["element_id"])
        roles[captain].add("official.captain")
        roles[vice].add("official.vice_captain")
        result["official_recommendation"] = {
            "outgoing_id": out_id,
            "incoming_id": in_id,
            "resulting_xi_ids": list(xi),
            "captain_id": captain,
            "vice_captain_id": vice,
        }
    original = {player_id: player.projection for player_id, player in context.players.items()}
    for rank, candidate in enumerate(context.candidates[:10], 1):
        diagnostic = _fixed_squad_diagnostic(
            _squad_ids(current_ids, candidate),
            context.players,
            original,
            include_captain=True,
        )
        out_id = int(candidate["out"]["element_id"])
        in_id = int(candidate["in"]["element_id"])
        roles[out_id].add(f"top_transfer_{rank}.outgoing")
        roles[in_id].add(f"top_transfer_{rank}.incoming")
        for player_id in diagnostic.starting_xi_ids:
            roles[player_id].add(f"top_transfer_{rank}.resulting_xi")
        roles[int(diagnostic.captain_id)].add(f"top_transfer_{rank}.captain")
        roles[int(diagnostic.vice_captain_id)].add(f"top_transfer_{rank}.vice_captain")
        result["top_transfer_alternatives"].append({
            "rank": rank,
            "outgoing_id": out_id,
            "incoming_id": in_id,
            "resulting_xi_ids": list(diagnostic.starting_xi_ids),
            "captain_id": diagnostic.captain_id,
            "vice_captain_id": diagnostic.vice_captain_id,
            "official_total_objective": candidate["total_objective"],
        })
    return roles, result


def _official_action(context: ReliabilityContext) -> dict[str, Any]:
    comparison = context.decision["comparison"]
    if comparison["recommended_action"] == ROLL:
        return _action_payload(
            None,
            objective=float(comparison["roll_objective"]),
            roll_objective=float(comparison["roll_objective"]),
        )
    best = context.decision["best_transfer"]
    return _action_payload(
        best,
        objective=float(comparison["best_transfer_objective"]),
        roll_objective=float(comparison["roll_objective"]),
    )


def _low_sample_context(
    context: ReliabilityContext, incoming: PlayerReliability | None
) -> dict[str, Any]:
    low_count = sum(player.low_sample for player in context.players.values())
    total = len(context.players)
    universal = total > 0 and low_count == total
    if universal and incoming is not None:
        wording = (
            f"low_sample is universal in this GW{context.decision['target_gameweek']} "
            f"projection universe ({low_count}/{total} players) and is therefore not "
            f"a player-specific discriminator; {incoming.player_name}'s distinctive "
            "reliability concern is the extremity of his one-match attacking rates."
        )
    elif universal:
        wording = (
            f"low_sample is universal in this GW{context.decision['target_gameweek']} "
            f"projection universe ({low_count}/{total} players) and is therefore not "
            "a player-specific discriminator."
        )
    else:
        wording = (
            f"low_sample applies to {low_count}/{total} players in the frozen projection "
            "universe and may distinguish player histories; interpret it alongside the "
            "underlying minutes, appearances, and attacking rates."
        )
    return {
        "low_sample_count": low_count,
        "projection_universe_count": total,
        "universal": universal,
        "player_specific_discriminator": not universal,
        "persisted_interpretation": wording,
    }


def _warnings(
    context: ReliabilityContext,
    official: Mapping[str, Any],
    views: list[dict[str, Any]],
    material_ids: set[int],
    low_sample_context: Mapping[str, Any],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    incoming = (
        context.players[int(official["incoming"]["element_id"])]
        if official.get("incoming")
        else None
    )
    if (
        incoming is not None
        and incoming.low_sample
        and not low_sample_context["universal"]
    ):
        warnings.append({
            "code": "recommendation_depends_on_low_sample_projection",
            "facts": {"element_id": incoming.fpl_player_id, "low_sample": True},
            "message": "The official incoming player is flagged low_sample in the frozen prediction.",
        })
    if incoming is not None and incoming.prior_appearances == 1:
        warnings.append({
            "code": "incoming_has_one_prior_appearance",
            "facts": {"element_id": incoming.fpl_player_id, "prior_appearances": 1},
            "message": "The incoming projection is based on one prior appearance.",
        })
    if incoming is not None:
        xg = _rate_diagnostic(incoming, "prior_xg_per_90", context.players, context.reference)
        xa = _rate_diagnostic(incoming, "prior_xa_per_90", context.players, context.reference)
        if xg["above_position_p95"] or xa["above_position_p95"]:
            warnings.append({
                "code": "incoming_attacking_rate_in_extreme_descriptive_tail",
                "facts": {
                    "element_id": incoming.fpl_player_id,
                    "xg_per_90_above_position_p95": xg["above_position_p95"],
                    "xa_per_90_above_position_p95": xa["above_position_p95"],
                },
                "message": "At least one incoming attacking rate is above its position-specific P95.",
            })
    xi_view = next(row for row in views if row["view_id"] == "xi_only_without_captain_amplification")
    amplification = float(official["gain_vs_roll"]) - float(
        xi_view["recommended_action_under_view"]["gain_vs_roll"]
    )
    if amplification > OBJECTIVE_TOLERANCE:
        warnings.append({
            "code": "captaincy_amplifies_recommendation",
            "facts": {"gain_with_captain": official["gain_vs_roll"], "gain_without_captain": xi_view["recommended_action_under_view"]["gain_vs_roll"], "amplification": amplification},
            "message": "Captaincy increases the official transfer gain relative to the XI-only view.",
        })
    material_incomplete = sorted(
        player_id
        for player_id in material_ids
        if context.players[player_id].projection_state == ProjectionState.INCOMPLETE.value
    )
    admitted_incomplete = sorted(
        int(row["element_id"])
        for row in context.decision.get("admitted_incomplete_roll_squad", [])
    )
    invalid_admitted = [
        player_id
        for player_id in admitted_incomplete
        if player_id not in context.players
        or context.players[player_id].projection_state
        != ProjectionState.INCOMPLETE.value
        or context.players[player_id].prediction_complete
    ]
    if invalid_admitted:
        raise DecisionReliabilityError(
            "Task 016 admitted-incomplete provenance does not reconcile for IDs: "
            + ", ".join(map(str, invalid_admitted))
        )
    if admitted_incomplete:
        warnings.append({
            "code": "appearance_only_policy_admits_incomplete_projections",
            "facts": {
                "element_ids": admitted_incomplete,
                "count": len(admitted_incomplete),
                "material_element_ids": material_incomplete,
                "material_count": len(material_incomplete),
            },
            "message": (
                "The ROLL squad contains numeric incomplete projections admitted by "
                "appearance_only_allowed; the material subset identifies those also "
                "involved in the reported decision surface."
            ),
        })
    low_count = int(low_sample_context["low_sample_count"])
    total = int(low_sample_context["projection_universe_count"])
    if low_sample_context["universal"]:
        warnings.append({
            "code": "early_season_low_sample_is_universal",
            "facts": {
                "low_sample_count": low_count,
                "projection_universe_count": total,
                "player_specific_discriminator": False,
                "incoming_element_id": (
                    incoming.fpl_player_id if incoming is not None else None
                ),
            },
            "message": low_sample_context["persisted_interpretation"],
        })
    elif low_count > total / 2:
        warnings.append({
            "code": "early_season_low_sample_is_predominant",
            "facts": {"low_sample_count": low_count, "projection_universe_count": total},
            "message": "A majority of the frozen projection universe is flagged low_sample.",
        })
    return warnings


def analyze_decision_reliability(
    context: ReliabilityContext,
    *,
    generation_timestamp: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic reliability report around an unchanged decision."""
    current_ids = _validate_official_surface(context)
    official = _official_action(context)
    views = build_sensitivity_views(context, current_ids)
    stability = build_stability_summary(official, views)
    roles, role_index = _material_roles(context, current_ids)
    missing_material = sorted(set(roles) - set(context.players))
    if missing_material:
        raise DecisionReliabilityError(
            "required material-player provenance is missing for IDs: "
            + ", ".join(map(str, missing_material))
        )
    registry = [
        player_reliability_payload(
            context.players[player_id],
            roles=player_roles,
            players=context.players,
            reference=context.reference,
        )
        for player_id, player_roles in sorted(roles.items())
    ]
    incoming = (
        context.players[int(official["incoming"]["element_id"])]
        if official.get("incoming")
        else None
    )
    low_sample_context = _low_sample_context(context, incoming)
    warnings = _warnings(
        context,
        official,
        views,
        set(roles),
        low_sample_context,
    )
    return {
        "version": DECISION_RELIABILITY_VERSION,
        "classification": DECISION_RELIABILITY_CLASSIFICATION,
        "diagnostic_only": True,
        "generation_timestamp": generation_timestamp,
        "season": context.decision["season"],
        "target_gameweek": context.decision["target_gameweek"],
        "entry_id": context.decision["entry_id"],
        "decision_policy": context.decision["decision_policy"],
        "official_recommendation_unchanged": True,
        "official_recommendation": official,
        "reference_population": {
            "definition": (
                "For each metric independently: prediction_complete=true rows from "
                "the same frozen player-GW artifact, grouped by frozen FPL position, "
                "with that metric's rate genuinely non-null and finite. Undefined "
                "rates are excluded, never coerced to zero."
            ),
            "metric_populations_are_independent": True,
            "incomplete_rows_are_numeric_members": False,
            "undefined_rates_coerced_to_zero": False,
            "quantile_method": "linear interpolation at (n-1)*q",
            "extreme_rate_definition": "strictly above position-specific P95; diagnostic only",
            "positions": context.reference,
        },
        "low_sample_context": low_sample_context,
        "decision_roles": role_index,
        "material_player_reliability": registry,
        "warnings": warnings,
        "diagnostic_sensitivity": views,
        "stability_summary": {
            **stability,
            "captaincy_materially_changes_gain": any(
                row["code"] == "captaincy_amplifies_recommendation" for row in warnings
            ),
        },
        "human_summary": {
            "model_result": (
                f"Frozen xFP v0.1 official action remains {_action_key(official)} "
                f"with gain {official['gain_vs_roll']:.2f}."
            ),
            "reliability_evidence": (
                f"{low_sample_context['persisted_interpretation']} Incoming prior "
                f"appearances={incoming.prior_appearances}, prior minutes="
                f"{incoming.prior_total_minutes:.0f}; {stability['different_action_count']} "
                f"of {stability['diagnostic_view_count']} declared diagnostic views "
                "select a different action."
                if incoming is not None
                else "The official action is ROLL; reliability metadata for the current XI is preserved."
            ),
            "interpretation": (
                "The official optimization is unchanged. Descriptive diagnostics show recommendation instability; "
                "this report does not assert that any transfer is definitively good or bad."
                if stability["different_action_count"]
                else "The official optimization is unchanged and all declared descriptive views retain the same action; no confidence grade is inferred."
            ),
        },
        "provenance": {
            "task_016_decision_artifact": {"path": str(context.decision_path), "sha256": context.decision_sha256},
            "task_016_candidate_artifact": {"path": str(context.candidate_path), "sha256": context.candidate_sha256},
            "candidate_universe": {
                "source": "exact Task 016 legality-checked candidate artifact",
                "candidate_count": len(context.candidates),
                "ordered_rows_consumed_directly": True,
                "transfer_legality_rebuilt_by_task_017": False,
            },
            "frozen_projection_artifact": {"path": context.projections.source_artifact_path, "sha256": context.projections.source_artifact_sha256},
            "frozen_feature_artifact": {"path": str(context.feature_path), "sha256": context.feature_sha256},
            "frozen_players_artifact": {"path": context.projections.players_artifact_path, "sha256": context.projections.players_artifact_sha256},
            "manual_state": context.decision["manual_state"],
            "selling_price_inputs": context.decision["selling_price_inputs"],
        },
        "guardrails": {
            "xfp_v01_modified": False,
            "task_016_objective_modified": False,
            "task_016_recommendation_modified": False,
            "automatic_transfer_veto": False,
            "unsupported_confidence_grade_emitted": False,
        },
    }


def _filesystem_timestamp(value: datetime) -> tuple[str, str]:
    if value.tzinfo is None:
        raise DecisionReliabilityError("generation timestamp must be timezone-aware")
    utc = value.astimezone(timezone.utc)
    return (
        utc.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        utc.strftime("%Y%m%dT%H%M%S.%fZ"),
    )


def _write_json(path: Path, payload: Any) -> None:
    with path.open("x", encoding="utf-8") as output:
        json.dump(payload, output, indent=2, sort_keys=True, allow_nan=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def write_decision_reliability(
    context: ReliabilityContext,
    *,
    generated_at: datetime | None = None,
) -> DecisionReliabilityArtifacts:
    """Write a new immutable child artifact without altering Task 016 files."""
    generated_iso, timestamp = _filesystem_timestamp(generated_at or datetime.now(timezone.utc))
    directory = context.decision_path.parent / DECISION_RELIABILITY_VERSION / timestamp
    if directory.exists():
        raise DecisionReliabilityOutputExistsError(
            f"decision reliability output already exists: {directory}"
        )
    directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = directory.parent / f".{timestamp}.{uuid.uuid4().hex}.tmp"
    temporary.mkdir()
    try:
        path = temporary / "decision_reliability.json"
        _write_json(path, analyze_decision_reliability(context, generation_timestamp=generated_iso))
        digest = sha256_file(path)
        temporary.rename(directory)
    except Exception:
        if temporary.exists():
            for child in temporary.iterdir():
                child.unlink()
            temporary.rmdir()
        raise
    final_path = directory / "decision_reliability.json"
    return DecisionReliabilityArtifacts(
        directory=directory,
        reliability_path=final_path,
        reliability_sha256=digest,
    )
