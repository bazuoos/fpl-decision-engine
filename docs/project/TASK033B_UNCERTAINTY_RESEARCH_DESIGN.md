# TASK033B — Uncertainty research design

## Status and authority

This is a research-protocol design and partial preregistration boundary. It is
not yet the executable candidate preregistration because Gate 0 provenance and
literal UM1/UA1 formulas and thresholds remain unresolved. It authorizes no
experiment run, private artifact inspection, sealed-holdout access, model
change, decision regeneration or FPL action. Task033A defines the parent
architecture. Code, schemas, immutable manifests and frozen records remain
authoritative.

The current production model remains xFP v0.1. Historical experiment modules
prove that protocols and candidate formulas exist; they do not prove the result
of a particular real run. Repository documentation says earlier conversation
reports described negative outcomes, while the exact Task009 and Task010 result
packages are not recovered. Those outcomes are therefore **REQUIRES HUMAN
CONTEXT**, not a blank slate that permits rerunning or relabelling candidates.

## Task033B1B checkpoint and proposed numerical completion

The original pending-Gate-0/unknown-result wording above and the future sequence
below are historical. The separately authorized, hash-verified sanitized Gate 0
report now records `LOCALLY_COHERENT_LEGACY_RESULTS`: both legacy experiments
have no development winner, no holdout evaluation and DO NOT PROMOTE decisions.
This is local coherence, not proof of historical execution identity or backup.

The [Task033B1B amendment](TASK033B1B_CANDIDATE_FREEZE_AMENDMENT.md) supplies the
proposed literal UM1/UA1 formulas, scores, gates and confirmation boundaries.
It explicitly proposes deferring the synthetic decision corpus and narrowing
resulting eligibility to component diagnostics. Those amendments need independent
Claude review and owner approval; neither this link nor completed Gate 0
constitutes implementation, experiment or confirmation permission.

## Research question

Can pre-deadline uncertainty in expected minutes and small-sample attacking
rates be represented in a way that:

1. improves component accuracy and calibration over xFP v0.1;
2. reduces large decision changes caused by implausibly precise inputs;
3. preserves or improves ranking quality and prediction coverage; and
4. produces a fixed, reproducible set of robustness views without using the
   target gameweek's outcome or manager preference?

This task does not ask which GW4 action would have scored more. GW4 may later be
one untouched observation in an authorized season-level evaluation; it cannot
select candidates, thresholds or views.

## Why a new protocol is required

Task009 compared scalar minutes candidates M0–M3. Task010 compared raw and
position-shrunk attacking rates S0–S3. Their 2023/24 development and conditional
2024/25 holdout rules are public, but their complete real numerical results are
not committed. 2024/25 was later reused as development for Task018D under an
experiment-specific boundary; 2025/26 remains sealed for that experiment.

Task033 needs distributions or finite uncertainty views and decision-level
stability, which the old component experiments did not establish. It must not:

- infer that an old candidate passed because its module exists;
- infer exact failure details from conversation summaries;
- reopen an old output directory to tune a replacement without authorization;
- use 2023/24 or 2024/25 as if their performance were unseen; or
- open 2025/26 merely because a different experiment named it as a holdout.

## Gate 0 — prior-result provenance

Before Task033C implements or evaluates a numerical candidate, create a
sanitized inventory of Task009 and Task010 result evidence. Access requires
separate owner authorization because the artifacts, if present, are ignored
local data.

For each experiment record only:

- expected experiment and manifest version;
- resolved artifact location or `NOT_LOCATED`;
- manifest SHA-256 and size when present;
- input-manifest identities and hashes;
- development winner identity or `none`;
- whether the conditional holdout was evaluated;
- holdout pass/fail/null and final decision;
- output file names, sizes and hashes; and
- independent verification status.

Do not place metric tables, player rows or private paths in Git or review
bundles. Verification must use the existing manifest contract and must not
recompute, overwrite or extend the experiment.

The gate has three outcomes:

