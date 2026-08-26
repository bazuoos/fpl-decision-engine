from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from fpl_decision_engine.__main__ import main
from fpl_decision_engine.historical import HistoricalBuildResult
from fpl_decision_engine.historical_backtest import HistoricalBacktestResult
from fpl_decision_engine.historical_attacking_rate_experiment import (
    HistoricalAttackingRateExperimentResult,
)
from fpl_decision_engine.historical_calibration_experiment import (
    HistoricalCalibrationExperimentResult,
)
from fpl_decision_engine.historical_minutes_experiment import (
    HistoricalMinutesExperimentResult,
)
from fpl_decision_engine.predictions import PredictionOutputs
from fpl_decision_engine.refresh import RefreshResult, RefreshUnlockResult


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

    @patch("fpl_decision_engine.__main__.refresh_fpl_data")
    def test_refresh_dispatches_explicit_resume_and_roots(self, refresh) -> None:
        refresh.return_value = RefreshResult(
            snapshot_timestamp="20260826T010203.456789Z",
            raw_directory=Path("raw/snapshot"),
            clean_directory=Path("clean/snapshot"),
            manifest_path=Path("raw/snapshot/refresh.manifest.json"),
            player_count=611,
            fixture_count=380,
            history_row_count=611,
        )
        self.assertEqual(
            main(
                [
                    "refresh",
                    "--resume", "20260826T010203.456789Z",
                    "--delay-seconds", "0.1",
                ]
            ),
            0,
        )
        refresh.assert_called_once_with(
            raw_data_root=Path("data/raw/fpl"),
            clean_data_root=Path("data/clean/fpl"),
            season="2026-27",
            resume_snapshot_timestamp="20260826T010203.456789Z",
            history_delay_seconds=0.1,
        )

    @patch("fpl_decision_engine.__main__.unlock_refresh_snapshot")
    def test_refresh_unlock_dispatches_exact_snapshot(self, unlock) -> None:
        unlock.return_value = RefreshUnlockResult(
            snapshot_timestamp="20260826T010203.456789Z",
            lock_path=Path("raw/20260826T010203.456789Z/.refresh.lock"),
            lock_metadata={"pid": 123},
        )
        self.assertEqual(
            main(
                [
                    "refresh-unlock",
                    "--season", "2026-27",
                    "--snapshot", "20260826T010203.456789Z",
                    "--raw-data-root", "custom/raw",
                ]
            ),
            0,
        )
        unlock.assert_called_once_with(
            raw_data_root=Path("custom/raw"),
            season="2026-27",
            snapshot_timestamp="20260826T010203.456789Z",
        )

    @patch("fpl_decision_engine.__main__.build_historical_datasets")
    def test_build_historical_dispatches_separate_roots(self, build) -> None:
        build.return_value = HistoricalBuildResult(
            directory=Path("custom/clean/restricted-pseudo-backtest-v1"),
            manifest_path=Path("custom/clean/restricted-pseudo-backtest-v1/manifest.json"),
            row_counts={},
        )
        self.assertEqual(
            main(
                [
                    "build-historical",
                    "--raw-data-root", "custom/raw",
                    "--clean-data-root", "custom/clean",
                ]
            ),
            0,
        )
        build.assert_called_once_with(
            raw_data_root=Path("custom/raw"),
            clean_data_root=Path("custom/clean"),
        )

    @patch("fpl_decision_engine.__main__.build_historical_xfp_v01_backtest")
    def test_historical_backtest_dispatches_separate_roots(self, build) -> None:
        build.return_value = HistoricalBacktestResult(
            directory=Path("custom/backtests/xfp-v01-baseline-v1"),
            manifest_path=Path("custom/backtests/xfp-v01-baseline-v1/manifest.json"),
            player_gameweek_path=Path("custom/backtests/xfp-v01-baseline-v1/players.parquet"),
            observations=100,
            modeled_complete_pairs=90,
        )
        self.assertEqual(
            main(
                [
                    "backtest-xfp-v01",
                    "--historical-clean-root", "custom/clean",
                    "--backtest-root", "custom/backtests",
                ]
            ),
            0,
        )
        build.assert_called_once_with(
            historical_clean_root=Path("custom/clean"),
            backtest_root=Path("custom/backtests"),
        )

    @patch("fpl_decision_engine.__main__.run_historical_minutes_experiment")
    def test_minutes_experiment_dispatches_immutable_inputs_and_output(self, run) -> None:
        run.return_value = HistoricalMinutesExperimentResult(
            directory=Path("custom/experiments/minutes-v02-experiment-v1"),
            manifest_path=Path("custom/experiments/minutes-v02-experiment-v1/manifest.json"),
            development_winner=None,
            holdout_passed=None,
            final_decision="DO NOT PROMOTE — KEEP v0.1 MINUTES",
        )
        self.assertEqual(
            main(
                [
                    "experiment-minutes-v02",
                    "--historical-clean-root", "custom/clean",
                    "--baseline-root", "custom/backtests",
                    "--experiment-root", "custom/experiments",
                ]
            ),
            0,
        )
        run.assert_called_once_with(
            historical_clean_root=Path("custom/clean"),
            baseline_root=Path("custom/backtests"),
            experiment_root=Path("custom/experiments"),
        )

    @patch("fpl_decision_engine.__main__.run_historical_attacking_rate_experiment")
    def test_attacking_rate_experiment_dispatches_frozen_inputs(self, run) -> None:
        run.return_value = HistoricalAttackingRateExperimentResult(
            directory=Path("custom/experiments/attacking-rate-v02-experiment-v1"),
            manifest_path=Path("custom/experiments/attacking-rate-v02-experiment-v1/manifest.json"),
            development_winner=None,
            holdout_passed=None,
            final_decision="DO NOT PROMOTE — KEEP v0.1 ATTACKING RATES",
        )
        self.assertEqual(
            main([
                "experiment-attacking-rates-v02",
                "--historical-clean-root", "custom/clean",
                "--baseline-root", "custom/backtests",
                "--experiment-root", "custom/experiments",
            ]),
            0,
        )
        run.assert_called_once_with(
            historical_clean_root=Path("custom/clean"),
            baseline_root=Path("custom/backtests"),
            experiment_root=Path("custom/experiments"),
        )

    @patch("fpl_decision_engine.__main__.run_historical_calibration_experiment")
    def test_calibration_experiment_dispatches_frozen_inputs(self, run) -> None:
        run.return_value = HistoricalCalibrationExperimentResult(
            directory=Path("custom/experiments/calibration-v02-experiment-v1"),
            manifest_path=Path("custom/experiments/calibration-v02-experiment-v1/manifest.json"),
            development_winner=None,
            holdout_passed=None,
            final_decision="DO NOT PROMOTE — KEEP RAW xFP v0.1",
        )
        self.assertEqual(
            main([
                "experiment-calibration-v02",
                "--historical-clean-root", "custom/clean",
                "--baseline-root", "custom/backtests",
                "--experiment-root", "custom/experiments",
            ]),
            0,
        )
        run.assert_called_once_with(
            historical_clean_root=Path("custom/clean"),
            baseline_root=Path("custom/backtests"),
            experiment_root=Path("custom/experiments"),
        )


if __name__ == "__main__":
    unittest.main()
