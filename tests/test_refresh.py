from __future__ import annotations

import json
import ssl
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import duckdb

from fpl_decision_engine.official_data import FPL_ELEMENT_SUMMARY_URL, FPL_FIXTURES_URL
from fpl_decision_engine.pipeline import FPL_BOOTSTRAP_STATIC_URL
from fpl_decision_engine.refresh import (
    RefreshError,
    RefreshIncompleteError,
    RefreshLockNotFoundError,
    _acquire_snapshot_lock,
    refresh_fpl_data,
    unlock_refresh_snapshot,
)
from fpl_decision_engine.tls import create_verified_ssl_context, network_error_reason
from fpl_decision_engine.transform import (
    CleanOutputExistsError,
    transform_players_for_snapshot,
)


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


def json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def player(player_id: int) -> dict:
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
        "minutes": 0,
        "starts": 0,
        "total_points": 0,
        "event_points": 0,
        "points_per_game": "0.0",
        "form": "0.0",
        "bonus": 0,
        "bps": 0,
        "expected_goals": "0.0",
        "expected_assists": "0.0",
        "expected_goal_involvements": "0.0",
        "expected_goals_conceded": "0.0",
        "expected_goals_per_90": 0.0,
        "expected_assists_per_90": 0.0,
        "expected_goal_involvements_per_90": 0.0,
        "expected_goals_conceded_per_90": 0.0,
        "clearances_blocks_interceptions": 0,
        "recoveries": 0,
        "tackles": 0,
        "defensive_contribution": 0,
        "defensive_contribution_per_90": 0.0,
        "penalties_order": None,
        "direct_freekicks_order": None,
        "corners_and_indirect_freekicks_order": None,
        "ep_this": "0.0",
        "ep_next": "0.0",
    }


def bootstrap(player_ids: list[int]) -> dict:
    return {
        "elements": [player(player_id) for player_id in player_ids],
        "teams": [
            {"id": 1, "name": "Home", "short_name": "HOM"},
            {"id": 2, "name": "Away", "short_name": "AWY"},
        ],
        "element_types": [{"id": 2, "singular_name": "Defender"}],
        "events": [
            {
                "id": 1,
                "finished": False,
                "data_checked": False,
                "is_current": True,
                "is_next": False,
            }
        ],
    }


def fixture() -> dict:
    return {
        "id": 10,
        "code": 10010,
        "event": 1,
        "kickoff_time": "2026-08-29T14:00:00Z",
        "started": False,
        "finished": False,
        "finished_provisional": False,
        "provisional_start_time": False,
        "minutes": 0,
        "team_h": 1,
        "team_a": 2,
        "team_h_score": None,
        "team_a_score": None,
        "team_h_difficulty": 2,
        "team_a_difficulty": 3,
        "pulse_id": 500,
    }


def history_body() -> bytes:
    return json_bytes({"fixtures": [], "history": [], "history_past": []})


