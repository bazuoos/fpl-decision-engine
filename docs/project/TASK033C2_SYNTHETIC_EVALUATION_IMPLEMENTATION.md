# Task033C2 synthetic evaluation implementation

This committed implementation follows the [candidate freeze](TASK033B1B_CANDIDATE_FREEZE_AMENDMENT.md)
and [reviewed clarification](TASK033C2_PROTOCOL_CLARIFICATION.md) at base
`7874ded703c2173827738d2fc23f1d456826f95a`. The historical
[blocker](TASK033C2_PROTOCOL_BLOCKER.md) is preserved. This document records
implementation at `6d695afc32c33627b7ddb2f8b410e5d7ab37e785`, not an amendment
or authorization to execute a real study.

## Boundary and entry point

`fpl_decision_engine.research.c2.evaluate` accepts validated, already-created
prediction records, separately joined synthetic outcomes and supplied resource
gates. It neither constructs predictions nor accesses a filesystem, database,
network, source resolver, clock, manager state or publication service. It returns
strict result records; `canonical_bytes` returns compact UTF-8 JSON with sorted
object keys and exactly one trailing LF. Four closed JSON schemas document the
prediction, outcome, resource and result contracts. Semantic cross-field checks
also require the Python validators; JSON Schema alone does not establish them.

The prediction contract fixes the C1 formula identities and requires all four
registered forecast slots. Combination predictions may exist before selection,
but their outcomes are not scored and their membership is not constructed until
both component evaluations pass all required gates. Synthetic confirmation accepts
one explicitly supplied selected package; it resolves no real season or authority.
The synthetic-only labels are declarations, not proof of source provenance.

## Pairing and deterministic inference

Each candidate/control pair freezes its own original ranking gameweeks before
resampling. Membership bytes are a compact JSON array of exact season/gameweek
objects, sorted lexicographically by season then numerically by gameweek, with
sorted object keys, no duplicates and a terminal LF. Results bind both identities
and SHA-256. Unevaluated packages have no membership or hash. Included identities
and objective excluded-gameweek reasons are published. The digest establishes
byte identity only; it does not attest valid inputs.

The evaluator implements the complete fixed global and subgroup endpoint vectors,
per-season gates and equal-season aggregate statistics. It recomputes nonlinear
statistics within each replicate. Copied-player rank midpoints and strict top-N
intersection counts use frozen prediction/actual order with draw-specific copy
counts. Count-calibration deciles recompute copied ranks and bin intersections.
No player-by-gameweek-by-replicate tensor is constructed. Dense arrays contain
only per-row sufficient metric data or replicate-by-endpoint diagnostics.

Both streams use all 9,999 prescribed draws, fresh PCG64 generators and the exact
per-replicate NumPy integer calls. Serial draws share player-code multiplicities
and draw independent blocks by season. The non-circular edge inclusion effect
is deliberately retained; it limits interpretation and is not corrected. Bounds
use the complete applicable family maximum, fixed scales, fixed quantile indices,
reserved alpha and strict thresholds. An undefined required draw fails its family;
there is no redraw or removal. Insufficient original populations receive no
invented interval. No separate ranking family exists.

Scoring retains infinite log losses without epsilon. Count RPS truncation uses
an analytic conservative upper bound on `E[(Y-K-1)+]`: bound the remaining PMF
ratios by `r < 1`, then bound the excess sum by `p[K+2]/(1-r)^2` per mixture
component. The sum certifies the frozen `1e-10` residual requirement; failure at
10,000 is numerical failure. PMFs and moments are validated against the C1
normalization tolerances, not relaxed scoring thresholds.

Results include natural and common-pair metrics, coverage/missingness and overflow
counts, required and observed diagnostic strata, per-gameweek component ranking,
calibration tables, predictive intervals, lag correlations, simultaneous bounds,
marginal paired intervals where calculable, and retained-cluster summaries.
Replicate calibration tables are streamed into canonical hashes; full per-draw
calibration tables are not retained in the result. Descriptive scalar intervals
are available for finite eligible metrics; infinite log losses and undefined
statistics retain explicit states rather than surrogate finite intervals.

## Validation and resource interpretation

The generated-only runner is `scripts/task033c2_synthetic_feasibility.py`.
It executes two complete identical evaluations, reports canonical result hashes,
wall time, normalized process peak RSS, exact environment and configured threads.
OS thread sampling is recorded separately in the review evidence. Supplied PASS
resource inputs in this synthetic fixture are declarations, not C3 measurement
receipts. Memory/time measurements and native thread observations must be read
together; an environment variable is not proof of actual thread count.

Tests cover numerical examples and copied-row oracles, strict malformed contracts,
all-9,999-draw RNG stream oracles, gates and exact quantile boundaries, pair-specific
membership, conditional orchestration, canonical output and schema freshness.
The architecture guard checks production dependency direction and the C2 pure
import/call boundary. The review package records focused, complete and isolated
export validation, the full feasibility evidence, and any failed attempts.

