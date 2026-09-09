# TASK028E — Production completion-monitor scheduling and terminal-stop design

## Status and authority

This is a design-only candidate based on
`2187c4074d5705aced46f2516a8097adc4e280b`.

It does not implement, install, bootstrap or enable a production scheduler and
does not authorize a live FPL request. Repository code, tests, contracts,
manifests and immutable artifacts remain authoritative. Task028B remains the
only implemented production completion mechanism, and it remains one-shot.

Task028E follows the independently reviewed
[Task028C design](TASK028C_SYNTHETIC_SCHEDULING_DRILL_SPEC.md) and
[Task028D synthetic scheduling drill](TASK028D_COMPLETION_MONITOR_SCHEDULING_DRILL.md).
That drill proved local `launchd` mechanics on one Mac with synthetic bytes. It
did not run Task028B, validate live network or refresh behavior, establish
deadline availability or authorize a production schedule.

This task is separate from Task026C and Task027E/F. It changes no web boundary,
decision semantics, manager evidence, model, recovery state or backup claim.
**NO VERIFIED OFFSITE BACKUP** remains true.

## Problem

Task028B exposes one bounded reconciliation for one explicit season/gameweek.
Its exit 0 deliberately includes three different outcomes:

- `WAITING`: keep checking later;
- `REALIZED_COMPLETE`: finalized public evidence is accepted without evaluation;
- `COMPLETE`: finalized public evidence and the explicitly requested evaluation
  are accepted.

Exit 3 represents `REVIEW_REQUIRED`, while temporary probe and target-lock
failures use exit 2. A LaunchAgent that invokes only the CLI cannot safely infer
whether exit 0 means wait or stop. Parsing human-readable logs would create a
second, unstable status contract.

The synthetic drill also showed that a `StartInterval` job can prevent overlap,
but `launchd` misses interval firings during sleep and while a prior invocation
is running. A production design must therefore preserve Task028B's restart-safe
reconciliation, stop further network work after a terminal outcome, expose the
state to the owner, and avoid claiming continuous availability.

## Repository-established inputs

- [`completion_monitor.monitor_completion`](../../src/fpl_decision_engine/completion_monitor.py)
  returns a typed `MonitorOutcome` with an exact status, detail, realized
  snapshot timestamp and optional evaluation directory.
- Task028B already owns completion probes, target locking, immutable receipt
  publication, restart adoption, realized validation and evaluation binding.
- `WAITING`, `REALIZED_COMPLETE`, `COMPLETE` and `REVIEW_REQUIRED` are explicit
  engine constants. `RetryableProbeError` and `MonitorLockedError` are the two
  retryable exceptions used by the CLI.
- A valid realized or evaluation receipt is revalidated before Task028B reports
  terminal success. Existing `REVIEW_REQUIRED` state returns without probing.
- The Task028D renderer can produce an exact-target 900-second plist, but it
  currently invokes the ambiguous CLI directly. It is a review artifact, not an
  approved production interface.
- The Task028D local drill passed complete and review-required scenarios with
  maximum concurrency one and exact-label cleanup. It used no production data.

## Decision summary

The later production implementation should add an orchestration controller
outside `src/fpl_decision_engine`. The controller may call the public Task028B
function and consume `MonitorOutcome`; it must not reproduce any FPL readiness,
refresh, evaluation, receipt or decision rule.

```text
generated immutable schedule plan + exact SHA-256
        |
        v
LaunchAgent every 900 seconds -> production schedule controller
        |                         |
        |                         +-> terminal marker already exists
        |                               -> validate marker -> QUIESCENT
        |                               -X-> no engine call or network
        |
        +-> no terminal marker -> Task028B monitor_completion once
                                  |
                +-----------------+-------------------+
                |                 |                   |
             WAITING          RETRYABLE        terminal outcome
                |                 |                   |
          record invocation  record invocation   publish immutable
          remain active      remain active       terminal marker
                                                    |
                                                    v
                                      future invocations are quiescent
                                                    |
                                      owner status -> exact deactivate
                                                    |
                                      prove exact label absent

        -X-> no log parsing, no inferred target, no auto-unlock/reset
        -X-> no prediction generation, decision, journal or manager action
```

“Terminal quiescence” has a precise meaning: the LaunchAgent may remain loaded
temporarily, but every later invocation exits before calling Task028B or opening
a network path. The project must not describe the schedule as removed until a
separate exact-label deactivation succeeds and absence is verified.

The scheduled worker must not unload itself. A job cannot reliably finish its
own evidence, unload itself and then prove its absence. The owner-facing
deactivation operation performs that lifecycle from outside the scheduled job.

