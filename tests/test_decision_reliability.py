from __future__ import annotations

import math
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from fpl_decision_engine.__main__ import build_parser
from fpl_decision_engine.decision_reliability import (
    DecisionReliabilityError,
    DecisionReliabilityOutputExistsError,
    PlayerReliability,
    _fixed_squad_diagnostic,
    _reconciled_prior_minutes,
    _validated_prior_history_provenance,
    analyze_decision_reliability,
    build_rate_reference,
    build_sensitivity_views,
    build_stability_summary,
    load_reliability_context,
    player_reliability_payload,
    write_decision_reliability,
)
from fpl_decision_engine.projection_provider import sha256_file
from tests.fixture_support import materialized_frozen_gw2


def reliability_player(
    player_id: int,
    position: str,
    rate: float,
) -> PlayerReliability:
    return PlayerReliability(
        fpl_player_id=player_id,
        player_name=f"Player {player_id}",
        position_id={"GK": 1, "DEF": 2, "MID": 3, "FWD": 4}[position],
        position=position,
        projection=2.0,
        projection_state="valid_projection",
        prior_total_minutes=90.0,
        prior_appearances=1,
        prior_starts=1,
        cumulative_prior_xg=rate,
        cumulative_prior_xa=rate / 2,
        prior_xg_per_90=rate,
        prior_xa_per_90=rate / 2,
        low_sample=True,
        prediction_complete=True,
        expected_minutes=90.0,
        appearance_xfp=2.0,
        availability_status="a",
        chance_of_playing_next_round=None,
        availability_news="",
    )


