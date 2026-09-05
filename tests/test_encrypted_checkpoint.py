"""Real age + isolated synthetic repositories only. Missing crypto is a failure.

No production data, stored identities, service credentials or network access.
"""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts import encrypted_checkpoint as cp


SCRIPT = Path(cp.__file__).resolve()


class EncryptedCheckpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Intentionally NOT skipUnless: no fake success when crypto is absent.
        cls.age = cp.age_tool()
        cls.keygen = shutil.which("age-keygen")
        if cls.keygen is None:
            raise RuntimeError("AGE_KEYGEN_REQUIRED: real-crypto acceptance unexecuted")
        if cp.command([cls.keygen, "--version"]).decode().strip() != cp.AGE_VERSION:
            raise RuntimeError("AGE_KEYGEN_VERSION_UNSUPPORTED")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / "repo"
        self.source = self.root / "sensitive-source"
        self.output = self.root / "private-output"
        self.scratch = self.root / "private-scratch"
        for path in (self.repo, self.source, self.output, self.scratch):
            path.mkdir(mode=0o700)
        self.git("init", "-q")
        (self.repo / "app.txt").write_bytes(b"committed synthetic source\n")
        self.git("add", "app.txt")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "synthetic")
        self.commit = self.git("rev-parse", "HEAD").decode().strip()
        self.identity = self.root / "ephemeral-key"
        subprocess.run([self.keygen, "-o", str(self.identity)], capture_output=True, check=True)
        self.identity.chmod(0o600)
        self.recipient = self.root / "ephemeral-recipient"
        public = subprocess.run([self.keygen, "-y", str(self.identity)], capture_output=True, check=True).stdout
        self.recipient.write_bytes(public)
        self.coverage = self.root / "private-coverage.json"
        self.gaps = {"status": "known_gaps", "gaps": ["SENSITIVE: original screenshot NOT LOCATED"]}
        self.coverage.write_bytes(cp.canonical(self.gaps))
        (self.source / "private-player-name.json").write_bytes(b'{"synthetic":null}\n')
        (self.source / "empty-dir").mkdir()
        long = self.source / ("a" * 120) / ("b" * 120)
        long.mkdir(parents=True)
        (long / "unicode-é.txt").write_bytes(bytes(range(256)) * 20)
        self.kw = dict(source_root=self.source, repository=self.repo, commit=self.commit,
                       recipients=self.recipient, output_parent=self.output,
                       coverage=self.coverage, quiescent=True)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, check=True).stdout

    def capture(self):
        result = cp.create(**self.kw)
        return self.output / result["checkpoint_id"], result

    def archive(self):
        checkpoint, _ = self.capture()
        plain = subprocess.run([self.age, "--decrypt", "-i", str(self.identity),
                                str(checkpoint / cp.CIPHERTEXT)], capture_output=True, check=True).stdout
        # Test-only in-memory synthetic archive inspection, not production verify.
        with tarfile.open(fileobj=io.BytesIO(plain), mode="r:") as tar:
            contents = {m.name: tar.extractfile(m).read() for m in tar}
        return checkpoint, plain, json.loads(contents.pop("manifest.json")), contents

    def forge(self, checkpoint, manifest, members, *, suffix=b"", manifest_body=None):
        body = cp.canonical(manifest) if manifest_body is None else manifest_body
        plain = cp._header("manifest.json", len(body)) + body + b"\0" * (-len(body) % 512)
        for name, data in members:
            plain += cp._header(name, len(data)) + data + b"\0" * (-len(data) % 512)
        plain += b"\0" * 1024 + suffix
        self.encrypt(checkpoint, plain)
        return plain

    def encrypt(self, checkpoint, plain):
        ciphertext = subprocess.run([self.age, "--encrypt", "-R", str(self.recipient)],
                                    input=plain, capture_output=True, check=True).stdout
        (checkpoint / cp.CIPHERTEXT).write_bytes(ciphertext)
        receipt = {"format_version": cp.FORMAT, "checkpoint_id": checkpoint.name,
                   "ciphertext_size_bytes": len(ciphertext),
                   "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(), "status": "CREATED"}
        (checkpoint / cp.RECEIPT).write_bytes(cp.canonical(receipt))

    def reject(self, checkpoint, **kwargs):
        destination = self.root / "restored"
        with self.assertRaises((cp.CheckpointError, OSError, ValueError, TypeError)):
            cp.restore(checkpoint=checkpoint, identity=self.identity, scratch_parent=self.scratch,
                       destination=destination, **kwargs)
        self.assertFalse(destination.exists(), "failed restore must not publish or retain normal-error output")

    def inventory(self, root):
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def test_real_crypto_round_trip_inventory_revision_null_bytes_and_gaps(self):
        before = self.inventory(self.source)
        index = (self.repo / ".git/index").read_bytes()
        checkpoint, result = self.capture()
        self.assertEqual(result["status"], "CREATED")
        self.assertEqual(result["cryptographic_archive_verification"], "not_run")
        verified = cp.verify(checkpoint=checkpoint, identity=self.identity,
                             scratch_parent=self.scratch)
        self.assertEqual(verified["status"], "VERIFIED")
        destination = self.root / "restore"
        report = cp.restore(checkpoint=checkpoint, identity=self.identity,
                            scratch_parent=self.scratch, destination=destination)
        self.assertEqual(report["status"], "RESTORE_VERIFIED")
        self.assertEqual(self.inventory(destination / "data"), before)
        self.assertTrue((destination / "data/empty-dir").is_dir())
        m = json.loads((destination / "manifest.json").read_bytes())
        self.assertEqual(m["source_commit"], self.commit)
        self.assertEqual(m["coverage"], self.gaps)
        self.assertEqual(m["file_count"], len(before) + 1)
        self.assertEqual(report["original_evidence_coverage"], "known_gaps")
        self.assertEqual(report["engine_trust_chain_validation"], "not_run")
        self.assertEqual(report["offsite_readback"], "not_run")
        self.assertEqual(report["independent_copy_verification"], "not_run")
        self.assertEqual(report["disaster_recovery"], "NO VERIFIED OFFSITE BACKUP")
        self.assertEqual(index, (self.repo / ".git/index").read_bytes())
        self.assertEqual(self.inventory(self.source), before)
        self.assertEqual(subprocess.run(["git", "bundle", "list-heads",
                                        str(destination / "repository/source.bundle")],
                                       check=True, capture_output=True).stdout,
                         f"{self.commit} HEAD\n".encode())
        for p in destination.rglob("*"):
            self.assertEqual(p.stat().st_mode & 0o777, 0o700 if p.is_dir() else 0o600)

    def test_bundle_excludes_untracked_config_other_branches_stashes_and_keys(self):
        self.git("checkout", "-qb", "other")
        (self.repo / "other-secret").write_bytes(b"unreachable branch")
        self.git("add", "other-secret")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "other")
        other = self.git("rev-parse", "HEAD").decode().strip()
        self.git("checkout", "--detach", self.commit)
        self.git("config", "test.secret", "SENSITIVE_CONFIGURATION")
        (self.repo / "untracked-secret").write_bytes(b"not committed")
        (self.repo / "app.txt").write_bytes(b"stashed bytes")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "stash", "push", "-m", "fixture")
        checkpoint, _, manifest, contents = self.archive()
        self.assertNotIn(self.identity.read_bytes(), b"".join(contents.values()))
        self.assertEqual(set(contents), {"payload/data/" + p for p in self.inventory(self.source)}
                         | {"payload/repository/source.bundle"})
        bare = self.root / "cloned.git"
        destination = self.root / "restore"
        cp.restore(checkpoint=checkpoint, identity=self.identity, scratch_parent=self.scratch,
                   destination=destination)
        subprocess.run(["git", "clone", "--bare", str(destination / "repository/source.bundle"), str(bare)],
                       capture_output=True, check=True)
        result = subprocess.run(["git", "-C", str(bare), "cat-file", "-e", other], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        tree = subprocess.run(["git", "-C", str(bare), "ls-tree", "-r", "--name-only", self.commit],
                              capture_output=True, check=True).stdout
        self.assertEqual(tree, b"app.txt\n")
        self.assertNotIn(b"SENSITIVE_CONFIGURATION", (bare / "config").read_bytes())

    def test_default_cli_output_never_discloses_paths_keys_or_coverage_payload(self):
        args = [sys.executable, str(SCRIPT), "create", "--source-root", str(self.source),
                "--repository", str(self.repo), "--commit", self.commit,
                "--recipients", str(self.recipient), "--output-parent", str(self.output),
                "--coverage", str(self.coverage), "--quiescent"]
        result = subprocess.run(args, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        combined = result.stdout + result.stderr
        for forbidden in [str(self.root).encode(), b"sensitive-source", b"private-player-name",
                          b"SENSITIVE", self.identity.read_bytes(), self.recipient.read_bytes().strip()]:
            self.assertNotIn(forbidden, combined)
        receipt = next(self.output.glob("*/COMPLETE.json"))
        self.assertEqual(set(json.loads(receipt.read_bytes())), {"format_version", "checkpoint_id",
                         "status", "ciphertext_size_bytes", "ciphertext_sha256"})
        failed = subprocess.run([sys.executable, str(SCRIPT), "verify", "--private-unknown",
                                 str(self.source)], capture_output=True)
        self.assertEqual(failed.returncode, 1)
        self.assertNotIn(str(self.source).encode(), failed.stderr)

    def test_explicit_private_scratch_is_required_and_used_for_every_pass(self):
        checkpoint, _ = self.capture()
        missing = subprocess.run(
            [sys.executable, str(SCRIPT), "verify", "--checkpoint", str(checkpoint),
             "--identity", str(self.identity)], capture_output=True)
        self.assertEqual(missing.returncode, 1)
        self.assertEqual(json.loads(missing.stderr)["code"], "INVALID_ARGUMENTS")

        observed = []
        real_temporary_directory = tempfile.TemporaryDirectory

        def temporary_directory(*args, **kwargs):
            observed.append(Path(kwargs["dir"]))
            return real_temporary_directory(*args, **kwargs)

        destination = self.root / "scratch-observed-restore"
        with patch.object(cp.tempfile, "TemporaryDirectory", side_effect=temporary_directory):
            cp.verify(checkpoint=checkpoint, identity=self.identity,
                      scratch_parent=self.scratch)
            cp.restore(checkpoint=checkpoint, identity=self.identity,
                       scratch_parent=self.scratch, destination=destination)
        self.assertEqual(observed, [self.scratch, self.scratch, self.scratch])
        self.assertEqual(list(self.scratch.iterdir()), [])

        public_scratch = self.root / "public-scratch"
        public_scratch.mkdir(mode=0o755)
        public_scratch.chmod(0o755)
        with self.assertRaisesRegex(cp.CheckpointError, "PRIVATE_DIRECTORY_REQUIRED"):
            cp.verify(checkpoint=checkpoint, identity=self.identity,
                      scratch_parent=public_scratch)
        with self.assertRaisesRegex(cp.CheckpointError, "OVERLAPPING_SCOPE"):
            cp.verify(checkpoint=checkpoint, identity=self.identity,
                      scratch_parent=self.output)

    def test_fixed_assurance_fields_cannot_be_overridden(self):
        for field in cp.RESERVED_ASSURANCE_FIELDS:
            with self.subTest(field=field), self.assertRaisesRegex(
                    cp.CheckpointError, "RESERVED_ASSURANCE_FIELD"):
                cp.assurance(**{field: "succeeded"})

    def test_missing_or_wrong_version_age_precedes_capture_and_publication(self):
        with patch.object(cp.shutil, "which", return_value=None), patch.object(cp, "scan") as scan:
            with self.assertRaisesRegex(cp.CheckpointError, "AGE_UNAVAILABLE"):
                cp.create(**self.kw)
            scan.assert_not_called()
        with patch.object(cp, "command", return_value=b"v0.0.0"):
            with self.assertRaisesRegex(cp.CheckpointError, "AGE_VERSION_UNSUPPORTED"):
                cp.create(**self.kw)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_dirty_tracked_source_wrong_revision_and_missing_quiescence_refuse(self):
        for change in ({"commit": "0" * 40}, {"quiescent": False}):
            with self.assertRaises(cp.CheckpointError):
                cp.create(**(self.kw | change))
        (self.repo / "app.txt").write_bytes(b"dirty")
        with self.assertRaisesRegex(cp.CheckpointError, "DIRTY_TRACKED_SOURCE"):
            cp.create(**self.kw)
        self.assertFalse(list(self.output.iterdir()))

    def test_wrong_key_and_ciphertext_tamper_truncation_fail_closed(self):
        checkpoint, _ = self.capture()
        wrong = self.root / "wrong-key"
        subprocess.run([self.keygen, "-o", str(wrong)], capture_output=True, check=True)
        with self.assertRaises(cp.CheckpointError):
            cp.verify(checkpoint=checkpoint, identity=wrong, scratch_parent=self.scratch)
        body = (checkpoint / cp.CIPHERTEXT).read_bytes()
        receipt = json.loads((checkpoint / cp.RECEIPT).read_bytes())
        for modified in (body[:-15], body[:100] + bytes([body[100] ^ 1]) + body[101:], body + b"trailing"):
            with self.subTest(length=len(modified)):
                (checkpoint / cp.CIPHERTEXT).write_bytes(modified)
                # Even a caller-recomputed outer hash cannot bypass age authentication.
                receipt.update(ciphertext_size_bytes=len(modified), ciphertext_sha256=hashlib.sha256(modified).hexdigest())
                (checkpoint / cp.RECEIPT).write_bytes(cp.canonical(receipt))
                self.reject(checkpoint)

    def test_receipt_hash_mismatch_or_missing_marker_cannot_verify(self):
        checkpoint, _ = self.capture()
        body = (checkpoint / cp.RECEIPT).read_bytes()
        receipt = json.loads(body)
        receipt["ciphertext_sha256"] = "0" * 64
        (checkpoint / cp.RECEIPT).write_bytes(cp.canonical(receipt))
        self.reject(checkpoint)
        (checkpoint / cp.RECEIPT).unlink()
        self.reject(checkpoint)

    def test_complete_plaintext_followed_by_real_late_age_failure_never_succeeds(self):
        checkpoint, _ = self.capture()
        wrapper = self.root / "late-age"
        wrapper.write_text(f"#!{sys.executable}\nimport subprocess,sys\n"
                           f"p=subprocess.run([{self.age!r}]+sys.argv[1:],close_fds=False)\n"
                           "sys.exit(9 if '--decrypt' in sys.argv else p.returncode)\n")
        wrapper.chmod(0o700)
        with self.assertRaisesRegex(cp.CheckpointError, "CHILD_FAILED"):
            cp.verify(checkpoint=checkpoint, identity=self.identity, scratch_parent=self.scratch,
                      age=str(wrapper))
        self.reject(checkpoint, age=str(wrapper))

    def test_broken_pipe_nonzero_child_and_bounded_private_stderr(self):
        wrapper = self.root / "broken-age"
        wrapper.write_text(f"#!{sys.executable}\nimport sys\n"
                           f"if '--version' in sys.argv: print({cp.AGE_VERSION!r}); sys.exit(0)\n"
                           "sys.stdin.close()\nsys.stderr.write('SENSITIVE' * 200000)\nsys.exit(7)\n")
        wrapper.chmod(0o700)
        with self.assertRaises((cp.CheckpointError, BrokenPipeError)):
            cp.create(**self.kw, age=str(wrapper))
        self.assertFalse(list(self.output.iterdir()))

    def test_failure_after_parsing_or_during_second_restore_pass_never_publishes(self):
        checkpoint, _ = self.capture()
        with patch.object(cp, "check_bundle", side_effect=cp.CheckpointError("BUNDLE_IDENTITY_INVALID")):
            self.reject(checkpoint)
        original = cp._read_archive
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 2:
                raise cp.CheckpointError("LATE_FAILURE")
            return result
        with patch.object(cp, "_read_archive", side_effect=fail_second):
            self.reject(checkpoint)
        self.assertEqual(calls, 2)

    def test_child_timeout_and_excess_output_fail_closed(self):
        with self.assertRaisesRegex(cp.CheckpointError, "CHILD_TIMEOUT"):
            cp.command([sys.executable, "-c", "import time; time.sleep(10)"],
                       replace(cp.DEFAULT_LIMITS, child_seconds=1))
        with self.assertRaisesRegex(cp.CheckpointError, "CHILD_OUTPUT_LIMIT"):
            cp.command([sys.executable, "-c", "import sys; sys.stdout.write('x'*100000)"])
        with patch.object(cp.os, "killpg", side_effect=PermissionError("sandbox")):
            with self.assertRaisesRegex(cp.CheckpointError, "ORIGINAL_FAILURE"):
                with cp.child([sys.executable, "-c", "import time; time.sleep(10)"]) as proc:
                    raise cp.CheckpointError("ORIGINAL_FAILURE")
        self.assertIsNotNone(proc.returncode)
        self.assertTrue(proc.stdout.closed)
        self.assertTrue(proc.stderr.closed)

    def test_destination_symlink_injection_never_writes_outside_private_tree(self):
        checkpoint, _ = self.capture()
        outside = self.root / "untouched"
        outside.mkdir(mode=0o700)
        original = cp._destination_file
        def inject(fd, name, directory=False):
            if name == "data" and directory:
                os.symlink(str(outside), "data", dir_fd=fd)
                return None
            return original(fd, name, directory)
        with patch.object(cp, "_destination_file", side_effect=inject):
            self.reject(checkpoint)
        self.assertFalse(list(outside.iterdir()))

    def test_post_capture_source_root_replacement_and_unsupported_publish_link_fail(self):
        original = cp.scan
        def replace_root(fd, limits, *, hashing):
            result = original(fd, limits, hashing=hashing)
            if not hashing:
                self.source.rename(self.root / "moved")
                self.source.mkdir(mode=0o700)
            return result
        with patch.object(cp, "scan", side_effect=replace_root):
            with self.assertRaisesRegex(cp.CheckpointError, "INPUT_CHANGED"):
                self.capture()
        self.assertFalse(list(self.output.iterdir()))
        with patch.object(cp.os, "link", side_effect=OSError("synthetic unsupported filesystem")):
            with self.assertRaises(OSError): self.capture()
        self.assertFalse(list(self.output.iterdir()))

    def test_existing_checkpoint_restore_and_completion_marker_never_overwritten(self):
        fixed = type("Fixed", (), {"hex": "1" * 32})()
        # Fix only the operation ID, leaving marker names unique.
        original = cp.uuid.uuid4
        with patch.object(cp.uuid, "uuid4", side_effect=[fixed, original()]):
            checkpoint, _ = self.capture()
        before = self.inventory(checkpoint)
        with patch.object(cp.uuid, "uuid4", return_value=fixed):
            with self.assertRaises(FileExistsError):
                self.capture()
        self.assertEqual(self.inventory(checkpoint), before)
        dest = self.root / "restored"
        dest.mkdir(mode=0o700)
        (dest / "sentinel").write_bytes(b"untouched")
        with self.assertRaises(FileExistsError):
                cp.restore(checkpoint=checkpoint, identity=self.identity,
                           scratch_parent=self.scratch, destination=dest)
        self.assertEqual(self.inventory(dest), {"sentinel": b"untouched"})
        _, fd = cp._directory(checkpoint)
        try:
            with self.assertRaises(FileExistsError):
                cp._marker(fd, cp.RECEIPT, {"do": "not overwrite"})
        finally:
            os.close(fd)
        self.assertEqual(self.inventory(checkpoint), before)

    def test_concurrent_operation_reservation_has_exactly_one_owner(self):
        import threading
        barrier = threading.Barrier(2)
        outcomes = []
        def attempt():
            barrier.wait()
            try:
                with cp.operation(self.output, "same-operation") as (_, fd):
                    cp._marker(fd, cp.RECEIPT, {"owner": True})
                outcomes.append("created")
            except FileExistsError:
                outcomes.append("refused")
        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertCountEqual(outcomes, ["created", "refused"])
        self.assertTrue((self.output / "same-operation" / cp.RECEIPT).exists())

    def test_crash_left_directory_is_not_complete_and_is_not_reused(self):
        code = ("from scripts import encrypted_checkpoint as c; import os; "
                f"ctx=c.operation({str(self.output)!r},'checkpoint_'+'2'*32); "
                "ctx.__enter__(); os._exit(19)")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, cwd=SCRIPT.parent.parent)
        self.assertEqual(result.returncode, 19)
        path = self.output / ("checkpoint_" + "2" * 32)
        self.assertTrue((path / ".INCOMPLETE").exists())
        self.assertFalse((path / cp.RECEIPT).exists())
        self.reject(path)
        with self.assertRaises(FileExistsError):
            with cp.operation(self.output, path.name): pass

    def test_overlap_and_symlink_parents_fail_before_source_changes(self):
        inside = self.source / "output"
        inside.mkdir(mode=0o700)
        before = self.inventory(self.source)
        for change in ({"output_parent": inside}, {"output_parent": self.repo},
                       {"source_root": self.root}):
            with self.assertRaises(cp.CheckpointError):
                cp.create(**(self.kw | change))
        linked = self.root / "linked-parent"
        linked.symlink_to(self.output, target_is_directory=True)
        with self.assertRaises(OSError):
            cp.create(**(self.kw | {"output_parent": linked}))
        self.assertEqual(self.inventory(self.source), before)
        checkpoint, _ = self.capture()
        before = self.inventory(self.source)
        with self.assertRaises(cp.CheckpointError):
            cp.restore(checkpoint=checkpoint, identity=self.identity,
                       scratch_parent=self.scratch, destination=self.source / "new")
        self.assertEqual(self.inventory(self.source), before)
        with self.assertRaises(OSError):
            cp.restore(checkpoint=checkpoint, identity=self.identity,
                       scratch_parent=self.scratch, destination=linked / "new")

    def test_keys_inside_scope_hardlinks_and_unsupported_key_forms_are_rejected(self):
        inside = self.source / "key"
        inside.write_bytes(self.recipient.read_bytes())
        with self.assertRaisesRegex(cp.CheckpointError, "KEY_SCOPE_OVERLAP"):
            cp.create(**(self.kw | {"recipients": inside}))
        inside.unlink()
        os.link(self.recipient, inside)
        with self.assertRaises(cp.CheckpointError):
            self.capture()
        inside.unlink()
        for material in (b"ssh-ed25519 AAAA\n", b"age-plugin-x\n", b"passphrase\n"):
            self.recipient.write_bytes(material)
            with self.assertRaisesRegex(cp.CheckpointError, "NATIVE_KEY_REQUIRED"):
                self.capture()

    def test_source_symlink_special_case_unicode_and_file_directory_alias_rejected(self):
        link = self.source / "link"
        link.symlink_to(self.source / "private-player-name.json")
        with self.assertRaises(cp.CheckpointError): self.capture()
        link.unlink()
        os.mkfifo(link)
        with self.assertRaises(cp.CheckpointError): self.capture()
        link.unlink()
        with self.assertRaises(cp.CheckpointError): cp.path_set(["data/A", "data/a"], ["data"], cp.DEFAULT_LIMITS)
        with self.assertRaises(cp.CheckpointError): cp.path_set(["data/é", "data/e\u0301"], ["data"], cp.DEFAULT_LIMITS)
        with self.assertRaises(cp.CheckpointError): cp.path_set(["data/f"], ["data", "data/f"], cp.DEFAULT_LIMITS)

    def test_source_mutation_replacement_addition_deletion_during_stream_fails(self):
        original = cp._stream_source
        for operation in ("modify", "replace", "add", "delete"):
            with self.subTest(operation=operation):
                victim = self.source / "private-player-name.json"
                saved = victim.read_bytes()
                fired = False
                def mutate(*args, **kwargs):
                    nonlocal fired
                    original(*args, **kwargs)
                    if not fired:
                        fired = True
                        if operation == "modify": victim.write_bytes(b"same or different bytes")
                        elif operation == "replace":
                            victim.unlink(); victim.write_bytes(saved)
                        elif operation == "add": (self.source / "new").write_bytes(b"added")
                        else: victim.unlink()
                with patch.object(cp, "_stream_source", side_effect=mutate):
                    with self.assertRaises((cp.CheckpointError, OSError)): self.capture()
                victim.write_bytes(saved)
                (self.source / "new").unlink(missing_ok=True)
                self.assertFalse(list(self.output.iterdir()))

    def test_hash_is_checked_against_exact_stream_not_earlier_inventory_only(self):
        original = cp._member
        def wrong_digest(out, name, size, reader):
            digest = original(out, name, size, reader)
            return "0" * 64 if name.startswith("payload/data/") else digest
        with patch.object(cp, "_member", side_effect=wrong_digest):
            with self.assertRaisesRegex(cp.CheckpointError, "STREAM_HASH_MISMATCH"):
                self.capture()
        self.assertFalse(list(self.output.iterdir()))

    def test_unknown_manifest_fields_versions_duplicates_and_coverage_cannot_claim_complete(self):
        checkpoint, _, manifest, contents = self.archive()
        variants = [manifest | {"format_version": "future"}, manifest | {"unexpected": 1},
                    manifest | {"coverage": {"status": "complete", "gaps": []}},
                    manifest | {"file_count": True}, manifest | {"source_commit": "0" * 40}]
        for index, changed in enumerate(variants):
            with self.subTest(variant=index):
                self.forge(checkpoint, changed, list(contents.items()))
                self.reject(checkpoint)
        body = cp.canonical(manifest)
        duplicate = b'{"format_version":"duplicate",' + body[1:]
        self.forge(checkpoint, manifest, list(contents.items()), manifest_body=duplicate)
        self.reject(checkpoint)

    def test_missing_extra_duplicate_members_wrong_sizes_hashes_and_bundle_identity(self):
        checkpoint, _, manifest, contents = self.archive()
        members = list(contents.items())
        for changed in (members[:-1], members + [("payload/data/extra", b"x")], members + [members[0]],
                        [(members[0][0], b"wrong-size"), *members[1:]],
                        [(members[0][0], b"x" * len(members[0][1])), *members[1:]]):
            self.forge(checkpoint, manifest, changed)
            self.reject(checkpoint)
        bundle_name = "payload/repository/source.bundle"
        data = contents[bundle_name].replace(self.commit.encode(), b"0" * 40, 1)
        modified = json.loads(cp.canonical(manifest))
        for row in modified["files"]:
            if row["path"] == "repository/source.bundle": row["sha256"] = hashlib.sha256(data).hexdigest()
        modified["bundle"]["sha256"] = hashlib.sha256(data).hexdigest()
        contents[bundle_name] = data
        self.forge(checkpoint, modified, list(contents.items()))
        self.reject(checkpoint)

    def test_unsafe_tar_types_paths_and_extensions_never_publish(self):
        checkpoint, plain, manifest, contents = self.archive()
        base = cp.canonical(manifest)
        first = cp._header("manifest.json", len(base)) + base + b"\0" * (-len(base) % 512)
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE,
                     tarfile.FIFOTYPE, tarfile.DIRTYPE, tarfile.GNUTYPE_SPARSE,
                     tarfile.GNUTYPE_LONGNAME, tarfile.XGLTYPE):
            info = tarfile.TarInfo("payload/data/evil")
            info.type, info.linkname = kind, "target" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
            self.encrypt(checkpoint, first + info.tobuf(format=tarfile.USTAR_FORMAT) + b"\0" * 1024)
            self.reject(checkpoint)
        for name in ("/absolute", "../parent", "payload/../escape", "payload\\escape", "payload/data/./x"):
            self.encrypt(checkpoint, first + cp._header(name, 0) + b"\0" * 1024)
            self.reject(checkpoint)
        info = tarfile.TarInfo("payload/data/evil")
        info.pax_headers = {"GNU.sparse.size": "1"}
        self.encrypt(checkpoint, first + info.tobuf(format=tarfile.PAX_FORMAT) + b"\0" * 1024)
        self.reject(checkpoint)

    def test_manifest_path_collisions_and_unlisted_parent_are_rejected(self):
        _, _, manifest, _ = self.archive()
        for names in (("data/A", "data/a"), ("data/é", "data/e\u0301"),
                      ("data/same", "data/same"), ("data/absent/file", "data/other")):
            changed = json.loads(cp.canonical(manifest))
            for row, name in zip(changed["files"][:2], names): row["path"] = name
            changed["files"].sort(key=lambda r: r["path"])
            with self.assertRaises(cp.CheckpointError): cp.validate_manifest(changed)

    def test_all_resource_limits_fail_closed(self):
        checkpoint, _, manifest, _ = self.archive()
        reductions = {"archive_bytes": 100, "total_bytes": 1, "file_bytes": 1,
                      "bundle_bytes": 1, "files": 1, "directories": 1,
                      "manifest_bytes": 1, "path_bytes": 10, "depth": 1, "pax_bytes": 1}
        for name, value in reductions.items():
            with self.subTest(limit=name):
                self.reject(checkpoint, limits=replace(cp.DEFAULT_LIMITS, **{name: value}))

    def test_noncanonical_headers_padding_manifest_order_and_trailing_bytes_refused(self):
        checkpoint, plain, manifest, contents = self.archive()
        for suffix in (b"x", b"\0" * 512):
            self.forge(checkpoint, manifest, list(contents.items()), suffix=suffix)
            self.reject(checkpoint)
        self.encrypt(checkpoint, cp._header("not-manifest", 0) + b"\0" * 1024)
        self.reject(checkpoint)
        info = tarfile.TarInfo("manifest.json")
        info.mode, info.size = 0o777, len(cp.canonical(manifest))
        self.encrypt(checkpoint, info.tobuf(format=tarfile.PAX_FORMAT) + plain[512:])
        self.reject(checkpoint)
        body = cp.canonical(manifest)
        if len(body) % 512:
            malformed = cp._header("manifest.json", len(body)) + body + b"x" + b"\0" * (-len(body) % 512 - 1)
            self.encrypt(checkpoint, malformed + b"\0" * 1024)
            self.reject(checkpoint)


if __name__ == "__main__":
    unittest.main()
