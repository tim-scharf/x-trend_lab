import json
import math
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from config import DB_PATH, EVAL_LAG_HOURS, SNAPSHOTS_DIR


def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def load_counts_from_db(query: str, start_ts: datetime, end_ts: datetime) -> pd.DataFrame:
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """
        SELECT query, bucket_start, bucket_end, tweet_count, pulled_at
        FROM counts
        WHERE query = ?
          AND bucket_end > ?
          AND bucket_end <= ?
        ORDER BY bucket_end
        """,
        con,
        params=(query, start_ts.isoformat(), end_ts.isoformat()),
    )
    con.close()

    if not df.empty:
        df["bucket_start"] = pd.to_datetime(df["bucket_start"], utc=True)
        df["bucket_end"] = pd.to_datetime(df["bucket_end"], utc=True)
    return df


def compute_growth_score(t0_value: float, future_value: float) -> float:
    if t0_value <= 0:
        return 0.0
    growth = future_value / t0_value
    return math.log(max(growth, 0.5))


def evaluate_snapshot(path: Path) -> dict:
    payload = json.loads(path.read_text())
    saved_at = parse_iso(payload["saved_at"])
    eval_cutoff = saved_at + timedelta(hours=EVAL_LAG_HOURS)

    snapshot_df = pd.DataFrame(payload.get("counts_snapshot", []))
    if snapshot_df.empty:
        payload["evaluated_at"] = datetime.now(timezone.utc).isoformat()
        payload["evaluation"] = {
            "status": "no_counts_snapshot",
            "eval_lag_hours": EVAL_LAG_HOURS,
            "query_results": [],
        }
        path.write_text(json.dumps(payload, indent=2, default=str))
        return {
            "path": path.name,
            "status": "no_counts_snapshot",
            "query_count": 0,
        }

    snapshot_df["bucket_start"] = pd.to_datetime(snapshot_df["bucket_start"], utc=True)
    snapshot_df["bucket_end"] = pd.to_datetime(snapshot_df["bucket_end"], utc=True)

    query_results = []
    for q in payload.get("queries", []):
        query = q["query"]

        t0_df = snapshot_df[snapshot_df["query"] == query].sort_values("bucket_end").tail(3)
        t0_3h = int(t0_df["tweet_count"].sum()) if not t0_df.empty else 0

        future_df = load_counts_from_db(query, saved_at, eval_cutoff)
        future_3h = int(future_df["tweet_count"].sum()) if not future_df.empty else 0

        realized_score = compute_growth_score(t0_3h, future_3h)

        query_results.append(
            {
                "query": query,
                "domain": q.get("domain"),
                "mode": q.get("mode"),
                "reason": q.get("reason"),
                "t0_3h": t0_3h,
                "future_3h": future_3h,
                "growth_ratio": round((future_3h / t0_3h), 4) if t0_3h > 0 else None,
                "realized_score": round(realized_score, 4),
                "future_bucket_count": int(len(future_df)),
            }
        )

    scores = [r["realized_score"] for r in query_results]
    avg_score = sum(scores) / len(scores) if scores else 0.0
    best = max(query_results, key=lambda x: x["realized_score"]) if query_results else None
    worst = min(query_results, key=lambda x: x["realized_score"]) if query_results else None

    payload["evaluated_at"] = datetime.now(timezone.utc).isoformat()
    payload["evaluation"] = {
        "status": "evaluated",
        "eval_lag_hours": EVAL_LAG_HOURS,
        "saved_at": saved_at.isoformat(),
        "evaluated_window_end": eval_cutoff.isoformat(),
        "avg_realized_score": round(avg_score, 4),
        "best_query": best,
        "worst_query": worst,
        "query_results": query_results,
    }

    path.write_text(json.dumps(payload, indent=2, default=str))

    return {
        "path": path.name,
        "status": "evaluated",
        "query_count": len(query_results),
        "avg_realized_score": round(avg_score, 4),
        "best_query": best["query"] if best else None,
        "best_score": best["realized_score"] if best else None,
    }


def main() -> None:
    snapshots = sorted(SNAPSHOTS_DIR.glob("batch_*.json"))
    if not snapshots:
        print("No prediction snapshots found yet.")
        return

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=EVAL_LAG_HOURS)

    mature = []
    pending = []
    already_done = []

    for path in snapshots:
        payload = json.loads(path.read_text())
        if payload.get("evaluated_at"):
            already_done.append(path)
            continue

        saved_at = parse_iso(payload["saved_at"])
        if saved_at <= cutoff:
            mature.append(path)
        else:
            pending.append(path)

    print(f"Eval lag hours: {EVAL_LAG_HOURS}")
    print(f"Total snapshots: {len(snapshots)}")
    print(f"Already evaluated: {len(already_done)}")
    print(f"Mature and ready now: {len(mature)}")
    print(f"Pending: {len(pending)}")

    if not mature:
        print("\\nNo mature batches yet. Still in bootstrap / warmup phase.")
        return

    print("\\nEvaluating mature batches:")
    results = []
    for path in mature:
        print(f"- {path.name}")
        results.append(evaluate_snapshot(path))

    print("\\nEvaluation complete:")
    for r in results:
        best_part = f' | best={r["best_query"]} ({r["best_score"]})' if r.get("best_query") else ""
        print(f'- {r["path"]} | queries={r["query_count"]} | avg_score={r.get("avg_realized_score", 0.0)}{best_part}')


if __name__ == "__main__":
    main()
