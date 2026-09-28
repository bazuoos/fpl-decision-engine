# Task033C3 — D04-F terminal feasibility memo

**DESIGN-FEASIBILITY PROPOSAL ONLY. Path 3 remains operative: completed
publication BLOCKED. Path 1 is unproved; path 2 is neither selected nor proposed
for adoption by this memo.** Prepared 2026-09-28 against Git HEAD
`d55421e52f7c8a921a87967434bbf9ca903b6a39` and the current unstaged C3.0 design
SHA-256 `28d508209ec8181a7a50278546d6988ca8de1462352f0231a80a20e995b1ee89`.
The owner reports SAFE / IMPLEMENTATION READY: NO with no mandatory findings
on the B05 amendment. This memo leaves that design and review intact.

## 1. One-page owner recommendation

**NO-GO for approving or implementing path 1 on the evidence inspected. Retain
path 3.** The missing proof is not ordinary atomic file installation or database
consistency. It is a recoverable, exclusive success admission whose final writes,
flushes, acknowledgments and resource evidence all completed within the frozen
ceilings, with no interval in which readers can accept an ultimately late result.

Two concrete candidates merit only comparison: Linux local-file publication
using no-replace installation and explicit flushes; or SQLite rollback-journal
transactions with documented durability settings plus a private admission gate.
Their documentation supports useful subproperties, not the combined terminal
contract. Neither candidate is selected. An external witness is not a free
measurement service or a reason to omit final I/O.

The narrow counterexample is a durable success record written before its final
acknowledgment: that acknowledgment can arrive after the deadline or be lost in
a crash. A post-return timer check cannot retroactively make the recorded success
safe. Persisting another check adds another final write. A live-only gate can
refuse success, but its lost volatile evidence cannot establish a completed
historical publication after reboot.

This is **not a universal impossibility theorem**. It establishes that the
ordinary prewritten-record/post-return-check constructions considered here do
not supply the required proof under unbounded I/O/scheduling delay. A stronger,
backend-specific terminal primitive or a sound bounded-operation proof might
change the result; none is established here. Fail-closed uncertainty is safe,
but an always-failing system is not a viable completed-publication backend.

The smallest useful next evidence is a primary-documented, exact-version terminal
contract and independently reviewed event/proof trace for one tiny synthetic
payload, including the final acknowledgment, crash boundary and every charged
helper. Only after that paper proof survives review would a separately authorized
synthetic test task be meaningful. This memo authorizes neither step's execution.

No owner decision is silently made about acknowledgment endpoints, permitted
loss of completed publications, service placement or clock/durability assumptions.
D04-F remains open alongside the existing D01–D07/E01–E07 gates. No implementation,
source access, publication, confirmation or promotion is authorized.

## 2. Fixed constraints and exact proposed terminal point

Technical authority: [C3.0 design](TASK033C3_CONTROLLED_RESEARCH_EXECUTION_DESIGN.md)
sections 17.5, 17.6 and 17.8;
[candidate freeze](TASK033B1B_CANDIDATE_FREEZE_AMENDMENT.md) section 10;
[approved C2 clarification](TASK033C2_PROTOCOL_CLARIFICATION.md) section 7.
The frozen offline ceilings remain 2 GiB peak RSS, one computational process
with at most four computational threads, 6 hours per season/candidate prediction
and scoring phase, and 24 hours per complete statistical evaluation, including
I/O and publication. This memo does not settle the open detailed phase attribution.
For any eventual accepted attribution, the terminal interval must fit every
applicable remaining budget; choosing the most generous interpretation is invalid.

[C2 `_Pair.run`](../../src/fpl_decision_engine/research/c2/evaluation.py) checks
supplied resource status after statistics; it supplies no terminal measurement.
[ReadOnceArtifactSnapshot](../../src/fpl_decision_engine/artifact_snapshot.py)
captures stable bytes but implements no timed admission transaction or durable
resource certification. Neither helper closes D04-F.

For analysis only, define the required linearization point **L** as the single
transition of the trusted study-slot state from `PREPARED_UNCERTIFIED` to
`ADMITTED_COMPLETE`, making the exact immutable package eligible for successful
reader admission. It is **not** rename visibility, a SQL COMMIT call's start,
or the time a PASS record was constructed. Only the existing proposed trusted
terminal/admission role may cause L, under the current slot fence and grant;
workers and ordinary readers cannot declare it.

