"""Fail-closed local scheduling for one explicit completion-monitor target.

Importing this module and using prepare, verify, status, or sanitize has no
network or launchd mutation side effect.  Preparation also renders the candidate
plist.  The scheduled ``run`` command imports the engine only after validating
the immutable plan and local execution context.  Activation and deactivation
require separate explicit execution flags.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from importlib import metadata
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


FORMAT = "task028f-production-completion-monitor-schedule-v1"
SCRIPT_RELATIVE = Path("scripts/completion_monitor_production_schedule.py")
PLAN_NAME = "schedule-plan.json"
PLAN_HASH_NAME = "schedule-plan.json.sha256"
PLIST_NAME = "launch-agent.plist"
PLIST_HASH_NAME = "launch-agent.plist.sha256"
TERMINAL_NAME = "terminal.json"
TERMINAL_HASH_NAME = "terminal.json.sha256"
INVOCATION_DIRECTORY = "scheduler-invocations"
LIFECYCLE_DIRECTORY = "lifecycle-events"
LABEL_PREFIX = "com.fpl-decision-engine.completion-monitor"
INTERVAL_SECONDS = 900
MAX_PLAN_LIFETIME = timedelta(days=14)
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_COMMAND_OUTPUT_BYTES = 128 * 1024
COMMAND_TIMEOUT_SECONDS = 15
SYSTEM_GIT = Path("/usr/bin/git")
SYSTEM_LAUNCHCTL = Path("/bin/launchctl")
SYSTEM_PLUTIL = Path("/usr/bin/plutil")
SEASON_PATTERN = re.compile(r"20[0-9]{2}-[0-9]{2}")
SNAPSHOT_PATTERN = re.compile(r"[0-9]{8}T[0-9]{6}\.[0-9]{6}Z")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SAFE_ID_PATTERN = re.compile(r"[0-9]{8}T[0-9]{6}\.[0-9]{6}Z-[0-9a-f]{32}")
PLIST_KEYS = {
    "KeepAlive",
    "Label",
    "ProgramArguments",
    "StandardErrorPath",
    "StandardOutPath",
    "StartInterval",
    "Umask",
    "WorkingDirectory",
}
PLAN_FIELDS = {
    "acknowledged_untracked_paths",
    "clean_data_root",
    "controller_path",
    "controller_sha256",
    "created_at",
    "evaluation_data_root",
    "expected_commit",
    "expires_at",
    "feature_data_root",
    "format",
    "gameweek",
    "installed_plist_path",
    "interval_seconds",
    "label",
    "not_before",
    "prediction_data_root",
    "prediction_snapshot_timestamp",
    "python_distribution_inventory_sha256",
    "python_path",
    "python_resolved_path",
    "python_version",
    "raw_data_root",
    "repository",
    "season",
    "task028b_control_data_root",
    "uid",
}
INVOCATION_FIELDS = {
    "ended_at",
    "error_class",
    "evaluation_directory",
    "format",
    "invocation_id",
    "plan_sha256",
    "process_exit_class",
    "realized_snapshot_timestamp",
    "schedule_status",
    "started_at",
    "target",
    "task028b_state_sha256",
    "task028b_status",
}
TERMINAL_FIELDS = {
    "error_class",
    "evaluation_directory",
    "format",
    "invocation_id",
    "invocation_sha256",
    "observed_at",
    "plan_sha256",
    "realized_snapshot_timestamp",
    "schedule_status",
    "target",
    "task028b_state_sha256",
    "task028b_status",
}
LIFECYCLE_FIELDS = {
    "command_evidence",
    "event_id",
    "event_type",
    "format",
    "installed_plist_path",
    "label",
    "observed_at",
    "plan_sha256",
    "status",
    "uid",
}
TERMINAL_STATUSES = {
    "TERMINAL_COMPLETE",
    "TERMINAL_REALIZED_COMPLETE",
    "TERMINAL_REVIEW_REQUIRED",
}
ACTIVE_STATUSES = {
    "ACTIVE_NOT_YET_DUE",
    "ACTIVE_RETRYABLE",
    "ACTIVE_WAITING",
}


class ScheduleError(Exception):
    """Bounded fail-closed error safe to report without private detail."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


@dataclass(frozen=True)
class MonitorCall:
    status: str | None = None
    error_class: str | None = None
    realized_snapshot_timestamp: str | None = None
    evaluation_directory: Path | None = None


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    exit_status: int
    engine_called: bool


@dataclass(frozen=True)
class PreparedSchedule:
    root: Path
    plan_path: Path
    plan_hash_path: Path
    plist_path: Path
    label: str


CommandRunner = Callable[[Sequence[str], int], CommandResult]
Clock = Callable[[], datetime]
MonitorRunner = Callable[[dict[str, object]], MonitorCall]
IdFactory = Callable[[datetime], str]
ActivationValidator = Callable[[dict[str, object], Path, str], None]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ScheduleError(message)


def canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ScheduleError("invalid canonical JSON value") from exc


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ScheduleError("could not hash schedule file") from exc
    return digest.hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
        raise ScheduleError(f"invalid {field}") from exc
    require(parsed.tzinfo == timezone.utc, f"invalid {field}")
    return parsed


def absolute_path(path: Path, *, must_exist: bool = True) -> Path:
    require(path.is_absolute(), "absolute path required")
    require(".." not in path.parts, "parent traversal is forbidden")
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
    path: Path, *, private: bool = True, executable: bool = False
) -> Path:
    path = absolute_path(path)
    require(not path.is_symlink(), "symlink file forbidden")
    require(path.resolve(strict=True) == path, "redirected file forbidden")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), "regular file required")
    require(info.st_uid == os.getuid(), "file owner mismatch")
    require(info.st_nlink == 1, "hard-linked file forbidden")
    require(info.st_size <= MAX_FILE_BYTES, "file exceeds size limit")
    if private:
        require(info.st_mode & 0o077 == 0, "owner-only file required")
    if executable:
        require(info.st_mode & 0o111 != 0, "executable file required")
    return path


def validated_python(path: Path) -> tuple[Path, Path]:
    invoked = absolute_path(path)
    info = invoked.lstat()
    require(info.st_uid == os.getuid(), "Python path owner mismatch")
    require(stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode), "invalid Python path")
    resolved = invoked.resolve(strict=True)
    target = resolved.stat()
    require(stat.S_ISREG(target.st_mode), "Python target must be a regular file")
    require(target.st_mode & 0o111 != 0, "Python target must be executable")
    return invoked, resolved


def publish(path: Path, body: bytes, mode: int = 0o600) -> None:
    require(not path.exists() and not path.is_symlink(), "refusing overwrite")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        offset = 0
        while offset < len(body):
            count = os.write(descriptor, body[offset:])
            require(count > 0, "short file write")
            offset += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def hash_body(path: Path, body: bytes) -> bytes:
    return f"{sha256_bytes(body)}  {path.name}\n".encode("ascii")


def publish_hashed(path: Path, body: bytes) -> None:
    publish(path, body)
    publish(path.with_name(path.name + ".sha256"), hash_body(path, body))


def read_bytes(path: Path, *, private: bool = True) -> bytes:
    path = owned_regular_file(path, private=private)
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise ScheduleError("could not read schedule file") from exc
    require(len(body) <= MAX_FILE_BYTES, "file exceeds size limit")
    return body


def read_hashed_bytes(path: Path, *, private: bool = True) -> bytes:
    body = read_bytes(path, private=private)
    digest = read_bytes(path.with_name(path.name + ".sha256"), private=private)
    require(digest == hash_body(path, body), "schedule file hash mismatch")
    return body


def read_hashed_json(
    path: Path, expected_fields: set[str], *, private: bool = True
) -> dict[str, object]:
    body = read_hashed_bytes(path, private=private)
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ScheduleError("invalid schedule JSON") from exc
    require(isinstance(value, dict), "schedule JSON object required")
    require(set(value) == expected_fields, "unexpected schedule JSON fields")
    require(canonical(value) == body, "noncanonical schedule JSON")
    return value


