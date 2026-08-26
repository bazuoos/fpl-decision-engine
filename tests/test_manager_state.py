from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fpl_decision_engine.__main__ import build_parser
from fpl_decision_engine import manager_decision as manager_decision_module
from fpl_decision_engine.decision import optimize_squad
from fpl_decision_engine.manager_decision import (
    CURRENT_SQUAD_CLASSIFICATION,
    DIFFERENCE_CLASSIFICATION,
    TRANSFER_NOT_PERFORMED,
    ManagerDecisionError,
    ManagerDecisionOutputExistsError,
    evaluate_current_squad,
    manager_decision_payload,
    write_manager_decision,
)
from fpl_decision_engine.manager_state import (
    FPL_BOOTSTRAP_URL,
    FPL_ENTRY_HISTORY_URL,
    FPL_ENTRY_TRANSFERS_URL,
    FPL_ENTRY_URL,
    FPL_EVENT_PICKS_URL,
    FRESHNESS_WARNING,
    POST_DEADLINE_WARNING,
    ManagerPick,
    ManagerSource,
    ManagerStateError,
    ManagerStateOutputExistsError,
    PublicFPLManagerStateProvider,
    PublicManagerState,
)
from fpl_decision_engine.projection_provider import (
    ProjectionDataset,
    ProjectionPlayer,
    ProjectionState,
)


ENTRY_ID = 12345
SEASON = "2026-27"
EVENT = 2
NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
PICK_ORDER = (1, 3, 4, 5, 8, 9, 10, 11, 13, 14, 15, 2, 6, 7, 12)


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


class RecordingOpener:
    def __init__(self, responses: dict[str, bytes]) -> None:
        self.responses = responses
        self.calls: list[tuple[object, dict[str, object]]] = []

    def __call__(self, endpoint: object, **kwargs: object) -> FakeResponse:
        self.calls.append((endpoint, kwargs))
        if not isinstance(endpoint, str) or endpoint not in self.responses:
            return FakeResponse(b"{}", status=404)
        return FakeResponse(self.responses[endpoint])


def position_for_id(player_id: int) -> tuple[int, str]:
    if player_id <= 2:
        return 1, "GK"
    if player_id <= 7:
        return 2, "DEF"
    if player_id <= 12:
        return 3, "MID"
    return 4, "FWD"


def raw_payloads() -> dict[str, object]:
    elements = [
        {
            "id": player_id,
            "team": ((player_id - 1) % 6) + 1,
            "element_type": position_for_id(player_id)[0],
        }
        for player_id in range(1, 16)
    ]
    picks = [
        {
            "element": player_id,
            "position": position,
            "multiplier": 2 if player_id == 15 else (1 if position <= 11 else 0),
            "is_captain": player_id == 15,
            "is_vice_captain": player_id == 14,
        }
        for position, player_id in enumerate(PICK_ORDER, start=1)
    ]
    return {
        "bootstrap": {
            "elements": elements,
            "events": [
                {"id": 1, "deadline_time": "2026-08-10T17:30:00Z"},
                {"id": EVENT, "deadline_time": "2026-08-20T17:30:00Z"},
                {"id": 3, "deadline_time": "2026-08-28T17:30:00Z"},
            ],
        },
        "entry": {"id": ENTRY_ID, "name": "Synthetic Entry"},
        "history": {
            "current": [{"event": EVENT, "points": 42}],
            "past": [],
            "chips": [{"name": "wildcard", "event": 1, "time": "2026-08-01T00:00:00Z"}],
        },
        "transfers": [
            {
                "event": 1,
                "element_in": 15,
                "element_out": 16,
                "element_in_cost": 70,
                "element_out_cost": 65,
                "time": "2026-08-01T00:00:00Z",
            }
        ],
        "picks": {
            "active_chip": None,
            "automatic_subs": [],
            "entry_history": {"event": EVENT, "bank": 15, "value": 1005},
            "picks": picks,
        },
    }


