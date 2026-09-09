"""Offline synthetic harness for a separately authorized macOS launchd drill.

The scheduled worker is deliberately independent of the production engine. It
has no networking imports and reads configuration only from argv and files in
one validated synthetic drill root. Merely importing this module or using the
``prepare`` and ``render-production`` commands cannot invoke launchctl.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import signal
import stat
import subprocess
import sys
import time
from typing import Callable, Sequence


FORMAT = "task028d-completion-monitor-schedule-drill-v1"
DRILL_ID = re.compile(r"[a-z0-9][a-z0-9-]{7,31}")
SEASON = re.compile(r"20[0-9]{2}-[0-9]{2}")
SNAPSHOT = re.compile(r"[0-9]{8}T[0-9]{6}\.[0-9]{6}Z")
LABEL_PREFIX = "com.fpl-decision-engine.completion-monitor-drill"
PRODUCTION_LABEL_PREFIX = "com.fpl-decision-engine.completion-monitor"
SCRIPT_RELATIVE = Path("scripts/completion_monitor_schedule_drill.py")
DRILL_PARENT_RELATIVE = Path(
    "data/operations/completion-monitor-scheduling-drills"
)
DEFAULT_INTERVAL_SECONDS = 10
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_QUIET_SECONDS = 12
COMMAND_TIMEOUT_SECONDS = 15
MAX_COMMAND_OUTPUT_BYTES = 64 * 1024
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
SYSTEM_LAUNCHCTL = Path("/bin/launchctl")
SYSTEM_PLUTIL = Path("/usr/bin/plutil")
SYSTEM_GIT = Path("/usr/bin/git")
TERMINAL_OUTCOMES = {"COMPLETE", "REVIEW_REQUIRED"}
EXIT_BY_OUTCOME = {
    "WAITING": 0,
    "RETRYABLE": 2,
    "COMPLETE": 0,
    "REVIEW_REQUIRED": 3,
    "UNEXPECTED_SEQUENCE": 4,
    "INTERRUPTED": 4,
    "OVERLAP": 4,
}
PLIST_KEYS = {
    "Label",
    "ProgramArguments",
    "WorkingDirectory",
    "StandardOutPath",
    "StandardErrorPath",
    "Umask",
    "StartInterval",
    "KeepAlive",
}
METADATA_FIELDS = {
    "drill_id",
    "format",
    "interval_seconds",
    "macos_version",
    "python_relative",
    "python_version",
    "repository_commit",
    "scenario",
    "started_at",
}
RESULT_FIELDS = {
    "cleanup_ok",
    "detail",
    "expected_outcomes",
    "format",
    "loaded_state_verified",
    "long_invocation_ok",
    "maximum_concurrency",
    "observed_exit_statuses",
    "observed_outcomes",
    "quiet_window_ok",
    "sequence_ok",
    "status",
    "terminal_record_ok",
}
PREPARED_FILES = {
    "launch-agent.plist",
    "launch-agent.plist.sha256",
    "metadata.json",
    "outcome-script.json",
    "worker.stderr.log",
    "worker.stdout.log",
}


class DrillError(Exception):
    """Expected fail-closed drill error without sensitive detail."""


class DrillInterrupted(DrillError):
    """The driver or worker received an ordinary termination signal."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


@dataclass(frozen=True)
class PreparedDrill:
    root: Path
    label: str
    plist: Path
    service_target: str
    scenario: str
    expected_outcomes: tuple[str, ...]
    interval_seconds: int


