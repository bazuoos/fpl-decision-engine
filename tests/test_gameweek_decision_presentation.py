from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fpl_decision_engine.decision import DecisionSelectionValidationError
from fpl_decision_engine.decision_reliability import (
    analyze_decision_reliability,
    load_reliability_context,
)
from fpl_decision_engine.presentation.gameweek_decision import (
    GameweekDecisionSchemaError,
    GameweekDecisionSourceValidationError,
    build_gameweek_decision,
    serialize_gameweek_decision,
    validate_gameweek_decision_schema,
)
from fpl_decision_engine.projection_provider import sha256_file
from tests.fixture_support import materialized_frozen_gw2


class FrozenGW2GameweekDecisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture_manager = materialized_frozen_gw2()
        cls.fixture = cls.fixture_manager.__enter__()
        cls.temporary_manager = tempfile.TemporaryDirectory()
        cls.temporary = Path(cls.temporary_manager.name)
        context = load_reliability_context(cls.fixture.decision, cls.fixture.features)
        cls.reliability_payload = analyze_decision_reliability(
            context,
            generation_timestamp="2026-08-27T01:02:03.456789Z",
        )
        cls.reliability_path = cls.temporary / "decision_reliability.json"
        cls.reliability_path.write_text(
            json.dumps(cls.reliability_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        cls.source_paths = (
            cls.fixture.decision,
            cls.fixture.decision_template,
            cls.fixture.candidates,
            cls.fixture.features,
            cls.fixture.gameweek_predictions,
            cls.fixture.fixture_predictions,
            cls.fixture.players,
            cls.fixture.manual_state,
            cls.reliability_path,
        )
        cls.source_hashes = {path: sha256_file(path) for path in cls.source_paths}
        cls.payload = build_gameweek_decision(
            cls.fixture.decision, cls.reliability_path
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_manager.cleanup()
        cls.fixture_manager.__exit__(None, None, None)

    def _mutated_sources(self, mutate: object) -> tuple[Path, Path]:
        decision = json.loads(self.fixture.decision.read_bytes())
        mutate(decision)  # type: ignore[operator]
        directory = Path(tempfile.mkdtemp(dir=self.temporary))
        decision_path = directory / "one_transfer_decision.json"
        decision_path.write_text(
            json.dumps(decision, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        reliability = copy.deepcopy(self.reliability_payload)
        link = reliability["provenance"]["task_016_decision_artifact"]
        link["path"] = str(decision_path)
        link["sha256"] = sha256_file(decision_path)
        reliability_path = directory / "decision_reliability.json"
        reliability_path.write_text(
            json.dumps(reliability, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return decision_path, reliability_path

    def test_deterministic_output_from_identical_frozen_inputs(self) -> None:
        repeated = build_gameweek_decision(
            self.fixture.decision, self.reliability_path
        )
        self.assertEqual(repeated, self.payload)
        self.assertEqual(
            serialize_gameweek_decision(repeated),
            serialize_gameweek_decision(self.payload),
        )

    def test_valid_gw2_contract_passes_json_schema(self) -> None:
        validate_gameweek_decision_schema(self.payload)
        self.assertEqual(self.payload["schema_name"], "GameweekDecision")
        self.assertEqual(self.payload["schema_version"], "1.0.0")
        self.assertEqual(self.payload["frozen_deadline"], "2026-08-28T17:30:00Z")

    def test_missing_required_field_fails_json_schema(self) -> None:
        invalid = copy.deepcopy(self.payload)
        del invalid["manager_state"]
        with self.assertRaisesRegex(GameweekDecisionSchemaError, "manager_state"):
            validate_gameweek_decision_schema(invalid)

    def test_invalid_action_type_fails_json_schema(self) -> None:
        invalid = copy.deepcopy(self.payload)
        invalid["recommended_action"]["action_type"] = "WILDCARD"
        with self.assertRaises(GameweekDecisionSchemaError):
            validate_gameweek_decision_schema(invalid)

    def test_unexpected_additional_field_fails_json_schema(self) -> None:
        invalid = copy.deepcopy(self.payload)
        invalid["unversioned_extension"] = True
        with self.assertRaisesRegex(
            GameweekDecisionSchemaError, "Additional properties are not allowed"
        ):
            validate_gameweek_decision_schema(invalid)

    def test_invalid_xi_fails_through_trusted_selection_validator(self) -> None:
        def mutate(decision: dict[str, object]) -> None:
            decision["roll"]["starting_xi"].pop()  # type: ignore[index,union-attr]

        decision_path, reliability_path = self._mutated_sources(mutate)
        with self.assertRaises(GameweekDecisionSourceValidationError) as caught:
            build_gameweek_decision(decision_path, reliability_path)
        self.assertIsInstance(caught.exception.__cause__, DecisionSelectionValidationError)
        self.assertIn("starter_count", caught.exception.__cause__.violation_codes)

    def test_duplicate_squad_player_fails_through_trusted_validator(self) -> None:
        def mutate(decision: dict[str, object]) -> None:
            squad = decision["roll"]["squad"]  # type: ignore[index]
            squad[-1] = copy.deepcopy(squad[0])  # type: ignore[index]

        decision_path, reliability_path = self._mutated_sources(mutate)
        with self.assertRaises(GameweekDecisionSourceValidationError) as caught:
            build_gameweek_decision(decision_path, reliability_path)
        self.assertIsInstance(caught.exception.__cause__, DecisionSelectionValidationError)
        self.assertIn(
            "duplicate_squad_players", caught.exception.__cause__.violation_codes
        )

    def test_invalid_captain_vice_fails_through_trusted_validator(self) -> None:
        def mutate(decision: dict[str, object]) -> None:
            roll = decision["roll"]  # type: ignore[index]
            roll["vice_captain"] = copy.deepcopy(roll["captain"])  # type: ignore[index]

        decision_path, reliability_path = self._mutated_sources(mutate)
        with self.assertRaises(GameweekDecisionSourceValidationError) as caught:
            build_gameweek_decision(decision_path, reliability_path)
        self.assertIsInstance(caught.exception.__cause__, DecisionSelectionValidationError)
        self.assertIn("captain_vice_distinct", caught.exception.__cause__.violation_codes)

    def test_transfer_must_match_hash_validated_trusted_candidate(self) -> None:
        def mutate(decision: dict[str, object]) -> None:
            decision["best_transfer"]["resulting_bank_units"] = 99  # type: ignore[index]

        decision_path, reliability_path = self._mutated_sources(mutate)
        with self.assertRaisesRegex(
            GameweekDecisionSourceValidationError,
            "does not match the trusted candidate",
        ):
            build_gameweek_decision(decision_path, reliability_path)

    def test_transfer_ids_must_identify_a_trusted_candidate(self) -> None:
        def mutate(decision: dict[str, object]) -> None:
            decision["best_transfer"]["in"]["element_id"] = 999999  # type: ignore[index]

        decision_path, reliability_path = self._mutated_sources(mutate)
        with self.assertRaisesRegex(
            GameweekDecisionSourceValidationError,
            "not uniquely present",
        ):
            build_gameweek_decision(decision_path, reliability_path)

    def test_transfer_price_facts_must_match_trusted_sources(self) -> None:
        mutations = (
            (
                lambda decision: decision["best_transfer"]["out"].__setitem__(  # type: ignore[index,union-attr]
                    "verified_selling_price_units", 54
                ),
                "player/price facts do not match the trusted candidate",
            ),
            (
                lambda decision: decision["best_transfer"]["in"].__setitem__(  # type: ignore[index,union-attr]
                    "purchase_price_units", 44
                ),
                "player/price facts do not match the trusted candidate",
            ),
        )
        for mutate, message in mutations:
            with self.subTest(message=message):
                decision_path, reliability_path = self._mutated_sources(mutate)
                with self.assertRaisesRegex(
                    GameweekDecisionSourceValidationError, message
                ):
                    build_gameweek_decision(decision_path, reliability_path)

    def test_transfer_cost_must_match_verified_manager_state(self) -> None:
        def mutate(decision: dict[str, object]) -> None:
            decision["manual_state"]["one_transfer_cost_points"] = 4  # type: ignore[index]

        decision_path, reliability_path = self._mutated_sources(mutate)
        with self.assertRaisesRegex(
            GameweekDecisionSourceValidationError,
            "current_transfer_cost_points does not reconcile",
        ):
            build_gameweek_decision(decision_path, reliability_path)

    def test_tampered_linked_source_artifact_fails_hash_validation(self) -> None:
        decision = json.loads(self.fixture.decision.read_bytes())
        directory = Path(tempfile.mkdtemp(dir=self.temporary))
        candidate_path = directory / "legal_transfer_candidates.json"
        candidate_path.write_bytes(self.fixture.candidates.read_bytes())
        expected_hash = sha256_file(candidate_path)
        candidate_path.write_bytes(candidate_path.read_bytes() + b"\n")
        decision["candidate_summaries_artifact"] = {
            "path": str(candidate_path),
            "sha256": expected_hash,
        }
        decision_path = directory / "one_transfer_decision.json"
        decision_path.write_text(
            json.dumps(decision, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        reliability = copy.deepcopy(self.reliability_payload)
        reliability["provenance"]["task_016_decision_artifact"] = {
            "path": str(decision_path),
            "sha256": sha256_file(decision_path),
        }
        reliability_path = directory / "decision_reliability.json"
        reliability_path.write_text(
            json.dumps(reliability, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            GameweekDecisionSourceValidationError, "hash mismatch"
        ):
            build_gameweek_decision(decision_path, reliability_path)

    def test_builder_never_reruns_optimizer(self) -> None:
        with patch(
            "fpl_decision_engine.decision.optimize_xi",
            side_effect=AssertionError("presentation must not optimize"),
        ):
            repeated = build_gameweek_decision(
                self.fixture.decision, self.reliability_path
            )
        self.assertEqual(repeated, self.payload)

    def test_reliability_has_no_confidence_score_and_preserves_all_views(self) -> None:
        def keys(value: object) -> set[str]:
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(item) for item in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(item) for item in value)) if value else set()
            return set()

        reliability = self.payload["reliability"]
        self.assertNotIn("confidence_score", keys(reliability))
        self.assertTrue(reliability["diagnostic_only"])
        self.assertTrue(reliability["official_recommendation_unchanged"])
        self.assertEqual(reliability["sensitivity_view_count"], 11)
        self.assertEqual(len(reliability["sensitivity_results"]), 11)
        self.assertEqual(reliability["same_exact_action_count"], 4)
        self.assertEqual(reliability["changed_action_count"], 7)

    def test_reliability_cannot_override_engine_recommendation(self) -> None:
        reliability = copy.deepcopy(self.reliability_payload)
        reliability["official_recommendation"]["action"] = "ROLL"
        path = self.temporary / "overriding_reliability.json"
        path.write_text(
            json.dumps(reliability, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            GameweekDecisionSourceValidationError,
            "does not match the decision",
        ):
            build_gameweek_decision(self.fixture.decision, path)

    def test_unordered_source_collections_are_canonicalized(self) -> None:
        def mutate(decision: dict[str, object]) -> None:
            decision["selling_price_inputs"]["prices"].reverse()  # type: ignore[index,union-attr]

        decision_path, reliability_path = self._mutated_sources(mutate)
        reliability = json.loads(reliability_path.read_bytes())
        reliability["warnings"].reverse()
        reliability["diagnostic_sensitivity"].reverse()
        reliability["material_player_reliability"].reverse()
        reliability_path.write_text(
            json.dumps(reliability, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        actual = build_gameweek_decision(decision_path, reliability_path)
        self.assertEqual(
            [row["element_id"] for row in actual["players"]],
            sorted(row["element_id"] for row in actual["players"]),
        )
        self.assertEqual(
            [row["role"] for row in actual["source_artifacts"]],
            sorted(row["role"] for row in actual["source_artifacts"]),
        )
        self.assertEqual(
            [row["view_id"] for row in actual["reliability"]["sensitivity_results"]],
            sorted(
                row["view_id"]
                for row in actual["reliability"]["sensitivity_results"]
            ),
        )
        self.assertEqual(
            actual["reliability"]["warnings"],
            sorted(
                actual["reliability"]["warnings"],
                key=lambda row: (row["code"], row["message"]),
            ),
        )

    def test_engine_recommendation_is_preserved_exactly(self) -> None:
        action = self.payload["recommended_action"]
        self.assertEqual(action["action_type"], "TRANSFER")
        self.assertEqual(action["outgoing"]["element_id"], 499)
        self.assertEqual(action["incoming"]["element_id"], 115)
        self.assertEqual(action["selection"]["formation"], "4-5-1")
        self.assertEqual(action["selection"]["captain"], 115)
        self.assertEqual(action["selection"]["vice_captain"], 40)
        self.assertAlmostEqual(action["selection"]["objective"]["total_xfp"], 61.59)
        self.assertAlmostEqual(action["objective_gain_vs_roll_xfp"], 13.77)
        self.assertEqual(self.payload["roll"]["objective"]["total_xfp"], 47.82)

    def test_source_artifacts_are_not_modified(self) -> None:
        self.assertEqual(
            {path: sha256_file(path) for path in self.source_paths},
            self.source_hashes,
        )
        self.assertTrue(self.payload["validation"]["all_source_hashes_validated"])
        self.assertTrue(self.payload["validation"]["transfer_legality"]["passed"])

    def test_installed_wheel_loads_packaged_schema_outside_repository(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wheelhouse = root / "wheelhouse"
            wheelhouse.mkdir()
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-deps",
                    "--no-build-isolation",
                    "--wheel-dir",
                    str(wheelhouse),
                    str(repository),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            wheels = tuple(wheelhouse.glob("*.whl"))
            self.assertEqual(len(wheels), 1)
            installation = root / "installed-package"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--target",
                    str(installation),
                    str(wheels[0]),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            outside = root / "outside-repository"
            outside.mkdir()
            clean_environment = dict(os.environ)
            clean_environment["PYTHONPATH"] = str(installation)
            clean_environment["PYTHONNOUSERSITE"] = "1"
            script = """
from importlib import resources
from pathlib import Path
import sys
from fpl_decision_engine.presentation import (
    GameweekDecisionSchemaError,
    validate_gameweek_decision_schema,
)
import fpl_decision_engine.presentation as presentation

module_path = Path(presentation.__file__).resolve()
installation = Path(sys.argv[1]).resolve()
assert installation in module_path.parents
schema = resources.files('fpl_decision_engine.presentation').joinpath(
    'schemas', 'gameweek_decision_v1.schema.json'
)
assert schema.is_file()
try:
    validate_gameweek_decision_schema({})
except GameweekDecisionSchemaError:
    pass
else:
    raise AssertionError('packaged schema was not used for validation')
"""
            subprocess.run(
                [sys.executable, "-c", script, str(installation)],
                cwd=outside,
                env=clean_environment,
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
