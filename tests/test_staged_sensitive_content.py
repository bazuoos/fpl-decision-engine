"""Synthetic-only checks for the staged-content guard."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_staged_sensitive_content.py"
SPEC = importlib.util.spec_from_file_location("staged_sensitive", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class StagedSensitiveContentTests(unittest.TestCase):
    def make_case(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        repo = base / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        denylist = base / "denylist"
        denylist.write_bytes(b"synthetic-private-value\n")
        denylist.chmod(0o600)
        command = [sys.executable, str(SCRIPT), "--repo", str(repo),
                   "--denylist", str(denylist)]
        return base, repo, denylist, command

    @staticmethod
    def stage(repo: Path, name: str, content: bytes):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        subprocess.run(["git", "-C", str(repo), "add", "--", name], check=True)

    def test_clean_index_passes_without_mutation(self):
        _, repo, _, command = self.make_case()
        self.stage(repo, "allowed.bin", b"ordinary synthetic bytes")
        before = (repo / ".git/index").read_bytes()
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertIn("guard passed", result.stdout)
        self.assertEqual(before, (repo / ".git/index").read_bytes())

    def test_exact_value_is_found_without_disclosure(self):
        _, repo, _, command = self.make_case()
        self.stage(repo, "permitted/document.txt", b"prefix synthetic-private-value suffix")
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("synthetic-private-value", result.stderr)
        self.assertNotIn("document.txt", result.stderr)

    def test_age_identity_pattern_is_found_without_denylist_entry(self):
        _, repo, _, command = self.make_case()
        identity = b"AGE-SECRET-KEY-1" + b"B" * 58
        self.stage(repo, "notes.md", b"prefix " + identity + b" suffix")
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(identity.decode(), result.stderr)
        self.assertNotIn("notes.md", result.stderr)

    def test_match_across_stream_boundary_is_found(self):
        _, repo, _, command = self.make_case()
        prefix = b"x" * (MODULE.READ_SIZE - 5)
        self.stage(repo, "large.bin", prefix + b"synthetic-private-value")
        result = subprocess.run(command, capture_output=True)
        self.assertEqual(result.returncode, 1)

    def test_only_staged_blob_is_scanned(self):
        _, repo, _, command = self.make_case()
        self.stage(repo, "candidate.txt", b"clean staged bytes")
        (repo / "candidate.txt").write_bytes(b"synthetic-private-value")
        (repo / "untracked.txt").write_bytes(b"synthetic-private-value")
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
        subprocess.run(["git", "-C", str(repo), "add", "candidate.txt"], check=True)
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 1)

    def test_staged_symlink_target_is_scanned(self):
        _, repo, _, command = self.make_case()
        (repo / "link").symlink_to("synthetic-private-value")
        subprocess.run(["git", "-C", str(repo), "add", "link"], check=True)
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 1)

    def test_git_environment_cannot_redirect_index(self):
        base, repo, _, command = self.make_case()
        self.stage(repo, "secret", b"synthetic-private-value")
        alternate = base / "alternate-index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(alternate)
        subprocess.run(["git", "-C", str(repo), "read-tree", "--empty"],
                       env=env, check=True)
        result = subprocess.run(command, env=env, capture_output=True)
        self.assertEqual(result.returncode, 1)

    def test_git_replace_object_cannot_hide_staged_value(self):
        _, repo, _, command = self.make_case()
        self.stage(repo, "secret", b"synthetic-private-value")
        secret_id = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", ":secret"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        clean_id = subprocess.run(
            ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
            input="clean replacement", capture_output=True, text=True, check=True,
        ).stdout.strip()
        subprocess.run(["git", "-C", str(repo), "replace", secret_id, clean_id], check=True)
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 1)

    def test_denylist_must_be_private_regular_outside_repository(self):
        base, repo, denylist, command = self.make_case()
        self.stage(repo, "clean", b"clean staged bytes")
        for content in (b"", b"short\n", b"duplicate-value\nduplicate-value\n",
                        b"valid-value\r\n", b"space value\n"):
            denylist.write_bytes(content)
            denylist.chmod(0o600)
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn(str(denylist), result.stderr)

        denylist.write_bytes(b"synthetic-private-value\n")
        denylist.chmod(0o644)
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
        denylist.chmod(0o600)

        inside = repo / "denylist"
        inside.write_bytes(b"synthetic-private-value\n")
        inside.chmod(0o600)
        inside_command = command[:-1] + [str(inside)]
        self.assertEqual(subprocess.run(inside_command, capture_output=True).returncode, 2)

        linked = base / "linked-denylist"
        linked.symlink_to(denylist)
        linked_command = command[:-1] + [str(linked)]
        self.assertEqual(subprocess.run(linked_command, capture_output=True).returncode, 2)

        linked_parent = base / "linked-parent"
        linked_parent.symlink_to(base, target_is_directory=True)
        parent_command = command[:-1] + [str(linked_parent / "denylist")]
        self.assertEqual(subprocess.run(parent_command, capture_output=True).returncode, 2)

        traversal_command = command[:-1] + [str(base / "missing" / ".." / "denylist")]
        self.assertEqual(subprocess.run(traversal_command, capture_output=True).returncode, 2)

    def test_hardlinked_denylist_is_rejected(self):
        base, repo, denylist, command = self.make_case()
        self.stage(repo, "clean", b"clean staged bytes")
        os.link(denylist, base / "second-name")
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)

    def test_denylist_parent_must_be_owner_private(self):
        base, repo, _, command = self.make_case()
        self.stage(repo, "clean", b"clean staged bytes")
        base.chmod(0o755)
        self.addCleanup(base.chmod, 0o700)
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)

    def test_index_change_and_git_failure_fail_closed(self):
        _, repo, denylist, _ = self.make_case()
        self.stage(repo, "clean", b"clean staged bytes")
        original = MODULE.run_git
        listings = 0

        def changed(repo_arg, *args):
            nonlocal listings
            value = original(repo_arg, *args)
            if args and args[0] == "ls-files":
                listings += 1
                if listings == 2:
                    return value + b"changed"
            return value

        with patch.object(MODULE, "run_git", side_effect=changed):
            with self.assertRaises(MODULE.GuardError):
                MODULE.check(str(repo), str(denylist))
        self.assertEqual(subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(repo / "missing"),
             "--denylist", str(denylist)], capture_output=True
        ).returncode, 2)

    def test_unmerged_or_unknown_index_entries_fail_closed(self):
        object_id = b"a" * 40
        with self.assertRaises(MODULE.GuardError):
            MODULE.parse_index(b"100644 " + object_id + b" 1\tfile\0")
        with self.assertRaises(MODULE.GuardError):
            MODULE.parse_index(b"100600 " + object_id + b" 0\tfile\0")


if __name__ == "__main__":
    unittest.main()
