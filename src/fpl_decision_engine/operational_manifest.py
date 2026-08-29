"""Immutable identity and manifest contracts for operational gameweek runs.

This module deliberately contains no orchestration or artifact writing.  It is the
validated boundary that a future two-phase runner can consume.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence


PREPARATION_MANIFEST_VERSION = "operational-preparation-manifest-v1"
FINAL_MANIFEST_VERSION = "operational-final-manifest-v1"
IDEMPOTENCY_POLICY_VERSION = (
    "completed-decision-id-returns-existing-validated-output-v1"
)
MODELED_TRANSFER_COST_POINTS = 0

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PREPARATION_ID_RE = re.compile(r"^prep_[0-9a-f]{64}$")
_DECISION_ID_RE = re.compile(r"^decision_[0-9a-f]{64}$")
_VERSION_RE = re.compile(
    r"^(?:[a-z][a-z0-9_-]*-v[1-9][0-9]*|v[0-9]+(?:\.[0-9]+){1,2}|"
    r"[0-9]+\.[0-9]+\.[0-9]+)$"
)
_UTC_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z$"
)


class OperationalContractError(ValueError):
    """Raised when operational identity or manifest evidence fails closed."""


class ChipState(str, Enum):
    """Explicit operational chip states; Task 023A does not implement behavior."""

    NO_CHIP = "NO_CHIP"
    WILDCARD = "WILDCARD"
    FREE_HIT = "FREE_HIT"
    BENCH_BOOST = "BENCH_BOOST"
    TRIPLE_CAPTAIN = "TRIPLE_CAPTAIN"


def _require_exact_fields(
    value: Mapping[str, Any], expected: set[str], label: str
) -> None:
    observed = set(value)
    if observed != expected:
        missing = sorted(expected - observed)
        additional = sorted(observed - expected)
        raise OperationalContractError(
            f"{label} fields do not match schema; missing={missing}, "
            f"additional={additional}"
        )


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OperationalContractError(f"{label} must be an object")
    return value


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise OperationalContractError(
            f"{label} must be a positive non-boolean integer"
        )
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise OperationalContractError(
            f"{label} must be a lowercase SHA-256 hexadecimal digest"
        )
    return value


def _version(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
        raise OperationalContractError(
            f"{label} must be a non-empty explicitly versioned identifier"
        )
    return value


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperationalContractError(f"{label} must be a non-empty string")
    return value


def _utc_timestamp(value: Any, label: str) -> tuple[datetime, str]:
    if not isinstance(value, str) or not _UTC_TIMESTAMP_RE.fullmatch(value):
        raise OperationalContractError(
            f"{label} must be an explicit ISO-8601 UTC timestamp ending in Z"
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise OperationalContractError(f"{label} is not a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise OperationalContractError(f"{label} must be UTC")
    canonical = parsed.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    return parsed, canonical


def _validate_json_value(value: Any, label: str = "payload") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise OperationalContractError(f"{label} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{label}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise OperationalContractError(f"{label} contains a non-string key")
            _validate_json_value(item, f"{label}.{key}")
        return
    raise OperationalContractError(
        f"{label} contains unsupported value type {type(value).__name__}"
    )


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Return the sole canonical UTF-8 representation used by this contract."""
    _validate_json_value(payload)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def build_preparation_id(
    *,
    target_gameweek: int,
    official_deadline: str,
    refresh_manifest_sha256: str,
    contract_version: str = PREPARATION_MANIFEST_VERSION,
) -> str:
    """Create a path-, clock-, environment-, and manager-independent ID."""
    gameweek = _positive_integer(target_gameweek, "target_gameweek")
    _, deadline = _utc_timestamp(official_deadline, "official_deadline")
    refresh_hash = _sha256(refresh_manifest_sha256, "refresh_manifest_sha256")
    version = _version(contract_version, "contract_version")
    digest = canonical_sha256(
        {
            "contract_version": version,
            "official_deadline": deadline,
            "refresh_manifest_sha256": refresh_hash,
            "target_gameweek": gameweek,
        }
    )
    return f"prep_{digest}"


def _preparation_id(value: Any, label: str = "preparation_id") -> str:
    if not isinstance(value, str) or not _PREPARATION_ID_RE.fullmatch(value):
        raise OperationalContractError(f"{label} is not a structurally valid ID")
    return value


