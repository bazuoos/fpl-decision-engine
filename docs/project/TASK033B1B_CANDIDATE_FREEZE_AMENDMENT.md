# TASK033B1B — Candidate-freeze amendment

## 1. Status, authority and evidence checkpoint

**Proposed numerical freeze for independent Claude review; not approved for
implementation or execution.** Base commit:
`251a4c484b745346fdb1a46c674a13add0c7744c`. This amendment completes the numerical
specification proposed by [Task033B](TASK033B_UNCERTAINTY_RESEARCH_DESIGN.md),
subject to review and owner approval. No empirical superiority is asserted.
All constants below are engineering choices/statistical assumptions fixed after
learning the legacy rejection status, not fitted estimates or historical
preregistration. No additional result tables were read in preparing this text.

The objective remains expected FPL points within the declared modeled scope.
Historical human thesis, trusted engine artifacts, and current football/manager
evidence remain separate. Ownership, the owner's GW4 action, rejected transfer,
captaincy and subsequent outcomes are neither inputs nor selection criteria.
Pre-deadline beliefs and immutable decisions must never be rewritten.

### Gate 0 is complete within its narrow assurance

The owner reports one separately authorized opaque capture and one verification,
followed by Claude's SAFE review with no required changes. For this amendment,
the supplied sanitized bundle and its embedded report were independently hashed:

| Identity | SHA-256 |
|---|---|
| Sanitized Gate 0 review bundle | `1a3608a7851c187ddb9a5a6c8b165a4f6d797e2704b5e536a75611aac7ae01f6` |
| Embedded canonical report, including its final newline | `ea87688561b4963410df6431e3b343d1e7533bc3f0503696730dbe99e5a69c06` |
| Tracked verifier source at base | `2247272398c515736dc051ba990045f4abb98f130154139b4f8c126bf28f8a17` |

The combined status is `LOCALLY_COHERENT_LEGACY_RESULTS`:

| Slot | Development winner | Holdout evaluated | Holdout passed | Recorded final decision |
|---|---|---|---|---|
| Minutes | null | false | null | DO NOT PROMOTE — KEEP v0.1 MINUTES |
| Attacking rates | null | false | null | DO NOT PROMOTE — KEEP v0.1 ATTACKING RATES |

These are present local bytes coherent with reviewed contracts and declared
output hashes. They establish neither historical generator identity nor
creation-time immutability, independent backup or production eligibility.
Matching the report's verifier-source hash to tracked source does **not** prove
which code historically executed. Stronger language in the supplied review is
not adopted. Private result/receipt hashes remain declarations; those files,
real experiment directories, Parquet outputs and sealed inputs were not opened.
**NO VERIFIED OFFSITE BACKUP.** Gate 0 must not be rerun for this task.

The base is the reviewed verifier implementation. Its reported validation is
26 focused and 655 full Python tests; those tests were not rerun here. Read-only
GitHub inspection on 2026-09-12 confirmed draft PR #2 open at this base and CI run
34684159577 successful; its test job ran 08:47:11–08:51:50 UTC (4m39s).
CI is mechanical evidence, not statistical approval.

### Explicit reconciliation and proposed amendments

Task033B's unknown-result/Gate-0-pending passages and Task033B1's future execution
sequence describe an earlier checkpoint. The table above supersedes those status
statements only. The old families remain rejected; their uncommitted metrics
remain uninspected. The earlier CURRENT_HANDOFF, PROJECT_STATE and ARCHITECTURE
statements about two stale inactive schedules do not describe the later schedule
identified by the owner. Its current lifecycle was not inspected; elapsed time
proves neither activity nor completion. Preserve operational isolation.

The following changes to the governing design are explicit proposals requiring
approval with this amendment:

1. Use Brier score as UM1's primary proper score, with log loss diagnostic:
   exact U0 and hard-zero forecasts can assign zero probability to realized
   events, making finite log-loss intervals impossible without changing U0.
2. Permit a generalized-Bayes mixture for UA1, not just a single linear
   shrinkage formula; define a count-distribution score separately from the
   fractional xG/xA update. This supplies material novelty, not new names for S1–S3.
3. Defer the synthetic decision corpus. Passing this study permits at most
   component diagnostic integration after confirmation. It cannot qualify a
   predictive mandatory decision view, `STABLE` claim, publication veto or
   Task033E promotion until separately designed decision-level validation passes.
4. Require an additional serial-block sensitivity gate and season-specific
   non-inferiority bounds. These strengthen the parent comparison gates.
5. Statistical resampling belongs to offline research, outside the 60-second
   per-decision budget. The operational budget is unchanged; the bounded
   offline budget in section 10 is additional, not an exemption for deployment.

Approval freezes formulas; it does not supply missing source manifests,
confirmation permission or evidence of computational feasibility.

## 2. Data contract and temporal population

Development seasons are **exactly 2023/24 and 2024/25**, target gameweeks 2–38,
equal season weights 1/2. Both seasons have been exposed to earlier development;
neither is untouched confirmation. No season substitution, pooled reweighting,
current-season partial outcome or cross-season fitted hyperparameter is allowed.
Use corrected `historical-v3.1` contracts for these seasons in a new isolated
research dataset; do not alter v2/v3 artifacts or silently relabel the legacy
v2 experiments. If the exact corrected sources cannot be authorized and bound,
stop before development; do not fall back to a different version.

The primary universe is every GK/DEF/MID/FWD player in each target's verified
pre-deadline player-state snapshot. Assistant-manager and unknown positions are
excluded by contract. Player key is `(season, element_id)`; verified `code`
bridges identify the same player across seasons only for dependence checks,
never for carrying model history across seasons. Duplicate or conflicting
identity mappings stop the dataset build. No end-of-season universe is used.

All history records must satisfy both `gameweek < target_gameweek` and
`kickoff_time < target_deadline`. Only same-season history is used. Source-GW
universe membership is required for an observation to enter a window; a player
not then registered contributes no artificial zero. A team blank contributes no
fixture observation. An explicit row recording zero minutes is an observation.
An expected row missing where the player was in the source universe and a
causally eligible team fixture existed is corruption, not a blank or a new
entrant: fail the build. Postponed lower-GW fixtures at/after deadline are excluded.
Source and target team changes follow the recorded pre-deadline states and
fixture identities; there is no reset, club-name heuristic or inferred lineup.

