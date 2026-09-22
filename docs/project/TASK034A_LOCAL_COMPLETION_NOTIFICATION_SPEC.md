# TASK034A — Zero-token local completion notification design

## Status and authority

This is a design for independent review. It does not implement, install or
activate a notification, change an active completion schedule, call FPL, read a
manager account, alter operational evidence, invoke an AI service or authorize
any later task.

The purpose is narrow: when one exact Task028F production completion schedule
reaches a terminal state or its scheduling/control state requires owner review,
macOS should show one generic local notification without a Codex turn or
model-token use.
The notification is a convenience signal only. Task028B remains the public-data
completion authority, and Task028F remains the scheduling authority.

The current active schedule is bound to exact repository, controller, Python
and plan bytes. Modifying its controller or plist would invalidate that reviewed
identity. This design therefore uses a separate local observer. It does not
retrofit, replace, restart, deactivate or write inside the active scheduler.

## Required outcome

For one explicitly prepared schedule identity, the observer must:

1. remain quiet while the schedule is active and healthy;
2. notify once with neutral terminal wording when the trusted Task028F status is
   `TERMINAL_QUIESCENT`;
3. notify once with scheduling-review wording when the trusted Task028F status
   is `REVIEW_REQUIRED`;
4. use fixed generic text containing no manager, squad, decision, path, price,
   credential, gameweek-result or private evidence value;
5. publish a private immutable receipt only after the macOS notification command
   returns success;
6. become locally quiescent after that receipt;
7. retry source-status and pre-execution failures only when the notification
   command is proved not to have started; and
8. make no network request and invoke no model, Codex task or OpenAI API.

Success means that the reviewed command was accepted by macOS and its receipt
was preserved. It does not prove that Notification Center visibly displayed the
message, that the owner noticed it or that Focus settings allowed an alert.

## Architecture choice

The implementation target is a new standard-library tool:

```text
scripts/completion_monitor_local_notification.py
```

It is an observer beside Task028F, not part of Task028B or the decision engine's
scoring path. A dedicated LaunchAgent invokes it on a fixed local interval. The
observer calls the exact reviewed Task028F controller's `status` command as a
child process and parses only its bounded JSON status. Task028F documents that
`status` performs no network request.

```text
Task028B public completion authority
             ^
             |
Task028F exact schedule/controller ----> private scheduler evidence
             |
             | bounded local `status` subprocess; read only
             v
Task034 observer LaunchAgent
             |
             +-- active status ----------> quiet
             +-- terminal/control-review -> fixed `/usr/bin/osascript` command
                                             |
                                             v
                                  private notification receipt
```

Keeping the observer separate provides three safety properties:

- notification failure cannot change a scheduler invocation, terminal marker,
  Task028B receipt, evaluation or engine result;
- the current exact schedule and its installed plist remain byte-identical; and
- notification lifecycle can be reviewed, activated and removed independently.

The observer must not import private Task028F helpers or duplicate Task028F's
terminal validation. The exact public `status` command is the trust seam.

Task028F deliberately reports `TERMINAL_QUIESCENT` for every valid installed
terminal marker. That public status does not distinguish
`TERMINAL_COMPLETE`, `TERMINAL_REALIZED_COMPLETE` or
`TERMINAL_REVIEW_REQUIRED`. Task034 therefore makes only the neutral claim that
the source schedule reached a terminal state and needs owner verification. Its
separate `REVIEW_REQUIRED` input means Task028F detected scheduling/control
drift, uncertainty or an unsafe remainder; it is not an outcome-level status.

Outcome-specific notification would require a new bounded public Task028F
status contract, a changed controller identity and a replacement source plan.
That is deferred. Task034 must not infer the hidden terminal subtype or present
`TERMINAL_QUIESCENT` as clean completion.

## Explicitly rejected alternatives

### Modify the active worker

Rejected for the current schedule. Its plan binds exact controller and commit
bytes. A modified worker would fail closed or require replacement of the active
schedule. Future Task028F plans may integrate a reviewed notifier after Task034
is proven, but that is not this task.

