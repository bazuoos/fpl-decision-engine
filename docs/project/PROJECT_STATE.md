# Project state

Checkpoint: 2026-09-04, commit `95dbbb0ba069bc7011fc75b72c8213325c403227`.
Start with [CURRENT_HANDOFF](CURRENT_HANDOFF.md); this pack is navigation and
continuity context, not a replacement for code, contracts, or frozen evidence.

## Purpose and maturity

Improve FPL decision-making through explainable projections, legal optimization,
and auditable pre-deadline evidence. A working, tested Engine v1 operational
pipeline and local read-only web skeleton exist. This is not yet a public,
authenticated web service or a validated full-FPL prediction model.

“Trustworthy Engine” describes provenance, legality, deterministic processing,
and fail-closed boundaries. It does **not** certify predictive accuracy or make
the selected action the objectively best FPL transfer.

## Capability map

| Area | Implemented and trusted for | Important limit |
|---|---|---|
| Official data | Immutable raw bytes, typed Parquet, coherent resumable refresh | Live collection needs network; completion and hashes must validate |
| Features / xFP | Frozen inputs and explicit missingness; xFP v0.1 | Appearance + goals + assists only; unstable early samples |
| Optimization | Legal deterministic squad/XI/C/VC; zero-or-one-free-transfer comparison | Single GW, no chips/hits/multi-GW or future-transfer valuation |
| Reliability | Provenance and 11 diagnostic sensitivity views | Not confidence, a veto, or a replacement recommendation |
| Engine v1 operations | Two phases, explicit IDs, manager gate, UTC deadlines, immutable completed results | Fresh verified editable manager evidence is indispensable |
| Journal / Diff | Separate human-action record; trusted same-scope structural comparison | Outcome v1 proves GW completion, not points/counterfactual performance; diff is not causal |
| Web | Authorization seam, explicit-ID verified decision reads, canonical payload rendering | Local single-user mode only; no real auth, uploads, commands, or research service |

Primary guide: [README](../../README.md). Implementation map:
[ARCHITECTURE](ARCHITECTURE.md). Product constraints:
[FPL_PRODUCT_PHILOSOPHY](FPL_PRODUCT_PHILOSOPHY.md).

## Versions and production/research separation

- Live model: `predictions.py` uses `MODEL_VERSION="v0.1"`; provider model ID is
  `xfp_v01`, scope `modeled_components_only`. Package version `0.1.0`, Engine v1,
  and optimizer `decision-engine-v2` are different version namespaces.
- `strict_complete_only` remains the general default. The explicit experimental
  `appearance_only_allowed` policy preserves incomplete status; numeric
  incomplete admission is guarded by expected minutes exactly zero. Null and
  non-finite projections are not admitted. The operational runner explicitly
  selects `appearance_only_allowed` for its one-transfer evaluation. That path
  requires at least one free transfer and zero current transfer cost; it is not
  a general ROLL-only fallback for a manager with no free transfers. See
  [runner](../../src/fpl_decision_engine/operational_runner.py) and
  [transfer evaluator](../../src/fpl_decision_engine/transfer_decision.py).
- GameweekDecision and DecisionDiff are schema `1.0.0`; preparation/final
  manifests and journal/outcome contracts each have their own v1 identifiers.
- Historical-v2 supports the frozen baseline; historical-v3 and corrected
  historical-v3.1 remain separate immutable versions. Historical-v3.1 covers
  2023/24–2025/26 and is labelled `restricted_pseudo_backtest`.
- Tasks009–012 and 018 are isolated preregistered experiments, not live model
  upgrades. Do not infer promotion from a module name containing `v02`.
  [DECISIONS](DECISIONS.md) identifies what result evidence is committed.
- Research/user-population export is an RFC design, not a deployed service.
  No research signal may enter production without preregistration, validation,
  independent review, and an explicit approved version change.

## Operational and web limits

The optimizer maximizes starter projections plus one extra captain copy. It
neither simulates substitutions nor values vice-captain fallback. ROLL wins an
objective tie; future transfer flexibility is not valued in the objective.

