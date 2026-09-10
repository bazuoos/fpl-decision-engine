# TASK030C — Python dependency reproducibility design

## Status and authority

This is a design-only candidate based on
`d6f5c145967a977f0a537b9af56b87c1c882bad8`.

It does not create a lockfile, install a package manager, change CI, update a
dependency, modify production code or authorize an operational FPL command.
Repository code, tests, contracts, immutable artifacts, manifests, frozen
decision records and Git history remain authoritative.

The design is being prepared in a separate Git worktree so the reviewed GW4
Task028G plan can remain bound to an unchanged `main` checkout. It is separate
from Task026C, changes no engine or application behavior, and does not activate
the completion monitor. **NO VERIFIED OFFSITE BACKUP** remains true.

## Problem

The project declares seven direct Python requirements in `pyproject.toml`, but
CI and the onboarding instructions install their allowed ranges with:

```text
python -m pip install --editable .
```

That command resolves transitive dependencies again on every fresh install.
Two runs from the same Git commit can therefore receive different package
versions. A future release, removal, changed wheel or incompatible transitive
dependency could make an old repository revision fail even though its CI once
passed.

The JavaScript application already has a committed `package-lock.json` and uses
`npm ci`. Python has no equivalent repository-owned resolution. Task028F partly
compensates by binding an activated schedule plan to the installed
name/version inventory, but that inventory detects drift only after an
environment exists. It cannot recreate the reviewed environment on a new Mac.

## Repository-established facts

- The package supports Python `>=3.10`; CI currently tests Python 3.10 on
  Ubuntu, while the current owner workstation has Python 3.14 available.
- `duckdb`, `highspy` and other transitive packages may select platform- and
  Python-specific wheels.
- The package is installed editable from a `src/` layout through
  `setuptools.build_meta`; `setuptools>=68` is currently a floating isolated
  build requirement.
- The test suite uses `unittest`. It has no separate Python test dependency
  group. `httpx` is declared directly and supports FastAPI's test client.
- The current direct ranges are package compatibility policy. Locking one
  reviewed environment does not require converting those ranges to exact
  public package requirements.
- Dependency installation requires network access. Tests themselves use
  offline fixtures and do not require generated `data/`.
- CI actions are full-SHA-pinned and action update proposals are reviewed;
  dependency updates are never auto-merged.

## Decision summary

Adopt one committed `uv.lock` as the authoritative Python environment
resolution. Keep the existing compatibility ranges in `pyproject.toml` and use
uv's project interface for local setup and CI.

```text
reviewed pyproject.toml ranges + exact uv tool policy
                         |
                         v
                explicit lock update
                         |
                         v
                 committed uv.lock
                  /             \
                 v               v
       macOS owner environment   Ubuntu/Python 3.10 CI
          exact locked sync      exact locked sync
                 \               /
                  v             v
             same resolved versions where
             environment markers permit

       -X-> no implicit lock rewrite in CI
       -X-> no second committed Python lock authority
       -X-> no automatic dependency merge
       -X-> no claim of offline or byte-identical builds
```

`uv.lock` is selected because it records a universal resolution across Python
and platform markers. That fits the real Mac/Ubuntu and Python 3.10/3.14
boundary better than a single-platform generated requirements file.

`pylock.toml` is a useful standardized export format, but it must not be
committed beside `uv.lock` in this task. Two generated resolution files create
ambiguity about which one is authoritative and require a drift gate. Export may
be added later for a concrete external consumer.

## Scope

### Included in the later implementation

1. Commit one uv-managed universal lockfile at repository root.
2. Pin the uv tool version as repository policy.
3. Constrain the current setuptools build dependency to an exact reviewed
   version for uv project installs.
4. Replace CI's range-resolving pip install with a fail-closed locked uv sync.
5. Pin the uv setup action by full commit SHA, pin the downloaded uv version and
   verify its platform artifact with an explicit checksum.
6. Disable persistent dependency caching in the first version so cache state
   cannot obscure the clean-install evidence.
7. Preserve Python 3.10 CI and validate the initial lock on the owner's current
   macOS arm64/Python 3.14 environment in a separate worktree environment.
8. Update setup, validation and dependency-update instructions.
9. Add reviewed weekly uv dependency proposals with a low open-PR limit and no
   auto-merge, after confirming the committed lock is recognized.
10. Record exact validation evidence and refresh continuity claims.

### Excluded

- Changing engine, model, optimizer, reliability, application or frontend
  behavior.
- Reclassifying, adding, removing or intentionally upgrading direct application
  dependencies merely to introduce the lock. Any version movement necessarily
  selected by the first universal resolution must be disclosed and reviewed.
