from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "completion_monitor_production_schedule.py"
)
SPEC = importlib.util.spec_from_file_location(
    "completion_monitor_production_schedule", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
schedule = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = schedule
SPEC.loader.exec_module(schedule)


class ProductionScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "repository"
        self.repository.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Synthetic Test")
        self.git("config", "user.email", "synthetic@example.invalid")
        (self.repository / ".gitignore").write_text("/data/\n")
        controller = self.repository / schedule.SCRIPT_RELATIVE
        controller.parent.mkdir()
        shutil.copyfile(SCRIPT, controller)
        controller.chmod(0o644)
        (self.repository / "tracked.txt").write_text("synthetic\n")
        self.data = self.repository / "data"
        self.roots = {}
        for name in (
            "raw",
            "clean",
            "features",
            "predictions",
            "evaluations",
            "monitor-control",
        ):
            path = self.data / name
            path.mkdir(parents=True)
            self.roots[name] = path
        self.schedule_parent = self.data / "operations" / "schedules"
        self.schedule_parent.mkdir(parents=True, mode=0o700)
        self.schedule_parent.chmod(0o700)
        self.git("add", ".gitignore", "scripts", "tracked.txt")
        self.git("commit", "-qm", "synthetic base")
        self.commit = self.git("rev-parse", "HEAD").stdout.strip()
        self.launch_agents = self.root / "Library" / "LaunchAgents"
        self.launch_agents.mkdir(parents=True)
        self.created = datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc)
        self.not_before = self.created + timedelta(hours=1)
        self.expires = self.created + timedelta(days=2)
        self.now = self.created + timedelta(hours=2)
        self.id_counter = 0
        self.home_patch = patch.object(
            schedule, "user_launch_agents", return_value=self.launch_agents
        )
        self.home_patch.start()

    def tearDown(self) -> None:
        self.home_patch.stop()
        self.temporary.cleanup()

    def git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def next_id(self, now: datetime) -> str:
        self.id_counter += 1
        prefix = schedule.iso_utc(now).replace(":", "").replace("-", "")
        return f"{prefix}-{self.id_counter:032x}"

    def prepare(
        self,
        schedule_id: str = "schedule-test-001",
        *,
        prediction: str | None = None,
        allowed_untracked: set[str] | None = None,
        clock=None,
    ) -> schedule.PreparedSchedule:
        with patch.object(schedule.platform, "system", return_value="Darwin"):
            return schedule.prepare_schedule(
                repository=self.repository,
                expected_commit=self.commit,
                python=Path(sys.executable),
                schedule_parent=self.schedule_parent,
                schedule_id=schedule_id,
                season="2026-27",
                gameweek=4,
                prediction_snapshot_timestamp=prediction,
                raw_data_root=self.roots["raw"],
                clean_data_root=self.roots["clean"],
                feature_data_root=self.roots["features"],
                prediction_data_root=self.roots["predictions"],
                evaluation_data_root=self.roots["evaluations"],
                task028b_control_data_root=self.roots["monitor-control"],
                not_before=schedule.iso_utc(self.not_before),
                expires_at=schedule.iso_utc(self.expires),
                allowed_untracked=allowed_untracked or set(),
                clock=clock or (lambda: self.created),
            )

    def execute(
        self,
        prepared: schedule.PreparedSchedule,
        call: schedule.MonitorCall | None = None,
        *,
        now: datetime | None = None,
        monitor_runner=None,
        after_monitor=None,
        runner=schedule.command,
    ) -> schedule.ExecutionResult:
        selected = monitor_runner or (lambda _plan: call or schedule.MonitorCall(status="WAITING"))
        return schedule.execute_once(
            prepared.plan_path,
            prepared.plan_hash_path,
            clock=lambda: now or self.now,
            monitor_runner=selected,
            runner=runner,
            id_factory=self.next_id,
            after_monitor=after_monitor,
            activation_validator=lambda _plan, _path, _sha: None,
        )

    def rewrite_hashed_json(self, path: Path, value: dict[str, object]) -> None:
        body = schedule.canonical(value)
        path.write_bytes(body)
        path.chmod(0o600)
        digest = path.with_name(path.name + ".sha256")
        digest.write_bytes(schedule.hash_body(path, body))
        digest.chmod(0o600)

    def composite_runner(self, service_handler):
        def runner(arguments, timeout):
            if arguments[0] == str(schedule.SYSTEM_GIT):
                return schedule.command(arguments, timeout)
            return service_handler(list(arguments), timeout)

        return runner

    def test_prepare_creates_canonical_private_plan_and_controller_plist(self) -> None:
        prepared = self.prepare()
        plan, digest = schedule.load_plan(prepared.plan_path, prepared.plan_hash_path)
        self.assertEqual(plan["expected_commit"], self.commit)
        self.assertEqual(plan["label"], "com.fpl-decision-engine.completion-monitor.2026-27.gw4")
        self.assertEqual(plan["interval_seconds"], 900)
        self.assertEqual(plan["python_version"], platform.python_version())
        self.assertEqual(digest, schedule.sha256_file(prepared.plan_path))
        self.assertEqual(prepared.root.stat().st_mode & 0o777, 0o700)
        for path in prepared.root.rglob("*"):
            if path.is_file():
                self.assertEqual(path.stat().st_mode & 0o077, 0)
        plist = plistlib.loads(prepared.plist_path.read_bytes())
        self.assertEqual(set(plist), schedule.PLIST_KEYS)
        self.assertEqual(plist["StartInterval"], 900)
        self.assertEqual(plist["Umask"], "077")
        self.assertFalse(plist["KeepAlive"])
        self.assertEqual(
            plist["ProgramArguments"],
            [
                str(Path(sys.executable)),
                "-I",
                str(self.repository / schedule.SCRIPT_RELATIVE),
                "run",
                "--plan",
                str(prepared.plan_path),
                "--plan-sha256-file",
                str(prepared.plan_hash_path),
            ],
        )
        for forbidden in ("RunAtLoad", "EnvironmentVariables", "WatchPaths", "QueueDirectories"):
            self.assertNotIn(forbidden, plist)

    def test_prepare_rejects_invalid_time_bounds_prediction_and_reuse(self) -> None:
        with self.assertRaisesRegex(schedule.ScheduleError, "timezone-aware clock"):
            self.prepare(
                "schedule-naive-clock",
                clock=lambda: self.created.replace(tzinfo=None),
            )
        with patch.object(schedule.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(schedule.ScheduleError, "lifetime"):
                schedule.prepare_schedule(
                    repository=self.repository,
                    expected_commit=self.commit,
                    python=Path(sys.executable),
                    schedule_parent=self.schedule_parent,
                    schedule_id="schedule-long-window",
                    season="2026-27",
                    gameweek=4,
                    prediction_snapshot_timestamp=None,
                    raw_data_root=self.roots["raw"],
                    clean_data_root=self.roots["clean"],
                    feature_data_root=self.roots["features"],
                    prediction_data_root=self.roots["predictions"],
                    evaluation_data_root=self.roots["evaluations"],
                    task028b_control_data_root=self.roots["monitor-control"],
                    not_before=schedule.iso_utc(self.not_before),
                    expires_at=schedule.iso_utc(self.created + timedelta(days=15)),
                    allowed_untracked=set(),
                    clock=lambda: self.created,
                )
        prepared = self.prepare("schedule-reuse-001")
        with self.assertRaisesRegex(schedule.ScheduleError, "already exists"):
            self.prepare("schedule-reuse-001")
        plan, _ = schedule.load_plan(prepared.plan_path, prepared.plan_hash_path)
        plan["prediction_snapshot_timestamp"] = "latest"
        self.rewrite_hashed_json(prepared.plan_path, plan)
        with self.assertRaisesRegex(schedule.ScheduleError, "prediction"):
            schedule.load_plan(prepared.plan_path, prepared.plan_hash_path)

    def test_waiting_not_yet_due_retryable_and_terminal_mappings(self) -> None:
        waiting = self.prepare("schedule-waiting")
        result = self.execute(waiting, schedule.MonitorCall(status="WAITING"))
        self.assertEqual((result.status, result.exit_status), ("ACTIVE_WAITING", 0))
        self.assertFalse((waiting.root / schedule.TERMINAL_NAME).exists())

        before = self.prepare("schedule-not-due")
        calls = []
        result = self.execute(
            before,
            now=self.created + timedelta(minutes=30),
            monitor_runner=lambda plan: calls.append(plan),
        )
        self.assertEqual(result.status, "ACTIVE_NOT_YET_DUE")
        self.assertFalse(result.engine_called)
        self.assertEqual(calls, [])

        for index, error in enumerate(("RetryableProbeError", "MonitorLockedError"), 1):
            prepared = self.prepare(f"schedule-retry-{index}")
            result = self.execute(prepared, schedule.MonitorCall(error_class=error))
            self.assertEqual((result.status, result.exit_status), ("ACTIVE_RETRYABLE", 2))
            self.assertFalse((prepared.root / schedule.TERMINAL_NAME).exists())

        realized = self.prepare("schedule-realized")
        result = self.execute(
            realized,
            schedule.MonitorCall(
                status="REALIZED_COMPLETE",
                realized_snapshot_timestamp="20260910T020000.000000Z",
            ),
        )
        self.assertEqual(result.status, "TERMINAL_REALIZED_COMPLETE")

        predicted = self.prepare(
            "schedule-complete", prediction="20260909T120000.000000Z"
        )
        evaluation = self.roots["evaluations"] / "synthetic-evaluation"
        evaluation.mkdir()
        result = self.execute(
            predicted,
            schedule.MonitorCall(
                status="COMPLETE",
                realized_snapshot_timestamp="20260910T020000.000000Z",
                evaluation_directory=evaluation,
            ),
        )
        self.assertEqual(result.status, "TERMINAL_COMPLETE")

        review = self.prepare("schedule-review")
        result = self.execute(review, schedule.MonitorCall(status="REVIEW_REQUIRED"))
        self.assertEqual((result.status, result.exit_status), ("TERMINAL_REVIEW_REQUIRED", 3))

    def test_mismatched_unknown_and_nonretryable_results_stop_terminally(self) -> None:
        cases = [
            ("schedule-mismatch-realized", "20260909T120000.000000Z", schedule.MonitorCall(status="REALIZED_COMPLETE")),
            ("schedule-mismatch-complete", None, schedule.MonitorCall(status="COMPLETE")),
            ("schedule-unknown-status", None, schedule.MonitorCall(status="REFRESHING")),
            ("schedule-known-error", None, schedule.MonitorCall(error_class="CompletionMonitorError")),
        ]
        for schedule_id, prediction, call in cases:
            with self.subTest(schedule_id=schedule_id):
                prepared = self.prepare(schedule_id, prediction=prediction)
                result = self.execute(prepared, call)
                self.assertEqual((result.status, result.exit_status), ("TERMINAL_REVIEW_REQUIRED", 3))
                terminal = schedule.validate_terminal(
                    prepared.root,
                    *schedule.load_plan(prepared.plan_path, prepared.plan_hash_path),
                )
                self.assertEqual(terminal["schedule_status"], "TERMINAL_REVIEW_REQUIRED")

        exceptional = self.prepare("schedule-unknown-exception")
        result = self.execute(
            exceptional,
            monitor_runner=lambda _plan: (_ for _ in ()).throw(RuntimeError("private detail")),
        )
        self.assertEqual(result.status, "TERMINAL_REVIEW_REQUIRED")
        combined = b"".join(path.read_bytes() for path in exceptional.root.rglob("*") if path.is_file())
        self.assertNotIn(b"private detail", combined)

    def test_success_status_requires_complete_typed_payload(self) -> None:
        cases = (
            schedule.MonitorCall(status="REALIZED_COMPLETE"),
            schedule.MonitorCall(
                status="REALIZED_COMPLETE",
                realized_snapshot_timestamp="not-a-snapshot",
            ),
            schedule.MonitorCall(
                status="COMPLETE",
                realized_snapshot_timestamp="20260910T020000.000000Z",
            ),
            schedule.MonitorCall(status=["WAITING"]),
            schedule.MonitorCall(
                status="REALIZED_COMPLETE",
                realized_snapshot_timestamp=123,
            ),
            schedule.MonitorCall(
                status="COMPLETE",
                realized_snapshot_timestamp="20260910T020000.000000Z",
                evaluation_directory="not-a-path",
            ),
            schedule.MonitorCall(error_class=[]),
            object(),
        )
        for index, call in enumerate(cases, 1):
            with self.subTest(index=index):
                prepared = self.prepare(
                    f"schedule-payload-{index}",
                    prediction=(
                        "20260909T120000.000000Z"
                        if index == 3
                        else None
                    ),
                )
                result = self.execute(
                    prepared,
                    monitor_runner=lambda _plan, value=call: value,
                )
                self.assertEqual(
                    (result.status, result.exit_status),
                    ("TERMINAL_REVIEW_REQUIRED", 3),
                )

    def test_expiry_is_terminal_without_engine_call(self) -> None:
        prepared = self.prepare("schedule-expired")
        calls = []
        result = self.execute(
            prepared,
            now=self.expires,
            monitor_runner=lambda plan: calls.append(plan),
        )
        self.assertEqual((result.status, result.exit_status), ("TERMINAL_REVIEW_REQUIRED", 3))
        self.assertFalse(result.engine_called)
        self.assertEqual(calls, [])

        boundary = self.prepare("schedule-exact-boundary")
        observed = []
        result = self.execute(
            boundary,
            now=self.not_before,
            monitor_runner=lambda plan: (
                observed.append(plan) or schedule.MonitorCall(status="WAITING")
            ),
        )
        self.assertEqual(result.status, "ACTIVE_WAITING")
        self.assertEqual(len(observed), 1)

    def test_engine_run_requires_active_evidence_and_exact_installed_plist(self) -> None:
        prepared = self.prepare("schedule-run-authorization")
        calls = []
        with self.assertRaisesRegex(schedule.ScheduleError, "activation evidence"):
            schedule.execute_once(
                prepared.plan_path,
                prepared.plan_hash_path,
                clock=lambda: self.now,
                monitor_runner=lambda plan: calls.append(plan),
                id_factory=self.next_id,
            )
        self.assertEqual(calls, [])
        plan, plan_sha = schedule.load_plan(prepared.plan_path, prepared.plan_hash_path)
        installed = Path(str(plan["installed_plist_path"]))
        schedule.publish(installed, prepared.plist_path.read_bytes())
        schedule.publish_lifecycle(
            prepared.root,
            plan,
            plan_sha,
            event_type="ACTIVATION",
            status="ACTIVE",
            results=[],
            observed_at=self.now,
            id_factory=self.next_id,
        )
        result = schedule.execute_once(
            prepared.plan_path,
            prepared.plan_hash_path,
            clock=lambda: self.now,
            monitor_runner=lambda plan: schedule.MonitorCall(status="WAITING"),
            id_factory=self.next_id,
        )
        self.assertEqual(result.status, "ACTIVE_WAITING")
        schedule.publish_lifecycle(
            prepared.root,
            plan,
            plan_sha,
            event_type="ACTIVATION",
            status="REVIEW_REQUIRED",
            results=[],
            observed_at=self.now + timedelta(seconds=1),
            id_factory=self.next_id,
        )
        with self.assertRaisesRegex(schedule.ScheduleError, "not active"):
            schedule.execute_once(
                prepared.plan_path,
                prepared.plan_hash_path,
                clock=lambda: self.now + timedelta(seconds=1),
                monitor_runner=lambda _plan: self.fail("engine called after review"),
                id_factory=self.next_id,
            )

    def test_valid_terminal_makes_every_later_run_engine_and_preflight_inert(self) -> None:
        prepared = self.prepare("schedule-quiescent")
        self.execute(
            prepared,
            schedule.MonitorCall(
                status="REALIZED_COMPLETE",
                realized_snapshot_timestamp="20260910T020000.000000Z",
            ),
        )
        (self.repository / "tracked.txt").write_text("changed\n")

        def forbidden(*_args, **_kwargs):
            raise AssertionError("terminal run crossed an inert boundary")

        result = self.execute(
            prepared,
            monitor_runner=forbidden,
            runner=forbidden,
        )
        self.assertEqual((result.status, result.engine_called), ("TERMINAL_QUIESCENT", False))
        self.assertEqual(len(list((prepared.root / schedule.INVOCATION_DIRECTORY).glob("*.json"))), 1)

    def test_partial_malformed_hash_corrupt_and_conflicting_terminal_block_engine(self) -> None:
        for suffix, mutation in (
            ("partial", "partial"),
            ("malformed", "malformed"),
            ("hash", "hash"),
            ("conflict", "conflict"),
        ):
            with self.subTest(mutation=mutation):
                prepared = self.prepare(f"schedule-terminal-{suffix}")
                if mutation == "partial":
                    schedule.publish(prepared.root / schedule.TERMINAL_NAME, b"{}")
                else:
                    self.execute(
                        prepared,
                        schedule.MonitorCall(
                            status="REALIZED_COMPLETE",
                            realized_snapshot_timestamp="20260910T020000.000000Z",
                        ),
                    )
                    terminal_path = prepared.root / schedule.TERMINAL_NAME
                    if mutation == "malformed":
                        self.rewrite_hashed_json(terminal_path, {"bad": True})
                    elif mutation == "hash":
                        terminal_path.write_bytes(terminal_path.read_bytes() + b"x")
                    else:
                        terminal = json.loads(terminal_path.read_bytes())
                        terminal["schedule_status"] = "TERMINAL_REVIEW_REQUIRED"
                        self.rewrite_hashed_json(terminal_path, terminal)
                calls = []
                with self.assertRaises(schedule.ScheduleError):
                    self.execute(prepared, monitor_runner=lambda plan: calls.append(plan))
                self.assertEqual(calls, [])

    def test_non_string_evidence_fields_report_review_without_traceback(self) -> None:
        def assert_verify_review(prepared: schedule.PreparedSchedule) -> None:
            with patch("builtins.print") as printed:
                result = schedule.main(
                    [
                        "verify",
                        "--plan",
                        str(prepared.plan_path),
                        "--plan-sha256-file",
                        str(prepared.plan_hash_path),
                    ]
                )
            self.assertEqual(result, 3)
            self.assertEqual(json.loads(printed.call_args.args[0])["status"], "REVIEW_REQUIRED")

        invocation_cases = (
            ("schedule_status", []),
            ("process_exit_class", {}),
            ("error_class", []),
            ("task028b_status", {}),
        )
        for index, (field, invalid) in enumerate(invocation_cases, 1):
            with self.subTest(record="invocation", field=field):
                prepared = self.prepare(f"schedule-bad-invocation-{index}")
                self.execute(prepared, schedule.MonitorCall(status="WAITING"))
                path = next(
                    (prepared.root / schedule.INVOCATION_DIRECTORY).glob("*.json")
                )
                value = json.loads(path.read_bytes())
                value[field] = invalid
                self.rewrite_hashed_json(path, value)
                assert_verify_review(prepared)

        for index, (field, invalid) in enumerate(
            (("event_type", 1), ("status", False)), 1
        ):
            with self.subTest(record="lifecycle", field=field):
                prepared = self.prepare(f"schedule-bad-lifecycle-{index}")
                plan, plan_sha = schedule.load_plan(
                    prepared.plan_path, prepared.plan_hash_path
                )
                event = schedule.publish_lifecycle(
                    prepared.root,
                    plan,
                    plan_sha,
                    event_type="ACTIVATION",
                    status="ACTIVE",
                    results=[],
                    observed_at=self.now,
                    id_factory=self.next_id,
                )
                path = (
                    prepared.root
                    / schedule.LIFECYCLE_DIRECTORY
                    / f"{event['event_id']}.json"
                )
                value = json.loads(path.read_bytes())
                value[field] = invalid
                self.rewrite_hashed_json(path, value)
                assert_verify_review(prepared)

        prepared = self.prepare("schedule-bad-terminal-status")
        self.execute(
            prepared,
            schedule.MonitorCall(
                status="REALIZED_COMPLETE",
                realized_snapshot_timestamp="20260910T020000.000000Z",
            ),
        )
        path = prepared.root / schedule.TERMINAL_NAME
        value = json.loads(path.read_bytes())
        value["schedule_status"] = []
        self.rewrite_hashed_json(path, value)
        assert_verify_review(prepared)

    def test_crashes_do_not_synthesize_success_and_reconciliation_can_finish(self) -> None:
        before = self.prepare("schedule-crash-before")
        calls = []
        with self.assertRaises(KeyboardInterrupt):
            self.execute(
                before,
                monitor_runner=lambda plan: calls.append(plan),
                runner=lambda _args, _timeout: (_ for _ in ()).throw(
                    KeyboardInterrupt()
                ),
            )
        self.assertEqual(calls, [])
        self.assertEqual(
            list((before.root / schedule.INVOCATION_DIRECTORY).iterdir()), []
        )

        during = self.prepare("schedule-crash-during")
        with self.assertRaises(KeyboardInterrupt):
            self.execute(
                during,
                monitor_runner=lambda _plan: (_ for _ in ()).throw(KeyboardInterrupt()),
            )
        self.assertEqual(list((during.root / schedule.INVOCATION_DIRECTORY).iterdir()), [])

        after = self.prepare("schedule-crash-after")
        with self.assertRaises(KeyboardInterrupt):
            self.execute(
                after,
                schedule.MonitorCall(
                    status="REALIZED_COMPLETE",
                    realized_snapshot_timestamp="20260910T020000.000000Z",
                ),
                after_monitor=lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
            )
        self.assertFalse((after.root / schedule.TERMINAL_NAME).exists())
        self.assertEqual(len(list((after.root / schedule.INVOCATION_DIRECTORY).glob("*.json"))), 1)
        result = self.execute(
            after,
            schedule.MonitorCall(
                status="REALIZED_COMPLETE",
                realized_snapshot_timestamp="20260910T020000.000000Z",
            ),
            now=self.now + timedelta(minutes=15),
        )
        self.assertEqual(result.status, "TERMINAL_REALIZED_COMPLETE")
        self.assertTrue((after.root / schedule.TERMINAL_NAME).exists())

    def test_clock_rollback_blocks_before_engine(self) -> None:
        prepared = self.prepare("schedule-clock-rollback")
        self.execute(prepared, schedule.MonitorCall(status="WAITING"), now=self.now)
        calls = []
        with self.assertRaisesRegex(schedule.ScheduleError, "clock moved backwards"):
            self.execute(
                prepared,
                now=self.now - timedelta(seconds=1),
                monitor_runner=lambda plan: calls.append(plan),
            )
        self.assertEqual(calls, [])

    def test_preflight_rejects_tracked_untracked_global_ignore_and_environment_drift(self) -> None:
        dirty = self.prepare("schedule-dirty")
        (self.repository / "tracked.txt").write_text("dirty\n")
        with self.assertRaisesRegex(schedule.ScheduleError, "tracked or staged"):
            self.execute(dirty)
        self.git("checkout", "--", "tracked.txt")

        untracked = self.prepare("schedule-untracked")
        (self.repository / "surprise.txt").write_text("surprise\n")
        with self.assertRaisesRegex(schedule.ScheduleError, "unreviewed untracked"):
            self.execute(untracked)
        (self.repository / "surprise.txt").unlink()

        hidden = self.prepare("schedule-global-ignore")
        excludes = self.root / "global-excludes"
        excludes.write_text("hidden.txt\n")
        self.git("config", "core.excludesFile", str(excludes))
        (self.repository / "hidden.txt").write_text("hidden\n")
        with self.assertRaisesRegex(schedule.ScheduleError, "unreviewed untracked"):
            self.execute(hidden)
        (self.repository / "hidden.txt").unlink()
        self.git("config", "--unset", "core.excludesFile")

        drift = self.prepare("schedule-environment-drift")
        with patch.object(schedule, "distribution_inventory_sha256", return_value="0" * 64):
            with self.assertRaisesRegex(schedule.ScheduleError, "distribution inventory"):
                self.execute(drift)

        wrong_commit = self.prepare("schedule-wrong-commit")
        plan, _ = schedule.load_plan(
            wrong_commit.plan_path, wrong_commit.plan_hash_path
        )
        plan["expected_commit"] = "0" * 40
        self.rewrite_hashed_json(wrong_commit.plan_path, plan)
        with self.assertRaisesRegex(schedule.ScheduleError, "commit mismatch"):
            self.execute(wrong_commit)

        wrong_controller = self.prepare("schedule-wrong-controller")
        plan, _ = schedule.load_plan(
            wrong_controller.plan_path, wrong_controller.plan_hash_path
        )
        plan["controller_sha256"] = "0" * 64
        self.rewrite_hashed_json(wrong_controller.plan_path, plan)
        with self.assertRaisesRegex(schedule.ScheduleError, "controller bytes"):
            self.execute(wrong_controller)

    def test_plan_plist_permission_hardlink_symlink_and_path_tamper_fail_closed(self) -> None:
        mode = self.prepare("schedule-mode")
        mode.plan_path.chmod(0o644)
        with self.assertRaisesRegex(schedule.ScheduleError, "owner-only"):
            schedule.load_plan(mode.plan_path, mode.plan_hash_path)

        hardlink = self.prepare("schedule-hardlink")
        os.link(hardlink.plan_path, hardlink.root / "second-plan-link")
        with self.assertRaisesRegex(schedule.ScheduleError, "hard-linked"):
            schedule.load_plan(hardlink.plan_path, hardlink.plan_hash_path)

        linked = self.prepare("schedule-symlink")
        original = linked.root / "original-plan.json"
        linked.plan_path.rename(original)
        linked.plan_path.symlink_to(original)
        with self.assertRaisesRegex(schedule.ScheduleError, "symlink"):
            schedule.load_plan(linked.plan_path, linked.plan_hash_path)

        plist = self.prepare("schedule-plist-tamper")
        plist.plist_path.write_bytes(plist.plist_path.read_bytes() + b"changed")
        with self.assertRaisesRegex(schedule.ScheduleError, "hash mismatch"):
            self.execute(plist)

        escaped = self.prepare("schedule-path-escape")
        plan, _ = schedule.load_plan(escaped.plan_path, escaped.plan_hash_path)
        plan["evaluation_data_root"] = str(self.root)
        self.rewrite_hashed_json(escaped.plan_path, plan)
        with self.assertRaisesRegex(schedule.ScheduleError, "evaluation_data_root"):
            schedule.load_plan(escaped.plan_path, escaped.plan_hash_path)

    def test_no_overwrite_publication_and_unexpected_directory_entries_rejected(self) -> None:
        path = self.root / "immutable"
        schedule.publish(path, b"first")
        with self.assertRaisesRegex(schedule.ScheduleError, "overwrite"):
            schedule.publish(path, b"second")
        prepared = self.prepare("schedule-extra-invocation")
        extra = prepared.root / schedule.INVOCATION_DIRECTORY / ".DS_Store"
        extra.write_bytes(b"x")
        extra.chmod(0o600)
        plan, plan_sha = schedule.load_plan(prepared.plan_path, prepared.plan_hash_path)
        with self.assertRaisesRegex(schedule.ScheduleError, "unexpected invocation"):
            schedule.load_invocations(prepared.root, plan, plan_sha)

    def test_command_is_absolute_bounded_and_uses_empty_environment(self) -> None:
        completed = subprocess.CompletedProcess(["/bin/example"], 0, b"ok", b"")
        with patch.object(schedule.subprocess, "run", return_value=completed) as called:
            result = schedule.command(["/bin/example", "literal"], 7)
        self.assertEqual(result.stdout, b"ok")
        kwargs = called.call_args.kwargs
        self.assertEqual(kwargs["env"], {})
        self.assertEqual(kwargs["timeout"], 7)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        with self.assertRaisesRegex(schedule.ScheduleError, "absolute executable"):
            schedule.command(["launchctl", "print", "value"])
        oversized = subprocess.CompletedProcess(
            ["/bin/example"], 0, b"x" * (schedule.MAX_COMMAND_OUTPUT_BYTES + 1), b""
        )
        with patch.object(schedule.subprocess, "run", return_value=oversized):
            with self.assertRaisesRegex(schedule.ScheduleError, "output exceeded"):
                schedule.command(["/bin/example"])
        signaled = subprocess.CompletedProcess(["/bin/example"], -15, b"", b"")
        with patch.object(schedule.subprocess, "run", return_value=signaled):
            self.assertEqual(schedule.command(["/bin/example"]).returncode, -15)
        with patch.object(
            schedule.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["/bin/example"], 7),
        ):
            with self.assertRaisesRegex(schedule.ScheduleError, "bounded system command"):
                schedule.command(["/bin/example"], 7)

    def test_activation_status_deactivation_use_only_exact_label(self) -> None:
        prepared = self.prepare("schedule-lifecycle")
        plan, _ = schedule.load_plan(prepared.plan_path, prepared.plan_hash_path)
        calls = []
        print_count = {"value": 0}

        def activation_service(arguments, _timeout):
            calls.append(arguments)
            if arguments[0] == str(schedule.SYSTEM_PLUTIL):
                return schedule.CommandResult(0, b"ok", b"")
            action = arguments[1]
            if action == "print":
                print_count["value"] += 1
                if print_count["value"] == 1:
                    return schedule.CommandResult(113, b"", b"Could not find service")
                return schedule.CommandResult(0, b"loaded", b"")
            return schedule.CommandResult(0, b"", b"")

        with patch.object(schedule.platform, "system", return_value="Darwin"):
            event = schedule.activate(
                prepared.plan_path,
                prepared.plan_hash_path,
                execute=True,
                runner=self.composite_runner(activation_service),
                clock=lambda: self.now,
                id_factory=self.next_id,
            )
        self.assertEqual(event["status"], "ACTIVE")
        installed = Path(str(plan["installed_plist_path"]))
        self.assertTrue(installed.is_file())
        target = schedule.service_target(plan)
        launchctl_calls = [call for call in calls if call[0] == str(schedule.SYSTEM_LAUNCHCTL)]
        self.assertEqual([call[1] for call in launchctl_calls], ["print", "bootstrap", "print", "kickstart"])
        self.assertTrue(all(target in call or call[1] == "bootstrap" for call in launchctl_calls))

        def loaded_service(arguments, _timeout):
            if arguments[0] == str(schedule.SYSTEM_LAUNCHCTL):
                return schedule.CommandResult(0, b"loaded", b"")
            raise AssertionError(arguments)

        self.assertEqual(
            schedule.schedule_status(
                prepared.plan_path,
                prepared.plan_hash_path,
                runner=self.composite_runner(loaded_service),
                clock=lambda: self.created + timedelta(minutes=30),
            ),
            "ACTIVE_NOT_YET_DUE",
        )

        deactivation_calls = []
        deactivation_print = {"value": 0}

        def deactivation_service(arguments, _timeout):
            deactivation_calls.append(arguments)
            action = arguments[1]
            if action == "print":
                deactivation_print["value"] += 1
                if deactivation_print["value"] == 1:
                    return schedule.CommandResult(0, b"loaded", b"")
                return schedule.CommandResult(113, b"", b"Could not find service")
            return schedule.CommandResult(0, b"", b"")

        with patch.object(schedule.platform, "system", return_value="Darwin"):
            event = schedule.deactivate(
                prepared.plan_path,
                prepared.plan_hash_path,
                execute=True,
                remove_installed_plist=True,
                runner=self.composite_runner(deactivation_service),
                clock=lambda: self.now + timedelta(minutes=1),
                id_factory=self.next_id,
            )
        self.assertEqual(event["status"], "DEACTIVATED")
        self.assertFalse(installed.exists())
        self.assertEqual(
            [call[1] for call in deactivation_calls], ["print", "bootout", "print"]
        )

        absent_runner = self.composite_runner(
            lambda _args, _timeout: schedule.CommandResult(
                113, b"", b"Could not find service"
            )
        )
        self.assertEqual(
            schedule.schedule_status(
                prepared.plan_path, prepared.plan_hash_path, runner=absent_runner
            ),
            "DEACTIVATED",
        )

    def test_absent_label_with_installed_plist_is_review_required(self) -> None:
        prepared = self.prepare("schedule-plist-remains")
        plan, _ = schedule.load_plan(prepared.plan_path, prepared.plan_hash_path)
        installed = Path(str(plan["installed_plist_path"]))
        schedule.publish(installed, prepared.plist_path.read_bytes())
        absent_runner = self.composite_runner(
            lambda _args, _timeout: schedule.CommandResult(
                113, b"", b"Could not find service"
            )
        )
        self.assertEqual(
            schedule.schedule_status(
                prepared.plan_path, prepared.plan_hash_path, runner=absent_runner
            ),
            "REVIEW_REQUIRED",
        )

    def test_terminal_marker_without_verified_deactivation_is_not_not_activated(self) -> None:
        prepared = self.prepare("schedule-terminal-no-activation")
        self.execute(
            prepared,
            schedule.MonitorCall(
                status="REALIZED_COMPLETE",
                realized_snapshot_timestamp="20260910T020000.000000Z",
            ),
        )
        absent_runner = self.composite_runner(
            lambda _args, _timeout: schedule.CommandResult(
                113, b"", b"Could not find service"
            )
        )
        self.assertEqual(
            schedule.schedule_status(
                prepared.plan_path, prepared.plan_hash_path, runner=absent_runner
            ),
            "REVIEW_REQUIRED",
        )

    def test_activation_failure_attempts_exact_rollback_and_removes_its_plist(self) -> None:
        prepared = self.prepare("schedule-activation-failure")
        plan, _ = schedule.load_plan(prepared.plan_path, prepared.plan_hash_path)
        calls = []

        def service(arguments, _timeout):
            calls.append(arguments)
            if arguments[0] == str(schedule.SYSTEM_PLUTIL):
                return schedule.CommandResult(0, b"ok", b"")
            action = arguments[1]
            if action == "print":
                return schedule.CommandResult(113, b"", b"Could not find service")
            if action == "bootstrap":
                return schedule.CommandResult(1, b"", b"failed")
            return schedule.CommandResult(0, b"", b"")

        with patch.object(schedule.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(schedule.ScheduleError, "rollback verified"):
                schedule.activate(
                    prepared.plan_path,
                    prepared.plan_hash_path,
                    execute=True,
                    runner=self.composite_runner(service),
                    clock=lambda: self.now,
                    id_factory=self.next_id,
                )
        self.assertFalse(Path(str(plan["installed_plist_path"])).exists())
        actions = [call[1] for call in calls if call[0] == str(schedule.SYSTEM_LAUNCHCTL)]
        self.assertEqual(actions, ["print", "bootstrap", "bootout", "print"])

    def test_activation_conflict_and_uncertain_cleanup_fail_closed(self) -> None:
        conflict = self.prepare("schedule-activation-conflict")

        def loaded_service(arguments, _timeout):
            if arguments[0] == str(schedule.SYSTEM_LAUNCHCTL):
                return schedule.CommandResult(0, b"loaded", b"")
            return schedule.CommandResult(0, b"ok", b"")

        with patch.object(schedule.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(schedule.ScheduleError, "not provably absent"):
                schedule.activate(
                    conflict.plan_path,
                    conflict.plan_hash_path,
                    execute=True,
                    runner=self.composite_runner(loaded_service),
                    clock=lambda: self.now,
                )
        plan, _ = schedule.load_plan(conflict.plan_path, conflict.plan_hash_path)
        self.assertFalse(Path(str(plan["installed_plist_path"])).exists())

        uncertain = self.prepare("schedule-activation-uncertain")

        def uncertain_service(arguments, _timeout):
            if arguments[0] == str(schedule.SYSTEM_PLUTIL):
                return schedule.CommandResult(0, b"ok", b"")
            if arguments[1] == "print":
                return schedule.CommandResult(113, b"", b"Could not find service")
            if arguments[1] == "bootstrap":
                return schedule.CommandResult(1, b"", b"failed")
            if arguments[1] == "bootout":
                raise schedule.ScheduleError("synthetic uncertainty")
            raise AssertionError(arguments)

        with patch.object(schedule.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(schedule.ScheduleError, "cleanup requires"):
                schedule.activate(
                    uncertain.plan_path,
                    uncertain.plan_hash_path,
                    execute=True,
                    runner=self.composite_runner(uncertain_service),
                    clock=lambda: self.now,
                    id_factory=self.next_id,
                )
        plan, plan_sha = schedule.load_plan(
            uncertain.plan_path, uncertain.plan_hash_path
        )
        self.assertTrue(Path(str(plan["installed_plist_path"])).exists())
        events = schedule.load_lifecycle(uncertain.root, plan, plan_sha)
        self.assertTrue(any(event["status"] == "REVIEW_REQUIRED" for event in events))

    def test_installed_plist_symlink_and_deactivation_without_removal_are_rejected(self) -> None:
        prepared = self.prepare("schedule-installed-symlink")
        plan, plan_sha = schedule.load_plan(prepared.plan_path, prepared.plan_hash_path)
        installed = Path(str(plan["installed_plist_path"]))
        installed.symlink_to(prepared.plist_path)
        schedule.publish_lifecycle(
            prepared.root,
            plan,
            plan_sha,
            event_type="ACTIVATION",
            status="ACTIVE",
            results=[],
            observed_at=self.now,
            id_factory=self.next_id,
        )
        with self.assertRaisesRegex(schedule.ScheduleError, "symlink"):
            schedule.validate_active_authorization(
                plan, prepared.plan_path, plan_sha
            )
        installed.unlink()
        schedule.publish(installed, prepared.plist_path.read_bytes())

        print_count = {"value": 0}

        def service(arguments, _timeout):
            if arguments[1] == "print":
                print_count["value"] += 1
                if print_count["value"] == 1:
                    return schedule.CommandResult(0, b"loaded", b"")
                return schedule.CommandResult(113, b"", b"Could not find service")
            return schedule.CommandResult(0, b"", b"")

        with patch.object(schedule.platform, "system", return_value="Darwin"):
            with self.assertRaisesRegex(schedule.ScheduleError, "removal flag"):
                schedule.deactivate(
                    prepared.plan_path,
                    prepared.plan_hash_path,
                    execute=True,
                    remove_installed_plist=False,
                    runner=service,
                )
        self.assertTrue(installed.exists())

    def test_activation_and_deactivation_require_explicit_flags(self) -> None:
        prepared = self.prepare("schedule-explicit-flags")
        with self.assertRaisesRegex(schedule.ScheduleError, "activation flag"):
            schedule.activate(
                prepared.plan_path,
                prepared.plan_hash_path,
                execute=False,
            )
        with self.assertRaisesRegex(schedule.ScheduleError, "deactivation flag"):
            schedule.deactivate(
                prepared.plan_path,
                prepared.plan_hash_path,
                execute=False,
                remove_installed_plist=False,
            )

    def test_sanitization_redacts_paths_uid_and_untracked_names_and_has_manifest(self) -> None:
        prepared = self.prepare(
            "schedule-sanitize", prediction="20260909T120000.000000Z"
        )
        evaluation = self.roots["evaluations"] / "private-local-path"
        evaluation.mkdir()
        self.execute(
            prepared,
            schedule.MonitorCall(
                status="COMPLETE",
                realized_snapshot_timestamp="20260910T020000.000000Z",
                evaluation_directory=evaluation,
            ),
        )
        plan, plan_sha = schedule.load_plan(prepared.plan_path, prepared.plan_hash_path)
        schedule.publish_lifecycle(
            prepared.root,
            plan,
            plan_sha,
            event_type="ACTIVATION",
            status="ACTIVE",
            results=[schedule.CommandResult(0, b"private output", b"")],
            observed_at=self.now,
            id_factory=self.next_id,
        )
        output_parent = self.root / "review-output"
        output_parent.mkdir(mode=0o700)
        destination = schedule.sanitize_evidence(
            prepared.plan_path, prepared.plan_hash_path, output_parent
        )
        self.assertTrue(schedule.verify_manifest(destination))
        combined = b"".join(path.read_bytes() for path in destination.rglob("*") if path.is_file())
        self.assertNotIn(str(self.repository).encode(), combined)
        self.assertNotIn(b"private output", combined)
        self.assertNotIn(b"private-local-path", combined)
        redacted_plan = json.loads(
            (destination / "schedule-plan.redacted.json").read_bytes()
        )
        self.assertEqual(redacted_plan["uid"], "<UID>")
        redacted_lifecycle = json.loads(
            (destination / "lifecycle.redacted.json").read_bytes()
        )
        self.assertEqual(redacted_lifecycle[0]["uid"], "<UID>")
        summary = json.loads((destination / "source-inventory-summary.json").read_bytes())
        self.assertEqual(summary["plan_sha256"], plan_sha)

    def test_sanitizer_rejects_repository_destination_and_detects_source_change(self) -> None:
        prepared = self.prepare("schedule-sanitize-failure")
        with self.assertRaisesRegex(schedule.ScheduleError, "outside repository"):
            schedule.sanitize_evidence(
                prepared.plan_path,
                prepared.plan_hash_path,
                self.schedule_parent,
            )
        output_parent = self.root / "output"
        output_parent.mkdir(mode=0o700)
        original = schedule.source_inventory
        count = {"value": 0}

        def changing(root):
            count["value"] += 1
            result = original(root)
            if count["value"] == 1:
                (prepared.root / "worker.stdout.log").write_bytes(b"changed")
            return result

        with patch.object(schedule, "source_inventory", side_effect=changing):
            with self.assertRaisesRegex(schedule.ScheduleError, "changed during"):
                schedule.sanitize_evidence(
                    prepared.plan_path, prepared.plan_hash_path, output_parent
                )

    def test_manifest_rejects_tamper_and_unmanifested_file(self) -> None:
        prepared = self.prepare("schedule-manifest")
        output_parent = self.root / "manifest-output"
        output_parent.mkdir(mode=0o700)
        destination = schedule.sanitize_evidence(
            prepared.plan_path, prepared.plan_hash_path, output_parent
        )
        (destination / "extra").write_bytes(b"x")
        (destination / "extra").chmod(0o600)
        with self.assertRaisesRegex(schedule.ScheduleError, "coverage"):
            schedule.verify_manifest(destination)

    def test_controller_imports_engine_only_inside_lazy_call_and_duplicates_no_rules(self) -> None:
        source = SCRIPT.read_text()
        tree = ast.parse(source)
        engine_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("fpl_decision_engine"):
                engine_imports.append(node)
        self.assertEqual(len(engine_imports), 1)
        lazy_function = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "call_task028b"
        )
        self.assertIn(engine_imports[0], list(ast.walk(lazy_function)))
        for forbidden in (
            "refresh_fpl_data",
            "evaluate_xfp",
            "optimize_squad",
            "record_decision_journal_entry",
        ):
            self.assertNotIn(forbidden, source)

    def test_lazy_adapter_passes_only_exact_plan_values_and_maps_engine_errors(self) -> None:
        from fpl_decision_engine import completion_monitor

        prepared = self.prepare(
            "schedule-adapter", prediction="20260909T120000.000000Z"
        )
        plan, _ = schedule.load_plan(prepared.plan_path, prepared.plan_hash_path)
        evaluation = self.roots["evaluations"] / "adapter-result"
        with patch.object(
            completion_monitor,
            "monitor_completion",
            return_value=completion_monitor.MonitorOutcome(
                "COMPLETE",
                "synthetic",
                "20260910T020000.000000Z",
                evaluation,
            ),
        ) as called:
            result = schedule.call_task028b(plan)
        self.assertEqual(result.status, "COMPLETE")
        self.assertEqual(
            called.call_args.kwargs,
            {
                "season": "2026-27",
                "target_gameweek": 4,
                "prediction_snapshot_timestamp": "20260909T120000.000000Z",
                "raw_data_root": self.roots["raw"],
                "clean_data_root": self.roots["clean"],
                "feature_data_root": self.roots["features"],
                "prediction_data_root": self.roots["predictions"],
                "evaluation_data_root": self.roots["evaluations"],
                "control_data_root": self.roots["monitor-control"],
            },
        )
        with patch.object(
            completion_monitor,
            "monitor_completion",
            side_effect=completion_monitor.RetryableProbeError("synthetic"),
        ):
            self.assertEqual(
                schedule.call_task028b(plan).error_class,
                "RetryableProbeError",
            )
        error_cases = (
            (completion_monitor.MonitorLockedError("synthetic"), "MonitorLockedError"),
            (completion_monitor.CompletionMonitorError("synthetic"), "CompletionMonitorError"),
            (RuntimeError("synthetic"), "UnknownException"),
        )
        for error, expected in error_cases:
            with patch.object(
                completion_monitor, "monitor_completion", side_effect=error
            ):
                self.assertEqual(schedule.call_task028b(plan).error_class, expected)

    def test_main_run_is_silent_on_normal_outcome(self) -> None:
        prepared = self.prepare("schedule-main-silent")
        with patch.object(schedule, "execute_once", return_value=schedule.ExecutionResult("ACTIVE_WAITING", 0, True)):
            with patch("builtins.print") as printed:
                result = schedule.main(
                    [
                        "run",
                        "--plan",
                        str(prepared.plan_path),
                        "--plan-sha256-file",
                        str(prepared.plan_hash_path),
                    ]
                )
        self.assertEqual(result, 0)
        printed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
