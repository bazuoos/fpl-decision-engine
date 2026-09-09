# TASK028B — Official-data completion monitor

## Status and safety boundary

Task028B implements the independently reviewed
[Task028A design](TASK028A_OFFICIAL_DATA_COMPLETION_MONITOR_SPEC.md). The command
is one-shot and inert until an operator or separately configured scheduler runs
it. This task does not install or enable a schedule and no live FPL request was
used for implementation or tests.

The monitor can collect public official data and optionally evaluate an explicit
frozen xFP v0.1 snapshot. It cannot create features, predictions, optimizations,
manager evidence, decisions, journals or FPL actions. It accepts no login cookie,
manager ID, API key or other credential.

Task028B does not change Task027 recovery status: **NO VERIFIED OFFSITE BACKUP**.
Outputs remain ignored local data until a separately authorized backup workflow
captures them.

## Commands

Run one bounded reconciliation for one exact target:

```bash
python -m fpl_decision_engine monitor-completion \
  --season 2026-27 \
  --target-gameweek 4
```

This makes two small in-memory public requests while waiting. It creates a full
official refresh only after the same finalized target semantics have been seen
twice at least 15 minutes apart. The process exits after one reconciliation; it
does not contain a polling loop.

Evaluation is opt-in and requires an exact immutable pre-deadline prediction:

```bash
python -m fpl_decision_engine monitor-completion \
  --season 2026-27 \
  --target-gameweek 4 \
  --prediction-snapshot-timestamp 20260910T061943.538960Z
```

The timestamp is never inferred and another prediction is never substituted.
The accepted realized snapshot is also passed explicitly to the existing
leakage-safe evaluator.

Custom data roots are available for isolated operation and tests:

```text
--raw-data-root
--clean-data-root
--feature-data-root
--prediction-data-root
--evaluation-data-root
--control-data-root
```

The working directory matters when relative roots are used. A scheduler must set
it to the repository root.

## Outcomes and exit status

| Outcome | Meaning | Exit |
| --- | --- | --- |
| `WAITING` | Target is not finalized or only the first stable observation exists | 0 |
| `REALIZED_COMPLETE` | A validated finalized refresh is accepted; evaluation was not requested | 0 |
| `COMPLETE` | Realized refresh and explicitly requested evaluation are accepted | 0 |
| Retryable probe/lock | Temporary network/5xx request failed after bounded retries, or another local invocation owns the target | 2 |
| `REVIEW_REQUIRED` | Identity, artifact, receipt, refresh or evaluation safety check failed | 3 |

Waiting is normal. Exit 0 does not always mean capture has completed; automation
must also read the logged outcome or validated control state. A scheduler should
stop invoking a target after `REALIZED_COMPLETE`, `COMPLETE` or
`REVIEW_REQUIRED`.

## Trusted validation chain

`evaluation.validate_realized_gameweek_snapshot` is the single public
finalization boundary used by both the monitor and evaluator. It requires:

- exact target event identity;
- `finished=true` and `data_checked=true` on the event;
- every target fixture and player-history row to carry final flags;
- realized fixture/history retrieval after the event deadline.

`refresh.validate_completed_refresh_snapshot` separately revalidates the
completed refresh identity, manifest paths, SHA-256 values, raw response
manifests, exact player-history response set, clean provenance and row counts.
Inputs are hashed again after validation to detect changes during the read.

An evaluation manifest now records SHA-256 values for all five output Parquet
files as well as its existing exact input bindings. Monitor adoption validates
both input and output hashes. An older evaluation without output-hash metadata
is not silently adopted by this monitor.

## Probe semantics

Probe response bodies remain in memory and are never published as raw snapshots.
Control state stores only observation times, deadline, completion booleans,
fixture counts and SHA-256 values.

The stable semantic digest contains exactly:

```text
event: id, deadline_time, finished, data_checked
fixtures (integer fixture-ID order): id, event, team_h, team_a, finished
```

It uses compact JSON with sorted keys and JSON booleans, UTF-8 encoding and
lowercase SHA-256 hex. Full response hashes remain visible for audit, but an
unrelated player-field change does not reset readiness.

Temporary network and 5xx failures retain a valid first observation and return
the retryable exit. Redirects, TLS verification failures, invalid JSON and other
non-5xx responses fail closed into `REVIEW_REQUIRED` without refreshing.

## Local control state

The default target directory is:

```text
data/operations/completion-monitor/fpl/<season>/gameweek=<N>/
```

It may contain:

```text
state.json
realized-receipt.json
evaluation-receipt.json
review-history/<timestamp>.json
.completion-monitor.lock
```

