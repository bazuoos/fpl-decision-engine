# Task033C3 — Controlled research execution design

## 1. Status, objective and stop decision

**DESIGN ONLY — BLOCKED BEFORE IMPLEMENTATION.** The original design was
prepared against `5ab1c393fb36c73e2e2cf559dbe4ffd242a88ca6`; the owner-decision
amendment was prepared against reviewed design commit
`2da9d7d63b6ac74626c957a7ec8e5be9f17ab21b` on
`codex/task033a-decision-robustness-design`. This document is a constrained
architecture and precise blocker report, not an executable authorization or an
approved extension of C1/C2. No real or synthetic experiment ran for this task.

Design-only remediation: the owner reports that Claude Sonnet 5 High reviewed
the preceding draft as SAFE / IMPLEMENTATION READY: NO and explicitly supplied
three mandatory design findings. The remediated revision was then independently
re-reviewed SAFE / IMPLEMENTATION READY: NO with no remaining mandatory design
finding. On 2026-09-22 the owner approved the recommended policy directions for
B01–B07 recorded in section 3.2. The seven blockers remain open as implementation
and evidence gates because their concrete contracts, parameters and validation
do not yet exist. This approval authorizes this documentation amendment only; it
does not authorize implementation, protected reads or research execution.

The objective is a future boundary connecting the pure research kernels to
explicitly authorized, attributable sources, with prediction/outcome separation,
measured resource controls, guarded joins and exclusive research publication.
It must reject unauthorized, ambiguous, incomplete, non-causal, resource-unsafe
or publication-conflicting execution. It must never decide that research should
run. An owner request to implement infrastructure would still not authorize data
access. A SAFE design review would still not authorize implementation.

The stop instruction applies now: repository evidence does not settle several
material execution choices. Section 3 identifies them and the exact evidence or
reviewed decisions required. No implementer may fill these gaps with convenient
defaults. Sections 4–16 specify preserved rules and proposed requirements for
review; where a contract is blocked, it is deliberately not a runnable schema.
Section 17 now supplies the separately scoped C3.0 contract proposal against
`d55421e52f7c8a921a87967434bbf9ca903b6a39`. It is ready for independent design
review with explicit open decisions/evidence, not implementation. This task
creates no C3 implementation, source registration or grant.

## 2. Repository-established foundation

