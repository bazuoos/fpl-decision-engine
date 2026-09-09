# TASK028A — Official-data completion monitor design

## Status and authority

This design passed independent review and was committed as `be7baf6`. The later
Task028B implementation must still pass its own review and CI. This document is
not a scheduler installation or authorization to make a network request.
Repository code, tests, schemas, immutable artifacts and manifests remain
authoritative.

Design base: `9eae70a213ac49761ad57cec2179cf19feaf06e4`.

Task028A is separate from Task026C and Task027E. It must not add web writes,
authentication, manager actions, decisions, journals, model changes, holdout
access, credentials, cloud resources or backup claims.

## Problem

The official FPL API does not publish finalized gameweek data on a fixed local
schedule. Today the owner must notice that the target event has become
`finished=true` and `data_checked=true`, run the existing coherent refresh, and
then explicitly run the leakage-safe evaluator. Missing that manual step delays
the evidence capture; repeatedly running full refreshes creates unnecessary
immutable snapshots and hundreds of player-history requests.

The monitor should detect completion with low-cost public requests and trigger
at most one accepted realized-data refresh for one explicit season/gameweek. It
must remain unable to create or alter an FPL decision.

## Repository-established seams

- `refresh_fpl_data` creates or explicitly resumes one raw/clean snapshot. It
  stores exact official bytes, hashes its inputs and outputs, validates coherent
  player/fixture/history identities and never overwrites a completed snapshot.
- A refresh being internally complete does not prove that a target gameweek is
  final. The refresh currently accepts whatever official state exists when it
  runs.
- `evaluation._validate_finalization` supplies the stronger realized-evidence
  boundary: the bootstrap event must be finished and data-checked, all target
  fixtures and target player-history rows must carry matching final flags, and
  the realized collections must be after the target deadline.
- `evaluate_xfp` is read-only with respect to its frozen inputs and writes a new
  immutable evaluation. It refuses post-deadline prediction inputs and
  non-finalized realized inputs.
- Existing refresh locking is snapshot-specific. Two independently started new
  refreshes can choose different timestamps, so it does not deduplicate a
  season/gameweek monitor target.

The implementation should expose a narrow public realized-snapshot validator
and make the evaluator delegate to it. It must not copy the finalization rules
into a second implementation.

## Scope

### Included

1. A one-shot, restart-safe monitor command for one explicit season and target
   gameweek.
2. Lightweight official completion probes that do not create raw snapshots.
3. Stable-readiness and target-fixture checks before a full refresh.
4. A target-level lock and durable, non-authoritative local control state.
5. Adoption of an already valid immutable realized snapshot after a restart or
   after a refresh performed outside the monitor.
6. Exactly one automatically accepted coherent refresh per target.
7. Optional, explicitly bound xFP v0.1 evaluation after realized validation.
8. Offline tests, CLI documentation and a separately reviewed local scheduling
   recipe.

### Excluded

- Task026C, web/API routes, background workers, queues or multi-user operation.
- Feature generation, new predictions, optimization, reliability changes,
  decision preparation/resume, manager evidence, journals or FPL actions.
- Model tuning, promotion, historical experiments or sealed-holdout access.
- Notifications, dashboards, provider configuration, credentials or cloud
  execution.
- Automatic deletion, mutation or repair of any raw, clean, prediction,
  evaluation or decision artifact.
- A guarantee that data is protected off the laptop. Task027E/F remain the
  recovery boundary and **NO VERIFIED OFFSITE BACKUP** remains true.

## Command boundary

Implement one command that performs one bounded reconciliation and exits:

```text
python -m fpl_decision_engine monitor-completion \
  --season 2026-27 \
  --target-gameweek 4 \
  [--prediction-snapshot-timestamp <explicit-pre-deadline-id>]
```

There is no `latest` target and no inferred season. The prediction option is
absent by default. Supplying it opts into evaluation for that exact immutable
prediction only; the monitor must not search for or substitute another
prediction snapshot.

The command performs at most one lightweight probe cycle and at most one full
refresh. An external scheduler may invoke it periodically after separate owner
approval. This keeps scheduling policy outside decision semantics and lets the
same command run manually, under macOS scheduling, or run on another authorized
host for a future gameweek.

Task028A binds each season/gameweek target to one authorized execution host. Its
filesystem lock does not provide cross-host mutual exclusion. Moving an active
target between hosts or invoking it from two hosts is outside this task. A future
target may use another host only after the owner disables every prior invocation
source for that same target. Multi-host execution requires shared lock and
artifact storage and is outside this task.

Exit outcomes must distinguish at least: waiting, success/already complete,
retryable probe failure and operator review required. Ordinary “not finalized
yet” is a successful waiting state, not an error and not a reason to run a full
refresh.