## Scope

### Included in a later implementation

1. An immutable, exact-target production schedule plan and hash.
2. A standard-library controller outside the engine package.
3. Direct consumption of Task028B's typed outcome without parsing CLI logs.
4. One immutable invocation record per scheduled attempt.
5. A no-overwrite terminal marker that makes future attempts network-inert.
6. Read-only `verify` and `status` operations.
7. Exact-label activation and deactivation commands behind explicit flags.
8. Generated LaunchAgent plists with absolute reviewed arguments.
9. Offline tests with fake monitor and service-manager seams.
10. A runbook and sanitized activation/deactivation evidence format.

### Excluded

- Any activation during implementation or automated tests.
- A live completion-monitor request or production evidence capture.
- Automatic season, gameweek or prediction-snapshot selection.
- Automatic target migration between hosts.
- Automatic monitor reset, lock removal, artifact repair or deletion.
- Manager IDs, FPL cookies, credentials, decisions or journals.
- Notifications, dashboards, web routes or Task026C work.
- Cloud execution, queues, distributed locks or multi-host scheduling.
- Changes to model, optimizer, reliability or experiment behavior.
- A claim of continuous availability, guaranteed deadline RPO or backup.

## Immutable schedule plan

Preparation must create one owner-only, no-overwrite plan under the ignored
target control directory. The plan is data for orchestration, not football
evidence and not decision authority. It must contain exactly:

- schema/version identifier;
- explicit season and integer gameweek;
- optional exact prediction snapshot timestamp, absent by default;
- absolute raw, clean, feature, prediction, evaluation and control roots;
- absolute repository and virtualenv Python paths;
- expected full Git commit and SHA-256 of the controller source;
- exact Python version and SHA-256 of a canonical sorted installed-distribution
  name/version inventory, with no filesystem paths in that inventory;
- exact LaunchAgent label;
- exact numeric user ID and absolute installed-plist path;
- fixed interval of 900 seconds;
- UTC `not_before` and `expires_at` bounds;
- the explicitly acknowledged untracked-path set used by code preflight;
- creation time.

The canonical plan file has a no-overwrite sibling SHA-256 file. The digest is
not embedded in the bytes it hashes. Both timestamps must be timezone-aware UTC,
`not_before` must precede `expires_at`, and `expires_at` must be after creation
and no more than 14 days later. A target that needs a longer window requires a
new reviewed plan rather than an indefinitely live job.

The plan must not contain credentials, environment values, manager identifiers,
host serial numbers or mutable “latest” references. Its target, roots, commit,
prediction choice and interval cannot be edited after creation. A changed plan
requires a new ID, a newly rendered plist and separate review.

The UID, absolute paths and explicit owner activation bind the plan to one local
user domain operationally; they are not hardware attestation. Sanitized evidence
must remove the UID and paths. Moving the target to another Mac remains a human
gate requiring verified deactivation of the old source.

`not_before` may be before the gameweek deadline so Task028B can record waiting.
`expires_at` is a safety bound, not a completion prediction. Reaching it without
a terminal Task028B outcome must publish terminal
`SCHEDULER_REVIEW_REQUIRED` and quiesce; it must not infer that a lock is stale,
reset Task028B or start another target.

## Controller outcome mapping

The controller calls `monitor_completion` at most once per invocation. It owns
only scheduling state and uses this exact mapping:

| Task028B result | Schedule result | Further Task028B/network calls |
|---|---|---|
| Current UTC time is before `not_before` | `ACTIVE_NOT_YET_DUE` | Allowed on the next interval; no Task028B call now |
| `WAITING` | `ACTIVE_WAITING` | Allowed on the next interval |
| `RetryableProbeError` | `ACTIVE_RETRYABLE` | Allowed on the next interval |
| `MonitorLockedError` | `ACTIVE_RETRYABLE` | Allowed on the next interval; never remove the lock |
| `REALIZED_COMPLETE` with no prediction in the plan | `TERMINAL_REALIZED_COMPLETE` | Forbidden |
| `COMPLETE` with the exact prediction in the plan | `TERMINAL_COMPLETE` | Forbidden |
| `REVIEW_REQUIRED` | `TERMINAL_REVIEW_REQUIRED` | Forbidden |
| Known non-retryable `CompletionMonitorError` | `TERMINAL_REVIEW_REQUIRED` | Forbidden |
| Unknown exception, outcome or plan/status mismatch | `TERMINAL_REVIEW_REQUIRED` | Forbidden |
| Plan expired before terminal completion | `TERMINAL_REVIEW_REQUIRED` | Forbidden |

