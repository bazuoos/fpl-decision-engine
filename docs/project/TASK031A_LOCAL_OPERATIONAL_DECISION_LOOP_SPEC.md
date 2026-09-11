# TASK031A — Local operational decision loop design

## Status and authority

This document designs a safer single-owner path from current official FPL data
and manually verified editable squad state to an immutable Engine v1 decision.
It does not implement that path, change engine calculations, choose a web UX,
start Task026C, access live manager data, create a prediction, create a journal,
or authorize an FPL action.

The design is being prepared in an isolated worktree at
`e10d4c9dc10467730cdb90fcbea494dcdf920b6f`. The primary checkout must remain
unchanged while the reviewed GW4 Task028G completion monitor is active. This
document must not be merged to `main` until that monitor reaches a terminal
state and is deactivated.

Repository code, schemas, validators, immutable artifacts and manifests remain
authoritative. Human football reasoning can explain or challenge an output but
cannot alter xFP, legality, reliability or recommendation ranking.

## Problem

The repository already implements the trusted numerical path, but operating it
requires several low-level commands and a hand-authored manager-evidence JSON
file. A non-technical owner can therefore make transcription, identity, timing
or provenance mistakes before reaching the engine.

The official public manager endpoint represents a locked prior-deadline squad.
It cannot establish the currently editable squad, manager-specific selling
prices, bank, free-transfer count or chip state. The project must not collect an
FPL password or session cookie to close that gap.

The minimum useful product step is a guided local workflow that lets the owner
select the current squad and transcribe the small amount of private state that
only the official Transfers screen can establish. The trusted operational
runner then validates and freezes the evidence and independently calculates the
recommendation.

## Repository-established foundation

| Capability | Existing authority | Current limit |
|---|---|---|
| Coherent official refresh | `refresh.py`, `operational_runner.prepare_gameweek` | Explicit run with network; no background freshness claim |
| Frozen target/deadline/input identity | `PreparationManifest` | Target must be the unique official `is_next` gameweek |
| xFP v0.1 prediction | Existing feature/prediction pipeline | Appearance, goals and assists only |
| Lower-level manual editable state | `editable_manager.py` | Existing experimental evaluation path; not the Task031B integration target |
| Operational manager evidence gate | `load_verified_manager_evidence`, `resume_gameweek` | Trusted Task031B target; requires exact fields and 15 players |
| Transfer legality and ranking | `transfer_decision.py` and trusted optimizer | Zero-or-one free transfer; no hits or chips |
| Reliability | `decision_reliability.py` | Diagnostic only; does not change ranking |
| Trusted presentation | `GameweekDecision` and trusted artifact reader | Existing web slice is read-only by explicit decision ID |
| Prospective human action | `DecisionJournalEntry` | Separate deliberate action before deadline |

No new optimizer or scoring calculation is needed for this workflow. The first
implementation should reduce operator error around the existing authorities.

## Goals

1. Let one local owner prepare an explicit upcoming gameweek from current
   official public data.
2. Make manual squad verification understandable without requiring raw JSON or
   repeated command-line flags.
3. Resolve player names, positions, clubs and current market prices from the
   exact frozen official snapshot while requiring the owner to supply all
   manager-specific facts.
4. Produce exactly the existing `verified-manager-evidence-v1` contract and
   pass it unchanged to `resume_gameweek`.
5. Display only a fully verified `GameweekDecision` as the engine result.
6. Keep the numeric recommendation independent of human theses, popularity,
   questionnaire answers and conversational preferences.
7. Preserve every pre-deadline observation and decision without retrospective
   rewriting.
8. Fail clearly and recoverably when evidence is incomplete, stale, illegal,
   unsupported or too late.

## Non-goals

- Task026C authentication, OIDC, sessions, CSRF, PostgreSQL or tenant storage.
- A network-exposed write API or multi-user product.
- Logging into FPL, scraping an authenticated page or executing transfers.
- OCR as authority. A screenshot may be retained as optional source evidence,
  but extracted values require explicit owner confirmation.
- A new xFP version, reliability-weighted ranking or human-adjusted score.
- Multiple transfers, hits, chips, multi-gameweek optimization, price-change
  speculation or bench-order optimization.
- A final web UX paradigm. The workflow contract must work with a terminal,
  native form or later authenticated web application.