### Keep an hourly Codex heartbeat

Rejected as the primary mechanism. It invokes a model-backed task, consumes
account usage, depends on Codex service availability and adds no authority to
the local evidence chain.

### Send email, Slack, SMS, push-webhook or cloud events

Rejected. These require network services, accounts, credentials and additional
privacy boundaries. The minimum trustworthy solo-project design is local.

### Read `terminal.json` directly

Rejected. That would duplicate validation and couple the notifier to private
scheduler internals. Task028F already exposes a bounded, network-inert status
command and validates the complete evidence chain before reporting state. The
resulting loss of outcome-level detail is explicit and accepted above.

### Use shell interpolation or notification text from a plan

Rejected. The message does not need dynamic content. Fixed constants avoid
AppleScript injection and prevent private values from reaching Notification
Center or lock-screen previews.

### Self-deactivate the scheduler

Rejected. Task028F deliberately requires exact-label owner deactivation and
verified plist removal. A notification is not authorization to change that
lifecycle.

## Fixed notification content

The implementation must own two compile-time constant messages:

```text
Title: FPL Decision Engine

Terminal message:
Completion monitoring reached a terminal state. Open the project to verify the status.

Review message:
Completion-monitor scheduling needs review. Open the project to inspect the safe status.
```

No plan field, subprocess output, exception text, filename, username, season,
gameweek, player, manager or result may be interpolated. The messages may appear
on a locked screen, so even otherwise public football details remain omitted.

The command must execute `/usr/bin/osascript` directly with an argument vector
and no shell. The fixed AppleScript source and fixed messages must be tested as
exact bytes. PATH lookup, `shell=True`, environment-provided code and executable
overrides are prohibited in production mode.

## Prepared observer identity

Preparation derives one canonical owner-only ignored directory outside the
Task028F schedule root. The full source schedule-plan SHA-256 is the directory
identity:

```text
<source-repository>/data/operations/completion-monitor-notifications/
  <source-schedule-plan-sha256>/
    observer-plan.json
    observer-plan.json.sha256
    launch-agent.plist
    launch-agent.plist.sha256
    observer.lock
    attempts/
    lifecycle-events/
    notified.json                 # absent until accepted
    notified.json.sha256          # absent until accepted
```

The files shown beneath the hash directory are not permitted at the parent
level. The final hash-named directory is created atomically with exclusive
`mkdir`; an existing directory, symlink or non-directory entry fails closed.
A partial directory from an interrupted preparation requires review and cannot
be replaced or renamed by this tool.

The observer plan binds:

- one observer identifier derived from the full source schedule-plan SHA-256;
- exact absolute repository and notification-tool paths;
- the committed notification-tool SHA-256;
- exact absolute Task028F controller path and committed SHA-256;
- exact absolute schedule-plan and sibling-hash paths;
- the expected schedule-plan SHA-256;
- the Task028F LaunchAgent label reported by the validated plan;
- the observer's exact LaunchAgent label and installed-plist path;
- the numeric owner user ID;
- the resolved Python path and version;
- the fixed 900-second polling interval;
- creation, not-before and expiry timestamps; and
- the only recognized pre-existing untracked repository paths.

The plan contains no notification text because the production messages are code
constants. It contains no manager ID, cookie, API key, email address, squad,
player, result or private artifact content.

Preparation must require an exact committed notification implementation and an
exact committed Task028F controller. It derives the notification parent from
the source plan's validated repository rather than accepting an arbitrary
parent. This makes every conforming preparation for one source-plan hash contend
for the same hash-named directory, including preparations launched from another
checkout.

Preparation refuses dirty tracked or staged state, unexpected untracked paths,
symlinks, non-owner roots, any existing entry for the source-plan hash, an
unignored canonical parent, unsafe absolute paths, a missing schedule-plan hash,
an invalid target plan or an observer expiry earlier than 24 hours after the
source schedule expiry. The observer window may not exceed 21 days from
preparation. This margin lets Task028F publish its expiry review state before
the observer itself stops.

