"""Minimal versioned HTTP API for verified Engine v1 artifacts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from .artifacts import EmptyDecisionArtifactStore, FilesystemDecisionArtifactStore
from .authorization import LOCAL_SINGLE_USER_PRINCIPAL, SingleUserAllowAllPolicy
from .read_facade import API_VERSION, ReadFailureCode, TrustedArtifactReadFacade, TrustedReadError


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["1.0"]
    status: Literal["ready"]


class ArtifactIdentityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_type: Literal["GameweekDecision"]
    artifact_schema_version: Literal["1.0.0"]
    semantic_id: str
    preparation_id: str
    sha256: str
    final_manifest_sha256: str


class TrustResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["VERIFIED"]
    reader_version: str
    complete_chain_validated: Literal[True]


class DecisionReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_version: Literal["1.0"]
    artifact_identity: ArtifactIdentityResponse
    trust: TrustResponse
    payload: dict[str, Any]


class ProblemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int
    code: ReadFailureCode
    detail: str


_HTTP_STATUS = {
    ReadFailureCode.UNAUTHORIZED: 403,
    ReadFailureCode.NOT_FOUND: 404,
    ReadFailureCode.ARTIFACT_INVALID: 400,
    ReadFailureCode.HASH_MISMATCH: 422,
    ReadFailureCode.UNSUPPORTED_SCHEMA: 422,
    ReadFailureCode.TRUST_CHAIN_INVALID: 422,
    ReadFailureCode.UPSTREAM_UNAVAILABLE: 503,
}


def _configured_store():
    root = os.environ.get("FPL_APP_ARTIFACT_ROOT")
    index = os.environ.get("FPL_APP_ARTIFACT_INDEX")
    if root is None and index is None:
        return EmptyDecisionArtifactStore()
    if root is None or index is None:
        raise RuntimeError(
            "FPL_APP_ARTIFACT_ROOT and FPL_APP_ARTIFACT_INDEX must be set together"
        )
    return FilesystemDecisionArtifactStore.from_index_file(Path(root), Path(index))


def create_app(facade: TrustedArtifactReadFacade | None = None) -> FastAPI:
    read_facade = facade or TrustedArtifactReadFacade(
        authorization_policy=SingleUserAllowAllPolicy(),
        artifact_store=_configured_store(),
    )
    app = FastAPI(
        title="FPL Decision Application API",
        version=API_VERSION,
        docs_url=None,
        redoc_url=None,
    )

    @app.exception_handler(TrustedReadError)
    async def trusted_read_error_handler(
        request: Request, exc: TrustedReadError
    ) -> JSONResponse:
        status = _HTTP_STATUS[exc.code]
        return JSONResponse(
            status_code=status,
            content={
                "type": "about:blank",
                "title": "Trusted decision read failed",
                "status": status,
                "code": exc.code.value,
                "detail": str(exc),
            },
        )

    @app.get("/api/v1/health", response_model=HealthResponse)
    def health() -> dict[str, str]:
        return {"api_version": API_VERSION, "status": "ready"}

    @app.get(
        "/api/v1/decisions/{decision_id}",
        response_model=DecisionReadResponse,
        responses={
            status: {"model": ProblemResponse, "description": "Fail-closed read error"}
            for status in sorted(set(_HTTP_STATUS.values()))
        },
    )
    def read_decision(decision_id: str, response: Response) -> dict[str, Any]:
        envelope = dict(
            read_facade.read_decision(
                principal_id=LOCAL_SINGLE_USER_PRINCIPAL,
                decision_id=decision_id,
            )
        )
        response.headers["ETag"] = f'"{envelope["artifact_identity"]["sha256"]}"'
        response.headers["Cache-Control"] = "private, immutable"
        return envelope

    return app


app = create_app()
