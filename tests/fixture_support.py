"""Test-only artifact fixtures for fresh-checkout integration coverage."""

from __future__ import annotations

import hashlib
import heapq
import json
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import duckdb


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
FROZEN_GW2_ROOT = FIXTURE_ROOT / "frozen_gw2"
ARTIFACT_METADATA_ROOT = FIXTURE_ROOT / "artifact_metadata"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class FrozenGW2Fixture:
    root: Path
    decision: Path
    decision_template: Path
    candidates: Path
    features: Path
    fixture_predictions: Path
    gameweek_predictions: Path
    players: Path
    manual_state: Path
    manual_state_pre_prices: Path
    manager_state_manifest: Path
    task014_manifest: Path
    task014_squad: Path
    task014_rankings: Path


@contextmanager
def materialized_frozen_gw2() -> Iterator[FrozenGW2Fixture]:
    """Materialize portable paths from committed copies of reviewed GW2 bytes."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for source in FROZEN_GW2_ROOT.iterdir():
            if source.is_file() and source.name != "README.md":
                shutil.copy2(source, root / source.name)

        decision_template = root / "one_transfer_decision.template.json"
        decision_payload = json.loads(decision_template.read_bytes())
        candidates = root / "legal_transfer_candidates.json"
        features = root / "player_gameweek_features.parquet"
        fixture_predictions = root / "xfp_v01_fixtures.parquet"
        gameweek_predictions = root / "xfp_v01_gameweek.parquet"
        players = root / "players.parquet"
        manual_state = root / "manual_editable_state.json"
        decision_payload["candidate_summaries_artifact"]["path"] = str(candidates)
        decision_payload["projection_provenance"]["artifact_path"] = str(
            gameweek_predictions
        )
        decision_payload["purchase_price_provenance"]["players_artifact_path"] = str(
            players
        )
        decision_payload["manual_state"]["artifact_path"] = str(manual_state)
        decision = root / "one_transfer_decision.json"
        decision.write_text(
            json.dumps(decision_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        yield FrozenGW2Fixture(
            root=root,
            decision=decision,
            decision_template=decision_template,
            candidates=candidates,
            features=features,
            fixture_predictions=fixture_predictions,
            gameweek_predictions=gameweek_predictions,
            players=players,
            manual_state=manual_state,
            manual_state_pre_prices=root / "manual_editable_state_pre_prices.json",
            manager_state_manifest=root / "manager_state_manifest.json",
            task014_manifest=root / "task014_decision_manifest.json",
            task014_squad=root / "optimized_squad.parquet",
            task014_rankings=root / "within_position_rankings.parquet",
        )


def _write_table(connection: duckdb.DuckDBPyConnection, table: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection.execute(f"COPY {table} TO ? (FORMAT PARQUET)", [str(path)])


def _synthetic_fixture_rows() -> list[tuple[int, str, str]]:
    teams = [
        "Arsenal",
        "Wolves",
        "Crystal Palace",
        "Man City",
        "Bournemouth",
        "Brighton",
        "Burnley",
        "Chelsea",
        "Leeds",
        "Aston Villa",
        "Brentford",
        "Everton",
        "Fulham",
        "Liverpool",
        "Man Utd",
        "Newcastle",
        "Nott'm Forest",
        "Sunderland",
        "Spurs",
        "West Ham",
    ]
    changes = {
        26: {"Arsenal": 1, "Wolves": 1},
        31: {"Arsenal": -1, "Crystal Palace": -1, "Man City": -1, "Wolves": -1},
        33: {
            "Bournemouth": 1,
            "Brighton": 1,
            "Burnley": 1,
            "Chelsea": 1,
            "Leeds": 1,
            "Man City": 1,
        },
        34: {
            "Bournemouth": -1,
            "Brighton": -1,
            "Burnley": -1,
            "Chelsea": -1,
            "Leeds": -1,
            "Man City": -1,
        },
        36: {"Crystal Palace": 1, "Man City": 1},
    }
    fixtures: list[tuple[int, str, str]] = []
    for gameweek in range(1, 39):
        counts = {team: 1 + changes.get(gameweek, {}).get(team, 0) for team in teams}
        heap = [(-count, team) for team, count in counts.items() if count]
        heapq.heapify(heap)
        while heap:
            home_count, home = heapq.heappop(heap)
            away_count, away = heapq.heappop(heap)
            fixtures.append((gameweek, home, away))
            if home_count + 1 < 0:
                heapq.heappush(heap, (home_count + 1, home))
            if away_count + 1 < 0:
                heapq.heappush(heap, (away_count + 1, away))
    if len(fixtures) != 380:
        raise AssertionError("synthetic historical fixture must contain 380 rows")
    return fixtures


@dataclass(frozen=True)
class HistoricalFixture:
    root: Path
    metadata_manifest: Path
    synthetic_manifest: Path
    v2_manifest: Path
    v3_manifest: Path
    v31_manifest: Path


@contextmanager
def materialized_historical_fixture() -> Iterator[HistoricalFixture]:
    """Build synthetic Parquets for contracts, never production-byte validation."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "historical-v3.1"
        season_root = root / "2025-26"
        season_root.mkdir(parents=True)
        connection = duckdb.connect(":memory:")
        try:
            connection.execute(
                """CREATE TABLE player_fixture AS
                   SELECT ((i - 1) % 840 + 1)::BIGINT AS element_id,
                          i::BIGINT AS fixture_id,
                          0.1::DOUBLE AS xg,
                          0.05::DOUBLE AS xa,
                          CASE WHEN i <= 11492 THEN 90 ELSE 0 END::INTEGER AS minutes
                     FROM range(1, 29748) AS rows(i)"""
            )
            connection.execute(
                "CREATE TABLE identity(element_id BIGINT); INSERT INTO identity VALUES (841)"
            )
            connection.execute(
                """CREATE TABLE player_state AS
                   SELECT gameweek::INTEGER AS target_gameweek, 1::BIGINT AS element_id
                     FROM range(1, 39) AS rows(gameweek)"""
            )
            connection.execute("CREATE TABLE features(element_id BIGINT)")
            connection.execute("CREATE TABLE actuals(element_id BIGINT)")
            connection.execute("CREATE TABLE exceptions(element_id BIGINT)")
            connection.execute(
                "CREATE TABLE fixtures(gameweek INTEGER, home_team_name VARCHAR, away_team_name VARCHAR)"
            )
            connection.executemany(
                "INSERT INTO fixtures VALUES (?, ?, ?)", _synthetic_fixture_rows()
            )
            tables = {
                "historical_player_fixture.parquet": "player_fixture",
                "historical_player_identity.parquet": "identity",
                "historical_predeadline_player_state.parquet": "player_state",
                "historical_prediction_features.parquet": "features",
                "historical_prediction_actuals.parquet": "actuals",
                "historical_reconciliation_exceptions.parquet": "exceptions",
                "historical_fixtures.parquet": "fixtures",
            }
            outputs = []
            for filename, table in tables.items():
                path = season_root / filename
                _write_table(connection, table, path)
                outputs.append(
                    {
                        "path": f"2025-26/{filename}",
                        "sha256": sha256_file(path),
                    }
                )
        finally:
            connection.close()

        metadata_manifest = ARTIFACT_METADATA_ROOT / "historical-v3.1-manifest.json"
        manifest = json.loads(metadata_manifest.read_bytes())
        manifest["outputs"] = outputs
        synthetic_manifest = root / "historical_ingestion_manifest.json"
        synthetic_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        yield HistoricalFixture(
            root=root,
            metadata_manifest=metadata_manifest,
            synthetic_manifest=synthetic_manifest,
            v2_manifest=ARTIFACT_METADATA_ROOT / "historical-v2-manifest.json",
            v3_manifest=ARTIFACT_METADATA_ROOT / "historical-v3-manifest.json",
            v31_manifest=metadata_manifest,
        )


@contextmanager
def materialized_task018d_fixture() -> Iterator[tuple[Path, Path]]:
    """Pair reviewed manifest metadata with metric-free synthetic output bytes."""
    metadata = ARTIFACT_METADATA_ROOT / "task018d-experiment-manifest.json"
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary) / "previous-season-attacking-prior-development-v1"
        root.mkdir(parents=True)
        manifest = json.loads(metadata.read_bytes())
        outputs = []
        for original in manifest["outputs"]:
            path = root / original["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            body = f"test-only synthetic output: {original['path']}\n".encode()
            path.write_bytes(body)
            outputs.append(
                {
                    **original,
                    "bytes": len(body),
                    "rows": 0,
                    "sha256": hashlib.sha256(body).hexdigest(),
                }
            )
        manifest["outputs"] = outputs
        manifest_path = root / "experiment_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        yield metadata, manifest_path