`COMPLETE` without a configured prediction and `REALIZED_COMPLETE` with a
configured prediction are mismatches. They fail closed into schedule review
rather than being treated as success.

The controller must not catch a retryable outcome and loop, sleep or immediately
rerun. `launchd` supplies the next interval. Task028B retains its own bounded
request retry behavior.

## Terminal marker and restart behavior

The terminal marker is canonical JSON published with file-fsync, exclusive
no-overwrite creation and a sibling SHA-256 file. It binds:

- plan SHA-256 and exact target;
- terminal schedule status;
- exact Task028B status or bounded error class;
- invocation ID and UTC observation time;
- realized snapshot timestamp when supplied by Task028B;
- evaluation directory when supplied by Task028B;
- SHA-256 values of any existing Task028B state and receipt files used only for
  identification, without treating the schedule marker as validation authority.

If a valid terminal marker exists, the controller validates its bytes and plan
binding, writes no new invocation record, calls no engine function and exits
quiescent. A present but partial, malformed, conflicting or hash-invalid marker
also forbids an engine or network call and reports local review required. It must
never overwrite or delete the marker.

If Task028B reaches terminal state but the controller crashes before publishing
the marker, the next scheduled invocation calls Task028B once. Task028B's
existing reconciliation revalidates or reports the terminal state, after which
the controller publishes the marker. Missing marker bytes never justify
synthesizing a success without that call.

The marker stops future automated work; it does not become trusted evidence for
model evaluation or decisions. Consumers still validate Task028B receipts and
referenced artifacts through the existing engine path.

## Invocation evidence

Each non-quiescent attempt creates one no-overwrite canonical JSON record under
an owner-only `scheduler-invocations/` directory. The record includes the plan
hash, exact target, start/end UTC times, schedule result, Task028B status or
bounded exception class, process exit class and hashes of relevant scheduler
state. It must not contain public response bodies, full exception tracebacks,
environment values, credentials or manager data.

The controller compares its current UTC clock with the latest valid invocation.
A rollback before that recorded start time blocks Task028B and network access
and reports local review required; it does not rewrite history or guess whether
the schedule bounds have passed. The clock remains the Mac's local system clock,
not an independently attested time source.

An invocation record is audit support, not proof that upstream data is correct.
No automatic truncation or deletion belongs to the active schedule. The later
runbook should estimate growth, retain all records through deactivation review
and treat archival/backup as a separate owner-authorized operation.

## Code and repository preflight

Every active invocation must fail closed before Task028B unless:

1. The plan and its SHA-256 are valid and owner-only.
2. The exact expected commit is current.
3. The controller bytes match the committed file at that revision.
4. The running Python version and canonical installed-distribution inventory
   match the plan. This detects ordinary environment drift without claiming
   byte-level dependency provenance.
5. No tracked or staged repository change exists.
6. The hermetic Git status contains exactly the plan's acknowledged unrelated
   untracked paths; ignored `data/` remains outside that set.
7. Repository, interpreter and all data roots resolve to the exact absolute
   locations in the plan, without symlink redirection.
8. The LaunchAgent label, plan target and plist target agree exactly.

The controller must use only the Python standard library through this preflight
and import Task028B lazily afterward. Importing the engine before the environment,
code and repository gates pass would make the preflight ineffective.

A plan/hash, code or repository preflight failure occurs before any engine or
network call. Because a corrupt plan cannot safely name a destination, the
controller need not publish a terminal marker for this class of failure. It exits
with a bounded local review error on every later launch until the owner repairs
or deactivates the exact reviewed job. It must not write through an untrusted
path merely to make the failure appear terminal.

This deliberately freezes the working checkout while a production target is
active. A new commit, tracked edit or unreviewed untracked path makes the
schedule block in review before network access. For a solo project this is
the minimum reliable defense against an editable install silently changing
beneath `launchd`. A future dedicated checkout may reduce this operational
constraint, but is not required for the first implementation.

## LaunchAgent design

The production plist must be generated from the immutable plan. It contains only:

- exact target-derived `Label`;
- absolute `ProgramArguments` invoking the controller and exact plan/hash;
- absolute repository `WorkingDirectory`;
- target-local `StandardOutPath` and `StandardErrorPath`;
- string `Umask` of `077`;
- integer `StartInterval` of 900;
- boolean `KeepAlive=false`.

It contains no shell, `RunAtLoad`, environment variables, `WatchPaths`,
`QueueDirectories`, calendar inference or self-unload command. Activation uses
one explicit exact-label kickstart after bootstrap. The controller and plan,
rather than process exit 0, determine whether later work is allowed.

