from __future__ import annotations

import ast
import contextlib
from datetime import datetime, timedelta, timezone
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "completion_monitor_local_notification.py"
SPEC = importlib.util.spec_from_file_location("completion_monitor_local_notification", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
notify = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = notify
SPEC.loader.exec_module(notify)
SOURCE_SCRIPT = ROOT / "scripts" / "completion_monitor_production_schedule.py"
SOURCE_SPEC = importlib.util.spec_from_file_location("task028f_status_producer", SOURCE_SCRIPT)
assert SOURCE_SPEC is not None and SOURCE_SPEC.loader is not None
source_controller = importlib.util.module_from_spec(SOURCE_SPEC)
sys.modules[SOURCE_SPEC.name] = source_controller
SOURCE_SPEC.loader.exec_module(source_controller)


class NotificationObserverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name).resolve() / "repo"
        self.repository.mkdir(mode=0o700)
        (self.repository / "scripts").mkdir()
        (self.repository / notify.SCRIPT_RELATIVE).write_bytes(SCRIPT.read_bytes())
        (self.repository / notify.SOURCE_SCRIPT_RELATIVE).write_text("# synthetic source\n")
        self.source_sha = "a" * 64
        parent = self.repository / notify.PARENT_RELATIVE
        parent.mkdir(parents=True, mode=0o700)
        self.root = parent / self.source_sha
        self.root.mkdir(mode=0o700)
        (self.root / notify.ATTEMPT_DIRECTORY).mkdir(mode=0o700)
        (self.root / notify.LIFECYCLE_DIRECTORY).mkdir(mode=0o700)
        notify.publish(self.root / notify.LOCK_NAME, b"")
        self.launch_agents = Path(self.temporary.name).resolve() / "home" / "Library" / "LaunchAgents"
        self.launch_agents.mkdir(parents=True)
        self.user_launch_agents_patch = patch.object(
            notify, "user_launch_agents", side_effect=lambda _uid: self.launch_agents
        )
        self.user_launch_agents_patch.start()
        self.addCleanup(self.user_launch_agents_patch.stop)
        self.now = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
        self.counter = 0
        self.plan = self.make_plan()
        self.plan_path = self.root / notify.PLAN_NAME
        notify.publish_hashed(self.plan_path, notify.canonical(self.plan))
        plist = notify.plist_body(self.plan, self.plan_path)
        notify.publish_hashed(self.root / notify.PLIST_NAME, plist, max_bytes=notify.MAX_PLIST_BYTES)
        self.installed = Path(str(self.plan["installed_plist_path"]))
        notify.publish(self.installed, plist, max_bytes=notify.MAX_PLIST_BYTES)
        self.preflight_patch = patch.object(notify, "full_preflight", return_value=self.source_sha)
        self.preflight_patch.start()
        self.addCleanup(self.preflight_patch.stop)

    def make_plan(self) -> dict[str, object]:
        label = notify.observer_label(self.source_sha)
        source_plan = (
            self.repository / "data" / "operations" / "completion-monitor-schedules"
            / "synthetic" / "schedule-plan.json"
        )
        return {
            "acknowledged_untracked_paths": [],
            "created_at": notify.iso_utc(self.now),
            "observer_expected_commit": "b" * 40,
            "observer_repository": str(self.repository),
            "expires_at": notify.iso_utc(self.now + timedelta(days=2)),
            "format": notify.FORMAT,
            "installed_plist_path": str(self.launch_agents / f"{label}.plist"),
            "interval_seconds": notify.INTERVAL_SECONDS,
            "label": label,
            "not_before": notify.iso_utc(self.now),
            "notification_tool_path": str(self.repository / notify.SCRIPT_RELATIVE),
            "notification_tool_sha256": notify.sha256_bytes(
                (self.repository / notify.SCRIPT_RELATIVE).read_bytes()
            ),
            "observer_id": self.source_sha,
            "python_path": str(Path(sys.executable)),
            "python_resolved_path": str(Path(sys.executable).resolve()),
            "python_version": notify.platform.python_version(),
            "source_controller_path": str(self.repository / notify.SOURCE_SCRIPT_RELATIVE),
            "source_controller_sha256": notify.sha256_bytes(
                (self.repository / notify.SOURCE_SCRIPT_RELATIVE).read_bytes()
            ),
            "source_expected_commit": "c" * 40,
            "source_label": "com.fpl-decision-engine.completion-monitor.synthetic",
            "source_plan_path": str(source_plan),
            "source_plan_sha256": self.source_sha,
            "source_plan_sha256_path": str(source_plan.with_name(source_plan.name + ".sha256")),
            "source_python_path": str(Path(sys.executable)),
            "source_python_resolved_path": str(Path(sys.executable).resolve()),
            "source_python_version": notify.platform.python_version(),
            "source_repository": str(self.repository),
            "uid": os.getuid(),
        }

    def identity(self, _now: datetime | None = None) -> str:
        self.counter += 1
        return f"20260914T120000.000000Z-{self.counter:032x}"

    @staticmethod
    def status_result(status: str, *, detail: str | None = None) -> object:
        value = {"status": status}
        code = 0
        if detail is not None:
            value["detail"] = detail
        if status == "REVIEW_REQUIRED":
            code = 3
        return notify.CommandResult(code, json.dumps(value, sort_keys=True).encode() + b"\n", b"")

    def execute(self, runner, *, at: datetime | None = None, **kwargs):
        return notify.execute_once(
            self.plan_path,
            self.plan_path.with_name(self.plan_path.name + ".sha256"),
            runner=runner,
            clock=lambda: at or self.now,
            id_factory=self.identity,
            activation_validator=lambda _root, _plan, _sha: True,
            **kwargs,
        )

    def test_fixed_messages_and_no_shell_notification_command(self) -> None:
        terminal = notify.notification_command(notify.TERMINAL_MESSAGE)
        review = notify.notification_command(notify.REVIEW_MESSAGE)
        self.assertEqual(terminal[0], "/usr/bin/osascript")
        self.assertEqual(terminal[1], "-e")
        self.assertEqual(len(terminal), 3)
        self.assertIn(notify.TITLE, terminal[2])
        self.assertIn(notify.TERMINAL_MESSAGE, terminal[2])
        self.assertIn(notify.REVIEW_MESSAGE, review[2])
        self.assertIn(notify.TEST_MESSAGE, notify.notification_command(notify.TEST_MESSAGE)[2])
        with self.assertRaisesRegex(notify.NotificationError, "dynamic"):
            notify.notification_command("manager-specific text")

    def test_plist_is_exact_bounded_and_scheduled_output_is_dev_null(self) -> None:
        body = notify.read_hashed_bytes(self.root / notify.PLIST_NAME,
                                        max_bytes=notify.MAX_PLIST_BYTES)
        self.assertLessEqual(len(body), notify.MAX_PLIST_BYTES)
        value = plistlib.loads(body)
        self.assertEqual(set(value), notify.PLIST_KEYS)
        self.assertTrue(value["RunAtLoad"])
        self.assertFalse(value["KeepAlive"])
        self.assertEqual(value["StartInterval"], 900)
        self.assertEqual(value["StandardOutPath"], "/dev/null")
        self.assertEqual(value["StandardErrorPath"], "/dev/null")
        self.assertEqual(value["ProgramArguments"][1], "-I")
        self.assertNotIn("EnvironmentVariables", value)

    def test_source_status_accepts_every_exact_shape(self) -> None:
        for status in sorted(notify.SOURCE_STATUS - {"REVIEW_REQUIRED"}):
            self.assertEqual(notify.parse_source_status(self.status_result(status)), status)
        self.assertEqual(
            notify.parse_source_status(self.status_result("REVIEW_REQUIRED", detail="safe detail")),
            "REVIEW_REQUIRED",
        )
        self.assertEqual(
            notify.parse_source_status(self.status_result("REVIEW_REQUIRED")),
            "REVIEW_REQUIRED",
        )

    def test_real_task028f_cli_produces_the_three_accepted_encodings(self) -> None:
        arguments = [
            "status", "--plan", "/synthetic/plan",
            "--plan-sha256-file", "/synthetic/plan.sha256",
        ]
        cases = [
            ("ACTIVE_WAITING", None),
            ("REVIEW_REQUIRED", None),
            (None, source_controller.ScheduleError("bounded synthetic detail")),
        ]
        for status, error in cases:
            output = io.StringIO()
            replacement = (lambda *_args, value=status, **_kwargs: value)
            if error is not None:
                replacement = lambda *_args, value=error, **_kwargs: (_ for _ in ()).throw(value)
            with self.subTest(status=status, error=error), patch.object(
                source_controller, "schedule_status", side_effect=replacement
            ), contextlib.redirect_stdout(output):
                exit_code = source_controller.main(arguments)
            result = notify.CommandResult(exit_code, output.getvalue().encode(), b"")
            expected = status or "REVIEW_REQUIRED"
            self.assertEqual(notify.parse_source_status(result), expected)

    def test_source_status_rejects_hostile_or_noncanonical_output(self) -> None:
        cases = [
            notify.CommandResult(0, b'{"status":"ACTIVE_WAITING"}\n', b""),
            notify.CommandResult(0, b'{"status": "ACTIVE_WAITING", "status": "ACTIVE_WAITING"}\n', b""),
            notify.CommandResult(0, b'{"status": "UNKNOWN"}\n', b""),
            notify.CommandResult(3, b'{"status": "ACTIVE_WAITING"}\n', b""),
            notify.CommandResult(0, b'{"status": "ACTIVE_WAITING"}\n', b"noise"),
            notify.CommandResult(0, b'{"extra": 1, "status": "ACTIVE_WAITING"}\n', b""),
            notify.CommandResult(3, json.dumps({"detail": "x" * 513, "status": "REVIEW_REQUIRED"}, sort_keys=True).encode() + b"\n", b""),
            notify.CommandResult(2, b'{"status": "ACTIVE_RETRYABLE"}\n', b""),
            notify.CommandResult(0, b"x" * 4097, b""),
        ]
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(notify.NotificationError):
                    notify.parse_source_status(value)

    def test_active_source_states_are_quiet_and_write_nothing(self) -> None:
        for status in sorted(notify.ACTIVE_SOURCE):
            calls = []
            result = self.execute(lambda args, _timeout: calls.append(list(args)) or self.status_result(status))
            self.assertEqual((result.status, result.exit_status), ("ACTIVE_QUIET", 0))
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0], notify.source_status_command(self.plan))
            self.assertEqual(list((self.root / notify.ATTEMPT_DIRECTORY).iterdir()), [])

    def test_inactive_source_states_are_quiet_and_write_nothing(self) -> None:
        for status in sorted(notify.INACTIVE_SOURCE):
            result = self.execute(lambda _args, _timeout: self.status_result(status))
            self.assertEqual((result.status, result.exit_status), ("INACTIVE_SOURCE", 0))
            self.assertEqual(list((self.root / notify.ATTEMPT_DIRECTORY).iterdir()), [])

    def test_terminal_success_records_once_and_becomes_subprocess_quiescent(self) -> None:
        calls = []
        def runner(args, _timeout):
            calls.append(list(args))
            if args[0] == str(notify.SYSTEM_OSASCRIPT):
                return notify.CommandResult(0)
            return self.status_result("TERMINAL_QUIESCENT")
        result = self.execute(runner)
        self.assertEqual((result.status, result.exit_status), ("NOTIFICATION_QUIESCENT", 0))
        self.assertTrue(result.source_called and result.notification_called)
        self.assertEqual(calls[1], notify.notification_command(notify.TERMINAL_MESSAGE))
        self.assertEqual(notify.pair_state(self.root / notify.NOTIFIED_NAME), "complete")
        second_calls = []
        second = self.execute(lambda args, timeout: second_calls.append((args, timeout)))
        self.assertEqual(second.status, "NOTIFICATION_QUIESCENT")
        self.assertEqual(second_calls, [])

    def test_review_source_uses_only_fixed_scheduling_message_and_discards_detail(self) -> None:
        calls = []
        def runner(args, _timeout):
            calls.append(list(args))
            return (notify.CommandResult(0) if args[0] == str(notify.SYSTEM_OSASCRIPT)
                    else self.status_result("REVIEW_REQUIRED", detail="private-looking value"))
        result = self.execute(runner)
        self.assertEqual(result.status, "NOTIFICATION_QUIESCENT")
        self.assertEqual(calls[1], notify.notification_command(notify.REVIEW_MESSAGE))
        for path in self.root.rglob("*.json"):
            self.assertNotIn(b"private-looking value", path.read_bytes())

    def test_invalid_source_output_records_bounded_class_and_never_notifies(self) -> None:
        calls = []
        result = self.execute(
            lambda args, _timeout: calls.append(list(args))
            or notify.CommandResult(0, b"not-json\n", b"")
        )
        self.assertEqual((result.status, result.exit_status), ("REVIEW_REQUIRED", 3))
        self.assertEqual(len(calls), 1)
        files = list((self.root / notify.ATTEMPT_DIRECTORY).glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertIn(b"SOURCE_STATUS_INVALID", files[0].read_bytes())
        self.assertNotIn(b"not-json", files[0].read_bytes())

    def test_source_process_failure_is_retryable_without_notification(self) -> None:
        calls = []
        def runner(args, _timeout):
            calls.append(list(args))
            raise notify.ProcessNotStarted("synthetic")
        result = self.execute(runner)
        self.assertEqual((result.status, result.exit_status), ("ACTIVE_RETRYABLE", 2))
        self.assertTrue(result.source_called)
        self.assertEqual(len(calls), 1)

    def test_source_timeout_is_retryable_and_records_distinct_bounded_class(self) -> None:
        result = self.execute(
            lambda _args, _timeout: (_ for _ in ()).throw(notify.CommandTimedOut("synthetic"))
        )
        self.assertEqual((result.status, result.exit_status), ("ACTIVE_RETRYABLE", 2))
        record = next((self.root / notify.ATTEMPT_DIRECTORY).glob("*.json"))
        self.assertIn(b"SOURCE_STATUS_RETRYABLE", record.read_bytes())

    def test_notification_not_started_can_retry_with_new_claim(self) -> None:
        notification_calls = 0
        def first(args, _timeout):
            nonlocal notification_calls
            if args[0] == str(notify.SYSTEM_OSASCRIPT):
                notification_calls += 1
                raise notify.ProcessNotStarted("synthetic")
            return self.status_result("TERMINAL_QUIESCENT")
        first_result = self.execute(first)
        self.assertEqual((first_result.status, first_result.exit_status), ("ACTIVE_RETRYABLE", 2))
        def second(args, _timeout):
            nonlocal notification_calls
            if args[0] == str(notify.SYSTEM_OSASCRIPT):
                notification_calls += 1
                return notify.CommandResult(0)
            return self.status_result("TERMINAL_QUIESCENT")
        second_result = self.execute(second)
        self.assertEqual(second_result.status, "NOTIFICATION_QUIESCENT")
        self.assertEqual(notification_calls, 2)
        self.assertEqual(len(notify.validate_attempts(self.root, self.plan,
                                                       notify.sha256_bytes(notify.canonical(self.plan)))), 2)

    def test_notification_uncertainty_blocks_automatic_resend(self) -> None:
        notification_calls = 0
        def runner(args, _timeout):
            nonlocal notification_calls
            if args[0] == str(notify.SYSTEM_OSASCRIPT):
                notification_calls += 1
                return notify.CommandResult(1, b"", b"synthetic")
            return self.status_result("TERMINAL_QUIESCENT")
        first = self.execute(runner)
        self.assertEqual((first.status, first.exit_status), ("REVIEW_REQUIRED", 3))
        second = self.execute(runner)
        self.assertEqual((second.status, second.exit_status), ("REVIEW_REQUIRED", 3))
        self.assertEqual(notification_calls, 1)

    def test_unresolved_claim_blocks_every_subprocess(self) -> None:
        plan_sha = notify.sha256_bytes(notify.canonical(self.plan))
        notify.publish_claim(self.root, self.plan, plan_sha, "TERMINAL_QUIESCENT",
                             self.now, self.identity)
        calls = []
        result = self.execute(lambda args, timeout: calls.append((args, timeout)))
        self.assertEqual((result.status, result.exit_status), ("REVIEW_REQUIRED", 3))
        self.assertEqual(calls, [])

    def test_success_result_without_receipt_is_reconciled_without_resend(self) -> None:
        plan_sha = notify.sha256_bytes(notify.canonical(self.plan))
        attempt_id, claim, claim_path = notify.publish_claim(
            self.root, self.plan, plan_sha, "TERMINAL_QUIESCENT", self.now, self.identity
        )
        result, result_path = notify.publish_result(
            self.root, self.plan, plan_sha, attempt_id, claim,
            "COMMAND_ACCEPTED_NOT_VISIBLE_DELIVERY_PROOF", self.now,
        )
        self.assertTrue(claim_path.exists() and result_path.exists())
        calls = []
        outcome = self.execute(lambda args, timeout: calls.append((args, timeout)))
        self.assertEqual(outcome.status, "NOTIFICATION_QUIESCENT")
        self.assertEqual(calls, [])
        self.assertTrue((self.root / notify.NOTIFIED_NAME).exists())

    def test_partial_or_corrupt_receipt_fails_closed_without_subprocess(self) -> None:
        notify.publish(self.root / notify.NOTIFIED_NAME, b"{}")
        calls = []
        result = self.execute(lambda args, timeout: calls.append((args, timeout)))
        self.assertEqual((result.status, result.exit_status), ("REVIEW_REQUIRED", 3))
        self.assertEqual(calls, [])

    def test_expiry_and_clock_rollback_fail_closed(self) -> None:
        self.plan["expires_at"] = notify.iso_utc(self.now + timedelta(hours=1))
        self.rewrite_plan()
        result = self.execute(
            lambda _args, _timeout: self.fail("source called"),
            at=self.now + timedelta(hours=2),
        )
        self.assertEqual((result.status, result.exit_status), ("REVIEW_REQUIRED", 3))
        self.assertEqual(len(list((self.root / notify.ATTEMPT_DIRECTORY).glob("*.json"))), 1)
        # A backwards clock is rejected before source observation and without another record.
        self.plan["created_at"] = notify.iso_utc(self.now + timedelta(hours=1))
        self.plan["not_before"] = notify.iso_utc(self.now + timedelta(hours=1))
        self.plan["expires_at"] = notify.iso_utc(self.now + timedelta(days=1))
        self.rewrite_plan()
        result = self.execute(lambda _args, _timeout: self.fail("source called"))
        self.assertEqual(result.status, "REVIEW_REQUIRED")

    def test_real_lifecycle_gate_keeps_prepared_and_deactivated_observers_inert(self) -> None:
        calls = []
        result = notify.execute_once(
            self.plan_path,
            self.plan_path.with_name(self.plan_path.name + ".sha256"),
            runner=lambda args, timeout: calls.append((list(args), timeout)),
            clock=lambda: self.now,
            id_factory=self.identity,
        )
        self.assertEqual((result.status, result.exit_status), ("PREPARED_NOT_INSTALLED", 0))
        self.assertEqual(calls, [])
        self.assertEqual(list((self.root / notify.ATTEMPT_DIRECTORY).iterdir()), [])

        plan_sha = notify.sha256_bytes(notify.canonical(self.plan))
        notify.publish_lifecycle(
            self.root, self.plan, plan_sha, "DEACTIVATION", "DEACTIVATED", [],
            self.now, self.identity,
        )
        result = notify.execute_once(
            self.plan_path,
            self.plan_path.with_name(self.plan_path.name + ".sha256"),
            runner=lambda args, timeout: calls.append((list(args), timeout)),
            clock=lambda: self.now,
            id_factory=self.identity,
        )
        self.assertEqual((result.status, result.exit_status), ("DEACTIVATED", 0))
        self.assertEqual(calls, [])
        self.assertEqual(list((self.root / notify.ATTEMPT_DIRECTORY).iterdir()), [])

    def rewrite_plan(self) -> None:
        for path in (self.plan_path, self.plan_path.with_name(self.plan_path.name + ".sha256")):
            path.unlink()
        notify.publish_hashed(self.plan_path, notify.canonical(self.plan))

    def test_attempt_cap_stops_without_subprocess_or_write(self) -> None:
        plan_sha = notify.sha256_bytes(notify.canonical(self.plan))
        for index in range(notify.MAX_ATTEMPTS):
            attempt_id = f"20260914T120000.000000Z-{index + 1:032x}"
            value = {
                "attempt_id": attempt_id, "error_class": "SOURCE_STATUS_INVALID",
                "format": notify.FORMAT, "observed_at": notify.iso_utc(self.now),
                "plan_sha256": plan_sha, "record_type": "FAILURE",
                "source_plan_sha256": self.source_sha,
            }
            notify.publish_hashed(
                self.root / notify.ATTEMPT_DIRECTORY / f"{attempt_id}-failure.json",
                notify.canonical(value), max_bytes=notify.MAX_RECORD_BYTES,
            )
        before = {path.name for path in (self.root / notify.ATTEMPT_DIRECTORY).iterdir()}
        calls = []
        result = self.execute(lambda args, timeout: calls.append((args, timeout)))
        self.assertEqual((result.status, result.exit_status), ("REVIEW_REQUIRED", 3))
        self.assertEqual(calls, [])
        self.assertEqual(before, {path.name for path in (self.root / notify.ATTEMPT_DIRECTORY).iterdir()})

    def test_lock_loser_is_observer_busy_and_has_no_side_effect(self) -> None:
        first = notify.observer_lock(self.root / notify.LOCK_NAME)
        self.assertGreaterEqual(first, 0)
        calls = []
        try:
            result = self.execute(lambda args, timeout: calls.append((args, timeout)))
        finally:
            fcntl.flock(first, fcntl.LOCK_UN)
            os.close(first)
        self.assertEqual((result.status, result.exit_status), ("OBSERVER_BUSY", 2))
        self.assertEqual(calls, [])
        self.assertEqual(list((self.root / notify.ATTEMPT_DIRECTORY).iterdir()), [])

    def test_lock_releases_when_holder_process_exits(self) -> None:
        code = (
            "import fcntl, os, sys; "
            "f=open(sys.argv[1], 'r+b'); fcntl.flock(f, fcntl.LOCK_EX); os._exit(0)"
        )
        subprocess.run([sys.executable, "-c", code, str(self.root / notify.LOCK_NAME)], check=True)
        descriptor = notify.observer_lock(self.root / notify.LOCK_NAME)
        self.assertGreaterEqual(descriptor, 0)
        os.close(descriptor)

    def test_lock_rejects_hardlink_and_symlink(self) -> None:
        original = self.root / notify.LOCK_NAME
        linked = self.root / "hardlink.lock"
        os.link(original, linked)
        with self.assertRaisesRegex(notify.NotificationError, "hard-linked"):
            notify.observer_lock(original)
        linked.unlink()
        original.unlink()
        original.symlink_to("missing")
        with self.assertRaisesRegex(notify.NotificationError, "symlink"):
            notify.observer_lock(original)

    def test_record_ceiling_and_no_overwrite_are_enforced(self) -> None:
        path = self.root / notify.ATTEMPT_DIRECTORY / "oversize.json"
        with self.assertRaisesRegex(notify.NotificationError, "size limit"):
            notify.publish_hashed(path, b"x" * (notify.MAX_RECORD_BYTES + 1),
                                  max_bytes=notify.MAX_RECORD_BYTES)
        target = self.root / "one-time"
        notify.publish(target, b"first")
        with self.assertRaisesRegex(notify.NotificationError, "overwrite"):
            notify.publish(target, b"second")

    def test_plan_schema_hash_mode_symlink_and_path_tamper_fail_closed(self) -> None:
        original = dict(self.plan)
        cases = []
        missing = dict(original)
        missing.pop("label")
        cases.append(missing)
        extra = dict(original)
        extra["extra"] = True
        cases.append(extra)
        relative = dict(original)
        relative["source_plan_path"] = "relative.json"
        cases.append(relative)
        escaped = dict(original)
        escaped["source_repository"] = str(self.repository / "elsewhere")
        cases.append(escaped)
        for value in cases:
            with self.subTest(keys=set(value)):
                self.plan = value
                self.rewrite_plan()
                with self.assertRaises(notify.NotificationError):
                    notify.load_plan(
                        self.plan_path,
                        self.plan_path.with_name(self.plan_path.name + ".sha256"),
                    )
        self.plan = original
        self.rewrite_plan()
        self.plan_path.chmod(0o644)
        with self.assertRaisesRegex(notify.NotificationError, "owner-only"):
            notify.load_plan(self.plan_path, self.plan_path.with_name(self.plan_path.name + ".sha256"))
        self.plan_path.chmod(0o600)
        self.plan_path.with_name(self.plan_path.name + ".sha256").write_text("0" * 64)
        with self.assertRaisesRegex(notify.NotificationError, "hash mismatch"):
            notify.load_plan(self.plan_path, self.plan_path.with_name(self.plan_path.name + ".sha256"))

    def test_atomic_root_creation_has_exactly_one_concurrent_winner(self) -> None:
        root = self.root.parent / ("f" * 64)
        barrier = threading.Barrier(3)
        outcomes = []
        def worker() -> None:
            barrier.wait()
            try:
                notify.create_observer_root(root)
                outcomes.append("created")
            except notify.NotificationError:
                outcomes.append("blocked")
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()
        self.assertCountEqual(outcomes, ["created", "blocked"])
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    def test_sanitized_output_excludes_paths_uid_messages_and_command_output(self) -> None:
        output = Path(self.temporary.name).resolve() / "sanitized"
        output.mkdir(mode=0o700)
        destination = notify.sanitize_evidence(
            self.plan_path, self.plan_path.with_name(self.plan_path.name + ".sha256"), output
        )
        body = (destination / "summary.json").read_bytes()
        for forbidden in (
            str(self.repository).encode(), str(os.getuid()).encode(),
            notify.TERMINAL_MESSAGE.encode(), notify.REVIEW_MESSAGE.encode(),
            notify.TEST_MESSAGE.encode(), b"stdout", b"stderr",
        ):
            self.assertNotIn(forbidden, body)

    def test_test_notification_requires_explicit_flag_and_darwin(self) -> None:
        with self.assertRaises(notify.NotificationError):
            notify.test_notification(execute=False, runner=lambda _args, _timeout: self.fail())
        calls = []
        with patch.object(notify.platform, "system", return_value="Darwin"):
            result = notify.test_notification(
                execute=True,
                runner=lambda args, timeout: calls.append((list(args), timeout)) or notify.CommandResult(0),
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            calls,
            [
                ([str(notify.SYSTEM_LAUNCHCTL), "print", f"gui/{os.getuid()}"], 15),
                (notify.notification_command(notify.TEST_MESSAGE), 10),
            ],
        )

    def test_prepare_binds_distinct_source_and_observer_repositories_and_is_inert(self) -> None:
        source_repository = self.repository
        observer_repository = Path(self.temporary.name).resolve() / "observer-repo"
        (observer_repository / "scripts").mkdir(parents=True)
        (observer_repository / notify.SCRIPT_RELATIVE).write_bytes(SCRIPT.read_bytes())
        operations = source_repository / "data" / "operations"
        operations.chmod(0o700)
        source_root = operations / "completion-monitor-schedules" / "nested" / "prepare-source"
        source_root.mkdir(parents=True, mode=0o700)
        source_root.parent.chmod(0o700)
        source = {
            "acknowledged_untracked_paths": [],
            "clean_data_root": str(source_repository / "data" / "clean"),
            "controller_path": str(source_repository / notify.SOURCE_SCRIPT_RELATIVE),
            "controller_sha256": notify.sha256_bytes((source_repository / notify.SOURCE_SCRIPT_RELATIVE).read_bytes()),
            "created_at": notify.iso_utc(self.now - timedelta(days=1)),
            "evaluation_data_root": str(source_repository / "data" / "evaluation"),
            "expected_commit": "c" * 40,
            "expires_at": notify.iso_utc(self.now + timedelta(days=1)),
            "feature_data_root": str(source_repository / "data" / "features"),
            "format": notify.SOURCE_FORMAT,
            "gameweek": 4,
            "installed_plist_path": str(self.launch_agents / "source.plist"),
            "interval_seconds": 900,
            "label": "com.fpl-decision-engine.completion-monitor.2026-27.gw4",
            "not_before": notify.iso_utc(self.now - timedelta(hours=1)),
            "prediction_data_root": str(source_repository / "data" / "predictions"),
            "prediction_snapshot_timestamp": None,
            "python_distribution_inventory_sha256": "d" * 64,
            "python_path": str(Path(sys.executable)),
            "python_resolved_path": str(Path(sys.executable).resolve()),
            "python_version": notify.platform.python_version(),
            "raw_data_root": str(source_repository / "data" / "raw"),
            "repository": str(source_repository),
            "season": "2026-27",
            "task028b_control_data_root": str(source_repository / "data" / "control"),
            "uid": os.getuid(),
        }
        source_path = source_root / "schedule-plan.json"
        notify.publish_hashed(source_path, notify.canonical(source))
        observer_commit = "e" * 40
        calls = []
        def runner(args, _timeout):
            calls.append(list(args))
            if "check-ignore" in args:
                return notify.CommandResult(0)
            if "rev-parse" in args:
                return notify.CommandResult(0, (observer_commit + "\n").encode())
            if "status" in args:
                return notify.CommandResult(0, b"")
            if "show" in args:
                if str(observer_repository) in args:
                    return notify.CommandResult(0, (observer_repository / notify.SCRIPT_RELATIVE).read_bytes())
                return notify.CommandResult(0, (source_repository / notify.SOURCE_SCRIPT_RELATIVE).read_bytes())
            self.fail(f"unexpected command: {args}")
        with patch.object(notify.platform, "system", return_value="Darwin"):
            prepared = notify.prepare_observer(
                source_plan_path=source_path,
                source_plan_hash_path=source_path.with_name(source_path.name + ".sha256"),
                observer_repository=observer_repository,
                observer_expected_commit=observer_commit,
                python=Path(sys.executable),
                expires_at=notify.iso_utc(self.now + timedelta(days=2)),
                allowed_untracked=set(), clock=lambda: self.now, runner=runner,
            )
        plan, _ = notify.load_plan(prepared.plan_path, prepared.plan_hash_path)
        self.assertEqual(plan["source_repository"], str(source_repository))
        self.assertEqual(plan["observer_repository"], str(observer_repository))
        self.assertEqual(plan["source_expected_commit"], source["expected_commit"])
        self.assertEqual(plan["observer_expected_commit"], observer_commit)
        self.assertTrue(all(call[0] == str(notify.SYSTEM_GIT) for call in calls))
        outside_root = source_repository / "outside-data"
        outside_root.mkdir()
        outside_path = outside_root / "schedule-plan.json"
        notify.publish_hashed(outside_path, notify.canonical(source))
        with self.assertRaisesRegex(notify.NotificationError, "outside repository data"):
            notify.read_source_plan(
                outside_path, outside_path.with_name(outside_path.name + ".sha256")
            )
        with patch.object(notify.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(notify.NotificationError, "already exists"):
                notify.prepare_observer(
                    source_plan_path=source_path,
                    source_plan_hash_path=source_path.with_name(source_path.name + ".sha256"),
                    observer_repository=observer_repository,
                    observer_expected_commit=observer_commit,
                    python=Path(sys.executable),
                    expires_at=notify.iso_utc(self.now + timedelta(days=2)),
                    allowed_untracked=set(), clock=lambda: self.now, runner=runner,
                )

    def test_prepare_rejects_source_margin_and_maximum_lifetime(self) -> None:
        (self.repository / "data" / "operations").chmod(0o700)
        source = {
            "repository": str(self.repository),
            "expires_at": notify.iso_utc(self.now + timedelta(days=1)),
        }
        source_path = self.repository / "data" / "source" / "schedule-plan.json"
        source_hash_path = source_path.with_name(source_path.name + ".sha256")
        common = {
            "source_plan_path": source_path,
            "source_plan_hash_path": source_hash_path,
            "observer_repository": self.repository,
            "observer_expected_commit": "b" * 40,
            "python": Path(sys.executable),
            "allowed_untracked": set(),
            "clock": lambda: self.now,
            "runner": lambda _args, _timeout: self.fail("Git called before time rejection"),
        }
        with patch.object(notify.platform, "system", return_value="Darwin"), \
                patch.object(notify, "read_source_plan", return_value=(source, b"{}", self.source_sha)), \
                patch.object(notify, "verify_ignored"):
            with self.assertRaisesRegex(notify.NotificationError, "lacks source margin"):
                notify.prepare_observer(
                    **common,
                    expires_at=notify.iso_utc(
                        self.now + timedelta(days=1) + notify.SOURCE_EXPIRY_MARGIN
                        - timedelta(seconds=1)
                    ),
                )
            with self.assertRaisesRegex(notify.NotificationError, "lifetime too long"):
                notify.prepare_observer(
                    **common,
                    expires_at=notify.iso_utc(self.now + notify.MAX_PLAN_LIFETIME + timedelta(seconds=1)),
                )

    def test_git_and_controller_preflights_reject_unsafe_state(self) -> None:
        plan = dict(self.plan)

        with self.assertRaisesRegex(notify.NotificationError, "not ignored"):
            notify.verify_ignored(
                self.repository,
                self.repository / notify.PARENT_RELATIVE,
                lambda _args, _timeout: notify.CommandResult(1),
            )

        def observer_runner(status: bytes, committed: bytes | None = None):
            def run(args, _timeout):
                if "rev-parse" in args:
                    return notify.CommandResult(0, (str(plan["observer_expected_commit"]) + "\n").encode())
                if "status" in args:
                    return notify.CommandResult(0, status)
                if "show" in args:
                    body = ((self.repository / notify.SCRIPT_RELATIVE).read_bytes()
                            if committed is None else committed)
                    return notify.CommandResult(0, body)
                self.fail(f"unexpected command: {args}")
            return run

        with self.assertRaisesRegex(notify.NotificationError, "tracked or staged"):
            notify.repository_preflight(plan, observer_runner(b" M tracked.py\0"))
        with self.assertRaisesRegex(notify.NotificationError, "unreviewed untracked"):
            notify.repository_preflight(plan, observer_runner(b"?? surprise.txt\0"))
        with self.assertRaisesRegex(notify.NotificationError, "differs from reviewed commit"):
            notify.repository_preflight(plan, observer_runner(b"", b"different observer bytes\n"))

        def source_runner(args, _timeout):
            self.assertIn("show", args)
            return notify.CommandResult(0, b"different source bytes\n")
        with self.assertRaisesRegex(notify.NotificationError, "differs from reviewed source commit"):
            notify.source_controller_preflight(plan, source_runner)

    def test_activation_uses_only_observer_label_and_publishes_active_after_load(self) -> None:
        self.installed.unlink()
        calls = []
        target = notify.service_target(self.plan)
        def runner(args, _timeout):
            calls.append(list(args))
            if args[:2] == [str(notify.SYSTEM_PLUTIL), "-lint"]:
                return notify.CommandResult(0)
            if args[:2] == [str(notify.SYSTEM_LAUNCHCTL), "print"]:
                return (notify.CommandResult(1, b"", b"Could not find service")
                        if len([c for c in calls if c[:2] == args[:2]]) == 1
                        else notify.CommandResult(0))
            return notify.CommandResult(0)
        with patch.object(notify.platform, "system", return_value="Darwin"):
            event = notify.activate(
                self.plan_path, self.plan_path.with_name(self.plan_path.name + ".sha256"),
                execute=True, runner=runner, clock=lambda: self.now, id_factory=self.identity,
            )
        self.assertEqual(event["status"], "ACTIVE")
        self.assertTrue(self.installed.exists())
        self.assertTrue(all(self.plan["source_label"] not in " ".join(call) for call in calls))
        self.assertIn([str(notify.SYSTEM_LAUNCHCTL), "kickstart", target], calls)

    def test_activation_failure_rolls_back_exact_observer_and_requires_review(self) -> None:
        self.installed.unlink()
        target = notify.service_target(self.plan)
        print_count = 0
        def runner(args, _timeout):
            nonlocal print_count
            if args[:2] == [str(notify.SYSTEM_PLUTIL), "-lint"]:
                return notify.CommandResult(0)
            if args[:2] == [str(notify.SYSTEM_LAUNCHCTL), "print"]:
                print_count += 1
                if print_count in {1, 3}:
                    return notify.CommandResult(1, b"", b"Could not find service")
                return notify.CommandResult(0)
            if args[:2] == [str(notify.SYSTEM_LAUNCHCTL), "kickstart"]:
                return notify.CommandResult(1)
            return notify.CommandResult(0)
        with patch.object(notify.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(notify.NotificationError, "rollback verified"):
                notify.activate(
                    self.plan_path,
                    self.plan_path.with_name(self.plan_path.name + ".sha256"),
                    execute=True, runner=runner, clock=lambda: self.now,
                    id_factory=self.identity,
                )
        self.assertFalse(self.installed.exists())
        self.assertEqual(notify.lifecycle_state(
            self.root, self.plan, notify.sha256_bytes(notify.canonical(self.plan))
        ), "REVIEW_REQUIRED")
        self.assertNotIn(self.plan["source_label"], target)

    def test_deactivation_touches_only_exact_observer_label_and_plist(self) -> None:
        plan_sha = notify.sha256_bytes(notify.canonical(self.plan))
        notify.publish_lifecycle(
            self.root, self.plan, plan_sha, "ACTIVATION", "ACTIVE", [], self.now,
            self.identity,
        )
        calls = []
        target = notify.service_target(self.plan)
        print_count = 0
        def runner(args, _timeout):
            nonlocal print_count
            calls.append(list(args))
            if args[:2] == [str(notify.SYSTEM_LAUNCHCTL), "print"]:
                print_count += 1
                return (notify.CommandResult(0) if print_count == 1
                        else notify.CommandResult(1, b"", b"Could not find service"))
            return notify.CommandResult(0)
        with patch.object(notify.platform, "system", return_value="Darwin"):
            event = notify.deactivate(
                self.plan_path, self.plan_path.with_name(self.plan_path.name + ".sha256"),
                execute=True, remove_installed_plist=True, runner=runner,
                clock=lambda: self.now, id_factory=self.identity,
            )
        self.assertEqual(event["status"], "DEACTIVATED")
        self.assertFalse(self.installed.exists())
        self.assertEqual(calls[1], [str(notify.SYSTEM_LAUNCHCTL), "bootout", target])
        self.assertTrue(all(self.plan["source_label"] not in " ".join(call) for call in calls))

    def test_deactivation_requires_explicit_installed_plist_removal(self) -> None:
        calls = []
        absent = notify.CommandResult(1, b"", b"Could not find service")
        with patch.object(notify.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(notify.NotificationError, "removal flag required"):
                notify.deactivate(
                    self.plan_path,
                    self.plan_path.with_name(self.plan_path.name + ".sha256"),
                    execute=True,
                    remove_installed_plist=False,
                    runner=lambda args, timeout: calls.append((list(args), timeout)) or absent,
                    clock=lambda: self.now,
                    id_factory=self.identity,
                )
        self.assertTrue(self.installed.exists())
        self.assertEqual(len(calls), 2)
        self.assertEqual(list((self.root / notify.LIFECYCLE_DIRECTORY).iterdir()), [])

    def test_ast_has_no_network_ai_codex_or_shell_pathway(self) -> None:
        tree = ast.parse(SCRIPT.read_text())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "shell":
                        self.assertIs(keyword.value.value, False)
        self.assertTrue(imports.isdisjoint({"httpx", "requests", "urllib", "socket", "openai", "anthropic"}))
        text = SCRIPT.read_text()
        self.assertNotIn("codex", text.casefold())
        self.assertNotIn("shell=True", text)
        self.assertNotIn("API_KEY", text)


if __name__ == "__main__":
    unittest.main()
