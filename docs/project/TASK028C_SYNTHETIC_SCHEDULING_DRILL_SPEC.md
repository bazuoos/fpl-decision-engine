# TASK028C — Synthetic completion-monitor scheduling drill design

## Status and authority

This is a design-only candidate based on
`acca17b11f957048ed2a2b01518f83557a0c46dd`. It does not implement, install,
bootstrap or enable a scheduler. It authorizes no live FPL request, refresh,
evaluation, prediction, decision, journal, manager action or private-data access.

Repository code, tests, manifests and immutable artifacts remain authoritative.
Task028B's one-shot monitor remains the only implemented completion mechanism.
This document must pass independent adversarial review before commit, and any
later drill tooling and any actual `launchd` bootstrap require separate scopes.

Task028C is separate from Task026C and Task027E/F. It does not change the web
boundary or disaster-recovery state: **NO VERIFIED OFFSITE BACKUP**.

## Problem

Task028B is intentionally inert until invoked. Its runbook sketches a macOS
LaunchAgent, but a sketch does not prove that the saved Python environment,
working directory, arguments, logging, interval behavior, overlap behavior or
cleanup work under `launchd`.

The one-shot CLI also uses exit 0 for both `WAITING` and terminal success.
Therefore a scheduler cannot treat exit 0 alone as proof that capture is complete
or as a stop signal. A live schedule installed without an isolated drill could
silently use a stale interpreter, write to the wrong relative roots, keep running
after a terminal outcome, or fail only after a deadline.

The minimum trustworthy next step is a temporary synthetic scheduling drill on
the owner's Mac. It must exercise the real macOS service manager while making it
structurally impossible for the scheduled worker to contact FPL or access the
project's operational data.

## Repository and platform facts

- `python -m fpl_decision_engine monitor-completion` performs one bounded
  reconciliation for one explicit season/gameweek and exits.
- A normal probing invocation makes two small public requests. A full refresh is
  possible only after two stable observations at least 15 minutes apart.
- The monitor's local target lock prevents overlapping work for one control root
  on one host. It is not a distributed lock.
- Exit 0 covers `WAITING`, `REALIZED_COMPLETE` and `COMPLETE`; exit 2 is retryable;
  exit 3 requires review. Automation must inspect the outcome or validated state,
  not only the process exit code.
- Existing Task028B tests already cover monitor semantics, locking, restarts,
  receipts, evaluation binding and failure behavior with fake public responses.
- On this macOS host, `launchd.plist(5)` documents that `StartInterval` firings
  are missed while the system sleeps and while the prior job instance is still
  running. This is acceptable because the monitor reconciles on a later run, but
  it prevents any continuous-availability claim.
- `launchctl bootstrap`, `print`, `kickstart` and `bootout` provide explicit
  service lifecycle operations. The drill must use only its unique service label
  and must never enumerate, modify or unload unrelated services.

## Trust boundary

The drill has two deliberately separate proof layers:

```text
EXISTING OFFLINE PROOF
Task028B tests -> monitor semantics, locks, artifacts and terminal outcomes

NEW SCHEDULER PROOF
temporary LaunchAgent -> synthetic worker -> synthetic invocation ledger
    -> timing / overlap / exit / logs / terminal-stop observation
    -> exact bootout and post-drill absence check
         -X-> no FPL client, refresh, evaluator or production data roots

COMPOSITION GATE
reviewed absolute production argv + successful synthetic scheduler drill
    -> evidence for a later production-schedule decision
         -X-> no automatic production installation or authorization
```

The synthetic worker must not call `monitor_completion`, `refresh_fpl_data`,
`evaluate_xfp`, URL openers or any command that can reach live data. This is a
limitation, not a shortcut to hide: the drill proves local scheduler integration,
while the existing test suite proves monitor behavior. A successful drill does
not prove upstream availability, live refresh duration or deadline capture.

## Scope

### Included in the later drill-tooling implementation

1. A standard-library-only synthetic scheduled worker with a deterministic
   outcome script.
2. A temporary LaunchAgent property-list renderer using absolute paths and
   explicit arguments, without a shell command string.
3. A bounded driver that performs preflight, bootstraps one unique service,
   observes it, boots it out in a `finally` path and verifies its absence.
4. Offline unit tests for property-list generation, path validation, the
   synthetic state machine, overlap detection, evidence hashing and cleanup
   command construction. Unit tests must mock service-manager execution.
