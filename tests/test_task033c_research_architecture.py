from __future__ import annotations

import ast
import unittest
from pathlib import Path

from fpl_decision_engine.historical_attacking_rate_experiment import CANDIDATES as LEGACY_RATE_CANDIDATES
from fpl_decision_engine.historical_minutes_experiment import CANDIDATES as LEGACY_MINUTE_CANDIDATES
from fpl_decision_engine.research.task033c1 import COMBINED_IDENTITY, UA1_IDENTITY, UM1_IDENTITY


REPOSITORY = Path(__file__).resolve().parents[1]


class Task033ResearchArchitectureTests(unittest.TestCase):
    def test_production_application_and_presentation_cannot_import_task033_research(self) -> None:
        violations: list[str] = []
        for source in sorted((REPOSITORY / "src").rglob("*.py")):
            relative = source.relative_to(REPOSITORY)
            if "research" in relative.parts:
                continue
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(relative))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(name == "research" or name.startswith("fpl_decision_engine.research") for name in names):
                    violations.append(f"{relative}:{node.lineno}")
        self.assertEqual(violations, [])

    def test_c1_kernel_has_no_filesystem_database_network_or_outcome_dependency(self) -> None:
        source = REPOSITORY / "src/fpl_decision_engine/research/task033c1.py"
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
        self.assertEqual(imported_roots, {"__future__", "json", "math", "dataclasses", "typing"})

    def test_registered_candidate_identities_are_not_legacy_candidates(self) -> None:
        legacy = set(LEGACY_MINUTE_CANDIDATES) | set(LEGACY_RATE_CANDIDATES)
        self.assertTrue({UM1_IDENTITY, UA1_IDENTITY, COMBINED_IDENTITY}.isdisjoint(legacy))
        self.assertEqual(LEGACY_MINUTE_CANDIDATES, ("M0", "M1", "M2", "M3"))
        self.assertEqual(LEGACY_RATE_CANDIDATES, ("S0", "S1", "S2", "S3"))


if __name__ == "__main__":
    unittest.main()
