"""Authorization port required before every authoritative artifact read."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


LOCAL_SINGLE_USER_PRINCIPAL = "single-user-local"


class AuthorizationDeniedError(PermissionError):
    """Raised when an artifact read is not authorized."""


@dataclass(frozen=True)
class AuthorizationRequest:
    principal_id: str
    action: str
    artifact_type: str
    semantic_id: str


class AuthorizationPolicy(Protocol):
    """Replaceable authorization boundary for artifact operations."""

    def authorize(self, request: AuthorizationRequest) -> None:
        """Return only when the requested operation is authorized."""


class SingleUserAllowAllPolicy:
    """Task026B-only policy limited to the local single-user principal."""

    def authorize(self, request: AuthorizationRequest) -> None:
        if request.principal_id != LOCAL_SINGLE_USER_PRINCIPAL:
            raise AuthorizationDeniedError("artifact read is not authorized")
