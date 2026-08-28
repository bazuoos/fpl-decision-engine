from __future__ import annotations

import unittest
from pathlib import Path


class TestFixtureIsolationTests(unittest.TestCase):
    def test_production_source_does_not_reference_test_artifact_fixtures(self) -> None:
        """Keep committed test artifacts outside every production code path."""
        repository = Path(__file__).resolve().parents[1]
        forbidden = ("tests/fixtures", "tests.fixture_support")
        violations: list[str] = []
        for source in sorted((repository / "src").rglob("*.py")):
            text = source.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    violations.append(f"{source.relative_to(repository)}: {marker}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
