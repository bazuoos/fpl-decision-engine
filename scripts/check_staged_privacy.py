"""Read-only index path guard; not a content/secret scanner or installed hook."""
import argparse
import json
import subprocess
import sys


def forbidden(path: bytes) -> bool:
    parts = path.split(b"/")
    return (
        parts[0] in {b"data", b".private-recovery"}
        or b".DS_Store" in parts
        or path in {b"task025_claude_review_bundle.txt", b"task025_review.patch"}
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="Repository to check (read-only)")
    args = parser.parse_args()
    result = subprocess.run(
        ["git", "-C", args.repo, "ls-files", "--cached", "--full-name", "-z", ":/"],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        print("Cannot read Git index; privacy check failed closed.", file=sys.stderr)
        return 2
    blocked = sorted({p for p in result.stdout.split(b"\0") if p and forbidden(p)})
    if blocked:
        print("Private paths found in index; do not commit or push:", file=sys.stderr)
        for path in blocked:
            # JSON escaping makes newlines/control characters unambiguous.
            print(json.dumps(path.decode("utf-8", "surrogateescape")), file=sys.stderr)
        print("Remove these paths from the index without deleting working files, "
              "then rerun this guard. No changes were made.", file=sys.stderr)
        return 1
    print("Index path guard passed. This does not detect secrets in allowed paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
