# Current handoff

> **CHECKPOINT / NAVIGATION, NOT AUTHORITY.** Code, schemas, tests, immutable
> artifacts, manifests, frozen decision records and git history win on technical
> contradiction. Verify current Git state before acting.

- Checkpoint: **2026-09-04**; inspected HEAD
  `95dbbb0ba069bc7011fc75b72c8213325c403227`, branch `main`.
- Latest completed implementation: **Task026B — Add web application skeleton**.
  Task026A RFC is committed. **Task026C has not started.**
- Current work: documentation-only continuity remediation, pending final review;
  no commit or push authorized. Seven pack files remain untracked; README has a
  discoverability edit. Preserve unrelated untracked `data/`, `.DS_Store` and
  Task025 review files. `data/operations/` is not covered by repository ignores.
- Engine v1 supplies decision authority; local FastAPI/React consume it. xFP v0.1
  remains limited to appearance/goals/assists. Human-approved policy and historical
  rationale are labelled separately from implemented behavior.

## Current restrictions and limits

- No Task026C, model/optimizer/reliability changes, refresh, journal creation,
  artifact regeneration, private-evidence access or sealed-holdout access follows
  from this pack. Current scope permits only the seven pack documents and the
  README discoverability link; no staging, commit or push.
- Web skeleton is **localhost-only**: every request receives the same local
  principal, not client authentication. Both `FPL_APP_ARTIFACT_ROOT` and
  `FPL_APP_ARTIFACT_INDEX` are needed for indexed reads. Health can succeed with
  an empty store. Only health/decision routes exist; server verification is not
  independently reproduced by the browser.
- Operational transfer evaluation explicitly uses `appearance_only_allowed` and
  requires >=1 free transfer with zero transfer cost. It is not a general
  no-free-transfer ROLL fallback. No silent correction of questionable minutes
  projections is permitted; improvements need the model-promotion process.
- Git/tests do not restore missing private prospective evidence. Recovery
  ownership and evidence inventory remain **REQUIRES HUMAN CONTEXT**; see
  [recovery boundaries](PROJECT_STATE.md#recovery-boundaries-and-safe-sequence).

## Unresolved review follow-ups

These five deferred items were supplied by the human after Task026B review;
that transcript is not repository evidence or authorization for implementation.

1. Add `decision_journal` to the app's forbidden imports before read expansion.
   Current app uses the public reader; the guard omits this module.
2. Design chain-wide read-once semantics. Returned-hash equality checks exist,
   but the facade and downstream validators still make path-based rereads.
3. Strengthen API/engine-schema/TypeScript drift detection; OpenAPI snapshot and
   TS checks exist, but payload types are manually maintained.
4. Keep `App.tsx` and explicit-ID navigation disposable.
5. Keep final styling and the UX paradigm **UNDECIDED**.

Additional documented RFC gap: current `DecisionView` omits reliability
and model caveats. This does not authorize frontend work. See
[implementation versus proposal](DECISIONS.md#web-rfc-decision-status).

## Immediate next question

Can the revised pack pass final review? Any subsequent engineering task needs
its own approved scope. **REQUIRES HUMAN CONTEXT**: next approved spec and
priorities, deployment/backup ownership, private artifact inventory, verified
live manager state, unrecovered prior review/result packages, and any permission
to unseal an experiment. Changing football facts need fresh verification.

## Read-next order

1. [PROJECT_STATE](PROJECT_STATE.md), including
   [reported validation versus repository facts](PROJECT_STATE.md#validation-evidence-and-procedure),
   and [AI_WORKFLOW](AI_WORKFLOW.md).
2. [ARCHITECTURE](ARCHITECTURE.md), [DECISIONS](DECISIONS.md),
   [FPL_PRODUCT_PHILOSOPHY](FPL_PRODUCT_PHILOSOPHY.md), [ROADMAP](ROADMAP.md).
3. [README](../../README.md), [RFC 0026A](../rfcs/0026a-web-product-architecture.md),
   [public reader](../../src/fpl_decision_engine/trusted_artifact_reader.py),
   [app facade](../../src/fpl_decision_app/read_facade.py),
   [boundary tests](../../tests/test_web_application.py),
   [OpenAPI](../../contracts/api/v1/openapi.json), and task-relevant code/tests.
   Read [fixture provenance](../../tests/fixtures/README.md) before interpreting hashes.

## Fresh AI session bootstrap

Read this handoff, PROJECT_STATE and AI_WORKFLOW; inspect Git status/log/HEAD.
Distinguish repository facts, human-approved policy, historical context, reported
validation and missing evidence. Report contradictions and dirty/untracked work;
preserve it. Do not infer task authorization from a roadmap. Do not access
private evidence, sealed holdouts or regenerate artifacts during onboarding.
Make no modifications until the human authorizes the continuation's scope.
