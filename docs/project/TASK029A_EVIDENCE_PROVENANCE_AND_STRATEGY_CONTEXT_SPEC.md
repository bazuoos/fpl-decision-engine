# TASK029A — Evidence provenance and strategy-context foundation

## Status and authority

This is a design-only candidate based on
`cc30c9976247a5dbd6d778a8233f09b8790e3c5c`.

It does not capture, copy, move, rewrite or publish evidence. It does not create
a decision journal, alter an engine artifact, change model behavior, start
Task026C or activate Task028G. Repository code, schemas, tests, immutable
artifacts, manifests, frozen decision records and Git history remain
authoritative on technical contradictions.

The design records human-approved project policy and historical human context.
Those categories are not trusted engine truth. Player opinions, ownership
theses and labels such as HOLD, WATCH or funding candidate remain provisional
judgments made at a particular time. Current evidence may overturn them.

**NO VERIFIED OFFSITE BACKUP** remains true. Task027E remains paused until an
independent owner-controlled key-custody copy can be established.

## Problem

The repository preserves engineering continuity and trusted engine artifacts,
but two related gaps remain:

1. Original manager evidence supplied as screenshots can remain only in
   temporary operating-system storage. A manager-evidence artifact may retain
   the correct source hash while the corresponding source bytes disappear.
2. Detailed human strategy and player reasoning currently live mainly in
   conversations. A fresh control-room session can recover the engine but not
   reliably recover why the squad was built, what assumptions were held at a
   deadline, or what later evidence changed them.

One current owner-supplied screenshot was found in temporary storage during the
design inspection. Its SHA-256 exactly matches the source hash already recorded
in the corresponding private manager evidence, but its bytes are not durably
captured under `data/`. Earlier source screenshots are not located. This is a
recoverability gap, not proof that the existing manager-evidence artifact is
false. The temporary source path and private manager contents are deliberately
omitted from this public document.

The solution must preserve original bytes and human reasoning without allowing
either to become a second optimizer, a retroactive prospective record, or an
unreviewed input to xFP.

## Required epistemic separation

The project must keep these layers separate in storage, schemas, APIs and prose:

| Layer | Meaning | Authority and allowed use |
|---|---|---|
| Historical human thesis/research | What the owner or analyst believed, investigated or hypothesized at the recorded time | Context for comparison and learning; never engine truth or ownership protection |
| Trusted engine artifacts | Hash-verified production inputs, outputs, reliability diagnostics, manifests, journal entries and outcomes | Sole source of engine recommendation, legality, XI, bench, captaincy and decision semantics |
| Current refreshed football/manager evidence | Newly collected public facts and fresh private editable-manager evidence | Input to current analysis through its existing reviewed boundary; staleness and provenance remain explicit |

Population ownership, sampled-manager behavior and market trends are contextual
research. They are not expected-points evidence and cannot silently alter xFP,
optimization, reliability or model selection.

An LLM may retrieve, organize, compare and explain records from the three
layers. It must not merge them into an unlabeled narrative and must never
construct or repair transfers, squad legality, XI, bench, captain or vice
captain in prose.

## Prospective truth and historical backfill

Time classification is structural, not a narrative label.

- A `PROSPECTIVE` context record must be created by the repository-reviewed
  local context writer
  strictly before its explicit official deadline. Its creation time comes from
  the writer's UTC clock. A supplied timestamp cannot make a later record
  prospective.
- A `HISTORICAL_BACKFILL` records later testimony or recovered material. It
  retains its actual later creation time and cannot claim a verified original
  action time.
- `ingested_at` means only when bytes entered the evidence store. It does not
  prove when a screenshot was taken, viewed or used.
- Filesystem creation/modification times, filenames, chat ordering and OCR text
  do not independently prove an observation time.
- An owner-reported earlier time may be preserved as an explicitly unverified
  assertion, separate from system-captured time. It cannot satisfy a
  pre-deadline gate.
- A pre-deadline context record can show that a thesis existed. It does not show
  which FPL action the owner took.

Actual human action remains exclusively within `DecisionJournalEntry`. If an
action was not journaled before its deadline, a later account must remain
historical backfill. In particular, the GW3 roll rationale may be preserved as
historical strategy context, but must not be recreated as a prospective GW3
journal entry.