Historical fixture assignments are finalized context and marked
`fixture_assignment_verified_predeadline=false`; source schemas do not establish
perfect historical deadline replay or creation-time availability of all later
archived performance values. Retain `restricted_pseudo_backtest`. Do not infer
prospective scheduling knowledge from a logical kickoff cutoff. Any claim beyond
that classification requires new attributable as-of evidence and a new review.

### Feasibility from tracked fields (not a private-data completeness assertion)

| Requirement | Tracked source/contract | Rule or gap |
|---|---|---|
| Fixture minutes, starts, xG, xA | `historical.py: PLAYER_FIXTURE_SCHEMA` | Reconstruct causal windows; aggregate feature table alone lacks the joint start/minute distribution |
| Player universe, position, club, status/chance | `PREDEADLINE_SCHEMA` and `FEATURE_SCHEMA` | Bind snapshot time, target and source hashes; never parse free-text news as a feature |
| Previous-GW null/blank/new-universe distinctions | `_previous_context`, `FEATURE_SCHEMA` | Preserve recorded context; no missing-to-zero repair |
| U0 mechanics and scoring | `predictions.py`, `historical_backtest.py` | Historical adapter must prove numerical/nullable-field parity on synthetic fixtures |
| Legacy definitions and rejection contract | `historical_minutes_experiment.py`, `historical_attacking_rate_experiment.py`, Task033B1 | Comparator identities only; no new legacy runs |
| Promoted-team diagnostic | No promoted flag in these schemas | Mandatory source-manifest metadata map from public pre-season membership, independently checked before predictions; never infer from outcome performance |
| Starts and event-field completeness | Nullable fixture fields exist | Actual completeness unknown here; apply explicit failure/missingness rules below |

Promoted means a target club absent from the previous season's Premier League
membership. Freeze season/team mapping and source hashes through separately
authorized public metadata collection before execution; missing/ambiguous mapping
blocks study publication. This metadata is for stratification only, not a model
input. No live FPL refresh is authorized by this amendment.

### Source-manifest freeze before any execution

A later owner-authorized preparation must publish an immutable, reviewed
execution manifest before predictions or metric evaluation. It must enumerate
logical source IDs, exact versions, approved seasons/GWs, source commits/blob
hashes, file byte hashes/sizes, schemas, identity bridges, snapshot timestamps,
deadlines, fixture-assignment classification, promoted map, known exposure
history, permission receipts, and missingness rules. Bind protocol, code,
environment/lockfile and cutoff-view hashes. Private physical locations belong
only in a separate private resolution map. Never discover `latest` or silently
substitute sources. Validate hashes before parsing; a mismatch stops execution.

For each target, an audited loader may release only allowed columns and lower-GW,
pre-cutoff history to the predictor. Reading a full season table into the
prediction process and filtering afterwards does not satisfy separation. The
loader may mechanically partition an explicitly authorized development source;
the model and analyst must not receive target/future values in prediction mode.
This loader, source preparation and all dataset access need later authorization.
No implementation may reuse legacy helpers that load joined actuals into the
prediction workspace merely because those helpers later ignore them.

## 3. U0 and fair scoring adapters

`U0` is exact production xFP v0.1 behavior through a proven historical field
adapter, not M1/S1 or a repaired baseline. Per target fixture:

- If previous-GW context has no minutes value, expected minutes `m0=null` before
  availability gating. Otherwise hard zero for known pre-deadline status `s/u`
  or target-next-round chance 0; else `m0=clip(previous_GW_total_minutes,0,90)`.
- `a0=0` if m0=0, `1` if 0<m0<60, `2` if m0>=60, null if m0 is null.
- Raw rates and their null propagation are exactly the existing causal feature
  formulas. `EG0=raw_xG_per90*m0/90`, `EA0=raw_xA_per90*m0/90` when defined.
  No new event-specific denominator repair is applied to U0.
- Goal points use the repository's frozen GK/DEF/MID/FWD multipliers
  **10/6/5/4**, assist points 3. This study does not change historical scoring
  conventions or claim full-FPL comparability across scoring-rule seasons.
- Preserve fixture-total coalescing, `prediction_complete`, nullable fields and
  blank/DGW aggregation exactly as coded, including numeric incomplete totals.
  Such totals do not count as complete modeled predictions in this study.
  On nonblanks the evaluation adapters require every contributing fixture to
  be complete, avoiding accidental partial-sum coverage. Verified blanks are 0.

For band scoring only, U0's scalar fixture minutes becomes a point mass at m0;
the integer prior-GW sum and cap make this integral. Do not round a nonintegral
unexpected baseline: parity failure stops implementation. Convolve these point
masses for a DGW. Thus the adapter preserves mean and appearance points.
It does not manufacture calibrated probabilities. U0 appearance probability is
`1[m0>0]`; **start probability is undefined** because minutes cannot identify a
start. Do not call `P(minutes>=60)` a start probability.

For count scoring only, U0's per-GW EG0 and EA0 become independent Poisson
marginals with those means (point mass zero when mean zero). This maximum-
simplicity adapter is an assumption, not a production output. It changes no
point projections, completeness or production schema. All scored distributions,
including zero-point controls, are published before outcomes. The UA1 primary
estimand is improvement against **U0 plus this adapter**, not evidence that U0
previously made probabilistic claims. Do not smooth zero mass, clip realized
outcomes or fit an adapter dispersion after seeing scores.

## 4. UM1: joint start/minute probability model

Identity: `UM1-joint-minute-dirichlet-v1`. Registered per-fixture support is
integer minutes 0–90; observed values >90 map to 90 for this bounded minutes
target, retaining an overflow flag/count. Negative or noninteger minutes and
starts outside {0,1} are invalid. Exact raw values remain in immutable input
artifacts. The bounded expected-minutes/MAE estimand uses this cap consistently;
raw-minute MAE is an additional diagnostic. In a DGW the cap is per fixture,
never on the summed GW target.