- Narrowing the declared Python support or supported operating systems without
  separate evidence and approval.
- Adding `.python-version` or allowing uv to download a Python interpreter.
- Committing `.venv`, caches, wheels, source distributions or package-index
  credentials.
- Committing both `uv.lock` and an exported requirements or `pylock.toml` file.
- Auto-merging Dependabot proposals or performing unattended lock upgrades.
- Claiming fully offline installation, byte-for-byte reproducible wheels,
  reproducible native solver behavior across CPUs, or permanent PyPI
  availability.
- Activating Task028G, accessing private data, starting Task026C or changing
  backup state.

## File and authority model

The later implementation should establish these roles:

| File or control | Role | Authority |
|---|---|---|
| `pyproject.toml` | Direct dependency ranges, Python compatibility, exact uv policy and build constraint | Human-reviewed intent |
| `uv.lock` | Complete resolved Python graph, markers, distribution locations and hashes supported by uv | Sole committed environment resolution |
| `.github/workflows/ci.yml` | Exact bootstrap and fail-closed consumption of the lock | Mechanical enforcement |
| `.github/dependabot.yml` | Proposes reviewable uv lock updates on a bounded schedule | Proposal source only |
| README/project docs | Owner workflow and honest limitations | Explanatory; never overrides files above |
| `.venv/` and uv cache | Disposable local materialization | Never evidence and never committed |

`pyproject.toml` and `uv.lock` are complementary rather than competing. The
former says which versions the project permits; the latter says which versions
were actually reviewed. A dependency-set or constraint change that makes the
lock stale must fail CI. A loosened range may leave the existing resolution
valid; the code review still sees that policy change even when no package
version needs to move.

## Tool bootstrap and supply-chain boundary

The implementation must not use an unpinned installer command or install
`latest`.

CI should retain the existing full-SHA-pinned `actions/setup-python` step and
its explicit Python 3.10 selection. It should then use the official
`astral-sh/setup-uv` action with all of these controls:

- an independently verified full commit SHA and human-readable release comment;
- one explicit uv version, also declared as an exact `required-version` in
  repository configuration;
- the expected checksum for the Linux x86-64 uv artifact;
- download from the official GitHub release rather than an implicit mutable
  mirror selection;
- dependency cache upload disabled;
- no uv-managed Python download.

The setup action is an additional third-party executable trust boundary. A full
SHA freezes its code; the uv version and checksum freeze the intended binary.
Neither proves the publisher benign. The independent review must verify the
action commit belongs to the cited immutable release, inspect the action's
download/checksum path, and verify the uv binary checksum against the official
release before the workflow is committed.

Local installation instructions should name the same uv version and direct the
owner to the official release/install guidance. They must include a version
check before any project operation. A future repository bootstrap helper may
make this easier, but this task must not store credentials or silently install
tools outside an explicit owner command.

## Lock generation policy

The first `uv.lock` must be generated from the exact reviewed base with the
pinned uv version. Generation is a dependency-selection event, not formatting.
The current installed inventory should be used as the preservation baseline.
Where the selected uv workflow can express compatible preferences safely, it
should retain those versions. Universal resolution may still require different
or additional selections for Python 3.10 and platform markers; every such delta
must be disclosed rather than described as a mechanical lock conversion.
The review package must include:

- old and new direct requirements;
- every selected package/version and applicable marker;
- additions, removals and version changes compared with the currently installed
  canonical distribution inventory;
- packages that require platform wheels or source builds;
- the lockfile checksum and pinned uv version;
- proof that the lock is unchanged by a second lock operation.

The initial task must preserve all seven direct requirements and their current
ranges. Any incompatibility discovered while resolving is a blocker requiring
its own explained dependency change, rather than permission to silently widen,
narrow or replace a package.

Do not limit uv's resolution environments merely to make locking succeed. The
current metadata declares Python `>=3.10` without an operating-system
restriction. If a dependency cannot satisfy that claim, report the real
compatibility boundary and review it separately.

## Build-dependency treatment

An application lock is incomplete if editable installation still chooses an
arbitrary future setuptools version. The implementation should retain
`setuptools.build_meta` and add one exact, reviewed setuptools build constraint
through uv project configuration. The constraint must agree with
`build-system.requires`.

This controls the version selected when uv builds the local package and any
dependency that requests setuptools. It does not establish hash-required,
byte-identical distribution builds. The project currently installs editable
source for development and CI; publishing wheels or source distributions is
outside scope. If production later consumes a built wheel, that artifact and
its complete build chain need a separate reproducible-build design.

## Locked setup and CI behavior

CI must use a sequence with these semantics:

