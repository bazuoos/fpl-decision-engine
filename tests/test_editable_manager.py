from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fpl_decision_engine.__main__ import build_parser
from fpl_decision_engine.decision import (
    APPEARANCE_ONLY_ALLOWED_POLICY,
    DEFAULT_DECISION_POLICY,
    optimize_xi,
    rank_players,
    write_decision_artifacts,
)
from fpl_decision_engine.editable_manager import (
    BENCH_ORDER_SEMANTICS,
    MANUAL_VERIFICATION_SOURCE,
    MODEL_CAVEAT,
    TRANSFER_BLOCKED_STATUS,
    UNCONSTRAINED_BENCHMARK_CLASSIFICATION,
    EditableManagerError,
    EditableManagerOutputExistsError,
    ManualEditablePick,
    create_manual_editable_state,
    editable_decision_payload,
    evaluate_editable_squad,
    load_task014_benchmark,
    write_editable_decision,
)
from fpl_decision_engine.projection_provider import (
    ProjectionDataset,
    ProjectionPlayer,
    ProjectionState,
    XfpV01ParquetProvider,
    sha256_file,
)


POSITIONS = (
    (1, "GK"),
    (2, "GK"),
    *((player_id, "DEF") for player_id in range(3, 8)),
    *((player_id, "MID") for player_id in range(8, 13)),
    *((player_id, "FWD") for player_id in range(13, 16)),
)


