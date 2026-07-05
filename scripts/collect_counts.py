import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).resolve().parents[1]))
from config import DB_PATH, MAX_COUNT_REQUESTS_PER_RUN, X_COUNTS_COST_PER_REQUEST

load_dotenv()
BEARER_TOKEN = os.getenv("X_BEARER_TOKEN")
if not BEARER_TOKEN:
    raise RuntimeError("Missing X_BEARER_TOKEN in .env")


def init_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS counts (
            query TEXT NOT NULL,
            bucket_start TEXT NOT NULL,
            bucket_end TEXT NOT NULL,
            tweet_count INTEGER NOT NULL,
            pulled_at TEXT NOT NULL,
            PRIMARY KEY (query, bucket_start, bucket_end)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS query_metadata (
            query TEXT PRIMARY KEY,
            domain TEXT,
            mode TEXT,
            reason TEXT,
            source_file TEXT,
            first_seen_at TEXT
        )
        """
    )
    return con


def load_queries(json_path: Path) -> list[dict]:
    payload = json.loads(json_path.read_text())
    queries = payload.get("queries", [])
    cleaned = []
    seen = set()
    for item in queries:
        query = item.get("query", "").strip()
        if not query or query in seen:
            continue
        seen.add(query)
        cleaned.append(
            {
                "query": query,
                "domain": item.get("domain", "unknown").strip(),
                "mode": item.get("mode", "unknown").strip(),
                "reason": item.get("reason", "").strip(),
            }
        )
    return cleaned


def get_recent_counts(query: str) -> dict:
    response = requests.get(
        "https://api.x.com/2/tweets/counts/recent",
        headers={"Authorization": f"Bearer {BEARER_TOKEN}"},
        params={"query": query, "granularity": "hour"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def save_query_metadata(con: sqlite3.Connection, item: dict, source_file: str) -> None:
    con.execute(
        """
        INSERT OR IGNORE INTO query_metadata
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            item["query"],
            item["domain"],
            item["mode"],
            item["reason"],
            source_file,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def save_counts(con: sqlite3.Connection, query: str, payload: dict) -> tuple[int, int | None]:
    pulled_at = datetime.now(timezone.utc).isoformat()
    rows = payload.get("data", [])
    for row in rows:
        con.execute(
            "INSERT OR REPLACE INTO counts VALUES (?, ?, ?, ?, ?)",
            (query, row["start"], row["end"], int(row["tweet_count"]), pulled_at),
        )
    con.commit()
    return len(rows), payload.get("meta", {}).get("total_tweet_count")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/collect_counts.py data/generated/<generated_queries_file>.json")
        sys.exit(1)

    json_path = Path(sys.argv[1]).resolve()
    queries = load_queries(json_path)
    if len(queries) > MAX_COUNT_REQUESTS_PER_RUN:
        raise RuntimeError(f"Refusing to run {len(queries)} requests. Cap is {MAX_COUNT_REQUESTS_PER_RUN}.")

    print(f"Queries to run: {len(queries)}")
    print(f"Estimated X counts cost: ${len(queries) * X_COUNTS_COST_PER_REQUEST:.3f}")

    con = init_db()
    for i, item in enumerate(queries, start=1):
        print(f"\n[{i}/{len(queries)}] {item['query']}")
        save_query_metadata(con, item, json_path.name)
        payload = get_recent_counts(item["query"])
        rows_saved, total = save_counts(con, item["query"], payload)
        print(f"Saved buckets: {rows_saved}")
        print(f"Total count: {total}")


if __name__ == "__main__":
    main()
