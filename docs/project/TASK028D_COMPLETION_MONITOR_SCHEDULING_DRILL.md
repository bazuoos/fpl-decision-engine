# TASK028D — Offline LaunchAgent drill harness

## Status and authority

Task028D implements the independently reviewed
[Task028C design](TASK028C_SYNTHETIC_SCHEDULING_DRILL_SPEC.md). This candidate
adds an offline synthetic harness, tests and this runbook. No LaunchAgent was
loaded, no `launchctl` lifecycle command was executed, and no live FPL request,
refresh, evaluation or production-data read occurred during implementation.

The harness is inert unless an owner runs its `run` command with an explicit
execution flag. Preparing, verifying, sanitizing or rendering a property list
does not load a service. The actual temporary macOS drill remains a separate
human authorization after implementation review, commit and CI.

Task028D proves no FPL semantics and changes no production engine code. Existing
Task028B tests remain the proof for the one-shot monitor. A future synthetic pass
would prove only local scheduler mechanics. No production schedule is installed
or authorized, Task026C remains unstarted, and **NO VERIFIED OFFSITE BACKUP**.

## Tool boundary

[completion_monitor_schedule_drill.py](../../scripts/completion_monitor_schedule_drill.py)
uses only the Python standard library and has five operator-facing commands:

| Command | Effect | Runs `launchctl` |
| --- | --- | --- |
| `prepare` | Creates one private, ignored synthetic package and deterministic plist | No |
| `run` | Revalidates an existing prepared package and performs its temporary lifecycle | Yes, only with the explicit flag |
| `verify` | Recomputes the complete evidence manifest | No |
| `sanitize` | Creates a no-overwrite, path/PID/log-redacted review package | No |
| `render-production` | Renders a candidate 900-second production plist under ignored `data/` | No |

`worker` is an internal scheduled entrypoint, not an operator workflow. It reads
only validated arguments and files under one private drill root. It imports no
network client or production engine module and never reads or logs the inherited
environment.

## What automated validation covers

The offline tests create isolated synthetic Git repositories and replace the
entire service-manager lifecycle with a fake command runner. They cover:

- exact plist keys, absolute argv, `077` umask and no shell/RunAtLoad/environment;
- exact committed-script and clean tracked-tree preflight;
- explicit acknowledgement of every unrelated untracked path;
- prepared-package byte, hash, permission, symlink and no-overwrite checks;
- `WAITING`/retryable/complete and `WAITING`/review-required sequences;
- long invocation, overlap, interruption and out-of-sequence exit 4;
- initial label checks, bootstrap failure/exception, exact bootout and cleanup
  failure;
- terminal binding, invocation hash chaining, full manifest coverage and
  corruption rejection;
- inherited hostile-looking environment values having no effect on evidence;
- sanitized evidence omitting raw `launchctl` output, PIDs and absolute paths;
- static rendering of the explicit 900-second production command.

The tests never invoke `launchctl`, contact FPL, read local production `data/`,
access credentials or use sealed holdouts.

## Evidence and paths

Every prepared scenario uses an explicit ID and refuses reuse:

```text
data/operations/completion-monitor-scheduling-drills/<drill-id>-<scenario>/
```

The parent is ignored by the repository's existing `/data/` rule. The scenario
root and files are owner-only. Preparation creates:

```text
invocations/
launch-agent.plist
launch-agent.plist.sha256
metadata.json
outcome-script.json
worker.stdout.log
worker.stderr.log
```

A cleanly stopped run adds the initial exact-label absence record, exact-label
lifecycle evidence, invocation records, `result.json` and `manifest.sha256`. A cleanup failure writes
`CLEANUP_REQUIRED.json` and deliberately does not publish a manifest over files
that may still be changing.

The complete local package may contain absolute paths, a UID, PIDs and raw output
for the exact synthetic service. Keep it ignored and local. `sanitize` omits the
raw service output and worker logs, removes PIDs, replaces repository/Python/drill
paths with placeholders and binds the source manifest hash. It does not upload or
stage either package.

## Preconditions for a future actual drill

