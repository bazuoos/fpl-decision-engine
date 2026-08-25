"""Command-line entry point for the FPL data pipeline."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .pipeline import FetchError, fetch_bootstrap_static


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch and snapshot the official FPL bootstrap-static dataset."
    )
    parser.add_argument(
        "--season",
        default="2026-27",
        help="Season directory name (default: %(default)s).",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data/raw/fpl"),
        help="Root directory for raw FPL snapshots (default: %(default)s).",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args()

    try:
        fetch_bootstrap_static(data_root=args.data_root, season=args.season)
    except FetchError as exc:
        logging.error("FPL fetch failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