The full source hash also appears in the LaunchAgent label. No caller-supplied
observer ID or output-parent option exists. Manual copies outside the canonical
parent are unsupported and cannot be presented as Task034 evidence.

`prepare` is inert: it must not call `launchctl`, Task028F status, `osascript`,
FPL or any network service.

## LaunchAgent

The generated observer plist contains only:

```text
Label
ProgramArguments
WorkingDirectory
StandardOutPath
StandardErrorPath
Umask
RunAtLoad
StartInterval
KeepAlive
```

`ProgramArguments` invokes the exact Python with `-I`, the exact notification
tool, `run`, the plan and its sibling hash. `Umask` is `077`, `KeepAlive` is
false, `RunAtLoad` is true and `StartInterval` is 900 seconds.

`StandardOutPath` and `StandardErrorPath` are both the literal `/dev/null`.
Scheduled `run` emits no output and uses bounded structured records for the
small set of actionable or failed observations. Interactive lifecycle commands
may return bounded JSON to the invoking terminal.

`RunAtLoad` is acceptable here because the observer is network-inert and cannot
alter engine or scheduler evidence. It lets a login after sleep or shutdown
detect an already-terminal schedule promptly. `StartInterval` is a best-effort
macOS schedule, not a five-minute delivery guarantee.

The plist must not contain a shell, environment variables, calendar inference,
watch paths, queue directories, KeepAlive predicates or dynamic message text.
Canonical plist bytes may not exceed 16 KiB. Preparation validates the exact key
set and values with `plistlib`; activation additionally requires the exact
candidate to pass `/usr/bin/plutil -lint -- <candidate>` before any copy or
bootstrap. Validation output is bounded and is never placed in structured
records or sanitized evidence.

## Commands and effects

| Command | Local effect | `launchctl` | Network/model |
|---|---|---|---|
| `prepare` | Create ignored plan, hashes, lock and candidate plist | No | No |
| `verify` | Validate plan, files, receipts and lifecycle | No | No |
| `run` | Read exact Task028F status; possibly call fixed `osascript` | Task028F status performs exact-label `print` | No |
| `status` | Validate observer state and exact service label | Exact-label `print` | No |
| `activate` | Install, bootstrap and kickstart exact observer plist | Yes, explicit flag | No |
| `deactivate` | Boot out exact observer label and remove matching plist | Yes, explicit flags | No |
| `sanitize` | Write a bounded redacted review package outside the repository | No | No |
| `test-notification` | Send one fixed test message | No | No |

`test-notification` is never called by `run`. It requires an explicit execution
flag and may run only on macOS in an interactive Aqua login. It publishes no
claim that the notification was visible. The owner must separately confirm what
they observed before real activation is considered trusted.

## Run algorithm

Each `run` performs this sequence:

1. Open and validate the canonical owner-only observer plan and sibling hash
   using stable reads.
2. Open the prepared owner-only regular `observer.lock` without following a
   symlink and acquire a nonblocking exclusive `fcntl.flock`. A losing concurrent
   invocation exits quietly with `OBSERVER_BUSY`, process exit code 2, writes
   nothing and invokes
   neither Task028F status nor `osascript`. The operating system releases the
   lock if the process exits or crashes.
3. Validate the exact notification-tool and Task028F-controller bytes, Python,
   repository state, source schedule plan and installed observer plist.
4. Require valid active observer lifecycle evidence. Manual execution of a
   prepared but inactive observer cannot send a production notification.
5. Validate every existing attempt and any notified marker. At most 64 attempt
   identities are permitted for one observer. Reaching the cap returns
   `REVIEW_REQUIRED` without further subprocesses or writes.
6. If a complete valid notified marker exists, return `NOTIFICATION_QUIESCENT`
   without calling Task028F status or `osascript`.
7. If either notified-marker file is partial, corrupt or contradictory, return
   `REVIEW_REQUIRED` without calling `osascript`.
8. If a valid successful result exists without the marker, publish the marker
   from its exact claim/result pair and return quiescent without resending.
9. If an unresolved claim or any post-start uncertain result exists, return
   `REVIEW_REQUIRED` without calling Task028F status or `osascript`.
