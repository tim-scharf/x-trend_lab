import glob
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

from config import BOOTSTRAP_MODE, EVAL_LAG_HOURS, GENERATED_DIR, RUN_INTERVAL_MINUTES, SEED_QUERIES

REASONING_MEMORY_MAX_AGE_HOURS = 12
REASONING_MEMORY_LATEST = ROOT / "data" / "memory" / "reasoning_memory_latest.json"


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


def latest_reasoning_memory_updated_at() -> datetime | None:
    if not REASONING_MEMORY_LATEST.exists():
        return None

    try:
        payload = json.loads(REASONING_MEMORY_LATEST.read_text())
        updated_at = payload.get("updated_at")
        if updated_at:
            return parse_iso(updated_at)
    except Exception:
        pass

    try:
        return datetime.fromtimestamp(REASONING_MEMORY_LATEST.stat().st_mtime, tz=timezone.utc)
    except Exception:
        return None


def refresh_reasoning_memory_if_stale() -> None:
    updated_at = latest_reasoning_memory_updated_at()
    now = datetime.now(timezone.utc)

    if updated_at is not None and now - updated_at < timedelta(hours=REASONING_MEMORY_MAX_AGE_HOURS):
        print(f"\nReasoning memory is fresh: {updated_at.isoformat()}")
        return

    if updated_at is None:
        print("\nNo reasoning memory found. Refreshing reasoning memory.")
    else:
        age_hours = (now - updated_at).total_seconds() / 3600
        print(f"\nReasoning memory is stale ({age_hours:.1f}h old). Refreshing reasoning memory.")

    run([sys.executable, "scripts/update_reasoning_memory.py"])


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
    refresh_reasoning_memory_if_stale()

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
