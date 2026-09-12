"""Capture and verify two legacy experiment manifests without parsing Parquet."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


FORMAT_VERSION = 1
VERIFIER_VERSION = "task033b1a-v1"
MANIFEST_NAME = "experiment_manifest.json"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
SHA256_RE = re.compile(r"[0-9a-f]{64}")
OUTPUT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\.parquet")
EMAIL_RE = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?Z")
HISTORICAL_CLASSIFICATION = "restricted_pseudo_backtest"
HISTORY_CUTOFF_RULE = (
    "performance_gameweek < target_gameweek AND fixture_kickoff < target_deadline"
)
ASSURANCE_CODE = "LOCALLY_COHERENT_LEGACY_RESULT"
ASSURANCE_LIMITATION_CODE = "LEGACY_LOCAL_COHERENCE_ONLY"
ASSURANCE_LIMITATION = (
    "Present local bytes are coherent with the reviewed legacy contract and declared "
    "outputs. This does not prove historical generator identity, creation-time "
    "immutability, independent backup, or production promotion."
)


class ProvenanceError(Exception):
    """Fixed-code failure; details must never contain private input values."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SlotContract:
    slot: str
    experiment_version: str
    contract_reference_commit: str
    source_blob: str
    candidates: tuple[str, ...]
    candidate_definitions: dict[str, str]
    development_thresholds: dict[str, Any]
    development_tie_breakers: list[str]
    promote_decision: str
    reject_decision: str
    extra_fields: dict[str, Any]


MINUTES = SlotContract(
    slot="minutes",
    experiment_version="minutes-v02-experiment-v1",
    contract_reference_commit="c5269ed4421b8937a8fc62ca35029af829345f92",
    source_blob="b58a47d84e48026335b235eb9e54b1fb685d471f",
    candidates=("M0", "M1", "M2", "M3"),
    candidate_definitions={
        "M0": "previous calendar-GW minutes persistence",
        "M1": "mean of observed minutes in prior 3 calendar FPL GWs",
        "M2": "mean of observed minutes in prior 5 calendar FPL GWs",
        "M3": (
            "0.60/0.30/0.10 recency weights over prior 3 calendar FPL GWs, "
            "renormalized over observed GWs"
        ),
    },
    development_thresholds={
        "minutes_mae_reduction_pct": 5.0,
        "minutes_rmse_reduction_pct": 3.0,
        "modeled_xfp_mae_reduction_pct": 2.0,
        "maximum_coverage_drop_pp": 1.0,
    },
    development_tie_breakers=[
        "largest modeled xFP MAE reduction",
        "lower modeled xFP RMSE",
        "lower minutes MAE",
        "simpler M1 then M2 then M3",
    ],
    promote_decision="PROMOTE CANDIDATE TO xFP v0.2 DESIGN",
    reject_decision="DO NOT PROMOTE — KEEP v0.1 MINUTES",
    extra_fields={
        "observation_policy": (
            "A real fixture with zero minutes is observed zero. A verified blank or "
            "player not in the historical universe is missing, not zero. Eligible DGW "
            "fixture minutes are summed to player-GW before the window. At least one "
            "observed GW is required. Candidate fixture minutes are capped to 0-90 "
            "before the frozen availability hard gate."
        ),
        "temporal_cutoff_rule": HISTORY_CUTOFF_RULE,
        "selection_population": (
            "common complete M0/candidate/actual pairs; coverage checked separately"
        ),
        "holdout_thresholds": {
            "minutes_mae_reduction_pct": 5.0,
            "minutes_rmse_reduction_pct": 3.0,
            "modeled_xfp_mae_reduction_pct": 2.0,
            "maximum_coverage_drop_pp": 1.0,
            "maximum_modeled_spearman_drop": 0.01,
            "appearance_mae_must_not_worsen": True,
        },
    },
)

