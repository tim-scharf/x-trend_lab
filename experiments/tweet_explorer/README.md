# Experiment: Tweet Explorer

## Purpose

Explore one X Post from a known tweet ID with strict cost accounting.

The current first step is intentionally small:

```text
tweet_id -> initial probe -> runs/<tweet_id>/<runtime>/
```

## Initial Probe

Run:

```bash
python experiments/tweet_explorer/scripts/playbook.py 1234567890123456789
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
    <runtime>/
      history.json
```

`history.json` is the append-only run record. It starts with the initial `probe` step and tracks X budget plus the OpenAI call cap.

Future loop shape:

```text
probe -> plan -> execute -> plan -> execute -> plan -> execute
```

## Scripts

```text
scripts/
  playbook.py       # orchestration entrypoint
  probe.py          # reusable tweet hydrate function
  helper.py         # shared JSON and summary helpers
```

## Next

Planner and reassessment steps should build on the saved `runs/<tweet_id>/<runtime>/history.json`.