10. If expired, publish a bounded attempt with `OBSERVER_EXPIRED` and return
   `REVIEW_REQUIRED` without notifying.
11. Execute the exact Task028F `status` command with a 15-second timeout, a fixed
    minimal environment, a 4 KiB output ceiling and captured stdout/stderr.
12. Require one canonical JSON object and one of Task028F's exact exit/payload
    pairs: exit zero with the sole `status` field and a recognized non-review
    status; or exit three with `status=REVIEW_REQUIRED` and either no other field
    or Task028F's optional bounded string `detail`. Reject exit/payload conflicts,
    unknown fields, duplicate keys, oversized output and any other exit code.
    Ignore the optional detail after validation and never store it, raw stderr or
    malformed stdout.
13. A timeout or failure before any delivery claim publishes one bounded
    retryable failure attempt; malformed or conflicting completed output
    publishes one bounded review failure attempt. Records contain only fixed
    error-class enums. Neither case calls `osascript`.
14. For `ACTIVE_NOT_YET_DUE`, `ACTIVE_WAITING` or `ACTIVE_RETRYABLE`, return
    `ACTIVE_QUIET` without a structured attempt or `osascript` call.
15. For `NOT_ACTIVATED` or `DEACTIVATED`, return `INACTIVE_SOURCE` without a
    structured attempt or `osascript` call. These states do not imply failure or
    completion.
16. For `TERMINAL_QUIESCENT`, select the fixed neutral terminal notification.
17. For `REVIEW_REQUIRED`, select the fixed scheduling-review notification.
18. Before starting `osascript`, publish an immutable delivery claim. Only one
    unresolved claim may exist for the observer plan.
19. If process creation is proved to have failed before `osascript` started,
    publish a bounded pre-execution failure result. A later interval may create
    a new claim and retry.
20. If `osascript` starts and returns zero within 10 seconds, publish an immutable
    success result and then an exclusive notified marker bound to the claim and
    result.
21. A nonzero exit, timeout, signal, lost process state or crash after the claim
    is delivery-uncertain. Publish a bounded result when possible and stop for
    review without automatic resend.

The observer must never call Task028F `run`, activate or deactivate. It must
never open Task028B receipts, scheduler terminal files, evaluation outputs or
manager evidence.

### Exact Task028F status encoding

Accepted stdout is strict UTF-8, ends with exactly one LF byte, contains no
other leading or trailing bytes and must equal the result of
`json.dumps(value, sort_keys=True) + "\n"` under the pinned Python. JSON parsing
rejects duplicate object keys. Examples of the only shapes are:

```json
{"status": "ACTIVE_WAITING"}
{"status": "REVIEW_REQUIRED"}
{"detail": "invalid plan field", "status": "REVIEW_REQUIRED"}
```

The optional `detail` must be a string of 1–512 Unicode code points, may appear
only with exit three and `REVIEW_REQUIRED`, and is discarded immediately after
shape validation. Accepted stderr is empty. Captured stdout and stderr are each
rejected above 4,096 bytes.

The exact Task028F controller is a committed, hash-bound trusted producer, but
ordinary `subprocess.run(..., stdout=PIPE, stderr=PIPE)` buffers output before a
post-hoc size rejection. Task034B may reuse that bounded precedent and must
state that 4 KiB is an accepted-output limit, not a hard child-output RSS quota.
Tests must exercise the real pinned controller's normal, direct-review and
exception-detail encodings with synthetic plans, plus hostile duplicate-key,
noncanonical, oversized, nonempty-stderr and exit/payload-conflict cases.

## Duplicate and crash behavior

There is no transactional primitive spanning a macOS visual side effect and a
filesystem receipt. The observer therefore promises automatic **at-most-once
attempt**, not exactly-once visible delivery. An immutable claim is the
pre-execution idempotency boundary; the notified marker is the successful
acceptance boundary.

The notified marker records:

- observer-plan SHA-256;
- source schedule-plan SHA-256;
- accepted status class (`TERMINAL_QUIESCENT` or `REVIEW_REQUIRED`);
- fixed message identity, not message text;
- delivery-claim and success-result identifiers and SHA-256 values;
- observation time;
- notification command exit class; and
- the claim `COMMAND_ACCEPTED_NOT_VISIBLE_DELIVERY_PROOF`.

Claims, results, the marker and their hashes use exclusive no-overwrite
publication. A claim is durably published before process creation. A result is
durably published after the child outcome is known. The marker is published
immediately after a successful result in the same invocation.

A crash before the claim is published causes a later normal attempt. A crash
after claim publication and before or during process execution leaves delivery
uncertain; restart stops for review and does not resend. This may miss a
notification if the command never displayed it, but prevents an automatic
duplicate if it did.

A crash after a successful result is published but before the notified marker
allows restart to publish the missing marker from the valid claim/result pair
without calling `osascript` again. A result claiming pre-execution failure may
permit a later new claim only when the injected runner can prove process creation
never succeeded. Nonzero exits, timeouts and signals after process creation are
uncertain and require review.

This design cannot guarantee both no duplicate and no missed notification under
arbitrary crashes. The selected at-most-once policy favors avoiding repeated
alerts and records uncertainty for owner inspection.

Technical failures that prove the notification command did not start may retry.
The tool retains all bounded attempt records until reviewed deactivation. There
is no deletion or compaction command.

## Status model

The observer reports one bounded status:

- `PREPARED_NOT_INSTALLED`;
- `ACTIVE_QUIET`;
- `ACTIVE_RETRYABLE`;
- `OBSERVER_BUSY`;
- `NOTIFICATION_QUIESCENT`;
- `INACTIVE_SOURCE`;
- `DEACTIVATED`; or
- `REVIEW_REQUIRED`.

It must never report that completion evidence is valid, a gameweek is final, an
evaluation succeeded or a notification was seen. Those claims lie outside its
authority.

`OBSERVER_BUSY` belongs only to the notification observer and means another
observer invocation currently owns the nonblocking run lock. It must not be
confused with Task028F's source status `ACTIVE_RETRYABLE`, which the observer
maps to `ACTIVE_QUIET` after validating the controller response.

The observer process exits 0 for quiet, inactive, quiescent and successfully
recorded outcomes; 2 for `OBSERVER_BUSY` and retryable technical failures proved
to occur before notification delivery starts; and 3 for `REVIEW_REQUIRED`,
including uncertain delivery or invalid state. Scheduled execution remains
silent for every exit class.

## Activation and owner-visible drill

Implementation and synthetic tests are Task034B. They must not touch the live
schedule or display a real notification.

Task034C prepares the sole canonical observer plan for the current exact
Task028F schedule. It must remain uninstalled and inactive while the plan, plist
and sanitized bundle are independently reviewed. Preparation does not inherit
authority from the Task028F activation.

After Task034C is committed and CI is green, Task034D requires two separate
owner actions:

1. Run `test-notification` once and ask the owner to confirm whether the fixed
   test notification was visibly observed. Do not treat command exit zero as
   human confirmation.
2. Only after that confirmation, explicitly authorize activation of the exact
   observer plan.

Activation validates absence of the exact observer label and installed plist,
copies without overwrite, bootstraps the exact user domain, verifies the label,
publishes active lifecycle evidence and kickstarts once. Failure uses exact-label
rollback and stops for review unless absence and unchanged-plist removal are
proved.

Because `RunAtLoad=true`, bootstrap may start one observer process before the
active lifecycle event is published. That process must return inertly without a
status subprocess, notification or structured attempt. The explicit kickstart
after active evidence is the first invocation allowed to observe the source.

The existing Task028F worker is neither restarted nor deactivated. If its source
status is already terminal when the observer activates, the kickstart may send
the real generic notification immediately; Task034C review must state this.

## Deactivation

Observer deactivation is always explicit. It prints, boots out and reprints only
the exact observer label, then removes only an installed plist whose bytes equal
the reviewed candidate. It publishes immutable deactivation evidence.

