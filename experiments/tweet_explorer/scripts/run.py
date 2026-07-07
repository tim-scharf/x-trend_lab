from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from probe import initial_probe


ROOT = Path(__file__).resolve().parents[3]
EXPERIMENT_DIR = Path(__file__).resolve().parents[1]
RUNS_DIR = EXPERIMENT_DIR / "runs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the initial tweet exploration probe.")
    parser.add_argument("tweet_id", help="Exact X Post/Tweet ID to probe.")
    return parser.parse_args()


def run_initial_probe(tweet_id: str) -> Path:
    probe = initial_probe(tweet_id)
    created_at = datetime.now(timezone.utc).isoformat()
    run_dir = RUNS_DIR / tweet_id
    run_dir.mkdir(parents=True, exist_ok=True)

    write_json(
        run_dir / "manifest.json",
        {
            "schema_version": 1,
            "created_at": created_at,
            "updated_at": created_at,
            "tweet_id": tweet_id,
            "state": "probed",
            "stages": [
                {
                    "name": "initial_probe",
                    "path": "initial_probe.json",
                    "estimated_x_cost_usd": probe["estimated_cost_usd"],
                    "status": "complete",
                }
            ],
            "ledger": {
                "estimated_x_spent_usd": probe["estimated_cost_usd"],
                "estimated_openai_spent_usd": 0.0,
                "estimated_all_in_spent_usd": probe["estimated_cost_usd"],
            },
        },
    )
    write_json(run_dir / "initial_probe.json", probe)
    write_json(run_dir / "request.json", probe["request"])
    write_json(run_dir / "tweet.json", compact_or_empty(probe.get("tweet")))
    write_json(run_dir / "author.json", compact_or_empty(probe.get("author")))
    write_json(
        run_dir / "ledger.json",
        {
            "estimated_x_cost_usd": probe["estimated_cost_usd"],
            "estimated_openai_cost_usd": 0.0,
            "estimated_all_in_cost_usd": probe["estimated_cost_usd"],
            "cost_basis": probe["cost_basis"],
        },
    )
    return run_dir


def compact_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def main() -> None:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    run_dir = run_initial_probe(args.tweet_id)
    print(json.dumps({"run_dir": str(run_dir)}, indent=2))


if __name__ == "__main__":
    main()