- Personalization. The historical personalization idea remains a research
  hypothesis and cannot affect rankings here.

## Trust boundaries

```text
OFFICIAL PUBLIC FPL BYTES
        |
        v
trusted prepare_gameweek
        |
        +--> immutable preparation + frozen xFP
        |
        v
UNTRUSTED LOCAL AUTHORING UI
  selects IDs; transcribes bank, FT, chip and selling prices
        |
        v
strict manager-evidence authoring/validation boundary
        |
        v
existing trusted resume_gameweek
        |
        +--> manager state -> legal candidates -> numeric recommendation
        |                    -> reliability -> GameweekDecision
        v
trusted artifact reader -> untrusted display
        |
        v
separate deliberate human action -> prospective journal
```

The authoring interface never computes affordability, transfer legality, xFP,
lineups, captaincy, reliability or recommendation order. It may filter and sort
the frozen official player catalogue for usability. Those operations are
presentation conveniences and have no decision authority.

## Proposed single-owner workflow

### 1. Choose and prepare one target

The owner explicitly selects a season and gameweek. The workflow invokes the
existing `prepare-gameweek` authority, which must prove the target is the unique
official next event, capture the deadline, perform one coherent refresh and
freeze the exact features and prediction.

The workflow reports the immutable preparation ID, observation timestamp,
official deadline and model version. It does not call the data “real-time.” The
accurate claim is “official data observed at `<timestamp>`.” A later refresh
creates a different preparation; it never updates an existing one in place.
The owner chooses whether newer public evidence justifies a new preparation;
the interface must not imply that an older preparation tracks later changes.

Downloading the public bootstrap player catalogue is small enough for an
explicit preparation. The expensive per-player history work remains inside the
existing coherent refresh and its resumable snapshot controls. The authoring interface must
use the preparation's copied player artifact and must not independently fetch
all players or create a second refresh loop.

### 2. Author manager evidence locally

The authoring tool opens one exact preparation and presents the 15-player squad
form from its hash-pinned player universe. The owner selects each owned player
by stable element ID; display name, official position and club are populated
from that frozen public artifact rather than typed as authority.

The owner must confirm:

- the current editable squad contains exactly 15 distinct players;
- the squad composition is 2 GK, 5 DEF, 5 MID and 3 FWD;
- no club contributes more than three players;
- bank in exact £0.1m increments;
- current free-transfer count;
- chip state;
- the manager-specific selling price for every player, in exact £0.1m units;
- the values were checked against the current official Transfers screen; and
- the submission is intended for the displayed preparation and deadline.

Selling price is always owner-supplied. Current market price, purchase price or
a third-party price feed cannot substitute for it. Public values may be shown
beside the input only as non-authoritative reconciliation aids.

The tool writes canonical `verified-manager-evidence-v1` bytes with exactly the
fields already accepted by `load_verified_manager_evidence`. It does not add UI
fields to that trusted input. Convenience state stays outside the evidence
contract.

### 3. Review before publication

Before the immutable evidence file is published, the tool shows a confirmation
summary:

- season, target gameweek, preparation ID and deadline;
- 15 selected players grouped by official position;
- bank, free transfers and chip state;
- every selling price and any difference from public market price;
- source description and optional source SHA-256;
- a visible statement that confirmation time will be taken from the trusted
  runner clock, not user input; and
- the current model and action limits.

The final confirmation is explicit and single-use. Drafts have no authority and
must never be accepted by `resume-gameweek`.

### 4. Run the trusted decision

The authoring tool passes the exact preparation manifest and published manager
evidence path to the existing `resume-gameweek` entry point. It does not call
lower-level optimizer functions.

The runner remains responsible for deadline enforcement, season/gameweek
alignment, chip rejection, immutable manager-state publication, player and
selling-price reconciliation, decision identity, legal transfer enumeration,
reliability construction, `GameweekDecision` serialization and final-manifest
validation.

The current operational runner explicitly selects `appearance_only_allowed`.
The output must therefore be labelled a **modeled-component recommendation**,
not expected total FPL points. A recommendation is numerically authoritative
for the frozen Engine v1 contract while remaining limited by that contract.
Engine v1 compares ROLL with zero or one free transfer and assigns no value to
carrying free transfers into later gameweeks. If the manager owns multiple free
transfers, the display must state that the recommendation does not optimize how
many to spend or the future value of retaining them.