Define joint state `z=(r,m)`: `N=(no start,0)`, `B=(no start,m>0)` or
`S=(start,m>=0)`. A recorded start at rounded zero minutes is legal and retained.
Prior state-class weights are `(N,B,S)=(0.50,0.15,0.35)`. Conditional on B/S,
minutes are uniform within each listed finite band, with band masses:

| Class | 0 | 1–29 | 30–59 | 60–89 | 90 |
|---|---:|---:|---:|---:|---:|
| B | 0 | 0.70 | 0.20 | 0.09 | 0.01 |
| S | 0.01 | 0.04 | 0.10 | 0.45 | 0.40 |

Let `q(z)` be the resulting joint PMF including q(N,0)=0.50. Its total mass is 1.
Use every eligible player fixture in target GWs `g-8,...,g-1`, clipped to season
start, weight `w_h=0.8^(g-1-h.gameweek)`. A double supplies two observations at
the same weight; no minutes-sum observation replaces them. Prior strength is
4 equivalent fixture observations. The sole estimate is

```text
Q(z) = [4*q(z) + sum_h w_h*1[z_h=z]] / [4 + sum_h w_h].
```

No player/position coefficients, optimizer fitting or alternative window are
allowed. Genuine empty history uses q exactly and is labelled `PRIOR_ONLY`.
Any eligible history row with missing starts/minutes makes UM1 missing for that
player-target, even if availability would force zero; it is not skipped or
classified as a nonappearance. Unknown availability itself is handled below.

Availability multiplier c is computed solely from the target-bound official
pre-deadline state: `c=0` for status s/u or valid target-next chance 0; otherwise
`c=chance/100` if the target-next chance is an integer in [0,100]; otherwise
`c=1` when chance is null/not target-bound. Invalid values fail input validation.
Known status d/i without numeric chance adds an explicit unknown-risk flag but
no invented multiplier. If availability is not known pre-deadline, ignore its
status/chance, c=1 and flag `AVAILABILITY_UNKNOWN`. No text-news interpretation.

Thinning transfers the removed probability to (N,0):
`Q_c(z)=c*Q(z)+(1-c)*1[z=(N,0)]`. Thus a hard gate gives exact zero expectation,
while soft availability represents an explicit mixture assumption. c is the same
for all target fixtures; fixture minutes are conditionally independent under
this first model. This deliberately ignores persistent availability correlation
within a DGW; joint-GW scoring tests that assumption. No claim of true posterior
confidence follows from the prior or thinning rule.

Marginal `p(m)=sum_r Q_c(r,m)`. Publish:

```text
P0=p(0); P1=sum_1^29 p(m); P2=sum_30^59 p(m);
P3=sum_60^89 p(m); P4=p(90);
E[M]=sum_0^90 m*p(m);
P(appearance)=1-p(0);
P(start)=sum_m Q_c(S,m);
E[appearance points]=P(M>=1)+P(M>=60).
```

Band masses are nonnegative and sum to 1. Do not calculate appearance points by
thresholding E[M]. Publish joint/marginal PMFs so expectation is not based on
unreported band midpoints. This also makes start versus cameo identifiable in
predictions without pretending a start equals playing 60 minutes.

For f target fixtures use the same frozen inputs without within-GW updates.
Convolve the fixture PMFs for total `T=sum M_f`, support 0–90f; its five registered
GW bands are 0,1–29,30–59,60–89,90+ (the last contains every T>=90).
GW expected minutes and appearance points are sums of the fixture expectations.
GW appearance probability is `1-product_f p_f(0)`; probability of at least one
start is `1-product_f(1-Pstart_f)`. Total-GW appearance points are **not** obtained
by thresholding T, because two cameos score two appearances. For a verified blank,
f=0, T=0, both appearance/start probabilities and all point components are zero.

UM1-only replaces U0 minutes and appearance components; it retains U0 raw
attacking rates and uses them times E[M]/90, with U0 missing-rate completeness
rules. No new-entrant attacking rate is inferred by UM1. Missing fixture identity
or incomplete modeled components cannot be hidden by the minutes prior.

## 5. UA1: separate event pooling with a latent mixture

Identity: `UA1-gamma-mixture-loss-update-v1`. Separate calculations for e=xG
and e=xA, using no realized goals/assists as training features. Rate units are
events per 90 minutes. Each target player uses the last 12 calendar GWs within
the same season and the causal cutoff above. Weight is `v_h=2^(-(g-1-h.GW)/6)`.

For each event separately, retain rows with positive observed minutes and a
non-null, finite nonnegative event value. Minutes for this rate calculation are
the recorded nonnegative minutes, not the UM1 minutes cap. A zero-minute,
zero-event row carries no exposure; positive event at zero minutes is a source
contradiction and stops the build. Non-null negative/nonfinite event values or invalid minutes stop validation;
they are not silently dropped. Missing event values never become zero:

```text
E_e = sum_usable v_h * minutes_h / 90;
X_e = sum_usable v_h * event_h;
missing_exposure_e = sum_positive_minutes_with_null_event v_h * minutes_h / 90.
```

If no historical minutes were played within the registered history window,
playing exposure and event-observed exposure E_e are both zero (X_e=0), so the
declared labelled prior-only fallback is allowed. If historical minutes exist
but every relevant event value is missing, playing exposure is positive while
event-observed exposure E_e is zero, so that component output must remain null.
If some event observations exist, use only their exposure and flag partial
missingness; observed zeros and absent event data remain distinguishable.
Missing minutes invalidate the player-target.
No raw null is overwritten in the source or U0 artifact.

The prior population is other players in the target's pre-deadline universe
whose target position matches the player's target position. Exclude the target
player by verified identity. Use only their eligible last-12-GW fixture records
while they belonged to their source-GW universe and had that same recorded
pre-deadline position. Do not use end-of-season position/team fields to determine
prior membership. For each peer compute event-specific E_j,X_j as above, then
`d_j=min(1,10/E_j)` for E_j>0; peers with E_j=0 do not contribute. Each peer
therefore supplies at most 900 weighted minutes to the population mean.

