"""Read-only ingestion of public FPL manager state at a locked deadline."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError

from .tls import network_error_reason, verified_urlopen


FPL_PUBLIC_BASE_URL = "https://fantasy.premierleague.com/api"
FPL_BOOTSTRAP_URL = f"{FPL_PUBLIC_BASE_URL}/bootstrap-static/"
FPL_ENTRY_URL = f"{FPL_PUBLIC_BASE_URL}/entry/{{entry_id}}/"
FPL_ENTRY_HISTORY_URL = f"{FPL_PUBLIC_BASE_URL}/entry/{{entry_id}}/history/"
FPL_ENTRY_TRANSFERS_URL = f"{FPL_PUBLIC_BASE_URL}/entry/{{entry_id}}/transfers/"
FPL_EVENT_PICKS_URL = (
    f"{FPL_PUBLIC_BASE_URL}/entry/{{entry_id}}/event/{{event_id}}/picks/"
)

MANAGER_STATE_VERSION = "public-manager-state-v1"
MANAGER_STATE_SEMANTICS = "manager_state_as_of_event_deadline"
TRANSFER_RECOMMENDATION_STATUS = "not_available_in_public_manager_state_v1"
FRESHNESS_WARNING = "PUBLIC MANAGER STATE IS LOCKED AS OF THE REPRESENTED DEADLINE"
POST_DEADLINE_WARNING = "Transfers made after that deadline may not be represented."
DEFAULT_TIMEOUT_SECONDS = 30

POSITION_CODES = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
SQUAD_POSITION_COUNTS = {"GK": 2, "DEF": 5, "MID": 5, "FWD": 3}


class ManagerStateError(Exception):
    """Raised when public manager data cannot be retrieved or validated safely."""


class ManagerHTTPStatusError(ManagerStateError):
    def __init__(self, endpoint: str, status: int) -> None:
        self.endpoint = endpoint
        self.status = status
        super().__init__(f"{endpoint} returned HTTP status {status}")


class ManagerStateOutputExistsError(ManagerStateError):
    """Raised rather than overwriting a public manager-state artifact."""


class HTTPResponse(Protocol):
    status: int

    def read(self) -> bytes: ...

    def __enter__(self) -> "HTTPResponse": ...

    def __exit__(self, *args: object) -> None: ...


Opener = Callable[..., HTTPResponse]


@dataclass(frozen=True)
class ManagerPick:
    element_id: int
    pick_position: int
    multiplier: int
    is_captain: bool
    is_vice_captain: bool
    team_id: int
    position: str


@dataclass(frozen=True)
class ManagerSource:
    name: str
    endpoint: str
    method: str
    raw_path: str
    sha256: str


@dataclass(frozen=True)
class PublicManagerState:
    version: str
    season: str
    entry_id: int
    represented_event: int
    deadline_time: str
    retrieval_timestamp: str
    state_semantics: str
    freshness_warning: str
    post_deadline_warning: str
    picks: tuple[ManagerPick, ...]
    manager_xi: tuple[int, ...]
    manager_bench: tuple[int, ...]
    manager_captain: int
    manager_vice_captain: int
    event_bank_units: int | None
    event_team_value_units: int | None
    active_chip: str | None
    chip_history: tuple[dict[str, Any], ...]
    transfer_history: tuple[dict[str, Any], ...]
    field_classification: dict[str, str]
    unavailable_public_fields: tuple[str, ...]
    transfer_recommendation_status: str
    sources: tuple[ManagerSource, ...]
    raw_directory: Path
    manifest_path: Path
    manifest_sha256: str


def _timestamp(now: datetime) -> tuple[str, str]:
    if now.tzinfo is None:
        raise ValueError("manager retrieval timestamp must be timezone-aware")
    utc = now.astimezone(timezone.utc)
    return (
        utc.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        utc.strftime("%Y%m%dT%H%M%S.%fZ"),
    )


def _parse_deadline(value: object) -> datetime:
    if not isinstance(value, str):
        raise ManagerStateError("event deadline_time must be an ISO UTC string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ManagerStateError(f"invalid event deadline_time: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ManagerStateError("event deadline_time must include a timezone")
    return parsed.astimezone(timezone.utc)


def _request_json(endpoint: str, *, opener: Opener, timeout: float) -> tuple[bytes, Any]:
    if not endpoint.startswith(f"{FPL_PUBLIC_BASE_URL}/"):
        raise ManagerStateError(f"refusing non-official public endpoint: {endpoint}")
    try:
        # Passing a URL string with no request body or headers is a public GET.
        with opener(endpoint, timeout=timeout) as response:
            if not 200 <= response.status < 300:
                raise ManagerHTTPStatusError(endpoint, response.status)
            body = response.read()
    except ManagerHTTPStatusError:
        raise
    except HTTPError as exc:
        raise ManagerHTTPStatusError(endpoint, exc.code) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ManagerStateError(
            f"could not reach {endpoint}: {network_error_reason(exc)}"
        ) from exc
    try:
        return body, json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ManagerStateError(f"{endpoint} returned invalid JSON") from exc


def _validate_entry_id(entry_id: int) -> int:
    if isinstance(entry_id, bool) or not isinstance(entry_id, int) or entry_id <= 0:
        raise ManagerStateError("entry_id must be a positive integer")
    return entry_id


def _validate_bootstrap(payload: Any) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise ManagerStateError("bootstrap-static response is not an object")
    elements = payload.get("elements")
    events = payload.get("events")
    if not isinstance(elements, list) or not isinstance(events, list):
        raise ManagerStateError("bootstrap-static response lacks elements/events lists")
    player_map: dict[int, dict[str, Any]] = {}
    for row in elements:
        if not isinstance(row, dict):
            raise ManagerStateError("bootstrap player is not an object")
        element_id = row.get("id")
        team_id = row.get("team")
        position_id = row.get("element_type")
        if (
            not isinstance(element_id, int)
            or isinstance(element_id, bool)
            or not isinstance(team_id, int)
            or isinstance(team_id, bool)
            or position_id not in POSITION_CODES
        ):
            raise ManagerStateError("bootstrap player identity/team/position is invalid")
        if element_id in player_map:
            raise ManagerStateError("bootstrap-static contains duplicate player IDs")
        player_map[element_id] = row
    event_ids: set[int] = set()
    for event in events:
        if not isinstance(event, dict):
            raise ManagerStateError("bootstrap event is not an object")
        event_id = event.get("id")
        if not isinstance(event_id, int) or isinstance(event_id, bool):
            raise ManagerStateError("bootstrap event has an invalid ID")
        _parse_deadline(event.get("deadline_time"))
        if event_id in event_ids:
            raise ManagerStateError("bootstrap-static contains duplicate event IDs")
        event_ids.add(event_id)
    return player_map, events


def _validate_entry(payload: Any, entry_id: int) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("id") != entry_id:
        raise ManagerStateError("entry summary does not match the requested entry ID")
    return payload


def _validate_history(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or any(
        not isinstance(payload.get(field), list) for field in ("current", "past", "chips")
    ):
        raise ManagerStateError("entry history response has an unexpected structure")
    if any(not isinstance(row, dict) for row in payload["chips"]):
        raise ManagerStateError("entry chip history contains a non-object row")
    return payload


def _validate_transfers(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
        raise ManagerStateError("entry transfers response is not a list of objects")
    return payload


def _validate_picks(
    payload: Any,
    *,
    represented_event: int,
    player_map: dict[int, dict[str, Any]],
) -> tuple[tuple[ManagerPick, ...], dict[str, Any], str | None]:
    if not isinstance(payload, dict):
        raise ManagerStateError("event picks response is not an object")
    raw_picks = payload.get("picks")
    entry_history = payload.get("entry_history")
    if not isinstance(raw_picks, list) or not isinstance(entry_history, dict):
        raise ManagerStateError("event picks response lacks picks/entry_history")
    if entry_history.get("event") not in (None, represented_event):
        raise ManagerStateError("event picks entry_history identifies another event")
    if len(raw_picks) != 15:
        raise ManagerStateError(f"event picks must contain exactly 15 rows, found {len(raw_picks)}")
    picks: list[ManagerPick] = []
    for raw in raw_picks:
        if not isinstance(raw, dict):
            raise ManagerStateError("event pick is not an object")
        element_id = raw.get("element")
        pick_position = raw.get("position")
        multiplier = raw.get("multiplier")
        is_captain = raw.get("is_captain")
        is_vice = raw.get("is_vice_captain")
        if (
            not isinstance(element_id, int)
            or isinstance(element_id, bool)
            or not isinstance(pick_position, int)
            or isinstance(pick_position, bool)
            or not isinstance(multiplier, int)
            or isinstance(multiplier, bool)
            or not isinstance(is_captain, bool)
            or not isinstance(is_vice, bool)
        ):
            raise ManagerStateError("event pick has invalid identity/selection fields")
        bootstrap_player = player_map.get(element_id)
        if bootstrap_player is None:
            raise ManagerStateError(
                f"owned player {element_id} does not resolve in bootstrap-static"
            )
        picks.append(
            ManagerPick(
                element_id=element_id,
                pick_position=pick_position,
                multiplier=multiplier,
                is_captain=is_captain,
                is_vice_captain=is_vice,
                team_id=int(bootstrap_player["team"]),
                position=POSITION_CODES[int(bootstrap_player["element_type"])],
            )
        )
    element_ids = [row.element_id for row in picks]
    if len(element_ids) != len(set(element_ids)):
        raise ManagerStateError("event picks contain duplicate player IDs")
    positions = sorted(row.pick_position for row in picks)
    if positions != list(range(1, 16)):
        raise ManagerStateError("pick positions must contain each value 1 through 15")
    composition = Counter(row.position for row in picks)
    if dict(composition) != SQUAD_POSITION_COUNTS:
        raise ManagerStateError(
            f"owned squad positions must be {SQUAD_POSITION_COUNTS}, found {dict(composition)}"
        )
    club_counts = Counter(row.team_id for row in picks)
    if max(club_counts.values()) > 3:
        raise ManagerStateError("owned squad exceeds the three-per-club limit")
    captains = [row for row in picks if row.is_captain]
    vice_captains = [row for row in picks if row.is_vice_captain]
    if len(captains) != 1 or len(vice_captains) != 1:
        raise ManagerStateError("event picks require exactly one captain and vice-captain")
    if captains[0].element_id == vice_captains[0].element_id:
        raise ManagerStateError("captain and vice-captain must be distinct")
    if captains[0].pick_position > 11 or vice_captains[0].pick_position > 11:
        raise ManagerStateError("captain and vice-captain must be in the locked XI")
    xi_counts = Counter(row.position for row in picks if row.pick_position <= 11)
    if not (
        xi_counts["GK"] == 1
        and xi_counts["DEF"] >= 3
        and xi_counts["MID"] >= 2
        and xi_counts["FWD"] >= 1
    ):
        raise ManagerStateError("locked positions 1-11 do not form a legal FPL XI")
    active_chip = payload.get("active_chip")
    if active_chip is not None and not isinstance(active_chip, str):
        raise ManagerStateError("active_chip must be a string or null")
    return tuple(sorted(picks, key=lambda row: row.pick_position)), entry_history, active_chip


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _write_manager_state(
    *,
    raw_data_root: Path,
    season: str,
    entry_id: int,
    filesystem_timestamp: str,
    bodies: dict[str, tuple[str, bytes]],
    manifest: dict[str, Any],
) -> tuple[Path, Path, tuple[ManagerSource, ...]]:
    final_directory = raw_data_root / season / f"entry={entry_id}" / filesystem_timestamp
    if final_directory.exists():
        raise ManagerStateOutputExistsError(
            f"manager-state output already exists: {final_directory}"
        )
    final_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = final_directory.parent / f".{filesystem_timestamp}.{uuid.uuid4().hex}.tmp"
    temporary.mkdir()
    sources: list[ManagerSource] = []
    try:
        for name, (endpoint, body) in bodies.items():
            filename = f"{name}.json"
            path = temporary / filename
            with path.open("xb") as output:
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            sources.append(
                ManagerSource(
                    name=name,
                    endpoint=endpoint,
                    method="GET",
                    raw_path=filename,
                    sha256=_sha256(body),
                )
            )
        manifest["sources"] = [asdict(source) for source in sources]
        manifest_path = temporary / "manager_state_manifest.json"
        with manifest_path.open("x", encoding="utf-8") as output:
            json.dump(manifest, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary.rename(final_directory)
    except Exception:
        for path in temporary.glob("*"):
            path.unlink()
        if temporary.exists():
            temporary.rmdir()
        raise
    return (
        final_directory,
        final_directory / "manager_state_manifest.json",
        tuple(sources),
    )


class PublicFPLManagerStateProvider:
    """Retrieve public entry state using only official unauthenticated GETs."""

    def __init__(
        self,
        *,
        raw_data_root: Path = Path("data/manager/raw/fpl"),
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        opener: Opener = verified_urlopen,
    ) -> None:
        self.raw_data_root = raw_data_root
        self.timeout = timeout
        self.opener = opener

    def fetch(
        self,
        *,
        entry_id: int,
        season: str,
        represented_event: int | None = None,
        now: datetime | None = None,
    ) -> PublicManagerState:
        entry_id = _validate_entry_id(entry_id)
        retrieval = now or datetime.now(timezone.utc)
        retrieval_iso, filesystem_timestamp = _timestamp(retrieval)

        bootstrap_body, bootstrap = _request_json(
            FPL_BOOTSTRAP_URL, opener=self.opener, timeout=self.timeout
        )
        player_map, events = _validate_bootstrap(bootstrap)
        entry_endpoint = FPL_ENTRY_URL.format(entry_id=entry_id)
        history_endpoint = FPL_ENTRY_HISTORY_URL.format(entry_id=entry_id)
        transfers_endpoint = FPL_ENTRY_TRANSFERS_URL.format(entry_id=entry_id)
        entry_body, entry = _request_json(
            entry_endpoint, opener=self.opener, timeout=self.timeout
        )
        history_body, history = _request_json(
            history_endpoint, opener=self.opener, timeout=self.timeout
        )
        transfers_body, transfers = _request_json(
            transfers_endpoint, opener=self.opener, timeout=self.timeout
        )
        _validate_entry(entry, entry_id)
        history = _validate_history(history)
        transfers = _validate_transfers(transfers)

        event_map = {int(event["id"]): event for event in events}
        if represented_event is not None:
            if represented_event not in event_map:
                raise ManagerStateError(
                    f"represented event {represented_event} is absent from bootstrap-static"
                )
            candidates = [event_map[represented_event]]
        else:
            candidates = sorted(
                (
                    event
                    for event in events
                    if _parse_deadline(event["deadline_time"]) <= retrieval
                ),
                key=lambda event: _parse_deadline(event["deadline_time"]),
                reverse=True,
            )
        if not candidates:
            raise ManagerStateError("no locked event deadline exists at retrieval time")

        selected: tuple[dict[str, Any], str, bytes, Any] | None = None
        for event in candidates:
            deadline = _parse_deadline(event["deadline_time"])
            if deadline > retrieval:
                raise ManagerStateError(
                    f"event {event['id']} deadline is after the retrieval timestamp"
                )
            picks_endpoint = FPL_EVENT_PICKS_URL.format(
                entry_id=entry_id, event_id=event["id"]
            )
            try:
                picks_body, picks_payload = _request_json(
                    picks_endpoint, opener=self.opener, timeout=self.timeout
                )
            except ManagerHTTPStatusError as exc:
                if represented_event is None and exc.status == 404:
                    continue
                raise
            selected = (event, picks_endpoint, picks_body, picks_payload)
            break
        if selected is None:
            raise ManagerStateError("no public locked picks were available for past deadlines")

        event, picks_endpoint, picks_body, picks_payload = selected
        event_id = int(event["id"])
        picks, entry_history, active_chip = _validate_picks(
            picks_payload, represented_event=event_id, player_map=player_map
        )
        manager_xi = tuple(row.element_id for row in picks if row.pick_position <= 11)
        manager_bench = tuple(row.element_id for row in picks if row.pick_position >= 12)
        manager_captain = next(row.element_id for row in picks if row.is_captain)
        manager_vice = next(row.element_id for row in picks if row.is_vice_captain)
        bank = entry_history.get("bank")
        value = entry_history.get("value")
        if bank is not None and (not isinstance(bank, int) or isinstance(bank, bool)):
            raise ManagerStateError("public event bank must be an integer or null")
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            raise ManagerStateError("public event team value must be an integer or null")

        classifications = {
            "entry_summary": "current_live_public",
            "manager_picks": "as_of_last_deadline_public",
            "locked_xi_and_bench": "as_of_last_deadline_public",
            "captain_and_vice": "as_of_last_deadline_public",
            "event_bank_and_team_value": "as_of_last_deadline_public",
            "transfer_history": "historical_public",
            "chip_history": "historical_public",
        }
        unavailable = (
            "current_editable_squad",
            "current_player_purchase_prices",
            "current_player_selling_prices",
            "free_transfer_count",
            "live_post_deadline_squad_changes",
        )
        bodies = {
            "bootstrap-static": (FPL_BOOTSTRAP_URL, bootstrap_body),
            "entry": (entry_endpoint, entry_body),
            "entry-history": (history_endpoint, history_body),
            "entry-transfers": (transfers_endpoint, transfers_body),
            f"event-{event_id}-picks": (picks_endpoint, picks_body),
        }
        manifest: dict[str, Any] = {
            "version": MANAGER_STATE_VERSION,
            "season": season,
            "entry_id": entry_id,
            "represented_event": event_id,
            "deadline_time": event["deadline_time"],
            "retrieval_timestamp": retrieval_iso,
            "state_semantics": MANAGER_STATE_SEMANTICS,
            "freshness_warning": FRESHNESS_WARNING,
            "post_deadline_warning": POST_DEADLINE_WARNING,
            "picks": [asdict(row) for row in picks],
            "manager_xi": list(manager_xi),
            "manager_bench": list(manager_bench),
            "manager_captain": manager_captain,
            "manager_vice_captain": manager_vice,
            "event_bank_units": bank,
            "event_team_value_units": value,
            "active_chip": active_chip,
            "chip_history": history["chips"],
            "transfer_history": transfers,
            "field_classification": classifications,
            "unavailable_public_fields": list(unavailable),
            "transfer_recommendation_status": TRANSFER_RECOMMENDATION_STATUS,
        }
        raw_directory, manifest_path, sources = _write_manager_state(
            raw_data_root=self.raw_data_root,
            season=season,
            entry_id=entry_id,
            filesystem_timestamp=filesystem_timestamp,
            bodies=bodies,
            manifest=manifest,
        )
        manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        return PublicManagerState(
            version=MANAGER_STATE_VERSION,
            season=season,
            entry_id=entry_id,
            represented_event=event_id,
            deadline_time=str(event["deadline_time"]),
            retrieval_timestamp=retrieval_iso,
            state_semantics=MANAGER_STATE_SEMANTICS,
            freshness_warning=FRESHNESS_WARNING,
            post_deadline_warning=POST_DEADLINE_WARNING,
            picks=picks,
            manager_xi=manager_xi,
            manager_bench=manager_bench,
            manager_captain=manager_captain,
            manager_vice_captain=manager_vice,
            event_bank_units=bank,
            event_team_value_units=value,
            active_chip=active_chip,
            chip_history=tuple(history["chips"]),
            transfer_history=tuple(transfers),
            field_classification=classifications,
            unavailable_public_fields=unavailable,
            transfer_recommendation_status=TRANSFER_RECOMMENDATION_STATUS,
            sources=sources,
            raw_directory=raw_directory,
            manifest_path=manifest_path,
            manifest_sha256=manifest_hash,
        )
