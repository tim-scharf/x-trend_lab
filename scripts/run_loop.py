import glob
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from config import BOOTSTRAP_MODE, EVAL_LAG_HOURS, GENERATED_DIR, RUN_INTERVAL_MINUTES, SEED_QUERIES


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def newest_generated_file() -> Path:
    files = sorted(glob.glob(str(ROOT / "data" / "generated" / "generated_queries_*.json")))
    if not files:
        raise RuntimeError("No generated query files found.")
    return Path(files[-1])


def run(cmd: list[str]) -> None:
    print("\n" + "=" * 80)
    print("Running:", " ".join(cmd))
    print("=" * 80)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        sys.exit(result.returncode)


def mature_snapshot_count() -> int:
    snapshot_dir = ROOT / "data" / "snapshots"
    files = sorted(snapshot_dir.glob("batch_*.json"))
    if not files:
        return 0

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=EVAL_LAG_HOURS)

    count = 0
    for path in files:
        try:
            payload = json.loads(path.read_text())
            saved_at = parse_iso(payload["saved_at"])
            if saved_at <= cutoff:
                count += 1
        except Exception:
            continue

    return count


def write_seed_queries_file() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    payload = {
        "strategy_notes": "Bootstrap seed queries used because no mature prediction batches exist yet.",
        "queries": SEED_QUERIES,
        "model_used": "seed_bootstrap",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "generation_settings": {
            "seed_mode": True,
            "run_interval_minutes": RUN_INTERVAL_MINUTES,
            "eval_lag_hours": EVAL_LAG_HOURS,
        },
    }

    path = GENERATED_DIR / f"generated_queries_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def main() -> None:
    print(f"Run interval minutes: {RUN_INTERVAL_MINUTES}")
    print(f"Evaluation lag hours: {EVAL_LAG_HOURS}")

    run([sys.executable, "scripts/evaluate_predictions.py"])
    run([sys.executable, "scripts/build_query_history.py"])

    ready_count = mature_snapshot_count()
    print(f"\nMature snapshot count: {ready_count}")

    if BOOTSTRAP_MODE and ready_count == 0:
        print("\nNo mature batches yet. Using hardcoded seed queries for bootstrap.")
        latest = write_seed_queries_file()
    else:
        print("\nMature batches detected. Using LLM-generated queries.")
        run([sys.executable, "scripts/generate_queries.py"])
        latest = newest_generated_file()

    print(f"Newest generated file: {latest}")

    run([sys.executable, "scripts/collect_counts.py", str(latest)])
    run([sys.executable, "scripts/score_candidates.py"])
    run([sys.executable, "scripts/save_prediction_batch.py", str(latest)])


if __name__ == "__main__":
    main()