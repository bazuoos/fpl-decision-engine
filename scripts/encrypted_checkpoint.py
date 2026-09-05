"""Local age checkpoints. No network, real-data defaults, or engine execution.

Only aggregate, path-free JSON is emitted by the CLI. See the Task027D runbook
for the deliberately strict archive dialect and incomplete-directory protocol.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import unicodedata
import uuid

try:
    from .inventory_private_evidence import fingerprint, open_root
except ImportError:  # direct script invocation
    from inventory_private_evidence import fingerprint, open_root


FORMAT = "fpl-private-checkpoint-v1"
AGE_VERSION = "v1.3.1"
CHUNK = 1024 * 1024
RECEIPT = "COMPLETE.json"
CIPHERTEXT = "checkpoint.age"
RESTORED = "RESTORE_COMPLETE.json"
HEX = re.compile(r"[0-9a-f]{64}\Z")
ID = re.compile(r"checkpoint_[0-9a-f]{32}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
RESERVED_ASSURANCE_FIELDS = {
    "byte_capture",
    "cryptographic_archive_verification",
    "original_evidence_coverage",
    "engine_trust_chain_validation",
    "offsite_readback",
    "independent_copy_verification",
    "disaster_recovery",
}


class CheckpointError(Exception):
    """Only stable non-private codes cross the public boundary."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise CheckpointError(code)


@dataclass(frozen=True)
class Limits:
    archive_bytes: int = 64 * 1024**3
    total_bytes: int = 60 * 1024**3
    file_bytes: int = 4 * 1024**3
    bundle_bytes: int = 512 * 1024**2
    files: int = 100_000
    directories: int = 100_000
    manifest_bytes: int = 16 * 1024**2
    path_bytes: int = 4096
    depth: int = 64
    pax_bytes: int = 8192
    child_seconds: int = 1800


DEFAULT_LIMITS = Limits()

# Git parses attacker-controlled bundle/pack bytes during verification. Run the
# trusted system Git under POSIX resource ceilings, never checkout restored code.
GIT_RESOURCE_WRAPPER = (
    "import os,resource,sys; "
    "(resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3)) "
    "if sys.platform.startswith('linux') else None); "
    "resource.setrlimit(resource.RLIMIT_FSIZE,(int(sys.argv[1]),int(sys.argv[1]))); "
    "resource.setrlimit(resource.RLIMIT_CPU,(120,120)); "
    "resource.setrlimit(resource.RLIMIT_NOFILE,(128,128)); "
    "os.execvp(sys.argv[2],sys.argv[2:])"
)


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_JSON_FIELD")
        result[key] = value
    return result


def parse_json(body):
    try:
        value = json.loads(body, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(
                               CheckpointError("INVALID_JSON")))
        require(canonical(value) == body, "NONCANONICAL_JSON")
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise CheckpointError("INVALID_JSON") from None


def exact(value, fields):
    require(isinstance(value, dict) and set(value) == set(fields), "INVALID_FIELDS")


def integer(value, maximum):
    require(type(value) is int and 0 <= value <= maximum, "LIMIT_EXCEEDED")


def _absolute(path):
    value = os.fspath(path)
    require(os.path.isabs(value) and ".." not in value.split(os.sep), "UNSAFE_LOCATION")
    return Path(os.path.abspath(value))


def _directory(path, private=False):
    path = _absolute(path)
    fd = open_root(path)
    if private:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            os.close(fd)
            raise CheckpointError("PRIVATE_DIRECTORY_REQUIRED")
    return path, fd


def _within(path, root):
    return path == root or root in path.parents


def _disjoint(a, b):
    require(not _within(a, b) and not _within(b, a), "OVERLAPPING_SCOPE")