Do not run the lifecycle command until all of these gates pass:

1. Task028D implementation review reports SAFE or all required remediation is
   independently verified.
2. The exact implementation commit is on `main` and CI passes.
3. The owner separately authorizes one exact synthetic scenario and service
   label on this Mac.
4. No tracked or staged change exists. Every unrelated untracked path is named
   explicitly; unlisted paths make preflight fail.
5. The reviewed virtualenv still exists at the explicit absolute path.
6. The drill parent exists with owner-only permissions.
7. The exact drill ID has never been used.

The tool verifies the full expected commit, committed script bytes, Git ignore
coverage, repository and interpreter paths, package identity and permissions,
outcome script, exact regenerated plist bytes and its SHA-256 before lifecycle
execution.

## Future preparation procedure — does not load a service

Replace `<TASK028D_COMMIT_SHA>` with the reviewed 40-character commit. These
commands are examples for the later authorized drill; they were not run during
Task028D implementation.

```bash
mkdir -p /absolute/path/to/fpl-decision-engine/data/operations/completion-monitor-scheduling-drills
chmod 700 /absolute/path/to/fpl-decision-engine/data/operations/completion-monitor-scheduling-drills

/absolute/path/to/fpl-decision-engine/.venv/bin/python \
  /absolute/path/to/fpl-decision-engine/scripts/completion_monitor_schedule_drill.py \
  prepare \
  --repository /absolute/path/to/fpl-decision-engine \
  --expected-commit <TASK028D_COMMIT_SHA> \
  --python /absolute/path/to/fpl-decision-engine/.venv/bin/python \
  --drill-parent /absolute/path/to/fpl-decision-engine/data/operations/completion-monitor-scheduling-drills \
  --drill-id task028d-complete-001 \
  --scenario complete \
  --allow-untracked task025_claude_review_bundle.txt \
  --allow-untracked task025_review.patch
```

Preparation returns `PREPARED_NOT_LOADED` and the exact label. Inspect the
generated plist and verify its recorded hash before authorizing the lifecycle:

```bash
/usr/bin/plutil -lint /absolute/path/to/fpl-decision-engine/data/operations/completion-monitor-scheduling-drills/task028d-complete-001-complete/launch-agent.plist

shasum -a 256 /absolute/path/to/fpl-decision-engine/data/operations/completion-monitor-scheduling-drills/task028d-complete-001-complete/launch-agent.plist
```

Do not add `--allow-untracked` entries mechanically. Each path is an explicit
statement that the operator recognizes it and accepts that the harness will
ignore it. The two Task025 names above reflect current known repository state;
re-check Git status at drill time.

## Future lifecycle command — separate authorization required

This command loads a temporary LaunchAgent directly from the ignored package,
uses one exact `gui/<uid>/<label>`, kickstarts it, observes the synthetic terminal
record, boots it out and verifies a quiet window. It does not copy anything into
`~/Library/LaunchAgents`.

```bash
/absolute/path/to/fpl-decision-engine/.venv/bin/python \
  /absolute/path/to/fpl-decision-engine/scripts/completion_monitor_schedule_drill.py \
  run \
  --repository /absolute/path/to/fpl-decision-engine \
  --expected-commit <TASK028D_COMMIT_SHA> \
  --python /absolute/path/to/fpl-decision-engine/.venv/bin/python \
  --drill-root /absolute/path/to/fpl-decision-engine/data/operations/completion-monitor-scheduling-drills/task028d-complete-001-complete \
  --allow-untracked task025_claude_review_bundle.txt \
  --allow-untracked task025_review.patch \
  --execute-temporary-launch-agent
```

Run the review-required scenario later with a new ID, `--scenario review` during
preparation and its resulting distinct root. Do not reuse the complete scenario's
directory or service label.

The complete scenario is `WAITING` exit 0, a deliberately long `RETRYABLE` exit
2, then `COMPLETE` exit 0. The review scenario is `WAITING` exit 0, then
`REVIEW_REQUIRED` exit 3. Any extra invocation records `UNEXPECTED_SEQUENCE` and
exits 4. A result passes only when the expected sequence, terminal-record hash,
long-invocation duration where applicable, maximum concurrency, exact-label
cleanup and post-bootout quiet window all pass.

