"""Versioned projection-provider boundary for the decision layer."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Protocol

import duckdb


PROJECTION_PROVIDER_VERSION = "projection-provider-v1"
XFP_V01_PROVIDER_ID = "xfp_v01_parquet_v1"
XFP_V01_MODEL_ID = "xfp_v01"
XFP_V01_MODEL_SCOPE = "modeled_components_only"

POSITION_CODES = {
    "Goalkeeper": "GK",
    "Defender": "DEF",
    "Midfielder": "MID",
    "Forward": "FWD",
}


class ProjectionProviderError(Exception):
    """Raised when a projection artifact cannot safely supply decision inputs."""


class ProjectionState(str, Enum):
    """Decision-layer projection states; missingness is never coerced to zero."""

    VALID = "valid_projection"
    VERIFIED_BLANK = "verified_blank"
    INCOMPLETE = "incomplete_projection"
    MISSING = "missing_projection"


@dataclass(frozen=True)
class ProjectionPlayer:
    season: str
    target_gameweek: int
    fpl_player_id: int
    player_name: str
    team_id: int
    team_name: str
    team_short_name: str
    position_id: int
    position: str
    price_units: int
    projection: float | None
    projection_state: ProjectionState
    verified_blank: bool
    availability_status: str | None
    chance_of_playing_next_round: int | None
    source_model_id: str
    model_scope: str
    source_artifact_path: str
    source_artifact_sha256: str
    expected_minutes: float | None = None

    @property
    def eligible(self) -> bool:
        return self.projection_state in {
            ProjectionState.VALID,
            ProjectionState.VERIFIED_BLANK,
        }

    @property
    def price_m(self) -> float:
        return self.price_units / 10


@dataclass(frozen=True)
class ProjectionDataset:
    season: str
    target_gameweek: int
    snapshot_timestamp: str
    provider_id: str
    provider_version: str
    source_model_id: str
    model_scope: str
    source_artifact_path: str
    source_artifact_sha256: str
    players_artifact_path: str
    players_artifact_sha256: str
    players: tuple[ProjectionPlayer, ...]


class ProjectionProvider(Protocol):
    """Interface consumed by ranking and optimization code."""

    provider_id: str
    provider_version: str

    def load(self, *, season: str, target_gameweek: int) -> ProjectionDataset:
        """Load immutable projections for one target gameweek."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _price_units(price_m: object, player_id: int) -> int:
    if price_m is None:
        raise ProjectionProviderError(f"player {player_id} has no price")
    price = Decimal(str(price_m)) * Decimal(10)
    if price != price.to_integral_value() or price < 0:
        raise ProjectionProviderError(
            f"player {player_id} has invalid tenth-million price {price_m!r}"
        )
    return int(price)


def _projection_state(
    *, fixture_count: int, projection: float | None, prediction_complete: bool
) -> ProjectionState:
    if fixture_count < 0:
        raise ProjectionProviderError("fixture_count cannot be negative")
    if fixture_count == 0:
        if projection is None or not math.isclose(projection, 0.0, abs_tol=1e-12):
            raise ProjectionProviderError(
                "a verified blank must have an explicit zero projection"
            )
        return ProjectionState.VERIFIED_BLANK
    if projection is None:
        return ProjectionState.MISSING
    if prediction_complete:
        return ProjectionState.VALID
    return ProjectionState.INCOMPLETE


