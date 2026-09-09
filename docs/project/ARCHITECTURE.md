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

PUBLIC COMPLETION CONTROL (collection orchestration, not decision authority)
two stable exact public probes -> target lock / restart reconciliation
    -> at most one coherent refresh -> validated immutable realized receipt
    -> optional exact pre-deadline prediction -> validated evaluation receipt
         -X-> no prediction generation, decision, journal or manager action

OFFLINE SCHEDULER VALIDATION (outside engine and decision authority)
Task028D synthetic worker -> generated temporary LaunchAgent package
    -> future owner-authorized exact-label lifecycle -> sanitized drill evidence
         -X-> no live monitor, FPL/private data, production installation or schedule

RESEARCH: historical*.py + separately stored historical/experiment artifacts
pinned archives -> causal features -> frozen baseline / preregistered experiments
                    -X-> no automatic promotion or runtime feedback to production

LOCAL/PRIVATE RECOVERY (outside decision authority and outside Git)
authorized quiescent data + recorded clean Git revision
    -> read-only inventory -> age-encrypted immutable checkpoint + Git bundle
    -> local verify/restore -> future exact-version B2 + disconnected copy
         -X-> restore success alone does not validate prospective or semantic truth
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
| Collection, completion control and raw immutability | `pipeline.py`, `official_data.py`, `refresh.py`, `completion_monitor.py`, `tls.py`; `test_pipeline`, `test_refresh`, `test_completion_monitor`, `test_gameweek_data` |
| Typed data, temporal features, predictions | `transform.py`, `gameweek_transform.py`, `features.py`, `predictions.py`; corresponding tests |
| Supplied projection boundary / legal optimization | `projection_provider.py`, `decision.py`, `transfer_decision.py`; `test_decision`, `test_decision_selection`, `test_transfer_decision` |
| Locked public vs editable private state | `manager_state.py`, `manager_decision.py`, `editable_manager.py`; manager/editable tests |
| Diagnostics / realized evaluation | `decision_reliability.py`, `evaluation.py`; `test_decision_reliability`, `test_evaluation` |
| Contracts and execution | `operational_manifest.py`, `operational_runner.py`; `test_operational_manifest`, `test_operational_runner` |
| Presentation / human record / comparison | `presentation/gameweek_decision.py`, `decision_journal.py`, `decision_diff.py`; corresponding tests |
| Historical research | `historical.py`, `historical_sources.py`, `historical_backtest.py`, `historical_*_experiment.py`; historical tests |

Operational tooling outside the engine package includes
[`scripts/completion_monitor_schedule_drill.py`](../../scripts/completion_monitor_schedule_drill.py).
Its focused offline coverage is `tests/test_completion_monitor_schedule_drill.py`.

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

The [completion monitor](TASK028B_COMPLETION_MONITOR.md) is a one-shot control
around public official-data collection. It probes bootstrap and fixture status
twice, accepts only an unchanged exact semantic digest at least 15 minutes apart,
and then locks the explicit season/GW target. Existing valid receipts are
reconciled deterministically after interruption; otherwise the monitor performs
at most one coherent refresh and revalidates the manifest, raw manifests, exact
history files, hashes, row counts and source coherence before publishing a
realized receipt. Optional evaluation uses the shared public realized-snapshot
validator and only an explicitly supplied exact pre-deadline prediction. Receipt
integrity failures fail closed into scoped review/archive/reset operations.
There is no polling loop or installed scheduler, and this path has no authority
to generate predictions or create decisions, journals or manager actions.

[Task028C](TASK028C_SYNTHETIC_SCHEDULING_DRILL_SPEC.md) separates existing
monitor proof from scheduler proof. [Task028D](TASK028D_COMPLETION_MONITOR_SCHEDULING_DRILL.md)
implements an offline standard-library harness that prepares one owner-only
synthetic package, validates exact committed inputs, drives an exact-label
temporary LaunchAgent lifecycle only behind an explicit execution flag, and
verifies/sanitizes the resulting evidence. Automated tests replace `launchctl`
with a fake runner and use no FPL or private data. No actual lifecycle command,
scheduler installation or production monitor schedule has run; those remain
separate authorization and evidence gates.

## Application boundary today

[Public reader](../../src/fpl_decision_engine/trusted_artifact_reader.py) delegates
to the existing completed-evidence reader and returns canonical decision bytes.
[Read facade](../../src/fpl_decision_app/read_facade.py) authorizes before
resolution, checks indexed final-manifest hash and returned identity/hash, and
returns one canonical payload. The facade supplies its already-read final
manifest bytes to a request-scoped snapshot; downstream completed-evidence and
GameweekDecision validation use stable private copies rather than reopening the
original paths. Snapshot capture rejects symlinks, hardlinks, non-regular files
and files that change during capture. It does not generate decisions.

[OpenAPI](../../contracts/api/v1/openapi.json) currently exposes only health and
explicit decision reads. [GameweekDecision / DecisionDiff schemas](../../src/fpl_decision_engine/presentation/schemas/)
govern artifact payloads independently of API v1. Journal/manifests instead use
typed exact-field validation. Unknown shapes/versions cannot be guessed into
compatibility. The public trusted-reader seam converts the authoritative
GameweekDecision schema into collision-checked OpenAPI components. Browser code
imports types generated from that checked document, and CI refuses stale
generated output. This is compile-time drift detection, not browser-side runtime
validation. Never migrate immutable bytes in place.

The [local authorization policy](../../src/fpl_decision_app/authorization.py)
assigns no client identity: the API supplies the same local principal for every
request. Run it on **localhost only**, never expose this mode on a network.
Both `FPL_APP_ARTIFACT_ROOT` and `FPL_APP_ARTIFACT_INDEX` are needed for indexed
reads; neither configured means an empty store. Health success is not proof of
artifact readiness. See [setup](../../README.md#webapplication-skeleton).

Canonical artifact verification occurs on the server. The browser client checks
trust/version envelope markers, not full artifact schemas or hashes. OpenAPI
snapshot, generated-type and import/bundle tests enforce specific checks; they
are not exhaustive proof of semantic isolation. The application import
guard includes `decision_journal` and expands `from package import member` forms;
it remains a test-time denylist rather than a runtime import boundary.
`DecisionView` does not yet display RFC-envisaged reliability diagnostics or model
caveats; documenting this gap authorizes no frontend implementation.

There is no app database, command worker,
research export or multi-tenant service yet. Request-scoped snapshots protect
the current explicit decision-read chain; they do not provide process-wide file
immutability, an object store, verified-by-hash caching or a general rule for
unrelated engine reads.

## Recovery boundary today

Recovery tooling supplies operational support only; engine artifacts retain
decision authority.
[Task027C](TASK027C_LOCAL_BACKUP_READINESS.md) protects private root paths from
ordinary staging and inventories opaque bytes without interpreting them.
[Task027D](TASK027D_ENCRYPTED_CHECKPOINTS.md) creates manifest-first,
age-encrypted no-overwrite checkpoints containing the selected private tree and
a clean recorded-revision Git bundle, then provides explicit verify and restore
operations. The Task027E1 staged-content guard scans the complete Git index for
age identities and owner-supplied exact private values before commit.

These controls do not establish that the selected evidence is complete or true.
A restored digest proves byte identity only; trusted readers must still validate
the restored engine chain, and missing pre-deadline evidence remains missing.
No provider upload, production recovery identity, disconnected copy or real
restore drill has passed. Provider identifiers, credentials, private manifests,
object receipts and custody locations remain outside the repository.
