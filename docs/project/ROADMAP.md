# Roadmap

As of `824b504` (2026-09-08). This is a planning map, not authorization to
execute. The [RFC phased plan](../rfcs/0026a-web-product-architecture.md#28-phased-implementation-plan)
is the source for Tasks026B–D. Proposed capabilities are not completed merely
because they appear in that RFC.

## DONE — repository-established implementation

- Official raw/clean pipeline; fixtures/history; coherent resumable refresh;
  explicit pre-deadline features and xFP v0.1.
- Frozen evaluation, restricted historical ingestion through historical-v3.1,
  baseline backtest and isolated preregistered experiment implementations.
  See [result-evidence limits](DECISIONS.md#experiment-status-preserve-the-distinction-between-rules-and-results).
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
  guard and Task027E 90-day B2 compliance-retention design.
- Task026B follow-up #1: application forbidden-import coverage now includes
  `decision_journal` and package-member import syntax (`824b504`).

## NEXT — proposed planning boundary, not work started

1. While Task027E is paused for a dedicated disconnected medium, select and scope
   one of the four remaining
   [Task026B follow-ups](CURRENT_HANDOFF.md#unresolved-task026b-follow-ups):
   chain-wide read-once semantics, schema/type drift detection, disposable
   navigation, and preservation of the undecided UX boundary.
2. Reconcile RFC scope with the delivered narrower read slice. Decide whether
   additional artifact read routes are needed; they are not implemented now.
   Also record the missing reliability/model-caveat rendering envisaged by the
   RFC. Neither delivery gap authorizes implementation.
3. Plan Task026C only after human approval: identity/ownership,
   OIDC/session/CSRF, PostgreSQL, private evidence review/storage, explicit engine
   commands/workers, consent/retention/export/deletion and isolation tests.
4. Review the current GitHub Actions version-deprecation annotation as a small
   maintenance task before platform enforcement; CI is currently passing.

The next engineering choice is between a remaining Task026B boundary follow-up
and resuming Task027E after the required owner-held medium exists. The engine
remains the sole decision authority. Exact sequencing and acceptance criteria
require an approved task scope.

## PAUSED — recovery owner setup

Task027E remains incomplete. Resume only when the owner has a dedicated encrypted
removable medium suitable for an independent key-custody copy. Remaining gates
include production age identity custody, restricted B2 application keys, a
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
  a new contract; outcome v1 does not measure human vs engine performance.

## RESEARCH IDEAS — unapproved experiments

Longer-history priors, alternative attacking-rate stabilization, richer minutes
evidence and additional scoring scope may be hypotheses, not defaults. The
existing experiments do not authorize new runs, holdout access, tuning, or live
promotion. Clean-sheet/GK expansion needs its own evidence/specification; a
complete Task013 research report is not in tracked docs (**REQUIRES HUMAN
CONTEXT**).

Population/behavior insights may inform separately preregistered research only
after consent and privacy infrastructure. They are not personalization inputs.

## Deliberately undecided / excluded

By **human-approved project policy**, the web UX paradigm is **UNDECIDED**.
Football Manager-style, decision-first, squad-first, analyst workspace, consumer
app, gameweek narrative, control-room/dashboard, mobile-first and other
approaches remain candidates. React/Vite is a stack decision, not a UX decision.

No automatic FPL execution, multi-GW/chip/hit optimization, engagement growth
features, unreviewed model upgrades, dashboard expansion or research-agent
infrastructure is authorized by this roadmap. See RFC explicit non-goals.
