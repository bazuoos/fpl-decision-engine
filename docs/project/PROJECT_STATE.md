# Project state

Implementation checkpoint summarized: 2026-09-10, commit
`7484484b7b3daccc814010af786f595608ce3e94`. Identify this document's own
revision and any later work from Git history.
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
| Official data | Immutable raw bytes, typed Parquet, coherent resumable refresh; stable one-shot completion monitoring; reviewed local scheduling controller | Live collection needs network; two reviewed GW4 plans are stale and inactive, so no current plan is installed or activated |
| Features / xFP | Frozen inputs and explicit missingness; xFP v0.1 | Appearance + goals + assists only; unstable early samples |
| Optimization | Legal deterministic squad/XI/C/VC; zero-or-one-free-transfer comparison | Single GW, no chips/hits/multi-GW or future-transfer valuation |
| Reliability | Provenance and 11 diagnostic sensitivity views | Not confidence, a veto, or a replacement recommendation |
| Engine v1 operations | Two phases, explicit IDs, manager gate, UTC deadlines, immutable completed results | Fresh verified editable manager evidence is indispensable |
| Journal / Diff | Separate human-action record; trusted same-scope structural comparison | Outcome v1 proves GW completion, not points/counterfactual performance; diff is not causal |
| Web | Authorization seam, explicit-ID verified decision reads, canonical payload rendering | Local single-user mode only; no real auth, uploads, commands, or research service |
| Recovery tooling | Private-path staging guards, read-only inventory, encrypted checkpoint create/verify/restore | No production key, verified remote/disconnected copy or real restore drill |
| Private evidence/context | Content-addressed explicit source capture, immutable human-context records, separated comparison layers | Local and outside decision authority; provenance/currentness stay explicit; no offsite copy |
| Repository CI | Full-SHA-pinned GitHub Actions on Node 24 runtimes; weekly action-only Dependabot proposals | Dependency ranges remain incompletely locked; update PRs still require review and CI |

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

Task028B adds an explicit one-shot `monitor-completion` operation for one season
and gameweek. It accepts completion only after two exact semantic public probes
at least 15 minutes apart, uses a target-local lock, reconciles interrupted runs,
performs at most one coherent refresh and validates immutable realized/evaluation
receipts. Optional evaluation requires an explicit exact pre-deadline prediction;
the monitor cannot generate, discover or substitute one. It does not create
decisions, journals or manager actions. No polling loop or scheduler is installed.

Task028C defines a synthetic macOS scheduling drill, and Task028D implements its
offline LaunchAgent harness outside the engine package. The harness prepares,
validates and sanitizes synthetic evidence; its automated tests replace the
service-manager lifecycle with a fake runner. Only an explicitly acknowledged
`run` command can invoke `launchctl`. On 2026-09-09, an owner-authorized local
drill ran the complete and review-required synthetic scenarios from `33fa48c`;
both exact labels were removed, sanitized evidence was independently reviewed,
and the reviewer reported SAFE. This is local operational evidence, not a
committed scheduler or production proof. No scheduler was installed. Rendering
a candidate production plist neither installs nor authorizes it.

Task028E defines the production scheduling boundary and Task028F implements the
standard-library controller outside the engine package. It consumes Task028B's
typed outcome only after exact plan, code, environment, repository and path
preflight. One immutable terminal marker makes later invocations network-inert;
activation and exact-label deactivation remain explicit owner operations. Two
GW4 Task028G plans were later prepared locally and independently reviewed; one
was realized-only and one bound the exact pre-deadline GW4 prediction. Neither
was installed or activated, and both now fail exact-commit preflight after later
repository work. The controller cannot operate while the Mac is powered off and
makes no continuous-availability claim.

Task029A defines evidence provenance and human-context separation; Task029B
implements explicit content-addressed private source capture, immutable context
records and a comparison view that names historical human context, trusted
engine references and current evidence separately. These tools live outside the
engine and have no decision, xFP, optimization, reliability or journal authority.
One later owner-authorized local source capture and seven historical-backfill
context records were verified as described in the sanitized
[Task029C record](TASK029C_PRIVATE_EVIDENCE_CAPTURE_AND_CONTEXT_POPULATION.md).

