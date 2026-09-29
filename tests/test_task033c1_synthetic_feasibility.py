from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "scripts/task033c1_synthetic_feasibility.py"
SPEC = importlib.util.spec_from_file_location("task033c1_synthetic_feasibility", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Task033C1SyntheticFeasibilityTests(unittest.TestCase):
    def test_small_drill_is_synthetic_bounded_and_deterministic(self) -> None:
        result = MODULE.run_spike(24)
        self.assertTrue(result["synthetic_only"])
        self.assertEqual(result["dimensions"]["players"], 24)
        self.assertTrue(result["deterministic_repeat"]["identical"])
        self.assertEqual(
            result["first_run"]["sha256"],
            result["deterministic_repeat"]["sha256"],
        )
        self.assertIn(
            "3000-candidate/12-view/60-second operational decision evaluation",
            result["claims_not_established"],
        )

    def test_player_ceiling_is_enforced_before_generation(self) -> None:
        for count in (0, 701):
            with self.subTest(count=count), self.assertRaises(ValueError):
                MODULE.run_spike(count)

    def test_drill_has_no_real_data_or_filesystem_input_option(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("read_parquet", text)
        self.assertNotIn("historical_clean", text)
        self.assertNotIn("manager_state", text)
        self.assertNotIn("target_outcome", text)
        self.assertNotIn("Path(", text)


if __name__ == "__main__":
    unittest.main()
