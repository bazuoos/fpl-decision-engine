"""Fetch and persist raw Fantasy Premier League data."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

FPL_BOOTSTRAP_STATIC_URL = (
    "https://fantasy.premierleague.com/api/bootstrap-static/"
)
DEFAULT_TIMEOUT_SECONDS = 30

logger = logging.getLogger(__name__)


class FetchError(Exception):
    """Base exception for expected pipeline failures."""


class NetworkError(FetchError):
    """Raised when the FPL endpoint cannot be reached."""


class HTTPStatusError(FetchError):
    """Raised when the FPL endpoint returns a non-success status."""


class InvalidJSONError(FetchError):
    """Raised when the FPL endpoint does not return valid JSON."""


class SnapshotExistsError(FetchError):
    """Raised rather than overwriting an existing snapshot."""


class HTTPResponse(Protocol):
    status: int

    def read(self) -> bytes: ...

    def __enter__(self) -> "HTTPResponse": ...

    def __exit__(self, *args: object) -> None: ...


Opener = Callable[..., HTTPResponse]


def _utc_timestamp(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def _fetch_response_bytes(
    endpoint: str,
    *,
    opener: Opener,
    timeout: float,
) -> bytes:
    try:
        with opener(endpoint, timeout=timeout) as response:
            status = response.status
            if not 200 <= status < 300:
                raise HTTPStatusError(
                    f"FPL endpoint returned HTTP status {status}"
                )
            return response.read()
    except HTTPStatusError:
        raise
    except HTTPError as exc:
        raise HTTPStatusError(
            f"FPL endpoint returned HTTP status {exc.code}"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise NetworkError(f"could not reach the FPL endpoint: {reason}") from exc


def _validate_json(body: bytes) -> None:
    try:
        json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InvalidJSONError("FPL endpoint returned invalid JSON") from exc


def fetch_bootstrap_static(
    *,
    data_root: Path = Path("data/raw/fpl"),
    season: str = "2026-27",
    endpoint: str = FPL_BOOTSTRAP_STATIC_URL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    opener: Opener = urlopen,
    now: datetime | None = None,
) -> Path:
    """Fetch bootstrap-static and save its original bytes to a new snapshot."""
    logger.info("Starting FPL bootstrap-static fetch from %s", endpoint)

    body = _fetch_response_bytes(endpoint, opener=opener, timeout=timeout)
    _validate_json(body)

    timestamp = _utc_timestamp(now or datetime.now(timezone.utc))
    snapshot_dir = data_root / season / timestamp
    snapshot_path = snapshot_dir / "bootstrap-static.json"

    try:
        snapshot_dir.mkdir(parents=True, exist_ok=False)
        with snapshot_path.open("xb") as snapshot_file:
            snapshot_file.write(body)
    except FileExistsError as exc:
        raise SnapshotExistsError(
            f"snapshot already exists and will not be overwritten: {snapshot_path}"
        ) from exc

    logger.info("FPL bootstrap-static fetch succeeded")
    logger.info("Snapshot saved to %s", snapshot_path)
    return snapshot_path