ATTACKING_RATES = SlotContract(
    slot="attacking_rates",
    experiment_version="attacking-rate-v02-experiment-v1",
    contract_reference_commit="391b04328754ad95bf4304c438826688d01ad7e0",
    source_blob="566b27cf438ef1821ba021d6080745db883c96bb",
    candidates=("S0", "S1", "S2", "S3"),
    candidate_definitions={
        "S0": "raw cumulative prior xG/90 and xA/90",
        "S1": (
            "(raw_rate*observed_minutes + causal_position_rate*450)/"
            "(observed_minutes+450)"
        ),
        "S2": (
            "(raw_rate*observed_minutes + causal_position_rate*900)/"
            "(observed_minutes+900)"
        ),
        "S3": "causal_position_rate when observed_minutes<180, otherwise raw_rate",
    },
    development_thresholds={
        "minimum_goal_spearman_improvement": 0.01,
        "minimum_assist_spearman_improvement": 0.01,
        "maximum_modeled_mae_worsening_pct": 1.0,
        "maximum_modeled_rmse_worsening_pct": 1.0,
        "maximum_absolute_modeled_bias_worsening": 0.02,
        "maximum_coverage_drop_pp": 1.0,
    },
    development_tie_breakers=[
        "mean goal/assist Spearman improvement",
        "lower modeled MAE",
        "lower modeled RMSE",
        "simpler S1 then S2 then S3",
    ],
    promote_decision="PROMOTE ATTACKING-RATE CANDIDATE TO xFP v0.2 DESIGN",
    reject_decision="DO NOT PROMOTE — KEEP v0.1 ATTACKING RATES",
    extra_fields={
        "primary_selection_population": (
            "actual_target_minutes > 0, applied only after predictions are generated"
        ),
        "position_prior_definition": (
            "90*sum(eligible prior event value)/sum(minutes for rows where that event "
            "value is non-null), separately for xG and xA"
        ),
        "position_prior_uses_player_own_history": True,
        "league_prior_fallback": False,
        "temporal_cutoff_rule": HISTORY_CUTOFF_RULE,
        "frozen_components": [
            "expected_minutes",
            "availability_hard_gate",
            "blanks",
            "DGWs",
            "position_scoring",
            "appearance",
            "aggregation",
            "evaluation_definitions",
        ],
        "coverage_policy": (
            "Metrics use only non-null prediction/actual pairs; modeled numeric "
            "appearance-only predictions retain baseline-v1 semantics; coverage is "
            "reported separately."
        ),
        "selection_common_pair_policy": (
            "S0, candidate, and actual all non-null within the played population"
        ),
    },
)

CONTRACTS = (MINUTES, ATTACKING_RATES)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ProvenanceError("INVALID_CLOCK")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _absolute_lexical(path: Path) -> Path:
    raw = os.fspath(path)
    if not path.is_absolute() or ".." in raw.split(os.sep):
        raise ProvenanceError("UNSAFE_PATH")
    return Path(os.path.abspath(raw))


