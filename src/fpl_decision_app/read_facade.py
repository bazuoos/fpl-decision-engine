"""Authorization-first trusted artifact read application service."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol

from fpl_decision_engine.trusted_artifact_reader import (
    TRUSTED_ARTIFACT_READER_VERSION,
    TrustedArtifactValidationError,
    VerifiedGameweekDecision,
    load_verified_gameweek_decision,
)

from .artifacts import (
    ArtifactNotFoundError,
    ArtifactStoreUnavailableError,
    DecisionArtifactStore,
)
from .authorization import (
    AuthorizationDeniedError,
    AuthorizationPolicy,
    AuthorizationRequest,
)


API_VERSION = "1.0"
_DECISION_ID_RE = re.compile(r"^decision_[0-9a-f]{64}$")


class ReadFailureCode(str, Enum):
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    ARTIFACT_INVALID = "ARTIFACT_INVALID"
    HASH_MISMATCH = "HASH_MISMATCH"
    UNSUPPORTED_SCHEMA = "UNSUPPORTED_SCHEMA"
    TRUST_CHAIN_INVALID = "TRUST_CHAIN_INVALID"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"


class TrustedReadError(Exception):
    """Fail-closed application read error with a stable machine code."""

    def __init__(self, code: ReadFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class CompletedDecisionLoader(Protocol):
    def load(self, final_manifest_path: Path) -> VerifiedGameweekDecision:
        """Return a decision only after the trusted engine accepts its chain."""


class EngineV1CompletedDecisionLoader:
    def load(self, final_manifest_path: Path) -> VerifiedGameweekDecision:
        return load_verified_gameweek_decision(final_manifest_path)


@dataclass(frozen=True)
class TrustedArtifactReadFacade:
    authorization_policy: AuthorizationPolicy
    artifact_store: DecisionArtifactStore
    loader: CompletedDecisionLoader = EngineV1CompletedDecisionLoader()

    def read_decision(
        self, *, principal_id: str, decision_id: str
    ) -> Mapping[str, Any]:
        """Authorize, resolve, and verify one explicit completed decision."""
        request = AuthorizationRequest(
            principal_id=principal_id,
            action="read",
            artifact_type="GameweekDecision",
            semantic_id=decision_id,
        )
        try:
            self.authorization_policy.authorize(request)
        except AuthorizationDeniedError as exc:
            raise TrustedReadError(
                ReadFailureCode.UNAUTHORIZED, "artifact read is not authorized"
            ) from exc

        if not isinstance(decision_id, str) or not _DECISION_ID_RE.fullmatch(decision_id):
            raise TrustedReadError(
                ReadFailureCode.ARTIFACT_INVALID,
                "decision identity is invalid",
            )
        try:
            reference = self.artifact_store.resolve_decision(decision_id)
        except ArtifactNotFoundError as exc:
            raise TrustedReadError(
                ReadFailureCode.NOT_FOUND, "decision does not exist"
            ) from exc
        except ArtifactStoreUnavailableError as exc:
            raise TrustedReadError(
                ReadFailureCode.UPSTREAM_UNAVAILABLE,
                "artifact store is unavailable",
            ) from exc

        try:
            body = reference.final_manifest_path.read_bytes()
        except FileNotFoundError as exc:
            raise TrustedReadError(
                ReadFailureCode.NOT_FOUND, "decision does not exist"
            ) from exc
        except OSError as exc:
            raise TrustedReadError(
                ReadFailureCode.UPSTREAM_UNAVAILABLE,
                "artifact store is unavailable",
            ) from exc
        observed = hashlib.sha256(body).hexdigest()
        if observed != reference.expected_final_manifest_sha256:
            raise TrustedReadError(
                ReadFailureCode.HASH_MISMATCH,
                "final manifest hash does not match its indexed identity",
            )
        try:
            verified = self.loader.load(reference.final_manifest_path)
        except TrustedArtifactValidationError as exc:
            raise TrustedReadError(
                ReadFailureCode.TRUST_CHAIN_INVALID,
                "completed decision trust chain is invalid",
            ) from exc
        if verified.decision_id != decision_id:
            raise TrustedReadError(
                ReadFailureCode.TRUST_CHAIN_INVALID,
                "resolved artifact does not match the requested decision identity",
            )
        if verified.final_manifest_sha256 != observed:
            raise TrustedReadError(
                ReadFailureCode.HASH_MISMATCH,
                "verified final manifest hash changed during the read",
            )
        if verified.schema_name != "GameweekDecision" or verified.schema_version != "1.0.0":
            raise TrustedReadError(
                ReadFailureCode.UNSUPPORTED_SCHEMA,
                "GameweekDecision schema is unsupported",
            )
        return {
            "api_version": API_VERSION,
            "artifact_identity": {
                "artifact_type": verified.schema_name,
                "artifact_schema_version": verified.schema_version,
                "semantic_id": verified.decision_id,
                "preparation_id": verified.preparation_id,
                "sha256": verified.artifact_sha256,
                "final_manifest_sha256": verified.final_manifest_sha256,
            },
            "trust": {
                "state": "VERIFIED",
                "reader_version": TRUSTED_ARTIFACT_READER_VERSION,
                "complete_chain_validated": True,
            },
            "payload": verified.payload(),
        }