Later evidence never edits an earlier thesis. A new immutable record may cite,
support, challenge or supersede the earlier record. The earlier bytes and their
original classification remain unchanged.

## Scope

### Included in the later implementation

1. A private, content-addressed store for exact owner-supplied source bytes.
2. A canonical manifest for each source, with explicit sensitivity,
   provenance and temporal limits.
3. Immutable structured human-context snapshots with explicit temporal
   classification and typed links to source evidence and engine artifacts.
4. A read-only comparison path that returns labeled layers and never imports
   decision or optimization logic.
5. Synthetic tests for path safety, source-change detection, immutability,
   canonical identity, time boundaries and layer separation.
6. A private runbook for capturing a deliberately supplied file and verifying
   an existing manager-evidence source hash without displaying private content.
7. Inclusion of the new private roots in the existing full-data checkpoint
   scope and inventory classification.
8. Public documentation of durable reasoning rules that contains no manager
   identifier, credentials, private screenshots or current player verdicts.

### Excluded

- Automatically scanning chats, Downloads, Photos, browser profiles or the
  owner's home directory.
- Reconstructing missing screenshots or inventing their timestamps.
- OCR as authoritative manager state or proof of observation time.
- Storing FPL passwords, cookies, session tokens, backup keys or credentials.
- Feeding context records directly into xFP, the optimizer, reliability or
  model selection.
- Creating current football claims without fresh source retrieval.
- Constructing alternative transfers, lineups or captaincy outside the trusted
  engine/validator.
- Automatic model promotion, research consent, analytics export or training
  use of owner records.
- Changing `DecisionJournalEntry`, `GameweekDecision`, operational manifests or
  any existing immutable artifact.
- Uploading evidence, running a real checkpoint, configuring B2 or claiming a
  successful restore.

## Storage and trust topology

The minimum design stays file based and local:

```text
explicit owner-supplied file
    -> private source-evidence writer
         -> exact immutable bytes addressed by SHA-256
         -> canonical private-source-evidence-v1 manifest
                 |
                 +-> existing manager evidence may match the source hash
                 +-> context snapshots may reference the evidence ID/hash

historical/prospective human reasoning
    -> immutable human-strategy-context-v1 snapshot
         -> optional typed references to source evidence
         -> optional typed references to trusted engine artifacts
         -X-> no import into xFP/optimizer/reliability

current decision discussion
    -> retrieve trusted engine layer unchanged
    -> retrieve fresh evidence layer with staleness/provenance
    -> compare with historical human layer
    -> explanation only
    -> actual action, if any, uses existing trusted journal workflow
```

Suggested ignored private roots are:

```text
data/evidence/source-v1/sha256/<first-two-digest-chars>/<sha256>/
  source.bin
  content_manifest.json
  observations/<evidence_record_id>/source_evidence.json

data/context/fpl/<season>/records/<context_id>/
  context.json
```

The stored source filename is fixed and opaque. The original filename and
source path are not identity inputs and should be omitted unless an explicit
private diagnostic need is established. Media type is descriptive and cannot
change how the bytes are parsed by a trusted boundary.

The public repository may contain generic strategy principles in
`FPL_PRODUCT_PHILOSOPHY.md`. Detailed player theses, manager-linked context and
source evidence remain under ignored private storage. No public continuity file
should contain an entry ID or enough private detail to reconstruct manager
evidence.

## Private source-evidence contract

The content manifest binds only the source SHA-256, byte length, fixed storage
name and content-format version. `private-source-evidence-v1` is a separate
observation record. It should use exact fields and canonical JSON. At minimum
it binds:

- schema version and semantic evidence-record ID;
- SHA-256 and byte length of `source.bin`;
- controlled source class, such as `OWNER_SUPPLIED_MANAGER_SCREEN`,
  `OWNER_SUPPLIED_DOCUMENT` or `PUBLIC_RESEARCH_CAPTURE`;
- sensitivity classification: `MANAGER_PRIVATE`, `PERSONAL_CONTEXT` or
  `PUBLIC_SOURCE`;