def build_decision_id(*, preparation_id: str, manager_state_sha256: str) -> str:
    preparation = _preparation_id(preparation_id)
    manager_hash = _sha256(manager_state_sha256, "manager_state_sha256")
    digest = canonical_sha256(
        {
            "manager_state_sha256": manager_hash,
            "preparation_id": preparation,
        }
    )
    return f"decision_{digest}"


def _decision_id(value: Any, label: str = "decision_id") -> str:
    if not isinstance(value, str) or not _DECISION_ID_RE.fullmatch(value):
        raise OperationalContractError(f"{label} is not a structurally valid ID")
    return value


@dataclass(frozen=True, order=True)
class ArtifactHash:
    role: str
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _nonempty_string(self.role, "artifact role"))
        object.__setattr__(self, "sha256", _sha256(self.sha256, f"{self.role} SHA-256"))

    def to_payload(self) -> dict[str, str]:
        return {"role": self.role, "sha256": self.sha256}


@dataclass(frozen=True, order=True)
class EvidenceObservation:
    source: str
    observed_at: str
    artifact_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _nonempty_string(self.source, "evidence source"))
        _, timestamp = _utc_timestamp(self.observed_at, f"{self.source} observed_at")
        object.__setattr__(self, "observed_at", timestamp)
        object.__setattr__(
            self,
            "artifact_sha256",
            _sha256(self.artifact_sha256, f"{self.source} artifact_sha256"),
        )

    def to_payload(self) -> dict[str, str]:
        return {
            "artifact_sha256": self.artifact_sha256,
            "observed_at": self.observed_at,
            "source": self.source,
        }


def _artifact_hashes(
    values: Mapping[str, str] | Sequence[ArtifactHash], label: str
) -> tuple[ArtifactHash, ...]:
    if isinstance(values, Mapping):
        artifacts = tuple(ArtifactHash(role, digest) for role, digest in values.items())
    else:
        artifacts = tuple(values)
        if not all(isinstance(item, ArtifactHash) for item in artifacts):
            raise OperationalContractError(f"{label} must contain ArtifactHash values")
    if not artifacts:
        raise OperationalContractError(f"{label} must not be empty")
    if len({item.role for item in artifacts}) != len(artifacts):
        raise OperationalContractError(f"{label} artifact roles must be unique")
    return tuple(sorted(artifacts))


