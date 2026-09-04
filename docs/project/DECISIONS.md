# Decision index

This is an evidence index, not a retroactively invented ADR history. Commit
subjects establish implementation milestones, not the contents of independent
review conversations. The RFC contains the formal ADR-style decisions for web
architecture; its own status still says proposed.

| Decision / invariant | Repository evidence | Consequence |
|---|---|---|
| Raw bytes and derived artifacts stay immutable; hashes and UTC provenance travel with them | README raw/refresh sections; `pipeline.py`, `refresh.py`; `4979798`, `b151a3c` | Never repair a completed artifact by overwrite |
| Predictor chronology and missingness are explicit | `features.py`, `historical.py`, tests; `9288a03`, `110ad7e` | No target outcome leakage; no missing-as-zero shortcut |
| Historical data is restricted/pseudo-backtest, not perfect replay | README historical section; `historical_sources.py`; `110ad7e`, `b87cb69` | Finalized fixture assignment is not proof of pre-deadline knowledge; strict prior-GW AND kickoff-before-deadline guard remains |
| Frozen baseline precedes experiments; candidates and promotion gates are declared | `historical_backtest.py`, five `historical_*_experiment.py` modules; `3d3787d`, `c5269ed`, `391b043`, `af41e32`, `122d771`, `c8577dd` | No tuning after results, no automatic live-model promotion |
| Decision engine optimizes supplied projections, not football truth | `projection_provider.py`, `decision.py`; `6d5ce32` | `decision-engine-v2` is not xFP v0.2; optimum remains model-qualified |
| Editable manager evidence is distinct from public locked picks | `manager_state.py`, `editable_manager.py`; `60a843e`, `4d9dc6b` | Fresh squad/SP/bank/FT/chip confirmation required |
| Appearance-only eligibility is explicit; the operational runner selects it | `decision.py`, `transfer_decision.py`; `4d9dc6b`, `9253582` | General default stays strict; operational transfer path requires >=1 FT and zero transfer cost; preserve incomplete status and zero-minutes invariant |
| Reliability diagnostics do not change official recommendation | `decision_reliability.py`; `a76eecc`, `cc7a344` | No confidence score, automatic threshold veto, or replacement action |
| Persisted selection has one trusted structural validator | `decision.validate_decision_selection`; `e411f06` | Presentation must not duplicate rules or rerun optimization |
| GameweekDecision is the versioned presentation boundary | JSON Schema + `presentation/gameweek_decision.py`; `6aa889d` | Canonical validated payload, no second optimizer output |
| Engine v1 has semantic identities, observation-only cutoffs, and a human evidence gate | `operational_manifest.py`, `operational_runner.py`; `ff6cb07`, `512d224` | Explicit preparation/resume, server UTC clock, fail closed |
| Prospective action and later completion are separate immutable records | `decision_journal.py`, adversarial tests; `3678736` | Backfill stays historical; consuming a journal re-verifies evidence |
| DecisionDiff reports structure, not causes | `decision_diff.py` + schema/tests; `05038b1` | Same scope, ordered trusted runs; no optimization/reliability rerun |
| Offline tests must execute without private/generated files | `tests/fixtures/README.md`, isolation test, CI; `aab7432`, `c0cdfb5` | Committed test copies are not production recovery artifacts |
| Engine is authority; app/browser consume verified output | RFC ADR-026A-01–05/10; `5ffbb4f`, `95dbbb0`; status detail below | One-way module dependency, local read-only API, no app/browser FPL semantics |
| Research/population evidence stays separate from production | RFC sections 14–16 and ADR-026A-09 | Consent-gated export and reviewed release are designs; no runtime feedback path |

## Historical presentation lesson (human-confirmed context and policy)

**HUMAN-APPROVED PROJECT POLICY**, confirmed on 2026-09-04. A conversational
presentation layer previously produced an invalid/incomplete selection despite
legal optimizer output. This incident is human-confirmed history, not an event
proven by Git. The durable lesson is that squad legality, starting XI, bench
accounting, captain, vice-captain and transfer semantics must originate from
trusted engine artifacts and deterministic validation. Human overrides must
pass the same legality boundary. LLMs may explain validated decisions, not
construct them. The repository-grounded response is the
[persisted-selection validator](../../src/fpl_decision_engine/decision.py) and
[GameweekDecision builder](../../src/fpl_decision_engine/presentation/gameweek_decision.py);
these establish current mechanisms, not the incident's historical attribution.

