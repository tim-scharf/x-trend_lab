# Experiment: Tweet Explorer

## Purpose

Explore one X Post from a known tweet ID with strict cost accounting.

The current loop is:

```text
tweet_id -> probe -> media_download -> (plan -> create_plan -> cost_verifier -> execute_plan) until stop -> history_summary
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

with `author_id` and `attachments.media_keys` expansion. If media URLs are returned, the playbook downloads the media bytes into the run folder.

Expected cost:

```text
1 Post read: $0.005
1 User read: $0.010
1 Media metadata request: $0.005 when media is present
Typical total with media: $0.020
```

Output:

```text
runs/
  <tweet_id>/
    <runtime>/
      history.json
      media/
        <media_key>.jpg
        media_manifest.json
```

`history.json` is the append-only run record. It starts with `probe` and `media_download`, then loops through `plan`, `create_plan`, `cost_verification`, and `execute_plan`. After the loop terminates, it appends one `history_summary`. It tracks X budget, OpenAI planner calls, and OpenAI summary calls.

Stop states:

```text
cost_rejected              # verified X cost would exceed remaining budget
openai_call_cap_reached    # planner has used the configured OpenAI call cap
x_budget_depleted          # no X budget remains after execution
execution_partial_failure  # one or more X requests failed
no_new_executable_actions  # planner only produced already-executed or empty work
```

Default caps are `$2.00` X budget and `4` OpenAI planner calls. One final memory summary is enabled by default and tracked separately as `budget.openai.summary_calls_used`.

Disable memory summaries:

```bash
python experiments/tweet_explorer/scripts/playbook.py 1234567890123456789 \
  --no-memory-summary
```

Create a planner response from an existing probe history:

```bash
python experiments/tweet_explorer/scripts/planner.py \
  experiments/tweet_explorer/runs/<tweet_id>/<runtime>/history.json
```

Verify X API cost for a created executable plan:

```bash
python experiments/tweet_explorer/scripts/cost_verifier.py \
  experiments/tweet_explorer/runs/<tweet_id>/<runtime>/history.json
```

Execute the latest verified plan:

```bash
python experiments/tweet_explorer/scripts/execute_plan.py \
  experiments/tweet_explorer/runs/<tweet_id>/<runtime>/history.json
```

Dry-run execution without calling X:

```bash
python experiments/tweet_explorer/scripts/execute_plan.py \
  experiments/tweet_explorer/runs/<tweet_id>/<runtime>/history.json \
  --dry-run
```

Summarize a run into compact investigation memory:

```bash
python experiments/tweet_explorer/scripts/summarize_history.py \
  experiments/tweet_explorer/runs/<tweet_id>/<runtime>/history.json
```

Append that summary back into `history.json`:

```bash
python experiments/tweet_explorer/scripts/summarize_history.py \
  experiments/tweet_explorer/runs/<tweet_id>/<runtime>/history.json \
  --append
```

## Scripts

```text
scripts/
  playbook.py       # orchestration entrypoint
  probe.py          # reusable tweet hydrate function
  planner.py        # OpenAI planner worker
  create_plan.py    # compiles planner output into executable X requests
  cost_verifier.py  # estimates executable X request cost from local pricing docs
  download_media.py # downloads media URLs returned by the initial probe
  execute_plan.py   # executes the verified X request plan and appends results
  summarize_history.py # compresses history into human/LLM investigation memory
  helper.py         # shared JSON and summary helpers
  config.py         # local experiment configuration
```

## Next

Planner and reassessment steps should build on the saved `runs/<tweet_id>/<runtime>/history.json`.
