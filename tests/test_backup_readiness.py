"""Synthetic-only checks for local readiness tools; no production evidence reads."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("inventory", ROOT / "scripts/inventory_private_evidence.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BackupReadinessTests(unittest.TestCase):
    def test_index_guard_and_ignores(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "-q", folder], check=True)
            (root / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
            allowed = "tests/fixtures/data/example.json"
            blocked = ["data/operations/private\nfile.json", "a/.DS_Store",
                       ".private-recovery/inventory.json", "task025_review.patch",
                       "task025_claude_review_bundle.txt"]
            for name in [allowed] + blocked:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic")
            for name in blocked[:3]:
                self.assertEqual(subprocess.run(["git", "-C", folder, "check-ignore", "-q", name]).returncode, 0)
            self.assertEqual(subprocess.run(["git", "-C", folder, "check-ignore", "-q", allowed]).returncode, 1)
            subprocess.run(["git", "-C", folder, "add", allowed], check=True)
            command = [sys.executable, str(ROOT / "scripts/check_staged_privacy.py"), "--repo", folder]
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            subprocess.run(["git", "-C", folder, "add", "-f", "--", *blocked], check=True)
            index_before = (root / ".git/index").read_bytes()
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('private\\nfile.json', result.stderr)
            self.assertNotIn("synthetic", result.stderr)
            self.assertEqual(index_before, (root / ".git/index").read_bytes())
            nested = subprocess.run(command[:-1] + [str(root / "tests")], capture_output=True)
            self.assertEqual(nested.returncode, 1)

    def test_inventory_and_private_opt_in(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / "private-name").write_bytes(b"synthetic")
            command = [sys.executable, str(ROOT / "scripts/inventory_private_evidence.py"),
                       "--source-root", str(root)]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertNotIn("private-name", result.stdout)
            self.assertEqual(json.loads(result.stdout)["file_count"], 1)
            private = subprocess.run(command + ["--private-manifest"], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(private.stdout)["files"], [{"path": "private-name", "size_bytes": 9,
                              "sha256": hashlib.sha256(b"synthetic").hexdigest()}])

    def test_symlink_and_special_file_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / "link").symlink_to("missing")
            with self.assertRaises(MODULE.InventoryError):
                MODULE.inventory(root)
            (root / "link").unlink()
            MODULE.os.mkfifo(root / "pipe")
            with self.assertRaises(MODULE.InventoryError):
                MODULE.inventory(root)

    def test_file_change_during_read_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            path = root / "file"
            path.write_bytes(b"before")
            original = MODULE.os.read
            changed = False
            def mutate(fd, count):
                nonlocal changed
                value = original(fd, count)
                if not changed:
                    changed = True
                    path.write_bytes(b"changed length")
                return value
            with patch.object(MODULE.os, "read", side_effect=mutate):
                with self.assertRaises(MODULE.InventoryError):
                    MODULE.inventory(root)

    def test_tree_addition_between_passes_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / "file").write_bytes(b"before")
            original = MODULE.walk
            def mutate(fd, prefix, metadata, records, hash_files):
                original(fd, prefix, metadata, records, hash_files)
                if hash_files and prefix == "":
                    (root / "added").write_bytes(b"new")
            with patch.object(MODULE, "walk", side_effect=mutate):
                with self.assertRaises(MODULE.InventoryError):
                    MODULE.inventory(root)

    def test_parent_traversal_rejected_before_normalization(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / "link").symlink_to(root, target_is_directory=True)
            with self.assertRaises(MODULE.InventoryError):
                MODULE.inventory(str(root / "link") + "/..")

    def test_failure_stdout_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / "sensitive-name").symlink_to("missing")
            result = subprocess.run([sys.executable, str(ROOT / "scripts/inventory_private_evidence.py"),
                                     "--source-root", str(root), "--private-manifest"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("sensitive-name", result.stderr)


if __name__ == "__main__":
    unittest.main()