Observer deactivation must not call the Task028F deactivation command or touch
the Task028F label, plist, plan, lifecycle or evidence. Task028F still requires
its own separate owner-reviewed deactivation after terminal status.

## Security and privacy model

- All observer plans, attempts, lifecycle records and receipts are owner-only
  beneath ignored `data/`.
- The observer reads only the source schedule plan, its hash and bounded status
  output. It does not enumerate the source schedule directory.
- Scheduled stdout and stderr are fixed to `/dev/null`; structured private
  records are the only operational evidence.
- Subprocess exception messages, raw stdout/stderr, environment values and local
  absolute paths never enter attempts, receipts, notifications or sanitized
  review packages.
- Production subprocesses use exact absolute paths, fixed argument vectors,
  timeouts, minimal environments and no shell.
- Repository staged-path and sensitive-content guards remain mandatory.
- Notification previews are treated as public-to-the-device-user; fixed text is
  therefore deliberately generic.
- No credential, cookie, email address, webhook, API key or model token is
  accepted by any command or schema.

This mechanism is not a backup, remote alert or disaster-recovery channel. It
does not work while the Mac is off, cannot notify after loss of the laptop and
does not improve the current **NO VERIFIED OFFSITE BACKUP** state.

Healthy interval and `RunAtLoad` executions write no attempt record. An observer
permits at most 64 actionable or failure attempt identities. Every claim, result,
lifecycle event and receipt has a 1 KiB canonical-byte ceiling and a sibling
hash; the complete structured state is therefore expected to remain below
256 KiB. The implementation and tests must enforce the attempt-count and
per-record ceilings. Scheduled stdout/stderr cannot grow because both are
`/dev/null`.

## Failure analysis

| Failure | Required behavior |
|---|---|
| Mac asleep or off | No run; `RunAtLoad` and later intervals retry after login |
| No Aqua login | Command may fail or be accepted without visibility; no visible-delivery claim |
| Focus mode or notifications disabled | Command may succeed; receipt remains acceptance-only |
| Task028F active and waiting | Quiet, no record |
| Task028F retryable | Quiet, no record and no owner alarm |
| Task028F review required | One generic review notification |
| Task028F terminal, subtype unknown | One neutral terminal-state notification |
| Task028F inactive/deactivated | Quiet inactive state |
| Invalid source status JSON | Bounded failure; no notification |
| Exit/status pair conflicts | Bounded failure; no notification |
| Source controller timeout | Retryable bounded failure; no raw output stored |
| `osascript` proven not started | Bounded pre-execution failure; later retry allowed |
| Crash after durable claim | Possible missed alert; stop for review; no automatic resend |
| Process started but outcome uncertain | Stop for review; no automatic resend |
| Receipt complete | Quiescent without subprocesses |
| Receipt partial/corrupt | Review required; no notification |
| Observer code, Python, plan or plist drift | Fail closed before status/notification |
| Active scheduler changes independently | Observer validates the exact source identity and fails closed |
| Observer activation rollback uncertain | Preserve state and require exact-label owner review |
| Concurrent observer invocation | Lock loser exits quietly without reads beyond pre-lock validation |
| 64-attempt cap reached | Review required; no subprocess or additional write |
| Laptop lost | No alert and no recovery benefit |

Failure of the observer itself before it validates an actionable Task028F status
may remain silent. The owner must still inspect local observer and Task028F
status when expected completion evidence is overdue. Task034 is a convenience
signal and cannot serve as its own independent alarm channel.

## Testing requirements

Task034B must use dependency-injected command runners and synthetic directories.
No test may call live `launchctl`, `osascript`, FPL, OpenAI, Codex, private data
or the active schedule.

`fcntl.flock` is new Task034B behavior; Task028F provides no existing lock
implementation to reuse. Dedicated subprocess and concurrency tests must cover
lock ownership, nonblocking contention, release on normal exit and crash, and
the existing symlink and hardlink rejection boundaries around `observer.lock`.

Tests must prove:

1. prepare is inert and exclusive;
2. concurrent or sequential preparations for the same source-plan hash contend
   for one canonical source-keyed directory and exactly one can succeed;
