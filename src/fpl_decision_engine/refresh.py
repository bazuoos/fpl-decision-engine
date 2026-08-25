"""Orchestrate one coherent, resumable official FPL data refresh."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import duckdb

from .gameweek_transform import (
    transform_fixtures_for_snapshot,
    transform_player_history_for_snapshot,
)
from .official_data import (
    DEFAULT_HISTORY_DELAY_SECONDS,
    DEFAULT_REQUEST_ATTEMPTS,
    DEFAULT_TIMEOUT_SECONDS,
    FPL_ELEMENT_SUMMARY_URL,
    FPL_FIXTURES_URL,
    Opener,
    Sleeper,
    _validate_fixture_payload,
    fetch_fixtures_for_snapshot,
    fetch_player_histories_for_snapshot,
)
from .pipeline import FPL_BOOTSTRAP_STATIC_URL, fetch_bootstrap_static
from .tls import verified_urlopen
from .transform import DataQualityError, transform_players_for_snapshot


logger = logging.getLogger(__name__)


class RefreshError(Exception):
    """Base exception for refresh orchestration failures."""


class RefreshIncompleteError(RefreshError):
    """Raised when an incomplete refresh can be resumed safely."""

    def __init__(self, snapshot_timestamp: str, stage: str, cause: BaseException):
        self.snapshot_timestamp = snapshot_timestamp
        self.stage = stage
        self.cause = cause
        super().__init__(
            f"refresh {snapshot_timestamp} is incomplete at stage {stage}: {cause}; "
            f"resume with: python -m fpl_decision_engine refresh --resume "
            f"{snapshot_timestamp}"
        )


class RefreshLockNotFoundError(RefreshError):
    """Raised when an explicitly requested manual unlock has no lock to remove."""


@dataclass(frozen=True)
class RefreshResult:
    snapshot_timestamp: str
    raw_directory: Path
    clean_directory: Path
    manifest_path: Path
    player_count: int
    fixture_count: int
    history_row_count: int


@dataclass(frozen=True)
class RefreshUnlockResult:
    snapshot_timestamp: str
    lock_path: Path
    lock_metadata: dict[str, Any] | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("refresh timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RefreshError(f"could not read {path}: {exc}") from exc


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


def _acquire_snapshot_lock(raw_directory: Path, acquired_at: datetime) -> Path:
    lock_path = raw_directory / ".refresh.lock"
    lock_body = json.dumps(
        {
            "pid": os.getpid(),
            "acquired_at": _iso_utc(acquired_at),
            "snapshot_directory": raw_directory.as_posix(),
        },
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    try:
        with lock_path.open("xb") as lock_file:
            lock_file.write(lock_body)
            lock_file.flush()
            os.fsync(lock_file.fileno())
    except FileExistsError as exc:
        raise RefreshError(
            f"refresh snapshot is locked by another process: {lock_path}; "
            "do not remove the lock until you have verified that process is no "
            "longer running"
        ) from exc
    return lock_path


def unlock_refresh_snapshot(
    *,
    raw_data_root: Path = Path("data/raw/fpl"),
    season: str = "2026-27",
    snapshot_timestamp: str,
) -> RefreshUnlockResult:
    """Explicitly remove only one operator-verified snapshot refresh lock."""
    if not re.fullmatch(r"\d{4}-\d{2}", season):
        raise RefreshError(f"invalid season directory name: {season}")
    if not re.fullmatch(r"\d{8}T\d{6}\.\d{6}Z", snapshot_timestamp):
        raise RefreshError(f"invalid snapshot timestamp: {snapshot_timestamp}")

    lock_path = raw_data_root / season / snapshot_timestamp / ".refresh.lock"
    if not lock_path.is_file():
        raise RefreshLockNotFoundError(
            f"refresh lock does not exist: {lock_path}"
        )

    logger.warning(
        "Manual refresh unlock requested for %s. Ensure no refresh process is "
        "currently running before removing this lock.",
        snapshot_timestamp,
    )
    lock_metadata: dict[str, Any] | None = None
    try:
        payload = json.loads(lock_path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Lock metadata is unavailable or malformed: %s", lock_path)
    else:
        if isinstance(payload, dict):
            lock_metadata = payload
            logger.warning("Lock metadata before removal: %s", lock_metadata)
        else:
            logger.warning("Lock metadata is not a JSON object: %s", lock_path)

    lock_path.unlink()
    logger.info("Removed refresh lock: %s", lock_path)
    return RefreshUnlockResult(
        snapshot_timestamp=snapshot_timestamp,
        lock_path=lock_path,
        lock_metadata=lock_metadata,
    )


def _project_metadata() -> dict[str, str | None]:
    try:
        project_version = version("fpl-decision-engine")
    except PackageNotFoundError:
        project_version = None
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    return {"project_version": project_version, "git_commit": commit or None}


def _bootstrap_players(snapshot_path: Path) -> tuple[dict[str, Any], list[int]]:
    bootstrap = _load_json(snapshot_path)
    if not isinstance(bootstrap, dict) or not isinstance(bootstrap.get("elements"), list):
        raise DataQualityError("bootstrap snapshot does not contain an elements list")
    if any(not isinstance(row, dict) for row in bootstrap["elements"]):
        raise DataQualityError("bootstrap elements must be JSON objects")
    player_ids = [row.get("id") for row in bootstrap["elements"]]
    if any(
        not isinstance(player_id, int) or isinstance(player_id, bool)
        for player_id in player_ids
    ):
        raise DataQualityError("bootstrap player IDs must be non-null integers")
    if len(player_ids) != len(set(player_ids)):
        raise DataQualityError("bootstrap player IDs must be unique")
    return bootstrap, player_ids


def _validate_or_fetch_fixtures(
    *,
    raw_data_root: Path,
    season: str,
    snapshot_timestamp: str,
    opener: Opener,
    timeout: float,
    attempts: int,
    clock: Any,
    sleeper: Sleeper,
) -> Path:
    snapshot_dir = raw_data_root / season / snapshot_timestamp
    snapshot_path = snapshot_dir / "bootstrap-static.json"
    fixture_path = snapshot_dir / "fixtures.json"
    manifest_path = snapshot_dir / "fixtures.manifest.json"
    if not fixture_path.exists() and not manifest_path.exists():
        return fetch_fixtures_for_snapshot(
            raw_data_root=raw_data_root,
            season=season,
            snapshot_timestamp=snapshot_timestamp,
            timeout=timeout,
            attempts=attempts,
            opener=opener,
            clock=clock,
            sleeper=sleeper,
        )
    if not fixture_path.is_file() or not manifest_path.is_file():
        raise RefreshError("partial fixture raw output cannot be reused safely")
    fixtures = _validate_fixture_payload(_load_json(fixture_path), FPL_FIXTURES_URL)
    manifest = _load_json(manifest_path)
    if (
        not isinstance(fixtures, list)
        or not isinstance(manifest, dict)
        or manifest.get("status") != "complete"
        or manifest.get("snapshot_timestamp") != snapshot_timestamp
        or manifest.get("bootstrap_sha256") != _sha256(snapshot_path)
        or manifest.get("response_sha256") != _sha256(fixture_path)
        or manifest.get("record_count") != len(fixtures)
    ):
        raise RefreshError("existing fixture response/manifest failed validation")
    return fixture_path


def _clean_output_details(path: Path) -> dict[str, Any]:
    connection = duckdb.connect(":memory:")
    try:
        row_count = connection.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(path)]
        ).fetchone()[0]
    finally:
        connection.close()
    return {"path": path.as_posix(), "sha256": _sha256(path), "row_count": row_count}


def _validate_snapshot_coherence(
    *,
    raw_directory: Path,
    clean_directory: Path,
    snapshot_timestamp: str,
    expected_player_ids: list[int],
) -> tuple[int, int, int]:
    bootstrap_path = raw_directory / "bootstrap-static.json"
    fixture_path = raw_directory / "fixtures.json"
    history_manifest_path = raw_directory / "player_history/manifest.json"
    players_path = clean_directory / "players.parquet"
    fixtures_path = clean_directory / "fixtures.parquet"
    history_path = clean_directory / "player_gameweek_history.parquet"
    required = [
        bootstrap_path,
        fixture_path,
        history_manifest_path,
        players_path,
        fixtures_path,
        history_path,
    ]
    missing = [path for path in required if not path.is_file()]
    if missing:
        raise DataQualityError(
            "refresh snapshot is missing required outputs: "
            + ", ".join(path.as_posix() for path in missing)
        )
    history_manifest = _load_json(history_manifest_path)
    if (
        not isinstance(history_manifest, dict)
        or history_manifest.get("status") != "complete"
        or history_manifest.get("expected_player_ids") != expected_player_ids
        or history_manifest.get("success_count") != len(expected_player_ids)
        or history_manifest.get("failure_count") != 0
    ):
        raise DataQualityError("player-history collection is not complete")
    response_ids = {
        row.get("fpl_player_id") for row in history_manifest.get("responses", [])
    }
    if (
        response_ids != set(expected_player_ids)
        or len(history_manifest.get("responses", [])) != len(expected_player_ids)
    ):
        raise DataQualityError("history response IDs do not match bootstrap players")
    expected_id_set = set(expected_player_ids)
    numeric_history_ids = set()
    response_metadata = {
        row["fpl_player_id"]: row for row in history_manifest["responses"]
    }
    for response_path in (raw_directory / "player_history").glob("*.json"):
        if response_path.name == "manifest.json":
            continue
        try:
            numeric_history_ids.add(int(response_path.stem))
        except ValueError:
            continue
    if numeric_history_ids != expected_id_set:
        raise DataQualityError(
            "history files contain missing or unexpected player IDs"
        )
    for player_id in expected_player_ids:
        response_path = raw_directory / "player_history" / f"{player_id}.json"
        response_body = response_path.read_bytes()
        response = _load_json(response_path)
        if (
            not isinstance(response, dict)
            or not isinstance(response.get("history"), list)
            or response_metadata[player_id].get("response_sha256")
            != hashlib.sha256(response_body).hexdigest()
            or any(
                not isinstance(row, dict) or row.get("element") != player_id
                for row in response["history"]
            )
        ):
            raise DataQualityError(
                f"player {player_id} history response failed final validation"
            )

    connection = duckdb.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE clean_players AS SELECT * FROM read_parquet(?)",
            [str(players_path)],
        )
        connection.execute(
            "CREATE TABLE clean_fixtures AS SELECT * FROM read_parquet(?)",
            [str(fixtures_path)],
        )
        connection.execute(
            "CREATE TABLE clean_history AS SELECT * FROM read_parquet(?)",
            [str(history_path)],
        )
        bootstrap_hash = _sha256(bootstrap_path)
        fixture_hash = _sha256(fixture_path)
        player_count, distinct_players, snapshot_mismatches, source_mismatches = (
            connection.execute(
            """SELECT count(*), count(DISTINCT fpl_player_id),
                      count(*) FILTER (WHERE snapshot_timestamp <> ?),
                      count(*) FILTER (WHERE source_sha256 <> ?)
               FROM clean_players""",
            [snapshot_timestamp, bootstrap_hash],
            ).fetchone()
        )
        clean_player_ids = {
            row[0] for row in connection.execute(
                "SELECT fpl_player_id FROM clean_players"
            ).fetchall()
        }
        if (
            player_count != len(expected_player_ids)
            or distinct_players != player_count
            or clean_player_ids != set(expected_player_ids)
            or snapshot_mismatches != 0
            or source_mismatches != 0
        ):
            raise DataQualityError("clean players do not match bootstrap players")
        fixture_count, fixture_snapshot_mismatches, fixture_source_mismatches = connection.execute(
            """SELECT count(*), count(*) FILTER (WHERE snapshot_timestamp <> ?),
                      count(*) FILTER (
                          WHERE source_sha256 <> ? OR bootstrap_sha256 <> ?)
               FROM clean_fixtures""",
            [snapshot_timestamp, fixture_hash, bootstrap_hash],
        ).fetchone()
        (
            history_count,
            history_snapshot_mismatches,
            unresolved_history_ids,
            history_source_mismatches,
        ) = (
            connection.execute(
                """SELECT count(*),
                          count(*) FILTER (WHERE snapshot_timestamp <> ?),
                          count(*) FILTER (
                              WHERE fpl_player_id NOT IN (
                                  SELECT fpl_player_id FROM clean_players)),
                          count(*) FILTER (
                              WHERE bootstrap_sha256 <> ?
                                 OR fixture_source_sha256 <> ?)
                   FROM clean_history""",
                [snapshot_timestamp, bootstrap_hash, fixture_hash],
            ).fetchone()
        )
        if (
            fixture_snapshot_mismatches
            or fixture_source_mismatches
            or history_snapshot_mismatches
            or history_source_mismatches
        ):
            raise DataQualityError("clean outputs use inconsistent snapshot timestamps")
        if unresolved_history_ids:
            raise DataQualityError("clean history contains unknown player IDs")
    finally:
        connection.close()
    return player_count, fixture_count, history_count


def refresh_fpl_data(
    *,
    raw_data_root: Path = Path("data/raw/fpl"),
    clean_data_root: Path = Path("data/clean/fpl"),
    season: str = "2026-27",
    resume_snapshot_timestamp: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    attempts: int = DEFAULT_REQUEST_ATTEMPTS,
    history_delay_seconds: float = DEFAULT_HISTORY_DELAY_SECONDS,
    bootstrap_opener: Opener = verified_urlopen,
    official_opener: Opener = verified_urlopen,
    clock: Any = _utc_now,
    sleeper: Sleeper = time.sleep,
) -> RefreshResult:
    """Create or explicitly resume one coherent official-data snapshot."""
    stage = "bootstrap"
    manifest: dict[str, Any] | None = None
    manifest_path: Path | None = None
    manifest_mutable = False
    lock_path: Path | None = None
    snapshot_timestamp = resume_snapshot_timestamp
    try:
        if resume_snapshot_timestamp is None:
            started = clock()
            logger.info("[1/7] Fetching bootstrap-static")
            snapshot_path = fetch_bootstrap_static(
                data_root=raw_data_root,
                season=season,
                timeout=timeout,
                opener=bootstrap_opener,
                now=started,
            )
            snapshot_timestamp = snapshot_path.parent.name
            raw_directory = snapshot_path.parent
            bootstrap, player_ids = _bootstrap_players(snapshot_path)
            lock_path = _acquire_snapshot_lock(raw_directory, clock())
            manifest_path = raw_directory / "refresh.manifest.json"
            manifest = {
                "season": season,
                "snapshot_timestamp": snapshot_timestamp,
                "refresh_started_at": _iso_utc(started),
                "refresh_completed_at": None,
                "status": "in_progress",
                "stage": "bootstrap_complete",
                "resume_count": 0,
                "endpoints": {
                    "bootstrap": FPL_BOOTSTRAP_STATIC_URL,
                    "fixtures": FPL_FIXTURES_URL,
                    "player_history": FPL_ELEMENT_SUMMARY_URL,
                },
                "software": _project_metadata(),
                "bootstrap": {
                    "path": snapshot_path.as_posix(),
                    "sha256": _sha256(snapshot_path),
                    "player_count": len(player_ids),
                },
                "expected_player_ids": player_ids,
                "failures": [],
            }
            _update_manifest(manifest_path, manifest)
            manifest_mutable = True
        else:
            raw_directory = raw_data_root / season / resume_snapshot_timestamp
            snapshot_path = raw_directory / "bootstrap-static.json"
            manifest_path = raw_directory / "refresh.manifest.json"
            if not snapshot_path.is_file() or not manifest_path.is_file():
                raise RefreshError(
                    f"refresh snapshot cannot be resumed: {resume_snapshot_timestamp}"
                )
            lock_path = _acquire_snapshot_lock(raw_directory, clock())
            manifest = _load_json(manifest_path)
            if not isinstance(manifest, dict):
                raise RefreshError("refresh manifest is not a JSON object")
            if manifest.get("status") == "complete":
                raise RefreshError(
                    f"completed refresh is immutable and cannot be resumed: "
                    f"{resume_snapshot_timestamp}"
                )
            if (
                manifest.get("season") != season
                or manifest.get("snapshot_timestamp") != resume_snapshot_timestamp
            ):
                raise RefreshError("refresh manifest identity does not match resume request")
            bootstrap, player_ids = _bootstrap_players(snapshot_path)
            if (
                manifest.get("bootstrap", {}).get("sha256") != _sha256(snapshot_path)
                or manifest.get("expected_player_ids") != player_ids
            ):
                raise RefreshError("bootstrap changed since the refresh began")
            manifest["status"] = "in_progress"
            manifest["stage"] = "resuming"
            manifest["resume_count"] = manifest.get("resume_count", 0) + 1
            manifest["last_resumed_at"] = _iso_utc(clock())
            manifest["failures"] = []
            _update_manifest(manifest_path, manifest)
            manifest_mutable = True

        assert snapshot_timestamp is not None and manifest is not None
        clean_directory = clean_data_root / season / snapshot_timestamp

        stage = "fixtures"
        logger.info("[2/7] Fetching or validating fixtures")
        fixture_path = _validate_or_fetch_fixtures(
            raw_data_root=raw_data_root,
            season=season,
            snapshot_timestamp=snapshot_timestamp,
            opener=official_opener,
            timeout=timeout,
            attempts=attempts,
            clock=clock,
            sleeper=sleeper,
        )
        manifest["fixtures"] = {
            "path": fixture_path.as_posix(),
            "sha256": _sha256(fixture_path),
            "manifest_path": (raw_directory / "fixtures.manifest.json").as_posix(),
            "manifest_sha256": _sha256(
                raw_directory / "fixtures.manifest.json"
            ),
        }
        manifest["stage"] = "fixtures_complete"
        _update_manifest(manifest_path, manifest)

        stage = "player_history"
        logger.info("[3/7] Fetching or resuming player histories")
        history_dir = fetch_player_histories_for_snapshot(
            raw_data_root=raw_data_root,
            season=season,
            snapshot_timestamp=snapshot_timestamp,
            timeout=timeout,
            attempts=attempts,
            delay_seconds=history_delay_seconds,
            opener=official_opener,
            clock=clock,
            sleeper=sleeper,
            resume=(raw_directory / "player_history").exists(),
        )
        history_manifest_path = history_dir / "manifest.json"
        history_manifest = _load_json(history_manifest_path)
        manifest["player_history"] = {
            "directory": history_dir.as_posix(),
            "manifest_path": history_manifest_path.as_posix(),
            "manifest_sha256": _sha256(history_manifest_path),
            "expected_count": history_manifest.get("expected_count"),
            "success_count": history_manifest.get("success_count"),
            "failure_count": history_manifest.get("failure_count"),
            "failures": history_manifest.get("failures", []),
            "retry_summary": {
                "maximum_request_attempts": history_manifest.get(
                    "maximum_request_attempts"
                ),
                "request_count": history_manifest.get("request_count"),
                "request_attempt_count": history_manifest.get(
                    "request_attempt_count"
                ),
                "resume_count": history_manifest.get("resume_count"),
                "reused_response_count": history_manifest.get(
                    "reused_response_count"
                ),
            },
        }
        manifest["stage"] = "raw_complete"
        _update_manifest(manifest_path, manifest)

        stage = "transform_players"
        logger.info("[4/7] Transforming players")
        players_path = clean_directory / "players.parquet"
        if not players_path.exists():
            players_path = transform_players_for_snapshot(
                raw_data_root=raw_data_root,
                clean_data_root=clean_data_root,
                season=season,
                snapshot_timestamp=snapshot_timestamp,
            )

        stage = "transform_fixtures"
        logger.info("[5/7] Transforming fixtures")
        clean_fixtures_path = clean_directory / "fixtures.parquet"
        if not clean_fixtures_path.exists():
            clean_fixtures_path = transform_fixtures_for_snapshot(
                raw_data_root=raw_data_root,
                clean_data_root=clean_data_root,
                season=season,
                snapshot_timestamp=snapshot_timestamp,
            )

        stage = "transform_player_history"
        logger.info("[6/7] Transforming player history")
        clean_history_path = clean_directory / "player_gameweek_history.parquet"
        if not clean_history_path.exists():
            clean_history_path = transform_player_history_for_snapshot(
                raw_data_root=raw_data_root,
                clean_data_root=clean_data_root,
                season=season,
                snapshot_timestamp=snapshot_timestamp,
            )

        stage = "validate_snapshot"
        logger.info("[7/7] Validating coherent snapshot")
        player_count, fixture_count, history_count = _validate_snapshot_coherence(
            raw_directory=raw_directory,
            clean_directory=clean_directory,
            snapshot_timestamp=snapshot_timestamp,
            expected_player_ids=player_ids,
        )
        manifest["clean_outputs"] = {
            "players": _clean_output_details(players_path),
            "fixtures": _clean_output_details(clean_fixtures_path),
            "player_gameweek_history": _clean_output_details(clean_history_path),
        }
        manifest["row_counts"] = {
            "players": player_count,
            "fixtures": fixture_count,
            "player_gameweek_history": history_count,
        }
        manifest["stage"] = "complete"
        manifest["status"] = "complete"
        manifest["refresh_completed_at"] = _iso_utc(clock())
        _update_manifest(manifest_path, manifest)
        logger.info(
            "Refresh complete: %s (%d players, %d fixtures, %d history rows)",
            snapshot_timestamp,
            player_count,
            fixture_count,
            history_count,
        )
        return RefreshResult(
            snapshot_timestamp=snapshot_timestamp,
            raw_directory=raw_directory,
            clean_directory=clean_directory,
            manifest_path=manifest_path,
            player_count=player_count,
            fixture_count=fixture_count,
            history_row_count=history_count,
        )
    except BaseException as exc:
        if manifest_mutable and manifest is not None and manifest_path is not None:
            if stage == "player_history" and snapshot_timestamp is not None:
                partial_history_manifest_path = (
                    raw_data_root
                    / season
                    / snapshot_timestamp
                    / "player_history/manifest.json"
                )
                if partial_history_manifest_path.is_file():
                    partial_history_manifest = _load_json(
                        partial_history_manifest_path
                    )
                    if isinstance(partial_history_manifest, dict):
                        manifest["player_history"] = {
                            "manifest_path": partial_history_manifest_path.as_posix(),
                            "manifest_sha256": _sha256(
                                partial_history_manifest_path
                            ),
                            "status": partial_history_manifest.get("status"),
                            "expected_count": partial_history_manifest.get(
                                "expected_count"
                            ),
                            "success_count": partial_history_manifest.get(
                                "success_count"
                            ),
                            "failure_count": partial_history_manifest.get(
                                "failure_count"
                            ),
                            "failed_or_remaining_player_ids": (
                                partial_history_manifest.get(
                                    "remaining_player_ids", []
                                )
                            ),
                        }
            manifest["status"] = "incomplete"
            manifest["stage"] = stage
            manifest["refresh_completed_at"] = None
            manifest["last_failure_at"] = _iso_utc(clock())
            manifest["failures"] = manifest.get("failures", []) + [
                {"stage": stage, "error": str(exc), "type": type(exc).__name__}
            ]
            _update_manifest(manifest_path, manifest)
        if isinstance(exc, KeyboardInterrupt):
            raise
        if isinstance(exc, RefreshError) and not manifest_mutable:
            raise
        if snapshot_timestamp is None:
            raise RefreshError(f"refresh failed before snapshot creation: {exc}") from exc
        raise RefreshIncompleteError(snapshot_timestamp, stage, exc) from exc
    finally:
        if lock_path is not None:
            lock_path.unlink(missing_ok=True)
