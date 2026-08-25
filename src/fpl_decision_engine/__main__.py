"""Command-line entry point for the FPL data pipeline."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from .gameweek_transform import (
    transform_fixtures_for_snapshot,
    transform_player_history_for_snapshot,
)
from .official_data import (
    DEFAULT_HISTORY_DELAY_SECONDS,
    OfficialDataError,
    fetch_fixtures_for_snapshot,
    fetch_player_histories_for_snapshot,
)
from .pipeline import FetchError, fetch_bootstrap_static
from .transform import TransformationError, transform_latest_players

COMMANDS = {
    "fetch",
    "transform-players",
    "fetch-fixtures",
    "fetch-player-history",
    "transform-fixtures",
    "transform-player-history",
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
    else:
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
