from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from fpl_decision_engine.__main__ import main


class CLITests(unittest.TestCase):
    @patch("fpl_decision_engine.__main__.fetch_bootstrap_static")
    def test_legacy_arguments_still_dispatch_to_fetch(self, fetch) -> None:
        self.assertEqual(main(["--season", "2025-26"]), 0)
        fetch.assert_called_once_with(
            data_root=Path("data/raw/fpl"), season="2025-26"
        )

    @patch("fpl_decision_engine.__main__.transform_latest_players")
    def test_transform_players_dispatches_without_fetching(self, transform) -> None:
        self.assertEqual(main(["transform-players", "--season", "2026-27"]), 0)
        transform.assert_called_once_with(
            raw_data_root=Path("data/raw/fpl"),
            clean_data_root=Path("data/clean/fpl"),
            season="2026-27",
        )


if __name__ == "__main__":
    unittest.main()