1. Verify the running uv version is the repository-required exact version.
2. Run `uv lock --check`; missing or stale lock is a hard failure.
3. Run an exact project sync with `--locked` and no Python downloads.
4. Run tests through that already-synced environment without allowing another
   resolution or synchronization.
5. Preserve the existing JavaScript `npm ci` and all contract, type, build,
   boundary and whitespace gates.

The exact commands should be proven against the selected uv version during
implementation. CI must never use plain `uv sync` or `uv run` in a mode that may
rewrite the lock. A generated diff after validation is a failure, even if the
test suite passes.

The first implementation should leave dependency caching disabled. The project
is small, and a clean download gives clearer evidence that the lock can
materialize without hidden cache state. Caching may be reconsidered only after
measured CI cost justifies it and cache keys are bound to the lock.

## Local workflow

The documented owner path should be short and match CI:

1. Install and verify the repository-pinned uv release.
2. From the checkout, run the locked exact sync with the existing compatible
   system Python; uv must not fetch another interpreter implicitly.
3. Run commands through the synchronized project environment.
4. Use the lock check before review or commit.

The implementation must verify how uv behaves when an older manually-created
`.venv` already exists. Instructions should require recreating that disposable
environment when its interpreter or ownership is incompatible, without ever
deleting private `data/` or operational control roots.

The GW4 Task028G plan remains bound to the existing main-worktree interpreter
and installed-distribution inventory. Task030C work and validation must use the
separate worktree environment. No uv command for this task may rewrite or
replace the main checkout's `.venv` before the monitor plan expires or is
explicitly retired.

## Dependency updates

A lockfile deliberately stops package versions from changing merely because a
new release appears. Updates require an explicit review event.

For manual updates:

1. Start from clean `main` in an isolated worktree.
2. Use the repository-pinned uv version.
3. State whether one package or the complete graph is being updated.
4. Generate the lock intentionally; do not hand-edit it.
5. Review the full graph diff, upstream release/security information and wheel
   availability for Python 3.10/Linux and the current owner Mac/Python.
6. Run the lock check, exact fresh sync and complete validation suite.
7. Obtain independent review before merge.

Dependabot may propose uv updates weekly, with individual reviewable PRs and a
small open-PR limit. It is a discovery mechanism. A green CI result does not by
itself authorize merging a new numerical solver, database engine, schema
validator, web framework or transitive package.

Before enabling the `uv` ecosystem entry, implementation review must confirm
that the repository-pinned uv release satisfies GitHub's then-current supported
version floor and that Dependabot parses the candidate `uv.lock` successfully.
Support observed while this design was written does not prove future support.

The uv tool version itself is a separate dependency. Its action pin, configured
version, checksum and compatibility with the lock must be updated together in
one reviewed change.

## Required validation

The implementation candidate is not safe until all of these pass:

### Static and lock checks

- The only committed Python resolution file is root `uv.lock`.
- The pinned uv version appears consistently in project policy, CI and docs.
- `uv lock --check` succeeds and a second lock operation produces no diff.
- CI contains no range-resolving `pip install --editable .` path.
- CI uses the reviewed action SHA, uv version and Linux artifact checksum.
- No cache upload, Python auto-download, credential, private index or trusted
  host bypass is enabled.

### Clean environment checks

- A fresh isolated Ubuntu/Python 3.10 environment syncs exactly from the lock
  and passes the full Python and frontend suite.
- A fresh isolated owner macOS arm64/Python 3.14 environment syncs from the same
  lock and passes the full Python suite and applicable repository gates; the
  sanitized evidence records the exact operating-system, architecture and
  Python patch version.
- The resulting canonical installed name/version inventories are captured in
  sanitized review evidence and match the lock's environment-specific
  selection.
- The compiled/native dependencies load and the optimizer tests pass on both
  checked environments.
- No test requires private `data/`, secrets or a live FPL request.

### Adversarial failure checks

- Removing a direct dependency, adding one, or changing a constraint so the
  locked selection is no longer valid fails the lock check.
- Editing the required uv version without the coordinated bootstrap change
  fails setup or version verification.
- A wrong uv artifact checksum fails before uv executes.
- A missing lock fails rather than resolving from ranges.
- CI validation leaves the tracked tree unchanged.
- Staged-sensitive-content and private-path safeguards still pass.

## Recovery procedure

On a replacement machine with a Git checkout:

1. Verify the intended Git revision and repository cleanliness.
2. Install the exact uv version from the documented official source and verify
   its version; verify the downloaded artifact checksum when using the
   documented standalone artifact path.
3. Provide a compatible system Python explicitly. Do not allow an unnoticed
   interpreter download or substitution.
