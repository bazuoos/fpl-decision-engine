# Task033C2 protocol clarification required

Status: **STOPPED_BEFORE_IMPLEMENTATION**. This is a design-blocker report,
not a completed C2 implementation or an experiment result.

## Verified repository state

- Primary checkout HEAD: `e10d4c9dc10467730cdb90fcbea494dcdf920b6f`.
- Isolated worktree base and development branch HEAD:
  `5e7f3c02592e9d09bd6e2b2e1c6d85b8691bbf08`.
- Branch: `codex/task033a-decision-robustness-design`.
- The former Task033 worktree directory was absent. Each of the four prunable
  registrations was checked for both existence and symlink status before Git
  pruning. No live worktree was deleted or altered. A fresh isolated worktree
  was created on the existing development branch.
- The two unrelated untracked Task025 files remain in the primary checkout.
- No applicable `AGENTS.md` was found in the checked ancestors or worktree.
- The frozen amendment is unchanged from commit `d6716bc`; its SHA-256 is
  `ef96bb7afe138d82754d22dea12f7028b57a28ade012a24797a0ae4a1fd1fef4`.
- The supplied CI and Claude-review results remain owner-reported context;
  no remote CI or PR state was fetched or changed during this task.

## Blocking ambiguity: cross-season gameweek-block coupling

The governing source is
`docs/project/TASK033B1B_CANDIDATE_FREEZE_AMENDMENT.md`, section 7,
particularly lines 636-646 at the base above.

It fixes the serial bootstrap seeds, 9,999 replicates, length-four blocks,
34 possible starts, ten drawn blocks and truncation to 37 positions. It also
explicitly fixes one player-multiplicity draw on the union of verified player
codes, reused across seasons. It does not explicitly specify whether the
ten block starts are drawn independently for each development season or
drawn once and reused across both seasons.

These are different joint resampling laws, even though both give each season
the stated marginal moving-block resampling rule and preserve the explicitly
shared player multiplicities:

1. Independent season blocks: draw ten starts for the first season and a
   separate ten starts for the second season.
2. Shared season blocks: draw ten starts once and use the same resulting
   gameweek multiplicities in both seasons.

The earlier crossed-bootstrap paragraph at lines 508-517 specifies draws for
each season. Independent season blocks would be a plausible intended extension
of that paragraph. The later serial paragraph explicitly changes cross-season
dependence for players, however, without explicitly stating which earlier
season-independence rules carry over to block draws. This report does not claim
shared season blocks are the intended design. It requests an authoritative
clarification instead of silently choosing a joint law.

### Why this changes inference

Consider generated, player-invariant paired GW loss differences
`d_1(g)=(g-20)/1000` and `d_2(g)=-d_1(g)` on GWs 2-38. The original seasonal
means and their equally weighted aggregate are zero. These are illustrative
loss differences, not evaluated predictions or an eligible study population.

With shared GW multiplicities, the aggregate replicate mean is exactly zero
for every draw. With independent GW multiplicities it need not be zero. For
example, repeatedly drawing the valid block 2-5 in season one and the valid
block 35-38 in season two, each ten times and truncated to 37 positions, gives
an aggregate paired mean of exactly `-33/2000` instead of zero.

Thus the covariance of the aggregate and season-specific replicate endpoints
is not determined solely by the specified marginal block law. Section 7's
family maximum `max_j[(Dhat_j-Dstar_bj)/s_j]` depends on their joint law.
Its quantile, simultaneous upper bounds and potentially package admission
can differ. No actual candidate gate flip or full-family numerical result
has been computed or claimed here.

Seeds do not resolve this choice. A fully reproducible clarification should
also fix the order of the shared-player and season-block draws and the
canonical ordering of verified player codes. The central blocker is the
joint resampling law, not merely a choice of code layout or output format.

## Required resolution

Have the owner and independent reviewer explicitly confirm whether the serial
bootstrap uses independent block-start draws per season or one shared block
sequence across seasons, and freeze the corresponding RNG draw order. If the
review concludes that the existing text already determines this unambiguously,
identify the exact controlling passage and its interpretation. No new season,
real outcome, threshold, candidate, seed or error allocation is needed to
resolve this design question.

The current owner request explicitly says to stop and report an ambiguous
frozen protocol rather than choose a convenient interpretation. Section 11
of the amendment, lines 805-807, also directs recording a design blocker when
a formula is ambiguous. Implementation has stopped under that instruction.

## Changes and validation

The only proposed repository addition is this report. No C2 source, schema,
tests, exports, prediction constructor, evaluator, resampler or selection API
has been added. Existing source and the frozen protocol are unchanged.

Baseline validation in the isolated worktree:

```text
PYTHONPATH=src <existing-venv-python> -m unittest discover -s tests -p 'test_task033c*.py'
44 tests passed; zero skips; zero warnings in captured output; 0.432 seconds.
```

This includes the existing C1 uncertainty, synthetic-feasibility and research
architecture tests. It is baseline evidence, not C2 correctness or feasibility.
The interpreter is CPython 3.14.5 and NumPy is 2.5.2 from the existing environment;
no dependency installation or environment synchronization occurred.

C2-focused tests, complete Python suite, fresh-checkout-equivalent execution,
C2 schema/compilation validation, 9,999-replicate feasibility, deterministic C2
repeat hashes, peak RSS and computational-thread measurements were **not run**:
there is no C2 implementation to validate. No C2 resource ceiling is claimed
exercised. Existing baseline tests do not establish C3 feasibility or any
decision-runtime ceiling. Repository whitespace, empty staged diff, index-path
privacy and review-package identity checks are reported separately in the
sanitized review package.

## Scope and stop state

All changes remain uncommitted and unstaged. No real/private/sealed/historical
result/manager/confirmation inputs were read, no Gate 0 or real experiment was
run, and no confirmation season was resolved. Production xFP, decisions,
journals, schedules, monitors and private evidence were not changed. No PR
update, commit, push, merge, Claude contact, Task033C3 or Task026C occurred.

The intended C2 boundary remains a pure research evaluator consuming supplied
predictions and separately joined outcomes. Filesystem/source resolution,
publication and execution receipts belong to C3. Source/cutoff manifests,
promoted metadata, reviewed loaders, complete C2/C3 synthetic validation and
separate real-source authorization remain prerequisites for real execution.
Untouched confirmation requires its own authorization and custody evidence.

No candidate was selected. No `DO_NOT_CONFIRM` evaluation result or successful
`AWAITING_AUTHORIZED_CONFIRMATION` transition is fabricated from this design
stop. The deferred decision corpus remains `NOT_EVALUATED`. No component
eligibility, predictive superiority, historical immutability, production
suitability or better FPL decisions are established.