## Web RFC decision status

The [RFC](../rfcs/0026a-web-product-architecture.md#25-adr-style-decisions) says
all its ADRs are proposed pending independent review. Human confirmation of
project policy does not automatically accept every RFC design. IMPLEMENTED
below describes observable code, not blanket acceptance of the entire ADR.

| RFC decision | Status at `95dbbb0` | Boundary / consequence |
|---|---|---|
| ADR-026A-01: engine artifacts are authority | IMPLEMENTED for decision reads | Public reader delegates existing validation; no second app/browser optimizer |
| ADR-026A-02: React/TypeScript/Vite | IMPLEMENTED stack | Static client; no chosen UX paradigm; current view omits RFC-envisaged reliability/model caveats |
| ADR-026A-03: separate Python/FastAPI app | IMPLEMENTED | One-way dependency; import tests enforce a specific forbidden list, not every possible bypass |
| ADR-026A-04: REST envelope and canonical artifact | IMPLEMENTED for health/decision API | Server verifies; browser checks markers; TypeScript payload types remain manual; broader routes are PROPOSED |
| ADR-026A-05: immutable indexed access | IMPLEMENTED local explicit-ID/hash checks; PARTIAL RFC design | Chain-wide read-once, object storage and verified-by-hash caching remain unimplemented |
| ADR-026A-06: separate app-user and FPL-entry identities | PROPOSED | Public entry ID does not prove control; season-scoped connections and ownership verification are not implemented |
| ADR-026A-07: fresh manager evidence | IMPLEMENTED CLI evidence/deadline gate; PROPOSED web workflow | Manual evidence and selling prices are required; OCR is advisory in the proposed design |
| ADR-026A-08: PostgreSQL with authenticated writes | PROPOSED; no app DB today | No production SQLite interim; does not authorize adding a database now |
| ADR-026A-09: separate one-way research plane | PROPOSED service; HUMAN-APPROVED isolation/consent policy | No deployed export/consent service or runtime research feedback; user records are not automatic training data |
| ADR-026A-10: fail closed; no authoritative latest | IMPLEMENTED current explicit-ID read path | Local principal is not authentication; broader authenticated commands/states remain PROPOSED |

## Experiment status: preserve the distinction between rules and results

The live source remains xFP v0.1. Experiment source and synthetic gate tests
establish the protocol, not the numerical result of every historical run.

- Task009: M0–M3 minutes experiment; Task010: S0–S3 attacking-rate experiment;
  Task011: C0–C2 calibration; Task012: F0–F2 opponent strength. README defines
  their development/conditional-holdout gates. Their full production result
  tables are not committed; exact winning/failed gates and whether a given
  holdout was opened require the authorized run manifest, not inference.
- Task018D's [committed metadata fixture](../../tests/fixtures/artifact_metadata/task018d-experiment-manifest.json)
  records `development_pass=false`, 19/21 gates passed, `holdout_evaluated=false`,
  no holdout input files read, and no live-model modification. Its regression
  checks the fixed metadata hash and development-only scope without real metric
  tables. It does not prove the continued integrity of a private production copy.
- Earlier conversation reports describe negative experiment outcomes. Treat
  unrecovered exact result packages as **REQUIRES HUMAN CONTEXT**, not permission
  to rerun candidates or inspect sealed performance.

Negative experiments remain negative. A failure of particular candidates is not
proof that all priors, calibration, minutes models, or opponent modelling fail.
New evidence requires a new preregistered question, not a revised old result.
Holdout permissions are experiment-specific: 2024/25 is development for Task018D
but was conditional holdout for earlier experiments; 2025/26 is sealed for
Task018D. Never treat a season's availability as blanket performance access.

## Future decision entries

Add date, status (proposed/accepted/rejected/superseded), evidence links, owner
approval and consequences when a new decision is reviewed. Preserve the earlier
entry when superseded. Do not promote a roadmap idea merely by listing it here.
