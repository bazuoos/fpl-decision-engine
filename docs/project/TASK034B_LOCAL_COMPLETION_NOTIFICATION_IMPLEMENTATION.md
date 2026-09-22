# TASK034B — Local completion-notification implementation

## Status and authority

Task034B implements the Task034A observer in synthetic isolation. It does not
prepare an observer for the current Task028F schedule, install a LaunchAgent,
display a real notification, activate or deactivate any service, refresh FPL,
alter a journal or authorize Task034C or Task034D.

The implementation is:

```text
scripts/completion_monitor_local_notification.py
```

Its synthetic and adversarial tests are:

```text
tests/test_completion_monitor_local_notification.py
```

Task028B remains the public completion authority. Task028F remains the schedule
authority. Task034B only interprets Task028F's bounded public `status` output and
may request one generic local macOS notification.

## Frozen-source and observer-repository separation

The current Task028F schedule is bound to a frozen primary checkout. The
notification implementation is developed in a separate committed worktree.
Task034B therefore models two repositories explicitly:

- `source_repository`, `source_expected_commit`, source controller bytes and
  source Python identity describe the frozen Task028F schedule;
- `observer_repository`, `observer_expected_commit`, observer-tool bytes and
  observer Python identity describe the Task034 implementation; and
- the canonical private observer directory is still derived beneath the source
  repository's ignored `data/operations/completion-monitor-notifications/`
  directory from the complete source-plan SHA-256.

This separation lets a future Task034C observer watch the current schedule
without modifying, checking out or restarting the frozen source repository.
The source controller's exact bytes must equal the blob at the source commit.
The observer tool's exact bytes must equal the blob at the observer commit.
Task028F's own `status` preflight remains responsible for detecting broader
source-repository drift.

## Implemented commands

| Command | Implemented behavior | Task034B execution status |
|---|---|---|
| `prepare` | Creates one source-hash-keyed private plan, plist, lock and empty record directories | Tested with synthetic repositories only |
| `verify` | Reopens hashes, schemas, code identities, attempts, lifecycle and receipt | Tested synthetically |
| `run` | Acquires the nonblocking lock, calls exact Task028F `status`, and applies at-most-once delivery rules | Tested with injected runners only |
| `status` | Reports bounded observer lifecycle state using the exact observer label | Tested with injected runners only |
| `activate` | Lints, installs, bootstraps, verifies and kickstarts the exact observer plist | Tested with injected runners only |
| `deactivate` | Boots out the exact observer label and removes only the byte-identical installed plist | Tested with injected runners only |
| `sanitize` | Publishes a bounded summary with no paths, UID, messages or command output | Tested synthetically |
| `test-notification` | Verifies the user's Aqua launchd domain and sends a fixed test-only message after an explicit flag | Never executed against real `launchctl` or `osascript` in Task034B |

No command accepts a shell, executable override, notification text, credential,
network destination or arbitrary observer-output parent.

## Source-status contract

The observer invokes only:

```text
<source-python> -I <exact-task028f-controller> status \
  --plan <exact-source-plan> \
  --plan-sha256-file <exact-source-plan-hash>
```

It accepts strict UTF-8 containing one canonical JSON object followed by one LF,
empty stderr and one of Task028F's documented exit/status pairs. Duplicate keys,
unknown fields, noncanonical layout, exit conflicts, overlong detail, nonempty
stderr and output above 4 KiB fail closed. Optional Task028F detail is discarded
after validation and is never written or displayed.

The 4 KiB rule is an accepted-output limit. `subprocess.run` buffers child
output before this post-hoc rejection and does not provide a hard child RSS
quota.

## Delivery and recovery behavior

The observer uses a private `fcntl.flock` to serialize runs. Lock contention
returns observer-only `OBSERVER_BUSY` with process exit 2 and performs no source
or notification subprocess and no structured write. Task028F source
`ACTIVE_RETRYABLE` instead maps to quiet observer status `ACTIVE_QUIET`.

An actionable source status produces an immutable claim before process creation.
The outcomes are:

- process creation proved impossible: publish `PROCESS_NOT_STARTED`, return
  observer `ACTIVE_RETRYABLE`/exit 2 and permit a later new claim;
- process started but returned nonzero, timed out or became uncertain: publish
  `DELIVERY_OUTCOME_UNCERTAIN`, return `REVIEW_REQUIRED`/exit 3 and never resend
  automatically;
- process returned zero: publish
  `COMMAND_ACCEPTED_NOT_VISIBLE_DELIVERY_PROOF`, then the immutable receipt, and
  become `NOTIFICATION_QUIESCENT`/exit 0; or
- a successful result exists after a crash but its receipt does not: reconstruct
  the receipt from the exact claim/result pair without calling `osascript`.

The observer does not claim visible delivery. A durable claim without a result
is deliberately review-required because the external side effect may already
have occurred.

## Fixed notification content

Production terminal and scheduling-review messages are compile-time constants.
The owner drill uses a third fixed message which explicitly states that it is a
test and reports no completion status. No dynamic value can enter the
AppleScript source.

## Storage and privacy

Plans and records use canonical JSON, sibling SHA-256 files, stable
descriptor-relative reads and exclusive no-overwrite publication. Private files
and directories require owner-only permissions. Open descriptors are matched
back to the validated device and inode; symlinks and hardlinks fail closed.
The source schedule plan may occupy any normalized location beneath the source
repository's `data/` tree, matching Task028F's own preparation boundary.

Healthy and inactive polls write no attempt record. One observer permits at most
64 attempt identities. Every attempt, lifecycle and notification record is
limited to 1 KiB. The plist is limited to 16 KiB and sends scheduled stdout and
stderr to `/dev/null`.

Sanitized output contains only the plan hash, attempt count, receipt-present
boolean, format and sanitized status. It omits absolute paths, usernames, UIDs,
message text, source detail and subprocess output.

## Verification completed

Task034B verification used synthetic repositories, plans, records and injected
command runners only:

- 36 focused tests passed;
- 735 full Python tests passed;
- Python syntax compilation passed;
- `git diff --check` passed; and
- a synthetic generated LaunchAgent plist passed macOS `/usr/bin/plutil -lint`.

The focused suite includes direct negative coverage for the real lifecycle
gate's prepared and deactivated states, source-expiry margin and maximum-plan
lifetime, Git-ignore failure, tracked/staged changes, unacknowledged untracked
paths, observer-tool commit drift, source-controller commit drift and refusal to
remove an installed plist without the explicit removal flag. Inert lifecycle
tests assert that no source or notification subprocess runs and no attempt
record is written.

The suite called no live `launchctl`, `osascript`, FPL endpoint, model, Codex
task or OpenAI API. It did not read or change the active Task028F schedule.

## Remaining gates

Task034B requires independent adversarial review before staging or commit.
Passing that review authorizes only commit and CI verification of this synthetic
implementation.

Task034C must separately prepare and review the one canonical observer for the
current exact Task028F source plan. Task034D must separately run the fixed test
notification, obtain owner confirmation of visible receipt and request explicit
activation authorization. Until those gates pass, there is no active local
completion notification.
