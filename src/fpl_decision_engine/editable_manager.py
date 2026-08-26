"""Manual editable-squad provenance and fixed-squad decision analysis."""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import duckdb

from .decision import (
    APPEARANCE_ONLY_ALLOWED_POLICY,
    DECISION_POLICIES,
    DECISION_POLICY_VERSION,
    DECISION_ENGINE_VERSION,
    DECISION_OUTPUT_CLASSIFICATION,
    DEFAULT_DECISION_POLICY,
    OBJECTIVE_TOLERANCE,
    SQUAD_POSITION_COUNTS,
    DecisionError,
    DecisionResult,
    optimize_xi,
    projection_eligible_for_policy,
    resolve_existing_squad,
)
from .projection_provider import (
    ProjectionDataset,
    ProjectionPlayer,
    ProjectionState,
    sha256_file,
)


MANUAL_STATE_VERSION = "manual-editable-manager-state-v1"
EDITABLE_DECISION_VERSION = "current-editable-squad-decision-v2"
EDITABLE_DECISION_CLASSIFICATION = (
    "experimental_current_editable_squad_decision_using_xfp_v01_"
    "modeled_components_only"
)
MANUAL_VERIFICATION_SOURCE = "manual_official_fpl_screen_confirmation"
UNCONSTRAINED_BENCHMARK_CLASSIFICATION = (
    "informational_unconstrained_projection_benchmark"
)
TRANSFER_BLOCKED_STATUS = "blocked_missing_verified_manager_selling_prices"
MODEL_CAVEAT = (
    "xFP v0.1 models appearance, goals and assists only; it is not expected total "
    "FPL points. Goalkeeper and defender clean-sheet, save and goals-conceded value "
    "is omitted."
)
APPEARANCE_ONLY_CAVEAT = (
    "For admitted incomplete projections, unavailable goal and assist contribution "
    "is implicitly treated as zero in the already-stored numeric xFP."
)
BENCH_ORDER_SEMANTICS = (
    "deterministic_task014_position_then_fpl_player_id_reporting_order; "
    "autosub_priority_is_not_optimized"
)
POSITION_CODES = {"GK", "DEF", "MID", "FWD"}


class EditableManagerError(Exception):
    """Raised when manual editable state or its decision cannot be proven safely."""


class EditableManagerOutputExistsError(EditableManagerError):
    """Raised rather than overwriting a manual-state or decision artifact."""


@dataclass(frozen=True)
class ManualEditablePick:
    element_id: int
    display_name: str
    position: str
    selling_price_units: int | None = None
    current_market_price_units: int | None = None


@dataclass(frozen=True)
class ManualEditableState:
    version: str
    entry_id: int
    season: str
    target_gameweek: int
    verification_source: str
    verification_timestamp: str | None
    recorded_timestamp: str
    bank_units: int
    free_transfers: int
    current_transfer_cost_points: int
    post_deadline_transfers_known: bool
    selling_prices_verified: bool
    picks: tuple[ManualEditablePick, ...]
    current_selection_verified: bool
    third_party_price_change_metadata: dict[str, Any] | None
    artifact_path: Path
    artifact_sha256: str


@dataclass(frozen=True)
class Task014Benchmark:
    classification: str
    manifest_path: Path
    manifest_sha256: str
    squad_path: Path
    squad_sha256: str
    season: str
    target_gameweek: int
    snapshot_timestamp: str
    formation: str
    squad_cost_units: int
    base_xi_projection: float
    captain_bonus: float
    total_objective: float
    captain_id: int
    captain_name: str
    vice_captain_id: int
    vice_captain_name: str


@dataclass(frozen=True)
class EditableDecisionResult:
    state: ManualEditableState
    projections: ProjectionDataset
    reconciliation: tuple[dict[str, Any], ...]
    reconciliation_counts: dict[str, int]
    projection_coverage_pct: float
    decision_policy: str
    decision_policy_coverage_pct: float
    optimized_result: DecisionResult | None
    bench_order: tuple[int, ...]
    benchmark: Task014Benchmark
    transfer_feasibility_status: str
    transfer_feasibility_reason: str


@dataclass(frozen=True)
class EditableDecisionArtifacts:
    directory: Path
    decision_path: Path
    decision_sha256: str