Its `ProgramArguments` must name the absolute reviewed Python interpreter, the
absolute committed controller, a scheduled-worker subcommand, the absolute plan
file and its sibling hash file. No target, root or policy value is repeated as a
mutable plist argument. A normal scheduled invocation, including a
terminal-quiescent one, emits nothing to stdout or stderr; bounded local error
output is reserved for failures requiring owner review.

The current Task028D production renderer must not be used unchanged for
activation because it invokes `monitor-completion` directly. A later
implementation must update or replace that renderer so the reviewed plist calls
the production controller.

## Activation and deactivation boundary

Implementation and automated tests must not execute `launchctl` or write to
`~/Library/LaunchAgents`. After implementation review, commit and CI, actual
activation is a separate owner-authorized task for one exact plan.

Activation must:

1. verify current commit, tracked/index state, acknowledged untracked paths,
   plan, controller, interpreter, directories and plist;
2. prove the exact label is absent without enumerating unrelated services;
3. copy the reviewed plist with no-overwrite semantics to its exact owner-only
   LaunchAgents path;
4. bootstrap and inspect only the exact label;
5. kickstart once and record sanitized activation evidence;
6. leave all other labels and files untouched.

Deactivation must be safe from any state. It issues `bootout` only for the exact
plan-bound label and verifies the specific absent response. Removing the exact
plan-bound installed plist is a distinct, explicit option allowed only after
absence is proved; it never deletes control state, receipts, logs or evidence.
Operational deactivation is complete only when both the exact label and the
installed plist are absent. An absent label with its plist still installed is
`REVIEW_REQUIRED`, because a later login could load it again.

A terminal marker makes future launches inert, but operational closure requires
the owner to run exact-label deactivation and verify absence. If power loss or a
tool failure makes state uncertain, the runbook must show the one exact command
for that label and stop rather than guess a PID or enumerate the service domain.

## Status operation

`status` is read-only and performs no network call. It validates the plan,
terminal marker, latest invocation record, expected plist and exact label. It
reports one bounded state:

- `NOT_ACTIVATED`;
- `ACTIVE_NOT_YET_DUE`;
- `ACTIVE_WAITING`;
- `ACTIVE_RETRYABLE`;
- `TERMINAL_QUIESCENT`;
- `DEACTIVATED`;
- `REVIEW_REQUIRED`.

It must distinguish “terminal marker exists” from “LaunchAgent is absent.” It
must report `DEACTIVATED` only when deactivation evidence is valid and both the
exact label and installed plist are absent. It must not claim Task028B evidence
remains valid merely from scheduler files; the trusted engine validation is
separate.

## Sleep, outage and single-host limits

`StartInterval` does not provide catch-up guarantees. Sleep, shutdown, lost
power, network loss or failure to log in can delay capture. The next permitted
invocation reconciles safely, but this design cannot promise a fixed RPO.

One active target is bound to one Mac and one exact label. Moving it requires
verified deactivation on the old host before a new plan is authorized. This task
adds no distributed lock or shared artifact store. Loss of the laptop remains a
loss-of-execution event, and ignored evidence remains unprotected until Task027F.

## Security and privacy

The scheduler accepts no FPL login, cookie, API key, entry ID or manager
evidence. Task028B contacts only public official endpoints. The controller must
not read or log the ambient environment, and service-manager commands use
absolute executables, bounded output and an empty subprocess environment.

Plan, plist, logs, invocation records, terminal markers and activation evidence
remain owner-only and ignored under `data/`. Sanitized review evidence removes
absolute paths, UID/PID values and raw service-manager output and binds the full
local manifest by hash. Sanitization does not upload or stage anything.

Root privacy guards remain mandatory before every Git commit. Scheduler output
must never be added to Git to make this design auditable.

## Failure behavior

| Condition | Required behavior |
|---|---|
| Before plan `not_before` | Record `ACTIVE_NOT_YET_DUE`; no Task028B/network call |
| Waiting for finalization | Record `ACTIVE_WAITING`; return until next interval |
| Temporary network/5xx failure | Record `ACTIVE_RETRYABLE`; no immediate retry |
| Target lock held | Record retryable; never infer stale or unlock |
| Invalid plan, code or repository state | No Task028B/network call; bounded local review error without trusting plan paths |
| Python version or distribution inventory drift | No engine import or network call; bounded local review error |
| Clock moves behind latest valid invocation | No Task028B/network call; bounded local review error |
| Task028B review outcome/error | Publish terminal review marker; future invocations quiescent |
| Successful realized/evaluation result | Publish matching terminal marker; future invocations quiescent |
| Crash before Task028B call | No false record; next interval may retry |
| Crash during Task028B | Existing Task028B reconciliation governs the next invocation |
| Crash after Task028B terminal result but before marker | Next invocation re-enters Task028B once and revalidates before publication |
| Partial/corrupt terminal marker | No engine/network call; local review required |
| Plan expiry | Terminal review; no reset, target change or continued probe |
| Laptop sleeps or is offline | Invocation is delayed or retryable; no availability claim |
| Label absent but installed plist remains | Report review required; do not claim deactivation |
| Activation/deactivation uncertainty | Inspect only exact label; stop for owner review |

