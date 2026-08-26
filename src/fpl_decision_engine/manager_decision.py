"""Evaluate a locked public manager squad with the Task 014 decision layer."""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .decision import (
    DECISION_ENGINE_VERSION,
    DecisionError,
    DecisionResult,
    optimize_xi,
    resolve_existing_squad,
)
from .manager_state import (
    FRESHNESS_WARNING,
    POST_DEADLINE_WARNING,
    TRANSFER_RECOMMENDATION_STATUS,
    PublicManagerState,
)
from .projection_provider import ProjectionDataset, ProjectionPlayer


CURRENT_SQUAD_DECISION_VERSION = "current-squad-decision-v1"
CURRENT_SQUAD_CLASSIFICATION = (
    "experimental_current_squad_decision_using_xfp_v01_modeled_components_only"
)
UNCONSTRAINED_BENCHMARK_CLASSIFICATION = (
    "informational_unconstrained_projection_benchmark"
)
DIFFERENCE_CLASSIFICATION = "modeled_component_projection_difference"
TRANSFER_NOT_PERFORMED = "Transfer optimization not performed."
NONCOMPARABLE_SCORING_CHIPS = {"3xc", "bboost"}


class ManagerDecisionError(Exception):
    """Raised for manager/projection alignment or decision-output failures."""


class ManagerDecisionOutputExistsError(ManagerDecisionError):
    """Raised rather than overwriting a manager decision."""


@dataclass(frozen=True)
class SelectionScore:
    base_xi_projection: float
    captain_bonus: float
    total_objective: float


@dataclass(frozen=True)
class ManagerDecisionResult:
    manager_state: PublicManagerState
    projection_dataset: ProjectionDataset
    reconciliation: tuple[dict[str, Any], ...]
    reconciliation_status: str
    incomplete_owned_player_ids: tuple[int, ...]
    missing_owned_player_ids: tuple[int, ...]
    unresolved_projection_player_ids: tuple[int, ...]
    manager_score: SelectionScore | None
    optimized_result: DecisionResult | None
    manager_formation: str
    optimized_formation: str | None
    xi_started_ids: tuple[int, ...]
    xi_benched_ids: tuple[int, ...]
    bench_added_ids: tuple[int, ...]
    bench_removed_ids: tuple[int, ...]
    captain_changed: bool | None
    vice_captain_changed: bool | None
    change_list: tuple[str, ...]
    modeled_component_projection_difference: float | None
    comparison_status: str
    chip_limitation: str | None
    unconstrained_benchmark: DecisionResult | None


@dataclass(frozen=True)
class ManagerDecisionArtifacts:
    directory: Path
    manifest_path: Path
    manifest_sha256: str


def _projection(player: ProjectionPlayer) -> float:
    if not player.eligible or player.projection is None or not math.isfinite(player.projection):
        raise ManagerDecisionError(
            f"owned player {player.fpl_player_id} has no eligible numeric projection"
        )
    return player.projection


def _formation(players: list[ProjectionPlayer]) -> str:
    counts = Counter(player.position for player in players)
    return f"{counts['DEF']}-{counts['MID']}-{counts['FWD']}"


def _score_locked_selection(
    state: PublicManagerState, by_id: dict[int, ProjectionPlayer]
) -> SelectionScore | None:
    if any(player_id not in by_id or not by_id[player_id].eligible for player_id in state.manager_xi):
        return None
    captain = by_id.get(state.manager_captain)
    if captain is None or not captain.eligible:
        return None
    base = sum(_projection(by_id[player_id]) for player_id in state.manager_xi)
    captain_bonus = _projection(captain)
    return SelectionScore(base, captain_bonus, base + captain_bonus)


def _change_list(
    *,
    started: tuple[int, ...],
    benched: tuple[int, ...],
    by_id: dict[int, ProjectionPlayer],
    old_captain: int,
    new_captain: int,
    old_vice: int,
    new_vice: int,
) -> tuple[str, ...]:
    changes: list[str] = []
    entering = list(started)
    leaving = list(benched)
    # Prefer auditable same-position swaps, then pair any formation-changing remainder.
    for position in ("GK", "DEF", "MID", "FWD"):
        position_in = sorted(
            (player_id for player_id in entering if by_id[player_id].position == position)
        )
        position_out = sorted(
            (player_id for player_id in leaving if by_id[player_id].position == position)
        )
        for player_in, player_out in zip(position_in, position_out):
            changes.append(
                f"START {by_id[player_in].player_name} instead of {by_id[player_out].player_name}"
            )
            entering.remove(player_in)
            leaving.remove(player_out)
    for player_in, player_out in zip(sorted(entering), sorted(leaving)):
        changes.append(
            f"START {by_id[player_in].player_name} instead of {by_id[player_out].player_name}"
        )
    if new_captain != old_captain:
        changes.append(
            f"CAPTAIN {by_id[new_captain].player_name} instead of {by_id[old_captain].player_name}"
        )
    if new_vice != old_vice:
        changes.append(
            f"VICE-CAPTAIN {by_id[new_vice].player_name} instead of {by_id[old_vice].player_name}"
        )
    return tuple(changes)