def _producer_versions(values: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    if not isinstance(values, Mapping) or not values:
        raise OperationalContractError("producer_versions must not be empty")
    result = []
    for producer, version in values.items():
        result.append(
            (
                _nonempty_string(producer, "producer_versions key"),
                _version(version, f"producer version for {producer}"),
            )
        )
    if len({item[0] for item in result}) != len(result):
        raise OperationalContractError("producer_versions keys must be unique")
    return tuple(sorted(result))


def _evidence_cutoff(
    observations: Sequence[EvidenceObservation], official_deadline: str
) -> str:
    if not observations:
        raise OperationalContractError("at least one evidence observation is required")
    deadline_dt, _ = _utc_timestamp(official_deadline, "official_deadline")
    parsed = []
    for observation in observations:
        timestamp_dt, timestamp = _utc_timestamp(
            observation.observed_at, f"{observation.source} observed_at"
        )
        if timestamp_dt >= deadline_dt:
            raise OperationalContractError(
                f"evidence timestamp for {observation.source} must be strictly before "
                "official_deadline"
            )
        parsed.append((timestamp_dt, timestamp))
    return max(parsed, key=lambda item: item[0])[1]


@dataclass(frozen=True)
class PreparationManifest:
    schema_version: str
    preparation_id: str
    target_gameweek: int
    official_deadline: str
    refresh_manifest_sha256: str
    frozen_snapshot: EvidenceObservation
    accepted_evidence: tuple[EvidenceObservation, ...]
    feature_artifacts: tuple[ArtifactHash, ...]
    prediction_artifacts: tuple[ArtifactHash, ...]
    frozen_player_artifact_sha256: str
    producer_versions: tuple[tuple[str, str], ...]
    evidence_cutoff: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "accepted_evidence": [item.to_payload() for item in self.accepted_evidence],
            "evidence_cutoff": self.evidence_cutoff,
            "feature_artifacts": [item.to_payload() for item in self.feature_artifacts],
            "frozen_player_artifact_sha256": self.frozen_player_artifact_sha256,
            "frozen_snapshot": self.frozen_snapshot.to_payload(),
            "official_deadline": self.official_deadline,
            "prediction_artifacts": [
                item.to_payload() for item in self.prediction_artifacts
            ],
            "preparation_id": self.preparation_id,
            "producer_versions": dict(self.producer_versions),
            "refresh_manifest_sha256": self.refresh_manifest_sha256,
            "schema_version": self.schema_version,
            "target_gameweek": self.target_gameweek,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_payload())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def build_preparation_manifest(
    *,
    target_gameweek: int,
    official_deadline: str,
    refresh_manifest_sha256: str,
    frozen_snapshot_sha256: str,
    frozen_snapshot_observed_at: str,
    accepted_evidence: Sequence[EvidenceObservation],
    feature_artifacts: Mapping[str, str] | Sequence[ArtifactHash],
    prediction_artifacts: Mapping[str, str] | Sequence[ArtifactHash],
    frozen_player_artifact_sha256: str,
    producer_versions: Mapping[str, str],
    schema_version: str = PREPARATION_MANIFEST_VERSION,
) -> PreparationManifest:
    version = _version(schema_version, "preparation schema_version")
    if version != PREPARATION_MANIFEST_VERSION:
        raise OperationalContractError(
            f"unsupported preparation schema_version {version!r}"
        )
    gameweek = _positive_integer(target_gameweek, "target_gameweek")
    _, deadline = _utc_timestamp(official_deadline, "official_deadline")
    refresh_hash = _sha256(refresh_manifest_sha256, "refresh_manifest_sha256")
    snapshot = EvidenceObservation(
        source="official_fpl_snapshot",
        observed_at=frozen_snapshot_observed_at,
        artifact_sha256=frozen_snapshot_sha256,
    )
    supplied_observations = tuple(accepted_evidence)
    if not all(isinstance(item, EvidenceObservation) for item in supplied_observations):
        raise OperationalContractError(
            "accepted_evidence must contain EvidenceObservation values"
        )
    observations = tuple(sorted(supplied_observations))
    if any(item.source == snapshot.source for item in observations):
        raise OperationalContractError(
            "accepted_evidence must not duplicate the frozen snapshot source"
        )
    if len({item.source for item in observations}) != len(observations):
        raise OperationalContractError("accepted evidence source names must be unique")
    all_observations = (snapshot, *observations)
    cutoff = _evidence_cutoff(all_observations, deadline)
    return PreparationManifest(
        schema_version=version,
        preparation_id=build_preparation_id(
            target_gameweek=gameweek,
            official_deadline=deadline,
            refresh_manifest_sha256=refresh_hash,
            contract_version=version,
        ),
        target_gameweek=gameweek,
        official_deadline=deadline,
        refresh_manifest_sha256=refresh_hash,
        frozen_snapshot=snapshot,
        accepted_evidence=observations,
        feature_artifacts=_artifact_hashes(feature_artifacts, "feature_artifacts"),
        prediction_artifacts=_artifact_hashes(
            prediction_artifacts, "prediction_artifacts"
        ),
        frozen_player_artifact_sha256=_sha256(
            frozen_player_artifact_sha256, "frozen_player_artifact_sha256"
        ),
        producer_versions=_producer_versions(producer_versions),
        evidence_cutoff=cutoff,
    )


_PREPARATION_FIELDS = {
    "accepted_evidence",
    "evidence_cutoff",
    "feature_artifacts",
    "frozen_player_artifact_sha256",
    "frozen_snapshot",
    "official_deadline",
    "prediction_artifacts",
    "preparation_id",
    "producer_versions",
    "refresh_manifest_sha256",
    "schema_version",
    "target_gameweek",
}


def _evidence_from_payload(value: Any, label: str) -> EvidenceObservation:
    source = _object(value, label)
    _require_exact_fields(
        source, {"artifact_sha256", "observed_at", "source"}, label
    )
    return EvidenceObservation(
        source=source["source"],
        observed_at=source["observed_at"],
        artifact_sha256=source["artifact_sha256"],
    )


