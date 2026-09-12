# TASK033B1 — Prior-result provenance and candidate-freeze plan

## Status and authority

This document designs the Task033B Gate 0 procedure. It does not inspect local
experiment artifacts, parse metric tables, open a holdout, run an experiment,
select a model, change xFP, alter operational evidence or authorize Task026C.

Task033A and Task033B remain the governing research boundary. Repository source
establishes the expected Task009/010 manifest shapes and identities, but only an
authorized, hash-verified local manifest can establish the result of a real run.

## Completed provenance checkpoint (2026-09-12)

Task033B1A is implemented at `251a4c484b745346fdb1a46c674a13add0c7744c`.
The owner separately authorized one capture and one verification, followed by
independent sanitized-result review reported SAFE. The hash-verified sanitized
report records `LOCALLY_COHERENT_LEGACY_RESULTS`, no development winner and no
holdout evaluation in either slot. Present local coherence does not prove
historical generator identity, immutability at creation, backup or promotion.

See the [Task033B1B amendment](TASK033B1B_CANDIDATE_FREEZE_AMENDMENT.md) for the
verified public hashes, exact recorded decisions and proposed candidate freeze.
The procedure below remains the historical contract, not an instruction to
repeat Gate 0. The numerical amendment still needs review and owner approval;
no experiment or confirmation access is authorized by this status correction.

## Decision

Split Task033B1 into two ordered gates:

1. **Task033B1A:** implement, test and independently review a read-only,
   manifest-level provenance verifier using synthetic fixtures only.
2. **Task033B1B:** after separate owner authorization, run that verifier against
   the two explicit local experiment directories and independently review only
   its sanitized report. Then amend the research protocol with literal UM1/UA1
   formulas, parameters, thresholds and confirmation permissions.

Task033B1 is incomplete until both gates finish. Candidate design must not run
in parallel with the real provenance read because result knowledge could shape
the candidate family. No formula or numerical gate may be backdated to before
Task033B1B.

## Repository-established targets

The verifier accepts exactly two logical slots:

| Slot | Expected version | Contract reference commit | Source blob |
|---|---|---|---|
| `minutes` | `minutes-v02-experiment-v1` | `c5269ed4421b8937a8fc62ca35029af829345f92` | `b58a47d84e48026335b235eb9e54b1fb685d471f` |
| `attacking_rates` | `attacking-rate-v02-experiment-v1` | `391b04328754ad95bf4304c438826688d01ad7e0` | `566b27cf438ef1821ba021d6080745db883c96bb` |

Both use `experiment_manifest.json`. Git verifies that each file is unchanged
between its reference commit and the Task033B1 design base. Task033B1A tests
must pin these identities rather than silently accepting later source bytes.

Each slot requires an explicit absolute experiment-directory argument. Because
no repository-established manifest digest exists yet, the first authorized
phase captures each manifest as opaque bytes and publishes its digest. The
second phase requires those exact digests before semantic verification. The tool
never searches `data/`, chooses the newest directory, follows a
manifest-provided absolute location or accepts an arbitrary experiment version.

Standard repository paths documented in README are navigation hints only. Their
existence or name does not prove a completed run.

## Threat model

The procedure must fail closed against:

- a missing, malformed, incomplete or wrong-version manifest;
- symlinks, special files, parent traversal and path substitution;
- output paths that are absolute, contain `..`, contain separators beyond one
  file name or escape the exact experiment directory;
- duplicate output names or duplicate logical experiment slots;
- changed files during hashing;
- manifest/output hash or size disagreement;
- inconsistent winner, holdout and final-decision states;
- a manifest claiming live-model modification;
- accidental parsing of Parquet tables;
- private absolute paths leaking through `immutable_inputs` or error messages;
- partial success presented as verified provenance; and
- publication into Git-tracked or staged locations.

This is local integrity verification, not disaster recovery. The resulting
evidence retains **NO VERIFIED OFFSITE BACKUP** unless Task027E/F later proves
otherwise.

## Filesystem boundary

Task033B1A should reuse or extract the descriptor-relative, no-symlink and
change-detection principles in `scripts/inventory_private_evidence.py`:

