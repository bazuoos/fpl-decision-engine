"""Public read-only facade for completed trusted Engine v1 decisions.

This module exposes the minimum stable seam needed by presentation/application
consumers.  It deliberately delegates the trust-chain work to the same completed
evidence reader used by the Decision Journal rather than reproducing validation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .decision_journal import DecisionJournalError, _load_completed_evidence
from .presentation.gameweek_decision import (
    GAMEWEEK_DECISION_SCHEMA_NAME,
    GAMEWEEK_DECISION_SCHEMA_VERSION,
    serialize_gameweek_decision,
)


TRUSTED_ARTIFACT_READER_VERSION = "trusted-artifact-reader-v1"


class TrustedArtifactValidationError(ValueError):
    """Raised when completed Engine v1 evidence cannot be trusted."""


@dataclass(frozen=True)
class VerifiedGameweekDecision:
    """Verified canonical decision bytes and non-path identity metadata."""

    preparation_id: str
    decision_id: str
    season: str
    target_gameweek: int
    official_deadline: str
    final_manifest_sha256: str
    artifact_sha256: str
    schema_name: str
    schema_version: str
    canonical_payload: bytes

    def payload(self) -> dict[str, Any]:
        """Return a detached parsed copy of the verified canonical payload."""
        value = json.loads(self.canonical_payload)
        if not isinstance(value, dict):  # pragma: no cover - established by loader
            raise TrustedArtifactValidationError(
                "verified GameweekDecision payload is not an object"
            )
        return value


def load_verified_gameweek_decision(
    final_manifest_path: Path,
) -> VerifiedGameweekDecision:
    """Re-anchor and load one explicit completed Engine v1 decision.

    The private completed-evidence implementation remains the sole validator.
    This function only converts its successful result into a path-free public
    value for application consumers.
    """
    try:
        evidence = _load_completed_evidence(final_manifest_path)
    except DecisionJournalError as exc:
        raise TrustedArtifactValidationError(
            "completed Engine v1 decision trust chain is invalid"
        ) from exc

    final = evidence.final_payload
    payload = evidence.gameweek_payload
    schema_name = payload.get("schema_name")
    schema_version = payload.get("schema_version")
    if (
        schema_name != GAMEWEEK_DECISION_SCHEMA_NAME
        or schema_version != GAMEWEEK_DECISION_SCHEMA_VERSION
    ):
        raise TrustedArtifactValidationError(
            "completed GameweekDecision schema is unsupported"
        )
    # Return the canonical bytes that were already accepted by the trusted
    # loader.  Do not perform a second filesystem read after validation.
    canonical_payload = serialize_gameweek_decision(dict(payload))
    artifact_sha256 = hashlib.sha256(canonical_payload).hexdigest()
    if artifact_sha256 != final["gameweek_decision_contract_sha256"]:
        raise TrustedArtifactValidationError(
            "verified GameweekDecision hash does not match final manifest"
        )
    return VerifiedGameweekDecision(
        preparation_id=evidence.preparation.preparation_id,
        decision_id=str(final["decision_id"]),
        season=str(payload["season"]),
        target_gameweek=int(payload["target_gameweek"]),
        official_deadline=str(payload["frozen_deadline"]),
        final_manifest_sha256=evidence.final_sha256,
        artifact_sha256=artifact_sha256,
        schema_name=str(schema_name),
        schema_version=str(schema_version),
        canonical_payload=canonical_payload,
    )