def safe_path(value, limits=DEFAULT_LIMITS):
    require(isinstance(value, str) and bool(value), "UNSAFE_ARCHIVE_PATH")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise CheckpointError("UNSAFE_ARCHIVE_PATH") from None
    require(len(encoded) <= limits.path_bytes, "LIMIT_EXCEEDED")
    parts = value.split("/")
    require(len(parts) <= limits.depth, "LIMIT_EXCEEDED")
    for part in parts:
        require(part not in ("", ".", "..") and not part.endswith((".", " "))
                and not any(ord(c) < 32 or ord(c) == 127 or c in "\\:" for c in part),
                "UNSAFE_ARCHIVE_PATH")
        require(part.split(".")[0].upper() not in {
            "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))}, "UNSAFE_ARCHIVE_PATH")
    return value


def path_set(files, directories, limits):
    """Conservative case/NFC collision checks, including all ancestor names."""
    names, kinds = {}, {}
    for kind, values in (("directory", directories), ("file", files)):
        for name in values:
            safe_path(name, limits)
            key = unicodedata.normalize("NFC", name).casefold()
            require(key not in names, "PATH_COLLISION")
            names[key], kinds[name] = name, kind
    for name in kinds:
        parts = name.split("/")
        for end in range(1, len(parts)):
            require(kinds.get("/".join(parts[:end])) == "directory", "PATH_COLLISION")


def _open_file(parent_fd, name, mode=os.O_RDONLY):
    fd = os.open(name, mode | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise CheckpointError("UNSAFE_FILE")
    return fd


def _small_file(path, maximum, private=False):
    path = _absolute(path)
    _, parent = _directory(path.parent)
    try:
        fd = _open_file(parent, path.name)
    finally:
        os.close(parent)
    try:
        before = os.fstat(fd)
        require(before.st_size <= maximum, "LIMIT_EXCEEDED")
        if private:
            require(before.st_uid == os.getuid() and not before.st_mode & 0o077,
                    "PRIVATE_FILE_REQUIRED")
        data = bytearray()
        while block := os.read(fd, min(CHUNK, maximum + 1 - len(data))):
            data.extend(block)
            require(len(data) <= maximum, "LIMIT_EXCEEDED")
        require(fingerprint(os.fstat(fd)) == fingerprint(before), "INPUT_CHANGED")
        return bytes(data)
    finally:
        os.close(fd)


def coverage_declaration(value):
    exact(value, ("status", "gaps"))
    require(value["status"] in ("known_gaps", "not_assessed"), "INVALID_COVERAGE")
    gaps = value["gaps"]
    require(isinstance(gaps, list) and len(gaps) <= 100, "INVALID_COVERAGE")
    require(all(isinstance(g, str) and 0 < len(g.encode("utf-8")) <= 8192 for g in gaps),
            "INVALID_COVERAGE")
    require(value["status"] != "known_gaps" or bool(gaps), "INVALID_COVERAGE")
    return value


def _environment():
    # Never inherit Git redirection, config injection, age plugins or prompts.
    return {k: v for k, v in os.environ.items()
            if not k.startswith(("GIT_", "AGE_"))} | {
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
                "GIT_NO_REPLACE_OBJECTS": "1"}


@contextmanager
def child(args, *, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
          pass_fds=(), limits=DEFAULT_LIMITS):
    """Discard stderr with bounded memory; timeout/late failure is never success."""
    proc = subprocess.Popen(args, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE,
                            env=_environment(), pass_fds=pass_fds, close_fds=True,
                            start_new_session=True)
    expired = threading.Event()
    def terminate():
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Some macOS sandboxes deny group signalling for signed age even
            # though terminating our directly owned child remains permitted.
            # Native age does not launch plugins/descendants in this format.
            proc.kill()
    def timeout():
        expired.set()
        terminate()
    timer = threading.Timer(limits.child_seconds, timeout)
    timer.daemon = True
    def discard():
        while proc.stderr.read(65536):
            pass
    drain = threading.Thread(target=discard, daemon=True)
    drain.start()
    timer.start()
    try:
        yield proc
        status = proc.wait(timeout=limits.child_seconds)
        require(not expired.is_set(), "CHILD_TIMEOUT")
        require(status == 0, "CHILD_FAILED")
    finally:
        timer.cancel()
        terminate()
        proc.wait()
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    # Cleanup must not replace the original integrity failure
                    # with a buffered-stdin BrokenPipeError.
                    pass
        drain.join(timeout=5)
        proc.stderr.close()


def command(args, limits=DEFAULT_LIMITS):
    if args[0] == "git":
        args = [sys.executable, "-I", "-c", GIT_RESOURCE_WRAPPER,
                str(limits.bundle_bytes), *args]
    with child(args, limits=limits) as proc:
        data = proc.stdout.read(65537)
        require(len(data) <= 65536, "CHILD_OUTPUT_LIMIT")
    return data


def age_tool(executable="age"):
    require(sys.platform == "darwin" or sys.platform.startswith("linux"), "PLATFORM_UNSUPPORTED")
    path = shutil.which(executable)
    require(path is not None, "AGE_UNAVAILABLE")
    try:
        version = command([path, "--version"], replace(DEFAULT_LIMITS, child_seconds=10)).decode("ascii").strip()
    except (OSError, UnicodeError, CheckpointError):
        raise CheckpointError("AGE_UNAVAILABLE") from None
    require(version == AGE_VERSION, "AGE_VERSION_UNSUPPORTED")
    return path


@contextmanager
def key_pipe(path, *, secret, scopes):
    """Validate native file-only keys; pass material through an inherited FD."""
    path = _absolute(path)
    for scope in scopes:
        require(not _within(path, scope), "KEY_SCOPE_OVERLAP")
    body = _small_file(path, 8192, private=secret)
    try:
        lines = [s.strip() for s in body.decode("ascii").splitlines()
                 if s.strip() and not s.lstrip().startswith("#")]
    except UnicodeError:
        raise CheckpointError("NATIVE_KEY_REQUIRED") from None
    pattern = r"AGE-SECRET-KEY-1[0-9A-Z]{58}" if secret else r"age1[0-9a-z]{58}"
    require(0 < len(lines) <= 16 and all(re.fullmatch(pattern, s) for s in lines),
            "NATIVE_KEY_REQUIRED")
    read_fd, write_fd = os.pipe()
    try:
        # Bounded material fits the portable pipe capacity; no key in argv/logs.
        key_bytes = ("\n".join(lines) + "\n").encode("ascii")
        require(len(key_bytes) <= 2048, "NATIVE_KEY_REQUIRED")
        os.write(write_fd, key_bytes)
        os.close(write_fd)
        write_fd = -1
        yield read_fd
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)


def git(repo, *args):
    return ["git", "--no-replace-objects", "-C", str(repo),
            "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args]


def source_revision(repo, commit):
    require(isinstance(commit, str) and bool(COMMIT.fullmatch(commit)), "SOURCE_REVISION_INVALID")
    require(command(git(repo, "rev-parse", "--show-toplevel")).decode().strip() == str(repo),
            "REPOSITORY_ROOT_REQUIRED")
    require(command(git(repo, "rev-parse", "HEAD")).decode().strip() == commit,
            "SOURCE_REVISION_MISMATCH")
    try:
        command(git(repo, "diff-index", "--quiet", "HEAD", "--"))
    except CheckpointError:
        raise CheckpointError("DIRTY_TRACKED_SOURCE") from None


def _digest_fd(fd, maximum):
    before = os.fstat(fd)
    require(before.st_size <= maximum, "LIMIT_EXCEEDED")
    digest, size = hashlib.sha256(), 0
    while block := os.read(fd, CHUNK):
        size += len(block)
        require(size <= maximum, "LIMIT_EXCEEDED")
        digest.update(block)
    require(size == before.st_size and fingerprint(os.fstat(fd)) == fingerprint(before),
            "INPUT_CHANGED")
    return size, digest.hexdigest()


def scan(fd, limits, *, hashing):
    """Task027C no-follow/fingerprint discipline, with archive resource bounds."""
    metadata, files, dirs = {}, [], []
    total, path_budget = 0, 0
    def visit(directory, prefix):
        nonlocal total, path_budget
        before = os.fstat(directory)
        metadata[prefix] = fingerprint(before)
        # scandir iterator avoids materializing an unbounded directory listing.
        with os.scandir(directory) as entries:
            for entry in entries:
                name = f"{prefix}/{entry.name}" if prefix else entry.name
                safe_path("data/" + name, limits)
                path_budget += len(name.encode("utf-8")) + 256
                require(path_budget <= limits.manifest_bytes, "LIMIT_EXCEEDED")
                info = os.stat(entry.name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    dirs.append("data/" + name)
                    require(len(dirs) <= limits.directories - 2, "LIMIT_EXCEEDED")
                    nested = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=directory)
                    try:
                        require(fingerprint(os.fstat(nested)) == fingerprint(info), "INPUT_CHANGED")
                        visit(nested, name)
                    finally:
                        os.close(nested)
                else:
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "UNSAFE_FILE")
                    metadata[name] = fingerprint(info)
                    integer(info.st_size, limits.file_bytes)
                    total += info.st_size
                    require(total <= limits.total_bytes and len(files) < limits.files - 1,
                            "LIMIT_EXCEEDED")
                    record = {"path": "data/" + name, "size_bytes": info.st_size}
                    if hashing:
                        source = _open_file(directory, entry.name)
                        try:
                            require(fingerprint(os.fstat(source)) == fingerprint(info), "INPUT_CHANGED")
                            size, digest = _digest_fd(source, limits.file_bytes)
                            record.update(size_bytes=size, sha256=digest)
                        finally:
                            os.close(source)
                    files.append(record)
        require(fingerprint(os.fstat(directory)) == fingerprint(before), "INPUT_CHANGED")
    visit(fd, "")
    dirs = sorted(["data", "repository", *dirs])
    path_set([r["path"] for r in files], dirs, limits)
    return metadata, sorted(files, key=lambda r: r["path"]), dirs


def _source_fd(root_fd, relative, metadata):
    parent = os.dup(root_fd)
    parts = relative.split("/")
    try:
        for i, part in enumerate(parts[:-1]):
            nested = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = nested
            require(fingerprint(os.fstat(parent)) == metadata["/".join(parts[:i + 1])],
                    "INPUT_CHANGED")
        fd = _open_file(parent, parts[-1])
        if fingerprint(os.fstat(fd)) != metadata[relative]:
            os.close(fd)
            raise CheckpointError("INPUT_CHANGED")
        return fd
    finally:
        os.close(parent)


def _header(name, size):
    info = tarfile.TarInfo(name)
    info.size, info.mode, info.mtime = size, 0o600, 0
    return info.tobuf(format=tarfile.PAX_FORMAT, encoding="utf-8", errors="strict")


def _member(out, name, size, reader):
    out.write(_header(name, size))
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        block = reader.read(min(remaining, CHUNK))
        require(bool(block), "INPUT_CHANGED")
        out.write(block)
        digest.update(block)
        remaining -= len(block)
    require(not reader.read(1), "INPUT_CHANGED")
    out.write(b"\0" * (-size % 512))
    return digest.hexdigest()


def _stream_source(out, root_fd, record, metadata):
    relative = record["path"][5:]
    fd = _source_fd(root_fd, relative, metadata)
    with os.fdopen(fd, "rb") as source:
        digest = _member(out, "payload/" + record["path"], record["size_bytes"], source)
        require(fingerprint(os.fstat(source.fileno())) == metadata[relative], "INPUT_CHANGED")
    require(digest == record["sha256"], "STREAM_HASH_MISMATCH")


def _exclusive_file(fd, name, body):
    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(out, "wb") as file:
        file.write(body)
        file.flush()
        os.fsync(file.fileno())


def _marker(fd, name, payload):
    # Hard-link publish is atomic and cannot replace a marker, even on a race.
    temporary = ".marker-" + uuid.uuid4().hex
    _exclusive_file(fd, temporary, canonical(payload))
    try:
        os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
    finally:
        os.unlink(temporary, dir_fd=fd)


def _same_directory(path, fd):
    check = open_root(path)
    try:
        before, after = os.fstat(fd), os.fstat(check)
        require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino), "INPUT_CHANGED")
    finally:
        os.close(check)


