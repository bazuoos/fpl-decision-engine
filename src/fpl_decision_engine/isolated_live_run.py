"""Fail-closed isolation checks for the first live operational run.

This module reads only local public metadata.  It never fetches FPL data, reads
manager evidence, changes monitor lifecycle state, or creates the sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import sys
from typing import Callable, Sequence


PLAN_FORMAT = "task028f-production-completion-monitor-schedule-v1"
PLAN_NAME = "schedule-plan.json"
PLAN_HASH_NAME = "schedule-plan.json.sha256"
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_COMMAND_BYTES = 128 * 1024
COMMAND_TIMEOUT_SECONDS = 15
SYSTEM_GIT = Path("/usr/bin/git")
SYSTEM_LAUNCHCTL = Path("/bin/launchctl")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
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
PROTECTED_ROOT_FIELDS = (
    "raw_data_root",
    "clean_data_root",
    "feature_data_root",
    "prediction_data_root",
    "evaluation_data_root",
    "task028b_control_data_root",
)


class IsolatedLiveRunError(Exception):
    """A bounded public failure from the local preflight boundary."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


CommandRunner = Callable[[Sequence[str], int], CommandResult]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class PreflightRequest:
    primary_repository: Path
    expected_primary_commit: str
    acknowledged_primary_untracked: tuple[str, ...]
    code_repository: Path
    expected_code_commit: str
    acknowledged_code_untracked: tuple[str, ...]
    sandbox_root: Path
    schedule_plan: Path
    schedule_plan_sha256_file: Path
    expected_schedule_plan_sha256: str
    official_deadline: str
    required_free_bytes: int = 1024 * 1024 * 1024


@dataclass(frozen=True)
class PreflightResult:
    status: str
    primary_commit: str
    code_commit: str
    schedule_plan_sha256: str
    monitor_state: str
    sandbox_root: Path
    free_bytes: int

    def public_payload(self) -> dict[str, object]:
        return {
            "code_commit": self.code_commit,
            "free_bytes": self.free_bytes,
            "monitor_state": self.monitor_state,
            "primary_commit": self.primary_commit,
            "sandbox_root": str(self.sandbox_root),
            "schedule_plan_sha256": self.schedule_plan_sha256,
            "status": self.status,
        }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise IsolatedLiveRunError(message)


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IsolatedLiveRunError("invalid schedule plan") from exc


def _regular_file(path: Path) -> Path:
    _require(path.is_absolute(), "absolute file path required")
    _require(".." not in path.parts, "parent traversal is forbidden")
    _require(path.exists() and not path.is_symlink(), "required regular file missing")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise IsolatedLiveRunError("file path cannot be resolved") from exc
    _require(resolved == path, "redirected file path forbidden")
    info = path.lstat()
    _require(stat.S_ISREG(info.st_mode), "regular file required")
    _require(info.st_uid == os.getuid(), "file owner mismatch")
    _require(info.st_nlink == 1, "hard-linked file forbidden")
    _require(info.st_size <= MAX_FILE_BYTES, "file exceeds size limit")
    return path


def _read_file(path: Path) -> bytes:
    try:
        body = _regular_file(path).read_bytes()
    except OSError as exc:
        raise IsolatedLiveRunError("could not read required file") from exc
    _require(len(body) <= MAX_FILE_BYTES, "file exceeds size limit")
    return body


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _parse_utc(value: str, field: str) -> datetime:
    _require(isinstance(value, str) and value.endswith("Z"), f"invalid {field}")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise IsolatedLiveRunError(f"invalid {field}") from exc
    _require(parsed.tzinfo == timezone.utc, f"invalid {field}")
    return parsed


