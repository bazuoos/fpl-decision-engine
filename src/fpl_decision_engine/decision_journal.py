"""Immutable prospective decision journal and post-gameweek outcome contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from .operational_manifest import (
    OperationalContractError,
    PreparationManifest,
    canonical_json_bytes,
    validate_final_operational_manifest,
    validate_preparation_manifest,
)
from .operational_runner import OperationalRunnerError, _write_atomic
from .presentation.gameweek_decision import (
    GameweekDecisionError,
    build_gameweek_decision,
    serialize_gameweek_decision,
)
from .projection_provider import sha256_file
from .transfer_decision import ROLL, TRANSFER


JOURNAL_SCHEMA_VERSION = "decision-journal-entry-v1"
OUTCOME_SCHEMA_VERSION = "decision-outcome-v1"
OUTCOME_SCOPE = "official_gameweek_completion_only"


class JournalClassification(str, Enum):
    PROSPECTIVE = "PROSPECTIVE"
    HISTORICAL_BACKFILL = "HISTORICAL_BACKFILL"


class HumanActionKind(str, Enum):
    FOLLOW_ENGINE = "FOLLOW_ENGINE"
    ROLL = ROLL
    TRANSFER = TRANSFER


class DecisionJournalError(ValueError):
    """Raised when a journal or outcome cannot be proven safely."""


class DecisionJournalConflictError(DecisionJournalError):
    """Raised rather than replacing conflicting immutable bytes."""


@dataclass(frozen=True, order=True)
class StructuredAction:
    action_type: str
    outgoing_element_id: int | None = None
    incoming_element_id: int | None = None

    def __post_init__(self) -> None:
        if self.action_type == ROLL:
            if self.outgoing_element_id is not None or self.incoming_element_id is not None:
                raise DecisionJournalError("ROLL must not contain transfer player IDs")
            return
        if self.action_type != TRANSFER:
            raise DecisionJournalError(f"unsupported action type {self.action_type!r}")
        outgoing = _positive_integer(self.outgoing_element_id, "outgoing element ID")
        incoming = _positive_integer(self.incoming_element_id, "incoming element ID")
        if outgoing == incoming:
            raise DecisionJournalError("transfer outgoing and incoming IDs must differ")

    def to_payload(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "incoming_element_id": self.incoming_element_id,
            "outgoing_element_id": self.outgoing_element_id,
        }


@dataclass(frozen=True)
class JournalArtifacts:
    journal_entry_id: str
    directory: Path
    entry_path: Path
    entry_sha256: str
    reused: bool


@dataclass(frozen=True)
class OutcomeArtifacts:
    outcome_id: str
    directory: Path
    outcome_path: Path
    outcome_sha256: str
    reused: bool


@dataclass(frozen=True)
class _CompletedEvidence:
    preparation: PreparationManifest
    final_payload: Mapping[str, Any]
    final_sha256: str
    decision_directory: Path
    gameweek_path: Path
    gameweek_payload: Mapping[str, Any]
    reliability_path: Path


_ACTION_FIELDS = {
    "action_type",
    "incoming_element_id",
    "outgoing_element_id",
}

_JOURNAL_FIELDS = {
    "schema_version",
    "journal_entry_id",
    "classification",
    "season",
    "target_gameweek",
    "official_deadline",
    "preparation_id",
    "decision_id",
    "final_operational_manifest_sha256",
    "gameweek_decision_sha256",
    "reliability_artifact_sha256",
    "manager_entry_id",
    "engine_action",
    "human_action_declaration",
    "human_action",
    "human_action_matches_engine",
    "override_status",
    "override_reason",
    "human_action_recorded_at",
    "journal_created_at",
    "evidence_cutoff",
    "historical_evidence_sha256",
}

_OUTCOME_FIELDS = {
    "schema_version",
    "outcome_id",
    "outcome_scope",
    "journal_entry_id",
    "journal_entry_sha256",
    "season",
    "target_gameweek",
    "official_deadline",
    "official_completion",
    "outcome_created_at",
    "realized_manager_gameweek_points",
    "engine_action_counterfactual_points",
}

_COMPLETION_FIELDS = {
    "bootstrap_snapshot_sha256",
    "observed_at",
    "event_finished",
    "event_data_checked",
}


def _system_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _exact_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise DecisionJournalError(
            f"{label} fields do not match schema; "
            f"missing={sorted(expected - set(value))}, "
            f"additional={sorted(set(value) - expected)}"
        )


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DecisionJournalError(f"{label} must be an object")
    return value


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DecisionJournalError(f"{label} must be a positive non-boolean integer")
    return value


def _sha256(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise DecisionJournalError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _semantic_id(value: Any, prefix: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith(prefix)
        or len(value) != len(prefix) + 64
        or any(character not in "0123456789abcdef" for character in value[len(prefix) :])
    ):
        raise DecisionJournalError(f"{label} is invalid")
    return value


def _utc(value: datetime, label: str) -> tuple[datetime, str]:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise DecisionJournalError(f"{label} must be an explicit UTC datetime")
    return value, value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc(value: Any, label: str) -> tuple[datetime, str]:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise DecisionJournalError(f"{label} must be an explicit UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise DecisionJournalError(f"{label} is invalid") from exc
    return _utc(parsed, label)


def _read_json(path: Path, label: str) -> Mapping[str, Any]:
    if not path.is_file():
        raise DecisionJournalError(f"{label} is missing: {path}")
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecisionJournalError(f"{label} is invalid: {exc}") from exc
    return _object(payload, label)


def _single(root: Path, filename: str, label: str) -> Path:
    matches = list(root.rglob(filename)) if root.is_dir() else []
    if len(matches) != 1:
        raise DecisionJournalError(
            f"{label} must resolve to exactly one {filename}; found {len(matches)}"
        )
    return matches[0]


def _matching_hash(root: Path, filename: str, expected: str, label: str) -> Path:
    matches = [
        path
        for path in root.rglob(filename)
        if path.is_file() and sha256_file(path) == expected
    ]
    if len(matches) != 1:
        raise DecisionJournalError(
            f"{label} must resolve to exactly one hash-matching artifact; found {len(matches)}"
        )
    return matches[0]


def _verify_hash(path: Path, expected: Any, label: str) -> str:
    digest = _sha256(expected, f"{label} expected SHA-256")
    if not path.is_file() or sha256_file(path) != digest:
        raise DecisionJournalError(f"{label} hash does not match: {path}")
    return digest


def _action_from_payload(value: Any, label: str) -> StructuredAction:
    payload = _object(value, label)
    _exact_fields(payload, _ACTION_FIELDS, label)
    return StructuredAction(
        action_type=payload.get("action_type"),
        outgoing_element_id=payload.get("outgoing_element_id"),
        incoming_element_id=payload.get("incoming_element_id"),
    )


def _engine_action(gameweek_payload: Mapping[str, Any]) -> StructuredAction:
    action = _object(gameweek_payload.get("recommended_action"), "engine recommendation")
    action_type = action.get("action_type")
    if action_type == ROLL:
        return StructuredAction(ROLL)
    if action_type != TRANSFER:
        raise DecisionJournalError("GameweekDecision recommendation action is unsupported")
    outgoing = _object(action.get("outgoing"), "engine outgoing player")
    incoming = _object(action.get("incoming"), "engine incoming player")
    return StructuredAction(
        TRANSFER,
        _positive_integer(outgoing.get("element_id"), "engine outgoing element ID"),
        _positive_integer(incoming.get("element_id"), "engine incoming element ID"),
    )


def _load_completed_evidence(final_manifest_path: Path) -> _CompletedEvidence:
    final_path = final_manifest_path.resolve()
    decision_directory = final_path.parent
    if final_path.name != "final_operational_manifest.json":
        raise DecisionJournalError("exact final_operational_manifest.json path is required")
    preparation_directory = decision_directory.parent.parent
    preparation_path = preparation_directory / "preparation_manifest.json"
    preparation_payload = _read_json(preparation_path, "preparation manifest")
    final_payload = _read_json(final_path, "final operational manifest")
    try:
        preparation = validate_preparation_manifest(preparation_payload)
        final = validate_final_operational_manifest(final_payload, preparation)
    except OperationalContractError as exc:
        raise DecisionJournalError(f"completed operational manifest is invalid: {exc}") from exc
    if preparation_path.read_bytes() != preparation.canonical_bytes():
        raise DecisionJournalError("preparation manifest bytes are not canonical")
    if final_path.read_bytes() != final.canonical_bytes():
        raise DecisionJournalError("final operational manifest bytes are not canonical")
    if preparation_directory.name != preparation.preparation_id:
        raise DecisionJournalError("preparation path does not match preparation identity")
    if decision_directory.name != final.decision_id:
        raise DecisionJournalError("decision path does not match decision identity")

    task_root = decision_directory / "task016"
    decision_path = _single(task_root, "one_transfer_decision.json", "Task 016 decision")
    candidate_path = _single(
        task_root, "legal_transfer_candidates.json", "Task 016 candidates"
    )
    reliability_path = _single(
        task_root, "decision_reliability.json", "Task 017 reliability"
    )
    gameweek_path = decision_directory / "gameweek_decision.json"
    _verify_hash(candidate_path, final.candidate_artifact_sha256, "candidate artifact")
    _verify_hash(
        decision_path, final.one_transfer_decision_sha256, "one-transfer decision"
    )
    _verify_hash(reliability_path, final.reliability_artifact_sha256, "reliability")
    _verify_hash(
        gameweek_path,
        final.gameweek_decision_contract_sha256,
        "GameweekDecision",
    )
    _matching_hash(
        preparation_directory / "manager_submissions",
        "manual_editable_state.json",
        final.manager_state_sha256,
        "manager state",
    )
    try:
        rebuilt = serialize_gameweek_decision(
            build_gameweek_decision(decision_path, reliability_path)
        )
    except GameweekDecisionError as exc:
        raise DecisionJournalError(
            f"completed GameweekDecision chain is invalid: {exc}"
        ) from exc
    if rebuilt != gameweek_path.read_bytes():
        raise DecisionJournalError("GameweekDecision does not deterministically rebuild")
    gameweek_payload = _read_json(gameweek_path, "GameweekDecision")
    gameweek_deadline, _ = _parse_utc(
        gameweek_payload.get("frozen_deadline"), "GameweekDecision frozen deadline"
    )
    final_deadline, _ = _parse_utc(final.official_deadline, "final official deadline")
    if (
        gameweek_payload.get("season") is None
        or gameweek_payload.get("target_gameweek") != final.target_gameweek
        or gameweek_deadline != final_deadline
    ):
        raise DecisionJournalError(
            "GameweekDecision season/gameweek/deadline does not match the final manifest"
        )
    return _CompletedEvidence(
        preparation=preparation,
        final_payload=final.to_payload(),
        final_sha256=sha256_file(final_path),
        decision_directory=decision_directory,
        gameweek_path=gameweek_path,
        gameweek_payload=gameweek_payload,
        reliability_path=reliability_path,
    )


def _human_action(
    declaration: HumanActionKind,
    engine: StructuredAction,
    outgoing_element_id: int | None,
    incoming_element_id: int | None,
) -> StructuredAction:
    if not isinstance(declaration, HumanActionKind):
        raise DecisionJournalError("human action declaration must be a HumanActionKind")
    if declaration is HumanActionKind.FOLLOW_ENGINE:
        if outgoing_element_id is not None or incoming_element_id is not None:
            raise DecisionJournalError("FOLLOW_ENGINE must not supply transfer IDs")
        return engine
    if declaration is HumanActionKind.ROLL:
        if outgoing_element_id is not None or incoming_element_id is not None:
            raise DecisionJournalError("ROLL must not supply transfer IDs")
        return StructuredAction(ROLL)
    return StructuredAction(TRANSFER, outgoing_element_id, incoming_element_id)


def _identity(prefix: str, payload_without_id: Mapping[str, Any]) -> str:
    return prefix + hashlib.sha256(canonical_json_bytes(payload_without_id)).hexdigest()


def _publish(path: Path, body: bytes) -> bool:
    existed = path.exists()
    try:
        _write_atomic(path, body)
    except OperationalRunnerError as exc:
        raise DecisionJournalConflictError(str(exc)) from exc
    return existed


def record_decision_journal_entry(
    *,
    final_manifest_path: Path,
    human_action: HumanActionKind,
    outgoing_element_id: int | None = None,
    incoming_element_id: int | None = None,
    override_reason: str | None = None,
    classification: JournalClassification = JournalClassification.PROSPECTIVE,
    historical_evidence_path: Path | None = None,
    clock: Callable[[], datetime] = _system_utc_now,
) -> JournalArtifacts:
    """Validate a completed Engine-v1 run and publish one immutable journal entry."""
    if not isinstance(classification, JournalClassification):
        raise DecisionJournalError("classification must be a JournalClassification")
    evidence = _load_completed_evidence(final_manifest_path)
    final = evidence.final_payload
    deadline_dt, deadline = _parse_utc(final["official_deadline"], "official deadline")
    now_dt, created_at = _utc(clock(), "journal creation time")
    engine = _engine_action(evidence.gameweek_payload)
    human = _human_action(
        human_action,
        engine,
        outgoing_element_id,
        incoming_element_id,
    )
    matches = human == engine
    override = not matches
    reason = override_reason.strip() if isinstance(override_reason, str) else override_reason
    if not override and reason is not None:
        raise DecisionJournalError("non-override entry must not contain an override reason")
    if classification is JournalClassification.PROSPECTIVE:
        if now_dt >= deadline_dt:
            raise DecisionJournalError(
                "prospective human action must be recorded strictly before the deadline"
            )
        if historical_evidence_path is not None:
            raise DecisionJournalError(
                "prospective entry must not claim historical-backfill evidence"
            )
        if override and (not isinstance(reason, str) or not reason):
            raise DecisionJournalError(
                "prospective override requires a non-empty contemporaneous reason"
            )
        action_recorded_at: str | None = created_at
        historical_hash: str | None = None
        final_cutoff_dt, _ = _parse_utc(final["evidence_cutoff"], "engine evidence cutoff")
        evidence_cutoff = max(
            (final_cutoff_dt, final["evidence_cutoff"]),
            (now_dt, created_at),
            key=lambda row: row[0],
        )[1]
    else:
        if historical_evidence_path is None or not historical_evidence_path.is_file():
            raise DecisionJournalError(
                "historical backfill requires a preserved evidence artifact"
            )
        if override and reason is not None and (not isinstance(reason, str) or not reason):
            raise DecisionJournalError("historical override reason must be non-empty or null")
        action_recorded_at = None
        historical_hash = sha256_file(historical_evidence_path)
        evidence_cutoff = final["evidence_cutoff"]

    manager_state = _object(
        evidence.gameweek_payload.get("manager_state"), "GameweekDecision manager state"
    )
    payload_without_id: dict[str, Any] = {
        "classification": classification.value,
        "decision_id": final["decision_id"],
        "engine_action": engine.to_payload(),
        "evidence_cutoff": evidence_cutoff,
        "final_operational_manifest_sha256": evidence.final_sha256,
        "gameweek_decision_sha256": sha256_file(evidence.gameweek_path),
        "historical_evidence_sha256": historical_hash,
        "human_action": human.to_payload(),
        "human_action_declaration": human_action.value,
        "human_action_matches_engine": matches,
        "human_action_recorded_at": action_recorded_at,
        "journal_created_at": created_at,
        "manager_entry_id": _positive_integer(
            manager_state.get("entry_id"), "manager entry ID"
        ),
        "official_deadline": deadline,
        "override_reason": reason,
        "override_status": "NO_OVERRIDE" if matches else "OVERRIDE",
        "preparation_id": final["preparation_id"],
        "reliability_artifact_sha256": sha256_file(evidence.reliability_path),
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "season": evidence.gameweek_payload["season"],
        "target_gameweek": final["target_gameweek"],
    }
    journal_id = _identity("journal_", payload_without_id)
    payload = {"journal_entry_id": journal_id, **payload_without_id}
    validated = validate_decision_journal_entry(payload)
    body = canonical_json_bytes(validated)
    directory = evidence.decision_directory / "journal" / journal_id
    entry_path = directory / "decision_journal_entry.json"
    reused = _publish(entry_path, body)
    return JournalArtifacts(
        journal_entry_id=journal_id,
        directory=directory,
        entry_path=entry_path,
        entry_sha256=hashlib.sha256(body).hexdigest(),
        reused=reused,
    )


def validate_decision_journal_entry(payload: Mapping[str, Any]) -> dict[str, Any]:
    source = _object(payload, "decision journal entry")
    _exact_fields(source, _JOURNAL_FIELDS, "decision journal entry")
    if source.get("schema_version") != JOURNAL_SCHEMA_VERSION:
        raise DecisionJournalError("journal schema version is unsupported")
    try:
        classification = JournalClassification(source.get("classification"))
        declaration = HumanActionKind(source.get("human_action_declaration"))
    except (TypeError, ValueError) as exc:
        raise DecisionJournalError("journal classification or action is unsupported") from exc
    _positive_integer(source.get("target_gameweek"), "target gameweek")
    _positive_integer(source.get("manager_entry_id"), "manager entry ID")
    deadline_dt, deadline = _parse_utc(
        source.get("official_deadline"), "official deadline"
    )
    created_dt, created = _parse_utc(
        source.get("journal_created_at"), "journal creation time"
    )
    cutoff_dt, cutoff = _parse_utc(source.get("evidence_cutoff"), "evidence cutoff")
    if (
        source.get("official_deadline") != deadline
        or source.get("journal_created_at") != created
        or source.get("evidence_cutoff") != cutoff
    ):
        raise DecisionJournalError("journal timestamps must use canonical UTC form")
    if cutoff_dt >= deadline_dt:
        raise DecisionJournalError("journal evidence cutoff must precede deadline")
    engine = _action_from_payload(source.get("engine_action"), "engine action")
    human = _action_from_payload(source.get("human_action"), "human action")
    matches = engine == human
    if source.get("human_action_matches_engine") is not matches:
        raise DecisionJournalError("human-action match flag is not derived correctly")
    if source.get("override_status") != ("NO_OVERRIDE" if matches else "OVERRIDE"):
        raise DecisionJournalError("override status is not derived correctly")
    reason = source.get("override_reason")
    if matches and reason is not None:
        raise DecisionJournalError("non-override entry must not contain a reason")
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        raise DecisionJournalError("override reason must be non-empty or null")
    if declaration is HumanActionKind.FOLLOW_ENGINE and not matches:
        raise DecisionJournalError("FOLLOW_ENGINE must resolve to the engine action")
    recorded = source.get("human_action_recorded_at")
    historical_hash = source.get("historical_evidence_sha256")
    if classification is JournalClassification.PROSPECTIVE:
        recorded_dt, recorded_value = _parse_utc(recorded, "human action recorded time")
        if recorded_dt >= deadline_dt:
            raise DecisionJournalError("prospective action time must precede deadline")
        if recorded_value != created or recorded_dt != created_dt:
            raise DecisionJournalError(
                "prospective action time must equal system-captured journal creation time"
            )
        if historical_hash is not None:
            raise DecisionJournalError("prospective entry cannot contain historical evidence")
        if not matches and reason is None:
            raise DecisionJournalError("prospective override requires a reason")
        if cutoff_dt < recorded_dt:
            raise DecisionJournalError("prospective evidence cutoff is invalid")
    else:
        if recorded is not None:
            raise DecisionJournalError(
                "historical backfill must keep unprovable action timestamp null"
            )
        _sha256(historical_hash, "historical evidence SHA-256")
    for field in (
        "final_operational_manifest_sha256",
        "gameweek_decision_sha256",
        "reliability_artifact_sha256",
    ):
        _sha256(source.get(field), field)
    if not isinstance(source.get("season"), str) or not source["season"]:
        raise DecisionJournalError("season must be a non-empty string")
    _semantic_id(source.get("preparation_id"), "prep_", "preparation ID")
    _semantic_id(source.get("decision_id"), "decision_", "decision ID")
    without_id = {key: value for key, value in source.items() if key != "journal_entry_id"}
    if source.get("journal_entry_id") != _identity("journal_", without_id):
        raise DecisionJournalError("journal entry identity does not match semantic bytes")
    canonical = dict(source)
    canonical["official_deadline"] = deadline
    canonical["journal_created_at"] = created
    canonical["evidence_cutoff"] = cutoff
    return canonical


def _official_completion(
    bootstrap_path: Path,
    target_gameweek: int,
    official_deadline: str,
) -> str:
    payload = _read_json(bootstrap_path, "official completion bootstrap snapshot")
    events = payload.get("events")
    if not isinstance(events, list) or not all(isinstance(row, Mapping) for row in events):
        raise DecisionJournalError("official completion snapshot has no valid events array")
    matches = [row for row in events if row.get("id") == target_gameweek]
    if len(matches) != 1:
        raise DecisionJournalError(
            "official completion snapshot must contain exactly one target event"
        )
    event = matches[0]
    _, event_deadline = _parse_utc(event.get("deadline_time"), "completion deadline")
    if event_deadline != official_deadline:
        raise DecisionJournalError("completion event deadline does not match journal")
    if event.get("finished") is not True or event.get("data_checked") is not True:
        raise DecisionJournalError(
            "official gameweek completion requires finished=true and data_checked=true"
        )
    return sha256_file(bootstrap_path)


def _validate_journal_trust_chain_for_outcome(
    journal_path: Path,
    journal_payload: Mapping[str, Any],
) -> _CompletedEvidence:
    """Re-anchor a structurally valid journal to its completed Engine-v1 evidence."""
    if journal_path.name != "decision_journal_entry.json":
        raise DecisionJournalError("exact decision_journal_entry.json path is required")
    journal_directory = journal_path.parent
    journal_root = journal_directory.parent
    decision_directory = journal_root.parent
    if (
        journal_directory.name != journal_payload["journal_entry_id"]
        or journal_root.name != "journal"
        or decision_directory.name != journal_payload["decision_id"]
        or decision_directory.parent.name != "decisions"
        or decision_directory.parent.parent.name != journal_payload["preparation_id"]
    ):
        raise DecisionJournalError(
            "journal path does not match its preparation/decision/journal identity"
        )

    evidence = _load_completed_evidence(
        decision_directory / "final_operational_manifest.json"
    )
    final = evidence.final_payload
    if evidence.final_sha256 != journal_payload["final_operational_manifest_sha256"]:
        raise DecisionJournalError(
            "journal final operational manifest hash is not anchored to trusted evidence"
        )
    if (
        final["preparation_id"] != journal_payload["preparation_id"]
        or final["decision_id"] != journal_payload["decision_id"]
        or final["target_gameweek"] != journal_payload["target_gameweek"]
    ):
        raise DecisionJournalError(
            "journal operational identity does not match trusted evidence"
        )
    journal_deadline, _ = _parse_utc(
        journal_payload["official_deadline"], "journal official deadline"
    )
    final_deadline, _ = _parse_utc(
        final["official_deadline"], "trusted final official deadline"
    )
    if journal_deadline != final_deadline:
        raise DecisionJournalError(
            "journal official deadline does not match trusted evidence"
        )
    if sha256_file(evidence.gameweek_path) != journal_payload["gameweek_decision_sha256"]:
        raise DecisionJournalError(
            "journal GameweekDecision hash is not anchored to trusted evidence"
        )
    if sha256_file(evidence.reliability_path) != journal_payload["reliability_artifact_sha256"]:
        raise DecisionJournalError(
            "journal reliability hash is not anchored to trusted evidence"
        )
    if _action_from_payload(journal_payload["engine_action"], "journal engine action") != (
        _engine_action(evidence.gameweek_payload)
    ):
        raise DecisionJournalError(
            "journal engine action does not match the verified GameweekDecision"
        )
    manager_state = _object(
        evidence.gameweek_payload.get("manager_state"),
        "trusted GameweekDecision manager state",
    )
    if (
        evidence.gameweek_payload.get("season") != journal_payload["season"]
        or _positive_integer(
            manager_state.get("entry_id"), "trusted manager entry ID"
        )
        != journal_payload["manager_entry_id"]
    ):
        raise DecisionJournalError(
            "journal season or manager identity does not match trusted evidence"
        )
    final_cutoff, _ = _parse_utc(final["evidence_cutoff"], "trusted evidence cutoff")
    journal_cutoff, _ = _parse_utc(
        journal_payload["evidence_cutoff"], "journal evidence cutoff"
    )
    if journal_cutoff < final_cutoff:
        raise DecisionJournalError(
            "journal evidence cutoff precedes its trusted Engine-v1 evidence"
        )
    return evidence


def record_decision_outcome(
    *,
    journal_entry_path: Path,
    completion_bootstrap_path: Path,
    clock: Callable[[], datetime] = _system_utc_now,
) -> OutcomeArtifacts:
    """Publish an immutable post-gameweek completion record without mutating the journal."""
    journal_path = journal_entry_path.resolve()
    journal_payload = validate_decision_journal_entry(
        _read_json(journal_path, "decision journal entry")
    )
    journal_body = canonical_json_bytes(journal_payload)
    if journal_path.read_bytes() != journal_body:
        raise DecisionJournalError("decision journal entry bytes are not canonical")
    _validate_journal_trust_chain_for_outcome(journal_path, journal_payload)
    journal_hash = hashlib.sha256(journal_body).hexdigest()
    deadline_dt, deadline = _parse_utc(
        journal_payload["official_deadline"], "official deadline"
    )
    observed_dt, observed_at = _utc(clock(), "official completion observation time")
    if observed_dt <= deadline_dt:
        raise DecisionJournalError("outcome observation must be after the official deadline")
    completion_hash = _official_completion(
        completion_bootstrap_path,
        journal_payload["target_gameweek"],
        deadline,
    )
    payload_without_id: dict[str, Any] = {
        "engine_action_counterfactual_points": None,
        "journal_entry_id": journal_payload["journal_entry_id"],
        "journal_entry_sha256": journal_hash,
        "official_completion": {
            "bootstrap_snapshot_sha256": completion_hash,
            "event_data_checked": True,
            "event_finished": True,
            "observed_at": observed_at,
        },
        "official_deadline": deadline,
        "outcome_created_at": observed_at,
        "outcome_scope": OUTCOME_SCOPE,
        "realized_manager_gameweek_points": None,
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "season": journal_payload["season"],
        "target_gameweek": journal_payload["target_gameweek"],
    }
    outcome_id = _identity("outcome_", payload_without_id)
    payload = {"outcome_id": outcome_id, **payload_without_id}
    validated = validate_decision_outcome(payload)
    body = canonical_json_bytes(validated)
    directory = journal_path.parent / "outcomes" / outcome_id
    outcome_path = directory / "decision_outcome.json"
    reused = _publish(outcome_path, body)
    if journal_path.read_bytes() != journal_body:
        raise DecisionJournalError("outcome publication mutated the journal entry")
    return OutcomeArtifacts(
        outcome_id=outcome_id,
        directory=directory,
        outcome_path=outcome_path,
        outcome_sha256=hashlib.sha256(body).hexdigest(),
        reused=reused,
    )


def validate_decision_outcome(payload: Mapping[str, Any]) -> dict[str, Any]:
    source = _object(payload, "decision outcome")
    _exact_fields(source, _OUTCOME_FIELDS, "decision outcome")
    if source.get("schema_version") != OUTCOME_SCHEMA_VERSION:
        raise DecisionJournalError("outcome schema version is unsupported")
    if source.get("outcome_scope") != OUTCOME_SCOPE:
        raise DecisionJournalError("outcome scope is unsupported")
    _positive_integer(source.get("target_gameweek"), "target gameweek")
    deadline_dt, deadline = _parse_utc(
        source.get("official_deadline"), "official deadline"
    )
    created_dt, created = _parse_utc(
        source.get("outcome_created_at"), "outcome creation time"
    )
    _sha256(source.get("journal_entry_sha256"), "journal entry SHA-256")
    _semantic_id(source.get("journal_entry_id"), "journal_", "journal entry ID")
    completion = _object(source.get("official_completion"), "official completion")
    _exact_fields(completion, _COMPLETION_FIELDS, "official completion")
    _sha256(completion.get("bootstrap_snapshot_sha256"), "completion snapshot SHA-256")
    observed_dt, observed = _parse_utc(
        completion.get("observed_at"), "completion observation"
    )
    if (
        source.get("official_deadline") != deadline
        or source.get("outcome_created_at") != created
        or completion.get("observed_at") != observed
    ):
        raise DecisionJournalError("outcome timestamps must use canonical UTC form")
    if observed_dt <= deadline_dt or observed_dt != created_dt:
        raise DecisionJournalError(
            "completion observation must equal outcome creation time after deadline"
        )
    if completion.get("event_finished") is not True or completion.get("event_data_checked") is not True:
        raise DecisionJournalError("outcome does not prove official completion")
    if source.get("realized_manager_gameweek_points") is not None:
        raise DecisionJournalError("DecisionOutcome v1 does not assert manager points")
    if source.get("engine_action_counterfactual_points") is not None:
        raise DecisionJournalError("DecisionOutcome v1 does not assert counterfactual points")
    if not isinstance(source.get("season"), str) or not source["season"]:
        raise DecisionJournalError("season must be a non-empty string")
    without_id = {key: value for key, value in source.items() if key != "outcome_id"}
    if source.get("outcome_id") != _identity("outcome_", without_id):
        raise DecisionJournalError("outcome identity does not match semantic bytes")
    canonical = dict(source)
    canonical["official_deadline"] = deadline
    canonical["outcome_created_at"] = created
    canonical["official_completion"] = {**completion, "observed_at": observed}
    return canonical
