"""Portable storage port for explicitly identified decision artifacts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol


ARTIFACT_INDEX_VERSION = "decision-artifact-index-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ArtifactStoreError(Exception):
    """Base storage adapter failure."""


class ArtifactNotFoundError(ArtifactStoreError):
    """The explicit semantic identity has no configured artifact."""


class ArtifactStoreUnavailableError(ArtifactStoreError):
    """Artifact storage could not be read safely."""


@dataclass(frozen=True)
class DecisionArtifactReference:
    decision_id: str
    final_manifest_path: Path
    expected_final_manifest_sha256: str


class DecisionArtifactStore(Protocol):
    """Storage interface; route/domain identity never contains a path."""

    def resolve_decision(self, decision_id: str) -> DecisionArtifactReference:
        """Resolve one explicit decision identity without discovery fallback."""


class EmptyDecisionArtifactStore:
    """Safe default for a local server with no configured artifact index."""

    def resolve_decision(self, decision_id: str) -> DecisionArtifactReference:
        raise ArtifactNotFoundError(f"decision {decision_id!r} is not configured")


class FilesystemDecisionArtifactStore:
    """Resolve explicit indexed decisions within one configured filesystem root."""

    def __init__(
        self,
        root: Path,
        references: Mapping[str, tuple[str, str]],
    ) -> None:
        self._root = root.resolve()
        self._references = dict(references)

    @classmethod
    def from_index_file(
        cls, root: Path, index_path: Path
    ) -> "FilesystemDecisionArtifactStore":
        try:
            payload = json.loads(index_path.read_bytes())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactStoreUnavailableError("artifact index cannot be read") from exc
        if not isinstance(payload, dict) or set(payload) != {"version", "decisions"}:
            raise ArtifactStoreUnavailableError("artifact index fields are invalid")
        if payload["version"] != ARTIFACT_INDEX_VERSION:
            raise ArtifactStoreUnavailableError("artifact index version is unsupported")
        rows = payload["decisions"]
        if not isinstance(rows, list):
            raise ArtifactStoreUnavailableError("artifact index decisions must be a list")
        references: dict[str, tuple[str, str]] = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != {
                "decision_id",
                "final_manifest_relative_path",
                "final_manifest_sha256",
            }:
                raise ArtifactStoreUnavailableError("artifact index row is invalid")
            decision_id = row["decision_id"]
            relative_path = row["final_manifest_relative_path"]
            digest = row["final_manifest_sha256"]
            if not all(isinstance(value, str) for value in (decision_id, relative_path, digest)):
                raise ArtifactStoreUnavailableError("artifact index row types are invalid")
            if not relative_path or not _SHA256_RE.fullmatch(digest):
                raise ArtifactStoreUnavailableError("artifact index row values are invalid")
            if decision_id in references:
                raise ArtifactStoreUnavailableError("artifact index has duplicate identities")
            references[decision_id] = (relative_path, digest)
        return cls(root, references)

    def resolve_decision(self, decision_id: str) -> DecisionArtifactReference:
        try:
            relative_path, digest = self._references[decision_id]
        except KeyError as exc:
            raise ArtifactNotFoundError(
                f"decision {decision_id!r} is not configured"
            ) from exc
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ArtifactStoreUnavailableError(
                "artifact index path escapes the configured store"
            )
        resolved = (self._root / relative).resolve()
        if not resolved.is_relative_to(self._root):
            raise ArtifactStoreUnavailableError(
                "artifact index path escapes the configured store"
            )
        return DecisionArtifactReference(
            decision_id=decision_id,
            final_manifest_path=resolved,
            expected_final_manifest_sha256=digest,
        )


def serialize_artifact_index(payload: Mapping[str, Any]) -> bytes:
    """Deterministic serialization for local index tooling and tests."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