`state.json` is mutable execution control, not football evidence. Receipts and
review-history entries publish with no-overwrite semantics. A receipt binds the
exact target, artifact identity and manifest hash. Every invocation revalidates
the receipt and referenced artifacts before reporting success.

Writes use file-fsync and atomic replacement or exclusive hard-link publication.
The containing directory is not fsynced, so this is not a full sudden-power-loss
durability guarantee. Restart reconciliation adopts the deterministic earliest
valid finalized refresh or evaluation when publication completed before the
corresponding receipt.

## Locks and interrupted refreshes

The target lock prevents two processes sharing this control directory on one
authorized host from creating distinct refreshes. It is not a distributed lock.
Each season/gameweek target must have exactly one authorized host; moving an
active target or monitoring it from multiple hosts is unsupported.

A handled completion-monitor exit removes its lock. A hard termination may leave
the lock behind. Inspect its PID and confirm that no monitor is running before:

```bash
python -m fpl_decision_engine monitor-completion-unlock \
  --season 2026-27 \
  --target-gameweek 4
```

Unlock removes only that target's monitor lock. It never infers staleness from
age and never kills a process.

The monitor records `REFRESHING` before calling the existing refresh. If an
attempt fails or an earlier `REFRESHING` state has no valid completed artifact,
the target enters `REVIEW_REQUIRED`. It does not automatically resume or launch
another full refresh. The operator must inspect any refresh manifest and use the
existing explicit refresh-resume workflow where appropriate.

## Review and reset

After resolving the recorded cause, archive and reset only the failed scope:

```bash
python -m fpl_decision_engine monitor-completion-reset \
  --season 2026-27 \
  --target-gameweek 4 \
  --reason "Verified upstream data is now coherent"
```

The reason is required and stored locally. Before changing mutable state, reset
writes an immutable no-overwrite review-history record containing the prior
state, failure, hashes, referenced snapshot and reset time.

- A realized-capture reset is allowed only without a realized receipt.
- An evaluation reset revalidates and retains the realized receipt and its
  artifacts, and is allowed only without an evaluation receipt.
- Receipt-integrity failures cannot be reset by this command.

Reset never deletes or rewrites a snapshot, receipt or earlier review record.
This distinction clarifies the original Task028A wording: an optional evaluation
can be retried after review without discarding an already accepted realized
snapshot.

## Evaluation deduplication

Before evaluating, the monitor scans only the exact season/gameweek/model path
for manifests bound to the supplied prediction and accepted realized snapshot.
The deterministic earliest valid evaluation is adopted. A matching but corrupt
candidate stops for review rather than causing a duplicate.

Changing or omitting the requested prediction after an evaluation receipt
exists is an identity conflict. Missing predictions, post-deadline provenance
or evaluator failures cannot trigger prediction generation or substitution.

## Proposed macOS schedule — not installed

After Task028B implementation review, commit and a manual synthetic drill, an
owner may separately authorize a LaunchAgent that invokes the command every 900
seconds. Its property list should use:

```xml
<key>WorkingDirectory</key>
<string>/absolute/path/to/fpl-decision-engine</string>
<key>ProgramArguments</key>
<array>
  <string>/absolute/path/to/fpl-decision-engine/.venv/bin/python</string>
  <string>-m</string>
  <string>fpl_decision_engine</string>
  <string>monitor-completion</string>
  <string>--season</string>
  <string>2026-27</string>
  <string>--target-gameweek</string>
  <string>4</string>
</array>
<key>StartInterval</key>
<integer>900</integer>
```

Use target-specific local log paths under ignored `data/operations/`; do not log
response bodies or credentials. The example is incomplete by design and has not
been written to `~/Library/LaunchAgents` or loaded. Scheduling needs a separate
owner action for each exact target and must be disabled after a terminal outcome.

A sleeping, powered-off or network-disconnected Mac delays capture. With a
15-minute invocation interval and two observations separated by at least 15
minutes, full refresh normally starts about 15–30 minutes after finalized state
first becomes observable, plus refresh runtime. Task028B does not claim
continuous availability or remote execution.

## Validation

Tests use temporary roots, fake public responses and synthetic artifacts only.
They cover probe gates and malformed payloads, stable-observation timing,
retry preservation, changed semantics, single-host locking, explicit unlock,
refresh/adoption deduplication, interrupted and nonfinal refresh stops, manifest
and receipt tampering, explicit evaluation binding/output hashes, review archive
and reset, and CLI exposure. They do not access local production `data/`, live
FPL endpoints, manager state, credentials or sealed holdouts.
