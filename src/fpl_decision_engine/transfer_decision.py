"""Legal zero-or-one-transfer decisions for a verified editable FPL squad."""

from __future__ import annotations

import json
import math
import os
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cmp_to_key
from pathlib import Path
from typing import Any, Mapping

from .decision import (
    APPEARANCE_ONLY_ALLOWED_POLICY,
    DECISION_ENGINE_VERSION,
    DECISION_POLICY_VERSION,
    OBJECTIVE_TOLERANCE,
    DecisionError,
    DecisionResult,
    optimize_xi,
    projection_eligible_for_policy,
    resolve_existing_squad,
)
from .editable_manager import (
    APPEARANCE_ONLY_CAVEAT,
    BENCH_ORDER_SEMANTICS,
    MODEL_CAVEAT,
    ManualEditableState,
)
from .projection_provider import ProjectionDataset, ProjectionPlayer, sha256_file


ONE_TRANSFER_DECISION_VERSION = "one-transfer-decision-v1"
ONE_TRANSFER_CLASSIFICATION = (
    "experimental_decision_output_using_xfp_v01_modeled_components_only"
)
SELLING_PRICE_SOURCE = (
    "manual_transcription_from_official_fpl_transfers_page_screenshot"
)
PURCHASE_PRICE_SOURCE = "frozen_official_fpl_clean_player_snapshot"
ROLL = "ROLL"
TRANSFER = "TRANSFER"


class TransferDecisionError(Exception):
    """Raised when a zero-or-one-transfer decision cannot be proven safely."""


class TransferDecisionOutputExistsError(TransferDecisionError):
    """Raised rather than overwriting an immutable transfer-decision artifact."""


@dataclass(frozen=True)
class TransferCandidate:
    outgoing: ProjectionPlayer
    incoming: ProjectionPlayer
    selling_price_units: int
    purchase_price_units: int
    resulting_bank_units: int
    optimized_result: DecisionResult


@dataclass(frozen=True)
class OneTransferDecision:
    state: ManualEditableState
    projections: ProjectionDataset
    decision_policy: str
    selling_price_source: str
    selling_prices: dict[int, int]
    roll_result: DecisionResult
    transfer_candidates: tuple[TransferCandidate, ...]
    best_transfer: TransferCandidate | None
    recommended_action: str


@dataclass(frozen=True)
class OneTransferArtifacts:
    directory: Path
    decision_path: Path
    decision_sha256: str
    candidates_path: Path
    candidates_sha256: str


def parse_selling_price(value: str) -> tuple[int, int]:
    """Parse ``element_id:price_m`` into exact tenth-million units."""
    parts = value.split(":", 1)
    if len(parts) != 2:
        raise TransferDecisionError("selling price must use element_id:price_m")
    try:
        player_id = int(parts[0])
    except ValueError as exc:
        raise TransferDecisionError("selling-price player ID must be an integer") from exc
    if player_id <= 0:
        raise TransferDecisionError("selling-price player ID must be positive")
    from .editable_manager import price_m_to_units

    try:
        units = price_m_to_units(parts[1], f"selling price for player {player_id}")
    except Exception as exc:
        raise TransferDecisionError(str(exc)) from exc
    return player_id, units


def selling_price_map(values: tuple[str, ...] | list[str]) -> dict[int, int]:
    prices: dict[int, int] = {}
    for value in values:
        player_id, units = parse_selling_price(value)
        if player_id in prices:
            raise TransferDecisionError(
                f"duplicate selling price for player {player_id}"
            )
        prices[player_id] = units
    return prices


def _validate_selling_prices(
    state: ManualEditableState, selling_prices: Mapping[int, int]
) -> dict[int, int]:
    owned_ids = {pick.element_id for pick in state.picks}
    supplied_ids = set(selling_prices)
    if supplied_ids != owned_ids:
        missing = sorted(owned_ids - supplied_ids)
        extra = sorted(supplied_ids - owned_ids)
        raise TransferDecisionError(
            f"selling prices must cover exactly the owned squad; missing={missing}, extra={extra}"
        )
    validated: dict[int, int] = {}
    for player_id, value in selling_prices.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TransferDecisionError(
                f"selling price for player {player_id} must be non-negative £0.1m units"
            )
        validated[int(player_id)] = value
    return dict(sorted(validated.items()))


def _candidate_compare(left: TransferCandidate, right: TransferCandidate) -> int:
    left_objective = left.optimized_result.total_objective
    right_objective = right.optimized_result.total_objective
    if not math.isclose(
        left_objective,
        right_objective,
        rel_tol=0.0,
        abs_tol=OBJECTIVE_TOLERANCE,
    ):
        return -1 if left_objective > right_objective else 1
    left_ids = (left.outgoing.fpl_player_id, left.incoming.fpl_player_id)
    right_ids = (right.outgoing.fpl_player_id, right.incoming.fpl_player_id)
    return (left_ids > right_ids) - (left_ids < right_ids)