@contextmanager
def operation(parent, name):
    """New private operation dir; existing paths are never removed or replaced."""
    parent, parent_fd = _directory(parent, private=True)
    fd = None
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        created = True
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        inode = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
        _exclusive_file(fd, ".INCOMPLETE", b"incomplete\n")
        yield parent / name, fd
    except BaseException:
        if created and fd is not None:
            current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == inode and stat.S_ISDIR(current.st_mode):
                # Only this operation's exclusively owned tree; never a prior tree.
                shutil.rmtree(parent / name)
        raise
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def assurance(*, capture="not_run", crypto="not_run", coverage="not_assessed", **extra):
    require(not RESERVED_ASSURANCE_FIELDS.intersection(extra), "RESERVED_ASSURANCE_FIELD")
    return {"byte_capture": capture, "cryptographic_archive_verification": crypto,
            "original_evidence_coverage": coverage, "engine_trust_chain_validation": "not_run",
            "offsite_readback": "not_run", "independent_copy_verification": "not_run",
            "disaster_recovery": "NO VERIFIED OFFSITE BACKUP", **extra}


def _bundle_header(path, commit):
    with open(path, "rb") as file:
        prefix = file.read(1024)
    require(prefix.startswith(f"# v2 git bundle\n{commit} HEAD\n\nPACK".encode()),
            "BUNDLE_IDENTITY_INVALID")


