# Roadmap

As of `6d695af` (2026-09-22) on the isolated Task033 branch. This is a planning
map, not authorization to
execute. The [RFC phased plan](../rfcs/0026a-web-product-architecture.md#28-phased-implementation-plan)
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
  (`6305f5e`, `cc30c99`). Two early plans remained inactive and revision-stale;
  the later completed/deactivated plan is recorded as local operational evidence
  below.
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
- Task031A/B and Task032A/B: guided local owner operations, verified manager
  evidence authoring and isolated live-run safeguards are implemented on draft
  PR #2. They do not automate FPL actions.
- Task033A/B/B1: numerical-robustness design, frozen uncertainty protocol and
  prior-result provenance verification are committed. The legacy holdouts remain
  unevaluated and no earlier candidate is promoted.
- Task033C1/C2: pure uncertainty kernels and deterministic synthetic evaluation
  are implemented and independently reviewed. Exact-commit CI run 35684460984
  passed on Python 3.10. The synthetic feasibility fixture returned
  `DO_NOT_CONFIRM`; this validates rejection mechanics, not real performance.

## LOCAL OPERATIONAL EVIDENCE — not repository implementation

- The Task028D complete and review-required synthetic scenarios passed locally;
  both exact labels were absent after cleanup. Sanitized evidence was reviewed.
- After Task029B was reviewed, committed and green, one owner-selected current
  manager source was captured and verified against the source hash in current
  verified manager evidence. Seven historical-backfill context records were
  created and verified. See the sanitized [Task029C record](TASK029C_PRIVATE_EVIDENCE_CAPTURE_AND_CONTEXT_POPULATION.md).
- These private/ignored artifacts are local only. They are not included in Git
  and have **NO VERIFIED OFFSITE BACKUP**.
- Two early GW4 Task028G plans remained inactive and revision-stale. A later
  evaluation-enabled plan bound to `e10d4c9` was owner-activated, produced
  validated GW4 realized and evaluation receipts, reached terminal state and was
  deactivated. This is local operational evidence and did not perform an FPL or
  manager action.
- An owner-authorized bounded Task028B run captured finalized GW5 public data on
  2026-09-22, then evaluated the exact frozen pre-deadline xFP v0.1 prediction.
  The monitor returned `COMPLETE`. This created model-wide evidence only, with no
  decision, journal or FPL action. The ignored artifacts and receipts have
  **NO VERIFIED OFFSITE BACKUP**.

## NEXT — proposed design boundary, not work started

1. Design Task033C3: controlled causal-source resolution, explicit permission
   gates, immutable prediction/outcome separation, identity bridges, guarded
   joins, resource enforcement, sanitized failures, exclusive publication and
   end-to-end synthetic privacy/scale validation. Design review must precede
   implementation. C3 does not authorize a real study or holdout access.
2. Preserve the two remaining
   [Task026B follow-ups](CURRENT_HANDOFF.md#unresolved-task026b-follow-ups): keep
   the current shell/navigation disposable and the UX paradigm undecided.
3. Reconcile RFC scope with the delivered narrower read slice. Decide whether
   additional artifact read routes are needed. Also record the missing
   reliability/model-caveat rendering envisaged by the RFC. Neither delivery gap
   authorizes implementation.
4. Plan Task026C only after human approval: identity/ownership,
   OIDC/session/CSRF, PostgreSQL, private evidence review/storage, explicit engine
   commands/workers, consent/retention/export/deletion and isolation tests.

Task033C3 design is the smallest proposed research-infrastructure boundary. The
separate completion monitor remains the trusted path for bounded public
post-gameweek data capture and has no research, decision or manager authority.

## PAUSED — recovery owner setup

Task027E remains incomplete. The owner reports that password-manager custody and
an encrypted removable key copy now exist; this is external state, not repository
proof. Remaining gates include restricted B2 application keys, a synthetic
exact-version upload/readback, decrypt/verify through both custody copies, and
sanitized independent review. No real evidence upload belongs to Task027E. Before
Task027F, the owner must explicitly accept the irreversible 90-day compliance
lock. **NO VERIFIED OFFSITE BACKUP.**

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
