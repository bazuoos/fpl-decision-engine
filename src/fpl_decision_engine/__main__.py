"""Command-line entry point for the FPL data pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from .features import build_player_gameweek_features
from .decision import (
    DECISION_OUTPUT_CLASSIFICATION,
    DecisionError,
    budget_m_to_units,
    decision_result_dict,
    optimize_squad,
    optimize_xi,
    rank_players,
    resolve_existing_squad,
    write_decision_artifacts,
)
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
from .historical_calibration_experiment import (
    HistoricalCalibrationExperimentError,
    run_historical_calibration_experiment,
)
from .historical_minutes_experiment import (
    HistoricalMinutesExperimentError,
    run_historical_minutes_experiment,
)
from .historical_opponent_strength_experiment import (
    HistoricalOpponentStrengthExperimentError,
    run_historical_opponent_strength_experiment,
)
from .manager_decision import (
    ManagerDecisionError,
    evaluate_current_squad,
    manager_decision_payload,
    write_manager_decision,
)
from .manager_state import (
    FRESHNESS_WARNING,
    POST_DEADLINE_WARNING,
    ManagerStateError,
    PublicFPLManagerStateProvider,
)
from .official_data import (
    DEFAULT_HISTORY_DELAY_SECONDS,
    OfficialDataError,
    fetch_fixtures_for_snapshot,
    fetch_player_histories_for_snapshot,
)
from .pipeline import FetchError, fetch_bootstrap_static
from .predictions import predict_xfp_v01
from .projection_provider import ProjectionProviderError, XfpV01ParquetProvider
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
    "experiment-calibration-v02",
    "experiment-opponent-strength-v02",
    "rank-players",
    "optimize-xi",
    "optimize-squad",
    "evaluate-entry",
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


def _add_decision_provider_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--season", default="2026-27")
    parser.add_argument("--target-gameweek", type=int, required=True)
    parser.add_argument(
        "--provider",
        choices=("xfp-v01",),
        default="xfp-v01",
        help="Projection provider (default: %(default)s).",
    )
    parser.add_argument(
        "--projection-artifact",
        type=Path,
        help="Explicit immutable player-GW projection Parquet; defaults to latest.",
    )
    parser.add_argument(
        "--players-artifact",
        type=Path,
        help="Explicit matching clean players Parquet; inferred from snapshot by default.",
    )
    parser.add_argument(
        "--prediction-data-root",
        type=Path,
        default=Path("data/predictions/fpl"),
    )
    parser.add_argument(
        "--clean-data-root",
        type=Path,
        default=Path("data/clean/fpl"),
    )
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable JSON."
    )


def _decision_provider(args: argparse.Namespace) -> XfpV01ParquetProvider:
    return XfpV01ParquetProvider(
        projection_artifact=args.projection_artifact,
        players_artifact=args.players_artifact,
        prediction_data_root=args.prediction_data_root,
        clean_data_root=args.clean_data_root,
    )


def _print_decision_summary(payload: dict[str, object]) -> None:
    print(DECISION_OUTPUT_CLASSIFICATION)
    print(
        f"Formation {payload['formation']} | captain {payload['captain']} | "
        f"vice {payload['vice_captain']}"
    )
    print(
        f"Cost £{payload['squad_cost_m']:.1f}m | remaining "
        f"£{payload['remaining_budget_m']:.1f}m"
    )
    print(
        f"Base XI {payload['base_xi_projection']:.3f} + captain bonus "
        f"{payload['captain_bonus']:.3f} = {payload['total_objective']:.3f}"
    )
    for row in payload["players"]:  # type: ignore[index]
        role = "XI" if row["starter"] else "BENCH"
        if row["captain"]:
            role += " C"
        elif row["vice_captain"]:
            role += " VC"
        print(
            f"{role:8} {row['position']:3} {row['player_name']:<20} "
            f"{row['team']:<4} £{row['price_m']:.1f}m  {row['projection']:.3f}"
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
    calibration_parser = subparsers.add_parser(
        "experiment-calibration-v02",
        help="Run the preregistered historical xFP calibration experiment.",
    )
    calibration_parser.add_argument(
        "--historical-clean-root", type=Path,
        default=Path("data/historical/clean"),
        help="Root containing immutable historical-v2 inputs (default: %(default)s).",
    )
    calibration_parser.add_argument(
        "--baseline-root", type=Path,
        default=Path("data/historical/backtests"),
        help="Root containing the frozen v0.1 baseline (default: %(default)s).",
    )
    calibration_parser.add_argument(
        "--experiment-root", type=Path,
        default=Path("data/historical/experiments"),
        help="Root for immutable experiment artifacts (default: %(default)s).",
    )
    opponent_parser = subparsers.add_parser(
        "experiment-opponent-strength-v02",
        help="Run the preregistered causal opponent-strength experiment.",
    )
    opponent_parser.add_argument(
        "--historical-clean-root", type=Path,
        default=Path("data/historical/clean"),
        help="Root containing immutable historical-v2 inputs (default: %(default)s).",
    )
    opponent_parser.add_argument(
        "--baseline-root", type=Path,
        default=Path("data/historical/backtests"),
        help="Root containing the frozen v0.1 baseline (default: %(default)s).",
    )
    opponent_parser.add_argument(
        "--experiment-root", type=Path,
        default=Path("data/historical/experiments"),
        help="Root for immutable experiment artifacts (default: %(default)s).",
    )

    rank_parser = subparsers.add_parser(
        "rank-players", help="Rank eligible projections within each FPL position."
    )
    _add_decision_provider_arguments(rank_parser)
    rank_parser.add_argument(
        "--limit", type=int, default=10, help="Rows to display per position."
    )

    xi_parser = subparsers.add_parser(
        "optimize-xi", help="Select an optimal XI/captain from 15 explicit player IDs."
    )
    _add_decision_provider_arguments(xi_parser)
    xi_parser.add_argument("--player-ids", type=int, nargs="+", required=True)
    xi_parser.add_argument(
        "--budget",
        help="Optional squad budget in £m, in exact £0.1m increments.",
    )

    squad_parser = subparsers.add_parser(
        "optimize-squad", help="Select an optimal legal squad, XI and captain."
    )
    _add_decision_provider_arguments(squad_parser)
    squad_parser.add_argument(
        "--budget", default="100.0", help="Squad budget in £m (default: %(default)s)."
    )
    squad_parser.add_argument(
        "--decision-data-root",
        type=Path,
        default=Path("data/decisions/fpl"),
    )

    entry_parser = subparsers.add_parser(
        "evaluate-entry",
        help="Evaluate a public locked manager squad with matching projections.",
    )
    entry_parser.add_argument("--entry-id", type=int, required=True)
    entry_parser.add_argument("--season", default="2026-27")
    entry_parser.add_argument(
        "--event",
        type=int,
        help="Exact locked public event; defaults to the latest available deadline.",
    )
    entry_parser.add_argument(
        "--target-gameweek",
        type=int,
        help="Projection target; defaults to the represented locked event.",
    )
    entry_parser.add_argument("--provider", choices=("xfp-v01",), default="xfp-v01")
    entry_parser.add_argument("--projection-artifact", type=Path)
    entry_parser.add_argument("--players-artifact", type=Path)
    entry_parser.add_argument(
        "--prediction-data-root", type=Path, default=Path("data/predictions/fpl")
    )
    entry_parser.add_argument(
        "--clean-data-root", type=Path, default=Path("data/clean/fpl")
    )
    entry_parser.add_argument(
        "--manager-raw-root", type=Path, default=Path("data/manager/raw/fpl")
    )
    entry_parser.add_argument(
        "--manager-decision-root",
        type=Path,
        default=Path("data/manager/decisions/fpl"),
    )
    entry_parser.add_argument(
        "--budget", default="100.0", help="Unconstrained benchmark budget in £m."
    )
    entry_parser.add_argument(
        "--skip-unconstrained-benchmark",
        action="store_true",
        help="Skip the informational full-pool benchmark.",
    )
    entry_parser.add_argument("--json", action="store_true")
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
    elif args.command in {"rank-players", "optimize-xi", "optimize-squad"}:
        try:
            dataset = _decision_provider(args).load(
                season=args.season, target_gameweek=args.target_gameweek
            )
            rankings = rank_players(dataset)
            if args.command == "rank-players":
                payload = {
                    "classification": DECISION_OUTPUT_CLASSIFICATION,
                    "season": dataset.season,
                    "target_gameweek": dataset.target_gameweek,
                    "projection_provider_id": dataset.provider_id,
                    "projection_model_id": dataset.source_model_id,
                    "projection_model_scope": dataset.model_scope,
                    "source_artifact_path": dataset.source_artifact_path,
                    "source_artifact_sha256": dataset.source_artifact_sha256,
                    "excluded_player_counts": rankings.excluded_counts,
                    "rankings": [
                        {
                            "position": row.player.position,
                            "rank": row.position_rank,
                            "fpl_player_id": row.player.fpl_player_id,
                            "player_name": row.player.player_name,
                            "team": row.player.team_short_name,
                            "price_m": row.player.price_m,
                            "projection": row.player.projection,
                            "projection_state": row.player.projection_state.value,
                            "availability_status": row.player.availability_status,
                        }
                        for row in rankings.rows
                    ],
                }
                if args.json:
                    print(json.dumps(payload, indent=2, sort_keys=True))
                else:
                    print(DECISION_OUTPUT_CLASSIFICATION)
                    for position in ("GK", "DEF", "MID", "FWD"):
                        print(f"\n{position}")
                        shown = 0
                        for row in rankings.rows:
                            if row.player.position != position or shown >= args.limit:
                                continue
                            print(
                                f"{row.position_rank:>2}. {row.player.player_name:<20} "
                                f"{row.player.team_short_name:<4} "
                                f"£{row.player.price_m:.1f}m  {float(row.player.projection):.3f}"
                            )
                            shown += 1
                    print(f"\nExcluded: {rankings.excluded_counts}")
            elif args.command == "optimize-xi":
                budget_units = (
                    budget_m_to_units(args.budget) if args.budget is not None else None
                )
                squad = resolve_existing_squad(
                    dataset, args.player_ids, budget_units=budget_units
                )
                result = optimize_xi(
                    squad,
                    budget_units=budget_units,
                    excluded_counts=rankings.excluded_counts,
                )
                payload = decision_result_dict(dataset, result)
                if args.json:
                    print(json.dumps(payload, indent=2, sort_keys=True))
                else:
                    _print_decision_summary(payload)
            else:
                budget_units = budget_m_to_units(args.budget)
                result = optimize_squad(dataset, budget_units=budget_units)
                artifacts = write_decision_artifacts(
                    dataset,
                    rankings,
                    result,
                    decision_data_root=args.decision_data_root,
                )
                payload = decision_result_dict(dataset, result)
                payload["artifact_directory"] = str(artifacts.directory)
                payload["artifact_hashes"] = {
                    "within_position_rankings.parquet": artifacts.rankings_sha256,
                    "optimized_squad.parquet": artifacts.squad_sha256,
                    "decision_manifest.json": artifacts.manifest_sha256,
                }
                if args.json:
                    print(json.dumps(payload, indent=2, sort_keys=True))
                else:
                    _print_decision_summary(payload)
                    print(f"Artifacts: {artifacts.directory}")
                    print(f"Excluded: {rankings.excluded_counts}")
        except (ProjectionProviderError, DecisionError) as exc:
            logging.error("Decision operation failed: %s", exc)
            return 1
    elif args.command == "evaluate-entry":
        try:
            state = PublicFPLManagerStateProvider(
                raw_data_root=args.manager_raw_root
            ).fetch(
                entry_id=args.entry_id,
                season=args.season,
                represented_event=args.event,
            )
            logging.info(
                "Retrieved public manager state: event %s, deadline %s, semantics %s",
                state.represented_event,
                state.deadline_time,
                state.state_semantics,
            )
            target_gameweek = args.target_gameweek or state.represented_event
            projections = XfpV01ParquetProvider(
                projection_artifact=args.projection_artifact,
                players_artifact=args.players_artifact,
                prediction_data_root=args.prediction_data_root,
                clean_data_root=args.clean_data_root,
            ).load(season=args.season, target_gameweek=target_gameweek)
            benchmark = None
            if not args.skip_unconstrained_benchmark:
                benchmark = optimize_squad(
                    projections, budget_units=budget_m_to_units(args.budget)
                )
            result = evaluate_current_squad(
                state, projections, unconstrained_benchmark=benchmark
            )
            artifacts = write_manager_decision(
                result, decision_data_root=args.manager_decision_root
            )
            payload = manager_decision_payload(result)
            payload["decision_artifact_path"] = str(artifacts.manifest_path)
            payload["decision_artifact_sha256"] = artifacts.manifest_sha256
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(FRESHNESS_WARNING)
                print(POST_DEADLINE_WARNING)
                print(
                    f"Represented event: GW{state.represented_event} "
                    f"(deadline {state.deadline_time})"
                )
                print(f"Public squad IDs: {[row.element_id for row in state.picks]}")
                print(f"Manager locked XI: {list(state.manager_xi)}")
                print(
                    f"Manager captain/vice: {state.manager_captain}/"
                    f"{state.manager_vice_captain}"
                )
                print(f"Projection reconciliation: {result.reconciliation_status}")
                if result.optimized_result is not None:
                    optimized = result.optimized_result
                    print(
                        f"Optimized owned-squad XI: "
                        f"{[row.player.fpl_player_id for row in optimized.selections if row.is_starter]}"
                    )
                    print(
                        f"Captain/vice: {optimized.captain.player_name}/"
                        f"{optimized.vice_captain.player_name}"
                    )
                    print(
                        f"Objective: {optimized.base_xi_projection:.3f} + "
                        f"{optimized.captain_bonus:.3f} = {optimized.total_objective:.3f}"
                    )
                    print(
                        "Modeled component projection difference: "
                        f"{result.modeled_component_projection_difference}"
                    )
                    for change in result.change_list:
                        print(change)
                else:
                    print(
                        "Owned-squad optimization unavailable: "
                        f"incomplete={list(result.incomplete_owned_player_ids)}, "
                        f"missing={list(result.missing_owned_player_ids)}, "
                        f"unresolved={list(result.unresolved_projection_player_ids)}"
                    )
                print("Unconstrained benchmark is informational and not a transfer plan.")
                print("Transfer optimization not performed.")
                print(f"Decision artifact: {artifacts.manifest_path}")
        except (ManagerStateError, ManagerDecisionError, ProjectionProviderError, DecisionError) as exc:
            logging.error("Public manager evaluation failed: %s", exc)
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
    elif args.command == "experiment-attacking-rates-v02":
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
    elif args.command == "experiment-calibration-v02":
        try:
            result = run_historical_calibration_experiment(
                historical_clean_root=args.historical_clean_root,
                baseline_root=args.baseline_root,
                experiment_root=args.experiment_root,
            )
            logging.info("Calibration experiment saved to %s", result.directory)
            logging.info("Development winner: %s", result.development_winner or "none")
            logging.info("Decision: %s", result.final_decision)
        except HistoricalCalibrationExperimentError as exc:
            logging.error("Calibration experiment failed: %s", exc)
            return 1
    else:
        try:
            result = run_historical_opponent_strength_experiment(
                historical_clean_root=args.historical_clean_root,
                baseline_root=args.baseline_root,
                experiment_root=args.experiment_root,
            )
            logging.info("Opponent-strength experiment saved to %s", result.directory)
            logging.info("Development winner: %s", result.development_winner or "none")
            logging.info("Decision: %s", result.final_decision)
        except HistoricalOpponentStrengthExperimentError as exc:
            logging.error("Opponent-strength experiment failed: %s", exc)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
