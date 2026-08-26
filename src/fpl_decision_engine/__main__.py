"""Command-line entry point for the FPL data pipeline."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from .features import build_player_gameweek_features
from .evaluation import evaluate_xfp
from .gameweek_transform import (
    transform_fixtures_for_snapshot,
    transform_player_history_for_snapshot,
)
from .historical import HistoricalIngestionError, build_historical_datasets
from .historical_backtest import (
    HistoricalBacktestError,
    build_historical_xfp_v01_backtest,
)
from .historical_attacking_rate_experiment import (
    HistoricalAttackingRateExperimentError,
    run_historical_attacking_rate_experiment,
)
from .historical_minutes_experiment import (
    HistoricalMinutesExperimentError,
    run_historical_minutes_experiment,
)
from .official_data import (
    DEFAULT_HISTORY_DELAY_SECONDS,
    OfficialDataError,
    fetch_fixtures_for_snapshot,
    fetch_player_histories_for_snapshot,
)
from .pipeline import FetchError, fetch_bootstrap_static
from .predictions import predict_xfp_v01
from .refresh import RefreshError, refresh_fpl_data, unlock_refresh_snapshot
from .transform import TransformationError, transform_latest_players

COMMANDS = {
    "fetch",
    "transform-players",
    "fetch-fixtures",
    "fetch-player-history",
    "transform-fixtures",
    "transform-player-history",
    "build-features",
    "predict-xfp",
    "evaluate-xfp",
    "refresh",
    "refresh-unlock",
    "build-historical",
    "backtest-xfp-v01",
    "experiment-minutes-v02",
    "experiment-attacking-rates-v02",
}


def _add_snapshot_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--season",
        default="2026-27",
        help="Season directory name (default: %(default)s).",
    )
    parser.add_argument(
        "--snapshot-timestamp",
        help="Bootstrap snapshot directory; defaults to the latest snapshot.",
    )


def _add_raw_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--raw-data-root",
        type=Path,
        default=Path("data/raw/fpl"),
        help="Root directory containing raw FPL snapshots (default: %(default)s).",
    )


def _add_clean_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--clean-data-root",
        type=Path,
        default=Path("data/clean/fpl"),
        help="Root directory for clean FPL datasets (default: %(default)s).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch raw FPL data or build clean analytical datasets."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch_parser = subparsers.add_parser(
        "fetch", help="Fetch a new bootstrap-static raw snapshot."
    )
    fetch_parser.add_argument(
        "--season",
        default="2026-27",
        help="Season directory name (default: %(default)s).",
    )
    fetch_parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data/raw/fpl"),
        help="Root directory for raw FPL snapshots (default: %(default)s).",
    )

    transform_parser = subparsers.add_parser(
        "transform-players", help="Transform the latest raw player snapshot to Parquet."
    )
    transform_parser.add_argument(
        "--season",
        default="2026-27",
        help="Season directory name (default: %(default)s).",
    )
    transform_parser.add_argument(
        "--raw-data-root",
        type=Path,
        default=Path("data/raw/fpl"),
        help="Root directory containing raw FPL snapshots (default: %(default)s).",
    )
    transform_parser.add_argument(
        "--clean-data-root",
        type=Path,
        default=Path("data/clean/fpl"),
        help="Root directory for clean FPL datasets (default: %(default)s).",
    )

    fixtures_fetch_parser = subparsers.add_parser(
        "fetch-fixtures", help="Fetch official fixtures for a bootstrap snapshot."
    )
    _add_snapshot_arguments(fixtures_fetch_parser)
    _add_raw_root(fixtures_fetch_parser)

    history_fetch_parser = subparsers.add_parser(
        "fetch-player-history",
        help="Fetch official element-summary history for every snapshot player.",
    )
    _add_snapshot_arguments(history_fetch_parser)
    _add_raw_root(history_fetch_parser)
    history_fetch_parser.add_argument(
        "--delay-seconds",
        type=float,
        default=DEFAULT_HISTORY_DELAY_SECONDS,
        help="Delay between player requests (default: %(default)s).",
    )

    fixtures_transform_parser = subparsers.add_parser(
        "transform-fixtures", help="Transform raw fixtures to typed Parquet."
    )
    _add_snapshot_arguments(fixtures_transform_parser)
    _add_raw_root(fixtures_transform_parser)
    _add_clean_root(fixtures_transform_parser)

    history_transform_parser = subparsers.add_parser(
        "transform-player-history",
        help="Transform realized player fixture history to typed Parquet.",
    )
    _add_snapshot_arguments(history_transform_parser)
    _add_raw_root(history_transform_parser)
    _add_clean_root(history_transform_parser)

    feature_parser = subparsers.add_parser(
        "build-features",
        help="Build leakage-safe player features for a target gameweek.",
    )
    feature_parser.add_argument(
        "--target-gameweek",
        type=int,
        required=True,
        help="Prediction target; history is restricted to earlier gameweeks.",
    )
    _add_snapshot_arguments(feature_parser)
    _add_raw_root(feature_parser)
    _add_clean_root(feature_parser)
    feature_parser.add_argument(
        "--feature-data-root",
        type=Path,
        default=Path("data/features/fpl"),
        help="Root directory for prediction features (default: %(default)s).",
    )

    prediction_parser = subparsers.add_parser(
        "predict-xfp",
        help="Generate explainable fixture and gameweek xFP v0.1 predictions.",
    )
    prediction_parser.add_argument(
        "--target-gameweek",
        type=int,
        required=True,
        help="Gameweek to predict from strictly earlier historical data.",
    )
    _add_snapshot_arguments(prediction_parser)
    _add_raw_root(prediction_parser)
    _add_clean_root(prediction_parser)
    prediction_parser.add_argument(
        "--feature-data-root",
        type=Path,
        default=Path("data/features/fpl"),
        help="Root directory for prediction features (default: %(default)s).",
    )
    prediction_parser.add_argument(
        "--prediction-data-root",
        type=Path,
        default=Path("data/predictions/fpl"),
        help="Root directory for xFP outputs (default: %(default)s).",
    )

    evaluation_parser = subparsers.add_parser(
        "evaluate-xfp",
        help="Evaluate frozen xFP predictions against finalized realized data.",
    )
    evaluation_parser.add_argument("--target-gameweek", type=int, required=True)
    evaluation_parser.add_argument("--model-version", default="v0.1")
    evaluation_parser.add_argument("--prediction-snapshot-timestamp")
    evaluation_parser.add_argument("--realized-snapshot-timestamp")
    evaluation_parser.add_argument("--top-n", type=int, default=10)
    evaluation_parser.add_argument("--season", default="2026-27")
    _add_raw_root(evaluation_parser)
    _add_clean_root(evaluation_parser)
    evaluation_parser.add_argument(
        "--feature-data-root",
        type=Path,
        default=Path("data/features/fpl"),
    )
    evaluation_parser.add_argument(
        "--prediction-data-root",
        type=Path,
        default=Path("data/predictions/fpl"),
    )
    evaluation_parser.add_argument(
        "--evaluation-data-root",
        type=Path,
        default=Path("data/evaluations/fpl"),
    )

    refresh_parser = subparsers.add_parser(
        "refresh",
        help="Create or explicitly resume one coherent official FPL snapshot.",
    )
    refresh_parser.add_argument("--season", default="2026-27")
    refresh_parser.add_argument(
        "--resume",
        dest="resume_snapshot_timestamp",
        metavar="SNAPSHOT_TIMESTAMP",
        help="Resume this incomplete refresh; completed snapshots are immutable.",
    )
    _add_raw_root(refresh_parser)
    _add_clean_root(refresh_parser)
    refresh_parser.add_argument(
        "--delay-seconds",
        type=float,
        default=DEFAULT_HISTORY_DELAY_SECONDS,
        help="Pacing delay between player-history requests (default: %(default)s).",
    )

    unlock_parser = subparsers.add_parser(
        "refresh-unlock",
        help="Manually remove one snapshot lock after verifying no refresh is running.",
    )
    unlock_parser.add_argument("--season", default="2026-27")
    unlock_parser.add_argument(
        "--snapshot",
        dest="snapshot_timestamp",
        required=True,
        metavar="SNAPSHOT_TIMESTAMP",
        help="Exact snapshot whose .refresh.lock should be removed.",
    )
    _add_raw_root(unlock_parser)

    historical_parser = subparsers.add_parser(
        "build-historical",
        help="Build pinned restricted/pseudo-backtest historical datasets.",
    )
    historical_parser.add_argument(
        "--raw-data-root",
        type=Path,
        default=Path("data/historical/raw"),
        help="Cache for immutable pinned historical source files (default: %(default)s).",
    )
    historical_parser.add_argument(
        "--clean-data-root",
        type=Path,
        default=Path("data/historical/clean"),
        help="Root for immutable historical Parquet outputs (default: %(default)s).",
    )

    backtest_parser = subparsers.add_parser(
        "backtest-xfp-v01",
        help="Measure frozen xFP v0.1 against immutable historical-v2 inputs.",
    )
    backtest_parser.add_argument(
        "--historical-clean-root",
        type=Path,
        default=Path("data/historical/clean"),
        help="Root containing historical-v2 inputs (default: %(default)s).",
    )
    backtest_parser.add_argument(
        "--backtest-root",
        type=Path,
        default=Path("data/historical/backtests"),
        help="Root for immutable backtest artifacts (default: %(default)s).",
    )
    experiment_parser = subparsers.add_parser(
        "experiment-minutes-v02",
        help="Run the preregistered historical expected-minutes experiment.",
    )
    experiment_parser.add_argument(
        "--historical-clean-root",
        type=Path,
        default=Path("data/historical/clean"),
        help="Root containing immutable historical-v2 inputs (default: %(default)s).",
    )
    experiment_parser.add_argument(
        "--baseline-root",
        type=Path,
        default=Path("data/historical/backtests"),
        help="Root containing the frozen v0.1 baseline (default: %(default)s).",
    )
    experiment_parser.add_argument(
        "--experiment-root",
        type=Path,
        default=Path("data/historical/experiments"),
        help="Root for immutable experiment artifacts (default: %(default)s).",
    )
    attacking_parser = subparsers.add_parser(
        "experiment-attacking-rates-v02",
        help="Run the preregistered historical attacking-rate experiment.",
    )
    attacking_parser.add_argument(
        "--historical-clean-root", type=Path,
        default=Path("data/historical/clean"),
        help="Root containing immutable historical-v2 inputs (default: %(default)s).",
    )
    attacking_parser.add_argument(
        "--baseline-root", type=Path,
        default=Path("data/historical/backtests"),
        help="Root containing the frozen v0.1 baseline (default: %(default)s).",
    )
    attacking_parser.add_argument(
        "--experiment-root", type=Path,
        default=Path("data/historical/experiments"),
        help="Root for immutable experiment artifacts (default: %(default)s).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in COMMANDS:
        arguments.insert(0, "fetch")
    args = build_parser().parse_args(arguments)

    if args.command == "fetch":
        try:
            fetch_bootstrap_static(data_root=args.data_root, season=args.season)
        except FetchError as exc:
            logging.error("FPL fetch failed: %s", exc)
            return 1
    elif args.command == "transform-players":
        try:
            transform_latest_players(
                raw_data_root=args.raw_data_root,
                clean_data_root=args.clean_data_root,
                season=args.season,
            )
        except TransformationError as exc:
            logging.error("Player transformation failed: %s", exc)
            return 1
    elif args.command == "fetch-fixtures":
        try:
            fetch_fixtures_for_snapshot(
                raw_data_root=args.raw_data_root,
                season=args.season,
                snapshot_timestamp=args.snapshot_timestamp,
            )
        except OfficialDataError as exc:
            logging.error("Fixture fetch failed: %s", exc)
            return 1
    elif args.command == "fetch-player-history":
        try:
            fetch_player_histories_for_snapshot(
                raw_data_root=args.raw_data_root,
                season=args.season,
                snapshot_timestamp=args.snapshot_timestamp,
                delay_seconds=args.delay_seconds,
            )
        except OfficialDataError as exc:
            logging.error("Player-history fetch failed: %s", exc)
            return 1
    elif args.command == "transform-fixtures":
        try:
            transform_fixtures_for_snapshot(
                raw_data_root=args.raw_data_root,
                clean_data_root=args.clean_data_root,
                season=args.season,
                snapshot_timestamp=args.snapshot_timestamp,
            )
        except (OfficialDataError, TransformationError) as exc:
            logging.error("Fixture transformation failed: %s", exc)
            return 1
    elif args.command == "transform-player-history":
        try:
            transform_player_history_for_snapshot(
                raw_data_root=args.raw_data_root,
                clean_data_root=args.clean_data_root,
                season=args.season,
                snapshot_timestamp=args.snapshot_timestamp,
            )
        except (OfficialDataError, TransformationError) as exc:
            logging.error("Player-history transformation failed: %s", exc)
            return 1
    elif args.command == "build-features":
        try:
            output_path = build_player_gameweek_features(
                target_gameweek=args.target_gameweek,
                raw_data_root=args.raw_data_root,
                clean_data_root=args.clean_data_root,
                feature_data_root=args.feature_data_root,
                season=args.season,
                snapshot_timestamp=args.snapshot_timestamp,
            )
            logging.info("Feature dataset saved to %s", output_path)
        except TransformationError as exc:
            logging.error("Feature build failed: %s", exc)
            return 1
    elif args.command == "predict-xfp":
        try:
            outputs = predict_xfp_v01(
                target_gameweek=args.target_gameweek,
                raw_data_root=args.raw_data_root,
                clean_data_root=args.clean_data_root,
                feature_data_root=args.feature_data_root,
                prediction_data_root=args.prediction_data_root,
                season=args.season,
                snapshot_timestamp=args.snapshot_timestamp,
            )
            logging.info(
                "Fixture predictions saved to %s (%s rows)",
                outputs.fixture_path,
                outputs.fixture_rows,
            )
            logging.info(
                "Gameweek predictions saved to %s (%s rows)",
                outputs.gameweek_path,
                outputs.gameweek_rows,
            )
        except TransformationError as exc:
            logging.error("xFP v0.1 prediction failed: %s", exc)
            return 1
    elif args.command == "evaluate-xfp":
        try:
            outputs = evaluate_xfp(
                target_gameweek=args.target_gameweek,
                model_version=args.model_version,
                prediction_snapshot_timestamp=args.prediction_snapshot_timestamp,
                realized_snapshot_timestamp=args.realized_snapshot_timestamp,
                raw_data_root=args.raw_data_root,
                clean_data_root=args.clean_data_root,
                feature_data_root=args.feature_data_root,
                prediction_data_root=args.prediction_data_root,
                evaluation_data_root=args.evaluation_data_root,
                season=args.season,
                top_n=args.top_n,
            )
            logging.info(
                "Evaluation saved to %s (%s player rows; %s evaluated)",
                outputs.directory,
                outputs.player_rows,
                outputs.evaluated_players,
            )
        except TransformationError as exc:
            logging.error("xFP evaluation failed: %s", exc)
            return 1
    elif args.command == "refresh":
        try:
            result = refresh_fpl_data(
                raw_data_root=args.raw_data_root,
                clean_data_root=args.clean_data_root,
                season=args.season,
                resume_snapshot_timestamp=args.resume_snapshot_timestamp,
                history_delay_seconds=args.delay_seconds,
            )
            logging.info("Snapshot timestamp: %s", result.snapshot_timestamp)
            logging.info("Raw snapshot: %s", result.raw_directory)
            logging.info("Clean snapshot: %s", result.clean_directory)
            logging.info("Refresh manifest: %s", result.manifest_path)
        except RefreshError as exc:
            logging.error("FPL refresh failed: %s", exc)
            return 1
    elif args.command == "refresh-unlock":
        try:
            result = unlock_refresh_snapshot(
                raw_data_root=args.raw_data_root,
                season=args.season,
                snapshot_timestamp=args.snapshot_timestamp,
            )
            logging.info("Refresh lock removed: %s", result.lock_path)
        except RefreshError as exc:
            logging.error("Refresh unlock failed: %s", exc)
            return 1
    elif args.command == "build-historical":
        try:
            result = build_historical_datasets(
                raw_data_root=args.raw_data_root,
                clean_data_root=args.clean_data_root,
            )
            logging.info("Historical datasets saved to %s", result.directory)
            logging.info("Historical manifest: %s", result.manifest_path)
        except HistoricalIngestionError as exc:
            logging.error("Historical ingestion failed: %s", exc)
            return 1
    elif args.command == "backtest-xfp-v01":
        try:
            result = build_historical_xfp_v01_backtest(
                historical_clean_root=args.historical_clean_root,
                backtest_root=args.backtest_root,
            )
            logging.info(
                "Historical xFP v0.1 backtest saved to %s (%s observations; %s pairs)",
                result.directory,
                result.observations,
                result.modeled_complete_pairs,
            )
        except HistoricalBacktestError as exc:
            logging.error("Historical xFP v0.1 backtest failed: %s", exc)
            return 1
    elif args.command == "experiment-minutes-v02":
        try:
            result = run_historical_minutes_experiment(
                historical_clean_root=args.historical_clean_root,
                baseline_root=args.baseline_root,
                experiment_root=args.experiment_root,
            )
            logging.info("Expected-minutes experiment saved to %s", result.directory)
            logging.info("Development winner: %s", result.development_winner or "none")
            logging.info("Decision: %s", result.final_decision)
        except HistoricalMinutesExperimentError as exc:
            logging.error("Expected-minutes experiment failed: %s", exc)
            return 1
    else:
        try:
            result = run_historical_attacking_rate_experiment(
                historical_clean_root=args.historical_clean_root,
                baseline_root=args.baseline_root,
                experiment_root=args.experiment_root,
            )
            logging.info("Attacking-rate experiment saved to %s", result.directory)
            logging.info("Development winner: %s", result.development_winner or "none")
            logging.info("Decision: %s", result.final_decision)
        except HistoricalAttackingRateExperimentError as exc:
            logging.error("Attacking-rate experiment failed: %s", exc)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
