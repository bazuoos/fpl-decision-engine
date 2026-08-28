# Test-only artifact fixtures

These files exist only to make the committed test suite deterministic in a
tracked-files-only checkout. Production code never discovers artifacts below
`tests/fixtures`; live and historical runtime artifacts remain under the
Git-ignored `data/` tree.

`frozen_gw2/` contains the minimum reviewed GW2 files needed to exercise the
projection provider, fixed-squad/one-transfer decisions, and Task 017
reliability diagnostics. The binary and JSON source bytes retain their reviewed
SHA-256 values. These assertions prove that the committed test fixtures match
the reviewed production bytes from which they were copied; they do not inspect
or prove the continued state of a developer's Git-ignored production files.
The fixture contains the full 610-player GW2 clean/feature/projection universe
and all 2,107 reviewed Task 016 legal candidates, rather than a reduced player
sample. Tests rewrite only absolute filesystem links in a temporary copy of the
Task 016 decision manifest.

`artifact_metadata/` contains reviewed historical-v2/v3/v3.1 and Task 018D
manifest bytes. Their fixed hashes prove the identity of the committed metadata
copies, not that a developer's ignored production artifacts remain unchanged.
Historical player-performance and Task 018 result tables are deliberately not
committed. Tests instead construct non-performance synthetic Parquets and
placeholder output bytes in temporary directories. Hashes calculated for those
temporary outputs validate integrity-checking behavior only; they are not
production-artifact hashes and contain no real experiment metric tables. The
fixtures preserve the structural grain, missingness, chronology, development-
only holdout, and hash-validation contracts without exposing sealed holdout
results or creating an alternate production dataset.

Production code must never discover or reference this directory. A permanent
test scans `src/` for such a dependency.