5. One owner-authorized real macOS drill using synthetic bytes only, after the
   implementation passes review and CI.
6. A local drill evidence package and a separately sanitized review package.
7. Rendering and static validation of a candidate 900-second production plist
   without installing or enabling it.

### Excluded

- Invoking the live completion monitor during the drill.
- Live FPL endpoints, refreshes, evaluations or operational `data/` artifacts.
- Manager IDs, cookies, credentials, private evidence or sealed holdouts.
- Installing a production LaunchAgent or writing to `~/Library/LaunchAgents`.
- Automatic selection of season, gameweek or prediction snapshot.
- Notifications, remote execution, cloud scheduling or multi-host locks.
- Decisions, journals, optimization, model changes or Task026C work.
- Automatic repair, deletion or mutation of existing project evidence.
- A claim that a synthetic pass authorizes reliance on scheduling for a deadline.

## Proposed later implementation boundary

The later implementation should be named **Task028D — Offline LaunchAgent drill
harness**. It should add only:

```text
scripts/completion_monitor_schedule_drill.py
tests/test_completion_monitor_schedule_drill.py
docs/project/TASK028D_COMPLETION_MONITOR_SCHEDULING_DRILL.md
```

The exact file split may change during implementation review, but the synthetic
worker must remain outside the production engine package and must have no network
or production-artifact dependency. Task028D may render commands and property
lists; implementation and automated tests must not execute `launchctl`. The
reviewed driver may execute it only when the human separately authorizes the
actual local drill.

No production wrapper, scheduler installation or monitor behavior change belongs
to Task028D. If the drill exposes a need for machine-readable terminal status or
a production stop gate, that becomes a separately designed engine task rather
than an unreviewed addition to the harness.

## Synthetic scenario

Use one unique drill ID and a dedicated ignored directory:

```text
data/operations/completion-monitor-scheduling-drills/<drill-id>/
```

The driver creates the directory with owner-only permissions and refuses reuse.
The synthetic worker accepts only that exact resolved drill root, drill ID and
an outcome script created by the driver. It rejects symlinks, non-regular inputs,
unexpected ownership, group/world permissions and paths outside the drill root.

The default success scenario is deterministic:

1. Invocation 1 records `WAITING` and exits 0.
2. Invocation 2 remains alive longer than one shortened drill interval, records
   `RETRYABLE` and exits 2.
3. Invocation 3 records `COMPLETE` and exits 0.
4. The driver observes the complete terminal record, boots out the exact service
   and proves that no fourth worker invocation occurs during a bounded quiet
   window.

If an invocation starts after the scripted sequence is exhausted, the worker
must append an `UNEXPECTED_SEQUENCE` anomaly record and exit promptly with
drill-only status 4. It must not crash before recording the anomaly, hang, reuse
the final scripted outcome or extend the sequence. The driver treats the record
or exit 4 as drill failure and still performs exact-label cleanup.

The long second invocation demonstrates the documented `launchd` behavior that
an interval firing while the job is running does not create an overlapping
instance. The worker also holds an exclusive synthetic lock and records any
overlap attempt as drill failure. It must never infer or delete a stale lock.

A separate review-stop scenario uses `WAITING`, then `REVIEW_REQUIRED` with exit
3. The driver must boot out the service and prove the same quiet window. No
automatic reset or retry follows a review-required terminal outcome.

Use a short drill-only interval, provisionally 10 seconds, and a bounded overall
deadline, provisionally five minutes per scenario. These values test mechanics;
they do not change the 900-second production proposal or Task028B's 15-minute
stability rule.

## LaunchAgent construction

The property list must be generated rather than hand-edited and must contain only
reviewed keys:

- a unique reverse-DNS-style `Label` containing the drill ID;
- `ProgramArguments` as an array whose first element is the resolved virtualenv
  Python executable and whose remaining elements select the synthetic script and
  exact drill arguments;
- the resolved repository root as `WorkingDirectory`;
- drill-local `StandardOutPath` and `StandardErrorPath`;
- a string `Umask` of `077`;
- drill-only `StartInterval`;
- no `RunAtLoad`; the driver uses one explicit exact-label `kickstart` after
  bootstrap for a prompt bounded drill start;
- `KeepAlive=false`;
- no credential-bearing `EnvironmentVariables`.

