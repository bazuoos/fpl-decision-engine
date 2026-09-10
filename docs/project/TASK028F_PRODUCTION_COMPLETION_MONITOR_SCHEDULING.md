# TASK028F — Production completion-monitor scheduling controller

## Status and authority

Task028F implements the offline controller and lifecycle tooling specified by
[Task028E](TASK028E_PRODUCTION_COMPLETION_MONITOR_SCHEDULING_SPEC.md). The
implementation is inert until an owner prepares one exact target after this
task is independently reviewed, committed and green in CI.

Task028F implementation and tests do not prepare a real schedule, install a
property list, execute `launchctl`, call FPL, read production `data/`, create or
change a Task028B target, or authorize activation. Task028B remains the sole
authority for public-data finalization, refresh, receipt validation and optional
evaluation. The controller owns only local scheduling state.

No production schedule is installed. Task026C has not started. Task027E remains
paused and **NO VERIFIED OFFSITE BACKUP** remains true.

## Tool boundary

[completion_monitor_production_schedule.py](../../scripts/completion_monitor_production_schedule.py)
uses the Python standard library until an active scheduled invocation passes all
preflight gates. Only then does `run` lazily import and call Task028B's public
`monitor_completion` function once.

| Command | Local effect | `launchctl` | FPL/network |
|---|---|---|---|
| `prepare` | Creates one ignored immutable plan, hashes, logs and candidate plist | No | No |
| `verify` | Revalidates plan, environment, Git state, paths, plist and local records | No | No |
| `status` | Revalidates local state and reads one exact service label | Exact-label `print` only | No |
| `run` | Performs one scheduled reconciliation after preflight | No | Task028B public requests only when active |
| `activate` | Installs, bootstraps and kickstarts one exact reviewed plist | Yes, explicit flag | The kickstarted job may call Task028B |
| `deactivate` | Boots out one exact label and explicitly removes its matching installed plist | Yes, explicit flags | No |
| `sanitize` | Creates a redacted, hashed review copy outside the repository | No | No |

Neither importing the script nor displaying command help performs any of these
operations.

## Prepared schedule layout

`prepare` creates a new owner-only schedule directory beneath an existing
owner-only parent under ignored `data/`:

```text
<schedule-root>/
  schedule-plan.json
  schedule-plan.json.sha256
  launch-agent.plist
  launch-agent.plist.sha256
  worker.stdout.log
  worker.stderr.log
  scheduler-invocations/
  lifecycle-events/
```

Every file starts owner-only. The schedule plan binds one explicit season and
gameweek, optional exact prediction timestamp, six absolute data roots, the
full reviewed commit, controller bytes, invoked and resolved Python paths,
Python version, installed-distribution inventory hash, numeric user ID, exact
LaunchAgent label, installed-plist path, acknowledged untracked paths and a
finite UTC window.

The distribution inventory is canonical sorted lower-case distribution names
and versions. Duplicate metadata entries are retained deterministically because
valid editable environments can expose them. Any later change fails closed; an
intentional dependency change requires a new reviewed plan. This detects normal
environment drift but is not byte-level dependency attestation.

The fixed interval is 900 seconds. A plan expires no later than 14 days after
creation. That bounds an unattended job while allowing a normal gameweek's
finalization and owner-review window with margin. A longer or postponed target
requires a new explicit plan review.

Preparation refuses an existing schedule ID, an unignored parent, a dirty or
wrong Git revision, changed controller bytes, an unrecognized untracked path,
an unsafe path, an invalid target or prediction, and invalid time bounds. It
does not infer a current gameweek, latest prediction or deadline.

## Generated LaunchAgent

The generated plist has exactly these keys:

```text
Label
ProgramArguments
WorkingDirectory
StandardOutPath
StandardErrorPath
Umask
StartInterval
KeepAlive
```

