"""Fail-closed local notifications for one exact Task028F schedule.

The observer is network-inert.  Importing it, preparing a plan, and verifying a
plan have no launchd or notification side effects.  Production lifecycle and
notification effects require explicit commands and exact absolute executables.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import pwd
import re
import stat
import subprocess
import sys
from typing import Callable, Sequence
import uuid


FORMAT = "task034b-local-completion-notification-v1"
SOURCE_FORMAT = "task028f-production-completion-monitor-schedule-v1"
SCRIPT_RELATIVE = Path("scripts/completion_monitor_local_notification.py")
SOURCE_SCRIPT_RELATIVE = Path("scripts/completion_monitor_production_schedule.py")
PARENT_RELATIVE = Path("data/operations/completion-monitor-notifications")
PLAN_NAME = "observer-plan.json"
PLIST_NAME = "launch-agent.plist"
LOCK_NAME = "observer.lock"
ATTEMPT_DIRECTORY = "attempts"
LIFECYCLE_DIRECTORY = "lifecycle-events"
NOTIFIED_NAME = "notified.json"
LABEL_PREFIX = "com.fpl-decision-engine.completion-notification"
INTERVAL_SECONDS = 900
MAX_PLAN_LIFETIME = timedelta(days=21)
SOURCE_EXPIRY_MARGIN = timedelta(hours=24)
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_RECORD_BYTES = 1024
MAX_PLIST_BYTES = 16 * 1024
MAX_STATUS_OUTPUT_BYTES = 4096
MAX_COMMAND_OUTPUT_BYTES = 128 * 1024
MAX_ATTEMPTS = 64
STATUS_TIMEOUT_SECONDS = 15
NOTIFICATION_TIMEOUT_SECONDS = 10
SYSTEM_GIT = Path("/usr/bin/git")
SYSTEM_LAUNCHCTL = Path("/bin/launchctl")
SYSTEM_PLUTIL = Path("/usr/bin/plutil")
SYSTEM_OSASCRIPT = Path("/usr/bin/osascript")
TITLE = "FPL Decision Engine"
TERMINAL_MESSAGE = (
    "Completion monitoring reached a terminal state. Open the project to verify "
    "the status."
)
REVIEW_MESSAGE = (
    "Completion-monitor scheduling needs review. Open the project to inspect the "
    "safe status."
)
TEST_MESSAGE = "Local notification test. No completion status is being reported."
TERMINAL_MESSAGE_ID = "TERMINAL_STATE_NEEDS_VERIFICATION"
REVIEW_MESSAGE_ID = "SCHEDULING_REVIEW_REQUIRED"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SAFE_ID_PATTERN = re.compile(r"[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{32}")
SOURCE_PLAN_FIELDS = {
    "acknowledged_untracked_paths", "clean_data_root", "controller_path",
    "controller_sha256", "created_at", "evaluation_data_root", "expected_commit",
    "expires_at", "feature_data_root", "format", "gameweek",
    "installed_plist_path", "interval_seconds", "label", "not_before",
    "prediction_data_root", "prediction_snapshot_timestamp",
    "python_distribution_inventory_sha256", "python_path", "python_resolved_path",
    "python_version", "raw_data_root", "repository", "season",
    "task028b_control_data_root", "uid",
}
PLAN_FIELDS = {
    "acknowledged_untracked_paths", "created_at", "expires_at", "format",
    "observer_expected_commit", "observer_repository",
    "installed_plist_path", "interval_seconds", "label", "not_before",
    "notification_tool_path", "notification_tool_sha256", "observer_id",
    "python_path", "python_resolved_path", "python_version",
    "source_controller_path", "source_controller_sha256", "source_label",
    "source_expected_commit", "source_plan_path", "source_plan_sha256",
    "source_plan_sha256_path", "source_python_path", "source_python_resolved_path",
    "source_python_version", "source_repository", "uid",
}
PLIST_KEYS = {
    "KeepAlive", "Label", "ProgramArguments", "RunAtLoad", "StandardErrorPath",
    "StandardOutPath", "StartInterval", "Umask", "WorkingDirectory",
}
LIFECYCLE_FIELDS = {
    "command_evidence", "event_id", "event_type", "format",
    "installed_plist_path", "label", "observed_at", "plan_sha256", "status", "uid",
}
CLAIM_FIELDS = {
    "attempt_id", "format", "message_id", "observed_at", "plan_sha256",
    "record_type", "source_plan_sha256", "source_status",
}
RESULT_FIELDS = {
    "attempt_id", "claim_sha256", "command_exit_class", "format", "observed_at",
    "plan_sha256", "record_type", "source_plan_sha256",
}
FAILURE_FIELDS = {
    "attempt_id", "error_class", "format", "observed_at", "plan_sha256",
    "record_type", "source_plan_sha256",
}
NOTIFIED_FIELDS = {
    "attempt_id", "claim", "command_exit_class", "format", "message_id",
    "observed_at", "plan_sha256", "result", "source_plan_sha256", "source_status",
}
ACTIVE_SOURCE = {"ACTIVE_NOT_YET_DUE", "ACTIVE_WAITING", "ACTIVE_RETRYABLE"}
INACTIVE_SOURCE = {"NOT_ACTIVATED", "DEACTIVATED"}
SOURCE_STATUS = ACTIVE_SOURCE | INACTIVE_SOURCE | {"TERMINAL_QUIESCENT", "REVIEW_REQUIRED"}
FAILURE_CLASSES = {"OBSERVER_EXPIRED", "SOURCE_PROCESS_NOT_STARTED", "SOURCE_STATUS_RETRYABLE",
                   "SOURCE_STATUS_INVALID"}
RESULT_CLASSES = {"PROCESS_NOT_STARTED", "DELIVERY_OUTCOME_UNCERTAIN",
                  "COMMAND_ACCEPTED_NOT_VISIBLE_DELIVERY_PROOF"}


class NotificationError(Exception):
    """Bounded fail-closed error safe for local control output."""


class ProcessNotStarted(NotificationError):
    """The injected runner proves process creation never succeeded."""


class CommandTimedOut(NotificationError):
    """The child started but did not finish before its bounded timeout."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


@dataclass(frozen=True)
class ObserverResult:
    status: str
    exit_status: int
    source_called: bool = False
    notification_called: bool = False


@dataclass(frozen=True)
class PreparedObserver:
    root: Path
    plan_path: Path
    plan_hash_path: Path
    plist_path: Path
    label: str


CommandRunner = Callable[[Sequence[str], int], CommandResult]
Clock = Callable[[], datetime]
IdFactory = Callable[[datetime], str]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise NotificationError(message)


def canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NotificationError("invalid canonical JSON value") from exc


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalized_clock(clock: Clock) -> datetime:
    value = clock()
    require(value.tzinfo is not None, "timezone-aware clock required")
    return value.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    require(value.tzinfo is not None, "timezone-aware clock required")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def parse_utc(value: object, field: str) -> datetime:
    require(isinstance(value, str) and value.endswith("Z"), f"invalid {field}")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise NotificationError(f"invalid {field}") from exc
    require(parsed.tzinfo == timezone.utc, f"invalid {field}")
    return parsed


def unique_id(now: datetime) -> str:
    return f"{now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')}-{uuid.uuid4().hex}"


def absolute_path(path: Path, *, must_exist: bool = True) -> Path:
    require(path.is_absolute() and ".." not in path.parts, "safe absolute path required")
    result = Path(os.path.abspath(path))
    if must_exist:
        require(result.exists() or result.is_symlink(), "required path does not exist")
    return result