- open every directory component with `O_DIRECTORY|O_NOFOLLOW`;
- open regular files relative to the verified directory descriptor;
- reject non-regular files and links;
- compare device, inode, mode, size, mtime and ctime before and after reads;
- hash in bounded chunks; and
- reopen the named root after verification to prove it still identifies the
  same directory.

The implementation must not import or weaken the generic inventory command in a
way that changes Task027C behavior. A small shared helper is permitted only with
existing tests plus new regression coverage proving identical safeguards.

## Semantic manifest validation

Parse only `experiment_manifest.json` as UTF-8 JSON. Verify passes the exact
byte buffer produced by one stable descriptor-relative read to both SHA-256
validation and the strict JSON parser; it must never reopen the manifest by path
or perform a second read for parsing. Reject duplicate JSON keys, non-finite
numbers and unexpected top-level types. Validate these common fields:

- `status == "complete"`;
- exact `experiment_version` for the logical slot;
- expected historical classification;
- `model_formula_frozen == "xfp_v01"`;
- `live_model_modified is false`;
- development season `2023-24` and holdout season `2024-25`;
- target gameweeks exactly 2–38;
- exact candidate identities from committed source;
- literal development thresholds and tie-breakers equal committed constants;
- `development_winner` is null or one declared non-control candidate;
- `holdout_evaluated` is exactly equivalent to a non-null winner;
- `holdout_passed` is null when no holdout was evaluated and Boolean otherwise;
- `final_decision` is one of the exact strings emitted by the corresponding
  committed implementation and agrees with `holdout_passed`;
- `generation_timestamp` is valid UTC;
- `immutable_inputs` is a non-empty list with unique path/hash pairs; and
- `outputs` is a non-empty list with unique safe file names, non-negative rows,
  positive byte sizes and lowercase SHA-256 values.

Candidate definitions, observation/cutoff policy, coverage policy and thresholds
must match the committed implementation revision used by the verifier. The
report binds that exact Git commit and verifier-file hash. A later source change
requires a new reviewed compatibility definition; the verifier must not compare
an old manifest to whatever code happens to be current.

## State consistency

For each slot enforce:

| Manifest state | Valid interpretation |
|---|---|
| winner null; holdout not evaluated; holdout pass null | Development produced no winner; holdout remained closed |
| winner declared; holdout evaluated; pass false | Candidate failed confirmation |
| winner declared; holdout evaluated; pass true | Candidate became eligible for a later design only |

Any other combination is invalid. Even the final row never proves that xFP was
promoted; `live_model_modified` must remain false and repository production
identity is checked separately.

The minutes manifest's oracle reference is evaluation-only and may be validated
for shape but must not be reported as model performance. The attacking-rate
manifest's position-prior and frozen-component declarations must match source.

## Opaque output verification

For every `outputs` entry, open the named file as opaque bytes and verify its
declared size and SHA-256. Do not import DuckDB, PyArrow or any Parquet library;
do not inspect schemas, row groups, statistics, metadata or values. The declared
row count is copied from the verified manifest for inventory completeness but is
not independently reproduced and is labelled `MANIFEST_DECLARED`.

Gate 0 verifies only the output files declared by the manifest. It does not
enumerate the experiment directory or claim that the directory contains no
additional undeclared files. Such files are outside this narrow assurance and
must not appear in either result record.

`immutable_inputs` may contain absolute or repository-relative historical paths.
The verifier validates their strings and declared hashes as provenance claims
but does not open them during Gate 0. Revalidating historical inputs would widen
the authorized read beyond the two experiment directories and belongs to a
separate experiment-reproduction task.

The manifest itself is hashed only after a stable descriptor-relative read, and
the identical in-memory bytes are used for semantic parsing.

## Assurance boundary

The legacy manifests do not record the generator Git commit or source-file
hash. They are also local, unsigned evidence without an independently retained
creation-time digest. Gate 0 can establish that the bytes captured now are
internally coherent, match a reviewed contract implementation and bind the
present output files. It cannot prove which historical code bytes executed,
that the directory was never altered before capture, or that the declared
generation timestamp is externally authentic.