def evaluate_current_squad(
    state: PublicManagerState,
    projections: ProjectionDataset,
    *,
    unconstrained_benchmark: DecisionResult | None = None,
) -> ManagerDecisionResult:
    """Reconcile and optimize only the 15 players in one locked public squad."""
    if state.season != projections.season:
        raise ManagerDecisionError(
            f"manager season {state.season} does not match projection season {projections.season}"
        )
    if state.represented_event != projections.target_gameweek:
        raise ManagerDecisionError(
            "manager-state event/projection target mismatch: "
            f"event {state.represented_event} versus target GW{projections.target_gameweek}"
        )
    owned_ids = tuple(row.element_id for row in state.picks)
    if len(owned_ids) != 15 or len(set(owned_ids)) != 15:
        raise ManagerDecisionError("manager state must contain exactly 15 unique picks")
    by_id = {player.fpl_player_id: player for player in projections.players}
    reconciliation: list[dict[str, Any]] = []
    unresolved: list[int] = []
    incomplete: list[int] = []
    missing: list[int] = []
    for pick in state.picks:
        projected = by_id.get(pick.element_id)
        if projected is None:
            status = "unresolved_projection_player"
            unresolved.append(pick.element_id)
        else:
            status = projected.projection_state.value
            if status == "incomplete_projection":
                incomplete.append(pick.element_id)
            elif status == "missing_projection":
                missing.append(pick.element_id)
        reconciliation.append(
            {
                "element_id": pick.element_id,
                "pick_position": pick.pick_position,
                "bootstrap_team_id": pick.team_id,
                "bootstrap_position": pick.position,
                "projection_status": status,
                "projection": projected.projection if projected is not None else None,
                "projection_team_id": projected.team_id if projected is not None else None,
                "projection_position": projected.position if projected is not None else None,
                "same_season_identity_resolved": projected is not None,
            }
        )
        if projected is not None and (
            projected.team_id != pick.team_id or projected.position != pick.position
        ):
            raise ManagerDecisionError(
                f"owned player {pick.element_id} team/position differs across same-season sources"
            )

    manager_players = [by_id[player_id] for player_id in state.manager_xi if player_id in by_id]
    manager_formation = _formation(manager_players) if len(manager_players) == 11 else "unresolved"
    manager_score = _score_locked_selection(state, by_id)
    optimized: DecisionResult | None = None
    if unresolved:
        raise ManagerDecisionError(
            "owned player IDs are unresolved in the projection universe: "
            + ", ".join(map(str, sorted(unresolved)))
        )
    if incomplete or missing:
        reconciliation_status = "optimization_blocked_by_owned_projection_reconciliation"
    else:
        try:
            squad = resolve_existing_squad(projections, owned_ids)
            optimized = optimize_xi(squad)
        except DecisionError as exc:
            raise ManagerDecisionError(f"owned squad failed Task 014 validation: {exc}") from exc
        reconciliation_status = "all_owned_players_eligible_and_resolved"

    chip_limitation: str | None = None
    direct_comparison_allowed = True
    if state.active_chip in NONCOMPARABLE_SCORING_CHIPS:
        chip_limitation = (
            f"active chip {state.active_chip!r} changes scoring; Task 015 reports only "
            "the standard Task 014 XI/captain objective"
        )
        direct_comparison_allowed = False
    elif state.active_chip:
        chip_limitation = (
            f"active chip {state.active_chip!r} is recorded but not optimized or reinterpreted"
        )

    started: tuple[int, ...] = ()
    benched: tuple[int, ...] = ()
    bench_added: tuple[int, ...] = ()
    bench_removed: tuple[int, ...] = ()
    captain_changed: bool | None = None
    vice_changed: bool | None = None
    changes: tuple[str, ...] = ()
    difference: float | None = None
    optimized_formation: str | None = None
    if optimized is not None:
        optimized_starters = {
            row.player.fpl_player_id for row in optimized.selections if row.is_starter
        }
        optimized_bench = {
            row.player.fpl_player_id for row in optimized.selections if not row.is_starter
        }
        manager_starters = set(state.manager_xi)
        manager_bench = set(state.manager_bench)
        started = tuple(sorted(optimized_starters - manager_starters))
        benched = tuple(sorted(manager_starters - optimized_starters))
        bench_added = tuple(sorted(optimized_bench - manager_bench))
        bench_removed = tuple(sorted(manager_bench - optimized_bench))
        captain_changed = optimized.captain.fpl_player_id != state.manager_captain
        vice_changed = optimized.vice_captain.fpl_player_id != state.manager_vice_captain
        optimized_formation = optimized.formation
        if manager_score is not None and direct_comparison_allowed:
            difference = optimized.total_objective - manager_score.total_objective
            changes = _change_list(
                started=started,
                benched=benched,
                by_id=by_id,
                old_captain=state.manager_captain,
                new_captain=optimized.captain.fpl_player_id,
                old_vice=state.manager_vice_captain,
                new_vice=optimized.vice_captain.fpl_player_id,
            )

    if optimized is None:
        comparison_status = "comparison_unavailable_incomplete_owned_projection_set"
    elif manager_score is None:
        comparison_status = "comparison_unavailable_manager_xi_projection_missing"
    elif not direct_comparison_allowed:
        comparison_status = "comparison_not_apples_to_apples_due_to_active_chip"
    else:
        comparison_status = "complete_standard_objective_comparison"

    return ManagerDecisionResult(
        manager_state=state,
        projection_dataset=projections,
        reconciliation=tuple(reconciliation),
        reconciliation_status=reconciliation_status,
        incomplete_owned_player_ids=tuple(sorted(incomplete)),
        missing_owned_player_ids=tuple(sorted(missing)),
        unresolved_projection_player_ids=tuple(sorted(unresolved)),
        manager_score=manager_score,
        optimized_result=optimized,
        manager_formation=manager_formation,
        optimized_formation=optimized_formation,
        xi_started_ids=started,
        xi_benched_ids=benched,
        bench_added_ids=bench_added,
        bench_removed_ids=bench_removed,
        captain_changed=captain_changed,
        vice_captain_changed=vice_changed,
        change_list=changes,
        modeled_component_projection_difference=difference,
        comparison_status=comparison_status,
        chip_limitation=chip_limitation,
        unconstrained_benchmark=unconstrained_benchmark,
    )