class ReliabilityPrimitiveTests(unittest.TestCase):
    def test_absent_prior_universe_null_history_is_accepted_and_excluded(self) -> None:
        minutes, appearances, starts = _validated_prior_history_provenance(
            feature_minutes=None,
            prediction_minutes=None,
            appearances=0,
            starts=None,
            projection=None,
            prediction_complete=False,
            player_id=611,
        )
        self.assertIsNone(minutes)
        self.assertEqual(appearances, 0)
        self.assertIsNone(starts)
        with self.assertRaisesRegex(
            DecisionReliabilityError, "prior minutes do not reconcile"
        ):
            _reconciled_prior_minutes(None, 0.0, 611)
        with self.assertRaisesRegex(
            DecisionReliabilityError, "prior minutes do not reconcile"
        ):
            _reconciled_prior_minutes(0.0, None, 611)
        players = {
            index: reliability_player(index, position, 0.2)
            for index, position in enumerate(("GK", "DEF", "MID", "FWD"), 1)
        }
        absent = replace(
            reliability_player(611, "DEF", 0.0),
            projection=None,
            projection_state="missing_projection",
            prior_total_minutes=None,
            prior_starts=None,
            prior_xg_per_90=None,
            prior_xa_per_90=None,
            prediction_complete=False,
            expected_minutes=None,
            appearance_xfp=None,
        )
        players[absent.fpl_player_id] = absent
        reference = build_rate_reference(players)
        defender_xg = reference["DEF"]["prior_xg_per_90"]
        self.assertEqual(defender_xg["position_rows_n"], 2)
        self.assertEqual(defender_xg["population_n"], 1)
        self.assertEqual(defender_xg["excluded_null_rate_n"], 1)
        payload = player_reliability_payload(
            absent,
            roles=("diagnostic",),
            players=players,
            reference=reference,
        )
        self.assertIsNone(payload["prior_total_minutes"])
        self.assertIsNone(payload["prior_starts"])
        self.assertFalse(
            payload["rate_diagnostics"]["xg_per_90"][
                "eligible_for_reference_population"
            ]
        )

    def test_unexpected_null_prior_history_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            DecisionReliabilityError, "unexpected null prior-history provenance"
        ):
            _validated_prior_history_provenance(
                feature_minutes=90.0,
                prediction_minutes=90.0,
                appearances=1,
                starts=None,
                projection=2.0,
                prediction_complete=True,
                player_id=42,
            )
        with self.assertRaisesRegex(
            DecisionReliabilityError, "unexpected null prior-history provenance"
        ):
            _validated_prior_history_provenance(
                feature_minutes=None,
                prediction_minutes=None,
                appearances=0,
                starts=None,
                projection=0.0,
                prediction_complete=False,
                player_id=43,
            )

    def test_position_percentile_rank_and_extreme_flag(self) -> None:
        players: dict[int, PlayerReliability] = {}
        player_id = 1
        for position in ("GK", "DEF", "MID", "FWD"):
            for rate in (0.1, 0.2, 1.0):
                players[player_id] = reliability_player(player_id, position, rate)
                player_id += 1
        reference = build_rate_reference(players)
        extreme = player_reliability_payload(
            players[6],
            roles=("official.incoming",),
            players=players,
            reference=reference,
        )
        self.assertAlmostEqual(reference["DEF"]["prior_xg_per_90"]["p95"], 0.92)
        self.assertEqual(
            extreme["rate_diagnostics"]["xg_per_90"]["position_rank_desc"], 1
        )
        self.assertEqual(
            extreme["rate_diagnostics"]["xg_per_90"]["empirical_percentile_pct"],
            100.0,
        )
        self.assertTrue(extreme["unusually_extreme_attacking_rate"])

    def test_rate_populations_exclude_incomplete_and_metric_null_rows(self) -> None:
        players: dict[int, PlayerReliability] = {}
        player_id = 1
        for position in ("GK", "MID", "FWD"):
            for rate in (0.1, 0.2):
                players[player_id] = reliability_player(player_id, position, rate)
                player_id += 1
        complete_both = reliability_player(20, "DEF", 0.1)
        complete_xa_only = replace(
            reliability_player(21, "DEF", 0.4),
            prior_xg_per_90=None,
        )
        complete_xg_only = replace(
            reliability_player(22, "DEF", 0.8),
            prior_xa_per_90=None,
        )
        complete_xa_extra = replace(
            reliability_player(25, "DEF", 0.5),
            prior_xg_per_90=None,
        )
        incomplete_numeric = replace(
            reliability_player(23, "DEF", 99.0),
            prediction_complete=False,
            projection_state="incomplete_projection",
        )
        incomplete_null = replace(
            reliability_player(24, "DEF", 0.0),
            prediction_complete=False,
            projection_state="incomplete_projection",
            prior_xg_per_90=None,
            prior_xa_per_90=None,
        )
        players.update({
            row.fpl_player_id: row
            for row in (
                complete_both,
                complete_xa_only,
                complete_xg_only,
                complete_xa_extra,
                incomplete_numeric,
                incomplete_null,
            )
        })
        reference = build_rate_reference(players)
        defender = reference["DEF"]
        self.assertEqual(defender["prior_xg_per_90"]["population_n"], 2)
        self.assertEqual(defender["prior_xa_per_90"]["population_n"], 3)
        self.assertEqual(
            defender["prior_xg_per_90"]["excluded_incomplete_n"], 2
        )
        self.assertEqual(
            defender["prior_xg_per_90"]["excluded_null_rate_n"], 3
        )
        self.assertEqual(
            defender["prior_xg_per_90"]["defined_incomplete_rate_n"], 1
        )
        self.assertGreater(defender["prior_xg_per_90"]["p95"], 0.1)
        self.assertLess(defender["prior_xg_per_90"]["p95"], 0.8)
        excluded = player_reliability_payload(
            incomplete_numeric,
            roles=("diagnostic",),
            players=players,
            reference=reference,
        )
        xg = excluded["rate_diagnostics"]["xg_per_90"]
        self.assertFalse(xg["eligible_for_reference_population"])
        self.assertEqual(xg["reference_exclusion_reason"], "prediction_incomplete")
        self.assertIsNone(xg["position_rank_desc"])

    def test_nonfinite_attacking_rate_fails_closed(self) -> None:
        players = {
            index: reliability_player(index, position, 0.2)
            for index, position in enumerate(("GK", "DEF", "MID", "FWD"), 1)
        }
        players[2] = replace(players[2], prior_xg_per_90=math.nan)
        with self.assertRaisesRegex(DecisionReliabilityError, "must be finite"):
            build_rate_reference(players)

    def test_stable_recommendation_summary_has_no_confidence_score(self) -> None:
        official = {
            "action": "TRANSFER",
            "outgoing": {"element_id": 1, "name": "Out"},
            "incoming": {"element_id": 2, "name": "In"},
            "objective": 11.0,
            "gain_vs_roll": 1.0,
        }
        views = [
            {
                "recommended_action_under_view": {
                    **official,
                    "objective": 10.5 + index,
                    "gain_vs_roll": 0.5 + index,
                }
            }
            for index in range(3)
        ]
        summary = build_stability_summary(official, views)
        self.assertTrue(summary["recommended_transfer_remains_same_across_all_views"])
        self.assertTrue(summary["incoming_player_remains_same_across_all_views"])
        self.assertEqual(summary["different_action_count"], 0)
        self.assertNotIn("confidence_score", summary)

    def test_missing_linked_provenance_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            decision = root / "one_transfer_decision.json"
            decision.write_text(
                """{
                  "version": "one-transfer-decision-v1",
                  "decision_policy": "appearance_only_allowed",
                  "candidate_summaries_artifact": {
                    "path": "/definitely/missing/candidates.json",
                    "sha256": "0000000000000000000000000000000000000000000000000000000000000000"
                  }
                }""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                DecisionReliabilityError, "candidate artifact hash mismatch"
            ):
                load_reliability_context(decision, root / "features.parquet")

    def test_cli_requires_explicit_immutable_inputs(self) -> None:
        parsed = build_parser().parse_args(
            [
                "analyze-decision-reliability",
                "--decision-artifact",
                "/tmp/decision.json",
                "--feature-artifact",
                "/tmp/features.parquet",
            ]
        )
        self.assertEqual(parsed.command, "analyze-decision-reliability")


class FrozenGW2ReliabilityTests(unittest.TestCase):
    """Exercise Task 017 on exact committed copies of reviewed GW2 source bytes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_manager = materialized_frozen_gw2()
        fixture = cls.fixture_manager.__enter__()
        cls.decision_path = fixture.decision
        cls.candidate_path = fixture.candidates
        cls.feature_path = fixture.features
        cls.projection_path = fixture.gameweek_predictions
        cls.fixture_prediction_path = fixture.fixture_predictions
        cls.players_path = fixture.players
        cls.reviewed_fixture_hashes = {
            cls.feature_path: "f7749a924f1223043f2d0d5c3be5004999157cde839a4c379c498e9a0c7a6887",
            cls.fixture_prediction_path: "5dc0042ca8e7da6ab96fb87e6bf8ef8b00f75ec8b4e017e68d140070de78c961",
            cls.projection_path: "105fc489991b568d1d572213f188543fbe8fd07504f0f7845504fa76a3eaa5fc",
            cls.players_path: "0ddbe5be615b2e5fc7eeb631035d5b65a382d70bf7e1acf3e9a269ec9cd35589",
            fixture.decision_template: "9bae364053fab15584860b6adbde9119ce41eb1cae505a87913b20078aeb72be",
            cls.candidate_path: "107216cdaa224d86e380c0bd2ab67b6afef5a7a233e105710a0255e0f3510c50",
        }
        cls.fixture_hashes_before = {
            path: sha256_file(path) for path in cls.reviewed_fixture_hashes
        }
        if cls.fixture_hashes_before != cls.reviewed_fixture_hashes:
            raise AssertionError(
                "committed GW2 fixture hashes do not match the reviewed source bytes"
            )
        cls.context = load_reliability_context(cls.decision_path, cls.feature_path)
        cls.payload = analyze_decision_reliability(
            cls.context,
            generation_timestamp="2026-08-27T01:02:03.456789Z",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fixture_manager.__exit__(None, None, None)

    def test_de_cuyper_provenance_and_warnings(self) -> None:
        official = self.payload["official_recommendation"]
        self.assertEqual(official["outgoing"]["element_id"], 499)
        self.assertEqual(official["incoming"]["element_id"], 115)
        self.assertAlmostEqual(official["gain_vs_roll"], 13.77, places=2)
        de_cuyper = next(
            row
            for row in self.payload["material_player_reliability"]
            if row["element_id"] == 115
        )
        self.assertAlmostEqual(de_cuyper["projection"], 11.45, places=2)
        self.assertEqual(de_cuyper["prior_total_minutes"], 77.0)
        self.assertEqual(de_cuyper["prior_appearances"], 1)
        self.assertEqual(de_cuyper["prior_starts"], 1)
        self.assertEqual(de_cuyper["cumulative_prior_xg"], 1.47)
        self.assertEqual(de_cuyper["cumulative_prior_xa"], 0.21)
        self.assertTrue(de_cuyper["low_sample"])
        self.assertTrue(de_cuyper["prediction_complete"])
        self.assertTrue(de_cuyper["unusually_extreme_attacking_rate"])
        self.assertEqual(
            de_cuyper["rate_diagnostics"]["xg_per_90"]["position_rank_desc"],
            1,
        )
        defender_reference = self.payload["reference_population"]["positions"][
            "DEF"
        ]
        self.assertEqual(
            defender_reference["prior_xg_per_90"]["population_n"], 108
        )
        self.assertEqual(
            defender_reference["prior_xa_per_90"]["population_n"], 108
        )
        self.assertEqual(
            defender_reference["prior_xg_per_90"]["excluded_incomplete_n"],
            94,
        )
        self.assertEqual(
            defender_reference["prior_xg_per_90"]["excluded_null_rate_n"],
            94,
        )
        warning_codes = {row["code"] for row in self.payload["warnings"]}
        self.assertIn("incoming_has_one_prior_appearance", warning_codes)
        self.assertIn(
            "incoming_attacking_rate_in_extreme_descriptive_tail", warning_codes
        )
        self.assertIn("captaincy_amplifies_recommendation", warning_codes)
        self.assertIn("early_season_low_sample_is_universal", warning_codes)
        wording = self.payload["low_sample_context"]["persisted_interpretation"]
        self.assertEqual(
            wording,
            "low_sample is universal in this GW2 projection universe (610/610 "
            "players) and is therefore not a player-specific discriminator; De "
            "Cuyper's distinctive reliability concern is the extremity of his "
            "one-match attacking rates.",
        )
        self.assertNotIn(
            "recommendation_depends_on_low_sample_projection", warning_codes
        )

    def test_frozen_metric_specific_reference_population_counts(self) -> None:
        expected = {
            "GK": (20, 47),
            "DEF": (108, 94),
            "MID": (147, 121),
            "FWD": (35, 38),
        }
        positions = self.payload["reference_population"]["positions"]
        for position, (eligible, incomplete) in expected.items():
            for metric in ("prior_xg_per_90", "prior_xa_per_90"):
                population = positions[position][metric]
                self.assertEqual(population["population_n"], eligible)
                self.assertEqual(
                    population["excluded_incomplete_n"], incomplete
                )
                self.assertEqual(
                    population["excluded_null_rate_n"], incomplete
                )
                self.assertEqual(population["defined_incomplete_rate_n"], 0)

    def test_sensitivity_disagrees_but_official_recommendation_is_unchanged(self) -> None:
        views = {
            row["view_id"]: row["recommended_action_under_view"]
            for row in self.payload["diagnostic_sensitivity"]
        }
        self.assertEqual(
            views["minimum_prior_minutes_90"]["incoming"]["element_id"], 86
        )
        self.assertEqual(views["minimum_prior_minutes_91"]["action"], "ROLL")
        self.assertEqual(
            views["exclude_rates_above_position_p95"]["incoming"]["element_id"],
            562,
        )
        self.assertEqual(
            views["cap_rates_at_position_p90"]["incoming"]["element_id"], 379
        )
        no_captain = views["xi_only_without_captain_amplification"]
        self.assertEqual(no_captain["incoming"]["element_id"], 115)
        self.assertAlmostEqual(no_captain["gain_vs_roll"], 9.42, places=2)
        self.assertEqual(self.payload["stability_summary"]["different_action_count"], 7)
        self.assertEqual(len(self.payload["diagnostic_sensitivity"]), 11)
        self.assertFalse(
            self.payload["stability_summary"][
                "recommended_transfer_remains_same_across_all_views"
            ]
        )
        self.assertTrue(self.payload["official_recommendation_unchanged"])
        self.assertEqual(
            self.payload["official_recommendation"]["incoming"]["element_id"], 115
        )

    def test_rate_cap_transforms_owned_players_and_roll_symmetrically(self) -> None:
        owned_id = 40
        players = dict(self.context.players)
        players[owned_id] = replace(
            players[owned_id],
            projection=52.0,
            appearance_xfp=2.0,
            expected_minutes=90.0,
            prior_xg_per_90=10.0,
            prior_xa_per_90=0.0,
        )
        context = replace(
            self.context,
            players=players,
            reference=build_rate_reference(players),
        )
        current_ids = tuple(
            int(row["element_id"])
            for row in context.decision["roll"]["squad"]
        )
        original = {player_id: player.projection for player_id, player in players.items()}
        uncapped_roll = _fixed_squad_diagnostic(
            current_ids,
            players,
            original,
            include_captain=True,
        )
        views = {
            row["view_id"]: row
            for row in build_sensitivity_views(context, current_ids)
        }
        capped = views["cap_rates_at_position_p90"]
        self.assertLess(capped["roll_objective"], uncapped_roll.total_objective)
        self.assertIn(
            owned_id,
            capped["transformation_audit"]["transformed_owned_player_ids"],
        )
        self.assertEqual(
            capped["comparison_semantics"]["kind"],
            "symmetric_projection_transform",
        )
        self.assertIn(
            "same cap",
            capped["comparison_semantics"]["projection_transformation"],
        )

    def test_material_roles_incomplete_diagnostics_and_no_confidence_score(self) -> None:
        records = {
            row["element_id"]: row
            for row in self.payload["material_player_reliability"]
        }
        self.assertIn("roll.captain", records[40]["roles"])
        self.assertIn("official.incoming", records[115]["roles"])
        self.assertIn("official.outgoing", records[499]["roles"])
        self.assertIn("official.resulting_xi", records[115]["roles"])
        for player_id in (111, 272, 499):
            self.assertIn(player_id, records)
            self.assertFalse(records[player_id]["prediction_complete"])
            self.assertEqual(
                records[player_id]["projection_state"], "incomplete_projection"
            )
            self.assertIn(
                "roll.admitted_incomplete_squad", records[player_id]["roles"]
            )
        self.assertIn("official.outgoing", records[499]["roles"])
        incomplete_warning = next(
            row
            for row in self.payload["warnings"]
            if row["code"] == "appearance_only_policy_admits_incomplete_projections"
        )
        self.assertEqual(incomplete_warning["facts"]["element_ids"], [111, 272, 499])

        def keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value)) if value else set()
            return set()

        self.assertNotIn("confidence_score", keys(self.payload))
        self.assertFalse(self.payload["guardrails"]["unsupported_confidence_grade_emitted"])

    def test_deterministic_output_and_fail_closed_missing_player_provenance(self) -> None:
        repeated = analyze_decision_reliability(
            self.context,
            generation_timestamp="2026-08-27T01:02:03.456789Z",
        )
        self.assertEqual(repeated, self.payload)
        self.assertEqual(
            repeated["reference_population"], self.payload["reference_population"]
        )
        self.assertEqual(
            repeated["diagnostic_sensitivity"], self.payload["diagnostic_sensitivity"]
        )
        self.assertEqual(
            repeated["stability_summary"], self.payload["stability_summary"]
        )
        self.assertEqual(
            len(repeated["diagnostic_sensitivity"]), 11
        )
        candidate_universe = repeated["provenance"]["candidate_universe"]
        self.assertEqual(
            candidate_universe["candidate_count"],
            self.context.decision["legal_transfer_candidate_count"],
        )
        self.assertTrue(candidate_universe["ordered_rows_consumed_directly"])
        self.assertFalse(
            candidate_universe["transfer_legality_rebuilt_by_task_017"]
        )
        missing = dict(self.context.players)
        missing.pop(115)
        with self.assertRaisesRegex(
            DecisionReliabilityError, "reliability provenance is missing"
        ):
            analyze_decision_reliability(replace(self.context, players=missing))

    def test_reliability_artifact_is_immutable_and_fixture_sources_stay_unchanged(self) -> None:
        """Use real writer refusal semantics against a temporary artifact copy."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copied_decision = root / "one_transfer_decision.json"
            copied_decision.write_bytes(self.decision_path.read_bytes())
            context = replace(self.context, decision_path=copied_decision)
            generated = datetime(2026, 8, 27, 1, 2, 3, 456789, tzinfo=timezone.utc)
            artifacts = write_decision_reliability(context, generated_at=generated)
            self.assertTrue(artifacts.reliability_path.is_file())
            with self.assertRaises(DecisionReliabilityOutputExistsError):
                write_decision_reliability(context, generated_at=generated)
        self.assertEqual(
            {
                path: sha256_file(path)
                for path in self.reviewed_fixture_hashes
            },
            self.fixture_hashes_before,
        )


if __name__ == "__main__":
    unittest.main()