### 5. Display the result

The result is rendered only through `load_verified_gameweek_decision` or the
existing trusted application read facade. The display must include:

- ROLL or the exact outgoing/incoming transfer;
- projected modeled-component difference versus ROLL;
- resulting bank;
- optimized XI, captain and vice-captain;
- frozen observation time and deadline;
- model coverage and omissions;
- reliability warnings for materially involved players;
- decision/preparation identities and verified trust state; and
- unsupported states such as chips, hits or more than one transfer.

Reliability must be visible beside the recommendation but remains diagnostic.
The UI cannot silently demote, promote or replace the engine action. Human
football reasoning may appear in a separate notes area clearly labelled as
context rather than engine evidence.

### 6. Record the owner's action separately

Viewing a recommendation does not imply acceptance. If the owner chooses an
action, the existing journal command records `FOLLOW_ENGINE`, `ROLL` or one
exact transfer as a separate prospective event before the deadline. An override
requires a contemporaneous reason. The authoring or display step must never
create a journal entry automatically.

## Authoring contract

The first implementation should add a local authoring artifact that is distinct
from trusted manager evidence:

```text
manager-evidence-draft-v1
  preparation_manifest_sha256
  entry_id
  selected_element_ids[15]
  bank_m
  free_transfers
  chip_state
  selling_price_m_by_element_id
  evidence_source
  evidence_source_sha256 | null
  current_selection_confirmed
```

This draft is mutable and explicitly untrusted. Publication resolves player
identity fields from the preparation, validates the complete form, and creates
new canonical `verified-manager-evidence-v1` bytes using no-overwrite semantics.
The trusted runner still performs all validation again. A draft identifier or
path never enters a final manifest.

The implementation should expose functions through a narrow port rather than
couple the UI to `operational_runner` internals:

```text
load_preparation_for_authoring(explicit_manifest) -> bounded public catalogue
validate_draft(draft, preparation) -> field errors only
publish_verified_evidence(draft, preparation) -> immutable reference
run_existing_resume(explicit_preparation, explicit_evidence) -> existing result
```

`publish_verified_evidence` must reuse or delegate the existing validation
rules. It cannot become a second definition of squad legality or manager
evidence semantics.

## Local privacy and storage

- Draft and verified manager evidence remain below ignored, owner-only `data/`.
- New directories are mode 0700 and files mode 0600.
- Publication is canonical, atomic and no-overwrite.
- Raw screenshots are optional private source evidence, never required Git
  content and never served by the read-only decision application.
- Logs contain stable error codes and field names, not squad contents, entry ID,
  prices, filenames or screenshot text.
- The staged-sensitive-content guard remains mandatory before later commits.
- The absence of a verified offsite backup remains explicit. Task031 does not
  claim disaster recovery for these prospective records.

## State model

```text
NOT_PREPARED
PREPARING
MANAGER_EVIDENCE_REQUIRED
DRAFT_INCOMPLETE
READY_FOR_CONFIRMATION
VERIFIED_EVIDENCE_PUBLISHED
DECISION_RUNNING
VERIFIED_DECISION_AVAILABLE
UNSUPPORTED_CHIP
UNSUPPORTED_TRANSFER_STATE
DEADLINE_PASSED
TRUST_CHAIN_INVALID
IMMUTABLE_CONFLICT
UPSTREAM_UNAVAILABLE
```

Only `VERIFIED_DECISION_AVAILABLE` exposes a current recommendation. Each state
is derived from trusted artifacts or a bounded operation result. The interface
does not infer success from file presence, HTTP success or a process exit alone.

## Failure and recovery behavior

| Failure | Required behavior |
|---|---|
| Official refresh interrupted | Resume the exact refresh; never select a newer snapshot silently |
| Target no longer official next | Stop and require a new explicit preparation |
| Draft incomplete | Preserve only local draft convenience state; publish nothing authoritative |
| Unknown/duplicate/wrong-position player | Reject against the preparation's player universe |
| Selling price missing or invalid | Block publication; never substitute another price |
| Chip selected | Preserve the input and report the exact unsupported chip code; no recommendation |
| No free transfer or a request needing multiple transfers/hits | Report the unsupported transfer state; never reinterpret the one-transfer result |
| Deadline reached during input | Reject evidence publication or finalization through the trusted clock |
| Existing identical evidence/decision | Validate and return the immutable existing result |
| Existing conflicting bytes | Stop with immutable conflict; never repair by overwrite |
| Trust-chain validation fails | Return no recommendation payload |
| Display closes or crashes | Resume from explicit preparation/evidence identities; never use “latest” as authority |

