from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest

from fpl_decision_engine.isolated_live_run import (
    CommandResult,
    IsolatedLiveRunError,
    PLAN_FIELDS,
    PLAN_FORMAT,
    PreflightRequest,
    paths_overlap,
    run_preflight,
    validate_sandbox_isolation,
)


PRIMARY_COMMIT = "1" * 40
CODE_COMMIT = "2" * 40
DEADLINE = "2099-09-12T12:30:00.000000Z"


class SyntheticPreflight:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.primary = root / "primary"
        self.code = root / "code"
        self.primary.mkdir()
        self.code.mkdir()
        self.data = self.primary / "data"
        self.data.mkdir()
        self.sandbox = self.data / "operational-sandboxes" / "task032"
        self.sandbox.mkdir(parents=True)
        self.sandbox.chmod(0o700)
        self.schedule_root = root / "schedule"
        self.schedule_root.mkdir()
        self.plan_path = self.schedule_root / "schedule-plan.json"
        self.digest_path = self.schedule_root / "schedule-plan.json.sha256"
        self.protected: dict[str, Path] = {}
        for field in (
            "raw_data_root",
            "clean_data_root",
            "feature_data_root",
            "prediction_data_root",
            "evaluation_data_root",
            "task028b_control_data_root",
        ):
            path = self.data / "protected" / field
            path.mkdir(parents=True)
            self.protected[field] = path
        self.installed = root / "installed.plist"
        self.plan = self._plan()
        self.write_plan()
        self.write_installed()
        self.launch_state = "idle"
        self.launch_arguments = list(self.expected_arguments)
        self.launch_returncode = 0
        self.primary_untracked = ("task025_claude_review_bundle.txt", "task025_review.patch")

    @property
    def expected_arguments(self) -> list[str]:
        return [
            str(Path(os.path.abspath(os.sys.executable))),
            "-I",
            str(self.primary / "scripts" / "completion_monitor_production_schedule.py"),
            "run",
            "--plan",
            str(self.plan_path),
            "--plan-sha256-file",
            str(self.digest_path),
        ]

    def _plan(self) -> dict[str, object]:
        value: dict[str, object] = {
            "acknowledged_untracked_paths": [],
            "controller_path": str(
                self.primary / "scripts" / "completion_monitor_production_schedule.py"
            ),
            "controller_sha256": "3" * 64,
            "created_at": "2099-09-01T00:00:00.000000Z",
            "expected_commit": PRIMARY_COMMIT,
            "expires_at": "2099-09-15T00:00:00.000000Z",
            "format": PLAN_FORMAT,
            "gameweek": 4,
            "installed_plist_path": str(self.installed),
            "interval_seconds": 900,
            "label": "com.fpl-decision-engine.completion-monitor.2099-00.gw4",
            "not_before": "2099-09-12T12:30:00.000000Z",
            "prediction_snapshot_timestamp": None,
            "python_distribution_inventory_sha256": "4" * 64,
            "python_path": str(Path(os.path.abspath(os.sys.executable))),
            "python_resolved_path": str(Path(os.path.abspath(os.sys.executable)).resolve()),
            "python_version": "synthetic",
            "repository": str(self.primary),
            "season": "2099-00",
            "uid": os.getuid(),
        }
        value.update({field: str(path) for field, path in self.protected.items()})
        self.assert_fields(value)
        return value

    @staticmethod
    def assert_fields(value: dict[str, object]) -> None:
        if set(value) != PLAN_FIELDS:
            raise AssertionError((set(value) - PLAN_FIELDS, PLAN_FIELDS - set(value)))

    def write_plan(self) -> str:
        body = json.dumps(
            self.plan,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        self.plan_path.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        self.digest_path.write_text(f"{digest}  schedule-plan.json\n", encoding="ascii")
        self.digest = digest
        return digest

    def write_installed(self) -> None:
        self.installed.write_bytes(
            plistlib.dumps(
                {
                    "Label": self.plan["label"],
                    "ProgramArguments": self.expected_arguments,
                }
            )
        )

    def request(self, **changes) -> PreflightRequest:
        values = dict(
            primary_repository=self.primary,
            expected_primary_commit=PRIMARY_COMMIT,
            acknowledged_primary_untracked=self.primary_untracked,
            code_repository=self.code,
            expected_code_commit=CODE_COMMIT,
            sandbox_root=self.sandbox,
            schedule_plan=self.plan_path,
            schedule_plan_sha256_file=self.digest_path,
            expected_schedule_plan_sha256=self.digest,
            official_deadline=DEADLINE,
            required_free_bytes=0,
        )
        values.update(changes)
        return PreflightRequest(**values)

    def runner(self, arguments, _timeout) -> CommandResult:
        args = list(arguments)
        if args[0] == "/usr/bin/git":
            repository = Path(args[2])
            command = args[3:]
            if command == ["rev-parse", "HEAD"]:
                commit = PRIMARY_COMMIT if repository == self.primary else CODE_COMMIT
                return CommandResult(0, f"{commit}\n".encode())
            if "status" in command:
                if repository == self.primary and "--" not in command:
                    body = b"".join(f"?? {item}\0".encode() for item in self.primary_untracked)
                    return CommandResult(0, body)
                return CommandResult(0)
            if command[:2] == ["check-ignore", "--quiet"]:
                return CommandResult(0)
            if command and command[0] in {"ls-files", "diff"}:
                return CommandResult(0)
        if args[:2] == ["/bin/launchctl", "print"]:
            if self.launch_returncode:
                return CommandResult(self.launch_returncode, stderr=b"missing")
            lines = [
                f"{args[2]} = {{",
                "\tactive count = " + ("1" if self.launch_state == "running" else "0"),
                f"\tpath = {self.installed}",
            ]
            if self.launch_state == "running":
                lines.append("\tstate = running")
            elif self.launch_state == "idle":
                lines.append("\tstate = not running")
            elif self.launch_state != "idle":
                lines.append(f"\tstate = {self.launch_state}")
            lines.append("\targuments = {")
            lines.extend(f"\t\t{item}" for item in self.launch_arguments)
            lines.extend(["\t}", "}"])
            return CommandResult(0, ("\n".join(lines) + "\n").encode())
        raise AssertionError(args)


class IsolatedLiveRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.synthetic = SyntheticPreflight(Path(self.temporary.name).resolve())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def execute(self, request: PreflightRequest | None = None):
        return run_preflight(
            request or self.synthetic.request(),
            runner=self.synthetic.runner,
            clock=lambda: datetime(2099, 9, 11, tzinfo=timezone.utc),
        )

    def test_success_returns_only_bounded_public_metadata(self) -> None:
        result = self.execute()
        self.assertEqual(result.status, "PREFLIGHT_PASSED")
        self.assertEqual(result.monitor_state, "loaded-idle")
        self.assertEqual(set(result.public_payload()), {
            "code_commit", "free_bytes", "monitor_state", "primary_commit",
            "sandbox_root", "schedule_plan_sha256", "status",
        })

    def test_running_monitor_is_accepted(self) -> None:
        self.synthetic.launch_state = "running"
        self.assertEqual(self.execute().monitor_state, "running")

    def test_missing_changed_and_unexpected_monitor_fail_closed(self) -> None:
        self.synthetic.launch_returncode = 113
        with self.assertRaises(IsolatedLiveRunError):
            self.execute()
        self.synthetic.launch_returncode = 0
        self.synthetic.launch_arguments[-1] = "/changed/digest"
        with self.assertRaises(IsolatedLiveRunError):
            self.execute()
        self.synthetic.launch_arguments = list(self.synthetic.expected_arguments)
        self.synthetic.launch_state = "waiting"
        with self.assertRaises(IsolatedLiveRunError):
            self.execute()

        def inspection_failure(_arguments, _timeout):
            raise IsolatedLiveRunError("synthetic inspection failure")
        with self.assertRaises(IsolatedLiveRunError):
            run_preflight(
                self.synthetic.request(),
                runner=inspection_failure,
                clock=lambda: datetime(2099, 9, 11, tzinfo=timezone.utc),
            )

    def test_installed_definition_change_fails_even_with_valid_live_text(self) -> None:
        self.synthetic.installed.write_bytes(
            plistlib.dumps(
                {
                    "Label": self.synthetic.plan["label"],
                    "ProgramArguments": ["changed"],
                }
            )
        )
        with self.assertRaises(IsolatedLiveRunError):
            self.execute()

    def test_missing_malformed_and_hash_mismatched_plan_fail_closed(self) -> None:
        missing = self.synthetic.request(
            schedule_plan=self.synthetic.root / "missing" / "schedule-plan.json"
        )
        with self.assertRaises(IsolatedLiveRunError):
            self.execute(missing)
        self.synthetic.plan_path.write_bytes(b"not json")
        with self.assertRaises(IsolatedLiveRunError):
            self.execute()
        self.synthetic.write_plan()
        self.synthetic.digest_path.write_text(f"{'0' * 64}  schedule-plan.json\n")
        with self.assertRaises(IsolatedLiveRunError):
            self.execute()

    def test_exact_primary_and_code_commits_are_required(self) -> None:
        with self.assertRaises(IsolatedLiveRunError):
            self.execute(self.synthetic.request(expected_primary_commit="0" * 40))
        with self.assertRaises(IsolatedLiveRunError):
            self.execute(self.synthetic.request(expected_code_commit="0" * 40))

    def test_deadline_and_disk_fail_closed(self) -> None:
        with self.assertRaises(IsolatedLiveRunError):
            run_preflight(
                self.synthetic.request(),
                runner=self.synthetic.runner,
                clock=lambda: datetime(2099, 9, 12, 12, 30, tzinfo=timezone.utc),
            )
        with self.assertRaises(IsolatedLiveRunError):
            self.execute(self.synthetic.request(required_free_bytes=10**30))

    def test_outside_data_and_git_visibility_fail_closed(self) -> None:
        outside = self.synthetic.root / "outside"
        outside.mkdir(mode=0o700)
        with self.assertRaises(IsolatedLiveRunError):
            self.execute(self.synthetic.request(sandbox_root=outside))

        original = self.synthetic.runner
        def visible(arguments, timeout):
            args = list(arguments)
            if args[0] == "/usr/bin/git" and "ls-files" in args:
                return CommandResult(0, b"data/operational-sandboxes/task032/file\n")
            return original(arguments, timeout)
        with self.assertRaises(IsolatedLiveRunError):
            run_preflight(
                self.synthetic.request(),
                runner=visible,
                clock=lambda: datetime(2099, 9, 11, tzinfo=timezone.utc),
            )

    def test_overlap_equality_and_both_ancestry_directions_are_rejected(self) -> None:
        for protected in self.synthetic.protected.values():
            child = protected / "child"
            child.mkdir()
            parent = protected.parent
            for candidate in (protected, child, parent):
                candidate.chmod(0o700)
                with self.assertRaises(IsolatedLiveRunError):
                    validate_sandbox_isolation(candidate, [protected])
            self.assertFalse(paths_overlap(self.synthetic.sandbox, protected))

    def test_symlink_alias_cannot_bypass_overlap_detection(self) -> None:
        protected = next(iter(self.synthetic.protected.values()))
        alias = self.synthetic.root / "alias"
        alias.symlink_to(protected, target_is_directory=True)
        self.assertTrue(paths_overlap(alias, protected))
        with self.assertRaises(IsolatedLiveRunError):
            validate_sandbox_isolation(alias, [protected])

    def test_preflight_boundary_has_no_engine_manager_or_network_import(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "fpl_decision_engine"
            / "isolated_live_run.py"
        ).read_text(encoding="utf-8")
        imported: list[str] = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        forbidden = ("fpl_decision_engine", "httpx", "urllib", "requests")
        self.assertFalse(
            any(
                name == item or name.startswith(item + ".")
                for name in imported
                for item in forbidden
            )
        )


if __name__ == "__main__":
    unittest.main()
