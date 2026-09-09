"""Restart-safe one-shot monitoring for finalized official FPL data."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .evaluation import (
    EvaluationError,
    EvaluationOutputs,
    evaluate_xfp,
    validate_realized_gameweek_snapshot,
)
from .official_data import (
    DEFAULT_HISTORY_DELAY_SECONDS,
    DEFAULT_REQUEST_ATTEMPTS,
    DEFAULT_TIMEOUT_SECONDS,
    FPL_FIXTURES_URL,
    NonRetryableSourceError,
    Opener,
    Sleeper,
    SourceRequestError,
    _request_json_bytes_with_attempts,
    _validate_fixture_payload,
)
from .pipeline import FPL_BOOTSTRAP_STATIC_URL
from .refresh import (
    RefreshError,
    RefreshIncompleteError,
    RefreshResult,
    refresh_fpl_data,
    validate_completed_refresh_snapshot,
)
from .tls import verified_no_redirect_urlopen


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
STABILITY_INTERVAL = timedelta(minutes=15)
SNAPSHOT_PATTERN = re.compile(r"\d{8}T\d{6}\.\d{6}Z")
SEASON_PATTERN = re.compile(r"\d{4}-\d{2}")
WAITING = "WAITING"
REFRESHING = "REFRESHING"
REALIZED_COMPLETE = "REALIZED_COMPLETE"
COMPLETE = "COMPLETE"
REVIEW_REQUIRED = "REVIEW_REQUIRED"


class CompletionMonitorError(Exception):
    """Base exception for expected completion-monitor failures."""


class RetryableProbeError(CompletionMonitorError):
    """Raised when a bounded public-data probe can be retried later."""


class MonitorLockedError(CompletionMonitorError):
    """Raised when another invocation owns the target lock."""


class MonitorLockNotFoundError(CompletionMonitorError):
    """Raised when an explicit monitor unlock has no lock to remove."""


class MonitorReviewError(CompletionMonitorError):
    """Raised when operator review is required before another attempt."""


@dataclass(frozen=True)
class CompletionProbe:
    ready: bool
    observed_at: datetime
    deadline: datetime
    event_finished: bool
    event_data_checked: bool
    fixture_count: int
    finished_fixture_count: int
    semantic_sha256: str
    bootstrap_sha256: str
    fixtures_sha256: str


@dataclass(frozen=True)
class MonitorOutcome:
    status: str
    detail: str
    realized_snapshot_timestamp: str | None = None
    evaluation_directory: Path | None = None


@dataclass(frozen=True)
class MonitorUnlockResult:
    lock_path: Path
    lock_metadata: dict[str, Any] | None


RefreshRunner = Callable[..., RefreshResult]
EvaluationRunner = Callable[..., EvaluationOutputs]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise CompletionMonitorError("monitor timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _parse_utc(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise CompletionMonitorError(f"{field} must be an ISO UTC string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CompletionMonitorError(f"{field} is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        raise CompletionMonitorError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _target_directory(control_data_root: Path, season: str, gameweek: int) -> Path:
    _validate_target(season, gameweek)
    return control_data_root / season / f"gameweek={gameweek}"


def _validate_target(season: str, gameweek: int) -> None:
    if not SEASON_PATTERN.fullmatch(season):
        raise CompletionMonitorError(f"invalid season: {season}")
    if isinstance(gameweek, bool) or not isinstance(gameweek, int) or not 1 <= gameweek <= 38:
        raise CompletionMonitorError("target_gameweek must be an integer from 1 to 38")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CompletionMonitorError(f"could not read monitor JSON: {path}") from exc


def _json_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _write_mutable_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(_json_body(payload))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_exclusive_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    linked = False
    try:
        with temporary.open("xb") as output:
            output.write(_json_body(payload))
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
            linked = True
        except FileExistsError as exc:
            raise MonitorReviewError(f"monitor output already exists: {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)
    if not linked:
        raise MonitorReviewError(f"could not publish monitor output: {path}")


def _new_state(season: str, gameweek: int, now: datetime) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "season": season,
        "target_gameweek": gameweek,
        "status": WAITING,
        "updated_at": _iso_utc(now),
        "first_ready_observation": None,
        "last_observation": None,
        "review": None,
    }


def _load_state(path: Path, season: str, gameweek: int, now: datetime) -> dict[str, Any]:
    if not path.exists():
        return _new_state(season, gameweek, now)
    state = _load_json(path)
    if (
        not isinstance(state, dict)
        or state.get("schema_version") != SCHEMA_VERSION
        or state.get("season") != season
        or state.get("target_gameweek") != gameweek
        or state.get("status")
        not in {WAITING, REFRESHING, REALIZED_COMPLETE, COMPLETE, REVIEW_REQUIRED}
    ):
        raise MonitorReviewError("monitor state identity or schema is invalid")
    return state


def _acquire_lock(target_directory: Path, season: str, gameweek: int, now: datetime) -> Path:
    target_directory.mkdir(parents=True, exist_ok=True)
    lock_path = target_directory / ".completion-monitor.lock"
    body = _json_body(
        {
            "schema_version": SCHEMA_VERSION,
            "season": season,
            "target_gameweek": gameweek,
            "pid": os.getpid(),
            "acquired_at": _iso_utc(now),
        }
    )
    try:
        with lock_path.open("xb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise MonitorLockedError(f"completion monitor target is locked: {lock_path}") from exc
    return lock_path


def unlock_completion_monitor(
    *,
    control_data_root: Path = Path("data/operations/completion-monitor/fpl"),
    season: str,
    target_gameweek: int,
) -> MonitorUnlockResult:
    """Explicitly remove a target lock after the operator verifies no process runs."""
    target = _target_directory(control_data_root, season, target_gameweek)
    lock_path = target / ".completion-monitor.lock"
    if not lock_path.is_file():
        raise MonitorLockNotFoundError(f"monitor lock does not exist: {lock_path}")
    metadata: dict[str, Any] | None = None
    try:
        loaded = json.loads(lock_path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Monitor lock metadata is unavailable or malformed: %s", lock_path)
    else:
        if isinstance(loaded, dict):
            metadata = loaded
    lock_path.unlink()
    return MonitorUnlockResult(lock_path=lock_path, lock_metadata=metadata)


def _request_probe_body(
    endpoint: str,
    *,
    opener: Opener,
    timeout: float,
    attempts: int,
    sleeper: Sleeper,
) -> bytes:
    try:
        body, _ = _request_json_bytes_with_attempts(
            endpoint,
            opener=opener,
            timeout=timeout,
            attempts=attempts,
            sleeper=sleeper,
        )
        return body
    except NonRetryableSourceError as exc:
        raise CompletionMonitorError("official completion probe failed closed") from exc
    except SourceRequestError as exc:
        raise RetryableProbeError("official completion probe failed") from exc


def probe_gameweek_completion(
    *,
    target_gameweek: int,
    opener: Opener = verified_no_redirect_urlopen,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_REQUEST_ATTEMPTS,
    sleeper: Sleeper = time.sleep,
    clock: Callable[[], datetime] = _utc_now,
) -> CompletionProbe:
    """Read public status into memory without publishing a raw snapshot."""
    if not 1 <= target_gameweek <= 38:
        raise CompletionMonitorError("target_gameweek must be from 1 to 38")
    observed_at = clock()
    if observed_at.tzinfo is None:
        raise CompletionMonitorError("monitor clock must be timezone-aware")
    observed_at = observed_at.astimezone(timezone.utc)
    bootstrap_body = _request_probe_body(
        FPL_BOOTSTRAP_STATIC_URL,
        opener=opener,
        timeout=timeout,
        attempts=attempts,
        sleeper=sleeper,
    )
    fixtures_body = _request_probe_body(
        FPL_FIXTURES_URL,
        opener=opener,
        timeout=timeout,
        attempts=attempts,
        sleeper=sleeper,
    )
    try:
        bootstrap = json.loads(bootstrap_body)
        fixture_payload = json.loads(fixtures_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CompletionMonitorError("probe response is not valid JSON") from exc
    if not isinstance(bootstrap, dict) or not isinstance(bootstrap.get("events"), list):
        raise CompletionMonitorError("bootstrap probe lacks an events list")
    target_events = [
        event
        for event in bootstrap["events"]
        if isinstance(event, dict) and event.get("id") == target_gameweek
    ]
    if len(target_events) != 1:
        raise CompletionMonitorError("bootstrap must contain exactly one target event")
    event = target_events[0]
    if (
        not isinstance(event.get("id"), int)
        or isinstance(event.get("id"), bool)
        or type(event.get("finished")) is not bool
        or type(event.get("data_checked")) is not bool
    ):
        raise CompletionMonitorError("target event identity or completion fields are invalid")
    deadline = _parse_utc(event.get("deadline_time"), "target deadline_time")
    try:
        fixtures = _validate_fixture_payload(fixture_payload, FPL_FIXTURES_URL)
    except SourceRequestError as exc:
        raise CompletionMonitorError("fixture probe has invalid identity fields") from exc
    target_fixtures = [row for row in fixtures if row["event"] == target_gameweek]
    if not target_fixtures:
        raise CompletionMonitorError("fixture probe contains no target fixtures")
    if any(type(row.get("finished")) is not bool for row in target_fixtures):
        raise CompletionMonitorError("target fixture completion fields are invalid")
    canonical = {
        "event": {
            key: event[key]
            for key in ("id", "deadline_time", "finished", "data_checked")
        },
        "fixtures": [
            {key: row[key] for key in ("id", "event", "team_h", "team_a", "finished")}
            for row in sorted(target_fixtures, key=lambda item: item["id"])
        ],
    }
    semantic_body = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    finished_count = sum(row["finished"] is True for row in target_fixtures)
    ready = (
        event["finished"] is True
        and event["data_checked"] is True
        and finished_count == len(target_fixtures)
        and observed_at > deadline
    )
    return CompletionProbe(
        ready=ready,
        observed_at=observed_at,
        deadline=deadline,
        event_finished=event["finished"],
        event_data_checked=event["data_checked"],
        fixture_count=len(target_fixtures),
        finished_fixture_count=finished_count,
        semantic_sha256=_sha256_bytes(semantic_body),
        bootstrap_sha256=_sha256_bytes(bootstrap_body),
        fixtures_sha256=_sha256_bytes(fixtures_body),
    )


def _observation(probe: CompletionProbe) -> dict[str, Any]:
    return {
        "observed_at": _iso_utc(probe.observed_at),
        "deadline": _iso_utc(probe.deadline),
        "ready": probe.ready,
        "event_finished": probe.event_finished,
        "event_data_checked": probe.event_data_checked,
        "fixture_count": probe.fixture_count,
        "finished_fixture_count": probe.finished_fixture_count,
        "semantic_sha256": probe.semantic_sha256,
        "bootstrap_sha256": probe.bootstrap_sha256,
        "fixtures_sha256": probe.fixtures_sha256,
    }


def _set_review(
    state: dict[str, Any],
    state_path: Path,
    *,
    now: datetime,
    scope: str,
    error: BaseException | str,
    snapshot_timestamp: str | None = None,
) -> MonitorOutcome:
    detail = str(error).replace("\n", " ")[:1000]
    state.update(
        {
            "status": REVIEW_REQUIRED,
            "updated_at": _iso_utc(now),
            "review": {
                "scope": scope,
                "error_type": (
                    type(error).__name__
                    if isinstance(error, BaseException)
                    else "MonitorState"
                ),
                "detail": detail,
                "snapshot_timestamp": snapshot_timestamp,
                "recorded_at": _iso_utc(now),
            },
        }
    )
    _write_mutable_json(state_path, state)
    return MonitorOutcome(REVIEW_REQUIRED, detail, snapshot_timestamp)


def _realized_paths(
    raw_data_root: Path, clean_data_root: Path, season: str, snapshot: str
) -> tuple[Path, Path, Path]:
    return (
        raw_data_root / season / snapshot / "bootstrap-static.json",
        clean_data_root / season / snapshot / "fixtures.parquet",
        clean_data_root / season / snapshot / "player_gameweek_history.parquet",
    )


def _validate_realized_candidate(
    *,
    raw_data_root: Path,
    clean_data_root: Path,
    season: str,
    target_gameweek: int,
    snapshot_timestamp: str,
) -> RefreshResult:
    result = validate_completed_refresh_snapshot(
        raw_data_root=raw_data_root,
        clean_data_root=clean_data_root,
        season=season,
        snapshot_timestamp=snapshot_timestamp,
    )
    bootstrap, fixtures, history = _realized_paths(
        raw_data_root, clean_data_root, season, snapshot_timestamp
    )
    validate_realized_gameweek_snapshot(
        realized_bootstrap_path=bootstrap,
        realized_fixtures_path=fixtures,
        realized_history_path=history,
        target_gameweek=target_gameweek,
        realized_snapshot_timestamp=snapshot_timestamp,
    )
    return result


def _candidate_is_provisionally_final(
    manifest_path: Path, season: str, gameweek: int, snapshot: str
) -> bool:
    try:
        manifest = json.loads(manifest_path.read_bytes())
        bootstrap_path = manifest_path.parent / "bootstrap-static.json"
        bootstrap = json.loads(bootstrap_path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not (
        isinstance(manifest, dict)
        and manifest.get("status") == "complete"
        and manifest.get("season") == season
        and manifest.get("snapshot_timestamp") == snapshot
        and isinstance(bootstrap, dict)
        and isinstance(bootstrap.get("events"), list)
    ):
        return False
    events = [
        row
        for row in bootstrap["events"]
        if isinstance(row, dict) and row.get("id") == gameweek
    ]
    return (
        len(events) == 1
        and events[0].get("finished") is True
        and events[0].get("data_checked") is True
    )


def _find_realized_candidate(
    *,
    raw_data_root: Path,
    clean_data_root: Path,
    season: str,
    target_gameweek: int,
) -> RefreshResult | None:
    season_root = raw_data_root / season
    if not season_root.is_dir():
        return None
    for path in sorted(season_root.iterdir(), key=lambda item: item.name):
        snapshot = path.name
        manifest_path = path / "refresh.manifest.json"
        if not SNAPSHOT_PATTERN.fullmatch(snapshot) or not manifest_path.is_file():
            continue
        if not _candidate_is_provisionally_final(
            manifest_path, season, target_gameweek, snapshot
        ):
            continue
        try:
            return _validate_realized_candidate(
                raw_data_root=raw_data_root,
                clean_data_root=clean_data_root,
                season=season,
                target_gameweek=target_gameweek,
                snapshot_timestamp=snapshot,
            )
        except (RefreshError, EvaluationError, OSError):
            continue
    return None


def _realized_receipt_payload(
    result: RefreshResult, season: str, gameweek: int, accepted_at: datetime
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "complete",
        "season": season,
        "target_gameweek": gameweek,
        "snapshot_timestamp": result.snapshot_timestamp,
        "manifest_path": result.manifest_path.as_posix(),
        "manifest_sha256": _sha256_file(result.manifest_path),
        "accepted_at": _iso_utc(accepted_at),
    }


def _validate_realized_receipt(
    path: Path,
    *,
    raw_data_root: Path,
    clean_data_root: Path,
    season: str,
    target_gameweek: int,
) -> RefreshResult:
    receipt = _load_json(path)
    if not isinstance(receipt, dict):
        raise MonitorReviewError("realized receipt is not a JSON object")
    snapshot = receipt.get("snapshot_timestamp")
    if (
        receipt.get("schema_version") != SCHEMA_VERSION
        or receipt.get("status") != "complete"
        or receipt.get("season") != season
        or receipt.get("target_gameweek") != target_gameweek
        or not isinstance(snapshot, str)
        or not SNAPSHOT_PATTERN.fullmatch(snapshot)
    ):
        raise MonitorReviewError("realized receipt identity is invalid")
    result = _validate_realized_candidate(
        raw_data_root=raw_data_root,
        clean_data_root=clean_data_root,
        season=season,
        target_gameweek=target_gameweek,
        snapshot_timestamp=snapshot,
    )
    if (
        receipt.get("manifest_path") != result.manifest_path.as_posix()
        or receipt.get("manifest_sha256") != _sha256_file(result.manifest_path)
    ):
        raise MonitorReviewError("realized receipt manifest binding is invalid")
    return result


def _publish_realized_receipt(
    receipt_path: Path,
    result: RefreshResult,
    season: str,
    gameweek: int,
    now: datetime,
) -> None:
    _write_exclusive_json(
        receipt_path, _realized_receipt_payload(result, season, gameweek, now)
    )


def _evaluation_paths(
    prediction_data_root: Path,
    raw_data_root: Path,
    clean_data_root: Path,
    season: str,
    gameweek: int,
    prediction_snapshot: str,
    realized_snapshot: str,
) -> dict[str, Path]:
    prediction_directory = (
        prediction_data_root
        / season
        / prediction_snapshot
        / f"gameweek={gameweek}"
    )
    bootstrap, fixtures, history = _realized_paths(
        raw_data_root, clean_data_root, season, realized_snapshot
    )
    return {
        "prediction": prediction_directory / "xfp_v01_gameweek.parquet",
        "fixture_prediction": prediction_directory / "xfp_v01_fixtures.parquet",
        "bootstrap": bootstrap,
        "fixtures": fixtures,
        "history": history,
    }


def _validate_evaluation_manifest(
    manifest_path: Path,
    *,
    expected_paths: dict[str, Path],
    season: str,
    gameweek: int,
    prediction_snapshot: str,
    realized_snapshot: str,
) -> Path:
    manifest = _load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise MonitorReviewError("evaluation manifest is not a JSON object")
    prediction = manifest.get("prediction")
    realized = manifest.get("realized_data")
    if not isinstance(prediction, dict) or not isinstance(realized, dict):
        raise MonitorReviewError("evaluation manifest provenance is invalid")
    if (
        manifest.get("status") != "complete"
        or manifest.get("season") != season
        or manifest.get("target_gameweek") != gameweek
        or manifest.get("model_version") != "v0.1"
        or prediction.get("snapshot_timestamp") != prediction_snapshot
        or realized.get("snapshot_timestamp") != realized_snapshot
    ):
        raise MonitorReviewError("evaluation manifest identity is invalid")
    bindings = (
        (prediction, "source_path", "sha256", expected_paths["prediction"]),
        (
            prediction,
            "fixture_source_path",
            "fixture_sha256",
            expected_paths["fixture_prediction"],
        ),
        (realized, "bootstrap_path", "bootstrap_sha256", expected_paths["bootstrap"]),
        (realized, "fixtures_path", "fixtures_sha256", expected_paths["fixtures"]),
        (realized, "history_path", "history_sha256", expected_paths["history"]),
    )
    initial: dict[Path, str] = {}
    for metadata, path_field, hash_field, expected in bindings:
        if metadata.get(path_field) != expected.as_posix() or not expected.is_file():
            raise MonitorReviewError("evaluation manifest path binding is invalid")
        digest = _sha256_file(expected)
        if metadata.get(hash_field) != digest:
            raise MonitorReviewError("evaluation manifest input hash is invalid")
        initial[expected] = digest
    required_outputs = {
        "player": "player_evaluation.parquet",
        "metrics": "metrics.parquet",
        "position": "position_metrics.parquet",
        "diagnostic": "diagnostic_metrics.parquet",
        "ranking": "ranking_summary.parquet",
    }
    output_metadata = manifest.get("outputs")
    if not isinstance(output_metadata, dict) or set(output_metadata) != set(
        required_outputs
    ):
        raise MonitorReviewError("evaluation output metadata is incomplete")
    output_hashes: dict[Path, str] = {}
    for key, name in required_outputs.items():
        output_path = manifest_path.parent / name
        metadata = output_metadata.get(key)
        digest = _sha256_file(output_path) if output_path.is_file() else None
        if (
            not isinstance(metadata, dict)
            or metadata.get("path") != output_path.as_posix()
            or digest is None
            or metadata.get("sha256") != digest
        ):
            raise MonitorReviewError("evaluation output hash binding is invalid")
        output_hashes[output_path] = digest
    if any(
        _sha256_file(path) != digest
        for path, digest in {**initial, **output_hashes}.items()
    ):
        raise MonitorReviewError("evaluation artifact changed during validation")
    return manifest_path.parent


def _find_evaluation_candidate(
    *,
    evaluation_data_root: Path,
    expected_paths: dict[str, Path],
    season: str,
    gameweek: int,
    prediction_snapshot: str,
    realized_snapshot: str,
) -> Path | None:
    root = evaluation_data_root / season / f"gameweek={gameweek}" / "v0.1"
    if not root.is_dir():
        return None
    for directory in sorted(root.iterdir(), key=lambda item: item.name):
        manifest_path = directory / "manifest.json"
        if not directory.is_dir() or not SNAPSHOT_PATTERN.fullmatch(directory.name):
            continue
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_bytes())
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        prediction = manifest.get("prediction")
        realized = manifest.get("realized_data")
        if not (
            isinstance(prediction, dict)
            and isinstance(realized, dict)
            and manifest.get("season") == season
            and manifest.get("target_gameweek") == gameweek
            and manifest.get("model_version") == "v0.1"
            and prediction.get("snapshot_timestamp") == prediction_snapshot
            and realized.get("snapshot_timestamp") == realized_snapshot
        ):
            continue
        return _validate_evaluation_manifest(
            manifest_path,
            expected_paths=expected_paths,
            season=season,
            gameweek=gameweek,
            prediction_snapshot=prediction_snapshot,
            realized_snapshot=realized_snapshot,
        )
    return None


def _evaluation_receipt_payload(
    evaluation_directory: Path,
    *,
    season: str,
    gameweek: int,
    prediction_snapshot: str,
    realized_snapshot: str,
    accepted_at: datetime,
) -> dict[str, Any]:
    manifest_path = evaluation_directory / "manifest.json"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "complete",
        "season": season,
        "target_gameweek": gameweek,
        "model_version": "v0.1",
        "prediction_snapshot_timestamp": prediction_snapshot,
        "realized_snapshot_timestamp": realized_snapshot,
        "evaluation_directory": evaluation_directory.as_posix(),
        "manifest_sha256": _sha256_file(manifest_path),
        "accepted_at": _iso_utc(accepted_at),
    }


def _ensure_evaluation(
    *,
    receipt_path: Path,
    state: dict[str, Any],
    state_path: Path,
    realized: RefreshResult,
    prediction_snapshot: str,
    raw_data_root: Path,
    clean_data_root: Path,
    feature_data_root: Path,
    prediction_data_root: Path,
    evaluation_data_root: Path,
    season: str,
    gameweek: int,
    now: datetime,
    runner: EvaluationRunner,
) -> MonitorOutcome:
    if not SNAPSHOT_PATTERN.fullmatch(prediction_snapshot):
        return _set_review(
            state,
            state_path,
            now=now,
            scope="evaluation",
            error="prediction snapshot timestamp is invalid",
            snapshot_timestamp=realized.snapshot_timestamp,
        )
    paths = _evaluation_paths(
        prediction_data_root,
        raw_data_root,
        clean_data_root,
        season,
        gameweek,
        prediction_snapshot,
        realized.snapshot_timestamp,
    )
    try:
        if receipt_path.exists():
            receipt = _load_json(receipt_path)
            if not isinstance(receipt, dict):
                raise MonitorReviewError("evaluation receipt is invalid")
            if (
                receipt.get("schema_version") != SCHEMA_VERSION
                or receipt.get("status") != "complete"
                or receipt.get("model_version") != "v0.1"
                or receipt.get("prediction_snapshot_timestamp") != prediction_snapshot
                or receipt.get("realized_snapshot_timestamp")
                != realized.snapshot_timestamp
                or receipt.get("season") != season
                or receipt.get("target_gameweek") != gameweek
            ):
                raise MonitorReviewError("evaluation receipt identity conflicts")
            directory_text = receipt.get("evaluation_directory")
            if not isinstance(directory_text, str):
                raise MonitorReviewError("evaluation receipt path is invalid")
            directory = Path(directory_text)
            expected_parent = (
                evaluation_data_root / season / f"gameweek={gameweek}" / "v0.1"
            )
            if (
                directory.parent != expected_parent
                or not SNAPSHOT_PATTERN.fullmatch(directory.name)
            ):
                raise MonitorReviewError("evaluation receipt directory is outside target")
            validated = _validate_evaluation_manifest(
                directory / "manifest.json",
                expected_paths=paths,
                season=season,
                gameweek=gameweek,
                prediction_snapshot=prediction_snapshot,
                realized_snapshot=realized.snapshot_timestamp,
            )
            if (
                directory != validated
                or receipt.get("manifest_sha256")
                != _sha256_file(directory / "manifest.json")
            ):
                raise MonitorReviewError("evaluation receipt manifest binding is invalid")
            state.update(status=COMPLETE, updated_at=_iso_utc(now), review=None)
            _write_mutable_json(state_path, state)
            return MonitorOutcome(
                COMPLETE,
                "realized snapshot and evaluation were already complete",
                realized.snapshot_timestamp,
                directory,
            )

        candidate = _find_evaluation_candidate(
            evaluation_data_root=evaluation_data_root,
            expected_paths=paths,
            season=season,
            gameweek=gameweek,
            prediction_snapshot=prediction_snapshot,
            realized_snapshot=realized.snapshot_timestamp,
        )
        if candidate is None:
            outputs = runner(
                target_gameweek=gameweek,
                model_version="v0.1",
                prediction_snapshot_timestamp=prediction_snapshot,
                realized_snapshot_timestamp=realized.snapshot_timestamp,
                raw_data_root=raw_data_root,
                clean_data_root=clean_data_root,
                feature_data_root=feature_data_root,
                prediction_data_root=prediction_data_root,
                evaluation_data_root=evaluation_data_root,
                season=season,
            )
            candidate = _validate_evaluation_manifest(
                outputs.manifest_path,
                expected_paths=paths,
                season=season,
                gameweek=gameweek,
                prediction_snapshot=prediction_snapshot,
                realized_snapshot=realized.snapshot_timestamp,
            )
        _write_exclusive_json(
            receipt_path,
            _evaluation_receipt_payload(
                candidate,
                season=season,
                gameweek=gameweek,
                prediction_snapshot=prediction_snapshot,
                realized_snapshot=realized.snapshot_timestamp,
                accepted_at=now,
            ),
        )
    except Exception as exc:
        return _set_review(
            state,
            state_path,
            now=now,
            scope="evaluation",
            error=exc,
            snapshot_timestamp=realized.snapshot_timestamp,
        )
    state.update(status=COMPLETE, updated_at=_iso_utc(now), review=None)
    _write_mutable_json(state_path, state)
    return MonitorOutcome(
        COMPLETE,
        "realized snapshot and evaluation are complete",
        realized.snapshot_timestamp,
        candidate,
    )


def monitor_completion(
    *,
    season: str,
    target_gameweek: int,
    prediction_snapshot_timestamp: str | None = None,
    raw_data_root: Path = Path("data/raw/fpl"),
    clean_data_root: Path = Path("data/clean/fpl"),
    feature_data_root: Path = Path("data/features/fpl"),
    prediction_data_root: Path = Path("data/predictions/fpl"),
    evaluation_data_root: Path = Path("data/evaluations/fpl"),
    control_data_root: Path = Path("data/operations/completion-monitor/fpl"),
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_REQUEST_ATTEMPTS,
    history_delay_seconds: float = DEFAULT_HISTORY_DELAY_SECONDS,
    opener: Opener = verified_no_redirect_urlopen,
    sleeper: Sleeper = time.sleep,
    clock: Callable[[], datetime] = _utc_now,
    refresh_runner: RefreshRunner = refresh_fpl_data,
    evaluation_runner: EvaluationRunner = evaluate_xfp,
) -> MonitorOutcome:
    """Reconcile one target, make one probe, and perform at most one refresh."""
    _validate_target(season, target_gameweek)
    if (
        prediction_snapshot_timestamp is not None
        and not SNAPSHOT_PATTERN.fullmatch(prediction_snapshot_timestamp)
    ):
        raise CompletionMonitorError("prediction snapshot timestamp is invalid")
    started = clock()
    if started.tzinfo is None:
        raise CompletionMonitorError("monitor clock must be timezone-aware")
    started = started.astimezone(timezone.utc)
    target = _target_directory(control_data_root, season, target_gameweek)
    lock_path = _acquire_lock(target, season, target_gameweek, started)
    state_path = target / "state.json"
    realized_receipt_path = target / "realized-receipt.json"
    evaluation_receipt_path = target / "evaluation-receipt.json"
    try:
        state = _load_state(state_path, season, target_gameweek, started)
        if evaluation_receipt_path.exists() and not realized_receipt_path.exists():
            return _set_review(
                state,
                state_path,
                now=started,
                scope="receipt",
                error="evaluation receipt exists without a realized receipt",
            )
        if realized_receipt_path.exists():
            try:
                realized = _validate_realized_receipt(
                    realized_receipt_path,
                    raw_data_root=raw_data_root,
                    clean_data_root=clean_data_root,
                    season=season,
                    target_gameweek=target_gameweek,
                )
            except (CompletionMonitorError, RefreshError, EvaluationError, OSError) as exc:
                return _set_review(
                    state,
                    state_path,
                    now=started,
                    scope="receipt",
                    error=exc,
                )
            if state.get("status") == REVIEW_REQUIRED:
                review = state.get("review")
                return MonitorOutcome(
                    REVIEW_REQUIRED,
                    (
                        str(review.get("detail", "operator review is required"))
                        if isinstance(review, dict)
                        else "operator review is required"
                    ),
                    realized.snapshot_timestamp,
                )
            if prediction_snapshot_timestamp is None:
                if evaluation_receipt_path.exists():
                    return _set_review(
                        state,
                        state_path,
                        now=started,
                        scope="receipt",
                        error=(
                            "an evaluation receipt exists but no prediction snapshot "
                            "was requested"
                        ),
                        snapshot_timestamp=realized.snapshot_timestamp,
                    )
                state.update(
                    status=REALIZED_COMPLETE, updated_at=_iso_utc(started), review=None
                )
                _write_mutable_json(state_path, state)
                return MonitorOutcome(
                    REALIZED_COMPLETE,
                    "realized snapshot was already complete; evaluation not requested",
                    realized.snapshot_timestamp,
                )
            return _ensure_evaluation(
                receipt_path=evaluation_receipt_path,
                state=state,
                state_path=state_path,
                realized=realized,
                prediction_snapshot=prediction_snapshot_timestamp,
                raw_data_root=raw_data_root,
                clean_data_root=clean_data_root,
                feature_data_root=feature_data_root,
                prediction_data_root=prediction_data_root,
                evaluation_data_root=evaluation_data_root,
                season=season,
                gameweek=target_gameweek,
                now=started,
                runner=evaluation_runner,
            )

        if state.get("status") == REVIEW_REQUIRED:
            review = state.get("review")
            detail = (
                str(review.get("detail"))
                if isinstance(review, dict)
                else "operator review is required"
            )
            return MonitorOutcome(REVIEW_REQUIRED, detail)

        candidate = _find_realized_candidate(
            raw_data_root=raw_data_root,
            clean_data_root=clean_data_root,
            season=season,
            target_gameweek=target_gameweek,
        )
        if candidate is not None:
            try:
                _publish_realized_receipt(
                    realized_receipt_path,
                    candidate,
                    season,
                    target_gameweek,
                    started,
                )
            except CompletionMonitorError as exc:
                return _set_review(
                    state,
                    state_path,
                    now=started,
                    scope="receipt",
                    error=exc,
                    snapshot_timestamp=candidate.snapshot_timestamp,
                )
            state.update(
                status=REALIZED_COMPLETE,
                updated_at=_iso_utc(started),
                review=None,
            )
            _write_mutable_json(state_path, state)
            if prediction_snapshot_timestamp is None:
                return MonitorOutcome(
                    REALIZED_COMPLETE,
                    "adopted an existing finalized realized snapshot",
                    candidate.snapshot_timestamp,
                )
            return _ensure_evaluation(
                receipt_path=evaluation_receipt_path,
                state=state,
                state_path=state_path,
                realized=candidate,
                prediction_snapshot=prediction_snapshot_timestamp,
                raw_data_root=raw_data_root,
                clean_data_root=clean_data_root,
                feature_data_root=feature_data_root,
                prediction_data_root=prediction_data_root,
                evaluation_data_root=evaluation_data_root,
                season=season,
                gameweek=target_gameweek,
                now=started,
                runner=evaluation_runner,
            )

        if state.get("status") == REFRESHING:
            return _set_review(
                state,
                state_path,
                now=started,
                scope="realized",
                error="a previous refresh attempt did not publish a valid completed snapshot",
                snapshot_timestamp=state.get("refresh_snapshot_timestamp"),
            )

        try:
            probe = probe_gameweek_completion(
                target_gameweek=target_gameweek,
                opener=opener,
                timeout=timeout,
                attempts=attempts,
                sleeper=sleeper,
                clock=lambda: started,
            )
        except RetryableProbeError:
            raise
        except CompletionMonitorError as exc:
            return _set_review(
                state,
                state_path,
                now=started,
                scope="probe",
                error=exc,
            )
        current_observation = _observation(probe)
        state.update(last_observation=current_observation, updated_at=_iso_utc(started))
        if not probe.ready:
            state.update(
                status=WAITING,
                first_ready_observation=None,
                review=None,
            )
            _write_mutable_json(state_path, state)
            return MonitorOutcome(WAITING, "official target gameweek is not finalized")

        first = state.get("first_ready_observation")
        stable = False
        if isinstance(first, dict) and first.get("semantic_sha256") == probe.semantic_sha256:
            try:
                first_time = _parse_utc(first.get("observed_at"), "first observation")
            except CompletionMonitorError as exc:
                return _set_review(
                    state,
                    state_path,
                    now=started,
                    scope="state",
                    error=exc,
                )
            stable = started >= first_time + STABILITY_INTERVAL
        if not stable:
            retained_first = (
                first
                if isinstance(first, dict)
                and first.get("semantic_sha256") == probe.semantic_sha256
                and first_time <= started
                else current_observation
            )
            state.update(
                status=WAITING,
                first_ready_observation=retained_first,
                review=None,
            )
            _write_mutable_json(state_path, state)
            return MonitorOutcome(WAITING, "first stable finalized observation recorded")

        state.update(
            status=REFRESHING,
            updated_at=_iso_utc(started),
            refresh_started_at=_iso_utc(started),
            refresh_snapshot_timestamp=None,
            review=None,
        )
        _write_mutable_json(state_path, state)
        try:
            result = refresh_runner(
                raw_data_root=raw_data_root,
                clean_data_root=clean_data_root,
                season=season,
                history_delay_seconds=history_delay_seconds,
                bootstrap_opener=opener,
                official_opener=opener,
                clock=clock,
                sleeper=sleeper,
            )
            state["refresh_snapshot_timestamp"] = result.snapshot_timestamp
            _write_mutable_json(state_path, state)
            realized = _validate_realized_candidate(
                raw_data_root=raw_data_root,
                clean_data_root=clean_data_root,
                season=season,
                target_gameweek=target_gameweek,
                snapshot_timestamp=result.snapshot_timestamp,
            )
            _publish_realized_receipt(
                realized_receipt_path,
                realized,
                season,
                target_gameweek,
                clock(),
            )
        except (RefreshError, EvaluationError, CompletionMonitorError, OSError) as exc:
            snapshot = (
                exc.snapshot_timestamp
                if isinstance(exc, RefreshIncompleteError)
                else state.get("refresh_snapshot_timestamp")
            )
            return _set_review(
                state,
                state_path,
                now=clock(),
                scope="realized",
                error=exc,
                snapshot_timestamp=snapshot,
            )
        completed = clock().astimezone(timezone.utc)
        state.update(
            status=REALIZED_COMPLETE,
            updated_at=_iso_utc(completed),
            review=None,
        )
        _write_mutable_json(state_path, state)
        if prediction_snapshot_timestamp is None:
            return MonitorOutcome(
                REALIZED_COMPLETE,
                "finalized realized snapshot was captured",
                realized.snapshot_timestamp,
            )
        return _ensure_evaluation(
            receipt_path=evaluation_receipt_path,
            state=state,
            state_path=state_path,
            realized=realized,
            prediction_snapshot=prediction_snapshot_timestamp,
            raw_data_root=raw_data_root,
            clean_data_root=clean_data_root,
            feature_data_root=feature_data_root,
            prediction_data_root=prediction_data_root,
            evaluation_data_root=evaluation_data_root,
            season=season,
            gameweek=target_gameweek,
            now=completed,
            runner=evaluation_runner,
        )
    finally:
        lock_path.unlink(missing_ok=True)


def reset_completion_monitor(
    *,
    control_data_root: Path = Path("data/operations/completion-monitor/fpl"),
    raw_data_root: Path = Path("data/raw/fpl"),
    clean_data_root: Path = Path("data/clean/fpl"),
    season: str,
    target_gameweek: int,
    reason: str,
    clock: Callable[[], datetime] = _utc_now,
) -> Path:
    """Archive a review state and explicitly permit the scoped next attempt."""
    if not reason.strip() or len(reason) > 500 or "\n" in reason or "\r" in reason:
        raise CompletionMonitorError("reset reason must be one non-empty line up to 500 characters")
    now = clock()
    if now.tzinfo is None:
        raise CompletionMonitorError("monitor clock must be timezone-aware")
    now = now.astimezone(timezone.utc)
    target = _target_directory(control_data_root, season, target_gameweek)
    lock_path = _acquire_lock(target, season, target_gameweek, now)
    state_path = target / "state.json"
    try:
        state = _load_state(state_path, season, target_gameweek, now)
        if state.get("status") != REVIEW_REQUIRED or not isinstance(
            state.get("review"), dict
        ):
            raise MonitorReviewError("monitor is not in a resettable review state")
        scope = state["review"].get("scope")
        realized_receipt = target / "realized-receipt.json"
        evaluation_receipt = target / "evaluation-receipt.json"
        if scope == "receipt" or evaluation_receipt.exists():
            raise MonitorReviewError("accepted or conflicting receipt requires manual repair review")
        if scope == "realized" and realized_receipt.exists():
            raise MonitorReviewError("realized review cannot reset an accepted receipt")
        if scope == "evaluation" and not realized_receipt.is_file():
            raise MonitorReviewError("evaluation reset requires an accepted realized receipt")
        if scope == "evaluation":
            try:
                _validate_realized_receipt(
                    realized_receipt,
                    raw_data_root=raw_data_root,
                    clean_data_root=clean_data_root,
                    season=season,
                    target_gameweek=target_gameweek,
                )
            except (CompletionMonitorError, RefreshError, EvaluationError, OSError) as exc:
                raise MonitorReviewError(
                    "evaluation reset requires a valid realized receipt"
                ) from exc
        archive_directory = target / "review-history"
        archive_path = archive_directory / f"{now.strftime('%Y%m%dT%H%M%S.%fZ')}.json"
        _write_exclusive_json(
            archive_path,
            {
                "schema_version": SCHEMA_VERSION,
                "season": season,
                "target_gameweek": target_gameweek,
                "archived_at": _iso_utc(now),
                "operator_reason": reason.strip(),
                "prior_state": state,
            },
        )
        state.update(
            status=REALIZED_COMPLETE if scope == "evaluation" else WAITING,
            updated_at=_iso_utc(now),
            first_ready_observation=None,
            last_observation=None,
            review=None,
        )
        _write_mutable_json(state_path, state)
        return archive_path
    finally:
        lock_path.unlink(missing_ok=True)
