# TASK027C — Local backup readiness

This first implementation slice supplies local path protections and a read-only
byte inventory. It does **not** deliver a backup, encryption, provider setup,
credential custody, an offsite copy, recovery guarantees or a restore drill.
Claude independent adversarial review is complete, with no blockers, according
to the user-supplied review recorded below. No CI or hooks are installed by
these tools; no Task026C work is included.

## Before staging and committing

`/data/`, `/.private-recovery/` and `.DS_Store` are ignored. The root-anchored
patterns leave synthetic `tests/fixtures/data/` visible. `.private-recovery/`
is reserved for deliberately local private recovery outputs; it is not a safe
place for the only backup or key. Ignore rules do not protect already tracked
files and can be bypassed by forced staging.

After staging explicitly named reviewed files and before committing, run:

```sh
python3 scripts/check_staged_privacy.py
```

This read-only guard checks **all paths present in the index**, including
unchanged tracked paths. It rejects root `data/`, `.private-recovery/`, any
`.DS_Store` component, and the exact root files `task025_review.patch` and
`task025_claude_review_bundle.txt`. It prints escaped paths, never contents, and
returns nonzero on violations or inability to read the index. Remove offending
paths from the index without deleting working files, then rerun. Nothing is
automatically removed. Run it again after any staging change.

This is not secret detection and cannot prevent deliberate bypass, later index
changes or publication through another route. CI alone is too late to prevent
an upload. Do not put secrets in allowed paths or assume this guard finds them.

## Read-only inventory

Run only against an explicitly authorized data root:

```sh
python3 scripts/inventory_private_evidence.py --source-root /absolute/authorized/data
```

Default stdout contains aggregate file count and bytes only. The tool hashes
opaque bytes without interpreting tables or following content references. It
rejects symlinks (including root path components), lexical `..` components, special files, unreadable
inputs and detected changes. It opens directories/files without following
symlinks, checks file identity/size/timestamps across reads, then compares a
second tree metadata scan for additions, deletions and replacements. It emits
no inventory until successful completion. It never writes source files.

For deliberate private disclosure only, add `--private-manifest` to emit
relative paths, sizes and SHA-256 hashes on stdout. No file-output option is
provided: the tool never creates or overwrites an inventory file. Avoid shared
terminals, captured logs, chat transcripts or ordinary shell redirection of
this plaintext. In particular, never redirect into the source tree. Authorized
private persistence and its no-overwrite handling are deferred to a later slice.

This is not an atomic snapshot: stop writers before scanning. Metadata checks
cannot guarantee detection of an adversary hiding changes or mutations after a
path's last check. Reading may update filesystem access times. Byte equality
does not establish semantic validity, original timestamps, prospective truth,
external dependency completeness or recoverability. Sealed results remain
opaque; permission to hash is not permission to evaluate.

## Remaining decisions and evidence gaps

The owner selected Backblaze B2 as the direction. Account/region setup, external
drive, key/account recovery custody and retention remain unresolved; no provider
configuration or upload is authorized by this local slice. Prior TASK027B inspection
reported original manager-input/source-evidence hash references without matching
bytes under `data/` for two operational runs. This slice neither searches other
private locations nor recovers or invents that evidence. Original inputs/source evidence are **NOT LOCATED**, not definitively lost.
The owner reports screenshots were temporary and locating them in the old chat
would require extensive scrolling. No scavenging or reconstruction is required
by this slice. External evidence locations and completeness remain unresolved;
even a successful inventory cannot be described as a complete evidence backup.
Future original-evidence capture must be designed in a later authorized slice.

Full delivery still needs authorized encrypted checkpoints, two independent
copies, integrity readback, a clean-environment restore drill, and measured
RPO/RTO. Preserve frozen artifacts and test relocation constraints rather than
editing their absolute paths.

## Synthetic verification

```sh
python3 -m unittest discover -s tests -p test_backup_readiness.py -v
```

Tests use temporary trees and temporary Git indexes only. They cover forced
private staging, visible synthetic fixtures, escaped filenames, index
nonmutation, opt-in manifest exposure, unsafe files and detectable mutations.

## Independent Claude review record

Base SHA: `407ff7454b8af3d010946f8bc9d4ede579a5f7dd`.
Exact scoped files:

- `.gitignore` (modified)
- `docs/project/AI_WORKFLOW.md` (modified)
- `docs/project/TASK027C_LOCAL_BACKUP_READINESS.md` (new)
- `scripts/check_staged_privacy.py` (new)
- `scripts/inventory_private_evidence.py` (new)
- `tests/test_backup_readiness.py` (new)

Implementation-worker reported validation: the synthetic unittest command above
passed seven tests; `python3 scripts/check_staged_privacy.py` and
`git diff --check` passed. The additional seventh test rejects lexical parent
traversal before normalization can conceal a symlink component. These are local
results, not CI or independent-review evidence. Review both the tracked diff
and all four new files; ordinary `git diff` omits those new files.

Review focus: full-index path selection and escaped filenames, privacy-safe
stdout/failure behavior, symlink/change handling, and limits of race detection.
The guard is not a secret scanner; inventory hashes are not semantic trust or
proof of prospective timestamps. There is no live backup, upload, encryption,
restore drill or promised RPO/RTO. Claude should review independently rather
than edit the implementation; any blockers return to Codex for remediation.

The user supplied Claude's independent review with verdict **SAFE TO COMMIT
TASK027C** and no blockers. That review reports seven focused tests and 418
total Python tests passing. These execution results are attributed to the
user-supplied Claude review; they were not rerun for this documentation update
and are not CI results. The verdict applies to local backup readiness, not to
an implemented or verified disaster-recovery system.

Accepted nonblocking limits: forbidden-path comparisons are case-sensitive;
review-scratch protection names only the two known Task025 files; and `--repo`
pointing at a repository subdirectory still scans the entire index, not just
that subtree. Other filenames and secret-bearing contents remain outside this
path guard's guarantees.
