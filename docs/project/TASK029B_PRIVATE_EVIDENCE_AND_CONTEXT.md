# TASK029B — Private source evidence and strategy context

## Status and authority

This document describes the implementation committed at
`cb72a78bf2a9ebcad70103fa70ad75abf7e98526`, based on the independently reviewed
[Task029A design](TASK029A_EVIDENCE_PROVENANCE_AND_STRATEGY_CONTEXT_SPEC.md).

The tooling is local operational support. It does not become part of the
trusted decision path, create a journal entry, validate football truth or
authorize an FPL action. It does not scan for evidence or upload anything.

Implementation and automated tests used synthetic bytes only. After independent
review, commit and successful CI, the owner separately authorized one real local
source capture and historical-context population. The sanitized outcome is
recorded in [Task029C](TASK029C_PRIVATE_EVIDENCE_CAPTURE_AND_CONTEXT_POPULATION.md).
That later operation did not change this implementation or establish an offsite
copy. **NO VERIFIED OFFSITE BACKUP** remains true.

## Delivered boundary

[`scripts/private_evidence_context.py`](../../scripts/private_evidence_context.py)
provides five explicit operations:

| Operation | Result |
|---|---|
| `capture-source` | Copies one explicitly named source into an owner-private, content-addressed store and publishes a separate immutable observation record |
| `verify-source` | Revalidates canonical manifests, path identities, permissions, byte length and SHA-256 |
| `verify-manager-binding` | Confirms only that a verified-manager-evidence source hash equals the captured content hash |
| `create-context` | Publishes one immutable human-context snapshot from an explicit private input file |
| `verify-context` | Revalidates canonical bytes, semantic identity, time classification, storage scope and typed references |

The module also exposes `build_comparison_view` for a future private consumer.
It returns three separately named layers: historical human context, trusted
engine references and current evidence. It performs no optimization, legality,
lineup, transfer, captaincy, xFP or reliability calculation. The current-
evidence layer explicitly says that Task029B has not established freshness, and
the engine layer directs semantic consumers to the trusted engine reader.

The public exact schemas are:

- [`private-source-content-v1`](../../contracts/private/v1/private-source-content-v1.schema.json)
- [`private-source-evidence-v1`](../../contracts/private/v1/private-source-evidence-v1.schema.json)
- [`human-strategy-context-v1`](../../contracts/private/v1/human-strategy-context-v1.schema.json)

These schemas describe structure only and contain no private instance data.
Runtime readers additionally enforce canonical JSON, semantic identities,
cross-field hashes, exact UTC form and storage-path binding.

## Source model

One byte object has one SHA-256 address:

```text
<evidence-root>/source-v1/sha256/<first-two>/<sha256>/
  source.bin
  content_manifest.json
  observations/<evidence_record_id>/source_evidence.json
```

Receiving the same bytes twice reuses `source.bin`. Each different observation
time or provenance receives a different evidence record. This prevents one
submission from overwriting another submission's history or forcing two
provenance claims to agree.

Task029B copies an existing file. It therefore always records:

```text
observed_at: null
observation_basis: NOT_SYSTEM_PROVEN
temporal_status: UNKNOWN
```

`ingested_at` proves only when the writer captured those bytes. An optional
owner-reported observation timestamp remains explicitly non-authoritative. It
cannot prove when a screenshot was taken or satisfy a pre-deadline evidence
gate. EXIF, PNG metadata, filesystem times, filenames, chat ordering and OCR are
not inspected or trusted.

## Context model

Context records use an explicit `record_type=HUMAN_STRATEGY_CONTEXT` and
`authority=HUMAN_CONTEXT_ONLY_NOT_ENGINE_INPUT_OR_FPL_ACTION`. They cannot be
mistaken for `DecisionJournalEntry` merely because both use the values
`PROSPECTIVE` and `HISTORICAL_BACKFILL`.