The worker's behavior must depend only on validated arguments and drill-local
files. It must not read `os.environ`, enumerate the inherited environment or log
any ambient value. The Python runtime may receive the ordinary `launchd` process
environment, but no inherited value is application input and no environment
content belongs in drill evidence.

Do not use a shell, `sh -c`, command substitution, globbing, PATH lookup, relative
paths, `WatchPaths`, `QueueDirectories`, `StartCalendarInterval`, `KeepAlive`
conditions, `AbandonProcessGroup` or a self-unload command. Do not set a runtime
timeout that could kill a future real refresh; the synthetic driver supplies its
own bounded observation deadline.

The driver writes the plist inside the drill directory and validates it with
`plutil -lint` before any bootstrap. The actual drill bootstraps that exact file
directly into `gui/<uid>`; it does not copy it into the persistent LaunchAgents
directory. The driver records a hash of the exact plist used.

## Preflight and authorization gates

Before a real synthetic drill, the driver must fail closed unless all of these
conditions hold:

1. The platform is macOS and required system commands resolve to the expected
   absolute system locations.
2. The repository root, reviewed HEAD and virtualenv interpreter are explicit.
3. No tracked or staged repository change exists. Named unrelated untracked files
   may remain only if recorded by the operator; the drill never reads them.
4. The synthetic script and its reviewed SHA-256 match the expected revision.
5. The drill root is absent and its parent is ignored by Git.
6. The exact generated service label is absent from `gui/<uid>`.
7. No same-drill process or lock exists.
8. The plist passes structural validation and contains no live monitor command,
   production data root, URL, manager identifier or secret-like environment key.
9. The human has explicitly authorized this one temporary synthetic bootstrap.

Preflight inspects only the exact drill label. It must not dump the user's full
`launchd` domain, process environment, shell configuration or unrelated jobs.

## Lifecycle and cleanup

The driver owns the temporary job lifecycle:

1. Create and validate the isolated drill package.
2. Record that the exact label is absent.
3. Bootstrap the exact plist into `gui/<uid>`.
4. Use `launchctl print gui/<uid>/<label>` to prove it is loaded, then
   `launchctl kickstart gui/<uid>/<label>` to start the first invocation.
5. Observe only drill-local evidence until terminal success, terminal review or
   the bounded deadline.
6. In a `finally` path, issue `launchctl bootout gui/<uid>/<label>` for that exact
   label and wait only within a documented bound.
7. Prove that `launchctl print` now reports the exact label absent and that no
   synthetic worker PID remains.
8. Preserve the drill directory and evidence for review; do not auto-delete it.

Cleanup failure makes the drill fail even if scheduled invocations succeeded.
The tool must print one exact manual `bootout` command for the owner and stop. It
must not use `killall`, wildcard process matching, guessed PIDs or deletion as a
cleanup substitute.

The driver must install signal and exception cleanup for ordinary termination.
Laptop power loss can still interrupt cleanup. The runbook must therefore begin
every retry with the exact-label absence check and end with a manual verification
step independent of the driver's success message.

## Evidence package

The ignored drill directory should contain:

```text
metadata.json
launch-agent.plist
launch-agent.plist.sha256
outcome-script.json
invocations.jsonl
worker.stdout.log
worker.stderr.log
launchctl-loaded.txt
launchctl-unloaded.txt
result.json
manifest.sha256
```

`metadata.json` binds the drill schema version, drill ID, repository commit,
sanitized macOS/Python versions, exact scenario and UTC start time. It must not
record username, email, home directory, environment variables, other service
labels or unrelated process details.

Each invocation record includes only sequence number, synthetic outcome, exit
status, PID, UTC start/end, monotonic duration, lock result and hashes chaining
it to the previous record. The final result records every assertion separately:
expected sequence, observed exit statuses, maximum concurrency, terminal stop,
quiet window, loaded-state proof, cleanup result and evidence-manifest hash.

The SHA-256 manifest covers every retained file except itself and is generated
only after cleanup verification. It proves byte integrity, not truth. No file in
this package is an FPL artifact or disaster-recovery checkpoint.

## Privacy and publication boundary

The full local package remains ignored. Before independent review, create a
sanitized package that:

- replaces the repository and home prefixes with stable placeholders;
- omits usernames, host names, UIDs, PIDs and raw `launchctl` fields unrelated to
  the exact service;
- includes the generated plist with paths redacted consistently;
- includes invocation/result records, file hashes and exact reviewed commit;
- contains no environment dump, credentials, API values or private project data.