def response_map(payloads: dict[str, object]) -> dict[str, bytes]:
    picks_endpoint = FPL_EVENT_PICKS_URL.format(entry_id=ENTRY_ID, event_id=EVENT)
    return {
        FPL_BOOTSTRAP_URL: json.dumps(payloads["bootstrap"]).encode(),
        FPL_ENTRY_URL.format(entry_id=ENTRY_ID): json.dumps(payloads["entry"]).encode(),
        FPL_ENTRY_HISTORY_URL.format(entry_id=ENTRY_ID): json.dumps(payloads["history"]).encode(),
        FPL_ENTRY_TRANSFERS_URL.format(entry_id=ENTRY_ID): json.dumps(payloads["transfers"]).encode(),
        picks_endpoint: json.dumps(payloads["picks"]).encode(),
    }


def projection_player(
    player_id: int,
    *,
    state: ProjectionState = ProjectionState.VALID,
    projection: float | None = None,
) -> ProjectionPlayer:
    position_id, position = position_for_id(player_id)
    value = player_id / 10 if projection is None and state is ProjectionState.VALID else projection
    if state is ProjectionState.VERIFIED_BLANK:
        value = 0.0
    return ProjectionPlayer(
        season=SEASON,
        target_gameweek=EVENT,
        fpl_player_id=player_id,
        player_name=f"Player {player_id}",
        team_id=((player_id - 1) % 6) + 1,
        team_name=f"Team {((player_id - 1) % 6) + 1}",
        team_short_name=f"T{((player_id - 1) % 6) + 1}",
        position_id=position_id,
        position=position,
        price_units=50,
        projection=value,
        projection_state=state,
        verified_blank=state is ProjectionState.VERIFIED_BLANK,
        availability_status="a",
        chance_of_playing_next_round=None,
        source_model_id="xfp_v01",
        model_scope="modeled_components_only",
        source_artifact_path="/immutable/projection.parquet",
        source_artifact_sha256="a" * 64,
    )


def projections(
    rows: tuple[ProjectionPlayer, ...] | None = None, *, event: int = EVENT
) -> ProjectionDataset:
    return ProjectionDataset(
        season=SEASON,
        target_gameweek=event,
        snapshot_timestamp="20260820T120000.000000Z",
        provider_id="synthetic-provider",
        provider_version="v1",
        source_model_id="xfp_v01",
        model_scope="modeled_components_only",
        source_artifact_path="/immutable/projection.parquet",
        source_artifact_sha256="a" * 64,
        players_artifact_path="/immutable/players.parquet",
        players_artifact_sha256="b" * 64,
        players=rows or tuple(projection_player(player_id) for player_id in range(1, 16)),
    )


def public_state(
    root: Path,
    *,
    active_chip: str | None = None,
    picks: tuple[ManagerPick, ...] | None = None,
) -> PublicManagerState:
    manager_picks = picks or tuple(
        ManagerPick(
            element_id=player_id,
            pick_position=position,
            multiplier=2 if player_id == 15 else (1 if position <= 11 else 0),
            is_captain=player_id == 15,
            is_vice_captain=player_id == 14,
            team_id=((player_id - 1) % 6) + 1,
            position=position_for_id(player_id)[1],
        )
        for position, player_id in enumerate(PICK_ORDER, start=1)
    )
    raw_directory = root / "20260826T120000.000000Z"
    return PublicManagerState(
        version="public-manager-state-v1",
        season=SEASON,
        entry_id=ENTRY_ID,
        represented_event=EVENT,
        deadline_time="2026-08-20T17:30:00Z",
        retrieval_timestamp="2026-08-26T12:00:00.000000Z",
        state_semantics="manager_state_as_of_event_deadline",
        freshness_warning=FRESHNESS_WARNING,
        post_deadline_warning=POST_DEADLINE_WARNING,
        picks=manager_picks,
        manager_xi=tuple(row.element_id for row in manager_picks if row.pick_position <= 11),
        manager_bench=tuple(row.element_id for row in manager_picks if row.pick_position >= 12),
        manager_captain=15,
        manager_vice_captain=14,
        event_bank_units=15,
        event_team_value_units=1005,
        active_chip=active_chip,
        chip_history=(),
        transfer_history=(),
        field_classification={"manager_picks": "as_of_last_deadline_public"},
        unavailable_public_fields=("current_editable_squad", "free_transfer_count"),
        transfer_recommendation_status="not_available_in_public_manager_state_v1",
        sources=(
            ManagerSource("picks", "https://fantasy.premierleague.com/api/test", "GET", "picks.json", "c" * 64),
        ),
        raw_directory=raw_directory,
        manifest_path=raw_directory / "manager_state_manifest.json",
        manifest_sha256="d" * 64,
    )