def evaluate_one_transfer(
    state: ManualEditableState,
    projections: ProjectionDataset,
    selling_prices: Mapping[int, int],
    *,
    decision_policy: str,
    selling_price_source: str = SELLING_PRICE_SOURCE,
) -> OneTransferDecision:
    """Evaluate ROLL and every legal same-position one-free-transfer squad."""
    if decision_policy != APPEARANCE_ONLY_ALLOWED_POLICY:
        raise TransferDecisionError(
            "Task 016 requires explicit decision_policy=appearance_only_allowed"
        )
    if selling_price_source != SELLING_PRICE_SOURCE:
        raise TransferDecisionError("selling-price provenance is unsupported")
    if state.season != projections.season or state.target_gameweek != projections.target_gameweek:
        raise TransferDecisionError(
            "manual editable state and projection season/gameweek do not align exactly"
        )
    if state.free_transfers < 1 or state.current_transfer_cost_points != 0:
        raise TransferDecisionError(
            "Task 016 requires at least one free transfer and zero one-transfer cost"
        )
    prices = _validate_selling_prices(state, selling_prices)
    projection_ids = [player.fpl_player_id for player in projections.players]
    if len(projection_ids) != len(set(projection_ids)):
        raise TransferDecisionError("projection universe contains duplicate player IDs")
    owned_ids = tuple(pick.element_id for pick in state.picks)
    owned_id_set = set(owned_ids)
    try:
        current_squad = resolve_existing_squad(
            projections,
            owned_ids,
            decision_policy=decision_policy,
        )
        current_by_id = {player.fpl_player_id: player for player in current_squad}
        mismatched_positions = sorted(
            pick.element_id
            for pick in state.picks
            if current_by_id[pick.element_id].position != pick.position
        )
        if mismatched_positions:
            raise TransferDecisionError(
                "manual/projection position mismatch for player IDs: "
                + ", ".join(map(str, mismatched_positions))
            )
        roll_result = optimize_xi(current_squad, decision_policy=decision_policy)
    except DecisionError as exc:
        raise TransferDecisionError(f"ROLL squad is invalid: {exc}") from exc

    by_position: dict[str, list[ProjectionPlayer]] = {}
    for player in projections.players:
        try:
            eligible = projection_eligible_for_policy(player, decision_policy)
        except DecisionError as exc:
            raise TransferDecisionError(str(exc)) from exc
        if eligible and player.fpl_player_id not in owned_id_set:
            by_position.setdefault(player.position, []).append(player)
    for rows in by_position.values():
        rows.sort(key=lambda player: player.fpl_player_id)

    candidates: list[TransferCandidate] = []
    for outgoing_id in sorted(owned_ids):
        outgoing = current_by_id[outgoing_id]
        available_budget = prices[outgoing_id] + state.bank_units
        remaining = tuple(
            player for player in current_squad if player.fpl_player_id != outgoing_id
        )
        remaining_clubs = Counter(player.team_id for player in remaining)
        for incoming in by_position.get(outgoing.position, []):
            if incoming.price_units > available_budget:
                continue
            if remaining_clubs[incoming.team_id] >= 3:
                continue
            candidate_squad = remaining + (incoming,)
            try:
                optimized = optimize_xi(
                    candidate_squad,
                    decision_policy=decision_policy,
                )
            except DecisionError as exc:
                raise TransferDecisionError(
                    f"legal-candidate validation failed for {outgoing_id}->{incoming.fpl_player_id}: {exc}"
                ) from exc
            candidates.append(
                TransferCandidate(
                    outgoing=outgoing,
                    incoming=incoming,
                    selling_price_units=prices[outgoing_id],
                    purchase_price_units=incoming.price_units,
                    resulting_bank_units=available_budget - incoming.price_units,
                    optimized_result=optimized,
                )
            )
    ordered = tuple(sorted(candidates, key=cmp_to_key(_candidate_compare)))
    best = ordered[0] if ordered else None
    recommended = ROLL
    if (
        best is not None
        and best.optimized_result.total_objective
        > roll_result.total_objective + OBJECTIVE_TOLERANCE
    ):
        recommended = TRANSFER
    return OneTransferDecision(
        state=state,
        projections=projections,
        decision_policy=decision_policy,
        selling_price_source=selling_price_source,
        selling_prices=prices,
        roll_result=roll_result,
        transfer_candidates=ordered,
        best_transfer=best,
        recommended_action=recommended,
    )