```text
Epop=sum_j d_j*E_j; Xpop=sum_j d_j*X_j;
mu_e=clip(Xpop/Epop, 0.01, 2.00).
```

Require at least 20 distinct contributing peers and Epop>=100 (9,000 weighted
minutes). If either fails, use a fixed, explicitly non-empirical fallback:

| Position | xG per90 | xA per90 |
|---|---:|---:|
| GK | 0.01 | 0.01 |
| DEF | 0.08 | 0.08 |
| MID | 0.25 | 0.20 |
| FWD | 0.40 | 0.15 |

No league or previous-season fallback. Publish population counts/exposure,
unclipped mean, clipping/fallback flags and missing exposure separately per
event. The lower/upper mu caps stabilize a prior only; the posterior event rate
has **no upper cap**. Nonfinite numerical outputs fail rather than being clipped.
Role, opponent, ownership, price, club strength and manager preference are excluded.

The prior for rate lambda is a two-component Gamma mixture (shape/rate form):

```text
K=(1,10) exposure units = (90,900) effective prior minutes;
a_k=K_k*mu_e; b_k=K_k; prior component probabilities pi=(0.5,0.5).
L(lambda)=E_e*lambda-X_e*log(lambda); learning rate = 1.
A_k=a_k+X_e; B_k=b_k+E_e;
logZ_k=a_k*log(b_k)-lgamma(a_k)+lgamma(A_k)-A_k*log(B_k);
W_k=exp(log(pi_k)+logZ_k) / sum_l exp(log(pi_l)+logZ_l).
posterior(lambda)=sum_k W_k*Gamma(A_k,B_k).
rate_mean=sum_k W_k*A_k/B_k;
rate_variance=sum_k W_k*[A_k/B_k^2+(A_k/B_k)^2]-rate_mean^2.
```

Use log-sum-exp for W. Publish both W values, K in minutes, A/B parameters,
posterior mean/variance, and 10th/50th/90th quantiles. The mixture does **not** have
a single conjugate effective prior size: also report `sum W_k*90*K_k` as a
summary (90–900), explicitly not a replacement for both components.

xG/xA are fractional expected statistics, **not Poisson event counts**. The
exponential loss update above is a generalized-Bayes working construction,
not an assertion of a true Gamma–Poisson sampling likelihood for xG/xA.
Learning rate 1 and the population plug-in prior are assumptions; uncertainty
in mu, missingness, time drift and the xG-to-realized-event relationship is not
fully propagated. The general loss-update framework supports such explicit
belief updates, not automatic frequentist calibration [3]. Held-out scoring
must earn any predictive claim.

For UA1-only, freeze U0 expected minutes/appearance. Let per-GW exposure
`t=sum_f m0_f/90`. Conditional on lambda, realized goal or assist count has a
working Poisson distribution with mean t*lambda. Integrating yields

```text
Pr(Y=y) = sum_k W_k * Gamma(y+A_k)/(Gamma(A_k)*Gamma(y+1))
                         * (B_k/(B_k+t))^A_k * (t/(B_k+t))^y,
y=0,1,... ; if t=0, Pr(Y=0)=1.
E[Y]=t*rate_mean; Var[Y]=t*rate_mean+t^2*rate_variance.
```

A common latent rate is shared across a player's target fixtures; count
aggregation uses total exposure, not independent redraws of the rate per fixture.
xG and xA rate mixtures are independent working marginals. No joint goal-assist
or full-point calibration is claimed. Publish marginal count probabilities/CDFs
in analytic parameter form, means, variances and central 80% intervals. The
sum of marginal proper scores tests the two marginals, not their dependence.

For UM1+UA1 (`UM1UA1-v1`), integrate the above count marginal over the exact UM1
PMF of T with `t=T/90`. The rate is assumed independent of minutes. Expected
points remain E[T]/90 times the rate mean plus UM1 expected appearance points.
No extra tuning or mixture candidate is created. Missing UM1/UA1 inputs make
the affected combined output missing; a verified blank remains deterministic 0.

### Material novelty and legacy identity preservation

M1 averages last-three observed GW minute totals, M2 last five; M3 weights the
last three calendar GWs 0.60/0.30/0.10 with observed-weight renormalization.
They cap the scalar at 90 and threshold it for appearance points. UM1 instead
learns a joint start/cameo/nonappearance distribution from fixture observations,
with a full bounded PMF, soft availability mixture and coherent expected
appearance points. Its distinction is not the eight-GW window alone.

S1/S2 use `(raw_rate*minutes+position_prior*K)/(minutes+K)` with K=450/900;
S3 switches to a prior below 180 minutes. Their position priors include the
player's own history; they output point rates. Separate xG/xA priors already
existed in that family and are **not** claimed as novelty. UA1 has a non-collapsed,
player-excluding, time-weighted latent mixture, exposure-specific missingness,
data-dependent posterior mixture weights and explicit predictive dispersion.
Each individual Gamma component's mean is still a shrinkage formula; presenting
one component alone, fixing W after results, or collapsing UA1 to renamed S1/S2
would destroy the claimed distinction and is prohibited. The registered question
is whether this whole distribution model improves count forecasting, not whether
a new constant rescues a rejected linear shrinker.

Retain M0–M3/S0–S3 source identities, contract commits and rejection status from
Task033B1. They are documentary historical comparators only in this study: no
legacy predictions/metrics are regenerated or counted as new candidates. No
per-candidate failure magnitude is inferred from the no-winner manifests.

## 6. Estimands, coverage and diagnostics

Primary observation is player-GW; verified blanks are reported separately and
excluded from improvement gates so free zero predictions cannot dominate.
Common pairs require both compared predictions and the relevant actual to be
complete. Report the exact denominator and separate missing-prediction,
missing-actual, missing-event and structural-blank counts. Natural coverage is
prediction completeness / full pre-deadline universe, independently of actual
availability. Report it both including and excluding blanks; the gate uses
nonblanks. U0 completeness is never inferred from a numeric incomplete total.

