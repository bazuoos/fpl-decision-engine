"""Offline tests for the Task028D synthetic launchd drill harness."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import completion_monitor_schedule_drill as drill


FIXED_TIME = datetime(2026, 9, 9, 4, 0, tzinfo=timezone.utc)


class ScheduleDrillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir(mode=0o700)
        self.git("init", "-q")
        (self.repo / "scripts").mkdir(mode=0o700)
        self.source_script = Path(drill.__file__).resolve()
        shutil.copyfile(
            self.source_script,
            self.repo / drill.SCRIPT_RELATIVE,
        )
        (self.repo / ".gitignore").write_text("/data/\n/.venv/\n", encoding="utf-8")
        venv_bin = self.repo / ".venv" / "bin"
        venv_bin.mkdir(parents=True, mode=0o700)
        self.python = venv_bin / "python"
        self.python.symlink_to(Path(sys.executable).resolve())
        self.git("add", ".gitignore", drill.SCRIPT_RELATIVE.as_posix())
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture",
        )
        self.commit = self.git("rev-parse", "HEAD").decode().strip()
        self.drill_parent = self.repo / drill.DRILL_PARENT_RELATIVE
        self.drill_parent.mkdir(parents=True, mode=0o700)
        self.drill_parent.chmod(0o700)

    def git(self, *args: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout

    def prepare(
        self,
        drill_id: str = "synthetic-001",
        scenario: str = "complete",
        **kwargs: object,
    ) -> drill.PreparedDrill:
        with patch.object(drill.platform, "system", return_value="Darwin"):
            return drill.prepare_drill(
                repository=self.repo,
                expected_commit=self.commit,
                python=self.python,
                drill_parent=self.drill_parent,
                drill_id=drill_id,
                scenario=scenario,
                **kwargs,
            )

    @staticmethod
    def fixed_clock() -> datetime:
        return FIXED_TIME

    @staticmethod
    def fixed_monotonic() -> float:
        return 100.0

    def run_worker(self, prepared: drill.PreparedDrill, sleeper=None) -> int:
        elapsed = {"value": 0.0}

        def synthetic_sleep(seconds):
            elapsed["value"] += float(seconds)

        return drill.worker(
            drill_root=prepared.root,
            drill_id=prepared.root.name.rsplit("-", 1)[0],
            scenario=prepared.scenario,
            sleeper=sleeper or synthetic_sleep,
            clock=self.fixed_clock,
            monotonic=lambda: 100.0 + elapsed["value"],
        )

    @staticmethod
    def add_lifecycle_evidence(
        prepared: drill.PreparedDrill, *, loaded: bool = True
    ) -> None:
        evidence = {
            "launchctl-initial-absent.txt": b"Could not find service\n",
            "launchctl-unloaded.txt": b"Could not find service\n",
        }
        if loaded:
            evidence["launchctl-loaded.txt"] = b"exact synthetic service loaded\n"
        for name, body in evidence.items():
            (prepared.root / name).write_bytes(body)
            (prepared.root / name).chmod(0o600)

    def fake_launchd_runner(
        self,
        prepared: drill.PreparedDrill,
        *,
        bootstrap_ok: bool = True,
        cleanup_ok: bool = True,
    ):
        calls: list[tuple[str, ...]] = []
        state = {"loaded": False}

        def runner(args, timeout):
            command = tuple(args)
            calls.append(command)
            self.assertEqual(timeout, drill.COMMAND_TIMEOUT_SECONDS)
            self.assertTrue(Path(command[0]).is_absolute())
            if command[0] == str(drill.SYSTEM_PLUTIL):
                return drill.CommandResult(0, b"plist ok\n")
            self.assertEqual(command[0], str(drill.SYSTEM_LAUNCHCTL))
            action = command[1]
            if action == "print":
                if state["loaded"]:
                    return drill.CommandResult(0, b"exact synthetic service loaded\n")
                return drill.CommandResult(113, b"", b"Could not find service\n")
            if action == "bootstrap":
                if not bootstrap_ok:
                    return drill.CommandResult(5, b"", b"synthetic failure\n")
                state["loaded"] = True
                return drill.CommandResult(0)
            if action == "kickstart":
                self.run_worker(prepared)
                return drill.CommandResult(0)
            if action == "bootout":
                if cleanup_ok:
                    state["loaded"] = False
                    return drill.CommandResult(0)
                return drill.CommandResult(5, b"", b"synthetic cleanup failure\n")
            self.fail(f"unexpected command: {command}")

        return runner, calls

    def test_worker_has_no_network_or_engine_import(self) -> None:
        tree = ast.parse(self.source_script.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse(
            imported
            & {
                "fpl_decision_engine",
                "http",
                "httpx",
                "requests",
                "socket",
                "urllib",
            }
        )
        self.assertNotIn("os.environ", self.source_script.read_text(encoding="utf-8"))

    def test_drill_plist_uses_exact_allowlist_and_absolute_argv(self) -> None:
        prepared = self.prepare()
        value = plistlib.loads(prepared.plist.read_bytes())
        self.assertEqual(set(value), drill.PLIST_KEYS)
        self.assertEqual(value["Label"], prepared.label)
        self.assertTrue(all(Path(item).is_absolute() for item in value["ProgramArguments"][:2]))
        self.assertEqual(value["ProgramArguments"][2], "worker")
        self.assertEqual(value["WorkingDirectory"], str(self.repo))
        self.assertEqual(value["Umask"], "077")
        self.assertEqual(value["StartInterval"], 10)
        self.assertFalse(value["KeepAlive"])
        self.assertNotIn("RunAtLoad", value)
        self.assertNotIn("EnvironmentVariables", value)
        self.assertNotIn("sh", value["ProgramArguments"])

    def test_production_plist_is_render_only_exact_target_and_900_seconds(self) -> None:
        body = drill.production_plist(
            label="com.fpl-decision-engine.completion-monitor.2026-27.gw4",
            python=self.python,
            repository=self.repo,
            season="2026-27",
            gameweek=4,
            stdout_path=self.repo / "data/out.log",
            stderr_path=self.repo / "data/err.log",
            prediction_snapshot="20260910T061943.538960Z",
        )
        value = plistlib.loads(body)
        self.assertEqual(set(value), drill.PLIST_KEYS)
        self.assertEqual(value["StartInterval"], 900)
        self.assertEqual(
            value["ProgramArguments"],
            [
                str(self.python),
                "-m",
                "fpl_decision_engine",
                "monitor-completion",
                "--season",
                "2026-27",
                "--target-gameweek",
                "4",
                "--prediction-snapshot-timestamp",
                "20260910T061943.538960Z",
            ],
        )
        self.assertNotIn("RunAtLoad", value)

    def test_prepare_creates_private_no_overwrite_package_without_launchctl(self) -> None:
        calls = []

        def runner(args, timeout):
            calls.append(tuple(args))
            return drill.command(args, timeout)

        prepared = self.prepare(runner=runner)
        self.assertEqual(prepared.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(
            {path.name for path in prepared.root.iterdir()},
            {
                "invocations",
                "launch-agent.plist",
                "launch-agent.plist.sha256",
                "metadata.json",
                "outcome-script.json",
                "worker.stderr.log",
                "worker.stdout.log",
            },
        )
        for path in prepared.root.iterdir():
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        self.assertFalse(any(call[0] == str(drill.SYSTEM_LAUNCHCTL) for call in calls))
        with self.assertRaises(drill.DrillError):
            self.prepare()

    def test_run_reload_revalidates_exact_prepared_bytes(self) -> None:
        prepared = self.prepare()
        with patch.object(drill.platform, "system", return_value="Darwin"):
            loaded = drill.load_prepared_drill(
                repository=self.repo,
                expected_commit=self.commit,
                python=self.python,
                drill_root=prepared.root,
            )
        self.assertEqual(loaded, prepared)
        with (prepared.root / "launch-agent.plist").open("ab") as stream:
            stream.write(b"changed")
        with patch.object(drill.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(drill.DrillError, "plist differs"):
                drill.load_prepared_drill(
                    repository=self.repo,
                    expected_commit=self.commit,
                    python=self.python,
                    drill_root=prepared.root,
                )

    def test_run_reload_rejects_unsafe_prepared_file_permissions(self) -> None:
        prepared = self.prepare()
        (prepared.root / "worker.stdout.log").chmod(0o644)
        with patch.object(drill.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(drill.DrillError, "owner-only file"):
                drill.load_prepared_drill(
                    repository=self.repo,
                    expected_commit=self.commit,
                    python=self.python,
                    drill_root=prepared.root,
                )

    def test_preflight_rejects_dirty_or_unreviewed_untracked_repository(self) -> None:
        (self.repo / "tracked.txt").write_text("tracked\n")
        self.git("add", "tracked.txt")
        with self.assertRaisesRegex(drill.DrillError, "tracked or staged"):
            self.prepare()
        self.git("reset", "-q")
        (self.repo / "tracked.txt").unlink()
        (self.repo / "surprise.txt").write_text("surprise\n")
        with self.assertRaisesRegex(drill.DrillError, "unreviewed untracked"):
            self.prepare()
        prepared = self.prepare(allowed_untracked={"surprise.txt"})
        self.assertTrue(prepared.root.is_dir())

    def test_preflight_rejects_script_different_from_reviewed_commit(self) -> None:
        with (self.repo / drill.SCRIPT_RELATIVE).open("a") as stream:
            stream.write("# changed\n")
        with self.assertRaisesRegex(drill.DrillError, "tracked or staged"):
            self.prepare()

    def test_complete_worker_sequence_and_out_of_sequence_exit_four(self) -> None:
        prepared = self.prepare()
        self.assertEqual([self.run_worker(prepared) for _ in range(4)], [0, 2, 0, 4])
        events = drill.load_events(prepared.root)
        self.assertEqual(
            [event["outcome"] for event in events],
            ["WAITING", "RETRYABLE", "COMPLETE", "UNEXPECTED_SEQUENCE"],
        )
        self.assertEqual([event["exit_status"] for event in events], [0, 2, 0, 4])
        self.assertTrue((prepared.root / "terminal.json").is_file())
        self.assertFalse((prepared.root / ".synthetic-worker.lock").exists())

    def test_review_required_worker_sequence(self) -> None:
        prepared = self.prepare("synthetic-002", "review")
        self.assertEqual([self.run_worker(prepared) for _ in range(2)], [0, 3])
        terminal = json.loads((prepared.root / "terminal.json").read_bytes())
        self.assertEqual(terminal["outcome"], "REVIEW_REQUIRED")

    def test_existing_worker_lock_records_overlap_and_is_not_removed(self) -> None:
        prepared = self.prepare()
        lock = prepared.root / ".synthetic-worker.lock"
        lock.write_text("held\n")
        lock.chmod(0o600)
        self.assertEqual(self.run_worker(prepared), 4)
        self.assertTrue(lock.exists())
        self.assertEqual(drill.load_events(prepared.root)[0]["outcome"], "OVERLAP")

    def test_interrupted_worker_records_failure_and_releases_its_lock(self) -> None:
        prepared = self.prepare()

        def interrupted(_seconds):
            raise drill.DrillInterrupted("synthetic signal")

        self.assertEqual(self.run_worker(prepared, sleeper=interrupted), 4)
        event = drill.load_events(prepared.root)[0]
        self.assertEqual(event["outcome"], "INTERRUPTED")
        self.assertFalse((prepared.root / ".synthetic-worker.lock").exists())

    def test_worker_ignores_hostile_environment_and_never_copies_it(self) -> None:
        secret = "HOSTILE_SYNTHETIC_VALUE_4m8q2k7v"
        first = self.prepare("synthetic-env1")
        second = self.prepare("synthetic-env2")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": secret}, clear=True):
            self.run_worker(first)
        with patch.dict(os.environ, {"DIFFERENT_SECRET": secret[::-1]}, clear=True):
            self.run_worker(second)
        self.assertEqual(
            (first.root / "invocations/0001.json").read_bytes(),
            (second.root / "invocations/0001.json").read_bytes(),
        )
        for root in (first.root, second.root):
            for path in root.rglob("*"):
                if path.is_file():
                    self.assertNotIn(secret.encode(), path.read_bytes())

    def test_tampered_outcome_script_and_symlink_root_fail_closed(self) -> None:
        prepared = self.prepare()
        body = json.loads((prepared.root / "outcome-script.json").read_bytes())
        body["steps"][0]["exit_status"] = 9
        (prepared.root / "outcome-script.json").write_bytes(drill.canonical(body))
        with self.assertRaises(drill.DrillError):
            self.run_worker(prepared)
        link = self.root / "linked-root"
        link.symlink_to(prepared.root, target_is_directory=True)
        with self.assertRaisesRegex(drill.DrillError, "symlink"):
            drill.worker(
                drill_root=link,
                drill_id="synthetic-001",
                scenario="complete",
            )

    def test_execute_complete_scenario_uses_exact_commands_and_verifies_evidence(self) -> None:
        prepared = self.prepare()
        runner, calls = self.fake_launchd_runner(prepared)
        waits = {"count": 0}

        def scheduler_sleep(_seconds):
            waits["count"] += 1
            if waits["count"] in {1, 2}:
                self.run_worker(prepared)

        with patch.object(drill, "process_absent", return_value=True):
            passed = drill.execute_drill(
                prepared,
                timeout_seconds=30,
                quiet_seconds=12,
                runner=runner,
                sleeper=scheduler_sleep,
                monotonic=lambda: float(waits["count"]),
            )
        self.assertTrue(passed)
        result = json.loads((prepared.root / "result.json").read_bytes())
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["observed_outcomes"], list(prepared.expected_outcomes))
        self.assertEqual(result["maximum_concurrency"], 1)
        self.assertTrue(drill.verify_manifest(prepared.root))
        self.assertTrue((prepared.root / "launchctl-initial-absent.txt").is_file())
        self.assertEqual(
            [call[1] for call in calls if call[0] == str(drill.SYSTEM_LAUNCHCTL)],
            ["print", "bootstrap", "print", "kickstart", "bootout", "print"],
        )
        self.assertFalse(any(len(call) >= 2 and call[1] in {"list", "print-disabled"} for call in calls))

    def test_execute_review_scenario_stops_on_exit_three(self) -> None:
        prepared = self.prepare("synthetic-review", "review")
        runner, _calls = self.fake_launchd_runner(prepared)
        waits = {"count": 0}

        def scheduler_sleep(_seconds):
            waits["count"] += 1
            if waits["count"] == 1:
                self.run_worker(prepared)

        with patch.object(drill, "process_absent", return_value=True):
            self.assertTrue(
                drill.execute_drill(
                    prepared,
                    timeout_seconds=30,
                    quiet_seconds=12,
                    runner=runner,
                    sleeper=scheduler_sleep,
                    monotonic=lambda: float(waits["count"]),
                )
            )
        result = json.loads((prepared.root / "result.json").read_bytes())
        self.assertEqual(result["observed_exit_statuses"], [0, 3])

    def test_bootstrap_failure_is_failed_evidence_and_still_checks_absence(self) -> None:
        prepared = self.prepare()
        runner, calls = self.fake_launchd_runner(prepared, bootstrap_ok=False)
        self.assertFalse(
            drill.execute_drill(
                prepared,
                timeout_seconds=30,
                quiet_seconds=12,
                runner=runner,
                sleeper=lambda _: None,
                monotonic=lambda: 0.0,
            )
        )
        result = json.loads((prepared.root / "result.json").read_bytes())
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(
            [call[1] for call in calls if call[0] == str(drill.SYSTEM_LAUNCHCTL)],
            ["print", "bootstrap", "bootout", "print"],
        )

    def test_bootstrap_exception_still_attempts_exact_bootout(self) -> None:
        prepared = self.prepare()
        base_runner, calls = self.fake_launchd_runner(prepared)

        def runner(args, timeout):
            if args[1] == "bootstrap":
                raise drill.DrillError("synthetic bootstrap timeout")
            return base_runner(args, timeout)

        self.assertFalse(
            drill.execute_drill(
                prepared,
                timeout_seconds=30,
                quiet_seconds=12,
                runner=runner,
                sleeper=lambda _: None,
                monotonic=lambda: 0.0,
            )
        )
        self.assertIn("bootout", [call[1] for call in calls])
        self.assertTrue((prepared.root / "manifest.sha256").is_file())

    def test_cleanup_failure_is_not_success_and_prints_exact_manual_command(self) -> None:
        prepared = self.prepare()
        runner, _calls = self.fake_launchd_runner(prepared, cleanup_ok=False)
        ticks = {"value": 0}

        def advancing_sleep(_seconds):
            ticks["value"] += 20

        with patch("builtins.print") as printed:
            self.assertFalse(
                drill.execute_drill(
                    prepared,
                    timeout_seconds=30,
                    quiet_seconds=12,
                    runner=runner,
                    sleeper=advancing_sleep,
                    monotonic=lambda: float(ticks["value"]),
                )
            )
        self.assertTrue(
            any("Manual cleanup required" in str(call) for call in printed.call_args_list)
        )
        self.assertTrue((prepared.root / "CLEANUP_REQUIRED.json").is_file())
        self.assertFalse((prepared.root / "manifest.sha256").exists())

    def test_interruption_during_quiet_window_preserves_cleanup_required_evidence(self) -> None:
        prepared = self.prepare()
        runner, calls = self.fake_launchd_runner(prepared)
        waits = {"count": 0}

        def interrupted_quiet_window(_seconds):
            waits["count"] += 1
            if waits["count"] in {1, 2}:
                self.run_worker(prepared)
            else:
                raise drill.DrillInterrupted("synthetic cleanup interruption")

        with patch.object(drill, "process_absent", return_value=True):
            self.assertFalse(
                drill.execute_drill(
                    prepared,
                    timeout_seconds=30,
                    quiet_seconds=12,
                    runner=runner,
                    sleeper=interrupted_quiet_window,
                    monotonic=lambda: float(waits["count"]),
                )
            )
        self.assertIn("bootout", [call[1] for call in calls])
        cleanup = json.loads((prepared.root / "CLEANUP_REQUIRED.json").read_bytes())
        self.assertTrue(cleanup["cleanup_ok"])
        self.assertFalse(cleanup["quiet_window_ok"])
        self.assertFalse((prepared.root / "manifest.sha256").exists())

    def test_running_worker_lock_prevents_quiet_cleanup_proof(self) -> None:
        prepared = self.prepare()
        runner, _calls = self.fake_launchd_runner(prepared, bootstrap_ok=False)

        def leave_running_lock(_seconds):
            lock = prepared.root / ".synthetic-worker.lock"
            lock.write_bytes(b"")
            lock.chmod(0o600)

        self.assertFalse(
            drill.execute_drill(
                prepared,
                timeout_seconds=30,
                quiet_seconds=12,
                runner=runner,
                sleeper=leave_running_lock,
                monotonic=lambda: 0.0,
            )
        )
        self.assertTrue((prepared.root / "CLEANUP_REQUIRED.json").is_file())
        self.assertFalse((prepared.root / "manifest.sha256").exists())

    def test_missing_terminal_times_out_then_cleans_up_and_records_failure(self) -> None:
        prepared = self.prepare()
        runner, calls = self.fake_launchd_runner(prepared)
        ticks = {"value": 0.0}

        def advancing_sleep(seconds):
            ticks["value"] += max(float(seconds), 31.0)

        with patch.object(drill, "process_absent", return_value=True):
            self.assertFalse(
                drill.execute_drill(
                    prepared,
                    timeout_seconds=30,
                    quiet_seconds=12,
                    runner=runner,
                    sleeper=advancing_sleep,
                    monotonic=lambda: ticks["value"],
                )
            )
        self.assertIn("bootout", [call[1] for call in calls])
        result = json.loads((prepared.root / "result.json").read_bytes())
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("terminal record missing", result["detail"])
        self.assertTrue(drill.verify_manifest(prepared.root))

    def test_tampered_terminal_binding_prevents_passed_result(self) -> None:
        prepared = self.prepare()
        for _ in prepared.expected_outcomes:
            self.run_worker(prepared)
        terminal = json.loads((prepared.root / "terminal.json").read_bytes())
        terminal["event_sha256"] = "0" * 64
        (prepared.root / "terminal.json").write_bytes(drill.canonical(terminal))
        drill.finalize_evidence(
            prepared,
            status="PASSED",
            cleanup_ok=True,
            quiet_ok=True,
            loaded_ok=True,
            detail="synthetic",
        )
        result = json.loads((prepared.root / "result.json").read_bytes())
        self.assertFalse(result["terminal_record_ok"])
        self.assertEqual(result["status"], "FAILED")

    def test_finalization_cannot_pass_without_loaded_state(self) -> None:
        prepared = self.prepare()
        for _ in prepared.expected_outcomes:
            self.run_worker(prepared)
        drill.finalize_evidence(
            prepared,
            status="PASSED",
            cleanup_ok=True,
            quiet_ok=True,
            loaded_ok=False,
            detail="synthetic",
        )
        result = json.loads((prepared.root / "result.json").read_bytes())
        self.assertEqual(result["status"], "FAILED")

    def test_evidence_tamper_and_unmanifested_file_are_rejected(self) -> None:
        prepared = self.prepare()
        for _ in prepared.expected_outcomes:
            self.run_worker(prepared)
        drill.finalize_evidence(
            prepared,
            status="PASSED",
            cleanup_ok=True,
            quiet_ok=True,
            loaded_ok=True,
            detail="synthetic",
        )
        self.assertTrue(drill.verify_manifest(prepared.root))
        (prepared.root / "extra.txt").write_text("extra\n")
        (prepared.root / "extra.txt").chmod(0o600)
        with self.assertRaisesRegex(drill.DrillError, "coverage"):
            drill.verify_manifest(prepared.root)
        (prepared.root / "extra.txt").unlink()
        with (prepared.root / "result.json").open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaises(drill.DrillError):
            drill.verify_manifest(prepared.root)

    def test_sanitized_package_omits_paths_pids_logs_and_environment(self) -> None:
        prepared = self.prepare()
        for _ in prepared.expected_outcomes:
            self.run_worker(prepared)
        self.add_lifecycle_evidence(prepared)
        (prepared.root / "launchctl-loaded.txt").write_text(str(self.repo))
        drill.finalize_evidence(
            prepared,
            status="PASSED",
            cleanup_ok=True,
            quiet_ok=True,
            loaded_ok=True,
            detail="terminal synthetic outcome observed",
        )
        output = self.root / "sanitized"
        output.mkdir(mode=0o700)
        destination = drill.sanitize_evidence(prepared.root, output)
        self.assertTrue(drill.verify_manifest(destination))
        self.assertNotIn("launchctl-loaded.txt", {p.name for p in destination.iterdir()})
        combined = b"".join(path.read_bytes() for path in destination.rglob("*") if path.is_file())
        self.assertNotIn(str(self.repo).encode(), combined)
        self.assertNotIn(str(os.getpid()).encode(), combined)
        redacted_plist = plistlib.loads((destination / "launch-agent.plist").read_bytes())
        self.assertEqual(redacted_plist["WorkingDirectory"], "<REPOSITORY>")
        self.assertEqual(redacted_plist["ProgramArguments"][4], "<DRILL_ROOT>")

    def test_sanitizer_rejects_manifested_plist_path_substitution(self) -> None:
        prepared = self.prepare()
        for _ in prepared.expected_outcomes:
            self.run_worker(prepared)
        self.add_lifecycle_evidence(prepared)
        drill.finalize_evidence(
            prepared,
            status="PASSED",
            cleanup_ok=True,
            quiet_ok=True,
            loaded_ok=True,
            detail="terminal synthetic outcome observed",
        )
        plist = plistlib.loads(prepared.plist.read_bytes())
        plist["WorkingDirectory"] = "/private/unrelated"
        prepared.plist.write_bytes(
            plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True)
        )
        (prepared.root / "manifest.sha256").unlink()
        drill.publish_manifest(prepared.root)
        output = self.root / "sanitized-tamper"
        output.mkdir(mode=0o700)
        with self.assertRaisesRegex(drill.DrillError, "synthetic argv"):
            drill.sanitize_evidence(prepared.root, output)

    def test_sanitizer_redacts_nonstandard_failure_detail(self) -> None:
        prepared = self.prepare()
        self.run_worker(prepared)
        self.add_lifecycle_evidence(prepared)
        drill.finalize_evidence(
            prepared,
            status="FAILED",
            cleanup_ok=True,
            quiet_ok=True,
            loaded_ok=True,
            detail=f"failure at {self.repo}",
        )
        output = self.root / "sanitized-failure"
        output.mkdir(mode=0o700)
        destination = drill.sanitize_evidence(prepared.root, output)
        result = json.loads((destination / "result.json").read_bytes())
        self.assertEqual(result["detail"], "<REDACTED_FAILURE_DETAIL>")
        combined = b"".join(
            path.read_bytes() for path in destination.rglob("*") if path.is_file()
        )
        self.assertNotIn(str(self.repo).encode(), combined)

    def test_command_uses_absolute_argv_empty_environment_and_bounds_output(self) -> None:
        completed = subprocess.CompletedProcess(["/bin/example"], 0, b"ok", b"")
        with patch.object(drill.subprocess, "run", return_value=completed) as called:
            result = drill.command(["/bin/example", "literal"], 9)
        self.assertEqual(result.stdout, b"ok")
        kwargs = called.call_args.kwargs
        self.assertEqual(kwargs["env"], {})
        self.assertEqual(kwargs["timeout"], 9)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        with self.assertRaisesRegex(drill.DrillError, "absolute executable"):
            drill.command(["launchctl", "print", "value"])

    def test_label_absence_requires_specific_message(self) -> None:
        self.assertTrue(
            drill.label_absent(
                drill.CommandResult(113, b"", b"Could not find service\n")
            )
        )
        self.assertFalse(drill.label_absent(drill.CommandResult(1, b"", b"permission denied")))
        self.assertFalse(drill.label_absent(drill.CommandResult(0, b"loaded", b"")))

    def test_run_parser_requires_explicit_launch_agent_flag(self) -> None:
        with self.assertRaises(SystemExit):
            drill.parser().parse_args(
                [
                    "run",
                    "--repository",
                    str(self.repo),
                    "--expected-commit",
                    self.commit,
                    "--python",
                    str(self.python),
                    "--drill-root",
                    str(self.drill_parent / "synthetic-001-complete"),
                ]
            )

    def test_invalid_identifiers_paths_and_plist_values_fail_closed(self) -> None:
        with self.assertRaises(drill.DrillError):
            self.prepare("bad")
        with self.assertRaises(drill.DrillError):
            drill.production_plist(
                label="wrong",
                python=self.python,
                repository=self.repo,
                season="2026-27",
                gameweek=4,
                stdout_path=self.repo / "data/out",
                stderr_path=self.repo / "data/err",
            )
        outside = self.root / "outside"
        outside.mkdir(mode=0o700)
        with patch.object(drill.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(drill.DrillError, "exact ignored"):
                drill.prepare_drill(
                    repository=self.repo,
                    expected_commit=self.commit,
                    python=self.python,
                    drill_parent=outside,
                    drill_id="synthetic-001",
                    scenario="complete",
                )


if __name__ == "__main__":
    unittest.main()