| Outcome | Consequence |
|---|---|
| Verified final manifest recovered | Respect its exact decision; previously failed candidates cannot be presented as fresh promotion candidates |
| Verified evidence proves no development winner | Holdout remains unopened; exclude the exact failed family until a materially new model family, causal input or research question is frozen in a separately reviewed preregistration; elapsed time alone never resets the failure |
| Manifest not located or unverifiable | Mark outcome `UNKNOWN`; do not run old formulas or open a new holdout until a new candidate family is independently specified and reviewed |

Memory or a transcript cannot upgrade `UNKNOWN` into a verified result.

## Study stages and data boundary

Subject to Gate 0 and a later implementation review, the study uses three
strict stages:

1. **Protocol development:** synthetic fixtures and repository test fixtures
   only. These establish mechanics, schemas and leakage rejection.
2. **Development evaluation:** seasons already exposed for model development,
   presently no later than 2024/25. Exact source manifests and cutoff rules must
   be frozen before execution. This stage may reject candidates or select one
   frozen candidate package.
3. **Confirmation:** one separately authorized, untouched season whose exact
   use is approved for this experiment. The design does not assume that
   2025/26 is available merely because it exists locally. Confirmation is opened
   once, only if every development gate passes, and is never used for refitting.

No current-season partial outcomes enter development. Every prediction must be
formed from fixtures with kickoff strictly before the target deadline and lower
gameweeks than the target. Actual target outcomes join only after predictions
and their hashes are frozen.

## Evaluation populations

The primary unit is player-gameweek, aggregated from eligible fixtures using
the repository's blank and double-gameweek rules. Report natural coverage and
exact common-pair comparisons for these non-overlapping or explicitly
overlapping diagnostics:

- all complete player-gameweeks;
- position: GK, DEF, MID and FWD;
- prior minutes: 0, 1–90, 91–270, 271–450, 451–900, 901+;
- previous-GW minutes: 0, 1–29, 30–59, 60–89, 90+;
- availability: available, doubtful/chance-limited, forced zero;
- fixture count: blank, single, double+;
- historical-universe status: established, new entrant;
- promoted-team status; and
- material-decision role once a public synthetic decision corpus exists:
  ROLL starter, ROLL bench, transfer-out, transfer-in and captain candidate.

Actual minutes may define evaluation strata only after prediction publication;
they may never be an input feature.

## Candidate-family freeze

Task033B freezes the controls and the form of permissible new candidates, but
deliberately does not invent coefficients before Gate 0.

### Controls

- `U0`: exact xFP v0.1 production behavior.
- Every recovered, verified Task009/010 final candidate is retained under its
  original identity and result status as a comparator only.
- Existing decision-reliability percentile exclusion/cap and minimum-minutes
  views remain diagnostics; they are not fitted models.

### New expected-minutes candidate requirements

One future `UM1` candidate may be specified after Gate 0. It must output a
bounded probability mass over `0`, `1–29`, `30–59`, `60–89`, and `90+` minutes,
plus its expectation. It may use only time-valid prior starts, substitute
appearances, minutes, fixture count and official pre-deadline availability.
Team news interpreted in chat, future lineups and actual target minutes are
forbidden. Every smoothing prior, window, coefficient, cap and fallback must be
fixed in a reviewed Task033B amendment before any development result is read.

### New attacking-rate candidate requirements

One future `UA1` candidate may partially pool xG and xA separately toward
time-valid position priors. It must preserve missing event values as missing,
never use target outcomes, and report effective prior minutes. Role priors are
excluded from the first study because no frozen role taxonomy exists. Every
prior population, pooling formula, strength, cap and fallback must be fixed in
the same reviewed amendment before evaluation.

### Combination policy

Evaluate `UM1` and `UA1` independently against U0 first. Their combination may
be evaluated only if both independently clear all development gates. No grid of
minutes × rate variants is allowed. This prevents combinatorial selection and
keeps attribution intelligible.

## Metrics

### Minutes

- MAE and RMSE of expected minutes;
- multiclass log loss and Brier score for the five minute bands;
- calibration error by predicted start and appearance probability;
- appearance-point MAE; and
- natural and common-pair coverage.

