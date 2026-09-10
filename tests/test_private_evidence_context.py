import ast
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/private_evidence_context.py"
SPEC = importlib.util.spec_from_file_location("private_evidence_context", SCRIPT)
assert SPEC and SPEC.loader
pec = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pec)

PRIVACY_SPEC = importlib.util.spec_from_file_location(
    "check_staged_privacy", ROOT / "scripts/check_staged_privacy.py"
)
assert PRIVACY_SPEC and PRIVACY_SPEC.loader
privacy = importlib.util.module_from_spec(PRIVACY_SPEC)
PRIVACY_SPEC.loader.exec_module(privacy)


class PrivateEvidenceContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "private-player-screen.png"
        self.source.write_bytes(b"synthetic screenshot bytes\x00\xff")
        self.source.chmod(0o600)
        self.observation_input = self.root / "observation.json"
        self.evidence_root = self.root / "evidence-store"
        self.context_root = self.root / "context-store"
        self.evidence_root_patch = patch.object(
            pec, "PRIVATE_EVIDENCE_ROOT", self.evidence_root
        )
        self.context_root_patch = patch.object(
            pec, "PRIVATE_CONTEXT_ROOT", self.context_root
        )
        self.evidence_root_patch.start()
        self.context_root_patch.start()
        self.now = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)
        self.deadline = "2026-09-12T12:30:00.000000Z"
        self.write_json(self.observation_input, self.observation())

    def tearDown(self) -> None:
        self.context_root_patch.stop()
        self.evidence_root_patch.stop()
        self.temporary.cleanup()

    @staticmethod
    def write_json(path: Path, payload: object, mode: int = 0o600) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        path.chmod(mode)

    def observation(self, **overrides):
        payload = {
            "source_class": "OWNER_SUPPLIED_MANAGER_SCREEN",
            "media_type": "image/png",
            "sensitivity": "MANAGER_PRIVATE",
            "provenance_description": "Synthetic owner-supplied transfer screen.",
            "owner_reported_observed_at": None,
            "season": "2026-27",
            "target_gameweek": 4,
            "official_deadline": self.deadline,
        }
        payload.update(overrides)
        return payload

    def capture(self, **overrides):
        arguments = {
            "source": self.source,
            "evidence_root": self.evidence_root,
            "observation_input": self.observation_input,
            "clock": lambda: self.now,
        }
        arguments.update(overrides)
        return pec.capture_source(**arguments)

    def evidence_path(self, result):
        digest = result["content_sha256"]
        return (
            self.evidence_root
            / "source-v1"
            / "sha256"
            / digest[:2]
            / digest
            / "observations"
            / result["evidence_record_id"]
            / "source_evidence.json"
        )

    def context_input(self, **overrides):
        payload = {
            "temporal_classification": "PROSPECTIVE",
            "season": "2026-27",
            "target_gameweek": 4,
            "official_deadline": self.deadline,
            "context_kind": "OPEN_HYPOTHESIS",
            "reasoning": "Synthetic multi-week structure hypothesis.",
            "assumptions": ["The supplied facts remain current."],
            "change_conditions": ["Fresh evidence contradicts the assumption."],
            "provisional_posture": "WATCH",
            "evidence_record_paths": [],
            "engine_references": [],
            "relations": [],
        }
        payload.update(overrides)
        return payload

    def create_context(self, payload=None, *, clock=None):
        path = self.root / ("context-input-" + uuid.uuid4().hex + ".json")
        self.write_json(path, payload or self.context_input())
        return pec.create_context(
            context_root=self.context_root,
            context_input=path,
            clock=clock or (lambda: self.now),
        )

    def context_path(self, result, season="2026-27"):
        return (
            self.context_root
            / "fpl"
            / season
            / "records"
            / result["context_id"]
            / "context.json"
        )

    def schema(self, name):
        return json.loads((ROOT / "contracts/private/v1" / name).read_text())

    def assert_schema(self, name, instance):
        schema = self.schema(name)
        Draft202012Validator.check_schema(schema)
        errors = list(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(instance)
        )
        self.assertEqual(errors, [], "\n".join(error.message for error in errors))

    def test_source_capture_verifies_bytes_schemas_permissions_and_source_immutability(self):
        before = self.source.read_bytes()
        result = self.capture()
        record_path = self.evidence_path(result)
        payload = pec.verify_source_record(record_path)
        content_dir = record_path.parents[2]
        content = json.loads((content_dir / "content_manifest.json").read_bytes())
        self.assertEqual((content_dir / "source.bin").read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(payload["observed_at"], None)
        self.assertEqual(payload["observation_basis"], "NOT_SYSTEM_PROVEN")
        self.assertEqual(payload["temporal_status"], "UNKNOWN")
        self.assert_schema("private-source-content-v1.schema.json", content)
        self.assert_schema("private-source-evidence-v1.schema.json", payload)
        for path in self.evidence_root.rglob("*"):
            self.assertEqual(
                stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600
            )

    def test_identical_capture_reuses_content_and_exact_observation(self):
        first = self.capture()
        second = self.capture()
        self.assertEqual(first["evidence_record_id"], second["evidence_record_id"])
        self.assertTrue(second["reused_content"])
        self.assertTrue(second["reused_record"])

        later = self.capture(clock=lambda: self.now + timedelta(seconds=1))
        self.assertEqual(first["content_sha256"], later["content_sha256"])
        self.assertNotEqual(first["evidence_record_id"], later["evidence_record_id"])
        self.assertTrue(later["reused_content"])
        self.assertFalse(later["reused_record"])

    def test_source_mutation_and_replacement_while_reading_fail_closed(self):
        original_read = pec.os.read
        changed = False

        def mutate(fd, size):
            nonlocal changed
            chunk = original_read(fd, size)
            if (
                chunk
                and not changed
                and os.fstat(fd).st_ino == self.source.stat().st_ino
            ):
                changed = True
                with self.source.open("ab") as output:
                    output.write(b"changed")
            return chunk

        with patch.object(pec.os, "read", side_effect=mutate):
            with self.assertRaisesRegex(pec.EvidenceContextError, "INPUT_CHANGED"):
                self.capture()
        self.assertFalse(
            self.evidence_root.exists() and any(self.evidence_root.rglob("source.bin"))
        )

        self.source.write_bytes(b"replacement test")
        self.source.chmod(0o600)
        original_read = pec.os.read
        replaced = False

        def replace(fd, size):
            nonlocal replaced
            chunk = original_read(fd, size)
            if (
                chunk
                and not replaced
                and os.fstat(fd).st_ino == self.source.stat().st_ino
            ):
                replaced = True
                self.source.rename(self.root / "old-source")
                self.source.write_bytes(b"replacement test")
                self.source.chmod(0o600)
            return chunk

        with patch.object(pec.os, "read", side_effect=replace):
            with self.assertRaisesRegex(pec.EvidenceContextError, "INPUT_CHANGED"):
                self.capture()

    def test_staged_byte_corruption_fails_before_publication(self):
        original_copy = pec._copy_source_to_stage

        def corrupt_stage(source, stage_fd):
            digest, byte_length = original_copy(source, stage_fd)
            fd = os.open("source.bin", os.O_WRONLY | os.O_APPEND, dir_fd=stage_fd)
            try:
                os.write(fd, b"corrupt staged byte")
                os.fsync(fd)
            finally:
                os.close(fd)
            return digest, byte_length

        with patch.object(pec, "_copy_source_to_stage", side_effect=corrupt_stage):
            with self.assertRaisesRegex(pec.EvidenceContextError, "SOURCE_CAPTURE_FAILED"):
                self.capture()
        self.assertFalse(any(self.evidence_root.rglob("source.bin")))

    def test_symlink_hardlink_fifo_and_oversize_sources_are_rejected(self):
        cases = []
        symlink = self.root / "source-link"
        symlink.symlink_to(self.source)
        cases.append(symlink)
        hardlink = self.root / "source-hardlink"
        os.link(self.source, hardlink)
        cases.extend([self.source, hardlink])
        fifo = self.root / "source-fifo"
        os.mkfifo(fifo, 0o600)
        cases.append(fifo)
        oversize = self.root / "source-oversize"
        with oversize.open("wb") as output:
            output.truncate(pec.MAX_SOURCE_BYTES + 1)
        oversize.chmod(0o600)
        cases.append(oversize)
        for source in cases:
            with self.subTest(source=source.name), self.assertRaises(pec.EvidenceContextError):
                self.capture(source=source)

    def test_partial_content_and_conflicting_observation_fail_closed(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        partial = self.evidence_root / "source-v1" / "sha256" / digest[:2] / digest
        partial.mkdir(parents=True, mode=0o700)
        for parent in [self.evidence_root, *partial.parents[:3]]:
            if parent.is_relative_to(self.evidence_root):
                parent.chmod(0o700)
        with self.assertRaisesRegex(pec.EvidenceContextError, "CONTENT_STORE_INVALID"):
            self.capture()

        self.tearDown()
        self.setUp()
        result = self.capture()
        record = self.evidence_path(result)
        record.write_bytes(record.read_bytes() + b" ")
        with self.assertRaisesRegex(pec.EvidenceContextError, "IMMUTABLE_CONFLICT"):
            self.capture()

    def test_source_record_rejects_noncanonical_manifest_and_corrupt_content(self):
        result = self.capture()
        record = self.evidence_path(result)
        payload = json.loads(record.read_bytes())
        record.write_text(json.dumps(payload, indent=2))
        record.chmod(0o600)
        with self.assertRaisesRegex(pec.EvidenceContextError, "SOURCE_EVIDENCE_INVALID"):
            pec.verify_source_record(record)

        self.tearDown()
        self.setUp()
        result = self.capture()
        record = self.evidence_path(result)
        content = record.parents[2] / "source.bin"
        content.write_bytes(b"corrupt")
        content.chmod(0o600)
        with self.assertRaisesRegex(pec.EvidenceContextError, "CONTENT_STORE_INVALID"):
            pec.verify_source_record(record)

    def test_stored_hardlink_symlink_extra_file_and_public_directory_are_rejected(self):
        result = self.capture()
        record = self.evidence_path(result)
        content_dir = record.parents[2]
        source = content_dir / "source.bin"
        outside_link = self.root / "stored-hardlink"
        os.link(source, outside_link)
        with self.assertRaisesRegex(pec.EvidenceContextError, "CONTENT_STORE_INVALID"):
            pec.verify_source_record(record)
        outside_link.unlink()

        source.rename(content_dir / "original-source")
        source.symlink_to(content_dir / "original-source")
        with self.assertRaises(pec.EvidenceContextError):
            pec.verify_source_record(record)

        source.unlink()
        (content_dir / "original-source").rename(source)
        source.chmod(0o600)
        (content_dir / "unexpected").write_bytes(b"unexpected")
        (content_dir / "unexpected").chmod(0o600)
        with self.assertRaisesRegex(pec.EvidenceContextError, "CONTENT_STORE_INVALID"):
            pec.verify_source_record(record)
        (content_dir / "unexpected").unlink()

        content_dir.chmod(0o755)
        with self.assertRaisesRegex(pec.EvidenceContextError, "PRIVATE_DIRECTORY_REQUIRED"):
            pec.verify_source_record(record)

    def test_destination_symlink_injection_never_writes_outside(self):
        self.evidence_root.mkdir(mode=0o700)
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        (self.evidence_root / "source-v1").symlink_to(outside)
        with self.assertRaises(pec.EvidenceContextError):
            self.capture()
        self.assertEqual(list(outside.iterdir()), [])

    def test_owner_reported_time_never_becomes_observed_or_predeadline_proof(self):
        self.write_json(
            self.observation_input,
            self.observation(owner_reported_observed_at="2026-09-13T07:00:00.000000Z"),
        )
        result = self.capture()
        payload = pec.verify_source_record(self.evidence_path(result))
        self.assertEqual(payload["owner_reported_observed_at"], "2026-09-13T07:00:00.000000Z")
        self.assertIsNone(payload["observed_at"])
        self.assertEqual(payload["temporal_status"], "UNKNOWN")

    def test_manager_binding_checks_only_exact_source_hash(self):
        result = self.capture()
        record = self.evidence_path(result)
        manager = self.root / "manager.json"
        self.write_json(manager, {"evidence_source_sha256": result["content_sha256"]})
        self.assertEqual(
            pec.verify_manager_binding(
                record_path=record, manager_evidence_path=manager
            )["status"],
            "MATCH",
        )
        self.write_json(manager, {"evidence_source_sha256": "0" * 64})
        with self.assertRaisesRegex(pec.EvidenceContextError, "MANAGER_SOURCE_HASH_MISMATCH"):
            pec.verify_manager_binding(record_path=record, manager_evidence_path=manager)

    def test_context_round_trip_schema_reuse_and_explicit_record_type(self):
        result = self.create_context()
        path = self.context_path(result)
        payload = pec.verify_context_record(path)
        self.assertEqual(payload["record_type"], "HUMAN_STRATEGY_CONTEXT")
        self.assertEqual(payload["authority"], pec.CONTEXT_AUTHORITY)
        self.assert_schema("human-strategy-context-v1.schema.json", payload)
        repeated = self.create_context()
        self.assertEqual(result["context_id"], repeated["context_id"])
        self.assertTrue(repeated["reused"])

    def test_context_rejects_noncanonical_bytes_extra_files_and_public_directory(self):
        result = self.create_context()
        path = self.context_path(result)
        payload = json.loads(path.read_bytes())
        path.write_text(json.dumps(payload, indent=2))
        path.chmod(0o600)
        with self.assertRaisesRegex(pec.EvidenceContextError, "CONTEXT_INVALID"):
            pec.verify_context_record(path)

        path.write_bytes(pec.canonical(payload))
        path.chmod(0o600)
        extra = path.parent / "unexpected"
        extra.write_bytes(b"unexpected")
        extra.chmod(0o600)
        with self.assertRaisesRegex(pec.EvidenceContextError, "CONTEXT_INVALID"):
            pec.verify_context_record(path)
        extra.unlink()

        path.parent.chmod(0o755)
        with self.assertRaisesRegex(pec.EvidenceContextError, "PRIVATE_DIRECTORY_REQUIRED"):
            pec.verify_context_record(path)

    def test_prospective_context_requires_complete_scope_and_strict_predeadline_clock(self):
        for payload in (
            self.context_input(season=None, target_gameweek=None, official_deadline=None),
            self.context_input(official_deadline=None),
        ):
            with self.subTest(payload=payload), self.assertRaises(pec.EvidenceContextError):
                self.create_context(payload)
        deadline = datetime(2026, 9, 12, 12, 30, tzinfo=timezone.utc)
        for clock in (deadline, deadline + timedelta(microseconds=1)):
            with self.subTest(clock=clock), self.assertRaisesRegex(
                pec.EvidenceContextError, "PROSPECTIVE_DEADLINE_REJECTED"
            ):
                self.create_context(clock=lambda clock=clock: clock)

    def test_historical_backfill_has_no_original_action_time_field(self):
        payload = self.context_input(
            temporal_classification="HISTORICAL_BACKFILL",
            season=None,
            target_gameweek=None,
            official_deadline=None,
        )
        result = self.create_context(payload)
        record = pec.verify_context_record(self.context_path(result, season="global"))
        self.assertNotIn("human_action_recorded_at", record)
        payload["human_action_recorded_at"] = "2026-09-01T00:00:00.000000Z"
        with self.assertRaisesRegex(pec.EvidenceContextError, "CONTEXT_INPUT_INVALID"):
            self.create_context(payload)

    def test_later_context_relation_preserves_original_bytes(self):
        original = self.create_context()
        original_path = self.context_path(original)
        before = original_path.read_bytes()
        later_input = self.context_input(
            context_kind="PLAYER_THESIS_SNAPSHOT",
            reasoning="New evidence challenges the earlier hypothesis.",
            relations=[{"relation": "CHALLENGES", "context_path": str(original_path)}],
        )
        later = self.create_context(later_input, clock=lambda: self.now + timedelta(seconds=1))
        payload = pec.verify_context_record(self.context_path(later))
        self.assertEqual(payload["relations"][0]["context_id"], original["context_id"])
        self.assertEqual(original_path.read_bytes(), before)

    def test_evidence_and_engine_references_are_verified_and_paths_are_not_stored(self):
        evidence = self.capture()
        evidence_path = self.evidence_path(evidence)
        artifact = self.root / "engine-artifact.json"
        artifact.write_bytes(b'{"synthetic":"engine artifact"}')
        artifact.chmod(0o600)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        payload = self.context_input(
            evidence_record_paths=[str(evidence_path)],
            engine_references=[{
                "artifact_path": str(artifact),
                "artifact_type": "GameweekDecision",
                "artifact_schema_version": "1.0.0",
                "semantic_id": "decision_" + "a" * 64,
                "expected_sha256": digest,
            }],
        )
        result = self.create_context(payload)
        body = self.context_path(result).read_bytes()
        self.assertNotIn(str(self.root).encode(), body)
        record = pec.verify_context_record(self.context_path(result))
        self.assertEqual(
            record["evidence_references"][0]["content_sha256"],
            evidence["content_sha256"],
        )
        self.assertEqual(
            record["evidence_references"][0]["evidence_record_sha256"],
            hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(record["engine_references"][0]["sha256"], digest)

        duplicate = self.context_input(
            evidence_record_paths=[str(evidence_path), str(evidence_path)]
        )
        with self.assertRaisesRegex(
            pec.EvidenceContextError, "DUPLICATE_EVIDENCE_REFERENCE"
        ):
            self.create_context(duplicate)

        payload["engine_references"][0]["expected_sha256"] = "0" * 64
        with self.assertRaisesRegex(pec.EvidenceContextError, "ENGINE_ARTIFACT_HASH_MISMATCH"):
            self.create_context(payload)

    def test_comparison_view_has_three_named_layers_and_no_decision_construction(self):
        evidence = self.capture()
        evidence_path = self.evidence_path(evidence)
        context = self.create_context(
            self.context_input(evidence_record_paths=[str(evidence_path)])
        )
        context_path = self.context_path(context)
        artifact = self.root / "artifact"
        artifact.write_bytes(b"trusted synthetic bytes")
        artifact.chmod(0o600)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        engine = {
            "artifact_path": str(artifact),
            "artifact_type": "GameweekDecision",
            "artifact_schema_version": "1.0.0",
            "semantic_id": "decision_" + "b" * 64,
            "expected_sha256": digest,
        }
        view = pec.build_comparison_view(
            historical_context_paths=[context_path],
            trusted_engine_references=[engine],
            current_evidence_paths=[evidence_path],
        )
        self.assertEqual(
            set(view),
            {
                "view_version",
                "historical_human_thesis_research",
                "trusted_engine_artifacts",
                "current_refreshed_football_manager_evidence",
                "authority",
            },
        )
        self.assertEqual(
            view["current_refreshed_football_manager_evidence"]["freshness_status"],
            "NOT_ESTABLISHED_BY_TASK029B",
        )
        self.assertIn(
            "USE_TRUSTED_ENGINE_READER",
            view["trusted_engine_artifacts"]["validation_status"],
        )
        self.assertNotIn("human_action", json.dumps(view))
        self.assertNotIn("starting_xi", json.dumps(view))
        self.assertEqual(
            view["current_refreshed_football_manager_evidence"]["freshness_status"],
            "NOT_ESTABLISHED_BY_TASK029B",
        )
        self.assertEqual(
            view["trusted_engine_artifacts"]["validation_status"],
            "HASH_REFERENCES_ONLY_USE_TRUSTED_ENGINE_READER_FOR_SEMANTICS",
        )

    def test_trusted_engine_package_does_not_import_task029b_tool(self):
        for path in (ROOT / "src/fpl_decision_engine").rglob("*.py"):
            tree = ast.parse(path.read_text())
            names = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    names.append(node.module or "")
                    names.extend(
                        f"{node.module}.{alias.name}" if node.module else alias.name
                        for alias in node.names
                    )
            self.assertFalse(
                any("private_evidence_context" in name for name in names), path
            )

    def test_private_roots_and_git_path_guard(self):
        self.evidence_root.mkdir(mode=0o755)
        self.evidence_root.chmod(0o755)
        with self.assertRaisesRegex(pec.EvidenceContextError, "PRIVATE_ROOT_REQUIRED"):
            self.capture()
        outside = self.root / "outside-data"
        with self.assertRaisesRegex(pec.EvidenceContextError, "EVIDENCE_ROOT_OUTSIDE_DATA"):
            self.capture(evidence_root=outside)

        self.evidence_root.chmod(0o700)
        result = self.capture()
        with patch.object(pec, "PRIVATE_EVIDENCE_ROOT", outside), self.assertRaisesRegex(
            pec.EvidenceContextError, "SOURCE_RECORD_PATH_INVALID"
        ):
            pec.verify_source_record(self.evidence_path(result))
        self.assertTrue(privacy.forbidden(b"data/evidence/source-v1/source.bin"))
        self.assertTrue(privacy.forbidden(b"data/context/fpl/2026-27/context.json"))

    def test_secret_markers_and_unknown_input_fields_fail_closed(self):
        self.observation_input.write_bytes(b'{"note":"AGE-SECRET-KEY-1SYNTHETIC"}')
        with self.assertRaisesRegex(pec.EvidenceContextError, "SECRET_MARKER_REJECTED"):
            self.capture()
        payload = self.context_input(unexpected="value")
        with self.assertRaisesRegex(pec.EvidenceContextError, "CONTEXT_INPUT_INVALID"):
            self.create_context(payload)

        self.write_json(self.observation_input, self.observation(), mode=0o644)
        with self.assertRaisesRegex(pec.EvidenceContextError, "OBSERVATION_INPUT_INVALID"):
            self.capture()

    def test_cli_output_is_bounded_and_does_not_disclose_paths_or_narrative(self):
        cli_repo = self.root / "cli-repository"
        cli_script = cli_repo / "scripts" / SCRIPT.name
        cli_script.parent.mkdir(parents=True)
        shutil.copyfile(SCRIPT, cli_script)
        (cli_repo / "data").mkdir()
        cli_evidence_root = cli_repo / "data" / "evidence"
        result = subprocess.run(
            [
                sys.executable,
                str(cli_script),
                "capture-source",
                "--source",
                str(self.source),
                "--evidence-root",
                str(cli_evidence_root),
                "--observation-input",
                str(self.observation_input),
            ],
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        combined = result.stdout + result.stderr
        self.assertNotIn(str(self.root).encode(), combined)
        self.assertNotIn(b"Synthetic owner-supplied", combined)
        self.assertLess(len(combined), 2048)
        failed = subprocess.run(
            [sys.executable, str(cli_script), "verify-source", "--record", str(self.source)],
            capture_output=True,
            check=False,
        )
        self.assertEqual(failed.returncode, 1)
        self.assertNotIn(str(self.root).encode(), failed.stderr)
        self.assertLess(len(failed.stderr), 512)


if __name__ == "__main__":
    unittest.main()