def player(player_id: int, position: str) -> ProjectionPlayer:
    position_id = {"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}[position]
    return ProjectionPlayer(
        season="2026-27",
        target_gameweek=2,
        fpl_player_id=player_id,
        player_name=f"Player {player_id}",
        team_id=(player_id - 1) % 8 + 1,
        team_name=f"Team {(player_id - 1) % 8 + 1}",
        team_short_name=f"T{(player_id - 1) % 8 + 1}",
        position_id=position_id,
        position=position,
        price_units=50,
        projection=float(player_id),
        projection_state=ProjectionState.VALID,
        verified_blank=False,
        availability_status="a",
        chance_of_playing_next_round=None,
        source_model_id="xfp_v01",
        model_scope="modeled_components_only",
        source_artifact_path="/frozen/gw2.parquet",
        source_artifact_sha256="a" * 64,
    )


def dataset(rows: tuple[ProjectionPlayer, ...] | None = None) -> ProjectionDataset:
    players = rows or tuple(player(player_id, position) for player_id, position in POSITIONS)
    return ProjectionDataset(
        season="2026-27",
        target_gameweek=2,
        snapshot_timestamp="20260825T073532.450889Z",
        provider_id="xfp_v01_parquet_v1",
        provider_version="projection-provider-v1",
        source_model_id="xfp_v01",
        model_scope="modeled_components_only",
        source_artifact_path="/frozen/gw2.parquet",
        source_artifact_sha256="a" * 64,
        players_artifact_path="/frozen/players.parquet",
        players_artifact_sha256="b" * 64,
        players=players,
    )


def manual_picks(*, with_market_prices: bool = False) -> tuple[ManualEditablePick, ...]:
    return tuple(
        ManualEditablePick(
            element_id=player_id,
            display_name=f"Player {player_id}",
            position=position,
            current_market_price_units=50 if with_market_prices else None,
        )
        for player_id, position in POSITIONS
    )


class EditableManagerTests(unittest.TestCase):
    def make_state(
        self,
        root: Path,
        *,
        picks: tuple[ManualEditablePick, ...] | None = None,
        gameweek: int = 2,
        third_party: dict[str, object] | None = None,
        microsecond: int = 0,
    ):
        return create_manual_editable_state(
            entry_id=12345,
            season="2026-27",
            target_gameweek=gameweek,
            picks=picks or manual_picks(),
            bank_m="0.0",
            free_transfers=1,
            current_transfer_cost_points=0,
            post_deadline_transfers_known=True,
            verification_source=MANUAL_VERIFICATION_SOURCE,
            verification_timestamp=None,
            third_party_price_change_metadata=third_party,
            manual_data_root=root / "manual",
            recorded_at=datetime(2026, 8, 26, 1, 2, 3, microsecond, tzinfo=timezone.utc),
        )

    def make_benchmark(self, root: Path, projections: ProjectionDataset):
        result = optimize_xi(projections.players, budget_units=1000)
        artifacts = write_decision_artifacts(
            projections,
            rank_players(projections),
            result,
            decision_data_root=root / "decisions",
            generation_timestamp="20260826T010203.000000Z",
        )
        return load_task014_benchmark(artifacts.manifest_path, projections), artifacts

    def test_manual_state_validates_money_ft_provenance_and_composition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.make_state(root)
            self.assertEqual(state.bank_units, 0)
            self.assertEqual(state.free_transfers, 1)
            self.assertEqual(state.current_transfer_cost_points, 0)
            self.assertTrue(state.post_deadline_transfers_known)
            self.assertIsNone(state.verification_timestamp)
            self.assertEqual(len(state.picks), 15)
            payload = json.loads(state.artifact_path.read_text())
            self.assertIn("distinct from public locked-deadline", payload["provenance_boundary"])

            with self.assertRaises(EditableManagerError):
                self.make_state(root, picks=manual_picks()[:-1], microsecond=1)
            with self.assertRaises(EditableManagerError):
                self.make_state(
                    root,
                    picks=manual_picks()[:-1] + (manual_picks()[0],),
                    microsecond=2,
                )
            wrong = list(manual_picks())
            wrong[-1] = replace(wrong[-1], position="MID")
            with self.assertRaises(EditableManagerError):
                self.make_state(root, picks=tuple(wrong), microsecond=3)

    def test_bank_and_free_transfer_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kwargs = dict(
                entry_id=12345,
                season="2026-27",
                target_gameweek=2,
                picks=manual_picks(),
                current_transfer_cost_points=0,
                post_deadline_transfers_known=True,
                manual_data_root=root,
                recorded_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
            )
            with self.assertRaises(EditableManagerError):
                create_manual_editable_state(bank_m="0.05", free_transfers=1, **kwargs)
            with self.assertRaises(EditableManagerError):
                create_manual_editable_state(bank_m="0.0", free_transfers=-1, **kwargs)

    def test_strict_manual_projection_gameweek_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = self.make_state(root, gameweek=3)
            benchmark, _ = self.make_benchmark(root, dataset())
            with self.assertRaisesRegex(EditableManagerError, "do not align exactly"):
                evaluate_editable_squad(state, dataset(), benchmark)

    def test_all_players_reconcile_and_fixed_squad_optimizer_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projections = dataset()
            state = self.make_state(root)
            benchmark, _ = self.make_benchmark(root, projections)
            with patch(
                "fpl_decision_engine.editable_manager.optimize_xi",
                wraps=optimize_xi,
            ) as fixed_optimizer:
                result = evaluate_editable_squad(state, projections, benchmark)
            fixed_optimizer.assert_called_once()
            self.assertEqual(result.projection_coverage_pct, 100.0)
            self.assertEqual(result.reconciliation_counts["valid_projection"], 15)
            self.assertIsNotNone(result.optimized_result)

    def test_incomplete_and_missing_are_not_changed_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = dataset()
            benchmark, _ = self.make_benchmark(root, valid)
            rows = list(valid.players)
            rows[0] = replace(
                rows[0], projection=0.0, projection_state=ProjectionState.INCOMPLETE,
                expected_minutes=0.0,
            )
            rows[1] = replace(
                rows[1], projection=None, projection_state=ProjectionState.MISSING
            )
            result = evaluate_editable_squad(
                self.make_state(root), dataset(tuple(rows)), benchmark
            )
            self.assertIsNone(result.optimized_result)
            by_id = {row["element_id"]: row for row in result.reconciliation}
            self.assertEqual(by_id[1]["projection"], 0.0)
            self.assertIsNone(by_id[2]["projection"])
            self.assertFalse(by_id[1]["usable_projection"])
            self.assertFalse(by_id[2]["usable_projection"])

    def test_appearance_only_is_explicit_and_preserves_incomplete_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = dataset()
            benchmark, _ = self.make_benchmark(root, valid)
            rows = list(valid.players)
            rows[0] = replace(
                rows[0], projection=0.0, projection_state=ProjectionState.INCOMPLETE,
                expected_minutes=0.0,
            )
            projections = dataset(tuple(rows))
            state = self.make_state(root)

            strict = evaluate_editable_squad(state, projections, benchmark)
            self.assertEqual(strict.decision_policy, DEFAULT_DECISION_POLICY)
            self.assertIsNone(strict.optimized_result)

            appearance_only = evaluate_editable_squad(
                state,
                projections,
                benchmark,
                decision_policy=APPEARANCE_ONLY_ALLOWED_POLICY,
            )
            self.assertIsNotNone(appearance_only.optimized_result)
            by_id = {row["element_id"]: row for row in appearance_only.reconciliation}
            self.assertEqual(by_id[1]["projection_state"], "incomplete_projection")
            self.assertFalse(by_id[1]["usable_projection"])
            self.assertTrue(by_id[1]["decision_policy_eligible"])
            payload = editable_decision_payload(appearance_only)
            diagnostics = payload["incomplete_projection_policy_diagnostics"]
            self.assertEqual(diagnostics["incomplete_projections_admitted_to_squad"], 1)
            self.assertEqual(diagnostics["selected_in_starting_xi"], [])
            self.assertEqual(diagnostics["total_objective_contribution"], 0.0)
            self.assertFalse(payload["decision_policy_is_default"])

    def test_appearance_only_rejects_null_missing_and_nonfinite_projections(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = dataset()
            benchmark, _ = self.make_benchmark(root, valid)
            for index, projection, state_name in (
                (0, None, ProjectionState.MISSING),
                (1, float("nan"), ProjectionState.INCOMPLETE),
                (2, float("inf"), ProjectionState.INCOMPLETE),
            ):
                rows = list(valid.players)
                rows[index] = replace(
                    rows[index], projection=projection, projection_state=state_name,
                    expected_minutes=0.0,
                )
                result = evaluate_editable_squad(
                    self.make_state(root, microsecond=index + 1),
                    dataset(tuple(rows)),
                    benchmark,
                    decision_policy=APPEARANCE_ONLY_ALLOWED_POLICY,
                )
                self.assertIsNone(result.optimized_result)
                self.assertLess(result.decision_policy_coverage_pct, 100.0)

    def test_positive_incomplete_projection_is_used_without_reclassification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = dataset()
            benchmark, _ = self.make_benchmark(root, valid)
            rows = list(valid.players)
            rows[-1] = replace(
                rows[-1], projection=25.0, projection_state=ProjectionState.INCOMPLETE,
                expected_minutes=0.0,
            )
            result = evaluate_editable_squad(
                self.make_state(root),
                dataset(tuple(rows)),
                benchmark,
                decision_policy=APPEARANCE_ONLY_ALLOWED_POLICY,
            )
            optimized = result.optimized_result
            self.assertIsNotNone(optimized)
            self.assertEqual(optimized.captain.fpl_player_id, 15)
            selected = next(
                row for row in optimized.selections if row.player.fpl_player_id == 15
            )
            self.assertEqual(selected.player.projection_state, ProjectionState.INCOMPLETE)
            payload = editable_decision_payload(result)
            diagnostics = payload["incomplete_projection_policy_diagnostics"]
            self.assertEqual(diagnostics["selected_in_starting_xi"][0]["element_id"], 15)
            self.assertEqual(diagnostics["incomplete_captain"]["element_id"], 15)
            self.assertEqual(diagnostics["total_objective_contribution"], 50.0)

    def test_appearance_only_runs_are_deterministic_and_cli_default_is_strict(self) -> None:
        parser = build_parser()
        required = [
            "evaluate-editable-squad",
            "--entry-id", "1",
            "--target-gameweek", "2",
            "--player", "1:GK:One",
            "--bank", "0.0",
            "--free-transfers", "1",
            "--benchmark-manifest", "/tmp/benchmark.json",
        ]
        self.assertEqual(parser.parse_args(required).decision_policy, DEFAULT_DECISION_POLICY)
        explicit = parser.parse_args(
            required + ["--decision-policy", APPEARANCE_ONLY_ALLOWED_POLICY]
        )
        self.assertEqual(explicit.decision_policy, APPEARANCE_ONLY_ALLOWED_POLICY)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = dataset()
            benchmark, _ = self.make_benchmark(root, valid)
            rows = list(valid.players)
            rows[0] = replace(
                rows[0], projection=0.0, projection_state=ProjectionState.INCOMPLETE,
                expected_minutes=0.0,
            )
            projections = dataset(tuple(rows))
            first = evaluate_editable_squad(
                self.make_state(root),
                projections,
                benchmark,
                decision_policy=APPEARANCE_ONLY_ALLOWED_POLICY,
            )
            second = evaluate_editable_squad(
                self.make_state(root, microsecond=1),
                projections,
                benchmark,
                decision_policy=APPEARANCE_ONLY_ALLOWED_POLICY,
            )
            self.assertEqual(first.bench_order, second.bench_order)
            self.assertEqual(
                first.optimized_result.total_objective,
                second.optimized_result.total_objective,
            )

    def test_verified_blank_is_an_explicit_usable_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            valid = dataset()
            benchmark, _ = self.make_benchmark(root, valid)
            rows = list(valid.players)
            rows[0] = replace(
                rows[0],
                projection=0.0,
                projection_state=ProjectionState.VERIFIED_BLANK,
                verified_blank=True,
            )
            result = evaluate_editable_squad(
                self.make_state(root), dataset(tuple(rows)), benchmark
            )
            self.assertIsNotNone(result.optimized_result)
            self.assertEqual(result.reconciliation_counts["verified_blank"], 1)

    def test_formation_captain_vice_and_bench_reporting_order_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projections = dataset()
            benchmark, _ = self.make_benchmark(root, projections)
            first = evaluate_editable_squad(self.make_state(root), projections, benchmark)
            second = evaluate_editable_squad(
                self.make_state(root, microsecond=1), projections, benchmark
            )
            self.assertEqual(first.bench_order, second.bench_order)
            self.assertEqual(len(first.bench_order), 4)
            optimized = first.optimized_result
            self.assertIsNotNone(optimized)
            self.assertNotEqual(
                optimized.captain.fpl_player_id, optimized.vice_captain.fpl_player_id
            )
            self.assertRegex(optimized.formation, r"^[3-5]-[2-5]-[1-3]$")
            payload = editable_decision_payload(first)
            self.assertEqual(
                payload["fixed_squad_optimization"]["bench_order_semantics"],
                BENCH_ORDER_SEMANTICS,
            )

    def test_market_prices_and_third_party_metadata_cannot_enable_transfers_or_change_xi(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projections = dataset()
            benchmark, _ = self.make_benchmark(root, projections)
            plain = evaluate_editable_squad(
                self.make_state(root), projections, benchmark
            )
            noisy = evaluate_editable_squad(
                self.make_state(
                    root,
                    picks=manual_picks(with_market_prices=True),
                    third_party={"Player 15": {"rise_probability_pct": 99.9}},
                    microsecond=1,
                ),
                projections,
                benchmark,
            )
            self.assertEqual(noisy.transfer_feasibility_status, TRANSFER_BLOCKED_STATUS)
            self.assertEqual(plain.bench_order, noisy.bench_order)
            self.assertEqual(
                plain.optimized_result.total_objective,
                noisy.optimized_result.total_objective,
            )
            payload = editable_decision_payload(noisy)
            self.assertFalse(
                payload["transfer_analysis"]["current_market_price_used_as_sell_price"]
            )
            self.assertEqual(
                payload["transfer_analysis"]["third_party_price_change_metadata_effect"],
                "none",
            )

    def test_unverified_sell_values_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = list(manual_picks())
            rows[0] = replace(rows[0], selling_price_units=50)
            with self.assertRaisesRegex(EditableManagerError, "unverified selling"):
                self.make_state(root, picks=tuple(rows))

    def test_benchmark_is_informational_and_objective_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projections = dataset()
            benchmark, artifacts = self.make_benchmark(root, projections)
            result = evaluate_editable_squad(
                self.make_state(root), projections, benchmark
            )
            payload = editable_decision_payload(result)
            self.assertEqual(
                payload["unconstrained_benchmark"]["classification"],
                UNCONSTRAINED_BENCHMARK_CLASSIFICATION,
            )
            self.assertTrue(payload["unconstrained_benchmark"]["not_a_transfer_plan"])
            self.assertIn("not expected total FPL points", MODEL_CAVEAT)

            manifest = json.loads(artifacts.manifest_path.read_text())
            manifest["total_objective"] += 1.0
            artifacts.manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(EditableManagerError, "proven objective"):
                load_task014_benchmark(artifacts.manifest_path, projections)

    def test_manual_and_decision_artifacts_refuse_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            projections = dataset()
            state = self.make_state(root)
            benchmark, _ = self.make_benchmark(root, projections)
            result = evaluate_editable_squad(state, projections, benchmark)
            artifacts = write_editable_decision(
                result, decision_data_root=root / "manager-decisions"
            )
            original = artifacts.decision_path.read_bytes()
            with self.assertRaises(EditableManagerOutputExistsError):
                write_editable_decision(
                    result, decision_data_root=root / "manager-decisions"
                )
            self.assertEqual(artifacts.decision_path.read_bytes(), original)
            with self.assertRaises(EditableManagerOutputExistsError):
                self.make_state(root)

    def test_frozen_gw2_appearance_only_result_and_protected_hashes(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        snapshot = "20260825T073532.450889Z"
        manager_manifests = list(
            repository.glob(
                "data/manager/raw/fpl/2026-27/entry=*/"
                "20260826T121828.264443Z/manager_state_manifest.json"
            )
        )
        manual_states = list(
            repository.glob(
                "data/manager/manual/fpl/2026-27/entry=*/gameweek=2/"
                "20260826T124307.527374Z/manual_editable_state.json"
            )
        )
        if len(manager_manifests) != 1 or len(manual_states) != 1:
            self.skipTest("local frozen GW2 manager review artifacts are not available")
        protected = {
            repository
            / f"data/features/fpl/2026-27/{snapshot}/gameweek=2/"
            "player_gameweek_features.parquet": (
                "f7749a924f1223043f2d0d5c3be5004999157cde839a4c379c498e9a0c7a6887"
            ),
            repository
            / f"data/predictions/fpl/2026-27/{snapshot}/gameweek=2/"
            "xfp_v01_fixtures.parquet": (
                "5dc0042ca8e7da6ab96fb87e6bf8ef8b00f75ec8b4e017e68d140070de78c961"
            ),
            repository
            / f"data/predictions/fpl/2026-27/{snapshot}/gameweek=2/"
            "xfp_v01_gameweek.parquet": (
                "105fc489991b568d1d572213f188543fbe8fd07504f0f7845504fa76a3eaa5fc"
            ),
            repository / f"data/clean/fpl/2026-27/{snapshot}/players.parquet": (
                "0ddbe5be615b2e5fc7eeb631035d5b65a382d70bf7e1acf3e9a269ec9cd35589"
            ),
            repository
            / f"data/decisions/fpl/2026-27/{snapshot}/gameweek=2/decision-engine-v2/"
            "20260826T114104.043249Z/decision_manifest.json": (
                "5f851d7890affc3f4784e5f492c32ef054e15df9a1431d202b709d71e15c3633"
            ),
            manager_manifests[0]: (
                "287a6c8cec1f7fff753abd3e6f5b7f5b158427bf277f346acad372432ebde727"
            ),
        }
        manual_path = manual_states[0]
        if not manual_path.is_file() or any(not path.is_file() for path in protected):
            self.skipTest("local frozen GW2 review artifacts are not available")
        before = {path: sha256_file(path) for path in protected}
        self.assertEqual(before, protected)

        manual = json.loads(manual_path.read_text())
        picks = tuple(ManualEditablePick(**row) for row in manual["picks"])
        projection_path = next(
            path for path in protected if path.name == "xfp_v01_gameweek.parquet"
        )
        players_path = next(path for path in protected if path.name == "players.parquet")
        benchmark_path = next(
            path for path in protected if path.name == "decision_manifest.json"
        )
        projections = XfpV01ParquetProvider(
            projection_artifact=projection_path,
            players_artifact=players_path,
        ).load(season="2026-27", target_gameweek=2)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = create_manual_editable_state(
                entry_id=manual["entry_id"],
                season=manual["season"],
                target_gameweek=manual["target_gameweek"],
                picks=picks,
                bank_m=str(manual["bank_units"] / 10),
                free_transfers=manual["free_transfers"],
                current_transfer_cost_points=manual["current_transfer_cost_points"],
                post_deadline_transfers_known=manual["post_deadline_transfers_known"],
                manual_data_root=root,
                recorded_at=datetime(2026, 8, 26, 13, 0, tzinfo=timezone.utc),
            )
            benchmark = load_task014_benchmark(benchmark_path, projections)
            strict = evaluate_editable_squad(state, projections, benchmark)
            self.assertIsNone(strict.optimized_result)

            result = evaluate_editable_squad(
                state,
                projections,
                benchmark,
                decision_policy=APPEARANCE_ONLY_ALLOWED_POLICY,
            )
            optimized = result.optimized_result
            self.assertIsNotNone(optimized)
            starters = [
                row.player.fpl_player_id
                for row in optimized.selections
                if row.is_starter
            ]
            self.assertEqual(
                starters,
                [1, 8, 175, 201, 391, 40, 368, 426, 481, 557, 411],
            )
            self.assertEqual(optimized.formation, "4-5-1")
            self.assertEqual(optimized.captain.fpl_player_id, 40)
            self.assertEqual(optimized.vice_captain.fpl_player_id, 368)
            self.assertAlmostEqual(optimized.base_xi_projection, 40.72, places=2)
            self.assertAlmostEqual(optimized.captain_bonus, 7.10, places=2)
            self.assertAlmostEqual(optimized.total_objective, 47.82, places=2)
            incomplete_ids = {
                row["element_id"]
                for row in result.reconciliation
                if row["projection_state"] == "incomplete_projection"
            }
            self.assertEqual(incomplete_ids, {111, 272, 499})
            self.assertTrue(incomplete_ids.issubset(set(result.bench_order)))
            payload = editable_decision_payload(result)
            diagnostic = payload["incomplete_projection_policy_diagnostics"]
            self.assertEqual(diagnostic["incomplete_projections_admitted_to_squad"], 3)
            self.assertEqual(diagnostic["selected_in_starting_xi"], [])
            self.assertEqual(diagnostic["total_objective_contribution"], 0.0)

        self.assertEqual({path: sha256_file(path) for path in protected}, before)


if __name__ == "__main__":
    unittest.main()