Prospective context requires season, gameweek and official deadline. The
writer's UTC time must be strictly before that deadline. Historical backfill may
be unscoped and has no field capable of claiming an original human-action time.
Actual action remains exclusively in the existing decision journal.

New records may link to an earlier record with `SUPPORTS`, `CHALLENGES` or
`SUPERSEDES`. The writer verifies and hashes the earlier canonical record; it
never edits it. Provisional BUY/HOLD/SELL/WATCH/funding labels are stored only
as time-bound human context.

Evidence references are resolved from validated evidence-record files and bind
both the observation-record SHA-256 and underlying content SHA-256. Engine
references require an explicit artifact path and expected SHA-256 at creation.
Only the declared artifact type, schema version, semantic ID and verified hash
are retained; absolute paths are not stored. Hash matching does not replace the
trusted engine reader or prove that caller-supplied descriptive identifiers are
semantically correct.

## Private input templates

Prepare inputs only in an owner-private local directory outside Git. Do not put
credentials, cookies, recovery keys or passwords in narrative fields.

Source observation input has exactly these fields:

```json
{
  "source_class": "OWNER_SUPPLIED_MANAGER_SCREEN",
  "media_type": "image/png",
  "sensitivity": "MANAGER_PRIVATE",
  "provenance_description": "Owner supplied the current transfer screen.",
  "owner_reported_observed_at": null,
  "season": "2026-27",
  "target_gameweek": 4,
  "official_deadline": "2026-09-12T12:30:00.000000Z"
}
```

All three scope values may instead be null. Partial scope is rejected. The
accepted source classes and sensitivities are enumerated in the schema and
tool. The maximum source size is 64 MiB.

Context input has exactly these fields:

```json
{
  "temporal_classification": "HISTORICAL_BACKFILL",
  "season": null,
  "target_gameweek": null,
  "official_deadline": null,
  "context_kind": "DURABLE_STRATEGY",
  "reasoning": "Example private historical strategy narrative.",
  "assumptions": ["Example time-bound assumption."],
  "change_conditions": ["Fresh evidence contradicts the assumption."],
  "provisional_posture": null,
  "evidence_record_paths": [],
  "engine_references": [],
  "relations": []
}
```

An evidence path points to an existing `source_evidence.json`. An engine input
uses this private creation-only shape:

```json
{
  "artifact_path": "/explicit/private/or/repository/artifact.json",
  "artifact_type": "GameweekDecision",
  "artifact_schema_version": "1.0.0",
  "semantic_id": "decision_<64 lowercase hex characters>",
  "expected_sha256": "<64 lowercase hex characters>"
}
```

A context relation uses:

```json
{
  "relation": "CHALLENGES",
  "context_path": "/explicit/private/context.json"
}
```

Paths exist only in the private input and process invocation. Published records
and normal command output omit them.

## Commands

Use the repository virtual environment. The following placeholders must be
replaced with explicit paths; the tool never discovers `latest` or scans the
computer.

```bash
.venv/bin/python scripts/private_evidence_context.py capture-source \
  --source /explicit/source-file \
  --evidence-root /absolute/repository/data/evidence \
  --observation-input /explicit/private/observation.json

.venv/bin/python scripts/private_evidence_context.py verify-source \
  --record /explicit/private/source_evidence.json

.venv/bin/python scripts/private_evidence_context.py verify-manager-binding \
  --record /explicit/private/source_evidence.json \
  --manager-evidence /explicit/private/verified-manager-evidence.json

.venv/bin/python scripts/private_evidence_context.py create-context \
  --context-root /absolute/repository/data/context \
  --input /explicit/private/context-input.json

.venv/bin/python scripts/private_evidence_context.py verify-context \
  --record /explicit/private/context.json
```

The only accepted destination roots are the repository's absolute
`data/evidence` and `data/context` paths. This keeps private outputs inside the
root-anchored Git-ignore and staged-path guard boundary and inside full-data
checkpoint scope. A destination root is created with mode `0700` when its
existing parent is safe and the leaf does not exist. An existing root must be
owned by the current user and must deny group/other access. Private JSON input
files and stored files use `0600`; directories use `0700`.