class XfpV01ParquetProvider:
    """Adapt immutable xFP v0.1 player-GW Parquet to generic projections."""

    provider_id = XFP_V01_PROVIDER_ID
    provider_version = PROJECTION_PROVIDER_VERSION

    def __init__(
        self,
        *,
        projection_artifact: Path | None = None,
        players_artifact: Path | None = None,
        prediction_data_root: Path = Path("data/predictions/fpl"),
        clean_data_root: Path = Path("data/clean/fpl"),
    ) -> None:
        self.projection_artifact = projection_artifact
        self.players_artifact = players_artifact
        self.prediction_data_root = prediction_data_root
        self.clean_data_root = clean_data_root

    def _find_projection(self, season: str, target_gameweek: int) -> Path:
        if self.projection_artifact is not None:
            path = self.projection_artifact
        else:
            candidates = list(
                (self.prediction_data_root / season).glob(
                    f"*/gameweek={target_gameweek}/xfp_v01_gameweek.parquet"
                )
            )
            if not candidates:
                raise ProjectionProviderError(
                    f"no xFP v0.1 projection found for {season} GW{target_gameweek}"
                )
            path = max(candidates, key=lambda candidate: candidate.parents[1].name)
        if not path.is_file():
            raise ProjectionProviderError(f"projection artifact does not exist: {path}")
        return path.resolve()

    def load(self, *, season: str, target_gameweek: int) -> ProjectionDataset:
        projection_path = self._find_projection(season, target_gameweek)
        connection = duckdb.connect()
        try:
            metadata = connection.execute(
                """SELECT season, target_gameweek, snapshot_timestamp, model_version,
                          count(*) AS row_count,
                          count(DISTINCT fpl_player_id) AS player_count
                     FROM read_parquet(?)
                 GROUP BY season, target_gameweek, snapshot_timestamp, model_version""",
                [str(projection_path)],
            ).fetchall()
            if len(metadata) != 1:
                raise ProjectionProviderError(
                    "projection artifact must contain exactly one season/GW/snapshot/model"
                )
            (
                artifact_season,
                artifact_gameweek,
                snapshot_timestamp,
                model_version,
                row_count,
                player_count,
            ) = metadata[0]
            if artifact_season != season or artifact_gameweek != target_gameweek:
                raise ProjectionProviderError(
                    "projection artifact season/target gameweek does not match request"
                )
            if model_version != "v0.1":
                raise ProjectionProviderError(
                    f"expected xFP model version v0.1, found {model_version!r}"
                )
            if row_count != player_count:
                raise ProjectionProviderError("projection player IDs are not unique")

            players_path = self.players_artifact or (
                self.clean_data_root / season / snapshot_timestamp / "players.parquet"
            )
            if not players_path.is_file():
                raise ProjectionProviderError(
                    f"matching clean player artifact does not exist: {players_path}"
                )
            players_path = players_path.resolve()
            player_metadata = connection.execute(
                """SELECT season, snapshot_timestamp, count(*) AS row_count,
                          count(DISTINCT fpl_player_id) AS player_count
                     FROM read_parquet(?)
                 GROUP BY season, snapshot_timestamp""",
                [str(players_path)],
            ).fetchall()
            if len(player_metadata) != 1:
                raise ProjectionProviderError(
                    "clean player artifact must contain one season and snapshot"
                )
            clean_season, clean_snapshot, clean_rows, clean_players = player_metadata[0]
            if clean_season != season or clean_snapshot != snapshot_timestamp:
                raise ProjectionProviderError(
                    "clean player artifact is not from the projection snapshot"
                )
            if clean_rows != clean_players:
                raise ProjectionProviderError("clean player IDs are not unique")
            rows = connection.execute(
                """SELECT p.season, p.target_gameweek, p.fpl_player_id,
                          p.web_name, p.team_id, p.team_name,
                          c.team_short_name, p.position_id, p.position,
                          c.price_m, p.gameweek_xfp_v01, p.fixture_count,
                          p.prediction_complete, p.gameweek_expected_minutes_v01,
                          c.status,
                          c.chance_of_playing_next_round,
                          c.team_id, c.position_id
                     FROM read_parquet(?) AS p
                LEFT JOIN read_parquet(?) AS c USING (fpl_player_id)
                 ORDER BY p.fpl_player_id""",
                [str(projection_path), str(players_path)],
            ).fetchall()
        except duckdb.Error as exc:
            raise ProjectionProviderError(f"could not read projection inputs: {exc}") from exc
        finally:
            connection.close()

        projection_hash = sha256_file(projection_path)
        players_hash = sha256_file(players_path)
        players: list[ProjectionPlayer] = []
        for row in rows:
            (
                row_season,
                row_gameweek,
                player_id,
                player_name,
                team_id,
                team_name,
                team_short_name,
                position_id,
                position_name,
                price_m,
                projection,
                fixture_count,
                prediction_complete,
                expected_minutes,
                availability_status,
                chance,
                clean_team_id,
                clean_position_id,
            ) = row
            if any(
                value is None
                for value in (
                    player_id,
                    player_name,
                    team_id,
                    team_name,
                    team_short_name,
                    position_id,
                    position_name,
                    fixture_count,
                    prediction_complete,
                )
            ):
                raise ProjectionProviderError(
                    f"projection/player join is incomplete for player {player_id!r}"
                )
            if position_name not in POSITION_CODES:
                raise ProjectionProviderError(
                    f"unsupported position {position_name!r} for player {player_id}"
                )
            if team_id != clean_team_id or position_id != clean_position_id:
                raise ProjectionProviderError(
                    f"projection/player team or position mismatch for player {player_id}"
                )
            numeric_projection = (
                float(projection) if projection is not None else None
            )
            if numeric_projection is not None and not math.isfinite(numeric_projection):
                raise ProjectionProviderError(
                    f"player {player_id} has a non-finite projection"
                )
            numeric_expected_minutes = (
                float(expected_minutes) if expected_minutes is not None else None
            )
            if numeric_expected_minutes is not None and not math.isfinite(
                numeric_expected_minutes
            ):
                raise ProjectionProviderError(
                    f"player {player_id} has non-finite expected minutes"
                )
            state = _projection_state(
                fixture_count=int(fixture_count),
                projection=numeric_projection,
                prediction_complete=bool(prediction_complete),
            )
            players.append(
                ProjectionPlayer(
                    season=row_season,
                    target_gameweek=int(row_gameweek),
                    fpl_player_id=int(player_id),
                    player_name=str(player_name),
                    team_id=int(team_id),
                    team_name=str(team_name),
                    team_short_name=str(team_short_name),
                    position_id=int(position_id),
                    position=POSITION_CODES[str(position_name)],
                    price_units=_price_units(price_m, int(player_id)),
                    projection=numeric_projection,
                    projection_state=state,
                    verified_blank=state is ProjectionState.VERIFIED_BLANK,
                    availability_status=(
                        str(availability_status)
                        if availability_status is not None
                        else None
                    ),
                    chance_of_playing_next_round=(
                        int(chance) if chance is not None else None
                    ),
                    source_model_id=XFP_V01_MODEL_ID,
                    model_scope=XFP_V01_MODEL_SCOPE,
                    source_artifact_path=str(projection_path),
                    source_artifact_sha256=projection_hash,
                    expected_minutes=numeric_expected_minutes,
                )
            )

        return ProjectionDataset(
            season=season,
            target_gameweek=target_gameweek,
            snapshot_timestamp=str(snapshot_timestamp),
            provider_id=self.provider_id,
            provider_version=self.provider_version,
            source_model_id=XFP_V01_MODEL_ID,
            model_scope=XFP_V01_MODEL_SCOPE,
            source_artifact_path=str(projection_path),
            source_artifact_sha256=projection_hash,
            players_artifact_path=str(players_path),
            players_artifact_sha256=players_hash,
            players=tuple(players),
        )