For each season metric use equal player-GW weights within its registered
population, except ranking/ECE as defined below. Aggregate season metrics with
weights 1/2 (confirmation: one season weight 1). Do not pool rows across seasons
and then take a root, correlation or ratio. For every metric publish natural
population values, exact common-pair values and common-pair size; only common
pairs support accuracy comparisons. Predictions unique to a candidate are
coverage evidence and diagnostics, not paired evidence of superiority.

| Metric | Literal definition; lower is better unless noted |
|---|---|
| UM1 primary | GW five-band Brier loss `sum_b(P_b-1[T in band b])^2`, unnormalized, dimensionless [0,2] |
| Minutes log loss | `-log(P_observed_band)` in nats, +infinity for zero mass; diagnostic only, including count of infinities |
| UA1 primary | Mean of goal and assist ranked probability scores: `0.5*sum_e sum_y>=0 (F_e(y)-1[Y_e<=y])^2`; event-count units |
| Marginal count log loss | `-log Pr(Y_e)` nats, infinities retained; diagnostic only |
| Point errors | MAE=`mean(abs(pred-actual))`; RMSE=`sqrt(mean((pred-actual)^2))`; signed bias=`mean(pred-actual)`; gate absolute bias=`abs(signed bias)` |
| Modeled scope | Sum of per-fixture appearance + goal + assist points, using frozen position scoring; never full-FPL points |
| Ranking | Per-GW Spearman of modeled prediction versus actual on common complete rows, average GWs equally; higher better |
| Top N | For N=10,25,50, strict predicted and realized top N on same common rows, descending score then ascending element_id; overlap=`intersection/N`, averaged equally over GWs; higher better |
| Binary calibration | Appearance (at least one appearance) and start (at least one start), per-GW outcome from fixtures; ten fixed bins [0,.1),...,[.9,1]; ECE=`sum_bin weight*abs(mean(prob)-mean(binary))` |
| Count calibration | Within each target, predicted-mean deciles, ascending mean then ID, bin `min(9,floor(10*(rank-1)/n))`; show weighted mean count versus forecast per event/bin; recompute bins in resamples |

The primary convolved gameweek-total Brier target evaluates total-minutes
calibration, while the separate appearance-point MAE guardrail in section 7
evaluates the per-fixture appearance scoring consequences in double gameweeks.

UA1 RPS is a proper score for integer-valued marginals; Brier is proper for
categorical probabilities [1]. Units, signs and averaging are deliberately
frozen. For RPS compute through K>=observed count, with remainder bounded by
`E[(Y-K-1)+]` since survival squared <= survival. Increase K until that analytic
mixture-tail bound is <=1e-10 per marginal; K ceiling 10,000. Failure to certify
the bound is a numerical failure, not permission to discard tails or observations.
Quantiles use the smallest integer whose CDF reaches the specified level;
Gamma rate quantiles use the infimum of the mixture CDF with absolute CDF error
<=1e-10. No Monte Carlo approximation to a prediction distribution is allowed.

Also report minutes MAE/RMSE, raw minutes diagnostics, appearance-point MAE,
goal/assist/combined attacking point MAE/RMSE/bias/Spearman, modeled-scope
MAE/RMSE/bias/Spearman/top-N, per-GW error tables, and 80% predictive-interval
coverage/width for T and each count. Actual starts missing excludes only the
start diagnostic and is counted; UM1 input missing starts follows section 4.
U0 start calibration is `NOT_DEFINED_FOR_CONTROL`, with no paired start test.
Constants/too-small populations give undefined Spearman, never zero correlation.
Empty calibration bins contribute zero weight, not invented outcomes. Log loss
with infinity is printed as such with no finite CI or surrogate epsilon score.

### Frozen strata

All strata except explicitly labelled realized-minute diagnostics are determined
before outcome joining. No intersections or additional outcome-selected families:

- Position: GK, DEF, MID, FWD.
- Prior observed minutes (same-season unweighted): 0, 1–90, 91–270, 271–450,
  451–900, 901+; separate UNKNOWN if missing. Genuine empty history is 0 only
  when the source-universe/history contract proves no eligible observations.
- Previous-GW observed minutes: 0, 1–29, 30–59, 60–89, 90+, plus MISSING_CONTEXT
  (including genuine blank/new-universe cases), preserving reason flags.
- Availability, mutually exclusive in this order: forced-zero (valid hard gate),
  doubtful/chance-limited (d/i or target-bound chance strictly between 0 and 100),
  available (known status a, not in earlier classes), unknown (everything else).
- Target fixture count: blank, single, double+.
- Universe: new entrant (absent from the immediately previous GW snapshot),
  established (present); missing previous snapshot stops source validation.
- Promoted team: yes/no by the frozen metadata map.

Only the position and availability families carry simultaneous subgroup gates
below. Other strata are fully published diagnostics; insufficient strata do not
support subgroup claims or permit later claims of demonstrated non-regression.
Material decision roles are absent because the corpus is deferred.

## 7. Paired inference, dependence and family-wise gates

All thresholds are literal choices, with no claimed power calculation or
empirical support. A statistically defensible null/insufficient result is an
acceptable study outcome. Improvements use negative candidate-minus-control
loss differences; positive differences mean deterioration.

### Resampling and interval construction

Use 9,999 replicates with NumPy `Generator(PCG64(330331))` for development and
`PCG64(330332)` for confirmation; exact library/runtime version is bound by the
execution manifest before any outcomes. Lexicographic season order, increasing
player ID and increasing GW order are canonical. For each replicate and each
season, draw P player indices and G target-GW indices independently uniformly
with replacement; row weight is player multiplicity times GW multiplicity.
Draw on the full registered nonblank universe once, not separately by model,
metric or stratum. Use identical paired weights for U0, every evaluated candidate,
every endpoint and all overlapping subgroup tables. Predictions are not refitted
inside the bootstrap: inference is conditional on the frozen prediction stream.