Verification and reference resolution enforce the same canonical roots; a
lookalike store elsewhere is rejected. The enclosing `data` directory must be
a real owner-owned directory rather than a symlink.

Successful output contains only stable status, IDs, hashes, byte count and reuse
state. Failures return a stable error code without a private path or payload.
The tool rejects known age/private-key markers in JSON input, but this narrow
check cannot prove that arbitrary narrative contains no secret. The owner must
keep secrets out of the inputs.

## Filesystem and immutability behavior

- Input and stored files must be owned, single-link regular files.
- Final-component symlinks, hardlinks, directories, devices, FIFOs and sources
  over 64 MiB fail closed.
- Ancestor symlinks such as macOS `/var` are resolved before directory-fd-
  anchored traversal; the final file is still opened with no-follow semantics.
- Source identity, size, timestamps and link metadata are compared before and
  after streaming. Replacement or mutation fails.
- Temporary publication occurs inside the owner-private destination filesystem.
- Files are fsynced and linked into place with exclusive no-overwrite semantics.
- Existing complete bytes are revalidated before reuse. Partial, noncanonical,
  permission-invalid or conflicting destinations fail; they are never repaired
  by overwrite.
- Published source bytes are streamed for hashing and never printed.

The writer may leave an incomplete immutable destination after abrupt process or
machine failure between exclusive publications. A later invocation reports the
partial state as invalid and does not repair it. Recovery requires separate
inspection and an explicitly reviewed archive/reset procedure; Task029B has no
delete command.

## Version and compatibility register

| Artifact | Current writer | Supported reader | In-place migration |
|---|---|---|---|
| `private-source-content-v1` | `task029b-v1` | v1 exact/canonical | Forbidden |
| `private-source-evidence-v1` | `task029b-v1` | v1 exact/canonical | Forbidden |
| `human-strategy-context-v1` | `task029b-v1` | v1 exact/canonical | Forbidden |

Future versions must keep v1 readers and synthetic golden examples. Adaptation
may occur in memory only and must retain the original version, bytes and hash.

## Privacy, recovery and operational limits

The intended roots under `data/evidence/` and `data/context/` are ignored by the
root `/data/` rule and rejected by the existing staged-path guard. This does not
make manual discipline unnecessary and does not install a Git hook.

Both roots fall inside a full authorized `data/` checkpoint automatically
because the inventory and encrypted-checkpoint tools traverse their explicit
source root generically. No real checkpoint, B2 upload, readback, disconnected
copy or restore drill has run. Task027E remains paused.

The planned B2 bucket applies irreversible 90-day compliance retention. If a
future checkpoint contains these private records, deleting a local record will
not delete the retained remote ciphertext before expiry.

`verify-manager-binding` proves one hash equality only. It does not inspect the
screenshot, validate the manager evidence fields, establish observation time or
revalidate a decision chain. `build_comparison_view` likewise labels already
verified references but remains outside decision authority.

The prospective-context gate compares two caller-environment values: the local
system clock and the supplied official deadline. Task029B does not independently
attest the clock or retrieve the official deadline, so downstream readers must
not treat the classification as external timestamp proof.

## Validation

Focused tests are in
[`tests/test_private_evidence_context.py`](../../tests/test_private_evidence_context.py).
They use only synthetic sources, manager files, engine-like bytes and strategy
narratives. They cover source mutation/replacement, symlink/hardlink/FIFO,
oversize input, private permissions, partial/conflicting output, canonical
bytes, hash corruption, owner-reported timing, strict prospective deadlines,
historical backfill, immutable relations, verified references, layer labels,
CLI privacy, schema validation and the engine import boundary.

Independent review preceded commit. Any further real evidence capture still
requires explicit owner authorization and the provenance rules above.