The staged private-path and sensitive-content guards remain mandatory before any
later commit. The drill must not stage, upload or back up either package.

## Restore, sleep and availability limits

This drill does not test laptop loss, restore or offsite execution. A sleeping,
powered-off or disconnected laptop still delays a production run. `StartInterval`
does not replay every interval missed during sleep and skips a firing while the
job is already running. Task028B reconciliation makes a later invocation safe;
it does not make it timely.

The normal-awake drill should record measured schedule delays and worker runtime.
A sleep/wake experiment is optional and separately authorized because it makes
the drill slower and does not change the documented limitation. Neither result
establishes a deadline RPO or continuous availability.

## Acceptance criteria

### Task028D implementation acceptance

1. The implementation changes only the synthetic harness, its tests and runbook.
2. Importing or unit-testing the harness cannot make a network request or call
   the production monitor, refresh or evaluator.
3. The property-list renderer uses the exact allowlisted keys and absolute argv;
   adversarial tests reject shell strings, relative paths, symlinks, path escape,
   unsafe permissions, reused roots and live-monitor arguments.
4. Unit tests cover the complete and review-required scenarios, long-running
   invocation, overlap detection, timeout, bootstrap failure, worker failure,
   out-of-sequence invocation, evidence corruption, cleanup failure and
   interrupted cleanup.
5. System commands are passed as argument arrays with bounded output; no test
   actually invokes `launchctl`.
6. Tests run the worker with hostile-looking inherited environment values and
   prove that behavior/evidence are unchanged and no value is copied to output.
7. Full Python/frontend suites, contract checks, dependency boundary and
   whitespace checks pass with no private/generated data dependency.
8. Documentation includes exact preflight, owner authorization, drill, manual
   cleanup, verification and sanitization procedures.
9. Independent adversarial review reports no blocker before commit; CI then
   passes the exact committed revision.

### Actual synthetic drill acceptance

1. The owner authorizes one exact reviewed commit and one unique temporary label.
2. Preflight passes without reading unrelated services or private data.
3. Both deterministic scenarios produce the expected ordered invocations and
   exit statuses.
4. Maximum observed synthetic concurrency is one, including the deliberately
   long invocation.
5. Terminal completion and terminal review both stop further scheduled workers
   within the bounded observation window.
6. Cleanup succeeds on both the normal path and an intentionally interrupted
   synthetic run; the exact labels are independently confirmed absent.
7. The full evidence manifest verifies, and a manually inspected sanitized
   package contains no private identifier or credential.
8. Independent review confirms the evidence supports only scheduler mechanics,
   with all stated live-operation limitations preserved.

## Failure behavior

| Condition | Required result |
| --- | --- |
| Wrong platform, revision, interpreter or path | Refuse before bootstrap |
| Existing exact label or drill root | Refuse; never adopt or overwrite it |
| Plist validation or hash failure | Refuse before bootstrap |
| Synthetic lock already present | Stop for inspection; never infer staleness |
| Invocation exceeds scripted sequence | Record `UNEXPECTED_SEQUENCE`, exit 4 and clean up |
| Unexpected invocation or exit sequence | Drill failure; boot out exact label |
| Overlap observed | Drill failure; retain evidence and boot out |
| Terminal record missing by deadline | Timeout failure; boot out |
| Worker or log path escapes drill root | Fail closed; do not bootstrap |
| `launchctl` bootstrap/print failure | Record bounded diagnostics; attempt exact cleanup |
| Bootout or absence verification failure | Drill failure; provide exact manual cleanup command |
| Laptop sleeps or powers off | No success claim; preflight exact label before retry |
| Evidence hash mismatch | Preserve bytes; mark review required; never rewrite result |

## Proposed delivery sequence

1. Independently review this Task028C design against Task028B and the host's
   documented `launchd` behavior.
2. Remediate any required design changes and repeat review where substantive.
3. Commit the reviewed design only after human approval and passing CI.
4. Human separately authorizes Task028D implementation of the offline harness,
   unit tests and runbook; no scheduler is loaded.
5. Claude independently reviews the complete Task028D candidate; Codex
   remediates blockers; CI verifies the committed revision.
6. Human separately authorizes one temporary synthetic `launchd` drill.
7. Run both scenarios, clean up exact labels, sanitize evidence and obtain
   independent evidence review.
8. Only then decide whether to design a production schedule. No successful
   synthetic drill automatically installs or authorizes one.
