# Experiment: Tweet Explorer

## Purpose

Explore one X Post from a known tweet ID with strict cost accounting.

The current first step is intentionally small:

```text
tweet_id -> initial probe -> runs/<tweet_id>/
```

## Initial Probe

Run:

```bash
python experiments/tweet_explorer/scripts/run.py 1234567890123456789
```

This calls:

```text
GET /2/tweets/{tweet_id}
```

with `author_id` expansion.

Expected cost:

```text
1 Post read: $0.005
1 User read: $0.010
Typical total: $0.015
```

Output:

```text
runs/
  <tweet_id>/
    manifest.json
    initial_probe.json
    request.json
    tweet.json
    author.json
    ledger.json
```

`initial_probe.json` contains the full raw X payload plus extracted tweet and author fields. The smaller `tweet.json` and `author.json` files are convenience views.

## Scripts

```text
scripts/
  run.py            # initial probe entrypoint
  probe.py          # reusable tweet hydrate function
```

## Next

Planner and reassessment steps should build on the saved `runs/<tweet_id>/initial_probe.json`.
