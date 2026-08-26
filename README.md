# fpl-decision-engine

A small, growing data pipeline for Fantasy Premier League (FPL) data.

## Raw and clean data

Raw data is the official API response preserved byte-for-byte as an immutable
snapshot under `data/raw`. Clean data is a typed analytical projection derived
from one raw snapshot and stored under `data/clean`. Transformations never edit
the source snapshot, and neither raw nor clean snapshots are overwritten.

## First pipeline

The first pipeline fetches the official FPL
[`bootstrap-static`](https://fantasy.premierleague.com/api/bootstrap-static/)
dataset, validates that the request succeeded and that the response is valid JSON,
then saves the original response bytes as an immutable raw snapshot.

Snapshots use a filesystem-safe UTC timestamp with microsecond precision:

```text
data/raw/fpl/2026-27/20260824T010203.456789Z/bootstrap-static.json
```

Existing snapshot directories and files are never overwritten.

## Setup

Python 3.10 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --editable .
```

DuckDB is the only runtime dependency. It provides typed analytical validation
and Parquet output without requiring pandas or PyArrow.

## Fetch data

From the repository root, run:

```bash
python -m fpl_decision_engine
```

This legacy command remains equivalent to `python -m fpl_decision_engine fetch`.

The season defaults to `2026-27`. It can be changed explicitly:

```bash
python -m fpl_decision_engine --season 2026-27
```

The installed `fpl-fetch` command is equivalent. Use `--data-root` to choose a
different snapshot root.

## Transform players

Transform the latest local raw snapshot without fetching new data:

```bash
python -m fpl_decision_engine transform-players
```

The result is written to:

```text
data/clean/fpl/2026-27/<snapshot_timestamp>/players.parquet
```

The clean expected-stat columns use concise analytical names: `xg`, `xa`,
`xgi`, and `xgc`, plus `xg_per_90`, `xa_per_90`, `xgi_per_90`, and
`xgc_per_90`. These map to the raw expected goals, assists, goal involvements,
and goals conceded fields. `ep_this` and `ep_next` are numeric but remain the
official FPL-provided expected-points estimates; they are not predictions made
by this project. Set-piece order nulls are preserved, and the transformation
does not reconstruct defensive-contribution scoring thresholds.

Every row includes the season, official source URL, snapshot directory timestamp,
source snapshot path, and source SHA-256 digest.

Query a generated dataset with DuckDB from Python:

```python
import duckdb

rows = duckdb.sql("""
    SELECT web_name, team_short_name, total_points
    FROM read_parquet('data/clean/fpl/2026-27/*/players.parquet')
    ORDER BY total_points DESC
    LIMIT 10
""").fetchall()
```

## Fixtures and gameweek history

The pipeline uses only these official FPL JSON endpoints:

- `https://fantasy.premierleague.com/api/fixtures/`
- `https://fantasy.premierleague.com/api/element-summary/<fpl_player_id>/`

Every ingestion run is tied to an existing bootstrap snapshot. Fetch fixtures
and one element-summary response for every player in that snapshot with:

```bash
python -m fpl_decision_engine fetch-fixtures \
  --snapshot-timestamp 20260825T073532.450889Z
python -m fpl_decision_engine fetch-player-history \
  --snapshot-timestamp 20260825T073532.450889Z
```

Fixture bytes are stored unchanged in the snapshot directory as `fixtures.json`.
Player responses are stored separately as
`player_history/<fpl_player_id>.json`. Provenance manifests record retrieval
timestamps, response hashes, progress, and every failed player ID. The history
fetch is paced and retried, and a partial run is rejected by the clean
transformation rather than silently treated as complete.

Create typed Parquet datasets with:

```bash
python -m fpl_decision_engine transform-fixtures \
  --snapshot-timestamp 20260825T073532.450889Z
python -m fpl_decision_engine transform-player-history \
  --snapshot-timestamp 20260825T073532.450889Z
```

Outputs are written alongside `players.parquet` in the matching clean snapshot:

```text
data/clean/fpl/2026-27/<snapshot_timestamp>/fixtures.parquet
data/clean/fpl/2026-27/<snapshot_timestamp>/player_gameweek_history.parquet
```

Gameweek history contains realized player/fixture results. Keeping its snapshot
time, realized gameweek, fixture, and official gameweek-finalization flags makes
future rolling features and backtests possible without confusing a completed
match with a finalized FPL gameweek.

Query a few realized rows with DuckDB:

```python
import duckdb

rows = duckdb.sql("""
    SELECT gameweek_id, web_name, team_name, opponent_team_name, total_points
    FROM read_parquet(
        'data/clean/fpl/2026-27/*/player_gameweek_history.parquet'
    )
    ORDER BY gameweek_id, total_points DESC
    LIMIT 10
""").fetchall()
```

## Reliable snapshot refresh

The high-level refresh command collects and transforms one coherent official
FPL snapshot:

```bash
python -m fpl_decision_engine refresh --season 2026-27
```

It fetches bootstrap-static, fixtures, and one element-summary response for
every player in that refresh's bootstrap, then creates players, fixtures, and
player-gameweek-history Parquet files under the same snapshot timestamp. Player
IDs are always derived dynamically; new signings require no code or fixed count.

Refresh deliberately does not build features, generate predictions, or run an
evaluation. Those remain explicit commands, so frozen predictions cannot be
changed by data collection.

Every raw response is preserved byte-for-byte. Writes use a temporary file,
file `fsync`, and an atomic rename, which prevents a process interruption from
exposing a torn destination file. The containing directory is not `fsync`ed, so
this is not a claim of full durability against sudden power loss. A
`refresh.manifest.json` records stages, start/completion times, endpoints,
counts, failures, request/retry summaries, source/output hashes, and software
provenance. Transformations begin only after raw collection is complete. Only a
fully validated raw and clean snapshot receives `status: complete`.

Player-history collection is sequential and conservatively paced. Resume an
explicit incomplete snapshot with:

```bash
python -m fpl_decision_engine refresh \
  --resume 20260826T010203.456789Z
```

Resume validates existing responses against their hashes and JSON/player
identity, reuses valid files, and requests only missing or failed players. A
corrupt partial response is quarantined before being fetched again. Completed
refreshes and clean outputs remain immutable, while a normal new refresh always
uses a new timestamp.

A per-snapshot `.refresh.lock` prevents two processes from refreshing or
resuming the same snapshot concurrently; different new snapshot timestamps do
not share a lock. Locks are removed after handled success or failure. After a
hard process termination, inspect the recorded PID and verify that no refresh is
running before explicitly unlocking it:

```bash
python -m fpl_decision_engine refresh-unlock \
  --season 2026-27 \
  --snapshot 20260826T010203.456789Z
```

The command displays available lock metadata and removes only that snapshot's
`.refresh.lock`. It never infers staleness from age, deletes a lock
automatically, or kills a process.

For non-empty player history, response identity is checked using the official
`element` value on every history row. The element-summary payload has no
top-level player ID, so a legitimate empty history has no stronger direct
content identity; it remains tied to the requested player-specific endpoint and
immutable player-ID filename.

HTTPS certificate and hostname verification are always required. The workflow
uses Python/platform trust and augments it with `certifi` only when already
installed. If no usable CA trust is available, refresh fails with an actionable
error; it never disables TLS verification.

## Prediction-ready features

A feature row represents one player, one target gameweek, and one target
fixture. This slightly finer physical grain supports double gameweeks; a player
with no target fixture receives one row with null fixture context. All historical
performance calculations apply `history_gameweek < target_gameweek` before any
prior or rolling aggregation.

The builder also requires the bootstrap snapshot, fixture retrieval, and player-
history retrieval to have completed before the target deadline. This makes the
included status and news fields genuinely pre-deadline state. FPL's
`chance_of_playing_next_round` is populated only when the target is the
snapshot's official `is_next` gameweek; otherwise it remains null and the
reference gameweek is exposed explicitly. Missing history remains null, while
an official zero-minute gameweek remains a real zero. Sample counts and
historical minutes are exposed alongside every rolling rate.

Build GW2 features from the specified pre-deadline snapshot:

```bash
python -m fpl_decision_engine build-features \
  --target-gameweek 2 \
  --snapshot-timestamp 20260825T073532.450889Z
```

Without `--snapshot-timestamp`, the latest complete snapshot whose inputs were
all collected before the target deadline is selected. Output is partitioned by
target gameweek so multiple historical targets can coexist:

```text
data/features/fpl/2026-27/<snapshot_timestamp>/gameweek=2/player_gameweek_features.parquet
```

Query low-sample attacking features with DuckDB:

```python
import duckdb

rows = duckdb.sql("""
    SELECT web_name, target_opponent_team_name, target_home_away,
           prior_total_minutes, previous_gw_xg, prior_xg_per_90,
           prior_gameweeks_with_data
    FROM read_parquet(
        'data/features/fpl/2026-27/*/gameweek=2/*.parquet'
    )
    ORDER BY prior_xg_per_90 DESC NULLS LAST
    LIMIT 10
""").fetchall()
```

## xFP v0.1 predictions

xFP v0.1 is an intentionally incomplete, event-based baseline. It closes the
first features → prediction → explanation → later evaluation loop; it is not a
trustworthy recommendation engine. For each player and target fixture it uses:

```text
expected_minutes_v01 = clamp(previous_gameweek_minutes, 0, 90)
expected_goals_v01   = prior_xg_per_90 × expected_minutes_v01 / 90
expected_assists_v01 = prior_xa_per_90 × expected_minutes_v01 / 90

fixture_xfp_v01 = appearance_xfp_v01
                 + expected_goals_v01 × positional_goal_points
                 + expected_assists_v01 × 3
```

Appearance points are deterministic: 0 minutes scores 0, 1–59 scores 1, and
60–90 scores 2. Goal points are the official positional values: 10 for a
goalkeeper, 6 for a defender, 5 for a midfielder, and 4 for a forward. The
small explicit mapping makes this model rule visible. Assist points are 3 for
every position.

Previous-gameweek minutes are persistence, not a calibrated minutes forecast.
An explicit zero chance of playing for the target next gameweek, or a safely
observed suspended/unavailable status, gates minutes to zero; uncertain 25/50/75
percent values are not multiplied into minutes. With no prior record, expected
minutes and the fixture total remain null. With usable expected minutes but no
attacking rate, the missing goal/assist contributions count as zero in the total
while `attacking_rate_available` and `prediction_complete` expose the gap.

Only historical xG/90 and xA/90 calculated from gameweeks strictly before the
target are used. FPL `ep_next`, form, target-gameweek events, points, and fixture
scores are never inputs. GW2 has only one prior gameweek, so every player with
that evidence is marked `low_sample`; the factual rule is fewer than three prior
gameweeks, not a calibrated confidence score.

Generate GW2 predictions from already-collected data (no network fetch occurs):

```bash
python -m fpl_decision_engine predict-xfp \
  --target-gameweek 2 \
  --snapshot-timestamp 20260825T073532.450889Z
```

If the safe feature file is absent, the command builds it from matching local
raw and clean inputs. Existing feature and prediction outputs are never
overwritten. Outputs are written to:

```text
data/predictions/fpl/2026-27/<snapshot_timestamp>/gameweek=2/xfp_v01_fixtures.parquet
data/predictions/fpl/2026-27/<snapshot_timestamp>/gameweek=2/xfp_v01_gameweek.parquet
```

The fixture file keeps every component and its inputs. The gameweek file sums
fixture predictions, so doubles sum both fixtures and blanks retain the player
with zero xFP. Its component columns also prepare for later comparison with a
component-matched actual target (appearance/goals/assists) and full actual FPL
points. FPL `ep_next` can later be an external full-points baseline.

Query the gameweek output with DuckDB:

```python
import duckdb

rows = duckdb.sql("""
    SELECT web_name, position, gameweek_xfp_v01, low_sample
    FROM read_parquet(
        'data/predictions/fpl/2026-27/*/gameweek=2/xfp_v01_gameweek.parquet'
    )
    ORDER BY gameweek_xfp_v01 DESC NULLS LAST
    LIMIT 10
""").fetchall()
```

Excluded from v0.1 are clean sheets, defensive contribution, saves, bonus,
goals-conceded deductions, cards, fixture strength, home/away adjustments,
shrinkage, positional priors, and machine learning. A future appearance model
should replace the deterministic rule with estimates of `P(minutes > 0)` and
`P(minutes >= 60)`.

## Leakage-safe evaluation

The evaluation methodology is defined before results are available so GW2
outcomes cannot influence metric choice or change the frozen model. Evaluation
reads an immutable gameweek prediction and a separate, later realized-data
snapshot; it never recalibrates or rewrites xFP.

Two targets are kept distinct:

- `actual_modeled_points_v01` independently scores only appearance, goals, and
  assists using official positional rules. This tests what v0.1 actually models.
- `actual_total_fpl_points` sums official realized FPL points. This tests how
  useful the incomplete baseline is for the full game.

For both targets, MAE is the average absolute miss, RMSE penalizes large misses
more heavily, and bias is `prediction - actual`; positive bias therefore means
overprediction. Each metric includes only rows where that predictor and actual
target are both non-null; missing values are never replaced with zero. Outputs
separately report eligible/evaluated players, missing predictions, missing
actuals, predictor coverage, and coverage among players with actuals. This is
reported independently for xFP, `ep_next`, and other baselines so headline
metrics cannot hide different populations.

The evaluator requires both frozen prediction files. It verifies that fixture-
level xFP sums exactly to gameweek xFP before comparing it with independently
fixture-scored realized points. This makes double-gameweek aggregation explicit.
A blank is zero only when the frozen fixture file contains the player's single
verified no-fixture row; missing or corrupt fixture evidence is rejected rather
than interpreted as a blank.

Actual goal points use the position preserved in the frozen pre-target
prediction, never a position from a later player/history snapshot. Expected
minutes, `low_sample`, and attacking-rate availability are likewise copied from
the frozen prediction and are not recomputed from realized data. Metrics are
also split by FPL position and by three concise diagnostics: actual minutes
band, `low_sample`, and attacking-rate availability.

The primary ranking population is every player with both frozen xFP and a
realized target-gameweek result, including zero-minute players. An appeared-only
ranking may be added later as a separate diagnostic, but is not the primary
metric. Ranking diagnostics use tie-aware Spearman correlation plus strict
N-player top-N sets (default 10); `fpl_player_id` is the final deterministic
tie-breaker at the cutoff. Ranking is secondary to the point-error metrics. FPL
`ep_next` is compared only with full FPL points and only when its matching
pre-deadline `is_next` snapshot and hashes can be proven. Leakage-safe previous-
gameweek and average-prior-points baselines are read from the frozen feature
input. A later snapshot is never used to reconstruct a missing baseline.

Evaluation requires the official event to have both `finished=true` and
`data_checked=true`, every target fixture to be finished and data-checked, and
realized fixture/history retrieval to have occurred after the deadline. Until
those conditions hold, refusal is the expected successful behavior.

Once a finalized realized snapshot has been collected and transformed, run:

```bash
python -m fpl_decision_engine evaluate-xfp \
  --target-gameweek 2 \
  --model-version v0.1 \
  --prediction-snapshot-timestamp 20260825T073532.450889Z \
  --realized-snapshot-timestamp <post-gameweek-snapshot>
```

No network request is made. Immutable results are written under:

```text
data/evaluations/fpl/<season>/gameweek=<gw>/<model_version>/<evaluation_timestamp>/
```

The directory contains player-level errors, overall metrics, position metrics,
diagnostic metrics, ranking results, and a manifest recording prediction and
realized-source paths, hashes, timestamps, and baseline provenance. Player rows
preserve doubles through fixture-level actual scoring before aggregation and
retain blank-gameweek players with zero actual points.

## Restricted historical datasets

The historical builder supports restricted/pseudo-backtesting for 2023/24 and
2024/25. It retrieves community-preserved official FPL files only from these
audited, commit-pinned sources:

- Vaastav `Fantasy-Premier-League` at
  `c2add969e11ec19002a091f8aa60164c9a255854`
- Randdalf `fplcache` at
  `36bdcddc5764628ec8ef9429dcdc1aafe4f6a867`

Every source has an approved SHA-256 in the source catalogue and is rejected
before parsing if its observed hash differs. The 76 selected Randdalf snapshots
(38 per season) are verified as strictly pre-deadline, with the target event
marked `is_next` and the exact stored deadline. They provide genuinely
pre-deadline player state such as status, news, price, ownership, cumulative
statistics, and benchmark-only `ep_next`.

Vaastav performance values are fixture-level archived actuals. Its fixture
table provides the finalized event assignment, which is useful for reconstructing
actuals but is explicitly labelled `finalized_fixture_assignment`; the project
does not claim that assignment was historically known before the deadline.
Consequently these outputs are restricted/pseudo-backtest data, not a perfect
historical deadline replay.

Build the immutable source cache and Parquet datasets with:

```bash
python -m fpl_decision_engine build-historical
```

Pinned raw files are cached under `data/historical/raw/`. Typed outputs and an
ingestion/provenance manifest are written under:

```text
data/historical/clean/historical-v2/<season>/
```

The player-fixture, fixture, identity, pre-deadline state, prediction-feature,
separately labelled actual, and reconciliation-exception datasets are all
Parquet. Existing `historical-v2` output is never overwritten. Both generated
historical roots are Git-ignored.

Within a season, player identity is `(season, element_id)`. FPL `code` is only
the audited candidate bridge across seasons; `element_id` alone must never join
seasons. Predictor performance must be known before the frozen target deadline:
the source row must have `gameweek < target_gameweek` and its fixture kickoff
must be strictly before the target deadline from the selected Randdalf snapshot.
The finalized event number alone never establishes temporal eligibility.

This guard excludes a postponed or rearranged lower-event fixture when its
kickoff is at or after the target deadline. A double contributes each eligible
fixture separately; if only part of a historical double had kicked off, only
that known portion can enter prior aggregates and the previous-GW context is
marked partial. A finalized blank has no fixture and remains an explicitly
verified blank rather than a chronological exclusion. Archived fixture actuals
and their finalized event assignments remain intact in the separate actual
datasets. The archives do not expose an exact match-completion timestamp, so
the machine-enforced chronology boundary is the strict kickoff cutoff.

Previous-GW minutes mean the sum from the calendar gameweek immediately before
the target, including all double-GW fixtures—not the most recent fixture. A
verified team blank remains null with an explicit blank flag; a zero-minute
fixture and a player absent from the prior pre-deadline universe are separate
states.

Historical total points remain exactly those awarded under that season's FPL
rules. The independent v0.1-compatible actual section reconstructs only
appearance, historical-position goal, and assist points. It does not retrofit
DEFCON, 2026/27 BPS, or modern assist rules. Missing expected statistics remain
null, assistant-manager pseudo-elements are excluded by position semantics, and
source reconciliation differences are reported without altering fixture data.
For the pinned 2024/25 sources, Evan Ferguson's differences are traced to GW27
fixture 266: `merged_gw.csv` row 18254 has 17 minutes, xA 0.00, xGI 0.11, and
xGC 0.06, whereas the pinned official cumulative snapshots advance across GW27
by 34 minutes, xA 0.01, xGI 0.12, and xGC 0.80. `players_raw.csv` retains the
later corrected totals. The fixture row is preserved and the exception is
classified as an upstream archived-row/later-correction inconsistency.

## Frozen xFP v0.1 historical baseline

The historical backtest measures the existing xFP v0.1 formula without tuning
or recalibration. It reads only immutable `historical-v2` inputs for 2023/24 and
2024/25, target GWs 2–38, and retains the
`restricted_pseudo_backtest` classification. Finalized fixture assignments are
useful for pseudo-historical measurement but are not claimed to be a perfect
deadline replay.

Run it once with:

```bash
python -m fpl_decision_engine backtest-xfp-v01
```

Immutable artifacts are written under:

```text
data/historical/backtests/xfp-v01-baseline-v1/
```

Fixture predictions are produced before player-gameweek aggregation. Doubles
sum their fixture predictions; verified blanks remain explicit zero prediction
and zero actual rows. Actual modeled points are independently reconstructed at
fixture grain from appearance, frozen-position goal points, and three points per
assist. Archived full FPL points remain under their historical season rules.

The predictor preserves the live v0.1 expected-minutes and availability gates,
including its documented incomplete-attacking-rate behavior. Missing expected
goal/assist events remain null and `prediction_complete=false`; v0.1's numeric
appearance-only total remains available as a separately flagged incomplete
prediction. Missing expected minutes leave xFP null. Metrics never impute a
missing prediction or actual as zero, and coverage is reported separately.

Outputs include player-GW observations, fixture predictions/actuals, overall,
season, position and per-GW metrics, expected-minutes and component diagnostics,
fixed prior-sample/availability bands, fixed calibration bins, zero and
pre-deadline `ep_next` baselines, and strict top-10/25/50 overlap. Rankings are
calculated within each season/gameweek, with `element_id` ascending as the final
deterministic tie-breaker. The manifest records every historical-v2 input hash,
the ingestion-manifest hash, cutoff and coverage policies, output hashes, and
generation time. A completed backtest version is never overwritten.

## Expected-minutes v0.2 experiment

Task 009 is an isolated, preregistered experiment; it does not change xFP v0.1
or any live prediction. It compares previous-GW persistence (`M0`) with only
three declared alternatives: a rolling three-GW mean (`M1`), a rolling five-GW
mean (`M2`), and fixed 60%/30%/10% recency weights (`M3`). All xG/xA rates,
position scoring, appearance/goal/assist mechanics, availability hard gates,
blank/DGW handling, and incomplete-rate behavior remain frozen from v0.1.

Run the experiment once with:

```bash
python -m fpl_decision_engine experiment-minutes-v02
```

It uses 2023/24 as development data. A candidate reaches the untouched 2024/25
holdout only when it clears all preregistered development thresholds: at least
5% minutes-MAE improvement, 3% minutes-RMSE improvement, and 2% modeled-xFP-MAE
improvement, with no more than one percentage point less coverage. Comparisons
use common complete M0/candidate/actual pairs, while each candidate's coverage
is reported separately. If more than one qualifies, selection uses modeled-xFP
MAE reduction, modeled-xFP RMSE, minutes MAE, then the fixed simplicity order
`M1`, `M2`, `M3`. If none qualifies, holdout evaluation stops.

A selected candidate passes holdout only if the same improvement thresholds
hold, appearance MAE does not worsen, modeled-xFP Spearman falls by no more than
0.01, and coverage falls by no more than one percentage point. Ranking overlap
is diagnostic and is not a pass criterion. Passing means only “promote this
candidate to an xFP v0.2 design”; it does not update the live model.

Windows use only calendar FPL GWs strictly before the target whose eligible
fixture kickoffs are before the frozen target deadline. A genuine fixture with
zero player minutes is an observed zero. A team blank and a player not yet in
the historical universe are missing observations, not zero. Eligible DGW
fixture minutes are summed to player-GW before entering a window; one observed
GW is the minimum needed to form a candidate estimate. `M3` renormalizes its
fixed weights over observed GWs. Each per-fixture estimate is capped to 0–90,
then the frozen availability hard gate is applied.

Immutable, Git-ignored outputs are written to:

```text
data/historical/experiments/minutes-v02-experiment-v1/
```

They contain fixture and player-GW candidate predictions, development metrics,
common-pair comparisons, strict top-10/25/50 diagnostics, fixed diagnostic
populations, the selection decision, holdout outputs only if a development
winner exists, and a manifest containing input/output hashes and all decision
rules. The completed experiment directory is never overwritten. The manifest
also reports how much of the fixed realized-minutes oracle's 34.53% modeled-MAE
improvement the selected candidate captures; the oracle remains an
evaluation-only ceiling, not model performance.

## Attacking-rate v0.2 experiment

Task 010 is a second isolated, preregistered experiment. It keeps frozen v0.1
expected minutes, availability, blanks, doubles, appearance scoring, position
goal points, aggregation, populations, and evaluation definitions unchanged.
Only the cumulative prior xG/90 and xA/90 inputs vary.

Run it once with:

```bash
python -m fpl_decision_engine experiment-attacking-rates-v02
```

The candidates are the raw-rate control (`S0`), position-aware shrinkage with
fixed `K=450` (`S1`) or `K=900` (`S2`), and a fixed positional-prior replacement
below 180 prior minutes (`S3`). xG and xA priors are calculated separately as
`90 * sum(prior event value) / sum(usable prior minutes)`. Each prior is rebuilt
for the target's frozen historical position using only lower-GW fixtures whose
kickoff precedes that target's frozen deadline. It is not a retrospective
season-wide prior, and no league fallback is invented when a positional prior
is unavailable.

Development selection uses only 2023/24 common pairs among players with actual
target minutes above zero. Actual minutes define that diagnostic population
only after candidate predictions exist; they are never predictors. A candidate
must improve both goal and assist Spearman by at least 0.01, keep modeled MAE
and RMSE worsening within 1%, keep absolute-bias worsening within 0.02 points,
and lose no more than one percentage point of coverage. The fixed selection
order then uses mean goal/assist Spearman gain, modeled MAE, modeled RMSE, and
the simplicity order `S1`, `S2`, `S3`.

Only one development winner may reach the 2024/25 confirmation holdout. If no
candidate clears development, holdout processing stops. Passing an eventual
holdout means only that the candidate may proceed to xFP v0.2 design; this
command never modifies or promotes the live model.

Immutable, Git-ignored artifacts are written to:

```text
data/historical/experiments/attacking-rate-v02-experiment-v1/
```

Outputs contain causal positional-prior provenance, fixture and player-GW
candidate predictions, natural and common-pair metrics, strict top-10/25/50
ranking diagnostics, fixed population breakdowns, rate-change-by-sample
diagnostics, the selection decision, conditional holdout results, and a
hash-pinned manifest. A completed experiment directory is never overwritten.

## xFP v0.1 calibration experiment

Task 011 tests post-hoc magnitude calibration without changing xFP v0.1 or any
input feature. The control (`C0`) is the immutable baseline. `C1` is an
unconstrained ordinary-least-squares mapping with an intercept, and `C2` is a
deterministic non-decreasing Pool Adjacent Violators isotonic step mapping.
Both mappings are fitted only on complete 2023/24 modeled-component pairs.
Missing raw predictions remain missing, and verified blanks remain explicit
zero predictions.

Run the immutable experiment once with:

```bash
python -m fpl_decision_engine experiment-calibration-v02
```

Development selection uses exact C0/candidate common pairs. A candidate must
reduce RMSE by at least 3% and MAE by at least 2%; materially improve absolute
bias (or finish within 0.03 points); limit pooled Spearman decline to 0.005;
limit strict top-10/25/50 mean-overlap decline to one percentage point each;
and lose at most one percentage point of natural coverage. If both candidates
qualify, RMSE reduction, then MAE reduction, then the simplicity order `C1`,
`C2` selects one winner.

The 2024/25 confirmation holdout is not evaluated unless one development
winner exists. The frozen winner is applied once without refitting and must
pass every preregistered holdout criterion. Calibration changes prediction
magnitude only; lower error would not establish improved goal/assist signal or
fundamentally better player ranking.

Outputs are written under:

```text
data/historical/experiments/calibration-v02-experiment-v1/
```

They include the exact C1 coefficients, the complete C2 block mapping,
representative transformations, natural and common-pair metrics, strict
ranking diagnostics, fixed raw-xFP calibration bands, diagnostic populations,
the gated selection decision, conditional holdout results, and a hash-pinned
manifest. Existing completed output is never overwritten.

## Run tests

```bash
python -m unittest discover -s tests
```

The tests use local fake HTTP responses and do not require network access.
