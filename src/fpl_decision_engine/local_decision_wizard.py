"""Guided local adapter over the verified manager-evidence workflow.

The wizard owns prompts and presentation only.  Task031B remains authoritative
for draft validation, evidence publication and operational execution, while the
trusted artifact reader remains authoritative for anything shown as a result.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Protocol

from .manager_evidence_authoring import (
    DEFAULT_EVIDENCE_ROOT,
    POSITION_COUNTS,
    AuthoringPreparation,
    CataloguePlayer,
    ManagerEvidenceAuthoringError,
    ManagerEvidenceDraft,
    PublishedManagerEvidence,
    load_draft,
    load_preparation_for_authoring,
    parse_draft_pick,
    publish_verified_evidence,
    run_existing_resume,
    save_draft,
    validate_draft,
)
from .operational_manifest import ChipState
from .operational_runner import CompletedRunResult, OperationalRunnerError
from .trusted_artifact_reader import (
    TrustedArtifactValidationError,
    VerifiedGameweekDecision,
    load_verified_gameweek_decision,
)


DEFAULT_DRAFT_ROOT = Path("data/manager/drafts/fpl")
DEFAULT_EVIDENCE_SOURCE = "Official Transfers screen, manually verified"
PUBLISH_PHRASE = "PUBLISH VERIFIED EVIDENCE"
RUN_PHRASE = "RUN ENGINE"
CURRENT_SELECTION_PHRASE = "CONFIRM CURRENT SELECTION"
PAGE_SIZE = 8


class PromptIO(Protocol):
    """Minimal replaceable interaction surface used by the wizard."""

    def show_public(self, message: str) -> None: ...

    def show_private(self, message: str) -> None: ...

    def ask_text(self, field: str, prompt: str) -> str: ...

    def ask_choice(self, field: str, choices: Sequence[str]) -> str: ...

    def confirm(self, field: str, phrase: str) -> bool: ...


class TerminalPromptIO:
    """Standard local terminal implementation of :class:`PromptIO`."""

    def show_public(self, message: str) -> None:
        print(message)

    def show_private(self, message: str) -> None:
        print(message)

    def ask_text(self, field: str, prompt: str) -> str:
        del field
        return input(f"{prompt}: ").strip()

    def ask_choice(self, field: str, choices: Sequence[str]) -> str:
        if not choices:
            raise ValueError("choices must not be empty")
        self.show_public(f"{field} choices:")
        for index, choice in enumerate(choices, start=1):
            self.show_public(f"  {index}. {choice}")
        while True:
            answer = input("Choose a number: ").strip()
            if answer.isascii() and answer.isdigit():
                index = int(answer)
                if 1 <= index <= len(choices):
                    return choices[index - 1]
            self.show_public("Enter one of the displayed choice numbers.")

    def confirm(self, field: str, phrase: str) -> bool:
        answer = self.ask_text(field, f"Type exactly: {phrase}")
        return answer == phrase


class WizardState(str, Enum):
    LOADING_PREPARATION = "LOADING_PREPARATION"
    DRAFT_NEW = "DRAFT_NEW"
    DRAFT_RESUMED = "DRAFT_RESUMED"
    UNSUPPORTED_CHIP = "UNSUPPORTED_CHIP"
    UNSUPPORTED_TRANSFER_STATE = "UNSUPPORTED_TRANSFER_STATE"
    VERIFIED_EVIDENCE_PUBLISHED = "VERIFIED_EVIDENCE_PUBLISHED"
    VERIFIED_DECISION_AVAILABLE = "VERIFIED_DECISION_AVAILABLE"
    CANCELLED = "CANCELLED"
    DEADLINE_REACHED = "DEADLINE_REACHED"
    TRUST_CHAIN_INVALID = "TRUST_CHAIN_INVALID"
    IMMUTABLE_CONFLICT = "IMMUTABLE_CONFLICT"
    STORAGE_FAILURE = "STORAGE_FAILURE"


@dataclass(frozen=True)
class WizardResult:
    state: WizardState
    preparation_id: str | None = None
    evidence_path: Path | None = None
    final_manifest_path: Path | None = None


@dataclass(frozen=True)
class WizardPorts:
    load_preparation: Callable[[Path], AuthoringPreparation] = (
        load_preparation_for_authoring
    )
    load_saved_draft: Callable[[Path], ManagerEvidenceDraft] = load_draft
    save_saved_draft: Callable[[ManagerEvidenceDraft, Path], Path] = save_draft
    validate_saved_draft: Callable[
        [ManagerEvidenceDraft, AuthoringPreparation], tuple
    ] = validate_draft
    publish: Callable[..., PublishedManagerEvidence] = publish_verified_evidence
    run: Callable[..., CompletedRunResult] = run_existing_resume
    load_verified_result: Callable[[Path], VerifiedGameweekDecision] = (
        load_verified_gameweek_decision
    )


def _system_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_draft_path(
    preparation: AuthoringPreparation, *, draft_root: Path = DEFAULT_DRAFT_ROOT
) -> Path:
    """Derive the ignored mutable draft path from public preparation identity."""
    return draft_root / preparation.manifest.preparation_id / "current.json"


def search_catalogue(
    catalogue: Sequence[CataloguePlayer],
    query: str,
    *,
    position: str | None = None,
    excluded_ids: frozenset[int] = frozenset(),
    limit: int = PAGE_SIZE,
    offset: int = 0,
) -> tuple[CataloguePlayer, ...]:
    """Return a bounded deterministic public-identity search result."""
    term = query.strip().casefold()
    exact_id = int(term) if term.isascii() and term.isdigit() else None
    matches = [
        row
        for row in catalogue
        if row.element_id not in excluded_ids
        and (position is None or row.position == position)
        and (
            row.element_id == exact_id
            or term in row.display_name.casefold()
            or term in row.team_name.casefold()
        )
    ]
    matches.sort(
        key=lambda row: (
            0 if exact_id is not None and row.element_id == exact_id else 1,
            row.display_name.casefold(),
            row.team_name.casefold(),
            row.element_id,
        )
    )
    start = max(0, offset)
    return tuple(matches[start : start + max(0, limit)])


def _new_draft(preparation: AuthoringPreparation) -> ManagerEvidenceDraft:
    return ManagerEvidenceDraft(
        preparation_manifest_sha256=preparation.manifest.sha256,
        entry_id=None,
        selected_element_ids=[],
        bank_m=None,
        free_transfers=None,
        chip_state=None,
        selling_price_m_by_element_id={},
        evidence_source=DEFAULT_EVIDENCE_SOURCE,
        evidence_source_sha256=None,
        current_selection_confirmed=False,
    )


def _before_deadline(preparation: AuthoringPreparation, now: datetime) -> bool:
    if now.tzinfo is None or now.utcoffset() != timezone.utc.utcoffset(now):
        return False
    deadline = datetime.fromisoformat(
        preparation.manifest.official_deadline.replace("Z", "+00:00")
    )
    return now < deadline


def _catalogue_label(row: CataloguePlayer) -> str:
    return (
        f"{row.element_id} | {row.display_name} | {row.position} | "
        f"{row.team_name} | £{row.market_price_m}m"
    )


def _save_or_stop(
    draft: ManagerEvidenceDraft,
    path: Path,
    ports: WizardPorts,
    io: PromptIO,
) -> bool:
    try:
        ports.save_saved_draft(draft, path)
        return True
    except ManagerEvidenceAuthoringError:
        io.show_public("STORAGE_FAILURE: private draft could not be saved.")
        return False


def _ask_integer(io: PromptIO, field: str, prompt: str, *, positive: bool) -> int:
    while True:
        raw = io.ask_text(field, prompt)
        try:
            value = int(raw)
        except ValueError:
            value = -1
        if not isinstance(value, bool) and (value > 0 if positive else value >= 0):
            return value
        io.show_public(f"{field}: enter a {'positive' if positive else 'non-negative'} whole number.")


def _ask_money(io: PromptIO, field: str, prompt: str) -> str:
    while True:
        raw = io.ask_text(field, prompt)
        try:
            return parse_draft_pick(f"1:{raw}")[1]
        except ManagerEvidenceAuthoringError:
            io.show_public(f"{field}: enter money to one decimal place, such as 4.5.")


def _select_squad(
    io: PromptIO,
    preparation: AuthoringPreparation,
    *,
    on_pick: Callable[[Sequence[int], dict[int, str]], None],
) -> tuple[list[int], dict[int, str]]:
    selected: list[int] = []
    prices: dict[int, str] = {}
    by_id = {row.element_id: row for row in preparation.catalogue}
    for position, count in POSITION_COUNTS.items():
        for slot in range(1, count + 1):
            while True:
                query = io.ask_text(
                    f"{position}_{slot}",
                    f"Search for {position} {slot}/{count} by name, club or element ID",
                )
                club_counts: dict[int, int] = {}
                for element_id in selected:
                    team_id = by_id[element_id].team_id
                    club_counts[team_id] = club_counts.get(team_id, 0) + 1
                full_club_ids = {
                    row.element_id
                    for row in preparation.catalogue
                    if club_counts.get(row.team_id, 0) >= 3
                }
                offset = 0
                while True:
                    page = search_catalogue(
                        preparation.catalogue,
                        query,
                        position=position,
                        excluded_ids=frozenset(selected) | frozenset(full_club_ids),
                        limit=PAGE_SIZE + 1,
                        offset=offset,
                    )
                    matches = page[:PAGE_SIZE]
                    if not matches:
                        io.show_public("No matching unselected player. Search again.")
                        break
                    labels = tuple(_catalogue_label(row) for row in matches)
                    choices = labels + (("Show more results",) if len(page) > PAGE_SIZE else ())
                    chosen_label = io.ask_choice(f"{position}_{slot}", choices)
                    if chosen_label == "Show more results":
                        offset += PAGE_SIZE
                        continue
                    chosen = matches[labels.index(chosen_label)]
                    break
                if not matches:
                    continue
                selected.append(chosen.element_id)
                prices[chosen.element_id] = _ask_money(
                    io,
                    f"selling_price_{chosen.element_id}",
                    (
                        f"Exact selling price for {chosen.display_name}; frozen market "
                        f"price is £{chosen.market_price_m}m (no default)"
                    ),
                )
                on_pick(tuple(selected), dict(prices))
                break
    return selected, prices


def _private_review(
    draft: ManagerEvidenceDraft,
    preparation: AuthoringPreparation,
) -> str:
    by_id = {row.element_id: row for row in preparation.catalogue}
    lines = [
        "PRIVATE REVIEW — do not share this terminal output",
        f"Gameweek: {preparation.manifest.target_gameweek}",
        f"Preparation: {preparation.manifest.preparation_id}",
        f"Observed: {preparation.observed_at}",
        f"Deadline: {preparation.manifest.official_deadline}",
        f"Entry ID: {draft.entry_id}",
    ]
    prices = {int(key): value for key, value in draft.selling_price_m_by_element_id.items()}
    for position in POSITION_COUNTS:
        lines.append(position)
        for element_id in draft.selected_element_ids:
            row = by_id[element_id]
            if row.position == position:
                lines.append(
                    f"  {row.display_name} ({row.team_name}) selling £{prices[element_id]}m; "
                    f"frozen market £{row.market_price_m}m"
                )
    lines.extend(
        [
            f"Bank: £{draft.bank_m}m",
            f"Free transfers: {draft.free_transfers}",
            f"Chip: {draft.chip_state}",
            f"Evidence source: {draft.evidence_source}",
            "Source hash present: " + ("yes" if draft.evidence_source_sha256 else "no"),
            "Model scope: appearance, goals and assists only.",
            "Engine v1: ROLL or one free transfer; no hits/chips/future carry value.",
            "Publication creates immutable evidence; it does not execute an FPL action or journal a decision.",
        ]
    )
    return "\n".join(lines)


def _ask_optional_sha256(io: PromptIO) -> str | None:
    while True:
        value = io.ask_text(
            "evidence_source_sha256",
            "Optional lowercase source-file SHA-256 (Enter skips)",
        )
        if not value:
            return None
        if len(value) == 64 and all(character in "0123456789abcdef" for character in value):
            return value
        io.show_public(
            "evidence_source_sha256: enter 64 lowercase hexadecimal characters or leave blank."
        )


def _has_complete_saved_selection(draft: ManagerEvidenceDraft) -> bool:
    if len(draft.selected_element_ids) != 15:
        return False
    try:
        selected = {int(item) for item in draft.selected_element_ids}
        priced = {int(item) for item in draft.selling_price_m_by_element_id}
    except (TypeError, ValueError):
        return False
    return len(selected) == 15 and selected == priced


def format_verified_result(
    verified: VerifiedGameweekDecision, *, observed_at: str
) -> str:
    """Format only fields from the independently verified decision payload."""
    payload = verified.payload()
    action = payload["recommended_action"]
    selection = action["selection"]
    by_id = {row["element_id"]: row["name"] for row in payload["players"]}
    lines = [
        "VERIFIED ENGINE DECISION",
        f"Action: {action['action_type']}",
    ]
    if action["action_type"] == "TRANSFER":
        lines.extend(
            [
                f"Transfer out: {action['outgoing']['name']}",
                f"Transfer in: {action['incoming']['name']}",
                f"Resulting bank: £{action['resulting_bank_units'] / 10:.1f}m",
            ]
        )
    else:
        lines.append(
            f"Resulting bank: £{payload['manager_state']['bank_units'] / 10:.1f}m"
        )
    lines.extend(
        [
            f"Modeled gain versus ROLL: {action['objective_gain_vs_roll_xfp']:.3f} xFP",
            "Starting XI: " + ", ".join(by_id[item] for item in selection["starting_xi"]),
            f"Captain: {by_id[selection['captain']]}",
            f"Vice-captain: {by_id[selection['vice_captain']]}",
            "Bench: " + ", ".join(by_id[item] for item in selection["bench"]),
            "Reliability: diagnostic only; official recommendation unchanged: "
            + str(payload["reliability"]["official_recommendation_unchanged"]).lower(),
        ]
    )
    for warning in payload["reliability"]["warnings"]:
        lines.append(f"Warning: {warning['code']} — {warning['message']}")
    lines.extend(
        [
            f"Official data observed: {observed_at}",
            f"Deadline: {verified.official_deadline}",
            f"Decision: {verified.decision_id}",
            f"Preparation: {verified.preparation_id}",
            "Trust state: VERIFIED_DECISION_AVAILABLE",
            "Model scope: appearance, goals and assists only.",
            "Engine v1: ROLL or one free transfer; future free-transfer value is ignored.",
            "Any FPL action is manual. A prospective journal entry is a separate step.",
        ]
    )
    return "\n".join(lines)


def run_guided_manager_decision(
    preparation_manifest: Path,
    *,
    io: PromptIO,
    draft_path: Path | None = None,
    draft_root: Path = DEFAULT_DRAFT_ROOT,
    evidence_root: Path = DEFAULT_EVIDENCE_ROOT,
    clock: Callable[[], datetime] = _system_utc_now,
    ports: WizardPorts = WizardPorts(),
) -> WizardResult:
    """Run one guided local owner session without acquiring decision authority."""
    preparation: AuthoringPreparation | None = None
    draft: ManagerEvidenceDraft | None = None
    published: PublishedManagerEvidence | None = None
    path: Path | None = None
    try:
        preparation = ports.load_preparation(preparation_manifest)
        started_at = clock()
        if not _before_deadline(preparation, started_at):
            io.show_public("DEADLINE_REACHED: the exact preparation can no longer be used.")
            return WizardResult(WizardState.DEADLINE_REACHED, preparation.manifest.preparation_id)

        io.show_public(
            "Guided local manager decision. Prefer this command for normal owner use; "
            "the older publish-manager-evidence flags may persist private values in "
            "shell history and process lists."
        )
        io.show_public(
            "Private answers can be visible in this terminal. Recording, screen sharing, "
            "copied transcripts, scrollback, and tmux/screen logging may retain them."
        )
        deadline = datetime.fromisoformat(
            preparation.manifest.official_deadline.replace("Z", "+00:00")
        )
        remaining_minutes = int((deadline - started_at).total_seconds() // 60)
        io.show_public(
            f"Preparation {preparation.manifest.preparation_id}; season {preparation.season}; "
            f"GW{preparation.manifest.target_gameweek}; observed {preparation.observed_at}; "
            f"deadline {preparation.manifest.official_deadline} UTC; local deadline "
            f"{deadline.astimezone().isoformat()}; {remaining_minutes} minutes remain."
        )
        io.show_public(
            "Model scope: appearance, goals and assists only. Engine v1 supports ROLL "
            "or one free transfer, no hits or chips, and ignores future carry value."
        )

        path = draft_path or default_draft_path(preparation, draft_root=draft_root)
        io.show_public(f"Mutable private draft: {path}")
        if path.exists():
            draft = ports.load_saved_draft(path)
            if draft.preparation_manifest_sha256 != preparation.manifest.sha256:
                io.show_public("TRUST_CHAIN_INVALID: draft belongs to another preparation.")
                return WizardResult(WizardState.TRUST_CHAIN_INVALID, preparation.manifest.preparation_id)
            io.show_public("DRAFT_RESUMED: values remain untrusted until publication.")
            io.show_public(
                "Saved field status: squad "
                + ("complete" if _has_complete_saved_selection(draft) else "incomplete")
                + "; manager facts will be reconfirmed."
            )
        else:
            draft = _new_draft(preparation)
            if not _save_or_stop(draft, path, ports, io):
                return WizardResult(WizardState.STORAGE_FAILURE, preparation.manifest.preparation_id)
            io.show_public("DRAFT_NEW: values remain untrusted until publication.")

        use_saved = False
        if _has_complete_saved_selection(draft):
            use_saved = (
                io.ask_choice(
                    "resume_squad",
                    ("Use saved squad and prices", "Replace saved squad and prices"),
                )
                == "Use saved squad and prices"
            )
        if use_saved:
            selected = [int(item) for item in draft.selected_element_ids]
            prices = {
                int(key): str(value)
                for key, value in draft.selling_price_m_by_element_id.items()
            }
        else:
            selected, prices = _select_squad(
                io,
                preparation,
                on_pick=lambda selected_ids, selected_prices: ports.save_saved_draft(
                    replace(
                        draft,
                        selected_element_ids=selected_ids,
                        selling_price_m_by_element_id=selected_prices,
                        current_selection_confirmed=False,
                    ),
                    path,
                ),
            )
        draft = replace(
            draft,
            selected_element_ids=selected,
            selling_price_m_by_element_id=prices,
            current_selection_confirmed=False,
        )
        if not _save_or_stop(draft, path, ports, io):
            return WizardResult(WizardState.STORAGE_FAILURE, preparation.manifest.preparation_id)

        entry_id = _ask_integer(io, "entry_id", "FPL entry ID", positive=True)
        draft = replace(draft, entry_id=entry_id)
        if not _save_or_stop(draft, path, ports, io):
            return WizardResult(WizardState.STORAGE_FAILURE, preparation.manifest.preparation_id)
        bank = _ask_money(io, "bank_m", "Bank in millions, to one decimal place")
        draft = replace(draft, bank_m=bank)
        if not _save_or_stop(draft, path, ports, io):
            return WizardResult(WizardState.STORAGE_FAILURE, preparation.manifest.preparation_id)
        free_transfers = _ask_integer(
            io, "free_transfers", "Exact free-transfer count", positive=False
        )
        draft = replace(draft, free_transfers=free_transfers)
        if not _save_or_stop(draft, path, ports, io):
            return WizardResult(WizardState.STORAGE_FAILURE, preparation.manifest.preparation_id)
        chip = io.ask_choice("chip_state", tuple(item.value for item in ChipState))
        draft = replace(draft, chip_state=chip)
        if not _save_or_stop(draft, path, ports, io):
            return WizardResult(WizardState.STORAGE_FAILURE, preparation.manifest.preparation_id)
        if chip != ChipState.NO_CHIP.value:
            io.show_public("UNSUPPORTED_CHIP: saved without changing the selected chip.")
            return WizardResult(WizardState.UNSUPPORTED_CHIP, preparation.manifest.preparation_id)
        if free_transfers == 0:
            io.show_public("UNSUPPORTED_TRANSFER_STATE: Engine v1 cannot evaluate this state.")
            return WizardResult(WizardState.UNSUPPORTED_TRANSFER_STATE, preparation.manifest.preparation_id)

        source = io.ask_text(
            "evidence_source",
            f"Evidence source (Enter uses: {DEFAULT_EVIDENCE_SOURCE})",
        )
        draft = replace(draft, evidence_source=source or DEFAULT_EVIDENCE_SOURCE)
        if not _save_or_stop(draft, path, ports, io):
            return WizardResult(WizardState.STORAGE_FAILURE, preparation.manifest.preparation_id)
        source_hash = _ask_optional_sha256(io)
        draft = replace(draft, evidence_source_sha256=source_hash)
        if not _save_or_stop(draft, path, ports, io):
            return WizardResult(WizardState.STORAGE_FAILURE, preparation.manifest.preparation_id)
        draft = replace(
            draft,
            current_selection_confirmed=io.confirm(
                "current_selection", CURRENT_SELECTION_PHRASE
            ),
        )
        if not _save_or_stop(draft, path, ports, io):
            return WizardResult(WizardState.STORAGE_FAILURE, preparation.manifest.preparation_id)
        errors = ports.validate_saved_draft(draft, preparation)
        if errors:
            summary = ", ".join(f"{item.field}:{item.code}" for item in errors)
            io.show_public(f"INVALID_DRAFT: {summary}")
            return WizardResult(WizardState.CANCELLED, preparation.manifest.preparation_id)

        while True:
            io.show_public(
                "Private output follows. Terminal recording, screen sharing, copied "
                "transcripts, scrollback, and tmux/screen logging may retain it."
            )
            io.show_private(_private_review(draft, preparation))
            review_action = io.ask_choice(
                "review_action", ("Publish this evidence", "Replace squad", "Cancel")
            )
            if review_action == "Cancel":
                io.show_public("CANCELLED: draft saved; nothing published.")
                return WizardResult(WizardState.CANCELLED, preparation.manifest.preparation_id)
            if review_action == "Publish this evidence":
                break
            draft = replace(draft, current_selection_confirmed=False)
            ports.save_saved_draft(draft, path)
            selected, prices = _select_squad(
                io,
                preparation,
                on_pick=lambda selected_ids, selected_prices: ports.save_saved_draft(
                    replace(
                        draft,
                        selected_element_ids=selected_ids,
                        selling_price_m_by_element_id=selected_prices,
                        current_selection_confirmed=False,
                    ),
                    path,
                ),
            )
            draft = replace(
                draft,
                selected_element_ids=selected,
                selling_price_m_by_element_id=prices,
                current_selection_confirmed=io.confirm(
                    "current_selection", CURRENT_SELECTION_PHRASE
                ),
            )
            ports.save_saved_draft(draft, path)
            errors = ports.validate_saved_draft(draft, preparation)
            if errors:
                summary = ", ".join(f"{item.field}:{item.code}" for item in errors)
                io.show_public(f"INVALID_DRAFT: {summary}")
                return WizardResult(WizardState.CANCELLED, preparation.manifest.preparation_id)
        if not io.confirm("publish", PUBLISH_PHRASE):
            io.show_public("CANCELLED: draft saved; nothing published.")
            return WizardResult(WizardState.CANCELLED, preparation.manifest.preparation_id)
        if not _before_deadline(preparation, clock()):
            io.show_public("DEADLINE_REACHED: nothing published.")
            return WizardResult(WizardState.DEADLINE_REACHED, preparation.manifest.preparation_id)
        ports.save_saved_draft(draft, path)
        published = ports.publish(draft, preparation, output_root=evidence_root, clock=clock)
        io.show_public("VERIFIED_EVIDENCE_PUBLISHED")
        if not io.confirm("run", RUN_PHRASE):
            return WizardResult(
                WizardState.VERIFIED_EVIDENCE_PUBLISHED,
                preparation.manifest.preparation_id,
                evidence_path=published.path,
            )
        completed = ports.run(preparation, published, clock=clock)
        verified = ports.load_verified_result(completed.final_manifest_path)
        io.show_private(format_verified_result(verified, observed_at=preparation.observed_at))
        return WizardResult(
            WizardState.VERIFIED_DECISION_AVAILABLE,
            preparation.manifest.preparation_id,
            evidence_path=published.path,
            final_manifest_path=completed.final_manifest_path,
        )
    except (EOFError, KeyboardInterrupt):
        if draft is not None and path is not None:
            try:
                ports.save_saved_draft(draft, path)
            except ManagerEvidenceAuthoringError:
                pass
        if published is not None:
            io.show_public(
                "VERIFIED_EVIDENCE_PUBLISHED: session ended before a verified decision."
            )
            return WizardResult(
                WizardState.VERIFIED_EVIDENCE_PUBLISHED,
                preparation.manifest.preparation_id if preparation else None,
                evidence_path=published.path,
            )
        io.show_public("CANCELLED: no new evidence or decision was claimed.")
        return WizardResult(
            WizardState.CANCELLED,
            preparation.manifest.preparation_id if preparation else None,
        )
    except ManagerEvidenceAuthoringError as exc:
        state = {
            "DEADLINE_REACHED": WizardState.DEADLINE_REACHED,
            "IMMUTABLE_CONFLICT": WizardState.IMMUTABLE_CONFLICT,
            "STORAGE_FAILURE": WizardState.STORAGE_FAILURE,
        }.get(exc.code.value, WizardState.TRUST_CHAIN_INVALID)
        io.show_public(f"{state.value}: trusted authoring boundary rejected the request.")
        return WizardResult(state, preparation.manifest.preparation_id if preparation else None)
    except OperationalRunnerError as exc:
        io.show_public(
            f"TRUSTED_STAGE_FAILED[{exc.code.value}]: no recommendation is available."
        )
        return WizardResult(
            WizardState.TRUST_CHAIN_INVALID,
            preparation.manifest.preparation_id if preparation else None,
            evidence_path=published.path if published else None,
        )
    except (TrustedArtifactValidationError, KeyError, TypeError):
        io.show_public("TRUST_CHAIN_INVALID: no recommendation is available.")
        return WizardResult(
            WizardState.TRUST_CHAIN_INVALID,
            preparation.manifest.preparation_id if preparation else None,
            evidence_path=published.path if published else None,
        )