def check_bundle(path, commit, scratch, limits):
    _bundle_header(path, commit)
    bare = Path(scratch) / "bare"
    command(git(scratch, "init", "--bare", "--template=", str(bare)), limits)
    command(git(bare, "bundle", "verify", str(path)), limits)
    command(git(bare, "bundle", "unbundle", str(path)), limits)
    require(command(git(bare, "cat-file", "-t", commit), limits).strip() == b"commit",
            "BUNDLE_IDENTITY_INVALID")
    command(git(bare, "fsck", "--full", "--strict", "--no-progress"), limits)


def create(*, source_root, repository, commit, recipients, output_parent,
           coverage, quiescent=False, age="age", limits=DEFAULT_LIMITS):
    executable = age_tool(age)  # BEFORE capture or publication
    require(quiescent is True, "QUIESCENCE_ACK_REQUIRED")
    source, root_fd = _directory(source_root)
    try:
        repo, repo_fd = _directory(repository)
        os.close(repo_fd)
        output, output_fd = _directory(output_parent, private=True)
        os.close(output_fd)
        _disjoint(source, output)
        _disjoint(repo, output)
        require(not _within(repo, source), "OVERLAPPING_SCOPE")
        coverage_path = _absolute(coverage)
        require(not _within(coverage_path, source), "OVERLAPPING_SCOPE")
        declaration = coverage_declaration(parse_json(_small_file(coverage_path, 1024**2)))
        with key_pipe(recipients, secret=False, scopes=(source, repo, output)) as key_fd:
            source_revision(repo, commit)
            metadata, records, directories = scan(root_fd, limits, hashing=True)
            checkpoint_id = "checkpoint_" + uuid.uuid4().hex
            with operation(output, checkpoint_id) as (op, op_fd):
                # Temporary committed-source bundle only; no data archive on disk.
                with tempfile.TemporaryDirectory(prefix=".bundle-", dir=op) as scratch:
                    bundle = Path(scratch) / "source.bundle"
                    command(git(repo, "bundle", "create", "--version=2", str(bundle), "HEAD"), limits)
                    os.chmod(bundle, 0o600)
                    check_bundle(bundle, commit, scratch, limits)
                    with open(bundle, "rb") as file:
                        size, digest = _digest_fd(file.fileno(), limits.bundle_bytes)
                    bundle_record = {"path": "repository/source.bundle", "size_bytes": size,
                                     "sha256": digest}
                    records = sorted([*records, bundle_record], key=lambda r: r["path"])
                    manifest = {
                        "format_version": FORMAT, "checkpoint_id": checkpoint_id,
                        "capture_time": datetime.now(timezone.utc).isoformat(),
                        "source_commit": commit,
                        "tool_versions": {"age": AGE_VERSION, "python": sys.version.split()[0],
                                          "git": command(["git", "--version"]).decode().strip()},
                        "logical_roots": {"data": str(source), "repository": str(repo)},
                        "coverage": declaration, "file_count": len(records),
                        "total_bytes": sum(r["size_bytes"] for r in records),
                        "directories": directories, "files": records,
                        "bundle": bundle_record,
                    }
                    validate_manifest(manifest, limits)
                    body = canonical(manifest)
                    require(len(body) <= limits.manifest_bytes, "LIMIT_EXCEEDED")
                    ciphertext_fd = os.open(CIPHERTEXT, os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                            0o600, dir_fd=op_fd)
                    pump_errors = []
                    with os.fdopen(ciphertext_fd, "wb") as target:
                        with child([executable, "--encrypt", "--recipients-file", f"/dev/fd/{key_fd}"],
                                   stdin=subprocess.PIPE, pass_fds=(key_fd,), limits=limits) as proc:
                            def pump():
                                try:
                                    total = 0
                                    while block := proc.stdout.read(CHUNK):
                                        total += len(block)
                                        require(total <= limits.archive_bytes, "LIMIT_EXCEEDED")
                                        target.write(block)
                                except BaseException as exc:
                                    pump_errors.append(exc)
                                    proc.kill()
                            thread = threading.Thread(target=pump, daemon=True)
                            thread.start()
                            try:
                                _member(proc.stdin, "manifest.json", len(body), io.BytesIO(body))
                                for record in records:
                                    if record is bundle_record:
                                        with open(bundle, "rb") as file:
                                            observed = _member(proc.stdin, "payload/" + record["path"],
                                                               record["size_bytes"], file)
                                        require(observed == record["sha256"], "STREAM_HASH_MISMATCH")
                                    else:
                                        _stream_source(proc.stdin, root_fd, record, metadata)
                                proc.stdin.write(b"\0" * 1024)
                                proc.stdin.close()
                            except BaseException:
                                proc.kill()
                                raise
                            finally:
                                thread.join(timeout=limits.child_seconds + 5)
                            require(not thread.is_alive() and not pump_errors, "ENCRYPTION_FAILED")
                        target.flush()
                        os.fsync(target.fileno())
                final, _, _ = scan(root_fd, limits, hashing=False)
                check = open_root(source)
                try:
                    require(final == metadata and fingerprint(os.fstat(check)) == metadata[""],
                            "INPUT_CHANGED")
                finally:
                    os.close(check)
                source_revision(repo, commit)
                check = _open_file(op_fd, CIPHERTEXT)
                try:
                    size, digest = _digest_fd(check, limits.archive_bytes)
                finally:
                    os.close(check)
                require(size > 0, "ENCRYPTION_FAILED")
                receipt = {"format_version": FORMAT, "checkpoint_id": checkpoint_id,
                           "ciphertext_size_bytes": size, "ciphertext_sha256": digest,
                           "status": "CREATED"}
                _same_directory(op, op_fd)
                _marker(op_fd, RECEIPT, receipt)
                # .INCOMPLETE stays as a harmless crash/protocol sentinel; only
                # the valid final receipt establishes checkpoint completeness.
                return assurance(capture="succeeded", coverage=declaration["status"],
                                 checkpoint_id=checkpoint_id, status="CREATED",
                                 ciphertext_sha256=digest, ciphertext_size_bytes=size)
    finally:
        os.close(root_fd)


