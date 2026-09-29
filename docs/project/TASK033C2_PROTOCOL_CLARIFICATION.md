# Task033C2 protocol clarification

## Status, authority and relationship to the freeze

**Owner-approved clarification; documentation-only submission for independent
Claude review.** Base commit:
`5e7f3c02592e9d09bd6e2b2e1c6d85b8691bbf08`.

The owner reports having reviewed Claude's prior protocol ruling and explicitly
approves the rules recorded below. The owner's current instruction supplies
authority for this clarification. The prior ruling is review context, not
repository authority or proof that an implementation is safe.

[Task033B1B](TASK033B1B_CANDIDATE_FREEZE_AMENDMENT.md) remains the governing
protocol except where this clarification resolves the listed ambiguities.
This document does not assert that the original text was already unambiguous.
The [blocker report](TASK033C2_PROTOCOL_BLOCKER.md) remains byte-identical as
the historical explanation of why implementation stopped. Its SHA-256 is
`3a54fbc1681a6a79e581adb7f06e24ab74e0236d9f5d4fc4566f748ae60e66f9`.

This continuation authorizes documentation and review packaging only. It does
not implement Task033C2 or Task033C3, authorize an experiment, or resolve a
confirmation season. A SAFE verdict on this clarification would concern only
the clarification; no C2 implementation exists to certify.

## 1. Serial-bootstrap block coupling

For each serial-bootstrap replicate, draw an independent ten-block gameweek
sequence for each development season. No block-start draw is shared between
seasons. The seasons' gameweeks are different football events; matching
gameweek numbers do not create a cross-season dependence relationship.

One player-multiplicity vector remains shared across seasons, as required by
Task033B1B. This shared player dependence does not imply shared gameweek-block
draws. The development seasons remain exactly 2023/24 and 2024/25.

## 2. Shared player multiplicities and canonical code ordering

Let `U` be the number of distinct verified player codes in the union across
the development seasons. Canonicalize those verified codes using ascending
bytewise UTF-8 ordering. This orders player codes for the shared serial draw;
it does not replace Task033B1B's increasing element-ID order for within-season
primary crossed draws or its ranking tie rules.

For each serial-bootstrap replicate, draw exactly `U` player indices with
replacement from `[0, U)`. Count each index's occurrences to obtain its
verified player code's multiplicity. Apply that multiplicity in every season
in which the code appears. Do not draw separate season-specific multiplicities
for a shared code. A missing identity bridge still prevents the serial gate;
it does not permit independent season-specific player resampling.

For confirmation, `U` is the number of distinct verified player codes in that
single season, and the same rule applies. This describes the rule only; the
confirmation season remains unassigned and unopened.

## 3. RNG construction and exact reference draw order

Use a fresh NumPy `Generator(PCG64(seed))` for each resampler. The existing
seeds and replicate counts are unchanged:

| Resampler | Development seed | Confirmation seed | Replicates |
|---|---:|---:|---:|
| Primary crossed player/gameweek | 330331 | 330332 | 9,999 |
| Serial player/moving-gameweek-block | 330431 | 330432 | 9,999 |

The runtime and NumPy versions remain bound by the execution manifest before
outcomes as required by Task033B1B. The reference order below fixes consumption
of each resampler's RNG stream across replicates and seasons.

### Serial bootstrap

For replicates `b = 1,...,9999` in order:

1. Draw the shared player indices with
   `integers(0, U, size=U, dtype=int64)`, where `int64` is NumPy's signed
   64-bit integer type and indices refer to the code ordering in section 2.
2. Process seasons in lexicographic season order.
3. For each season independently, draw its block offsets with
   `integers(0, 34, size=10, dtype=int64)`.
4. Convert each offset to the block start `2 + value`.
5. Expand each start into its four consecutive gameweeks, in increasing
   order within that block.
6. Preserve block draw order, concatenate the ten blocks, and retain the
   first 37 gameweek positions.

The ten starts are drawn with replacement. The 34 possible starts are GWs
2 through 35, each yielding a block within GWs 2-38. Count gameweek occurrences
in the retained positions to obtain that season's gameweek multiplicities.
The player draw precedes all season-block draws in that replicate; the next
replicate begins only after those season-block draws.

The serial row weight is shared player-code multiplicity times that season's
gameweek multiplicity. Keep the paired candidate/control outcomes and
predictions together. The same weights apply to all evaluated candidates,
endpoints and overlapping subgroups; do not draw again by metric or stratum.

### Primary crossed bootstrap

For each replicate in order, process seasons in lexicographic order. Within
each season `s`, draw player indices using exactly:

```python
generator.integers(
    0,
    P_s,
    size=P_s,
    dtype=np.int64,
)
```

Then draw gameweek indices using exactly:

```python
generator.integers(
    0,
    G_s,
    size=G_s,
    dtype=np.int64,
)
```

Here `generator` is the primary resampler's NumPy generator and `np` denotes
NumPy. The resulting counts are the player and gameweek multiplicities for
that season and replicate. Preserve the frozen seeds, replicate order and
per-season player-then-gameweek draw order. Batching or streaming is permitted
only when proven byte-for-byte identical to this reference stream.

These are independent uniform draws with replacement, using the canonical
increasing player-ID and increasing GW order and full registered nonblank
universe already specified by Task033B1B. The row weight remains player
multiplicity times gameweek multiplicity, shared across candidates, endpoints
and strata. Ranking eligibility below does not redefine this resampling
universe.

