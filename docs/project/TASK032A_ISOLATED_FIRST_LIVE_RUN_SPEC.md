# TASK032A — Isolated first live operational run design

## Status and authority

This document designs a safe first real pre-deadline use of the reviewed
Task031 guided manager workflow while the GW4 Task028G completion monitor is
active. It does not fetch FPL data, inspect or create manager evidence, run the
engine, journal a decision, alter the active monitor, merge Task031 into
`main`, push a branch, start Task026C or authorize an FPL action.

The design is based on local Task031 commit `0584e22` in the isolated
`codex/task031a-operational-decision-loop` lineage. Repository code, schemas,
validators, immutable artifacts and Git history remain authoritative. The
Task028G schedule is an independent post-deadline completion/evaluation job
pinned to primary-checkout commit `e10d4c9`.

## Problem

A valid GW4 preparation currently exists, but its official data was observed at
`2026-09-09T19:19:16.939033Z`. The GW4 deadline is
`2026-09-12T12:30:00.000000Z`. That preparation proves what was known at its
observation time; it is not automatically current merely because its trust
chain remains valid.

Task031C also defaults draft and evidence storage relative to the process's
working directory. The reviewed code currently lives in a temporary worktree.
A real run from that directory could place irreplaceable prospective evidence
under `/private/tmp`, while using the primary repository's normal operational
roots could overlap the active monitor's files.

The first live run therefore needs explicit, durable storage and a mechanical
non-overlap check before any network request or private prompt.

## Decision

Use the reviewed Task031 code from a dedicated worktree, but write every live
artifact below one explicit sandbox rooted in the primary repository's ignored
`data/` tree:

```text
/Users/nattawatyamchaianan/Projects/fpl-decision-engine/
  data/operational-sandboxes/task032-gw4-first-live-run/
    raw/fpl/
    clean/fpl/
    features/fpl/
    predictions/fpl/
    operations/fpl/
    manager/drafts/fpl/
    manager/evidence/fpl/
```

The directory is durable relative to disposable worktrees and is already
covered by the repository's root `data/` Git exclusion. It remains local-only
and has **NO VERIFIED OFFSITE BACKUP**.

Task032B should add one missing presentation option to the guided command:

```text
--evidence-root <explicit private evidence root>
```

The existing `--draft` option already permits an explicit durable draft file.
The underlying `run_guided_manager_decision` function already accepts an
`evidence_root`; Task032B only exposes that reviewed port through the CLI. No
business validation, evidence schema, output identity or engine behavior
changes.

## Trust and isolation boundaries

```text
primary checkout at e10d4c9
  + active Task028G schedule
  + standard data/{raw,clean,features,predictions,operations}/...
                    || mechanical path non-overlap
                    \/
Task032 worktree at reviewed Task031 lineage
  + explicit operational sandbox under data/operational-sandboxes/...
  + fresh public preparation
  + private draft/evidence
  + trusted operational decision chain
```

The exact Task028G schedule plan and its adjacent SHA-256 file are mandatory,
explicit, non-discoverable preflight inputs. The preflight never searches for a
current, newest or active plan. An omitted plan path, omitted digest path,
missing file, malformed file or hash mismatch is a hard failure; absence is
never interpreted as “no active monitor.” The verified plan is the sole source
of monitor paths and label identity.

The live-run preflight proves that none of the sandbox's resolved roots is equal
to, contains, or is contained by any scheduled raw, clean, feature, prediction,
evaluation or Task028B control root. String-prefix checks are insufficient;
resolved path ancestry must be used.

The preflight also proves:

- primary `HEAD` remains exactly `e10d4c9`;
- the active plan hash remains
  `cee32fb4623e098dc814b535df30e62814a33262ba179142c4d9e1b71b761e4c`;
- the Task032 code checkout is exactly the independently reviewed revision;
- the sandbox is below the repository's ignored root `data/`;
- no sandbox file is tracked, staged or visible through `git status`;
- the selected Python interpreter is the repository's pinned environment;
- the clock is explicit UTC and still strictly before the official deadline;
  and
- sufficient disk space exists before starting a full public refresh.

As defense in depth, the preflight also queries the live `launchctl` domain for
the plan's exact label. It verifies that the loaded job points to the expected
controller, plan and digest arguments and reports an expected loaded/idle or
running state. A missing job, changed arguments, unexpected state or inability
to inspect the job fails closed even when the plan bytes remain hash-valid.

Failure of any check stops before network access and before manager prompts.

## Fresh public preparation