Accordingly, `VERIFIED` in the parent design is represented operationally as
`LOCALLY_COHERENT_LEGACY_RESULT`. It is sufficient to prevent accidental reuse
of an old candidate and to preserve the manifest's recorded decision. It is not
a cryptographic attestation, independent backup receipt or production-promotion
record. Generation time, row counts and immutable-input hashes remain explicitly
`MANIFEST_DECLARED` unless separately verified.

## Private result record

Write the complete Gate 0 result below an explicit ignored `data/operations/`
destination with owner-only directory and file permissions. Publication is
exclusive and no-overwrite. The private record contains:

- format and verifier version;
- logical slot;
- manifest SHA-256 and byte size;
- exact validated semantic fields;
- declared immutable-input path/hash claims;
- every output name, declared rows, verified bytes and verified SHA-256;
- source commit and verifier hash;
- verification timestamp; and
- status `LOCALLY_COHERENT_LEGACY_RESULT`, `NOT_LOCATED` or `INVALID`.

The two slots are verified independently but the combined Gate 0 status is
`LOCALLY_COHERENT_LEGACY_RESULTS` only when both are coherent. `NOT_LOCATED` and
`INVALID` remain distinct and neither permits old-candidate reuse.

## Sanitized report

Create a separate sanitized JSON report suitable for independent review. It may
contain only:

- format/verifier version and source commit;
- logical slot and expected experiment version;
- manifest SHA-256 and size, or `NOT_LOCATED`;
- development winner identity or `none`;
- holdout evaluated/pass state;
- exact final decision;
- output **logical file names**, sizes and verified hashes;
- counts of immutable-input claims and outputs;
- validation status and fixed reason code; and
- the assurance limitation above in a fixed code and human-readable sentence;
- the hash and size of the private result record.

It must replace repository, home and temporary-directory prefixes with stable
tokens and contain no absolute path, username, email, entry ID, raw metric,
player row, credential, environment value or command transcript. Error output
uses fixed reason codes and never echoes input paths or JSON fragments.

Before release, scan the sanitized bytes for known path prefixes and sensitive
patterns. Treat a clean scan as a necessary check, not proof that arbitrary
private content is absent.

## CLI boundary

Task033B1A may add one command with two explicit modes. Capture reads only the
two manifest files as opaque bytes and writes an immutable digest receipt:

```text
python scripts/verify_prior_experiment_provenance.py capture \
  --minutes-directory <ABSOLUTE_DIRECTORY> \
  --attacking-rates-directory <ABSOLUTE_DIRECTORY> \
  --digest-receipt <ABSOLUTE_IGNORED_JSON>
```

Verify parses only manifests whose bytes match that captured receipt, then
hashes every declared output as opaque bytes:

```text
python scripts/verify_prior_experiment_provenance.py verify \
  --minutes-directory <ABSOLUTE_DIRECTORY> \
  --minutes-manifest-sha256 <CAPTURED_SHA256> \
  --attacking-rates-directory <ABSOLUTE_DIRECTORY> \
  --attacking-rates-manifest-sha256 <CAPTURED_SHA256> \
  --private-output <ABSOLUTE_IGNORED_JSON> \
  --sanitized-output <ABSOLUTE_IGNORED_JSON>
```

The digest receipt and both result paths must be explicit, absent before
execution, owner-only and beneath the repository's ignored `data/operations/`
tree. Capture emits no semantic result and never parses JSON. Verify rejects a
manifest changed since capture before parsing it. Inputs are paths and captured
hashes only. No result, metric, key or credential appears in arguments or
environment variables.

The command performs no discovery and has no option to parse tables, skip hash
checks, overwrite output, reveal private paths or continue after invalid state.

## Tests

All Task033B1A tests use synthetic directories and manifests. Required cases:

- valid no-winner, failed-holdout and passed-holdout states for each slot;
- capture reads only manifest bytes, produces no semantic fields and binds both
  logical slots atomically;