def _result_payload(result: DecisionResult) -> dict[str, Any]:
    return {
        "squad": [
            {
                "element_id": player.fpl_player_id,
                "name": player.player_name,
                "team_id": player.team_id,
                "position": player.position,
                "purchase_price_units": player.price_units,
                "projection": player.projection,
                "projection_state": player.projection_state.value,
                "prediction_complete": player.projection_state.value
                != "incomplete_projection",
                "expected_minutes": player.expected_minutes,
            }
            for player in result.squad
        ],
        "starting_xi": [
            {"element_id": row.player.fpl_player_id, "name": row.player.player_name}
            for row in result.selections
            if row.is_starter
        ],
        "bench": [
            {"element_id": row.player.fpl_player_id, "name": row.player.player_name}
            for row in result.selections
            if not row.is_starter
        ],
        "bench_order_semantics": BENCH_ORDER_SEMANTICS,
        "formation": result.formation,
        "captain": {
            "element_id": result.captain.fpl_player_id,
            "name": result.captain.player_name,
        },
        "vice_captain": {
            "element_id": result.vice_captain.fpl_player_id,
            "name": result.vice_captain.player_name,
        },
        "base_xi_projection": result.base_xi_projection,
        "captain_bonus": result.captain_bonus,
        "total_objective": result.total_objective,
    }


def _candidate_summary(
    candidate: TransferCandidate, roll_objective: float
) -> dict[str, Any]:
    optimized = candidate.optimized_result
    return {
        "out": {
            "element_id": candidate.outgoing.fpl_player_id,
            "name": candidate.outgoing.player_name,
            "verified_selling_price_units": candidate.selling_price_units,
        },
        "in": {
            "element_id": candidate.incoming.fpl_player_id,
            "name": candidate.incoming.player_name,
            "purchase_price_units": candidate.purchase_price_units,
            "projection_state": candidate.incoming.projection_state.value,
            "expected_minutes": candidate.incoming.expected_minutes,
        },
        "resulting_bank_units": candidate.resulting_bank_units,
        "formation": optimized.formation,
        "captain_id": optimized.captain.fpl_player_id,
        "vice_captain_id": optimized.vice_captain.fpl_player_id,
        "base_xi_projection": optimized.base_xi_projection,
        "captain_bonus": optimized.captain_bonus,
        "total_objective": optimized.total_objective,
        "gain_vs_roll": optimized.total_objective - roll_objective,
    }


