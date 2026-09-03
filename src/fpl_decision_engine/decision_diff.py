"""Deterministic read-only differences between trusted completed Engine-v1 runs."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, Sequence

import duckdb
from jsonschema import Draft202012Validator

from .decision_journal import DecisionJournalError, _load_completed_evidence
from .operational_runner import (
    OperationalRunnerError,
    _validate_preparation_directory,
    _write_atomic,
)
from .projection_provider import (
    ProjectionProviderError,
    ProjectionState,
    XfpV01ParquetProvider,
    sha256_file,
)


DECISION_DIFF_SCHEMA_NAME = "DecisionDiff"
DECISION_DIFF_SCHEMA_VERSION = "1.0.0"
DECISION_DIFF_CLASSIFICATION = "read_only_trusted_decision_diff"
SCHEMA_RESOURCE_PARTS = ("schemas", "decision_diff_v1.schema.json")
_ID_RE = re.compile(r"^decision_diff_[0-9a-f]{64}$")
_PREPARATION_ID_RE = re.compile(r"^prep_[0-9a-f]{64}$")
_DECISION_ID_RE = re.compile(r"^decision_[0-9a-f]{64}$")


class DecisionDiffErrorCode(str, Enum):
    LEFT_RUN_INVALID = "LEFT_RUN_INVALID"
    RIGHT_RUN_INVALID = "RIGHT_RUN_INVALID"
    TRUST_CHAIN_HASH_MISMATCH = "TRUST_CHAIN_HASH_MISMATCH"
    DIFFERENT_SEASON = "DIFFERENT_SEASON"
    DIFFERENT_TARGET_GAMEWEEK = "DIFFERENT_TARGET_GAMEWEEK"
    DIFFERENT_OFFICIAL_DEADLINE = "DIFFERENT_OFFICIAL_DEADLINE"
    MALFORMED_ARTIFACT = "MALFORMED_ARTIFACT"
    UNSUPPORTED_CONTRACT_VERSION = "UNSUPPORTED_CONTRACT_VERSION"
    NON_FINITE_NUMBER = "NON_FINITE_NUMBER"
    IMMUTABLE_PUBLICATION_CONFLICT = "IMMUTABLE_PUBLICATION_CONFLICT"


class DecisionDiffError(ValueError):
    """Fail-closed Decision Diff error carrying a stable machine code."""

    def __init__(self, code: DecisionDiffErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, order=True)
class _OfficialPlayer:
    element_id: int
    name: str
    team_id: int
    team_name: str
    team_short_name: str
    position_id: int
    position: str
    price_units: int
    status: str
    chance_of_playing_next_round: int | None
    news: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "chance_of_playing_next_round": self.chance_of_playing_next_round,
            "element_id": self.element_id,
            "name": self.name,
            "news": self.news,
            "position": self.position,
            "position_id": self.position_id,
            "price_units": self.price_units,
            "status": self.status,
            "team_id": self.team_id,
            "team_name": self.team_name,
            "team_short_name": self.team_short_name,
        }


@dataclass(frozen=True, order=True)
class _Projection:
    element_id: int
    name: str
    projected_xfp: float | None
    expected_minutes: float | None
    projection_state: str
    prediction_complete: bool
    attacking_rate_available: bool
    low_sample: bool
    fixture_count: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "attacking_rate_available": self.attacking_rate_available,
            "element_id": self.element_id,
            "expected_minutes": self.expected_minutes,
            "fixture_count": self.fixture_count,
            "low_sample": self.low_sample,
            "name": self.name,
            "prediction_complete": self.prediction_complete,
            "projected_xfp": self.projected_xfp,
            "projection_state": self.projection_state,
        }


@dataclass(frozen=True)
class _ManagerState:
    entry_id: int
    squad: tuple[int, ...]
    bank_units: int
    free_transfers: int
    transfer_cost_points: int
    chip_state: str
    selling_prices: tuple[tuple[int, str, int], ...]


@dataclass(frozen=True)
class _TrustedRun:
    season: str
    target_gameweek: int
    official_deadline: str
    preparation_id: str
    decision_id: str
    run_fields: tuple[tuple[str, str | None], ...]
    official_players: tuple[_OfficialPlayer, ...]
    projections: tuple[_Projection, ...]
    manager: _ManagerState
    action: Mapping[str, Any]
    reliability: Mapping[str, Any]


@dataclass(frozen=True)
class DecisionDiff:
    """A validated immutable-in-use DecisionDiff v1 value."""

    payload: Mapping[str, Any]

    @property
    def decision_diff_id(self) -> str:
        return str(self.payload["decision_diff_id"])

    def to_payload(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.payload, allow_nan=False))

    def canonical_bytes(self) -> bytes:
        return serialize_decision_diff(self.payload)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


@dataclass(frozen=True)
class DecisionDiffArtifacts:
    decision_diff_id: str
    directory: Path
    artifact_path: Path
    artifact_sha256: str
    reused: bool
    summary: Mapping[str, bool]


def _non_finite(value: Any, label: str = "DecisionDiff") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DecisionDiffError(
                DecisionDiffErrorCode.NON_FINITE_NUMBER,
                f"{label} contains a non-finite number",
            )
        return
    if isinstance(value, list) or isinstance(value, tuple):
        for index, item in enumerate(value):
            _non_finite(item, f"{label}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DecisionDiffError(
                    DecisionDiffErrorCode.MALFORMED_ARTIFACT,
                    f"{label} contains a non-string key",
                )
            _non_finite(item, f"{label}.{key}")
        return
    raise DecisionDiffError(
        DecisionDiffErrorCode.MALFORMED_ARTIFACT,
        f"{label} contains unsupported type {type(value).__name__}",
    )


def _schema() -> Mapping[str, Any]:
    try:
        raw = resources.files("fpl_decision_engine.presentation").joinpath(
            *SCHEMA_RESOURCE_PARTS
        ).read_bytes()
        payload = json.loads(raw)
    except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            f"DecisionDiff schema is unavailable or invalid: {exc}",
        ) from exc
    if not isinstance(payload, Mapping):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "DecisionDiff schema must be a JSON object",
        )
    return payload


def _semantic_identity_payload(
    *,
    season: str,
    target_gameweek: int,
    official_deadline: str,
    left_preparation_id: str,
    left_decision_id: str,
    right_preparation_id: str,
    right_decision_id: str,
) -> dict[str, Any]:
    return {
        "left_decision_id": left_decision_id,
        "left_preparation_id": left_preparation_id,
        "official_deadline": official_deadline,
        "right_decision_id": right_decision_id,
        "right_preparation_id": right_preparation_id,
        "schema_version": DECISION_DIFF_SCHEMA_VERSION,
        "season": season,
        "target_gameweek": target_gameweek,
    }


def build_decision_diff_id(**identity: Any) -> str:
    """Build an ordered, path/clock/environment-independent semantic ID."""
    if not isinstance(identity.get("season"), str) or not identity["season"]:
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT, "season is invalid"
        )
    gameweek = identity.get("target_gameweek")
    if isinstance(gameweek, bool) or not isinstance(gameweek, int) or not 1 <= gameweek <= 38:
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT, "target_gameweek is invalid"
        )
    for field, pattern in (
        ("left_preparation_id", _PREPARATION_ID_RE),
        ("right_preparation_id", _PREPARATION_ID_RE),
        ("left_decision_id", _DECISION_ID_RE),
        ("right_decision_id", _DECISION_ID_RE),
    ):
        if not isinstance(identity.get(field), str) or not pattern.fullmatch(identity[field]):
            raise DecisionDiffError(
                DecisionDiffErrorCode.MALFORMED_ARTIFACT, f"{field} is invalid"
            )
    semantic = _semantic_identity_payload(**identity)
    raw = json.dumps(
        semantic,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return "decision_diff_" + hashlib.sha256(raw).hexdigest()


def _price_units(value: Any, player_id: int) -> int:
    try:
        decimal = Decimal(str(value)) * Decimal(10)
    except (InvalidOperation, ValueError) as exc:
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            f"player {player_id} price is invalid",
        ) from exc
    if not decimal.is_finite() or decimal < 0 or decimal != decimal.to_integral_value():
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            f"player {player_id} price is not an exact non-negative £0.1m value",
        )
    return int(decimal)


def _load_official_players(path: Path) -> tuple[_OfficialPlayer, ...]:
    connection = duckdb.connect(":memory:")
    try:
        rows = connection.execute(
            """SELECT fpl_player_id, web_name, team_id, team_name, team_short_name,
                      position_id, position, price_m, status,
                      chance_of_playing_next_round, news
                 FROM read_parquet(?)
             ORDER BY fpl_player_id""",
            [str(path)],
        ).fetchall()
    except duckdb.Error as exc:
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            f"could not read frozen official player state: {exc}",
        ) from exc
    finally:
        connection.close()
    result: list[_OfficialPlayer] = []
    for row in rows:
        (
            element_id,
            name,
            team_id,
            team_name,
            team_short_name,
            position_id,
            position,
            price_m,
            status,
            chance,
            news,
        ) = row
        required = (
            element_id,
            name,
            team_id,
            team_name,
            team_short_name,
            position_id,
            position,
            price_m,
            status,
            news,
        )
        if any(value is None for value in required):
            raise DecisionDiffError(
                DecisionDiffErrorCode.MALFORMED_ARTIFACT,
                f"official player state is incomplete for element {element_id!r}",
            )
        if chance is not None and (
            isinstance(chance, bool) or not isinstance(chance, int) or not 0 <= chance <= 100
        ):
            raise DecisionDiffError(
                DecisionDiffErrorCode.MALFORMED_ARTIFACT,
                f"player {element_id} chance_of_playing_next_round is invalid",
            )
        result.append(
            _OfficialPlayer(
                element_id=int(element_id),
                name=str(name),
                team_id=int(team_id),
                team_name=str(team_name),
                team_short_name=str(team_short_name),
                position_id=int(position_id),
                position=str(position),
                price_units=_price_units(price_m, int(element_id)),
                status=str(status),
                chance_of_playing_next_round=(int(chance) if chance is not None else None),
                news=str(news),
            )
        )
    if not result or len({row.element_id for row in result}) != len(result):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "official player state must contain unique element IDs",
        )
    return tuple(result)


def _load_projections(
    projection_path: Path,
    players_path: Path,
    *,
    season: str,
    target_gameweek: int,
) -> tuple[_Projection, ...]:
    try:
        dataset = XfpV01ParquetProvider(
            projection_artifact=projection_path,
            players_artifact=players_path,
        ).load(season=season, target_gameweek=target_gameweek)
    except ProjectionProviderError as exc:
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            f"frozen projection artifact is invalid: {exc}",
        ) from exc
    connection = duckdb.connect(":memory:")
    try:
        raw_rows = connection.execute(
            """SELECT fpl_player_id, attacking_rate_available,
                      prediction_complete, low_sample, fixture_count
                 FROM read_parquet(?)
             ORDER BY fpl_player_id""",
            [str(projection_path)],
        ).fetchall()
    except duckdb.Error as exc:
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            f"could not read frozen projection diagnostics: {exc}",
        ) from exc
    finally:
        connection.close()
    diagnostics = {int(row[0]): row[1:] for row in raw_rows}
    if len(diagnostics) != len(raw_rows) or len(diagnostics) != len(dataset.players):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "projection diagnostics do not cover the projection universe exactly",
        )
    result = []
    for player in dataset.players:
        raw = diagnostics.get(player.fpl_player_id)
        if raw is None or any(value is None for value in raw):
            raise DecisionDiffError(
                DecisionDiffErrorCode.MALFORMED_ARTIFACT,
                f"projection diagnostics are incomplete for player {player.fpl_player_id}",
            )
        attacking_available, prediction_complete, low_sample, fixture_count = raw
        expected_complete = player.projection_state in {
            ProjectionState.VALID,
            ProjectionState.VERIFIED_BLANK,
        }
        if bool(prediction_complete) != expected_complete:
            raise DecisionDiffError(
                DecisionDiffErrorCode.MALFORMED_ARTIFACT,
                f"projection completeness disagrees for player {player.fpl_player_id}",
            )
        result.append(
            _Projection(
                element_id=player.fpl_player_id,
                name=player.player_name,
                projected_xfp=player.projection,
                expected_minutes=player.expected_minutes,
                projection_state=player.projection_state.value,
                prediction_complete=bool(prediction_complete),
                attacking_rate_available=bool(attacking_available),
                low_sample=bool(low_sample),
                fixture_count=int(fixture_count),
            )
        )
    return tuple(result)


def _source_hashes(gameweek: Mapping[str, Any]) -> dict[str, str]:
    rows = gameweek.get("source_artifacts")
    if not isinstance(rows, list):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "GameweekDecision source_artifacts is invalid",
        )
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise DecisionDiffError(
                DecisionDiffErrorCode.MALFORMED_ARTIFACT,
                "GameweekDecision source artifact is invalid",
            )
        role, digest = row.get("role"), row.get("sha256")
        if not isinstance(role, str) or not isinstance(digest, str) or role in result:
            raise DecisionDiffError(
                DecisionDiffErrorCode.MALFORMED_ARTIFACT,
                "GameweekDecision source artifact roles/hashes are invalid",
            )
        result[role] = digest
    return result


def _required_artifact_hash(
    artifacts: Sequence[Any], role: str, label: str
) -> str:
    matches = [item.sha256 for item in artifacts if item.role == role]
    if len(matches) != 1:
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            f"{label} must contain exactly one {role!r} artifact",
        )
    return matches[0]


def _manager_state(gameweek: Mapping[str, Any]) -> _ManagerState:
    manager = gameweek.get("manager_state")
    players = gameweek.get("players")
    if not isinstance(manager, Mapping) or not isinstance(players, list):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "GameweekDecision manager/player state is invalid",
        )
    squad = manager.get("squad")
    if not isinstance(squad, list):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "GameweekDecision manager squad is invalid",
        )
    registry = {
        int(row["element_id"]): row
        for row in players
        if isinstance(row, Mapping) and isinstance(row.get("element_id"), int)
    }
    selling = []
    for element_id in squad:
        row = registry.get(element_id)
        if row is None or not isinstance(row.get("selling_price_units"), int):
            raise DecisionDiffError(
                DecisionDiffErrorCode.MALFORMED_ARTIFACT,
                f"manager selling price is missing for player {element_id}",
            )
        selling.append((element_id, str(row.get("name")), row["selling_price_units"]))
    return _ManagerState(
        entry_id=int(manager["entry_id"]),
        squad=tuple(sorted(int(value) for value in squad)),
        bank_units=int(manager["bank_units"]),
        free_transfers=int(manager["free_transfers"]),
        transfer_cost_points=int(manager["current_transfer_cost_points"]),
        chip_state=str(manager["chip_state"]),
        selling_prices=tuple(sorted(selling)),
    )


def _selection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT, "engine selection is invalid"
        )
    objective = value.get("objective")
    if not isinstance(objective, Mapping):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "engine selection objective is invalid",
        )
    return {
        "base_xi_xfp": float(objective["base_xi_xfp"]),
        "bench": sorted(int(item) for item in value["bench"]),
        "captain": int(value["captain"]),
        "captain_bonus_xfp": float(objective["captain_bonus_xfp"]),
        "formation": str(value["formation"]),
        "squad": sorted(int(item) for item in value["squad"]),
        "starting_xi": sorted(int(item) for item in value["starting_xi"]),
        "total_xfp": float(objective["total_xfp"]),
        "vice_captain": int(value["vice_captain"]),
    }


def _engine_action(gameweek: Mapping[str, Any], manager: _ManagerState) -> dict[str, Any]:
    action = gameweek.get("recommended_action")
    if not isinstance(action, Mapping):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "GameweekDecision recommendation is invalid",
        )
    action_type = action.get("action_type")
    if action_type == "ROLL":
        outgoing_id = incoming_id = None
        outgoing_name = incoming_name = None
        resulting_bank = manager.bank_units
        transfer_cost = 0
    elif action_type == "TRANSFER":
        outgoing = action.get("outgoing")
        incoming = action.get("incoming")
        if not isinstance(outgoing, Mapping) or not isinstance(incoming, Mapping):
            raise DecisionDiffError(
                DecisionDiffErrorCode.MALFORMED_ARTIFACT,
                "GameweekDecision transfer players are invalid",
            )
        outgoing_id = int(outgoing["element_id"])
        incoming_id = int(incoming["element_id"])
        outgoing_name = str(outgoing["name"])
        incoming_name = str(incoming["name"])
        resulting_bank = int(action["resulting_bank_units"])
        transfer_cost = int(action["transfer_cost_points"])
    else:
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            f"unsupported action type {action_type!r}",
        )
    return {
        "action_type": action_type,
        "incoming_element_id": incoming_id,
        "incoming_name": incoming_name,
        "objective_gain_vs_roll_xfp": float(action["objective_gain_vs_roll_xfp"]),
        "outgoing_element_id": outgoing_id,
        "outgoing_name": outgoing_name,
        "resulting_bank_units": resulting_bank,
        "selection": _selection(action["selection"]),
        "transfer_cost_points": transfer_cost,
    }


def _reliability(gameweek: Mapping[str, Any]) -> dict[str, Any]:
    source = gameweek.get("reliability")
    if not isinstance(source, Mapping):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "GameweekDecision reliability section is invalid",
        )
    views = source.get("sensitivity_results")
    warnings = source.get("warnings")
    if not isinstance(views, list) or not isinstance(warnings, list):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "GameweekDecision reliability views/warnings are invalid",
        )
    normalized_views = []
    for row in views:
        if not isinstance(row, Mapping):
            raise DecisionDiffError(
                DecisionDiffErrorCode.MALFORMED_ARTIFACT,
                "GameweekDecision reliability view is invalid",
            )
        normalized_views.append(
            {
                "action_type": str(row["action_type"]),
                "category": str(row["category"]),
                "gain_vs_roll_xfp": float(row["gain_vs_roll_xfp"]),
                "incoming_element_id": row["incoming_element_id"],
                "objective_xfp": float(row["objective_xfp"]),
                "outgoing_element_id": row["outgoing_element_id"],
                "retains_official_exact_action": bool(
                    row["retains_official_exact_action"]
                ),
                "view_id": str(row["view_id"]),
            }
        )
    if len({row["view_id"] for row in normalized_views}) != len(normalized_views):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "reliability view IDs must be unique",
        )
    warning_codes = []
    for warning in warnings:
        if not isinstance(warning, Mapping) or not isinstance(warning.get("code"), str):
            raise DecisionDiffError(
                DecisionDiffErrorCode.MALFORMED_ARTIFACT,
                "reliability warning is invalid",
            )
        warning_codes.append(warning["code"])
    return {
        "captaincy_materially_changes_gain": bool(
            source["captaincy_materially_changes_gain"]
        ),
        "changed_action_count": int(source["changed_action_count"]),
        "same_exact_action_count": int(source["same_exact_action_count"]),
        "sensitivity_view_count": int(source["sensitivity_view_count"]),
        "sensitivity_results": sorted(normalized_views, key=lambda row: row["view_id"]),
        "warning_codes": sorted(set(warning_codes)),
    }


def _trusted_run(final_manifest_path: Path, side: str) -> _TrustedRun:
    invalid_code = (
        DecisionDiffErrorCode.LEFT_RUN_INVALID
        if side == "left"
        else DecisionDiffErrorCode.RIGHT_RUN_INVALID
    )
    try:
        completed = _load_completed_evidence(final_manifest_path)
        preparation_directory = completed.decision_directory.parent.parent
        preparation = _validate_preparation_directory(preparation_directory)
        if preparation != completed.preparation:
            raise DecisionDiffError(
                DecisionDiffErrorCode.TRUST_CHAIN_HASH_MISMATCH,
                f"{side} preparation validators disagree",
            )
        gameweek = completed.gameweek_payload
        source_hashes = _source_hashes(gameweek)
        expected_sources = {
            "frozen_features": _required_artifact_hash(
                preparation.feature_artifacts,
                "player_gameweek_features",
                "preparation feature artifacts",
            ),
            "frozen_players": preparation.frozen_player_artifact_sha256,
            "frozen_projections": _required_artifact_hash(
                preparation.prediction_artifacts,
                "xfp_v01_gameweek",
                "preparation prediction artifacts",
            ),
        }
        if any(source_hashes.get(role) != digest for role, digest in expected_sources.items()):
            raise DecisionDiffError(
                DecisionDiffErrorCode.TRUST_CHAIN_HASH_MISMATCH,
                f"{side} GameweekDecision sources do not match its preparation",
            )
        artifact_root = preparation_directory / "artifacts"
        players_path = artifact_root / "players.parquet"
        projection_path = artifact_root / "xfp_v01_gameweek.parquet"
        season = gameweek.get("season")
        if not isinstance(season, str) or not season:
            raise DecisionDiffError(
                DecisionDiffErrorCode.MALFORMED_ARTIFACT,
                f"{side} GameweekDecision season is invalid",
            )
        manager = _manager_state(gameweek)
        fixture_hash = next(
            (
                item.artifact_sha256
                for item in preparation.accepted_evidence
                if item.source == "official_fpl_fixtures"
            ),
            None,
        )
        if fixture_hash is None:
            raise DecisionDiffError(
                DecisionDiffErrorCode.MALFORMED_ARTIFACT,
                f"{side} preparation lacks official fixture evidence",
            )
        fields: dict[str, str | None] = {
            "bootstrap_sha256": preparation.frozen_snapshot.artifact_sha256,
            "candidate_artifact_sha256": completed.final_payload[
                "candidate_artifact_sha256"
            ],
            "decision_id": completed.final_payload["decision_id"],
            "feature_artifact_sha256": expected_sources["frozen_features"],
            "final_operational_manifest_sha256": completed.final_sha256,
            "fixture_prediction_sha256": _required_artifact_hash(
                preparation.prediction_artifacts,
                "xfp_v01_fixtures",
                "preparation prediction artifacts",
            ),
            "fixtures_sha256": fixture_hash,
            "gameweek_decision_sha256": completed.final_payload[
                "gameweek_decision_contract_sha256"
            ],
            "gameweek_prediction_sha256": expected_sources["frozen_projections"],
            "manager_evidence_source_sha256": completed.final_payload[
                "manager_evidence_source_sha256"
            ],
            "manager_state_sha256": completed.final_payload["manager_state_sha256"],
            "manager_verification_timestamp": completed.final_payload[
                "manager_verification_timestamp"
            ],
            "one_transfer_decision_sha256": completed.final_payload[
                "one_transfer_decision_sha256"
            ],
            "preparation_id": preparation.preparation_id,
            "preparation_manifest_sha256": preparation.sha256,
            "refresh_manifest_sha256": preparation.refresh_manifest_sha256,
            "reliability_artifact_sha256": completed.final_payload[
                "reliability_artifact_sha256"
            ],
            "version.final_manifest": completed.final_payload["schema_version"],
            "version.gameweek_decision": completed.final_payload[
                "gameweek_decision_schema_version"
            ],
            "version.preparation_manifest": preparation.schema_version,
        }
        for producer, version in preparation.producer_versions:
            fields[f"version.producer.{producer}"] = version
        engine = gameweek.get("engine")
        if isinstance(engine, Mapping):
            for name, version in engine.items():
                fields[f"version.engine.{name}"] = str(version)
        return _TrustedRun(
            season=season,
            target_gameweek=int(completed.final_payload["target_gameweek"]),
            official_deadline=str(completed.final_payload["official_deadline"]),
            preparation_id=preparation.preparation_id,
            decision_id=str(completed.final_payload["decision_id"]),
            run_fields=tuple(sorted(fields.items())),
            official_players=_load_official_players(players_path),
            projections=_load_projections(
                projection_path,
                players_path,
                season=season,
                target_gameweek=int(completed.final_payload["target_gameweek"]),
            ),
            manager=manager,
            action=_engine_action(gameweek, manager),
            reliability=_reliability(gameweek),
        )
    except DecisionDiffError:
        raise
    except (DecisionJournalError, OperationalRunnerError, KeyError, TypeError, ValueError) as exc:
        message = str(exc)
        code = (
            DecisionDiffErrorCode.TRUST_CHAIN_HASH_MISMATCH
            if "hash" in message.lower() or "deterministically rebuild" in message.lower()
            else invalid_code
        )
        raise DecisionDiffError(code, f"{side} trusted run is invalid: {message}") from exc


def _map(values: Sequence[Any]) -> dict[int, Any]:
    return {value.element_id: value for value in values}


def _official_state_diff(left: _TrustedRun, right: _TrustedRun) -> dict[str, Any]:
    left_map, right_map = _map(left.official_players), _map(right.official_players)
    added = [right_map[key].to_payload() for key in sorted(right_map.keys() - left_map)]
    removed = [left_map[key].to_payload() for key in sorted(left_map.keys() - right_map)]
    changed = []
    for element_id in sorted(left_map.keys() & right_map):
        before, after = left_map[element_id], right_map[element_id]
        if before == after:
            continue
        before_payload, after_payload = before.to_payload(), after.to_payload()
        fields = sorted(
            key
            for key in before_payload
            if key != "element_id"
            and before_payload[key] != after_payload[key]
        )
        changed.append(
            {
                "after": after_payload,
                "before": before_payload,
                "changed_fields": fields,
                "element_id": element_id,
                "name": after.name,
                "price_delta_units": after.price_units - before.price_units,
            }
        )
    return {
        "added": added,
        "changed": changed,
        "changed_count": len(added) + len(removed) + len(changed),
        "removed": removed,
    }


def _projection_diff(left: _TrustedRun, right: _TrustedRun) -> dict[str, Any]:
    left_map, right_map = _map(left.projections), _map(right.projections)
    added = [right_map[key].to_payload() for key in sorted(right_map.keys() - left_map)]
    removed = [left_map[key].to_payload() for key in sorted(left_map.keys() - right_map)]
    changed = []
    for element_id in sorted(left_map.keys() & right_map):
        before, after = left_map[element_id], right_map[element_id]
        if before == after:
            continue
        before_payload, after_payload = before.to_payload(), after.to_payload()
        delta = (
            after.projected_xfp - before.projected_xfp
            if before.projected_xfp is not None and after.projected_xfp is not None
            else None
        )
        changed.append(
            {
                "after": after_payload,
                "before": before_payload,
                "changed_fields": sorted(
                    key
                    for key in before_payload
                    if key != "element_id"
                    and before_payload[key] != after_payload[key]
                ),
                "element_id": element_id,
                "name": after.name,
                "xfp_delta": delta,
            }
        )
    return {
        "added": added,
        "changed": changed,
        "changed_count": len(added) + len(removed) + len(changed),
        "removed": removed,
    }


def _field_changes(left: Mapping[str, Any], right: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {"field": field, "left": left.get(field), "right": right.get(field)}
        for field in sorted(set(left) | set(right))
        if left.get(field) != right.get(field)
    ]


def _run_diff(left: _TrustedRun, right: _TrustedRun) -> dict[str, Any]:
    left_fields, right_fields = dict(left.run_fields), dict(right.run_fields)
    fields = [
        {
            "changed": left_fields.get(field) != right_fields.get(field),
            "field": field,
            "left": left_fields.get(field),
            "right": right_fields.get(field),
        }
        for field in sorted(set(left_fields) | set(right_fields))
    ]
    return {"changed": any(row["changed"] for row in fields), "fields": fields}


def _manager_diff(left: _TrustedRun, right: _TrustedRun) -> dict[str, Any]:
    left_manager, right_manager = left.manager, right.manager
    left_fields = {
        "bank_units": left_manager.bank_units,
        "chip_state": left_manager.chip_state,
        "entry_id": left_manager.entry_id,
        "free_transfers": left_manager.free_transfers,
        "transfer_cost_points": left_manager.transfer_cost_points,
    }
    right_fields = {
        "bank_units": right_manager.bank_units,
        "chip_state": right_manager.chip_state,
        "entry_id": right_manager.entry_id,
        "free_transfers": right_manager.free_transfers,
        "transfer_cost_points": right_manager.transfer_cost_points,
    }
    left_prices = {row[0]: row for row in left_manager.selling_prices}
    right_prices = {row[0]: row for row in right_manager.selling_prices}
    price_changes = []
    for element_id in sorted(set(left_prices) | set(right_prices)):
        before, after = left_prices.get(element_id), right_prices.get(element_id)
        before_price = before[2] if before else None
        after_price = after[2] if after else None
        if before_price != after_price:
            price_changes.append(
                {
                    "element_id": element_id,
                    "left_price_units": before_price,
                    "name": (after or before)[1],
                    "right_price_units": after_price,
                }
            )
    field_changes = _field_changes(left_fields, right_fields)
    squad_added = sorted(set(right_manager.squad) - set(left_manager.squad))
    squad_removed = sorted(set(left_manager.squad) - set(right_manager.squad))
    semantic_changed = bool(field_changes or squad_added or squad_removed or price_changes)
    left_run, right_run = dict(left.run_fields), dict(right.run_fields)
    provenance_fields = {
        "manager_evidence_source_sha256",
        "manager_state_sha256",
        "manager_verification_timestamp",
    }
    provenance_changes = [
        {
            "field": field,
            "left": left_run.get(field),
            "right": right_run.get(field),
        }
        for field in sorted(provenance_fields)
        if left_run.get(field) != right_run.get(field)
    ]
    return {
        "field_changes": field_changes,
        "provenance_changed": bool(provenance_changes),
        "provenance_changes": provenance_changes,
        "selling_price_changes": price_changes,
        "semantic_changed": semantic_changed,
        "squad_added": squad_added,
        "squad_removed": squad_removed,
    }


def _numeric_change(field: str, left: float, right: float) -> dict[str, Any]:
    return {"delta": right - left, "field": field, "left": left, "right": right}


def _engine_diff(left: _TrustedRun, right: _TrustedRun) -> dict[str, Any]:
    left_action, right_action = dict(left.action), dict(right.action)
    left_selection = dict(left_action["selection"])
    right_selection = dict(right_action["selection"])
    identity_fields = (
        "action_type",
        "outgoing_element_id",
        "incoming_element_id",
        "resulting_bank_units",
        "transfer_cost_points",
        "objective_gain_vs_roll_xfp",
    )
    action_changes = [
        {"field": field, "left": left_action[field], "right": right_action[field]}
        for field in identity_fields
        if left_action[field] != right_action[field]
    ]
    left_starters, right_starters = set(left_selection["starting_xi"]), set(
        right_selection["starting_xi"]
    )
    left_bench, right_bench = set(left_selection["bench"]), set(right_selection["bench"])
    objective_changes = [
        _numeric_change(field, float(left_selection[field]), float(right_selection[field]))
        for field in ("base_xi_xfp", "captain_bonus_xfp", "total_xfp")
        if left_selection[field] != right_selection[field]
    ]
    lineup_changed = bool(
        left_starters != right_starters
        or left_bench != right_bench
        or left_selection["formation"] != right_selection["formation"]
    )
    captaincy_changed = bool(
        left_selection["captain"] != right_selection["captain"]
        or left_selection["vice_captain"] != right_selection["vice_captain"]
    )
    changed = bool(action_changes or lineup_changed or captaincy_changed or objective_changes)
    return {
        "action_changes": action_changes,
        "captain": {
            "changed": left_selection["captain"] != right_selection["captain"],
            "left": left_selection["captain"],
            "right": right_selection["captain"],
        },
        "captaincy_changed": captaincy_changed,
        "changed": changed,
        "left": {**left_action, "selection": left_selection},
        "lineup": {
            "bench_added": sorted(right_bench - left_bench),
            "bench_removed": sorted(left_bench - right_bench),
            "formation_changed": left_selection["formation"] != right_selection["formation"],
            "left_formation": left_selection["formation"],
            "right_formation": right_selection["formation"],
            "starters_added": sorted(right_starters - left_starters),
            "starters_removed": sorted(left_starters - right_starters),
        },
        "lineup_changed": lineup_changed,
        "objective_changes": objective_changes,
        "right": {**right_action, "selection": right_selection},
        "vice_captain": {
            "changed": left_selection["vice_captain"] != right_selection["vice_captain"],
            "left": left_selection["vice_captain"],
            "right": right_selection["vice_captain"],
        },
    }


def _reliability_diff(left: _TrustedRun, right: _TrustedRun) -> dict[str, Any]:
    left_rel, right_rel = dict(left.reliability), dict(right.reliability)
    headline_fields = (
        "sensitivity_view_count",
        "same_exact_action_count",
        "changed_action_count",
        "captaincy_materially_changes_gain",
    )
    headline_changes = [
        {"field": field, "left": left_rel[field], "right": right_rel[field]}
        for field in headline_fields
        if left_rel[field] != right_rel[field]
    ]
    left_views = {row["view_id"]: row for row in left_rel["sensitivity_results"]}
    right_views = {row["view_id"]: row for row in right_rel["sensitivity_results"]}
    added = [right_views[key] for key in sorted(right_views.keys() - left_views)]
    removed = [left_views[key] for key in sorted(left_views.keys() - right_views)]
    changed = [
        {"after": right_views[key], "before": left_views[key], "view_id": key}
        for key in sorted(left_views.keys() & right_views)
        if left_views[key] != right_views[key]
    ]
    left_warnings, right_warnings = set(left_rel["warning_codes"]), set(
        right_rel["warning_codes"]
    )
    reliability_changed = bool(
        headline_changes or added or removed or changed or left_warnings != right_warnings
    )
    return {
        "changed": reliability_changed,
        "headline_changes": headline_changes,
        "left_summary": {field: left_rel[field] for field in headline_fields},
        "right_summary": {field: right_rel[field] for field in headline_fields},
        "views_added": added,
        "views_changed": changed,
        "views_removed": removed,
        "warning_codes_added": sorted(right_warnings - left_warnings),
        "warning_codes_removed": sorted(left_warnings - right_warnings),
    }


def _compare_runs(left: _TrustedRun, right: _TrustedRun) -> DecisionDiff:
    if left.season != right.season:
        raise DecisionDiffError(
            DecisionDiffErrorCode.DIFFERENT_SEASON,
            f"trusted runs have different seasons: {left.season!r} != {right.season!r}",
        )
    if left.target_gameweek != right.target_gameweek:
        raise DecisionDiffError(
            DecisionDiffErrorCode.DIFFERENT_TARGET_GAMEWEEK,
            "trusted runs have different target gameweeks: "
            f"{left.target_gameweek} != {right.target_gameweek}",
        )
    if left.official_deadline != right.official_deadline:
        raise DecisionDiffError(
            DecisionDiffErrorCode.DIFFERENT_OFFICIAL_DEADLINE,
            "trusted runs have different official deadlines",
        )
    identity = {
        "season": left.season,
        "target_gameweek": left.target_gameweek,
        "official_deadline": left.official_deadline,
        "left_preparation_id": left.preparation_id,
        "left_decision_id": left.decision_id,
        "right_preparation_id": right.preparation_id,
        "right_decision_id": right.decision_id,
    }
    run = _run_diff(left, right)
    official = _official_state_diff(left, right)
    projections = _projection_diff(left, right)
    manager = _manager_diff(left, right)
    engine = _engine_diff(left, right)
    reliability = _reliability_diff(left, right)
    summary = {
        "captaincy_changed": engine["captaincy_changed"],
        "engine_action_changed": engine["changed"],
        "lineup_changed": engine["lineup_changed"],
        "manager_provenance_changed": manager["provenance_changed"],
        "manager_state_changed": manager["semantic_changed"],
        "official_state_changed": official["changed_count"] > 0,
        "projections_changed": projections["changed_count"] > 0,
        "reliability_changed": reliability["changed"],
        "run_provenance_changed": run["changed"],
    }
    payload = {
        "classification": DECISION_DIFF_CLASSIFICATION,
        "decision_diff_id": build_decision_diff_id(**identity),
        "engine_action": engine,
        "manager_state": manager,
        "official_player_state": official,
        "projections": projections,
        "reliability": reliability,
        "run_provenance": run,
        "schema_name": DECISION_DIFF_SCHEMA_NAME,
        "schema_version": DECISION_DIFF_SCHEMA_VERSION,
        "scope": {
            "left": {
                "decision_id": left.decision_id,
                "preparation_id": left.preparation_id,
            },
            "official_deadline": left.official_deadline,
            "right": {
                "decision_id": right.decision_id,
                "preparation_id": right.preparation_id,
            },
            "season": left.season,
            "target_gameweek": left.target_gameweek,
        },
        "summary": summary,
    }
    validate_decision_diff(payload)
    return DecisionDiff(payload)


def build_decision_diff(
    left_final_manifest_path: Path, right_final_manifest_path: Path
) -> DecisionDiff:
    """Validate two completed trust chains and build their deterministic diff."""
    left = _trusted_run(left_final_manifest_path, "left")
    right = _trusted_run(right_final_manifest_path, "right")
    return _compare_runs(left, right)


def _expected_summary(payload: Mapping[str, Any]) -> dict[str, bool]:
    engine = payload["engine_action"]
    manager = payload["manager_state"]
    official = payload["official_player_state"]
    projections = payload["projections"]
    reliability = payload["reliability"]
    run = payload["run_provenance"]
    return {
        "captaincy_changed": bool(engine["captaincy_changed"]),
        "engine_action_changed": bool(engine["changed"]),
        "lineup_changed": bool(engine["lineup_changed"]),
        "manager_provenance_changed": bool(manager["provenance_changed"]),
        "manager_state_changed": bool(manager["semantic_changed"]),
        "official_state_changed": int(official["changed_count"]) > 0,
        "projections_changed": int(projections["changed_count"]) > 0,
        "reliability_changed": bool(reliability["changed"]),
        "run_provenance_changed": bool(run["changed"]),
    }


def _validate_derived_fields(payload: Mapping[str, Any]) -> None:
    run = payload["run_provenance"]
    expected_run_changed = any(row["left"] != row["right"] for row in run["fields"])
    if any(row["changed"] != (row["left"] != row["right"]) for row in run["fields"]):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "run-provenance field flags do not reconcile",
        )
    if run["changed"] != expected_run_changed:
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "run-provenance changed flag does not reconcile",
        )

    for key in ("official_player_state", "projections"):
        section = payload[key]
        expected_count = sum(len(section[name]) for name in ("added", "removed", "changed"))
        if section["changed_count"] != expected_count:
            raise DecisionDiffError(
                DecisionDiffErrorCode.MALFORMED_ARTIFACT,
                f"{key} changed_count does not reconcile",
            )

    manager = payload["manager_state"]
    semantic = bool(
        manager["field_changes"]
        or manager["selling_price_changes"]
        or manager["squad_added"]
        or manager["squad_removed"]
    )
    if manager["semantic_changed"] != semantic or manager["provenance_changed"] != bool(
        manager["provenance_changes"]
    ):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "manager-state change flags do not reconcile",
        )

    engine = payload["engine_action"]
    left_selection = engine["left"]["selection"]
    right_selection = engine["right"]["selection"]
    lineup_changed = bool(
        set(left_selection["starting_xi"]) != set(right_selection["starting_xi"])
        or set(left_selection["bench"]) != set(right_selection["bench"])
        or left_selection["formation"] != right_selection["formation"]
    )
    captaincy_changed = bool(
        left_selection["captain"] != right_selection["captain"]
        or left_selection["vice_captain"] != right_selection["vice_captain"]
    )
    changed = bool(
        engine["action_changes"]
        or engine["objective_changes"]
        or lineup_changed
        or captaincy_changed
    )
    if (
        engine["lineup_changed"] != lineup_changed
        or engine["captaincy_changed"] != captaincy_changed
        or engine["changed"] != changed
        or engine["captain"]["changed"]
        != (left_selection["captain"] != right_selection["captain"])
        or engine["vice_captain"]["changed"]
        != (left_selection["vice_captain"] != right_selection["vice_captain"])
    ):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "engine-action change flags do not reconcile",
        )

    reliability = payload["reliability"]
    reliability_changed = bool(
        reliability["headline_changes"]
        or reliability["views_added"]
        or reliability["views_removed"]
        or reliability["views_changed"]
        or reliability["warning_codes_added"]
        or reliability["warning_codes_removed"]
    )
    if reliability["changed"] != reliability_changed:
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "reliability changed flag does not reconcile",
        )


def validate_decision_diff(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate strict schema, semantic identity, summary, and numeric safety."""
    if not isinstance(payload, Mapping):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "DecisionDiff must be a JSON object",
        )
    if payload.get("schema_version") != DECISION_DIFF_SCHEMA_VERSION:
        raise DecisionDiffError(
            DecisionDiffErrorCode.UNSUPPORTED_CONTRACT_VERSION,
            f"unsupported DecisionDiff schema version {payload.get('schema_version')!r}",
        )
    _non_finite(payload)
    errors = sorted(
        Draft202012Validator(_schema()).iter_errors(dict(payload)),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        details = "; ".join(
            f"{'/'.join(map(str, error.absolute_path)) or '<root>'}: {error.message}"
            for error in errors
        )
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            f"DecisionDiff v1 schema validation failed: {details}",
        )
    scope = payload["scope"]
    expected_id = build_decision_diff_id(
        season=scope["season"],
        target_gameweek=scope["target_gameweek"],
        official_deadline=scope["official_deadline"],
        left_preparation_id=scope["left"]["preparation_id"],
        left_decision_id=scope["left"]["decision_id"],
        right_preparation_id=scope["right"]["preparation_id"],
        right_decision_id=scope["right"]["decision_id"],
    )
    if payload["decision_diff_id"] != expected_id or not _ID_RE.fullmatch(
        payload["decision_diff_id"]
    ):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "decision_diff_id does not match ordered semantic inputs",
        )
    _validate_derived_fields(payload)
    if payload["summary"] != _expected_summary(payload):
        raise DecisionDiffError(
            DecisionDiffErrorCode.MALFORMED_ARTIFACT,
            "DecisionDiff summary does not reconcile with diff contents",
        )
    return payload