3. schemas reject missing, extra, malformed and non-finite fields;
4. plan/hash and every structured record use stable reads and canonical bytes;
5. symlinks, unsafe ownership/modes, relative paths and path escapes fail closed;
6. a nonblocking `fcntl` lock serializes runs and a losing invocation performs no
   status, notification or structured write, reports `OBSERVER_BUSY` and exits
   with code 2;
7. only the exact Task028F `status` command is permitted;
8. all active states are quiet and write no attempt;
9. `TERMINAL_QUIESCENT` always selects neutral terminal wording and never claims
   a clean outcome subtype;
10. Task028F `REVIEW_REQUIRED` selects scheduling/control-review wording and is
   never described as an outcome subtype;
11. inactive/deactivated source states remain quiet and write no attempt;
12. unknown, malformed, oversized, noncanonical or nonzero status output cannot
   reach `osascript`, except the exact Task028F exit-three
   `REVIEW_REQUIRED` payload defined above;
13. exact byte-layout tests cover normal status, direct review, exception detail,
    duplicate keys, the 512-code-point detail bound and proof that accepted
    detail is discarded from every record and message;
14. the production notification command uses `/usr/bin/osascript`, a fixed
    argument vector, no shell and no dynamic message content;
15. notification success publishes exactly one attempt and one receipt;
16. a valid receipt prevents future Task028F and notification subprocesses;
17. partial/corrupt receipts are network- and notification-inert;
18. the delivery claim is durably published before notification process start;
19. failures proven before command start may retry with a new claim;
20. every post-claim delivery-uncertain boundary blocks an automatic resend;
21. recovery from a published successful result but missing receipt publishes
    the receipt without resending;
22. expiry, clock rollback and lifecycle conflicts fail closed;
23. the 64-attempt cap and 1 KiB record ceilings are enforced for interval and
    `RunAtLoad` executions;
24. the plist is size-bounded and parses exactly; a macOS synthetic check passes
    `/usr/bin/plutil -lint` before any live lifecycle task;
25. activation/deactivation touch only the exact observer label and plist;
26. observer cleanup never touches Task028F lifecycle or evidence;
27. sanitized output contains no absolute path, UID, username, command output,
    message text or private source value;
28. AST and subprocess tests prove no network, AI SDK, Codex CLI, shell or
    credential pathway exists; and
29. the primary checkout and active Task028F schedule are byte/state unchanged
    by the complete synthetic suite.

A macOS synthetic LaunchAgent drill must later demonstrate `RunAtLoad`, interval
execution, at-most-once attempt behavior using a fake notification runner, quiet
post-receipt behavior and exact-label removal. The real `osascript` visual test
remains a distinct owner-observed Task034D gate.

With the Mac awake and both LaunchAgents healthy, the notification objective is
15 minutes from Task028F terminal publication or review-required status to the
next observer attempt. `RunAtLoad` provides a prompt best-effort check after
login. Neither objective is a hard delivery guarantee.

## Acceptance criteria

Task034A design is complete when independent review agrees that it:

- provides a local proactive signal without model-token use;
- preserves Task028B and Task028F authority and the current active schedule;
- enforces one canonical observer identity per source schedule-plan hash;
- states that Task028F's public terminal status cannot distinguish clean and
  review-required terminal subtypes;
- cannot leak dynamic or manager-specific values into Notification Center;
- enforces automatic at-most-once attempts across post-claim crash boundaries
  while stating the corresponding missed-notification risk;
- does not equate command acceptance with visible delivery;
- separates synthetic implementation, current-plan preparation, visual testing
  and activation into distinct reviewed gates;
- fails closed on source, code, plan, lifecycle and receipt uncertainty; and
- makes no backup, remote-delivery or laptop-off guarantee.

Passing Task034A authorizes only Task034B implementation in synthetic isolation.
It does not authorize a current-plan observer, real notification, installation,
activation, Task028F deactivation, FPL refresh, journal change, model work,
Task033C2 or Task026C.