The old GW4 preparation is preserved unchanged. The real run creates a new
preparation identity using existing `prepare-gameweek` with all five data roots
set explicitly to the sandbox. It performs the existing coherent official
refresh and prediction pipeline; it does not copy or mutate the monitor's
snapshot.

Preparation remains deliberately manual. “Fresh enough” is a human operational
choice based on the remaining deadline buffer; this task does not invent a new
model or freshness threshold. The preparation's exact observation timestamp is
shown before manager input. If preparation fails or the deadline becomes too
close to complete careful review, stop and retain the prior evidence rather
than switching silently to an older preparation.

The implementation must not add a second refresh algorithm, download players
one by one outside the existing bounded refresh, or make Task031C fetch data.

## Private manager evidence

The guided command runs from the Task032 worktree with three explicit absolute
paths:

1. the new sandbox preparation manifest;
2. the sandbox draft file; and
3. the sandbox evidence root.

The command line contains paths only. Entry ID, squad, bank, free transfers,
chip state and selling prices are entered interactively. The terminal privacy
warnings remain applicable. No screenshot, authentication cookie, password or
FPL session token is required.

The owner must confirm facts from the official Transfers screen at the time of
the run. Public market price is only a reconciliation aid and never replaces
manager-specific selling price. Unsupported chip or zero-free-transfer states
stop without reinterpretation.

## Result and action boundary

The engine result may be displayed only through the Task031 trusted-reader
gate. It remains a numerical Engine v1 recommendation with the existing model
scope and limitations. Human strategy context does not change its ranking.

The result does not execute an FPL action. After reviewing it, the owner may
manually act on the official site. A prospective journal entry is a separate
explicit operation and must never be reconstructed after the deadline as if it
were prospective.

The first real run must stop after producing and verifying the decision unless
the owner separately authorizes journal creation. It does not capture outcome
evidence before the gameweek is complete.

## Operational sequence

### Gate 1 — implementation review

Task032B adds `--evidence-root`, CLI dispatch coverage and an explicit preflight
command backed by a pure path-isolation validator. All tests use synthetic paths
and fixtures.
It performs no live request and reads no private evidence.

### Gate 2 — independent review

Claude independently reviews Task032B, including adversarial path-overlap cases,
CLI privacy, deadline behavior and proof that engine authority is unchanged.
The candidate is committed only after a SAFE verdict.

### Gate 3 — sanitized dry run

Run the isolation preflight and the guided workflow against committed synthetic
fixtures with a disposable synthetic sandbox. Verify owner-only permissions,
restart, cancellation, publication, trusted result display and cleanup rules.
The sanitized evidence contains paths, hashes, sizes, modes and status only.

### Gate 4 — owner-authorized public refresh

Only after the first three gates pass, obtain explicit owner authorization for
the live official FPL refresh. Re-run the complete preflight immediately before
starting the refresh. Create a new exact GW4 preparation in the durable sandbox.
Do not use authenticated endpoints.

### Gate 5 — owner-entered private facts

Re-run the complete preflight before constructing the guided prompt adapter or
requesting any manager fact. The owner then runs the guided command locally and
enters manager-only facts. Codex does not request those values in chat or print
them in review material. Re-run the preflight after private review and before
accepting `PUBLISH VERIFIED EVIDENCE`, then again before accepting `RUN ENGINE`.
Any changed monitor state pauses the workflow without discarding the draft or
published evidence. The wizard publishes immutable evidence and runs the
trusted engine only after its two confirmation phrases.

Task032B may provide these later checks through one injected, block-only safety
callback. The CLI constructs it only after the initial preflight succeeds. The
wizard invokes it at the two transition gates; it receives no draft, manager
fact, engine payload or recommendation and returns no value that can alter
them. Success permits the existing transition, while any exception stops it.
Task031B and the trusted reader remain the only evidence and result authorities.

### Gate 6 — verify and preserve

Re-open the result through the trusted reader, record only sanitized identities
and hashes for independent review, and verify the sandbox is absent from Git.
Do not claim disaster recovery. The evidence remains exposed to loss of the Mac
until Task027E/F is completed.

## Failure behavior

