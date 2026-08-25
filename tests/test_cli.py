from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from fpl_decision_engine.__main__ import main
from fpl_decision_engine.predictions import PredictionOutputs


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

    @patch("fpl_decision_engine.__main__.fetch_player_histories_for_snapshot")
    def test_player_history_fetch_dispatches_snapshot_and_pacing(self, fetch) -> None:
        self.assertEqual(
            main(
                [
                    "fetch-player-history",
                    "--snapshot-timestamp",
                    "20260825T073532.450889Z",
                    "--delay-seconds",
                    "0.1",
                ]
            ),
            0,
        )
        fetch.assert_called_once_with(
            raw_data_root=Path("data/raw/fpl"),
            season="2026-27",
            snapshot_timestamp="20260825T073532.450889Z",
            delay_seconds=0.1,
        )

    @patch("fpl_decision_engine.__main__.build_player_gameweek_features")
    def test_build_features_dispatches_target_gameweek(self, build) -> None:
        build.return_value = Path("features.parquet")
        self.assertEqual(
            main(
                [
                    "build-features",
                    "--target-gameweek",
                    "2",
                    "--snapshot-timestamp",
                    "20260825T073532.450889Z",
                ]
            ),
            0,
        )
        build.assert_called_once_with(
            target_gameweek=2,
            raw_data_root=Path("data/raw/fpl"),
            clean_data_root=Path("data/clean/fpl"),
            feature_data_root=Path("data/features/fpl"),
            season="2026-27",
            snapshot_timestamp="20260825T073532.450889Z",
        )

    @patch("fpl_decision_engine.__main__.predict_xfp_v01")
    def test_predict_xfp_dispatches_without_fetching(self, predict) -> None:
        predict.return_value = PredictionOutputs(
            fixture_path=Path("fixtures.parquet"),
            gameweek_path=Path("gameweek.parquet"),
            fixture_rows=12,
            gameweek_rows=10,
        )
        self.assertEqual(
            main(
                [
                    "predict-xfp",
                    "--target-gameweek",
                    "2",
                    "--snapshot-timestamp",
                    "20260825T073532.450889Z",
                ]
            ),
            0,
        )
        predict.assert_called_once_with(
            target_gameweek=2,
            raw_data_root=Path("data/raw/fpl"),
            clean_data_root=Path("data/clean/fpl"),
            feature_data_root=Path("data/features/fpl"),
            prediction_data_root=Path("data/predictions/fpl"),
            season="2026-27",
            snapshot_timestamp="20260825T073532.450889Z",
        )

    @patch("fpl_decision_engine.__main__.evaluate_xfp")
    def test_evaluate_xfp_dispatches_without_fetching(self, evaluate) -> None:
        output = type("Output", (), {
            "directory": Path("evaluation"),
            "player_rows": 10,
            "evaluated_players": 8,
        })()
        evaluate.return_value = output
        self.assertEqual(
            main(
                [
                    "evaluate-xfp",
                    "--target-gameweek", "2",
                    "--model-version", "v0.1",
                    "--prediction-snapshot-timestamp",
                    "20260825T073532.450889Z",
                    "--realized-snapshot-timestamp",
                    "20260901T120000.000000Z",
                ]
            ),
            0,
        )
        evaluate.assert_called_once_with(
            target_gameweek=2,
            model_version="v0.1",
            prediction_snapshot_timestamp="20260825T073532.450889Z",
            realized_snapshot_timestamp="20260901T120000.000000Z",
            raw_data_root=Path("data/raw/fpl"),
            clean_data_root=Path("data/clean/fpl"),
            feature_data_root=Path("data/features/fpl"),
            prediction_data_root=Path("data/predictions/fpl"),
            evaluation_data_root=Path("data/evaluations/fpl"),
            season="2026-27",
            top_n=10,
        )


if __name__ == "__main__":
    unittest.main()