CommandRunner = Callable[[Sequence[str], int], CommandResult]
Sleeper = Callable[[float], None]
Clock = Callable[[], datetime]
Monotonic = Callable[[], float]


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise DrillError("timezone-aware clock required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DrillError(message)


def absolute_path(value: Path, *, must_exist: bool = True) -> Path:
    require(value.is_absolute(), "absolute path required")
    require(".." not in value.parts, "parent traversal is forbidden")
    result = Path(os.path.abspath(value))
    if must_exist:
        require(result.exists(), "required path does not exist")
    return result


def private_directory(path: Path) -> Path:
    path = absolute_path(path)
    require(path.resolve(strict=True) == path, "symlink directory forbidden")
    info = path.lstat()
    require(not path.is_symlink(), "symlink directory forbidden")
    require(stat.S_ISDIR(info.st_mode), "private directory required")
    require(info.st_uid == os.getuid(), "directory owner mismatch")
    require(info.st_mode & 0o077 == 0, "owner-only directory required")
    return path


def regular_file(path: Path, *, private: bool = False) -> Path:
    path = absolute_path(path)
    require(path.resolve(strict=True) == path, "symlink file forbidden")
    info = path.lstat()
    require(not path.is_symlink(), "symlink file forbidden")
    require(stat.S_ISREG(info.st_mode), "regular file required")
    require(info.st_uid == os.getuid(), "file owner mismatch")
    if private:
        require(info.st_mode & 0o077 == 0, "owner-only file required")
    require(info.st_size <= MAX_EVIDENCE_BYTES, "file exceeds drill limit")
    return path


def within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def read_json(path: Path, expected_fields: set[str]) -> dict[str, object]:
    path = regular_file(path, private=True)
    try:
        body = path.read_bytes()
        require(len(body) <= MAX_EVIDENCE_BYTES, "JSON exceeds drill limit")
        value = json.loads(body)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DrillError("invalid drill JSON") from exc
    require(isinstance(value, dict), "JSON object required")
    require(set(value) == expected_fields, "unexpected JSON fields")
    try:
        require(canonical(value) == body, "noncanonical drill JSON")
    except (TypeError, ValueError) as exc:
        raise DrillError("invalid drill JSON value") from exc
    return value


def publish(path: Path, body: bytes, mode: int = 0o600) -> None:
    require(not path.exists() and not path.is_symlink(), "refusing overwrite")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        written = 0
        while written < len(body):
            count = os.write(descriptor, body[written:])
            require(count > 0, "short evidence write")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def command(args: Sequence[str], timeout: int = COMMAND_TIMEOUT_SECONDS) -> CommandResult:
    require(bool(args) and all(isinstance(arg, str) for arg in args), "invalid command")
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
        raise DrillError("bounded system command failed") from exc
    require(
        len(result.stdout) <= MAX_COMMAND_OUTPUT_BYTES
        and len(result.stderr) <= MAX_COMMAND_OUTPUT_BYTES,
        "system command output exceeded limit",
    )
    return CommandResult(result.returncode, result.stdout, result.stderr)


def git_output(repository: Path, args: Sequence[str], runner: CommandRunner) -> bytes:
    result = runner(
        [str(SYSTEM_GIT), "-C", str(repository), *args],
        COMMAND_TIMEOUT_SECONDS,
    )
    require(result.returncode == 0, "Git command failed")
    require(
        len(result.stdout) <= MAX_COMMAND_OUTPUT_BYTES
        and len(result.stderr) <= MAX_COMMAND_OUTPUT_BYTES,
        "Git command output exceeded limit",
    )
    return result.stdout


def parse_status(raw: bytes) -> tuple[set[str], bool]:
    untracked: set[str] = set()
    tracked_change = False
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            text = record.decode("utf-8", "strict")
        except UnicodeError as exc:
            raise DrillError("non-UTF-8 Git status") from exc
        require(len(text) >= 4 and text[2] == " ", "invalid Git status")
        status, path = text[:2], text[3:]
        require(path and "\0" not in path, "invalid Git path")
        if status == "??":
            untracked.add(path)
        else:
            tracked_change = True
    return untracked, tracked_change


def preflight_repository(
    repository: Path,
    expected_commit: str,
    allowed_untracked: set[str],
    runner: CommandRunner,
) -> None:
    repository = absolute_path(repository)
    require(repository.resolve(strict=True) == repository, "symlink repository forbidden")
    require((repository / ".git").exists(), "Git repository required")
    require(re.fullmatch(r"[0-9a-f]{40}", expected_commit) is not None, "full commit required")
    head = git_output(repository, ["rev-parse", "HEAD"], runner).decode().strip()
    require(head == expected_commit, "reviewed commit mismatch")
    raw_status = git_output(
        repository,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        runner,
    )
    untracked, tracked_change = parse_status(raw_status)
    require(not tracked_change, "tracked or staged repository change present")
    expected = set(allowed_untracked)
    require(untracked == expected, "unreviewed untracked repository path present")
    script = repository / SCRIPT_RELATIVE
    regular_file(script)
    committed = git_output(repository, ["show", f"{expected_commit}:{SCRIPT_RELATIVE.as_posix()}"], runner)
    require(script.read_bytes() == committed, "drill script differs from reviewed commit")


def validate_python(repository: Path, python: Path) -> Path:
    python = absolute_path(python)
    require(within(python, repository / ".venv"), "virtualenv interpreter required")
    require(python.is_file() and os.access(python, os.X_OK), "executable Python required")
    require(
        python.parent.resolve(strict=True) == python.parent,
        "symlinked interpreter parent forbidden",
    )
    target = python.resolve(strict=True)
    require(target.is_file() and os.access(target, os.X_OK), "invalid Python target")
    require(
        Path(sys.executable).resolve(strict=True) == target,
        "tool must run with the selected virtualenv Python",
    )
    return python


def outcome_steps(scenario: str, long_seconds: int) -> list[dict[str, object]]:
    require(1 <= long_seconds <= 120, "invalid synthetic duration")
    if scenario == "complete":
        return [
            {"exit_status": 0, "outcome": "WAITING", "sleep_seconds": 0},
            {"exit_status": 2, "outcome": "RETRYABLE", "sleep_seconds": long_seconds},
            {"exit_status": 0, "outcome": "COMPLETE", "sleep_seconds": 0},
        ]
    if scenario == "review":
        return [
            {"exit_status": 0, "outcome": "WAITING", "sleep_seconds": 0},
            {"exit_status": 3, "outcome": "REVIEW_REQUIRED", "sleep_seconds": 0},
        ]
    raise DrillError("unknown scenario")


def drill_plist(
    *,
    label: str,
    python: Path,
    repository: Path,
    root: Path,
    drill_id: str,
    scenario: str,
    interval_seconds: int,
) -> bytes:
    require(DRILL_ID.fullmatch(drill_id) is not None, "invalid drill ID")
    require(label == f"{LABEL_PREFIX}.{drill_id}.{scenario}", "invalid drill label")
    require(5 <= interval_seconds <= 60, "invalid drill interval")
    values = {
        "Label": label,
        "ProgramArguments": [
            str(python),
            str(repository / SCRIPT_RELATIVE),
            "worker",
            "--drill-root",
            str(root),
            "--drill-id",
            drill_id,
            "--scenario",
            scenario,
        ],
        "WorkingDirectory": str(repository),
        "StandardOutPath": str(root / "worker.stdout.log"),
        "StandardErrorPath": str(root / "worker.stderr.log"),
        "Umask": "077",
        "StartInterval": interval_seconds,
        "KeepAlive": False,
    }
    require(set(values) == PLIST_KEYS, "unexpected plist fields")
    return plistlib.dumps(values, fmt=plistlib.FMT_XML, sort_keys=True)


def production_plist(
    *,
    label: str,
    python: Path,
    repository: Path,
    season: str,
    gameweek: int,
    stdout_path: Path,
    stderr_path: Path,
    prediction_snapshot: str | None = None,
) -> bytes:
    require(SEASON.fullmatch(season) is not None, "invalid season")
    require(1 <= gameweek <= 38, "invalid gameweek")
    require(
        label == f"{PRODUCTION_LABEL_PREFIX}.{season}.gw{gameweek}",
        "invalid production label",
    )
    argv = [
        str(python),
        "-m",
        "fpl_decision_engine",
        "monitor-completion",
        "--season",
        season,
        "--target-gameweek",
        str(gameweek),
    ]
    if prediction_snapshot is not None:
        require(SNAPSHOT.fullmatch(prediction_snapshot) is not None, "invalid prediction snapshot")
        argv.extend(["--prediction-snapshot-timestamp", prediction_snapshot])
    values = {
        "Label": label,
        "ProgramArguments": argv,
        "WorkingDirectory": str(repository),
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "Umask": "077",
        "StartInterval": 900,
        "KeepAlive": False,
    }
    require(set(values) == PLIST_KEYS, "unexpected plist fields")
    return plistlib.dumps(values, fmt=plistlib.FMT_XML, sort_keys=True)


def create_private_root(parent: Path, drill_id: str, scenario: str) -> Path:
    parent = absolute_path(parent)
    require(parent.resolve(strict=True) == parent, "symlink drill parent forbidden")
    info = parent.lstat()
    require(stat.S_ISDIR(info.st_mode) and not parent.is_symlink(), "safe drill parent required")
    require(info.st_uid == os.getuid(), "drill parent owner mismatch")
    root = parent / f"{drill_id}-{scenario}"
    require(not root.exists() and not root.is_symlink(), "drill root already exists")
    os.mkdir(root, 0o700)
    private_directory(root)
    os.mkdir(root / "invocations", 0o700)
    return root


def sanitized_version(value: str, maximum: int = 120) -> str:
    value = value.strip().replace("\n", " ")
    require(0 < len(value) <= maximum, "invalid version text")
    require(all(0x20 <= ord(char) <= 0x7E for char in value), "invalid version text")
    return value


def prepare_drill(
    *,
    repository: Path,
    expected_commit: str,
    python: Path,
    drill_parent: Path,
    drill_id: str,
    scenario: str,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    allowed_untracked: set[str] | None = None,
    runner: CommandRunner = command,
) -> PreparedDrill:
    require(platform.system() == "Darwin", "macOS required")
    repository = absolute_path(repository)
    require(repository.resolve(strict=True) == repository, "symlink repository forbidden")
    python = validate_python(repository, python)
    require(
        drill_parent == repository / DRILL_PARENT_RELATIVE,
        "exact ignored drill parent required",
    )
    ignored = runner(
        [
            str(SYSTEM_GIT),
            "-C",
            str(repository),
            "check-ignore",
            "-q",
            "--no-index",
            str(drill_parent / "probe"),
        ],
        COMMAND_TIMEOUT_SECONDS,
    )
    require(ignored.returncode == 0, "drill parent is not ignored by Git")
    preflight_repository(
        repository,
        expected_commit,
        allowed_untracked or set(),
        runner,
    )
    root = create_private_root(drill_parent, drill_id, scenario)
    long_seconds = interval_seconds + 2
    steps = outcome_steps(scenario, long_seconds)
    script_payload = {
        "drill_id": drill_id,
        "format": FORMAT,
        "scenario": scenario,
        "steps": steps,
    }
    publish(root / "outcome-script.json", canonical(script_payload))
    label = f"{LABEL_PREFIX}.{drill_id}.{scenario}"
    plist_body = drill_plist(
        label=label,
        python=python,
        repository=repository,
        root=root,
        drill_id=drill_id,
        scenario=scenario,
        interval_seconds=interval_seconds,
    )
    publish(root / "launch-agent.plist", plist_body)
    publish(
        root / "launch-agent.plist.sha256",
        (sha256_bytes(plist_body) + "  launch-agent.plist\n").encode("ascii"),
    )
    metadata = {
        "drill_id": drill_id,
        "format": FORMAT,
        "interval_seconds": interval_seconds,
        "macos_version": sanitized_version(platform.mac_ver()[0] or "unknown"),
        "python_relative": python.relative_to(repository).as_posix(),
        "python_version": sanitized_version(platform.python_version()),
        "repository_commit": expected_commit,
        "scenario": scenario,
        "started_at": iso_utc(utc_now()),
    }
    publish(root / "metadata.json", canonical(metadata))
    for name in ("worker.stdout.log", "worker.stderr.log"):
        publish(root / name, b"")
    return PreparedDrill(
        root=root,
        label=label,
        plist=root / "launch-agent.plist",
        service_target=f"gui/{os.getuid()}/{label}",
        scenario=scenario,
        expected_outcomes=tuple(str(step["outcome"]) for step in steps),
        interval_seconds=interval_seconds,
    )


def load_prepared_drill(
    *,
    repository: Path,
    expected_commit: str,
    python: Path,
    drill_root: Path,
    allowed_untracked: set[str] | None = None,
    runner: CommandRunner = command,
) -> PreparedDrill:
    require(platform.system() == "Darwin", "macOS required")
    repository = absolute_path(repository)
    require(repository.resolve(strict=True) == repository, "symlink repository forbidden")
    python = validate_python(repository, python)
    preflight_repository(
        repository,
        expected_commit,
        allowed_untracked or set(),
        runner,
    )
    root = private_directory(drill_root)
    require(
        root.parent == repository / DRILL_PARENT_RELATIVE,
        "prepared root is outside the ignored drill parent",
    )
    metadata = read_json(root / "metadata.json", METADATA_FIELDS)
    require(
        metadata["format"] == FORMAT
        and metadata["repository_commit"] == expected_commit,
        "prepared metadata revision mismatch",
    )
    drill_id = metadata["drill_id"]
    scenario = metadata["scenario"]
    interval = metadata["interval_seconds"]
    require(
        isinstance(drill_id, str)
        and DRILL_ID.fullmatch(drill_id) is not None
        and scenario in {"complete", "review"}
        and type(interval) is int
        and 5 <= interval <= 60,
        "prepared metadata is invalid",
    )
    require(root.name == f"{drill_id}-{scenario}", "prepared root identity mismatch")
    require(
        metadata["python_relative"] == python.relative_to(repository).as_posix(),
        "prepared Python mismatch",
    )
    steps = load_outcome_script(root, drill_id, scenario)
    expected_outcomes = tuple(str(step["outcome"]) for step in steps)
    label = f"{LABEL_PREFIX}.{drill_id}.{scenario}"
    expected_plist = drill_plist(
        label=label,
        python=python,
        repository=repository,
        root=root,
        drill_id=drill_id,
        scenario=scenario,
        interval_seconds=interval,
    )
    plist = regular_file(root / "launch-agent.plist", private=True)
    require(plist.read_bytes() == expected_plist, "prepared plist differs from reviewed rendering")
    hash_file = regular_file(root / "launch-agent.plist.sha256", private=True)
    require(
        hash_file.read_bytes()
        == (sha256_bytes(expected_plist) + "  launch-agent.plist\n").encode("ascii"),
        "prepared plist hash mismatch",
    )
    require(
        {path.name for path in root.iterdir()} == PREPARED_FILES | {"invocations"},
        "unexpected prepared path",
    )
    for name in PREPARED_FILES:
        regular_file(root / name, private=True)
    require(
        {path.name for path in root.iterdir() if path.is_dir()} == {"invocations"}
        and not invocation_files(root),
        "prepared invocation directory is not empty",
    )
    for name in ("worker.stdout.log", "worker.stderr.log"):
        require((root / name).read_bytes() == b"", "prepared worker log is not empty")
    return PreparedDrill(
        root=root,
        label=label,
        plist=plist,
        service_target=f"gui/{os.getuid()}/{label}",
        scenario=scenario,
        expected_outcomes=expected_outcomes,
        interval_seconds=interval,
    )


def load_outcome_script(root: Path, drill_id: str, scenario: str) -> list[dict[str, object]]:
    metadata = read_json(
        root / "metadata.json",
        METADATA_FIELDS,
    )
    require(
        metadata["format"] == FORMAT
        and metadata["drill_id"] == drill_id
        and metadata["scenario"] == scenario,
        "metadata identity mismatch",
    )
    interval = metadata["interval_seconds"]
    require(type(interval) is int and 5 <= interval <= 60, "invalid metadata interval")
    value = read_json(
        root / "outcome-script.json",
        {"drill_id", "format", "scenario", "steps"},
    )
    require(
        value["format"] == FORMAT
        and value["drill_id"] == drill_id
        and value["scenario"] == scenario,
        "outcome script identity mismatch",
    )
    steps = value["steps"]
    require(isinstance(steps, list), "invalid outcome steps")
    expected = outcome_steps(scenario, interval + 2)
    require(steps == expected, "invalid outcome sequence")
    validated: list[dict[str, object]] = []
    for index, step in enumerate(steps):
        require(isinstance(step, dict), "invalid outcome step")
        require(set(step) == {"exit_status", "outcome", "sleep_seconds"}, "invalid outcome step")
        outcome = step["outcome"]
        exit_status = step["exit_status"]
        sleep_seconds = step["sleep_seconds"]
        require(isinstance(outcome, str) and outcome == expected[index]["outcome"], "invalid outcome")
        require(type(exit_status) is int and exit_status == EXIT_BY_OUTCOME[outcome], "invalid exit status")
        require(type(sleep_seconds) is int and 0 <= sleep_seconds <= 120, "invalid sleep duration")
        validated.append(step)
    return validated


def invocation_files(root: Path) -> list[Path]:
    directory = private_directory(root / "invocations")
    files = sorted(directory.glob("*.json"))
    for path in files:
        regular_file(path, private=True)
    return files


def event_payload(
    *,
    sequence: int,
    outcome: str,
    exit_status: int,
    started_at: datetime,
    ended_at: datetime,
    duration: float,
    previous_sha256: str | None,
    lock_result: str,
) -> dict[str, object]:
    return {
        "duration_seconds": round(duration, 6),
        "ended_at": iso_utc(ended_at),
        "exit_status": exit_status,
        "lock_result": lock_result,
        "outcome": outcome,
        "pid": os.getpid(),
        "previous_sha256": previous_sha256,
        "sequence": sequence,
        "started_at": iso_utc(started_at),
    }


def publish_event(root: Path, payload: dict[str, object]) -> Path:
    sequence = payload["sequence"]
    require(type(sequence) is int and sequence >= 1, "invalid event sequence")
    path = root / "invocations" / f"{sequence:04d}.json"
    publish(path, canonical(payload))
    return path


def worker(
    *,
    drill_root: Path,
    drill_id: str,
    scenario: str,
    sleeper: Sleeper = time.sleep,
    clock: Clock = utc_now,
    monotonic: Monotonic = time.monotonic,
) -> int:
    root = private_directory(drill_root)
    require(DRILL_ID.fullmatch(drill_id) is not None, "invalid drill ID")
    require(root.name == f"{drill_id}-{scenario}", "drill root identity mismatch")
    steps = load_outcome_script(root, drill_id, scenario)
    lock = root / ".synthetic-worker.lock"
    started = clock()
    begin = monotonic()
    try:
        lock_fd = os.open(
            lock,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except FileExistsError:
        existing = invocation_files(root)
        previous = sha256_file(existing[-1]) if existing else None
        payload = event_payload(
            sequence=len(existing) + 1,
            outcome="OVERLAP",
            exit_status=4,
            started_at=started,
            ended_at=clock(),
            duration=max(0.0, monotonic() - begin),
            previous_sha256=previous,
            lock_result="blocked",
        )
        publish_event(root, payload)
        return 4
    os.close(lock_fd)
    try:
        existing = invocation_files(root)
        sequence = len(existing) + 1
        previous = sha256_file(existing[-1]) if existing else None
        if sequence > len(steps):
            payload = event_payload(
                sequence=sequence,
                outcome="UNEXPECTED_SEQUENCE",
                exit_status=4,
                started_at=started,
                ended_at=clock(),
                duration=max(0.0, monotonic() - begin),
                previous_sha256=previous,
                lock_result="acquired",
            )
            publish_event(root, payload)
            return 4
        step = steps[sequence - 1]
        outcome = str(step["outcome"])
        exit_status = int(step["exit_status"])
        try:
            sleeper(int(step["sleep_seconds"]))
            ended = clock()
            payload = event_payload(
                sequence=sequence,
                outcome=outcome,
                exit_status=exit_status,
                started_at=started,
                ended_at=ended,
                duration=max(0.0, monotonic() - begin),
                previous_sha256=previous,
                lock_result="acquired",
            )
        except DrillInterrupted:
            payload = event_payload(
                sequence=sequence,
                outcome="INTERRUPTED",
                exit_status=4,
                started_at=started,
                ended_at=clock(),
                duration=max(0.0, monotonic() - begin),
                previous_sha256=previous,
                lock_result="acquired",
            )
            exit_status = 4
            outcome = "INTERRUPTED"
        event = publish_event(root, payload)
        if outcome in TERMINAL_OUTCOMES:
            publish(
                root / "terminal.json",
                canonical(
                    {
                        "event_sha256": sha256_file(event),
                        "format": FORMAT,
                        "outcome": outcome,
                        "scenario": scenario,
                        "sequence": sequence,
                    }
                ),
            )
        return exit_status
    finally:
        lock.unlink(missing_ok=True)


def install_signal_handlers() -> dict[int, object]:
    previous: dict[int, object] = {}

    def interrupted(signum: int, _frame: object) -> None:
        raise DrillInterrupted(f"signal {signum}")

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, interrupted)
    return previous


def restore_signal_handlers(previous: dict[int, object]) -> None:
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def label_absent(result: CommandResult) -> bool:
    return result.returncode != 0 and b"Could not find service" in (
        result.stdout + result.stderr
    )


def write_command_evidence(path: Path, result: CommandResult) -> None:
    body = result.stdout + result.stderr
    require(len(body) <= MAX_COMMAND_OUTPUT_BYTES, "command evidence too large")
    publish(path, body)


def wait_for_terminal(
    root: Path,
    timeout_seconds: int,
    sleeper: Sleeper,
    monotonic: Monotonic,
) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if (root / "terminal.json").is_file():
            return
        sleeper(0.25)
    raise DrillError("terminal record missing by deadline")


def process_absent(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def load_events(root: Path) -> list[dict[str, object]]:
    fields = {
        "duration_seconds",
        "ended_at",
        "exit_status",
        "lock_result",
        "outcome",
        "pid",
        "previous_sha256",
        "sequence",
        "started_at",
    }
    result: list[dict[str, object]] = []
    previous: str | None = None
    for index, path in enumerate(invocation_files(root), start=1):
        value = read_json(path, fields)
        require(
            type(value["sequence"]) is int and value["sequence"] == index,
            "invocation sequence mismatch",
        )
        require(value["previous_sha256"] == previous, "invocation hash chain mismatch")
        outcome = value["outcome"]
        exit_status = value["exit_status"]
        duration = value["duration_seconds"]
        pid = value["pid"]
        require(
            isinstance(outcome, str)
            and outcome in EXIT_BY_OUTCOME
            and type(exit_status) is int
            and exit_status == EXIT_BY_OUTCOME[outcome],
            "invalid invocation outcome",
        )
        require(
            isinstance(duration, (int, float))
            and not isinstance(duration, bool)
            and 0 <= duration <= 900,
            "invalid invocation duration",
        )
        require(type(pid) is int and pid > 0, "invalid invocation PID")
        require(
            value["lock_result"] in {"acquired", "blocked"}
            and (value["lock_result"] == "blocked") == (outcome == "OVERLAP"),
            "invalid invocation lock result",
        )
        timestamps: list[datetime] = []
        for field in ("started_at", "ended_at"):
            timestamp = value[field]
            try:
                require(
                    isinstance(timestamp, str) and timestamp.endswith("Z"),
                    "invalid invocation timestamp",
                )
                parsed = datetime.fromisoformat(timestamp[:-1] + "+00:00")
                require(parsed.tzinfo == timezone.utc, "invalid invocation timestamp")
            except (TypeError, ValueError) as exc:
                raise DrillError("invalid invocation timestamp") from exc
            timestamps.append(parsed)
        require(timestamps[1] >= timestamps[0], "invocation time moved backwards")
        previous = sha256_file(path)
        result.append(value)
    return result


def terminal_record_ok(
    prepared: PreparedDrill, events: list[dict[str, object]]
) -> bool:
    path = prepared.root / "terminal.json"
    if not path.is_file() or not events:
        return False
    try:
        value = read_json(
            path,
            {"event_sha256", "format", "outcome", "scenario", "sequence"},
        )
    except DrillError:
        return False
    final = invocation_files(prepared.root)[-1]
    return (
        value["format"] == FORMAT
        and value["scenario"] == prepared.scenario
        and value["outcome"] == events[-1]["outcome"]
        and value["outcome"] in TERMINAL_OUTCOMES
        and value["sequence"] == events[-1]["sequence"]
        and value["event_sha256"] == sha256_file(final)
    )


def finalize_evidence(
    prepared: PreparedDrill,
    *,
    status: str,
    cleanup_ok: bool,
    quiet_ok: bool,
    loaded_ok: bool,
    detail: str,
) -> None:
    events = load_events(prepared.root)
    invocations = b"".join(canonical(event) + b"\n" for event in events)
    publish(prepared.root / "invocations.jsonl", invocations)
    outcomes = [event["outcome"] for event in events]
    concurrency_ok = all(event["outcome"] != "OVERLAP" for event in events)
    sequence_ok = outcomes == list(prepared.expected_outcomes)
    terminal_ok = terminal_record_ok(prepared, events)
    long_invocation_ok = (
        prepared.scenario != "complete"
        or (
            len(events) >= 2
            and isinstance(events[1]["duration_seconds"], (int, float))
            and not isinstance(events[1]["duration_seconds"], bool)
            and events[1]["duration_seconds"] >= prepared.interval_seconds
        )
    )
    result = {
        "cleanup_ok": cleanup_ok,
        "detail": detail,
        "expected_outcomes": list(prepared.expected_outcomes),
        "format": FORMAT,
        "loaded_state_verified": loaded_ok,
        "long_invocation_ok": long_invocation_ok,
        "maximum_concurrency": (1 if events else 0) if concurrency_ok else 2,
        "observed_exit_statuses": [event["exit_status"] for event in events],
        "observed_outcomes": outcomes,
        "quiet_window_ok": quiet_ok,
        "sequence_ok": sequence_ok,
        "status": (
            status
            if cleanup_ok
            and quiet_ok
            and loaded_ok
            and concurrency_ok
            and sequence_ok
            else "FAILED"
        ),
        "terminal_record_ok": terminal_ok,
    }
    if not terminal_ok or not long_invocation_ok:
        result["status"] = "FAILED"
    publish(prepared.root / "result.json", canonical(result))
    names = sorted(
        path.relative_to(prepared.root).as_posix()
        for path in prepared.root.rglob("*")
        if path.is_file() and path.name != "manifest.sha256"
    )
    lines = [f"{sha256_file(prepared.root / name)}  {name}\n" for name in names]
    publish(prepared.root / "manifest.sha256", "".join(lines).encode("utf-8"))


def execute_drill(
    prepared: PreparedDrill,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    quiet_seconds: int = DEFAULT_QUIET_SECONDS,
    runner: CommandRunner = command,
    sleeper: Sleeper = time.sleep,
    monotonic: Monotonic = time.monotonic,
) -> bool:
    require(30 <= timeout_seconds <= 900, "invalid drill timeout")
    require(
        prepared.interval_seconds < quiet_seconds <= 120,
        "quiet window must exceed one interval",
    )
    domain = f"gui/{os.getuid()}"
    target = prepared.service_target
    absent = runner([str(SYSTEM_LAUNCHCTL), "print", target], COMMAND_TIMEOUT_SECONDS)
    require(label_absent(absent), "exact service label is not provably absent")
    write_command_evidence(prepared.root / "launchctl-initial-absent.txt", absent)
    bootstrap_attempted = False
    loaded_ok = False
    cleanup_ok = False
    quiet_ok = False
    detail = "drill did not complete"
    before_quiet = 0
    previous_handlers = install_signal_handlers()
    try:
        lint = runner(
            [str(SYSTEM_PLUTIL), "-lint", str(prepared.plist)],
            COMMAND_TIMEOUT_SECONDS,
        )
        require(lint.returncode == 0, "plist validation failed")
        bootstrap_attempted = True
        bootstrap = runner(
            [str(SYSTEM_LAUNCHCTL), "bootstrap", domain, str(prepared.plist)],
            COMMAND_TIMEOUT_SECONDS,
        )
        require(bootstrap.returncode == 0, "launchctl bootstrap failed")
        loaded = runner(
            [str(SYSTEM_LAUNCHCTL), "print", target], COMMAND_TIMEOUT_SECONDS
        )
        require(loaded.returncode == 0, "loaded service cannot be inspected")
        write_command_evidence(prepared.root / "launchctl-loaded.txt", loaded)
        loaded_ok = True
        kickstart = runner(
            [str(SYSTEM_LAUNCHCTL), "kickstart", target], COMMAND_TIMEOUT_SECONDS
        )
        require(kickstart.returncode == 0, "launchctl kickstart failed")
        wait_for_terminal(prepared.root, timeout_seconds, sleeper, monotonic)
        detail = "terminal synthetic outcome observed"
    except (DrillError, OSError) as exc:
        detail = str(exc)
    finally:
        try:
            if bootstrap_attempted:
                try:
                    runner(
                        [str(SYSTEM_LAUNCHCTL), "bootout", target],
                        COMMAND_TIMEOUT_SECONDS,
                    )
                except (DrillError, OSError):
                    pass
            try:
                after = runner(
                    [str(SYSTEM_LAUNCHCTL), "print", target],
                    COMMAND_TIMEOUT_SECONDS,
                )
            except (DrillError, OSError):
                after = CommandResult(255, b"", b"exact-label absence check failed\n")
            cleanup_ok = label_absent(after)
            try:
                write_command_evidence(
                    prepared.root / "launchctl-unloaded.txt", after
                )
            except (DrillError, OSError):
                cleanup_ok = False
            try:
                before_quiet = len(invocation_files(prepared.root))
                sleeper(quiet_seconds)
                quiet_ok = (
                    len(invocation_files(prepared.root)) == before_quiet
                    and not (prepared.root / ".synthetic-worker.lock").exists()
                )
            except (DrillError, OSError):
                quiet_ok = False
            try:
                pids = [event.get("pid") for event in load_events(prepared.root)]
                cleanup_ok = cleanup_ok and all(
                    type(pid) is int and process_absent(pid) for pid in pids
                )
            except (DrillError, OSError):
                cleanup_ok = False
        finally:
            restore_signal_handlers(previous_handlers)
    if not cleanup_ok or not quiet_ok:
        publish(
            prepared.root / "CLEANUP_REQUIRED.json",
            canonical(
                {
                    "cleanup_ok": cleanup_ok,
                    "detail": detail,
                    "format": FORMAT,
                    "quiet_window_ok": quiet_ok,
                    "service_target": target,
                }
            ),
        )
        if not cleanup_ok:
            print(
                f"Manual cleanup required: {SYSTEM_LAUNCHCTL} bootout {target}",
                file=sys.stderr,
            )
        return False
    success = True
    finalize_evidence(
        prepared,
        status="PASSED" if success else "FAILED",
        cleanup_ok=cleanup_ok,
        quiet_ok=quiet_ok,
        loaded_ok=loaded_ok,
        detail=detail,
    )
    return success and json.loads((prepared.root / "result.json").read_bytes())["status"] == "PASSED"


def verify_manifest(root: Path) -> bool:
    root = private_directory(root)
    manifest = regular_file(root / "manifest.sha256", private=True).read_text("utf-8")
    seen: set[str] = set()
    for line in manifest.splitlines():
        digest, separator, name = line.partition("  ")
        require(separator == "  " and re.fullmatch(r"[0-9a-f]{64}", digest) is not None, "invalid manifest line")
        require(name and name not in seen, "duplicate manifest path")
        seen.add(name)
        path = absolute_path(root / name)
        require(within(path, root), "manifest path escape")
        regular_file(path, private=True)
        require(sha256_file(path) == digest, "evidence hash mismatch")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.sha256"
    }
    require(seen == actual, "manifest coverage mismatch")
    return True


def sanitized_plist(
    source: Path,
    *,
    drill_root: Path,
    metadata: dict[str, object],
) -> bytes:
    source = regular_file(source, private=True)
    try:
        value = plistlib.loads(source.read_bytes())
    except (plistlib.InvalidFileException, OSError) as exc:
        raise DrillError("invalid source plist") from exc
    require(isinstance(value, dict) and set(value) == PLIST_KEYS, "invalid source plist")
    argv = value.get("ProgramArguments")
    require(isinstance(argv, list) and len(argv) == 9, "invalid synthetic argv")
    repository = value.get("WorkingDirectory")
    python_relative = metadata.get("python_relative")
    drill_id = metadata.get("drill_id")
    scenario = metadata.get("scenario")
    interval = metadata.get("interval_seconds")
    require(
        isinstance(repository, str)
        and Path(repository).is_absolute()
        and isinstance(python_relative, str)
        and not Path(python_relative).is_absolute()
        and ".." not in Path(python_relative).parts
        and Path(python_relative).parts[:1] == (".venv",)
        and isinstance(drill_id, str)
        and DRILL_ID.fullmatch(drill_id) is not None
        and scenario in {"complete", "review"}
        and type(interval) is int
        and 5 <= interval <= 60,
        "invalid source plist identity",
    )
    expected_argv = [
        str(Path(repository) / python_relative),
        str(Path(repository) / SCRIPT_RELATIVE),
        "worker",
        "--drill-root",
        str(drill_root),
        "--drill-id",
        drill_id,
        "--scenario",
        scenario,
    ]
    require(argv == expected_argv, "invalid synthetic argv")
    require(
        value["Label"] == f"{LABEL_PREFIX}.{drill_id}.{scenario}"
        and value["StandardOutPath"] == str(drill_root / "worker.stdout.log")
        and value["StandardErrorPath"] == str(drill_root / "worker.stderr.log")
        and value["Umask"] == "077"
        and value["StartInterval"] == interval
        and value["KeepAlive"] is False,
        "invalid source plist values",
    )
    redacted = dict(value)
    redacted["ProgramArguments"] = [
        "<PYTHON>",
        "<REPOSITORY>/scripts/completion_monitor_schedule_drill.py",
        "worker",
        "--drill-root",
        "<DRILL_ROOT>",
        "--drill-id",
        drill_id,
        "--scenario",
        scenario,
    ]
    redacted["WorkingDirectory"] = "<REPOSITORY>"
    redacted["StandardOutPath"] = "<DRILL_ROOT>/worker.stdout.log"
    redacted["StandardErrorPath"] = "<DRILL_ROOT>/worker.stderr.log"
    return plistlib.dumps(redacted, fmt=plistlib.FMT_XML, sort_keys=True)


def validated_finalized_evidence(
    drill_root: Path,
) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    metadata = read_json(drill_root / "metadata.json", METADATA_FIELDS)
    drill_id = metadata["drill_id"]
    scenario = metadata["scenario"]
    interval = metadata["interval_seconds"]
    commit = metadata["repository_commit"]
    started_at = metadata["started_at"]
    require(
        metadata["format"] == FORMAT
        and isinstance(drill_id, str)
        and DRILL_ID.fullmatch(drill_id) is not None
        and scenario in {"complete", "review"}
        and type(interval) is int
        and 5 <= interval <= 60
        and isinstance(commit, str)
        and re.fullmatch(r"[0-9a-f]{40}", commit) is not None
        and isinstance(started_at, str)
        and started_at.endswith("Z"),
        "invalid source metadata",
    )
    try:
        require(
            datetime.fromisoformat(started_at[:-1] + "+00:00").tzinfo == timezone.utc,
            "invalid source metadata timestamp",
        )
        require(
            isinstance(metadata["macos_version"], str)
            and isinstance(metadata["python_version"], str),
            "invalid source metadata version",
        )
        sanitized_version(metadata["macos_version"])
        sanitized_version(metadata["python_version"])
    except (TypeError, ValueError) as exc:
        raise DrillError("invalid source metadata") from exc
    require(drill_root.name == f"{drill_id}-{scenario}", "source identity mismatch")
    steps = load_outcome_script(drill_root, drill_id, scenario)
    expected_outcomes = [str(step["outcome"]) for step in steps]
    events = load_events(drill_root)
    require(
        regular_file(drill_root / "invocations.jsonl", private=True).read_bytes()
        == b"".join(canonical(event) + b"\n" for event in events),
        "invocation aggregate mismatch",
    )
    prepared = PreparedDrill(
        root=drill_root,
        label=f"{LABEL_PREFIX}.{drill_id}.{scenario}",
        plist=drill_root / "launch-agent.plist",
        service_target="<SANITIZATION_ONLY>",
        scenario=scenario,
        expected_outcomes=tuple(expected_outcomes),
        interval_seconds=interval,
    )
    observed_outcomes = [event["outcome"] for event in events]
    observed_exit_statuses = [event["exit_status"] for event in events]
    concurrency_ok = all(event["outcome"] != "OVERLAP" for event in events)
    sequence_ok = observed_outcomes == expected_outcomes
    terminal_ok = terminal_record_ok(prepared, events)
    long_ok = (
        scenario != "complete"
        or (
            len(events) >= 2
            and events[1]["duration_seconds"] >= interval
        )
    )
    result = read_json(drill_root / "result.json", RESULT_FIELDS)
    require(
        result["format"] == FORMAT
        and result["expected_outcomes"] == expected_outcomes
        and result["observed_outcomes"] == observed_outcomes
        and result["observed_exit_statuses"] == observed_exit_statuses
        and result["maximum_concurrency"]
        == ((1 if events else 0) if concurrency_ok else 2)
        and result["sequence_ok"] is sequence_ok
        and result["terminal_record_ok"] is terminal_ok
        and result["long_invocation_ok"] is long_ok
        and type(result["cleanup_ok"]) is bool
        and type(result["quiet_window_ok"]) is bool
        and type(result["loaded_state_verified"]) is bool
        and isinstance(result["detail"], str),
        "final result does not match invocation evidence",
    )
    passed = (
        result["cleanup_ok"]
        and result["quiet_window_ok"]
        and result["loaded_state_verified"]
        and sequence_ok
        and terminal_ok
        and long_ok
        and concurrency_ok
    )
    require(result["status"] == ("PASSED" if passed else "FAILED"), "invalid final status")
    require(result["cleanup_ok"] and result["quiet_window_ok"], "unclean evidence cannot be sanitized")
    require(
        (drill_root / "launchctl-loaded.txt").is_file()
        == bool(result["loaded_state_verified"]),
        "loaded-state evidence mismatch",
    )
    return metadata, result, events


def publish_manifest(root: Path) -> None:
    names = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.sha256"
    )
    lines = [f"{sha256_file(root / name)}  {name}\n" for name in names]
    publish(root / "manifest.sha256", "".join(lines).encode("utf-8"))


