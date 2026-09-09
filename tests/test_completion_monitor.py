from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import URLError

from fpl_decision_engine.__main__ import build_parser
from fpl_decision_engine.completion_monitor import (
    COMPLETE,
    REALIZED_COMPLETE,
    REVIEW_REQUIRED,
    WAITING,
    CompletionMonitorError,
    MonitorLockedError,
    RetryableProbeError,
    _acquire_lock,
    monitor_completion,
    probe_gameweek_completion,
    reset_completion_monitor,
    unlock_completion_monitor,
)
from fpl_decision_engine.evaluation import EvaluationError, EvaluationOutputs
from fpl_decision_engine.official_data import FPL_ELEMENT_SUMMARY_URL, FPL_FIXTURES_URL
from fpl_decision_engine.pipeline import FPL_BOOTSTRAP_STATIC_URL
from fpl_decision_engine.refresh import (
    RefreshError,
    refresh_fpl_data,
    validate_completed_refresh_snapshot,
)


def json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class RoutingOpener:
    def __init__(self, routes: dict[str, bytes | BaseException]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def __call__(self, url: str, *, timeout: float) -> FakeResponse:
        self.calls.append(url)
        value = self.routes[url]
        if isinstance(value, BaseException):
            raise value
        return FakeResponse(value)


def player(player_id: int = 1) -> dict[str, object]:
    return {
        "id": player_id,
        "code": 1000 + player_id,
        "opta_code": f"p{1000 + player_id}",
        "first_name": "Test",
        "second_name": str(player_id),
        "web_name": f"Player {player_id}",
        "team": 1,
        "element_type": 2,
        "now_cost": 50,
        "selected_by_percent": "1.0",
        "status": "a",
        "chance_of_playing_next_round": None,
        "news": "",
        "minutes": 90,
        "starts": 1,
        "total_points": 8,
        "event_points": 8,
        "points_per_game": "8.0",
        "form": "8.0",
        "bonus": 2,
        "bps": 30,
        "expected_goals": "0.25",
        "expected_assists": "0.05",
        "expected_goal_involvements": "0.30",
        "expected_goals_conceded": "0.40",
        "expected_goals_per_90": 0.25,
        "expected_assists_per_90": 0.05,
        "expected_goal_involvements_per_90": 0.30,
        "expected_goals_conceded_per_90": 0.40,
        "clearances_blocks_interceptions": 5,
        "recoveries": 3,
        "tackles": 2,
        "defensive_contribution": 7,
        "defensive_contribution_per_90": 7.0,
        "penalties_order": None,
        "direct_freekicks_order": None,
        "corners_and_indirect_freekicks_order": None,
        "ep_this": "8.0",
        "ep_next": "4.0",
    }


def bootstrap(*, finished: bool = True, checked: bool = True) -> dict[str, object]:
    return {
        "elements": [player()],
        "teams": [
            {"id": 1, "name": "Home", "short_name": "HOM"},
            {"id": 2, "name": "Away", "short_name": "AWY"},
        ],
        "element_types": [{"id": 2, "singular_name": "Defender"}],
        "events": [
            {
                "id": 1,
                "name": "Gameweek 1",
                "deadline_time": "2026-08-20T17:30:00Z",
                "finished": finished,
                "data_checked": checked,
                "is_current": False,
                "is_next": False,
            }
        ],
    }


def fixture(*, finished: bool = True) -> dict[str, object]:
    return {
        "id": 10,
        "code": 10010,
        "event": 1,
        "kickoff_time": "2026-08-21T19:00:00Z",
        "started": True,
        "finished": finished,
        "finished_provisional": finished,
        "provisional_start_time": False,
        "minutes": 90,
        "team_h": 1,
        "team_a": 2,
        "team_h_score": 2,
        "team_a_score": 0,
        "team_h_difficulty": 2,
        "team_a_difficulty": 4,
        "pulse_id": 500,
    }


def history_record() -> dict[str, object]:
    return {
        "element": 1,
        "fixture": 10,
        "opponent_team": 2,
        "total_points": 8,
        "was_home": True,
        "kickoff_time": "2026-08-21T19:00:00Z",
        "team_h_score": 2,
        "team_a_score": 0,
        "round": 1,
        "modified": False,
        "minutes": 90,
        "goals_scored": 1,
        "assists": 0,
        "clean_sheets": 1,
        "goals_conceded": 0,
        "own_goals": 0,
        "penalties_saved": 0,
        "penalties_missed": 0,
        "yellow_cards": 0,
        "red_cards": 0,
        "saves": 0,
        "bonus": 2,
        "bps": 30,
        "influence": "20.0",
        "creativity": "1.5",
        "threat": "30.0",
        "ict_index": "5.2",
        "clearances_blocks_interceptions": 5,
        "recoveries": 3,
        "tackles": 2,
        "defensive_contribution": 7,
        "starts": 1,
        "expected_goals": "0.25",
        "expected_assists": "0.05",
        "expected_goal_involvements": "0.30",
        "expected_goals_conceded": "0.40",
        "value": 50,
        "transfers_balance": 10,
        "selected": 1000,
        "transfers_in": 20,
        "transfers_out": 10,
    }


def history_body() -> bytes:
    return json_bytes(
        {"fixtures": [], "history": [history_record()], "history_past": []}
    )


class CompletionMonitorTests(unittest.TestCase):
    season = "2026-27"
    first = datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc)

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.raw = self.root / "raw"
        self.clean = self.root / "clean"
        self.features = self.root / "features"
        self.predictions = self.root / "predictions"
        self.evaluations = self.root / "evaluations"
        self.control = self.root / "operations/completion-monitor/fpl"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def routes(
        self,
        *,
        bootstrap_payload: dict[str, object] | None = None,
        fixture_payload: list[dict[str, object]] | None = None,
    ) -> dict[str, bytes | BaseException]:
        return {
            FPL_BOOTSTRAP_STATIC_URL: json_bytes(
                bootstrap_payload if bootstrap_payload is not None else bootstrap()
            ),
            FPL_FIXTURES_URL: json_bytes(
                fixture_payload if fixture_payload is not None else [fixture()]
            ),
            FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=1): history_body(),
        }

    def monitor(
        self,
        now: datetime,
        opener: RoutingOpener,
        **overrides: object,
    ):
        arguments = {
            "season": self.season,
            "target_gameweek": 1,
            "raw_data_root": self.raw,
            "clean_data_root": self.clean,
            "feature_data_root": self.features,
            "prediction_data_root": self.predictions,
            "evaluation_data_root": self.evaluations,
            "control_data_root": self.control,
            "attempts": 1,
            "history_delay_seconds": 0,
            "opener": opener,
            "sleeper": lambda seconds: None,
            "clock": lambda: now,
        }
        arguments.update(overrides)
        return monitor_completion(**arguments)

    def create_realized_snapshot(self, now: datetime | None = None):
        fixed = now or self.first
        return refresh_fpl_data(
            raw_data_root=self.raw,
            clean_data_root=self.clean,
            season=self.season,
            attempts=1,
            history_delay_seconds=0,
            bootstrap_opener=RoutingOpener(self.routes()),
            official_opener=RoutingOpener(self.routes()),
            clock=lambda: fixed,
            sleeper=lambda seconds: None,
        )

    def test_probe_waits_without_persisting_response_bodies(self) -> None:
        opener = RoutingOpener(
            self.routes(bootstrap_payload=bootstrap(finished=False, checked=False))
        )
        probe = probe_gameweek_completion(
            target_gameweek=1,
            opener=opener,
            attempts=1,
            clock=lambda: self.first,
            sleeper=lambda seconds: None,
        )
        self.assertFalse(probe.ready)
        self.assertEqual(probe.fixture_count, 1)
        self.assertEqual(
            opener.calls, [FPL_BOOTSTRAP_STATIC_URL, FPL_FIXTURES_URL]
        )

    def test_monitor_state_contains_status_metadata_but_not_probe_bodies(self) -> None:
        self.monitor(
            self.first,
            RoutingOpener(
                self.routes(bootstrap_payload=bootstrap(finished=False, checked=False))
            ),
        )
        state_body = (
            self.control / self.season / "gameweek=1/state.json"
        ).read_text()
        self.assertIn('"bootstrap_sha256"', state_body)
        self.assertNotIn("Player 1", state_body)
        self.assertNotIn('"elements"', state_body)

    def test_each_readiness_gate_refuses_early_refresh(self) -> None:
        before_deadline = datetime(2026, 8, 20, 17, 0, tzinfo=timezone.utc)
        cases = (
            (bootstrap(finished=False), [fixture()], self.first),
            (bootstrap(checked=False), [fixture()], self.first),
            (bootstrap(), [fixture(finished=False)], self.first),
            (bootstrap(), [fixture()], before_deadline),
        )
        for bootstrap_payload, fixture_payload, now in cases:
            with self.subTest(now=now, fixture_finished=fixture_payload[0]["finished"]):
                probe = probe_gameweek_completion(
                    target_gameweek=1,
                    opener=RoutingOpener(
                        self.routes(
                            bootstrap_payload=bootstrap_payload,
                            fixture_payload=fixture_payload,
                        )
                    ),
                    attempts=1,
                    clock=lambda now=now: now,
                    sleeper=lambda seconds: None,
                )
                self.assertFalse(probe.ready)
        with self.assertRaisesRegex(CompletionMonitorError, "no target fixtures"):
            probe_gameweek_completion(
                target_gameweek=1,
                opener=RoutingOpener(self.routes(fixture_payload=[])),
                attempts=1,
                clock=lambda: self.first,
                sleeper=lambda seconds: None,
            )

    def test_semantic_digest_ignores_unrelated_bootstrap_changes(self) -> None:
        first_payload = bootstrap()
        second_payload = bootstrap()
        second_payload["elements"][0]["event_points"] = 99
        one = probe_gameweek_completion(
            target_gameweek=1,
            opener=RoutingOpener(self.routes(bootstrap_payload=first_payload)),
            attempts=1,
            clock=lambda: self.first,
            sleeper=lambda seconds: None,
        )
        two = probe_gameweek_completion(
            target_gameweek=1,
            opener=RoutingOpener(self.routes(bootstrap_payload=second_payload)),
            attempts=1,
            clock=lambda: self.first,
            sleeper=lambda seconds: None,
        )
        self.assertEqual(one.semantic_sha256, two.semantic_sha256)
        self.assertNotEqual(one.bootstrap_sha256, two.bootstrap_sha256)

    def test_probe_rejects_malformed_identity_and_retries_network_failure(self) -> None:
        duplicate = bootstrap()
        duplicate["events"].append(dict(duplicate["events"][0]))
        with self.assertRaisesRegex(CompletionMonitorError, "exactly one"):
            probe_gameweek_completion(
                target_gameweek=1,
                opener=RoutingOpener(self.routes(bootstrap_payload=duplicate)),
                attempts=1,
                clock=lambda: self.first,
                sleeper=lambda seconds: None,
            )
        routes = self.routes()
        routes[FPL_BOOTSTRAP_STATIC_URL] = URLError("offline")
        with self.assertRaises(RetryableProbeError):
            probe_gameweek_completion(
                target_gameweek=1,
                opener=RoutingOpener(routes),
                attempts=1,
                clock=lambda: self.first,
                sleeper=lambda seconds: None,
            )

    def test_probe_rejects_redirected_response(self) -> None:
        class RedirectResponse(FakeResponse):
            def geturl(self) -> str:
                return "https://example.invalid/bootstrap-static/"

        def redirected(url: str, *, timeout: float) -> FakeResponse:
            return RedirectResponse(json_bytes(bootstrap()))

        with self.assertRaises(CompletionMonitorError) as raised:
            probe_gameweek_completion(
                target_gameweek=1,
                opener=redirected,
                attempts=1,
                clock=lambda: self.first,
                sleeper=lambda seconds: None,
            )
        self.assertNotIsInstance(raised.exception, RetryableProbeError)

        outcome = self.monitor(self.first, redirected)
        self.assertEqual(outcome.status, REVIEW_REQUIRED)
        state = json.loads(
            (self.control / self.season / "gameweek=1/state.json").read_bytes()
        )
        self.assertEqual(state["review"]["scope"], "probe")

    def test_invalid_json_is_not_treated_as_retryable(self) -> None:
        routes = self.routes()
        routes[FPL_BOOTSTRAP_STATIC_URL] = b"not-json"
        opener = RoutingOpener(routes)
        with self.assertRaises(CompletionMonitorError) as raised:
            probe_gameweek_completion(
                target_gameweek=1,
                opener=opener,
                attempts=2,
                clock=lambda: self.first,
                sleeper=lambda seconds: None,
            )
        self.assertNotIsInstance(raised.exception, RetryableProbeError)
        self.assertEqual(opener.calls, [FPL_BOOTSTRAP_STATIC_URL])

    def test_two_stable_observations_create_one_refresh_and_receipt(self) -> None:
        first_opener = RoutingOpener(self.routes())
        outcome = self.monitor(self.first, first_opener)
        self.assertEqual(outcome.status, WAITING)
        self.assertFalse(self.raw.exists())

        second_opener = RoutingOpener(self.routes())
        outcome = self.monitor(self.first + timedelta(minutes=15), second_opener)
        self.assertEqual(outcome.status, REALIZED_COMPLETE)
        self.assertIsNotNone(outcome.realized_snapshot_timestamp)
        target = self.control / self.season / "gameweek=1"
        self.assertTrue((target / "realized-receipt.json").is_file())
        self.assertEqual(len(list((self.raw / self.season).iterdir())), 1)

        no_network = RoutingOpener({})
        repeated = self.monitor(self.first + timedelta(minutes=30), no_network)
        self.assertEqual(repeated.status, REALIZED_COMPLETE)
        self.assertEqual(no_network.calls, [])
        self.assertEqual(len(list((self.raw / self.season).iterdir())), 1)

    def test_sub_interval_observation_does_not_slide_stability_window(self) -> None:
        self.monitor(self.first, RoutingOpener(self.routes()))
        self.monitor(self.first + timedelta(minutes=10), RoutingOpener(self.routes()))
        result = self.monitor(
            self.first + timedelta(minutes=15), RoutingOpener(self.routes())
        )
        self.assertEqual(result.status, REALIZED_COMPLETE)

    def test_retryable_probe_failure_preserves_first_ready_observation(self) -> None:
        self.monitor(self.first, RoutingOpener(self.routes()))
        offline = self.routes()
        offline[FPL_BOOTSTRAP_STATIC_URL] = URLError("offline")
        with self.assertRaises(RetryableProbeError):
            self.monitor(
                self.first + timedelta(minutes=5), RoutingOpener(offline)
            )
        outcome = self.monitor(
            self.first + timedelta(minutes=15), RoutingOpener(self.routes())
        )
        self.assertEqual(outcome.status, REALIZED_COMPLETE)

    def test_changed_semantics_restart_stability_window(self) -> None:
        self.monitor(self.first, RoutingOpener(self.routes()))
        changed = [fixture(finished=False)]
        waiting = self.monitor(
            self.first + timedelta(minutes=15),
            RoutingOpener(self.routes(fixture_payload=changed)),
        )
        self.assertEqual(waiting.status, WAITING)
        self.assertFalse(self.raw.exists())

    def test_existing_finalized_snapshot_is_adopted_without_probe(self) -> None:
        snapshot = self.create_realized_snapshot()
        opener = RoutingOpener({})
        outcome = self.monitor(self.first + timedelta(hours=1), opener)
        self.assertEqual(outcome.status, REALIZED_COMPLETE)
        self.assertEqual(outcome.realized_snapshot_timestamp, snapshot.snapshot_timestamp)
        self.assertEqual(opener.calls, [])

    def test_completed_orphan_is_adopted_from_refreshing_state(self) -> None:
        snapshot = self.create_realized_snapshot()
        target = self.control / self.season / "gameweek=1"
        target.mkdir(parents=True)
        state = {
            "schema_version": 1,
            "season": self.season,
            "target_gameweek": 1,
            "status": "REFRESHING",
            "updated_at": self.first.isoformat(),
            "first_ready_observation": None,
            "last_observation": None,
            "review": None,
            "refresh_snapshot_timestamp": snapshot.snapshot_timestamp,
        }
        (target / "state.json").write_text(json.dumps(state))
        outcome = self.monitor(self.first + timedelta(hours=1), RoutingOpener({}))
        self.assertEqual(outcome.status, REALIZED_COMPLETE)
        self.assertEqual(outcome.realized_snapshot_timestamp, snapshot.snapshot_timestamp)

    def test_crash_after_refresh_before_receipt_is_reconciled_without_refresh(self) -> None:
        self.monitor(self.first, RoutingOpener(self.routes()))
        with patch(
            "fpl_decision_engine.completion_monitor._publish_realized_receipt",
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.monitor(
                    self.first + timedelta(minutes=15),
                    RoutingOpener(self.routes()),
                )

        target = self.control / self.season / "gameweek=1"
        state = json.loads((target / "state.json").read_bytes())
        self.assertEqual(state["status"], "REFRESHING")
        self.assertFalse((target / "realized-receipt.json").exists())
        self.assertEqual(len(list((self.raw / self.season).iterdir())), 1)

        refresh_runner = Mock(side_effect=AssertionError("must not refresh twice"))
        outcome = self.monitor(
            self.first + timedelta(minutes=30),
            RoutingOpener({}),
            refresh_runner=refresh_runner,
        )
        self.assertEqual(outcome.status, REALIZED_COMPLETE)
        self.assertTrue((target / "realized-receipt.json").is_file())
        refresh_runner.assert_not_called()

    def test_crash_after_receipt_is_reconciled_without_probe_or_refresh(self) -> None:
        self.monitor(self.first, RoutingOpener(self.routes()))
        original_write = __import__(
            "fpl_decision_engine.completion_monitor", fromlist=["_write_mutable_json"]
        )._write_mutable_json

        def crash_on_realized(path: Path, payload: dict[str, object]) -> None:
            if payload.get("status") == REALIZED_COMPLETE:
                raise KeyboardInterrupt
            original_write(path, payload)

        with patch(
            "fpl_decision_engine.completion_monitor._write_mutable_json",
            side_effect=crash_on_realized,
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.monitor(
                    self.first + timedelta(minutes=15),
                    RoutingOpener(self.routes()),
                )

        target = self.control / self.season / "gameweek=1"
        self.assertTrue((target / "realized-receipt.json").is_file())
        outcome = self.monitor(
            self.first + timedelta(minutes=30),
            RoutingOpener({}),
            refresh_runner=Mock(side_effect=AssertionError("must not refresh twice")),
        )
        self.assertEqual(outcome.status, REALIZED_COMPLETE)

    def test_completed_but_nonfinal_refresh_stops_without_automatic_retry(self) -> None:
        self.monitor(self.first, RoutingOpener(self.routes()))

        def nonfinal_refresh(**arguments: object):
            nonfinal = RoutingOpener(
                self.routes(
                    bootstrap_payload=bootstrap(finished=True, checked=False)
                )
            )
            arguments.update(
                attempts=1,
                bootstrap_opener=nonfinal,
                official_opener=nonfinal,
            )
            return refresh_fpl_data(**arguments)

        outcome = self.monitor(
            self.first + timedelta(minutes=15),
            RoutingOpener(self.routes()),
            refresh_runner=nonfinal_refresh,
        )
        self.assertEqual(outcome.status, REVIEW_REQUIRED)
        blocked_runner = Mock()
        repeated = self.monitor(
            self.first + timedelta(minutes=30),
            RoutingOpener({}),
            refresh_runner=blocked_runner,
        )
        self.assertEqual(repeated.status, REVIEW_REQUIRED)
        blocked_runner.assert_not_called()

    def test_interrupted_refresh_state_stops_without_second_refresh(self) -> None:
        target = self.control / self.season / "gameweek=1"
        target.mkdir(parents=True)
        state = {
            "schema_version": 1,
            "season": self.season,
            "target_gameweek": 1,
            "status": "REFRESHING",
            "updated_at": self.first.isoformat(),
            "first_ready_observation": None,
            "last_observation": None,
            "review": None,
            "refresh_snapshot_timestamp": None,
        }
        (target / "state.json").write_text(json.dumps(state))
        refresh_runner = Mock()
        outcome = self.monitor(
            self.first + timedelta(minutes=1),
            RoutingOpener({}),
            refresh_runner=refresh_runner,
        )
        self.assertEqual(outcome.status, REVIEW_REQUIRED)
        refresh_runner.assert_not_called()

    def test_target_lock_blocks_concurrent_invocation_and_unlock_is_explicit(self) -> None:
        target = self.control / self.season / "gameweek=1"
        lock = _acquire_lock(target, self.season, 1, self.first)
        with self.assertRaises(MonitorLockedError):
            self.monitor(self.first, RoutingOpener({}))
        result = unlock_completion_monitor(
            control_data_root=self.control,
            season=self.season,
            target_gameweek=1,
        )
        self.assertEqual(result.lock_path, lock)
        self.assertFalse(lock.exists())

    def test_completed_refresh_validator_rejects_manifest_tampering(self) -> None:
        snapshot = self.create_realized_snapshot()
        validated = validate_completed_refresh_snapshot(
            raw_data_root=self.raw,
            clean_data_root=self.clean,
            season=self.season,
            snapshot_timestamp=snapshot.snapshot_timestamp,
        )
        self.assertEqual(validated.snapshot_timestamp, snapshot.snapshot_timestamp)
        manifest = json.loads(snapshot.manifest_path.read_bytes())
        manifest["row_counts"]["players"] = 99
        snapshot.manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(RefreshError, "row counts"):
            validate_completed_refresh_snapshot(
                raw_data_root=self.raw,
                clean_data_root=self.clean,
                season=self.season,
                snapshot_timestamp=snapshot.snapshot_timestamp,
            )

    def test_realized_receipt_hash_mismatch_requires_review(self) -> None:
        snapshot = self.create_realized_snapshot()
        self.monitor(self.first + timedelta(hours=1), RoutingOpener({}))
        manifest = json.loads(snapshot.manifest_path.read_bytes())
        manifest["software"]["project_version"] = "tampered"
        snapshot.manifest_path.write_text(json.dumps(manifest))
        outcome = self.monitor(self.first + timedelta(hours=2), RoutingOpener({}))
        self.assertEqual(outcome.status, REVIEW_REQUIRED)
        state = json.loads(
            (self.control / self.season / "gameweek=1/state.json").read_bytes()
        )
        self.assertEqual(state["review"]["scope"], "receipt")

    def fake_evaluator(self, **arguments: object) -> EvaluationOutputs:
        prediction_snapshot = str(arguments["prediction_snapshot_timestamp"])
        realized_snapshot = str(arguments["realized_snapshot_timestamp"])
        directory = (
            self.evaluations
            / self.season
            / "gameweek=1/v0.1/20260822T120000.000000Z"
        )
        directory.mkdir(parents=True)
        prediction_directory = (
            self.predictions / self.season / prediction_snapshot / "gameweek=1"
        )
        realized_raw = self.raw / self.season / realized_snapshot
        realized_clean = self.clean / self.season / realized_snapshot

        def sha(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        manifest = {
            "status": "complete",
            "season": self.season,
            "target_gameweek": 1,
            "model_version": "v0.1",
            "prediction": {
                "snapshot_timestamp": prediction_snapshot,
                "source_path": (
                    prediction_directory / "xfp_v01_gameweek.parquet"
                ).as_posix(),
                "sha256": sha(prediction_directory / "xfp_v01_gameweek.parquet"),
                "fixture_source_path": (
                    prediction_directory / "xfp_v01_fixtures.parquet"
                ).as_posix(),
                "fixture_sha256": sha(
                    prediction_directory / "xfp_v01_fixtures.parquet"
                ),
            },
            "realized_data": {
                "snapshot_timestamp": realized_snapshot,
                "bootstrap_path": (realized_raw / "bootstrap-static.json").as_posix(),
                "bootstrap_sha256": sha(realized_raw / "bootstrap-static.json"),
                "fixtures_path": (realized_clean / "fixtures.parquet").as_posix(),
                "fixtures_sha256": sha(realized_clean / "fixtures.parquet"),
                "history_path": (
                    realized_clean / "player_gameweek_history.parquet"
                ).as_posix(),
                "history_sha256": sha(
                    realized_clean / "player_gameweek_history.parquet"
                ),
            },
        }
        output_names = {
            "player": "player_evaluation.parquet",
            "metrics": "metrics.parquet",
            "position": "position_metrics.parquet",
            "diagnostic": "diagnostic_metrics.parquet",
            "ranking": "ranking_summary.parquet",
        }
        for name in output_names.values():
            (directory / name).write_bytes(b"synthetic-output")
        manifest["outputs"] = {
            key: {
                "path": (directory / name).as_posix(),
                "sha256": sha(directory / name),
            }
            for key, name in output_names.items()
        }
        manifest_path = directory / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        return EvaluationOutputs(
            directory=directory,
            player_path=directory / output_names["player"],
            metrics_path=directory / output_names["metrics"],
            position_metrics_path=directory / output_names["position"],
            diagnostic_metrics_path=directory / output_names["diagnostic"],
            ranking_path=directory / output_names["ranking"],
            manifest_path=manifest_path,
            player_rows=1,
            evaluated_players=1,
        )

    def write_synthetic_predictions(self, timestamp: str) -> None:
        directory = self.predictions / self.season / timestamp / "gameweek=1"
        directory.mkdir(parents=True)
        (directory / "xfp_v01_gameweek.parquet").write_bytes(b"prediction")
        (directory / "xfp_v01_fixtures.parquet").write_bytes(b"fixtures")

    def test_explicit_evaluation_is_deduplicated_by_validated_receipt(self) -> None:
        self.create_realized_snapshot()
        prediction = "20260819T120000.000000Z"
        self.write_synthetic_predictions(prediction)
        runner = Mock(side_effect=self.fake_evaluator)
        outcome = self.monitor(
            self.first + timedelta(hours=1),
            RoutingOpener({}),
            prediction_snapshot_timestamp=prediction,
            evaluation_runner=runner,
        )
        self.assertEqual(outcome.status, COMPLETE)
        runner.assert_called_once()
        repeated = self.monitor(
            self.first + timedelta(hours=2),
            RoutingOpener({}),
            prediction_snapshot_timestamp=prediction,
            evaluation_runner=Mock(side_effect=AssertionError("must not run")),
        )
        self.assertEqual(repeated.status, COMPLETE)

    def test_evaluation_output_tampering_invalidates_receipt(self) -> None:
        self.create_realized_snapshot()
        prediction = "20260819T120000.000000Z"
        self.write_synthetic_predictions(prediction)
        completed = self.monitor(
            self.first + timedelta(hours=1),
            RoutingOpener({}),
            prediction_snapshot_timestamp=prediction,
            evaluation_runner=self.fake_evaluator,
        )
        self.assertIsNotNone(completed.evaluation_directory)
        if completed.evaluation_directory is None:
            self.fail("evaluation directory was not returned")
        (completed.evaluation_directory / "metrics.parquet").write_bytes(b"tampered")
        outcome = self.monitor(
            self.first + timedelta(hours=2),
            RoutingOpener({}),
            prediction_snapshot_timestamp=prediction,
        )
        self.assertEqual(outcome.status, REVIEW_REQUIRED)

    def test_evaluation_failure_can_be_archived_and_retried(self) -> None:
        self.create_realized_snapshot()
        prediction = "20260819T120000.000000Z"
        self.write_synthetic_predictions(prediction)
        failed = self.monitor(
            self.first + timedelta(hours=1),
            RoutingOpener({}),
            prediction_snapshot_timestamp=prediction,
            evaluation_runner=Mock(side_effect=EvaluationError("synthetic failure")),
        )
        self.assertEqual(failed.status, REVIEW_REQUIRED)
        archive = reset_completion_monitor(
            control_data_root=self.control,
            raw_data_root=self.raw,
            clean_data_root=self.clean,
            season=self.season,
            target_gameweek=1,
            reason="Reviewed synthetic evaluator failure",
            clock=lambda: self.first + timedelta(hours=2),
        )
        self.assertTrue(archive.is_file())
        state = json.loads(
            (self.control / self.season / "gameweek=1/state.json").read_bytes()
        )
        self.assertEqual(state["status"], REALIZED_COMPLETE)

    def test_evaluation_reset_revalidates_realized_receipt(self) -> None:
        snapshot = self.create_realized_snapshot()
        prediction = "20260819T120000.000000Z"
        self.write_synthetic_predictions(prediction)
        self.monitor(
            self.first + timedelta(hours=1),
            RoutingOpener({}),
            prediction_snapshot_timestamp=prediction,
            evaluation_runner=Mock(side_effect=EvaluationError("synthetic failure")),
        )
        manifest = json.loads(snapshot.manifest_path.read_bytes())
        manifest["software"]["project_version"] = "tampered"
        snapshot.manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(CompletionMonitorError, "valid realized receipt"):
            reset_completion_monitor(
                control_data_root=self.control,
                raw_data_root=self.raw,
                clean_data_root=self.clean,
                season=self.season,
                target_gameweek=1,
                reason="Attempted reset after receipt corruption",
                clock=lambda: self.first + timedelta(hours=2),
            )

    def test_unexpected_evaluation_failure_requires_review(self) -> None:
        self.create_realized_snapshot()
        prediction = "20260819T120000.000000Z"
        self.write_synthetic_predictions(prediction)
        failed = self.monitor(
            self.first + timedelta(hours=1),
            RoutingOpener({}),
            prediction_snapshot_timestamp=prediction,
            evaluation_runner=Mock(side_effect=RuntimeError("synthetic unexpected failure")),
        )
        self.assertEqual(failed.status, REVIEW_REQUIRED)
        blocked_runner = Mock()
        repeated = self.monitor(
            self.first + timedelta(hours=2),
            RoutingOpener({}),
            prediction_snapshot_timestamp=prediction,
            evaluation_runner=blocked_runner,
        )
        self.assertEqual(repeated.status, REVIEW_REQUIRED)
        blocked_runner.assert_not_called()

    def test_evaluation_review_cannot_be_bypassed_by_omitting_prediction(self) -> None:
        self.create_realized_snapshot()
        prediction = "20260819T120000.000000Z"
        self.write_synthetic_predictions(prediction)
        self.monitor(
            self.first + timedelta(hours=1),
            RoutingOpener({}),
            prediction_snapshot_timestamp=prediction,
            evaluation_runner=Mock(side_effect=EvaluationError("synthetic failure")),
        )
        outcome = self.monitor(
            self.first + timedelta(hours=2), RoutingOpener({})
        )
        self.assertEqual(outcome.status, REVIEW_REQUIRED)

    def test_completed_evaluation_cannot_be_downgraded_by_omission(self) -> None:
        self.create_realized_snapshot()
        prediction = "20260819T120000.000000Z"
        self.write_synthetic_predictions(prediction)
        completed = self.monitor(
            self.first + timedelta(hours=1),
            RoutingOpener({}),
            prediction_snapshot_timestamp=prediction,
            evaluation_runner=self.fake_evaluator,
        )
        self.assertEqual(completed.status, COMPLETE)
        omitted = self.monitor(
            self.first + timedelta(hours=2), RoutingOpener({})
        )
        self.assertEqual(omitted.status, REVIEW_REQUIRED)
        state = json.loads(
            (self.control / self.season / "gameweek=1/state.json").read_bytes()
        )
        self.assertEqual(state["review"]["scope"], "receipt")

    def test_cli_requires_explicit_monitor_target_and_exposes_reset(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "monitor-completion",
                "--season",
                self.season,
                "--target-gameweek",
                "1",
            ]
        )
        self.assertEqual((args.season, args.target_gameweek), (self.season, 1))
        reset = parser.parse_args(
            [
                "monitor-completion-reset",
                "--season",
                self.season,
                "--target-gameweek",
                "1",
                "--reason",
                "reviewed",
            ]
        )
        self.assertEqual(reset.reason, "reviewed")


if __name__ == "__main__":
    unittest.main()