At L, all of the following must already be true or be established indivisibly
by the terminal operation itself:

1. All payload, manifest, resource/authority evidence, file and directory flushes,
   witness reservations/acknowledgments and mandatory final acknowledgment work
   are complete and bound to the exact attempt/package/fence.
2. Completed elapsed time and continuous resource accounting, including the
   terminal/admission actor, satisfy every ceiling. No measurement ends before
   an included final operation. Authority remains valid at admission.
3. Readers cannot observe success before L. After crash/power loss, recovered
   evidence distinguishes a valid L from a late, partial or unknown transition.
   Missing proof yields uncertified/quarantined state, never reconstructed PASS.

A **logical requirement**, not an available API, is thus an admission operation
that couples deadline/resource checking, exclusive durable state and completed
acknowledgment evidence. The trusted admission process can observe syscall/RPC
returns and sample its clock while alive. It cannot by itself observe the exact
physical persistence instant, prove honest device acknowledgment, or preserve
its post-return observation without further work. A witness sees only its own
side of a protocol unless an evidenced contract says otherwise.

The precise acknowledgment endpoint is a material unresolved acceptance choice:
server persistence, controller receipt and consumer receipt are different events.
For this pass, all acknowledgments required to establish publication success are
included; none is quietly declared optional or moved after L. Arbitrary later
human reads need not be part of creating a publication, but first admission or
receipt work necessary to establish success cannot be relabelled a later read.
No supplied backend documents L with all these properties. **Stop here for any
implementation choice; the rest is a feasibility analysis of this unmet contract.**

## 3. Primary documentation: two candidates and supporting controls

Sources below were read on 2026-09-28. OS manual pages and current SQLite pages
are interface/vendor references, not an exact deployed build attestation. Linux
memory details below use the versioned 6.6 documentation. No software was installed,
configured or executed. Exact kernel, filesystem, device/cache, VFS, SQLite,
CPython and clock profiles remain unselected.

