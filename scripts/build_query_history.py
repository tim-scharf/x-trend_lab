import json
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
GENERATED_DIR = ROOT / "data" / "generated"
OUT_DIR = ROOT / "data" / "runtime"
OUT_DIR.mkdir(parents=True, exist_ok=True)

OUT_CSV = OUT_DIR / "query_level_history.csv"


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def load_generated_metadata(generated_file: str | None) -> dict:
    """
    Backfill generation metadata from data/generated/<generated_file>.

    This repairs older batch snapshots that stored generated_file but did not copy
    model_used / created_at / generation_settings into the snapshot itself.
    """
    if not generated_file:
        return {}

    path = GENERATED_DIR / generated_file
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text())
    except Exception:
        return {}

    return {
        "model_used": payload.get("model_used"),
        "generated_created_at": payload.get("created_at"),
        "generation_settings": payload.get("generation_settings"),
        "strategy_notes": payload.get("strategy_notes"),
    }


def get_query_results(payload: dict) -> list[dict]:
    """
    Supports likely evaluated snapshot shapes:
    - payload["query_results"]
    - payload["results"]
    - payload["evaluation"]["query_results"]
    - payload["evaluation"]["results"]
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
    return any("realized_score" in row for row in rows)


def safe_get(payload: dict, *keys, default=None):
    """
    Pull the first present key from the payload.
    """
    for key in keys:
        if key in payload:
            return payload.get(key)
    return default


def flatten_snapshot(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text())
    except Exception as e:
        print(f"Skipping unreadable snapshot: {path.name} ({e})")
        return []

    if not is_evaluated(payload):
        return []

    rows = get_query_results(payload)
    if not rows:
        return []

    batch_id = safe_get(payload, "batch_id", default=path.stem)
    saved_at = safe_get(payload, "saved_at", "created_at")
    evaluated_at = get_evaluated_at(payload)

    generated_file = safe_get(payload, "generated_file", "source_generated_file")
    generated_meta = load_generated_metadata(generated_file)

    model_used = safe_get(payload, "model_used") or generated_meta.get("model_used")
    generated_created_at = (
        safe_get(payload, "generated_created_at")
        or generated_meta.get("generated_created_at")
    )
    generation_settings = (
        safe_get(payload, "generation_settings")
        or generated_meta.get("generation_settings")
    )
    strategy_notes = (
        safe_get(payload, "strategy_notes")
        or generated_meta.get("strategy_notes")
    )

    out = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue

        out.append(
            {
                "batch_file": path.name,
                "batch_id": batch_id,
                "saved_at": saved_at,
                "evaluated_at": evaluated_at,
                "generated_file": generated_file,
                "model_used": model_used,
                "generated_created_at": generated_created_at,
                "generation_settings": generation_settings,
                "strategy_notes": strategy_notes,
                "query_index": i,

                # Generated query metadata
                "query": row.get("query"),
                "domain": row.get("domain"),
                "mode": row.get("mode"),
                "reason": row.get("reason"),

                # Evaluation metrics
                "t0_3h": row.get("t0_3h"),
                "future_3h": row.get("future_3h"),
                "growth_ratio": row.get("growth_ratio"),
                "realized_score": row.get("realized_score"),
                "future_bucket_count": row.get("future_bucket_count"),

                # Optional fields if your evaluator adds them later
                "t0_bucket_count": row.get("t0_bucket_count"),
                "future_6h": row.get("future_6h"),
                "candidate_score": row.get("candidate_score"),
                "score": row.get("score"),
            }
        )

    return out


def build_history() -> pd.DataFrame:
    if not SNAPSHOT_DIR.exists():
        raise FileNotFoundError(f"Snapshot directory not found: {SNAPSHOT_DIR}")

    snapshot_files = sorted(SNAPSHOT_DIR.glob("batch_*.json"))

    rows = []
    scanned = 0
    for path in snapshot_files:
        scanned += 1
        rows.extend(flatten_snapshot(path))

    df = pd.DataFrame(rows)

    if df.empty:
        print(f"Scanned snapshots: {scanned}")
        print("No evaluated query rows found yet.")
        return df

    # Type cleanup
    dt_cols = ["saved_at", "evaluated_at", "generated_created_at"]
    for col in dt_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")

    numeric_cols = [
        "query_index",
        "t0_3h",
        "future_3h",
        "growth_ratio",
        "realized_score",
        "future_bucket_count",
        "t0_bucket_count",
        "future_6h",
        "candidate_score",
        "score",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Helpful derived flags
    if "future_3h" in df.columns:
        df["future_nonzero"] = df["future_3h"].fillna(0) > 0
    else:
        df["future_nonzero"] = False

    if "realized_score" in df.columns:
        df["score_positive"] = df["realized_score"].fillna(0) > 0
        df["score_negative"] = df["realized_score"].fillna(0) < 0
        df["score_zero"] = df["realized_score"].fillna(0) == 0
    else:
        df["score_positive"] = False
        df["score_negative"] = False
        df["score_zero"] = False

    if {"t0_3h", "future_3h"}.issubset(df.columns):
        df["burst_trap"] = (df["t0_3h"].fillna(0) > 0) & (df["future_3h"].fillna(0) == 0)
        df["no_signal"] = (df["t0_3h"].fillna(0) == 0) & (df["future_3h"].fillna(0) == 0)
    else:
        df["burst_trap"] = False
        df["no_signal"] = False

    # Chronological order
    sort_cols = []
    if "saved_at" in df.columns:
        sort_cols.append("saved_at")
    sort_cols.extend(["batch_file", "query_index"])
    df = df.sort_values(sort_cols, na_position="last").reset_index(drop=True)

    return df


def main() -> None:
    df = build_history()

    if df.empty:
        return

    df.to_csv(OUT_CSV, index=False)

    print("=== Query-Level History ===")
    print(f"Snapshot dir:           {SNAPSHOT_DIR}")
    print(f"Rows written:           {len(df)}")
    print(f"Distinct batches:       {df['batch_id'].nunique() if 'batch_id' in df else 'n/a'}")
    print(f"Distinct queries:       {df['query'].nunique() if 'query' in df else 'n/a'}")
    print(f"Wrote CSV:              {OUT_CSV}")

    if "model_used" in df.columns:
        populated = df["model_used"].notna().sum()
        print(f"Rows with model_used:   {populated}")
    print()

    # Small survival summary
    if "realized_score" in df.columns:
        print("Score summary:")
        print(f"  avg_realized_score:   {df['realized_score'].mean():.4f}")
        print(f"  pct_positive:         {(df['realized_score'] > 0).mean():.4f}")
        print(f"  pct_zero:             {(df['realized_score'] == 0).mean():.4f}")
        print(f"  pct_negative:         {(df['realized_score'] < 0).mean():.4f}")

    if "future_3h" in df.columns:
        print(f"  pct_future_nonzero:   {(df['future_3h'] > 0).mean():.4f}")

    if "burst_trap" in df.columns:
        print(f"  pct_burst_trap:       {df['burst_trap'].mean():.4f}")

    print()
    cols = [
        "batch_id",
        "query_index",
        "model_used",
        "domain",
        "mode",
        "t0_3h",
        "future_3h",
        "realized_score",
        "future_bucket_count",
        "burst_trap",
        "query",
    ]
    cols = [c for c in cols if c in df.columns]

    print("Latest rows:")
    print(df[cols].tail(20).to_string(index=False))


if __name__ == "__main__":
    main()