## Readiness probe

The probe reads only the public `bootstrap-static` and fixtures endpoints into
memory using the repository's verified TLS and bounded request/retry behavior.
It validates JSON shape and target identity before reading status fields.

A target is provisionally ready only when all of these are true:

1. Exactly one bootstrap event has the requested integer gameweek ID.
2. Its `finished` and `data_checked` values are exactly `true`.
3. At least one fixture belongs to that gameweek.
4. Every target fixture has `finished=true`; fixture IDs are unique and their
   team/event identity fields pass the existing fixture validation.
5. Trusted UTC time is after the event deadline.

To reduce the chance of reading across an upstream publication transition,
readiness must be observed in two successful invocations separated by at least
15 minutes. Persist only the target identity, observation time, deadline,
relevant booleans/counts, a canonical digest of those target-event and
target-fixture fields, and SHA-256 hashes of the two response bodies. Do not
persist probe response bodies or represent a probe as an official snapshot.

Construct the semantic digest from a JSON object containing exactly:

- `event`: `id`, `deadline_time`, `finished`, `data_checked`;
- `fixtures`: each target fixture's `id`, `event`, `team_h`, `team_a` and
  `finished`, ordered by integer fixture ID.

Serialize with UTF-8, lexicographically sorted object keys, no insignificant
whitespace and JSON booleans, then record the lowercase SHA-256 hex digest. All
fields must pass type and identity validation before serialization. A changed
target semantic digest resets the stability observation. A body hash change in
unrelated bootstrap or fixture fields remains visible in the audit state but
does not by itself reset readiness.

Malformed, contradictory or missing target data is a probe failure. Network and
5xx failures are retryable. A target with explicit false completion fields is
waiting. Redirects, TLS failures, target identity changes and a deadline that
cannot be parsed fail closed.

## State, locking and deduplication

Use an ignored local control directory scoped by exact target, for example:

```text
data/operations/completion-monitor/fpl/<season>/gameweek=<N>/
```

Its mutable state is execution control, not evidence and not a source of
football truth. Write it with temporary-file, file-fsync and atomic replacement;
document the existing containing-directory durability limitation. Never place
private manager data or credentials in this state.

Acquire one exclusive target-level lock before reading or changing control
state. The lock prevents two schedulers from launching distinct new snapshots
for the same target. Do not infer a lock is stale from age or automatically
delete it; provide the same inspect-and-explicit-unlock discipline as refresh.

Before probing or refreshing, reconcile control state against repository
artifacts:

1. If a recorded realized snapshot still passes manifest/hash/coherence and the
   shared finalization validator, report already complete.
2. If no valid receipt exists, scan explicit completed refresh manifests for the
   requested season. Validate candidates rather than trusting directory names or
   `latest`; among valid finalized candidates, deterministically adopt the
   earliest snapshot timestamp.
3. Never adopt an incomplete, malformed, hash-inconsistent, pre-deadline or
   non-finalized snapshot.

After stable readiness, call the existing refresh seam once. Record the returned
explicit timestamp, then validate its completed manifest, hashes/coherence and
target finalization through the shared public validator. Publish the accepted
receipt only after all checks pass.

If the new refresh completes but target finalization fails, retain the immutable
snapshot and enter `REVIEW_REQUIRED`. Do not automatically launch another full
refresh. This avoids an unbounded trail of internally complete but historically
unsuitable snapshots.

A reset is an explicit operator command under the same target lock and is
allowed only from `REVIEW_REQUIRED`. A realized-capture reset requires that no
realized receipt exists. An evaluation reset may retain a still-valid realized
receipt but requires that no evaluation receipt exists. Receipt-integrity
failures cannot use this reset. Every reset requires an operator-supplied reason
and first writes a no-overwrite archive record with the prior state, referenced
snapshot, hashes, failure and reset time. It then returns only the failed scope
to a retryable control state. It never deletes or rewrites the failed snapshot,
archive history or any accepted receipt.

A crash at any boundary must be recoverable by reconciliation: a valid refresh
created before receipt publication is adopted; a receipt is never synthesized
for missing or invalid bytes. No operation may overwrite an existing receipt
with a different snapshot identity.

## Optional evaluation

Evaluation is enabled only by an explicit prediction snapshot timestamp. After
the realized receipt is valid, bind the call to:

- exact season and target gameweek;
- exact supplied prediction snapshot and model version `v0.1`;
- exact accepted realized snapshot.

Before writing, scan complete evaluation manifests for that exact binding and
validate their referenced paths and hashes. Adopt the deterministic earliest
valid match rather than creating a duplicate. If none exists, call the existing
evaluator once. The evaluator remains authoritative for leakage and population
checks.