def serialize_decision_diff(payload: Mapping[str, Any]) -> bytes:
    """Return deterministic UTF-8, sorted-key, finite JSON with a trailing newline."""
    validate_decision_diff(payload)
    return (
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def write_decision_diff(
    *,
    left_final_manifest_path: Path,
    right_final_manifest_path: Path,
    output_root: Path = Path("data/operations/fpl/decision-diffs"),
) -> DecisionDiffArtifacts:
    """Build and immutably publish one explicitly addressed DecisionDiff."""
    decision_diff = build_decision_diff(
        left_final_manifest_path, right_final_manifest_path
    )
    payload = decision_diff.to_payload()
    scope = payload["scope"]
    directory = (
        output_root
        / scope["season"]
        / f"gameweek={scope['target_gameweek']}"
        / decision_diff.decision_diff_id
    )
    path = directory / "decision_diff.json"
    body = decision_diff.canonical_bytes()
    existed = path.exists()
    try:
        _write_atomic(path, body)
    except OperationalRunnerError as exc:
        raise DecisionDiffError(
            DecisionDiffErrorCode.IMMUTABLE_PUBLICATION_CONFLICT, str(exc)
        ) from exc
    return DecisionDiffArtifacts(
        decision_diff_id=decision_diff.decision_diff_id,
        directory=directory,
        artifact_path=path,
        artifact_sha256=sha256_file(path),
        reused=existed,
        summary=dict(payload["summary"]),
    )
