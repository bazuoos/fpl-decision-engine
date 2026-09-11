# TASK031C — Guided local owner interface design

## Status and authority

This document designs the first non-technical local interface over the reviewed
Task031B manager-evidence authoring foundation. It does not implement the
interface, prepare or refresh a gameweek, publish manager evidence, run the
engine, create a journal entry, choose a long-term web UX, start Task026C, or
authorize an FPL action.

The design is based on local Task031 commits `4a273e2` and `3e2a4e7` in the
isolated `codex/task031a-operational-decision-loop` branch. Those commits must
remain off `main` until the active GW4 Task028G monitor reaches a verified
terminal state and is deactivated. Repository code, schemas, validators,
immutable artifacts and Git history remain authoritative.

## Decision

Task031C should implement a guided interactive terminal wizard as a disposable
local adapter over Task031B. The wizard is the minimum interface that removes
raw JSON and repeated command-line flags without creating a local write server,
authentication exception, browser security boundary, new UI framework or
platform-specific application.

This choice does not settle the product UX. The prompt adapter and presentation
can later be replaced by a native or authenticated web interface while the
Task031B draft, evidence and operational contracts remain unchanged.

## Why this interface is first

| Option | Benefit | Current reason not selected |
|---|---|---|
| Interactive terminal wizard | No new dependency or server; easy to test; works through existing local Python entry point | Temporary interface with limited visual polish |
| Local browser form | Familiar form controls and search | Adds an HTTP write surface and collides with unresolved Task026C authentication, CSRF and ownership boundaries |
| macOS-native form | Better local usability | Adds a second language, platform-specific packaging and accessibility work before the workflow is proven |
| Tkinter or third-party TUI | Richer widgets | Runtime availability or a new dependency becomes part of the trusted operational path |
| Continue with repeated flags | Already implemented | Too error-prone for normal owner use, exposes bank and selling-price values through shell history and process argument lists, and does not provide a guided review step |

## Goals

1. Guide one local owner from an exact reviewed preparation to a verified
   Engine v1 decision without hand-writing JSON.
2. Make the frozen observation time, deadline, model scope and action limits
   visible before any private input is requested.
3. Let the owner find players by public name, club or position while storing
   only exact element IDs as selections.
4. Require explicit manager-specific selling prices, bank, free transfers and
   chip state; never infer or default those facts from public data.
5. Save incomplete work as an owner-only, mutable, explicitly untrusted draft.
6. Show a complete private review screen and require a deliberate single-use
   publication confirmation.
7. Delegate publication and calculation to Task031B and `resume_gameweek`.
8. Render the result only from the verified final artifact chain, including
   model limitations and reliability warnings.
9. Keep engine rankings independent of human strategy, questionnaire answers,
   popularity and conversational preferences.

## Non-goals

- Preparing a gameweek or fetching current public data inside the wizard.
- Discovering or silently selecting a `latest` preparation.
- FPL login, cookies, authenticated scraping or automatic transfers.
- A browser endpoint, background daemon, remote access or multi-user storage.
- Task026C identity, OIDC, sessions, CSRF, PostgreSQL or tenant isolation.
- A new optimizer, xFP version, reliability rule or ranking adjustment.
- Hits, chips, multiple transfers, multi-gameweek planning, future free-transfer
  value, price-change prediction or automatic bench-order optimization.
- Journaling the owner's eventual action. That remains a separate prospective
  command after the recommendation is reviewed.
- Screenshot OCR. A source screenshot may be privately retained and hashed, but
  every extracted manager fact still requires explicit owner confirmation.
- A permanent UX choice.

## Trust boundaries

```text
explicit preparation_manifest.json
        |
        v
Task031B load_preparation_for_authoring
  validates complete pinned public chain
        |
        v
UNTRUSTED LOCAL WIZARD
  search/filter, prompts, mutable draft, review display
        |
        v
Task031B validate_draft + publish_verified_evidence
  canonical verified-manager-evidence-v1
        |
        v
Task031B run_existing_resume -> trusted resume_gameweek
  legality, optimizer, reliability, GameweekDecision, final manifest
        |
        v
trusted artifact reader
        |
        v
UNTRUSTED LOCAL PRESENTATION
```

