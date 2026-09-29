# Current handoff

> **CHECKPOINT / NAVIGATION, NOT AUTHORITY.** Code, schemas, tests, immutable
> artifacts, manifests, frozen decision records and Git history win on technical
> contradiction. Verify current Git state before acting.

- Latest verified `main` checkpoint: **2026-09-29**,
  `8a883eeb6a93b3b160c4d0429aa96be76e0b529b`, the merge of
  [Task031 PR #5](https://github.com/bazuoos/fpl-decision-engine/pull/5).
  Independent Claude Sonnet 5 High review reported SAFE with no mandatory
  findings. The exact-merge-commit
  [CI run 36587619212](https://github.com/bazuoos/fpl-decision-engine/actions/runs/36587619212)
  passed; the pre-merge PR-head
  [CI run 36530915472](https://github.com/bazuoos/fpl-decision-engine/actions/runs/36530915472)
  also passed. Task031 adds local guided manager-evidence authoring and a verified
  decision display. It does not authorize a live run, journal or FPL action.
  [PR #2](https://github.com/bazuoos/fpl-decision-engine/pull/2) remains open and
  draft at `c611d5e`; it contains later Task032/033/034 work and must not be
  treated as merged merely because Task031 landed.
- Earlier completed repository work: **Task030A–D dependency reproducibility and
  update monitoring**. CI actions are full-SHA pinned to releases using Node 24
  runtimes. Python uses one universal `uv.lock`, exact uv 0.12.7, prohibited uv
  interpreter downloads, an exact build constraint and a checksum-verified CI
  bootstrap. Dependabot checks GitHub Actions and uv weekly at 09:00 Tuesday in
  `Asia/Bangkok`, with at most three individual proposals per ecosystem and no
  auto-merge. Independent adversarial reviews reported SAFE. Exact-commit CI run
  34518385310 passed 582 Python and four frontend tests. The required uv update
  run 34519957982 succeeded after remediation, and created unreviewed
  [PR #1](https://github.com/bazuoos/fpl-decision-engine/pull/1) for pip
  26.1.1 -> 26.2.1; its CI passed, but it remains an untrusted open proposal.
- Production completion scheduling: Task028E design and Task028F controller are
  committed. The controller can prepare, verify, inspect, run, activate,
  deactivate and sanitize one exact immutable schedule plan, while Task028B
  retains all completion/refresh authority. Two GW4 plans were later prepared
  locally and independently reviewed, but neither was installed or activated.
  Both became deliberately stale. A later owner-authorized GW4 plan reached a
  validated terminal state and was deactivated. This is local operational
  evidence, not a standing active schedule or authorization for another run.
- **Local operational evidence, not repository-established:** after Task029B was
  reviewed, committed and green, the owner authorized capture of one current
  manager source. Its bytes matched the source hash already bound by the current
  verified manager evidence; both the source record and binding verified. Seven
  immutable historical-backfill human-context records were also created and
  verified. They preserve historical reasoning and an open personalization
  hypothesis as human context only; none is prospective or an engine input. See
  [Task029C local-operation record](TASK029C_PRIVATE_EVIDENCE_CAPTURE_AND_CONTEXT_POPULATION.md).
- Resilience work: Task027C local safeguards, Task027D encrypted-checkpoint
  tooling and Task027E1 staged sensitive-content guard are committed. Task027E
  owner custody/destination setup remains **PAUSED**. Reported custody steps do
  not establish a verified offsite restore. **NO VERIFIED OFFSITE BACKUP.**
- **Task026C has not started.** The web UX paradigm remains deliberately
  **UNDECIDED**.
- The only visible working-tree items before this documentation refresh were
  unrelated untracked `task025_claude_review_bundle.txt` and
  `task025_review.patch`. Preserve them. Root `data/`, `.private-recovery/` and
  `.DS_Store` paths are ignored and guarded against staging.
- Earlier authorized local public-data evidence includes finalized Gameweeks 3
  and 5 and leakage-safe xFP v0.1 evaluations against exact pre-deadline
  predictions. Those ignored artifacts have **NO VERIFIED OFFSITE BACKUP**.
  They are model-wide evaluation evidence, not a prospective journal or an FPL
  action; no historical belief is rewritten from later outcomes.
- A 2026-09-29 scripted Task031 walkthrough used the committed, public-derived
  GW2 test fixture, a constructed 15-player squad, fake manager entry ID and
  temporary storage. It reached a verified decision; separate stop checks showed
  that declining publication left no evidence or decision, while declining the
  engine left evidence but no decision. This is test-fixture workflow
  verification, not live manager-state or predictive validation.

## Current restrictions and limits

- This checkpoint refresh authorizes no schedule activation, Task026C,
  model/optimizer/reliability change, operational refresh, journal creation,
  real decision run, artifact regeneration, private evidence inspection,
  sealed-holdout access, credential creation or provider action.
- Task028F is inert without a current exact plan and explicit owner lifecycle
  action. It does not infer a target or prediction. Two early GW4 plans stayed
  stale and inactive; the later completed GW4 plan is deactivated. Any new plan
  requires a new exact revision and independent review; preparation is not
  installation and installation is not activation.
- Human strategy context preserves what was believed and why. It never rewrites
  trusted engine artifacts, creates prospective evidence after a deadline or
  changes xFP, legality, optimization, reliability or model selection.
- Dependabot pull requests are discovery artifacts, not approved maintenance.
  Open PR #1 must not be merged merely because its mechanical CI passed.
- Task028B remains bounded and one-shot. Its optional evaluation requires an
  explicit exact pre-deadline prediction and cannot generate or substitute one.
- Web skeleton is **localhost-only**: every request receives the same local
  principal, not client authentication. Both `FPL_APP_ARTIFACT_ROOT` and
  `FPL_APP_ARTIFACT_INDEX` are needed for indexed reads. Health can succeed with
  an empty store. Only health/decision routes exist; server verification is not
  independently reproduced by the browser.
- Operational transfer evaluation explicitly uses `appearance_only_allowed` and
  requires at least one free transfer with zero transfer cost. It is not a
  general no-free-transfer ROLL fallback. Engine v1 optimizes one gameweek and
  does not value future free-transfer flexibility.
- GitHub protects committed source and documentation, not local private evidence.
  There is no production recovery identity, verified remote copy, disconnected
  copy or real restore drill. Original source screenshots referenced by two
  earlier operational runs remain **NOT LOCATED**.

## Unresolved Task026B follow-ups

Follow-ups #1–#3 closed at `824b504`, `76c10f2` and `946da7d`. Two review
follow-ups remain planning inputs, not authorization for implementation:

1. Keep `App.tsx` and explicit-ID navigation disposable.
2. Keep final styling and the UX paradigm **UNDECIDED**.

Additional documented RFC gap: current `DecisionView` omits reliability and
model caveats. This does not authorize frontend work. See
[implementation versus proposal](DECISIONS.md#web-rfc-decision-status).

## Immediate next question

Task031 is merged and its scripted test-fixture walkthrough passed. The next
product decision is whether to plan a first real use for a future gameweek.
Task032A/B in draft PR #2 were designed for an active GW4 monitor and exact
older revisions; they must be re-scoped and re-reviewed before use for a later
gameweek. A real public refresh, private manager-fact entry and engine run each
need their own applicable authorization and gates. Do not merge PR #2 wholesale.
Task033C3 has a design and owner-selected policy directions in PR #2, but its
implementation/source/authority/resource gates remain open. No real research
run, confirmation opening or model promotion is authorized.

**REQUIRES HUMAN CONTEXT:** any future real-run or Task028G activation decision;
private evidence completeness; credential/key custody and private backup receipts; explicit
acceptance of irreversible 90-day compliance retention before Task027F; verified
live manager state; unrecovered prior review/result packages; and any permission
to unseal an experiment. Changing football facts need fresh verification.

## Read-next order

1. [PROJECT_STATE](PROJECT_STATE.md), including
   [validation evidence](PROJECT_STATE.md#validation-evidence-and-procedure), and
   [AI_WORKFLOW](AI_WORKFLOW.md).
2. [ARCHITECTURE](ARCHITECTURE.md), [DECISIONS](DECISIONS.md),
   [FPL_PRODUCT_PHILOSOPHY](FPL_PRODUCT_PHILOSOPHY.md), [ROADMAP](ROADMAP.md).
3. For completion control: [Task028B implementation](TASK028B_COMPLETION_MONITOR.md),
   [Task028D drill](TASK028D_COMPLETION_MONITOR_SCHEDULING_DRILL.md),
   [Task028E design](TASK028E_PRODUCTION_COMPLETION_MONITOR_SCHEDULING_SPEC.md)
   and [Task028F controller](TASK028F_PRODUCTION_COMPLETION_MONITOR_SCHEDULING.md).
4. For private evidence/recovery: [Task027C safeguards](TASK027C_LOCAL_BACKUP_READINESS.md),
   [Task027D checkpoints](TASK027D_ENCRYPTED_CHECKPOINTS.md),
   [Task027E setup](TASK027E_OWNER_KEY_AND_B2_SETUP_SPEC.md),
   [Task029A design](TASK029A_EVIDENCE_PROVENANCE_AND_STRATEGY_CONTEXT_SPEC.md),
   [Task029B tooling](TASK029B_PRIVATE_EVIDENCE_AND_CONTEXT.md) and the sanitized
   [Task029C record](TASK029C_PRIVATE_EVIDENCE_CAPTURE_AND_CONTEXT_POPULATION.md).
5. For dependency and CI maintenance: [workflow](../../.github/workflows/ci.yml),
   [Dependabot configuration](../../.github/dependabot.yml),
   [Task030C design](TASK030C_PYTHON_DEPENDENCY_REPRODUCIBILITY_SPEC.md) and
   [Task030D implementation](TASK030D_PYTHON_DEPENDENCY_REPRODUCIBILITY.md).
   Treat automated update pull requests as proposals requiring ordinary review
   and green CI.
6. For the merged local workflow:
   [Task031A design](TASK031A_LOCAL_OPERATIONAL_DECISION_LOOP_SPEC.md),
   [Task031C interface design](TASK031C_GUIDED_LOCAL_OWNER_INTERFACE_SPEC.md),
   [wizard](../../src/fpl_decision_engine/local_decision_wizard.py) and
   [authoring tests](../../tests/test_manager_evidence_authoring.py).
7. For web work: [README](../../README.md),
   [RFC 0026A](../rfcs/0026a-web-product-architecture.md),
   [public reader](../../src/fpl_decision_engine/trusted_artifact_reader.py),
   [app facade](../../src/fpl_decision_app/read_facade.py),
   [boundary tests](../../tests/test_web_application.py), and
   [OpenAPI](../../contracts/api/v1/openapi.json). Read
   [fixture provenance](../../tests/fixtures/README.md) before interpreting hashes.

## Fresh AI session bootstrap

Read this handoff, PROJECT_STATE and AI_WORKFLOW; inspect Git status/log/HEAD.
Distinguish repository facts, human-approved policy, owner-reported external
state, historical context, reported validation and missing evidence. Preserve
dirty/untracked work. Do not infer authorization from a roadmap. Do not inspect
private evidence, credentials or sealed holdouts during onboarding. Make no
modifications until the human authorizes the continuation's scope.
