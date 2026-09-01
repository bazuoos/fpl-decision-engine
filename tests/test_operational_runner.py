from __future__ import annotations

import json
import inspect
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from fixture_support import materialized_frozen_gw2, sha256_file

from fpl_decision_engine.operational_manifest import ChipState
from fpl_decision_engine.operational_manifest import build_decision_id
import fpl_decision_engine.operational_runner as operational_runner_module
from fpl_decision_engine.operational_runner import (
    COMPLETED_STATUS,
    MANAGER_EVIDENCE_VERSION,
    PREPARATION_STATUS,
    OperationalErrorCode,
    OperationalRunnerError,
    OperationalStages,
    _official_event,
    prepare_gameweek,
    resume_gameweek,
)
from fpl_decision_engine.refresh import RefreshResult


SEASON = "2026-27"
SNAPSHOT = "20260825T073532.450889Z"
DEADLINE = datetime(2026, 8, 28, 17, 30, tzinfo=timezone.utc)
BEFORE = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
SELLING_PRICES = {
    1: 6.0,
    111: 4.0,
    8: 5.5,
    175: 4.0,
    201: 5.5,
    391: 5.5,
    499: 5.5,
    40: 7.5,
    368: 7.0,
    426: 12.0,
    481: 6.5,
    557: 6.5,
    272: 4.5,
    321: 4.5,
    411: 15.5,
}


class SequenceClock:
    def __init__(self, *values: datetime) -> None:
        self.values = list(values)
        self.last = values[-1]

    def __call__(self) -> datetime:
        if self.values:
            self.last = self.values.pop(0)
        return self.last


