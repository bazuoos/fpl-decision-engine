# TASK027D — Encrypted checkpoints and isolated restore

Status: implementation specification; not an implemented backup or deployment approval.
Base inspected: `2a0f389c38021bc81717c7b003e71461aa9b4785` on `main`.
Task027C independent Claude review passed. Its CI run
[33871404131](https://github.com/bazuoos/fpl-decision-engine/actions/runs/33871404131)
completed successfully for this exact base SHA, verified during this spec task.

## Objective and delivery gates

Preserve the exact bytes currently available, encrypt them before storage, and
prove an isolated restore reproduces those bytes without changing production
evidence. Missing original evidence remains missing; encryption does not fill gaps.

1. Codex implements the local checkpoint/verification/restore tooling and synthetic
   acceptance tests below. No real private-data execution or provider access.
2. Claude independently reviews the implementation; Codex addresses agreed
   blockers, followed by verification. Commit/push requires explicit authorization.
3. After review, establish owner-controlled recovery keys and B2 account settings.
   Confirm a concrete upload destination, contents, retention and access policy.
4. Create the first authorized real checkpoint, verify decryption, upload and
   read back ciphertext, then restore in a clean environment. Add an independent
   disconnected copy. Report actual coverage and measured recovery times.

This spec defines gate 1 in detail and records requirements for later gates.
It does not authorize cloud configuration, upload, purchases, production key
creation, hooks/scheduling, retention deletion or use of real evidence now.
No Task026C, FPL refresh, journal creation, model promotion or holdout evaluation.

## Evidence baseline and assurance

Task027B recorded 3,020 files / 135,598,838 bytes under `data/`. This is a dated
inventory, not a frozen expectation for future backups. Public-source snapshots
can be historically irreplaceable; derived bytes may be referenced by hashes.
Back up the full authorized tree, preserving paths and bytes, rather than
selectively dropping data believed to be regenerable.

Original manager inputs and source evidence referenced by two operational runs
are **NOT LOCATED** under the inspected tree. The owner reports that screenshots
were temporary. Do not search other private locations automatically, reconstruct
original inputs, invent timestamps, or label this as definitive loss.

Keep these independent statuses in reports:

- byte capture: succeeded/failed;
- cryptographic decryption and archive verification: succeeded/failed/not run;
- original-evidence coverage: known gaps/not assessed (never inferred complete);
- engine trust-chain validation: succeeded/failed/not run;
- offsite readback and independent-copy verification: succeeded/failed/not run.

An intact archive with known evidence gaps is useful, but is not complete evidence
recovery. A local round trip is not disaster recovery from loss of the Mac.

## Scope and interfaces for Codex

Implement standalone tooling under `scripts/`, focused tests under `tests/`,
and associated task documentation. Reuse the Task027C inventory mechanisms where
appropriate; any shared-helper changes must preserve its CLI and existing tests.
Keep engine, application, frontend, artifact contracts, RFCs and live data unchanged.
Do not install a hook or alter CI merely to bypass an unavailable dependency.

Provide three explicit operations, with final CLI names documented by Codex:

- **create**: explicit source root, committed source revision/repository, public
  recipient file, output parent and private coverage declaration;
- **verify**: explicit checkpoint, identity file and owner-private scratch parent;
  authenticate, parse and hash every archived file, with no persistent plaintext
  extraction or fallback to the OS default temporary directory;
- **restore**: explicit checkpoint, identity file, owner-private scratch parent and
  new isolated destination; perform the same checks and publish only a fully
  verified restored tree.

No discovery of `latest`, silent default to the real `data/`, automatic execution
of restored code, reading credentials from repository files, or network calls.
Do not log identity contents, private paths, filenames, source-root names, command
stderr containing private metadata, or manifest payloads by default. Output stable
failure codes and aggregate assurance states. A private diagnostic mode, if needed,
must be deliberate and must not print keys.

## Cryptography and dependency boundary

Use the established `age` executable; do not implement cryptography. Initially
support native age recipients/identity files only; defer passphrase prompts,
SSH recipients, plugins and hardware-token integration. Creation needs public
recipient material only. Identity files must remain outside captured scope.

Codex's read-only feasibility check found no `age` executable on PATH. Installation
in the implementation/test environment is still needed before real-crypto tests
can pass; no installation or production key creation occurred in this spec task.

Use subprocess argument arrays and controlled pipes. Resolve/check the executable
before any source capture or output publication. A missing/broken tool fails
clearly; never fall back to plaintext. Pin and record the tested age version and
document provenance verification before installation. Do not embed key material
in arguments, logs, source, test fixtures or Git.

Use ephemeral synthetic keys for real-crypto integration tests only, in isolated
temporary locations; do not confuse these with owner recovery credentials.
Mock subprocess failures where useful, but fake encryption cannot satisfy the
successful encryption/decryption acceptance gate. If age is unavailable, report
that gate as unexecuted; do not silently skip it and report full success.

## Checkpoint format and creation

Use a versioned archive encrypted as one age object. Keep encrypted content
self-contained for its declared scope: data bytes, private inventory/coverage
manifest, and a Git bundle containing the explicitly recorded commit and its
required history. Do not include local Git configuration, credentials, untracked
work, other branches/stashes, or arbitrary home-directory files in that bundle.
Verify the committed source identity; never represent dirty tracked code as HEAD.

Prefer an uncompressed archive initially to avoid compression-bomb complexity.
Specify manifest-first order and a fixed payload prefix. Support the existing
long operational paths through an explicitly supported archive representation;
do not silently truncate them. Reject sparse members and unsupported extension
headers; any supported long-path metadata must pass the same path checks.

The inner manifest must identify format version, opaque checkpoint ID, capture
time (not an FPL observation time), source commit, tested tool versions, logical
roots, file count/total bytes, and every regular file's relative path, size and
SHA-256. Include repository-bundle identity and explicit coverage gaps. Preserve
source path-layout requirements privately; do not rewrite embedded absolute paths.

An outer completion receipt may expose only format/checkpoint identity, ciphertext
size/hash and status. It must not expose private inventories or keys. A receipt
stored beside ciphertext is not an independent authenticity anchor; later remote
verification must use an explicitly recorded trusted checkpoint/object version.
Public-key encryption alone does not authenticate who created a checkpoint.

Creation requirements:

- Require quiescent input and retain Task027C's no-follow/change detection. Hash
  the exact bytes streamed into the archive; an earlier inventory followed by
  unchecked reopening is insufficient. Reconcile final enumeration and metadata.
- Reject symlinks, special files and unsafe/ambiguous archive paths. Detect
  additions, removals or modifications and refuse successful publication.
- Reject output/key locations inside source scope and source/output overlap.
  Use an explicit private output parent outside the repository for initial v1.
- Stream private archive plaintext to age; no plaintext evidence archive on disk.
  Bound memory and subprocess output; drain stderr safely without leaking it.
- Create only private, uniquely named staging output, restrictive permissions,
  ciphertext integrity checks and no-overwrite publication. Existing checkpoint
  paths must never be replaced, including during races or retries.
- Encryption success alone is `CREATED`, not `RESTORE_VERIFIED`. Publish a
  completion receipt only after all capture/encryption/hash checks finish.
- Interrupted work must be recognizable as incomplete. Cleanup may remove only
  the current operation's own temporary files, never pre-existing paths.

A multi-file checkpoint directory can simplify publication. Codex must document
and test its exact no-clobber and crash behavior on macOS/Linux. A pre-existence
check followed by an overwriting rename is not adequate. Do not claim an atomic
filesystem snapshot or power-loss durability without implementing the mechanisms.
An exclusively created, randomly named operation directory with a final completion
marker is acceptable if it cannot be mistaken for complete before that marker.
For single-file publication, an exclusive hard-link operation can provide local
no-replace semantics; unsupported filesystems must fail rather than overwrite.

## Verify and restore safety

Treat even a decryptable archive as untrusted structured input. Require supported
format versions, exact manifest fields, unique paths and matching member sets.
Verify sizes and hashes for every file, including the repository bundle.

Reject absolute paths, parent traversal, symlinks/hardlinks, devices/FIFOs,
duplicate members, case/Unicode aliases on supported destination filesystems,
file/directory collisions, malformed manifests and unlisted/missing members.
Use documented limits for archive bytes, uncompressed bytes, file count, path
length/depth and manifest size; fail on limit breaches rather than allocate
without bounds. Do not rely solely on `tarfile.extractall` defaults.

Consume the complete decryption stream and require age's successful exit. A
parser reaching the end of an archive is not sufficient evidence that the entire
encrypted object authenticated. Truncation, trailing-data policy violations and
late subprocess failures must prevent success and final restore publication.

Restore only to a new isolated location outside the repository and source tree;
never overlay `data/`. Require an explicit existing owner-private scratch parent
for verify and restore; temporarily materialized repository bundle/Git objects must
not default to the shared OS temporary location. Write plaintext only on an
explicitly chosen recovery filesystem. Validate before final publication;
do not restore archive ownership, executable privileges or other unsafe metadata.
Never follow destination symlinks. A failed restore may leave private incomplete
staging after a crash; document controlled cleanup and do not promise secure erase.

Byte restore does not itself validate FPL semantics. The later real drill must use
existing trusted readers against the restored chain and recorded source revision,
without refresh/resume writes or fabricated pre-deadline evidence. Existing
absolute paths may require an isolated environment reproducing the original
layout; do not silently edit manifests or claim relocation support is proven.

## Required synthetic acceptance cases

1. Real age create/verify/restore round trip with matching complete inventories
   and repository revision, using ephemeral test keys only.
2. Source bytes/index unchanged; keys and unrelated files absent from archive;
   default logs contain neither paths nor private payloads.
3. Wrong key, missing executable, nonzero/late child exit, broken pipe, truncated
   or modified ciphertext, and failure after archive parsing all fail closed.
4. Existing output/restore paths and concurrent creation cannot be overwritten;
   source/output/key overlap and symlinked parents are rejected.
5. File mutation/replacement and tree addition/deletion during packaging fail;
   archive hashes describe streamed bytes rather than stale inventory values.
6. Malicious archives exercising every unsafe member class and declared resource
   limit fail before final destination publication.
7. Unknown versions, malformed/duplicate manifest fields, missing/extra members,
   hash/size mismatch and altered bundle identity fail validation.
8. Explicit known-evidence gaps survive the round trip and cannot be relabelled
   complete by successful encryption, inventory or restore.
9. Existing Task027C tests remain passing; fresh-checkout execution needs no real
   `data/`, credentials, networked service or sealed performance inspection.

## Owner setup and real-drill gates (later)

Backblaze B2 is owner-selected. Account/region, bucket, retention, restricted
uploader access, separate recovery/deletion access, MFA and off-laptop key
recovery need guided setup. The external drive is not yet selected. Do not
quietly provision any of these as part of local implementation.

Before real capture, review the exact scope and evidence-gap declaration, prove
the recovery key is usable and has an independent recovery copy, and confirm
where ciphertext and temporary restored plaintext may be written. An old
source-evidence gap need not stop preserving available bytes, but must be reported.

Then perform authorized upload/readback and restoration without access to the
original Mac/data. Test the external copy independently and measure actual
RPO/RTO. Until these gates pass, report `NO VERIFIED OFFSITE BACKUP`.

## Review handoff

Codex returns exact base SHA, changed/new files, complete diff and new-file
contents, format/CLI description, threat assumptions, dependency versions,
commands/results and unresolved limitations. Claude reviews independently;
remediation returns to Codex. Do not stage, commit or push without authorization.

Primary references used for this design:

- [age official usage and release verification](https://github.com/FiloSottile/age)
- [Python tarfile extraction security](https://docs.python.org/3/library/tarfile.html)
- [Task027C local boundaries](TASK027C_LOCAL_BACKUP_READINESS.md)
- [AI workflow](AI_WORKFLOW.md)