### Attacking components

- goal, assist and combined-attacking MAE/RMSE;
- bias and Spearman rank correlation;
- probabilistic log score if the candidate emits event distributions;
- calibration by predicted-event decile; and
- coverage and rate change by prior-minute band and position.

### Total modeled scope

- appearance+goal+assist MAE/RMSE, bias and Spearman;
- strict top-10/25/50 overlap using deterministic player-ID tie-breaking; and
- per-gameweek error distributions, not only pooled means.

### Decision-level diagnostics

No private historical manager squads are available as a representative corpus.
Task033C must therefore not claim population decision regret from one owner's
live decisions. It may build a separate, versioned **public synthetic decision
corpus** only after Codex documents a construction rule fixing squad sampling,
budget, team limits, free-transfer state and target gameweeks without looking at
candidate outcomes, Claude independently reviews it, and the owner approves it
before construction.

On that corpus report:

- action agreement with U0;
- view-by-view winner and objective margin;
- realized one-gameweek modeled-scope regret after outcomes are joined;
- transfer frequency and false-positive transfer rate under the frozen loss;
- captain agreement and realized captain regret separately; and
- computation time and peak memory by candidate count and view count.

These are restricted pseudo-backtest diagnostics, not proof of real-manager
benefit or total FPL performance.

## Dependency-aware uncertainty

Player-gameweek rows are not independent: the same player recurs across targets,
while players in one target gameweek share fixtures, availability shocks and
the football environment. Every candidate-control comparison must use a
**paired two-way cluster bootstrap**, with player identity and target gameweek
as the clustering dimensions. Candidate and control retain identical
resampling weights in every replicate so the estimate is a paired metric
difference.

Within each registered season, independently resample unique player IDs and
unique target gameweeks with replacement, apply the product of their
multiplicities to each player-gameweek row, and recompute the complete metric,
including fixed calibration bins, inside every replicate. Report the paired
point difference, interval and effective unique-player and unique-gameweek
counts. An IID row-level interval is forbidden. Task033B1 must freeze the seed,
replicate count, interval method, confidence level, small-cluster behavior,
minimum cluster counts and any serial-block diagnostic before results are read.
When development spans more than one season, Task033B1 must also freeze how
season-specific bootstrap metrics are combined, including season weights; it
may not choose between pooled and season-balanced results after inspection.

For synthetic decision-corpus metrics, target gameweek is the resampling unit;
all deterministic squad seeds within a sampled gameweek remain together. This
prevents many squads exposed to the same outcomes from creating false
independent evidence.

An improvement gate passes only when its registered interval bound clears the
positive threshold. A non-inferiority gate passes only when the adverse bound
remains inside its registered margin. Point estimates alone cannot pass. Too
few clusters or failed interval construction produces insufficient evidence and
fails the relevant gate closed.

Simultaneous subgroup non-regression gates form registered metric families.
Task033B1 must freeze a family-wise procedure, such as bootstrap maximum-
statistic one-sided simultaneous bounds, plus the family membership and error
rate. Unadjusted per-stratum intervals cannot be substituted after results are
known. This control prevents an expanding diagnostic table from creating an
arbitrary promotion barrier while preserving fail-closed regression checks.

## Development gates

Exact numerical thresholds for UM1/UA1 must be added in the candidate amendment
before evaluation. The amendment must include, at minimum:

- a positive minimum improvement in the candidate's primary proper score;
- no material worsening in modeled-scope MAE/RMSE and bias;
- bounded Spearman and top-N overlap loss;
- at most one percentage point natural-coverage loss;
- no material regression in any sufficiently sized position or availability
  stratum;
- the registered family-wise subgroup procedure and error rate;
- decision-regret and unnecessary-transfer limits if the synthetic corpus has
  been independently accepted; and
- deterministic simplicity tie-breaking, though only one new candidate per
  component is preferred.

