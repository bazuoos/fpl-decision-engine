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

## Run tests

```bash
python -m unittest discover -s tests
```

The tests use local fake HTTP responses and do not require network access.