Its arguments invoke the absolute reviewed Python in isolated mode (`-I`), the
absolute controller, `run`, the absolute plan path and its sibling hash path.
Isolated mode prevents `PYTHONPATH` and user-site injection from changing module
resolution. The plan owns all target and policy values. The plist has
`StartInterval=900`, string umask `077` and `KeepAlive=false`. It contains no
shell, environment, `RunAtLoad`, self-unload, calendar inference, watch path or
queue directory.

The Task028D `render-production` output remains historical drill support and is
not an activation input. It calls the ambiguous Task028B CLI directly, while
Task028F consumes the typed outcome.

## Scheduled reconciliation

Every nonterminal `run` invocation performs this order:

1. Validate the owner-only canonical plan and sibling hash.
2. If either terminal-marker file exists, validate the complete marker pair and
   its referenced invocation. A valid pair returns quiescent. A partial, corrupt
   or conflicting pair stops for local review. Neither path imports the engine.
3. Validate the exact Python path, resolved interpreter, Python version and
   distribution inventory.
4. Validate all data roots, current commit, committed controller bytes, tracked
   and staged cleanliness, exact hermetic untracked set, and generated plist.
5. Require immutable successful activation evidence with no later review or
   deactivation event, plus installed-plist bytes equal to the candidate. A
   prepared but unactivated plan cannot call Task028B even if `run` is invoked
   manually.
6. Validate every existing invocation record and reject clock rollback.
7. Before `not_before`, record `ACTIVE_NOT_YET_DUE` without importing Task028B.
8. At or after expiry, record terminal review without importing Task028B.
9. Otherwise import Task028B lazily and call `monitor_completion` once with the
   plan's exact target, optional prediction and roots.
10. Publish one immutable invocation record. For terminal outcomes, then publish
   a no-overwrite terminal marker bound to that invocation.

The outcome mapping is:

| Task028B/controller observation | Scheduler result | Exit |
|---|---|---:|
| Before `not_before` | `ACTIVE_NOT_YET_DUE` | 0 |
| `WAITING` | `ACTIVE_WAITING` | 0 |
| `RetryableProbeError` or `MonitorLockedError` | `ACTIVE_RETRYABLE` | 2 |
| `REALIZED_COMPLETE` with no configured prediction and a valid realized timestamp | `TERMINAL_REALIZED_COMPLETE` | 0 |
| `COMPLETE` with a configured prediction, valid realized timestamp and contained evaluation directory | `TERMINAL_COMPLETE` | 0 |
| Task028B review, expiry, nonretryable error, unknown error, invalid payload or plan/status mismatch | `TERMINAL_REVIEW_REQUIRED` | 3 |

The controller stores only bounded error class names, not exception messages or
response bodies. It never loops, sleeps, removes a Task028B lock, resets a
monitor, changes target, selects a prediction, or implements FPL finalization or
evaluation rules.

## Terminal behavior and crashes

The terminal marker and its hash are immutable. It binds the plan, target,
outcome, invocation hash, Task028B status, realized timestamp, evaluation path
when present, and identification hashes for existing Task028B state and receipt
files. It is scheduling control, not decision or evaluation authority.

A valid terminal marker makes every later `run` engine- and network-inert even
if the checkout or Python environment later changes. A partial, malformed,
hash-invalid or conflicting marker is also network-inert and requires local
review. Scheduled terminal invocations print nothing.

If the process stops before or during Task028B, no terminal success is invented;
Task028B's own lock and reconciliation behavior controls the next attempt. If it
stops after publishing a terminal invocation but before the marker, the next
run calls Task028B once more and revalidates the result before publishing a new
terminal invocation and marker. A backward system clock blocks before Task028B.
A rollback detected after Task028B becomes terminal review.

## Repository and privacy checks

Git status is read with the global excludes file disabled. Tracked or staged
changes always fail. Untracked files must equal the plan's sorted acknowledged
set exactly; repository-ignored `data/` remains outside that set. Do not add
names mechanically when preparation reports an unexpected file.

Plan, plist, logs, invocations, terminal marker and lifecycle evidence stay
under ignored `data/`. They may contain absolute local paths, a UID and target
metadata, so they must not be staged or pasted into a review. No scheduler input
accepts an FPL cookie, manager ID, API credential or secret.