Missing predictions, provenance conflicts, post-deadline prediction timestamps
or evaluation validation failures enter `REVIEW_REQUIRED`; they do not trigger
prediction regeneration or selection of a different input. Evaluation failure
does not invalidate an already accepted realized snapshot.

## Scheduling and load policy

The first implementation should ship the one-shot command and a documented
macOS scheduling example, but must not install or enable that schedule. Suggested
owner policy:

- invoke every 15 minutes from the target deadline until success;
- one command instance per explicit gameweek;
- bounded request attempts and the existing TLS verification;
- stop scheduling after success or `REVIEW_REQUIRED`;
- retain concise local logs without response bodies, manager data or secrets.

Each waiting invocation makes two small public requests. Only stable readiness
permits the heavier one-bootstrap, one-fixtures and per-player-history refresh.
There is no tight polling loop. A sleeping or powered-off laptop can delay the
run; on the next invocation reconciliation resumes safely. This task does not
claim continuous availability. With a 15-minute schedule and a required
15-minute separation, capture will normally begin about 15–30 minutes after the
first finalized upstream state becomes observable, plus the full-refresh runtime.

## Failure behavior

| Condition | Required behavior |
| --- | --- |
| Not finalized | Record waiting observation; no full refresh |
| Temporary network/5xx failure | Bounded retry, then retryable exit; preserve prior valid observation |
| Malformed/contradictory official payload | Fail closed; no refresh |
| Concurrent invocation | One target lock wins; the other exits without refreshing |
| Crash during probe/state write | Ignore temporary state; retain last complete state |
| Crash during full refresh | Existing incomplete snapshot remains explicitly resumable; do not silently start another |
| Valid refresh exists but receipt is missing | Revalidate and adopt it |
| Completed refresh fails target finalization | Preserve it; `REVIEW_REQUIRED`; no automatic second refresh |
| Receipt/artifact hash mismatch | `REVIEW_REQUIRED`; never repair or overwrite evidence |
| Explicit prediction is absent | Complete realized capture and skip evaluation |
| Explicit prediction/evaluation is invalid | Preserve realized success; `REVIEW_REQUIRED`; no regeneration/substitution |
| Laptop unavailable | Capture is delayed; no false success or remote-execution claim |

## Security and privacy

All source endpoints are public and require no credential. The command must not
accept API keys, FPL login cookies, entry IDs or manager evidence. Logs and state
contain only public target metadata, hashes, artifact IDs, bounded error classes
and paths under the configured roots.

Root `data/` remains ignored and protected by the Task027C index path guard.
Task027E1's staged sensitive-content guard remains mandatory before commit. The
monitor must never stage, upload or back up its outputs. Backup remains a
separate owner-authorized workflow.

## Acceptance criteria for the later implementation

1. No network access occurs at import time or during offline tests.
2. The command requires explicit season and gameweek and runs one bounded cycle.
3. Fake-response tests cover waiting, stable readiness, changed readiness,
   malformed identities, TLS/network failure and all finalization fields.
4. Two concurrent invocations sharing one target control directory on the
   authorized host cannot create two target refreshes. Tests and documentation
   make clear that cross-host mutual exclusion is not provided.
5. Crash-point tests cover before/after refresh and before/after receipt
   publication; valid orphaned outputs are adopted by deterministic identity.
6. An accepted target causes no more than one automatically started full
   refresh. A post-refresh finalization mismatch stops for review.
7. The evaluator and monitor use one shared public realized-snapshot validator;
   existing evaluation semantics and tests remain unchanged.
8. Evaluation requires the explicit pre-deadline prediction timestamp, binds the
   accepted realized timestamp and is deduplicated by validated manifest inputs.
9. No test accesses real `data/`, manager state, credentials, sealed holdouts or
   live official endpoints. No skips or xfails depend on local artifacts.
10. Existing Python and frontend suites, generated-contract freshness,
    dependency-boundary checks and `git diff --check` pass.
11. The Task027C index guard and Task027E1 content guard pass immediately before
    any authorized commit.
12. Documentation clearly states that scheduling is not installed, laptop
    downtime delays capture and **NO VERIFIED OFFSITE BACKUP** remains true.

## Proposed delivery sequence

1. Independent adversarial review of this Task028A design.
2. Remediate design blockers and re-review if required.
3. Human authorization for a separately named Task028B implementation.
4. Codex implements the shared validator, one-shot monitor, offline tests and
   runbook without enabling a schedule.
5. Claude reviews the complete implementation bundle; Codex remediates agreed
   blockers; Claude verifies substantive remediation.
6. Human authorizes commit/push; CI provides mechanical verification.
7. A later owner step enables and observes a synthetic/off-season scheduling
   drill before relying on it for a live gameweek.
