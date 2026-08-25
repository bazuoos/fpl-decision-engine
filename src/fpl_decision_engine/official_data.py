"""Ingest fixture and player-history responses from official FPL APIs."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError

from .tls import network_error_reason, verified_urlopen
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
    player_ids = []
    for player in data["elements"]:
        if (
            not isinstance(player, dict)
            or not isinstance(player.get("id"), int)
            or isinstance(player.get("id"), bool)
        ):
            raise OfficialDataError(
                "bootstrap snapshot contains an invalid player ID"
            )
        player_ids.append(player["id"])
    if len(player_ids) != len(set(player_ids)):
        raise OfficialDataError("bootstrap snapshot contains duplicate player IDs")
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


def _request_json_bytes_with_attempts(
    endpoint: str,
    *,
    opener: Opener,
    timeout: float,
    attempts: int,
    sleeper: Sleeper,
) -> tuple[bytes, int]:
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
            return body, attempt
        except HTTPError as exc:
            last_error = SourceRequestError(
                f"{endpoint} returned HTTP status {exc.code}"
            )
        except (URLError, TimeoutError, OSError) as exc:
            last_error = SourceRequestError(
                f"could not reach {endpoint}: {network_error_reason(exc)}"
            )
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            last_error = SourceRequestError(f"{endpoint} returned invalid JSON")
        except SourceRequestError as exc:
            last_error = exc

        if attempt < attempts:
            sleeper(0.5 * attempt)

    assert last_error is not None
    raise last_error


def _request_json_bytes(
    endpoint: str,
    *,
    opener: Opener,
    timeout: float,
    attempts: int,
    sleeper: Sleeper,
) -> bytes:
    body, _ = _request_json_bytes_with_attempts(
        endpoint,
        opener=opener,
        timeout=timeout,
        attempts=attempts,
        sleeper=sleeper,
    )
    return body


def _validate_history_response(body: bytes, player_id: int, endpoint: str) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceRequestError(f"{endpoint} returned invalid JSON") from exc
    if not isinstance(payload, dict) or any(
        not isinstance(payload.get(field), list)
        for field in ("fixtures", "history", "history_past")
    ):
        raise SourceRequestError(
            f"{endpoint} does not have the expected element-summary structure"
        )
    if any(
        not isinstance(record, dict) or record.get("element") != player_id
        for record in payload["history"]
    ):
        raise SourceRequestError(
            f"{endpoint} contains history for a different player"
        )
    return payload


def _validate_fixture_payload(payload: Any, endpoint: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise SourceRequestError(f"{endpoint} is not a JSON fixture list")
    fixture_ids = []
    for index, fixture in enumerate(payload):
        if not isinstance(fixture, dict):
            raise SourceRequestError(
                f"{endpoint} fixture {index} is not a JSON object"
            )
        fixture_id = fixture.get("id")
        home_team = fixture.get("team_h")
        away_team = fixture.get("team_a")
        event = fixture.get("event")
        if (
            not isinstance(fixture_id, int)
            or isinstance(fixture_id, bool)
            or not isinstance(home_team, int)
            or isinstance(home_team, bool)
            or not isinstance(away_team, int)
            or isinstance(away_team, bool)
            or (event is not None and (
                not isinstance(event, int) or isinstance(event, bool)
            ))
        ):
            raise SourceRequestError(
                f"{endpoint} fixture {index} has invalid identity fields"
            )
        fixture_ids.append(fixture_id)
    if len(fixture_ids) != len(set(fixture_ids)):
        raise SourceRequestError(f"{endpoint} contains duplicate fixture IDs")
    return payload


def _write_exact(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary_path.open("xb") as output_file:
            output_file.write(body)
            output_file.flush()
            os.fsync(output_file.fileno())
        if path.exists():
            raise RawOutputExistsError(
                f"raw output already exists and will not be overwritten: {path}"
            )
        temporary_path.rename(path)
    except FileExistsError as exc:
        raise RawOutputExistsError(
            f"raw output already exists and will not be overwritten: {path}"
        ) from exc
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_manifest_exclusive(path: Path, manifest: dict[str, Any]) -> None:
    body = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    _write_exact(path, body)


def _update_manifest(path: Path, manifest: dict[str, Any]) -> None:
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        body = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        with temporary_path.open("xb") as manifest_file:
            manifest_file.write(body)
            manifest_file.flush()
            os.fsync(manifest_file.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def fetch_fixtures_for_snapshot(
    *,
    raw_data_root: Path = Path("data/raw/fpl"),
    season: str = "2026-27",
    snapshot_timestamp: str | None = None,
    endpoint: str = FPL_FIXTURES_URL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_REQUEST_ATTEMPTS,
    opener: Opener = verified_urlopen,
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
    body, attempts_used = _request_json_bytes_with_attempts(
        endpoint,
        opener=opener,
        timeout=timeout,
        attempts=attempts,
        sleeper=sleeper,
    )
    payload = _validate_fixture_payload(json.loads(body), endpoint)
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
        "request_attempts": attempts_used,
        "maximum_request_attempts": attempts,
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
    opener: Opener = verified_urlopen,
    clock: Clock = _utc_now,
    sleeper: Sleeper = time.sleep,
    resume: bool = False,
) -> Path:
    """Fetch exact element-summary responses, optionally resuming partial work."""
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
    bootstrap_hash = _sha256_file(snapshot_path)
    if not resume:
        try:
            history_dir.mkdir(exist_ok=False)
        except FileExistsError as exc:
            raise RawOutputExistsError(
                "player-history output already exists and will not be overwritten: "
                f"{history_dir}"
            ) from exc
        manifest: dict[str, Any] = {
            "season": season,
            "snapshot_timestamp": snapshot_path.parent.name,
            "bootstrap_snapshot": snapshot_path.as_posix(),
            "bootstrap_sha256": bootstrap_hash,
            "source_endpoint_template": endpoint_template,
            "started_at": _iso_utc(clock()),
            "completed_at": None,
            "expected_player_ids": player_ids,
            "expected_count": len(player_ids),
            "success_count": 0,
            "failure_count": 0,
            "responses": [],
            "failures": [],
            "remaining_player_ids": player_ids,
            "resume_count": 0,
            "reused_response_count": 0,
            "request_count": 0,
            "request_attempt_count": 0,
            "maximum_request_attempts": attempts,
            "delay_seconds": delay_seconds,
            "invalid_responses": [],
            "status": "in_progress",
        }
        _write_manifest_exclusive(manifest_path, manifest)
    else:
        if not manifest_path.is_file():
            raise OfficialDataError(
                f"cannot resume without player-history manifest: {manifest_path}"
            )
        try:
            manifest = json.loads(manifest_path.read_bytes())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OfficialDataError(
                f"cannot resume corrupt player-history manifest: {manifest_path}"
            ) from exc
        if manifest.get("status") == "complete":
            raise RawOutputExistsError(
                f"completed player-history collection is immutable: {history_dir}"
            )
        if (
            manifest.get("season") != season
            or manifest.get("snapshot_timestamp") != snapshot_path.parent.name
            or manifest.get("bootstrap_sha256") != bootstrap_hash
            or manifest.get("expected_player_ids") != player_ids
        ):
            raise OfficialDataError(
                "player-history manifest does not match the bootstrap snapshot"
            )

        response_metadata = {
            response.get("fpl_player_id"): response
            for response in manifest.get("responses", [])
            if isinstance(response, dict)
        }
        unexpected_ids = []
        for response_path in history_dir.glob("*.json"):
            if response_path.name == "manifest.json":
                continue
            try:
                file_player_id = int(response_path.stem)
            except ValueError:
                continue
            if file_player_id not in set(player_ids):
                unexpected_ids.append(file_player_id)
        if unexpected_ids:
            raise OfficialDataError(
                "unexpected player-history IDs contaminate the snapshot: "
                f"{sorted(unexpected_ids)}"
            )

        valid_responses = []
        invalid_responses = list(manifest.get("invalid_responses", []))
        quarantine_dir = history_dir / "quarantine"
        for player_id in player_ids:
            response_path = history_dir / f"{player_id}.json"
            metadata = response_metadata.get(player_id)
            if not response_path.exists():
                continue
            try:
                body = response_path.read_bytes()
                endpoint = endpoint_template.format(fpl_player_id=player_id)
                payload = _validate_history_response(body, player_id, endpoint)
                response_hash = _sha256_bytes(body)
                if isinstance(metadata, dict) and metadata.get(
                    "response_sha256"
                ) != response_hash:
                    raise SourceRequestError(
                        "response hash/provenance does not match its manifest"
                    )
                if not isinstance(metadata, dict):
                    recovered_at = datetime.fromtimestamp(
                        response_path.stat().st_mtime, tz=timezone.utc
                    )
                    metadata = {
                        "fpl_player_id": player_id,
                        "source_endpoint": endpoint,
                        "retrieved_at": _iso_utc(recovered_at),
                        "retrieved_at_source": "recovered_from_file_mtime",
                        "response_sha256": response_hash,
                        "history_record_count": len(payload["history"]),
                        "request_attempts": None,
                    }
            except (OSError, OfficialDataError) as exc:
                quarantine_dir.mkdir(exist_ok=True)
                quarantine_path = quarantine_dir / (
                    f"{player_id}.{uuid.uuid4().hex}.invalid.json"
                )
                response_path.replace(quarantine_path)
                invalid_responses.append(
                    {
                        "fpl_player_id": player_id,
                        "reason": str(exc),
                        "quarantined_path": quarantine_path.as_posix(),
                    }
                )
            else:
                valid_responses.append(metadata)

        manifest["responses"] = valid_responses
        manifest["success_count"] = len(valid_responses)
        manifest["failure_count"] = 0
        manifest["failures"] = []
        manifest["remaining_player_ids"] = sorted(
            set(player_ids) - {row["fpl_player_id"] for row in valid_responses}
        )
        manifest["resume_count"] = manifest.get("resume_count", 0) + 1
        manifest["reused_response_count"] = manifest.get(
            "reused_response_count", 0
        ) + len(valid_responses)
        manifest["last_resume_reused_count"] = len(valid_responses)
        manifest["last_resumed_at"] = _iso_utc(clock())
        manifest["invalid_responses"] = invalid_responses
        manifest["completed_at"] = None
        manifest["maximum_request_attempts"] = attempts
        manifest["delay_seconds"] = delay_seconds
        manifest["status"] = "in_progress"
        _update_manifest(manifest_path, manifest)

    completed_ids = {
        response["fpl_player_id"] for response in manifest.get("responses", [])
    }
    missing_ids = [player_id for player_id in player_ids if player_id not in completed_ids]
    logger.info(
        "%s official FPL histories: %d/%d already valid; %d requests remaining",
        "Resuming" if resume else "Fetching",
        len(completed_ids),
        len(player_ids),
        len(missing_ids),
    )
    try:
        for request_index, player_id in enumerate(missing_ids, start=1):
            endpoint = endpoint_template.format(fpl_player_id=player_id)
            manifest["request_count"] = manifest.get("request_count", 0) + 1
            try:
                body, attempts_used = _request_json_bytes_with_attempts(
                    endpoint,
                    opener=opener,
                    timeout=timeout,
                    attempts=attempts,
                    sleeper=sleeper,
                )
                manifest["request_attempt_count"] = manifest.get(
                    "request_attempt_count", 0
                ) + attempts_used
                payload = _validate_history_response(body, player_id, endpoint)
                response_path = history_dir / f"{player_id}.json"
                _write_exact(response_path, body)
                manifest["responses"].append(
                    {
                        "fpl_player_id": player_id,
                        "source_endpoint": endpoint,
                        "retrieved_at": _iso_utc(clock()),
                        "response_sha256": _sha256_bytes(body),
                        "history_record_count": len(payload["history"]),
                        "request_attempts": attempts_used,
                    }
                )
                manifest["success_count"] += 1
            except OfficialDataError as exc:
                manifest["request_attempt_count"] = manifest.get(
                    "request_attempt_count", 0
                ) + attempts
                logger.error("Player %d history fetch failed: %s", player_id, exc)
                manifest["failures"].append(
                    {"fpl_player_id": player_id, "error": str(exc)}
                )
                manifest["failure_count"] += 1

            successful_ids = {
                response["fpl_player_id"] for response in manifest["responses"]
            }
            manifest["remaining_player_ids"] = sorted(
                set(player_ids) - successful_ids
            )
            _update_manifest(manifest_path, manifest)
            completed = len(successful_ids)
            if completed % progress_every == 0 or request_index == len(missing_ids):
                logger.info(
                    "Player-history progress: %d/%d (%d unresolved)",
                    completed,
                    len(player_ids),
                    len(manifest["remaining_player_ids"]),
                )
            if request_index < len(missing_ids) and delay_seconds:
                sleeper(delay_seconds)
    except KeyboardInterrupt:
        manifest["interrupted_at"] = _iso_utc(clock())
        manifest["status"] = "incomplete"
        manifest["completed_at"] = None
        _update_manifest(manifest_path, manifest)
        raise

    manifest["status"] = (
        "complete" if not manifest["remaining_player_ids"] else "partial"
    )
    manifest["completed_at"] = (
        _iso_utc(clock()) if manifest["status"] == "complete" else None
    )
    manifest["last_attempt_finished_at"] = _iso_utc(clock())
    _update_manifest(manifest_path, manifest)

    if manifest["remaining_player_ids"]:
        failed_ids = manifest["remaining_player_ids"]
        raise PartialHistoryFetchError(failed_ids, manifest_path)

    logger.info("Player-history fetch succeeded for all %d players", len(player_ids))
    logger.info("Raw player histories saved under %s", history_dir)
    return history_dir