def _selection_payload(result: DecisionResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "squad_ids": [player.fpl_player_id for player in result.squad],
        "starting_xi": [
            row.player.fpl_player_id for row in result.selections if row.is_starter
        ],
        "bench": [
            row.player.fpl_player_id for row in result.selections if not row.is_starter
        ],
        "bench_order_semantics": "set_only_no_priority_optimization",
        "captain": result.captain.fpl_player_id,
        "vice_captain": result.vice_captain.fpl_player_id,
        "formation": result.formation,
        "squad_cost_units": result.squad_cost_units,
        "base_xi_projection": result.base_xi_projection,
        "captain_bonus": result.captain_bonus,
        "total_objective": result.total_objective,
    }


def manager_decision_payload(result: ManagerDecisionResult) -> dict[str, Any]:
    state = result.manager_state
    projections = result.projection_dataset
    manager_score = result.manager_score
    optimized = result.optimized_result
    comparable = (
        manager_score is not None
        and optimized is not None
        and result.modeled_component_projection_difference is not None
    )
    return {
        "version": CURRENT_SQUAD_DECISION_VERSION,
        "classification": CURRENT_SQUAD_CLASSIFICATION,
        "entry_id": state.entry_id,
        "season": state.season,
        "represented_event": state.represented_event,
        "manager_state_deadline": state.deadline_time,
        "manager_state_retrieval_timestamp": state.retrieval_timestamp,
        "manager_state_semantics": state.state_semantics,
        "freshness_warning": FRESHNESS_WARNING,
        "post_deadline_warning": POST_DEADLINE_WARNING,
        "manager_state_manifest_path": str(state.manifest_path.resolve()),
        "manager_state_manifest_sha256": state.manifest_sha256,
        "projection_target_gameweek": projections.target_gameweek,
        "projection_snapshot_timestamp": projections.snapshot_timestamp,
        "projection_provider_id": projections.provider_id,
        "projection_model_id": projections.source_model_id,
        "projection_model_scope": projections.model_scope,
        "projection_artifact_path": projections.source_artifact_path,
        "projection_artifact_sha256": projections.source_artifact_sha256,
        "decision_engine_version": DECISION_ENGINE_VERSION,
        "reconciliation_status": result.reconciliation_status,
        "reconciliation": list(result.reconciliation),
        "incomplete_owned_player_ids": list(result.incomplete_owned_player_ids),
        "missing_owned_player_ids": list(result.missing_owned_player_ids),
        "unresolved_projection_player_ids": list(result.unresolved_projection_player_ids),
        "manager_locked_selection": {
            "starting_xi": list(state.manager_xi),
            "bench_order": list(state.manager_bench),
            "captain": state.manager_captain,
            "vice_captain": state.manager_vice_captain,
            "formation": result.manager_formation,
            "active_chip": state.active_chip,
            "standard_objective_score": (
                {
                    "base_xi_projection": manager_score.base_xi_projection,
                    "captain_bonus": manager_score.captain_bonus,
                    "total_objective": manager_score.total_objective,
                }
                if manager_score is not None
                else None
            ),
        },
        "optimized_xi_within_owned_squad": _selection_payload(result.optimized_result),
        "manager_vs_engine": {
            "comparison_status": result.comparison_status,
            "difference_classification": DIFFERENCE_CLASSIFICATION,
            "xi_started_ids": list(result.xi_started_ids),
            "xi_benched_ids": list(result.xi_benched_ids),
            "bench_added_ids": list(result.bench_added_ids),
            "bench_removed_ids": list(result.bench_removed_ids),
            "captain_changed": result.captain_changed,
            "vice_captain_changed": result.vice_captain_changed,
            "formation_changed": (
                result.optimized_formation != result.manager_formation
                if result.optimized_formation is not None
                else None
            ),
            "base_xi_projection_difference": (
                optimized.base_xi_projection - manager_score.base_xi_projection
                if comparable
                else None
            ),
            "captain_bonus_difference": (
                optimized.captain_bonus - manager_score.captain_bonus
                if comparable
                else None
            ),
            "modeled_component_projection_difference": (
                result.modeled_component_projection_difference
            ),
            "change_list": list(result.change_list),
        },
        "chip_limitation": result.chip_limitation,
        "unconstrained_benchmark": (
            {
                "classification": UNCONSTRAINED_BENCHMARK_CLASSIFICATION,
                "not_a_transfer_plan": True,
                "selection": _selection_payload(result.unconstrained_benchmark),
            }
            if result.unconstrained_benchmark is not None
            else None
        ),
        "transfer_recommendation_status": TRANSFER_RECOMMENDATION_STATUS,
        "transfer_boundary": TRANSFER_NOT_PERFORMED,
        "task_015b_prerequisites": [
            "current editable squad confirmation",
            "manager-specific selling prices or explicit manual sell values",
            "bank",
            "free transfers",
        ],
    }


def write_manager_decision(
    result: ManagerDecisionResult,
    *,
    decision_data_root: Path = Path("data/manager/decisions/fpl"),
) -> ManagerDecisionArtifacts:
    state = result.manager_state
    retrieval_directory = state.raw_directory.name
    directory = (
        decision_data_root
        / state.season
        / f"entry={state.entry_id}"
        / f"event={state.represented_event}"
        / retrieval_directory
        / CURRENT_SQUAD_DECISION_VERSION
    )
    if directory.exists():
        raise ManagerDecisionOutputExistsError(
            f"manager decision already exists and will not be overwritten: {directory}"
        )
    directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = directory.parent / f".{CURRENT_SQUAD_DECISION_VERSION}.{uuid.uuid4().hex}.tmp"
    temporary.mkdir()
    try:
        manifest = temporary / "current_squad_decision.json"
        with manifest.open("x", encoding="utf-8") as output:
            json.dump(manager_decision_payload(result), output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.rename(directory)
    except Exception:
        for path in temporary.glob("*"):
            path.unlink()
        if temporary.exists():
            temporary.rmdir()
        raise
    manifest = directory / "current_squad_decision.json"
    return ManagerDecisionArtifacts(
        directory=directory,
        manifest_path=manifest,
        manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
    )