def within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def owned_directory(path: Path, *, private: bool = False) -> Path:
    path = absolute_path(path)
    require(not path.is_symlink(), "symlink directory forbidden")
    require(path.resolve(strict=True) == path, "redirected directory forbidden")
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode), "directory required")
    require(info.st_uid == os.getuid(), "directory owner mismatch")
    if private:
        require(info.st_mode & 0o077 == 0, "owner-only directory required")
    return path


def owned_regular_file(
    path: Path, *, private: bool = True, max_bytes: int = MAX_FILE_BYTES,
    executable: bool = False,
) -> Path:
    path = absolute_path(path)
    require(not path.is_symlink(), "symlink file forbidden")
    require(path.resolve(strict=True) == path, "redirected file forbidden")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), "regular file required")
    require(info.st_uid == os.getuid(), "file owner mismatch")
    require(info.st_nlink == 1, "hard-linked file forbidden")
    require(info.st_size <= max_bytes, "file exceeds size limit")
    if private:
        require(info.st_mode & 0o077 == 0, "owner-only file required")
    if executable:
        require(info.st_mode & 0o111 != 0, "executable file required")
    return path


def stable_read(path: Path, *, private: bool = True, max_bytes: int = MAX_FILE_BYTES) -> bytes:
    path = owned_regular_file(path, private=private, max_bytes=max_bytes)
    expected = path.lstat()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise NotificationError("could not open file") from exc
    try:
        before = os.fstat(descriptor)
        require(
            stat.S_ISREG(before.st_mode) and before.st_nlink == 1
            and before.st_uid == os.getuid()
            and (before.st_dev, before.st_ino) == (expected.st_dev, expected.st_ino),
            "unsafe file",
        )
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(65536, max_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            require(size <= max_bytes, "file exceeds size limit")
        after = os.fstat(descriptor)
        require(
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_nlink)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_nlink),
            "file changed while read",
        )
        require(after.st_nlink == 1, "hard-linked file forbidden")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def hash_body(path: Path, body: bytes) -> bytes:
    return f"{sha256_bytes(body)}  {path.name}\n".encode("ascii")


def publish(path: Path, body: bytes, *, max_bytes: int = MAX_FILE_BYTES) -> None:
    require(len(body) <= max_bytes, "publication exceeds size limit")
    require(not path.exists() and not path.is_symlink(), "refusing overwrite")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        offset = 0
        while offset < len(body):
            count = os.write(descriptor, body[offset:])
            require(count > 0, "short file write")
            offset += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_hashed(path: Path, body: bytes, *, max_bytes: int = MAX_FILE_BYTES) -> None:
    publish(path, body, max_bytes=max_bytes)
    publish(path.with_name(path.name + ".sha256"), hash_body(path, body), max_bytes=256)


def read_hashed_bytes(
    path: Path, *, private: bool = True, max_bytes: int = MAX_FILE_BYTES
) -> bytes:
    body = stable_read(path, private=private, max_bytes=max_bytes)
    digest = stable_read(path.with_name(path.name + ".sha256"), private=private, max_bytes=256)
    require(digest == hash_body(path, body), "file hash mismatch")
    return body


def strict_object(body: bytes, fields: set[str], *, context: str) -> dict[str, object]:
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in values:
            require(key not in result, f"duplicate {context} field")
            result[key] = value
        return result

    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotificationError(f"invalid {context} JSON") from exc
    require(isinstance(value, dict) and set(value) == fields, f"invalid {context} fields")
    require(canonical(value) == body, f"noncanonical {context} JSON")
    return value


def read_hashed_json(
    path: Path, fields: set[str], *, private: bool = True, max_bytes: int = MAX_FILE_BYTES,
    context: str = "record",
) -> dict[str, object]:
    return strict_object(
        read_hashed_bytes(path, private=private, max_bytes=max_bytes), fields, context=context
    )