Within each season recompute every metric on its fixed original common-pair
membership with these weights. Loss means/RMSE/bias are weighted; ECE bin means
and weights are recomputed. For per-GW ranks interpret player multiplicity as
copies (copies tied on score, ordered by ID then copy index for top-N); compute
midranks for Spearman across those copies, correlation on ranks, and average
GW metrics using GW multiplicity. Top-N compares predicted and realized selected
copies. The original all-one-weight estimate is exactly strict top-N on distinct
players. Zero-weight GWs are not evaluated. A positive-weight GW with fewer
than N copied common rows makes that endpoint replicate undefined. Decile
calibration likewise uses copied rows. Do not pretend ranking bootstrap coverage
is an exact finite-sample theorem; these nonlinear metrics are engineering
non-regression checks under the same working resampling law.

Report two-sided 95% marginal percentile intervals for diagnostics only:
order-statistic indices 250 and 9750 (1-based, B=9999). Report observed P/G and
min/median/max distinct retained players/GWs across replicates. Gate inference
uses the simultaneous one-sided **basic** bounds below, not these marginal CIs.

Required global endpoints need at least 1,000 complete nonblank common
player-GWs, 100 distinct players and 30 distinct target GWs in **each** season.
Ranking additionally needs at least 30 GWs with >=50 distinct common players
and nonconstant actual/predicted rankings; otherwise the required ranking gate
is insufficient. Natural nonblank coverage must be >=95% for both candidate
and U0; common modeled pairs must be >=90% of the nonblank universe, and
relevant actual completeness >=99%. These thresholds apply per season before
accuracy gates. No removal of inconvenient GWs to achieve the minimum.

For required position/availability subgroup endpoints, each present subgroup
needs >=200 complete common rows, >=20 players and >=20 GWs per season.
An availability subgroup with zero pre-outcome universe rows is structurally
`NOT_APPLICABLE` (no assurance for that group); a nonempty undersized subgroup
is `INSUFFICIENT_EVIDENCE` and fails the candidate closed. Every position is
required. Reducing the subgroup family because common pairs became sparse is
forbidden. Other diagnostic strata use the same 200/20/20 rule for a CI; below
it report descriptive values/counts only.

Any undefined required point statistic, undefined replicate, numerical failure,
empty denominator or failure to construct all 9,999 required replicates makes
the affected gate fail. Do not redraw, omit replicates, relax minimum clusters,
replace a correlation with zero, or silently publish a finite interval. An
algebraically identical candidate/control endpoint may have exact D=0 and a
zero-width interval, labelled `STRUCTURALLY_IDENTICAL`; this does not waive
population requirements. Constant calibration outcomes do not invalidate Brier.

### Families, multiplicity and numerical bounds

Three possible candidate packages have permanently reserved error budgets:
UM1, UA1, UM1UA1. Each gets alpha=1/60 total. Within each, global family and
subgroup family get alpha=1/120 each: the union bound over all six families is
0.05. Unused allocations are not recycled. This is **nominal**, conditional on
the resampling assumptions, not a guarantee under arbitrary football dependence.
Confirmation retains the same conservative allocation even for its single
selected package. Conditional combination testing never receives a fresh budget.

For each family create the complete endpoint vector specified below, including
aggregate and every required season-specific endpoint. Define D_j as the
adverse candidate-minus-U0 difference; for higher-is-better metrics use
U0-minus-candidate. Let s_j be the positive scale in the threshold table.
For replicate b calculate

```text
R_b=max_j[(Dhat_j-Dstar_bj)/s_j];
q=max(0, 9917th sorted R_b);       # ceil((9999+1)*(1-1/120))
U_j=Dhat_j+q*s_j.
```

This is a nonstudentized, scale-normalized simultaneous basic upper bound;
no estimated standard error, model-chosen scaling or normal approximation is
substituted. Each family has nominal one-sided simultaneous confidence
119/120 (99.1667%); simultaneous overall coverage is at least 95% under valid
bootstrap approximation. Improvement requires **U < -threshold**; non-inferiority
requires **U < margin**. Equality fails. Show every bound, threshold and Boolean.

Global family membership is exactly the following relevant rows, aggregate
plus each season. The primary improvement row applies to the aggregate only;
for each individual season replace it with primary non-inferiority as stated.
All other rows apply both to the aggregate and each season.

| Adverse endpoint | UM1 | UA1 | UM1UA1 | Threshold/margin; scale s |
|---|---|---|---|---|
| Five-band Brier | required | — | required | Aggregate improvement 0.01; each-season NI 0.01; s=0.01 |
| Mean count RPS | — | required | required | Aggregate improvement 0.005 events; each-season NI 0.005; s=0.005 |
| Minutes MAE | required | — | required | NI 1.0 minute; s=1.0 |
| Minutes RMSE | required | — | required | NI 2.0 minutes; s=2.0 |
| Appearance-point MAE | required | — | required | NI 0.02 point; s=0.02 |
| Appearance ECE | required | — | required | NI 0.02 probability; s=0.02 |
| Each goal/assist marginal RPS (two endpoints) | — | required | required | NI 0.005 events; s=0.005 |
| Each goal/assist point MAE (two endpoints) | — | required | required | NI 0.02 point; s=0.02 |
| Modeled-point MAE | required | required | required | NI 0.03 point; s=0.03 |
| Modeled-point RMSE | required | required | required | NI 0.05 point; s=0.05 |
| Absolute modeled-point bias | required | required | required | NI 0.02 point; s=0.02 |
| Modeled-point Spearman loss | required | required | required | NI 0.01 correlation; s=0.01 |
| Modeled-point top-10/25/50 overlap loss (three) | required | required | required | NI 0.02 overlap fraction; s=0.02 |
| Natural nonblank modeled-coverage loss | required | required | required | NI 0.01 fraction (one percentage point); s=0.01 |