class RefreshWorkflowTests(unittest.TestCase):
    season = "2026-27"
    player_ids = [1, 2, 611]
    first_time = datetime(2026, 8, 26, 1, 2, 3, 456789, tzinfo=timezone.utc)
    second_time = datetime(2026, 8, 27, 1, 2, 3, 456789, tzinfo=timezone.utc)

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.raw_root = root / "raw"
        self.clean_root = root / "clean"
        self.bootstrap_body = json_bytes(bootstrap(self.player_ids))
        self.fixture_body = json_bytes([fixture()])
        self.histories = {player_id: history_body() for player_id in self.player_ids}

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def routes(self, overrides: dict[str, bytes | BaseException] | None = None):
        routes: dict[str, bytes | BaseException] = {
            FPL_BOOTSTRAP_STATIC_URL: self.bootstrap_body,
            FPL_FIXTURES_URL: self.fixture_body,
        }
        routes.update(
            {
                FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=player_id): body
                for player_id, body in self.histories.items()
            }
        )
        routes.update(overrides or {})
        return routes

    def run_refresh(
        self,
        opener: RoutingOpener,
        *,
        now: datetime | None = None,
        resume: str | None = None,
    ):
        fixed = now or self.first_time
        return refresh_fpl_data(
            raw_data_root=self.raw_root,
            clean_data_root=self.clean_root,
            season=self.season,
            resume_snapshot_timestamp=resume,
            attempts=1,
            history_delay_seconds=0,
            bootstrap_opener=opener,
            official_opener=opener,
            clock=lambda: fixed,
            sleeper=lambda seconds: None,
        )

    def test_complete_refresh_is_coherent_dynamic_and_immutable(self) -> None:
        opener = RoutingOpener(self.routes())
        result = self.run_refresh(opener)
        self.assertEqual(result.snapshot_timestamp, "20260826T010203.456789Z")
        self.assertEqual(result.raw_directory.name, result.clean_directory.name)
        self.assertEqual(result.player_count, 3)
        self.assertIn(
            FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=611), opener.calls
        )
        manifest = json.loads(result.manifest_path.read_bytes())
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["expected_player_ids"], [1, 2, 611])
        self.assertEqual(manifest["player_history"]["success_count"], 3)
        self.assertEqual(manifest["row_counts"]["players"], 3)
        self.assertFalse((result.raw_directory / ".refresh.lock").exists())
        self.assertEqual(list(result.raw_directory.rglob("*.tmp")), [])

        raw_files = {
            path: path.read_bytes()
            for path in result.raw_directory.rglob("*")
            if path.is_file()
        }
        with self.assertRaisesRegex(RefreshError, "cannot be resumed"):
            self.run_refresh(RoutingOpener({}), resume=result.snapshot_timestamp)
        for path, original in raw_files.items():
            self.assertEqual(path.read_bytes(), original)
        with self.assertRaises(CleanOutputExistsError):
            transform_players_for_snapshot(
                raw_data_root=self.raw_root,
                clean_data_root=self.clean_root,
                season=self.season,
                snapshot_timestamp=result.snapshot_timestamp,
            )

        second = self.run_refresh(RoutingOpener(self.routes()), now=self.second_time)
        self.assertNotEqual(second.snapshot_timestamp, result.snapshot_timestamp)
        for path, original in raw_files.items():
            self.assertEqual(path.read_bytes(), original)

    def test_partial_resume_reuses_successes_and_fetches_only_missing(self) -> None:
        failed_endpoint = FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=2)
        first = RoutingOpener(self.routes({failed_endpoint: URLError("offline")}))
        with self.assertRaises(RefreshIncompleteError) as raised:
            self.run_refresh(first)
        timestamp = raised.exception.snapshot_timestamp
        raw_dir = self.raw_root / self.season / timestamp
        player_one_path = raw_dir / "player_history/1.json"
        player_611_path = raw_dir / "player_history/611.json"
        player_one_bytes = player_one_path.read_bytes()
        player_611_bytes = player_611_path.read_bytes()
        self.assertFalse((self.clean_root / self.season / timestamp).exists())
        self.assertEqual(
            json.loads((raw_dir / "refresh.manifest.json").read_bytes())["status"],
            "incomplete",
        )
        self.assertFalse((raw_dir / ".refresh.lock").exists())

        resume_opener = RoutingOpener({failed_endpoint: self.histories[2]})
        result = self.run_refresh(resume_opener, resume=timestamp)
        self.assertEqual(resume_opener.calls, [failed_endpoint])
        self.assertEqual(player_one_path.read_bytes(), player_one_bytes)
        self.assertEqual(player_611_path.read_bytes(), player_611_bytes)
        history_manifest = json.loads(
            (raw_dir / "player_history/manifest.json").read_bytes()
        )
        self.assertEqual(history_manifest["status"], "complete")
        self.assertEqual(history_manifest["last_resume_reused_count"], 2)
        self.assertEqual(result.player_count, 3)

    def test_clean_transform_failure_never_marks_refresh_complete(self) -> None:
        with patch(
            "fpl_decision_engine.refresh.transform_players_for_snapshot",
            side_effect=RuntimeError("synthetic transform failure"),
        ):
            with self.assertRaises(RefreshIncompleteError) as raised:
                self.run_refresh(RoutingOpener(self.routes()))

        raw_dir = self.raw_root / self.season / raised.exception.snapshot_timestamp
        refresh_manifest = json.loads(
            (raw_dir / "refresh.manifest.json").read_bytes()
        )
        history_manifest = json.loads(
            (raw_dir / "player_history/manifest.json").read_bytes()
        )
        self.assertEqual(history_manifest["status"], "complete")
        self.assertEqual(refresh_manifest["status"], "incomplete")
        self.assertEqual(refresh_manifest["stage"], "transform_players")
        self.assertIsNone(refresh_manifest["refresh_completed_at"])
        self.assertNotIn("row_counts", refresh_manifest)
        self.assertFalse((raw_dir / ".refresh.lock").exists())

    def test_resume_freezes_bootstrap_player_universe_and_snapshot_identity(self) -> None:
        failed_endpoint = FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=2)
        with self.assertRaises(RefreshIncompleteError) as raised:
            self.run_refresh(
                RoutingOpener(self.routes({failed_endpoint: URLError("offline")}))
            )
        timestamp = raised.exception.snapshot_timestamp
        raw_dir = self.raw_root / self.season / timestamp
        original_bootstrap = (raw_dir / "bootstrap-static.json").read_bytes()

        later_bootstrap = json_bytes(bootstrap([1, 2, 611, 4]))
        resume_opener = RoutingOpener(
            {
                FPL_BOOTSTRAP_STATIC_URL: later_bootstrap,
                failed_endpoint: self.histories[2],
            }
        )
        result = self.run_refresh(resume_opener, resume=timestamp)

        self.assertEqual(resume_opener.calls, [failed_endpoint])
        self.assertEqual(result.snapshot_timestamp, timestamp)
        self.assertEqual(result.raw_directory, raw_dir)
        self.assertEqual(result.clean_directory.name, timestamp)
        self.assertEqual(
            (raw_dir / "bootstrap-static.json").read_bytes(), original_bootstrap
        )
        manifest = json.loads(result.manifest_path.read_bytes())
        self.assertEqual(manifest["expected_player_ids"], self.player_ids)
        self.assertEqual(
            [path.name for path in (self.raw_root / self.season).iterdir()],
            [timestamp],
        )

    def test_crash_lock_requires_manual_unlock_before_resume(self) -> None:
        failed_endpoint = FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=2)
        with self.assertRaises(RefreshIncompleteError) as raised:
            self.run_refresh(
                RoutingOpener(self.routes({failed_endpoint: URLError("offline")}))
            )
        timestamp = raised.exception.snapshot_timestamp
        raw_dir = self.raw_root / self.season / timestamp
        manifest_path = raw_dir / "refresh.manifest.json"
        manifest_before = manifest_path.read_bytes()
        unrelated_paths = [
            self.raw_root
            / self.season
            / "20260820T000000.000000Z"
            / "unrelated.bin",
            self.clean_root
            / self.season
            / "20260820T000000.000000Z"
            / "unrelated.parquet",
            self.raw_root.parent / "predictions/unrelated.parquet",
            self.raw_root.parent / "evaluations/unrelated.json",
        ]
        for index, unrelated_path in enumerate(unrelated_paths):
            unrelated_path.parent.mkdir(parents=True, exist_ok=True)
            unrelated_path.write_bytes(f"unrelated bytes {index}".encode("utf-8"))
        immutable_paths = [
            raw_dir / "bootstrap-static.json",
            raw_dir / "fixtures.json",
            raw_dir / "player_history/1.json",
            raw_dir / "player_history/611.json",
            *unrelated_paths,
        ]
        immutable_bytes = {path: path.read_bytes() for path in immutable_paths}

        # Simulate a hard process crash by deliberately leaving an acquired lock.
        lock_path = _acquire_snapshot_lock(raw_dir, self.first_time)
        self.assertTrue(lock_path.is_file())
        files_before_unlock = {
            path: path.read_bytes()
            for path in self.raw_root.rglob("*")
            if path.is_file() and path != lock_path
        }

        with self.assertRaisesRegex(RefreshError, "locked by another process"):
            _acquire_snapshot_lock(raw_dir, self.first_time)

        with self.assertRaisesRegex(RefreshError, "locked by another process"):
            self.run_refresh(RoutingOpener({}), resume=timestamp)

        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        self.assertTrue(lock_path.is_file())

        with self.assertLogs("fpl_decision_engine.refresh", level="WARNING") as logs:
            unlock_result = unlock_refresh_snapshot(
                raw_data_root=self.raw_root,
                season=self.season,
                snapshot_timestamp=timestamp,
            )
        self.assertFalse(lock_path.exists())
        self.assertIsNotNone(unlock_result.lock_metadata)
        self.assertEqual(
            unlock_result.lock_metadata["snapshot_directory"], raw_dir.as_posix()
        )
        self.assertTrue(any("Ensure no refresh process" in line for line in logs.output))
        self.assertTrue(any("Lock metadata" in line for line in logs.output))
        self.assertEqual(manifest_path.read_bytes(), manifest_before)
        for path, body in files_before_unlock.items():
            self.assertEqual(path.read_bytes(), body)
        for path, body in immutable_bytes.items():
            self.assertEqual(path.read_bytes(), body)

        with self.assertRaises(RefreshLockNotFoundError):
            unlock_refresh_snapshot(
                raw_data_root=self.raw_root,
                season=self.season,
                snapshot_timestamp=timestamp,
            )

        resume_opener = RoutingOpener({failed_endpoint: self.histories[2]})
        result = self.run_refresh(resume_opener, resume=timestamp)
        self.assertEqual(result.snapshot_timestamp, timestamp)
        self.assertEqual(resume_opener.calls, [failed_endpoint])
        for path, body in immutable_bytes.items():
            self.assertEqual(path.read_bytes(), body)

    def test_mismatched_player_history_identity_is_quarantined_and_not_completed(self) -> None:
        endpoint_42 = FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=42)
        bootstrap_42 = json_bytes(bootstrap([42]))
        initial_opener = RoutingOpener(
            {
                FPL_BOOTSTRAP_STATIC_URL: bootstrap_42,
                FPL_FIXTURES_URL: self.fixture_body,
                endpoint_42: URLError("offline"),
            }
        )
        with self.assertRaises(RefreshIncompleteError) as raised:
            self.run_refresh(initial_opener)
        timestamp = raised.exception.snapshot_timestamp
        raw_dir = self.raw_root / self.season / timestamp

        wrong_identity_body = json_bytes(
            {
                "fixtures": [],
                "history": [{"element": 43}],
                "history_past": [],
            }
        )
        target_path = raw_dir / "player_history/42.json"
        target_path.write_bytes(wrong_identity_body)

        with self.assertRaises(RefreshIncompleteError):
            self.run_refresh(
                RoutingOpener({endpoint_42: URLError("still offline")}),
                resume=timestamp,
            )

        history_manifest = json.loads(
            (raw_dir / "player_history/manifest.json").read_bytes()
        )
        self.assertEqual(history_manifest["status"], "partial")
        self.assertEqual(history_manifest["success_count"], 0)
        self.assertEqual(history_manifest["remaining_player_ids"], [42])
        self.assertFalse(target_path.exists())
        quarantined = list((raw_dir / "player_history/quarantine").glob("*.json"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_bytes(), wrong_identity_body)

    def test_wrong_schema_fixture_response_is_rejected_before_storage(self) -> None:
        opener = RoutingOpener(
            self.routes({FPL_FIXTURES_URL: json_bytes({"error": "unavailable"})})
        )
        with self.assertRaises(RefreshIncompleteError) as raised:
            self.run_refresh(opener)
        raw_dir = self.raw_root / self.season / raised.exception.snapshot_timestamp
        manifest = json.loads((raw_dir / "refresh.manifest.json").read_bytes())
        self.assertEqual(manifest["status"], "incomplete")
        self.assertEqual(manifest["stage"], "fixtures")
        self.assertFalse((raw_dir / "fixtures.json").exists())

    def test_wrong_schema_player_history_is_rejected_but_empty_history_is_valid(self) -> None:
        endpoint_one = FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=1)
        opener = RoutingOpener(
            self.routes({endpoint_one: json_bytes({"error": "unavailable"})})
        )
        with self.assertRaises(RefreshIncompleteError) as raised:
            self.run_refresh(opener)
        raw_dir = self.raw_root / self.season / raised.exception.snapshot_timestamp
        history_manifest = json.loads(
            (raw_dir / "player_history/manifest.json").read_bytes()
        )
        self.assertEqual(history_manifest["status"], "partial")
        self.assertEqual(history_manifest["remaining_player_ids"], [1])
        self.assertFalse((raw_dir / "player_history/1.json").exists())
        self.assertTrue((raw_dir / "player_history/2.json").is_file())
        self.assertTrue((raw_dir / "player_history/611.json").is_file())

    def test_corrupt_partial_response_is_quarantined_and_refetched(self) -> None:
        failed_endpoint = FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=2)
        with self.assertRaises(RefreshIncompleteError) as raised:
            self.run_refresh(
                RoutingOpener(self.routes({failed_endpoint: URLError("offline")}))
            )
        timestamp = raised.exception.snapshot_timestamp
        raw_dir = self.raw_root / self.season / timestamp
        corrupt_path = raw_dir / "player_history/1.json"
        corrupt_path.write_bytes(b"corrupt partial bytes")
        endpoint_one = FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=1)
        resume_opener = RoutingOpener(
            {endpoint_one: self.histories[1], failed_endpoint: self.histories[2]}
        )
        self.run_refresh(resume_opener, resume=timestamp)
        self.assertEqual(resume_opener.calls, [endpoint_one, failed_endpoint])
        self.assertEqual(corrupt_path.read_bytes(), self.histories[1])
        quarantined = list((raw_dir / "player_history/quarantine").glob("*.json"))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_bytes(), b"corrupt partial bytes")

    def test_keyboard_interrupt_leaves_explicit_resumable_state(self) -> None:
        interrupted_endpoint = FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=2)
        with self.assertRaises(KeyboardInterrupt):
            self.run_refresh(
                RoutingOpener(
                    self.routes({interrupted_endpoint: KeyboardInterrupt()})
                )
            )
        timestamp = "20260826T010203.456789Z"
        raw_dir = self.raw_root / self.season / timestamp
        refresh_manifest = json.loads(
            (raw_dir / "refresh.manifest.json").read_bytes()
        )
        history_manifest = json.loads(
            (raw_dir / "player_history/manifest.json").read_bytes()
        )
        self.assertEqual(refresh_manifest["status"], "incomplete")
        self.assertEqual(history_manifest["status"], "incomplete")
        self.assertTrue((raw_dir / "player_history/1.json").is_file())
        self.assertFalse((self.clean_root / self.season / timestamp).exists())

        # Simulate termination in the narrow window after the atomic response
        # write but before its manifest entry was persisted.
        player_one_bytes = (raw_dir / "player_history/1.json").read_bytes()
        history_manifest["responses"] = []
        history_manifest["success_count"] = 0
        (raw_dir / "player_history/manifest.json").write_text(
            json.dumps(history_manifest), encoding="utf-8"
        )

        resume_opener = RoutingOpener(
            {
                interrupted_endpoint: self.histories[2],
                FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=611): self.histories[611],
            }
        )
        result = self.run_refresh(resume_opener, resume=timestamp)
        self.assertEqual(result.player_count, 3)
        self.assertNotIn(
            FPL_ELEMENT_SUMMARY_URL.format(fpl_player_id=1), resume_opener.calls
        )
        self.assertEqual(
            (raw_dir / "player_history/1.json").read_bytes(), player_one_bytes
        )
        resumed_manifest = json.loads(
            (raw_dir / "player_history/manifest.json").read_bytes()
        )
        recovered = next(
            row for row in resumed_manifest["responses"]
            if row["fpl_player_id"] == 1
        )
        self.assertEqual(
            recovered["retrieved_at_source"], "recovered_from_file_mtime"
        )

    def test_tls_context_always_requires_certificate_and_hostname_verification(self) -> None:
        context = create_verified_ssl_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        message = network_error_reason(
            ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED")
        )
        self.assertIn("CA trust store", message)
        self.assertIn("not disabled", message)


if __name__ == "__main__":
    unittest.main()
