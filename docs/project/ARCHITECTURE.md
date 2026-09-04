# Architecture map

**Engine = authority. Applications/agents/LLMs = consumers/assistants.**
LLMs must not construct FPL decision semantics. The [web RFC](../rfcs/0026a-web-product-architecture.md)
defines direction and future seams; the map below describes implemented code.

```text
TRUSTED OPERATIONAL PATHS within src/fpl_decision_engine
official FPL bytes -> refresh / raw -> typed clean Parquet
    -> frozen player × target-fixture features -> xFP v0.1 fixture/GW artifacts
    -> projection provider + verified editable manager evidence
    -> legal ROLL / one-transfer candidates -> fixed-squad XI/C/VC optimizer
    -> separate reliability diagnostics (no change to official action)
    -> GameweekDecision builder
         -> trusted persisted-selection validation + candidate/provenance checks
    -> immutable final operational manifest and canonical decision contract
         |                         |
         +-> journal -> outcome    +-> ordered same-scope DecisionDiff
         |
    public trusted_artifact_reader (existing validation, not another engine)
         |
APPLICATION: src/fpl_decision_app
authorization -> explicit-ID store/index -> read facade -> /api/v1 envelope
         |
PRESENTATION: web/
React / TypeScript / Vite -> server-verified payload or fail-closed error

RESEARCH: historical*.py + separately stored historical/experiment artifacts
pinned archives -> causal features -> frozen baseline / preregistered experiments
                    -X-> no automatic promotion or runtime feedback to production
```

Validation is not a last-minute UI step: each stage validates its inputs.
`presentation/` inside the engine is a trusted contract-construction boundary;
the browser presentation layer is not. GameweekDecision validates persisted
selection using `decision.validate_decision_selection`, not `optimize_xi`.
Transfer proof uses existing trusted mechanisms/candidates separately.

## Code navigation

Package location does not establish production authority: `historical*.py` and
experiment modules also live in the engine package. Their research role and
promotion gates remain distinct from operational execution.

All paths below are under [src/fpl_decision_engine](../../src/fpl_decision_engine/).

| Concern | Sources / focused tests |
|---|---|
| Collection and raw immutability | `pipeline.py`, `official_data.py`, `refresh.py`, `tls.py`; `test_pipeline`, `test_refresh`, `test_gameweek_data` |
| Typed data, temporal features, predictions | `transform.py`, `gameweek_transform.py`, `features.py`, `predictions.py`; corresponding tests |
| Supplied projection boundary / legal optimization | `projection_provider.py`, `decision.py`, `transfer_decision.py`; `test_decision`, `test_decision_selection`, `test_transfer_decision` |
| Locked public vs editable private state | `manager_state.py`, `manager_decision.py`, `editable_manager.py`; manager/editable tests |
| Diagnostics / realized evaluation | `decision_reliability.py`, `evaluation.py`; `test_decision_reliability`, `test_evaluation` |
| Contracts and execution | `operational_manifest.py`, `operational_runner.py`; `test_operational_manifest`, `test_operational_runner` |
| Presentation / human record / comparison | `presentation/gameweek_decision.py`, `decision_journal.py`, `decision_diff.py`; corresponding tests |
| Historical research | `historical.py`, `historical_sources.py`, `historical_backtest.py`, `historical_*_experiment.py`; historical tests |

## Operational identity and evidence

Phase 1 (`prepare_gameweek`) refreshes an explicit GW, proves unique official
`is_next`, freezes the deadline/features/predictions, and stops at
`BLOCKED: VERIFIED_MANAGER_STATE_REQUIRED`. Phase 2 (`resume_gameweek`) consumes
the exact preparation plus fresh verified manager input. It never switches to
`latest`. The server clock supplies manager verification time; no selling-price
inference or carried-forward editable state is allowed. The runner explicitly
uses experimental `appearance_only_allowed`; its transfer evaluator requires
at least one free transfer and zero current transfer cost. Numeric incomplete
admission still requires expected minutes exactly zero. The objective is
starter projections plus one extra captain copy, without substitution
simulation or vice-captain fallback valuation.

`operational_manifest.canonical_json_bytes` sorts keys, rejects non-finite
numbers, and produces compact UTF-8 for SHA-256 semantic IDs. Preparation ID
binds contract version, GW, deadline, refresh hash; decision ID binds preparation
and manager-state hash. Processing time and paths are not independent ID inputs.
Evidence cutoff is the latest accepted observation time, not generation time;
evidence and finalization must precede the deadline. Exact-field manifests
reference outputs, not duplicate their recommendation values.

Completed artifacts live under
`data/operations/fpl/<season>/gameweek=<N>/<preparation_id>/decisions/<decision_id>/`.
Safe identical reuse and conflict refusal preserve immutability. Outcome
consumption re-anchors a journal through `_load_completed_evidence`; a
self-consistent standalone JSON is not enough. DecisionDiff also validates
preparation directories, ordered sides and same season/GW/deadline.

## Application boundary today

[Public reader](../../src/fpl_decision_engine/trusted_artifact_reader.py) delegates
to the existing completed-evidence reader and returns canonical decision bytes.
[Read facade](../../src/fpl_decision_app/read_facade.py) authorizes before
resolution, checks indexed final-manifest hash and returned identity/hash, and
returns one canonical payload. It does not generate decisions.

[OpenAPI](../../contracts/api/v1/openapi.json) currently exposes only health and
explicit decision reads. [GameweekDecision / DecisionDiff schemas](../../src/fpl_decision_engine/presentation/schemas/)
govern artifact payloads independently of API v1. Journal/manifests instead use
typed exact-field validation. Unknown shapes/versions cannot be guessed into
compatibility. Never migrate immutable bytes in place.

The [local authorization policy](../../src/fpl_decision_app/authorization.py)
assigns no client identity: the API supplies the same local principal for every
request. Run it on **localhost only**, never expose this mode on a network.
Both `FPL_APP_ARTIFACT_ROOT` and `FPL_APP_ARTIFACT_INDEX` are needed for indexed
reads; neither configured means an empty store. Health success is not proof of
artifact readiness. See [setup](../../README.md#webapplication-skeleton).

Canonical artifact verification occurs on the server. The browser client checks
trust/version envelope markers, not full artifact schemas or hashes. OpenAPI
snapshot and import/bundle tests enforce specific checks; they are not exhaustive
proof of contract drift prevention or semantic isolation. The application import
guard omits `decision_journal`, although current app imports use the public reader.
`DecisionView` does not yet display RFC-envisaged reliability diagnostics or model
caveats; documenting this gap authorizes no frontend implementation.

There is no app database, command worker,
research export or multi-tenant service yet. The read-bytes-once aspiration is
not fully implemented across the chain; see [handoff follow-ups](CURRENT_HANDOFF.md#unresolved-review-follow-ups).