def open_root(path: Path) -> int:
    path = _absolute_lexical(path)
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            next_descriptor = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_stable_at(
    directory_fd: int,
    name: str,
    *,
    maximum_bytes: int,
) -> tuple[bytes, os.stat_result]:
    before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    # Link count catches a currently aliased file, which helps with accidental
    # substitution. It cannot attest prior link history or defeat a same-owner
    # actor who removes every other name before verification.
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ProvenanceError("UNSAFE_INPUT_FILE")
    if before.st_size < 1 or before.st_size > maximum_bytes:
        raise ProvenanceError("INVALID_INPUT_SIZE")
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    try:
        if fingerprint(os.fstat(descriptor)) != fingerprint(before):
            raise ProvenanceError("INPUT_CHANGED")
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(descriptor, min(CHUNK_BYTES, maximum_bytes + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > maximum_bytes:
                raise ProvenanceError("INVALID_INPUT_SIZE")
        after_fd = os.fstat(descriptor)
        after_name = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            total != before.st_size
            or fingerprint(after_fd) != fingerprint(before)
            or fingerprint(after_name) != fingerprint(before)
        ):
            raise ProvenanceError("INPUT_CHANGED")
        return b"".join(chunks), before
    finally:
        os.close(descriptor)


def _hash_stable_at(
    directory_fd: int,
    name: str,
    *,
    expected_size: int,
) -> str:
    before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    # This is a current-alias check, not historical or adversarial provenance.
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ProvenanceError("UNSAFE_OUTPUT_FILE")
    if before.st_size != expected_size or before.st_size > MAX_OUTPUT_BYTES:
        raise ProvenanceError("OUTPUT_SIZE_MISMATCH")
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=directory_fd,
    )
    try:
        if fingerprint(os.fstat(descriptor)) != fingerprint(before):
            raise ProvenanceError("OUTPUT_CHANGED")
        digest = hashlib.sha256()
        total = 0
        while block := os.read(descriptor, CHUNK_BYTES):
            digest.update(block)
            total += len(block)
            if total > MAX_OUTPUT_BYTES:
                raise ProvenanceError("OUTPUT_SIZE_MISMATCH")
        after_fd = os.fstat(descriptor)
        after_name = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            total != expected_size
            or fingerprint(after_fd) != fingerprint(before)
            or fingerprint(after_name) != fingerprint(before)
        ):
            raise ProvenanceError("OUTPUT_CHANGED")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _root_unchanged(path: Path, descriptor: int, initial: tuple[int, ...]) -> None:
    if fingerprint(os.fstat(descriptor)) != initial:
        raise ProvenanceError("DIRECTORY_CHANGED")
    reopened = open_root(path)
    try:
        if fingerprint(os.fstat(reopened)) != initial:
            raise ProvenanceError("DIRECTORY_CHANGED")
    finally:
        os.close(reopened)


def _reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProvenanceError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ProvenanceError("NON_FINITE_JSON_NUMBER")


def parse_manifest(manifest_bytes: bytes) -> dict[str, Any]:
    try:
        text = manifest_bytes.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate,
            parse_constant=_reject_constant,
        )
    except ProvenanceError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ProvenanceError("INVALID_MANIFEST_JSON") from exc
    if not isinstance(value, dict):
        raise ProvenanceError("INVALID_MANIFEST_TYPE")
    _reject_non_finite(value)
    return value


