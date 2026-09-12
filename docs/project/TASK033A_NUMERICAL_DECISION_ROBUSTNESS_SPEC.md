# TASK033A — Numerical decision robustness design

## Status and authority

This document designs a future numerical robustness foundation. It does not
change xFP, promote an experiment, inspect a sealed holdout, regenerate an
artifact, alter the active GW4 monitor, journal a decision or authorize an FPL
action. Existing code, schemas, frozen artifacts and Git history remain
authoritative.

The motivating GW4 observation is historical operational context: a trusted
Engine v1 transfer recommendation carried an extreme attacking-rate warning,
and a separately reported sensitivity audit found that plausible rate and
minutes assumptions materially reduced its advantage. That observation may
motivate this design, but must not become training data, a retrospective
promotion justification or a rewritten pre-deadline belief.

## Problem

Engine v1 produces deterministic legal decisions, but its point estimate can
look more certain than its inputs justify. Current production behavior:

- derives expected minutes primarily from recent playing time;
- uses observed attacking rates without production shrinkage;
- does not adjust for opponent or home/away strength;
- models appearance, goals and assists only;
- maximizes starting-XI xFP plus one additional captain copy;
- does not value substitutions, vice-captain fallback or future transfers; and
- reports diagnostics separately without changing the official ranking.

The repository contains research implementations for minutes, attacking-rate
stabilization, opponent strength and previous-season priors. Their existence
does not prove predictive improvement or authorize production use. A stronger
foundation needs an explicit path from frozen research to versioned production
inputs, and must distinguish a high point estimate from a decision that remains
best under credible uncertainty.

## Decision

Add a versioned **decision robustness layer** after candidate scoring and before
an official action is published. It evaluates the same legal ROLL and transfer
candidates under a preregistered set of numerical views. It never invents a new
candidate, changes legality, consumes human preference or edits the base model.

```text
frozen public inputs
  -> versioned production projection model
  -> legal candidate set and base point estimates
  -> versioned robustness views over the same candidates
  -> stability classification and evidence
  -> publication policy
```

The base-model winner remains visible. An official `TRANSFER` is published only
when it passes the frozen robustness policy. Otherwise the outer operational
publication gate reports `ROBUSTNESS_REVIEW_REQUIRED`; it is not silently
converted to ROLL. This state is never written into the existing closed
`GameweekDecision.action_type`, which remains `ROLL` or `TRANSFER`. It gates
whether a new GameweekDecision and final operational manifest may be emitted at
all. The owner may still act manually, but no prose layer may present that
action as the engine's verified recommendation. This robustness publication
gate is distinct from `decision-eligibility-policy-v1`, which governs player
and candidate admission.

Captaincy receives its own stability result. A stable transfer does not imply a
stable captain, and captain uncertainty cannot change transfer ranking unless a
future contract explicitly and prospectively defines that coupling.
The existing transfer-side `xi_only_without_captain_amplification` sensitivity
remains a mandatory transfer-stability view because captain amplification can
change which transfer wins. The separate captaincy channel additionally asks
whether the selected captain is stable; it does not replace that transfer view.

## Evidence boundaries

Keep these layers separate:

1. **Historical human context** records what was believed and why; never scores.
2. **Research evidence** uses preregistered historical populations and sealed
   evaluation; never becomes production merely by existing.
3. **Production projections** are immutable, schema-versioned point estimates.
4. **Robustness evidence** re-scores the exact candidate set using only frozen,
   versioned numerical views.
5. **Current football evidence** may enter only through a defined, attributable
   production feature contract; chat interpretation is not a numeric override.

No live GW outcome may tune the model or thresholds that produced that GW's
decision. Promotion requires a later, independent decision record.

## Numerical view families

Task033B must specify each view completely before evaluation, including its
population, priors, constants, missing-data behavior and identity.

### Expected minutes

Represent minutes as a bounded distribution or an explicit finite scenario set,
not an unlabelled replacement scalar. Candidate views should cover credible
start, cameo and non-appearance mass using only pre-deadline evidence. Missing
evidence must widen uncertainty or fail closed; it must not imply 90 minutes.

### Attacking rates

Evaluate preregistered partial pooling toward position and, if earned by
evidence, role priors. Shrinkage strength must depend only on prior minutes and
the frozen research design. Percentile caps may be diagnostics or comparator
views, but cannot be selected after seeing the desired decision.

### Fixture context

Opponent and home/away adjustments require time-valid team-strength inputs and
explicit treatment of promoted teams, sparse seasons and manager changes. Team
identity and fixture provenance must be hash-bound.

### Scoring scope

Clean sheets, conceded-goal deductions, saves, cards, bonus and defensive
contributions require separate contracts and validation. Task033A does not fold
them into the current model merely to make it appear complete.

## Stability classifications

The future contract should distinguish:

- `STABLE`: the same action wins every mandatory view and clears the frozen
  minimum advantage in each;
- `MARGIN_SENSITIVE`: the same action wins, but at least one mandatory view is
  below the minimum advantage;
- `RANK_SENSITIVE`: mandatory views select different actions;
- `INPUT_INCOMPLETE`: a material player in the incumbent ROLL squad or any
  transfer alternative lacks required uncertainty inputs;
- `ROBUSTNESS_NOT_AVAILABLE`: compatible robustness evidence was not produced.