def _artifacts_from_payload(value: Any, label: str) -> tuple[ArtifactHash, ...]:
    if not isinstance(value, list):
        raise OperationalContractError(f"{label} must be an array")
    result = []
    for index, item in enumerate(value):
        artifact = _object(item, f"{label}[{index}]")
        _require_exact_fields(artifact, {"role", "sha256"}, f"{label}[{index}]")
        result.append(ArtifactHash(artifact["role"], artifact["sha256"]))
    return _artifact_hashes(result, label)


def validate_preparation_manifest(payload: Mapping[str, Any]) -> PreparationManifest:
    source = _object(payload, "preparation manifest")
    _require_exact_fields(source, _PREPARATION_FIELDS, "preparation manifest")
    evidence = source["accepted_evidence"]
    if not isinstance(evidence, list):
        raise OperationalContractError("accepted_evidence must be an array")
    producer_versions = _object(source["producer_versions"], "producer_versions")
    manifest = build_preparation_manifest(
        target_gameweek=source["target_gameweek"],
        official_deadline=source["official_deadline"],
        refresh_manifest_sha256=source["refresh_manifest_sha256"],
        frozen_snapshot_sha256=_object(
            source["frozen_snapshot"], "frozen_snapshot"
        ).get("artifact_sha256"),
        frozen_snapshot_observed_at=_object(
            source["frozen_snapshot"], "frozen_snapshot"
        ).get("observed_at"),
        accepted_evidence=tuple(
            _evidence_from_payload(item, f"accepted_evidence[{index}]")
            for index, item in enumerate(evidence)
        ),
        feature_artifacts=_artifacts_from_payload(
            source["feature_artifacts"], "feature_artifacts"
        ),
        prediction_artifacts=_artifacts_from_payload(
            source["prediction_artifacts"], "prediction_artifacts"
        ),
        frozen_player_artifact_sha256=source["frozen_player_artifact_sha256"],
        producer_versions=producer_versions,
        schema_version=source["schema_version"],
    )
    frozen_snapshot = _evidence_from_payload(source["frozen_snapshot"], "frozen_snapshot")
    if frozen_snapshot.source != "official_fpl_snapshot":
        raise OperationalContractError(
            "frozen_snapshot source must be official_fpl_snapshot"
        )
    if source["preparation_id"] != manifest.preparation_id:
        _preparation_id(source["preparation_id"])
        raise OperationalContractError("preparation_id does not match semantic inputs")
    _, supplied_cutoff = _utc_timestamp(source["evidence_cutoff"], "evidence_cutoff")
    if supplied_cutoff != manifest.evidence_cutoff:
        raise OperationalContractError(
            "evidence_cutoff does not equal the maximum accepted evidence timestamp"
        )
    if source != manifest.to_payload():
        raise OperationalContractError(
            "preparation manifest is not in canonical semantic form"
        )
    return manifest


