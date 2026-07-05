import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))
from config import DB_PATH, EVAL_LAG_HOURS

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
GENERATED_DIR = DATA_DIR / "generated"
REASONING_DIR = DATA_DIR / "reasoning"
MEMORY_DIR = DATA_DIR / "memory"


def parse_iso(ts: str):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def latest_file(pattern_dir: Path, pattern: str):
    files = sorted(pattern_dir.glob(pattern))
    return files[-1] if files else None


def fmt_dt(dt):
    if not dt:
        return "n/a"
    if isinstance(dt, str):
        return dt
    return dt.isoformat()


def get_crontab_lines():
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def find_run_loop_cron_line():
    for line in get_crontab_lines():
        if "scripts/run_loop.py" in line:
            return line
    return None


def summarize_snapshots():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=EVAL_LAG_HOURS)

    total = 0
    evaluated = 0
    mature_ready = 0
    pending = 0
    bad = 0
    oldest_pending = None
    next_mature_at = None

    for path in sorted(SNAPSHOTS_DIR.glob("batch_*.json")):
        total += 1
        try:
            payload = json.loads(path.read_text())
        except Exception:
            bad += 1
            continue

        saved_at = parse_iso(payload.get("saved_at", ""))
        has_eval = bool(payload.get("evaluated_at")) or bool(payload.get("evaluation"))

        if has_eval:
            evaluated += 1
            continue

        if not saved_at:
            pending += 1
            continue

        if saved_at <= cutoff:
            mature_ready += 1
        else:
            pending += 1
            if oldest_pending is None or saved_at < oldest_pending:
                oldest_pending = saved_at

            matured_at = saved_at + timedelta(hours=EVAL_LAG_HOURS)
            if next_mature_at is None or matured_at < next_mature_at:
                next_mature_at = matured_at

    return {
        "total": total,
        "evaluated": evaluated,
        "mature_ready": mature_ready,
        "pending": pending,
        "bad": bad,
        "oldest_pending": oldest_pending,
        "next_mature_at": next_mature_at,
        "cutoff": cutoff,
    }


def summarize_db():
    if not Path(DB_PATH).exists():
        return {
            "db_exists": False,
            "counts_rows": 0,
            "distinct_queries": 0,
            "latest_bucket_end": None,
        }

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    counts_rows = cur.execute("SELECT COUNT(*) FROM counts").fetchone()[0]
    distinct_queries = cur.execute("SELECT COUNT(DISTINCT query) FROM counts").fetchone()[0]
    latest_bucket_end = cur.execute("SELECT MAX(bucket_end) FROM counts").fetchone()[0]

    con.close()

    return {
        "db_exists": True,
        "counts_rows": counts_rows,
        "distinct_queries": distinct_queries,
        "latest_bucket_end": latest_bucket_end,
    }


def summarize_scored_batches(limit: int = 3):
    rows = []

    for path in sorted(SNAPSHOTS_DIR.glob("batch_*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue

        evaluated_at = payload.get("evaluated_at")
        evaluation = payload.get("evaluation") or {}
        if not evaluated_at and not evaluation:
            continue

        rows.append(
            {
                "batch_id": payload.get("batch_id", path.name),
                "evaluated_at": evaluated_at,
                "avg_realized_score": evaluation.get("avg_realized_score"),
                "best_query": evaluation.get("best_query"),
                "worst_query": evaluation.get("worst_query"),
                "query_results_count": len(evaluation.get("query_results", [])),
            }
        )

        if len(rows) >= limit:
            break

    return rows


def main():
    snap = summarize_snapshots()
    db = summarize_db()
    scored_batches = summarize_scored_batches(limit=3)

    latest_generated = latest_file(GENERATED_DIR, "generated_queries_*.json")
    latest_reasoning = latest_file(REASONING_DIR, "reasoning_*.json")
    latest_memory_archive = latest_file(MEMORY_DIR / "archive", "strategy_memory_*.json")
    latest_memory_canonical = MEMORY_DIR / "strategy_memory_latest.json"
    cron_line = find_run_loop_cron_line()

    print("=== X Trend Lab Status ===")
    print(f"Now (UTC):               {datetime.now(timezone.utc).isoformat()}")
    print(f"Eval lag hours:          {EVAL_LAG_HOURS}")
    print()

    print("Cron")
    print(f"  Run-loop schedule:     {cron_line or 'n/a'}")
    print()

    print("Snapshots")
    print(f"  Total batch files:     {snap['total']}")
    print(f"  Evaluated:             {snap['evaluated']}")
    print(f"  Mature and ready:      {snap['mature_ready']}")
    print(f"  Pending:               {snap['pending']}")
    print(f"  Unreadable/bad files:  {snap['bad']}")
    print(f"  Eval cutoff (UTC):     {fmt_dt(snap['cutoff'])}")
    print(f"  Oldest pending saved:  {fmt_dt(snap['oldest_pending'])}")
    print(f"  Next batch matures at: {fmt_dt(snap['next_mature_at'])}")
    print()

    print("Latest artifacts")
    print(f"  Generated:             {latest_generated.name if latest_generated else 'n/a'}")
    print(f"  Reasoning:             {latest_reasoning.name if latest_reasoning else 'n/a'}")
    print(f"  Memory latest:         {latest_memory_canonical.name if latest_memory_canonical.exists() else 'n/a'}")
    print(f"  Memory archive latest: {latest_memory_archive.name if latest_memory_archive else 'n/a'}")
    print()

    print("Database")
    print(f"  DB exists:             {db['db_exists']}")
    print(f"  Counts rows:           {db['counts_rows']}")
    print(f"  Distinct queries:      {db['distinct_queries']}")
    print(f"  Latest bucket_end:     {db['latest_bucket_end'] or 'n/a'}")
    print()

    print("Recently scored batches")
    if not scored_batches:
        print("  None yet.")
    else:
        for row in scored_batches:
            print(f"  - {row['batch_id']}")
            print(f"      evaluated_at:      {fmt_dt(row['evaluated_at'])}")
            print(f"      avg_score:         {row['avg_realized_score'] if row['avg_realized_score'] is not None else 'n/a'}")
            print(f"      query_results:     {row['query_results_count']}")
            print(f"      best_query:        {row['best_query'] or 'n/a'}")
            print(f"      worst_query:       {row['worst_query'] or 'n/a'}")
    print()

    print("Summary")
    if snap["mature_ready"] > 0:
        print("  There are mature unevaluated batches ready to score now.")
    elif snap["pending"] > 0:
        print("  No mature unevaluated batches yet. Waiting for pending batches to age past the eval lag.")
    else:
        print("  No pending snapshot batches found.")

    if latest_reasoning:
        print("  The loop has produced reasoning artifacts.")
    if latest_memory_canonical.exists():
        print("  Strategy memory exists and can be used later for prompt steering.")


if __name__ == "__main__":
    main()