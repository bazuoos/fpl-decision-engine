# TASK030D — Python dependency reproducibility implementation

## Status and authority

The initial implementation was committed as
`624649e4ff35ecc5c6d8ca3885dc4b52a0183432` after independent review reported
SAFE. Its Linux CI passed, but the required live Dependabot gate exposed an
incompatibility between repository uv 0.12.12 and Dependabot's uv 0.12.7
updater. This is the uncommitted remediation candidate for that finding.

It introduces a Python lock and changes development/CI dependency setup. It
does not change engine, model, optimizer, reliability, application or frontend
behavior. It accesses no private evidence, performs no live FPL request, does
not activate Task028G and does not start Task026C. Repository code, tests,
contracts, immutable artifacts, manifests and frozen decision records remain
authoritative.

The candidate is being built and tested in the isolated worktree
`/private/tmp/fpl-task030c-python-reproducibility`. The active `main` checkout
and its Task028G-bound `.venv` remain unchanged. **NO VERIFIED OFFSITE BACKUP**
remains true.

## Delivered controls

The candidate adds these repository controls:

- one root `uv.lock` as the sole committed Python environment resolution;
- exact uv `0.12.7` policy in `pyproject.toml`;
- `python-downloads = "never"` repository policy;
- exact setuptools `84.0.0` build constraint;
- an exact `dev` group containing pip `26.1.1` and setuptools `84.0.0`;
- full-SHA-pinned `astral-sh/setup-uv` v10.0.1 in CI;
- an explicit SHA-256 for the uv 0.12.7 Linux x86-64 archive;
- direct GitHub Release download, with persistent setup-uv and uv dependency
  caching disabled in CI;
- a lock freshness check, exact locked sync and test execution without another
  sync;
- weekly, bounded, review-only Dependabot proposals for the `uv` ecosystem;
- updated owner setup and validation instructions.

The seven application dependency ranges are unchanged. Pip and setuptools are
development tools rather than application runtime requirements.

## Trust and version provenance

The selected uv release is `0.12.7`, published by `astral-sh/uv` on
2026-08-27. The official macOS arm64 archive was downloaded to a temporary
directory and verified before execution:

```text
uv-aarch64-apple-darwin.tar.gz
SHA-256 127ebdda7ad953cdf198e964b570ea5771b85467ea93eb7cb6d6f8e6f55408f3
uv 0.12.7 (61291a8ca 2026-08-27 aarch64-apple-darwin)
```

GitHub's release-asset digest and the separately downloaded official `.sha256`
file agreed with that value.

CI uses setup-uv release v10.0.1 at the exact tag commit:

```text
20cfd1bf945f4377ade1205e4dbc17946fc9a30d
```

The exact action source was inspected. Its checksum path hashes the downloaded
archive before extraction, and a supplied checksum overrides its internal known
checksum table. CI supplies the official Linux x86-64 archive hash:

```text
uv-x86_64-unknown-linux-gnu.tar.gz
SHA-256 788f18abea7c5f55d6216e4f5613fd89d4d59b631efeec117b2b07fe72f1da21
```

`download-from-astral-mirror: false` makes the pinned action use the artifact's
official GitHub Release URL. `enable-cache`, `restore-cache` and `save-cache`
are all false. The action remains third-party executable code; its full commit
pin freezes identity but does not prove publisher intent or eliminate GitHub as
an availability boundary.

## Lock construction

The owner Mac's pre-existing operational environment was read without changing
it. Its relevant identity was:

```text
macOS 26.5.1 (Build 25F80)
arm64
Python 3.14.5
```

Its installed distribution name/version inventory was used as the preservation
baseline. A temporary project copy constrained the existing compatible
versions during the first universal resolution. The constraints were not
committed and do not remain in the final lock manifest. After they were removed,
uv regenerated only the manifest section and retained the selected versions.
A second ordinary lock operation was byte-stable.

All packages selected for the actual macOS/Python 3.14 environment match the
pre-existing environment. Universal resolution added only these branches that
are not installed on that Mac:

| Environment marker | Additional locked selection | Reason |
|---|---|---|
| Python below 3.11 | `numpy==2.2.6` | current NumPy 2.5.2 requires Python 3.12+ |
| Python 3.11 | `numpy==2.4.6` | current NumPy 2.5.2 requires Python 3.12+ |
| Python below 3.11 | `rpds-py==0.30.0` | current rpds-py 2026.6.3 requires Python 3.11+ |
| Python below 3.11 | `exceptiongroup==1.3.1` | AnyIO compatibility dependency |

The final lock contains 31 package records, including the editable project,
environment-specific duplicate records and exact development tools. Its current
SHA-256 is:

```text
391883c61a0e5796dcb1965883def48aba516d0b45b7060fa91a9659d4eba1bc
```

## Hidden test-tool dependency found

The first clean locked environment intentionally contained only declared
project dependencies. One test failed because it invokes:

```text
python -m pip wheel --no-deps --no-build-isolation
```

The old environment happened to contain pip and setuptools, but the project did
not declare either as a test requirement. This was an environment leak rather
than an engine failure.

The candidate adds exact pip and setuptools versions to the default development
group. They are present for tests and local development while remaining outside
the application's published runtime dependency list. The exact setuptools
version also agrees with the isolated build constraint. The previously failing
packaged-schema wheel test then passed independently before the full suite was
rerun.

## CI behavior

The proposed CI sequence is:

1. Set up the existing Python 3.10 interpreter with the current full-SHA-pinned
   official action.
2. Install uv 0.12.7 through the full-SHA-pinned setup-uv action and verify the
   downloaded archive checksum before extraction.
3. Run `uv lock --check --no-python-downloads`.
4. Run an exact `uv sync --locked --no-python-downloads --no-cache`.
5. Run the test suite through `uv run --locked --no-sync` so test execution
   cannot repair or resynchronize the environment.
6. Preserve every existing frontend, generated-contract, type, build,
   dependency-boundary and whitespace gate.

The repository setting and explicit flags both prohibit uv-managed Python
downloads. A missing or stale lock and a mismatched required uv version were
each tested in disposable project copies and failed closed.

The initial commit's Ubuntu x86-64/Python 3.10 run passed all 582 Python tests
and four frontend tests in GitHub CI run `34514867297`. The remediation must
repeat that gate because its uv bootstrap version and checksum changed.

## Dependency update behavior

The new `uv` Dependabot entry uses the existing Tuesday 09:00 Asia/Bangkok
schedule and limits open uv update pull requests to three. It does not group,
approve or merge updates.

GitHub's documentation listed the `uv` ecosystem with a supported version floor
of 0.11, but that public floor did not establish compatibility with every newer
uv release. The first live run, `34514872002`, accepted the ecosystem entry,
parsed the project and enumerated its dependencies. When it attempted to update
pip, it failed closed with `tool_version_not_supported`: the repository required
`==0.12.12` while the Dependabot updater supported `0.12.7`.

This remediation pins the repository bootstrap and required version to 0.12.7,
matching the updater actually observed in the required empirical gate. A
successful second default-branch run is still required after commit. Future uv
tool updates must confirm the live updater version rather than infer
compatibility from the documented floor.

An update proposal may change direct or transitive packages, platform branches,
the uv lock schema, or the tool bootstrap. Each proposal requires ordinary code
review, the complete suite and independent review when it affects a critical
native or decision dependency. Nothing is auto-merged.

## Local validation evidence

The candidate currently has this local evidence:

| Check | Result |
|---|---|
| Verified uv binary | 0.12.7, macOS arm64 archive hash matched |
| Lock freshness | passed |
| Lock idempotence | second ordinary generation byte-identical |
| Exact clean sync | passed on macOS 26.5.1 arm64, Python 3.14.5 |
| Environment check | 27 installed packages, no changes required |
| Packaging regression | 1 focused test passed after dev-tool declaration |
| Full Python suite | 582 passed |
| Frontend suite | 4 passed |
| Generated contracts | current |
| TypeScript | passed |
| Production frontend build | passed |
| Browser dependency boundary | passed |
| Missing lock | rejected |
| Stale direct dependency set | rejected after resolution, lock not rewritten |
| Wrong required uv version | rejected before lock use |
| Dependabot update simulation | uv 0.12.7 upgraded pip in a disposable project copy |
| Patch whitespace | passed |
| Ubuntu x86-64/Python 3.10 | initial commit passed; remediation rerun pending |
| Dependabot uv parsing | project parsed; 0.12.12 update failed; remediation rerun pending |

Tests used offline fakes and temporary files. No operational FPL command or
private-data read occurred.

During the unchanged frontend install, npm reported two high-severity audit
findings in the existing `package-lock.json`. Task030D did not introduce or
remediate them, and current CI does not make `npm audit` a gate. They require a
separate dependency-security triage rather than an unreviewed package update in
this Python-lock task.

The Python run also emitted the existing FastAPI/Starlette warning that its
current TestClient path uses deprecated `httpx` integration. The tests pass and
the lock prevents surprise movement, but the warning is a future compatibility
item for an explicit dependency update.

## Recovery and limitations

From an intended Git revision, a replacement machine can install the exact uv
release, provide a compatible system Python, verify the lock, materialize the
environment and run the offline suite. The lock records exact package versions,
markers, distribution locations and hashes.

This does not vendor the distributions. PyPI or GitHub removal and network
outages can still block a rebuild. It does not make different interpreters,
operating systems or CPUs execute native DuckDB/HiGHS code byte-identically. It
does not create a vulnerability verdict, software bill of materials, hermetic
wheel build, private-data backup or scheduler migration.

The exact setuptools constraint controls selection for current uv builds but
does not require hashed, byte-reproducible wheel construction. A future built
deployment artifact needs a separate build-chain design.

## Remaining gates

Before commit authorization:

1. Recheck the final diff, lock identity, installed inventory, links and
   whitespace.
2. Independently review the complete candidate, bootstrap provenance, hidden
   dev dependency, marker forks, CI failure behavior and remaining claims.

After an independently reviewed commit is explicitly authorized:

1. Push `main` and require the exact commit's GitHub CI to pass on Ubuntu
   x86-64/Python 3.10.
2. Confirm setup-uv reports the expected uv version and accepts the supplied
   archive checksum.
3. Confirm the Python suite, frontend suite and all existing gates pass.
4. Confirm GitHub Dependabot accepts the `uv` ecosystem entry and parses the
   committed lock.
5. Refresh continuity documents with exact commit/run evidence. If CI or
   Dependabot fails, remediate through the same review workflow rather than
   weakening the lock or bypassing a gate.

Passing local macOS tests does not establish the Linux result. Passing CI does
not establish permanent artifact availability or make a future dependency
update safe to merge.
