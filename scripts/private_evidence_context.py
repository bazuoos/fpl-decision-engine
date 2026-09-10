#!/usr/bin/env python3
"""Capture private source bytes and immutable human strategy context safely."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
from typing import Any


CONTENT_VERSION = "private-source-content-v1"
EVIDENCE_VERSION = "private-source-evidence-v1"
CONTEXT_VERSION = "human-strategy-context-v1"
COMPARISON_VERSION = "evidence-layer-comparison-v1"
WRITER_VERSION = "task029b-v1"

SOURCE_AUTHORITY = "SOURCE_BYTES_AND_PROVENANCE_ONLY_NOT_ENGINE_INPUT_OR_FPL_ACTION"
CONTEXT_AUTHORITY = "HUMAN_CONTEXT_ONLY_NOT_ENGINE_INPUT_OR_FPL_ACTION"
COMPARISON_AUTHORITY = "LABELED_READ_ONLY_VIEW_NOT_DECISION_AUTHORITY"

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_TEXT_CHARS = 20_000
MAX_SHORT_TEXT_CHARS = 2_000
MAX_LIST_ITEMS = 64
READ_SIZE = 1024 * 1024
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_EVIDENCE_ROOT = REPOSITORY_ROOT / "data" / "evidence"
PRIVATE_CONTEXT_ROOT = REPOSITORY_ROOT / "data" / "context"

SOURCE_CLASSES = {
    "OWNER_SUPPLIED_MANAGER_SCREEN",
    "OWNER_SUPPLIED_DOCUMENT",
    "PUBLIC_RESEARCH_CAPTURE",
}
SENSITIVITIES = {"MANAGER_PRIVATE", "PERSONAL_CONTEXT", "PUBLIC_SOURCE"}
TEMPORAL_CLASSES = {"PROSPECTIVE", "HISTORICAL_BACKFILL"}
CONTEXT_KINDS = {
    "DURABLE_STRATEGY",
    "SQUAD_CONSTRUCTION_THESIS",
    "PLAYER_THESIS_SNAPSHOT",
    "DECISION_RATIONALE_CONTEXT",
    "KNOWN_MODEL_LIMITATION",
    "OPEN_HYPOTHESIS",
}
POSTURES = {"BUY", "HOLD", "SELL", "WATCH", "FUNDING_CANDIDATE"}
RELATIONS = {"SUPPORTS", "CHALLENGES", "SUPERSEDES"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SEMANTIC_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,199}$")
SEASON_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MEDIA_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,62}/[a-z0-9][a-z0-9.+-]{0,62}$")
SECRET_MARKERS = (
    b"AGE-SECRET-KEY-1",
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
)

CONTENT_FIELDS = {
    "format_version",
    "content_id",
    "content_sha256",
    "byte_length",
    "stored_name",
}
EVIDENCE_FIELDS = {
    "schema_version",
    "record_type",
    "evidence_record_id",
    "content_id",
    "content_sha256",
    "byte_length",
    "media_type",
    "source_class",
    "sensitivity",
    "ingested_at",
    "observed_at",
    "observation_basis",
    "owner_reported_observed_at",
    "season",
    "target_gameweek",
    "official_deadline",
    "temporal_status",
    "provenance_description",
    "authority",
    "writer_version",
}
CONTEXT_FIELDS = {
    "schema_version",
    "record_type",
    "context_id",
    "temporal_classification",
    "context_created_at",
    "season",
    "target_gameweek",
    "official_deadline",
    "context_kind",
    "reasoning",
    "assumptions",
    "change_conditions",
    "provisional_posture",
    "evidence_references",
    "engine_references",
    "relations",
    "authority",
    "writer_version",
}
OBSERVATION_INPUT_FIELDS = {
    "source_class",
    "media_type",
    "sensitivity",
    "provenance_description",
    "owner_reported_observed_at",
    "season",
    "target_gameweek",
    "official_deadline",
}
CONTEXT_INPUT_FIELDS = {
    "temporal_classification",
    "season",
    "target_gameweek",
    "official_deadline",
    "context_kind",
    "reasoning",
    "assumptions",
    "change_conditions",
    "provisional_posture",
    "evidence_record_paths",
    "engine_references",
    "relations",
}
ENGINE_REFERENCE_INPUT_FIELDS = {
    "artifact_path",
    "artifact_type",
    "artifact_schema_version",
    "semantic_id",
    "expected_sha256",
}
ENGINE_REFERENCE_FIELDS = {
    "artifact_type",
    "artifact_schema_version",
    "semantic_id",
    "sha256",
}
EVIDENCE_REFERENCE_FIELDS = {
    "evidence_record_id",
    "evidence_record_sha256",
    "content_sha256",
}
RELATION_INPUT_FIELDS = {"relation", "context_path"}
RELATION_FIELDS = {"relation", "context_id", "context_sha256"}


class EvidenceContextError(Exception):
    """Stable fail-closed error that never includes private data or paths."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise EvidenceContextError(code)