def one_transfer_payload(
    decision: OneTransferDecision,
    *,
    candidates_path: Path | None = None,
    candidates_sha256: str | None = None,
    generation_timestamp: str | None = None,
) -> dict[str, Any]:
    roll_objective = decision.roll_result.total_objective
    best = decision.best_transfer
    best_payload = None
    if best is not None:
        best_payload = _candidate_summary(best, roll_objective)
        best_payload["optimized_squad"] = _result_payload(best.optimized_result)
    admitted_incomplete = [
        {
            "element_id": player.fpl_player_id,
            "name": player.player_name,
            "expected_minutes": player.expected_minutes,
            "projection": player.projection,
            "prediction_complete": False,
        }
        for player in decision.projections.players
        if player.projection_state.value == "incomplete_projection"
        and player.fpl_player_id
        in {squad_player.fpl_player_id for squad_player in decision.roll_result.squad}
    ]
    return {
        "version": ONE_TRANSFER_DECISION_VERSION,
        "classification": ONE_TRANSFER_CLASSIFICATION,
        "generation_timestamp": generation_timestamp,
        "season": decision.state.season,
        "target_gameweek": decision.state.target_gameweek,
        "entry_id": decision.state.entry_id,
        "scope": "single_gameweek_zero_or_one_free_transfer",
        "decision_policy": decision.decision_policy,
        "decision_policy_version": DECISION_POLICY_VERSION,
        "decision_policy_is_default": False,
        "incomplete_projection_invariant": (
            "every admitted numeric incomplete xFP v0.1 projection has expected_minutes exactly 0"
        ),
        "appearance_only_policy_caveat": APPEARANCE_ONLY_CAVEAT,
        "model_caveat": MODEL_CAVEAT,
        "manual_state": {
            "artifact_path": str(decision.state.artifact_path.resolve()),
            "artifact_sha256": decision.state.artifact_sha256,
            "bank_units": decision.state.bank_units,
            "free_transfers": decision.state.free_transfers,
            "one_transfer_cost_points": decision.state.current_transfer_cost_points,
            "no_chip_modeled": True,
        },
        "selling_price_inputs": {
            "source": decision.selling_price_source,
            "manager_specific": True,
            "inferred_from_current_price": False,
            "prices": [
                {
                    "element_id": player_id,
                    "selling_price_units": units,
                }
                for player_id, units in decision.selling_prices.items()
            ],
        },
        "purchase_price_provenance": {
            "source": PURCHASE_PRICE_SOURCE,
            "players_artifact_path": decision.projections.players_artifact_path,
            "players_artifact_sha256": decision.projections.players_artifact_sha256,
            "snapshot_timestamp": decision.projections.snapshot_timestamp,
            "third_party_price_used": False,
        },
        "projection_provenance": {
            "provider_id": decision.projections.provider_id,
            "model_id": decision.projections.source_model_id,
            "artifact_path": decision.projections.source_artifact_path,
            "artifact_sha256": decision.projections.source_artifact_sha256,
        },
        "optimizer": {
            "decision_engine_version": DECISION_ENGINE_VERSION,
            "objective": "starting_xi_xfp_plus_captain_xfp",
            "reused_task014_optimize_xi": True,
            "objective_tolerance": OBJECTIVE_TOLERANCE,
        },
        "tie_breaking": {
            "roll_beats_transfer_within_tolerance": True,
            "tied_transfers": "outgoing_fpl_player_id_then_incoming_fpl_player_id_ascending",
        },
        "admitted_incomplete_roll_squad": admitted_incomplete,
        "roll": _result_payload(decision.roll_result),
        "legal_transfer_candidate_count": len(decision.transfer_candidates),
        "candidate_summaries_artifact": (
            {
                "path": str(candidates_path.resolve()),
                "sha256": candidates_sha256,
            }
            if candidates_path is not None
            else None
        ),
        "top_10_legal_transfers": [
            _candidate_summary(candidate, roll_objective)
            for candidate in decision.transfer_candidates[:10]
        ],
        "best_transfer": best_payload,
        "comparison": {
            "roll_objective": roll_objective,
            "best_transfer_objective": (
                best.optimized_result.total_objective if best is not None else None
            ),
            "projected_gain_from_best_transfer": (
                best.optimized_result.total_objective - roll_objective
                if best is not None
                else None
            ),
            "recommended_action": decision.recommended_action,
        },
        "interpretation": (
            "Best legal action only under frozen xFP v0.1 and the explicitly selected "
            "experimental appearance-only policy; not a definitive best FPL transfer."
        ),
    }


def _filesystem_timestamp(value: datetime) -> tuple[str, str]:
    if value.tzinfo is None:
        raise TransferDecisionError("generation timestamp must be timezone-aware")
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


def write_one_transfer_decision(
    decision: OneTransferDecision,
    *,
    decision_data_root: Path = Path("data/manager/decisions/fpl"),
    generated_at: datetime | None = None,
) -> OneTransferArtifacts:
    """Write immutable decision and all-candidate summaries with atomic renames."""
    generated_iso, timestamp = _filesystem_timestamp(
        generated_at or datetime.now(timezone.utc)
    )
    manual_record = decision.state.artifact_path.parent.name
    directory = (
        decision_data_root
        / decision.state.season
        / f"entry={decision.state.entry_id}"
        / f"gameweek={decision.state.target_gameweek}"
        / manual_record
        / ONE_TRANSFER_DECISION_VERSION
        / timestamp
    )
    if directory.exists():
        raise TransferDecisionOutputExistsError(
            f"one-transfer decision output already exists: {directory}"
        )
    directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = directory.parent / f".{timestamp}.{uuid.uuid4().hex}.tmp"
    temporary.mkdir()
    try:
        candidates_path = temporary / "legal_transfer_candidates.json"
        roll_objective = decision.roll_result.total_objective
        _write_json(
            candidates_path,
            [
                _candidate_summary(candidate, roll_objective)
                for candidate in decision.transfer_candidates
            ],
        )
        candidates_hash = sha256_file(candidates_path)
        decision_path = temporary / "one_transfer_decision.json"
        _write_json(
            decision_path,
            one_transfer_payload(
                decision,
                candidates_path=directory / candidates_path.name,
                candidates_sha256=candidates_hash,
                generation_timestamp=generated_iso,
            ),
        )
        temporary.rename(directory)
    except Exception:
        for child in temporary.glob("*"):
            child.unlink()
        if temporary.exists():
            temporary.rmdir()
        raise
    decision_path = directory / "one_transfer_decision.json"
    candidates_path = directory / "legal_transfer_candidates.json"
    return OneTransferArtifacts(
        directory=directory,
        decision_path=decision_path,
        decision_sha256=sha256_file(decision_path),
        candidates_path=candidates_path,
        candidates_sha256=sha256_file(candidates_path),
    )