def command(args: Sequence[str], timeout: int) -> CommandResult:
    require(bool(args) and all(isinstance(item, str) for item in args), "invalid command")
    require(Path(args[0]).is_absolute(), "absolute executable required")
    try:
        result = subprocess.run(
            list(args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env={}, timeout=timeout, check=False,
        )
    except FileNotFoundError as exc:
        raise ProcessNotStarted("command process was not started") from exc
    except OSError as exc:
        raise ProcessNotStarted("command process was not started") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandTimedOut("command timed out") from exc
    require(
        len(result.stdout) <= MAX_COMMAND_OUTPUT_BYTES
        and len(result.stderr) <= MAX_COMMAND_OUTPUT_BYTES,
        "command output exceeded limit",
    )
    return CommandResult(result.returncode, result.stdout, result.stderr)


def git_output(repository: Path, args: Sequence[str], runner: CommandRunner) -> bytes:
    result = runner([str(SYSTEM_GIT), "-C", str(repository), *args], STATUS_TIMEOUT_SECONDS)
    require(result.returncode == 0 and not result.stderr, "Git command failed")
    require(len(result.stdout) <= MAX_FILE_BYTES, "Git output exceeded limit")
    return result.stdout


def parse_git_status(raw: bytes) -> tuple[set[str], bool]:
    untracked: set[str] = set()
    tracked = False
    for record in raw.split(b"\0"):
        if not record:
            continue
        require(len(record) >= 4 and record[2:3] == b" ", "invalid Git status")
        try:
            name = record[3:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NotificationError("non-UTF-8 Git path") from exc
        if record[:2] == b"??":
            untracked.add(name)
        else:
            tracked = True
    return untracked, tracked


def validate_untracked(value: object) -> list[str]:
    require(isinstance(value, list), "untracked list required")
    result: list[str] = []
    for item in value:
        require(isinstance(item, str) and item, "invalid untracked path")
        path = Path(item)
        require(not path.is_absolute() and ".." not in path.parts, "unsafe untracked path")
        require(path.as_posix() == item, "unnormalized untracked path")
        result.append(item)
    require(result == sorted(set(result)), "untracked paths must be unique and sorted")
    return result


def repository_preflight(plan: dict[str, object], runner: CommandRunner) -> None:
    repository = owned_directory(Path(str(plan["observer_repository"])))
    commit = git_output(repository, ["rev-parse", "HEAD"], runner).decode().strip()
    require(commit == plan["observer_expected_commit"], "observer repository commit mismatch")
    raw = git_output(
        repository,
        ["-c", "core.excludesFile=/dev/null", "status", "--porcelain=v1", "-z",
         "--untracked-files=all"], runner,
    )
    untracked, tracked = parse_git_status(raw)
    require(not tracked, "tracked or staged repository change")
    require(untracked == set(validate_untracked(plan["acknowledged_untracked_paths"])),
            "unreviewed untracked repository path")
    path = owned_regular_file(Path(str(plan["notification_tool_path"])), private=False)
    body = stable_read(path, private=False)
    require(sha256_bytes(body) == plan["notification_tool_sha256"],
            "notification tool changed")
    committed = git_output(
        repository,
        ["show", f"{plan['observer_expected_commit']}:{SCRIPT_RELATIVE.as_posix()}"],
        runner,
    )
    require(committed == body, "notification tool differs from reviewed commit")


def source_controller_preflight(plan: dict[str, object], runner: CommandRunner) -> None:
    repository = owned_directory(Path(str(plan["source_repository"])))
    controller = owned_regular_file(Path(str(plan["source_controller_path"])), private=False)
    body = stable_read(controller, private=False)
    require(sha256_bytes(body) == plan["source_controller_sha256"],
            "source controller changed")
    committed = git_output(
        repository,
        ["show", f"{plan['source_expected_commit']}:{SOURCE_SCRIPT_RELATIVE.as_posix()}"],
        runner,
    )
    require(committed == body, "source controller differs from reviewed source commit")


def read_source_plan(path: Path, hash_path: Path) -> tuple[dict[str, object], bytes, str]:
    path = absolute_path(path)
    require(hash_path == path.with_name(path.name + ".sha256"), "source hash sibling required")
    body = stable_read(path, max_bytes=MAX_FILE_BYTES)
    digest = stable_read(hash_path, max_bytes=256)
    require(digest == hash_body(path, body), "source plan hash mismatch")
    value = strict_object(body, SOURCE_PLAN_FIELDS, context="source plan")
    require(value["format"] == SOURCE_FORMAT, "source plan format mismatch")
    for field in ("controller_sha256", "expected_commit"):
        pattern = SHA256_PATTERN if field.endswith("sha256") else COMMIT_PATTERN
        require(isinstance(value[field], str) and pattern.fullmatch(value[field]), f"invalid {field}")
    require(type(value["uid"]) is int and value["uid"] == os.getuid(), "source user mismatch")
    require(value["interval_seconds"] == INTERVAL_SECONDS, "source interval mismatch")
    parse_utc(value["created_at"], "source creation")
    parse_utc(value["not_before"], "source not-before")
    parse_utc(value["expires_at"], "source expiry")
    repository = owned_directory(Path(str(value["repository"])))
    require(within(path, repository / "data"), "source plan outside repository data")
    controller = owned_regular_file(Path(str(value["controller_path"])), private=False)
    require(controller == repository / SOURCE_SCRIPT_RELATIVE, "unexpected source controller")
    require(sha256_bytes(stable_read(controller, private=False)) == value["controller_sha256"],
            "source controller hash mismatch")
    return value, body, sha256_bytes(body)


def observer_label(source_sha: str) -> str:
    require(SHA256_PATTERN.fullmatch(source_sha) is not None, "invalid source hash")
    return f"{LABEL_PREFIX}.{source_sha}"


def user_launch_agents(uid: int) -> Path:
    try:
        return Path(pwd.getpwuid(uid).pw_dir) / "Library" / "LaunchAgents"
    except KeyError as exc:
        raise NotificationError("observer user does not exist") from exc


def validate_plan_values(plan: dict[str, object], plan_path: Path) -> None:
    require(set(plan) == PLAN_FIELDS and plan["format"] == FORMAT, "observer plan mismatch")
    uid = plan["uid"]
    require(type(uid) is int and uid == os.getuid(), "observer user mismatch")
    source_sha = plan["source_plan_sha256"]
    require(isinstance(source_sha, str) and SHA256_PATTERN.fullmatch(source_sha), "invalid source hash")
    require(plan["observer_id"] == source_sha, "observer identity mismatch")
    require(plan["label"] == observer_label(source_sha), "observer label mismatch")
    require(plan["interval_seconds"] == INTERVAL_SECONDS, "observer interval mismatch")
    require(plan_path.name == PLAN_NAME and plan_path.parent.name == source_sha,
            "noncanonical observer root")
    observer_repository = Path(str(plan["observer_repository"]))
    source_repository = Path(str(plan["source_repository"]))
    require(plan_path.parent.parent == source_repository / PARENT_RELATIVE,
            "observer path mismatch")
    require(plan["notification_tool_path"] == str(observer_repository / SCRIPT_RELATIVE),
            "notification tool path mismatch")
    require(plan["source_controller_path"] == str(source_repository / SOURCE_SCRIPT_RELATIVE),
            "source controller path mismatch")
    for field in ("notification_tool_sha256", "source_controller_sha256"):
        require(isinstance(plan[field], str) and SHA256_PATTERN.fullmatch(str(plan[field])),
                f"invalid {field}")
    for field in ("observer_expected_commit", "source_expected_commit"):
        require(isinstance(plan[field], str) and COMMIT_PATTERN.fullmatch(str(plan[field])),
                f"invalid {field}")
    for field in ("source_plan_path", "source_plan_sha256_path", "installed_plist_path",
                  "python_path", "python_resolved_path", "source_python_path",
                  "source_python_resolved_path"):
        require(Path(str(plan[field])).is_absolute(), f"invalid {field}")
    require(
        Path(str(plan["source_plan_sha256_path"]))
        == Path(str(plan["source_plan_path"])).with_name(Path(str(plan["source_plan_path"])).name + ".sha256"),
        "source hash path mismatch",
    )
    require(
        plan["installed_plist_path"] == str(user_launch_agents(uid) / f"{plan['label']}.plist"),
        "installed plist path mismatch",
    )
    created = parse_utc(plan["created_at"], "creation")
    not_before = parse_utc(plan["not_before"], "not-before")
    expires = parse_utc(plan["expires_at"], "expiry")
    require(created <= not_before < expires, "invalid observer time order")
    require(expires - created <= MAX_PLAN_LIFETIME, "observer lifetime too long")
    require(isinstance(plan["python_version"], str) and plan["python_version"], "invalid Python version")
    require(isinstance(plan["source_python_version"], str) and plan["source_python_version"],
            "invalid source Python version")
    validate_untracked(plan["acknowledged_untracked_paths"])


def load_plan(path: Path, hash_path: Path) -> tuple[dict[str, object], str]:
    path = absolute_path(path)
    require(hash_path == path.with_name(path.name + ".sha256"), "observer hash sibling required")
    plan = read_hashed_json(path, PLAN_FIELDS, context="observer plan")
    validate_plan_values(plan, path)
    return plan, sha256_bytes(canonical(plan))


def plist_body(plan: dict[str, object], plan_path: Path) -> bytes:
    value = {
        "KeepAlive": False,
        "Label": plan["label"],
        "ProgramArguments": [
            plan["python_path"], "-I", plan["notification_tool_path"], "run",
            "--plan", str(plan_path), "--plan-sha256-file",
            str(plan_path.with_name(plan_path.name + ".sha256")),
        ],
        "RunAtLoad": True,
        "StandardErrorPath": "/dev/null",
        "StandardOutPath": "/dev/null",
        "StartInterval": INTERVAL_SECONDS,
        "Umask": "077",
        "WorkingDirectory": plan["observer_repository"],
    }
    body = plistlib.dumps(value, fmt=plistlib.FMT_XML, sort_keys=True)
    require(len(body) <= MAX_PLIST_BYTES, "plist exceeds size limit")
    return body


def validate_plist(plan: dict[str, object], plan_path: Path) -> bytes:
    body = read_hashed_bytes(plan_path.parent / PLIST_NAME, max_bytes=MAX_PLIST_BYTES)
    try:
        value = plistlib.loads(body)
    except Exception as exc:
        raise NotificationError("invalid observer plist") from exc
    require(isinstance(value, dict) and set(value) == PLIST_KEYS, "invalid plist fields")
    require(body == plist_body(plan, plan_path), "observer plist mismatch")
    return body


def verify_ignored(repository: Path, parent: Path, runner: CommandRunner) -> None:
    relative = parent.relative_to(repository).as_posix()
    result = runner(
        [str(SYSTEM_GIT), "-C", str(repository), "check-ignore", "--quiet", "--", relative],
        STATUS_TIMEOUT_SECONDS,
    )
    require(result.returncode == 0, "observer parent is not ignored by Git")


def create_observer_root(root: Path) -> Path:
    require(not root.exists() and not root.is_symlink(), "observer root already exists")
    try:
        root.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise NotificationError("observer root already exists") from exc
    return owned_directory(root, private=True)


def prepare_observer(
    *, source_plan_path: Path, source_plan_hash_path: Path,
    observer_repository: Path, observer_expected_commit: str,
    python: Path, expires_at: str, allowed_untracked: set[str], clock: Clock = utc_now,
    runner: CommandRunner = command,
) -> PreparedObserver:
    require(platform.system() == "Darwin", "observer preparation requires macOS")
    require(COMMIT_PATTERN.fullmatch(observer_expected_commit) is not None,
            "invalid observer expected commit")
    source, _source_body, source_sha = read_source_plan(source_plan_path, source_plan_hash_path)
    source_repository = owned_directory(Path(str(source["repository"])))
    observer_repository = owned_directory(observer_repository)
    parent = source_repository / PARENT_RELATIVE
    owned_directory(parent.parent, private=True)
    if not parent.exists() and not parent.is_symlink():
        try:
            parent.mkdir(mode=0o700)
        except FileExistsError:
            pass
    owned_directory(parent, private=True)
    verify_ignored(source_repository, parent, runner)
    tool = owned_regular_file(observer_repository / SCRIPT_RELATIVE, private=False)
    controller = owned_regular_file(source_repository / SOURCE_SCRIPT_RELATIVE, private=False)
    invoked = absolute_path(python)
    resolved = invoked.resolve(strict=True)
    require(Path(os.path.abspath(sys.executable)) == invoked, "prepare with selected Python")
    require(resolved.is_file() and resolved.stat().st_mode & 0o111, "invalid Python executable")
    created = normalized_clock(clock)
    observer_expiry = parse_utc(expires_at, "observer expiry")
    source_expiry = parse_utc(source["expires_at"], "source expiry")
    require(observer_expiry >= source_expiry + SOURCE_EXPIRY_MARGIN,
            "observer expiry lacks source margin")
    require(observer_expiry - created <= MAX_PLAN_LIFETIME, "observer lifetime too long")
    root = parent / source_sha
    require(not root.exists() and not root.is_symlink(), "observer root already exists")
    label = observer_label(source_sha)
    plan = {
        "acknowledged_untracked_paths": sorted(allowed_untracked),
        "created_at": iso_utc(created),
        "observer_expected_commit": observer_expected_commit,
        "observer_repository": str(observer_repository),
        "expires_at": iso_utc(observer_expiry),
        "format": FORMAT,
        "installed_plist_path": str(user_launch_agents(os.getuid()) / f"{label}.plist"),
        "interval_seconds": INTERVAL_SECONDS,
        "label": label,
        "not_before": iso_utc(created),
        "notification_tool_path": str(tool),
        "notification_tool_sha256": sha256_bytes(stable_read(tool, private=False)),
        "observer_id": source_sha,
        "python_path": str(invoked),
        "python_resolved_path": str(resolved),
        "python_version": platform.python_version(),
        "source_controller_path": str(controller),
        "source_controller_sha256": sha256_bytes(stable_read(controller, private=False)),
        "source_expected_commit": source["expected_commit"],
        "source_label": source["label"],
        "source_plan_path": str(source_plan_path),
        "source_plan_sha256": source_sha,
        "source_plan_sha256_path": str(source_plan_hash_path),
        "source_python_path": source["python_path"],
        "source_python_resolved_path": source["python_resolved_path"],
        "source_python_version": source["python_version"],
        "source_repository": str(source_repository),
        "uid": os.getuid(),
    }
    try:
        validate_plan_values(plan, root / PLAN_NAME)
        repository_preflight(plan, runner)
        source_controller_preflight(plan, runner)
        create_observer_root(root)
        publish_hashed(root / PLAN_NAME, canonical(plan))
        (root / ATTEMPT_DIRECTORY).mkdir(mode=0o700)
        (root / LIFECYCLE_DIRECTORY).mkdir(mode=0o700)
        publish(root / LOCK_NAME, b"")
        publish_hashed(root / PLIST_NAME, plist_body(plan, root / PLAN_NAME), max_bytes=MAX_PLIST_BYTES)
    except Exception:
        # Never remove a partially published canonical root: it is review evidence.
        raise
    return PreparedObserver(root, root / PLAN_NAME, root / f"{PLAN_NAME}.sha256",
                            root / PLIST_NAME, label)


def pair_state(path: Path) -> str:
    body = path.exists() or path.is_symlink()
    digest_path = path.with_name(path.name + ".sha256")
    digest = digest_path.exists() or digest_path.is_symlink()
    if body and digest:
        return "complete"
    if body or digest:
        return "partial"
    return "absent"


def record_paths(directory: Path) -> list[Path]:
    directory = owned_directory(directory, private=True)
    names = {path.name for path in directory.iterdir()}
    json_paths = sorted(path for path in directory.iterdir() if path.name.endswith(".json"))
    expected: set[str] = set()
    for path in json_paths:
        require(not path.is_symlink(), "symlink record forbidden")
        expected.update({path.name, path.name + ".sha256"})
    require(names == expected, "unexpected record entry")
    return json_paths


def validate_attempts(root: Path, plan: dict[str, object], plan_sha: str) -> dict[str, dict[str, object]]:
    attempts: dict[str, dict[str, object]] = {}
    identities: set[str] = set()
    for path in record_paths(root / ATTEMPT_DIRECTORY):
        match = re.fullmatch(r"(.+)-(claim|result|failure)\.json", path.name)
        require(match is not None and SAFE_ID_PATTERN.fullmatch(match.group(1)), "invalid attempt filename")
        attempt_id, kind = match.groups()
        fields = {"claim": CLAIM_FIELDS, "result": RESULT_FIELDS, "failure": FAILURE_FIELDS}[kind]
        value = read_hashed_json(path, fields, max_bytes=MAX_RECORD_BYTES, context=kind)
        require(value["format"] == FORMAT and value["record_type"] == kind.upper(),
                "attempt format mismatch")
        require(value["attempt_id"] == attempt_id, "attempt identity mismatch")
        require(value["plan_sha256"] == plan_sha, "attempt plan mismatch")
        require(value["source_plan_sha256"] == plan["source_plan_sha256"],
                "attempt source mismatch")
        parse_utc(value["observed_at"], "attempt observation")
        if kind == "claim":
            require(value["source_status"] in {"TERMINAL_QUIESCENT", "REVIEW_REQUIRED"},
                    "invalid claimed source status")
            expected_message = (TERMINAL_MESSAGE_ID if value["source_status"] == "TERMINAL_QUIESCENT"
                                else REVIEW_MESSAGE_ID)
            require(value["message_id"] == expected_message, "claim message mismatch")
        elif kind == "result":
            require(value["command_exit_class"] in RESULT_CLASSES, "invalid result class")
        else:
            require(value["error_class"] in FAILURE_CLASSES, "invalid failure class")
        require(kind not in attempts.setdefault(attempt_id, {}), "duplicate attempt record")
        attempts[attempt_id][kind] = value
        identities.add(attempt_id)
    require(len(identities) <= MAX_ATTEMPTS, "attempt cap exceeded")
    for attempt_id, group in attempts.items():
        require(not ("failure" in group and ("claim" in group or "result" in group)),
                "conflicting attempt records")
        if "result" in group:
            require("claim" in group, "result without claim")
            claim_path = root / ATTEMPT_DIRECTORY / f"{attempt_id}-claim.json"
            require(group["result"]["claim_sha256"] == sha256_bytes(canonical(group["claim"])),
                    "result claim mismatch")
            require(pair_state(claim_path) == "complete", "claim pair incomplete")
    return attempts


def lifecycle_state(root: Path, plan: dict[str, object], plan_sha: str) -> str:
    paths = record_paths(root / LIFECYCLE_DIRECTORY)
    if not paths:
        return "PREPARED"
    values = [read_hashed_json(path, LIFECYCLE_FIELDS, max_bytes=MAX_RECORD_BYTES,
                               context="lifecycle") for path in paths]
    for path, value in zip(paths, values):
        require(value["format"] == FORMAT and value["event_id"] == path.stem,
                "lifecycle identity mismatch")
        require(value["plan_sha256"] == plan_sha and value["label"] == plan["label"],
                "lifecycle plan mismatch")
        require(value["uid"] == plan["uid"] and value["installed_plist_path"] == plan["installed_plist_path"],
                "lifecycle target mismatch")
        require(value["event_type"] in {"ACTIVATION", "DEACTIVATION"}, "invalid lifecycle type")
        require(value["status"] in {"ACTIVE", "DEACTIVATED", "REVIEW_REQUIRED"},
                "invalid lifecycle status")
        parse_utc(value["observed_at"], "lifecycle observation")
        require(isinstance(value["command_evidence"], list), "invalid command evidence")
        require(all(type(item) is int for item in value["command_evidence"]),
                "invalid command evidence")
    if any(value["status"] == "REVIEW_REQUIRED" for value in values):
        return "REVIEW_REQUIRED"
    if any(value["event_type"] == "DEACTIVATION" for value in values):
        return "DEACTIVATED"
    require(all(value["event_type"] == "ACTIVATION" and value["status"] == "ACTIVE"
                for value in values), "invalid active lifecycle")
    return "ACTIVE"


def active_lifecycle(root: Path, plan: dict[str, object], plan_sha: str) -> bool:
    return lifecycle_state(root, plan, plan_sha) == "ACTIVE"


def full_preflight(plan: dict[str, object], plan_path: Path, runner: CommandRunner) -> str:
    repository_preflight(plan, runner)
    source_controller_preflight(plan, runner)
    validate_plist(plan, plan_path)
    source, _body, source_sha = read_source_plan(
        Path(str(plan["source_plan_path"])), Path(str(plan["source_plan_sha256_path"]))
    )
    require(source_sha == plan["source_plan_sha256"], "source plan identity changed")
    require(source["controller_sha256"] == plan["source_controller_sha256"],
            "source controller identity changed")
    require(source["label"] == plan["source_label"], "source label changed")
    require(source["expected_commit"] == plan["source_expected_commit"],
            "source commit identity changed")
    require(source["python_path"] == plan["source_python_path"]
            and source["python_resolved_path"] == plan["source_python_resolved_path"]
            and source["python_version"] == plan["source_python_version"],
            "source Python identity changed")
    require(Path(os.path.abspath(sys.executable)) == Path(str(plan["python_path"])),
            "wrong observer Python interpreter")
    require(Path(str(plan["python_path"])).resolve(strict=True) == Path(str(plan["python_resolved_path"])),
            "Python target changed")
    require(platform.python_version() == plan["python_version"], "Python version changed")
    return source_sha


def parse_source_status(result: CommandResult) -> str:
    require(len(result.stdout) <= MAX_STATUS_OUTPUT_BYTES and len(result.stderr) <= MAX_STATUS_OUTPUT_BYTES,
            "source status output exceeded accepted limit")
    require(result.stderr == b"", "source status wrote stderr")
    require(result.stdout.endswith(b"\n") and not result.stdout.endswith(b"\n\n"),
            "source status framing mismatch")
    body = result.stdout[:-1]
    def pairs(values: list[tuple[str, object]]) -> dict[str, object]:
        output: dict[str, object] = {}
        for key, value in values:
            require(key not in output, "duplicate source status field")
            output[key] = value
        return output
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotificationError("invalid source status JSON") from exc
    require(isinstance(value, dict), "source status object required")
    status_value = value.get("status")
    require(isinstance(status_value, str) and status_value in SOURCE_STATUS,
            "unknown source status")
    if result.returncode == 0:
        require(set(value) == {"status"} and status_value != "REVIEW_REQUIRED",
                "source exit/status conflict")
    elif result.returncode == 3:
        require(status_value == "REVIEW_REQUIRED" and set(value) <= {"status", "detail"},
                "source exit/status conflict")
        if "detail" in value:
            detail = value["detail"]
            require(isinstance(detail, str) and 1 <= len(detail) <= 512,
                    "invalid source detail")
    else:
        raise NotificationError("unsupported source exit code")
    require(result.stdout == json.dumps(value, sort_keys=True).encode() + b"\n",
            "noncanonical source status encoding")
    return status_value


def source_status_command(plan: dict[str, object]) -> list[str]:
    return [
        str(plan["source_python_path"]), "-I", str(plan["source_controller_path"]), "status",
        "--plan", str(plan["source_plan_path"]), "--plan-sha256-file",
        str(plan["source_plan_sha256_path"]),
    ]


def apple_script(message: str) -> str:
    require(message in {TERMINAL_MESSAGE, REVIEW_MESSAGE, TEST_MESSAGE},
            "dynamic notification forbidden")
    escaped_title = TITLE.replace("\\", "\\\\").replace('"', '\\"')
    escaped_message = message.replace("\\", "\\\\").replace('"', '\\"')
    return f'display notification "{escaped_message}" with title "{escaped_title}"'


def notification_command(message: str) -> list[str]:
    return [str(SYSTEM_OSASCRIPT), "-e", apple_script(message)]


def publish_failure(
    root: Path, plan: dict[str, object], plan_sha: str, error_class: str,
    now: datetime, id_factory: IdFactory,
) -> None:
    attempt_id = id_factory(now)
    require(SAFE_ID_PATTERN.fullmatch(attempt_id) is not None, "invalid attempt ID")
    value = {
        "attempt_id": attempt_id, "error_class": error_class, "format": FORMAT,
        "observed_at": iso_utc(now), "plan_sha256": plan_sha,
        "record_type": "FAILURE", "source_plan_sha256": plan["source_plan_sha256"],
    }
    publish_hashed(root / ATTEMPT_DIRECTORY / f"{attempt_id}-failure.json",
                   canonical(value), max_bytes=MAX_RECORD_BYTES)


def publish_claim(
    root: Path, plan: dict[str, object], plan_sha: str, source_status: str,
    now: datetime, id_factory: IdFactory,
) -> tuple[str, dict[str, object], Path]:
    attempt_id = id_factory(now)
    require(SAFE_ID_PATTERN.fullmatch(attempt_id) is not None, "invalid attempt ID")
    message_id = TERMINAL_MESSAGE_ID if source_status == "TERMINAL_QUIESCENT" else REVIEW_MESSAGE_ID
    value = {
        "attempt_id": attempt_id, "format": FORMAT, "message_id": message_id,
        "observed_at": iso_utc(now), "plan_sha256": plan_sha, "record_type": "CLAIM",
        "source_plan_sha256": plan["source_plan_sha256"], "source_status": source_status,
    }
    path = root / ATTEMPT_DIRECTORY / f"{attempt_id}-claim.json"
    publish_hashed(path, canonical(value), max_bytes=MAX_RECORD_BYTES)
    return attempt_id, value, path


def publish_result(
    root: Path, plan: dict[str, object], plan_sha: str, attempt_id: str,
    claim: dict[str, object], exit_class: str, now: datetime,
) -> tuple[dict[str, object], Path]:
    value = {
        "attempt_id": attempt_id, "claim_sha256": sha256_bytes(canonical(claim)),
        "command_exit_class": exit_class, "format": FORMAT, "observed_at": iso_utc(now),
        "plan_sha256": plan_sha, "record_type": "RESULT",
        "source_plan_sha256": plan["source_plan_sha256"],
    }
    path = root / ATTEMPT_DIRECTORY / f"{attempt_id}-result.json"
    publish_hashed(path, canonical(value), max_bytes=MAX_RECORD_BYTES)
    return value, path


def publish_notified(
    root: Path, plan: dict[str, object], plan_sha: str, claim: dict[str, object],
    claim_path: Path, result: dict[str, object], result_path: Path, now: datetime,
) -> None:
    value = {
        "attempt_id": claim["attempt_id"],
        "claim": {"sha256": sha256_bytes(canonical(claim)), "name": claim_path.name},
        "command_exit_class": result["command_exit_class"], "format": FORMAT,
        "message_id": claim["message_id"], "observed_at": iso_utc(now),
        "plan_sha256": plan_sha,
        "result": {"sha256": sha256_bytes(canonical(result)), "name": result_path.name},
        "source_plan_sha256": plan["source_plan_sha256"],
        "source_status": claim["source_status"],
    }
    publish_hashed(root / NOTIFIED_NAME, canonical(value), max_bytes=MAX_RECORD_BYTES)


def validate_notified(
    root: Path, plan: dict[str, object], plan_sha: str,
    attempts: dict[str, dict[str, object]],
) -> bool:
    state = pair_state(root / NOTIFIED_NAME)
    require(state != "partial", "partial notification receipt")
    if state == "absent":
        return False
    value = read_hashed_json(root / NOTIFIED_NAME, NOTIFIED_FIELDS,
                             max_bytes=MAX_RECORD_BYTES, context="notification receipt")
    require(value["format"] == FORMAT and value["plan_sha256"] == plan_sha,
            "notification receipt plan mismatch")
    require(value["source_plan_sha256"] == plan["source_plan_sha256"],
            "notification receipt source mismatch")
    attempt_id = value["attempt_id"]
    require(isinstance(attempt_id, str) and attempt_id in attempts, "receipt attempt missing")
    group = attempts[attempt_id]
    require(set(group) == {"claim", "result"}, "receipt attempt incomplete")
    claim, result = group["claim"], group["result"]
    require(result["command_exit_class"] == "COMMAND_ACCEPTED_NOT_VISIBLE_DELIVERY_PROOF",
            "receipt result is not successful")
    require(value["claim"] == {"name": f"{attempt_id}-claim.json",
                                "sha256": sha256_bytes(canonical(claim))},
            "receipt claim mismatch")
    require(value["result"] == {"name": f"{attempt_id}-result.json",
                                 "sha256": sha256_bytes(canonical(result))},
            "receipt result mismatch")
    require(value["source_status"] == claim["source_status"]
            and value["message_id"] == claim["message_id"], "receipt content mismatch")
    parse_utc(value["observed_at"], "receipt observation")
    return True


def reconcile_success(
    root: Path, plan: dict[str, object], plan_sha: str,
    attempts: dict[str, dict[str, object]], now: datetime,
) -> bool:
    successful: list[tuple[str, dict[str, object], dict[str, object]]] = []
    for attempt_id, group in attempts.items():
        if set(group) == {"claim", "result"} and group["result"]["command_exit_class"] == "COMMAND_ACCEPTED_NOT_VISIBLE_DELIVERY_PROOF":
            successful.append((attempt_id, group["claim"], group["result"]))
    require(len(successful) <= 1, "multiple successful notification attempts")
    if not successful:
        return False
    attempt_id, claim, result = successful[0]
    publish_notified(
        root, plan, plan_sha, claim,
        root / ATTEMPT_DIRECTORY / f"{attempt_id}-claim.json", result,
        root / ATTEMPT_DIRECTORY / f"{attempt_id}-result.json", now,
    )
    return True


def observer_lock(path: Path) -> int:
    path = owned_regular_file(path, max_bytes=0)
    expected = path.lstat()
    descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        actual = os.fstat(descriptor)
        require((actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino)
                and actual.st_uid == os.getuid() and actual.st_nlink == 1,
                "unsafe lock file")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return -1
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def execute_once(
    plan_path: Path, plan_hash_path: Path, *, runner: CommandRunner = command,
    clock: Clock = utc_now, id_factory: IdFactory = unique_id,
    activation_validator: Callable[[Path, dict[str, object], str], bool] | None = None,
) -> ObserverResult:
    plan, plan_sha = load_plan(plan_path, plan_hash_path)
    root = plan_path.parent
    lock = observer_lock(root / LOCK_NAME)
    if lock < 0:
        return ObserverResult("OBSERVER_BUSY", 2)
    try:
        full_preflight(plan, plan_path, runner)
        if activation_validator is not None:
            lifecycle = "ACTIVE" if activation_validator(root, plan, plan_sha) else "PREPARED"
        else:
            lifecycle = lifecycle_state(root, plan, plan_sha)
        if lifecycle == "PREPARED":
            return ObserverResult("PREPARED_NOT_INSTALLED", 0)
        if lifecycle == "DEACTIVATED":
            return ObserverResult("DEACTIVATED", 0)
        if lifecycle != "ACTIVE":
            return ObserverResult("REVIEW_REQUIRED", 3)
        installed = Path(str(plan["installed_plist_path"]))
        require(read_hashed_bytes(root / PLIST_NAME, max_bytes=MAX_PLIST_BYTES)
                == stable_read(installed, max_bytes=MAX_PLIST_BYTES), "installed plist mismatch")
        attempts = validate_attempts(root, plan, plan_sha)
        if validate_notified(root, plan, plan_sha, attempts):
            return ObserverResult("NOTIFICATION_QUIESCENT", 0)
        now = normalized_clock(clock)
        observations = [
            parse_utc(record["observed_at"], "attempt observation")
            for group in attempts.values() for record in group.values()
        ]
        require(not observations or now >= max(observations), "system clock moved backwards")
        if reconcile_success(root, plan, plan_sha, attempts, now):
            return ObserverResult("NOTIFICATION_QUIESCENT", 0)
        for group in attempts.values():
            if "claim" in group and "result" not in group:
                return ObserverResult("REVIEW_REQUIRED", 3)
            if "result" in group and group["result"]["command_exit_class"] != "PROCESS_NOT_STARTED":
                return ObserverResult("REVIEW_REQUIRED", 3)
        if len(attempts) >= MAX_ATTEMPTS:
            return ObserverResult("REVIEW_REQUIRED", 3)
        created = parse_utc(plan["created_at"], "creation")
        require(now >= created, "system clock moved backwards")
        if now >= parse_utc(plan["expires_at"], "expiry"):
            publish_failure(root, plan, plan_sha, "OBSERVER_EXPIRED", now, id_factory)
            return ObserverResult("REVIEW_REQUIRED", 3)
        source_called = True
        try:
            status_result = runner(source_status_command(plan), STATUS_TIMEOUT_SECONDS)
            source_status = parse_source_status(status_result)
        except ProcessNotStarted:
            publish_failure(root, plan, plan_sha, "SOURCE_PROCESS_NOT_STARTED", now, id_factory)
            return ObserverResult("ACTIVE_RETRYABLE", 2, source_called=True)
        except CommandTimedOut:
            publish_failure(root, plan, plan_sha, "SOURCE_STATUS_RETRYABLE", now, id_factory)
            return ObserverResult("ACTIVE_RETRYABLE", 2, source_called=True)
        except NotificationError:
            publish_failure(root, plan, plan_sha, "SOURCE_STATUS_INVALID", now, id_factory)
            return ObserverResult("REVIEW_REQUIRED", 3, source_called=True)
        if source_status in ACTIVE_SOURCE:
            return ObserverResult("ACTIVE_QUIET", 0, source_called=True)
        if source_status in INACTIVE_SOURCE:
            return ObserverResult("INACTIVE_SOURCE", 0, source_called=True)
        message = TERMINAL_MESSAGE if source_status == "TERMINAL_QUIESCENT" else REVIEW_MESSAGE
        attempt_id, claim, claim_path = publish_claim(
            root, plan, plan_sha, source_status, now, id_factory
        )
        try:
            notification = runner(notification_command(message), NOTIFICATION_TIMEOUT_SECONDS)
        except ProcessNotStarted:
            result, _path = publish_result(
                root, plan, plan_sha, attempt_id, claim, "PROCESS_NOT_STARTED", clock()
            )
            return ObserverResult("ACTIVE_RETRYABLE", 2, True, False)
        except NotificationError:
            publish_result(root, plan, plan_sha, attempt_id, claim,
                           "DELIVERY_OUTCOME_UNCERTAIN", clock())
            return ObserverResult("REVIEW_REQUIRED", 3, True, True)
        if notification.returncode != 0:
            result, _path = publish_result(
                root, plan, plan_sha, attempt_id, claim, "DELIVERY_OUTCOME_UNCERTAIN", clock()
            )
            return ObserverResult("REVIEW_REQUIRED", 3, True, True)
        result, result_path = publish_result(
            root, plan, plan_sha, attempt_id, claim,
            "COMMAND_ACCEPTED_NOT_VISIBLE_DELIVERY_PROOF", clock(),
        )
        publish_notified(root, plan, plan_sha, claim, claim_path, result, result_path, clock())
        return ObserverResult("NOTIFICATION_QUIESCENT", 0, True, True)
    except NotificationError:
        return ObserverResult("REVIEW_REQUIRED", 3)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)


def command_evidence(result: CommandResult) -> int:
    return result.returncode


def service_target(plan: dict[str, object]) -> str:
    return f"gui/{plan['uid']}/{plan['label']}"


def label_absent(result: CommandResult) -> bool:
    return result.returncode != 0 and b"Could not find service" in result.stdout + result.stderr


def publish_lifecycle(
    root: Path, plan: dict[str, object], plan_sha: str, event_type: str, status_value: str,
    results: list[CommandResult], now: datetime, id_factory: IdFactory,
) -> dict[str, object]:
    event_id = id_factory(now)
    require(SAFE_ID_PATTERN.fullmatch(event_id) is not None, "invalid lifecycle ID")
    value = {
        "command_evidence": [command_evidence(item) for item in results],
        "event_id": event_id, "event_type": event_type, "format": FORMAT,
        "installed_plist_path": plan["installed_plist_path"], "label": plan["label"],
        "observed_at": iso_utc(now), "plan_sha256": plan_sha, "status": status_value,
        "uid": plan["uid"],
    }
    publish_hashed(root / LIFECYCLE_DIRECTORY / f"{event_id}.json", canonical(value),
                   max_bytes=MAX_RECORD_BYTES)
    return value


def activate(
    plan_path: Path, hash_path: Path, *, execute: bool, runner: CommandRunner = command,
    clock: Clock = utc_now, id_factory: IdFactory = unique_id,
) -> dict[str, object]:
    require(execute and platform.system() == "Darwin", "explicit macOS activation required")
    plan, plan_sha = load_plan(plan_path, hash_path)
    full_preflight(plan, plan_path, runner)
    require(not record_paths(plan_path.parent / LIFECYCLE_DIRECTORY), "lifecycle already exists")
    require(not validate_attempts(plan_path.parent, plan, plan_sha),
            "observer with attempt history cannot be activated")
    require(pair_state(plan_path.parent / NOTIFIED_NAME) == "absent", "observer already notified")
    activation_time = normalized_clock(clock)
    require(parse_utc(plan["created_at"], "creation") <= activation_time
            < parse_utc(plan["expires_at"], "expiry"),
            "activation time outside observer window")
    installed = absolute_path(Path(str(plan["installed_plist_path"])), must_exist=False)
    owned_directory(installed.parent)
    require(not installed.exists() and not installed.is_symlink(), "installed plist exists")
    target = service_target(plan)
    results: list[CommandResult] = []
    absent = runner([str(SYSTEM_LAUNCHCTL), "print", target], STATUS_TIMEOUT_SECONDS)
    results.append(absent)
    require(label_absent(absent), "observer label not provably absent")
    lint = runner([str(SYSTEM_PLUTIL), "-lint", "--", str(plan_path.parent / PLIST_NAME)],
                  STATUS_TIMEOUT_SECONDS)
    results.append(lint)
    require(lint.returncode == 0, "plist lint failed")
    body = validate_plist(plan, plan_path)
    publish(installed, body, max_bytes=MAX_PLIST_BYTES)
    try:
        bootstrap = runner(
            [str(SYSTEM_LAUNCHCTL), "bootstrap", f"gui/{plan['uid']}", str(installed)],
            STATUS_TIMEOUT_SECONDS,
        )
        results.append(bootstrap)
        require(bootstrap.returncode == 0, "observer bootstrap failed")
        loaded = runner([str(SYSTEM_LAUNCHCTL), "print", target], STATUS_TIMEOUT_SECONDS)
        results.append(loaded)
        require(loaded.returncode == 0, "observer load not verified")
        event = publish_lifecycle(
            plan_path.parent, plan, plan_sha, "ACTIVATION", "ACTIVE", results,
            clock(), id_factory,
        )
        kick = runner([str(SYSTEM_LAUNCHCTL), "kickstart", target], STATUS_TIMEOUT_SECONDS)
        results.append(kick)
        require(kick.returncode == 0, "observer kickstart failed")
        return event
    except NotificationError as exc:
        cleanup_ok = False
        try:
            bootout = runner([str(SYSTEM_LAUNCHCTL), "bootout", target], STATUS_TIMEOUT_SECONDS)
            results.append(bootout)
            absent_after = runner([str(SYSTEM_LAUNCHCTL), "print", target], STATUS_TIMEOUT_SECONDS)
            results.append(absent_after)
            cleanup_ok = label_absent(absent_after)
            if (cleanup_ok and installed.exists() and not installed.is_symlink()
                    and stable_read(installed, max_bytes=MAX_PLIST_BYTES) == body):
                installed.unlink()
            cleanup_ok = cleanup_ok and not installed.exists() and not installed.is_symlink()
        except (OSError, NotificationError):
            cleanup_ok = False
        publish_lifecycle(
            plan_path.parent, plan, plan_sha, "ACTIVATION", "REVIEW_REQUIRED", results,
            clock(), id_factory,
        )
        if cleanup_ok:
            raise NotificationError("activation failed; exact rollback verified") from exc
        raise NotificationError("activation failed; cleanup requires owner review") from exc


def deactivate(
    plan_path: Path, hash_path: Path, *, execute: bool, remove_installed_plist: bool,
    runner: CommandRunner = command, clock: Clock = utc_now, id_factory: IdFactory = unique_id,
) -> dict[str, object]:
    require(execute and platform.system() == "Darwin", "explicit macOS deactivation required")
    plan, plan_sha = load_plan(plan_path, hash_path)
    validate_plist(plan, plan_path)
    target = service_target(plan)
    results: list[CommandResult] = []
    state = runner([str(SYSTEM_LAUNCHCTL), "print", target], STATUS_TIMEOUT_SECONDS)
    results.append(state)
    if state.returncode == 0:
        out = runner([str(SYSTEM_LAUNCHCTL), "bootout", target], STATUS_TIMEOUT_SECONDS)
        results.append(out)
        require(out.returncode == 0, "observer bootout failed")
    else:
        require(label_absent(state), "observer state uncertain")
    absent = runner([str(SYSTEM_LAUNCHCTL), "print", target], STATUS_TIMEOUT_SECONDS)
    results.append(absent)
    require(label_absent(absent), "observer absence not verified")
    installed = Path(str(plan["installed_plist_path"]))
    if installed.exists() or installed.is_symlink():
        require(remove_installed_plist, "installed plist removal flag required")
        require(stable_read(installed, max_bytes=MAX_PLIST_BYTES)
                == read_hashed_bytes(plan_path.parent / PLIST_NAME, max_bytes=MAX_PLIST_BYTES),
                "installed plist differs from candidate")
        installed.unlink()
    return publish_lifecycle(plan_path.parent, plan, plan_sha, "DEACTIVATION", "DEACTIVATED",
                             results, clock(), id_factory)


def observer_status(
    plan_path: Path, hash_path: Path, *, runner: CommandRunner = command,
) -> str:
    plan, plan_sha = load_plan(plan_path, hash_path)
    full_preflight(plan, plan_path, runner)
    attempts = validate_attempts(plan_path.parent, plan, plan_sha)
    if validate_notified(plan_path.parent, plan, plan_sha, attempts):
        return "NOTIFICATION_QUIESCENT"
    lifecycle = lifecycle_state(plan_path.parent, plan, plan_sha)
    target = service_target(plan)
    state = runner([str(SYSTEM_LAUNCHCTL), "print", target], STATUS_TIMEOUT_SECONDS)
    installed = Path(str(plan["installed_plist_path"]))
    installed_present = installed.exists() or installed.is_symlink()
    if lifecycle == "ACTIVE" and state.returncode == 0 and installed_present:
        require(stable_read(installed, max_bytes=MAX_PLIST_BYTES)
                == read_hashed_bytes(plan_path.parent / PLIST_NAME,
                                     max_bytes=MAX_PLIST_BYTES),
                "installed plist mismatch")
        return "ACTIVE_QUIET"
    if lifecycle == "DEACTIVATED" and label_absent(state) and not installed_present:
        return "DEACTIVATED"
    if lifecycle == "PREPARED" and label_absent(state) and not installed_present:
        return "PREPARED_NOT_INSTALLED"
    return "REVIEW_REQUIRED"


def verify_observer(plan_path: Path, hash_path: Path, *, runner: CommandRunner = command) -> dict[str, object]:
    plan, plan_sha = load_plan(plan_path, hash_path)
    full_preflight(plan, plan_path, runner)
    attempts = validate_attempts(plan_path.parent, plan, plan_sha)
    notified = validate_notified(plan_path.parent, plan, plan_sha, attempts)
    active_lifecycle(plan_path.parent, plan, plan_sha)
    return {"attempt_count": len(attempts), "notified": notified, "plan_sha256": plan_sha}


def sanitize_evidence(plan_path: Path, hash_path: Path, output_parent: Path) -> Path:
    plan, plan_sha = load_plan(plan_path, hash_path)
    attempts = validate_attempts(plan_path.parent, plan, plan_sha)
    notified = validate_notified(plan_path.parent, plan, plan_sha, attempts)
    parent = owned_directory(output_parent, private=True)
    destination = parent / f"task034b-{plan_sha}"
    require(not destination.exists() and not destination.is_symlink(), "sanitized output exists")
    destination.mkdir(mode=0o700)
    summary = {
        "attempt_count": len(attempts), "format": FORMAT, "notified": notified,
        "plan_sha256": plan_sha, "status": "SANITIZED",
    }
    publish_hashed(destination / "summary.json", canonical(summary), max_bytes=MAX_RECORD_BYTES)
    return destination


def test_notification(*, execute: bool, runner: CommandRunner = command) -> CommandResult:
    require(execute and platform.system() == "Darwin", "explicit macOS test notification required")
    session = runner(
        [str(SYSTEM_LAUNCHCTL), "print", f"gui/{os.getuid()}"], STATUS_TIMEOUT_SECONDS
    )
    require(session.returncode == 0, "interactive Aqua login was not verified")
    result = runner(notification_command(TEST_MESSAGE), NOTIFICATION_TIMEOUT_SECONDS)
    require(result.returncode == 0, "test notification command failed")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--source-plan", type=Path, required=True)
    prepare.add_argument("--source-plan-sha256-file", type=Path, required=True)
    prepare.add_argument("--observer-repository", type=Path, required=True)
    prepare.add_argument("--observer-expected-commit", required=True)
    prepare.add_argument("--python", type=Path, required=True)
    prepare.add_argument("--expires-at", required=True)
    prepare.add_argument("--allow-untracked", action="append", default=[])
    for name in ("verify", "run", "status", "activate", "deactivate", "sanitize"):
        item = sub.add_parser(name)
        item.add_argument("--plan", type=Path, required=True)
        item.add_argument("--plan-sha256-file", type=Path, required=True)
    sub.choices["activate"].add_argument("--execute-launch-agent-activation", action="store_true")
    sub.choices["deactivate"].add_argument("--execute-launch-agent-deactivation", action="store_true")
    sub.choices["deactivate"].add_argument("--remove-installed-plist", action="store_true")
    sub.choices["sanitize"].add_argument("--output-parent", type=Path, required=True)
    test = sub.add_parser("test-notification")
    test.add_argument("--execute-test-notification", action="store_true")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "prepare":
            prepared = prepare_observer(
                source_plan_path=args.source_plan,
                source_plan_hash_path=args.source_plan_sha256_file,
                observer_repository=args.observer_repository,
                observer_expected_commit=args.observer_expected_commit,
                python=args.python,
                expires_at=args.expires_at, allowed_untracked=set(args.allow_untracked),
            )
            print(json.dumps({"label": prepared.label, "status": "PREPARED_NOT_INSTALLED"}, sort_keys=True))
            return 0
        if args.command == "run":
            return execute_once(args.plan, args.plan_sha256_file).exit_status
        if args.command == "verify":
            value = verify_observer(args.plan, args.plan_sha256_file)
            print(json.dumps({"status": "VERIFIED", **value}, sort_keys=True))
            return 0
        if args.command == "status":
            value = observer_status(args.plan, args.plan_sha256_file)
            print(json.dumps({"status": value}, sort_keys=True))
            return 3 if value == "REVIEW_REQUIRED" else 0
        if args.command == "activate":
            activate(args.plan, args.plan_sha256_file,
                     execute=args.execute_launch_agent_activation)
            print(json.dumps({"status": "ACTIVE"}, sort_keys=True))
            return 0
        if args.command == "deactivate":
            value = deactivate(
                args.plan, args.plan_sha256_file,
                execute=args.execute_launch_agent_deactivation,
                remove_installed_plist=args.remove_installed_plist,
            )
            print(json.dumps({"status": value["status"]}, sort_keys=True))
            return 0
        if args.command == "sanitize":
            destination = sanitize_evidence(args.plan, args.plan_sha256_file, args.output_parent)
            print(json.dumps({"status": "SANITIZED", "summary_sha256": sha256_bytes(
                stable_read(destination / "summary.json"))}, sort_keys=True))
            return 0
        if args.command == "test-notification":
            test_notification(execute=args.execute_test_notification)
            print(json.dumps({"status": "COMMAND_ACCEPTED_NOT_VISIBLE_DELIVERY_PROOF"}, sort_keys=True))
            return 0
        raise NotificationError("unsupported command")
    except NotificationError as exc:
        if args.command != "run":
            print(json.dumps({"detail": str(exc), "status": "REVIEW_REQUIRED"}, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