The subgroup family includes, for every required position and every nonempty
availability class, aggregate plus each season: modeled MAE deterioration
(margin/scale 0.05 point), modeled RMSE deterioration (0.10 point), absolute
modeled bias deterioration (0.05 point), and natural modeled-coverage loss
(0.01 fraction). No primary improvement is required separately in a subgroup.
If an availability group is structurally absent in one season, its two-season
aggregate endpoint is NOT_APPLICABLE; retain the present season endpoint and
make no combined-season claim for that group. Do not reweight the remaining
season into a nominal two-season subgroup estimate.
Use the maximum across all of these endpoints, not separate unadjusted CIs per
stratum. The multiplicity reservation includes structurally absent endpoints;
their absence never reallocates alpha. Subgroup actual completeness must be
>=99% and common modeled coverage >=90%; failure is insufficient evidence.

### Serial dependence diagnostic and decision role

The crossed player/GW bootstrap accommodates repeated players and shared GW
shocks [2]; it does not automatically model persistent shocks across adjacent
GWs or shared multi-season players under independent season resampling.
The proposed inference is a conditional two-season estimand, not a sample of
independent seasons or a claim about arbitrary future seasons.

Always publish lag-1 and lag-2 autocorrelations of each season's GW-mean paired
primary-loss differences. Missing/constant series are labelled undefined, not
zero. Always run a second, paired player × moving-GW-block bootstrap, irrespective
of the autocorrelations: PCG64 seeds 330431 development / 330432 confirmation,
9,999 replicates, block length 4, draw starts uniformly from the 34 overlapping
blocks of GWs 2–38, concatenate 10 blocks and truncate to 37 positions. Keep all
players/outcomes for each copied GW. Draw player multiplicities once on the
union of verified player codes across development seasons and apply them in
both seasons; season-specific appearances of a code share that multiplicity.
Confirmation uses the identical rule on its one season. Missing identity bridge
prevents this gate, not permission to resample players as independent seasons.

Recompute the same global and subgroup families, scales, thresholds and bounds.
Every required gate must pass **both** primary and serial-block bounds. This
intersection can only restrict admission; no alpha is reclaimed and no switch
between favorable intervals is allowed. At least 30 evaluable GWs remains
required; no shorter block fallback. The block model assumes local weak
stationarity/dependence over this fixed horizon [4]; four GWs is an engineering
choice, not evidence that longer dependence is absent. Report disagreement as
`SERIAL_SENSITIVITY_FAIL`; passing is not proof of dependence-free precision.

## 8. Selection and unopened confirmation

First publish U0, UM1 and UA1 development predictions before their outcomes are
joined. Evaluate UM1 and UA1 independently. Both must pass every coverage,
statistical, subgroup, serial and resource gate before combination metrics are
evaluated. Derive the dormant combination predictions only from the component
PMFs/parameters and publish their own hash in the initial prediction package,
**before any development outcome join**. Their existence does not authorize
evaluation. No new feature fitting or revisions of component predictions are
allowed. Component scores are already exposed when this gate opens: label the
combination as a **pre-specified conditional evaluation**, never an unseen test.

Select at most one confirmation package using the following fixed priority:

1. If exactly one component passes, select it.
2. If both pass, evaluate the one combination. Select the combination only if
   all its registered gates pass, including both primary improvements.
3. If both pass but combination fails, select UM1 (simpler bounded probability
   model, priority fixed here), irrespective of point-score magnitude.
4. Otherwise select none and publish `DO_NOT_CONFIRM`.

No candidate race by nominal p-value, strongest margin or the owner's action.
Preserve all failed gates and original candidate identities. No alternate
formula or follow-up seed after failure. A numerical bug requires an explicit
reviewed correction/version; previously seen results remain exposed, not reset.

Confirmation season is **unassigned and unopened**. No assumption that 2025/26
is available: its Task018D seal is independent. Before any confirmation source
is resolved or opened, a separate owner receipt must name one complete untouched
season, its experiment-specific permission, exact source and custody identities,
known exposure audit, frozen selected package hash and reviewed implementation.
An independent custodian must establish outcome-unseen status without releasing
results. If no eligible season exists, status is `AWAITING_AUTHORIZED_CONFIRMATION`,
not successful research or permission to use a partial current season. Selecting
a season based on performance or replacing a failed confirmation season is banned.

Use exactly GWs 2–38 and the same within-season online update rules, constants,
metrics/gates, subgroup definitions and budgets. Recomputing fixed causal
sufficient statistics as earlier fixtures become eligible is permitted prediction
construction, not hyperparameter refitting. All package predictions for the
season must be immutably published before aggregate confirmation outcomes or
scores are made available to the analyst; a blind loader may release earlier
history for later-target forecasts under the registered cutoff. No interim
confirmation metrics, alternate candidate or adaptive stopping. Confirmation is
opened once and evaluated once; failure/insufficiency is `DO_NOT_PROMOTE`.
Successful independent reproduction and review can give only
`ELIGIBLE_FOR_TASK033D_COMPONENT_DIAGNOSTICS` under this corpus deferral.

## 9. Synthetic decision corpus decision and claim limits

**Deferred from this first study.** No synthetic squad construction, transfer
search, private squad sampling or decision regeneration is authorized. Therefore
no action-agreement, transfer frequency/false-positive transfer, realized regret,
captain regret or robustness-margin thresholds are claimed to have passed.
They are `NOT_EVALUATED`, not zero, not inherited from component accuracy.

A later separate design must fix public squad/budget/team-limit/free-transfer
sampling, gameweek coverage, candidate enumeration, tie rules, loss and regret
estimands, and GW-cluster inference before construction. Claude review and owner
approval remain required before that construction. Prefer genuinely new
confirmation evidence for outcome-based decision validation; this study's seen
seasons cannot be relabelled untouched for that later task.

This amendment may produce a rejected component, a development-eligible package,
or a confirmed component for isolated diagnostics. It cannot establish better
real-manager decisions, total FPL benefit, unnecessary-transfer restraint or
captain quality. Task033D mechanical work may remain diagnostic only. Predictive
mandatory views, stability/publication thresholds and Task033E production
promotion require that separate decision-level work and explicit owner approval.
Deterministic sensitivity views already justified independently of predictive
performance retain Task033A/B's separate admission path; they do not gain new
predictive authority here.