Batching or streaming is permitted only when proven byte-for-byte equivalent
to this per-replicate reference order. RNG ordering must never change after
outcomes are observed. No seed, resampler, replicate count or fallback is
added by this clarification.

## 4. At least 30 evaluable gameweeks

The requirement for at least 30 evaluable gameweeks applies to the original
observed evaluation population in each season, before bootstrap resampling.
It does not require each moving-block replicate to contain 30 distinct
gameweeks. Distinct retained-gameweek counts across replicates remain
published diagnostics, including the existing minimum/median/maximum
summaries.

The separate original-population requirements in Task033B1B remain in force,
including coverage, completeness, common-row counts, player counts and subgroup
minimums. This clarification does not relax undefined-replicate failure rules.

## 5. Frozen ranking and top-N gameweek membership

For the required Spearman and top-10/25/50 endpoints, freeze the eligible
gameweek set separately for each registered candidate-versus-U0 comparison:

- UM1 versus U0;
- UA1 versus U0; and
- UM1UA1 versus U0, only if conditional combination evaluation is authorized
  by the component gates.

For each pair, use its exact original common evaluation membership and freeze
the set before bootstrap resampling. Candidate packages may therefore have
different eligible gameweek sets when their natural completeness differs.
Never transfer eligibility from one candidate to another. This clarification
does not permit evaluation of UM1UA1 unless both components first pass every
required gate.

A gameweek is eligible for that pair only when its original common population
has:

- at least 50 distinct complete common players;
- nonconstant realized modeled-scope points; and
- nonconstant predictions wherever needed for both sides of the paired
  statistic.

For each pair, every season must contain at least 30 ranking-eligible gameweeks.
If a season has fewer than 30 ranking-eligible gameweeks, the required ranking
endpoints are `INSUFFICIENT_EVIDENCE`, and therefore the applicable global-family
gate fails closed. This does not create a separate ranking family or change
multiplicity allocation.

Average ranking and top-N metrics only over this frozen eligible set. In the
original estimate, eligible GWs receive equal weight; in each bootstrap
replicate, use GW multiplicity as specified by Task033B1B. For each pair,
publish included and excluded gameweek identities, objective reasons and a
canonical membership hash. Never exclude a gameweek based on whether its
result helps or harms the candidate.
The membership is fixed before resampling; it is not reselected in a replicate.

Within an eligible positive-weight gameweek, retain the specified player-copy
interpretation, Spearman midranks and deterministic strict top-N tie rules.
If it has too few copied common rows for the required top-N endpoint, or any
other required statistic is undefined, the replicate is undefined and the
applicable gate fails closed. Do not redraw, omit that replicate, replace an
undefined statistic, or remove the GW from the frozen set. Zero-weight GWs
are not evaluated, and an empty required denominator still fails closed.

These rules apply to both resampling systems. They change neither the
endpoint families nor the thresholds and do not filter other metrics through
the ranking-eligible GW set.

## 6. Non-circular moving-block edge effect

Preserve the specified non-circular block construction. Do not introduce
circular blocks, reweight edge gameweeks, center the bootstrap distribution,
or otherwise repair unequal inclusion probabilities near the boundaries.

The unequal inclusion probability of boundary gameweeks is a claim
limitation of the frozen engineering resampling law. Passing its sensitivity
gate does not establish absence of longer dependence or universally valid
coverage. The simultaneous basic-bound formula, scales, order-statistic
indices and strict comparisons in Task033B1B remain unchanged.

## 7. C2 resource-gate interface

Task033C2 receives resource-gate status as a strict supplied input. It does
not measure C3 execution wall time or RSS itself. Missing, malformed,
unsuccessful or inapplicable required resource evidence fails selection
closed. C2 must never infer resource success from statistical success.

This interface clarification does not reduce the resource ceilings or their
scope, including I/O and publication. It supplies no evidence that a ceiling
has been met. Execution measurement and publication remain within the
separately authorized [C3 boundary](TASK033C_SYNTHETIC_UNCERTAINTY_IMPLEMENTATION.md).
No input schema or resource-measurement implementation is added here.

## Preserved boundaries and review scope

All existing candidates, formulas, metrics, endpoint families, thresholds,
seeds, alpha allocations, confirmation restrictions and decision-corpus
deferral remain unchanged outside the explicit clarifications above. Unused
alpha is not recycled. Every applicable statistical gate must still pass
both resampling systems; the conditional UM1/UA1/UM1UA1 selection priority
is unchanged.

No new season, endpoint, model, fallback, tolerance, alpha allocation or
adaptive choice is introduced. No real, private, sealed, historical-result,
manager or confirmation data may be inspected for this continuation. It does
not run Gate 0, an experiment or a synthetic statistical feasibility drill.
Production xFP, decisions, journals, monitors, schedules and PR #2 are outside
scope. Nothing is staged, committed or pushed by this documentation task.

The decision corpus remains `NOT_EVALUATED`; the eventual maximum eligibility
remains `ELIGIBLE_FOR_TASK033D_COMPONENT_DIAGNOSTICS`, subject to all governing
confirmation and review requirements. This document grants no candidate
eligibility or implementation approval and makes no empirical or operational
success claim. Hashes establish byte identity only.

Independent review under [AI_WORKFLOW](AI_WORKFLOW.md) must return SAFE or
NOT SAFE for this clarification alone, addressing each rule above and its
consistency with Task033B1B. Review must not certify C2 implementation because
none exists.