class OperationalFixture:
    def __init__(self, root: Path, frozen) -> None:
        self.root = root
        self.frozen = frozen
        self.raw_root = root / "raw"
        self.clean_root = root / "clean"
        self.feature_root = root / "features"
        self.prediction_root = root / "predictions"
        self.operations_root = root / "operations"
        raw = self.raw_root / SEASON / SNAPSHOT
        clean = self.clean_root / SEASON / SNAPSHOT
        feature = self.feature_root / SEASON / SNAPSHOT / "gameweek=2"
        prediction = self.prediction_root / SEASON / SNAPSHOT / "gameweek=2"
        (raw / "player_history").mkdir(parents=True)
        clean.mkdir(parents=True)
        feature.mkdir(parents=True)
        prediction.mkdir(parents=True)
        self.bootstrap = raw / "bootstrap-static.json"
        self.bootstrap.write_text(
            json.dumps(
                {
                    "events": [
                        {
                            "id": 2,
                            "is_next": True,
                            "deadline_time": "2026-08-28T17:30:00Z",
                        }
                    ]
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.fixtures = raw / "fixtures.json"
        self.fixtures.write_text("[]", encoding="utf-8")
        self.fixture_manifest = raw / "fixtures.manifest.json"
        self.fixture_manifest.write_text(
            json.dumps(
                {
                    "season": SEASON,
                    "snapshot_timestamp": SNAPSHOT,
                    "status": "complete",
                    "retrieved_at": "2026-08-25T08:00:00Z",
                    "bootstrap_sha256": sha256_file(self.bootstrap),
                    "response_sha256": sha256_file(self.fixtures),
                    "record_count": 0,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.history_manifest = raw / "player_history" / "manifest.json"
        self.history_manifest.write_text(
            json.dumps(
                {
                    "season": SEASON,
                    "snapshot_timestamp": SNAPSHOT,
                    "status": "complete",
                    "completed_at": "2026-08-25T08:01:00Z",
                    "bootstrap_sha256": sha256_file(self.bootstrap),
                    "expected_count": 610,
                    "success_count": 610,
                    "failure_count": 0,
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.players = clean / "players.parquet"
        self.features = feature / "player_gameweek_features.parquet"
        self.fixture_predictions = prediction / "xfp_v01_fixtures.parquet"
        self.gameweek_predictions = prediction / "xfp_v01_gameweek.parquet"
        shutil.copy2(frozen.players, self.players)
        shutil.copy2(frozen.features, self.features)
        shutil.copy2(frozen.fixture_predictions, self.fixture_predictions)
        shutil.copy2(frozen.gameweek_predictions, self.gameweek_predictions)
        # Snapshot discovery requires these clean inputs to exist; Phase 1 safely
        # reuses the already hash-validated feature fixture instead of reading them.
        (clean / "fixtures.parquet").write_bytes(b"test-only unused fixture input")
        (clean / "player_gameweek_history.parquet").write_bytes(
            b"test-only unused history input"
        )
        self.refresh_manifest = raw / "refresh.manifest.json"
        self.refresh_manifest.write_text(
            json.dumps(
                {
                    "season": SEASON,
                    "snapshot_timestamp": SNAPSHOT,
                    "status": "complete",
                    "bootstrap": {"sha256": sha256_file(self.bootstrap)},
                    "fixtures": {
                        "sha256": sha256_file(self.fixtures),
                        "manifest_sha256": sha256_file(self.fixture_manifest),
                    },
                    "player_history": {
                        "manifest_sha256": sha256_file(self.history_manifest)
                    },
                    "clean_outputs": {
                        "players": {"sha256": sha256_file(self.players)}
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        self.refresh_result = RefreshResult(
            snapshot_timestamp=SNAPSHOT,
            raw_directory=raw,
            clean_directory=clean,
            manifest_path=self.refresh_manifest,
            player_count=610,
            fixture_count=380,
            history_row_count=610,
        )
        self.refresh = Mock(return_value=self.refresh_result)
        self.stages = replace(OperationalStages(), refresh=self.refresh)

    def prepare(self, clock=SequenceClock(BEFORE, BEFORE, BEFORE)):
        return prepare_gameweek(
            target_gameweek=2,
            season=SEASON,
            raw_data_root=self.raw_root,
            clean_data_root=self.clean_root,
            feature_data_root=self.feature_root,
            prediction_data_root=self.prediction_root,
            operations_root=self.operations_root,
            resume_refresh_snapshot_timestamp=None,
            history_delay_seconds=0,
            clock=clock,
            stages=self.stages,
        )

    def manager_evidence(
        self,
        *,
        chip_state: str = ChipState.NO_CHIP.value,
        bank_m: float = 0.0,
        name: str = "manager.json",
        additional: dict[str, object] | None = None,
    ) -> Path:
        manual = json.loads(self.frozen.manual_state.read_bytes())
        payload = {
            "version": MANAGER_EVIDENCE_VERSION,
            "entry_id": manual["entry_id"],
            "season": SEASON,
            "target_gameweek": 2,
            "bank_m": bank_m,
            "free_transfers": 1,
            "chip_state": chip_state,
            "evidence_source": "official FPL Transfers screenshot transcription",
            "evidence_source_sha256": "a" * 64,
            "current_selection_verified": True,
            "players": [
                {
                    "element_id": row["element_id"],
                    "display_name": row["display_name"],
                    "position": row["position"],
                    "selling_price_m": SELLING_PRICES[row["element_id"]],
                }
                for row in manual["picks"]
            ],
        }
        if additional:
            payload.update(additional)
        path = self.root / name
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return path


class OperationalPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.temporary = Path(
            self.stack.enter_context(tempfile.TemporaryDirectory())
        )
        self.frozen = self.stack.enter_context(materialized_frozen_gw2())
        self.fixture = OperationalFixture(self.temporary, self.frozen)

    def tearDown(self) -> None:
        self.stack.close()

    def test_phase_one_freezes_exact_refresh_and_returns_manager_gate(self) -> None:
        result = self.fixture.prepare()
        self.assertEqual(result.status, PREPARATION_STATUS)
        self.assertTrue(result.preparation_manifest_path.is_file())
        self.assertFalse(result.reused)
        copied = result.preparation_manifest_path.parent / "artifacts"
        self.assertEqual(
            sha256_file(copied / "xfp_v01_gameweek.parquet"),
            sha256_file(self.fixture.gameweek_predictions),
        )

    def test_existing_valid_preparation_is_reused_without_overwrite(self) -> None:
        first = self.fixture.prepare()
        before = first.preparation_manifest_path.read_bytes()
        second = self.fixture.prepare()
        self.assertEqual(first.preparation_id, second.preparation_id)
        self.assertTrue(second.reused)
        self.assertEqual(before, second.preparation_manifest_path.read_bytes())

    def test_explicit_gameweek_must_equal_unique_official_next(self) -> None:
        with self.assertRaises(OperationalRunnerError) as raised:
            prepare_gameweek(
                target_gameweek=3,
                season=SEASON,
                raw_data_root=self.fixture.raw_root,
                clean_data_root=self.fixture.clean_root,
                feature_data_root=self.fixture.feature_root,
                prediction_data_root=self.fixture.prediction_root,
                operations_root=self.fixture.operations_root,
                clock=SequenceClock(BEFORE, BEFORE),
                stages=self.fixture.stages,
            )
        self.assertEqual(raised.exception.code, OperationalErrorCode.TARGET_NOT_OFFICIAL_NEXT)

    def test_no_or_multiple_official_next_fails_closed(self) -> None:
        for flags in ((False,), (True, True)):
            with self.subTest(flags=flags):
                payload = {
                    "events": [
                        {
                            "id": index + 1,
                            "is_next": flag,
                            "deadline_time": "2026-08-28T17:30:00Z",
                        }
                        for index, flag in enumerate(flags)
                    ]
                }
                self.fixture.bootstrap.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(OperationalRunnerError) as raised:
                    _official_event(self.fixture.bootstrap, 2)
                self.assertEqual(
                    raised.exception.code, OperationalErrorCode.OFFICIAL_NEXT_INVALID
                )

    def test_phase_one_at_or_after_deadline_fails(self) -> None:
        with self.assertRaises(OperationalRunnerError) as raised:
            self.fixture.prepare(SequenceClock(BEFORE, DEADLINE))
        self.assertEqual(raised.exception.code, OperationalErrorCode.DEADLINE_ALREADY_PASSED)

    def test_non_utc_clock_fails_without_host_timezone_normalization(self) -> None:
        naive = datetime(2026, 8, 27, 12, 0)
        with self.assertRaises(OperationalRunnerError):
            self.fixture.prepare(SequenceClock(naive))

    def test_conflicting_existing_preparation_fails_closed(self) -> None:
        result = self.fixture.prepare()
        feature = result.preparation_manifest_path.parent / "artifacts" / "player_gameweek_features.parquet"
        with feature.open("ab") as output:
            output.write(b"tamper")
        with self.assertRaises(OperationalRunnerError) as raised:
            self.fixture.prepare()
        self.assertEqual(
            raised.exception.code, OperationalErrorCode.PINNED_ARTIFACT_HASH_MISMATCH
        )


class OperationalResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.stack = ExitStack()
        cls.temporary = Path(cls.stack.enter_context(tempfile.TemporaryDirectory()))
        cls.frozen = cls.stack.enter_context(materialized_frozen_gw2())
        cls.fixture = OperationalFixture(cls.temporary, cls.frozen)
        cls.preparation = cls.fixture.prepare()
        cls.manager = cls.fixture.manager_evidence()
        cls.completed = resume_gameweek(
            preparation_manifest_path=cls.preparation.preparation_manifest_path,
            manager_evidence_path=cls.manager,
            clock=SequenceClock(BEFORE, BEFORE, BEFORE, BEFORE, BEFORE),
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.stack.close()

    def test_end_to_end_resume_publishes_trusted_gameweek_decision_and_final_manifest(self) -> None:
        self.assertEqual(self.completed.status, COMPLETED_STATUS)
        self.assertTrue(self.completed.gameweek_decision_path.is_file())
        self.assertTrue(self.completed.final_manifest_path.is_file())
        payload = json.loads(self.completed.gameweek_decision_path.read_bytes())
        self.assertEqual(payload["schema_name"], "GameweekDecision")

    def test_completed_same_decision_is_validated_and_reused_without_regeneration(self) -> None:
        before = {
            path: path.read_bytes()
            for path in self.completed.final_manifest_path.parent.rglob("*")
            if path.is_file()
        }
        forbidden = Mock(side_effect=AssertionError("completed run regenerated"))
        stages = replace(
            OperationalStages(),
            evaluate_transfer=forbidden,
            write_transfer=forbidden,
            load_reliability=forbidden,
            write_reliability=forbidden,
        )
        result = resume_gameweek(
            preparation_manifest_path=self.preparation.preparation_manifest_path,
            manager_evidence_path=self.manager,
            clock=SequenceClock(BEFORE),
            stages=stages,
        )
        self.assertTrue(result.reused)
        self.assertEqual(
            before,
            {
                path: path.read_bytes()
                for path in self.completed.final_manifest_path.parent.rglob("*")
                if path.is_file()
            },
        )
        forbidden.assert_not_called()

    def test_resume_never_discovers_latest_or_refreshes(self) -> None:
        refresh = Mock(side_effect=AssertionError("resume refreshed"))
        stages = replace(OperationalStages(), refresh=refresh)
        result = resume_gameweek(
            preparation_manifest_path=self.preparation.preparation_manifest_path,
            manager_evidence_path=self.manager,
            clock=SequenceClock(BEFORE),
            stages=stages,
        )
        self.assertTrue(result.reused)
        refresh.assert_not_called()

    def test_resume_at_deadline_fails_before_manager_processing(self) -> None:
        with self.assertRaises(OperationalRunnerError) as raised:
            resume_gameweek(
                preparation_manifest_path=self.preparation.preparation_manifest_path,
                manager_evidence_path=self.manager,
                clock=SequenceClock(DEADLINE),
            )
        self.assertEqual(raised.exception.code, OperationalErrorCode.DEADLINE_ALREADY_PASSED)

    def test_manager_verification_exactly_at_deadline_fails(self) -> None:
        manager = self.fixture.manager_evidence(bank_m=0.2, name="deadline-manager.json")
        with self.assertRaises(OperationalRunnerError) as raised:
            resume_gameweek(
                preparation_manifest_path=self.preparation.preparation_manifest_path,
                manager_evidence_path=manager,
                clock=SequenceClock(BEFORE, DEADLINE),
            )
        self.assertEqual(
            raised.exception.code,
            OperationalErrorCode.MANAGER_VERIFICATION_AT_OR_AFTER_DEADLINE,
        )

    def test_clock_crossing_deadline_does_not_publish_completed_output(self) -> None:
        manager = self.fixture.manager_evidence(bank_m=0.3, name="crossing-manager.json")
        with self.assertRaises(OperationalRunnerError) as raised:
            resume_gameweek(
                preparation_manifest_path=self.preparation.preparation_manifest_path,
                manager_evidence_path=manager,
                clock=SequenceClock(BEFORE, BEFORE, BEFORE, BEFORE, DEADLINE),
            )
        self.assertEqual(
            raised.exception.code,
            OperationalErrorCode.DEADLINE_PASSED_DURING_FINALIZATION,
        )
        source_hash = sha256_file(manager)
        states = list(
            (
                self.preparation.preparation_manifest_path.parent
                / "manager_submissions"
                / source_hash
            ).rglob("manual_editable_state.json")
        )
        self.assertEqual(len(states), 1)
        decision_id = build_decision_id(
            preparation_id=self.preparation.preparation_id,
            manager_state_sha256=sha256_file(states[0]),
        )
        decision_root = (
            self.preparation.preparation_manifest_path.parent
            / "decisions"
            / decision_id
        )
        self.assertFalse((decision_root / "gameweek_decision.json").exists())
        self.assertFalse((decision_root / "final_operational_manifest.json").exists())

    def test_manager_evidence_cannot_supply_authoritative_timestamp_or_current_prices(self) -> None:
        invalid = self.fixture.manager_evidence(
            name="invalid-manager.json",
            additional={"verification_timestamp": "2026-08-01T00:00:00Z"},
        )
        with self.assertRaises(OperationalRunnerError) as raised:
            resume_gameweek(
                preparation_manifest_path=self.preparation.preparation_manifest_path,
                manager_evidence_path=invalid,
                clock=SequenceClock(BEFORE),
            )
        self.assertEqual(raised.exception.code, OperationalErrorCode.INVALID_MANAGER_EVIDENCE)

    def test_each_unsupported_chip_has_specific_error_code(self) -> None:
        expected = {
            ChipState.WILDCARD: OperationalErrorCode.UNSUPPORTED_CHIP_WILDCARD,
            ChipState.FREE_HIT: OperationalErrorCode.UNSUPPORTED_CHIP_FREE_HIT,
            ChipState.BENCH_BOOST: OperationalErrorCode.UNSUPPORTED_CHIP_BENCH_BOOST,
            ChipState.TRIPLE_CAPTAIN: OperationalErrorCode.UNSUPPORTED_CHIP_TRIPLE_CAPTAIN,
        }
        for chip, code in expected.items():
            with self.subTest(chip=chip):
                path = self.fixture.manager_evidence(
                    chip_state=chip.value, name=f"{chip.value}.json"
                )
                with self.assertRaises(OperationalRunnerError) as raised:
                    resume_gameweek(
                        preparation_manifest_path=self.preparation.preparation_manifest_path,
                        manager_evidence_path=path,
                        clock=SequenceClock(BEFORE),
                    )
                self.assertEqual(raised.exception.code, code)

    def test_tampering_any_completed_chain_artifact_fails_closed(self) -> None:
        decision_root = self.completed.final_manifest_path.parent
        targets = [
            next(decision_root.rglob("legal_transfer_candidates.json")),
            next(decision_root.rglob("one_transfer_decision.json")),
            next(decision_root.rglob("decision_reliability.json")),
            self.completed.gameweek_decision_path,
            self.completed.final_manifest_path,
        ]
        for target in targets:
            with self.subTest(target=target.name):
                original = target.read_bytes()
                try:
                    target.write_bytes(original + b"tamper")
                    with self.assertRaises(OperationalRunnerError):
                        resume_gameweek(
                            preparation_manifest_path=self.preparation.preparation_manifest_path,
                            manager_evidence_path=self.manager,
                            clock=SequenceClock(BEFORE),
                        )
                finally:
                    target.write_bytes(original)

    def test_each_pinned_preparation_artifact_tamper_fails_before_manager_use(self) -> None:
        artifact_root = self.preparation.preparation_manifest_path.parent / "artifacts"
        for target in sorted(artifact_root.iterdir()):
            with self.subTest(target=target.name):
                original = target.read_bytes()
                try:
                    target.write_bytes(original + b"tamper")
                    with self.assertRaises(OperationalRunnerError) as raised:
                        resume_gameweek(
                            preparation_manifest_path=self.preparation.preparation_manifest_path,
                            manager_evidence_path=self.manager,
                            clock=SequenceClock(BEFORE),
                        )
                    self.assertIn(
                        raised.exception.code,
                        {
                            OperationalErrorCode.PINNED_ARTIFACT_HASH_MISMATCH,
                            OperationalErrorCode.INVALID_PREPARATION_MANIFEST,
                        },
                    )
                finally:
                    target.write_bytes(original)

    def test_tampered_preparation_manifest_and_manager_state_fail_closed(self) -> None:
        preparation_path = self.preparation.preparation_manifest_path
        manager_state = next(
            (
                preparation_path.parent
                / "manager_submissions"
                / sha256_file(self.manager)
            ).rglob("manual_editable_state.json")
        )
        for target in (preparation_path, manager_state):
            with self.subTest(target=target.name):
                original = target.read_bytes()
                try:
                    target.write_bytes(original + b"tamper")
                    with self.assertRaises(OperationalRunnerError):
                        resume_gameweek(
                            preparation_manifest_path=preparation_path,
                            manager_evidence_path=self.manager,
                            clock=SequenceClock(BEFORE),
                        )
                finally:
                    target.write_bytes(original)

    def test_gameweek_decision_without_final_manifest_is_reconciled_not_overwritten(self) -> None:
        original = self.completed.gameweek_decision_path.read_bytes()
        final = self.completed.final_manifest_path
        final_bytes = final.read_bytes()
        try:
            final.unlink()
            result = resume_gameweek(
                preparation_manifest_path=self.preparation.preparation_manifest_path,
                manager_evidence_path=self.manager,
                clock=SequenceClock(BEFORE, BEFORE, BEFORE),
            )
            self.assertFalse(result.reused)
            self.assertEqual(original, self.completed.gameweek_decision_path.read_bytes())
        finally:
            if not final.exists():
                final.write_bytes(final_bytes)

    def test_conflicting_gameweek_decision_without_final_manifest_fails_closed(self) -> None:
        gameweek = self.completed.gameweek_decision_path
        final = self.completed.final_manifest_path
        gameweek_bytes = gameweek.read_bytes()
        final_bytes = final.read_bytes()
        try:
            final.unlink()
            gameweek.write_bytes(gameweek_bytes + b"conflict")
            with self.assertRaises(OperationalRunnerError) as raised:
                resume_gameweek(
                    preparation_manifest_path=self.preparation.preparation_manifest_path,
                    manager_evidence_path=self.manager,
                    clock=SequenceClock(BEFORE, BEFORE, BEFORE),
                )
            self.assertEqual(
                raised.exception.code, OperationalErrorCode.CONFLICTING_IMMUTABLE_OUTPUT
            )
        finally:
            gameweek.write_bytes(gameweek_bytes)
            final.write_bytes(final_bytes)

    def test_partial_candidate_decision_publication_fails_closed(self) -> None:
        manager = self.fixture.manager_evidence(bank_m=0.4, name="partial-manager.json")
        interrupted = replace(
            OperationalStages(),
            evaluate_transfer=Mock(side_effect=RuntimeError("simulated interruption")),
        )
        with self.assertRaises(RuntimeError):
            resume_gameweek(
                preparation_manifest_path=self.preparation.preparation_manifest_path,
                manager_evidence_path=manager,
                clock=SequenceClock(BEFORE, BEFORE),
                stages=interrupted,
            )
        state = next(
            (
                self.preparation.preparation_manifest_path.parent
                / "manager_submissions"
                / sha256_file(manager)
            ).rglob("manual_editable_state.json")
        )
        decision_id = build_decision_id(
            preparation_id=self.preparation.preparation_id,
            manager_state_sha256=sha256_file(state),
        )
        partial = (
            self.preparation.preparation_manifest_path.parent
            / "decisions"
            / decision_id
            / "task016"
            / "partial"
            / "legal_transfer_candidates.json"
        )
        partial.parent.mkdir(parents=True)
        partial.write_text("[]", encoding="utf-8")
        with self.assertRaises(OperationalRunnerError) as raised:
            resume_gameweek(
                preparation_manifest_path=self.preparation.preparation_manifest_path,
                manager_evidence_path=manager,
                clock=SequenceClock(BEFORE),
            )
        self.assertEqual(raised.exception.code, OperationalErrorCode.PINNED_ARTIFACT_MISSING)

    def test_manager_state_exists_without_decision_can_resume(self) -> None:
        changed = self.fixture.manager_evidence(bank_m=0.1, name="changed-bank.json")
        # The manager-state stage publishes before transfer evaluation. A trusted-stage
        # interruption therefore leaves a valid state but no decision.
        interrupted = replace(
            OperationalStages(),
            evaluate_transfer=Mock(side_effect=RuntimeError("simulated interruption")),
        )
        with self.assertRaises(RuntimeError):
            resume_gameweek(
                preparation_manifest_path=self.preparation.preparation_manifest_path,
                manager_evidence_path=changed,
                clock=SequenceClock(BEFORE, BEFORE),
                stages=interrupted,
            )
        states = list(
            self.preparation.preparation_manifest_path.parent.rglob(
                "manual_editable_state.json"
            )
        )
        self.assertGreaterEqual(len(states), 2)
        result = resume_gameweek(
            preparation_manifest_path=self.preparation.preparation_manifest_path,
            manager_evidence_path=changed,
            clock=SequenceClock(BEFORE, BEFORE, BEFORE, BEFORE),
        )
        self.assertNotEqual(result.decision_id, self.completed.decision_id)
        self.assertTrue(self.completed.final_manifest_path.is_file())

    def test_manager_prices_and_zero_transfer_cost_are_preserved_not_inferred(self) -> None:
        state_path = next(
            (
                self.preparation.preparation_manifest_path.parent
                / "manager_submissions"
                / sha256_file(self.manager)
            ).rglob("manual_editable_state.json")
        )
        state = json.loads(state_path.read_bytes())
        observed = {
            row["element_id"]: row["selling_price_units"] for row in state["picks"]
        }
        self.assertEqual(observed, {key: round(value * 10) for key, value in SELLING_PRICES.items()})
        self.assertTrue(
            all(row["current_market_price_units"] is None for row in state["picks"])
        )
        self.assertEqual(state["current_transfer_cost_points"], 0)

    def test_runner_delegates_model_legality_optimization_and_reliability(self) -> None:
        source = inspect.getsource(operational_runner_module)
        forbidden_reimplementations = (
            "optimize_xi(",
            "projection_eligible_for_policy(",
            "analyze_decision_reliability(",
            "gameweek_xfp_v01 =",
            "goal_xfp",
            "assist_xfp",
        )
        for forbidden in forbidden_reimplementations:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn("stages.evaluate_transfer(", source)
        self.assertIn("stages.load_reliability(", source)
        self.assertIn("stages.build_gameweek_decision(", source)


if __name__ == "__main__":
    unittest.main()