The web skeleton must run on **localhost only**. Every HTTP decision request is
assigned the same local principal; the authorization seam does not authenticate
clients or prove FPL team ownership. Set both `FPL_APP_ARTIFACT_ROOT` and
`FPL_APP_ARTIFACT_INDEX` for explicit indexed reads; setting neither selects an
empty store, and setting only one fails configuration. `/api/v1/health` reports
API readiness even with an empty store, not decision-artifact availability or
integrity. Follow the [local setup](../../README.md#webapplication-skeleton).

Verification is server-side. The browser checks trust/version envelope markers
but does not independently reproduce engine hash or full schema validation.
The current `DecisionView` renders action, selection and identity fields, but
not the reliability diagnostics or model caveats envisaged by the RFC. This is
a delivery gap, not authorization to implement it.

## Validation evidence and procedure

**Repository-established at this review:** HEAD `95dbbb0`, branch `main`, 109
tracked files, 411 Python test methods and four frontend tests in source. The
CI workflow configures Python 3.10 and Node 22. Counts and workflow configuration
do not prove test execution or CI success.

**Reported by the original continuity-pack authoring session, not independently
rerun or verified by this review:** on 2026-09-04, 411 Python tests passed with
zero skips in 126.303 seconds under Python 3.14.5; four frontend tests,
TypeScript, production build, browser boundary, document-link and whitespace
checks passed. That session reported all 109 tracked files byte-identical and
[CI run 33848602481](https://github.com/bazuoos/fpl-decision-engine/actions/runs/33848602481)
successful at 2026-09-04 07:33:04 UTC. Local execution logs are not linked here;
these historical reports do not establish current or future checkout health.
The documentation remediation intentionally changes README discoverability;
it does not claim that all 109 tracked files remain identical after that edit.

When installation/test execution is authorized, use a virtualenv and run:

```bash
git status --short
git log -5 --oneline
python -m pip install --editable .
python -m unittest discover -s tests
git diff --check
cd web
npm ci
npm test
npm run typecheck
npm run build
npm run check:boundary
```

Python >=3.10 is declared. Tests use offline inputs; dependency installation
needs network. No generated `data/` is required. Installation/builds write local
files, so these are not read-only onboarding checks. Do not run operational
refresh or experiment commands merely to bootstrap a session. Plain Git diff
and whitespace checks omit untracked documents; review their complete contents
and check their links/whitespace separately before staging named files.

## Known historical documentation discrepancies

The RFC remains proposed and its original no-web/API findings predate Task026B.
Only health and decision read routes exist; do not infer diff/journal routes
from the RFC or CLI. README's Task023A “future runner” and older transfer
exclusions describe earlier task scopes; later modules implement both paths.
Python locking and chain-wide read-once ambitions remain undelivered. See the
[decision status index](DECISIONS.md#web-rfc-decision-status) and
[deferred follow-ups](CURRENT_HANDOFF.md#unresolved-review-follow-ups).

## Reproducibility is not disaster recovery

The committed tests use offline fake responses and explicit test-only fixtures;
they do not require a developer's generated datasets. See
[fixture provenance](../../tests/fixtures/README.md). Fixed fixture hashes prove
those copies, not the current health of private production artifact stores.

CI installs Python dependencies from declared ranges, not a Python lockfile.
JavaScript has `web/package-lock.json` and `npm ci`. A reproducible validation
procedure therefore exists, but identical future Python dependency resolution
is not guaranteed. File-fsync plus atomic publication is not a full power-loss
durability or backup guarantee; see README refresh durability wording.

### Recovery boundaries and safe sequence

1. Restore a verified repository revision; inspect status/log before work.
2. Install using the [validation commands](#validation-evidence-and-procedure) and run
   all tests. Never repurpose test fixtures as recovered production evidence.
3. Separately obtain an authorized private backup of required raw/clean/features,
   predictions, manager evidence, operations, journals/diffs, and experiment
   manifests/artifacts. Preserve original bytes and referenced paths; inspect
   path assumptions before relocation. Do not rewrite manifests to make a move
   appear valid.
4. Verify the required chain with existing trusted readers before using any
   recovered decision. A digest or an index alone is not evidence recovery or
   authentication; the referenced bytes and trusted source must exist.
5. Missing pre-deadline evidence cannot be recovered by fetching today's data,
   rebuilding under an old ID, or inventing timestamps. Fail closed and ask the
   human which operations remain possible. Historical backfill has separate
   evidence rules and cannot masquerade as prospective evidence.

Backup/deployment ownership, backup location, custodian, encryption/access,
retention, restore procedures and drill, private evidence inventory, and
recovery objectives: **REQUIRES HUMAN CONTEXT**. This documentation creates no
backup and does not attest to private artifact integrity. Most generated roots
are ignored, but `data/operations/` is not currently covered by `.gitignore`;
explicit staging is essential. Do not add private data to Git to solve recovery.