def command(args: Sequence[str], timeout: int = COMMAND_TIMEOUT_SECONDS) -> CommandResult:
    require(bool(args) and all(isinstance(item, str) for item in args), "invalid command")
    require(Path(args[0]).is_absolute(), "absolute executable required")
    try:
        result = subprocess.run(
            list(args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={},
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ScheduleError("bounded system command failed") from exc
    require(
        len(result.stdout) <= MAX_COMMAND_OUTPUT_BYTES
        and len(result.stderr) <= MAX_COMMAND_OUTPUT_BYTES,
        "system command output exceeded limit",
    )
    return CommandResult(result.returncode, result.stdout, result.stderr)


def git_output(
    repository: Path,
    args: Sequence[str],
    runner: CommandRunner,
    *,
    output_limit: int = MAX_COMMAND_OUTPUT_BYTES,
) -> bytes:
    result = runner(
        [str(SYSTEM_GIT), "-C", str(repository), *args], COMMAND_TIMEOUT_SECONDS
    )
    require(result.returncode == 0, "Git command failed")
    require(
        len(result.stdout) <= output_limit and len(result.stderr) <= MAX_COMMAND_OUTPUT_BYTES,
        "Git command output exceeded limit",
    )
    return result.stdout


def parse_git_status(raw: bytes) -> tuple[set[str], bool]:
    untracked: set[str] = set()
    tracked_change = False
    for record in raw.split(b"\0"):
        if not record:
            continue
        require(len(record) >= 4 and record[2:3] == b" ", "invalid Git status")
        try:
            path = record[3:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ScheduleError("non-UTF-8 Git path") from exc
        if record[:2] == b"??":
            untracked.add(path)
        else:
            tracked_change = True
    return untracked, tracked_change


def validate_untracked(values: object) -> list[str]:
    require(isinstance(values, list), "untracked paths must be a list")
    result: list[str] = []
    for item in values:
        require(isinstance(item, str) and item, "invalid untracked path")
        path = Path(item)
        require(not path.is_absolute() and ".." not in path.parts, "invalid untracked path")
        require(item == path.as_posix(), "untracked path must be normalized")
        result.append(item)
    require(result == sorted(set(result)), "untracked paths must be unique and sorted")
    return result


def repository_preflight(
    plan: dict[str, object], runner: CommandRunner = command
) -> None:
    repository = owned_directory(Path(str(plan["repository"])))
    commit = git_output(repository, ["rev-parse", "HEAD"], runner).decode().strip()
    require(commit == plan["expected_commit"], "repository commit mismatch")
    raw_status = git_output(
        repository,
        [
            "-c",
            "core.excludesFile=/dev/null",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
        runner,
        output_limit=MAX_FILE_BYTES,
    )
    untracked, tracked_change = parse_git_status(raw_status)
    require(not tracked_change, "tracked or staged repository change")
    expected_untracked = set(validate_untracked(plan["acknowledged_untracked_paths"]))
    require(untracked == expected_untracked, "unreviewed untracked repository path")
    controller = owned_regular_file(
        Path(str(plan["controller_path"])), private=False
    )
    body = controller.read_bytes()
    require(sha256_bytes(body) == plan["controller_sha256"], "controller bytes changed")
    committed = git_output(
        repository,
        ["show", f"{plan['expected_commit']}:{SCRIPT_RELATIVE.as_posix()}"],
        runner,
        output_limit=MAX_FILE_BYTES,
    )
    require(body == committed, "controller differs from reviewed commit")


def distribution_inventory() -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        version = distribution.version
        require(isinstance(name, str) and name.strip(), "distribution name missing")
        require(isinstance(version, str) and version.strip(), "distribution version missing")
        values.append({"name": name.strip().casefold(), "version": version.strip()})
    values.sort(key=lambda value: (value["name"], value["version"]))
    return values


def distribution_inventory_sha256() -> str:
    return sha256_bytes(canonical(distribution_inventory()))


def expected_label(season: str, gameweek: int) -> str:
    require(SEASON_PATTERN.fullmatch(season) is not None, "invalid season")
    require(type(gameweek) is int and 1 <= gameweek <= 38, "invalid gameweek")
    return f"{LABEL_PREFIX}.{season}.gw{gameweek}"


def user_launch_agents(uid: int) -> Path:
    try:
        home = Path(pwd.getpwuid(uid).pw_dir)
    except KeyError as exc:
        raise ScheduleError("schedule user does not exist") from exc
    return home / "Library" / "LaunchAgents"


def validate_plan_values(plan: dict[str, object], plan_path: Path) -> None:
    require(plan.get("format") == FORMAT, "plan format mismatch")
    season = plan.get("season")
    gameweek = plan.get("gameweek")
    require(isinstance(season, str), "invalid plan season")
    require(type(gameweek) is int, "invalid plan gameweek")
    label = expected_label(season, gameweek)
    require(plan.get("label") == label, "plan label mismatch")
    require(plan.get("interval_seconds") == INTERVAL_SECONDS, "plan interval mismatch")
    prediction = plan.get("prediction_snapshot_timestamp")
    require(
        prediction is None
        or (isinstance(prediction, str) and SNAPSHOT_PATTERN.fullmatch(prediction) is not None),
        "invalid prediction snapshot",
    )
    commit = plan.get("expected_commit")
    require(isinstance(commit, str) and COMMIT_PATTERN.fullmatch(commit) is not None, "invalid commit")
    for field in ("controller_sha256", "python_distribution_inventory_sha256"):
        value = plan.get(field)
        require(isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None, f"invalid {field}")
    uid = plan.get("uid")
    require(type(uid) is int and uid >= 0 and uid == os.getuid(), "plan user mismatch")
    repository = Path(str(plan.get("repository")))
    require(repository.is_absolute(), "invalid repository path")
    data_root = repository / "data"
    path_fields = (
        "raw_data_root",
        "clean_data_root",
        "feature_data_root",
        "prediction_data_root",
        "evaluation_data_root",
        "task028b_control_data_root",
    )
    for field in path_fields:
        path = Path(str(plan.get(field)))
        require(path.is_absolute() and within(path, data_root), f"invalid {field}")
    controller = Path(str(plan.get("controller_path")))
    require(controller == repository / SCRIPT_RELATIVE, "controller path mismatch")
    python_path = Path(str(plan.get("python_path")))
    python_resolved = Path(str(plan.get("python_resolved_path")))
    require(python_path.is_absolute() and python_resolved.is_absolute(), "invalid Python path")
    installed = Path(str(plan.get("installed_plist_path")))
    require(
        installed == user_launch_agents(uid) / f"{label}.plist",
        "installed plist path mismatch",
    )
    require(isinstance(plan.get("python_version"), str) and plan["python_version"], "invalid Python version")
    validate_untracked(plan.get("acknowledged_untracked_paths"))
    created = parse_utc(plan.get("created_at"), "creation time")
    not_before = parse_utc(plan.get("not_before"), "not-before time")
    expires = parse_utc(plan.get("expires_at"), "expiry time")
    require(not_before < expires, "plan time bounds are invalid")
    require(created < expires <= created + MAX_PLAN_LIFETIME, "plan lifetime is invalid")
    require(plan_path.name == PLAN_NAME, "unexpected plan filename")


def load_plan(plan_path: Path, plan_hash_path: Path | None = None) -> tuple[dict[str, object], str]:
    plan_path = absolute_path(plan_path)
    root = owned_directory(plan_path.parent, private=True)
    require(plan_path.parent == root, "invalid plan root")
    expected_hash_path = root / PLAN_HASH_NAME
    if plan_hash_path is not None:
        require(absolute_path(plan_hash_path) == expected_hash_path, "plan hash path mismatch")
    body = read_hashed_bytes(plan_path)
    try:
        value = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ScheduleError("invalid plan JSON") from exc
    require(isinstance(value, dict) and set(value) == PLAN_FIELDS, "unexpected plan fields")
    require(canonical(value) == body, "noncanonical plan JSON")
    validate_plan_values(value, plan_path)
    return value, sha256_bytes(body)


def validate_runtime(plan: dict[str, object]) -> None:
    python_path, resolved = validated_python(Path(str(plan["python_path"])))
    require(Path(os.path.abspath(sys.executable)) == python_path, "wrong Python interpreter")
    require(str(resolved) == plan["python_resolved_path"], "Python target changed")
    require(platform.python_version() == plan["python_version"], "Python version changed")
    require(
        distribution_inventory_sha256() == plan["python_distribution_inventory_sha256"],
        "Python distribution inventory changed",
    )


def validate_data_roots(plan: dict[str, object]) -> None:
    for field in (
        "raw_data_root",
        "clean_data_root",
        "feature_data_root",
        "prediction_data_root",
        "evaluation_data_root",
        "task028b_control_data_root",
    ):
        owned_directory(Path(str(plan[field])))


def plist_body(plan: dict[str, object], plan_path: Path) -> bytes:
    root = plan_path.parent
    values = {
        "KeepAlive": False,
        "Label": plan["label"],
        "ProgramArguments": [
            plan["python_path"],
            "-I",
            plan["controller_path"],
            "run",
            "--plan",
            str(plan_path),
            "--plan-sha256-file",
            str(root / PLAN_HASH_NAME),
        ],
        "StandardErrorPath": str(root / "worker.stderr.log"),
        "StandardOutPath": str(root / "worker.stdout.log"),
        "StartInterval": INTERVAL_SECONDS,
        "Umask": "077",
        "WorkingDirectory": plan["repository"],
    }
    require(set(values) == PLIST_KEYS, "unexpected plist fields")
    return plistlib.dumps(values, fmt=plistlib.FMT_XML, sort_keys=True)


def validate_plist(plan: dict[str, object], plan_path: Path) -> Path:
    path = plan_path.parent / PLIST_NAME
    body = read_hashed_bytes(path)
    require(body == plist_body(plan, plan_path), "plist differs from immutable plan")
    try:
        parsed = plistlib.loads(body)
    except plistlib.InvalidFileException as exc:
        raise ScheduleError("invalid plist") from exc
    require(isinstance(parsed, dict) and set(parsed) == PLIST_KEYS, "invalid plist fields")
    return path


def full_preflight(
    plan: dict[str, object], plan_path: Path, runner: CommandRunner = command
) -> None:
    validate_runtime(plan)
    validate_data_roots(plan)
    repository_preflight(plan, runner)
    validate_plist(plan, plan_path)


def unique_id(now: datetime) -> str:
    return f"{iso_utc(now).replace(':', '').replace('-', '')}-{uuid.uuid4().hex}"


def pair_presence(path: Path) -> bool:
    return path.exists() or path.is_symlink() or path.with_name(path.name + ".sha256").exists() or path.with_name(path.name + ".sha256").is_symlink()


def invocation_paths(root: Path) -> list[Path]:
    directory = owned_directory(root / INVOCATION_DIRECTORY, private=True)
    paths: list[Path] = []
    allowed: set[str] = set()
    for path in directory.iterdir():
        require(not path.is_symlink(), "symlink invocation entry forbidden")
        if path.name.endswith(".json"):
            require(SAFE_ID_PATTERN.fullmatch(path.stem) is not None, "invalid invocation filename")
            paths.append(path)
            allowed.add(path.name)
            allowed.add(path.name + ".sha256")
    require({path.name for path in directory.iterdir()} == allowed, "unexpected invocation entry")
    return sorted(paths, key=lambda path: path.name)


def validate_state_hashes(value: object) -> dict[str, str | None]:
    require(isinstance(value, dict), "invalid Task028B state hashes")
    expected = {"evaluation-receipt.json", "realized-receipt.json", "state.json"}
    require(set(value) == expected, "invalid Task028B state hash fields")
    result: dict[str, str | None] = {}
    for name, digest in value.items():
        require(
            digest is None
            or (isinstance(digest, str) and SHA256_PATTERN.fullmatch(digest) is not None),
            "invalid Task028B state hash",
        )
        result[str(name)] = digest
    return result


def validate_invocation(
    value: dict[str, object], path: Path, plan: dict[str, object], plan_sha: str
) -> None:
    require(value["format"] == FORMAT, "invocation format mismatch")
    require(value["invocation_id"] == path.stem, "invocation identity mismatch")
    require(value["plan_sha256"] == plan_sha, "invocation plan mismatch")
    require(value["target"] == {"gameweek": plan["gameweek"], "season": plan["season"]}, "invocation target mismatch")
    status = value["schedule_status"]
    require(
        isinstance(status, str) and status in ACTIVE_STATUSES | TERMINAL_STATUSES,
        "invalid invocation status",
    )
    exit_class = value["process_exit_class"]
    require(
        isinstance(exit_class, str)
        and exit_class in {"RETRYABLE", "REVIEW", "SUCCESS"},
        "invalid exit class",
    )
    start = parse_utc(value["started_at"], "invocation start")
    end = parse_utc(value["ended_at"], "invocation end")
    require(end >= start, "invocation clock moved backwards")
    validate_state_hashes(value["task028b_state_sha256"])
    for field in ("error_class", "task028b_status", "realized_snapshot_timestamp", "evaluation_directory"):
        require(value[field] is None or isinstance(value[field], str), f"invalid {field}")
    error_class = value["error_class"]
    task_status = value["task028b_status"]
    realized = value["realized_snapshot_timestamp"]
    evaluation = value["evaluation_directory"]
    if realized is not None:
        require(SNAPSHOT_PATTERN.fullmatch(realized) is not None, "invalid realized snapshot")
    if evaluation is not None:
        evaluation_path = Path(evaluation)
        require(
            evaluation_path.is_absolute()
            and within(evaluation_path, Path(str(plan["evaluation_data_root"]))),
            "invalid evaluation directory",
        )
    if status == "ACTIVE_NOT_YET_DUE":
        require(
            task_status is None and error_class is None and realized is None and evaluation is None,
            "not-due invocation payload mismatch",
        )
        require(value["process_exit_class"] == "SUCCESS", "not-due exit mismatch")
    elif status == "ACTIVE_WAITING":
        require(
            task_status == "WAITING"
            and error_class is None
            and realized is None
            and evaluation is None,
            "waiting invocation payload mismatch",
        )
        require(value["process_exit_class"] == "SUCCESS", "waiting exit mismatch")
    elif status == "ACTIVE_RETRYABLE":
        require(
            task_status is None
            and error_class in {"MonitorLockedError", "RetryableProbeError"}
            and realized is None
            and evaluation is None,
            "retryable invocation payload mismatch",
        )
        require(value["process_exit_class"] == "RETRYABLE", "retryable exit mismatch")
    elif status == "TERMINAL_REALIZED_COMPLETE":
        require(
            task_status == "REALIZED_COMPLETE"
            and error_class is None
            and realized is not None
            and evaluation is None,
            "realized terminal payload mismatch",
        )
        require(
            plan["prediction_snapshot_timestamp"] is None,
            "realized terminal prediction mismatch",
        )
        require(value["process_exit_class"] == "SUCCESS", "realized exit mismatch")
    elif status == "TERMINAL_COMPLETE":
        require(
            task_status == "COMPLETE"
            and error_class is None
            and realized is not None
            and evaluation is not None,
            "complete terminal payload mismatch",
        )
        require(
            plan["prediction_snapshot_timestamp"] is not None,
            "complete terminal prediction mismatch",
        )
        require(value["process_exit_class"] == "SUCCESS", "complete exit mismatch")
    else:
        require(
            task_status
            in {None, "COMPLETE", "REALIZED_COMPLETE", "REVIEW_REQUIRED", "WAITING"},
            "invalid review Task028B status",
        )
        require(value["process_exit_class"] == "REVIEW", "review exit mismatch")


def load_invocations(
    root: Path, plan: dict[str, object], plan_sha: str
) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for path in invocation_paths(root):
        value = read_hashed_json(path, INVOCATION_FIELDS)
        validate_invocation(value, path, plan, plan_sha)
        values.append(value)
    return values


def task028b_state_hashes(plan: dict[str, object]) -> dict[str, str | None]:
    target = (
        Path(str(plan["task028b_control_data_root"]))
        / str(plan["season"])
        / f"gameweek={plan['gameweek']}"
    )
    result: dict[str, str | None] = {}
    for name in ("evaluation-receipt.json", "realized-receipt.json", "state.json"):
        path = target / name
        if not path.exists() and not path.is_symlink():
            result[name] = None
            continue
        result[name] = sha256_file(owned_regular_file(path, private=False))
    return result


def publish_invocation(root: Path, payload: dict[str, object]) -> Path:
    require(set(payload) == INVOCATION_FIELDS, "invalid invocation fields")
    path = root / INVOCATION_DIRECTORY / f"{payload['invocation_id']}.json"
    publish_hashed(path, canonical(payload))
    return path


def terminal_path(root: Path) -> Path:
    return root / TERMINAL_NAME


def validate_terminal(
    root: Path, plan: dict[str, object], plan_sha: str
) -> dict[str, object]:
    value = read_hashed_json(terminal_path(root), TERMINAL_FIELDS)
    require(value["format"] == FORMAT, "terminal format mismatch")
    require(value["plan_sha256"] == plan_sha, "terminal plan mismatch")
    require(value["target"] == {"gameweek": plan["gameweek"], "season": plan["season"]}, "terminal target mismatch")
    require(
        isinstance(value["schedule_status"], str)
        and value["schedule_status"] in TERMINAL_STATUSES,
        "invalid terminal status",
    )
    parse_utc(value["observed_at"], "terminal observation")
    validate_state_hashes(value["task028b_state_sha256"])
    invocation_id = value["invocation_id"]
    require(isinstance(invocation_id, str) and SAFE_ID_PATTERN.fullmatch(invocation_id) is not None, "invalid terminal invocation")
    invocation_path = root / INVOCATION_DIRECTORY / f"{invocation_id}.json"
    invocation = read_hashed_json(invocation_path, INVOCATION_FIELDS)
    validate_invocation(invocation, invocation_path, plan, plan_sha)
    require(value["invocation_sha256"] == sha256_file(invocation_path), "terminal invocation hash mismatch")
    require(value["schedule_status"] == invocation["schedule_status"], "terminal status conflict")
    for field in (
        "error_class",
        "evaluation_directory",
        "realized_snapshot_timestamp",
        "task028b_state_sha256",
        "task028b_status",
    ):
        require(value[field] == invocation[field], "terminal invocation conflict")
    return value


def publish_terminal(
    root: Path,
    invocation_path: Path,
    invocation: dict[str, object],
    plan_sha: str,
) -> None:
    payload = {
        "error_class": invocation["error_class"],
        "evaluation_directory": invocation["evaluation_directory"],
        "format": FORMAT,
        "invocation_id": invocation["invocation_id"],
        "invocation_sha256": sha256_file(invocation_path),
        "observed_at": invocation["ended_at"],
        "plan_sha256": plan_sha,
        "realized_snapshot_timestamp": invocation["realized_snapshot_timestamp"],
        "schedule_status": invocation["schedule_status"],
        "target": invocation["target"],
        "task028b_state_sha256": invocation["task028b_state_sha256"],
        "task028b_status": invocation["task028b_status"],
    }
    require(set(payload) == TERMINAL_FIELDS, "invalid terminal fields")
    publish_hashed(terminal_path(root), canonical(payload))


def call_task028b(plan: dict[str, object]) -> MonitorCall:
    from fpl_decision_engine.completion_monitor import (  # noqa: PLC0415
        CompletionMonitorError,
        MonitorLockedError,
        RetryableProbeError,
        monitor_completion,
    )

    try:
        outcome = monitor_completion(
            season=str(plan["season"]),
            target_gameweek=int(plan["gameweek"]),
            prediction_snapshot_timestamp=plan["prediction_snapshot_timestamp"],
            raw_data_root=Path(str(plan["raw_data_root"])),
            clean_data_root=Path(str(plan["clean_data_root"])),
            feature_data_root=Path(str(plan["feature_data_root"])),
            prediction_data_root=Path(str(plan["prediction_data_root"])),
            evaluation_data_root=Path(str(plan["evaluation_data_root"])),
            control_data_root=Path(str(plan["task028b_control_data_root"])),
        )
    except RetryableProbeError:
        return MonitorCall(error_class="RetryableProbeError")
    except MonitorLockedError:
        return MonitorCall(error_class="MonitorLockedError")
    except CompletionMonitorError:
        return MonitorCall(error_class="CompletionMonitorError")
    except Exception:
        return MonitorCall(error_class="UnknownException")
    return MonitorCall(
        status=outcome.status,
        realized_snapshot_timestamp=outcome.realized_snapshot_timestamp,
        evaluation_directory=outcome.evaluation_directory,
    )


def outcome_mapping(
    call: MonitorCall, prediction: object
) -> tuple[str, int, str]:
    if call.error_class in {"MonitorLockedError", "RetryableProbeError"}:
        return "ACTIVE_RETRYABLE", 2, "RETRYABLE"
    if call.error_class is not None:
        return "TERMINAL_REVIEW_REQUIRED", 3, "REVIEW"
    if call.status == "WAITING":
        return "ACTIVE_WAITING", 0, "SUCCESS"
    if call.status == "REVIEW_REQUIRED":
        return "TERMINAL_REVIEW_REQUIRED", 3, "REVIEW"
    if call.status == "REALIZED_COMPLETE" and prediction is None:
        return "TERMINAL_REALIZED_COMPLETE", 0, "SUCCESS"
    if call.status == "COMPLETE" and prediction is not None:
        return "TERMINAL_COMPLETE", 0, "SUCCESS"
    return "TERMINAL_REVIEW_REQUIRED", 3, "REVIEW"


def normalized_monitor_call(call: MonitorCall) -> MonitorCall:
    if (
        (call.status is not None and not isinstance(call.status, str))
        or (call.error_class is not None and not isinstance(call.error_class, str))
        or (
            call.realized_snapshot_timestamp is not None
            and not isinstance(call.realized_snapshot_timestamp, str)
        )
        or (
            call.evaluation_directory is not None
            and not isinstance(call.evaluation_directory, Path)
        )
    ):
        return MonitorCall(error_class="OutcomePayloadMismatch")
    realized_ok = (
        call.realized_snapshot_timestamp is None
        or SNAPSHOT_PATTERN.fullmatch(call.realized_snapshot_timestamp) is not None
    )
    if call.error_class is not None:
        valid = (
            call.status is None
            and call.realized_snapshot_timestamp is None
            and call.evaluation_directory is None
        )
    elif call.status == "WAITING":
        valid = call.realized_snapshot_timestamp is None and call.evaluation_directory is None
    elif call.status == "REALIZED_COMPLETE":
        valid = call.realized_snapshot_timestamp is not None and call.evaluation_directory is None
    elif call.status == "COMPLETE":
        valid = call.realized_snapshot_timestamp is not None and call.evaluation_directory is not None
    elif call.status == "REVIEW_REQUIRED":
        valid = call.evaluation_directory is None
    else:
        valid = False
    if valid and realized_ok:
        return call
    known_status = (
        call.status
        if call.status in {"COMPLETE", "REALIZED_COMPLETE", "REVIEW_REQUIRED", "WAITING"}
        else None
    )
    return MonitorCall(status=known_status, error_class="OutcomePayloadMismatch")


def validated_evaluation_directory(call: MonitorCall, plan: dict[str, object]) -> str | None:
    if call.evaluation_directory is None:
        return None
    path = owned_directory(call.evaluation_directory)
    root = owned_directory(Path(str(plan["evaluation_data_root"])))
    require(within(path, root), "evaluation directory escaped configured root")
    return str(path)


def execute_once(
    plan_path: Path,
    plan_hash_path: Path,
    *,
    clock: Clock = utc_now,
    monitor_runner: MonitorRunner | None = None,
    runner: CommandRunner = command,
    id_factory: IdFactory = unique_id,
    after_monitor: Callable[[], None] | None = None,
    activation_validator: ActivationValidator | None = None,
) -> ExecutionResult:
    plan, plan_sha = load_plan(plan_path, plan_hash_path)
    root = plan_path.parent
    if pair_presence(terminal_path(root)):
        terminal = validate_terminal(root, plan, plan_sha)
        exit_status = 3 if terminal["schedule_status"] == "TERMINAL_REVIEW_REQUIRED" else 0
        return ExecutionResult("TERMINAL_QUIESCENT", exit_status, False)
    full_preflight(plan, plan_path, runner)
    selected_activation_validator = activation_validator or validate_active_authorization
    selected_activation_validator(plan, plan_path, plan_sha)
    invocations = load_invocations(root, plan, plan_sha)
    started = clock()
    require(started.tzinfo is not None, "timezone-aware clock required")
    started = started.astimezone(timezone.utc)
    if invocations:
        latest_start = max(parse_utc(value["started_at"], "invocation start") for value in invocations)
        require(started >= latest_start, "system clock moved backwards")
    not_before = parse_utc(plan["not_before"], "not-before time")
    expires = parse_utc(plan["expires_at"], "expiry time")
    engine_called = False
    if started < not_before:
        call = MonitorCall()
        schedule_status, exit_status, exit_class = "ACTIVE_NOT_YET_DUE", 0, "SUCCESS"
    elif started >= expires:
        call = MonitorCall(error_class="PlanExpired")
        schedule_status, exit_status, exit_class = "TERMINAL_REVIEW_REQUIRED", 3, "REVIEW"
    else:
        engine_called = True
        selected_runner = monitor_runner or call_task028b
        try:
            call = selected_runner(plan)
        except Exception:
            call = MonitorCall(error_class="UnknownException")
        call = (
            normalized_monitor_call(call)
            if isinstance(call, MonitorCall)
            else MonitorCall(error_class="UnknownOutcomeType")
        )
        schedule_status, exit_status, exit_class = outcome_mapping(
            call, plan["prediction_snapshot_timestamp"]
        )
        if (
            schedule_status == "TERMINAL_REVIEW_REQUIRED"
            and call.error_class is None
            and call.status not in {"REVIEW_REQUIRED"}
        ):
            call = MonitorCall(
                status=call.status,
                error_class="OutcomePlanMismatch",
                realized_snapshot_timestamp=call.realized_snapshot_timestamp,
                evaluation_directory=call.evaluation_directory,
            )
    ended = clock()
    require(ended.tzinfo is not None, "timezone-aware clock required")
    ended = ended.astimezone(timezone.utc)
    if ended < started:
        ended = started
        call = MonitorCall(
            status=call.status,
            error_class="ClockRollbackDuringInvocation",
            realized_snapshot_timestamp=call.realized_snapshot_timestamp,
            evaluation_directory=call.evaluation_directory,
        )
        schedule_status, exit_status, exit_class = "TERMINAL_REVIEW_REQUIRED", 3, "REVIEW"
    try:
        evaluation_directory = validated_evaluation_directory(call, plan)
        state_hashes = task028b_state_hashes(plan)
    except ScheduleError:
        call = MonitorCall(status=call.status, error_class="SchedulerEvidenceError")
        evaluation_directory = None
        state_hashes = {
            "evaluation-receipt.json": None,
            "realized-receipt.json": None,
            "state.json": None,
        }
        schedule_status, exit_status, exit_class = "TERMINAL_REVIEW_REQUIRED", 3, "REVIEW"
    invocation_id = id_factory(started)
    require(SAFE_ID_PATTERN.fullmatch(invocation_id) is not None, "invalid invocation ID")
    payload = {
        "ended_at": iso_utc(ended),
        "error_class": call.error_class,
        "evaluation_directory": evaluation_directory,
        "format": FORMAT,
        "invocation_id": invocation_id,
        "plan_sha256": plan_sha,
        "process_exit_class": exit_class,
        "realized_snapshot_timestamp": call.realized_snapshot_timestamp,
        "schedule_status": schedule_status,
        "started_at": iso_utc(started),
        "target": {"gameweek": plan["gameweek"], "season": plan["season"]},
        "task028b_state_sha256": state_hashes,
        "task028b_status": call.status,
    }
    invocation_path = publish_invocation(root, payload)
    if after_monitor is not None:
        after_monitor()
    if schedule_status in TERMINAL_STATUSES:
        publish_terminal(root, invocation_path, payload, plan_sha)
    return ExecutionResult(schedule_status, exit_status, engine_called)


def create_private_root(parent: Path, name: str) -> Path:
    parent = owned_directory(parent, private=True)
    require(re.fullmatch(r"[a-z0-9][a-z0-9-]{7,63}", name) is not None, "invalid schedule ID")
    root = parent / name
    require(not root.exists() and not root.is_symlink(), "schedule root already exists")
    root.mkdir(mode=0o700)
    return owned_directory(root, private=True)


def verify_ignored(repository: Path, path: Path, runner: CommandRunner) -> None:
    relative = path.relative_to(repository).as_posix()
    result = runner(
        [str(SYSTEM_GIT), "-C", str(repository), "check-ignore", "--quiet", "--", relative],
        COMMAND_TIMEOUT_SECONDS,
    )
    require(result.returncode == 0, "schedule root is not ignored by Git")


def prepare_schedule(
    *,
    repository: Path,
    expected_commit: str,
    python: Path,
    schedule_parent: Path,
    schedule_id: str,
    season: str,
    gameweek: int,
    prediction_snapshot_timestamp: str | None,
    raw_data_root: Path,
    clean_data_root: Path,
    feature_data_root: Path,
    prediction_data_root: Path,
    evaluation_data_root: Path,
    task028b_control_data_root: Path,
    not_before: str,
    expires_at: str,
    allowed_untracked: set[str],
    clock: Clock = utc_now,
    runner: CommandRunner = command,
) -> PreparedSchedule:
    require(platform.system() == "Darwin", "production schedules require macOS")
    repository = owned_directory(repository)
    require(COMMIT_PATTERN.fullmatch(expected_commit) is not None, "invalid expected commit")
    python_path, python_resolved = validated_python(python)
    require(Path(os.path.abspath(sys.executable)) == python_path, "prepare with the selected Python")
    controller = repository / SCRIPT_RELATIVE
    owned_regular_file(controller, private=False)
    schedule_parent = owned_directory(schedule_parent, private=True)
    require(within(schedule_parent, repository / "data"), "schedule parent must be under repository data")
    verify_ignored(repository, schedule_parent, runner)
    roots = {
        "raw_data_root": owned_directory(raw_data_root),
        "clean_data_root": owned_directory(clean_data_root),
        "feature_data_root": owned_directory(feature_data_root),
        "prediction_data_root": owned_directory(prediction_data_root),
        "evaluation_data_root": owned_directory(evaluation_data_root),
        "task028b_control_data_root": owned_directory(task028b_control_data_root),
    }
    require(all(within(path, repository / "data") for path in roots.values()), "data roots must remain under repository data")
    created = clock()
    require(created.tzinfo is not None, "timezone-aware clock required")
    created = created.astimezone(timezone.utc)
    uid = os.getuid()
    label = expected_label(season, gameweek)
    plan = {
        "acknowledged_untracked_paths": sorted(allowed_untracked),
        "clean_data_root": str(roots["clean_data_root"]),
        "controller_path": str(controller),
        "controller_sha256": sha256_file(controller),
        "created_at": iso_utc(created),
        "evaluation_data_root": str(roots["evaluation_data_root"]),
        "expected_commit": expected_commit,
        "expires_at": iso_utc(parse_utc(expires_at, "expiry time")),
        "feature_data_root": str(roots["feature_data_root"]),
        "format": FORMAT,
        "gameweek": gameweek,
        "installed_plist_path": str(user_launch_agents(uid) / f"{label}.plist"),
        "interval_seconds": INTERVAL_SECONDS,
        "label": label,
        "not_before": iso_utc(parse_utc(not_before, "not-before time")),
        "prediction_data_root": str(roots["prediction_data_root"]),
        "prediction_snapshot_timestamp": prediction_snapshot_timestamp,
        "python_distribution_inventory_sha256": distribution_inventory_sha256(),
        "python_path": str(python_path),
        "python_resolved_path": str(python_resolved),
        "python_version": platform.python_version(),
        "raw_data_root": str(roots["raw_data_root"]),
        "repository": str(repository),
        "season": season,
        "task028b_control_data_root": str(roots["task028b_control_data_root"]),
        "uid": uid,
    }
    require(set(plan) == PLAN_FIELDS, "invalid generated plan fields")
    temporary_plan_path = schedule_parent / PLAN_NAME
    validate_plan_values(plan, temporary_plan_path)
    repository_preflight(plan, runner)
    root = create_private_root(schedule_parent, schedule_id)
    try:
        plan_path = root / PLAN_NAME
        publish_hashed(plan_path, canonical(plan))
        (root / INVOCATION_DIRECTORY).mkdir(mode=0o700)
        (root / LIFECYCLE_DIRECTORY).mkdir(mode=0o700)
        publish(root / "worker.stdout.log", b"")
        publish(root / "worker.stderr.log", b"")
        publish_hashed(root / PLIST_NAME, plist_body(plan, plan_path))
    except Exception:
        raise
    return PreparedSchedule(root, plan_path, root / PLAN_HASH_NAME, root / PLIST_NAME, label)


def command_evidence(result: CommandResult) -> dict[str, object]:
    return {
        "returncode": result.returncode,
        "stderr_bytes": len(result.stderr),
        "stderr_sha256": sha256_bytes(result.stderr),
        "stdout_bytes": len(result.stdout),
        "stdout_sha256": sha256_bytes(result.stdout),
    }


def label_absent(result: CommandResult) -> bool:
    return result.returncode != 0 and b"Could not find service" in (result.stdout + result.stderr)


def lifecycle_paths(root: Path) -> list[Path]:
    directory = owned_directory(root / LIFECYCLE_DIRECTORY, private=True)
    paths: list[Path] = []
    allowed: set[str] = set()
    for path in directory.iterdir():
        require(not path.is_symlink(), "symlink lifecycle entry forbidden")
        if path.name.endswith(".json"):
            require(SAFE_ID_PATTERN.fullmatch(path.stem) is not None, "invalid lifecycle filename")
            paths.append(path)
            allowed.update({path.name, path.name + ".sha256"})
    require({path.name for path in directory.iterdir()} == allowed, "unexpected lifecycle entry")
    return sorted(paths, key=lambda path: path.name)


def load_lifecycle(root: Path, plan: dict[str, object], plan_sha: str) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for path in lifecycle_paths(root):
        value = read_hashed_json(path, LIFECYCLE_FIELDS)
        require(value["format"] == FORMAT, "lifecycle format mismatch")
        require(value["event_id"] == path.stem, "lifecycle identity mismatch")
        require(value["plan_sha256"] == plan_sha, "lifecycle plan mismatch")
        require(value["label"] == plan["label"] and value["uid"] == plan["uid"], "lifecycle target mismatch")
        require(value["installed_plist_path"] == plan["installed_plist_path"], "lifecycle plist mismatch")
        require(
            isinstance(value["event_type"], str)
            and value["event_type"] in {"ACTIVATION", "DEACTIVATION"},
            "invalid lifecycle type",
        )
        require(
            isinstance(value["status"], str)
            and value["status"] in {"ACTIVE", "DEACTIVATED", "REVIEW_REQUIRED"},
            "invalid lifecycle status",
        )
        evidence = value["command_evidence"]
        require(isinstance(evidence, list), "invalid command evidence")
        for item in evidence:
            require(
                isinstance(item, dict)
                and set(item)
                == {
                    "returncode",
                    "stderr_bytes",
                    "stderr_sha256",
                    "stdout_bytes",
                    "stdout_sha256",
                },
                "invalid command evidence",
            )
            require(type(item["returncode"]) is int, "invalid command return code")
            for prefix in ("stdout", "stderr"):
                require(
                    type(item[f"{prefix}_bytes"]) is int
                    and 0 <= item[f"{prefix}_bytes"] <= MAX_COMMAND_OUTPUT_BYTES,
                    "invalid command output size",
                )
                digest = item[f"{prefix}_sha256"]
                require(
                    isinstance(digest, str)
                    and SHA256_PATTERN.fullmatch(digest) is not None,
                    "invalid command output hash",
                )
        parse_utc(value["observed_at"], "lifecycle observation")
        values.append(value)
    return values


def publish_lifecycle(
    root: Path,
    plan: dict[str, object],
    plan_sha: str,
    *,
    event_type: str,
    status: str,
    results: list[CommandResult],
    observed_at: datetime,
    id_factory: IdFactory = unique_id,
) -> dict[str, object]:
    event_id = id_factory(observed_at)
    payload = {
        "command_evidence": [command_evidence(result) for result in results],
        "event_id": event_id,
        "event_type": event_type,
        "format": FORMAT,
        "installed_plist_path": plan["installed_plist_path"],
        "label": plan["label"],
        "observed_at": iso_utc(observed_at),
        "plan_sha256": plan_sha,
        "status": status,
        "uid": plan["uid"],
    }
    require(set(payload) == LIFECYCLE_FIELDS, "invalid lifecycle fields")
    publish_hashed(root / LIFECYCLE_DIRECTORY / f"{event_id}.json", canonical(payload))
    return payload


def service_target(plan: dict[str, object]) -> str:
    return f"gui/{plan['uid']}/{plan['label']}"


def validate_active_authorization(
    plan: dict[str, object], plan_path: Path, plan_sha: str
) -> None:
    lifecycle = load_lifecycle(plan_path.parent, plan, plan_sha)
    require(bool(lifecycle), "activation evidence is missing")
    require(
        all(
            event["event_type"] == "ACTIVATION" and event["status"] == "ACTIVE"
            for event in lifecycle
        ),
        "schedule activation is not active",
    )
    installed = Path(str(plan["installed_plist_path"]))
    installed_body = read_bytes(installed)
    candidate_body = read_hashed_bytes(plan_path.parent / PLIST_NAME)
    require(installed_body == candidate_body, "installed plist differs from reviewed candidate")


def activate(
    plan_path: Path,
    plan_hash_path: Path,
    *,
    execute: bool,
    runner: CommandRunner = command,
    clock: Clock = utc_now,
    id_factory: IdFactory = unique_id,
) -> dict[str, object]:
    require(execute, "explicit activation flag required")
    require(platform.system() == "Darwin", "activation requires macOS")
    plan, plan_sha = load_plan(plan_path, plan_hash_path)
    full_preflight(plan, plan_path, runner)
    candidate = validate_plist(plan, plan_path)
    require(
        not pair_presence(terminal_path(plan_path.parent)),
        "terminal schedule cannot be activated",
    )
    require(
        not load_invocations(plan_path.parent, plan, plan_sha),
        "schedule with invocation history cannot be activated",
    )
    require(
        not load_lifecycle(plan_path.parent, plan, plan_sha),
        "schedule with lifecycle history cannot be activated",
    )
    activation_time = clock()
    require(activation_time.tzinfo is not None, "timezone-aware clock required")
    activation_time = activation_time.astimezone(timezone.utc)
    require(
        parse_utc(plan["created_at"], "creation time") <= activation_time
        < parse_utc(plan["expires_at"], "expiry time"),
        "schedule activation time is outside its valid window",
    )
    installed = absolute_path(Path(str(plan["installed_plist_path"])), must_exist=False)
    parent = owned_directory(installed.parent)
    require(not installed.exists() and not installed.is_symlink(), "installed plist path already exists")
    target = service_target(plan)
    results: list[CommandResult] = []
    absent = runner([str(SYSTEM_LAUNCHCTL), "print", target], COMMAND_TIMEOUT_SECONDS)
    results.append(absent)
    require(label_absent(absent), "exact service label is not provably absent")
    lint = runner([str(SYSTEM_PLUTIL), "-lint", str(candidate)], COMMAND_TIMEOUT_SECONDS)
    results.append(lint)
    require(lint.returncode == 0, "plist validation failed")
    body = read_hashed_bytes(candidate)
    publish(installed, body)
    require(installed.parent == parent, "installed plist parent changed")
    try:
        bootstrap = runner(
            [str(SYSTEM_LAUNCHCTL), "bootstrap", f"gui/{plan['uid']}", str(installed)],
            COMMAND_TIMEOUT_SECONDS,
        )
        results.append(bootstrap)
        require(bootstrap.returncode == 0, "LaunchAgent bootstrap failed")
        loaded = runner([str(SYSTEM_LAUNCHCTL), "print", target], COMMAND_TIMEOUT_SECONDS)
        results.append(loaded)
        require(loaded.returncode == 0, "LaunchAgent load was not verified")
        publish_lifecycle(
            plan_path.parent,
            plan,
            plan_sha,
            event_type="ACTIVATION",
            status="ACTIVE",
            results=results,
            observed_at=activation_time,
            id_factory=id_factory,
        )
        kickstart = runner([str(SYSTEM_LAUNCHCTL), "kickstart", target], COMMAND_TIMEOUT_SECONDS)
        results.append(kickstart)
        require(kickstart.returncode == 0, "LaunchAgent kickstart failed")
    except ScheduleError as exc:
        cleanup_ok = False
        try:
            bootout = runner([str(SYSTEM_LAUNCHCTL), "bootout", target], COMMAND_TIMEOUT_SECONDS)
            results.append(bootout)
            absence = runner([str(SYSTEM_LAUNCHCTL), "print", target], COMMAND_TIMEOUT_SECONDS)
            results.append(absence)
            cleanup_ok = label_absent(absence)
            if cleanup_ok and installed.exists() and read_bytes(installed) == body:
                installed.unlink()
        except (OSError, ScheduleError):
            cleanup_ok = False
        publish_lifecycle(
            plan_path.parent,
            plan,
            plan_sha,
            event_type="ACTIVATION",
            status="REVIEW_REQUIRED",
            results=results,
            observed_at=clock(),
            id_factory=id_factory,
        )
        if cleanup_ok:
            raise ScheduleError("activation failed; exact rollback verified") from exc
        raise ScheduleError("activation failed; cleanup requires owner review") from exc
    return publish_lifecycle(
        plan_path.parent,
        plan,
        plan_sha,
        event_type="ACTIVATION",
        status="ACTIVE",
        results=results,
        observed_at=clock(),
        id_factory=id_factory,
    )


def deactivate(
    plan_path: Path,
    plan_hash_path: Path,
    *,
    execute: bool,
    remove_installed_plist: bool,
    runner: CommandRunner = command,
    clock: Clock = utc_now,
    id_factory: IdFactory = unique_id,
) -> dict[str, object]:
    require(execute, "explicit deactivation flag required")
    require(platform.system() == "Darwin", "deactivation requires macOS")
    plan, plan_sha = load_plan(plan_path, plan_hash_path)
    validate_plist(plan, plan_path)
    target = service_target(plan)
    results: list[CommandResult] = []
    state = runner([str(SYSTEM_LAUNCHCTL), "print", target], COMMAND_TIMEOUT_SECONDS)
    results.append(state)
    if state.returncode == 0:
        bootout = runner([str(SYSTEM_LAUNCHCTL), "bootout", target], COMMAND_TIMEOUT_SECONDS)
        results.append(bootout)
        require(bootout.returncode == 0, "exact LaunchAgent bootout failed")
    else:
        require(label_absent(state), "exact service state is uncertain")
    absent = runner([str(SYSTEM_LAUNCHCTL), "print", target], COMMAND_TIMEOUT_SECONDS)
    results.append(absent)
    require(label_absent(absent), "exact LaunchAgent absence was not verified")
    installed = Path(str(plan["installed_plist_path"]))
    if installed.exists() or installed.is_symlink():
        require(remove_installed_plist, "installed plist removal flag required")
        installed_body = read_bytes(installed)
        candidate_body = read_hashed_bytes(plan_path.parent / PLIST_NAME)
        require(installed_body == candidate_body, "installed plist differs from reviewed candidate")
        installed.unlink()
    status = "DEACTIVATED" if not installed.exists() and not installed.is_symlink() else "REVIEW_REQUIRED"
    return publish_lifecycle(
        plan_path.parent,
        plan,
        plan_sha,
        event_type="DEACTIVATION",
        status=status,
        results=results,
        observed_at=clock(),
        id_factory=id_factory,
    )


def schedule_status(
    plan_path: Path,
    plan_hash_path: Path,
    *,
    runner: CommandRunner = command,
    clock: Clock = utc_now,
) -> str:
    plan, plan_sha = load_plan(plan_path, plan_hash_path)
    full_preflight(plan, plan_path, runner)
    invocations = load_invocations(plan_path.parent, plan, plan_sha)
    lifecycle = load_lifecycle(plan_path.parent, plan, plan_sha)
    terminal: dict[str, object] | None = None
    if pair_presence(terminal_path(plan_path.parent)):
        terminal = validate_terminal(plan_path.parent, plan, plan_sha)
    state = runner([str(SYSTEM_LAUNCHCTL), "print", service_target(plan)], COMMAND_TIMEOUT_SECONDS)
    if state.returncode == 0:
        loaded = True
    elif label_absent(state):
        loaded = False
    else:
        return "REVIEW_REQUIRED"
    installed = Path(str(plan["installed_plist_path"]))
    installed_present = installed.exists() or installed.is_symlink()
    authorization_active = bool(lifecycle) and all(
        event["event_type"] == "ACTIVATION" and event["status"] == "ACTIVE"
        for event in lifecycle
    )
    if terminal is not None:
        if loaded and installed_present and authorization_active:
            return "TERMINAL_QUIESCENT"
        if (
            not loaded
            and not installed_present
            and any(
                event["event_type"] == "DEACTIVATION"
                and event["status"] == "DEACTIVATED"
                for event in lifecycle
            )
        ):
            return "DEACTIVATED"
        return "REVIEW_REQUIRED"
    if not loaded and not installed_present:
        if any(event["event_type"] == "DEACTIVATION" and event["status"] == "DEACTIVATED" for event in lifecycle):
            return "DEACTIVATED"
        if not lifecycle:
            return "NOT_ACTIVATED"
        return "REVIEW_REQUIRED"
    if not loaded or not installed_present:
        return "REVIEW_REQUIRED"
    if not authorization_active:
        return "REVIEW_REQUIRED"
    if not invocations:
        observed = clock()
        require(observed.tzinfo is not None, "timezone-aware clock required")
        return "ACTIVE_NOT_YET_DUE" if observed.astimezone(timezone.utc) < parse_utc(plan["not_before"], "not-before time") else "REVIEW_REQUIRED"
    latest = max(invocations, key=lambda value: str(value["started_at"]))
    return str(latest["schedule_status"]) if latest["schedule_status"] in ACTIVE_STATUSES else "REVIEW_REQUIRED"


def verify_schedule(
    plan_path: Path,
    plan_hash_path: Path,
    *,
    runner: CommandRunner = command,
) -> dict[str, int | str | bool]:
    plan, plan_sha = load_plan(plan_path, plan_hash_path)
    full_preflight(plan, plan_path, runner)
    invocations = load_invocations(plan_path.parent, plan, plan_sha)
    lifecycle = load_lifecycle(plan_path.parent, plan, plan_sha)
    terminal_present = pair_presence(terminal_path(plan_path.parent))
    if terminal_present:
        validate_terminal(plan_path.parent, plan, plan_sha)
    return {
        "invocations": len(invocations),
        "lifecycle_events": len(lifecycle),
        "plan_sha256": plan_sha,
        "terminal": terminal_present,
    }


def source_inventory(root: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() and not path.is_symlink():
            continue
        file_path = owned_regular_file(path, private=True)
        relative = file_path.relative_to(root).as_posix()
        body = file_path.read_bytes()
        result.append({"path": relative, "sha256": sha256_bytes(body), "size": len(body)})
    return result


def sanitized_plan(plan: dict[str, object]) -> dict[str, object]:
    result = dict(plan)
    for field in (
        "clean_data_root",
        "controller_path",
        "evaluation_data_root",
        "feature_data_root",
        "installed_plist_path",
        "prediction_data_root",
        "python_path",
        "python_resolved_path",
        "raw_data_root",
        "repository",
        "task028b_control_data_root",
    ):
        result[field] = f"<{field.upper()}>"
    acknowledged = canonical(result["acknowledged_untracked_paths"])
    result["acknowledged_untracked_paths"] = {
        "count": len(plan["acknowledged_untracked_paths"]),
        "sha256": sha256_bytes(acknowledged),
    }
    result["uid"] = "<UID>"
    return result


def sanitized_plist(body: bytes) -> bytes:
    value = plistlib.loads(body)
    value["ProgramArguments"] = [
        "<PYTHON_PATH>",
        "-I",
        "<CONTROLLER_PATH>",
        "run",
        "--plan",
        "<PLAN_PATH>",
        "--plan-sha256-file",
        "<PLAN_HASH_PATH>",
    ]
    value["WorkingDirectory"] = "<REPOSITORY>"
    value["StandardOutPath"] = "<STDOUT_PATH>"
    value["StandardErrorPath"] = "<STDERR_PATH>"
    return plistlib.dumps(value, fmt=plistlib.FMT_XML, sort_keys=True)


def publish_manifest(root: Path) -> Path:
    names = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.sha256"
    )
    body = "".join(f"{sha256_file(root / name)}  {name}\n" for name in names).encode("utf-8")
    path = root / "manifest.sha256"
    publish(path, body)
    return path


def verify_manifest(root: Path) -> bool:
    root = owned_directory(root, private=True)
    manifest = read_bytes(root / "manifest.sha256")
    expected: dict[str, str] = {}
    try:
        lines = manifest.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ScheduleError("invalid manifest encoding") from exc
    for line in lines:
        require(len(line) > 66 and line[64:66] == "  ", "invalid manifest line")
        digest, name = line[:64], line[66:]
        require(SHA256_PATTERN.fullmatch(digest) is not None, "invalid manifest hash")
        relative = Path(name)
        require(
            name == relative.as_posix()
            and not relative.is_absolute()
            and ".." not in relative.parts,
            "invalid manifest path",
        )
        require(name not in expected, "duplicate manifest path")
        expected[name] = digest
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.sha256"
    }
    require(set(expected) == actual, "manifest coverage mismatch")
    for name, digest in expected.items():
        require(
            sha256_file(owned_regular_file(root / name, private=True)) == digest,
            "manifest content mismatch",
        )
    return True


def sanitize_evidence(
    plan_path: Path,
    plan_hash_path: Path,
    output_parent: Path,
) -> Path:
    plan, plan_sha = load_plan(plan_path, plan_hash_path)
    root = plan_path.parent
    validate_plist(plan, plan_path)
    invocations = load_invocations(root, plan, plan_sha)
    lifecycle = load_lifecycle(root, plan, plan_sha)
    terminal = validate_terminal(root, plan, plan_sha) if pair_presence(terminal_path(root)) else None
    before = source_inventory(root)
    output_parent = owned_directory(output_parent, private=True)
    require(
        not within(output_parent, Path(str(plan["repository"])))
        and not within(output_parent, root)
        and not within(root, output_parent),
        "sanitized output must remain outside repository and source",
    )
    destination = output_parent / f"{root.name}-sanitized"
    require(not destination.exists() and not destination.is_symlink(), "sanitized destination exists")
    destination.mkdir(mode=0o700)
    publish(destination / "schedule-plan.redacted.json", canonical(sanitized_plan(plan)))
    publish(destination / "launch-agent.redacted.plist", sanitized_plist(read_hashed_bytes(root / PLIST_NAME)))
    redacted_invocations = []
    for value in invocations:
        copy = dict(value)
        if copy["evaluation_directory"] is not None:
            copy["evaluation_directory"] = "<EVALUATION_DIRECTORY>"
        redacted_invocations.append(copy)
    publish(destination / "invocations.redacted.json", canonical(redacted_invocations))
    if terminal is not None:
        redacted_terminal = dict(terminal)
        if redacted_terminal["evaluation_directory"] is not None:
            redacted_terminal["evaluation_directory"] = "<EVALUATION_DIRECTORY>"
        publish(destination / "terminal.redacted.json", canonical(redacted_terminal))
    redacted_lifecycle = []
    for value in lifecycle:
        copy = dict(value)
        copy["installed_plist_path"] = "<INSTALLED_PLIST_PATH>"
        copy["uid"] = "<UID>"
        redacted_lifecycle.append(copy)
    publish(destination / "lifecycle.redacted.json", canonical(redacted_lifecycle))
    after = source_inventory(root)
    require(before == after, "source evidence changed during sanitization")
    inventory_body = canonical(before)
    publish(destination / "source-inventory.json", inventory_body)
    publish(
        destination / "source-inventory-summary.json",
        canonical(
            {
                "file_count": len(before),
                "format": FORMAT,
                "plan_sha256": plan_sha,
                "source_inventory_sha256": sha256_bytes(inventory_body),
            }
        ),
    )
    publish_manifest(destination)
    verify_manifest(destination)
    return destination


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--repository", type=Path, required=True)
    prepare.add_argument("--expected-commit", required=True)
    prepare.add_argument("--python", type=Path, required=True)
    prepare.add_argument("--schedule-parent", type=Path, required=True)
    prepare.add_argument("--schedule-id", required=True)
    prepare.add_argument("--season", required=True)
    prepare.add_argument("--target-gameweek", type=int, required=True)
    prepare.add_argument("--prediction-snapshot-timestamp")
    prepare.add_argument("--raw-data-root", type=Path, required=True)
    prepare.add_argument("--clean-data-root", type=Path, required=True)
    prepare.add_argument("--feature-data-root", type=Path, required=True)
    prepare.add_argument("--prediction-data-root", type=Path, required=True)
    prepare.add_argument("--evaluation-data-root", type=Path, required=True)
    prepare.add_argument("--task028b-control-data-root", type=Path, required=True)
    prepare.add_argument("--not-before", required=True)
    prepare.add_argument("--expires-at", required=True)
    prepare.add_argument("--allow-untracked", action="append", default=[])
    for name in ("run", "verify", "status", "activate", "deactivate", "sanitize"):
        item = sub.add_parser(name)
        item.add_argument("--plan", type=Path, required=True)
        item.add_argument("--plan-sha256-file", type=Path, required=True)
        if name == "sanitize":
            item.add_argument("--output-parent", type=Path, required=True)
    sub.choices["activate"].add_argument(
        "--execute-launch-agent-activation", action="store_true", required=True
    )
    sub.choices["deactivate"].add_argument(
        "--execute-launch-agent-deactivation", action="store_true", required=True
    )
    sub.choices["deactivate"].add_argument(
        "--remove-installed-plist", action="store_true", required=True
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "run":
            return execute_once(args.plan, args.plan_sha256_file).exit_status
        if args.command == "verify":
            value = verify_schedule(args.plan, args.plan_sha256_file)
            print(json.dumps({"status": "VERIFIED", **value}, sort_keys=True))
            return 0
        if args.command == "status":
            value = schedule_status(args.plan, args.plan_sha256_file)
            print(json.dumps({"status": value}, sort_keys=True))
            return 3 if value == "REVIEW_REQUIRED" else 0
        if args.command == "sanitize":
            destination = sanitize_evidence(args.plan, args.plan_sha256_file, args.output_parent)
            print(
                json.dumps(
                    {
                        "manifest_sha256": sha256_file(destination / "manifest.sha256"),
                        "status": "SANITIZED",
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "activate":
            activate(
                args.plan,
                args.plan_sha256_file,
                execute=args.execute_launch_agent_activation,
            )
            print(json.dumps({"status": "ACTIVE"}))
            return 0
        if args.command == "deactivate":
            value = deactivate(
                args.plan,
                args.plan_sha256_file,
                execute=args.execute_launch_agent_deactivation,
                remove_installed_plist=args.remove_installed_plist,
            )
            print(json.dumps({"status": value["status"]}, sort_keys=True))
            return 0 if value["status"] == "DEACTIVATED" else 3
        if args.command == "prepare":
            prepared = prepare_schedule(
                repository=args.repository,
                expected_commit=args.expected_commit,
                python=args.python,
                schedule_parent=args.schedule_parent,
                schedule_id=args.schedule_id,
                season=args.season,
                gameweek=args.target_gameweek,
                prediction_snapshot_timestamp=args.prediction_snapshot_timestamp,
                raw_data_root=args.raw_data_root,
                clean_data_root=args.clean_data_root,
                feature_data_root=args.feature_data_root,
                prediction_data_root=args.prediction_data_root,
                evaluation_data_root=args.evaluation_data_root,
                task028b_control_data_root=args.task028b_control_data_root,
                not_before=args.not_before,
                expires_at=args.expires_at,
                allowed_untracked=set(args.allow_untracked),
            )
            print(
                json.dumps(
                    {
                        "label": prepared.label,
                        "plan_sha256": sha256_file(prepared.plan_path),
                        "status": "PREPARED_NOT_INSTALLED",
                    },
                    sort_keys=True,
                )
            )
            return 0
        raise ScheduleError("unsupported command")
    except ScheduleError as exc:
        if args.command == "run":
            print(f"completion schedule review required: {exc}", file=sys.stderr)
        else:
            print(json.dumps({"status": "REVIEW_REQUIRED", "detail": str(exc)}, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