def validate_manifest(m, limits=DEFAULT_LIMITS):
    exact(m, ("format_version", "checkpoint_id", "capture_time", "source_commit", "tool_versions",
              "logical_roots", "coverage", "file_count", "total_bytes", "directories", "files", "bundle"))
    require(m["format_version"] == FORMAT, "UNSUPPORTED_FORMAT")
    require(isinstance(m["checkpoint_id"], str) and ID.fullmatch(m["checkpoint_id"]), "INVALID_ID")
    require(isinstance(m["source_commit"], str) and COMMIT.fullmatch(m["source_commit"]), "INVALID_COMMIT")
    try:
        require(isinstance(m["capture_time"], str), "INVALID_CAPTURE_TIME")
        stamp = datetime.fromisoformat(m["capture_time"])
        require(stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0,
                "INVALID_CAPTURE_TIME")
    except ValueError:
        raise CheckpointError("INVALID_CAPTURE_TIME") from None
    exact(m["tool_versions"], ("age", "git", "python"))
    require(m["tool_versions"]["age"] == AGE_VERSION and all(
        isinstance(s, str) and 0 < len(s) < 128 for s in m["tool_versions"].values()), "INVALID_TOOLS")
    exact(m["logical_roots"], ("data", "repository"))
    for value in m["logical_roots"].values():
        require(isinstance(value, str) and len(value.encode()) <= limits.path_bytes, "INVALID_ROOT")
        require(str(_absolute(value)) == value, "INVALID_ROOT")
    coverage_declaration(m["coverage"])
    integer(m["file_count"], limits.files)
    integer(m["total_bytes"], limits.total_bytes)
    require(isinstance(m["directories"], list) and len(m["directories"]) <= limits.directories,
            "LIMIT_EXCEEDED")
    require(isinstance(m["files"], list) and len(m["files"]) == m["file_count"], "MEMBER_SET_INVALID")
    paths, total = [], 0
    for record in m["files"]:
        exact(record, ("path", "size_bytes", "sha256"))
        name = safe_path(record["path"], limits)
        safe_path("payload/" + name, limits)
        require(name.startswith("data/") or name == "repository/source.bundle", "UNSAFE_ARCHIVE_PATH")
        integer(record["size_bytes"], limits.bundle_bytes if name.startswith("repository/") else limits.file_bytes)
        require(isinstance(record["sha256"], str) and HEX.fullmatch(record["sha256"]), "INVALID_HASH")
        total += record["size_bytes"]
        paths.append(name)
    require(total == m["total_bytes"], "SIZE_MISMATCH")
    require(paths == sorted(paths), "MEMBER_ORDER_INVALID")
    require(m["directories"] == sorted(m["directories"]), "MEMBER_ORDER_INVALID")
    require(all(d in ("data", "repository") or (isinstance(d, str) and d.startswith("data/"))
                for d in m["directories"]), "UNSAFE_ARCHIVE_PATH")
    path_set(paths, m["directories"], limits)
    bundles = [r for r in m["files"] if r["path"] == "repository/source.bundle"]
    require(len(bundles) == 1 and m["bundle"] == bundles[0], "BUNDLE_IDENTITY_INVALID")
    require("data" in m["directories"] and "repository" in m["directories"], "MEMBER_SET_INVALID")


