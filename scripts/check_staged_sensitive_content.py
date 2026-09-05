"""Fail-closed staged-blob scanner for exact private values and age identities."""
import argparse
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


MAX_DENYLIST_BYTES = 64 * 1024
MAX_DENYLIST_ENTRIES = 256
MAX_VALUE_BYTES = 4096
MIN_VALUE_BYTES = 8
READ_SIZE = 64 * 1024
AGE_IDENTITY = re.compile(rb"AGE-SECRET-KEY-1[0-9A-Z]{58}")
ALLOWED_BLOB_MODES = {b"100644", b"100755", b"120000"}
GITLINK_MODE = b"160000"


class GuardError(Exception):
    """Expected validation or Git-read failure with no private detail."""


def git_environment() -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_INDEX_FILE", "GIT_NAMESPACE",
        "GIT_CEILING_DIRECTORIES", "GIT_EXEC_PATH",
    ):
        env.pop(name, None)
    for name in tuple(env):
        if name.startswith("GIT_CONFIG_"):
            env.pop(name)
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def run_git(repo: str, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "--no-replace-objects", "-C", repo, *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=git_environment(),
        check=False,
    )
    if result.returncode:
        raise GuardError
    return result.stdout


def repository_root(repo: str) -> Path:
    raw = run_git(repo, "rev-parse", "--show-toplevel")
    try:
        value = raw.decode("utf-8", "strict").rstrip("\n")
    except UnicodeDecodeError as exc:
        raise GuardError from exc
    if not value or "\n" in value or "\0" in value:
        raise GuardError
    return Path(value).resolve(strict=True)


def open_private_file(path_text: str) -> tuple[int, os.stat_result, Path]:
    if not path_text or not os.path.isabs(path_text):
        raise GuardError
    components = path_text.split(os.sep)
    if any(component in {".", ".."} for component in components):
        raise GuardError
    normalized = Path(os.path.abspath(path_text))
    parts = normalized.parts[1:]
    if not parts:
        raise GuardError

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    current = os.open(os.sep, directory_flags)
    try:
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags | nofollow, dir_fd=current)
            os.close(current)
            current = next_fd
        parent = os.fstat(current)
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077:
            raise GuardError
        file_fd = os.open(parts[-1], os.O_RDONLY | nofollow, dir_fd=current)
    except (OSError, ValueError) as exc:
        raise GuardError from exc
    finally:
        os.close(current)

    try:
        metadata = os.fstat(file_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_nlink != 1
            or metadata.st_size > MAX_DENYLIST_BYTES
        ):
            raise GuardError
        return file_fd, metadata, normalized
    except Exception:
        os.close(file_fd)
        raise


def read_denylist(path_text: str, repo_root: Path) -> tuple[bytes, ...]:
    fd, before, path = open_private_file(path_text)
    try:
        try:
            if os.path.commonpath((str(repo_root), str(path))) == str(repo_root):
                raise GuardError
        except ValueError as exc:
            raise GuardError from exc

        chunks: list[bytes] = []
        remaining = MAX_DENYLIST_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(READ_SIZE, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        after = os.fstat(fd)
        try:
            named = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise GuardError from exc
        identity = lambda item: (
            item.st_dev, item.st_ino, item.st_mode, item.st_uid, item.st_nlink,
            item.st_size, item.st_mtime_ns, item.st_ctime_ns,
        )
        if len(content) > MAX_DENYLIST_BYTES or identity(before) != identity(after):
            raise GuardError
        if identity(after) != identity(named):
            raise GuardError
    finally:
        os.close(fd)

    if not content or b"\0" in content or b"\r" in content:
        raise GuardError
    lines = content.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    if not lines or len(lines) > MAX_DENYLIST_ENTRIES or len(lines) != len(set(lines)):
        raise GuardError
    for value in lines:
        if (
            len(value) < MIN_VALUE_BYTES
            or len(value) > MAX_VALUE_BYTES
            or any(byte < 0x21 or byte > 0x7E for byte in value)
        ):
            raise GuardError
    return tuple(lines)


def parse_index(raw: bytes) -> list[bytes]:
    objects: list[bytes] = []
    seen_paths: set[bytes] = set()
    for record in raw.split(b"\0"):
        if not record:
            continue
        header, separator, path = record.partition(b"\t")
        fields = header.split(b" ")
        if not separator or not path or len(fields) != 3:
            raise GuardError
        mode, object_id, stage = fields
        if stage != b"0" or path in seen_paths:
            raise GuardError
        seen_paths.add(path)
        if mode == GITLINK_MODE:
            continue
        if mode not in ALLOWED_BLOB_MODES:
            raise GuardError
        if len(object_id) not in {40, 64} or any(
            byte not in b"0123456789abcdef" for byte in object_id
        ):
            raise GuardError
        objects.append(object_id)
    return objects


def contains_sensitive(repo: str, object_id: bytes, values: tuple[bytes, ...]) -> bool:
    try:
        object_text = object_id.decode("ascii", "strict")
    except UnicodeDecodeError as exc:
        raise GuardError from exc
    process = subprocess.Popen(
        ["git", "--no-replace-objects", "-C", repo, "cat-file", "blob", object_text],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=git_environment(),
    )
    assert process.stdout is not None
    overlap = max(max(map(len, values)), 76) - 1
    tail = b""
    found = False
    try:
        while True:
            chunk = process.stdout.read(READ_SIZE)
            if not chunk:
                break
            candidate = tail + chunk
            if AGE_IDENTITY.search(candidate) or any(value in candidate for value in values):
                found = True
                break
            tail = candidate[-overlap:]
    except OSError as exc:
        process.kill()
        process.wait()
        raise GuardError from exc
    finally:
        process.stdout.close()
    returncode = process.wait()
    if found:
        return True
    if returncode:
        raise GuardError
    return False


def check(repo: str, denylist: str) -> bool:
    root = repository_root(repo)
    values = read_denylist(denylist, root)
    before = run_git(repo, "ls-files", "--cached", "--stage", "--full-name", "-z", ":/")
    objects = parse_index(before)
    for object_id in dict.fromkeys(objects):
        if contains_sensitive(repo, object_id, values):
            return False
    after = run_git(repo, "ls-files", "--cached", "--stage", "--full-name", "-z", ":/")
    if before != after:
        raise GuardError
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Repository whose full index is checked")
    parser.add_argument("--denylist", required=True,
                        help="Absolute owner-private file; one exact value per line")
    args = parser.parse_args()
    try:
        passed = check(args.repo, args.denylist)
    except (GuardError, OSError, subprocess.SubprocessError):
        print("Cannot complete staged-content check; guard failed closed.", file=sys.stderr)
        return 2
    if not passed:
        print("Sensitive content found in Git index; do not commit or push.", file=sys.stderr)
        return 1
    print("Staged-content guard passed; no configured sensitive value was found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
