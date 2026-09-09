# Roadmap

As of `e3758e4` (2026-09-09). This is a planning map, not authorization to
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
- Task026B follow-up #2: the current explicit decision-read chain captures
  original artifact paths once per request, then validates stable private
  snapshots with fail-closed filesystem checks (`76c10f2`).
- Task026B follow-up #3: authoritative GameweekDecision schema components feed
  the checked OpenAPI and exact-pinned generated browser types; CI rejects stale
  generated output (`946da7d`).
- Task028A/B: reviewed completion-monitor design and one-shot implementation.
  Stable exact public probes, deterministic target locking/reconciliation,
  one coherent refresh, validated immutable receipts and optional explicitly
  pinned leakage-safe evaluation are implemented (`be7baf6`, `bec2343`). No
  scheduler was installed or enabled.
- Task028C/D: reviewed synthetic scheduling-drill design and offline LaunchAgent
  harness (`de24e14`, `e3758e4`). The harness has strict preflight, an isolated
  synthetic worker, exact-label lifecycle/cleanup construction and sanitized
  evidence tooling. Automated tests use a fake service manager; no real
  `launchctl` lifecycle or scheduler installation occurred.

## NEXT — proposed operational gate, not authorized

1. After this continuity refresh is reviewed, separately authorize the actual
   temporary Task028D macOS drill on the exact reviewed commit. Run both the
   complete and review-required synthetic scenarios with unique labels, prove
   exact-label cleanup, sanitize the evidence and obtain independent evidence
   review. This roadmap entry does not authorize running it.
2. Only after a successful evidence review, decide whether to design a production
   schedule and terminal-stop control. A synthetic pass does not authorize an
   installation or establish deadline reliability.
3. Preserve the two remaining
   [Task026B follow-ups](CURRENT_HANDOFF.md#unresolved-task026b-follow-ups): keep
   the current shell/navigation disposable and the UX paradigm undecided.
4. Reconcile RFC scope with the delivered narrower read slice. Decide whether
   additional artifact read routes are needed; they are not implemented now.
   Also record the missing reliability/model-caveat rendering envisaged by the
   RFC. Neither delivery gap authorizes implementation.
5. Plan Task026C only after human approval: identity/ownership,
   OIDC/session/CSRF, PostgreSQL, private evidence review/storage, explicit engine
   commands/workers, consent/retention/export/deletion and isolation tests.
6. Review the current GitHub Actions version-deprecation annotation as a small
   maintenance task before platform enforcement; CI is currently passing.

The real Task028D synthetic drill is the smallest proposed next gate. Task026B
presentation boundaries and Task027E remain available later. The engine remains
the sole decision authority. Exact execution and evidence handling require
separate owner authorization.

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
