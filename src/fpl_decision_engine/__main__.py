"""Command-line entry point for the FPL data pipeline."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from collections.abc import Sequence

from .pipeline import FetchError, fetch_bootstrap_static
from .transform import TransformationError, transform_latest_players


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in {"fetch", "transform-players"}:
        arguments.insert(0, "fetch")
    args = build_parser().parse_args(arguments)

    if args.command == "fetch":
        try:
            fetch_bootstrap_static(data_root=args.data_root, season=args.season)
        except FetchError as exc:
            logging.error("FPL fetch failed: %s", exc)
            return 1
    else:
        try:
            transform_latest_players(
                raw_data_root=args.raw_data_root,
                clean_data_root=args.clean_data_root,
                season=args.season,
            )
        except TransformationError as exc:
            logging.error("Player transformation failed: %s", exc)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