## 10. Computation, publication and failure reporting

Preserve all governing operational ceilings: <=700 public players/GW,
<=3,000 legal ROLL/one-transfer candidates per decision case, U0 plus at most
three numerical variants, <=12 mandatory views, <=60 seconds and <=2 GiB peak
resident memory per decision robustness evaluation on the owner's macOS arm64
environment. Exceeding any ceiling fails without partial success publication.
No candidate-specific shortcut may weaken trust checks or change candidate order.
No operational-scale performance is asserted by this document.

Before Task033C implementation approval, a separately authorized synthetic
feasibility spike must exercise the proposed kernels and artifact accounting at
those dimensions; before any real-data execution, the reviewed implementation
must pass end-to-end synthetic correctness, scale and privacy checks. Such
benchmarks are future work, not authorized or run by this documentation task.
Because the decision corpus is deferred, decision-runtime compliance remains
unproven and a downstream integration blocker even if component research passes.

Additional offline research ceiling: 2 GiB peak RSS, one OS process with at most
four computational threads, <=6 hours wall time per season/candidate prediction
and scoring phase, and <=24 hours for each complete development or confirmation
statistical evaluation (both resamplers, all evaluated packages). Batch/stream
replicates and cache only outcome-independent predictions and deterministic
per-row sufficient metric data; no dense player-by-GW-by-replicate tensor.
Ceilings include I/O and publication. A timeout/memory breach publishes a
sanitized failure receipt, no candidate eligibility; do not reduce B, drop gates
or raise a budget after inspecting performance without a new reviewed amendment.

Required immutable, exclusive/no-overwrite artifacts:

- Reviewed protocol/base/design hashes, candidate identities/constants, exact
  source manifest and causal feature-view hashes; public logical IDs only in
  sanitized reports, private resolution mapping stored separately.
- Code revision/source hashes, environment and numerical-library identities,
  RNG algorithm/seeds/draw order, UTC creation times and stage permissions.
- Full prediction PMFs or exact reconstructible analytic parameters, per-row
  mean/completeness/flags, input cutoff audit, prediction-before-outcome hashes
  and a separate post-publication join/evaluation receipt. Hashes attest byte
  identity and stage ordering, not that an archive was historically untouched.
- Exact population counts, common-pair membership digest, subgroup membership
  definitions/digests, missingness, all metrics including infinities/undefined
  reasons, resampling diagnostics and every gate/bound, not only passing results.
- Resource evidence, conditional-combination decision, deterministic selection,
  unopened/authorized confirmation state, and final bounded-eligibility status.
- Failure manifest with stage, fixed reason code, completed artifact identities
  and invalid/unevaluated gate states. Partial artifacts remain explicitly
  incomplete and cannot be consumed as a successful prediction/evaluation chain.

Independent reproduction uses the same frozen code, environment, source and
prediction hashes. In the same pinned environment, semantic prediction/metric
bytes and selection must reproduce exactly; timestamps/provenance receipts are
separate. Cross-platform drift must be reported and reviewed, not normalized
away after a gate flips. Synthetic tests must verify PMF normalization, moments,
appearance consistency, generalized-update algebra, missing versus zero,
blank/DGW behavior, U0 parity, hostile target-outcome perturbations, no access to
sealed sources, paired resampling, all thresholds/NA/failure paths, conditional
combination and exclusive publication. This amendment adds no implementation
or tests and runs no experiment.

Source/permission ambiguity, source hash mismatch, required input corruption,
insufficient clusters, failed interval, resource failure or review absence stops
the relevant next stage. Never rerun Gate 0, unseal a holdout, fit a replacement,
change a journal, refresh live FPL, alter a monitor, execute Task026C, regenerate
a decision or promote a model to make the task appear complete.

## 11. Review checklist and unresolved execution prerequisites

The formulas, score definitions, gates and selection above have no discretionary
post-result tuning slots. They are a **reviewable proposed freeze**, not an
approved or executable freeze. Remaining prerequisites are explicit: independent
statistical/novelty review and owner approval; exact authorized source manifest
and promoted map; proof of cutoff-loader isolation and required field coverage;
synthetic implementation/resource validation; an untouched authorized confirmation
season; and later decision-corpus evidence for decision-level eligibility.
These are not claimed completed and do not authorize opening inputs to resolve
them now. If review finds the mixture insufficiently novel, bootstrap assumptions
unsound or any formula ambiguous, record a design blocker and revise before
execution rather than call it frozen.

The review should particularly challenge U0 adapter fairness, Gamma loss-update
calibration assumptions, DGW dependence, sparse required availability strata,
nonlinear ranking inference, mixture novelty versus S1/S2 and the narrowed
Task033D eligibility. No empirical evidence has been fabricated to settle those
questions. Current xFP v0.1 remains unchanged whatever this design's status.

## 12. Primary statistical references

1. Gneiting & Raftery (2007), *Strictly Proper Scoring Rules, Prediction, and
   Estimation*, JASA 102:359–378.
   [Author-hosted paper](https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf).
   Supports proper-score definitions; does not prescribe the thresholds here.
2. Owen & Eckles (2012), *Bootstrapping data arrays of arbitrary order*,
   Annals of Applied Statistics 6:895–927.
   [Author paper](https://arxiv.org/abs/1106.2125).
   Supports crossed-factor product reweighting under specified random-effects
   conditions; no exact universal bootstrap or finite-sample football guarantee.
3. Bissiri, Holmes & Walker (2016), *A general framework for updating belief
   distributions*, JRSS B 78:1103–1130.
   [Author paper](https://arxiv.org/abs/1306.6430).
   Supports explicitly defined loss-based belief updates; not this learning rate,
   football prior, mixture choice or automatic calibration.
4. Künsch (1989), *The Jackknife and the Bootstrap for General Stationary
   Observations*, Annals of Statistics 17:1217–1241.
   [Original publication](https://doi.org/10.1214/aos/1176347265).
   Motivates block resampling for serial dependence; neither length four nor
   the combined crossed/block procedure is a sourced optimal football design.
