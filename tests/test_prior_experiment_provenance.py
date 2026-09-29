from __future__ import annotations

import copy
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_prior_experiment_provenance.py"
SPEC = importlib.util.spec_from_file_location("verify_prior_experiment_provenance", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


class PriorExperimentProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # macOS reports temporary paths through /var, a symlink to /private/var.
        # Use the canonical path so the production no-symlink rule remains strict.
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "repo"
        self.repository.mkdir(mode=0o700)
        subprocess.run(
            ["git", "init", "--quiet", str(self.repository)], check=True
        )
        (self.repository / ".gitignore").write_text("/data/\n")
        self.operations = self.repository / "data" / "operations" / "synthetic"
        self.operations.mkdir(parents=True, mode=0o700)
        self.operations.chmod(0o700)
        self.minutes = self.root / "minutes"
        self.rates = self.root / "rates"
        self.minutes.mkdir(mode=0o700)
        self.rates.mkdir(mode=0o700)
        self.clock = lambda: datetime(2026, 9, 12, 1, 2, 3, tzinfo=timezone.utc)
        self.write_slot(self.minutes, MODULE.MINUTES)
        self.write_slot(self.rates, MODULE.ATTACKING_RATES)

    def manifest(
        self,
        contract: MODULE.SlotContract,
        *,
        winner: str | None = None,
        passed: bool | None = None,
        output_name: str = "synthetic_result.parquet",
        output_body: bytes | None = None,
    ) -> tuple[dict[str, object], bytes]:
        body = output_body or (b"opaque-synthetic-" + contract.slot.encode())
        evaluated = winner is not None
        decision = contract.promote_decision if passed is True else contract.reject_decision
        manifest: dict[str, object] = {
            "status": "complete",
            "experiment_version": contract.experiment_version,
            "historical_classification": MODULE.HISTORICAL_CLASSIFICATION,
            "model_formula_frozen": "xfp_v01",
            "live_model_modified": False,
            "development_season": "2023-24",
            "holdout_season": "2024-25",
            "target_gameweeks": list(range(2, 39)),
            "candidate_definitions": copy.deepcopy(contract.candidate_definitions),
            "development_thresholds": copy.deepcopy(contract.development_thresholds),
            "development_tie_breakers": copy.deepcopy(contract.development_tie_breakers),
            **copy.deepcopy(contract.extra_fields),
            "development_winner": winner,
            "holdout_evaluated": evaluated,
            "holdout_passed": passed,
            "final_decision": decision,
            "generation_timestamp": "2026-01-02T03:04:05.123456Z",
            "immutable_inputs": [
                {"path": "/owner/private/synthetic-input.parquet", "sha256": "a" * 64}
            ],
            "outputs": [
                {
                    "path": output_name,
                    "rows": 2,
                    "bytes": len(body),
                    "sha256": digest(body),
                }
            ],
        }
        if contract.slot == "minutes":
            manifest["oracle_reference"] = {
                "evaluation_only": True,
                "modeled_mae_reduction_pct": 34.53,
                "modeled_rmse_reduction_pct": 17.35,
                "holdout_oracle_mae_improvement_captured_pct": (
                    50.0 if evaluated else None
                ),
            }
        return manifest, body

    def write_slot(
        self,
        directory: Path,
        contract: MODULE.SlotContract,
        *,
        winner: str | None = None,
        passed: bool | None = None,
        manifest_mutator=None,
        output_name: str = "synthetic_result.parquet",
        output_body: bytes | None = None,
    ) -> dict[str, object]:
        manifest, body = self.manifest(
            contract,
            winner=winner,
            passed=passed,
            output_name=output_name,
            output_body=output_body,
        )
        if manifest_mutator:
            manifest_mutator(manifest)
        (directory / output_name).write_bytes(body)
        (directory / MODULE.MANIFEST_NAME).write_bytes(MODULE.canonical_json(manifest))
        return manifest

    def reset_slots(
        self, *, winner: bool = False, passed: bool | None = None
    ) -> None:
        for directory in (self.minutes, self.rates):
            for child in directory.iterdir():
                child.unlink()
        self.write_slot(
            self.minutes,
            MODULE.MINUTES,
            winner="M1" if winner else None,
            passed=passed,
        )
        self.write_slot(
            self.rates,
            MODULE.ATTACKING_RATES,
            winner="S1" if winner else None,
            passed=passed,
        )

    def expected_digests(self) -> tuple[str, str]:
        return (
            digest((self.minutes / MODULE.MANIFEST_NAME).read_bytes()),
            digest((self.rates / MODULE.MANIFEST_NAME).read_bytes()),
        )

    def output_paths(self, prefix: str) -> tuple[Path, Path]:
        return (
            self.operations / f"{prefix}-private.json",
            self.operations / f"{prefix}-sanitized.json",
        )

    def verify_pair(self, prefix: str = "result") -> dict[str, object]:
        minutes_digest, rates_digest = self.expected_digests()
        private, sanitized = self.output_paths(prefix)
        return MODULE.verify(
            minutes_directory=self.minutes,
            minutes_manifest_sha256=minutes_digest,
            attacking_rates_directory=self.rates,
            attacking_rates_manifest_sha256=rates_digest,
            private_output=private,
            sanitized_output=sanitized,
            repository=self.repository,
            clock=self.clock,
        )

    def test_valid_no_winner_failed_holdout_and_passed_holdout(self) -> None:
        states = ((False, None), (True, False), (True, True))
        for index, (winner, passed) in enumerate(states):
            with self.subTest(winner=winner, passed=passed):
                self.reset_slots(winner=winner, passed=passed)
                result = self.verify_pair(f"state-{index}")
                self.assertEqual(
                    result["combined_status"], "LOCALLY_COHERENT_LEGACY_RESULTS"
                )
                self.assertEqual(result["slots"][0]["holdout_passed"], passed)

    def test_capture_is_opaque_atomic_and_owner_only(self) -> None:
        receipt = self.operations / "capture.json"
        original_open = MODULE.os.open
        manifest_opens = 0

        def counting_open(path, *args, **kwargs):
            nonlocal manifest_opens
            if path == MODULE.MANIFEST_NAME:
                manifest_opens += 1
            return original_open(path, *args, **kwargs)

        with patch.object(MODULE, "parse_manifest", side_effect=AssertionError), patch.object(
            MODULE.os, "open", side_effect=counting_open
        ):
            result = MODULE.capture(
                minutes_directory=self.minutes,
                attacking_rates_directory=self.rates,
                digest_receipt=receipt,
                repository=self.repository,
                clock=self.clock,
            )
        self.assertEqual(manifest_opens, 2)
        self.assertNotIn("development_winner", receipt.read_text())
        self.assertEqual(stat.S_IMODE(receipt.stat().st_mode), 0o600)
        self.assertEqual(len(result["slots"]), 2)

        receipt.unlink()
        (self.rates / MODULE.MANIFEST_NAME).unlink()
        with self.assertRaises((OSError, MODULE.ProvenanceError)):
            MODULE.capture(
                minutes_directory=self.minutes,
                attacking_rates_directory=self.rates,
                digest_receipt=receipt,
                repository=self.repository,
                clock=self.clock,
            )
        self.assertFalse(receipt.exists())

    def test_verify_hashes_and_parses_each_manifest_from_one_read(self) -> None:
        original_open = MODULE.os.open
        manifest_opens = 0

        def counting_open(path, *args, **kwargs):
            nonlocal manifest_opens
            if path == MODULE.MANIFEST_NAME:
                manifest_opens += 1
            return original_open(path, *args, **kwargs)

        with patch.object(MODULE.os, "open", side_effect=counting_open):
            self.verify_pair("single-read")
        self.assertEqual(manifest_opens, 2)

    def test_changed_since_capture_rejected_before_parse(self) -> None:
        old_minutes, rates = self.expected_digests()
        manifest = json.loads((self.minutes / MODULE.MANIFEST_NAME).read_text())
        manifest["status"] = "changed"
        (self.minutes / MODULE.MANIFEST_NAME).write_bytes(MODULE.canonical_json(manifest))
        private, sanitized = self.output_paths("changed")
        with patch.object(MODULE, "parse_manifest", side_effect=AssertionError):
            with self.assertRaisesRegex(MODULE.ProvenanceError, "MANIFEST_DIGEST_MISMATCH"):
                MODULE.verify(
                    minutes_directory=self.minutes,
                    minutes_manifest_sha256=old_minutes,
                    attacking_rates_directory=self.rates,
                    attacking_rates_manifest_sha256=rates,
                    private_output=private,
                    sanitized_output=sanitized,
                    repository=self.repository,
                    clock=self.clock,
                )
        self.assertFalse(private.exists())
        self.assertFalse(sanitized.exists())

    def test_each_fixed_contract_family_is_enforced(self) -> None:
        cases = {
            "status": lambda m: m.__setitem__("status", "running"),
            "version": lambda m: m.__setitem__("experiment_version", "other"),
            "classification": lambda m: m.__setitem__("historical_classification", "other"),
            "formula": lambda m: m.__setitem__("model_formula_frozen", "xfp_v02"),
            "live": lambda m: m.__setitem__("live_model_modified", True),
            "seasons": lambda m: m.__setitem__("development_season", "2024-25"),
            "gameweeks": lambda m: m.__setitem__("target_gameweeks", [2, 3]),
            "candidates": lambda m: m["candidate_definitions"].__setitem__("M1", "changed"),
            "thresholds": lambda m: m["development_thresholds"].__setitem__(
                "minutes_mae_reduction_pct", 4.0
            ),
            "tie_breakers": lambda m: m.__setitem__("development_tie_breakers", []),
            "policy": lambda m: m.__setitem__("observation_policy", "changed"),
        }
        for label, mutation in cases.items():
            with self.subTest(label=label):
                self.reset_slots()
                manifest = json.loads((self.minutes / MODULE.MANIFEST_NAME).read_text())
                mutation(manifest)
                (self.minutes / MODULE.MANIFEST_NAME).write_bytes(
                    MODULE.canonical_json(manifest)
                )
                with self.assertRaises(MODULE.ProvenanceError):
                    self.verify_pair(f"contract-{label}")

    def test_attacking_rate_prior_and_frozen_fields_are_enforced(self) -> None:
        fields = (
            "position_prior_definition",
            "position_prior_uses_player_own_history",
            "league_prior_fallback",
            "frozen_components",
            "coverage_policy",
            "selection_common_pair_policy",
        )
        for field in fields:
            with self.subTest(field=field):
                self.reset_slots()
                manifest = json.loads((self.rates / MODULE.MANIFEST_NAME).read_text())
                manifest[field] = "changed"
                (self.rates / MODULE.MANIFEST_NAME).write_bytes(
                    MODULE.canonical_json(manifest)
                )
                with self.assertRaises(MODULE.ProvenanceError):
                    self.verify_pair(f"rate-{field}")

    def test_invalid_output_declarations_are_rejected(self) -> None:
        names = (
            "/absolute.parquet",
            "../parent.parquet",
            "nested/file.parquet",
            "nested\\file.parquet",
            "not_parquet.txt",
        )
        for index, name in enumerate(names):
            with self.subTest(name=name):
                manifest, _ = self.manifest(MODULE.MINUTES)
                manifest["outputs"][0]["path"] = name
                with self.assertRaisesRegex(
                    MODULE.ProvenanceError, "INVALID_OUTPUT_DECLARATIONS"
                ):
                    MODULE.validate_manifest(manifest, MODULE.MINUTES)
        manifest, _ = self.manifest(MODULE.MINUTES)
        manifest["outputs"].append(copy.deepcopy(manifest["outputs"][0]))
        with self.assertRaises(MODULE.ProvenanceError):
            MODULE.validate_manifest(manifest, MODULE.MINUTES)
        manifest, _ = self.manifest(MODULE.MINUTES)
        manifest["outputs"].append(
            {"path": "extra.parquet", "rows": 0, "bytes": 1, "sha256": "b" * 64}
        )
        (self.minutes / MODULE.MANIFEST_NAME).write_bytes(MODULE.canonical_json(manifest))
        with self.assertRaises(OSError):
            MODULE._verify_slot(self.minutes, digest(MODULE.canonical_json(manifest)), MODULE.MINUTES)

    def test_undeclared_file_is_not_opened_or_reported(self) -> None:
        undeclared = self.minutes / "undeclared.parquet"
        undeclared.write_bytes(b"private-extra")
        original_open = MODULE.os.open

        def guarded_open(path, *args, **kwargs):
            if path == undeclared.name:
                raise AssertionError("undeclared file opened")
            return original_open(path, *args, **kwargs)

        with patch.object(MODULE.os, "open", side_effect=guarded_open):
            result = self.verify_pair("undeclared")
        body = MODULE.canonical_json(result)
        self.assertNotIn(b"undeclared", body)

    def test_output_size_and_digest_mismatch_fail_without_publication(self) -> None:
        for label, mutation in (
            ("size", lambda body: body + b"x"),
            ("digest", lambda body: b"X" * len(body)),
        ):
            with self.subTest(label=label):
                self.reset_slots()
                output = self.minutes / "synthetic_result.parquet"
                output.write_bytes(mutation(output.read_bytes()))
                private, sanitized = self.output_paths(f"output-{label}")
                with self.assertRaises(MODULE.ProvenanceError):
                    self.verify_pair(f"output-{label}")
                self.assertFalse(private.exists())
                self.assertFalse(sanitized.exists())

    def test_symlink_at_root_manifest_and_output_is_rejected(self) -> None:
        real = self.root / "real"
        real.mkdir()
        root_link = self.root / "root-link"
        root_link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(OSError):
            MODULE.open_root(root_link)

        self.reset_slots()
        manifest = self.minutes / MODULE.MANIFEST_NAME
        manifest_body = manifest.read_bytes()
        target = self.root / "manifest-target"
        target.write_bytes(manifest_body)
        manifest.unlink()
        manifest.symlink_to(target)
        with self.assertRaises((OSError, MODULE.ProvenanceError)):
            MODULE._capture_slot(self.minutes, MODULE.MINUTES)

        self.reset_slots()
        output = self.minutes / "synthetic_result.parquet"
        body = output.read_bytes()
        target = self.root / "output-target"
        target.write_bytes(body)
        output.unlink()
        output.symlink_to(target)
        with self.assertRaises((OSError, MODULE.ProvenanceError)):
            MODULE._verify_slot(
                self.minutes,
                digest((self.minutes / MODULE.MANIFEST_NAME).read_bytes()),
                MODULE.MINUTES,
            )

    def test_current_manifest_and_output_hard_links_are_rejected(self) -> None:
        manifest_alias = self.root / "manifest-hard-link"
        os.link(self.minutes / MODULE.MANIFEST_NAME, manifest_alias)
        with self.assertRaisesRegex(MODULE.ProvenanceError, "UNSAFE_INPUT_FILE"):
            MODULE._capture_slot(self.minutes, MODULE.MINUTES)
        manifest_alias.unlink()

        output_alias = self.root / "output-hard-link"
        os.link(self.minutes / "synthetic_result.parquet", output_alias)
        with self.assertRaisesRegex(MODULE.ProvenanceError, "UNSAFE_OUTPUT_FILE"):
            MODULE._verify_slot(
                self.minutes,
                digest((self.minutes / MODULE.MANIFEST_NAME).read_bytes()),
                MODULE.MINUTES,
            )

    def test_special_manifest_and_output_files_are_rejected(self) -> None:
        self.reset_slots()
        manifest = self.minutes / MODULE.MANIFEST_NAME
        manifest.unlink()
        os.mkfifo(manifest, 0o600)
        with self.assertRaises(MODULE.ProvenanceError):
            MODULE._capture_slot(self.minutes, MODULE.MINUTES)
        manifest.unlink()

        self.write_slot(self.minutes, MODULE.MINUTES)
        output = self.minutes / "synthetic_result.parquet"
        output.unlink()
        os.mkfifo(output, 0o600)
        with self.assertRaises(MODULE.ProvenanceError):
            MODULE._verify_slot(
                self.minutes,
                digest((self.minutes / MODULE.MANIFEST_NAME).read_bytes()),
                MODULE.MINUTES,
            )

    def test_duplicate_keys_invalid_utf8_nonfinite_and_malformed_json(self) -> None:
        cases = (
            b'{"status":"complete","status":"complete"}',
            b"\xff",
            b'{"value":NaN}',
            b'{"value":1e999}',
            b"{",
            b"[]",
        )
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(MODULE.ProvenanceError):
                    MODULE.parse_manifest(value)

    def test_inconsistent_result_states_are_rejected(self) -> None:
        cases = (
            (None, True, None, MODULE.MINUTES.reject_decision),
            (None, False, False, MODULE.MINUTES.reject_decision),
            ("M1", False, False, MODULE.MINUTES.reject_decision),
            ("M1", True, None, MODULE.MINUTES.reject_decision),
            ("M9", True, True, MODULE.MINUTES.promote_decision),
            ("M1", True, True, MODULE.MINUTES.reject_decision),
        )
        for winner, evaluated, passed, decision in cases:
            with self.subTest(winner=winner, evaluated=evaluated, passed=passed):
                manifest, _ = self.manifest(MODULE.MINUTES)
                manifest.update(
                    development_winner=winner,
                    holdout_evaluated=evaluated,
                    holdout_passed=passed,
                    final_decision=decision,
                )
                with self.assertRaisesRegex(MODULE.ProvenanceError, "INVALID_RESULT_STATE"):
                    MODULE.validate_manifest(manifest, MODULE.MINUTES)

    def test_immutable_inputs_are_validated_but_never_opened(self) -> None:
        sentinel = self.root / "must-not-open"
        sentinel.write_bytes(b"secret")
        for directory, contract in ((self.minutes, MODULE.MINUTES), (self.rates, MODULE.ATTACKING_RATES)):
            manifest = json.loads((directory / MODULE.MANIFEST_NAME).read_text())
            manifest["immutable_inputs"] = [{"path": str(sentinel), "sha256": "f" * 64}]
            (directory / MODULE.MANIFEST_NAME).write_bytes(MODULE.canonical_json(manifest))
        original_open = MODULE.os.open

        def guarded_open(path, *args, **kwargs):
            if os.fspath(path) == os.fspath(sentinel):
                raise AssertionError("immutable input opened")
            return original_open(path, *args, **kwargs)

        with patch.object(MODULE.os, "open", side_effect=guarded_open):
            self.verify_pair("immutable")

    def test_no_parquet_library_or_parser_is_present(self) -> None:
        source = SCRIPT.read_text()
        self.assertNotIn("import duckdb", source.lower())
        self.assertNotIn("import pyarrow", source.lower())
        self.assertNotIn("read_parquet", source.lower())
        self.assertNotIn("parquetfile", source.lower())

    def test_publication_is_exclusive_private_and_sanitized(self) -> None:
        private, sanitized = self.output_paths("published")
        result = self.verify_pair("published")
        self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(sanitized.stat().st_mode), 0o600)
        private_body = private.read_bytes()
        sanitized_body = sanitized.read_bytes()
        self.assertEqual(result, json.loads(sanitized_body))
        self.assertEqual(result["private_record_sha256"], digest(private_body))
        self.assertEqual(result["private_record_bytes"], len(private_body))
        self.assertIn(b"/owner/private/", private_body)
        self.assertNotIn(b"/owner/private/", sanitized_body)
        self.assertNotIn(b"MANIFEST_DECLARED", sanitized_body)
        with self.assertRaises(MODULE.ProvenanceError):
            self.verify_pair("published")

    def test_seeded_sensitive_values_do_not_reach_sanitized_output(self) -> None:
        token = "private.person@example.test"
        for directory in (self.minutes, self.rates):
            manifest = json.loads((directory / MODULE.MANIFEST_NAME).read_text())
            manifest["immutable_inputs"][0]["path"] = f"/Users/{token}/secret.parquet"
            (directory / MODULE.MANIFEST_NAME).write_bytes(MODULE.canonical_json(manifest))
        result = self.verify_pair("sensitive")
        private, sanitized = self.output_paths("sensitive")
        self.assertIn(token.encode(), private.read_bytes())
        self.assertNotIn(token.encode(), sanitized.read_bytes())
        self.assertNotIn(token.encode(), MODULE.canonical_json(result))

    def test_destinations_must_be_absent_ignored_private_and_under_operations(self) -> None:
        outside = self.repository / "outside.json"
        with self.assertRaisesRegex(MODULE.ProvenanceError, "OUTPUT_OUTSIDE_PRIVATE_ROOT"):
            MODULE.validate_destination(outside, self.repository)

        unignored_repo = self.root / "unignored"
        unignored_repo.mkdir()
        subprocess.run(["git", "init", "--quiet", str(unignored_repo)], check=True)
        unignored = unignored_repo / "data" / "operations" / "x"
        unignored.mkdir(parents=True, mode=0o700)
        with self.assertRaisesRegex(MODULE.ProvenanceError, "OUTPUT_NOT_SAFELY_IGNORED"):
            MODULE.validate_destination(unignored / "out.json", unignored_repo)

        public_parent = self.repository / "data" / "operations" / "public"
        public_parent.mkdir(mode=0o755)
        with self.assertRaisesRegex(MODULE.ProvenanceError, "OUTPUT_PARENT_NOT_OWNER_ONLY"):
            MODULE.validate_destination(public_parent / "out.json", self.repository)

        existing = self.operations / "existing.json"
        existing.write_text("x")
        with self.assertRaisesRegex(MODULE.ProvenanceError, "OUTPUT_EXISTS"):
            MODULE.validate_destination(existing, self.repository)

    def test_partial_publication_is_rolled_back(self) -> None:
        first_path, second_path = self.output_paths("rollback")
        first = MODULE.validate_destination(first_path, self.repository)
        second = MODULE.validate_destination(second_path, self.repository)
        original_link = MODULE.os.link
        calls = 0

        def failing_link(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic publication failure")
            return original_link(*args, **kwargs)

        try:
            with patch.object(MODULE.os, "link", side_effect=failing_link):
                with self.assertRaisesRegex(MODULE.ProvenanceError, "OUTPUT_PUBLICATION_FAILED"):
                    MODULE.publish_many([(first, b"one"), (second, b"two")])
        finally:
            first.close()
            second.close()
        self.assertFalse(first_path.exists())
        self.assertFalse(second_path.exists())
        self.assertEqual(list(self.operations.glob(".*.tmp")), [])

    def test_output_replacement_during_hashing_is_rejected(self) -> None:
        body = b"a" * (MODULE.CHUNK_BYTES + 1)
        output = self.minutes / "large.parquet"
        output.write_bytes(body)
        descriptor = MODULE.open_root(self.minutes)
        original_read = MODULE.os.read
        changed = False

        def changing_read(fd, count):
            nonlocal changed
            block = original_read(fd, count)
            if block and not changed:
                changed = True
                replacement = self.minutes / "replacement"
                replacement.write_bytes(body)
                os.replace(replacement, output)
            return block

        try:
            with patch.object(MODULE.os, "read", side_effect=changing_read):
                with self.assertRaises(MODULE.ProvenanceError):
                    MODULE._hash_stable_at(descriptor, output.name, expected_size=len(body))
        finally:
            os.close(descriptor)

    def test_root_replacement_and_duplicate_slot_are_rejected(self) -> None:
        with self.assertRaisesRegex(MODULE.ProvenanceError, "DUPLICATE_LOGICAL_SLOT"):
            MODULE.capture(
                minutes_directory=self.minutes,
                attacking_rates_directory=self.minutes,
                digest_receipt=self.operations / "same.json",
                repository=self.repository,
                clock=self.clock,
            )
        descriptor = MODULE.open_root(self.minutes)
        initial = MODULE.fingerprint(os.fstat(descriptor))
        moved = self.root / "moved"
        self.minutes.rename(moved)
        self.minutes.mkdir()
        try:
            with self.assertRaisesRegex(MODULE.ProvenanceError, "DIRECTORY_CHANGED"):
                MODULE._root_unchanged(self.minutes, descriptor, initial)
        finally:
            os.close(descriptor)

    def test_invalid_immutable_inputs_output_types_and_timestamp(self) -> None:
        mutations = (
            lambda m: m.__setitem__("immutable_inputs", []),
            lambda m: m["immutable_inputs"].append(copy.deepcopy(m["immutable_inputs"][0])),
            lambda m: m["outputs"][0].__setitem__("rows", True),
            lambda m: m["outputs"][0].__setitem__("bytes", 0),
            lambda m: m.__setitem__("generation_timestamp", "2026-01-02T03:04:05+00:00"),
            lambda m: m.__setitem__("generation_timestamp", "2026-01-02T03:04:05.123Z"),
        )
        for mutation in mutations:
            manifest, _ = self.manifest(MODULE.MINUTES)
            mutation(manifest)
            with self.assertRaises(MODULE.ProvenanceError):
                MODULE.validate_manifest(manifest, MODULE.MINUTES)

    def test_contract_source_identities_are_pinned(self) -> None:
        self.assertEqual(
            MODULE.MINUTES.contract_reference_commit,
            "c5269ed4421b8937a8fc62ca35029af829345f92",
        )
        self.assertEqual(
            MODULE.MINUTES.source_blob,
            "b58a47d84e48026335b235eb9e54b1fb685d471f",
        )
        self.assertEqual(
            MODULE.ATTACKING_RATES.contract_reference_commit,
            "391b04328754ad95bf4304c438826688d01ad7e0",
        )
        self.assertEqual(
            MODULE.ATTACKING_RATES.source_blob,
            "566b27cf438ef1821ba021d6080745db883c96bb",
        )
        source_paths = (
            (
                ROOT / "src/fpl_decision_engine/historical_minutes_experiment.py",
                MODULE.MINUTES.source_blob,
            ),
            (
                ROOT / "src/fpl_decision_engine/historical_attacking_rate_experiment.py",
                MODULE.ATTACKING_RATES.source_blob,
            ),
        )
        for path, expected in source_paths:
            body = path.read_bytes()
            git_blob = hashlib.sha1(
                f"blob {len(body)}\0".encode("ascii") + body, usedforsecurity=False
            ).hexdigest()
            self.assertEqual(git_blob, expected)

    def test_cli_capture_verify_and_generic_failure_output(self) -> None:
        receipt = self.operations / "cli-receipt.json"
        stdout = io.StringIO()
        with patch.object(MODULE, "_repo_root", return_value=self.repository), contextlib.redirect_stdout(stdout):
            code = MODULE.main(
                [
                    "capture",
                    "--minutes-directory",
                    str(self.minutes),
                    "--attacking-rates-directory",
                    str(self.rates),
                    "--digest-receipt",
                    str(receipt),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "CAPTURE_COMPLETE")
        captured = json.loads(receipt.read_text())
        captured_by_slot = {entry["logical_slot"]: entry for entry in captured["slots"]}
        private, sanitized = self.output_paths("cli")
        stdout = io.StringIO()
        with patch.object(MODULE, "_repo_root", return_value=self.repository), contextlib.redirect_stdout(stdout):
            code = MODULE.main(
                [
                    "verify",
                    "--minutes-directory",
                    str(self.minutes),
                    "--minutes-manifest-sha256",
                    captured_by_slot["minutes"]["manifest_sha256"],
                    "--attacking-rates-directory",
                    str(self.rates),
                    "--attacking-rates-manifest-sha256",
                    captured_by_slot["attacking_rates"]["manifest_sha256"],
                    "--private-output",
                    str(private),
                    "--sanitized-output",
                    str(sanitized),
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(stdout.getvalue())["status"],
            "LOCALLY_COHERENT_LEGACY_RESULTS",
        )

        stderr = io.StringIO()
        private_path_text = str(self.root / "private-missing-directory")
        with patch.object(MODULE, "_repo_root", return_value=self.repository), contextlib.redirect_stderr(stderr):
            code = MODULE.main(
                [
                    "capture",
                    "--minutes-directory",
                    private_path_text,
                    "--attacking-rates-directory",
                    str(self.rates),
                    "--digest-receipt",
                    str(self.operations / "failure.json"),
                ]
            )
        self.assertEqual(code, 1)
        self.assertEqual(
            stderr.getvalue(),
            "Prior-result provenance operation failed: IO_OR_GIT_FAILURE.\n",
        )
        self.assertNotIn(private_path_text, stderr.getvalue())

        private, sanitized = self.output_paths("fixed-code")
        stderr = io.StringIO()
        with patch.object(
            MODULE, "_repo_root", return_value=self.repository
        ), contextlib.redirect_stderr(stderr):
            code = MODULE.main(
                [
                    "verify",
                    "--minutes-directory",
                    str(self.minutes),
                    "--minutes-manifest-sha256",
                    "invalid",
                    "--attacking-rates-directory",
                    str(self.rates),
                    "--attacking-rates-manifest-sha256",
                    captured_by_slot["attacking_rates"]["manifest_sha256"],
                    "--private-output",
                    str(private),
                    "--sanitized-output",
                    str(sanitized),
                ]
            )
        self.assertEqual(code, 1)
        self.assertEqual(
            stderr.getvalue(),
            "Prior-result provenance operation failed: INVALID_CAPTURED_DIGEST.\n",
        )

        stderr = io.StringIO()
        with patch.object(
            MODULE, "capture", side_effect=RuntimeError(private_path_text)
        ), contextlib.redirect_stderr(stderr):
            code = MODULE.main(
                [
                    "capture",
                    "--minutes-directory",
                    str(self.minutes),
                    "--attacking-rates-directory",
                    str(self.rates),
                    "--digest-receipt",
                    str(self.operations / "internal-error.json"),
                ]
            )
        self.assertEqual(code, 1)
        self.assertEqual(
            stderr.getvalue(),
            "Prior-result provenance operation failed: INTERNAL_ERROR.\n",
        )
        self.assertNotIn(private_path_text, stderr.getvalue())

    def test_manifest_replacement_during_single_read_is_rejected(self) -> None:
        body = b"{" + b" " * MODULE.CHUNK_BYTES + b"}"
        manifest = self.minutes / MODULE.MANIFEST_NAME
        manifest.write_bytes(body)
        descriptor = MODULE.open_root(self.minutes)
        original_read = MODULE.os.read
        changed = False

        def changing_read(fd, count):
            nonlocal changed
            block = original_read(fd, count)
            if block and not changed:
                changed = True
                replacement = self.minutes / "replacement-manifest"
                replacement.write_bytes(body)
                os.replace(replacement, manifest)
            return block

        try:
            with patch.object(MODULE.os, "read", side_effect=changing_read):
                with self.assertRaisesRegex(MODULE.ProvenanceError, "INPUT_CHANGED"):
                    MODULE._read_stable_at(
                        descriptor,
                        MODULE.MANIFEST_NAME,
                        maximum_bytes=MODULE.MAX_MANIFEST_BYTES,
                    )
        finally:
            os.close(descriptor)


if __name__ == "__main__":
    unittest.main()