def _reject_non_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ProvenanceError("NON_FINITE_JSON_NUMBER")
    if isinstance(value, dict):
        for nested in value.values():
            _reject_non_finite(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_non_finite(nested)


def _same_type_value(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _same_type_value(actual[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _same_type_value(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _require_exact(manifest: dict[str, Any], field: str, expected: Any) -> None:
    if field not in manifest or not _same_type_value(manifest[field], expected):
        raise ProvenanceError("MANIFEST_CONTRACT_MISMATCH")


def _valid_utc(value: Any) -> bool:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo == timezone.utc


def _safe_output_name(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value not in (".", "..", MANIFEST_NAME)
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
        and OUTPUT_NAME_RE.fullmatch(value) is not None
    )


def _validate_immutable_inputs(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ProvenanceError("INVALID_IMMUTABLE_INPUTS")
    result: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            raise ProvenanceError("INVALID_IMMUTABLE_INPUTS")
        path = entry["path"]
        digest = entry["sha256"]
        if (
            not isinstance(path, str)
            or not path
            or "\x00" in path
            or path in seen_paths
            or not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
        ):
            raise ProvenanceError("INVALID_IMMUTABLE_INPUTS")
        seen_paths.add(path)
        result.append({"path": path, "sha256": digest})
    return result


def _validate_outputs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ProvenanceError("INVALID_OUTPUT_DECLARATIONS")
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"path", "rows", "bytes", "sha256"}:
            raise ProvenanceError("INVALID_OUTPUT_DECLARATIONS")
        name, rows, size, digest = (
            entry["path"],
            entry["rows"],
            entry["bytes"],
            entry["sha256"],
        )
        if (
            not _safe_output_name(name)
            or name in names
            or type(rows) is not int
            or rows < 0
            or type(size) is not int
            or size < 1
            or size > MAX_OUTPUT_BYTES
            or not isinstance(digest, str)
            or SHA256_RE.fullmatch(digest) is None
        ):
            raise ProvenanceError("INVALID_OUTPUT_DECLARATIONS")
        names.add(name)
        result.append(dict(entry))
    return result


def validate_manifest(
    manifest: dict[str, Any], contract: SlotContract
) -> tuple[dict[str, Any], list[dict[str, str]], list[dict[str, Any]]]:
    fixed = {
        "status": "complete",
        "experiment_version": contract.experiment_version,
        "historical_classification": HISTORICAL_CLASSIFICATION,
        "model_formula_frozen": "xfp_v01",
        "live_model_modified": False,
        "development_season": "2023-24",
        "holdout_season": "2024-25",
        "target_gameweeks": list(range(2, 39)),
        "candidate_definitions": contract.candidate_definitions,
        "development_thresholds": contract.development_thresholds,
        "development_tie_breakers": contract.development_tie_breakers,
        **contract.extra_fields,
    }
    for field, expected in fixed.items():
        _require_exact(manifest, field, expected)

    winner = manifest.get("development_winner")
    evaluated = manifest.get("holdout_evaluated")
    passed = manifest.get("holdout_passed")
    decision = manifest.get("final_decision")
    if winner is not None and winner not in contract.candidates[1:]:
        raise ProvenanceError("INVALID_RESULT_STATE")
    if type(evaluated) is not bool or evaluated is not (winner is not None):
        raise ProvenanceError("INVALID_RESULT_STATE")
    if passed is not None and type(passed) is not bool:
        raise ProvenanceError("INVALID_RESULT_STATE")
    if not evaluated and passed is not None:
        raise ProvenanceError("INVALID_RESULT_STATE")
    if evaluated and passed is None:
        raise ProvenanceError("INVALID_RESULT_STATE")
    expected_decision = contract.promote_decision if passed is True else contract.reject_decision
    if decision != expected_decision:
        raise ProvenanceError("INVALID_RESULT_STATE")
    if not _valid_utc(manifest.get("generation_timestamp")):
        raise ProvenanceError("INVALID_GENERATION_TIMESTAMP")

    if contract.slot == "minutes":
        oracle = manifest.get("oracle_reference")
        if not isinstance(oracle, dict) or set(oracle) != {
            "evaluation_only",
            "modeled_mae_reduction_pct",
            "modeled_rmse_reduction_pct",
            "holdout_oracle_mae_improvement_captured_pct",
        }:
            raise ProvenanceError("INVALID_ORACLE_REFERENCE")
        if (
            oracle["evaluation_only"] is not True
            or type(oracle["modeled_mae_reduction_pct"]) is not float
            or oracle["modeled_mae_reduction_pct"] != 34.53
            or type(oracle["modeled_rmse_reduction_pct"]) is not float
            or oracle["modeled_rmse_reduction_pct"] != 17.35
        ):
            raise ProvenanceError("INVALID_ORACLE_REFERENCE")
        dynamic = oracle["holdout_oracle_mae_improvement_captured_pct"]
        if evaluated:
            if type(dynamic) not in (int, float) or not math.isfinite(dynamic):
                raise ProvenanceError("INVALID_ORACLE_REFERENCE")
        elif dynamic is not None:
            raise ProvenanceError("INVALID_ORACLE_REFERENCE")

    immutable_inputs = _validate_immutable_inputs(manifest.get("immutable_inputs"))
    outputs = _validate_outputs(manifest.get("outputs"))
    semantic = {field: manifest[field] for field in fixed}
    semantic.update(
        {
            "development_winner": winner,
            "holdout_evaluated": evaluated,
            "holdout_passed": passed,
            "final_decision": decision,
            "generation_timestamp": manifest["generation_timestamp"],
        }
    )
    if contract.slot == "minutes":
        semantic["oracle_reference"] = manifest["oracle_reference"]
    return semantic, immutable_inputs, outputs


def _capture_slot(path: Path, contract: SlotContract) -> dict[str, Any]:
    descriptor = open_root(path)
    try:
        root_fingerprint = fingerprint(os.fstat(descriptor))
        manifest_bytes, _ = _read_stable_at(
            descriptor, MANIFEST_NAME, maximum_bytes=MAX_MANIFEST_BYTES
        )
        _root_unchanged(path, descriptor, root_fingerprint)
        return {
            "logical_slot": contract.slot,
            "manifest_bytes": len(manifest_bytes),
            "manifest_sha256": sha256_bytes(manifest_bytes),
        }
    finally:
        os.close(descriptor)


def _verify_slot(
    path: Path,
    expected_manifest_sha256: str,
    contract: SlotContract,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if SHA256_RE.fullmatch(expected_manifest_sha256) is None:
        raise ProvenanceError("INVALID_CAPTURED_DIGEST")
    descriptor = open_root(path)
    try:
        root_fingerprint = fingerprint(os.fstat(descriptor))
        manifest_bytes, _ = _read_stable_at(
            descriptor, MANIFEST_NAME, maximum_bytes=MAX_MANIFEST_BYTES
        )
        manifest_digest = sha256_bytes(manifest_bytes)
        if manifest_digest != expected_manifest_sha256:
            raise ProvenanceError("MANIFEST_DIGEST_MISMATCH")
        # The exact bytes hashed above are passed directly to the strict parser.
        manifest = parse_manifest(manifest_bytes)
        semantic, immutable_inputs, outputs = validate_manifest(manifest, contract)
        verified_outputs: list[dict[str, Any]] = []
        for output in outputs:
            actual = _hash_stable_at(
                descriptor, output["path"], expected_size=output["bytes"]
            )
            if actual != output["sha256"]:
                raise ProvenanceError("OUTPUT_DIGEST_MISMATCH")
            verified_outputs.append(
                {
                    "logical_file_name": output["path"],
                    "rows": output["rows"],
                    "rows_assurance": "MANIFEST_DECLARED",
                    "bytes": output["bytes"],
                    "sha256": actual,
                }
            )
        _root_unchanged(path, descriptor, root_fingerprint)
    finally:
        os.close(descriptor)

    private = {
        "logical_slot": contract.slot,
        "expected_experiment_version": contract.experiment_version,
        "contract_reference_commit": contract.contract_reference_commit,
        "contract_source_blob": contract.source_blob,
        "manifest_sha256": manifest_digest,
        "manifest_bytes": len(manifest_bytes),
        "validated_semantic_fields": semantic,
        "immutable_inputs": immutable_inputs,
        "outputs": verified_outputs,
        "status": ASSURANCE_CODE,
    }
    sanitized = {
        "logical_slot": contract.slot,
        "expected_experiment_version": contract.experiment_version,
        "contract_reference_commit": contract.contract_reference_commit,
        "contract_source_blob": contract.source_blob,
        "manifest_sha256": manifest_digest,
        "manifest_bytes": len(manifest_bytes),
        "development_winner": semantic["development_winner"],
        "holdout_evaluated": semantic["holdout_evaluated"],
        "holdout_passed": semantic["holdout_passed"],
        "final_decision": semantic["final_decision"],
        "immutable_input_count": len(immutable_inputs),
        "output_count": len(verified_outputs),
        "outputs": [
            {
                "logical_file_name": output["logical_file_name"],
                "bytes": output["bytes"],
                "sha256": output["sha256"],
            }
            for output in verified_outputs
        ],
        "status": ASSURANCE_CODE,
    }
    return private, sanitized


def _tool_sha256() -> str:
    tool = _absolute_lexical(Path(os.path.abspath(__file__)))
    descriptor = open_root(tool.parent)
    try:
        root_fingerprint = fingerprint(os.fstat(descriptor))
        body, _ = _read_stable_at(
            descriptor, tool.name, maximum_bytes=MAX_MANIFEST_BYTES
        )
        _root_unchanged(tool.parent, descriptor, root_fingerprint)
        return sha256_bytes(body)
    finally:
        os.close(descriptor)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )


@dataclass
class Destination:
    path: Path
    parent_fd: int
    parent_fingerprint: tuple[int, ...]

    def close(self) -> None:
        os.close(self.parent_fd)


def validate_destination(
    path: Path,
    repository: Path,
    *,
    git_runner: Callable[..., subprocess.CompletedProcess[bytes]] = _run_git,
) -> Destination:
    path = _absolute_lexical(path)
    repository = _absolute_lexical(repository)
    operations = repository / "data" / "operations"
    try:
        relative = path.relative_to(operations)
    except ValueError as exc:
        raise ProvenanceError("OUTPUT_OUTSIDE_PRIVATE_ROOT") from exc
    if not relative.parts or path.name in ("", ".", ".."):
        raise ProvenanceError("INVALID_OUTPUT_PATH")
    repository_relative = path.relative_to(repository).as_posix()
    ignored = git_runner(
        repository, "check-ignore", "--quiet", "--", repository_relative
    )
    tracked = git_runner(
        repository, "ls-files", "--cached", "--error-unmatch", "--", repository_relative
    )
    if ignored.returncode != 0 or tracked.returncode == 0:
        raise ProvenanceError("OUTPUT_NOT_SAFELY_IGNORED")
    if os.path.lexists(path):
        raise ProvenanceError("OUTPUT_EXISTS")
    parent_fd = open_root(path.parent)
    try:
        info = os.fstat(parent_fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ProvenanceError("OUTPUT_PARENT_NOT_OWNER_ONLY")
        if os.path.lexists(path):
            raise ProvenanceError("OUTPUT_EXISTS")
        return Destination(path, parent_fd, fingerprint(info))
    except BaseException:
        os.close(parent_fd)
        raise


def _write_temporary(destination: Destination, body: bytes) -> tuple[str, tuple[int, ...]]:
    temporary = f".{destination.path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=destination.parent_fd,
    )
    try:
        offset = 0
        while offset < len(body):
            written = os.write(descriptor, body[offset:])
            if written <= 0:
                raise ProvenanceError("OUTPUT_WRITE_FAILED")
            offset += written
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        if stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != len(body):
            raise ProvenanceError("OUTPUT_WRITE_FAILED")
        return temporary, fingerprint(info)
    finally:
        os.close(descriptor)


def _unlink_if_identity(destination: Destination, name: str, identity: tuple[int, ...]) -> None:
    try:
        info = os.stat(name, dir_fd=destination.parent_fd, follow_symlinks=False)
        if (info.st_dev, info.st_ino) == (identity[0], identity[1]):
            os.unlink(name, dir_fd=destination.parent_fd)
    except FileNotFoundError:
        pass


def _try_unlink_identity(
    destination: Destination, name: str, identity: tuple[int, ...]
) -> bool:
    try:
        _unlink_if_identity(destination, name, identity)
        return True
    except OSError:
        return False


def publish_many(items: list[tuple[Destination, bytes]]) -> None:
    if len({os.fspath(item[0].path) for item in items}) != len(items):
        raise ProvenanceError("DUPLICATE_OUTPUT_DESTINATION")
    staged: list[tuple[Destination, str, tuple[int, ...]]] = []
    published: list[tuple[Destination, tuple[int, ...]]] = []
    try:
        # Validate every retained parent descriptor before our own temporary
        # files intentionally change a shared parent's directory metadata.
        for destination, _ in items:
            if fingerprint(os.fstat(destination.parent_fd)) != destination.parent_fingerprint:
                raise ProvenanceError("OUTPUT_PARENT_CHANGED")
        for destination, body in items:
            name, identity = _write_temporary(destination, body)
            staged.append((destination, name, identity))
        for destination, temporary, identity in staged:
            os.link(
                temporary,
                destination.path.name,
                src_dir_fd=destination.parent_fd,
                dst_dir_fd=destination.parent_fd,
                follow_symlinks=False,
            )
            published.append((destination, identity))
        for destination, temporary, identity in staged:
            _unlink_if_identity(destination, temporary, identity)
        for destination, identity in published:
            reopened = open_root(destination.path.parent)
            try:
                parent = os.fstat(reopened)
                output = os.stat(
                    destination.path.name,
                    dir_fd=reopened,
                    follow_symlinks=False,
                )
                if (
                    (parent.st_dev, parent.st_ino)
                    != (
                        destination.parent_fingerprint[0],
                        destination.parent_fingerprint[1],
                    )
                    or (output.st_dev, output.st_ino) != (identity[0], identity[1])
                    or not stat.S_ISREG(output.st_mode)
                    or stat.S_IMODE(output.st_mode) != 0o600
                ):
                    raise ProvenanceError("OUTPUT_PUBLICATION_FAILED")
            finally:
                os.close(reopened)
        for destination, _ in published:
            os.fsync(destination.parent_fd)
    except BaseException as exc:
        rollback_complete = True
        for destination, identity in reversed(published):
            rollback_complete &= _try_unlink_identity(
                destination, destination.path.name, identity
            )
        for destination, temporary, identity in staged:
            rollback_complete &= _try_unlink_identity(
                destination, temporary, identity
            )
        original = (
            exc
            if isinstance(exc, ProvenanceError)
            else ProvenanceError("OUTPUT_PUBLICATION_FAILED")
        )
        if not rollback_complete:
            raise ProvenanceError("OUTPUT_ROLLBACK_FAILED") from original
        if isinstance(exc, ProvenanceError):
            raise exc
        raise original from exc
    finally:
        for destination, temporary, identity in staged:
            _try_unlink_identity(destination, temporary, identity)


def _validate_sanitized(
    body: bytes,
    *,
    forbidden_paths: list[Path],
) -> None:
    if EMAIL_RE.search(body):
        raise ProvenanceError("SANITIZATION_FAILED")
    forbidden = {
        os.fspath(Path.home()).encode(),
        b"/private/tmp/",
        b"/tmp/",
        *(os.fspath(path).encode() for path in forbidden_paths),
    }
    if any(value and value in body for value in forbidden):
        raise ProvenanceError("SANITIZATION_FAILED")


def capture(
    *,
    minutes_directory: Path,
    attacking_rates_directory: Path,
    digest_receipt: Path,
    repository: Path,
    clock: Callable[[], datetime] = utc_now,
) -> dict[str, Any]:
    if minutes_directory == attacking_rates_directory:
        raise ProvenanceError("DUPLICATE_LOGICAL_SLOT")
    slots = [
        _capture_slot(minutes_directory, MINUTES),
        _capture_slot(attacking_rates_directory, ATTACKING_RATES),
    ]
    receipt = {
        "format_version": FORMAT_VERSION,
        "verifier_version": VERIFIER_VERSION,
        "capture_timestamp": iso_utc(clock()),
        "slots": slots,
    }
    body = canonical_json(receipt)
    destination = validate_destination(digest_receipt, repository)
    try:
        publish_many([(destination, body)])
    finally:
        destination.close()
    return receipt


def verify(
    *,
    minutes_directory: Path,
    minutes_manifest_sha256: str,
    attacking_rates_directory: Path,
    attacking_rates_manifest_sha256: str,
    private_output: Path,
    sanitized_output: Path,
    repository: Path,
    clock: Callable[[], datetime] = utc_now,
) -> dict[str, Any]:
    if minutes_directory == attacking_rates_directory:
        raise ProvenanceError("DUPLICATE_LOGICAL_SLOT")
    private_slots: list[dict[str, Any]] = []
    sanitized_slots: list[dict[str, Any]] = []
    for directory, digest, contract in (
        (minutes_directory, minutes_manifest_sha256, MINUTES),
        (attacking_rates_directory, attacking_rates_manifest_sha256, ATTACKING_RATES),
    ):
        private, sanitized = _verify_slot(directory, digest, contract)
        private_slots.append(private)
        sanitized_slots.append(sanitized)

    timestamp = iso_utc(clock())
    tool_digest = _tool_sha256()
    private_record = {
        "format_version": FORMAT_VERSION,
        "verifier_version": VERIFIER_VERSION,
        "verifier_source_sha256": tool_digest,
        "verification_timestamp": timestamp,
        "combined_status": "LOCALLY_COHERENT_LEGACY_RESULTS",
        "assurance_limitation_code": ASSURANCE_LIMITATION_CODE,
        "assurance_limitation": ASSURANCE_LIMITATION,
        "slots": private_slots,
    }
    private_body = canonical_json(private_record)
    sanitized_record = {
        "format_version": FORMAT_VERSION,
        "verifier_version": VERIFIER_VERSION,
        "verifier_source_sha256": tool_digest,
        "verification_timestamp": timestamp,
        "combined_status": "LOCALLY_COHERENT_LEGACY_RESULTS",
        "assurance_limitation_code": ASSURANCE_LIMITATION_CODE,
        "assurance_limitation": ASSURANCE_LIMITATION,
        "private_record_sha256": sha256_bytes(private_body),
        "private_record_bytes": len(private_body),
        "slots": sanitized_slots,
    }
    sanitized_body = canonical_json(sanitized_record)
    _validate_sanitized(
        sanitized_body,
        forbidden_paths=[
            repository,
            minutes_directory,
            attacking_rates_directory,
            private_output,
            sanitized_output,
        ],
    )

    private_destination = validate_destination(private_output, repository)
    try:
        sanitized_destination = validate_destination(sanitized_output, repository)
        try:
            publish_many(
                [
                    (private_destination, private_body),
                    (sanitized_destination, sanitized_body),
                ]
            )
        finally:
            sanitized_destination.close()
    finally:
        private_destination.close()
    return sanitized_record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--minutes-directory", type=Path, required=True)
    capture_parser.add_argument("--attacking-rates-directory", type=Path, required=True)
    capture_parser.add_argument("--digest-receipt", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--minutes-directory", type=Path, required=True)
    verify_parser.add_argument("--minutes-manifest-sha256", required=True)
    verify_parser.add_argument("--attacking-rates-directory", type=Path, required=True)
    verify_parser.add_argument("--attacking-rates-manifest-sha256", required=True)
    verify_parser.add_argument("--private-output", type=Path, required=True)
    verify_parser.add_argument("--sanitized-output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.mode == "capture":
            result = capture(
                minutes_directory=args.minutes_directory,
                attacking_rates_directory=args.attacking_rates_directory,
                digest_receipt=args.digest_receipt,
                repository=_repo_root(),
            )
            public = {
                "status": "CAPTURE_COMPLETE",
                "receipt_sha256": sha256_bytes(canonical_json(result)),
            }
        else:
            result = verify(
                minutes_directory=args.minutes_directory,
                minutes_manifest_sha256=args.minutes_manifest_sha256,
                attacking_rates_directory=args.attacking_rates_directory,
                attacking_rates_manifest_sha256=args.attacking_rates_manifest_sha256,
                private_output=args.private_output,
                sanitized_output=args.sanitized_output,
                repository=_repo_root(),
            )
            public = {
                "status": result["combined_status"],
                "sanitized_report_sha256": sha256_bytes(canonical_json(result)),
            }
    except ProvenanceError as exc:
        code = exc.code if re.fullmatch(r"[A-Z0-9_]+", exc.code) else "INTERNAL_ERROR"
        print(f"Prior-result provenance operation failed: {code}.", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError):
        print("Prior-result provenance operation failed: IO_OR_GIT_FAILURE.", file=sys.stderr)
        return 1
    except Exception:
        # Never allow an unforeseen exception to emit a traceback containing a
        # private path or parsed value. Tests exercise this last-resort boundary.
        print("Prior-result provenance operation failed: INTERNAL_ERROR.", file=sys.stderr)
        return 1
    print(json.dumps(public, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