| Candidate/control | Documented property | Inference and unproved assumption for D04-F |
|---|---|---|
| A: Linux local filesystem, no-replace installation, file/directory flushes | `renameat2(RENAME_NOREPLACE)` rejects an existing destination on supporting filesystems, including documented ext4 support. `fsync` waits for device-reported completion; directory entries require their own directory flush. [rename documentation](https://man7.org/linux/man-pages/man2/rename.2.html), [fsync documentation](https://man7.org/linux/man-pages/man2/fsync.2.html) | Useful exclusive installation and persistence building blocks. These interfaces provide no combined deadline-conditional durable admission plus completed-ack proof. Hardware/cache honesty and supported mount behavior remain assumptions. A staged directory must remain uncertified even if installed atomically. |
| B: SQLite rollback journal, DELETE mode with synchronous EXTRA, protected admission service | SQLite describes rollback-journal deletion as its transaction commit point, followed by lock release. EXTRA adds a directory sync after DELETE-mode journal unlink. Durability depends on the documented OS/storage assumptions. [Atomic commit](https://www.sqlite.org/atomiccommit.html), [synchronous settings](https://www.sqlite.org/pragma.html#pragma_synchronous) | The documented database commit point precedes remaining work. SQL atomicity is not proof that the final sync, response and admission met a wall-time/RSS ceiling. External payload files and a witness are not automatically one SQLite transaction. A PASS row or second transaction has the same terminal-evidence gap. |
| Suspend-aware timing | `CLOCK_BOOTTIME` includes suspend time; `CLOCK_MONOTONIC` does not. [Linux clock documentation](https://man7.org/linux/man-pages/man2/clock_gettime.2.html) | Candidate elapsed-clock input only. It neither attests historical cross-boot completion nor atomically couples a sample to persistence/admission. Existing stop-on-suspend/clock-discontinuity proposals remain unresolved, unchanged. |
| Deadline notification | timerfd exposes timer expirations through a file descriptor. [timerfd documentation](https://man7.org/linux/man-pages/man2/timerfd_create.2.html) | Notification is not a documented atomic cancellation/rollback of publication I/O or a worst-case response-time guarantee. A delayed watchdog cannot certify a late commit. |
| Memory containment/accounting | Linux 6.6 documents possible temporary overrun of `memory.max`; `memory.peak` records cgroup/descendant memory usage since creation. [Linux 6.6 cgroup v2](https://www.kernel.org/doc/html/v6.6/admin-guide/cgroup-v2.html) | Neither fact proves the existing continuous aggregate RSS requirement. A measured cgroup peak is not interchangeable with RSS. The reviewed collective allocation remains unchanged; its enforcement proof is still absent. |

A VFS callback, commit hook, timeout option, preflight headroom or signed expiry
is not treated as a third solution: each would need primary documentation and
a proof covering its position relative to persistence, acknowledgments and L.
No additional backend or custom kernel/hardware mechanism is invented here.

## 4. Narrow counterexample and accounting closure

Consider a construction where a success record R is fixed before final I/O and
recovery accepts R without a trusted record of the final acknowledgment's time.
Let D be the applicable deadline. Two permitted schedules can leave the same R:

- Schedule A: R becomes durable, the final acknowledgment completes before D,
  and a subsequent crash loses the controller's volatile observations.
- Schedule B: the same R becomes durable, but the final acknowledgment is delayed
  until after D (or never received before the crash). Recovery sees the same R.

If acknowledgment completion is in scope, accepting both is unsound; rejecting
both is fail-closed but does not establish recoverable success for A. A timestamp
chosen before the final operation cannot distinguish them. If a post-ack record
S is added, its own write/flush/required acknowledgment must be covered. Under
arbitrary delay the same suffix problem reappears. This argument assumes those
indistinguishable schedules and no stronger commit-time evidence; it does not
rule out all conceivable primitives or justified worst-case bounds.

A proof of preventive containment could avoid self-measuring bytes in principle;
a receipt need not literally contain its own size/time. But the proof must bind
the entire final operation to enforced limits, including its acknowledgment and
admission tail. Mere reservation of time or disk space cannot do that. A live
post-return gate offers rejection, not durable historical proof. Permanently
quarantining all ambiguous recoveries may preserve safety; accepting loss of
otherwise completed publications is a separate owner requirement, not selected
here as a path-1 solution.

| Included work | Required accounting; no implied exception |
|---|---|
| Final data/receipt writes, directory changes, flushes and acknowledgments | Charge canonical/allocated bytes, I/O, elapsed time and peak memory through completion. Journal, WAL if ever proposed, temporary copies and recovery-critical side files cannot disappear from the inventory/accounting. No background required durability work after declared success. |
| Admission, validation and authority/witness work | Charge controller, verifier/ledger, publisher and witness clients/servers/storage helpers. Keep worker 1,536 + broker 256 + publisher 128 + ONE combined trusted-helper 128 MiB = 2,048 MiB. Distinct roles do not create additional allowances. External witness attribution remains unsupported without proof. |
| Retries, lock contention, stalled I/O, suspend and cancellation | Same attempt/deadline/fence and cumulative accounting. No fresh timer, nonce or retry that erases exposure. Cancellation can fail to stop already-issued work promptly; such uncertainty cannot produce success. Failure handling must remain bounded, never turn failure into late success. |
| Recovery | No restart may finish original publication with a new budget and backdate it. A later separately authorized verification may inspect a proved completed publication; it cannot complete missing original I/O or create missing historical resource evidence. Boot discontinuity or absent witness/high-water evidence blocks admission. |

RSS/timer observations must include the last responsible parent/helper; dismissing
it before its receipt finishes creates an accounting gap. The measurement actor
is itself charged. Device/kernel attribution, asynchronous work and any shared
service accounting require an exact backend argument under the existing profile;
unattributable cost is a blocker, not zero. This analysis does not change B05's
accepted clarification or claim that every kernel byte is process RSS.

The identity graph stays acyclic: terminal evidence binds the prior prepared
package and fence; the package never hashes back to that evidence. This solves
hash dependency only. It does not supply a physical terminal event or durable
proof of its timing. Neither candidate changes the existing recovery rule:
installed payload without adequate terminal proof is uncertified.

## 5. Proof obligations and proposed synthetic-only acceptance cases

These are **unexecuted requirements**, not tests added or authorized now. A finite
test suite can refute a design and exercise documented assumptions; successful
runs alone cannot prove a worst-case deadline or power-loss guarantee. Use an
independent event oracle, not the publisher's own PASS bit.

| Obligation | Later synthetic-only discriminator and required oracle |
|---|---|
| Exact L and no premature success | Place an independent reader at every barrier from last payload write through final flush/ack/admission. A competing publisher uses the same slot. No reader accepts a candidate or late attempt; one valid fence at most can succeed. Observe operations, not directory names or SQL labels. |
| Last-operation deadline | Delay final write, directory sync, database commit tail, witness reply and admission delivery individually across D; stall the watchdog too. Every overrun/unknown denies completion. Include delay after a successful clock sample. A quick normal run proves nothing about these cases. |
| Durable recovery discrimination | Crash at every boundary before/after acknowledgment and L; separately model/test power loss on disposable synthetic storage under a later approved environment. Recovered success requires original within-budget evidence; otherwise quarantine. Process kill is not evidence of power-loss behavior. |
| Whole-scope resource enforcement | Stress finalization helpers and witness while worker is near its cap; provoke brief peaks, task creation, sparse/unlinked-open writes and receipt overflow. Independent OS/accounting evidence must bound aggregate RSS and storage without polling gaps or uncharged observers. |
| Clock/authority/fence races | Suspend, reboot, lose clock confidence, revoke authority and change witness epoch at admission barriers. No stale fence, pre-deadline token or cross-boot counter comparison admits success. Retain possible exposure. |
| Retry and fault honesty | Inject EINTR/I/O error, full disk, lock contention, lost response after commit and witness rollback/fork. Retrying cannot duplicate success, restart budgets or infer acknowledgment from file existence. Failure-record write failure must still deny success. |
| Nontrivial success and exact adoption | One small generated immutable payload completes through the actual proposed primitive and survives independent recovery with exact bytes/evidence. This distinguishes a viable path from an always-deny implementation; it is not full C3 scale or predictive validation. |

Before those tests, a reviewer needs: a complete event/state model; the precise
primitive and its primary-supported guarantees; bounded final tail or atomic
coupling proof; clock/storage fault assumptions; exhaustive resource attribution;
and a recovery acceptance predicate that rejects Schedule B. These map to
existing E02/E04/E05/E07. No implementation-shaped label closes an obligation.

## 6. Smallest evidence threshold, limits and stop

The smallest **paper** result that would change this pass from NO-GO to
"potentially viable for separately authorized synthetic investigation" is a
backend-specific trace and proof for a single tiny payload satisfying section 2,
including both schedules in section 4. It must identify who observes L, how its
timing/resource validity survives a crash, why the last acknowledgment/admission
suffix cannot exceed a ceiling unnoticed, and how the observer/witness fits the
unchanged collective budget. Primary documentation must support each assumed
primitive; latency samples cannot substitute for a guaranteed bound.

The smallest **empirical** follow-on would be that payload's normal, delayed-tail,
competing-writer and crash/recovery cases with independent evidence on an exact
approved stack. No research sources, C1/C2 calculation or full study are needed
to investigate terminal mechanics. Even success would leave full resource/scale,
source, protocol and authorization gates open. Neither follow-on is authorized
by this memo.

Current result: **no supplied candidate meets that paper threshold**. The
constructive feasibility of path 1 remains UNKNOWN; the ordinary constructions
above are insufficient under their stated model. Path 3 therefore remains the
only operative disposition. No path-2 accounting carve-out, reduced guarantee,
new backend, owner topology choice or implied execution permission is introduced.
The pass stops at that evidence gap rather than filling it with an unverified
platform claim. This memo is ready for narrow adversarial design review, not
implementation approval.

Validation is limited to the new memo's complete text/diff, whitespace, link
shape/local targets, Git scope and preservation hashes. Existing C3.0 design,
protocol/code and previous review files remain untouched by this pass. Primary
checkout and the two Task025 files are preserved; old broken worktree remnants
are untouched. No tests, experiments, benchmarks, services, credentials, grants,
protected/private/manager/data-directory reads, research execution/publication,
confirmation, model promotion, FPL action, staging, commit, push or PR mutation
occurred. Public primary documentation reads supplied the citations above; they
are not platform validation. No giant bundle or review submission was created.
