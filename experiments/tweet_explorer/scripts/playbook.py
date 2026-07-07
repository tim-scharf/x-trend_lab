from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from helper import compact_or_empty, summarize_probe, write_json
from probe import initial_probe


ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
RUNS_DIR = EXPERIMENT_DIR / "runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the initial tweet exploration probe.")
    parser.add_argument("tweet_id", help="Exact X Post/Tweet ID to probe.")
    parser.add_argument(
        "--x-budget-usd",
        type=float,
        default=2.0,
        help="Maximum X API budget for this exploration run. Default: 2.0.",
    )
    parser.add_argument(
        "--max-openai-calls",
        type=int,
        default=4,
        help="Maximum OpenAI planning/reassessment calls allowed. Default: 4.",
    )
    return parser.parse_args()


def run_initial_probe(tweet_id: str, x_budget_usd: float, max_openai_calls: int) -> Path:
    if x_budget_usd <= 0:
        raise ValueError("x_budget_usd must be greater than zero.")
    if max_openai_calls < 0:
        raise ValueError("max_openai_calls must be non-negative.")

    probe = initial_probe(tweet_id)
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    run_id = now.strftime("%Y%m%d_%H%M%S")
    run_dir = RUNS_DIR / tweet_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    x_spent = float(probe["estimated_cost_usd"])
    x_remaining = round(x_budget_usd - x_spent, 6)
    state = "probed" if x_remaining >= 0 else "x_budget_exceeded_by_probe"

    history = {
        "schema_version": 1,
        "tweet_id": tweet_id,
        "run_id": run_id,
        "created_at": created_at,
        "updated_at": created_at,
        "state": state,
        "next_sequence": 1,
        "budget": {
            "x": {
                "budget_usd": x_budget_usd,
                "spent_usd": x_spent,
                "remaining_usd": x_remaining,
            },
            "openai": {
                "max_calls": max_openai_calls,
                "calls_used": 0,
            },
        },
        "steps": [
            {
                "sequence": 0,
                "kind": "probe",
                "created_at": created_at,
                "status": "complete",
                "x_cost_usd": x_spent,
                "openai_calls_used": 0,
                "request": probe["request"],
                "response": probe["payload"],
                "tweet": compact_or_empty(probe.get("tweet")),
                "author": compact_or_empty(probe.get("author")),
                "cost_basis": probe["cost_basis"],
                "summary": summarize_probe(probe),
            }
        ],
    }
    write_json(run_dir / "history.json", history)
    return run_dir


def main() -> None:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    run_dir = run_initial_probe(args.tweet_id, args.x_budget_usd, args.max_openai_calls)
    print(json.dumps({"run_dir": str(run_dir), "history": str(run_dir / "history.json")}, indent=2))


if __name__ == "__main__":
    main()