@dataclass(frozen=True)
class FinalOperationalManifest:
    schema_version: str
    preparation_id: str
    decision_id: str
    target_gameweek: int
    official_deadline: str
    preparation_manifest_sha256: str
    manager_state_sha256: str
    manager_verification_timestamp: str
    manager_evidence_source: str
    manager_evidence_source_sha256: str | None
    chip_state: ChipState
    modeled_transfer_cost_points: int
    evidence_cutoff: str
    candidate_artifact_sha256: str
    one_transfer_decision_sha256: str
    reliability_artifact_sha256: str
    gameweek_decision_schema_version: str
    gameweek_decision_schema_sha256: str
    gameweek_decision_contract_sha256: str
    finalization_timestamp: str
    idempotency_policy_version: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "candidate_artifact_sha256": self.candidate_artifact_sha256,
            "chip_state": self.chip_state.value,
            "decision_id": self.decision_id,
            "evidence_cutoff": self.evidence_cutoff,
            "finalization_timestamp": self.finalization_timestamp,
            "gameweek_decision_contract_sha256": self.gameweek_decision_contract_sha256,
            "gameweek_decision_schema_sha256": self.gameweek_decision_schema_sha256,
            "gameweek_decision_schema_version": self.gameweek_decision_schema_version,
            "idempotency_policy_version": self.idempotency_policy_version,
            "manager_evidence_source": self.manager_evidence_source,
            "manager_evidence_source_sha256": self.manager_evidence_source_sha256,
            "manager_state_sha256": self.manager_state_sha256,
            "manager_verification_timestamp": self.manager_verification_timestamp,
            "modeled_transfer_cost_points": self.modeled_transfer_cost_points,
            "official_deadline": self.official_deadline,
            "one_transfer_decision_sha256": self.one_transfer_decision_sha256,
            "preparation_id": self.preparation_id,
            "preparation_manifest_sha256": self.preparation_manifest_sha256,
            "reliability_artifact_sha256": self.reliability_artifact_sha256,
            "schema_version": self.schema_version,
            "target_gameweek": self.target_gameweek,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_payload())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def build_final_operational_manifest(
    *,
    preparation: PreparationManifest,
    manager_state_sha256: str,
    manager_verification_timestamp: str,
    manager_evidence_source: str,
    manager_evidence_source_sha256: str | None,
    chip_state: ChipState,
    candidate_artifact_sha256: str,
    one_transfer_decision_sha256: str,
    reliability_artifact_sha256: str,
    gameweek_decision_schema_version: str,
    gameweek_decision_schema_sha256: str,
    gameweek_decision_contract_sha256: str,
    finalization_timestamp: str,
    modeled_transfer_cost_points: int = MODELED_TRANSFER_COST_POINTS,
    schema_version: str = FINAL_MANIFEST_VERSION,
) -> FinalOperationalManifest:
    if not isinstance(preparation, PreparationManifest):
        raise OperationalContractError("preparation must be a validated manifest")
    preparation = validate_preparation_manifest(preparation.to_payload())
    version = _version(schema_version, "final schema_version")
    if version != FINAL_MANIFEST_VERSION:
        raise OperationalContractError(f"unsupported final schema_version {version!r}")
    if not isinstance(chip_state, ChipState):
        raise OperationalContractError("chip_state must be a known ChipState")
    if (
        isinstance(modeled_transfer_cost_points, bool)
        or not isinstance(modeled_transfer_cost_points, int)
        or modeled_transfer_cost_points != MODELED_TRANSFER_COST_POINTS
    ):
        raise OperationalContractError(
            "Engine v1 modeled transfer cost must be exactly zero"
        )
    manager_hash = _sha256(manager_state_sha256, "manager_state_sha256")
    manager_time_dt, manager_time = _utc_timestamp(
        manager_verification_timestamp, "manager_verification_timestamp"
    )
    deadline_dt, deadline = _utc_timestamp(
        preparation.official_deadline, "official_deadline"
    )
    if manager_time_dt >= deadline_dt:
        raise OperationalContractError(
            "manager_verification_timestamp must be strictly before official_deadline"
        )
    preparation_cutoff_dt, _ = _utc_timestamp(
        preparation.evidence_cutoff, "preparation evidence_cutoff"
    )
    evidence_cutoff = max(
        (preparation_cutoff_dt, preparation.evidence_cutoff),
        (manager_time_dt, manager_time),
        key=lambda item: item[0],
    )[1]
    finalization_dt, finalization = _utc_timestamp(
        finalization_timestamp, "finalization_timestamp"
    )
    if finalization_dt >= deadline_dt:
        raise OperationalContractError(
            "finalization_timestamp must be strictly before official_deadline"
        )
    if finalization_dt < max(preparation_cutoff_dt, manager_time_dt):
        raise OperationalContractError(
            "finalization_timestamp cannot precede accepted evidence"
        )
    if manager_evidence_source_sha256 is not None:
        manager_source_hash = _sha256(
            manager_evidence_source_sha256, "manager_evidence_source_sha256"
        )
    else:
        manager_source_hash = None
    return FinalOperationalManifest(
        schema_version=version,
        preparation_id=preparation.preparation_id,
        decision_id=build_decision_id(
            preparation_id=preparation.preparation_id,
            manager_state_sha256=manager_hash,
        ),
        target_gameweek=preparation.target_gameweek,
        official_deadline=deadline,
        preparation_manifest_sha256=preparation.sha256,
        manager_state_sha256=manager_hash,
        manager_verification_timestamp=manager_time,
        manager_evidence_source=_nonempty_string(
            manager_evidence_source, "manager_evidence_source"
        ),
        manager_evidence_source_sha256=manager_source_hash,
        chip_state=chip_state,
        modeled_transfer_cost_points=MODELED_TRANSFER_COST_POINTS,
        evidence_cutoff=evidence_cutoff,
        candidate_artifact_sha256=_sha256(
            candidate_artifact_sha256, "candidate_artifact_sha256"
        ),
        one_transfer_decision_sha256=_sha256(
            one_transfer_decision_sha256, "one_transfer_decision_sha256"
        ),
        reliability_artifact_sha256=_sha256(
            reliability_artifact_sha256, "reliability_artifact_sha256"
        ),
        gameweek_decision_schema_version=_version(
            gameweek_decision_schema_version, "gameweek_decision_schema_version"
        ),
        gameweek_decision_schema_sha256=_sha256(
            gameweek_decision_schema_sha256, "gameweek_decision_schema_sha256"
        ),
        gameweek_decision_contract_sha256=_sha256(
            gameweek_decision_contract_sha256,
            "gameweek_decision_contract_sha256",
        ),
        finalization_timestamp=finalization,
        idempotency_policy_version=IDEMPOTENCY_POLICY_VERSION,
    )


