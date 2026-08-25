# fpl-decision-engine

A small, growing data pipeline for Fantasy Premier League (FPL) data.

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

There are no third-party runtime dependencies.

## Fetch data

From the repository root, run:

```bash
python -m fpl_decision_engine
```

The season defaults to `2026-27`. It can be changed explicitly:

```bash
python -m fpl_decision_engine --season 2026-27
```

The installed `fpl-fetch` command is equivalent. Use `--data-root` to choose a
different snapshot root.

## Run tests

```bash
python -m unittest discover -s tests
```

The tests use local fake HTTP responses and do not require network access.
