# Roadmap

As of merged Task031 `8a883ee` (2026-09-29). This is a planning map, not
authorization to execute. The [RFC phased plan](../rfcs/0026a-web-product-architecture.md#28-phased-implementation-plan)
is the source for Tasks026B–D. Proposed capabilities are not completed merely
because they appear in an RFC or this file.

## DONE — repository-established implementation

- Official raw/clean pipeline; fixtures/history; coherent resumable refresh;
  explicit pre-deadline features and xFP v0.1.
- Frozen evaluation, restricted historical ingestion through historical-v3.1,
  baseline backtest and isolated preregistered experiment implementations. See
  [result-evidence limits](DECISIONS.md#experiment-status-preserve-the-distinction-between-rules-and-results).
- Deterministic squad/XI optimization, public locked and manual editable state,
  legal zero-or-one-free-transfer decisions, separate reliability diagnostics.
- Persisted selection validator and GameweekDecision v1.
- Engine v1 identity/manifest contract and two-phase operational runner.
- Decision Journal/outcome and DecisionDiff v1.
- Fresh-checkout-independent tests and GitHub CI.
- Task026A architecture RFC and Task026B local read-only skeleton with
  health/decision API, frontend and trust-boundary tests.
- Project Continuity Foundation committed at `407ff74`.
- Task027C private-path safeguards/inventory; Task027D local age-encrypted
  checkpoint create/verify/restore tooling; Task027E1 staged sensitive-content
  guard; and Task027E B2 compliance-retention **design documentation**.
- Task026B follow-ups #1–#3: application import-boundary coverage, request-scoped
  stable artifact snapshots, and authoritative schema-to-browser contract drift
  detection (`824b504`, `76c10f2`, `946da7d`).
- Task028A/B: reviewed completion-monitor design and one-shot implementation.
  Stable exact public probes, deterministic target reconciliation, one coherent
  refresh, validated immutable receipts and optional exact-prediction evaluation
  are implemented (`be7baf6`, `bec2343`).
- Task028C/D and the separately authorized local drill: reviewed offline
  LaunchAgent harness plus successful complete/review-required synthetic
  lifecycles and cleanup (`de24e14`, `e3758e4`, `2187c40`).
- Task028E/F: reviewed production scheduling design and inert controller with an
  exact immutable plan, code/environment/repository preflight, typed Task028B
  outcome handling, terminal quiescence and explicit owner lifecycle controls
  (`6305f5e`, `cc30c99`). Two later plans were prepared but became stale;
  neither was installed or activated.
- Task029A/B: reviewed evidence-provenance design and private source/context
  foundation (`a715d48`, `cb72a78`). Content-addressed source bytes, immutable
  historical/prospective context structures and separated comparison layers are
  implemented outside engine decision authority.
- Task030A–D: GitHub Actions use reviewed full-SHA pins and Node 24 runtimes;
  Python uses exact uv 0.12.7, a universal lock and fail-closed CI sync/test
  controls (`4037102`, `7484484`, `00ad7e1`, `624649e`, `274e5bb`). Weekly
  GitHub Actions and uv Dependabot proposals are bounded and never auto-merged.
  Exact-commit CI and the remediated live uv update run passed. Open pip-update
  PR #1 remains an unreviewed proposal despite green CI.
- Task031A/B/C: local guided manager-evidence authoring, private draft and
  immutable publication, and verified decision display merged through
  [PR #5](https://github.com/bazuoos/fpl-decision-engine/pull/5) at `8a883ee`.
  Independent review found no mandatory findings and exact-head CI passed.
  This does not authorize a real manager-data run or FPL action.

## LOCAL OPERATIONAL EVIDENCE — not repository implementation

- The Task028D complete and review-required synthetic scenarios passed locally;
  both exact labels were absent after cleanup. Sanitized evidence was reviewed.
- After Task029B was reviewed, committed and green, one owner-selected current
  manager source was captured and verified against the source hash in current
  verified manager evidence. Seven historical-backfill context records were
  created and verified. See the sanitized [Task029C record](TASK029C_PRIVATE_EVIDENCE_CAPTURE_AND_CONTEXT_POPULATION.md).
- These private/ignored artifacts are local only. They are not included in Git
  and have **NO VERIFIED OFFSITE BACKUP**.
- Two early GW4 Task028G completion-monitor plans were prepared and independently
  reviewed but stayed inactive and became stale. A later owner-authorized plan
  reached validated terminal completion and was deactivated. This is local
  operational evidence, not an active schedule or authorization to repeat it.
- A scripted Task031 walkthrough on 2026-09-29 used committed public-derived
  GW2 fixture bytes, a constructed squad, fake manager ID and temporary files.
  It reached a verified decision; the two stop paths behaved as designed. This
  did not inspect current manager evidence or validate predictive performance.

## NEXT — proposed boundaries, not authorization

1. Decide whether to plan a first real Task031 use for a future gameweek.
   Task032A/B on [draft PR #2](https://github.com/bazuoos/fpl-decision-engine/pull/2)
   were scoped to an active GW4 monitor and older exact commits. Re-scope and
   independently review any reusable safeguards before real execution. A public
   refresh and private manager entry need separate applicable owner authority.
   Do not merge PR #2 wholesale.
2. Preserve the two remaining
   [Task026B follow-ups](CURRENT_HANDOFF.md#unresolved-task026b-follow-ups): keep
   the current shell/navigation disposable and the UX paradigm undecided.
3. Reconcile RFC scope with the delivered narrower read slice. Decide whether
   additional artifact read routes are needed. Also record the missing
   reliability/model-caveat rendering envisaged by the RFC. Neither delivery gap
   authorizes implementation.
4. Task033C3 remains design-only and blocked on source, authority, contract,
   resource and publication gates. Synthetic C1/C2 success is not real
   validation or permission to access outcomes, open confirmation or promote.
5. Plan Task026C only after human approval: identity/ownership,
   OIDC/session/CSRF, PostgreSQL, private evidence review/storage, explicit engine
   commands/workers, consent/retention/export/deletion and isolation tests.

The first real owner workflow and the research design are separate tracks.
Neither a merged workflow nor this roadmap permits an operational run, model
change, journal entry or automatic manager action.

## PAUSED — recovery owner setup

Task027E remains incomplete and owner-paused. Remaining gates include
production age identity custody, restricted B2 application keys, a
synthetic exact-version upload/readback, decrypt/verify through both custody
copies, and sanitized independent review. No real evidence upload belongs to
Task027E. Before Task027F, the owner must explicitly accept the irreversible
90-day compliance lock. **NO VERIFIED OFFSITE BACKUP.**

## LATER — proposed directions, not delivery commitments

- Task027F: create the first real checkpoint, upload and read back its exact
  immutable version, restore without the original Mac/data, validate through
  trusted engine readers, test the disconnected copy, and measure RPO/RTO.
- Task026D: tenant isolation, private object stores, queue/worker scale,
  lifecycle and operational observability, plus opt-in one-way pseudonymous
  research export. Validate service capacity before claiming support for
  100,000 users. No runtime research-to-production feedback.
- Operational outcome extensions need independently verified score sources and
  a new contract; outcome v1 does not measure human versus engine performance.

## RESEARCH IDEAS — unapproved experiments

Longer-history priors, alternative attacking-rate stabilization, richer minutes
evidence and additional scoring scope may be hypotheses, not defaults. The
existing experiments do not authorize new runs, holdout access, tuning or live
promotion. Clean-sheet/goalkeeper expansion needs its own evidence and
specification; a complete Task013 research report is not in tracked docs
(**REQUIRES HUMAN CONTEXT**).

A future opt-in personalization study may test whether explicit questionnaire
answers or observed choice patterns improve explanations, practical constraint
handling or selection among numerically close options. Maximizing expected FPL
points remains fixed. Inferred style must be confidence-aware and cannot alter
xFP, legality, optimizer scores, reliability, model selection or recommendation
ranking unless a separately consented, preregistered and validated mechanism
earns formal promotion. Historical player labels are time-bound judgments, not
permanent ratings.

Population/behavior insights require consent and privacy infrastructure before
research use. They are not current personalization or production inputs.

## Deliberately undecided / excluded

By **human-approved project policy**, the web UX paradigm is **UNDECIDED**.
Football Manager-style, decision-first, squad-first, analyst workspace, consumer
app, gameweek narrative, control-room/dashboard, mobile-first and other
approaches remain candidates. React/Vite is a stack decision, not a UX decision.

No automatic FPL execution, multi-GW/chip/hit optimization, engagement growth
features, unreviewed model upgrades, dashboard expansion or research-agent
infrastructure is authorized by this roadmap. See RFC explicit non-goals.