The root staged-content privacy guard remains mandatory before any repository
commit. Task028F does not make ignored operational data safe to publish.

## Status meanings

`status` makes no network request and reports one bounded state:

- `NOT_ACTIVATED`: exact label and installed plist are absent with no lifecycle
  event;
- `ACTIVE_NOT_YET_DUE`, `ACTIVE_WAITING`, or `ACTIVE_RETRYABLE`: exact label and
  installed plist are present and local state supports that active result;
- `TERMINAL_QUIESCENT`: a valid terminal marker exists while the exact label and
  installed plist remain present;
- `DEACTIVATED`: valid deactivation evidence exists and both label and plist are
  absent;
- `REVIEW_REQUIRED`: any mismatch, uncertainty, unsafe remainder or invalid
  local record exists.

An absent label with an installed plist is review-required because it may load
at a later login. A terminal marker alone never proves service removal. Status
does not validate the underlying football evidence; Task028B remains responsible
for that trust chain.

## Later Task028G preparation — inactive and now stale

After Task028F was independently reviewed, committed and green in CI, Task028G
prepared two local GW4 plans in sequence: a realized-only plan bound to
`cc30c99`, then an evaluation-enabled replacement bound to `f66fe4b`. Both plan
preparations were independently reviewed. Neither plan was installed or
activated, and both have zero invocations and lifecycle events. Later repository
commits make both plans fail the exact-revision preflight; preserve them as
immutable stale operational evidence.

A fresh replacement still requires an explicit target, prediction behavior and
window and must bind the final reviewed repository revision. The command shape
remains:

```bash
<ABSOLUTE_REPOSITORY>/.venv/bin/python -I \
  <ABSOLUTE_REPOSITORY>/scripts/completion_monitor_production_schedule.py \
  prepare \
  --repository <ABSOLUTE_REPOSITORY> \
  --expected-commit <REVIEWED_TASK028F_COMMIT> \
  --python <ABSOLUTE_REPOSITORY>/.venv/bin/python \
  --schedule-parent <ABSOLUTE_PRIVATE_IGNORED_SCHEDULE_PARENT> \
  --schedule-id <NEW_EXACT_SCHEDULE_ID> \
  --season <YYYY-YY> \
  --target-gameweek <N> \
  --raw-data-root <ABSOLUTE_RAW_ROOT> \
  --clean-data-root <ABSOLUTE_CLEAN_ROOT> \
  --feature-data-root <ABSOLUTE_FEATURE_ROOT> \
  --prediction-data-root <ABSOLUTE_PREDICTION_ROOT> \
  --evaluation-data-root <ABSOLUTE_EVALUATION_ROOT> \
  --task028b-control-data-root <ABSOLUTE_TASK028B_CONTROL_ROOT> \
  --not-before <UTC_TIMESTAMP> \
  --expires-at <UTC_TIMESTAMP> \
  --allow-untracked <ONE_EXPLICITLY_REVIEWED_PATH>
```

Omit `--prediction-snapshot-timestamp` to capture realized evidence only. Add it
only with one exact immutable pre-deadline prediction. Repeat `--allow-untracked`
only for paths individually verified at preparation time.

Preparation returns `PREPARED_NOT_INSTALLED`. Inspect the complete plan, plist
and both hashes. Rendering is not activation. A replacement plan does not
inherit authorization from either stale plan.

## Future activation — separate authorization

Actual activation remains a separate owner action after Task028G plan review.
It requires:

```text
activate
--plan <ABSOLUTE_PLAN>
--plan-sha256-file <ABSOLUTE_PLAN_HASH>
--execute-launch-agent-activation
```

The command reruns full preflight, confirms the exact label and installed path
are absent, validates the candidate plist, copies it without overwrite,
bootstraps the exact user domain, verifies the exact label and kickstarts once.
The kickstarted worker may make the authorized public Task028B requests.

After bootstrap and exact-label verification, activation publishes an immutable
active authorization event before kickstart so the new worker can pass its
authorization gate. A second active event records the successful kickstart.
Any review or deactivation event makes that plan ineligible for further engine
calls; reuse requires a new plan.