Only `STABLE` may support an official transfer by default. Exact thresholds are
not chosen in this design. They must be preregistered and justified using
decision-level loss, calibration and turnover, including the opportunity cost
of an unnecessary transfer. Future-transfer value remains out of scope until it
has a numerical model; documentation must state that omission.

For captaincy, report the top candidate, runner-up, base margin, view-by-view
winner and stability class. Do not use ownership or “differential” value in the
expected-points objective.

## Artifact and trust-chain requirements

A robustness artifact must bind:

- base projection, candidate and decision artifact hashes;
- model and policy identities;
- ordered mandatory and diagnostic view identities;
- per-view candidate objectives and winner;
- material input flags and missingness;
- stability classification and publication eligibility;
- code revision, creation time, target season/gameweek and deadline; and
- source-artifact hashes needed to reproduce every view.

The operational manifest and trusted reader must validate this artifact before
displaying any robustness claim. Browser/application code may render it but may
not recompute it. Artifacts are immutable and no-overwrite.

Compatibility must be explicit. A new final-manifest schema version may bind a
robustness artifact, while the trusted loader must accept the enumerated legacy
v1 and new versions, validate each by its own closed schema, and map the
structural absence of robustness in a valid v1 manifest to
`ROBUSTNESS_NOT_AVAILABLE`. It must not mutate, migrate or republish old bytes.
Unknown versions remain rejected. Absence must never be represented as
`STABLE`.

## Research and promotion gates

1. Freeze candidate models, baselines, datasets, splits, metrics and promotion
   thresholds before opening evaluation results.
2. Use rolling, deadline-respecting historical evaluation with no future data.
3. Compare against current xFP v0.1 and simple honest baselines.
4. Measure point accuracy, minutes calibration, probabilistic calibration,
   decision regret, action stability and transfer frequency.
5. Report performance by position, sample size, promoted-team status and
   availability class; aggregate improvement cannot hide material regressions.
6. Preserve failures and null results. Do not tune on sealed holdouts.
7. Require independent adversarial review of data lineage, leakage controls,
   statistics and implementation.
8. Promote only through a separate human-approved decision record, versioned
   schemas, migration behavior, tests and green CI.

One component may be promoted without the others only when its evaluation and
contract are independently valid. A successful minutes model does not authorize
attacking-rate or opponent-strength changes.

## Failure behavior

| Failure | Required result |
|---|---|
| Missing or incompatible robustness artifact | `ROBUSTNESS_NOT_AVAILABLE` |
| Hash, schema or model-identity mismatch | Reject trusted read |
| Material uncertainty input absent | `INPUT_INCOMPLETE` |
| Mandatory views disagree | `RANK_SENSITIVE` |
| Winner survives but minimum margin does not | `MARGIN_SENSITIVE` |
| Captain views disagree or miss the margin | Publish the captain stability evidence even when transfer publication remains eligible |
| Research result unavailable or inconclusive | Preserve current production model |
| Deadline passes during prospective production | Publish no new prospective decision |

Warnings remain evidence. They cannot be dismissed by
`official_recommendation_unchanged=true` when the frozen policy defines them as
material to eligibility.

## Proposed delivery slices

### Task033B — preregistered uncertainty research design

Inventory existing experiments and freeze the first bounded comparison. Start
with expected-minutes uncertainty and attacking-rate stabilization. Do not add
full scoring scope or tune from the GW4 result.

### Task033C — research execution and report

Implement or reuse isolated experiment paths, verify leakage controls, execute
only the authorized development evaluation, and obtain independent review. A
null result is acceptable.

### Task033D — robustness contract and mechanical layer

Define schemas and compute view-by-view stability over synthetic/frozen inputs.
This slice may remain diagnostic if no new projection component has earned
promotion. Before implementation it must set a bounded computational budget
for candidate count, mandatory views, runtime and memory, and test that the
expected operational candidate population cannot trigger unbounded work.

### Task033E — separately authorized production promotion

Only after evidence and review, version the chosen production component and
publication policy. Re-run complete trust-chain and operational tests. No live
decision regeneration is implied.

### Later independent scopes

Opponent strength, complete FPL scoring, substitution/vice fallback, future
free-transfer value and multi-gameweek/chip optimization each require separate
design and evidence. Captaincy robustness may reuse common mechanics but needs
its own acceptance metrics.

## Task033A acceptance criteria

- The design preserves legality, optimizer and trusted-reader authority.
- Historical context, research, production, robustness and current evidence are
  unambiguously separated.
- No existing experiment is described as successful or promoted without a
  repository-established result.
- Fragile recommendations cannot be silently presented as robust.
- Captaincy stability is separate and numerical.
- Missing old robustness evidence remains honest and backward compatible.
- Promotion is prospective, versioned, independently reviewed and reversible by
  selecting the prior model version; historical artifacts are never rewritten.
- Task026C, live execution, current artifact changes and sealed evidence remain
  outside scope.

## Explicit non-goals

- Personalizing recommendation rankings from manager style.
- Using ownership, fear, narrative or reviewer preference as model features.
- Retrofitting GW4 with a result-aware model.
- Automatically executing an FPL action.
- Claiming probabilistic confidence from a handful of hand-selected scenarios.
- Treating a warning, audit or external projection site as production truth.