def _timestamp(value: datetime) -> tuple[str, str]:
    if value.tzinfo is None:
        raise EditableManagerError("recorded timestamp must be timezone-aware")
    utc = value.astimezone(timezone.utc)
    return (
        utc.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        utc.strftime("%Y%m%dT%H%M%S.%fZ"),
    )


def _validate_optional_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EditableManagerError("verification_timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise EditableManagerError("verification_timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EditableManagerError(f"{field} must be a non-negative integer")
    return value


def price_m_to_units(value: str | float | Decimal, field: str) -> int:
    """Convert a manager money input to exact £0.1m integer units."""
    try:
        units = Decimal(str(value)) * Decimal(10)
    except InvalidOperation as exc:
        raise EditableManagerError(f"{field} is not a valid money amount") from exc
    if not units.is_finite() or units < 0 or units != units.to_integral_value():
        raise EditableManagerError(
            f"{field} must be non-negative and use exact £0.1m increments"
        )
    return int(units)


def parse_manual_pick(value: str) -> ManualEditablePick:
    """Parse ``element_id:position:display_name`` from the CLI."""
    parts = value.split(":", 2)
    if len(parts) != 3:
        raise EditableManagerError(
            "manual player must use element_id:position:display_name"
        )
    raw_id, position, name = parts
    try:
        element_id = int(raw_id)
    except ValueError as exc:
        raise EditableManagerError("manual player element ID must be an integer") from exc
    return ManualEditablePick(element_id=element_id, position=position, display_name=name)


def _validated_picks(
    picks: Iterable[ManualEditablePick], *, selling_prices_verified: bool
) -> tuple[ManualEditablePick, ...]:
    rows = tuple(picks)
    if len(rows) != 15:
        raise EditableManagerError(
            f"manual editable state must contain exactly 15 players, found {len(rows)}"
        )
    ids = [row.element_id for row in rows]
    if any(isinstance(player_id, bool) or player_id <= 0 for player_id in ids):
        raise EditableManagerError("manual player IDs must be positive integers")
    if len(ids) != len(set(ids)):
        raise EditableManagerError("manual editable state contains duplicate player IDs")
    for row in rows:
        if row.position not in POSITION_CODES:
            raise EditableManagerError(
                f"manual player {row.element_id} has unsupported position {row.position!r}"
            )
        if not row.display_name.strip():
            raise EditableManagerError(
                f"manual player {row.element_id} requires a display name"
            )
        for field, value in (
            ("selling_price_units", row.selling_price_units),
            ("current_market_price_units", row.current_market_price_units),
        ):
            if value is not None:
                _nonnegative_int(value, f"player {row.element_id} {field}")
    counts = Counter(row.position for row in rows)
    if dict(counts) != SQUAD_POSITION_COUNTS:
        raise EditableManagerError(
            f"manual squad positions must be {SQUAD_POSITION_COUNTS}, found {dict(counts)}"
        )
    sell_count = sum(row.selling_price_units is not None for row in rows)
    if selling_prices_verified and sell_count != 15:
        raise EditableManagerError(
            "selling_prices_verified requires an explicit sell value for all 15 players"
        )
    if not selling_prices_verified and sell_count:
        raise EditableManagerError(
            "unverified selling values must not be recorded as manager sell prices"
        )
    return rows


def create_manual_editable_state(
    *,
    entry_id: int,
    season: str,
    target_gameweek: int,
    picks: Iterable[ManualEditablePick],
    bank_m: str | float | Decimal,
    free_transfers: int,
    current_transfer_cost_points: int,
    post_deadline_transfers_known: bool,
    selling_prices_verified: bool = False,
    verification_source: str = MANUAL_VERIFICATION_SOURCE,
    verification_timestamp: str | None = None,
    current_selection_verified: bool = False,
    third_party_price_change_metadata: dict[str, Any] | None = None,
    manual_data_root: Path = Path("data/manager/manual/fpl"),
    recorded_at: datetime | None = None,
) -> ManualEditableState:
    """Validate and immutably record a manually verified editable squad."""
    if isinstance(entry_id, bool) or not isinstance(entry_id, int) or entry_id <= 0:
        raise EditableManagerError("entry_id must be a positive integer")
    if not season or "/" in season or ".." in season:
        raise EditableManagerError("season is invalid")
    if (
        isinstance(target_gameweek, bool)
        or not isinstance(target_gameweek, int)
        or not 1 <= target_gameweek <= 38
    ):
        raise EditableManagerError("target_gameweek must be between 1 and 38")
    if verification_source != MANUAL_VERIFICATION_SOURCE:
        raise EditableManagerError(
            f"verification_source must be {MANUAL_VERIFICATION_SOURCE!r}"
        )
    if not isinstance(post_deadline_transfers_known, bool):
        raise EditableManagerError("post_deadline_transfers_known must be boolean")
    if not isinstance(selling_prices_verified, bool):
        raise EditableManagerError("selling_prices_verified must be boolean")
    if not isinstance(current_selection_verified, bool):
        raise EditableManagerError("current_selection_verified must be boolean")
    rows = _validated_picks(picks, selling_prices_verified=selling_prices_verified)
    bank_units = price_m_to_units(bank_m, "bank")
    free_transfers = _nonnegative_int(free_transfers, "free_transfers")
    transfer_cost = _nonnegative_int(
        current_transfer_cost_points, "current_transfer_cost_points"
    )
    verified_at = _validate_optional_timestamp(verification_timestamp)
    recorded_iso, filesystem_timestamp = _timestamp(
        recorded_at or datetime.now(timezone.utc)
    )
    directory = (
        manual_data_root
        / season
        / f"entry={entry_id}"
        / f"gameweek={target_gameweek}"
        / filesystem_timestamp
    )
    if directory.exists():
        raise EditableManagerOutputExistsError(
            f"manual editable-state output already exists: {directory}"
        )
    directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = directory.parent / f".{filesystem_timestamp}.{uuid.uuid4().hex}.tmp"
    temporary.mkdir()
    payload = {
        "version": MANUAL_STATE_VERSION,
        "entry_id": entry_id,
        "season": season,
        "target_gameweek": target_gameweek,
        "verification_source": verification_source,
        "verification_timestamp": verified_at,
        "recorded_timestamp": recorded_iso,
        "bank_units": bank_units,
        "bank_m": bank_units / 10,
        "free_transfers": free_transfers,
        "current_transfer_cost_points": transfer_cost,
        "post_deadline_transfers_known": post_deadline_transfers_known,
        "selling_prices_verified": selling_prices_verified,
        "current_selection_verified": current_selection_verified,
        "current_selection": None,
        "picks": [asdict(row) for row in rows],
        "third_party_price_change_metadata": third_party_price_change_metadata,
        "third_party_price_change_effect": "none",
        "provenance_boundary": (
            "manual editable state; distinct from public locked-deadline manager state"
        ),
    }
    try:
        path = temporary / "manual_editable_state.json"
        with path.open("x", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.rename(directory)
    except Exception:
        for child in temporary.glob("*"):
            child.unlink()
        if temporary.exists():
            temporary.rmdir()
        raise
    path = directory / "manual_editable_state.json"
    return ManualEditableState(
        version=MANUAL_STATE_VERSION,
        entry_id=entry_id,
        season=season,
        target_gameweek=target_gameweek,
        verification_source=verification_source,
        verification_timestamp=verified_at,
        recorded_timestamp=recorded_iso,
        bank_units=bank_units,
        free_transfers=free_transfers,
        current_transfer_cost_points=transfer_cost,
        post_deadline_transfers_known=post_deadline_transfers_known,
        selling_prices_verified=selling_prices_verified,
        picks=rows,
        current_selection_verified=current_selection_verified,
        third_party_price_change_metadata=third_party_price_change_metadata,
        artifact_path=path,
        artifact_sha256=sha256_file(path),
    )


def load_manual_editable_state(path: Path) -> ManualEditableState:
    """Load and validate an existing immutable manual editable-state artifact."""
    if not path.is_file():
        raise EditableManagerError(f"manual editable-state artifact is absent: {path}")
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise EditableManagerError(
            f"could not read manual editable-state artifact: {exc}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("version") != MANUAL_STATE_VERSION:
        raise EditableManagerError("manual editable-state version is unsupported")
    try:
        selling_prices_verified = payload["selling_prices_verified"]
        if not isinstance(selling_prices_verified, bool):
            raise EditableManagerError("selling_prices_verified must be boolean")
        picks = _validated_picks(
            (ManualEditablePick(**row) for row in payload["picks"]),
            selling_prices_verified=selling_prices_verified,
        )
        entry_id = int(payload["entry_id"])
        target_gameweek = int(payload["target_gameweek"])
        bank_units = int(payload["bank_units"])
        free_transfers = int(payload["free_transfers"])
        transfer_cost = int(payload["current_transfer_cost_points"])
        verification_source = str(payload["verification_source"])
        recorded_timestamp = str(payload["recorded_timestamp"])
        season = str(payload["season"])
        post_deadline_known = payload["post_deadline_transfers_known"]
        current_selection_verified = payload["current_selection_verified"]
    except (KeyError, TypeError, ValueError) as exc:
        raise EditableManagerError(
            f"manual editable-state structure is invalid: {exc}"
        ) from exc
    if entry_id <= 0 or not 1 <= target_gameweek <= 38 or not season:
        raise EditableManagerError("manual editable-state identity is invalid")
    _nonnegative_int(bank_units, "bank_units")
    _nonnegative_int(free_transfers, "free_transfers")
    _nonnegative_int(transfer_cost, "current_transfer_cost_points")
    if verification_source != MANUAL_VERIFICATION_SOURCE:
        raise EditableManagerError("manual editable-state verification source is invalid")
    _validate_optional_timestamp(recorded_timestamp)
    verification_timestamp = _validate_optional_timestamp(
        payload.get("verification_timestamp")
    )
    if not isinstance(post_deadline_known, bool) or not isinstance(
        current_selection_verified, bool
    ):
        raise EditableManagerError("manual editable-state boolean field is invalid")
    third_party = payload.get("third_party_price_change_metadata")
    if third_party is not None and not isinstance(third_party, dict):
        raise EditableManagerError("third-party metadata must be an object or null")
    return ManualEditableState(
        version=MANUAL_STATE_VERSION,
        entry_id=entry_id,
        season=season,
        target_gameweek=target_gameweek,
        verification_source=verification_source,
        verification_timestamp=verification_timestamp,
        recorded_timestamp=recorded_timestamp,
        bank_units=bank_units,
        free_transfers=free_transfers,
        current_transfer_cost_points=transfer_cost,
        post_deadline_transfers_known=post_deadline_known,
        selling_prices_verified=selling_prices_verified,
        picks=picks,
        current_selection_verified=current_selection_verified,
        third_party_price_change_metadata=third_party,
        artifact_path=path.resolve(),
        artifact_sha256=sha256_file(path),
    )


def _require_close(actual: object, expected: float, field: str) -> None:
    if not isinstance(actual, (int, float)) or isinstance(actual, bool) or not math.isclose(
        float(actual), expected, rel_tol=0.0, abs_tol=OBJECTIVE_TOLERANCE
    ):
        raise EditableManagerError(
            f"Task 014 benchmark {field} does not match reconstructed proven objective"
        )


def load_task014_benchmark(
    manifest_path: Path, projections: ProjectionDataset
) -> Task014Benchmark:
    """Load and independently reconstruct an immutable Task 014 v2 benchmark."""
    if not manifest_path.is_file():
        raise EditableManagerError(f"Task 014 benchmark manifest is absent: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EditableManagerError("Task 014 benchmark manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise EditableManagerError("Task 014 benchmark manifest is not an object")
    expected_metadata = {
        "decision_engine_version": DECISION_ENGINE_VERSION,
        "classification": DECISION_OUTPUT_CLASSIFICATION,
        "season": projections.season,
        "target_gameweek": projections.target_gameweek,
        "snapshot_timestamp": projections.snapshot_timestamp,
        "projection_model_id": projections.source_model_id,
        "projection_model_scope": projections.model_scope,
        "source_artifact_sha256": projections.source_artifact_sha256,
    }
    for field, expected in expected_metadata.items():
        if manifest.get(field) != expected:
            raise EditableManagerError(
                f"Task 014 benchmark {field} does not match the frozen projection"
            )
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict) or not isinstance(
        outputs.get("optimized_squad.parquet"), str
    ):
        raise EditableManagerError("Task 014 benchmark lacks its squad output hash")
    squad_path = manifest_path.parent / "optimized_squad.parquet"
    if not squad_path.is_file():
        raise EditableManagerError("Task 014 optimized squad artifact is absent")
    squad_hash = sha256_file(squad_path)
    if squad_hash != outputs["optimized_squad.parquet"]:
        raise EditableManagerError("Task 014 optimized squad artifact hash mismatch")

    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """SELECT fpl_player_id, player_name, projection, starter, captain,
                      vice_captain, base_contribution, captain_bonus,
                      total_contribution, price_units
                 FROM read_parquet(?)
             ORDER BY fpl_player_id""",
            [str(squad_path)],
        ).fetchall()
    except duckdb.Error as exc:
        raise EditableManagerError(f"could not read Task 014 squad artifact: {exc}") from exc
    finally:
        connection.close()
    if len(rows) != 15 or len({row[0] for row in rows}) != 15:
        raise EditableManagerError("Task 014 benchmark must contain 15 unique players")
    starters = [row for row in rows if row[3]]
    captains = [row for row in rows if row[4]]
    vice_captains = [row for row in rows if row[5]]
    if len(starters) != 11 or len(captains) != 1 or len(vice_captains) != 1:
        raise EditableManagerError("Task 014 benchmark selection shape is invalid")
    captain = captains[0]
    vice = vice_captains[0]
    if captain[0] == vice[0] or not captain[3] or not vice[3]:
        raise EditableManagerError("Task 014 captain/vice selection is invalid")
    if any(row[2] is None or not math.isfinite(float(row[2])) for row in rows):
        raise EditableManagerError("Task 014 benchmark contains unusable projections")
    reconstructed_base = sum(float(row[2]) for row in starters)
    reconstructed_captain = float(captain[2])
    reconstructed_total = reconstructed_base + reconstructed_captain
    if any(
        not math.isclose(
            float(row[6]), float(row[2]) if row[3] else 0.0,
            rel_tol=0.0, abs_tol=OBJECTIVE_TOLERANCE,
        )
        or not math.isclose(
            float(row[7]), float(row[2]) if row[4] else 0.0,
            rel_tol=0.0, abs_tol=OBJECTIVE_TOLERANCE,
        )
        or not math.isclose(
            float(row[8]), float(row[6]) + float(row[7]),
            rel_tol=0.0, abs_tol=OBJECTIVE_TOLERANCE,
        )
        for row in rows
    ):
        raise EditableManagerError(
            "Task 014 benchmark row contributions do not reconstruct"
        )
    _require_close(manifest.get("base_xi_projection"), reconstructed_base, "base")
    _require_close(manifest.get("captain_bonus"), reconstructed_captain, "captain bonus")
    _require_close(manifest.get("total_objective"), reconstructed_total, "total")
    if manifest.get("captain") != captain[1] or manifest.get("vice_captain") != vice[1]:
        raise EditableManagerError("Task 014 benchmark captain/vice names do not reconstruct")
    return Task014Benchmark(
        classification=UNCONSTRAINED_BENCHMARK_CLASSIFICATION,
        manifest_path=manifest_path.resolve(),
        manifest_sha256=sha256_file(manifest_path),
        squad_path=squad_path.resolve(),
        squad_sha256=squad_hash,
        season=projections.season,
        target_gameweek=projections.target_gameweek,
        snapshot_timestamp=projections.snapshot_timestamp,
        formation=str(manifest["formation"]),
        squad_cost_units=int(manifest["squad_cost_units"]),
        base_xi_projection=reconstructed_base,
        captain_bonus=reconstructed_captain,
        total_objective=reconstructed_total,
        captain_id=int(captain[0]),
        captain_name=str(captain[1]),
        vice_captain_id=int(vice[0]),
        vice_captain_name=str(vice[1]),
    )


def evaluate_editable_squad(
    state: ManualEditableState,
    projections: ProjectionDataset,
    benchmark: Task014Benchmark,
    *,
    decision_policy: str = DEFAULT_DECISION_POLICY,
) -> EditableDecisionResult:
    """Evaluate only the manually supplied 15; never select an unconstrained squad."""
    if decision_policy not in DECISION_POLICIES:
        raise EditableManagerError(f"unsupported decision policy: {decision_policy!r}")
    if state.season != projections.season or state.target_gameweek != projections.target_gameweek:
        raise EditableManagerError(
            "manual editable state and projection season/gameweek do not align exactly"
        )
    if benchmark.season != state.season or benchmark.target_gameweek != state.target_gameweek:
        raise EditableManagerError(
            "Task 014 benchmark and manual editable state do not align exactly"
        )
    by_id = {player.fpl_player_id: player for player in projections.players}
    reconciliation: list[dict[str, Any]] = []
    counts = Counter()
    for pick in state.picks:
        projected = by_id.get(pick.element_id)
        if projected is None:
            status = "unresolved_projection_player"
            projection = None
        else:
            if projected.position != pick.position:
                raise EditableManagerError(
                    f"manual/projection position mismatch for player {pick.element_id}"
                )
            status = projected.projection_state.value
            projection = projected.projection
        counts[status] += 1
        reconciliation.append(
            {
                "element_id": pick.element_id,
                "manual_display_name": pick.display_name,
                "manual_position": pick.position,
                "projection_player_name": (
                    projected.player_name if projected is not None else None
                ),
                "projection_state": status,
                "projection": projection,
                "expected_minutes": (
                    projected.expected_minutes if projected is not None else None
                ),
                "projection_team_id": (
                    projected.team_id if projected is not None else None
                ),
                "usable_projection": projected.eligible if projected is not None else False,
                "decision_policy_eligible": (
                    projection_eligible_for_policy(projected, decision_policy)
                    if projected is not None
                    else False
                ),
                "admitted_incomplete_under_policy": (
                    projected is not None
                    and projected.projection_state == ProjectionState.INCOMPLETE
                    and projection_eligible_for_policy(projected, decision_policy)
                ),
            }
        )
    canonical_counts = {
        ProjectionState.VALID.value: counts[ProjectionState.VALID.value],
        ProjectionState.VERIFIED_BLANK.value: counts[ProjectionState.VERIFIED_BLANK.value],
        ProjectionState.INCOMPLETE.value: counts[ProjectionState.INCOMPLETE.value],
        ProjectionState.MISSING.value: counts[ProjectionState.MISSING.value],
        "unresolved_projection_player": counts["unresolved_projection_player"],
    }
    usable = canonical_counts[ProjectionState.VALID.value] + canonical_counts[
        ProjectionState.VERIFIED_BLANK.value
    ]
    resolved_players = [
        by_id[pick.element_id]
        for pick in state.picks
        if pick.element_id in by_id
    ]
    if len(resolved_players) == 15:
        club_counts = Counter(player.team_id for player in resolved_players)
        over_limit = {team: count for team, count in club_counts.items() if count > 3}
        if over_limit:
            raise EditableManagerError(
                f"manual squad exceeds the three-per-club limit: {over_limit}"
            )
    optimized: DecisionResult | None = None
    bench_order: tuple[int, ...] = ()
    policy_eligible = sum(
        projection_eligible_for_policy(player, decision_policy)
        for player in resolved_players
    )
    if policy_eligible == 15:
        try:
            squad = resolve_existing_squad(
                projections,
                (pick.element_id for pick in state.picks),
                decision_policy=decision_policy,
            )
            optimized = optimize_xi(squad, decision_policy=decision_policy)
        except DecisionError as exc:
            raise EditableManagerError(
                f"manual squad failed Task 014 fixed-squad validation: {exc}"
            ) from exc
        bench_order = tuple(
            row.player.fpl_player_id
            for row in optimized.selections
            if not row.is_starter
        )
    if state.selling_prices_verified:
        transfer_status = "verified_sell_prices_present_transfer_optimization_not_implemented"
        transfer_reason = (
            "Verified sell values are present, but Task 015B intentionally does not implement "
            "transfer optimization."
        )
    else:
        transfer_status = TRANSFER_BLOCKED_STATUS
        transfer_reason = (
            "A legal affordability calculation requires the manager-specific selling value "
            "of each possible outgoing player; current market prices are not substitutes."
        )
    return EditableDecisionResult(
        state=state,
        projections=projections,
        reconciliation=tuple(reconciliation),
        reconciliation_counts=canonical_counts,
        projection_coverage_pct=100.0 * usable / 15,
        decision_policy=decision_policy,
        decision_policy_coverage_pct=100.0 * policy_eligible / 15,
        optimized_result=optimized,
        bench_order=bench_order,
        benchmark=benchmark,
        transfer_feasibility_status=transfer_status,
        transfer_feasibility_reason=transfer_reason,
    )


def editable_decision_payload(result: EditableDecisionResult) -> dict[str, Any]:
    state = result.state
    projections = result.projections
    optimized = result.optimized_result
    benchmark = result.benchmark
    all_problem_ids = {
        state_name: [
            row["element_id"]
            for row in result.reconciliation
            if row["projection_state"] == state_name
        ]
        for state_name in (
            ProjectionState.INCOMPLETE.value,
            ProjectionState.MISSING.value,
            "unresolved_projection_player",
        )
    }
    blocking_ids = {
        state_name: [
            row["element_id"]
            for row in result.reconciliation
            if row["projection_state"] == state_name
            and not row["decision_policy_eligible"]
        ]
        for state_name in (
            ProjectionState.INCOMPLETE.value,
            ProjectionState.MISSING.value,
            "unresolved_projection_player",
        )
    }
    admitted_incomplete = [
        {
            "element_id": row["element_id"],
            "name": row["projection_player_name"] or row["manual_display_name"],
            "projection": row["projection"],
            "prediction_complete": False,
        }
        for row in result.reconciliation
        if row["admitted_incomplete_under_policy"]
    ]
    incomplete_ids = {row["element_id"] for row in admitted_incomplete}
    incomplete_xi: list[dict[str, Any]] = []
    incomplete_captain: dict[str, Any] | None = None
    incomplete_vice: dict[str, Any] | None = None
    incomplete_base = 0.0
    incomplete_captain_bonus = 0.0
    if optimized is not None:
        for selection in optimized.selections:
            player = selection.player
            if player.fpl_player_id not in incomplete_ids:
                continue
            detail = {
                "element_id": player.fpl_player_id,
                "name": player.player_name,
                "projection": player.projection,
                "prediction_complete": False,
            }
            if selection.is_starter:
                incomplete_xi.append(detail)
            if selection.is_captain:
                incomplete_captain = detail
            if selection.is_vice_captain:
                incomplete_vice = detail
            incomplete_base += selection.base_contribution
            incomplete_captain_bonus += selection.captain_bonus
    return {
        "version": EDITABLE_DECISION_VERSION,
        "classification": EDITABLE_DECISION_CLASSIFICATION,
        "model_caveat": MODEL_CAVEAT,
        "decision_policy": result.decision_policy,
        "decision_policy_version": DECISION_POLICY_VERSION,
        "decision_policy_is_default": result.decision_policy == DEFAULT_DECISION_POLICY,
        "appearance_only_policy_caveat": (
            APPEARANCE_ONLY_CAVEAT
            if result.decision_policy == APPEARANCE_ONLY_ALLOWED_POLICY
            else None
        ),
        "entry_id": state.entry_id,
        "season": state.season,
        "target_gameweek": state.target_gameweek,
        "manual_state": {
            "artifact_path": str(state.artifact_path.resolve()),
            "artifact_sha256": state.artifact_sha256,
            "verification_source": state.verification_source,
            "verification_timestamp": state.verification_timestamp,
            "recorded_timestamp": state.recorded_timestamp,
            "bank_units": state.bank_units,
            "free_transfers": state.free_transfers,
            "current_transfer_cost_points": state.current_transfer_cost_points,
            "post_deadline_transfers_known": state.post_deadline_transfers_known,
            "selling_prices_verified": state.selling_prices_verified,
            "distinct_from_public_locked_state": True,
        },
        "projection": {
            "provider_id": projections.provider_id,
            "model_id": projections.source_model_id,
            "model_scope": projections.model_scope,
            "snapshot_timestamp": projections.snapshot_timestamp,
            "artifact_path": projections.source_artifact_path,
            "artifact_sha256": projections.source_artifact_sha256,
        },
        "reconciliation_counts": result.reconciliation_counts,
        "all_incomplete_missing_or_unresolved_player_ids": all_problem_ids,
        "blocking_projection_player_ids": blocking_ids,
        "projection_coverage_pct": result.projection_coverage_pct,
        "decision_policy_coverage_pct": result.decision_policy_coverage_pct,
        "reconciliation": list(result.reconciliation),
        "incomplete_projection_policy_diagnostics": {
            "incomplete_projections_admitted_to_squad": len(admitted_incomplete),
            "admitted_players": admitted_incomplete,
            "selected_in_starting_xi": incomplete_xi,
            "incomplete_captain": incomplete_captain,
            "incomplete_vice_captain": incomplete_vice,
            "base_xi_objective_contribution": incomplete_base,
            "captain_bonus_objective_contribution": incomplete_captain_bonus,
            "total_objective_contribution": (
                incomplete_base + incomplete_captain_bonus
            ),
            "prediction_complete_preserved": True,
            "caveat": (
                APPEARANCE_ONLY_CAVEAT
                if result.decision_policy == APPEARANCE_ONLY_ALLOWED_POLICY
                else None
            ),
        },
        "fixed_squad_optimization": (
            {
                "status": "complete",
                "decision_engine_version": DECISION_ENGINE_VERSION,
                "starting_xi": [
                    row.player.fpl_player_id
                    for row in optimized.selections
                    if row.is_starter
                ],
                "bench_order": list(result.bench_order),
                "bench_order_semantics": BENCH_ORDER_SEMANTICS,
                "formation": optimized.formation,
                "captain": {
                    "element_id": optimized.captain.fpl_player_id,
                    "name": optimized.captain.player_name,
                },
                "vice_captain": {
                    "element_id": optimized.vice_captain.fpl_player_id,
                    "name": optimized.vice_captain.player_name,
                },
                "base_xi_projection": optimized.base_xi_projection,
                "captain_bonus": optimized.captain_bonus,
                "total_objective": optimized.total_objective,
            }
            if optimized is not None
            else {
                "status": "blocked_by_decision_policy_ineligible_projection",
                "decision_policy": result.decision_policy,
                "blocking_projection_player_ids": blocking_ids,
                "bench_order": None,
                "bench_order_semantics": BENCH_ORDER_SEMANTICS,
            }
        ),
        "current_editable_selection_comparison": {
            "status": "not_available_not_manually_verified",
            "fabricated": False,
        },
        "unconstrained_benchmark": {
            "classification": benchmark.classification,
            "not_a_transfer_plan": True,
            "manifest_path": str(benchmark.manifest_path),
            "manifest_sha256": benchmark.manifest_sha256,
            "squad_path": str(benchmark.squad_path),
            "squad_sha256": benchmark.squad_sha256,
            "formation": benchmark.formation,
            "squad_cost_units": benchmark.squad_cost_units,
            "base_xi_projection": benchmark.base_xi_projection,
            "captain_bonus": benchmark.captain_bonus,
            "total_objective": benchmark.total_objective,
            "captain": benchmark.captain_name,
            "vice_captain": benchmark.vice_captain_name,
            "difference_from_current_squad_optimum": (
                {
                    "base_xi_projection": (
                        benchmark.base_xi_projection - optimized.base_xi_projection
                    ),
                    "captain_bonus": benchmark.captain_bonus - optimized.captain_bonus,
                    "total_objective": benchmark.total_objective - optimized.total_objective,
                    "classification": "informational_projection_ceiling_difference",
                }
                if optimized is not None
                else None
            ),
        },
        "transfer_analysis": {
            "status": result.transfer_feasibility_status,
            "reason": result.transfer_feasibility_reason,
            "free_transfers": state.free_transfers,
            "bank_units": state.bank_units,
            "current_transfer_cost_points": state.current_transfer_cost_points,
            "current_market_price_used_as_sell_price": False,
            "third_party_price_change_metadata_effect": "none",
            "recommendation": None,
        },
    }


def write_editable_decision(
    result: EditableDecisionResult,
    *,
    decision_data_root: Path = Path("data/manager/decisions/fpl"),
) -> EditableDecisionArtifacts:
    state = result.state
    record_directory = state.artifact_path.parent.name
    directory = (
        decision_data_root
        / state.season
        / f"entry={state.entry_id}"
        / f"gameweek={state.target_gameweek}"
        / record_directory
        / EDITABLE_DECISION_VERSION
        / f"policy={result.decision_policy}"
    )
    if directory.exists():
        raise EditableManagerOutputExistsError(
            f"editable decision output already exists: {directory}"
        )
    directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = directory.parent / f".{EDITABLE_DECISION_VERSION}.{uuid.uuid4().hex}.tmp"
    temporary.mkdir()
    try:
        path = temporary / "current_editable_squad_decision.json"
        with path.open("x", encoding="utf-8") as output:
            json.dump(editable_decision_payload(result), output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.rename(directory)
    except Exception:
        for child in temporary.glob("*"):
            child.unlink()
        if temporary.exists():
            temporary.rmdir()
        raise
    path = directory / "current_editable_squad_decision.json"
    return EditableDecisionArtifacts(
        directory=directory,
        decision_path=path,
        decision_sha256=sha256_file(path),
    )
