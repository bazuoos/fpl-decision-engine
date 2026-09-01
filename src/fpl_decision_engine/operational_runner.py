"""Two-phase, fail-closed operational orchestration for Trustworthy Engine v1."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

import duckdb

from .decision import APPEARANCE_ONLY_ALLOWED_POLICY
from .decision_reliability import (
    DecisionReliabilityError,
    load_reliability_context,
    write_decision_reliability,
)
from .editable_manager import (
    EditableManagerError,
    ManualEditablePick,
    ManualEditableState,
    create_manual_editable_state,
    load_manual_editable_state,
    price_m_to_units,
)
from .features import FeatureBuildError, build_player_gameweek_features, find_feature_snapshot
from .operational_manifest import (
    ChipState,
    EvidenceObservation,
    FinalOperationalManifest,
    OperationalContractError,
    PreparationManifest,
    build_final_operational_manifest,
    build_decision_id,
    build_preparation_manifest,
    canonical_json_bytes,
    validate_final_operational_manifest,
    validate_preparation_manifest,
)
from .predictions import MODEL_VERSION, PredictionError, predict_xfp_v01_from_feature
from .presentation.gameweek_decision import (
    GameweekDecisionError,
    build_gameweek_decision,
    serialize_gameweek_decision,
)
from .projection_provider import XfpV01ParquetProvider, sha256_file
from .refresh import RefreshError, RefreshResult, refresh_fpl_data
from .transfer_decision import (
    SELLING_PRICE_SOURCE,
    TransferDecisionError,
    evaluate_one_transfer,
    write_one_transfer_decision,
)


MANAGER_EVIDENCE_VERSION = "verified-manager-evidence-v1"
MANAGER_SUBMISSION_VERSION = "operational-manager-submission-v1"
PREPARATION_STATUS = "BLOCKED: VERIFIED_MANAGER_STATE_REQUIRED"
COMPLETED_STATUS = "COMPLETED"

_PREPARATION_FILES = {
    "bootstrap": "bootstrap-static.json",
    "refresh_manifest": "refresh.manifest.json",
    "fixtures": "fixtures.json",
    "fixtures_manifest": "fixtures.manifest.json",
    "history_manifest": "player_history.manifest.json",
    "players": "players.parquet",
    "features": "player_gameweek_features.parquet",
    "fixture_predictions": "xfp_v01_fixtures.parquet",
    "gameweek_predictions": "xfp_v01_gameweek.parquet",
}


class OperationalErrorCode(str, Enum):
    TARGET_NOT_OFFICIAL_NEXT = "TARGET_NOT_OFFICIAL_NEXT"
    OFFICIAL_NEXT_INVALID = "OFFICIAL_NEXT_INVALID"
    DEADLINE_ALREADY_PASSED = "DEADLINE_ALREADY_PASSED"
    MANAGER_VERIFICATION_AT_OR_AFTER_DEADLINE = (
        "MANAGER_VERIFICATION_AT_OR_AFTER_DEADLINE"
    )
    DEADLINE_PASSED_DURING_FINALIZATION = "DEADLINE_PASSED_DURING_FINALIZATION"
    UNSUPPORTED_CHIP_WILDCARD = "UNSUPPORTED_CHIP_WILDCARD"
    UNSUPPORTED_CHIP_FREE_HIT = "UNSUPPORTED_CHIP_FREE_HIT"
    UNSUPPORTED_CHIP_BENCH_BOOST = "UNSUPPORTED_CHIP_BENCH_BOOST"
    UNSUPPORTED_CHIP_TRIPLE_CAPTAIN = "UNSUPPORTED_CHIP_TRIPLE_CAPTAIN"
    INVALID_MANAGER_EVIDENCE = "INVALID_MANAGER_EVIDENCE"
    PINNED_ARTIFACT_MISSING = "PINNED_ARTIFACT_MISSING"
    PINNED_ARTIFACT_HASH_MISMATCH = "PINNED_ARTIFACT_HASH_MISMATCH"
    CONFLICTING_IMMUTABLE_OUTPUT = "CONFLICTING_IMMUTABLE_OUTPUT"
    INVALID_PREPARATION_MANIFEST = "INVALID_PREPARATION_MANIFEST"
    INVALID_COMPLETED_FINAL_MANIFEST = "INVALID_COMPLETED_FINAL_MANIFEST"
    TRUSTED_STAGE_FAILED = "TRUSTED_STAGE_FAILED"


class OperationalRunnerError(Exception):
    """Structured, machine-testable operational failure."""

    def __init__(self, code: OperationalErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class VerifiedManagerPlayer:
    element_id: int
    display_name: str
    position: str
    selling_price_m: str | int | float


@dataclass(frozen=True)
class VerifiedManagerEvidence:
    version: str
    entry_id: int
    season: str
    target_gameweek: int
    bank_m: str | int | float
    free_transfers: int
    chip_state: ChipState
    evidence_source: str
    evidence_source_sha256: str | None
    current_selection_verified: bool
    players: tuple[VerifiedManagerPlayer, ...]
    input_path: Path
    input_sha256: str


@dataclass(frozen=True)
class PreparationRunResult:
    status: str
    preparation_id: str
    preparation_manifest_path: Path
    preparation_manifest_sha256: str
    reused: bool


@dataclass(frozen=True)
class CompletedRunResult:
    status: str
    preparation_id: str
    decision_id: str
    gameweek_decision_path: Path
    gameweek_decision_sha256: str
    final_manifest_path: Path
    final_manifest_sha256: str
    reused: bool


@dataclass(frozen=True)
class OperationalStages:
    refresh: Callable[..., RefreshResult] = refresh_fpl_data
    build_features: Callable[..., Path] = build_player_gameweek_features
    predict: Callable[..., Any] = predict_xfp_v01_from_feature
    create_manager_state: Callable[..., ManualEditableState] = create_manual_editable_state
    evaluate_transfer: Callable[..., Any] = evaluate_one_transfer
    write_transfer: Callable[..., Any] = write_one_transfer_decision
    load_reliability: Callable[..., Any] = load_reliability_context
    write_reliability: Callable[..., Any] = write_decision_reliability
    build_gameweek_decision: Callable[..., dict[str, Any]] = build_gameweek_decision
    serialize_gameweek_decision: Callable[..., bytes] = serialize_gameweek_decision


def _system_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime, label: str) -> tuple[datetime, str]:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED,
            f"{label} must be an explicit UTC datetime",
        )
    canonical = value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return value, canonical


def _parse_utc(value: Any, label: str) -> tuple[datetime, str]:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED,
            f"{label} must be an explicit UTC timestamp ending in Z",
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED,
            f"{label} is invalid",
        ) from exc
    return _utc(parsed, label)


def _before_deadline(
    now: datetime,
    deadline: datetime,
    code: OperationalErrorCode,
    label: str,
) -> str:
    current, canonical = _utc(now, label)
    if current >= deadline:
        raise OperationalRunnerError(code, f"{label} must be strictly before deadline")
    return canonical


def _positive_gameweek(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 38:
        raise OperationalRunnerError(
            OperationalErrorCode.TARGET_NOT_OFFICIAL_NEXT,
            "target gameweek must be an explicit integer between 1 and 38",
        )
    return value


def _read_json(path: Path, code: OperationalErrorCode, label: str) -> Any:
    if not path.is_file():
        raise OperationalRunnerError(code, f"{label} is missing: {path}")
    try:
        return json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OperationalRunnerError(code, f"{label} is invalid: {exc}") from exc


def _file_hash(path: Path, expected: str | None = None) -> str:
    if not path.is_file():
        raise OperationalRunnerError(
            OperationalErrorCode.PINNED_ARTIFACT_MISSING,
            f"pinned artifact is missing: {path}",
        )
    observed = sha256_file(path)
    if expected is not None and observed != expected:
        raise OperationalRunnerError(
            OperationalErrorCode.PINNED_ARTIFACT_HASH_MISMATCH,
            f"pinned artifact hash mismatch for {path}: expected {expected}, observed {observed}",
        )
    return observed


def _write_atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.is_file() and path.read_bytes() == body:
            return
        raise OperationalRunnerError(
            OperationalErrorCode.CONFLICTING_IMMUTABLE_OUTPUT,
            f"immutable output conflicts with existing bytes: {path}",
        )
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if not path.is_file() or path.read_bytes() != body:
                raise OperationalRunnerError(
                    OperationalErrorCode.CONFLICTING_IMMUTABLE_OUTPUT,
                    f"immutable output was concurrently published with different bytes: {path}",
                )
    finally:
        temporary.unlink(missing_ok=True)


def _copy_fsync(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise OperationalRunnerError(
            OperationalErrorCode.PINNED_ARTIFACT_MISSING,
            f"source artifact is missing: {source}",
        )
    with source.open("rb") as input_file, destination.open("xb") as output_file:
        shutil.copyfileobj(input_file, output_file)
        output_file.flush()
        os.fsync(output_file.fileno())


def _official_event(bootstrap_path: Path, target_gameweek: int) -> tuple[datetime, str]:
    payload = _read_json(
        bootstrap_path, OperationalErrorCode.TRUSTED_STAGE_FAILED, "bootstrap snapshot"
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise OperationalRunnerError(
            OperationalErrorCode.OFFICIAL_NEXT_INVALID,
            "bootstrap snapshot has no events array",
        )
    if not all(isinstance(event, dict) for event in payload["events"]):
        raise OperationalRunnerError(
            OperationalErrorCode.OFFICIAL_NEXT_INVALID,
            "bootstrap events must all be objects",
        )
    next_events = [event for event in payload["events"] if event.get("is_next") is True]
    if len(next_events) != 1:
        raise OperationalRunnerError(
            OperationalErrorCode.OFFICIAL_NEXT_INVALID,
            f"official bootstrap must contain exactly one is_next event; found {len(next_events)}",
        )
    event = next_events[0]
    if event.get("id") != target_gameweek:
        raise OperationalRunnerError(
            OperationalErrorCode.TARGET_NOT_OFFICIAL_NEXT,
            f"requested GW{target_gameweek} is not official is_next GW{event.get('id')}",
        )
    return _parse_utc(event.get("deadline_time"), "official deadline")


def _snapshot_observed_at(snapshot_timestamp: str) -> str:
    try:
        parsed = datetime.strptime(snapshot_timestamp, "%Y%m%dT%H%M%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED,
            f"refresh snapshot timestamp is invalid: {snapshot_timestamp}",
        ) from exc
    return _utc(parsed, "snapshot observation timestamp")[1]


def _validate_refresh(refresh: RefreshResult, season: str) -> dict[str, Path]:
    raw = refresh.raw_directory
    clean = refresh.clean_directory
    paths = {
        "bootstrap": raw / "bootstrap-static.json",
        "refresh_manifest": refresh.manifest_path,
        "fixtures": raw / "fixtures.json",
        "fixtures_manifest": raw / "fixtures.manifest.json",
        "history_manifest": raw / "player_history" / "manifest.json",
        "players": clean / "players.parquet",
    }
    for path in paths.values():
        _file_hash(path)
    manifest = _read_json(
        paths["refresh_manifest"],
        OperationalErrorCode.TRUSTED_STAGE_FAILED,
        "refresh manifest",
    )
    if not isinstance(manifest, dict) or manifest.get("status") != "complete":
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED,
            "refresh manifest is not complete",
        )
    if (
        manifest.get("season") != season
        or manifest.get("snapshot_timestamp") != refresh.snapshot_timestamp
        or manifest.get("bootstrap", {}).get("sha256") != _file_hash(paths["bootstrap"])
        or manifest.get("fixtures", {}).get("sha256") != _file_hash(paths["fixtures"])
        or manifest.get("fixtures", {}).get("manifest_sha256")
        != _file_hash(paths["fixtures_manifest"])
        or manifest.get("player_history", {}).get("manifest_sha256")
        != _file_hash(paths["history_manifest"])
        or manifest.get("clean_outputs", {}).get("players", {}).get("sha256")
        != _file_hash(paths["players"])
    ):
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED,
            "refresh manifest identity/provenance does not reconcile",
        )
    fixture_manifest = _read_json(
        paths["fixtures_manifest"],
        OperationalErrorCode.TRUSTED_STAGE_FAILED,
        "fixture manifest",
    )
    history_manifest = _read_json(
        paths["history_manifest"],
        OperationalErrorCode.TRUSTED_STAGE_FAILED,
        "history manifest",
    )
    if not isinstance(fixture_manifest, dict) or fixture_manifest.get("status") != "complete":
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED, "fixture manifest is not complete"
        )
    if not isinstance(history_manifest, dict) or history_manifest.get("status") != "complete":
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED, "history manifest is not complete"
        )
    try:
        fixture_rows = json.loads(paths["fixtures"].read_bytes())
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED,
            f"fixture response is invalid: {exc}",
        ) from exc
    if (
        not isinstance(fixture_rows, list)
        or fixture_manifest.get("season") != season
        or fixture_manifest.get("snapshot_timestamp") != refresh.snapshot_timestamp
        or fixture_manifest.get("bootstrap_sha256") != _file_hash(paths["bootstrap"])
        or fixture_manifest.get("response_sha256") != _file_hash(paths["fixtures"])
        or fixture_manifest.get("record_count") != len(fixture_rows)
        or history_manifest.get("season") != season
        or history_manifest.get("snapshot_timestamp") != refresh.snapshot_timestamp
        or history_manifest.get("bootstrap_sha256") != _file_hash(paths["bootstrap"])
        or history_manifest.get("expected_count")
        != history_manifest.get("success_count")
        or history_manifest.get("failure_count") != 0
    ):
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED,
            "accepted official refresh evidence does not reconcile",
        )
    return paths


def _validate_feature(path: Path, season: str, snapshot: str, gameweek: int) -> None:
    _file_hash(path)
    connection = duckdb.connect(":memory:")
    try:
        row = connection.execute(
            """SELECT count(*), count(DISTINCT season), min(season),
                      count(DISTINCT snapshot_timestamp), min(snapshot_timestamp),
                      count(DISTINCT target_gameweek), min(target_gameweek)
                 FROM read_parquet(?)""",
            [str(path)],
        ).fetchone()
    except duckdb.Error as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.PINNED_ARTIFACT_HASH_MISMATCH,
            f"feature artifact cannot be validated: {exc}",
        ) from exc
    finally:
        connection.close()
    if not row or row[0] == 0 or row[1:] != (1, season, 1, snapshot, 1, gameweek):
        raise OperationalRunnerError(
            OperationalErrorCode.PINNED_ARTIFACT_HASH_MISMATCH,
            "feature identity does not match preparation",
        )


def _validate_predictions(
    fixture_path: Path,
    gameweek_path: Path,
    feature_path: Path,
    players_path: Path,
    season: str,
    snapshot: str,
    gameweek: int,
) -> None:
    expected_feature = _file_hash(feature_path)
    for path in (fixture_path, gameweek_path):
        _file_hash(path)
        connection = duckdb.connect(":memory:")
        try:
            row = connection.execute(
                """SELECT count(*), count(DISTINCT season), min(season),
                          count(DISTINCT snapshot_timestamp), min(snapshot_timestamp),
                          count(DISTINCT target_gameweek), min(target_gameweek),
                          count(DISTINCT feature_input_sha256), min(feature_input_sha256)
                     FROM read_parquet(?)""",
                [str(path)],
            ).fetchone()
        except duckdb.Error as exc:
            raise OperationalRunnerError(
                OperationalErrorCode.PINNED_ARTIFACT_HASH_MISMATCH,
                f"prediction artifact cannot be validated: {exc}",
            ) from exc
        finally:
            connection.close()
        if (
            not row
            or row[0] == 0
            or row[1:7] != (1, season, 1, snapshot, 1, gameweek)
            or row[7:] != (1, expected_feature)
        ):
            raise OperationalRunnerError(
                OperationalErrorCode.PINNED_ARTIFACT_HASH_MISMATCH,
                f"prediction provenance does not match preparation: {path}",
            )
    try:
        XfpV01ParquetProvider(
            projection_artifact=gameweek_path, players_artifact=players_path
        ).load(season=season, target_gameweek=gameweek)
    except Exception as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.PINNED_ARTIFACT_HASH_MISMATCH,
            f"projection provider rejected pinned artifacts: {exc}",
        ) from exc


def _stage_feature_and_predictions(
    *,
    refresh: RefreshResult,
    paths: Mapping[str, Path],
    season: str,
    target_gameweek: int,
    raw_data_root: Path,
    clean_data_root: Path,
    feature_data_root: Path,
    prediction_data_root: Path,
    stages: OperationalStages,
) -> tuple[Path, Path, Path]:
    try:
        selected, _, _ = find_feature_snapshot(
            raw_data_root=raw_data_root,
            clean_data_root=clean_data_root,
            season=season,
            target_gameweek=target_gameweek,
            snapshot_timestamp=refresh.snapshot_timestamp,
        )
    except FeatureBuildError as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED, str(exc)
        ) from exc
    if selected != refresh.snapshot_timestamp:
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED,
            "feature stage switched away from the frozen refresh",
        )
    feature_path = (
        feature_data_root
        / season
        / selected
        / f"gameweek={target_gameweek}"
        / "player_gameweek_features.parquet"
    )
    try:
        if not feature_path.is_file():
            feature_path = stages.build_features(
                target_gameweek=target_gameweek,
                raw_data_root=raw_data_root,
                clean_data_root=clean_data_root,
                feature_data_root=feature_data_root,
                season=season,
                snapshot_timestamp=selected,
            )
        _validate_feature(feature_path, season, selected, target_gameweek)
        prediction_directory = (
            prediction_data_root / season / selected / f"gameweek={target_gameweek}"
        )
        fixture_path = prediction_directory / "xfp_v01_fixtures.parquet"
        gameweek_path = prediction_directory / "xfp_v01_gameweek.parquet"
        existing = (fixture_path.is_file(), gameweek_path.is_file())
        if existing == (False, False):
            outputs = stages.predict(
                feature_path=feature_path,
                prediction_data_root=prediction_data_root,
                season=season,
                snapshot_timestamp=selected,
                target_gameweek=target_gameweek,
            )
            fixture_path, gameweek_path = outputs.fixture_path, outputs.gameweek_path
        elif existing != (True, True):
            raise OperationalRunnerError(
                OperationalErrorCode.PINNED_ARTIFACT_MISSING,
                "prediction stage is partially published",
            )
        _validate_predictions(
            fixture_path,
            gameweek_path,
            feature_path,
            paths["players"],
            season,
            selected,
            target_gameweek,
        )
    except OperationalRunnerError:
        raise
    except (FeatureBuildError, PredictionError, OSError) as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED,
            f"trusted feature/prediction stage failed: {exc}",
        ) from exc
    return feature_path, fixture_path, gameweek_path


def _preparation_directory(
    root: Path, season: str, gameweek: int, preparation_id: str
) -> Path:
    return root / season / f"gameweek={gameweek}" / preparation_id


def _artifact_paths(preparation_directory: Path) -> dict[str, Path]:
    root = preparation_directory / "artifacts"
    return {name: root / filename for name, filename in _PREPARATION_FILES.items()}


def _validate_preparation_directory(path: Path) -> PreparationManifest:
    manifest_path = path / "preparation_manifest.json"
    payload = _read_json(
        manifest_path,
        OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
        "preparation manifest",
    )
    try:
        manifest = validate_preparation_manifest(payload)
    except OperationalContractError as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_PREPARATION_MANIFEST, str(exc)
        ) from exc
    if manifest_path.read_bytes() != manifest.canonical_bytes():
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
            "preparation manifest bytes are not canonical",
        )
    if path.name != manifest.preparation_id:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
            "preparation directory identity does not match manifest",
        )
    artifacts = _artifact_paths(path)
    feature_hashes = {item.role: item.sha256 for item in manifest.feature_artifacts}
    prediction_hashes = {
        item.role: item.sha256 for item in manifest.prediction_artifacts
    }
    try:
        expected = {
            "bootstrap": manifest.frozen_snapshot.artifact_sha256,
            "refresh_manifest": manifest.refresh_manifest_sha256,
            "players": manifest.frozen_player_artifact_sha256,
            "features": feature_hashes["player_gameweek_features"],
            "fixture_predictions": prediction_hashes["xfp_v01_fixtures"],
            "gameweek_predictions": prediction_hashes["xfp_v01_gameweek"],
        }
    except KeyError as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
            f"preparation manifest is missing required artifact role {exc.args[0]!r}",
        ) from exc
    evidence = {item.source: item.artifact_sha256 for item in manifest.accepted_evidence}
    if set(evidence) != {"official_fpl_fixtures", "official_fpl_player_history"}:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
            "preparation evidence sources are unsupported",
        )
    expected["fixtures"] = evidence["official_fpl_fixtures"]
    expected["history_manifest"] = evidence["official_fpl_player_history"]
    for name, digest in expected.items():
        _file_hash(artifacts[name], digest)
    fixture_manifest_hash = _file_hash(artifacts["fixtures_manifest"])
    refresh_payload = _read_json(
        artifacts["refresh_manifest"],
        OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
        "pinned refresh manifest",
    )
    fixture_payload = _read_json(
        artifacts["fixtures_manifest"],
        OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
        "pinned fixture manifest",
    )
    history_payload = _read_json(
        artifacts["history_manifest"],
        OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
        "pinned player-history manifest",
    )
    if (
        not isinstance(refresh_payload, dict)
        or refresh_payload.get("status") != "complete"
        or refresh_payload.get("season") != _season_from_features(
            artifacts["features"], manifest
        )
        or refresh_payload.get("bootstrap", {}).get("sha256") != expected["bootstrap"]
        or refresh_payload.get("fixtures", {}).get("sha256") != expected["fixtures"]
        or refresh_payload.get("fixtures", {}).get("manifest_sha256")
        != fixture_manifest_hash
        or refresh_payload.get("player_history", {}).get("manifest_sha256")
        != expected["history_manifest"]
        or refresh_payload.get("clean_outputs", {}).get("players", {}).get("sha256")
        != expected["players"]
        or not isinstance(fixture_payload, dict)
        or fixture_payload.get("status") != "complete"
        or fixture_payload.get("snapshot_timestamp")
        != refresh_payload.get("snapshot_timestamp")
        or fixture_payload.get("bootstrap_sha256") != expected["bootstrap"]
        or fixture_payload.get("response_sha256") != expected["fixtures"]
        or not isinstance(history_payload, dict)
        or history_payload.get("status") != "complete"
        or history_payload.get("snapshot_timestamp")
        != refresh_payload.get("snapshot_timestamp")
        or history_payload.get("bootstrap_sha256") != expected["bootstrap"]
        or history_payload.get("expected_count")
        != history_payload.get("success_count")
        or history_payload.get("failure_count") != 0
    ):
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
            "pinned refresh artifact chain does not reconcile",
        )
    snapshot_timestamp = refresh_payload.get("snapshot_timestamp")
    if (
        not isinstance(snapshot_timestamp, str)
        or _snapshot_observed_at(snapshot_timestamp)
        != manifest.frozen_snapshot.observed_at
    ):
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
            "pinned snapshot timestamp does not reconcile with frozen evidence",
        )
    observation_times = {
        item.source: item.observed_at for item in manifest.accepted_evidence
    }
    fixture_observed = _parse_utc(
        fixture_payload.get("retrieved_at"), "pinned fixture retrieval time"
    )[1]
    history_observed = _parse_utc(
        history_payload.get("completed_at"), "pinned history completion time"
    )[1]
    if (
        fixture_observed != observation_times["official_fpl_fixtures"]
        or history_observed != observation_times["official_fpl_player_history"]
    ):
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
            "pinned evidence observation timestamps do not reconcile",
        )
    _, deadline = _official_event(artifacts["bootstrap"], manifest.target_gameweek)
    if deadline != manifest.official_deadline:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
            "bootstrap deadline does not match preparation manifest",
        )
    return manifest


def prepare_gameweek(
    *,
    target_gameweek: int,
    season: str = "2026-27",
    raw_data_root: Path = Path("data/raw/fpl"),
    clean_data_root: Path = Path("data/clean/fpl"),
    feature_data_root: Path = Path("data/features/fpl"),
    prediction_data_root: Path = Path("data/predictions/fpl"),
    operations_root: Path = Path("data/operations/fpl"),
    resume_refresh_snapshot_timestamp: str | None = None,
    history_delay_seconds: float = 0.2,
    clock: Callable[[], datetime] = _system_utc_now,
    stages: OperationalStages = OperationalStages(),
) -> PreparationRunResult:
    """Run Phase 1 and stop at the expected verified-manager-state gate."""
    gameweek = _positive_gameweek(target_gameweek)
    _utc(clock(), "preparation start time")
    try:
        refresh = stages.refresh(
            raw_data_root=raw_data_root,
            clean_data_root=clean_data_root,
            season=season,
            resume_snapshot_timestamp=resume_refresh_snapshot_timestamp,
            history_delay_seconds=history_delay_seconds,
            clock=clock,
        )
    except RefreshError as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED, f"official refresh failed: {exc}"
        ) from exc
    paths = _validate_refresh(refresh, season)
    deadline_dt, deadline = _official_event(paths["bootstrap"], gameweek)
    _before_deadline(
        clock(), deadline_dt, OperationalErrorCode.DEADLINE_ALREADY_PASSED, "preparation time"
    )
    feature_path, fixture_prediction, gameweek_prediction = _stage_feature_and_predictions(
        refresh=refresh,
        paths=paths,
        season=season,
        target_gameweek=gameweek,
        raw_data_root=raw_data_root,
        clean_data_root=clean_data_root,
        feature_data_root=feature_data_root,
        prediction_data_root=prediction_data_root,
        stages=stages,
    )
    fixture_manifest = _read_json(
        paths["fixtures_manifest"],
        OperationalErrorCode.TRUSTED_STAGE_FAILED,
        "fixture manifest",
    )
    history_manifest = _read_json(
        paths["history_manifest"],
        OperationalErrorCode.TRUSTED_STAGE_FAILED,
        "history manifest",
    )
    try:
        preparation = build_preparation_manifest(
            target_gameweek=gameweek,
            official_deadline=deadline,
            refresh_manifest_sha256=_file_hash(paths["refresh_manifest"]),
            frozen_snapshot_sha256=_file_hash(paths["bootstrap"]),
            frozen_snapshot_observed_at=_snapshot_observed_at(refresh.snapshot_timestamp),
            accepted_evidence=(
                EvidenceObservation(
                    "official_fpl_fixtures",
                    fixture_manifest.get("retrieved_at"),
                    _file_hash(paths["fixtures"]),
                ),
                EvidenceObservation(
                    "official_fpl_player_history",
                    history_manifest.get("completed_at"),
                    _file_hash(paths["history_manifest"]),
                ),
            ),
            feature_artifacts={"player_gameweek_features": _file_hash(feature_path)},
            prediction_artifacts={
                "xfp_v01_fixtures": _file_hash(fixture_prediction),
                "xfp_v01_gameweek": _file_hash(gameweek_prediction),
            },
            frozen_player_artifact_sha256=_file_hash(paths["players"]),
            producer_versions={"prediction_model": MODEL_VERSION},
        )
    except OperationalContractError as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED,
            f"preparation evidence contract failed: {exc}",
        ) from exc
    _before_deadline(
        clock(),
        deadline_dt,
        OperationalErrorCode.DEADLINE_ALREADY_PASSED,
        "preparation finalization time",
    )
    directory = _preparation_directory(
        operations_root, season, gameweek, preparation.preparation_id
    )
    if directory.exists():
        existing = _validate_preparation_directory(directory)
        if existing.canonical_bytes() != preparation.canonical_bytes():
            raise OperationalRunnerError(
                OperationalErrorCode.CONFLICTING_IMMUTABLE_OUTPUT,
                "existing preparation has conflicting semantic bytes",
            )
        reused = True
    else:
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging = directory.parent / f".{preparation.preparation_id}.{uuid.uuid4().hex}.tmp"
        staging.mkdir()
        try:
            artifact_dir = staging / "artifacts"
            artifact_dir.mkdir()
            sources = {
                **paths,
                "features": feature_path,
                "fixture_predictions": fixture_prediction,
                "gameweek_predictions": gameweek_prediction,
            }
            for name, destination_name in _PREPARATION_FILES.items():
                _copy_fsync(sources[name], artifact_dir / destination_name)
            _write_atomic(
                staging / "preparation_manifest.json", preparation.canonical_bytes()
            )
            try:
                staging.rename(directory)
            except FileExistsError as exc:
                raise OperationalRunnerError(
                    OperationalErrorCode.CONFLICTING_IMMUTABLE_OUTPUT,
                    "preparation was concurrently published",
                ) from exc
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        _validate_preparation_directory(directory)
        reused = False
    return PreparationRunResult(
        status=PREPARATION_STATUS,
        preparation_id=preparation.preparation_id,
        preparation_manifest_path=directory / "preparation_manifest.json",
        preparation_manifest_sha256=preparation.sha256,
        reused=reused,
    )


_MANAGER_FIELDS = {
    "version",
    "entry_id",
    "season",
    "target_gameweek",
    "bank_m",
    "free_transfers",
    "chip_state",
    "evidence_source",
    "evidence_source_sha256",
    "current_selection_verified",
    "players",
}
_MANAGER_PLAYER_FIELDS = {"element_id", "display_name", "position", "selling_price_m"}


def load_verified_manager_evidence(path: Path) -> VerifiedManagerEvidence:
    payload = _read_json(
        path, OperationalErrorCode.INVALID_MANAGER_EVIDENCE, "manager evidence"
    )
    if not isinstance(payload, dict) or set(payload) != _MANAGER_FIELDS:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_MANAGER_EVIDENCE,
            "manager evidence fields do not match verified-manager-evidence-v1",
        )
    if payload.get("version") != MANAGER_EVIDENCE_VERSION:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_MANAGER_EVIDENCE,
            "manager evidence version is unsupported",
        )
    try:
        chip = ChipState(payload["chip_state"])
    except (TypeError, ValueError) as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_MANAGER_EVIDENCE,
            "manager chip state is unknown",
        ) from exc
    source_hash = payload["evidence_source_sha256"]
    if source_hash is not None and (
        not isinstance(source_hash, str)
        or len(source_hash) != 64
        or any(character not in "0123456789abcdef" for character in source_hash)
    ):
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_MANAGER_EVIDENCE,
            "manager evidence source SHA-256 is invalid",
        )
    rows = payload["players"]
    if not isinstance(rows, list) or len(rows) != 15:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_MANAGER_EVIDENCE,
            "manager evidence must contain exactly 15 players",
        )
    players = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != _MANAGER_PLAYER_FIELDS:
            raise OperationalRunnerError(
                OperationalErrorCode.INVALID_MANAGER_EVIDENCE,
                f"manager player {index} fields are invalid",
            )
        players.append(VerifiedManagerPlayer(**row))
    if len({row.element_id for row in players}) != 15:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_MANAGER_EVIDENCE,
            "manager evidence player IDs must be unique",
        )
    if (
        isinstance(payload["entry_id"], bool)
        or not isinstance(payload["entry_id"], int)
        or payload["entry_id"] <= 0
        or not isinstance(payload["season"], str)
        or not payload["season"]
        or isinstance(payload["target_gameweek"], bool)
        or not isinstance(payload["target_gameweek"], int)
        or not 1 <= payload["target_gameweek"] <= 38
        or isinstance(payload["free_transfers"], bool)
        or not isinstance(payload["free_transfers"], int)
        or payload["free_transfers"] < 0
    ):
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_MANAGER_EVIDENCE,
            "manager evidence identity or free-transfer count is invalid",
        )
    if not isinstance(payload["evidence_source"], str) or not payload[
        "evidence_source"
    ].strip():
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_MANAGER_EVIDENCE,
            "manager evidence source description is required",
        )
    if payload["current_selection_verified"] is not True:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_MANAGER_EVIDENCE,
            "current editable selection must be explicitly verified",
        )
    return VerifiedManagerEvidence(
        version=MANAGER_EVIDENCE_VERSION,
        entry_id=payload["entry_id"],
        season=payload["season"],
        target_gameweek=payload["target_gameweek"],
        bank_m=payload["bank_m"],
        free_transfers=payload["free_transfers"],
        chip_state=chip,
        evidence_source=payload["evidence_source"],
        evidence_source_sha256=source_hash,
        current_selection_verified=True,
        players=tuple(players),
        input_path=path.resolve(),
        input_sha256=sha256_file(path),
    )


_UNSUPPORTED_CHIP_CODES = {
    ChipState.WILDCARD: OperationalErrorCode.UNSUPPORTED_CHIP_WILDCARD,
    ChipState.FREE_HIT: OperationalErrorCode.UNSUPPORTED_CHIP_FREE_HIT,
    ChipState.BENCH_BOOST: OperationalErrorCode.UNSUPPORTED_CHIP_BENCH_BOOST,
    ChipState.TRIPLE_CAPTAIN: OperationalErrorCode.UNSUPPORTED_CHIP_TRIPLE_CAPTAIN,
}


def _validate_manager_alignment(
    evidence: VerifiedManagerEvidence, preparation: PreparationManifest, season: str
) -> None:
    if (
        evidence.season != season
        or evidence.target_gameweek != preparation.target_gameweek
    ):
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_MANAGER_EVIDENCE,
            "manager evidence season/gameweek does not match preparation",
        )
    if evidence.chip_state != ChipState.NO_CHIP:
        raise OperationalRunnerError(
            _UNSUPPORTED_CHIP_CODES[evidence.chip_state],
            f"Engine v1 does not support chip state {evidence.chip_state.value}",
        )


def _state_matches_evidence(
    state: ManualEditableState, evidence: VerifiedManagerEvidence
) -> None:
    expected = {
        row.element_id: (
            row.display_name,
            row.position,
            price_m_to_units(row.selling_price_m, "selling price"),
        )
        for row in evidence.players
    }
    observed = {
        row.element_id: (row.display_name, row.position, row.selling_price_units)
        for row in state.picks
    }
    if (
        state.entry_id != evidence.entry_id
        or state.season != evidence.season
        or state.target_gameweek != evidence.target_gameweek
        or state.bank_units != price_m_to_units(evidence.bank_m, "bank")
        or state.free_transfers != evidence.free_transfers
        or state.current_transfer_cost_points != 0
        or not state.selling_prices_verified
        or not state.current_selection_verified
        or observed != expected
    ):
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_MANAGER_EVIDENCE,
            "immutable manager state does not match submitted verified evidence",
        )


def _manager_state(
    *,
    evidence: VerifiedManagerEvidence,
    preparation_directory: Path,
    deadline: datetime,
    clock: Callable[[], datetime],
    stages: OperationalStages,
) -> ManualEditableState:
    root = preparation_directory / "manager_submissions" / evidence.input_sha256
    receipt_path = root / "manager_submission.json"
    existing_states = list(root.rglob("manual_editable_state.json")) if root.exists() else []
    if len(existing_states) > 1:
        raise OperationalRunnerError(
            OperationalErrorCode.CONFLICTING_IMMUTABLE_OUTPUT,
            "manager submission contains multiple immutable states",
        )
    if existing_states:
        try:
            state = load_manual_editable_state(existing_states[0])
        except EditableManagerError as exc:
            raise OperationalRunnerError(
                OperationalErrorCode.PINNED_ARTIFACT_HASH_MISMATCH,
                f"manager state failed validation: {exc}",
            ) from exc
        _state_matches_evidence(state, evidence)
        if receipt_path.exists():
            receipt = _read_json(
                receipt_path,
                OperationalErrorCode.PINNED_ARTIFACT_HASH_MISMATCH,
                "manager submission receipt",
            )
            expected_receipt = {
                "version": MANAGER_SUBMISSION_VERSION,
                "manager_input_sha256": evidence.input_sha256,
                "manager_state_sha256": state.artifact_sha256,
                "manager_verification_timestamp": state.verification_timestamp,
                "manager_state_relative_path": state.artifact_path.relative_to(root).as_posix(),
            }
            if receipt != expected_receipt:
                raise OperationalRunnerError(
                    OperationalErrorCode.PINNED_ARTIFACT_HASH_MISMATCH,
                    "manager submission receipt does not reconcile",
                )
        else:
            _write_atomic(
                receipt_path,
                canonical_json_bytes(
                    {
                        "version": MANAGER_SUBMISSION_VERSION,
                        "manager_input_sha256": evidence.input_sha256,
                        "manager_state_sha256": state.artifact_sha256,
                        "manager_verification_timestamp": state.verification_timestamp,
                        "manager_state_relative_path": state.artifact_path.relative_to(root).as_posix(),
                    }
                ),
            )
        return state
    verification_now = clock()
    verification_timestamp = _before_deadline(
        verification_now,
        deadline,
        OperationalErrorCode.MANAGER_VERIFICATION_AT_OR_AFTER_DEADLINE,
        "manager verification time",
    )
    try:
        state = stages.create_manager_state(
            entry_id=evidence.entry_id,
            season=evidence.season,
            target_gameweek=evidence.target_gameweek,
            picks=tuple(
                ManualEditablePick(
                    element_id=row.element_id,
                    display_name=row.display_name,
                    position=row.position,
                    selling_price_units=price_m_to_units(
                        row.selling_price_m, "selling price"
                    ),
                )
                for row in evidence.players
            ),
            bank_m=evidence.bank_m,
            free_transfers=evidence.free_transfers,
            current_transfer_cost_points=0,
            post_deadline_transfers_known=False,
            selling_prices_verified=True,
            verification_timestamp=verification_timestamp,
            current_selection_verified=True,
            manual_data_root=root,
            recorded_at=verification_now,
        )
    except EditableManagerError as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_MANAGER_EVIDENCE,
            f"trusted manager-state validation failed: {exc}",
        ) from exc
    _state_matches_evidence(state, evidence)
    _write_atomic(
        receipt_path,
        canonical_json_bytes(
            {
                "version": MANAGER_SUBMISSION_VERSION,
                "manager_input_sha256": evidence.input_sha256,
                "manager_state_sha256": state.artifact_sha256,
                "manager_verification_timestamp": state.verification_timestamp,
                "manager_state_relative_path": state.artifact_path.relative_to(root).as_posix(),
            }
        ),
    )
    return state


def _unique_artifact(root: Path, filename: str) -> Path | None:
    matches = list(root.rglob(filename)) if root.exists() else []
    if len(matches) > 1:
        raise OperationalRunnerError(
            OperationalErrorCode.CONFLICTING_IMMUTABLE_OUTPUT,
            f"multiple {filename} artifacts exist under {root}",
        )
    return matches[0] if matches else None


def _season_from_features(path: Path, preparation: PreparationManifest) -> str:
    connection = duckdb.connect(":memory:")
    try:
        rows = connection.execute(
            "SELECT DISTINCT season FROM read_parquet(?)", [str(path)]
        ).fetchall()
    except duckdb.Error as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.PINNED_ARTIFACT_HASH_MISMATCH,
            f"could not establish preparation season: {exc}",
        ) from exc
    finally:
        connection.close()
    if len(rows) != 1 or not isinstance(rows[0][0], str):
        raise OperationalRunnerError(
            OperationalErrorCode.PINNED_ARTIFACT_HASH_MISMATCH,
            "preparation feature artifact has ambiguous season",
        )
    return rows[0][0]


def _completed_result(
    decision_directory: Path,
    preparation: PreparationManifest,
    manager_state: ManualEditableState,
    manager_evidence: VerifiedManagerEvidence,
    stages: OperationalStages,
) -> CompletedRunResult:
    final_path = decision_directory / "final_operational_manifest.json"
    payload = _read_json(
        final_path,
        OperationalErrorCode.INVALID_COMPLETED_FINAL_MANIFEST,
        "final operational manifest",
    )
    try:
        final = validate_final_operational_manifest(payload, preparation)
    except OperationalContractError as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_COMPLETED_FINAL_MANIFEST, str(exc)
        ) from exc
    if final_path.read_bytes() != final.canonical_bytes():
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_COMPLETED_FINAL_MANIFEST,
            "final manifest bytes are not canonical",
        )
    if final.decision_id != decision_directory.name:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_COMPLETED_FINAL_MANIFEST,
            "final decision identity does not match directory",
        )
    manager_verification = _parse_utc(
        manager_state.verification_timestamp, "manager verification timestamp"
    )[1]
    if (
        final.chip_state != ChipState.NO_CHIP
        or final.manager_evidence_source != manager_evidence.evidence_source
        or final.manager_evidence_source_sha256
        != manager_evidence.evidence_source_sha256
        or final.manager_verification_timestamp != manager_verification
    ):
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_COMPLETED_FINAL_MANIFEST,
            "completed final manifest does not match verified manager evidence",
        )
    _file_hash(manager_state.artifact_path, final.manager_state_sha256)
    task_root = decision_directory / "task016"
    decision_path = _unique_artifact(task_root, "one_transfer_decision.json")
    candidates_path = _unique_artifact(task_root, "legal_transfer_candidates.json")
    reliability_path = _unique_artifact(task_root, "decision_reliability.json")
    gameweek_path = decision_directory / "gameweek_decision.json"
    if not all((decision_path, candidates_path, reliability_path)):
        raise OperationalRunnerError(
            OperationalErrorCode.PINNED_ARTIFACT_MISSING,
            "completed run is missing a trusted child artifact",
        )
    _file_hash(candidates_path, final.candidate_artifact_sha256)  # type: ignore[arg-type]
    _file_hash(decision_path, final.one_transfer_decision_sha256)  # type: ignore[arg-type]
    _file_hash(reliability_path, final.reliability_artifact_sha256)  # type: ignore[arg-type]
    _file_hash(gameweek_path, final.gameweek_decision_contract_sha256)
    try:
        rebuilt = stages.serialize_gameweek_decision(
            stages.build_gameweek_decision(decision_path, reliability_path)  # type: ignore[arg-type]
        )
    except (GameweekDecisionError, DecisionReliabilityError) as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_COMPLETED_FINAL_MANIFEST,
            f"completed GameweekDecision chain failed validation: {exc}",
        ) from exc
    if rebuilt != gameweek_path.read_bytes():
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_COMPLETED_FINAL_MANIFEST,
            "completed GameweekDecision does not deterministically rebuild",
        )
    gameweek_payload = _read_json(
        gameweek_path,
        OperationalErrorCode.INVALID_COMPLETED_FINAL_MANIFEST,
        "completed GameweekDecision",
    )
    source_artifacts = (
        gameweek_payload.get("source_artifacts")
        if isinstance(gameweek_payload, dict)
        else None
    )
    schema_rows = (
        [row for row in source_artifacts if row.get("role") == "contract_schema"]
        if isinstance(source_artifacts, list)
        and all(isinstance(row, dict) for row in source_artifacts)
        else []
    )
    if (
        len(schema_rows) != 1
        or gameweek_payload.get("schema_version")
        != final.gameweek_decision_schema_version
        or schema_rows[0].get("sha256") != final.gameweek_decision_schema_sha256
    ):
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_COMPLETED_FINAL_MANIFEST,
            "completed GameweekDecision schema provenance does not reconcile",
        )
    return CompletedRunResult(
        status=COMPLETED_STATUS,
        preparation_id=preparation.preparation_id,
        decision_id=final.decision_id,
        gameweek_decision_path=gameweek_path,
        gameweek_decision_sha256=final.gameweek_decision_contract_sha256,
        final_manifest_path=final_path,
        final_manifest_sha256=final.sha256,
        reused=True,
    )


def resume_gameweek(
    *,
    preparation_manifest_path: Path,
    manager_evidence_path: Path,
    clock: Callable[[], datetime] = _system_utc_now,
    stages: OperationalStages = OperationalStages(),
) -> CompletedRunResult:
    """Run Phase 2 against one exact frozen preparation; never discover latest."""
    preparation_directory = preparation_manifest_path.resolve().parent
    preparation = _validate_preparation_directory(preparation_directory)
    if preparation_manifest_path.resolve() != (
        preparation_directory / "preparation_manifest.json"
    ):
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
            "resume requires the exact preparation manifest path",
        )
    artifacts = _artifact_paths(preparation_directory)
    season = _season_from_features(artifacts["features"], preparation)
    # Snapshot discovery is deliberately absent. Validate only the pinned copies.
    pinned_refresh = _read_json(
        artifacts["refresh_manifest"],
        OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
        "pinned refresh manifest",
    )
    pinned_snapshot = (
        pinned_refresh.get("snapshot_timestamp")
        if isinstance(pinned_refresh, dict)
        else None
    )
    if not isinstance(pinned_snapshot, str):
        raise OperationalRunnerError(
            OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
            "pinned refresh manifest has no snapshot timestamp",
        )
    _validate_feature(
        artifacts["features"],
        season,
        pinned_snapshot,
        preparation.target_gameweek,
    )
    _validate_predictions(
        artifacts["fixture_predictions"],
        artifacts["gameweek_predictions"],
        artifacts["features"],
        artifacts["players"],
        season,
        pinned_snapshot,
        preparation.target_gameweek,
    )
    deadline_dt, _ = _parse_utc(preparation.official_deadline, "official deadline")
    _before_deadline(
        clock(), deadline_dt, OperationalErrorCode.DEADLINE_ALREADY_PASSED, "resume start time"
    )
    evidence = load_verified_manager_evidence(manager_evidence_path)
    _validate_manager_alignment(evidence, preparation, season)
    manager_state = _manager_state(
        evidence=evidence,
        preparation_directory=preparation_directory,
        deadline=deadline_dt,
        clock=clock,
        stages=stages,
    )
    decision_id = build_decision_id(
        preparation_id=preparation.preparation_id,
        manager_state_sha256=manager_state.artifact_sha256,
    )
    decision_directory = preparation_directory / "decisions" / decision_id
    final_path = decision_directory / "final_operational_manifest.json"
    if final_path.exists():
        return _completed_result(
            decision_directory, preparation, manager_state, evidence, stages
        )
    projections = XfpV01ParquetProvider(
        projection_artifact=artifacts["gameweek_predictions"],
        players_artifact=artifacts["players"],
    ).load(season=season, target_gameweek=preparation.target_gameweek)
    selling_prices = {
        row.element_id: row.selling_price_units
        for row in manager_state.picks
        if row.selling_price_units is not None
    }
    task_root = decision_directory / "task016"
    decision_path = _unique_artifact(task_root, "one_transfer_decision.json")
    candidates_path = _unique_artifact(task_root, "legal_transfer_candidates.json")
    if (decision_path is None) != (candidates_path is None):
        raise OperationalRunnerError(
            OperationalErrorCode.PINNED_ARTIFACT_MISSING,
            "Task 016 candidate/decision publication is incomplete",
        )
    try:
        if decision_path is None:
            decision = stages.evaluate_transfer(
                manager_state,
                projections,
                selling_prices,
                decision_policy=APPEARANCE_ONLY_ALLOWED_POLICY,
                selling_price_source=SELLING_PRICE_SOURCE,
            )
            generated_at, _ = _utc(clock(), "decision generation time")
            written = stages.write_transfer(
                decision, decision_data_root=task_root, generated_at=generated_at
            )
            decision_path, candidates_path = written.decision_path, written.candidates_path
        context = stages.load_reliability(decision_path, artifacts["features"])
        reliability_path = _unique_artifact(task_root, "decision_reliability.json")
        if reliability_path is None:
            generated_at, _ = _utc(clock(), "reliability generation time")
            reliability = stages.write_reliability(context, generated_at=generated_at)
            reliability_path = reliability.reliability_path
        payload = stages.build_gameweek_decision(decision_path, reliability_path)
        gameweek_bytes = stages.serialize_gameweek_decision(payload)
    except (TransferDecisionError, DecisionReliabilityError, GameweekDecisionError) as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED,
            f"trusted decision stage failed: {exc}",
        ) from exc
    gameweek_path = decision_directory / "gameweek_decision.json"
    if gameweek_path.exists() and gameweek_path.read_bytes() != gameweek_bytes:
        raise OperationalRunnerError(
            OperationalErrorCode.CONFLICTING_IMMUTABLE_OUTPUT,
            "existing GameweekDecision differs from deterministic rebuild",
        )
    finalization_now = clock()
    finalization_timestamp = _before_deadline(
        finalization_now,
        deadline_dt,
        OperationalErrorCode.DEADLINE_PASSED_DURING_FINALIZATION,
        "finalization time",
    )
    source_entries = payload.get("source_artifacts")
    schema_entries = (
        [row for row in source_entries if row.get("role") == "contract_schema"]
        if isinstance(source_entries, list)
        else []
    )
    if len(schema_entries) != 1:
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED,
            "GameweekDecision schema provenance is ambiguous",
        )
    try:
        final = build_final_operational_manifest(
            preparation=preparation,
            manager_state_sha256=manager_state.artifact_sha256,
            manager_verification_timestamp=manager_state.verification_timestamp,
            manager_evidence_source=evidence.evidence_source,
            manager_evidence_source_sha256=evidence.evidence_source_sha256,
            chip_state=evidence.chip_state,
            candidate_artifact_sha256=_file_hash(candidates_path),  # type: ignore[arg-type]
            one_transfer_decision_sha256=_file_hash(decision_path),  # type: ignore[arg-type]
            reliability_artifact_sha256=_file_hash(reliability_path),
            gameweek_decision_schema_version=payload["schema_version"],
            gameweek_decision_schema_sha256=schema_entries[0]["sha256"],
            gameweek_decision_contract_sha256=hashlib.sha256(gameweek_bytes).hexdigest(),
            finalization_timestamp=finalization_timestamp,
        )
    except (OperationalContractError, KeyError, TypeError) as exc:
        raise OperationalRunnerError(
            OperationalErrorCode.TRUSTED_STAGE_FAILED,
            f"final operational contract failed: {exc}",
        ) from exc
    _write_atomic(gameweek_path, gameweek_bytes)
    _write_atomic(final_path, final.canonical_bytes())
    validated = _completed_result(
        decision_directory, preparation, manager_state, evidence, stages
    )
    return CompletedRunResult(
        status=validated.status,
        preparation_id=validated.preparation_id,
        decision_id=validated.decision_id,
        gameweek_decision_path=validated.gameweek_decision_path,
        gameweek_decision_sha256=validated.gameweek_decision_sha256,
        final_manifest_path=validated.final_manifest_path,
        final_manifest_sha256=validated.final_manifest_sha256,
        reused=False,
    )