## Offline validation required for Task028F

Tests must inject the Task028B caller, clock, Git runner and service-manager
runner. They use synthetic temporary directories and must cover at least:

- all outcome/exception combinations in the mapping table, including mismatched
  success status versus prediction configuration;
- pre-`not_before`, exact-boundary and expired plans, plus clock rollback relative
  to the latest valid invocation;
- valid, partial, malformed, conflicting and hash-corrupt terminal markers;
- a crash before Task028B, during Task028B and after a terminal return but before
  marker publication, proving the next invocation reconciles safely;
- proof that a valid terminal marker causes zero Task028B and network-capable
  calls on every later invocation;
- owner/mode, symlink, hardlink, path-containment and no-overwrite rejection for
  plans, markers, invocation records, plists and installed paths;
- exact expected commit, committed controller bytes, tracked/index cleanliness
  and hermetic untracked-path equality, including files hidden only by a global
  excludes file;
- exact Python-version and canonical distribution-inventory drift, plus proof
  that the engine import occurs only after all preflight gates pass;
- exact plist keys, absolute argv, plan/hash binding, 900-second interval,
  `077` umask and absence of shell, environment and self-unload behavior;
- exact-label activation, already-loaded conflicts, bootstrap uncertainty,
  deactivation, absence checks and installed-plist removal as separate actions;
- bounded command output, empty subprocess environments, signals and cleanup;
- sanitized evidence coverage and removal of paths, UID/PID values, raw service-
  manager output and environment content; and
- static/import assertions that the controller delegates to Task028B and contains
  no FPL readiness, refresh, evaluation, decision or journal implementation.

Automated tests must never execute the real `launchctl`, contact FPL, read local
production `data/`, install a plist, access credentials or use sealed holdouts.

## Proposed later implementation boundary

The later implementation should be **Task028F — Production scheduling
controller and offline lifecycle tooling**. Proposed files are:

```text
scripts/completion_monitor_production_schedule.py
tests/test_completion_monitor_production_schedule.py
docs/project/TASK028F_PRODUCTION_COMPLETION_MONITOR_SCHEDULING.md
```

The new Task028F tool must render the controller-based production plist itself.
The Task028D tool and tests remain historical synthetic-drill artifacts and are
not production activation inputs. Task028F must not change Task028B semantics,
install a plist, execute `launchctl` in implementation/tests or make a live
request.

After Task028F passes independent review, commit and CI, **Task028G** may prepare
one exact production plan and candidate plist. The owner must review the target,
prediction choice, expiry, code revision and local availability limits before
separately authorizing activation. Rendering or preparation is not activation.

## Acceptance criteria for Task028E

The design is complete only if independent review confirms that it:

1. consumes typed Task028B outcomes without log parsing or duplicated FPL rules;
2. distinguishes waiting, retryable, success and review states exactly;
3. makes every post-terminal invocation engine- and network-inert;
4. honestly distinguishes quiescence from verified LaunchAgent removal;
5. fails closed on plan, code, repository, marker or status mismatch;
6. preserves Task028B restart, locking, receipt and explicit-target guarantees;
7. binds one immutable plan to one host label and exact code revision;
8. provides exact-label activation, status and deactivation boundaries;
9. addresses sleep, outage, expiry, crash and partial-publication behavior;
10. keeps scheduler state outside decision authority and private evidence outside
    Git;
11. authorizes no implementation, live request or production activation; and
12. leaves Task026C, model behavior, manager evidence and recovery status
    unchanged.

## Known limits

- A loaded terminal-quiescent LaunchAgent still starts a small controller process
  every interval until exact deactivation; it performs no engine or network work.
- The first implementation freezes the active working checkout. Ordinary project
  development must wait or use a separately designed dedicated checkout.
- A local schedule cannot run while the Mac is unavailable and cannot replace an
  offsite service.
- Python dependencies are not lockfile-pinned, so the exact local environment is
  version-inventory checked but not byte-for-byte reproducible or supply-chain
  attested.
- No notification tells the owner that review or deactivation is required.
- No scheduler evidence backs itself up. **NO VERIFIED OFFSITE BACKUP**.