## Testing requirements for implementation

All tests use synthetic public and manager evidence. No test reads operational
`data/`, contacts FPL or uses a real entry ID.

1. Contract tests prove the authoring output is accepted by
   `load_verified_manager_evidence` and rejected after any field tampering.
2. Differential tests compare authoring validation with the existing trusted
   manager-state and runner validators; the authoring layer cannot accept a
   case the trusted boundary rejects.
3. Tests cover 15-player composition, duplicates, unknown IDs, positions,
   three-per-club, money precision, bank, free transfers, chips and all selling
   prices.
4. Deadline tests use an injected clock immediately before, exactly at and
   after the deadline.
5. Idempotency and crash tests cover draft save, evidence publication, manager
   state, decision artifacts and final manifest.
6. Privacy tests prove logs and application responses omit entry IDs, squad
   contents, prices and local paths.
7. A new Python AST import-boundary guard proves the authoring/application
   packages do not import optimizer, features, predictions, transfer-decision
   or journal internals. This is new Task031B tooling; there is no existing
   Python equivalent of the frontend boundary command to reuse.
8. End-to-end tests use the public operational entry points and verify the final
   artifact through the trusted reader.
9. Presentation tests require model caveats and reliability warnings and prove
   the browser does not recompute recommendation values.
10. Fresh-checkout CI runs entirely from committed synthetic fixtures.

## Implementation slices

### Task031B — Verified manager-evidence authoring foundation

Add a local, UI-neutral authoring service and CLI using synthetic tests only.
It loads one explicit preparation, exposes a bounded catalogue, validates a
draft, publishes canonical owner-only `verified-manager-evidence-v1`, and can
invoke the existing resume entry point. It must not add HTTP writes, alter the
engine, contact FPL independently, journal an action or read real manager data.
The integration target is the operational
`verified-manager-evidence-v1`/`resume_gameweek` chain, not the separate
experimental `evaluate-editable-squad` command or its decision artifact.

### Task031C — Guided local owner interface

Choose the smallest non-technical local interface after Task031B is proven. It
may be a local native/form flow or a deliberately local-only application seam,
but choosing it does not settle the eventual product UX. Any HTTP write route
would require explicit reconciliation with Task026C's authentication and
privacy boundary before implementation.

### Later model work

Evaluate missing scoring components through separately preregistered research.
The operational workflow must remain versioned and honest while xFP v0.1 is in
use. Better input UX cannot convert modeled-component xFP into complete expected
FPL points.

## Acceptance criteria for this design

1. A fresh reviewer can identify every authoritative existing component and
   every proposed component.
2. Manual selection convenience cannot become a second engine or price source.
3. “Current” official data is tied to one observation and preparation rather
   than described as real-time.
4. Human strategy context cannot affect numeric recommendation ranking.
5. The workflow produces the existing manager-evidence and final artifact
   contracts without changing them.
6. Private data, deadline, idempotency and recovery behavior are explicit.
7. Current model and action limitations are visible before the owner relies on
   a result.
8. Task031B is implementable without Task026C, a chosen web UX or real manager
   data.
9. No part of the design authorizes implementation, a live refresh, a prediction
   freeze, a journal entry or an FPL action.

## Review questions

1. Does the design correctly reuse the existing prepare/resume/reader chain
   without duplicating decision authority?
2. Can Task031B remain outside Task026C while producing private local evidence
   safely?
3. Are all manager-only facts distinguished from frozen public facts?
4. Is the distinction between explicit snapshot freshness and “real-time” data
   honest and usable?
5. Does the design preserve numerical independence from human reasoning and
   personalization ideas?
6. Are xFP v0.1 and Engine v1 limits prominent enough to prevent overclaiming?
7. Can a non-technical guided interface be added later without changing the
   trusted evidence contract?
8. Are error, deadline, recovery, privacy and no-overwrite behaviors complete?
9. Does the design avoid interfering with the active Task028G monitor and make
   the delayed-merge constraint explicit?
10. What is blocking, required hardening or optional hardening before Task031B?
