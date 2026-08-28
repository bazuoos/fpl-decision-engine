"""Deterministic GameweekDecision v1 presentation contract builder."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any

import duckdb
from jsonschema import Draft202012Validator, FormatChecker

from ..decision import (
    POSITION_ORDER,
    DecisionSelectionValidationError,
    validate_decision_selection,
)
from ..decision_reliability import DECISION_RELIABILITY_VERSION
from ..projection_provider import (
    ProjectionPlayer,
    ProjectionState,
    XfpV01ParquetProvider,
    sha256_file,
)
from ..transfer_decision import ONE_TRANSFER_DECISION_VERSION, ROLL, TRANSFER


GAMEWEEK_DECISION_SCHEMA_NAME = "GameweekDecision"
GAMEWEEK_DECISION_SCHEMA_VERSION = "1.0.0"
GAMEWEEK_DECISION_CLASSIFICATION = "read_only_validated_single_gameweek_decision"
SCHEMA_RESOURCE_PARTS = ("schemas", "gameweek_decision_v1.schema.json")


class GameweekDecisionError(Exception):
    """Raised when a presentation contract cannot be proven safely."""


class GameweekDecisionSourceValidationError(GameweekDecisionError):
    """Raised when an immutable upstream artifact fails closed validation."""


class GameweekDecisionSchemaError(GameweekDecisionError):
    """Raised when a GameweekDecision payload violates the v1 JSON Schema."""


def _read_json(path: Path, label: str) -> Any:
    if not path.is_file():
        raise GameweekDecisionSourceValidationError(f"{label} does not exist: {path}")
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise GameweekDecisionSourceValidationError(
            f"could not read {label}: {exc}"
        ) from exc


def _schema_document(schema_path: Path | None) -> tuple[dict[str, Any], str, str]:
    """Load the sole authoritative v1 schema from package data or an override."""
    try:
        if schema_path is None:
            resource = resources.files(__package__).joinpath(*SCHEMA_RESOURCE_PARTS)
            raw = resource.read_bytes()
            artifact_name = resource.name
        else:
            resolved = schema_path.resolve()
            raw = resolved.read_bytes()
            artifact_name = resolved.name
    except (FileNotFoundError, OSError) as exc:
        raise GameweekDecisionSourceValidationError(
            f"could not read GameweekDecision JSON Schema: {exc}"
        ) from exc
    try:
        schema = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GameweekDecisionSourceValidationError(
            f"could not parse GameweekDecision JSON Schema: {exc}"
        ) from exc
    return (
        _object(schema, "GameweekDecision JSON Schema"),
        artifact_name,
        hashlib.sha256(raw).hexdigest(),
    )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GameweekDecisionSourceValidationError(f"{label} must be a JSON object")
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GameweekDecisionSourceValidationError(f"{label} must be an integer")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GameweekDecisionSourceValidationError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise GameweekDecisionSourceValidationError(f"{label} must be finite")
    return result


def _verified_artifact(
    value: Any,
    *,
    path_field: str,
    hash_field: str,
    label: str,
) -> tuple[Path, str]:
    link = _object(value, f"{label} provenance")
    raw_path = link.get(path_field)
    expected_hash = link.get(hash_field)
    if not isinstance(raw_path, str) or not raw_path:
        raise GameweekDecisionSourceValidationError(f"{label} path is missing")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise GameweekDecisionSourceValidationError(f"{label} SHA-256 is missing")
    path = Path(raw_path).resolve()
    observed = sha256_file(path) if path.is_file() else None
    if observed != expected_hash:
        raise GameweekDecisionSourceValidationError(
            f"{label} hash mismatch: expected {expected_hash}, observed {observed}"
        )
    return path, expected_hash


def _deadline(feature_path: Path, target_gameweek: int) -> str:
    connection = duckdb.connect(":memory:")
    try:
        rows = connection.execute(
            """SELECT DISTINCT strftime(
                       timezone('UTC', target_deadline_time),
                       '%Y-%m-%dT%H:%M:%SZ'
                   )
                 FROM read_parquet(?)
                WHERE target_gameweek = ?""",
            [str(feature_path), target_gameweek],
        ).fetchall()
    except duckdb.Error as exc:
        raise GameweekDecisionSourceValidationError(
            f"could not establish frozen target deadline: {exc}"
        ) from exc
    finally:
        connection.close()
    deadlines = {row[0] for row in rows if row[0] is not None}
    if len(deadlines) != 1:
        raise GameweekDecisionSourceValidationError(
            f"expected one frozen target deadline, found {sorted(deadlines)!r}"
        )
    return str(next(iter(deadlines)))


def _ids(rows: Any, label: str) -> tuple[int, ...]:
    if not isinstance(rows, list):
        raise GameweekDecisionSourceValidationError(f"{label} must be an array")
    result: list[int] = []
    for index, row in enumerate(rows):
        item = _object(row, f"{label}[{index}]")
        result.append(_integer(item.get("element_id"), f"{label}[{index}].element_id"))
    return tuple(result)


def _canonical_selection_ids(
    player_ids: tuple[int, ...], players: Mapping[int, ProjectionPlayer]
) -> list[int]:
    return sorted(player_ids, key=lambda player_id: (POSITION_ORDER[players[player_id].position], player_id))


def _validated_selection(
    source: Any,
    players: Mapping[int, ProjectionPlayer],
    *,
    label: str,
) -> dict[str, Any]:
    section = _object(source, label)
    squad_ids = _ids(section.get("squad"), f"{label}.squad")
    starting_ids = _ids(section.get("starting_xi"), f"{label}.starting_xi")
    bench_ids = _ids(section.get("bench"), f"{label}.bench")
    missing = sorted(set(squad_ids) - set(players))
    if missing:
        raise GameweekDecisionSourceValidationError(
            f"{label} contains player IDs absent from frozen projections: {missing}"
        )
    captain = _object(section.get("captain"), f"{label}.captain")
    vice = _object(section.get("vice_captain"), f"{label}.vice_captain")
    captain_id = _integer(captain.get("element_id"), f"{label}.captain.element_id")
    vice_id = _integer(vice.get("element_id"), f"{label}.vice_captain.element_id")
    try:
        validated = validate_decision_selection(
            tuple(players[player_id] for player_id in squad_ids),
            starting_xi_ids=starting_ids,
            bench_ids=bench_ids,
            captain_id=captain_id,
            vice_captain_id=vice_id,
        )
    except DecisionSelectionValidationError as exc:
        raise GameweekDecisionSourceValidationError(
            f"{label} failed trusted persisted-selection validation: {exc}"
        ) from exc
    source_formation = section.get("formation")
    if source_formation != validated.formation:
        raise GameweekDecisionSourceValidationError(
            f"{label} formation does not match trusted player positions"
        )
    return {
        "squad": sorted(validated.squad_ids),
        "starting_xi": _canonical_selection_ids(validated.starting_xi_ids, players),
        "bench": _canonical_selection_ids(bench_ids, players),
        "captain": validated.captain_id,
        "vice_captain": validated.vice_captain_id,
        "formation": validated.formation,
        "bench_order_semantics": str(section.get("bench_order_semantics")),
        "objective": {
            "base_xi_xfp": _number(
                section.get("base_xi_projection"), f"{label}.base_xi_projection"
            ),
            "captain_bonus_xfp": _number(
                section.get("captain_bonus"), f"{label}.captain_bonus"
            ),
            "total_xfp": _number(
                section.get("total_objective"), f"{label}.total_objective"
            ),
        },
        "incomplete_projection_ids": sorted(
            player_id
            for player_id in validated.squad_ids
            if players[player_id].projection_state == ProjectionState.INCOMPLETE
        ),
        "warnings": sorted(
            {
                "xfp_v01_models_appearance_goals_assists_only",
                *(
                    ["appearance_only_policy_admits_incomplete_projections"]
                    if any(
                        players[player_id].projection_state
                        == ProjectionState.INCOMPLETE
                        for player_id in validated.squad_ids
                    )
                    else []
                ),
            }
        ),
    }


def _projection_complete(player: ProjectionPlayer) -> bool:
    return player.projection_state in {
        ProjectionState.VALID,
        ProjectionState.VERIFIED_BLANK,
    }


def _player_payload(
    player: ProjectionPlayer,
    *,
    selling_price_units: int | None,
) -> dict[str, Any]:
    return {
        "element_id": player.fpl_player_id,
        "name": player.player_name,
        "team_id": player.team_id,
        "team_name": player.team_name,
        "team_short_name": player.team_short_name,
        "position": player.position,
        "purchase_price_units": player.price_units,
        "selling_price_units": selling_price_units,
        "projected_xfp": player.projection,
        "expected_minutes": player.expected_minutes,
        "prediction_complete": _projection_complete(player),
        "projection_state": player.projection_state.value,
    }


def _action_key(action: Mapping[str, Any]) -> str:
    if action.get("action") == ROLL:
        return ROLL
    outgoing = _object(action.get("outgoing"), "reliability outgoing player")
    incoming = _object(action.get("incoming"), "reliability incoming player")
    return f"TRANSFER:{outgoing.get('element_id')}->{incoming.get('element_id')}"


def _reliability_payload(reliability: Mapping[str, Any]) -> dict[str, Any]:
    stability = _object(reliability.get("stability_summary"), "reliability stability")
    official = _object(
        reliability.get("official_recommendation"), "reliability official recommendation"
    )
    official_key = _action_key(official)
    raw_views = reliability.get("diagnostic_sensitivity")
    if not isinstance(raw_views, list):
        raise GameweekDecisionSourceValidationError(
            "reliability diagnostic_sensitivity must be an array"
        )
    sensitivity: list[dict[str, Any]] = []
    for index, raw_view in enumerate(raw_views):
        view = _object(raw_view, f"reliability view {index}")
        action = _object(
            view.get("recommended_action_under_view"),
            f"reliability view {index} action",
        )
        action_type = action.get("action")
        if action_type not in {ROLL, TRANSFER}:
            raise GameweekDecisionSourceValidationError(
                f"reliability view {index} action is unsupported"
            )
        outgoing = action.get("outgoing")
        incoming = action.get("incoming")
        sensitivity.append(
            {
                "view_id": str(view.get("view_id")),
                "category": str(view.get("category")),
                "action_type": action_type,
                "outgoing_element_id": (
                    _integer(_object(outgoing, "view outgoing").get("element_id"), "view outgoing ID")
                    if outgoing is not None
                    else None
                ),
                "incoming_element_id": (
                    _integer(_object(incoming, "view incoming").get("element_id"), "view incoming ID")
                    if incoming is not None
                    else None
                ),
                "objective_xfp": _number(action.get("objective"), "view objective"),
                "gain_vs_roll_xfp": _number(
                    action.get("gain_vs_roll"), "view gain versus ROLL"
                ),
                "retains_official_exact_action": _action_key(action) == official_key,
            }
        )

    raw_material = reliability.get("material_player_reliability")
    if not isinstance(raw_material, list):
        raise GameweekDecisionSourceValidationError(
            "reliability material_player_reliability must be an array"
        )
    material: list[dict[str, Any]] = []
    for index, raw_player in enumerate(raw_material):
        player = _object(raw_player, f"material reliability player {index}")
        rate = _object(player.get("rate_diagnostics"), "material rate diagnostics")
        xg = _object(rate.get("xg_per_90"), "material xG/90 diagnostics")
        xa = _object(rate.get("xa_per_90"), "material xA/90 diagnostics")
        material.append(
            {
                "element_id": _integer(player.get("element_id"), "material element ID"),
                "roles": sorted(str(role) for role in player.get("roles", [])),
                "prior_total_minutes": player.get("prior_total_minutes"),
                "prior_appearances": _integer(
                    player.get("prior_appearances"), "material prior appearances"
                ),
                "prior_starts": player.get("prior_starts"),
                "prior_xg_per_90": player.get("prior_xg_per_90"),
                "prior_xa_per_90": player.get("prior_xa_per_90"),
                "low_sample": bool(player.get("low_sample")),
                "prediction_complete": bool(player.get("prediction_complete")),
                "unusually_extreme_attacking_rate": bool(
                    player.get("unusually_extreme_attacking_rate")
                ),
                "xg_position_rank": xg.get("position_rank_desc"),
                "xg_position_population": _integer(
                    xg.get("position_population_n"), "material xG population"
                ),
                "xa_position_rank": xa.get("position_rank_desc"),
                "xa_position_population": _integer(
                    xa.get("position_population_n"), "material xA population"
                ),
            }
        )
    raw_warnings = reliability.get("warnings")
    if not isinstance(raw_warnings, list):
        raise GameweekDecisionSourceValidationError("reliability warnings must be an array")
    warnings = sorted(
        (
            {
                "code": str(_object(row, "reliability warning").get("code")),
                "message": str(_object(row, "reliability warning").get("message")),
            }
            for row in raw_warnings
        ),
        key=lambda row: (row["code"], row["message"]),
    )
    result = {
        "diagnostic_only": bool(reliability.get("diagnostic_only")),
        "official_recommendation_unchanged": bool(
            reliability.get("official_recommendation_unchanged")
        ),
        "sensitivity_view_count": _integer(
            stability.get("diagnostic_view_count"), "sensitivity view count"
        ),
        "same_exact_action_count": _integer(
            stability.get("same_exact_action_count"), "same-action count"
        ),
        "changed_action_count": _integer(
            stability.get("different_action_count"), "changed-action count"
        ),
        "captaincy_materially_changes_gain": bool(
            stability.get("captaincy_materially_changes_gain")
        ),
        "warnings": warnings,
        "material_players": sorted(material, key=lambda row: row["element_id"]),
        "sensitivity_results": sorted(sensitivity, key=lambda row: row["view_id"]),
    }
    if result["sensitivity_view_count"] != len(result["sensitivity_results"]):
        raise GameweekDecisionSourceValidationError(
            "reliability sensitivity count does not reconcile"
        )
    if not result["diagnostic_only"] or not result["official_recommendation_unchanged"]:
        raise GameweekDecisionSourceValidationError(
            "reliability must remain diagnostic-only and preserve the recommendation"
        )
    return result


def _source_entry(role: str, path: Path, digest: str) -> dict[str, Any]:
    return {
        "role": role,
        "artifact_name": path.name,
        "sha256": digest,
        "hash_validated": True,
    }


def validate_gameweek_decision_schema(
    payload: Mapping[str, Any], *, schema_path: Path | None = None
) -> None:
    """Validate a payload against the committed GameweekDecision v1 schema."""
    schema, _, _ = _schema_document(schema_path)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(dict(payload)), key=lambda error: list(error.path))
    if errors:
        details = "; ".join(
            f"{'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
            for error in errors
        )
        raise GameweekDecisionSchemaError(f"GameweekDecision v1 schema validation failed: {details}")


def build_gameweek_decision(
    decision_artifact: Path,
    reliability_artifact: Path,
    *,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Build a read-only contract from already-materialized trusted artifacts."""
    decision_path = decision_artifact.resolve()
    reliability_path = reliability_artifact.resolve()
    decision = _object(_read_json(decision_path, "decision artifact"), "decision artifact")
    reliability = _object(
        _read_json(reliability_path, "reliability artifact"), "reliability artifact"
    )
    if decision.get("version") != ONE_TRANSFER_DECISION_VERSION:
        raise GameweekDecisionSourceValidationError("decision artifact version is unsupported")
    if reliability.get("version") != DECISION_RELIABILITY_VERSION:
        raise GameweekDecisionSourceValidationError("reliability artifact version is unsupported")
    decision_hash = sha256_file(decision_path)
    reliability_hash = sha256_file(reliability_path)
    reliability_provenance = _object(
        reliability.get("provenance"), "reliability provenance"
    )
    reliability_decision = _object(
        reliability_provenance.get("task_016_decision_artifact"),
        "reliability decision provenance",
    )
    if reliability_decision.get("sha256") != decision_hash:
        raise GameweekDecisionSourceValidationError(
            "reliability artifact does not identify the supplied decision hash"
        )

    candidate_path, candidate_hash = _verified_artifact(
        decision.get("candidate_summaries_artifact"),
        path_field="path",
        hash_field="sha256",
        label="legality-checked candidate artifact",
    )
    candidates = _read_json(candidate_path, "legality-checked candidate artifact")
    if not isinstance(candidates, list) or len(candidates) != decision.get(
        "legal_transfer_candidate_count"
    ):
        raise GameweekDecisionSourceValidationError(
            "legality-checked candidate count does not reconcile"
        )
    reliability_candidate = _object(
        reliability_provenance.get("task_016_candidate_artifact"),
        "reliability candidate provenance",
    )
    if reliability_candidate.get("sha256") != candidate_hash:
        raise GameweekDecisionSourceValidationError(
            "reliability candidate provenance does not reconcile"
        )
    projection_path, projection_hash = _verified_artifact(
        decision.get("projection_provenance"),
        path_field="artifact_path",
        hash_field="artifact_sha256",
        label="frozen projection artifact",
    )
    players_path, players_hash = _verified_artifact(
        decision.get("purchase_price_provenance"),
        path_field="players_artifact_path",
        hash_field="players_artifact_sha256",
        label="frozen player artifact",
    )
    manual_path, manual_hash = _verified_artifact(
        decision.get("manual_state"),
        path_field="artifact_path",
        hash_field="artifact_sha256",
        label="verified manager-state artifact",
    )
    manual = _object(_read_json(manual_path, "verified manager-state artifact"), "manager state")
    feature_path, feature_hash = _verified_artifact(
        reliability_provenance.get("frozen_feature_artifact"),
        path_field="path",
        hash_field="sha256",
        label="frozen feature artifact",
    )
    reliability_projection = _object(
        reliability_provenance.get("frozen_projection_artifact"),
        "reliability projection provenance",
    )
    reliability_players = _object(
        reliability_provenance.get("frozen_players_artifact"),
        "reliability player provenance",
    )
    reliability_manual = _object(
        reliability_provenance.get("manual_state"),
        "reliability manager-state provenance",
    )
    if reliability_projection.get("sha256") != projection_hash:
        raise GameweekDecisionSourceValidationError(
            "reliability projection provenance does not reconcile"
        )
    if reliability_players.get("sha256") != players_hash:
        raise GameweekDecisionSourceValidationError(
            "reliability player provenance does not reconcile"
        )
    if reliability_manual.get("artifact_sha256") != manual_hash:
        raise GameweekDecisionSourceValidationError(
            "reliability manager-state provenance does not reconcile"
        )
    _, schema_artifact_name, schema_hash = _schema_document(schema_path)

    season = str(decision.get("season"))
    target_gameweek = _integer(decision.get("target_gameweek"), "target gameweek")
    if reliability.get("season") != season or reliability.get("target_gameweek") != target_gameweek:
        raise GameweekDecisionSourceValidationError(
            "decision and reliability season/gameweek do not reconcile"
        )
    if (
        manual.get("season") != season
        or manual.get("target_gameweek") != target_gameweek
        or manual.get("entry_id") != decision.get("entry_id")
    ):
        raise GameweekDecisionSourceValidationError(
            "manager state does not align with decision season/gameweek/entry"
        )
    projections = XfpV01ParquetProvider(
        projection_artifact=projection_path,
        players_artifact=players_path,
    ).load(season=season, target_gameweek=target_gameweek)
    player_by_id = {player.fpl_player_id: player for player in projections.players}
    if len(player_by_id) != len(projections.players):
        raise GameweekDecisionSourceValidationError(
            "frozen projection universe contains duplicate player IDs"
        )

    roll_source = _object(decision.get("roll"), "ROLL result")
    roll_selection = _validated_selection(roll_source, player_by_id, label="ROLL result")
    comparison = _object(decision.get("comparison"), "decision comparison")
    if not math.isclose(
        roll_selection["objective"]["total_xfp"],
        _number(comparison.get("roll_objective"), "comparison ROLL objective"),
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        raise GameweekDecisionSourceValidationError("ROLL objective does not reconcile")

    selling_inputs = _object(decision.get("selling_price_inputs"), "selling-price inputs")
    prices = selling_inputs.get("prices")
    if not isinstance(prices, list):
        raise GameweekDecisionSourceValidationError("selling-price inputs must be an array")
    selling_prices = {
        _integer(_object(row, "selling-price row").get("element_id"), "selling-price player ID"):
        _integer(_object(row, "selling-price row").get("selling_price_units"), "selling price")
        for row in prices
    }
    roll_ids = tuple(roll_selection["squad"])
    if set(selling_prices) != set(roll_ids):
        raise GameweekDecisionSourceValidationError(
            "selling prices do not cover the persisted manager squad exactly"
        )
    manual_ids = _ids(manual.get("picks"), "manager-state picks")
    if set(manual_ids) != set(roll_ids) or len(manual_ids) != len(roll_ids):
        raise GameweekDecisionSourceValidationError(
            "manager-state squad does not reconcile with the persisted ROLL squad"
        )
    decision_manager = _object(decision.get("manual_state"), "decision manager state")
    for field, manual_field in (
        ("bank_units", "bank_units"),
        ("free_transfers", "free_transfers"),
        ("one_transfer_cost_points", "current_transfer_cost_points"),
    ):
        if decision_manager.get(field) != manual.get(manual_field):
            raise GameweekDecisionSourceValidationError(
                f"manager-state field {manual_field} does not reconcile"
            )

    recommended_type = comparison.get("recommended_action")
    transfer_legality_applicable = recommended_type == TRANSFER
    if recommended_type == TRANSFER:
        best = _object(decision.get("best_transfer"), "recommended transfer")
        optimized = _object(best.get("optimized_squad"), "recommended transfer selection")
        recommended_selection = _validated_selection(
            optimized, player_by_id, label="recommended transfer selection"
        )
        outgoing = _object(best.get("out"), "recommended outgoing player")
        incoming = _object(best.get("in"), "recommended incoming player")
        outgoing_id = _integer(outgoing.get("element_id"), "outgoing player ID")
        incoming_id = _integer(incoming.get("element_id"), "incoming player ID")
        outgoing_selling_price = _integer(
            outgoing.get("verified_selling_price_units"), "outgoing selling price"
        )
        incoming_purchase_price = _integer(
            incoming.get("purchase_price_units"), "incoming purchase price"
        )
        matches = [
            row
            for row in candidates
            if _object(row, "candidate").get("out", {}).get("element_id") == outgoing_id
            and _object(row, "candidate").get("in", {}).get("element_id") == incoming_id
        ]
        if len(matches) != 1:
            raise GameweekDecisionSourceValidationError(
                "recommended transfer is not uniquely present in the legality-checked candidate artifact"
            )
        candidate = _object(matches[0], "recommended candidate")
        if candidate.get("out") != outgoing or candidate.get("in") != incoming:
            raise GameweekDecisionSourceValidationError(
                "recommended transfer player/price facts do not match the trusted candidate"
            )
        if outgoing_selling_price != selling_prices.get(outgoing_id):
            raise GameweekDecisionSourceValidationError(
                "recommended outgoing selling price does not match verified manager input"
            )
        if (
            incoming_id not in player_by_id
            or incoming_purchase_price != player_by_id[incoming_id].price_units
        ):
            raise GameweekDecisionSourceValidationError(
                "recommended incoming price does not match frozen player data"
            )
        for field in (
            "resulting_bank_units",
            "formation",
            "captain_id",
            "vice_captain_id",
            "base_xi_projection",
            "captain_bonus",
            "total_objective",
            "gain_vs_roll",
        ):
            if candidate.get(field) != best.get(field):
                raise GameweekDecisionSourceValidationError(
                    f"recommended transfer field {field} does not match the trusted candidate"
                )
        if not math.isclose(
            _number(comparison.get("best_transfer_objective"), "comparison transfer objective"),
            _number(best.get("total_objective"), "best-transfer objective"),
            rel_tol=0.0,
            abs_tol=1e-9,
        ) or not math.isclose(
            _number(
                comparison.get("projected_gain_from_best_transfer"),
                "comparison transfer gain",
            ),
            _number(best.get("gain_vs_roll"), "best-transfer gain"),
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise GameweekDecisionSourceValidationError(
                "recommended transfer objective does not reconcile with comparison"
            )
        recommended_action: dict[str, Any] = {
            "action_type": TRANSFER,
            "outgoing": {
                "element_id": outgoing_id,
                "name": str(outgoing.get("name")),
                "selling_price_units": outgoing_selling_price,
            },
            "incoming": {
                "element_id": incoming_id,
                "name": str(incoming.get("name")),
                "purchase_price_units": incoming_purchase_price,
            },
            "resulting_bank_units": _integer(
                best.get("resulting_bank_units"), "resulting bank"
            ),
            "transfer_cost_points": _integer(
                _object(decision.get("manual_state"), "decision manager state").get(
                    "one_transfer_cost_points"
                ),
                "transfer cost",
            ),
            "objective_gain_vs_roll_xfp": _number(
                best.get("gain_vs_roll"), "transfer gain versus ROLL"
            ),
            "selection": recommended_selection,
        }
    elif recommended_type == ROLL:
        if decision.get("best_transfer") is not None and comparison.get(
            "best_transfer_objective"
        ) is None:
            raise GameweekDecisionSourceValidationError("ROLL comparison is inconsistent")
        outgoing_id = incoming_id = None
        recommended_selection = roll_selection
        recommended_action = {
            "action_type": ROLL,
            "objective_gain_vs_roll_xfp": 0.0,
            "selection": roll_selection,
        }
    else:
        raise GameweekDecisionSourceValidationError(
            f"unsupported recommended action {recommended_type!r}"
        )

    official = _object(
        reliability.get("official_recommendation"), "reliability official recommendation"
    )
    if official.get("action") != recommended_type:
        raise GameweekDecisionSourceValidationError(
            "reliability official recommendation does not match the decision"
        )
    if recommended_type == TRANSFER:
        if (
            _object(official.get("outgoing"), "reliability outgoing").get("element_id")
            != outgoing_id
            or _object(official.get("incoming"), "reliability incoming").get("element_id")
            != incoming_id
        ):
            raise GameweekDecisionSourceValidationError(
                "reliability transfer identity does not match the decision"
            )
        if not math.isclose(
            _number(official.get("objective"), "reliability official objective"),
            recommended_selection["objective"]["total_xfp"],
            rel_tol=0.0,
            abs_tol=1e-9,
        ) or not math.isclose(
            _number(official.get("gain_vs_roll"), "reliability official gain"),
            recommended_action["objective_gain_vs_roll_xfp"],
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise GameweekDecisionSourceValidationError(
                "reliability objective does not match the decision"
            )

    material_ids = set(roll_ids) | set(recommended_selection["squad"])
    registry = [
        _player_payload(
            player_by_id[player_id],
            selling_price_units=selling_prices.get(player_id),
        )
        for player_id in sorted(material_ids)
    ]
    admitted_incomplete = sorted(
        _integer(row.get("element_id"), "admitted incomplete player ID")
        for row in decision.get("admitted_incomplete_roll_squad", [])
    )
    source_artifacts = sorted(
        (
            _source_entry("decision", decision_path, decision_hash),
            _source_entry("reliability", reliability_path, reliability_hash),
            _source_entry("legality_checked_candidates", candidate_path, candidate_hash),
            _source_entry("frozen_features", feature_path, feature_hash),
            _source_entry("frozen_projections", projection_path, projection_hash),
            _source_entry("frozen_players", players_path, players_hash),
            _source_entry("verified_manager_state", manual_path, manual_hash),
            {
                "role": "contract_schema",
                "artifact_name": schema_artifact_name,
                "sha256": schema_hash,
                "hash_validated": True,
            },
        ),
        key=lambda row: row["role"],
    )
    payload = {
        "schema_name": GAMEWEEK_DECISION_SCHEMA_NAME,
        "schema_version": GAMEWEEK_DECISION_SCHEMA_VERSION,
        "classification": GAMEWEEK_DECISION_CLASSIFICATION,
        "generation_timestamp": decision.get("generation_timestamp"),
        "generation_timestamp_semantics": "copied_from_upstream_decision_artifact",
        "season": season,
        "target_gameweek": target_gameweek,
        "frozen_deadline": _deadline(feature_path, target_gameweek),
        "engine": {
            "decision_artifact_version": str(decision.get("version")),
            "decision_engine_version": str(
                _object(decision.get("optimizer"), "optimizer provenance").get(
                    "decision_engine_version"
                )
            ),
            "model_id": str(
                _object(decision.get("projection_provenance"), "projection provenance").get(
                    "model_id"
                )
            ),
            "model_scope": projections.model_scope,
            "decision_policy": str(decision.get("decision_policy")),
            "decision_policy_version": str(decision.get("decision_policy_version")),
            "reliability_version": str(reliability.get("version")),
        },
        "source_artifacts": source_artifacts,
        "manager_state": {
            "entry_id": _integer(decision.get("entry_id"), "entry ID"),
            "bank_units": _integer(
                decision_manager.get("bank_units"),
                "manager bank",
            ),
            "free_transfers": _integer(
                decision_manager.get("free_transfers"),
                "free transfers",
            ),
            "current_transfer_cost_points": _integer(
                decision_manager.get("one_transfer_cost_points"),
                "current transfer cost",
            ),
            "chip_state": "NONE_MODELED",
            "squad": list(roll_ids),
            "verified_provenance": {
                "manager_state_source": str(manual.get("verification_source")),
                "selling_price_source": str(selling_inputs.get("source")),
                "manager_specific_selling_prices": bool(
                    selling_inputs.get("manager_specific")
                ),
                "authentication_data_exposed": False,
            },
        },
        "players": registry,
        "roll": {
            **roll_selection,
            "incomplete_projection_ids": admitted_incomplete,
            "warnings": sorted(
                {
                    "appearance_only_policy_admits_incomplete_projections",
                    "xfp_v01_models_appearance_goals_assists_only",
                }
            ),
        },
        "recommended_action": recommended_action,
        "reliability": _reliability_payload(reliability),
        "validation": {
            "squad_legality_passed": True,
            "xi_legality_passed": True,
            "bench_accounting_passed": True,
            "captain_vice_constraints_passed": True,
            "transfer_legality": {
                "applicable": transfer_legality_applicable,
                "passed": True,
                "proof": (
                    "matched_unique_row_in_hash_validated_task016_candidate_artifact"
                    if transfer_legality_applicable
                    else "not_applicable_roll_recommendation"
                ),
                "candidate_count": len(candidates),
            },
            "all_source_hashes_validated": True,
            "reliability_provenance_validated": True,
        },
    }
    validate_gameweek_decision_schema(payload, schema_path=schema_path)
    return payload


def serialize_gameweek_decision(
    payload: Mapping[str, Any], *, schema_path: Path | None = None
) -> bytes:
    """Return canonical UTF-8 JSON bytes after schema validation."""
    validate_gameweek_decision_schema(payload, schema_path=schema_path)
    return (
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