def sanitize_evidence(drill_root: Path, output_parent: Path) -> Path:
    drill_root = private_directory(drill_root)
    verify_manifest(drill_root)
    allowed_source_files = PREPARED_FILES | {
        "invocations.jsonl",
        "launchctl-initial-absent.txt",
        "launchctl-loaded.txt",
        "launchctl-unloaded.txt",
        "manifest.sha256",
        "result.json",
        "terminal.json",
    }
    source_files = {path.name for path in drill_root.iterdir() if path.is_file()}
    require(
        PREPARED_FILES
        | {
            "invocations.jsonl",
            "launchctl-initial-absent.txt",
            "launchctl-unloaded.txt",
            "result.json",
            "manifest.sha256",
        }
        <= source_files <= allowed_source_files,
        "unexpected finalized evidence path",
    )
    require(
        {path.name for path in drill_root.iterdir() if path.is_dir()} == {"invocations"},
        "unexpected finalized evidence directory",
    )
    for name in ("launchctl-initial-absent.txt", "launchctl-unloaded.txt"):
        content = regular_file(drill_root / name, private=True).read_bytes()
        require(b"Could not find service" in content, "exact-label absence evidence missing")
    metadata, result, events = validated_finalized_evidence(drill_root)
    payloads: dict[str, bytes] = {}
    for name, fields in (
        (
            "metadata.json",
            {
                "drill_id",
                "format",
                "interval_seconds",
                "macos_version",
                "python_relative",
                "python_version",
                "repository_commit",
                "scenario",
                "started_at",
            },
        ),
        (
            "outcome-script.json",
            {"drill_id", "format", "scenario", "steps"},
        ),
        (
            "result.json",
            RESULT_FIELDS,
        ),
    ):
        if name == "metadata.json":
            value = metadata
        elif name == "result.json":
            value = result
        else:
            value = read_json(drill_root / name, fields)
        if name == "result.json" and value.get("detail") != "terminal synthetic outcome observed":
            value = dict(value)
            value["detail"] = "<REDACTED_FAILURE_DETAIL>"
        payloads[name] = canonical(value)
    payloads["launch-agent.plist"] = sanitized_plist(
        drill_root / "launch-agent.plist",
        drill_root=drill_root,
        metadata=metadata,
    )
    redacted_events = []
    for event in events:
        redacted = dict(event)
        redacted.pop("pid", None)
        redacted_events.append(redacted)
    payloads["invocations.jsonl"] = b"".join(
        canonical(event) + b"\n" for event in redacted_events
    )
    summary = {
        "format": FORMAT,
        "privacy": "paths-redacted-pids-and-raw-launchctl-output-omitted",
        "source_manifest_sha256": sha256_file(drill_root / "manifest.sha256"),
    }
    payloads["review-summary.json"] = canonical(summary)
    output_parent = private_directory(output_parent)
    destination = output_parent / f"{drill_root.name}-sanitized"
    require(not destination.exists() and not destination.is_symlink(), "sanitized package exists")
    os.mkdir(destination, 0o700)
    for name, body in payloads.items():
        publish(destination / name, body)
    publish_manifest(destination)
    return destination