The wizard may organize, search and display public catalogue rows. It cannot
calculate affordability, legality, xFP, transfer gain, lineup, captaincy,
reliability or recommendation order. It must not import decision, prediction,
feature, transfer-decision, reliability or journal internals.

## Command boundary

The implementation adds one explicit command:

```text
python -m fpl_decision_engine guided-manager-decision \
  --preparation-manifest <exact preparation_manifest.json> \
  [--draft <explicit private draft path>]
```

The preparation manifest is always required. The command never scans for or
chooses the newest preparation. If `--draft` is omitted, the wizard derives a
mutable path below ignored owner-only storage using only the public preparation
ID:

```text
data/manager/drafts/fpl/<preparation_id>/current.json
```

The default is convenient but is printed before use. An explicit draft path is
required to resume a draft stored elsewhere. The verified evidence continues to
use Task031B's content-addressed no-overwrite path. Drafts never enter final
manifests and may be edited or replaced; published evidence never is.

## Interaction model

All interaction goes through a narrow `PromptIO` protocol so tests can inject
scripted answers and capture output without patching global input or writing to
a real terminal:

```text
show_public(message)
show_private(message)
ask_text(field, prompt) -> text
ask_choice(field, choices) -> exact choice
confirm(field, phrase) -> bool
```

`show_public` is safe for ordinary logs or captured test output.
`show_private` is deliberately visible only to the local owner and may contain
the selected squad, selling prices or bank. The implementation must not send
private output through Python logging. Before private review output, the wizard
warns that terminal recording, screen sharing, copied transcripts, terminal
scrollback and terminal-multiplexer logging such as `tmux` or `screen` can
retain manager-specific information.

EOF, `Ctrl-C`, a closed terminal or an explicit cancel produces `CANCELLED`,
saves the latest validly serializable draft where possible, publishes no
evidence and runs no decision.

## Guided flow

### 1. Establish the exact public context

The wizard loads the supplied preparation through
`load_preparation_for_authoring`. Before asking for private data it displays:

- preparation ID;
- season and target gameweek;
- official-data observation timestamp;
- official deadline in UTC and the host's local timezone;
- remaining time based on an injected trusted UTC clock;
- xFP v0.1 coverage: appearance, goals and assists only; and
- Engine v1 limits: ROLL or one free transfer, no hits or chips, one-gameweek
  objective and no value for preserving free transfers.

The local-time rendering is presentation only. UTC remains authoritative. An
invalid trust chain or a time at or after the deadline stops before private
prompts. The Task031B publication and trusted runner repeat deadline checks.
The startup banner recommends the guided command for normal owner use and warns
that the older flag-based `publish-manager-evidence` command can expose private
bank and selling-price values through shell history and process argument lists.

### 2. Resume or initialize an untrusted draft

If the explicit/default draft exists, the wizard loads it through Task031B and
requires its preparation hash to match. It reports which fields are complete
without printing their values in the public status area. A malformed or
mismatched draft is never repaired automatically; the owner may choose a new
explicit draft path or stop.

A new draft begins with the preparation hash and incomplete values. The wizard
autosaves after every accepted answer and selection. Autosave uses Task031B's
owner-only atomic mutable-draft writer. A saved draft is never described as
verified, current or safe to run.

### 3. Select the squad by stable identity

The wizard fills the required slots in the official order: 2 GK, 5 DEF, 5 MID
and 3 FWD. For each slot the owner enters a search term. Search matches public
display name, team name, team short name if later exposed, or exact element ID.
Results are deterministic and limited to a small numbered page. Each row shows:

```text
element ID | display name | position | club | frozen market price
```

