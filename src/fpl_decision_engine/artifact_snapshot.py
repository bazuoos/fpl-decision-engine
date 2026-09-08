"""Request-scoped immutable snapshots for trusted artifact validation."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
import stat
import tempfile


class ArtifactSnapshotError(OSError):
    """Raised when one regular artifact cannot be captured consistently."""


@dataclass(frozen=True)
class _CapturedArtifact:
    snapshot_path: Path
    sha256: str


class ReadOnceArtifactSnapshot:
    """Capture each original path once and validate only stable private copies.

    Instances are synchronous request objects and must not be shared across threads.
    """

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="fpl-artifact-snapshot-")
        self._root = Path(self._temporary.name)
        self._captured: dict[Path, _CapturedArtifact] = {}
        self._closed = False

    def __enter__(self) -> ReadOnceArtifactSnapshot:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            try:
                self._temporary.cleanup()
            except OSError as exc:
                raise ArtifactSnapshotError(
                    "could not remove private artifact snapshot"
                ) from exc
            self._closed = True

    @staticmethod
    def _identity(info: os.stat_result) -> tuple[int, ...]:
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

    def _require_open(self) -> None:
        if self._closed:
            raise ArtifactSnapshotError("artifact snapshot is closed")

    @staticmethod
    def _absolute(path: Path) -> Path:
        try:
            return Path(os.path.abspath(os.fspath(path)))
        except (OSError, TypeError, ValueError) as exc:
            raise ArtifactSnapshotError("could not normalize artifact path") from exc

    @staticmethod
    def _open_regular(path: Path) -> int:
        """Open one lexical absolute path without following any symlink."""
        parts = path.parts
        if not path.is_absolute() or len(parts) < 2:
            raise ArtifactSnapshotError("artifact path is not a file path")
        directory_fd: int | None = None
        source_fd: int | None = None
        directory_flags = (
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        try:
            directory_fd = os.open(os.sep, directory_flags)
            for component in parts[1:-1]:
                nested_fd = os.open(component, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = nested_fd
            source_fd = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
            info = os.fstat(source_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ArtifactSnapshotError(
                    "artifact must be a single-link regular file"
                )
            return source_fd
        except ArtifactSnapshotError:
            if source_fd is not None:
                os.close(source_fd)
            raise
        except OSError as exc:
            if source_fd is not None:
                os.close(source_fd)
            raise ArtifactSnapshotError("could not safely open artifact") from exc
        finally:
            if directory_fd is not None:
                os.close(directory_fd)

    @staticmethod
    def _open_destination(path: Path) -> int:
        try:
            return os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
        except OSError as exc:
            raise ArtifactSnapshotError(
                "could not create private artifact snapshot file"
            ) from exc

    def _destination(self, source: Path) -> Path:
        directory = self._root / f"artifact-{len(self._captured):04d}"
        try:
            directory.mkdir(mode=0o700)
        except OSError as exc:
            raise ArtifactSnapshotError(
                "could not create private artifact snapshot directory"
            ) from exc
        return directory / source.name

    def seed(self, path: Path, body: bytes) -> None:
        """Supply bytes already captured by the caller without reopening the path."""
        self._require_open()
        if not isinstance(body, bytes):
            raise ArtifactSnapshotError("seeded artifact body must be bytes")
        absolute = self._absolute(path)
        existing = self._captured.get(absolute)
        if existing is not None:
            if self.read_bytes(absolute) != body:
                raise ArtifactSnapshotError("conflicting seeded artifact bytes")
            return
        destination = self._destination(absolute)
        output_fd: int | None = None
        try:
            output_fd = self._open_destination(destination)
            with os.fdopen(output_fd, "wb") as output:
                output_fd = None
                output.write(body)
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise ArtifactSnapshotError("could not store seeded artifact") from exc
        finally:
            if output_fd is not None:
                os.close(output_fd)
        self._captured[absolute] = _CapturedArtifact(
            destination,
            hashlib.sha256(body).hexdigest(),
        )

    def _capture(self, path: Path) -> _CapturedArtifact:
        self._require_open()
        absolute = self._absolute(path)
        existing = self._captured.get(absolute)
        if existing is not None:
            return existing

        destination = self._destination(absolute)
        source_fd: int | None = None
        output_fd: int | None = None
        try:
            source_fd = self._open_regular(absolute)
            before = os.fstat(source_fd)
            digest = hashlib.sha256()
            copied = 0
            output_fd = self._open_destination(destination)
            with os.fdopen(output_fd, "wb") as output:
                output_fd = None
                while True:
                    chunk = os.read(source_fd, 1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    copied += len(chunk)
            after = os.fstat(source_fd)
            if (
                self._identity(before) != self._identity(after)
                or copied != before.st_size
            ):
                raise ArtifactSnapshotError("artifact changed while being captured")
        except ArtifactSnapshotError:
            destination.unlink(missing_ok=True)
            raise
        except OSError as exc:
            destination.unlink(missing_ok=True)
            raise ArtifactSnapshotError("could not capture artifact") from exc
        finally:
            if output_fd is not None:
                os.close(output_fd)
            if source_fd is not None:
                os.close(source_fd)

        captured = _CapturedArtifact(destination, digest.hexdigest())
        self._captured[absolute] = captured
        return captured

    def read_bytes(self, path: Path) -> bytes:
        captured = self._capture(path)
        try:
            return captured.snapshot_path.read_bytes()
        except OSError as exc:  # pragma: no cover - private snapshot invariant
            raise ArtifactSnapshotError("could not read captured artifact") from exc

    def sha256(self, path: Path) -> str:
        return self._capture(path).sha256

    def materialized_path(self, path: Path) -> Path:
        """Return the stable private path used by path-only validators."""
        return self._capture(path).snapshot_path