class ManagerStateProviderTests(unittest.TestCase):
    def test_evaluate_entry_cli_uses_generic_explicit_arguments(self) -> None:
        args = build_parser().parse_args(
            [
                "evaluate-entry",
                "--entry-id",
                str(ENTRY_ID),
                "--season",
                SEASON,
                "--event",
                str(EVENT),
                "--target-gameweek",
                str(EVENT),
            ]
        )
        self.assertEqual(args.command, "evaluate-entry")
        self.assertEqual((args.entry_id, args.event, args.target_gameweek), (ENTRY_ID, EVENT, EVENT))

    def test_provider_uses_only_five_official_public_gets_and_persists_exact_bytes(self) -> None:
        payloads = raw_payloads()
        responses = response_map(payloads)
        opener = RecordingOpener(responses)
        with tempfile.TemporaryDirectory() as temporary:
            state = PublicFPLManagerStateProvider(
                raw_data_root=Path(temporary), opener=opener
            ).fetch(entry_id=ENTRY_ID, season=SEASON, now=NOW)
            expected_endpoints = set(responses)
            self.assertEqual({call[0] for call in opener.calls}, expected_endpoints)
            self.assertEqual(len(opener.calls), 5)
            for endpoint, kwargs in opener.calls:
                self.assertIsInstance(endpoint, str)
                self.assertTrue(str(endpoint).startswith("https://fantasy.premierleague.com/api/"))
                self.assertEqual(kwargs, {"timeout": 30})
            self.assertTrue(all(source.method == "GET" for source in state.sources))
            for source in state.sources:
                body = responses[source.endpoint]
                raw_path = state.raw_directory / source.raw_path
                self.assertEqual(raw_path.read_bytes(), body)
                self.assertEqual(source.sha256, hashlib.sha256(body).hexdigest())

    def test_latest_locked_event_and_public_field_semantics(self) -> None:
        opener = RecordingOpener(response_map(raw_payloads()))
        with tempfile.TemporaryDirectory() as temporary:
            state = PublicFPLManagerStateProvider(
                raw_data_root=Path(temporary), opener=opener
            ).fetch(entry_id=ENTRY_ID, season=SEASON, now=NOW)
            self.assertEqual(state.represented_event, EVENT)
            self.assertEqual(state.deadline_time, "2026-08-20T17:30:00Z")
            self.assertEqual(state.manager_xi, PICK_ORDER[:11])
            self.assertEqual(state.manager_bench, PICK_ORDER[11:])
            self.assertEqual((state.manager_captain, state.manager_vice_captain), (15, 14))
            self.assertEqual((state.event_bank_units, state.event_team_value_units), (15, 1005))
            self.assertEqual(len(state.picks), 15)
            self.assertEqual(len(state.transfer_history), 1)
            self.assertEqual(len(state.chip_history), 1)
            self.assertEqual(state.field_classification["entry_summary"], "current_live_public")
            self.assertEqual(
                state.field_classification["manager_picks"],
                "as_of_last_deadline_public",
            )
            self.assertIn("free_transfer_count", state.unavailable_public_fields)
            self.assertEqual(state.freshness_warning, FRESHNESS_WARNING)

    def test_duplicate_pick_is_refused(self) -> None:
        payloads = raw_payloads()
        payloads["picks"]["picks"][-1]["element"] = PICK_ORDER[0]  # type: ignore[index]
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
            ManagerStateError, "duplicate"
        ):
            PublicFPLManagerStateProvider(
                raw_data_root=Path(temporary), opener=RecordingOpener(response_map(payloads))
            ).fetch(entry_id=ENTRY_ID, season=SEASON, now=NOW)

    def test_position_composition_is_validated_from_bootstrap(self) -> None:
        payloads = raw_payloads()
        payloads["bootstrap"]["elements"][11]["element_type"] = 4  # type: ignore[index]
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
            ManagerStateError, "positions"
        ):
            PublicFPLManagerStateProvider(
                raw_data_root=Path(temporary), opener=RecordingOpener(response_map(payloads))
            ).fetch(entry_id=ENTRY_ID, season=SEASON, now=NOW)

    def test_captain_and_vice_must_be_distinct_starters(self) -> None:
        payloads = raw_payloads()
        picks = payloads["picks"]["picks"]  # type: ignore[index]
        next(row for row in picks if row["element"] == 14)["is_vice_captain"] = False
        next(row for row in picks if row["element"] == 15)["is_vice_captain"] = True
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
            ManagerStateError, "distinct"
        ):
            PublicFPLManagerStateProvider(
                raw_data_root=Path(temporary), opener=RecordingOpener(response_map(payloads))
            ).fetch(entry_id=ENTRY_ID, season=SEASON, now=NOW)

    def test_player_absent_from_bootstrap_fails_loudly(self) -> None:
        payloads = raw_payloads()
        payloads["picks"]["picks"][-1]["element"] = 999  # type: ignore[index]
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
            ManagerStateError, "does not resolve"
        ):
            PublicFPLManagerStateProvider(
                raw_data_root=Path(temporary), opener=RecordingOpener(response_map(payloads))
            ).fetch(entry_id=ENTRY_ID, season=SEASON, now=NOW)

    def test_manager_raw_artifact_refuses_overwrite(self) -> None:
        responses = response_map(raw_payloads())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            provider = PublicFPLManagerStateProvider(
                raw_data_root=root, opener=RecordingOpener(responses)
            )
            state = provider.fetch(entry_id=ENTRY_ID, season=SEASON, now=NOW)
            before = {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in state.raw_directory.iterdir()
            }
            with self.assertRaises(ManagerStateOutputExistsError):
                PublicFPLManagerStateProvider(
                    raw_data_root=root, opener=RecordingOpener(responses)
                ).fetch(entry_id=ENTRY_ID, season=SEASON, now=NOW)
            self.assertEqual(
                before,
                {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before},
            )


