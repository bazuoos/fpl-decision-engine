# Current handoff

> **CHECKPOINT / NAVIGATION, NOT AUTHORITY.** Code, schemas, tests, immutable
> artifacts, manifests, frozen decision records and git history win on technical
> contradiction. Verify current Git state before acting.

- Implementation checkpoint summarized: **2026-09-08**,
  `946da7d1f99f26ea368dbf9918111589bb402214`. It was on `main`, aligned with
  `origin/main`, with successful CI. Identify this document's own revision and
  any later work from Git history rather than assuming the embedded SHA is HEAD.
- Latest completed implementation: **Task026B deferred follow-up #3**. The
  authoritative GameweekDecision JSON Schema now feeds the checked OpenAPI
  payload components and generated browser types. CI fails on stale generated
  TypeScript; browser consumers no longer maintain a parallel handwritten
  payload shape. Independent adversarial review reported SAFE and CI passed.
- Resilience work: Task027C local safeguards, Task027D encrypted-checkpoint
  tooling and Task027E1 staged sensitive-content guard are committed. Task027E
  owner custody/destination setup is **PAUSED** before production key generation,
  application-key creation or any upload while a dedicated disconnected medium
  is unavailable. **NO VERIFIED OFFSITE BACKUP.**
- **Task026C has not started.** The web UX paradigm remains deliberately
  **UNDECIDED**.
- Outside this five-file continuity refresh, the only visible working-tree items
  are unrelated untracked `task025_claude_review_bundle.txt` and
  `task025_review.patch`. Preserve them. Root `data/`, `.private-recovery/` and
  `.DS_Store` paths are ignored and guarded against staging.
- **Local operational state, not repository-established:** an authorized refresh
  captured finalized, data-checked Gameweek 3 public data at
  `20260908T120547.189577Z`, and a leakage-safe xFP v0.1 evaluation used the
  pre-deadline prediction snapshot `20260903T061943.538960Z`. The complete local
  evaluation covers 626 of 654 player rows. These ignored artifacts are not part
  of this documentation change and have **NO VERIFIED OFFSITE BACKUP**. No
  prospective GW3 journal or outcome was created or retrospectively invented.

## Current restrictions and limits

- This checkpoint refresh authorizes no Task026C, model/optimizer/reliability
  change, operational refresh, journal creation, artifact regeneration, private
  evidence access, sealed-holdout access, credential creation or provider action.
- Web skeleton is **localhost-only**: every request receives the same local
  principal, not client authentication. Both `FPL_APP_ARTIFACT_ROOT` and
  `FPL_APP_ARTIFACT_INDEX` are needed for indexed reads. Health can succeed with
  an empty store. Only health/decision routes exist; server verification is not
  independently reproduced by the browser.
- Operational transfer evaluation explicitly uses `appearance_only_allowed` and
  requires >=1 free transfer with zero transfer cost. It is not a general
  no-free-transfer ROLL fallback. No silent correction of questionable minutes
  projections is permitted; improvements need the model-promotion process.
- GitHub protects committed source and documentation, not local private evidence.
  Task027 tooling can inventory and create/verify/restore encrypted checkpoints,
  but there is no production recovery identity, verified remote copy,
  disconnected copy or real restore drill. Original source screenshots referenced
  by two earlier operational runs remain **NOT LOCATED**.

## Unresolved Task026B follow-ups

Follow-ups #1–#3 closed at `824b504`, `76c10f2` and `946da7d`. Two review
follow-ups remain planning inputs, not authorization for implementation:

1. Keep `App.tsx` and explicit-ID navigation disposable.
2. Keep final styling and the UX paradigm **UNDECIDED**.

Additional documented RFC gap: current `DecisionView` omits reliability and
model caveats. This does not authorize frontend work. See
[implementation versus proposal](DECISIONS.md#web-rfc-decision-status).

## Immediate next question

The human selected a bounded official-data completion-monitor design as the next
proposed task after this documentation-only refresh. The design should consider
lightweight completion polling, one deduplicated immutable refresh and optional
leakage-safe evaluation, while excluding automatic decisions, journals and
manager actions. No monitor implementation, scheduling or network operation has
started; exact scope and task identity require separate review and approval.

**REQUIRES HUMAN CONTEXT:** next approved priority; private evidence locations
and completeness; credential/key custody and private backup receipts; explicit
acceptance of irreversible 90-day compliance retention before Task027F; verified
live manager state; unrecovered prior review/result packages; and any permission
to unseal an experiment. Changing football facts need fresh verification.

## Read-next order

1. [PROJECT_STATE](PROJECT_STATE.md), including
   [validation evidence](PROJECT_STATE.md#validation-evidence-and-procedure), and
   [AI_WORKFLOW](AI_WORKFLOW.md).
2. [ARCHITECTURE](ARCHITECTURE.md), [DECISIONS](DECISIONS.md),
   [FPL_PRODUCT_PHILOSOPHY](FPL_PRODUCT_PHILOSOPHY.md), [ROADMAP](ROADMAP.md).
3. For recovery work: [Task027C local safeguards](TASK027C_LOCAL_BACKUP_READINESS.md),
   [Task027D checkpoint runbook](TASK027D_ENCRYPTED_CHECKPOINTS.md), and
   [Task027E setup specification](TASK027E_OWNER_KEY_AND_B2_SETUP_SPEC.md).
4. For web work: [README](../../README.md),
   [RFC 0026A](../rfcs/0026a-web-product-architecture.md),
   [public reader](../../src/fpl_decision_engine/trusted_artifact_reader.py),
   [app facade](../../src/fpl_decision_app/read_facade.py),
   [boundary tests](../../tests/test_web_application.py), and
   [OpenAPI](../../contracts/api/v1/openapi.json), plus the
   [generated browser types](../../web/src/api/generated-contracts.ts). Read
   [fixture provenance](../../tests/fixtures/README.md) before interpreting hashes.

## Fresh AI session bootstrap

Read this handoff, PROJECT_STATE and AI_WORKFLOW; inspect Git status/log/HEAD.
Distinguish repository facts, human-approved policy, owner-reported external
state, historical context, reported validation and missing evidence. Report
contradictions and dirty/untracked work; preserve it. Do not infer task
authorization from a roadmap. Do not access private evidence, credentials,
sealed holdouts or regenerate artifacts during onboarding. Make no modifications
until the human authorizes the continuation's scope.
