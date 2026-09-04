# Roadmap

As of `95dbbb0` (2026-09-04). This is a planning map, not authorization to execute.
The [RFC phased plan](../rfcs/0026a-web-product-architecture.md#28-phased-implementation-plan)
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
- Task026A architecture RFC committed; Task026B local read-only skeleton
  committed with health/decision API, frontend and trust-boundary tests.

## NEXT — proposed planning boundary, not work started

1. Review this continuity pack and resolve recovery ownership/context gaps.
2. Plan Task026C only after human approval. Before real artifact/read expansion,
   close or explicitly design the five [Task026B follow-ups](CURRENT_HANDOFF.md#unresolved-review-follow-ups):
   import boundary, read-once semantics, schema drift, disposable navigation,
   and undecided visual/UX style.
3. Reconcile RFC scope with the delivered narrower read slice. Decide whether
   additional artifact read routes are needed; they are not implemented now.
   Also record the missing reliability/model-caveat rendering envisaged by the
   RFC. Neither delivery gap authorizes implementation in this documentation task.
4. Scope the RFC's Task026C authenticated private workflow: identity/ownership,
   OIDC/session/CSRF, PostgreSQL, private evidence review/storage, explicit engine
   commands/workers, consent/retention/export/deletion and isolation tests.

The immediate engineering question is how to integrate real authorized artifact
reads and manager evidence safely, not how to add a second decision engine.
Exact Task026C sequencing, acceptance criteria and UX require a new approved spec.

## LATER — RFC direction, not delivery commitments

Task026D: tenant isolation, private object stores, queue/worker scale, lifecycle
and backup/restore drills, operational observability, and opt-in one-way
pseudonymous research export. Validate service capacity before claiming support
for 100,000 users. No runtime research-to-production feedback.

Operational outcome extensions would need independently verified score sources
and a new contract; outcome v1 does not yet measure human vs engine performance.
Backup/restore procedures need ownership and a real drill, not just this pack.

## RESEARCH IDEAS — unapproved experiments

Longer-history priors, alternative attacking-rate stabilization, richer minutes
evidence and additional scoring scope may be hypotheses, not defaults. The
existing experiments do not authorize new runs, holdout access, tuning, or live
promotion. Clean-sheet/GK expansion needs its own evidence/specification; a
complete Task013 research report is not in tracked docs (**REQUIRES HUMAN CONTEXT**).

Population/behavior insights may inform separately preregistered research only
after consent and privacy infrastructure. They are not personalization inputs.

## Deliberately undecided / excluded

By **human-approved project policy**, the web UX paradigm is **UNDECIDED**. Football Manager-style, decision-first,
squad-first, analyst workspace, consumer app, gameweek narrative,
control-room/dashboard, mobile-first and other approaches remain candidates.
React/Vite is a stack decision, not a UX decision.

No automatic FPL execution, multi-GW/chip/hit optimization, engagement growth
features, unreviewed model upgrades, dashboard expansion or research-agent
infrastructure is authorized by this roadmap. See RFC explicit non-goals.