- system-captured UTC `ingested_at`;
- nullable system-proven `observed_at` plus an exact observation-basis enum;
- optional owner-reported observation text/time, explicitly non-authoritative;
- optional season/gameweek scope and official deadline;
- a derived temporal status that distinguishes system-proven pre-deadline,
  post-deadline and unknown timing;
- a bounded provenance description that contains no credential;
- writer/tool version.

Neither identity may depend on a local absolute path. Identical source bytes may
be reused only when the existing bytes and content manifest validate exactly.
If two captures of the same bytes have different provenance, each receives a
separate immutable evidence-record child. The byte object is reused without
forcing the observation records to agree and without duplicating source bytes.

The implementation must not infer `observed_at` from EXIF, PNG metadata or
filesystem timestamps. A future reviewed extractor may preserve such values as
source claims, not as trusted clock evidence.

## Human strategy-context contract

`human-strategy-context-v1` should represent a snapshot rather than a current
player database. At minimum it binds:

- schema version and semantic context ID;
- `PROSPECTIVE` or `HISTORICAL_BACKFILL` temporal classification;
- system-captured UTC creation time;
- optional exact season/gameweek/deadline scope;
- controlled context kind:
  `DURABLE_STRATEGY`, `SQUAD_CONSTRUCTION_THESIS`,
  `PLAYER_THESIS_SNAPSHOT`, `DECISION_RATIONALE_CONTEXT`,
  `KNOWN_MODEL_LIMITATION` or `OPEN_HYPOTHESIS`;
- concise original reasoning and assumptions;
- evidence that would support, challenge or invalidate it;
- optional provisional posture such as BUY, HOLD, SELL, WATCH or funding
  candidate, always labeled as time-bound and non-authoritative;
- typed evidence references by evidence ID and SHA-256;
- typed engine references by artifact type, version, semantic ID and SHA-256;
- relations to earlier context IDs, such as `SUPPORTS`, `CHALLENGES` or
  `SUPERSEDES`;
- explicit statement that the record is not an engine input or FPL action;
- writer/tool version.

Narrative is necessary for human reasoning, but identity and validation cannot
depend on an LLM's summary of referenced artifacts. Links must use exact IDs and
hashes. A record that mentions an engine recommendation must preserve it exactly
or reference it; it cannot silently restate a different transfer or lineup.

The initial historical import should be selective. Preserve durable strategy,
the original squad-construction thesis, the João Pedro structural question,
the GW2/GW3 rolling rationale, known model limitations and the earlier lineup
trust incident. Do not automatically import an entire conversation or promote
every player opinion into permanent project doctrine.

## Current reasoning workflow

For a serious BUY/HOLD/SELL/WATCH decision, the control-room workflow is:

1. Resolve and validate the current trusted engine artifacts.
2. Obtain fresh private editable-manager evidence through the existing gate.
3. Refresh time-sensitive football facts, including prices, fixtures, club,
   coach, injury, availability, role, minutes and competition.
4. Load relevant historical thesis snapshots and state what was believed then.
5. Compare current evidence with the original assumptions without rewriting the
   old record.
6. Consider dimensions that Engine v1 explicitly omits, including multi-week
   value, free-transfer option value, information from waiting, structural
   funding and price risk.
7. Present the engine recommendation exactly and label human analysis
   separately.
8. Construct any proposed action only through a trusted engine or deterministic
   legality validator.
9. Record the actual human action through the journal before the deadline, or
   preserve any later account only as historical backfill.

Time-sensitive claims require refresh even when a historical handoff states
them confidently. Historical labels are useful comparison points, not current
ratings.

## Capture and publication safety

The Task029B writer must:

- require an explicit source file and explicit private destination root;
- reject symlinks, directories, devices and non-regular files;
- open without following links and detect source replacement/change while
  reading;
- bound accepted file size and stream hashing/copying;
- stage bytes only in an owner-only directory on the destination filesystem;
- fsync, validate the staged bytes, then publish with exclusive no-overwrite
  semantics;
- re-open and hash the published bytes before reporting success;
- never move, truncate, delete or alter the supplied source;
- never print source contents, private manifest narrative or absolute source
  paths by default;