class ArchiveReader:
    """Strict manifest-first PAX/ustar subset; never calls extractall."""
    def __init__(self, stream, limits):
        self.stream, self.limits, self.total = stream, limits, 0

    def read(self, count):
        require(self.total + count <= self.limits.archive_bytes, "LIMIT_EXCEEDED")
        data = bytearray()
        while len(data) < count:
            part = self.stream.read(count - len(data))
            require(bool(part), "ARCHIVE_TRUNCATED")
            data.extend(part)
        self.total += count
        return bytes(data)

    def header(self):
        raw = self.read(512)
        require(raw != b"\0" * 512, "MISSING_MEMBER")
        try:
            info = tarfile.TarInfo.frombuf(raw, "utf-8", "strict")
        except (tarfile.TarError, ValueError, UnicodeError):
            raise CheckpointError("INVALID_TAR_HEADER") from None
        prefix = b""
        name = info.name
        if info.type == tarfile.XHDTYPE:
            integer(info.size, self.limits.pax_bytes)
            payload = self.read(info.size)
            padding = self.read(-info.size % 512)
            require(not any(padding), "INVALID_PADDING")
            # Exactly one UTF-8 path record; no global/size/sparse/other extensions.
            try:
                length, field = payload.split(b" ", 1)
                require(length.isdigit() and int(length) == len(payload), "UNSUPPORTED_EXTENSION")
                require(field.startswith(b"path=") and field.endswith(b"\n")
                        and field.count(b"\n") == 1, "UNSUPPORTED_EXTENSION")
                name = field[5:-1].decode("utf-8")
            except (ValueError, UnicodeError):
                raise CheckpointError("UNSUPPORTED_EXTENSION") from None
            prefix = raw + payload + padding
            raw = self.read(512)
            try:
                info = tarfile.TarInfo.frombuf(raw, "utf-8", "strict")
            except (tarfile.TarError, ValueError, UnicodeError):
                raise CheckpointError("INVALID_TAR_HEADER") from None
        require(info.type == tarfile.REGTYPE and info.linkname == "", "UNSAFE_MEMBER_TYPE")
        safe_path(name, self.limits)
        integer(info.size, self.limits.total_bytes)
        # Canonical metadata excludes privilege/ownership flags and opaque headers.
        require(prefix + raw == _header(name, info.size), "NONCANONICAL_TAR_HEADER")
        return name, info.size

    def member(self, name, size, sink=None):
        actual_name, actual_size = self.header()
        require(actual_name == name, "MEMBER_SET_INVALID")
        require(actual_size == size, "SIZE_MISMATCH")
        return self.body(size, sink)

    def body(self, size, sink=None):
        digest = hashlib.sha256()
        remaining = size
        while remaining:
            block = self.read(min(remaining, CHUNK))
            digest.update(block)
            if sink is not None:
                sink.write(block)
            remaining -= len(block)
        require(not any(self.read(-size % 512)), "INVALID_PADDING")
        return digest.hexdigest()

    def finish(self):
        require(self.read(1024) == b"\0" * 1024, "ARCHIVE_END_INVALID")
        # Exactly two end blocks; drain even disallowed trailing plaintext so
        # successful age exit/authentication is mandatory on the success path.
        trailing = 0
        while block := self.stream.read(CHUNK):
            trailing += len(block)
            require(self.total + trailing <= self.limits.archive_bytes, "LIMIT_EXCEEDED")
        require(trailing == 0, "TRAILING_ARCHIVE_DATA")