_FINAL_FIELDS = {
    "candidate_artifact_sha256",
    "chip_state",
    "decision_id",
    "evidence_cutoff",
    "finalization_timestamp",
    "gameweek_decision_contract_sha256",
    "gameweek_decision_schema_sha256",
    "gameweek_decision_schema_version",
    "idempotency_policy_version",
    "manager_evidence_source",
    "manager_evidence_source_sha256",
    "manager_state_sha256",
    "manager_verification_timestamp",
    "modeled_transfer_cost_points",
    "official_deadline",
    "one_transfer_decision_sha256",
    "preparation_id",
    "preparation_manifest_sha256",
    "reliability_artifact_sha256",
    "schema_version",
    "target_gameweek",
}


def validate_final_operational_manifest(
    payload: Mapping[str, Any], preparation: PreparationManifest
) -> FinalOperationalManifest:
    source = _object(payload, "final operational manifest")
    _require_exact_fields(source, _FINAL_FIELDS, "final operational manifest")
    target_gameweek = _positive_integer(
        source["target_gameweek"], "final manifest target_gameweek"
    )
    try:
        chip_state = ChipState(source["chip_state"])
    except (TypeError, ValueError) as exc:
        raise OperationalContractError("chip_state is not a known value") from exc
    manifest = build_final_operational_manifest(
        preparation=preparation,
        manager_state_sha256=source["manager_state_sha256"],
        manager_verification_timestamp=source["manager_verification_timestamp"],
        manager_evidence_source=source["manager_evidence_source"],
        manager_evidence_source_sha256=source["manager_evidence_source_sha256"],
        chip_state=chip_state,
        candidate_artifact_sha256=source["candidate_artifact_sha256"],
        one_transfer_decision_sha256=source["one_transfer_decision_sha256"],
        reliability_artifact_sha256=source["reliability_artifact_sha256"],
        gameweek_decision_schema_version=source[
            "gameweek_decision_schema_version"
        ],
        gameweek_decision_schema_sha256=source[
            "gameweek_decision_schema_sha256"
        ],
        gameweek_decision_contract_sha256=source[
            "gameweek_decision_contract_sha256"
        ],
        finalization_timestamp=source["finalization_timestamp"],
        modeled_transfer_cost_points=source["modeled_transfer_cost_points"],
        schema_version=source["schema_version"],
    )
    _preparation_id(source["preparation_id"])
    _decision_id(source["decision_id"])
    _sha256(source["preparation_manifest_sha256"], "preparation_manifest_sha256")
    if source["preparation_id"] != preparation.preparation_id:
        raise OperationalContractError(
            "final manifest does not reference the supplied preparation_id"
        )
    if source["preparation_manifest_sha256"] != preparation.sha256:
        raise OperationalContractError(
            "final manifest does not reference the supplied preparation manifest hash"
        )
    if target_gameweek != preparation.target_gameweek:
        raise OperationalContractError(
            "final manifest target_gameweek does not match preparation"
        )
    if source["official_deadline"] != preparation.official_deadline:
        raise OperationalContractError(
            "final manifest official_deadline does not match preparation"
        )
    if source["idempotency_policy_version"] != IDEMPOTENCY_POLICY_VERSION:
        raise OperationalContractError("unsupported idempotency policy version")
    if source != manifest.to_payload():
        raise OperationalContractError(
            "final operational manifest is not in canonical semantic form"
        )
    return manifest
