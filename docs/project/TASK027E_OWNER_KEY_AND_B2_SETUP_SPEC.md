# TASK027E — Owner key custody and B2 destination setup

Status: implementation specification; no account, credential, key, bucket, upload,
or backup has been created by this task.
Base: `60d00dd994cf3b418515c2bb63a22b8e1c39eec2` on `main`.

Read [Task027C's local boundaries](TASK027C_LOCAL_BACKUP_READINESS.md),
[Task027D's checkpoint runbook](TASK027D_ENCRYPTED_CHECKPOINTS.md), and the
[AI workflow](AI_WORKFLOW.md) first. Task027D passed independent remediation
review and CI at its committed SHA. Its tooling can create and locally verify an
encrypted checkpoint, but no owner recovery identity or offsite copy exists.

This task establishes recoverable owner custody and validates a Backblaze B2
destination with synthetic ciphertext only. It does not authorize access to or
upload of `data/`, creation of a real checkpoint, deletion, lifecycle automation,
Task026C, an FPL refresh, or a claim that disaster recovery is complete.

The owner-supplied independent Claude design review returned **SAFE WITH REQUIRED
CHANGES**, with no blockers. It required independent custody for the removable
drive unlock secret, an exhaustive B2 capability allowlist, and staged-content
screening for key/identifier leaks. All three are incorporated below, together
with its nonblocking Object Lock, trusted-hash, and live metadata-check notes.

## Intended result and gates

Task027E is complete only when the owner and Codex can record, without recording
secrets or private object names:

1. the production age recipient and two independently usable private-identity
   custody copies;
2. a private B2 bucket with encryption and retention settings reviewed in the
   console;
3. restricted upload and restore credentials whose denied capabilities were
   tested;
4. an uploaded synthetic ciphertext read back by exact immutable object version
   and matched to a separately held SHA-256;
5. a successful synthetic decrypt/verify using each recovery-key copy; and
6. an independently reviewed, sanitized setup receipt.

The owner must perform or directly observe the account, MFA, recovery-code, key,
and billing steps. Codex may guide and verify settings but must not print, paste,
store in the repository, or include in review evidence any credential, private
identity, account identifier, private bucket/object name, or recovery code.

Passing these gates proves destination readiness. It does not prove that the real
private evidence is complete, uploaded, restorable, or semantically usable.
Until Task027F completes an independent real restore drill: **NO VERIFIED OFFSITE
BACKUP**.

## Minimal recovery architecture

Use three failure domains for the eventual real checkpoint:

- the working `data/` tree on the Mac;
- client-side age-encrypted checkpoint objects in a private B2 bucket; and
- a later disconnected encrypted copy on removable storage kept away from the Mac.

GitHub remains the repository backup for committed source and documentation. The
checkpoint's Git bundle is a self-contained recovery aid for its recorded commit,
not a replacement for GitHub. Public data that might be fetchable today is still
included in a real checkpoint because historical endpoint responses can change or
disappear. Derived artifacts are included to preserve exact decision provenance.
Secrets, credentials, caches, temporary scratch material, incomplete checkpoint
directories, unrelated home-directory files, and lost/not-located evidence are not
checkpoint inputs.

Task027E configures only the B2 failure domain and key custody. Selection and
testing of the disconnected medium belongs to Task027F. A 500 GB encrypted USB-C
SSD is ample against the current roughly 129 MiB evidence baseline and leaves
substantial growth room; select a device when Task027F begins rather than freezing
a product recommendation here.

## Age recovery-key custody

Use native age X25519 material compatible with Task027D and age v1.3.1. Generate
one production identity only after the owner has prepared both custody locations.
Generation must write directly into an owner-private location outside the
repository, `data/`, checkpoint output, scratch, and restore trees. Do not use a
passphrase recipient, SSH key, plugin, or test key as the production recovery key.

Store:

- the public recipient on the Mac in an owner-private ignored recovery directory;
  it is not secret, but keeping it outside Git avoids unsupported configuration;
- private copy A as a secure item or attachment in the owner's password manager,
  protected by the owner's normal account recovery and MFA;
- private copy B on an encrypted removable drive kept physically away from the
  Mac and not left connected; and
- the removable drive's unlock secret written in a sealed recovery record stored
  separately from the Mac, drive, and password manager. It must not exist only in
  the password manager that holds copy A. The same separately stored record may
  contain a printed password-manager recovery code if that recovery model supports
  one.

The two private identity copies must not depend on the same laptop, removable
device, cloud account, or unlock secret. Do not retain the private identity in the
repository, `.private-recovery/`, ordinary shell history, terminal transcripts,
clipboard managers, chat uploads, B2, review bundles, or an unencrypted local file.
The public recipient alone cannot decrypt a checkpoint.

Before deleting any temporary generation copy, use each custody copy separately
to decrypt a small synthetic age object and confirm that its derived recipient
matches the recorded public recipient. Record only pass/fail, age version, date,
and a SHA-256 fingerprint of the public recipient file. Cleanup is a deliberate
owner action after both checks; no secure-erasure guarantee is claimed for SSDs,
swap, backups, or clipboard history.

If the age identity is lost, existing checkpoints are unrecoverable. If it is
exposed, create a new recipient for future checkpoints; old checkpoints remain
decryptable by the exposed key and must be recaptured and retired under a separate
privacy/deletion decision. Key rotation does not rewrite history automatically.

## B2 account and bucket policy

The owner creates the account and bucket in the Backblaze web console. Use a
non-identifying random bucket name and opaque object names; bucket names, object
names, metadata, and upload receipts must contain no manager name, email, FPL team
name, paths, filenames, or evidence descriptions. Choose the region based on the
owner's residency, latency, and data-location preference and record it privately.

Required bucket settings:

- private access;
- Backblaze server-side encryption enabled as defense in depth, while age remains
  the client-side confidentiality boundary;
- Object Lock enabled at bucket creation for this setup; Backblaze also permits
  enabling it later, but once enabled it cannot be disabled;
- default **governance-mode retention of 90 days** during the initial season; and
- no public sharing, CORS, replication, event notification, or application
  integration.

Governance mode is the initial recommendation because restricted operational keys
cannot bypass it, while the owner can still recover from a serious configuration
or cost mistake. Compliance mode provides stronger account-compromise protection
but cannot be shortened even by the account owner; consider it only after the
synthetic drill and retention policy have operated successfully. The owner account
uses strong unique authentication, MFA, and separately recoverable account codes.

Use unique immutable object paths such as an opaque checkpoint identifier plus the
fixed ciphertext/receipt role. Never upload to `latest` and never overwrite an
object name. Save the returned B2 file ID/version in a private receipt so restore
does not depend on ambiguous name lookup.

Do not configure automatic lifecycle deletion during Task027E. At the current
baseline, even daily full checkpoints grow by only about 47 GiB per year before
source growth, so a short observation period is inexpensive. After Task027F proves
restore and actual growth, adopt an explicit retention rule: keep every checkpoint
through its 90-day lock, retain deadline checkpoints for the current and previous
FPL seasons, and retain at least one verified season-end checkpoint until the owner
approves deletion. A lifecycle rule must never be introduced merely to control an
unexpected bill without first preserving required versions elsewhere.

## Least-privilege application keys

Do not use the B2 master key or owner web session in scripts. Create separate,
expiring standard application keys restricted to the one private bucket and the
one opaque project prefix. These are exact allowlists; if the pinned client needs
anything else, stop and review the design before adding it:

| Key | Exact allowed capabilities |
| --- | --- |
| Synthetic uploader | `listBuckets`, `writeFiles` |
| Synthetic restore | `listBuckets`, `listFiles`, `readFiles`, `readFileRetentions` |

Every capability not in the applicable allowlist is denied. At the time of this
design, the exhaustive known denied complement is `listKeys`, `writeKeys`,
`deleteKeys`, `listAllBucketNames`, `readBuckets`, `writeBuckets`, `deleteBuckets`,
`readBucketRetentions`, `writeBucketRetentions`, `readBucketEncryption`,
`writeBucketEncryption`, `shareFiles`, `deleteFiles`, `readFileLegalHolds`,
`writeFileLegalHolds`, `writeFileRetentions`, `bypassGovernance`,
`readBucketReplications`, `writeBucketReplications`, `readBucketNotifications`,
`writeBucketNotifications`, `readBucketLogging`, and `writeBucketLogging`, plus
the capabilities assigned only to the other role. Reconcile this list against the
current official capability list immediately before creating keys; a newly added
provider capability is denied unless explicitly reviewed and allowed.

If the Backblaze console's presets grant more access than this table, create keys
through a separately reviewed account-owner procedure or document the minimum
unavoidable excess before proceeding. Default bucket retention removes the need
for the uploader to set or change per-file retention. Short expirations are
preferred for the Task027E synthetic drill; later automation credentials require
a separate expiry/rotation decision.

Verify the provider reports each key's actual capabilities as exactly equal to its
allowlist. Verify the uploader cannot read, list files, share, access outside its
allowed prefix, delete, change bucket/encryption/retention/legal-hold settings,
bypass governance, administer keys, or access another bucket. Verify the restore
key cannot write, share, delete, change settings, bypass governance, administer
keys, or access another bucket. Expected authorization failures are evidence of
the boundary, not failed setup. Revoke the synthetic uploader after the drill;
whether to retain the restore key is an owner decision recorded privately.

## Client and credential handling

Use the official Backblaze B2 CLI, pinned to version **4.7.1**, for the setup drill.
Record the installed package/binary SHA-256 and provenance at implementation time;
do not assume a moving package index still serves the reviewed bytes. Install it
in an isolated tool environment, not the project's application dependencies.

Supply credentials through `B2_APPLICATION_KEY_ID` and `B2_APPLICATION_KEY` from
an owner-controlled secret source without echoing them or placing them in command
arguments. Set `B2_ACCOUNT_INFO` to a dedicated owner-private path outside the
repository, source data, checkpoint, and ordinary shared temporary directories;
the CLI otherwise caches authorization information in its default account-info
database. Never create a repository `.env` file. Default command output and the
sanitized receipt must suppress account IDs, bucket/object names, authorization
tokens, private paths, and file IDs.

Authentication-cache cleanup and key revocation are explicit owner actions after
verification. If a command fails, preserve only sanitized error class and exit
status. Do not attach raw CLI output to Claude or CI.

## Synthetic destination drill

Use a new temporary directory with restrictive permissions and content containing
no real paths, names, account data, or FPL evidence. The drill proceeds in order:

1. Confirm the pinned B2 CLI version/hash and age v1.3.1.
2. Produce a small random synthetic plaintext, hash it, encrypt it to the production
   public recipient, and hash the ciphertext. Destroying synthetic plaintext is
   optional; it is not private evidence.
3. With the uploader key, upload the ciphertext under a new opaque object name.
   Empirically determine whether B2 CLI 4.7.1's upload response exposes encryption
   and retention without extra uploader capabilities. Do not widen the uploader
   key if it does not: confirm server-side encryption and unexpired governance
   retention through the restore key or owner console instead.
4. Record the exact file ID/version and expected ciphertext SHA-256 only in an
   owner-private receipt. Keep a separately held copy of the expected hash outside
   the B2 account and its metadata so provider/account compromise cannot replace
   both the object and comparison value.
5. Exercise the denied uploader operations listed above and record pass/fail only.
6. In a new private download directory, use the restore key and exact file
   ID/version to read the ciphertext back. Compare byte count and SHA-256 before
   decrypting.
7. Decrypt once with custody copy A and once with custody copy B. Compare recovered
   synthetic plaintext to the original hash. Do not combine the two key copies in
   one location for convenience.
8. Exercise the restore key's denied write/delete operations. Confirm the object
   cannot be deleted through the restricted keys during retention.
9. Revoke the synthetic uploader and prove its next authorization attempt fails.
10. Produce a sanitized receipt and independent review bundle containing settings,
    tool provenance, hashes of public/synthetic material, access-control results,
    and known limitations only.

The drill must not use CI because credentials and live provider access do not
belong in repository automation. Claude reviews the sanitized evidence using
Sonnet 5 high. Any blocker returns to Codex for remediation. A passing review can
authorize committing documentation and sanitized tooling only; private receipts
remain outside Git.

## Failure and recovery model

| Failure | Recovery or limit |
| --- | --- |
| Mac lost | Obtain the age identity from either independent custody copy and use the restore credential/account recovery path to fetch the exact B2 version. Task027F must prove this without the Mac. |
| B2 account/provider unavailable | Use the later disconnected encrypted copy. Task027E alone does not close this risk. |
| B2 operational key stolen | Bucket/prefix scope, missing deletion/bypass rights, expiry, and Object Lock limit damage; revoke and replace it. Client-side encryption protects plaintext. |
| Owner account stolen | Governance retention may be bypassed by sufficiently privileged account access. MFA and the disconnected copy are required; compliance mode is the stronger later option. |
| Age identity lost | Recover from the other custody copy. Loss of both copies makes ciphertext permanently unrecoverable. |
| Age identity exposed | Rotate for new captures; assume all old checkpoint plaintext is exposed to the attacker. Recapture and deletion require owner review. |
| Local corruption | Fetch the exact B2 version and compare with the separately trusted ciphertext hash before decrypting. |
| Remote corruption | Use another retained B2 version or disconnected copy. Hash agreement alone does not prove evidence completeness or semantic validity. |
| Malicious deletion/ransomware | Object Lock and a key without deletion/bypass rights protect the retention window; the disconnected copy protects against provider/account loss. |
| Credential loss | Recover the owner account with independently stored MFA recovery material, then issue a new restricted key. Credentials are replaceable; the age identity is not. |

## Recovery objectives

Target RPO for private prospective evidence: create and upload a checkpoint after
each authorized evidence capture or finalized decision, and no later than the end
of an active working day. During the final 24 hours before an FPL deadline, upload
immediately after every material capture; target maximum RPO is **one hour**.
These are targets until scheduling and real runs prove them.

Target RTO after loss of the Mac is **four hours** to download, verify, and restore
the latest checkpoint to a prepared private filesystem when B2 and one key copy
are available. Target RTO using only the disconnected copy is **one business day**.
Task027F must measure both paths. Engine trust-chain validation and owner FPL action
are separate from byte-restoration time.

## Privacy and deletion

Age ciphertext conceals private manifests and filenames, but bucket/account
metadata can still reveal timing, size, IP/account activity, region, and opaque
object identifiers. B2 and any credential/password-manager providers remain
processors of that metadata. The owner must accept their terms, account recovery,
data-location, deletion, and billing behavior before real upload.

Object Lock intentionally delays deletion. A privacy deletion request cannot erase
a locked version until retention expires. After expiry, delete every specified B2
version and disconnected copy, revoke unnecessary keys, and verify absence through
an authorized listing; provider internal deletion schedules remain subject to its
terms. Never promise forensic erasure from SSDs or provider systems.

The repository's root ignore rules and full-index privacy guard remain mandatory.
Provider tooling, credential files, account-info databases, private receipts, and
download/restore directories must stay outside the repository. Stage exact reviewed
paths and run `python3 scripts/check_staged_privacy.py` after every index change.

Before generating real credentials, use the implemented second, fail-closed
staged-content check. It inspects every blob in the Git index for
the age private-identity prefix and for exact sensitive values supplied through an
owner-private denylist: age private identity, B2 application-key IDs and secrets,
account ID, bucket ID/name, and object IDs/names. The denylist must be outside the
repository with owner-only permissions. The scanner must read it directly rather
than receiving its values through process arguments or environment variables, and
must not copy them into logs, diagnostics, filenames, test fixtures, or review
bundles. The check reports only a generic pass/fail message, never the matching
bytes or repository path. It treats unreadable staged blobs, an
unreadable/malformed denylist, and scanner failure as failure.

The denylist is an absolute, non-symlinked, owner-owned regular file outside the
repository, in an owner-owned mode-0700 parent, with file mode 0600, one unique
printable ASCII value per line, and no blank lines. Each value is 8–4,096 bytes;
the file permits at most 256 values / 64 KiB. On macOS, create its private parent
directly under the owner's `/Users/...` home path and pass that fully expanded
absolute path; do not use `/tmp`, `/var`, or an unresolved path returned by plain
`mktemp`, because those locations traverse macOS system symlinks and correctly
fail this guard.
The guard reads raw committed blob objects rather than working-tree files, scans
regular/executable/symlink blobs in bounded chunks, refuses unmerged/unknown index
entries, disables Git replacement objects, rejects inherited Git repository/index
redirection, and rechecks the index listing for changes. Gitlinks contain only a
commit object reference in the parent index and have no staged blob to scan.

```sh
python3 scripts/check_staged_sensitive_content.py \
  --repo /absolute/repository \
  --denylist /absolute/owner-private/staged-denylist
```

Exit 0 is a clean scan, 1 means sensitive staged content was found, and 2 means
the check could not complete safely. Default output never identifies the value or
matching path. The check does not scan untracked files, unstaged working-tree
bytes, Git history, encoded/transformed variants, secrets omitted from the
denylist, submodule contents, or future content staged after it runs.
It scans sequentially and has no Git subprocess wall-time limit; it trusts the
selected local Git executable and repository object database to terminate. A
broken or unavailable Git process fails the guard rather than producing a pass.

Run both the existing path guard and the new content check after every staging
change and immediately before committing. Also inspect the exact candidate diff
and status so an allowed but unrelated file is not published. The new check is a
targeted last line of defense, not a general secret scanner and not permission to
place credentials in a working-tree file. The implementation and synthetic tests
must be independently reviewed before key/account setup.

## Task027E implementation sequence

1. The completed independent review is remediated in this specification.
2. Codex's staged-content guard and synthetic tests receive independent Claude
   review before any real secret or provider identifier exists.
3. Owner prepares password-manager recovery, an encrypted removable key medium,
   and its separately stored unlock record.
4. Owner and Codex generate the production age identity and verify both custody
   copies using synthetic data only.
5. Owner creates the B2 account controls and private Object-Lock bucket in the web
   console, then creates the two restricted, short-lived application keys.
6. Codex installs the pinned official B2 client in an isolated environment and
   performs the synthetic destination drill without exposing raw output.
7. Claude Sonnet 5 high reviews the sanitized setup evidence and any reusable
   scripts. Codex remediates agreed blockers and reruns affected checks.
8. With explicit owner authorization, commit only reviewed, sanitized documentation
   or tooling and verify CI if triggered.

Task027F is a separate authorization: create the first real checkpoint, upload and
read back its exact immutable version, restore it without relying on the original
Mac/data, run trusted engine readers at the recorded revision, test a disconnected
copy, and measure actual RPO/RTO.

Focused Task027E1 acceptance:

```sh
python3 -m unittest discover -s tests -p test_staged_sensitive_content.py -v
```

Implementation handoff at the uncommitted base above: the new guard is
`scripts/check_staged_sensitive_content.py` and its synthetic suite is
`tests/test_staged_sensitive_content.py`. Locally, 13 focused tests, the seven
Task027C tests, and all 460 Python tests pass. A temporary fresh candidate
repository containing the exact proposed files also passes the guard with a
synthetic denylist. Independent review is recorded below; CI remains pending.
These reported results are not authorization to generate real values or stage
the files.

The owner-supplied Task027E1 independent review matched the review-bundle hash,
reran all 13 focused, seven Task027C, and 460 full tests successfully, and reported
no blocker in the guard's security logic. Its verdict was **SAFE WITH REQUIRED
CHANGES** solely because the macOS symlinked-system-path guidance above was absent;
that clarification is now incorporated. Optional parser consistency, broader
exception handling, Git config isolation, subprocess timeout, and additional live
merge/worktree/executable regression tests remain deferred because the reviewer
found no concrete bypass and independently exercised the latter three scenarios.
CI/Python 3.10/Linux remains pending until an authorized commit.

## Design references

- [Backblaze B2 application keys](https://www.backblaze.com/docs/en/cloud-storage-application-keys)
- [Backblaze B2 application-key capabilities](https://www.backblaze.com/docs/cloud-storage-application-key-capabilities)
- [Backblaze B2 Object Lock](https://www.backblaze.com/docs/cloud-storage-object-lock)
- [Backblaze B2 lifecycle rules](https://www.backblaze.com/docs/en/cloud-storage-lifecycle-rules)
- [Official B2 CLI releases](https://github.com/Backblaze/B2_Command_Line_Tool/releases)
- [Official B2 CLI quick start](https://github.com/Backblaze/B2_Command_Line_Tool/blob/master/doc/source/quick_start.rst)
- [Backblaze B2 pricing](https://www.backblaze.com/cloud-storage/pricing)