def canonical(payload: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _fail("CANONICAL_JSON_INVALID")


def _utc(value: datetime, code: str) -> tuple[datetime, str]:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail(code)
    converted = value.astimezone(timezone.utc)
    return converted, converted.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc(value: Any, code: str) -> tuple[datetime, str]:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail(code)
    converted, normalized = _utc(parsed, code)
    if value != normalized:
        _fail(code)
    return converted, normalized


def _exact_object(value: Any, fields: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return value


def _sha256(value: Any, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _integer(value: Any, code: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(code)
    if maximum is not None and value > maximum:
        _fail(code)
    return value


def _text(value: Any, code: str, *, maximum: int = MAX_TEXT_CHARS, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        _fail(code)
    if any(ord(character) < 0x20 and character not in "\n\t" for character in value):
        _fail(code)
    return value


def _media_type(value: Any, code: str) -> str:
    if not isinstance(value, str) or MEDIA_TYPE_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _text_list(value: Any, code: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_LIST_ITEMS:
        _fail(code)
    return [_text(item, code, maximum=MAX_SHORT_TEXT_CHARS) for item in value]


def _semantic_id(value: Any, prefix: str, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith(prefix)
        or len(value) != len(prefix) + 64
        or SHA256_RE.fullmatch(value[len(prefix) :]) is None
    ):
        _fail(code)
    return value


def _absolute(path: Path, code: str) -> Path:
    try:
        raw = os.fspath(path)
    except TypeError:
        _fail(code)
    if not isinstance(raw, str) or not os.path.isabs(raw) or "\x00" in raw:
        _fail(code)
    if any(part in {".", "..", ""} for part in Path(raw).parts[1:]):
        _fail(code)
    try:
        resolved_parent = os.path.realpath(os.path.dirname(raw), strict=True)
    except OSError:
        _fail(code)
    return Path(resolved_parent) / os.path.basename(raw)


def _fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _open_regular(
    path: Path,
    code: str,
    *,
    maximum: int = MAX_JSON_BYTES,
    require_private: bool = False,
) -> tuple[int, Path, os.stat_result]:
    absolute = _absolute(path, code)
    directory_fd: int | None = None
    file_fd: int | None = None
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        directory_fd = os.open(os.sep, directory_flags)
        for component in absolute.parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            absolute.parts[-1],
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        info = os.fstat(file_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_size > maximum
            or (require_private and stat.S_IMODE(info.st_mode) & 0o077)
        ):
            _fail(code)
        return file_fd, absolute, info
    except EvidenceContextError:
        if file_fd is not None:
            os.close(file_fd)
        raise
    except (OSError, ValueError):
        if file_fd is not None:
            os.close(file_fd)
        _fail(code)
    finally:
        if directory_fd is not None:
            os.close(directory_fd)


def _read_open_file(fd: int, before: os.stat_result, maximum: int, code: str) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            chunk = os.read(fd, min(READ_SIZE, maximum + 1 - total))
        except OSError:
            _fail(code)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            _fail(code)
    try:
        after = os.fstat(fd)
    except OSError:
        _fail(code)
    if _fingerprint(before) != _fingerprint(after) or total != before.st_size:
        _fail("INPUT_CHANGED")
    return b"".join(chunks)


def _read_regular(
    path: Path,
    code: str,
    *,
    maximum: int = MAX_JSON_BYTES,
    require_private: bool = False,
) -> bytes:
    fd, absolute, before = _open_regular(
        path, code, maximum=maximum, require_private=require_private
    )
    try:
        body = _read_open_file(fd, before, maximum, code)
        try:
            named = os.stat(absolute, follow_symlinks=False)
        except OSError:
            _fail("INPUT_CHANGED")
        if _fingerprint(before) != _fingerprint(named):
            _fail("INPUT_CHANGED")
        return body
    finally:
        os.close(fd)


def _hash_regular(
    path: Path, code: str, *, maximum: int, require_private: bool = False
) -> tuple[str, int]:
    fd, absolute, before = _open_regular(
        path, code, maximum=maximum, require_private=require_private
    )
    digest = hashlib.sha256()
    total = 0
    try:
        while True:
            chunk = os.read(fd, READ_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                _fail(code)
            digest.update(chunk)
        after = os.fstat(fd)
        try:
            named = os.stat(absolute, follow_symlinks=False)
        except OSError:
            _fail("INPUT_CHANGED")
        if (
            _fingerprint(before) != _fingerprint(after)
            or _fingerprint(before) != _fingerprint(named)
            or total != before.st_size
        ):
            _fail("INPUT_CHANGED")
        return digest.hexdigest(), total
    except EvidenceContextError:
        raise
    except OSError:
        _fail(code)
    finally:
        os.close(fd)


def _load_json(path: Path, code: str, *, require_private: bool = False) -> Mapping[str, Any]:
    body = _read_regular(path, code, require_private=require_private)
    if any(marker in body for marker in SECRET_MARKERS):
        _fail("SECRET_MARKER_REJECTED")
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        _fail(code)
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _load_canonical_json(path: Path, code: str) -> Mapping[str, Any]:
    body = _read_regular(path, code, require_private=True)
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        _fail(code)
    if not isinstance(value, Mapping) or canonical(value) != body:
        _fail(code)
    return value


def _open_private_root(path: Path) -> tuple[int, Path]:
    absolute = _absolute(path, "PRIVATE_ROOT_INVALID")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    current = os.open(os.sep, directory_flags)
    try:
        for index, component in enumerate(absolute.parts[1:]):
            final = index == len(absolute.parts[1:]) - 1
            try:
                next_fd = os.open(component, directory_flags, dir_fd=current)
            except FileNotFoundError:
                if not final:
                    _fail("PRIVATE_ROOT_INVALID")
                try:
                    os.mkdir(component, 0o700, dir_fd=current)
                    os.fsync(current)
                    next_fd = os.open(component, directory_flags, dir_fd=current)
                except OSError:
                    _fail("PRIVATE_ROOT_INVALID")
            os.close(current)
            current = next_fd
        info = os.fstat(current)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            _fail("PRIVATE_ROOT_REQUIRED")
        return current, absolute
    except Exception:
        os.close(current)
        raise


def _require_storage_root(path: Path, required_root: Path, code: str) -> None:
    try:
        parent_info = os.lstat(required_root.parent)
    except OSError:
        _fail(code)
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != os.getuid()
    ):
        _fail(code)
    if _absolute(path, code) != _absolute(required_root, code):
        _fail(code)


def _require_private_directories(paths: Sequence[Path], code: str) -> None:
    for path in paths:
        try:
            info = os.lstat(path)
        except OSError:
            _fail(code)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            _fail(code)


def _open_or_create_directory(parent_fd: int, name: str) -> tuple[int, bool]:
    if not name or "/" in name or name in {".", ".."}:
        _fail("STORAGE_LAYOUT_INVALID")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    created = False
    try:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            os.fsync(parent_fd)
            created = True
        except FileExistsError:
            pass
        fd = os.open(name, flags, dir_fd=parent_fd)
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            os.close(fd)
            _fail("PRIVATE_DIRECTORY_REQUIRED")
        return fd, created
    except EvidenceContextError:
        raise
    except OSError:
        _fail("STORAGE_LAYOUT_INVALID")


def _directory_chain(root_fd: int, names: Sequence[str]) -> tuple[int, list[bool]]:
    current = os.dup(root_fd)
    created: list[bool] = []
    try:
        for name in names:
            nested, was_created = _open_or_create_directory(current, name)
            os.close(current)
            current = nested
            created.append(was_created)
        return current, created
    except Exception:
        os.close(current)
        raise


def _write_stage_file(directory_fd: int, name: str, body: bytes) -> None:
    try:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        with os.fdopen(fd, "wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        _fail("PRIVATE_PUBLICATION_FAILED")


def _link_stage_file(stage_fd: int, stage_name: str, destination_fd: int, name: str) -> None:
    try:
        os.link(
            stage_name,
            name,
            src_dir_fd=stage_fd,
            dst_dir_fd=destination_fd,
            follow_symlinks=False,
        )
        os.unlink(stage_name, dir_fd=stage_fd)
        os.fsync(destination_fd)
    except OSError:
        _fail("IMMUTABLE_CONFLICT")


def _read_at(directory_fd: int, name: str, code: str, *, maximum: int = MAX_JSON_BYTES) -> bytes:
    fd: int | None = None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o077
            or before.st_size > maximum
        ):
            _fail(code)
        body = _read_open_file(fd, before, maximum, code)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if _fingerprint(before) != _fingerprint(named):
            _fail("INPUT_CHANGED")
        return body
    except EvidenceContextError:
        raise
    except OSError:
        _fail(code)
    finally:
        if fd is not None:
            os.close(fd)


def _hash_at(directory_fd: int, name: str, code: str, *, maximum: int) -> tuple[str, int]:
    fd: int | None = None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o077
            or before.st_size > maximum
        ):
            _fail(code)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, READ_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                _fail(code)
            digest.update(chunk)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            _fingerprint(before) != _fingerprint(after)
            or _fingerprint(before) != _fingerprint(named)
            or total != before.st_size
        ):
            _fail("INPUT_CHANGED")
        return digest.hexdigest(), total
    except EvidenceContextError:
        raise
    except OSError:
        _fail(code)
    finally:
        if fd is not None:
            os.close(fd)


def _json_at(directory_fd: int, name: str, code: str) -> Mapping[str, Any]:
    body = _read_at(directory_fd, name, code)
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        _fail(code)
    if not isinstance(value, Mapping) or canonical(value) != body:
        _fail(code)
    return value


def _cleanup_stage(root_fd: int, stage_fd: int | None, stage_name: str) -> None:
    if stage_fd is None:
        return
    try:
        try:
            for name in os.listdir(stage_fd):
                try:
                    os.unlink(name, dir_fd=stage_fd)
                except OSError:
                    pass
        finally:
            os.close(stage_fd)
        try:
            os.rmdir(stage_name, dir_fd=root_fd)
        except OSError:
            pass
    except OSError:
        pass


def _new_stage(root_fd: int) -> tuple[int, str]:
    name = ".task029b-stage-" + uuid.uuid4().hex
    try:
        os.mkdir(name, 0o700, dir_fd=root_fd)
        os.fsync(root_fd)
        fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=root_fd,
        )
        return fd, name
    except OSError:
        _fail("PRIVATE_PUBLICATION_FAILED")


def _validate_scope(
    season: Any,
    gameweek: Any,
    deadline: Any,
    *,
    required: bool,
) -> tuple[str | None, int | None, str | None, datetime | None]:
    supplied = (season is not None, gameweek is not None, deadline is not None)
    if required and not all(supplied):
        _fail("COMPLETE_SCOPE_REQUIRED")
    if any(supplied) and not all(supplied):
        _fail("PARTIAL_SCOPE_REJECTED")
    if not any(supplied):
        return None, None, None, None
    if not isinstance(season, str) or SEASON_RE.fullmatch(season) is None:
        _fail("SEASON_INVALID")
    gameweek_value = _integer(gameweek, "GAMEWEEK_INVALID", minimum=1, maximum=38)
    deadline_dt, deadline_value = _parse_utc(deadline, "DEADLINE_INVALID")
    return season, gameweek_value, deadline_value, deadline_dt


def _identity(prefix: str, payload_without_id: Mapping[str, Any]) -> str:
    return prefix + hashlib.sha256(canonical(payload_without_id)).hexdigest()


def validate_content_manifest(payload: Mapping[str, Any]) -> dict[str, Any]:
    source = _exact_object(payload, CONTENT_FIELDS, "CONTENT_MANIFEST_INVALID")
    digest = _sha256(source.get("content_sha256"), "CONTENT_MANIFEST_INVALID")
    if (
        source.get("format_version") != CONTENT_VERSION
        or source.get("content_id") != "source_" + digest
        or source.get("stored_name") != "source.bin"
    ):
        _fail("CONTENT_MANIFEST_INVALID")
    _integer(source.get("byte_length"), "CONTENT_MANIFEST_INVALID", maximum=MAX_SOURCE_BYTES)
    return dict(source)


def validate_source_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    source = _exact_object(payload, EVIDENCE_FIELDS, "SOURCE_EVIDENCE_INVALID")
    if (
        source.get("schema_version") != EVIDENCE_VERSION
        or source.get("record_type") != "SOURCE_EVIDENCE_OBSERVATION"
        or source.get("writer_version") != WRITER_VERSION
        or source.get("authority") != SOURCE_AUTHORITY
        or source.get("source_class") not in SOURCE_CLASSES
        or source.get("sensitivity") not in SENSITIVITIES
    ):
        _fail("SOURCE_EVIDENCE_INVALID")
    digest = _sha256(source.get("content_sha256"), "SOURCE_EVIDENCE_INVALID")
    _media_type(source.get("media_type"), "SOURCE_EVIDENCE_INVALID")
    if source.get("content_id") != "source_" + digest:
        _fail("SOURCE_EVIDENCE_INVALID")
    _integer(source.get("byte_length"), "SOURCE_EVIDENCE_INVALID", maximum=MAX_SOURCE_BYTES)
    _parse_utc(source.get("ingested_at"), "SOURCE_EVIDENCE_INVALID")
    if (
        source.get("observed_at") is not None
        or source.get("observation_basis") != "NOT_SYSTEM_PROVEN"
        or source.get("temporal_status") != "UNKNOWN"
    ):
        _fail("SOURCE_EVIDENCE_INVALID")
    owner_time = source.get("owner_reported_observed_at")
    if owner_time is not None:
        _parse_utc(owner_time, "SOURCE_EVIDENCE_INVALID")
    _validate_scope(
        source.get("season"),
        source.get("target_gameweek"),
        source.get("official_deadline"),
        required=False,
    )
    _text(
        source.get("provenance_description"),
        "SOURCE_EVIDENCE_INVALID",
        maximum=MAX_SHORT_TEXT_CHARS,
    )
    without_id = {key: value for key, value in source.items() if key != "evidence_record_id"}
    if source.get("evidence_record_id") != _identity("evidence_", without_id):
        _fail("SOURCE_EVIDENCE_INVALID")
    return dict(source)


def _validate_engine_reference(value: Any) -> dict[str, Any]:
    source = _exact_object(value, ENGINE_REFERENCE_FIELDS, "ENGINE_REFERENCE_INVALID")
    result = {
        "artifact_type": _text(
            source.get("artifact_type"), "ENGINE_REFERENCE_INVALID", maximum=200
        ),
        "artifact_schema_version": _text(
            source.get("artifact_schema_version"), "ENGINE_REFERENCE_INVALID", maximum=200
        ),
        "semantic_id": _text(source.get("semantic_id"), "ENGINE_REFERENCE_INVALID", maximum=200),
        "sha256": _sha256(source.get("sha256"), "ENGINE_REFERENCE_INVALID"),
    }
    if SEMANTIC_ID_RE.fullmatch(result["semantic_id"]) is None:
        _fail("ENGINE_REFERENCE_INVALID")
    return result


def _validate_evidence_reference(value: Any) -> dict[str, Any]:
    source = _exact_object(value, EVIDENCE_REFERENCE_FIELDS, "EVIDENCE_REFERENCE_INVALID")
    return {
        "evidence_record_id": _semantic_id(
            source.get("evidence_record_id"), "evidence_", "EVIDENCE_REFERENCE_INVALID"
        ),
        "evidence_record_sha256": _sha256(
            source.get("evidence_record_sha256"), "EVIDENCE_REFERENCE_INVALID"
        ),
        "content_sha256": _sha256(source.get("content_sha256"), "EVIDENCE_REFERENCE_INVALID"),
    }


def _validate_relation(value: Any) -> dict[str, Any]:
    source = _exact_object(value, RELATION_FIELDS, "CONTEXT_RELATION_INVALID")
    if source.get("relation") not in RELATIONS:
        _fail("CONTEXT_RELATION_INVALID")
    return {
        "relation": source["relation"],
        "context_id": _semantic_id(
            source.get("context_id"), "context_", "CONTEXT_RELATION_INVALID"
        ),
        "context_sha256": _sha256(source.get("context_sha256"), "CONTEXT_RELATION_INVALID"),
    }


def validate_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    source = _exact_object(payload, CONTEXT_FIELDS, "CONTEXT_INVALID")
    if (
        source.get("schema_version") != CONTEXT_VERSION
        or source.get("record_type") != "HUMAN_STRATEGY_CONTEXT"
        or source.get("writer_version") != WRITER_VERSION
        or source.get("authority") != CONTEXT_AUTHORITY
        or source.get("temporal_classification") not in TEMPORAL_CLASSES
        or source.get("context_kind") not in CONTEXT_KINDS
        or source.get("provisional_posture") not in POSTURES | {None}
    ):
        _fail("CONTEXT_INVALID")
    created_dt, _ = _parse_utc(source.get("context_created_at"), "CONTEXT_INVALID")
    season, gameweek, deadline, deadline_dt = _validate_scope(
        source.get("season"),
        source.get("target_gameweek"),
        source.get("official_deadline"),
        required=source["temporal_classification"] == "PROSPECTIVE",
    )
    if source["temporal_classification"] == "PROSPECTIVE":
        assert deadline_dt is not None
        if created_dt >= deadline_dt:
            _fail("PROSPECTIVE_DEADLINE_REJECTED")
    _text(source.get("reasoning"), "CONTEXT_INVALID")
    _text_list(source.get("assumptions"), "CONTEXT_INVALID")
    _text_list(source.get("change_conditions"), "CONTEXT_INVALID")
    evidence = source.get("evidence_references")
    engines = source.get("engine_references")
    relations = source.get("relations")
    if not isinstance(evidence, list) or len(evidence) > MAX_LIST_ITEMS:
        _fail("CONTEXT_INVALID")
    if not isinstance(engines, list) or len(engines) > MAX_LIST_ITEMS:
        _fail("CONTEXT_INVALID")
    if not isinstance(relations, list) or len(relations) > MAX_LIST_ITEMS:
        _fail("CONTEXT_INVALID")
    normalized_evidence = [_validate_evidence_reference(item) for item in evidence]
    normalized_engines = [_validate_engine_reference(item) for item in engines]
    normalized_relations = [_validate_relation(item) for item in relations]
    for keys, code in (
        (
            [row["evidence_record_id"] for row in normalized_evidence],
            "DUPLICATE_EVIDENCE_REFERENCE",
        ),
        (
            [row["semantic_id"] for row in normalized_engines],
            "DUPLICATE_ENGINE_REFERENCE",
        ),
        (
            [row["context_id"] for row in normalized_relations],
            "DUPLICATE_CONTEXT_RELATION",
        ),
    ):
        if len(keys) != len(set(keys)):
            _fail(code)
    without_id = {key: value for key, value in source.items() if key != "context_id"}
    if source.get("context_id") != _identity("context_", without_id):
        _fail("CONTEXT_INVALID")
    result = dict(source)
    result["season"] = season
    result["target_gameweek"] = gameweek
    result["official_deadline"] = deadline
    return result


def _copy_source_to_stage(source: Path, stage_fd: int) -> tuple[str, int]:
    source_fd, absolute, before = _open_regular(source, "SOURCE_INVALID", maximum=MAX_SOURCE_BYTES)
    output_fd: int | None = None
    digest = hashlib.sha256()
    copied = 0
    try:
        output_fd = os.open(
            "source.bin",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=stage_fd,
        )
        with os.fdopen(output_fd, "wb") as output:
            output_fd = None
            while True:
                chunk = os.read(source_fd, READ_SIZE)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > MAX_SOURCE_BYTES:
                    _fail("SOURCE_INVALID")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        after = os.fstat(source_fd)
        try:
            named = os.stat(absolute, follow_symlinks=False)
        except OSError:
            _fail("INPUT_CHANGED")
        if (
            _fingerprint(before) != _fingerprint(after)
            or _fingerprint(before) != _fingerprint(named)
            or copied != before.st_size
        ):
            _fail("INPUT_CHANGED")
        return digest.hexdigest(), copied
    except EvidenceContextError:
        raise
    except OSError:
        _fail("SOURCE_CAPTURE_FAILED")
    finally:
        if output_fd is not None:
            os.close(output_fd)
        os.close(source_fd)


def _validate_content_directory(content_fd: int, digest: str, byte_length: int) -> None:
    try:
        if set(os.listdir(content_fd)) != {"source.bin", "content_manifest.json", "observations"}:
            _fail("CONTENT_STORE_INVALID")
    except OSError:
        _fail("CONTENT_STORE_INVALID")
    manifest = validate_content_manifest(
        _json_at(content_fd, "content_manifest.json", "CONTENT_STORE_INVALID")
    )
    if manifest["content_sha256"] != digest or manifest["byte_length"] != byte_length:
        _fail("CONTENT_STORE_INVALID")
    stored_digest, stored_length = _hash_at(
        content_fd, "source.bin", "CONTENT_STORE_INVALID", maximum=MAX_SOURCE_BYTES
    )
    if stored_length != byte_length or stored_digest != digest:
        _fail("CONTENT_STORE_INVALID")


def capture_source(
    *,
    source: Path,
    evidence_root: Path,
    observation_input: Path,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    _require_storage_root(
        evidence_root, PRIVATE_EVIDENCE_ROOT, "EVIDENCE_ROOT_OUTSIDE_DATA"
    )
    request = _exact_object(
        _load_json(
            observation_input,
            "OBSERVATION_INPUT_INVALID",
            require_private=True,
        ),
        OBSERVATION_INPUT_FIELDS,
        "OBSERVATION_INPUT_INVALID",
    )
    if (
        request.get("source_class") not in SOURCE_CLASSES
        or request.get("sensitivity") not in SENSITIVITIES
    ):
        _fail("OBSERVATION_INPUT_INVALID")
    media_type = _media_type(request.get("media_type"), "OBSERVATION_INPUT_INVALID")
    description = _text(
        request.get("provenance_description"),
        "OBSERVATION_INPUT_INVALID",
        maximum=MAX_SHORT_TEXT_CHARS,
    )
    owner_reported = request.get("owner_reported_observed_at")
    if owner_reported is not None:
        _, owner_reported = _parse_utc(owner_reported, "OBSERVATION_INPUT_INVALID")
    season, gameweek, deadline, _ = _validate_scope(
        request.get("season"),
        request.get("target_gameweek"),
        request.get("official_deadline"),
        required=False,
    )
    _, ingested_at = _utc(clock(), "CLOCK_INVALID")

    root_fd, _ = _open_private_root(evidence_root)
    stage_fd: int | None = None
    stage_name = ""
    content_fd: int | None = None
    observations_fd: int | None = None
    record_fd: int | None = None
    try:
        stage_fd, stage_name = _new_stage(root_fd)
        digest, byte_length = _copy_source_to_stage(source, stage_fd)
        content_manifest = {
            "format_version": CONTENT_VERSION,
            "content_id": "source_" + digest,
            "content_sha256": digest,
            "byte_length": byte_length,
            "stored_name": "source.bin",
        }
        validate_content_manifest(content_manifest)
        _write_stage_file(stage_fd, "content_manifest.json", canonical(content_manifest))
        staged_manifest = validate_content_manifest(
            _json_at(stage_fd, "content_manifest.json", "SOURCE_CAPTURE_FAILED")
        )
        staged_digest, staged_length = _hash_at(
            stage_fd,
            "source.bin",
            "SOURCE_CAPTURE_FAILED",
            maximum=MAX_SOURCE_BYTES,
        )
        if (
            staged_manifest != content_manifest
            or staged_digest != digest
            or staged_length != byte_length
        ):
            _fail("SOURCE_CAPTURE_FAILED")

        content_fd, created = _directory_chain(
            root_fd, ("source-v1", "sha256", digest[:2], digest)
        )
        if created[-1]:
            observations_fd, _ = _open_or_create_directory(content_fd, "observations")
            _link_stage_file(stage_fd, "source.bin", content_fd, "source.bin")
            _link_stage_file(
                stage_fd, "content_manifest.json", content_fd, "content_manifest.json"
            )
        else:
            _validate_content_directory(content_fd, digest, byte_length)
            os.unlink("source.bin", dir_fd=stage_fd)
            os.unlink("content_manifest.json", dir_fd=stage_fd)
            observations_fd, observation_created = _open_or_create_directory(
                content_fd, "observations"
            )
            if observation_created:
                _fail("CONTENT_STORE_INVALID")

        payload_without_id = {
            "schema_version": EVIDENCE_VERSION,
            "record_type": "SOURCE_EVIDENCE_OBSERVATION",
            "content_id": "source_" + digest,
            "content_sha256": digest,
            "byte_length": byte_length,
            "media_type": media_type,
            "source_class": request["source_class"],
            "sensitivity": request["sensitivity"],
            "ingested_at": ingested_at,
            "observed_at": None,
            "observation_basis": "NOT_SYSTEM_PROVEN",
            "owner_reported_observed_at": owner_reported,
            "season": season,
            "target_gameweek": gameweek,
            "official_deadline": deadline,
            "temporal_status": "UNKNOWN",
            "provenance_description": description,
            "authority": SOURCE_AUTHORITY,
            "writer_version": WRITER_VERSION,
        }
        evidence_id = _identity("evidence_", payload_without_id)
        payload = {"evidence_record_id": evidence_id, **payload_without_id}
        validate_source_evidence(payload)
        body = canonical(payload)
        _write_stage_file(stage_fd, "source_evidence.json", body)
        assert observations_fd is not None
        record_fd, record_created = _open_or_create_directory(observations_fd, evidence_id)
        if record_created:
            _link_stage_file(stage_fd, "source_evidence.json", record_fd, "source_evidence.json")
        else:
            existing = _read_at(record_fd, "source_evidence.json", "SOURCE_EVIDENCE_INVALID")
            if existing != body or set(os.listdir(record_fd)) != {"source_evidence.json"}:
                _fail("IMMUTABLE_CONFLICT")
            os.unlink("source_evidence.json", dir_fd=stage_fd)
        published_record = _read_at(
            record_fd, "source_evidence.json", "SOURCE_EVIDENCE_INVALID"
        )
        if published_record != body:
            _fail("IMMUTABLE_CONFLICT")
        validate_source_evidence(json.loads(published_record))
        _validate_content_directory(content_fd, digest, byte_length)
        return {
            "status": "CAPTURED",
            "evidence_record_id": evidence_id,
            "content_sha256": digest,
            "byte_length": byte_length,
            "reused_content": not created[-1],
            "reused_record": not record_created,
        }
    finally:
        for fd in (record_fd, observations_fd, content_fd):
            if fd is not None:
                os.close(fd)
        _cleanup_stage(root_fd, stage_fd, stage_name)
        os.close(root_fd)


def verify_source_record(record_path: Path) -> dict[str, Any]:
    absolute = _absolute(record_path, "SOURCE_RECORD_PATH_INVALID")
    if (
        len(absolute.parents) < 7
        or absolute.name != "source_evidence.json"
        or absolute.parent.parent.name != "observations"
    ):
        _fail("SOURCE_RECORD_PATH_INVALID")
    payload = validate_source_evidence(
        _load_canonical_json(absolute, "SOURCE_EVIDENCE_INVALID")
    )
    if absolute.parent.name != payload["evidence_record_id"]:
        _fail("SOURCE_RECORD_PATH_INVALID")
    content_directory = absolute.parents[2]
    digest = payload["content_sha256"]
    if (
        content_directory.name != digest
        or content_directory.parent.name != digest[:2]
        or content_directory.parent.parent.name != "sha256"
        or content_directory.parent.parent.parent.name != "source-v1"
    ):
        _fail("SOURCE_RECORD_PATH_INVALID")
    evidence_root = content_directory.parents[3]
    _require_storage_root(
        evidence_root,
        PRIVATE_EVIDENCE_ROOT,
        "SOURCE_RECORD_PATH_INVALID",
    )
    _require_private_directories(
        (
            evidence_root,
            content_directory.parents[2],
            content_directory.parents[1],
            content_directory.parent,
            content_directory,
            absolute.parent.parent,
            absolute.parent,
        ),
        "PRIVATE_DIRECTORY_REQUIRED",
    )
    try:
        if set(os.listdir(content_directory)) != {
            "source.bin",
            "content_manifest.json",
            "observations",
        } or set(os.listdir(absolute.parent)) != {"source_evidence.json"}:
            _fail("CONTENT_STORE_INVALID")
    except OSError:
        _fail("CONTENT_STORE_INVALID")
    content = validate_content_manifest(
        _load_canonical_json(
            content_directory / "content_manifest.json", "CONTENT_STORE_INVALID"
        )
    )
    stored_digest, total = _hash_regular(
        content_directory / "source.bin",
        "CONTENT_STORE_INVALID",
        maximum=MAX_SOURCE_BYTES,
        require_private=True,
    )
    if (
        content["content_sha256"] != digest
        or content["byte_length"] != payload["byte_length"]
        or total != payload["byte_length"]
        or stored_digest != digest
    ):
        _fail("CONTENT_STORE_INVALID")
    return payload


def verify_manager_binding(*, record_path: Path, manager_evidence_path: Path) -> dict[str, Any]:
    evidence = verify_source_record(record_path)
    manager = _load_json(
        manager_evidence_path,
        "MANAGER_EVIDENCE_INVALID",
        require_private=True,
    )
    declared = manager.get("evidence_source_sha256")
    if declared is None:
        _fail("MANAGER_SOURCE_HASH_ABSENT")
    digest = _sha256(declared, "MANAGER_EVIDENCE_INVALID")
    if digest != evidence["content_sha256"]:
        _fail("MANAGER_SOURCE_HASH_MISMATCH")
    return {"status": "MATCH", "content_sha256": digest}


def _verified_engine_reference(value: Any) -> dict[str, Any]:
    source = _exact_object(value, ENGINE_REFERENCE_INPUT_FIELDS, "ENGINE_REFERENCE_INPUT_INVALID")
    expected = _sha256(source.get("expected_sha256"), "ENGINE_REFERENCE_INPUT_INVALID")
    body = _read_regular(
        Path(_text(source.get("artifact_path"), "ENGINE_REFERENCE_INPUT_INVALID", maximum=4096)),
        "ENGINE_ARTIFACT_INVALID",
        maximum=MAX_SOURCE_BYTES,
    )
    if hashlib.sha256(body).hexdigest() != expected:
        _fail("ENGINE_ARTIFACT_HASH_MISMATCH")
    return _validate_engine_reference(
        {
            "artifact_type": source.get("artifact_type"),
            "artifact_schema_version": source.get("artifact_schema_version"),
            "semantic_id": source.get("semantic_id"),
            "sha256": expected,
        }
    )


def verify_context_record(record_path: Path) -> dict[str, Any]:
    absolute = _absolute(record_path, "CONTEXT_PATH_INVALID")
    if (
        len(absolute.parents) < 5
        or absolute.name != "context.json"
        or absolute.parent.parent.name != "records"
        or absolute.parents[3].name != "fpl"
    ):
        _fail("CONTEXT_PATH_INVALID")
    payload = validate_context(_load_canonical_json(absolute, "CONTEXT_INVALID"))
    if absolute.parent.name != payload["context_id"]:
        _fail("CONTEXT_PATH_INVALID")
    scope = payload["season"] if payload["season"] is not None else "global"
    if absolute.parents[2].name != scope:
        _fail("CONTEXT_PATH_INVALID")
    _require_storage_root(
        absolute.parents[4],
        PRIVATE_CONTEXT_ROOT,
        "CONTEXT_PATH_INVALID",
    )
    _require_private_directories(
        (
            absolute.parents[4],
            absolute.parents[3],
            absolute.parents[2],
            absolute.parents[1],
            absolute.parent,
        ),
        "PRIVATE_DIRECTORY_REQUIRED",
    )
    try:
        if set(os.listdir(absolute.parent)) != {"context.json"}:
            _fail("CONTEXT_INVALID")
    except OSError:
        _fail("CONTEXT_INVALID")
    return payload


def _verified_evidence_reference(path_value: Any) -> dict[str, Any]:
    path = Path(_text(path_value, "EVIDENCE_REFERENCE_INPUT_INVALID", maximum=4096))
    payload = verify_source_record(path)
    return {
        "evidence_record_id": payload["evidence_record_id"],
        "evidence_record_sha256": hashlib.sha256(canonical(payload)).hexdigest(),
        "content_sha256": payload["content_sha256"],
    }


def _verified_relation(value: Any) -> dict[str, Any]:
    source = _exact_object(value, RELATION_INPUT_FIELDS, "CONTEXT_RELATION_INPUT_INVALID")
    if source.get("relation") not in RELATIONS:
        _fail("CONTEXT_RELATION_INPUT_INVALID")
    path = Path(_text(source.get("context_path"), "CONTEXT_RELATION_INPUT_INVALID", maximum=4096))
    payload = verify_context_record(path)
    return {
        "relation": source["relation"],
        "context_id": payload["context_id"],
        "context_sha256": hashlib.sha256(canonical(payload)).hexdigest(),
    }


def create_context(
    *,
    context_root: Path,
    context_input: Path,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    _require_storage_root(context_root, PRIVATE_CONTEXT_ROOT, "CONTEXT_ROOT_OUTSIDE_DATA")
    request = _exact_object(
        _load_json(
            context_input,
            "CONTEXT_INPUT_INVALID",
            require_private=True,
        ),
        CONTEXT_INPUT_FIELDS,
        "CONTEXT_INPUT_INVALID",
    )
    classification = request.get("temporal_classification")
    if classification not in TEMPORAL_CLASSES or request.get("context_kind") not in CONTEXT_KINDS:
        _fail("CONTEXT_INPUT_INVALID")
    posture = request.get("provisional_posture")
    if posture not in POSTURES | {None}:
        _fail("CONTEXT_INPUT_INVALID")
    season, gameweek, deadline, deadline_dt = _validate_scope(
        request.get("season"),
        request.get("target_gameweek"),
        request.get("official_deadline"),
        required=classification == "PROSPECTIVE",
    )
    now_dt, created_at = _utc(clock(), "CLOCK_INVALID")
    if classification == "PROSPECTIVE":
        assert deadline_dt is not None
        if now_dt >= deadline_dt:
            _fail("PROSPECTIVE_DEADLINE_REJECTED")

    evidence_paths = request.get("evidence_record_paths")
    engine_inputs = request.get("engine_references")
    relation_inputs = request.get("relations")
    if not isinstance(evidence_paths, list) or len(evidence_paths) > MAX_LIST_ITEMS:
        _fail("CONTEXT_INPUT_INVALID")
    if not isinstance(engine_inputs, list) or len(engine_inputs) > MAX_LIST_ITEMS:
        _fail("CONTEXT_INPUT_INVALID")
    if not isinstance(relation_inputs, list) or len(relation_inputs) > MAX_LIST_ITEMS:
        _fail("CONTEXT_INPUT_INVALID")

    evidence = [_verified_evidence_reference(value) for value in evidence_paths]
    engines = [_verified_engine_reference(value) for value in engine_inputs]
    relations = [_verified_relation(value) for value in relation_inputs]
    payload_without_id = {
        "schema_version": CONTEXT_VERSION,
        "record_type": "HUMAN_STRATEGY_CONTEXT",
        "temporal_classification": classification,
        "context_created_at": created_at,
        "season": season,
        "target_gameweek": gameweek,
        "official_deadline": deadline,
        "context_kind": request["context_kind"],
        "reasoning": _text(request.get("reasoning"), "CONTEXT_INPUT_INVALID"),
        "assumptions": _text_list(request.get("assumptions"), "CONTEXT_INPUT_INVALID"),
        "change_conditions": _text_list(
            request.get("change_conditions"), "CONTEXT_INPUT_INVALID"
        ),
        "provisional_posture": posture,
        "evidence_references": evidence,
        "engine_references": engines,
        "relations": relations,
        "authority": CONTEXT_AUTHORITY,
        "writer_version": WRITER_VERSION,
    }
    context_id = _identity("context_", payload_without_id)
    payload = {"context_id": context_id, **payload_without_id}
    validate_context(payload)
    body = canonical(payload)

    root_fd, _ = _open_private_root(context_root)
    stage_fd: int | None = None
    stage_name = ""
    record_fd: int | None = None
    records_fd: int | None = None
    try:
        stage_fd, stage_name = _new_stage(root_fd)
        _write_stage_file(stage_fd, "context.json", body)
        if validate_context(_json_at(stage_fd, "context.json", "CONTEXT_INVALID")) != payload:
            _fail("CONTEXT_INVALID")
        scope = season if season is not None else "global"
        records_fd, _ = _directory_chain(root_fd, ("fpl", scope, "records"))
        record_fd, created = _open_or_create_directory(records_fd, context_id)
        if created:
            _link_stage_file(stage_fd, "context.json", record_fd, "context.json")
        else:
            existing = _read_at(record_fd, "context.json", "CONTEXT_INVALID")
            if existing != body or set(os.listdir(record_fd)) != {"context.json"}:
                _fail("IMMUTABLE_CONFLICT")
            os.unlink("context.json", dir_fd=stage_fd)
        published_record = _read_at(record_fd, "context.json", "CONTEXT_INVALID")
        if published_record != body:
            _fail("IMMUTABLE_CONFLICT")
        validate_context(json.loads(published_record))
        return {
            "status": "CONTEXT_CREATED",
            "context_id": context_id,
            "context_sha256": hashlib.sha256(body).hexdigest(),
            "reused": not created,
        }
    finally:
        for fd in (record_fd, records_fd):
            if fd is not None:
                os.close(fd)
        _cleanup_stage(root_fd, stage_fd, stage_name)
        os.close(root_fd)


def build_comparison_view(
    *,
    historical_context_paths: Sequence[Path],
    trusted_engine_references: Sequence[Mapping[str, Any]],
    current_evidence_paths: Sequence[Path],
) -> dict[str, Any]:
    """Return three explicitly labeled layers without constructing a decision."""
    return {
        "view_version": COMPARISON_VERSION,
        "historical_human_thesis_research": {
            "authority": CONTEXT_AUTHORITY,
            "records": [verify_context_record(path) for path in historical_context_paths],
        },
        "trusted_engine_artifacts": {
            "validation_status": "HASH_REFERENCES_ONLY_USE_TRUSTED_ENGINE_READER_FOR_SEMANTICS",
            "records": [
                _verified_engine_reference(value) for value in trusted_engine_references
            ],
        },
        "current_refreshed_football_manager_evidence": {
            "freshness_status": "NOT_ESTABLISHED_BY_TASK029B",
            "records": [verify_source_record(path) for path in current_evidence_paths],
        },
        "authority": COMPARISON_AUTHORITY,
    }


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise EvidenceContextError("INVALID_ARGUMENTS")


def _parser() -> argparse.ArgumentParser:
    parser = _SafeParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=_SafeParser)

    capture = subparsers.add_parser("capture-source")
    capture.add_argument("--source", required=True, type=Path)
    capture.add_argument("--evidence-root", required=True, type=Path)
    capture.add_argument("--observation-input", required=True, type=Path)

    verify_source = subparsers.add_parser("verify-source")
    verify_source.add_argument("--record", required=True, type=Path)

    binding = subparsers.add_parser("verify-manager-binding")
    binding.add_argument("--record", required=True, type=Path)
    binding.add_argument("--manager-evidence", required=True, type=Path)

    context = subparsers.add_parser("create-context")
    context.add_argument("--context-root", required=True, type=Path)
    context.add_argument("--input", required=True, type=Path)

    verify_context = subparsers.add_parser("verify-context")
    verify_context.add_argument("--record", required=True, type=Path)
    return parser


def _safe_summary(command: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if command == "verify-source":
        return {
            "status": "VERIFIED",
            "evidence_record_id": payload["evidence_record_id"],
            "content_sha256": payload["content_sha256"],
            "byte_length": payload["byte_length"],
        }
    if command == "verify-context":
        body = canonical(payload)
        return {
            "status": "VERIFIED",
            "context_id": payload["context_id"],
            "context_sha256": hashlib.sha256(body).hexdigest(),
        }
    return dict(payload)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "capture-source":
            result = capture_source(
                source=args.source,
                evidence_root=args.evidence_root,
                observation_input=args.observation_input,
            )
        elif args.command == "verify-source":
            result = _safe_summary(args.command, verify_source_record(args.record))
        elif args.command == "verify-manager-binding":
            result = verify_manager_binding(
                record_path=args.record, manager_evidence_path=args.manager_evidence
            )
        elif args.command == "create-context":
            result = create_context(context_root=args.context_root, context_input=args.input)
        else:
            result = _safe_summary(args.command, verify_context_record(args.record))
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except (EvidenceContextError, OSError, ValueError, TypeError):
        error = sys.exc_info()[1]
        code = error.code if isinstance(error, EvidenceContextError) else "UNEXPECTED_FAILURE"
        print(json.dumps({"status": "ERROR", "code": code}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