Choosing a numbered row resolves immediately to its element ID. Duplicate
selection is blocked locally and Task031B validates it again. Search order and
filtering have no recommendation authority. The wizard continuously displays
position completion and club counts, but Task031B remains the publication gate.

The owner can remove or replace any selection before publication. A change
invalidates the prior review confirmation.

### 4. Enter manager-only facts

The wizard asks for:

1. positive FPL entry ID;
2. bank in £0.1m units;
3. exact free-transfer count;
4. chip state from the existing enum;
5. selling price for each selected player; and
6. optional source-file SHA-256.

The evidence-source description defaults visibly to “Official Transfers screen,
manually verified” and can be edited. Frozen public market prices are shown only
beside selling-price prompts as reconciliation aids. Pressing Enter cannot copy
the market price. Every selling price requires explicit text input accepted by
Task031B's exact money parser.

If a chip other than `NO_CHIP` is selected, the wizard saves the draft and
enters `UNSUPPORTED_CHIP`. It does not suggest pretending no chip is active. A
zero-free-transfer state may be preserved in the draft, but the wizard explains
that current Engine v1 cannot produce the one-free-transfer operational result.
Multiple available free transfers may be recorded, while the result must state
that Engine v1 still evaluates at most one and ignores future carry value.

### 5. Validate and privately review

The wizard calls `validate_draft`. Field errors are mapped to plain prompts and
the owner is returned to the affected field; submitted private values are not
placed in log messages or exception strings.

When validation passes, one private review screen shows:

- gameweek, preparation, observed time and deadline;
- the 15 players grouped by position with club;
- each selling price beside frozen market price and the difference;
- bank, free transfers and chip state;
- evidence-source description and whether a source hash is present;
- xFP v0.1 and Engine v1 limits; and
- a warning that publication creates immutable prospective evidence but does
  not execute an FPL action or journal a human decision.

The review view may calculate price differences for display only. Those values
never enter evidence or ranking. Any return to editing invalidates confirmation.

### 6. Publish with deliberate confirmation

The owner must type the exact phrase `PUBLISH VERIFIED EVIDENCE`. A yes/no
default or a stale earlier answer is insufficient. Immediately before calling
Task031B, the wizard rebuilds the draft from current in-memory fields and saves
it. Task031B then revalidates the preparation, draft, clock, canonical contract,
permissions and no-overwrite publication.

The wizard holds no separate “verified” flag. Success exists only when
`publish_verified_evidence` returns a validated immutable reference. If the
deadline is crossed, the trust chain changes, or publication fails, the wizard
prints the stable error code, retains the draft and exposes no success state.

### 7. Run the trusted engine separately

After publication, the wizard asks for the separate exact phrase `RUN ENGINE`.
Declining leaves verified evidence available for an explicit later pre-deadline
run. Acceptance calls only `run_existing_resume` with the exact preparation and
published evidence references.

No recommendation is shown from the return status alone. The wizard loads the
returned final manifest through `load_verified_gameweek_decision` and renders
only the verified object. Any reader failure yields `TRUST_CHAIN_INVALID` and no
recommendation payload.

### 8. Present the numerical result

The private result screen displays:

- ROLL or exact outgoing/incoming player;
- modeled-component gain versus ROLL;
- resulting bank;
- optimized XI, captain, vice-captain and bench;
- reliability status and warnings for materially involved players;
- observation time and official deadline;
- decision and preparation IDs;
- verified trust state;
- xFP v0.1 component coverage; and
- Engine v1 action limits, including ignored future free-transfer value.

It must use trusted artifact fields verbatim and must not recompute or reorder
recommendations. Human strategy notes are absent from this first interface. A
later notes view must keep them visibly separate from engine evidence.

Viewing the result does not imply acceptance. The wizard ends by stating that
any real FPL action is manual and any prospective journal entry is a separate
deliberate step before the deadline.

## State model