def render_production(args: argparse.Namespace) -> None:
    repository = absolute_path(args.repository)
    require(repository.resolve(strict=True) == repository, "symlink repository forbidden")
    python = validate_python(repository, args.python)
    stdout_path = absolute_path(args.stdout_path, must_exist=False)
    stderr_path = absolute_path(args.stderr_path, must_exist=False)
    output = absolute_path(args.output, must_exist=False)
    data_root = repository / "data"
    require(
        within(output, data_root)
        and within(stdout_path, data_root)
        and within(stderr_path, data_root),
        "production render paths must remain under ignored data",
    )
    require(not output.exists() and not output.is_symlink(), "refusing overwrite")
    for parent in {output.parent, stdout_path.parent, stderr_path.parent}:
        parent = absolute_path(parent)
        require(parent.resolve(strict=True) == parent, "symlinked render parent forbidden")
        info = parent.lstat()
        require(
            stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid(),
            "owned render directory required",
        )
    body = production_plist(
        label=f"{PRODUCTION_LABEL_PREFIX}.{args.season}.gw{args.target_gameweek}",
        python=python,
        repository=repository,
        season=args.season,
        gameweek=args.target_gameweek,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        prediction_snapshot=args.prediction_snapshot_timestamp,
    )
    publish(output, body)
    print(json.dumps({"status": "RENDERED_NOT_INSTALLED", "sha256": sha256_bytes(body)}))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    for item in (prepare,):
        item.add_argument("--repository", type=Path, required=True)
        item.add_argument("--expected-commit", required=True)
        item.add_argument("--python", type=Path, required=True)
        item.add_argument("--drill-parent", type=Path, required=True)
        item.add_argument("--drill-id", required=True)
        item.add_argument("--scenario", choices=("complete", "review"), required=True)
        item.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL_SECONDS)
        item.add_argument("--allow-untracked", action="append", default=[])
    run = sub.add_parser("run")
    run.add_argument("--repository", type=Path, required=True)
    run.add_argument("--expected-commit", required=True)
    run.add_argument("--python", type=Path, required=True)
    run.add_argument("--drill-root", type=Path, required=True)
    run.add_argument("--allow-untracked", action="append", default=[])
    run.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    run.add_argument("--quiet-seconds", type=int, default=DEFAULT_QUIET_SECONDS)
    run.add_argument("--execute-temporary-launch-agent", action="store_true", required=True)
    worker_parser = sub.add_parser("worker")
    worker_parser.add_argument("--drill-root", type=Path, required=True)
    worker_parser.add_argument("--drill-id", required=True)
    worker_parser.add_argument("--scenario", choices=("complete", "review"), required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--drill-root", type=Path, required=True)
    sanitize = sub.add_parser("sanitize")
    sanitize.add_argument("--drill-root", type=Path, required=True)
    sanitize.add_argument("--output-parent", type=Path, required=True)
    render = sub.add_parser("render-production")
    render.add_argument("--repository", type=Path, required=True)
    render.add_argument("--python", type=Path, required=True)
    render.add_argument("--season", required=True)
    render.add_argument("--target-gameweek", type=int, required=True)
    render.add_argument("--prediction-snapshot-timestamp")
    render.add_argument("--stdout-path", type=Path, required=True)
    render.add_argument("--stderr-path", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "worker":
            previous = install_signal_handlers()
            try:
                return worker(
                    drill_root=args.drill_root,
                    drill_id=args.drill_id,
                    scenario=args.scenario,
                )
            finally:
                restore_signal_handlers(previous)
        if args.command == "verify":
            verify_manifest(args.drill_root)
            print(json.dumps({"status": "VERIFIED"}))
            return 0
        if args.command == "sanitize":
            destination = sanitize_evidence(args.drill_root, args.output_parent)
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
        if args.command == "render-production":
            render_production(args)
            return 0
        if args.command == "prepare":
            prepared = prepare_drill(
                repository=args.repository,
                expected_commit=args.expected_commit,
                python=args.python,
                drill_parent=args.drill_parent,
                drill_id=args.drill_id,
                scenario=args.scenario,
                interval_seconds=args.interval_seconds,
                allowed_untracked=set(args.allow_untracked),
            )
            print(
                json.dumps(
                    {
                        "label": prepared.label,
                        "status": "PREPARED_NOT_LOADED",
                    },
                    sort_keys=True,
                )
            )
            return 0
        if not args.execute_temporary_launch_agent:
            raise DrillError("explicit temporary LaunchAgent flag required")
        prepared = load_prepared_drill(
            repository=args.repository,
            expected_commit=args.expected_commit,
            python=args.python,
            drill_root=args.drill_root,
            allowed_untracked=set(args.allow_untracked),
        )
        return 0 if execute_drill(
            prepared,
            timeout_seconds=args.timeout_seconds,
            quiet_seconds=args.quiet_seconds,
        ) else 1
    except (DrillError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"code": "DRILL_FAILED", "detail": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