The initial isolated worktree was clean at the base above. Read-only remote
inspection on 2026-09-22 confirmed that the branch tip was that same SHA and
[CI run 35688498714](https://github.com/bazuoos/fpl-decision-engine/actions/runs/35688498714)
was completed successfully on that SHA, including the Python 3.10 test job.
The primary checkout was `e10d4c9dc10467730cdb90fcbea494dcdf920b6f`, with only
untracked `task025_claude_review_bundle.txt` and `task025_review.patch`.
Continuity documents refer to the earlier C2 implementation commit; their
embedded checkpoint is not the current branch HEAD. No applicable AGENTS.md
was found in the checked worktree or ancestors.

Read together, in this order of technical authority:

- [C1 code](../../src/fpl_decision_engine/research/task033c1.py),
  [C2 contracts](../../src/fpl_decision_engine/research/c2/contracts.py),
  [evaluator](../../src/fpl_decision_engine/research/c2/evaluation.py),
  [inference](../../src/fpl_decision_engine/research/c2/inference.py),
  [metrics](../../src/fpl_decision_engine/research/c2/metrics.py), and
  [results](../../src/fpl_decision_engine/research/c2/results.py).
- The closed [research schemas](../../contracts/research/),
  [architecture guards](../../tests/test_task033c_research_architecture.py),
  [compatibility examples](../../tests/test_task033c2_compatibility.py),
  [serial union tests](../../tests/test_task033c2_serial_union.py),
  [orchestration tests](../../tests/test_task033c2_orchestration.py) and
  [independent row oracle](../../tests/task033c2_naive_oracle.py).
- The [candidate freeze](TASK033B1B_CANDIDATE_FREEZE_AMENDMENT.md) and
  [owner-approved clarification](TASK033C2_PROTOCOL_CLARIFICATION.md).
  Preserve the earlier [C2 blocker](TASK033C2_PROTOCOL_BLOCKER.md) as history.
- [Task033A](TASK033A_NUMERICAL_DECISION_ROBUSTNESS_SPEC.md),
  [Task033B](TASK033B_UNCERTAINTY_RESEARCH_DESIGN.md),
  [prior provenance](TASK033B1_PRIOR_RESULT_PROVENANCE_SPEC.md),
  [C1 implementation](TASK033C_SYNTHETIC_UNCERTAINTY_IMPLEMENTATION.md) and
  [C2 implementation](TASK033C2_SYNTHETIC_EVALUATION_IMPLEMENTATION.md).
- [Handoff](CURRENT_HANDOFF.md), [state](PROJECT_STATE.md),
  [architecture](ARCHITECTURE.md), [decisions](DECISIONS.md),
  [workflow](AI_WORKFLOW.md) and [roadmap](ROADMAP.md) provide context, not grants.

Concrete constraints found in code:

| Established behavior | C3 consequence |
|---|---|
| C1 accepts typed history and Boolean cutoff/universe markers; it does not authenticate their provenance | Loader must prove markers from evidence; setting them true is no proof |
| C1 header fixes PREDICTION_ONLY, outcome_joined=false, UNOPENED_UNAUTHORIZED and byte-identity-only claims | Do not reuse this header as a confirmation authorization receipt |
| C2 PredictionBatch and EvaluationResult require SYNTHETIC_ONLY; JoinedOutcomes requires GENERATED_SYNTHETIC_OUTCOMES | Real data cannot honestly be passed through these schemas |
| ResourceGate scope is SYNTHETIC_C2_EVALUATION and evidence digest is supplied, not measured | A digest or PASS token is not resource enforcement |
| evaluate evaluates the combination automatically if both components pass | External conditional permission needs a reviewed interface; do not bypass private APIs |
| Result schema requires conditional combination evaluation when both components pass | Withholding combination cannot be represented by inventing a valid completed development result |
| C2 missing outcomes are representable and coverage is statistical | C3 distinguishes missing evidence from corrupt joins; it must not erase prediction rows |
| Code bridges include blank-only codes in the serial union | No dropping blank rows before bridge validation or serial draws |
| Membership digest covers included season/GW identities only | Source hashes, pair identity and exclusions belong in separate fields |
| Architecture checks are AST-based import/call guards | Useful regression evidence, not a runtime filesystem or process sandbox |
| historical_sources.py declares PARSER_SCHEMA_VERSION="historical-v3.1"; historical_backtest.py fixes EXPECTED_HISTORICAL_VERSION="historical-v2" and rejects any different parser_schema_version | The legacy backtester is outside the proposed C3 execution path; preserve its v2 gate, do not invent v3.1 compatibility |
| Existing local authorization uses human process, ownership checks and explicit CLI gates; the web SingleUserAllowAllPolicy checks a shared local principal | No research signing service or independent custodian is established; B02 must choose an achievable deployment profile without upgrading process controls into cryptographic or independent-custody proof |

[Historical contracts](../../src/fpl_decision_engine/historical.py) and the
[pinned catalogue](../../src/fpl_decision_engine/historical_sources.py) prove
schemas and declared source identities, not current local data integrity.
Historical snapshot times are derived from archive paths and checked against
target deadlines. The manifest explicitly records completion timestamp absence,
finalized fixture assignments and possible retroactive corrections. No local
historical files, results, sealed data or operational GW5 artifacts were opened.

Exact header verification at the base: the tracked and bundled
[C1 header schema](../../contracts/research/task033c1_prediction_header.schema.json)
sets JSON property `properties.stage.const` to `PREDICTION_ONLY` and
`properties.confirmation_state.const` to `UNOPENED_UNAUTHORIZED`; both properties
are listed in `required` and `additionalProperties` is false. C1
`ArtifactHeader.__post_init__` repeats both literals in its `fixed` mapping.
These are verified schema/code facts, not an inference from the document title.
They constrain the old header; they do not establish actual custody or permission.

For the source-version conflict, see `historical_sources.PARSER_SCHEMA_VERSION`,
[historical_backtest.py](../../src/fpl_decision_engine/historical_backtest.py)
`EXPECTED_HISTORICAL_VERSION`, `_input_paths` and `_validate_input_manifest`.
Its `_load_input_tables` loads feature, actual-fixture and predeadline tables into
one connection. This is not a prediction-only source-serving boundary. Section
5.1 selects a separate proposed C3 adapter without changing the legacy gate.

For the existing authority model, see [AI_WORKFLOW](AI_WORKFLOW.md),
[local authorization](../../src/fpl_decision_app/authorization.py)
`SingleUserAllowAllPolicy.authorize`, and the tracked
[schedule controller](../../scripts/completion_monitor_production_schedule.py)
owner/permission checks and `activate(..., execute=...)` explicit CLI gate.
These are evidence of local procedural controls, not research grant mechanisms;
C3 does not reuse operational activation as research authorization.

## 3. Blocking questions and required resolutions

These are implementation stop gates, not optional follow-ups. A resolution must
be frozen and independently reviewed before dependent code or data access.

| ID | Material gap and precise blocker | Required resolution; until then |
|---|---|---|
| B01 — causal source contract | historical-v3.1 permits restricted pseudo-backtests but cannot prove first availability of each performance value, completion time, or correction. Logical kickoff filtering cannot prevent corrected-future leakage. The requested causal boundary cannot be claimed from those tables alone. | Separately review an immutable as-of research-source contract and source evidence/custody plan. Decide whether exact v3.1-compatible values can be supported by attributable pre-cutoff evidence. Any relaxation of causal claims or changed values is a protocol amendment, not a C3 default. No historical fallback; SOURCE_PROVENANCE_INVALID. |
| B02 — authority and time | Existing single-operator human/ownership/CLI controls do not establish cryptographic research grants, independent custody or rollback-resistant authority. Requiring an unspecified independent service is not an achievable deployment definition. | The owner selected A2-single-operator for development only, subject to the still-open trust/clock/key/ledger contract and proof in section 6.1. Confirmation still needs the frozen independent-custodian prerequisite. The worker may never issue its own grant; real access remains UNAUTHORIZED until the concrete profile is reviewed and implemented. |
| B03 — C1/C2 contract bridge | Closed C2 authority literals exclude real execution; C1 header cannot assert authorized confirmation. evaluate() has no external combination control point and automatically scores UM1UA1 after both components pass. | The owner selected EVAL_AUTH (section 6.3): authorization must precede combination computation. A separately reviewed versioned API/schema proposal is required before code work. No real-source call through current C2, synthetic relabel, unchecked model or private _Pair bypass. |
| B04 — final resource evidence | C2 needs PASS evidence before selection/combination; frozen budget includes I/O and publication that have not finished. Neither an evaluation seam nor suppressing publication resolves this timing cycle. | Owner/reviewer must approve which completed resource intervals permit component admission and conditional dispatch, which final intervals gate result completion, and an acyclic receipt graph preserving frozen ceilings. If that changes the meaning of a frozen resource gate, amend the protocol before implementation. No prospective PASS; both alternative combination policies remain OUTCOME_ACCESS_BLOCKED until resolved. |
| B05 — enforceable resource profile | No reviewed backend continuously enforces RSS, numerical threads, descendants, scratch bytes and publication bounds on both platforms. Scratch/output caps and accounting of trusted parent/broker overhead are unspecified. | Freeze numeric byte/inode/output/controller budgets, phase accounting and OS enforcement with synthetic evidence. macOS and Linux need separately accepted profiles. No environmental-variable or sampling-only PASS; OUTCOME_ACCESS_BLOCKED. |
| B06 — bridge serialization | Historical code is an integer while C2 serial ordering uses UTF-8 text; canonical integer-to-code representation and admissible historical reconciliation are not frozen by a complete adapter. | Review one explicit mapping/encoding contract with independent test vectors and verified season-specific bridges, including blank-only codes. Suggested decimal encoding below is only a proposal. No name matching or guessed bridge; SOURCE_PROVENANCE_INVALID. |
| B07 — publication identity and recovery | C2 result bytes embed resource-evidence hashes, which may vary between identical attempts. Final resource/publication receipts can form a hash/time cycle. Platform-exclusive commit and durability semantics have no C3 contract. | Review the identity hierarchy, deterministic-core boundary and terminal commit point in sections 9/12 together with B04. Prove conflict/adoption and recovery behavior on each supported filesystem. No silent timestamp stripping or replacing a receipt; PUBLICATION_CONFLICT. |

B01 does not retrospectively invalidate the approved restricted-pseudo-backtest
classification; it identifies why that classification is insufficient for the
stronger causal assurance requested here. C3 cannot invent missing history.
B06 cannot be resolved merely by choosing the most convenient string conversion:
changing lexical code order changes the seeded serial RNG assignment.

No confirmation season is assigned. 2025/26 is not implicitly available, and no
source may be opened to discover whether it would make an attractive holdout.
A custodian's untouchedness evidence must precede confirmation authorization.

### 3.1. B01–B07 decision ledger

All entries remain **OPEN / FAIL CLOSED** as implementation and evidence gates.
The owner-approved policy directions are recorded in section 3.2; independent
review must still accept the resulting technical and statistical contracts.
Policy approval is not a research grant. Naming a module below is a design choice,
not approval to create or execute it.

| B01 field | Decision record |
|---|---|
| Decision owner | Product owner for allowable causal claims/source scope; independent source/protocol reviewer for consistency |
| Alternatives | Immutable attributable as-of evidence compatible with v3.1; an explicit protocol amendment accepting weaker historical claims; remain synthetic-only if neither is supportable |
| Owner-approved direction | Keep the causal gate closed until the first option is evidenced. Exclude the v2 backtester; use the separately reviewed adapter in section 5.1. No silent weaker-claim fallback. |
| Repository evidence | Freeze section 2 requires corrected historical-v3.1; historical_sources declares v3.1, legacy backtester accepts only v2; historical.py records incomplete historical availability proof. |
| Security/statistical consequence | Archive identity alone cannot prevent corrected-future leakage; changing source values or causal classification may change the estimand and requires protocol review. |
| Exact contract changes | New immutable as-of source/view registration and lineage contract; explicit v3.1-to-C1/C2 mapping contract. No change to EXPECTED_HISTORICAL_VERSION or legacy input gate. |
| Validation required | Independent cutoff/correction timelines, strict version rejection, field/identity/missingness/U0 parity and prediction/outcome separation tests, then separately authorized source evidence review |
| Current status / unauthorized | POLICY DIRECTION APPROVED; EVIDENCE GATE OPEN. Attributable pre-cutoff evidence is required; otherwise the affected scope remains synthetic-only. Causality/compatibility is unproved. No source registration, acquisition, protected read, adapter implementation or development run authorized. |

| B02 field | Decision record |
|---|---|
| Decision owner | Product owner chooses deployment/threat boundary, key and recovery custody; independent reviewer assesses enforcement; a genuinely separate custodian is needed for frozen confirmation requirements |
| Alternatives | A2-independent owner/custodian/verifier separation; A2-single-operator with an offline owner signer and isolated verifier/broker/workers; A2-procedural human/CLI controls with no hostile-worker assurance and continued synthetic-only operation |
| Owner-approved direction | A2-single-operator is the development-only target. Do not claim independent custody. Keep confirmation closed without the separately required independent custodian. |
| Repository evidence | Human workflow, filesystem-owner checks, explicit activation flag, shared local web principal; no existing research key issuer, revocation or nonreplay ledger |
| Security/statistical consequence | Can constrain a compromised worker if OS/key/ledger separation is effective; cannot prevent the owner/root forging history, prove the owner's ignorance of outcomes or recover lost custody proof. Same-UID unrestricted workers defeat the proposed boundary. |
| Exact contract changes | Grant/receipt profile and assurance literals, owner trust-anchor enrollment/rotation, verifier-only key distribution, bounded expiry/time policy, nonce ledger/revocation epoch/high-water recovery records. Exact algorithms/providers/clock tolerances remain owner-reviewed choices, not defaults. |
| Validation required | Worker cannot access issuer key/ledger/source roots; signature/scope/expiry/replay/revocation race tests; missing-anchor, clock rollback, restored-spent-grant and offline-key recovery failures; report no independent-custody claim |
| Current status / unauthorized | POLICY DIRECTION APPROVED; CONTRACT GATE OPEN. A2-single-operator is selected for development only; independent custody remains mandatory for confirmation. Concrete custody, isolation, clock, nonce, revocation and recovery controls remain unapproved. No key generation, credentials, new account/service installation, grant issuance, confirmation access or real execution authorized. |

| B03 field | Decision record |
|---|---|
| Decision owner | Product owner chooses the meaning of combination authorization; independent C2 contract/protocol review before any API work |
| Alternatives | EVAL_AUTH: authorize at a new computation boundary; PUBLICATION_ONLY: authorize full conditional computation beforehand, then gate exposure/publication; keep C2 synthetic-only |
| Owner-approved direction | EVAL_AUTH with versioned public pure component/combination interfaces and a controller authorization checkpoint; do not compute and discard to simulate denial |
| Repository evidence | evaluate() directly calls run('UM1UA1') when components pass; EvaluationResult rejects a NOT_EVALUATED combination in that case; current authority/resource literals are synthetic-only |
| Security/statistical consequence | EVAL_AUTH limits approved-code computation before permission. PUBLICATION_ONLY consumes outcomes/resources and creates exposure even when output is suppressed; it cannot honestly be called NOT_EVALUATED. Neither changes fixed selection priorities. |
| Exact contract changes | New versioned authority/header/result contracts; public component result and grant-bound combination request types; separate incomplete authorization-stop record; stage-specific input views bound to the full freeze; pure final selection API. Preserve existing v1 synthetic schemas and statistics. PUBLICATION_ONLY instead needs explicit compute-versus-expose grants and computed-but-withheld execution receipts. |
| Validation required | Prove zero combination membership/score/resample calls before a valid checkpoint, malformed/missing/revoked permit rejection, preserved all-draw statistics/selection and schema compatibility; publication-only tests if that alternative is selected |
| Current status / unauthorized | POLICY DIRECTION APPROVED; CONTRACT GATE OPEN. EVAL_AUTH is selected and a later design-only versioned contract proposal is permitted. No C2 code changes, direct private calls, real evidence conversion or combination computation authorized. |

| B04 field | Decision record |
|---|---|
| Decision owner | Product owner plus protocol/resource reviewer define when the frozen resource gate is satisfied |
| Alternatives | Acyclic measured stage receipts and separately finalized result resource gate after explicit review; full-computation publication gating with upfront scope (still needs timing resolution); remain blocked |
| Owner-approved direction | Design the first option with EVAL_AUTH, but do not claim it satisfies the existing gate until its phase/publication coverage is approved. No provisional PASS or fabricated measurement. |
| Repository evidence | ResourceGate is supplied to evaluate before work; PackageResult PASS requires it; combination uses that PASS; freeze section 10 includes I/O and publication. |
| Security/statistical consequence | A future publication cost cannot justify present gate admission. Changing which resource phase counts can change combination admission/selection and cannot be hidden as orchestration. |
| Exact contract changes | Versioned component-statistic, completed-stage resource, conditional-permit and final-selection/result contracts; explicit measured interval definitions, final commit boundary and acyclic upstream edges. Any changed frozen resource-gate meaning needs an explicit protocol clarification/amendment. |
| Validation required | Last-write/fsync/commit breach, revoked permit at dispatch, interrupted stage, identical numerical core across measured attempts, and independent proof no resource receipt attests future work |
| Current status / unauthorized | POLICY DIRECTION APPROVED; CONTRACT GATE OPEN. Staged completed-resource receipts plus a final all-inclusive gate are selected, but exact intervals, retry accounting, commit boundary and any protocol amendment remain unapproved. No outcome access, resource PASS, conditional dispatch or eligible publication authorized. |

| B05 field | Decision record |
|---|---|
| Decision owner | Product owner selects supported platforms and literal budgets; independent systems reviewer accepts backend enforcement |
| Alternatives | Proven native profiles on macOS and Linux; a separately approved contained runtime with explicitly narrowed platform claims; synthetic-only unsupported-host refusal |
| Owner-approved direction | Qualify supported profiles with measured synthetic enforcement; reject unsupported profiles before outcomes. Prioritize contained Linux/Python 3.10 and do not imply native macOS enforcement from Linux or sampled observations. |
| Repository evidence | C1/C2 benchmark reporting is not a controller; frozen ceilings exist but no C3 OS backend or scratch/output/controller budget does. |
| Security/statistical consequence | Unbounded parent/descendant/spill work or missed RSS/thread bursts invalidates resource PASS; changing budgets after results is impermissible. |
| Exact contract changes | Resource-profile and receipt schema with approved numeric scratch/output/inode/controller limits, process/thread/RSS accounting, watchdog/retry/cleanup policy and supported backend identity |
| Validation required | Real backend denial/termination for brief spikes, children, threads, timeout, output/disk overflow; end-to-end 700-player and conditional-combination measurements on declared profiles |
| Current status / unauthorized | POLICY DIRECTION APPROVED; CONTRACT GATE OPEN. A proved contained Linux/Python 3.10 profile is the first target; native macOS remains development/synthetic-only until separately proved. Additional numeric budgets and enforcement details remain unapproved. No runtime installation, benchmark, experiment or real protected access authorized. |

| B06 field | Decision record |
|---|---|
| Decision owner | Product owner approves a frozen encoding/identity specification after source and statistical reviewer checks |
| Alternatives | Minimal ASCII decimal verified official integer code; another explicitly frozen bijective representation with independent ordering vectors; reject unresolved bridges |
| Owner-approved direction | Minimal ASCII decimal with UTF-8 lexical sorting unchanged. Reject missing/ambiguous mappings including blank-only players. |
| Repository evidence | Historical code is integer; C2 player_code is text and serial union sorts UTF-8 bytes; blank-only codes participate. Existing compatibility examples are incomplete. |
| Security/statistical consequence | Representation changes RNG-to-player assignment; inferred/name-based mappings can merge distinct players or falsely split dependence. |
| Exact contract changes | Versioned bridge encoding/evidence schema and complete adapter mapping specification; separate provenance hashes; no expansion of existing membership digest scope |
| Validation required | Literal integer/text/ordering vectors, collisions/renames/transfers/blank-only identities, full distribution/null/blank parity, unchanged seeded serial stream under approved mapping |
| Current status / unauthorized | POLICY DIRECTION APPROVED; CONTRACT GATE OPEN. Minimal ASCII decimal with existing UTF-8 lexical ordering is selected; independent vectors and complete bridge/parity proof remain required. No source is opened, and no kernel/schema change or real evaluation is authorized. |

| B07 field | Decision record |
|---|---|
| Decision owner | Product owner approves identity/adoption/durability claims; independent publication/recovery reviewer validates filesystem protocol |
| Alternatives | Versioned deterministic core plus distinct attempt/resource/publication receipts; attempt-specific complete results with equality assessed only on an explicitly defined core; no completed publication until identity/timing is resolved |
| Owner-approved direction | Separate deterministic core from immutable attempt evidence, with exact-byte-only completed-result adoption; approve the acyclic graph jointly with B04. Never strip fields from v1 bytes retrospectively. |
| Repository evidence | C2 embeds resource evidence digests; artifact_snapshot.py offers read-once capture but no semantic-slot reservation, publisher election or atomic multi-file commit protocol. |
| Security/statistical consequence | Conflating equal scores with identical provenance permits substitution; restoration/retry may erase exposure or spend the same grant twice. Atomic visibility does not prove crash durability. |
| Exact contract changes | Versioned evaluation/core/attempt identities, publication reservation/receipt, adoption/conflict/failure/recovery contracts and supported no-replace commit/durability profile |
| Validation required | Two-writer races, exact adoption versus different receipt bytes, injected crash at every transition, output inventory verification and restored ledger reconciliation; C3.5 must prove guarantees absent from the snapshot helper |
| Current status / unauthorized | POLICY DIRECTION APPROVED; CONTRACT GATE OPEN. Separate deterministic-core, attempt/evidence and publication identities with exact-byte completed-result adoption are selected. The exact acyclic graph, no-replace/durability profile and recovery proof remain unapproved. No package is published and no recovery drill or backup is created. All seven implementation/evidence gates remain prerequisites. |

### 3.2. Owner-approved policy directions and change control

On 2026-09-22 the product owner approved all seven recommended directions:

1. **B01:** require attributable pre-cutoff evidence for causal use of
   historical-v3.1 values; otherwise keep the affected scope synthetic-only.
   Do not silently downgrade the causal claim. Keep the legacy v2 backtester and
   its version gate outside the C3 path and unchanged.
2. **B02:** target A2-single-operator for development only. Do not claim
   independent custody. Confirmation remains closed until a genuinely independent
   custodian and the frozen confirmation prerequisites are established.
3. **B03:** select EVAL_AUTH. Durable authorization must precede approved-code
   combination membership, scoring or resampling. Preserve the current synthetic
   v1 API; any split API is a separately reviewed, versioned contract.
4. **B04:** use completed stage receipts for conditional dispatch and a final
   all-inclusive resource gate covering verification and publication. No receipt
   may attest future work, and a final write/commit breach fails the attempt.
5. **B05:** prioritize a proved contained Linux/Python 3.10 enforcement profile.
   Native macOS remains development/synthetic-only until equivalent continuous
   enforcement is independently proved. Freeze missing numeric budgets using
   outcome-free synthetic evidence before protected outcome access.
6. **B06:** use minimal ASCII decimal encoding of each positive official integer
   player code with no sign, leading zero, whitespace or alternate spelling;
   preserve existing UTF-8 lexical ordering. Missing, ambiguous, inferred or
   name-based bridges fail closed, including for blank-only members.
7. **B07:** separate deterministic-core identity from immutable attempt,
   authority/resource and publication evidence. Completed-result adoption requires
   exact semantic identity, exact file bytes and matching complete provenance;
   differing receipts identify a distinct attempt and never overwrite a completed
   slot.

These choices freeze product direction, not missing engineering facts. Exact
algorithms, trust anchors, clock bounds, grant lifetimes, budgets, stage intervals,
schemas, filesystem profiles and evidence remain subject to separately reviewed
contracts and validation. Until those gates close, implementation and protected
access remain blocked.

A direction may change later only through a prospective, versioned protocol/design
amendment with independent review before the affected source, outcome or result is
opened. A change after exposure cannot rewrite the old protocol or result; it must
create a new request/protocol identity and preserve the earlier evidence and
decision history. Convenience, failed gates or observed performance are not valid
reasons to relabel an old run.

## 4. Scope, architecture and trust boundaries (A)

C3 owns only request validation, authorization enforcement, exact resolution,
causal release, adapters, phase transitions, guarded joining, resource evidence,
exclusive publication, sanitized failures and re-verification. Split it into
reviewable slices (section 16), not one large loader/evaluator command.

C1 remains pure: U0 mechanics/adapters, UM1 joint minute distributions, UA1
rate mixtures and candidate assembly. C2 remains pure: realized-value derivation,
proper scores, missingness/coverage, pair-specific ranking membership, both RNG
streams, complete-family bounds, subgroup gates and fixed statistical choice.
C3 must not duplicate these formulas or decide a different winner. New authority
wrappers may be designed only through B03 under the owner-selected EVAL_AUTH
direction. Section 6.3 retains PUBLICATION_ONLY as an unselected alternative.
I/O, clocks, credentials and process controls never enter C1/C2.

Preserve three evidence layers: historical human reasoning records beliefs and
motivation; trusted engine artifacts retain validated decision semantics;
current football/manager evidence needs its own attributable, time-valid contract.
None can substitute for another. Human reasoning, owner actions and later outcomes
cannot tune C1/C2 or rewrite predeadline artifacts. No current evidence is accessed
by this design remediation.

Proposed components and access matrix:

| Component | Permitted reads and writes | Forbidden capability |
|---|---|---|
| Parent controller | Reviewed request, sanitized grant metadata, stage ledger, resource counters; fixed-code receipts | Raw source/outcome rows, production operations, issuing grants |
| Source broker (custodian only when separately appointed) | Only exact stage-authorized source objects; bounded causal partitioning into separate views | Publishing research conclusions, target values in prediction views |
| Prediction worker | One target's authorized cutoff view at a time; C1; private staged prediction outputs | Outcome package/root/capability, full-season raw tables, network, arbitrary children |
| Join/evaluation worker | Published frozen prediction plus separately authorized frozen outcomes; C2 | C1 invocation, source discovery, changing predictions, confirmation discovery |
| Publisher | Exact verified staged bytes and terminal evidence; a single result namespace | Recomputing scores, rewriting existing artifacts, operational publishing |
| Reviewer/exporter | Allowlisted sanitized metadata and aggregates | Source rows, resolution map, credentials, manager state |
| Future backup tool | Separately authorized opaque encrypted checkpoint capture/restore | Research grants, decryption authority by implication, execution or promotion |

Owner-selected EVAL_AUTH direction (exact flow contracts remain proposed, not
implemented or approved; each arrow requires a validated receipt):

```text
reviewed protocol + request + explicit stage grant
  -> enforced preflight -> authorized source broker -> causal target views
  -> pure C1 -> adapter -> exclusive complete prediction freeze
  -> separately authorized outcome resolution -> outcome freeze -> guarded join
  -> pure C2 components -> conditional grant AND every component gate
  -> pure C2 combination if permitted -> measured finalization
  -> exclusive immutable research result -> human review
  -> STOP (separate confirmation / promotion authority required)
```

A different directory or Python object is not a security boundary. Workers need
an OS-enforced least-privilege environment: no general source-root mount, no
inherited source descriptors, credentials, network or arbitrary executable
access. B02/B05 block claims of enforcement until those controls are selected
and independently demonstrated. An owner/root compromise, hostile kernel,
forged upstream historical timestamps and colluding custodian are outside local
byte-integrity assurance; such events require stopping and authority review.

Deferred: real source acquisition/registration, real development execution,
confirmation assignment/execution, independent reproduction on real sources,
Task033D decision corpus/diagnostics and Task033E promotion. Task026C, production
xFP, optimization, reliability, decisions, journals, FPL actions and scheduling
are explicit non-goals. Research success gives no runtime route into production.

## 5. Exact source classes, chronology and resolution (B)

The proposed resolver accepts only the following closed logical source classes.
There are currently **zero authorized real-source instances** in this design.
Each instance must be named by a reviewed registration, never discovered.

| Class | Logical identity tuple | Released purpose |
|---|---|---|
| SYNTHETIC_FIXTURE | generator revision, fixture-definition hash, seed, schema | Generated test objects only; no production fixture reinterpretation |
| PREDEADLINE_STATE | source contract/version, season, GW, snapshot digest | Universe, official element/code, target position/team, target-bound availability and deadline |
| CAUSAL_FIXTURE_HISTORY | source contract/version, season, target GW, cutoff-view digest | Lower-GW performance fields whose exact versions satisfy availability checks |
| FIXTURE_ASSIGNMENT | season, assignment-version digest, fixture IDs, classification | Registered target fixture sets and structural blanks; retain finalized-context limitation |
| IDENTITY_BRIDGE | season, bridge version, mapping digest, evidence digest | Historical IDs to official element/code and fixture/team namespace mapping |
| PRESEASON_TEAM_MEMBERSHIP | season, prior-season membership digest, source digest | Promoted-team diagnostic only |
| FINALIZED_OUTCOME | source contract/version, season, GW, immutable outcome-version digest | Separately gated fixture minutes/starts/goals/assists and finalization evidence |

Registrations bind exact upstream repository/commit/blob where applicable, byte
SHA-256 and size, schema/parser revision, allowed columns, population, cutoff
policy, exposure class, permitted stage and source-authority evidence. Catalogue
presence is not permission. historical-v2/v3, arbitrary URLs, filesystem globs,
"latest", manager/account records and legacy experiment tables are disallowed.
Only exact development seasons `2023-24`, `2024-25`, targets 2–38 are contemplated;
source state GW1 is also needed. No expansion to the catalogue's other season.

A private map translates approved logical IDs to capabilities/locations. It is
not embedded in a request, manifest or review package. Before authorization,
C3 may parse a bounded sanitized registration supplied by the authority; it may
not stat, enumerate, hash, decompress or parse protected source objects.
Authorization must precede every protected read, including metadata, schema and
Parquet footer reads, not only data-row reads. A file containing target outcomes
cannot be opened
under a prediction-only capability even to copy it opaquely. Mixed archives
require a separately granted blind broker preparation stage; the model and
analyst do not receive mixed bytes. That grant must explicitly cover mechanical
partitioning and its sealed contents and is unavailable here (B01/B02).

Every released row must trace to immutable bytes, parser and mapping revisions:

1. Season and GW/deadline come from the registered snapshot/event identity;
   snapshot capture evidence is strictly earlier than its deadline. A filename
   timestamp or Git timestamp alone is a declared timestamp, not authentication.
2. The target and each historical source-GW player universe prove registration,
   position and team then. No end-of-season universe/position/team substitution.
3. Each history fixture has the same season, lower assigned GW and kickoff
   strictly earlier than the target deadline. Additionally, exact field-version
   availability and match-completion evidence must precede cutoff for a causal
   claim. A kickoff-before-deadline match still in progress fails this claim.
4. History contains only permitted columns (identity, GW, fixture, source
   position, minutes, starts, xG/xA, provenance). No target goals/assists,
   total FPL points, news text, ownership, prices or future states reach C1.
5. Each correction has an immutable version and attributable availability time.
   An after-cutoff correction cannot replace a before-cutoff value. Unknown
   revision time is not "probably safe". Preserve superseded bytes and reason;
   never silently prefer the newest, most complete or best-fitting record.
6. Preserve `restricted_pseudo_backtest` and
   `fixture_assignment_verified_predeadline=false` when required by the freeze.
   Stronger scheduling replay requires new evidence and separate review.

The new source contract must separate valid-time (football event), knowledge-time
(when that exact value was available), capture-time and parsing-time. Row-level
lineage may reference a shared immutable proof by hash; it cannot consist only
of unchecked booleans. historical-v3.1 is a compatibility input specification,
not sufficient authentication. B01 must decide the admissible source derivation
without silently changing the frozen value semantics or study classification.

Missing expected history rows are corruption. A verified absence of registration
or fixture is structural absence. Legitimate nullable xG/xA/starts follow the
frozen C1 missingness rules, not invented zeros. Conflicting keys, unsupported
schema/version, unproven chronology or an unapproved correction stop the source
phase; no partial dataset or success receipt. A new source version requires a
new prereviewed request; outcome exposure is permanently recorded, not reset.

### 5.1. Source-serving decision and v3.1 compatibility gate

**Proposed-path decision for this design:** `historical_backtest.py` is not in
C3's runtime source, prediction, outcome or evaluation path. Keep its exact
historical-v2 gate unchanged. Do not monkey-patch its constant, rewrite a manifest
version, bypass `_validate_input_manifest`, or copy its full-table loading as a
prediction loader. Its code remains historical comparison context, not a v3.1
compatibility oracle. Repository evidence supports this separation; no evidence
inspected requires weakening the v2 gate.

Name the proposed source-serving module precisely:
`src/fpl_decision_engine/research/c3/source_adapter.py`
(import name `fpl_decision_engine.research.c3.source_adapter`). This module does
not exist and is not created here. It would run only inside the authorized
broker, accept stage-scoped descriptors/capabilities, enforce exact registered
historical-v3.1 schema/version plus the new as-of evidence contract, and release
canonical per-target C1 views or separate outcome views. It would not discover
sources, download archives, accept v2/v3 aliases, read manager evidence, expose a
mixed season table to C1, invoke the legacy backtester or publish results.
Separate pure forecast-to-C2 mapping remains C3.3, not hidden inside this loader.

Required compatibility evidence before implementation approval: a reviewed
field-by-field mapping from the v3.1 PLAYER_FIXTURE_SCHEMA, PREDEADLINE_SCHEMA,
FIXTURE_SCHEMA and relevant FEATURE_SCHEMA semantics to TargetContext,
HistoryFixture, peer membership, U0 input, bridge and outcome contracts; exact
same-season/GW/fixture identity handling; null/zero/new-entrant/blank/DGW/transfer
cases; preservation of xG/xA event values and correction provenance; and a proof
that availability metadata is evidence-backed rather than asserted booleans.
Do not use aggregate feature rows as a replacement for missing joint fixture
starts/minutes history. Unknown v3.1 source completeness stays unknown.

Synthetic compatibility tests must independently verify field/row membership,
U0 point/nullable behavior against the frozen production formula on generated
inputs, full C1/C2 distribution parity, strict v2/v3/wrong-schema rejection and
zero target/future access. Existing historical_backtest tests can document its
unchanged v2 behavior; they do not certify v3.1 parity. Eventual actual source
compatibility requires separately authorized immutable evidence review, not
opening sources during design. B01, B03 and B06 remain open; the new adapter name
creates neither compatibility nor execution authority.

## 6. Stage authority, timing and replay (C)

### 6.1. Achievable authority profiles and owner decision (B02)

Current project practice is single-operator human approval, local filesystem
ownership and explicit command gates. No cryptographic research-grant mechanism
or independent research custodian exists in the inspected repository. Cryptography
is a proposed addition, not a description of current behavior. Human approval
can be real authorization without a signature; a protected worker cannot safely
infer that approval from an editable flag/file under the same unrestricted UID.
Separate a human role from a process privilege boundary and from genuinely
independent human custody. They are three different assurances.

| Option | Concrete deployment and supported threat boundary | Limit and current disposition |
|---|---|---|
| A2-independent (ideal) | Owner approves request; a genuinely independent custodian controls sealed source release/exposure attestation; isolated verifier/broker validates grants using public verification material; worker has neither issuer secrets nor broad source/ledger access | Stronger separation of duties, still assumes trustworthy custody/OS and attributable sources. No custodian is established here; this cannot be claimed deployed. |
| A2-single-operator (owner-selected development direction) | One owner intentionally approves a bounded request and signs with an offline or separately access-controlled key. A distinct verifier/broker privilege domain has verification-only material and owns replay/revocation state. A confined worker can use only stage-specific broker access and cannot read keys or alter authority state. | Selected as policy direction, subject to a reviewed concrete contract and real isolation tests. The owner remains issuer/operator and can override the host. It proves neither independent custody nor that the owner has never seen outcomes. |
| A2-procedural (current-style baseline) | Human records exact approval, ownership-protected request and explicit CLI acknowledgment; same-owner local process checks phase/files before executing | Useful against mistakes, not grant authenticity/replay integrity under a compromised same-UID worker or host. Recommend keeping this profile synthetic-only for C3. A request for weaker real-operation assurance needs explicit new review, not an implicit downgrade. |

The owner selected A2-single-operator **for development only**, accepting the
stated limits subject to a later reviewed contract and proof of the required
separation. Independent confirmation custody is still required by the frozen
protocol; a self-signed owner statement does not satisfy untouched-season
attestation. If no independent custodian is available, confirmation remains
unassigned/closed even if development infrastructure passes. This policy choice
does not select the missing algorithms, numeric bounds, custody mechanism or
enforcement backend and does not authorize their implementation.

Minimum viable controls to specify and validate for A2-single-operator:

- **Key custody and identity:** intentional owner enrollment binds a verification
  key to an opaque owner identity and exact approval record. Signing key stays
  outside worker/broker-readable storage and inherited descriptors/environment.
  Use separate interactive signing from execution; no background worker signer.
  Owner chooses offline/hardware/separate-account custody and recovery/rotation
  procedure. No keys are generated or inspected here. A signature proves control
  of the enrolled key, not independent human intent or historical data truth.
- **Issuer/verifier separation:** verifier uses public verification material only;
  broker owns immutable registration and authority state. Worker runs under a
  different enforced privilege domain, not just another directory/process with
  unrestricted same-UID access. Owner must choose a proved OS/container/account
  profile jointly with B05. Unknown enforcement blocks real access.
- **Scope, expiry and clocks:** explicit finite not-before/expiry, stage, code,
  sources, resource profile and audience bind each grant. Owner chooses numeric
  lifetime and time tolerances before execution; no default supplied. Options
  are an approved authenticated time source or an operator-attested UTC launch
  anchor plus a monotonic elapsed clock covering suspend/resume under a reviewed
  local-clock trust model. The latter trusts the owner/host and cannot establish
  archive availability. Reboot, rollback, uncertain suspend accounting or stale
  time evidence stops use pending reauthorization/reconciliation; no auto-renewal.
- **Nonce/replay:** verifier atomically reserves a one-use nonce and experiment
  stage before any protected access; durable record precedes the broker release.
  A fresh nonce is not permission to repeat an exposed study. Worker cannot edit
  the ledger. A valid signature alone does not detect spent grants.
- **Revocation:** issuer-controlled revocation epoch/list is checked by the broker
  at the approved access linearization point and by the controller at computation
  dispatch/publication; stale or unavailable state denies progress. Owner must
  approve freshness bounds and cancellation behavior, including ongoing reads.
  Worker cannot suppress revocation or mint a replacement grant. Released bytes
  cannot be recalled and revocation cannot undo exposure.
- **Restore rollback:** preserve an independently retained high-water digest/epoch
  and exposure/nonce history outside the restorable worker checkpoint, e.g. an
  owner-kept separate medium or a separately approved append-only service.
  Separate media under the same owner is not independent human custody, and a
  static digest without freshness evidence does not prove the latest state.
  Owner chooses retention/update and reconciliation procedure. Missing/uncertain
  high-water evidence means no grants are re-enabled; do not restore-and-trust a
  signed old ledger or claim an offsite backup exists.
- **Worker compromise:** assume the worker can be faulty/malicious and try to
  forge requests, read alternate paths, spawn or exfiltrate. Broker isolation,
  signature checks, least-privilege mounts/FDs, no network, bounded allowlisted
  outputs and parent/OS enforcement must prevent new unauthorized reads or grant
  issuance. Once authorized values are delivered, their computation cannot be
  cryptographically undone or proven unknown to that worker. Owner/root, signer,
  verifier/broker or kernel compromise is outside this local profile's assurance;
  stop and investigate rather than claiming independent verification.

**Recorded owner policy decision:** A2-single-operator is the development-only
target and does not satisfy the independent-confirmation requirement. **Still
required before B02 closes:** identify issuer/custodian/verifier roles without
exposing personal details and approve custody, trust-anchor/rotation, worker
isolation, grant authentication format, expiry and clock policy,
nonce/revocation linearization and recovery high-water procedure. A later
reviewed specification must supply concrete algorithms, bounds and backend
evidence. No arbitrary numeric/security default closes B02. Ownership alone or
two services under the same unrestricted user is insufficient for the proposed
hostile-worker boundary. No existing keys/services are assumed.

### 6.2. Proposed grants, pre-read checks and separate permissions

Proposed grant schema name: `task033c3-stage-grant-v1` (not implemented). Under
the recommended profile an authenticated issuer outside the worker attests:
grant ID/nonce, authority-profile ID, opaque owner subject and issuer/key ID,
experiment/protocol/request hashes, audience, stage/purpose, allowed logical
source digests/columns/seasons/GWs, selected package/freeze binding if applicable,
code/environment hashes, resource profile, publication namespace, not-before/
expiry, use limit, revocation epoch and approval-record digest. Profile assurance
is explicit; single-operator receipts must never assert independent custody.
Credentials and approval conversation contents stay outside the artifact.
An editable digest of prose is not an authenticated grant. The same human may
approve and operate under the selected single-operator profile; the worker must
never issue its own permission. The profile direction is approved; its concrete
format/parameters remain proposed in section 17.3.

| Permission | Required prerequisite | Grants no authority for |
|---|---|---|
| DEVELOPMENT_PREDICTION | Reviewed exact request/source contract, causal broker permission, enforced preflight | Development outcome resolution or metrics |
| DEVELOPMENT_OUTCOME_JOIN | Complete prediction publication receipt, exact outcome IDs, exposure ledger and fresh preflight | Regenerating any forecast or opening confirmation |
| CONDITIONAL_COMBINATION under EVAL_AUTH | Explicit prebound conditional grant, completed component gates/resources under B04, fresh verification and durable permit before new public C2 combination call | Computing membership, scores or resamples before permit; bypassing gates; alternative combinations |
| CONDITIONAL_COMBINATION under PUBLICATION_ONLY (alternative, not selected) | Explicit permission for conditional computation before evaluate is called, plus separate publication/exposure permission | Calling withheld results NOT_EVALUATED, erasing exposure, using publication permission as retroactive compute permission |
| CONFIRMATION_PREDICTION | Separate owner receipt, one genuinely untouched independently custodied season, exposure audit, selected development package and reviewed code | Confirmation target outcomes, scores or season substitution |
| CONFIRMATION_OUTCOME | Complete confirmation-season prediction freeze, separate one-use grant and custodian release | Interim metrics, refitting, another confirmation attempt |
| PUBLICATION | Exact package kind, semantic identity, namespace, export classification and final evidence; combination policy named | Reading new inputs, retroactively authorizing computation, public uploading, backup or model eligibility |
| PROMOTION_REVIEW | Separate future human-reviewed task and explicit owner scope | Automatic model deployment or FPL action |

Prediction and outcome grants remain separate even if issued together. Outcome
activation requires the actual complete freeze. Statistical success is not a
conditional grant. Under the owner-selected EVAL_AUTH direction, absent permission stops
before combination computation, preserves component evidence only as incomplete
execution, and records combination NOT_EVALUATED outside current completed C2 v1
EvaluationResult. Never manufacture component FAIL/resource FAIL to trick the
current evaluator into skipping the combination.

Required order: validate bounded request -> authenticate selected-profile grant
and scope -> check time/expiry/revocation -> atomically reserve nonce/stage ->
persist pre-read receipt -> broker opens the exact protected object -> verify
stable bytes -> parse only that snapshot. Receipt failure prevents the open.
Authorization precedes **every protected read**, including metadata, schema,
Parquet footer, decompression, snapshot re-read and recovery validation, with
permission still current for its stage. A metadata-only source claim is no bypass.
Unprotected bounded sanitized authority metadata may be examined to request access;
this does not permit reading the underlying protected manifest or file.

The broker, not a caller Boolean, enforces scope and the approved revocation
linearization point; record ordered access/release events and byte scope. Before
new read/release, dispatch and publication, revalidate current authority. A
cancelled or interrupted read remains an exposure event for any delivered bytes.
Exact nonce replay can only retrieve a completed receipt under current read
permission; it cannot read source bytes or repeat scoring. Offline revocation/time
uncertainty and lost restore high-water state stop execution. These are proposed
minimums pending the specific B02 contract, not evidence of deployed guarantees.

### 6.3. Evaluation-time versus publication-time combination control (B03/B04)

**EVAL_AUTH policy is owner-approved; the split contract remains open.** Current C2
`evaluate()` has no external callback, stage permit or return between the two
component results and `run('UM1UA1')`. A wrapper checking permission after return
is too late. Supplying a missing/FAIL UM1UA1 ResourceGate does not prevent its
computation when both components pass; `_Pair.run` computes statistics before
checking its supplied resource status. Withholding component resource success to
force a skip would falsify the study result. No such workaround is permitted.

| Choice | Exact control point and exposure | Contract consequence |
|---|---|---|
| EVAL_AUTH (owner-selected; contract open) | Pure component evaluation returns first. Controller validates completed component statistical/resource evidence, prebound combination scope, current revocation/time and remaining enforced budget. It durably reserves the permit before the combination membership/score/resample function can be invoked. Missing permit stops without computation. | Requires separately reviewed/versioned public component and combination APIs and result types; cannot be implemented by current evaluate plus an outer check. C2 stays free of I/O, clocks and signature verification. |
| PUBLICATION_ONLY (alternative) | Owner explicitly authorizes conditional computation up front. Current-style evaluate may then compute the combination after component PASS, creating outcome-joined derived values in memory/temporary storage. Publisher checks an additional gate before exposing a completed package. | New real-authority contracts still required. Receipt must distinguish COMPUTED_WITHHELD from NOT_EVALUATED; resources and exposure ledger include withheld computation. Denial of publication cannot erase computation or license a fresh unseen trial. |

PUBLICATION_ONLY is valid only if the owner intentionally permits that computation;
without such prior permission it computes unauthorized results, even if discarded.
It protects a release boundary, not the requested computational boundary, incurs
avoidable resource/privacy/recovery risk and is not recommended. It also cannot
hide a computed combination result and still claim a completed selection under
v1: EvaluationResult requires its combination status when both components pass.
Do not mutate a valid result into a false NOT_EVALUATED package. Withheld values
stay unavailable to analysts and any retained derived package requires separately
approved storage/retention; discard records must retain exposure and failure facts.

Proposed EVAL_AUTH contract change set (names are review placeholders, not APIs
created here): `evaluate_components` returns immutable component statistical
records; `evaluate_combination` accepts that exact component-result binding,
prediction/outcome/join IDs and a controller-validated typed permit; pure
`finalize_selection` retains the existing fixed priority. B04 must determine which
completed resource records are required before dispatch and final selection.
Grant authentication is in C3; pure C2 validates structural scope/bindings and
cannot itself prove cryptographic authorization. Public v1 evaluate and its
synthetic schemas remain unchanged. Never call private _Pair as an escape hatch.

Introduce versioned real-versus-synthetic authority/header/resource/result
contracts, component-only and blocked execution receipts, and stage-specific
input views bound by hash to the full four-slot prediction freeze. The dormant
combination is still constructed/frozen before outcomes, but the component call
need not receive its forecast payload. Withheld forecasts do not make the
combination mathematically unknowable: authorized component parameters/outcomes
can permit derivation by a malicious worker. The guarantee is controlled dispatch
of approved code under the chosen worker threat boundary, not proof that a
compromised process did no unobservable arithmetic. B02/B05 must confine release,
outputs and privileges; owner accepts that explicit limit or keeps execution
blocked. The private _COMBINATION_AUTHORITY sentinel is not a security capability.

**Recorded owner policy decision:** EVAL_AUTH is selected, so conditional
permission controls computation and must bind before outcomes. A separate
versioned C2 contract/API design review is permitted as design work, including an
incomplete authorization-stop state that does not change statistical choice.
**Still required before B03/B04 close:** approve the exact split API and prove
byte-identical RNG/statistical behavior, then resolve measured component/final
resource intervals, publication completion and acyclic receipts. Selecting
EVAL_AUTH does not settle resource timing. Any reinterpretation of frozen
resource PASS must be a reviewed protocol clarification/amendment. This decision
authorizes no code work, protected reads or evaluation.

## 7. Prediction/outcome phases and state machine (D, M)

Proposed separate roots are logical names, not executable paths:
`requests`, `prediction-staging`, `prediction-store`, `outcome-staging`,
`outcome-store`, `evaluation-staging`, `result-store`, `failures`, `access-ledger`.
The evaluator cannot write prediction-store; prediction workers cannot resolve
outcome-store or its private map. Scratch roots are unique owner-private attempts.
The controller holds no broad source capability to accidentally pass through.

All four development forecast slots, including dormant UM1UA1 parameters, must
be complete as a package and published before outcome paths become resolvable to
the join/evaluation side. "Complete package" includes explicitly missing
candidate values under the protocol; it does not falsely mark every forecast
complete. No target-by-target development metric feedback during construction.
For confirmation, the blind loader may release legitimate earlier history for
later targets, but all GWs 2–38 must be frozen before aggregate outcomes/scores
are accessible to the analyst. Same-GW updates remain forbidden.

| State | Required transition evidence | Next permitted action |
|---|---|---|
| REQUEST_VALIDATED | Reviewed protocol/request and supported contract/profile | Seek exact prediction grant; no protected reads |
| PREDICTION_AUTHORIZED | Durable pre-read receipt and successful enforcement preflight | Causal views only |
| PREDICTION_BUILDING | Stable source/view manifests and access ledger | Pure C1 and reviewed adapter |
| PREDICTION_FROZEN | All forecast bytes, inventory and exclusive complete publication | Revoke prediction writer; reverify freeze |
| OUTCOME_AUTHORIZED | Separate grant bound to that freeze, resource preflight, exposure reservation | Resolve exact outcome objects |
| OUTCOMES_FROZEN | Independently verified bytes/finalization and outcome receipt | Guarded join only |
| JOIN_VALIDATED | Exact key sets, ledger, coverage/missingness reconciliation | Component evaluation |
| COMPONENTS_EVALUATED (owner-selected EVAL_AUTH direction) | Versioned component results and completed resource gates; no combination work | Controller must verify/reserve the separate evaluation-time permit |
| COMBINATION_AUTHORIZED (owner-selected EVAL_AUTH direction) | Durable fresh scope/nonce/revocation check binding exact component results and full freeze | New public pure combination API may construct membership, scores and resamples once |
| COMBINATION_COMPUTED_WITHHELD (PUBLICATION_ONLY alternative) | Computation was explicitly authorized before evaluate; conditional gates passed; no exposure/publication permission yet | No analyst exposure or final selection claim; retain computation/resource/exposure accounting, do not label NOT_EVALUATED |
| EVALUATION_COMPLETE_PENDING_PUBLICATION | All required evaluated packages, final resource protocol resolved | Exclusive finalization only |
| RESEARCH_RESULT_PUBLISHED | Terminal receipt and fully verified immutable chain | Independent human review; no automatic next study |

These alternative rows are mutually exclusive policy branches, not runtime
fallbacks. The owner-selected EVAL_AUTH direction is not expressible through today's
monolithic C2 API; JOIN_VALIDATED must not call that API for real research. Under
PUBLICATION_ONLY, upfront computation permission is required at JOIN_VALIDATED
before evaluate can run; a later release gate never retroactively permits it.
COMBINATION_COMPUTED_WITHHELD cannot transition to an EVAL_AUTH NOT_EVALUATED
record. The policy ID is immutable in request/grants/receipts. All new dispatch
and final transitions are non-executable until B02/B03/B04/B07 are resolved.
A reader must never accept a candidate result merely because some files or a C2 PASS
exist in staging.

Exposure ledger proposal: record separate monotonic events for outcome bytes
released, component evaluation started/completed, combination computation
reserved/started/completed, derived-result exposure to a human/consumer, and
publication committed/withheld. Bind policy ID, request/freeze/outcome IDs,
component-result/resource hashes and permit receipt. Record no result values in
the ledger. Under EVAL_AUTH no combination-start event is permissible before its
permit; under PUBLICATION_ONLY, withheld computation still consumes authorization
and exposure history. Lack of a completion event after a start never proves no
computation occurred. Unknown computation/exposure fails closed.

Terminal states are immutable per attempt:

| State | Exact meaning and permitted consequence |
|---|---|
| UNAUTHORIZED | Missing, malformed, expired, revoked, replayed or wrong-scope grant; no newly protected read or computation dispatch, including a denied EVAL_AUTH combination |
| SOURCE_PROVENANCE_INVALID | Missing/conflicting chronology, identity or byte proof; no predictions accepted |
| PREDICTION_FREEZE_FAILED | Incomplete/mismatched/contested prediction package; outcomes remain blocked |
| OUTCOME_ACCESS_BLOCKED | Valid prediction may exist, but outcome prerequisite/profile is missing; no outcome open |
| JOIN_INVALID | Key, fixture, manifest, provenance or count reconciliation failed; no scoring |
| RESOURCE_LIMIT_EXCEEDED | Measured bound breached or enforcement failed; terminate, no completed eligible result |
| EVALUATION_FAILED | Contract/numerical/controller failure prevents valid complete C2 evidence; no forged statistical rejection |
| PUBLICATION_CONFLICT | Different immutable bytes or upstream bindings under a reserved identity; preserve both as quarantined evidence |
| DO_NOT_CONFIRM | Valid completed development evaluation selects none; preserve all failures, never reopen confirmation |
| CONFIRMATION_NOT_AUTHORIZED | A confirmation request lacks an explicit valid grant/custody proof; no source resolution |
| AWAITING_AUTHORIZED_CONFIRMATION | Valid development selection only; no assigned/read confirmation implied |
| DO_NOT_PROMOTE | Valid confirmation failure/insufficiency; no alternate candidate/season |
| INTERRUPTED_REVIEW_REQUIRED | Interrupted or ambiguous stage/access history; no automatic resume or fresh nonce |

Statistical failure can be a successfully published research result. Infrastructure
failure is a separate failure receipt and cannot masquerade as DO_NOT_CONFIRM.
Confirmation success remains research evidence requiring independent reproduction
and review; it cannot change xFP or declare production eligibility.

Restart rules: verify the entire durable chain and reconcile ledger reservations
before opening anything. A completed exact freeze is adopted by identity only;
never choose the newest prediction. An interrupted prediction attempt can be
retried only if ledger/custodian prove no outcomes were released, code/source
identities match, and fresh restart authority covers the attempt. If exposure is
unknown, stop. After outcome release, prediction rebuilding is prohibited in the
study path. A later separately authorized blind reproduction is comparison only,
never a substitute for lost or changed frozen predictions. Confirmation opens
once; crash recovery must be explicitly resolved by the custodian and owner,
not treated as a second untouched trial.

Combination-specific recovery must preserve the selected policy. Under EVAL_AUTH,
a reserved/spent permit or ambiguous start cannot be replayed automatically;
reconcile computation events and exact incomplete bytes under fresh recovery
scope before any separately permitted continuation. If stopped before a permit,
combination remains NOT_EVALUATED with no completed development selection.
Under PUBLICATION_ONLY, a withheld/discarded combination is already computed;
interruption, deletion or restore must not reset it to unseen or NOT_EVALUATED.
Neither path may regenerate predictions, change the winner rule, obtain a fresh
scientific trial by changing nonce, or release a withheld result merely because
it was restored. B02/B04/B07 must settle retained-state/continuation semantics.

## 8. Identity bridges and complete adapter requirements (E)

The proposed canonical identities are:

- Season uses exact registered ASCII `YYYY-YY`; target key is `(season, GW)`.
- Player key is `(season, official element_id)`; never join element IDs across
  seasons. Verified player code is used across seasons only for dependence.
- Fixture key is `(season, official fixture_id)`; team key is season-qualified.
  Archive IDs must have exact evidence-backed one-to-one mappings to these keys.
- Prediction row is `(season, GW, element_id)` with ascending unique fixture IDs.
  History row is `(season, element_id, fixture_id)` plus source assigned GW and
  immutable revision. The same fixture cannot become two history observations.
- Snapshot bridge `(season, GW, element_id) -> code, position, team` binds its
  evidence, not a player's name. Renames are descriptive. Transfers and position
  changes follow source and target snapshots; history is not reset or reassigned.

B06 proposal for review: map a positive official integer code to its minimal
ASCII decimal representation, no whitespace/sign/leading zeros; retain integer
and mapping evidence in provenance. C2 would still sort these strings bytewise,
so `"10"` precedes `"2"`. This is not yet an accepted mapping rule. Reject aliases,
missing code, many-to-one and inconsistent within-season mappings, including
blank-only players. Do not infer identity from names, clubs, similarity or row
order. New entrants need a verified previous snapshot, not inferred absence.

Adapter parity must cover every C2 field and candidate, not just mean xFP:

| C2 field group | Required C1/source mapping and parity |
|---|---|
| Candidate/formula order | U0/UM1/UA1/UM1UA1 to exact frozen formula identities; no aliases |
| U0 means | Use evaluation-complete minute/appearance/goal/assist fields, preserve the distinct production nullable/numeric aggregate semantics |
| UM1/UA1/combined means | Map ModeledComponents and corresponding minute PMFs; preserve numeric incomplete component-only totals and null combined totals |
| Distribution parameters | U0 Poisson means; UA1 weights/shapes/rates with fixed exposure; combination same rate parameters with exact convolved minute PMF; UM1 count distributions absent |
| Probability/completeness | Full support 0..90f; probability of appearance/start and per-fixture appearance-point sums; U0/UA1 start probability undefined |
| Availability | Explicit lowercase code normalization, target-bound chance, known-before-deadline flag; no new multiplier or heuristic |
| Population/strata | Snapshot position, prior unweighted minutes, previous-GW reason, entrant, promoted and fixture set from verified views |
| Blank | Empty fixture tuple, deterministic zero moments, PMF (1), correct zero-exposure analytic distributions and candidate-specific start semantics |
| Missingness | No zero repair, no dropped row, no partial fixture sum treated as complete; field-specific flags retained in companion provenance |

C1 can raise for missing UM1 inputs. The adapter must map only enumerated,
protocol-permitted missingness into incomplete forecasts; it must not catch all
exceptions as benign nulls. A corrupt source and legitimate unavailable component
are different cases. Any unrepresentable nullable/blank case or changed numerical
semantics stops B03/B06 review instead of coercing C2 validation. C1 tolerances
(1e-12 normalization, 1e-10 moments) are validation tolerances, not new rounding
rules. Same-environment deterministic bytes cannot be "repaired" with rounding.

## 9. Immutable manifests, serialization and identity (F)

All names below are **proposed v1 contract identifiers**. Existing C1/C2 schemas
stay unchanged. New schemas must be closed at every object, strictly typed,
reject duplicate keys/nonfinite values/unknown versions and validate cross-field
semantics. Missing material fields deny execution; no placeholders pass validation.

Proposed common envelope fields are exactly: `schema`, `semantic_id`,
`identity_inputs`, `files`, `upstream`, `recorded_at_utc`, `time_evidence`,
`ledger_sequence`, `previous_receipt_sha256`, `status`, `assurance`, `metadata`.
Each schema must close its own `identity_inputs`, `assurance` and `metadata`
objects. Requests/receipts bind the owner-selected authority profile and
combination policy ID (EVAL_AUTH or PUBLICATION_ONLY); assurance fields distinguish
single-operator control from independent custody. Access, computation and human
exposure events have separate types, never an ambiguous single "opened" flag.
No free-form dict, exception text or arbitrary path strings. `files`
entries contain safe relative logical name, media/schema identifier, integer byte
size, lowercase SHA-256, role and optional declared/verified row counts labelled
separately. `upstream` entries contain role/schema/semantic ID/content SHA-256.
Inventories exclude the containing manifest to avoid self-hashing; an external
receipt hashes the whole manifest. Empty inventory is explicit for pure receipts.

Canonical rule proposal for new JSON follows C1/C2: UTF-8, sorted object keys,
compact comma/colon separators, ensure_ascii=false, finite native JSON types,
exactly one final LF, no implicit Unicode/float normalization. Arrays use
schema-defined order; identities/keys are unique. Hash raw file bytes including
LF. Semantic IDs are SHA-256 of the canonical `identity_inputs` object containing
its schema-domain string. Timestamps, host paths, receipt nonce and run duration
are not inputs to a deterministic mathematical identity. They remain hashed in
whole-receipt content. Never expand an existing C2 digest's scope.

Proposed identity layers, blocked on B04/B07 approval:

1. Request ID binds protocol/clarification file hashes, exact candidate definitions,
   data-contract/source registrations, stage/seasons/GWs, code commit plus relevant
   source hashes, lock/runtime/native-library profile, adapters, resource policy
   and output-contract versions, chosen authority/clock assurance profile and
   combination policy ID. It excludes later outcome bytes and wall time.
2. Prediction freeze ID binds request ID, resolved prediction source/view/bridge/
   promoted-map hashes, canonical prediction-row digest, C1 detail inventory and
   C2 adapter bytes. It includes dormant combination, flags and missingness.
3. Outcome ID binds source-contract/registration, exact finalized versions,
   canonical outcome bytes and protocol scope. A join separately binds freeze ID.
4. Evaluation identity binds prediction freeze, outcome package, join, protocol,
   code/environment, exact evaluated package set and resource-policy identity.
   Completed-result identity must also bind actual resource/access/publication
   evidence without making circular hashes; the exact decomposition is B04/B07.
5. Attempt/receipt identities are separate from deterministic prediction/statistic
   content. Different timestamps do not justify silently stripping fields from
   C2 v1 results. Current C2 embeds resource hashes; reproducibility across newly
   measured attempts therefore needs a reviewed versioned core/envelope split.

Contract inventory (all share the rules above):

| Schema name | Semantic inputs and upstream bindings | Required payload/inventory; evidence versus description |
|---|---|---|
| task033c3-execution-request-v1 | Request ID inputs above, approved design version and exposure scope | Exact source allowlist, requested stage, pre-outcome exclusions, resource profile, authority receipt references; requested scope is intent, not permission |
| task033c3-authorization-receipt-v1 | Grant content hash, request ID, stage, nonce reservation, issuer/subject/audience, revocation epoch | Authenticated approval binding and pre-read ledger event, expiry/time proof; empty file inventory; receipt attests checked grant, not source validity |
| task033c3-resolved-source-v1 | Request/grant, source registration, parser and view policy | Source byte inventory, cutoff proofs, snapshot/deadline/fixture/row lineage, bridge and promoted map; source-declared timestamps distinguished from attested availability |
| task033c3-prediction-package-v1 | Freeze ID, request, resolved-source, adapter, grants | Complete C1 detail and four-slot C2 prediction bytes, universe/strata and source/view hashes, source classification; publication receipt needed to claim frozen |
| task033c3-outcome-package-v1 | Outcome ID, separate grant/registration and exact source versions | Outcome bytes, fixture finalization, nullable-value/missing-object ledger, access receipt; no prediction regeneration |
| task033c3-join-v1 | Freeze ID, outcome ID, protocol/bridge/join-policy versions | Canonical joined record inventory, all key/fixture counts, anti-join sets and missingness reason digests; verified counts, not merely input-declared counts |
| task033c3-resource-receipt-v1 | Attempt, request, phase/candidate/scope, enforcement profile, measured interval and ledger | Parent/OS measurements and child diagnostics, units, limits, enforcement tests, missing measurements, termination cause and I/O/publication coverage; configuration is descriptive, not proof |
| task033c3-c2-result-package-v1 | Evaluation identity, exact C2 schema, freeze/outcome/join, grants/resources | All result bytes, metrics/failures, population/common/subgroup/ranking digests, conditional permission evidence and selected/none; C2 PASS only statistical/resource evidence under reviewed contract |
| task033c3-publication-receipt-v1 | Namespace/slot, package identity and manifest digest, publication grant, attempt | Commit record, verified file set, size/hash totals, durability capability, final resource binding and re-verification; empty or fixed audit inventory; local publication only |
| task033c3-failure-receipt-v1 | Attempt/request, stage, fixed terminal/reason codes and completed prior receipt hashes | Empty source-row inventory, bounded sanitized diagnostics, cleanup status, incomplete gate states; cannot carry success status or candidate eligibility |

UTC times are recorded by the trusted controller/broker with the explicitly
selected B02 clock-assurance profile, anchor/source identity and health evidence,
and monotonic sequence/duration. An operator-attested local anchor must be labelled
as trusting the operator/host, not an independently authenticated historical time
source. B02 must approve and bound the concrete clock profile; section 17.3 proposes
parameters without treating them as accepted. Wall clock alone cannot prove
causal archive availability or access order.
Monotonic elapsed time is for budgets, not a portable historical timestamp.
After clock/ledger discontinuity, require reconciliation; never backdate receipts.

Validation order is invariant: bounded metadata shape/version -> authenticated
scope/revocation/time -> nonce/phase reservation -> resource preflight -> receipt
persistence -> exact protected object resolution -> stable hash/size check -> parse
that same verified snapshot -> semantic/source/cutoff validation -> adapter/join
checks -> pure computation -> final resource/provenance verification -> exclusive
publication. Output parsing is also bounded. All immutable objects use absent-only
creation; existing identity is reverified, never overwritten.

## 10. Guarded joins, exclusions and statistical preservation (G)

Before scoring, join one prediction row to at most one outcome row on
`(season, GW, element_id)`. Within that row, outcome fixture IDs must equal the
prediction's sorted unique fixture set exactly. Verify underlying fixture rows
on `(season, fixture_id, element_id)` and registered fixture-to-GW mapping before
aggregation. No join on names, code alone, unqualified fixture IDs or team names.
Assert cardinalities before and after every join; no many-to-many expansion,
implicit duplicate removal or counting an appearance twice.

The full target universe is frozen before outcomes. Prediction package row count
must equal the sum of eligible snapshot players over registered targets (including
blanks); one missing PredictionRecord fails the package. An incomplete candidate
Forecast stays present. Known synthetic/contract position exclusions (assistant
manager/unknown positions) and structural membership rules are fixed and counted
before outcomes, not invented after scoring. Missing snapshots or unexplained
fixture/player exclusions stop the build.

Every outcome key is either matched to one frozen prediction or rejected as
unexpected. Missing outcome objects are explicitly recorded as unavailable,
never deleted prediction rows. Every expected fixture is either an authorized
finalized record with preserved field nulls or an explicit unavailable record in
the package ledger. Entire missing rows may be omitted from C2 JoinedOutcomes only
with that ledger and exact count reconciliation; partial fixture sets may not be
passed as complete. Legitimate absent values follow C2 field completeness rules.
Malformed files, unknown fixture sets, corrupted bytes and unfinalized sources
are infrastructure failures, not statistical missingness.

A partial/unfinalized GW cannot authorize scoring on its convenient finished
subset. Wait for a separately authorized complete finalization package; no
scheduled polling is implied. Finalized records with legitimate missing values
may yield insufficient statistical evidence under existing gates. No thresholds
are relaxed to accommodate inaccessible data. B01 must freeze the finalization
proof and unavailable-evidence policy before access.

Blanks require proof of an empty registered fixture set and remain zero-point
structural outcomes, reported separately from unavailable evidence. C2 can
represent an empty fixture OutcomeRecord; C3 must not infer a blank from an absent
outcome row. DGWs retain every fixture with per-fixture minute cap and appearance
points. A postponed lower-GW history fixture after cutoff is excluded. Future
rescheduling/corrections cannot modify frozen fixture membership; disagreement
requires a new reviewed source/protocol decision, never substitution into the
existing join. Finalized assignment is still not predeadline scheduling proof.

Reconcile per season/GW and candidate: universe, blanks/nonblanks, complete and
incomplete predictions, outcome-present/absent, nullable minutes/starts/events,
common pairs, excluded/unknown keys, fixture totals, duplicates, overflow and
subgroup counts. Counts must partition their declared sets; overlapping diagnostic
strata must not be summed as disjoint populations.

C2 owns ranking eligibility. After authorized outcomes are frozen, it determines
pair-specific common membership and nonconstant actual/prediction checks before
resampling. It cannot freeze this outcome-dependent eligibility before outcomes
exist. The **rule** and universe are frozen before outcomes; the resulting
membership is frozen before resampling and never reselected in a draw.
C2 may compute per-row scores while constructing that original membership; C3
must not claim its current implementation freezes membership before every score
calculation. The prohibition is outcome-selected rules or adaptive exclusions.
Use each pair's >=50 complete common players and >=30 eligible GWs per season;
combination membership does not exist until conditional evaluation is permitted.
Exclusion reasons are objective frozen reasons, never improvement-dependent.

Preserve exact C2 membership hashes: ranking SHA-256 covers canonical included
`[{"gameweek":g,"season":s},...]` sorted by season then numeric GW, including LF.
It excludes candidate/control and excluded reasons; those are separately bound
in results. Common-population digest covers ordered `[season,GW,element_id]`
arrays as implemented. Source, bridge, cutoff, grants, bytes and fixture identities
are separate provenance hashes, not additions to these existing membership bytes.

Frozen statistical invariants: 9,999 PCG64 draws for each of crossed and serial;
development seeds 330331/330431 and confirmation 330332/330432. Serial uses the
shared UTF-8 code union including blank-only players, then independent season
blocks in lexicographic season order, ten starts from 2..35, length four,
truncate to 37. Crossed draws use the nonblank per-season universe and player
then GW order. Equal season weighting; no nonlinear pooled substitute. No RNG
batching change without byte-identical stream proof.

For every complete applicable endpoint family preserve
`R_b=max_j((Dhat_j-Dstar_bj)/s_j)`,
`q=max(0,9917th sorted R_b)`, `U_j=Dhat_j+q*s_j` and strict comparisons.
Reserve alpha 1/60 per package, 1/120 per family; no alpha recycling. Undefined
required endpoints invalidate the family; no dropped coordinates/draws or
correlation zero repair. Both resamplers must pass. Coverage remains >=95% natural
nonblank for both predictions, >=90% common and >=99% actual, per season;
global minima 1000/100/30 and subgroup 200/20/20. All positions are required;
only truly absent nonblank availability groups are structurally NOT_APPLICABLE.
See the complete freeze/clarification and inference code for all literal scales,
proper scores and diagnostics; C3 must not introduce a second implementation.

## 11. Resource enforcement and feasibility (H, L)

Preserved ceilings: <=700 players/GW; U0 plus at most three variants; one evaluator
worker; <=4 computational threads; <=2 GiB peak RSS; <=6 hours per season/candidate
prediction and scoring phase; <=24 hours per complete development/confirmation
statistical evaluation, both resamplers and all evaluated packages. I/O and
publication are included. The clarification permits a trusted launcher outside
the evaluator-worker count, but does not license unbounded parent/broker work.
B05 must define their independent limits and aggregate accounting explicitly.

Required controls are preventive where supported, not merely sampled reporting:

| Resource | OS / parent / child responsibility | Evidence and denial rule |
|---|---|---|
| Worker/process count | OS denies unapproved fork/exec/descendants; parent serially starts one computational worker and accounts broker/controller roles | Process/capability profile, observed tree and exit evidence; unexpected child kills attempt |
| Computational threads | Configure before numeric imports; restrict available CPU/thread backend and verify native pool behavior under the reviewed OS profile | Config plus enforcement proof and observations; environment variables alone never PASS |
| Wall clock | Parent monotonic watchdog covers protected I/O, preparation, computation, verification and commit; worker cannot reset it | Start/end and phase totals, cumulative retry charges, timeout/kill evidence; reserve completion overhead before release |
| RSS | OS-enforced supported memory containment plus normalized high-water evidence; parent includes approved process scope | Raw units, normalized bytes, OS/backend limits, child peak and parent observations; polling alone cannot prove no peak breach |
| Scratch disk | Dedicated bounded storage/quota and file/inode budget; broker counts writes/decompression and parent measures use | Allocated/logical-byte scope, peak, limit, cleanup; no reliance on periodic directory polling alone |
| Canonical outputs | Counting bounded writer prevents exceeding per-file and total reviewed caps before publication | Actual bytes/hash/inventory and rejected overflow; compression does not waive canonical-byte cap |
| Interruption | Parent revokes access, terminates whole contained worker group, reaps children, marks terminal state | Exit/signal codes, remaining-process check, cleanup status; unknown state blocks restart |

B05 must not equate virtual address-space limit to RSS, total OS thread count to
numerical thread count, or cgroup memory accounting to RSS without an explicitly
reviewed accounting relation. A stricter total-thread cap may be a reviewed
conservative profile, not an inferred protocol amendment. Linux containment
mechanisms and macOS development controls have different capabilities; ordinary
macOS subprocess polling alone is not continuous enforcement. A native macOS
real run stays unsupported unless a sufficient profile is proved. The owner has
selected a contained Linux/Python 3.10 profile as the first enforcement target,
but its exact backend and limits still require review and proof. It is not a
silent substitute for a different platform claim. No infrastructure is installed
by this design.

Before **any outcome object is opened**, verify supported enforcement backend,
resource grant/profile, source/package size metadata, scratch capacity, output
caps, healthy watchdog, code/environment, complete prediction freeze and required
synthetic scale certification. If a measurement/control is unavailable, deny the
open. Recheck through execution. Child self-reports complement parent and OS
evidence; they cannot establish their own authorization or PASS. Failure-receipt
space must be bounded and reserved outside untrusted scratch; inability to write
a receipt still must not yield success.

B04 is a concrete circularity blocker: do not provide a fake C2 PASS on the
promise that publication will finish within budget; do not rerun C2 with a new
resource token to manufacture a completed selection. The owner-selected EVAL_AUTH
redesign in section 6.3 would keep deterministic statistical evidence separate
from resource-gated
selection, permit conditional dispatch only under completed component gates,
and expose results only after a measured terminal commit. PUBLICATION_ONLY also
needs exact resource completion semantics; suppressing exposure does not resolve
the timing cycle or remove the cost of already computed combination work.
The owner approved this staged direction. It still requires a new pure
interface/contract, an exact definition of publication completion and an acyclic
receipt graph before B04 closes. Tests must include a breach during last-byte
write/fsync/commit, not just during numerical work.

Scale claims and gates are distinct:

| Scope | Existing evidence | Required future gate |
|---|---|---|
| 100-player synthetic C2 fixture | Tracked C2 report describes full-repeat rejection: UM1 FAIL, UA1 FAIL, UM1UA1 NOT_EVALUATED, DO_NOT_CONFIRM; no new drill or raw review logs inspected here | C3 full chain/privacy/restart tests at this size; synthetic supplied resource tokens are not measured C3 receipts |
| 700-player ceiling | C1 report records a single-target 700-player kernel/serialization drill; it is not 700-player C2/full-chain evidence | C3 end-to-end generated two-season 37-target scale, realistic full PMF/support and I/O accounting, within frozen limits on accepted profiles |
| Full UM1+UA1 conditional evaluation | C2 tests exercise dispatch using fixed-statistic seams; reported 100-player drill never evaluated combination | Separate generated benchmark must exercise real 9,999 draws for both streams and all three packages; full PMFs and passing component population/gates needed; no mock-PASS feasibility claim |
| Full historical development | Unmeasured and unauthorized; actual data completeness not inspected | Later exact authorized source registration and separately approved real execution; C3 synthetic evidence cannot prove historical completion/performance |
| 3,000 candidates / 12 views / 60 seconds / 2 GiB | Decision corpus explicitly deferred and NOT_EVALUATED | Later separately designed Task033D/decision-corpus work; cannot gate a STABLE or promotion claim from component C3 success |

The combination scale gate may itself remain blocked if no outcome-independent
generated benchmark passes component gates. Test-only forced dispatch can measure
an isolated combination kernel cost, but cannot certify the complete conditional
path. No reduced replicates, dropped subgroups, shortened season or extrapolated
linear timing is a substitute. Record exact dimensions, versions, observed
resources and claims not exercised. Numeric scratch/output caps must be frozen
through outcome-free engineering review before implementing real-source access.
Section 17.6 proposes concrete caps; none is approved or measured.

CI remains Linux x86-64/Python 3.10 with exact uv 0.12.7 and the committed lock.
The available macOS arm64 evidence used a different Python/NumPy resolution.
Same pinned environment requires byte-identical semantic predictions/statistics;
platform drift must be reported and reviewed, never rounded away after a gate
flip. Existing exact-commit CI success certifies no C3 backend. New backend tests
must exercise actual enforcement, with no skipped gate presented as PASS.

## 12. Exclusive publication, concurrency and determinism (I)

Proposed publication is private local research storage, never a Git or production
namespace. A publication grant names package kind, experiment/request, semantic
slot and allowed assurance. Use one reserved slot per experiment/stage so changing
code/source hashes cannot secretly obtain a second untouched study. Attempts
have separate immutable identities; a new attempt never erases exposure history.

Required algorithm, pending B04/B07 platform/identity resolution:

1. Atomically reserve request/stage and grant nonce in the trusted ledger. Lock
   ownership uses attempt identity, not only a reusable process number. Losing
   contenders do no protected read. Never steal a lock solely because it is old.
2. Create an absent owner-private workspace under a verified staging root. Writer
   accepts only declared safe relative file names. No absolute paths, separators
   in single-file names, dot components, symlinks, hardlinks, devices or sockets.
3. Capture authorized source files descriptor-relatively; open each directory
   component without symlink following and each single-link regular file once.
   Compare device/inode/mode/owner/link count/size/mtime/ctime before/after,
   count bytes, verify expected hash, and parse the same captured bytes or sealed
   private snapshot. Recheck root/name identity; never hash then reopen by name.
4. Finish each file, hash, validate and flush it; enumerate only the owned output
   staging directory to prove the declared exact set. Reject unexpected files.
   Input assurance remains **declared-file only**; do not enumerate protected
   source directories or claim they have no other files.
5. Verify the manifest, upstream chain, exclusive namespace and final measured
   conditions. Commit with a proven no-replace primitive on a supported same
   filesystem; ordinary overwrite-capable rename plus exists-check is unsafe.
   Publish the terminal completion evidence only at the reviewed commit point.
6. Readers require that exact complete receipt, not directory existence. Reopen
   and verify all declared bytes, inventory, upstream bindings and terminal state.
   A partial write, orphan staging directory or failure receipt is not a result.

The [snapshot helper](../../src/fpl_decision_engine/artifact_snapshot.py) is a
reusable TOCTOU-safe I/O pattern for read-once stable capture under its stated
local assumptions, **not an exclusive publication protocol**. It supplies no
semantic-slot reservation, cross-process publisher election/locking, competing-run
nonce coordination, atomic multi-file completed-result commit, identical-result
adoption, publication conflict handling or crash-recovery durability protocol.
It is not a C3 authorization service, immutable filesystem or protection against
a privileged attacker. Its reuse requires synthetic regression coverage and must
not change operational reader semantics. C3.5 must independently implement and
prove the missing reservation/concurrency/commit guarantees; citing this helper
cannot satisfy that exit gate.

Identical completed-result adoption requires exact semantic identity **and**
manifest/file byte equality plus matching complete provenance; it performs no
new scoring and records a separate authorized adoption receipt. If a slot has
different bytes or upstream bindings, fail PUBLICATION_CONFLICT, preserving the
original. Similar metrics, equal row hashes with different code, matching filenames
or matching timestamps are insufficient. A separately authorized reproducibility
attempt may match the deterministic core while having different resource receipts;
that is not automatically byte-identical result adoption. B07 resolves this split
before implementation.

Atomic namespace visibility is not durability. File/directory flush behavior,
no-replace availability, filesystem/mount identity and power-loss limits must be
recorded per supported profile. Cross-device moves, network filesystems and
unproven durability modes deny publication. A completed marker cannot precede
all required immutable payloads and final resource evidence; self-referential
hashes and unmeasured completion I/O are forbidden (B04/B07).

On restart, reverify complete publications rather than trusting locks or indexes.
Orphaned work is quarantined as incomplete; only verified owner-scoped scratch is
cleaned. No delete-and-recreate of a contested destination. Restore does not
resurrect expired authority, reset exposure, or repair a corrupt result under its
old identity. Local immutable publication is neither offsite backup nor model
eligibility.

## 13. Privacy, failures and recovery (J, N)

Source rows, raw prediction/outcome rows, manager information, credential material,
private identities, local usernames and absolute local paths are prohibited in
review packages. Even public-player research rows are excluded from exports.
Private packages and sanitized aggregate reports are different artifacts with
separate inventories/hashes and explicit export approval. Public logical source
IDs must themselves be checked against an allowlist; do not echo attacker-chosen
IDs. No raw environment dumps or command transcripts.

Use closed fixed error/reason codes. Catch exceptions at the boundary without
forwarding parser fragments, filenames, repr, SQL, stack locals or child stderr.
Child stdout/stderr are bounded and suppressed from public logs; unknown failures
map to a fixed EVALUATION_FAILED reason. Seed synthetic sentinel values to verify
no leakage. A sanitized failure receipt may retain stage, attempt opaque ID,
completed safe artifact hashes, counts, limit/observed aggregates, exit category
and cleanup status. Do not retain raw protected buffers or dumps for diagnosis.
Disable core dumps and control scratch/spill; memory zeroization and forensic
secure deletion on modern storage are not claimed. Unverified cleanup quarantines
and blocks reuse; do not recursively delete paths supplied by an input manifest.

Review malformed/unauthorized file failures without opening other files to
"repair" them. Failed decompression/hash/schema/provenance never falls back to a
different archive. Chunk/input/decompression limits apply before parsing.
Sanitization uses allowlisted fields and sensitive-pattern scanning; a clean scan
alone does not prove arbitrary secrets absent.

Future outputs must reside under a dedicated reviewed ignored research root,
separate from operational subtrees, with staging/privacy guards extended and
tested as necessary. Existing root ignore rules are not confidentiality or runtime
access control. Do not place review bundles in Git, weaken staging guards or scan
private denylists without authorization. Stage named reviewed files only in a
separately authorized commit task. This design and review package contain no
ignored-data contents; tracked code/doc references to data paths are context only.

**NO VERIFIED OFFSITE BACKUP.** A future encrypted checkpoint may capture opaque
C3 packages, immutable manifests, source versions within its separate read scope,
code/lock identities and exposure/replay ledgers. Backup access is not research
access; checkpoint creation cannot execute parsers/evaluators, issue grants or
open confirmation. Existing [checkpoint tooling](TASK027D_ENCRYPTED_CHECKPOINTS.md)
needs a separately reviewed C3 inventory/capture boundary, quiescence and encrypted
private mapping/credential custody plan. No upload or checkpoint is authorized here.

If source access/credentials are lost, retained authenticated bytes may support
only a newly authorized reproduction after integrity checks. Missing source
versions, predeadline proofs, prediction freezes, approval/custody history or
keys cannot be reconstructed from result hashes or current web data. Loss of the
decryption key can make checkpoint contents unrecoverable. Loss/rollback of the
external revocation/exposure ledger blocks all continuation even if local files
verify. Backup retention cannot prove a historical result was untouched at creation.

After restore: decrypt under separate recovery authority; verify checkpoint
inventory; safely restore without symlink/path escapes or overwrite; verify every
manifest/file/upstream hash, exact schemas, bridge/cutoff/finalization proofs,
code/environment identity, exhaustive declared output sets and terminal markers;
reconcile trusted off-checkpoint ledger/revocation high-water state; re-establish
current authorization and backend profile. Reverification of protected contents
itself needs read authority. No automatic evaluation, prediction regeneration,
confirmation opening or promotion occurs. RPO/RTO and offsite recoverability
remain unmeasured until a separately authorized restore drill.

## 14. Testing and adversarial acceptance plan (K)

These are future implementation requirements. **No tests were executed for this
design task.** All C3 implementation tests use generated, clearly synthetic
sources in temporary roots, never existing data or operational fixtures as
real evidence. Existing C1/C2 tests and independent oracle remain unchanged
unless a separately approved contract amendment requires explicit additions.

| Area | Meaningful adversarial case and expected oracle |
|---|---|
| No read before grant | Instrument OS/broker opens, stat, directory enumeration, metadata/schema/footer reads and decompression; invalid grant must produce zero protected operations. Independent event recorder verifies receipt-before-open order. |
| Prediction isolation | Worker lacks outcome mounts/FDs/map; target/future sentinel values and attempts to import outcome resolver cannot be observed. Try leaked parent descriptors and subprocess escape. OS denial is required, not just mock assertions. |
| Chronology | Same-GW early kickoff, lower-GW late kickoff, still-playing fixture, late snapshot, later correction, missing knowledge-time proof and mixed-season rows all reject under the approved source contract. Hand-written availability timelines are the oracle. |
| Paths/TOCTOU | Traversal, absolute/encoded separators, symlink at every ancestor/file, hardlink, FIFO/device, root rename, file swap after hash, mutation during read and before parse. Independent attacker process and original expected byte digest prove rejection or stable exact capture. |
| Authority | Forged issuer, wrong audience/stage/candidate/source/code/environment, expired grant, rollback clock, revocation race, duplicate nonce and restored spent grant. Independent fake authority and ledger order, then real backend tests; verify single-operator workers cannot reach signing keys/authority state, and no receipt falsely asserts independent custody. |
| Combination permission | EVAL_AUTH denies before any combination membership/score/resample with absent/revoked/wrong-binding permit even after both components pass; test crash after reservation. PUBLICATION_ONLY, if chosen, requires prior compute authority and records computed-withheld exposure on publish denial. Independent event oracle; never fake a resource FAIL to stop computation. |
| Source versions | Legacy backtester retains historical-v2 rejection unchanged; proposed C3 adapter rejects v2/v3/mislabelled v3.1 and releases only reviewed v3.1 causal views. Literal schema/field/null/timeline oracles; no backtester import or mixed actual table in prediction worker. |
| Bridges | Element collision across seasons, one code mapped to two players, Unicode/decimal aliases, rename and transfer, missing bridge on blank-only code. Literal bridge/serialized byte vectors independent of adapter. |
| Adapter | Every field in section 8, all four candidates, every position, missing rates/minutes/starts, hard/soft/unknown availability, blank/DGW/overflow/prior-only/partial event history. Independent algebra/recurrence and existing production U0 on synthetic inputs; never production regeneration. |
| Joins | Duplicate prediction/outcome/fixture, many-to-many, absent prediction row versus incomplete forecast, orphan outcome, partial finalized GW, unavailable evidence and explicit blank. Hand-enumerated key multisets/counts; independent set arithmetic. |
| Calendar edges | Blank, double+, postponed lower GW, rescheduled fixture, team change and new entrant without previous snapshot. Literal expected fixture sets and per-fixture appearance calculation. |
| Membership/statistics | Different candidate completeness changes only its pair; constant actual/prediction excludes by frozen reasons; union retains blank-only codes. Use literal byte hashes, naive copied-row rank/decile/count oracles and all 9,999 reference RNG draws. |
| Interruption/restart | Crash at every durable transition including grant reservation, first outcome read, last output write and completion commit. No automatic retry after exposure, no adopted partial output, no stale lock stealing. Independent operation/event ledger. |
| Concurrent publication | Two publishers with identical and conflicting bytes; exactly one exclusive commit, identical adoption only after full verification, loser does not read protected sources. Barrier-controlled processes, reader observes absent or complete only. |
| Resource breach | Spawn/thread burst, brief RSS spike between samples, endless CPU, blocked I/O, decompression bomb, many small files, last-byte canonical overflow, disk-full and publication timeout. OS counters/enforcement and parent timer are oracles, never child PASS. |
| Determinism | Fresh same-environment authorized synthetic reruns with fixed inputs yield identical prediction/statistical core bytes; altered source/code/lock creates changed request identity. Changed bytes under same identity conflict; new resource receipts are not hidden. |
| Sanitized failures | Seed fake usernames, private paths, tokens, source rows in malformed files, IDs, stderr and exceptions; independent scan of all export bytes plus allowlist schema. No original input echo. |
| Architecture | Production/app/presentation cannot import research; C1/C2 remain pure; evaluator cannot call C1; predictor cannot reach outcomes; no operational CLI wiring or manager root mount. Static graph plus runtime denied-access tests. |
| Clean checkout/CI | Fresh tracked-only checkout, no private files/network, Python 3.10 locked Linux environment and separately documented macOS profile; schemas match validators. No skips/xfails concealing an unsupported control. |
| End-to-end privacy/provenance | Synthetic source registration -> grants -> causal views -> C1 adapter -> freeze -> separate outcome join -> real C2 -> measured exclusive publication -> restore/reverify. Verify every hash/receipt edge independently and export only sanitized aggregates. |

Tests that merely call the same serializer, resolver, adapter or join helper to
construct expected results are insufficient. Membership bytes, identity mappings,
chronology acceptance, nonce consumption, filesystem races, accounting totals and
publication visibility need independent oracles. Numerical formula tests use
manual closed forms/explicit player copies/probability recurrence; full C2 PASS
orchestration tests with private fixed-statistic seams are useful mechanics tests
but not resource or predictive evidence. Adversarial tests must also assert what
was **not read**, not merely that an error was returned.

## 15. Acceptance criteria, claims and validation of this submission

Implementation is acceptable only after all B01–B07 decisions are resolved in
reviewed documents and separately authorized slices demonstrate:

1. Every protected read, including metadata/schema/Parquet footer reads, has
   prior selected-profile authenticated, scoped, unexpired authority and durable
   nonreplayable ordering. Report single-operator and independent-custody assurance
   separately; confirmation and publication remain distinct. The owner-selected
   combination policy is enforced at its declared computation or release boundary,
   with no false NOT_EVALUATED state or unrecorded withheld computation.
2. Every released prediction input has the approved causal provenance; known
   restricted historical limitations remain visible and cannot be upgraded by hash.
3. Four forecast slots, every registered row and all missingness are frozen before
   outcomes; no substitution/regeneration after exposure, including on restart.
4. Exact bridges, complete adapter parity, cardinality guards and C2 statistical
   semantics pass independent tests without changes to production behavior.
5. Resource enforcement includes I/O/publication, realistic full 700-player and
   conditional-combination synthetic gates, with no provisional fabricated PASS.
6. Exclusive no-overwrite publication, conflict/adoption, failure separation,
   clean-checkout tests, Python 3.10 and platform-specific enforcement pass.
7. Sanitized review export and authorized restore preserve byte/provenance integrity
   without exposing protected content or reviving spent/revoked authority.
8. No C2 PASS, synthetic success or published result implies production eligibility.
   Development success does not authorize confirmation. Confirmation success
   cannot change xFP. A later model change needs separate promotion review/task,
   explicit owner approval, model/contract version change, regression comparison,
   full trust-chain tests and exact-commit CI. Decision-corpus status remains
   NOT_EVALUATED and component diagnostic eligibility is only a ceiling, not earned.

The original submission was one new design document. This C3.0 continuation
modifies that same tracked file, unstaged and uncommitted. Documentation-only
validation checks whitespace, its local relative links, exactly one modified
design file, unchanged index/HEAD and primary checkout,
preserved Task025 byte hashes, and sanitized review-package inventories/hashes.
No continuity documents are edited. The external review package records actual
validation results and exact Git status; it is not a research publication.

No tests, experiments, generated predictions/outcomes, refreshes, decisions,
journals or monitors are run by this task. No data directory is enumerated or
read. GW5 and other ignored artifacts are untouched by task actions. Since those
bytes are deliberately uninspected, this is an action-scope assurance, not an
independent before/after content audit or a claim about concurrent outside actors.
No private denylist or credentials are read to perform sanitization. No PR update,
stage, commit, push, merge, Task026C or research publication is performed.

## 16. Proposed independently reviewable C3 task breakdown

All slices require separate owner authorization; the order below is not that
authorization. Each implementation slice needs independent adversarial review,
explicit commit permission and exact-commit CI before dependent work.

| Slice | Exact deliverable and dependency | Exit gate / prohibited expansion |
|---|---|---|
| C3.0 — resolve design blockers | Apply the owner-approved directions in section 3.2 to reviewed concrete contracts: A2-single-operator development profile, EVAL_AUTH, v3.1 source adapter compatibility, minimal-decimal bridge encoding, measured stage/final resource boundaries, contained Linux-first enforcement and acyclic publication identity | Independent review must accept the exact contracts and remaining parameters; all implementation/evidence gates remain open, with no data reads or code used to settle unknowns |
| C3.1 — contracts and state machine | Closed schemas, selected authority/combination policy and typed computation/exposure transitions; separately reviewed versioned C2 public component/combination/final-selection contracts under the selected EVAL_AUTH direction, preserving v1 synthetic API | Independent byte/state/replay and no-combination-before-permit oracles; C1/C2 remain pure, no keys/services or real-source configuration |
| C3.2 — broker and causal source boundary | Proposed research/c3/source_adapter.py: descriptor-based v3.1 causal source serving, phase capabilities, immutable views and provenance/missingness checks; synthetic sources only | Independent v3.1 parity, strict version rejection, no metadata/schema/footer read before authority, OS/TOCTOU/chronology proof; no legacy backtester runtime dependency or v2 gate change |
| C3.3 — full adapters and guarded joins | C1-to-versioned-C2 parity, bridges, prediction freeze and outcome packages, count reconciliation | Independent U0/formula/blank/DGW/identity oracles; no revised models or statistics |
| C3.4 — enforced controller | Parent/OS profiles, selected authority model, completed resource receipts, policy-specific conditional dispatch/exposure ledger, interruption and recovery under C3.0 decisions | No EVAL_AUTH combination arithmetic by approved code before permit; PUBLICATION_ONLY cannot hide computed exposure; last-stage resource breach fails; no real study |
| C3.5 — exclusive publication and recovery | No-replace commit, exhaustive owned-output inventory, adoption/conflict, sanitized failures, policy-specific withheld exposure and authorized restore verification | Independently prove semantic-slot reservation, cross-process concurrency, atomic complete publication and restart guarantees absent from artifact_snapshot.py; race/crash/durability limits documented, no ledger rollback, no backup upload or promotion |
| C3.6 — end-to-end synthetic certification | Fresh-checkout provenance/privacy, 100 and 700 players, all-9,999-replicate conditional combination, deterministic repeats, Python 3.10 and macOS evidence | Complete measured report with failed gates and unexercised limits; success authorizes no real read |
| Later R1 — source registration | Separately authorized exact historical source/custody review and immutable registration, promoted map, availability/bridge coverage | No automatic development run; B01 evidence must actually exist |
| Later R2 — development execution | Separate owner grant for exact reviewed code/source/request and stages | Publish bounded research result or failure; no confirmation source discovery |
| Later R3 — confirmation | Independent untouched-season custody and separate prediction/outcome/publication grants, once only | No automatic promotion; failure remains exposed |
| Later D/E — decision validation/promotion | Separately designed deferred corpus, Task033D diagnostics and Task033E versioned model review | Explicit approval, regression/operational validation; no retrospective decision rewriting |

If a slice finds a new material ambiguity, stop it and write the exact conflicting
contract/evidence and required decision. Do not weaken a guard, change a budget,
choose a new season, drop a row/family, retry a failed scientific question or call
missing evidence PASS to keep the sequence moving.

## 17. C3.0 concrete contract proposal (2026-09-28)

**Ready for independent adversarial design review; NOT implementation-ready.**
This addendum is proposed against branch tip
`d55421e52f7c8a921a87967434bbf9ca903b6a39`. Sections 3.2 and 17 distinguish
approved direction from proposed mechanics. Every contract, bound and backend
below is a review proposal, not a deployed control or an executable grant.
B01–B07 remain OPEN. No new protocol rule takes effect merely by appearing here.
If a proposal conflicts with the freeze, code or approved clarification, stop
and obtain a prospective amendment; do not implement the convenient reading.
Sections 1–16 preserve the earlier design and its history; this section supplies
concrete candidates for the previously unspecified details.

Repository setup was inspected first. The former temporary worktree had only
remaining directories at its root, no `.git` link and no design file; Git retained
a prunable registration at the stated branch tip. Neither that directory nor its
registration was removed, repaired or overwritten. A new managed isolated
worktree was created from the existing Task033 branch, detached at the exact
SHA above. This avoids forcing the branch into two checkouts. The primary
checkout remained on `main` at `e10d4c9dc10467730cdb90fcbea494dcdf920b6f`, with its
two unrelated Task025 files preserved. Absolute host paths belong only in the
local delivery message, not in this document or exported bundle.

### 17.1. Evidence, precedence and proposal notation

Current technical evidence was read as source text, never executed. In particular:

| Evidence at the base SHA | Contract consequence |
|---|---|
| `historical.py` schema tuples, `_build_features_and_actuals`, `_previous_context`, manifest construction; `historical_sources.PARSER_SCHEMA_VERSION` | v3.1 contains numeric/null semantics and archive provenance, but no historical completion timestamp or authenticated first-availability proof. Its feature builder filters GW/kickoff, not the proposed field-version knowledge proof. |
| `historical_backtest.EXPECTED_HISTORICAL_VERSION`, `_input_paths`, `_validate_input_manifest` | v2 remains an exact legacy gate; not a v3.1 runtime path or trusted parity oracle. |
| C1 `TargetContext`, `HistoryFixture`, `PeerHistory`, `U0FixtureInput`, `build_u0`, three assemblers | The new broker must supply evidence-backed identities/flags; adapters must preserve nullable evaluation fields and component-specific count distributions. |
| C1 `ArtifactHeader.__post_init__` and `task033c1_prediction_header.schema.json` required constant properties | `PREDICTION_ONLY`, `UNOPENED_UNAUTHORIZED` and false `outcome_joined` are verified facts about the old contract. Do not relabel them as a new grant. |
| C2 `PredictionRecord`, `PredictionBatch`, `JoinedOutcomes`, `ResourceGate`, `EvaluationResult` | Strict synthetic authority; exactly four forecasts; supplied resource hashes are not measured evidence. v1 stays byte/schema compatible. |
| C2 `evaluation.evaluate`, `_Pair.run`, `inference.resamples`, `conditional_choice` | Combination currently runs automatically; resource status is checked after statistics. A post-return wrapper cannot implement EVAL_AUTH. |
| Compatibility, serial-union and orchestration test source | Existing parity examples are partial; serial union includes blank-only codes; orchestration PASS tests use fixed numerical seams. Reading these tests is not running them. |
| Candidate freeze sections 2, 3, 7–10; clarification sections 2, 3, 5, 7 | Corrected v3.1 values, populations, chronology, complete families, exact RNG and inclusive resource ceilings remain binding. |
| `artifact_snapshot.py`; local app authorization; schedule controller | Stable read pattern and procedural local controls exist. Research grants, exclusive publication and rollback-resistant custody do not follow from them. |
| CI workflow and locked dependencies | CI requests Python 3.10 on `ubuntu-latest`; that moving label is not a frozen research kernel/image/filesystem profile or new C3 certification. |

History read: `7874ded` clarified the resampling protocol; `6d695af` added
synthetic C2; `5ab1c39` refreshed continuity; `2da9d7d` added the reviewed C3
design; `d55421e` recorded seven policy approvals. No current remote or CI check
was performed. Earlier CI/reviewer claims remain dated reported evidence.
Continuity's statement that C3 has not started is stale navigation, not a reason
to ignore the current design. Earlier human beliefs, legacy candidate rejection
and original artifacts remain historical evidence, not retrospectively repaired
predictions. Nothing here establishes predictive superiority.

Notation: `C(x)` is the existing compact, sorted-key, finite JSON encoding with
UTF-8 and one terminal LF; schema-defined arrays retain their prescribed order.
`H(x)` is lowercase SHA-256 of exact bytes. `ID(domain, fields)` is
`H(C({"domain":domain,"fields":fields}))`. Domain is a fixed version literal,
never a caller string. This defines NEW semantic identities only; existing C1/C2
hashes, including membership hashes, retain their exact existing byte scope.
Times/counters use integer units in new authority/resource records. Numeric
model values keep C1/C2 serialization; no decimal rounding or platform smoothing.
All proposed objects are recursively closed and strictly typed; duplicate JSON
keys, booleans in integer fields, unknown versions and nonfinite numbers reject.
A hash proves byte identity only.

`Dxx` below denotes an owner/reviewer decision still required; `Exx` denotes
missing evidence. A parameter without an accepted D record is not a default.
An evidence assertion without its E record is not a guarantee. Section 17.9 is
the closure register; new discoveries must extend it rather than be guessed.

### 17.2. B01 — causal source/evidence contract and compatibility

Propose `task033c3-asof-evidence-v1`, separate from historical-v3.1. A reviewed
source registration binds exact upstream object hashes/sizes, parser/schema
hashes, permitted seasons/GWs/fields, proof authority, correction policy,
finalization policy, exposure class, bridge/map IDs and contract version. Its
private resolver mapping never enters the research request or review export.
Before any protected stat/open/footer/decompression, the broker must hold a
current stage grant and durable access reservation. Mixed-archive partitioning
requires its own `BLIND_SOURCE_PREPARATION` grant; prediction permission cannot
open the mixed archive even for an opaque copy.

Each field-version proof has the following closed payload (shared proof objects
may be referenced by hash; no unchecked Boolean stands in for them):

| Field group | Required content and interpretation |
|---|---|
| Identity | season, element ID where relevant, fixture ID where relevant, source GW or target GW, field enum, raw object digest plus row/column locator; no display-name join |
| Value | typed value or explicit null, original-value encoding digest, parser/normalization revision, exact v3.1 value digest; null has provenance too |
| Event time | registered kickoff interval, completion interval for performance fields, authoritative deadline interval and their independent proof references |
| Knowledge time | earliest **proved** availability interval for this exact value version; not a claim to know the unknowable first-ever publication time |
| Capture/parse time | actual capture and parse instants, capture authority and proof digests; these are distinct from valid/knowledge time |
| Revision lineage | immutable version ID, predecessor hash or explicit genesis, correction reason enum, attribution proof; no replacement of predecessor bytes |
| Assurance | proof method enum, attestor/key identity, raw evidence digest, uncertainty bounds, verified scope and permitted claim literal |

For target deadline interval `[d_lo,d_hi]`, require snapshot availability upper
bound `< d_lo`. For every released history field require same season, source
GW `< target GW`, proven source-universe membership, kickoff upper bound
`< d_lo`, completion upper bound `< d_lo`, and exact value-version knowledge
upper bound `< d_lo`. Equality and overlapping/unknown bounds reject. A later
capture may attest earlier availability only through independently attributable
pre-cutoff evidence for identical bytes; a newly signed retrospective assertion,
filename or Git timestamp alone does not establish it. Parse time never moves
a value's knowledge time backward. The proof-method allowlist, trust anchors,
time uncertainty and acceptable attribution are **D01/E01**, currently empty.
No numerical grace period makes a post-cutoff value causal.

The broker needs two independent checks: (a) this exact historical-v3.1 value
has admissible pre-cutoff proof; (b) all required identities/missingness and
value semantics match the frozen v3.1 contract. A corrected v3.1 value first
available after the cutoff fails (a), even if an earlier different value exists.
Do not substitute that earlier value, drop the affected row/GW, choose a different
archive or call it statistical missingness. The required study scope remains
blocked unless a prospective source/protocol amendment is reviewed. A correction
of unchanged value bytes still needs attribution to that exact value and its
identity, not a matching decimal elsewhere.

Illustrative chronology vectors, not observations or executed tests (cutoff
lower bound 100 in arbitrary time units):

| Source GW / target GW | Kickoff upper | Completion upper | Exact-value knowledge upper | Decision |
|---|---:|---:|---:|---|
| 4 / 5 | 80 | 90 | 95 | Timeline predicates pass; remaining identity/v3.1 proofs still required |
| 5 / 5 | 80 | 90 | 95 | Reject same-GW history |
| 4 / 5 | 101 | 110 | 111 | Exclude causally ineligible postponed fixture by frozen rule; record exclusion |
| 4 / 5 | 80 | 101 | 95 | Reject inconsistent or still-playing history |
| 4 / 5 | 80 | 90 | 100 | Reject equality at cutoff |
| 4 / 5 | 80 | 90 | unknown | Reject unproved availability |
| 4 / 5 | 80 | 90 | 105 for frozen corrected value; 95 for different predecessor | Block v3.1 compatibility; no predecessor substitution |

The implementation candidate remains
`src/fpl_decision_engine/research/c3/source_adapter.py`, broker-only and not
created here. It must not import the mixed legacy backtest loader. Prediction
and outcome releases have separate inventories, descriptors, capabilities and
process lifetimes. The following is the proposed mapping boundary:

| v3.1 inputs | C1/C2 target and parity obligation |
|---|---|
| PREDEADLINE `season,target_gameweek,element_id,code,position,team_id` | TargetContext plus season-scoped bridge; preserve snapshot universe and position/team, exclude AM/unknown positions by frozen rule with counts |
| Snapshot time/deadline and status/chance | Proof-backed Availability; normalize recognized letter case only, retain unknown versus null and target-next-round binding; do not derive knownness from a Boolean alone |
| FIXTURE identity/GW/home-away teams | Exact sorted target fixture tuple; explicit verified blank, no absent-file inference. Exclude scores/finished fields from predictor release |
| PLAYER_FIXTURE identity/GW/historical position/minutes/starts/xg/xa | HistoryFixture and PeerHistory in same-season calendar windows; exact joint observations, registered peers, source-time position and C1 flags backed by proofs |
| FEATURE previous-GW minutes/context, prior total minutes/xg/xa and rates | Independent reconstruction on admitted fixture history must match frozen U0 formula and null semantics; feature rows alone cannot prove admissibility |
| `_previous_context` reason fields | OBSERVED including recorded zero -> value; verified blank -> BLANK/null; proved absent previous registration -> NEW_ENTRANT/null; genuine unavailable context -> MISSING_CONTEXT/null only where frozen rules permit. Missing expected source rows remain corruption |
| `prior_xg_per_90`, `prior_xa_per_90` | Existing nullable aggregate numerator and total-minute denominator; do not replace with UA1 event-specific denominator, repair missing event exposure or change no-history semantics |
| Public preseason membership map | Proved promoted diagnostic only; no model input or inference from performance. No map exists by approval of this design |
| Separately granted finalized fixture outcomes | Exact expected fixture set and original nullable minutes/starts/goals/assists to C2; exclude total FPL points and unrelated archived statistics |

The existing feature builder's lower-GW/kickoff filtering does not itself prove
source-universe or field-version availability. If adding proof filters would
change a required frozen feature/value, fail **D01/E01/E06**, not silently
recompute a more convenient U0. The all-row/all-field comparison includes null,
zero, datatype, overflow flags, previous-GW reason and row/fixture membership;
not just equal final point totals. Existing functions may serve as read-only
semantic references; independently handwritten/algebraic oracles must also
check them on generated examples in a later authorized task.

Keep `restricted_pseudo_backtest` and finalized-assignment limitation explicit.
Field-causal performance inputs do not prove predeadline fixture scheduling.
Propose separate claims `performance_asof_verified` and
`fixture_assignment_verified_predeadline=false`, never a blanket
`perfect_causal_replay=true`. **D01** must approve the precise claim wording and
which finalized context is permitted; stronger scheduling claims need a separate
amendment and proof. Outcome finalization requires an exact whole-GW expected
fixture inventory, attributed finalization/version proof and complete identity
reconciliation. A legitimate null is retained; an inaccessible/corrupt object
or unfinalized GW is not a scored partial subset. Admissible unavailable-object
reason enums and finalization authority remain **D01/E01**, not inferred here.

### 17.3. B02 — development authority, time and recovery proposal

Propose profile `A2-SINGLE-OPERATOR-DEV-1`. Scope is exactly the two development
seasons, not confirmation. Same owner controls intent and operation; no statement
of independent custody, outcome ignorance or protection from owner/root, signer,
broker, verifier or kernel compromise is valid. Separate OS privilege domains
are necessary even when the human is the same. Approved-code dispatch can be
controlled; a malicious already-authorized evaluator cannot be proved unable
to reconstruct a combination from component parameters/outcomes.

Proposed roles: owner-operated offline signing device; online verifier/ledger
service with public issuer key only; broker with source capabilities and no
issuer key; one confined compute worker; publisher with owned-output capabilities
only. An online receipt authentication key, if selected, is separate, has only
receipt audience and cannot sign grants or trust-anchor changes. Worker cannot
access any issuer/receipt private key, ledger mutation API, unrestricted source
root, network, inherited privileged descriptor, executable installation or host
control socket. Different process names under unrestricted same UID are invalid.
No device, key, account or service is assumed to exist or created here.

Concrete cryptographic proposal for **D02**: Ed25519 signatures over
`ASCII("TASK033C3-GRANT-V1") || LF || C(payload)` (LF is byte 0x0a); SHA-256
identities; exact 32-byte public key and
64-byte signature encoded as lowercase hex. Key ID is H of raw public-key bytes.
Signature envelope has exactly `schema,payload,key_id,signature_hex`; payload
has the following fields, all required, with explicit null only where stated:

- `authority_profile`, `assurance=SINGLE_OPERATOR_DEVELOPMENT_ONLY`, opaque
  issuer/owner/audience IDs, `approval_record_sha256`, `request_id`, `study_id`,
  exact stage enum, code/environment/profile IDs and protocol hashes;
- `source_scope` as ordered exact registration/field/season/GW bindings;
  `prediction_freeze_id`, `outcome_id`, `component_core_ids` when already known;
  otherwise only the closed prebound predicate below, never a wildcard;
- `publication_slot` and package kind when applicable; no path, URL or key material;
- 256-bit one-use nonce, `use_limit=1`, `issuer_epoch`, `not_before_utc_ns`,
  `expires_utc_ns`, clock-policy ID and recovery-policy ID.

A pre-issued conditional grant cannot contain unknown future component hashes.
It binds exact request/freeze, candidate UM1UA1, and predicate
`BOTH_COMPONENTS_ALL_COMPLETED_GATES_PASS_FOR_THIS_ATTEMPT`; the verifier later
creates a one-use derived permit binding exact component/core/resource/join
hashes after checking that predicate. A post-outcome owner signature cannot
introduce a newly chosen candidate, scope, gate or seed. Grant payloads and
signatures remain immutable; no placeholder hash is filled in later.

Proposed **D02** numeric choices: grant validity at most 36 hours, run time never
extended beyond frozen resource ceilings; an operator-attested UTC anchor made
within 5 minutes of launch with declared uncertainty at most 2 seconds; a
suspend-inclusive monotonic clock and boot identity; backward realtime step or
realtime-versus-anchor drift over 2 seconds stops progress. Treat uncertainty
conservatively: the whole derived UTC interval must lie inside grant validity.
No expiry grace, renewal during a stage, cross-boot monotonic subtraction or
clock-reset retry. Suspend/resume detection stops this initial profile even
though its elapsed time must be charged. These numbers and clock mechanisms
are proposals requiring owner acceptance and backend evidence, not evidence
of trustworthy UTC or historical archive time.

Online verifier maintains a serialized, durable ledger with increasing sequence,
previous-event hash, boot/session identity, authority epoch and study/stage fence.
Propose no cached revocation grace: every new access/release, dispatch and
publication admission serializes against current local verifier state. Missing
verifier response denies operation. While authorized work runs, propose heartbeat
at most 1 second and whole-group cancellation target at most 2 seconds after
revocation/heartbeat loss. These are **D02/E02** proposed upper bounds, not
real-time OS guarantees. Reads already dispatched may complete during this
window; released bytes remain exposed. If the owner requires instantaneous
revocation, this profile is unsuitable and must remain blocked.

Ledger ordering must durably record nonce consumption and possible exposure
before issuing a bounded per-object capability. Capabilities have exact object,
field, byte and time scope, cannot be extended by worker input and cannot survive
a stage/boot change. Broker checks the same epoch/fence before each new protected
operation, including stat and footer reads; it records bounded release progress.
An already-spent nonce can retrieve only an existing safe receipt with separate
current read permission, never execute again. At first outcome dispatch mark
`POSSIBLY_EXPOSED`; successful release refines that event, failure never erases it.
Propose read leases no longer than 1 second, renewed by the broker for continued
reads, subject to the same cancellation limit. Blocked I/O and buffered releases
must be included in **E02**, not treated as instantaneous atomic reads.

Custody/recovery candidate: offline issuer enrolls exact public trust anchor;
verifier stores append-only epoch/nonce/exposure events on trusted storage outside
worker/checkpoint scope; a separate owner-controlled witness store synchronously
acknowledges each authority/exposure reservation before protected dispatch.
Witness acknowledgments bind monotonically increasing sequence and chain hash.
That separate store is rollback detection only under its explicit nonrollback
trust assumption, not independent human custody or a verified offsite backup.
Physical/provider choice, authentication, atomicity and retention are **D02/E02**.
An offline stale digest alone is insufficient. No witness, uncertain latest head,
ledger fork or restored state behind the witness -> stop all new grants/access.

Rotation requires an owner-approved new epoch and enrollment record; revoke old
key for new activity while preserving verification of historical receipts with
its historical validity interval. Lost issuer key -> no new grants until new
trust anchor enrollment and ledger reconciliation. Lost receipt key or witness
state -> no manufacture of missing history. Restoring a worker snapshot cannot
restore unspent authority. Recovery uses a distinct grant and attempt ID, only
reverifies named artifacts; it cannot resume scoring or authorize prediction
regeneration after possible exposure. Retention lengths and compromise response
are **D02/D07**; key generation, witness installation and recovery drills remain
outside this task.

### 17.4. B03 — proposed versioned EVAL_AUTH interfaces

Candidate API namespace: `research.c2.controlled_v2`; schema prefix
`task033c2-controlled-*-v2`. No code or schema is added. Public v1 `evaluate`, its
synthetic literals, result bytes and tests stay intact. This is a new structural
input/result layer around unchanged pure numerical rules, not relabeling an old
PredictionBatch or calling private `_Pair` from C3.

All v2 input views carry `protocol_identity`, clarification hash, four formula
IDs, stage DEVELOPMENT, exact two-season/GW universe, source-class literal
`GENERATED_SYNTHETIC` or `REGISTERED_DEVELOPMENT_V31`, request/freeze/outcome/join
bindings and view bytes hashes. Source class describes provenance, not authority.
Real confirmation is rejected by this initial profile, not admitted using a
synthetic confirmation switch. Pure C2 receives typed structural admission
receipts; C3 alone authenticates grants. No signer, clock, I/O or source resolver
enters C2. Full four-slot prediction freeze remains mandatory before outcomes.

Each controlled input has two separately hashed closed objects: `core_view`
contains request ID, prediction/outcome/join **core** IDs, candidate projection,
ordered rows and fixed original source/proof/adapter identities; `admission_binding`
contains the actual attempt-specific freeze ID, grant/permit and receipt bindings.
The pure API validates both. Deterministic cores hash only `core_view`, never the
whole controlled input envelope. Likewise admitted source views keep run-specific
capture/parse/verification receipts outside their deterministic value/provenance
projection. Immutable original archive proof identities remain inside it. This
prevents a freeze receipt timestamp from indirectly contaminating core identity.

| Proposed pure API | Closed input/output contract and admission |
|---|---|
| `evaluate_component_v2(candidate, component_view, joined_outcomes, admission)` | Candidate is exactly UM1 or UA1. View contains U0 and that candidate only, with exact projections from full freeze and original row universe including blanks. Admission binds this component stage, attempt/request/join and proof digests. Returns one StatisticalPackageV2. Controller calls UM1 then UA1, measuring/persisting each completed interval between calls. No combination memberships, metrics, distributions or resamples are computed. |
| `admit_components_v2(statistics, completed_stage_evidence)` | Validates binding and completed component intervals, produces structural admission decision with separate statistical status and stage-resource status. No computation is rerun; no final attempt PASS is asserted. C3 checks actual evidence authenticity before calling. |
| `evaluate_combination_v2(combination_view, joined_outcomes, component_admission, permit)` | View contains U0/UM1UA1 projected from the original freeze. Checks both component gates, exact join/freeze/core/admission IDs and derived permit. Fresh isolated worker starts only after durable C3 authorization. Returns one StatisticalPackageV2; no synthetic fallback or private sentinel bypass. |
| `prepare_selection_v2(package_cores, admissions)` | Pure fixed priority produces `SelectionPlanV2` with `effective=false` pending final resource/publication gate. Both component passes require evaluated combination. This plan is private and cannot be exposed as completed selection. |
| `validate_completed_result_v2(plan, final_gate, publication_evidence)` | Pure structural validation of matching completed evidence yields development decision or rejects. The outer verifier must authenticate final authority/resource evidence first; this function cannot certify clocks or publication. Final-gate backend remains B04/B07 blocked. |

`StatisticalPackageV2` retains ALL existing membership, populations, metrics,
endpoint families, clusters, diagnostics, calibration hashes and objective
failure reasons. Its `statistical_status` is PASS/FAIL only for fully evaluated
statistics; it contains no clock, resource digest, grant/attempt ID, selection,
publication timestamp or inferred eligibility. Numerical/infrastructure failures
are separate attempt failures; never manufacture DO_NOT_CONFIRM.
`NOT_EVALUATED` is a disjoint record with candidate and reason only, no membership
or result payload. `SelectionPlanV2` records exact package-core hashes and the
fixed rule, but is neither a research result nor authorization for confirmation.

Owner/reviewer **D03** must approve these exact names/field closures and distinguish
(a) statistical FAIL after complete calculation, (b) absent computation by rule,
(c) authorization/infrastructure interruption. **E03** requires independent
old/new arithmetic and all 9,999-draw parity; no implementation test exists yet.
New core bytes differ intentionally from the v1 envelope; compare defined
mathematical payloads, never claim entire v1/v2 byte equality or strip resource
fields from a published v1 artifact.

Truth table for DEVELOPMENT after completed component intervals:

| UM1 / UA1 complete component gates | Permit and combination | Permissible next state |
|---|---|---|
| FAIL / FAIL | Not requested; no combination work | Pending plan selecting none; DO_NOT_CONFIRM only after successful final gate/publication |
| PASS / FAIL or FAIL / PASS | Not requested; no combination work | Pending plan selecting the passing component; same final prerequisites |
| PASS / PASS | Missing/expired/revoked permit | `UNAUTHORIZED`, combination NOT_EVALUATED; no completed selection or fallback to UM1 |
| PASS / PASS | Valid permit; combination full statistical FAIL with completed resource interval | Pending UM1 plan under frozen priority |
| PASS / PASS | Valid permit; combination full PASS | Pending UM1UA1 plan |
| Any | Missing resource evidence, interrupted computation or final write breach | Infrastructure failure; no completed selection or candidate eligibility |

A component statistical rejection and an infrastructure failure are not
interchangeable. Stage-resource failure stops this proposed execution attempt;
it does not falsify a component's statistical record to open or close a gate.
Whether this stricter attempt failure semantics and stage admission preserve
all frozen resource-gate meanings needs **D03/D04** protocol review. Repeated
entry cannot consume RNG twice or change seeds: each evaluated package follows
the current full reference stream; separate views cannot change its universe,
code union, season order, common populations or pair-specific membership.

### 17.5. B04 — completed intervals, final gate and the remaining terminal problem

Propose stage receipt `task033c3-completed-resource-v2` with exact fields:
`schema,attempt_id,request_id,stage,candidates,seasons,predecessor_receipt_ids,
input_ids,output_manifest_sha256,profile_id,clock_session,start_ns,end_ns,
measurements,limits,enforcement_events,status,ledger_sequence`.
Nested measurements specify units and process scope, peak/total rather than an
ambiguous number, and whether controller/OS or worker supplied each observation.
No end time or PASS may precede the final operation of the interval it certifies.
Receipt emission has a cost charged to the next interval and enclosing total;
there is never an uncharged gap. Stage PASS means **this completed interval**,
not a promise about remaining work. Missing/unknown observation is not zero.

Proposed accounting (**D04**, stricter counting must be explicitly accepted):

- Prediction clock begins before broker preparation/read for the first target;
  ends only after prediction payload verification, writes and freeze commit.
  Per-season/candidate 6-hour counters include that candidate's input and output
  I/O. Shared source preparation/freeze costs are conservatively charged in full
  to each affected candidate; no favorable fractional allocation after outcomes.
- Propose a conservative enclosing 24-hour clock beginning before the first
  run-specific prediction/source preflight, including predictions, outcome
  authorization/resolution, joining, both component phases, conditional wait,
  combination if permitted, selection, verification, all publication and final
  authority/resource recording. This is broader than a statistics-only reading
  of the freeze and needs D04 acceptance, not a silent reinterpretation.
  Waiting for grants, retries and blocked I/O cannot pause this elapsed interval.
- Per-season/candidate scoring counters preserve the 6-hour ceiling. Shared
  two-season bootstrap, join and publication work is charged in full to each
  affected season/candidate counter. If this conservative attribution makes the
  study infeasible, report that; do not silently charge bootstrap elsewhere.
  Prediction and scoring get distinct counters in this proposal; whether the
  freeze intended a combined 6-hour counter is an explicit **D04** ambiguity.
- Source registration is a separately authorized preparation task; run-specific
  source capture, verification or partitioning cannot be renamed registration
  to escape the execution budget. Exact reuse/capture boundary is **D04**.

Completed stage chain:

```text
request + scope + preflight
  -> prediction payload + completed freeze interval
  -> outcome reservation/read/freeze + guarded join
  -> UM1 statistics + completed UM1 interval
  -> UA1 statistics + completed UA1 interval
  -> components admission + fresh durable derived permit (both pass only)
  -> UM1UA1 statistics + completed combination interval (if authorized)
  -> deterministic selection plan + sealed prepared inventory
  -> payload installation/verification/flush [UNCERTIFIED]
  -> observation of completed publication work
  -> final resource/authority attestation
  -> completed-result admission [BLOCKED on terminal contract below]
```

Component dispatch consumes only receipts for already-completed work and checks
remaining *enforced* time/space. Proposed dispatch headroom is 15 minutes for
finalization (D05); it is admission headroom, not proof publication will finish.
A final breach invalidates the attempt even if all component receipts passed.
The owner-approved stage direction must be formalized as a protocol clarification:
"every component resource gate" in the freeze cannot silently become a promise
of final PASS. No supplied v1 PASS, fake FAIL or evaluator rerun closes this gap.

**Terminal-resource problem remains an explicit blocker, not a hidden exclusion.**
A stored final receipt can hash/measure earlier work, but cannot contain a
measurement of the completion of its own last write/fsync/commit. Adding another
marker moves the problem; a post-rename timer check allows an unqualified reader
to see a late result unless admission remains guarded. Budget reservation is not
measurement. The existing local snapshot helper supplies no timed durable commit
primitive that resolves this.

Concrete review candidate **D04-F**: separate immutable payload from a trusted
publication admission service. Payload installation is never visible as success;
only the service can admit a result after observing all completed payload and
receipt I/O and checking the live enclosing budget, current authority and fence.
Its final attestation references prior payload/resource hashes; those objects do
not hash back to it. This is an acyclic data graph. However, persisting/exporting
the service's terminal admission and its recovery proof is itself work. This
proposal does **not** declare that work outside the frozen ceiling.

Before implementation the owner/protocol reviewer must choose and accept one
of these precise closure paths, with evidence:

1. Prove a backend terminal transaction/admission contract with an explicit
   linearization point, deadline and durability accounting covering its final
   write/acknowledgment, plus reader/crash semantics. No such backend is established
   in the repository; naming a database transaction or no-replace rename is not proof.
2. Prospectively amend the protocol to define payload-publication completion and
   separately bounded audit/admission work. Specify which final I/O lies in each
   budget, how an audit breach prevents success, and what a recovered reader
   accepts. This is a **proposed amendment**, not permission to exclude the audit.
3. Keep completed publication blocked. This is the current effective disposition.

Thus completed-stage gates and identity dependencies are concrete and acyclic;
the all-inclusive durable final gate is specified as an unmet acceptance contract.
No completed result, adoption or eligibility is possible until **D04-F/E04/E07**
close. This limitation is a central independent-review question, not a claim
that B04 was solved by renaming publication. Final receipts must never attest
future resource success. The same issue applies to prediction freeze completion
where outcome release depends on a durable completed freeze; it cannot be bypassed
by treating prediction publication as outside the budget.

### 17.6. B05 — contained Linux/Python 3.10 candidate and proposed budgets

Candidate profile `C3-LINUX-X86_64-PY310-1`, not an implemented platform guarantee:
owner-administered dedicated Linux environment, exact pinned kernel/build, boot
configuration, image digest, CPU/native-library identities, CPython 3.10 patch
version, lockfile and resolved wheel hashes. No reliance on the moving CI image.
Proposed OS baseline is a supported Linux 6.6-series build with cgroup v2,
seccomp and a local ext4 publication volume; exact revisions, mount options,
storage/cache assumptions and support horizon are **D05/E05/E07**. This is a
candidate requiring proof, not a claim about this macOS host or every Linux build.
macOS stays native synthetic/development-only. Emulation/VM results bind that
profile and cannot certify native macOS enforcement or byte parity.

Worker runs without privilege escalation, mount/network/ptrace/host-control
capabilities, with only exact read-only stage inputs and bounded output channels.
Proposed seccomp policy denies process fork/vfork, arbitrary exec, namespace
creation and unexpected clone modes; permit only audited thread creation needed
by pinned libraries. Reject clone3 unless a separately proved policy constrains
it. Establish constraints before numerical imports or any source release; inherited
FD/mount/environment allowlists are explicit. An architecture test is regression
coverage, not proof of sandbox enforcement. Syscall/FD/mount lists and container
launcher/provider remain **D05/E05** and must not be guessed by an implementer.

Concrete numeric **proposals**, all OPEN under **D05**, with outcome-free full-scale
certification **E05** required before any protected outcome access:

| Limit | Proposed enforceable scope and value | Acceptance evidence still missing |
|---|---|---|
| Compute | One evaluator address space, no child processes; at most 4 total live tasks in its cgroup (main plus all threads) | Conservative total-task cap rather than treating CPU quota as thread count; thread creation/syscall escape and pinned native-pool compatibility |
| Worker memory | 1,536 MiB virtual-address ceiling AND 1,536 MiB cgroup memory ceiling, swap disabled | This is a proposed conservative restriction, not an equation of AS/cgroup usage with RSS. Prove resident-accounting relation and brief-spike containment on selected kernel, including shared pages/file maps; independently measure peak RSS |
| Trusted overhead | Broker 256 MiB; publisher 128 MiB; ONE combined 128 MiB allowance for controller, verifier/ledger, witness-related processes and otherwise unassigned trusted helpers; aggregate experiment peak RSS <=2,048 MiB including worker | This is not 128 MiB per controller/verifier/witness role. Exact process topology, witness placement, collective accounting and enforcement remain D02/D05/D07 and E02/E05/E07 gates; see clarification below |
| Helper tasks | At most 16 total tasks across trusted role groups, no second numerical worker; serialize broker/publisher work where possible | Role-specific no-fork/exec permissions, process-tree membership and correct concurrency accounting |
| Wall time | Frozen <=6 h per season/candidate prediction/scoring and <=24 h complete evaluation; 15 min finalization headroom; 1 s heartbeat / 2 s cancellation target | Continuous enclosing timer, defined suspend/clock semantics and last-operation breach tests. Kill latency is not zero and a late kill still fails |
| Scratch | Dedicated per-attempt storage, <=16 GiB allocated AND logical bytes, <=65,536 inodes, including temporary copies/decompression/spill | Filesystem hard quota plus bounded logical writers; sparse files, hardlinks, mmap writes, unlinked-open files and quota exhaustion coverage |
| Canonical payload | <=512 MiB per file, <=8 GiB total, <=8,192 files per attempt across predictions/outcomes/evaluation/receipts, no compression discount | Count before every write; chunking fixed before outcomes, exact inventory and full-PMF/analytic reconstruction verified |
| Input/parser | <=512 MiB uncompressed object; <=16 GiB cumulative uncompressed bytes processed per attempt, repeats counted; metadata object <=1 MiB, JSON nesting <=16 | Trusted registration declares exact sizes before open; streaming parser/decompression cap; incompatible source layout blocks, cannot silently repartition under another grant |
| Audit/failure reserve | Additional dedicated <=64 MiB and <=4,096 records per attempt; each receipt <=64 KiB, sanitized stderr <=64 KiB total | Reserve before release; include its CPU/RSS/time in applicable totals. Overflow stops; cannot drop exposure events. B04-F determines terminal audit treatment |

**B05 review clarification — shared budget, unresolved topology.** The proposed
sum is worker 1,536 + broker 256 + publisher 128 + combined trusted helpers 128
= 2,048 MiB. Controller and verifier remain functionally distinct roles; separate
processes/privilege domains must collectively fit the ONE 128 MiB allowance,
not receive 128 MiB each. This does not merge their permissions or select a
single-process implementation. All descendants and helper processes count.
The exact process layout and subdivision of that allowance remain blocked owner
choices under D02/D05/D07; fitting or enforcing it has not been demonstrated.

The witness store in section 17.3 is outside the restorable worker checkpoint,
not automatically outside experiment accounting. Its client, server, ledger and
storage-service processes attributable to this attempt must be accounted for
within the combined 128 MiB allowance and existing helper-task ceiling. A local
witness does not earn an extra budget. An external/shared witness service is
NOT an accepted exclusion: it requires a reviewed attribution/enforcement method
within the same 2,048 MiB aggregate ceiling, or a prospective protocol amendment
explicitly changing scope. Until then that deployment is unsupported. Witness
placement, shared-service attribution and rollback assurance remain unresolved;
no witness backend is selected or assumed to exist. Witness request/acknowledgment
waits, retries, CPU, storage and I/O also stay in the applicable existing time,
process and byte accounting; separate storage supplies no uncharged allowance.
If any of this is unmeasurable, uncontained or infeasible, preflight fails. Neither
the frozen 2 GiB ceiling nor the absence of enforcement evidence has changed.

Binary units: MiB=1,048,576 bytes, GiB=1,073,741,824 bytes; limits are inclusive,
no rounding before comparison. Proposed canonical cap counts all logical payload
bytes written for the attempt; scratch peak includes simultaneous copies. Audit
reserve is storage headroom only, not a waiver of final time or memory limits.
Any requirement for more space/tasks/VA changes D05 before outcomes, never after
seeing performance. Grants bind accepted numbers/profile hash, not mutable config.

Cgroup `memory.max`, RLIMIT_AS, pids limits, mount quotas and watchdogs are
**candidate mechanisms**. Neither cgroup memory counters nor virtual-address
limits are called RSS measurements. A conservative containment proof needs the
actual supported mappings/process model and continuous failure behavior; absent
that proof this row remains unsupported. Brief violations missed by polling,
quota overrun, task migrations, unavailable counters, controller death or hung
I/O fail closed. Child logs/configured environment are supplementary only.
No unproved kernel, real-time scheduler, filesystem flush or power-loss guarantee
is asserted. The implementer must not install a backend under this design grant.

Certification must separately cover existing 100-player rejection mechanics,
700-player full two-season/all-37-target pipeline, real conditional combination
using all 9,999 draws in both streams with no fixed-statistic seam, repeat byte
identity in one frozen environment, all last-write/timeout/cancellation failures,
and the budgeted authority/publication chain. Existing C1 single-target evidence
and C2 mocked orchestration do not satisfy these. No such benchmark ran here.
If no outcome-independent synthetic population traverses real component PASS,
conditional end-to-end feasibility remains unproved; forced dispatch can describe
kernel cost only. Actual source completeness/real-development feasibility is
still a later authorized task. The 3,000-candidate/12-view/60-second decision corpus
remains NOT_EVALUATED and a separate downstream blocker.

### 17.7. B06 — decimal identity vectors and adapter parity contract

Propose bridge schema `task033c3-decimal-bridge-v1`: ordered entries contain
`season,element_id,official_code,player_code,source_identity_sha256,
verification_evidence_sha256`; order by season then numeric element ID.
`official_code` is a strict positive integer in the v3.1 signed BIGINT range
1..9,223,372,036,854,775,807; `player_code` is its unique minimal ASCII decimal
UTF-8 spelling. Proposed explicit upper bound requires **D06** acceptance; it
reflects the tracked BIGINT source field and must not be silently narrowed to
JavaScript's safe-integer range or parsed through float. Raw text sources require
a separately reviewed parser contract; an alias cannot be accepted by coercion.
Within season, one element maps to one code and one code to one element. Across
seasons, official proof must establish continuity; element ID alone does not.
The same code may have a different element ID/club/name next season, without
carrying any C1 history across seasons. Transfer/name changes never mint codes.

Literal, unexecuted design vectors:

| Input | Expected text / action |
|---|---|
| integer 1; 2; 10; 100; 20; 3 | `"1"`; `"2"`; `"10"`; `"100"`; `"20"`; `"3"` respectively |
| integer 9007199254740993 | `"9007199254740993"` exactly; no binary64 round trip |
| integer 9223372036854775807 | `"9223372036854775807"` exactly |
| 0, negative integer, 9223372036854775808 | Reject range |
| true, 2.0, null | Reject type; not integer aliases |
| raw `"02"`, `"+2"`, `" 2"`, `"2 "`, `"2.0"`, `"2e0"`, `"٢"`, `"２"` | Reject alternate spelling; no Unicode or whitespace normalization |
| same-season two elements assigned code 10 | Reject collision before any resampling |
| missing bridge for a blank-only member | Reject entire serial admission; no independent-season fallback |

Canonical sorted union bytes for the first row's six distinct codes are exactly
`["1","10","100","2","20","3"]` followed by one LF. Thus index 1 is code 10,
not code 2. A hypothetical index vector `[3,1,1,5,0,3]` has code multiplicities
`1:1,10:2,100:0,2:2,20:0,3:1`. This is a hand-specified mapping oracle, **not** a
PCG64 draw or a claimed seed result. If code 100 occurs only on blank rows, it
still occupies index 2 and keeps union size six. Dropping it changes the draw
law and RNG consumption even if its realized score weight is zero.

Cross-season illustrative mapping: season A elements 7->10 and 8->2, season B
9->10 and 7->3. Code 10 shares multiplicity between A/7 and B/9; element 7 does
not link A/7 to B/7. Canonical row key order remains season/GW/numeric element;
crossed bootstrap player order remains numeric element on its frozen nonblank
universe. No decimal lexical order is substituted there. These identifiers are
invented examples, not source rows or evidence about any player.

Future literal bytes/hashes and full-stream vectors must be independently
checked under the frozen NumPy environment; none are generated by importing
C1/C2 here. Existing serial tests use `c1/c2/c3/zz`, proving the current rule's
intent but not a complete historical-decimal adapter. **E06** must supply exact
9999-replicate reference streams with decimal union including blank-only members,
without changing seeds or adding an implementation-derived oracle.

Adapter completeness obligations beyond identity (all OPEN under E06):

| Case/field | Exact required C1 -> v2 mapping or independent oracle |
|---|---|
| U0 | Map `*_for_evaluation` nullable fields, `prediction_complete`, modeled_points, full minute PMF and Poisson means; preserve separate numeric incomplete aggregates in C1 detail only |
| UM1 | Preserve gameweek PMF, start/appearance probabilities and expected appearance sum; UM1-only raw-rate means and nulls; goal/assist distributions absent |
| UA1 | Preserve U0 minute fields/start undefined; exact posterior weights/shapes/rates and fixed exposure U0 evaluation minutes/90, not a fitted Poisson substitute |
| UM1UA1 | Preserve UM1 minute fields and same UA1 rate parameters, mixing over full minute PMF; missing attack component leaves combined total null |
| Blank | Four present complete forecasts, empty fixture tuple, support `[0]`, PMF `[1]`, zero moments; U0/UA1 start undefined, UM1/combined start as defined by C1; zero-exposure registered distribution families retained |
| DGW+ | PMF support 0..90*f; per-fixture appearance expectations sum, not appearance formula applied to total minutes; all fixture IDs and overflow counts retained |
| Nonblank missing rates/minutes/starts | No dropped row or null-to-zero; numeric component-only totals may exist while completeness=false; no false complete C2 row |
| Availability | Hard zero, soft chance, unknown, target binding, uppercase normalization and null order agree with C1/production; hard zero cannot repair missing baseline minutes |
| Strata/history | Same-season source/target positions, previous-GW reason, unweighted prior minutes, prior-only/partial event exposure and promoted map; no outcome-dependent grouping |

Hand-checkable numeric vectors for later parity review: one complete MID fixture
with baseline minutes 90, raw xG/90=1, xA/90=1 and known available status gives
U0 expected minutes 90, appearance 2, goals 1, assists 1, modeled points 10,
point-mass minutes at 90, start undefined. Two such fixtures give minutes 180,
appearance 4, goals 2, assists 2, modeled points 20. Two fixtures at 30 minutes
each have appearance total 2 (one per fixture), not an inference that the player
started. Missing raw xG with known minutes retains missing goal mean and false
completeness even when a component-only numeric total exists. No scientific
success is inferred from these algebraic examples. Numerical posterior, tail,
overflow and all-null golden values remain E06; do not invent them here.

Required ranking membership byte vector, independent of bridge serialization:
`[{"gameweek":2,"season":"2023-24"},{"gameweek":38,"season":"2024-25"}]`
plus LF. It includes no candidate, player code, exclusion or source hash. Those
belong in separate provenance fields. C2's existing membership/common-population
hash scopes remain unchanged, including blanks where the relevant scope requires.

### 17.8. B07 — identity DAG, exclusive slots and recovery

Propose the following identities; objects store their inputs and a separate
content hash. No object includes its own hash or a downstream hash. For new
contracts this refines section 9's earlier common-envelope sketch: schema-specific
objects may omit time/attempt fields that would pollute deterministic identity;
field sets must be closed explicitly in D03/D07 before schema implementation.

| Identity / domain | Exact semantic inputs; exclusions and use |
|---|---|
| `study-slot-v1` | Owner-assigned opaque study ID, stage DEVELOPMENT, authorized exposure-scope ID and protocol lineage. Stable across code/request revisions for the same scientific question; cannot mint a fresh untouched slot by changing a hash |
| `request-v2` | Study ID, exact protocol/clarification/design/contract hashes, scope/universe/exclusion rules, source registration and bridge/map/proof policy IDs, code commit + relevant source hashes, lock/runtime/native profile, authority/clock/resource policy, EVAL_AUTH and output contracts. Excludes future outcome content hash, grant nonce, attempt and times |
| `prediction-core-v2` | Request ID, ordered admitted source/view/bridge/map content hashes, all four canonical forecast slot hashes, C1 detail inventory/flags, exact universe. No resource/access receipt hashes or recording times |
| `prediction-freeze-v2` | Prediction core plus exact immutable package-manifest hash and completed authority/freeze evidence. Attempt-specific freeze; must be fully certified before outcome release |
| `outcome-core-v2` | Exact authorized registration/finalization versions, ordered canonical outcome files and missingness inventory, protocol/scope. No grant/attempt/time fields; join separately binds prediction freeze |
| `join-core-v2` | Prediction core, outcome core, bridge/join rule, canonical joined bytes and exhaustive reconciliation counts/digests. Access/freeze receipts live in attempt envelope |
| `statistical-core-v2` | Request, prediction/outcome/join core IDs, candidate, exact `core_view` hashes and ALL deterministic StatisticalPackageV2 fields. No stage resource receipt, timestamps, permit, attempt or operational choice |
| `selection-core-v2` | Ordered statistical core IDs/NOT_EVALUATED-by-rule records, frozen selection rule and candidate scope. Resource/admission-dependent effectiveness is outside this deterministic mathematical core |
| `attempt-v1` | Study slot, request, verifier epoch, durable sequence and fresh 256-bit nonce allocated before any protected access. Every interrupted/recovery/reproduction attempt gets a distinct ID |
| `prepared-package-v2` | Complete exact payload manifest/file hashes, deterministic core IDs, attempt ID and completed upstream authority/resource evidence available before publication. A package candidate, not completion |
| `publication-v2` | Exact prepared-package hash, attempt, reserved slot/fence and terminal evidence admitted by the accepted B04-F backend. Never fed back into core/prepared payload; unavailable until B04-F is resolved |
| `adoption-v1` / `recovery-v1` | New action ID/grant/ledger sequence, exact prior publication or incomplete attempt, current witness head and verification report. Outside original publication inventory; cannot change original bytes/history |

Statistical core identity excludes policy-dependent operational PASS; report it
only as mathematical evidence, never as a complete result. Changing an input
source/version/code/environment changes request/core identity even if point
scores happen to coincide. Repeating identical inputs in the exact environment
must reproduce deterministic core bytes; new attempt times, receipt hashes or
resource observations change the attempt/publication, not those mathematical
bytes. This is a new versioned contract, not retroactive field-stripping from v1.
If completed prediction freeze identity differs on reproduction, mathematical
comparison binds its prediction **core**; attempt envelopes retain both freezes
and full provenance. No ambiguity between byte equality and semantic similarity.

Acyclic references, arrows meaning "depends on already-existing":

```text
request -> protocol/source registrations/code/environment/policy
prediction core -> request + admitted views + four forecasts
prediction freeze -> prediction core + completed attempt evidence
outcome core -> authorized outcome versions
join core -> prediction core + outcome core + reconciliation
component cores -> join core + component views
component admissions -> component cores + completed resource receipts
combination permit -> component admissions + grant reservation + prediction freeze
combination core -> join core + combination view (permit bound in attempt evidence)
selection core -> evaluated cores + frozen rule
prepared package -> cores + completed attempt evidence + exhaustive inventory
terminal evidence -> prepared package + completed measured publication operations
publication admission -> terminal evidence + slot fence [B04-F unresolved]
adoption/recovery -> original publication/attempt + fresh grant/witness proof
```

Resource receipts bind completed output manifests and previous receipts, not a
future whole-result hash. A prepared manifest excludes itself and downstream
terminal receipts from its file inventory. A publication verifier requires the
separate terminal chain; directory existence is never sufficient. Terminal
attestation emission is not silently excluded from accounting: section 17.5
remains a hard stop. An acyclic hash graph alone does not prove acyclic durable
completion or inclusive resource measurement.

Proposed concurrency protocol (**D07/E07**): trusted verifier atomically reserves
both study-stage slot and grant nonce, emits a monotonically increasing fencing
number, obtains durable witness acknowledgment, then authorizes work. Only its
current fence can mutate owned staging or request publication. A losing contender
has no source read capability. No lock stolen because of PID reuse, age, process
absence or elapsed lease. Recovery first proves old workers/capabilities revoked,
ledger/witness agreement and exact authorized scope before any new fence. If
that cannot be established, remain INTERRUPTED_REVIEW_REQUIRED.

Prepared files have safe fixed logical names and exact inventories, absent-only
creation, no symlink/hardlink/device acceptance, single-filesystem staging and
bounded writes. Candidate Linux primitive is no-replace installation plus
file/directory flushes on the selected filesystem; exact syscall and durability
semantics require backend review and crash/race proof. Plain exists-check then
rename is insufficient. Privileged host mutation/storage failure is outside
local immutability assurance and must not be described as impossible.
`artifact_snapshot.py` proves none of slot arbitration, fencing, publisher
election, atomic complete visibility or crash durability.

Completed-result adoption means current authorized re-verification of the SAME
publication identity, SAME exact manifest and every file byte, SAME upstream
provenance and valid terminal evidence. Record adoption separately, do not rewrite
timestamps or resource evidence, and perform no new source read or scoring.
A new measured attempt with equal core but different receipts is a reproduction,
not adoption. It needs a separately authorized reproduction namespace linked to
the original study/exposure history; it cannot overwrite or reopen its slot.
Different bytes presented for an occupied slot -> PUBLICATION_CONFLICT. Different
requests for the same study do not escape the slot or restart an unseen trial.
Assignment/governance of study IDs and reproduction namespace is **D07**, not a
UUID-generation solution to scientific replay.

Recovery matrix:

| Last independently durable fact | Permissible action under fresh recovery authority |
|---|---|
| Reserved nonce, no proven dispatch | Preserve consumed nonce; reconcile broker/witness events. Absence of a completion event is not proof of no read |
| Prediction staged, no certified freeze | No outcome access; no adoption of partial prediction; retry only with affirmative no-outcome-exposure proof and exact approved identity |
| Certified freeze, outcome dispatch reserved | Treat outcomes as possibly exposed, prohibit prediction regeneration in study path |
| Components complete, no combination permit | Preserve component evidence; no completed selection if both passed and combination remains unauthorized |
| Combination permit reserved or start uncertain | Treat compute as possibly performed; never replay automatically or label unseen |
| Payload installed, terminal evidence absent/late/corrupt | Quarantine as uncertified; no result admission, no synthetic reconstruction of historical resource PASS |
| Complete publication with accepted terminal evidence | Reverify exact chain/files under current read grant, reconcile witness/high-water, then exact adoption only |
| Any rollback/fork, missing latest witness or unknown cleanup | Block new authority and reuse; preserve exposure; owner/reviewer reconciliation required |

No recovery pass from hashing today's files proves historical authorization,
chronology, untouchedness or a missing past resource measurement. Cancellation
records survive crashes; discarded payloads do not erase possible computation.
Cleanup touches only verified owned scratch under scoped authority; unknown
cleanup quarantines. Retention and secure deletion guarantees are not assumed.
No public upload, offsite backup, key recovery, successful restore or promotion
is established. **NO VERIFIED OFFSITE BACKUP** remains the project status.

### 17.9. Open decision/evidence register and B01–B07 ledger

All entries below are required closure inputs, not optional enhancements. Owners
are roles; no person, key, service or source instance is invented. Approval of
section 3.2 does not approve the following parameter values or missing evidence.
No request or grant may use unresolved placeholders.

| ID | Decision owner and precise outstanding acceptance |
|---|---|
| D01 | Owner + source/protocol reviewer: admissible attribution/proof methods and uncertainty, exact as-of versus corrected-v3.1 compatibility rule/claims, finalized scheduling limitation, outcome finalization authority and unavailable-object enums. No source instance is approved; incompatible frozen corrections require an amendment or continued synthetic-only scope |
| D02 | Owner + security reviewer: Ed25519 format/provider and key enrollment/custody/rotation/compromise policy; role/audience IDs and privilege separation; 36 h validity, 5 min anchor age, 2 s uncertainty/drift, 1 s lease/heartbeat and 2 s cancellation semantics; clock source/boot/suspend policy; verifier/witness backend, synchronous acknowledgment and retention/recovery authority |
| D03 | Owner + independent C2/protocol reviewer: exact v2 API/schema field sets, synthetic/registered provenance literals, structural permit projection, incomplete versus statistical FAIL semantics, immutable selection-plan effectiveness, compatibility/parity acceptance. No real confirmation API admitted by this development profile |
| D04 | Owner + protocol/resource reviewer: completed-component admission interpretation versus freeze wording; exact prediction/evaluation/shared-cost intervals, separate versus combined 6 h phase reading, retries/waits and registration boundary. D04-F must choose/prove terminal completion or explicitly amend audit accounting; current effective choice is remain blocked |
| D05 | Owner + platform reviewer: all proposed numeric table values, exact Linux/kernel/CPython/native/wheel/image/filesystem builds, backend launcher and syscall/FD/mount allowlists, conservative memory relation, helper/witness accounting, headroom and certification dimensions. No sampling-only waiver or platform substitution |
| D06 | Owner + source/statistical reviewer: exact bridge schema/BIGINT bound/parser, official identity evidence, no normalization aliases, full parity/missingness mapping; freeze independent decimal/reference vectors and same-environment numerical acceptance without rounding |
| D07 | Owner + publication/recovery reviewer: study/exposure slot governance, reproduction/adoption scope, fencing/ledger/witness atomicity, terminal evidence linkage with D04-F, supported durability/power-loss claim, retention and cleanup policy, recovery admission and RPO/RTO claim (currently unmeasured) |

| Evidence | Required proof; current disposition |
|---|---|
| E01 | Separately authorized exact source-version/correction/completion/availability, bridge/map and finalization evidence; no protected source inspected here; availability/compatibility may prove impossible |
| E02 | Independent real isolation, issuer/verifier/capability/replay/revocation tests and witnessed rollback/clock/recovery failures on accepted backend; no keys/services created and no tests run |
| E03 | Closed v2 contracts, no unauthorized combination dispatch, immutable projections and independent v1/v2 numerical/RNG/membership/selection parity; no v2 implementation exists |
| E04 | Completed-stage scope/timer evidence and full terminal accounting, last-byte/fsync/ack timeout and reader-admission proof; no final backend or justified audit exclusion exists |
| E05 | Actual continuous memory/task/thread/byte/inode/deadline enforcement and full-scale outcome-free certification on exact Linux/Python 3.10 profile; neither CI configuration nor old feasibility report proves it |
| E06 | Independent decimal, cross-season, blank-only, null/blank/DGW/overflow/availability/full-distribution/U0 parity vectors plus full 9,999-reference-stream comparison; examples in this document are specified, not executed |
| E07 | Two-writer races, fences/no-read losers, exact adoption conflicts, crash/power-loss/durability and witness reconciliation at every transition; no proof inherited from the snapshot helper |

| Gate | Approved policy / concrete C3.0 candidate | Remaining decisions/evidence and effective status |
|---|---|---|
| B01 | Attributable pre-cutoff evidence; separate v3.1 adapter, unchanged legacy v2 gate; field-version contract and literal timeline vectors in 17.2 | D01, E01, E06; OPEN, real source scope unregistered/unauthorized |
| B02 | Development single operator without independent custody; signature/grant/clock/lease/witness proposal in 17.3 | D02, D05, D07, E02; OPEN, no grants or access; confirmation closed |
| B03 | EVAL_AUTH before combination computation; explicit pure v2 interfaces/truth table in 17.4 | D03/D04, E03/E06; OPEN, v1 remains synthetic-only; no private API workaround |
| B04 | Completed intervals for conditional admission, inclusive final gate; 17.5 gives accounting and exposes terminal-attestation gap | D04 including D04-F, E04/E07; OPEN, no certified freeze/result publication until terminal contract closes |
| B05 | Linux/Python 3.10 first; proposed numeric containment profile and full-scale gates in 17.6 | D05, E05 and terminal/isolation evidence; OPEN, no Linux/macOS resource guarantee |
| B06 | Minimal ASCII decimal, unchanged UTF-8 lexical serial order, blank-only inclusion; 17.7 literal identity and parity obligations | D06, E01/E03/E06; OPEN, decimal examples do not certify actual bridges or predictive performance |
| B07 | Deterministic cores separate from attempts/receipts; exact-byte adoption, acyclic identities and recovery matrix in 17.8 | D07/D04-F, E02/E04/E07; OPEN, no completed-result adoption or recovery guarantee |

Alternatives remain explicit: reject insufficient source scope rather than weaken
causality (B01); stay synthetic-only if custody/isolation cannot be proved (B02);
keep v1 rather than bypass EVAL_AUTH (B03); remain publication-blocked or obtain
an explicit prospective terminal-accounting amendment (B04/B07); deny unsupported
profiles rather than infer guarantees (B05); reject unresolved bridges rather
than infer identities (B06). Changing an approved direction requires section
3.2's prospective change control, not a fallback chosen during implementation.

### 17.10. Review handoff, validation boundary and stopping point

This proposal is independently reviewable as a **design with explicit blockers**,
not a complete implementation specification certified by its author. Review must
challenge especially the final-attestation accounting problem, conservative RSS
containment, source correction compatibility and hidden v2/RNG semantic changes.
A SAFE design verdict can mean it correctly stops; it does not close any evidence
gate or authorize code. All dependent implementation remains blocked pending
accepted contracts/parameters, a separately authorized scope and the relevant
staged evidence plan. Source-specific E01 remains a later separately authorized
registration gate even if synthetic infrastructure is eventually permitted.

Only this design file is modified. Validation for this task consists of complete
single-file diff inspection, whitespace and relative-link checks, Git HEAD/index/
status checks, preserved primary Task025 hashes, and byte/inventory/sanitization
checks on the external review bundle. These are document checks, not executed
bridge/parity tests, kernel tests, experiments or benchmarks. Full mathematical
and platform evidence is explicitly absent. Source/test/schema files are read
and copied as tracked review context, never imported or executed.

No protected/private/credential/data-directory contents were opened or enumerated.
Existing ignored operational artifacts were not touched by task actions; their
contents were deliberately not audited, so no claim about concurrent actors is
made. No grant, credential, prediction, outcome, research publication, confirmation
access, model promotion or FPL action was created or performed. Nothing was
staged, committed, pushed, submitted to Claude or changed on PR #2. The sanitized
review bundle is an external design-review artifact, not a research result.


### 17.11. Owner decision sheet — D01–D07 remain open

The owner-supplied independent Claude Sonnet 5 High review reported **SAFE /
IMPLEMENTATION READY: NO** for the preceding proposal, with one mandatory Medium
B05 memory/witness-accounting ambiguity. Section 17.6 clarifies that ambiguity;
this amendment has not itself been independently re-reviewed. No policy direction
from section 3.2 is reopened. Every recommendation below is a **proposal**, not
an owner decision, evidence of feasibility, or permission to implement or run.
The detailed parameter register in 17.9 remains binding; these summaries do not
approve unspecified algorithms, providers, clock bounds, budgets or schemas.

| Decision | Plain-language choice and recommendation (proposal) | Consequence and evidence needed | What remains blocked |
|---|---|---|---|
| D01 — Which historical evidence is sufficient? | Approve an explicit proof standard for when each exact corrected v3.1 value was available. Recommend requiring attributable pre-cutoff proof and keeping incompatible scope synthetic-only. | Some corrected values may be unusable; no earlier-value substitution or row deletion. E01/E06: exact version/correction/completion timelines, finalization and independent mapping/parity evidence. | Real-source registration/use and stronger causal claims; no historical data inspection is authorized by choosing the standard. |
| D02 — How will one owner control access safely? | Approve custody, role separation, signing/clock/lease/revocation parameters and recovery witness arrangements. Recommend the development-only profile in 17.3, subject to explicit acceptance of each parameter and proof; retain separate worker and authority privileges. | The owner/root remains trusted; this cannot prove independent custody or instantaneous revocation. E02/E05/E07: isolation, clock/replay/revocation failures and current witness/rollback reconciliation. | Grant infrastructure implementation pending accepted contracts and separate scope; all grant issuance, real reads and confirmation access remain unauthorized. |
| D03 — How will computation wait for permission? | Accept or revise the exact versioned pure component/combination interfaces. Recommend the proposed EVAL_AUTH split, retaining synthetic v1 unchanged and authorization-stop states outside completed results. | Requires a reviewed API/schema change, not a wrapper after computation. E03/E06: independent full-stream, membership, numerical and selection parity, plus no-combination-before-permit evidence. | C2 changes and real evaluation; policy approval alone does not approve schemas or prove parity. |
| D04 — What exactly counts as finished within budget? | Set the phase/shared-cost boundaries and resolve D04-F terminal attestation. Recommend keeping final completion blocked until an inclusive terminal contract is proved; any audit exclusion needs an explicit prospective amendment. | A final receipt cannot certify its own future write. E04/E07: final-write/fsync/ack timing, reader admission and crash evidence; owner must also resolve the 6-hour and enclosing 24-hour interpretations. | Certified prediction freeze, completed research publication and dependent outcome release; no reserved headroom may substitute for measurement. |
| D05 — Can the whole system fit the limits? | Approve exact Linux/Python 3.10 backend, numeric caps, process layout and witness accounting. Recommend distinct functional roles sharing the one combined helper allowance in 17.6; do not approve a topology or external-service exclusion without evidence. | The allowance is collective, not per role: the four allocations sum to 2,048 MiB. Feasibility is unknown. E05 plus E02/E07: continuous aggregate RSS/task/byte/time containment, witness attribution and full-scale synthetic certification. | Platform/profile implementation pending accepted scope, resource PASS and protected outcome access; no extra witness budget or macOS equivalence is implied. |
| D06 — How are player identities and forecasts preserved? | Approve the exact decimal/BIGINT bridge and complete adapter mapping. Recommend minimal ASCII decimal, current lexical serial order and rejection of ambiguous or blank-only missing bridges. | No name matching, float conversion or seeded-order repair. E01/E03/E06: official identity proof, independent decimal/full-stream vectors and null/blank/DGW/distribution/U0 parity. | Real bridge admission and adapter certification; handwritten examples are not executed proof. |
| D07 — What can be published, adopted or recovered? | Approve study-slot governance, exact-byte adoption/reproduction rules, durability/retention and recovery scope. Recommend separate immutable cores/attempts and exposure-preserving recovery; keep terminal admission coupled to D04-F. | Equal scores do not permit replacement or a new unseen trial. E02/E04/E07: competing-writer/fencing tests, exact provenance checks, crash/durability and witness reconciliation. | Completed adoption/publication and recovery guarantees; no verified offsite backup, confirmation authority or promotion follows. |

All D01–D07 and E01–E07 remain OPEN; B01–B07 remain implementation/evidence
gates. Evidence requires later separately authorized work in the relevant slice;
this sheet authorizes none. Synthetic infrastructure success would still not
establish predictive validation. The present amendment requires only document
checks and a short diff-only re-review; no new full review bundle is requested.