| Failure | Required response |
|---|---|
| Primary HEAD or schedule-plan hash changed | Stop; reassess monitor isolation |
| Sandbox overlaps any scheduled root | Stop before network/private input |
| Sandbox is outside ignored `data/` | Stop before writing |
| Stale or invalid Task031/032 code revision | Stop; do not mix implementations |
| Refresh interruption | Resume only the exact incomplete sandbox snapshot through the existing explicit resume option |
| Official target is no longer uniquely GW4 | Stop; do not force a target |
| Deadline reached or insufficient careful-review time | Stop without publication |
| Draft/evidence permissions fail | Stop before accepting later facts as durable |
| Unsupported manager state | Preserve exact draft; produce no recommendation |
| Trusted runner or reader fails | Preserve evidence; show no recommendation |
| Terminal or Mac closes | Resume exact sandbox preparation/draft only if still pre-deadline |
| Active monitor changes state during the run | Stop at the next mandatory preflight: before refresh, prompts, publication or engine run; finish no new step until assessed |
| Accidental staging visibility | Stop, unstage without deleting, investigate ignore boundary |

## Task032B implementation boundary

Task032B may add:

- the guided CLI `--evidence-root` path option;
- a small pure path-isolation validator and explicit preflight CLI requiring
  exact `--schedule-plan` and `--schedule-plan-sha256-file` inputs;
- bounded live `launchctl` inspection for the plan's exact label and arguments;
- one injected block-only wizard safety callback for the pre-publication and
  pre-engine transition checks, with no manager or decision payload access;
- synthetic tests for equal, ancestor, descendant, symlink-resolved and disjoint
  paths;
- tests proving preflight failure precedes network and private prompts;
- CLI/help documentation; and
- a sanitized first-run runbook.

It must not add:

- a second refresh or decision implementation;
- private facts in arguments, environment variables, logs or fixtures;
- automatic live execution;
- monitor lifecycle changes;
- FPL authentication or action execution;
- model, optimizer, reliability, schema, web, CI or Task026C changes; or
- a claim that local evidence is backed up.

## Testing requirements

1. `--evidence-root` reaches the existing Task031C port exactly and has no
   manager values.
2. The isolation validator rejects equality and ancestry in both directions for
   every scheduled data root.
3. Symlink aliases cannot bypass overlap detection.
4. A malformed, missing or hash-mismatched schedule plan fails closed.
   Both the exact plan and digest paths are required arguments; discovery is
   forbidden and absence cannot mean that no monitor exists.
5. A sandbox outside the repository's ignored `data/` root fails closed.
6. Primary and worktree exact revisions are checked without changing either.
7. Preflight performs no network request and reads no manager evidence.
8. Failure occurs before the guided prompt adapter is constructed or called.
9. Synthetic successful preflight returns only bounded public metadata.
   Mocked `launchctl` tests cover loaded/idle, running, missing, changed-argument,
   unexpected-state and inspection-failure responses.
10. The block-only safety callback is invoked after private review and before
    publication, then after publication and before engine execution. Failure at
    the first gate publishes nothing; failure at the second preserves and
    reports the verified evidence but displays no recommendation.
11. Existing Task031 end-to-end and privacy tests remain unchanged and green.
12. Full Python and frontend checks remain green without skips or weakened
    assertions.
13. Staged-sensitive-content protection passes before any commit.

## Acceptance criteria

1. A reviewed Task031 command can write draft, evidence and final decision
   artifacts to durable explicit roots without using its temporary worktree for
   storage.
2. The active Task028G monitor and the first live run cannot share or nest data
   roots.
3. No live request or private prompt can start before exact revision, plan,
   path, ignore, deadline and disk preflight succeeds.
   The same gate is repeated before publication and before engine execution.
4. A fresh preparation never overwrites or masquerades as the 9 September
   preparation.
5. Private manager facts remain interactive and absent from Git/review output.
6. Only the trusted reader can authorize result display.
7. The process remains manual, single-owner and pre-deadline.
8. The first live run can be abandoned safely at every gate.
9. The active monitor remains installed, pinned and unmodified.
10. Recovery limitations remain stated honestly: **NO VERIFIED OFFSITE BACKUP**.

## Review questions

1. Is the dedicated sandbox the minimum reliable way to use Task031 before the
   monitor releases `main`?
2. Is exposing the existing `evidence_root` port through the CLI sufficient, or
   is a broader workspace abstraction justified?
3. Does the resolved ancestry check fully protect the monitor's scheduled roots?
4. Are any monitor-owned or manager-private paths still accidentally shared?
5. Does the sequence keep public refresh authorization separate from private
   evidence publication?
6. Can any failure cause fallback to the old GW4 preparation silently?
7. Does the design preserve Task031 and engine trust authority?
8. Are the local-only recovery limitations honest?
9. What is blocking, required hardening or optional hardening before Task032B?
