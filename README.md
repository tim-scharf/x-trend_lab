# X Trend Lab v2 (minimal)

Minimal clean starting point for the next version of the lab.

## What this version keeps
- LLM-generated query baskets
- X recent counts collection
- candidate scoring from recent counts
- prediction batch snapshots for later evaluation
- a simple loop runner

## What this version does not include yet
- tweet inspection
- X trends / WOEID collection
- rich reporting / charts
- old generated JSON files and old SQLite data

## Folder layout
- `config.py` – all tunable settings
- `scripts/generate_queries.py` – ask OpenAI for new query baskets
- `scripts/collect_counts.py` – collect X recent counts for a generated query file
- `scripts/score_candidates.py` – rank current queries by recent acceleration
- `scripts/save_prediction_batch.py` – save a point-in-time snapshot of the latest query batch
- `scripts/evaluate_predictions.py` – placeholder for lagged fitness evaluation
- `scripts/run_loop.py` – small orchestration script
- `data/runtime/x_trends.db` – live SQLite database
- `data/generated/` – generated query JSON files
- `data/snapshots/` – saved prediction batches
- `logs/reasoning/` – future markdown reasoning logs

## Quick start
1. Create `.env` from `.env.example`
2. Install dependencies: `pip install -r requirements.txt`
3. Generate queries:
   `python scripts/generate_queries.py`
4. Collect counts for the newest query file:
   `python scripts/collect_counts.py data/generated/<file>.json`
5. Score current candidates:
   `python scripts/score_candidates.py`
6. Save a prediction batch snapshot:
   `python scripts/save_prediction_batch.py data/generated/<file>.json`

## Notes
- This version keeps paths explicit under `data/` so the project root stays clean.
- `evaluate_predictions.py` is intentionally lightweight for now; it is the next place to tighten the true fitness function.