“Material,” minimum sample sizes and every number above must be literal values,
not reviewer discretion, before results are opened. Failure of any required gate
means `DO_NOT_CONFIRM`; no threshold may be relaxed afterward.

## Confirmation gates

Only one frozen component candidate or one independently qualified combination
may enter confirmation. It is applied without fitting or threshold changes and
must:

- pass the same development thresholds on exact common pairs;
- preserve the registered minimum coverage;
- show no preregistered material subgroup regression;
- stay within the computation budget; and
- reproduce its complete artifact chain independently.

Failure means `DO_NOT_PROMOTE`. Passing means only `ELIGIBLE_FOR_TASK033D
DIAGNOSTIC_INTEGRATION`; production promotion still requires Task033E and a
separate owner decision.

## Robustness-view admission

A view becomes mandatory only through one of these paths:

1. it is a deterministic boundary/sensitivity view justified independently of
   predictive performance and preregistered before the evaluated decision; or
2. its component model passes the complete development and confirmation gates.

Mandatory views, their ordering and minimum transfer/captain margins are frozen
in a versioned policy. Failed models, post-result scenarios and hand-selected
external projections cannot become mandatory. Diagnostic views remain clearly
labelled and cannot contribute to `STABLE`.

## Artifacts

Task033C must publish immutable, no-overwrite artifacts containing:

- protocol and candidate-amendment hashes;
- code revision and environment identity;
- every source manifest/file hash and causal cutoff;
- prediction-before-outcome publication hashes;
- candidate definitions and parameters;
- population counts, missingness and all registered metrics;
- gate results including failures and nulls;
- selected candidate identity or `none`;
- confirmation access receipt and result when authorized;
- synthetic decision-corpus identity when used;
- runtime and memory evidence; and
- a final decision that cannot imply production promotion.

Sanitized review material includes metadata, hashes, aggregate metrics and gate
results only. It excludes raw player rows, private manager state and local
identifying paths.

## Computational budget

Before Task033C implementation, synthetic benchmarks must demonstrate bounded
work at the current operational scale. The default ceiling is:

- at most 700 public players per gameweek;
- at most 3,000 legal ROLL/one-transfer candidates per decision case;
- U0 plus no more than three evaluated numerical variants;
- no more than 12 mandatory robustness views;
- 60 seconds and 2 GiB peak resident memory for one decision-level robustness
  evaluation on the owner macOS arm64 environment; and
- deterministic failure without partial publication when a ceiling is exceeded.

These are engineering safety limits, not evidence of predictive adequacy.

## Stop conditions

Stop before implementation or evaluation when:

- Gate 0 is incomplete;
- the proposed candidate amendment lacks literal formulas or thresholds;
- a source or manifest hash does not validate;
- a season's permission or sealed status is ambiguous;
- prediction construction cannot be separated from outcome access;
- the synthetic decision corpus is outcome-selected;
- its construction rule lacks independent adversarial review and owner approval;
- a required population is too small under its preregistered rule;
- the current design, candidate amendment or implementation lacks its required
  independent adversarial review;
- the active GW4 operational lifecycle would be disturbed; or
- current-season evidence is proposed as a tuning signal.

## Exact next task

Task033B1 is a **design amendment and provenance plan**, not an experiment. It
should define the sanitized Gate 0 inventory procedure and, using repository
facts plus independently justified statistical assumptions, freeze literal UM1
and UA1 formulas, parameters and numerical gates. Claude reviews that amendment
before any private result inventory or implementation is authorized.

## Acceptance criteria

- Old experiment rules and real results are not conflated.
- Previously exposed seasons are not described as untouched.
- No sealed season is opened or implicitly authorized.
- Minutes distributions and attacking-rate pooling remain separable and causal.
- Combination search is bounded and conditional.
- Proper scoring, calibration, coverage, ranking and decision diagnostics are
  all registered without claiming more than the available corpus supports.
- Thresholds must become literal before evaluation, with no after-result edits.
- Robustness views cannot be chosen to support a preferred live action.
- Production xFP v0.1 and every historical decision remain unchanged.
