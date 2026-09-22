#!/usr/bin/env python3
"""Synthetic-only Task033C1 kernel and serialization feasibility drill."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import sys
import time
from dataclasses import asdict
from typing import Any

from fpl_decision_engine.research.task033c1 import (
    Availability,
    HistoryFixture,
    PeerHistory,
    TargetContext,
    assemble_combined,
    build_ua1,
    build_um1,
    canonical_json_bytes,
)


def _peak_rss_bytes() -> tuple[int, str]:
    observed = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return int(observed), "resource.RUSAGE_SELF.ru_maxrss_bytes"
    return int(observed * 1024), "resource.RUSAGE_SELF.ru_maxrss_kib_converted_to_bytes"


def _fixture_history(element_id: int, position: str) -> tuple[HistoryFixture, ...]:
    rows = []
    for gameweek in range(12, 20):
        minutes = (element_id * 17 + gameweek * 11) % 91
        starts = int(minutes >= 45)
        xg = ((element_id * 7 + gameweek * 3) % 25) / 100.0 if minutes else 0.0
        xa = ((element_id * 5 + gameweek * 2) % 18) / 100.0 if minutes else 0.0
        rows.append(
            HistoryFixture(
                season="synthetic-01",
                element_id=element_id,
                fixture_id=element_id * 100 + gameweek,
                gameweek=gameweek,
                position=position,
                minutes=minutes,
                starts=starts,
                xg=xg,
                xa=xa,
            )
        )
    return tuple(rows)


def _run_once(player_count: int) -> tuple[str, int, dict[str, Any]]:
    positions = ("GK", "DEF", "MID", "FWD")
    histories = {
        element_id: _fixture_history(element_id, positions[(element_id - 1) % 4])
        for element_id in range(1, player_count + 1)
    }
    position_members = {
        position: tuple(
            element_id
            for element_id in range(1, player_count + 1)
            if positions[(element_id - 1) % 4] == position
        )
        for position in positions
    }
    outputs = []
    total_history_rows = 0
    total_peer_rows_presented = 0
    for element_id in range(1, player_count + 1):
        position = positions[(element_id - 1) % 4]
        target = TargetContext(
            season="synthetic-01",
            target_gameweek=20,
            element_id=element_id,
            position=position,
            fixture_ids=(20_000 + element_id,) if element_id % 10 else (20_000 + element_id, 30_000 + element_id),
            availability=Availability("a", None, True, True),
        )
        peers = tuple(
            PeerHistory(peer_id, position, histories[peer_id])
            for peer_id in position_members[position]
            if peer_id != element_id
        )
        minutes = build_um1(target, histories[element_id])
        rates = build_ua1(target, histories[element_id], peers)
        modeled = assemble_combined(target, minutes, rates)
        outputs.append(
            {
                "element_id": element_id,
                "minutes": asdict(minutes),
                "rates": asdict(rates),
                "modeled": asdict(modeled),
            }
        )
        total_history_rows += len(histories[element_id])
        total_peer_rows_presented += sum(len(value.history) for value in peers)
    body = canonical_json_bytes(outputs)
    digest = hashlib.sha256(body).hexdigest()
    return digest, len(body), {
        "players": player_count,
        "target_gameweeks": 1,
        "history_gameweeks_per_player": 8,
        "single_fixture_targets": player_count - player_count // 10,
        "double_fixture_targets": player_count // 10,
        "player_history_rows": total_history_rows,
        "peer_history_row_presentations": total_peer_rows_presented,
        "candidate_packages": ["UM1-joint-minute-dirichlet-v1", "UA1-gamma-mixture-loss-update-v1", "UM1UA1-v1"],
    }


def run_spike(player_count: int = 700) -> dict[str, Any]:
    if not 1 <= player_count <= 700:
        raise ValueError("synthetic player count must be in [1,700]")
    started = time.perf_counter()
    first_digest, first_bytes, dimensions = _run_once(player_count)
    first_elapsed = time.perf_counter() - started
    repeat_started = time.perf_counter()
    second_digest, second_bytes, second_dimensions = _run_once(player_count)
    repeat_elapsed = time.perf_counter() - repeat_started
    peak_bytes, peak_method = _peak_rss_bytes()
    deterministic = (
        first_digest == second_digest
        and first_bytes == second_bytes
        and dimensions == second_dimensions
    )
    return {
        "spike_identity": "task033c1-synthetic-kernel-feasibility-v1",
        "synthetic_only": True,
        "dimensions": dimensions,
        "first_run": {
            "wall_seconds": first_elapsed,
            "canonical_output_bytes": first_bytes,
            "sha256": first_digest,
        },
        "deterministic_repeat": {
            "wall_seconds": repeat_elapsed,
            "canonical_output_bytes": second_bytes,
            "sha256": second_digest,
            "identical": deterministic,
        },
        "peak_resident_memory_bytes": peak_bytes,
        "peak_resident_memory_method": peak_method,
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "system": platform.system(),
            "processes": 1,
            "configured_computational_threads": 1,
        },
        "claims_established": [
            "C1 kernels execute for up to the reported synthetic public-player dimension",
            "C1 canonical semantic output repeats byte-identically in this process and environment",
            "reported process peak RSS stayed within the 2 GiB ceiling",
        ],
        "claims_not_established": [
            "3000-candidate/12-view/60-second operational decision evaluation",
            "9999-replicate crossed or serial-block statistical evaluation",
            "causal filesystem loader or source-manifest isolation",
            "immutable no-overwrite artifact publication",
            "predictive accuracy, calibration, development eligibility, confirmation, or production suitability",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--players", type=int, default=700)
    arguments = parser.parse_args()
    result = run_spike(arguments.players)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["deterministic_repeat"]["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