```text
LOADING_PREPARATION
PUBLIC_CONTEXT_READY
DRAFT_NEW
DRAFT_RESUMED
SQUAD_INCOMPLETE
MANAGER_FACTS_INCOMPLETE
UNSUPPORTED_CHIP
UNSUPPORTED_TRANSFER_STATE
READY_FOR_PRIVATE_REVIEW
AWAITING_PUBLICATION_PHRASE
VERIFIED_EVIDENCE_PUBLISHED
AWAITING_RUN_PHRASE
DECISION_RUNNING
VERIFIED_DECISION_AVAILABLE
CANCELLED
DEADLINE_REACHED
TRUST_CHAIN_INVALID
IMMUTABLE_CONFLICT
STORAGE_FAILURE
```

Only `VERIFIED_DECISION_AVAILABLE` may render a recommendation. State
transitions are explicit return values suitable for tests; terminal text is not
parsed to infer state.

## Privacy and terminal behavior

- The wizard is local and single-owner, but terminal output is not treated as
  encrypted storage.
- It emits no private values through `logging`, exception messages, analytics,
  shell arguments, process titles or environment variables.
- Private input values are collected interactively after process start. The
  command line contains only preparation and optional draft paths.
- Normal shell history therefore contains no squad, entry ID, bank or prices.
- The existing flag-based `publish-manager-evidence --bank/--pick` command is
  privacy-inferior because manager-specific bank and selling-price values can
  be retained in shell history and exposed in process argument lists. Task031C
  must add a warning to that command's `--help` output and README usage section,
  directing normal owner use to `guided-manager-decision`.
- Private review output is intentional and visually marked. The owner is warned
  before it appears, including that terminal scrollback and `tmux`/`screen`
  logging may persist it.
- Draft and evidence files remain under ignored storage with Task031B's 0700
  directory and 0600 file enforcement.
- The interface never reads arbitrary screenshots. An optional source hash is
  supplied explicitly; source custody stays outside the wizard.
- No crash report should include the in-memory draft or prompt transcript.
- The staged-sensitive-content guard remains required before repository commits.
- There is still **NO VERIFIED OFFSITE BACKUP** for local prospective evidence.

## Failure and recovery behavior

| Failure | Required response |
|---|---|
| Missing, malformed or tampered preparation | Stop before private prompts; no fallback discovery |
| Preparation changes during input | Task031B revalidation blocks publication |
| Draft missing | Offer a new draft at the displayed explicit path |
| Draft malformed or bound to another preparation | Stop or require a different path; never coerce it |
| Invalid answer | Show field and stable reason; re-prompt without logging value |
| No search match | Keep the slot empty and allow a new search |
| Ambiguous player name | Require numbered selection resolving to exact ID |
| Duplicate or illegal squad | Return to squad editing; publish nothing |
| Missing selling price | Return to that player's prompt; never use market price |
| Unsupported chip or transfer state | Save draft, state limitation, produce no recommendation |
| Cancel, EOF or interrupt | Best-effort atomic draft save; no evidence or engine run |
| Deadline reached at any stage | Stop; Task031B/runner gate takes precedence over reuse |
| Draft-save failure | Stop before accepting later answers as durable |
| Publication conflict | Preserve both identities/paths in memory but print only bounded error information |
| Engine failure | Preserve published evidence; show stable code; no inferred recommendation |
| Trusted-reader failure | Show no action, XI, captain or gain |
| Terminal closes after publication | Resume later using exact preparation/evidence references if still pre-deadline |

## Implementation structure

The implementation should add one module such as
`fpl_decision_engine.local_decision_wizard` containing only:

- `PromptIO` and a standard terminal adapter;
- immutable wizard-state/result types;
- deterministic public catalogue search and display formatting;
- draft progression and autosave orchestration;
- explicit confirmation-phrase handling;
- calls to the four Task031B ports; and
- verified-result loading and formatting through the trusted reader.

