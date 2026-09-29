# TASK033C — Synthetic uncertainty-research implementation

## Authority and current scope

Task033C1 implements the independently reviewed Task033B1B numerical protocol
proposal in research-only code. Owner approval of this C1 implementation is a
separate gate; this document does not retroactively label the proposal as an
approved or executable freeze. It does not authorize a historical dataset read, outcome
join, confirmation-season resolution, production xFP change, decision
regeneration, model promotion, journal update, or operational publication.
Production `xfp_v01` remains authoritative.

The implementation is deliberately split into three independently reviewable
slices:

1. **Task033C1 — prediction kernels and contracts.** Pure U0 adapters, UM1 and
   UA1 kernels, exact modeled-component assembly, strict prediction-side input
   contracts, deterministic serialization, architecture isolation, and a
   synthetic kernel/resource drill.  This slice has no filesystem loader and
   accepts no target outcomes.
2. **Task033C2 — evaluation and selection.** Registered proper scores,
   diagnostics, paired crossed and serial-block inference, multiplicity,
   coverage/subgroup/resource gates, and the deterministic conditional package
   selection rule.  It consumes separately published predictions and joined
   outcomes but cannot construct predictions or select data sources.
3. **Task033C3 — execution and publication.** An audited causal loader, source
   and cutoff binding, prediction-before-outcome workflow, exclusive immutable
   publication, failure receipts, deterministic reproduction, and end-to-end
   synthetic scale/privacy drills.  Real development execution remains blocked
   until a separately authorized and reviewed source manifest exists.

Only Task033C1 is implemented in this change.  Nothing in the C1 API or its
synthetic benchmark is a successful C2/C3 artifact chain.

## C1 trust boundary

The C1 implementation lives under `fpl_decision_engine.research`.  Production,
application, and presentation modules must not import that namespace.  The
kernel accepts typed, already cutoff-safe fixture history; it performs a second
defensive rejection of target/future gameweeks, duplicate fixture identities,
cross-season rows, invalid universe/cutoff markers, missing required minutes,
and non-finite or malformed values.  No target outcome field exists in the
prediction API.

The artifact header contract is separate from kernel result objects.  It binds
protocol and candidate identity, unresolved public source/cutoff placeholders,
prediction-only stage, canonical ordering, exclusive/no-overwrite expectation,
and unopened/unauthorized confirmation state.  A hash proves byte identity
only; it makes no historical immutability or semantic-validity claim.  Actual
publication and failure-manifest behavior belong to C3.

Probability validation uses an implementation tolerance of `1e-12` for finite,
bounded normalization checks.  This is a numerical representation tolerance,
not a change to any Task033B1B statistical threshold or formula.

Component-only candidates preserve U0's diagnostic incomplete-total behavior:
when an attacking input is missing, its contribution is coalesced to zero while
`prediction_complete` remains false. The combined `UM1UA1-v1` candidate instead
sets the entire modeled total to null when either uncertain attacking component
is missing. Blank gameweeks remain the explicit zero-point exception. This
asymmetry is deliberate and must remain visible to later C2 scoring and gating.

## C1 claim limits

The synthetic drill exercises at most 700 generated public-player identities
and the C1 prediction kernels.  It cannot establish the 3,000-candidate,
12-view, 60-second operational decision limit because candidate enumeration and
robustness views are not implemented here.  It also cannot establish the full
9,999-replicate offline statistical budget, source-loader isolation, immutable
publication behavior, or predictive benefit.  Those claims remain blocked on
C2/C3 implementation, review, explicit real-source authorization, and the
unopened confirmation protocol.

## Synthetic feasibility evidence

The final C1 drill was run on 2026-09-12 with CPython 3.14.5 on the owner's
macOS 26.5.1 arm64 environment, one process and one configured computational
thread. It generated exactly 700 public-player identities for one synthetic
target gameweek: 630 single-fixture targets, 70 double-fixture targets, 5,600
player-history rows, and 974,400 peer-history-row presentations. It exercised
UM1, UA1 and UM1UA1 and canonically serialized 11,854,885 output bytes.

The first run took 2.5365143748931587 seconds and the immediate deterministic
repeat took 2.5553669999353588 seconds. Both produced SHA-256
`eea5e22baa6b48e10ddbb3655ae4cd91e5765bb1361c69d71b9eb2e3ff46a794`.
Peak process RSS reported by `resource.RUSAGE_SELF.ru_maxrss` was 97,075,200
bytes. This supports only the bounded C1 kernel/serialization claims above;
the process high-water mark includes interpreter and imported-package memory
and is not per-object attribution.

## Validation evidence

Validation used the existing primary checkout's virtual environment with
`PYTHONPATH` pointed at this isolated worktree. The available interpreter was
CPython 3.14.5; the repository's CI Python 3.10 environment was not locally
available, and the exact `uv` executable was unavailable in this shell.

- Focused C1 tests: 44 passed, zero skipped.
- Full Python discovery suite: 699 passed, zero skipped, in 108.669 seconds.
  It emitted the pre-existing Starlette/httpx deprecation warning.
- Fresh-checkout-equivalent copy from exact base `d6716bc`, with byte comparisons
  for every changed file: 44 focused tests passed, zero skipped, in 0.437
  seconds. An initial shell orchestration attempt copied no files because the
  loop variable `path` overwrote zsh's command path; no project test ran in that
  attempt. The corrected run above used `file_path` and passed.
- `git diff --check`, JSON schema parsing, targeted `compileall`, the Task033
  architecture/import-boundary tests, deterministic-repeat tests, and the
  repository index-path privacy guard passed.
- Frontend checks were not run because no frontend, API, generated contract, or
  shared application boundary changed. The staged exact-value secret guard was
  not run because nothing is staged and it requires an owner-supplied private
  denylist; no such private input was read.
