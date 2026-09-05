# TASK027D — Local encrypted checkpoints and isolated restore

Implementation/review handoff, not authorization to capture real evidence.
Base: `2a0f389c38021bc81717c7b003e71461aa9b4785` (`main`).
Read [the specification](TASK027D_ENCRYPTED_CHECKPOINT_SPEC.md) and
[Task027C boundaries](TASK027C_LOCAL_BACKUP_READINESS.md) first.

The implementation changes are the standalone checkpoint script, synthetic tests,
this runbook, and a checksum-pinned CI prerequisite installation. Engine/application
code, artifact contracts, model/optimizer/reliability semantics, hooks and provider
settings are untouched.
There is no network operation in the tool. No real data/key capture or holdout
evaluation is part of this implementation.

## Dependency and execution boundary

Requires Python 3.10+, system Git with SHA-1 repositories and bundle v2 support,
and native **age v1.3.1**. `age-keygen v1.3.1` is additionally required by tests.
The exact age version is checked before source capture/publication; missing,
broken or different versions fail, without a plaintext fallback. Upgrades require
review and rerunning the crypto acceptance tests. Linux/macOS are the supported
POSIX targets; Windows and unusual/network filesystems are not validated.

Installation performed for synthetic testing: Homebrew `age` 1.3.1 on macOS
26.5.1 arm64, with Python 3.14.5 and Apple Git 2.50.1. Before installation,
the official [age installation guidance](https://github.com/FiloSottile/age)
and Homebrew formula/bottle metadata were inspected. Homebrew verified its
download checksum. Recorded supply-chain references:

- Homebrew tap commit: `87fb5dd411a526473943f07e581a20d5e6ed3515`.
- Formula source SHA-256: `33b86759342242f9736d0e5f04f65e7a2cabf5aaf3221bd35884567dee102b27`.
- Upstream source: `https://github.com/FiloSottile/age/archive/refs/tags/v1.3.1.tar.gz`;
  SHA-256 `396007bc0bc53de253391493bda1252757ba63af1a19db86cfb60a35cb9d290a`.
- Official Linux amd64 release archive SHA-256:
  `bdc69c09cbdd6cf8b1f333d372a1f58247b3a33146406333e30c0f26e8f51377`.
- Homebrew arm64_tahoe bottle SHA-256:
  `772ce6765f7cd9232cb23d1875cbe7617a762644c19acda569fb3770201cf2b9`.
- Installed `age` SHA-256:
  `f52e5ee772e1c0e3c6be5bf837b469a40346df3515db9a1b41230376fdff6a76`.
- Installed `age-keygen` SHA-256:
  `4a0de88e9365ee19910d2cc067b6f2f3f7895cd9917493b15173ddf31a96a9fe`.

This is Homebrew provenance, **not** a claim of independent upstream Sigsum
verification. Before another installation, verify the appropriate pinned release
or package provenance for that platform; do not assume today's moving package
formula still selects this version. No owner recovery key was created.

Native X25519 recipient/identity files only: up to 16 keys, 8 KiB input, comments
allowed. No passphrases, SSH, plugins, hardware tokens or post-quantum key forms.
Private identities require owner-only permissions and must be outside all captured
scope, checkpoint, scratch and restore destinations. Public recipient files are
also required outside source/repository/output.
Keys are read without following symlinks and passed to age through inherited file
descriptors, never embedded in command arguments or copied into the checkpoint.
The invoking shell still knows user-supplied paths; default tool output does not
print paths, filenames, private manifests, key contents or subprocess stderr.

## Explicit CLI (future authorized use only)

All paths must be absolute, without lexical `..` or symlink components. There is
no `latest` lookup or default data path. Prepare owner-private (0700) output,
scratch and recovery parents **outside** the repository and source tree. The
scratch parent is mandatory for verify and restore; it must be on the explicitly
chosen private recovery filesystem and separate from the checkpoint, repository,
source and identity. Coverage is a private canonical JSON file outside source
scope, for example these exact bytes with no trailing newline:

```json
{"gaps":["Original source evidence NOT LOCATED; scope requires owner review"],"status":"known_gaps"}
```

`not_assessed` with an empty gaps list is also allowed; `complete` is not.
Stop all writers, then explicitly acknowledge quiescence. The full recorded
commit must equal clean tracked HEAD; untracked work is excluded, not mislabelled
as committed source.

```sh
python3 scripts/encrypted_checkpoint.py create \
  --source-root /absolute/authorized/source \
  --repository /absolute/repository \
  --commit FULL_40_CHARACTER_HEAD_COMMIT \
  --recipients /absolute/private/recipients.txt \
  --output-parent /absolute/private/checkpoints \
  --coverage /absolute/private/coverage.json --quiescent

python3 scripts/encrypted_checkpoint.py verify \
  --checkpoint /absolute/private/checkpoints/checkpoint_EXPLICIT_ID \
  --identity /absolute/private/recovery-identity.txt \
  --scratch-parent /absolute/private/recovery-scratch

python3 scripts/encrypted_checkpoint.py restore \
  --checkpoint /absolute/private/checkpoints/checkpoint_EXPLICIT_ID \
  --identity /absolute/private/recovery-identity.txt \
  --scratch-parent /absolute/private/recovery-scratch \
  --destination /absolute/private/recovery/NEW_RESTORE_DIRECTORY
```

Create returns an opaque checkpoint ID, ciphertext hash/size and aggregate
assurances; combine that ID with the explicitly supplied output parent. Verify
and restore require that exact directory, not a renamed checkpoint identity, and
never fall back to the OS default temporary directory. The scratch parent is
validated as an existing owner-private directory. Temporary bundle and bare-Git
material is exclusively created beneath it and removed after a handled run;
crash residue remains private and requires separately authorized cleanup.
Restore refuses existing destinations, overlap with original roots/current tool
repository/checkpoint, and unsafe parents. It first verifies without extracting
data, then reads/verifies again into the new destination. This extra pass prevents
even a refused restore from creating a temporary directory inside the source.
It does not checkout or execute bundled code or call engine readers.

## Format and publication

Format: `fpl-private-checkpoint-v1`, one binary age object wrapping an uncompressed
manifest-first tar subset. The checkpoint directory contains `checkpoint.age`,
`.INCOMPLETE`, and, only after successful creation, `COMPLETE.json`.

```text
encrypted stream
  manifest.json                       canonical JSON, first member
  payload/data/<relative files>       lexicographically ordered exact bytes
  payload/repository/source.bundle    committed HEAD + required ancestor history
  exactly two 512-byte zero end blocks; no trailing bytes
```

The manifest contains exactly: `format_version`, `checkpoint_id`, `capture_time`,
`source_commit`, `tool_versions` (age/git/python), `logical_roots` (data/repository),
`coverage`, `file_count`, `total_bytes`, `directories`, `files`, `bundle`.
Each file record has `path`, `size_bytes`, `sha256`. Counts include the Git bundle.
Directories are explicitly recorded, including empty directories; they are not tar
members. Capture time is UTC packaging time, never an FPL evidence-observation time.
Private absolute source roots and embedded artifact paths are preserved unchanged.

The Git bundle advertises exactly the recorded commit as `HEAD`, with no
prerequisites. Bundle verification uses a temporary bare repository, commit type
check and strict Git object verification. Only that commit's required history is
bundled: no separate branch/stash refs, local config or untracked files. Any secret
already committed in that history would remain in the bundle; this is not a secret
scanner. The source repository must itself be trusted and quiescent.

Tar metadata is canonical: uid/gid/mtime zero, regular files mode 0600, no links,
ownership or executable restoration. Long/UTF-8 paths use one local PAX `path`
record only. Global/GNU/sparse/size extensions and other member types are refused.
The reader checks exact header bytes, member order/set, zero padding, sizes,
hashes, canonical JSON and exact fields. It never calls `extractall`.

Task027C `open_root`/`fingerprint` primitives are reused unchanged. Capture hashes
an initial no-follow inventory, checks **each exact streamed byte digest**, then
rechecks enumeration/metadata/root identity/committed HEAD. Symlinks, hardlinked
source files, special files and detected changes fail. This is not an atomic
filesystem snapshot: quiescence is required and metadata checks do not defeat a
malicious owner hiding mutations or changes after the final check. Reads can
update filesystem access times.

Publication reserves a new directory atomically using exclusive `mkdir`, with
0700 permissions. New files are 0600 and use exclusive creation. A fsynced
temporary marker is hard-linked to the final marker name, atomically refusing
replacement, then its temporary name is removed. Unsupported hard-link semantics
fail; there is no overwriting rename fallback. Existing paths are never reused.
`.INCOMPLETE` intentionally remains even after success: **only a valid final
marker plus verification establishes completion**, not directory presence.

Handled failure removes only the operation's own inode-checked, newly reserved
tree. An abrupt crash can leave a private incomplete directory (ciphertext,
temporary committed-source bundle/bare repository, or partially restored plaintext).
No automatic stale-directory reuse or age-based deletion exists. An operator must
verify the exact path is unpublished and no process is using it before separately
authorizing cleanup; never delete a completed checkpoint to retry. No secure-erase
guarantee is made. Files are fsynced but directories are not: this guards process
interruption/torn destination publication, **not full sudden-power-loss durability**.

The outer receipt has exactly `format_version`, `checkpoint_id`,
`ciphertext_size_bytes`, `ciphertext_sha256`, `status=CREATED`. It is not an
independent authenticity anchor. Anyone with a public recipient can encrypt a
new valid archive; age does not establish the creator's identity. A later remote
readback must compare to a separately trusted checkpoint hash/object version.

## Untrusted-input limits and threat assumptions

| Resource | Maximum |
| --- | ---: |
| Ciphertext / plaintext archive (each) | 64 GiB |
| Total declared file bytes including bundle | 60 GiB |
| Individual data file | 4 GiB |
| Git bundle | 512 MiB |
| File records including bundle | 100,000 |
| Directories including logical roots | 100,000 |
| Manifest | 16 MiB |
| UTF-8 path / depth | 4,096 bytes / 64 components |
| PAX path metadata | 8,192 bytes |
| Subprocess wall time / version preflight | 1,800 s / 10 s |
| Captured command stdout | 65,536 bytes |

Streaming buffers are bounded; stderr is drained/discarded without accumulation.
The Git subprocess additionally has 120 CPU seconds, 128 descriptors and a
bundle-limit-sized per-file write ceiling. Linux uses a 2 GiB address-space ceiling;
macOS does **not** support that same enforced memory limit here. Native Git/age
are trusted, patched dependencies, not a general-purpose hostile-code sandbox.
Git's temporary object storage and memory on macOS are not given a total-volume/
RSS quota; use a dedicated resource-limited recovery environment before handling
adversarial large bundles. Input limits alone are not a complete pack-bomb defense.

No-follow descriptor traversal and NFC/case-fold path-collision checks apply on
both sides. Absolute/traversal/control/backslash/colon paths, trailing spaces/dots,
Windows device names, duplicate/aliased paths and missing directory parents fail.
This conservative portable subset can reject legitimate but ambiguous filenames.
Owner/root compromise, hostile same-UID modification of the explicitly selected
private scratch space,
compromised executables, filesystem failure, swap/core dumps and forensic erasure
are outside this protection boundary. Restore plaintext belongs on an explicitly
approved private recovery filesystem. Verify materializes only the committed
source bundle/bare Git objects temporarily under the supplied scratch parent;
data members are hashed and discarded.
No plaintext evidence archive is written during create/verify.

The whole decryption stream must end and age must exit successfully. Wrong keys,
tampering, truncation, trailing plaintext/ciphertext, timeout, early parser errors
and late nonzero exit cannot publish a successful restore. Group termination is
preferred; macOS sandbox denial falls back to terminating/reaping the directly
owned native child (plugins are disallowed). Original validation errors remain
visible as stable codes rather than being masked by pipe cleanup.

## Independent assurances and later gates

`CREATED` means byte capture succeeded, crypto archive verification `not_run`.
`VERIFIED`/`RESTORE_VERIFIED` mean decryption, archive bytes and bundled revision
verified; they do not reclassify original coverage or prove FPL semantic trust.
All operations retain separate `original_evidence_coverage`,
`engine_trust_chain_validation`, `offsite_readback`,
`independent_copy_verification` states. This tool never sets the latter three
to succeeded; those fields and the disaster-recovery sentinel are structurally
reserved against caller overrides. A restored tree preserves the private manifest and records
`RESTORE_COMPLETE.json`; checkpoint receipts are never rewritten by verification.

Original inputs/source evidence remain **NOT LOCATED**, not reconstructed or
definitively lost. After independent review and human authorization, establish
owner keys and off-laptop recovery custody, B2 account/access/retention/destination,
and an independent disconnected copy. Then explicitly authorize real capture,
remote ciphertext readback and restore without access to the original Mac. Run
the existing trusted engine readers at the recorded revision in an isolated
environment, without refresh/resume or fabricated evidence. Absolute layouts may
need reproduction; manifest rewriting is not relocation validation. Measure
RPO/RTO then. Until those gates pass: **NO VERIFIED OFFSITE BACKUP**.

## Synthetic acceptance and fresh-checkout verification

```sh
python3 -m unittest discover -s tests -p test_encrypted_checkpoint.py -v
python3 -m unittest discover -s tests -p test_backup_readiness.py -v
python3 -m unittest discover -s tests
python3 scripts/check_staged_privacy.py
git diff --check
```

The 29 checkpoint tests use temporary synthetic Git repositories/data and ephemeral
age keys. They exercise real encryption, decryption and restore, exact bytes/nulls/
long paths/empty directories, gaps, unsafe archives, resource limits, source races,
no-clobber concurrency, crash-left state, symlink injection, wrong keys, broken
pipes, child timeout/late failure, private logs and exclusion of other Git state.
Task027C's seven tests remain unchanged. No artifact-dependent skips are added:
missing age/keygen fails setup and leaves the real-crypto gate unexecuted, not passed.

Fresh-checkout equivalence uses a temporary export of tracked HEAD plus the task
files and CI change, with imports resolved from that export and no real `data/` or
local review artifacts. CI downloads the official age v1.3.1 Linux amd64 release,
verifies its pinned SHA-256, and exposes its `age` and `age-keygen` executables to
the real-crypto tests. Missing or changed bytes fail CI; tests are never skipped or
mocked. Local macOS results remain separate from Linux/Python 3.10 execution, which
is not established until the changed workflow successfully runs.

### Local implementation validation record (2026-09-04)

- `./.venv/bin/python -m unittest discover -s tests -p test_encrypted_checkpoint.py`:
  **27 passed**, 52.354 s, no skips or resource warnings in the final focused run.
- `./.venv/bin/python -m unittest discover -s tests -p test_backup_readiness.py -v`:
  **7 passed**, 0.626 s.
- `./.venv/bin/python -m unittest discover -s tests`: **445 passed**, 85.498 s,
  zero skips (418 existing + 27 checkpoint tests).
- Fresh tracked-HEAD export plus these task files: **445 discovered/executed,
  0 skipped, 0 failures, 0 errors**, 70.845 s. The same Python environment and
  installed age dependency were used; `PYTHONPATH` selected only the exported
  `src/` and root. Engine/app/script import paths were asserted inside that
  export; real `data/` was absent. This is file-independence proof, not a new
  dependency installation or Linux run.
- Both new Python files also parse with Python 3.10 grammar; runtime acceptance
  on Python 3.10 remains unexecuted here.
- Tracked and new-file no-index whitespace checks passed; the full-index privacy
  guard passed. No staging, commit or push. Existing tracked files have no diff.
- Full suites emitted the existing Starlette/httpx deprecation warning; no
  dependency upgrade or unrelated cleanup was made.

### Independent-review remediation record (2026-09-05)

Claude's first independent review reproduced the original 27 real-crypto tests
and found no blockers. It identified the OS-default scratch location, missing CI
age prerequisite, caller-overridable assurance sentinels and unexecuted Python
3.10 runtime as follow-ups. This remediation requires an explicit private scratch
parent, reserves the fixed assurance fields, and adds the checksum-pinned CI step.

- Official GitHub release metadata and a separately downloaded archive both
  reported the pinned Linux amd64 SHA-256 recorded above; the archive contained
  `age/age` and `age/age-keygen` at the workflow's configured paths. The downloaded
  Linux binaries were inspected as archive members, not executed on macOS.
- Final focused checkpoint suite: **29 passed**, 41.473 s, zero skips.
- Task027C backup-readiness suite: **7 passed**, 0.852 s.
- Full local suite: **447 passed**, 168.516 s, zero skips.
- Fresh tracked-HEAD export plus the five Task027D files: **447 passed**,
  165.319 s, zero skips, with source imports resolved from the export.
- YAML parsing, Python compilation, tracked diff whitespace and the staged-index
  privacy guard passed. No files were staged.
- Linux/Python 3.10 execution remains pending the changed CI workflow after an
  authorized push. No cross-version success is claimed before that run passes.
- The CI and scratch-boundary remediation still requires the requested second
  independent review before commit/push authorization.

Tests initially exposed a buffered broken-pipe cleanup masking the original
integrity error, and macOS group-signal denial leaving child resources uncollected.
The final implementation preserves the original error, falls back to terminating
the directly owned child when necessary, reaps it and closes its streams. The
explicit sandbox-denial assertion is in
`test_child_timeout_and_excess_output_fail_closed`.

All success claims above concern ephemeral synthetic local round trips only.
Real capture, owner-key custody, Linux/CI validation, remediation re-review,
engine trust-chain recovery, remote readback and disconnected-copy tests remain
separate gates. **NO VERIFIED OFFSITE BACKUP**.
