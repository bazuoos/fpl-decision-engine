# Weekly workflow readiness — offline assessment

Assessment date: 2026-10-15. Source baseline: merged PR #7, `688ada6`.
This is source-based planning, not permission for data access or execution.
No private/ignored evidence was inspected and no operational command was run.

## Finding

The project has a local guided path, but has not demonstrated a complete
current-owner weekly workflow. A historical constructed-data walkthrough and
green CI establish mechanics, not current-data readiness or predictive benefit.
The next useful step is one bounded offline rehearsal, not C3 implementation,
new authentication infrastructure or a broader optimization feature.

## Existing path and remaining gates

| Step | Repository evidence | What remains unresolved for real use |
|---|---|---|
| Prepare frozen public inputs | [operational runner](../../src/fpl_decision_engine/operational_runner.py) | Exact future target, current approved revision and separately authorized public refresh/preparation |
| Enter and review squad facts | [wizard](../../src/fpl_decision_engine/local_decision_wizard.py), [authoring boundary](../../src/fpl_decision_engine/manager_evidence_authoring.py) | Fresh owner-confirmed 15-player selection, selling prices, bank, free transfers and chip state; no inference from an old screenshot |
| Publish evidence, then run | Wizard has separate exact publication/run confirmations | Separate applicable authority for private entry/publication and engine execution; stopping after either stage must remain possible |
| Read verified result | Wizard uses trusted artifact reader; web consumes verified decision payload | Browser delivery needs an explicit artifact index/root and ID; do not assume automatic wizard-to-browser wiring or browser-independent verification |
| Understand usefulness | PR #7 shows caveats; [product philosophy](FPL_PRODUCT_PHILOSOPHY.md) records limits | Only appearance/goals/assists modeled; weak minutes and missing defensive/other scoring prevent a complete FPL rating |
| Decide and preserve evidence | Human owns actual FPL action; journal is separate | No automatic transfer, journal, outcome comparison or backup authorization; no verified offsite backup |

The supported operational transfer path requires at least one free transfer,
zero modeled transfer cost and `NO_CHIP`. It compares ROLL with one transfer in
one gameweek; it does not optimize a five-transfer rebuild, chips, hits, future
transfer flexibility, substitutions or vice-captain fallback. An unsupported
state must stop, not be silently simplified to fit the engine.

## Proposed next slice: offline owner rehearsal

Use only committed public-derived test fixtures, a clearly fictional squad and
manager identity, an explicitly injected test clock and isolated temporary
storage. Do not access repository ignored data or substitute current facts.
Use the existing tests/harness rather than inventing new operational commands.
This proposal does not itself authorize running it.

Acceptance checks:

1. Complete the guided path without hand-writing evidence JSON; inspect a result
   only through the trusted reader. Clearly label it constructed, not current.
2. Declining publication leaves no evidence or decision; declining the run leaves
   evidence but no decision. Deadline, unsupported-state and invalid-result
   cases stop without displaying an authoritative recommendation.
3. Explain the result and its limitations in plain English without inventing
   football reasons, confidence probabilities or alternative selections.
4. Record usability friction and any browser setup gap separately from semantic
   defects. Do not add routes or change model/optimizer behavior to make the
   demonstration look complete.
5. Retain only sanitized review evidence; never include real manager identifiers,
   private input rows or identifying local paths in a committed report.

Existing focused coverage is in [wizard tests](../../tests/test_local_decision_wizard.py)
and [authoring tests](../../tests/test_manager_evidence_authoring.py). This
assessment inspected source and test definitions; it did not run a new rehearsal
or count historical test reports as fresh execution.

## Subsequent decisions, not this task

After rehearsal, choose whether the next bottleneck is owner usability or model
quality. Minutes robustness, missing scoring and multi-transfer/horizon support
need separately scoped designs and validation/promotion where applicable; none
is approved here. Recovery setup remains owner-paused and must be resumed
explicitly. C3, protected sources, confirmation access and research promotion
remain outside this work. A later real-run proposal must identify its exact
target, inputs, permissions, stop conditions and recovery limitations first.