def _destination_file(root_fd, name, directory=False):
    parent = os.dup(root_fd)
    parts = name.split("/")
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = next_fd
        if directory:
            os.mkdir(parts[-1], 0o700, dir_fd=parent)
            return None
        return os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                       0o600, dir_fd=parent)
    finally:
        os.close(parent)


def _read_archive(stream, receipt, identity, destination, dest_fd, scratch, limits):
    reader = ArchiveReader(stream, limits)
    name, size = reader.header()
    require(name == "manifest.json", "MANIFEST_FIRST_REQUIRED")
    integer(size, limits.manifest_bytes)
    body = io.BytesIO()
    reader.body(size, body)
    manifest = parse_json(body.getvalue())
    validate_manifest(manifest, limits)
    require(manifest["checkpoint_id"] == receipt["checkpoint_id"], "INVALID_ID")
    roots = [Path(p) for p in manifest["logical_roots"].values()]
    require(not any(_within(identity, p) for p in roots), "KEY_SCOPE_OVERLAP")
    for root in roots:
        _disjoint(Path(scratch), root)
    if destination is not None:
        for root in roots:
            _disjoint(destination, root)
        for directory in sorted(manifest["directories"], key=lambda p: (p.count("/"), p)):
            _destination_file(dest_fd, directory, directory=True)
    bundle_path = Path(scratch) / "source.bundle"
    for record in manifest["files"]:
        bundle = record["path"] == "repository/source.bundle"
        if destination is not None:
            fd = _destination_file(dest_fd, record["path"])
            with os.fdopen(fd, "wb") as file:
                digest = reader.member("payload/" + record["path"], record["size_bytes"], file)
                file.flush()
                os.fsync(file.fileno())
            if bundle:
                bundle_path = destination / record["path"]
        elif bundle:
            with open(bundle_path, "xb") as file:
                os.chmod(bundle_path, 0o600)
                digest = reader.member("payload/" + record["path"], record["size_bytes"], file)
        else:
            digest = reader.member("payload/" + record["path"], record["size_bytes"])
        require(digest == record["sha256"], "MEMBER_HASH_MISMATCH")
    reader.finish()
    return manifest, bundle_path


def _consume(checkpoint, identity, scratch_parent, destination, dest_fd, age, limits):
    executable = age_tool(age)
    checkpoint, cp_fd = _directory(checkpoint, private=True)
    identity = _absolute(identity)
    try:
        scratch_parent, scratch_fd = _directory(scratch_parent, private=True)
        os.close(scratch_fd)
        _disjoint(scratch_parent, checkpoint)
        _disjoint(scratch_parent, Path(__file__).resolve().parents[1])
        receipt = parse_json(_small_file(checkpoint / RECEIPT, 4096))
        exact(receipt, ("format_version", "checkpoint_id", "ciphertext_size_bytes", "ciphertext_sha256", "status"))
        require(receipt["format_version"] == FORMAT and receipt["status"] == "CREATED", "UNSUPPORTED_FORMAT")
        require(isinstance(receipt["checkpoint_id"], str) and ID.fullmatch(receipt["checkpoint_id"]), "INVALID_ID")
        require(checkpoint.name == receipt["checkpoint_id"], "INVALID_ID")
        integer(receipt["ciphertext_size_bytes"], limits.archive_bytes)
        require(isinstance(receipt["ciphertext_sha256"], str) and HEX.fullmatch(receipt["ciphertext_sha256"]),
                "INVALID_HASH")
        fd = _open_file(cp_fd, CIPHERTEXT)
        try:
            before = fingerprint(os.fstat(fd))
            size, digest = _digest_fd(fd, limits.archive_bytes)
            require(size == receipt["ciphertext_size_bytes"] and digest == receipt["ciphertext_sha256"],
                    "CIPHERTEXT_HASH_MISMATCH")
            os.lseek(fd, 0, os.SEEK_SET)
            scopes = (checkpoint, scratch_parent) if destination is None else (
                checkpoint, scratch_parent, destination)
            with key_pipe(identity, secret=True, scopes=scopes) as key_fd:
                # Only the committed-source Git bundle is temporarily materialized
                # under the explicit private scratch parent. Data members are
                # hashed/discarded during verify, never extracted.
                with tempfile.TemporaryDirectory(prefix=".checkpoint-verify-",
                                                 dir=scratch_parent) as scratch:
                    os.chmod(scratch, 0o700)
                    with child([executable, "--decrypt", "--identity", f"/dev/fd/{key_fd}"],
                               stdin=fd, pass_fds=(key_fd,), limits=limits) as proc:
                        manifest, bundle_path = _read_archive(proc.stdout, receipt, identity,
                                                              destination, dest_fd, scratch, limits)
                    require(fingerprint(os.fstat(fd)) == before, "INPUT_CHANGED")
                    check_bundle(bundle_path, manifest["source_commit"], scratch, limits)
                    if destination is not None:
                        _exclusive_file(dest_fd, "manifest.json", canonical(manifest))
                    return manifest
        finally:
            os.close(fd)
    finally:
        os.close(cp_fd)