4. Run the lock freshness check and exact locked sync.
5. Run the complete offline validation suite.
6. Record the interpreter, platform, uv version, lock SHA-256 and canonical
   installed-distribution inventory before using the environment for an
   operational plan.

This rebuilds code dependencies. It does not restore ignored private evidence,
completion-monitor state, an age identity or B2 credentials. Those remain under
the Task027 recovery design.

## Failure model and honest limits

| Failure | Control | Remaining limit |
|---|---|---|
| New transitive release breaks old code | committed exact resolution | index/artifact availability can still fail |
| Dependency metadata changes in the repo | `uv lock --check` | a reviewer can intentionally accept a new lock |
| Local environment accumulates extras | exact sync | manually running other installers afterward can drift it |
| CI uv executable changes | action SHA, exact version and checksum | publisher/action code remains trusted software |
| Platform selects different package | universal markers and two-platform validation | untested platforms remain unproven |
| Setuptools changes | exact uv build constraint | build artifacts are not hash-required or byte-reproducible |
| Cached artifact hides a missing download | cache disabled initially | PyPI/GitHub availability is still external |
| Malicious or compromised upstream release | explicit update review and hashes | hashes prove identity, not safety |
| Laptop loss | Git restores declarations and lock | private data and credentials need Task027 recovery |
| Old revision restored years later | exact versions and artifact hashes/locations | upstream artifacts may be removed; no wheel mirror exists |
| Native solver differs by CPU/OS | platform tests and immutable output evidence | lockfiles cannot make native floating-point behavior universal |

The lock improves repeatability and auditability. It is not a software bill of
materials vulnerability verdict, artifact escrow, hermetic build system or
backup.

## Alternatives considered

### Keep pip range installation

This adds no tool and is operationally familiar, but it preserves the current
failure: each new environment resolves a different graph. Recording `pip
freeze` after installation is evidence of what happened, not a controlled
input for the next installation.

### Generated requirements files

Hash-pinned requirements can strongly constrain one target, but the project
would need carefully maintained outputs for Python/platform combinations or a
universal compilation policy. The public `pyproject.toml` plus several generated
files would also require explicit drift and authority rules. This is viable if
uv project management later proves unsuitable, but it is more operational work
for the current Mac/Linux boundary.

### `pylock.toml` as the primary lock

PEP 751 provides a standard format and is attractive for tool portability.
Current uv project features still use `uv.lock`, while pip's lock command is
experimental and produces a lock guaranteed only for the current Python and
platform. Selecting `pylock.toml` now would weaken the proven cross-platform
workflow or require multiple tools and outputs.

### Poetry, PDM or container-only pinning

These can provide locks, but introduce broader project conventions without a
current benefit over uv's universal lock. A container would add an image and
runtime boundary while leaving the native macOS operational path unresolved.

### Vendor every wheel and build input

Artifact escrow would improve offline and long-horizon recovery, but multiplies
storage, platform maintenance and update-review work. It is disproportionate
for the current solo project. Revisit only if old revisions must be rebuildable
without PyPI or if operational deployment moves to a fixed image.

## Acceptance criteria

Task030C implementation is complete only when:

1. A single reviewed `uv.lock` is committed and current with `pyproject.toml`.
2. The exact uv tool and setuptools build versions are repository-controlled.
3. Fresh Linux x86-64/Python 3.10 and owner macOS arm64/Python 3.14
   environments pass the required validations from that lock, with exact OS,
   architecture and Python patch versions recorded.
4. CI consumes the lock without rewriting it and fails on missing/stale state.
5. Bootstrap code and the uv binary are pinned and independently verified.
6. Update proposals remain review-only and bounded.
7. README and continuity docs no longer instruct range-resolving Python setup or
   claim locking is undelivered.
8. The implementation changes no decision semantics and touches no private
   evidence.
9. Independent adversarial review reports no blocker, CI passes, and `main`
   remains unchanged until explicit commit authorization.

## Proposed task sequence

1. **Task030C review:** review this design against repository truth and current
   uv, pip and GitHub behavior.
2. **Task030D implementation:** create the lock and exact tool/build policy,
   change CI/setup/update monitoring, validate both real environments, and
   prepare a complete review bundle without committing.
3. **Task030D independent review:** verify source provenance, lock behavior,
   failure modes, platform evidence and scope.
4. **Task030D commit/CI:** only after explicit owner authorization, commit and
   push, then confirm GitHub CI and dependency-monitor parsing.
5. **Continuity refresh:** replace the documented unlocked-Python limitation
   with the exact delivered controls and their remaining limits.

No step authorizes Task026C, an FPL network operation, GW4 scheduler activation
or a dependency upgrade beyond what the initial lock necessarily resolves.
