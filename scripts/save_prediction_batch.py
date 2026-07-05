import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
from config import DB_PATH, SNAPSHOTS_DIR


def load_generated_payload(path: Path) -> dict:
    return json.loads(path.read_text())


def load_generated_queries_from_payload(payload: dict) -> pd.DataFrame:
    rows = []
    for item in payload.get("queries", []):
        rows.append(
            {
                "query": item["query"],
                "domain": item.get("domain"),
                "mode": item.get("mode"),
                "reason": item.get("reason"),
            }
        )
    return pd.DataFrame(rows)


def load_latest_counts(queries: list[str], buckets_per_query: int = 6) -> pd.DataFrame:
    if not queries:
        return pd.DataFrame(
            columns=["query", "bucket_start", "bucket_end", "tweet_count", "pulled_at"]
        )

    con = sqlite3.connect(DB_PATH)
    placeholders = ",".join(["?"] * len(queries))

    df = pd.read_sql_query(
        f"""
        WITH ranked AS (
            SELECT
                query,
                bucket_start,
                bucket_end,
                tweet_count,
                pulled_at,
                ROW_NUMBER() OVER (
                    PARTITION BY query
                    ORDER BY bucket_end DESC, pulled_at DESC
                ) AS rn
            FROM counts
            WHERE query IN ({placeholders})
        )
        SELECT query, bucket_start, bucket_end, tweet_count, pulled_at
        FROM ranked
        WHERE rn <= {buckets_per_query}
        ORDER BY query, bucket_end
        """,
        con,
        params=queries,
    )

    con.close()
    return df


def keep_only_complete_queries(
    queries_df: pd.DataFrame,
    counts_df: pd.DataFrame,
    buckets_per_query: int = 6,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if counts_df.empty:
        return queries_df.iloc[0:0].copy(), counts_df

    bucket_counts = counts_df.groupby("query").size().rename("n_buckets")
    eligible_queries = set(bucket_counts[bucket_counts >= buckets_per_query].index)

    filtered_queries_df = queries_df[queries_df["query"].isin(eligible_queries)].copy()
    filtered_counts_df = counts_df[counts_df["query"].isin(eligible_queries)].copy()

    return filtered_queries_df, filtered_counts_df


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/save_prediction_batch.py data/generated/<generated_queries_file>.json")
        sys.exit(1)

    json_path = Path(sys.argv[1]).resolve()

    generated_payload = load_generated_payload(json_path)
    queries_df = load_generated_queries_from_payload(generated_payload)

    if queries_df.empty:
        print("No queries found in generated file.")
        sys.exit(1)

    buckets_per_query = 6

    counts_df = load_latest_counts(
        queries_df["query"].tolist(),
        buckets_per_query=buckets_per_query,
    )

    queries_df, counts_df = keep_only_complete_queries(
        queries_df,
        counts_df,
        buckets_per_query=buckets_per_query,
    )

    if queries_df.empty or counts_df.empty:
        print(f"No queries had a full {buckets_per_query}-bucket snapshot yet. Skipping batch save.")
        return

    saved_at = datetime.now(timezone.utc).isoformat()
    batch_id = datetime.now(timezone.utc).strftime("batch_%Y%m%d_%H%M%S")
    out_path = SNAPSHOTS_DIR / f"{batch_id}.json"

    payload = {
        "batch_id": batch_id,
        "generated_file": json_path.name,

        # Copy generation metadata into the batch snapshot so the fossil is self-contained.
        "model_used": generated_payload.get("model_used"),
        "generated_created_at": generated_payload.get("created_at"),
        "generation_settings": generated_payload.get("generation_settings"),
        "strategy_notes": generated_payload.get("strategy_notes"),

        "saved_at": saved_at,
        "snapshot_bucket_count_per_query": buckets_per_query,
        "queries": queries_df.to_dict(orient="records"),
        "counts_snapshot": counts_df.to_dict(orient="records"),
    }

    out_path.write_text(json.dumps(payload, indent=2, default=str))

    print(f"Saved prediction batch: {out_path}")
    print(f"Generated file: {json_path.name}")
    print(f"Model used: {payload.get('model_used')}")
    print(f"Queries saved: {len(queries_df)}")
    print(f"Snapshot rows saved: {len(counts_df)}")


if __name__ == "__main__":
    main()
