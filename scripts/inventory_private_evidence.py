"""Read-only byte inventory; aggregate stdout unless --private-manifest is explicit."""
import argparse
import hashlib
import json
import os
import stat
import sys


class InventoryError(Exception):
    """Unsafe or unstable input; messages intentionally omit private paths."""


def fingerprint(st):
    return (st.st_dev, st.st_ino, st.st_mode, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def open_root(path):
    # Reject lexical parent traversal before abspath can normalize it away.
    if ".." in os.fspath(path).split(os.sep):
        raise InventoryError("Parent traversal rejected.")
    # Walk every component without following symlinks, including parent directories.
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in os.path.abspath(path).split("/"):
            if not component:
                continue
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                              dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def walk(fd, prefix, metadata, records, hash_files):
    before = os.fstat(fd)
    metadata[prefix] = fingerprint(before)
    for name in sorted(os.listdir(fd)):
        relative = f"{prefix}/{name}" if prefix else name
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                if fingerprint(os.fstat(child)) != fingerprint(info):
                    raise InventoryError("Directory changed during scan.")
                walk(child, relative, metadata, records, hash_files)
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode):
            metadata[relative] = fingerprint(info)
            if hash_files:
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                try:
                    if fingerprint(os.fstat(child)) != fingerprint(info):
                        raise InventoryError("File changed during scan.")
                    digest = hashlib.sha256()
                    size = 0
                    while block := os.read(child, 1024 * 1024):
                        digest.update(block)
                        size += len(block)
                    if fingerprint(os.fstat(child)) != fingerprint(info) or size != info.st_size:
                        raise InventoryError("File changed during scan.")
                    records.append({"path": relative, "size_bytes": size,
                                    "sha256": digest.hexdigest()})
                finally:
                    os.close(child)
        else:
            raise InventoryError("Symlink or special file rejected.")
    if fingerprint(os.fstat(fd)) != fingerprint(before):
        raise InventoryError("Directory changed during scan.")


def inventory(root):
    fd = open_root(root)
    try:
        initial, records = {}, []
        walk(fd, "", initial, records, True)
        final = {}
        walk(fd, "", final, [], False)
        # Also verify the requested root still names the same directory.
        check_fd = open_root(root)
        try:
            if fingerprint(os.fstat(check_fd)) != initial[""] or final != initial:
                raise InventoryError("Tree changed during scan.")
        finally:
            os.close(check_fd)
        return {"format_version": 1, "file_count": len(records),
                "size_bytes": sum(r["size_bytes"] for r in records), "files": records}
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, help="Explicit directory to inventory")
    parser.add_argument("--private-manifest", action="store_true",
                        help="Explicitly expose relative file paths and hashes on stdout")
    args = parser.parse_args()
    try:
        result = inventory(args.source_root)
    except (OSError, InventoryError):
        print("Inventory failed: unsafe, unreadable or changing input. "
              "No complete inventory produced.", file=sys.stderr)
        return 1
    if not args.private_manifest:
        result.pop("files")
    result["assurance"] = "Byte inventory only; no semantic, external-evidence or backup verification."
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