- return only stable status, evidence ID, digest, byte count and whether exact
  validated bytes were reused;
- fail closed on a partial, conflicting or malformed existing destination;
- avoid network access and never choose `latest` or search for evidence.

Source capture and context publication are separate commands. Failure in
context publication cannot roll back or delete a safely captured source.

The existing manager-evidence hash may be verified against a captured source
through an explicit read-only command. A match proves byte identity with the
declared source hash. It does not revalidate the screenshot's content,
observation time or the operational decision chain.

## Privacy, Git and recovery

All source bytes, manifests and detailed context records are private/local data
and remain under root-anchored `data/`. Existing staged-path protection must
continue to reject the entire root. Task029B must also add focused tests proving
that its proposed paths are rejected by the staged path guard.

The content scanner and path guard remain independent controls. Neither should
print private matches. Installing an automatic local hook is a separate
repository-safety task because hook failure, denylist custody and direct-push
behavior need their own design.

The full authorized `data/` tree, including the new roots, belongs in encrypted
checkpoint scope. Recovery identities, passwords, credentials and private
denylist values do not. A successful future restore proves recovered bytes and
manifests only; trusted readers must still validate engine chains and temporal
claims.

The planned B2 destination has irreversible 90-day compliance retention.
Capturing private evidence locally does not itself upload it. Before the first
real checkpoint, the owner must accept that uploaded evidence cannot be deleted
from that destination until its retention expires. Local deletion would not
erase a retained backup copy.

## Compatibility and evolution

Existing immutable artifacts are never rewritten to adopt this design. Readers
must dispatch on an exact supported version and fail closed on unknown fields or
versions.

When a future v2 is needed:

1. keep the v1 reader and validator;
2. add a new writer and reader;
3. retain synthetic golden v1 fixtures and compatibility tests;
4. adapt old records in memory only when an explicitly reviewed use requires
   it;
5. preserve the original version, bytes and SHA-256 in every adapted view;
6. never overwrite or relabel the source record.

A small version registry should document current writers, supported readers and
retirement constraints. This policy should later be applied across engine and
operational artifacts, but Task029B must not refactor unrelated contracts.

## Task029B acceptance gates

The implementation is acceptable only when:

1. Synthetic files pass exact create, validate and byte-identical reuse tests.
2. Source mutation/replacement, symlink, hardlink, special-file, oversize,
   malformed-existing-output and hash-conflict cases fail closed.
3. Prospective context at or after its deadline is rejected.
4. Historical backfill cannot claim a system-proven original action time.
5. Later context can reference but cannot mutate or relabel earlier context.
6. Context-to-engine references are hash checked without importing optimizer,
   decision-construction or reliability behavior.
7. No source/context function is imported by the trusted production path.
8. Logs and test output contain no synthetic private payload or absolute private
   path.
9. Existing decision, journal, operational, application and privacy tests remain
   unchanged and pass.
10. Full Python and frontend validation pass with no skips or weakened checks.
11. Tests use synthetic bytes and identities only. Real evidence capture remains
    a separately authorized post-review operation.
12. Independent adversarial review reports no unresolved blocker before commit.

## Review questions

The independent reviewer should answer:

1. Can any historical or owner-reported record be mistaken for system-proven
   prospective evidence?
2. Can context or source evidence become decision authority through an import,
   schema or presentation shortcut?
3. Does the design preserve the exact distinction between engine action, human
   analysis and journaled human action?
4. Can later evidence alter, overwrite or silently relabel an earlier thesis?
5. Does source capture preserve exact bytes without trusting paths, filenames,
   metadata or OCR?
6. Are manager identifiers, source contents, credentials and private paths kept
   out of Git and normal output?
7. Are compatibility, recovery and deletion limitations stated honestly?
8. Is Task029B narrow enough to implement without Task026C, model changes or a
   second decision engine?

## Proposed next task

**TASK029B — Private source-evidence and strategy-context implementation**

Implement the two standalone local writers/readers, exact schemas, synthetic
tests and private runbook described above. Do not capture real evidence during
implementation. After independent review and any required remediation, request
separate owner authorization to capture the still-available current screenshot
and verify its existing manager-evidence hash binding.