def verify(*, checkpoint, identity, scratch_parent, age="age", limits=DEFAULT_LIMITS):
    manifest = _consume(checkpoint, identity, scratch_parent, None, None, age, limits)
    return assurance(crypto="succeeded", coverage=manifest["coverage"]["status"],
                     status="VERIFIED", checkpoint_id=manifest["checkpoint_id"],
                     file_count=manifest["file_count"], total_bytes=manifest["total_bytes"])


def restore(*, checkpoint, identity, scratch_parent, destination, age="age", limits=DEFAULT_LIMITS):
    age_tool(age)  # no output directory when age is missing/broken
    destination = _absolute(destination)
    # Never restore into this tool's repository, even if archive roots are forged.
    tool_repo = Path(__file__).resolve().parents[1]
    _disjoint(destination, tool_repo)
    _disjoint(destination, _absolute(checkpoint))
    require(not _within(_absolute(identity), destination), "KEY_SCOPE_OVERLAP")
    safe_path(destination.name, limits)
    # Learn authenticated private source roots BEFORE creating anything at the
    # requested destination. Otherwise a refused restore could mutate its source.
    manifest = _consume(checkpoint, identity, scratch_parent, None, None, age, limits)
    for root in manifest["logical_roots"].values():
        _disjoint(destination, Path(root))
    with operation(destination.parent, destination.name) as (op, fd):
        restored_manifest = _consume(checkpoint, identity, scratch_parent, op, fd, age, limits)
        require(restored_manifest == manifest, "INPUT_CHANGED")
        report = assurance(crypto="succeeded", coverage=manifest["coverage"]["status"],
                           status="RESTORE_VERIFIED", checkpoint_id=manifest["checkpoint_id"],
                           file_count=manifest["file_count"], total_bytes=manifest["total_bytes"])
        _same_directory(op, fd)
        _marker(fd, RESTORED, report)
        return report


class PrivateParser(argparse.ArgumentParser):
    def error(self, message):
        raise CheckpointError("INVALID_ARGUMENTS")


def main(argv=None):
    parser = PrivateParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True, parser_class=PrivateParser)
    create_parser = sub.add_parser("create")
    for name in ("source-root", "repository", "commit", "recipients", "output-parent", "coverage"):
        create_parser.add_argument("--" + name, required=True)
    create_parser.add_argument("--quiescent", action="store_true", required=True)
    for operation_name in ("verify", "restore"):
        item = sub.add_parser(operation_name)
        item.add_argument("--checkpoint", required=True)
        item.add_argument("--identity", required=True)
        item.add_argument("--scratch-parent", required=True)
        if operation_name == "restore":
            item.add_argument("--destination", required=True)
    try:
        args = vars(parser.parse_args(argv))
        operation_name = args.pop("operation")
        result = {"create": create, "verify": verify, "restore": restore}[operation_name](**args)
    except (CheckpointError, OSError, ValueError, TypeError, KeyError, RecursionError,
            subprocess.SubprocessError, tarfile.TarError):
        exc = sys.exc_info()[1]
        code = exc.code if isinstance(exc, CheckpointError) else "UNSAFE_OR_FAILED_OPERATION"
        print(json.dumps({"status": "FAILED", "code": code,
                          **assurance(capture="failed" if locals().get("operation_name") == "create" else "not_run",
                                      crypto="failed" if locals().get("operation_name") in ("verify", "restore") else "not_run")}),
              file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