- verify rejects a manifest changed after capture and never parses changed bytes;
- exact semantic mismatch for every fixed version/candidate/threshold field;
- wrong expected manifest hash and changed manifest during read;
- missing, extra, duplicate, absolute, nested and parent-traversing outputs;
- undeclared directory files are neither opened nor included in the assurance;
- output hash/size mismatch, replacement during hashing and special files;
- symlink at root, manifest and output positions;
- duplicate JSON keys, invalid UTF-8, non-finite values and malformed JSON;
- inconsistent winner/holdout/final-decision combinations;
- immutable inputs are not opened even when their paths exist;
- no DuckDB/Parquet dependency or parser is invoked;
- exclusive publication and owner-only permissions;
- failure publishes neither a partial private record nor sanitized success;
- sanitized output rejects/leaks no absolute path or seeded sensitive token;
- output destination outside ignored `data/operations/` is rejected; and
- current Task027C inventory behavior remains unchanged if code is shared.

No test reads real `data/`, private evidence or a sealed holdout.

## Review and authorization sequence

1. Review this design independently.
2. Commit the design only after a SAFE verdict and owner authorization.
3. Implement Task033B1A on synthetic fixtures only.
4. Independently review implementation, privacy and adversarial filesystem
   behavior; commit only after owner authorization and green CI.
5. Prepare exact real input/output paths. Obtain explicit owner authorization
   for the two-phase Gate 0 read.
6. Run opaque capture once, bind the two digests, then run verify once against
   those exact digests. Verify private and sanitized record hashes and stop.
7. Independently review the sanitized result only. The reviewer does not open
   local experiment directories or private result records.
8. Record the Gate 0 outcome without committing private paths or metric data.
9. Draft the Task033B1B candidate amendment. Freeze formulas, parameters,
   bootstrap rules, literal thresholds, cross-season aggregation, family-wise
   procedure, synthetic-corpus decision and confirmation permission.
10. Obtain Claude review and owner approval before Task033C implementation.

## Failure behavior

| Failure | Response |
|---|---|
| Either captured manifest hash is unavailable | Stop before semantic parsing or output hashing |
| Slot missing | Publish no combined success; record `NOT_LOCATED` privately only after authorized execution |
| Semantic or byte validation fails | Publish no combined success; fixed `INVALID` reason only |
| Output changes during hashing | Fail the slot; preserve no partial success |
| Sanitization check fails | Keep private record local; release no review report |
| Primary monitor or exact operational checkout would be disturbed | Stop; use isolated synthetic work only |
| Gate 0 result is unknown or invalid | Specify only a materially new family; do not reuse M1–M3/S1–S3 |
| Gate 0 proves prior failure | Preserve it; do not relax, rename or immediately rerun the failed family |
| Gate 0 proves prior design eligibility | Treat as historical comparator evidence; do not infer production promotion |

## Task033B1 acceptance criteria

- Real result claims can originate only from validated manifests.
- Validation is described as present local coherence, never historical code
  attestation or proof of creation-time immutability.
- Parquet outputs are hash-verified as opaque bytes and never parsed.
- Historical immutable inputs are not opened during Gate 0.
- Both logical slots are explicit; semantic verification requires the exact
  hashes from the preceding opaque capture and no discovery occurs.
- Manifest hashing and parsing use the same stable descriptor-relative byte
  buffer, with no second path-based read.
- Assurance covers only manifest-declared outputs and makes no directory-
  exhaustiveness claim.
- Private and sanitized records are distinct, immutable and untracked.
- Sanitized evidence reveals no paths, raw metrics or player data.
- Candidate freezing happens only after Gate 0 and through a newly reviewed
  amendment.
- A positive old experiment never becomes automatic production authority.
- A negative or missing old experiment cannot be disguised as fresh evidence.
- The active GW4 monitor, live decisions and sealed holdouts remain untouched.

## Explicit non-goals

- Recovering missing files from backups or conversations.
- Recomputing any Task009/010 metric or row count.
- Verifying every historical input file.
- Judging whether an old threshold was statistically ideal.
- Opening the Task018D 2025/26 holdout.
- Selecting UM1/UA1 before provenance is known.
- Changing the current transfer or captaincy recommendation.
