"""Local authoring boundary for verified operational manager evidence.

This module turns an untrusted, mutable draft into the existing
``verified-manager-evidence-v1`` contract.  It has no decision authority and
never contacts FPL: public identity data comes only from one validated,
hash-pinned operational preparation.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import duckdb

from .editable_manager import EditableManagerError, price_m_to_units
from .operational_manifest import ChipState, PreparationManifest, canonical_json_bytes
from .operational_runner import (
    MANAGER_EVIDENCE_VERSION,
    CompletedRunResult,
    OperationalRunnerError,
    load_validated_preparation,
    load_verified_manager_evidence,
    resume_gameweek,
)


DRAFT_VERSION = "manager-evidence-draft-v1"
DEFAULT_EVIDENCE_ROOT = Path("data/manager/evidence/fpl")
POSITION_COUNTS = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}
POSITION_ORDER = {name: index for index, name in enumerate(POSITION_COUNTS)}


class AuthoringErrorCode(str, Enum):
    INVALID_PREPARATION = "INVALID_PREPARATION"
    INVALID_DRAFT = "INVALID_DRAFT"
    DEADLINE_REACHED = "DEADLINE_REACHED"
    IMMUTABLE_CONFLICT = "IMMUTABLE_CONFLICT"
    STORAGE_FAILURE = "STORAGE_FAILURE"


class ManagerEvidenceAuthoringError(Exception):
    """Failure safe to report without manager-specific values."""

    def __init__(self, code: AuthoringErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class DraftFieldError:
    field: str
    code: str


@dataclass(frozen=True)
class CataloguePlayer:
    element_id: int
    display_name: str
    position: str
    team_id: int
    team_name: str
    market_price_m: str


@dataclass(frozen=True)
class AuthoringPreparation:
    manifest: PreparationManifest
    manifest_path: Path
    season: str
    observed_at: str
    catalogue: tuple[CataloguePlayer, ...]


@dataclass(frozen=True)
class ManagerEvidenceDraft:
    preparation_manifest_sha256: str
    entry_id: Any
    selected_element_ids: Sequence[Any]
    bank_m: Any
    free_transfers: Any
    chip_state: Any
    selling_price_m_by_element_id: Mapping[Any, Any]
    evidence_source: Any
    evidence_source_sha256: Any = None
    current_selection_confirmed: Any = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "bank_m": self.bank_m,
            "chip_state": self.chip_state,
            "current_selection_confirmed": self.current_selection_confirmed,
            "entry_id": self.entry_id,
            "evidence_source": self.evidence_source,
            "evidence_source_sha256": self.evidence_source_sha256,
            "free_transfers": self.free_transfers,
            "preparation_manifest_sha256": self.preparation_manifest_sha256,
            "selected_element_ids": list(self.selected_element_ids),
            "selling_price_m_by_element_id": {
                str(key): value for key, value in self.selling_price_m_by_element_id.items()
            },
            "version": DRAFT_VERSION,
        }


@dataclass(frozen=True)
class PublishedManagerEvidence:
    path: Path
    sha256: str
    reused: bool


_DRAFT_FIELDS = {
    "version",
    "preparation_manifest_sha256",
    "entry_id",
    "selected_element_ids",
    "bank_m",
    "free_transfers",
    "chip_state",
    "selling_price_m_by_element_id",
    "evidence_source",
    "evidence_source_sha256",
    "current_selection_confirmed",
}


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _system_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_draft_pick(value: str) -> tuple[int, str]:
    """Parse one ``element_id:selling_price_m`` CLI value."""
    parts = value.split(":", 1)
    if len(parts) != 2:
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.INVALID_DRAFT,
            "pick must use element_id:selling_price_m",
        )
    try:
        element_id = int(parts[0])
        units = price_m_to_units(parts[1], "selling price")
    except (ValueError, EditableManagerError) as exc:
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.INVALID_DRAFT,
            "pick contains an invalid element ID or selling price",
        ) from exc
    if element_id <= 0:
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.INVALID_DRAFT,
            "pick contains an invalid element ID or selling price",
        )
    return element_id, f"{units / 10:.1f}"


def _private_directory(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(directory, 0o700)
    if path.is_dir():
        os.chmod(path, 0o700)
    else:
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.STORAGE_FAILURE,
            "private storage root is not a directory",
        )


def _write_mutable_private(path: Path, body: bytes) -> None:
    _private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.STORAGE_FAILURE, "private draft could not be saved"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def load_preparation_for_authoring(manifest_path: Path) -> AuthoringPreparation:
    """Validate one exact preparation and expose its bounded public catalogue."""
    resolved = manifest_path.resolve()
    try:
        validated = load_validated_preparation(resolved)
        manifest = validated.manifest
        season = validated.season
        connection = duckdb.connect(":memory:")
        try:
            rows = connection.execute(
                """SELECT fpl_player_id, web_name, position_id, team_id, team_name, price_m
                   FROM read_parquet(?) ORDER BY position_id, fpl_player_id""",
                [str(validated.artifact("players"))],
            ).fetchall()
        finally:
            connection.close()
    except (OperationalRunnerError, duckdb.Error, OSError) as exc:
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.INVALID_PREPARATION,
            "preparation trust chain or player catalogue is invalid",
        ) from exc
    catalogue: list[CataloguePlayer] = []
    seen: set[int] = set()
    try:
        position_codes = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
        for element_id, name, position_id, team_id, team_name, price_m in rows:
            position = position_codes.get(position_id)
            if (
                isinstance(element_id, bool)
                or not isinstance(element_id, int)
                or element_id <= 0
                or element_id in seen
                or not isinstance(name, str)
                or not name.strip()
                or position not in POSITION_COUNTS
                or isinstance(team_id, bool)
                or not isinstance(team_id, int)
                or team_id <= 0
                or not isinstance(team_name, str)
                or not team_name.strip()
            ):
                raise ValueError
            market_units = price_m_to_units(price_m, "market price")
            seen.add(element_id)
            catalogue.append(
                CataloguePlayer(
                    element_id=element_id,
                    display_name=name,
                    position=position,
                    team_id=team_id,
                    team_name=team_name,
                    market_price_m=f"{market_units / 10:.1f}",
                )
            )
    except (ValueError, EditableManagerError) as exc:
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.INVALID_PREPARATION,
            "preparation player catalogue contains invalid public identity data",
        ) from exc
    if not catalogue:
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.INVALID_PREPARATION,
            "preparation player catalogue is empty",
        )
    return AuthoringPreparation(
        manifest=manifest,
        manifest_path=resolved,
        season=season,
        observed_at=manifest.frozen_snapshot.observed_at,
        catalogue=tuple(catalogue),
    )


def validate_draft(
    draft: ManagerEvidenceDraft, preparation: AuthoringPreparation
) -> tuple[DraftFieldError, ...]:
    """Return bounded field errors without exposing manager values."""
    errors: set[tuple[str, str]] = set()

    def add(field: str, code: str) -> None:
        errors.add((field, code))

    if draft.preparation_manifest_sha256 != preparation.manifest.sha256:
        add("preparation_manifest_sha256", "MISMATCH")
    if (
        isinstance(draft.entry_id, bool)
        or not isinstance(draft.entry_id, int)
        or draft.entry_id <= 0
    ):
        add("entry_id", "INVALID")
    if (
        isinstance(draft.free_transfers, bool)
        or not isinstance(draft.free_transfers, int)
        or draft.free_transfers < 0
    ):
        add("free_transfers", "INVALID")
    try:
        price_m_to_units(draft.bank_m, "bank")
    except EditableManagerError:
        add("bank_m", "INVALID_MONEY")
    try:
        chip = ChipState(draft.chip_state)
        if chip is not ChipState.NO_CHIP:
            add("chip_state", "UNSUPPORTED")
    except (TypeError, ValueError):
        add("chip_state", "INVALID")
    if not isinstance(draft.evidence_source, str) or not draft.evidence_source.strip():
        add("evidence_source", "REQUIRED")
    source_hash = draft.evidence_source_sha256
    if source_hash is not None and (
        not isinstance(source_hash, str)
        or len(source_hash) != 64
        or any(character not in "0123456789abcdef" for character in source_hash)
    ):
        add("evidence_source_sha256", "INVALID_SHA256")
    if draft.current_selection_confirmed is not True:
        add("current_selection_confirmed", "REQUIRED")

    ids = (
        list(draft.selected_element_ids)
        if isinstance(draft.selected_element_ids, (list, tuple))
        else []
    )
    if len(ids) != 15:
        add("selected_element_ids", "REQUIRES_15")
    valid_ids = [
        value
        for value in ids
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    ]
    if len(valid_ids) != len(ids):
        add("selected_element_ids", "INVALID_ID")
    if len(set(valid_ids)) != len(valid_ids):
        add("selected_element_ids", "DUPLICATE")
    by_id = {row.element_id: row for row in preparation.catalogue}
    selected = [by_id[value] for value in valid_ids if value in by_id]
    if len(selected) != len(ids):
        add("selected_element_ids", "UNKNOWN")
    if len(selected) == 15:
        if Counter(row.position for row in selected) != Counter(POSITION_COUNTS):
            add("selected_element_ids", "POSITION_COMPOSITION")
        if any(count > 3 for count in Counter(row.team_id for row in selected).values()):
            add("selected_element_ids", "CLUB_LIMIT")

    prices = draft.selling_price_m_by_element_id
    normalized_keys: set[int] = set()
    if not isinstance(prices, Mapping):
        add("selling_price_m_by_element_id", "INVALID")
        prices = {}
    else:
        for raw_key, value in prices.items():
            if isinstance(raw_key, bool) or not (
                isinstance(raw_key, int)
                or (isinstance(raw_key, str) and raw_key.isascii() and raw_key.isdigit())
            ):
                add("selling_price_m_by_element_id", "INVALID_ID")
                continue
            key = int(raw_key)
            if key <= 0 or key in normalized_keys:
                add("selling_price_m_by_element_id", "INVALID_ID")
            normalized_keys.add(key)
            try:
                price_m_to_units(value, "selling price")
            except EditableManagerError:
                add("selling_price_m_by_element_id", "INVALID_MONEY")
    if normalized_keys != set(valid_ids) or len(valid_ids) != len(ids):
        add("selling_price_m_by_element_id", "MUST_MATCH_SELECTION")
    return tuple(DraftFieldError(field, code) for field, code in sorted(errors))


def save_draft(draft: ManagerEvidenceDraft, path: Path) -> Path:
    """Atomically save mutable private convenience state with owner-only mode."""
    _write_mutable_private(path, canonical_json_bytes(draft.to_payload()))
    return path


def load_draft(path: Path) -> ManagerEvidenceDraft:
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.INVALID_DRAFT, "draft file is invalid"
        ) from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != _DRAFT_FIELDS
        or payload.get("version") != DRAFT_VERSION
    ):
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.INVALID_DRAFT, "draft fields or version are invalid"
        )
    prices = payload["selling_price_m_by_element_id"]
    if not isinstance(prices, dict):
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.INVALID_DRAFT, "draft selling-price map is invalid"
        )
    return ManagerEvidenceDraft(
        preparation_manifest_sha256=payload["preparation_manifest_sha256"],
        entry_id=payload["entry_id"],
        selected_element_ids=payload["selected_element_ids"],
        bank_m=payload["bank_m"],
        free_transfers=payload["free_transfers"],
        chip_state=payload["chip_state"],
        selling_price_m_by_element_id=prices,
        evidence_source=payload["evidence_source"],
        evidence_source_sha256=payload["evidence_source_sha256"],
        current_selection_confirmed=payload["current_selection_confirmed"],
    )


def _evidence_payload(
    draft: ManagerEvidenceDraft, preparation: AuthoringPreparation
) -> dict[str, Any]:
    by_id = {row.element_id: row for row in preparation.catalogue}
    prices = {
        int(key): value for key, value in draft.selling_price_m_by_element_id.items()
    }
    players = []
    for element_id in sorted(
        draft.selected_element_ids,
        key=lambda value: (POSITION_ORDER[by_id[value].position], value),
    ):
        row = by_id[element_id]
        units = price_m_to_units(prices[element_id], "selling price")
        players.append(
            {
                "display_name": row.display_name,
                "element_id": row.element_id,
                "position": row.position,
                "selling_price_m": f"{units / 10:.1f}",
            }
        )
    bank_units = price_m_to_units(draft.bank_m, "bank")
    return {
        "bank_m": f"{bank_units / 10:.1f}",
        "chip_state": ChipState(draft.chip_state).value,
        "current_selection_verified": True,
        "entry_id": draft.entry_id,
        "evidence_source": draft.evidence_source.strip(),
        "evidence_source_sha256": draft.evidence_source_sha256,
        "free_transfers": draft.free_transfers,
        "players": players,
        "season": preparation.season,
        "target_gameweek": preparation.manifest.target_gameweek,
        "version": MANAGER_EVIDENCE_VERSION,
    }


def _require_before_deadline(
    preparation: AuthoringPreparation, clock: Callable[[], datetime]
) -> None:
    now = clock()
    if now.tzinfo is None or now.utcoffset() != timezone.utc.utcoffset(now):
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.DEADLINE_REACHED,
            "publication clock must be explicit UTC",
        )
    deadline = datetime.fromisoformat(
        preparation.manifest.official_deadline.replace("Z", "+00:00")
    )
    if now >= deadline:
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.DEADLINE_REACHED,
            "verified evidence must be published strictly before the deadline",
        )


def publish_verified_evidence(
    draft: ManagerEvidenceDraft,
    preparation: AuthoringPreparation,
    *,
    output_root: Path = DEFAULT_EVIDENCE_ROOT,
    clock: Callable[[], datetime] = _system_utc_now,
) -> PublishedManagerEvidence:
    """Publish canonical evidence atomically after bounded draft validation."""
    fresh_preparation = load_preparation_for_authoring(preparation.manifest_path)
    if fresh_preparation != preparation:
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.INVALID_PREPARATION,
            "preparation changed after authoring began",
        )
    errors = validate_draft(draft, fresh_preparation)
    if errors:
        summary = ",".join(f"{item.field}:{item.code}" for item in errors)
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.INVALID_DRAFT, f"draft validation failed [{summary}]"
        )
    _require_before_deadline(fresh_preparation, clock)
    body = canonical_json_bytes(_evidence_payload(draft, fresh_preparation))
    digest = _sha256(body)
    directory = output_root / preparation.manifest.preparation_id / digest
    final = directory / "verified_manager_evidence.json"
    if directory.exists():
        if final.is_file() and final.read_bytes() == body:
            load_verified_manager_evidence(final)
            return PublishedManagerEvidence(final, digest, True)
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.IMMUTABLE_CONFLICT,
            "verified evidence identity already contains conflicting bytes",
        )
    _private_directory(output_root)
    parent = directory.parent
    _private_directory(parent)
    staging = parent / f".{digest}.{uuid.uuid4().hex}.tmp"
    try:
        staging.mkdir(mode=0o700)
        candidate = staging / final.name
        descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        load_verified_manager_evidence(candidate)
        _require_before_deadline(fresh_preparation, clock)
        try:
            staging.rename(directory)
        except FileExistsError:
            if final.is_file() and final.read_bytes() == body:
                staging.rmdir()
                load_verified_manager_evidence(final)
                return PublishedManagerEvidence(final, digest, True)
            raise ManagerEvidenceAuthoringError(
                AuthoringErrorCode.IMMUTABLE_CONFLICT,
                "verified evidence was concurrently published with conflicting bytes",
            )
    except ManagerEvidenceAuthoringError:
        if staging.exists():
            for child in staging.iterdir():
                child.unlink()
            staging.rmdir()
        raise
    except (OSError, OperationalRunnerError) as exc:
        if staging.exists():
            for child in staging.iterdir():
                child.unlink()
            staging.rmdir()
        raise ManagerEvidenceAuthoringError(
            AuthoringErrorCode.STORAGE_FAILURE,
            "verified evidence publication or contract validation failed",
        ) from exc
    return PublishedManagerEvidence(final, digest, False)


def run_existing_resume(
    preparation: AuthoringPreparation,
    evidence: PublishedManagerEvidence,
    *,
    clock: Callable[[], datetime] = _system_utc_now,
) -> CompletedRunResult:
    """Delegate unchanged to the trusted operational resume entry point."""
    return resume_gameweek(
        preparation_manifest_path=preparation.manifest_path,
        manager_evidence_path=evidence.path,
        clock=clock,
    )