The CLI entry point constructs the terminal adapter and calls one public wizard
function. Business validation remains in Task031B and the operational runner.
Formatting functions receive immutable view models and return text; they do not
read files or calculate decisions.

## Testing requirements

All tests use scripted PromptIO and committed synthetic fixtures. They do not
read operational `data/`, contact FPL, use a real entry ID or depend on a TTY.

1. A complete scripted flow publishes Task031B evidence, calls the real trusted
   resume path, loads the result through the trusted reader and reaches
   `VERIFIED_DECISION_AVAILABLE`.
2. Preparation tests cover missing, non-exact, malformed, hash-tampered and
   deadline-passed inputs before any private prompt.
3. Search tests cover exact ID, case-insensitive name, club, ambiguity,
   pagination, deterministic ordering and no-match behavior.
4. Draft tests cover new, resumed, malformed, mismatched, autosaved,
   interrupted and storage-failure states.
5. Input tests cover all 15 slots, replacement, duplicates, composition,
   club limit, every selling price, money precision, bank, free transfers,
   chip state and optional source hash.
6. Confirmation tests prove exact phrases, edit-invalidates-confirmation,
   separate publish/run gates and no engine call after decline or cancellation.
7. Clock tests cover before, exactly at, after and crossing the deadline during
   private input, review, publication and engine start.
8. Failure injection covers every Task031B port and trusted-reader failure;
   no failed path renders recommendation fields.
9. Privacy tests capture logging, exceptions and public output and prove they
   contain no synthetic entry ID, bank, prices or squad list. Private review
   output is tested separately as deliberate local disclosure.
10. AST tests forbid direct imports of decision, prediction, feature,
    transfer-decision, reliability and journal internals.
11. CLI tests prove private values never appear in arguments and the wizard
    requires one exact preparation manifest. They also prove that the legacy
    flag-based publishing command's `--help` text carries the privacy warning.
12. Existing 596 Python tests, frontend contract checks and fresh-checkout CI
    remain green without skips or weakened assertions.

## Acceptance criteria

1. A non-technical owner can complete the manager-evidence workflow without
   editing JSON or placing private values in shell history.
2. The wizard cannot produce or display a recommendation without a valid
   Task031B publication, trusted runner completion and trusted-reader result.
3. All manager-only facts remain explicit owner inputs and all public identity
   fields come from one exact frozen preparation.
4. Draft recovery cannot be confused with verified evidence or a journal.
5. Deadline, cancellation and trust failures leave no false success state.
6. The engine remains numerically independent of human reasoning.
7. No HTTP write surface, FPL credential, network fetch, Task026C dependency or
   permanent UX decision is introduced.
8. Private display and storage behavior is honest about local terminal and
   disaster-recovery limits.
9. The README and `publish-manager-evidence --help` explicitly warn that its
   bank and pick arguments may be retained in shell history and process lists,
   and direct normal owner use to `guided-manager-decision`.

## Proposed implementation boundary

After independent design review, Task031C implementation may add the wizard
module, one CLI command, synthetic tests and usage documentation in this
isolated branch. The usage changes include the legacy flag-based command's
README and `--help` privacy warning. It may not run against real manager data or
a live preparation during implementation review. Merge and push remain blocked
by the active GW4 monitor's exact-commit requirement.

## Review questions

1. Is a terminal wizard the smallest useful interface that preserves Task026C
   and long-term UX flexibility?
2. Does the design keep Task031B and the operational runner authoritative?
3. Are public and private display channels separated honestly?
4. Can a transcript, shell history or error path leak unnecessary manager data?
5. Are autosave, cancellation, deadline and restart semantics safe?
6. Are exact publication/run confirmation phrases proportionate and usable?
7. Can the interface display a recommendation only after trusted-reader
   verification?
8. Does the design handle unsupported chips and transfer states without
   silently changing owner input?
9. Are the tests sufficient to prevent the wizard becoming a second engine?
10. What is blocking, required hardening or optional hardening before
    implementation?
