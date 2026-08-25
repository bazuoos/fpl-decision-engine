"""Ingest fixture and player-history responses from official FPL APIs."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from .transform import find_latest_snapshot

FPL_FIXTURES_URL = "https://fantasy.premierleague.com/api/fixtures/"
FPL_ELEMENT_SUMMARY_URL = (
    "https://fantasy.premierleague.com/api/element-summary/{fpl_player_id}/"
)
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_HISTORY_DELAY_SECONDS = 0.05
DEFAULT_REQUEST_ATTEMPTS = 2

logger = logging.getLogger(__name__)


class OfficialDataError(Exception):
    """Base exception for expected official-data ingestion failures."""


class SourceRequestError(OfficialDataError):
    """Raised when an official FPL response cannot be retrieved or parsed."""


class RawOutputExistsError(OfficialDataError):
    """Raised rather than overwriting an existing raw response."""


class PartialHistoryFetchError(OfficialDataError):
    """Raised after recording one or more failed player-history requests."""

    def __init__(self, failed_player_ids: list[int], manifest_path: Path) -> None:
        self.failed_player_ids = failed_player_ids
        self.manifest_path = manifest_path
        super().__init__(
            "player-history fetch was partial; failed player IDs: "
            f"{', '.join(map(str, failed_player_ids))}; manifest: {manifest_path}"
        )


class HTTPResponse(Protocol):
    status: int

    def read(self) -> bytes: ...

    def __enter__(self) -> "HTTPResponse": ...

    def __exit__(self, *args: object) -> None: ...


Opener = Callable[..., HTTPResponse]
Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("retrieval timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_bootstrap(snapshot_path: Path) -> dict[str, Any]:
    try:
        data = json.loads(snapshot_path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OfficialDataError(f"could not read bootstrap snapshot: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
        raise OfficialDataError("bootstrap snapshot does not contain an elements list")
    return data


def resolve_bootstrap_snapshot(
    raw_data_root: Path,
    season: str,
    snapshot_timestamp: str | None,
) -> Path:
    if snapshot_timestamp is None:
        return find_latest_snapshot(raw_data_root, season)
    snapshot_path = (
        raw_data_root / season / snapshot_timestamp / "bootstrap-static.json"
    )
    if not snapshot_path.is_file():
        raise OfficialDataError(f"bootstrap snapshot does not exist: {snapshot_path}")
    return snapshot_path


def _request_json_bytes(
    endpoint: str,
    *,
    opener: Opener,
    timeout: float,
    attempts: int,
    sleeper: Sleeper,
) -> bytes:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with opener(endpoint, timeout=timeout) as response:
                if not 200 <= response.status < 300:
                    raise SourceRequestError(
                        f"{endpoint} returned HTTP status {response.status}"
                    )
                body = response.read()
            json.loads(body)
            return body
        except HTTPError as exc:
            last_error = SourceRequestError(
                f"{endpoint} returned HTTP status {exc.code}"
            )
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            last_error = SourceRequestError(f"could not reach {endpoint}: {reason}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            last_error = SourceRequestError(f"{endpoint} returned invalid JSON")
        except SourceRequestError as exc:
            last_error = exc

        if attempt < attempts:
            sleeper(0.5 * attempt)

    assert last_error is not None
    raise last_error


def _write_exact(path: Path, body: bytes) -> None:
    try:
        with path.open("xb") as output_file:
            output_file.write(body)
    except FileExistsError as exc:
        raise RawOutputExistsError(
            f"raw output already exists and will not be overwritten: {path}"
        ) from exc


def _write_manifest_exclusive(path: Path, manifest: dict[str, Any]) -> None:
    body = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    _write_exact(path, body)


def _update_manifest(path: Path, manifest: dict[str, Any]) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary_path.replace(path)


def fetch_fixtures_for_snapshot(
    *,
    raw_data_root: Path = Path("data/raw/fpl"),
    season: str = "2026-27",
    snapshot_timestamp: str | None = None,
    endpoint: str = FPL_FIXTURES_URL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_REQUEST_ATTEMPTS,
    opener: Opener = urlopen,
    clock: Clock = _utc_now,
    sleeper: Sleeper = time.sleep,
) -> Path:
    """Fetch the exact official fixtures response for a bootstrap snapshot."""
    snapshot_path = resolve_bootstrap_snapshot(
        raw_data_root, season, snapshot_timestamp
    )
    snapshot_dir = snapshot_path.parent
    fixture_path = snapshot_dir / "fixtures.json"
    manifest_path = snapshot_dir / "fixtures.manifest.json"
    if fixture_path.exists() or manifest_path.exists():
        existing = fixture_path if fixture_path.exists() else manifest_path
        raise RawOutputExistsError(
            f"raw fixture output already exists and will not be overwritten: {existing}"
        )

    logger.info("Fetching official FPL fixtures from %s", endpoint)
    body = _request_json_bytes(
        endpoint,
        opener=opener,
        timeout=timeout,
        attempts=attempts,
        sleeper=sleeper,
    )
    payload = json.loads(body)
    if not isinstance(payload, list):
        raise SourceRequestError("official fixture response is not a JSON list")
    retrieved_at = _iso_utc(clock())
    manifest = {
        "season": season,
        "snapshot_timestamp": snapshot_dir.name,
        "bootstrap_snapshot": snapshot_path.as_posix(),
        "bootstrap_sha256": _sha256_file(snapshot_path),
        "source_endpoint": endpoint,
        "retrieved_at": retrieved_at,
        "record_count": len(payload),
        "response_sha256": _sha256_bytes(body),
        "status": "complete",
    }

    _write_exact(fixture_path, body)
    _write_manifest_exclusive(manifest_path, manifest)
    logger.info("Fixture fetch succeeded with %d records", len(payload))
    logger.info("Raw fixture response saved to %s", fixture_path)
    return fixture_path


def fetch_player_histories_for_snapshot(
    *,
    raw_data_root: Path = Path("data/raw/fpl"),
    season: str = "2026-27",
    snapshot_timestamp: str | None = None,
    endpoint_template: str = FPL_ELEMENT_SUMMARY_URL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_REQUEST_ATTEMPTS,
    delay_seconds: float = DEFAULT_HISTORY_DELAY_SECONDS,
    progress_every: int = 25,
    opener: Opener = urlopen,
    clock: Clock = _utc_now,
    sleeper: Sleeper = time.sleep,
) -> Path:
    """Fetch exact element-summary responses for every snapshot player."""
    if delay_seconds < 0:
        raise ValueError("delay_seconds cannot be negative")
    snapshot_path = resolve_bootstrap_snapshot(
        raw_data_root, season, snapshot_timestamp
    )
    bootstrap = _load_bootstrap(snapshot_path)
    player_ids = [player.get("id") for player in bootstrap["elements"]]
    if any(not isinstance(player_id, int) for player_id in player_ids):
        raise OfficialDataError("bootstrap snapshot contains a non-integer player ID")
    if len(player_ids) != len(set(player_ids)):
        raise OfficialDataError("bootstrap snapshot contains duplicate player IDs")

    history_dir = snapshot_path.parent / "player_history"
    manifest_path = history_dir / "manifest.json"
    try:
        history_dir.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise RawOutputExistsError(
            "player-history output already exists and will not be overwritten: "
            f"{history_dir}"
        ) from exc

    started_at = _iso_utc(clock())
    manifest: dict[str, Any] = {
        "season": season,
        "snapshot_timestamp": snapshot_path.parent.name,
        "bootstrap_snapshot": snapshot_path.as_posix(),
        "bootstrap_sha256": _sha256_file(snapshot_path),
        "source_endpoint_template": endpoint_template,
        "started_at": started_at,
        "completed_at": None,
        "expected_player_ids": player_ids,
        "expected_count": len(player_ids),
        "success_count": 0,
        "failure_count": 0,
        "responses": [],
        "failures": [],
        "status": "in_progress",
    }
    _write_manifest_exclusive(manifest_path, manifest)

    logger.info("Fetching official FPL histories for %d players", len(player_ids))
    for index, player_id in enumerate(player_ids, start=1):
        endpoint = endpoint_template.format(fpl_player_id=player_id)
        try:
            body = _request_json_bytes(
                endpoint,
                opener=opener,
                timeout=timeout,
                attempts=attempts,
                sleeper=sleeper,
            )
            payload = json.loads(body)
            if not isinstance(payload, dict) or not isinstance(
                payload.get("history"), list
            ):
                raise SourceRequestError(
                    f"{endpoint} does not contain a history list"
                )
            response_path = history_dir / f"{player_id}.json"
            _write_exact(response_path, body)
            manifest["responses"].append(
                {
                    "fpl_player_id": player_id,
                    "source_endpoint": endpoint,
                    "retrieved_at": _iso_utc(clock()),
                    "response_sha256": _sha256_bytes(body),
                    "history_record_count": len(payload["history"]),
                }
            )
            manifest["success_count"] += 1
        except OfficialDataError as exc:
            logger.error("Player %d history fetch failed: %s", player_id, exc)
            manifest["failures"].append(
                {"fpl_player_id": player_id, "error": str(exc)}
            )
            manifest["failure_count"] += 1

        _update_manifest(manifest_path, manifest)
        if index % progress_every == 0 or index == len(player_ids):
            logger.info(
                "Player-history progress: %d/%d (%d failed)",
                index,
                len(player_ids),
                manifest["failure_count"],
            )
        if index < len(player_ids) and delay_seconds:
            sleeper(delay_seconds)

    manifest["completed_at"] = _iso_utc(clock())
    manifest["status"] = (
        "complete" if manifest["failure_count"] == 0 else "partial"
    )
    _update_manifest(manifest_path, manifest)

    if manifest["failures"]:
        failed_ids = [failure["fpl_player_id"] for failure in manifest["failures"]]
        raise PartialHistoryFetchError(failed_ids, manifest_path)

    logger.info("Player-history fetch succeeded for all %d players", len(player_ids))
    logger.info("Raw player histories saved under %s", history_dir)
    return history_dir