A full-replicate validation attempt caught a NumPy boolean crossing the strict
result boundary. The comparison now explicitly returns a Python bool, with a
regression assertion. Sufficient-sum reductions use deterministic non-BLAS
`einsum(..., optimize=False)` to avoid dispatching matrix-vector worker pools.
These are implementation fixes, not changes to the RNG or statistical rules.

## Remaining prerequisites

Independent C2 remediation review reported SAFE, the owner authorized the commit,
and exact-commit CI passed. C3 must implement and
validate causal source resolution, permission checks, immutable prediction/outcome
separation, identity bridges, exact manifests, guarded joins, resource enforcement,
sanitized failures and exclusive publication, with end-to-end synthetic privacy
and scale tests. C2 hashes and synthetic labels establish none of these properties.

This drill does not establish real-source feasibility, full historical-study
completion, prediction-phase or I/O/publication budgets, the 700-player ceiling,
conditional-combination full-scale runtime, or decision-runtime compliance. The
3,000-candidate/12-view decision corpus and its 60-second limit remain deferred.
A real development study and any untouched confirmation source require separate
explicit authorization after the reviewed C3 prerequisites. No result here grants
production eligibility, changes `xfp_v01`, proves better decisions, or begins
Task033D or Task026C. Decision-corpus status remains `NOT_EVALUATED`; the highest
possible future eligibility label remains component diagnostics only.

## Round-two review remediation

NumPy is a direct runtime dependency (`numpy>=2.2,<3`). The universal lock is
regenerated only with repository-required uv 0.12.7; its pre-existing Python-specific
NumPy selections are retained, with no unrelated version drift. Locked synchronization
uses an existing interpreter with Python downloads prohibited. Exact-commit Python 3.10
CI was the mandatory post-commit gate. Run 35684460984 passed on the exact C2
commit using Python 3.10, pinned uv 0.12.7 and the locked environment.

The serial union literally includes every distinct verified player code represented
by the validated registered prediction universe, including codes represented only
by blank rows. Those codes participate in the bytewise UTF-8 ordered U-draw and can
receive multiplicity without contributing any scored nonblank rows. Missing bridges
on blank rows also fail closed. This does not alter the crossed nonblank population.
In contrast, availability subgroup structural presence is determined from the
registered nonblank pre-outcome universe. Blank-only availability groups are
`NOT_APPLICABLE`; one nonblank row makes such a group present but undersized.

Malformed resource evidence is rejected by strict contract validation before scoring;
it is not converted into a fabricated `DO_NOT_CONFIRM` result. Valid missing, failed
or inapplicable evidence cannot authorize selection. One invalid required endpoint
prevents construction of its entire simultaneous family bound: the frozen statistic
is a maximum over the complete applicable endpoint vector, so dropping a coordinate
would change the registered family. The other family is still evaluated independently.

The membership SHA-256 covers exactly the canonical included identity array and LF.
Candidate and control identities are bound separately by the result contract.
Excluded identities and reasons are published separately and are not part of that
membership digest. No digest-format or statistical-rule expansion is introduced.

A private replicate-stream method permits test-only fixed numerical draws to exercise
the real family, status and selection orchestration. The public API has no override.
A separate naive oracle explicitly expands rows and player copies, recomputes ranks,
bins and scores, and uses count-probability recurrence independently of production
metric helpers. Randomized generated outcomes include blanks, double gameweeks and
candidate-specific completeness. Focused C1 compatibility tests cover lowercase
availability codes, identities, scoring constants and U0 examples without a runtime
C2-to-C1 import. Full C1-to-C2 adapter parity remains a C3 prerequisite; these examples
do not establish complete adapter or artifact-chain compatibility.

Importing the feasibility fixture no longer changes `os.environ`. Its computational
thread variables must be supplied explicitly by the launching subprocess environment
before numerical imports; the runner rejects absent or different settings. Evidence
now publishes the final decision, selection and every package's status, failures,
resource status and optional membership hash. `DO_NOT_CONFIRM`, failing components
and the `NOT_EVALUATED` combination must remain visible even when deterministic
repetition and measured time/memory checks succeed.

For example, launch the synthetic drill from the project root with an explicit
subprocess environment (using the locked project interpreter):

```python
import os
import subprocess
import sys

names = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
         "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")
environment = {**os.environ, **{name: "1" for name in names}}
subprocess.run([sys.executable, "scripts/task033c2_synthetic_feasibility.py",
                "--players", "100"], env=environment, check=True)
```

The declared process count concerns the evaluator worker. Any shell or launcher
parent is outside that count. Native-thread observations must be reported alongside
configured computational threads; a parent launcher and environment settings do
not establish continuous thread enforcement or a complete C3 resource receipt.
