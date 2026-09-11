# Task032B — Isolated Live-Run Safeguards

**Status:** implemented locally for independent review; no live run authorized

**Design authority:**
[`TASK032A_ISOLATED_FIRST_LIVE_RUN_SPEC.md`](TASK032A_ISOLATED_FIRST_LIVE_RUN_SPEC.md)

## Purpose

Task032B provides a fail-closed boundary for the first real Task031 workflow
while the GW4 completion monitor is active. It does not fetch public data,
read manager evidence, create a sandbox, execute an FPL action or alter the
monitor.

The safeguards verify one explicit schedule plan and digest, two exact Git
revisions, one durable ignored sandbox, path separation from every monitor data
root, the pinned Python interpreter, UTC deadline headroom, free disk space and
the exact loaded launchd job. The monitor check combines its live label/state
and loaded plist path with the installed plist's exact program arguments.

## Commands

Use the Task032 committed revision and the pinned Python interpreter. Every path
must be absolute. The plan and digest are mandatory; the command never searches
for a current plan.

```text
python -m fpl_decision_engine preflight-isolated-live-run \
  --primary-repository <PRIMARY_REPOSITORY> \
  --expected-primary-commit <PRIMARY_COMMIT> \
  --acknowledged-primary-untracked <REVIEWED_UNTRACKED_PATH> \
  --code-repository <TASK032_WORKTREE> \
  --expected-code-commit <TASK032_COMMIT> \
  --sandbox-root <ABSOLUTE_SANDBOX_ROOT> \
  --schedule-plan <ABSOLUTE_SCHEDULE_PLAN> \
  --schedule-plan-sha256-file <ABSOLUTE_SCHEDULE_DIGEST> \
  --expected-schedule-plan-sha256 <REVIEWED_PLAN_SHA256> \
  --official-deadline <UTC_DEADLINE> \
  --json
```

Repeat `--acknowledged-primary-untracked` exactly once for every previously
reviewed untracked path in the primary checkout. It never authorizes new paths.
Successful output contains only status, paths, commits, the schedule hash,
monitor state and free-byte count.

The normal guided command now accepts an explicit evidence root:

```text
python -m fpl_decision_engine guided-manager-decision \
  --preparation-manifest <ABSOLUTE_PREPARATION_MANIFEST> \
  --draft <ABSOLUTE_PRIVATE_DRAFT> \
  --evidence-root <ABSOLUTE_PRIVATE_EVIDENCE_ROOT>
```

For the isolated real workflow, use the integrated command with the same
preflight arguments:

```text
python -m fpl_decision_engine guided-isolated-manager-decision \
  --preparation-manifest <ABSOLUTE_SANDBOX_PREPARATION_MANIFEST> \
  --draft <ABSOLUTE_SANDBOX_DRAFT> \
  --evidence-root <ABSOLUTE_SANDBOX_EVIDENCE_ROOT> \
  <THE SAME EXPLICIT PREFLIGHT ARGUMENTS>
```

The integrated command completes the first preflight before it constructs the
terminal prompt adapter. It then re-runs the same block-only check after private
review and before the publication phrase, and after immutable evidence
publication and before the engine phrase. The callback receives no manager or
decision payload and its return value is ignored.

## Failure behavior

- Initial failure exits before any manager prompt.
- Failure before publication leaves only the private mutable draft.
- Failure after publication preserves and reports the immutable evidence path,
  runs no engine and displays no recommendation.
- Any schedule, repository, path, interpreter, clock, disk, launchd or Git
  ambiguity fails closed.
- The sandbox remains local private evidence. **NO VERIFIED OFFSITE BACKUP.**

## Later operational gates

Implementation review and a sanitized synthetic drill must pass before any
live request. A live public refresh requires separate owner authorization. The
owner enters manager facts locally; none belong in chat, arguments, environment
variables, logs or review bundles. The engine result remains advisory and any
FPL action remains manual.
