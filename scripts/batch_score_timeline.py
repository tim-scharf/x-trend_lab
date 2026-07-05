import json
from pathlib import Path
from datetime import datetime

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]

SNAPSHOT_DIR = ROOT / "data" / "snapshots"
OUT_DIR = ROOT / "data" / "runtime"
OUT_DIR.mkdir(parents=True, exist_ok=True)

OUT_CSV = OUT_DIR / "batch_score_timeline.csv"


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def get_query_results(payload: dict):
    """
    Supports likely evaluated batch shapes.
    """
    if isinstance(payload.get("query_results"), list):
        return payload["query_results"]

    if isinstance(payload.get("results"), list):
        return payload["results"]

    evaluation = payload.get("evaluation")
    if isinstance(evaluation, dict):
        if isinstance(evaluation.get("query_results"), list):
            return evaluation["query_results"]
        if isinstance(evaluation.get("results"), list):
            return evaluation["results"]

    return []


def get_evaluated_at(payload: dict):
    if payload.get("evaluated_at"):
        return payload["evaluated_at"]

    evaluation = payload.get("evaluation")
    if isinstance(evaluation, dict):
        return evaluation.get("evaluated_at")

    return None


def is_evaluated(payload: dict) -> bool:
    if get_evaluated_at(payload):
        return True

    rows = get_query_results(payload)
    return any("realized_score" in r for r in rows)


def summarize_batch(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text())
    except Exception as e:
        print(f"Skipping unreadable file: {path.name} ({e})")
        return None

    if not is_evaluated(payload):
        return None

    rows = get_query_results(payload)
    if not rows:
        return None

    df = pd.DataFrame(rows)

    if "realized_score" not in df.columns:
        return None

    df["realized_score"] = pd.to_numeric(df["realized_score"], errors="coerce")
    df["t0_3h"] = pd.to_numeric(df.get("t0_3h"), errors="coerce")
    df["future_3h"] = pd.to_numeric(df.get("future_3h"), errors="coerce")
    df["future_bucket_count"] = pd.to_numeric(df.get("future_bucket_count"), errors="coerce")

    valid_scores = df["realized_score"].dropna()
    if valid_scores.empty:
        return None

    batch_id = payload.get("batch_id") or path.stem
    saved_at = payload.get("saved_at") or payload.get("created_at")
    evaluated_at = get_evaluated_at(payload)

    best_idx = df["realized_score"].idxmax()
    worst_idx = df["realized_score"].idxmin()

    best_row = df.loc[best_idx].to_dict()
    worst_row = df.loc[worst_idx].to_dict()

    return {
        "batch_file": path.name,
        "batch_id": batch_id,
        "saved_at": saved_at,
        "evaluated_at": evaluated_at,
        "n_queries": int(len(df)),

        "avg_realized_score": round(float(valid_scores.mean()), 6),
        "median_realized_score": round(float(valid_scores.median()), 6),
        "min_realized_score": round(float(valid_scores.min()), 6),
        "max_realized_score": round(float(valid_scores.max()), 6),

        "pct_positive": round(float((df["realized_score"] > 0).mean()), 4),
        "pct_zero": round(float((df["realized_score"] == 0).mean()), 4),
        "pct_negative": round(float((df["realized_score"] < 0).mean()), 4),

        "avg_t0_3h": round(float(df["t0_3h"].mean()), 4),
        "avg_future_3h": round(float(df["future_3h"].mean()), 4),
        "pct_future_nonzero": round(float((df["future_3h"] > 0).mean()), 4),
        "avg_future_bucket_count": round(float(df["future_bucket_count"].mean()), 4),

        "best_query": best_row.get("query"),
        "best_score": round(float(best_row.get("realized_score")), 6),
        "best_t0_3h": best_row.get("t0_3h"),
        "best_future_3h": best_row.get("future_3h"),

        "worst_query": worst_row.get("query"),
        "worst_score": round(float(worst_row.get("realized_score")), 6),
        "worst_t0_3h": worst_row.get("t0_3h"),
        "worst_future_3h": worst_row.get("future_3h"),
    }


def main():
    if not SNAPSHOT_DIR.exists():
        raise FileNotFoundError(f"Snapshot directory not found: {SNAPSHOT_DIR}")

    batch_files = sorted(SNAPSHOT_DIR.glob("batch_*.json"))

    if not batch_files:
        print(f"No batch_*.json files found in {SNAPSHOT_DIR}")
        return

    summaries = []
    skipped_not_evaluated = 0

    for path in batch_files:
        row = summarize_batch(path)
        if row:
            summaries.append(row)
        else:
            skipped_not_evaluated += 1

    if not summaries:
        print("No evaluated batches found yet.")
        print(f"Batch files scanned: {len(batch_files)}")
        return

    out = pd.DataFrame(summaries)

    out["_saved_dt"] = out["saved_at"].apply(parse_dt)
    out = (
        out.sort_values(["_saved_dt", "batch_file"], na_position="last")
        .drop(columns=["_saved_dt"])
        .reset_index(drop=True)
    )

    out.to_csv(OUT_CSV, index=False)

    print("=== Batch Score Timeline ===")
    print(f"Snapshot dir:              {SNAPSHOT_DIR}")
    print(f"Batch files scanned:       {len(batch_files)}")
    print(f"Evaluated summarized:      {len(out)}")
    print(f"Skipped/not evaluated:     {skipped_not_evaluated}")
    print(f"Wrote CSV:                 {OUT_CSV}")
    print()

    cols = [
        "batch_id",
        "saved_at",
        "avg_realized_score",
        "median_realized_score",
        "pct_positive",
        "pct_future_nonzero",
        "avg_t0_3h",
        "avg_future_3h",
        "best_score",
        "worst_score",
    ]

    print(out[cols].tail(20).to_string(index=False))


if __name__ == "__main__":
    main()