## Failure and cleanup

The driver attempts exact-label `bootout` after any bootstrap attempt, including
a timeout or exception whose result is uncertain. It then requires the specific
`Could not find service` response for that exact service and checks every recorded
worker PID. It never lists the full user service domain, uses `killall`, guesses a
PID, removes an inferred stale lock or touches another label.

If cleanup cannot be proven, the command fails, writes `CLEANUP_REQUIRED.json`,
prints the one exact manual command and does not create a final manifest. Run the
printed command exactly, then confirm only the named service is absent. Preserve
the package for review; do not delete or rerun it.

An ordinary SIGINT/SIGTERM enters the same cleanup path. Power loss cannot run a
finally block. After power loss, inspect the exact label before doing anything
else and use the recorded exact manual bootout command if it remains loaded.

## Verification and sanitized review package

After a clean run:

```bash
/absolute/path/to/fpl-decision-engine/.venv/bin/python \
  /absolute/path/to/fpl-decision-engine/scripts/completion_monitor_schedule_drill.py \
  verify \
  --drill-root /absolute/drill/root
```

Create a private output parent outside the repository and sanitize without
printing its local path:

```bash
mkdir -m 700 /private/tmp/task028d-sanitized

/absolute/path/to/fpl-decision-engine/.venv/bin/python \
  /absolute/path/to/fpl-decision-engine/scripts/completion_monitor_schedule_drill.py \
  sanitize \
  --drill-root /absolute/drill/root \
  --output-parent /private/tmp/task028d-sanitized
```

The output reports only `SANITIZED` and a manifest hash. Manually inspect the
sanitized bytes before sharing them with an independent reviewer. A passing hash
proves byte consistency only; it does not prove that `launchd` or the reviewer is
trusted.

## Candidate production rendering — never installation

After the synthetic drill has passed independent evidence review, the tool may
render an exact candidate production plist for a later design discussion:

```bash
/absolute/path/to/fpl-decision-engine/.venv/bin/python \
  /absolute/path/to/fpl-decision-engine/scripts/completion_monitor_schedule_drill.py \
  render-production \
  --repository /absolute/path/to/fpl-decision-engine \
  --python /absolute/path/to/fpl-decision-engine/.venv/bin/python \
  --season 2026-27 \
  --target-gameweek 4 \
  --stdout-path /absolute/path/to/fpl-decision-engine/data/operations/completion-monitor/fpl/2026-27/gameweek=4/scheduler.stdout.log \
  --stderr-path /absolute/path/to/fpl-decision-engine/data/operations/completion-monitor/fpl/2026-27/gameweek=4/scheduler.stderr.log \
  --output /absolute/path/to/fpl-decision-engine/data/operations/completion-monitor/fpl/2026-27/gameweek=4/candidate-launch-agent.plist
```

This writes an ignored, no-overwrite file and reports
`RENDERED_NOT_INSTALLED`. It neither loads nor copies the plist. Its command is
explicitly bound to one season/gameweek and uses `StartInterval=900`. An optional
prediction requires the exact `--prediction-snapshot-timestamp`; none is inferred.

## Limits

- The actual macOS lifecycle remains unexecuted until separately authorized.
- Fake-runner tests cannot prove real `launchd` behavior, permissions or timing.
- The synthetic worker does not invoke Task028B and cannot validate live network,
  refresh, realized evidence or evaluation behavior.
- `StartInterval` firings are missed during sleep and while the job is running.
  A sleeping, powered-off or disconnected Mac still delays evidence capture.
- A synthetic pass does not establish deadline RPO, continuous availability,
  production terminal-stop control or permission to install a live schedule.
- The generated production plist is a review artifact only. A production stop
  gate remains a separate design issue because Task028B exit 0 includes both
  waiting and terminal success.
- None of this tooling backs up ignored local evidence. **NO VERIFIED OFFSITE
  BACKUP** remains the recovery truth.