def _run(arguments: Sequence[str], timeout: int) -> CommandResult:
    _require(bool(arguments) and Path(arguments[0]).is_absolute(), "absolute command required")
    try:
        completed = subprocess.run(
            list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={},
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IsolatedLiveRunError("bounded local command failed") from exc
    _require(
        len(completed.stdout) <= MAX_COMMAND_BYTES
        and len(completed.stderr) <= MAX_COMMAND_BYTES,
        "local command output exceeded limit",
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _command_output(
    runner: CommandRunner, arguments: Sequence[str], *, allow_failure: bool = False
) -> bytes:
    result = runner(arguments, COMMAND_TIMEOUT_SECONDS)
    _require(
        len(result.stdout) <= MAX_COMMAND_BYTES
        and len(result.stderr) <= MAX_COMMAND_BYTES,
        "local command output exceeded limit",
    )
    if not allow_failure:
        _require(result.returncode == 0, "local command failed")
    return result.stdout


def _git_output(
    runner: CommandRunner, repository: Path, arguments: Sequence[str]
) -> bytes:
    return _command_output(
        runner, [str(SYSTEM_GIT), "-C", str(repository), *arguments]
    )


def _repository_commit(runner: CommandRunner, repository: Path) -> str:
    try:
        value = _git_output(runner, repository, ["rev-parse", "HEAD"]).decode().strip()
    except UnicodeDecodeError as exc:
        raise IsolatedLiveRunError("invalid repository commit encoding") from exc
    _require(COMMIT_PATTERN.fullmatch(value) is not None, "invalid repository commit")
    return value


def _status_paths(raw: bytes) -> tuple[tuple[str, ...], bool]:
    untracked: list[str] = []
    tracked_change = False
    for record in raw.split(b"\0"):
        if not record:
            continue
        _require(len(record) >= 4 and record[2:3] == b" ", "invalid Git status")
        try:
            path = record[3:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IsolatedLiveRunError("non-UTF-8 Git path") from exc
        if record[:2] == b"??":
            untracked.append(path)
        else:
            tracked_change = True
    return tuple(sorted(untracked)), tracked_change


def _validate_repository(
    runner: CommandRunner,
    repository: Path,
    expected_commit: str,
    expected_untracked: tuple[str, ...],
) -> str:
    _require(repository.is_absolute() and repository.is_dir(), "repository missing")
    _require(COMMIT_PATTERN.fullmatch(expected_commit) is not None, "invalid expected commit")
    commit = _repository_commit(runner, repository)
    _require(commit == expected_commit, "repository commit mismatch")
    raw = _git_output(
        runner,
        repository,
        [
            "-c",
            "core.excludesFile=/dev/null",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
    )
    untracked, tracked_change = _status_paths(raw)
    _require(not tracked_change, "tracked or staged repository change")
    normalized: list[str] = []
    for value in expected_untracked:
        _require(isinstance(value, str) and bool(value), "invalid acknowledged path")
        path = Path(value)
        _require(
            not path.is_absolute()
            and ".." not in path.parts
            and value == path.as_posix(),
            "invalid acknowledged untracked path",
        )
        normalized.append(value)
    _require(len(normalized) == len(set(normalized)), "duplicate acknowledged path")
    _require(untracked == tuple(sorted(normalized)), "unexpected untracked path")
    return commit


def paths_overlap(first: Path, second: Path) -> bool:
    """Return whether resolved paths are equal or ancestors in either direction."""
    first_resolved = first.resolve(strict=True)
    second_resolved = second.resolve(strict=True)
    return (
        first_resolved == second_resolved
        or first_resolved in second_resolved.parents
        or second_resolved in first_resolved.parents
    )


def validate_sandbox_isolation(sandbox_root: Path, protected_roots: Sequence[Path]) -> Path:
    _require(sandbox_root.is_absolute(), "sandbox path must be absolute")
    try:
        resolved = sandbox_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise IsolatedLiveRunError("sandbox path cannot be resolved") from exc
    _require(resolved.is_dir(), "sandbox directory required")
    _require(resolved.stat().st_uid == os.getuid(), "sandbox owner mismatch")
    _require(resolved.stat().st_mode & 0o077 == 0, "sandbox must be owner-only")
    for protected in protected_roots:
        try:
            overlap = paths_overlap(resolved, protected)
        except (OSError, RuntimeError) as exc:
            raise IsolatedLiveRunError("protected path cannot be resolved") from exc
        _require(not overlap, "sandbox overlaps an active monitor root")
    return resolved


def _load_plan(request: PreflightRequest) -> tuple[dict[str, object], str]:
    _require(request.schedule_plan.name == PLAN_NAME, "unexpected schedule plan filename")
    _require(
        request.schedule_plan_sha256_file.name == PLAN_HASH_NAME,
        "unexpected schedule digest filename",
    )
    _require(
        request.schedule_plan_sha256_file.parent == request.schedule_plan.parent,
        "schedule digest must be adjacent to plan",
    )
    body = _read_file(request.schedule_plan)
    digest_body = _read_file(request.schedule_plan_sha256_file)
    digest = _sha256(body)
    expected_line = f"{digest}  {PLAN_NAME}\n".encode("ascii")
    _require(digest_body == expected_line, "schedule plan hash mismatch")
    _require(
        SHA256_PATTERN.fullmatch(request.expected_schedule_plan_sha256) is not None
        and digest == request.expected_schedule_plan_sha256,
        "unexpected schedule plan identity",
    )
    try:
        plan = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise IsolatedLiveRunError("invalid schedule plan JSON") from exc
    _require(
        isinstance(plan, dict) and set(plan) == PLAN_FIELDS,
        "unexpected schedule plan fields",
    )
    _require(_canonical(plan) == body, "schedule plan must be canonical JSON")
    _require(plan.get("format") == PLAN_FORMAT, "schedule plan format mismatch")
    _require(
        plan.get("repository") == str(request.primary_repository),
        "plan repository mismatch",
    )
    _require(
        plan.get("expected_commit") == request.expected_primary_commit,
        "plan commit mismatch",
    )
    _require(
        type(plan.get("uid")) is int and plan["uid"] == os.getuid(),
        "plan user mismatch",
    )
    label = plan.get("label")
    _require(isinstance(label, str) and label, "invalid monitor label")
    for field in PROTECTED_ROOT_FIELDS:
        value = plan.get(field)
        _require(isinstance(value, str) and Path(value).is_absolute(), f"invalid {field}")
    return plan, digest


def _verify_sandbox_git_boundary(
    runner: CommandRunner, repository: Path, sandbox: Path
) -> None:
    data_root = (repository / "data").resolve(strict=True)
    _require(
        data_root == sandbox or data_root in sandbox.parents,
        "sandbox is outside ignored data root",
    )
    relative = sandbox.relative_to(repository.resolve(strict=True)).as_posix()
    ignored = runner(
        [str(SYSTEM_GIT), "-C", str(repository), "check-ignore", "--quiet", "--", relative],
        COMMAND_TIMEOUT_SECONDS,
    )
    _require(ignored.returncode == 0, "sandbox is not Git-ignored")
    tracked = _git_output(runner, repository, ["ls-files", "--", relative])
    staged = _git_output(
        runner, repository, ["diff", "--cached", "--name-only", "--", relative]
    )
    visible = _git_output(
        runner,
        repository,
        ["status", "--porcelain=v1", "--untracked-files=all", "--", relative],
    )
    _require(not tracked and not staged and not visible, "sandbox is visible to Git")


def _parse_launchctl(output: bytes, expected_arguments: Sequence[str]) -> str:
    try:
        lines = output.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise IsolatedLiveRunError("invalid launchctl output") from exc
    stripped = [line.strip() for line in lines]
    if "arguments = {" in stripped:
        start = stripped.index("arguments = {") + 1
        try:
            end = stripped.index("}", start)
        except ValueError as exc:
            raise IsolatedLiveRunError("monitor arguments unavailable") from exc
        arguments = stripped[start:end]
        _require(arguments == list(expected_arguments), "monitor arguments changed")
    state_lines = [line for line in stripped if line.startswith("state = ")]
    if state_lines:
        state = state_lines[0].removeprefix("state = ")
        if state == "running":
            return "running"
        _require(state == "not running", "unexpected monitor state")
        active_lines = [line for line in stripped if line.startswith("active count = ")]
        _require(
            bool(active_lines) and active_lines[0] == "active count = 0",
            "unexpected monitor state",
        )
        return "loaded-idle"
    active_lines = [line for line in stripped if line.startswith("active count = ")]
    _require(active_lines == ["active count = 0"], "unexpected monitor state")
    return "loaded-idle"


def _verify_launchctl(
    runner: CommandRunner,
    plan: dict[str, object],
    plan_path: Path,
    digest_path: Path,
) -> str:
    target = f"gui/{plan['uid']}/{plan['label']}"
    result = runner(
        [str(SYSTEM_LAUNCHCTL), "print", target], COMMAND_TIMEOUT_SECONDS
    )
    _require(result.returncode == 0, "active monitor is unavailable")
    expected = [
        str(plan["python_path"]),
        "-I",
        str(plan["controller_path"]),
        "run",
        "--plan",
        str(plan_path),
        "--plan-sha256-file",
        str(digest_path),
    ]
    installed_path = Path(str(plan["installed_plist_path"]))
    installed_body = _read_file(installed_path)
    try:
        installed = plistlib.loads(installed_body)
    except plistlib.InvalidFileException as exc:
        raise IsolatedLiveRunError("invalid installed monitor definition") from exc
    _require(
        isinstance(installed, dict)
        and installed.get("Label") == plan["label"]
        and installed.get("ProgramArguments") == expected,
        "installed monitor arguments changed",
    )
    try:
        launch_text = result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IsolatedLiveRunError("invalid launchctl output") from exc
    _require(
        f"path = {installed_path}" in (line.strip() for line in launch_text.splitlines()),
        "loaded monitor definition changed",
    )
    return _parse_launchctl(result.stdout, expected)


def run_preflight(
    request: PreflightRequest,
    *,
    runner: CommandRunner = _run,
    clock: Clock = lambda: datetime.now(timezone.utc),
) -> PreflightResult:
    """Verify isolation without network access or manager-data reads."""
    plan, plan_digest = _load_plan(request)
    primary_commit = _validate_repository(
        runner,
        request.primary_repository,
        request.expected_primary_commit,
        request.acknowledged_primary_untracked,
    )
    code_commit = _validate_repository(
        runner,
        request.code_repository,
        request.expected_code_commit,
        request.acknowledged_code_untracked,
    )
    protected = [Path(str(plan[field])) for field in PROTECTED_ROOT_FIELDS]
    sandbox = validate_sandbox_isolation(request.sandbox_root, protected)
    _verify_sandbox_git_boundary(runner, request.primary_repository, sandbox)
    invoked = Path(os.path.abspath(sys.executable))
    _require(invoked == Path(str(plan["python_path"])), "wrong pinned Python interpreter")
    _require(
        invoked.resolve(strict=True) == Path(str(plan["python_resolved_path"])),
        "pinned Python target changed",
    )
    now = clock()
    _require(
        now.tzinfo is not None
        and now.utcoffset() == timezone.utc.utcoffset(now),
        "UTC clock required",
    )
    deadline = _parse_utc(request.official_deadline, "official deadline")
    _require(now < deadline, "official deadline reached")
    try:
        free_bytes = shutil.disk_usage(sandbox).free
    except OSError as exc:
        raise IsolatedLiveRunError("disk space inspection failed") from exc
    _require(
        type(request.required_free_bytes) is int
        and request.required_free_bytes >= 0
        and free_bytes >= request.required_free_bytes,
        "insufficient free disk space",
    )
    monitor_state = _verify_launchctl(
        runner, plan, request.schedule_plan, request.schedule_plan_sha256_file
    )
    return PreflightResult(
        status="PREFLIGHT_PASSED",
        primary_commit=primary_commit,
        code_commit=code_commit,
        schedule_plan_sha256=plan_digest,
        monitor_state=monitor_state,
        sandbox_root=sandbox,
        free_bytes=free_bytes,
    )


def safety_check(request: PreflightRequest) -> Callable[[], None]:
    """Return a zero-argument, block-only callback for wizard transitions."""
    def check() -> None:
        run_preflight(request)

    return check