## Validation evidence and procedure

**Repository-established at this review:** HEAD `7484484`, branch `main`, 149
tracked files and four frontend tests in source. The CI workflow configures
Python 3.10, Node 22 and checksum-pinned age 1.3.1 installation. Checkout,
setup-python and setup-node are pinned by full commit SHA to releases that use
Node 24 action runtimes.

At this exact commit, [CI run 34480636762](https://github.com/bazuoos/fpl-decision-engine/actions/runs/34480636762)
passed 582 Python tests, four frontend tests, generated-contract freshness,
TypeScript, production build, browser dependency-boundary and whitespace checks.
Task030A's independent review verified the release provenance and full SHA pins;
the former Node 20 runtime warning did not recur after the upgrade. Task030B's
independent review verified the action-only Dependabot scope and reported SAFE.
[Dependabot run 34480644733](https://github.com/bazuoos/fpl-decision-engine/actions/runs/34480644733)
then completed successfully and found the pinned actions current. This proves
GitHub accepted this configuration, not that future update pull requests are
safe to merge. Historical CI success does not establish future checkout health,
predictive validity, private-evidence truth or a real installed production
schedule.

When installation/test execution is authorized, use a virtualenv and run:

```bash
git status --short
git log -5 --oneline
python -m pip install --editable .
python -m unittest discover -s tests
git diff --check
cd web
npm ci
npm run check:contracts
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
Python dependency locking remains undelivered. The current explicit application
decision-read chain now captures original artifact paths once per request and
validates private snapshot copies; this is not process-wide file immutability,
object storage or a general replacement for every path read elsewhere. The
application forbidden-import guard includes `decision_journal` and reconstructs
fully qualified members for `from package import member`; it remains a test-time
specific denylist rather than a runtime import mechanism. The authoritative
GameweekDecision JSON Schema is converted into the checked OpenAPI document, and
browser consumers import exact-pinned, generated TypeScript types directly.
CI detects changes between the application, checked OpenAPI and generated file;
these compile-time types do not add browser-side runtime schema validation. See
the [decision status index](DECISIONS.md#web-rfc-decision-status) and
[deferred follow-ups](CURRENT_HANDOFF.md#unresolved-task026b-follow-ups).
The completion monitor is implemented as a one-shot command, so the earlier
Task028A future-tense design wording should be read with its implementation guide
and current code. Task028C/D now supply a reviewed scheduling-drill design and
offline harness. The temporary synthetic `launchd` drill subsequently passed
local execution and independent sanitized-evidence review. Task028E/F now supply
the reviewed production-scheduling design and inert controller; older statements
that production scheduling design is still future work are obsolete. Two local
Task028G plans were prepared and reviewed but never installed or activated; both
are now stale by exact revision. Automatic decision work remains unimplemented
and unauthorized. Task029A/B add private provenance/context mechanisms; they do
not promote human reasoning into trusted engine evidence.

## Current local public-data evidence

**Local operational state observed during the authorized 2026-09-08 session,
not committed repository state:** the official refresh at
`20260908T120547.189577Z` records Gameweek 3 as finished and data-checked. A
complete xFP v0.1 evaluation manifest at
`data/evaluations/fpl/2026-27/gameweek=3/v0.1/20260908T121934.700265Z/`
binds the pre-deadline prediction snapshot `20260903T061943.538960Z` to that
realized snapshot and evaluates 626 of 654 player rows.

The evaluation is model-wide realized evidence, not a prospective manager
decision, human-action record or model-promotion decision. One early-season
gameweek is insufficient for tuning or promotion. The ignored local data is not
included in this continuity diff and has **NO VERIFIED OFFSITE BACKUP**. A
prospective GW3 journal cannot be recreated honestly after its deadline; none
was created by the refresh or evaluation.

**Additional local operational state observed on 2026-09-10:** one explicitly
selected current manager source was captured with Task029B and verified against
the source hash in the current verified-manager-evidence artifact. Seven
historical-backfill human-context records were created and verified. This is a
sanitized operational report, not committed private evidence or proof of source
time/currentness; see
[Task029C](TASK029C_PRIVATE_EVIDENCE_CAPTURE_AND_CONTEXT_POPULATION.md). None of
these records is prospective or an engine input.

## Reproducibility and current recovery state

The committed tests use offline fake responses and explicit test-only fixtures;
they do not require a developer's generated datasets. See
[fixture provenance](../../tests/fixtures/README.md). Fixed fixture hashes prove
those copies, not the current health of private production artifact stores.

CI installs Python dependencies from declared ranges, not a Python lockfile.
JavaScript has `web/package-lock.json` and `npm ci`. A reproducible validation
procedure therefore exists, but identical future Python dependency resolution
is not guaranteed. File-fsync plus atomic publication is not a full power-loss
durability or backup guarantee; see README refresh durability wording.

Task027C committed root ignore rules for `data/` and `.private-recovery/`, a
full-index private-path guard, and a read-only inventory tool. Task027D committed
age-encrypted checkpoint create/verify/restore tooling that packages a private
manifest and a Git bundle, detects source changes and validates restored bytes.
Task027E1 committed a fail-closed staged-blob scanner for age identities and
owner-supplied exact sensitive values. These controls reduce accidental Git
publication and make local encrypted recovery testable; they do not upload,
schedule or attest to a backup.

Task029B adds private source/content manifests and human-context records beneath
the same ignored, guarded `data/` tree, so a future full-data checkpoint can
include them without a new backup format. Their own verification proves bytes,
identity and declared provenance only. It does not prove football truth,
prospective timing, manager-state currency or semantic validity of a referenced
engine artifact.

The owner reports that password-manager recovery and a private B2 bucket with
default encryption, Object Lock and 90-day compliance retention have been
prepared. Label this as external owner-reported state; repository evidence does
not verify it, and identifiers and credentials do not belong here. Task027E is paused
before production age-key generation, application-key creation or a synthetic
upload while a dedicated encrypted removable medium suitable for an independent
key-custody copy is unavailable. The owner must
explicitly accept the irreversible 90-day lock before Task027F uploads real
evidence. There is no production checkpoint, exact-version readback,
disconnected copy or clean-environment real restore drill: **NO VERIFIED OFFSITE
BACKUP**.

Task027B/027C identified original manager-input/source screenshots referenced by
two earlier operational runs whose matching source bytes were not found under
the authorized `data/` inventory. They remain **NOT LOCATED**, not proven
destroyed. The newly captured current source does not replace their prospective
evidence, and reconstructed screenshots cannot do so.

### Recovery boundaries and safe sequence

1. Restore a verified repository revision; inspect status/log before work.
2. Install using the [validation commands](#validation-evidence-and-procedure) and run
   all tests. Never repurpose test fixtures as recovered production evidence.
3. Separately obtain an authorized encrypted checkpoint of required
   raw/clean/features, predictions, manager evidence, operations, journals/diffs,
   and experiment manifests/artifacts. Preserve original bytes and referenced
   paths; inspect path assumptions before relocation. Do not rewrite manifests
   to make a move appear valid. Follow the Task027D runbook rather than treating
   this summary as an operator command.
4. Verify the required chain with existing trusted readers before using any
   recovered decision. A digest or an index alone is not evidence recovery or
   authentication; the referenced bytes and trusted source must exist.
5. Missing pre-deadline evidence cannot be recovered by fetching today's data,
   rebuilding under an old ID, or inventing timestamps. Fail closed and ask the
   human which operations remain possible. Historical backfill has separate
   evidence rules and cannot masquerade as prospective evidence.

The design targets an upload after each material evidence capture/finalized
decision, at worst by end of active day, and within one hour during the final
24 hours before a deadline. Target RTO is four hours through B2 or one business
day through the disconnected copy. These RPO/RTO values are unproven targets
until Task027F measures a real restore. Private inventory completeness, custody
locations, access/recovery records, object-version receipts, provider account
state, deletion decisions and drill results remain **REQUIRES HUMAN CONTEXT**.
Do not add private data to Git to solve recovery; explicitly stage reviewed paths
and run both Task027C/Task027E1 guards immediately before every commit.