class ManagerDecisionTests(unittest.TestCase):
    def test_projection_event_mismatch_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
            ManagerDecisionError, "mismatch"
        ):
            evaluate_current_squad(
                public_state(Path(temporary)), projections(event=EVENT + 1)
            )

    def test_unresolved_projection_player_fails_loudly(self) -> None:
        rows = tuple(projection_player(player_id) for player_id in range(1, 15))
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
            ManagerDecisionError, "unresolved"
        ):
            evaluate_current_squad(public_state(Path(temporary)), projections(rows))

    def test_team_or_position_mismatch_fails_same_season_reconciliation(self) -> None:
        rows = tuple(
            replace(projection_player(player_id), team_id=99)
            if player_id == 1 else projection_player(player_id)
            for player_id in range(1, 16)
        )
        with tempfile.TemporaryDirectory() as temporary, self.assertRaisesRegex(
            ManagerDecisionError, "differs"
        ):
            evaluate_current_squad(public_state(Path(temporary)), projections(rows))

    def test_incomplete_and_missing_owned_players_block_without_zero_imputation(self) -> None:
        rows = tuple(
            projection_player(1, state=ProjectionState.INCOMPLETE, projection=999.0)
            if player_id == 1
            else projection_player(2, state=ProjectionState.MISSING, projection=None)
            if player_id == 2
            else projection_player(player_id)
            for player_id in range(1, 16)
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = evaluate_current_squad(public_state(Path(temporary)), projections(rows))
            self.assertIsNone(result.optimized_result)
            self.assertIsNone(result.modeled_component_projection_difference)
            self.assertEqual(result.incomplete_owned_player_ids, (1,))
            self.assertEqual(result.missing_owned_player_ids, (2,))

    def test_verified_blank_remains_eligible_explicit_zero(self) -> None:
        rows = tuple(
            projection_player(1, state=ProjectionState.VERIFIED_BLANK)
            if player_id == 1 else projection_player(player_id)
            for player_id in range(1, 16)
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = evaluate_current_squad(public_state(Path(temporary)), projections(rows))
            self.assertIsNotNone(result.optimized_result)
            reconciled = next(row for row in result.reconciliation if row["element_id"] == 1)
            self.assertEqual(reconciled["projection_status"], "verified_blank")
            self.assertEqual(reconciled["projection"], 0.0)

    def test_reuses_task014_fixed_squad_optimizer_and_comparison_arithmetic(self) -> None:
        data = projections()
        with tempfile.TemporaryDirectory() as temporary, patch(
            "fpl_decision_engine.manager_decision.optimize_xi",
            wraps=manager_decision_module.optimize_xi,
        ) as optimize:
            result = evaluate_current_squad(public_state(Path(temporary)), data)
            optimize.assert_called_once()
            self.assertIsNotNone(result.manager_score)
            self.assertIsNotNone(result.optimized_result)
            self.assertAlmostEqual(
                result.modeled_component_projection_difference,
                result.optimized_result.total_objective - result.manager_score.total_objective,
            )
            self.assertTrue(any(change.startswith("START ") for change in result.change_list))
            self.assertEqual(result.comparison_status, "complete_standard_objective_comparison")

    def test_chip_scoring_limitation_prevents_apples_to_apples_delta(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = evaluate_current_squad(
                public_state(Path(temporary), active_chip="bboost"), projections()
            )
            self.assertIsNotNone(result.optimized_result)
            self.assertIsNone(result.modeled_component_projection_difference)
            self.assertIn("active_chip", result.comparison_status)
            self.assertIn("bboost", result.chip_limitation)

    def test_payload_always_has_freshness_transfer_boundary_and_benchmark_label(self) -> None:
        data = projections()
        benchmark = optimize_squad(data, budget_units=750)
        with tempfile.TemporaryDirectory() as temporary:
            result = evaluate_current_squad(
                public_state(Path(temporary)), data, unconstrained_benchmark=benchmark
            )
            payload = manager_decision_payload(result)
            self.assertEqual(payload["classification"], CURRENT_SQUAD_CLASSIFICATION)
            self.assertEqual(payload["freshness_warning"], FRESHNESS_WARNING)
            self.assertEqual(payload["post_deadline_warning"], POST_DEADLINE_WARNING)
            self.assertEqual(payload["transfer_boundary"], TRANSFER_NOT_PERFORMED)
            self.assertEqual(
                payload["manager_vs_engine"]["difference_classification"],
                DIFFERENCE_CLASSIFICATION,
            )
            self.assertTrue(payload["unconstrained_benchmark"]["not_a_transfer_plan"])

    def test_manager_decision_artifact_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = evaluate_current_squad(public_state(root), projections())
            artifacts = write_manager_decision(result, decision_data_root=root / "decisions")
            before = artifacts.manifest_path.read_bytes()
            with self.assertRaises(ManagerDecisionOutputExistsError):
                write_manager_decision(result, decision_data_root=root / "decisions")
            self.assertEqual(before, artifacts.manifest_path.read_bytes())

    def test_actual_entry_is_not_committed_in_readme_or_tests(self) -> None:
        forbidden = "664" + "4775"
        paths = [Path("README.md"), *Path("tests").glob("test_*.py")]
        for path in paths:
            self.assertNotIn(forbidden, path.read_text(encoding="utf-8"), path)


if __name__ == "__main__":
    unittest.main()