If activation fails after installation, the tool attempts an exact-label
bootout, verifies absence and removes only the unchanged plist it just installed.
A verified rollback is still recorded as review-required. If exact cleanup
cannot be proved, the plist remains and the command stops for owner review; use
the deactivation path rather than guessing a PID or touching another service.

## Future deactivation

The complete deactivation shape is:

```text
deactivate
--plan <ABSOLUTE_PLAN>
--plan-sha256-file <ABSOLUTE_PLAN_HASH>
--execute-launch-agent-deactivation
--remove-installed-plist
```

It prints, boots out and rechecks only `gui/<plan-uid>/<plan-label>`. It removes
the installed plist only after absence is proved and only if its bytes still
match the reviewed candidate. It never removes schedule state, Task028B state,
logs, receipts or evidence. Deactivation deliberately does not require the
active checkout preflight, so an exact reviewed plan remains usable to stop its
bound service after repository drift.

Operational closure requires a valid `DEACTIVATED` lifecycle event plus absence
of both the exact label and installed plist.

## Sanitized evidence

`sanitize` requires an owner-only output parent outside both the repository and
source schedule. It takes a stable before/after inventory, refuses a changing
source, and creates a new no-overwrite directory containing:

```text
schedule-plan.redacted.json
launch-agent.redacted.plist
invocations.redacted.json
terminal.redacted.json          # only when terminal exists
lifecycle.redacted.json
source-inventory.json
source-inventory-summary.json
manifest.sha256
```

The plan redacts absolute paths, UID and acknowledged untracked names. The plist
redacts all paths. Invocation and terminal copies redact evaluation paths.
Lifecycle copies redact UID and installed path and retain hashes and sizes of
bounded command output rather than raw output. The source inventory contains
only relative schedule paths, sizes and hashes, allowing the sanitized copy to
bind the local source without copying logs or private path values.

`manifest.sha256` covers every other sanitized file and rejects added, missing
or changed files. Sanitization does not upload or stage the result.

## Offline validation

The synthetic test module creates temporary Git repositories and data roots. It
uses fake monitor and exact-label service-manager runners. Coverage includes:

- every waiting, retryable, success, review, mismatch and malformed-payload
  mapping;
- pre-window, expiry, clock rollback and crash/reconciliation behavior;
- valid, partial, malformed, corrupt and conflicting terminal markers;
- proof that terminal state bypasses both engine and full preflight;
- plan, hash, permission, symlink, hardlink, path containment, plist and
  no-overwrite checks;
- current commit, controller bytes, tracked/index state, ordinary untracked,
  global-ignore and Python-inventory drift checks;
- exact plist keys and arguments, with no shell, environment or self-unload;
- exact-label activation, status, deactivation, installed-plist handling and
  failed-activation rollback through fake runners, including proof that direct
  `run` is inert before successful activation evidence;
- empty subprocess environment and bounded output;
- redaction, stable source inventory and complete sanitized manifest; and
- static proof that the only engine import is inside the lazy Task028B call and
  that the controller contains no refresh, evaluation, optimizer or journal
  implementation.

The tests never invoke real `launchctl`, contact FPL, read production `data/`,
write `~/Library/LaunchAgents`, use credentials or inspect sealed holdouts.

## Known limits

- `launchd` does not guarantee catch-up after sleep, shutdown or missed
  intervals. A local schedule provides no fixed RPO.
- One exact target is bound to one user on one Mac. There is no distributed
  lock, failover or automatic migration.
- The active working checkout is frozen. A commit, tracked edit or new
  unacknowledged untracked path blocks future active work before network access.
- Version inventory detects dependency drift but does not make the environment
  reproducible or attest package bytes.
- A terminal-quiescent LaunchAgent still starts a small network-inert process
  every interval until exact deactivation.
- There is no notification channel. The owner must inspect status.
- Local scheduling evidence is not backed up. **NO VERIFIED OFFSITE BACKUP**.
